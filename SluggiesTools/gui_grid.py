"""Character grid tab: everything that needs no running Dear PyGui (testable on its own).

* ``StateLoader``: the background ``start.py --roster-state`` read and its
  result (idle / running / done / failed).
* ``GridNav``: the pop-out levels (grid -> square -> slot) and what a click
  or ``Esc`` does.
* Label and tooltip text for squares and slots, and where each portrait crop
  is (``icon_file``) and whether it is the slot's own (``icon_note``).
* ``PendingEdits``: the staged slot edits and the overlay text
  and portrait previews they give.
* ``slot_dialog``: the confirm dialog of "Select .sluggie..." / "Clear slot",
  from the staging check's batch plan (``--apply-slots FILE --dry-run``: the
  pending edits plus the new one) and its output; ``summary_dialog``: Patch
  Game's summary of the full dry run.
* Equipment: ``equipment_tiles`` (the slot level's Bat / Left glove / Right glove / Extra bat tiles:
  state from the read, overlay from the pending edits).
* Roster packs: ``load_dialog`` (the per-slot diff of
  ``--load-roster FILE --dry-run``), ``save_pending_dialog``, and the
  "changed since the pack was saved / loaded" marker (``Reference``).

The roster state is the dict ``Roster/state.py`` writes
(``_gui/roster_state.json``).
"""

import json
import os
import re
from dataclasses import dataclass, field

STATE_REL = os.path.join('_gui', 'roster_state.json')
SLOT_PLAN_REL = os.path.join('_gui', 'slot', 'plan.json')
EDITS_REL = os.path.join('_gui', 'slot', 'edits.json')
PACK_PLAN_REL = os.path.join('_gui', 'pack', 'plan.json')
SWITCH_PLAN_REL = os.path.join('_gui', 'switch', 'plan.json')
SLOT_EXPORT_REL = os.path.join('_gui', 'slot_export.json')    # Roster/slot_export.RESULT_FILE
PACK_DIR_REL = 'Roster_Packs'                   # where the pack dialogs start
PACK_EXTENSION = '.sluggiesroster'
# start.py modes whose commands can change what the grid shows: the tab re-reads after them
WRITING_FLAGS = frozenset({'--export', '--roster', '--patch', '--unpatch', '--resplit-unused',
                           '--patch-slot', '--clear-slot', '--copy-slot', '--rename-slot', '--set-voice',
                           '--set-stats',
                           '--set-icon', '--apply-slots', '--load-roster',
                           '--write-slot-blocks', '--write-slot-equipment', '--game-options'})
UNNAMED = '-'
CPU_VS_CPU_OPTIONS = ('cpu_vs_cpu', 'cpu_management')   # what the Options tab's CPU vs CPU button turns on
LUIGI = 0x01


def hex_id(cid: int) -> str:
    return f'0x{cid:02X}'


def chain_writes(steps) -> bool:
    """Whether a command chain can change the game files the grid is read from (dry runs and build checks do
    not)."""
    return any(arg in WRITING_FLAGS for step in steps for arg in step
               if '--dry-run' not in step and '--validate-only' not in step)


CPU_VS_CPU_ENABLE = 'Enable CPU vs CPU + management'
CPU_VS_CPU_DISABLE = 'Disable CPU vs CPU + management'


def cpu_vs_cpu_command(enable: bool = True) -> tuple:
    """The Options tab's CPU vs CPU button step: turn both options on, or both off."""
    return ('--game-options', '--on' if enable else '--off', *CPU_VS_CPU_OPTIONS)


def cpu_vs_cpu_button(state) -> tuple[str, bool]:
    """The button's label and whether it enables (only fully enabled shows Disable; a half-on state enables
    the missing option)."""
    enabled = cpu_vs_cpu_status(state)[1]
    return (CPU_VS_CPU_DISABLE, False) if enabled else (CPU_VS_CPU_ENABLE, True)


def cpu_vs_cpu_status(state) -> tuple[str, bool | None]:
    """The status label beside that button and whether both options are on (None: not known, e.g. no state yet or
    a state written before the reader listed game options)."""
    options = (state or {}).get('game_options')
    if options is None:
        return 'Cpu vs Cpu: unknown', None
    if all(key in options for key in CPU_VS_CPU_OPTIONS):
        return 'Cpu vs Cpu: enabled', True
    if 'cpu_vs_cpu' in options:
        return 'Cpu vs Cpu: enabled (without management)', False
    return 'Cpu vs Cpu: disabled', False


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


# The name plate (Roster/names.py: PLATE_CELL, FONT_SIZE, FONT_WEIGHT; test-pinned to ``names.fit_problem``)
PLATE_TEXT_WIDTH = 113               # the 115 px plate, 2 px margin
PLATE_FONT_SIZE, PLATE_FONT_WEIGHT = 14, 800


def name_problem(text: str, font_path: str) -> str | None:
    """Why ``text`` cannot be a slot name (None: it can): what ``Roster.names.fit_problem`` says, measured with the
    plate font at ``font_path`` (the GUI's rename dialog shows it live; the planner checks again)."""
    if not text or not text.strip():
        return 'a name cannot be empty'
    if text != text.strip():
        return 'a name cannot start or end with a space'
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return 'a name cannot hold control characters or line breaks'
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype(font_path, PLATE_FONT_SIZE)
        font.set_variation_by_axes([PLATE_FONT_WEIGHT, 100])
    except (OSError, ValueError, AttributeError):
        return None                      # no font to measure with: the planner decides
    width = ImageDraw.Draw(Image.new('RGBA', (115, 16))).textbbox((0, 0), text, font=font)[2]
    if width > PLATE_TEXT_WIDTH:
        return f'too long for the name plate ({width} px, at most {PLATE_TEXT_WIDTH})'
    return None


def rename_prefill(state: dict, cid: int) -> str:
    """The text the rename dialog starts with: the slot's English name, nothing for an unnamed / open one."""
    c = characters(state).get(cid) or {}
    if c.get('default_name'):
        return c['default_name']
    name = (c.get('name') or {}).get('en') or ''
    return '' if name == UNNAMED else name


# --------------------------------------------------------------------------
# Voice and stats
# --------------------------------------------------------------------------

PLAYER_END = 0x4D                    # stock player IDs: the stats sources (Roster/ids.py PLAYER_END)


def _labelled(state: dict, cid: int) -> str:
    return f'{known_name(state, cid)} ({hex_id(cid)})'


def stats_choices(state: dict, cid: int) -> list[tuple[str, str | None]]:
    """``(label, source)`` for the Stats dialog: the default first (``None``: a stock ID's own stats, a new ID's
    template's), then every stock player on the grid."""
    c = characters(state).get(cid) or {}
    default = c.get('template') if c.get('template') is not None else cid
    first = (f'Default: its template {_labelled(state, default)}' if c.get('template') is not None
             else f'Default: its own ({known_name(state, cid)})')
    sources = sorted(i for i in characters(state) if i < PLAYER_END)
    return [(first, None)] + [(_labelled(state, i), hex_id(i)) for i in sources if i != default]


