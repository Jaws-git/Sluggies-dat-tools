"""The stat editor's fields: editor field name -> table, byte offset, type, range.

The one mapping between what the Sluggers Stat Editor (Philenarion) shows and
where the game keeps it. The bridge (``bridge.py``) describes tables with it,
and edit files name values by these keys, never by address.

**Field keys** are the editor's own list entries (``statsList``,
``pitchingList``, ``sizeList``, ``speedList``, ``teamList``,
``starEventsList``, ``starBoostStatsList``, ``starBoostList``, copied below as
``EDITOR_LISTS``). A name that occurs more than once in its list gets its list
index: ``height#5`` and ``height#13`` (``sizeList``), ``???#4``
(``starEventsList``). Rows and columns without names in the editor (speed
steps, trajectory heights, star handicap, handicap params) are numbered from
``"0"``.

Per-character fields (``CHARACTER_FIELDS``, by group as the edit file has
them):

* ``stats``: ``statsList``. Entries 0-25 are in the stats row (0x8E bytes,
  bytes 0-1 the row's own ID); u16 for entries 10-17 and 22-25, else u8.
  Entry 26 ``traj`` and 27 ``hit curve`` are the ``traj`` table's two bytes,
  28 ``stamina`` the ``stamina`` table (u16), 29 ``star pitch type`` the
  ``starpitch`` table.
* ``pitching``: ``pitchingList``: 0-2 the ``pitchwindup`` table, 3-4
  ``changeup`` (f32).
* ``size``: ``sizeList``: 0-1 ``sizescale``, 2-11 ``catchrange``, 12-13
  ``hitbox`` (f32).
* ``chemistry``: towards stock ID ``k`` at stats row ``+0x28 + k`` (u8); see
  ``bridge.py`` for new IDs.

Global fields (``GLOBAL_FIELDS``): speed lookups, trajectory heights, team
star gains (s16), star handicap, star boosts (``u32`` add/mult op, f32
amount, s16 min/max) and the six handicap immediates (the low byte of an
``addi``/``li`` in code).

Every offset was checked against the vanilla DOL by
``probe_stat_editor_layout.py`` (16,507 fields, 0 mismatches). Ranges are the
storage type's (plus the add/mult choices); the editor's spinbox limits are a
UI matter and not enforced here.
"""

import math
import struct
from dataclasses import dataclass

STATS_ROW = 0x8E
CHEM_BASE = 0x28
CHEM_COLUMNS = 0x65            # chemistry columns in a stats row: stock IDs 0x00-0x64

# -- the editor's lists (editor.py v4.3), pinned by probe_stat_editor_layout.py ---------------------------------
STATS_LIST = ("pitching arm", "batting arm", "character class", "???", "weight",
              "captain", "star pitch", "star swing", "fielding ability",
              "baserunning ability", "slap size", "charge size", "slap power",
              "charge power", "bunting", "speed", "outfield throwing", "fielding",
              "displayed pitching", "displayed batting", "displayed fielding",
              "dis speed", "curveball speed", "charge pitch speed", "curve",
              "curse ball", "traj", "hit curve", "stamina", "star pitch type")
PITCHING_LIST = ("charge", "captain star", "curve", "Speed Mult", "Height")
SIZE_LIST = ("Gameplay", "Select Screens", "regular", "facing away", "safer catch", "height",
             "reach up threshold", "??? (height)", "dive", "line drive dive height", "jump", "??? (regular)",
             "width", "height")
SPEED_LIST = ("Baserunning", "Fielding")
TEAM_LIST = ("Fireballs", "Knights", "Wilds", "Monkeys", "Monarchs", "Flowers",
             "Eggs", "Monsters", "Muscles", "Spitballs", "Bows", "Rookies")
STAR_EVENTS_LIST = ("single", "double", "triple", "ground rule double", "???",
                    "solo HR", "2HR", "3HR", "grand slam", "???", "???", "???", "???",
                    "???", "???", "getting struck out", "getting any other out",
                    "???", "???", "foul", "whiffing a star hit", "not steal base",
                    "???", "???", "???", "???", "???", "walking", "bean balling",
                    "letting a runner to 1b", "letting a HR", "per run given",
                    "strike", "out (any)", "???", "???", "???", "???", "???", "???", "???")
STAR_BOOST_STATS_LIST = ("displayed Pitching", "curveball speed", "fastball speed",
                         "curve", "curse ball", "displayed batting", "slap power",
                         "charge power", "slap contact", "charge contact", "bunting",
                         "displayed fielding", "displayed speed", "fielding", "throwing arm", "speed")
STAR_BOOST_LIST = ("add/mult", "amount", "min", "max")
EDITOR_LISTS = {'statsList': STATS_LIST, 'pitchingList': PITCHING_LIST, 'sizeList': SIZE_LIST,
                'speedList': SPEED_LIST, 'teamList': TEAM_LIST, 'starEventsList': STAR_EVENTS_LIST,
                'starBoostStatsList': STAR_BOOST_STATS_LIST, 'starBoostList': STAR_BOOST_LIST}

