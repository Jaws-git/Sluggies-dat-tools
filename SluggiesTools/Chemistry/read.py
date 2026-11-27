"""The chemistry a ``main.dol`` holds, per pair of character IDs.

The game stores chemistry per direction (``StatEditor/carry.chem_address``):
stock x stock is two bytes (each row's column), stock x new is one byte in the
new ID's row (both directions read it), new x new is two bytes of the separate
matrix. ``directional`` gives ``{(a, b): byte}`` for every ordered pair of two
different IDs of the roster; ``pairs`` folds it into ``{(a, b): (a towards b,
b towards a)}`` with ``a < b``. The game writes both directions equal; another
tool may not (``mismatch``).

``baseline`` is the same for the rows a roster run writes before any stat edit
(``bridge.baseline_rows``): the "unedited" chemistry of this roster, which for
a stock game is ``1_Input``'s.

Values: ``BAD`` 0, ``NEUTRAL`` 1, ``GOOD`` 2. A character's chemistry with
itself is not part of any of this.
"""

from dataclasses import dataclass

try:
    from ..Dol import dolfile
    from ..Roster import ids
    from ..StatEditor import bridge, carry, fields
except ImportError:
    from Dol import dolfile
    from Roster import ids
    from StatEditor import bridge, carry, fields

BAD, NEUTRAL, GOOD = 0, 1, 2
VALUES = (BAD, NEUTRAL, GOOD)
VALUE_NAMES = {BAD: 'bad', NEUTRAL: 'neutral', GOOD: 'good'}
MII_IDS = range(ids.PLAYER_END, ids.STOCK_IDS)           # the stock Miis


def value_name(value: int) -> str:
    return VALUE_NAMES.get(value, f'unknown ({value})')


@dataclass(frozen=True)
class Character:
    id: int
    name: str | None              # the name the game shows; None without ``dt_na.dat``
    family: int                   # the base character of its species (colour variants share one)
    kind: str                     # 'stock' / 'new'
    mii: bool

    @property
    def label(self) -> str:
        if self.mii and self.id in MII_IDS:
            return f'Mii {self.id - MII_IDS.start + 1}'
        return self.name or f'0x{self.id:02X}'


def _fold(present: list[int], row_of, matrix: bytes | None) -> dict[tuple[int, int], int]:
    """``{(a, b): byte}`` from each ID's chemistry columns (``row_of(cid)``: towards the stock IDs) and the new x new
    matrix."""
    out = {}
    stock = [c for c in present if c < ids.STOCK_IDS]
    new = [c for c in present if c >= ids.FIRST_NEW]
    for a in present:
        row = row_of(a)
        for b in stock:
            if a == b:
                continue
            out[(a, b)] = row[b]
            if a >= ids.FIRST_NEW:
                out[(b, a)] = row[b]                   # stock x new: one byte, both directions
    n = carry.NEW_X_NEW
    for a in new:
        for b in new:
            if a != b:
                out[(a, b)] = matrix[(a - ids.FIRST_NEW) * n + b - ids.FIRST_NEW]
    return out


def directional(image: dolfile.DolImage, place: carry.Placement) -> dict[tuple[int, int], int]:
    """``{(a, b): the byte of a towards b}`` for every two different IDs of ``image``'s roster."""
    stats = place.layouts['stats']
    matrix = image.read(place.matrix, carry.NEW_X_NEW ** 2) if place.matrix is not None else None
    return _fold(place.present,
                 lambda cid: image.read(stats.row_address(cid) + fields.CHEM_BASE, fields.CHEM_COLUMNS), matrix)


def baseline(vanilla: dolfile.DolImage, place: carry.Placement) -> dict[tuple[int, int], int]:
    """``directional`` of what the roster writes without stat edits (``vanilla``: ``1_Input/main.dol``)."""
    rows = bridge.baseline_rows(vanilla, place.roster)['stats']
    matrix = bridge.new_by_new_baseline(vanilla, place.roster) if place.roster.new else None
    return _fold(place.present,
                 lambda cid: rows[cid][fields.CHEM_BASE:fields.CHEM_BASE + fields.CHEM_COLUMNS], matrix)


def pairs(values: dict[tuple[int, int], int]) -> dict[tuple[int, int], tuple[int, int]]:
    """``{(a, b): (a towards b, b towards a)}``, ``a < b``."""
    return {(a, b): (v, values[(b, a)]) for (a, b), v in values.items() if a < b}


def matrix(image: dolfile.DolImage, place: carry.Placement | None = None) -> dict[tuple[int, int], tuple[int, int]]:
    """``pairs`` of ``image``; empty when it does not map the stat tables. Raises ``bridge.BridgeError`` for a
    roster that cannot be read."""
    place = place or carry.placement(image)
    return pairs(directional(image, place)) if place is not None else {}


def mismatch(pair: tuple[int, int]) -> bool:
    """The two directions differ (never in the stock game)."""
    return pair[0] != pair[1]


def characters(image: dolfile.DolImage, dat, place: carry.Placement) -> list[Character]:
    """The roster's characters in ID order (``dat``: anything with ``read(offset, size)``, for the names; None
    leaves them out)."""
    out = []
    for c in bridge.character_list(image, dat, place.roster):
        cid, family = int(c['id'], 16), int(c['family'], 16)
        out.append(Character(cid, c['name'], family, c['kind'], cid in MII_IDS or family in MII_IDS))
    return out
