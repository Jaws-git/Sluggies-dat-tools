"""Roster packs (``Roster/pack.py``): fingerprints, the per-slot diff, the pack file
and the load plan.

A small synthetic game: a stock square with Mario (0x00, vanilla models) and Toad (0x0D, a patched High model),
and a new square with 0x66 (an own model directory from Bowser, 0x09, with a patched High model) and 0x67 (no own
directory). Model blocks are plain byte strings; the block checks are a stand-in ``LoadEnv``.
"""

import copy
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

import numpy as np
from PIL import Image

from SluggiesTools.Roster import derive, pack

VANILLA = {(0x12, 0): b'mario-hp', (0x12, 1): b'mario-l', (0x1F, 0): b'toad-hp', (0x1F, 1): b'toad-l',
           (0x1B, 0): b'koopa-hp', (0x1B, 1): b'koopa-l'}
PIXELS = {'side_0_0.png': np.full((51, 48, 4), 200, np.uint8), 'front_0_0.png': np.full((51, 48, 4), 90, np.uint8),
          'stock.png': np.full((51, 48, 4), 30, np.uint8)}


def sha1(data: bytes) -> str:
    import hashlib
    return hashlib.sha1(data).hexdigest()


class Game:
    """A read state, its blocks and its derived config (module docstring); tests change them before use."""

    def __init__(self):
        self.blocks = {(0x00, 'high'): VANILLA[(0x12, 0)], (0x00, 'low'): VANILLA[(0x12, 1)],
                       (0x0D, 'high'): b'toad-hp-edited', (0x0D, 'low'): VANILLA[(0x1F, 1)],
                       (0x66, 'high'): b'koopa-hp-edited', (0x66, 'low'): VANILLA[(0x1B, 1)],
                       (0x67, 'high'): b'template-hp', (0x67, 'low'): b'template-l'}
        self.routes = [[0x2000000, 100], [0x2001000, 50]]
        self.names = {0x00: 'Mario', 0x0D: 'Toad', 0x66: 'Own Bowser', 0x67: 'Empty slot'}
        self.stock_names = []

    def state(self) -> dict:
        def char(cid, square, **extra):
            out = {'id': cid, 'name': {'en': self.names[cid], 'fr': self.names[cid], 'sp': self.names[cid]},
                   'square': square, 'stats': cid, 'own_model_dir': False, 'model_source': cid,
                   'icon': {'front': {'file': 'stock.png'}, 'side': {'file': 'stock.png'}},
                   'blocks': {role: {'sha1': sha1(self.blocks[(cid, role)])} for role in ('high', 'low')}}
            out.update(extra)
            return out
        own = {'file': 'side_0_0.png'}, {'file': 'front_0_0.png'}
        return {'kind': 'expanded', 'shape': [11, 5],
                'squares': [{'kind': 'stock', 'head': 0x00, 'voice': 0x00, 'members': [0x00, 0x0D]},
                            {'kind': 'new', 'head': 0x66, 'voice': 0x09, 'members': [0x66, 0x67]}],
                'characters': [char(0x00, 0), char(0x0D, 0),
                               char(0x66, 1, own_model_dir=True, model_source=0x09, stats=0x09,
                                    icon={'side': own[0], 'front': own[1]}),
                               char(0x67, 1, template=0x06, model_source=0x06, stats=0x06)]}

    def fingerprints(self) -> dict:
        st = self.state()
        return pack.add_fingerprints(st, lambda ref: PIXELS[ref['file']])

    def derived(self, side='side_0_0.png', front='front_0_0.png') -> derive.Derived:
        config = {'version': 1, 'comment': 'derived',
                  'ids': [{'id': '0x66', 'template': '0x06', 'wheel': None, 'stats': '0x09',
                           'model': {'from': '0x09', 'routes': copy.deepcopy(self.routes)},
                           'name': {'en': self.names[0x66]},
                           'icon': {'side': side, 'front': front, 'like': '0x06', 'fit': 'strict'}},
                          {'id': '0x67', 'template': '0x06', 'wheel': None}],
                  'grid': {'shape': [11, 5], 'squares': [{'members': ['0x66', '0x67'], 'voice': '0x09'}]}}
        if self.stock_names:
            config['stock_names'] = copy.deepcopy(self.stock_names)
        portraits = {side: (Image.fromarray(PIXELS['side_0_0.png'], 'RGBA'), b'\x01' * 32),
                     front: (Image.fromarray(PIXELS['front_0_0.png'], 'RGBA'), None)}
        return derive.Derived(config, portraits)

    def pack_files(self) -> dict:
        st = self.state()
        pack.add_fingerprints(st, lambda ref: PIXELS[ref['file']])
        return pack.pack_files(st, self.derived(), lambda c, r: self.blocks[(c['id'], r)],
                               lambda d, f: VANILLA.get((d, f)))