# -- storage types ------------------------------------------------------------------------------------------------
KINDS = {'u8': ('>B', 0, 0xFF), 'u16': ('>H', 0, 0xFFFF), 's16': ('>h', -0x8000, 0x7FFF),
         'u32': ('>I', 0, 0xFFFFFFFF), 'f32': ('>f', None, None)}
STAR_BOOST_OPS = (1, 2)        # 1 add, 2 mult

# The per-ID tables the editor edits (all moved by a roster run with an "ids" key).
CHARACTER_TABLES = ('stats', 'pitchwindup', 'starpitch', 'stamina', 'changeup', 'traj', 'catchrange', 'hitbox',
                    'sizescale')


class FieldError(ValueError):
    pass


def keys(names) -> tuple[str, ...]:
    """Edit-file keys for a list of editor names: the name, or ``name#index`` where it repeats."""
    return tuple(n if names.count(n) == 1 else f'{n}#{i}' for i, n in enumerate(names))


def numbered(count: int) -> tuple[str, ...]:
    return tuple(str(i) for i in range(count))


@dataclass(frozen=True)
class Value:
    """Where one value lives and how it is stored (shared by character and global fields)."""
    kind: str
    choices: tuple | None = None

    @property
    def size(self) -> int:
        return struct.calcsize(KINDS[self.kind][0])

    def problem(self, value) -> str | None:
        """Why ``value`` cannot be stored, or None."""
        if self.kind == 'f32':
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return f'{value!r} is not a finite number'
            if abs(value) > 3.4028234663852886e38:
                return f'{value!r} does not fit a 32-bit float'
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            return f'{value!r} is not a whole number'
        if self.choices is not None and value not in self.choices:
            return f'{value} is not one of {", ".join(map(str, self.choices))}'
        _fmt, lo, hi = KINDS[self.kind]
        if not lo <= value <= hi:
            return f'{value} is outside {lo}-{hi} ({self.kind})'
        return None

    def pack(self, value) -> bytes:
        problem = self.problem(value)
        if problem:
            raise FieldError(problem)
        return struct.pack(KINDS[self.kind][0], value)

    def unpack(self, raw: bytes):
        return struct.unpack(KINDS[self.kind][0], raw)[0]


@dataclass(frozen=True)
class Field(Value):
    """A per-character value: ``offset`` bytes into the character's row of ``table``."""
    group: str = ''
    name: str = ''
    index: int = 0
    table: str = ''
    offset: int = 0


@dataclass(frozen=True)
class GlobalField(Value):
    """A value of a global table at a fixed address (these tables never move)."""
    table: str = ''
    row: str = ''
    column: str = ''
    address: int = 0


def _stats_offset(j: int) -> int:
    """Row offset of editor stat ``j`` (0-25): the editor's ``getStatOffset(j) - 1``."""
    if j < 11:
        return j + 2
    if j < 19:
        return 2 * j - 8
    if j < 23:
        return j + 10
    return 2 * j - 12


STATS_U16 = frozenset(range(10, 18)) | frozenset(range(22, 26))


def _character_fields() -> tuple[Field, ...]:
    out = []
    for j, name in enumerate(keys(STATS_LIST)):
        if j < 26:
            table, offset, kind = 'stats', _stats_offset(j), 'u16' if j in STATS_U16 else 'u8'
        else:
            table, offset, kind = {26: ('traj', 0, 'u8'), 27: ('traj', 1, 'u8'), 28: ('stamina', 0, 'u16'),
                                   29: ('starpitch', 0, 'u8')}[j]
        out.append(Field(kind, group='stats', name=name, index=j, table=table, offset=offset))
    for j, name in enumerate(keys(PITCHING_LIST)):
        table, offset = ('pitchwindup', 4 * j) if j < 3 else ('changeup', 4 * (j - 3))
        out.append(Field('f32', group='pitching', name=name, index=j, table=table, offset=offset))
    for j, name in enumerate(keys(SIZE_LIST)):
        table, offset = (('sizescale', 4 * j) if j < 2 else ('catchrange', 4 * (j - 2)) if j < 12
                         else ('hitbox', 4 * (j - 12)))
        out.append(Field('f32', group='size', name=name, index=j, table=table, offset=offset))
    return tuple(out)


CHARACTER_FIELDS = _character_fields()
CHARACTER_GROUPS = ('stats', 'pitching', 'size')
BY_GROUP = {g: {f.name: f for f in CHARACTER_FIELDS if f.group == g} for g in CHARACTER_GROUPS}
CHEMISTRY = Value('u8')


def chemistry_offset(column: int) -> int:
    """Stats-row offset of the chemistry towards stock ID ``column``."""
    if not 0 <= column < CHEM_COLUMNS:
        raise FieldError(f'chemistry column 0x{column:02X} is not a stock ID (0x00-0x64)')
    return CHEM_BASE + column


