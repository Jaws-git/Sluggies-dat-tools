"""Roster expansion Phase 6: the exhibition grid on the synthetic inventory DOL and a synthetic layout bank.

The DOL words and the layout bytes were also checked against the external
tool on the real files (plan Phase 6); these tests pin the behaviour.
"""

import struct
import unittest
from unittest import mock

from SluggiesTools.Dol import inventory, relocate
from SluggiesTools.Icons import layout2d
from SluggiesTools.Icons.tests.test_layout2d import make_bank, make_element, make_sprite_record, make_track
from SluggiesTools.Roster import dol_hammerspace as dhs
from SluggiesTools.Roster import grid, layout_file, steps, wheels
from SluggiesTools.tests.test_roster_ids import fresh_image
from SluggiesTools.tests.test_roster_wheels import FakeLayout

# the US DOL's head list (head -> character) and square -> head map
HEADS = bytes.fromhex('000102030405060708090a0b0c0d0e0f101112131415181b1c212526272829303637383' +
                      '93a3e3f40414d59')
STOCK_MAP = bytes([6, 17, 5, 4, 0, 11, 10, 2, 19, 9, 7, 22, 13, 24, 12, 3, 28, 34, 31, 23,
                   8, 18, 15, 16, 20, 21, 35, 14, 26, 27, 38, 39, 33, 29, 30, 25, 40, 36, 37, 32])


def parse(cfg):
    return grid.parse_grid({'grid': cfg}, HEADS[:41], STOCK_MAP)


def image_for_grid():
    image = fresh_image()
    image.write(grid.HEAD_LIST, HEADS)
    image.write(grid.STOCK_MAP, STOCK_MAP)
    for address, word in grid.ROW_SITES_STOCK.items():
        image.write_word(address, word)
    image.write_word(grid.HIT_CALL - 8, 0x387E0338)
    for call, _fn, step in grid.DPAD_CALLS:
        setup = (0x7FC3F378, 0x7EC4B378) + (() if step is None else (0x38A00000 | (step & 0xFFFF),))
        for i, word in enumerate(setup):
            image.write_word(call - 4 * len(setup) + 4 * i, word)
    return image


class ParseTests(unittest.TestCase):
    def test_default_shape_and_cells(self):
        g = parse({})
        self.assertEqual((g.cols, g.rows), (11, 4))
        self.assertEqual(g.cells[0], ('stock', grid.LUIGI_HEAD))           # column 0 top: Luigi
        self.assertEqual(g.cells[1:11], tuple(('stock', h) for h in STOCK_MAP[:10]))
        self.assertEqual(g.empty, [11, 22, 33])
        g = parse({'squares': [['0x66'], ['0x67'], ['0x68'], ['0x69']]})
        self.assertEqual((g.cols, g.rows), (12, 4))                          # 45 squares
        self.assertEqual([g.cells[i] for i in (0, 12, 24, 36, 11)],
                         [('square', 0), ('square', 1), ('square', 2), ('square', 3), ('stock', 1)])
        self.assertEqual(g.empty, [23, 35, 47])

    def test_heads(self):
        g = parse({'squares': [['0x66', '0x67']], 'shape': [11, 4]})
        heads = g.heads()
        self.assertEqual(heads[0], grid.STOCK_HEADS)                         # the square: head 43
        self.assertEqual(heads[11], grid.LUIGI_HEAD)
        self.assertEqual([heads[i] for i in g.empty], [44, 45])
        self.assertEqual(g.first_empty_head, 44)

    def test_order(self):
        order = [[f'0x{HEADS[h]:02X}' for h in STOCK_MAP[r * 10:r * 10 + 10]] + [None] for r in range(4)]
        order[3][10] = '0x66'
        order[0][10] = '0x01'
        g = parse({'squares': [['0x66', '0x47']], 'order': order})
        self.assertEqual(g.cells[10], ('stock', 1))
        self.assertEqual(g.cells[43], ('square', 0))
        self.assertEqual(g.empty, [21, 32])

    def test_errors(self):
        stock = [f'0x{HEADS[h]:02X}' for h in STOCK_MAP]
        for cfg, message in (({'squares': [['0x00']]}, 'head character'),
                             ({'squares': [['0x66'], ['0x66']]}, 'two squares'),
                             ({'squares': [[]]}, '1-10'),
                             ({'shape': [13, 4]}, 'not one of'),
                             ({'squares': [[f'0x{0x66 + i:02X}'] for i in range(20)]}, 'do not fit'),
                             ({'order': stock}, 'leaves out 0x01'),
                             ({'order': stock + ['0x01', '0x01']}, 'twice'),
                             ({'order': stock + ['0x70']}, 'neither')):
            with self.subTest(cfg=cfg), self.assertRaisesRegex(grid.GridConfigError, message):
                parse(cfg)


