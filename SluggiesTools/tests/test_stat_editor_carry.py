"""Roster runs keep stat edits (``StatEditor/carry.py``, wired into ``Roster/runner.py``).

Synthetic DOLs as in ``test_stat_editor_bridge``: the roster steps run on
``image_for_grid`` (row i of every per-ID table holds the byte i, stock
chemistry (a + b) % 4). A "run" here is ``detect`` on the old DOL and
``apply`` on a fresh build of the new config, which is what the runner does
around its reset; ``RunnerTests`` goes through ``runner.run`` itself.
"""

import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools.Dol import dolfile, relocate
from SluggiesTools.Roster import ids, runner
from SluggiesTools.StatEditor import bridge, carry, fields
from SluggiesTools.tests.test_game_options import synthetic_dol
from SluggiesTools.tests.test_roster_grid import image_for_grid
from SluggiesTools.tests.test_roster_voice_stats_reassign import build as build_roster
from SluggiesTools.tests.test_roster_voice_stats_reassign import fill_voice_tables

EXPANDED = {'ids': [{'id': '0x66', 'template': '0x00', 'wheel': '0x00', 'stats': '0x0D'},
                    {'id': '0x67', 'template': '0x04', 'wheel': '0x04'}],
            'stock_stats': [{'id': '0x05', 'stats': '0x0A'}]}
ONE_NEW = {'ids': [{'id': '0x66', 'template': '0x00', 'wheel': '0x00', 'stats': '0x0D'}]}

BATTING = fields.character_field('stats', 'batting arm')
PITCHING_ARM = fields.character_field('stats', 'pitching arm')
SPEED = fields.character_field('stats', 'speed')                       # u16
GAMEPLAY = fields.character_field('size', 'Gameplay')                  # f32, a body table
CHARGE = fields.character_field('pitching', 'charge')
SINGLE = fields.global_field('team_stars', 'Fireballs', 'single')
HANDICAP = fields.global_field('handicap_params', '1', '2')


def vanilla() -> dolfile.DolImage:
    image = image_for_grid()
    fill_voice_tables(image)
    return image


def copy(image: dolfile.DolImage) -> dolfile.DolImage:
    return dolfile.DolImage(image.to_bytes())


class Editor:
    """Writes values where the bridge says they are, as the stat editor's edits would end up."""

    def __init__(self, image: dolfile.DolImage):
        self.image = image
        self.roster = bridge.read_roster(image)
        self.layouts = bridge.table_layouts(image, self.roster)

    def set(self, cid: int, f: fields.Field, value) -> None:
        self.image.write(self.layouts[f.table].row_address(cid) + f.offset, f.pack(value))

    def get(self, cid: int, f: fields.Field):
        return f.unpack(self.image.read(self.layouts[f.table].row_address(cid) + f.offset, f.size))

    def chem(self, a: int, b: int, value: int) -> None:
        if a >= ids.FIRST_NEW and b >= ids.FIRST_NEW:
            n = carry.NEW_X_NEW
            at = bridge.new_by_new_address(self.image, self.layouts['stats'])
            self.image.write(at + (a - ids.FIRST_NEW) * n + b - ids.FIRST_NEW, bytes([value]))
        else:
            if b >= ids.FIRST_NEW:
                a, b = b, a                          # stock x new: the new ID's row
            self.image.write(self.layouts['stats'].row_address(a) + fields.chemistry_offset(b), bytes([value]))


def run(old: dolfile.DolImage, config: dict | None) -> tuple[dolfile.DolImage, list[str], carry.StatEdits]:
    edits = carry.detect(old, vanilla())
    new = copy(vanilla()) if config is None else build_roster(config)[0]
    return new, carry.apply(new, edits), edits


