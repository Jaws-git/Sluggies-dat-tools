import os
import sys

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

# Initialize universal logger in child process.
import slogger as _slogger
_slogger.configure()

INPUT_DAT  = os.path.join(_ROOT_DIR, '1_Input', 'dt_na.dat')
OUTPUT_DIR = os.path.join(_ROOT_DIR, '3_Output_Dat')
OUTPUT_DAT = os.path.join(OUTPUT_DIR, 'dt_na.dat')

import patch_skn_inplace as _skn
import texture_helper as _tex
import root_scale as _root_scale
import bone_geo_inplace as _bone_geo
import dat_writes as _dat_writes
from Hammerspace import HammerspaceHelper as _hh
import facial_pose_edits as _facial_edits
from binfmt import comp_size as _comp_size
from binfmt import decode_field as _decode_field
from binfmt import load_sluggie as _load_sluggie
from binfmt import shader_mode_bytes as _shader_mode_bytes
from compact_channel import compact_channel, compact_channel_fixed_slots

# ---------------------------------------------------------------------------
# Shader-mode conversion constants and helpers
# ---------------------------------------------------------------------------

# FourCC modes whose vertex stream includes a per-vertex 'lighting' index.
_LIGHTING_MODES    = frozenset({'Spec', 'RhSp', 'LhSp', 'SpRf', 'GhSp'})
# FourCC modes whose vertex stream does NOT include a 'lighting' index.
_NO_LIGHTING_MODES = frozenset({'Shdw', 'Audi', 'Oeka'})


def _to_bytes(data) -> bytes:
    """Decode a binary field: ``x:`` hex string (legacy: base64 or ``z:`` zlib) or a list of byte values."""
    return _decode_field(data)


def _shader_mode_to_bytes(s: str) -> bytes:
    """Convert a ShaderMode value to its 4 raw bytes.

    Accepts either the 4-char printable-ASCII form (e.g. 'Spec') that export.py
    writes for modes whose bytes are all in range 32-126, or the 8-char lowercase
    hex form (e.g. '11110000') that export.py writes for non-printable modes.
    """
    return _shader_mode_bytes(s)


class _PatchFailed(Exception):
    """A write-phase failure: the writes made so far are undone."""


def abort(message):
    _slogger.error(message, source="patch_inplace")
    if '--validate-only' not in sys.argv:        # a build check runs inside a chain: no prompt
        answer = _slogger.ask("\nPress any key to exit...", kind="key")
        _slogger.log_user_input("Press any key to exit", answer, source="patch_inplace")
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _facial_position_patches(model: dict, restore: bool) -> list[tuple[int, bytes]]:
    """``(offset, bytes)`` of every facial position pose array (``facial_pose_edits``)."""
    try:
        arrays = _facial_edits.position_pose_arrays(model, restore)
    except ValueError as exc:
        abort(f"Facial pose edit refused: {exc}.")
    for submesh_index in sorted({a.submesh_index for a in arrays}):
        objects = {a.object_index for a in arrays if a.submesh_index == submesh_index}
        _slogger.info(
            f"Submesh {submesh_index}: queued "
            f"{sum(1 for a in arrays if a.submesh_index == submesh_index)} facial position poses "
            f"({len(objects)} object(s)).",
            source="patch_inplace",
        )
    return [(a.absolute_offset, a.data) for a in arrays]


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

unpatch    = '--unpatch' in sys.argv
# --validate-only: every check of a patch (the edits read, encoded and checked), nothing written; the build check
# of start.py --patch FILE --validate-only. A submesh edit that would be dropped fails it.
validate_only = '--validate-only' in sys.argv
argv_clean = [a for a in sys.argv[1:] if a not in ('--unpatch', '--validate-only')]

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

if unpatch and validate_only:
    abort("'--validate-only' checks a patch; it does not combine with '--unpatch'.")

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

try:
    data = _load_sluggie(json_path)
