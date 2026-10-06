"""Patch a slot / clear a slot: the apply chain as a list of commands.

A batch of staged edits is one chain of ``start.py`` commands, run in order
and stopped at the first failure (``start.py --apply-slots``; ``--patch-slot``
/ ``--clear-slot`` are batches of one). ``plan_batch`` plans each edit on the
config the previous one left and merges the commands (one build check per
patched slot, at most one roster rebuild, the slot commands in staging order,
one read); ``merge_edits`` holds the per-slot rules (one model edit per slot,
a Low pick joins a pending High pick, a clear drops earlier renames). One
edit's chain:

1. ``--patch FILES --target-id 0xNN --validate-only``: build and validate
   every block first, so a file that does not build stops the chain before
   anything is written;
2. ``--roster --state <derived>``: read -> rebuild with the one change applied
   to the derived config (``derive.py``), when the change needs the roster
   (own model directory, stats, square voice, name, portraits);
3. ``--patch FILES --target-id 0xNN [--as-low]`` (HP first), or for a cleared
   stock slot ``--unpatch --target-id 0xNN`` (both models back to vanilla);
4. ``--roster-state``: a fresh read for the GUI.

``plan_patch`` / ``plan_clear`` decide everything from the read state, the
derived config and a few file checks (``Env``); a refused change raises
``PlanError`` and gives no commands, so nothing is written.

Patch:

* **New ID:** the slot keeps its own model directory when it already holds
  copies of the source character's files (its earlier patches stay), else it
  gets a fresh copy of the source's 15 files (``ids[].model.from``). On a new
  square it takes the source's stats, and the square's voice becomes the
  source's when none is set yet. An open slot ("Empty slot", or no name) is
  named after the source.
* **Stock ID:** model only (stats, voice and name stay). The source's
  vanilla skeleton must match the slot's (same check as ``SlotTarget``).
* **HP / ``L_``:** a picked file brings its partner from the sibling folder
  (same chunk, same geo-name stem). An HP without ``L_`` partner is used as
  the low-poly model too (``--as-low``, warned). An ``L_`` without HP partner
  is refused on a new ID, and on a stock ID allowed only when the slot's
  current high-poly model is its partner (``L_`` binds textures by index into
  its HP's TEX).
* **Portraits:** the source's exported ``icon/SideIcon.png`` +
  ``FrontIcon.png`` (``model_icons``); when one is missing the slot keeps its
  portraits and the plan says so.

Clear: a stock slot gets its vanilla HP and ``L_`` back and loses
replaced portraits (``stock_icons``) and another stats source
(``stock_stats``); a new ID gets a fresh copy of its template's files, the
template's stats, the "Empty slot" name and portraits. The square voice
stays.

Stats and voice (``plan_stats`` / ``plan_voice``): any slot can
play with another stock player's stats (new IDs: ``ids[].stats``; stock IDs:
``stock_stats``), and any square can speak with another square's voice (new
squares: ``grid.squares[k].voice``, which reaches only the square-only new
IDs on it; stock squares: ``stock_voices``, which reaches the whole species:
its wheel, spare rows and new IDs on it). A voice is named by a character;
the square takes its family's voice (the base character of its species).

Portraits (``plan_icon``): one view (front or side) of a slot
takes a user's image, fitted to 48x51 (``icon_import``). The other view keeps
what the slot has: its own cell (by name, so its CMPR blocks stay), its
model's portrait, or, without own portraits, what the game shows now (the
template's or neighbour's crop; a stock ID's stock art), which then becomes
the slot's own. Miis have no portrait records and are refused.

Equipment (``plan_equip`` / ``plan_equip_clear``): each slot's bat (file 2), left glove (3), right
glove (4) and extra bat (5) can be replaced or reset on their own. The edit names a ``.sluggie`` exported from a
character's equipment folder and the slot file it goes to (default: the file it came from; a bat also fits
file 5, a glove only its own hand, ``gear.target_file``). It is one targeted patch of that route only: the
vanilla bats and gloves other characters share stay as they are. A model patch into a new ID also stages the
gear that belongs to the model (``gear.find_gear``, edits with ``origin`` ``bundled``).

Stat edits (``plan_stat_edits``): an edit file the Sluggers Stat Editor sent back from Bridge Mode
(``StatEditor/apply.py``). It belongs to no slot (its ``id`` is ``GAME_WIDE``) and is valid only for the
``main.dol`` it was made from, so the chain writes every staged stat edit file in one ``--apply-stat-edits``
before the roster rebuild; the rebuild then carries the values onto the new layout like any other stat edit
(``StatEditor/carry.py``).

Copy (``plan_copy``, op ``copy``, the grid's paste): slot ``id`` becomes a clone of ``source`` as the game holds
it (finished blocks, name, portraits, stats source and every stat value; the square's voice stays unless the slot is
alone on a new square). The planner snapshots the source into ``copy_dir`` before anything is written, so pending
edits of the source are never copied; a paste drops the slot's earlier pending edits.

Stat edits and slot edits: the rebuild carries a slot's stat edits by character ID, so they stay on top of a changed
stats source (``plan_stats``, a patch that sets a new square's stats) and through a clear; those plans say so
(``Plan.stat_edits_kept``). ``plan_stat_reset`` (op ``stat_reset``) clears them: a ``reset:0xNN`` item in the same
``--apply-stat-edits`` step, at its place in the staging order (a later stat edit file sets values on top of it).
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field

try:
    from . import gear, icon_art, icon_import, icons, ids, model_icons, names, open_slot, voices, wheels
except ImportError:
    import gear
    import icon_art
    import icon_import
    import icons
    import ids
    import model_icons
    import names
    import open_slot
    import voices
    import wheels

HIGH_FILE, LOW_FILE = 0, 1
CHARACTER_DIRS = range(ids.MODEL_DIR_BASE, ids.MODEL_DIR_BASE + ids.STOCK_IDS)


class PlanError(ValueError):
    pass


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


# --------------------------------------------------------------------------
# The picked .sluggie and its partner
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Pair:
    """A picked ``.sluggie`` and its HP/``L_`` partner (paths; None when missing)."""
    chunk: int
    high: str | None
    low: str | None
    picked: str
    stem: str                       # geo-name stem shared by both models (``luigi``)

    @property
    def source(self) -> int:
        """The character ID the models were exported from."""
        return self.chunk - ids.MODEL_DIR_BASE

    @property
    def files(self) -> list[str]:
        """The files to patch, high-poly first."""
        return [p for p in (self.high, self.low) if p]


def _load_model(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f).get('SluggiesModel') or {}
    except (OSError, ValueError) as exc:
        raise PlanError(f'could not read {path}: {exc}') from exc


def _role(model: dict, folder: str) -> tuple[int | None, bool, str]:
    """``(chunk, is_low, stem)`` of an exported model."""
    geo = (model.get('ACTHeader') or {}).get('GeoName')
    if not geo:
        m = model_icons.MODEL_FOLDER.match(folder)
        geo = (('L_' if m.group('low') else '') + m.group('geo')) if m else folder
    is_low = model.get('FileIndex') == LOW_FILE or geo.lower().startswith('l_')
    stem = geo[2:] if geo.lower().startswith('l_') else geo
    return model.get('ChunkNumber'), is_low, model_icons._stem(stem)


def _partner(path: str, chunk: int, is_low: bool, stem: str) -> str | None:
    """The other model's ``.sluggie`` in a sibling folder (same chunk, same stem, the other role)."""
    folder = os.path.dirname(os.path.abspath(path))
    parent = os.path.dirname(folder)
    try:
        siblings = sorted(os.listdir(parent))
    except OSError:
        return None
    for sibling in siblings:
        m = model_icons.MODEL_FOLDER.match(sibling)
        sibling_dir = os.path.join(parent, sibling)
        if (m is None or bool(m.group('low')) == is_low or model_icons._stem(m.group('geo')) != stem
                or not os.path.isdir(sibling_dir) or os.path.normcase(sibling_dir) == os.path.normcase(folder)):
            continue
        for name in sorted(os.listdir(sibling_dir)):
            if not name.lower().endswith('.sluggie'):
                continue
            candidate = os.path.join(sibling_dir, name)
            other = _load_model(candidate)
            if other.get('ChunkNumber') == chunk and other.get('FileIndex') == (HIGH_FILE if is_low else LOW_FILE):
                return candidate
    return None


def classify(path: str) -> Pair:
    """The picked file's role and partner; refuses anything that cannot go into a slot."""
    path = os.path.abspath(path)
    if not path.lower().endswith('.sluggie') or not os.path.isfile(path):
        raise PlanError(f'{path} is not a .sluggie file')
    model = _load_model(path)
    chunk, is_low, stem = _role(model, os.path.basename(os.path.dirname(path)))
    if chunk not in CHARACTER_DIRS:
        raise PlanError(f'{os.path.basename(path)} comes from chunk {chunk}, not a character directory '
                        f'({CHARACTER_DIRS.start}-{CHARACTER_DIRS.stop - 1}): stadiums, props and bats cannot go '
                        'into a slot')
    if model.get('FileIndex') in gear.ROLES:
        raise PlanError(f'{os.path.basename(path)} is a {gear.label(model["FileIndex"]).lower()} (file '
                        f'{model["FileIndex"]}), not a character model: use the Equipment row of the slot')
    if model.get('FileIndex') not in (HIGH_FILE, LOW_FILE):
        raise PlanError(f'{os.path.basename(path)} is file {model.get("FileIndex")} of its directory, not a '
                        'character model (only the High model, file 0, and its Low partner, file 1)')
    partner = _partner(path, chunk, is_low, stem)
    high, low = (partner, path) if is_low else (path, partner)
    return Pair(chunk, high, low, path, stem)


@dataclass(frozen=True)
class GearPick:
    """A picked equipment ``.sluggie``: where it was exported from."""
    chunk: int
    source_file: int                # its FileIndex (2-5)
    path: str

    @property
    def source(self) -> int:
        return self.chunk - ids.MODEL_DIR_BASE


def classify_gear(path: str) -> GearPick:
    """The picked equipment file's origin; refuses anything that is not a character's bat or glove."""
    path = os.path.abspath(path)
    if not path.lower().endswith('.sluggie') or not os.path.isfile(path):
        raise PlanError(f'{path} is not a .sluggie file')
    model = _load_model(path)
    chunk, file = model.get('ChunkNumber'), model.get('FileIndex')
    name = os.path.basename(path)
    if file in (HIGH_FILE, LOW_FILE):
        raise PlanError(f'{name} is a character model (file {file}), not a bat or glove: use the Select button of '
                        'the slot\'s model row')
    if file not in gear.ROLES:
        raise PlanError(f'{name} is file {file} of its directory, not equipment (2 bat, 3 left glove, 4 right '
                        'glove, 5 extra bat)')
    if chunk not in CHARACTER_DIRS:
        raise PlanError(f'{name} comes from chunk {chunk}, not a character directory '
                        f'({CHARACTER_DIRS.start}-{CHARACTER_DIRS.stop - 1}): only a character\'s own bats and '
                        'gloves can go into a slot')
    return GearPick(chunk, file, path)


# --------------------------------------------------------------------------
# File checks (the default reads the game files; tests pass their own)
# --------------------------------------------------------------------------

