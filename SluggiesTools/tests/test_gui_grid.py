"""GUI character grid, Phase 1: the state loader and the pop-out navigation, without a running Dear PyGui."""

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
        self.assertIn('Blocks: HP 0.75 MB [abababab], L_ 0.10 MB [cdcdcdcd]', lines)


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


if __name__ == '__main__':
    unittest.main()
