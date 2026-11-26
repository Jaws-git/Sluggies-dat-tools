"""A character file's stats and chemistry overrides (``slgmodel.SlgModel.stat_overrides`` /
``chemistry_overrides``, the ``community/`` folder of a ``.slgmodel``) as a stat copy snapshot
(``StatEditor/apply.py``, format ``sluggies-stat-copy``): the values the planner writes onto a new ID after the
roster rebuild (``slot_plan.plan_slgmodel``).

The patcher (Slugout) names the values its own way; ``STATS`` and ``TABLE_STATS`` map them onto the stat
editor's fields (``StatEditor/fields.py``). The mapping was read from its source on 2026-11-26 (``charbuild.py``
``TABLE_STATS``, ``sluggers_data.STAT_FIELDS``): the 25 ``stats`` names and their row offsets are the stat
editor's own, the ``table_stats`` are the same tables under other names.

* **Not mapped** (``UNMAPPED``): ``pitch steering`` and ``strike zone height`` live in tables the stat editor's
  fields do not cover; they are left out with a warning.
* **Choices** (``CHOICES``): a value that selects something (arm, class, star pitch, abilities, ...) is left
  out when the game's own list does not have it: the patcher goes further for moves that exist only in its own
  code (star pitch up to 20). Every other value is written when it fits its storage; one outside the stat
  editor's input range is warned about.
* **Scale**: one factor on the base's size as the roster gives it (``base_sizes``: the base's ``base_gameplay``
  / ``base_select`` of ``state_cli.read_sizes``), in game and on the select screens, as the patcher applies it.
* **Chemistry** is symmetric in the file (bad / neutral / good by partner name): the same value goes into both
  directions. A partner the roster does not have is left out.

Nothing here reads or writes the game.
"""

from __future__ import annotations

try:
    from ..StatEditor import fields
except ImportError:
    from StatEditor import fields

COPY_FORMAT = 'sluggies-stat-copy'                      # StatEditor/apply.COPY_FORMAT (test-pinned)

# the patcher's "stats" section: the stat editor's own names (statsList without its unnamed entry 3)
STATS = tuple(name for i, name in enumerate(fields.STATS_LIST[:26]) if i != 3)
# the patcher's "table_stats" section -> (the stat editor's group, field key)
TABLE_STATS = {
    'hit trajectory': ('stats', 'traj'), 'hit curve': ('stats', 'hit curve'), 'stamina': ('stats', 'stamina'),
    'star pitch type': ('stats', 'star pitch type'),
    'changeup speed mult': ('pitching', 'Speed Mult'), 'changeup arc height': ('pitching', 'Height'),
    'windup charge frames': ('pitching', 'charge'),
    'catch normal': ('size', 'regular'), 'catch any direction': ('size', 'facing away'),
    'catch centered': ('size', 'safer catch'), 'catch max height': ('size', 'height#5'),
    'catch dive': ('size', 'dive'),
    'body radius': ('size', 'width'), 'body height': ('size', 'height#13'),
}
UNMAPPED = ('pitch steering', 'strike zone height')
# values that select something: only what the game's own list has (the stat editor's range) is written
CHOICES = frozenset({'pitching arm', 'batting arm', 'character class', 'weight', 'captain', 'star pitch',
                     'star swing', 'fielding ability', 'baserunning ability', 'traj', 'hit curve',
                     'star pitch type'})
SCALE_FIELDS = (('base_gameplay', 'Gameplay'), ('base_select', 'Select Screens'))
CHEMISTRY = {'bad': 0, 'neutral': 1, 'good': 2}


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


def snapshot(target: int, overrides: dict, chemistry: dict, base_sizes: dict | None, partner_id) -> tuple:
    """``(document or None, count, warnings)``: the stat copy snapshot that writes a character file's overrides
    onto ``target``, how many values it holds (a chemistry pair counts once), and what was left out.
    ``overrides``: ``{"stats": {name: number}, "table_stats": {name: number}, "scale": number or None}``;
    ``chemistry``: ``{partner name: "bad" | "neutral" | "good"}``; ``partner_id(name)``: the roster ID a partner
    name means, or None. None as the document: nothing to write."""
    groups, warnings, left_out, count = {}, [], [], 0

    def put(group, key, value, shown):
        nonlocal count
        f = fields.character_field(group, key)
        if f.kind != 'f32':
            if isinstance(value, float) and not value.is_integer():
                left_out.append(f'{shown} ({value:g} is not a whole number)')
                return
            value = int(value)
        problem = f.problem(value)
        limit = fields.limit_problem(f, value)
        if problem or (limit and key in CHOICES):
            left_out.append(f'{shown} ({problem or limit})')
            return
        if limit:
            warnings.append(f'{shown}: {limit}; written anyway')
        groups.setdefault(group, {})[key] = f.pack(float(value) if f.kind == 'f32' else value).hex()
        count += 1

    for name, value in sorted((overrides.get('stats') or {}).items()):
        if name in STATS:
            put('stats', name, value, name)
        else:
            left_out.append(f'{name} (not a stat this tool knows)')
    for name, value in sorted((overrides.get('table_stats') or {}).items()):
        if name in TABLE_STATS:
            put(*TABLE_STATS[name], value, name)
        elif name in UNMAPPED:
            left_out.append(f'{name} (no stat field for it here)')
        else:
            left_out.append(f'{name} (not a stat this tool knows)')
    scale = overrides.get('scale')
    if scale is not None:
        if not base_sizes or any(not base_sizes.get(key) for key, _name in SCALE_FIELDS):
            left_out.append(f'scale {scale:g} (the base\'s size cannot be read)')
        else:
            before = count
            for key, name in SCALE_FIELDS:
                put('size', name, float(f'{base_sizes[key] * scale:.6g}'), f'scale {scale:g}')
            count = min(count, before + 1)            # one value in the file
    pairs, missing = {}, []
    for partner, word in sorted((chemistry or {}).items()):
        other = partner_id(partner)
        if other is None or other == target:
            missing.append(partner)
            continue
        value = CHEMISTRY[word]
        pairs[_hex(other)] = [value, value]
    count += len(pairs)
    if left_out:
        warnings.append('stat values left out: ' + '; '.join(left_out))
    if missing:
        warnings.append('chemistry left out (the roster has no such character): ' + ', '.join(missing))
    if not count:
        return None, 0, warnings
    doc = {'format': COPY_FORMAT, 'source': _hex(target), 'target': _hex(target), 'fields': groups,
           'chemistry': pairs}
    return doc, count, warnings
