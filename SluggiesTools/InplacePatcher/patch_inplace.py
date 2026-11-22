"""In-place patch of one model: ``python patch_inplace.py <model.sluggie> [--unpatch] [--validate-only]
[--texture-file PNG [--texture-index N]]`` (``start.py --patch`` runs it as a child script).

The run is ``main(argv)``: ``parse_args`` -> ``load_model`` -> ``prepare_output`` (the first copy of the
input DAT) -> ``plan`` (every edit read, encoded and checked; nothing written) -> ``apply`` (the writes,
through one guarded handle, undone as a whole when one fails). A step that cannot go on raises
``PatchRefused``; ``main`` logs it and returns 1.
"""

import os
import sys
from dataclasses import dataclass

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
from binfmt import decode_field as _to_bytes
from binfmt import load_sluggie as _load_sluggie
from binfmt import shader_mode_bytes as _shader_mode_bytes
from compact_channel import compact_channel, compact_channel_fixed_slots

# ---------------------------------------------------------------------------
# Shader-mode classes
# ---------------------------------------------------------------------------

# FourCC modes whose vertex stream includes a per-vertex 'lighting' index.
_LIGHTING_MODES    = frozenset({'Spec', 'RhSp', 'LhSp', 'SpRf', 'GhSp'})
# FourCC modes whose vertex stream does NOT include a 'lighting' index.
_NO_LIGHTING_MODES = frozenset({'Shdw', 'Audi', 'Oeka'})
_ALL_KNOWN_MODES = _LIGHTING_MODES | _NO_LIGHTING_MODES


class PatchRefused(Exception):
    """The run cannot go on. From ``plan`` and the steps before it: nothing was written. From ``apply``:
    the message says whether the writes were undone."""


class _PatchFailed(Exception):
    """A write-phase failure: the writes made so far are undone."""


def abort(message):
    """Stop the run with *message* (``main`` logs it and returns 1). Never returns."""
    raise PatchRefused(message)


@dataclass(frozen=True)
class Options:
    """The command line."""
    json_path:     str
    unpatch:       bool = False
    # every check of a patch (the edits read, encoded and checked), nothing written; the build check of
    # start.py --patch FILE --validate-only. A submesh edit that would be dropped fails it.
    validate_only: bool = False
    texture_file:  str | None = None       # single-texture mode: this PNG ...
    texture_index: int | None = None       # ... into this TextureIndex (else matched by file name)


@dataclass
class Plan:
    """Everything a run writes, checked: ``apply`` only writes."""
    options:         Options
    block_start:     int            # the model's own block: the only range written into
    block_length:    int
    writes:          list           # (log label, offset, bytes), inside the block
    texture_writes:  tuple
    skin_data:       dict | None
    skin_resized:    bool
    bind_pose_scale: tuple | None
    counts:          dict           # per kind of patch, for the log lines
    textures_skipped: int = 0


# ---------------------------------------------------------------------------
# Command line and input
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> Options:
    unpatch = '--unpatch' in argv
    validate_only = '--validate-only' in argv
    texture_file: str | None = None
    texture_index: int | None = None
    rest: list[str] = []
    tokens = iter(a for a in argv if a not in ('--unpatch', '--validate-only'))
    for token in tokens:
        if token == '--texture-file':
            texture_file = next(tokens, None)
            if texture_file is None:
                abort("--texture-file requires a PNG path argument.")
        elif token == '--texture-index':
            index_text = next(tokens, None)
            if index_text is None:
                abort("--texture-index requires an integer argument.")
            try:
                texture_index = int(index_text)
            except ValueError:
                abort(f"--texture-index must be an integer, got: {index_text}")
        else:
            rest.append(token)

    if unpatch and validate_only:
        abort("'--validate-only' checks a patch; it does not combine with '--unpatch'.")
    if unpatch and texture_file:
        abort(
            "'--unpatch' does not accept .png files: pass the model's .sluggie "
            "(e.g. <model>.gpl.sluggie) to restore the original texture bytes."
        )
    if not rest:
        abort("No .sluggies file path provided.\nUsage: python patch_inplace.py <path_to_model.sluggies> [--unpatch]")
    if not os.path.exists(rest[0]):
        abort(f"JSON file not found: {rest[0]}")
    return Options(rest[0], unpatch, validate_only, texture_file, texture_index)


