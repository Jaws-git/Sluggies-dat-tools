"""GUI character grid, Phase 7: free voice and stats reassignment.

* ``stock_voices`` (``Roster/voices.py``): a stock square speaks with another
  species' voice by copying that species' voice bank word and clip row; new
  squares' voices pick a species that still speaks with the wanted sounds.
* ``stock_stats`` (``ids`` step): a stock ID plays with another stock
  player's stats rows, with consistent chemistry, in the moved tables or in
  place.
* The planner's ``stats`` / ``voice`` edits, the CLI and the dispatcher.

Synthetic DOLs as in ``test_roster_ids`` (row i of every table holds the
byte i) and ``test_roster_state``; the voice tables get distinct contents
(``fill_voice_tables``).
"""

import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools.Dol import inventory, relocate
from SluggiesTools.Roster import dol_hammerspace as dhs
from SluggiesTools.Roster import grid, ids, layout_file, manifest, runner, slot_cli, slot_plan, state, steps, voices
from SluggiesTools.tests.test_roster_grid import HEADS, image_for_grid
from SluggiesTools.tests.test_roster_ids import fresh_image, moved_to
from SluggiesTools.tests.test_roster_wheels import FakeLayout

STATE_FILE = '/out/_gui/slot/roster.json'


def fill_voice_tables(image) -> None:
    """Species s: voice bank 0x100 + s, clip row of the INFO IDs 0x1000 * (s + 1) + slot."""
    for s in range(voices.SPECIES + 2):
        image.write(voices.GROUPS + 4 * s, struct.pack('>i', 0x100 + s))
        image.write(voices.CLIPS + voices.CLIP_ROW * s,
                    struct.pack('>12I', *(0x1000 * (s + 1) + k for k in range(12))))


def group(image, s):
    return struct.unpack('>i', image.read(voices.GROUPS + 4 * s, 4))[0]


def clip0(image, s):
    return struct.unpack('>I', image.read(voices.CLIPS + voices.CLIP_ROW * s, 4))[0]


def build(config: dict, keys=('dol_hammerspace', 'ids', 'wheels', 'voices', 'grid')):
    image = image_for_grid()
    fill_voice_tables(image)
    ctx = steps.RosterContext(dol=image, dat=None, config=config)
    ctx.state[layout_file.STATE_KEY] = FakeLayout()
    with mock.patch.object(relocate, 'scan_refs', return_value=[]):
        for step in steps.all_steps():
            if step.key in keys:
                step.apply(ctx)
        runner.write_manifest(ctx)
    return ctx.dol, ctx


def species_byte(image, cid):
    table = inventory.table('selector')
    return image.read(moved_to(image, table) + table.header + 8 * cid + 2, 1)[0]


class VoiceTableTests(unittest.TestCase):
    def test_constants_match_the_grid_step(self):
        self.assertEqual((voices.HEAD_LIST, voices.SPECIES), (grid.HEAD_LIST, grid.SQUARE_HEADS))
        self.assertEqual(steps.STEPS.index(('voices', 'Stock square voices')),
                         [k for k, _t in steps.STEPS].index('grid') - 1)

    def test_parse(self):
        heads = HEADS[:voices.SPECIES]
        self.assertEqual(voices.parse_stock_voices({'stock_voices': [{'id': '0x00', 'voice': '0x09'},
                                                                     {'id': '0x0D', 'voice': '0x0D'}]}, heads),
                         {0x00: 0x09})                                        # its own voice drops out
        for bad in ([{'id': '0x16', 'voice': '0x09'}],                        # not a square's head
                    [{'id': '0x00', 'voice': '0x4D'}],
                    [{'id': '0x00'}],
                    [{'id': '0x00', 'voice': '0x09'}, {'id': '0x00', 'voice': '0x01'}],
                    {'id': '0x00'}):
            with self.subTest(bad), self.assertRaises(voices.VoiceConfigError):
                voices.parse_stock_voices({'stock_voices': bad}, heads)

    def test_species_for_voice(self):
        swap = {0: 9, 9: 0}
        self.assertEqual(voices.species_for_voice(9, {}), 9)
        self.assertEqual(voices.species_for_voice(9, swap), 0)                 # the other half of the swap
        self.assertEqual(voices.species_for_voice(9, {9: 0}), None)            # given away, nobody took it
        self.assertEqual(voices.species_for_voice(0, {9: 0}), 0)

    def test_swap_reads_every_row_first(self):
        image = fresh_image()
        fill_voice_tables(image)
        voices.write_remap(image, {0: 9, 9: 0, 0x0D: 9})
        self.assertEqual([group(image, s) for s in (0, 9, 0x0D, 1)], [0x109, 0x100, 0x109, 0x101])
        self.assertEqual([clip0(image, s) for s in (0, 9, 0x0D)], [0xA000, 0x1000, 0xA000])
        self.assertEqual(image.read(voices.CLIPS + voices.CLIP_ROW * 0, voices.CLIP_ROW),
                         image.read(voices.CLIPS + voices.CLIP_ROW * 0x0D, voices.CLIP_ROW))


