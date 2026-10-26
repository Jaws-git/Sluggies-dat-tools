"""GUI character grid: the state loader, the pop-out navigation, the slot actions' confirm dialog
and the staged edits with Patch Game's summary, without a running Dear PyGui."""

import json
import os
import tempfile
import unittest

from SluggiesTools import gui_grid
from SluggiesTools.gui_grid import GRID, SLOT, SQUARE, GridNav, StateLoader


def state(*squares, luigi=True) -> dict:
    """A roster state with one cell per square; ``squares``: member lists (first = head)."""
    chars = [{'id': m, 'name': {'en': f'C{m:02X}'}, 'template': None, 'model_dir': m + 0x12, 'stats': m,
              'square': k} for k, sq in enumerate(squares) for m in sq]
    return {'kind': 'expanded', 'shape': [len(squares), 1], 'luigi_own_square': luigi,
            'cells': list(range(len(squares))),
            'squares': [{'kind': 'stock', 'head_index': k, 'head': sq[0], 'members': list(sq), 'voice': sq[0]}
                        for k, sq in enumerate(squares)],
            'characters': chars, 'warnings': []}


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(self.enterContext(tempfile.TemporaryDirectory()), 'roster_state.json')
        self.loader = StateLoader(self.path)

    def test_running_then_done(self):
        self.assertTrue(self.loader.start())
        self.assertEqual(self.loader.status, StateLoader.RUNNING)
        self.assertIn('Reading', self.loader.message)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(state([0x06, 0x42], [0x00]), f)
        self.assertFalse(self.loader.finish(0))
        self.assertEqual(self.loader.status, StateLoader.DONE)
        self.assertEqual(self.loader.message, 'Roster grid, 2x1, 2 squares')

    def test_failed_keeps_the_last_state_and_names_the_error(self):
        self.loader.state = state([0x00])
        self.loader.start()
        self.loader.finish(1, '[2026-10-04 12:00:00] [Info] [x] reading\n'
                              '[2026-10-04 12:00:00] [Error] [roster.state] main.dol is missing: run menu [1]\n')
        self.assertEqual(self.loader.status, StateLoader.FAILED)
        self.assertEqual(self.loader.message, 'Could not read the grid: main.dol is missing: run menu [1]')
        self.assertIsNotNone(self.loader.state)
        self.loader.start()
        self.loader.finish(0)                                   # exit 0 but no file
        self.assertIn('could not load', self.loader.message)

    def test_refresh_while_running_is_queued_once(self):
        self.assertTrue(self.loader.start())
        self.assertFalse(self.loader.start())
        self.assertFalse(self.loader.start())
        self.assertTrue(self.loader.finish(1, ''))             # the caller starts the queued read
        self.assertTrue(self.loader.start())
        self.assertFalse(self.loader.finish(1, ''))

    def test_chain_writes(self):
        self.assertTrue(gui_grid.chain_writes([('--export', '--untangle'), ('--export-icons',)]))
        self.assertTrue(gui_grid.chain_writes([('--patch', 'a.sluggie')]))
        self.assertFalse(gui_grid.chain_writes([('--export-icons', '--use-output'), ('--roster-state',)]))
        self.assertTrue(gui_grid.chain_writes([gui_grid.apply_command('edits.json')]))
        self.assertTrue(gui_grid.chain_writes([('--patch-slot', '0x66', 'a.sluggie'), ('--clear-slot', '0x66')]))
        self.assertFalse(gui_grid.chain_writes([gui_grid.preview_command('edits.json')]))   # dry runs write nothing
        self.assertFalse(gui_grid.chain_writes([('--clear-slot', '0x66', '--dry-run')]))
        self.assertFalse(gui_grid.chain_writes([('--patch', 'a.sluggie', '--target-id', '0x66', '--validate-only')]))


class NavTests(unittest.TestCase):
    def setUp(self):
        self.state = state([0x06, 0x42, 0x43], [0x00], [0x0D, 0x1D])
        self.nav = GridNav(self.state)

    def test_grid_square_slot_and_back(self):
        self.nav.open_square(0)
        self.assertEqual((self.nav.level, self.nav.levels()), (SQUARE, [SQUARE]))
        self.nav.open_slot(0x42)
        self.assertEqual((self.nav.level, self.nav.levels()), (SLOT, [SQUARE, SLOT]))
        self.assertTrue(self.nav.click(inside_top_box=False))
        self.assertEqual(self.nav.level, SQUARE)
        self.assertTrue(self.nav.back())                        # Esc
        self.assertEqual(self.nav.level, GRID)
        self.assertFalse(self.nav.back())

    def test_click_inside_does_not_pop(self):
        self.nav.open_square(0)
        self.nav.open_slot(0x43)
        self.assertFalse(self.nav.click(inside_top_box=True))
        self.assertEqual(self.nav.slot, 0x43)

    def test_one_member_square_skips_level_one(self):
        self.nav.open_square(1)
        self.assertEqual((self.nav.level, self.nav.levels()), (SLOT, [SLOT]))
        self.nav.back()
        self.assertEqual(self.nav.level, GRID)
        with self.assertRaises(ValueError):
            self.nav.open_slot(0x00)

    def test_refresh_reopens_the_same_square_and_id(self):
        self.nav.open_square(0)
        self.nav.open_slot(0x43)
        moved = state([0x0D, 0x1D], [0x06, 0x42, 0x43, 0x66], [0x00])      # the square moved and grew
        self.nav.refresh(moved)
        self.assertEqual((self.nav.square_index(), self.nav.slot), (1, 0x43))

    def test_refresh_falls_back_one_level(self):
        self.nav.open_square(0)
        self.nav.open_slot(0x43)
        self.nav.refresh(state([0x06, 0x42], [0x00]))                     # the ID is gone
        self.assertEqual((self.nav.level, self.nav.square_index()), (SQUARE, 0))
        self.nav.refresh(state([0x00], [0x0D, 0x1D]))                     # the square is gone
        self.assertEqual(self.nav.level, GRID)
        self.nav.open_square(0)                                           # skipped slot, square gone
        self.nav.refresh(state([0x0D, 0x1D]))
        self.assertEqual(self.nav.level, GRID)

    def test_refresh_changes_member_count(self):
        self.nav.open_square(1)                                           # one member: straight to the slot
        self.nav.refresh(state([0x06, 0x42, 0x43], [0x00, 0x66]))
        self.assertEqual((self.nav.level, self.nav.levels()), (SLOT, [SQUARE, SLOT]))
        nav = GridNav(self.state)
        nav.open_square(2)
        nav.refresh(state([0x06], [0x00], [0x0D]))                       # shrank to one member
        self.assertEqual((nav.level, nav.slot, nav.levels()), (SLOT, 0x0D, [SLOT]))


