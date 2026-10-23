"""Item models: the planner's game-wide ``item`` op (``Roster/slot_plan``) and the grid tab's item tile
(``gui_grid``). Item ``.sluggie`` files are written here with ``binfmt.dump_sluggie`` (no exported data)."""

import os
import tempfile
import unittest

from SluggiesTools import gui_grid
from SluggiesTools.binfmt import dump_sluggie
from SluggiesTools.Roster import slot_plan
from SluggiesTools.tests.test_roster_voice_stats_reassign import NAMES, STATE_FILE, make_config, make_state


def write_model(folder: str, name: str, chunk: int | None, file: int = 0, offset: str | None = '0x263632c0') -> str:
    path = os.path.join(folder, name)
    model = {'FileIndex': file}
    if chunk is not None:
        model['ChunkNumber'] = chunk
    if offset is not None:
        model['ModelOffset'] = offset
    dump_sluggie({'SluggiesModel': model}, path)
    return path


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.banana = write_model(self.dir, '641086144_banana.gpl.sluggie', 136)
        self.ball = write_model(self.dir, '639456960_ball.gpl.sluggie', 136, offset='0x26196200')

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, items, env=None):
        return slot_plan.plan_batch(make_state(), make_config(), slot_plan.parse_edits(items),
                                    env or slot_plan.Env(), STATE_FILE, NAMES)

    def test_plain_patch_before_the_re_read(self):
        b = self.plan([{'op': 'item', 'file': self.banana}, {'op': 'item', 'file': self.ball}])
        self.assertTrue(b.ok, b.refused)
        self.assertEqual(b.commands, [('--patch', self.banana, '--validate-only'),   # build checks first
                                      ('--patch', self.ball, '--validate-only'),
                                      ('--patch', self.banana), ('--patch', self.ball), ('--roster-state',)])
        self.assertIsNone(b.config)                    # no roster rebuild
        sections = b.to_json()['edits']
        self.assertEqual([(s['action'], s['target']) for s in sections], [('item', '0xFF')] * 2)
        self.assertEqual(sections[0]['effects'], {'item': '641086144_banana.gpl.sluggie'})
        merged = b.to_json()['merged']
        self.assertEqual(merged[0], {'op': 'item', 'id': '0xFF', 'file': self.banana})

    def test_with_the_slot_writes_after_the_rebuild(self):
        b = self.plan([{'op': 'item', 'file': self.banana}, {'op': 'stats', 'id': '0x0D', 'source': '0x09'}])
        self.assertTrue(b.ok, b.refused)
        self.assertEqual([c[0] for c in b.commands], ['--patch', '--roster', '--patch', '--roster-state'])
        self.assertEqual(b.commands[0], ('--patch', self.banana, '--validate-only'))

    def test_checked_item_skips_its_build_check(self):
        class NoSizes(slot_plan.Env):
            def build_size_known(self, path):
                return False                           # item checks record no size: never needed for them
        edits = slot_plan.parse_edits([{'op': 'item', 'file': self.banana, 'checked': True},
                                       {'op': 'item', 'file': self.ball}])
        b = slot_plan.plan_batch(make_state(), make_config(), edits, NoSizes(), STATE_FILE, NAMES, skip_checked=True)
        self.assertEqual([c for c in b.commands if '--validate-only' in c], [('--patch', self.ball, '--validate-only')])

    def test_later_file_of_the_same_model_wins(self):
        again = write_model(self.dir, 'edited_banana.gpl.sluggie', 136)
        b = self.plan([{'op': 'item', 'file': self.banana}, {'op': 'item', 'file': self.ball},
                       {'op': 'item', 'file': again}])
        self.assertEqual(b.commands[2:4], [('--patch', self.ball), ('--patch', again)])
        self.assertTrue(any('replaced by the later edited_banana' in n for n in b.notes), b.notes)

    def test_refusals(self):
        mario = write_model(self.dir, 'mario.gpl.sluggie', 18)
        nameless = write_model(self.dir, 'old.gpl.sluggie', None)
        for path, why in ((mario, 'character directory'), (nameless, 'ChunkNumber'),
                          (os.path.join(self.dir, 'missing.sluggie'), 'not a .sluggie file')):
            b = self.plan([{'op': 'item', 'file': self.banana}, {'op': 'item', 'file': path}])
            self.assertFalse(b.ok)
            self.assertEqual(b.commands, [])
            self.assertIn(why, b.refused[0][1])
        with self.assertRaises(slot_plan.PlanError):
            slot_plan.parse_edits([{'op': 'item'}])

    def test_stadium_models(self):
        bowser = write_model(self.dir, '123_castle.gpl.sluggie', 13)
        again = write_model(self.dir, 'edited_castle.gpl.sluggie', 13)
        b = self.plan([{'op': 'stadium', 'file': bowser}, {'op': 'item', 'file': self.banana},
                       {'op': 'stadium', 'file': again}])
        self.assertTrue(b.ok, b.refused)
        self.assertEqual(b.commands, [('--patch', self.banana, '--validate-only'), ('--patch', again, '--validate-only'),
                                      ('--patch', self.banana), ('--patch', again), ('--roster-state',)])
        self.assertTrue(any(n.startswith('stadium: the pending 123_castle') for n in b.notes), b.notes)
        sections = b.to_json()['edits']
        self.assertEqual([(s['action'], s['target']) for s in sections], [('item', '0xFF'), ('stadium', '0xFF')])
        self.assertEqual(sections[1]['effects'], {'stadium': 'edited_castle.gpl.sluggie'})

    def test_items_and_stadiums_refuse_each_other(self):
        stadium = write_model(self.dir, 'castle.gpl.sluggie', 7)
        for op, path, why in (('item', stadium, 'Stadiums square'), ('stadium', self.banana, 'Items square'),
                              ('stadium', write_model(self.dir, 'mario.gpl.sluggie', 18), 'character directory')):
            b = self.plan([{'op': op, 'file': path}])
            self.assertFalse(b.ok)
            self.assertIn(why, b.refused[0][1])

    def test_constants(self):
        self.assertEqual(gui_grid.ITEM, slot_plan.ITEM)
        self.assertEqual(gui_grid.STADIUM, slot_plan.STADIUM)
        self.assertIn(slot_plan.ITEM, slot_plan.GAME_OPS)
        self.assertIn(slot_plan.STADIUM, slot_plan.GAME_OPS)
        self.assertEqual(gui_grid.GAME_WIDE, slot_plan.GAME_WIDE)


