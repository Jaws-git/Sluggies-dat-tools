"""GUI character grid, Phase 4e: the apply chain (``Roster/slot_plan.py``, ``slot_cli.py``).

The plans are built from a hand-made read state and derived config, with the
file checks (skeletons, the slot's current high-poly model, portraits) given
by a fake ``Env``: every case's command list, the config change it carries,
and that refused cases give no commands and write nothing.
"""

import copy
import json
import os
import tempfile
import unittest
from unittest import mock

from SluggiesTools.Roster import model_icons, open_slot, slot_cli, slot_plan

STATE_FILE = '/out/_gui/slot/roster.json'
HP = '/m/27 Bowser/114968608_koopa.gpl/114968608_koopa.gpl.sluggie'
LOW = '/m/27 Bowser/115434464_L_koopa.gpl/115434464_L_koopa.gpl.sluggie'
SLOT_NAMES = {'en': 'Empty slot', 'fr': 'Emplacement vide', 'sp': 'Espacio vacío'}
NAMES_TEXT = {0x09: {'en': 'Bowser', 'fr': 'Bowser', 'sp': 'Bowser'}, 0x0D: {'en': 'Toad', 'fr': 'Toad', 'sp': 'Toad'}}


def char(cid, square, model_dir, *, template=None, own=False, source=None, name='X'):
    return {'id': cid, 'name': {'en': name}, 'default_name': None, 'template': template, 'model_dir': model_dir,
            'own_model_dir': own, 'model_source': cid if source is None else source,
            'stats': cid if template is None else template, 'square': square, 'icon': None}


def make_state(voice_set=None):
    return {'squares': [{'kind': 'stock', 'head': 0x09, 'members': [0x09, 0x68]},
                        {'kind': 'stock', 'head': 0x0D, 'members': [0x0D]},
                        {'kind': 'new', 'head': 0x66, 'members': [0x66, 0x67], 'voice_set': voice_set},
                        {'kind': 'stock', 'head': 0x47, 'members': [0x47, 0x50]}],
            'characters': [char(0x09, 0, 27, name='Bowser'), char(0x0D, 1, 31, name='Red Toad'),
                           char(0x47, 3, 89, name='#N/A'), char(0x50, 3, 98, name='Mii'),
                           char(0x66, 2, 22, template=0x04, source=0x04, name='Empty slot'),
                           char(0x67, 2, 172, template=0x04, own=True, source=0x09, name='Koopa King'),
                           char(0x68, 0, 27, template=0x09, source=0x09, name='Empty slot')]}


def make_config():
    return {'version': 1,
            'ids': [{'id': '0x66', 'template': '0x04', 'wheel': None, 'name': dict(SLOT_NAMES),
                     'icon': {'side': 'side_0_0.png', 'front': 'front_0_0.png', 'like': '0x04', 'fit': 'strict'}},
                    {'id': '0x67', 'template': '0x04', 'wheel': None, 'stats': '0x09',
                     'model': {'from': '0x09', 'routes': [[0x30000000, 0x100]]}, 'name': {'en': 'Koopa King'}},
                    {'id': '0x68', 'template': '0x09', 'wheel': '0x09', 'swatch': 3}],
            'wheels': [{'id': '0x47', 'wheel': '0x06', 'swatch': 1}],
            'grid': {'shape': [12, 5], 'squares': [['0x66', '0x67']], 'order': []}}


class FakeEnv(slot_plan.Env):
    def __init__(self, icons_ok=True, skeleton_errors=(), high_stems=None, shows=False):
        self.icons_ok, self.skeleton_errors, self.shows = icons_ok, list(skeleton_errors), shows
        self.high_stems = high_stems or {}
        self.skeleton_calls = []

    def model_icons(self, path):
        if not self.icons_ok:
            return model_icons.ModelIcons('/m/home', None, '/m/home/icon/FrontIcon.png',
                                          'icon/SideIcon.png missing in home: no portraits imported')
        return model_icons.ModelIcons('/m/home', '/m/home/icon/SideIcon.png', '/m/home/icon/FrontIcon.png')

    def skeleton(self, source, target):
        self.skeleton_calls.append((source, target))
        return list(self.skeleton_errors), []

    def current_high_stem(self, cid):
        return self.high_stems.get(cid)

    def shows_portraits(self, char, found):
        return self.shows


