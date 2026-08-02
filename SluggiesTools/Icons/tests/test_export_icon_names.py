import os
import tempfile
import unittest

from SluggiesTools.Icons import export_icons
from SluggiesTools.Icons import patch_icons_inplace


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


if __name__ == '__main__':
    unittest.main()