class LabelTests(unittest.TestCase):
    def test_names_and_tooltips(self):
        s = state([0x06, 0x42], [0x01], luigi=False)
        s['characters'][1]['name'] = {'en': '-'}
        self.assertEqual(gui_grid.name_of(s, 0x42), '(unnamed 0x42)')
        self.assertEqual(gui_grid.square_label(s, 0), 'C06')
        tip = gui_grid.square_tooltip(s, 1)
        self.assertIn('Voice: C01 (0x01)', tip)
        self.assertTrue(any('captain' in line for line in tip))
        self.assertIn('captain', gui_grid.stock_luigi_note(s))
        s['characters'][0].update(name={'en': '#N/A'}, default_name='Black Yoshi')
        self.assertEqual(gui_grid.name_of(s, 0x06), 'Black Yoshi')
        self.assertTrue(any('#N/A' in line for line in gui_grid.slot_details(s, 0x06)))

    def test_slot_details(self):
        s = state([0x06, 0x66])
        s['characters'][1].update(template=0x06, model_dir=0x18, stats=0x06,
                                  name={'en': 'Purple', 'fr': 'Violet', 'sp': 'Purple'})
        lines = gui_grid.slot_details(s, 0x66)
        self.assertEqual(lines[0], 'ID: 0x66')
        self.assertIn('Model: template C06 (0x06), directory 24', lines)
        self.assertIn('Stats: C06 (0x06)', lines)
        self.assertFalse(any('Violet' in line for line in lines))     # FR/SP names are not listed

    def test_slot_details_own_directory_and_blocks(self):
        s = state([0x06, 0x66])
        s['characters'][1].update(template=0x06, model_dir=172, own_model_dir=True, model_source=0x09, stats=0x09,
                                  blocks={'high': {'offset': 0, 'length': 3 * 1024 * 1024 // 4, 'sha1': 'ab' * 20},
                                          'low': {'offset': 0, 'length': 1024 * 1024 // 10, 'sha1': 'cd' * 20}})
        lines = gui_grid.slot_details(s, 0x66)
        self.assertIn('Model: own directory 172 (files of 0x09)', lines)
        self.assertIn('Blocks: High 0.75 MB [abababab], Low 0.10 MB [cdcdcdcd]', lines)


def ref(source, file='a.png', key=0x06, alias=None):
    return {'source': source, 'key': key, 'alias': alias, 'file': file}


class PortraitTests(unittest.TestCase):
    """Portrait crops and their fallback marks (``Roster/state_icons`` results in the state)."""

    def setUp(self):
        self.dir = self.enterContext(tempfile.TemporaryDirectory())
        self.state_path = os.path.join(self.dir, 'roster_state.json')
        os.makedirs(os.path.join(self.dir, 'icons'))
        open(os.path.join(self.dir, 'icons', 'a.png'), 'wb').close()
        self.s = state([0x06, 0x47, 0x66], [0x50])
        self.s['icon_dir'] = 'icons'
        icons = {0x06: (ref('own'), ref('own')), 0x47: (ref('neighbour', key=0x46), ref('own', file='gone.png')),
                 0x66: (ref('template', key=0x06, alias=0x06), None), 0x50: (ref('mii', key=None), ref('invalid'))}
        for c in self.s['characters']:
            front, side = icons[c['id']]
            c['icon'] = {'front': front, 'side': side}

    def test_icon_file(self):
        self.assertEqual(gui_grid.icon_file(self.s, self.state_path, 0x06, gui_grid.FRONT),
                         os.path.join(self.dir, 'icons', 'a.png'))
        self.assertIsNone(gui_grid.icon_file(self.s, self.state_path, 0x47, gui_grid.SIDE))    # not on disk
        self.assertIsNone(gui_grid.icon_file(self.s, self.state_path, 0x66, gui_grid.SIDE))    # not resolved
        self.s['characters'][0]['icon'] = None                                                 # no DAT
        self.assertIsNone(gui_grid.icon_file(self.s, self.state_path, 0x06, gui_grid.FRONT))

    def test_fallback_notes(self):
        self.assertFalse(gui_grid.is_fallback(self.s, 0x06, gui_grid.FRONT))
        self.assertEqual(gui_grid.icon_note(self.s, 0x06, gui_grid.FRONT), 'own portrait')
        self.assertTrue(gui_grid.is_fallback(self.s, 0x47, gui_grid.FRONT))
        self.assertEqual(gui_grid.icon_note(self.s, 0x47, gui_grid.FRONT),
                         'no own portrait: shows 0x46 (0x46), the next lower key')      # 0x46 is not on the grid
        self.assertIn('its template C06 (0x06)', gui_grid.icon_note(self.s, 0x66, gui_grid.FRONT))
        self.assertEqual(gui_grid.icon_note(self.s, 0x50, gui_grid.FRONT), 'Mii icon')
        self.assertIn('"?"', gui_grid.icon_note(self.s, 0x50, gui_grid.SIDE))
        self.assertFalse(gui_grid.is_fallback(self.s, 0x66, gui_grid.SIDE))
        self.assertEqual(gui_grid.icon_note(self.s, 0x66, gui_grid.SIDE), 'no portrait read')

    def test_square_tooltip_marks_a_fallback_head(self):
        self.assertFalse(any('Portrait' in line for line in gui_grid.square_tooltip(self.s, 0)))
        self.assertIn('Portrait: Mii icon', gui_grid.square_tooltip(self.s, 1))

    def test_loader_message_without_portraits(self):
        loader = StateLoader(self.state_path)
        loader.status, loader.state = StateLoader.DONE, dict(self.s, icons_read=False)
        self.assertTrue(loader.message.endswith('(no portraits)'))


HP = '/m/27 Bowser/114968608_koopa.gpl/114968608_koopa.gpl.sluggie'
LOW = '/m/27 Bowser/115434464_L_koopa.gpl/115434464_L_koopa.gpl.sluggie'
BUILD_OUTPUT = ('[2026-10-05 12:00:00] [Info] [hammerspace.main] Slot build check passed | Model: '
                '114968608_koopa.gpl.sluggie | Size: 0.44 MB (465856 bytes) | nothing written\n'
                '[2026-10-05 12:00:01] [Info] [hammerspace.main] Slot build check passed | Model: '
                '115434464_L_koopa.gpl.sluggie | Size: 0.08 MB (84288 bytes) | nothing written\n')


def patch_section(cid='0x66', high=HP, low=LOW, warnings=(), joined=False, effects=None):
    edit = {'op': 'patch', 'id': cid, 'file': high or low}
    if joined:
        edit['low'] = low
    return {'action': 'patch', 'target': cid, 'rebuild': True, 'source': '0x06',
            'files': {'high': high, 'low': low, 'picked': high or low},
            'commands': [['--patch', 'x', '--target-id', cid, '--validate-only'], ['--roster-state']],
            'notes': [f'a + b -> C66 ({cid})', f'C66 ({cid}) gets an own model directory: a copy of C06 (0x06)\'s files',
                      'portraits from home/icon'],
            'warnings': list(warnings), 'nothing': False, 'effects': effects or {}, 'edit': edit, 'checked': False}


def clear_section(cid='0x0D', nothing=False):
    notes = ([f'nothing to clear: C0D ({cid}) is at its baseline already'] if nothing
             else [f'C0D ({cid}): vanilla High and Low models from 1_Input'])
    return {'action': 'clear', 'target': cid, 'rebuild': False, 'commands': [], 'notes': notes, 'warnings': [],
            'nothing': nothing, 'effects': {} if nothing else {'model': 'vanilla High and Low models'},
            'edit': {'op': 'clear', 'id': cid}, 'checked': False}


def batch(*sections, skipped=(), refused=(), notes=(), rebuild=True):
    return {'action': 'batch', 'ok': not refused, 'rebuild': rebuild, 'commands': [], 'notes': list(notes),
            'warnings': [w for s in sections for w in s['warnings']], 'edits': list(sections),
            'skipped': list(skipped), 'refused': list(refused), 'merged': [s['edit'] for s in sections]}


class SlotDialogTests(unittest.TestCase):
    """The confirm dialog of "Select .sluggie..." / "Clear slot" after the staging check (High/Low warnings,
    sizes, verdict)."""

    def setUp(self):
        self.s = state([0x06, 0x66], [0x0D])

    def text(self, dialog, kind=None):
        return '\n'.join(t for t, k in dialog.lines if kind is None or k == kind)

    def test_commands(self):
        self.assertEqual(gui_grid.preview_command('e.json'), ('--apply-slots', 'e.json', '--dry-run'))
        self.assertEqual(gui_grid.apply_command('e.json'), ('--apply-slots', 'e.json'))

    def test_build_sizes(self):
        self.assertEqual(gui_grid.build_sizes(BUILD_OUTPUT), {'114968608_koopa.gpl.sluggie': 465856,
                                                              '115434464_L_koopa.gpl.sluggie': 84288})
        self.assertEqual(gui_grid.build_sizes('nothing here'), {})

    def test_load_plan(self):
        path = os.path.join(self.enterContext(tempfile.TemporaryDirectory()), 'plan.json')
        self.assertIsNone(gui_grid.load_plan(path))                            # the planner failed: no plan file
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(batch(patch_section()), f)
        self.assertEqual(gui_grid.load_plan(path)['edits'][0]['source'], '0x06')

    def test_pair_passed(self):
        dialog = gui_grid.slot_dialog(self.s, 0x66, True, batch(patch_section()), 0, BUILD_OUTPUT)
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.title, 'Put a model into C66 (0x66)?')
        text = self.text(dialog)
        self.assertIn('Source: C06 (0x06) -> C66 (0x66)', text)
        self.assertIn('Models: 114968608_koopa.gpl.sluggie (High, 0.44 MB) + 115434464_L_koopa.gpl.sluggie (Low, '
                      '0.08 MB), found side by side', text)
        self.assertIn('- C66 (0x66) gets an own model directory', text)
        self.assertIn('- portraits from home/icon', text)
        self.assertNotIn('a + b ->', text)                                     # shown as the Models line
        self.assertIn('Stage adds the edit to the pending list', self.text(dialog, gui_grid.OK))
        self.assertEqual(self.text(dialog, gui_grid.WARN), '')

    def test_joined_pair_and_merge_notes(self):
        plan = batch(patch_section(joined=True), notes=['0x66: L.sluggie joins the pending H.sluggie as its Low partner',
                                                        '0x0D: the pending patch is replaced by the later clear'])
        text = self.text(gui_grid.slot_dialog(self.s, 0x66, True, plan, 0, BUILD_OUTPUT))
        self.assertIn('the Low pick joins the pending High pick', text)
        self.assertIn('- L.sluggie joins the pending H.sluggie', text)
        self.assertNotIn('replaced by the later clear', text)                 # another slot's note

    def test_hp_only_warns_with_the_combined_size(self):
        plan = batch(patch_section(low=None, warnings=['koopa has no Low partner beside it: ...']))
        dialog = gui_grid.slot_dialog(self.s, 0x66, True, plan, 0, BUILD_OUTPUT)
        self.assertTrue(dialog.can_apply)                                      # OK and Cancel
        warn = self.text(dialog, gui_grid.WARN)
        self.assertIn('no Low partner beside it', warn)
        self.assertIn('loads it twice on the field: 0.89 MB together', warn)
        self.assertIn('Warning: koopa has no Low partner', warn)

    def test_low_only(self):
        dialog = gui_grid.slot_dialog(self.s, 0x66, True, batch(patch_section(high=None)), 0, BUILD_OUTPUT)
        self.assertTrue(dialog.can_apply)
        self.assertIn('115434464_L_koopa.gpl.sluggie (Low only, 0.08 MB), no High partner',
                      self.text(dialog, gui_grid.WARN))

    def test_refused_by_the_planner(self):
        refused = batch(refused=[{'edit': {'op': 'patch', 'id': '0x0D', 'file': HP}, 'target': '0x0D',
                                  'error': 'the skeletons do not match (105 vs 89 bones)'}])
        dialog = gui_grid.slot_dialog(self.s, 0x0D, True, refused, 1, '')
        self.assertFalse(dialog.can_apply)                                     # Close only
        self.assertEqual(dialog.title, 'C0D (0x0D): refused')
        self.assertIn('Refused: the skeletons do not match', self.text(dialog, gui_grid.ERROR))
        self.assertIn('Nothing was staged', self.text(dialog))

    def test_refused_pending_edit_of_another_slot_is_named(self):
        refused = batch(refused=[{'edit': {'op': 'clear', 'id': '0x06'}, 'target': '0x06',
                                  'error': '0x06 is not a slot of this roster'}])
        text = self.text(gui_grid.slot_dialog(self.s, 0x0D, False, refused, 1, ''))
        self.assertIn('Refused: C06 (0x06): 0x06 is not a slot', text)
        self.assertIn('discard it on its slot', text)

    def test_planner_failure_without_plan(self):
        output = '[2026-10-05 12:00:00] [Error] [roster.slot] refused, nothing written: main.dol is missing\n'
        dialog = gui_grid.slot_dialog(self.s, 0x0D, True, None, 1, output)
        self.assertFalse(dialog.can_apply)
        self.assertIn('Refused: main.dol is missing', self.text(dialog, gui_grid.ERROR))

    def test_failed_build_check(self):
        output = '[2026-10-05 12:00:00] [Error] [hammerspace.main] Hammerspace operation failed | x\n'
        dialog = gui_grid.slot_dialog(self.s, 0x66, True, batch(patch_section()), 1, output)
        self.assertFalse(dialog.can_apply)
        self.assertIn('Build check failed: Hammerspace operation failed | x', self.text(dialog, gui_grid.ERROR))
        self.assertIn('- portraits from home/icon', self.text(dialog))           # the plan is still shown

    def test_clear(self):
        dialog = gui_grid.slot_dialog(self.s, 0x0D, False, batch(clear_section(), rebuild=False), 0, '')
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.title, 'Clear C0D (0x0D)?')
        self.assertIn('vanilla High and Low models', self.text(dialog))
        self.assertIn('no roster rebuild needed', self.text(dialog))
        self.assertNotIn('Source', self.text(dialog))

    def test_nothing_to_clear(self):
        plan = batch(skipped=[clear_section(nothing=True)])
        dialog = gui_grid.slot_dialog(self.s, 0x0D, False, plan, 0, '', gui_grid.PendingEdits())
        self.assertFalse(dialog.can_apply)                                     # Close only: nothing is staged
        self.assertEqual(dialog.title, 'C0D (0x0D): nothing to clear')
        pending = gui_grid.PendingEdits()
        pending.accept(batch(patch_section('0x0D')))
        dialog = gui_grid.slot_dialog(self.s, 0x0D, False, plan, 0, '', pending)
        self.assertTrue(dialog.can_apply)                                      # Stage drops the pending patch
        self.assertIn("Stage drops the slot's pending patch", self.text(dialog, gui_grid.OK))


class PendingEditsTests(unittest.TestCase):
    """The pending list: staging files, accept / replace / discard, the overlay text."""

    def setUp(self):
        self.s = state([0x06, 0x66], [0x0D])
        self.p = gui_grid.PendingEdits()
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())

    def test_staging_file_marks_the_pending_edits_checked(self):
        self.p.accept(batch(patch_section()))
        self.assertEqual(self.p.staging({'op': 'clear', 'id': '0x0D'}),
                         {'edits': [{'op': 'patch', 'id': '0x66', 'file': HP, 'checked': True},
                                    {'op': 'clear', 'id': '0x0D'}]})
        self.assertEqual(self.p.to_file(), {'edits': [{'op': 'patch', 'id': '0x66', 'file': HP}]})   # all re-checked

    def test_gecko_marks(self):
        def checked(section):
            return dict(section, checked=True)
        self.p.accept(batch(patch_section()), {'breaks': True, 'new_breaks': True})
        self.assertTrue(self.p.breaks_gecko(0x66))
        self.assertTrue(self.p.square_gecko(self.s, 0))
        self.assertFalse(self.p.square_gecko(self.s, 1))
        # another slot's edit while 0x66 breaks it: marked only when it breaks it on its own
        self.p.accept(batch(checked(patch_section()), clear_section()), {'breaks': True, 'new_breaks': False})
        self.assertEqual(self.p.gecko, {0x66})
        self.p.accept(batch(checked(patch_section()), clear_section()), {'breaks': True, 'new_breaks': None})
        self.assertEqual(self.p.gecko, {0x66})
        self.p.accept(batch(checked(patch_section()), clear_section()), {'breaks': True, 'new_breaks': True})
        self.assertEqual(self.p.gecko, {0x66, 0x0D})
        self.p.discard(0x66)
        self.assertEqual(self.p.gecko, {0x0D})
        self.p.accept(batch(checked(clear_section())), {})       # nothing breaks it (or incompatible already)
        self.assertEqual(self.p.gecko, set())

    def test_gecko_completing_edit_is_marked(self):
        self.p.accept(batch(patch_section()), {})
        self.p.accept(batch(dict(patch_section(), checked=True), clear_section()),
                      {'breaks': True, 'new_breaks': False})          # neither alone, both together
        self.assertEqual(self.p.gecko, {0x0D})
        self.p.clear()
        self.assertFalse(self.p.breaks_gecko(0x0D))

    def test_gecko_ignores_stat_edit_files_and_slots_no_longer_pending(self):
        stat = {'action': gui_grid.STAT_EDITS, 'target': '0xFF', 'warnings': [],
                'edit': {'op': gui_grid.STAT_EDITS, 'id': '0xFF'},
                'checked': False}
        self.p.accept(batch(stat), {'breaks': True, 'new_breaks': True})
        self.assertEqual(self.p.gecko, set())
        self.p.gecko = {0x66}                                          # left from an edit dropped since
        self.assertFalse(self.p.breaks_gecko(0x66))

    def test_accept_replaces_and_discard(self):
        self.p.accept(batch(patch_section(), clear_section()))
        self.assertEqual(len(self.p), 2)
        self.assertTrue(self.p.square_pending(self.s, 0) and self.p.square_pending(self.s, 1))
        self.p.accept(batch(clear_section()))                 # the staging check merged the 0x66 patch away
        self.assertFalse(self.p.has(0x66))
        self.p.discard(0x0D)
        self.assertEqual((len(self.p), self.p.sections), (0, []))
        self.p.accept(batch(patch_section()))
        self.p.clear()
        self.assertEqual(len(self.p), 0)

    def test_overlay_lines_and_portrait_preview(self):
        front = os.path.join(self.tmp, 'Bowser', 'icon', 'FrontIcon.png')
        os.makedirs(os.path.dirname(front))
        with open(front, 'wb') as f:
            f.write(b'png')
        effects = {'model': 'Bowser (0x09)\'s models in an own directory', 'name': 'Bowser', 'stats': 'Bowser (0x09)',
                   'voice': 'Bowser (0x09) (square voice)',
                   'portraits': {'front': front, 'side': os.path.join(self.tmp, 'missing.png')}}
        self.p.accept(batch(patch_section(effects=effects), clear_section()))
        lines = self.p.lines(0x66)
        self.assertEqual(lines[0], 'Pending: put 114968608_koopa.gpl.sluggie into this slot')
        self.assertIn('  Name: Bowser', lines)
        self.assertIn('  Voice: Bowser (0x09) (square voice)', lines)
        self.assertIn('  Portraits: Bowser (previewed, marked "pending")', lines)
        self.assertEqual(self.p.portrait(0x66, gui_grid.FRONT), front)
        self.assertIsNone(self.p.portrait(0x66, gui_grid.SIDE))                # missing file: the game's crop stays
        self.assertEqual(self.p.lines(0x0D), ['Pending: clear this slot', '  Model: vanilla High and Low models'])
        self.assertEqual(self.p.summary(0x0D), ['Pending: clear this slot'])
        self.assertEqual(self.p.lines(0x06), [])

    def test_stat_edits_mark_the_characters_their_file_names(self):
        path = os.path.join(self.tmp, 'stat_edits.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'format': 'sluggies-stat-edits', 'characters': {'0x66': {'stats': {}}, 'bad': {}}}, f)
        self.p.edits = [gui_grid.stat_edit(path), {'op': 'clear', 'id': '0x0D'}]
        self.assertEqual(gui_grid.stat_file_ids(path), {0x66})
        self.assertTrue(self.p.has_stats(0x66) and self.p.square_stats(self.s, 0))
        self.assertFalse(self.p.has_stats(0x0D) or self.p.square_stats(self.s, 1))
        self.assertFalse(self.p.has(0x66))                      # a game-wide edit: not the slot's own pending edit
        self.assertEqual(self.p.stat_summary(0x66), ['Pending: stat editor value changes'])
        self.assertEqual(gui_grid.stat_file_ids(os.path.join(self.tmp, 'missing.json')), frozenset())

    def test_write_edits(self):
        path = os.path.join(self.tmp, 'slot', 'edits.json')
        gui_grid.write_edits(path, {'edits': []})
        with open(path, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'edits': []})


def rename_section(cid='0x0D', text='Little Toad', nothing=False):
    notes = ([f'nothing to rename: C0D ({cid}) is named that already'] if nothing
             else [f'C0D ({cid}): renamed to {text!r} (English, French and Spanish)'])
    return {'action': 'rename', 'target': cid, 'rebuild': not nothing, 'commands': [], 'notes': notes,
            'warnings': [], 'nothing': nothing, 'effects': {} if nothing else {'name': text or 'its stock name'},
            'edit': {'op': 'rename', 'id': cid, 'text': text}, 'checked': False}


class RenameTests(unittest.TestCase):
    """The rename dialog's live check, the pending overlay and the confirm dialog."""

    def setUp(self):
        self.s = state([0x06, 0x66], [0x0D])
        self.font = os.path.join(os.path.dirname(gui_grid.__file__), 'Roster', 'fonts', 'OpenSans.ttf')

    def text(self, dialog, kind=None):
        return '\n'.join(t for t, k in dialog.lines if kind is None or k == kind)

    def test_name_problem_is_the_planners_rule(self):
        from SluggiesTools.Roster import names
        for text in ('Little Toad', 'Purple Yoshi', 'W' * 30, 'The Extraordinarily Long Toad Name', '', '  ', ' Toad',
                     'To\nad', 'iiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiii'):
            with self.subTest(text=text):
                self.assertEqual(gui_grid.name_problem(text, self.font) is None, names.fits(text))
        self.assertIn('too long', gui_grid.name_problem('W' * 30, self.font))
        self.assertIsNone(gui_grid.name_problem('Toad', os.path.join(self.font, 'missing.ttf')))   # no font: planner

    def test_prefill(self):
        self.s['characters'][0]['name'] = {'en': 'Red Toad'}
        self.s['characters'][1]['name'] = {'en': gui_grid.UNNAMED}
        self.assertEqual(gui_grid.rename_prefill(self.s, 0x06), 'Red Toad')
        self.assertEqual(gui_grid.rename_prefill(self.s, 0x66), '')
        self.s['characters'][0]['default_name'] = 'Black Yoshi'
        self.assertEqual(gui_grid.rename_prefill(self.s, 0x06), 'Black Yoshi')
        self.assertEqual(gui_grid.rename_prefill(self.s, 0x99), '')

    def test_dialog(self):
        dialog = gui_grid.slot_dialog(self.s, 0x0D, False, batch(rename_section(), rebuild=True), 0, '', rename=True)
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.title, 'Rename C0D (0x0D)?')
        self.assertIn("renamed to 'Little Toad'", self.text(dialog))
        self.assertIn('Checks passed: slot rules.', self.text(dialog, gui_grid.OK))
        self.assertNotIn('and every model built', self.text(dialog))

    def test_nothing_to_rename(self):
        plan = batch(skipped=[rename_section(nothing=True)])
        pending = gui_grid.PendingEdits()
        dialog = gui_grid.slot_dialog(self.s, 0x0D, False, plan, 0, '', pending, rename=True)
        self.assertFalse(dialog.can_apply)
        self.assertEqual(dialog.title, 'C0D (0x0D): nothing to rename')
        pending.accept(batch(rename_section(text='Other')))                    # undoing a pending rename is allowed
        dialog = gui_grid.slot_dialog(self.s, 0x0D, False, plan, 0, '', pending, rename=True)
        self.assertTrue(dialog.can_apply)
        self.assertIn("Stage drops the slot's pending rename", self.text(dialog, gui_grid.OK))
        pending.accept(batch(clear_section()))                                  # a pending clear is not a rename
        self.assertFalse(gui_grid.slot_dialog(self.s, 0x0D, False, plan, 0, '', pending, rename=True).can_apply)

    def test_pending_lines_and_titles(self):
        pending = gui_grid.PendingEdits()
        empty = rename_section('0x67', '')
        empty['effects']['name'] = 'Empty slot'                                # a new ID resets to the open-slot name
        pending.accept(batch(rename_section(), rename_section('0x66', ''), empty))
        self.assertEqual(pending.lines(0x0D), ["Pending: rename to 'Little Toad'"])
        self.assertEqual(pending.lines(0x66), ['Pending: reset the name to its stock name'])
        self.assertEqual(pending.lines(0x67), ["Pending: reset the name to 'Empty slot'"])
        self.assertIsNotNone(pending.value_edit(0x0D, 'rename'))
        self.assertIsNone(pending.model_edit(0x0D))                            # a rename is not a model edit
        self.assertEqual(pending.staging({'op': 'rename', 'id': '0x06', 'text': 'X'})['edits'][-1],
                         {'op': 'rename', 'id': '0x06', 'text': 'X'})

    def test_summary_lines(self):
        plan = batch(rename_section(), rename_section('0x66', ''), skipped=[rename_section('0x06', nothing=True)])
        text = self.text(gui_grid.summary_dialog(self.s, plan, 0, ''))
        self.assertIn("C0D (0x0D): rename to 'Little Toad'", text)
        self.assertIn('C66 (0x66): reset the name', text)
        self.assertIn('C06 (0x06): nothing to rename', text)
        self.assertIn('--rename-slot', ' '.join(gui_grid.WRITING_FLAGS))


