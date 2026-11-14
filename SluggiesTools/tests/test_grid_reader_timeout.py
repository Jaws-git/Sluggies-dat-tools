"""The character grid's --roster-state read is stopped after a timeout and when
the window closes, instead of blocking every later refresh."""
import os
import pathlib
import queue
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import gui  # noqa: E402
import gui_character_grid  # noqa: E402
import gui_grid  # noqa: E402

# A reader that starts a child of its own and hangs, as start.py --roster-state does.
_HANG = ('import subprocess, sys, time; '
         'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]); time.sleep(60)')


class GridReaderTimeoutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = SimpleNamespace(
            command_prefix=[sys.executable, '-c', _HANG], root_dir=tmp.name, output_queue=queue.Queue(),
            attach_job=gui.SluggiesGui.attach_job, end_process_tree=gui.SluggiesGui.end_process_tree)
        self.tab = object.__new__(gui_character_grid.CharacterGridTab)
        self.tab.app = self.app
        self.tab.loader = gui_grid.StateLoader(os.path.join(tmp.name, 'state.json'))
        self.tab.reader = None
        self.tab._show_status = lambda: None

    def _result(self):
        kind, code, output = self.app.output_queue.get(timeout=20)
        self.assertEqual(kind, 'grid_state')
        return code, output

    def test_a_hanging_read_is_stopped_after_the_timeout(self):
        with mock.patch.object(gui_character_grid, 'READ_TIMEOUT', 1):
            started = time.monotonic()
            self.tab.request_read()
            code, output = self._result()
        self.assertEqual(code, 1)
        self.assertIn('did not finish within 1 s', gui_grid.error_message(output))
        self.assertLess(time.monotonic() - started, 15)
        self.assertIsNone(self.tab.reader)

    def test_stop_reader_ends_a_running_read(self):
        self.tab.request_read()
        deadline = time.monotonic() + 10
        while self.tab.reader is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(self.tab.reader)
        self.tab.stop_reader()
        code, _output = self._result()
        self.assertNotEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
