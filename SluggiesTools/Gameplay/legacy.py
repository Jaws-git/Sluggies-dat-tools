"""The AI Gameplay Tool's save files (``cpu_behavior.json``, the HTML Gecko code generator), migrated on load.

Read-only: we never write that layout (no format or version key, display
units, only the fields that tool knows). **Detection**: no ``format`` key and a
``starConfig`` object, the tool's own load test. Its other sections are
optional, as in its loader.

**Same in-game result as its codes.** A migrated value is the byte its
generator would have written, not the raw JSON number: percentages are rounded
(JavaScript ``Math.round``) and clamped to 0-100, modifiers to -128..127, the
stamina cost to s16, star costs are its "stars" x 50 in points (its unit: one
HUD star is the non-captain cost, 50 in a stock game), star boost amounts
are rounded to f32, and star boost min/max are clamped to u16 and stored in
the s16 field (40000 becomes -25536, as its Gecko line made it). Every clamp
and wrap gives a note.

**Only what its codes changed.** The generator emits a block only where the
file differs from its defaults, which equal the stock bytes: item odds as a
whole table; each star cost entry, the stamina cost and each star boost row
on its own. The migration keeps exactly those blocks, "differs" tested
against ``1_Input/main.dol``; everything else keeps the game's value.

**Not migrated**: its star usage, charge odds, modifier and preferred pitch
sections (``MOVED_SECTIONS``). Those tables are custom CPU level settings now
(``tables.MOVED``); a section that differs from the clean game gives a note.

A malformed file (a missing key in a present section, a value that is not a
number, a choice its own UI never offers) is refused.
"""

import math
import struct
from dataclasses import dataclass, field

try:
    from ..StatEditor import fields
    from . import tables
except ImportError:
    from StatEditor import fields
    from Gameplay import tables

TITLE = 'AI Gameplay Tool file'
CLASSES = ('Balanced', 'Power', 'Speed', 'Technique')         # rows 0-3
STAR_KEYS = ('b1', 'b2', 'b3', 'b4', 'b5')
LEVELS = ('Level1', 'Level2', 'Level3', 'Level4')
STAR_COSTS = (('nonMain', '1'), ('regular', '4'), ('main', '5'))
BOOST_STATS = ('displayPitching', 'curveballSpeed', 'fastballSpeed', 'curve', 'unknownPitch', 'displayBatting',
               'slapPower', 'chargePower', 'slapContact', 'chargeContact', 'bunting', 'displayFielding',
               'displaySpeed', 'fielding', 'throwingArm', 'speed')      # star_boost rows 0-15, in table order
ITEM_ROWS = ('first', 'middle', 'last')                                   # item_odds rows 0 / 1 / 2


class LegacyError(ValueError):
    pass


@dataclass
class Migrated:
    values: dict = field(default_factory=dict)              # {field: value}: the blocks its codes wrote
    notes: list[str] = field(default_factory=list)          # clamps, wraps, sections not in the file
    blocks: list[str] = field(default_factory=list)         # the blocks kept (differ from 1_Input)
    same: list[str] = field(default_factory=list)           # blocks equal to 1_Input (its codes wrote nothing)


def is_legacy(doc) -> bool:
    return isinstance(doc, dict) and 'format' not in doc and isinstance(doc.get('starConfig'), dict)


def js_round(x: float) -> int:
    """JavaScript ``Math.round``: halves round up."""
    return math.floor(x + 0.5)


