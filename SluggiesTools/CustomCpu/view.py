"""What the GUI's Custom CPU tab shows (no Dear PyGui): the deployed level, the tab's draft and its rows.

* ``Snapshot``: what ``3_Output_Dat/main.dol`` holds (the deployed record, None when the level is off) and the
  clean game's bytes from ``1_Input/main.dol`` (the "Stock (level 4)" column and every default).
* ``Draft``: what the tab shows and Deploy writes: the main switch, the button name, the settings. It starts as
  the deployed level and is **pending** while it differs from it.
* ``rows``: one section's value table. Marks: ``problem``, ``edited`` (differs from the deployed value: Deploy
  will change it), ``changed`` (differs from the stock level 4 value).

DOL images are cached by path, size and modification time.
"""

import json
import os
from dataclasses import dataclass, field

try:
    from ..Dol import dolfile
    from . import engine, menu_files, settings
except ImportError:
    from Dol import dolfile
    from CustomCpu import engine, menu_files, settings

PROBLEM, EDITED, CHANGED = 'problem', 'edited', 'changed'
STAGING_REL = os.path.join('_gui', 'custom_cpu')         # the settings file a Deploy runs with
SETTINGS_DIR_REL = os.path.join('_gui', 'CPU_Settings')  # where Save / Load settings... start
DIR_SETTING = 'custom_cpu_last_dir'
DEPLOY_FILE = 'deploy' + settings.EXTENSION

_IMAGES: dict = {}


