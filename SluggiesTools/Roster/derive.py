"""Read -> rebuild (GUI character grid): the roster config that rebuilds the game files as they are.

Every GUI change to the roster (patch a slot, rename, clear, load a pack) is
read -> rebuild: read the roster from the output files, turn it into a
**derived config**, apply the one change, then reset and rebuild with the
roster steps (``start.py --roster --state FILE``). The game files stay the
only truth; nothing is remembered between runs.

``derive(image, dat)`` gives that config, plus the portraits it names:

* ``ids``, ``wheels``, ``grid`` and ``wheel_order`` as the steps read them.
  Facts the binary holds come from the binary (grid shape and cells, spare
  rows, names, portraits); facts only hook code holds come from the roster
  manifest (``manifest.py``: new IDs, square members, wheel order). A
  top-level key is present exactly when the original config had it, as
  "ids" and "wheels" change the build even when empty.
* ``names``: only the IDs the original config named (manifest), each with
  the text its name table holds now.
* ``icon`` on every entry that has own art, in the bank's resource-row order
  (the order the icons step packs them in), with the ``like`` its records
  were copied from. The portraits are the bank's own cells: the 48x51 PNG
  decoded from the page plus the cell's CMPR blocks (``<name>.cmpr`` beside
  it). The icons step keeps those blocks for a PNG that still matches them,
  so an unchanged portrait keeps its bytes (wimgt's decode -> encode drifts);
  a replaced PNG is encoded afresh.

Rebuilding from an unchanged derived config gives byte-identical ``main.dol``
and ``dt_na.dat`` (``tests/test_roster_derive.py``). ``write(derived,
folder)`` stores it as ``roster.json`` plus ``icons/``; the runner's
``--state`` reads that folder.
"""

import json
import os
import struct
from dataclasses import dataclass, field

from PIL import Image

try:
    from ..Dol import dolfile, inventory, relocate
    from ..Icons import gx_decode
    from . import icon_art, icons, ids, state, state_icons
except ImportError:
    from Dol import dolfile, inventory, relocate
    from Icons import gx_decode
    import icon_art
    import icons
    import ids
    import state
    import state_icons

CONFIG_FILE = 'roster.json'
ICON_DIR = 'icons'                 # beside CONFIG_FILE; the runner's --state reads portraits from here
VIEWS = ('side', 'front')
PAGES = {'side': icons.SIDE_PAGE, 'front': icons.FRONT_PAGE}


class DeriveError(ValueError):
    pass


@dataclass
class Derived:
    config: dict
    portraits: dict = field(default_factory=dict)      # {file name: (48x51 RGBA image, cell blocks)}
    warnings: list = field(default_factory=list)


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


# --------------------------------------------------------------------------
# Config keys and entries
# --------------------------------------------------------------------------

def tables_moved(image: dolfile.DolImage) -> bool:
    """Whether the ids step ran (it moves the per-ID tables even with no new IDs)."""
    table = inventory.table('selector')
    return relocate.pair_address(image, *table.all_pairs[0]) != table.address


def config_keys(image: dolfile.DolImage, mf: dict) -> list[str]:
    """The original config's top-level keys (``manifest.CONFIG_KEYS``): stored by newer runs, else inferred."""
    if mf.get('config_keys') is not None:
        return list(mf['config_keys'])
    keys = []
    if tables_moved(image):
        keys.append('ids')
    if mf.get('spares'):
        keys.append('wheels')
    if mf.get('grid') is not None:
        keys.append('grid')
    if mf.get('wheel_order'):
        keys.append('wheel_order')
    return keys


def spare_wheels(image: dolfile.DolImage, mf: dict) -> list[tuple[int, int | None, int | None]]:
    """``[(spare id, wheel, swatch)]`` as configured (manifest), or read back from the selector rows."""
    if 'spare_wheels' in mf:
        return [tuple(entry) for entry in mf['spare_wheels']]
    rows = state.selector_rows(image)
    return [(cid, rows[cid][1], rows[cid][7]) for cid in mf.get('spares') or []]


def grid_config(st: dict) -> dict:
    """``grid`` from the read state: its shape, every cell in reading order (``order``) and the new squares."""
    new = sorted((sq for sq in st['squares'] if sq['kind'] == 'new'), key=lambda sq: sq['head_index'])
    order = [None if i is None else _hex(st['squares'][i]['head']) for i in st['cells']]
    while order and order[-1] is None:
        order.pop()
    return {'shape': list(st['shape']), 'squares': [[_hex(c) for c in sq['members']] for sq in new],
            'order': order}


