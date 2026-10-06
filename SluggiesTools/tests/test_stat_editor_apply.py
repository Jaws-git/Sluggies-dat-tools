"""Apply the stat editor's edit files (``StatEditor/apply.py``, ``cli.py --apply``, the ``stat_edits`` slot op).

Synthetic DOLs as in ``test_stat_editor_carry`` (the roster steps run on
``image_for_grid``: row i of every per-ID table holds the byte i, stock
chemistry (a + b) % 4).
"""

import hashlib
import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools import gui_grid
from SluggiesTools.Dol import dolfile
from SluggiesTools.Roster import slot_plan
from SluggiesTools.StatEditor import apply, bridge, carry, cli, fields
from SluggiesTools.tests.test_stat_editor_carry import EXPANDED, Editor, copy, vanilla
from SluggiesTools.tests.test_roster_voice_stats_reassign import STATE_FILE, NAMES, make_config, make_state
from SluggiesTools.tests.test_roster_voice_stats_reassign import build as build_roster

BATTING = fields.character_field('stats', 'batting arm')
SPEED = fields.character_field('stats', 'speed')                       # u16, stats row
STAMINA = fields.character_field('stats', 'stamina')                   # u16, own table
TRAJ = fields.character_field('stats', 'traj')                         # u8, traj table
STAR_PITCH_TYPE = fields.character_field('stats', 'star pitch type')   # u8, starpitch table
WEIGHT = fields.character_field('stats', 'weight')                     # editor range 0-4
FIELDING = fields.character_field('stats', 'fielding ability')
BASERUNNING = fields.character_field('stats', 'baserunning ability')
GAMEPLAY = fields.character_field('size', 'Gameplay')                  # f32, body table
HITBOX = fields.character_field('size', 'height#13')
CHARGE = fields.character_field('pitching', 'charge')
SINGLE = fields.global_field('team_stars', 'Fireballs', 'single')      # s16
FIELDING_SPEED = fields.global_field('speed', '3', 'Fielding')         # f32
BOOST_OP = fields.global_field('star_boost', 'curve', 'add/mult')      # u32 op
HANDICAP = fields.global_field('handicap_params', '1', '2')            # u8 in code


def doc(image: dolfile.DolImage, characters=None, globals_=None, **header) -> dict:
    out = {'format': apply.FORMAT, 'version': apply.VERSION, 'main_dol_sha1': hashlib.sha1(image.data).hexdigest(),
           'characters': characters or {}, 'globals': globals_ or {}}
    out.update(header)
    return out


