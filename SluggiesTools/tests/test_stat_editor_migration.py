"""Stat edits on roster changes: what the user sees (the plan texts, ``stat_reset``, the pack and export warnings).

The carrying itself is ``test_stat_editor_carry``; this covers the pieces on
top of it: ``carry.by_character`` / ``summary`` / ``reset_spots``, ``apply``'s
``Reset`` steps (``reset:0xNN``), the planner's ``stat_edits_kept`` notes and
``stat_reset`` op, the pack load's notes and the GUI texts. Synthetic DOLs as
in ``test_stat_editor_carry``.
"""

import os
import tempfile
import unittest

from SluggiesTools import gui_grid
from SluggiesTools.Dol import dolfile
from SluggiesTools.Roster import pack, slot_plan
from SluggiesTools.StatEditor import apply, bridge, carry, cli
from SluggiesTools.tests.test_stat_editor_apply import BATTING, SPEED, StatEnv, doc
from SluggiesTools.tests.test_stat_editor_carry import EXPANDED, Editor, copy, vanilla
from SluggiesTools.tests.test_roster_voice_stats_reassign import STATE_FILE, NAMES, make_config, make_state
from SluggiesTools.tests.test_roster_voice_stats_reassign import build as build_roster


def edited() -> dolfile.DolImage:
    """The expanded roster with stat edits on 0x05 (a field, stock x stock chemistry both ways, stock x new) and on
    0x66 (a field, new x new)."""
    image = build_roster(EXPANDED)[0]
    ed = Editor(image)
    ed.set(0x05, BATTING, 40)
    ed.set(0x66, SPEED, 0x0203)
    matrix = bridge.new_by_new_address(image, ed.layouts['stats'])
    for a, b in ((0x05, 0x01), (0x02, 0x05), (0x05, 0x66), (0x66, 0x67)):
        now = image.read(carry.chem_address(a, b, ed.layouts, matrix), 1)[0]
        ed.chem(a, b, (now + 1) % 3)                             # any other storable value
    return image


class SummaryTests(unittest.TestCase):
    def test_by_character_and_summary(self):
        edits = carry.detect(edited(), vanilla())
        per = carry.by_character(edits)
        self.assertEqual(sorted(per), [0x01, 0x02, 0x05, 0x66, 0x67])
        self.assertEqual(per[0x05], carry.CharacterEdits(('stats.batting arm',), 3))
        self.assertEqual(per[0x66].fields, ('stats.speed',))
        self.assertEqual(per[0x66].chemistry, 2)                 # stock x new and new x new
        self.assertEqual(per[0x05].text(), '1 field (batting arm), 3 chemistry values')
        summary = carry.summary(edits)
        self.assertEqual(summary['characters']['0x05'], {'fields': ['stats.batting arm'], 'chemistry': 3})
        self.assertEqual(summary['globals'], 0)
        self.assertEqual(gui_grid.stat_edits_text(summary['characters']['0x05']), per[0x05].text())

    def test_text_shortens_long_field_lists(self):
        e = carry.CharacterEdits(tuple(f'stats.f{n}' for n in range(6)), 0)
        self.assertEqual(e.text(), '6 fields (f0, f1, f2, f3, +2 more)')
        self.assertEqual(gui_grid.stat_edits_text({'fields': list(e.fields), 'chemistry': 0}), e.text())


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.image = edited()

    def prepared(self, *documents, image=None):
        return apply.prepare(copy(image or self.image), list(documents), vanilla=vanilla())

    def written(self, *documents) -> dolfile.DolImage:
        out = copy(self.image)
        apply.write(out, apply.prepare(out, list(documents), vanilla=vanilla()))
        return out

    def test_reset_clears_one_character(self):
        out = self.written(apply.Reset(0x05))
        after = carry.by_character(carry.detect(out, vanilla()))
        self.assertNotIn(0x05, after)
        self.assertEqual(after[0x66], carry.CharacterEdits(('stats.speed',), 1))   # new x new stays
        self.assertNotIn(0x01, after)                                              # its pairs went with it
        prepared = self.prepared(apply.Reset(0x05))
        self.assertEqual((len(prepared.changes), prepared.same), (4, 0))           # unedited spots are not counted
        self.assertEqual(prepared.characters, [0x05])

    def test_order_matters(self):
        image = self.image
        set_after = doc(image, {'0x05': {'stats': {'batting arm': 50, 'speed': 9}}})
        out = self.written(apply.Reset(0x05), set_after)            # a later file sets values on top of the reset
        self.assertEqual(carry.detect(out, vanilla()).rows[0x05], {BATTING: bytes([50]), SPEED: (9).to_bytes(2, 'big')})
        out = self.written(set_after, apply.Reset(0x05))            # a later reset clears what the file set
        self.assertNotIn(0x05, carry.detect(out, vanilla()).rows)

    def test_refusals(self):
        with self.assertRaisesRegex(apply.EditFileError, '1_Input'):
            apply.prepare(copy(self.image), [apply.Reset(0x05)])
        with self.assertRaisesRegex(apply.EditFileError, 'not a character of this roster'):
            self.prepared(apply.Reset(0x70))
        self.assertEqual(apply.parse_item('reset:0x05'), apply.Reset(0x05))
        self.assertEqual(apply.parse_item('C:/a/reset.json'), 'C:/a/reset.json')
        self.assertEqual(apply.Reset(0x66).item, 'reset:0x66')
        with self.assertRaises(apply.EditFileError):
            apply.parse_item('reset:Mario')

    def test_nothing_to_clear(self):
        prepared = self.prepared(apply.Reset(0x07))
        self.assertEqual((prepared.changes, prepared.same), ([], 0))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir, in_dir = os.path.join(tmp, '3_Output_Dat'), os.path.join(tmp, '1_Input')
            for folder, image in ((out_dir, self.image), (in_dir, vanilla())):
                os.makedirs(folder)
                with open(os.path.join(folder, 'main.dol'), 'wb') as f:
                    f.write(image.to_bytes())
            args = ['--apply', 'reset:0x05', '--output-dir', out_dir, '--input-dir', in_dir]
            self.assertEqual(cli.main(args + ['--dry-run']), 0)
            self.assertEqual(cli.main(args), 0)
            with open(os.path.join(out_dir, 'main.dol'), 'rb') as f:
                out = dolfile.DolImage(f.read())
            self.assertNotIn(0x05, carry.by_character(carry.detect(out, vanilla())))

    def test_dispatcher_keeps_reset_items(self):
        from unittest import mock
        import start
        with mock.patch('start.subprocess.run', return_value=mock.Mock(returncode=0)) as run:
            start.run_apply_stat_edits(['a.json', 'reset:0x05'])
        self.assertEqual(run.call_args.args[0][-3:], ['--apply', os.path.abspath('a.json'), 'reset:0x05'])