def load_model(options: Options) -> dict:
    """The ``.sluggie`` document, refused when the in-place patcher cannot write it."""
    json_path = options.json_path
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
    if not data["SluggiesModel"].get("Submeshes", []):
        abort("No submeshes found in JSON.")
    return data


def prepare_output(options: Options) -> None:
    """Make sure the output DAT is there to patch: a whole copy of the input, made now when it is missing
    (not for a build check, which creates and copies nothing)."""
    unpatch, validate_only = options.unpatch, options.validate_only
    _slogger.info(
        f"Mode: {'--unpatch (restoring original data)' if unpatch else 'build check (nothing written)' if validate_only else 'patch (writing edited data)'}",
        source="patch_inplace",
    )

    if not os.path.exists(INPUT_DAT):
        abort(f"Input file not found: {INPUT_DAT}\nCannot continue without a source dt_na.dat.")

    # An output shorter than the input is an interrupted copy (or a cut-off file): patching into it would be
    # lost work at best, and a write past its end would grow it with zeros.
    short_output = _hh.outputDatProblem()
    if short_output:
        abort(short_output)
    if os.path.exists(OUTPUT_DAT):
        _slogger.info(f"Output file already exists, skipping copy: {OUTPUT_DAT}", source="patch_inplace")
    elif not validate_only:
        try:
            _hh.copyFileAtomic(INPUT_DAT, OUTPUT_DAT)      # through a temporary file: never a half copy
        except OSError as exc:
            abort(f"Could not copy {INPUT_DAT} to {OUTPUT_DAT}: {exc}")
        _slogger.info(f"Copied {INPUT_DAT} -> {OUTPUT_DAT}", source="patch_inplace")


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def _model_block(model: dict, json_path: str) -> tuple[int, int]:
    """``(start, length)`` of the model's own block in the DAT: the only range this patcher writes into.
    Refuses when the .sluggie names none, or one that cannot be this model's block in the input game files."""
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


def _restore_buffers(i: int, submesh: dict, patches: list, uv_patches: list, normal_patches: list) -> None:
    """Unpatch: queue the original vertex, UV and normal bytes of submesh *i*."""
    vb = submesh.get("VertexBuffer", {})
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


def _edited_buffers(i: int, submesh: dict, patches: list, uv_patches: list, normal_patches: list,
                    dropped_edits: list) -> None:
    """Patch: queue the edited vertex, UV and normal bytes of submesh *i*. An edit whose buffer changed
    size cannot be written in place: it is named in *dropped_edits* (the whole patch is refused later)."""
    vb = submesh.get("VertexBuffer", {})
    if "VertexBufferDataEdited" not in vb:
        _slogger.info(f"Submesh {i}: no VertexBufferDataEdited data, skipping.", source="patch_inplace")
        return

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
        dropped_edits.append(f"submesh {i}: {'; '.join(reasons)}")
        return

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
            dropped_edits.append(f"submesh {i} normals: {nb['NormalBufferLength']} → {len(compact_norm)} bytes")
        else:
            normal_patches.append((i, int(nb["NormalBufferOffset"], 16), compact_norm))
            _slogger.info(f"Submesh {i} normal: {len(compact_norm)} bytes (in-place)", source="patch_inplace")


def _shader_mode_patches(submeshes: list, unpatch: bool) -> list:
    """Shader-mode (Type-7 FourCC) patches, as ``(submesh, display state, offset, bytes)``.

    GUARDRAIL: changing between lighting modes (Spec / RhSp / LhSp / SpRf /
    GhSp) and no-lighting modes (Shdw / Audi / Oeka) alters the per-vertex
    stride of every primitive list in the affected Type-3 group and is
    therefore not supported: a ``ShaderModeEdited`` value that crosses that
    boundary, or an unrecognised mode, refuses the patch.

    Changes within the same class (e.g. Spec → RhSp, or Shdw → Audi) are
    structurally safe and applied as a plain 4-byte FourCC overwrite.
    """
    setting_patches = []
    for i, submesh in enumerate(submeshes):
        for ds_idx, ds in enumerate(submesh.get("DisplayStates", [])):
            if ds.get("DisplayStateId") != 7:
                continue
            off_str = ds.get("ShaderModeFieldOffset")
            if not off_str:
                continue
            off       = int(off_str, 16)
            old_code  = ds.get("ShaderMode", "")
            edit_code = ds.get("ShaderModeEdited")

            if unpatch:
                setting_patches.append((i, ds_idx, off, _shader_mode_bytes(old_code)))
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
                # a known mode: four ASCII characters
                setting_patches.append((i, ds_idx, off, _shader_mode_bytes(edit_code)))
                _slogger.info(f"Submesh {i} DS[{ds_idx}] ShaderMode: \"{old_code}\" -> \"{edit_code}\" at {off_str}", source="patch_inplace")
    return setting_patches


