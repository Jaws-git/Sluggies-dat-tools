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
        en = (char.get('name') or {}).get('en')
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
        if new != config:
            plan.config = new
        _prepare(plan, state_file, [])
        plan.commands.append(('--unpatch', '--target-id', _hex(cid)))
        plan.notes.insert(0, f'{target_name}: vanilla High and Low models from 1_Input')
        plan.effects['model'] = 'vanilla High and Low models'
    plan.commands.append(('--roster-state',))
    return plan


def _display(char: dict) -> str:
    name = char.get('default_name') or (char.get('name') or {}).get('en')
    return f'{name} ({_hex(char["id"])})' if name and name != names.UNNAMED else _hex(char['id'])


# --------------------------------------------------------------------------
# Batches: staged edits, one chain (decision 14)
# --------------------------------------------------------------------------

MODEL_OPS = ('patch', 'clear')
LATER_OPS = {'rename': 5, 'voice': 7, 'stats': 7}     # ops a later plan phase brings: {op: phase}


@dataclass
class Edit:
    """One staged edit (an entry of the edits file)."""
    op: str                         # 'patch' / 'clear'; later phases: 'rename', 'voice', 'stats'
    cid: int
    file: str | None = None         # patch: the picked .sluggie (a joined pair: the High model)
    low: str | None = None          # patch: a Low pick joined to a pending High pick
    text: str | None = None         # rename
    checked: bool = False           # its build check already passed when it was staged (dry runs skip it)
    index: int = 0                  # position in the edits file (1-based)
    pair: Pair | None = None

    def to_json(self) -> dict:
        out = {'op': self.op, 'id': _hex(self.cid)}
        for key in ('file', 'low', 'text'):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        return out


def parse_edits(data) -> list[Edit]:
    """The edits file's list (``{"edits": [...]}`` or the bare list); malformed entries raise ``PlanError``."""
    items = data.get('edits') if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise PlanError('the edits file holds no list of edits')
    edits = []
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict) or item.get('op') not in MODEL_OPS + tuple(LATER_OPS) or item.get('id') is None:
            raise PlanError(f'edit {n} is not an edit: {item!r}')
        try:
            cid = ids._number(item['id'], 'id')
        except (ValueError, TypeError) as exc:
            raise PlanError(f'edit {n}: {exc}') from exc
        edit = Edit(item['op'], cid, item.get('file'), item.get('low'), item.get('text'), bool(item.get('checked')), n)
        if edit.op == 'patch' and not edit.file:
            raise PlanError(f'edit {n}: a patch needs a "file"')
        edits.append(edit)
    return edits


def _classify_edit(edit: Edit, classify_fn) -> Pair:
    pair = classify_fn(edit.file)
    if edit.low is None:
        return pair
    low = classify_fn(edit.low)
    if pair.high is None or low.chunk != pair.chunk or low.stem != pair.stem or low.low is None:
        raise PlanError(f'{os.path.basename(edit.low)} is not the Low partner of {os.path.basename(edit.file)}')
    return Pair(pair.chunk, pair.high, low.low, edit.low, pair.stem)


def merge_edits(edits: list[Edit], classify_fn=None) -> tuple[list[Edit], list[str], list[tuple[Edit, str]]]:
    """``(merged, notes, refused)``: the edits in staging order after the slot rules of section 4g:

    * at most one model edit (patch / clear) per slot: a later one replaces an earlier one;
    * a Low-only pick after a pending High pick of the same character joins it as a pair; after another
      pending High pick or a pending clear it is refused (its High model would not be the one it binds into);
    * a clear drops the slot's earlier renames (it resets the name); a later rename replaces an earlier one."""
    classify_fn = classify_fn or classify
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
        elif edit.op in LATER_OPS:
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
               names_text: dict | None = None, skip_checked: bool = False, classify_fn=None) -> Batch:
    """One chain for every edit (section 4g): each edit is planned on the config the previous one left
    (``plan_patch`` / ``plan_clear``), then the chain is

    1. one ``--patch ... --validate-only`` per patched slot (``skip_checked``: not for edits whose check
       already passed, the GUI's staging dry run);
    2. one ``--roster --state`` when the merged config differs from the derived one;
    3. each slot's ``--patch`` / ``--unpatch``, in staging order;
    4. one ``--roster-state``.

    Any refused edit refuses the whole batch: no commands, no config (``refused`` names them all)."""
    merged, notes, refused = merge_edits(edits, classify_fn)
    batch = Batch(None, notes=notes, refused=refused)
    current = config
    for edit in merged:
        try:
            if edit.op in LATER_OPS:
                raise PlanError(f'{edit.op} edits come with plan Phase {LATER_OPS[edit.op]}')
            if edit.op == 'patch':
                plan = plan_patch(st, current, edit.cid, edit.pair, env, state_file, names_text)
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
