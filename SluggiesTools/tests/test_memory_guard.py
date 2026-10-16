"""The player memory check before a write (``memory_guard``, ``Roster/slot_memory.py``): the selected level's limit,
the build checks' size records, the projection of a planned chain, the dry-run record and the question, the lower
player memory level, and the GUI's dialog lines."""

import json
import os
import tempfile
import unittest
from unittest import mock

from SluggiesTools import game_memory as gm
from SluggiesTools import gui_grid, memory_guard
from SluggiesTools.GameOptions import game_options as go
from SluggiesTools.GameOptions import runner as options_runner
from SluggiesTools.Roster import slot_memory
from SluggiesTools import gecko_compat
from SluggiesTools.tests.test_game_options import synthetic_dol

STOCK = gm.PlayerHeap(go.PLAYER_HEAP_STOCK)
BIG_2MB = gm.PlayerHeap(go.PLAYER_HEAP_STOCK + go.PLAYER_HEAP_SAFE_KB * 1024, go.PLAYER_HEAP_STOCK + 2048 * 1024,
                        'player_heap_big_2048')
HP, LOW, BAT = '/m/hp.sluggie', '/m/low.sluggie', '/m/bat.sluggie'
MODELS = {HP: {'ChunkNumber': 27, 'FileIndex': 0}, LOW: {'ChunkNumber': 27, 'FileIndex': 1},
          BAT: {'ChunkNumber': 27, 'FileIndex': 2}}
VANILLA = {(27, 0): 465_856, (27, 1): 84_288, (27, 2): 31_072, (27, 3): 25_600, (27, 4): 25_600, (27, 5): 0,
           (22, 0): 400_000, (22, 1): 70_000, (22, 2): 20_000, (22, 3): 20_000, (22, 4): 20_000, (22, 5): 0}


def ref(path):
    return {memory_guard.BUILD: os.path.abspath(path)}


def character(cid, model_dir, high, low, gear=(), *, own=False, source=None, template=None, name='X'):
    return {'id': cid, 'name': {'en': name}, 'model_dir': model_dir, 'own_model_dir': own, 'template': template,
            'model_source': cid if source is None else source,
            'blocks': {'high': {'length': high}, 'low': {'length': low}},
            'equipment': {f'f{f}': {'file': f, 'length': n} for f, n in gear}}


def state():
    return {'characters': [character(0x09, 27, 500_000, 90_000, ((2, 31_072), (3, 25_600)), name='Bowser'),
                           character(0x66, 22, 400_000, 70_000, template=0x04, source=0x04, name='Empty slot'),
                           character(0x67, 172, 600_000, 80_000, ((2, 40_000),), own=True, source=0x09,
                                     template=0x04, name='Koopa King')]}


def project(commands, final_config=None, st=None):
    return slot_memory.project(st or state(), final_config, commands, lambda d, f: VANILLA.get((d, f)),
                               slot_memory.display_names(st or state()), read_model=lambda p: MODELS.get(p, {}))


class SelectedHeapTests(unittest.TestCase):
    def test_the_selected_level_is_the_limit(self):
        self.assertEqual(STOCK.selected, go.PLAYER_HEAP_STOCK)
        plus64 = gm.PlayerHeap(go.PLAYER_HEAP_STOCK + 64 * 1024, level='player_heap_64')
        self.assertEqual(plus64.selected_usable, plus64.usable)
        self.assertEqual(BIG_2MB.selected, go.PLAYER_HEAP_STOCK + 2048 * 1024)   # not the +48 KB fallback

    def test_need_of_files(self):
        need = gm.need_of({0: 500_000, 1: 90_000, 2: 31_072, 3: 40_000, 5: None})
        self.assertEqual((need.high, need.low, need.gear), (500_000, 90_000, 40_000))
        self.assertEqual(need.need, 630_000 + gm.BLOCK_OVERHEAD)


class SizeRecordTests(unittest.TestCase):
    def test_records_follow_the_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'a.sluggie')
            with open(path, 'w') as f:
                f.write('{}')
            self.assertIsNone(memory_guard.lookup_size(root, path))
            memory_guard.record_size(root, path, 123_456)
            self.assertEqual(memory_guard.lookup_size(root, path), 123_456)
            with open(path, 'w') as f:
                f.write('{"changed": true}')
            self.assertIsNone(memory_guard.lookup_size(root, path))     # changed since: built again