except (OSError, ValueError) as e:
    abort(f"Could not read {json_path}: {e}")

if "SluggiesModel" not in data:
    abort(f"JSON does not contain a 'SluggiesModel' entry: {json_path}")
if data["SluggiesModel"].get("DonorEntry") is not None:
    # A slot export's offsets are its embedded donor block's, not the DAT's.
    abort(f"{os.path.basename(json_path)} is a slot export (it carries its own donor block): only the "
          "Hammerspace patcher can write it (start.py --patch does).")

submeshes = data["SluggiesModel"].get("Submeshes", [])
if not submeshes:
    abort("No submeshes found in JSON.")


def _model_block(model: dict) -> tuple[int, int]:
    """``(start, length)`` of the model's own block in the DAT: the only range this patcher writes into.
    Aborts when the .sluggie names none, or one that cannot be this model's block in the input game files."""
    try:
        start, length = int(model["ModelOffset"], 16), int(model["ModelLength"])
    except (KeyError, TypeError, ValueError):
        abort(f"{os.path.basename(json_path)} has no usable ModelOffset / ModelLength; export the model again.")
    record = None
    chunk, file_index = model.get("ChunkNumber"), model.get("FileIndex")
    if os.path.isfile(_hh.INPUT_DOL) and isinstance(chunk, int) and isinstance(file_index, int):
        record = _hh.readDolEntry(chunk, file_index)
        if record[0] < 0:
            abort(f"{os.path.basename(json_path)}: ChunkNumber {chunk} / FileIndex {file_index} is not a model "
                  "of the input main.dol; the .sluggie does not belong to these game files.")
    problem = _dat_writes.block_problem(start, length, os.path.getsize(INPUT_DAT), record)
    if problem:
        abort(f"In-place patch refused, nothing written: {problem}.")
    return start, length

# ---------------------------------------------------------------------------
# Ensure output file exists
# ---------------------------------------------------------------------------

mode_label = "original" if unpatch else "edited"
_slogger.info(
    f"Mode: {'--unpatch (restoring original data)' if unpatch else 'build check (nothing written)' if validate_only else 'patch (writing edited data)'}",
    source="patch_inplace",
)

if not os.path.exists(INPUT_DAT):
    abort(f"Input file not found: {INPUT_DAT}\nCannot continue without a source dt_na.dat.")

# An output shorter than the input is an interrupted copy (or a cut-off file): patching into it would be lost
# work at best, and a write past its end would grow it with zeros.
_short_output = _hh.outputDatProblem()
if _short_output:
    abort(_short_output)
if os.path.exists(OUTPUT_DAT):
    _slogger.info(f"Output file already exists, skipping copy: {OUTPUT_DAT}", source="patch_inplace")
elif not validate_only:                      # a build check creates and copies nothing
    try:
        _hh.copyFileAtomic(INPUT_DAT, OUTPUT_DAT)      # through a temporary file: never a half copy
    except OSError as exc:
        abort(f"Could not copy {INPUT_DAT} to {OUTPUT_DAT}: {exc}")
    _slogger.info(f"Copied {INPUT_DAT} -> {OUTPUT_DAT}", source="patch_inplace")
# The DAT a patch writes into (a build check without an output DAT: the input copy a patch would start from).
_SIZE_DAT = OUTPUT_DAT if os.path.exists(OUTPUT_DAT) else INPUT_DAT
_block_start, _block_length = _model_block(data["SluggiesModel"])
_dropped_edits: list[str] = []   # submesh edits a patch would leave out (a build check fails on them)

# ---------------------------------------------------------------------------
# Build patch list
# ---------------------------------------------------------------------------

patches    = []   # (submesh_idx, file_offset, raw_bytes)
uv_patches = []   # (submesh_idx, ch_ind, file_offset, raw_bytes)
normal_patches = []   # (submesh_idx, file_offset, raw_bytes)
setting_patches  = []   # (submesh_idx, ds_idx, file_offset, raw_bytes)
param_patches    = []   # (submesh_idx, ds_idx, file_offset, raw_bytes)

