"""One writer of the output sound archive at a time (``Sound/archive_lock``), what the GUI does while one
runs, and the Sound FX status text."""
import pathlib
import sys
import threading
import time
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import gui  # noqa: E402
import gui_effects  # noqa: E402
from Sound import archive_lock, effects, volumes  # noqa: E402


class ArchiveLockTests(unittest.TestCase):
    def test_writers_take_turns_and_busy_covers_the_waiting_one(self):
        order, inside, release = [], threading.Event(), threading.Event()

        def first():
            with archive_lock.writing():
                order.append('first in')
                inside.set()
                release.wait(5)
                order.append('first out')

        def second():
            with archive_lock.writing():
                order.append('second in')

        a = threading.Thread(target=first)
        a.start()
        inside.wait(5)
        b = threading.Thread(target=second)
        b.start()
        time.sleep(0.1)
        self.assertTrue(archive_lock.busy())
        self.assertEqual(order, ['first in'])                # the second waits
        release.set()
        a.join(5)
        b.join(5)
        self.assertEqual(order, ['first in', 'first out', 'second in'])
        self.assertFalse(archive_lock.busy())

    def test_the_same_thread_may_nest(self):
        with archive_lock.writing():
            with archive_lock.writing():                     # set_volume makes the archive through sync_output
                self.assertTrue(archive_lock.busy())
        self.assertFalse(archive_lock.busy())

    def test_temp_names_differ_per_thread(self):
        names = [archive_lock.temp_path('MY2.brsar')]
        thread = threading.Thread(target=lambda: names.append(archive_lock.temp_path('MY2.brsar')))
        thread.start()
        thread.join()
        self.assertNotEqual(*names)

    def test_both_writers_go_through_the_lock(self):
        for module, name, args in ((effects, '_sync_output', ('root',)), (volumes, '_set_volume', ('root', 'x', 1))):
            seen = []
            with mock.patch.object(module, name, side_effect=lambda *a: seen.append(archive_lock.busy())):
                getattr(module, name[1:])(*args)
            self.assertEqual(seen, [True], name)


class GuiRefusesWhileASoundIsWrittenTests(unittest.TestCase):
    def setUp(self):
        self.app = gui.SluggiesGui(['start'], 'root')
        self.app.toast = mock.Mock()
        self.app._start_next = lambda: setattr(self.app, 'process', object())

    def test_a_writing_chain_waits_a_reading_one_does_not(self):
        with archive_lock.writing():
            self.assertFalse(self.app.run_chain([('--apply-slots', 'edits.json')]))
            self.app.toast.warn.assert_called_once()
            self.assertEqual(self.app.pending, [])
            self.assertTrue(self.app.run_chain([('--roster', '--dry-run')]))
        self.app.process, self.app.pending = None, []      # that chain is over
        self.assertTrue(self.app.run_chain([('--apply-slots', 'edits.json')]))


class EffectOutcomeTests(unittest.TestCase):
    DONE = 'se_hit replaced by hit.wav'

    def outcome(self, **kw):
        values = {'notes': [], 'error': None, 'problems': [], 'changed': 1, 'stored': True}
        values.update(kw)
        return gui_effects.effect_outcome('se_hit', self.DONE, **values)

    def test_written(self):
        text, kind = self.outcome(notes=['resampled'])
        self.assertEqual(kind, gui_effects.OUTCOME_OK)
        self.assertIn('MY2.brsar written. (resampled)', text)

    def test_the_sound_was_skipped_by_the_archive_write(self):
        text, kind = self.outcome(problems=['se_hit: the output archive does not hold it under ID 7', 'se_x: y'])
        self.assertEqual(kind, gui_effects.OUTCOME_WARN)
        self.assertIn('was not written', text)
        self.assertIn('does not hold it under ID 7', text)
        self.assertNotIn('se_x', text)

    def test_another_sounds_problem_does_not_hide_this_ones_success(self):
        self.assertEqual(self.outcome(problems=['se_x: y'])[1], gui_effects.OUTCOME_OK)

    def test_nothing_changed(self):
        text, kind = self.outcome(changed=0)
        self.assertEqual(kind, gui_effects.OUTCOME_WARN)
        self.assertIn('nothing was written', text)

    def test_stored_in_the_library_but_the_archive_write_failed(self):
        text, kind = self.outcome(error='MY2.brsar is in use', changed=0)
        self.assertEqual(kind, gui_effects.OUTCOME_WARN)
        self.assertIn('kept in the library', text)
        self.assertNotIn('Refused', text)

    def test_refused_before_anything_was_stored(self):
        text, kind = self.outcome(error='not a WAV file', stored=False)
        self.assertEqual((text, kind), ('Refused: not a WAV file', gui_effects.OUTCOME_ERROR))


if __name__ == '__main__':
    unittest.main()
