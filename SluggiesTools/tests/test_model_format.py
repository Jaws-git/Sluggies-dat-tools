import os
import pathlib
import struct
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
HAMMERSPACE_DIR = ROOT / 'SluggiesTools' / 'Hammerspace'
BLENDER_ADDON_DIR = ROOT / 'BlenderAddonSrc'
INPLACE_PATCHER_DIR = ROOT / 'SluggiesTools' / 'InplacePatcher'
for import_path in (HAMMERSPACE_DIR, BLENDER_ADDON_DIR, INPLACE_PATCHER_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from ModelFormat import compute_mem_clear_range, conservative_flush_indices
from SkinWeights import quantize_skin_weights
import root_scale as _root_scale
import patch_skn_inplace as _skn


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


def _hierarchy(bone_id, parent_id, srt_offset=None, scale=(1.0, 1.0, 1.0)):
    """Build a minimal BoneHierarchy entry for root-scale tests."""
    return {
        'BoneId': bone_id,
        'ParentBoneId': parent_id,
        'SRTOffset': srt_offset,
        'Scale': list(scale),
    }


class RootBoneScaleTests(unittest.TestCase):
    def test_main_root_is_parentless_bone_with_most_descendants_not_id_zero(self):
        # Mirrors the real layout: Bone 0 and high-id bones are parentless
        # leaves; Bone 1 is the parentless root of the whole visible model.
        bones = [
            _hierarchy(0, None),
            _hierarchy(1, None, srt_offset='0x4000'),
            _hierarchy(2, 1, srt_offset='0x4034'),
            _hierarchy(3, 2, srt_offset='0x4068'),
            _hierarchy(86, None),
            _hierarchy(87, None),
        ]
        self.assertEqual(_root_scale.main_root_bone(bones)['BoneId'], 1)

    def test_main_root_handles_two_top_level_roots(self):
        # Toad-like: two parentless roots, but only one owns the model subtree.
        bones = [
            _hierarchy(0, None),
            _hierarchy(1, None, srt_offset='0x5000'),
            _hierarchy(2, 1),
            _hierarchy(3, 2),
        ]
        self.assertEqual(_root_scale.main_root_bone(bones)['BoneId'], 1)

    def test_main_root_none_when_no_bones(self):
        self.assertIsNone(_root_scale.main_root_bone([]))

    def test_pack_srt_scale_is_three_big_endian_floats(self):
        packed = _root_scale.pack_srt_scale([2.0, 1.5, 1.0])
        self.assertEqual(len(packed), 12)
        self.assertEqual(
            struct.unpack('>3f', packed),
            (2.0, 1.5, 1.0),
        )

    def test_patch_mode_writes_edited_scale_at_srt_offset_plus_4(self):
        bones = [
            _hierarchy(0, None),
            _hierarchy(1, None, srt_offset='0x4000'),
            _hierarchy(2, 1),
        ]
        model = {'RootBoneScaleEdited': [2.0, 2.0, 2.0]}
        patch = _root_scale.root_scale_patch(model, bones, restore=False, abort=self._abort)
        self.assertIsNotNone(patch)
        bone_id, offset, raw = patch
        self.assertEqual(bone_id, 1)
        self.assertEqual(offset, 0x4000 + 0x04)
        self.assertEqual(struct.unpack('>3f', raw), (2.0, 2.0, 2.0))

    def test_no_edit_returns_no_patch(self):
        bones = [_hierarchy(1, None, srt_offset='0x4000')]
        self.assertIsNone(
            _root_scale.root_scale_patch({}, bones, restore=False, abort=self._abort)
        )

    def test_unpatch_restores_original_root_scale(self):
        bones = [
            _hierarchy(0, None),
            _hierarchy(1, None, srt_offset='0x4000', scale=(1.0, 1.0, 1.0)),
            _hierarchy(2, 1),
        ]
        # Even with an edit present, unpatch writes the original scale back.
        model = {'RootBoneScaleEdited': [3.0, 3.0, 3.0]}
        patch = _root_scale.root_scale_patch(model, bones, restore=True, abort=self._abort)
        bone_id, offset, raw = patch
        self.assertEqual(bone_id, 1)
        self.assertEqual(offset, 0x4000 + 0x04)
        self.assertEqual(struct.unpack('>3f', raw), (1.0, 1.0, 1.0))

    def test_missing_srt_offset_aborts(self):
        bones = [
            _hierarchy(0, None),
            _hierarchy(1, None),  # no SRTOffset (pre-Step-1 export)
            _hierarchy(2, 1),
        ]
        model = {'RootBoneScaleEdited': [2.0, 2.0, 2.0]}
        with self.assertRaises(SystemExit):
            _root_scale.root_scale_patch(model, bones, restore=False, abort=self._abort)

    def test_non_finite_scale_aborts(self):
        bones = [_hierarchy(1, None, srt_offset='0x4000')]
        with self.assertRaises(SystemExit):
            _root_scale.root_scale_patch(
                {'RootBoneScaleEdited': [float('inf'), 1.0, 1.0]},
                bones, restore=False, abort=self._abort,
            )

    def test_out_of_range_scale_aborts(self):
        bones = [_hierarchy(1, None, srt_offset='0x4000')]
        with self.assertRaises(SystemExit):
            _root_scale.root_scale_patch(
                {'RootBoneScaleEdited': [1e9, 1.0, 1.0]},
                bones, restore=False, abort=self._abort,
            )

    def _abort(self, message):
        raise SystemExit(1)


class HammerspaceRootBoneScaleTests(unittest.TestCase):
    def _abort(self, message):
        raise SystemExit(1)

    def test_hammerspace_patch_offset_is_act_section_relative(self):
        # SRTOffset is an absolute INPUT-file offset (ACT.absolute +
        # orientationPTR). The hammerspace patcher must convert it to an
        # ACT-section-relative offset (orientationPTR + 0x04) so the write
        # stays correct after the block is relocated.
        bones = [
            _hierarchy(0, None),
            _hierarchy(1, None, srt_offset='0x4050'),
            _hierarchy(2, 1),
        ]
        model = {'RootBoneScaleEdited': [2.0, 1.5, 1.0]}
        patch = _root_scale.hammerspace_root_scale_patch(
            model, bones, act_section_absolute=0x4000, abort=self._abort,
        )
        self.assertIsNotNone(patch)
        bone_id, offset, raw = patch
        self.assertEqual(bone_id, 1)
        # 0x4050 - 0x4000 + 0x04 = 0x54 (orientationPTR + 0x04)
        self.assertEqual(offset, 0x54)
        self.assertEqual(struct.unpack('>3f', raw), (2.0, 1.5, 1.0))

    def test_hammerspace_patch_no_edit_returns_none(self):
        bones = [_hierarchy(1, None, srt_offset='0x4050')]
        self.assertIsNone(
            _root_scale.hammerspace_root_scale_patch(
                {}, bones, act_section_absolute=0x4000, abort=self._abort,
            )
        )

    def test_hammerspace_patch_missing_srt_offset_aborts(self):
        bones = [
            _hierarchy(0, None),
            _hierarchy(1, None),  # no SRTOffset (pre-Step-1 export)
            _hierarchy(2, 1),
        ]
        model = {'RootBoneScaleEdited': [2.0, 2.0, 2.0]}
        with self.assertRaises(SystemExit):
            _root_scale.hammerspace_root_scale_patch(
                model, bones, act_section_absolute=0x4000, abort=self._abort,
            )

    def test_hammerspace_patch_non_finite_scale_aborts(self):
        bones = [_hierarchy(1, None, srt_offset='0x4050')]
        with self.assertRaises(SystemExit):
            _root_scale.hammerspace_root_scale_patch(
                {'RootBoneScaleEdited': [float('inf'), 1.0, 1.0]},
                bones, act_section_absolute=0x4000, abort=self._abort,
            )

    def test_hammerspace_patch_out_of_range_scale_aborts(self):
        bones = [_hierarchy(1, None, srt_offset='0x4050')]
        with self.assertRaises(SystemExit):
            _root_scale.hammerspace_root_scale_patch(
                {'RootBoneScaleEdited': [1e9, 1.0, 1.0]},
                bones, act_section_absolute=0x4000, abort=self._abort,
            )

    def test_hammerspace_patch_srt_before_act_start_aborts(self):
        # A SRTOffset that lands before the ACT section start means the
        # .sluggie metadata does not match this model's ACT layout.
        bones = [_hierarchy(1, None, srt_offset='0x3FFF')]
        model = {'RootBoneScaleEdited': [2.0, 2.0, 2.0]}
        with self.assertRaises(SystemExit):
            _root_scale.hammerspace_root_scale_patch(
                model, bones, act_section_absolute=0x4000, abort=self._abort,
            )


class BindPoseScaleTests(unittest.TestCase):
    def test_resolve_returns_factors(self):
        self.assertEqual(
            _root_scale.resolve_bind_pose_scale({'RootBoneScaleEdited': [1.0, 2.0, 0.5]}),
            (1.0, 2.0, 0.5),
        )

    def test_resolve_none_when_absent(self):
        self.assertIsNone(_root_scale.resolve_bind_pose_scale({}))

    def test_resolve_none_when_malformed(self):
        self.assertIsNone(
            _root_scale.resolve_bind_pose_scale({'RootBoneScaleEdited': [1.0, 2.0]})
        )

    def test_scale_float_format_scales_positions_only(self):
        # quantize_info 0x40 -> 32-bit float, divisor 1.0
        data = struct.pack('>6f', 1.0, 2.0, 3.0, 0.1, 0.2, 0.3)
        out = _root_scale.scale_bind_pose_data(
            data, 0x40, vertex_cnt=1, vertex_offset=0, factors=(2.0, 3.0, 4.0)
        )
        x, y, z, nx, ny, nz = struct.unpack('>6f', out)
        self.assertEqual((x, y, z), (2.0, 6.0, 12.0))
        # normals are copied through unchanged (byte-for-byte)
        self.assertEqual(out[12:], data[12:])

    def test_scale_int16_format(self):
        # quantize_info 0x30 -> 16-bit int, divisor 1.0
        data = struct.pack('>6h', 100, 200, 300, 10, 20, 30)
        out = _root_scale.scale_bind_pose_data(
            data, 0x30, vertex_cnt=1, vertex_offset=0, factors=(2.0, 1.0, 0.5)
        )
        x, y, z, nx, ny, nz = struct.unpack('>6h', out)
        self.assertEqual((x, y, z), (200, 200, 150))
        self.assertEqual((nx, ny, nz), (10, 20, 30))

    def test_scale_noop_returns_same(self):
        data = struct.pack('>6f', 1.0, 2.0, 3.0, 0.1, 0.2, 0.3)
        out = _root_scale.scale_bind_pose_data(data, 0x40, 1, 0, (1.0, 1.0, 1.0))
        self.assertEqual(out, data)

    def test_scale_preserves_prefix(self):
        prefix = b'\x01\x02\x03\x04'
        data = prefix + struct.pack('>6f', 1.0, 2.0, 3.0, 0.1, 0.2, 0.3)
        out = _root_scale.scale_bind_pose_data(data, 0x40, 1, 4, (2.0, 2.0, 2.0))
        self.assertEqual(out[:4], prefix)
        x, y, z, *_ = struct.unpack('>6f', out[4:])
        self.assertEqual((x, y, z), (2.0, 4.0, 6.0))

    def test_scale_multiple_vertices(self):
        data = struct.pack('>12f', 1, 2, 3, 0, 0, 0, 10, 20, 30, 0, 0, 0)
        out = _root_scale.scale_bind_pose_data(data, 0x40, 2, 0, (2.0, 2.0, 2.0))
        vals = struct.unpack('>12f', out)
        self.assertEqual(vals[0:3], (2.0, 4.0, 6.0))
        self.assertEqual(vals[6:9], (20.0, 40.0, 60.0))


class SknScaledBindPoseTests(unittest.TestCase):
    """Cover the per-entry helper in patch_skn_inplace and the write path."""

    def _skin(self, quant=0x40):
        return {'QuantizeInfo': quant, 'SK1s': [], 'SK2s': [], 'SKAccs': []}

    def test_returns_original_when_no_factors(self):
        entry = {'BindPoseData': [1, 2, 3, 4], 'VertexCnt': 1, 'VertexOffset': 0}
        self.assertEqual(
            _skn._scaled_bind_pose(entry, self._skin(), None), bytes([1, 2, 3, 4])
        )

    def test_returns_original_when_unit_factors(self):
        entry = {'BindPoseData': [1, 2, 3, 4], 'VertexCnt': 1, 'VertexOffset': 0}
        self.assertEqual(
            _skn._scaled_bind_pose(entry, self._skin(), (1.0, 1.0, 1.0)),
            bytes([1, 2, 3, 4]),
        )

    def test_scales_original_payload(self):
        data = struct.pack('>6f', 1.0, 2.0, 3.0, 0.1, 0.2, 0.3)
        entry = {'BindPoseData': list(data), 'VertexCnt': 1, 'VertexOffset': 0}
        out = _skn._scaled_bind_pose(entry, self._skin(0x40), (2.0, 1.0, 1.0))
        x, y, z, *_ = struct.unpack('>6f', out)
        self.assertEqual((x, y, z), (2.0, 2.0, 3.0))

    def test_prefers_edited_payload_with_edited_count(self):
        edited = struct.pack('>6f', 1.0, 2.0, 3.0, 0.1, 0.2, 0.3)
        entry = {
            'BindPoseData': list(struct.pack('>6f', 9, 9, 9, 0, 0, 0)),
            'BindPoseDataEdited': list(edited),
            'VertexCnt': 1,
            'VertexCntEdited': 1,
            'VertexOffset': 0,
        }
        out = _skn._scaled_bind_pose(entry, self._skin(0x40), (2.0, 1.0, 1.0))
        x, y, z, *_ = struct.unpack('>6f', out)
        self.assertEqual((x, y, z), (2.0, 2.0, 3.0))


    def _run_patch(self, skin_data, factors):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b'\x00' * 0x100)
            path = tf.name
        orig_output = _skn.OUTPUT_DAT
        _skn.OUTPUT_DAT = path
        try:
            wrote = _skn.patchSKNInPlace(skin_data, factors)
        finally:
            _skn.OUTPUT_DAT = orig_output
        with open(path, 'rb') as f:
            f.seek(0x40)
            written = f.read(24)
        os.unlink(path)
        return wrote, written

    def test_patch_writes_scaled_original_when_root_scale_only(self):
        # A root-scale-only edit (no BindPoseDataEdited) must still land the
        # scaled original bind-pose in the file.
        data = struct.pack('>6f', 1.0, 2.0, 3.0, 0.1, 0.2, 0.3)
        skin_data = {
            'SK1s': [{
                'BindPoseData': list(data),
                'VertexCnt': 1,
                'VertexOffset': 0,
                'VertexArrAbsolutePtr': '0x40',
            }],
            'SK2s': [],
            'SKAccs': [],
            'QuantizeInfo': 0x40,
        }
        wrote, written = self._run_patch(skin_data, (2.0, 1.0, 1.0))
        self.assertTrue(wrote)
        x, y, z, *_ = struct.unpack('>6f', written)
        self.assertEqual((x, y, z), (2.0, 2.0, 3.0))

    def test_patch_writes_scaled_edited_payload(self):
        edited = struct.pack('>6f', 1.0, 2.0, 3.0, 0.1, 0.2, 0.3)
        skin_data = {
            'SK1s': [{
                'BindPoseData': list(struct.pack('>6f', 9, 9, 9, 0, 0, 0)),
                'BindPoseDataEdited': list(edited),
                'VertexCnt': 1,
                'VertexCntEdited': 1,
                'VertexOffset': 0,
                'VertexArrAbsolutePtr': '0x40',
            }],
            'SK2s': [],
            'SKAccs': [],
            'QuantizeInfo': 0x40,
        }
        wrote, written = self._run_patch(skin_data, (2.0, 1.0, 1.0))
        self.assertTrue(wrote)
        x, y, z, *_ = struct.unpack('>6f', written)
        self.assertEqual((x, y, z), (2.0, 2.0, 3.0))

    def test_patch_no_factors_no_write(self):
        # Without factors and without edited data, nothing is written.
        data = struct.pack('>6f', 1.0, 2.0, 3.0, 0.1, 0.2, 0.3)
        skin_data = {
            'SK1s': [{
                'BindPoseData': list(data),
                'VertexCnt': 1,
                'VertexOffset': 0,
                'VertexArrAbsolutePtr': '0x40',
            }],
            'SK2s': [],
            'SKAccs': [],
            'QuantizeInfo': 0x40,
        }
        wrote, written = self._run_patch(skin_data, None)
        self.assertFalse(wrote)
        self.assertEqual(written, b'\x00' * 24)

if __name__ == '__main__':
    unittest.main()