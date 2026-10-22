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
file differs from its defaults, which equal the stock bytes: star usage,
charge odds, modifiers, preferred pitch and item odds as whole tables; each
star cost entry, the stamina cost and each star boost row on its own. The
migration keeps exactly those blocks, "differs" tested against
``1_Input/main.dol``; everything else keeps the game's value.

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
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
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


def migrate(doc: dict, vanilla: dict) -> Migrated:
    """The values ``doc``'s Gecko codes wrote (module docstring); ``vanilla``: ``{field: value}`` of
    ``1_Input/main.dol`` (``read.values``). Raises ``LegacyError``."""
    if not is_legacy(doc):
        raise LegacyError('not an AI Gameplay Tool file (no "starConfig")')
    m = _Migration(vanilla)
    f = tables.field
    missing = []

    star = doc['starConfig']
    values = {}
    for row, cls in zip(tables.CLASSES, CLASSES):
        entry = _get(star, cls, 'starConfig')
        for column, key in zip(tables.TABLES_BY_KEY['star_usage'].columns, STAR_KEYS):
            where = f'starConfig.{cls}.{key}'
            values[f('star_usage', row, column)] = m.percent(_get(entry, key, f'starConfig.{cls}'), where)
    m.block('Star usage odds', values)

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

    charge = doc.get('chargeConfig')
    if charge is None:
        missing.append('chargeConfig')
    else:
        values = {}
        for row, cls in zip(tables.CLASSES, CLASSES):
            entry = _get(charge, cls, 'chargeConfig')
            for column, key in (('0_1_strikes', 'charge01'), ('2_strikes', 'charge2')):
                values[f('charge_odds', row, column)] = m.percent(_get(entry, key, f'chargeConfig.{cls}'),
                                                                  f'chargeConfig.{cls}.{key}')
        m.block('Charge swing odds', values)

    modifiers = doc.get('modifierConfig')
    if modifiers is None:
        missing.append('modifierConfig')
    else:
        values = {}
        for row, level in zip(tables.TABLES_BY_KEY['star_modifiers'].rows, LEVELS):
            entry = _get(modifiers, level, 'modifierConfig')
            for column in ('normal', 'special'):
                where = f'modifierConfig.{level}.{column}'
                values[f('star_modifiers', row, column)] = m.clamp(_get(entry, column, f'modifierConfig.{level}'),
                                                                   -0x80, 0x7F, where, 'signed byte,')
        m.block('Star swing modifiers', values)

    pitch = doc.get('pitchConfig')
    if pitch is None:
        missing.append('pitchConfig')
    else:
        values = {}
        for row, cls in zip(tables.CLASSES, CLASSES):
            entry = _get(pitch, cls, 'pitchConfig')
            for strikes, pkey, ckey in (('0_1', 'pitch01', 'prob01'), ('2', 'pitch2', 'prob2')):
                kind = _get(entry, pkey, f'pitchConfig.{cls}')
                if kind not in (0, 1, 2) or isinstance(kind, bool):
                    raise LegacyError(f'pitchConfig.{cls}.{pkey}: {kind!r} is not a pitch type (0, 1 or 2)')
                values[f('preferred_pitch', row, f'pitch_{strikes}')] = int(kind)
                values[f('preferred_pitch', row, f'chance_{strikes}')] = m.percent(
                    _get(entry, ckey, f'pitchConfig.{cls}'), f'pitchConfig.{cls}.{ckey}')
        m.block('Preferred pitch', values)

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