class Env:
    """The checks a plan needs beyond the state and the derived config."""

    def model_icons(self, path: str) -> model_icons.ModelIcons:
        return model_icons.find(path)

    def skeleton(self, source: tuple[int, int], target: tuple[int, int]) -> tuple[list[str], list[str]]:
        """(errors, warnings) for posing ``source``'s vanilla skeleton with ``target``'s animations."""
        raise NotImplementedError

    def current_high_stem(self, cid: int) -> str | None:
        """Geo-name stem of the high-poly model the slot loads now (output files)."""
        raise NotImplementedError

    def shows_portraits(self, char: dict, found: model_icons.ModelIcons) -> bool:
        """Whether the slot already shows exactly these portraits (then nothing is imported)."""
        return False

    def at_baseline(self, char: dict, config: dict) -> bool:
        """Whether the slot is at its baseline already (a clear would change nothing)."""
        return False

    def models_at_baseline(self, char: dict) -> bool:
        """Whether the slot's High and Low models are the vanilla blocks of its source (an unused character's split
        copies do not count: a clear repairs them)."""
        return False

    def shows_open_slot_portraits(self, char: dict) -> bool:
        """Whether the slot shows the "empty slot" portraits (``open_slot.SLOT_ICON``)."""
        return False

    def equipment_problems(self, source: tuple[int, int], target: tuple[int, int]) -> list[str]:
        """Errors for posing the bat/glove ``source`` (vanilla route) with the animations of ``target``'s vanilla
        file (an empty one has none to compare: no errors)."""
        return []

    def equipment_at_baseline(self, char: dict, file: int) -> bool:
        """Whether the slot's equipment ``file`` is at its baseline already (a reset would change nothing)."""
        return ((char.get('equipment') or {}).get(gear.ROLES[file]) or {}).get('vanilla') is True

    def same_portrait(self, char: dict, view: str, image) -> bool:
        """Whether the game shows exactly ``image`` (48x51 RGBA) as the slot's ``view`` portrait now."""
        return False

    def close_portrait(self, char: dict, view: str, image) -> bool:
        """Whether the slot shows ``image`` as its ``view`` portrait up to the roster's CMPR re-encoding (a portrait
        copied from stock art never decodes back exactly)."""
        return self.same_portrait(char, view, image)

    def shown_portrait(self, char: dict, view: str):
        """The slot's ``view`` portrait as the game shows it now (RGBA image), or None when it cannot be read."""
        return None

    def stock_portrait(self, cid: int, view: str):
        """A stock ID's own ``view`` portrait from the stock icon bank (RGBA image), or None."""
        return None

    def stat_edits(self, path: str) -> StatCheck:
        """The stat edit file at ``path`` (or a ``reset:0xNN`` item) checked against the game files
        (``StatEditor/apply.py``); a refusal raises ``PlanError``."""
        raise NotImplementedError

    def stat_edited(self, cid: int) -> str | None:
        """What stat edits the game holds for ``cid`` (``StatEditor/carry.CharacterEdits.text``), or None."""
        return None

    def current_block(self, char: dict, role: str) -> bytes | None:
        """The block the slot loads now for ``role`` (``high`` / ``low`` / an equipment role), or None."""
        return None

    def vanilla_block(self, directory: int, file: int) -> bytes | None:
        """File ``file`` of directory ``directory`` in ``1_Input``, or None."""
        return None

    def slot_problems(self, directory: int, high: bytes, low: bytes) -> tuple[list[str], list[str]]:
        """(errors, warnings) for loading ``high`` / ``low`` in a slot whose vanilla files are ``directory``'s
        (validator, skeleton, High/Low pair rules: ``pack.LoadEnv.slot_problems``)."""
        return [], []

    def equipment_block_problems(self, directory: int, file: int, block: bytes) -> tuple[list[str], list[str]]:
        """(errors, warnings) for loading the equipment ``block`` as file ``file`` of a slot whose vanilla files are
        ``directory``'s (``SlotTarget.equipment_block_problems``)."""
        return [], []

    def stat_snapshot(self, cid: int) -> dict | None:
        """``cid``'s live stat values (``StatEditor/apply`` copy document without ``target``: ``fields`` {group:
        {name: hex bytes}}, ``chemistry`` {other ID: [row byte, column byte]}), or None when unreadable."""
        return None


@dataclass
class StatCheck:
    """What a stat edit file changes: ``summary`` (``12 values on 3 characters``; None: nothing to change),
    ``lines`` (per character and table), ``warnings``."""
    summary: str | None
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------

@dataclass
class Plan:
    action: str                     # 'patch' or 'clear'
    target: int
    config: dict | None             # the changed derived config, or None: no roster rebuild
    commands: list[tuple] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extra_portraits: dict = field(default_factory=dict)  # {file name in the derived icon folder: open-slot view}
    portraits: dict = field(default_factory=dict)        # {file name in the derived icon folder: RGBA image or PNG}
    pair: Pair | None = None        # patch: the picked file and its partner
    nothing: bool = False           # clear: the slot is at its baseline already, no commands
    gear: dict | None = None        # equip / equip_clear: {'file', 'label', 'path', 'shared_with', 'origin'}
    # The slot's stat edits that stay on top of a changed stats source or a clear (``Env.stat_edited``); the GUI
    # offers to clear them (a ``stat_reset`` edit)
    stat_edits_kept: str | None = None
    # What the slot shows once the edit is written (the GUI's pending lines): 'model', 'name', 'stats', 'voice'
    # (display text) and 'portraits' ({'front', 'side'}: PNG paths to preview, or None: unchanged)
    effects: dict = field(default_factory=dict)
    copy_source: int | None = None  # copy: the character the slot becomes a clone of
    # copy: the snapshot files the chain reads ({file name in the copy folder: bytes, RGBA image or JSON data})
    copy_files: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        out = {'action': self.action, 'target': _hex(self.target), 'rebuild': self.config is not None,
               'commands': [list(c) for c in self.commands], 'notes': self.notes, 'warnings': self.warnings,
               'nothing': self.nothing, 'effects': self.effects}
        if self.copy_source is not None:
            out['source'] = _hex(self.copy_source)
        if self.pair is not None:
            out['source'] = _hex(self.pair.source)
            out['files'] = {'high': self.pair.high, 'low': self.pair.low, 'picked': self.pair.picked}
        if self.gear is not None:
            out['gear'] = dict(self.gear)
        if self.stat_edits_kept is not None:
            out['stat_edits_kept'] = self.stat_edits_kept
        return out


def _character(st: dict, cid: int) -> dict:
    for c in st['characters']:
        if c['id'] == cid:
            return c
    raise PlanError(f'{_hex(cid)} is not a slot of this roster (not on any square)')


def _entry(config: dict, key: str, cid: int) -> dict | None:
    for entry in config.get(key) or []:
        if isinstance(entry, dict) and entry.get('id') is not None and ids._number(entry['id'], key) == cid:
            return entry
    return None


def _square_config(config: dict, cid: int) -> tuple[int, list | dict] | None:
    for k, sq in enumerate((config.get('grid') or {}).get('squares') or []):
        members = sq.get('members') if isinstance(sq, dict) else sq
        if any(ids._number(m, 'grid.squares') == cid for m in members):
            return k, sq
    return None


def _set_icon(config: dict, cid: int, icon: dict) -> str | None:
    """Put ``icon`` on the config entry that owns ``cid``'s portraits; returns why not (None: done)."""
    if cid >= ids.FIRST_NEW:
        entry = _entry(config, 'ids', cid)
        if entry is None:
            return f'{_hex(cid)} has no ids entry'
        like = (entry.get('icon') or {}).get('like')
        entry['icon'] = dict(icon, **({'like': like} if like is not None else {}))
        return None
    if cid in icons.SPARE_DONORS:
        entry = _entry(config, 'wheels', cid)
        if entry is None:
            return f'{_hex(cid)} is not on a wheel'
        like = (entry.get('icon') or {}).get('like')
        entry['icon'] = dict(icon, **({'like': like} if like is not None else {}))
        return None
    if cid < icons.STOCK_ICON_END:
        stock = config.setdefault(icons.STOCK_KEY, [])
        entry = _entry(config, icons.STOCK_KEY, cid)
        if entry is None:
            stock.append({'id': _hex(cid), 'icon': dict(icon)})
        else:
            entry['icon'] = dict(icon)
        return None
    return f'{_hex(cid)} has no portrait records of its own'


def _prepare(plan: Plan, state_file: str, files: list[str]) -> None:
    """The chain's first commands: validate ``files`` (when any), then rebuild when the config changed."""
    if files:
        plan.commands.append(('--patch', *files, '--target-id', _hex(plan.target), '--validate-only'))
    if plan.config is not None:
        plan.commands.append(('--roster', '--state', state_file))


