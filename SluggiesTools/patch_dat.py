import os
import sys
import shutil
import json
import base64
import struct

INPUT_DAT  = '../1_Input/dt_na.dat'
OUTPUT_DIR = '../3_Output_Dat'
OUTPUT_DAT = os.path.join(OUTPUT_DIR, 'dt_na.dat')

# Allow importing sibling modules (HammerspaceHelpers, drawlist)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import HammerspaceHelpers as _hs
import skn_patch as _skn


def abort(message):
    print(f"ERROR: {message}")
    input("\nPress any key to exit...")
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _comp_size(quant_info: int) -> int:
    fmt = quant_info >> 4
    return 4 if fmt in [4, 7, 0xa] else 2


def _align4(data: bytes) -> bytes:
    r = len(data) % 4
    return data + b'\x00' * ((4 - r) % 4)


def _chunk_name(submesh_offset_hex: str, submesh_idx: int) -> str:
    return f"slugmesh_{submesh_offset_hex.lstrip('0x')}_{submesh_idx}"


def writeExpandedMesh(i: int, submesh: dict, new_verts_raw: bytes,
                      new_uvs_per_ch: dict,
                      skin_data: dict = None) -> bool:
    """Write all buffers for one submesh to hammerspace and patch the pointers.

    ``new_uvs_per_ch`` is a dict ``{ch_ind: bytes}`` for channels that have
    changed.  Channels absent from the dict fall back to their original data
    recorded in the JSON so the chunk always contains complete UV data.

    ``skin_data`` is the model-level ``SkinData`` dict from the .sluggie JSON.
    When provided, SKN destination-buffer pointer fields are also patched.
    NOTE (Phase 4): SKN patching only applies when submesh index i == 0, since
    the memClr region starts at submesh 0's vertex buffer.  Phase 6 will
    extend this to the full multi-submesh contiguous-buffer case.

    The draw list data is currently written verbatim from the JSON (original
    primitive lists).  Per-draw-state face index updates are reserved for a
    future step once the Blender exporter emits new index data.

    Returns True on success, False on failure.
    """
    relative_base = int(submesh['SubmeshOffset'], 16)
    vb = submesh['VertexBuffer']
    chunk_name = _chunk_name(submesh['SubmeshOffset'], i)

    # Build ordered sections: vertex → each UV channel → each draw state
    sections = [('verts', new_verts_raw)]

    for ch in submesh.get('UVChannels', []):
        ch_ind = ch['UVChannelIndex']
        raw = new_uvs_per_ch.get(ch_ind)
        if raw is None:
            raw = base64.b64decode(ch['UVChannelData'])
        sections.append((f'uv{ch_ind}', raw))

    for ds_ind, ds in enumerate(submesh.get('DrawStates', [])):
        sections.append((f'dl{ds_ind}', base64.b64decode(ds['PrimListData'])))

    # Pack into a single 4-byte-aligned blob
    blob = bytearray()
    offsets: dict[str, int] = {}
    for tag, raw in sections:
        offsets[tag] = len(blob)
        blob.extend(_align4(raw))
    blob = bytes(blob)

    # Write chunk — also patches the vertex data pointer field
    original_vb_offset = int(vb['VertexBufferOffset'], 16)
    pos_ptr_field      = int(submesh['PositionDataPtrFieldOffset'], 16)

    chunk_start = _hs.writeNewMemoryChunk(
        chunk_name, blob, original_vb_offset, pos_ptr_field, relative_base
    )
    if chunk_start == -1:
        print(f"  Submesh {i}: ERROR — writeNewMemoryChunk failed.")
        return False

    data_abs, _ = _hs.findChunk(chunk_name)
    if data_abs == -1:
        print(f"  Submesh {i}: ERROR — could not locate chunk after writing.")
        return False

    with open(OUTPUT_DAT, 'r+b') as f:
        # Vertex count (uint16 BE)
        vb_cs = _comp_size(vb['VertexBufferQuantizeInfo'])
        new_vcount = len(new_verts_raw) // (vb['VertexBufferCompCount'] * vb_cs)
        f.seek(int(submesh['VertexCountFieldOffset'], 16))
        f.write(struct.pack('>H', new_vcount))

        for ch in submesh.get('UVChannels', []):
            ch_ind = ch['UVChannelIndex']
            raw = new_uvs_per_ch.get(ch_ind, base64.b64decode(ch['UVChannelData']))
            uv_abs = data_abs + offsets[f'uv{ch_ind}']
            _hs.patchPointerField(int(ch['UVDataPtrFieldOffset'], 16), uv_abs, relative_base)
            uv_cs = _comp_size(ch['UVChannelQuantizeInfo'])
            new_uv_count = len(raw) // (ch['UVChannelCompCount'] * uv_cs)
            f.seek(int(ch['UVCountFieldOffset'], 16))
            f.write(struct.pack('>H', new_uv_count))

        for ds_ind, ds in enumerate(submesh.get('DrawStates', [])):
            dl_raw = base64.b64decode(ds['PrimListData'])
            dl_abs = data_abs + offsets[f'dl{ds_ind}']
            _hs.patchPointerField(int(ds['PrimListPtrFieldOffset'], 16), dl_abs, relative_base)
            f.seek(int(ds['PrimListSizeFieldOffset'], 16))
            f.write(struct.pack('>I', len(dl_raw)))

    # Phase 4: patch SKN destination buffer pointer when this is the first
    # submesh (submesh 0 is the start of the contiguous memClr region).
    if skin_data is not None and i == 0:
        new_vert_abs = data_abs + offsets['verts']
        _skn.patchSKNForNewDestBuffer(skin_data, new_vert_abs)
        print(f"  Submesh {i}: SKN memClrPtr patched to 0x{new_vert_abs:X}")

    print(f"  Submesh {i}: hammerspace '{chunk_name}' at 0x{data_abs:X} "
          f"({len(blob)} bytes payload, {new_vcount} vertices)")
    return True