SWAPPED = {
    'ids': [{'id': '0x66', 'template': '0x06', 'wheel': None}, {'id': '0x67', 'template': '0x00', 'wheel': '0x00'}],
    'stock_voices': [{'id': '0x00', 'voice': '0x09'}, {'id': '0x09', 'voice': '0x00'}],
    'grid': {'squares': [{'members': ['0x66'], 'voice': '0x09'}], 'shape': [12, 5]},
}


class StockVoiceStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.ctx = build(SWAPPED)
        cls.plain, _ = build({k: v for k, v in SWAPPED.items() if k != 'stock_voices'})

    def test_tables_swap_and_nothing_else_changes(self):
        self.assertEqual((group(self.image, 0), group(self.image, 9)), (0x109, 0x100))
        self.assertEqual((clip0(self.image, 0), clip0(self.image, 9)), (0xA000, 0x1000))
        self.assertEqual((group(self.plain, 0), clip0(self.plain, 9)), (0x100, 0xA000))
        for s in (1, 0x0D):
            self.assertEqual((group(self.image, s), clip0(self.image, s)), (group(self.plain, s), clip0(self.plain, s)))
        for cid in (0x00, 0x09, 0x67):
            self.assertEqual(species_byte(self.image, cid), species_byte(self.plain, cid))   # byte 2 stays

    def test_new_square_takes_the_species_that_speaks_with_its_voice(self):
        self.assertEqual(species_byte(self.plain, 0x66), 0x09)
        self.assertEqual(species_byte(self.image, 0x66), 0x00)    # Mario's species speaks with Bowser's voice

    def test_given_away_voice_is_refused(self):
        bad = dict(SWAPPED, stock_voices=[{'id': '0x09', 'voice': '0x00'}])
        with self.assertRaises(grid.GridConfigError):
            build(bad)

    def test_state_and_manifest(self):
        result = state.read_state(self.image)
        by_head = {sq['head']: sq for sq in result['squares']}
        self.assertEqual((by_head[0x00]['voice'], by_head[0x00]['voice_set']), (0x09, 0x09))
        self.assertEqual((by_head[0x09]['voice'], by_head[0x09]['voice_set']), (0x00, 0x00))
        self.assertEqual((by_head[0x0D]['voice'], by_head[0x0D]['voice_set']), (0x0D, None))
        self.assertEqual((by_head[0x66]['voice'], by_head[0x66]['voice_set']), (0x09, 0x09))
        self.assertEqual(result['warnings'], [])
        chars = {c['id']: c for c in result['characters']}
        self.assertEqual((chars[0x67]['family'], chars[0x66]['family']), (0x00, 0x00))
        self.assertEqual(state._manifest(self.image)['stock_voices'], [[0, 9], [9, 0]])

    def test_without_the_key_nothing_is_written(self):
        image, ctx = build({'stock_voices': []})
        self.assertNotIn('stock_voices', manifest.build(ctx.state, {}))
        self.assertEqual(group(image, 0), 0x100)


def stats_row(image, name, cid, moved=True):
    table = inventory.table(name)
    base = (moved_to(image, table) if moved else table.address) + table.header
    return image.read(base + cid * table.row_size, table.row_size)


STATS_TABLES = [t.name for t in ids.moved_tables() if ids.is_stats_table(t.name)]


