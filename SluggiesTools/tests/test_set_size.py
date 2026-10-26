"""The character grid's Set size...: the roster state's size scales (``Roster/state_cli.read_sizes``) and the
one-character stat edit file it stages (``gui_grid.size_doc``), checked by ``StatEditor/apply.py`` as the chain does.

Synthetic DOLs as in ``test_stat_editor_carry``, with real size scales written into the vanilla rows."""

import hashlib
import os
import tempfile
import unittest

from SluggiesTools import gui_grid
from SluggiesTools.Roster import state_cli
from SluggiesTools.StatEditor import apply, fields
from SluggiesTools.tests.test_stat_editor_carry import Editor, copy, vanilla

GAMEPLAY = fields.character_field('size', 'Gameplay')
SELECT = fields.character_field('size', 'Select Screens')


class SetSizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.vanilla = vanilla()
        editor = Editor(self.vanilla)
        for cid in range(0x10):
            editor.set(cid, GAMEPLAY, 1.18)
            editor.set(cid, SELECT, 1.1)
        self.input_dir = os.path.join(self.tmp, '1_Input')
        os.makedirs(self.input_dir)
        with open(os.path.join(self.input_dir, 'main.dol'), 'wb') as f:
            f.write(self.vanilla.to_bytes())
        self.image = copy(self.vanilla)

    def state(self, image=None) -> dict:
        return {'characters': [{'id': 0x01, 'name': {'en': 'Luigi'}}],
                'sizes': state_cli.read_sizes(image or self.image, self.input_dir)}

    def test_read_sizes_gives_live_and_roster_values(self):
        Editor(self.image).set(0x01, GAMEPLAY, 1.5)
        sizes = state_cli.read_sizes(self.image, self.input_dir)
        self.assertEqual(sizes['main_dol_sha1'], hashlib.sha1(self.image.data).hexdigest())
        self.assertEqual(sizes['characters']['0x01'],
                         {'gameplay': 1.5, 'select': 1.1, 'base_gameplay': 1.18, 'base_select': 1.1})
        self.assertAlmostEqual(gui_grid.size_factor(sizes['characters']['0x01']), 1.5 / 1.18)

    def test_read_sizes_without_input_dol(self):
        self.assertIsNone(state_cli.read_sizes(self.image, os.path.join(self.tmp, 'nowhere')))

    def test_size_doc_applies_the_factor_to_both_scales(self):
        doc = gui_grid.size_doc(self.state(), 0x01, 1.5)
        prepared = apply.prepare(self.image, [doc])
        self.assertEqual({(c.who, c.key): c.raw for c in prepared.changes},
                         {(0x01, 'Gameplay'): GAMEPLAY.pack(1.77), (0x01, 'Select Screens'): SELECT.pack(1.65)})
        apply.write(self.image, prepared)
        after = state_cli.read_sizes(self.image, self.input_dir)['characters']['0x01']
        self.assertAlmostEqual(gui_grid.size_factor(after), 1.5, places=5)
        self.assertEqual(gui_grid.size_text(self.state(), 0x01),
                         'Size: 1.5x its normal size (scale 1.77 in game, 1.65 on the select screens)')

    def test_normal_size_resets_an_edited_size(self):
        Editor(self.image).set(0x01, GAMEPLAY, 2.0)
        doc = gui_grid.size_doc(self.state(), 0x01, 1)
        apply.write(self.image, apply.prepare(self.image, [doc]))
        self.assertEqual(gui_grid.size_text(self.state(), 0x01),
                         'Size: normal (scale 1.18 in game, 1.1 on the select screens)')

    def test_size_doc_is_refused_on_another_main_dol(self):
        doc = gui_grid.size_doc(self.state(), 0x01, 1.5)
        Editor(self.image).set(0x02, GAMEPLAY, 2.0)                # patched after the state was read
        with self.assertRaisesRegex(apply.EditFileError, 'another state of main.dol'):
            apply.prepare(self.image, [doc])

    def test_parse_size(self):
        self.assertEqual(gui_grid.parse_size(' 1.25 '), (1.25, None))
        self.assertEqual(gui_grid.parse_size('0,8x'), (0.8, None))
        self.assertIn('between', gui_grid.parse_size('9')[1])
        self.assertIn('between', gui_grid.parse_size('0')[1])
        self.assertIn('number', gui_grid.parse_size('big')[1])

    def test_size_file_marks_the_pending_edit(self):
        state = self.state()
        path = gui_grid.write_size_file(self.tmp, gui_grid.size_doc(state, 0x01, 1.3), now=0)
        self.assertTrue(os.path.basename(path).startswith('stat_edits_'))     # the Stat Editor tab's pruning
        self.assertEqual(gui_grid.size_file(path), (0x01, 1.3))
        self.assertNotEqual(gui_grid.write_size_file(self.tmp, gui_grid.size_doc(state, 0x01, 1.3), now=0), path)
        pending = gui_grid.PendingEdits()
        pending.edits = [gui_grid.stat_edit(path)]
        self.assertEqual(gui_grid.pending_size(pending, 0x01), 1.3)
        self.assertIsNone(gui_grid.pending_size(pending, 0x02))
        self.assertEqual(gui_grid._edit_title(pending.edits[0]), 'Pending: size 1.3x')
        dialog = gui_grid.stat_edits_dialog(state, {'refused': [], 'edits': [], 'skipped': []}, 0, '', path)
        self.assertEqual(dialog.title, 'Size: nothing to change')
        self.assertIn('has that size already', dialog.lines[-1][0])

    def test_other_stat_edit_files_are_not_size_files(self):
        path = os.path.join(self.tmp, 'stat_edits_x.json')
        with open(path, 'w') as f:
            f.write('{"format": "sluggies-stat-edits", "characters": {"0x01": {}}}')
        self.assertIsNone(gui_grid.size_file(path))
        self.assertEqual(gui_grid._edit_title(gui_grid.stat_edit(path)), 'Pending: stat editor value changes')


if __name__ == '__main__':
    unittest.main()