def _param_byte_patches(submeshes: list, unpatch: bool) -> list:
    """Renderer-parameter (DisplayStateParamBytes) patches, as ``(submesh, display state, offset, bytes)``.
    The 3 bytes are struct offsets +1..+3; index 0 (offset +1) is the confirmed specular intensity for
    'Spec'/'LhSp'/'RhSp' modes; indices 1 and 2 are unconfirmed and are written back verbatim.
    See _docs/_docs_model_format/overview.html (display state parameter bytes)."""
    param_patches = []
    for i, submesh in enumerate(submeshes):
        for ds_idx, ds in enumerate(submesh.get("DisplayStates", [])):
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
    return param_patches


def _refuse_hammerspace_only_edits(model: dict) -> None:
    """A rebuilt or excluded submesh needs the Hammerspace builder. The dispatcher never sends such a file
    here; an edited or stale file could."""
    # A rebuilt rigid submesh replaces its whole GPL blob.
    rigid_rebuilt = [
        f"submesh {index}" for index, submesh in enumerate(model.get("Submeshes") or [])
        if submesh.get("RigidRebuild")
    ]
    if rigid_rebuilt:
        abort(
            f"{', '.join(rigid_rebuilt)} carr{'ies' if len(rigid_rebuilt) == 1 else 'y'} a "
            "RigidRebuild (changed topology, Object Mode transform, kept world position "
            "or moved faces). Rigid rebuilds require Hammerspace; patch with "
            "start.py --patch, which routes the file through the Hammerspace builder."
        )

    # An excluded submesh (Blender's "Exclude all unselected meshes") gets empty primitive lists.
    excluded = [
        f"submesh {index}" for index, submesh in enumerate(model.get("Submeshes") or [])
        if submesh.get("Excluded")
    ]
    if excluded:
        abort(
            f"{', '.join(excluded)} {'is' if len(excluded) == 1 else 'are'} excluded from the model. "
            "Excluded meshes require Hammerspace; patch with start.py --patch, which routes the file "
            "through the Hammerspace builder."
        )


def _encoded_texture_writes(json_path: str, descriptors: list, input_dat_size: int, output_dat_size: int,
                            **plan_options) -> tuple[tuple, int]:
    """Encode the PNGs of *descriptors* and lay out their writes. Returns ``(writes, skipped count)``;
    each skipped texture is logged."""
    texture_plan = _tex.build_texture_plan(json_path, descriptors, **plan_options)
    for skipped in texture_plan.skipped:
        fields = [f"expected {skipped.expected_payload_length} bytes"]
        if skipped.generated_payload_length is not None:
            fields.append(f"generated {skipped.generated_payload_length} bytes")
        _slogger.warning(
            f"texture {skipped.texture_index} ({skipped.texture_file_name}): "
            f"{', '.join(fields)}; left unchanged ({skipped.reason})",
            source="patch_inplace",
        )
    writes = _tex.build_texture_writes(descriptors, texture_plan, input_dat_size, output_dat_size)
    return writes, len(texture_plan.skipped)