def load_image(path: str) -> dolfile.DolImage | None:
    """``path``'s DOL (cached by size and modification time), or None when it is missing or unreadable."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = os.path.normcase(os.path.abspath(path))
    cached = _IMAGES.get(key)
    if cached is not None and cached[0] == (st.st_size, st.st_mtime_ns):
        return cached[1]
    try:
        with open(path, 'rb') as f:
            image = dolfile.DolImage(f.read())
    except (OSError, dolfile.DolError):
        return None
    _IMAGES[key] = ((st.st_size, st.st_mtime_ns), image)
    return image


@dataclass
class Snapshot:
    deployed: engine.Record | None = None
    stock_get: object = None                 # the clean game's bytes (None: the module's own copy)
    readonly: bool = True                    # no output game: nothing can be deployed
    source: str = ''
    error: str | None = None
    outfield_dive: bool = False              # the global game option is on


@dataclass
class Draft:
    on: bool = False
    button: str = engine.DEFAULT_BUTTON
    edits: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    name: str = ''

    def record(self) -> engine.Record:
        return engine.Record(self.button, dict(self.edits), dict(self.extra), self.name)

    @classmethod
    def of(cls, record: engine.Record | None) -> 'Draft':
        if record is None:
            return cls()
        return cls(True, record.button, dict(record.edits), dict(record.extra), record.name)


def snapshot(output_dol: str, input_dol: str) -> Snapshot:
    out, vanilla = load_image(output_dol), load_image(input_dol)
    snap = Snapshot()
    if vanilla is not None and all(vanilla.is_mapped(a, n) for a, n in settings.runs()):
        snap.stock_get = settings.image_get(vanilla)
    if out is None:
        snap.source = ('3_Output_Dat/main.dol is missing: nothing can be deployed. Run the export first.'
                       if vanilla is not None else 'No main.dol in 3_Output_Dat or 1_Input.')
        return snap
    if vanilla is None:
        snap.source = '1_Input/main.dol is missing: the custom CPU level needs the original game there.'
        return snap
    snap.readonly = False
    try:
        snap.deployed = engine.detect(out)
        snap.outfield_dive = out.is_mapped(settings.GRID_DIVE_GATE, 4) and engine.outfield_dive_on(out)
    except dolfile.DolError as exc:
        snap.error = str(exc)
    snap.source = ('The custom CPU level is on in 3_Output_Dat.' if snap.deployed is not None
                   else 'The custom CPU level is off in 3_Output_Dat.')
    return snap


def pending(snap: Snapshot, draft: Draft) -> bool:
    """Whether Deploy would change the game."""
    if snap.deployed is None:
        return draft.on
    return not draft.on or draft.record() != snap.deployed


def pending_text(snap: Snapshot, draft: Draft) -> str:
    if not pending(snap, draft):
        return 'Nothing pending: the game holds what the tab shows.'
    if not draft.on:
        return 'Pending: turn the custom CPU level off.'
    if snap.deployed is None:
        return 'Pending: turn the custom CPU level on with these settings.'
    old = Draft.of(snap.deployed)
    keys = {k for k in set(old.edits) | set(draft.edits) if old.edits.get(k) != draft.edits.get(k)}
    parts = ([f'{len(keys)} changed setting{"s" if len(keys) != 1 else ""}'] if keys else []) \
        + (['a new button name'] if old.button != draft.button else [])
    return 'Pending: ' + (' and '.join(parts) or 'the settings\' name') + '.'


def problems(snap: Snapshot, draft: Draft) -> list[str]:
    """Why Deploy is refused now (plain words), [] if it is not."""
    if not draft.on:
        return []
    found = settings.check(draft.edits, snap.stock_get)
    name = menu_files.name_problem(draft.button)
    return ([name[0].upper() + name[1:] + '.'] if name else []) + found


def warnings(draft: Draft) -> list[str]:
    odd = menu_files.unproven(draft.button) if draft.on else ''
    return [f'{" ".join(odd)} was never seen in the game: the button name may show gaps there.'] if odd else []


@dataclass
class Row:
    setting: object
    value: object                # what the tab shows
    stock: object                # the stock level 4 value (the default)
    base: object                 # what it holds without its own value (the group's tuned value while that is on)
    mark: str | None
    note: str = ''               # "held at 0 by ...", "on at every level (game option)"
    locked: bool = False


def _deployed_value(snap: Snapshot, s):
    if snap.deployed is None:
        return settings.default(s, snap.stock_get)
    return settings.value(s, snap.deployed.edits, snap.stock_get)


def row(snap: Snapshot, draft: Draft, s) -> Row:
    stock = settings.default(s, snap.stock_get)
    base = settings.base_value(s, draft.edits, snap.stock_get)
    shown = settings.value(s, draft.edits, snap.stock_get)
    note, locked = '', False
    held = settings.locked(draft.edits, s.key)
    if held is not None:
        lock = next(k for k, (keys, _v) in settings.LOCKS.items() if s.key in keys)
        note, locked, shown = f'held at {held} by "{settings.BY_KEY[lock].label}"', True, held
    elif settings.replaced_by(draft.edits, s.key):
        note = f'not used while "{settings.BY_KEY[settings.replaced_by(draft.edits, s.key)].label}" is on'
    elif s.key == settings.FIELD and snap.outfield_dive:
        note = 'the grounder dive is on at every level already (the "outfield dive" game option)'
    mark = None
    bad = settings._value_problem(s, shown)
    if bad:
        mark = PROBLEM
    elif not settings.same(s, shown, _deployed_value(snap, s)):
        mark = EDITED
    elif not settings.same(s, shown, stock):
        mark = CHANGED
    return Row(s, shown, stock, base, mark, note, locked)


def rows(snap: Snapshot, draft: Draft, section: str) -> list[Row]:
    return [row(snap, draft, s) for s in settings.SETTINGS if s.section == section]


def changed_sections(snap: Snapshot, draft: Draft) -> set[str]:
    """Sections with a value that differs from the stock level 4 value."""
    return {settings.BY_KEY[k].section for k in draft.edits if k in settings.BY_KEY}


def text(s, x) -> str:
    """A value as the tab shows it in its Stock column."""
    return settings._text(s, x)


def set_value(snap: Snapshot, draft: Draft, key: str, new) -> None:
    settings.set_value(draft.edits, key, new, snap.stock_get)


def reset(snap: Snapshot, draft: Draft, key: str) -> None:
    draft.edits.pop(key, None)


def reset_section(draft: Draft, section: str) -> None:
    for key in [k for k in draft.edits if k in settings.BY_KEY and settings.BY_KEY[k].section == section]:
        del draft.edits[key]


def load_levels(path: str) -> list[int]:
    """The level columns a settings file gives values for (``settings.levels_in``)."""
    return settings.levels_in(settings.read_document(path))


def load_file(snap: Snapshot, draft: Draft, path: str, level: int | None = None) -> list[str]:
    """Put a settings file's column into ``draft`` (the button name too when the file has one). Returns the
    notes; raises ``settings.SettingsError``."""
    loaded = settings.load(path, level, snap.stock_get)
    doc = settings.read_document(path)
    draft.edits, draft.extra, draft.name = loaded.edits, loaded.extra, loaded.name
    button = doc.get('button')
    if isinstance(button, str) and menu_files.name_problem(button) is None:
        draft.button = button
    return loaded.notes


def save_file(draft: Draft, path: str, name_from_file: bool = True) -> str:
    """Write ``draft`` as a settings file (the other tool's format plus the button name); the path written.
    ``name_from_file``: a draft without a name takes the file's."""
    if not path.lower().endswith(settings.EXTENSION):
        path += settings.EXTENSION
    name = draft.name or (os.path.splitext(os.path.basename(path))[0] if name_from_file else '')
    doc = settings.document(draft.edits, name, draft.extra)
    doc['button'] = draft.button
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def deploy_command(root_dir: str, draft: Draft) -> tuple:
    """The ``start.py`` command of a Deploy; writes the settings file it runs with."""
    if not draft.on:
        return ('--custom-cpu-off',)
    path = save_file(draft, os.path.join(root_dir, STAGING_REL, DEPLOY_FILE), name_from_file=False)
    return ('--custom-cpu', path, '--cpu-level', '4', '--cpu-button', draft.button)
