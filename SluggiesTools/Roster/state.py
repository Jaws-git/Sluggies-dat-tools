"""Read the exhibition draft grid back from the game files (GUI character grid, no side effects).

``read_state(image, dat)`` gives the grid as the game shows it, as a plain
JSON-able dict:

* ``kind``: ``'stock'`` (no roster sections) or ``'expanded'`` (a roster run);
* ``shape``: ``[columns, rows]``; ``luigi_own_square``: whether Luigi has a
  square (stock grid: no, the game hands him a captain's square at runtime);
* ``cells``: per cell in reading order, the index into ``squares`` or None
  (empty, hidden in game);
* ``squares``: per square ``kind`` (``'stock'``: a stock species family,
  ``'new'``: a roster square), ``head_index``, ``head`` (the character the
  square shows), ``members`` (its wheel, in wheel order) and ``voice``;
* ``characters``: per member ``id``, ``name`` (``{'en', 'fr', 'sp'}`` or
  None, as the name table holds it), ``default_name`` (a spare row whose
  table text is still the stock "#N/A": its usual name, e.g. "Black
  Kritter"; else None), ``template`` (new IDs), ``model_dir``, ``stats``
  (whose stats it plays with) and ``square``;
* ``warnings``: manifest facts the binary contradicts (the binary wins).

Most facts come from the binary: the grid shape (square count and D-pad
divide), the square -> head map (``MAP_COPY`` table), the head list, the
selector rows (species byte 2, selectable byte 6), the names (dir 121 file
5). The facts only hook code holds come from the roster manifest
(``manifest.py``). A DOL whose grid code is neither stock nor a roster this
tool built is refused (``StateError``), never guessed.
"""

import struct

try:
    from ..Dol import dolfile, inventory, relocate
    from . import dat_hammerspace as dhs
    from . import dol_hammerspace, grid, ids, manifest, names, wheels
except ImportError:
    from Dol import dolfile, inventory, relocate
    import dat_hammerspace as dhs
    import dol_hammerspace
    import grid
    import ids
    import manifest
    import names
    import wheels

VERSION = 1


class StateError(ValueError):
    pass


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


# --------------------------------------------------------------------------
# Binary facts
# --------------------------------------------------------------------------

def _grid_built(image: dolfile.DolImage) -> bool:
    lis = grid.MAP_COPY[0]
    return image.u32(lis) != inventory.site(grid.GROUP, lis).stock


def _shape(image: dolfile.DolImage) -> tuple[int, int]:
    """(columns, rows) from the square count and the D-pad divide, cross-checked."""
    word = image.u32(grid.COUNT_LOOP_B)
    if word >> 16 != 0x3800:
        raise StateError(f'square count 0x{grid.COUNT_LOOP_B:08X}: {word:08X} is not li r0,N')
    size = word & 0xFFFF
    hi = image.u32(grid.DIV_SITES[0][0]) & 0xFFFF
    cols = next((c for c, (h, _l, _s) in grid.DIVIDE.items() if h == hi), None)
    if cols is None:
        raise StateError(f'D-pad divide 0x{hi:04X} matches no grid width')
    rows, rest = divmod(size, cols)
    if rest or (cols, rows) not in grid.SHAPES:
        raise StateError(f'{size} squares at {cols} columns is not a grid shape this tool builds')
    if image.u32(grid.DOWN_ROWS) & 0xFFFF != rows:
        raise StateError(f'D-pad row bound {image.u32(grid.DOWN_ROWS) & 0xFFFF} disagrees with {rows} rows')
    return cols, rows


def selector_rows(image: dolfile.DolImage) -> list[bytes]:
    """Every selector row: the moved table (256 rows) or the stock one (to row 0x65)."""
    table = inventory.table('selector')
    at = relocate.pair_address(image, *table.all_pairs[0])
    count = ids.ROWS if at != table.address else ids.STOCK_IDS + 1
    base = at + table.header
    return [image.read(base + 8 * i, 8) for i in range(count)]


def head_list(image: dolfile.DolImage, count: int) -> bytes:
    table = inventory.table('head_list')
    return image.read(relocate.pair_address(image, *table.all_pairs[0]), count)


