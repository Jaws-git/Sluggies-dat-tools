"""Character portraits as the game draws them (GUI character grid): resolve each ID, cut the crops.

``resolve(image, bank, cid, view, portrait_of)`` follows the portrait renderer
(``0x80395DB0``; ``_docs/_docs_roster/RosterExpansion.md``, "Icons"):

1. a new ID (``0x66`` and up) is first aliased through ``portrait_of``, the
   roster's table in DOL hammerspace (to its template, or to itself when it
   has own art);
2. a player ID (and, with the roster's resolver branch at ``0x80395E1C``, a
   new ID) shows the record with the highest key <= ID of the view's source
   table (front or side; the container descriptor's pointers, as the icons
   step reads them);
3. Mii IDs (``0x4D``-``0x64``, or any ID >= ``0x4D`` without the branch) show
   resource row ``0x97``; other IDs show key ``0x4D``, the "?" icon;
4. the record's resource row gives the page and the rect.

Each result records where the portrait comes from (``source``): ``own``,
``neighbour`` (a lower key), ``template`` (``portrait_of`` aliased it),
``mii`` or ``invalid``. The GUI marks everything but ``own``.

``write_crops`` decodes the pages the results use (``Icons/gx_decode``) and
writes each crop as a PNG, named by its page's SHA-1 and rect, so a re-read
of unchanged files decodes nothing.
"""

import hashlib
import os
import struct

from PIL import Image

try:
    from ..Icons import gx_decode
    from . import dat_hammerspace as dhs
    from . import icons, ids
except ImportError:
    from Icons import gx_decode
    import dat_hammerspace as dhs
    import icons
    import ids

VIEWS = ('front', 'side')
TABLE_FIELDS = {'side': icons.SIDE_FIELD, 'front': icons.FRONT_FIELD}
PLAYER_END = 0x4D                                  # IDs below take the track path
MII_END = 0x64                                     # 0x4D-0x64: the Mii path
MII_ROW = 0x97
INVALID_KEY = 0x4D                                 # the "?" icon
SOURCES = ('own', 'neighbour', 'template', 'mii', 'invalid')


class IconStateError(ValueError):
    pass


class IconBank:
    """The parsed icon bank: source tables, resource rows, page descriptors."""

    def __init__(self, data: bytes):
        self.data = data
        try:
            container = struct.unpack_from('>I', data, 4)[0]
            desc = container + icons.CONTAINER_HEAD
            res = icons._pointer(data, desc, icons.RESOURCE_FIELD)
            count = struct.unpack_from('>I', data, res)[0]
            self.rows = [(struct.unpack_from('>H', data, row)[0], struct.unpack_from('>4f', data, row + 4))
                         for row in range(res + 8, res + 8 + count * icons.ROW_SIZE, icons.ROW_SIZE)]
            self.tables = {view: sorted((icons._key(r), struct.unpack_from('>H', r, 6)[0])
                                        for r in icons._table(data, icons._pointer(data, desc, field)))
                           for view, field in TABLE_FIELDS.items()}
        except (icons.IconBankError, struct.error) as exc:
            raise IconStateError(f'the icon bank does not parse: {exc}') from exc
        self.page_count = struct.unpack_from('>H', data, 0x20)[0]
        self._sha1 = {}

    def page(self, page: int) -> dict:
        """Descriptor facts of one page: format, size, image and palette bytes."""
        if not 0 <= page < self.page_count:
            raise IconStateError(f'page 0x{page:02X} does not exist ({self.page_count} pages)')
        d = icons.DESCRIPTOR_TABLE + page * icons.DESCRIPTOR_SIZE
        image_at, palette_at, height, width = struct.unpack_from('>IIHH', self.data, d)
        fmt = self.data[d + 0x17]
        entries = struct.unpack_from('>H', self.data, d + 0x18)[0]
        palette_format = self.data[d + 0x1A]
        try:
            length = gx_decode.data_length(fmt, width, height)
        except gx_decode.GxDecodeError as exc:
            raise IconStateError(f'page 0x{page:02X}: {exc}') from exc
        image = self.data[icons.TEX_BASE + image_at:icons.TEX_BASE + image_at + length]
        palette = b''
        if fmt == gx_decode.C8:
            palette = self.data[icons.TEX_BASE + palette_at:icons.TEX_BASE + palette_at + 2 * entries]
        if len(image) != length or (fmt == gx_decode.C8 and len(palette) != 2 * entries):
            raise IconStateError(f'page 0x{page:02X} runs past the end of the bank')
        return {'format': fmt, 'width': width, 'height': height, 'image': image, 'palette': palette,
                'palette_format': palette_format}

    def page_sha1(self, page: int) -> str:
        if page not in self._sha1:
            p = self.page(page)
            h = hashlib.sha1(struct.pack('>BHHB', p['format'], p['width'], p['height'], p['palette_format']))
            h.update(p['image'])
            h.update(p['palette'])
            self._sha1[page] = h.hexdigest()
        return self._sha1[page]

    def decode_page(self, page: int):
        p = self.page(page)
        try:
            return gx_decode.decode(p['format'], p['image'], p['width'], p['height'], p['palette'],
                                    p['palette_format'])
        except gx_decode.GxDecodeError as exc:
            raise IconStateError(f'page 0x{page:02X}: {exc}') from exc

    def key_in_force(self, view: str, cid: int) -> tuple[int, int] | None:
        """(key, resource row) of the record with the highest key <= ``cid``, or None."""
        best = None
        for key, row in self.tables[view]:
            if key > cid:
                break
            best = (key, row)
        return best

    def row_rect(self, row: int) -> tuple[int, list[int]]:
        """(page, [x, y, w, h]) of one resource row, the rect clipped to the page."""
        if not 0 <= row < len(self.rows):
            raise IconStateError(f'resource row {row} does not exist ({len(self.rows)} rows)')
        page, (v1, u1, v2, u2) = self.rows[row]
        p = self.page(page)
        width, height = p['width'], p['height']
        x0, y0 = max(0, round(u1 * width)), max(0, round(v1 * height))
        x1, y1 = min(width, round(u2 * width)), min(height, round(v2 * height))
        if x1 <= x0 or y1 <= y0:
            raise IconStateError(f'resource row {row} has an empty rect on page 0x{page:02X}')
        return page, [x0, y0, x1 - x0, y1 - y0]


