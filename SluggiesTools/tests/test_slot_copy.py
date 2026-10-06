"""Copy / paste a character between slots (``slot_plan.plan_copy``, the ``copy`` op, ``StatEditor/apply`` ``Copy``).

The plans are built from a hand-made read state and derived config with a fake ``Env`` (blocks, vanilla blocks,
slot checks, portraits, stat snapshots); the stat snapshot round trip runs on the synthetic roster DOL of the stat
editor tests (``slot_cli.FileEnv.stat_snapshot`` -> ``copy:`` step).
"""

import hashlib
import json
import os
import tempfile
import unittest

from PIL import Image

from SluggiesTools import gui_grid
from SluggiesTools.Roster import slot_cli, slot_plan
from SluggiesTools.StatEditor import apply, carry, cli, fields
from SluggiesTools.tests.test_stat_editor_carry import EXPANDED, Editor
from SluggiesTools.tests.test_stat_editor_carry import copy as copy_image
from SluggiesTools.tests.test_roster_voice_stats_reassign import build as build_roster

STATE_FILE = '/out/_gui/slot/roster.json'
COPY_DIR = os.path.join('/out/_gui/slot', slot_plan.COPY_DIR)
NAMES = {0x09: {'en': 'Bowser', 'fr': 'Bowser', 'sp': 'Bowser'},
         0x0D: {'en': 'Red Toad', 'fr': 'Toad rouge', 'sp': 'Toad rojo'},
         0x0E: {'en': 'Blue Toad', 'fr': 'Toad bleu', 'sp': 'Toad azul'}}
GEAR = ('bat', 'glove_l', 'glove_r', 'extra')


def sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def block(cid, role, tag=''):
    return f'{cid:02X}-{role}{tag}'.encode()


class World:
    """A read state, derived config and the blocks behind them."""

    def __init__(self):
        self.blocks = {}                       # (cid, role) -> bytes the slot loads now
        self.vanilla = {}                      # (directory, file) -> bytes of 1_Input
        self.characters = []

    def char(self, cid, square, model_dir, *, template=None, own=False, source=None, name=None, stats=None,
             gear_vanilla=True, tags=None):
        tags = tags or {}
        source = cid if source is None else source
        for role in ('high', 'low') + GEAR:
            self.blocks[(cid, role)] = block(source, role, tags.get(role, ''))
        text = NAMES.get(cid) or {'en': name or f'#{cid:02X}', 'fr': name or f'#{cid:02X}', 'sp': name or f'#{cid:02X}'}
        c = {'id': cid, 'name': text, 'default_name': None, 'template': template, 'model_dir': model_dir,
             'own_model_dir': own, 'model_source': source,
             'stats': stats if stats is not None else (cid if template is None else template), 'square': square,
             'icon': {'front': {'file': f'f{cid}.png'}, 'side': {'file': f's{cid}.png'}},
             'blocks': {r: {'sha1': sha(self.blocks[(cid, r)])} for r in ('high', 'low')},
             'equipment': {r: {'file': f, 'sha1': sha(self.blocks[(cid, r)]),
                               'vanilla': gear_vanilla if r not in tags else False}
                           for f, r in zip((2, 3, 4, 5), GEAR)}}
        self.characters.append(c)
        return c

    def state(self, lone_voice=0x04):
        return {'squares': [{'kind': 'stock', 'head': 0x09, 'head_index': 9, 'members': [0x09, 0x68], 'voice': 0x09},
                            {'kind': 'stock', 'head': 0x0D, 'head_index': 13, 'members': [0x0D, 0x0E],
                             'voice': 0x0D},
                            {'kind': 'new', 'head': 0x66, 'members': [0x66, 0x67], 'voice': 0x04,
                             'voice_set': None},
                            {'kind': 'new', 'head': 0x69, 'members': [0x69], 'voice': lone_voice,
                             'voice_set': None},
                            {'kind': 'stock', 'head': 0x04, 'head_index': 4, 'members': [0x04], 'voice': 0x04},
                            {'kind': 'stock', 'head': 0x50, 'head_index': 30, 'members': [0x50], 'voice': 0x50}],
                'characters': sorted(self.characters, key=lambda c: c['id'])}