class FakeEnv(pack.LoadEnv):
    def __init__(self, errors=None, warnings=None):
        self.errors, self.warnings, self.calls = errors or {}, warnings or {}, []

    def slot_problems(self, directory, high, low):
        self.calls.append((directory, high, low))
        return list(self.errors.get(directory, [])), list(self.warnings.get(directory, []))


class PackTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.saved = Game()
        self.path = os.path.join(self.tmp, 'roster' + pack.EXTENSION)
        pack.write_pack(self.path, self.saved.pack_files())

    def plan(self, game: Game, env=None, path=None):
        p = pack.read_pack(path or self.path)
        return pack.plan_load(p, game.state(), game.fingerprints(), game.derived(), env or FakeEnv(), 'state.json',
                              lambda name: f'load/{name}')


class FingerprintTests(unittest.TestCase):
    def test_fields(self):
        fp = Game().fingerprints()
        self.assertEqual(fp['0x0D']['high'], sha1(b'toad-hp-edited'))
        self.assertIsNone(fp['0x0D']['model'])
        self.assertEqual(fp['0x66']['model'], '0x09')
        self.assertEqual((fp['0x67']['high'], fp['0x67']['low']), (None, None))   # loads its template's files
        self.assertEqual(fp['0x66']['side'], pack.pixel_sha1(PIXELS['side_0_0.png']))
        self.assertEqual((fp['0x66']['voice'], fp['0x66']['square'], fp['0x66']['stats']), ('0x09', '0x66', '0x09'))
        self.assertEqual(fp['0x00']['name']['en'], 'Mario')

    def test_diff(self):
        game = Game()
        before = game.fingerprints()
        game.names[0x0D] = 'Little Toad'
        after = game.fingerprints()
        after['0x70'] = after.pop('0x67')
        rows = {d.cid: d for d in pack.diff(before, after)}
        self.assertEqual(rows[0x00].status, pack.SAME)
        self.assertEqual((rows[0x0D].status, rows[0x0D].fields), (pack.DIFFERS, ['name']))
        self.assertEqual(rows[0x67].status, pack.PACK_ONLY)
        self.assertEqual(rows[0x70].status, pack.GAME_ONLY)

    def test_crops_from_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            Image.fromarray(PIXELS['stock.png'], 'RGBA').save(os.path.join(tmp, 'a.png'))
            crop = pack.crops_from_dir(tmp)
            self.assertEqual(pack.pixel_sha1(crop({'file': 'a.png'})), pack.pixel_sha1(PIXELS['stock.png']))
            self.assertIsNone(crop({'file': 'missing.png'}))