skin_data = data["SluggiesModel"].get("SkinData")  # None for non-skinned models
if skin_data is not None and not unpatch:
    _skn_problems = _skn.skn_edit_problems(
        skin_data, [b["BoneId"] for b in data["SluggiesModel"].get("BoneHierarchy") or [] if "BoneId" in b])
    if _skn_problems:
        abort("Skin edit refused, it would crash or break the game: "
              + "; ".join(_skn_problems))
facial_patches = _facial_position_patches(data["SluggiesModel"], unpatch)
bone_hierarchy = data["SluggiesModel"].get("BoneHierarchy") or []
root_scale_patch = _root_scale.root_scale_patch(data["SluggiesModel"], bone_hierarchy, unpatch, abort)
# Same factors, reused to scale the SKN bind-pose vertices. None when the
# model has no RootBoneScaleEdited (or it is a no-op unit scale).
bind_pose_scale = _root_scale.resolve_bind_pose_scale(data["SluggiesModel"])
if bind_pose_scale == (1.0, 1.0, 1.0):
    bind_pose_scale = None


# Keep offset to bone reassignments: only the ACT GeoId words change
# (planner in bone_geo_inplace.py).
bone_geo_patches = _bone_geo.bone_geo_patches(bone_hierarchy, unpatch, abort)

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
            # ERROR, not a warning: the user's edit is not applied (legacy
            # files only; a current export carries RigidRebuild instead and
            # is refused below).
            _slogger.error(
                f"Submesh {i}: buffer size changed ({'; '.join(reasons)}). "
                f"Buffer size changes cannot be patched in place; this submesh's edit is NOT applied. "
                f"Re-export from Blender (a changed size is a Hammerspace export) and patch with start.py --patch.",
                source="patch_inplace",
            )
            _dropped_edits.append(f"submesh {i}: {'; '.join(reasons)}")
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
            if int(vb.get("VertexBufferCompCount", 3)) == 6:
                # Interleaved skinned normals (pos + 6): the slot layout is
                # pinned to the position slots, so values are patched in
                # place and splits resolved per slot by majority.
                compact_norm, _, conflict_slots = compact_channel_fixed_slots(
                    f'sub{i} normal', norm_stride,
                    donor_norm, donor_indices,
                    expanded_norm, expanded_indices,
                    loop_count,
                )
                if conflict_slots:
                    _slogger.warning(
                        f"Submesh {i}: {conflict_slots} skinned vertex slot(s) were given "
                        "more than one normal; interleaved normals cannot be split per face "
                        "corner, so each slot keeps the value most of its corners asked for.",
                        source="patch_inplace",
                    )
            else:
                compact_norm, _, _ = compact_channel(
                    f'sub{i} normal', norm_stride,
                    donor_norm, donor_indices,
                    expanded_norm, expanded_indices,
                    loop_count,
                )
            if len(compact_norm) != nb["NormalBufferLength"]:
                _slogger.error(
                    f"Submesh {i}: normal buffer size changed "
                    f"({nb['NormalBufferLength']} → {len(compact_norm)} bytes). "
                    f"The normal edit is NOT applied; re-export from Blender and patch with start.py --patch.",
                    source="patch_inplace",
                )
                _dropped_edits.append(f"submesh {i} normals: {nb['NormalBufferLength']} → {len(compact_norm)} bytes")
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
# Collect renderer-parameter (DisplayStateParamBytes) patches. The 3 bytes are
# struct offsets +1..+3; index 0 (offset +1) is the confirmed specular
# intensity for 'Spec'/'LhSp'/'RhSp' modes; indices 1 and 2 are unconfirmed and
# are written back verbatim.
# See _docs/_docs_model_format/overview.html (display state parameter bytes).
# ---------------------------------------------------------------------------

