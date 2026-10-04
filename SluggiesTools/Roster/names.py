"""User-set names (plan Phase 8): port of the external tool's ``char_names`` steps 1-3.

Config: an optional ``name`` on an ``ids`` entry (new ID) or a ``wheels``
entry (spare row 0x47-0x4C), a string or one per language::

    {"id": "0x66", "template": "0x06", "name": "Purple Yoshi"}
    {"id": "0x47", "wheel": "0x06", "name": {"en": "Black Yoshi", "fr": "Yoshi noir", "sp": "Yoshi negro"}}

A language without its own name uses the English one. SluggiesTools ships
no names: without any ``name`` in the config the step changes nothing (new
IDs then show no name, and the select screen shows their template's name
plate, as since Phase 3).

With at least one name:

* text (dt_na dir 121 file 5, a message table per language: message i is
  character i's name, 102/103 are formatting codes): every language's
  table runs to ID 0xFE, a named ID gets its name, every other new ID "-";
  the two codes move behind it (0xFF / 0x100) and their four readers are
  repointed; the tables go to DAT hammerspace;
* the three name widgets' ``cmpwi rX,0x4D`` (names only below the Miis) let
  IDs 0x66 and up through as well;
* the select screen's name plates (resource row ``id + 0x149`` of the select
  layout, 115x16 white text): a new texture page per language with one cell
  per distinct name and one "-" cell, drawn with Open Sans ExtraBold
  (``fonts/``, SIL OFL); rows for every new ID after the stock rows (an
  unnamed one shows the "-" cell); a named spare row's own row repointed to
  its cell; the four plate sites take ``row = stock rows + id - 0x66`` for
  new IDs (replacing the ids step's template alias there).
"""

import os
import struct

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from ..Dol import dolfile, inventory
    from ..Dol.ppc import Asm, one
    from ..Icons import layout2d
    from . import dat_hammerspace as dhs
    from . import dol_hammerspace, ids, layout_file, steps, wheels
except ImportError:
    from Dol import dolfile, inventory
    from Dol.ppc import Asm, one
    from Icons import layout2d
    import dat_hammerspace as dhs
    import dol_hammerspace
    import ids
    import layout_file
    import steps
    import wheels

NAME_DIR, NAME_FILE = 121, 5
STOCK_MESSAGES = 104
CODE_ENTRIES = (102, 103)
LAST_ID = ids.MAX_ID                         # every table runs to the highest possible new ID
UNNAMED = '-'
# the code entries' readers: lwz r0,0x198(r3) (entry 102) and lwz r5,0x19c(r5) (entry 103)
CODE_READS = {102: (0x80486E68, 0x80486E88), 103: (0x8047C4C0, 0x8047C4D8)}
RANGE_TESTS = (0x80486EAC, 0x80489F94, 0x804923C0)      # cmpwi crN,rX,0x4d (then bge: no name)
INVENTORY_GROUP = 'char_names'

PLATE_ROW = 0x149                            # resource row of character i's plate: i + 0x149
PLATE_CELL = (115, 16)
PLATE_COLUMN = 128                           # page column width (a cell is 115 wide)
PLATE_TEMPLATE_PAGE = 124                    # the stock plates' page (sampler fields)
GX_RGB5A3 = 5
FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'OpenSans.ttf')
FONT_WEIGHT, FONT_SIZE, FONT_DY, FONT_MIN = 800, 14, -2, 9


class NameConfigError(ValueError):
    pass


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def _name(value, where: str) -> dict[str, str]:
    if isinstance(value, str):
        value = {'en': value}
    if not isinstance(value, dict) or 'en' not in value:
        raise NameConfigError(f'{where}.name must be a string or an object with at least "en"')
    out = {}
    for lang, text in value.items():
        if lang not in dhs.LANGS:
            raise NameConfigError(f'{where}.name: {lang!r} is not one of {", ".join(dhs.LANGS)}')
        if not isinstance(text, str) or not text or '\0' in text:
            raise NameConfigError(f'{where}.name.{lang} must be a non-empty string')
        out[lang] = text
    return {lang: out.get(lang, out['en']) for lang in dhs.LANGS}


