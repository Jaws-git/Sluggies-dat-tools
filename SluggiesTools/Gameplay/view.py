"""What the GUI's Gameplay tab shows (no Dear PyGui): values, marks and problems per field.

A ``Snapshot`` holds three layers, all read from the game files (no built-in
values):

* ``current``: ``3_Output_Dat/main.dol`` (without it the tab shows
  ``1_Input`` read-only, ``readonly``);
* ``vanilla``: ``1_Input/main.dol`` (the reset target and the "changed" mark);
* ``pending``: the result of the pending gameplay items (``apply.prepare`` on
  the output DOL, in staging order), equal to ``current`` without any.

On top of them the tab keeps **drafts**: values typed but not staged yet. A
cell shows draft > pending > current. Its marks: ``problem`` (a cross-field
check on the shown values fails), ``draft``, ``pending`` (Patch Game will
change it), ``changed`` (differs from 1_Input).

DOL images are cached by path, size and modification time, so the tab can
take a new snapshot on every re-read cheaply.
"""

import json
import os
from dataclasses import dataclass, field

try:
    from ..Dol import dolfile
    from . import apply, legacy, read, tables
except ImportError:
    from Dol import dolfile
    from Gameplay import apply, legacy, read, tables

PROBLEM, DRAFT, PENDING, CHANGED = 'problem', 'draft', 'pending', 'changed'

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
    current: dict = field(default_factory=dict)        # {field: value}
    vanilla: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)
    readonly: bool = True
    source: str = ''                                     # what the values come from (the tab's status line)
    error: str | None = None                             # the pending items could not be read / checked
    missing: list = field(default_factory=list)          # tables the shown DOL does not map

    @property
    def usable(self) -> bool:
        return bool(self.current)


def _document(item: str):
    if item.strip().lower() == apply.RESET_ITEM:
        return apply.Reset()
    return apply.read_file(item)


def snapshot(output_dol: str, input_dol: str, items: list[str] = ()) -> Snapshot:
    """The layers for the tab (module docstring); ``items``: the pending gameplay items, in staging order."""
    out, vanilla = load_image(output_dol), load_image(input_dol)
    snap = Snapshot()
    snap.vanilla = read.values(vanilla) if vanilla is not None else {}
    if out is None:
        snap.current = dict(snap.vanilla)
        snap.pending = dict(snap.vanilla)
        snap.missing = read.missing_tables(vanilla) if vanilla is not None else [t.key for t in tables.TABLES]
        snap.source = ('3_Output_Dat/main.dol is missing: showing 1_Input/main.dol read-only. Run the export first.'
                       if vanilla is not None else 'No main.dol in 3_Output_Dat or 1_Input: nothing to show.')
        return snap
    snap.readonly = False
    snap.current = read.values(out)
    snap.missing = read.missing_tables(out)
    snap.source = 'Values as 3_Output_Dat/main.dol holds them.'
    snap.pending = dict(snap.current)
    if items:
        try:
            prepared = apply.prepare(out, [_document(i) for i in items], list(items), vanilla)
        except (apply.EditFileError, OSError, ValueError) as exc:
            snap.error = f'The pending gameplay edits cannot be read: {exc}'
        else:
            for change in prepared.changes:
                snap.pending[change.field] = change.after
    return snap


def same(f: tables.GlobalField, a, b) -> bool:
    """Whether ``a`` and ``b`` store the same bytes in ``f`` (an f32 typed as 1.1 is the stored 1.1)."""
    if a is None or b is None:
        return a is b
    try:
        return f.pack(a) == f.pack(b)
    except (ValueError, OverflowError):
        return False


def base(snap: Snapshot, f: tables.GlobalField):
    """What the cell shows without a draft."""
    return snap.pending.get(f, snap.current.get(f))


def shown(snap: Snapshot, drafts: dict) -> dict:
    """``{field: value}`` the tab shows (drafts on top of pending)."""
    out = dict(snap.pending)
    out.update(drafts)
    return out


def problems(snap: Snapshot, drafts: dict) -> list[tables.Problem]:
    return tables.problems(shown(snap, drafts))