def applied(image: dolfile.DolImage, *documents) -> tuple[dolfile.DolImage, apply.Prepared]:
    out = copy(image)
    prepared = apply.prepare(out, list(documents))
    apply.write(out, prepared)
    return out, prepared


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.image = build_roster(EXPANDED)[0]

    def test_every_field_kind(self):
        d = doc(self.image, {
            '0x05': {'stats': {'batting arm': 40, 'speed': 0x1234, 'stamina': 300, 'traj': 2, 'star pitch type': 3},
                     'size': {'Gameplay': 1.25, 'height#13': -0.5}, 'pitching': {'charge': 2.5}},
            '0x66': {'stats': {'batting arm': 41}}},
            {'team_stars': {'Fireballs': {'single': -30}}, 'speed': {'3': {'Fielding': 1.5}},
             'star_boost': {'curve': {'add/mult': 2}}, 'handicap_params': {'1': {'2': 9}}})
        out, prepared = applied(self.image, d)
        ed = Editor(out)
        for cid, f, value in ((0x05, BATTING, 40), (0x05, SPEED, 0x1234), (0x05, STAMINA, 300), (0x05, TRAJ, 2),
                              (0x05, STAR_PITCH_TYPE, 3), (0x05, GAMEPLAY, 1.25), (0x05, HITBOX, -0.5),
                              (0x05, CHARGE, 2.5), (0x66, BATTING, 41)):
            with self.subTest(field=f.name, cid=cid):
                self.assertEqual(ed.get(cid, f), value)
        for g, value in ((SINGLE, -30), (FIELDING_SPEED, 1.5), (BOOST_OP, 2), (HANDICAP, 9)):
            self.assertEqual(g.unpack(out.read(g.address, g.size)), value, g.table)
        self.assertEqual(len(prepared.changes), 13)
        self.assertEqual((prepared.characters, prepared.global_values), ([0x05, 0x66], 4))
        detected = carry.detect(out, vanilla())
        self.assertEqual(detected.rows[0x66], {BATTING: bytes([41])})
        self.assertEqual(len(detected.globals), 4)

    def test_chemistry_regions(self):
        d = doc(self.image, {'0x01': {'chemistry': {'0x02': 2}},       # stock x stock: directional
                             '0x03': {'chemistry': {'0x66': 2}},       # stock x new: the new row's byte
                             '0x66': {'chemistry': {'0x67': 2}}})      # new x new: the matrix
        out, prepared = applied(self.image, d)
        stats = Editor(out).layouts['stats']
        self.assertEqual(out.read(stats.row_address(0x01) + fields.chemistry_offset(0x02), 1), b'\x02')
        self.assertEqual(out.read(stats.row_address(0x02) + fields.chemistry_offset(0x01), 1), b'\x03')   # stays
        self.assertEqual(out.read(stats.row_address(0x66) + fields.chemistry_offset(0x03), 1), b'\x02')
        self.assertEqual(carry.detect(out, vanilla()).chemistry, {(0x01, 0x02): 2, (0x66, 0x03): 2, (0x66, 0x67): 2})
        self.assertEqual(len(prepared.changes), 3)

    def test_stock_by_new_both_directions(self):
        same = doc(self.image, {'0x03': {'chemistry': {'0x66': 2}}, '0x66': {'chemistry': {'0x03': 2}}})
        self.assertEqual(len(apply.prepare(copy(self.image), [same]).changes), 1)
        differ = doc(self.image, {'0x03': {'chemistry': {'0x66': 2}}, '0x66': {'chemistry': {'0x03': 1}}})
        with self.assertRaisesRegex(apply.EditFileError, 'one byte'):
            apply.prepare(copy(self.image), [differ])

    def test_refusals(self):
        cases = {
            'hash': doc(self.image, main_dol_sha1='0' * 40),
            'format': doc(self.image, format='sluggies-stat-bridge'),
            'version': doc(self.image, version=2),
            'unknown ID': doc(self.image, {'0x70': {'stats': {'batting arm': 1}}}),
            'not an ID': doc(self.image, {'Mario': {'stats': {'batting arm': 1}}}),
            'unknown field': doc(self.image, {'0x01': {'stats': {'batting': 1}}}),
            'unknown group': doc(self.image, {'0x01': {'colours': {'x': 1}}}),
            'chemistry column': doc(self.image, {'0x01': {'chemistry': {'0x70': 1}}}),
            'chemistry 3': doc(self.image, {'0x01': {'chemistry': {'0x02': 3}}}),
            'u8 overflow': doc(self.image, {'0x01': {'stats': {'batting arm': 256}}}),
            'float for u8': doc(self.image, {'0x01': {'stats': {'batting arm': 1.5}}}),
            'bool': doc(self.image, {'0x01': {'stats': {'captain': True}}}),
            'f32 nan': doc(self.image, {'0x01': {'size': {'Gameplay': 'nan'}}}),
            'boost op': doc(self.image, globals_={'star_boost': {'curve': {'add/mult': 3}}}),
            'unknown table': doc(self.image, globals_={'stars': {'a': {'b': 1}}}),
            'unknown row': doc(self.image, globals_={'team_stars': {'Nobody': {'single': 1}}}),
        }
        for name, d in cases.items():
            with self.subTest(name), self.assertRaises(apply.EditFileError):
                apply.prepare(copy(self.image), [d])
        vanilla_image = vanilla()                     # no new IDs: 0x66 does not exist
        with self.assertRaisesRegex(apply.EditFileError, 'not a character of this roster'):
            apply.prepare(vanilla_image, [doc(vanilla_image, {'0x66': {'stats': {'batting arm': 1}}})])

    def test_values_held_already_are_not_written(self):
        ed = Editor(self.image)
        d = doc(self.image, {'0x05': {'stats': {'batting arm': ed.get(0x05, BATTING), 'speed': 7}}})
        prepared = apply.prepare(copy(self.image), [d])
        self.assertEqual((len(prepared.changes), prepared.same), (1, 1))
        empty = apply.prepare(copy(self.image), [doc(self.image)])
        self.assertEqual((empty.changes, empty.same), ([], 0))

    def test_several_files_later_wins_and_all_need_the_same_dol(self):
        first = doc(self.image, {'0x05': {'stats': {'batting arm': 40, 'speed': 9}}})
        second = doc(self.image, {'0x05': {'stats': {'batting arm': 50}}})
        out, _prepared = applied(self.image, first, second)
        self.assertEqual((Editor(out).get(0x05, BATTING), Editor(out).get(0x05, SPEED)), (50, 9))
        with self.assertRaisesRegex(apply.EditFileError, 'another state of main.dol'):
            apply.prepare(copy(out), [doc(out), second])

    def test_warnings(self):
        d = doc(self.image, {'0x00': {'stats': {'fielding ability': 3, 'baserunning ability': 2}},   # row 0: both 0
                             '0x01': {'stats': {'fielding ability': 0}},                              # clears one
                             '0x05': {'stats': {'weight': 9}},
                             '0x06': {'pitching': {'charge': -1.0}}})
        prepared = apply.prepare(copy(self.image), [d], names={0x00: 'Mario'})
        text = '\n'.join(prepared.warnings)
        self.assertIn('Mario (0x00): fielding ability 3 and baserunning ability 2', text)
        self.assertNotIn('0x01', text)
        self.assertIn('0x05: stats.weight: 9 is outside the stat editor\'s range (0-4)', text)
        self.assertIn('0x06: pitching.charge: -1.0 is outside the stat editor\'s range (0 or more)', text)

    def test_describe(self):
        d = doc(self.image, {'0x05': {'stats': {'batting arm': 40, 'speed': 9}, 'size': {'Gameplay': 1.1},
                                      'chemistry': {'0x01': 2}}},
                {'team_stars': {'Fireballs': {'single': 30}}})
        prepared = apply.prepare(copy(self.image), [d], names={0x01: 'Luigi', 0x05: 'Peach'})
        lines = apply.describe(prepared, {0x01: 'Luigi', 0x05: 'Peach'})
        self.assertTrue(lines[0].startswith('Peach (0x05): chemistry: Luigi (0x01) '), lines)
        self.assertTrue(any(line.startswith('Peach (0x05): size: Gameplay ') and line.endswith('-> 1.1')
                            for line in lines), lines)
        self.assertIn('Peach (0x05): stats: batting arm 10 -> 40, speed 2570 -> 9', lines)
        self.assertEqual(lines[-1], 'global: team_stars: Fireballs / single 0 -> 30')
        self.assertEqual(apply.summary(prepared), '5 values on 1 character (1 global)')

    def test_round_trip_through_the_bridge(self):
        b = bridge.build(self.image, vanilla())
        d = {'format': apply.FORMAT, 'version': apply.VERSION, 'main_dol_sha1': b['files']['main_dol_sha1'],
             'characters': {'0x66': {'stats': {'speed': 0x0203}, 'size': {'Gameplay': 0.75}}}, 'globals': {}}
        out, _prepared = applied(self.image, d)
        again = bridge.build(out, vanilla())
        self.assertNotEqual(again['files']['main_dol_sha1'], b['files']['main_dol_sha1'])
        stats = again['tables']['stats']
        at = stats['file_offset'] + stats['header'] + stats['row_size'] * 0x66 + SPEED.offset
        self.assertEqual(bytes(out.data[at:at + 2]), b'\x02\x03')
        self.assertEqual(carry.detect(out, vanilla()).rows,
                         {0x66: {SPEED: b'\x02\x03', GAMEPLAY: struct.pack('>f', 0.75)}})


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.image = build_roster(EXPANDED)[0]
        with open(os.path.join(self.tmp, 'main.dol'), 'wb') as f:
            f.write(self.image.to_bytes())

    def edit_file(self, d: dict, name='stat_edits.json') -> str:
        path = os.path.join(self.tmp, name)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        return path

    def current(self) -> bytes:
        with open(os.path.join(self.tmp, 'main.dol'), 'rb') as f:
            return f.read()

    def test_dry_run_then_write(self):
        path = self.edit_file(doc(self.image, {'0x05': {'stats': {'batting arm': 40}}}))
        self.assertEqual(cli.main(['--apply', path, '--dry-run', '--output-dir', self.tmp]), 0)
        self.assertEqual(self.current(), self.image.to_bytes())
        self.assertEqual(cli.main(['--apply', path, '--output-dir', self.tmp]), 0)
        self.assertEqual(Editor(dolfile.DolImage(self.current())).get(0x05, BATTING), 40)
        self.assertEqual(cli.main(['--apply', path, '--output-dir', self.tmp]), 1)     # stale now: refused

    def test_a_refused_file_writes_nothing(self):
        good = self.edit_file(doc(self.image, {'0x05': {'stats': {'batting arm': 40}}}), 'a.json')
        bad = self.edit_file(doc(self.image, {'0x05': {'stats': {'batting arm': 400}}}), 'b.json')
        self.assertEqual(cli.main(['--apply', good, bad, '--output-dir', self.tmp]), 1)
        self.assertEqual(self.current(), self.image.to_bytes())

    def test_dispatcher(self):
        import start
        with mock.patch('start.subprocess.run', return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(start.run_apply_stat_edits(['a.json', 'b.json'], dry_run=True))
        self.assertEqual(run.call_args.args[0][-4:], ['--apply', os.path.abspath('a.json'), os.path.abspath('b.json'),
                                                      '--dry-run'])


class StatEnv(slot_plan.Env):
    def __init__(self, checks: dict):
        self.checks = checks

    def stat_edits(self, path):
        check = self.checks[path]
        if isinstance(check, str):
            raise slot_plan.PlanError(check)
        return check


class SlotOpTests(unittest.TestCase):
    CHANGED = slot_plan.StatCheck('2 values on 1 character', ['Mario (0x00): stats: batting arm 1 -> 2'],
                                  ['a warning'])

    def plan(self, items, checks):
        return slot_plan.plan_batch(make_state(), make_config(), slot_plan.parse_edits(items), StatEnv(checks),
                                    STATE_FILE, NAMES)

    def test_one_step_before_the_rebuild(self):
        b = self.plan([{'op': 'stat_edits', 'file': 'a.json'}, {'op': 'stats', 'id': '0x0D', 'source': '0x09'},
                       {'op': 'stat_edits', 'file': 'b.json'}],
                      {'a.json': self.CHANGED, 'b.json': self.CHANGED})
        self.assertTrue(b.ok, b.refused)
        self.assertEqual(b.commands, [('--apply-stat-edits', 'a.json', 'b.json'), ('--roster', '--state', STATE_FILE),
                                      ('--roster-state',)])
        self.assertEqual(b.warnings, ['a warning', 'a warning'])
        section = b.to_json()['edits'][0]
        self.assertEqual((section['action'], section['target'], section['effects']),
                         ('stat_edits', '0xFF', {'stat_edits': '2 values on 1 character'}))
        self.assertEqual(section['edit'], {'op': 'stat_edits', 'id': '0xFF', 'file': 'a.json'})

    def test_nothing_and_refused(self):
        b = self.plan([{'op': 'stat_edits', 'file': 'a.json'}], {'a.json': slot_plan.StatCheck(None)})
        self.assertEqual((b.commands, len(b.skipped)), ([], 1))
        b = self.plan([{'op': 'stat_edits', 'file': 'a.json'}, {'op': 'stats', 'id': '0x0D', 'source': '0x09'}],
                      {'a.json': 'made for another state of main.dol'})
        self.assertFalse(b.ok)
        self.assertEqual(b.commands, [])
        with self.assertRaises(slot_plan.PlanError):
            slot_plan.parse_edits([{'op': 'stat_edits'}])

    def test_a_clear_keeps_stat_edits(self):
        merged, _notes, _refused = slot_plan.merge_edits(slot_plan.parse_edits([
            {'op': 'stat_edits', 'file': 'a.json'}, {'op': 'clear', 'id': '0x66'}]))
        self.assertEqual([e.op for e in merged], ['stat_edits', 'clear'])

    def test_gui_constants_and_texts(self):
        self.assertEqual((gui_grid.STAT_EDITS, gui_grid.GAME_WIDE), (slot_plan.STAT_EDITS, slot_plan.GAME_WIDE))
        self.assertEqual(gui_grid.edit_who(None, slot_plan.GAME_WIDE), 'Stat edits')
        pending = gui_grid.PendingEdits()
        pending.edits = [{'op': 'stat_edits', 'id': '0xFF', 'file': 'C:/x/stat_edits.json'}]
        self.assertEqual(pending.titles(), [('0xFF', 'stat editor values from stat_edits.json')])
        self.assertFalse(pending.has(0x00))


if __name__ == '__main__':
    unittest.main()
