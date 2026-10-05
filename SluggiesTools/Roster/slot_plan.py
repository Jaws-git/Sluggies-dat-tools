"""Patch a slot / clear a slot (GUI character grid, Phase 4e): the apply chain as a list of commands.

One GUI action is one chain of ``start.py`` commands, run in order and
stopped at the first failure (``start.py --patch-slot`` / ``--clear-slot``
run the same list; the GUI runs those):

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
  is allowed only when the slot's high-poly model is its partner after the
  rebuild (``L_`` binds textures by index into its HP's TEX).
* **Portraits:** the source's exported ``icon/SideIcon.png`` +
  ``FrontIcon.png`` (``model_icons``); when one is missing the slot keeps its
  portraits and the plan says so.

Clear (decision 9): a stock slot gets its vanilla HP and ``L_`` back and loses
replaced portraits (``stock_icons``); a new ID gets a fresh copy of its
template's files, the template's stats, the "Empty slot" name and portraits.
The square voice stays.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field

try:
    from . import icons, ids, model_icons, names, open_slot
except ImportError:
    import icons
    import ids
    import model_icons
    import names
    import open_slot

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
                        'character model (only the high-poly model, file 0, and its L_ partner, file 1)')
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
    pair: Pair | None = None        # patch: the picked file and its partner

    def to_json(self) -> dict:
        out = {'action': self.action, 'target': _hex(self.target), 'rebuild': self.config is not None,
               'commands': [list(c) for c in self.commands], 'notes': self.notes, 'warnings': self.warnings}
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
        entry = _entry(new, 'ids', cid)
        if entry is None:
            raise PlanError(f'{_hex(cid)} has no ids entry in the derived config')
        kept = char.get('own_model_dir') and char.get('model_source') == source
        if kept:
            plan.notes.append(f'{target_name} keeps its own model directory (copies of {source_name}\'s files)')
        else:
            entry['model'] = {'from': _hex(source)}
            if char.get('own_model_dir'):
                plan.notes.append(f'{target_name}\'s own model directory is copied afresh from {source_name} (it '
                                  f'held {_hex(char["model_source"])}\'s files; models patched into it are dropped)')
            else:
                plan.notes.append(f'{target_name} gets an own model directory: a copy of {source_name}\'s files')
        if pair.high is None and kept:  # (a fresh copy's HP is the L_ model's own vanilla partner)
            _check_low_alone(env, cid, pair, target_name)
        square = st['squares'][char['square']]
        if square['kind'] == 'new':
            if entry.get('stats') != _hex(source):
                entry['stats'] = _hex(source)
                plan.notes.append(f'{target_name} takes {source_name}\'s stats (new square)')
            if square.get('voice_set') is None:
                k, sq = _square_config(new, cid) or (None, None)
                if sq is None:
                    raise PlanError(f'{_hex(cid)}: its square is missing from the derived config')
                members = sq['members'] if isinstance(sq, dict) else sq
                new['grid']['squares'][k] = {'members': list(members), 'voice': _hex(source)}
                plan.notes.append(f'the square\'s voice becomes {source_name}\'s (first patch on it)')
        else:
            plan.notes.append(f'{target_name} keeps its stats and voice (stock square)')
        en = (char.get('name') or {}).get('en')
        source_text = (names_text or {}).get(source)
        if en in (None, '', names.UNNAMED, open_slot.SLOT_NAME['en']) and source_text and source_text.get('en'):
            entry['name'] = {lang: source_text.get(lang) or source_text['en'] for lang in open_slot.SLOT_NAME}
            plan.notes.append(f'{target_name} is named "{source_text["en"]}" (it was an open slot)')
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

    if as_low:
        plan.warnings.append(f'{os.path.basename(pair.high)} has no L_ partner beside it: it is used as the '
                             'low-poly model too, so the slot loads it twice on the field (counts twice against '
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

    if new != config:
        plan.config = new
    _prepare(plan, state_file, files)
    plan.commands.append(('--patch', *files, '--target-id', _hex(cid)) + (('--as-low',) if as_low else ()))
    plan.commands.append(('--roster-state',))
    plan.notes.insert(0, f'{" + ".join(os.path.basename(f) for f in files)} -> {target_name}'
                         + (' (HP as the low-poly model too)' if as_low else ''))
    return plan


def _check_low_alone(env: Env, cid: int, pair: Pair, target_name: str) -> None:
    stem = env.current_high_stem(cid)
    if stem != pair.stem:
        raise PlanError(
            f'{os.path.basename(pair.low)} has no high-poly partner beside it, and binds its textures by index into '
            f'its own high-poly model, but {target_name}\'s high-poly model is {stem or "nothing readable"}. Patch '
            'the high-poly model into the slot (it brings its L_ partner along).')


def plan_clear(st: dict, config: dict, cid: int, state_file: str) -> Plan:
    """The chain that returns slot ``cid`` to its baseline (module docstring)."""
    char = _character(st, cid)
    new = copy.deepcopy(config)
    plan = Plan('clear', cid, None)
    target_name = _display(char)
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
        if new != config:
            plan.config = new
        _prepare(plan, state_file, [])
        plan.commands.append(('--unpatch', '--target-id', _hex(cid)))
        plan.notes.insert(0, f'{target_name}: vanilla high- and low-poly models from 1_Input')
    plan.commands.append(('--roster-state',))
    return plan


def _display(char: dict) -> str:
    name = char.get('default_name') or (char.get('name') or {}).get('en')
    return f'{name} ({_hex(char["id"])})' if name and name != names.UNNAMED else _hex(char['id'])
