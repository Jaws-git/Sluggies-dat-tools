"""Icons for spare rows and new IDs: the roster step owns the icon bank.

Config: an optional ``icon`` on a ``wheels`` entry (spare rows 0x47-0x4C) or an
``ids`` entry (new IDs 0x66 and up)::

    {"id": "0x47", "wheel": "0x06", "icon": {"side": "black_yoshi_side.png",
                                             "front": "black_yoshi_front.png"}}

The PNGs live in ``1_Input/_Icons`` and are fitted into 48x51 (``fit``:
contain, cover or strict; default contain; ``icon_art``). ``like`` (optional)
names the character whose source records the new keys copy; it decides the
record flags, e.g. byte +0x26 of a side record (0x82 for most characters,
0x02 for Luigi and a few others, probably a mirror flag). The default is
``SPARE_DONORS`` for a spare row and the template for a new ID (as the
external tool does).

What the step builds, when at least one entry has an ``icon`` (else it
leaves the icon bank and the DOL alone):

* a fresh icon bank from the stock one (dir 0 file 1574): the stock texture
  section, then page 0x86's palette (moved: the new page descriptors cover
  it), the private CMPR pages 0x92 (side) and 0x93 (front) sized to the
  portraits they hold, then the container. Each distinct portrait gets a
  52x52 cell (48x51 art plus transparent texels against filtering bleed,
  whole CMPR blocks); identical portraits share one. A page is the smallest
  power-of-two size that holds its cells (``page_size``; at most 1024x1024 =
  361 cells). The source tables sit at 0x87520 (free rows of a stock atlas)
  while they fit there, else after the pages; they stay inside the texture
  section either way, as the page images must (the external tool: images
  after the container drew garbage). Page image and palette offsets in a
  descriptor count from bank +0x20.
* one side and one front resource row per entry after the 152 stock rows,
  and keys pointing at them: side and front for every entry, normal_a too
  for new IDs (it shows the front row, as in the external tool). Each
  table's last frame (header +0x18) covers the highest key.
* the bank in DAT hammerspace, with the icon record pointing at it;
* the three runtime hooks of the retired icon pipeline back to their stock
  words, should an older output still carry them (keys alone draw the icons;
  Dolphin 2026-10-03);
* for new IDs with art: ``portrait_of[id] = id`` (the ids step aliases the
  portrait to the template otherwise) and the portrait renderer's branch at
  0x80395E1C sends IDs >= 0x66 down the normal track path instead of the Mii
  path.
"""

import os
import struct
from dataclasses import dataclass
from dataclasses import field as dataclass_field

import numpy as np
from PIL import Image

try:
    from ..Dol import dolfile
    from ..Dol.ppc import Asm, one
    from ..Icons import gx_decode
    from . import dat_hammerspace as dhs
    from . import dol_hammerspace, icon_art, ids, steps