def pair(high=HP, low=LOW, chunk=27, stem='koopa'):
    return slot_plan.Pair(chunk, high, low, high or low, stem)


def ids_entry(config, cid):
    return next(e for e in config['ids'] if int(e['id'], 16) == cid)


class PatchNewIdTests(unittest.TestCase):
    def plan(self, cid, p=None, env=None, st=None, config=None):
        return slot_plan.plan_patch(st or make_state(), config or make_config(), cid, p or pair(),
                                    env or FakeEnv(), STATE_FILE, NAMES_TEXT)

    def test_open_slot_on_a_new_square(self):
        plan = self.plan(0x66)
        self.assertEqual(plan.commands, [
            ('--patch', HP, LOW, '--target-id', '0x66', '--validate-only'),
            ('--roster', '--state', STATE_FILE),
            ('--patch', HP, LOW, '--target-id', '0x66'),
            ('--roster-state',)])
        entry = ids_entry(plan.config, 0x66)
        self.assertEqual(entry['model'], {'from': '0x09'})                   # fresh copy, no routes
        self.assertEqual(entry['stats'], '0x09')
        self.assertEqual(entry['name'], NAMES_TEXT[0x09])
        self.assertEqual(entry['icon'], {'model': '/m/home', 'like': '0x04'})
        self.assertEqual(plan.config['grid']['squares'][0], {'members': ['0x66', '0x67'], 'voice': '0x09'})
        self.assertEqual(plan.to_json()['files'], {'high': HP, 'low': LOW, 'picked': HP})       # the GUI dialog
        self.assertEqual(plan.to_json()['source'], '0x09')

    def test_own_directory_of_the_same_source_is_kept(self):
        plan = self.plan(0x67)
        entry = ids_entry(plan.config, 0x67)
        self.assertEqual(entry['model'], make_config()['ids'][1]['model'])    # routes kept: earlier patches stay
        self.assertEqual(entry['name'], {'en': 'Koopa King'})                 # a user name is not replaced

    def test_own_directory_of_another_source_is_copied_afresh(self):
        toad = pair(high='/m/31 Toad/1_kinopio.gpl/1_kinopio.gpl.sluggie', low=None, chunk=31, stem='kinopio')
        plan = self.plan(0x67, toad)
        self.assertEqual(ids_entry(plan.config, 0x67)['model'], {'from': '0x0D'})
        self.assertTrue(any('dropped' in note for note in plan.notes))

    def test_set_voice_is_kept(self):
        plan = self.plan(0x66, st=make_state(voice_set=0x00))
        self.assertEqual(plan.config['grid']['squares'][0], ['0x66', '0x67'])

    def test_stock_square_keeps_stats_and_voice(self):
        plan = self.plan(0x68)
        entry = ids_entry(plan.config, 0x68)
        self.assertNotIn('stats', entry)
        self.assertEqual(entry['model'], {'from': '0x09'})
        self.assertEqual(plan.config['grid'], make_config()['grid'])

    def test_high_poly_without_partner_is_the_low_variant_too(self):
        plan = self.plan(0x66, pair(low=None))
        self.assertEqual(plan.commands[0], ('--patch', HP, '--target-id', '0x66', '--validate-only'))
        self.assertEqual(plan.commands[2], ('--patch', HP, '--target-id', '0x66', '--as-low'))
        self.assertTrue(any('loads it twice' in w for w in plan.warnings))

    def test_low_alone_is_refused_on_a_new_id(self):
        # Fresh copy of its own source, and a kept directory whose HP is its partner: both refused.
        with self.assertRaisesRegex(slot_plan.PlanError, 'new ID takes a character as a whole'):
            self.plan(0x66, pair(high=None))
        with self.assertRaisesRegex(slot_plan.PlanError, 'new ID takes a character as a whole'):
            self.plan(0x67, pair(high=None), FakeEnv(high_stems={0x67: 'koopa'}))

    def test_missing_portrait_view_keeps_the_icon(self):
        plan = self.plan(0x66, env=FakeEnv(icons_ok=False))
        self.assertEqual(ids_entry(plan.config, 0x66)['icon'], make_config()['ids'][0]['icon'])
        self.assertTrue(any('SideIcon.png missing' in note for note in plan.notes))

    def test_a_non_player_source_cannot_become_a_directory(self):
        with self.assertRaisesRegex(slot_plan.PlanError, 'not a stock player'):
            self.plan(0x66, pair(chunk=100, stem='mii'))

    def test_input_config_is_not_changed(self):
        config = make_config()
        self.plan(0x66, config=config)
        self.assertEqual(config, make_config())