def voice_choices(state: dict, index: int) -> list[tuple[str, str | None]]:
    """``(label, source)`` for the Voice dialog: the default first (a stock square's own voice; a new square:
    none set), then every stock square's head (one voice per species)."""
    sq = state['squares'][index]
    heads = sorted({s['head'] for s in state['squares'] if s['kind'] == 'stock'})
    if sq['kind'] == 'stock':
        first = f'Default: its own ({known_name(state, sq["head"])})'
        heads = [h for h in heads if h != sq['head']]
    else:
        first = 'Default: none set (each member speaks with its own species\' voice)'
    return [(first, None)] + [(_labelled(state, h), hex_id(h)) for h in heads]


def stats_text(state: dict, cid: int) -> str:
    c = characters(state).get(cid) or {}
    if c.get('stats') is None:
        return 'Stats: not read'
    return f'Stats now: {_labelled(state, c["stats"])}'


def voice_text(state: dict, index: int) -> str:
    sq = state['squares'][index]
    shown = f'Voice now: {_labelled(state, sq["voice"])}'
    return shown + (' (set)' if sq.get('voice_set') is not None else '')


def voice_reach(state: dict, index: int) -> str:
    """Who a square's voice reaches (``Roster/voices.py``, the grid step's square voices)."""
    if state['squares'][index]['kind'] == 'stock':
        return ('A stock square speaks as one species: the voice changes for every member of its wheel (spare '
                'rows and new IDs on it included), on the select screen and on the field. Nothing else changes.')
    return ('A new square\'s voice reaches its members without a colour wheel; members on a wheel keep their '
            'wheel\'s voice. It plays on the select screen and on the field.')


def choice_index(choices: list, source: str | None) -> int:
    return next((i for i, (_label, value) in enumerate(choices) if value == source), 0)


# --------------------------------------------------------------------------
# Portraits
# --------------------------------------------------------------------------

FRONT, SIDE = 'front', 'side'


def icon_ref(state: dict, cid: int, view: str) -> dict | None:
    """Where the game takes ``cid``'s ``view`` portrait from (``Roster/state_icons.resolve``), or None."""
    return ((characters(state).get(cid) or {}).get('icon') or {}).get(view)


def icon_file(state: dict, state_path: str, cid: int, view: str) -> str | None:
    """The crop PNG of a portrait (``_gui/icons/...``), or None when there is none on disk."""
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


def slot_count(state: dict, index: int) -> int:
    """How many slots (colour swatches) a square has: the number the grid shows on it."""
    return len(state['squares'][index]['members'])


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
    edits = stat_edits_of(state, cid)
    if edits:
        lines.append(f'Stat edits: {stat_edits_text(edits)}')
    if c.get('default_name'):
        lines.append(f'Name: the game text is still {c["name"]["en"]!r}; shown with its usual name')
    return lines


# --------------------------------------------------------------------------
# Portrait replacement
# --------------------------------------------------------------------------

MII_START, NEW_START = 0x4D, 0x66    # Mii IDs have no portrait records (Roster/slot_plan.has_portrait_records)
STAGED_ICONS_REL = os.path.join('_gui', 'slot', 'staged_icons')
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tga', '.webp')   # the picker's filter only
IMAGE_PATTERNS = ';'.join('*' + ext for ext in IMAGE_EXTENSIONS)
IMAGE_FILTERS = [('Images', IMAGE_PATTERNS), ('All files', '*.*')]
FIT_CHOICES = (('contain', 'Fit inside'), ('cover', 'Fill and crop'), ('strict', 'Exact 48x51'))
ICON_SIZE = (48, 51)


def can_replace_portrait(cid: int) -> bool:
    return cid < MII_START or cid >= NEW_START


def portrait_tip(cid: int, view: str) -> str:
    if not can_replace_portrait(cid):
        return f'{view.capitalize()} portrait (Miis show the Mii icon; it cannot be replaced)'
    return f'Click to replace the {view} portrait'


def _icon_import():
    try:
        from Roster import icon_import
    except ImportError:
        from SluggiesTools.Roster import icon_import
    return icon_import


class IconPreview:
    """The icon dialog's state: the picked image (read and checked once, ``Roster/icon_import``), the fit mode and
    the trim switch; ``result()`` redraws the 48x51 portrait in-process (Pillow only)."""

    def __init__(self, path: str):
        self.path = path
        self.fit, self.trim = 'contain', True
        self.source, self.error = None, None
        self._cache = {}
        icon_import = _icon_import()
        try:
            self.source = icon_import.load_user_image(path)
        except icon_import.IconImportError as exc:
            self.error = str(exc)

    def result(self):
        """``(48x51 RGBA portrait or None, warnings, refusal or None)`` for the current fit and trim."""
        if self.source is None:
            return None, [], self.error
        key = (self.fit, self.trim)
        if key not in self._cache:
            icon_import = _icon_import()
            try:
                image, warns = icon_import.fit_user_image(self.source.image, self.fit, self.trim)
                self._cache[key] = (image, self.source.warnings + warns, None)
            except icon_import.IconImportError as exc:
                self._cache[key] = (None, list(self.source.warnings), str(exc))
        return self._cache[key]

    def lines(self) -> list:
        """The dialog's message lines: the source's facts, notes, warnings and a refusal."""
        if self.source is None:
            return [(f'Refused: {self.error}', ERROR)]
        w, h = self.source.size
        lines = [(f'{os.path.basename(self.path)}: {self.source.format}, {w}x{h}', TEXT)]
        lines += [(note, TEXT) for note in self.source.notes]
        _image, warns, error = self.result()
        lines += [(f'Warning: {w_}', WARN) for w_ in warns]
        if error:
            lines.append((f'Refused: {error}', ERROR))
        return lines

    @property
    def ok(self) -> bool:
        return self.result()[0] is not None

    def save(self, folder: str, cid: int, view: str) -> str:
        """Write the portrait into ``folder`` (named by its pixels, so a preview texture never shows a stale file);
        returns its path."""
        import hashlib
        image = self.result()[0]
        digest = hashlib.sha1(image.tobytes()).hexdigest()[:12]
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f'{cid:02X}_{view}_{digest}.png')
        image.save(path, 'PNG')
        return path

    def edit(self, cid: int, view: str, staged: str) -> dict:
        """The staged icon edit: the normalised copy, taken as it is (strict, no trim)."""
        return {'op': 'icon', 'id': hex_id(cid), 'view': view, 'file': staged, 'fit': 'strict', 'trim': False,
                'origin': self.path}


SOURCE_BOX = (240, 240)


def source_thumbnail(image, box=SOURCE_BOX):
    """The icon dialog's view of the picked image: scaled to fit ``box`` (up by whole factors, nearest; down
    smoothly)."""
    from PIL import Image
    w, h = image.size
    factor = min(box[0] / w, box[1] / h)
    if factor >= 1:
        whole = int(factor)
        return image.resize((w * whole, h * whole), Image.Resampling.NEAREST) if whole > 1 else image.copy()
    return image.resize((max(1, round(w * factor)), max(1, round(h * factor))), Image.Resampling.LANCZOS)


def staged_icon_files(pending: 'PendingEdits') -> set[str]:
    return {e['file'] for e in pending.edits if e['op'] == 'icon' and e.get('file')}


