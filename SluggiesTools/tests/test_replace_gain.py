"""The Replace gain: ``clips.apply_gain`` and the ``gain_db`` of ``clips.prepare`` / ``effects.encode`` /
``brstm_write.encode_wav``."""

import pathlib
import sys
import unittest

import numpy as np

TESTS_DIR = pathlib.Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
for _p in (TOOLS_DIR, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from Sound import clips    # noqa: E402


class ApplyGainTests(unittest.TestCase):
    def test_zero_is_untouched(self):
        x = np.array([0.1, -0.2])
        out, notes = clips.apply_gain(x, 0.0)
        self.assertIs(out, x)
        self.assertEqual(notes, [])

    def test_scales(self):
        out, notes = clips.apply_gain(np.array([0.25, -0.5]), 20.0)
        np.testing.assert_allclose(out, [2.5, -5.0])
        self.assertEqual(notes, ['gain +20.0 dB'])
        out, _ = clips.apply_gain(np.array([0.5]), -6.0206)
        np.testing.assert_allclose(out, [0.25], rtol=1e-4)

    def test_range(self):
        for bad in (24.5, -30.0):
            with self.assertRaises(clips.ClipError):
                clips.apply_gain(np.zeros(2), bad)


class PrepareGainTests(unittest.TestCase):
    def test_prepare(self):
        x = np.full(320, 0.1)
        base, _ = clips.prepare(clips.RATE, x)
        louder, notes = clips.prepare(clips.RATE, x, 6.0)
        self.assertAlmostEqual(louder[0] / base[0], 10 ** (6 / 20), places=2)
        self.assertIn('gain +6.0 dB', notes)
        clipped, notes = clips.prepare(clips.RATE, x, 24.0)
        self.assertEqual(int(clipped.max()), 32767)
        self.assertIn('peaks above full scale were clipped', notes)


if __name__ == '__main__':
    unittest.main()