for i, submesh in enumerate(submeshes):
    display_states = submesh.get("DisplayStates", [])

    for ds_idx, ds in enumerate(display_states):
        if ds.get("DisplayStateId") != 7:
            continue
        off_str = ds.get("DisplayStateParamBytesFieldOffset")
        if not off_str:
            continue
        off      = int(off_str, 16)
        old_hex  = ds.get("DisplayStateParamBytes", "000000")
        edit_hex = ds.get("DisplayStateParamBytesEdited")

        if unpatch:
            raw = bytes.fromhex(old_hex).ljust(3, b'\x00')[:3]
            param_patches.append((i, ds_idx, off, raw))
            _slogger.info(f"Submesh {i} DS[{ds_idx}] ParamBytes: restore \"{old_hex}\" at {off_str}", source="patch_inplace")
        else:
            if edit_hex is None:
                continue
            try:
                raw = bytes.fromhex(edit_hex)
            except ValueError:
                abort(f"Submesh {i} DS[{ds_idx}]: DisplayStateParamBytesEdited \"{edit_hex}\" is not valid hex.")
            if len(raw) != 3:
                abort(
                    f"Submesh {i} DS[{ds_idx}]: DisplayStateParamBytesEdited must be exactly "
                    f"3 bytes (6 hex chars), got {len(raw)}."
                )
            param_patches.append((i, ds_idx, off, raw))
            _slogger.info(f"Submesh {i} DS[{ds_idx}] ParamBytes: \"{old_hex}\" -> \"{edit_hex}\" at {off_str}", source="patch_inplace")

# ---------------------------------------------------------------------------
# Build texture writes (patch: encode and validate; unpatch: restore from input)
# ---------------------------------------------------------------------------

texture_writes: tuple[_tex.TextureWrite, ...] = ()
texture_skipped_count = 0

_model = data["SluggiesModel"]

# A rebuilt rigid submesh replaces its whole GPL
# blob, which only the Hammerspace builder can do. The dispatcher never sends
# such a file here; an edited or stale file could.
_rigid_rebuilt = [
    f"submesh {index}" for index, submesh in enumerate(_model.get("Submeshes") or [])
    if submesh.get("RigidRebuild")
]
if _rigid_rebuilt and not unpatch:
    abort(
        f"{', '.join(_rigid_rebuilt)} carr{'ies' if len(_rigid_rebuilt) == 1 else 'y'} a "
        "RigidRebuild (changed topology, Object Mode transform, kept world position "
        "or moved faces). Rigid rebuilds require Hammerspace; patch with "
        "start.py --patch, which routes the file through the Hammerspace builder."
    )

# An excluded submesh (Blender's "Exclude all unselected meshes") gets empty
# primitive lists, which only the Hammerspace builder writes.
_excluded = [
    f"submesh {index}" for index, submesh in enumerate(_model.get("Submeshes") or [])
    if submesh.get("Excluded")
]
if _excluded and not unpatch:
    abort(
        f"{', '.join(_excluded)} {'is' if len(_excluded) == 1 else 'are'} excluded from the model. "
        "Excluded meshes require Hammerspace; patch with start.py --patch, which routes the file "
        "through the Hammerspace builder."
    )

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
    _output_dat_size = os.path.getsize(_SIZE_DAT)
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
        _output_dat_size = os.path.getsize(_SIZE_DAT)
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


def _refuse(message: str):
    """Stop before the first write: a failed build check, or a refused patch."""
    if validate_only:
        _slogger.error("In-place build check failed: " + message, source="patch_inplace")
        raise SystemExit(1)
    abort(f"In-place patch refused, nothing written: {message}")


_skin_resized = skin_data is not None and any(
    sk.get('VertexCntEdited') is not None
    for lst in [skin_data.get('SK1s', []), skin_data.get('SK2s', []), skin_data.get('SKAccs', [])]
    for sk in lst)

