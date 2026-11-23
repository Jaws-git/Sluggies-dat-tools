"""The roster state as the grid shows it (no Dear PyGui): ``StateLoader`` (the background
``start.py --roster-state`` read and its result), character names and the name-plate check, the Stats and
Voice choices and their text, where each portrait crop is (``icon_file``) and whether it is the slot's own
(``icon_note``), square labels and tooltips, and ``GridNav`` (the pop-out levels and what a click or ``Esc``
does).

The state is the dict ``Roster/state.py`` writes (``_gui/roster_state.json``). Reached through ``gui_grid``.
"""

import json
import os
from typing import TYPE_CHECKING

if __package__:
    from .grid_common import (
        LUIGI, UNNAMED, hex_id,
    )
else:
    from grid_common import (
        LUIGI, UNNAMED, hex_id,
    )

if TYPE_CHECKING:                      # annotations only: grid_pending imports this module
    from grid_pending import PendingEdits


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------

# The files ``start.py --roster-state`` reads (relative to the project root): while none of them changed, a
# read gives the state the GUI already has.
STATE_SOURCES = (
    os.path.join('3_Output_Dat', 'main.dol'),
    os.path.join('3_Output_Dat', 'dt_na.dat'),
    os.path.join('3_Output_Dat', 'sound_NA', 'MY2.brsar'),
    os.path.join('1_Input', 'main.dol'),
)


def state_signature(root_dir: str) -> tuple:
    """``(size, mtime)`` of every file the grid state is read from (None for a missing one)."""
    signature = []
    for rel in STATE_SOURCES:
        try:
            info = os.stat(os.path.join(root_dir, rel))
            signature.append((info.st_size, info.st_mtime_ns))
        except OSError:
            signature.append(None)
    return tuple(signature)


STATE_VERSION = 1                # Roster/state.VERSION (test-pinned): the form of roster_state.json read here
_STATE_KEYS = ('kind', 'shape', 'cells', 'squares', 'characters')


def state_problem(state) -> str | None:
    """Why a loaded ``roster_state.json`` cannot be used, or None. Checked once where the file is read: the
    tabs take its keys for granted, and a file of another form (another version of the tools, a hand edit)
    used to fail somewhere in a redraw."""
    if not isinstance(state, dict):
        return 'it is not a roster state (not a JSON object)'
    if state.get('version') != STATE_VERSION:
        return (f'it is roster state version {state.get("version")!r}, this window reads version {STATE_VERSION} '
                '(written by another version of the tools?)')
    missing = [key for key in _STATE_KEYS if key not in state]
    if missing:
        return f'it lacks {", ".join(missing)}'
    shape = state['shape']
    if not (isinstance(shape, list) and len(shape) == 2) or not isinstance(state['squares'], list) \
            or not isinstance(state['characters'], list):
        return 'its grid fields have an unexpected form'
    return None


class StateLoader:
    """One background read at a time; a refresh asked for while one runs is queued (run once afterwards)."""

    IDLE, RUNNING, DONE, FAILED = 'idle', 'running', 'done', 'failed'

    def __init__(self, state_path: str):
        self.state_path = state_path
        self.status = self.IDLE
        self.state = None
        self.error = ''
        self.queued = False
        self.signature = None                # state_signature() the loaded state was read from
        self._started_with = None

    def start(self, signature: tuple | None = None) -> bool:
        """True when the caller should launch a read now; False when one runs (the refresh is queued).
        ``signature``: ``state_signature()`` taken now, before the read starts."""
        if self.status == self.RUNNING:
            self.queued = True
            return False
        self.status = self.RUNNING
        self.error = ''
        self._started_with = signature
        return True

    def current(self, signature: tuple) -> bool:
        """Whether the loaded state was read from files with this ``state_signature()``: a read would only
        repeat it."""
        return self.status == self.DONE and self.signature is not None and self.signature == signature

    def finish(self, code: int, output: str = '') -> bool:
        """Take a read's exit code and output. Returns True when a queued refresh should start next."""
        if code == 0:
            try:
                with open(self.state_path, encoding='utf-8') as f:
                    state = json.load(f)
                problem = state_problem(state)
                if problem:
                    raise ValueError(problem)
                self.state = state
                self.status = self.DONE
            except (OSError, ValueError) as exc:
                self.status, self.error = self.FAILED, f'could not load {self.state_path}: {exc}'
        else:
            self.status, self.error = self.FAILED, error_message(output) or f'exit code {code}'
        self.signature = self._started_with if self.status == self.DONE else None
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