def prune_staged_icons(folder: str, keep: set[str]) -> None:
    """Delete normalised portraits no pending edit uses any more."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    keep = {os.path.normcase(os.path.abspath(p)) for p in keep}
    for name in names:
        path = os.path.join(folder, name)
        if name.endswith('.png') and os.path.normcase(os.path.abspath(path)) not in keep:
            try:
                os.remove(path)
            except OSError:
                pass


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
# Staged edits: the pending list and its overlay
# --------------------------------------------------------------------------

class PendingEdits:
    """The pending slot edits, in GUI memory only. ``edits`` is the merged list in staging order
    (``plan['merged']`` of the last staging check), ``sections`` the planner's per-edit sections (notes,
    effects) that the overlay shows. Both survive a re-read; Patch Game re-plans everything on a fresh one.

    ``pack``: a staged roster pack load (``stage_pack``), or None. It replaces the whole roster, so it is the only
    pending edit while it is staged: staging it drops the slot edits, and slot edits cannot be staged on top of it
    (they would be planned against the game before the load)."""

    def __init__(self):
        self.edits: list[dict] = []
        self.sections: list[dict] = []
        self.pack: dict | None = None      # {'path', 'name', 'diff': {id: fields or status}}

    def __len__(self) -> int:
        return len(self.edits) + (1 if self.pack else 0)

    def stage_pack(self, path: str, plan: dict) -> None:
        """Stage the load of the roster pack at ``path`` (its dry-run ``plan``): the slot edits are dropped."""
        diff = {int(d['id'], 16): (d['fields'] if d['status'] == 'differs' else d['status'])
                for d in plan.get('diff') or [] if d['status'] != 'same'}
        self.clear()
        self.pack = {'path': path, 'name': os.path.basename(path), 'diff': diff}

    def _pack_lines(self, cid: int) -> list[str]:
        if not self.pack or cid not in self.pack['diff']:
            return []
        what = self.pack['diff'][cid]
        what = ('changes ' + ', '.join(PACK_FIELDS.get(f, f) for f in what) if isinstance(what, list)
                else DIFF_TEXT.get(what, what))
        return [f'Pending: load {self.pack["name"]}: {what}']

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
        """Drop the slot's pending edits; a pending pack load touching the slot is dropped as a whole."""
        self.edits = [e for e in self.edits if int(e['id'], 16) != cid]
        self.sections = [s for s in self.sections if int(s['target'], 16) != cid]
        if self.pack and cid in self.pack['diff']:
            self.pack = None

    def clear(self) -> None:
        self.edits, self.sections, self.pack = [], [], None

    def has(self, cid: int) -> bool:
        return any(int(e['id'], 16) == cid for e in self.edits) or bool(self._pack_lines(cid))

    def model_edit(self, cid: int) -> dict | None:
        return next((e for e in self.edits if int(e['id'], 16) == cid and e['op'] in MODEL_OPS), None)

    def value_edit(self, cid: int, op: str) -> dict | None:
        """The slot's pending ``op`` edit ('rename', 'stats', or 'voice': staged on the square's head)."""
        return next((e for e in self.edits if int(e['id'], 16) == cid and e['op'] == op), None)

    def icon_edit(self, cid: int, view: str) -> dict | None:
        """The slot's pending portrait edit of ``view``."""
        return next((e for e in self.edits if int(e['id'], 16) == cid and e['op'] == 'icon' and e.get('view') == view),
                    None)

    def equip_edit(self, cid: int, file: int) -> dict | None:
        """The slot's pending equipment edit (``equip`` / ``equip_clear``) for slot file ``file`` (2-5)."""
        return next((e for e in self.edits if int(e['id'], 16) == cid and e['op'] in EQUIP_OPS
                     and e.get('file') == file), None)

    def sections_for(self, cid: int) -> list[dict]:
        return [s for s in self.sections if int(s['target'], 16) == cid]

    def square_pending(self, state: dict, index: int) -> bool:
        return any(self.has(m) for m in state['squares'][index]['members'])

    def stat_edits_for(self, cid: int) -> list[dict]:
        """The pending stat edit files (bridged in from the Stat Editor, staged game-wide) that name slot ``cid``."""
        return [e for e in self.edits if e['op'] == STAT_EDITS and cid in stat_file_ids(e.get('file'))]

    def has_stats(self, cid: int) -> bool:
        return bool(self.stat_edits_for(cid))

    def square_stats(self, state: dict, index: int) -> bool:
        return any(self.has_stats(m) for m in state['squares'][index]['members'])

    def stat_summary(self, cid: int) -> list[str]:
        """One line per pending stat edit file that names the slot (tooltips, the slot level)."""
        return [_edit_title(e) for e in self.stat_edits_for(cid)]

    def summary(self, cid: int) -> list[str]:
        """One line per pending edit of the slot (tooltips)."""
        return [_edit_title(e) for e in self.edits if int(e['id'], 16) == cid] + self._pack_lines(cid)

    def lines(self, cid: int) -> list[str]:
        """The slot level's pending lines: each edit, then what the slot shows once it is written."""
        out = []
        for section in self.sections_for(cid):
            out.append(_edit_title(section['edit']))
            effects = section.get('effects') or {}
            for key, label in (('model', 'Model'), ('name', 'Name'), ('stats', 'Stats'), ('voice', 'Voice'),
                               ('portrait_note', 'Portraits'), (STAT_EDITS, 'Stat edits')):
                if effects.get(key) and not (key == 'portrait_note' and section['action'] == 'icon'):
                    out.append(f'  {label}: {effects[key]}')
            equipment = effects.get('equipment')
            if isinstance(equipment, dict):
                out += [f'  Equipment: {text}' for _file, text in sorted(equipment.items())]
            elif equipment:
                out.append(f'  Equipment: {equipment}')
            if effects.get('portraits') and section['action'] == COPY:
                out.append('  Portraits: copied (previewed, marked "pending")')
            if effects.get('portraits') and section['action'] == 'patch':
                out.append('  Portraits: ' + os.path.basename(os.path.dirname(os.path.dirname(
                    effects['portraits']['front']))) + ' (previewed, marked "pending")')
        return out + self._pack_lines(cid)

    def titles(self) -> list[tuple[str | None, str]]:
        """``(slot id or None, text)`` per pending edit, for the discard / save questions."""
        out = [(e['id'], _edit_title(e).removeprefix('Pending: ')) for e in self.edits]
        if self.pack:
            n = len(self.pack['diff'])
            out.append((None, f'load the roster pack {self.pack["name"]} ({n} slot{"s" if n != 1 else ""} change)'))
        return out

    def portrait(self, cid: int, view: str) -> str | None:
        """A pending portrait to preview (the model folder's ``icon/*.png``), or None."""
        for section in reversed(self.sections_for(cid)):
            path = ((section.get('effects') or {}).get('portraits') or {}).get(view)
            if path and os.path.isfile(path):
                return path
        return None