class ProjectionTests(unittest.TestCase):
    def test_a_slot_patch_takes_the_built_blocks(self):
        out = project([('--patch', HP, LOW, '--target-id', '0x09', '--validate-only'),
                       ('--patch', HP, LOW, '--target-id', '0x09'), ('--roster-state',)])
        self.assertEqual(list(out), ['0x09'])
        after = out['0x09']['after']
        self.assertEqual((after['0'], after['1'], after['2']), (ref(HP), ref(LOW), 31_072))
        self.assertEqual(out['0x09']['before']['0'], 500_000)
        self.assertEqual(out['0x09']['name'], 'Bowser (0x09)')

    def test_as_low_loads_the_high_model_twice(self):
        after = project([('--patch', HP, '--target-id', '0x09', '--as-low')])['0x09']['after']
        self.assertEqual((after['0'], after['1']), (ref(HP), ref(HP)))

    def test_equipment_goes_to_its_target_file(self):
        after = project([('--patch', BAT, '--target-id', '0x09', '--target-file', '3')])['0x09']['after']
        self.assertEqual((after['2'], after['3']), (31_072, ref(BAT)))

    def test_fresh_directory_copy_starts_from_the_vanilla_source(self):
        config = {'ids': [{'id': '0x66', 'model': {'from': '0x09'}},
                          {'id': '0x67', 'model': {'from': '0x09', 'routes': [[1, 2]]}}]}
        out = project([('--patch', HP, '--target-id', '0x66', '--as-low')], config)
        self.assertEqual(list(out), ['0x66'])                   # 0x67 keeps its copies: untouched
        after = out['0x66']['after']
        self.assertEqual((after['0'], after['1'], after['2'], after['3']), (ref(HP), ref(HP), 31_072, 25_600))

    def test_dirless_new_ids_and_dropped_ones_are_left_out(self):
        self.assertEqual(project([('--patch', HP, '--target-id', '0x09')], {'ids': []}).keys(), {'0x09'})

    def test_unpatch_and_pack_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            block = os.path.join(tmp, 'bat.bin')
            with open(block, 'wb') as f:
                f.write(b'\0' * 5000)
            out = project([('--unpatch', '--target-id', '0x09'),
                           ('--write-slot-equipment', '0x09', '2', block),
                           ('--unpatch', '--target-id', '0x67', '--target-file', '2')])
        self.assertEqual({k: out['0x09']['after'][k] for k in '012'}, {'0': 465_856, '1': 84_288, '2': 5000})
        self.assertEqual(out['0x67']['after']['2'], 31_072)     # its own directory's source: Bowser's vanilla bat

    def test_unchanged_slots_are_not_listed(self):
        self.assertEqual(project([('--roster-state',), ('--write-slot-animation', '0x09', '6', '/x.anm')]), {})


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.hp = os.path.join(self.root, 'hp.sluggie')
        with open(self.hp, 'w') as f:
            f.write('{}')

    def section(self, high):
        return {'0x09': {'name': 'Bowser (0x09)', 'after': {'0': high, '1': 80_000, '2': 30_000}}}

    def test_over_the_selected_heap(self):
        memory_guard.record_size(self.root, self.hp, 800_000)
        problems, notes = memory_guard.evaluate(self.section(ref(self.hp)), STOCK, self.root)
        self.assertEqual(notes, [])
        self.assertEqual(problems[0]['id'], '0x09')
        self.assertEqual(problems[0]['over'], 910_404 - STOCK.selected_usable)
        self.assertIn('870,400-byte player memory (stock)', problems[0]['text'])
        self.assertEqual(memory_guard.evaluate(self.section(ref(self.hp)), BIG_2MB, self.root)[0], [])

    def test_unknown_sizes_are_noted_not_refused(self):
        problems, notes = memory_guard.evaluate(self.section(ref(self.hp)), STOCK, self.root)
        self.assertEqual(problems, [])
        self.assertIn('not checked', notes[0])


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(memory_guard.game_memory, 'load'))
        self.enterContext(mock.patch.object(memory_guard.game_memory, 'player_heap', return_value=STOCK))
        self.enterContext(mock.patch.dict(os.environ))
        os.environ.pop(memory_guard.ACCEPT_ENV, None)
        self.over = {'0x09': {'name': 'Bowser', 'after': {'0': 900_000, '1': 80_000}}}

    def test_dry_run_records_for_the_gui(self):
        with mock.patch.object(memory_guard.slogger, 'ask') as ask:
            self.assertTrue(memory_guard.guard(self.over, 'the slot edits', self.root, dry_run=True))
            ask.assert_not_called()
        details = memory_guard.read_details(self.root)
        self.assertEqual(details['problems'][0]['id'], '0x09')
        memory_guard.clear_check(self.root)
        self.assertEqual(memory_guard.read_details(self.root), {})

    def test_a_writing_run_asks_unless_accepted(self):
        with mock.patch.object(memory_guard.slogger, 'ask', return_value='n') as ask:
            self.assertFalse(memory_guard.guard(self.over, 'the slot edits', self.root))
            self.assertEqual(ask.call_args.kwargs['kind'], 'danger')
        memory_guard.accept()
        with mock.patch.object(memory_guard.slogger, 'ask') as ask:
            self.assertTrue(memory_guard.guard(self.over, 'the slot edits', self.root))
            ask.assert_not_called()

    def test_fitting_chains_pass_silently(self):
        fits = {'0x09': {'name': 'Bowser', 'after': {'0': 500_000, '1': 80_000}}}
        with mock.patch.object(memory_guard.slogger, 'ask') as ask:
            self.assertTrue(memory_guard.guard(fits, 'the slot edits', self.root))
            self.assertTrue(memory_guard.guard({}, 'the slot edits', self.root))
            ask.assert_not_called()


