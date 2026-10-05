import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import native_dialog


class EnabledTests(unittest.TestCase):
    def test_windows_only(self):
        self.assertTrue(native_dialog.enabled(env={}, system='Windows'))
        self.assertFalse(native_dialog.enabled(env={}, system='Linux'))

    def test_switch_off(self):
        for value in ('0', 'false', 'off', 'no', ' 0 '):
            self.assertFalse(native_dialog.enabled(env={'SLUGGIES_NATIVE_DIALOG': value}, system='Windows'))
        self.assertTrue(native_dialog.enabled(env={'SLUGGIES_NATIVE_DIALOG': '1'}, system='Windows'))


class FilterStringTests(unittest.TestCase):
    def test_pairs_and_terminator(self):
        self.assertEqual(native_dialog.filter_string([('Sluggie files', '*.sluggie'), ('All files', '*.*')]),
                         'Sluggie files (*.sluggie)\0*.sluggie\0All files (*.*)\0*.*\0\0')


class ParseSelectionTests(unittest.TestCase):
    def test_single_file(self):
        self.assertEqual(native_dialog.parse_selection('C:\\m\\10 Yoshi\\a.sluggie\0\0junk'),
                         ['C:\\m\\10 Yoshi\\a.sluggie'])

    def test_multi_select(self):
        self.assertEqual(native_dialog.parse_selection('C:\\m\0a.sluggie\0b.png\0\0junk'),
                         [os.path.join('C:\\m', 'a.sluggie'), os.path.join('C:\\m', 'b.png')])

    def test_empty(self):
        self.assertEqual(native_dialog.parse_selection('\0\0'), [])


if __name__ == '__main__':
    unittest.main()
