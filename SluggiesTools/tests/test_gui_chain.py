"""GUI plumbing without a window: the command chain's state under two threads, grid reads that are skipped
while a chain writes or when the game files did not change, the console update, and ``render_turns``."""
import os
import pathlib
import queue
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import gui  # noqa: E402
import gui_audio  # noqa: E402
import gui_character_grid  # noqa: E402
import gui_grid  # noqa: E402


class ChainStateTests(unittest.TestCase):
    """``run_chain`` runs on Dear PyGui's callback thread, ``_finish_process`` on the main thread."""

    def setUp(self):
        self.app = gui.SluggiesGui(['start'], 'root')
        self.app.grid_tab = mock.Mock()
        self.app._close_prompt = lambda: None
        self.app._set_busy = lambda busy: None
        self.app._start_next = self._start_next
        self.started = []

    def _start_next(self):
        self.app.current = self.app.pending.pop(0)
        self.app.process = object()
        self.started.append(self.app.current)

    def test_busy_covers_the_gap_between_two_steps(self):
        seen = []
        self.assertTrue(self.app.run_chain([('--export',), ('--roster',)]))
        self.app._start_next = lambda: seen.append((self.app.process, self.app.busy, self.app.writing()))
        self.app._finish_process(0)
        self.assertEqual(seen, [(None, True, True)])
        self.assertFalse(self.app.run_chain([('--export',)]))            # still the first chain's turn

    def test_a_chain_started_by_the_callback_keeps_its_own_callback_and_output(self):
        results = []

        def first_done(code, output):
            results.append(('first', code, output))
            self.assertTrue(self.app.run_chain([('--b',)], on_done=lambda c, o: results.append(('second', c, o))))

        self.app.run_chain([('--a',)], on_done=first_done)
        self.app._handle_queue_item('output of a\n')
        self.app._finish_process(0)
        self.app._handle_queue_item('output of b\n')
        self.app._finish_process(3)
        self.assertEqual(results, [('first', 0, 'output of a\n'), ('second', 3, 'output of b\n')])
        self.assertEqual(self.started, [('--a',), ('--b',)])

    def test_a_click_while_the_chain_finishes_waits_for_it(self):
        """The defect: ``process`` was already None while the old chain's callback and output were still in
        place; a chain started in that window got the old chain's result and lost its own callback."""
        results, clicked, thread = [], threading.Event(), []

        def click():
            clicked.set()
            self.app.run_chain([('--b',)], on_done=lambda c, o: results.append(('second', c, o)))

        def set_busy(busy):                          # runs inside _finish_process, the chain half finished
            if not busy and not thread:
                thread.append(threading.Thread(target=click))
                thread[0].start()
                clicked.wait(5)
                thread[0].join(0.2)                  # give the click every chance to get in between
                self.assertTrue(thread[0].is_alive(), 'run_chain did not wait for the finishing chain')

        self.app._set_busy = set_busy
        self.app.run_chain([('--a',)], on_done=lambda c, o: results.append(('first', c, o)))
        self.app._handle_queue_item('output of a\n')
        self.app._finish_process(0)
        thread[0].join(5)
        self.assertEqual(results, [('first', 0, 'output of a\n')])
        self.assertEqual(self.started, [('--a',), ('--b',)])
        self.app._handle_queue_item('output of b\n')
        self.app._finish_process(0)
        self.assertEqual(results[1], ('second', 0, 'output of b\n'))

    def test_a_writing_chain_is_followed_by_a_grid_read(self):
        self.app.run_chain([('--export', '--glb')])
        self.app._finish_process(0)
        self.app.grid_tab.request_read.assert_called_once_with()
        self.app.run_chain([('--roster', '--dry-run')])
        self.app._finish_process(0)
        self.app.grid_tab.request_read.assert_called_once_with()         # a dry run changes nothing


