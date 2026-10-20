"""Read the exhibition draft grid back from the game files (GUI character grid, no side effects).

``read_state(image, dat)`` gives the grid as the game shows it, as a plain
JSON-able dict:

* ``kind``: ``'stock'`` (no roster sections) or ``'expanded'`` (a roster run);
* ``shape``: ``[columns, rows]``; ``luigi_own_square``: whether Luigi has a
  square (stock grid: no, the game hands him a captain's square at runtime);
* ``cells``: per cell in reading order, the index into ``squares`` or None
  (empty, hidden in game);
* ``off_grid``: indices of squares on no cell (``off_grid: True``): Luigi's
  family on the stock grid, which the GUI shows beside the grid so his
  slots stay reachable;
* ``squares``: per square ``kind`` (``'stock'``: a stock species family,
  ``'new'``: a roster square), ``head_index``, ``head`` (the character the
  square shows), ``members`` (its wheel, in wheel order), ``voice`` (the
  base character whose voice the head speaks with: selector byte 2, through
  the stock squares' voice swaps, ``voices.py``) and ``voice_set`` (the voice
  the config set, or None: a new square's ``voice``, a stock square's
  ``stock_voices`` entry);
* ``characters``: per member ``id``, ``name`` (``{'en', 'fr', 'sp'}`` or
  None, as the name table holds it), ``default_name`` (a spare row whose
  table text is still the stock "#N/A": its usual name, e.g. "Black
  Kritter"; else None), ``template`` (new IDs), ``model_dir`` (with
  ``own_model_dir``: a new ID's own directory, ``model_source``: whose files
  it holds), ``stats``
  (whose stats it plays with: the configured stats source, else the
  template), ``family`` (the base character of its species, selector byte
  2: the square whose voice and wheel it shares), ``voice`` (the base
  character whose voice it speaks with: its own character voice, else its
  species' through the swaps), ``char_voice`` (the configured character
  voice, ``character_voices``, or None), ``char_clip_ids`` (its custom clips,
  ``{moment: INFO ID}``; None without a character voice) and ``char_pack``
  (the voice pack label they came from, or None); squares carry ``clip_ids``
  (the stock voice's moments ``species_clips`` replaced, ``{moment: INFO ID}``);
  ``state_cli`` turns both into ``char_clips`` / ``clips`` (``{moment: SHA-1 of the RWAV}``, None when there are
  none) for the fingerprints), ``square``, ``icon`` (``{'front', 'side', 'side_alt'}``:
  where the game takes each portrait from, ``state_icons.resolve``; the
  alternate side view carries ``distinct`` when it is a cell of its own; None
  without a DAT) and ``blocks`` (``{'high', 'low'}``: the model directory's
  files 0 and 1 as the game loads them, ``offset``, ``length``, ``sha1``; a
  patch changes the fingerprint; None without a DAT) and ``equipment``
  (``{'bat', 'glove_l', 'glove_r', 'extra'}``: the model directory's files
  2-5 the same way, plus ``placeholder`` (an empty file 5) and
  ``shared_with`` (how many other slots of this state load the same block);
  ``state_cli`` adds ``vanilla``: the block equals its vanilla source's) and
  ``animations`` (a list, one entry per animation bank, files 6-14: ``file``,
  ``offset``, ``length``, ``sha1``, ``shared_with``, and ``state_cli``'s
  ``vanilla``);
* ``warnings``: manifest facts the binary contradicts (the binary wins).

Most facts come from the binary: the grid shape (square count and D-pad
divide), the square -> head map (``MAP_COPY`` table), the head list, the
selector rows (species byte 2, selectable byte 6), the names (dir 121 file
5). The facts only hook code holds come from the roster manifest
(``manifest.py``). A DOL whose grid code is neither stock nor a roster this
tool built is refused (``StateError``), never guessed.
"""

import hashlib
import struct

try:
    from ..Dol import dolfile, inventory, relocate
    from . import dat_hammerspace as dhs
    from . import anim, dol_hammerspace, gear, grid, ids, layout_file, manifest, names, state_icons, voices, wheels
