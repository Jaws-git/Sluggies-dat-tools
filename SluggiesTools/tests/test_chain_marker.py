"""The "last chain did not finish" marker: written while a chain's writing steps run, kept when one fails or
the run is cut off, removed when the chain finishes."""
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
for path in (ROOT_DIR, ROOT_DIR / 'SluggiesTools'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import start  # noqa: E402
from SluggiesTools import chain_marker  # noqa: E402

CHECK = ['--patch', 'a.sluggie', '--validate-only']
WRITE_1 = ['--roster', '--state', 's.json']
WRITE_2 = ['--write-slot-blocks', '0x66', 'h.bin', 'l.bin']


class ChainMarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(start, 'ROOT_DIR', self.tmp))
        self.seen = []                       # (step arguments, the marker on disk while the step ran, child env)

    def run_chain(self, commands, fail_at=None, **kwargs):
        def run(command, cwd=None, env=None):
            args = command[len(start.self_command()):]
            self.seen.append((args, chain_marker.read(self.tmp), (env or {}).get(chain_marker.CHILD_ENV)))
            if fail_at == args:
                return SimpleNamespace(returncode=1)
            if fail_at == 'kill' and args == WRITE_2:
                raise KeyboardInterrupt
            return SimpleNamespace(returncode=0)

        with mock.patch.object(start.subprocess, 'run', side_effect=run):
            return start.run_chain_commands(commands, 'Slot chain', **kwargs)

    def test_a_finished_chain_leaves_no_marker(self):
        self.assertTrue(self.run_chain([CHECK, WRITE_1, WRITE_2]))
        self.assertIsNone(chain_marker.read(self.tmp))
        self.assertIsNone(self.seen[0][1])                           # nothing on disk during the check
        self.assertEqual([m['done'] for _a, m, _e in self.seen[1:]], [1, 2])   # in place before each write
        self.assertEqual({env for _a, _m, env in self.seen}, {'1'})

    def test_a_failed_write_step_keeps_the_marker(self):
        self.assertFalse(self.run_chain([WRITE_1, WRITE_2], fail_at=WRITE_2, start=3, total=4))
        marker = chain_marker.read(self.tmp)
        self.assertEqual((marker['label'], marker['done'], marker['failed'], marker['total']),
                         ('Slot chain', 1, 4, 4))
        text = chain_marker.message(marker)
        self.assertIn('step 4 of 4 failed', text)
        self.assertIn('1 writing step had been applied', text)
        self.assertIn('--write-slot-blocks 0x66', text)

    def test_a_failed_check_before_any_write_leaves_none(self):
        self.assertFalse(self.run_chain([CHECK, WRITE_1], fail_at=CHECK))
        self.assertIsNone(chain_marker.read(self.tmp))

    def test_a_cut_off_chain_keeps_the_marker(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_chain([WRITE_1, WRITE_2], fail_at='kill')
        marker = chain_marker.read(self.tmp)
        self.assertEqual((marker['done'], marker['failed']), (1, None))
        self.assertIn('cut off', chain_marker.message(marker))

    def test_only_the_same_kind_of_chain_clears_it(self):
        self.run_chain([WRITE_1], fail_at=WRITE_1)
        with mock.patch.object(start.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
            self.assertTrue(start.run_chain_commands([WRITE_1], 'Pack load'))
            self.assertIsNotNone(chain_marker.read(self.tmp))
            self.assertTrue(start.run_chain_commands([CHECK], 'Slot chain'))       # checks alone change nothing
            self.assertIsNotNone(chain_marker.read(self.tmp))
            self.assertTrue(start.run_chain_commands([WRITE_1], 'Slot chain'))
        self.assertIsNone(chain_marker.read(self.tmp))

    def test_later_commands_warn_but_a_chains_own_steps_do_not(self):
        from SluggiesTools.Hammerspace import DatAllocation
        chain_marker.write(self.tmp, chain_marker.begin('Slot chain', [WRITE_1]))

        def warnings(env):
            with (mock.patch.object(sys, 'argv', ['start.py', '--deploy-game-files']),
                  mock.patch.dict(os.environ, env),
                  mock.patch.object(DatAllocation, 'defragBackupPending', return_value=False),
                  mock.patch.object(start, 'run_deploy_game_files', return_value=True),
                  mock.patch.object(start.slogger, 'warning') as warning):
                os.environ.pop(chain_marker.CHILD_ENV, None) if not env else None
                self.assertEqual(start.main(), 0)
            return [call.args[0] for call in warning.call_args_list if 'did not finish' in call.args[0]]

        self.assertEqual(len(warnings({})), 1)
        self.assertEqual(warnings({chain_marker.CHILD_ENV: '1'}), [])

    def test_an_unreadable_marker_still_counts(self):
        os.makedirs(os.path.dirname(chain_marker.path(self.tmp)))
        with open(chain_marker.path(self.tmp), 'w') as f:
            f.write('{ not json')
        self.assertIn('did not finish', chain_marker.message(chain_marker.read(self.tmp)))
        self.assertTrue(chain_marker.clear(self.tmp))
        self.assertFalse(chain_marker.clear(self.tmp))


if __name__ == '__main__':
    unittest.main()
