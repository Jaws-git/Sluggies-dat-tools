"""Patch a slot / clear a slot (GUI character grid, Phases 4e/4g): the apply chain as a list of commands.

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

Patch (decisions 4-7 of the plan):

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

Clear (decision 9): a stock slot gets its vanilla HP and ``L_`` back and loses
replaced portraits (``stock_icons``) and another stats source
(``stock_stats``); a new ID gets a fresh copy of its template's files, the
template's stats, the "Empty slot" name and portraits. The square voice
stays.

Stats and voice (plan Phase 7, ``plan_stats`` / ``plan_voice``): any slot can
play with another stock player's stats (new IDs: ``ids[].stats``; stock IDs:
``stock_stats``), and any square can speak with another square's voice (new
squares: ``grid.squares[k].voice``, which reaches only the square-only new
IDs on it; stock squares: ``stock_voices``, which reaches the whole species:
its wheel, spare rows and new IDs on it). A voice is named by a character;
the square takes its family's voice (the base character of its species).

Portraits (plan Phase 8, ``plan_icon``): one view (front or side) of a slot
takes a user's image, fitted to 48x51 (``icon_import``). The other view keeps
what the slot has: its own cell (by name, so its CMPR blocks stay), its
model's portrait, or, without own portraits, what the game shows now (the
template's or neighbour's crop; a stock ID's stock art), which then becomes
the slot's own. Miis have no portrait records and are refused.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field

try:
    from . import icon_art, icon_import, icons, ids, model_icons, names, open_slot, voices, wheels
except ImportError:
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
    if model.get('FileIndex') not in (HIGH_FILE, LOW_FILE):
        raise PlanError(f'{os.path.basename(path)} is file {model.get("FileIndex")} of its directory, not a '
                        'character model (only the High model, file 0, and its Low partner, file 1)')
    partner = _partner(path, chunk, is_low, stem)
    high, low = (partner, path) if is_low else (path, partner)
    return Pair(chunk, high, low, path, stem)


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

    def same_portrait(self, char: dict, view: str, image) -> bool:
        """Whether the game shows exactly ``image`` (48x51 RGBA) as the slot's ``view`` portrait now."""
        return False

    def shown_portrait(self, char: dict, view: str):
        """The slot's ``view`` portrait as the game shows it now (RGBA image), or None when it cannot be read."""
        return None

    def stock_portrait(self, cid: int, view: str):
        """A stock ID's own ``view`` portrait from the stock icon bank (RGBA image), or None."""
        return None


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
    # What the slot shows once the edit is written (the GUI's pending lines): 'model', 'name', 'stats', 'voice'
    # (display text) and 'portraits' ({'front', 'side'}: PNG paths to preview, or None: unchanged)
    effects: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        out = {'action': self.action, 'target': _hex(self.target), 'rebuild': self.config is not None,
               'commands': [list(c) for c in self.commands], 'notes': self.notes, 'warnings': self.warnings,
               'nothing': self.nothing, 'effects': self.effects}
        if self.pair is not None:
            out['source'] = _hex(self.pair.source)
            out['files'] = {'high': self.pair.high, 'low': self.pair.low, 'picked': self.pair.picked}
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
            else:
                plan.notes.append(f'{target_name} gets an own model directory: a copy of {source_name}\'s files')
        square = st['squares'][char['square']]
        if square['kind'] == 'new':
            if entry.get('stats') != _hex(source):
                entry['stats'] = _hex(source)
                plan.notes.append(f'{target_name} takes {source_name}\'s stats (new square)')
                plan.effects['stats'] = source_name
            k, sq = _square_config(new, cid) or (None, None)
            # set in the game, or by an earlier edit of the same batch (decision 5 counts pending patches)
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
    plan.commands.append(('--roster-state',))
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
               names_text: dict | None = None) -> Plan:
    """The chain that lets slot ``cid`` play with stock player ``source``'s stats (``None``: its default, a new
    ID's template's, a stock ID's own). The stats rows only: model, size, voice and name stay."""
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


def _display(char: dict) -> str:
    name = char.get('default_name') or (char.get('name') or {}).get('en')
    return f'{name} ({_hex(char["id"])})' if name and name != names.UNNAMED else _hex(char['id'])


# --------------------------------------------------------------------------
# Batches: staged edits, one chain (decision 14)
# --------------------------------------------------------------------------

MODEL_OPS = ('patch', 'clear')
VALUE_OPS = ('voice', 'stats')                        # the last one per slot (voice: per square) wins
SLOT_OPS = MODEL_OPS + ('rename',) + VALUE_OPS + ('icon',)
DEFAULT_WORDS = ('', '-', 'default')                  # an edit's "source" that resets (CLI text)