class EditedEnv(StatEnv):
    def __init__(self, checks: dict, edited_ids: dict):
        super().__init__(checks)
        self.edited_ids = edited_ids

    def stat_edited(self, cid):
        return self.edited_ids.get(cid)


class PlanTests(unittest.TestCase):
    CHANGED = slot_plan.StatCheck('3 values on 1 character', ['0x0D: stats: batting arm 1 -> 2'])

    def plan(self, items, checks=None, edited_ids=None):
        return slot_plan.plan_batch(make_state(), make_config(), slot_plan.parse_edits(items),
                                    EditedEnv(checks or {}, edited_ids or {}), STATE_FILE, NAMES)

    def test_a_stats_change_keeps_stat_edits(self):
        b = self.plan([{'op': 'stats', 'id': '0x0D', 'source': '0x09'}], edited_ids={0x0D: '1 field (speed)'})
        section = b.to_json()['edits'][0]
        self.assertEqual(section['stat_edits_kept'], '1 field (speed)')
        self.assertTrue(any('keeps its stat edits (1 field (speed)) on top of the new stats' in n
                            for n in section['notes']), section['notes'])
        b = self.plan([{'op': 'stats', 'id': '0x0D', 'source': '0x09'}])
        self.assertNotIn('stat_edits_kept', b.to_json()['edits'][0])

    def test_stat_reset_goes_with_the_stat_files_in_staging_order(self):
        b = self.plan([{'op': 'stat_edits', 'file': 'a.json'}, {'op': 'stats', 'id': '0x0D', 'source': '0x09'},
                       {'op': 'stat_reset', 'id': '0x0D'}, {'op': 'stat_edits', 'file': 'b.json'}],
                      {'a.json': self.CHANGED, 'b.json': self.CHANGED, 'reset:0x0D': self.CHANGED})
        self.assertTrue(b.ok, b.refused)
        self.assertEqual(b.commands[0], ('--apply-stat-edits', 'a.json', 'reset:0x0D', 'b.json'))
        self.assertEqual(b.commands[1:], [('--roster', '--state', STATE_FILE), ('--roster-state',)])
        reset = next(s for s in b.to_json()['edits'] if s['action'] == 'stat_reset')
        self.assertEqual((reset['target'], reset['effects']), ('0x0D', {'stat_edits': 'cleared (3 values on 1 character)'}))
        self.assertEqual(reset['edit'], {'op': 'stat_reset', 'id': '0x0D'})

    def test_nothing_to_clear_unless_a_file_comes_first(self):
        none = slot_plan.StatCheck(None)
        b = self.plan([{'op': 'stat_reset', 'id': '0x0D'}], {'reset:0x0D': none})
        self.assertEqual((b.commands, [p.action for _e, p in b.skipped]), ([], ['stat_reset']))
        b = self.plan([{'op': 'stat_edits', 'file': 'a.json'}, {'op': 'stat_reset', 'id': '0x0D'}],
                      {'a.json': self.CHANGED, 'reset:0x0D': none})
        self.assertEqual(b.commands[0], ('--apply-stat-edits', 'a.json', 'reset:0x0D'))

    def test_one_reset_per_slot(self):
        merged, _notes, _refused = slot_plan.merge_edits(slot_plan.parse_edits([
            {'op': 'stat_reset', 'id': '0x0D'}, {'op': 'stat_edits', 'file': 'a.json'},
            {'op': 'stat_reset', 'id': '0x0D'}]))
        self.assertEqual([e.op for e in merged], ['stat_edits', 'stat_reset'])

    def test_constants_pinned(self):
        self.assertEqual(slot_plan.RESET_PREFIX, apply.RESET_PREFIX)
        self.assertEqual(gui_grid.STAT_RESET, slot_plan.STAT_RESET)
        self.assertEqual(gui_grid.NEW_START, slot_plan.ids.FIRST_NEW)