class StockStatsTests(unittest.TestCase):
    NEW = [{'id': '0x66', 'template': '0x00', 'stats': '0x0D'}, {'id': '0x67', 'template': '0x04'}]
    STOCK = {0x00: 0x09, 0x0D: 0x00}

    @classmethod
    def setUpClass(cls):
        cls.image = fresh_image()
        hs = dhs.DolHammerspace.create(cls.image)
        with mock.patch.object(relocate, 'scan_refs', return_value=[]):
            ids.apply_ids(cls.image, hs, ids.parse_ids({'ids': cls.NEW}), stock_stats=cls.STOCK)
        hs.commit()

    def chem(self, a, b):
        return stats_row(self.image, 'stats', a)[ids.CHEM_BASE + b]

    def test_stats_rows_follow_the_source_body_rows_stay(self):
        for name in STATS_TABLES:
            row = stats_row(self.image, name, 0x00)
            self.assertEqual(set(row[2:ids.CHEM_BASE] if name == 'stats' else row), {0x09}, name)
        for name in ids.BODY_TABLES | {'hasmodel'}:
            self.assertEqual(set(stats_row(self.image, name, 0x00)), {0x00}, name)
        self.assertEqual(stats_row(self.image, 'selector', 0x00)[2], 0x00)
        self.assertEqual(struct.unpack_from('>H', stats_row(self.image, 'stats', 0x00))[0], 0x00)   # own ID kept

    def test_chemistry_reads_the_sources_pair(self):
        vanilla = lambda a, b: (a + b) % 4                          # the synthetic stock chemistry
        self.assertEqual(self.chem(0x00, 0x05), vanilla(0x09, 0x05))    # restatted row
        self.assertEqual(self.chem(0x05, 0x00), vanilla(0x05, 0x09))    # every other row's column
        self.assertEqual(self.chem(0x00, 0x0D), vanilla(0x09, 0x00))    # both restatted
        self.assertEqual(self.chem(0x0D, 0x00), vanilla(0x00, 0x09))
        self.assertEqual(self.chem(0x66, 0x00), vanilla(0x0D, 0x09))    # a new ID's row (stats source 0x0D)
        self.assertEqual(self.chem(0x67, 0x0D), vanilla(0x04, 0x00))

    def test_new_ids_copy_the_stock_rows_not_the_restatted_ones(self):
        self.assertEqual(set(stats_row(self.image, 'stats', 0x66)[2:ids.CHEM_BASE]), {0x0D})
        stats = inventory.table('stats')
        table = ids.new_by_new_chemistry(self.image.read(stats.address, stats.header + 0x66 * ids.STATS_ROW),
                                         stats.header, ids.parse_ids({'ids': self.NEW}))
        n = ids.ID_BOUND - ids.FIRST_NEW + 1
        self.assertEqual(table[1], (0x0D + 0x04) % 4)

    def test_in_place_without_ids(self):
        ctx = steps.RosterContext(dol=fresh_image(), dat=None, config={'stock_stats': [{'id': '0x00', 'stats': '0x09'}]})
        log = ids.apply(ctx)
        self.assertIn('tables in place', log[-1])
        self.assertEqual(set(stats_row(ctx.dol, 'stats', 0x00, moved=False)[2:ids.CHEM_BASE]), {0x09})
        self.assertEqual(stats_row(ctx.dol, 'stats', 0x05, moved=False)[ids.CHEM_BASE], (0x05 + 0x09) % 4)
        self.assertEqual(ctx.state['stock_stats'], {0x00: 0x09})

    def test_own_stats_change_nothing(self):
        a = ids.parse_stock_stats({'stock_stats': [{'id': '0x00', 'stats': '0x00'}]})
        self.assertEqual(a, {})
        for bad in ([{'id': '0x4D', 'stats': '0x00'}], [{'id': '0x00', 'stats': '0x66'}], [{'id': '0x00'}],
                    [{'id': '0x00', 'stats': '0x01'}, {'id': '0x00', 'stats': '0x02'}]):
            with self.subTest(bad), self.assertRaises(ids.IdConfigError):
                ids.parse_stock_stats({'stock_stats': bad})

    def test_manifest_and_state(self):
        image, ctx = build({'ids': [], 'stock_stats': [{'id': '0x0D', 'stats': '0x09'}]})
        mf = state._manifest(image)
        self.assertEqual(mf['stats'], [[0x0D, 0x09]])
        chars = {c['id']: c for c in state.read_state(image)['characters']}
        self.assertEqual((chars[0x0D]['stats'], chars[0x09]['stats']), (0x09, 0x09))


