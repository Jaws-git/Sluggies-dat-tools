"""Character grid tab: everything that needs no running Dear PyGui (testable on its own).

* ``StateLoader``: the background ``start.py --roster-state`` read and its
  result (idle / running / done / failed).
* ``GridNav``: the pop-out levels (grid -> square -> slot) and what a click
  or ``Esc`` does.
* Label and tooltip text for squares and slots, and where each portrait crop
  is (``icon_file``) and whether it is the slot's own (``icon_note``).
* ``PendingEdits``: the staged slot edits (decision 14) and the overlay text
  and portrait previews they give.
* ``slot_dialog``: the confirm dialog of "Select .sluggie..." / "Clear slot",
  from the staging check's batch plan (``--apply-slots FILE --dry-run``: the
  pending edits plus the new one) and its output; ``summary_dialog``: Patch
  Game's summary of the full dry run.

The roster state is the dict ``Roster/state.py`` writes
(``3_Output_Dat/_gui/roster_state.json``).
"""

import json
import os
import re
from dataclasses import dataclass, field

STATE_REL = os.path.join('3_Output_Dat', '_gui', 'roster_state.json')
SLOT_PLAN_REL = os.path.join('3_Output_Dat', '_gui', 'slot', 'plan.json')
EDITS_REL = os.path.join('3_Output_Dat', '_gui', 'slot', 'edits.json')
# start.py modes whose commands can change what the grid shows: the tab re-reads after them
WRITING_FLAGS = frozenset({'--export', '--roster', '--patch', '--unpatch', '--resplit-unused',
                           '--patch-slot', '--clear-slot', '--apply-slots'})
UNNAMED = '-'
LUIGI = 0x01


def hex_id(cid: int) -> str:
    return f'0x{cid:02X}'


def chain_writes(steps) -> bool:
    """Whether a command chain can change the game files the grid is read from (dry runs and build checks do
    not)."""
    return any(arg in WRITING_FLAGS for step in steps for arg in step
               if '--dry-run' not in step and '--validate-only' not in step)


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------

class StateLoader:
    """One background read at a time; a refresh asked for while one runs is queued (run once afterwards)."""

    IDLE, RUNNING, DONE, FAILED = 'idle', 'running', 'done', 'failed'

    def __init__(self, state_path: str):
        self.state_path = state_path
        self.status = self.IDLE
        self.state = None
        self.error = ''
        self.queued = False

    def start(self) -> bool:
        """True when the caller should launch a read now; False when one runs (the refresh is queued)."""
        if self.status == self.RUNNING:
            self.queued = True
            return False
        self.status = self.RUNNING
        self.error = ''
        return True

    def finish(self, code: int, output: str = '') -> bool:
        """Take a read's exit code and output. Returns True when a queued refresh should start next."""
        if code == 0:
            try:
                with open(self.state_path, encoding='utf-8') as f:
                    self.state = json.load(f)
                self.status = self.DONE
            except (OSError, ValueError) as exc:
                self.status, self.error = self.FAILED, f'could not load {self.state_path}: {exc}'
        else:
            self.status, self.error = self.FAILED, error_message(output) or f'exit code {code}'
        queued, self.queued = self.queued, False
        return queued

    @property
    def message(self) -> str:
        if self.status == self.RUNNING:
            return 'Reading the grid from 3_Output_Dat...'
        if self.status == self.FAILED:
            return f'Could not read the grid: {self.error}'
        if self.status == self.DONE and self.state is not None:
            cols, rows = self.state['shape']
            kind = 'Stock' if self.state['kind'] == 'stock' else 'Roster'
            note = '' if self.state.get('names_read', True) else ' (no names: dt_na.dat missing)'
            if self.state.get('names_read', True) and not self.state.get('icons_read', True):
                note += ' (no portraits)'
            return f'{kind} grid, {cols}x{rows}, {len(self.state["squares"])} squares{note}'
        return ''


