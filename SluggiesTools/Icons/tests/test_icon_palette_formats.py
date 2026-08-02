import os
import tempfile
import unittest
from types import SimpleNamespace

from SluggiesTools.Icons import export_icons
from SluggiesTools.Icons import patch_icons_inplace


class IconPaletteFormatTests(unittest.TestCase):
    def _descriptor(self, palette_format):
        return SimpleNamespace(
            width=1024,
            height=256,
            format=0x09,
            paletteFormat=palette_format,
            paletteEntries=256,
            paletteDataPtr=0x100,
        )

    def test_descriptor_discovery_accepts_stock_ia8_and_rgb5a3(self):
        self.assertTrue(export_icons._is_expected_icon_descriptor(self._descriptor(0x00)))
        self.assertTrue(export_icons._is_expected_icon_descriptor(self._descriptor(0x02)))
        self.assertFalse(export_icons._is_expected_icon_descriptor(self._descriptor(0x01)))

    def test_ia8_act_export_uses_intensity_as_grayscale(self):
        palette = b''.join(bytes((value, 255 - value)) for value in range(256))

        rgb = export_icons._ia8_to_rgb8(palette)

        self.assertEqual(rgb[:6], bytes((0, 0, 0, 1, 1, 1)))
        self.assertEqual(rgb[-3:], bytes((255, 255, 255)))

    def test_ia8_act_reimport_round_trips_intensity_and_alpha(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_rel = 'palette.bin'
            raw = b''.join(bytes((value, 255 - value)) for value in range(256))
            with open(os.path.join(temp_dir, raw_rel), 'wb') as output:
                output.write(raw)
            act = b''.join(bytes((value, value, value)) for value in range(256))

            converted = patch_icons_inplace._act_to_ia8_preserving_alpha(
                temp_dir, raw_rel, act
            )

            self.assertEqual(converted, raw)


if __name__ == '__main__':
    unittest.main()