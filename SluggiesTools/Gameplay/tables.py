"""The gameplay tables: key -> address, storage type, labels and cross-field checks. Holds no values.

Every table is static data in ``main.dol``'s own data sections (D4
``0x806233C0``-``0x80639D00``, D7 ``0x80796DC0``-``0x807A4DE0``). Nothing is
on the heap and nothing is indexed by character ID (rows are the stats row's
"character class" byte, the CPU level, the roulette row), so a roster run
never moves them; it only resets them with the rest of ``main.dol``
(``carry.py``). The code reads them with ``lis``/``addi`` + ``lbz``/``lhax``
and was not seen to store to them, so a value written once into the DOL acts
like a Gecko ``04`` code rewriting it every frame.

Files name values by **table / row / column key** (``FIELD_LOOKUP``), never
by address. Every field is a ``StatEditor.fields.GlobalField``; the star boost
table is the stat editor's own (``fields.GLOBAL_FIELDS``, table
``star_boost``), so both tools write the same fields.

Tables (readers found by cross-reference and disassembly):

* ``star_cost`` (``0x8062BD40``): a 6-entry s16 table in team star points,
  stock ``0, 100, 100, 0, 50, 50``. **Entry 4 is also the HUD's star unit**:
  FUN at ``0x8033A71C`` copies it to ``r13 - 0x99C``, and the star gauge code
  divides the team points by it (``divw`` at ``0x8033091C``, ``0x80331060``,
  near ``0x803370E4``), so one HUD star is the non-captain cost (stock 50;
  Dolphin 2026-10-22: 150 there made one star 150 points). Entry 1 is the alt.
  captain's star move, 4 a non-captain's, 5 the main captain's (Dolphin,
  2026-10-22: costs 250 / 150 / 100 left the team's points, watched at
  ``*(0x80794C80)`` +0 / +2, by exactly those amounts). Entry 1 is also
  stored into a team's points in six places (``sth`` after FUN_80180804, e.g.
  ``0x804E16B4``): what for is unknown. 0 is the dive check, 2 and 3 are
  unknown; only 1, 4 and 5 are fields.
* ``stamina`` (``0x80627B8E``, ``0x80627B90``): the stamina a star pitch and a
  charge pitch cost, two s16 of a row at ``0x80627B88`` (the charge pitch's
  confirmed in Dolphin, 2026-10-22: −40 there tires the pitcher faster).
* ``star_boost`` (``0x806318D8``): 16 x (u32 op 1 add / 2 mult, f32 amount,
  s16 min, s16 max).
* ``item_odds`` (``0x80630FB0``): 3 roulette rows x 6 u8 weights, the batting
  item roulette of every batter (FUN_804583C4). Row ``d < 3 -> 2``,
  ``3..5 -> 1``, ``>= 6 -> 0``; ``d`` is the innings remaining (Dolphin,
  2026-10-22: a 9-inning game uses row 0 in innings 1-3, 1 in 4-6, 2 in 7-9).

Cross-field checks (``problems``):

* an item row totals at most 127: the pick sign-extends the roll
  (``extsb`` at ``0x80458530``); weights are relative, they need not total 100;
* the stamina costs stay negative (a drain);
* the non-captain star cost stays above 0: it is the HUD's star unit, a divisor.

**Moved to the custom CPU level** (``MOVED``; ``CustomCpu/settings.py``, its
``sluggies_`` and charge swing settings): ``star_usage`` (``0x80624778``,
class x situation u8 %, FUN_800C5778 / FUN_800C710C), ``charge_odds``
(``0x80797218``, class x strikes u8 %, FUN_800C5974), ``star_modifiers``
(``0x80797210``, CPU level x situation s8, added to the star usage roll) and
``preferred_pitch`` (``0x807971C0``, class x strikes pitch type, then the
chance at ``+8``, FUN_800C7378). They are no gameplay fields any more: a
custom match swaps its own values in, and levels 1-4 play the clean game's.
Files that still name them load without them, with a note (``apply``,
``legacy``); a reset and a roster run put their bytes back to ``1_Input``.
"""