def read_names(image: dolfile.DolImage, dat) -> dict[int, dict[str, str]] | None:
    """``{id: {lang: text}}`` from the name tables (dir 121 file 5), or None without a DAT or directory."""
    if dat is None or not image.is_mapped(dhs.dol_base_address(dhs.hh._DIRS_START), 4 * dhs.hh._DIRS_COUNT):
        return None
    words = dhs.read_record(image, names.name_record(image))
    out: dict[int, dict[str, str]] = {}
    for lang in dhs.LANGS:
        offset, length, _alloc = dhs.slot(words, lang)
        msgs = names.messages(dat.read(offset, length))
        for cid in range(len(msgs) - len(names.CODE_ENTRIES)):
            out.setdefault(cid, {})[lang] = msgs[cid].decode('utf-16-be', errors='replace')
    return out


# --------------------------------------------------------------------------
# Grid
# --------------------------------------------------------------------------

def _stock_grid(image: dolfile.DolImage) -> tuple[int, int, list, list[int], bytes]:
    heads = image.read(grid.HEAD_LIST, grid.STOCK_HEADS)
    stock_map = image.read(grid.STOCK_MAP, grid.STOCK_SQUARES)
    cells = [('stock', h) for h in stock_map]
    return grid.STOCK_COLS, grid.STOCK_ROWS, cells, [], heads


def _expanded_grid(image: dolfile.DolImage, mf: dict, warnings: list[str]):
    cols, rows = _shape(image)
    size = cols * rows
    heads_map = image.read(relocate.pair_address(image, grid.MAP_COPY[0], grid.MAP_COPY[1]), size)
    heads = head_list(image, max(max(heads_map) + 1, grid.STOCK_HEADS))
    empty_char = heads[0]                  # empty cells' heads repeat head 0 (parse_grid refuses it on a square)
    square_heads = sorted({h for h in heads_map if h >= grid.STOCK_HEADS and heads[h] != empty_char})
    if square_heads != list(range(grid.STOCK_HEADS, grid.STOCK_HEADS + len(square_heads))):
        raise StateError('the new squares\' heads are not consecutive from 43: not a grid this tool built')
    cells = []
    for h in heads_map:
        if h < grid.SQUARE_HEADS:
            cells.append(('stock', h))
        elif h in square_heads:
            cells.append(('square', h - grid.STOCK_HEADS))
        elif h >= grid.STOCK_HEADS:
            cells.append(None)
        else:
            raise StateError(f'cell head {h} is a Mii group')
    stock_listed = [c for c in cells if c and c[0] == 'stock']
    if len(set(stock_listed)) != len(stock_listed) or len(stock_listed) != grid.SQUARE_HEADS:
        raise StateError('the stock squares are not each on the grid once: not a grid this tool built')

    mgrid = mf.get('grid')
    squares = []
    if mgrid is None:
        warnings.append('the manifest has no grid, but the DOL has one; new squares show only their head')
    else:
        if mgrid['shape'] != [cols, rows]:
            warnings.append(f'manifest grid shape {mgrid["shape"]} differs from the DOL\'s {[cols, rows]}')
        mcells = [None if c is None else tuple(c) for c in mgrid['cells']]
        if mcells != cells:
            warnings.append('manifest grid cells differ from the DOL\'s square -> head map')
        squares = [list(sq) for sq in mgrid['squares']]
    for k, h in enumerate(square_heads):
        if k >= len(squares):
            squares.append([heads[h]])
        elif squares[k][0] != heads[h]:
            warnings.append(f'manifest square {k} starts with {_hex(squares[k][0])}, the DOL shows {_hex(heads[h])}')
            squares[k] = [heads[h]] + [m for m in squares[k] if m != heads[h]]
    if len(squares) > len(square_heads):
        warnings.append(f'the manifest lists {len(squares)} new squares, the DOL {len(square_heads)}')
        squares = squares[:len(square_heads)]
    return cols, rows, cells, squares, heads


def _ordered(members: list[int], order: list[int]) -> list[int]:
    listed = [c for c in order if c in members]
    return listed + [c for c in members if c not in listed]


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def _manifest(image: dolfile.DolImage) -> dict | None:
    """The manifest of a roster-built DOL, None for a stock one; refuses DOLs this tool did not build."""
    try:
        hs = dol_hammerspace.DolHammerspace.open(image)
    except dolfile.DolError as exc:
        raise StateError(f'not a roster this tool built: {exc}') from exc
    if hs is None:
        bad = inventory.stock_mismatches(image, inventory.group(grid.GROUP))
        if bad:
            raise StateError('not a roster this tool built: the grid code is changed but there are no roster '
                             'sections (' + '; '.join(bad[:2]) + ')')
        return None
    try:
        mf = manifest.find(bytes(hs.data.blob))
    except manifest.ManifestError as exc:
        raise StateError(str(exc)) from exc
    if mf is None:
        raise StateError('this roster was built by an older version of the roster tool (no manifest): run the '
                         'roster again (menu [9]) to read it here')
    return mf


