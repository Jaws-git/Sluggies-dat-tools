"""Stats sources and square voices.

* ``ids[].stats``: the stats rows come from the stats source, the body rows
  (size and effect scales) from the model source, the selector row and the
  own-data flag from the template.
* ``grid.squares[].voice``: a voiced square's square-only new IDs get the
  voice's species (selector byte 2); every other row stays as it was.

Synthetic DOLs as in ``test_roster_ids`` (row i of every table holds the
byte i, selector species i) and ``test_roster_state``.
"""

import struct
import unittest

from SluggiesTools.Dol import inventory
from SluggiesTools.Roster import grid, ids, manifest, state
from SluggiesTools.tests.test_roster_ids import moved_to, run
from SluggiesTools.tests.test_roster_state import SQUARES_12X5, build


def row(image, name, cid):
    table = inventory.table(name)
    base = moved_to(image, table) + table.header
    return image.read(base + cid * table.row_size, table.row_size)


STATS_TABLES = [t.name for t in ids.moved_tables()
                if t.name not in ids.BODY_TABLES | ids.TEMPLATE_TABLES | {ids.HANDLE_TABLE}]


class StatsSourceTests(unittest.TestCase):
    CONFIG = {'ids': [{'id': '0x66', 'template': '0x00', 'stats': '0x09'},
                      {'id': '0x67', 'template': '0x00', 'model': {'from': '0x0D'}},
                      {'id': '0x68', 'template': '0x00', 'model': {'from': '0x09'}, 'stats': '0x0D'},
                      {'id': '0x69', 'template': '0x00'}]}

    @classmethod
    def setUpClass(cls):
        cls.image, cls.hs, cls.log = run(cls.CONFIG)

    def sources(self, cid):
        """{table: the byte its row repeats} (stats: the bytes after the ID, before the chemistry)."""
        out = {}
        for name in STATS_TABLES + sorted(ids.BODY_TABLES):
            r = row(self.image, name, cid)
            r = r[2:ids.CHEM_BASE] if name == 'stats' else r
            self.assertEqual(len(set(r)), 1, f'{name} of 0x{cid:02X} mixes rows')
            out[name] = r[0]
        return out

    def test_split_by_source(self):
        for cid, stats, body in ((0x66, 0x09, 0x00), (0x67, 0x00, 0x0D), (0x68, 0x0D, 0x09), (0x69, 0x00, 0x00)):
            with self.subTest(f'0x{cid:02X}'):
                got = self.sources(cid)
                self.assertEqual({n: got[n] for n in STATS_TABLES}, dict.fromkeys(STATS_TABLES, stats))
                self.assertEqual({n: got[n] for n in ids.BODY_TABLES}, dict.fromkeys(ids.BODY_TABLES, body))
                self.assertEqual(row(self.image, 'hasmodel', cid), bytes([0]))           # the template's
                self.assertEqual(row(self.image, 'selector', cid)[1:3], bytes([0, 0]))   # Mario's host and species

    def test_stats_row_keeps_its_id_and_takes_the_source_chemistry(self):
        stats = row(self.image, 'stats', 0x66)
        self.assertEqual(struct.unpack_from('>H', stats)[0], 0x66)
        self.assertEqual(stats[ids.CHEM_BASE:ids.CHEM_BASE + 4], bytes((9 + j) % 4 for j in range(4)))

    def test_new_by_new_chemistry_follows_the_stats_sources(self):
        stats = inventory.table('stats')
        blob = self.image.read(moved_to(self.image, stats), stats.header + ids.ROWS * ids.STATS_ROW)
        table = ids.new_by_new_chemistry(blob, stats.header, ids.parse_ids(self.CONFIG))
        n = ids.ID_BOUND - ids.FIRST_NEW + 1
        self.assertEqual(table[(0x66 - 0x66) * n + (0x68 - 0x66)], (9 + 0x0D) % 4)   # Bowser's row, 0x0D's column
        self.assertEqual(table[(0x69 - 0x66) * n + (0x66 - 0x66)], (0 + 9) % 4)

    def test_stock_rows_untouched(self):
        for name in STATS_TABLES + sorted(ids.BODY_TABLES):
            for cid in (0x00, 0x09, 0x0D):
                r = row(self.image, name, cid)
                self.assertEqual(set(r[2:ids.CHEM_BASE] if name == 'stats' else r), {cid}, name)

    def test_without_stats_or_model_the_bytes_are_unchanged(self):
        plain = {'ids': [{'id': '0x66', 'template': '0x00'}, {'id': '0x67', 'template': '0x06'}]}
        same = {'ids': [{'id': '0x66', 'template': '0x00', 'stats': '0x00'}, {'id': '0x67', 'template': '0x06'}]}
        self.assertEqual(run(plain)[0].to_bytes(), run(same)[0].to_bytes())

    def test_refusals(self):
        for bad in ('0x4D', '0x66', 'Bowser'):
            with self.subTest(bad), self.assertRaises(ids.IdConfigError):
                ids.parse_ids({'ids': [{'template': 0, 'stats': bad}]})