def plan_patch(st: dict, config: dict, cid: int, pair: Pair, env: Env, state_file: str,
               names_text: dict | None = None) -> Plan:
    """The chain that puts ``pair``'s models into slot ``cid`` (module docstring)."""
    char = _character(st, cid)
    new = copy.deepcopy(config)
    plan = Plan('patch', cid, None, pair=pair)
    source = pair.source
    target_name = _display(char)
    source_name = _hex(source)
    if names_text and (names_text.get(source) or {}).get('en'):
        source_name = f'{names_text[source]["en"]} ({_hex(source)})'
    as_low = pair.low is None
    files = pair.files

    if cid >= ids.FIRST_NEW:
        if not 0 <= source < ids.PLAYER_END:
            raise PlanError(f'{os.path.basename(pair.picked)} comes from {_hex(source)}, which is not a stock player '
                            f'(0x00-0x{ids.PLAYER_END - 1:02X}): its files cannot become a new ID\'s directory')
        if pair.high is None:
            raise PlanError(f'{os.path.basename(pair.low)} has no High partner beside it. A new ID takes a '
                            'character as a whole: patch the High model into the slot (it brings its Low '
                            'partner along).')
        entry = _entry(new, 'ids', cid)
        if entry is None:
            raise PlanError(f'{_hex(cid)} has no ids entry in the derived config')
        kept = char.get('own_model_dir') and char.get('model_source') == source
        plan.effects['model'] = (f'{source_name}\'s models in ' + ('its own directory (kept)' if kept else
                                 f'an own directory (fresh copy of {source_name}\'s files)'))
        if kept:
            plan.notes.append(f'{target_name} keeps its own model directory (copies of {source_name}\'s files)')
        else:
            entry['model'] = {'from': _hex(source)}
            if char.get('own_model_dir'):
                plan.notes.append(f'{target_name}\'s own model directory is copied afresh from {source_name} (it '
                                  f'held {_hex(char["model_source"])}\'s files; models patched into it are dropped)')
                changed = [gear.label(e['file']).lower() for e in (char.get('equipment') or {}).values()
                           if e.get('vanilla') is False]
                if changed:
                    plan.warnings.append(f'{target_name}: the fresh directory copy also resets its equipment '
                                         f'({", ".join(changed)}); stage the gear again after this patch')
            else:
                plan.notes.append(f'{target_name} gets an own model directory: a copy of {source_name}\'s files')
        square = st['squares'][char['square']]
        if square['kind'] == 'new':
            if entry.get('stats') != _hex(source):
                entry['stats'] = _hex(source)
                plan.notes.append(f'{target_name} takes {source_name}\'s stats (new square)')
                plan.effects['stats'] = source_name
                _note_stat_edits(plan, env, cid, target_name)
            k, sq = _square_config(new, cid) or (None, None)
            # set in the game, or by an earlier edit of the same batch (pending patches count too)
            voice_set = square.get('voice_set') is not None or (isinstance(sq, dict) and sq.get('voice') is not None)
            if not voice_set:
                if sq is None:
                    raise PlanError(f'{_hex(cid)}: its square is missing from the derived config')
                members = sq['members'] if isinstance(sq, dict) else sq
                new['grid']['squares'][k] = {'members': list(members), 'voice': _hex(source)}
                plan.notes.append(f'the square\'s voice becomes {source_name}\'s (first patch on it)')
                plan.effects['voice'] = f'{source_name} (square voice)'
        else:
            plan.notes.append(f'{target_name} keeps its stats and voice (stock square)')
        en = (entry.get('name') or char.get('name') or {}).get('en')     # a pending rename counts
        source_text = (names_text or {}).get(source)
        if en in (None, '', names.UNNAMED, open_slot.SLOT_NAME['en']) and source_text and source_text.get('en'):
            entry['name'] = {lang: source_text.get(lang) or source_text['en'] for lang in open_slot.SLOT_NAME}
            plan.notes.append(f'{target_name} is named "{source_text["en"]}" (it was an open slot)')
            plan.effects['name'] = source_text['en']
    else:
        target_dir = cid + ids.MODEL_DIR_BASE
        if target_dir != pair.chunk:
            roles = [HIGH_FILE] if pair.high else []
            roles += [LOW_FILE] if pair.low else []
            for role in roles:
                errors, warnings = env.skeleton((pair.chunk, role), (target_dir, role))
                if errors:
                    raise PlanError(f'the skeletons do not match ({errors[0]}). A stock slot keeps its own '
                                    'animations, so only a model with the same skeleton fits; put this model on a '
                                    'new ID instead.')
                plan.warnings += warnings
        if pair.high is None:
            _check_low_alone(env, cid, pair, target_name)
        plan.notes.append(f'{target_name} keeps its stats, voice and name (stock slot)')
        plan.effects['model'] = f'{source_name}\'s ' + ('Low model' if pair.high is None else 'models')

    if as_low:
        plan.warnings.append(f'{os.path.basename(pair.high)} has no Low partner beside it: it is used as the '
                             'Low model too, so the slot loads it twice on the field (counts twice against '
                             'the memory budget)')
    found = env.model_icons(pair.high or pair.low)
    if not found.ok:
        plan.notes.append(f'portraits not imported, {target_name} keeps its own: {found.problem}')
    elif env.shows_portraits(char, found):
        plan.notes.append(f'{target_name} already shows the portraits of {os.path.basename(found.home)}')
    else:
        why = _set_icon(new, cid, {'model': found.home})
        if why:
            plan.notes.append(f'portraits not imported: {why}')
        else:
            plan.notes.append(f'portraits from {os.path.basename(found.home)}/{model_icons.ICON_SUBDIR}')
            plan.effects['portraits'] = {'front': found.front, 'side': found.side}

    if new != config:
        plan.config = new
    _prepare(plan, state_file, files)
    plan.commands.append(('--patch', *files, '--target-id', _hex(cid)) + (('--as-low',) if as_low else ()))
    plan.commands.append(('--roster-state',))
    plan.notes.insert(0, f'{" + ".join(os.path.basename(f) for f in files)} -> {target_name}'
                         + (' (High model as the Low model too)' if as_low else ''))
    return plan


def _note_stat_edits(plan: Plan, env: Env | None, cid: int, target_name: str, clear: bool = False) -> None:
    """A slot whose stats source changes (or that is cleared) keeps its stat edits: the roster rebuild carries them
    by character ID onto the new rows (``StatEditor/carry.py``). Say so; the GUI offers to clear them."""
    kept = env.stat_edited(cid) if env is not None else None
    if not kept:
        return
    plan.stat_edits_kept = kept
    plan.notes.append(f'{target_name} keeps its stat edits ({kept}) on top of '
                      + ('its baseline: a clear does not touch stat values' if clear else 'the new stats'))


def _check_low_alone(env: Env, cid: int, pair: Pair, target_name: str) -> None:
    stem = env.current_high_stem(cid)
    if stem != pair.stem:
        raise PlanError(
            f'{os.path.basename(pair.low)} has no High partner beside it, and binds its textures by index into '
            f'its own High model, but {target_name}\'s High model is {stem or "nothing readable"}. Patch '
            'the High model into the slot (it brings its Low partner along).')


def plan_clear(st: dict, config: dict, cid: int, state_file: str, env: Env | None = None) -> Plan:
    """The chain that returns slot ``cid`` to its baseline (module docstring). A slot already at its baseline
    (``env.at_baseline``) gets a plan with ``nothing`` set and no commands."""
    char = _character(st, cid)
    new = copy.deepcopy(config)
    plan = Plan('clear', cid, None)
    target_name = _display(char)
    if env is not None and env.at_baseline(char, config):
        plan.nothing = True
        plan.notes.append(f'nothing to clear: {target_name} is at its baseline already')
        return plan
    if cid >= ids.FIRST_NEW:
        entry = _entry(new, 'ids', cid)
        if entry is None:
            raise PlanError(f'{_hex(cid)} has no ids entry in the derived config')
        template = ids._number(entry['template'], 'template')
        entry['model'] = {'from': _hex(template)}
        entry.pop('stats', None)
        entry['name'] = dict(open_slot.SLOT_NAME)
        like = (entry.get('icon') or {}).get('like')
        entry['icon'] = dict(open_slot.SLOT_ICON, **({'like': like} if like is not None else {}))
        plan.extra_portraits = dict((name, view) for view, name in open_slot.SLOT_ICON.items())
        plan.effects.update(model=f'its template {_hex(template)}\'s files (fresh copy)',
                            name=open_slot.SLOT_NAME['en'], stats=f'its template {_hex(template)}',
                            portraits={view: os.path.join(open_slot.ICON_DIR, name)
                                       for view, name in open_slot.SLOT_ICON.items()})
        plan.notes += [f'{target_name}: own model directory copied afresh from its template {_hex(template)}',
                       'stats back to the template\'s; name "Empty slot"; empty-slot portraits',
                       'the square\'s voice is kept']
        plan.config = new
        _prepare(plan, state_file, [])
    else:
        entry = _entry(new, icons.STOCK_KEY, cid)
        if entry is not None:
            new[icons.STOCK_KEY].remove(entry)
            if not new[icons.STOCK_KEY]:
                del new[icons.STOCK_KEY]
            plan.notes.append(f'{target_name}: its stock portraits come back')
            plan.effects['portrait_note'] = 'stock portraits come back'
        entry = _entry(new, ids.STOCK_STATS_KEY, cid)
        if entry is not None:
            _drop(new, ids.STOCK_STATS_KEY, entry)
            plan.notes.append(f'{target_name}: its own stats come back')
            plan.effects['stats'] = 'its own'
        if new != config:
            plan.config = new
        _prepare(plan, state_file, [])
        plan.commands.append(('--unpatch', '--target-id', _hex(cid)))
        plan.notes.insert(0, f'{target_name}: vanilla High and Low models from 1_Input')
        plan.effects['model'] = 'vanilla High and Low models'
        changed = [e['file'] for e in (char.get('equipment') or {}).values() if e.get('vanilla') is False]
        for file in changed:
            plan.commands.append(('--unpatch', '--target-id', _hex(cid), '--target-file', str(file)))
        if changed:
            names_ = ', '.join(gear.label(f).lower() for f in changed)
            plan.notes.append(f'{target_name}: its {names_} go back to the vanilla ones')
            plan.effects['equipment'] = f'vanilla {names_}'
    plan.commands.append(('--roster-state',))
    _note_stat_edits(plan, env, cid, target_name, clear=True)
    return plan


def _gear_text(char: dict, file: int) -> dict:
    entry = (char.get('equipment') or {}).get(gear.ROLES[file]) or {}
    return {'file': file, 'label': gear.label(file), 'shared_with': int(entry.get('shared_with') or 0)}


def _own_directory_source(char: dict, entry: dict | None = None) -> int | None:
    """The stock character whose directory's files the slot's equipment starts from: a stock ID's own, a new ID's
    ``ids[].model.from`` (``entry``: the config as earlier edits of the batch left it), else its own directory's
    source, else its template."""
    if char['id'] < ids.FIRST_NEW:
        return char['id']
    model = (entry or {}).get('model')
    if isinstance(model, dict) and model.get('from') is not None:
        return ids._number(model['from'], 'model.from')
    return char.get('model_source') if char.get('own_model_dir') else char.get('template')


def plan_equip(st: dict, config: dict, cid: int, pick: GearPick, file: int, env: Env, state_file: str,
               origin: str = 'user') -> Plan:
    """The chain that puts the bat or glove ``pick`` into file ``file`` of slot ``cid`` (module docstring): one
    build check, then one targeted write of that route only. A new ID without an own model directory gets one first
    (a copy of its template's files, ``ids[].model.from``), so its template's equipment is not touched."""
    char = _character(st, cid)
    new = copy.deepcopy(config)
    plan = Plan('equip', cid, None)
    target_name = _display(char)
    name = os.path.basename(pick.path)
    plan.gear = dict(_gear_text(char, file), path=pick.path, origin=origin)
    entry = None
    if cid >= ids.FIRST_NEW:
        entry = _entry(new, 'ids', cid)
        if entry is None:
            raise PlanError(f'{_hex(cid)} has no ids entry in the derived config')
        if not char.get('own_model_dir') and entry.get('model') is None:
            entry['model'] = {'from': _hex(ids._number(entry['template'], 'template'))}
            plan.notes.append(f'{target_name} gets an own model directory (a copy of its template\'s files), so '
                              'its equipment can differ from the template\'s')
    source_dir = _own_directory_source(char, entry)
    if source_dir is None:
        raise PlanError(f'{target_name}: its model directory is unknown, so its {gear.label(file).lower()} '
                        'cannot be checked')
    errors = env.equipment_problems((pick.chunk, pick.source_file), (source_dir + ids.MODEL_DIR_BASE, file))
    if errors:
        raise PlanError(f'the skeletons do not match ({errors[0]}). The slot poses its '
                        f'{gear.label(file).lower()} with the animations made for its own one.')
    shared = plan.gear['shared_with']
    if shared and cid < ids.FIRST_NEW:
        plan.notes.append(f'the {gear.label(file).lower()} of {target_name} is shared by {shared} other '
                          f'slot{"s" if shared != 1 else ""}: only this slot changes')
    plan.notes.insert(0, f'{name} -> {gear.label(file).lower()} of {target_name}')
    plan.effects['equipment'] = {file: f'{gear.label(file).lower()} from {name}'}
    if new != config:
        plan.config = new
    extra = ('--target-file', str(file)) if file != pick.source_file else ()
    plan.commands.append(('--patch', pick.path, '--target-id', _hex(cid), '--validate-only') + extra)
    if plan.config is not None:
        plan.commands.append(('--roster', '--state', state_file))
    plan.commands.append(('--patch', pick.path, '--target-id', _hex(cid)) + extra)
    plan.commands.append(('--roster-state',))
    return plan