def _edit_title(edit: dict) -> str:
    if edit['op'] == STAT_EDITS:
        return f'Pending: stat editor value changes'
    if edit['op'] == 'equip':
        what = EQUIP_LABELS.get(edit.get('file'), 'equipment').lower()
        where = f' (file {edit["file"]})' if edit.get('file') in (5,) else ''
        bundled = ' (bundled with the model)' if edit.get('origin') == 'bundled' else ''
        return f'Pending: {what}{where} from {os.path.basename(edit.get("sluggie") or "?")}{bundled}'
    if edit['op'] == 'equip_clear':
        return f'Pending: reset the {EQUIP_LABELS.get(edit.get("file"), "equipment").lower()}'
    if edit['op'] == 'patch':
        files = [os.path.basename(edit['file'])] + ([os.path.basename(edit['low'])] if edit.get('low') else [])
        return 'Pending: put ' + ' + '.join(files) + ' into this slot'
    if edit['op'] == 'clear':
        return 'Pending: clear this slot'
    if edit['op'] == COPY:
        return f'Pending: paste a clone of {edit.get("source")}'
    if edit['op'] == 'rename':
        return f'Pending: rename to {edit["text"]!r}' if edit.get('text') else 'Pending: reset the name'
    if edit['op'] == 'stats':
        return f'Pending: play with {edit["source"]}\'s stats' if edit.get('source') else 'Pending: default stats'
    if edit['op'] == 'voice':
        return (f'Pending: the square speaks with {edit["source"]}\'s voice' if edit.get('source')
                else 'Pending: the square\'s default voice')
    if edit['op'] == 'icon':
        return f'Pending: {edit.get("view")} portrait from {icon_label(edit)}'
    if edit['op'] == STAT_RESET:
        return 'Pending: clear its stat edits'
    return f'Pending: {edit["op"]}'


def icon_label(edit: dict) -> str:
    """The user's file name of an icon edit (``origin``: the picked file, ``file``: its normalised copy)."""
    return os.path.basename(edit.get('origin') or edit.get('file') or '?')


# --------------------------------------------------------------------------
# Equipment tiles
# --------------------------------------------------------------------------

EQUIP_OPS = ('equip', 'equip_clear')
COPY = 'copy'                                 # paste: the slot becomes a clone (Roster/slot_plan, test-pinned)
MODEL_OPS = ('patch', 'clear', COPY)
# A stat edit (the stat editor's edit file) belongs to no slot: its "id" is GAME_WIDE (Roster/slot_plan, test-pinned)
STAT_EDITS, GAME_WIDE = 'stat_edits', 0xFF
STAT_RESET = 'stat_reset'                     # clears one slot's stat edits (Roster/slot_plan, test-pinned)


COPY_MULTIPLE = 'Cannot copy multiple characters at once'
COPY_LABEL, PASTE_LABEL = 'Copy', 'Paste'
EXPORT_LABEL = 'Export as .sluggie'


def copy_edit(target: int, source: int) -> dict:
    """The pending edit that makes slot ``target`` a clone of ``source`` (paste)."""
    return {'op': COPY, 'id': hex_id(target), 'source': hex_id(source)}


def context_menu(state: dict, members: list, copied: int | None, pack_pending: bool,
                 locked: bool) -> list[tuple[str, bool, str | None]]:
    """The right-click menu of a grid square or a colour-wheel swatch: ``(label, enabled, why disabled)``. A square
    with several characters gets only a disabled line; one character gets Copy and Paste (Paste needs a copied
    character other than this one), then Export as .sluggie."""
    if len(members) != 1:
        return [(COPY_MULTIPLE, False, 'Open the square and right-click one of its characters.')]
    cid = members[0]
    busy = 'A command is running or a dialog is open.' if locked else None
    pack = 'A roster pack load is pending: press "Patch Game" (or Discard it) first.' if pack_pending else None
    copy_why = busy
    if copied is None:
        paste_why = 'Copy a character first (right-click it, Copy).'
    elif copied == cid:
        paste_why = 'This is the copied character itself.'
    elif copied not in characters(state):
        paste_why = 'The copied character is no longer on the grid.'
    else:
        paste_why = busy or pack
    return [(COPY_LABEL, copy_why is None, copy_why), (PASTE_LABEL, paste_why is None, paste_why),
            (EXPORT_LABEL, busy is None, busy)]


def export_command(cid: int) -> tuple:
    """Export as .sluggie: the slot as the game holds it into ``2_Output_Models/Custom <name> NN``."""
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


def folder_size(folder: str) -> int:
    """The bytes of every file under ``folder``."""
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def export_summary_dialog(state: dict | None, cid: int, code: int, result: dict | None) -> 'SlotDialog':
    """Shown when "Export as .sluggie" ends: the new folder, its character and total size; or why it aborted."""
    who = f'{name_of(state, cid) if state else hex_id(cid)} ({hex_id(cid)})'
    folder = (result or {}).get('folder') if code == 0 else None
    if folder and os.path.isdir(folder):
        return SlotDialog('Export finished', [
            (f'Exported {who}.', OK),
            (f'Folder: {os.path.normpath(folder)}', TEXT),
            (f'Total size: {_mb(folder_size(folder))}', TEXT)])
    error = (result or {}).get('error')
    return SlotDialog('Export aborted', [
        (f'{who} was not exported; nothing was written.', ERROR),
        (f'Reason: {error}' if error else 'Reason: see the log for details.', WARN if error else TEXT)])


def export_pending_dialog(state: dict | None, cid: int, pending: PendingEdits) -> 'SlotDialog':
    """Asked before "Export as .sluggie" while the slot has pending edits: the export takes the game as it is."""
    who = f'{name_of(state, cid) if state else hex_id(cid)} ({hex_id(cid)})'
    lines = [(f'{who} has pending edits (not written to the game yet):', TEXT)]
    lines += [(f'  {line}', WARN) for line in pending.lines(cid)]
    lines.append(('The export takes the slot as the game holds it now, so they are not in it. To include them, '
                  'Cancel and run "Patch Game" first.', TEXT))
    return SlotDialog(f'Export {who} without its pending edits?', lines, True)


def stat_reset_edit(cid: int) -> dict:
    """The pending edit that clears slot ``cid``'s stat edits."""
    return {'op': STAT_RESET, 'id': hex_id(cid)}


def stat_edits_of(state: dict | None, cid: int) -> dict | None:
    """The stat edits the game holds for ``cid`` (``{'fields': [...], 'chemistry': n}``, from the state read), or
    None."""
    return (((state or {}).get('stat_edits') or {}).get('characters') or {}).get(hex_id(cid))


def stat_edits_text(edits: dict) -> str:
    """``3 fields (stamina, slap size, charge pitch speed), 2 chemistry values`` (``StatEditor/carry``'s wording)."""
    parts = []
    names = [f.split('.', 1)[-1] for f in edits.get('fields') or []]
    if names:
        shown = ', '.join(names[:4]) + (f', +{len(names) - 4} more' if len(names) > 4 else '')
        parts.append(f'{len(names)} field{"s" if len(names) != 1 else ""} ({shown})')
    if edits.get('chemistry'):
        n = edits['chemistry']
        parts.append(f'{n} chemistry value{"s" if n != 1 else ""}')
    return ', '.join(parts)


STATS_REPLACED = 'this step replaces main.dol, so they are lost'
STATS_RESET = 'a reset to vanilla clears them'