class PackFileTests(PackTestCase):
    def test_gui_uses_the_pack_extension(self):
        from SluggiesTools import gui_grid       # keeps its own copy: it imports nothing from Roster
        self.assertEqual(gui_grid.PACK_EXTENSION, pack.EXTENSION)

    def test_round_trip(self):
        p = pack.read_pack(self.path)
        # only blocks that differ from the vanilla one the slot started from
        self.assertEqual(set(p.blocks), {(0x0D, 'high'), (0x66, 'high')})
        self.assertEqual(p.blocks[(0x66, 'high')], b'koopa-hp-edited')
        self.assertEqual(p.meta['blocks'], {'0x0D': {'high': 'models/0x0D_hp.bin'},
                                            '0x66': {'high': 'models/0x66_hp.bin'}})
        # the output's routes are dropped: a fresh copy of the source anywhere else
        self.assertEqual(p.config['ids'][0]['model'], {'from': '0x09'})
        self.assertEqual(set(p.icons), {'side_0_0.png', 'side_0_0.cmpr', 'front_0_0.png'})
        self.assertEqual(p.icons['side_0_0.cmpr'], b'\x01' * 32)
        self.assertEqual(p.fingerprints, self.saved.fingerprints())

    def rewrite(self, change):
        with zipfile.ZipFile(self.path) as zf:
            files = {n: zf.read(n) for n in zf.namelist()}
        change(files)
        bad = os.path.join(self.tmp, 'bad' + pack.EXTENSION)
        with zipfile.ZipFile(bad, 'w') as zf:
            for name, data in files.items():
                zf.writestr(name, data)
        return bad

    def test_a_damaged_block_is_refused(self):
        bad = self.rewrite(lambda f: f.__setitem__('models/0x0D_hp.bin', b'toad-hp-damaged'))
        with self.assertRaisesRegex(pack.PackError, '0x0D does not match its fingerprint'):
            pack.read_pack(bad)

    def test_other_formats_and_files_are_refused(self):
        def meta(files):
            files['pack.json'] = json.dumps({'format': 99}).encode()
        with self.assertRaisesRegex(pack.PackError, 'format 99'):
            pack.read_pack(self.rewrite(meta))
        with self.assertRaisesRegex(pack.PackError, 'unexpected file'):
            pack.read_pack(self.rewrite(lambda f: f.__setitem__('icons/../evil.png', b'')))
        with self.assertRaisesRegex(pack.PackError, 'portrait front_0_0.png is missing'):
            pack.read_pack(self.rewrite(lambda f: f.pop('icons/front_0_0.png')))
        not_zip = os.path.join(self.tmp, 'x' + pack.EXTENSION)
        with open(not_zip, 'wb') as f:
            f.write(b'not a zip')
        with self.assertRaisesRegex(pack.PackError, 'not a roster pack'):
            pack.read_pack(not_zip)

    def test_extract(self):
        game = Game()
        game.blocks[(0x0D, 'high')] = VANILLA[(0x1F, 0)]
        p = pack.read_pack(self.path)
        plan = self.plan(game)
        folder = os.path.join(self.tmp, 'load')
        path = pack.extract(p, plan, folder)
        with open(path, encoding='utf-8') as f:
            self.assertEqual(json.load(f), plan.config if plan.config is not None else p.config)
        with open(os.path.join(folder, 'models', '0x0D_hp.bin'), 'rb') as f:
            self.assertEqual(f.read(), b'toad-hp-edited')
        self.assertTrue(os.path.isfile(os.path.join(folder, derive.ICON_DIR, 'side_0_0.cmpr')))