class PatchStockTests(unittest.TestCase):
    def plan(self, cid, p=None, env=None):
        return slot_plan.plan_patch(make_state(), make_config(), cid, p or pair(), env or FakeEnv(), STATE_FILE,
                                    NAMES_TEXT)

    def test_skeleton_mismatch_is_refused(self):
        env = FakeEnv(skeleton_errors=['koopa.gpl has 105 bones, kinopio.gpl 89'])
        with self.assertRaisesRegex(slot_plan.PlanError, 'skeletons do not match'):
            self.plan(0x0D, env=env)

    def test_both_roles_are_checked(self):
        env = FakeEnv()
        self.plan(0x0D, env=env)
        self.assertEqual(env.skeleton_calls, [((27, 0), (31, 0)), ((27, 1), (31, 1))])

    def test_own_slot_needs_no_skeleton_check(self):
        env = FakeEnv()
        self.plan(0x09, env=env)
        self.assertEqual(env.skeleton_calls, [])

    def test_portraits_already_shown_need_no_rebuild(self):
        plan = self.plan(0x09, env=FakeEnv(shows=True))
        self.assertIsNone(plan.config)
        self.assertEqual(plan.commands, [('--patch', HP, LOW, '--target-id', '0x09', '--validate-only'),
                                         ('--patch', HP, LOW, '--target-id', '0x09'), ('--roster-state',)])

    def test_stock_portraits_go_to_stock_icons(self):
        plan = self.plan(0x0D)
        self.assertEqual(plan.config['stock_icons'], [{'id': '0x0D', 'icon': {'model': '/m/home'}}])
        self.assertEqual(plan.config['ids'], make_config()['ids'])           # stats, names untouched

    def test_spare_row_portraits_go_to_its_wheels_entry(self):
        plan = self.plan(0x47)
        self.assertEqual(plan.config['wheels'][0]['icon'], {'model': '/m/home'})

    def test_id_without_own_portrait_records(self):
        plan = self.plan(0x50)
        self.assertIsNone(plan.config)
        self.assertTrue(any('no portrait records' in note for note in plan.notes))

    def test_low_alone_under_its_partner_is_allowed(self):
        plan = self.plan(0x09, pair(high=None), FakeEnv(high_stems={0x09: 'koopa'}))
        self.assertEqual(plan.commands[-2], ('--patch', LOW, '--target-id', '0x09'))

    def test_low_alone_under_another_high_poly_model_is_refused(self):
        with self.assertRaisesRegex(slot_plan.PlanError, 'High model'):
            self.plan(0x0D, pair(high=None), FakeEnv(high_stems={0x0D: 'kinopio'}))

    def test_unknown_slot_is_refused(self):
        with self.assertRaisesRegex(slot_plan.PlanError, 'not a slot'):
            self.plan(0x99)