def read_state(image: dolfile.DolImage, dat=None) -> dict:
    """The grid as the game shows it (module docstring). ``dat``: anything with ``read(offset, size)``."""
    mf = _manifest(image)
    warnings: list[str] = []
    if mf is not None and _grid_built(image):
        cols, rows, cells, new_squares, heads = _expanded_grid(image, mf, warnings)
        luigi = True
    else:
        if mf is not None and mf.get('grid') is not None:
            warnings.append('the manifest has a grid, but the DOL\'s grid code is stock')
        cols, rows, cells, new_squares, heads = _stock_grid(image)
        luigi = False

    new_ids = {c[0]: {'template': c[1], 'wheel': c[2], 'swatch': c[3]} for c in (mf or {}).get('ids', [])}
    order = {s: o for s, o in (mf or {}).get('wheel_order', [])}
    rows_ = selector_rows(image)
    on_squares = {c for sq in new_squares for c in sq}
    families = wheels.wheel_members(rows_)

    def template_of(cid):
        return new_ids[cid]['template'] if cid in new_ids else cid

    squares, cell_index, by_square = [], [], {}
    for cell in cells:
        if cell is None:
            cell_index.append(None)
            continue
        if cell not in by_square:
            if cell[0] == 'stock':
                h = cell[1]
                members = [c for c in families.get(h, []) if c not in on_squares]
                square = {'kind': 'stock', 'head_index': h, 'head': heads[h],
                          'members': _ordered(members, order.get(h, [])), 'voice': heads[h]}
            else:
                k = cell[1]
                members = new_squares[k]
                square = {'kind': 'new', 'head_index': grid.STOCK_HEADS + k, 'head': members[0],
                          'members': list(members), 'voice': template_of(members[0])}
            by_square[cell] = len(squares)
            squares.append(square)
        cell_index.append(by_square[cell])

    text = read_names(image, dat)
    mnames = {int(k): v for k, v in (mf or {}).get('names', {}).items()}
    characters = []
    for index, square in enumerate(squares):
        for cid in square['members']:
            name = text.get(cid) if text is not None else None
            if cid in mnames and name is not None and name != mnames[cid]:
                warnings.append(f'{_hex(cid)}: the name table says {name.get("en")!r}, the manifest '
                                f'{mnames[cid].get("en")!r}')
            template = new_ids[cid]['template'] if cid in new_ids else None
            default = (wheels.SPARE_NAMES.get(cid) if name is not None and name.get('en') == wheels.SPARE_TEXT
                       else None)
            characters.append({'id': cid, 'name': name, 'default_name': default, 'template': template,
                               'model_dir': template_of(cid) + ids.MODEL_DIR_BASE,
                               'stats': template_of(cid), 'square': index})
    characters.sort(key=lambda c: c['id'])
    return {'version': VERSION, 'kind': 'stock' if mf is None else 'expanded', 'shape': [cols, rows],
            'luigi_own_square': luigi, 'cells': cell_index, 'squares': squares, 'characters': characters,
            'names_read': text is not None, 'warnings': warnings}


# --------------------------------------------------------------------------
# Display helpers (CLI text grid, GUI labels)
# --------------------------------------------------------------------------

def display_name(character: dict | None, cid: int | None = None) -> str:
    """The English name, or ``0xNN`` when the game shows no name of its own."""
    if character is None:
        return _hex(cid) if cid is not None else '?'
    if character.get('default_name'):
        return character['default_name']
    name = (character.get('name') or {}).get('en')
    if not name or name == names.UNNAMED:
        return f'(unnamed {_hex(character["id"])})'
    return name


def text_grid(state: dict) -> list[str]:
    """One line per grid row: cells as ``0xNN Name``, empty cells as ``-``."""
    by_id = {c['id']: c for c in state['characters']}
    cols, rows = state['shape']
    cells = []
    for index in state['cells']:
        if index is None:
            cells.append('-')
        else:
            head = state['squares'][index]['head']
            cells.append(f'{_hex(head)} {display_name(by_id.get(head), head)}')
    width = max(len(c) for c in cells)
    return [' | '.join(c.ljust(width) for c in cells[r * cols:(r + 1) * cols]) for r in range(rows)]