class CpuVsCpuTests(unittest.TestCase):
    def test_status(self):
        status = gui_grid.cpu_vs_cpu_status
        self.assertEqual(status(None), ('Cpu vs Cpu: unknown', None))
        self.assertEqual(status({'squares': []}), ('Cpu vs Cpu: unknown', None))     # state from an older reader
        self.assertEqual(status({'game_options': []}), ('Cpu vs Cpu: disabled', False))
        self.assertEqual(status({'game_options': ['cpu_management']}), ('Cpu vs Cpu: disabled', False))
        self.assertEqual(status({'game_options': ['cpu_vs_cpu']}), ('Cpu vs Cpu: enabled', True))

    def test_management_status(self):
        status = gui_grid.cpu_management_status
        self.assertEqual(status(None), ('Management: unknown', None))
        self.assertEqual(status({'game_options': ['cpu_vs_cpu']}), ('Management: disabled', False))
        self.assertEqual(status({'game_options': ['cpu_management']}),
                         ('Management: enabled (no effect without CPU vs CPU)', False))
        self.assertEqual(status({'game_options': ['cpu_vs_cpu', 'cpu_management']}), ('Management: enabled', True))

    def test_edits_stage_known_options(self):
        from GameOptions import game_options
        from Roster import slot_plan
        self.assertEqual(gui_grid.GAME_OPTION, slot_plan.GAME_OPTION)
        self.assertEqual(gui_grid.GAME_OPTION_TITLES, slot_plan.GAME_OPTION_TITLES)
        self.assertTrue(set(slot_plan.GAME_OPTION_TITLES) <= set(game_options.BY_KEY))
        self.assertEqual(gui_grid.cpu_vs_cpu_edits(), [{'op': 'game_option', 'id': '0xFF', 'file': 'on:cpu_vs_cpu'}])
        self.assertEqual(gui_grid.cpu_management_edit(False)['file'], 'off:cpu_management')
        # turning CPU vs CPU off takes management with it (its button is locked afterwards); no close play level
        self.assertEqual([e['file'] for e in gui_grid.cpu_vs_cpu_edits(False)],
                         ['off:cpu_vs_cpu', 'off:cpu_management'])
        self.assertTrue(gui_grid.chain_writes([('--game-options', '--on', 'cpu_vs_cpu')]))

    def test_pending_edits_count(self):
        pending = gui_grid.PendingEdits()
        pending.edits = gui_grid.cpu_vs_cpu_edits()
        state = {'game_options': []}
        self.assertEqual(gui_grid.cpu_vs_cpu_button(state, pending), (gui_grid.CPU_VS_CPU_DISABLE, False))
        self.assertTrue(gui_grid.cpu_management_button(state, pending)[2])     # usable: CPU vs CPU is staged on
        self.assertEqual(gui_grid.cpu_vs_cpu_status(state, pending),
                         ('Cpu vs Cpu: disabled (pending: enabled)', False))
        self.assertEqual(gui_grid._edit_title(pending.edits[0]), 'Pending: turn on CPU vs CPU')
        self.assertEqual(pending.titles(), [(None, 'turn on CPU vs CPU')])

    def test_planner(self):
        from Roster import slot_plan

        class Env(slot_plan.Env):
            def game_options(self):
                return ['cpu_vs_cpu']

        def plan(items):
            return slot_plan.plan_batch({'characters': [], 'squares': []}, {}, slot_plan.parse_edits(items), Env(),
                                        'state.json')
        on = {'op': 'game_option', 'id': '0xFF', 'file': 'on:cpu_management'}
        off = {'op': 'game_option', 'id': '0xFF', 'file': 'off:cpu_vs_cpu'}
        b = plan([on, off])
        self.assertTrue(b.ok, b.refused)
        self.assertEqual(b.commands, [('--game-options', '--on', 'cpu_management', '--off', 'cpu_vs_cpu'),
                                      ('--roster-state',)])
        # the last edit per option wins; back to what the game has: nothing to write, nothing stays pending
        b = plan([off, dict(off, file='on:cpu_vs_cpu')])
        self.assertEqual((b.commands, b.to_json()['merged'], [p.action for _e, p in b.skipped]),
                         ([], [], ['game_option']))
        b = plan([dict(on, file='on:player_heap_32')])
        self.assertTrue(b.refused)
        with self.assertRaises(slot_plan.PlanError):
            slot_plan.parse_edits([{'op': 'game_option'}])

    def test_buttons(self):
        button = gui_grid.cpu_vs_cpu_button
        self.assertEqual(button(None), (gui_grid.CPU_VS_CPU_ENABLE, True))
        self.assertEqual(button({'game_options': ['cpu_management']}), (gui_grid.CPU_VS_CPU_ENABLE, True))
        self.assertEqual(button({'game_options': ['cpu_vs_cpu']}), (gui_grid.CPU_VS_CPU_DISABLE, False))

    def test_management_button_needs_cpu_vs_cpu(self):
        button = gui_grid.cpu_management_button
        self.assertEqual(button(None), (gui_grid.CPU_MANAGEMENT_ENABLE, True, False))
        self.assertEqual(button({'game_options': []}), (gui_grid.CPU_MANAGEMENT_ENABLE, True, False))
        self.assertEqual(button({'game_options': ['cpu_management']}), (gui_grid.CPU_MANAGEMENT_DISABLE, False, False))
        self.assertEqual(button({'game_options': ['cpu_vs_cpu']}), (gui_grid.CPU_MANAGEMENT_ENABLE, True, True))
        self.assertEqual(button({'game_options': ['cpu_vs_cpu', 'cpu_management']}),
                         (gui_grid.CPU_MANAGEMENT_DISABLE, False, True))


