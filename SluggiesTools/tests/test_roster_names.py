"""Roster expansion Phase 8: user-set names (config, text tables, DOL sites, name plates)."""

import struct
import unittest
from unittest import mock

from SluggiesTools.Dol import inventory
from SluggiesTools.Icons import layout2d
from SluggiesTools.Icons.tests.test_layout2d import make_bank, make_element
from SluggiesTools.Roster import dol_hammerspace as dhs
from SluggiesTools.Roster import names
from SluggiesTools.tests.test_roster_ids import fresh_image


def stock_table() -> bytes:
    msgs = [f'Name {i}'.encode('utf-16-be') for i in range(102)]
    msgs += [''.encode('utf-16-be'), ''.encode('utf-16-be')]
    return names.build_table(msgs)


class ParseTests(unittest.TestCase):
    def test_names_and_fallback(self):
        config = {'ids': [{'id': '0x66', 'template': 6, 'name': 'Purple Yoshi'}, {'id': '0x67', 'template': 6}],
                  'wheels': [{'id': '0x47', 'name': {'en': 'Black Yoshi', 'sp': 'Yoshi negro'}}]}
        out = names.parse_names(config)
        self.assertEqual(out[0x66], {'en': 'Purple Yoshi', 'fr': 'Purple Yoshi', 'sp': 'Purple Yoshi'})
        self.assertEqual(out[0x47], {'en': 'Black Yoshi', 'fr': 'Black Yoshi', 'sp': 'Yoshi negro'})
        self.assertNotIn(0x67, out)
        self.assertEqual(names.parse_names({'ids': [{'template': 0}]}), {})

    def test_errors(self):
        for config, message in (({'ids': [{'template': 0, 'name': 'X'}]}, 'explicit "id"'),
                                ({'ids': [{'id': '0x66', 'template': 0, 'name': {'fr': 'X'}}]}, 'at least "en"'),
                                ({'ids': [{'id': '0x66', 'template': 0, 'name': {'en': 'X', 'de': 'Y'}}]}, 'not one of'),
                                ({'ids': [{'id': '0x66', 'template': 0, 'name': ''}]}, 'non-empty'),
                                ({'wheels': [{'id': '0x10', 'name': 'X'}]}, 'not a spare row')):
            with self.subTest(config=config), self.assertRaisesRegex(names.NameConfigError, message):
                names.parse_names(config)


class TableTests(unittest.TestCase):
    def test_names_table(self):
        msgs = names.messages(names.names_table(stock_table(), {0x66: 'Purple Yoshi', 0x47: 'Black Yoshi'}))
        self.assertEqual(len(msgs), names.LAST_ID + 3)
        self.assertEqual(msgs[0x66].decode('utf-16-be'), 'Purple Yoshi')
        self.assertEqual(msgs[0x47].decode('utf-16-be'), 'Black Yoshi')
        self.assertEqual(msgs[0x67].decode('utf-16-be'), '-')
        self.assertEqual(msgs[101].decode('utf-16-be'), 'Name 101')
        self.assertEqual(msgs[names.LAST_ID + 1:], [m for m in names.messages(stock_table())[102:]])

    def test_wrong_table_refused(self):
        with self.assertRaises(names.NameConfigError):
            names.names_table(names.build_table([b'\0A'] * 10), {})


class CodeTests(unittest.TestCase):
    def test_code_reads_widgets_and_plates(self):
        image = fresh_image()
        hs = dhs.DolHammerspace.create(image)
        names.code_reads(image)
        names.range_tests(image, hs)
        sites = inventory.group('name_label_rows')
        image.write_word(sites[0].address, 0x48000010)        # the ids step's alias branch
        log = names.plate_sites(image, hs, 483)
        hs.commit()
        self.assertEqual([s for s in inventory.group('char_names') if s.tool is not None and image.u32(s.address) != s.tool], [])
        for site in names.RANGE_TESTS + tuple(s.address for s in sites):
            self.assertEqual(image.u32(site) >> 26, 18, hex(site))
        self.assertEqual(len(log), 1)


