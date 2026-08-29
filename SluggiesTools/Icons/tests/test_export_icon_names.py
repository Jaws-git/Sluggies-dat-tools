import io
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from base import File
from tpl import TEXPalette, xxh64

from SluggiesTools.Icons import export_icons
from SluggiesTools.Icons import patch_icons_inplace


def _make_icon_tpl(image_data, tlut_data):
    """Build an in-memory TPL holding one C8 1024x256 icon descriptor."""
    data_ptr = 0x24
    palette_ptr = data_ptr + len(image_data)
    header = struct.pack('>HH', 1, 0)
    descriptor = struct.pack(
        '>IIHHBBBBI3sBHB1sI',
        data_ptr,
        palette_ptr,
        256,    # height
        1024,   # width
        0,      # edgeLODEnable
        0,      # minLOD
        0,      # maxLOD
        0,      # unpacked
        0,      # unknown word
        b'\x00\x00\x00',
        0x09,   # format: C8
        256,    # paletteEntries
        0x00,   # paletteFormat: IA8
        b'\x00',
        0,
    )
    blob = header + descriptor + image_data + tlut_data
    root = File(io.BytesIO(blob))
    tex_palette = root.add_child(0, len(blob), TEXPalette, 'icon')
    tex_palette.analyze()
    return tex_palette


class ExportIconNameTests(unittest.TestCase):
    def test_all_stock_side_pages_have_character_names(self):
        names = [
            export_icons._page_base_name('side', page_index)
            for page_index in range(0x4F)
        ]

        self.assertEqual(len(export_icons.SIDE_DIR_NAMES), 0x4F)
        self.assertTrue(all(name.count('_') >= 4 for name in names))

    def test_duplicate_side_palettes_use_the_same_character_name(self):
        self.assertTrue(export_icons._page_base_name('side', 0x01).endswith('_Luigi'))
        self.assertTrue(export_icons._page_base_name('side', 0x02).endswith('_Luigi'))
        self.assertTrue(export_icons._page_base_name('side', 0x42).endswith('_KingKRool'))
        self.assertTrue(export_icons._page_base_name('side', 0x43).endswith('_KingKRool'))

    def test_side_name_boundaries_and_front_names(self):
        self.assertEqual(
            export_icons._page_base_name('side', 0x00),
            'side_page_00_t000_Mario',
        )
        self.assertEqual(
            export_icons._page_base_name('side', 0x4E),
            'side_page_4E_t078_PinkYoshi',
        )
        self.assertEqual(
            export_icons._page_base_name('front', 0x4F),
            'front_page_4F_t079_Mario',
        )

    def test_reimport_prefers_exact_named_act_over_legacy_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = os.path.join(temp_dir, 'side_page_00_t000.act')
            named_path = os.path.join(temp_dir, 'side_page_00_t000_Mario.act')
            open(legacy_path, 'wb').close()
            open(named_path, 'wb').close()

            selected = patch_icons_inplace._find_act_file(
                temp_dir,
                'side',
                0,
                'sheets (EDIT BASE.PNG)/side/side_page_00_t000_Mario.png',
            )

            self.assertEqual(selected, named_path)