class LoadPlanTests(PackTestCase):
    def test_an_unchanged_game_gives_nothing_to_do(self):
        plan = self.plan(Game())
        self.assertEqual([d.status for d in plan.diff], [pack.SAME] * 4)
        self.assertEqual(plan.commands, [])
        self.assertIsNone(plan.config)
        self.assertEqual(plan.kept_dirs, [0x66])
        self.assertIn('nothing to load', plan.notes[0])

    def test_every_pack_block_is_checked(self):
        env = FakeEnv()
        self.plan(Game(), env)
        self.assertEqual(sorted(c[:2] for c in env.calls), [(0x1B, b'koopa-hp-edited'), (0x1F, b'toad-hp-edited')])

    def test_a_roster_change_is_one_rebuild(self):
        game = Game()
        game.names[0x0D] = 'Little Toad'
        game.stock_names = [{'id': '0x0D', 'name': {'en': 'Little Toad'}}]
        plan = self.plan(game)
        self.assertEqual(plan.commands, [('--roster', '--state', 'state.json'), ('--roster-state',)])
        self.assertEqual([(d.cid, d.fields) for d in plan.diff if d.status == pack.DIFFERS], [(0x0D, ['name'])])
        self.assertEqual(plan.config['ids'][0]['model']['routes'], game.routes)   # 0x66's directory is kept

    def test_a_stock_slot_goes_back_to_vanilla_then_takes_the_pack_block(self):
        game = Game()
        game.blocks[(0x0D, 'high')] = VANILLA[(0x1F, 0)]          # Toad cleared in the game
        plan = self.plan(game)
        self.assertEqual(plan.commands, [('--unpatch', '--target-id', '0x0D'),
                                         ('--write-slot-blocks', '0x0D', 'load/models/0x0D_hp.bin', '-'),
                                         ('--roster-state',)])
        game.blocks[(0x0D, 'high')] = b'toad-hp-edited'           # only the Low model differs: the pack's is vanilla
        game.blocks[(0x0D, 'low')] = b'toad-l-edited'
        self.assertEqual(self.plan(game).commands, [('--unpatch', '--target-id', '0x0D'),
                                                    ('--write-slot-blocks', '0x0D', 'load/models/0x0D_hp.bin', '-'),
                                                    ('--roster-state',)])
        game.blocks[(0x00, 'high')] = b'mario-hp-edited'          # the pack's Mario is vanilla: a clear alone
        self.assertEqual(self.plan(game).commands[0], ('--unpatch', '--target-id', '0x00'))
        self.assertNotIn('0x00', [c[1] for c in self.plan(game).commands if c[0] == '--write-slot-blocks'])

    def test_a_changed_own_directory_is_copied_afresh(self):
        game = Game()
        game.blocks[(0x66, 'high')] = b'koopa-hp-other-edit'
        plan = self.plan(game)
        self.assertEqual(plan.config['ids'][0]['model'], {'from': '0x09'})
        self.assertEqual(plan.commands, [('--roster', '--state', 'state.json'),
                                         ('--write-slot-blocks', '0x66', 'load/models/0x66_hp.bin', '-'),
                                         ('--roster-state',)])
        self.assertEqual(plan.kept_dirs, [])

    def test_a_pack_from_another_output(self):
        """Other DAT routes in the game and other portrait file names: the same roster, nothing to do."""
        game = Game()
        game.routes = [[0x3000000, 100], [0x3005000, 50]]
        derived = game.derived(side='side_52_0.png', front='front_52_0.png')
        p = pack.read_pack(self.path)
        plan = pack.plan_load(p, game.state(), game.fingerprints(), derived, FakeEnv(), 'state.json', str)
        self.assertEqual(plan.commands, [])
        # onto a game without the own directory: a fresh copy and the pack's block
        game = Game()
        state = game.state()
        state['characters'][2].update(own_model_dir=False, model_source=0x06)
        derived = game.derived()
        del derived.config['ids'][0]['model']
        fp = pack.add_fingerprints(state, lambda ref: PIXELS[ref['file']])
        plan = pack.plan_load(p, state, fp, derived, FakeEnv(), 'state.json', lambda n: n)
        self.assertEqual(plan.commands[0], ('--roster', '--state', 'state.json'))
        self.assertIn(('--write-slot-blocks', '0x66', 'models/0x66_hp.bin', '-'), plan.commands)

    def test_a_refused_block_refuses_the_load(self):
        game = Game()
        game.blocks[(0x0D, 'high')] = VANILLA[(0x1F, 0)]
        plan = self.plan(game, FakeEnv(errors={0x1B: ['the skeletons do not match']},
                                       warnings={0x1F: ['not its partner']}))
        self.assertFalse(plan.ok)
        self.assertEqual(plan.refused, [(0x66, 'the skeletons do not match')])
        self.assertEqual((plan.commands, plan.writes, plan.clears), ([], [], []))
        self.assertEqual(plan.warnings, ['0x0D: not its partner'])

    def test_blocks_of_a_new_id_without_own_directory_are_refused(self):
        def change(files):
            meta = json.loads(files['pack.json'])
            meta['blocks']['0x67'] = {'high': 'models/0x67_hp.bin'}
            files['pack.json'] = json.dumps(meta).encode()
            fp = json.loads(files['fingerprints.json'])
            fp['0x67']['high'] = sha1(b'x')
            files['fingerprints.json'] = json.dumps(fp).encode()
            files['models/0x67_hp.bin'] = b'x'
        with zipfile.ZipFile(self.path) as zf:
            files = {n: zf.read(n) for n in zf.namelist()}
        change(files)
        pack.write_pack(self.path, files)
        plan = self.plan(Game())
        self.assertEqual([c for c, _e in plan.refused], [0x67])

    def test_a_stock_pack_resets_the_roster(self):
        saved = Game()
        saved.blocks[(0x66, 'high')] = VANILLA[(0x1B, 0)]          # (a stock roster has no own directories)
        derived = derive.Derived({'version': 1, 'comment': 'derived'}, {})
        st = saved.state()
        pack.add_fingerprints(st, lambda ref: PIXELS[ref['file']])
        files = pack.pack_files(st, derived, lambda c, r: saved.blocks[(c['id'], r)], lambda d, f: VANILLA.get((d, f)))
        pack.write_pack(self.path, files)
        plan = self.plan(Game())
        self.assertTrue(plan.remove)
        self.assertEqual(plan.commands[0], ('--roster', '--remove'))
        self.assertEqual(plan.to_json()['rebuild'], True)