def plan_equip_clear(st: dict, config: dict, cid: int, file: int, env: Env, state_file: str) -> Plan:
    """The chain that returns file ``file`` (bat, glove or extra bat) of slot ``cid`` to its baseline: the vanilla
    block of a stock slot, a fresh copy of its source's block for a new ID's own directory. A file at its
    baseline already (``env.equipment_at_baseline``) gives a plan with ``nothing`` set."""
    char = _character(st, cid)
    plan = Plan('equip_clear', cid, None)
    target_name = _display(char)
    plan.gear = dict(_gear_text(char, file), origin='user')
    what = gear.label(file).lower()
    if env.equipment_at_baseline(char, file):
        plan.nothing = True
        plan.notes.append(f'nothing to reset: the {what} of {target_name} is at its baseline already')
        return plan
    plan.commands.append(('--unpatch', '--target-id', _hex(cid), '--target-file', str(file)))
    plan.commands.append(('--roster-state',))
    plan.notes.append(f'{target_name}: the {what} goes back to '
                      + ('its template\'s' if cid >= ids.FIRST_NEW else 'the vanilla one'))
    plan.effects['equipment'] = {file: 'back to ' + ('its template\'s' if cid >= ids.FIRST_NEW else 'vanilla')}
    return plan


def plan_rename(st: dict, config: dict, cid: int, text: str, state_file: str) -> Plan:
    """The chain that names slot ``cid`` ``text`` in all three languages (a roster rebuild: the name tables and
    the select screen's name plates). A blank ``text`` resets the name: a stock character gets its stock name
    back, a new ID the "Empty slot" name. A name that does not fit the plate is refused (``names.fit_problem``)."""
    char = _character(st, cid)
    new = copy.deepcopy(config)
    plan = Plan('rename', cid, None)
    target_name = _display(char)
    text = (text or '').strip()
    reset = not text
    if not reset:
        problem = names.fit_problem(text)
        if problem:
            raise PlanError(f'{target_name}: {problem}')
    value = dict(open_slot.SLOT_NAME) if reset else {lang: text for lang in open_slot.SLOT_NAME}
    current = char.get('name')
    if cid >= ids.FIRST_NEW:
        entry = _entry(new, 'ids', cid)
        if entry is None:
            raise PlanError(f'{_hex(cid)} has no ids entry in the derived config')
        entry['name'] = value
    elif cid in wheels.SPARE_IDS:
        entry = _entry(new, 'wheels', cid)
        if entry is None:
            raise PlanError(f'{_hex(cid)} is not on a wheel, so it has no name to change')
        if reset:
            entry.pop('name', None)
        else:
            entry['name'] = value
    elif cid < names.RENAMEABLE_END:
        entry = _entry(new, names.STOCK_KEY, cid)
        if reset:
            if entry is not None:
                new[names.STOCK_KEY].remove(entry)
                if not new[names.STOCK_KEY]:
                    del new[names.STOCK_KEY]
        elif entry is not None:
            entry['name'] = value
        elif current != value:                        # the same text as the stock name: nothing to write
            new.setdefault(names.STOCK_KEY, []).append({'id': _hex(cid), 'name': value})
            new[names.STOCK_KEY].sort(key=lambda e: ids._number(e['id'], names.STOCK_KEY))
    else:
        raise PlanError(f'{_hex(cid)} has no name plate to change')
    if new == config:
        plan.nothing = True
        plan.notes.append(f'nothing to rename: {target_name} is named that already' if not reset
                          else f'nothing to reset: {target_name} has its default name already')
        return plan
    plan.config = new
    _prepare(plan, state_file, [])
    plan.commands.append(('--roster-state',))
    if reset:
        shown = open_slot.SLOT_NAME['en'] if cid >= ids.FIRST_NEW else 'its stock name'
        plan.notes.append(f'{target_name}: name reset to {shown!r}')
    else:
        shown = text
        plan.notes.append(f'{target_name}: renamed to {text!r} (English, French and Spanish)')
    plan.effects['name'] = shown
    return plan


def _drop(config: dict, key: str, entry: dict) -> None:
    config[key].remove(entry)
    if not config[key]:
        del config[key]


def _set_stock_entry(config: dict, key: str, cid: int, field_: str, value: str | None) -> None:
    """Put ``{"id": cid, field_: value}`` into the stock list ``key`` (sorted by ID); None removes the entry."""
    entry = _entry(config, key, cid)
    if value is None:
        if entry is not None:
            _drop(config, key, entry)
    elif entry is not None:
        entry[field_] = value
    else:
        config.setdefault(key, []).append({'id': _hex(cid), field_: value})
        config[key].sort(key=lambda e: ids._number(e['id'], key))


def _source_name(source: int, names_text: dict | None, st: dict | None = None) -> str:
    text = ((names_text or {}).get(source) or {}).get('en')
    if not text and st is not None:
        char = next((c for c in st['characters'] if c['id'] == source), None)
        text = char and (char.get('default_name') or (char.get('name') or {}).get('en'))
    return f'{text} ({_hex(source)})' if text else _hex(source)


def plan_stats(st: dict, config: dict, cid: int, source: int | None, state_file: str,
               names_text: dict | None = None, env: Env | None = None) -> Plan:
    """The chain that lets slot ``cid`` play with stock player ``source``'s stats (``None``: its default, a new
    ID's template's, a stock ID's own). The stats rows only: model, size, voice and name stay. The slot's stat edits
    stay on top of the new rows (``_note_stat_edits``)."""
    char = _character(st, cid)
    new = copy.deepcopy(config)
    plan = Plan('stats', cid, None)
    target_name = _display(char)
    if source is not None and not 0 <= source < ids.PLAYER_END:
        raise PlanError(f'{_hex(source)} is not a stock player (0x00-0x{ids.PLAYER_END - 1:02X}): its stats cannot '
                        'be copied')
    if cid >= ids.FIRST_NEW:
        entry = _entry(new, 'ids', cid)
        if entry is None:
            raise PlanError(f'{_hex(cid)} has no ids entry in the derived config')
        default = ids._number(entry['template'], 'template')
        if source is None or source == default:
            entry.pop('stats', None)
        else:
            entry['stats'] = _hex(source)
        default_text = f'its template {_source_name(default, names_text, st)}\'s'
    elif cid < ids.PLAYER_END:
        default = cid
        _set_stock_entry(new, ids.STOCK_STATS_KEY, cid, 'stats',
                         None if source is None or source == cid else _hex(source))
        default_text = 'its own'
    else:
        raise PlanError(f'{target_name} has no stats rows of its own (Miis cannot take other stats)')
    shown = default_text if source is None or source == default else f'{_source_name(source, names_text, st)}\'s'
    if new == config:
        plan.nothing = True
        plan.notes.append(f'nothing to change: {target_name} plays with {shown} stats already')
        return plan
    plan.config = new
    _prepare(plan, state_file, [])
    plan.commands.append(('--roster-state',))
    plan.notes.append(f'{target_name} plays with {shown} stats (stats, pitching, fielding, chemistry; its model, '
                      'size and voice stay)')
    plan.effects['stats'] = shown[:-2] if shown.endswith("'s") else shown
    _note_stat_edits(plan, env, cid, target_name)
    return plan


def _voice_species(st: dict) -> dict[int, int]:
    """``{base character: species}`` of the stock squares."""
    return {sq['head']: sq['head_index'] for sq in st['squares'] if sq['kind'] == 'stock'}


def voice_family(st: dict, source: int) -> int:
    """The base character whose voice ``source`` names (its family); refuses characters without a voice."""
    species_of = _voice_species(st)
    if source in species_of:
        return source
    char = next((c for c in st['characters'] if c['id'] == source), None)
    if char is None or char.get('family') not in species_of:
        raise PlanError(f'{_hex(source)} has no voice to take: pick a character of a stock square')
    return char['family']


def _check_voices(st: dict, config: dict) -> None:
    """Every new square's voice must still be spoken by some stock square after the stock squares' swaps."""
    species_of = _voice_species(st)
    heads = {s: h for h, s in species_of.items()}
    try:
        remap = voices.parse_stock_voices(config, bytes(heads.get(s, 0xFF) for s in range(voices.SPECIES)))
    except voices.VoiceConfigError as exc:
        raise PlanError(str(exc)) from exc
    for sq in (config.get('grid') or {}).get('squares') or []:
        if not isinstance(sq, dict) or sq.get('voice') is None:
            continue
        family = voice_family(st, ids._number(sq['voice'], 'voice'))
        if voices.species_for_voice(species_of[family], remap) is None:
            head = ids._number(sq['members'][0], 'members')
            raise PlanError(f'the new square of {_hex(head)} speaks with {_source_name(family, None, st)}\'s voice, '
                            'and no stock square would keep it: give that square another voice first')


def plan_voice(st: dict, config: dict, cid: int, source: int | None, state_file: str,
               names_text: dict | None = None) -> Plan:
    """The chain that gives the square of slot ``cid`` the voice of ``source``'s family (``None``: its own; a
    new square: none set). A stock square changes the voice of its whole species; a new square the voice of its
    square-only new IDs (members with a wheel keep their wheel's)."""
    char = _character(st, cid)
    square = st['squares'][char['square']]
    head = square['head']
    new = copy.deepcopy(config)
    plan = Plan('voice', head, None)
    family = None if source is None else voice_family(st, source)
    square_name = f'the square of {_display(_character(st, head))}'
    if square['kind'] == 'stock':
        if family == head:
            family = None
        _set_stock_entry(new, voices.STOCK_KEY, head, 'voice', None if family is None else _hex(family))
        shown = 'its own voice' if family is None else f'{_source_name(family, names_text, st)}\'s voice'
        reach = 'every member of the species (its wheel)'
    else:
        k, sq = _square_config(new, head) or (None, None)
        if sq is None:
            raise PlanError(f'{_hex(head)}: its square is missing from the derived config')
        members = list(sq['members'] if isinstance(sq, dict) else sq)
        new['grid']['squares'][k] = members if family is None else {'members': members, 'voice': _hex(family)}
        shown = 'no set voice' if family is None else f'{_source_name(family, names_text, st)}\'s voice'
        square_only = [m for m in members if (_entry(new, 'ids', ids._number(m, 'members')) or {}).get('wheel', 0)
                       is None]
        kept = [m for m in members if m not in square_only]
        reach = ('its square-only new IDs (' + (', '.join(square_only) or 'none') + ')'
                 + (f'; {", ".join(kept)} keep their own wheel\'s voice' if kept else ''))
    if new == config:
        plan.nothing = True
        plan.notes.append(f'nothing to change: {square_name} has {shown} already')
        return plan
    _check_voices(st, new)
    plan.config = new
    _prepare(plan, state_file, [])
    plan.commands.append(('--roster-state',))
    plan.notes.append(f'{square_name} speaks with {shown}: {reach}, on the select screen and on the field')
    plan.effects['voice'] = (shown[:-len("'s voice")] if shown.endswith("'s voice") else shown) + ' (square voice)'
    return plan


VIEWS = ('front', 'side')


