"""Roster expansion: the icon bank built by the roster step (synthetic stock bank, no wimgt)."""

import os
import struct
import tempfile
import unittest
from unittest import mock

from PIL import Image

from SluggiesTools.Dol import relocate
from SluggiesTools.Roster import dat_hammerspace as dhs
from SluggiesTools.Roster import dol_hammerspace, datfile, icons, ids, steps
from SluggiesTools.tests.test_roster_ids import fresh_image


def source_table(count: int, donor_rows) -> bytes:
    """A source table of ``count`` records, IDs descending (the front table starts with key 0x4D, as stock);
    IDs 0-5 point at ``donor_rows``, the others at their index; each body is filled with its index."""
    table = bytearray(icons.SOURCE_HEADER + count * icons.SOURCE_RECORD)
    struct.pack_into('>I', table, 0x08, len(table))
    struct.pack_into('>HH', table, 0x24, count, icons.SOURCE_RECORD)
    donors = dict(zip(range(6), donor_rows))
    keys = [0x4D, *range(0x46, -1, -1)] if count == 72 else list(range(count - 1, -1, -1))
    for index, cid in enumerate(keys):
        start = icons.SOURCE_HEADER + index * icons.SOURCE_RECORD
        struct.pack_into('>HHHH', table, start, 0x0014 if index == 0 else 0x0114, cid, 0x0400, donors.get(cid, index))
        table[start + 8:start + icons.SOURCE_RECORD] = bytes([index & 0xFF]) * (icons.SOURCE_RECORD - 8)
    return bytes(table)


def stock_bank() -> bytes:
    """A synthetic stock icon bank: the stock header, an empty texture section and a container with the three
    source tables and 152 resource rows, laid out as the stock bank."""
    bank = bytearray(icons.STOCK_BANK_LENGTH)
    struct.pack_into('>II', bank, 0, icons.STOCK_TEXTURE_SECTION, icons.STOCK_CONTAINER)
    struct.pack_into('>H', bank, 0x20, icons.STOCK_TEXTURE_COUNT)
    root = icons.STOCK_CONTAINER
    descriptor = root + icons.CONTAINER_HEAD
    tables = [source_table(71, [0x00, 0x02, 0x03, 0x04, 0x05, 0x06]), source_table(71, [0x00, 0x02, 0x03, 0x04, 0x05, 0x06]),
              source_table(72, [0x4F, 0x50, 0x51, 0x52, 0x53, 0x54])]
    at = descriptor + 0x14
    starts = []
    for t in tables:
        bank[at:at + len(t)] = t
        starts.append(at)
        at += len(t)
    res = at
    struct.pack_into('>II', bank, res, icons.STOCK_ROWS, 8 + icons.STOCK_ROWS * icons.ROW_SIZE)
    struct.pack_into('>I', bank, root + icons.CONTAINER_END_FIELD, res + 8 + icons.STOCK_ROWS * icons.ROW_SIZE - root)
    struct.pack_into('>I', bank, descriptor, 3)
    for field, target in zip((icons.RESOURCE_FIELD,) + icons.TABLE_FIELDS, [res] + starts):
        struct.pack_into('>i', bank, descriptor + field, target - descriptor)
    return bytes(bank)


STOCK = stock_bank()


def entry(cid, like=0x00):
    return icons.IconEntry(cid, f'{cid:02x}_side.png', f'{cid:02x}_front.png', like)


def table_length(bank, offset):
    return struct.unpack_from('>I', bank, offset + 0x08)[0]


def portrait(colour):
    return Image.new('RGBA', (48, 51), colour)


def blank_payload(page):
    return bytes(page.payload_length)


