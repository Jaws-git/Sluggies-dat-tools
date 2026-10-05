import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parents[2]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from SluggiesTools.Icons import export_icons
from SluggiesTools.Icons.tests.test_layout2d import make_bank, make_track

# The stock Mario cell: rect (1, 1) 48x51 on a 1024x256 page, stored as (v1, u1, v2, u2).
MARIO_UV = (0.00390625, 0.0009765625, 0.203125, 0.0478515625)
LUIGI_UV = (157 / 256, 540 / 1024, 208 / 256, 588 / 1024)
PAGE_SIZES = {0x00: (1024, 256), 0x02: (1024, 256), 0x4F: (1024, 256)}


def make_source_record(first, char_id, row):
    record = bytearray(0x50)
    struct.pack_into('>BBHHH', record, 0, 0 if first else 1, 0x50 // 4, char_id, 0x0400, row)
    return bytes(record)


def make_source_table(keys):
    """A source table element: info block plus one track of 0x50-byte records, highest ID first."""
    info = bytes(0x10)
    records = [make_source_record(i == 0, char_id, row)
               for i, (char_id, row) in enumerate(sorted(keys.items(), reverse=True))]
    track = make_track(records)
    header = 0x0C + 4 * 2
    length = header + len(info) + len(track)
    return struct.pack('>HHII2I', 0, 0, 2, length, header, header + len(info)) + info + track


def make_icon_bank(side, front, rows):
    return make_bank([make_source_table({0x00: 0}), make_source_table(side), make_source_table(front)], rows)


def make_model_folder(root, folder, hp_name, extra=()):
    hp = os.path.join(root, folder, hp_name)
    os.makedirs(hp)
    open(os.path.join(hp, hp_name + '.sluggie'), 'w').close()
    for name in extra:
        os.makedirs(os.path.join(root, folder, name))
    return hp


class CharacterIconCellTests(unittest.TestCase):
    def test_keys_resolve_to_page_and_rect(self):
        bank = make_icon_bank({0x00: 0, 0x01: 1}, {0x00: 2}, ((0x00, MARIO_UV), (0x02, LUIGI_UV), (0x4F, MARIO_UV)))
        cells, problems = export_icons._character_icon_cells(bank, PAGE_SIZES)
        self.assertEqual(problems, [])
        self.assertEqual(cells[0x00], {'side': (0x00, (1, 1, 48, 51)), 'front': (0x4F, (1, 1, 48, 51))})
        self.assertEqual(cells[0x01], {'side': (0x02, (540, 157, 48, 51))})

    def test_ids_without_own_key_and_mii_ids_are_absent(self):
        bank = make_icon_bank({0x00: 0, 0x4D: 0}, {0x00: 0}, ((0x00, MARIO_UV),))
        cells, _ = export_icons._character_icon_cells(bank, PAGE_SIZES)
        self.assertEqual(set(cells), {0x00})

    def test_rect_that_is_not_a_cell_is_rejected(self):
        wide = (MARIO_UV[0], MARIO_UV[1], MARIO_UV[2], MARIO_UV[3] + 4 / 1024)
        bank = make_icon_bank({0x00: 0}, {0x00: 1}, ((0x00, wide), (0x7F, MARIO_UV)))
        cells, problems = export_icons._character_icon_cells(bank, PAGE_SIZES)
        self.assertEqual(cells, {})
        self.assertEqual(len(problems), 2)
        self.assertIn('52x51', problems[0])
        self.assertIn('page 0x7F', problems[1])


class PageDecodeTests(unittest.TestCase):
    def test_reads_descriptors_and_decodes_ia8(self):
        # bank +0x20: u16 page count; descriptors from +0x24; image offsets count from +0x20
        bank = bytearray(0x80)
        struct.pack_into('>H', bank, 0x20, 2)
        struct.pack_into('>IIHH', bank, 0x24, 0x40, 0, 4, 4)
        bank[0x24 + 0x17] = 0x03                                      # IA8
        bank[0x44 + 0x17] = 0x7F                                      # unknown format: left out
        bank[0x60:0x80] = bytes((0x80, 0x10)) * 16                    # alpha 0x80, intensity 0x10
        pages = export_icons._page_descriptors(bytes(bank))
        self.assertEqual(set(pages), {0})
        image = export_icons._decode_page(bytes(bank), pages[0])
        self.assertEqual((image.mode, image.size), ('RGBA', (4, 4)))
        self.assertEqual(image.getpixel((3, 3)), (0x10, 0x10, 0x10, 0x80))


class HomeFolderTests(unittest.TestCase):
    def test_picks_the_high_poly_folder(self):
        with tempfile.TemporaryDirectory() as root:
            hp = make_model_folder(root, '18 Mario', '78277664_mario.gpl',
                                   ('78711424_L_mario.gpl', '78789408', 'anm'))
            make_model_folder(root, '180 Not Mario', '1_other.gpl')
            self.assertEqual(export_icons._character_home_folder(root, 18), (hp, None))

    def test_tolerates_a_stray_byte_after_gpl(self):
        with tempfile.TemporaryDirectory() as root:
            hp = make_model_folder(root, '30 Koopa', '127244288_nokonoko.gpl°')
            self.assertEqual(export_icons._character_home_folder(root, 30), (hp, None))

    def test_missing_or_ambiguous_folders_are_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(export_icons._character_home_folder(root, 18), (None, 'no model export'))
            os.makedirs(os.path.join(root, '18 Mario', '1_mario.gpl'))    # no .sluggie
            self.assertEqual(export_icons._character_home_folder(root, 18), (None, 'no model export'))
            make_model_folder(root, '19 Luigi', '1_luigi.gpl')
            make_model_folder(root, '19 Luigi', '2_luigi.gpl')
            self.assertEqual(export_icons._character_home_folder(root, 19),
                             (None, 'several high-poly model folders'))
        self.assertEqual(export_icons._character_home_folder(os.path.join(root, 'gone'), 18),
                         (None, 'no model export'))


class ExportCharacterIconTests(unittest.TestCase):
    def setUp(self):
        page = Image.new('RGBA', (1024, 256), (0, 0, 0, 0))
        page.paste(Image.new('RGBA', (48, 51), (255, 0, 0, 255)), (1, 1))
        self.pages = {0x00: page, 0x4F: page}

    def test_writes_rgba_pngs_into_icon_folder(self):
        with tempfile.TemporaryDirectory() as root:
            hp = make_model_folder(root, '18 Mario', '78277664_mario.gpl')
            cells = {0x00: {'side': (0x00, (1, 1, 48, 51)), 'front': (0x4F, (1, 1, 48, 51))}}
            rows, skipped = export_icons._export_character_icons(root, cells, self.pages)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['png'], '18 Mario/78277664_mario.gpl/icon/FrontIcon.png')
            for name in ('FrontIcon.png', 'SideIcon.png'):
                with Image.open(os.path.join(hp, 'icon', name)) as image:
                    self.assertEqual((image.mode, image.size), ('RGBA', (48, 51)))
                    self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
            self.assertEqual(skipped['no model export'][0], 19)

    def test_removes_icons_when_the_key_is_gone(self):
        with tempfile.TemporaryDirectory() as root:
            hp = make_model_folder(root, '89 Unused Yoshi A', '715046144_yoshi.gpl')
            cells = {0x47: {'side': (0x00, (1, 1, 48, 51)), 'front': (0x4F, (1, 1, 48, 51))}}
            export_icons._export_character_icons(root, cells, self.pages)
            self.assertTrue(os.path.isfile(os.path.join(hp, 'icon', 'SideIcon.png')))
            _, skipped = export_icons._export_character_icons(root, {}, self.pages)
            self.assertFalse(os.path.exists(os.path.join(hp, 'icon')))
            self.assertEqual(skipped['no own icon'], [89])


if __name__ == '__main__':
    unittest.main()
