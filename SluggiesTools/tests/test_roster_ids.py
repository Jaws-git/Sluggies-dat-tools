"""New character IDs on a synthetic DOL built from the site inventory.

The synthetic DOL has one text section over the code the inventory names and
one data section over the per-ID tables. Every inventory site holds its stock
word, and every table pair is a lis/addi that builds its table's address, so
the step runs exactly as on the real DOL, but on made-up table contents.
"""

import struct
import unittest
from unittest import mock

from SluggiesTools.Dol import dolfile, inventory, ppc, relocate
from SluggiesTools.Roster import dol_hammerspace as dhs
from SluggiesTools.Roster import ids

TEXT, TEXT_END = 0x80060000, 0x80600000
DATA, DATA_END = 0x80622C00, 0x806D3000
BSS, BSS_SIZE = 0x80706D00, 0x9E180


def synthetic_dol() -> bytes:
    text = bytearray(TEXT_END - TEXT)
    data = bytearray(DATA_END - DATA)

    def put_word(address, word):
        if TEXT <= address < TEXT_END:
            struct.pack_into('>I', text, address - TEXT, word)

    for t in inventory.tables():
        for lis_site, low_site in t.all_pairs:
            put_word(lis_site, ppc.one(lis_site, lambda a: a.lis('r5', ppc.ha(t.address))))
            put_word(low_site, ppc.one(low_site, lambda a: a.addi('r5', 'r5', ppc.lo(t.address))))
    for name in inventory.load()['groups']:
        for s in inventory.group(name):
            put_word(s.address, s.stock)
    # table contents: row i of every table is filled with the byte i (stats keep their ID in bytes 0-1)
    for t in inventory.tables():
        if not DATA <= t.address < DATA_END:
            continue
        for i in range(ids.STOCK_IDS):
            start = t.address - DATA + t.header + i * t.row_size
            data[start:start + t.row_size] = bytes([i]) * t.row_size
    selector = inventory.table('selector').address - DATA
    for i in range(ids.STOCK_IDS):
        data[selector + 8 * i:selector + 8 * i + 8] = bytes([0, i, i, 1, 0, 0, 1 if i < 0x47 else 0, 0])
    for i in (0x06, 0x42, 0x43):         # a Yoshi-like wheel: group 0x0B, species 6
        data[selector + 8 * i:selector + 8 * i + 8] = bytes([0x0B, 6, 6, 0, 0, 0, 1, i & 7])
    stats = inventory.table('stats')
    for i in range(ids.STOCK_IDS):
        row = stats.address - DATA + stats.header + i * stats.row_size
        data[row:row + 2] = struct.pack('>H', i)
        data[row + ids.CHEM_BASE:row + ids.CHEM_BASE + ids.STOCK_IDS] = bytes((i + j) % 4 for j in range(ids.STOCK_IDS))

    header = bytearray(dolfile.HEADER_SIZE)
    struct.pack_into('>I', header, 0, dolfile.HEADER_SIZE)
    struct.pack_into('>I', header, 0x48, TEXT)
    struct.pack_into('>I', header, 0x90, len(text))
    struct.pack_into('>I', header, 7 * 4, dolfile.HEADER_SIZE + len(text))
    struct.pack_into('>I', header, 0x48 + 7 * 4, DATA)
    struct.pack_into('>I', header, 0x90 + 7 * 4, len(data))
    struct.pack_into('>III', header, 0xD8, BSS, BSS_SIZE, TEXT)
    return bytes(header) + bytes(text) + bytes(data)


_DOL = None


def fresh_image() -> dolfile.DolImage:
    global _DOL
    if _DOL is None:
        _DOL = synthetic_dol()
    return dolfile.DolImage(_DOL)


def run(config: dict) -> tuple[dolfile.DolImage, dhs.DolHammerspace, list[str]]:
    image = fresh_image()
    hs = dhs.DolHammerspace.create(image)
    with mock.patch.object(relocate, 'scan_refs', return_value=[]):
        log = ids.apply_ids(image, hs, ids.parse_ids(config))
    hs.commit()
    return image, hs, log


def moved_to(image, table) -> int:
    lis_site, low_site = table.all_pairs[0]
    return relocate.pair_address(image, lis_site, low_site)


class ParseIdsTests(unittest.TestCase):
    def test_defaults_and_swatch_names(self):
        parsed = ids.parse_ids({'ids': [{'template': 0}, {'id': '0x80', 'template': '0x06', 'swatch': 'green'}]})
        self.assertEqual(parsed, [ids.NewId(0x66, 0, 0, None), ids.NewId(0x80, 6, 6, 3)])

    def test_refusals(self):
        for bad in ({'id': '0x65', 'template': 0}, {'id': 0xFF, 'template': 0}, {'template': 0x4D},
                    {'id': 0x70}, {'template': 0, 'wheel': 0x70}, {'template': 0, 'swatch': 11}):
            with self.subTest(bad=bad), self.assertRaises(ids.IdConfigError):
                ids.parse_ids({'ids': [bad]})
        with self.assertRaises(ids.IdConfigError):
            ids.parse_ids({'ids': [{'id': 0x66, 'template': 0}, {'id': 0x66, 'template': 1}]})