class ClearTests(unittest.TestCase):
    def test_new_id(self):
        plan = slot_plan.plan_clear(make_state(), make_config(), 0x67, STATE_FILE)
        self.assertEqual(plan.commands, [('--roster', '--state', STATE_FILE), ('--roster-state',)])
        entry = ids_entry(plan.config, 0x67)
        self.assertEqual(entry['model'], {'from': '0x04'})                    # fresh copy of the template
        self.assertNotIn('stats', entry)
        self.assertEqual(entry['name'], open_slot.SLOT_NAME)
        self.assertEqual(entry['icon'], open_slot.SLOT_ICON)
        self.assertEqual(set(plan.extra_portraits), set(open_slot.SLOT_ICON.values()))
        self.assertEqual(plan.config['grid'], make_config()['grid'])        # the square voice stays

    def test_stock_slot(self):
        plan = slot_plan.plan_clear(make_state(), make_config(), 0x0D, STATE_FILE)
        self.assertIsNone(plan.config)
        self.assertEqual(plan.commands, [('--unpatch', '--target-id', '0x0D'), ('--roster-state',)])

    def test_stock_slot_with_replaced_portraits(self):
        config = dict(make_config(), stock_icons=[{'id': '0x0D', 'icon': {'side': 'a.png', 'front': 'b.png'}}])
        plan = slot_plan.plan_clear(make_state(), config, 0x0D, STATE_FILE)
        self.assertNotIn('stock_icons', plan.config)
        self.assertEqual(plan.commands, [('--roster', '--state', STATE_FILE), ('--unpatch', '--target-id', '0x0D'),
                                         ('--roster-state',)])


TOAD_HP = '/m/31 Toad/1_kinopio.gpl/1_kinopio.gpl.sluggie'
TOAD_LOW = '/m/31 Toad/2_L_kinopio.gpl/2_L_kinopio.gpl.sluggie'
MARIO_HP = '/m/18 Mario/1_mario.gpl/1_mario.gpl.sluggie'
LONE_LOW = '/x/2_L_koopa.gpl/2_L_koopa.gpl.sluggie'           # a Low export with no High beside it
PAIRS = {HP: pair(), LOW: pair(), TOAD_HP: pair(TOAD_HP, TOAD_LOW, 31, 'kinopio'),
         MARIO_HP: pair(MARIO_HP, None, 18, 'mario'), LONE_LOW: slot_plan.Pair(27, None, LONE_LOW, LONE_LOW, 'koopa'),
         TOAD_LOW: slot_plan.Pair(31, None, TOAD_LOW, TOAD_LOW, 'kinopio')}
HP_ALONE = '/y/1_koopa.gpl/1_koopa.gpl.sluggie'                # a High export with no Low beside it
PAIRS[HP_ALONE] = slot_plan.Pair(27, HP_ALONE, None, HP_ALONE, 'koopa')


def fake_classify(path):
    if path not in PAIRS:
        raise slot_plan.PlanError(f'{path} is not a .sluggie file')
    p = PAIRS[path]
    return slot_plan.Pair(p.chunk, p.high, p.low, path, p.stem)


def edits(*items):
    return slot_plan.parse_edits([dict(op=op, id=cid, **({'file': f} if f else {}), **extra)
                                  for op, cid, f, *rest in items for extra in [rest[0] if rest else {}]])


class BaselineEnv(FakeEnv):
    def __init__(self, baseline=(), **kwargs):
        super().__init__(**kwargs)
        self.baseline = set(baseline)

    def at_baseline(self, char, config):
        return char['id'] in self.baseline