except ImportError:
    from Dol import dolfile, inventory, relocate
    import anim
    import dat_hammerspace as dhs
    import dol_hammerspace
    import gear
    import grid
    import ids
    import layout_file
    import manifest
    import names
    import state_icons
    import voices
    import wheels

VERSION = 1


class StateError(ValueError):
    pass


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


def _moment(slot: int) -> str:
    try:
        from ..Sound import sound_packs
    except ImportError:
        from Sound import sound_packs
    return sound_packs.MOMENT_KEYS[slot]


def clip_hashes(result: dict, archive) -> None:
    """Replace the clip INFO IDs of a read state by the SHA-1 of their RWAV (``archive``: the output ``Brsar``, or
    None: unknown hashes): characters get ``char_clips``, squares ``clips`` (``{moment: sha1 or None}``; None when
    there are none)."""
    try:
        from ..Sound import brsar, brsar_write
    except ImportError:
        from Sound import brsar, brsar_write
    cache: dict[int, str | None] = {}

    def sha(sid):
        if sid not in cache:
            cache[sid] = None
            if archive is not None:
                sounds = archive.sounds()
                if 0 <= sid < len(sounds) and sounds[sid].type == brsar.WAVE:
                    try:
                        cache[sid] = hashlib.sha1(brsar_write.sound_rwav(archive, sounds[sid])).hexdigest()
                    except Exception:            # BrsarError / WriteError of either import path, struct errors
                        pass
        return cache[sid]

    def convert(ids_):
        return {m: sha(i) for m, i in ids_.items()} if ids_ else None
    for char in result['characters']:
        char['char_clips'] = convert(char.get('char_clip_ids'))
    for square in result['squares']:
        square['clips'] = convert(square.get('clip_ids'))


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