class PackNoteTests(unittest.TestCase):
    def test_dropped_and_kept(self):
        plan = pack.LoadPlan([])
        stat_edits = {'characters': {'0x05': {}, '0x66': {}, '0x67': {}}, 'globals': 2}
        pack._stat_edit_notes(plan, stat_edits, {'ids': [{'id': '0x66'}]})
        self.assertEqual(plan.notes, ['stat edits stay (2 characters and 2 global values): the pack holds no stat '
                                      'values, so the game\'s are carried over by character ID, on top of the stats '
                                      'the pack\'s roster gives'])
        self.assertEqual(plan.warnings, ['the stat edits of 1 new ID are dropped: the pack\'s roster does not have '
                                         '0x67'])
        quiet = pack.LoadPlan([])
        pack._stat_edit_notes(quiet, None, {})
        self.assertEqual((quiet.notes, quiet.warnings), ([], []))


class GuiTests(unittest.TestCase):
    STATE = {'stat_edits': {'characters': {'0x05': {'fields': ['stats.speed'], 'chemistry': 0},
                                           '0x66': {'fields': [], 'chemistry': 1}}, 'globals': 0},
             'characters': [{'id': 0x05}, {'id': 0x66}], 'squares': []}

    def test_risks(self):
        self.assertEqual(gui_grid.stat_edit_risk(self.STATE, lost=True),
                         'Stat edits on 2 characters: this step replaces main.dol, so they are lost.')
        self.assertIn('Stat edits on 1 new ID: a roster without that ID drops them',
                      gui_grid.stat_edit_risk(self.STATE, lost=False))
        stock_only = {'stat_edits': {'characters': {'0x05': {}}, 'globals': 0}}
        self.assertIsNone(gui_grid.stat_edit_risk(stock_only, lost=False))
        self.assertIsNone(gui_grid.stat_edit_risk({}, lost=True))

    def test_slot_details_and_titles(self):
        self.assertIn('Stat edits: 1 field (speed)', gui_grid.slot_details(self.STATE, 0x05))
        self.assertEqual(gui_grid._edit_title(gui_grid.stat_reset_edit(0x05)), 'Pending: clear its stat edits')

    def test_slot_dialog_offers_the_reset(self):
        state = make_state()
        section = {'action': 'stats', 'target': '0x0D', 'rebuild': True, 'notes': ['n'], 'warnings': [],
                   'effects': {'stats': 'x'}, 'edit': {'op': 'stats', 'id': '0x0D', 'source': '0x09'},
                   'stat_edits_kept': '1 field (speed)'}
        plan = {'edits': [section], 'skipped': [], 'refused': [], 'notes': [], 'merged': [section['edit']]}
        dialog = gui_grid.slot_dialog(state, 0x0D, False, plan, 0, '', kind='stats')
        self.assertTrue(dialog.can_apply and dialog.offer_stat_reset)
        plan['merged'].append(gui_grid.stat_reset_edit(0x0D))
        dialog = gui_grid.slot_dialog(state, 0x0D, False, plan, 0, '', kind='stats')
        self.assertFalse(dialog.offer_stat_reset)
        self.assertIn(('Its stat edits are cleared too (a pending edit).', gui_grid.TEXT), dialog.lines)


if __name__ == '__main__':
    unittest.main()
