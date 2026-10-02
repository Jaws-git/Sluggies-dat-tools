"""The DOL site inventory (``site_inventory.json``): addresses and stock words for every expansion step.

``probe_site_inventory.py`` writes the JSON from the clean US ``main.dol``
and the external tool's site lists (P12 of the character-expansion plan).
Steps read their addresses here instead of declaring constants, and check
each stock word before writing.
"""

import json
import os
from dataclasses import dataclass
from functools import lru_cache

try:
    from . import dolfile
except ImportError:
    import dolfile

INVENTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'site_inventory.json')


class InventoryError(dolfile.DolError):
    pass


@dataclass(frozen=True)
class Site:
    address: int
    stock: int
    tool: int | None = None
    note: str = ''


@dataclass(frozen=True)
class Table:
    name: str
    address: int
    row_size: int
    header: int
    rows: int
    status: str
    pairs: tuple[tuple[int, int], ...]
    extra_pairs: tuple[tuple[int, int], ...]
    note: str = ''

    @property
    def length(self) -> int:
        """Bytes covered by the stock rows (header included)."""
        return self.header + self.row_size * self.rows

    @property
    def all_pairs(self) -> tuple[tuple[int, int], ...]:
        return self.pairs + self.extra_pairs


def _hex(value: str) -> int:
    return int(value, 16)


@lru_cache(maxsize=None)
def load(path: str = INVENTORY_PATH) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def group(name: str, path: str = INVENTORY_PATH) -> tuple[Site, ...]:
    groups = load(path)['groups']
    if name not in groups:
        raise InventoryError(f'site group {name!r} is not in the inventory')
    return tuple(Site(_hex(s['address']), _hex(s['stock']), _hex(s['tool']) if 'tool' in s else None,
                      s.get('note', '')) for s in groups[name])


def site(group_name: str, address: int, path: str = INVENTORY_PATH) -> Site:
    for entry in group(group_name, path):
        if entry.address == address:
            return entry
    raise InventoryError(f'0x{address:08X} is not in site group {group_name!r}')


def table(name: str, path: str = INVENTORY_PATH) -> Table:
    for t in load(path)['tables']:
        if t['name'] == name:
            pairs = tuple((_hex(a), _hex(b)) for a, b in t['pairs'])
            extra = tuple((_hex(a), _hex(b)) for a, b in t['extra_pairs'])
            return Table(t['name'], _hex(t['address']), t['row_size'], t['header'], t['rows'], t['status'],
                         pairs, extra, t.get('note', ''))
    raise InventoryError(f'table {name!r} is not in the inventory')


def tables(status: str | None = None, path: str = INVENTORY_PATH) -> list[Table]:
    names = [t['name'] for t in load(path)['tables'] if status is None or t['status'] == status]
    return [table(n, path) for n in names]


def all_pairs(path: str = INVENTORY_PATH) -> list[tuple[int, int, int]]:
    """``(lis_site, low_site, table address)`` for every known table pair, for shared-``lis`` checks."""
    out = []
    for t in tables(path=path):
        out += [(a, b, t.address) for a, b in t.all_pairs]
    return out


def stock_mismatches(image: dolfile.DolImage, sites) -> list[str]:
    """One line per site whose current word is not its stock word."""
    out = []
    for s in sites:
        got = image.u32(s.address)
        if got != s.stock:
            out.append(f'0x{s.address:08X}: {got:08X}, stock {s.stock:08X}')
    return out
