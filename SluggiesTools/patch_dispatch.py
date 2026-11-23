"""Patch routing for ``start.py --patch`` / ``--unpatch``: which patcher a ``.sluggie`` or ``.png`` goes to.

``start.py`` only parses the command line and calls :func:`run_patching` / :func:`run_clear_target`. Here:

* ``hammerspace_section_args`` reads the edits a ``.sluggie`` carries and names the sections the Hammerspace
  builder has to rebuild (``--gpl build``, ``--skn build``, ``--tex build``);
* ``_needs_hammerspace`` decides between the Hammerspace builder and the in-place patcher (the Blender exporter's
  ``UseHammerspace``, edits only Hammerspace applies, unused characters);
* ``_patch_sluggie`` / ``_patch_png`` launch the patcher (and a stadium's collision step) as child processes;
* ``_lod_pairs`` / ``_build_partner_block`` check a High / Low pair patched in one run against each other;
* ``_run_patching`` walks the file list and reports whether every file went through.

Imported as ``SluggiesTools.patch_dispatch`` (the dispatcher has put the project root on ``sys.path``); it
launches child scripts and never patches anything itself.
"""

import os
import subprocess
import sys

if getattr(sys, 'frozen', False):
    ROOT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, 'SluggiesTools')
HS_DIR = os.path.join(TOOLS_DIR, 'Hammerspace')
# In front, in this order (the dispatcher has the first two there already). Not appended: in a frozen build the
# runtime holds its own copies of these modules, and a child script must find the loose files beside the
# executable first. With the Hammerspace folder at the end, a child's ``import HammerspaceHelper`` took the
# bundled copy, whose paths are relative to ``_internal`` (it looked for 3_Output_Dat one folder too high).
for _index, _path in enumerate((ROOT_DIR, TOOLS_DIR, HS_DIR)):
    if _path not in sys.path:
        sys.path.insert(_index, _path)

import SluggiesTools.slogger as slogger  # noqa: E402
import SluggiesTools.texture_helper as _tex  # noqa: E402
import SluggiesTools.model_files as _model_files  # noqa: E402
from SluggiesTools.binfmt import decode_field as _decode_field  # noqa: E402
from SluggiesTools.binfmt import load_sluggie as _load_sluggie  # noqa: E402
import UntanglePolicy  # noqa: E402

SEARCH_DIR = os.path.join(ROOT_DIR, '2_Output_Assets')
PATCH_SCRIPT = os.path.join(TOOLS_DIR, 'InplacePatcher', 'patch_inplace.py')
HS_MAIN_SCRIPT = os.path.join(HS_DIR, 'HammerspaceMain.py')
COLLISION_SCRIPT = os.path.join(HS_DIR, 'StadiumCollision.py')
# Model directories of the unused characters: no data block of their own, written only through Hammerspace.
UNUSED_CHARACTER_DIR_INDICES = frozenset(UntanglePolicy.UNUSED_CHARACTER_DIRS)


_SLUGGIE_CACHE = {}
_SLUGGIE_CACHE_SIZE = 8


