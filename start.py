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
HS_DIR = os.path.join(TOOLS_DIR, 'Hammerspace')
HS_MAIN_SCRIPT = os.path.join(HS_DIR, 'HammerspaceMain.py')
UNTANGLE_POLICY_SCRIPT = os.path.join(HS_DIR, 'UntanglePolicy.py')
ROSTER_SCRIPT = os.path.join(TOOLS_DIR, 'Roster', 'runner.py')
ROSTER_STATE_SCRIPT = os.path.join(TOOLS_DIR, 'Roster', 'state_cli.py')
ROSTER_SLOT_SCRIPT = os.path.join(TOOLS_DIR, 'Roster', 'slot_cli.py')
ROSTER_PACK_SCRIPT = os.path.join(TOOLS_DIR, 'Roster', 'pack_cli.py')
ROSTER_SWITCH_SCRIPT = os.path.join(TOOLS_DIR, 'Roster', 'switch_cli.py')
PACK_PLAN_FILE = os.path.join(ROOT_DIR, '3_Output_Dat', '_gui', 'pack', 'plan.json')
SWITCH_PLAN_FILE = os.path.join(ROOT_DIR, '3_Output_Dat', '_gui', 'switch', 'plan.json')
SLOT_PLAN_FILE = os.path.join(ROOT_DIR, '3_Output_Dat', '_gui', 'slot', 'plan.json')
GAME_OPTIONS_SCRIPT = os.path.join(TOOLS_DIR, 'GameOptions', 'runner.py')
STAT_EDITOR_SCRIPT = os.path.join(TOOLS_DIR, 'StatEditor', 'cli.py')

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


def run_resplit_unused():
    """Give re-tangled unused-character routes their own block copies again."""
    subprocess.run(python_script_command(UNTANGLE_POLICY_SCRIPT), cwd=HS_DIR, check=True)


def run_roster(config=None, remove=False, dry_run=False, state=None, keep_stat_edits=False):
    """Roster expansion: inject a roster configuration or a derived state (or only reset the roster to vanilla,
    stat edits included unless ``keep_stat_edits``). A ``config`` given here keeps no slot customisations
    (``--fresh``); ``run_roster_switch`` is the default."""
    cmd = python_script_command(ROSTER_SCRIPT)
    if config:
        cmd += ['--config', os.path.abspath(config)]       # the injector runs in SluggiesTools/
    if state:
        cmd += ['--state', os.path.abspath(state)]
    if remove:
        cmd.append('--remove')
    if keep_stat_edits:
        cmd.append('--keep-stat-edits')
    if dry_run:
        cmd.append('--dry-run')
    subprocess.run(cmd, cwd=TOOLS_DIR, check=True)


def run_roster_switch(config, dry_run=False):
    """Inject a roster configuration and keep the slots' customisations (Roster/switch_cli.py: the game is read
    and merged with the preset, IDs on both grids keep theirs, slots leaving the grid are reset), then run the
    planned chain in order. ``dry_run``: plan, then build the merged roster in memory only. Returns True on
    success."""
    if subprocess.run(python_script_command(ROSTER_SWITCH_SCRIPT, '--config', os.path.abspath(config)),
                      cwd=TOOLS_DIR).returncode != 0:
        return False
    with open(SWITCH_PLAN_FILE, 'r', encoding='utf-8') as f:
        commands = json.load(f)['commands']
    if dry_run:
        checks = [[*c, '--dry-run'] for c in commands if c[:2] == ['--roster', '--state']]
        if not run_chain_commands(checks, 'Roster switch check'):
            return False
        slogger.info('Dry run: the switch was planned and the merged roster built in memory; nothing was written.',
                     source="dispatcher")
        return True
    return run_chain_commands(commands, 'Roster switch')


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