# -- global tables -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class GlobalTable:
    key: str
    address: int
    size: int
    rows: tuple[str, ...]
    columns: tuple[str, ...]
    note: str = ''


SPEED_FIELDING, SPEED_BASERUNNING, SPEED_STEPS = 0x80625898, 0x80626208, 43
TRAJ_HEIGHTS, TRAJ_ROWS, TRAJ_COLUMNS = 0x80626E88, 24, 25
TEAM_STARS = 0x8062BD50
STAR_HANDICAP = 0x8062C128
STAR_BOOSTS, STAR_BOOST_ROW = 0x806318D8, 12
# The low byte of six addi/li immediates (editor handicapCode): two rows of three.
HANDICAP_PARAMS = ((0x801808EF, 0x801808FB, 0x80180903), (0x80180977, 0x80180983, 0x8018098B))

GLOBAL_TABLES = (
    GlobalTable('speed', SPEED_FIELDING, 4 * SPEED_STEPS, numbered(SPEED_STEPS), keys(SPEED_LIST),
                f'f32 by speed stat; Fielding at 0x{SPEED_FIELDING:08X}, Baserunning at 0x{SPEED_BASERUNNING:08X}'),
    GlobalTable('traj_heights', TRAJ_HEIGHTS, TRAJ_ROWS * TRAJ_COLUMNS, numbered(TRAJ_ROWS), numbered(TRAJ_COLUMNS),
                'u8'),
    GlobalTable('team_stars', TEAM_STARS, len(TEAM_LIST) * 2 * len(STAR_EVENTS_LIST), keys(TEAM_LIST),
                keys(STAR_EVENTS_LIST), 's16'),
    GlobalTable('star_handicap', STAR_HANDICAP, 4 * 2 * 4, numbered(4), numbered(2), 'f32'),
    GlobalTable('star_boost', STAR_BOOSTS, len(STAR_BOOST_STATS_LIST) * STAR_BOOST_ROW, keys(STAR_BOOST_STATS_LIST),
                keys(STAR_BOOST_LIST), 'u32 op (1 add, 2 mult), f32 amount, s16 min, s16 max'),
    GlobalTable('handicap_params', HANDICAP_PARAMS[0][0], 0, numbered(2), numbered(3),
                'u8: low byte of an addi/li immediate in code'),
)
GLOBALS_BY_KEY = {t.key: t for t in GLOBAL_TABLES}


def _global_fields() -> tuple[GlobalField, ...]:
    out = []
    speed = GLOBALS_BY_KEY['speed']
    for i, row in enumerate(speed.rows):
        for column, base in zip(speed.columns, (SPEED_BASERUNNING, SPEED_FIELDING)):
            out.append(GlobalField('f32', table='speed', row=row, column=column, address=base + 4 * i))
    for i, row in enumerate(numbered(TRAJ_ROWS)):
        for j, column in enumerate(numbered(TRAJ_COLUMNS)):
            out.append(GlobalField('u8', table='traj_heights', row=row, column=column,
                                   address=TRAJ_HEIGHTS + TRAJ_COLUMNS * i + j))
    events = keys(STAR_EVENTS_LIST)
    for i, row in enumerate(keys(TEAM_LIST)):
        for j, column in enumerate(events):
            out.append(GlobalField('s16', table='team_stars', row=row, column=column,
                                   address=TEAM_STARS + 2 * len(events) * i + 2 * j))
    for i in range(4):
        for j in range(2):
            out.append(GlobalField('f32', table='star_handicap', row=str(i), column=str(j),
                                   address=STAR_HANDICAP + 8 * i + 4 * j))
    layout = (('u32', 0, STAR_BOOST_OPS), ('f32', 4, None), ('s16', 8, None), ('s16', 10, None))
    for i, row in enumerate(keys(STAR_BOOST_STATS_LIST)):
        for column, (kind, offset, choices) in zip(keys(STAR_BOOST_LIST), layout):
            out.append(GlobalField(kind, choices, table='star_boost', row=row, column=column,
                                   address=STAR_BOOSTS + STAR_BOOST_ROW * i + offset))
    for i, addresses in enumerate(HANDICAP_PARAMS):
        for j, address in enumerate(addresses):
            out.append(GlobalField('u8', table='handicap_params', row=str(i), column=str(j), address=address))
    return tuple(out)


GLOBAL_FIELDS = _global_fields()
GLOBAL_LOOKUP = {(f.table, f.row, f.column): f for f in GLOBAL_FIELDS}


def character_field(group: str, name: str) -> Field:
    try:
        return BY_GROUP[group][name]
    except KeyError:
        raise FieldError(f'unknown field {group}.{name!r}') from None


def global_field(table: str, row: str, column: str) -> GlobalField:
    try:
        return GLOBAL_LOOKUP[(table, row, column)]
    except KeyError:
        raise FieldError(f'unknown global field {table}[{row!r}][{column!r}]') from None