except ImportError:
    from Dol import dolfile
    from Dol.ppc import Asm, one
    from Icons import gx_decode
    import dat_hammerspace as dhs
    import dol_hammerspace
    import icon_art
    import ids
    import steps

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
ICON_DIR = os.path.join(ROOT, '1_Input', '_Icons')
ICON_RECORD = dhs.dol_base_address(0x68DE88)   # DOL directory record of the icon bank (dir 0 file 1574)
STOCK_BANK_OFFSET, STOCK_BANK_LENGTH = 0x167E7420, 0x985F0
STOCK_TEXTURE_SECTION = 0x20                   # header +0
STOCK_CONTAINER = 0x93680                      # header +4: end of the texture section = the container
STOCK_TEXTURE_COUNT = 0x92                     # header +0x20
DESCRIPTOR_TABLE, DESCRIPTOR_SIZE = 0x24, 0x20
SIDE_PAGE, FRONT_PAGE = 0x92, 0x93
TEXTURE_COUNT = 0x94
DESCRIPTOR_TEMPLATE_PAGE = 0x91                # the new descriptors copy its sampler fields
TEX_BASE = 0x20                                # descriptor image / palette offsets count from here
CONTAINER_HEAD = 0x14                          # container header before its descriptor
CONTAINER_END_FIELD = 0x10                     # container +0x10: its length
RESOURCE_FIELD = 0x04
NORMAL_A_FIELD, SIDE_FIELD, FRONT_FIELD = 0x08, 0x0C, 0x10
TABLE_FIELDS = (NORMAL_A_FIELD, SIDE_FIELD, FRONT_FIELD)
TABLES_AT = 0x87520                            # free rows of a stock atlas: the source tables while they fit
SOURCE_HEADER, SOURCE_RECORD = 0x28, 0x50
FOLLOWING_RECORD_FLAG = 0x0100
STOCK_ROWS, ROW_SIZE = 0x98, 0x14
RECORD_KIND = 0x0400
BANK_TAIL = 8                                  # zero bytes after the resource table
CELL = 52                                      # 48x51 art + >= 1 transparent texel right and below, whole blocks
PAGE_MAX = 1024                                # GX texture limit
PAGE_MIN = 8                                   # one CMPR tile
ALIGN = 0x20
KEPT_BLOCKS_EXT = '.cmpr'                      # a portrait's kept CMPR cell blocks, beside its PNG (derived states)
# The records the spare rows' keys copy by default (the donors the retired icon pipeline used).
SPARE_DONORS = {0x47: 0x04, 0x48: 0x00, 0x49: 0x01, 0x4A: 0x02, 0x4B: 0x03, 0x4C: 0x05}
RESOLVER_SITE, RESOLVER_STOCK = 0x80395E1C, 0x4080008C   # bge 0x80395EA8 (the Mii block) after cmpwi r24,0x4D
RESOLVER_NORMAL, RESOLVER_MII = 0x80395E20, 0x80395EA8
# The retired icon pipeline's runtime hooks: (site, stock word, its stub in a low-memory cave)
OLD_HOOKS = ((0x8050A5AC, 0x9421FFA0, 0x80004C6C), (0x80519478, 0x4BFF5099, 0x8000576C),
             (0x8051952C, 0xA0BF0000, 0x80005DB0))


class IconConfigError(ValueError):
    pass


class IconBankError(RuntimeError):
    pass


@dataclass(frozen=True)
class IconEntry:
    char_id: int
    side_path: str
    front_path: str
    like: int
    fit: str = icon_art.DEFAULT_FIT_MODE

    @property
    def new_id(self) -> bool:
        return self.char_id >= ids.FIRST_NEW


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def parse_icons(config: dict, icon_dir: str | None = None, check_files: bool = True) -> list[IconEntry]:
    """Icon entries in config order: ``wheels`` entries first, then ``ids`` entries."""
    icon_dir = ICON_DIR if icon_dir is None else icon_dir
    out = []
    new_ids = {c.id: c for c in ids.parse_ids(config)} if config.get('ids') else {}
    listed = []
    for key in ('wheels', 'ids'):
        for n, entry in enumerate(config.get(key) or []):
            if entry.get('icon') is not None:
                listed.append((f'{key}[{n}]', key, entry))
    for where, key, entry in listed:
        icon = entry['icon']
        if not isinstance(icon, dict):
            raise IconConfigError(f'{where}.icon must be an object with "side" and "front"')
        if key == 'wheels':
            cid = ids._number(entry.get('id'), f'{where}.id')
            if cid not in SPARE_DONORS:
                raise IconConfigError(f'{where}: 0x{cid:02X} is not a spare row')
            default_like = SPARE_DONORS[cid]
        else:
            if entry.get('id') is None:
                raise IconConfigError(f'{where}: an entry with an icon needs an explicit "id"')
            cid = ids._number(entry['id'], f'{where}.id')
            default_like = new_ids[cid].template
        like = ids._number(icon['like'], f'{where}.icon.like') if icon.get('like') is not None else default_like
        if not 0 <= like < ids.PLAYER_END:
            raise IconConfigError(f'{where}.icon.like: 0x{like:02X} is not a stock player ID')
        fit = icon.get('fit', icon_art.DEFAULT_FIT_MODE)
        if fit not in icon_art.FIT_MODES:
            raise IconConfigError(f'{where}.icon.fit: {fit!r} is not one of {", ".join(icon_art.FIT_MODES)}')
        paths = []
        for view in ('side', 'front'):
            name = icon.get(view)
            if not isinstance(name, str) or not name or os.path.basename(name) != name:
                raise IconConfigError(f'{where}.icon.{view} must be a plain PNG file name (in 1_Input/_Icons)')
            path = os.path.join(icon_dir, name)
            if check_files and not os.path.isfile(path):
                raise IconConfigError(f'{where}.icon.{view}: {path} not found')
            paths.append(path)
        out.append(IconEntry(cid, paths[0], paths[1], like, fit))
    seen = [e.char_id for e in out]
    if len(set(seen)) != len(seen):
        raise IconConfigError('an ID has two icon entries')
    return out