@dataclass
class IconPick:
    """An icon edit's image, fitted to 48x51 (``icon_import``)."""
    image: object                   # 48x51 RGBA, alpha hardened
    path: str                       # the file it was read from (the GUI's normalised copy: its preview)
    label: str                      # the user's file name, for the texts
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _icon_entry(config: dict, cid: int) -> dict | None:
    """The config entry that holds ``cid``'s portraits (``_set_icon``'s choice), or None."""
    if cid >= ids.FIRST_NEW:
        return _entry(config, 'ids', cid)
    if cid in icons.SPARE_DONORS:
        return _entry(config, 'wheels', cid)
    if cid < icons.STOCK_ICON_END:
        return _entry(config, icons.STOCK_KEY, cid)
    return None


def has_portrait_records(cid: int) -> bool:
    """Whether ``cid`` can show portraits of its own (Miis cannot: they show the Mii icon)."""
    return cid >= ids.FIRST_NEW or cid in icons.SPARE_DONORS or cid < icons.STOCK_ICON_END


def plan_icon(st: dict, config: dict, cid: int, view: str, pick: IconPick, env: Env, state_file: str,
              compare: bool = True) -> Plan:
    """The chain that makes ``pick`` slot ``cid``'s ``view`` portrait (module docstring). ``compare``: an image
    the game shows already for that view gives a plan with ``nothing`` set (only meaningful while no earlier edit
    of the batch changed the slot's portraits)."""
    char = _character(st, cid)
    target_name = _display(char)
    if view not in VIEWS:
        raise PlanError(f'{view!r} is not a portrait view (front or side)')
    if not has_portrait_records(cid):
        raise PlanError(f'{target_name} has no portrait records of its own (Miis show the Mii icon)')
    plan = Plan('icon', cid, None)
    if compare and env.same_portrait(char, view, pick.image):
        plan.nothing = True
        plan.notes.append(f'nothing to change: {target_name} shows this {view} portrait already')
        return plan
    new = copy.deepcopy(config)
    other = 'side' if view == 'front' else 'front'
    entry = _icon_entry(new, cid)
    current = (entry or {}).get('icon') or {}
    name = f'pick_{cid:02X}_{view}.png'
    keep = f'keep_{cid:02X}_{other}.png'
    plan.portraits[name] = pick.image
    other_name = None
    if current.get('model') is not None:
        path = getattr(env.model_icons(current['model']), other)
        if path:
            other_name = keep
            plan.portraits[keep] = path
            plan.notes.append(f'its {other} portrait stays the one from {os.path.basename(current["model"])}')
    elif current.get(other):
        other_name = current[other]                  # its own cell: kept by name (and CMPR blocks)
    if other_name is None:
        stock = cid < icons.STOCK_ICON_END
        image = env.stock_portrait(cid, other) if stock else env.shown_portrait(char, other)
        if image is None:
            raise PlanError(f'{target_name}: its current {other} portrait cannot be read, so it cannot be kept '
                            'beside the new one')
        other_name = keep
        plan.portraits[keep] = image
        shown = 'its stock' if stock else 'the'
        plan.notes.append(f'{shown} {other} portrait it shows now becomes its own (kept beside the new {view} one)')
    why = _set_icon(new, cid, {view: name, other: other_name, 'fit': icon_art.DEFAULT_FIT_MODE})
    if why:
        raise PlanError(f'{target_name}: {why}')
    plan.config = new
    _prepare(plan, state_file, [])
    plan.commands.append(('--roster-state',))
    plan.notes.insert(0, f'{target_name}: {view} portrait from {pick.label} (48x51)')
    plan.notes += pick.notes
    plan.warnings += pick.warnings
    plan.effects['portraits'] = {view: pick.path}
    plan.effects['portrait_note'] = f'{view} portrait from {pick.label}'
    return plan


# --------------------------------------------------------------------------
# Copy / paste: a slot becomes a clone of another
# --------------------------------------------------------------------------

COPY_DIR = 'copies'                 # beside the batch's roster.json: the snapshot of each copied character
COPY_PREFIX = 'copy:'               # StatEditor/apply.COPY_PREFIX (test-pinned)
COPY_FORMAT = 'sluggies-stat-copy'  # StatEditor/apply.COPY_FORMAT (test-pinned)
MODEL_ROLES = {'high': HIGH_FILE, 'low': LOW_FILE}
BLOCK_SUFFIX = {'high': 'hp', 'low': 'l', **{role: role for role in gear.ROLES.values()}}


def copy_dir(state_file: str) -> str:
    return os.path.join(os.path.dirname(state_file), COPY_DIR)


def _sha(char: dict, role: str) -> str | None:
    table = char.get('blocks') if role in MODEL_ROLES else char.get('equipment')
    return ((table or {}).get(role) or {}).get('sha1')


def _role_label(role: str) -> str:
    return {'high': 'High model', 'low': 'Low model'}.get(role) or gear.label(
        next(f for f, r in gear.ROLES.items() if r == role)).lower()


def _set_name(config: dict, cid: int, value: dict) -> str | None:
    """Name slot ``cid`` ``value`` (every language) in ``config`` (``plan_rename``'s entries); returns why not."""
    if cid >= ids.FIRST_NEW:
        entry = _entry(config, 'ids', cid)
        if entry is None:
            return f'{_hex(cid)} has no ids entry'
        entry['name'] = dict(value)
    elif cid in wheels.SPARE_IDS:
        entry = _entry(config, 'wheels', cid)
        if entry is None:
            return f'{_hex(cid)} is not on a wheel'
        entry['name'] = dict(value)
    elif cid < names.RENAMEABLE_END:
        _set_stock_entry(config, names.STOCK_KEY, cid, 'name', dict(value))
    else:
        return 'Miis have no name plate of their own'
    return None


def _same_stat_values(source: dict, target: dict, sid: int, tid: int) -> bool:
    """Whether ``target``'s live stat values (``Env.stat_snapshot``) equal ``source``'s as a copy would map them."""
    if source.get('fields') != target.get('fields'):
        return False
    theirs = target.get('chemistry') or {}
    for other, pair in (source.get('chemistry') or {}).items():
        o = int(other, 16)
        if o == tid:
            continue                                  # the pair of source and target is kept
        if theirs.get(_hex(tid if o == sid else o)) != pair:
            return False
    return True