def parse_names(config: dict) -> dict[int, dict[str, str]]:
    """``{id: {lang: name}}`` from the ``name`` keys of ``ids`` and ``wheels`` entries."""
    out = {}
    for key in ('ids', 'wheels'):
        for n, entry in enumerate(config.get(key) or []):
            if entry.get('name') is None:
                continue
            where = f'{key}[{n}]'
            if entry.get('id') is None:
                raise NameConfigError(f'{where}: an entry with a name needs an explicit "id"')
            cid = ids._number(entry['id'], f'{where}.id')
            if key == 'wheels' and cid not in wheels.SPARE_IDS:
                raise NameConfigError(f'{where}: 0x{cid:02X} is not a spare row')
            out[cid] = _name(entry['name'], where)
    return out


# --------------------------------------------------------------------------
# Text tables (dir 121 file 5)
# --------------------------------------------------------------------------

def messages(table: bytes) -> list[bytes]:
    """Raw UTF-16 bytes of each message, without its terminator."""
    if table[:2] != b'\1\1':
        raise NameConfigError('not a message table')
    count = struct.unpack_from('>H', table, 2)[0]
    offsets = struct.unpack_from(f'>{count}I', table, 4)
    text = 4 + 4 * count
    out = []
    for o in offsets:
        p = start = text + 2 * o
        while table[p:p + 2] != b'\0\0':
            p += 2
        out.append(bytes(table[start:p]))
    return out