def _table(bank: bytes, offset: int) -> list[bytearray]:
    length = struct.unpack_from('>I', bank, offset + 0x08)[0]
    count, stride = struct.unpack_from('>HH', bank, offset + 0x24)
    if stride != SOURCE_RECORD or length != SOURCE_HEADER + count * stride or offset + length > len(bank):
        raise IconBankError(f'no source table at 0x{offset:X}')
    start = offset + SOURCE_HEADER
    return [bytearray(bank[start + i * stride:start + (i + 1) * stride]) for i in range(count)]


def _key(record: bytes) -> int:
    return struct.unpack_from('>H', record, 2)[0]


def extend_table(bank: bytes, offset: int, keys: dict[int, tuple[int, int]]) -> bytes:
    """The source table at ``offset`` with ``keys`` = {id: (like, resource row)} added; returns the table bytes."""
    header = bytearray(bank[offset:offset + SOURCE_HEADER])
    records = _table(bank, offset)
    by_id = {_key(r): r for r in records}
    for cid, (like, row) in keys.items():
        if cid in by_id:
            raise IconBankError(f'0x{cid:02X} already has a key in the source table at 0x{offset:X}')
        donor = by_id.get(like) or by_id[max(k for k in by_id if k <= like)]   # the key in force at ``like``
        record = bytearray(donor)
        struct.pack_into('>HHH', record, 0x02, cid, RECORD_KIND, row)
        records.append(record)
    records.sort(key=_key, reverse=True)
    for i, record in enumerate(records):
        flags = struct.unpack_from('>H', record, 0)[0]
        flags = flags & ~FOLLOWING_RECORD_FLAG if i == 0 else flags | FOLLOWING_RECORD_FLAG
        struct.pack_into('>H', record, 0, flags)
    struct.pack_into('>I', header, 0x08, SOURCE_HEADER + len(records) * SOURCE_RECORD)
    struct.pack_into('>H', header, 0x24, len(records))
    if keys:
        last = struct.unpack_from('>H', header, 0x18)[0]
        struct.pack_into('>H', header, 0x18, max(last, max(keys)))
    return bytes(header) + b''.join(bytes(r) for r in records)


def _check_stock(stock_bank: bytes) -> None:
    if len(stock_bank) != STOCK_BANK_LENGTH:
        raise IconBankError(f'stock icon bank is 0x{len(stock_bank):X} bytes, expected 0x{STOCK_BANK_LENGTH:X}')
    header = struct.unpack_from('>II', stock_bank, 0) + (struct.unpack_from('>H', stock_bank, 0x20)[0],)
    if header != (STOCK_TEXTURE_SECTION, STOCK_CONTAINER, STOCK_TEXTURE_COUNT):
        raise IconBankError('the stock icon bank range does not hold the stock icon bank')


def _table_keys(entries: list[IconEntry], side_row: dict, front_row: dict) -> dict:
    return {
        NORMAL_A_FIELD: {e.char_id: (e.like, front_row[e.char_id]) for e in entries if e.new_id},
        SIDE_FIELD: {e.char_id: (e.like, side_row[e.char_id]) for e in entries},
        FRONT_FIELD: {e.char_id: (e.like, front_row[e.char_id]) for e in entries},
    }