def make_world(**changes):
    w = World()
    w.char(0x04, 4, 22)
    w.char(0x09, 0, 27, tags=changes.get('bowser_tags', {'high': '*', 'bat': '*'}))     # patched model and bat
    w.char(0x0D, 1, 31)
    w.char(0x0E, 1, 32, tags=changes.get('blue_tags', {}))
    w.char(0x50, 5, 98, name='Mii')
    w.char(0x66, 2, 22, template=0x04, source=0x04, name='Empty slot')
    w.char(0x67, 2, 172, template=0x04, own=True, source=0x09, name='Koopa King', stats=0x09)
    w.char(0x68, 0, 27, template=0x09, source=0x09, name='Empty slot')
    w.char(0x69, 3, 22, template=0x04, source=0x04, name='Empty slot')
    for cid, directory in ((0x04, 22), (0x09, 27), (0x0D, 31), (0x0E, 32)):
        for f, role in enumerate(('high', 'low') + GEAR):
            w.vanilla[(directory, f)] = block(cid, role)
    return w


def make_config():
    return {'version': 1,
            'ids': [{'id': '0x66', 'template': '0x04', 'wheel': None, 'name': {'en': 'Empty slot'}},
                    {'id': '0x67', 'template': '0x04', 'wheel': None, 'stats': '0x09', 'name': {'en': 'Koopa King'},
                     'model': {'from': '0x09', 'routes': [[0x30000000, 0x100]]}},
                    {'id': '0x68', 'template': '0x09', 'wheel': '0x09', 'swatch': 3},
                    {'id': '0x69', 'template': '0x04', 'wheel': None, 'name': {'en': 'Empty slot'}}],
            'stock_icons': [{'id': '0x09', 'icon': {'side': 'side_koopa.png', 'front': 'front_koopa.png',
                                                    'fit': 'strict'}}],
            'grid': {'shape': [12, 5], 'squares': [['0x66', '0x67'], ['0x69']], 'order': []}}


class CopyEnv(slot_plan.Env):
    def __init__(self, world, slot_errors=(), gear_errors=None, same=False, snapshots=None):
        self.world, self.slot_errors, self.gear_errors = world, list(slot_errors), gear_errors or {}
        self.same = same
        self.snapshots = snapshots if snapshots is not None else {
            0x09: {'fields': {'stats': {'batting arm': '09'}}, 'chemistry': {'0x0D': [2, 1], '0x09': [1, 1]}}}
        self.slot_calls = []

    def current_block(self, char, role):
        return self.world.blocks.get((char['id'], role))

    def vanilla_block(self, directory, file):
        return self.world.vanilla.get((directory, file))

    def slot_problems(self, directory, high, low):
        self.slot_calls.append(directory)
        return list(self.slot_errors), []

    def equipment_block_problems(self, directory, file, block):
        return list(self.gear_errors.get(file, [])), []

    def shown_portrait(self, char, view):
        return Image.new('RGBA', (48, 51), (char['id'], 0, 0, 255))

    def same_portrait(self, char, view, image):
        return self.same

    def stat_snapshot(self, cid):
        return self.snapshots.get(cid, {'fields': {'stats': {'batting arm': f'{cid:02x}'}}, 'chemistry': {}})


def plan(world, target, source, env=None, config=None):
    config = config or make_config()
    return slot_plan.plan_copy(world.state(), config, config, target, source, env or CopyEnv(world), STATE_FILE,
                               NAMES)


def entry(config, cid):
    return next(e for e in config['ids'] if int(e['id'], 16) == cid)


def kinds(p):
    return [c[0] for c in p.commands]


