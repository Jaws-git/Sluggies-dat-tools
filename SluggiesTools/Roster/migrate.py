"""Switch roster presets and keep the slots' customisations (``start.py --roster --config FILE``).

A roster run resets the roster to vanilla before it builds the preset
(``runner.py``), so on its own it keeps only what lives outside the roster
config: models patched into stock slots (their directory records) and the
stat edits (``StatEditor/carry.py``). ``plan_switch`` instead merges the preset
with the derived config of the game as it is now (``derive.py``) and plans one
chain, the same read -> rebuild the GUI's slot edits and pack loads use.

Rules, by character ID:

* **On the old and the new grid:** the slot keeps its customisations; where it
  sits (``template``, ``wheel``, ``swatch``, square, wheel order) comes from the
  preset.

  - new ID: its own model directory (``model`` with the current DAT
    ``routes``, so its models and equipment stay byte for byte) unless it only
    holds an unchanged copy of its old template's files; its ``stats``; its
    name unless it is the open-slot name; its portraits unless they are the
    open-slot ones. The portraits' ``like`` stays only when it was not the old
    template (else the new template is the default).
  - spare row (``wheels``): its name and portraits.
  - stock ID: its ``stock_names``, ``stock_icons`` and ``stock_stats`` entries
    (they replace the preset's for that ID). Stock squares keep their
    ``stock_voices``; a new square whose head is a kept new ID takes the voice
    of the old new square that held it.
  - stat edits: carried by the rebuild (``carry.py``), as before.

  A new ID's own directory holds all of its source's files (models, equipment,
  animations), so a different template does not change what poses its
  models; stock IDs never change directory. No block moves, so nothing needs
  checking again.
* **Only on the old grid:** the slot loses everything. A new ID leaves the
  config (its directory is freed by the reset, its stat edits are dropped by
  the rebuild). A stock-range ID (the spare rows 0x47-0x4C) gets its stat edits
  cleared (``--apply-stat-edits reset:0xNN``), its models and changed
  equipment back to their baseline (``--unpatch --target-id``; an unused
  character's split copies count as changed, a clear gives fresh ones), and
  its name, portraits and stats source are not in the config.
* **Only on the new grid:** the preset's entry (new IDs: the open slot).

The chain: one stat reset step, the unpatches (while the slots are still on the
grid), the rebuild (``--roster --state``; a stock result is ``--roster --remove
--keep-stat-edits``), one ``--roster-state``. The preset's own portraits are
copied beside the derived ones under ``PRESET_ICON_PREFIX``, so the two sets
never clash.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field

try:
    from . import icons, ids, names, open_slot, slot_plan, voices, wheels
except ImportError:
    import icons
    import ids
    import names
    import open_slot
    import slot_plan
    import voices
    import wheels

PRESET_ICON_PREFIX = 'preset_'
OPEN_NAMES = (None, '', names.UNNAMED, open_slot.SLOT_NAME['en'])
STOCK_ENTRIES = ((names.STOCK_KEY, 'name'), (icons.STOCK_KEY, 'icon'), (ids.STOCK_STATS_KEY, 'stats'),
                 (voices.STOCK_KEY, 'voice'))
FIELD_LABELS = {'model': 'models', 'equipment': 'equipment', 'name': 'name', 'icon': 'portraits',
                'stats': 'stats source', 'voice': 'square voice', 'stat_edits': 'stat edits'}


class SwitchError(ValueError):
    pass


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


@dataclass
class SwitchPlan:
    config: dict                                         # the merged config the rebuild reads
    preset_icons: dict = field(default_factory=dict)     # {file name in the merged icon folder: preset PNG path}
    commands: list = field(default_factory=list)
    carried: dict = field(default_factory=dict)          # {id: [field, ...]}: kept slots and what they keep
    reset: dict = field(default_factory=dict)            # {id: [field, ...]}: stock-range slots leaving the grid
    dropped: dict = field(default_factory=dict)          # {id: [field, ...]}: new IDs leaving the roster
    remove: bool = False                                 # the merged config is stock: the rebuild is a reset
    notes: list = field(default_factory=list)

    def to_json(self) -> dict:
        def rows(items):
            return [{'id': _hex(c), 'fields': list(items[c])} for c in sorted(items)]
        return {'action': 'switch_roster', 'ok': True, 'remove': self.remove,
                'commands': [list(c) for c in self.commands], 'carried': rows(self.carried),
                'reset': rows(self.reset), 'dropped': rows(self.dropped), 'notes': self.notes}


def labels(fields_) -> str:
    """``models, name, portraits`` for a field list."""
    return ', '.join(FIELD_LABELS.get(f, f) for f in fields_)


# --------------------------------------------------------------------------
# Who is on a grid
# --------------------------------------------------------------------------

def on_grid(config: dict) -> set[int]:
    """The character IDs a config puts on the grid: the stock players 0x00-0x46 (always), the spare rows its
    ``wheels`` lists and its new IDs (``ids``; a square-only one is on a new square by ``parse_ids``' rule)."""
    return (set(range(icons.STOCK_ICON_END)) | set(wheels.parse_wheels(config) or {})
            | {n.id for n in ids.parse_ids(config)})


def _entries(config: dict, key: str) -> dict[int, dict]:
    return {ids._number(e['id'], key): e for e in config.get(key) or []
            if isinstance(e, dict) and e.get('id') is not None}


def _number_new_ids(config: dict) -> None:
    """Give every ``ids`` entry its ``id`` (as ``parse_ids`` numbers the ones without), so entries match by ID."""
    used: set[int] = set()
    for n, entry in enumerate(config.get('ids') or []):
        if entry.get('id') is None:
            entry['id'] = _hex(next(i for i in range(ids.FIRST_NEW, ids.MAX_ID + 2) if i not in used))
        used.add(ids._number(entry['id'], f'ids[{n}].id'))


# --------------------------------------------------------------------------
# What a slot holds
# --------------------------------------------------------------------------

def changed_equipment(char: dict, unknown_counts: bool = False) -> list[int]:
    """Files 2-5 not at their baseline (``vanilla`` False; with ``unknown_counts`` also None: split copies)."""
    out = []
    for entry in (char.get('equipment') or {}).values():
        flag = entry.get('vanilla')
        if flag is False or (unknown_counts and flag is None):
            out.append(entry['file'])
    return sorted(out)


def keeps_own_directory(char: dict | None, env: slot_plan.Env) -> bool:
    """Whether a new ID's own model directory holds anything of its own: another character's files, or patched
    models or equipment (an unchanged copy of its template's files does not count)."""
    if not char or not char.get('own_model_dir'):
        return False
    if char.get('model_source') != char.get('template'):
        return True
    return bool(changed_equipment(char)) or not env.models_at_baseline(char)


def _own_name(entry: dict | None) -> bool:
    name = (entry or {}).get('name')
    return isinstance(name, dict) and name.get('en') not in OPEN_NAMES


def _own_icon(entry: dict | None, char: dict | None, env: slot_plan.Env) -> bool:
    return isinstance((entry or {}).get('icon'), dict) and not (char is not None
                                                                and env.shows_open_slot_portraits(char))


def new_id_customisations(char: dict | None, entry: dict | None, env: slot_plan.Env) -> list[str]:
    """What a new ID holds of its own (``FIELD_LABELS`` keys, stat edits not included)."""
    out = ['model'] if keeps_own_directory(char, env) else []
    if (entry or {}).get('stats') is not None:
        out.append('stats')
    if _own_name(entry):
        out.append('name')
    if _own_icon(entry, char, env):
        out.append('icon')
    return out


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------

def _prefix_preset_icons(config: dict, icon_dir: str, plan: SwitchPlan) -> None:
    for key in ('ids', 'wheels', icons.STOCK_KEY):
        for entry in config.get(key) or []:
            icon = entry.get('icon') if isinstance(entry, dict) else None
            if not isinstance(icon, dict) or icon.get('model') is not None:
                continue
            for view in ('side', 'front'):
                name = icon.get(view)
                if not isinstance(name, str) or not name or os.path.basename(name) != name:
                    raise SwitchError(f'{key} {entry.get("id")}: portrait {name!r} must be a plain PNG file name '
                                      '(in 1_Input/_Icons)')
                plan.preset_icons[PRESET_ICON_PREFIX + name] = os.path.join(icon_dir, name)
                icon[view] = PRESET_ICON_PREFIX + name


def _merge_new_id(entry: dict, old: dict, char: dict | None, env: slot_plan.Env) -> list[str]:
    kept = new_id_customisations(char, old, env)
    if 'model' in kept:
        entry['model'] = copy.deepcopy(old['model'])
    if 'stats' in kept:
        entry['stats'] = old['stats']
    if 'name' in kept:
        entry['name'] = copy.deepcopy(old['name'])
    if 'icon' in kept:
        icon = {k: v for k, v in old['icon'].items() if k != 'like'}
        like = old['icon'].get('like')
        if like is not None and ids._number(like, 'like') != ids._number(old['template'], 'template'):
            icon['like'] = like
        entry['icon'] = icon
    return kept


def plan_switch(preset: dict, game_st: dict, game_derived, env: slot_plan.Env, state_file: str,
                icon_dir: str = icons.ICON_DIR) -> SwitchPlan:
    """The merged config and chain that switch the game to ``preset`` (module docstring). ``game_st``: the read
    state with ``vanilla`` flags (``state_cli.add_vanilla_flags``) and ``stat_edits``; ``game_derived``: its
    derived config (``derive.Derived``); ``icon_dir``: where the preset's portrait files are."""
    config = copy.deepcopy(preset)
    plan = SwitchPlan(config)
    try:
        _number_new_ids(config)
        present = on_grid(config)
    except (ValueError, StopIteration) as exc:
        raise SwitchError(f'the roster configuration is not valid: {exc}') from exc
    _prefix_preset_icons(config, icon_dir, plan)
    derived = game_derived.config
    chars = {c['id']: c for c in game_st.get('characters') or []}
    edited = {int(k, 16) for k in ((game_st.get('stat_edits') or {}).get('characters') or {})}
    old_new, old_spares = _entries(derived, 'ids'), _entries(derived, 'wheels')

    def keep(cid, what):
        plan.carried.setdefault(cid, [])
        if what not in plan.carried[cid]:
            plan.carried[cid].append(what)

    # new IDs and spare rows on both grids
    for cid, entry in sorted(_entries(config, 'ids').items()):
        if cid in old_new:
            for what in _merge_new_id(entry, old_new[cid], chars.get(cid), env):
                keep(cid, what)
    for cid, entry in sorted(_entries(config, 'wheels').items()):
        for key in ('name', 'icon'):
            if (old_spares.get(cid) or {}).get(key) is not None:
                entry[key] = copy.deepcopy(old_spares[cid][key])
                keep(cid, key)

    # stock entries (names, portraits, stats sources, square voices): the game's replace the preset's
    lost_stock: dict[int, list] = {}
    for key, what in STOCK_ENTRIES:
        old = _entries(derived, key)
        if not old:
            continue
        stays = {c: e for c, e in old.items() if c in present}
        for c in old:
            if c not in present:
                lost_stock.setdefault(c, []).append(what)
        if not stays:
            continue
        mine = [e for e in config.get(key) or []
                if not (isinstance(e, dict) and e.get('id') is not None and ids._number(e['id'], key) in stays)]
        config[key] = mine + [copy.deepcopy(stays[c]) for c in sorted(stays)]
        for c in stays:
            keep(c, what)

    # new squares: a kept new ID heading one takes its old square's voice
    old_voice = {}
    for sq in (derived.get('grid') or {}).get('squares') or []:
        if isinstance(sq, dict) and sq.get('voice') is not None:
            for m in sq['members']:
                old_voice[ids._number(m, 'grid.squares')] = sq['voice']
    squares = (config.get('grid') or {}).get('squares') or []
    for k, sq in enumerate(squares):
        members = sq.get('members') if isinstance(sq, dict) else sq
        head = ids._number(members[0], 'grid.squares') if members else None
        if head in old_new and old_voice.get(head) is not None:
            squares[k] = {**(sq if isinstance(sq, dict) else {'members': list(members)}), 'voice': old_voice[head]}
            keep(head, 'voice')

    for cid in edited & present:
        keep(cid, 'stat_edits')

    # leaving the grid
    clears = []
    for cid in sorted(chars):
        if cid in present:
            continue
        char = chars[cid]
        if cid >= ids.FIRST_NEW:
            plan.dropped[cid] = new_id_customisations(char, old_new.get(cid), env) + (
                ['stat_edits'] if cid in edited else [])
            continue
        lost = ['stat_edits'] if cid in edited else []
        if not env.models_at_baseline(char):
            lost.append('model')
            clears.append(('--unpatch', '--target-id', _hex(cid)))
        files = changed_equipment(char, unknown_counts=True)
        if files:
            lost.append('equipment')
            clears += [('--unpatch', '--target-id', _hex(cid), '--target-file', str(f)) for f in files]
        lost += [key for key in ('name', 'icon') if (old_spares.get(cid) or {}).get(key) is not None]
        lost += [w for w in lost_stock.get(cid, []) if w not in lost]
        plan.reset[cid] = lost
    resets = [f'reset:{_hex(c)}' for c in sorted(plan.reset) if 'stat_edits' in plan.reset[c]]
    if resets:
        plan.commands.append(('--apply-stat-edits', *resets))
    plan.commands += clears

    # the rebuild
    if set(config) <= {'version', 'comment'}:
        plan.remove = True
        plan.commands.append(('--roster', '--remove', '--keep-stat-edits'))
    else:
        plan.commands.append(('--roster', '--state', state_file))
    plan.commands.append(('--roster-state',))

    stats_only = sorted(c for c, f in plan.carried.items() if f == ['stat_edits'])
    for c in stats_only:
        del plan.carried[c]                              # listed as a count: stat edits travel by ID anyway
    n = len(plan.carried)
    plan.notes.append(f'{n} slot{"s" if n != 1 else ""} on both grids keep their customisations' if n
                      else 'no slot on both grids has customisations beyond stat edits')
    if stats_only:
        plan.notes.append(f'stat edits stay on {len(stats_only)} other character{"s" if len(stats_only) != 1 else ""}'
                          f' ({ids.id_ranges(stats_only)})')
    plain = sum(1 for f in plan.dropped.values() if not f)
    if plain:
        plan.notes.append(f'{plain} unchanged new ID{"s" if plain != 1 else ""} leave the roster')
    return plan