def error_message(output: str) -> str:
    """The last ``[Error]`` log line's message in a child's output."""
    for line in reversed(output.splitlines()):
        if '[Error]' in line:
            return line.rsplit('] ', 1)[-1].strip()
    return ''


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------

def characters(state: dict) -> dict[int, dict]:
    return {c['id']: c for c in state['characters']}


def name_of(state: dict, cid: int) -> str:
    c = characters(state).get(cid)
    if (c or {}).get('default_name'):
        return c['default_name']
    name = ((c or {}).get('name') or {}).get('en')
    if not name or name == UNNAMED:
        return f'(unnamed {hex_id(cid)})'
    return name


def known_name(state: dict, cid: int) -> str:
    """The name of a grid character, else just its ID (fallback keys can be characters not on the grid)."""
    return name_of(state, cid) if cid in characters(state) else hex_id(cid)


# --------------------------------------------------------------------------
# Portraits
# --------------------------------------------------------------------------

FRONT, SIDE = 'front', 'side'


def icon_ref(state: dict, cid: int, view: str) -> dict | None:
    """Where the game takes ``cid``'s ``view`` portrait from (``Roster/state_icons.resolve``), or None."""
    return ((characters(state).get(cid) or {}).get('icon') or {}).get(view)


def icon_file(state: dict, state_path: str, cid: int, view: str) -> str | None:
    """The crop PNG of a portrait (``3_Output_Dat/_gui/icons/...``), or None when there is none on disk."""
    ref = icon_ref(state, cid, view)
    if not ref or not ref.get('file'):
        return None
    path = os.path.join(os.path.dirname(state_path), state.get('icon_dir') or 'icons', ref['file'])
    return path if os.path.isfile(path) else None


def is_fallback(state: dict, cid: int, view: str) -> bool:
    ref = icon_ref(state, cid, view)
    return bool(ref) and ref['source'] != 'own'


def icon_note(state: dict, cid: int, view: str) -> str:
    """Where a portrait comes from, for the GUI's fallback marks."""
    ref = icon_ref(state, cid, view)
    if not ref:
        return 'no portrait read'
    source = ref['source']
    if source == 'own':
        return 'own portrait'
    if source == 'neighbour':
        return f'no own portrait: shows {known_name(state, ref["key"])} ({hex_id(ref["key"])}), the next lower key'
    if source == 'template':
        return f'no own portrait: shows its template {known_name(state, ref["alias"])} ({hex_id(ref["alias"])})'
    if source == 'mii':
        return 'Mii icon'
    return 'shows the "?" icon'


def square_label(state: dict, index: int) -> str:
    return name_of(state, state['squares'][index]['head'])


def square_tooltip(state: dict, index: int) -> list[str]:
    sq = state['squares'][index]
    lines = [f'{"Stock" if sq["kind"] == "stock" else "New"} square: {name_of(state, sq["head"])}',
             f'Voice: {name_of(state, sq["voice"])} ({hex_id(sq["voice"])})',
             'Members:'] + [f'  {hex_id(m)}  {name_of(state, m)}' for m in sq['members']]
    if is_fallback(state, sq['head'], FRONT):
        lines.append(f'Portrait: {icon_note(state, sq["head"], FRONT)}')
    if sq['head'] == LUIGI and not state.get('luigi_own_square', True):
        lines.append('(not on the stock grid: the game gives Luigi a captain\'s square at runtime; '
                     'shown beside the grid so his slots stay reachable)')
    return lines


def stock_luigi_note(state: dict) -> str:
    if state.get('luigi_own_square', True):
        return ''
    return ('Luigi has no square on the stock grid (the game hands him a captain\'s square at runtime); '
            'he is shown to the right of the grid.')