class CpuVsCpuExtrasTests(unittest.TestCase):
    """Close play odds (one level of a group at a time) and the CPU fixes (plain toggles)."""

    @staticmethod
    def pending(*files):
        pending = gui_grid.PendingEdits()
        pending.edits = [{'op': 'game_option', 'id': '0xFF', 'file': f} for f in files]
        return pending

    def test_levels_and_groups_mirror_the_game_options(self):
        from GameOptions import game_options
        from Roster import slot_plan
        self.assertEqual(gui_grid.CLOSE_PLAY_LEVELS, game_options.CLOSE_PLAY_LEVELS)
        self.assertEqual(gui_grid.CLOSE_PLAY_LEVELS, slot_plan.CLOSE_PLAY_LEVELS)
        self.assertEqual(gui_grid.CLOSE_PLAY_GECKO, game_options.CLOSE_PLAY_GECKO)
        self.assertEqual(gui_grid.GAME_OPTION_GROUPS, slot_plan.GAME_OPTION_GROUPS)
        self.assertEqual(gui_grid.GAME_OPTION_GROUPS, {o.key: o.group for o in game_options.OPTIONS
                                                       if o.key in slot_plan.GAME_OPTION_TITLES and o.group})
        self.assertTrue({key for key, _label, _tip in gui_grid.CPU_FIXES} <= set(slot_plan.GAME_OPTION_TITLES))
        for key, _label, _tip in gui_grid.CPU_FIXES:       # plain patches / floors: no DOL hammerspace
            self.assertFalse(game_options.BY_KEY[key].hooks, key)

    def test_snap(self):
        self.assertEqual([gui_grid.close_play_snap(v) for v in (0, 4, 5, 46, 100, 130, -5, None)],
                         [0, 0, 0, 50, 100, 100, 0, gui_grid.CLOSE_PLAY_GECKO])

    def test_a_staged_level_replaces_the_game_level(self):
        state = {'game_options': ['cpu_vs_cpu', 'close_play_30']}
        self.assertEqual(gui_grid.close_play_level(state), 30)
        pending = self.pending('on:close_play_70')
        self.assertEqual(gui_grid._effective_options(state, pending), {'cpu_vs_cpu', 'close_play_70'})
        self.assertEqual(gui_grid.close_play_status(state, pending), ('Close plays: 30% (pending: 70%)', True))
        self.assertEqual(gui_grid.close_play_buttons(state, pending, 70), (False, True))
        self.assertEqual(gui_grid.close_play_buttons(state, pending, 40), (True, True))
        # off names the game's level, so the planner drops the pending one too
        self.assertEqual(gui_grid.close_play_edit(state, pending, None)['file'], 'off:close_play_30')
        self.assertEqual(gui_grid.close_play_edit({'game_options': ['cpu_vs_cpu']}, pending, None)['file'],
                         'off:close_play_70')
        self.assertIsNone(gui_grid.close_play_edit({'game_options': ['cpu_vs_cpu']}, None, None))

    def test_close_plays_need_cpu_vs_cpu(self):
        self.assertEqual(gui_grid.close_play_buttons({'game_options': []}, None, 50), (False, False))
        self.assertEqual(gui_grid.close_play_buttons({'game_options': []}, self.pending('on:cpu_vs_cpu'), 50),
                         (True, False))
        self.assertEqual(gui_grid.close_play_status({'game_options': ['close_play_50']}),
                         ('Close plays: 50% (no effect without CPU vs CPU)', False))
        self.assertEqual(gui_grid.close_play_status(None), ('Close plays: unknown', None))
        self.assertEqual(gui_grid.close_play_status({'game_options': []}), ('Close plays: stock', False))
        # turning CPU vs CPU off takes the close play level along
        state = {'game_options': ['cpu_vs_cpu', 'close_play_50']}
        self.assertEqual([e['file'] for e in gui_grid.cpu_vs_cpu_edits(False, state)],
                         ['off:cpu_vs_cpu', 'off:cpu_management', 'off:close_play_50'])

    def test_cpu_fixes(self):
        state = {'game_options': ['triple_take']}
        self.assertEqual(gui_grid.cpu_fix_button(state, None, 'triple_take'), ('Disable triple take home runs', False))
        self.assertEqual(gui_grid.cpu_fix_button(state, None, 'no_precharge'), ('Enable precharge removal', True))
        self.assertEqual(gui_grid.cpu_fix_status(state, self.pending('off:triple_take'), 'triple_take'),
                         ('Triple take home runs: enabled (pending: disabled)', True))
        self.assertEqual(gui_grid.cpu_fix_status(None, None, 'outfield_dives'),
                         ('Outfield dives and jumps: unknown', None))
        # the fixes write the same as their Gecko codes in place: no warning for them alone
        self.assertFalse(gui_grid.cpu_gecko_warning(state))
        self.assertTrue(gui_grid.cpu_gecko_warning(state, self.pending('on:close_play_50')))

    def test_extras(self):
        self.assertTrue({key for key, _label, _tip in gui_grid.EXTRAS} <= set(gui_grid.GAME_OPTION_TITLES))
        state = {'game_options': ['all_captains']}
        self.assertEqual(gui_grid.cpu_fix_button(state, None, 'all_captains'),
                         ('Disable every character a captain', False))
        self.assertEqual(gui_grid.cpu_fix_status(state, self.pending('on:duplicate_captains'), 'duplicate_captains'),
                         ('Duplicate captains: disabled (pending: enabled)', False))
        self.assertTrue(gui_grid.extras_gecko_warning(state))
        self.assertFalse(gui_grid.extras_gecko_warning({'game_options': ['triple_take']}))
        self.assertFalse(gui_grid.extras_gecko_warning(state, self.pending('off:all_captains')))

    def test_extras_99_0(self):
        self.assertTrue({key for key, _label, _tip in gui_grid.EXTRAS_99_0} <= set(gui_grid.GAME_OPTION_TITLES))
        self.assertTrue(gui_grid.extras_gecko_warning({'game_options': ['extra_innings']}))
        # 999 runs turns "always away team" on with it (the Gecko code has its line), not off
        self.assertEqual(gui_grid.cpu_fix_edits({'game_options': []}, None, 'score_999'),
                         [gui_grid.game_option_edit('score_999', True), gui_grid.game_option_edit('always_away', True)])
        self.assertEqual(gui_grid.cpu_fix_edits({'game_options': ['always_away']}, None, 'score_999'),
                         [gui_grid.game_option_edit('score_999', True)])
        self.assertEqual(gui_grid.cpu_fix_edits({'game_options': ['score_999', 'always_away']}, None, 'score_999'),
                         [gui_grid.game_option_edit('score_999', False)])

    def test_extras_misc(self):
        from GameOptions import game_options
        self.assertTrue({key for key, _label, _tip in gui_grid.EXTRAS_MISC} <= set(gui_grid.GAME_OPTION_TITLES))
        self.assertTrue(gui_grid.extras_gecko_warning({'game_options': ['night_music']}))
        self.assertEqual(gui_grid.MERCY_RULE_STOCK, game_options.MERCY_RULE_STOCK_RUNS)
        self.assertEqual(gui_grid.MERCY_RULE_LEVELS, game_options.MERCY_RULE_LEVELS)

    def test_mercy_rule_lead(self):
        self.assertEqual([gui_grid.mercy_rule_clamp(v) for v in (0, 1, 15, 99, 150, None, 'x')],
                         [1, 1, 15, 99, 99, gui_grid.MERCY_RULE_STOCK, gui_grid.MERCY_RULE_STOCK])
        stock = {'game_options': []}
        self.assertEqual(gui_grid.mercy_rule_level(stock), 10)
        self.assertEqual(gui_grid.mercy_rule_status(stock), ('Mercy rule: stock (10 runs)', False))
        self.assertEqual(gui_grid.mercy_rule_buttons(stock, None, 10), (False, False))
        self.assertEqual(gui_grid.mercy_rule_buttons(stock, None, 5), (True, False))
        self.assertIsNone(gui_grid.mercy_rule_edit(stock, None, None))
        self.assertIsNone(gui_grid.mercy_rule_edit(stock, None, 10))
        self.assertEqual(gui_grid.mercy_rule_edit(stock, None, 5)['file'], 'on:mercy_rule_5')
        state = {'game_options': ['mercy_rule_15']}
        pending = self.pending('on:mercy_rule_20')
        self.assertEqual(gui_grid.mercy_rule_level(state, pending), 20)
        self.assertEqual(gui_grid.mercy_rule_status(state, pending), ('Mercy rule: 15 runs (pending: 20 runs)', True))
        self.assertEqual(gui_grid.mercy_rule_buttons(state, pending, 20), (False, True))
        self.assertEqual(gui_grid.mercy_rule_buttons(state, pending, 10), (True, True))
        # back to stock names the game's level, so the planner drops the pending one too
        self.assertEqual(gui_grid.mercy_rule_edit(state, pending, 10)['file'], 'off:mercy_rule_15')
        self.assertEqual(gui_grid.mercy_rule_edit(stock, pending, None)['file'], 'off:mercy_rule_20')
        self.assertTrue(gui_grid.extras_gecko_warning(state))
        # a mercy rule edit is not a close play edit
        self.assertEqual(gui_grid.close_play_status({'game_options': []}, pending), ('Close plays: stock', False))

    def test_planner_merges_a_group(self):
        from Roster import slot_plan

        class Env(slot_plan.Env):
            def game_options(self):
                return ['cpu_vs_cpu', 'close_play_30']

        def plan(*files):
            items = [{'op': 'game_option', 'id': '0xFF', 'file': f} for f in files]
            return slot_plan.plan_batch({'characters': [], 'squares': []}, {}, slot_plan.parse_edits(items), Env(),
                                        'state.json')
        b = plan('on:close_play_60', 'on:close_play_80', 'on:no_precharge')
        self.assertEqual(b.commands[0], ('--game-options', '--on', 'close_play_80', 'no_precharge'))
        b = plan('on:close_play_60', 'off:close_play_30')       # the later off replaces the staged level
        self.assertEqual(b.commands[0], ('--game-options', '--off', 'close_play_30'))
        b = plan('on:close_play_60', 'on:close_play_30')        # back to the game's level: nothing to write
        self.assertEqual(b.commands, [])