def stat_edit_risk(state: dict | None, lost: bool, reason: str = STATS_REPLACED) -> str | None:
    """What an export / roster injection does to the game's stat edits (``config_risks``). ``lost``: every stat
    edit goes (``reason``: the untangle export replaces main.dol, a reset to vanilla clears them); else the roster
    run carries them over by character ID and only new IDs the new roster lacks lose theirs (None when no new ID
    has any)."""
    summary = (state or {}).get('stat_edits') or {}
    edited = [int(k, 16) for k in summary.get('characters') or {}]
    globals_ = summary.get('globals') or 0
    if lost:
        if not edited and not globals_:
            return None
        what = [f'{len(edited)} character{"s" if len(edited) != 1 else ""}'] if edited else []
        what += [f'{globals_} global value{"s" if globals_ != 1 else ""}'] if globals_ else []
        return f'Stat edits on {" and ".join(what)}: {reason}.'
    new = sorted(c for c in edited if c >= NEW_START)
    if not new:
        return None
    return (f'Stat edits on {len(new)} new ID{"s" if len(new) != 1 else ""}: a roster without '
            f'{"that ID" if len(new) == 1 else "those IDs"} drops them (the other stat edits are carried over by '
            'character ID).')


def edit_who(state: dict | None, cid: int) -> str:
    """Who a pending or planned edit is for: ``Mario (0x00)``, or ``Stat edits`` for a game-wide stat edit."""
    if cid == GAME_WIDE:
        return 'Stat edits'
    return f'{name_of(state, cid) if state else hex_id(cid)} ({hex_id(cid)})'
# state key -> (slot file, label); the file numbers are Roster/gear.py's (test-pinned)
EQUIP_TILES = (('bat', 2, 'Bat'), ('glove_l', 3, 'Left glove'), ('glove_r', 4, 'Right glove'),
               ('extra', 5, 'Extra bat'))
EQUIP_LABELS = {file: label for _role, file, label in EQUIP_TILES}
EXTRA_FILE = 5
EXTRA_HINT = ('File 5 is an extra slot that only Peach (a second bat) and Wario fill; what the game does with it on '
              'other characters is not confirmed yet.')
ORIGINAL, MODIFIED, EMPTY, PENDING_STATE = 'Original', 'Modified', 'Empty', 'Pending'


def equipment_state(state: dict, cid: int, role: str) -> str:
    """``Empty`` (a placeholder), ``Modified`` (not the vanilla block of its directory) or ``Original`` (also when
    the read cannot tell: the input files are unreadable)."""
    entry = ((characters(state).get(cid) or {}).get('equipment') or {}).get(role) or {}
    if entry.get('placeholder'):
        return EMPTY
    return MODIFIED if entry.get('vanilla') is False else ORIGINAL


def equipment_tiles(state: dict, cid: int, pending: 'PendingEdits | None' = None) -> list[dict]:
    """One dict per equipment file for the slot level: ``file``, ``label``, ``state`` (Original / Modified / Empty,
    or Pending with a pending edit), ``text`` (the line under the label), ``tip`` (tooltip lines) and ``can_reset``
    (the tile's Reset does something: modified, or a pending edit to drop). ``[]`` without equipment in the read."""
    char = characters(state).get(cid) or {}
    equipment = char.get('equipment')
    if not equipment:
        return []
    tiles = []
    for role, file, label in EQUIP_TILES:
        entry = equipment.get(role)
        if entry is None:
            continue
        now = equipment_state(state, cid, role)
        edit = pending.equip_edit(cid, file) if pending is not None else None
        tip = [f'{label} = file {file} of the slot\'s model directory']
        if entry.get('placeholder'):
            tip.append('empty: the shared placeholder block most characters ship in this file')
        else:
            tip.append(f'{_mb(entry["length"])} [{entry["sha1"][:8]}]')
        if entry.get('shared_with') and char.get('id', cid) < NEW_START:
            tip.append(f'also loaded by {entry["shared_with"]} other slot{"s" if entry["shared_with"] != 1 else ""}; '
                       'a replacement only changes this slot')
        if file == EXTRA_FILE:
            tip.append(EXTRA_HINT)
        if edit is not None:
            text = _edit_title(edit).removeprefix('Pending: ')
            tiles.append({'file': file, 'label': label, 'state': PENDING_STATE, 'text': text, 'tip': tip,
                          'can_reset': True})
            continue
        tiles.append({'file': file, 'label': label, 'state': now, 'text': now, 'tip': tip,
                      'can_reset': now == MODIFIED})
    return tiles


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


PATCH_LABEL = 'Patch Game'
PATCH_DEPLOY_LABEL = 'Patch Game & copy files'
DEPLOY_LABEL = 'Deploy patched files to game directory'
DEPLOY_SETTING = 'deploy_to_game_dir'           # the GUI settings key (``gui_settings``) of that checkbox
DEPLOY_SCRIPT_REL = os.path.join('3_Output_Dat', 'CopyFilesToGameDir.bat')
GAME_DIR_FILE_REL = os.path.join('3_Output_Dat', 'sluggiespath')    # the script's game directory, one line
# where CopyFilesToGameDir.bat copies dt_na.dat (DATA/files) and main.dol / fst.bin (DATA/sys)
GAME_SUBDIRS = (os.path.join('DATA', 'files'), os.path.join('DATA', 'sys'))
_GAME_DIR_ENCODING = 'oem' if os.name == 'nt' else 'utf-8'           # what cmd's set /p and echo use


class ShellStep(tuple):
    """A command chain step run as it is, not as start.py arguments (``SluggiesGui.run_chain``)."""


def patch_label(count: int, deploy: bool) -> str:
    return f'{PATCH_DEPLOY_LABEL if deploy else PATCH_LABEL} ({count})'


def deploy_command(script: str, comspec: str | None = None) -> ShellStep:
    """Run CopyFilesToGameDir.bat (through cmd, so it also runs from a path with spaces)."""
    return ShellStep((comspec or os.environ.get('ComSpec', 'cmd.exe'), '/c', script))


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


def write_game_dir(path: str, game_dir: str) -> None:
    """Store the game directory for CopyFilesToGameDir.bat (it reads the first line with ``set /p``)."""
    with open(path, 'w', encoding=_GAME_DIR_ENCODING) as f:
        f.write(game_dir + '\n')


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
    offer_stat_reset: bool = False              # the slot keeps stat edits: offer "Stage + clear stat edits"


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
    """A planned edit's lines (the confirm dialog's content): source and models with their sizes, notes, warnings."""
    lines = []
    cid = int(section['target'], 16)
    patch = section['action'] == 'patch'
    equip = section['action'] == 'equip'
    if section['action'] == COPY:
        source = int(section['source'], 16)
        lines.append((f'Clone of {known_name(state, source)} ({hex_id(source)}) -> {name_of(state, cid)} '
                      f'({hex_id(cid)}), as the game holds it now (its pending edits are not copied)', TEXT))
        effects = section.get('effects') or {}
        for key, label in (('model', 'Models'), ('name', 'Name'), ('stats', 'Stats'), ('voice', 'Voice'),
                           ('portrait_note', 'Portraits'), (STAT_EDITS, 'Stat values')):
            if effects.get(key):
                lines.append((f'{label}: {effects[key]}', TEXT))
        for _file, text in sorted((effects.get('equipment') or {}).items()):
            lines.append((f'Equipment: {text}', TEXT))
        lines += [(f'- {note}', TEXT) for note in section['notes'][1:]]
        if warnings:
            lines += [(f'Warning: {warning}', WARN) for warning in section['warnings']]
        return lines
    if patch:
        source = int(section['source'], 16)
        lines.append((f'Source: {known_name(state, source)} ({hex_id(source)}) -> {name_of(state, cid)} '
                      f'({hex_id(cid)})', TEXT))
        lines += _partner_lines(section.get('files') or {}, sizes, joined='low' in (section.get('edit') or {}))
    if equip:
        gear = section.get('gear') or {}
        name = os.path.basename(gear.get('path') or '?')
        size = sizes.get(name)
        bundled = ' (bundled with the model)' if gear.get('origin') == 'bundled' else ''
        lines.append((f'{gear.get("label", "Equipment")}: {name}{bundled}' + (f', {_mb(size)}' if size else ''), TEXT))
    notes = section['notes'][1:] if patch or equip else section['notes']   # the first note is the line above
    lines += [(f'- {note}', TEXT) for note in notes]
    if warnings:
        lines += [(f'Warning: {warning}', WARN) for warning in section['warnings']]
    return lines