class PackFingerprintTests(unittest.TestCase):
    def test_fingerprints_carry_stock_stats_and_voices(self):
        from SluggiesTools.Roster import pack
        image, _ctx = build({'ids': [], 'stock_stats': [{'id': '0x0D', 'stats': '0x09'}],
                             'stock_voices': [{'id': '0x0D', 'voice': '0x00'}]})
        plain, _ = build({'ids': []})
        def prints(img):
            st = state.read_state(img)
            return {c['id']: pack.fingerprint(st, c, lambda ref: None) for c in st['characters']}
        a, b = prints(image), prints(plain)
        self.assertEqual((a[0x0D]['stats'], a[0x0D]['voice']), ('0x09', '0x00'))
        self.assertEqual((b[0x0D]['stats'], b[0x0D]['voice']), ('0x0D', '0x0D'))
        self.assertEqual(a[0x00], b[0x00])


# --------------------------------------------------------------------------
# Planner
# --------------------------------------------------------------------------

def char(cid, square, *, template=None, family=None, name='X', stats=None):
    return {'id': cid, 'name': {'en': name}, 'default_name': None, 'template': template, 'model_dir': 0x30,
            'own_model_dir': False, 'model_source': cid if template is None else template,
            'stats': stats if stats is not None else (cid if template is None else template),
            'family': cid if family is None else family, 'square': square, 'icon': None}


def make_state():
    return {'squares': [{'kind': 'stock', 'head_index': 0, 'head': 0x00, 'members': [0x00, 0x68], 'voice': 0x00,
                         'voice_set': None},
                        {'kind': 'stock', 'head_index': 9, 'head': 0x09, 'members': [0x09], 'voice': 0x09,
                         'voice_set': None},
                        {'kind': 'stock', 'head_index': 0x0D, 'head': 0x0D, 'members': [0x0D, 0x0E],
                         'voice': 0x0D, 'voice_set': None},
                        {'kind': 'new', 'head_index': 43, 'head': 0x66, 'members': [0x66, 0x67, 0x47],
                         'voice': 0x00, 'voice_set': None}],
            'characters': [char(0x00, 0, name='Mario'), char(0x09, 1, name='Bowser'), char(0x0D, 2, name='Red Toad'),
                           char(0x0E, 2, family=0x0D, name='Blue Toad'),
                           char(0x47, 3, family=0x06, name='#N/A'),
                           char(0x66, 3, template=0x00, name='Empty slot', family=0x00),
                           char(0x67, 3, template=0x00, name='Own', family=0x00),
                           char(0x68, 0, template=0x00, name='Wheel Mario', family=0x00)]}


def make_config():
    return {'version': 1,
            'ids': [{'id': '0x66', 'template': '0x00', 'wheel': None},
                    {'id': '0x67', 'template': '0x00', 'wheel': None, 'stats': '0x09'},
                    {'id': '0x68', 'template': '0x00', 'wheel': '0x00'}],
            'wheels': [{'id': '0x47', 'wheel': '0x06'}],
            'grid': {'shape': [12, 5], 'squares': [['0x66', '0x67', '0x47']], 'order': []}}


NAMES = {0x00: {'en': 'Mario'}, 0x09: {'en': 'Bowser'}, 0x0D: {'en': 'Red Toad'}}


def batch(*items, st=None, config=None):
    parsed = slot_plan.parse_edits([dict(op=op, id=cid, source=src) for op, cid, src in items])
    return slot_plan.plan_batch(st or make_state(), config or make_config(), parsed, slot_plan.Env(), STATE_FILE,
                                NAMES)


