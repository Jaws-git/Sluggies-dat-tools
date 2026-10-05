"""Roster expansion Phase 9: open slots (square-only IDs, the "empty slot" icon, shared name plates) and the presets."""

import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools.Dol import relocate
from SluggiesTools.Roster import grid, icons, ids, make_presets, names, wheels
from SluggiesTools.tests.test_roster_grid import HEADS, STOCK_MAP
from SluggiesTools.tests.test_roster_ids import fresh_image
from SluggiesTools.tests.test_roster_names import plate_bank
from SluggiesTools.Icons import layout2d
from SluggiesTools.Roster import dol_hammerspace as dhs

PRESETS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), '1_Input',
                       '_RosterConfigurations')
SHIPPED = ('01_Stock_Roster.json', '02_Stock_and_Unused.json', '03_Unuseds_and_8_color_slots.json',
           '04_Unuseds_and_10_color_slots.json', '05_all_in_one_maximum_12x5_grid.json')
SQUARE_CONFIG = {'ids': [{'id': '0x66', 'template': '0x02', 'wheel': None},
                         {'id': '0x67', 'template': '0x06', 'wheel': '0x06'}],
                 'grid': {'squares': [['0x66']]}}


class SquareOnlyIdTests(unittest.TestCase):
    def test_parse(self):
        new = ids.parse_ids(SQUARE_CONFIG)
        self.assertEqual([c.wheel for c in new], [None, 0x06])
        with self.assertRaisesRegex(ids.IdConfigError, 'grid square'):
            ids.parse_ids({'ids': [{'id': '0x66', 'template': 2, 'wheel': None}]})
        with self.assertRaisesRegex(ids.IdConfigError, 'neither'):
            ids.parse_ids({'ids': SQUARE_CONFIG['ids'][:1] + [{'id': '0x68', 'template': 2, 'wheel': '0x66'}],
                           'grid': SQUARE_CONFIG['grid']})

    def test_rows_and_roster_list(self):
        image = fresh_image()
        hs = dhs.DolHammerspace.create(image)
        state = {}
        with mock.patch.object(relocate, 'scan_refs', return_value=[]):
            ids.apply_ids(image, hs, ids.parse_ids(SQUARE_CONFIG), state)
        hs.commit()
        address, count = state['tables']['selector']
        rows = [image.read(address + 8 * i, 8) for i in range(count)]
        self.assertEqual((rows[0x66][0], rows[0x66][2], rows[0x66][6]), (0, rows[0x02][2], 1))   # no wheel group
        self.assertNotEqual(rows[0x67][0], 0)
        members = wheels.wheel_members(rows)
        self.assertNotIn(0x66, members.get(rows[0x02][2], []))
        self.assertIn(0x67, members[6])


class SlotIconTests(unittest.TestCase):
    def test_portrait(self):
        img = make_presets.slot_portrait()
        self.assertEqual(img.size, (48, 51))
        self.assertEqual(img.getpixel((24, 25))[3], 255)

    def test_same_file_one_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in make_presets.SLOT_ICON.values():
                make_presets.slot_portrait().save(os.path.join(tmp, name))
            config = {'ids': [{'id': f'0x{c:02X}', 'template': '0x06', 'icon': dict(make_presets.SLOT_ICON)}
                              for c in (0x66, 0x67)]}
            side, front = icons.compose_pages(icons.parse_icons(config, tmp))
        self.assertEqual(side.cells, [(0, 0), (0, 0)])                    # one shared cell
        self.assertEqual(front.cells, side.cells)


class PlateSharingTests(unittest.TestCase):
    def test_same_name_one_cell(self):
        data = plate_bank()
        first = len(layout2d.Layout(data).rows)
        with mock.patch.object(names, 'PLATE_TEMPLATE_PAGE', 0):
            out = names.plate_layout(data, {0x66: 'Empty slot', 0x67: 'Empty slot'}, first)
        lay = layout2d.Layout(out)
        r66, r67 = (struct.unpack('>HH4f', lay.rows[first + k]) for k in (0, 1))
        self.assertEqual(r66, r67)
        self.assertNotEqual(r66[2], 0.0)                                  # not the "-" cell


class IdRangeTests(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(ids.id_ranges([0x48, 0x47, 0x66, 0x4A, 0x49]), '0x47-0x4A, 0x66')
        self.assertEqual(ids.id_ranges([]), '')


class PresetTests(unittest.TestCase):
    def load(self, name):
        with open(os.path.join(PRESETS, name), encoding='utf-8') as f:
            return json.load(f)

    def test_presets_parse(self):
        for name in SHIPPED:                     # (not the user's own files in that folder)
            with self.subTest(preset=name):
                config = self.load(name)
                new = ids.parse_ids(config)
                wheels.parse_wheels(config)
                icons.parse_icons(config, check_files=False)
                names.parse_names(config)
                g = grid.parse_grid(config, HEADS[:41], STOCK_MAP)
                if g is not None:
                    self.assertEqual(g.empty, [])
                self.assertLessEqual(len(new), ids.MAX_ID - ids.FIRST_NEW + 1)

    def test_wheels_of_ten_and_new_wheels_of_three(self):
        new = ids.parse_ids(self.load('04_Unuseds_and_10_color_slots.json'))
        self.assertEqual(len(new), 123)
        by_wheel = {}
        for c in new:
            by_wheel.setdefault(c.wheel, []).append(c)
        self.assertEqual(len(by_wheel[0x00]), 2)                              # Mario: a new wheel of 3
        self.assertEqual(len(by_wheel[0x06]), 2)                              # Yoshi: 6 + 2 unused + 2
        self.assertEqual(sum(1 for w, g in by_wheel.items() if len(g) == 2 and w < 0x47), 31)

    def test_all_in_one(self):
        config = self.load('05_all_in_one_maximum_12x5_grid.json')
        new = ids.parse_ids(config)
        squares = [c for c in new if c.wheel is None]
        self.assertEqual(len(squares), 19)
        self.assertTrue(all(c.template == 0x04 for c in squares))                # Peach
        self.assertTrue(all(e['icon'] == make_presets.SLOT_ICON and e['name']['en'] == 'Empty slot'
                            for e in config['ids']))
        g = grid.parse_grid(config, HEADS[:41], STOCK_MAP)
        self.assertEqual((g.cols, g.rows), (12, 5))


if __name__ == '__main__':
    unittest.main()