def renamed_ids(image: dolfile.DolImage, dat) -> list[int]:
    """The IDs below the Miis whose name plate a names run replaced (``names.renamed_ids``, EN select layout)."""
    if dat is None:
        return []
    offset, length, _alloc = dhs.slot(dhs.read_record(image, layout_file.RECORD), 'en')
    return names.renamed_ids(dat.read(offset, length))


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
    """The manifest of a roster-built DOL (data section, else the trailer of a run without DOL code), None for a
    stock one; refuses DOLs this tool did not build."""
    try:
        hs = dol_hammerspace.DolHammerspace.open(image)
    except dolfile.DolError as exc:
        raise StateError(f'not a roster this tool built: {exc}') from exc
    if hs is None or not hs.has_data:   # no sections, or only game option stubs (CPU vs CPU): a stock roster
        bad = inventory.stock_mismatches(image, inventory.group(grid.GROUP))
        if bad:
            raise StateError('not a roster this tool built: the grid code is changed but there are no roster '
                             'sections (' + '; '.join(bad[:2]) + ')')
        try:                            # a run without DOL code: its manifest is the file's trailer, if any
            return manifest.find_trailer(image)
        except manifest.ManifestError as exc:
            raise StateError(str(exc)) from exc
    try:
        mf = manifest.find(bytes(hs.data.blob))
    except manifest.ManifestError as exc:
        raise StateError(str(exc)) from exc
    if mf is None:
        raise StateError('this roster was built by an older version of the roster tool (no manifest): run the '
                         'roster again (menu [7]) to read it here')
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
    stats_of = {cid: src for cid, src in (mf or {}).get('stats') or []}
    voices_set = ((mf or {}).get('grid') or {}).get('voices') or []
    remap = {s: v for s, v in (mf or {}).get('stock_voices') or []}
    char_voices = manifest.character_voices(mf)
    replaced = manifest.species_clips(mf)          # {(species, slot): INFO ID}: a stock voice's replaced clips
    own_dirs = {c[0]: (c[1], c[2]) for c in (mf or {}).get('model_dirs') or []}
    order = {s: o for s, o in (mf or {}).get('wheel_order', [])}
    rows_ = selector_rows(image)
    on_squares = {c for sq in new_squares for c in sq}
    families = wheels.wheel_members(rows_)

    def template_of(cid):
        return new_ids[cid]['template'] if cid in new_ids else cid

    def family_of(cid):
        """The base character of ``cid``'s species (``cid`` itself outside the squares' species)."""
        species = rows_[cid][2]
        return heads[species] if species < grid.SQUARE_HEADS else cid

    def voice_of(cid):
        """The base character whose voice ``cid`` speaks with."""
        species = voices.effective(remap, rows_[cid][2])
        return heads[species] if species < grid.SQUARE_HEADS else cid

    def stock_square_voice(h):
        return (heads[voices.effective(remap, h)],
                heads[remap[h]] if h in remap else None)

    def species_clip_ids(h):
        """The moments of species ``h``'s own voice that ``species_clips`` replaced: ``{moment: INFO ID}``."""
        return {_moment(k): i for (s, k), i in sorted(replaced.items()) if s == h}

    def own_voice(cid):
        """``(voice, char_voice, clip ids, pack)`` of ``cid``: its character voice when it has one."""
        entry = char_voices.get(cid)
        if entry is None:
            return voice_of(cid), None, None, None
        voice = entry['voice'] if entry['voice'] is not None else voice_of(cid)
        return voice, entry['voice'], {_moment(k): i for k, i in sorted(entry['clips'].items())}, entry['pack']

    squares, cell_index, by_square = [], [], {}
    for cell in cells:
        if cell is None:
            cell_index.append(None)
            continue
        if cell not in by_square:
            if cell[0] == 'stock':
                h = cell[1]
                members = [c for c in families.get(h, []) if c not in on_squares]
                voice, voice_set = stock_square_voice(h)
                square = {'kind': 'stock', 'head_index': h, 'head': heads[h],
                          'members': _ordered(members, order.get(h, [])), 'voice': voice, 'voice_set': voice_set,
                          'clip_ids': species_clip_ids(h)}
            else:
                k = cell[1]
                members = new_squares[k]
                voice_set = voices_set[k] if k < len(voices_set) else None
                square = {'kind': 'new', 'head_index': grid.STOCK_HEADS + k, 'head': members[0],
                          'members': list(members), 'voice': voice_of(members[0]), 'voice_set': voice_set,
                          'clip_ids': {}}
                if voice_set is not None:
                    wanted = rows_[voice_set][2]
                    off = [m for m in members if m in new_ids and new_ids[m]['wheel'] is None
                           and voices.effective(remap, rows_[m][2]) != wanted]
                    if off:
                        warnings.append(f'square {_hex(members[0])}: voice {_hex(voice_set)} is set, but '
                                        + ', '.join(_hex(m) for m in off) + ' speak with another species')
            by_square[cell] = len(squares)
            squares.append(square)
        cell_index.append(by_square[cell])
    off_grid = []
    if not luigi:                         # the stock grid has no Luigi square: list his family beside the grid
        h = grid.LUIGI_HEAD
        members = [c for c in families.get(h, []) if c not in on_squares]
        if members:
            off_grid.append(len(squares))
            voice, voice_set = stock_square_voice(h)
            squares.append({'kind': 'stock', 'head_index': h, 'head': heads[h],
                            'members': _ordered(members, order.get(h, [])), 'voice': voice, 'voice_set': voice_set,
                            'clip_ids': species_clip_ids(h), 'off_grid': True})

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
            own = own_dirs.get(cid)
            voice, char_voice, clip_ids, pack = own_voice(cid)
            characters.append({'id': cid, 'name': name, 'default_name': default, 'template': template,
                               'model_dir': own[0] if own else template_of(cid) + ids.MODEL_DIR_BASE,
                               'own_model_dir': own is not None,
                               'model_source': own[1] if own else template_of(cid),
                               'stats': stats_of.get(cid, template_of(cid)), 'family': family_of(cid),
                               'voice': voice, 'char_voice': char_voice, 'char_clip_ids': clip_ids,
                               'char_pack': pack, 'square': index})
    characters.sort(key=lambda c: c['id'])
    _model_blocks(image, dat, characters, warnings)
    icons_read = _resolve_icons(image, dat, mf, characters, warnings)
    return {'version': VERSION, 'kind': 'stock' if mf is None else 'expanded', 'shape': [cols, rows],
            'luigi_own_square': luigi, 'cells': cell_index, 'off_grid': off_grid, 'squares': squares,
            'characters': characters,
            'names_read': text is not None, 'icons_read': icons_read, 'warnings': warnings}