class PageTests(unittest.TestCase):
    def test_page_size(self):
        self.assertEqual(icons.page_size(1)[:2], (64, 64))
        self.assertEqual(icons.page_size(3)[:2], (256, 64))            # ties (also 128x128, 64x256): the wider page
        self.assertEqual(icons.page_size(19)[:2], (1024, 64))
        self.assertEqual(icons.page_size(20)[:2], (1024, 128))
        self.assertEqual(icons.page_size(159)[:2], (1024, 512))        # every new ID + the six spare rows
        self.assertEqual(icons.page_size(361)[:2], (1024, 1024))
        with self.assertRaises(icons.IconBankError):
            icons.page_size(362)

    def test_identical_portraits_share_a_cell(self):
        page = icons.pack_page([portrait('red'), portrait('blue'), portrait('red')])
        self.assertEqual(page.cells, [(0, 0), (52, 0), (0, 0)])
        self.assertEqual(page.image.getpixel((52 + 47, 50)), (0, 0, 255, 255))
        self.assertEqual(page.image.getpixel((52 + 48, 50))[3], 0)      # transparent texel right of the art
        self.assertEqual(page.image.getpixel((52, 51))[3], 0)            # and below it


def packed(entries, side_cells=None):
    n = len(entries)
    width, height, per_row = icons.page_size(n)
    grid = [((k % per_row) * 52, (k // per_row) * 52) for k in range(n)]
    side = icons.Page(width, height, side_cells or grid)
    front = icons.Page(width, height, grid)
    payloads = [bytes([0x11 + i]) * p.payload_length for i, p in enumerate((side, front))]
    return icons.build_packed_bank(STOCK, entries, side, front, *payloads), side, front


def container(bank):
    tex_end = struct.unpack_from('>I', bank, 4)[0]
    desc = tex_end + icons.CONTAINER_HEAD
    return tex_end, desc, lambda field: icons._pointer(bank, desc, field)


class PackedBankTests(unittest.TestCase):
    def setUp(self):
        self.entries = [entry(0x48), entry(0x49, 0x01), entry(0x66, 0x02)]
        self.bank, self.side, self.front = packed(self.entries, [(0, 0), (52, 0), (0, 0)])

    def test_pages(self):
        bank = self.bank
        self.assertEqual(struct.unpack_from('>H', bank, 0x20)[0], icons.TEXTURE_COUNT)
        tex_end = struct.unpack_from('>I', bank, 4)[0]
        self.assertEqual(tex_end % 0x20, 0)
        for page_id, fill in ((icons.SIDE_PAGE, 0x11), (icons.FRONT_PAGE, 0x12)):
            d = icons._descriptor_offset(page_id)
            image, palette, height, width = struct.unpack_from('>IIHH', bank, d)
            self.assertEqual((width, height, palette, bank[d + 0x17]), (256, 64, 0, icons.icon_art.CMPR_FORMAT))
            at = icons.TEX_BASE + image                                  # offsets count from bank +0x20
            self.assertEqual(at % 0x20, 0)
            self.assertGreaterEqual(at, icons.STOCK_CONTAINER)
            self.assertLessEqual(at + width * height // 2, tex_end)
            self.assertEqual(bank[at:at + width * height // 2], bytes([fill]) * (width * height // 2))

    def test_rows_and_container(self):
        bank = self.bank
        tex_end, desc, ptr = container(bank)
        res = ptr(icons.RESOURCE_FIELD)
        count, length = struct.unpack_from('>II', bank, res)
        n = icons.STOCK_ROWS
        self.assertEqual(count, n + 6)
        self.assertEqual(length, 8 + count * 0x14)
        self.assertEqual(struct.unpack_from('>I', bank, tex_end + 0x10)[0], res + length - tex_end)
        self.assertEqual(len(bank), res + length + icons.BANK_TAIL)
        rows = [struct.unpack_from('>HH4f', bank, res + 8 + i * 0x14) for i in range(n, n + 6)]
        self.assertEqual(rows[1][:2], (icons.SIDE_PAGE, 0))
        self.assertEqual((rows[1][3] * 256, rows[1][5] * 256, rows[1][4] * 64), (52, 100, 51))
        self.assertEqual(rows[2][2:], rows[0][2:])                     # 0x66 shares 0x48's side portrait
        self.assertEqual(rows[5][0], icons.FRONT_PAGE)
        self.assertEqual(rows[5][3] * 256, 104)

    def test_keys_and_tables(self):
        bank = self.bank
        tex_end, desc, ptr = container(bank)
        front = icons._descriptor_offset(icons.FRONT_PAGE)
        page = icons.TEX_BASE + struct.unpack_from('>I', bank, front)[0]
        expect = page + self.front.payload_length                # right after the front page, never in a stock atlas
        self.assertEqual(bank[:icons.STOCK_CONTAINER][icons.DESCRIPTOR_TABLE + icons.TEXTURE_COUNT * icons.DESCRIPTOR_SIZE:],
                         STOCK[icons.DESCRIPTOR_TABLE + icons.TEXTURE_COUNT * icons.DESCRIPTOR_SIZE:icons.STOCK_CONTAINER])
        n = icons.STOCK_ROWS
        found = {}
        for field in icons.TABLE_FIELDS:
            offset = ptr(field)
            self.assertEqual(offset, expect)
            table = icons._table(bank, offset)
            found[field] = {struct.unpack_from('>H', r, 2)[0]: struct.unpack_from('>H', r, 6)[0] for r in table}
            keys = [struct.unpack_from('>H', r, 2)[0] for r in table]
            self.assertEqual(keys, sorted(keys, reverse=True))
            expect = offset + table_length(bank, offset)
        self.assertEqual([found[icons.SIDE_FIELD][c] for c in (0x48, 0x49, 0x66)], [n, n + 1, n + 2])
        self.assertEqual(found[icons.NORMAL_A_FIELD][0x66], n + 5)
        self.assertNotIn(0x48, found[icons.NORMAL_A_FIELD])
        stock_res = icons._pointer(STOCK, icons.STOCK_CONTAINER + 0x14, icons.RESOURCE_FIELD)
        self.assertEqual(stock_res - icons.STOCK_CONTAINER, ptr(icons.RESOURCE_FIELD) - tex_end)
        self.assertFalse(any(bank[desc + 0x14:ptr(icons.RESOURCE_FIELD)]))   # the old tables' place: zeroed

    def test_full_roster_moves_the_tables_after_the_pages(self):
        entries = [entry(c, 0x04) for c in range(0x47, 0x4D)] + [entry(c, 0x02) for c in range(0x66, 0xFF)]
        bank, side, _front = packed(entries)
        self.assertEqual((side.width, side.height), (1024, 512))
        tex_end, _desc, ptr = container(bank)
        starts = [ptr(f) for f in icons.TABLE_FIELDS]
        front = icons._descriptor_offset(icons.FRONT_PAGE)
        front_image = icons.TEX_BASE + struct.unpack_from('>I', bank, front)[0]
        self.assertGreaterEqual(starts[0], front_image + 1024 * 512 // 2)
        end = starts[2] + table_length(bank, starts[2])
        self.assertLessEqual(end, tex_end)                             # inside the texture section
        self.assertEqual(len(icons._table(bank, starts[1])), 71 + len(entries))
        self.assertLess(len(bank) - len(STOCK), 600_000)

    def test_covered_palette_moves(self):
        stock = bytearray(STOCK)
        d86 = icons._descriptor_offset(0x86)
        struct.pack_into('>II', stock, d86, 0x13660, 0x1260)           # the real page 0x86: palette at file 0x1280
        struct.pack_into('>H', stock, d86 + 0x18, 0x100)
        stock[0x1280:0x1480] = bytes(range(256)) * 2
        side = icons.Page(1024, 64, [(0, 0)])
        bank = icons.build_packed_bank(bytes(stock), [entry(0x48)], side, side, bytes(0x8000), bytes(0x8000))
        palette = icons.TEX_BASE + struct.unpack_from('>I', bank, d86 + 4)[0]
        self.assertEqual(palette, icons.STOCK_CONTAINER)
        self.assertEqual(bank[palette:palette + 0x200], bytes(range(256)) * 2)
        self.assertEqual(struct.unpack_from('>I', bank, d86)[0], 0x13660)

    def test_wrong_payload_length(self):
        side = icons.Page(1024, 64, [(0, 0)])
        with self.assertRaisesRegex(icons.IconBankError, 'payload'):
            icons.build_packed_bank(STOCK, [entry(0x48)], side, side, bytes(0x8000), bytes(0x4000))


class ParseTests(unittest.TestCase):
    def parse(self, config):
        return icons.parse_icons(config, check_files=False)

    def test_defaults(self):
        config = {'ids': [{'id': '0x66', 'template': '0x06', 'icon': {'side': 'a.png', 'front': 'b.png'}}],
                  'wheels': [{'id': '0x49', 'icon': {'side': 'c.png', 'front': 'd.png'}}, {'id': '0x47'}]}
        out = self.parse(config)
        self.assertEqual([(e.char_id, e.like) for e in out], [(0x49, 0x01), (0x66, 0x06)])
        self.assertTrue(out[0].side_path.endswith('c.png'))
        self.assertEqual(self.parse({'ids': [], 'wheels': []}), [])

    def test_errors(self):
        icon = {'side': 'a.png', 'front': 'b.png'}
        for config, message in (({'ids': [{'template': 0, 'icon': icon}]}, 'explicit "id"'),
                                ({'wheels': [{'id': '0x47', 'icon': {'side': 'x/a.png', 'front': 'b.png'}}]}, 'plain'),
                                ({'wheels': [{'id': '0x47', 'icon': dict(icon, like='0x50')}]}, 'not a stock'),
                                ({'wheels': [{'id': '0x47', 'icon': dict(icon, fit='zoom')}]}, 'fit')):
            with self.assertRaisesRegex(icons.IconConfigError, message):
                self.parse(config)


class StepTests(unittest.TestCase):
    STOCK_AT = 0x1000

    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(dhs, 'BASE_SIZE', 0x100000))
        self.enterContext(mock.patch.object(icons, 'STOCK_BANK_OFFSET', self.STOCK_AT))
        self.enterContext(mock.patch.object(icons, 'ICON_DIR', tmp))
        path = os.path.join(tmp, 'dt_na.dat')
        with open(path, 'wb') as f:
            f.write(bytes(self.STOCK_AT) + STOCK + bytes(0x100000 - self.STOCK_AT - len(STOCK)))
        for name, colour in (('a.png', 'red'), ('b.png', 'blue')):
            portrait(colour).save(os.path.join(tmp, name))
        self.dat = datfile.DatFile(path)
        self.image = fresh_image()
        # stock words at the hook and resolver sites, the stock icon record, a minimal directory table
        for site, stock, _stub in icons.OLD_HOOKS:
            self.image.write_word(site, stock)
        self.image.write_word(icons.RESOLVER_SITE, icons.RESOLVER_STOCK)
        record = b''.join(struct.pack('>4I', dhs.hh._DAT_FNAME_PTR, len(STOCK), self.STOCK_AT, len(STOCK))
                          for _ in range(3))
        self.image.write(icons.ICON_RECORD, record)
        table = dhs.dol_base_address(dhs.hh._DIRS_START)
        self.image.write(table, struct.pack(f'>{dhs.hh._DIRS_COUNT}I', *[icons.ICON_RECORD] * dhs.hh._DIRS_COUNT))

    def run_step(self, config, state=None):
        ctx = steps.RosterContext(dol=self.image, dat=self.dat, config=config)
        ctx.state.update(state or {})
        return ctx, icons.apply(ctx, encode_cmpr=blank_payload)

    def test_spare_rows_and_hooks(self):
        hooked = icons.one(icons.OLD_HOOKS[0][0], lambda a: a.b(icons.OLD_HOOKS[0][2]))
        self.image.write_word(icons.OLD_HOOKS[0][0], hooked)      # the retired pipeline's lower hook
        icon = {'side': 'a.png', 'front': 'b.png'}
        config = {'wheels': [{'id': '0x47', 'icon': icon}]}
        _ctx, log = self.run_step(config)
        words = dhs.read_record(self.image, icons.ICON_RECORD)
        offset, length, alloc = dhs.slot(words, 'fr')
        self.assertGreaterEqual(offset, 0x100000)
        self.assertEqual(alloc, length)
        bank = self.dat.read(offset, length)
        entries = icons.parse_icons(config, icons.ICON_DIR)
        side, front = icons.compose_pages(entries)
        self.assertEqual(bank, icons.build_packed_bank(STOCK, entries, side, front, blank_payload(side),
                                                       blank_payload(front)))
        self.assertIn('side 64x64 (1 portraits)', ' '.join(log))
        self.assertEqual(self.image.u32(icons.OLD_HOOKS[0][0]), icons.OLD_HOOKS[0][1])
        self.assertIn('back to stock', ' '.join(log))
        self.assertEqual(self.image.u32(icons.RESOLVER_SITE), icons.RESOLVER_STOCK)   # no new IDs with art

    def test_new_id_with_art(self):
        hs = dol_hammerspace.DolHammerspace.create(self.image)
        state = {}
        config = {'ids': [{'id': '0x66', 'template': '0x00', 'icon': {'side': 'a.png', 'front': 'b.png'}}]}
        with mock.patch.object(relocate, 'scan_refs', return_value=[]):
            ids.apply_ids(self.image, hs, ids.parse_ids(config), state)
        hs.commit()
        state['hammerspace'] = hs
        self.assertEqual(self.image.read(state['portrait_of'] + 0x66, 1), b'\x00')
        _ctx, log = self.run_step(config, state)
        self.assertEqual(self.image.read(state['portrait_of'] + 0x66, 1), b'\x66')
        self.assertEqual(self.image.u32(icons.RESOLVER_SITE) >> 26, 18)
        self.assertIn('own portraits: 0x66', ' '.join(log))

    def test_nothing_configured(self):
        before = self.image.to_bytes()
        _ctx, log = self.run_step({'wheels': [{'id': '0x47'}]})
        self.assertEqual(self.image.to_bytes(), before)
        self.assertIn('stays as it is', log[0])

    def test_alternate_side_view(self):
        config = {'wheels': [{'id': '0x47', 'icon': {'side': 'a.png', 'front': 'b.png', 'side_alt': 'b.png'}}]}
        entries = icons.parse_icons(config, icons.ICON_DIR)
        self.assertTrue(entries[0].has_alt)
        side, front = icons.compose_pages(entries)
        self.assertEqual((side.cells, front.cells), ([(0, 0), (52, 0)], [(0, 0)]))   # the alt cell after the side one
        _ctx, log = self.run_step(config)
        self.assertIn('alternate side views: 0x47', ' '.join(log))
        offset, length, _alloc = dhs.slot(dhs.read_record(self.image, icons.ICON_RECORD), 'en')
        bank = self.dat.read(offset, length)
        _tex_end, _desc, ptr = container(bank)
        normal_a = {struct.unpack_from('>H', r, 2)[0]: struct.unpack_from('>H', r, 6)[0]
                    for r in icons._table(bank, ptr(icons.NORMAL_A_FIELD))}
        self.assertEqual(normal_a[0x47], icons.STOCK_ROWS + 2)


def with_mirror_bits(stock: bytes) -> bytes:
    """``stock`` with the mirror bit set on every side record (as the real bank has for most characters)."""
    bank = bytearray(stock)
    start = icons._pointer(bank, icons.STOCK_CONTAINER + icons.CONTAINER_HEAD, icons.SIDE_FIELD)
    count = struct.unpack_from('>H', bank, start + 0x24)[0]
    for i in range(count):
        bank[start + icons.SOURCE_HEADER + i * icons.SOURCE_RECORD + icons.SIDE_FLAG_OFFSET] |= icons.SIDE_MIRROR_BIT
    return bytes(bank)


class AlternateSideTests(unittest.TestCase):
    """An entry with an alternate side view: its cell follows the side cells on the side page, its normal_a key
    points at its row (after the side and front rows) and the mirror bit of its side record is cleared."""

    def setUp(self):
        alt = lambda cid, like: icons.IconEntry(cid, f'{cid:02x}_side.png', f'{cid:02x}_front.png', like,
                                                side_alt_path=f'{cid:02x}_alt.png')
        self.entries = [entry(0x48), alt(0x49, 0x01), alt(0x01, 0x01), alt(0x66, 0x02)]
        n = len(self.entries)
        width, height, per_row = icons.page_size(n + 3)
        grid = [((k % per_row) * 52, (k // per_row) * 52) for k in range(n + 3)]
        self.side = icons.Page(width, height, grid)
        self.front = icons.Page(width, height, grid[:n])

    def build(self, stock=STOCK):
        return icons.build_packed_bank(stock, self.entries, self.side, self.front,
                                       bytes(self.side.payload_length), bytes(self.front.payload_length))

    @staticmethod
    def records(bank, field):
        _tex_end, _desc, ptr = container(bank)
        return {struct.unpack_from('>H', r, 2)[0]: r for r in icons._table(bank, ptr(field))}

    def test_rows_and_keys(self):
        bank = self.build()
        n, m = icons.STOCK_ROWS, len(self.entries)
        _tex_end, _desc, ptr = container(bank)
        res = ptr(icons.RESOURCE_FIELD)
        count = struct.unpack_from('>I', bank, res)[0]
        self.assertEqual(count, n + 2 * m + 3)
        rows = [struct.unpack_from('>HH4f', bank, res + 8 + i * 0x14) for i in range(n, count)]
        self.assertEqual([r[0] for r in rows[2 * m:]], [icons.SIDE_PAGE] * 3)     # the alternate cells: side page
        self.assertEqual(round(rows[2 * m][3] * self.side.width), self.side.cells[m][0])
        row = lambda field, cid: struct.unpack_from('>H', self.records(bank, field)[cid], 6)[0]
        self.assertEqual([row(icons.NORMAL_A_FIELD, c) for c in (0x49, 0x01, 0x66)],
                         [n + 2 * m, n + 2 * m + 1, n + 2 * m + 2])
        self.assertNotIn(0x48, self.records(bank, icons.NORMAL_A_FIELD))        # a spare row without one: no key
        self.assertEqual(row(icons.SIDE_FIELD, 0x49), n + 1)
        self.assertEqual(row(icons.FRONT_FIELD, 0x01), n + m + 2)

    def test_mirror_bit_cleared_for_entries_with_an_alternate_view(self):
        bank = self.build(with_mirror_bits(STOCK))
        side = self.records(bank, icons.SIDE_FIELD)
        flag = lambda cid: side[cid][icons.SIDE_FLAG_OFFSET] & icons.SIDE_MIRROR_BIT
        self.assertEqual([flag(c) for c in (0x49, 0x01, 0x66)], [0, 0, 0])
        self.assertEqual(flag(0x48), icons.SIDE_MIRROR_BIT)            # no alternate view: the side cell is mirrored
        self.assertEqual(flag(0x00), icons.SIDE_MIRROR_BIT)            # an untouched stock record

    def test_cell_count_is_checked(self):
        with self.assertRaisesRegex(icons.IconBankError, 'alternate'):
            icons.build_packed_bank(STOCK, self.entries, self.front, self.front,
                                    bytes(self.front.payload_length), bytes(self.front.payload_length))

    def test_parse(self):
        config = {'wheels': [{'id': '0x47', 'icon': {'side': 'a.png', 'front': 'b.png', 'side_alt': 'c.png'}},
                             {'id': '0x48', 'icon': {'side': 'a.png', 'front': 'b.png'}}]}
        out = icons.parse_icons(config, check_files=False)
        self.assertTrue(out[0].side_alt_path.endswith('c.png'))
        self.assertIsNone(out[1].side_alt_path)
        with self.assertRaisesRegex(icons.IconConfigError, 'side_alt'):
            icons.parse_icons({'wheels': [{'id': '0x47', 'icon': {'side': 'a.png', 'front': 'b.png',
                                                                  'side_alt': 'x/c.png'}}]}, check_files=False)


if __name__ == '__main__':
    unittest.main()