class StrikeZoneTests(unittest.TestCase):
    def test_button_status_command(self):
        from GameOptions import game_options
        self.assertIn(gui_grid.STRIKE_ZONE_KEY, game_options.BY_KEY)
        self.assertFalse(game_options.BY_KEY[gui_grid.STRIKE_ZONE_KEY].hooks)       # no DOL hammerspace
        self.assertEqual(gui_grid.strike_zone_button({'game_options': []}), (gui_grid.STRIKE_ZONE_ENABLE, True))
        self.assertEqual(gui_grid.strike_zone_button({'game_options': ['unused_strike_zones']}),
                         (gui_grid.STRIKE_ZONE_DISABLE, False))
        self.assertEqual(gui_grid.strike_zone_status(None), ('Strike zone fix: unknown', None))
        self.assertTrue(gui_grid.strike_zone_status({'game_options': ['unused_strike_zones']})[1])
        self.assertEqual(gui_grid.strike_zone_command(False), ('--game-options', '--off', 'unused_strike_zones'))


class CheatTests(unittest.TestCase):
    def test_keys_are_game_options(self):
        from GameOptions import game_options
        for key, _label, _tip in gui_grid.CHEATS:
            self.assertIn(key, game_options.BY_KEY)

    def test_button_status_command(self):
        self.assertEqual(gui_grid.cheat_button({'game_options': []}, 'all_items'), ('Enable all items', True))
        self.assertEqual(gui_grid.cheat_button({'game_options': ['all_items']}, 'all_items'),
                         ('Disable all items', False))
        self.assertEqual(gui_grid.cheat_button(None, 'reset_outs'), ('Enable reset outs (B + 1)', True))
        self.assertEqual(gui_grid.cheat_status(None, 'all_items'), ('unknown', None))
        self.assertEqual(gui_grid.cheat_status({'game_options': ['all_items']}, 'all_items'), ('on', True))
        self.assertEqual(gui_grid.cheat_status({'game_options': ['all_stars']}, 'all_items'), ('off', False))
        self.assertEqual(gui_grid.cheat_command('all_stars', True), ('--game-options', '--on', 'all_stars'))


