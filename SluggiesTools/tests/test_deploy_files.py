"""deploy_files.py: the copy of the patched game files into the unpacked game (start.py --deploy-game-files)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import deploy_files  # noqa: E402
import gui_grid  # noqa: E402


class DeployFilesTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.out = os.path.join(self.root, '3_Output_Dat')
        self.game = os.path.join(self.root, 'Sluggers (US)')
        for sub in gui_grid.GAME_SUBDIRS:
            os.makedirs(os.path.join(self.game, sub))
        os.makedirs(self.out)
        self.enterContext(mock.patch.object(deploy_files.slogger, 'configure'))

    def put(self, base, relative, data):
        path = os.path.join(base, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)

    def read(self, relative):
        with open(os.path.join(self.game, relative), 'rb') as f:
            return f.read()

    def everything(self):
        self.put(self.out, 'dt_na.dat', b'DAT')
        self.put(self.out, 'main.dol', b'DOL')
        self.put(self.out, 'fst.bin', b'FST')
        self.put(self.out, os.path.join('sound_NA', 'MY2.brsar'), b'BRSAR')
        self.put(self.out, os.path.join('sound_NA', 'stream', 'a.brstm'), b'A')
        self.put(self.out, os.path.join('sound_NA', 'stream', 'b.brstm'), b'B')

    def run_main(self, *extra):
        return deploy_files.main(['--output-dir', self.out, *extra])

    def test_every_file_goes_to_its_place(self):
        self.everything()
        self.put(self.out, 'SluggiesTools.log', b'log')                              # not a game file
        self.put(self.out, os.path.join('sound_NA', 'stream', 'c.brstm.tmp'), b'T')   # nor this
        self.put(self.game, os.path.join('DATA', 'sys', 'main.dol'), b'OLD')
        self.assertEqual(self.run_main('--game-dir', self.game), 0)
        self.assertEqual(self.read(os.path.join('DATA', 'files', 'dt_na.dat')), b'DAT')
        self.assertEqual(self.read(os.path.join('DATA', 'sys', 'main.dol')), b'DOL')
        self.assertEqual(self.read(os.path.join('DATA', 'sys', 'fst.bin')), b'FST')
        self.assertEqual(self.read(os.path.join('DATA', 'files', 'sound_NA', 'MY2.brsar')), b'BRSAR')
        stream = os.path.join(self.game, 'DATA', 'files', 'sound_NA', 'stream')
        self.assertEqual(sorted(os.listdir(stream)), ['a.brstm', 'b.brstm'])
        leftovers = [name for _d, _s, names in os.walk(self.game) for name in names
                     if name.endswith(deploy_files.TEMP_SUFFIX) or name == 'SluggiesTools.log']
        self.assertEqual(leftovers, [])

    def test_missing_files_are_skipped(self):
        self.put(self.out, 'main.dol', b'DOL')
        self.assertEqual(self.run_main('--game-dir', self.game), 0)
        self.assertEqual(self.read(os.path.join('DATA', 'sys', 'main.dol')), b'DOL')
        self.assertFalse(os.path.exists(os.path.join(self.game, 'DATA', 'files', 'dt_na.dat')))

    def test_game_dir_comes_from_the_stored_file(self):
        self.put(self.out, 'main.dol', b'DOL')
        gui_grid.write_game_dir(os.path.join(self.out, 'sluggiespath'), self.game)
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.read(os.path.join('DATA', 'sys', 'main.dol')), b'DOL')

    def test_refusals_copy_nothing(self):
        self.everything()
        self.assertEqual(self.run_main(), 1)                                              # no stored directory
        self.assertEqual(self.run_main('--game-dir', os.path.join(self.root, 'nowhere')), 1)
        self.assertEqual(self.run_main('--game-dir', self.out), 1)                        # no DATA folders
        self.assertEqual(os.listdir(os.path.join(self.game, 'DATA', 'files')), [])

    def test_nothing_to_copy_fails(self):
        self.assertEqual(self.run_main('--game-dir', self.game), 1)

    def test_failed_copy_keeps_the_old_file_and_fails_the_run(self):
        self.put(self.out, 'dt_na.dat', b'NEW')
        self.put(self.out, 'main.dol', b'DOL')
        self.put(self.game, os.path.join('DATA', 'files', 'dt_na.dat'), b'OLD')
        real_copy = deploy_files.shutil.copyfile

        def copy(source, target):
            if os.path.basename(source) == 'dt_na.dat':
                with open(target, 'wb') as f:
                    f.write(b'NE')                                # half written, then the disk is full
                raise OSError('disk full')
            return real_copy(source, target)

        with mock.patch.object(deploy_files.shutil, 'copyfile', side_effect=copy):
            self.assertEqual(self.run_main('--game-dir', self.game), 1)
        self.assertEqual(self.read(os.path.join('DATA', 'files', 'dt_na.dat')), b'OLD')
        self.assertEqual(self.read(os.path.join('DATA', 'sys', 'main.dol')), b'DOL')      # the others still go
        self.assertEqual(os.listdir(os.path.join(self.game, 'DATA', 'files')), ['dt_na.dat'])


if __name__ == '__main__':
    unittest.main()
