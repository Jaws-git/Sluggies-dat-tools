"""The ``start.py`` commands the grid runs and the files around them (no Dear PyGui): command tuples
(staging check, apply, slot and package export, roster pack save / load, roster switch, gameplay save and
report), the readers of their result and plan files, the game directory setting (``read_game_dir``,
``write_game_dir``, used by ``deploy_files`` too), ``write_edits`` and the Patch Game label.

Reached through ``gui_grid``.
"""

import json
import os
import re
from typing import TYPE_CHECKING

if __package__:
    from .grid_common import (
        GAMEPLAY, GAME_WIDE, PACKAGE_EXPORT_REL, PACKAGE_EXTENSION, PACK_EXTENSION, SLOT_EXPORT_REL,
        _BUILD_LINE, hex_id,
    )
    from .grid_state import (
        name_of,
    )
else:
    from grid_common import (
        GAMEPLAY, GAME_WIDE, PACKAGE_EXPORT_REL, PACKAGE_EXTENSION, PACK_EXTENSION, SLOT_EXPORT_REL,
        _BUILD_LINE, hex_id,
    )
    from grid_state import (
        name_of,
    )

if TYPE_CHECKING:                      # annotations only: grid_pending imports this module
    from grid_pending import PendingEdits


def export_command(cid: int) -> tuple:
    """Export as .sluggie: the slot as the game holds it into ``2_Output_Assets/Custom <name> NN``."""
    return ('--export-slot', hex_id(cid))