class AutopickerTests(unittest.TestCase):
    def test_key_is_a_game_option(self):
        from GameOptions import game_options
        self.assertIn(gui_grid.AUTOPICKER_KEY, game_options.BY_KEY)

    def test_button_status_and_command(self):
        self.assertEqual(gui_grid.autopicker_button({'game_options': []}), (gui_grid.AUTOPICKER_ENABLE, True))
        self.assertEqual(gui_grid.autopicker_button({'game_options': ['autopicker']}),
                         (gui_grid.AUTOPICKER_DISABLE, False))
        self.assertIsNone(gui_grid.autopicker_status({})[1])
        self.assertFalse(gui_grid.autopicker_status({'game_options': ['cpu_vs_cpu']})[1])
        self.assertTrue(gui_grid.autopicker_status({'game_options': ['autopicker']})[1])
        self.assertEqual(gui_grid.autopicker_command(True), ('--game-options', '--on', 'autopicker'))
        self.assertEqual(gui_grid.autopicker_command(False), ('--game-options', '--off', 'autopicker'))


class PlayerHeapTests(unittest.TestCase):
    def test_levels_mirror_the_game_options(self):
        from GameOptions import game_options
        # The combo offers a selection of the game options' levels, in their order.
        known = [game_options.player_heap_key(kb) for kb in game_options.PLAYER_HEAP_LEVELS_KB] \
            + [game_options.big_heap_key(kb) for kb in game_options.BIG_HEAP_LEVELS_KB]
        self.assertEqual(gui_grid.player_heap_keys(), [key for key in known if key in gui_grid.player_heap_keys()])
        for key, _label, kb, big in gui_grid.PLAYER_HEAP_LEVELS:
            make_key = game_options.big_heap_key if big else game_options.player_heap_key
            self.assertEqual(key, make_key(kb))
        self.assertEqual(gui_grid.PLAYER_HEAP_SAFE_KB, game_options.PLAYER_HEAP_SAFE_KB)
        self.assertEqual(gui_grid.PLAYER_HEAP_STOCK, game_options.PLAYER_HEAP_STOCK)

    def test_level_and_status(self):
        self.assertIsNone(gui_grid.player_heap_level(None))
        self.assertEqual(gui_grid.player_heap_level({'game_options': ['cpu_vs_cpu']}), '')
        self.assertEqual(gui_grid.player_heap_level({'game_options': ['player_heap_32']}), 'player_heap_32')
        self.assertTrue(gui_grid.player_heap_is_big('player_heap_big_1024'))
        self.assertFalse(gui_grid.player_heap_is_big('player_heap_128'))
        self.assertEqual(gui_grid.player_heap_status(None), ('Player memory: unknown', None))
        self.assertEqual(gui_grid.player_heap_status({'game_options': []})[1], False)
        self.assertEqual(gui_grid.player_heap_status({'game_options': ['player_heap_64']}),
                         ('Player memory: +64 KB (935,936 bytes per player)', True))
        self.assertIn('128 MB', gui_grid.player_heap_status({'game_options': ['player_heap_big_4096']})[0])

    def test_choices_and_commands(self):
        normal, big = gui_grid.player_heap_choices(), gui_grid.player_heap_choices(True)
        self.assertEqual(normal[0], gui_grid.PLAYER_HEAP_STOCK_LABEL)
        self.assertEqual(len(normal), 1 + sum(not is_big for *_rest, is_big in gui_grid.PLAYER_HEAP_LEVELS))
        self.assertEqual(big[:len(normal)], normal)
        self.assertEqual(len(big), 1 + len(gui_grid.PLAYER_HEAP_LEVELS))
        self.assertEqual(gui_grid.player_heap_choice('player_heap_128'), '+128 KB (978 KB)')
        self.assertIn('experimental', gui_grid.player_heap_choice('player_heap_200'))
        self.assertIn('experimental', gui_grid.player_heap_choice('player_heap_big_5120'))
        self.assertEqual(gui_grid.player_heap_choice(''), normal[0])
        self.assertEqual(gui_grid.player_heap_choice(None), normal[0])
        for key in gui_grid.player_heap_keys():
            self.assertEqual(gui_grid.player_heap_command(gui_grid.player_heap_choice(key)),
                             ('--game-options', '--on', key))
        self.assertEqual(gui_grid.player_heap_command(normal[0]),
                         ('--game-options', '--off', *gui_grid.player_heap_keys()))
        self.assertIsNone(gui_grid.player_heap_command('nonsense'))
        self.assertTrue(gui_grid.chain_writes([gui_grid.player_heap_command(normal[1])]))


class SummaryDialogTests(unittest.TestCase):
    def setUp(self):
        self.s = state([0x06, 0x66], [0x0D])

    def text(self, dialog, kind=None):
        return '\n'.join(t for t, k in dialog.lines if kind is None or k == kind)

    def test_all_edits_with_sizes_and_verdict(self):
        plan = batch(patch_section(low=None, warnings=['koopa has no Low partner']), clear_section(),
                     skipped=[clear_section('0x06', nothing=True)], notes=['0x0D: the pending patch is replaced'])
        dialog = gui_grid.summary_dialog(self.s, plan, 0, BUILD_OUTPUT)
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.title, 'Patch Game: write 2 pending edits?')
        text = self.text(dialog)
        self.assertIn('C66 (0x66): put a model in', text)
        self.assertIn('114968608_koopa.gpl.sluggie (High, 0.44 MB)', text)
        self.assertIn('C0D (0x0D): clear', text)
        self.assertIn('C06 (0x06): nothing to clear', text)
        self.assertIn('- 0x0D: the pending patch is replaced', text)
        self.assertIn('- one roster rebuild', text)
        self.assertEqual(text.count('Warning: koopa has no Low partner'), 1)   # warnings once, at the end
        self.assertIn('Patch Game writes them now', self.text(dialog, gui_grid.OK))

    def test_gecko_warning_before_the_verdict(self):
        dialog = gui_grid.summary_dialog(self.s, batch(clear_section()), 0, BUILD_OUTPUT)
        self.assertTrue(gui_grid.dialog_skippable(dialog))
        self.assertIs(gui_grid.with_gecko_warning(dialog, False), dialog)
        self.assertFalse(dialog.gecko_break)
        gui_grid.with_gecko_warning(dialog, True)
        self.assertTrue(dialog.gecko_break)
        self.assertEqual(dialog.lines[-2], (gui_grid.gecko_compat.WARNING, gui_grid.ERROR))
        self.assertIn('Patch Game writes them now', dialog.lines[-1][0])
        self.assertFalse(gui_grid.dialog_skippable(dialog))   # shown even with "Skip confirmation popups"

    def test_gecko_warning_skips_refusals(self):
        dialog = gui_grid.with_gecko_warning(gui_grid.summary_dialog(self.s, None, 1, ''), True)
        self.assertFalse(dialog.gecko_break)
        self.assertNotIn(gui_grid.gecko_compat.WARNING, self.text(dialog))

    def test_accept_gecko_commands(self):
        self.assertEqual(gui_grid.apply_command('e.json'), ('--apply-slots', 'e.json'))
        self.assertEqual(gui_grid.apply_command('e.json', True), ('--apply-slots', 'e.json', '--accept-gecko-break'))
        self.assertEqual(gui_grid.load_command('p', accept_gecko=True), ('--load-roster', 'p', '--accept-gecko-break'))
        self.assertEqual(gui_grid.switch_command('c', accept_gecko=True),
                         ('--roster', '--config', 'c', '--accept-gecko-break'))

    def test_refused_edit_gives_close_only(self):
        plan = batch(refused=[{'edit': {'op': 'patch', 'id': '0x66', 'file': HP}, 'target': '0x66',
                               'error': '0x66 is not a slot of this roster'}])
        dialog = gui_grid.summary_dialog(self.s, plan, 1, '')
        self.assertFalse(dialog.can_apply)
        self.assertEqual(dialog.title, 'Patch Game: refused')
        self.assertIn('Refused: C66 (0x66): 0x66 is not a slot', self.text(dialog, gui_grid.ERROR))

    def test_failed_build_check(self):
        output = '[2026-10-05 12:00:00] [Error] [hammerspace.main] Slot build check failed | x\n'
        dialog = gui_grid.summary_dialog(self.s, batch(patch_section()), 1, output)
        self.assertFalse(dialog.can_apply)
        self.assertIn('Build check failed: Slot build check failed | x', self.text(dialog, gui_grid.ERROR))


