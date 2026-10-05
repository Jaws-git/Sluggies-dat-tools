import subprocess
import sys
import os
import argparse
import json
import importlib.util
import runpy

# this file is for dispatching only; patching and export logic live in SluggiesTools

# ---------------------------------------------------------------------------
# Initialize universal logging BEFORE anything else so every command,
# including invalid invocations, is captured.
# ---------------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    ROOT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# Portable Windows builds carry Wiimm's tools beside the application. Put that
# directory first so existing subprocess calls find wimgt without user setup.
_BUNDLED_WIIMMS_BIN = os.path.join(ROOT_DIR, 'tools', 'wiimms-szs-tools', 'bin')
if os.path.isdir(_BUNDLED_WIIMMS_BIN):
    os.environ['PATH'] = _BUNDLED_WIIMMS_BIN + os.pathsep + os.environ.get('PATH', '')

# Ensure SluggiesTools package is importable when start.py is run directly.
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Many modules inside SluggiesTools use bare ``import slogger`` (and similar
# sibling imports).  Add the package directory so those resolve correctly.
_SLUGGIES_PKG_DIR = os.path.join(ROOT_DIR, 'SluggiesTools')
if _SLUGGIES_PKG_DIR not in sys.path:
    sys.path.insert(1, _SLUGGIES_PKG_DIR)

import SluggiesTools.slogger as slogger  # noqa: E402 – must come after sys.path fix
import SluggiesTools.texture_helper as _tex  # noqa: E402
from SluggiesTools.binfmt import decode_field as _decode_field  # noqa: E402

slogger.configure()

# Read batch metadata if StartTools.bat set environment variables.
_BATCH_SOURCE = os.environ.get("SLUGGIES_SOURCE", "")
_BATCH_MENU_SELECTION = os.environ.get("SLUGGIES_MENU_SELECTION", "")
_BATCH_MODEL_FILES = os.environ.get("SLUGGIES_MODEL_FILES", "")
_BATCH_ICON_SHARED_MODE = os.environ.get("SLUGGIES_ICON_SHARED_MODE", "")
SEARCH_DIR = os.path.join(ROOT_DIR, '2_Output_Models')
PATCH_SCRIPT = os.path.join(ROOT_DIR, 'SluggiesTools', 'InplacePatcher', 'patch_inplace.py')
EXPORT_SCRIPT = os.path.join(ROOT_DIR, 'SluggiesTools', 'export.py')
TOOLS_DIR = os.path.join(ROOT_DIR, 'SluggiesTools')
ICONS_DIR = os.path.join(TOOLS_DIR, 'Icons')
ICON_EXPORT_SCRIPT = os.path.join(ICONS_DIR, 'export_icons.py')
ICON_PATCH_SCRIPT = os.path.join(ICONS_DIR, 'patch_icons_inplace.py')
HS_DIR = os.path.join(TOOLS_DIR, 'Hammerspace')
HS_HELPER_SCRIPT = os.path.join(HS_DIR, 'HammerspaceHelper.py')
HS_MAIN_SCRIPT = os.path.join(HS_DIR, 'HammerspaceMain.py')
UNTANGLE_POLICY_SCRIPT = os.path.join(HS_DIR, 'UntanglePolicy.py')
ROSTER_SCRIPT = os.path.join(TOOLS_DIR, 'Roster', 'runner.py')
ROSTER_STATE_SCRIPT = os.path.join(TOOLS_DIR, 'Roster', 'state_cli.py')
ROSTER_SLOT_SCRIPT = os.path.join(TOOLS_DIR, 'Roster', 'slot_cli.py')
SLOT_PLAN_FILE = os.path.join(ROOT_DIR, '3_Output_Dat', '_gui', 'slot', 'plan.json')
GAME_OPTIONS_SCRIPT = os.path.join(TOOLS_DIR, 'GameOptions', 'runner.py')

# Model directory indices that hold unused characters (see folderNameMap in
# export.py). These characters share a playable character's model block and
# have no data block of their own, so they can only be written back through
# hammerspace. UntanglePolicy is the one home of this list; it does not import
# export.py, which has interactive side effects at import time.
if HS_DIR not in sys.path:
    sys.path.insert(2, HS_DIR)
import UntanglePolicy  # noqa: E402

UNUSED_CHARACTER_DIR_INDICES = frozenset(UntanglePolicy.UNUSED_CHARACTER_DIRS)


def python_script_command(script, *args):
    """Return a child-script command that works in source and frozen builds."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--_run-script', script, *args]
    return [sys.executable, script, *args]


def run_bundled_script_mode():
    """Run an external project script through the frozen Python runtime."""
    if len(sys.argv) < 2 or sys.argv[1] != '--_run-script':
        return False
    if len(sys.argv) < 3:
        raise SystemExit('--_run-script requires a script path')

    script = os.path.abspath(sys.argv[2])
    if not os.path.isfile(script):
        raise SystemExit(f'Bundled script not found: {script}')
    if os.path.commonpath((ROOT_DIR, script)) != ROOT_DIR:
        raise SystemExit(f'Refusing to run a script outside the application folder: {script}')

    script_dir = os.path.dirname(script)
    tools_dir = os.path.join(ROOT_DIR, 'SluggiesTools')
    for path in (ROOT_DIR, tools_dir, script_dir):
        if path not in sys.path:
            sys.path.insert(0, path)

    sys.argv = [script, *sys.argv[3:]]
    runpy.run_path(script, run_name='__main__')
    return True


def run_gui():
    """Open the Dear PyGui front end; each button re-invokes this entry point."""
    from SluggiesTools.gui import run_gui as _run_gui
    prefix = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, os.path.abspath(__file__)]
    _run_gui(prefix, ROOT_DIR)


def run_hammerspace_helper():
    subprocess.run(python_script_command(HS_HELPER_SCRIPT), cwd=HS_DIR, check=True)


def run_resplit_unused():
    """Give re-tangled unused-character routes their own block copies again."""
    subprocess.run(python_script_command(UNTANGLE_POLICY_SCRIPT), cwd=HS_DIR, check=True)


def run_roster(config=None, remove=False, dry_run=False, state=None):
    """Roster expansion: inject a roster configuration or a derived state (or only reset the roster to vanilla)."""
    cmd = python_script_command(ROSTER_SCRIPT)
    if config:
        cmd += ['--config', os.path.abspath(config)]       # the injector runs in SluggiesTools/
    if state:
        cmd += ['--state', os.path.abspath(state)]
    if remove:
        cmd.append('--remove')
    if dry_run:
        cmd.append('--dry-run')
    subprocess.run(cmd, cwd=TOOLS_DIR, check=True)


def run_roster_state(derive=False):
    """Read the draft grid from 3_Output_Dat into 3_Output_Dat/_gui/roster_state.json (GUI character grid), or
    (``derive``) write the derived config that rebuilds it into 3_Output_Dat/_gui/derived."""
    cmd = python_script_command(ROSTER_STATE_SCRIPT)
    if derive:
        cmd.append('--derive')
    subprocess.run(cmd, cwd=TOOLS_DIR, check=True)


def self_command(*args):
    """A command line that runs this dispatcher again (source and frozen builds)."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, *args]
    return [sys.executable, os.path.abspath(__file__), *args]


def run_slot_chain(target_id, sluggie=None, dry_run=False):
    """Patch a .sluggie (and its HP/L_ partner) into a slot, or clear the slot (``sluggie`` None): plan the chain
    (Roster/slot_cli.py: read, derive, apply the change; a refused change writes nothing), then run its commands
    in order, stopping at the first failure. Returns True on success."""
    cmd = python_script_command(ROSTER_SLOT_SCRIPT)
    cmd += ['--patch', target_id, os.path.abspath(sluggie)] if sluggie else ['--clear', target_id]
    if subprocess.run(cmd, cwd=TOOLS_DIR).returncode != 0:
        return False
    if dry_run:
        slogger.info('Dry run: the chain above was planned, nothing was written.', source="dispatcher")
        return True
    with open(SLOT_PLAN_FILE, 'r', encoding='utf-8') as f:
        commands = json.load(f)['commands']
    for n, args in enumerate(commands, 1):
        slogger.info(f'Slot chain step {n}/{len(commands)}: start.py {" ".join(args)}', source="dispatcher")
        if subprocess.run(self_command(*args), cwd=ROOT_DIR).returncode != 0:
            slogger.error(f'Slot chain step {n} failed; the remaining {len(commands) - n} step(s) were skipped.',
                          source="dispatcher")
            return False
    return True


def run_game_options(on=(), off=(), dry_run=False):
    """Game options (CPU vs CPU, ...): turn options on or off in 3_Output_Dat/main.dol; no options = status."""
    cmd = python_script_command(GAME_OPTIONS_SCRIPT)
    if on:
        cmd += ['--on', *on]
    if off:
        cmd += ['--off', *off]
    if dry_run:
        cmd.append('--dry-run')
    subprocess.run(cmd, cwd=TOOLS_DIR, check=True)


def run_export(debug=False, notex=False, untangle=False, glb=False):
    if importlib.util.find_spec('numpy') is None:
        slogger.error("Missing required package: numpy", source="dispatcher")
        slogger.error("Run: pip install numpy", source="dispatcher")
        sys.exit(1)

    extra_args = []
    if notex:
        extra_args.append('--notex')
    if debug:
        extra_args.append('--debug')
    if untangle:
        extra_args.append('--untangle')
    if glb:
        extra_args.append('--glb')

    subprocess.run(
        python_script_command(EXPORT_SCRIPT, *extra_args),
        cwd=TOOLS_DIR,
        check=True
    )
    slogger.info('Export complete. Find your files in the folder "2_Output_Models"', source="dispatcher")


def run_export_icons(use_output=False):
    if importlib.util.find_spec('PIL') is None:
        slogger.error("Missing required package: Pillow", source="dispatcher")
        slogger.error("Run: pip install Pillow", source="dispatcher")
        sys.exit(1)
    if importlib.util.find_spec('numpy') is None:
        slogger.error("Missing required package: numpy", source="dispatcher")
        slogger.error("Run: pip install numpy", source="dispatcher")
        sys.exit(1)

    cmd = python_script_command(ICON_EXPORT_SCRIPT)
    if use_output:
        dol_path = os.path.join(ROOT_DIR, '3_Output_Dat', 'main.dol')
        dat_path = os.path.join(ROOT_DIR, '3_Output_Dat', 'dt_na.dat')
        if not os.path.exists(dol_path) or not os.path.exists(dat_path):
            slogger.error('Missing 3_Output_Dat/main.dol or 3_Output_Dat/dt_na.dat', source="dispatcher")
            slogger.error('Run a patch step first (e.g. --roster or --patch), or drop --use-output', source="dispatcher")
            sys.exit(1)
        cmd += ['--dol-path', dol_path, '--dat-path', dat_path]

    subprocess.run(
        cmd,
        cwd=TOOLS_DIR,
        check=True
    )
    slogger.info('Icon export complete. Find your files in the folder "2_Output_Models/_ICONS"', source="dispatcher")


def run_patch_icons(source=None, dry_run=False):
    cmd = python_script_command(ICON_PATCH_SCRIPT)
    if source:
        cmd.append(source)
    if dry_run:
        cmd.append('--dry-run')

    subprocess.run(cmd, cwd=TOOLS_DIR, check=True)

    if dry_run:
        slogger.info('Icon reimport dry-run complete. Check metadata [META]/reimport_report.json for details.', source="dispatcher")
    else:
        slogger.info('Icon reimport complete. Patched DAT is in the folder "3_Output_Dat"', source="dispatcher")


def hammerspace_section_args(model):
    """Select rebuild modes required by exported hammerspace edit markers."""
    use_base64 = model.get('UseBase64', True)

    def decode_binary(value):
        return _decode_field(value, use_base64)

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
    # builder; one bound to an appended PNG also needs the TEX builder.
    custom_submeshes = model.get('CustomSubmeshes') or []
    custom_texture_added = any(
        (entry.get('TextureAssignment') or {}).get('AdditionalTextureFileName')
        for entry in custom_submeshes
    )

    args = []
    if model.get('DesiredTextureAssignments') or any(
        submesh.get('FaceSurfaceIdsEdited') is not None
        for submesh in model.get('Submeshes', [])
    ) or changed_positions or has_uv_edits or has_normal_edits or has_color_edits \
            or custom_submeshes:
        args.extend(['--gpl', 'build'])
        if any(
            submesh.get('VertexBuffer', {}).get('VertexBufferCompCount') == 6
            for submesh in changed_positions
        ) and model.get('SkinDataEdited'):
            args.extend(('--skn', 'build'))

    if model.get('ReimportTextures') or custom_texture_added:
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
    except Exception as exc:  # pragma: no cover - import side effects
        slogger.warning(
            f"Could not import HammerspaceHelper to check DOL state ({exc}); "
            "assuming the model is not in hammerspace.",
            source="dispatcher",
        )
        return False

    try:
        offset, _length = HammerspaceHelper.readOutputDolEntry(chunk_number, file_index)
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
    the leading integer of the first path component under ``2_Output_Models``.
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


# Model-level fields only the Hammerspace builder applies.
_HAMMERSPACE_ONLY_MODEL_FIELDS = (
    'CustomSubmeshes',
    'BoneHierarchyEdited',
    'AdditionalTextureDescriptors',
    'DesiredTextureAssignments',
)
# Submesh-level fields only the Hammerspace builder applies.
_HAMMERSPACE_ONLY_SUBMESH_FIELDS = ('FacesDataEdited', 'FaceSurfaceIdsEdited')


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


def _patch_sluggie(found, model, unpatch, target_id=None, as_low=False, validate_only=False):
    """Dispatch a .sluggie patch/unpatch through Hammerspace or in-place.

    With ``target_id`` the model goes into that character's slot, which is
    always a Hammerspace write (the in-place patcher can only write over the
    model's own block). ``validate_only`` builds and validates that block and
    writes nothing."""
    if target_id is not None or _needs_hammerspace(model, found):
        cmd = python_script_command(HS_MAIN_SCRIPT, found, *hammerspace_section_args(model))
        if target_id is not None:
            cmd += ['--target-id', target_id] + (['--as-low'] if as_low else [])
            cmd += ['--validate-only'] if validate_only else []
        if unpatch:
            cmd.append('--unpatch')
        subprocess.run(cmd, cwd=HS_DIR, check=True)
    else:
        if _current_model_in_hammerspace(model.get('ChunkNumber'), model.get('FileIndex')):
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
        subprocess.run(cmd, cwd=TOOLS_DIR, check=True)


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


def run_clear_target(target_id):
    """Return a stock slot's high- and low-poly models to vanilla (``--unpatch --target-id`` without files)."""
    return subprocess.run(python_script_command(HS_MAIN_SCRIPT, '--clear-target', target_id),
                          cwd=HS_DIR).returncode == 0


def run_patching(filenames, unpatch=False, target_id=None, as_low=False, validate_only=False):
    """Patch or unpatch each file. Returns True when every file went through.

    With ``target_id`` (a slot) the files go in high-poly first, and the first
    failure skips the rest: an ``L_`` model must not follow a high-poly model
    that did not make it into the slot."""
    failed = False
    if target_id is not None:
        # A slot takes an L_ model only under its own high-poly model, so the
        # high-poly file goes first.
        filenames = sorted(filenames, key=lambda name: '_L_' in os.path.basename(name))
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
            continue

        if is_png:
            try:
                target = _tex.resolve_png_to_texture(filename, SEARCH_DIR)
            except ValueError as exc:
                slogger.error(str(exc), source="dispatcher")
                continue

            slogger.info(
                f"Resolved PNG '{filename}' → "
                f"{os.path.basename(target.sluggie_path)} texture {target.texture_index}",
                source="dispatcher",
            )

            try:
                with open(target.sluggie_path, 'r') as f:
                    sluggies_data = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                slogger.error(f"Could not read '{target.sluggie_path}': {e}", source="dispatcher")
                continue

            model = sluggies_data.get('SluggiesModel', {})
            try:
                _patch_png(target, model)
            except subprocess.CalledProcessError as e:
                slogger.error(
                    f"PNG patch failed for '{filename}' (exit code {e.returncode})",
                    source="dispatcher",
                )
                continue
        else:
            sluggie_name = filename if filename.lower().endswith('.sluggie') else f'{filename}.sluggie'
            if os.path.isabs(sluggie_name) and os.path.isfile(sluggie_name):
                matches = [sluggie_name]
            else:
                matches = [
                    os.path.join(root, f)
                    for root, _, files in os.walk(SEARCH_DIR)
                    for f in files
                    if f == sluggie_name
                ]

            if not matches:
                slogger.info(f"No file named '{filename}' found in {SEARCH_DIR}", source="dispatcher")
                failed = True
                continue

            found = matches[0]
            slogger.info(f"Found: {found}", source="dispatcher")

            try:
                with open(found, 'r') as f:
                    sluggies_data = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                slogger.error(f"Could not read '{found}': {e}", source="dispatcher")
                failed = True
                continue

            model = sluggies_data.get('SluggiesModel', {})
            try:
                _patch_sluggie(found, model, unpatch, target_id, as_low, validate_only)
            except subprocess.CalledProcessError as e:
                slogger.error(
                    f"Patch failed for '{filename}' (exit code {e.returncode})",
                    source="dispatcher",
                )
                failed = True
                continue
    return not failed


def parse_args():
    parser = argparse.ArgumentParser(
        description='Central dispatcher for Sluggies patching and export tasks.',
        epilog=(
            'Examples:\n'
            '  python start.py            (opens the GUI; same as --gui)\n'
            '  python start.py --export\n'
            '  python start.py --export --debug --notex --untangle\n'
            '  python start.py --export --untangle\n'
            '  python start.py --export --glb\n'
            '  python start.py --roster --config 1_Input/_RosterConfigurations/02_Stock_and_Unused.json\n'
            '  python start.py --roster --remove\n'
            '  python start.py --game-options\n'
            '  python start.py --game-options --on cpu_vs_cpu cpu_management\n'
            '  python start.py --game-options --off cpu_management\n'
            '  python start.py --export-icons\n'
            '  python start.py --export-icons --use-output\n'
            '  python start.py --patch-icons\n'
            '  python start.py --patch-icons --dry-run\n'
            '  python start.py --patch model.sluggie\n'
            '  python start.py --patch model1.sluggie model2.sluggie\n'
            '  python start.py --patch texture.png\n'
            '  python start.py --patch model.sluggie texture.png\n'
            '  python start.py --unpatch model.sluggie\n'
            '  python start.py --patch model.gpl.sluggie L_model.gpl.sluggie --target-id 0x4A\n'
            '  python start.py --unpatch model.gpl.sluggie --target-id 0x4A\n'
            '  python start.py --unpatch --target-id 0x4A\n'
            '  python start.py --patch-slot 0xE1 path/to/model.gpl.sluggie\n'
            '  python start.py --clear-slot 0xE1\n'
            '  python start.py --hammerspace\n'
            '  python start.py --resplit-unused\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--gui', action='store_true', help='open the graphical front end (also the default when no arguments are given)')
    mode.add_argument('--patch', nargs='+', metavar='FILENAME', help='patch one or more .sluggie and/or .png files')
    mode.add_argument('--unpatch', nargs='*', metavar='FILENAME', help="restore original data for one or more .sluggies files (with --target-id and no files: that stock slot's high- and low-poly models)")
    mode.add_argument('--patch-slot', nargs=2, metavar=('0xNN', 'FILE'), help='put a .sluggie (and its HP/L_ partner) into a slot: read -> rebuild (own model directory, stats, voice, name, portraits) -> patch (GUI character grid)')
    mode.add_argument('--clear-slot', metavar='0xNN', help='return a slot to its baseline: a stock slot gets its vanilla models and portraits back, a new ID a fresh copy of its template and the open-slot look')
    mode.add_argument('-hs', '--hammerspace', action='store_true', help='change available memory space in outputdt_na.dat')
    mode.add_argument('--resplit-unused', action='store_true', help='repair: give unused-character routes (dirs 89-94) that point at a playable character\'s block their own copy again')
    mode.add_argument('--roster', '--roster-dev', dest='roster', action='store_true', help='inject a roster configuration (--config, e.g. from 1_Input/_RosterConfigurations) into 3_Output_Dat, replacing the previous injection')
    mode.add_argument('--roster-state', action='store_true', help='read the draft grid from 3_Output_Dat into 3_Output_Dat/_gui/roster_state.json (used by the GUI)')
    mode.add_argument('--roster-derive', action='store_true', help='write the roster config that rebuilds 3_Output_Dat as it is into 3_Output_Dat/_gui/derived (read -> rebuild; then --roster --state)')
    mode.add_argument('--game-options', action='store_true', help='show or change game options (CPU vs CPU, ...) in 3_Output_Dat/main.dol; use with --on/--off')
    mode.add_argument('--export', action='store_true', help='export all models from 1_Input to 2_Output_Models')
    mode.add_argument('--export-icons', action='store_true', help='export character-select icon atlases and metadata to 2_Output_Models/_ICONS')
    mode.add_argument(
        '--patch-icons',
        nargs='?',
        const='',
        metavar='SOURCE',
        help='reimport icon sheets and patch dt_na.dat using metadata from _ICONS (optional SOURCE path)'
    )

    parser.add_argument('--debug', action='store_true', help='export only: write binary blobs as raw byte arrays instead of base64')
    parser.add_argument('--notex', action='store_true', help='export only: skip texture extraction')
    parser.add_argument('--untangle', action='store_true', help='export only: pass untangling flag through to export process')
    parser.add_argument('--glb', action='store_true', help='export only: also write .glb model files to disk (always writes .sluggie files)')
    parser.add_argument('--use-output', action='store_true', help='export-icons only: read DOL/DAT from 3_Output_Dat instead of 1_Input')
    parser.add_argument('--dry-run', action='store_true', help='patch-icons/roster: validate without writing bytes')
    parser.add_argument('--config', metavar='PATH', help='roster only: the roster configuration JSON')
    parser.add_argument('--state', metavar='PATH', help='roster only: a derived config (--roster-derive) instead of --config')
    parser.add_argument('--target-id', metavar='0xNN', help="patch/unpatch only: write the .sluggie models into this character ID's slot instead of their own route (always Hammerspace)")
    parser.add_argument('--as-low', action='store_true', help='with --target-id: a high-poly model without L_ partner is the low-poly model too')
    parser.add_argument('--validate-only', action='store_true', help='with --patch --target-id: build and validate the slot blocks, write nothing')
    parser.add_argument('--remove', action='store_true', help='roster only: reset the roster to vanilla (against 1_Input) and stop')
    parser.add_argument('--on', nargs='+', default=[], metavar='OPTION', help='game-options only: turn these options on (cpu_vs_cpu, cpu_management)')
    parser.add_argument('--off', nargs='+', default=[], metavar='OPTION', help='game-options only: turn these options off')

    args = parser.parse_args()

    if args.debug and not args.export:
        parser.error('--debug can only be used with --export.')
    if args.notex and not args.export:
        parser.error('--notex can only be used with --export.')
    if args.untangle and not args.export:
        parser.error('--untangle can only be used with --export.')
    if args.glb and not args.export:
        parser.error('--glb can only be used with --export.')
    if args.use_output and not args.export_icons:
        parser.error('--use-output can only be used with --export-icons.')
    if args.dry_run and not (args.patch_icons is not None or args.roster or args.game_options
                             or args.patch_slot or args.clear_slot):
        parser.error('--dry-run can only be used with --patch-icons, --roster, --game-options, --patch-slot or '
                     '--clear-slot.')
    if (args.on or args.off) and not args.game_options:
        parser.error('--on and --off can only be used with --game-options.')
    if (args.config or args.remove or args.state) and not args.roster:
        parser.error('--config, --state and --remove can only be used with --roster.')
    if args.target_id and not (args.patch or args.unpatch is not None):
        parser.error('--target-id can only be used with --patch or --unpatch.')
    if args.unpatch == [] and not args.target_id:
        parser.error("--unpatch needs files (or --target-id 0xNN to restore a stock slot's models).")
    if args.unpatch == [] and args.as_low:
        parser.error("--as-low does not apply to restoring a slot's models.")
    if args.validate_only and not (args.patch and args.target_id):
        parser.error('--validate-only needs --patch and --target-id.')
    if args.as_low and not args.target_id:
        parser.error('--as-low needs --target-id.')
    if args.config and args.state:
        parser.error('--config and --state cannot be used together.')
    if args.roster and not (args.config or args.remove or args.state):
        parser.error('--roster needs --config PATH (a roster configuration), --state PATH or --remove.')
    if not any([args.gui, args.patch, args.unpatch is not None, args.patch_slot, args.clear_slot, args.hammerspace, args.resplit_unused, args.export, args.export_icons, args.patch_icons is not None, args.roster, args.roster_state, args.roster_derive, args.game_options]):
        if len(sys.argv) == 1:
            args.gui = True
        else:
            parser.print_help()
            sys.exit(0)

    return args


def _get_invocation_source() -> str:
    """Return 'StartTools.bat' when batch metadata is present, else 'CLI'."""
    if _BATCH_SOURCE:
        return _BATCH_SOURCE
    return "CLI"


def _log_batch_metadata() -> None:
    """Log batch menu selection and interactive inputs if coming from StartTools.bat."""
    if not _BATCH_SOURCE:
        return
    if _BATCH_MENU_SELECTION:
        slogger.info(
            f"Batch menu selection: {_BATCH_MENU_SELECTION}",
            source="dispatcher",
        )
    if _BATCH_MODEL_FILES:
        slogger.info(
            f"Batch model file input: {_BATCH_MODEL_FILES!r}",
            source="dispatcher",
        )
    if _BATCH_ICON_SHARED_MODE:
        slogger.info(
            f"Batch icon shared-mode input: {_BATCH_ICON_SHARED_MODE!r}",
            source="dispatcher",
        )


def main() -> int:
    """Dispatch user command. Returns 0 on success, 1 on failure."""
    source = _get_invocation_source()

    # 2.1 – Log normalized command before parsing so even invalid invocations
    # are captured.
    slogger.log_command(sys.argv, source=source)

    # 2.3 – Log batch metadata (menu choice, interactive inputs).
    _log_batch_metadata()

    try:
        args = parse_args()
    except SystemExit as exc:
        # argparse calls sys.exit on --help (code 0) or on parser.error (code 2).
        code = exc.code if isinstance(exc.code, int) else 1
        if code != 0:
            slogger.info(f"Command exited with code {code}", source="dispatcher")
        return code

    try:
        if args.gui:
            run_gui()
        elif args.hammerspace:
            run_hammerspace_helper()
        elif args.resplit_unused:
            run_resplit_unused()
        elif args.roster:
            run_roster(config=args.config, remove=args.remove, dry_run=args.dry_run, state=args.state)
        elif args.roster_state:
            run_roster_state()
        elif args.roster_derive:
            run_roster_state(derive=True)
        elif args.game_options:
            run_game_options(on=args.on, off=args.off, dry_run=args.dry_run)
        elif args.export:
            run_export(debug=args.debug, notex=args.notex, untangle=args.untangle, glb=args.glb)
        elif args.export_icons:
            run_export_icons(use_output=args.use_output)
        elif args.patch_icons is not None:
            source_path = args.patch_icons if args.patch_icons != '' else None
            run_patch_icons(
                source=source_path,
                dry_run=args.dry_run,
            )
        elif args.patch:
            # A failed slot patch fails the command (the apply chain stops on it); plain patches keep going.
            if not run_patching(args.patch, unpatch=False, target_id=args.target_id, as_low=args.as_low,
                                validate_only=args.validate_only) and args.target_id:
                return 1
        elif args.unpatch is not None:
            if not args.unpatch:
                if not run_clear_target(args.target_id):
                    return 1
            elif not run_patching(args.unpatch, unpatch=True, target_id=args.target_id,
                                  as_low=args.as_low) and args.target_id:
                return 1
        elif args.patch_slot:
            if not run_slot_chain(args.patch_slot[0], args.patch_slot[1], dry_run=args.dry_run):
                return 1
        elif args.clear_slot:
            if not run_slot_chain(args.clear_slot, dry_run=args.dry_run):
                return 1

        slogger.info("Command completed", source="dispatcher")
        return 0
    except KeyboardInterrupt:
        slogger.info("Command interrupted by user", source="dispatcher")
        return 130
    except Exception as exc:
        slogger.exception(
            "Unexpected failure during command execution",
            source="dispatcher",
            exc=exc,
        )
        return 1


if __name__ == '__main__':
    if not run_bundled_script_mode():
        rc = main()
        if rc != 0:
            sys.exit(rc)