def run_slot_chain(target_id=None, sluggie=None, dry_run=False, edits_file=None, rename=None, voice=None,
                   stats=None, icon=None, fit=None, trim=True, equipment=None, no_gear=False, copy_from=None):
    """Write staged slot edits as one chain: an edits file (``edits_file``, ``--apply-slots``), or a batch of one:
    patch a .sluggie (and its HP/L_ partner) into a slot, name the slot (``rename``: the text, blank resets it),
    give its square another voice (``voice``) or the slot other stats (``stats``: a character ID, ``-`` resets),
    replace one portrait (``icon``: ``(view, image)``, fitted with ``fit`` / ``trim``), make the slot a clone of
    another character (``copy_from``: its ID, the grid's paste), or clear the slot (none given). Plans the chain
    (Roster/slot_cli.py: read and derive once, apply every edit; a refused edit refuses the batch and writes
    nothing), then runs its commands in order, stopping at the first failure. Returns True on success.

    ``dry_run`` plans and runs only the chain's build checks (``--validate-only``: every block built and validated,
    nothing written; edits marked "checked" in the edits file are skipped); the GUI's confirm dialogs show its
    result."""
    cmd = python_script_command(ROSTER_SLOT_SCRIPT)
    if edits_file:
        cmd += ['--apply', os.path.abspath(edits_file)]
    elif sluggie:
        cmd += ['--patch', target_id, os.path.abspath(sluggie)]
        cmd += ['--equipment', str(equipment)] if equipment is not None else []
        cmd += ['--no-gear'] if no_gear else []
    elif rename is not None:
        cmd += ['--rename', target_id, rename]
    elif voice is not None:
        cmd += ['--voice', target_id, voice]
    elif stats is not None:
        cmd += ['--stats', target_id, stats]
    elif copy_from is not None:
        cmd += ['--copy', copy_from, target_id]
    elif icon is not None:
        cmd += ['--icon', target_id, icon[0], os.path.abspath(icon[1])]
        cmd += ['--fit', fit] if fit else []
        cmd += [] if trim else ['--no-trim']
    else:
        cmd += ['--clear', target_id]
        cmd += ['--equipment', str(equipment)] if equipment is not None else []
    if dry_run:
        cmd.append('--dry-run')
    if subprocess.run(cmd, cwd=TOOLS_DIR).returncode != 0:
        return False
    with open(SLOT_PLAN_FILE, 'r', encoding='utf-8') as f:
        commands = json.load(f)['commands']
    if dry_run:
        commands = [args for args in commands if '--validate-only' in args]
    if not run_chain_commands(commands, 'Slot chain'):
        return False
    if dry_run:
        slogger.info('Dry run: the chain above was planned and its build checks ran; nothing was written.',
                     source="dispatcher")
    return True


def run_chain_commands(commands, label):
    """Run planned ``start.py`` commands in order, stopping at the first failure. Returns True on success."""
    for n, args in enumerate(commands, 1):
        slogger.info(f'{label} step {n}/{len(commands)}: start.py {" ".join(args)}', source="dispatcher")
        if subprocess.run(self_command(*args), cwd=ROOT_DIR).returncode != 0:
            slogger.error(f'{label} step {n} failed; the remaining {len(commands) - n} step(s) were skipped.',
                          source="dispatcher")
            return False
    return True


def run_save_roster(path):
    """Write 3_Output_Dat's roster into a roster pack (Roster/pack_cli.py); the game files are only read."""
    return subprocess.run(python_script_command(ROSTER_PACK_SCRIPT, '--save', os.path.abspath(path)),
                          cwd=TOOLS_DIR).returncode == 0


def run_load_roster(path, dry_run=False):
    """Load a roster pack: check it and plan the chain (Roster/pack_cli.py: per-slot diff, every block checked;
    a refused pack writes nothing), then run its commands in order (one roster rebuild at most, stock slots back
    to vanilla where needed, the pack's blocks written as they are, one re-read). ``dry_run``: plan only."""
    if subprocess.run(python_script_command(ROSTER_PACK_SCRIPT, '--load', os.path.abspath(path)),
                      cwd=TOOLS_DIR).returncode != 0:
        return False
    if dry_run:
        slogger.info('Dry run: the pack was checked and the load planned; nothing was written.', source="dispatcher")
        return True
    with open(PACK_PLAN_FILE, 'r', encoding='utf-8') as f:
        commands = json.load(f)['commands']
    if not commands:
        slogger.info('The game holds this roster already: nothing to load.', source="dispatcher")
        return True
    return run_chain_commands(commands, 'Pack load')


