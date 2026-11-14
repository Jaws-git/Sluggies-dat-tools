"""The GUI main loop survives a failing result handler, and reports it once."""
import pathlib
import sys
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import gui  # noqa: E402


class DrainQueueGuardTests(unittest.TestCase):
    def setUp(self):
        self.app = gui.SluggiesGui(['start'], 'root')
        self.app.toast = mock.Mock()
        patcher = mock.patch.object(gui.slogger, 'error')
        self.slogger_error = patcher.start()
        self.addCleanup(patcher.stop)

    def _boom(self):
        raise KeyError('missing')

    def test_failing_handler_does_not_stop_later_items(self):
        done = []
        self.app.output_queue.put(('call', self._boom))
        self.app.output_queue.put(('call', done.append, 'second'))
        self.app._drain_queue()
        self.assertEqual(done, ['second'])
        self.app.toast.error.assert_called_once()
        self.assertIn('KeyError', self.app.toast.error.call_args[0][0])
        self.slogger_error.assert_called_once()
        self.assertTrue(any('KeyError' in line for line in self.app.log_lines))

    def test_same_error_every_frame_is_reported_once(self):
        for _ in range(3):
            self.app._guarded('A tick failed', self._boom)
        self.app.toast.error.assert_called_once()


if __name__ == '__main__':
    unittest.main()