class ConsoleFlushTests(unittest.TestCase):
    def setUp(self):
        self.app = gui.SluggiesGui(['start'], 'root')
        patcher = mock.patch.object(gui, 'dpg')
        self.dpg = patcher.start()
        self.addCleanup(patcher.stop)
        self.dpg.get_text_size.return_value = (8, 16)
        self.dpg.get_item_configuration.return_value = {'show': False}

    def test_a_hidden_console_is_not_rebuilt_and_catches_up_when_shown(self):
        self.app.log_line('one')
        self.app._flush_log()
        self.dpg.set_value.assert_not_called()
        self.app.show_console(True)
        self.app._flush_log()
        self.assertIn(mock.call('log_text', 'one'), self.dpg.set_value.call_args_list)

    def test_updates_are_spaced(self):
        self.app.console_visible = True
        with mock.patch.object(gui.time, 'monotonic', side_effect=[100.0, 100.05, 100.2]):
            self.app.log_line('one')
            self.app._flush_log()
            self.app.log_line('two')
            self.app._flush_log()                    # 50 ms later: waits
            self.assertEqual(self.dpg.set_value.call_count, 1)
            self.app._flush_log()                    # nothing new was logged, the waiting line still shows
        self.assertEqual(self.dpg.set_value.call_args_list[-1], mock.call('log_text', 'one\ntwo'))


class GridReadSkipTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        os.makedirs(os.path.join(self.root, '3_Output_Dat'))
        self.dol = os.path.join(self.root, '3_Output_Dat', 'main.dol')
        with open(self.dol, 'wb') as f:
            f.write(b'dol')
        self.writing = False
        self.app = SimpleNamespace(command_prefix=[sys.executable, '-c', 'pass'], root_dir=self.root,
                                   output_queue=queue.Queue(), writing=lambda: self.writing,
                                   attach_job=lambda process: None, end_process_tree=lambda process, job: None)
        self.tab = object.__new__(gui_character_grid.CharacterGridTab)
        self.tab.app = self.app
        self.tab.loader = gui_grid.StateLoader(os.path.join(self.root, 'state.json'))
        self.tab.reader = None
        self.tab._show_status = lambda: None
        with open(self.tab.loader.state_path, 'w') as f:
            f.write('{"version": 1, "kind": "stock", "shape": [1, 1], "cells": [0], "squares": [], '
                    '"characters": []}')

    def _read(self, **kwargs) -> bool:
        """Whether ``request_read`` started a reader; its result is taken so the next call can start one."""
        self.tab.request_read(**kwargs)
        if self.tab.loader.status != self.tab.loader.RUNNING:
            return False
        kind, code, output = self.app.output_queue.get(timeout=30)
        self.assertEqual((kind, code), ('grid_state', 0), output)
        self.tab.loader.finish(code, output)
        return True

    def test_no_read_while_a_chain_writes_the_game_files(self):
        self.writing = True
        self.assertFalse(self._read())
        self.assertFalse(self._read(if_changed=True))
        self.writing = False
        self.assertTrue(self._read())

    def test_a_tab_switch_reads_only_when_the_game_files_changed(self):
        self.assertTrue(self._read(if_changed=True))                     # nothing loaded yet
        self.assertFalse(self._read(if_changed=True))                    # the same files
        self.assertTrue(self._read())                                    # Refresh and a chain's end always read
        with open(self.dol, 'wb') as f:
            f.write(b'a new dol')
        self.assertTrue(self._read(if_changed=True))
        self.assertFalse(self._read(if_changed=True))
        self.tab.loader.finish(1, '[Error] x')                           # a failed read is never "current"
        self.assertTrue(self._read(if_changed=True))

    def test_the_signature_is_the_one_from_before_the_read(self):
        loader = self.tab.loader
        before = gui_grid.state_signature(self.root)
        loader.start(before)
        with open(self.dol, 'wb') as f:
            f.write(b'changed while the reader ran')
        self.assertFalse(loader.finish(0))
        self.assertEqual(loader.status, loader.DONE)
        self.assertTrue(loader.current(before))
        self.assertFalse(loader.current(gui_grid.state_signature(self.root)))


class RenderTurnsTests(unittest.TestCase):
    def test_overlapping_workers_put_the_interval_back(self):
        """Second in, last out: it used to save the fast interval as "the original" and leave it set."""
        original = sys.getswitchinterval()
        first, second = gui_audio.render_turns(), gui_audio.render_turns()
        first.__enter__()
        second.__enter__()
        self.assertEqual(sys.getswitchinterval(), gui_audio._FAST_SWITCH)
        first.__exit__(None, None, None)
        self.assertEqual(sys.getswitchinterval(), gui_audio._FAST_SWITCH)
        second.__exit__(None, None, None)
        self.assertEqual(sys.getswitchinterval(), original)
        with self.assertRaises(RuntimeError):
            with gui_audio.render_turns():
                raise RuntimeError
        self.assertEqual(sys.getswitchinterval(), original)


if __name__ == '__main__':
    unittest.main()
