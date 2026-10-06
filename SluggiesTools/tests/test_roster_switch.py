"""Roster switches that keep the slots' customisations (``Roster/migrate.py``, ``start.py --roster --config``).

A small synthetic game on an expanded roster: Mario (0x00) renamed and with another stats source, the spare row
0x47 (Black Yoshi) with patched models, a bat and stat edits, and four new IDs: 0x66 (an own Bowser directory, a
name, own portraits, stats), 0x67 (an open slot), 0x68 (customised, absent from the new preset) and 0x69 (an own
directory that is an unchanged copy of its template). The file checks are a stand-in ``Env``.
"""

import copy
import json
import os
import tempfile
import unittest
from unittest import mock

from SluggiesTools import gui_grid
from SluggiesTools.Roster import derive, migrate, slot_plan

ROUTES = [[0x2000000, 100], [0x2001000, 50]]


class Env(slot_plan.Env):
    def __init__(self, at_baseline=(), open_portraits=()):
        self.at_baseline_ids, self.open_portraits = set(at_baseline), set(open_portraits)

    def models_at_baseline(self, char):
        return char['id'] in self.at_baseline_ids

    def shows_open_slot_portraits(self, char):
        return char['id'] in self.open_portraits


def char(cid, **extra):
    out = {'id': cid, 'own_model_dir': False, 'template': None, 'model_source': cid,
           'equipment': {'bat': {'file': 2, 'vanilla': True}, 'glove_l': {'file': 3, 'vanilla': True}}}
    out.update(extra)
    return out


def game_state() -> dict:
    return {'characters': [
        char(0x00), char(0x01),
        char(0x47, equipment={'bat': {'file': 2, 'vanilla': False}, 'glove_l': {'file': 3, 'vanilla': None},
                              'glove_r': {'file': 4, 'vanilla': True}}),
        char(0x66, template=0x06, own_model_dir=True, model_source=0x09),
        char(0x67, template=0x06, model_source=0x06),
        char(0x68, template=0x06, own_model_dir=True, model_source=0x0D),
        char(0x69, template=0x06, own_model_dir=True, model_source=0x06)],
        'stat_edits': {'characters': {'0x01': {}, '0x47': {}, '0x66': {}, '0x68': {}}, 'globals': 0}}


def game_derived() -> derive.Derived:
    return derive.Derived({
        'version': 1, 'comment': 'derived',
        'ids': [{'id': '0x66', 'template': '0x06', 'wheel': None, 'stats': '0x09',
                 'model': {'from': '0x09', 'routes': copy.deepcopy(ROUTES)}, 'name': {'en': 'Own Bowser'},
                 'icon': {'side': 'side_0_0.png', 'front': 'front_0_0.png', 'like': '0x06', 'fit': 'strict'}},
                {'id': '0x67', 'template': '0x06', 'wheel': '0x06', 'name': {'en': 'Empty slot'},
                 'icon': {'side': 'side_4_0.png', 'front': 'front_4_0.png', 'like': '0x06', 'fit': 'strict'}},
                {'id': '0x68', 'template': '0x06', 'wheel': '0x06', 'model': {'from': '0x0D', 'routes': ROUTES},
                 'name': {'en': 'Gone'}},
                {'id': '0x69', 'template': '0x06', 'wheel': '0x06', 'model': {'from': '0x06', 'routes': ROUTES},
                 'name': {'en': 'Empty slot'}}],
        'wheels': [{'id': '0x47', 'wheel': '0x06', 'name': {'en': 'Inky'},
                    'icon': {'side': 'side_8_0.png', 'front': 'front_8_0.png', 'like': '0x04', 'fit': 'strict'}}],
        'grid': {'shape': [11, 5], 'squares': [{'members': ['0x66'], 'voice': '0x09'}], 'order': []},
        'stock_names': [{'id': '0x00', 'name': {'en': 'Super Mario'}}],
        'stock_stats': [{'id': '0x00', 'stats': '0x09'}, {'id': '0x47', 'stats': '0x02'}],
        'stock_voices': [{'id': '0x00', 'voice': '0x09'}]},
        {'side_0_0.png': None, 'front_0_0.png': None})