def slot_details(state: dict, cid: int) -> list[str]:
    c = characters(state).get(cid, {'id': cid})
    lines = [f'ID: {hex_id(cid)}']
    if c.get('own_model_dir'):
        source = known_name(state, c['model_source'])
        if source != hex_id(c['model_source']):
            source += f' ({hex_id(c["model_source"])})'
        lines.append(f'Model: own directory {c["model_dir"]} (files of {source})')
    elif c.get('template') is not None:
        lines.append(f'Model: template {name_of(state, c["template"])} ({hex_id(c["template"])}), '
                     f'directory {c["model_dir"]}')
    elif 'model_dir' in c:
        lines.append(f'Model: own, directory {c["model_dir"]}')
    blocks = c.get('blocks') or {}
    if blocks:
        lines.append('Blocks: ' + ', '.join(
            f'{label} {blocks[role]["length"] / (1024 * 1024):.2f} MB [{blocks[role]["sha1"][:8]}]'
            for role, label in (('high', 'High'), ('low', 'Low')) if role in blocks))
    if 'stats' in c:
        lines.append(f'Stats: {name_of(state, c["stats"])}' + ('' if c['stats'] == cid else f' ({hex_id(c["stats"])})'))
    if c.get('default_name'):
        lines.append(f'Name: the game text is still {c["name"]["en"]!r}; shown with its usual name')
    name = c.get('name') or {}
    if name and not c.get('default_name'):
        others = [f'{lang.upper()} {name[lang]}' for lang in ('fr', 'sp') if name.get(lang) and name[lang] != name.get('en')]
        if others:
            lines.append('Names: ' + ', '.join(others))
    return lines


# --------------------------------------------------------------------------
# Navigation
# --------------------------------------------------------------------------

GRID, SQUARE, SLOT = 'grid', 'square', 'slot'


class GridNav:
    """The open pop-out levels. A square is remembered by its head character, so a refresh reopens it even when
    the square moved; a one-member square opens straight at the slot level (``skipped``)."""

    def __init__(self, state: dict | None = None):
        self.state = state
        self.square_head = None        # head character of the open square, or None
        self.slot = None               # ID of the open slot, or None
        self.skipped = False           # the slot was opened straight from the grid (one-member square)

    # -- queries
    @property
    def level(self) -> str:
        if self.slot is not None:
            return SLOT
        if self.square_head is not None:
            return SQUARE
        return GRID

    @property
    def depth(self) -> int:
        return {GRID: 0, SQUARE: 1, SLOT: 1 if self.skipped else 2}[self.level]

    def square_index(self) -> int | None:
        if self.state is None or self.square_head is None:
            return None
        return next((i for i, sq in enumerate(self.state['squares']) if sq['head'] == self.square_head), None)

    def levels(self) -> list[str]:
        """The open levels above the grid, bottom first (what is drawn)."""
        if self.level == GRID:
            return []
        if self.level == SQUARE:
            return [SQUARE]
        return [SLOT] if self.skipped else [SQUARE, SLOT]

    # -- actions
    def open_square(self, index: int) -> None:
        sq = self.state['squares'][index]
        self.square_head = sq['head']
        if len(sq['members']) == 1:
            self.slot, self.skipped = sq['members'][0], True
        else:
            self.slot, self.skipped = None, False

    def open_slot(self, cid: int) -> None:
        if self.level != SQUARE:
            raise ValueError('a slot opens from the square level')
        self.slot, self.skipped = cid, False

    def back(self) -> bool:
        """One level back (outside click, Esc). False when already at the grid."""
        if self.level == SLOT and not self.skipped:
            self.slot = None
        elif self.level in (SLOT, SQUARE):
            self.square_head = self.slot = None
            self.skipped = False
        else:
            return False
        return True

    def click(self, inside_top_box: bool) -> bool:
        """A mouse click anywhere: outside the top level's box goes back one level."""
        return False if inside_top_box else self.back()

    def refresh(self, state: dict) -> None:
        """New state: reopen the same square / ID, one level lower for what no longer exists."""
        self.state = state
        index = self.square_index()
        if index is None:
            self.square_head = self.slot = None
            self.skipped = False
            return
        members = state['squares'][index]['members']
        if self.slot is not None and self.slot not in members:
            self.slot = None
            if self.skipped:
                self.square_head, self.skipped = None, False
        if self.slot is None and self.square_head is not None and len(members) == 1:
            self.open_square(index)           # now a one-member square: straight to its slot
        elif self.skipped and len(members) > 1:
            self.skipped = False              # the square grew: the slot now sits above its square