_CHAR_INDEX: list = [None, -1, {}]     # [characters list, its length, {id: entry}]


def characters(state: dict) -> dict[int, dict]:
    """{id: entry} of the state's characters, built once per loaded list.

    Called several times per cell and tooltip line. The index is rebuilt when
    the state brings another list or the list changed length; entries edited
    in place stay valid because the index holds the same dicts."""
    chars = state['characters']
    cached = _CHAR_INDEX
    if cached[0] is not chars or cached[1] != len(chars):
        cached[:] = [chars, len(chars), {c['id']: c for c in chars}]
    return cached[2]


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


# Character voices (Roster/char_voices.py): a slot's own voice, a stock voice and/or a custom voice pack
CHAR_VOICE = 'char_voice'                     # the edit op (Roster/slot_plan.CHAR_VOICE, test-pinned)
SPECIES_CLIP = 'species_clip'                 # one moment of a stock voice (Roster/slot_plan.SPECIES_CLIP)
SPECIES_CLIPS = 'species_clips'               # its effects key
CHAR_VOICE_DEFAULT = 'Default: its square\'s voice'


def char_voice_choices(state: dict, cid: int, packs=()) -> list[tuple[str, str | None, str | None]]:
    """``(label, source, pack)`` for the slot's Voice dialog: the default first (its square's voice), every stock
    square's head (one voice per species), then every custom voice pack (``packs``: ``[(name, base voice ID or None,
    clip count)]``) as "pack NAME (N custom clips on VOICE's voice)"."""
    heads = sorted({s['head'] for s in state['squares'] if s['kind'] == 'stock'})
    out = [(CHAR_VOICE_DEFAULT, None, None)]
    out += [(_labelled(state, h), hex_id(h), None) for h in heads]
    out += [(_pack_label(state, name, base, count), None, name) for name, base, count in packs]
    return out


def _pack_label(state: dict, name: str, base: int | None, count: int) -> str:
    rest = f'{known_name(state, base)}\'s voice' if base is not None else 'its square\'s voice'
    return f'Pack {name} ({count} custom clip{"s" if count != 1 else ""}, the rest {rest})'


SQUARE_PACK = 'square_pack'                   # a square Voice dialog value: ``(SQUARE_PACK, pack name)``


def square_pack_choices(state: dict, packs=()) -> list[tuple[str, tuple[str, str]]]:
    """``(label, (SQUARE_PACK, name))`` for the square's Voice dialog, after ``voice_choices``: a pack is staged as
    every member's own voice (``square_pack_edits``); the square voice itself holds a stock voice only."""
    return [(_pack_label(state, name, base, count) + ', as every member\'s own voice', (SQUARE_PACK, name))
            for name, base, count in packs]


def square_pack_edits(state: dict, index: int, pack: str) -> list[dict]:
    """One ``char_voice`` edit per member of square ``index`` that gives it the voice pack ``pack``."""
    return [char_voice_edit(cid, None, pack) for cid in state['squares'][index]['members']]


def char_voice_text(state: dict, cid: int) -> str:
    """The slot level's effective-voice line: ``Voice: Bowser (own)``, ``Voice: Mario (square)`` or
    ``Voice: pack Rosalina (own, 5 custom clips on Bowser's voice)``."""
    c = characters(state).get(cid) or {}
    if 'voice' not in c:
        return 'Voice: not read'
    clips = c.get('char_clips') or c.get('char_clip_ids') or {}
    if c.get('char_voice') is None and not clips:
        return f'Voice: {known_name(state, c["voice"])} (square)'
    if not clips:
        return f'Voice: {known_name(state, c["voice"])} (own)'
    n = len(clips)
    base = (f'{known_name(state, c["char_voice"])}\'s voice' if c.get('char_voice') is not None
            else 'its square\'s voice')
    label = f'pack {c["char_pack"]}' if c.get('char_pack') else 'custom clips'
    return f'Voice: {label} (own, {n} custom clip{"s" if n != 1 else ""} on {base})'


def char_voice_edit(cid: int, source: str | None, pack: str | None) -> dict:
    out = {'op': CHAR_VOICE, 'id': hex_id(cid), 'source': source}
    if pack:
        out['pack'] = pack
    return out