def plate_bank() -> bytes:
    """A bank with one 4x4 RGB5A3 texture (image at 0x60) and 0x196 resource rows."""
    elements = [make_element([(0, 0, 0xFF)]) for _ in range(2)]
    rows = [(0, (0.0, 0.0, 1.0, 1.0))] * (names.PLATE_ROW + 0x4D)
    lay = layout2d.Layout(make_bank(elements, resource_rows=rows))
    prefix = bytearray(lay.prefix[:0x24]) + bytes(0x80 - 0x24)
    struct.pack_into('>I', prefix, 4, 0x80)
    struct.pack_into('>H', prefix, 0x20, 1)
    struct.pack_into('>IIHH', prefix, 0x24, 0x60 - 0x20, 0, 4, 4)
    prefix[0x24 + 0x17] = names.GX_RGB5A3
    prefix[0x60:0x80] = bytes([0xAB]) * 0x20
    lay.prefix = bytes(prefix)
    return lay.to_bytes()


class PlateTests(unittest.TestCase):
    def test_plate_page_sizes(self):
        self.assertEqual(names.plate_page(['-', 'A'])[:2], (128, 32))
        width, height, at = names.plate_page(['x'] * 160)
        self.assertEqual((width, height), (512, 1024))
        self.assertEqual(at[64], (128, 0))

    def test_rgb5a3(self):
        from PIL import Image
        img = Image.new('RGBA', (4, 4), (255, 255, 255, 255))
        img.putpixel((1, 0), (0, 0, 0, 0))
        out = struct.unpack('>16H', names.rgb5a3(img))
        self.assertEqual((out[0], out[1]), (0xFFFF, 0x0000))

    def test_plate_layout(self):
        data = plate_bank()
        first_row = len(layout2d.Layout(data).rows)
        with mock.patch.object(names, 'PLATE_TEMPLATE_PAGE', 0):
            out = names.plate_layout(data, {0x66: 'Purple Yoshi', 0x47: 'Black Yoshi'}, first_row)
        lay = layout2d.Layout(out)
        self.assertEqual(struct.unpack_from('>H', out, 0x20)[0], 2)                 # a new page
        self.assertEqual(struct.unpack_from('>I', out, 0x24)[0], 0x60)                # page 0 moved by 0x20
        self.assertEqual(out[0x80:0xA0], bytes([0xAB]) * 0x20)
        self.assertEqual(len(lay.rows), first_row + names.LAST_ID + 1 - 0x66)
        page, _z, v1, u1, v2, u2 = struct.unpack('>HH4f', lay.rows[first_row])       # 0x66: its own cell
        self.assertEqual((page, v1 * 64, u2 * 128), (1, 32.0, 115.0))
        self.assertEqual(struct.unpack('>HH4f', lay.rows[first_row + 1])[2], 0.0)    # 0x67: the "-" cell
        self.assertEqual(struct.unpack('>HH4f', lay.rows[names.PLATE_ROW + 0x47])[2] * 64, 16.0)
        descriptor = out[0x24 + 0x20:0x24 + 2 * 0x20]
        image, _pal, height, width = struct.unpack_from('>IIHH', descriptor)
        self.assertEqual((width, height, descriptor[0x17]), (128, 64, names.GX_RGB5A3))
        self.assertEqual((0x20 + image) % 32, 0)
        self.assertLessEqual(0x20 + image + 128 * 64 * 2, struct.unpack_from('>I', out, 4)[0])
        with self.assertRaises(layout2d.Layout2dError):
            names.plate_layout(data, {}, first_row + 1)


class StepTests(unittest.TestCase):
    def test_no_names(self):
        from SluggiesTools.Roster import steps
        ctx = steps.RosterContext(dol=fresh_image(), dat=None, config={'ids': [{'template': 0}]})
        self.assertIn('stay as they are', names.apply(ctx)[0])


if __name__ == '__main__':
    unittest.main()