class PlanStatsTests(unittest.TestCase):
    def test_stock_slot(self):
        plan = slot_plan.plan_stats(make_state(), make_config(), 0x0D, 0x09, STATE_FILE, NAMES)
        self.assertEqual(plan.config['stock_stats'], [{'id': '0x0D', 'stats': '0x09'}])
        self.assertEqual(plan.commands, [('--roster', '--state', STATE_FILE), ('--roster-state',)])
        self.assertEqual(plan.effects['stats'], 'Bowser (0x09)')
        back = slot_plan.plan_stats(make_state(), plan.config, 0x0D, None, STATE_FILE, NAMES)
        self.assertNotIn('stock_stats', back.config)
        same = slot_plan.plan_stats(make_state(), make_config(), 0x0D, 0x0D, STATE_FILE, NAMES)
        self.assertTrue(same.nothing)

    def test_new_id(self):
        plan = slot_plan.plan_stats(make_state(), make_config(), 0x66, 0x0D, STATE_FILE, NAMES)
        self.assertEqual(plan.config['ids'][0]['stats'], '0x0D')
        back = slot_plan.plan_stats(make_state(), make_config(), 0x67, 0x00, STATE_FILE, NAMES)   # the template
        self.assertNotIn('stats', back.config['ids'][1])
        self.assertIn('template', back.effects['stats'])

    def test_refusals(self):
        for cid, source in ((0x66, 0x4D), (0x0D, 0x66)):
            with self.subTest(source=source), self.assertRaises(slot_plan.PlanError):
                slot_plan.plan_stats(make_state(), make_config(), cid, source, STATE_FILE, NAMES)
        st = make_state()
        st['characters'].append(char(0x50, 0, name='Mii'))
        with self.assertRaises(slot_plan.PlanError):
            slot_plan.plan_stats(st, make_config(), 0x50, 0x00, STATE_FILE, NAMES)


class PlanVoiceTests(unittest.TestCase):
    def test_stock_square_by_any_member_and_family(self):
        plan = slot_plan.plan_voice(make_state(), make_config(), 0x0E, 0x09, STATE_FILE, NAMES)
        self.assertEqual(plan.target, 0x0D)
        self.assertEqual(plan.config['stock_voices'], [{'id': '0x0D', 'voice': '0x09'}])
        self.assertEqual(plan.effects['voice'], 'Bowser (0x09) (square voice)')
        by_family = slot_plan.plan_voice(make_state(), make_config(), 0x09, 0x0E, STATE_FILE, NAMES)
        self.assertEqual(by_family.config['stock_voices'], [{'id': '0x09', 'voice': '0x0D'}])   # Blue Toad -> Toad
        back = slot_plan.plan_voice(make_state(), plan.config, 0x0D, None, STATE_FILE, NAMES)
        self.assertNotIn('stock_voices', back.config)
        self.assertTrue(slot_plan.plan_voice(make_state(), make_config(), 0x0D, 0x0D, STATE_FILE, NAMES).nothing)

    def test_new_square(self):
        plan = slot_plan.plan_voice(make_state(), make_config(), 0x67, 0x09, STATE_FILE, NAMES)
        self.assertEqual(plan.config['grid']['squares'][0], {'members': ['0x66', '0x67', '0x47'], 'voice': '0x09'})
        note = plan.notes[-1]
        self.assertIn('0x66, 0x67', note)
        self.assertIn('0x47 keep', note)
        back = slot_plan.plan_voice(make_state(), plan.config, 0x66, None, STATE_FILE, NAMES)
        self.assertEqual(back.config['grid']['squares'][0], ['0x66', '0x67', '0x47'])

    def test_a_new_square_keeps_its_voice_available(self):
        config = make_config()
        config['grid']['squares'][0] = {'members': ['0x66', '0x67', '0x47'], 'voice': '0x09'}
        with self.assertRaises(slot_plan.PlanError) as err:
            slot_plan.plan_voice(make_state(), config, 0x09, 0x00, STATE_FILE, NAMES)    # Bowser gives his away
        self.assertIn('another voice', str(err.exception))
        config['stock_voices'] = [{'id': '0x00', 'voice': '0x09'}]
        plan = slot_plan.plan_voice(make_state(), config, 0x09, 0x00, STATE_FILE, NAMES)  # a swap keeps it
        self.assertEqual(plan.config['stock_voices'], [{'id': '0x00', 'voice': '0x09'}, {'id': '0x09', 'voice': '0x00'}])

    def test_refusals(self):
        with self.assertRaises(slot_plan.PlanError):
            slot_plan.plan_voice(make_state(), make_config(), 0x0D, 0x47, STATE_FILE, NAMES)   # no stock square
        with self.assertRaises(slot_plan.PlanError):
            slot_plan.plan_voice(make_state(), make_config(), 0x0D, 0x99, STATE_FILE, NAMES)