class IdentityRelocationTests(unittest.TestCase):
    def test_every_table_moves_unchanged(self):
        before = fresh_image()
        image, hs, log = run({'ids': []})
        self.assertIn('identity relocation', log[-1])
        for table in ids.moved_tables():
            new = moved_to(image, table)
            self.assertTrue(hs.data.base <= new < hs.data.here, table.name)
            for lis_site, low_site in table.all_pairs:
                self.assertEqual(relocate.pair_address(image, lis_site, low_site), new)
            if table.name != ids.HANDLE_TABLE:
                self.assertEqual(image.read(new, table.length), before.read(table.address, table.length))
        # no hook site changed
        for s in inventory.group('roster_hook') + inventory.group('model_resolver'):
            self.assertEqual(image.u32(s.address), s.stock)


class NewIdTests(unittest.TestCase):
    CONFIG = {'ids': [{'id': '0x66', 'template': '0x00', 'swatch': 'blue'},
                      {'id': '0x80', 'template': '0x00', 'swatch': 'green'},
                      {'id': '0x67', 'template': '0x06', 'swatch': 'pink'}]}

    @classmethod
    def setUpClass(cls):
        cls.image, cls.hs, cls.log = run(cls.CONFIG)

    def row(self, name, cid):
        table = inventory.table(name)
        base = moved_to(self.image, table) + table.header
        return self.image.read(base + cid * table.row_size, table.row_size)

    def test_selector_rows_and_new_wheel_group(self):
        self.assertEqual(self.row('selector', 0x00)[0], 0x0C)     # Mario gets the next free group
        self.assertEqual(self.row('selector', 0x66), bytes([0x0C, 0, 0, 0, 0, 1, 1, 1]))
        self.assertEqual(self.row('selector', 0x80), bytes([0x0C, 0, 0, 0, 0, 2, 1, 3]))
        self.assertEqual(self.row('selector', 0x67)[:3], bytes([0x0B, 6, 6]))
        self.assertEqual(self.row('selector', 0x67)[7], 8)
        self.assertEqual(self.row('selector', 0x70), bytes(8))
        self.assertIn('0x00 gets wheel group 0x0C', self.log)

    def test_other_tables_copy_the_template(self):
        self.assertEqual(self.row('hitbox', 0x67), bytes([6]) * 8)
        self.assertEqual(self.row('charfloats', 0x80), bytes(0x24))
        stats = self.row('stats', 0x67)
        self.assertEqual(struct.unpack_from('>H', stats)[0], 0x67)
        self.assertEqual(stats[ids.CHEM_BASE:ids.CHEM_BASE + 4], bytes([2, 3, 0, 1]))   # Yoshi's row
        self.assertEqual(self.row('stats', 0x70)[ids.CHEM_BASE], ids.NEUTRAL)
        handles = self.row('model_handles', 0x66)
        self.assertEqual(struct.unpack('>III', handles), (1, 2, 4))

    def test_hooks_branch_into_the_text_section(self):
        for group, address, link in (('roster_hook', 0x8006BD58, False), ('model_resolver', 0x80367078, False),
                                     ('portrait_renderer', 0x80395DD0, False), ('chemistry_hook', 0x8015C880, False),
                                     ('select_chemistry', 0x80069B80, True), ('charge_scale', 0x800F3478, True),
                                     ('portrait_preview_calls', 0x80064664, True)):
            word = self.image.u32(address)
            target = ppc.branch_target(word, address)
            self.assertIsNotNone(target, f'{address:08X}')
            self.assertTrue(dhs.TEXT_BASE < target < self.hs.code.here, f'{address:08X} -> {target:08X}')
            self.assertEqual(bool(word & 1), link, f'{address:08X}')

    def test_constant_patches(self):
        for s in inventory.group('availability_bounds'):
            self.assertEqual(self.image.u32(s.address) & 0xFFFF, 0xFF)
        self.assertEqual(self.image.u32(0x80431E10), 0x38600200)
        self.assertEqual(self.image.u32(0x80431EF8), 0x2C170100)
        self.assertEqual(self.image.u32(0x80069B50) & 0xFFFF, 0x100)
        for s in inventory.group('id_limits'):           # Toy Field slot-machine tests stay stock
            if s.stock >> 16 == 0x4080:
                self.assertEqual(self.image.u32(s.address), s.stock)

    def test_new_by_new_chemistry_follows_templates(self):
        stats = inventory.table('stats')
        blob = self.image.read(moved_to(self.image, stats), stats.header + ids.ROWS * ids.STATS_ROW)
        table = ids.new_by_new_chemistry(blob, stats.header, ids.parse_ids(self.CONFIG))
        n = ids.ID_BOUND - ids.FIRST_NEW + 1
        self.assertEqual(table[(0x66 - 0x66) * n + (0x67 - 0x66)], (0 + 6) % 4)   # Mario's row, Yoshi's column
        self.assertEqual(table[(0x66 - 0x66) * n + (0x70 - 0x66)], ids.NEUTRAL)

    def test_wheel_cap_refused(self):
        many = {'ids': [{'template': 0x06} for _ in range(8)]}       # 3 shown Yoshis + 8 = 11
        with self.assertRaisesRegex(ids.IdConfigError, 'at most 10'):
            run(many)


if __name__ == '__main__':
    unittest.main()
