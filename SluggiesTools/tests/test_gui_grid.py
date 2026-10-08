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
        pending.accept(batch(rename_section(), rename_section('0x66', '')))
        self.assertEqual(pending.lines(0x0D), ["Pending: rename to 'Little Toad'", '  Name: Little Toad'])
        self.assertEqual(pending.lines(0x66), ['Pending: reset the name', '  Name: its stock name'])
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
        self.assertEqual(status({'game_options': ['cpu_vs_cpu']}), ('Cpu vs Cpu: enabled (without management)', False))
        self.assertEqual(status({'game_options': ['cpu_vs_cpu', 'cpu_management']}), ('Cpu vs Cpu: enabled', True))

    def test_command_writes_and_names_known_options(self):
        from GameOptions import game_options
        command = gui_grid.cpu_vs_cpu_command()
        self.assertEqual(command[:2], ('--game-options', '--on'))
        self.assertTrue(set(command[2:]) <= set(game_options.BY_KEY))
        self.assertTrue(gui_grid.chain_writes([command]))      # the grid re-reads afterwards (status label)
        self.assertEqual(gui_grid.cpu_vs_cpu_command(False)[:2], ('--game-options', '--off'))

    def test_button_toggles_only_when_fully_enabled(self):
        button = gui_grid.cpu_vs_cpu_button
        self.assertEqual(button(None), (gui_grid.CPU_VS_CPU_ENABLE, True))
        self.assertEqual(button({'game_options': ['cpu_vs_cpu']}), (gui_grid.CPU_VS_CPU_ENABLE, True))
        self.assertEqual(button({'game_options': ['cpu_vs_cpu', 'cpu_management']}),
                         (gui_grid.CPU_VS_CPU_DISABLE, False))


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