def plan_copy(st: dict, config: dict, base_config: dict, cid: int, source: int, env: Env, state_file: str,
              names_text: dict | None = None) -> Plan:
    """The chain that makes slot ``cid`` a clone of ``source`` as the game holds it (``st``, ``base_config``: the
    read and derived config before the batch, so pending edits of the source are not copied; ``config``: as earlier
    edits of the batch left it).

    * Models and equipment: the source's finished blocks (``Env.current_block``, snapshot files in ``copy_dir``),
      written with ``--write-slot-blocks`` / ``--write-slot-equipment``. A new ID gets an own model directory from
      the source's model source (kept when it has one already); a stock slot takes the models only with a matching
      skeleton (refused otherwise); a patched gear file whose vanilla block is the source's is unpatched instead.
      Gear that does not fit
      is skipped with a warning; every other bat / glove file is copied (a full clone).
    * Name, stats source and portraits (the source's own cells by name, else the pixels it shows): one roster
      rebuild. The voice belongs to the square: only a target alone on a new square takes the source's.
    * Stat values: ``--apply-stat-edits copy:<snapshot>`` after the rebuild (every field, and the chemistry in both
      directions; the target's own pair with the source stays)."""
    if source == cid:
        raise PlanError(f'{_hex(cid)} cannot be pasted onto itself')
    char, src = _character(st, cid), _character(st, source)
    target_name, source_name = _display(char), _display(src)
    if not src.get('blocks') or char.get('blocks') is None:
        raise PlanError('the model blocks cannot be read (is dt_na.dat in 3_Output_Dat?)')
    new = copy.deepcopy(config)
    plan = Plan('copy', cid, None, copy_source=source)
    folder = copy_dir(state_file)
    tag = f'{source:02X}_{cid:02X}'

    def stash(name, data):
        plan.copy_files[name] = data
        return os.path.join(folder, name)

    blocks = {}
    for role in list(MODEL_ROLES) + [gear.ROLES[f] for f in gear.FILES]:
        if _sha(src, role) is None:
            continue
        block = env.current_block(src, role)
        if block is None:
            raise PlanError(f'{source_name}: its {_role_label(role)} cannot be read')
        blocks[role] = block
    if any(role not in blocks for role in MODEL_ROLES):
        raise PlanError(f'{source_name} has no High and Low model to copy')

    # models and equipment
    model_source = src['model_source']
    source_dir = model_source + ids.MODEL_DIR_BASE
    writes, gear_unpatches, fresh = {}, [], False
    if cid >= ids.FIRST_NEW:
        entry = _entry(new, 'ids', cid)
        if entry is None:
            raise PlanError(f'{_hex(cid)} has no ids entry in the derived config')
        if not 0 <= model_source < ids.PLAYER_END:
            raise PlanError(f'{source_name} loads the files of {_hex(model_source)}, which is not a stock player '
                            f'(0x00-0x{ids.PLAYER_END - 1:02X}): they cannot become a new ID\'s directory')
        differs = [r for r in blocks if _sha(char, r) != _sha(src, r)]
        files_of = _source_name(model_source, names_text, st)
        if char.get('model_source') == model_source and not differs:
            plan.notes.append(f'{target_name} loads the same model files already')
        elif char.get('own_model_dir') and char.get('model_source') == model_source:
            writes = {r: blocks[r] for r in differs}
            plan.notes.append(f'{target_name} keeps its own model directory (copies of {files_of}\'s files)')
        else:
            entry['model'] = {'from': _hex(model_source)}
            fresh = True
            # the fresh copy comes from 1_Input: every file whose bytes differ goes on top (also untangled texture
            # bytes, so the clone keeps the source's Dolphin texture hashes)
            files = {**MODEL_ROLES, **{r: f for f, r in gear.ROLES.items()}}
            writes = {r: b for r, b in blocks.items() if b != env.vanilla_block(source_dir, files[r])}
            plan.notes.append(f'{target_name} gets an own model directory: a fresh copy of {files_of}\'s files'
                              + (', then the source\'s changed files on top' if writes else '')
                              + (' (models patched into its old directory are dropped)'
                                 if char.get('own_model_dir') else ''))
        check_dir = source_dir
        if any(r in writes for r in MODEL_ROLES):
            errors, warnings = env.slot_problems(check_dir, blocks['high'], blocks['low'])
            if errors:
                raise PlanError(f'{source_name}\'s models do not fit: {errors[0]}')
            plan.warnings += warnings
    else:
        check_dir = char['model_dir']
        differs = [r for r in MODEL_ROLES if _sha(char, r) != _sha(src, r)]
        if differs:
            errors, warnings = env.slot_problems(check_dir, blocks['high'], blocks['low'])
            if errors:
                raise PlanError(f'{source_name} does not fit {target_name} ({errors[0]}). A stock slot keeps its own '
                                'animations, so only a character with the same skeleton fits; paste it onto a new '
                                'ID instead.')
            plan.warnings += warnings
            writes.update({r: blocks[r] for r in differs})
        for f in gear.FILES:
            role = gear.ROLES[f]
            if role not in blocks or _sha(char, role) in (None, _sha(src, role)):
                continue
            # a patched file whose vanilla block is the source's goes back to its vanilla route; a file still on its
            # vanilla route is never unpatched (that would rewrite a route other slots may share)
            if char['equipment'][role].get('vanilla') is False and blocks[role] == env.vanilla_block(check_dir, f):
                gear_unpatches.append(f)
            else:
                writes[role] = blocks[role]
    for f in gear.FILES:
        role = gear.ROLES[f]
        if role not in writes:
            continue
        errors, warnings = env.equipment_block_problems(check_dir, f, writes[role])
        if errors:
            del writes[role]
            plan.warnings.append(f'{target_name} keeps its {gear.label(f).lower()}: {source_name}\'s does not fit '
                                 f'({errors[0]})')
        else:
            plan.warnings += warnings
    slot_commands = []
    if any(r in writes for r in MODEL_ROLES):
        slot_commands.append(('--write-slot-blocks', _hex(cid),
                              *(stash(f'{tag}_{BLOCK_SUFFIX[r]}.bin', writes[r]) if r in writes else '-'
                                for r in MODEL_ROLES)))
    for f in gear_unpatches:
        slot_commands.append(('--unpatch', '--target-id', _hex(cid), '--target-file', str(f)))
    for f in gear.FILES:
        role = gear.ROLES[f]
        if role in writes:
            slot_commands.append(('--write-slot-equipment', _hex(cid), str(f),
                                  stash(f'{tag}_{BLOCK_SUFFIX[role]}.bin', writes[role])))
    model_changed = fresh or any(r in writes for r in MODEL_ROLES)
    plan.effects['model'] = f'{source_name}\'s models' if model_changed else 'unchanged (the same models already)'
    gear_changed = [f for f in gear.FILES if gear.ROLES[f] in writes] + gear_unpatches
    if gear_changed:
        plan.effects['equipment'] = {f: f'{gear.label(f).lower()} of {source_name}' for f in sorted(gear_changed)}

    # name
    text = (names_text or {}).get(source) or src.get('name') or {}
    if text.get('en') and text['en'] != names.UNNAMED:
        value = {lang: text.get(lang) or text['en'] for lang in open_slot.SLOT_NAME}
        if char.get('name') != value:
            why = _set_name(new, cid, value)
            if why:
                plan.notes.append(f'name not copied: {why}')
            else:
                plan.effects['name'] = value['en']

    # stats source
    stats = src.get('stats')
    stats_copied = False
    if stats is None or not 0 <= stats < ids.PLAYER_END:
        plan.notes.append(f'stats source not copied: {source_name} has no stock stats rows')
    elif cid >= ids.FIRST_NEW:
        entry = _entry(new, 'ids', cid)
        if stats == ids._number(entry['template'], 'template'):
            entry.pop('stats', None)
        else:
            entry['stats'] = _hex(stats)
        stats_copied = True
    elif cid < ids.PLAYER_END:
        _set_stock_entry(new, ids.STOCK_STATS_KEY, cid, 'stats', None if stats == cid else _hex(stats))
        stats_copied = True
    else:
        plan.notes.append('stats source not copied: Miis cannot take other stats')
    if stats_copied and char.get('stats') != stats:
        plan.effects['stats'] = _source_name(stats, names_text, st)

    # voice: only a target alone on a new square (its square-only ID speaks with the square's voice)
    square = st['squares'][char['square']]
    voice = st['squares'][src['square']]['voice']
    entry = _entry(new, 'ids', cid) if cid >= ids.FIRST_NEW else None
    if square['kind'] == 'new' and square['members'] == [cid] and entry is not None and entry.get('wheel') is None:
        k, sq = _square_config(new, cid) or (None, None)
        if sq is not None and square.get('voice') != voice:
            new['grid']['squares'][k] = {'members': list(sq['members'] if isinstance(sq, dict) else sq),
                                         'voice': _hex(voice_family(st, voice))}
            _check_voices(st, new)
            plan.effects['voice'] = f'{_source_name(voice, names_text, st)} (square voice)'
    elif square.get('voice') != voice:
        plan.notes.append(f'{target_name} keeps its square\'s voice ({_source_name(square["voice"], names_text, st)})')

    # portraits
    if not has_portrait_records(cid):
        plan.notes.append(f'portraits not copied: {target_name} has no portrait records (Miis show the Mii icon)')
    elif not has_portrait_records(source):
        plan.notes.append(f'portraits not copied: {source_name} shows the Mii icon')
    else:
        shown = {view: env.shown_portrait(src, view) for view in VIEWS}
        if any(image is None for image in shown.values()):
            plan.notes.append(f'portraits not copied: {source_name}\'s portraits cannot be read')
        elif not all(env.close_portrait(char, view, shown[view]) for view in VIEWS):
            own = (_icon_entry(base_config, source) or {}).get('icon') or {}
            if all(own.get(view) for view in VIEWS):
                icon = {view: own[view] for view in VIEWS}           # its own cells, by name: blocks kept
            else:
                icon = {view: f'copy_{tag}_{view}.png' for view in VIEWS}
                plan.portraits.update({icon[view]: shown[view] for view in VIEWS})
            why = _set_icon(new, cid, dict(icon, fit='strict'))       # 48x51 cells, as the derive writes them
            if why:
                plan.notes.append(f'portraits not copied: {why}')
            else:
                plan.effects['portraits'] = {view: stash(f'preview_{tag}_{view}.png', shown[view]) for view in VIEWS}
                plan.effects['portrait_note'] = f'{source_name}\'s portraits'

    # stat values, after the rebuild (the target's rows follow the copied stats source then)
    stat_commands = []
    snapshot = env.stat_snapshot(source)
    if snapshot is None:
        plan.notes.append('stat values not copied: the stat tables cannot be read')
    else:
        theirs = env.stat_snapshot(cid)
        if theirs is None or not _same_stat_values(snapshot, theirs, source, cid) or 'stats' in plan.effects:
            doc = dict(snapshot, format=COPY_FORMAT, source=_hex(source), target=_hex(cid))
            stat_commands.append(('--apply-stat-edits', COPY_PREFIX + stash(f'stats_{tag}.json', doc)))
            plan.effects['stat_edits'] = f'{source_name}\'s values (every field and chemistry)'

    if new != config:
        plan.config = new
    if plan.config is None and not slot_commands and not stat_commands:
        plan.nothing = True
        plan.copy_files.clear()
        plan.notes.insert(0, f'nothing to paste: {target_name} is a clone of {source_name} already')
        return plan
    _prepare(plan, state_file, [])
    plan.commands += stat_commands + slot_commands
    plan.commands.append(('--roster-state',))
    plan.notes.insert(0, f'{source_name} -> {target_name} (a clone of what the game holds now)')
    return plan


def _display(char: dict) -> str:
    name = char.get('default_name') or (char.get('name') or {}).get('en')
    return f'{name} ({_hex(char["id"])})' if name and name != names.UNNAMED else _hex(char['id'])


# --------------------------------------------------------------------------
# Batches: staged edits, one chain
# --------------------------------------------------------------------------

def plan_stat_edits(path: str, env: Env) -> Plan:
    """The chain step that writes the stat edit file at ``path`` (``--apply-stat-edits``, merged by ``plan_batch``);
    ``nothing`` when the game holds its values already."""
    check = env.stat_edits(path)
    plan = Plan('stat_edits', GAME_WIDE, None)
    if check.summary is None:
        plan.nothing = True
        plan.notes.append(f'nothing to change: the game holds the values of {os.path.basename(path)} already')
        return plan
    plan.commands.append(('--apply-stat-edits', path))
    plan.notes += check.lines
    plan.warnings += check.warnings
    plan.effects['stat_edits'] = check.summary
    return plan


RESET_PREFIX = 'reset:'                               # StatEditor/apply.RESET_PREFIX (test-pinned)


def plan_stat_reset(st: dict, cid: int, env: Env, after_files: bool = False) -> Plan:
    """The chain step that clears slot ``cid``'s stat edits (every field and chemistry value back to what the roster
    writes without them), merged by ``plan_batch`` into its ``--apply-stat-edits`` in staging order. ``nothing``
    when the game holds none, unless ``after_files``: an earlier staged stat edit file may set some."""
    target_name = _display(_character(st, cid))
    item = f'{RESET_PREFIX}{_hex(cid)}'
    check = env.stat_edits(item)
    plan = Plan(STAT_RESET, cid, None)
    if check.summary is None and not after_files:
        plan.nothing = True
        plan.notes.append(f'nothing to clear: {target_name} has no stat edits')
        return plan
    plan.commands.append(('--apply-stat-edits', item))
    plan.notes.append(f'{target_name}: its stat edits are cleared (fields and chemistry back to the values its '
                      'stats source gives)')
    plan.notes += check.lines
    plan.effects['stat_edits'] = f'cleared ({check.summary})' if check.summary else 'cleared'
    return plan


SLOT_WRITES = ('--patch', '--unpatch', '--write-slot-blocks', '--write-slot-equipment')   # step 4 of a batch
MODEL_OPS = ('patch', 'clear', 'copy')               # copy: the slot becomes a clone of ``source`` (paste)
VALUE_OPS = ('voice', 'stats')                        # the last one per slot (voice: per square) wins
EQUIP_OPS = ('equip', 'equip_clear')                  # the last one per slot file wins
STAT_RESET = 'stat_reset'                             # one per slot; written with the stat edit files, in order
SLOT_OPS = MODEL_OPS + ('rename',) + VALUE_OPS + ('icon',) + EQUIP_OPS + (STAT_RESET,)
COPY = 'copy'
STAT_EDITS = 'stat_edits'                             # not a slot edit: every one is written, in staging order
GAME_WIDE = 0xFF                                      # the "id" of a stat edit: no character has it
ORIGINS = ('user', 'bundled')
DEFAULT_WORDS = ('', '-', 'default')                  # an edit's "source" that resets (CLI text)


@dataclass
class Edit:
    """One staged edit (an entry of the edits file)."""
    op: str                         # 'patch' / 'clear' / 'rename' / 'voice' / 'stats' / 'icon' / ... / 'stat_edits'
    cid: int                        # the slot (voice: any slot of the square; stat_edits: GAME_WIDE)
    file: str | None = None         # patch: the picked .sluggie (a joined pair: the High model); icon: the image;
    #                                 stat_edits: the stat editor's edit file
    low: str | None = None          # patch: a Low pick joined to a pending High pick
    text: str | None = None         # rename
    checked: bool = False           # its build check already passed when it was staged (dry runs skip it)
    index: int = 0                  # position in the edits file (1-based)
    pair: Pair | None = None
    source: int | None = None       # voice / stats: the character to take them from (None: back to the default);
    #                                 copy: the character the slot becomes a clone of
    view: str | None = None         # icon: 'front' or 'side'
    fit: str = icon_art.DEFAULT_FIT_MODE                  # icon: contain / cover / strict
    trim: bool = icon_import.DEFAULT_TRIM                 # icon: crop the transparent border first
    origin: str | None = None       # icon: the user's file, when ``file`` is the GUI's normalised copy;
    #                                 equip: 'user' or 'bundled' (gear taken along with a model patch)
    pick: IconPick | None = None    # icon: the loaded image (``merge_edits``)
    gear_file: int | None = None    # equip / equip_clear: the slot file (2-5); equip: None = the file it came from
    gear: GearPick | None = None    # equip: the picked file's origin (``merge_edits``)
    no_gear: bool = False           # patch into a new ID: do not take the model's bats and gloves along

    def to_json(self) -> dict:
        out = {'op': self.op, 'id': _hex(self.cid)}
        if self.op in EQUIP_OPS:
            if self.gear_file is not None:
                out['file'] = self.gear_file
            if self.op == 'equip':
                out['sluggie'] = self.file
                out['origin'] = self.origin or 'user'
            return out
        for key in ('file', 'low', 'text', 'view', 'origin'):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.no_gear:
            out['gear'] = False
        if self.op in VALUE_OPS + (COPY,):
            out['source'] = None if self.source is None else _hex(self.source)
        if self.op == 'icon':
            out.update(fit=self.fit, trim=self.trim)
        return out