class DolphinIconNameTests(unittest.TestCase):
    def _entry(self, view, texture_index, base_name, character_name, dolphin_name):
        return {
            'view': view,
            'texture_index_dec': texture_index,
            'base_name': base_name,
            'character_name': character_name,
            'dolphin_name': dolphin_name,
        }

    def test_dolphin_name_matches_tpl_hashing_for_c8_icon(self):
        image_data = bytes(range(256)) * 4096   # 0x40000 bytes, all palette indices
        tlut_data = bytes((i & 0xFF) for i in range(512))  # 512 bytes, indices 0..255 used

        tex_palette = _make_icon_tpl(image_data, tlut_data)
        desc = tex_palette.descriptors[0]
        image_data, tlut_data = desc._read_payload()

        name = export_icons._dolphin_texture_name(desc, image_data, tlut_data)

        self.assertTrue(name.endswith('.png'))
        self.assertTrue(name.startswith(f'tex1_{desc.width}x{desc.height}_'))
        self.assertTrue(name.endswith(f'_{desc.format}.png'))

        expected_tex_hash = format(xxh64(image_data, 0), '016x')
        expected_tlut_hash = format(xxh64(tlut_data, 0), '016x')
        self.assertEqual(
            name,
            f'tex1_{desc.width}x{desc.height}_{expected_tex_hash}_{expected_tlut_hash}_{desc.format}.png',
        )
        # C8 uses all 256 indices here, so the TLUT is not trimmed.
        self.assertEqual(
            desc.dolphinTextureBasenameForPayload(image_data, tlut_data) + '.png',
            name,
        )

    def test_dolphin_name_uses_trimmed_tlut_for_sparse_indices(self):
        image_data = bytes([0, 3, 0, 3]) * (export_icons.EXPECTED_IMAGE_LEN // 4)
        tlut_data = bytes((i & 0xFF) for i in range(512))

        tex_palette = _make_icon_tpl(image_data, tlut_data)
        desc = tex_palette.descriptors[0]
        image_data, tlut_data = desc._read_payload()

        name = export_icons._dolphin_texture_name(desc, image_data, tlut_data)

        trimmed = tlut_data[0:2 * 4]   # only indices 0..3 are referenced
        self.assertEqual(
            name,
            f'tex1_{desc.width}x{desc.height}_'
            f'{format(xxh64(image_data, 0), "016x")}'
            f'_{format(xxh64(trimmed, 0), "016x")}'
            f'_{desc.format}.png',
        )

    def test_character_name_lookup(self):
        self.assertEqual(export_icons._page_character_name('side', 0x00), 'Mario')
        self.assertEqual(export_icons._page_character_name('front', 0x4F), 'Mario')
        self.assertEqual(export_icons._page_character_name('front', 0x50), 'Luigi')
        self.assertEqual(export_icons._page_character_name('front', 0x8B), 'YoshiRed')
        self.assertEqual(export_icons._page_character_name('side', 0x4E), 'PinkYoshi')
        # Out-of-range page indices have no known character.
        self.assertEqual(export_icons._page_character_name('front', 0x8C), '')
        self.assertEqual(export_icons._page_character_name('side', 0x4F), '')

    def test_write_dolphin_names_txt_is_sorted_by_character(self):
        entries = [
            self._entry('front', 0x50, 'front_page_50_t080_Luigi', 'Luigi', 'tex1_1024x256_aaaa_9999_9.png'),
            self._entry('side', 0x00, 'side_page_00_t000_Mario', 'Mario', 'tex1_1024x256_bbbb_8888_9.png'),
            self._entry('side', 0x4E, 'side_page_4E_t078_PinkYoshi', 'PinkYoshi', 'tex1_1024x256_cccc_7777_9.png'),
            self._entry('front', 0x8B, 'front_page_8B_t139_YoshiRed', 'YoshiRed', 'tex1_1024x256_dddd_6666_9.png'),
            self._entry('front', 0x50, 'front_page_50_t080', '', 'tex1_1024x256_eeee_5555_9.png'),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, 'dolphin_icon_names.txt')
            export_icons._write_dolphin_names_txt(path, entries)

            with open(path, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()

        self.assertEqual(
            lines,
            [
                'front_page_50_t080 -> tex1_1024x256_eeee_5555_9.png',
                'front_page_50_t080_Luigi -> tex1_1024x256_aaaa_9999_9.png',
                'side_page_00_t000_Mario -> tex1_1024x256_bbbb_8888_9.png',
                'side_page_4E_t078_PinkYoshi -> tex1_1024x256_cccc_7777_9.png',
                'front_page_8B_t139_YoshiRed -> tex1_1024x256_dddd_6666_9.png',
            ],
        )


if __name__ == '__main__':
    unittest.main()