import os
import sys
import shutil
import json
import base64
import struct

INPUT_DAT  = '../1_Input/dt_na.dat'
OUTPUT_DIR = '../3_Output_Dat'
OUTPUT_DAT = os.path.join(OUTPUT_DIR, 'dt_na.dat')

# Allow importing sibling modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patch_skn_inplace as _skn

# ---------------------------------------------------------------------------
# Shader-mode conversion constants and helpers
# ---------------------------------------------------------------------------

# FourCC modes whose vertex stream includes a per-vertex 'lighting' index.
_LIGHTING_MODES    = frozenset({'Spec', 'RhSp', 'LhSp', 'SpRf', 'GhSp'})
# FourCC modes whose vertex stream does NOT include a 'lighting' index.
_NO_LIGHTING_MODES = frozenset({'Shdw', 'Audi', 'Oeka'})


def _to_bytes(data) -> bytes:
    """Decode binary data that is either a base64 string or a list of byte values."""
    if isinstance(data, list):
        return bytes(data)
    return base64.b64decode(data)


def _shader_mode_to_bytes(s: str) -> bytes:
    """Convert a ShaderMode value to its 4 raw bytes.

    Accepts either the 4-char printable-ASCII form (e.g. 'Spec') that export.py
    writes for modes whose bytes are all in range 32-126, or the 8-char lowercase
    hex form (e.g. '11110000') that export.py writes for non-printable modes.
    """
    if len(s) == 8 and all(c in '0123456789abcdefABCDEF' for c in s):
        return bytes.fromhex(s)
    return s.encode('ascii', errors='replace').ljust(4, b'\x00')[:4]


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


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from('>H', data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('>I', data, offset)[0]


def _facial_position_patches(model: dict, restore: bool) -> list[tuple[int, bytes]]:
    """Build position-pose patches from exported facial metadata and Blender edits."""
    facial = model.get('FacialPoseData')
    if not facial:
        return []

    edited_lookup = {
        (facial_object.get('ObjectIndex'), pose.get('PoseIndex')): _to_bytes(pose['PoseData'])
        for facial_object in model.get('FacialPoseDataEdited', {}).get('Objects', [])
        for pose in facial_object.get('PositionPoseEdits', [])
    }

    submesh_buffers = []
    for submesh_index, submesh in enumerate(model.get('Submeshes', [])):
        vb = submesh.get('VertexBuffer', {})
        original_data = vb.get('VertexBufferData')
        edited_data = vb.get('VertexBufferDataEdited')
        if original_data is None or (edited_data is None and not restore):
            continue
        original = _to_bytes(original_data)
        edited = original if edited_data is None else _to_bytes(edited_data)
        vertex_stride = (
            vb.get('VertexBufferCompCount', 3)
            * _comp_size(vb.get('VertexBufferQuantizeInfo', 0))
        )
        if len(original) == len(edited) and vertex_stride > 0 and len(original) % vertex_stride == 0:
            submesh_buffers.append((submesh_index, original, edited, vertex_stride))

    facial_patches = []
    for facial_object in facial.get('Objects', []):
        object_index = facial_object.get('ObjectIndex')
        submesh_index = facial_object.get('SubmeshIndex')
        position = facial_object.get('Position', {})
        original_poses = [_to_bytes(data) for data in position.get('PoseData', [])]
        pose_offsets = [int(offset, 16) for offset in position.get('PoseAbsoluteOffsets', [])]
        vertex_count = position.get('EntryCount', 0)
        component_count = position.get('ComponentCount', 3)
        component_size = position.get('ComponentSize', 2)
        pose_stride = component_count * component_size
        vertex_indices = [
            vertex_index
            for run in position.get('Runs', [])
            for vertex_index in range(
                run.get('FirstVertex', 0),
                run.get('FirstVertex', 0) + run.get('VertexCount', 0),
            )
        ]
        if (
            submesh_index is None
            or len(vertex_indices) != vertex_count
            or len(original_poses) != len(pose_offsets)
            or component_size != 2
            or component_count < 3
            or any(len(pose) != vertex_count * pose_stride for pose in original_poses)
        ):
            continue

        matched = next(
            (entry for entry in submesh_buffers if entry[0] == submesh_index),
            None,
        )
        for pose_index, (pose_offset, original_pose) in enumerate(
            zip(pose_offsets, original_poses)
        ):
            sparse_edit = edited_lookup.get((object_index, pose_index))
            if restore:
                patched_pose = original_pose
            elif sparse_edit is not None:
                if len(sparse_edit) != len(original_pose):
                    abort(
                        f"Facial object {object_index}, pose {pose_index}: edited length "
                        f"{len(sparse_edit)} does not match original {len(original_pose)}."
                    )
                patched_pose = sparse_edit
            elif pose_index == 0 and matched is not None:
                _, original, edited, vertex_stride = matched
                patched_pose = bytearray(original_pose)
                for mapped_index, vertex_index in enumerate(vertex_indices):
                    if vertex_index * vertex_stride + 6 > len(original) or len(original) != len(edited):
                        abort(
                            f"Submesh {submesh_index}: facial vertex {vertex_index} "
                            "is outside the editable vertex buffer."
                        )
                    original_vertex = struct.unpack_from(
                        '>3h', original, vertex_index * vertex_stride
                    )
                    edited_vertex = struct.unpack_from(
                        '>3h', edited, vertex_index * vertex_stride
                    )
                    for component in range(3):
                        value_offset = mapped_index * pose_stride + component * component_size
                        value = struct.unpack_from('>h', patched_pose, value_offset)[0]
                        value += edited_vertex[component] - original_vertex[component]
                        if not -32768 <= value <= 32767:
                            abort(
                                f"Submesh {submesh_index}: facial pose coordinate overflow "
                                f"in object {object_index}, pose {pose_index}."
                            )
                        struct.pack_into('>h', patched_pose, value_offset, value)
                patched_pose = bytes(patched_pose)
            else:
                patched_pose = original_pose
            facial_patches.append((pose_offset, patched_pose))
        print(
            f"  Submesh {submesh_index}: queued {len(original_poses)} facial position poses "
            f"({vertex_count} mapped vertices each)."
        )

    return facial_patches


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

unpatch    = '--unpatch' in sys.argv
argv_clean = [a for a in sys.argv[1:] if a != '--unpatch']

if not argv_clean:
    abort("No .sluggies file path provided.\nUsage: python patch_inplace.py <path_to_model.sluggies> [--unpatch]")

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
setting_patches  = []   # (submesh_idx, ds_idx, file_offset, raw_bytes)

skin_data = data["SluggiesModel"].get("SkinData")  # None for non-skinned models
facial_patches = _facial_position_patches(data["SluggiesModel"], unpatch)

for i, submesh in enumerate(submeshes):
    vb = submesh.get("VertexBuffer", {})

    if unpatch:
        vb_data = vb.get("VertexBufferData")
        if vb_data:
            raw = _to_bytes(vb_data)
            patches.append((i, int(vb["VertexBufferOffset"], 16), raw))
            print(f"  Submesh {i}: queued {len(raw)} vertex bytes at {vb['VertexBufferOffset']}")

        for ch in submesh.get("UVChannels", []):
            ch_ind   = ch.get("UVChannelIndex", "?")
            uv_data  = ch.get("UVChannelData")
            if uv_data:
                raw = _to_bytes(uv_data)
                uv_patches.append((i, ch_ind, int(ch["UVChannelOffset"], 16), raw))
                print(f"  Submesh {i} UV ch {ch_ind}: queued {len(raw)} bytes at {ch['UVChannelOffset']}")

    else:
        # -- patch mode --
        if "VertexBufferDataEdited" not in vb:
            print(f"  Submesh {i}: no VertexBufferDataEdited data, skipping.")
            continue

        new_verts = _to_bytes(vb["VertexBufferDataEdited"])
        original_vb_length = vb.get("VertexBufferLength", 0)

        # Collect edited UV channels
        new_uvs: dict[int, bytes] = {}
        uv_size_changed = False
        for ch in submesh.get("UVChannels", []):
            ch_ind = ch["UVChannelIndex"]
            edited = ch.get("UVChannelDataEdited")
            if edited:
                raw = _to_bytes(edited)
                new_uvs[ch_ind] = raw
                if len(raw) != ch["UVChannelLength"]:
                    uv_size_changed = True

        vb_size_changed = len(new_verts) != original_vb_length

        if vb_size_changed or uv_size_changed:
            reasons = []
            if vb_size_changed:
                reasons.append(f"vertex buffer {original_vb_length} → {len(new_verts)} bytes")
            for ch in submesh.get("UVChannels", []):
                ch_ind = ch["UVChannelIndex"]
                if ch_ind in new_uvs and len(new_uvs[ch_ind]) != ch["UVChannelLength"]:
                    reasons.append(f"UV ch {ch_ind} {ch['UVChannelLength']} → {len(new_uvs[ch_ind])} bytes")
            print(f"  Submesh {i}: WARNING — buffer size changed ({'; '.join(reasons)}). "
                  f"Buffer size changes are not currently supported; skipping.")
            continue

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
# Collect shader-mode (Type-7 FourCC) patches.
#
# GUARDRAIL: changing between lighting modes (Spec / RhSp / LhSp / SpRf /
# GhSp) and no-lighting modes (Shdw / Audi / Oeka) alters the per-vertex
# stride of every primitive list in the affected Type-3 group and is
# therefore not supported.  patch_inplace.py aborts if any SettingEdited value
# crosses that boundary or uses an unrecognised mode.
#
# Changes within the same class (e.g. Spec → RhSp, or Shdw → Audi) are
# structurally safe and applied as a plain 4-byte FourCC overwrite.
# ---------------------------------------------------------------------------

_ALL_KNOWN_MODES = _LIGHTING_MODES | _NO_LIGHTING_MODES

for i, submesh in enumerate(submeshes):
    display_states = submesh.get("DisplayStates", [])

    for ds_idx, ds in enumerate(display_states):
        if ds.get("DisplayStateId") != 7:
            continue
        off_str = ds.get("ShaderModeFieldOffset")
        if not off_str:
            continue
        off       = int(off_str, 16)
        old_code  = ds.get("ShaderMode", "")
        edit_code = ds.get("ShaderModeEdited")

        if unpatch:
            raw = _shader_mode_to_bytes(old_code)
            setting_patches.append((i, ds_idx, off, raw))
            print(f"  Submesh {i} DS[{ds_idx}] ShaderMode: restore \"{old_code}\" at {off_str}")
        else:
            if edit_code is None:
                continue
            if edit_code not in _ALL_KNOWN_MODES:
                abort(
                    f"Submesh {i} DS[{ds_idx}]: unrecognised shader mode \"{edit_code}\". "
                    f"Known modes: {sorted(_ALL_KNOWN_MODES)}."
                )
            if (old_code in _LIGHTING_MODES) != (edit_code in _LIGHTING_MODES):
                abort(
                    f"Submesh {i} DS[{ds_idx}]: cannot change shader mode "
                    f"from \"{old_code}\" to \"{edit_code}\" — crossing the "
                    f"lighting / no-lighting boundary would require repacking "
                    f"all primitive lists in the Type-3 group."
                )
            raw = edit_code.encode('ascii', errors='replace').ljust(4, b' ')[:4]
            setting_patches.append((i, ds_idx, off, raw))
            print(f"  Submesh {i} DS[{ds_idx}] ShaderMode: \"{old_code}\" -> \"{edit_code}\" at {off_str}")

# ---------------------------------------------------------------------------
# Write in-place patches
# ---------------------------------------------------------------------------

if patches or uv_patches or setting_patches or facial_patches:
    print(f"\nWriting {len(patches)} vertex, {len(uv_patches)} UV, "
          f"{len(setting_patches)} shader-mode, {len(facial_patches)} facial-pose "
          f"patch(es) to {OUTPUT_DAT} ...")
    with open(OUTPUT_DAT, 'r+b') as f:
        for i, offset, raw in patches:
            f.seek(offset)
            f.write(raw)
            print(f"  Submesh {i} vertex: wrote {len(raw)} bytes at 0x{offset:X}")
        for i, ch_ind, offset, raw in uv_patches:
            f.seek(offset)
            f.write(raw)
            print(f"  Submesh {i} UV ch {ch_ind}: wrote {len(raw)} bytes at 0x{offset:X}")
        for i, ds_idx, offset, raw in setting_patches:
            f.seek(offset)
            f.write(raw)
            print(f"  Submesh {i} DS[{ds_idx}] shader: wrote {raw!r} at 0x{offset:X}")
        for offset, raw in facial_patches:
            f.seek(offset)
            f.write(raw)
            print(f"  Facial pose: wrote {len(raw)} bytes at 0x{offset:X}")

# In-place skin source and weight patching (skinned models)
if skin_data is not None and not unpatch:
    needs_resize = any(
        sk.get('VertexCntEdited') is not None
        for lst in [skin_data.get('SK1s', []), skin_data.get('SK2s', []), skin_data.get('SKAccs', [])]
        for sk in lst
    )
    if needs_resize:
        total_sk = _skn.patchSKNInPlaceResized(skin_data)
        if total_sk >= 0:
            print(f"  Skin bind-pose source and weight arrays patched in-place (resized, {total_sk} SK1+SK2 verts).")
    else:
        if _skn.patchSKNInPlace(skin_data):
            print("  Skin bind-pose source and weight arrays patched in-place.")

# In-place skin source and weight restoration (--unpatch)
if skin_data is not None and unpatch:
    needs_restore_block = any(
        sk.get('VertexCntEdited') is not None
        for lst in [skin_data.get('SK1s', []), skin_data.get('SK2s', []), skin_data.get('SKAccs', [])]
        for sk in lst
    )
    if needs_restore_block:
        if _skn.restoreSKNBlockInPlace(skin_data):
            print("  Skin bind-pose source arrays restored in-place (resized block undone).")
    else:
        if _skn.restoreSKNInPlace(skin_data):
            print("  Skin bind-pose source and weight arrays restored in-place.")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print(f"\n--- Summary ---")
print(f"Vertex submeshes patched (in-place) : {len(patches)}")
print(f"UV channels patched (in-place)      : {len(uv_patches)}")
print(f"ShaderMode (Type-7 FourCC) patched  : {len(setting_patches)}")
print(f"Facial position poses patched       : {len(facial_patches)}")
print(f"Output file                         : {OUTPUT_DAT}")
if unpatch:
    print("Done. The output file has been restored to the original data.")
else:
    print("Done. You can now overwrite your original dt_na.dat in the game folder.")