def run_write_slot_blocks(target_id, high, low):
    """Write finished model blocks (a roster pack's; '-' keeps that file) into a slot as they are."""
    paths = [p if p == '-' else os.path.abspath(p) for p in (high, low)]
    return subprocess.run(python_script_command(HS_MAIN_SCRIPT, '--write-slot-blocks', target_id, *paths),
                          cwd=HS_DIR).returncode == 0


def run_write_slot_equipment(target_id, file_index, block):
    """Write a finished equipment block (a roster pack's) into a slot's file 2-5 as it is."""
    return subprocess.run(python_script_command(HS_MAIN_SCRIPT, '--write-slot-equipment', target_id,
                                                str(file_index), os.path.abspath(block)),
                          cwd=HS_DIR).returncode == 0


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


def run_stat_bridge_export(path, focus=None):
    """Write the stat editor bridge (stat_bridge.json) for 3_Output_Dat to ``path`` (StatEditor/cli.py)."""
    cmd = python_script_command(STAT_EDITOR_SCRIPT, '--export', os.path.abspath(path))
    if focus:
        cmd += ['--focus', focus]
    return subprocess.run(cmd, cwd=TOOLS_DIR).returncode == 0


def run_apply_stat_edits(paths, dry_run=False):
    """Write the stat editor's edit files (stat_edits.json, in order) into 3_Output_Dat/main.dol (StatEditor/cli.py):
    every file is checked first (made from this main.dol, known IDs and fields, storable values); ``dry_run`` lists
    the changes and writes nothing. An item ``reset:0xNN`` clears that character's stat edits at its place in the
    order."""
    items = [p if p.lower().startswith('reset:') else 'copy:' + os.path.abspath(p[5:])
             if p.lower().startswith('copy:') else os.path.abspath(p) for p in paths]
    cmd = python_script_command(STAT_EDITOR_SCRIPT, '--apply', *items)
    if dry_run:
        cmd.append('--dry-run')
    return subprocess.run(cmd, cwd=TOOLS_DIR).returncode == 0


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
    """Write each character's FrontIcon.png/SideIcon.png into its model folder in 2_Output_Models."""
    for package, name in (('PIL', 'Pillow'), ('numpy', 'numpy')):
        if importlib.util.find_spec(package) is None:
            slogger.error(f"Missing required package: {name}", source="dispatcher")
            slogger.error(f"Run: pip install {name}", source="dispatcher")
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
    slogger.info('Icon export complete: FrontIcon.png/SideIcon.png are in each character model folder', source="dispatcher")


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
    # builder; one bound to an appended PNG also needs the TEX builder. A
    # rebuilt rigid submesh (RigidRebuild) replaces its GPL blob the same way,
    # and a new surface on it may bind an appended PNG too.
    custom_submeshes = model.get('CustomSubmeshes') or []
    rigid_rebuilds = [
        submesh['RigidRebuild'] for submesh in model.get('Submeshes', [])
        if submesh.get('RigidRebuild')
    ]
    custom_texture_added = any(
        (owner.get('TextureAssignment') or {}).get('AdditionalTextureFileName')
        for entry in custom_submeshes
        for owner in [entry] + list(entry.get('AdditionalSurfaces') or [])
    ) or any(
        (surface.get('TextureAssignment') or {}).get('AdditionalTextureFileName')
        for rebuild in rigid_rebuilds
        for surface in rebuild.get('NewSurfaces') or []
    )

    args = []
    if model.get('DesiredTextureAssignments') or any(
        submesh.get('FaceSurfaceIdsEdited') is not None
        for submesh in model.get('Submeshes', [])
    ) or changed_positions or has_uv_edits or has_normal_edits or has_color_edits \
            or custom_submeshes or rigid_rebuilds:
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
_HAMMERSPACE_ONLY_SUBMESH_FIELDS = ('FacesDataEdited', 'FaceSurfaceIdsEdited', 'RigidRebuild')


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