def export_result(root_dir: str) -> dict | None:
    """The last slot export's result (``Roster/slot_export``): ``folder`` + ``name`` when it wrote one, ``error``
    when it aborted; None when there is none (it crashed before writing it)."""
    try:
        with open(os.path.join(root_dir, SLOT_EXPORT_REL), 'r', encoding='utf-8') as f:
            result = json.load(f)
    except (OSError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def export_toast(code: int, result: dict | None) -> str | None:
    """The note shown when "Export as .sluggie" wrote its folder; None when it did not (``export_failed_dialog``)."""
    folder = (result or {}).get('folder') if code == 0 else None
    if not folder or not os.path.isdir(folder):
        return None
    return f'{os.path.basename(os.path.normpath(folder))} has been created in 2_Output_Assets'


def package_name(state: dict | None, cid: int) -> str:
    """The file name "Export to .sluggiezip" offers: the slot's name as the grid shows it, safe for Windows."""
    name = ' '.join(re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', name_of(state, cid) if state else '').split()).rstrip(' .')
    return (name or f'Slot {hex_id(cid)}') + PACKAGE_EXTENSION


def package_export_command(cid: int, path: str) -> tuple:
    """Export to .sluggiezip: the slot as the game holds it into one character package."""
    return ('--export-sluggiezip', hex_id(cid), path)


def package_export_result(root_dir: str) -> dict | None:
    """The last package export's result (``Roster/slot_zip``): ``path`` when it wrote one, ``error`` when it
    aborted; None when there is none (it crashed before writing it)."""
    try:
        with open(os.path.join(root_dir, PACKAGE_EXPORT_REL), 'r', encoding='utf-8') as f:
            result = json.load(f)
    except (OSError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def preview_command(edits_path: str) -> tuple:
    """The dry run behind a confirm dialog: plan the edits file + run its build checks, nothing written."""
    return ('--apply-slots', edits_path, '--dry-run')


def apply_command(edits_path: str, accept_gecko: bool = False, accept_memory: bool = False) -> tuple:
    return ('--apply-slots', edits_path) + accept_gecko_args(accept_gecko) + accept_memory_args(accept_memory)


def accept_gecko_args(accept: bool) -> tuple:
    """After a summary that showed the Gecko warning was confirmed: the command must not ask again."""
    return ('--accept-gecko-break',) if accept else ()


def accept_memory_args(accept: bool) -> tuple:
    """After a summary that showed the player memory warning was confirmed ("Patch anyway"): no second question."""
    return ('--accept-memory-over',) if accept else ()


PATCH_LABEL = 'Patch Game'
PATCH_DEPLOY_LABEL = 'Patch Game & copy files'
DEPLOY_LABEL = 'Deploy patched files to game directory'
DEPLOY_SETTING = 'deploy_to_game_dir'           # the GUI settings key (``gui_settings``) of that checkbox
DEPLOY_ARGS = ('--deploy-game-files',)         # the start.py step that copies the files (deploy_files.py)
# the game directory, one line; shared with 3_Output_Dat/CopyFilesToGameDir.bat (the same copy, run by hand)
GAME_DIR_FILE_REL = os.path.join('3_Output_Dat', 'sluggiespath')
# where dt_na.dat and sound_NA/ (DATA/files) and main.dol / fst.bin (DATA/sys) go
GAME_SUBDIRS = (os.path.join('DATA', 'files'), os.path.join('DATA', 'sys'))
_GAME_DIR_ENCODING = 'oem' if os.name == 'nt' else 'utf-8'           # what cmd's set /p and echo use


def patch_label(count: int, deploy: bool) -> str:
    return f'{PATCH_DEPLOY_LABEL if deploy else PATCH_LABEL} ({count})'


def read_game_dir(path: str) -> str:
    """The game directory stored in the sluggiespath file (its first line), '' when there is none."""
    try:
        with open(path, encoding=_GAME_DIR_ENCODING, errors='replace') as f:
            return clean_game_dir(f.readline())
    except OSError:
        return ''


def clean_game_dir(text: str) -> str:
    """A typed or pasted directory: no surrounding blanks or quotes, no trailing separator."""
    text = (text or '').strip().strip('"').strip()
    return text.rstrip('\\/') if len(text) > 3 else text     # keep a drive root such as C:\


def game_dir_problem(game_dir: str) -> str | None:
    """Why ``game_dir`` is no unpacked Mario Super Sluggers main folder to copy into, or None when it is one."""
    if not game_dir:
        return 'no game directory given'
    if not os.path.isdir(game_dir):
        return f'the game directory does not exist: {game_dir}'
    missing = [sub for sub in GAME_SUBDIRS if not os.path.isdir(os.path.join(game_dir, sub))]
    if missing:
        return f'{game_dir} has no {" or ".join(missing)} folder (not the unpacked game\'s main folder?)'
    return None


def _write_whole(path: str, text: str, encoding: str = 'utf-8') -> None:
    """Write a file a child process reads next: into a temp file beside it, then renamed over it. A write that
    fails halfway (disk full, encoding) leaves the file as it was, never a cut-off one; the OSError goes to
    the caller."""
    tmp = f'{path}.{os.getpid()}.tmp'
    try:
        with open(tmp, 'w', encoding=encoding) as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_game_dir(path: str, game_dir: str) -> None:
    """Store the game directory (CopyFilesToGameDir.bat reads the first line with ``set /p``, hence the
    encoding)."""
    _write_whole(path, game_dir + '\n', _GAME_DIR_ENCODING)


def write_edits(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_whole(path, json.dumps(data, ensure_ascii=False, indent=1))


def build_sizes(output: str) -> dict[str, int]:
    """Block size per model file name, from the build check's log lines."""
    return {m.group('name'): int(m.group('bytes')) for m in _BUILD_LINE.finditer(output)}


def load_plan(path: str) -> dict | None:
    """The dry run's batch plan; None when there is none (the planner failed before planning)."""
    try:
        with open(path, encoding='utf-8') as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return None
    return plan if isinstance(plan, dict) and plan.get('action') == 'batch' else None


# --------------------------------------------------------------------------
# Roster packs
# --------------------------------------------------------------------------

# the fingerprint fields (Roster/pack.py FIELDS), as the dialogs and markers name them
PACK_FIELDS = {'high': 'High model', 'low': 'Low model', 'bat': 'bat', 'glove_l': 'left glove',
               'glove_r': 'right glove', 'extra': 'extra bat', 'model': 'model directory', 'front': 'front portrait',
               'side': 'side portrait', 'name': 'name', 'stats': 'stats', 'voice': 'square voice',
               'char_voice': 'own voice', 'clips': 'own voice clips', 'species_clips': 'voice pack clips',
               'square': 'square'}
EQUIP_FIELDS = ('bat', 'glove_l', 'glove_r', 'extra', 'clips', 'species_clips')   # absent from older packs' fingerprints
DIFF_TEXT = {'game': 'only in the game (leaves the grid)', 'pack': 'only in the pack (comes onto the grid)'}


def save_command(path: str) -> tuple:
    return ('--save-roster', path)


def load_command(path: str, dry_run: bool = False, accept_gecko: bool = False, accept_memory: bool = False) -> tuple:
    return (('--load-roster', path) + (('--dry-run',) if dry_run else ()) + accept_gecko_args(accept_gecko)
            + accept_memory_args(accept_memory))


def with_extension(path: str) -> str:
    return path if path.lower().endswith(PACK_EXTENSION) else path + PACK_EXTENSION


def pack_fingerprints(path: str) -> dict | None:
    """The per-slot fingerprints a roster pack holds (``fingerprints.json``), or None when it cannot be read."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as zf:
            data = json.loads(zf.read('fingerprints.json').decode('utf-8'))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    return data if isinstance(data, dict) else None


def load_pack_plan(path: str, action: str = 'load_pack') -> dict | None:
    """A planner's plan file (``action``: 'load_pack', or 'switch_roster' for ``switch_plan``), or None."""
    try:
        with open(path, encoding='utf-8') as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return None
    return plan if isinstance(plan, dict) and plan.get('action') == action else None


# --------------------------------------------------------------------------
# Roster switch (Roster Size tab: inject a preset, keep the slots' customisations)
# --------------------------------------------------------------------------

# Roster/migrate.py's field keys (test-pinned to migrate.FIELD_LABELS)
SWITCH_FIELDS = {'model': 'models', 'equipment': 'equipment', 'name': 'name', 'icon': 'portraits',
                 'stats': 'stats source', 'voice': 'square voice', 'char_voice': 'own voice',
                 'species_clips': 'voice pack clips', 'stat_edits': 'stat edits'}


def switch_command(path: str, dry_run: bool = False, fresh: bool = False, accept_gecko: bool = False) -> tuple:
    """``start.py --roster --config``: the switch chain (``fresh``: no slot customisations kept)."""
    return (('--roster', '--config', path) + (('--fresh',) if fresh else ()) + (('--dry-run',) if dry_run else ())
            + accept_gecko_args(accept_gecko))


def switch_plan(path: str) -> dict | None:
    return load_pack_plan(path, 'switch_roster')


# --------------------------------------------------------------------------
# Gameplay settings (the Gameplay tab, gui_gameplay.py)
# --------------------------------------------------------------------------

GAMEPLAY_RESET = 'reset'                              # Gameplay/apply.RESET_ITEM (test-pinned)
GAMEPLAY_STAGING_REL = os.path.join('_gui', 'gameplay')        # the staged edit files and load copies
GAMEPLAY_DIR_REL = os.path.join('_gui', 'Gameplay_Settings')   # where Save / Load settings... start
GAMEPLAY_EXTENSION = '.sluggiesgameplay'                       # Gameplay/apply.EXTENSION (test-pinned)
GAMEPLAY_DIR_SETTING = 'gameplay_last_dir'
GAMEPLAY_REPORT_REL = os.path.join('_gui', 'gameplay', 'report.json')


def gameplay_edit(item: str) -> dict:
    """The pending edit of a gameplay item: a file (the tab's staged values, a loaded settings file) or ``reset``."""
    return {'op': GAMEPLAY, 'id': hex_id(GAME_WIDE), 'file': item}


def staged_gameplay_items(pending: 'PendingEdits') -> list[str]:
    """The pending gameplay items (files, ``reset``), in staging order."""
    return [e['file'] for e in pending.edits if e['op'] == GAMEPLAY and e.get('file')]


def gameplay_item_label(item: str | None) -> str:
    """``back to 1_Input`` for a reset, ``loaded from NAME`` for a loaded file, ``changed values`` for the tab's own
    staged values (``edit_*.json`` in the staging folder)."""
    if not item:
        return 'changed values'
    if item.strip().lower() == GAMEPLAY_RESET:
        return 'every value back to 1_Input'
    name = os.path.basename(item)
    if name.startswith('load_'):
        return f'loaded from {name.split("_", 2)[-1]}'
    return 'changed values'


def load_gameplay_report(path: str) -> dict | None:
    """The ``--apply-gameplay --report`` JSON (``Gameplay/apply.report``, or ``{"error"}``); None when unreadable."""
    try:
        with open(path, encoding='utf-8') as f:
            report = json.load(f)
    except (OSError, ValueError):
        return None
    return report if isinstance(report, dict) else None


def gameplay_save_path(path: str) -> str:
    return path if path.lower().endswith(GAMEPLAY_EXTENSION) else path + GAMEPLAY_EXTENSION


def gameplay_save_command(path: str) -> tuple:
    return ('--save-gameplay', path)


def gameplay_report_command(path: str, report: str) -> tuple:
    return ('--apply-gameplay', path, '--dry-run', '--report', report)