SLOT = {'side': 'empty_slot_side.png', 'front': 'empty_slot_front.png'}


def preset(**extra) -> dict:
    """A preset that keeps 0x66, 0x67 and 0x69 (on other templates) and drops 0x68 and the spare rows."""
    out = {'version': 1, 'comment': 'preset',
           'ids': [{'id': '0x66', 'template': '0x0C', 'wheel': None, 'icon': dict(SLOT), 'name': {'en': 'Empty slot'}},
                   {'id': '0x67', 'template': '0x0C', 'wheel': '0x0C', 'swatch': 2, 'icon': dict(SLOT),
                    'name': {'en': 'Empty slot'}},
                   {'id': '0x69', 'template': '0x0C', 'wheel': '0x0C', 'icon': dict(SLOT)},
                   {'id': '0x6A', 'template': '0x0C', 'wheel': '0x0C', 'icon': dict(SLOT)}],
           'grid': {'shape': [12, 5], 'squares': [['0x66']], 'order': []}}
    out.update(extra)
    return out


def plan(p=None, env=None, st=None):
    return migrate.plan_switch(p or preset(), st or game_state(), game_derived(),
                               env or Env(at_baseline={0x00, 0x01, 0x69}, open_portraits={0x67}), 'merged.json',
                               icon_dir='ICONS')


def entry(config, cid):
    return next(e for e in config['ids'] if e['id'] == f'0x{cid:02X}')


class MergeTests(unittest.TestCase):
    def test_a_kept_new_id_keeps_its_customisations_but_sits_where_the_preset_puts_it(self):
        result = plan()
        e = entry(result.config, 0x66)
        self.assertEqual(e['template'], '0x0C')
        self.assertIsNone(e['wheel'])
        self.assertEqual(e['model'], {'from': '0x09', 'routes': ROUTES})
        self.assertEqual(e['stats'], '0x09')
        self.assertEqual(e['name'], {'en': 'Own Bowser'})
        # the like was the old template: the new template is the default
        self.assertEqual(e['icon'], {'side': 'side_0_0.png', 'front': 'front_0_0.png', 'fit': 'strict'})
        self.assertEqual(result.carried[0x66], ['model', 'stats', 'name', 'icon', 'voice', 'stat_edits'])

    def test_a_like_that_is_not_the_old_template_stays(self):
        derived = game_derived()
        derived.config['ids'][0]['icon']['like'] = '0x09'
        result = migrate.plan_switch(preset(), game_state(), derived, Env(open_portraits={0x67}), 'm.json', 'ICONS')
        self.assertEqual(entry(result.config, 0x66)['icon']['like'], '0x09')

    def test_open_slots_take_the_preset_entry(self):
        result = plan()
        e = entry(result.config, 0x67)
        self.assertEqual((e['template'], e['wheel'], e['swatch']), ('0x0C', '0x0C', 2))
        self.assertEqual(e['name'], {'en': 'Empty slot'})
        self.assertEqual(e['icon'], {view: migrate.PRESET_ICON_PREFIX + name for view, name in SLOT.items()})
        self.assertNotIn(0x67, result.carried)

    def test_an_unchanged_template_copy_is_not_kept(self):
        result = plan()
        self.assertNotIn('model', entry(result.config, 0x69))
        # but a patched one is
        result = plan(env=Env(at_baseline={0x00}, open_portraits={0x67}))
        self.assertEqual(entry(result.config, 0x69)['model']['routes'], ROUTES)
        # and so is one whose equipment changed
        st = game_state()
        st['characters'][-1]['equipment']['bat']['vanilla'] = False
        self.assertIn('model', entry(plan(st=st).config, 0x69))

    def test_new_ids_only_in_the_preset_are_open_slots(self):
        e = entry(plan().config, 0x6A)
        self.assertEqual(e['icon'], {view: migrate.PRESET_ICON_PREFIX + name for view, name in SLOT.items()})
        self.assertNotIn('model', e)

    def test_stock_entries_of_the_game_replace_the_presets(self):
        p = preset(stock_names=[{'id': '0x00', 'name': {'en': 'Preset Mario'}}, {'id': '0x02', 'name': {'en': 'P'}}])
        config = plan(p).config
        self.assertEqual(config['stock_names'], [{'id': '0x02', 'name': {'en': 'P'}},
                                                 {'id': '0x00', 'name': {'en': 'Super Mario'}}])
        self.assertEqual(config['stock_stats'], [{'id': '0x00', 'stats': '0x09'}])      # 0x47 left the grid
        self.assertEqual(config['stock_voices'], [{'id': '0x00', 'voice': '0x09'}])
        self.assertEqual(plan(p).carried[0x00], ['name', 'stats', 'voice'])

    def test_a_new_square_headed_by_a_kept_id_takes_its_old_voice(self):
        self.assertEqual(plan().config['grid']['squares'], [{'members': ['0x66'], 'voice': '0x09'}])

    def test_preset_portraits_are_copied_under_a_prefix(self):
        result = plan()
        self.assertEqual(result.preset_icons, {migrate.PRESET_ICON_PREFIX + n: os.path.join('ICONS', n)
                                               for n in SLOT.values()})

    def test_ids_without_id_are_numbered_as_the_ids_step_does(self):
        p = preset()
        del p['ids'][3]['id']
        self.assertEqual(entry(plan(p).config, 0x67)['template'], '0x0C')
        self.assertIn('0x68', [e['id'] for e in plan(p).config['ids']])   # the first free ID

    def test_an_invalid_preset_is_refused(self):
        p = preset()
        p['ids'][0]['template'] = '0x60'
        with self.assertRaises(migrate.SwitchError):
            plan(p)


