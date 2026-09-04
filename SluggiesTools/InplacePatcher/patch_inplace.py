import os
import sys
import shutil
import json
import base64
import struct

# Resolve project directories relative to this file so the script works
# regardless of the current working directory.
_THIS_DIR  = os.path.dirname(os.path.abspath(__file__))   # .../SluggiesTools/InplacePatcher
_TOOLS_DIR = os.path.dirname(_THIS_DIR)                    # .../SluggiesTools
_ROOT_DIR  = os.path.dirname(_TOOLS_DIR)                   # project root

# Ensure SluggiesTools/ (slogger, texture_helper, …) and this directory
# (patch_skn_inplace) are importable.
for _p in (_TOOLS_DIR, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Step 2.2 – Initialize universal logger in child process.
import slogger as _slogger
_slogger.configure()

INPUT_DAT  = os.path.join(_ROOT_DIR, '1_Input', 'dt_na.dat')
OUTPUT_DIR = os.path.join(_ROOT_DIR, '3_Output_Dat')
OUTPUT_DAT = os.path.join(OUTPUT_DIR, 'dt_na.dat')

import patch_skn_inplace as _skn
import texture_helper as _tex
import root_scale as _root_scale
from compact_channel import comp_size as _comp_size, compact_channel

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
    _slogger.error(message, source="patch_inplace")
    answer = input("\nPress any key to exit...")
    _slogger.log_user_input("Press any key to exit", answer, source="patch_inplace")
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
        _slogger.info(
            f"Submesh {submesh_index}: queued {len(original_poses)} facial position poses "
            f"({vertex_count} mapped vertices each).",
            source="patch_inplace",
        )

    return facial_patches


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

unpatch    = '--unpatch' in sys.argv
argv_clean = [a for a in sys.argv[1:] if a != '--unpatch']

# Parse --texture-file <png> and --texture-index <N> for single-texture mode.
_texture_file: str | None = None
_texture_index_arg: int | None = None
_argv_rest: list[str] = []
_it = iter(argv_clean)
for _tok in _it:
    if _tok == '--texture-file':
        _texture_file = next(_it, None)
        if _texture_file is None:
            abort("--texture-file requires a PNG path argument.")
    elif _tok == '--texture-index':
        _idx_str = next(_it, None)
        if _idx_str is None:
            abort("--texture-index requires an integer argument.")
        try:
            _texture_index_arg = int(_idx_str)
        except ValueError:
            abort(f"--texture-index must be an integer, got: {_idx_str}")
    else:
        _argv_rest.append(_tok)
argv_clean = _argv_rest

if unpatch and _texture_file:
    abort(
        "'--unpatch' does not accept .png files: pass the model's .sluggie "
        "(e.g. <model>.gpl.sluggie) to restore the original texture bytes."
    )

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
_slogger.info(
    f"Mode: {'--unpatch (restoring original data)' if unpatch else 'patch (writing edited data)'}",
    source="patch_inplace",
)

if not os.path.exists(OUTPUT_DIR):
    os.mkdir(OUTPUT_DIR)
    _slogger.info(f"Created folder: {OUTPUT_DIR}/", source="patch_inplace")

if not os.path.exists(INPUT_DAT):
    abort(f"Input file not found: {INPUT_DAT}\nCannot continue without a source dt_na.dat.")

if os.path.exists(OUTPUT_DAT):
    _slogger.info(f"Output file already exists, skipping copy: {OUTPUT_DAT}", source="patch_inplace")
else:
    shutil.copy2(INPUT_DAT, OUTPUT_DAT)
    _slogger.info(f"Copied {INPUT_DAT} -> {OUTPUT_DAT}", source="patch_inplace")

# ---------------------------------------------------------------------------
# Build patch list
# ---------------------------------------------------------------------------

patches    = []   # (submesh_idx, file_offset, raw_bytes)
uv_patches = []   # (submesh_idx, ch_ind, file_offset, raw_bytes)
normal_patches = []   # (submesh_idx, file_offset, raw_bytes)
setting_patches  = []   # (submesh_idx, ds_idx, file_offset, raw_bytes)
bone_geo_patches = []   # (bone_id, file_offset, raw_bytes)

skin_data = data["SluggiesModel"].get("SkinData")  # None for non-skinned models
facial_patches = _facial_position_patches(data["SluggiesModel"], unpatch)
bone_hierarchy = data["SluggiesModel"].get("BoneHierarchy") or []
root_scale_patch = _root_scale.root_scale_patch(data["SluggiesModel"], bone_hierarchy, unpatch, abort)
# Same factors, reused to scale the SKN bind-pose vertices. None when the
# model has no RootBoneScaleEdited (or it is a no-op unit scale).
bind_pose_scale = _root_scale.resolve_bind_pose_scale(data["SluggiesModel"])
if bind_pose_scale == (1.0, 1.0, 1.0):
    bind_pose_scale = None


def _bone_geo_raw_original(bd: dict) -> int:
    if bd.get('GeoIdRaw') is not None:
        return int(bd['GeoIdRaw'])
    if bd.get('Skinned'):
        return 0xFFFF
    return int(bd.get('GeoId', 0xFFFF))


if bone_hierarchy:
    missing_offsets = []
    for bd in bone_hierarchy:
        if unpatch:
            target_geo_raw = _bone_geo_raw_original(bd)
        else:
            if bd.get('GeoIdEdited') is None:
                continue
            target_geo_raw = int(bd['GeoIdEdited'])

        if target_geo_raw < 0 or target_geo_raw > 0xFFFF:
            abort(
                f"Bone {bd.get('BoneId', '?')}: GeoId value {target_geo_raw} is out of range (0..65535)."
            )

        field_off = bd.get('GeoIdFieldOffset')
        if not field_off:
            missing_offsets.append(int(bd.get('BoneId', -1)))
            continue

        original_geo_raw = _bone_geo_raw_original(bd)
        if target_geo_raw == original_geo_raw:
            continue

        bone_geo_patches.append((
            int(bd.get('BoneId', -1)),
            int(field_off, 16),
            struct.pack('>H', target_geo_raw),
        ))

    if missing_offsets and not unpatch:
        abort(
            "This .sluggie contains non-skinned bone reassignment edits but is missing "
            "BoneHierarchy.GeoIdFieldOffset metadata required for ACT in-place patching. "
            "Re-export the model with the latest SluggiesTools export.py, then export from Blender again. "
            f"Affected bones: {', '.join(str(x) for x in sorted(x for x in missing_offsets if x >= 0))}"
        )

for i, submesh in enumerate(submeshes):
    vb = submesh.get("VertexBuffer", {})

    if unpatch:
        vb_data = vb.get("VertexBufferData")
        if vb_data:
            raw = _to_bytes(vb_data)
            patches.append((i, int(vb["VertexBufferOffset"], 16), raw))
            _slogger.info(f"Submesh {i}: queued {len(raw)} vertex bytes at {vb['VertexBufferOffset']}", source="patch_inplace")

        for ch in submesh.get("UVChannels", []):
            ch_ind   = ch.get("UVChannelIndex", "?")
            uv_data  = ch.get("UVChannelData")
            if uv_data:
                raw = _to_bytes(uv_data)
                uv_patches.append((i, ch_ind, int(ch["UVChannelOffset"], 16), raw))
                _slogger.info(f"Submesh {i} UV ch {ch_ind}: queued {len(raw)} bytes at {ch['UVChannelOffset']}", source="patch_inplace")

        nb = submesh.get("NormalBuffer", {})
        nb_data = nb.get("NormalBufferData")
        if nb_data and nb.get("NormalBufferOffset"):
            raw = _to_bytes(nb_data)
            normal_patches.append((i, int(nb["NormalBufferOffset"], 16), raw))
            _slogger.info(f"Submesh {i} normal: queued {len(raw)} bytes at {nb['NormalBufferOffset']}", source="patch_inplace")

    else:
        # -- patch mode --
        if "VertexBufferDataEdited" not in vb:
            _slogger.info(f"Submesh {i}: no VertexBufferDataEdited data, skipping.", source="patch_inplace")
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
            _slogger.warning(
                f"Submesh {i}: buffer size changed ({'; '.join(reasons)}). "
                f"Buffer size changes are not currently supported; skipping.",
                source="patch_inplace",
            )
            continue

        # Sizes unchanged — in-place write
        patches.append((i, int(vb["VertexBufferOffset"], 16), new_verts))
        _slogger.info(f"Submesh {i}: {len(new_verts)} vertex bytes (in-place)", source="patch_inplace")

        for ch in submesh.get("UVChannels", []):
            ch_ind = ch["UVChannelIndex"]
            if ch_ind not in new_uvs:
                continue
            raw = new_uvs[ch_ind]
            uv_patches.append((i, ch_ind, int(ch["UVChannelOffset"], 16), raw))
            _slogger.info(f"Submesh {i} UV ch {ch_ind}: {len(raw)} bytes (in-place)", source="patch_inplace")

        # Standalone NormalBuffer patching
        nb = submesh.get("NormalBuffer", {})
        nb_edited = nb.get("NormalBufferDataEdited")
        if nb_edited and nb.get("NormalBufferOffset"):
            donor_norm = _to_bytes(nb["NormalBufferData"])
            donor_faces_raw = _to_bytes(nb["NormalFacesData"])
            expanded_norm = _to_bytes(nb_edited)
            expanded_faces_raw = _to_bytes(nb["NormalFacesDataEdited"])
            norm_comp = nb.get("NormalBufferCompCount", 3)
            norm_quant = nb.get("NormalBufferQuantizeInfo", 0)
            norm_stride = norm_comp * _comp_size(norm_quant)
            loop_count = len(donor_faces_raw) // 2
            donor_indices = [
                int.from_bytes(donor_faces_raw[k*2:k*2+2], 'big')
                for k in range(loop_count)
            ]
            expanded_indices = [
                int.from_bytes(expanded_faces_raw[k*2:k*2+2], 'big')
                for k in range(len(expanded_faces_raw) // 2)
            ]
            compact_norm, _, _ = compact_channel(
                f'sub{i} normal', norm_stride,
                donor_norm, donor_indices,
                expanded_norm, expanded_indices,
                loop_count,
            )
            if len(compact_norm) != nb["NormalBufferLength"]:
                _slogger.warning(
                    f"Submesh {i}: normal buffer size changed "
                    f"({nb['NormalBufferLength']} → {len(compact_norm)} bytes). "
                    f"Skipping normal patch.",
                    source="patch_inplace",
                )
            else:
                normal_patches.append((i, int(nb["NormalBufferOffset"], 16), compact_norm))
                _slogger.info(f"Submesh {i} normal: {len(compact_norm)} bytes (in-place)", source="patch_inplace")

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
            _slogger.info(f"Submesh {i} DS[{ds_idx}] ShaderMode: restore \"{old_code}\" at {off_str}", source="patch_inplace")
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
            _slogger.info(f"Submesh {i} DS[{ds_idx}] ShaderMode: \"{old_code}\" -> \"{edit_code}\" at {off_str}", source="patch_inplace")

# ---------------------------------------------------------------------------
# Build texture writes (patch: encode and validate; unpatch: restore from input)
# ---------------------------------------------------------------------------

texture_writes: tuple[_tex.TextureWrite, ...] = ()
texture_skipped_count = 0

_model = data["SluggiesModel"]

if _texture_file and not unpatch:
    # Single-texture mode: patch exactly one texture from a given PNG,
    # independent of the ReimportTextures flag.
    if _model.get("UseHammerspace"):
        abort(
            "UseHammerspace is true — single-texture in-place patching is not "
            "supported for Hammerspace models. Use start.py --patch to route "
            "through the Hammerspace builder."
        )
    _descriptors = _model.get("TextureDescriptors") or []
    if not _descriptors:
        abort(f"No TextureDescriptors in {json_path}; cannot patch a texture.")
    if _texture_index_arg is not None:
        _matched = [d for d in _descriptors if d.get("TextureIndex") == _texture_index_arg]
        if not _matched:
            abort(
                f"No descriptor with TextureIndex={_texture_index_arg} in {json_path}."
            )
        _target_descs = _matched[:1]
        _target_index = _texture_index_arg
    else:
        _png_basename = os.path.basename(_texture_file)
        _matched = [d for d in _descriptors if d.get("TextureFileName") == _png_basename]
        if not _matched:
            _known = [d.get("TextureFileName", "?") for d in _descriptors]
            abort(
                f"'{_png_basename}' does not match any TextureFileName in "
                f"'{json_path}' (known: {', '.join(_known)})"
            )
        _target_descs = _matched[:1]
        _target_index = _target_descs[0].get("TextureIndex", 0)
    _input_dat_size = os.path.getsize(INPUT_DAT)
    _output_dat_size = os.path.getsize(OUTPUT_DAT)
    try:
        _plan = _tex.build_texture_plan(
            json_path, _target_descs,
            png_overrides={_target_index: os.path.abspath(_texture_file)},
        )
        texture_skipped_count = len(_plan.skipped)
        for _sk in _plan.skipped:
            _sk_fields = [f"expected {_sk.expected_payload_length} bytes"]
            if _sk.generated_payload_length is not None:
                _sk_fields.append(f"generated {_sk.generated_payload_length} bytes")
            _slogger.warning(
                f"texture {_sk.texture_index} ({_sk.texture_file_name}): "
                f"{', '.join(_sk_fields)}; left unchanged ({_sk.reason})",
                source="patch_inplace",
            )
        texture_writes = _tex.build_texture_writes(
            _target_descs, _plan, _input_dat_size, _output_dat_size,
        )
    except (_tex.TextureEncodingError, ValueError) as exc:
        abort(str(exc))
elif _model.get("ReimportTextures"):
    if _model.get("UseHammerspace"):
        abort(
            "UseHammerspace and ReimportTextures are both true. "
            "Texture re-import is not supported with Hammerspace mode; "
            "disable ReimportTextures in Blender before re-exporting."
        )
    _descriptors = _model.get("TextureDescriptors") or []
    if _descriptors:
        _input_dat_size = os.path.getsize(INPUT_DAT)
        _output_dat_size = os.path.getsize(OUTPUT_DAT)
        try:
            if unpatch:
                texture_writes = _tex.build_unpatch_texture_writes(
                    _descriptors, INPUT_DAT, _input_dat_size, _output_dat_size,
                )
            else:
                _plan = _tex.build_texture_plan(json_path, _descriptors)
                texture_skipped_count = len(_plan.skipped)
                for _sk in _plan.skipped:
                    _sk_fields = [f"expected {_sk.expected_payload_length} bytes"]
                    if _sk.generated_payload_length is not None:
                        _sk_fields.append(f"generated {_sk.generated_payload_length} bytes")
                    _slogger.warning(
                        f"texture {_sk.texture_index} ({_sk.texture_file_name}): "
                        f"{', '.join(_sk_fields)}; left unchanged ({_sk.reason})",
                        source="patch_inplace",
                    )
                texture_writes = _tex.build_texture_writes(
                    _descriptors, _plan, _input_dat_size, _output_dat_size,
                )
        except (_tex.TextureEncodingError, ValueError) as exc:
            abort(str(exc))

# ---------------------------------------------------------------------------
# Write in-place patches
# ---------------------------------------------------------------------------

_textures_patched = len({w.texture_index for w in texture_writes})

if patches or uv_patches or normal_patches or setting_patches or facial_patches or bone_geo_patches or root_scale_patch or texture_writes:
    _slogger.info(
        f"Writing {len(patches)} vertex, {len(uv_patches)} UV, "
    f"{len(normal_patches)} normal, {len(setting_patches)} shader-mode, "
    f"{len(facial_patches)} facial-pose, {len(bone_geo_patches)} bone-geo, "
    f"{1 if root_scale_patch else 0} root-scale, "
        f"{_textures_patched} texture "
        f"patch(es) to {OUTPUT_DAT} ...",
        source="patch_inplace",
    )
    with open(OUTPUT_DAT, 'r+b') as f:
        for i, offset, raw in patches:
            f.seek(offset)
            f.write(raw)
            _slogger.info(f"Submesh {i} vertex: wrote {len(raw)} bytes at 0x{offset:X}", source="patch_inplace")
        for i, ch_ind, offset, raw in uv_patches:
            f.seek(offset)
            f.write(raw)
            _slogger.info(f"Submesh {i} UV ch {ch_ind}: wrote {len(raw)} bytes at 0x{offset:X}", source="patch_inplace")
        for i, offset, raw in normal_patches:
            f.seek(offset)
            f.write(raw)
            _slogger.info(f"Submesh {i} normal: wrote {len(raw)} bytes at 0x{offset:X}", source="patch_inplace")
        for i, ds_idx, offset, raw in setting_patches:
            f.seek(offset)
            f.write(raw)
            _slogger.info(f"Submesh {i} DS[{ds_idx}] shader: wrote {raw!r} at 0x{offset:X}", source="patch_inplace")
        for offset, raw in facial_patches:
            f.seek(offset)
            f.write(raw)
            _slogger.info(f"Facial pose: wrote {len(raw)} bytes at 0x{offset:X}", source="patch_inplace")
        for bone_id, offset, raw in bone_geo_patches:
            f.seek(offset)
            f.write(raw)
            _slogger.info(
                f"Bone {bone_id} GeoId: wrote {raw.hex()} at 0x{offset:X}",
                source="patch_inplace",
            )
        if root_scale_patch:
            bone_id, offset, raw = root_scale_patch
            f.seek(offset)
            f.write(raw)
            _slogger.info(
                f"Bone {bone_id} root SRT scale: wrote {raw.hex()} at 0x{offset:X}",
                source="patch_inplace",
            )
        # Apply texture writes grouped by texture_index; verify each write
        # byte-for-byte before logging the per-texture success line (PLAN 4.2).
        _writes_by_tex: dict = {}
        for _tw in texture_writes:
            _writes_by_tex.setdefault(_tw.texture_index, []).append(_tw)
        for _tex_idx, _tex_group in _writes_by_tex.items():
            _img_bytes = 0
            _pal_bytes = 0
            for _tw in _tex_group:
                f.seek(_tw.offset)
                f.write(_tw.bytes)
                f.seek(_tw.offset)
                _readback = f.read(_tw.payload_length)
                if _readback != _tw.bytes:
                    _differ = sum(a != b for a, b in zip(_readback, _tw.bytes))
                    abort(
                        f"texture {_tw.texture_index} {_tw.kind}: "
                        f"write verification failed at 0x{_tw.offset:X}: "
                        f"{_differ} byte(s) differ in {_tw.payload_length} written"
                    )
                if _tw.kind == "image":
                    _img_bytes = _tw.payload_length
                else:
                    _pal_bytes = _tw.payload_length
            _log_parts = [f"{_img_bytes} image bytes"]
            if _pal_bytes:
                _log_parts.append(f"{_pal_bytes} palette bytes")
            _slogger.info(
                f"texture {_tex_idx}: wrote {', '.join(_log_parts)}",
                source="patch_inplace",
            )

# In-place skin source and weight patching (skinned models)
if skin_data is not None and not unpatch:
    needs_resize = any(
        sk.get('VertexCntEdited') is not None
        for lst in [skin_data.get('SK1s', []), skin_data.get('SK2s', []), skin_data.get('SKAccs', [])]
        for sk in lst
    )
    if bind_pose_scale is not None:
        _slogger.info(
            f"Applying root-bone scale to SKN bind-pose vertices: {bind_pose_scale}",
            source="patch_inplace",
        )
    if needs_resize:
        total_sk = _skn.patchSKNInPlaceResized(skin_data, bind_pose_scale)
        if total_sk >= 0:
            _slogger.info(f"Skin bind-pose source and weight arrays patched in-place (resized, {total_sk} SK1+SK2 verts).", source="patch_inplace")
    else:
        if _skn.patchSKNInPlace(skin_data, bind_pose_scale):
            _slogger.info("Skin bind-pose source and weight arrays patched in-place.", source="patch_inplace")

# In-place skin source and weight restoration (--unpatch)
if skin_data is not None and unpatch:
    needs_restore_block = any(
        sk.get('VertexCntEdited') is not None
        for lst in [skin_data.get('SK1s', []), skin_data.get('SK2s', []), skin_data.get('SKAccs', [])]
        for sk in lst
    )
    if needs_restore_block:
        if _skn.restoreSKNBlockInPlace(skin_data):
            _slogger.info("Skin bind-pose source arrays restored in-place (resized block undone).", source="patch_inplace")
    else:
        if _skn.restoreSKNInPlace(skin_data):
            _slogger.info("Skin bind-pose source and weight arrays restored in-place.", source="patch_inplace")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

summary = (
    f"--- Summary ---\n"
    f"Vertex submeshes patched (in-place) : {len(patches)}\n"
    f"UV channels patched (in-place)      : {len(uv_patches)}\n"
    f"Normal buffers patched (in-place)   : {len(normal_patches)}\n"
    f"ShaderMode (Type-7 FourCC) patched  : {len(setting_patches)}\n"
    f"Facial position poses patched       : {len(facial_patches)}\n"
    f"Bone GeoId fields patched           : {len(bone_geo_patches)}\n"
    f"Root-bone SRT scale patched         : {1 if root_scale_patch else 0}\n"
    f"Bind-pose vertices scaled           : {1 if bind_pose_scale else 0}\n"
    f"Textures patched (in-place)         : {_textures_patched}\n"
    f"Textures skipped (mip layout)       : {texture_skipped_count}\n"
    f"Output file                         : {OUTPUT_DAT}"
)
_slogger.info(summary, source="patch_inplace")
if unpatch:
    _slogger.info("Done. The output file has been restored to the original data.", source="patch_inplace")
else:
    _slogger.info("Done. You can now overwrite your original dt_na.dat in the game folder.", source="patch_inplace")