# --------------------------------------------------------------------------
# Packed bank
# --------------------------------------------------------------------------

@dataclass
class Page:
    """One private page: its size, each entry's portrait cell (top-left texel; identical portraits share a cell),
    for the encoder the RGBA image, and the CMPR blocks to keep per cell (``keep_blocks``)."""
    width: int
    height: int
    cells: list[tuple[int, int]]
    image: object = None
    blocks: dict = dataclass_field(default_factory=dict)      # {(x, y): cell_blocks bytes}

    @property
    def payload_length(self) -> int:
        return self.width * self.height // 2


def _pow2(n: int) -> int:
    return max(PAGE_MIN, 1 << (n - 1).bit_length())


def page_size(cells: int) -> tuple[int, int, int]:
    """(width, height, cells a row) of the smallest power-of-two page holding ``cells`` 52x52 cells
    (ties: the wider page)."""
    best = None
    width = PAGE_MAX
    while width >= CELL:
        per_row = width // CELL
        height = _pow2(-(-max(cells, 1) // per_row) * CELL)
        if height <= PAGE_MAX and (best is None or width * height < best[0] * best[1]):
            best = (width, height, per_row)
        width //= 2
    if best is None:
        raise IconBankError(f'{cells} portraits do not fit a {PAGE_MAX}x{PAGE_MAX} page')
    return best


def pack_page(portraits: list, keys: list | None = None, blocks: list | None = None) -> Page:
    """A page for ``portraits`` (48x51 RGBA images, one per entry), identical ones in one cell.

    ``keys`` (optional, one per portrait): what makes two portraits "identical" instead of their pixels (a kept
    cell's file, so two cells stay two cells). ``blocks`` (optional, one per portrait, None or ``cell_blocks``
    bytes): CMPR blocks to keep for that portrait's cell (``keep_blocks``)."""
    keys = [image.tobytes() for image in portraits] if keys is None else keys
    unique = {}
    for key, image in zip(keys, portraits):
        unique.setdefault(key, image)
    width, height, per_row = page_size(len(unique))
    at = {key: ((k % per_row) * CELL, (k // per_row) * CELL) for k, key in enumerate(unique)}
    page = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    for key, image in unique.items():
        page.paste(image, at[key])
    kept = {}
    for key, cell_blocks in zip(keys, blocks or []):
        if cell_blocks is not None:
            kept.setdefault(at[key], cell_blocks)
    return Page(width, height, [at[key] for key in keys], page, kept)


def kept_blocks_path(png_path: str) -> str:
    """Where a portrait PNG's kept CMPR blocks live: ``<name>.cmpr`` beside it (a derived roster state's icons)."""
    return os.path.splitext(png_path)[0] + KEPT_BLOCKS_EXT


def _kept_blocks(png_path: str) -> bytes | None:
    path = kept_blocks_path(png_path)
    if not os.path.isfile(path):
        return None
    with open(path, 'rb') as f:
        return f.read()


def compose_pages(entries: list[IconEntry]) -> tuple[Page, Page]:
    """Side and front pages: each entry's PNGs fitted to 48x51 with its own fit mode (``icon_art``). A PNG with
    kept CMPR blocks beside it (``kept_blocks_path``) is its own cell, and the encoder keeps its blocks."""
    pages = []
    for view in ('side', 'front'):
        paths = [getattr(e, f'{view}_path') for e in entries]
        blocks = [_kept_blocks(p) for p in paths]
        keys = [('kept', os.path.normcase(os.path.abspath(p))) if b is not None else None
                for p, b in zip(paths, blocks)]
        portraits = [icon_art.load_portrait(p, e.fit) for p, e in zip(paths, entries)]
        keys = [k if k is not None else image.tobytes() for k, image in zip(keys, portraits)]
        pages.append(pack_page(portraits, keys, blocks))
    return tuple(pages)


def keep_blocks(page: Page, payload: bytes) -> tuple[bytes, int]:
    """``payload`` with each kept cell's own CMPR blocks put back, so an unchanged portrait keeps its bytes through
    a rebuild (wimgt's decode -> encode is not stable). A cell's blocks are kept only when they decode to exactly
    the cell's pixels on the page (else the portrait changed and the new encoding stands). Returns the payload and
    the number of cells kept."""
    if not page.blocks:
        return payload, 0
    out = bytearray(payload)
    pixels = np.asarray(page.image.convert('RGBA'))
    kept = 0
    for (x, y), blocks in page.blocks.items():
        try:
            decoded = gx_decode.decode(gx_decode.CMPR, icon_art.cell_payload(blocks, CELL), CELL, CELL)
            if not np.array_equal(decoded, pixels[y:y + CELL, x:x + CELL]):
                continue
            icon_art.put_cell_blocks(out, page.width, x, y, CELL, blocks)
        except (icon_art.IconArtError, gx_decode.GxDecodeError):
            continue
        kept += 1
    return bytes(out), kept


def encode_page(page: Page) -> bytes:
    """The page's CMPR payload (wimgt)."""
    return icon_art.encode_cmpr(page.image)


def _descriptor_offset(page_id: int) -> int:
    return DESCRIPTOR_TABLE + page_id * DESCRIPTOR_SIZE


def _cmpr_descriptor(template: bytes, image: int, width: int, height: int) -> bytes:
    descriptor = bytearray(template)
    struct.pack_into('>IIHH', descriptor, 0, image - TEX_BASE, 0, height, width)
    descriptor[0x17] = icon_art.CMPR_FORMAT
    struct.pack_into('>H', descriptor, 0x18, 0)
    descriptor[0x1A] = 0
    return bytes(descriptor)


def _pointer(bank: bytes, descriptor: int, field: int) -> int:
    return descriptor + struct.unpack_from('>i', bank, descriptor + field)[0]


def _covered_palettes(stock_bank: bytes, lo: int, hi: int) -> list[tuple[int, int, int]]:
    """(page, palette offset, length) of the stock palettes that bytes [lo, hi) of the bank overlap; refuses an
    overlapped image."""
    out = []
    for page_id in range(STOCK_TEXTURE_COUNT):
        d = _descriptor_offset(page_id)
        image, palette = struct.unpack_from('>II', stock_bank, d)
        if image and TEX_BASE + image < hi and lo < TEX_BASE + image + 0x20:
            raise IconBankError(f'the new page descriptors would cover page 0x{page_id:02X}\'s image')
        length = struct.unpack_from('>H', stock_bank, d + 0x18)[0] * 2
        if palette and length and TEX_BASE + palette < hi and lo < TEX_BASE + palette + length:
            out.append((page_id, TEX_BASE + palette, length))
    return out


def build_packed_bank(stock_bank: bytes, entries: list[IconEntry], side: Page, front: Page,
                      side_payload: bytes, front_payload: bytes) -> bytes:
    """The packed icon bank with keys, rows and art for ``entries`` (see the module docstring)."""
    _check_stock(stock_bank)
    n = len(entries)
    if len(side.cells) != n or len(front.cells) != n:
        raise IconBankError('the pages do not have one cell per entry')
    stock_end = STOCK_CONTAINER                          # stock texture section end = container start
    stock_desc = stock_end + CONTAINER_HEAD
    res = _pointer(stock_bank, stock_desc, RESOURCE_FIELD)
    count, length = struct.unpack_from('>II', stock_bank, res)
    if count != STOCK_ROWS:
        raise IconBankError(f'resource table has 0x{count:X} rows, expected 0x{STOCK_ROWS:X}')
    stock_tables = [_pointer(stock_bank, stock_desc, f) for f in TABLE_FIELDS]

    tex = bytearray(stock_bank[:stock_end])

    def append(blob: bytes) -> int:
        at = len(tex)
        tex.extend(blob + bytes(-len(blob) % ALIGN))
        return at

    # descriptors 0x92 / 0x93 (they run over the stock data after the descriptor table: move what they cover)
    first, last = _descriptor_offset(SIDE_PAGE), _descriptor_offset(FRONT_PAGE) + DESCRIPTOR_SIZE
    for page_id, palette, size in _covered_palettes(stock_bank, first, last):
        struct.pack_into('>I', tex, _descriptor_offset(page_id) + 4,
                         append(stock_bank[palette:palette + size]) - TEX_BASE)
    template = stock_bank[_descriptor_offset(DESCRIPTOR_TEMPLATE_PAGE):][:DESCRIPTOR_SIZE]
    for page_id, page, payload in ((SIDE_PAGE, side, side_payload), (FRONT_PAGE, front, front_payload)):
        if len(payload) != page.payload_length:
            raise IconBankError(f'page 0x{page_id:02X} payload is 0x{len(payload):X} bytes, '
                                f'expected 0x{page.payload_length:X} ({page.width}x{page.height} CMPR)')
        d = _descriptor_offset(page_id)
        tex[d:d + DESCRIPTOR_SIZE] = _cmpr_descriptor(template, append(payload), page.width, page.height)
    struct.pack_into('>H', tex, 0x20, TEXTURE_COUNT)

    # resource rows and keys
    rows = [struct.pack('>HHffff', page_id, 0, y / page.height, x / page.width,
                        (y + icon_art.ICON_HEIGHT) / page.height, (x + icon_art.ICON_WIDTH) / page.width)
            for page_id, page in ((SIDE_PAGE, side), (FRONT_PAGE, front)) for x, y in page.cells]
    side_row = {e.char_id: count + i for i, e in enumerate(entries)}
    front_row = {e.char_id: count + n + i for i, e in enumerate(entries)}
    keys = _table_keys(entries, side_row, front_row)
    tables = [extend_table(stock_bank, start, keys[f]) for f, start in zip(TABLE_FIELDS, stock_tables)]
    run = b''.join(tables)
    if TABLES_AT + len(run) <= stock_end and not any(stock_bank[TABLES_AT:stock_end]):
        at = TABLES_AT
        tex[at:at + len(run)] = run
    else:
        at = append(run)
    table_starts = [at + sum(len(t) for t in tables[:i]) for i in range(len(tables))]

    # container: the stock one after the texture section, old tables zeroed, rows appended
    tex_end = len(tex)
    struct.pack_into('>I', tex, 0x04, tex_end)
    container = bytearray(stock_bank[stock_end:res + length])
    if struct.unpack_from('>I', container, CONTAINER_END_FIELD)[0] != len(container):
        raise IconBankError('the stock container does not end with its resource table')
    lo = min(stock_tables) - stock_end
    container[lo:res - stock_end] = bytes(res - stock_end - lo)
    container += b''.join(rows)
    struct.pack_into('>II', container, res - stock_end, count + 2 * n, length + len(rows) * ROW_SIZE)
    struct.pack_into('>I', container, CONTAINER_END_FIELD, len(container))
    desc = tex_end + CONTAINER_HEAD
    for field, start in zip(TABLE_FIELDS, table_starts):
        struct.pack_into('>i', container, CONTAINER_HEAD + field, start - desc)
    return bytes(tex + container + bytes(BANK_TAIL))


# --------------------------------------------------------------------------
# DOL
# --------------------------------------------------------------------------

def retire_hooks(image: dolfile.DolImage) -> list[str]:
    """Put the retired icon pipeline's three hook sites back to their stock words (older outputs)."""
    log = []
    for site, stock, stub in OLD_HOOKS:
        word = image.u32(site)
        if word == stock:
            continue
        if word != one(site, lambda a: a.b(stub)):
            raise dolfile.DolError(f'icon hook site 0x{site:08X} is {word:08X}: neither stock nor the pipeline hook')
        image.write_word(site, stock)
        log.append(f'icon hook 0x{site:08X} back to stock (stub 0x{stub:08X} left as dead code)')
    return log


def resolver_branch(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace) -> str:
    """IDs >= 0x66 take the normal track path (they reach the renderer only with own art: the others are
    aliased to their template at its entry)."""
    if image.u32(RESOLVER_SITE) != RESOLVER_STOCK:
        raise dolfile.DolError(f'0x{RESOLVER_SITE:08X} is {image.u32(RESOLVER_SITE):08X}, expected the stock bge')
    hs.code.put(b'', 4)
    a = Asm(hs.code.here)
    a.blt('normal')
    a.cmpwi('r24', ids.FIRST_NEW).bge('normal')
    a.b(RESOLVER_MII)
    a.label('normal')
    a.b(RESOLVER_NORMAL)
    at = hs.code.put(a.assemble(), 4)
    image.patch_word(RESOLVER_SITE, RESOLVER_STOCK, one(RESOLVER_SITE, lambda b: b.b(at)))
    return f'portrait resolver 0x{RESOLVER_SITE:08X}: IDs 0x{ids.FIRST_NEW:02X}+ take the normal track path'


# --------------------------------------------------------------------------
# Step
# --------------------------------------------------------------------------

def read_record(image) -> list[int]:
    words = dhs.read_record(image, ICON_RECORD)
    slots = {dhs.slot(words, lang)[:2] for lang in dhs.LANGS}
    if len(slots) != 1:
        raise IconBankError('the icon record\'s language slots disagree')
    return words


@steps.register('icons')
def apply(ctx: steps.RosterContext, encode_cmpr=None) -> list[str]:
    """``encode_cmpr``: a Page -> its CMPR payload (default ``encode_page``, wimgt); tests pass a stand-in."""
    encode_cmpr = encode_cmpr or encode_page
    entries = parse_icons(ctx.config, ctx.icon_dir)
    if not entries:
        bare = [ids._number(w.get('id'), 'wheels.id') for w in ctx.config.get('wheels') or [] if w.get('id')]
        note = ['no "icon" in the roster config: the icon bank stays as it is']
        if bare:
            note.append('note: ' + ', '.join(f'0x{c:02X}' for c in bare) + ' have no icon here; they show the '
                        'held stock key (Pink Yoshi)')
        return note
    if ctx.dat is None:
        raise IconBankError('dt_na.dat is missing in the output folder')
    stock = ctx.dat.read(STOCK_BANK_OFFSET, STOCK_BANK_LENGTH)
    side, front = compose_pages(entries)
    (side_payload, side_kept), (front_payload, front_kept) = (keep_blocks(p, encode_cmpr(p)) for p in (side, front))
    bank = build_packed_bank(stock, entries, side, front, side_payload, front_payload)
    page_note = ('CMPR pages: ' + ', '.join(f'{view} {p.width}x{p.height} ({len(set(p.cells))} portraits'
                                           + (f', {kept} kept as encoded' if kept else '') + ')'
                                           for view, p, kept in (('side', side, side_kept),
                                                                 ('front', front, front_kept))))
    words = read_record(ctx.dol)
    before = dhs.slot(words, 'en')[:2]
    at = dhs.allocate(ctx.dat, len(bank), dhs.routed_ranges(ctx.dol))
    ctx.dat.write(at, bank)
    for lang in dhs.LANGS:
        dhs.set_slot(words, lang, at, len(bank))
    dhs.write_record(ctx.dol, ICON_RECORD, words)
    log = [f'icon bank 0x{before[0]:08X}+0x{before[1]:X} -> 0x{at:08X}+0x{len(bank):X}: '
           f'{len(entries)} icons (' + ids.id_ranges(e.char_id for e in entries) + ')', page_note]
    log += retire_hooks(ctx.dol)
    own = [e for e in entries if e.new_id]
    if own:
        portrait_of = ctx.state.get('portrait_of')
        if portrait_of is None:
            raise IconBankError('new IDs with icons need the ids step (its portrait alias table)')
        hs = dol_hammerspace.get(ctx)
        for e in own:
            hs.write(portrait_of + e.char_id, bytes([e.char_id]))
        log.append(resolver_branch(ctx.dol, hs))
        hs.commit()
        log.append('own portraits: ' + ids.id_ranges(e.char_id for e in own))
    growth = len(bank) - STOCK_BANK_LENGTH
    log.append(f'icon bank game heap {growth:+,} bytes against stock (resident in MEM2 during a match)')
    return log