class BatchTests(unittest.TestCase):
    def batch(self, items, env=None, st=None, **kwargs):
        return slot_plan.plan_batch(st or make_state(), make_config(), items, env or FakeEnv(), STATE_FILE,
                                    NAMES_TEXT, classify_fn=fake_classify, **kwargs)

    def test_three_slots_give_one_rebuild_and_one_read(self):
        batch = self.batch(edits(('patch', '0x66', HP), ('patch', '0x0D', TOAD_HP), ('clear', '0x67', None)))
        self.assertTrue(batch.ok)
        self.assertEqual(batch.commands, [
            ('--patch', HP, LOW, '--target-id', '0x66', '--validate-only'),
            ('--patch', TOAD_HP, TOAD_LOW, '--target-id', '0x0D', '--validate-only'),
            ('--roster', '--state', STATE_FILE),
            ('--patch', HP, LOW, '--target-id', '0x66'),
            ('--patch', TOAD_HP, TOAD_LOW, '--target-id', '0x0D'),
            ('--roster-state',)])
        self.assertEqual(ids_entry(batch.config, 0x66)['model'], {'from': '0x09'})        # every edit in one config
        self.assertEqual(ids_entry(batch.config, 0x67)['model'], {'from': '0x04'})
        self.assertEqual(batch.config['stock_icons'], [{'id': '0x0D', 'icon': {'model': '/m/home'}}])
        self.assertEqual(set(batch.extra_portraits), set(open_slot.SLOT_ICON.values()))
        self.assertEqual([e['id'] for e in batch.to_json()['merged']], ['0x66', '0x0D', '0x67'])

    def test_model_only_stock_patches_need_no_rebuild(self):
        batch = self.batch(edits(('patch', '0x09', HP), ('patch', '0x0D', TOAD_HP)), env=FakeEnv(shows=True))
        self.assertIsNone(batch.config)
        self.assertNotIn(('--roster', '--state', STATE_FILE), batch.commands)
        self.assertEqual(batch.commands[-3:], [('--patch', HP, LOW, '--target-id', '0x09'),
                                               ('--patch', TOAD_HP, TOAD_LOW, '--target-id', '0x0D'),
                                               ('--roster-state',)])

    def test_a_refused_edit_refuses_the_batch(self):
        env = FakeEnv(skeleton_errors=['105 vs 89 bones'])
        batch = self.batch(edits(('patch', '0x66', HP), ('patch', '0x0D', HP), ('patch', '0x99', HP)), env=env)
        self.assertFalse(batch.ok)
        self.assertEqual(batch.commands, [])
        self.assertIsNone(batch.config)
        self.assertEqual([(e.index, e.cid) for e, _msg in batch.refused], [(2, 0x0D), (3, 0x99)])
        self.assertIn('skeletons do not match', batch.refused[0][1])
        self.assertIn('not a slot', batch.refused[1][1])

    def test_last_model_edit_wins(self):
        batch = self.batch(edits(('patch', '0x66', HP), ('patch', '0x66', TOAD_HP)))
        self.assertEqual([(e.cid, e.file) for e, _p in batch.plans], [(0x66, TOAD_HP)])
        batch = self.batch(edits(('patch', '0x0D', TOAD_HP), ('clear', '0x0D', None)))
        self.assertEqual([e.op for e, _p in batch.plans], ['clear'])
        self.assertEqual(batch.commands, [('--unpatch', '--target-id', '0x0D'), ('--roster-state',)])
        self.assertTrue(any('replaced by the later clear' in n for n in batch.notes))

    def test_low_pick_joins_a_pending_high_pick(self):
        batch = self.batch(edits(('patch', '0x09', HP_ALONE), ('patch', '0x09', LONE_LOW)))
        self.assertTrue(batch.ok)
        (edit, plan), = batch.plans
        self.assertEqual((edit.file, edit.low), (HP_ALONE, LONE_LOW))
        self.assertEqual(batch.commands[-2], ('--patch', HP_ALONE, LONE_LOW, '--target-id', '0x09'))  # no --as-low
        self.assertEqual(batch.to_json()['merged'], [{'op': 'patch', 'id': '0x09', 'file': HP_ALONE, 'low': LONE_LOW}])
        # the staged pair comes back from the edits file as the same pair
        again = self.batch(slot_plan.parse_edits(batch.to_json()['merged']))
        self.assertEqual(again.commands, batch.commands)

    def test_low_rule_sees_the_pending_high_pick(self):
        # In the game 0x09 still has its own Bowser model, but the pending High pick is Mario's: refused.
        env = FakeEnv(high_stems={0x09: 'koopa'})
        batch = self.batch(edits(('patch', '0x09', MARIO_HP), ('patch', '0x09', LONE_LOW)), env=env)
        self.assertFalse(batch.ok)
        self.assertIn('pending High model of 0x09 is 1_mario.gpl.sluggie', batch.refused[0][1])
        batch = self.batch(edits(('clear', '0x09', None), ('patch', '0x09', LONE_LOW)), env=env)
        self.assertIn('clear of 0x09 is pending', batch.refused[0][1])
        self.assertTrue(self.batch(edits(('patch', '0x09', LONE_LOW)), env=env).ok)     # no pending pick: the game

    def test_clear_then_rename_and_rename_then_clear(self):
        merged, notes, refused = slot_plan.merge_edits(
            edits(('rename', '0x66', None, {'text': 'Early'}), ('clear', '0x66', None),
                  ('rename', '0x66', None, {'text': 'Late'})), fake_classify)
        self.assertEqual([(e.op, e.text) for e in merged], [('clear', None), ('rename', 'Late')])
        self.assertTrue(any('"Early" is dropped' in n for n in notes))
        self.assertEqual(refused, [])
        # rename ops are not written before Phase 5: the batch refuses them
        batch = self.batch(edits(('rename', '0x66', None, {'text': 'Late'})))
        self.assertIn('Phase 5', batch.refused[0][1])

    def test_voice_rule_counts_pending_patches(self):
        batch = self.batch(edits(('patch', '0x66', HP), ('patch', '0x67', TOAD_HP)))
        self.assertEqual(batch.config['grid']['squares'][0], {'members': ['0x66', '0x67'], 'voice': '0x09'})
        self.assertTrue(any('voice' in n for n in batch.plans[0][1].notes))
        self.assertFalse(any('voice' in n for n in batch.plans[1][1].notes))
        self.assertEqual(ids_entry(batch.config, 0x67)['stats'], '0x0D')                 # stats stay per slot

    def test_batch_of_one_equals_the_single_plans(self):
        for edit, single in (
                (('patch', '0x66', HP), lambda: slot_plan.plan_patch(make_state(), make_config(), 0x66, pair(),
                                                                     FakeEnv(), STATE_FILE, NAMES_TEXT)),
                (('patch', '0x09', HP), lambda: slot_plan.plan_patch(make_state(), make_config(), 0x09, pair(),
                                                                     FakeEnv(shows=True), STATE_FILE, NAMES_TEXT)),
                (('clear', '0x67', None), lambda: slot_plan.plan_clear(make_state(), make_config(), 0x67, STATE_FILE)),
                (('clear', '0x0D', None), lambda: slot_plan.plan_clear(make_state(), make_config(), 0x0D, STATE_FILE))):
            with self.subTest(edit=edit):
                plan = single()
                env = FakeEnv(shows=True) if edit[1] == '0x09' else FakeEnv()
                batch = self.batch(edits(edit), env=env)
                self.assertEqual(batch.commands, plan.commands)
                self.assertEqual(batch.config, plan.config)

    def test_staging_dry_run_skips_checked_build_checks(self):
        items = edits(('patch', '0x66', HP, {'checked': True}), ('patch', '0x0D', TOAD_HP))
        batch = self.batch(items, skip_checked=True)
        self.assertEqual([c for c in batch.commands if '--validate-only' in c],
                         [('--patch', TOAD_HP, TOAD_LOW, '--target-id', '0x0D', '--validate-only')])
        self.assertEqual(len([c for c in self.batch(items).commands if '--validate-only' in c]), 2)

    def test_clear_at_baseline_is_skipped(self):
        batch = self.batch(edits(('clear', '0x0D', None)), env=BaselineEnv({0x0D}))
        self.assertEqual((batch.commands, batch.plans), ([], []))
        self.assertTrue(batch.ok)
        self.assertTrue(batch.to_json()['skipped'][0]['nothing'])
        self.assertIn('nothing to clear', batch.skipped[0][1].notes[0])
        # a pending patch then the clear: the clear replaces it and is skipped, so nothing stays pending
        batch = self.batch(edits(('patch', '0x0D', TOAD_HP), ('clear', '0x0D', None)), env=BaselineEnv({0x0D}))
        self.assertEqual(batch.to_json()['merged'], [])

    def test_effects_for_the_pending_view(self):
        batch = self.batch(edits(('patch', '0x66', HP), ('clear', '0x67', None)))
        effects = batch.plans[0][1].effects
        self.assertEqual(effects['name'], 'Bowser')
        self.assertEqual(effects['stats'], 'Bowser (0x09)')
        self.assertEqual(effects['voice'], 'Bowser (0x09) (square voice)')
        self.assertEqual(effects['portraits'], {'front': '/m/home/icon/FrontIcon.png', 'side': '/m/home/icon/SideIcon.png'})
        self.assertEqual(batch.plans[1][1].effects['name'], 'Empty slot')
        self.assertTrue(batch.plans[1][1].effects['portraits']['front'].endswith(open_slot.SLOT_ICON['front']))

    def test_malformed_edits_are_refused(self):
        for data in ({'edits': 'x'}, [{'op': 'paint', 'id': '0x66'}], [{'op': 'patch', 'id': '0x66'}]):
            with self.subTest(data=data), self.assertRaises(slot_plan.PlanError):
                slot_plan.parse_edits(data)


