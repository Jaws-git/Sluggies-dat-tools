"""The Blender add-on's bpy-free scaling facts (BlenderAddonSrc/AnimScale.py):
the layer-root track ids and the mapping of scene-axis scale factors onto a
bone's own axes."""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADDON_DIR = ROOT / 'BlenderAddonSrc'
if str(ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(ADDON_DIR))

import AnimScale as S  # noqa: E402

IDENTITY = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
# Local X -> world Y, local Y -> world Z, local Z -> world X (columns).
PERMUTATION = [[0, 0, 1], [1, 0, 0], [0, 1, 0]]
# Rotated 90 deg about X: local Y -> world Z, local Z -> world -Y.
ROT_X90 = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
# 45 deg about Z: no local axis is close to one world axis.
ROT_Z45 = [[0.7071, -0.7071, 0], [0.7071, 0.7071, 0], [0, 0, 1]]


class LayerRoots(unittest.TestCase):
    def test_ids_are_the_six_game_roles(self):
        self.assertEqual(S.LAYER_ROOT_TRACK_IDS, frozenset({5, 6, 9, 10, 0x13, 0x19}))

    def test_is_layer_root_takes_strings_and_junk(self):
        self.assertTrue(S.is_layer_root(5))
        self.assertTrue(S.is_layer_root('0x13' if False else 19))
        self.assertFalse(S.is_layer_root(4))
        self.assertFalse(S.is_layer_root(0xFFFF))
        self.assertFalse(S.is_layer_root(None))
        self.assertFalse(S.is_layer_root('x'))

    def test_layer_roots_sorted_by_id_and_skips_missing_roles(self):
        bones = {'bone_3': 4, 'bone_5': 5, 'bone_17': 6, 'bone_28': 0x13, 'bone_64': 0x19,
                 'bone_51': 9, 'bone_6': 0xFFFF, 'bone_91': 0xC0}
        self.assertEqual(list(S.layer_roots(bones).items()),
                         [('bone_5', 5), ('bone_17', 6), ('bone_51', 9), ('bone_28', 0x13), ('bone_64', 0x19)])
        self.assertEqual(S.layer_roots({'a': 0xFFFF}), {})


class LocalFactors(unittest.TestCase):
    def test_uniform_factors_ignore_the_orientation(self):
        for axes in (IDENTITY, PERMUTATION, ROT_Z45):
            self.assertEqual(S.local_factors(axes, (1.5, 1.5, 1.5)), [1.5, 1.5, 1.5])

    def test_identity_keeps_the_order(self):
        self.assertEqual(S.local_factors(IDENTITY, (1.1, 1.2, 1.3)), [1.1, 1.2, 1.3])

    def test_permutation_follows_the_axes(self):
        # local X points along world Y -> takes sy, and so on.
        self.assertEqual(S.local_factors(PERMUTATION, (1.1, 1.2, 1.3)), [1.2, 1.3, 1.1])
        self.assertEqual(S.nearest_axes(PERMUTATION), [1, 2, 0])

    def test_sign_does_not_matter(self):
        self.assertEqual(S.local_factors(ROT_X90, (1.1, 1.2, 1.3)), [1.1, 1.3, 1.2])

    def test_alignment(self):
        self.assertAlmostEqual(S.alignment(IDENTITY), 1.0)
        self.assertAlmostEqual(S.alignment(ROT_X90), 1.0)
        self.assertAlmostEqual(S.alignment(ROT_Z45), 0.7071)
        self.assertLess(S.alignment(ROT_Z45), S.MIN_ALIGNMENT)
        self.assertGreater(S.alignment(ROT_X90), S.MIN_ALIGNMENT)

    def test_is_uniform(self):
        self.assertTrue(S.is_uniform((2.0, 2.0, 2.0)))
        self.assertFalse(S.is_uniform((2.0, 2.0, 2.1)))


if __name__ == '__main__':
    unittest.main()