def restoreSubmeshFromHammerspace(i: int, submesh: dict,
                                   skin_data: dict = None) -> None:
    """If this submesh has a hammerspace chunk, erase it and restore all
    pointer fields and count fields to their original values.

    When ``skin_data`` is provided, SKN pointer fields are also restored.
    This is safe to call for every submesh of a skinned model since
    ``restoreSKNPointers`` is idempotent (always writes the same original values).

    The actual data bytes are written back by the standard --unpatch flow
    immediately after this call.
    """
    chunk_name = _chunk_name(submesh['SubmeshOffset'], i)
    _, existing_len = _hs.findChunk(chunk_name)
    if existing_len == -1:
        return  # Not in hammerspace

    # Erase chunk without touching pointers (we restore them manually below)
    _hs.removeChunk(chunk_name)

    relative_base = int(submesh['SubmeshOffset'], 16)
    vb = submesh['VertexBuffer']

    # Restore vertex pointer
    original_vb_offset = int(vb['VertexBufferOffset'], 16)
    _hs.patchPointerField(int(submesh['PositionDataPtrFieldOffset'], 16),
                          original_vb_offset, relative_base)

    with open(OUTPUT_DAT, 'r+b') as f:
        # Restore vertex count
        vb_cs = _comp_size(vb['VertexBufferQuantizeInfo'])
        orig_vcount = vb['VertexBufferLength'] // (vb['VertexBufferCompCount'] * vb_cs)
        f.seek(int(submesh['VertexCountFieldOffset'], 16))
        f.write(struct.pack('>H', orig_vcount))

        for ch in submesh.get('UVChannels', []):
            original_uv_offset = int(ch['UVChannelOffset'], 16)
            _hs.patchPointerField(int(ch['UVDataPtrFieldOffset'], 16),
                                  original_uv_offset, relative_base)
            uv_cs = _comp_size(ch['UVChannelQuantizeInfo'])
            orig_uv_count = ch['UVChannelLength'] // (ch['UVChannelCompCount'] * uv_cs)
            f.seek(int(ch['UVCountFieldOffset'], 16))
            f.write(struct.pack('>H', orig_uv_count))

        for ds in submesh.get('DrawStates', []):
            original_dl_offset = int(ds['PrimListAbsoluteOffset'], 16)
            _hs.patchPointerField(int(ds['PrimListPtrFieldOffset'], 16),
                                  original_dl_offset, relative_base)
            f.seek(int(ds['PrimListSizeFieldOffset'], 16))
            f.write(struct.pack('>I', ds['PrimListLength']))

    if skin_data is not None:
        _skn.restoreSKNPointers(skin_data)
        print(f"  Submesh {i}: SKN pointers restored.")

    print(f"  Submesh {i}: hammerspace chunk removed, pointers restored.")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

unpatch    = '--unpatch' in sys.argv
argv_clean = [a for a in sys.argv[1:] if a != '--unpatch']

if not argv_clean:
    abort("No .sluggies file path provided.\nUsage: python patch_dat.py <path_to_model.sluggies> [--unpatch]")

json_path = argv_clean[0]
if not os.path.exists(json_path):
    abort(f"JSON file not found: {json_path}")

# ---------------------------------------------------------------------------
# Load JSON
# ---------------------------------------------------------------------------

with open(json_path, 'r') as f:
    try:
        data = json.load(f)
    except json.JSONDecodeError as e:
        abort(f"JSON parse error in {json_path}: {e}")

if "SluggiesModel" not in data:
    abort(f"JSON does not contain a 'SluggiesModel' entry: {json_path}")

submeshes = data["SluggiesModel"].get("Submeshes", [])
if not submeshes:
    abort("No submeshes found in JSON.")

# ---------------------------------------------------------------------------
# Ensure output file exists
# ---------------------------------------------------------------------------

mode_label = "original" if unpatch else "edited"
print(f"Mode: {'--unpatch (restoring original data)' if unpatch else 'patch (writing edited data)'}")

if not os.path.exists(OUTPUT_DIR):
    os.mkdir(OUTPUT_DIR)
    print(f"Created folder: {OUTPUT_DIR}/")

if not os.path.exists(INPUT_DAT):
    abort(f"Input file not found: {INPUT_DAT}\nCannot continue without a source dt_na.dat.")