def _patch_sluggie(found, model, unpatch, target_id=None, as_low=False, validate_only=False, target_file=None):
    """Dispatch a .sluggie patch/unpatch through Hammerspace or in-place.

    With ``target_id`` the model goes into that character's slot, which is
    always a Hammerspace write (the in-place patcher can only write over the
    model's own block). ``validate_only`` builds and validates that block and
    writes nothing."""
    if target_id is not None or _needs_hammerspace(model, found):
        cmd = python_script_command(HS_MAIN_SCRIPT, found, *hammerspace_section_args(model))
        if target_id is not None:
            cmd += ['--target-id', target_id] + (['--as-low'] if as_low else [])
            cmd += ['--target-file', str(target_file)] if target_file is not None else []
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


def run_clear_target(target_id, target_file=None):
    """Return a stock slot's high- and low-poly models to vanilla (``--unpatch --target-id`` without files); with
    ``target_file`` (2-5): only that bat or glove file, back to its baseline."""
    cmd = python_script_command(HS_MAIN_SCRIPT, '--clear-target', target_id)
    cmd += ['--target-file', str(target_file)] if target_file is not None else []
    return subprocess.run(cmd, cwd=HS_DIR).returncode == 0


def run_patching(filenames, unpatch=False, target_id=None, as_low=False, validate_only=False, target_file=None):
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
                _patch_sluggie(found, model, unpatch, target_id, as_low, validate_only, target_file)
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
            '  python start.py --roster --config 1_Input/_RosterConfigurations/03_Unuseds_and_8_color_slots.json --dry-run\n'
            '  python start.py --roster --config 1_Input/_RosterConfigurations/01_Stock_Roster.json --fresh\n'
            '  python start.py --roster --remove\n'
            '  python start.py --game-options\n'
            '  python start.py --game-options --on cpu_vs_cpu cpu_management\n'
            '  python start.py --game-options --off cpu_management\n'
            '  python start.py --export-icons\n'
            '  python start.py --export-icons --use-output\n'
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
            '  python start.py --rename-slot 0xE1 "Purple Yoshi"\n'
            '  python start.py --copy-slot 0x00 0xE1\n'
            '  python start.py --set-voice 0x00 0x09\n'
            '  python start.py --set-stats 0x00 -\n'
            '  python start.py --set-icon 0xE1 front my_portrait.png --fit cover\n'
            '  python start.py --apply-slots 3_Output_Dat/_gui/slot/edits.json --dry-run\n'
            '  python start.py --apply-stat-edits stat_edits.json --dry-run\n'
            '  python start.py --save-roster my_roster.sluggiesroster\n'
            '  python start.py --load-roster my_roster.sluggiesroster --dry-run\n'
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
    mode.add_argument('--set-voice', nargs=2, metavar=('SQUARE', '0xNN'), help="give a square (any of its slots' IDs) the voice of 0xNN's family, on the select screen and on the field; a stock square changes its whole species, a new square its square-only new IDs; - resets it (read -> rebuild)")
    mode.add_argument('--set-stats', nargs=2, metavar=('0xNN', '0xMM'), help="let slot 0xNN play with stock player 0xMM's stats (stats, pitching, fielding, chemistry; model, size and voice stay); - resets it to its own / its template's (read -> rebuild)")
    mode.add_argument('--set-icon', nargs=3, metavar=('0xNN', 'VIEW', 'IMAGE'), help="make an image (PNG, JPEG, BMP, GIF, TGA or WEBP) slot 0xNN's front or side portrait, fitted to 48x51 (see --fit, --no-trim); the other view keeps what the slot shows now (read -> rebuild)")
    mode.add_argument('--copy-slot', nargs=2, metavar=('0xSS', '0xTT'), help='make slot 0xTT a clone of '
                      'character 0xSS as the game holds it (models, equipment, name, portraits, stats; the '
                      "square's voice stays unless 0xTT is alone on a new square)")
    mode.add_argument('--rename-slot', nargs=2, metavar=('0xNN', 'TEXT'), help='name a slot (stock characters included), one name for English, French and Spanish; it must fit the name plate; a blank TEXT resets the name (read -> rebuild)')
    mode.add_argument('--apply-slots', metavar='FILE', help='write staged slot edits (an edits file with patch/clear/rename edits per slot, as the GUI\'s "Patch Game" writes it) as one chain: read once, at most one roster rebuild, then the slot patches')
    mode.add_argument('--save-roster', metavar='FILE', help='save the whole roster of 3_Output_Dat (grid, names, voices, stats, own model directories, patched models, portraits) into a roster pack (.sluggiesroster)')
    mode.add_argument('--load-roster', metavar='FILE', help='load a roster pack into 3_Output_Dat: replaces the whole roster (with --dry-run: check the pack and show the per-slot differences only)')
    mode.add_argument('--write-slot-blocks', nargs=3, metavar=('0xNN', 'HIGH', 'LOW'), help="write finished model blocks (a roster pack's; '-' keeps that file) into a slot as they are (used by --load-roster)")
    mode.add_argument('--write-slot-equipment', nargs=3, metavar=('0xNN', 'FILE', 'BLOCK'), help="write a finished equipment block (a roster pack's) into a slot's file 2-5 (2 bat, 3 left glove, 4 right glove, 5 extra bat) as it is (used by --load-roster)")
    mode.add_argument('--resplit-unused', action='store_true', help='repair: give unused-character routes (dirs 89-94) that point at a playable character\'s block their own copy again')
    mode.add_argument('--roster', '--roster-dev', dest='roster', action='store_true', help='inject a roster configuration (--config, e.g. from 1_Input/_RosterConfigurations) into 3_Output_Dat, replacing the previous injection')
    mode.add_argument('--roster-state', action='store_true', help='read the draft grid from 3_Output_Dat into 3_Output_Dat/_gui/roster_state.json (used by the GUI)')
    mode.add_argument('--roster-derive', action='store_true', help='write the roster config that rebuilds 3_Output_Dat as it is into 3_Output_Dat/_gui/derived (read -> rebuild; then --roster --state)')
    mode.add_argument('--game-options', action='store_true', help='show or change game options (CPU vs CPU, ...) in 3_Output_Dat/main.dol; use with --on/--off')
    mode.add_argument('--stat-bridge-export', metavar='FILE', help="write the Sluggers Stat Editor's bridge file (where 3_Output_Dat/main.dol keeps every stat table, the characters, the baseline values) to FILE")
    mode.add_argument('--apply-stat-edits', nargs='+', metavar='FILE', help="write the Sluggers Stat Editor's edit files (stat_edits.json from Bridge Mode; several: in order, a later one wins) into 3_Output_Dat/main.dol; refused unless made from this main.dol; an item reset:0xNN clears that character's stat edits at its place in the order (with --dry-run: list the changes only)")
    mode.add_argument('--export', action='store_true', help='export all models from 1_Input to 2_Output_Models')
    mode.add_argument('--export-icons', action='store_true', help="write each character's FrontIcon.png/SideIcon.png into its model folder in 2_Output_Models")

    parser.add_argument('--debug', action='store_true', help='export only: write binary blobs as raw byte arrays instead of base64')
    parser.add_argument('--notex', action='store_true', help='export only: skip texture extraction')
    parser.add_argument('--untangle', action='store_true', help='export only: pass untangling flag through to export process')
    parser.add_argument('--glb', action='store_true', help='export only: also write .glb model files to disk (always writes .sluggie files)')
    parser.add_argument('--use-output', action='store_true', help='export-icons only: read DOL/DAT from 3_Output_Dat instead of 1_Input')
    parser.add_argument('--dry-run', action='store_true', help='roster/game-options/slot chains: validate without writing bytes')
    parser.add_argument('--config', metavar='PATH', help='roster only: the roster configuration JSON')
    parser.add_argument('--state', metavar='PATH', help='roster only: a derived config (--roster-derive) instead of --config')
    parser.add_argument('--target-id', metavar='0xNN', help="patch/unpatch only: write the .sluggie models into this character ID's slot instead of their own route (always Hammerspace)")
    parser.add_argument('--target-file', type=int, choices=(2, 3, 4, 5), metavar='N', help="with --target-id: the slot file an equipment .sluggie goes to (2 bat, 3 left glove, 4 right glove, 5 extra bat; default: the file it was exported from); with --unpatch --target-id and no files: reset only that equipment file")
    parser.add_argument('--as-low', action='store_true', help='with --target-id: a high-poly model without L_ partner is the low-poly model too')
    parser.add_argument('--validate-only', action='store_true', help='with --patch --target-id: build and validate the slot blocks, write nothing')
    parser.add_argument('--fit', choices=('contain', 'cover', 'strict'), help='set-icon only: contain (fit inside, the default), cover (fill and crop) or strict (the image must be 48x51)')
    parser.add_argument('--equipment', metavar='FILE', help="--patch-slot: the slot file (2 bat, 3 left glove, 4 right glove, 5 extra bat; or bat, glove_l, glove_r, extra) a bat or glove .sluggie goes to (default: the file it was exported from); --clear-slot: reset only that equipment file, or 'all' four, instead of the models")
    parser.add_argument('--no-gear', action='store_true', help="--patch-slot of a model into a new ID: do not take the model's bats and gloves along")
    parser.add_argument('--no-trim', action='store_true', help='set-icon only: do not crop the transparent border before fitting')
    parser.add_argument('--focus', metavar='0xNN', help='stat-bridge-export only: the character the stat editor preselects')
    parser.add_argument('--remove', action='store_true', help='roster only: reset the roster to vanilla (against 1_Input), stat edits included, and stop')
    parser.add_argument('--fresh', action='store_true', help="roster --config only: do not keep the slots' customisations (models in new IDs, names, portraits, stats sources, voices; slots leaving the grid are not reset); stat edits are still carried")
    parser.add_argument('--keep-stat-edits', action='store_true', help='roster --remove only: carry the stat edits over (used by --load-roster)')
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
    if args.dry_run and not (args.roster or args.game_options or args.load_roster
                             or args.patch_slot or args.clear_slot or args.rename_slot or args.set_voice
                             or args.copy_slot
                             or args.set_stats or args.set_icon or args.apply_slots or args.apply_stat_edits):
        parser.error('--dry-run can only be used with --roster, --game-options, --patch-slot, '
                     '--clear-slot, --copy-slot, --rename-slot, --set-voice, --set-stats, --set-icon, '
                     '--apply-slots, '
                     '--apply-stat-edits or --load-roster.')
    if args.equipment and not (args.patch_slot or args.clear_slot):
        parser.error('--equipment can only be used with --patch-slot or --clear-slot.')
    if args.no_gear and not args.patch_slot:
        parser.error('--no-gear can only be used with --patch-slot.')
    if (args.fit or args.no_trim) and not args.set_icon:
        parser.error('--fit and --no-trim can only be used with --set-icon.')
    if args.set_icon and args.set_icon[1] not in ('front', 'side'):
        parser.error('--set-icon VIEW must be front or side.')
    if args.focus and not args.stat_bridge_export:
        parser.error('--focus can only be used with --stat-bridge-export.')
    if (args.on or args.off) and not args.game_options:
        parser.error('--on and --off can only be used with --game-options.')
    if (args.config or args.remove or args.state) and not args.roster:
        parser.error('--config, --state and --remove can only be used with --roster.')
    if args.fresh and not (args.roster and args.config):
        parser.error('--fresh can only be used with --roster --config.')
    if args.keep_stat_edits and not (args.roster and args.remove):
        parser.error('--keep-stat-edits can only be used with --roster --remove.')
    if args.target_id and not (args.patch or args.unpatch is not None):
        parser.error('--target-id can only be used with --patch or --unpatch.')
    if args.unpatch == [] and not args.target_id:
        parser.error("--unpatch needs files (or --target-id 0xNN to restore a stock slot's models).")
    if args.target_file and not args.target_id:
        parser.error('--target-file needs --target-id.')
    if args.target_file and args.as_low:
        parser.error('--target-file does not combine with --as-low.')
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
    if not any([args.gui, args.patch, args.unpatch is not None, args.patch_slot, args.clear_slot, args.copy_slot, args.rename_slot, args.set_voice, args.set_stats, args.set_icon, args.apply_slots, args.save_roster, args.load_roster, args.write_slot_blocks, args.write_slot_equipment, args.resplit_unused, args.export, args.export_icons, args.roster, args.roster_state, args.roster_derive, args.game_options, args.stat_bridge_export, args.apply_stat_edits]):
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
        elif args.resplit_unused:
            run_resplit_unused()
        elif args.roster and args.config and not (args.fresh or args.remove):
            if not run_roster_switch(args.config, dry_run=args.dry_run):
                return 1
        elif args.roster:
            run_roster(config=args.config, remove=args.remove, dry_run=args.dry_run, state=args.state,
                       keep_stat_edits=args.keep_stat_edits)
        elif args.roster_state:
            run_roster_state()
        elif args.roster_derive:
            run_roster_state(derive=True)
        elif args.game_options:
            run_game_options(on=args.on, off=args.off, dry_run=args.dry_run)
        elif args.stat_bridge_export:
            if not run_stat_bridge_export(args.stat_bridge_export, focus=args.focus):
                return 1
        elif args.apply_stat_edits:
            if not run_apply_stat_edits(args.apply_stat_edits, dry_run=args.dry_run):
                return 1
        elif args.export:
            run_export(debug=args.debug, notex=args.notex, untangle=args.untangle, glb=args.glb)
        elif args.export_icons:
            run_export_icons(use_output=args.use_output)
        elif args.patch:
            # A failed slot patch fails the command (the apply chain stops on it); plain patches keep going.
            if not run_patching(args.patch, unpatch=False, target_id=args.target_id, as_low=args.as_low,
                                validate_only=args.validate_only, target_file=args.target_file) and args.target_id:
                return 1
        elif args.unpatch is not None:
            if not args.unpatch:
                if not run_clear_target(args.target_id, args.target_file):
                    return 1
            elif not run_patching(args.unpatch, unpatch=True, target_id=args.target_id,
                                  as_low=args.as_low, target_file=args.target_file) and args.target_id:
                return 1
        elif args.patch_slot:
            if not run_slot_chain(args.patch_slot[0], args.patch_slot[1], dry_run=args.dry_run,
                                  equipment=args.equipment, no_gear=args.no_gear):
                return 1
        elif args.clear_slot:
            if not run_slot_chain(args.clear_slot, dry_run=args.dry_run, equipment=args.equipment):
                return 1
        elif args.copy_slot:
            if not run_slot_chain(args.copy_slot[1], copy_from=args.copy_slot[0], dry_run=args.dry_run):
                return 1
        elif args.rename_slot:
            if not run_slot_chain(args.rename_slot[0], rename=args.rename_slot[1], dry_run=args.dry_run):
                return 1
        elif args.set_voice:
            if not run_slot_chain(args.set_voice[0], voice=args.set_voice[1], dry_run=args.dry_run):
                return 1
        elif args.set_stats:
            if not run_slot_chain(args.set_stats[0], stats=args.set_stats[1], dry_run=args.dry_run):
                return 1
        elif args.set_icon:
            if not run_slot_chain(args.set_icon[0], icon=tuple(args.set_icon[1:]), fit=args.fit,
                                  trim=not args.no_trim, dry_run=args.dry_run):
                return 1
        elif args.apply_slots:
            if not run_slot_chain(edits_file=args.apply_slots, dry_run=args.dry_run):
                return 1
        elif args.save_roster:
            if not run_save_roster(args.save_roster):
                return 1
        elif args.load_roster:
            if not run_load_roster(args.load_roster, dry_run=args.dry_run):
                return 1
        elif args.write_slot_blocks:
            if not run_write_slot_blocks(*args.write_slot_blocks):
                return 1
        elif args.write_slot_equipment:
            if not run_write_slot_equipment(*args.write_slot_equipment):
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