# Edits a patch would leave out refuse the whole patch before the first write
# (a build check and a real patch alike), so dt_na.dat never holds a partial
# patch reported as done.
if not unpatch:
    if _skin_resized and _skn.patchSKNInPlaceResized(skin_data, bind_pose_scale, dry_run=True) < 0:
        _dropped_edits.append("the skin's resized source arrays do not fit the original ones")
    if _dropped_edits:
        _refuse("a patch would leave these edits out (re-export from Blender; a changed size is a Hammerspace "
                "export): " + "; ".join(_dropped_edits))

# Every write, as (log label, offset, bytes), checked against the model's own block: the offsets come from the
# .sluggie, and nothing else keeps a wrong one out of a neighbouring model.
_planned_writes = (
    [(f"Submesh {i} vertex", offset, raw) for i, offset, raw in patches]
    + [(f"Submesh {i} UV ch {ch_ind}", offset, raw) for i, ch_ind, offset, raw in uv_patches]
    + [(f"Submesh {i} normal", offset, raw) for i, offset, raw in normal_patches]
    + [(f"Submesh {i} DS[{ds_idx}] shader", offset, raw) for i, ds_idx, offset, raw in setting_patches]
    + [(f"Submesh {i} DS[{ds_idx}] params", offset, raw) for i, ds_idx, offset, raw in param_patches]
    + [("Facial pose", offset, raw) for offset, raw in facial_patches]
    + [(f"Bone {bone_id} GeoId", offset, raw) for bone_id, offset, raw in bone_geo_patches]
    + ([(f"Bone {root_scale_patch[0]} root SRT scale", root_scale_patch[1], root_scale_patch[2])]
       if root_scale_patch else [])
)
_writes, _cut_writes, _outside_writes = _dat_writes.fit_to_block(_planned_writes, _block_start, _block_length)
# A texture is written whole or not at all (its read-back check needs every byte).
_outside_writes += [
    f"texture {_tw.texture_index} {_tw.kind}: 0x{_tw.offset:X}+{_tw.payload_length:,}"
    for _tw in texture_writes
    if not (_block_start <= _tw.offset and _tw.offset + _tw.payload_length <= _block_start + _block_length)
]
if _outside_writes:
    _refuse(f"these writes are outside the model's block (0x{_block_start:X}+{_block_length:,}); the .sluggie is "
            "damaged or does not belong to these game files: " + "; ".join(_outside_writes))
for _note in _cut_writes:
    _slogger.warning(f"{_note}: the original buffer runs past the end of the model's block; the bytes beyond it "
                     "belong to the next model and are left alone.", source="patch_inplace")

if validate_only:
    _slogger.info(
        f"In-place build check passed | {os.path.basename(json_path)} | {len(patches)} vertex, "
        f"{len(uv_patches)} UV, {len(normal_patches)} normal, {len(setting_patches)} shader-mode, "
        f"{len(param_patches)} renderer-param, {len(facial_patches)} facial-pose, {len(bone_geo_patches)} bone-geo, "
        f"{1 if root_scale_patch else 0} root-scale, {_textures_patched} texture patch(es) | nothing written",
        source="patch_inplace",
    )
    raise SystemExit(0)


