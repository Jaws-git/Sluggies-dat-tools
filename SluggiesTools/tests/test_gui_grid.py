"""GUI character grid: the state loader, the pop-out navigation, the slot actions' confirm dialog (Phase 4f)
and the staged edits with Patch Game's summary (Phase 4g), without a running Dear PyGui."""

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
        self.assertEqual((self.nav.level, self.nav.levels(), self.nav.depth), (SLOT, [SQUARE, SLOT], 2))
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
        self.assertEqual((self.nav.level, self.nav.levels(), self.nav.depth), (SLOT, [SLOT], 1))
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
        self.assertIn('Names: FR Violet', lines)

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
    """Phase 2: portrait crops and their fallback marks (``Roster/state_icons`` results in the state)."""

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
    """The confirm dialog of "Select .sluggie..." / "Clear slot" after the staging check (decision 6 warnings,
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
    """The pending list (decision 14): staging files, accept / replace / discard, the overlay text."""

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
    """GUI character grid Phase 5: the rename dialog's live check, the pending overlay and the confirm dialog."""

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
        pending.accept(batch(rename_section(), rename_section('0x66', '')))
        self.assertEqual(pending.lines(0x0D), ["Pending: rename to 'Little Toad'", '  Name: Little Toad'])
        self.assertEqual(pending.lines(0x66), ['Pending: reset the name', '  Name: its stock name'])
        self.assertIsNotNone(pending.rename_edit(0x0D))
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


if __name__ == '__main__':
    unittest.main()