VOICED = {
    'ids': [{'id': '0x66', 'template': '0x06', 'wheel': None},
            {'id': '0x67', 'template': '0x00', 'wheel': None, 'stats': '0x0D'},
            {'id': '0x68', 'template': '0x00', 'wheel': '0x06'},
            {'id': '0x69', 'template': '0x06', 'wheel': None}],
    'wheels': [{'id': '0x47', 'wheel': '0x06'}],
    'grid': {'squares': [{'members': ['0x66', '0x67', '0x68', '0x47'], 'voice': '0x09'}, ['0x69']],
             'shape': [12, 5]},
}


class SquareVoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.ctx = build(VOICED)
        cls.plain, _ = build({**VOICED, 'grid': {'squares': [['0x66', '0x67', '0x68', '0x47'], ['0x69']],
                                                 'shape': [12, 5]}})

    def species(self, image, cid):
        return row(image, 'selector', cid)[2]

    def test_square_only_members_take_the_voice(self):
        self.assertEqual([self.species(self.image, c) for c in (0x66, 0x67)], [0x09, 0x09])
        self.assertEqual(self.species(self.image, 0x68), 0x06)      # on a wheel: keeps its wheel's species
        self.assertEqual(self.species(self.image, 0x47), 0x06)      # a spare row: on Yoshi's species list
        self.assertEqual(self.species(self.image, 0x69), 0x06)      # an unvoiced square

    def test_only_byte_2_of_the_voiced_rows_changes(self):
        for cid in range(ids.ROWS):
            a, b = row(self.image, 'selector', cid), row(self.plain, 'selector', cid)
            if cid in (0x66, 0x67):
                self.assertEqual(a[:2] + a[3:], b[:2] + b[3:])
            else:
                self.assertEqual(a, b, f'0x{cid:02X}')

    def test_state_and_manifest(self):
        result = state.read_state(self.image)
        new = [sq for sq in result['squares'] if sq['kind'] == 'new']
        self.assertEqual([(sq['voice'], sq['voice_set']) for sq in new], [(0x09, 0x09), (0x06, None)])
        stats = {c['id']: c['stats'] for c in result['characters']}
        self.assertEqual((stats[0x66], stats[0x67]), (0x06, 0x0D))
        self.assertEqual(result['warnings'], [])
        mf = state._manifest(self.image)
        self.assertEqual((mf['grid']['voices'], mf['stats']), ([0x09, None], [[0x67, 0x0D]]))

    def test_configs_without_voices_or_stats_keep_their_manifest(self):
        _image, ctx = build(SQUARES_12X5)
        mf = manifest.build(ctx.state, SQUARES_12X5)
        self.assertNotIn('stats', mf)
        self.assertNotIn('voices', mf['grid'])

    def test_parse(self):
        heads = bytes(range(grid.SQUARE_HEADS))
        stock_map = bytes(range(grid.STOCK_SQUARES))
        g = grid.parse_grid({'grid': {'squares': [{'members': ['0x66'], 'voice': '0x0B'}, ['0x67']]}},
                            heads, stock_map)
        self.assertEqual((g.squares, g.voices), (((0x66,), (0x67,)), (0x0B, None)))
        self.assertEqual(grid.parse_grid({'grid': {'squares': [['0x66']]}}, heads, stock_map).voices, ())
        for bad in ({'members': ['0x66'], 'voice': '0x4D'}, {'members': ['0x66'], 'vioce': '0x00'}, {'voice': 0}):
            with self.subTest(bad), self.assertRaises(grid.GridConfigError):
                grid.parse_grid({'grid': {'squares': [bad]}}, heads, stock_map)
        self.assertEqual(ids.square_member_lists({'grid': {'squares': [{'members': [1]}, [2]]}}), [[1], [2]])

    def test_special_species_warning(self):
        config = {'ids': [{'id': '0x66', 'template': '0x00', 'wheel': None}],
                  'grid': {'squares': [{'members': ['0x66'], 'voice': '0x19'}], 'shape': [12, 5]}}
        _image, ctx = build(config)
        g = ctx.state['grid']
        lines = grid.apply_voices(ctx, g, lambda _a, _b: None)
        self.assertTrue(any(line.startswith('warning: 0x66 has a Magikoopa voice or body') for line in lines), lines)


if __name__ == '__main__':
    unittest.main()