def build_table(msgs: list[bytes]) -> bytes:
    offsets, text = [], bytearray()
    for m in msgs:
        offsets.append(len(text) // 2)
        text += m + b'\0\0'
    return b'\1\1' + struct.pack(f'>H{len(msgs)}I', len(msgs), *offsets) + bytes(text)


def names_table(table: bytes, names: dict[int, str]) -> bytes:
    """Entries 0..LAST_ID (new IDs "-" unless named), then the two codes."""
    msgs = messages(table)
    if len(msgs) != STOCK_MESSAGES:
        raise NameConfigError(f'the name table has {len(msgs)} messages, expected {STOCK_MESSAGES}')
    codes = [msgs[i] for i in CODE_ENTRIES]
    out = msgs[:CODE_ENTRIES[0]] + [UNNAMED.encode('utf-16-be')] * (LAST_ID + 1 - CODE_ENTRIES[0])
    for cid, name in names.items():
        out[cid] = name.encode('utf-16-be')
    return build_table(out + codes)


def name_record(image) -> int:
    return dhs.dir_pointers(image)[NAME_DIR] + NAME_FILE * dhs.RECORD_SIZE


def write_tables(ctx: steps.RosterContext, names: dict[int, dict[str, str]]) -> list[str]:
    record = name_record(ctx.dol)
    words = dhs.read_record(ctx.dol, record)
    log = []
    for lang in dhs.LANGS:
        offset, length, _alloc = dhs.slot(words, lang)
        table = names_table(ctx.dat.read(offset, length), {c: n[lang] for c, n in names.items()})
        at = dhs.allocate(ctx.dat, len(table), dhs.reserved(ctx))
        ctx.dat.write(at, table)
        dhs.set_slot(words, lang, at, len(table))
        dhs.write_record(ctx.dol, record, words)
        log.append(f'{lang} names: 0x{offset:08X}+0x{length:X} -> 0x{at:08X}+0x{len(table):X}')
    return log


# --------------------------------------------------------------------------
# DOL
# --------------------------------------------------------------------------

def _stock(address: int) -> int:
    return inventory.site(INVENTORY_GROUP, address).stock


def code_reads(image: dolfile.DolImage) -> None:
    for k, (entry, sites) in enumerate(CODE_READS.items()):
        for site in sites:
            image.patch_word(site, _stock(site), (_stock(site) & 0xFFFF0000) | (4 * (LAST_ID + 1 + k)))


def range_tests(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace) -> None:
    """The name widgets' "id < 0x4D" also holds for IDs 0x66 and up."""
    for site in RANGE_TESTS:
        word = _stock(site)
        if word >> 26 != 11 or word & 0xFFFF != 0x4D:
            raise dolfile.DolError(f'0x{site:08X}: {word:08X} is not cmpwi _,0x4d')
        cr, reg = (word >> 23) & 7, (word >> 16) & 31
        hs.code.put(b'', 4)
        a = Asm(hs.code.here)
        a.cmpwi(reg, 0x4D, cr=cr).blt('back', cr=cr)        # stock names
        a.cmpwi(reg, ids.FIRST_NEW, cr=cr).blt('mii', cr=cr)
        a.cmpwi(reg, 0x7FFF, cr=cr).b('back')               # new IDs: "less than" -> the name path
        a.label('mii')
        a.cmpwi(reg, 0x4D, cr=cr)                           # Miis / sentinel: the stock result
        a.label('back')
        a.b(site + 4)
        at = hs.code.put(a.assemble(), 4)
        image.patch_word(site, word, one(site, lambda b: b.b(at)))


def plate_sites(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace, first_row: int) -> list[str]:
    """``addi rD,r4,0x149`` -> ``first_row + id - 0x66`` for new IDs; replaces the ids step's alias stub there."""
    log = []
    for s in inventory.group('name_label_rows'):
        word = image.u32(s.address)
        if word != s.stock and word >> 26 != 18:
            raise dolfile.DolError(f'0x{s.address:08X}: {word:08X} is neither stock nor a branch')
        rd = (s.stock >> 21) & 31
        hs.code.put(b'', 4)
        a = Asm(hs.code.here)
        a.cmplwi('r4', ids.FIRST_NEW).blt('stock').cmplwi('r4', LAST_ID).bgt('stock')
        a.addi(f'r{rd}', 'r4', first_row - ids.FIRST_NEW).b(s.address + 4)
        a.label('stock')
        a.word(s.stock).b(s.address + 4)
        at = hs.code.put(a.assemble(), 4)
        image.write_word(s.address, one(s.address, lambda b: b.b(at)))
        if word != s.stock:
            log.append(f'0x{s.address:08X}: the template alias for name plates replaced')
    return log


# --------------------------------------------------------------------------
# Name plates (select layout rows id + 0x149)
# --------------------------------------------------------------------------

def font(size: int):
    font = ImageFont.truetype(FONT, size)
    font.set_variation_by_axes([FONT_WEIGHT, 100])          # weight, width 100 (normal)
    return font


def plate_image(text: str):
    """A 115x16 plate: white text, centred, shrunk to fit."""
    img = Image.new('RGBA', PLATE_CELL, (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    size = FONT_SIZE
    while size > FONT_MIN and draw.textbbox((0, 0), text, font=font(size))[2] > PLATE_CELL[0] - 2:
        size -= 1
    face = font(size)
    x0, _y0, x1, _y1 = draw.textbbox((0, 0), text, font=face)
    draw.text(((PLATE_CELL[0] - (x1 - x0)) // 2 - x0, FONT_DY + (FONT_SIZE - size) // 2), text, font=face,
              fill=(255, 255, 255, 255))
    return img


def rgb5a3(img) -> bytes:
    """GX RGB5A3 in 4x4 tiles."""
    a = np.asarray(img.convert('RGBA'), dtype=np.uint16)
    h, w = a.shape[:2]
    r, g, b, alpha = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    opaque = 0x8000 | (r >> 3) << 10 | (g >> 3) << 5 | b >> 3
    clear = (alpha >> 5) << 12 | (r >> 4) << 8 | (g >> 4) << 4 | b >> 4
    v = np.where(alpha >= 0xE0, opaque, clear).astype('>u2')
    return v.reshape(h // 4, 4, w // 4, 4).transpose(0, 2, 1, 3).tobytes()


def plate_page(cells: list[str]) -> tuple[int, int, list[tuple[int, int]]]:
    """(width, height, top-left per cell) of the plate page: columns of up to 64 cells, power-of-two sizes."""
    per_column = 1024 // PLATE_CELL[1]
    columns = -(-len(cells) // per_column)
    width = 1 << (columns * PLATE_COLUMN - 1).bit_length()
    height = 1 << (min(len(cells), per_column) * PLATE_CELL[1] - 1).bit_length()
    return width, height, [((k // per_column) * PLATE_COLUMN, (k % per_column) * PLATE_CELL[1])
                           for k in range(len(cells))]


def plate_layout(data: bytes, names: dict[int, str], first_row: int) -> bytes:
    """One language's select layout with the plate page, rows for 0x66..LAST_ID and the named spare rows'
    rows repointed. ``names``: {id: that language's name}."""
    lay = layout2d.Layout(data)
    if len(lay.rows) != first_row:
        raise layout2d.Layout2dError(f'the select layout has {len(lay.rows)} rows, expected {first_row}')
    named = sorted(names)
    texts = list(dict.fromkeys([UNNAMED] + [names[c] for c in named]))     # one cell per distinct text
    width, height, at = plate_page(texts)
    page_img = Image.new('RGBA', (width, height), (255, 255, 255, 0))
    for text, xy in zip(texts, at):
        page_img.paste(plate_image(text), xy)
    page = lay.add_texture(rgb5a3(page_img), width, height, GX_RGB5A3, PLATE_TEMPLATE_PAGE)
    cell = {c: at[texts.index(names[c])] for c in named}

    def uv(xy):
        x, y = xy
        return y / height, x / width, (y + PLATE_CELL[1]) / height, (x + PLATE_CELL[0]) / width
    for cid in range(ids.FIRST_NEW, LAST_ID + 1):
        lay.add_row(page, uv(cell.get(cid, at[0])))
    for cid in named:
        if cid < ids.FIRST_NEW:
            lay.rows[PLATE_ROW + cid] = struct.pack('>HH4f', page, 0, *uv(cell[cid]))
    return lay.to_bytes()


# --------------------------------------------------------------------------
# Step
# --------------------------------------------------------------------------

@steps.register('names')
def apply(ctx: steps.RosterContext) -> list[str]:
    names = parse_names(ctx.config)
    ctx.state['names'] = names
    if not names:
        return ['no "name" in the roster config: names stay as they are']
    if ctx.dat is None:
        raise NameConfigError('dt_na.dat is missing in the output folder')
    log = write_tables(ctx, names)
    files = layout_file.get(ctx)
    counts = {len(layout2d.Layout(files.read(lang)).rows) for lang in dhs.LANGS}
    if len(counts) != 1:
        raise layout2d.Layout2dError(f'the select layout copies differ in their row count: {sorted(counts)}')
    first_row = counts.pop()
    hs = dol_hammerspace.get(ctx)
    code_reads(ctx.dol)
    range_tests(ctx.dol, hs)
    log += plate_sites(ctx.dol, hs, first_row)
    hs.commit()
    log += files.update(lambda lang, data: plate_layout(data, {c: n[lang] for c, n in names.items()}, first_row))
    by_name: dict[str, list[int]] = {}
    for c, n in sorted(names.items()):
        by_name.setdefault(n['en'], []).append(c)
    log.append('names: ' + '; '.join(f'{ids.id_ranges(group)} {name}' for name, group in by_name.items())
               + f'; text entries to 0x{LAST_ID:X} (codes {CODE_ENTRIES[0]}/{CODE_ENTRIES[1]} -> '
               f'{LAST_ID + 1}/{LAST_ID + 2}); plates on rows {first_row}.. (new IDs), "-" for unnamed IDs')
    return log