class DispatchTests(unittest.TestCase):
    """``start.py --load-roster``: the planner, then its commands in order (none with ``--dry-run``)."""

    def setUp(self):
        import start
        self.start = start
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.plan_file = os.path.join(self.tmp, 'plan.json')
        self.enterContext(mock.patch.object(start, 'PACK_PLAN_FILE', self.plan_file))

    def run_load(self, commands, codes, **kwargs):
        with open(self.plan_file, 'w') as f:
            json.dump({'commands': commands}, f)
        results = [mock.Mock(returncode=code) for code in codes]
        with mock.patch('start.subprocess.run', side_effect=results) as run:
            ok = self.start.run_load_roster('r.sluggiesroster', **kwargs)
        return ok, [call.args[0] for call in run.call_args_list]

    def test_runs_the_planned_commands(self):
        ok, calls = self.run_load([['--roster', '--state', 's.json'], ['--roster-state']], [0, 0, 0])
        self.assertTrue(ok)
        self.assertEqual(calls[0][-2:], ['--load', os.path.abspath('r.sluggiesroster')])
        self.assertEqual(calls[1][-3:], ['--roster', '--state', 's.json'])

    def test_dry_run_and_refusals_run_no_command(self):
        self.assertEqual(len(self.run_load([['--roster-state']], [0], dry_run=True)[1]), 1)
        ok, calls = self.run_load([['--roster-state']], [1])
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1)
        ok, calls = self.run_load([], [0])
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)

    def test_save_and_write_slot_blocks(self):
        with mock.patch('start.subprocess.run', return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(self.start.run_save_roster('r.sluggiesroster'))
            self.assertTrue(self.start.run_write_slot_blocks('0x0D', 'a.bin', '-'))
        self.assertEqual(run.call_args_list[0].args[0][-2:], ['--save', os.path.abspath('r.sluggiesroster')])
        self.assertEqual(run.call_args_list[1].args[0][-4:], ['--write-slot-blocks', '0x0D', os.path.abspath('a.bin'),
                                                              '-'])


if __name__ == '__main__':
    unittest.main()