class GuiTests(unittest.TestCase):
    PLAN = {'refused': [], 'notes': ['item: the pending a.sluggie is replaced by the later b.sluggie (the same model)'],
            'edits': [{'action': 'item', 'target': '0xFF', 'notes': ['b.sluggie is patched'], 'warnings': [],
                       'effects': {'item': 'b.sluggie'}, 'edit': {'op': 'item', 'id': '0xFF', 'file': '/x/b.sluggie'}}],
            'merged': [{'op': 'item', 'id': '0xFF', 'file': '/x/b.sluggie'}], 'skipped': [], 'warnings': [],
            'rebuild': False}

    def test_pending_lines(self):
        pending = gui_grid.PendingEdits()
        pending.accept(self.PLAN)
        self.assertEqual(gui_grid.staged_item_files(pending), ['/x/b.sluggie'])
        self.assertEqual(pending.titles(), [(None, 'item model b.sluggie')])
        self.assertEqual(gui_grid.item_tooltip(pending), ['1 pending item model:', '  b.sluggie'])
        self.assertEqual(pending.gecko, set())
        dialog = gui_grid.items_dialog(pending)
        self.assertTrue(dialog.can_apply and dialog.menu)
        self.assertFalse(gui_grid.dialog_skippable(dialog))
        pending.discard_op(gui_grid.ITEM)
        self.assertEqual(len(pending), 0)

    def test_item_dialog(self):
        dialog = gui_grid.item_dialog({}, self.PLAN, 0, '', ['/x/b.sluggie'])
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.lines[0], ('Item model: b.sluggie', gui_grid.OK))
        self.assertIn(('- ' + self.PLAN['notes'][0], gui_grid.TEXT), dialog.lines)
        refused = dict(self.PLAN, refused=[{'target': '0xFF', 'edit': {'op': 'item'}, 'error': 'from a character'}])
        dialog = gui_grid.item_dialog({}, refused, 1, '', ['/x/b.sluggie'])
        self.assertFalse(dialog.can_apply)
        self.assertEqual(dialog.lines[0], ('Refused: from a character', gui_grid.ERROR))
        self.assertFalse(gui_grid.item_dialog({}, self.PLAN, 0, '', ['/x/b.sluggie', '/x/c.sluggie']).can_apply)

    def test_summary(self):
        dialog = gui_grid.summary_dialog({}, dict(self.PLAN, commands=[]), 0, '')
        self.assertIn(('Item models: patch b.sluggie', gui_grid.OK), dialog.lines)
        self.assertIn((gui_grid.ITEM_PATCH_NOTE, gui_grid.TEXT), dialog.lines)

    def test_stadium_tile(self):
        plan = {'refused': [], 'notes': [], 'skipped': [], 'warnings': [], 'rebuild': False,
                'edits': [{'action': 'stadium', 'target': '0xFF', 'notes': [], 'warnings': [],
                           'effects': {'stadium': 'c.sluggie'},
                           'edit': {'op': 'stadium', 'id': '0xFF', 'file': '/x/c.sluggie'}}],
                'merged': [{'op': 'stadium', 'id': '0xFF', 'file': '/x/c.sluggie'}]}
        pending = gui_grid.PendingEdits()
        pending.accept(dict(plan, merged=self.PLAN['merged'] + plan['merged']))
        self.assertEqual(gui_grid.staged_item_files(pending), ['/x/b.sluggie'])
        self.assertEqual(gui_grid.staged_item_files(pending, gui_grid.STADIUM), ['/x/c.sluggie'])
        self.assertEqual(pending.titles()[-1], (None, 'stadium model c.sluggie'))
        self.assertEqual(gui_grid.item_tooltip(pending, gui_grid.STADIUM), ['1 pending stadium model:', '  c.sluggie'])
        self.assertEqual(gui_grid.items_dialog(pending, gui_grid.STADIUM).title, 'Stadium models')
        dialog = gui_grid.item_dialog({}, plan, 0, '', ['/x/c.sluggie'], gui_grid.STADIUM)
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.lines[0], ('Stadium model: c.sluggie', gui_grid.OK))
        summary = gui_grid.summary_dialog({}, dict(plan, commands=[]), 0, '')
        self.assertIn(('Stadium models: patch c.sluggie', gui_grid.OK), summary.lines)
        self.assertIn((gui_grid.STADIUM_PATCH_NOTE, gui_grid.TEXT), summary.lines)
        pending.discard_op(gui_grid.STADIUM)
        self.assertEqual(gui_grid.staged_item_files(pending), ['/x/b.sluggie'])

    def test_banana_icon(self):
        image = gui_grid.banana_icon((72, 76))
        self.assertEqual((image.size, image.mode), ((72, 76), 'RGBA'))
        alpha = image.getchannel('A')
        self.assertEqual(alpha.getpixel((0, 0)), 0)               # transparent around it
        self.assertGreater(sum(1 for a in alpha.tobytes() if a > 200), 500)

    def test_flag_icon(self):
        image = gui_grid.flag_icon((72, 76))
        self.assertEqual((image.size, image.mode), ((72, 76), 'RGBA'))
        alpha = image.getchannel('A')
        self.assertEqual(alpha.getpixel((0, 0)), 0)
        self.assertGreater(sum(1 for a in alpha.tobytes() if a > 200), 500)


if __name__ == '__main__':
    unittest.main()