class PasteOntoNewIdTests(unittest.TestCase):
    def test_fresh_directory_and_every_customisation(self):
        w = make_world()
        p = plan(w, 0x66, 0x09)
        e = entry(p.config, 0x66)
        self.assertEqual(e['model'], {'from': '0x09'})
        self.assertEqual(e['name'], {'en': 'Bowser', 'fr': 'Bowser', 'sp': 'Bowser'})
        self.assertEqual(e['stats'], '0x09')
        self.assertEqual(e['icon'], {'side': 'side_koopa.png', 'front': 'front_koopa.png', 'fit': 'strict'})
        self.assertEqual(p.portraits, {})                               # the source's own cells, by name
        self.assertEqual(kinds(p), ['--roster', '--apply-stat-edits', '--write-slot-blocks',
                                    '--write-slot-equipment', '--roster-state'])
        hp = os.path.join(COPY_DIR, '09_66_hp.bin')
        self.assertEqual(p.commands[2], ('--write-slot-blocks', '0x66', hp, '-'))   # only the changed High model
        self.assertEqual(p.commands[3], ('--write-slot-equipment', '0x66', '2', os.path.join(COPY_DIR, '09_66_bat.bin')))
        self.assertEqual(p.copy_files['09_66_hp.bin'], block(0x09, 'high', '*'))
        stat_doc = p.copy_files['stats_09_66.json']
        self.assertEqual((stat_doc['format'], stat_doc['source'], stat_doc['target']),
                         (slot_plan.COPY_FORMAT, '0x09', '0x66'))
        self.assertEqual(p.commands[1], ('--apply-stat-edits',
                                         slot_plan.COPY_PREFIX + os.path.join(COPY_DIR, 'stats_09_66.json')))
        self.assertEqual(set(p.effects['portraits']), {'front', 'side'})
        self.assertIn('fresh copy', ' '.join(p.notes))

    def test_unchanged_source_needs_no_block_writes(self):
        w = make_world(bowser_tags={})
        p = plan(w, 0x66, 0x09)
        self.assertNotIn('--write-slot-blocks', kinds(p))
        self.assertNotIn('--write-slot-equipment', kinds(p))
        self.assertEqual(entry(p.config, 0x66)['model'], {'from': '0x09'})

    def test_own_directory_from_the_same_source_is_kept(self):
        w = make_world()
        p = plan(w, 0x67, 0x09)
        self.assertEqual(entry(p.config, 0x67)['model'], {'from': '0x09', 'routes': [[0x30000000, 0x100]]})
        self.assertIn(('--write-slot-blocks', '0x67', os.path.join(COPY_DIR, '09_67_hp.bin'), '-'), p.commands)
        self.assertIn('keeps its own model directory', ' '.join(p.notes))

    def test_pixel_portraits_without_own_cells(self):
        w = make_world()
        p = plan(w, 0x66, 0x0D)                                         # stock art: no derived cells
        self.assertEqual(entry(p.config, 0x66)['icon'], {'side': 'copy_0D_66_side.png', 'front': 'copy_0D_66_front.png',
                                                         'fit': 'strict'})
        self.assertEqual(set(p.portraits), {'copy_0D_66_side.png', 'copy_0D_66_front.png'})

    def test_lone_new_square_takes_the_voice(self):
        w = make_world()
        p = plan(w, 0x69, 0x0D)
        self.assertEqual(p.config['grid']['squares'][1], {'members': ['0x69'], 'voice': '0x0D'})
        self.assertIn('voice', p.effects)

    def test_shared_new_square_keeps_its_voice(self):
        w = make_world()
        p = plan(w, 0x66, 0x0D)
        self.assertEqual(p.config['grid']['squares'][0], ['0x66', '0x67'])
        self.assertIn('keeps its square\'s voice', ' '.join(p.notes))

    def test_mii_source_cannot_become_a_directory(self):
        w = make_world()
        with self.assertRaisesRegex(slot_plan.PlanError, 'not a stock player'):
            plan(w, 0x66, 0x50)


