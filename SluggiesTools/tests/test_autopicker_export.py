"""The autopicker's character list (``Roster/autopicker_export.py``)."""

import json
import os
import tempfile
import unittest
from unittest import mock

from SluggiesTools.Roster import autopicker_export


def character(cid, en, default=None):
    return {'id': cid, 'name': {'en': en, 'fr': en, 'sp': en} if en is not None else None, 'default_name': default}


class NamesTests(unittest.TestCase):
    def test_dense_list_with_gaps(self):
        roster = {'characters': [character(0, 'Mario'), character(2, 'Peach'), character(0x66, 'Dentman')]}
        chars = autopicker_export.character_names(roster)
        self.assertEqual(len(chars), 0x67)
        self.assertEqual(chars[0], 'Mario')
        self.assertEqual(chars[1], '')
        self.assertEqual(chars[2], 'Peach')
        self.assertEqual(chars[0x4C], '')
        self.assertEqual(chars[0x66], 'Dentman')

    def test_duplicate_names_get_their_id(self):
        roster = {'characters': [character(0, 'Yoshi'), character(6, 'Yoshi'), character(1, 'Luigi')]}
        chars = autopicker_export.character_names(roster)
        self.assertEqual(chars[0], 'Yoshi (0x00)')
        self.assertEqual(chars[6], 'Yoshi (0x06)')
        self.assertEqual(chars[1], 'Luigi')

    def test_default_name_wins(self):
        roster = {'characters': [character(0x66, 'Mario', default='Spare 1')]}
        self.assertEqual(autopicker_export.character_names(roster)[0x66], 'Spare 1')

    def test_empty_roster(self):
        self.assertEqual(autopicker_export.character_names({'characters': []}), [])

    def test_write(self):
        roster = {'characters': [character(0, 'Mario'), character(1, 'Luigi')], 'names_read': True}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'characters.json')
            with mock.patch.object(autopicker_export.state_cli, '_open', return_value=(None, None)), \
                    mock.patch.object(autopicker_export.state, 'read_state', return_value=roster):
                self.assertEqual(autopicker_export.write(path, folder), ['Mario', 'Luigi'])
            with open(path, encoding='utf-8') as f:
                self.assertEqual(json.load(f), {'characters': ['Mario', 'Luigi']})


if __name__ == '__main__':
    unittest.main()