# --------------------------------------------------------------------------
# Staged edits (decision 14): the pending list and its overlay
# --------------------------------------------------------------------------

class PendingEdits:
    """The pending slot edits, in GUI memory only (decision 14). ``edits`` is the merged list in staging order
    (``plan['merged']`` of the last staging check), ``sections`` the planner's per-edit sections (notes,
    effects) that the overlay shows. Both survive a re-read; Patch Game re-plans everything on a fresh one."""

    def __init__(self):
        self.edits: list[dict] = []
        self.sections: list[dict] = []

    def __len__(self) -> int:
        return len(self.edits)

    def staging(self, edit: dict) -> dict:
        """The edits file of a staging check: the pending edits (build checks passed when they were staged)
        plus the new one."""
        return {'edits': [dict(e, checked=True) for e in self.edits] + [dict(edit)]}

    def to_file(self) -> dict:
        """The edits file Patch Game writes: every build check runs again."""
        return {'edits': [dict(e) for e in self.edits]}

    def accept(self, plan: dict) -> None:
        """Take the staging check's merged list (the new edit staged, earlier ones replaced or joined)."""
        self.edits = [dict(e) for e in plan.get('merged') or []]
        self.sections = [dict(s) for s in plan.get('edits') or []]

    def discard(self, cid: int) -> None:
        self.edits = [e for e in self.edits if int(e['id'], 16) != cid]
        self.sections = [s for s in self.sections if int(s['target'], 16) != cid]

    def clear(self) -> None:
        self.edits, self.sections = [], []

    def has(self, cid: int) -> bool:
        return any(int(e['id'], 16) == cid for e in self.edits)

    def model_edit(self, cid: int) -> dict | None:
        return next((e for e in self.edits if int(e['id'], 16) == cid and e['op'] in ('patch', 'clear')), None)

    def sections_for(self, cid: int) -> list[dict]:
        return [s for s in self.sections if int(s['target'], 16) == cid]

    def square_pending(self, state: dict, index: int) -> bool:
        return any(self.has(m) for m in state['squares'][index]['members'])

    def summary(self, cid: int) -> list[str]:
        """One line per pending edit of the slot (tooltips)."""
        return [_edit_title(e) for e in self.edits if int(e['id'], 16) == cid]

    def lines(self, cid: int) -> list[str]:
        """The slot level's pending lines: each edit, then what the slot shows once it is written."""
        out = []
        for section in self.sections_for(cid):
            out.append(_edit_title(section['edit']))
            effects = section.get('effects') or {}
            for key, label in (('model', 'Model'), ('name', 'Name'), ('stats', 'Stats'), ('voice', 'Voice'),
                               ('portrait_note', 'Portraits')):
                if effects.get(key):
                    out.append(f'  {label}: {effects[key]}')
            if effects.get('portraits'):
                out.append('  Portraits: ' + os.path.basename(os.path.dirname(os.path.dirname(
                    effects['portraits']['front']))) + ' (previewed, marked "pending")')
        return out

    def portrait(self, cid: int, view: str) -> str | None:
        """A pending portrait to preview (the model folder's ``icon/*.png``), or None."""
        for section in reversed(self.sections_for(cid)):
            path = ((section.get('effects') or {}).get('portraits') or {}).get(view)
            if path and os.path.isfile(path):
                return path
        return None


def _edit_title(edit: dict) -> str:
    if edit['op'] == 'patch':
        files = [os.path.basename(edit['file'])] + ([os.path.basename(edit['low'])] if edit.get('low') else [])
        return 'Pending: put ' + ' + '.join(files) + ' into this slot'
    if edit['op'] == 'clear':
        return 'Pending: clear this slot'
    return f'Pending: {edit["op"]}'


