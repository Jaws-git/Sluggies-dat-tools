import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
HAMMERSPACE_DIR = ROOT / 'SluggiesTools' / 'Hammerspace'
BLENDER_ADDON_DIR = ROOT / 'BlenderAddonSrc'
for import_path in (HAMMERSPACE_DIR, BLENDER_ADDON_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from ModelFormat import compute_mem_clear_range, conservative_flush_indices
from SkinWeights import quantize_skin_weights


class ModelFormatTests(unittest.TestCase):
    def test_mem_clear_is_position_relative_accumulation_only_span(self):
        self.assertEqual(
            compute_mem_clear_range({0, 24}, {24, 48, 72}, 24),
            (48, 64),
        )

    def test_conservative_flush_is_deterministic(self):
        self.assertEqual(
            conservative_flush_indices({0, 24, 48}, 24, 48, 32, 4),
            [0, 1, 2, 3],
        )

    def test_weight_target_256_emits_observed_255_plus_1_duplicate(self):
        self.assertEqual(quantize_skin_weights([(7, 1.0)], 256), [(7, 255), (7, 1)])

    def test_weight_target_255_uses_bone_id_then_stable_order_for_ties(self):
        self.assertEqual(
            quantize_skin_weights([(7, 0.5), (2, 0.5)], 255),
            [(7, 127), (2, 128)],
        )

    def test_weight_quantization_removes_zeroes_and_normalizes(self):
        result = quantize_skin_weights([(5, 0.0), (9, 0.2), (3, 0.6)], 256)
        self.assertEqual(result, [(9, 64), (3, 192)])
        self.assertEqual(sum(weight for _bone, weight in result), 256)


if __name__ == '__main__':
    unittest.main()