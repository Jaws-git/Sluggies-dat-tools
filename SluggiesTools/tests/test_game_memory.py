"""Player memory checks (``game_memory``): the fit rule, the heap a game option sets, and which changes warn."""

import os
import tempfile
import unittest

from SluggiesTools import game_memory as gm
from SluggiesTools import maintenance
from SluggiesTools.Dol import dolfile
from SluggiesTools.GameOptions import game_options as go
from SluggiesTools.tests.test_game_options import synthetic_dol

STOCK = gm.PlayerHeap(go.PLAYER_HEAP_STOCK)


def mario(high: int) -> gm.CharacterNeed:
    return gm.CharacterNeed(18, high, 77_984, 26_944)


class FitRuleTests(unittest.TestCase):
    def test_matches_the_measured_edge(self):
        # Dolphin, 2026-10-10: High 761,472 played 8/8 on the stock heap, 777,856 crashed 7/7
        self.assertIsNone(gm.problem_text(mario(761_472), STOCK, 'Mario'))
        self.assertIn('crashes', gm.problem_text(mario(777_856), STOCK, 'Mario'))
        plus48 = gm.PlayerHeap(go.PLAYER_HEAP_STOCK + 48 * 1024, level='player_heap_48')
        self.assertIsNone(gm.problem_text(mario(810_624), plus48, 'Mario'))      # played, 4 KB left

    def test_big_level_checks_the_fallback_first(self):
        big = gm.PlayerHeap(go.PLAYER_HEAP_STOCK + 48 * 1024, go.PLAYER_HEAP_STOCK + 4096 * 1024,
                            'player_heap_big_4096')
        text = gm.problem_text(mario(843_392), big, 'Mario')
        self.assertIn('only with the override', text)
        self.assertIn('crashes', gm.problem_text(mario(6_000_000), big, 'Mario'))
        self.assertIsNone(gm.problem_text(mario(433_760), big, 'Mario'))


class PlayerHeapTests(unittest.TestCase):
    def test_reads_the_game_option(self):
        image = dolfile.DolImage(synthetic_dol())
        self.assertEqual(gm.player_heap(image), STOCK)
        go.apply(image, ['player_heap_64'])
        self.assertEqual(gm.player_heap(image).size, go.PLAYER_HEAP_STOCK + 64 * 1024)
        go.apply(image, ['player_heap_big_1024'])
        heap = gm.player_heap(image)
        self.assertEqual((heap.size, heap.big_size, heap.level),
                         (go.PLAYER_HEAP_STOCK + go.PLAYER_HEAP_SAFE_KB * 1024, go.PLAYER_HEAP_STOCK + 1024 * 1024,
                          'player_heap_big_1024'))


class ChangedProblemsTests(unittest.TestCase):
    def test_only_changed_characters_warn(self):
        big, ok = mario(800_000), gm.CharacterNeed(19, 400_000, 70_000, 30_000)
        before = (STOCK, {18: big, 19: ok})
        self.assertEqual(gm.changed_problems(before, before, {}), [])     # an old problem is not repeated
        grown = gm.CharacterNeed(19, 900_000, 70_000, 30_000)
        lines = gm.changed_problems(before, (STOCK, {18: big, 19: grown}), {19: '19 Luigi'})
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('19 Luigi'))

    def test_a_smaller_heap_rechecks_everyone(self):
        plus128 = gm.PlayerHeap(go.PLAYER_HEAP_STOCK + 128 * 1024, level='player_heap_128')
        chars = {18: mario(800_000)}
        self.assertEqual(gm.changed_problems((plus128, chars), (plus128, chars), {}), [])
        self.assertEqual(len(gm.changed_problems((plus128, chars), (STOCK, chars), {})), 1)

    def test_no_after_snapshot_warns_nothing(self):
        self.assertEqual(gm.changed_problems(None, None, {}), [])


class MaintenanceTests(unittest.TestCase):
    def test_memory_checks_skip_a_folder_without_an_output_game(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(maintenance.player_memory(root), [])
            self.assertEqual(maintenance.stadium_memory(root), [])
            self.assertTrue(os.path.isdir(root))

    def test_checks_are_registered(self):
        titles = [title for title, _check in maintenance.CHECKS]
        self.assertIn('Player memory', titles)
        self.assertIn('Stadium memory', titles)


if __name__ == '__main__':
    unittest.main()