def _texture_writes(model: dict, options: Options, size_dat: str) -> tuple[tuple, int]:
    """The texture writes of the run (patch: encoded and validated; unpatch: the input's bytes) and the
    number of textures skipped."""
    json_path, unpatch, texture_file = options.json_path, options.unpatch, options.texture_file
    if texture_file and not unpatch:
        # Single-texture mode: patch exactly one texture from a given PNG,
        # independent of the ReimportTextures flag.
        if model.get("UseHammerspace"):
            abort(
                "UseHammerspace is true — single-texture in-place patching is not "
                "supported for Hammerspace models. Use start.py --patch to route "
                "through the Hammerspace builder."
            )
        descriptors = model.get("TextureDescriptors") or []
        if not descriptors:
            abort(f"No TextureDescriptors in {json_path}; cannot patch a texture.")
        if options.texture_index is not None:
            matched = [d for d in descriptors if d.get("TextureIndex") == options.texture_index]
            if not matched:
                abort(
                    f"No descriptor with TextureIndex={options.texture_index} in {json_path}."
                )
            target_descs = matched[:1]
            target_index = options.texture_index
        else:
            png_basename = os.path.basename(texture_file)
            matched = [d for d in descriptors if d.get("TextureFileName") == png_basename]
            if not matched:
                known = [d.get("TextureFileName", "?") for d in descriptors]
                abort(
                    f"'{png_basename}' does not match any TextureFileName in "
                    f"'{json_path}' (known: {', '.join(known)})"
                )
            target_descs = matched[:1]
            target_index = target_descs[0].get("TextureIndex", 0)
        input_dat_size = os.path.getsize(INPUT_DAT)
        output_dat_size = os.path.getsize(size_dat)
        try:
            return _encoded_texture_writes(
                json_path, target_descs, input_dat_size, output_dat_size,
                png_overrides={target_index: os.path.abspath(texture_file)},
            )
        except (_tex.TextureEncodingError, ValueError) as exc:
            abort(str(exc))
    elif model.get("ReimportTextures"):
        if model.get("UseHammerspace"):
            abort(
                "UseHammerspace and ReimportTextures are both true. "
                "Texture re-import is not supported with Hammerspace mode; "
                "disable ReimportTextures in Blender before re-exporting."
            )
        descriptors = model.get("TextureDescriptors") or []
        if descriptors:
            input_dat_size = os.path.getsize(INPUT_DAT)
            output_dat_size = os.path.getsize(size_dat)
            try:
                if unpatch:
                    return _tex.build_unpatch_texture_writes(
                        descriptors, INPUT_DAT, input_dat_size, output_dat_size,
                    ), 0
                return _encoded_texture_writes(json_path, descriptors, input_dat_size, output_dat_size)
            except (_tex.TextureEncodingError, ValueError) as exc:
                abort(str(exc))
    return (), 0