class DetectTests(unittest.TestCase):
    def test_unedited_rosters_have_no_edits(self):
        for name, config in (('vanilla', None), ('identity relocation', {'ids': []}), ('expanded', EXPANDED),
                             ('stock stats in place', {'stock_stats': [{'id': '0x05', 'stats': '0x0A'}]})):
            with self.subTest(name):
                image = vanilla() if config is None else build_roster(config)[0]
                self.assertFalse(carry.detect(image, vanilla()))

    def test_fields_chemistry_and_globals(self):
        image = build_roster(EXPANDED)[0]
        ed = Editor(image)
        ed.set(0x05, BATTING, 0x30)
        ed.set(0x66, SPEED, 0x1234)
        ed.set(0x67, GAMEPLAY, 1.25)
        ed.chem(0x01, 0x02, 7)
        ed.chem(0x66, 0x03, 7)
        ed.chem(0x66, 0x67, 7)
        image.write(SINGLE.address, SINGLE.pack(30))
        image.write(HANDICAP.address, HANDICAP.pack(9))
        edits = carry.detect(image, vanilla())
        self.assertEqual(edits.rows, {0x05: {BATTING: b'\x30'}, 0x66: {SPEED: b'\x12\x34'},
                                      0x67: {GAMEPLAY: struct.pack('>f', 1.25)}})
        self.assertEqual(edits.chemistry, {(0x01, 0x02): 7, (0x66, 0x03): 7, (0x66, 0x67): 7})
        self.assertEqual(edits.globals, {SINGLE: SINGLE.pack(30), HANDICAP: b'\x09'})

    def test_unmapped_tables_are_skipped(self):
        bare = dolfile.DolImage(synthetic_dol())        # code and a small data section, no stat tables
        self.assertFalse(carry.detect(bare, bare))