class LowerLevelTests(unittest.TestCase):
    """Options tab: a lower player memory level some character no longer fits asks first."""

    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.dict(os.environ, {gecko_compat.ACCEPT_ENV: '1'}))
        os.environ.pop(memory_guard.ACCEPT_ENV, None)
        self.out = os.path.join(self.tmp, '3_Output_Dat')
        os.makedirs(self.out)
        self.dol_path = os.path.join(self.out, 'main.dol')
        with open(self.dol_path, 'wb') as f:
            f.write(synthetic_dol())
        # fits +64 KB (935,856 usable), not stock (870,320)
        self.enterContext(mock.patch.object(memory_guard.game_memory, 'characters',
                                            return_value={18: gm.CharacterNeed(18, 800_000, 70_000, 20_000)}))

    def dol(self):
        with open(self.dol_path, 'rb') as f:
            return f.read()

    def test_lowering_asks_and_a_no_writes_nothing(self):
        with mock.patch.object(memory_guard.slogger, 'ask') as ask:
            options_runner.run(self.out, on=['player_heap_64'])          # raising never asks
            ask.assert_not_called()
        raised = self.dol()
        with mock.patch.object(memory_guard.slogger, 'ask', return_value='n'):
            with self.assertRaisesRegex(options_runner.GameOptionsRunError, 'player memory'):
                options_runner.run(self.out, off=['player_heap_64'])
        self.assertEqual(self.dol(), raised)
        with mock.patch.object(memory_guard.slogger, 'ask', return_value='y'):
            options_runner.run(self.out, off=['player_heap_64'])
        self.assertNotEqual(self.dol(), raised)

    def test_dry_run_records(self):
        options_runner.run(self.out, on=['player_heap_64'])
        self.assertEqual(options_runner.main(['--off', 'player_heap_64', '--dry-run', '--output-dir', self.out]), 0)
        self.assertIn('needs', memory_guard.read_details(self.tmp)['problems'][0]['text'])


class DialogTests(unittest.TestCase):
    CHECK = {'over': True, 'problems': [{'id': '0x09', 'text': 'Bowser needs ...'},
                                        {'id': '0x0D', 'text': 'Toad needs ...'}]}

    def dialog(self):
        return gui_grid.SlotDialog('Patch', [('a line', gui_grid.TEXT), ('verdict', gui_grid.OK)], True)

    def test_red_lines_above_the_verdict_and_never_skipped(self):
        dialog = gui_grid.with_memory_warning(self.dialog(), self.CHECK)
        self.assertTrue(dialog.memory_over and dialog.danger)
        self.assertEqual(dialog.lines[-1], ('verdict', gui_grid.OK))
        errors = [t for t, k in dialog.lines if k == gui_grid.ERROR]
        self.assertEqual(errors, [memory_guard.WARNING, 'Bowser needs ...', 'Toad needs ...'])   # headline first
        self.assertFalse(gui_grid.dialog_skippable(dialog))

    def test_staging_reports_only_the_new_edits_slots(self):
        plan = {'edits': [{'target': '0x09', 'checked': True}, {'target': '0x0D'}]}
        dialog = gui_grid.with_memory_warning(self.dialog(), self.CHECK, only=gui_grid.new_targets(plan))
        self.assertEqual([t for t, k in dialog.lines if k == gui_grid.ERROR], [memory_guard.WARNING, 'Toad needs ...'])
        clean = gui_grid.with_memory_warning(self.dialog(), self.CHECK, only={'0x66'})
        self.assertFalse(clean.memory_over)

    def test_pending_slots_over_the_memory_are_marked(self):
        pending = gui_grid.PendingEdits()
        plan = {'merged': [{'op': 'patch', 'id': '0x09', 'file': 'a'}], 'edits': [{'target': '0x09'}]}
        pending.accept(plan, memory=self.CHECK)               # 0x0D has no pending edit: not marked
        self.assertEqual(set(pending.memory), {0x09})
        self.assertTrue(pending.over_memory(0x09))
        self.assertTrue(pending.square_memory({'squares': [{'members': [0x09, 0x68]}]}, 0))
        self.assertEqual(pending.memory_line(0x09), 'Player memory: Bowser needs ...')
        pending.accept(plan)                                  # the next check found nothing: the mark goes
        self.assertFalse(pending.over_memory(0x09))
        pending.accept(plan, memory=self.CHECK)
        pending.discard(0x09)
        self.assertEqual(pending.memory, {})

    def test_labels_and_commands(self):
        self.assertEqual(gui_grid.anyway_label('Patch Game & copy files (3)'), 'Patch anyway')
        self.assertEqual(gui_grid.anyway_label('Stage'), 'Stage anyway')
        self.assertEqual(gui_grid.apply_command('e.json', accept_memory=True)[-1], '--accept-memory-over')
        self.assertIn('--accept-memory-over', gui_grid.load_command('p', accept_memory=True))


if __name__ == '__main__':
    unittest.main()
