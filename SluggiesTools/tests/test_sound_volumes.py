"""Sound volumes: ``Sound/volumes`` (the volume byte, the ``sound_volumes.json`` library, the direct output write)
on the synthetic archive of ``test_sound_export``."""

import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
for _p in (TOOLS_DIR, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from Sound import brsar, volumes                   # noqa: E402
from test_sound_export import make_archive         # noqa: E402


def volume_of(data, name):
    return next(s.volume for s in brsar.Brsar(data).sounds() if s.name == name)


class WriteVolumesTests(unittest.TestCase):
    def setUp(self):
        self.data = make_archive()[0]

    def test_sets_only_the_byte(self):
        out, changed, missing = volumes.write_volumes(self.data, {'hit_a': 120, 'bgm_test': 60, 'nope': 5})
        self.assertEqual(changed, {'bgm_test': (100, 60), 'hit_a': (107, 120)})
        self.assertEqual(missing, ['nope'])
        self.assertEqual(len(out), len(self.data))
        self.assertEqual(sum(a != b for a, b in zip(out, self.data)), 2)
        self.assertEqual((volume_of(out, 'hit_a'), volume_of(out, 'bgm_test'), volume_of(out, 'seq_test')),
                         (120, 60, 64))

    def test_unchanged_returns_the_input(self):
        out, changed, _ = volumes.write_volumes(self.data, {'hit_a': 107})
        self.assertEqual(changed, {})
        self.assertEqual(out, self.data)

    def test_check_volume(self):
        self.assertEqual(volumes.check_volume(' 127 '), 127)
        self.assertEqual(volumes.check_volume(0), 0)
        for bad in ('128', '-1', 'loud', '1.5'):
            with self.assertRaises(volumes.VolumeError):
                volumes.check_volume(bad)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='sluggies_volume_test_')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.data = make_archive()[0]
        os.makedirs(os.path.join(self.root, '1_Input', 'sound_NA'))
        with open(os.path.join(self.root, '1_Input', brsar.ARCHIVE), 'wb') as handle:
            handle.write(self.data)
        self.output = os.path.join(self.root, '3_Output_Dat', brsar.ARCHIVE)

    def read_output(self):
        with open(self.output, 'rb') as handle:
            return handle.read()

    def test_set_volume_writes_output_and_library(self):
        self.assertEqual(volumes.current_volume(self.root, 'hit_a'), 107)        # from 1_Input: no output yet
        self.assertEqual(volumes.set_volume(self.root, 'hit_a', '90'), (107, 107))
        self.assertEqual(volume_of(self.read_output(), 'hit_a'), 90)
        self.assertEqual(volumes.load_library(self.root), {'hit_a': 90})
        self.assertEqual(volumes.current_volume(self.root, 'hit_a'), 90)         # from 3_Output_Dat now
        # back to the stock volume: written, and dropped from the library
        self.assertEqual(volumes.set_volume(self.root, 'hit_a', 107), (90, 107))
        self.assertEqual(volume_of(self.read_output(), 'hit_a'), 107)
        self.assertEqual(volumes.load_library(self.root), {})

    def test_game_volumes(self):
        self.assertEqual(volumes.game_volumes(self.root)['hit_a'], (107, 107))   # no output: the input's
        volumes.set_volume(self.root, 'hit_a', 80)
        found = volumes.game_volumes(self.root)
        self.assertEqual((found['hit_a'], found['bgm_test']), ((80, 107), (100, 100)))

    def test_set_volume_refuses(self):
        with self.assertRaises(volumes.VolumeError):
            volumes.set_volume(self.root, 'hit_a', 200)
        with self.assertRaises(volumes.VolumeError):
            volumes.set_volume(self.root, 'no_such_sound', 50)
        self.assertEqual(volumes.load_library(self.root), {})

    def test_apply(self):
        volumes.save_library(self.root, {'bgm_test': 117, 'gone': 3})
        out, lines = volumes.apply(self.data, self.root)
        self.assertEqual(volume_of(out, 'bgm_test'), 117)
        self.assertEqual(len(lines), 2)
        self.assertIn('gone', lines[0])
        self.assertIn('bgm_test 100->117', lines[1])
        os.remove(volumes.library_path(self.root))
        self.assertIs(volumes.apply(self.data, self.root)[0], self.data)

    def test_bad_library(self):
        os.makedirs(os.path.dirname(volumes.library_path(self.root)), exist_ok=True)
        with open(volumes.library_path(self.root), 'w', encoding='utf-8') as handle:
            json.dump({'format': 1, 'volumes': {'hit_a': 300}}, handle)
        out, lines = volumes.apply(self.data, self.root)
        self.assertIs(out, self.data)
        self.assertIn('not applied', lines[0])


if __name__ == '__main__':
    unittest.main()