def _ordered_ids(entries: dict[int, dict], rank: dict[int, int], wheel_of: dict[int, int | None]) -> list[dict]:
    """The ids entries in icon order (then by ID), each new-ID wheel host before its members (``parse_ids``)."""
    out, done = [], set()

    def emit(cid, path=()):
        if cid in done:
            return
        if cid in path:
            raise DeriveError(f'new ID {_hex(cid)}: its wheel hosts form a loop')
        host = wheel_of[cid]
        if host is not None and host in entries:
            emit(host, path + (cid,))
        done.add(cid)
        out.append(entries[cid])
    for cid in sorted(entries, key=lambda c: (rank.get(c, len(rank)), c)):
        emit(cid)
    return out


# --------------------------------------------------------------------------
# Portraits
# --------------------------------------------------------------------------

def _records(bank: bytes, field_: int) -> dict[int, bytes]:
    desc = struct.unpack_from('>I', bank, 4)[0] + icons.CONTAINER_HEAD
    return {icons._key(r): bytes(r) for r in icons._table(bank, icons._pointer(bank, desc, field_))}


def _same_donor(record: bytes, donor: bytes) -> bool:
    """Whether ``record`` is a copy of ``donor`` as ``extend_table`` makes it (key, kind, row and the
    following-record flag replaced)."""
    flags = lambda r: int.from_bytes(r[0:2], 'big') & ~icons.FOLLOWING_RECORD_FLAG
    return record[8:] == donor[8:] and flags(record) == flags(donor)


def _like(cid: int, default: int, records: dict[int, dict[int, bytes]], stock: dict[int, dict[int, bytes]]) -> int | None:
    """The ``like`` whose stock records ``cid``'s records copy (``default`` first), or None."""
    fields = [icons.SIDE_FIELD, icons.FRONT_FIELD] + ([icons.NORMAL_A_FIELD] if cid >= ids.FIRST_NEW else [])
    for like in [default] + [c for c in range(ids.PLAYER_END) if c != default]:
        ok = True
        for f in fields:
            table = stock[f]
            below = [k for k in table if k <= like]
            if cid not in records[f] or not below:
                ok = False
                break
            donor = table.get(like) or table[max(below)]
            if not _same_donor(records[f][cid], donor):
                ok = False
                break
        if ok:
            return like
    return None


def own_portraits(image: dolfile.DolImage, dat) -> tuple[list[dict], dict, dict]:
    """The icon entries the bank holds, in its resource-row order (``{'id', 'side': (name, image, blocks),
    'front': ...}``), plus the bank's and the stock bank's source records per table (for ``_like``). A stock bank
    gives no entries."""
    bank = state_icons.read_bank(image, dat)
    n, rest = divmod(len(bank.rows) - icons.STOCK_ROWS, 2)
    if n <= 0:
        return [], {}, {}
    if rest:
        raise DeriveError(f'the icon bank has {len(bank.rows)} resource rows: not a bank the icons step packed')
    by_row = {view: {row: key for key, row in bank.tables[view]} for view in VIEWS}
    stock_data = dat.read(icons.STOCK_BANK_OFFSET, icons.STOCK_BANK_LENGTH)
    records = {f: _records(bank.data, f) for f in icons.TABLE_FIELDS}
    stock = {f: _records(stock_data, f) for f in icons.TABLE_FIELDS}
    pages = {}
    out = []
    for i in range(n):
        rows = {'side': icons.STOCK_ROWS + i, 'front': icons.STOCK_ROWS + n + i}
        cids = {view: by_row[view].get(rows[view]) for view in VIEWS}
        if cids['side'] is None or cids['side'] != cids['front']:
            raise DeriveError(f'icon bank rows {rows["side"]}/{rows["front"]}: no key owns both '
                              f'(side {cids["side"]}, front {cids["front"]})')
        cid = cids['side']
        entry = {'id': cid}
        for view in VIEWS:
            page_id, (x, y, w, h) = bank.row_rect(rows[view])
            if page_id != PAGES[view] or (w, h) != (icon_art.ICON_WIDTH, icon_art.ICON_HEIGHT) or x % 4 or y % 4:
                raise DeriveError(f'{_hex(cid)} {view}: resource row {rows[view]} is not a packed portrait cell')
            if page_id not in pages:
                pages[page_id] = bank.page(page_id)
            page = pages[page_id]
            if page['format'] != gx_decode.CMPR:
                raise DeriveError(f'page 0x{page_id:02X} is not CMPR')
            blocks = icon_art.cell_blocks(page['image'], page['width'], x, y, icons.CELL)
            cell = gx_decode.decode(gx_decode.CMPR, icon_art.cell_payload(blocks, icons.CELL), icons.CELL, icons.CELL)
            art = Image.fromarray(cell[:icon_art.ICON_HEIGHT, :icon_art.ICON_WIDTH], 'RGBA')
            entry[view] = (f'{view}_{x}_{y}.png', art, blocks)
        out.append(entry)
    return out, records, stock