class RosterPackTests(unittest.TestCase):
    """The load dialog, the save question with pending edits, and the "changed since" marker."""

    def setUp(self):
        self.state = state([0x00, 0x0D], [0x66])
        fp = lambda cid: {'high': f'h{cid}', 'low': f'l{cid}', 'model': None, 'front': 'f', 'side': 's',
                          'name': {'en': f'C{cid:02X}'}, 'stats': gui_grid.hex_id(cid), 'voice': '0x00',
                          'square': '0x00'}
        for c in self.state['characters']:
            c['fingerprint'] = fp(c['id'])
        self.fingerprints = {gui_grid.hex_id(c['id']): dict(c['fingerprint']) for c in self.state['characters']}
        self.plan = {'action': 'load_pack', 'ok': True, 'rebuild': True,
                     'commands': [['--roster', '--state', 's.json'], ['--roster-state']],
                     'diff': [{'id': '0x00', 'status': 'same', 'fields': []},
                              {'id': '0x0D', 'status': 'differs', 'fields': ['name', 'high']},
                              {'id': '0x66', 'status': 'game', 'fields': []},
                              {'id': '0x70', 'status': 'pack', 'fields': []}],
                     'writes': [{'id': '0x0D', 'blocks': ['high']}], 'clears': ['0x0D'], 'kept_dirs': [],
                     'notes': ['n1'], 'warnings': [], 'refused': [],
                     'pack_fingerprints': {'0x70': {'name': {'en': 'Packed'}}},
                     'pack_meta': {'slots': 3, 'blocks': {'0x0D': {'high': 'models/0x0D_hp.bin'}}}}

    @staticmethod
    def text(dialog, kind=None):
        return '\n'.join(t for t, k in dialog.lines if kind is None or k == kind)

    def test_load_dialog(self):
        dialog = gui_grid.load_dialog(self.state, self.plan, 0, '', 'x/my.sluggiesroster')
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.title, 'Load the roster pack my.sluggiesroster?')
        body = self.text(dialog)
        self.assertIn('1 slots are the same, 1 differ, 1 only in the game, 1 only in the pack.', body)
        self.assertIn('C0D (0x0D): differs: name, High model', body)
        self.assertIn('Packed (0x70): only in the pack', body)
        self.assertNotIn('C00 (0x00)', body)                       # same slots only when the filter is off
        self.assertIn('C00 (0x00): the same', self.text(gui_grid.load_dialog(self.state, self.plan, 0, '', 'my',
                                                                              differing_only=False)))
        self.assertIn('one roster rebuild; 1 stock slot(s) back to vanilla models first; 1 slot(s) get', body)

    def test_load_dialog_refusals_and_nothing_to_load(self):
        refused = dict(self.plan, ok=False, refused=[{'id': '0x0D', 'error': 'the skeletons do not match'}])
        dialog = gui_grid.load_dialog(self.state, refused, 1, '', 'my.sluggiesroster')
        self.assertFalse(dialog.can_apply)
        self.assertIn('Refused: C0D (0x0D): the skeletons do not match', self.text(dialog, gui_grid.ERROR))
        dialog = gui_grid.load_dialog(self.state, None, 1, '[Error] [roster.pack] refused, nothing written: bad zip',
                                      'my.sluggiesroster')
        self.assertIn('Refused: bad zip', self.text(dialog))
        nothing = dict(self.plan, commands=[])
        dialog = gui_grid.load_dialog(self.state, nothing, 0, '', 'my.sluggiesroster')
        self.assertFalse(dialog.can_apply)
        self.assertIn('nothing to load', dialog.title)

    def test_save_pending_dialog(self):
        pending = gui_grid.PendingEdits()
        pending.edits = [{'op': 'rename', 'id': '0x0D', 'text': 'Little Toad'}]
        dialog = gui_grid.save_pending_dialog(self.state, pending)
        self.assertTrue(dialog.can_apply)
        self.assertIn("C0D (0x0D): rename to 'Little Toad'", self.text(dialog))

    def test_dialog_skippable(self):
        D, T = gui_grid.SlotDialog, gui_grid
        self.assertTrue(T.dialog_skippable(D('ask', [('careful', T.WARN)], True)))     # OK'd, warnings logged
        self.assertTrue(T.dialog_skippable(D('Export finished', [('done', T.OK), ('x', T.TEXT)])))
        self.assertFalse(T.dialog_skippable(D('refused', [('no', T.ERROR)])))
        self.assertFalse(T.dialog_skippable(D('aborted', [('why', T.WARN)])))

    def test_reference_marks_changed_slots(self):
        ref = gui_grid.Reference('saved (my.sluggiesroster)', self.fingerprints)
        self.assertEqual(ref.count(self.state), 0)
        self.assertIn('no slot changed since', ref.line(self.state))
        self.state['characters'][1]['fingerprint']['name'] = {'en': 'Little Toad'}
        del ref.fingerprints['0x66']
        self.assertEqual(ref.changed(self.state, 0x0D), ['name'])
        self.assertEqual(ref.changed(self.state, 0x66), ['not in the pack'])
        self.assertTrue(ref.square_changed(self.state, 0))
        self.assertIn('2 slots changed since', ref.line(self.state))

    def test_load_is_staged(self):
        """The load is one pending edit: it drops the slot edits, marks the slots it changes, Patch Game writes it."""
        pending = gui_grid.PendingEdits()
        pending.edits = [{'op': 'rename', 'id': '0x00', 'text': 'X'}]
        pending.stage_pack('x/my.sluggiesroster', self.plan)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending.edits, [])
        self.assertTrue(pending.has(0x0D))
        self.assertFalse(pending.has(0x00))                       # the same in pack and game
        self.assertTrue(pending.square_pending(self.state, 0))
        self.assertEqual(pending.summary(0x0D), ['Pending: load my.sluggiesroster: changes name, High model'])
        self.assertEqual(pending.lines(0x66), ['Pending: load my.sluggiesroster: only in the game (leaves the grid)'])
        self.assertEqual(pending.titles(), [(None, 'load the roster pack my.sluggiesroster (3 slots change)')])
        self.assertIn('Stage adds the load', self.text(gui_grid.load_dialog(self.state, self.plan, 0, '', 'my')))
        writing = gui_grid.load_dialog(self.state, self.plan, 0, '', 'my', writing=True)
        self.assertTrue(writing.title.startswith('Patch Game: load'))
        self.assertIn('Patch Game now replaces', self.text(writing))
        pending.discard(0x00)                                     # a slot the load leaves alone: kept
        self.assertEqual(len(pending), 1)
        pending.discard(0x0D)                                     # a slot it changes: the whole load goes
        self.assertEqual(len(pending), 0)

    def test_commands_and_files(self):
        self.assertEqual(gui_grid.load_command('a', dry_run=True), ('--load-roster', 'a', '--dry-run'))
        self.assertFalse(gui_grid.chain_writes([gui_grid.load_command('a', dry_run=True)]))
        self.assertTrue(gui_grid.chain_writes([gui_grid.load_command('a')]))
        self.assertFalse(gui_grid.chain_writes([gui_grid.save_command('a')]))     # saving only reads the game
        self.assertEqual(gui_grid.with_extension('r'), 'r.sluggiesroster')
        self.assertEqual(gui_grid.with_extension('r.SluggiesRoster'), 'r.SluggiesRoster')
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'p.sluggiesroster')
            with zipfile.ZipFile(path, 'w') as zf:
                zf.writestr('fingerprints.json', json.dumps(self.fingerprints))
            self.assertEqual(gui_grid.pack_fingerprints(path), self.fingerprints)
            self.assertIsNone(gui_grid.pack_fingerprints(os.path.join(tmp, 'missing')))


if __name__ == '__main__':
    unittest.main()


def value_section(op, cid='0x0D', source='0x09', effect='C09 (0x09)', nothing=False):
    return {'action': op, 'target': cid, 'rebuild': not nothing, 'commands': [], 'warnings': [], 'nothing': nothing,
            'notes': [f'nothing to change: {cid}'] if nothing else [f'{cid} {op} from {source}'],
            'effects': {} if nothing else {op: effect + (' (square voice)' if op == 'voice' else '')},
            'edit': {'op': op, 'id': cid, 'source': source}, 'checked': False}