def species_clip_edit(head: int, moment: str, file: str | None, origin: str | None = None) -> dict:
    """The pending edit that replaces ``moment`` of ``head``'s species voice (``file``: the staged clip, None: the
    stock clip back; ``origin``: the user's WAV, for the texts)."""
    out = {'op': SPECIES_CLIP, 'id': hex_id(head), 'moment': moment, 'file': file}
    if origin:
        out['origin'] = origin
    return out


def char_voice_current(state: dict, cid: int, pending: 'PendingEdits | None') -> tuple[str | None, str | None]:
    """``(source, pack)`` the Voice dialog starts on: the pending edit's, else what the game holds."""
    edit = pending.value_edit(cid, CHAR_VOICE) if pending is not None else None
    if edit is not None:
        return edit.get('source'), edit.get('pack')
    c = characters(state).get(cid) or {}
    if c.get('char_pack'):
        return None, c['char_pack']
    return (hex_id(c['char_voice']) if c.get('char_voice') is not None else None), None


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
SIDE_ALT = 'side_alt'                    # the alternate side view (Roster/icons.ALT_VIEW): facing the other way
VIEW_LABELS = {FRONT: 'front', SIDE: 'side', SIDE_ALT: 'alternate side'}
HAS_ALT_TEXT = 'Has alternate side icon'
SET_ALT_TEXT = '[Set Side Alt Icon]'


def view_label(view: str) -> str:
    return VIEW_LABELS.get(view, view)


def has_alt_icon(state: dict, cid: int) -> bool:
    """Whether the slot shows an alternate side portrait of its own (a cell distinct from the side and front
    ones; most characters mirror their side cell instead)."""
    try:
        from Roster import state_icons
    except ImportError:
        from SluggiesTools.Roster import state_icons
    return state_icons.has_alternate((characters(state).get(cid) or {}).get('icon'))


def icon_ref(state: dict, cid: int, view: str) -> dict | None:
    """Where the game takes ``cid``'s ``view`` portrait from (``Roster/state_icons.resolve``), or None."""
    return ((characters(state).get(cid) or {}).get('icon') or {}).get(view)


def icon_file(state: dict, state_path: str, cid: int, view: str, check: bool = True) -> str | None:
    """The crop PNG of a portrait (``_gui/icons/...``), or None when there is none on disk. ``check=False``:
    the path the state names, not looked up on disk (for a caller that opens or stats it anyway)."""
    ref = icon_ref(state, cid, view)
    if not ref or not ref.get('file'):
        return None
    path = os.path.join(os.path.dirname(state_path), state.get('icon_dir') or 'icons', ref['file'])
    return path if not check or os.path.isfile(path) else None


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


def animations_text(char: dict) -> str | None:
    """The slot summary's animations line: stock, or which banks (files 6-14) are custom; None without a read."""
    banks = char.get('animations')
    if not banks:
        return None
    custom = [e['file'] for e in banks if e.get('vanilla') is False]
    if custom:
        return f'Animations: custom (bank{"s" if len(custom) != 1 else ""} {", ".join(map(str, custom))})'
    if any(e.get('vanilla') is None for e in banks):
        return 'Animations: unknown (1_Input could not be read)'
    return 'Animations: stock'


# --------------------------------------------------------------------------
# Portrait replacement
# --------------------------------------------------------------------------

MII_START, NEW_START = 0x4D, 0x66    # Mii IDs have no portrait records (Roster/slot_plan.has_portrait_records)
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tga', '.webp')   # the picker's filter only
IMAGE_PATTERNS = ';'.join('*' + ext for ext in IMAGE_EXTENSIONS)
IMAGE_FILTERS = [('Images', IMAGE_PATTERNS), ('All files', '*.*')]
FIT_CHOICES = (('contain', 'Fit inside'), ('cover', 'Fill and crop'), ('strict', 'Exact 48x51'))


def can_replace_portrait(cid: int) -> bool:
    return cid < MII_START or cid >= NEW_START


def portrait_tip(cid: int, view: str) -> str:
    label = view_label(view)
    if not can_replace_portrait(cid):
        return f'{label.capitalize()} portrait (Miis show the Mii icon; it cannot be replaced)'
    if view == SIDE_ALT:
        return ('Click to pick an alternate side portrait: the side view facing the other way. Without one the '
                'game mirrors the side portrait; characters with asymmetric art (Luigi\'s "L", Waluigi\'s "Γ") '
                'carry a second drawing instead. "Clear slot" drops it again where the character had none.')
    return f'Click to replace the {label} portrait'


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