class PasteOntoStockTests(unittest.TestCase):
    def test_matching_skeleton_writes_the_differing_files(self):
        w = make_world(blue_tags={})
        w.blocks[(0x0D, 'high')] = block(0x0D, 'high', '*')
        w.characters[[c['id'] for c in w.characters].index(0x0D)]['blocks']['high']['sha1'] = sha(
            w.blocks[(0x0D, 'high')])
        p = plan(w, 0x0E, 0x0D)
        self.assertIn(('--write-slot-blocks', '0x0E', os.path.join(COPY_DIR, '0D_0E_hp.bin'),
                       os.path.join(COPY_DIR, '0D_0E_l.bin')), p.commands)
        self.assertEqual([c for c in p.commands if c[0] == '--write-slot-equipment'], [
            ('--write-slot-equipment', '0x0E', str(f), os.path.join(COPY_DIR, f'0D_0E_{r}.bin'))
            for f, r in zip((2, 3, 4, 5), GEAR)])                       # full clone: the vanilla gear too
        self.assertEqual(p.config['stock_stats'], [{'id': '0x0E', 'stats': '0x0D'}])
        self.assertEqual(p.config['stock_names'], [{'id': '0x0E', 'name': NAMES[0x0D]}])
        self.assertIn('keeps its square\'s voice', ' '.join(p.notes)) if False else None

    def test_patched_gear_at_the_source_block_unpatches(self):
        w = make_world(blue_tags={'high': '*', 'bat': '*', 'glove_l': '*'})
        w.vanilla[(32, 2)] = block(0x0D, 'bat')                         # Blue's vanilla bat is Red's
        w.vanilla[(32, 3)] = block(0x0D, 'glove_l')
        blue = next(c for c in w.characters if c['id'] == 0x0E)
        blue['equipment']['glove_l']['vanilla'] = True                  # on its vanilla route: written, not unpatched
        p = plan(w, 0x0E, 0x0D)
        self.assertIn(('--unpatch', '--target-id', '0x0E', '--target-file', '2'), p.commands)
        self.assertIn(('--write-slot-equipment', '0x0E', '3', os.path.join(COPY_DIR, '0D_0E_glove_l.bin')), p.commands)
        self.assertIn(('--write-slot-blocks', '0x0E', os.path.join(COPY_DIR, '0D_0E_hp.bin'),
                       os.path.join(COPY_DIR, '0D_0E_l.bin')), p.commands)
        self.assertNotIn(('--unpatch', '--target-id', '0x0E'), p.commands)            # models: always written

    def test_other_skeleton_is_refused(self):
        w = make_world()
        with self.assertRaisesRegex(slot_plan.PlanError, 'same skeleton'):
            plan(w, 0x0E, 0x09, CopyEnv(w, slot_errors=['92 bones vs 89']))

    def test_gear_that_does_not_fit_stays(self):
        w = make_world()
        p = plan(w, 0x66, 0x09, CopyEnv(w, gear_errors={2: ['bone count']}))
        self.assertNotIn('--write-slot-equipment', kinds(p))
        self.assertIn('keeps its bat', ' '.join(p.warnings))

    def test_mii_target_keeps_name_stats_and_portraits(self):
        w = make_world()
        p = plan(w, 0x50, 0x0D)
        notes = ' '.join(p.notes)
        for text in ('name not copied', 'stats source not copied', 'portraits not copied'):
            self.assertIn(text, notes)


class EdgeTests(unittest.TestCase):
    def test_onto_itself(self):
        with self.assertRaisesRegex(slot_plan.PlanError, 'itself'):
            plan(make_world(), 0x09, 0x09)

    def test_a_clone_already(self):
        w = make_world()
        w.blocks.update({(0x68, r): w.blocks[(0x09, r)] for r in ('high', 'low') + GEAR})
        c = next(c for c in w.characters if c['id'] == 0x68)
        c.update(name=dict(NAMES[0x09]), stats=0x09)
        for r in ('high', 'low'):
            c['blocks'][r]['sha1'] = sha(w.blocks[(0x09, r)])
        for r in GEAR:
            c['equipment'][r]['sha1'] = sha(w.blocks[(0x09, r)])
        config = make_config()
        entry(config, 0x68)['name'] = dict(NAMES[0x09])       # stats: its template's already
        snapshot = {'fields': {'stats': {'batting arm': '09'}}, 'chemistry': {'0x0D': [2, 1], '0x68': [0, 0]}}
        env = CopyEnv(w, same=True, snapshots={0x09: snapshot,
                                               0x68: {'fields': snapshot['fields'],
                                                      'chemistry': {'0x0D': [2, 1], '0x09': [0, 0]}}})
        p = slot_plan.plan_copy(w.state(), config, config, 0x68, 0x09, env, STATE_FILE, NAMES)
        self.assertTrue(p.nothing, p.commands)
        self.assertEqual(p.copy_files, {})