class VoiceStatsTests(unittest.TestCase):
    """The Stats / Voice pick lists, the pending overlay and the dialogs."""

    def setUp(self):
        self.s = state([0x00], [0x09], [0x0D, 0x0E], [0x66, 0x67])
        self.s['squares'][3]['kind'] = 'new'
        self.s['squares'][3].update(voice=0x00, voice_set=None)
        for c in self.s['characters']:
            if c['id'] >= 0x66:
                c['template'] = c['stats'] = 0x00

    def test_stats_choices(self):
        stock = gui_grid.stats_choices(self.s, 0x0D)
        self.assertEqual(stock[0], ('Default: its own (C0D)', None))
        self.assertEqual([v for _l, v in stock[1:]], ['0x00', '0x09', '0x0E'])      # stock players on the grid
        new = gui_grid.stats_choices(self.s, 0x66)
        self.assertEqual(new[0], ('Default: its template C00 (0x00)', None))
        self.assertNotIn('0x00', [v for _l, v in new])
        self.assertEqual(gui_grid.choice_index(stock, '0x09'), 2)
        self.assertEqual(gui_grid.choice_index(stock, '0x77'), 0)
        self.assertEqual(gui_grid.stats_text(self.s, 0x66), 'Stats now: C00 (0x00)')

    def test_voice_choices(self):
        stock = gui_grid.voice_choices(self.s, 2)
        self.assertEqual(stock[0], ('Default: its own (C0D)', None))
        self.assertEqual([v for _l, v in stock[1:]], ['0x00', '0x09'])             # one voice per stock square
        new = gui_grid.voice_choices(self.s, 3)
        self.assertTrue(new[0][0].startswith('Default: none set'))
        self.assertEqual([v for _l, v in new[1:]], ['0x00', '0x09', '0x0D'])
        self.assertIn('every member of its wheel', gui_grid.voice_reach(self.s, 2))
        self.assertIn('without a colour wheel', gui_grid.voice_reach(self.s, 3))
        self.s['squares'][2].update(voice=0x09, voice_set=0x09)
        self.assertEqual(gui_grid.voice_text(self.s, 2), 'Voice now: C09 (0x09) (set)')

    def test_pending_titles_and_lines(self):
        pending = gui_grid.PendingEdits()
        pending.accept(batch(value_section('stats'), value_section('voice', cid='0x00', source=None,
                                                                    effect='its own voice')))
        self.assertEqual(pending.summary(0x0D), ["Pending: play with 0x09's stats"])
        self.assertEqual(pending.summary(0x00), ["Pending: the square's default voice"])
        self.assertIn('  Stats: C09 (0x09)', pending.lines(0x0D))
        self.assertIn('  Voice: its own voice (square voice)', pending.lines(0x00))
        self.assertEqual(pending.value_edit(0x0D, 'stats')['source'], '0x09')
        self.assertIsNone(pending.value_edit(0x0D, 'voice'))
        self.assertTrue(pending.square_pending(self.s, 0))

    def test_dialogs(self):
        dialog = gui_grid.slot_dialog(self.s, 0x0D, False, batch(value_section('stats')), 0, '', kind='stats')
        self.assertTrue(dialog.can_apply)
        self.assertEqual(dialog.title, 'Other stats for C0D (0x0D)?')
        voice = gui_grid.slot_dialog(self.s, 0x0D, False, batch(value_section('voice')), 0, '', kind='voice')
        self.assertEqual(voice.title, 'Another voice for the square of C0D (0x0D)?')
        nothing = gui_grid.slot_dialog(self.s, 0x0D, False, batch(skipped=[value_section('voice', nothing=True)]),
                                       0, '', gui_grid.PendingEdits(), kind='voice')
        self.assertEqual((nothing.title, nothing.can_apply), ('C0D (0x0D): nothing to change', False))
        summary = gui_grid.summary_dialog(self.s, batch(value_section('stats'), value_section('voice', cid='0x00'),
                                                        skipped=[value_section('stats', cid='0x09', nothing=True)]),
                                          0, '')
        text = '\n'.join(t for t, _k in summary.lines)
        self.assertIn('C0D (0x0D): stats of C09 (0x09)', text)
        self.assertIn('C00 (0x00): square voice of C09 (0x09)', text)
        self.assertIn('C09 (0x09): nothing to change', text)

    def test_writing_flags(self):
        self.assertTrue(gui_grid.chain_writes([('--set-voice', '0x00', '0x09')]))
        self.assertTrue(gui_grid.chain_writes([('--set-stats', '0x00', '-')]))
        self.assertTrue(gui_grid.chain_writes([('--set-char-voice', '0x00', 'pack:P')]))
        self.assertTrue(gui_grid.chain_writes([('--set-species-clip', '0x00', 'v1', 'a.wav')]))

    def test_square_pack_choices_and_edits(self):
        """The square's Voice dialog lists the voice packs; a pick stages a char_voice pack edit per member."""
        choices = gui_grid.square_pack_choices(self.s, packs=[('Rosa', 0x09, 5)])
        self.assertEqual(choices, [("Pack Rosa (5 custom clips, the rest C09's voice), as every member's own voice",
                                    (gui_grid.SQUARE_PACK, 'Rosa'))])
        index = next(i for i, sq in enumerate(self.s['squares']) if len(sq['members']) > 1)
        members = self.s['squares'][index]['members']
        edits = gui_grid.square_pack_edits(self.s, index, 'Rosa')
        self.assertEqual(edits, [{'op': gui_grid.CHAR_VOICE, 'id': gui_grid.hex_id(m), 'source': None, 'pack': 'Rosa'}
                                 for m in members])
        staged = gui_grid.PendingEdits().staging(edits)['edits']
        self.assertEqual(len(staged), len(members))

    def test_char_voice_choices_and_text(self):
        choices = gui_grid.char_voice_choices(self.s, 0x0E, packs=[('Rosa', 0x09, 5), ('Plain', None, 1)])
        self.assertEqual(choices[0], (gui_grid.CHAR_VOICE_DEFAULT, None, None))
        self.assertEqual([(s, p) for _l, s, p in choices[1:4]], [('0x00', None), ('0x09', None), ('0x0D', None)])
        self.assertEqual(choices[4], ("Pack Rosa (5 custom clips, the rest C09's voice)", None, 'Rosa'))
        self.assertEqual(choices[5], ("Pack Plain (1 custom clip, the rest its square's voice)", None, 'Plain'))
        paired = [(label, (s, p)) for label, s, p in choices]          # as the Voice dialog lists them
        self.assertEqual(gui_grid.choice_index(paired, (None, 'Rosa')), 4)
        self.assertEqual(gui_grid.choice_index(paired, ('0x09', None)), 2)
        self.assertEqual(gui_grid.choice_index(paired, ('0x77', None)), 0)
        c = gui_grid.characters(self.s)[0x0E]
        c.update(voice=0x0D, char_voice=None)
        self.assertEqual(gui_grid.char_voice_text(self.s, 0x0E), 'Voice: C0D (square)')
        c.update(voice=0x09, char_voice=0x09)
        self.assertEqual(gui_grid.char_voice_text(self.s, 0x0E), 'Voice: C09 (own)')
        c.update(char_clips={'v1': 'abc', 'v13': 'def'}, char_pack='Rosa')
        self.assertEqual(gui_grid.char_voice_text(self.s, 0x0E), "Voice: pack Rosa (own, 2 custom clips on C09's voice)")
        self.assertEqual(gui_grid.char_voice_current(self.s, 0x0E, None), (None, 'Rosa'))
        c.update(char_clips=None, char_pack=None)
        self.assertEqual(gui_grid.char_voice_current(self.s, 0x0E, None), ('0x09', None))
        pending = gui_grid.PendingEdits()
        pending.edits = [gui_grid.char_voice_edit(0x0E, None, 'Rosa')]
        self.assertEqual(gui_grid.char_voice_current(self.s, 0x0E, pending), (None, 'Rosa'))
        self.assertEqual(pending.char_voice_users('Rosa'), [0x0E])
        self.assertIn('Voice: C09 (own)', gui_grid.slot_details(self.s, 0x0E))

    def test_char_voice_and_species_clip_edits(self):
        self.assertEqual(gui_grid.char_voice_edit(0x0E, '0x09', None), {'op': 'char_voice', 'id': '0x0E', 'source': '0x09'})
        self.assertEqual(gui_grid.char_voice_edit(0x0E, None, 'Rosa'),
                         {'op': 'char_voice', 'id': '0x0E', 'source': None, 'pack': 'Rosa'})
        edit = gui_grid.species_clip_edit(0x0D, 'v1', '/staged/abc.rwav', origin='C:/x/cheer.wav')
        self.assertEqual(edit, {'op': 'species_clip', 'id': '0x0D', 'moment': 'v1', 'file': '/staged/abc.rwav',
                                'origin': 'C:/x/cheer.wav'})
        self.assertEqual(gui_grid._edit_title(edit), 'Pending: voice pack clip v1 from cheer.wav')
        self.assertEqual(gui_grid._edit_title(gui_grid.species_clip_edit(0x0D, 'v13', None)),
                         'Pending: voice pack clip v13 back to the stock clip')
        self.assertEqual(gui_grid._edit_title(gui_grid.char_voice_edit(0x0E, '0x09', 'Rosa')),
                         "Pending: speaks with the voice pack 'Rosa' on 0x09's voice as its own")
        self.assertEqual(gui_grid._edit_title(gui_grid.char_voice_edit(0x0E, None, None)),
                         'Pending: speaks with its square\'s voice again')
        pending = gui_grid.PendingEdits()
        pending.edits = [edit, gui_grid.species_clip_edit(0x0D, 'v13', None)]
        self.assertEqual(pending.species_clip_edit(0x0D, 'v1'), edit)
        self.assertIsNone(pending.species_clip_edit(0x0D, 'v3'))
        self.assertEqual(len(pending.species_clip_edits(0x0D)), 2)
        self.assertEqual(gui_grid.staged_clip_files(pending), {'/staged/abc.rwav'})
        pending.drop_species_clip(0x0D, 'v1')
        self.assertEqual([e['moment'] for e in pending.species_clip_edits(0x0D)], ['v13'])
        self.assertTrue(pending.has(0x0D))
        clip_section = value_section('species_clip')
        clip_section.update(effects={'species_clips': 'v1 from cheer.wav'}, edit=edit)
        sections = batch(clip_section, value_section('char_voice', cid='0x0E', effect='pack Rosa (2 custom, rest C09)'))
        pending.accept(sections)
        self.assertIn('  Voice pack: v1 from cheer.wav', pending.lines(0x0D))
        self.assertIn('  Own voice: pack Rosa (2 custom, rest C09)', pending.lines(0x0E))
        dialog = gui_grid.slot_dialog(self.s, 0x0E, False, sections, 0, '', kind='char_voice')
        self.assertEqual((dialog.title, dialog.can_apply), ('An own voice for C0E (0x0E)?', True))
        dialog = gui_grid.slot_dialog(self.s, 0x0D, False, sections, 0, '', kind='species_clip', moment='v1')
        self.assertEqual(dialog.title, 'Replace clip v1 of the voice of C0D (0x0D)?')
        self.assertTrue(dialog.can_apply)
        text = '\n'.join(t for t, _k in gui_grid.summary_dialog(self.s, sections, 0, '').lines)
        self.assertIn('C0D (0x0D): voice pack clip v1 from cheer.wav', text)
        self.assertIn('C0E (0x0E): own voice: pack Rosa (2 custom, rest C09)', text)
        with tempfile.TemporaryDirectory() as tmp:
            keep, drop = os.path.join(tmp, 'keep.rwav'), os.path.join(tmp, 'drop.rwav')
            for path in (keep, drop):
                open(path, 'wb').close()
            gui_grid.prune_staged_clips(tmp, {keep})
            self.assertEqual(os.listdir(tmp), ['keep.rwav'])


class SlotCountTests(unittest.TestCase):
    def test_slot_count_is_the_member_count(self):
        s = state([0x06, 0x66, 0x67], [0x0D])
        self.assertEqual([gui_grid.slot_count(s, i) for i in range(2)], [3, 1])


class DeployTests(unittest.TestCase):
    """Deploy patched files to game directory: the Patch Game label, the copy command and the game directory."""

    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())

    def game(self, *subdirs):
        game_dir = os.path.join(self.root, 'Sluggers')
        for sub in subdirs:
            os.makedirs(os.path.join(game_dir, sub))
        return game_dir

    def test_patch_label(self):
        self.assertEqual(gui_grid.patch_label(3, False), 'Patch Game (3)')
        self.assertEqual(gui_grid.patch_label(3, True), 'Patch Game & copy files (3)')

    def test_deploy_command_runs_as_it_is(self):
        command = gui_grid.deploy_command(r'C:\My Tools\CopyFilesToGameDir.bat', comspec='cmd.exe')
        self.assertIsInstance(command, gui_grid.ShellStep)
        self.assertEqual(command, ('cmd.exe', '/c', r'C:\My Tools\CopyFilesToGameDir.bat'))
        self.assertFalse(gui_grid.chain_writes([command]))         # the copy leaves 3_Output_Dat as it is

    def test_clean_game_dir(self):
        self.assertEqual(gui_grid.clean_game_dir('  "D:\\Games\\MSS\\"  \n'), r'D:\Games\MSS')
        self.assertEqual(gui_grid.clean_game_dir('C:\\'), 'C:\\')
        self.assertEqual(gui_grid.clean_game_dir('   '), '')
        self.assertEqual(gui_grid.clean_game_dir(None), '')

    def test_game_dir_problem(self):
        self.assertIn('no game directory', gui_grid.game_dir_problem(''))
        self.assertIn('does not exist', gui_grid.game_dir_problem(os.path.join(self.root, 'nowhere')))
        self.assertIn('DATA', gui_grid.game_dir_problem(self.game(os.path.join('DATA', 'files'))))

    def test_game_dir_ok(self):
        self.assertIsNone(gui_grid.game_dir_problem(self.game(*gui_grid.GAME_SUBDIRS)))

    def test_game_dir_file_round_trip(self):
        path = os.path.join(self.root, 'sluggiespath')
        self.assertEqual(gui_grid.read_game_dir(path), '')        # no file yet
        gui_grid.write_game_dir(path, r'D:\Games\MSS')
        self.assertEqual(gui_grid.read_game_dir(path), r'D:\Games\MSS')
        with open(path, 'w') as f:
            f.write('D:\\Games\\MSS \n')                         # the batch file's own echo leaves a blank
        self.assertEqual(gui_grid.read_game_dir(path), r'D:\Games\MSS')
