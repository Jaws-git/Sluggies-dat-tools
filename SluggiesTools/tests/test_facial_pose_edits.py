"""Facial position-pose edits (``facial_pose_edits.py``): the arrays both patchers write, and the Hammerspace
build splicing them into the ptr7 section of a submesh that is not rebuilt (2026-10-15: before, a slot patch
refused them and a Hammerspace-mode file dropped them silently)."""

import base64
import struct
import unittest
from unittest import mock

import test_rigid_rebuild as trr  # noqa: E402  (sets up the import paths)
import test_skinned_rebuild  # noqa: E402
import facial_pose_edits  # noqa: E402
import HammerspaceMain as main  # noqa: E402
import synthetic_donor  # noqa: E402

RIGID = trr.RIGID
POSES = trr.FACIAL_POSES


def _facial_data(section: bytes) -> dict:
    """``FacialPoseData`` of *section* with the absolute offsets export.py adds (section at 0x1000)."""
    data = test_skinned_rebuild._facial_data_from_section(section)
    for obj in data['Objects']:
        for attribute in [obj['Position'], obj['Normal']]:
            if attribute:
                table = int(attribute['RecordOffset'], 16)
                offsets = struct.unpack_from(f'>{obj["PoseCount"]}I', section, table + 0x0C)
                attribute['PoseOffsets'] = [hex(o) for o in offsets]
                attribute['PoseAbsoluteOffsets'] = [hex(0x1000 + o) for o in offsets]
    return data


def _pose_edit(pose_index: int, values) -> dict:
    return {'Objects': [{'ObjectIndex': 0, 'PositionPoseEdits': [
        {'PoseIndex': pose_index, 'PoseData': trr._b64(struct.pack('>6h', *values))}]}]}


class PoseArrayTests(unittest.TestCase):
    def setUp(self):
        self.section = trr._rigid_facial_section()
        self.model = {'FacialPoseData': _facial_data(self.section), 'Submeshes': [{}] * (RIGID + 1)}

    def test_unedited_model_changes_nothing(self):
        arrays = facial_pose_edits.position_pose_arrays(self.model)
        self.assertEqual(len(arrays), POSES)
        self.assertTrue(all(a.data == a.original for a in arrays))
        self.assertEqual(facial_pose_edits.changed_position_poses(self.model), {})

    def test_sparse_edit_replaces_its_pose(self):
        self.model['FacialPoseDataEdited'] = _pose_edit(1, range(6))
        self.assertEqual(facial_pose_edits.changed_position_poses(self.model), {(0, 1): struct.pack('>6h', *range(6))})
        arrays = facial_pose_edits.position_pose_arrays(self.model, restore=True)
        self.assertTrue(all(a.data == a.original for a in arrays))                 # unpatch writes the originals

    def test_edit_of_another_length_is_refused(self):
        self.model['FacialPoseDataEdited'] = {'Objects': [{'ObjectIndex': 0, 'PositionPoseEdits': [
            {'PoseIndex': 1, 'PoseData': trr._b64(bytes(4))}]}]}
        with self.assertRaisesRegex(ValueError, 'edited length'):
            facial_pose_edits.position_pose_arrays(self.model)

    def test_pose_zero_follows_a_moved_vertex_buffer(self):
        original = struct.pack('>9h', *range(9))                                    # 3 vertices, x y z int16
        edited = struct.pack('>9h', 0, 1, 2, 3 + 100, 4, 5 - 7, 6, 7, 8)          # vertex 1 moved
        self.model['Submeshes'] = [{}] * RIGID + [{'VertexBuffer': {
            'VertexBufferData': trr._b64(original), 'VertexBufferDataEdited': trr._b64(edited),
            'VertexBufferCompCount': 3, 'VertexBufferQuantizeInfo': 0x13}}]
        changes = facial_pose_edits.changed_position_poses(self.model)
        self.assertEqual(list(changes), [(0, 0)])
        self.assertEqual(struct.unpack('>6h', changes[(0, 0)]), (0, 0, 0, 100, 0, -7))   # vertices 0-1 of pose 0


class HammerspaceBuildTests(unittest.TestCase):
    """A build that rebuilds nothing splices the edited pose arrays into ptr7; the section keeps its length."""

    def setUp(self):
        self.env = self.enterContext(synthetic_donor.donor_environment(trr._with_normals))
        self.data = self.env.reload()
        self.model = self.data['SluggiesModel']
        self.section = trr._rigid_facial_section()
        self.model['FacialPoseData'] = _facial_data(self.section)
        self.model['TrailingSections'] = [
            {'HeaderFieldOffset': '0x18', 'OriginalPtr': '0x1000', 'Length': len(self.section),
             'Data': trr._b64(self.section)},
        ]

    def _build(self):
        donor_header = bytearray(main.CloneHEADER(self.env.model_offset))
        struct.pack_into('>I', donor_header, 0x18, 0x1000)
        with mock.patch.object(main, 'CloneHEADER', return_value=bytes(donor_header)):
            result = main.BuildModelBlock(self.data, main.SectionModes(), sluggie_path=self.env.sluggie_path)
        self.assertTrue(result.validation_report['valid'], result.validation_report.get('errors'))
        ptr7 = struct.unpack_from('>I', result.block, 0x18)[0]
        return result.block[ptr7:ptr7 + len(self.section)]

    def test_unedited_section_is_kept(self):
        self.assertEqual(self._build(), self.section)

    def test_pose_edit_is_spliced_in(self):
        self.model['FacialPoseDataEdited'] = _pose_edit(2, (9, 8, 7, 6, 5, 4))
        built = self._build()
        _slots, poses = test_skinned_rebuild._parse_facial_runs(built, 0)
        self.assertEqual(struct.unpack('>6h', poses[2]), (9, 8, 7, 6, 5, 4))
        expected = bytearray(self.section)
        offset = int(self.model['FacialPoseData']['Objects'][0]['Position']['PoseOffsets'][2], 16)
        expected[offset:offset + 12] = struct.pack('>6h', 9, 8, 7, 6, 5, 4)
        self.assertEqual(built, bytes(expected))                                   # nothing else moves

    def test_inplace_file_is_no_longer_refused_by_a_slot_build(self):
        self.model['FacialPoseDataEdited'] = _pose_edit(1, range(6))
        self.assertEqual(main.PromoteInplaceEdits(self.model, main.SectionModes()), main.SectionModes())

    def test_edit_without_header_data_asks_for_a_re_export(self):
        self.model['FacialPoseDataEdited'] = _pose_edit(1, range(6))
        del self.model['FacialPoseData']['HeaderData']
        with self.assertRaisesRegex(ValueError, 're-export'):
            self._build()


if __name__ == '__main__':
    unittest.main()