def _number(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LegacyError(f'{where}: {value!r} is not a number')
    try:
        finite = math.isfinite(value)
    except OverflowError:            # an int too large for a float: no setting is that large
        raise LegacyError(f'{where}: a number of {len(str(abs(value)))} digits is not a setting') from None
    if not finite:
        raise LegacyError(f'{where}: {value!r} is not a number')
    return value


def _get(obj, key: str, where: str):
    if not isinstance(obj, dict):
        raise LegacyError(f'{where}: not an object')
    if key not in obj:
        raise LegacyError(f'{where}.{key} is missing')
    return obj[key]


class _Migration:
    def __init__(self, vanilla: dict):
        self.vanilla = vanilla
        self.out = Migrated()

    def clamp(self, value, lo: int, hi: int, where: str, what: str) -> int:
        n = js_round(_number(value, where))
        if not lo <= n <= hi:
            clamped = max(lo, min(hi, n))
            self.out.notes.append(f'{where}: {value} -> {clamped} ({what} {lo} to {hi}, as its code wrote it)')
            return clamped
        return n

    def percent(self, value, where: str) -> int:
        return self.clamp(value, 0, 100, where, 'percent,')

    def block(self, name: str, values: dict) -> None:
        """Keep ``values`` when any differs from 1_Input (its generator emitted the block)."""
        differs = any(f not in self.vanilla or f.pack(v) != f.pack(self.vanilla[f]) for f, v in values.items())
        if differs:
            self.out.values.update(values)
            self.out.blocks.append(name)
        else:
            self.out.same.append(name)


# Sections whose tables are custom CPU level settings now (``tables.MOVED``): (section, row keys, value keys,
# the clean game's values in that order). Not migrated; a section that differs from the clean game gives a note.
MOVED_SECTIONS = (
    ('starConfig', CLASSES, STAR_KEYS,
     (5, 5, 20, 75, 95, 15, 15, 30, 80, 95, 1, 1, 10, 30, 60, 25, 25, 30, 50, 95)),
    ('chargeConfig', CLASSES, ('charge01', 'charge2'), (40, 10, 90, 40, 30, 10, 40, 10)),
    ('modifierConfig', LEVELS, ('normal', 'special'), (0, 0, 0, 0, -10, 0, -20, 5)),
    ('pitchConfig', CLASSES, ('pitch01', 'pitch2', 'prob01', 'prob2'),
     (0, 1, 70, 70, 1, 1, 85, 85, 0, 0, 85, 80, 0, 2, 50, 60)),
)


def _moved_sections(doc: dict, m: '_Migration') -> None:
    changed = []
    for section, rows, keys, stock in MOVED_SECTIONS:
        entry = doc.get(section)
        if entry is None:
            continue
        try:
            given = tuple(js_round(entry[row][key]) for row in rows for key in keys)
        except (KeyError, TypeError, ValueError, OverflowError):
            given = None                     # not read any more, so a malformed section is no refusal
        if given != stock:
            changed.append(section)
    if changed:
        m.out.notes.append(f'{", ".join(changed)}: star usage, charge odds, star swing modifiers and preferred pitch '
                           'are custom CPU level settings now (Custom CPU tab): not loaded')


def migrate(doc: dict, vanilla: dict) -> Migrated:
    """The values ``doc``'s Gecko codes wrote (module docstring); ``vanilla``: ``{field: value}`` of
    ``1_Input/main.dol`` (``read.values``). Raises ``LegacyError``."""
    if not is_legacy(doc):
        raise LegacyError('not an AI Gameplay Tool file (no "starConfig")')
    m = _Migration(vanilla)
    f = tables.field
    missing = []

    _moved_sections(doc, m)

    costs = doc.get('starCostConfig')
    if costs is None:
        missing.append('starCostConfig')
    else:
        for key, row in STAR_COSTS:
            where = f'starCostConfig.{key}'
            points = js_round(_number(_get(costs, key, 'starCostConfig'), where) * 50)
            if not 0 <= points <= 0x7FFF:
                raise LegacyError(f'{where}: {points} points is not a star cost the game can hold')
            m.block(tables.label(f('star_cost', row, 'points')), {f('star_cost', row, 'points'): points})

    if 'staminaCost' not in doc:
        missing.append('staminaCost')
    else:
        cost = m.clamp(doc['staminaCost'], -0x8000, 0x7FFF, 'staminaCost', 'signed 16-bit,')
        m.block('Star pitch stamina cost', {f('stamina', 'star_pitch', 'cost'): cost})

    boosts = doc.get('starBoostConfig')
    if boosts is None:
        missing.append('starBoostConfig')
    else:
        for row, stat, row_label in zip(tables.TABLES_BY_KEY['star_boost'].rows, BOOST_STATS,
                                        tables.TABLES_BY_KEY['star_boost'].row_labels):
            entry = _get(boosts, stat, 'starBoostConfig')
            where = f'starBoostConfig.{stat}'
            op = _get(entry, 'addMult', where)
            if op not in fields.STAR_BOOST_OPS or isinstance(op, bool):
                raise LegacyError(f'{where}.addMult: {op!r} is not 1 (add) or 2 (mult)')
            amount = _number(_get(entry, 'amount', where), f'{where}.amount')
            try:
                amount = struct.unpack('>f', struct.pack('>f', amount))[0]
            except OverflowError:
                raise LegacyError(f'{where}.amount: {amount} does not fit a 32-bit float') from None
            values = {f('star_boost', row, 'add/mult'): int(op), f('star_boost', row, 'amount'): amount}
            for column in ('min', 'max'):
                n = m.clamp(_get(entry, column, where), 0, 0xFFFF, f'{where}.{column}', 'unsigned 16-bit,')
                if n > 0x7FFF:
                    m.out.notes.append(f'{where}.{column}: {n} is stored as {n - 0x10000} (the game reads a signed '
                                       '16-bit value; its Gecko line wrote the same bytes)')
                    n -= 0x10000
                values[f('star_boost', row, column)] = n
            m.block(f'Star boosts: {row_label}', values)

    items = doc.get('itemConfig')
    if items is None:
        missing.append('itemConfig')
    else:
        values = {}
        columns = tables.TABLES_BY_KEY['item_odds'].columns
        for row, key in zip(tables.TABLES_BY_KEY['item_odds'].rows, ITEM_ROWS):
            weights = _get(items, key, 'itemConfig')
            if not isinstance(weights, list) or len(weights) != len(columns):
                raise LegacyError(f'itemConfig.{key}: not a list of {len(columns)} numbers')
            for i, (column, w) in enumerate(zip(columns, weights)):
                values[f('item_odds', row, column)] = m.percent(w, f'itemConfig.{key}[{i}]')
        m.block('Item odds', values)

    if missing:
        m.out.notes.append(f'not in the file, the game keeps its values: {", ".join(missing)}')
    return m.out
