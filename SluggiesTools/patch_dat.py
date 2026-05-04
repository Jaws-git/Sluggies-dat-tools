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
import Hammerspace as _hs
import patch_skn_dat as _skn
import drawlist as _dl


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


def _rebuild_draw_states(submesh: dict) -> dict:
    """Rebuild primitive list bytes for each draw state using new face indices.

    Two routing strategies are used depending on available data:

    1. **Texture-based routing** (when ``FaceTextureIndicesEdited`` is present):
       Each new face carries a texture index derived from its Blender material
       slot.  A ``texture_index → draw_state_index`` map is built from the
       original ``FaceTextureIndices`` array.  New faces are then routed to
       their draw state by texture index.  This correctly handles face count
       changes (additions and removals).

    2. **Count-based routing** (fallback, no ``FaceTextureIndicesEdited``):
       The original per-draw-state face counts are used to split the flat new
       face array in the same proportions.  Works only when total face count
       is unchanged.

    UV indices come from ``UVFacesDataEdited`` per channel.
    ``lighting``, ``color0``, and ``color1`` are co-indexed with ``position``
    for all known Sluggers models.

    Returns a dict ``{ds_ind: bytes}`` of rebuilt primitive list data.
    Draw states with no active descriptors are returned verbatim.
    """
    faces_edited_b64 = submesh.get('FacesDataEdited')
    if not faces_edited_b64:
        return {}

    # Decode flat position face indices → list of [i0, i1, i2] triplets
    raw_f = base64.b64decode(faces_edited_b64)
    n_f = len(raw_f) // 2
    flat_f = list(struct.unpack(f'>{n_f}H', raw_f))
    all_pos_faces = [flat_f[i * 3: i * 3 + 3] for i in range(n_f // 3)]
    total_new = len(all_pos_faces)

    # Decode UV face indices per channel
    uv_faces_by_ch: dict[int, list] = {}
    for ch in submesh.get('UVChannels', []):
        ch_ind = ch['UVChannelIndex']
        uv_edited = ch.get('UVFacesDataEdited')
        if uv_edited:
            raw_uv = base64.b64decode(uv_edited)
            n_uv = len(raw_uv) // 2
            flat_uv = list(struct.unpack(f'>{n_uv}H', raw_uv))
            uv_faces_by_ch[ch_ind] = [flat_uv[i * 3: i * 3 + 3] for i in range(n_uv // 3)]

    draw_states = submesh.get('DrawStates', [])

    # --- Decode original draw lists once (needed for face counts and fallback UV) ---
    orig_decoded: list[list] = []
    for ds in draw_states:
        descriptors = ds.get('ActiveDescriptors', [])
        orig_raw = base64.b64decode(ds['PrimListData'])
        orig_decoded.append(_dl.decodeDrawList(orig_raw, descriptors) if descriptors else [])

    # --- Build texture_index → draw_state_index from original FaceTextureIndices ---
    tex_to_ds: dict[int, int] = {}
    face_tex_b64 = submesh.get('FaceTextureIndices')
    if face_tex_b64:
        raw_ti = base64.b64decode(face_tex_b64)
        n_ti = len(raw_ti) // 2
        orig_face_tex = list(struct.unpack(f'>{n_ti}H', raw_ti))
        face_off = 0
        for ds_ind, orig_faces in enumerate(orig_decoded):
            cnt = len(orig_faces)
            if cnt > 0 and face_off < len(orig_face_tex):
                tex_idx = orig_face_tex[face_off]
                if tex_idx not in tex_to_ds:
                    tex_to_ds[tex_idx] = ds_ind
            face_off += cnt

    # --- Assign new faces to draw states ---
    # ds_assignments[ds_ind] = ordered list of global face indices for that draw state
    ds_assignments: dict[int, list[int]] = {i: [] for i in range(len(draw_states))}

    face_tex_edited_b64 = submesh.get('FaceTextureIndicesEdited')
    if face_tex_edited_b64 and tex_to_ds:
        # Path 1: texture-based routing — handles face count changes
        raw_fte = base64.b64decode(face_tex_edited_b64)
        n_fte = len(raw_fte) // 2
        new_face_tex = list(struct.unpack(f'>{n_fte}H', raw_fte))
        skipped = 0
        for fi, tex_idx in enumerate(new_face_tex):
            ds_ind = tex_to_ds.get(tex_idx)
            if ds_ind is None:
                skipped += 1
            else:
                ds_assignments[ds_ind].append(fi)
        if skipped:
            print(f"  WARNING _rebuild_draw_states: {skipped} face(s) had a texture "
                  f"index not found in any original draw state and were dropped.")
    else:
        # Path 2: count-based routing — face count must be unchanged
        face_offset = 0
        for ds_ind, orig_faces in enumerate(orig_decoded):
            orig_count = len(orig_faces)
            available = total_new - face_offset
            take = min(orig_count, available)
            if take < orig_count:
                print(f"  WARNING draw state {ds_ind}: expected {orig_count} faces "
                      f"but only {available} remain in FacesDataEdited — truncated.")
            ds_assignments[ds_ind] = list(range(face_offset, face_offset + take))
            face_offset += take
        if face_offset < total_new:
            print(f"  WARNING _rebuild_draw_states: {total_new - face_offset} face(s) "
                  f"in FacesDataEdited were not assigned to any draw state.")

    # --- Rebuild each draw list from assigned faces ---
    result = {}
    for ds_ind, ds in enumerate(draw_states):
        descriptors = ds.get('ActiveDescriptors', [])
        orig_raw = base64.b64decode(ds['PrimListData'])
        assigned = ds_assignments.get(ds_ind, [])

        if not descriptors or not assigned:
            result[ds_ind] = orig_raw
            continue

        new_faces = []
        for global_fi in assigned:
            pos_tri = all_pos_faces[global_fi]
            face = []
            for vi in range(3):
                vertex = {}
                for d in descriptors:
                    key = d['key']
                    if key in ('position', 'lighting', 'color0', 'color1'):
                        vertex[key] = pos_tri[vi]
                    elif key.startswith('texture'):
                        ch_ind = int(key[7:])
                        if ch_ind in uv_faces_by_ch and global_fi < len(uv_faces_by_ch[ch_ind]):
                            vertex[key] = uv_faces_by_ch[ch_ind][global_fi][vi]
                        else:
                            vertex[key] = 0  # UV layer absent — warned by exporter
                    else:
                        vertex[key] = 0
                face.append(vertex)
            new_faces.append(face)

        result[ds_ind] = _dl.rebuildDrawList(orig_raw, descriptors, new_faces)

    return result


def writeExpandedMesh(i: int, submesh: dict, new_verts_raw: bytes,
                      new_uvs_per_ch: dict,
                      skin_data: dict = None,
                      skin_data_edited: dict = None,
                      new_skn_dest_size: int = None) -> bool:
    """Write all buffers for one submesh to hammerspace and patch the pointers.

    ``new_uvs_per_ch`` is a dict ``{ch_ind: bytes}`` for channels that have
    changed.  Channels absent from the dict fall back to their original data
    recorded in the JSON so the chunk always contains complete UV data.

    ``skin_data`` is the model-level ``SkinData`` dict from the .sluggie JSON.
    When provided, SKN destination-buffer pointer fields are also patched.
    NOTE: SKN patching only applies when submesh index i == 0, since
    the memClr region starts at submesh 0's vertex buffer.

    When ``submesh`` carries ``FacesDataEdited`` and/or ``UVFacesDataEdited``
    (written by ``encode_mesh_hammerspace``), each draw state's primitive list
    is rebuilt via ``_rebuild_draw_states`` so UV seam splits and face count
    changes are correctly reflected in the GX vertex index stream.

    Returns True on success, False on failure.
    """
    relative_base = int(submesh['SubmeshOffset'], 16)
    vb = submesh['VertexBuffer']
    chunk_name = _chunk_name(submesh['SubmeshOffset'], i)

    # Rebuild draw lists when new face/UV index data is available
    rebuilt_dls = _rebuild_draw_states(submesh)

    # Build ordered sections: vertex → each UV channel → each draw state
    sections = [('verts', new_verts_raw)]

    for ch in submesh.get('UVChannels', []):
        ch_ind = ch['UVChannelIndex']
        raw = new_uvs_per_ch.get(ch_ind)
        if raw is None:
            raw = base64.b64decode(ch['UVChannelData'])
        sections.append((f'uv{ch_ind}', raw))

    for ds_ind, ds in enumerate(submesh.get('DrawStates', [])):
        dl_raw = rebuilt_dls.get(ds_ind, base64.b64decode(ds['PrimListData']))
        sections.append((f'dl{ds_ind}', dl_raw))

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
            dl_raw = rebuilt_dls.get(ds_ind, base64.b64decode(ds['PrimListData']))
            dl_abs = data_abs + offsets[f'dl{ds_ind}']
            _hs.patchPointerField(int(ds['PrimListPtrFieldOffset'], 16), dl_abs, relative_base)
            f.seek(int(ds['PrimListSizeFieldOffset'], 16))
            f.write(struct.pack('>I', len(dl_raw)))

    # Patch SKN destination buffer pointer (and optionally source
    # arrays) when this is the first submesh (submesh 0 is the start of the
    # contiguous memClr region).
    if skin_data is not None and i == 0:
        new_vert_abs = data_abs + offsets['verts']
        _skn.patchSKNForNewDestBuffer(skin_data, new_vert_abs, new_skn_dest_size)
        print(f"  Submesh {i}: SKN memClrPtr patched to 0x{new_vert_abs:X}")
        if skin_data_edited is not None:
            total_sk = _skn.patchSKNSourceArrays(skin_data, skin_data_edited, new_vert_abs)
            if total_sk != -1:
                print(f"  Submesh {i}: SKN source arrays rebuilt "
                      f"({total_sk} vertices in SK1+SK2).")
            else:
                print(f"  Submesh {i}: WARNING — SKN source array rebuild failed; "
                      f"bind-pose data may be stale.")

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

skin_data        = data["SluggiesModel"].get("SkinData")        # None for non-skinned models
skin_data_edited = data["SluggiesModel"].get("SkinDataEdited")  # present when Blender HS export used
use_hammerspace  = data["SluggiesModel"].get("UseHammerspace", False)

# Pre-compute new total dest buffer size for skinned models (sum of all
# submesh vertex buffer byte lengths after editing).
new_skn_dest_size = None
if skin_data is not None:
    total_dest_bytes = 0
    for _sm in submeshes:
        _vb = _sm.get("VertexBuffer", {})
        _edited = _vb.get("VertexBufferDataEdited")
        if _edited:
            total_dest_bytes += len(base64.b64decode(_edited))
        else:
            total_dest_bytes += _vb.get("VertexBufferLength", 0)
    new_skn_dest_size = total_dest_bytes

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
        if "VertexBufferDataEdited" not in vb:
            print(f"  Submesh {i}: no VertexBufferDataEdited data, skipping.")
            continue

        new_verts = base64.b64decode(vb["VertexBufferDataEdited"])
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

        # Guard: any buffer size change requires hammerspace mode.
        # If the exporter did not enable UseHammerspace, refuse to silently
        # relocate data; warn and skip so the user re-exports with the flag on.
        if (vb_size_changed or uv_size_changed) and not use_hammerspace:
            reasons = []
            if vb_size_changed:
                reasons.append(f"vertex buffer {original_vb_length} → {len(new_verts)} bytes")
            for ch in submesh.get("UVChannels", []):
                ch_ind = ch["UVChannelIndex"]
                if ch_ind in new_uvs and len(new_uvs[ch_ind]) != ch["UVChannelLength"]:
                    reasons.append(f"UV ch {ch_ind} {ch['UVChannelLength']} → {len(new_uvs[ch_ind])} bytes")
            print(f"  Submesh {i}: WARNING — buffer size changed ({'; '.join(reasons)}) "
                  f"but UseHammerspace=False in the .sluggie file. "
                  f"Re-export from Blender with Hammerspace Mode enabled. Skipping.")
            continue

        if vb_size_changed or uv_size_changed:
            # Sizes changed — write everything to hammerspace
            ok = writeExpandedMesh(i, submesh, new_verts, new_uvs,
                                   skin_data, skin_data_edited, new_skn_dest_size)
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

# In-place skin source and weight patching (non-hammerspace skinned models)
if skin_data is not None and not use_hammerspace and not unpatch:
    if _skn.patchSKNInPlace(skin_data):
        print("  Skin bind-pose source and weight arrays patched in-place.")

# In-place skin source and weight restoration (non-hammerspace --unpatch)
if skin_data is not None and not use_hammerspace and unpatch:
    if _skn.restoreSKNInPlace(skin_data):
        print("  Skin bind-pose source and weight arrays restored in-place.")

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