BLOCK_FILES = {'high': 0, 'low': 1}       # a character directory's high-poly model and its L_ partner


def _model_blocks(image: dolfile.DolImage, dat, characters: list[dict], warnings: list[str]) -> None:
    """Each character's ``blocks`` (module docstring); None without a DAT."""
    for c in characters:
        c['blocks'] = None
        c['equipment'] = None
        c['animations'] = None
    if dat is None:
        return
    try:
        pointers = dhs.dir_pointers(image)
    except dolfile.DolError as exc:
        warnings.append(f'no model blocks: {exc}')
        return
    seen: dict[tuple[int, int], str] = {}

    def entry_of(directory: int, index: int) -> dict | None:
        """``{'offset', 'length', 'sha1'}`` of file ``index`` of ``directory`` as the game loads it, or None."""
        address = pointers[directory] + dhs.RECORD_SIZE * index
        if not image.is_mapped(address, dhs.RECORD_SIZE):
            return None
        words = struct.unpack('>12I', image.read(address, dhs.RECORD_SIZE))
        if words[0] != dhs.hh._DAT_FNAME_PTR:
            return None
        offset, length, _alloc = dhs.slot(words, 'en')
        if (offset, length) not in seen:
            seen[(offset, length)] = hashlib.sha1(dat.read(offset, length)).hexdigest()
        return {'offset': offset, 'length': length, 'sha1': seen[(offset, length)]}

    for c in characters:
        if c['model_dir'] >= len(pointers):
            continue
        blocks = {}
        for role, index in BLOCK_FILES.items():
            entry = entry_of(c['model_dir'], index)
            if entry is not None:
                blocks[role] = entry
        c['blocks'] = blocks
        equipment = {}
        for index, role in gear.ROLES.items():
            entry = entry_of(c['model_dir'], index)
            if entry is not None:
                equipment[role] = {'file': index, **entry, 'placeholder': entry['length'] <= gear.PLACEHOLDER_MAX
                                   and gear.is_placeholder(dat.read(entry['offset'], entry['length']))}
        c['equipment'] = equipment
        animations = []
        for index in anim.FILES:
            entry = entry_of(c['model_dir'], index)
            if entry is not None:
                animations.append({'file': index, **entry})
        c['animations'] = animations
    users: dict[tuple[int, int], int] = {}
    for c in characters:
        for entry in list((c.get('equipment') or {}).values()) + (c.get('animations') or []):
            users[(entry['offset'], entry['length'])] = users.get((entry['offset'], entry['length']), 0) + 1
    for c in characters:
        for entry in list((c.get('equipment') or {}).values()) + (c.get('animations') or []):
            entry['shared_with'] = users[(entry['offset'], entry['length'])] - 1


def _resolve_icons(image: dolfile.DolImage, dat, mf: dict | None, characters: list[dict], warnings: list[str]) -> bool:
    """Each character's ``icon`` (``state_icons``); False (icons None) without a DAT or a readable bank."""
    for c in characters:
        c['icon'] = None
    if dat is None:
        return False
    try:
        bank = state_icons.read_bank(image, dat)
    except state_icons.IconStateError as exc:
        warnings.append(f'no portraits: {exc}')
        return False
    results, problems = state_icons.resolve_all(image, bank, [c['id'] for c in characters],
                                                (mf or {}).get('portrait_of'))
    for c in characters:
        c['icon'] = results[c['id']]
    warnings.extend(f'portrait: {p}' for p in problems)
    return True


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