def parse_source(value) -> int | None:
    """An edit's ``source``: a character ID, or None for "back to the default" (null, blank, ``-``, ``default``)."""
    if value is None or (isinstance(value, str) and value.strip().lower() in DEFAULT_WORDS):
        return None
    try:
        return ids._number(value.strip() if isinstance(value, str) else value, 'source')
    except ValueError as exc:
        raise PlanError(str(exc)) from exc


def parse_edits(data) -> list[Edit]:
    """The edits file's list (``{"edits": [...]}`` or the bare list); malformed entries raise ``PlanError``."""
    items = data.get('edits') if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise PlanError('the edits file holds no list of edits')
    edits = []
    for n, item in enumerate(items, 1):
        if isinstance(item, dict) and item.get('op') == STAT_EDITS:
            if not item.get('file'):
                raise PlanError(f'edit {n}: a stat_edits edit needs a "file" (the stat editor\'s edit file)')
            edits.append(Edit(STAT_EDITS, GAME_WIDE, item['file'], checked=bool(item.get('checked')), index=n))
            continue
        if not isinstance(item, dict) or item.get('op') not in SLOT_OPS or item.get('id') is None:
            raise PlanError(f'edit {n} is not an edit: {item!r}')
        try:
            cid = ids._number(item['id'], 'id')
        except (ValueError, TypeError) as exc:
            raise PlanError(f'edit {n}: {exc}') from exc
        if item['op'] in EQUIP_OPS:
            edit = Edit(item['op'], cid, item.get('sluggie'), checked=bool(item.get('checked')), index=n)
            edit.origin = item.get('origin', 'user')
            if edit.origin not in ORIGINS:
                raise PlanError(f'edit {n}: "origin" must be one of {", ".join(ORIGINS)}')
            try:
                edit.gear_file = None if item.get('file') is None else gear.parse_file(item['file'])
            except gear.GearError as exc:
                raise PlanError(f'edit {n}: {exc}') from exc
            if edit.op == 'equip' and not edit.file:
                raise PlanError(f'edit {n}: an equip edit needs a "sluggie" (the bat or glove file)')
            if edit.op == 'equip_clear' and edit.gear_file is None:
                raise PlanError(f'edit {n}: an equip_clear edit needs a "file" (2 bat, 3 left glove, 4 right '
                                'glove, 5 extra bat)')
            edits.append(edit)
            continue
        edit = Edit(item['op'], cid, item.get('file'), item.get('low'), item.get('text'), bool(item.get('checked')), n)
        edit.no_gear = item.get('gear') is False
        if edit.op == 'patch' and not edit.file:
            raise PlanError(f'edit {n}: a patch needs a "file"')
        if edit.op == 'rename' and not isinstance(edit.text, str):
            raise PlanError(f'edit {n}: a rename needs a "text" (blank resets the name)')
        if edit.op == COPY:
            try:
                edit.source = parse_source(item.get('source'))
            except PlanError as exc:
                raise PlanError(f'edit {n}: {exc}') from exc
            if edit.source is None:
                raise PlanError(f'edit {n}: a copy edit needs a "source" (the character to clone)')
        if edit.op in VALUE_OPS:
            if 'source' not in item:
                raise PlanError(f'edit {n}: a {edit.op} edit needs a "source" (null: back to the default)')
            try:
                edit.source = parse_source(item['source'])
            except PlanError as exc:
                raise PlanError(f'edit {n}: {exc}') from exc
        if edit.op == 'icon':
            edit.view, edit.origin = item.get('view'), item.get('origin')
            edit.fit = item.get('fit', icon_art.DEFAULT_FIT_MODE)
            edit.trim = item.get('trim', icon_import.DEFAULT_TRIM)
            if not edit.file:
                raise PlanError(f'edit {n}: an icon edit needs a "file" (the image)')
            if edit.view not in VIEWS:
                raise PlanError(f'edit {n}: an icon edit needs a "view" (front or side), not {edit.view!r}')
            if edit.fit not in icon_art.FIT_MODES:
                raise PlanError(f'edit {n}: "fit" must be one of {", ".join(icon_art.FIT_MODES)}')
            if not isinstance(edit.trim, bool):
                raise PlanError(f'edit {n}: "trim" must be true or false')
        edits.append(edit)
    return edits


def load_icon(edit: Edit) -> IconPick:
    """An icon edit's image, read and fitted (``icon_import``); a refusal raises ``PlanError``."""
    try:
        image, notes, warnings = icon_import.prepare(edit.file, edit.fit, edit.trim)
    except (icon_import.IconImportError, icon_art.IconArtError) as exc:
        raise PlanError(str(exc)) from exc
    return IconPick(image, edit.file, os.path.basename(edit.origin or edit.file), notes, warnings)


def _classify_edit(edit: Edit, classify_fn) -> Pair:
    pair = classify_fn(edit.file)
    if edit.low is None:
        return pair
    low = classify_fn(edit.low)
    if pair.high is None or low.chunk != pair.chunk or low.stem != pair.stem or low.low is None:
        raise PlanError(f'{os.path.basename(edit.low)} is not the Low partner of {os.path.basename(edit.file)}')
    return Pair(pair.chunk, pair.high, low.low, edit.low, pair.stem)


def _bundled_gear(edit: Edit, merged: list[Edit], notes: list[str], find_gear_fn,
                  classify_gear_fn) -> list[Edit] | str:
    """The ``equip`` edits that go behind a model patch into a new ID (module docstring, "Equipment"), or the reason
    the patch is refused (several candidate files for one slot file). Explicit (``user``) equip edits already
    merged for the slot keep their file; the notes say what was found and what was not."""
    pair = edit.pair
    found, ambiguous = find_gear_fn(pair.high or pair.low, pair.chunk)
    if ambiguous:
        file, paths = sorted(ambiguous.items())[0]
        return (f'{gear.label(file).lower()}: several .sluggie files of the character folder fit file {file} '
                f'({", ".join(os.path.basename(p) for p in paths)}); leave only one, or pick the gear by hand')
    out, lines = [], []
    for file in gear.FILES:
        if file not in found:
            lines.append(f'no {gear.label(file).lower()} found')
            continue
        if any(e.cid == edit.cid and e.op in EQUIP_OPS and e.gear_file == file and e.origin != 'bundled'
               for e in merged):
            lines.append(f'{gear.label(file).lower()}: the one you staged stays')
            continue
        try:
            pick = classify_gear_fn(found[file].path)
            target = gear.target_file(pick.source_file, file)
        except (PlanError, gear.GearError) as exc:
            return f'{gear.label(file).lower()}: {exc}'
        out.append(Edit('equip', edit.cid, found[file].path, index=edit.index, gear_file=target, origin='bundled',
                        gear=pick))
        lines.append(f'{gear.label(file).lower()}: {os.path.basename(found[file].path)}')
    notes.append(f'{_hex(edit.cid)}: gear taken along with {os.path.basename(pair.picked)}: ' + '; '.join(lines)
                 + ('' if len(out) == len(gear.FILES) else '; the missing ones stay as the slot has them'))
    return out