def marks(snap: Snapshot, drafts: dict, found: list[tables.Problem] | None = None) -> dict:
    """``{field: mark}`` for every field with one (module docstring, the strongest mark wins)."""
    found = problems(snap, drafts) if found is None else found
    bad = {f for p in found for f in p.fields}
    out = {}
    for f in tables.FIELDS:
        if f not in snap.current:
            continue
        value = drafts.get(f, base(snap, f))
        if f in bad:
            out[f] = PROBLEM
        elif f in drafts:
            out[f] = DRAFT
        elif not same(f, base(snap, f), snap.current[f]):
            out[f] = PENDING
        elif f in snap.vanilla and not same(f, value, snap.vanilla[f]):
            out[f] = CHANGED
    return out


def set_draft(snap: Snapshot, drafts: dict, f: tables.GlobalField, value) -> None:
    """A typed value: a draft unless it equals what the cell shows without one."""
    if same(f, value, base(snap, f)):
        drafts.pop(f, None)
    else:
        drafts[f] = value


def prune_drafts(snap: Snapshot, drafts: dict) -> None:
    """Drop drafts the game or the pending edits hold already (after a staging or a re-read)."""
    for f in [f for f, v in drafts.items() if f not in snap.current or same(f, v, base(snap, f))]:
        del drafts[f]


def section_values(snap: Snapshot, section: str) -> dict:
    """``{field: vanilla value}`` of a section's fields that the tab does not show at 1_Input now ("Reset
    section")."""
    return {f: snap.vanilla[f] for f in tables.section_fields(section)
            if f in snap.vanilla and f in snap.current and not same(f, base(snap, f), snap.vanilla[f])}


def tooltip(snap: Snapshot, drafts: dict, f: tables.GlobalField, text=None) -> str:
    """``1_Input: 95 / game: 50 / pending: 60 / typed: 70`` (only the layers that differ)."""
    text = text or (lambda v: str(apply.short(v)))
    parts = [f'1_Input: {text(snap.vanilla[f])}' if f in snap.vanilla else '1_Input: ?']
    if f in snap.current and not (f in snap.vanilla and same(f, snap.current[f], snap.vanilla[f])):
        parts.append(f'game: {text(snap.current[f])}')
    if f in snap.current and not same(f, base(snap, f), snap.current[f]):
        parts.append(f'pending: {text(base(snap, f))}')
    if f in drafts:
        parts.append(f'edited, not staged: {text(drafts[f])}')
    return '  /  '.join(parts)


def changed_count(snap: Snapshot) -> int:
    """How many values the game (with the pending edits) holds that differ from 1_Input."""
    return sum(1 for f, v in snap.pending.items() if f in snap.vanilla and not same(f, v, snap.vanilla[f]))


def write_edits(folder: str, values: dict, stem: str = 'edit') -> str:
    """Write an edit file (``apply.edits_document``) for ``values`` into ``folder``; its path."""
    os.makedirs(folder, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(folder, f'{stem}_{n}.json')):
        n += 1
    path = os.path.join(folder, f'{stem}_{n}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(apply.edits_document(values), f, ensure_ascii=False, indent=1)
    return path


def copy_for_staging(folder: str, path: str) -> str:
    """A copy of a settings file the user picked (``load_<n>_<name>``), so later changes to it do not change what
    is staged."""
    os.makedirs(folder, exist_ok=True)
    n = 1
    name = os.path.basename(path)
    while os.path.exists(os.path.join(folder, f'load_{n}_{name}')):
        n += 1
    target = os.path.join(folder, f'load_{n}_{name}')
    with open(path, 'rb') as src, open(target, 'wb') as dst:
        dst.write(src.read())
    return target


def prune_staging(folder: str, keep) -> None:
    """Delete staged files no pending edit uses any more."""
    keep = {os.path.normcase(os.path.abspath(p)) for p in keep}
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        if (name.startswith(('edit_', 'load_')) and os.path.normcase(os.path.abspath(path)) not in keep
                and os.path.isfile(path)):
            try:
                os.remove(path)
            except OSError:
                pass


def cell_labels() -> dict:
    """``{(table, row, column): (tab title, cell label)}`` for the load dialog."""
    tabs = dict(tables.TABS)
    return {(f.table, f.row, f.column): (tabs[tables.TABLES_BY_KEY[f.table].tab], tables.label(f))
            for f in tables.FIELDS}


LEGACY_TITLE = legacy.TITLE