def _refused_lines(state: dict, plan: dict, cid: int | None = None) -> list:
    lines = []
    for refused in plan['refused']:
        rid = int(refused['target'], 16)
        where = ('' if rid == cid else 'Stat edits: ' if rid == GAME_WIDE
                 else f'{known_name(state, rid)} ({hex_id(rid)}): ')
        lines.append((f'Refused: {where}{refused["error"]}', ERROR))
    return lines


VALUE_KINDS = ('rename', 'stats', 'voice', 'icon')   # edits that change one value, no model


def _find_section(plan: dict, cid: int, kind: str, view: str | None, gear_file: int | None) -> dict | None:
    """The planned section of the edit just staged (the plan also lists the pending edits' sections)."""
    for section in plan['edits']:
        if int(section['target'], 16) != cid or section['action'] != kind:
            continue
        edit = section.get('edit') or {}
        if kind == 'icon' and view is not None and edit.get('view') != view:
            continue
        if kind in EQUIP_OPS and gear_file is not None and edit.get('file') != gear_file:
            continue
        return section
    return None


def slot_dialog(state: dict, cid: int, patch: bool, plan: dict | None, code: int, output: str,
                pending: PendingEdits | None = None, rename: bool = False, kind: str | None = None,
                view: str | None = None, gear_file: int | None = None) -> SlotDialog:
    """The confirm dialog of a staged edit, after its staging check (the pending edits plus this one, exit
    ``code``, log ``output``): what the edit does, its warnings and the verdict. ``can_apply`` (Stage stages it)
    only when the planner and the build check passed. ``kind``: 'patch', 'clear', 'rename', 'stats', 'voice' or
    'icon' (``view``: its portrait view), 'equip' or 'equip_clear' (``gear_file``: the slot file) (default from
    ``patch`` / ``rename``; a voice edit's ``cid`` is its square's head)."""
    kind = kind or ('rename' if rename else 'patch' if patch else 'clear')
    rename = kind in VALUE_KINDS
    target = f'{name_of(state, cid)} ({hex_id(cid)})'
    gear_label = EQUIP_LABELS.get(gear_file, 'equipment').lower()
    dialog = SlotDialog({'rename': f'Rename {target}?', 'patch': f'Put a model into {target}?',
                         'equip': f'Put a {gear_label} into {target}?',
                         'equip_clear': f'Reset the {gear_label} of {target}?',
                         'stats': f'Other stats for {target}?',
                         'icon': f'New {view} portrait for {target}?',
                         'voice': f'Another voice for the square of {target}?',
                         COPY: f'Paste onto {target}?'}.get(kind, f'Clear {target}?'))
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
    section = _find_section(plan, cid, kind, view, gear_file)
    if section is None:                       # a clear of a slot at its baseline, or a rename that changes nothing
        skipped = next((s for s in plan['skipped'] if int(s['target'], 16) == cid and s['action'] == kind), None)
        lines += [(f'- {note}', TEXT) for note in (skipped or {}).get('notes', [])]
        earlier = None
        if pending is not None:
            earlier = (pending.icon_edit(cid, view) if kind == 'icon' else pending.value_edit(cid, kind) if rename
                       else pending.equip_edit(cid, gear_file) if kind in EQUIP_OPS else pending.model_edit(cid))
        if earlier is not None:
            lines.append((f'Stage drops the slot\'s pending {earlier["op"]}, so the slot stays as it is.', OK))
            dialog.can_apply = True
        else:
            dialog.title = {'rename': f'{target}: nothing to rename', 'clear': f'{target}: nothing to clear',
                            'equip_clear': f'{target}: nothing to reset'}.get(kind, f'{target}: nothing to change')
        return dialog
    lines += _section_lines(state, section, build_sizes(output))
    prefix = f'{hex_id(cid)}: '
    lines += [(f'- {note.removeprefix(prefix)}', TEXT) for note in plan['notes'] if note.startswith(prefix)]
    if not section['rebuild']:
        lines.append(('- no roster rebuild needed', TEXT))
    if code != 0:
        lines.append((f'Build check failed: {error or f"exit code {code}"}. Nothing was staged.', ERROR))
        return dialog
    checks = ('slot rules, and every copied block validated' if kind == COPY
              else 'slot rules, and the model built and validated' if kind == 'equip'
              else 'slot rules, and every model built and validated' if patch and not rename else 'slot rules')
    if section.get('stat_edits_kept'):
        if any(e['op'] == STAT_RESET and int(e['id'], 16) == cid for e in plan.get('merged') or []):
            lines.append(('Its stat edits are cleared too (a pending edit).', TEXT))
        else:
            lines.append(('"Stage + clear stat edits" also clears them, so the slot plays with exactly the new '
                          'stats.', TEXT))
            dialog.offer_stat_reset = True
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
        what = {'patch': 'put a model in', 'rename': 'rename'}.get(section['action'], 'clear')
        if section['action'] in EQUIP_OPS:
            gear = section.get('gear') or {}
            label = str(gear.get('label') or 'equipment').lower()
            what = (f'put a {label} in' if section['action'] == 'equip' else f'reset the {label}')
            if gear.get('origin') == 'bundled':
                what += ' (bundled with the model)'
        elif section['action'] == 'rename':
            text = (section.get('edit') or {}).get('text')
            what = f'rename to {text!r}' if text else 'reset the name'
        elif section['action'] in ('stats', 'voice'):
            effect = ((section.get('effects') or {}).get(section['action']) or 'default').removesuffix(' (square voice)')
            what = f'stats of {effect}' if section['action'] == 'stats' else f'square voice of {effect}'
        elif section['action'] == 'icon':
            what = (section.get('effects') or {}).get('portrait_note') or 'new portrait'
        elif section['action'] == STAT_EDITS:
            what = (section.get('effects') or {}).get(STAT_EDITS) or 'changed values'
        elif section['action'] == STAT_RESET:
            what = 'stat edits ' + ((section.get('effects') or {}).get(STAT_EDITS) or 'cleared')
        lines.append((f'{edit_who(state, cid)}: {what}', OK))
        lines += [('    ' + text, kind) for text, kind in _section_lines(state, section, sizes, warnings=False)]
    for section in plan['skipped']:
        cid = int(section['target'], 16)
        why = {'rename': 'nothing to rename (named that already)', 'clear': 'nothing to clear (at its baseline '
               'already)', STAT_RESET: 'no stat edits to clear'}.get(section['action'], 'nothing to change')
        lines.append((f'{edit_who(state, cid)}: {why}', TEXT))
    lines += [(f'- {note}', TEXT) for note in plan['notes']]
    lines += [(f'Warning: {warning}', WARN) for warning in plan['warnings']]
    lines.append(('- one roster rebuild' if plan['rebuild'] else '- no roster rebuild needed', TEXT))
    if code != 0:
        lines.append((f'Build check failed: {error or f"exit code {code}"}. Nothing was written.', ERROR))
        return dialog
    if not plan['edits']:
        lines.append(('Nothing to write.', TEXT))
        return dialog
    lines.append(('Checks passed: slot rules, every model built and validated'
                  + (', every stat edit file fits the game' if any(s['action'] == STAT_EDITS for s in plan['edits'])
                     else '') + '. Patch Game writes them now.', OK))
    dialog.can_apply = True
    return dialog