class CarryTests(unittest.TestCase):
    def test_in_place_to_moved(self):
        old = vanilla()
        ed = Editor(old)
        ed.set(0x05, BATTING, 9)
        ed.set(0x01, GAMEPLAY, 1.25)
        ed.set(0x01, CHARGE, -2.5)
        ed.chem(0x01, 0x02, 7)                       # directional: 0x02 -> 0x01 keeps its value
        old.write(SINGLE.address, SINGLE.pack(-30))
        new, log, edits = run(old, EXPANDED)
        self.assertTrue(all(t.moved for t in Editor(new).layouts.values()))
        self.assertEqual(carry.detect(new, vanilla()), edits)
        after = Editor(new)
        self.assertEqual(after.get(0x05, BATTING), 9)
        self.assertEqual(after.get(0x05, PITCHING_ARM), 0x0A)          # the rest from its stats source
        self.assertEqual(SINGLE.unpack(new.read(SINGLE.address, 2)), -30)
        self.assertEqual(log, ['carried: 3 fields on 0x01, 0x05, 1 chemistry values, 1 global values'])

    def test_moved_to_fewer_ids_drops_the_removed_ones(self):
        old = build_roster(EXPANDED)[0]
        ed = Editor(old)
        ed.set(0x66, BATTING, 0x30)
        ed.set(0x67, GAMEPLAY, 2.0)
        ed.chem(0x66, 0x03, 7)
        ed.chem(0x66, 0x67, 7)
        ed.chem(0x67, 0x66, 6)
        new, log, _edits = run(old, ONE_NEW)
        kept = carry.detect(new, vanilla())
        self.assertEqual(kept.rows, {0x66: {BATTING: b'\x30'}})
        self.assertEqual(kept.chemistry, {(0x66, 0x03): 7})
        self.assertEqual(log[-1], 'dropped (IDs no longer in the roster): 0x67')

    def test_stats_source_change_keeps_edited_fields(self):
        old = build_roster(EXPANDED)[0]
        Editor(old).set(0x66, BATTING, 0x30)
        changed = json.loads(json.dumps(EXPANDED))
        changed['ids'][0]['stats'] = '0x0A'
        new, _log, edits = run(old, changed)
        after = Editor(new)
        self.assertEqual(after.get(0x66, BATTING), 0x30)
        self.assertEqual(after.get(0x66, PITCHING_ARM), 0x0A)
        self.assertEqual(carry.detect(new, vanilla()).rows, edits.rows)

    def test_moved_to_vanilla(self):
        old = build_roster(EXPANDED)[0]
        ed = Editor(old)
        ed.set(0x01, SPEED, 0x0102)
        ed.set(0x66, BATTING, 0x30)
        ed.chem(0x66, 0x01, 7)
        new, log, _edits = run(old, None)
        self.assertEqual(carry.detect(new, vanilla()).rows, {0x01: {SPEED: b'\x01\x02'}})
        self.assertFalse(Editor(new).layouts['stats'].moved)
        self.assertEqual(log[-1], 'dropped (IDs no longer in the roster): 0x66')

    def test_same_config_rebuilds_byte_identically(self):
        old = build_roster(EXPANDED)[0]
        ed = Editor(old)
        ed.set(0x05, BATTING, 0x30)
        ed.set(0x66, GAMEPLAY, 3.0)
        ed.chem(0x01, 0x66, 0)
        ed.chem(0x67, 0x66, 7)
        old.write(HANDICAP.address, b'\x05')
        new, _log, _edits = run(old, EXPANDED)
        self.assertEqual(new.to_bytes(), old.to_bytes())

    def test_stock_by_new_pair_is_one_byte(self):
        image = build_roster(EXPANDED)[0]
        ed = Editor(image)
        edits = carry.StatEdits(chemistry={(0x03, 0x66): 5})
        carry.apply(image, edits)
        self.assertEqual(image.read(ed.layouts['stats'].row_address(0x66) + fields.chemistry_offset(0x03), 1), b'\x05')
        with self.assertRaises(bridge.BridgeError):
            carry.apply(image, carry.StatEdits(chemistry={(0x03, 0x66): 5, (0x66, 0x03): 6}))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(relocate, 'scan_refs', return_value=[]))
        self.input_dir = os.path.join(self.tmp, '1_Input')
        os.makedirs(self.input_dir)
        self.dol_path = os.path.join(self.tmp, 'main.dol')
        for path in (self.dol_path, os.path.join(self.input_dir, 'main.dol')):
            with open(path, 'wb') as f:
                f.write(vanilla().to_bytes())

    def config(self, config: dict) -> str:
        path = os.path.join(self.tmp, 'roster.json')
        with open(path, 'w') as f:
            json.dump(config, f)
        return path

    def edit(self, change) -> None:
        with open(self.dol_path, 'rb') as f:
            image = dolfile.DolImage(f.read())
        change(Editor(image))
        with open(self.dol_path, 'wb') as f:
            f.write(image.to_bytes())

    def current(self) -> dolfile.DolImage:
        with open(self.dol_path, 'rb') as f:
            return dolfile.DolImage(f.read())

    def test_edits_survive_roster_runs(self):
        self.edit(lambda ed: ed.set(0x01, BATTING, 0x30))
        report = runner.run(self.tmp, config_path=self.config(EXPANDED), input_dir=self.input_dir)
        self.assertTrue(any(line.startswith('[stat edits] carried: 1 fields on 0x01') for line in report['log']))
        self.edit(lambda ed: ed.set(0x66, SPEED, 0x0203))
        runner.run(self.tmp, config_path=self.config(EXPANDED), input_dir=self.input_dir)
        self.assertEqual(Editor(self.current()).get(0x66, SPEED), 0x0203)
        report = runner.run(self.tmp, remove_only=True, input_dir=self.input_dir)
        self.assertIn('[stat edits] dropped (IDs no longer in the roster): 0x66', report['log'])
        self.assertEqual(carry.detect(self.current(), vanilla()).rows, {0x01: {BATTING: b'\x30'}})


if __name__ == '__main__':
    unittest.main()