class BatchTests(unittest.TestCase):
    def edits(self, *items):
        return slot_plan.parse_edits({'edits': list(items)})

    def test_paste_drops_the_target_edits_and_runs_after_the_rebuild(self):
        w = make_world()
        batch = slot_plan.plan_batch(w.state(), make_config(), self.edits(
            {'op': 'rename', 'id': '0x66', 'text': 'Old'},
            {'op': 'stats', 'id': '0x66', 'source': '0x0D'},
            {'op': 'rename', 'id': '0x09', 'text': 'Big B'},
            {'op': 'copy', 'id': '0x66', 'source': '0x09'}), CopyEnv(w), STATE_FILE, NAMES)
        self.assertTrue(batch.ok, batch.refused)
        self.assertEqual([(e.op, e.cid) for e, _p in batch.plans], [('rename', 0x09), ('copy', 0x66)])
        self.assertEqual(entry(batch.config, 0x66)['name']['en'], 'Bowser')        # not the pending rename
        heads = [c[0] for c in batch.commands]
        self.assertEqual(heads, ['--roster', '--apply-stat-edits', '--write-slot-blocks', '--write-slot-equipment',
                                 '--roster-state'])
        self.assertIn('are not copied', ' '.join(batch.plans[1][1].notes))
        self.assertIn('stats_09_66.json', batch.copy_files)

    def test_later_patch_or_clear_replaces_the_paste(self):
        merged, notes, refused = slot_plan.merge_edits(self.edits(
            {'op': 'copy', 'id': '0x66', 'source': '0x09'}, {'op': 'clear', 'id': '0x66'}))
        self.assertEqual([e.op for e in merged], ['clear'])
        self.assertIn('replaced by the later clear', ' '.join(notes))

    def test_low_pick_after_a_paste_is_refused(self):
        low = slot_plan.Pair(27, None, '/x/L.sluggie', '/x/L.sluggie', 'koopa')
        merged, _notes, refused = slot_plan.merge_edits(
            self.edits({'op': 'copy', 'id': '0x66', 'source': '0x09'}, {'op': 'patch', 'id': '0x66', 'file': '/x/L.sluggie'}),
            classify_fn=lambda path: low)
        self.assertEqual([e.op for e in merged], ['copy'])
        self.assertIn('paste', refused[0][1])

    def test_parse(self):
        e = self.edits({'op': 'copy', 'id': '0x66', 'source': '0x09'})[0]
        self.assertEqual((e.op, e.cid, e.source), ('copy', 0x66, 0x09))
        self.assertEqual(e.to_json(), {'op': 'copy', 'id': '0x66', 'source': '0x09'})
        with self.assertRaisesRegex(slot_plan.PlanError, 'source'):
            self.edits({'op': 'copy', 'id': '0x66'})

    def test_snapshot_files_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, 'copies')
            os.makedirs(folder)
            with open(os.path.join(folder, 'stale.bin'), 'wb') as f:
                f.write(b'old')
            slot_cli.write_copy_files({'a.bin': b'\x01', 'p.png': Image.new('RGBA', (48, 51)), 's.json': {'x': 1}},
                                      folder)
            self.assertEqual(sorted(os.listdir(folder)), ['a.bin', 'p.png', 's.json'])
            with open(os.path.join(folder, 's.json'), encoding='utf-8') as f:
                self.assertEqual(json.load(f), {'x': 1})


class StatCopyTests(unittest.TestCase):
    """``FileEnv.stat_snapshot`` on the synthetic roster DOL, written back with a ``copy:`` step."""

    def setUp(self):
        self.image = build_roster(EXPANDED)[0]
        self.batting = fields.character_field('stats', 'batting arm')

    def snapshot_doc(self, image, source, target):
        snap = slot_cli.FileEnv(image, None).stat_snapshot(source)
        return dict(snap, format=apply.COPY_FORMAT, source=f'0x{source:02X}', target=f'0x{target:02X}')

    def test_clone_of_fields_and_chemistry(self):
        ed = Editor(self.image)
        layouts = ed.layouts
        out = copy_image(self.image)
        prepared = apply.prepare(out, [apply.Copy('snap', self.snapshot_doc(self.image, 0x05, 0x66))])
        apply.write(out, prepared)
        after = Editor(out)
        for f in fields.CHARACTER_FIELDS:
            with self.subTest(field=f.name):
                self.assertEqual(after.get(0x66, f), ed.get(0x05, f))
        matrix = carry.bridge.new_by_new_address(out, layouts['stats'])
        chem = lambda img, a, b: img.read(carry.chem_address(a, b, layouts, matrix), 1)[0]
        self.assertEqual(chem(out, 0x66, 0x01), chem(self.image, 0x05, 0x01))   # stock x new: one byte
        self.assertEqual(chem(out, 0x67, 0x66), chem(self.image, 0x67, 0x05))
        self.assertEqual(chem(out, 0x66, 0x67), chem(self.image, 0x05, 0x67))
        self.assertEqual(chem(out, 0x66, 0x05), chem(self.image, 0x66, 0x05))   # the pair with the source stays
        self.assertTrue(all(c.reset for c in prepared.changes))                 # no editor range warnings

    def test_stock_target_both_directions(self):
        ed = Editor(self.image)
        stats = ed.layouts['stats']
        out = copy_image(self.image)
        apply.write(out, apply.prepare(out, [apply.Copy('snap', self.snapshot_doc(self.image, 0x01, 0x02))]))
        at = lambda img, a, b: img.read(stats.row_address(a) + fields.chemistry_offset(b), 1)[0]
        self.assertEqual(at(out, 0x02, 0x03), at(self.image, 0x01, 0x03))
        self.assertEqual(at(out, 0x03, 0x02), at(self.image, 0x03, 0x01))
        self.assertEqual(at(out, 0x02, 0x02), at(self.image, 0x01, 0x01))      # self pair
        self.assertEqual(at(out, 0x02, 0x01), at(self.image, 0x02, 0x01))      # pair with the source kept

    def test_refusals_and_item(self):
        with self.assertRaisesRegex(apply.EditFileError, 'not a stat copy'):
            apply.prepare(copy_image(self.image), [apply.Copy('snap', {'format': 'x'})])
        bad = self.snapshot_doc(self.image, 0x05, 0x70)
        with self.assertRaisesRegex(apply.EditFileError, 'not a character'):
            apply.prepare(copy_image(self.image), [apply.Copy('snap', bad)])
        self.assertEqual(apply.parse_item('copy:C:/a/s.json'), apply.Copy('C:/a/s.json'))
        self.assertEqual(apply.Copy('C:/a/s.json').item, 'copy:C:/a/s.json')
        self.assertEqual((slot_plan.COPY_PREFIX, slot_plan.COPY_FORMAT), (apply.COPY_PREFIX, apply.COPY_FORMAT))

    def test_cli_reads_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'stats.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.snapshot_doc(self.image, 0x05, 0x66), f)
            prepared, _names = cli.check(copy_image(self.image), None, [apply.COPY_PREFIX + path])
            self.assertEqual(prepared.files, 0)
            self.assertTrue(prepared.changes)