# --------------------------------------------------------------------------
# Stat edits (the Stat Editor tab, StatEditor/gui_tab.py)
# --------------------------------------------------------------------------

def stat_edit(path: str) -> dict:
    """The pending edit of a stat edit file the Stat Editor sent (``Roster/slot_plan``'s ``stat_edits`` op)."""
    return {'op': STAT_EDITS, 'id': hex_id(GAME_WIDE), 'file': path}


def staged_stat_files(pending: 'PendingEdits') -> list[str]:
    """The pending stat edit files, in staging order."""
    return [e['file'] for e in pending.edits if e['op'] == STAT_EDITS and e.get('file')]


_STAT_IDS: dict[tuple[str, float], frozenset[int]] = {}


def stat_file_ids(path: str | None) -> frozenset[int]:
    """The character IDs a stat edit file names (its ``characters`` keys; chemistry partners not counted), empty
    when unreadable. Cached per path and modification time (the grid asks on every redraw)."""
    try:
        key = (os.path.normcase(os.path.abspath(path)), os.path.getmtime(path))
    except (OSError, TypeError, ValueError):
        return frozenset()
    if key not in _STAT_IDS:
        ids = set()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                doc = json.load(f)
            for text in (doc.get('characters') or {}) if isinstance(doc, dict) else ():
                try:                           # StatEditor/apply._id's rules: "0xNN", 0x00-0xFF
                    cid = int(text, 16) if text.lower().startswith('0x') else -1
                except (AttributeError, ValueError):
                    cid = -1
                if 0 <= cid <= 0xFF:
                    ids.add(cid)
        except (OSError, ValueError, AttributeError):
            pass
        _STAT_IDS[key] = frozenset(ids)
    return _STAT_IDS[key]


def _same_file(a: str | None, b: str) -> bool:
    return bool(a) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def stat_edits_dialog(state: dict | None, plan: dict | None, code: int, output: str, path: str) -> SlotDialog:
    """The confirm dialog of a stat edit file after its staging check (the pending edits plus this one): what it
    changes per character and table, its warnings and the verdict. ``can_apply`` (Stage stages it) only when the
    whole check passed and the file changes something."""
    dialog = SlotDialog('Stage the Stat Editor\'s values?')
    lines = dialog.lines
    error = error_message(output)
    if plan is None or plan['refused']:
        dialog.title = 'Stat edits: refused'
        if plan is None:
            error = error.removeprefix('refused, nothing written: ')
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            lines += _refused_lines(state or {}, plan, GAME_WIDE)
            if any(not _same_file((r.get('edit') or {}).get('file'), path) for r in plan['refused']):
                lines.append(('A pending edit no longer fits the game files: discard it first.', TEXT))
        lines.append(('Nothing was staged.', TEXT))
        return dialog
    section = next((s for s in plan['edits'] if s['action'] == STAT_EDITS
                    and _same_file((s.get('edit') or {}).get('file'), path)), None)
    if section is None:
        dialog.title = 'Stat edits: nothing to change'
        skipped = next((s for s in plan['skipped'] if s['action'] == STAT_EDITS
                        and _same_file((s.get('edit') or {}).get('file'), path)), None)
        lines += [(f'- {note}', TEXT) for note in (skipped or {}).get('notes', [])]
        lines.append(('The Stat Editor sent no value the game does not hold already. Nothing was staged.', TEXT))
        return dialog
    lines.append((f'Changes: {(section.get("effects") or {}).get(STAT_EDITS) or "changed values"}', OK))
    lines += [(f'- {note}', TEXT) for note in section['notes']]
    lines += [(f'Warning: {warning}', WARN) for warning in section['warnings']]
    if code != 0:
        lines.append((f'Check failed: {error or f"exit code {code}"}. Nothing was staged.', ERROR))
        return dialog
    lines.append(('Checks passed: made from this main.dol, known characters and fields, every value fits. Nothing '
                  'written yet: Stage adds the values to the pending list, "Patch Game" (Character grid tab) writes '
                  'them.', OK))
    dialog.can_apply = True
    return dialog


# --------------------------------------------------------------------------
# Roster packs
# --------------------------------------------------------------------------

# the fingerprint fields (Roster/pack.py FIELDS), as the dialogs and markers name them
PACK_FIELDS = {'high': 'High model', 'low': 'Low model', 'bat': 'bat', 'glove_l': 'left glove',
               'glove_r': 'right glove', 'extra': 'extra bat', 'model': 'model directory', 'front': 'front portrait',
               'side': 'side portrait', 'name': 'name', 'stats': 'stats', 'voice': 'square voice', 'square': 'square'}
EQUIP_FIELDS = ('bat', 'glove_l', 'glove_r', 'extra')    # absent from the fingerprints of format 1 packs
DIFF_TEXT = {'game': 'only in the game (leaves the grid)', 'pack': 'only in the pack (comes onto the grid)'}


def save_command(path: str) -> tuple:
    return ('--save-roster', path)


def load_command(path: str, dry_run: bool = False) -> tuple:
    return ('--load-roster', path) + (('--dry-run',) if dry_run else ())


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
                 'stats': 'stats source', 'voice': 'square voice', 'stat_edits': 'stat edits'}


def switch_command(path: str, dry_run: bool = False, fresh: bool = False) -> tuple:
    """``start.py --roster --config``: the switch chain (``fresh``: no slot customisations kept)."""
    return ('--roster', '--config', path) + (('--fresh',) if fresh else ()) + (('--dry-run',) if dry_run else ())


def switch_plan(path: str) -> dict | None:
    return load_pack_plan(path, 'switch_roster')