from dataclasses import dataclass

try:
    from ..StatEditor import fields
except ImportError:
    from StatEditor import fields

GlobalField = fields.GlobalField

TABS = (('cpu', 'Star and Stamina Costs'), ('boosts', 'Star Boosts'), ('items', 'Item Odds'))

CLASSES = ('balanced', 'power', 'speed', 'technique')
CLASS_LABELS = ('Balanced', 'Power', 'Speed', 'Technique')
PITCH_TYPES = ('Curveball', 'Fastball', 'Changeup')
STAR_BOOST_OPS = {1: 'Add', 2: 'Mult'}
ROLL_LIMIT = 127                         # a byte the code sign-extends

STAR_USAGE, STAR_COST, CHARGE_ODDS = 0x80624778, 0x8062BD40, 0x80797218
STAR_MODIFIERS, PREFERRED_PITCH, STAMINA = 0x80797210, 0x807971C0, 0x80627B8E     # star pitch; charge pitch +2
ITEM_ODDS = 0x80630FB0


@dataclass(frozen=True)
class Table:
    key: str
    title: str
    tab: str                             # TABS key
    section: str                         # SECTIONS key ("Reset section")
    rows: tuple[str, ...]
    row_labels: tuple[str, ...]
    columns: tuple[str, ...]
    column_labels: tuple[str, ...]
    note: str = ''


# Tables that are custom CPU level settings now (module docstring): key -> title, and their bytes.
MOVED = {'star_usage': 'Star usage odds', 'charge_odds': 'Charge swing odds',
         'star_modifiers': 'Star swing modifiers', 'preferred_pitch': 'Preferred pitch'}
MOVED_RANGES = ((STAR_USAGE, 20), (STAR_MODIFIERS, 8), (CHARGE_ODDS, 8), (PREFERRED_PITCH, 16))

SECTIONS = (
    ('star_cost', 'cpu', 'Star costs'),
    ('stamina', 'cpu', 'Stamina costs'),
    ('star_boost', 'boosts', 'Star boosts'),
    ('item_odds', 'items', 'Item odds'),
)

TABLES = (
    Table('star_cost', 'Star costs', 'cpu', 'star_cost', ('1', '4', '5'),
          ('Alt. captain', 'Non-captain', 'Main captain'), ('points',), ('Points',),
          'Team star points a star swing or star pitch costs. The non-captain cost is also one star on the match '
          'HUD (stock 50), so changing it rescales the star gauge; it must stay above 0.'),
    Table('stamina', 'Stamina costs', 'cpu', 'stamina', ('star_pitch', 'charge_pitch'),
          ('Star pitch', 'Charge pitch'), ('cost',), ('Stamina',), 'Stamina a pitch drains (negative).'),
    Table('star_boost', 'Star boosts', 'boosts', 'star_boost', fields.keys(fields.STAR_BOOST_STATS_LIST),
          ('Displayed pitching', 'Curveball speed', 'Fastball speed', 'Curve', 'Curse ball', 'Displayed batting',
           'Slap power', 'Charge power', 'Slap contact', 'Charge contact', 'Bunting', 'Displayed fielding',
           'Displayed speed', 'Fielding', 'Throwing arm', 'Speed'),
          fields.keys(fields.STAR_BOOST_LIST), ('Add/Mult', 'Amount', 'Min', 'Max'),
          'What a star boost does to each stat: add or multiply by the amount, kept within min and max. The Stat '
          'Editor writes the same values.'),
    Table('item_odds', 'Item odds', 'items', 'item_odds', ('0', '1', '2'),
          ('6+ innings left', '3-5 innings left', 'Under 3 innings left'),
          ('shell', 'fireball', 'bob_omb', 'pow', 'banana', 'boo'),
          ('Green Shell', 'Fireball', 'Bob-omb', 'POW Block', 'Banana', 'Boo'),
          'Relative weights of the batting item roulette (every batter, on good chemistry), by innings left (a '
          '9-inning game: innings 1-3, 4-6, 7-9). A row totals at most 127; it need not total 100.'),
)
TABLES_BY_KEY = {t.key: t for t in TABLES}