def write_sluggie(folder, name, chunk, file_index, geo):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'SluggiesModel': {'ChunkNumber': chunk, 'FileIndex': file_index,
                                     'ACTHeader': {'GeoName': geo}}}, f)
    return path


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.char = os.path.join(self.tmp, '27 Bowser')

    def test_partner_found_from_either_side(self):
        hp = write_sluggie(os.path.join(self.char, '1_koopa.gpl'), '1_koopa.gpl.sluggie', 27, 0, 'koopa.gpl')
        low = write_sluggie(os.path.join(self.char, '2_L_koopa.gpl'), '2_L_koopa.gpl.sluggie', 27, 1, 'L_koopa.gpl')
        for picked in (hp, low):
            p = slot_plan.classify(picked)
            self.assertEqual((p.high, p.low, p.stem, p.source), (hp, low, 'koopa', 0x09))

    def test_partner_of_another_chunk_is_ignored(self):
        hp = write_sluggie(os.path.join(self.char, '1_koopa.gpl'), '1_koopa.gpl.sluggie', 27, 0, 'koopa.gpl')
        write_sluggie(os.path.join(self.char, '2_L_koopa.gpl'), '2_L_koopa.gpl.sluggie', 28, 1, 'L_koopa.gpl')
        self.assertIsNone(slot_plan.classify(hp).low)

    def test_stadiums_and_props_are_refused(self):
        stadium = write_sluggie(os.path.join(self.tmp, '10 Yoshi Park', '1_sta03.gpl'), 'sta03.gpl.sluggie', 10, 0,
                                'sta03.gpl')
        bat = write_sluggie(os.path.join(self.char, '3', '4_bat.gpl'), 'bat.gpl.sluggie', 27, 2, 'bat.gpl')
        with self.assertRaisesRegex(slot_plan.PlanError, 'not a character directory'):
            slot_plan.classify(stadium)
        with self.assertRaisesRegex(slot_plan.PlanError, 'not a character model'):
            slot_plan.classify(bat)