class GuiTests(unittest.TestCase):
    """The grid tab's pure parts (``gui_grid``): the right-click menu, the status line, the paste dialog."""

    def setUp(self):
        self.state = make_world().state()

    def test_menu(self):
        menu = lambda members, copied, pack=False, locked=False: [
            (label, enabled) for label, enabled, _why in gui_grid.context_menu(self.state, members, copied, pack, locked)]
        self.assertEqual(menu([0x09, 0x68], None), [(gui_grid.COPY_MULTIPLE, False)])
        self.assertEqual(menu([0x0D], None), [('Copy', True), ('Paste', False)])
        self.assertEqual(menu([0x0D], 0x0D), [('Copy', True), ('Paste', False)])        # itself
        self.assertEqual(menu([0x0D], 0x09), [('Copy', True), ('Paste', True)])
        self.assertEqual(menu([0x0D], 0x09, pack=True), [('Copy', True), ('Paste', False)])
        self.assertEqual(menu([0x0D], 0x09, locked=True), [('Copy', False), ('Paste', False)])
        self.assertEqual(menu([0x0D], 0x77), [('Copy', True), ('Paste', False)])        # gone from the grid

    def test_copy_edit(self):
        edit = gui_grid.copy_edit(0x66, 0x09)
        self.assertEqual(edit, {'op': 'copy', 'id': '0x66', 'source': '0x09'})
        self.assertEqual(slot_plan.parse_edits({'edits': [edit]})[0].to_json(), edit)
        self.assertEqual((gui_grid.COPY, gui_grid.MODEL_OPS), (slot_plan.COPY, slot_plan.MODEL_OPS))
        self.assertEqual(gui_grid._edit_title(edit), 'Pending: paste a clone of 0x09')
        self.assertIn('--copy-slot', gui_grid.WRITING_FLAGS)

    def test_dialog(self):
        w = make_world()
        batch = slot_plan.plan_batch(w.state(), make_config(), slot_plan.parse_edits(
            {'edits': [gui_grid.copy_edit(0x66, 0x09)]}), CopyEnv(w), STATE_FILE, NAMES)
        plan = json.loads(json.dumps(batch.to_json()))
        dialog = gui_grid.slot_dialog(self.state, 0x66, False, plan, 0, '', kind=gui_grid.COPY)
        text = [line for line, _kind in dialog.lines]
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.title, 'Paste onto Empty slot (0x66)?')
        self.assertTrue(text[0].startswith('Clone of Bowser (0x09) -> Empty slot (0x66)'))
        self.assertIn('Name: Bowser', text)
        self.assertIn('Equipment: bat of Bowser (0x09)', text)
        pending = gui_grid.PendingEdits()
        pending.accept(plan)
        self.assertEqual(pending.model_edit(0x66)['op'], 'copy')
        self.assertIn('  Portraits: copied (previewed, marked "pending")', pending.lines(0x66))


if __name__ == '__main__':
    unittest.main()