def _fields() -> tuple[GlobalField, ...]:
    out = []

    def put(kind, table, row, column, address, choices=None):
        out.append(GlobalField(kind, choices, table=table, row=row, column=column, address=address))

    t = TABLES_BY_KEY['star_cost']
    for row in t.rows:
        put('s16', t.key, row, 'points', STAR_COST + 2 * int(row))
    put('s16', 'stamina', 'star_pitch', 'cost', STAMINA)
    put('s16', 'stamina', 'charge_pitch', 'cost', STAMINA + 2)
    out += [f for f in fields.GLOBAL_FIELDS if f.table == 'star_boost']
    t = TABLES_BY_KEY['item_odds']
    for i, row in enumerate(t.rows):
        for j, column in enumerate(t.columns):
            put('u8', t.key, row, column, ITEM_ODDS + len(t.columns) * i + j)
    return tuple(out)


FIELDS = _fields()
FIELD_LOOKUP = {(f.table, f.row, f.column): f for f in FIELDS}
BY_TABLE = {t.key: tuple(f for f in FIELDS if f.table == t.key) for t in TABLES}


class FieldError(ValueError):
    pass


def field(table: str, row: str, column: str) -> GlobalField:
    try:
        return FIELD_LOOKUP[(table, row, column)]
    except KeyError:
        raise FieldError(f'unknown gameplay field {table} / {row} / {column}') from None


def label(f: GlobalField) -> str:
    """``Star usage odds: Balanced / Dire``."""
    t = TABLES_BY_KEY[f.table]
    row = t.row_labels[t.rows.index(f.row)]
    column = t.column_labels[t.columns.index(f.column)]
    if len(t.columns) == 1:
        return f'{t.title}: {row}'
    return f'{t.title}: {row} / {column}'


def section_fields(section: str) -> tuple[GlobalField, ...]:
    return tuple(f for t in TABLES if t.section == section for f in BY_TABLE[t.key])


def tab_fields(tab: str) -> tuple[GlobalField, ...]:
    return tuple(f for t in TABLES if t.tab == tab for f in BY_TABLE[t.key])


# -- cross-field checks --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Problem:
    message: str
    fields: tuple[GlobalField, ...]


def problems(values: dict) -> list[Problem]:
    """The cross-field checks (module docstring) on ``values`` (``{field: value}``, every field the game maps);
    checks whose fields are not all given are skipped."""
    out = []
    items = TABLES_BY_KEY['item_odds']
    for row, row_label in zip(items.rows, items.row_labels):
        row_fields = [FIELD_LOOKUP[('item_odds', row, c)] for c in items.columns]
        if all(f in values for f in row_fields):
            total = sum(values[f] for f in row_fields)
            if total > ROLL_LIMIT:
                out.append(Problem(f'Item odds: {row_label} totals {total}; at most {ROLL_LIMIT} (the game reads the '
                                   'roll as a signed byte)', tuple(row_fields)))
    unit = FIELD_LOOKUP[('star_cost', '4', 'points')]
    if unit in values and values[unit] <= 0:
        out.append(Problem(f'{label(unit)} {values[unit]}: must stay above 0 (it is one star on the match HUD, the '
                           'star gauge divides by it)', (unit,)))
    for cost in BY_TABLE['stamina']:
        if cost in values and values[cost] >= 0:
            out.append(Problem(f'{label(cost)} {values[cost]}: must stay negative (a drain)', (cost,)))
    return out