class SlotCliTests(unittest.TestCase):
    """A refused plan leaves nothing behind: no plan file (so no stale chain), no derived config."""

    def test_refused_plan_writes_nothing(self):
        with tempfile.TemporaryDirectory() as out:
            folder = slot_cli.slot_dir(out)
            os.makedirs(folder)
            with open(slot_cli.plan_path(out), 'w') as f:
                f.write('{"commands": [["--roster-state"]]}')          # a stale plan from an earlier run
            with mock.patch.object(slot_cli, 'run', side_effect=slot_plan.PlanError('no')):
                self.assertEqual(slot_cli.main(['--clear', '0x0D', '--output-dir', out]), 1)
            self.assertEqual(os.listdir(folder), [])

    def test_refused_batch_writes_only_the_plan(self):
        """A refused edit: the plan names it and holds no commands; no derived config is written."""
        with tempfile.TemporaryDirectory() as out:
            refused = slot_plan.Batch(None, refused=[(slot_plan.Edit('patch', 0x0D, 'a.sluggie', index=2), 'bones')])
            with mock.patch.object(slot_cli.state_cli, '_open', return_value=(None, None)), \
                    mock.patch.object(slot_cli.state, 'read_state', return_value={}), \
                    mock.patch.object(slot_cli.state, 'read_names', return_value={}), \
                    mock.patch.object(slot_cli.derive, 'derive', return_value=mock.Mock(config={}, warnings=[], portraits={})), \
                    mock.patch.object(slot_cli.slot_plan, 'plan_batch', return_value=refused), \
                    mock.patch.object(slot_cli.derive, 'write') as write:
                edits_file = os.path.join(out, 'edits.json')
                with open(edits_file, 'w') as f:
                    json.dump({'edits': [{'op': 'clear', 'id': '0x66'}, {'op': 'patch', 'id': '0x0D', 'file': 'a'}]}, f)
                self.assertEqual(slot_cli.main(['--apply', edits_file, '--output-dir', out]), 1)
            write.assert_not_called()
            with open(slot_cli.plan_path(out), encoding='utf-8') as f:
                plan = json.load(f)
            self.assertEqual((plan['ok'], plan['commands']), (False, []))
            self.assertEqual(plan['refused'], [{'edit': {'op': 'patch', 'id': '0x0D', 'file': 'a.sluggie'},
                                                'target': '0x0D', 'error': 'bones'}])