# --------------------------------------------------------------------------
# Slot actions: the confirm dialogs
# --------------------------------------------------------------------------

TEXT, WARN, ERROR, OK = 'text', 'warn', 'error', 'ok'
_BUILD_LINE = re.compile(r'Slot build check passed \| Model: (?P<name>.+?) \| Size: [\d.]+ MB \((?P<bytes>\d+) bytes\)')


def preview_command(edits_path: str) -> tuple:
    """The dry run behind a confirm dialog: plan the edits file + run its build checks, nothing written."""
    return ('--apply-slots', edits_path, '--dry-run')


def apply_command(edits_path: str) -> tuple:
    return ('--apply-slots', edits_path)


def write_edits(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


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
    return plan if plan.get('action') == 'batch' else None


def _mb(size: int) -> str:
    return f'{size / (1024 * 1024):.2f} MB'


@dataclass
class SlotDialog:
    title: str
    lines: list = field(default_factory=list)   # [(text, TEXT/WARN/ERROR/OK)]
    can_apply: bool = False


def _partner_lines(files: dict, sizes: dict, joined: bool = False) -> list:
    def label(path, role):
        name = os.path.basename(path)
        size = sizes.get(name)
        return f'{name} ({role}' + (f', {_mb(size)})' if size else ')')

    high, low = files.get('high'), files.get('low')
    if high and low:
        how = 'the Low pick joins the pending High pick' if joined else 'found side by side'
        return [(f'Models: {label(high, "High")} + {label(low, "Low")}, {how}', TEXT)]
    if high:
        size = sizes.get(os.path.basename(high))
        twice = f': {_mb(2 * size)} together' if size else ''
        return [(f'Model: {label(high, "High")}, no Low partner beside it', WARN),
                (f'The High model is used as the Low model too, so the slot loads it twice on the field{twice}',
                 WARN)]
    return [(f'Model: {label(low, "Low only")}, no High partner beside it', WARN),
            ("It goes under the slot's current High model, which must be its own partner (checked)", TEXT)]


def _section_lines(state: dict, section: dict, sizes: dict, warnings: bool = True) -> list:
    """A planned edit's lines (4f dialog content): source and models with their sizes, notes, warnings."""
    lines = []
    cid = int(section['target'], 16)
    patch = section['action'] == 'patch'
    if patch:
        source = int(section['source'], 16)
        lines.append((f'Source: {known_name(state, source)} ({hex_id(source)}) -> {name_of(state, cid)} '
                      f'({hex_id(cid)})', TEXT))
        lines += _partner_lines(section.get('files') or {}, sizes, joined='low' in (section.get('edit') or {}))
    notes = section['notes'][1:] if patch else section['notes']   # a patch's first note is the files line above
    lines += [(f'- {note}', TEXT) for note in notes]
    if warnings:
        lines += [(f'Warning: {warning}', WARN) for warning in section['warnings']]
    return lines


def _refused_lines(state: dict, plan: dict, cid: int | None = None) -> list:
    lines = []
    for refused in plan['refused']:
        rid = int(refused['target'], 16)
        where = '' if rid == cid else f'{known_name(state, rid)} ({hex_id(rid)}): '
        lines.append((f'Refused: {where}{refused["error"]}', ERROR))
    return lines


def slot_dialog(state: dict, cid: int, patch: bool, plan: dict | None, code: int, output: str,
                pending: PendingEdits | None = None) -> SlotDialog:
    """The confirm dialog of a staged edit, after its staging check (the pending edits plus this one, exit
    ``code``, log ``output``): what the edit does, its warnings and the verdict. ``can_apply`` (Stage stages it)
    only when the planner and the build check passed."""
    target = f'{name_of(state, cid)} ({hex_id(cid)})'
    dialog = SlotDialog(f'Put a model into {target}?' if patch else f'Clear {target}?')
    error = error_message(output)
    lines = dialog.lines
    if plan is None or plan['refused']:
        dialog.title = f'{target}: refused'
        if plan is None:
            error = error.removeprefix('refused, nothing written: ')        # the planner's wording; said below
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            lines += _refused_lines(state, plan, cid)
            if any(int(r['target'], 16) != cid for r in plan['refused']):
                lines.append(('A pending edit no longer fits the game files: discard it on its slot.', TEXT))
        lines.append(('Nothing was staged.', TEXT))
        return dialog
    section = next((s for s in plan['edits'] if int(s['target'], 16) == cid), None)
    if section is None:                       # a clear of a slot at its baseline
        skipped = next((s for s in plan['skipped'] if int(s['target'], 16) == cid), None)
        lines += [(f'- {note}', TEXT) for note in (skipped or {}).get('notes', [])]
        earlier = pending.model_edit(cid) if pending else None
        if earlier is not None:
            lines.append((f'Stage drops the slot\'s pending {earlier["op"]}, so the slot stays as it is.', OK))
            dialog.can_apply = True
        else:
            dialog.title = f'{target}: nothing to clear'
        return dialog
    lines += _section_lines(state, section, build_sizes(output))
    prefix = f'{hex_id(cid)}: '
    lines += [(f'- {note.removeprefix(prefix)}', TEXT) for note in plan['notes'] if note.startswith(prefix)]
    if not section['rebuild']:
        lines.append(('- no roster rebuild needed', TEXT))
    if code != 0:
        lines.append((f'Build check failed: {error or f"exit code {code}"}. Nothing was staged.', ERROR))
        return dialog
    checks = 'slot rules, and every model built and validated' if patch else 'slot rules'
    lines.append((f'Checks passed: {checks}. Nothing written yet: Stage adds the edit to the pending list, '
                  '"Patch Game" writes it.', OK))
    dialog.can_apply = True
    return dialog


def summary_dialog(state: dict, plan: dict | None, code: int, output: str) -> SlotDialog:
    """Patch Game's summary after the full dry run (every pending edit planned on a fresh read, every build
    check run): per-slot lines, all warnings, the verdict. ``can_apply`` (Patch Game runs the chain) only when every
    edit passed."""
    count = len((plan or {}).get('edits') or [])
    dialog = SlotDialog(f'Patch Game: write {count} pending edit{"s" if count != 1 else ""}?')
    lines = dialog.lines
    error = error_message(output)
    if plan is None or plan['refused']:
        dialog.title = 'Patch Game: refused'
        if plan is None:
            error = error.removeprefix('refused, nothing written: ')
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            lines += _refused_lines(state, plan)
            lines.append(('Discard or replace the refused edits on their slots, then try again.', TEXT))
        lines.append(('Nothing was written.', TEXT))
        return dialog
    sizes = build_sizes(output)
    for section in plan['edits']:
        cid = int(section['target'], 16)
        what = 'put a model in' if section['action'] == 'patch' else 'clear'
        lines.append((f'{name_of(state, cid)} ({hex_id(cid)}): {what}', OK))
        lines += [('    ' + text, kind) for text, kind in _section_lines(state, section, sizes, warnings=False)]
    for section in plan['skipped']:
        cid = int(section['target'], 16)
        lines.append((f'{name_of(state, cid)} ({hex_id(cid)}): nothing to clear (at its baseline already)', TEXT))
    lines += [(f'- {note}', TEXT) for note in plan['notes']]
    lines += [(f'Warning: {warning}', WARN) for warning in plan['warnings']]
    lines.append(('- one roster rebuild' if plan['rebuild'] else '- no roster rebuild needed', TEXT))
    if code != 0:
        lines.append((f'Build check failed: {error or f"exit code {code}"}. Nothing was written.', ERROR))
        return dialog
    if not plan['edits']:
        lines.append(('Nothing to write.', TEXT))
        return dialog
    lines.append(('Checks passed: slot rules, and every model built and validated. Patch Game writes them now.', OK))
    dialog.can_apply = True
    return dialog