class LeavingTests(unittest.TestCase):
    def test_new_ids_leaving_are_dropped(self):
        result = plan()
        self.assertNotIn('0x68', [e['id'] for e in result.config['ids']])
        self.assertEqual(result.dropped, {0x68: ['model', 'name', 'stat_edits']})

    def test_a_spare_row_leaving_the_grid_is_reset(self):
        result = plan()
        self.assertEqual(result.reset[0x47], ['stat_edits', 'model', 'equipment', 'name', 'icon', 'stats'])
        self.assertEqual(result.commands, [
            ('--apply-stat-edits', 'reset:0x47'),
            ('--unpatch', '--target-id', '0x47'),
            ('--unpatch', '--target-id', '0x47', '--target-file', '2'),
            ('--unpatch', '--target-id', '0x47', '--target-file', '3'),       # a split copy: a clear repairs it
            ('--roster', '--state', 'merged.json'),
            ('--roster-state',)])

    def test_a_spare_row_on_both_grids_keeps_its_name_and_portraits(self):
        p = preset(wheels=[{'id': '0x47', 'wheel': '0x04', 'swatch': 'black',
                            'icon': {'side': 'black_yoshi_side.png', 'front': 'black_yoshi_front.png'}}])
        result = plan(p)
        spare = result.config['wheels'][0]
        self.assertEqual((spare['wheel'], spare['swatch']), ('0x04', 'black'))
        self.assertEqual(spare['name'], {'en': 'Inky'})
        self.assertEqual(spare['icon']['side'], 'side_8_0.png')
        self.assertNotIn(0x47, result.reset)
        self.assertIn({'id': '0x47', 'stats': '0x02'}, result.config['stock_stats'])
        self.assertEqual(result.commands, [('--roster', '--state', 'merged.json'), ('--roster-state',)])

    def test_a_stock_result_is_a_reset_that_keeps_stat_edits(self):
        derived = derive.Derived({'version': 1}, {})
        st = {'characters': [char(0x00)], 'stat_edits': None}
        result = migrate.plan_switch({'version': 1, 'comment': 'stock'}, st, derived, Env(at_baseline={0x00}),
                                     'm.json')
        self.assertTrue(result.remove)
        self.assertEqual(result.commands, [('--roster', '--remove', '--keep-stat-edits'), ('--roster-state',)])

    def test_on_grid(self):
        self.assertEqual(migrate.on_grid({}), set(range(0x47)))
        self.assertEqual(migrate.on_grid(preset(wheels=[{'id': '0x48'}])) - set(range(0x47)),
                         {0x48, 0x66, 0x67, 0x69, 0x6A})