class DispatchTests(unittest.TestCase):
    """``start.py --patch-slot``: the planner first, then its commands in order, stopping at a failure."""

    def setUp(self):
        import start
        self.start = start
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.plan_file = os.path.join(self.tmp, 'plan.json')
        with open(self.plan_file, 'w') as f:
            json.dump({'commands': [['--roster', '--state', 's.json'], ['--patch', 'a', '--target-id', '0x66'],
                                    ['--roster-state']]}, f)
        self.enterContext(mock.patch.object(start, 'SLOT_PLAN_FILE', self.plan_file))

    def run_chain(self, codes, **kwargs):
        results = [mock.Mock(returncode=code) for code in codes]
        with mock.patch('start.subprocess.run', side_effect=results) as run:
            ok = self.start.run_slot_chain('0x66', 'a.sluggie', **kwargs)
        return ok, [call.args[0] for call in run.call_args_list]

    def test_runs_every_command(self):
        ok, calls = self.run_chain([0, 0, 0, 0])
        self.assertTrue(ok)
        self.assertEqual(calls[0][-3:], ['--patch', '0x66', os.path.abspath('a.sluggie')])   # the planner
        self.assertEqual(calls[1][-3:], ['--roster', '--state', 's.json'])
        self.assertEqual(calls[2][-4:], ['--patch', 'a', '--target-id', '0x66'])
        self.assertEqual(calls[3][-1], '--roster-state')

    def test_refused_plan_runs_nothing(self):
        ok, calls = self.run_chain([1])
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1)

    def test_failed_step_stops_the_chain(self):
        ok, calls = self.run_chain([0, 0, 1])
        self.assertFalse(ok)
        self.assertEqual(len(calls), 3)

    def test_dry_run_runs_only_the_build_check(self):
        with open(self.plan_file, 'w') as f:
            json.dump({'commands': [['--patch', 'a', '--target-id', '0x66', '--validate-only'],
                                    ['--roster', '--state', 's.json'], ['--patch', 'a', '--target-id', '0x66'],
                                    ['--roster-state']]}, f)
        ok, calls = self.run_chain([0, 0], dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][-1], '--validate-only')

    def test_edits_file_dry_run_tells_the_planner(self):
        results = [mock.Mock(returncode=0) for _ in range(4)]
        with mock.patch('start.subprocess.run', side_effect=results) as run:
            self.assertTrue(self.start.run_slot_chain(edits_file='e.json', dry_run=True))
        planner = run.call_args_list[0].args[0]
        self.assertEqual(planner[-3:], ['--apply', os.path.abspath('e.json'), '--dry-run'])

    def test_dry_run_of_a_clear_only_plans(self):
        ok, calls = self.run_chain([0], dry_run=True)                          # no build check in this chain
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)


class TargetedPatchStopsTests(unittest.TestCase):
    def test_low_poly_file_is_skipped_after_a_failed_high_poly_file(self):
        import subprocess
        import start
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name in ('0_koopa.gpl.sluggie', '1_L_koopa.gpl.sluggie'):
                paths.append(os.path.join(tmp, name))
                with open(paths[-1], 'w') as f:
                    json.dump({'SluggiesModel': {}}, f)
            with mock.patch('start._patch_sluggie', side_effect=subprocess.CalledProcessError(1, 'x')) as patch:
                self.assertFalse(start.run_patching(list(reversed(paths)), target_id='0x66'))
        self.assertEqual([os.path.basename(c.args[0]) for c in patch.call_args_list], ['0_koopa.gpl.sluggie'])


if __name__ == '__main__':
    unittest.main()