class BatchTests(unittest.TestCase):
    def test_one_rebuild_for_stats_and_voices(self):
        b = batch(('stats', '0x0D', '0x09'), ('voice', '0x00', '0x09'), ('voice', '0x09', '0x00'))
        self.assertTrue(b.ok, b.refused)
        self.assertEqual(b.commands, [('--roster', '--state', STATE_FILE), ('--roster-state',)])
        self.assertEqual(b.config['stock_voices'], [{'id': '0x00', 'voice': '0x09'}, {'id': '0x09', 'voice': '0x00'}])

    def test_last_edit_per_slot_and_square_wins(self):
        b = batch(('stats', '0x0D', '0x09'), ('stats', '0x0D', '0x00'), ('voice', '0x0D', '0x09'),
                  ('voice', '0x0E', '0x00'))
        self.assertEqual(b.config['stock_stats'], [{'id': '0x0D', 'stats': '0x00'}])
        self.assertEqual(b.config['stock_voices'], [{'id': '0x0D', 'voice': '0x00'}])
        self.assertEqual([(e.op, e.cid) for e, _p in b.plans], [('stats', 0x0D), ('voice', 0x0E)])

    def test_clear_drops_earlier_stats_and_keeps_the_voice(self):
        merged, notes, _ = slot_plan.merge_edits(slot_plan.parse_edits([
            {'op': 'stats', 'id': '0x66', 'source': '0x09'}, {'op': 'voice', 'id': '0x66', 'source': '0x09'},
            {'op': 'clear', 'id': '0x66'}]))
        self.assertEqual([e.op for e in merged], ['voice', 'clear'])
        self.assertTrue(any('stats edit is dropped' in n for n in notes))

    def test_stock_clear_resets_stock_stats(self):
        config = make_config()
        config['stock_stats'] = [{'id': '0x0D', 'stats': '0x09'}]
        plan = slot_plan.plan_clear(make_state(), config, 0x0D, STATE_FILE)
        self.assertNotIn('stock_stats', plan.config)
        self.assertEqual(plan.effects['stats'], 'its own')

    def test_edit_json_and_parsing(self):
        e = slot_plan.parse_edits([{'op': 'stats', 'id': '0x0D', 'source': None},
                                   {'op': 'voice', 'id': '0x0D', 'source': '0x09'}])
        self.assertEqual([x.to_json() for x in e], [{'op': 'stats', 'id': '0x0D', 'source': None},
                                                    {'op': 'voice', 'id': '0x0D', 'source': '0x09'}])
        for word in ('-', 'default', ''):
            self.assertIsNone(slot_plan.parse_source(word))
        with self.assertRaises(slot_plan.PlanError):
            slot_plan.parse_edits([{'op': 'voice', 'id': '0x0D'}])


class CliTests(unittest.TestCase):
    def test_voice_and_stats_are_batches_of_one(self):
        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(slot_cli, 'run', return_value=slot_plan.Batch(None)) as run:
            self.assertEqual(slot_cli.main(['--voice', '0x0D', '0x09', '--output-dir', out]), 0)
            self.assertEqual(slot_cli.main(['--stats', '0x0D', '-', '--output-dir', out]), 0)
        (first,), _ = run.call_args_list[0]
        self.assertEqual([(e.op, e.cid, e.source) for e in first], [('voice', 0x0D, 0x09)])
        self.assertEqual([(e.op, e.source) for e in run.call_args_list[1].args[0]], [('stats', None)])

    def test_dispatcher(self):
        import start
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = os.path.join(tmp, 'plan.json')
            with open(plan_file, 'w') as f:
                json.dump({'commands': [['--roster', '--state', 's.json'], ['--roster-state']]}, f)
            results = [mock.Mock(returncode=0) for _ in range(6)]
            with mock.patch.object(start, 'SLOT_PLAN_FILE', plan_file), \
                    mock.patch('start.subprocess.run', side_effect=results) as run:
                self.assertTrue(start.run_slot_chain('0x0D', voice='0x09'))
                self.assertTrue(start.run_slot_chain('0x0D', stats='-', dry_run=True))
        planners = [c.args[0] for c in run.call_args_list if '--voice' in c.args[0] or '--stats' in c.args[0]]
        self.assertEqual(planners[0][-3:], ['--voice', '0x0D', '0x09'])
        self.assertEqual(planners[1][-4:], ['--stats', '0x0D', '-', '--dry-run'])


if __name__ == '__main__':
    unittest.main()