def _read_sluggie(path):
    """``_load_sluggie(path)``, kept while the file is unchanged: one run resolves a file several times (the
    High-first sort, the pair check, the patch loop), each a full decompress and parse. Callers only read it."""
    try:
        stat = os.stat(path)
    except OSError:
        return _load_sluggie(path)
    key = (_load_sluggie, os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
    if key not in _SLUGGIE_CACHE:
        if len(_SLUGGIE_CACHE) >= _SLUGGIE_CACHE_SIZE:
            _SLUGGIE_CACHE.clear()
        _SLUGGIE_CACHE[key] = _load_sluggie(path)
    return _SLUGGIE_CACHE[key]


def python_script_command(script, *args):
    """A child-script command that works in source and frozen builds (as ``start.python_script_command``)."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--_run-script', script, *args]
    return [sys.executable, script, *args]


def hammerspace_section_args(model):
    """Select rebuild modes required by exported hammerspace edit markers."""
    use_compression = model.get('UseCompression', True)

    def decode_binary(value):
        return _decode_field(value, use_compression)

    changed_positions = []
    has_uv_edits = False
    has_normal_edits = False
    has_color_edits = False
    for submesh in model.get('Submeshes', []):
        vertex_buffer = submesh.get('VertexBuffer', {})
        edited = decode_binary(vertex_buffer.get('VertexBufferDataEdited'))
        original = decode_binary(vertex_buffer.get('VertexBufferData'))
        if edited is not None and original is not None and edited != original:
            changed_positions.append(submesh)
        if any(
            channel.get('UVChannelDataEdited') is not None
            and channel.get('UVFacesDataEdited') is not None
            and decode_binary(channel.get('UVChannelDataEdited'))
                != decode_binary(channel.get('UVChannelData'))
            for channel in submesh.get('UVChannels', [])
        ):
            has_uv_edits = True
        normal_buffer = submesh.get('NormalBuffer', {})
        if (
            normal_buffer.get('NormalBufferDataEdited') is not None
            and decode_binary(normal_buffer.get('NormalBufferDataEdited'))
                != decode_binary(normal_buffer.get('NormalBufferData'))
        ):
            has_normal_edits = True
        if any(
            channel.get('ColorChannelDataEdited') is not None
            and decode_binary(channel.get('ColorChannelDataEdited'))
                != decode_binary(channel.get('ColorChannelData'))
            for channel in submesh.get('ColorChannels', [])
        ):
            has_color_edits = True

    # A custom submesh is a new GPL submesh, so it always needs the GPL
    # builder; one bound to an appended PNG also needs the TEX builder. A
    # rebuilt rigid submesh (RigidRebuild) replaces its GPL blob the same way,
    # and a new surface on it may bind an appended PNG too.
    custom_submeshes = model.get('CustomSubmeshes') or []
    rigid_rebuilds = [
        submesh['RigidRebuild'] for submesh in model.get('Submeshes', [])
        if submesh.get('RigidRebuild')
    ]
    # A rebuilt skinned body (SkinnedRebuild) replaces submesh 0's blob and
    # the whole SKN section.
    skinned_rebuilds = [
        submesh['SkinnedRebuild'] for submesh in model.get('Submeshes', [])
        if submesh.get('SkinnedRebuild')
    ]
    custom_texture_added = any(
        (owner.get('TextureAssignment') or {}).get('AdditionalTextureFileName')
        for entry in custom_submeshes
        for owner in [entry] + list(entry.get('AdditionalSurfaces') or [])
    ) or any(
        (surface.get('TextureAssignment') or {}).get('AdditionalTextureFileName')
        for rebuild in rigid_rebuilds + skinned_rebuilds
        for surface in rebuild.get('NewSurfaces') or []
    )

    # Repainted skin weights on the donor's own entries (``WeightDataEdited`` at the donor's length: an
    # in-place export, or a Hammerspace export of a body that keeps its layout) need the SKN builder.
    skin = model.get('SkinData') or {}
    skin_weights_edited = any(
        entry.get('WeightDataEdited') is not None and entry.get('VertexCntEdited') is None
        and decode_binary(entry['WeightDataEdited']) != decode_binary(entry.get('WeightData'))
        for entry in (skin.get('SK2s') or []) + (skin.get('SKAccs') or [])
    )
    # The same for an in-place bind-pose edit (``BindPoseDataEdited`` on the donor entries).
    skin_weights_edited = skin_weights_edited or any(
        entry.get('BindPoseDataEdited') is not None and entry.get('VertexCntEdited') is None
        and decode_binary(entry['BindPoseDataEdited']) != decode_binary(entry.get('BindPoseData'))
        for entry in (skin.get('SK1s') or []) + (skin.get('SK2s') or []) + (skin.get('SKAccs') or [])
    )

    args = []
    if model.get('DesiredTextureAssignments') or any(
        submesh.get('FaceSurfaceIdsEdited') is not None
        for submesh in model.get('Submeshes', [])
    ) or changed_positions or has_uv_edits or has_normal_edits or has_color_edits \
            or custom_submeshes or rigid_rebuilds or skinned_rebuilds \
            or any(submesh.get('Excluded') for submesh in model.get('Submeshes', [])):
        args.extend(['--gpl', 'build'])
        if skinned_rebuilds or (any(
            submesh.get('VertexBuffer', {}).get('VertexBufferCompCount') == 6
            for submesh in changed_positions
        ) and model.get('SkinDataEdited')):
            args.extend(('--skn', 'build'))
    if skin_weights_edited and '--skn' not in args:
        args.extend(('--skn', 'build'))

    # New PNGs are appended to TEX whether or not the donor textures are
    # reimported (ReimportTextures only covers those).
    if model.get('ReimportTextures') or model.get('AdditionalTextureDescriptors') \
            or model.get('TextureReplacements') or custom_texture_added:
        args.extend(['--tex', 'build'])

    return args


def _current_model_in_hammerspace(chunk_number, file_index):
    """Return True when the model's DOL entry currently points into the
    hammerspace region of ``3_Output_Dat/main.dol``.

    This detects a stale-state gap: a model that was previously patched via
    Hammerspace (``UseHammerspace=true``) but is now being re-patched with an
    in-place file (``UseHammerspace=false``). After a Hammerspace patch the
    original DAT region has been zeroed and the DOL points at the hammerspace
    block, so a plain in-place patch would write into a zeroed region while
    the game keeps loading from hammerspace -- silently losing the edit.

    The check reads the *current* (output) DOL entry, not the file's flag, so
    it reflects what is actually on disk. Any read failure fails safe to
    ``False`` (the existing, unguarded behavior).
    """
    if chunk_number is None or file_index is None:
        return False

    # Lazy import so start.py stays a pure dispatcher at import time and the
    # Hammerspace module (and its slogger / sys.path setup) is only loaded when
    # a patch is actually being dispatched.
    try:
        from SluggiesTools.Hammerspace import HammerspaceHelper
        from SluggiesTools.Hammerspace import DolRecords
    except Exception as exc:  # pragma: no cover - import side effects
        slogger.warning(
            f"Could not import HammerspaceHelper to check DOL state ({exc}); "
            "assuming the model is not in hammerspace.",
            source="dispatcher",
        )
        return False

    try:
        offset, _length = DolRecords.readOutputDolEntry(chunk_number, file_index)
    except Exception as exc:
        slogger.warning(
            f"Could not read the current DOL entry (chunk={chunk_number}, "
            f"file_index={file_index}): {exc}; assuming the model is not in hammerspace.",
            source="dispatcher",
        )
        return False

    if offset == -1:
        # No output DOL yet, or chunk out of range -> nothing in hammerspace.
        return False
    return offset >= HammerspaceHelper.BASE_SIZE


def _unused_character_dir_index(found):
    """Return the model directory index encoded in a .sluggie's top-level
    export folder name, or None when it cannot be determined.

    Export folders are named ``<dir_index> <character name>`` (see
    ``export.top_level_folder_name``), e.g. ``89 Unused Yoshi A``. The index is
    the leading integer of the first path component under ``2_Output_Assets``.
    """
    if not found:
        return None
    try:
        rel = os.path.relpath(found, SEARCH_DIR)
    except ValueError:
        # found and SEARCH_DIR are on different drives (Windows) — cannot
        # resolve a relative path, so the index is unknown.
        return None
    parts = rel.split(os.sep)
    # The first component is the top-level export folder (e.g. "89 Unused Yoshi A").
    if not parts or parts[0] in (os.curdir, os.pardir):
        return None
    prefix = parts[0].split(' ', 1)[0]
    if prefix.isdigit():
        return int(prefix)
    return None


# Model-level fields only the Hammerspace builder applies. DonorEntry: a slot
# export (Roster/slot_export.py), whose donor block is embedded, not in the DAT.
_HAMMERSPACE_ONLY_MODEL_FIELDS = (
    'DonorEntry',
    'CustomSubmeshes',
    'BoneHierarchyEdited',
    'AdditionalTextureDescriptors',
    'TextureReplacements',
    'DesiredTextureAssignments',
)
# Submesh-level fields only the Hammerspace builder applies.
_HAMMERSPACE_ONLY_SUBMESH_FIELDS = ('FacesDataEdited', 'FaceSurfaceIdsEdited', 'RigidRebuild', 'SkinnedRebuild',
                                    'Excluded')


def _needs_hammerspace(model, sluggie_path):
    """Whether a .sluggie must go through the Hammerspace builder.

    The Blender exporter chooses the mode and writes ``UseHammerspace``. A
    file without the flag still goes to Hammerspace when it carries edits
    the in-place patcher cannot apply (older or hand-edited files), or when
    it is an unused character, whose split blocks only Hammerspace patches.
    """
    if model.get('UseHammerspace', False):
        return True
    if any(model.get(field) for field in _HAMMERSPACE_ONLY_MODEL_FIELDS):
        return True
    if any(
        submesh.get(field) is not None
        for submesh in model.get('Submeshes', [])
        for field in _HAMMERSPACE_ONLY_SUBMESH_FIELDS
    ):
        return True
    return _unused_character_dir_index(sluggie_path) in UNUSED_CHARACTER_DIR_INDICES


def _patch_sluggie(found, model, unpatch, target_id=None, as_low=False, validate_only=False, target_file=None,
                   lod_partner_block=None):
    """Dispatch a .sluggie patch/unpatch through Hammerspace or in-place.

    With ``target_id`` the model goes into that character's slot, which is
    always a Hammerspace write (the in-place patcher can only write over the
    model's own block). ``validate_only`` builds and validates that block and
    writes nothing; without ``target_id`` it runs the plain patch's route as a
    dry run (Hammerspace ``--dry-run``, the in-place patcher's
    ``--validate-only``, the collision ``--dry-run``). ``lod_partner_block``: the
    high-/low-poly partner's incoming block (``_build_partner_block``), checked
    instead of the live one."""
    if target_id is None and model.get('StadiumCollision') is not None and not unpatch and not validate_only:
        # A stadium's collision is written after its model, by its own step. Checked first, with nothing
        # written: a refused collision entry must not leave the new model live over the old collision.
        subprocess.run(python_script_command(COLLISION_SCRIPT, found, '--dry-run'), cwd=HS_DIR, check=True)
    if target_id is not None or _needs_hammerspace(model, found):
        cmd = python_script_command(HS_MAIN_SCRIPT, found, *hammerspace_section_args(model))
        if target_id is not None:
            cmd += ['--target-id', target_id] + (['--as-low'] if as_low else [])
            cmd += ['--target-file', str(target_file)] if target_file is not None else []
            cmd += ['--validate-only'] if validate_only else []
        elif validate_only:
            cmd.append('--dry-run')
        if unpatch:
            cmd.append('--unpatch')
        if lod_partner_block is not None:
            cmd += ['--lod-partner-block', lod_partner_block]
        subprocess.run(cmd, cwd=HS_DIR, check=True)
    else:
        if _current_model_in_hammerspace(model.get('ChunkNumber'), model.get('FileIndex')):
            if validate_only:
                slogger.info("Model is currently in hammerspace: the patch removes that block first (not in a "
                             "build check).", source="dispatcher")
            else:
                slogger.info(
                    f"Model is currently in hammerspace but this file requests an "
                    f"{'in-place unpatch' if unpatch else 'in-place patch'}; removing the "
                    "hammerspace block first to restore the original model bytes "
                    "and DOL route.",
                    source="dispatcher",
                )
                subprocess.run(
                    python_script_command(HS_MAIN_SCRIPT, found, '--unpatch'),
                    cwd=HS_DIR,
                    check=True,
                )

        cmd = python_script_command(PATCH_SCRIPT, found)
        if unpatch:
            cmd.append('--unpatch')
        if validate_only:
            cmd.append('--validate-only')
        subprocess.run(cmd, cwd=TOOLS_DIR, check=True)
    # A stadium's collision is a file of its own, written next to either path.
    # Not for slot targets (characters have no collision file).
    if target_id is None and model.get('StadiumCollision') is not None:
        cmd = python_script_command(COLLISION_SCRIPT, found)
        if unpatch:
            cmd.append('--unpatch')
        if validate_only:
            cmd.append('--dry-run')
        subprocess.run(cmd, cwd=HS_DIR, check=True)


def _patch_png(target, model):
    """Dispatch a single-texture PNG patch through the appropriate path."""
    sluggie_path = target.sluggie_path
    png_path = target.png_path
    texture_index = target.texture_index

    if _needs_hammerspace(model, sluggie_path):
        cmd = python_script_command(
            HS_MAIN_SCRIPT, sluggie_path,
            *hammerspace_section_args(model),
            '--tex', 'build',
            '--texture-file', png_path,
            '--texture-index', str(texture_index),
        )
        subprocess.run(cmd, cwd=HS_DIR, check=True)
        return

    if _current_model_in_hammerspace(model.get('ChunkNumber'), model.get('FileIndex')):
        slogger.info(
            "Model is currently in hammerspace but this PNG targets an "
            "in-place model; removing the hammerspace block first to "
            "restore the original model bytes and DOL route.",
            source="dispatcher",
        )
        subprocess.run(
            python_script_command(HS_MAIN_SCRIPT, sluggie_path, '--unpatch'),
            cwd=HS_DIR,
            check=True,
        )

    cmd = python_script_command(
        PATCH_SCRIPT, sluggie_path,
        '--texture-file', png_path,
        '--texture-index', str(texture_index),
    )
    subprocess.run(cmd, cwd=TOOLS_DIR, check=True)


def run_clear_target(target_id, target_file=None):
    """Return a stock slot's high- and low-poly models to vanilla (``--unpatch --target-id`` without files); with
    ``target_file`` (2-5): only that bat or glove file, (6-14) only that animation bank, back to its baseline."""
    cmd = python_script_command(HS_MAIN_SCRIPT, '--clear-target', target_id)
    cmd += ['--target-file', str(target_file)] if target_file is not None else []
    return subprocess.run(cmd, cwd=HS_DIR).returncode == 0


def _is_low_file(name):
    """Whether a file to patch is a Low model: its ``FileIndex`` (1), else ``_L_`` in its name."""
    if name.lower().endswith('.sluggie'):
        try:
            path = name if os.path.isfile(name) else _model_files.find_unique(SEARCH_DIR, os.path.basename(name))
            index = _read_sluggie(path)['SluggiesModel'].get('FileIndex') if path else None
            if isinstance(index, int):
                return index == 1
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return '_L_' in os.path.basename(name)


def _resolve_sluggie_quietly(filename):
    """``(path, model)`` of a .sluggie file named on the command line, or None (a PNG, a missing, ambiguous or
    unreadable file: the patch loop reports those)."""
    if filename.lower().endswith('.png'):
        return None
    name = filename if filename.lower().endswith('.sluggie') else f'{filename}.sluggie'
    try:
        path = name if os.path.isabs(name) and os.path.isfile(name) else _model_files.find_unique(SEARCH_DIR, name)
        return (path, _read_sluggie(path)['SluggiesModel']) if path else None
    except (_model_files.AmbiguousNameError, OSError, ValueError, KeyError, TypeError):
        return None


def _lod_pairs(filenames, target_id=None):
    """``{first filename: (first path, second path, second model)}`` for each high-/low-poly pair this run patches
    both models of, ``first`` and ``second`` in run order.

    The first model's partner check must see the second model as it will be, not as it is: a pair that drops an
    added bone from both models (or moves geometry off it) is refused otherwise, whichever goes first. Both models
    must be Hammerspace writes (the in-place patcher has no partner check). Pairs: with ``target_id``, the slot's
    high-poly and ``L_`` file; without, two models of one chunk at file indices ``n`` and ``n + 1``, the second
    an ``L_`` model."""
    resolved = []
    for filename in filenames:
        found = _resolve_sluggie_quietly(filename)
        if found is not None and (target_id is not None or _needs_hammerspace(found[1], found[0])):
            resolved.append((filename, *found))
    pairs = {}
    if target_id is not None:
        highs = [entry for entry in resolved if not _is_low_file(entry[1])]
        lows = [entry for entry in resolved if _is_low_file(entry[1])]
        if len(highs) == 1 and len(lows) == 1:
            (first, first_path, _), (_, second_path, second_model) = highs[0], lows[0]
            pairs[first] = (first_path, second_path, second_model)
        return pairs
    for i, (name_a, path_a, model_a) in enumerate(resolved):
        for name_b, path_b, model_b in resolved[i + 1:]:
            if model_a.get('ChunkNumber') != model_b.get('ChunkNumber'):
                continue
            index_a, index_b = model_a.get('FileIndex'), model_b.get('FileIndex')
            if not (isinstance(index_a, int) and isinstance(index_b, int)) or abs(index_a - index_b) != 1:
                continue
            low_path = path_a if index_a > index_b else path_b
            if '_L_' not in os.path.basename(low_path) or name_a in pairs:
                continue
            pairs[name_a] = (path_a, path_b, model_b)
    return pairs


def _build_partner_block(path, model, out_path, target_id=None):
    """Build *path*'s block into *out_path* (nothing patched); False when it does not build (its own patch reports
    why)."""
    cmd = python_script_command(HS_MAIN_SCRIPT, path, *hammerspace_section_args(model), '--build-to', out_path)
    if target_id is not None:
        # a slot run promotes in-place edits (HammerspaceMain), so the partner is built the same way
        cmd += ['--target-id', target_id]
    return subprocess.run(cmd, cwd=HS_DIR).returncode == 0


def run_patching(filenames, unpatch=False, target_id=None, as_low=False, validate_only=False, target_file=None):
    """Patch or unpatch each file. Returns True when every file went through (a .png as much as a .sluggie:
    the batch menu, the GUI and every chain go by the exit code).

    With ``target_id`` (a slot) the files go in high-poly first, and the first
    failure skips the rest: an ``L_`` model must not follow a high-poly model
    that did not make it into the slot. When the run patches both models of a
    high-/low-poly pair, the first one is checked against the second one's
    incoming block (``_lod_pairs``)."""
    if target_id is not None:
        # A slot takes an L_ model only under its own high-poly model, so the
        # high-poly file goes first (by the model it patches: a variant's name
        # need not say L_).
        filenames = sorted(filenames, key=_is_low_file)
    pairs = {} if unpatch or validate_only or as_low or target_file is not None else _lod_pairs(filenames, target_id)
    if not pairs:
        return _run_patching(filenames, unpatch, target_id, as_low, validate_only, target_file)
    import tempfile
    with tempfile.TemporaryDirectory(prefix='sluggies_lod_') as block_dir:
        return _run_patching(filenames, unpatch, target_id, as_low, validate_only, target_file, pairs, block_dir)


def _run_patching(filenames, unpatch, target_id, as_low, validate_only, target_file, pairs=None, block_dir=None):
    failed = False
    pairs = pairs or {}
    checked_against = {}  # second path -> first filename, patched against the second's incoming block
    for filename in filenames:
        if failed and target_id is not None:
            slogger.error(f"'{filename}' skipped: an earlier file of this slot patch failed.", source="dispatcher")
            continue
        is_png = filename.lower().endswith('.png')

        if is_png and target_id is not None:
            slogger.error(
                f"'{filename}': --target-id takes .sluggie files only; patch the model's "
                ".sluggie into the slot instead.",
                source="dispatcher",
            )
            failed = True
            continue

        if is_png and unpatch:
            slogger.error(
                f"'{filename}': --unpatch does not accept .png files: pass "
                "the model's .sluggie (e.g. <model>.gpl.sluggie) to restore "
                "the original texture bytes.",
                source="dispatcher",
            )
            failed = True
            continue

        if is_png:
            try:
                target = _tex.resolve_png_to_texture(filename, SEARCH_DIR)
            except ValueError as exc:
                slogger.error(str(exc), source="dispatcher")
                failed = True
                continue

            slogger.info(
                f"Resolved PNG '{filename}' → "
                f"{os.path.basename(target.sluggie_path)} texture {target.texture_index}",
                source="dispatcher",
            )

            try:
                sluggies_data = _read_sluggie(target.sluggie_path)
            except (OSError, ValueError) as e:
                slogger.error(f"Could not read '{target.sluggie_path}': {e}", source="dispatcher")
                failed = True
                continue

            model = sluggies_data.get('SluggiesModel', {})
            try:
                _patch_png(target, model)
            except subprocess.CalledProcessError as e:
                slogger.error(
                    f"PNG patch failed for '{filename}' (exit code {e.returncode})",
                    source="dispatcher",
                )
                failed = True
                continue
        else:
            sluggie_name = filename if filename.lower().endswith('.sluggie') else f'{filename}.sluggie'
            if os.path.isabs(sluggie_name) and os.path.isfile(sluggie_name):
                found = sluggie_name
            else:
                try:
                    found = _model_files.find_unique(SEARCH_DIR, sluggie_name)
                except _model_files.AmbiguousNameError as exc:
                    slogger.error(str(exc), source="dispatcher")
                    failed = True
                    continue

            if found is None:
                slogger.info(f"No file named '{filename}' found in {SEARCH_DIR}", source="dispatcher")
                failed = True
                continue

            slogger.info(f"Found: {found}", source="dispatcher")

            try:
                sluggies_data = _read_sluggie(found)
            except (OSError, ValueError) as e:
                slogger.error(f"Could not read '{found}': {e}", source="dispatcher")
                failed = True
                continue

            model = sluggies_data.get('SluggiesModel', {})
            partner_block = None
            if filename in pairs:
                _, partner_path, partner_model = pairs[filename]
                partner_block = os.path.join(block_dir, f'{filenames.index(filename)}.bin')
                if _build_partner_block(partner_path, partner_model, partner_block, target_id):
                    slogger.info(
                        f"'{os.path.basename(found)}' is checked against the incoming "
                        f"'{os.path.basename(partner_path)}' (both models of the pair are patched in this run)",
                        source="dispatcher",
                    )
                else:
                    partner_block = None
            try:
                _patch_sluggie(found, model, unpatch, target_id, as_low, validate_only, target_file, partner_block)
            except subprocess.CalledProcessError as e:
                slogger.error(
                    f"Patch failed for '{filename}' (exit code {e.returncode})",
                    source="dispatcher",
                )
                if found in checked_against:
                    slogger.error(
                        f"'{checked_against[found]}' was already patched, checked against this model as it would "
                        "have been: the High and Low models may not match now (a Low model drawing on a bone its "
                        f"High model lacks crashes on load). Fix and patch '{filename}' again, or re-patch "
                        f"'{checked_against[found]}'.",
                        source="dispatcher",
                    )
                failed = True
                continue
            if partner_block is not None:
                checked_against[pairs[filename][1]] = filename
    return not failed
