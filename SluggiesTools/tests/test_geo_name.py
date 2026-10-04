import os
import sys
import unittest

TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from binfmt import clean_geo_name


class CleanGeoNameTests(unittest.TestCase):
    def test_clean_names_are_unchanged(self):
        for name in ('mario.gpl', 'L_mario.gpl', 'glove_L.tpl', 'mii_female.gpl'):
            self.assertEqual(clean_geo_name(name), name)

    def test_leftover_byte_after_the_extension_is_dropped(self):
        # Game strings without a NUL terminator (all 10 vanilla cases have one leftover byte).
        self.assertEqual(clean_geo_name('nokonoko.gpl°'), 'nokonoko.gpl')
        self.assertEqual(clean_geo_name('L_teresa.gpl\x80'), 'L_teresa.gpl')
        self.assertEqual(clean_geo_name('L_baby_mario.gpl¼'), 'L_baby_mario.gpl')
        self.assertEqual(clean_geo_name('mii_male.gplp'), 'mii_male.gpl')
        self.assertEqual(clean_geo_name('L_mii_female.gplp'), 'L_mii_female.gpl')

    def test_names_without_a_known_extension_are_unchanged(self):
        for name in ('', 'body', 'foo.bin'):
            self.assertEqual(clean_geo_name(name), name)


if __name__ == '__main__':
    unittest.main()