def _write_all(f) -> None:
    """The whole write phase, through the guarded handle *f*. Raises on any failure; the caller's ``with``
    block then undoes the writes made so far."""
    for label, offset, raw in _writes:
        f.seek(offset)
        f.write(raw)
        shown = raw.hex() if len(raw) <= 12 else f"{len(raw)} bytes"
        _slogger.info(f"{label}: wrote {shown} at 0x{offset:X}", source="patch_inplace")

    # Texture writes grouped by texture_index; each is verified byte for byte
    # before the per-texture success line is logged.
    writes_by_tex: dict = {}
    for tw in texture_writes:
        writes_by_tex.setdefault(tw.texture_index, []).append(tw)
    for tex_idx, tex_group in writes_by_tex.items():
        img_bytes = 0
        pal_bytes = 0
        for tw in tex_group:
            f.seek(tw.offset)
            f.write(tw.bytes)
            f.seek(tw.offset)
            readback = f.read(tw.payload_length)
            if readback != tw.bytes:
                differ = sum(a != b for a, b in zip(readback, tw.bytes))
                raise _PatchFailed(
                    f"texture {tw.texture_index} {tw.kind}: write verification failed at 0x{tw.offset:X}: "
                    f"{differ} byte(s) differ in {tw.payload_length} written")
            if tw.kind == "image":
                img_bytes = tw.payload_length
            else:
                pal_bytes = tw.payload_length
        log_parts = [f"{img_bytes} image bytes"]
        if pal_bytes:
            log_parts.append(f"{pal_bytes} palette bytes")
        _slogger.info(f"texture {tex_idx}: wrote {', '.join(log_parts)}", source="patch_inplace")

    if skin_data is None:
        return
    if unpatch:
        # In-place skin source and weight restoration
        if _skin_resized:
            if _skn.restoreSKNBlockInPlace(skin_data, dat=f):
                _slogger.info("Skin bind-pose source arrays restored in-place (resized block undone).",
                              source="patch_inplace")
        elif _skn.restoreSKNInPlace(skin_data, dat=f):
            _slogger.info("Skin bind-pose source and weight arrays restored in-place.", source="patch_inplace")
        return
    # In-place skin source and weight patching
    if bind_pose_scale is not None:
        _slogger.info(f"Applying root-bone scale to SKN bind-pose vertices: {bind_pose_scale}",
                      source="patch_inplace")
    if _skin_resized:
        total_sk = _skn.patchSKNInPlaceResized(skin_data, bind_pose_scale, dat=f)
        if total_sk < 0:                               # the dry run above passed, so this is unexpected
            raise _PatchFailed("the resized skin patch failed (see the error above)")
        _slogger.info(
            f"Skin bind-pose source and weight arrays patched in-place (resized, {total_sk} SK1+SK2 verts).",
            source="patch_inplace")
    elif _skn.patchSKNInPlace(skin_data, bind_pose_scale, dat=f):
        _slogger.info("Skin bind-pose source and weight arrays patched in-place.", source="patch_inplace")


_slogger.info(
    f"Writing {len(patches)} vertex, {len(uv_patches)} UV, {len(normal_patches)} normal, "
    f"{len(setting_patches)} shader-mode, {len(param_patches)} renderer-param, {len(facial_patches)} facial-pose, "
    f"{len(bone_geo_patches)} bone-geo, {1 if root_scale_patch else 0} root-scale, {_textures_patched} texture "
    f"patch(es) to {OUTPUT_DAT} ...",
    source="patch_inplace",
)
# Every write goes through one guarded handle: none leaves the model's block, and a failure midway puts the
# replaced bytes back, so the DAT holds the whole patch or none of it.
try:
    with _dat_writes.GuardedDat(OUTPUT_DAT, _block_start, _block_length) as _dat:
        _write_all(_dat)
except _dat_writes.RollbackFailed as exc:
    abort(f"In-place {'unpatch' if unpatch else 'patch'} failed and could not be undone: {exc}. The output "
          "dt_na.dat is only partly written; restore it from a backup before using it.")
except (_PatchFailed, _dat_writes.OutOfBlockWrite, OSError, KeyError, ValueError) as exc:
    abort(f"In-place {'unpatch' if unpatch else 'patch'} failed; every write was undone and the output dt_na.dat "
          f"is as it was before: {exc}")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

summary = (
    f"--- Summary ---\n"
    f"Vertex submeshes patched (in-place) : {len(patches)}\n"
    f"UV channels patched (in-place)      : {len(uv_patches)}\n"
    f"Normal buffers patched (in-place)   : {len(normal_patches)}\n"
    f"ShaderMode (Type-7 FourCC) patched  : {len(setting_patches)}\n"
    f"Renderer params (specular) patched  : {len(param_patches)}\n"
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