@dataclass
class Edit:
    """One staged edit (an entry of the edits file)."""
    op: str                         # 'patch' / 'clear' / 'rename' / 'voice' / 'stats' / 'icon'
    cid: int                        # the slot (voice: any slot of the square)
    file: str | None = None         # patch: the picked .sluggie (a joined pair: the High model); icon: the image
    low: str | None = None          # patch: a Low pick joined to a pending High pick
    text: str | None = None         # rename
    checked: bool = False           # its build check already passed when it was staged (dry runs skip it)
    index: int = 0                  # position in the edits file (1-based)
    pair: Pair | None = None
    source: int | None = None       # voice / stats: the character to take them from (None: back to the default)
    view: str | None = None         # icon: 'front' or 'side'
    fit: str = icon_art.DEFAULT_FIT_MODE                  # icon: contain / cover / strict
    trim: bool = icon_import.DEFAULT_TRIM                 # icon: crop the transparent border first
    origin: str | None = None       # icon: the user's file, when ``file`` is the GUI's normalised copy
    pick: IconPick | None = None    # icon: the loaded image (``merge_edits``)

    def to_json(self) -> dict:
        out = {'op': self.op, 'id': _hex(self.cid)}
        for key in ('file', 'low', 'text', 'view', 'origin'):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.op in VALUE_OPS:
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
        if not isinstance(item, dict) or item.get('op') not in SLOT_OPS or item.get('id') is None:
            raise PlanError(f'edit {n} is not an edit: {item!r}')
        try:
            cid = ids._number(item['id'], 'id')
        except (ValueError, TypeError) as exc:
            raise PlanError(f'edit {n}: {exc}') from exc
        edit = Edit(item['op'], cid, item.get('file'), item.get('low'), item.get('text'), bool(item.get('checked')), n)
        if edit.op == 'patch' and not edit.file:
            raise PlanError(f'edit {n}: a patch needs a "file"')
        if edit.op == 'rename' and not isinstance(edit.text, str):
            raise PlanError(f'edit {n}: a rename needs a "text" (blank resets the name)')
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


def merge_edits(edits: list[Edit], classify_fn=None,
                load_icon_fn=None) -> tuple[list[Edit], list[str], list[tuple[Edit, str]]]:
    """``(merged, notes, refused)``: the edits in staging order after the slot rules of section 4g:

    * at most one model edit (patch / clear) per slot: a later one replaces an earlier one;
    * a Low-only pick after a pending High pick of the same character joins it as a pair; after another
      pending High pick or a pending clear it is refused (its High model would not be the one it binds into);
    * a clear drops the slot's earlier renames, stats and icon edits (it resets them); a later rename, voice or
      stats edit replaces an earlier one of the same slot, a later icon edit the earlier one of the same view
      (a patch that sets stats or brings portraits drops earlier stats / icon edits too, in ``plan_batch``, where
      it is known)."""
    classify_fn = classify_fn or classify
    load_icon_fn = load_icon_fn or load_icon
    merged: list[Edit] = []
    notes: list[str] = []
    refused: list[tuple[Edit, str]] = []

    for edit in edits:
        if edit.op == 'patch':
            try:
                edit.pair = edit.pair or _classify_edit(edit, classify_fn)
            except PlanError as exc:
                refused.append((edit, str(exc)))
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
            if earlier.op == 'clear':
                refused.append((edit, f'{low_name} has no High partner beside it, and a clear of {_hex(edit.cid)} '
                                      'is pending: patch the High model instead, or discard the pending clear '
                                      'first'))
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
        if edit.op == 'clear':
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
               load_icon_fn=None) -> Batch:
    """One chain for every edit (section 4g): each edit is planned on the config the previous one left
    (``plan_patch`` / ``plan_clear``), then the chain is

    1. one ``--patch ... --validate-only`` per patched slot (``skip_checked``: not for edits whose check
       already passed, the GUI's staging dry run);
    2. one ``--roster --state`` when the merged config differs from the derived one;
    3. each slot's ``--patch`` / ``--unpatch``, in staging order;
    4. one ``--roster-state``.

    Any refused edit refuses the whole batch: no commands, no config (``refused`` names them all)."""
    merged, notes, refused = merge_edits(edits, classify_fn, load_icon_fn)
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
            elif edit.op == 'rename':
                plan = plan_rename(st, current, edit.cid, edit.text, state_file)
            elif edit.op == 'stats':
                plan = plan_stats(st, current, edit.cid, edit.source, state_file, names_text)
            elif edit.op == 'voice':
                plan = plan_voice(st, current, edit.cid, edit.source, state_file, names_text)
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
    if current != config:
        batch.config = current
        batch.commands.append(('--roster', '--state', state_file))
    for _edit, plan in batch.plans:
        batch.commands += [c for c in plan.commands if c[0] in ('--patch', '--unpatch') and '--validate-only' not in c]
    batch.commands.append(('--roster-state',))
    return batch