def merge_edits(edits: list[Edit], classify_fn=None, load_icon_fn=None, classify_gear_fn=None,
                find_gear_fn=None) -> tuple[list[Edit], list[str], list[tuple[Edit, str]]]:
    """``(merged, notes, refused)``: the edits in staging order after the slot rules:

    * at most one model edit (patch / clear / copy) per slot: a later one replaces an earlier one;
    * a copy (paste) drops every earlier edit of its slot: the slot becomes a clone of the source as a whole;
    * a Low-only pick after a pending High pick of the same character joins it as a pair; after another
      pending High pick or a pending clear it is refused (its High model would not be the one it binds into);
    * a clear drops the slot's earlier renames, stats and icon edits (it resets them); a later rename, voice or
      stats edit replaces an earlier one of the same slot, a later icon edit the earlier one of the same view
      (a patch that sets stats or brings portraits drops earlier stats / icon edits too, in ``plan_batch``, where
      it is known);
    * equipment: the last edit per slot file wins (an ``equip_clear`` replaces an ``equip`` and the reverse);
      a clear of the slot drops its earlier equipment edits (it resets the equipment); a model patch into a new ID
      replaces the gear an earlier patch brought along (``bundled`` edits) and stages its own behind it, never over
      a ``user`` edit of the same file."""
    classify_fn = classify_fn or classify
    load_icon_fn = load_icon_fn or load_icon
    classify_gear_fn = classify_gear_fn or classify_gear
    find_gear_fn = find_gear_fn or gear.find_gear
    merged: list[Edit] = []
    notes: list[str] = []
    refused: list[tuple[Edit, str]] = []

    for edit in edits:
        if edit.op == STAT_EDITS:
            merged.append(edit)
            continue
        if edit.op == STAT_RESET:
            same = next((e for e in merged if e.cid == edit.cid and e.op == STAT_RESET), None)
            if same is not None:
                merged.remove(same)
            merged.append(edit)
            continue
        if edit.op == COPY:
            for old in [e for e in merged if e.cid == edit.cid]:
                merged.remove(old)
                notes.append(f'{_hex(edit.cid)}: the pending {old.op} is dropped: the later paste replaces the '
                             'whole slot')
            merged.append(edit)
            continue
        if edit.op == 'patch':
            try:
                edit.pair = edit.pair or _classify_edit(edit, classify_fn)
            except PlanError as exc:
                refused.append((edit, str(exc)))
                continue
        if edit.op in EQUIP_OPS:
            if edit.op == 'equip':
                try:
                    edit.gear = edit.gear or classify_gear_fn(edit.file)
                    edit.gear_file = gear.target_file(edit.gear.source_file, edit.gear_file)
                except (PlanError, gear.GearError) as exc:
                    refused.append((edit, str(exc)))
                    continue
            same = next((e for e in merged if e.cid == edit.cid and e.op in EQUIP_OPS
                         and e.gear_file == edit.gear_file), None)
            if same is not None:
                merged.remove(same)
                notes.append(f'{_hex(edit.cid)}: the pending {gear.label(edit.gear_file).lower()} edit is replaced '
                             'by the later one')
            merged.append(edit)
            continue
        if edit.op == 'icon':
            try:
                edit.pick = edit.pick or load_icon_fn(edit)
            except PlanError as exc:
                refused.append((edit, str(exc)))
                continue
            same = next((e for e in merged if e.cid == edit.cid and e.op == 'icon' and e.view == edit.view), None)
            if same is not None:
                merged.remove(same)
                notes.append(f'{_hex(edit.cid)}: the pending {edit.view} portrait is replaced by the later one')
            merged.append(edit)
            continue
        earlier = (next((e for e in merged if e.cid == edit.cid and e.op in MODEL_OPS), None)
                   if edit.op in MODEL_OPS else None)
        if edit.op == 'patch' and edit.pair.high is None and earlier is not None:
            low_name = os.path.basename(edit.pair.low)
            if earlier.op in ('clear', COPY):
                what = 'clear' if earlier.op == 'clear' else 'paste'
                refused.append((edit, f'{low_name} has no High partner beside it, and a {what} of '
                                      f'{_hex(edit.cid)} is pending: patch the High model instead, or discard the '
                                      f'pending {what} first'))
                continue
            p = earlier.pair
            if p.high is None or p.chunk != edit.pair.chunk or p.stem != edit.pair.stem:
                refused.append((edit, f'{low_name} has no High partner beside it, and binds its textures by index '
                                      f'into its own High model, but the pending High model of {_hex(edit.cid)} '
                                      f'is {os.path.basename(p.high or p.low)}. Patch the High model into the '
                                      'slot (it brings its Low partner along).'))
                continue
            merged.remove(earlier)
            edit = Edit('patch', edit.cid, p.high, edit.pair.low, index=edit.index,
                        pair=Pair(p.chunk, p.high, edit.pair.low, edit.pair.picked, p.stem))
            notes.append(f'{_hex(edit.cid)}: {low_name} joins the pending {os.path.basename(p.high)} as its Low '
                         'partner')
        elif earlier is not None:
            merged.remove(earlier)
            notes.append(f'{_hex(edit.cid)}: the pending {earlier.op} is replaced by the later {edit.op}')
        bundled: list[Edit] = []
        if edit.op == 'patch' and edit.cid >= ids.FIRST_NEW and edit.pair.high is not None and not edit.no_gear:
            bundled = _bundled_gear(edit, merged, notes, find_gear_fn, classify_gear_fn)
            if isinstance(bundled, str):
                refused.append((edit, bundled))
                continue
        if edit.op == 'patch' and edit.cid >= ids.FIRST_NEW:
            for old in [e for e in merged if e.cid == edit.cid and e.op in EQUIP_OPS and e.origin == 'bundled']:
                merged.remove(old)
                notes.append(f'{_hex(edit.cid)}: the gear of the earlier patch is replaced by this patch\'s')
        if edit.op == 'clear':
            for old in [e for e in merged if e.cid == edit.cid and e.op in EQUIP_OPS]:
                merged.remove(old)
                notes.append(f'{_hex(edit.cid)}: the pending {gear.label(old.gear_file).lower()} edit is dropped: '
                             'the later clear resets the equipment')
            for rename in [e for e in merged if e.cid == edit.cid and e.op == 'rename']:
                merged.remove(rename)
                notes.append(f'{_hex(edit.cid)}: the rename to "{rename.text}" is dropped: the later clear resets '
                             'the name')
            for stats in [e for e in merged if e.cid == edit.cid and e.op == 'stats']:
                merged.remove(stats)
                notes.append(f'{_hex(edit.cid)}: the stats edit is dropped: the later clear resets the stats')
            for icon in [e for e in merged if e.cid == edit.cid and e.op == 'icon']:
                merged.remove(icon)
                notes.append(f'{_hex(edit.cid)}: the {icon.view} portrait edit is dropped: the later clear resets '
                             'the portraits')
        elif edit.op in VALUE_OPS + ('rename',):
            same = next((e for e in merged if e.cid == edit.cid and e.op == edit.op), None)
            if same is not None:
                merged.remove(same)
        merged.append(edit)
        merged.extend(bundled)
    return merged, notes, refused


@dataclass
class Batch:
    """The merged chain of a list of edits."""
    config: dict | None             # the derived config with every edit applied, or None: no roster rebuild
    commands: list[tuple] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    plans: list[tuple[Edit, Plan]] = field(default_factory=list)
    skipped: list[tuple[Edit, Plan]] = field(default_factory=list)   # clears of slots at their baseline
    refused: list[tuple[Edit, str]] = field(default_factory=list)
    extra_portraits: dict = field(default_factory=dict)
    portraits: dict = field(default_factory=dict)  # icon edits' portraits ({file name: RGBA image or PNG path})
    copy_files: dict = field(default_factory=dict)  # pastes' snapshot files ({name in copy_dir: data})

    @property
    def ok(self) -> bool:
        return not self.refused

    def to_json(self) -> dict:
        def section(edit, plan):
            return dict(plan.to_json(), edit=edit.to_json(), checked=edit.checked)
        return {'action': 'batch', 'ok': self.ok, 'rebuild': self.config is not None,
                'commands': [list(c) for c in self.commands], 'notes': self.notes, 'warnings': self.warnings,
                'edits': [section(e, p) for e, p in self.plans],
                'skipped': [section(e, p) for e, p in self.skipped],
                'refused': [{'edit': e.to_json(), 'target': _hex(e.cid), 'error': error} for e, error in self.refused],
                'merged': [e.to_json() for e, _p in self.plans]}


def plan_batch(st: dict, config: dict, edits: list[Edit], env: Env, state_file: str,
               names_text: dict | None = None, skip_checked: bool = False, classify_fn=None,
               load_icon_fn=None, classify_gear_fn=None, find_gear_fn=None) -> Batch:
    """One chain for every edit: each edit is planned on the config the previous one left
    (``plan_patch`` / ``plan_clear``), then the chain is

    1. one ``--patch ... --validate-only`` per patched slot (``skip_checked``: not for edits whose check
       already passed, the GUI's staging dry run);
    2. one ``--apply-stat-edits`` with every staged stat edit file (they hold values for the DOL as it is now, and
       the rebuild carries them over);
    3. one ``--roster --state`` when the merged config differs from the derived one;
    3b. one ``--apply-stat-edits`` with the pastes' stat values (``copy:`` items), after the rebuild;
    4. each slot's ``--patch`` / ``--unpatch`` / ``--write-slot-blocks`` / ``--write-slot-equipment``, in staging
       order;
    5. one ``--roster-state``.

    Any refused edit refuses the whole batch: no commands, no config (``refused`` names them all)."""
    merged, notes, refused = merge_edits(edits, classify_fn, load_icon_fn, classify_gear_fn, find_gear_fn)
    batch = Batch(None, notes=notes, refused=refused)
    current = config
    portraits_touched = set()                       # slots whose portraits an earlier edit of the batch changed
    for edit in merged:
        try:
            if edit.op == 'patch':
                plan = plan_patch(st, current, edit.cid, edit.pair, env, state_file, names_text)
            elif edit.op == 'icon':
                plan = plan_icon(st, current, edit.cid, edit.view, edit.pick, env, state_file,
                                 compare=edit.cid not in portraits_touched)
            elif edit.op == 'equip':
                plan = plan_equip(st, current, edit.cid, edit.gear, edit.gear_file, env, state_file,
                                  edit.origin or 'user')
            elif edit.op == 'equip_clear':
                plan = plan_equip_clear(st, current, edit.cid, edit.gear_file, env, state_file)
            elif edit.op == 'rename':
                plan = plan_rename(st, current, edit.cid, edit.text, state_file)
            elif edit.op == 'stats':
                plan = plan_stats(st, current, edit.cid, edit.source, state_file, names_text, env)
            elif edit.op == 'voice':
                plan = plan_voice(st, current, edit.cid, edit.source, state_file, names_text)
            elif edit.op == STAT_EDITS:
                plan = plan_stat_edits(edit.file, env)
            elif edit.op == COPY:
                plan = plan_copy(st, current, config, edit.cid, edit.source, env, state_file, names_text)
                if any(e.cid == edit.source for e in merged if e is not edit):
                    plan.notes.append(f'pending edits of {_hex(edit.source)} are not copied: the paste takes what '
                                      'the game holds now')
            elif edit.op == STAT_RESET:
                plan = plan_stat_reset(st, edit.cid, env,
                                       after_files=any(e.op == STAT_EDITS for e, _p in batch.plans))
            else:
                plan = plan_clear(st, current, edit.cid, state_file, env)
        except PlanError as exc:
            batch.refused.append((edit, str(exc)))
            continue
        if plan.nothing:
            batch.skipped.append((edit, plan))
            continue
        if plan.config is not None:
            current = plan.config
        if edit.op == 'patch' and 'stats' in plan.effects:
            for earlier in [(e, p) for e, p in batch.plans if e.op == 'stats' and e.cid == edit.cid]:
                batch.plans.remove(earlier)
                batch.notes.append(f'{_hex(edit.cid)}: the stats edit is dropped: the later patch sets the stats')
        if edit.op == 'patch' and 'portraits' in plan.effects:     # its model's portraits replace both views
            for earlier in [(e, p) for e, p in batch.plans if e.op == 'icon' and e.cid == edit.cid]:
                batch.plans.remove(earlier)
                batch.notes.append(f'{_hex(edit.cid)}: the {earlier[0].view} portrait edit is dropped: the later '
                                   'patch brings its model\'s portraits')
        if edit.op in ('clear', 'icon') or 'portraits' in plan.effects or 'portrait_note' in plan.effects:
            portraits_touched.add(edit.cid)
        batch.portraits.update(plan.portraits)
        batch.copy_files.update(plan.copy_files)
        if edit.op == 'voice':
            square = st['squares'][_character(st, edit.cid)['square']]
            for earlier in [(e, p) for e, p in batch.plans
                            if e.op == 'voice' and e.cid != edit.cid and e.cid in square['members']]:
                batch.plans.remove(earlier)
                batch.notes.append(f'{_hex(edit.cid)}: the earlier voice edit of its square is replaced')
        batch.plans.append((edit, plan))
        batch.warnings += plan.warnings
        batch.extra_portraits.update(plan.extra_portraits)
    if batch.refused:
        batch.refused.sort(key=lambda r: r[0].index)
        return batch
    if not batch.plans:
        return batch                                 # only clears of slots at their baseline: nothing to run
    for edit, plan in batch.plans:
        if not (skip_checked and edit.checked):
            batch.commands += [c for c in plan.commands if '--validate-only' in c]
    stat_items = [p for _edit, plan in batch.plans for c in plan.commands if c[0] == '--apply-stat-edits'
                  for p in c[1:]]                   # files and reset:0xNN items, in staging order
    stat_files = [p for p in stat_items if not p.startswith(COPY_PREFIX)]
    stat_copies = [p for p in stat_items if p.startswith(COPY_PREFIX)]
    if stat_files:                                  # one step: every file is checked against the DOL before writing
        batch.commands.append(('--apply-stat-edits', *stat_files))
    if current != config:
        batch.config = current
        batch.commands.append(('--roster', '--state', state_file))
    if stat_copies:                                 # pastes: the target's rows follow its new stats source by now
        batch.commands.append(('--apply-stat-edits', *stat_copies))
    for _edit, plan in batch.plans:
        batch.commands += [c for c in plan.commands if c[0] in SLOT_WRITES and '--validate-only' not in c]
    batch.commands.append(('--roster-state',))
    return batch