class GuiTests(unittest.TestCase):
    def test_field_labels_match(self):
        self.assertEqual(gui_grid.SWITCH_FIELDS, migrate.FIELD_LABELS)

    def test_dialog(self):
        out = plan().to_json()
        out['names'] = {'0x66': 'Own Bowser', '0x47': 'Inky'}
        dialog = gui_grid.switch_dialog(out, 0, '', 'x/03.json')
        texts = [t for t, _k in dialog.lines]
        self.assertTrue(dialog.can_apply)
        self.assertIn('  Own Bowser (0x66): models, stats source, name, portraits, square voice, stat edits', texts)
        self.assertIn('  Inky (0x47): stat edits, models, equipment, name, portraits, stats source', texts)
        self.assertIn('  0x68: models, name, stat edits', texts)
        refused = gui_grid.switch_dialog(None, 1, '[roster.switch] [Error] refused, nothing written: bad', 'x/03.json')
        self.assertFalse(refused.can_apply)
        self.assertEqual(refused.lines[0][0], 'Refused: bad')

    def test_commands(self):
        self.assertEqual(gui_grid.switch_command('p.json', dry_run=True), ('--roster', '--config', 'p.json', '--dry-run'))
        self.assertEqual(gui_grid.switch_command('p.json', fresh=True), ('--roster', '--config', 'p.json', '--fresh'))

    def test_reset_risk(self):
        state = {'stat_edits': {'characters': {'0x05': {}}, 'globals': 1}}
        self.assertEqual(gui_grid.stat_edit_risk(state, True, gui_grid.STATS_RESET),
                         'Stat edits on 1 character and 1 global value: a reset to vanilla clears them.')


class DispatchTests(unittest.TestCase):
    """``start.py --roster --config``: the planner, then its commands; ``--fresh`` and ``--remove`` go direct."""

    def setUp(self):
        import start
        self.start = start
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.plan_file = os.path.join(self.tmp, 'plan.json')
        self.enterContext(mock.patch.object(start, 'SWITCH_PLAN_FILE', self.plan_file))
        with open(self.plan_file, 'w') as f:
            json.dump({'commands': [['--apply-stat-edits', 'reset:0x47'], ['--roster', '--state', 's.json'],
                                    ['--roster-state']]}, f)

    def run_switch(self, dry_run):
        with mock.patch.object(self.start.subprocess, 'run') as run, \
                mock.patch.object(self.start, 'run_chain_commands', return_value=True) as chain:
            run.return_value.returncode = 0
            self.assertTrue(self.start.run_roster_switch('p.json', dry_run=dry_run))
        self.assertIn('switch_cli.py', ' '.join(run.call_args.args[0]))
        return chain.call_args.args[0]

    def test_runs_the_planned_chain(self):
        self.assertEqual(self.run_switch(False)[0], ['--apply-stat-edits', 'reset:0x47'])

    def test_dry_run_only_builds_the_merged_roster(self):
        self.assertEqual(self.run_switch(True), [['--roster', '--state', 's.json', '--dry-run']])

    def test_flags(self):
        def parse(*argv):
            with mock.patch('sys.argv', ['start.py', *argv]):
                return self.start.parse_args()
        self.assertTrue(parse('--roster', '--config', 'x.json', '--fresh').fresh)
        self.assertTrue(parse('--roster', '--remove', '--keep-stat-edits').keep_stat_edits)
        for argv in (('--roster', '--remove', '--fresh'), ('--roster', '--config', 'x.json', '--keep-stat-edits')):
            with self.subTest(argv=argv), mock.patch('sys.stderr'), self.assertRaises(SystemExit):
                parse(*argv)


if __name__ == '__main__':
    unittest.main()