def read_bank(image, dat) -> IconBank:
    """The icon bank the output DOL's icon record points at (all three language slots share one)."""
    try:
        words = icons.read_record(image)
    except (icons.IconBankError, dhs.DatHammerspaceError) as exc:
        raise IconStateError(f'icon record: {exc}') from exc
    offset, length, _alloc = dhs.slot(words, 'en')
    data = dat.read(offset, length)
    if len(data) != length:
        raise IconStateError(f'the icon bank 0x{offset:08X}+0x{length:X} runs past the end of dt_na.dat')
    return IconBank(data)


def resolver_branch(image) -> bool:
    """Whether new IDs with own art take the normal track path (the icons step's branch at 0x80395E1C)."""
    return image.u32(icons.RESOLVER_SITE) != icons.RESOLVER_STOCK


def portrait_alias(image, portrait_of: int | None, cid: int) -> int:
    if portrait_of is None or cid < ids.FIRST_NEW:
        return cid
    return image.read(portrait_of + cid, 1)[0]


def resolve(bank: IconBank, cid: int, view: str, alias: int, branch: bool) -> dict:
    """Where the game takes ``cid``'s ``view`` portrait from (module docstring); ``alias``: ``portrait_alias``."""
    shown = alias
    if shown < PLAYER_END or (branch and shown >= ids.FIRST_NEW):
        hit = bank.key_in_force(view, shown)
        if hit is None:
            raise IconStateError(f'0x{cid:02X} {view}: no key at or below 0x{shown:02X}')
        key, row = hit
        source = 'template' if alias != cid else ('own' if key == cid else 'neighbour')
    elif shown <= MII_END:
        key, row, source = None, MII_ROW, 'mii'
    else:
        hit = bank.key_in_force(view, INVALID_KEY)
        if hit is None:
            raise IconStateError(f'0x{cid:02X} {view}: no "?" key')
        (key, row), source = hit, 'invalid'
    page, rect = bank.row_rect(row)
    return {'source': source, 'alias': alias, 'key': key, 'row': row, 'page': page, 'rect': rect,
            'page_sha1': bank.page_sha1(page)}


def resolve_all(image, bank: IconBank, cids, portrait_of: int | None) -> tuple[dict, list[str]]:
    """``({id: {view: result}}, problems)``; an ID that does not resolve gets None and a problem line."""
    branch = resolver_branch(image)
    out, problems = {}, []
    for cid in cids:
        alias = portrait_alias(image, portrait_of, cid)
        views = {}
        for view in VIEWS:
            try:
                views[view] = resolve(bank, cid, view, alias, branch)
            except IconStateError as exc:
                views[view] = None
                problems.append(str(exc))
        out[cid] = views
    return out, problems


# --------------------------------------------------------------------------
# Crops
# --------------------------------------------------------------------------

def crop_name(result: dict) -> str:
    x, y, w, h = result['rect']
    return f'{result["page_sha1"][:16]}_{x}_{y}_{w}x{h}.png'


def write_crops(bank: IconBank, results, icon_dir: str) -> tuple[int, int]:
    """Write every result's crop into ``icon_dir`` (unless there already), set ``result['file']`` to its name,
    and delete crops no result uses. Returns (pages decoded, crops written)."""
    os.makedirs(icon_dir, exist_ok=True)
    pages, written, used = {}, 0, set()
    for result in results:
        name = crop_name(result)
        result['file'] = name
        used.add(name)
        path = os.path.join(icon_dir, name)
        if os.path.isfile(path):
            continue
        if result['page'] not in pages:
            pages[result['page']] = bank.decode_page(result['page'])
        x, y, w, h = result['rect']
        tmp = path + '.tmp'
        Image.fromarray(pages[result['page']][y:y + h, x:x + w], 'RGBA').save(tmp, 'PNG')
        os.replace(tmp, path)
        written += 1
    for name in os.listdir(icon_dir):
        if name.endswith('.png') and name not in used:
            os.remove(os.path.join(icon_dir, name))
    return len(pages), written
