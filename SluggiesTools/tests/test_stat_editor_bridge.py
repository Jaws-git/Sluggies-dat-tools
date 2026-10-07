"""Stat editor bridge export (``StatEditor/fields.py``, ``bridge.py``, ``cli.py``).

Synthetic DOLs as in ``test_roster_ids`` (row i of every per-ID table holds
the byte i, stock chemistry (a + b) % 4) and ``test_roster_voice_stats_reassign``
(the roster steps run on ``image_for_grid``). The real editor and game files
are checked by the hand-run ``probe_stat_editor_layout.py``.
"""

import base64
import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools.Dol import dolfile, inventory
from SluggiesTools.Roster import ids
from SluggiesTools.StatEditor import bridge, cli, fields
from SluggiesTools.tests.test_roster_grid import image_for_grid
from SluggiesTools.tests.test_roster_ids import fresh_image
from SluggiesTools.tests.test_roster_voice_stats_reassign import build as build_roster

EXPANDED = {'ids': [{'id': '0x66', 'template': '0x00', 'wheel': '0x00', 'stats': '0x0D'},
                    {'id': '0x67', 'template': '0x04', 'wheel': '0x04'}],
            'stock_stats': [{'id': '0x05', 'stats': '0x0A'}]}


def live_row(image, layout, cid):
    return image.read(layout.row_address(cid), layout.row_size)


class FieldTableTests(unittest.TestCase):
    def test_keys_are_unique_and_follow_the_editor_lists(self):
        self.assertEqual(fields.keys(['a', 'b', 'a']), ('a#0', 'b', 'a#2'))
        self.assertEqual(len(fields.BY_GROUP['stats']), len(fields.STATS_LIST))
        self.assertEqual(len(fields.BY_GROUP['pitching']), len(fields.PITCHING_LIST))
        self.assertEqual(len(fields.BY_GROUP['size']), len(fields.SIZE_LIST))
        self.assertIn('height#5', fields.BY_GROUP['size'])
        self.assertIn('height#13', fields.BY_GROUP['size'])
        self.assertEqual(len(fields.GLOBAL_LOOKUP), len(fields.GLOBAL_FIELDS))
        self.assertIn(('team_stars', 'Fireballs', 'single'), fields.GLOBAL_LOOKUP)
        self.assertIn(('team_stars', 'Rookies', '???#40'), fields.GLOBAL_LOOKUP)

    def test_character_fields_fit_their_rows_and_never_overlap(self):
        used: dict[str, set] = {}
        for f in fields.CHARACTER_FIELDS:
            table = inventory.table(f.table)
            span = set(range(f.offset, f.offset + f.size))
            self.assertLessEqual(f.offset + f.size, table.row_size, f.name)
            self.assertFalse(span & used.setdefault(f.table, set()), f.name)
            used[f.table] |= span
        self.assertEqual(used['stats'], set(range(2, fields.CHEM_BASE)))     # bytes 0-1 (the row's ID) stay out
        for name in ('pitchwindup', 'changeup', 'catchrange', 'hitbox', 'sizescale', 'traj', 'stamina', 'starpitch'):
            self.assertEqual(used[name], set(range(inventory.table(name).row_size)), name)
        self.assertEqual(fields.chemistry_offset(0x64) + 1, fields.CHEM_BASE + fields.CHEM_COLUMNS)
        with self.assertRaises(fields.FieldError):
            fields.chemistry_offset(0x65)

    def test_global_fields_never_overlap(self):
        spans = sorted((f.address, f.address + f.size, f.table) for f in fields.GLOBAL_FIELDS)
        for (_a, end, t1), (start, _b, t2) in zip(spans, spans[1:]):
            self.assertLessEqual(end, start, (t1, t2))
        for t in fields.GLOBAL_TABLES:
            mine = [f for f in fields.GLOBAL_FIELDS if f.table == t.key]
            self.assertEqual(len(mine), len(t.rows) * len(t.columns), t.key)
            if t.size:
                self.assertEqual(sum(f.size for f in mine), t.size + (4 * fields.SPEED_STEPS if t.key == 'speed'
                                                                     else 0), t.key)

    def test_value_ranges(self):
        u16, s16, f32 = fields.Value('u16'), fields.Value('s16'), fields.Value('f32')
        self.assertEqual(u16.pack(0x1234), b'\x12\x34')
        self.assertEqual(s16.unpack(s16.pack(-3)), -3)
        self.assertEqual(f32.unpack(f32.pack(1.5)), 1.5)
        for value, field in ((0x10000, u16), (-1, u16), (1.0, u16), (True, u16), (float('nan'), f32),
                             (1e39, f32), ('1', f32)):
            with self.subTest(value=value, kind=field.kind):
                self.assertIsNotNone(field.problem(value))
                with self.assertRaises(fields.FieldError):
                    field.pack(value)
        op = fields.global_field('star_boost', 'curve', 'add/mult')
        self.assertIsNone(op.problem(2))
        self.assertIsNotNone(op.problem(3))
        with self.assertRaises(fields.FieldError):
            fields.character_field('stats', 'nope')