def switch_dialog(plan: dict | None, code: int, output: str, path: str) -> SlotDialog:
    """The dialog of "Inject roster", after ``--roster --config FILE --dry-run``: which slots keep their
    customisations, which are reset or dropped, the verdict. ``can_apply`` only when the plan and the in-memory
    build passed."""
    name = os.path.basename(path)
    dialog = SlotDialog(f'Inject the roster {name}?')
    lines = dialog.lines
    if plan is None or code != 0:
        dialog.title = f'{name}: refused'
        error = error_message(output).removeprefix('refused, nothing written: ')
        lines.append((f'Refused: {error or f"the check failed (exit code {code})"}', ERROR))
        lines.append(('Nothing was written.', TEXT))
        return dialog
    names = plan.get('names') or {}

    def rows(key, title, kind, empty_too=True):
        items = [r for r in plan.get(key) or [] if empty_too or r['fields']]
        if not items:
            return
        lines.append((title, TEXT))
        for r in items:
            who = f'{names[r["id"]]} ({r["id"]})' if names.get(r['id']) else r['id']
            what = ', '.join(SWITCH_FIELDS.get(f, f) for f in r['fields'])
            lines.append((f'  {who}' + (f': {what}' if what else ''), kind))
    rows('carried', 'Kept (on both grids; where they sit comes from the preset):', OK)
    rows('reset', 'Reset (leaving the grid, back to their baseline):', WARN)
    rows('dropped', 'Dropped (new IDs leaving the roster) with their customisations:', WARN, empty_too=False)
    lines += [(f'- {note}', TEXT) for note in plan.get('notes') or []]
    lines += [(f'Warning: {w}', WARN) for w in plan.get('warnings') or []]
    lines.append(('- Pending grid edits are dropped. Stat edits travel with their characters.', TEXT))
    lines.append(('Checks passed: the merged roster builds. Inject writes it into 3_Output_Dat.', OK))
    dialog.can_apply = True
    return dialog


@dataclass
class Reference:
    """The roster pack last saved or loaded in this session (GUI memory only): slots that differ from it get the
    "changed since" marker."""
    label: str                                  # 'saved X.sluggiesroster' / 'loaded X.sluggiesroster'
    fingerprints: dict

    def changed(self, state: dict, cid: int) -> list[str]:
        """What differs between the slot now and the pack (field names); ``[]`` when nothing does."""
        now = (characters(state).get(cid) or {}).get('fingerprint')
        then = self.fingerprints.get(hex_id(cid))
        if now is None:
            return []
        if then is None:
            return ['not in the pack']
        return [label for f, label in PACK_FIELDS.items()
                if now.get(f) != then.get(f) and not (f in EQUIP_FIELDS and f not in then)]

    def square_changed(self, state: dict, index: int) -> bool:
        return any(self.changed(state, m) for m in state['squares'][index]['members'])

    def count(self, state: dict) -> int:
        return sum(bool(self.changed(state, c['id'])) for c in state['characters'])

    def line(self, state: dict | None) -> str:
        if state is None:
            return f'Reference: the roster pack you {self.label}.'
        n = self.count(state)
        return (f'Reference: the roster pack you {self.label}; '
                + (f'{n} slot{"s" if n != 1 else ""} changed since (blue border).' if n else 'no slot changed since.'))


def _pack_name(state: dict | None, plan: dict, cid: int) -> str:
    if state is not None and cid in characters(state):
        return f'{name_of(state, cid)} ({hex_id(cid)})'
    name = ((plan.get('pack_fingerprints') or {}).get(hex_id(cid)) or {}).get('name') or {}
    return f'{name.get("en") or "?"} ({hex_id(cid)})'


def load_dialog(state: dict | None, plan: dict | None, code: int, output: str, path: str,
                differing_only: bool = True, writing: bool = False) -> SlotDialog:
    """The dialog of "Load roster...", after ``--load-roster FILE --dry-run``: the per-slot diff (only the
    differing slots with ``differing_only``), what the load does, the verdict. ``can_apply`` (Stage adds the load
    to the pending list; with ``writing``, Patch Game's summary, Patch Game writes it) only when the pack passed
    every check and there is something to load."""
    name = os.path.basename(path)
    dialog = SlotDialog(f'Patch Game: load the roster pack {name}?' if writing else f'Load the roster pack {name}?')
    lines = dialog.lines
    error = error_message(output)
    if plan is None or plan.get('refused') or code != 0:
        dialog.title = f'{name}: refused'
        if plan is None or not plan.get('refused'):
            error = error.removeprefix('refused, nothing written: ')
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            for refused in plan['refused']:
                lines.append((f'Refused: {_pack_name(state, plan, int(refused["id"], 16))}: {refused["error"]}',
                              ERROR))
        lines.append(('Nothing was written.', TEXT))
        return dialog
    diff = plan.get('diff') or []
    counts = {k: sum(d['status'] == k for d in diff) for k in ('same', 'differs', 'game', 'pack')}
    meta = plan.get('pack_meta') or {}
    blocks = sum(len(r) for r in (meta.get('blocks') or {}).values())
    lines.append((f'Pack: {name} ({meta.get("slots", "?")} slots, {blocks} model blocks)', TEXT))
    lines.append((f'{counts["same"]} slots are the same, {counts["differs"]} differ, {counts["game"]} only in the '
                  f'game, {counts["pack"]} only in the pack.', TEXT))
    for d in diff:
        if d['status'] == 'same' and differing_only:
            continue
        if d['status'] == 'differs':
            what = 'differs: ' + ', '.join(PACK_FIELDS.get(f, f) for f in d['fields'])
        else:
            what = DIFF_TEXT.get(d['status'], 'the same')
        lines.append((f'  {_pack_name(state, plan, int(d["id"], 16))}: {what}',
                      TEXT if d['status'] == 'same' else WARN))
    lines += [(f'- {note}', TEXT) for note in plan.get('notes') or []]
    lines += [(f'Warning: {w}', WARN) for w in plan.get('warnings') or []]
    if not plan.get('commands'):
        dialog.title = f'{name}: nothing to load'
        lines.append(('The game holds this roster already.', OK))
        return dialog
    what = ['one roster rebuild' if plan.get('rebuild') else 'no roster rebuild']
    if plan.get('clears'):
        what.append(f'{len(plan["clears"])} stock slot(s) back to vanilla models first')
    if plan.get('writes'):
        what.append(f'{len(plan["writes"])} slot(s) get the pack\'s model blocks')
    lines.append(('- ' + '; '.join(what), TEXT))
    if writing:
        lines.append(('Checks passed: every model block of the pack. Patch Game now replaces the whole roster of '
                      '3_Output_Dat with the pack.', OK))
    else:
        lines.append(('Checks passed: every model block of the pack. Nothing written yet: Stage adds the load to '
                      'the pending list, "Patch Game" writes it (it replaces the whole roster).', OK))
    dialog.can_apply = True
    return dialog


def save_pending_dialog(state: dict | None, pending: PendingEdits) -> SlotDialog:
    """Asked before "Save roster..." while edits are pending: the pack saves the game as it is."""
    count = len(pending)
    lines = [(f'{count} pending edit{"s" if count != 1 else ""} (not written to the game yet):', TEXT)]
    for cid, text in pending.titles():
        who = '' if cid is None else f'{name_of(state, int(cid, 16)) if state else cid} ({cid}): '
        lines.append((f'  {who}{text}', WARN))
    lines.append(('A roster pack saves the game as it is, so they are not in it. To include them, Cancel and run '
                  '"Patch Game" first.', TEXT))
    return SlotDialog('Save the roster without the pending edits?', lines, True)