class CodeTests(unittest.TestCase):
    def build(self, cfg, cap=6):
        image = image_for_grid()
        hs = dhs.DolHammerspace.create(image)
        g = parse(cfg)
        log = grid.grid_code(image, hs, g, cap, refs=[])
        hs.commit()
        return image, hs, g, log

    def test_square_grid(self):
        image, _hs, g, log = self.build({'squares': [['0x66', '0x67'], ['0x47']], 'shape': [12, 5]})
        self.assertEqual(image.u32(grid.COUNT_LOOP_B), 0x38000000 | 60)
        self.assertEqual(image.u32(grid.OBJ_ALLOC), 0x38600000 | grid.OBJ_SIZE)
        self.assertEqual(image.u32(grid.CAPTAIN_SWAP_STORE), 0x60000000)
        # f: cursor constants moved by 20 (e.g. cmpwi 0x3D -> 0x51)
        site = grid.POSITION_SITES[0]
        self.assertEqual((image.u32(site) - inventory.site(grid.GROUP, site).stock) & 0xFFFF, 20)
        # d: the map table the constructor copies
        at = relocate.pair_address(image, grid.MAP_COPY[0], grid.MAP_COPY[1])
        self.assertEqual(image.read(at, 60), g.heads())
        self.assertEqual(image.u32(grid.MAP_COPY[2]) & 0xFFFF, (grid.SEL + 0x40 - grid.MAP) // 8)
        # e: the head list moved and grew
        table = inventory.table('head_list')
        moved = relocate.pair_address(image, *table.all_pairs[0])
        self.assertEqual(image.read(moved, 46), HEADS + b'\x66\x47' + HEADS[:1])
        for site in (grid.MEMBERS_FN, grid.MEMBER_TAKE, grid.HAS_MEMBERS, grid.CURSOR_FAMILY, grid.HIT_CALL,
                     grid.RANDOM_HEAD_BOUND):
            self.assertEqual(image.u32(site) >> 26, 18, hex(site))
        # g: 12 columns, 5 rows
        self.assertEqual(image.u32(grid.COLUMN_BOUNDS[0]) & 0xFFFF, 12)
        self.assertEqual(image.u32(grid.DOWN_ROWS) & 0xFFFF, 5)
        self.assertEqual(image.u32(grid.UP_WRAP) & 0xFFFF, 0x16 + 48)
        self.assertIn('17 empty cells', ' '.join(log))

    def test_stock_squares_only(self):
        image, _hs, g, log = self.build({})
        self.assertEqual(image.u32(grid.MEMBERS_FN), inventory.site(grid.GROUP, grid.MEMBERS_FN).stock)
        self.assertEqual(image.u32(grid.HAS_MEMBERS) >> 26, 18)
        self.assertEqual(image.u32(grid.DOWN_ROWS), grid.ROW_SITES_STOCK[grid.DOWN_ROWS])
        self.assertIn('3 empty cells', ' '.join(log))

    def test_refusals(self):
        image, hs, g, _log = self.build({})
        with self.assertRaisesRegex(Exception, 'not stock'):
            grid.grid_code(image, hs, g, 6, refs=[])
        image = image_for_grid()
        with self.assertRaisesRegex(Exception, 'more than 6'):
            grid.grid_code(image, dhs.DolHammerspace.create(image),
                           parse({'squares': [[f'0x{0x66 + i:02X}' for i in range(7)]]}), 6, refs=[])


def grid_bank() -> bytes:
    def element(tracks):
        return make_element([], extra_tracks=tracks)

    def track(x, y=0, ref=0xA2, n=1):
        return make_track([make_sprite_record(i == 0, i, x, y, resource=ref) for i in range(n)])

    elements = [make_element([(0, 0, 0xFF)]) for _ in range(0xBF)]
    squares = [(43 + 49 * c, 135 + 52 * r, 0xFF) for r in range(4) for c in range(10)]
    elements[grid.GRID_ELEMENT] = make_element(squares + [(533, 291, 0), (582, 291, 0)])
    for pos, bar, slots in grid.BARS:
        elements[pos] = element([track(9, 31, ref=bar)])
        elements[bar] = element([track(0), track(60), track(-60)])
        elements[slots] = element([track(47 + 53 * i) for i in range(9)])
    return make_bank(elements)


def xs(lay, element):
    return [struct.unpack_from('>hh', n, 4 + 8) for n in lay.node_blobs(element)]


class LayoutTests(unittest.TestCase):
    def test_twelve_by_five(self):
        g = parse({'squares': [['0x66']], 'shape': [12, 5]})
        lay = layout2d.Layout(grid.grid_layout(grid_bank(), g))
        cells = xs(lay, grid.GRID_ELEMENT)
        self.assertEqual(len(cells), 60)
        self.assertEqual(cells[0], (43 - 2 * 49 + 40, 105))                  # column 0, screen +40
        self.assertEqual(cells[10 + 12], (43 + 8 * 49 + 40, 105 + 48))         # stock column 9, one left
        self.assertEqual(cells[11 + 12][0], 43 + 9 * 49 + 40 - 2000)           # column 11: empty here
        self.assertEqual(cells[g.empty[0]][0], 43 + 40 - 2000 + (g.empty[0] % 12 - 2) * 49)
        bar = xs(lay, 0x9A)[0]
        self.assertEqual(bar, (9 - 98 + 40, 31 - 28))
        middle = lay.node_blobs(0x9C)[0]
        self.assertAlmostEqual(struct.unpack_from('>f', middle, 4 + 0x38)[0], 1.0 + 98 / 60, places=5)
        self.assertEqual(xs(lay, 0x9C)[1][0], 60 + 98)
        self.assertEqual([x for x, _y in xs(lay, 0xB9)][::8], [47 - 98 + 40, 471 + 40])
        banner = lay.node_blobs(grid.BANNER)[0]
        self.assertEqual(struct.unpack_from('>h', banner, 4 + 8)[0], grid.BANNER_X + 40)
        self.assertEqual(struct.unpack_from('>ff', banner, 4 + 0x34), (1.5, 1.5))

    def test_eleven_by_four_keeps_rows(self):
        lay = layout2d.Layout(grid.grid_layout(grid_bank(), parse({})))
        cells = xs(lay, grid.GRID_ELEMENT)
        self.assertEqual(len(cells), 44)
        self.assertEqual(cells[1], (43 + 20, 135))                           # stock column 0, screen +20
        self.assertEqual(cells[0], (43 - 49 + 20, 135))
        self.assertEqual(cells[11][0], 43 - 49 + 20 - 2000)                  # empty: off screen
        self.assertEqual(cells[12], (43 + 20, 135 + 52))


class StepTests(unittest.TestCase):
    def test_square_members_must_be_selectable(self):
        ctx = steps.RosterContext(dol=image_for_grid(), dat=None, config={'grid': {'squares': [['0x66']]}})
        ctx.state[layout_file.STATE_KEY] = FakeLayout()
        with self.assertRaisesRegex(grid.GridConfigError, 'not selectable'):
            grid.apply(ctx)

    def test_stock_grid_step(self):
        ctx = steps.RosterContext(dol=image_for_grid(), dat=None, config={'grid': {}})
        ctx.state[layout_file.STATE_KEY] = FakeLayout()
        with mock.patch.object(relocate, 'scan_refs', return_value=[]):
            log = grid.apply(ctx)
        self.assertIn('grid 11x4', log[0])
        self.assertEqual(len(ctx.state[layout_file.STATE_KEY].applied), 1)
        self.assertFalse(wheels.has_ten_members(ctx.dol))

    def test_no_grid_key_or_null(self):
        for config in ({}, {'grid': None}):
            ctx = steps.RosterContext(dol=image_for_grid(), dat=None, config=config)
            self.assertIn('stays stock', grid.apply(ctx)[0])


if __name__ == '__main__':
    unittest.main()