if os.path.exists(OUTPUT_DAT):
    print(f"Output file already exists, skipping copy: {OUTPUT_DAT}")
else:
    shutil.copy2(INPUT_DAT, OUTPUT_DAT)
    print(f"Copied {INPUT_DAT} -> {OUTPUT_DAT}")

# ---------------------------------------------------------------------------
# Build patch list
# ---------------------------------------------------------------------------

patches    = []   # (submesh_idx, file_offset, raw_bytes)
uv_patches = []   # (submesh_idx, ch_ind, file_offset, raw_bytes)
hammerspace_count = 0

skin_data = data["SluggiesModel"].get("SkinData")  # None for non-skinned models

for i, submesh in enumerate(submeshes):
    vb = submesh.get("VertexBuffer", {})

    if unpatch:
        # -- restore hammerspace state first, then queue in-place data restore --
        restoreSubmeshFromHammerspace(i, submesh, skin_data)

        vb_data = vb.get("VertexBufferData")
        if vb_data:
            raw = base64.b64decode(vb_data)
            patches.append((i, int(vb["VertexBufferOffset"], 16), raw))
            print(f"  Submesh {i}: queued {len(raw)} vertex bytes at {vb['VertexBufferOffset']}")

        for ch in submesh.get("UVChannels", []):
            ch_ind   = ch.get("UVChannelIndex", "?")
            uv_data  = ch.get("UVChannelData")
            if uv_data:
                raw = base64.b64decode(uv_data)
                uv_patches.append((i, ch_ind, int(ch["UVChannelOffset"], 16), raw))
                print(f"  Submesh {i} UV ch {ch_ind}: queued {len(raw)} bytes at {ch['UVChannelOffset']}")

    else:
        # -- patch mode --
        vb_edited_block = submesh.get("VertexBufferEdited")
        if not vb_edited_block or "VertexBufferDataEdited" not in vb_edited_block:
            print(f"  Submesh {i}: no VertexBufferEdited data, skipping.")
            continue

        new_verts = base64.b64decode(vb_edited_block["VertexBufferDataEdited"])
        original_vb_length = vb.get("VertexBufferLength", 0)

        # Collect edited UV channels
        new_uvs: dict[int, bytes] = {}
        uv_size_changed = False
        for ch in submesh.get("UVChannels", []):
            ch_ind = ch["UVChannelIndex"]
            edited = ch.get("UVChannelDataEdited")
            if edited:
                raw = base64.b64decode(edited)
                new_uvs[ch_ind] = raw
                if len(raw) != ch["UVChannelLength"]:
                    uv_size_changed = True

        vb_size_changed = len(new_verts) != original_vb_length

        # Guard: skinned mesh vertex count change requires Phase 6
        if vb_size_changed and vb.get('VertexBufferCompCount', 3) == 6:
            print(f"  Submesh {i}: WARNING — vertex count change on a skinned mesh "
                  f"(compCount=6) is not yet supported (requires Phase 6 bone weight "
                  f"rebuilding). Skipping this submesh.")
            continue

        if vb_size_changed or uv_size_changed:
            # Sizes changed — write everything to hammerspace
            ok = writeExpandedMesh(i, submesh, new_verts, new_uvs, skin_data)
            if ok:
                hammerspace_count += 1
        else:
            # Sizes unchanged — in-place write
            patches.append((i, int(vb["VertexBufferOffset"], 16), new_verts))
            print(f"  Submesh {i}: {len(new_verts)} vertex bytes (in-place)")

            for ch in submesh.get("UVChannels", []):
                ch_ind = ch["UVChannelIndex"]
                if ch_ind not in new_uvs:
                    continue
                raw = new_uvs[ch_ind]
                uv_patches.append((i, ch_ind, int(ch["UVChannelOffset"], 16), raw))
                print(f"  Submesh {i} UV ch {ch_ind}: {len(raw)} bytes (in-place)")

# ---------------------------------------------------------------------------
# Write in-place patches
# ---------------------------------------------------------------------------

if patches or uv_patches:
    print(f"\nWriting {len(patches)} in-place vertex patch(es) and "
          f"{len(uv_patches)} UV patch(es) to {OUTPUT_DAT} ...")
    with open(OUTPUT_DAT, 'r+b') as f:
        for i, offset, raw in patches:
            f.seek(offset)
            f.write(raw)
            print(f"  Submesh {i} vertex: wrote {len(raw)} bytes at 0x{offset:X}")
        for i, ch_ind, offset, raw in uv_patches:
            f.seek(offset)
            f.write(raw)
            print(f"  Submesh {i} UV ch {ch_ind}: wrote {len(raw)} bytes at 0x{offset:X}")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print(f"\n--- Summary ---")
print(f"Vertex submeshes patched (in-place) : {len(patches)}")
print(f"UV channels patched (in-place)      : {len(uv_patches)}")
print(f"Submeshes written to hammerspace    : {hammerspace_count}")
print(f"Output file                         : {OUTPUT_DAT}")
if unpatch:
    print("Done. The output file has been restored to the original data.")
else:
    print("Done. You can now overwrite your original dt_na.dat in the game folder.")