# --------------------------------------------------------------------------
# Derive
# --------------------------------------------------------------------------

def derive(image: dolfile.DolImage, dat) -> Derived:
    """The derived config of the output files (module docstring)."""
    if dat is None:
        raise DeriveError('dt_na.dat is missing: the roster cannot be read back without it')
    mf = state._manifest(image)
    st = state.read_state(image, dat)
    out = Derived({'version': 1, 'comment': 'Derived from the game files (GUI character grid, read -> rebuild).'})
    out.warnings += st['warnings']
    if mf is None:
        return out                                     # a stock output: nothing to carry
    keys = config_keys(image, mf)
    config = out.config

    new_ids = {c[0]: c for c in mf.get('ids') or []}
    id_entries: dict[int, dict] = {}
    for cid, template, wheel, swatch in new_ids.values():
        entry = {'id': _hex(cid), 'template': _hex(template), 'wheel': None if wheel is None else _hex(wheel)}
        if swatch is not None:
            entry['swatch'] = swatch
        id_entries[cid] = entry
    spare_entries: dict[int, dict] = {}
    for cid, wheel, swatch in spare_wheels(image, mf):
        entry = {'id': _hex(cid)}
        if wheel is not None:
            entry['wheel'] = _hex(wheel)
        if swatch is not None:
            entry['swatch'] = swatch
        spare_entries[cid] = entry

    # names: the IDs the config named, with the text the name tables hold now
    text = state.read_names(image, dat) or {}
    for key, value in (mf.get('names') or {}).items():
        cid = int(key)
        entry = id_entries.get(cid) or spare_entries.get(cid)
        if entry is None:
            out.warnings.append(f'{_hex(cid)} is named but has no ids/wheels entry: name dropped')
            continue
        entry['name'] = dict(text.get(cid) or value)

    # portraits, in the bank's order
    rank = {}
    portraits, records, stock = own_portraits(image, dat)
    for i, p in enumerate(portraits):
        cid = p['id']
        entry = id_entries.get(cid) or spare_entries.get(cid)
        if entry is None:
            out.warnings.append(f'{_hex(cid)} has own portraits but no ids/wheels entry: portraits dropped')
            continue
        default = new_ids[cid][1] if cid in new_ids else icons.SPARE_DONORS[cid]
        like = _like(cid, default, records, stock)
        if like is None:
            out.warnings.append(f'{_hex(cid)}: its icon records copy no stock record; '
                                f'using 0x{default:02X} (record flags may change)')
            like = default
        entry['icon'] = {'side': p['side'][0], 'front': p['front'][0], 'like': _hex(like), 'fit': 'strict'}
        for view in VIEWS:
            name, art, blocks = p[view]
            out.portraits.setdefault(name, (art, blocks))
        rank[cid] = i

    if 'ids' in keys:
        wheel_of = {cid: new_ids[cid][2] for cid in id_entries}
        config['ids'] = _ordered_ids(id_entries, rank, wheel_of)
    elif id_entries:
        out.warnings.append('the manifest lists new IDs, but the per-ID tables are not moved: new IDs dropped')
    if 'wheels' in keys:
        config['wheels'] = [spare_entries[c] for c in sorted(spare_entries, key=lambda c: (rank.get(c, len(rank)), c))]
    if 'grid' in keys:
        if st['kind'] == 'expanded' and st['luigi_own_square']:
            config['grid'] = grid_config(st)
        else:
            out.warnings.append('the manifest has a grid, but the DOL\'s grid is stock: grid dropped')
    if 'wheel_order' in keys:
        config['wheel_order'] = [[_hex(c) for c in order] for _species, order in mf.get('wheel_order') or []]
    return out


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

def write(derived: Derived, folder: str) -> str:
    """``roster.json`` and ``icons/`` (each portrait's PNG and kept CMPR blocks) in ``folder``; returns the JSON's
    path. Portrait files from an earlier derive are removed."""
    icon_dir = os.path.join(folder, ICON_DIR)
    os.makedirs(icon_dir, exist_ok=True)
    for name in os.listdir(icon_dir):
        if name.endswith(('.png', icons.KEPT_BLOCKS_EXT)):
            os.remove(os.path.join(icon_dir, name))
    for name, (art, blocks) in derived.portraits.items():
        path = os.path.join(icon_dir, name)
        art.save(path, 'PNG')
        with open(icons.kept_blocks_path(path), 'wb') as f:
            f.write(blocks)
    path = os.path.join(folder, CONFIG_FILE)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(derived.config, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def icon_dir_of(config_path: str) -> str:
    """The portrait folder of a derived config written by ``write``."""
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), ICON_DIR)