class StockBridgeTests(unittest.TestCase):
    def test_nothing_moved(self):
        image = fresh_image()
        b = bridge.build(image, fresh_image(), dol_path='x/main.dol')
        self.assertEqual(b['format'], 'sluggies-stat-bridge')
        self.assertEqual(len(b['characters']), 101)
        self.assertTrue(all(c['kind'] == 'stock' and c['name'] is None for c in b['characters']))
        for name, t in b['tables'].items():
            table = inventory.table(name)
            self.assertEqual((int(t['address'], 16), t['rows'], t['moved']), (table.address, 101, False), name)
        self.assertIsNone(b['chemistry']['new_x_new'])
        self.assertEqual(b['baseline'], {})
        self.assertEqual(b['files']['main_dol_sha1'], __import__('hashlib').sha1(image.data).hexdigest())

    def test_globals_and_sections(self):
        image = fresh_image()
        b = bridge.build(image, fresh_image())
        self.assertEqual(b['globals']['speed']['Fielding'],
                         {'address': '0x80625898', 'file_offset': image.offset_of(fields.SPEED_FIELDING)})
        self.assertEqual(b['globals']['handicap_params'][1][2]['address'], '0x8018098B')
        self.assertEqual(b['globals']['team_stars']['size'], 12 * 41 * 2)
        for s in b['dol_sections']:
            self.assertEqual(image.offset_of(int(s['address'], 16)), s['file_offset'])


class ExpandedBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, _ctx = build_roster(EXPANDED)
        cls.vanilla = image_for_grid()
        cls.bridge = bridge.build(cls.image, cls.vanilla, focus=0x66)
        cls.layouts = bridge.table_layouts(cls.image, bridge.read_roster(cls.image))

    def test_tables_moved_with_256_rows(self):
        for name, t in self.bridge['tables'].items():
            self.assertTrue(t['moved'], name)
            self.assertEqual(t['rows'], ids.ROWS, name)
            self.assertNotEqual(int(t['address'], 16), inventory.table(name).address)

    def test_offsets_read_the_same_bytes_as_the_addresses(self):
        raw = bytes(self.image.data)
        for name, t in self.bridge['tables'].items():
            size = t['header'] + t['row_size'] * t['rows']
            self.assertEqual(raw[t['file_offset']:t['file_offset'] + size],
                             self.image.read(int(t['address'], 16), size), name)
        nn = self.bridge['chemistry']['new_x_new']
        self.assertEqual(raw[nn['file_offset']:nn['file_offset'] + nn['size'] ** 2],
                         self.image.read(int(nn['address'], 16), nn['size'] ** 2))

    def test_characters(self):
        chars = {c['id']: c for c in self.bridge['characters']}
        self.assertEqual(len(chars), 103)
        self.assertEqual((chars['0x66']['kind'], chars['0x66']['stats_source'], chars['0x66']['model_source']),
                         ('new', '0x0D', '0x00'))
        self.assertEqual(chars['0x67']['stats_source'], '0x04')
        self.assertEqual((chars['0x05']['kind'], chars['0x05']['stats_source']), ('stock', '0x0A'))
        self.assertEqual(self.bridge['focus'], '0x66')

    def test_new_by_new_matrix(self):
        nn = self.bridge['chemistry']['new_x_new']
        self.assertEqual((nn['first_id'], nn['size']), ('0x66', ids.ID_BOUND - ids.FIRST_NEW + 1))
        matrix = self.image.read(int(nn['address'], 16), nn['size'] ** 2)
        self.assertEqual(matrix[0 * nn['size'] + 1], (0x0D + 0x04) % 4)    # 0x66 (stats 0x0D) -> 0x67 (0x04)
        self.assertEqual(matrix, base64.b64decode(self.bridge['baseline']['new_x_new']))

    def test_baseline_is_the_roster_without_edits(self):
        base = self.bridge['baseline']
        for name, layout in self.layouts.items():
            for c in self.bridge['characters']:
                cid = int(c['id'], 16)
                entry = base.get(name, {}).get(c['id'])
                want = base64.b64decode(entry) if entry else self.vanilla.read(
                    inventory.table(name).address + layout.header + layout.row_size * cid, layout.row_size)
                self.assertEqual(live_row(self.image, layout, cid), want, (name, c['id']))

    def test_baseline_lists_only_changed_stock_rows(self):
        base = self.bridge['baseline']
        self.assertEqual(set(base['hitbox']), {'0x66', '0x67'})              # body rows: new IDs only
        self.assertIn('0x05', base['pitchwindup'])                           # stock_stats
        self.assertNotIn('0x01', base['pitchwindup'])
        self.assertIn('0x01', base['stats'])                                 # its chemistry towards 0x05 changed
        row = base64.b64decode(base['stats']['0x66'])
        self.assertEqual(struct.unpack_from('>H', row)[0], 0x66)
        self.assertEqual(row[2], 0x0D)                                       # stats from the stats source

    def test_focus_must_exist(self):
        with self.assertRaises(bridge.BridgeError):
            bridge.build(self.image, self.vanilla, focus=0x70)

    def test_missing_chemistry_hook_is_refused(self):
        image = dolfile.DolImage(self.image.data)
        image.write_word(bridge.CHEMISTRY_HOOK, inventory.site('chemistry_hook', bridge.CHEMISTRY_HOOK).stock)
        with self.assertRaises(bridge.BridgeError):
            bridge.build(image, self.vanilla)


class IdentityRelocationTests(unittest.TestCase):
    def test_moved_but_101_rows_and_no_matrix(self):
        image, _ctx = build_roster({'ids': []})
        b = bridge.build(image, image_for_grid())
        self.assertTrue(all(t['moved'] and t['rows'] == ids.STOCK_IDS for t in b['tables'].values()))
        self.assertIsNone(b['chemistry']['new_x_new'])
        self.assertEqual(b['baseline'], {})


class CliTests(unittest.TestCase):
    def test_export_writes_the_bridge(self):
        image, _ctx = build_roster(EXPANDED)
        with tempfile.TemporaryDirectory() as tmp:
            out, inp = os.path.join(tmp, 'out'), os.path.join(tmp, 'in')
            for folder, dol in ((out, image), (inp, image_for_grid())):
                os.makedirs(folder)
                with open(os.path.join(folder, 'main.dol'), 'wb') as f:
                    f.write(dol.to_bytes())
            target = os.path.join(tmp, 'Bridge', 'stat_bridge.json')
            self.assertEqual(cli.main(['--export', target, '--focus', '0x67', '--output-dir', out,
                                       '--input-dir', inp]), 0)
            with open(target, encoding='utf-8') as f:
                b = json.load(f)
            self.assertEqual((b['focus'], b['files']['dt_na_dat']), ('0x67', ''))
            self.assertEqual(b['files']['main_dol'], os.path.abspath(os.path.join(out, 'main.dol')))
            self.assertEqual(cli.main(['--export', target, '--output-dir', tmp, '--input-dir', inp]), 1)

    def test_dispatcher(self):
        import start
        with mock.patch('start.subprocess.run', return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(start.run_stat_bridge_export('b.json', focus='0x66'))
        cmd = run.call_args.args[0]
        self.assertTrue(cmd[-5].endswith(os.path.join('StatEditor', 'cli.py')) or '--_run-script' in cmd)
        self.assertEqual(cmd[-4:], ['--export', os.path.abspath('b.json'), '--focus', '0x66'])


if __name__ == '__main__':
    unittest.main()