def plan(data: dict, options: Options) -> Plan:
    """Read, encode and check every edit of the run. Writes nothing; raises ``PatchRefused`` when the run
    cannot go on (an edit a patch would leave out, a write outside the model's block, ...)."""
    json_path, unpatch, validate_only = options.json_path, options.unpatch, options.validate_only
    model = data["SluggiesModel"]
    submeshes = model.get("Submeshes", [])
    # The DAT a patch writes into (a build check without an output DAT: the input copy a patch would start from).
    size_dat = OUTPUT_DAT if os.path.exists(OUTPUT_DAT) else INPUT_DAT
    block_start, block_length = _model_block(model, json_path)
    dropped_edits: list[str] = []   # submesh edits a patch would leave out (a build check fails on them)

    patches    = []   # (submesh_idx, file_offset, raw_bytes)
    uv_patches = []   # (submesh_idx, ch_ind, file_offset, raw_bytes)
    normal_patches = []   # (submesh_idx, file_offset, raw_bytes)

    skin_data = model.get("SkinData")  # None for non-skinned models
    if skin_data is not None and not unpatch:
        skn_problems = _skn.skn_edit_problems(
            skin_data, [b["BoneId"] for b in model.get("BoneHierarchy") or [] if "BoneId" in b])
        if skn_problems:
            abort("Skin edit refused, it would crash or break the game: "
                  + "; ".join(skn_problems))
    facial_patches = _facial_position_patches(model, unpatch)
    bone_hierarchy = model.get("BoneHierarchy") or []
    root_scale_patch = _root_scale.root_scale_patch(model, bone_hierarchy, unpatch, abort)
    # Same factors, reused to scale the SKN bind-pose vertices. None when the
    # model has no RootBoneScaleEdited (or it is a no-op unit scale).
    bind_pose_scale = _root_scale.resolve_bind_pose_scale(model)
    if bind_pose_scale == (1.0, 1.0, 1.0):
        bind_pose_scale = None

    # Keep offset to bone reassignments: only the ACT GeoId words change
    # (planner in bone_geo_inplace.py).
    bone_geo_patches = _bone_geo.bone_geo_patches(bone_hierarchy, unpatch, abort)

    for i, submesh in enumerate(submeshes):
        if unpatch:
            _restore_buffers(i, submesh, patches, uv_patches, normal_patches)
        else:
            _edited_buffers(i, submesh, patches, uv_patches, normal_patches, dropped_edits)

    setting_patches = _shader_mode_patches(submeshes, unpatch)     # (submesh_idx, ds_idx, file_offset, raw_bytes)
    param_patches = _param_byte_patches(submeshes, unpatch)        # (submesh_idx, ds_idx, file_offset, raw_bytes)

    if not unpatch:
        _refuse_hammerspace_only_edits(model)
    texture_writes, texture_skipped_count = _texture_writes(model, options, size_dat)

    def refuse(message: str):
        """Stop before the first write: a failed build check, or a refused patch."""
        if validate_only:
            abort("In-place build check failed: " + message)
        abort(f"In-place patch refused, nothing written: {message}")

    skin_resized = skin_data is not None and any(
        sk.get('VertexCntEdited') is not None
        for lst in [skin_data.get('SK1s', []), skin_data.get('SK2s', []), skin_data.get('SKAccs', [])]
        for sk in lst)

    # Edits a patch would leave out refuse the whole patch before the first write
    # (a build check and a real patch alike), so dt_na.dat never holds a partial
    # patch reported as done.
    if not unpatch:
        if skin_resized and _skn.patchSKNInPlaceResized(skin_data, bind_pose_scale, dry_run=True) < 0:
            dropped_edits.append("the skin's resized source arrays do not fit the original ones")
        if dropped_edits:
            refuse("a patch would leave these edits out (re-export from Blender; a changed size is a Hammerspace "
                   "export): " + "; ".join(dropped_edits))

    # Every write, as (log label, offset, bytes), checked against the model's own block: the offsets come from
    # the .sluggie, and nothing else keeps a wrong one out of a neighbouring model.
    planned_writes = (
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
    writes, cut_writes, outside_writes = _dat_writes.fit_to_block(planned_writes, block_start, block_length)
    # A texture is written whole or not at all (its read-back check needs every byte).
    outside_writes += [
        f"texture {tw.texture_index} {tw.kind}: 0x{tw.offset:X}+{tw.payload_length:,}"
        for tw in texture_writes
        if not (block_start <= tw.offset and tw.offset + tw.payload_length <= block_start + block_length)
    ]
    if outside_writes:
        refuse(f"these writes are outside the model's block (0x{block_start:X}+{block_length:,}); the .sluggie is "
               "damaged or does not belong to these game files: " + "; ".join(outside_writes))
    for note in cut_writes:
        _slogger.warning(f"{note}: the original buffer runs past the end of the model's block; the bytes beyond it "
                         "belong to the next model and are left alone.", source="patch_inplace")

    return Plan(
        options=options,
        block_start=block_start,
        block_length=block_length,
        writes=writes,
        texture_writes=texture_writes,
        skin_data=skin_data,
        skin_resized=skin_resized,
        bind_pose_scale=bind_pose_scale,
        counts={
            'vertex': len(patches),
            'uv': len(uv_patches),
            'normal': len(normal_patches),
            'shader': len(setting_patches),
            'param': len(param_patches),
            'facial': len(facial_patches),
            'bone_geo': len(bone_geo_patches),
            'root_scale': 1 if root_scale_patch else 0,
            'textures': len({w.texture_index for w in texture_writes}),
        },
        textures_skipped=texture_skipped_count,
    )


def _counts_text(counts: dict) -> str:
    return (
        f"{counts['vertex']} vertex, {counts['uv']} UV, {counts['normal']} normal, "
        f"{counts['shader']} shader-mode, {counts['param']} renderer-param, {counts['facial']} facial-pose, "
        f"{counts['bone_geo']} bone-geo, {counts['root_scale']} root-scale, {counts['textures']} texture patch(es)"
    )


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _write_all(f, the_plan: Plan) -> None:
    """The whole write phase, through the guarded handle *f*. Raises on any failure; the caller's ``with``
    block then undoes the writes made so far."""
    unpatch, skin_data, bind_pose_scale = the_plan.options.unpatch, the_plan.skin_data, the_plan.bind_pose_scale
    for label, offset, raw in the_plan.writes:
        f.seek(offset)
        f.write(raw)
        shown = raw.hex() if len(raw) <= 12 else f"{len(raw)} bytes"
        _slogger.info(f"{label}: wrote {shown} at 0x{offset:X}", source="patch_inplace")

    # Texture writes grouped by texture_index; each is verified byte for byte
    # before the per-texture success line is logged.
    writes_by_tex: dict = {}
    for tw in the_plan.texture_writes:
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
        if the_plan.skin_resized:
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
    if the_plan.skin_resized:
        total_sk = _skn.patchSKNInPlaceResized(skin_data, bind_pose_scale, dat=f)
        if total_sk < 0:                               # the dry run of plan() passed, so this is unexpected
            raise _PatchFailed("the resized skin patch failed (see the error above)")
        _slogger.info(
            f"Skin bind-pose source and weight arrays patched in-place (resized, {total_sk} SK1+SK2 verts).",
            source="patch_inplace")
    elif _skn.patchSKNInPlace(skin_data, bind_pose_scale, dat=f):
        _slogger.info("Skin bind-pose source and weight arrays patched in-place.", source="patch_inplace")


def apply(the_plan: Plan) -> None:
    """Write the plan into the output DAT. Every write goes through one guarded handle: none leaves the
    model's block, and a failure midway puts the replaced bytes back, so the DAT holds the whole patch or
    none of it (``PatchRefused`` either way)."""
    unpatch = the_plan.options.unpatch
    _slogger.info(f"Writing {_counts_text(the_plan.counts)} to {OUTPUT_DAT} ...", source="patch_inplace")
    try:
        with _dat_writes.GuardedDat(OUTPUT_DAT, the_plan.block_start, the_plan.block_length) as dat:
            _write_all(dat, the_plan)
    except _dat_writes.RollbackFailed as exc:
        abort(f"In-place {'unpatch' if unpatch else 'patch'} failed and could not be undone: {exc}. The output "
              "dt_na.dat is only partly written; restore it from a backup before using it.")
    except (_PatchFailed, _dat_writes.OutOfBlockWrite, OSError, KeyError, ValueError) as exc:
        abort(f"In-place {'unpatch' if unpatch else 'patch'} failed; every write was undone and the output "
              f"dt_na.dat is as it was before: {exc}")


def _log_summary(the_plan: Plan) -> None:
    counts = the_plan.counts
    summary = (
        f"--- Summary ---\n"
        f"Vertex submeshes patched (in-place) : {counts['vertex']}\n"
        f"UV channels patched (in-place)      : {counts['uv']}\n"
        f"Normal buffers patched (in-place)   : {counts['normal']}\n"
        f"ShaderMode (Type-7 FourCC) patched  : {counts['shader']}\n"
        f"Renderer params (specular) patched  : {counts['param']}\n"
        f"Facial position poses patched       : {counts['facial']}\n"
        f"Bone GeoId fields patched           : {counts['bone_geo']}\n"
        f"Root-bone SRT scale patched         : {counts['root_scale']}\n"
        f"Bind-pose vertices scaled           : {1 if the_plan.bind_pose_scale else 0}\n"
        f"Textures patched (in-place)         : {counts['textures']}\n"
        f"Textures skipped (mip layout)       : {the_plan.textures_skipped}\n"
        f"Output file                         : {OUTPUT_DAT}"
    )
    _slogger.info(summary, source="patch_inplace")
    if the_plan.options.unpatch:
        _slogger.info("Done. The output file has been restored to the original data.", source="patch_inplace")
    else:
        _slogger.info("Done. You can now overwrite your original dt_na.dat in the game folder.", source="patch_inplace")


def _run(argv: list[str]) -> Plan:
    """The run up to its last write. Returns the plan that was checked (a build check) or written."""
    options = parse_args(argv)
    data = load_model(options)
    prepare_output(options)
    the_plan = plan(data, options)
    if not options.validate_only:
        apply(the_plan)
    return the_plan


def main(argv=None) -> int:
    """The command line. Returns the exit code."""
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        the_plan = _run(argv)
        refusal = None
    except PatchRefused as exc:
        refusal = str(exc)
    if refusal is not None:
        _slogger.error(refusal, source="patch_inplace")
        if '--validate-only' not in argv:        # a build check runs inside a chain: no prompt
            answer = _slogger.ask("\nPress any key to exit...", kind="key")
            _slogger.log_user_input("Press any key to exit", answer, source="patch_inplace")
        return 1
    if the_plan.options.validate_only:
        _slogger.info(
            f"In-place build check passed | {os.path.basename(the_plan.options.json_path)} | "
            f"{_counts_text(the_plan.counts)} | nothing written",
            source="patch_inplace",
        )
        return 0
    _log_summary(the_plan)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
