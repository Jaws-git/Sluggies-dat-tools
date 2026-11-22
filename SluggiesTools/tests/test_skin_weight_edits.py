"""Repainted skin weights on a model that keeps its layout: ``WeightDataEdited`` on the donor SK2/SKAcc
entries (donor length and order) reaches the Hammerspace builder, and the dispatcher asks for the SKN build."""

import pathlib
import sys
import unittest

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
for path in (ROOT_DIR, ROOT_DIR / 'SluggiesTools', ROOT_DIR / 'SluggiesTools' / 'Hammerspace'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import HammerspaceMain as main  # noqa: E402
from SluggiesTools import patch_dispatch  # noqa: E402


def _model(sk2=None, skacc=None):
    """One SK2 entry (2 vertices) and one SKAcc entry (2 records), plus *sk2* / *skacc* fields on them."""
    return {
        'UseCompression': False,
        'Submeshes': [],
        'SkinData': {
            'QuantizeInfo': 0,
            'SK1s': [],
            'SK2s': [dict({
                'BoneIndex1': 2, 'BoneIndex2': 3, 'VertexCnt': 2, 'GplVertexArrValue': 0,
                'BindPoseData': [0] * 24, 'WeightData': [128, 128, 200, 46],
            }, **(sk2 or {}))],
            'SKAccs': [dict({
                'BoneIndex': 4, 'VertexCnt': 2, 'GplDestArrValue': 0, 'BindPoseData': [0] * 24,
                'DestIndexData': [0, 1, 0, 1], 'WeightData': [10, 255],
            }, **(skacc or {}))],
        },
    }


def _weights(model):
    skinning = main.ParseSluggie({'SluggiesModel': model}).skinning
    return skinning.sk2s[0].weight_data, skinning.sk_accs[0].weight_data


class BuilderTests(unittest.TestCase):
    def test_donor_weights_without_an_edit(self):
        self.assertEqual(_weights(_model()), (bytes([128, 128, 200, 46]), bytes([10, 255])))

    def test_repainted_weights_replace_the_donor_arrays(self):
        model = _model(sk2={'WeightDataEdited': [64, 192, 200, 46]}, skacc={'WeightDataEdited': [0, 255]})
        self.assertEqual(_weights(model), (bytes([64, 192, 200, 46]), bytes([0, 255])))

    def test_they_apply_beside_a_skin_data_edited_that_is_not_used(self):
        """Without a geometry edit the model-level SkinDataEdited is left out; the weights still apply."""
        model = _model(sk2={'WeightDataEdited': [64, 192, 200, 46]})
        model['SkinDataEdited'] = {'SK1s': [], 'SK2s': [dict(model['SkinData']['SK2s'][0])], 'SKAccs': []}
        self.assertEqual(_weights(model)[0], bytes([64, 192, 200, 46]))

    def test_an_in_place_vertex_count_edit_keeps_the_donor_weights(self):
        model = _model(sk2={'WeightDataEdited': [64, 192], 'VertexCntEdited': 1})
        self.assertEqual(_weights(model)[0], bytes([128, 128, 200, 46]))

    def test_an_array_of_another_length_is_refused(self):
        model = _model(skacc={'WeightDataEdited': [0]})
        with self.assertRaisesRegex(ValueError, 'bone 4 does not have the length of its WeightData'):
            _weights(model)


class BindPoseTests(unittest.TestCase):
    """An in-place bind-pose edit (``BindPoseDataEdited`` on the donor entries) reaches a Hammerspace build."""

    def _bind_poses(self, model):
        skinning = main.ParseSluggie({'SluggiesModel': model}).skinning
        return skinning.sk2s[0].bind_pose_data, skinning.sk_accs[0].bind_pose_data

    def test_the_edited_bind_pose_replaces_the_donor_array(self):
        model = _model(sk2={'BindPoseDataEdited': [1] * 24}, skacc={'BindPoseDataEdited': [2] * 24})
        self.assertEqual(self._bind_poses(model), (bytes([1] * 24), bytes([2] * 24)))

    def test_an_in_place_vertex_count_edit_keeps_the_donor_bind_pose(self):
        model = _model(sk2={'BindPoseDataEdited': [1] * 12, 'VertexCntEdited': 1})
        self.assertEqual(self._bind_poses(model)[0], bytes(24))

    def test_an_array_of_another_length_is_refused(self):
        model = _model(sk2={'BindPoseDataEdited': [1] * 12})
        with self.assertRaisesRegex(ValueError, 'bone 2 does not have the length of its BindPoseData'):
            self._bind_poses(model)

    def test_the_dispatcher_asks_for_the_skn_build(self):
        model = _model(skacc={'BindPoseDataEdited': [2] * 24})
        self.assertEqual(patch_dispatch.hammerspace_section_args(model), ['--skn', 'build'])
        model = _model(sk2={'BindPoseDataEdited': [0] * 24})        # written on every entry, changed or not
        self.assertEqual(patch_dispatch.hammerspace_section_args(model), [])


class SplitEntryTests(unittest.TestCase):
    """On the topology path a bone with two donor entries takes its two edited entries in order."""

    def _model(self, edited):
        entry = {'BoneIndex': 5, 'VertexCnt': 1, 'GplVertexArrValue': 0}
        return {
            'UseCompression': False,
            'Submeshes': [{'DisplayStates': [{'PrimListDataEdited': [0]}]}],
            'SkinData': {'QuantizeInfo': 0, 'SK2s': [], 'SKAccs': [], 'SK1s': [
                dict(entry, BindPoseData=[0] * 12), dict(entry, BindPoseData=[0] * 12, GplVertexArrValue=12)]},
            'SkinDataEdited': {'SK2s': [], 'SKAccs': [], 'SK1s': [
                dict(entry, BindPoseData=[value] * 12) for value in edited]},
        }

    def _bind_poses(self, edited):
        skinning = main._parse_skinning(self._model(edited), False, set())
        return [entry.bind_pose_data for entry in skinning.sk1s]

    def test_each_donor_entry_takes_its_own_edit(self):
        self.assertEqual(self._bind_poses([1, 2]), [bytes([1] * 12), bytes([2] * 12)])

    def test_one_edit_for_both_is_used_for_both(self):
        self.assertEqual(self._bind_poses([7]), [bytes([7] * 12), bytes([7] * 12)])


class DispatcherTests(unittest.TestCase):
    def test_changed_weights_request_the_skn_build(self):
        model = _model(sk2={'WeightDataEdited': [64, 192, 200, 46]})
        self.assertEqual(patch_dispatch.hammerspace_section_args(model), ['--skn', 'build'])
        model = _model(skacc={'WeightDataEdited': [0, 255]})
        self.assertEqual(patch_dispatch.hammerspace_section_args(model), ['--skn', 'build'])

    def test_equal_weights_keep_the_clone(self):
        """An in-place export writes the field on every entry, changed or not."""
        model = _model(sk2={'WeightDataEdited': [128, 128, 200, 46]}, skacc={'WeightDataEdited': [10, 255]})
        self.assertEqual(patch_dispatch.hammerspace_section_args(model), [])

    def test_a_vertex_count_edit_is_not_a_weight_edit_here(self):
        model = _model(sk2={'WeightDataEdited': [64, 192], 'VertexCntEdited': 1})
        self.assertEqual(patch_dispatch.hammerspace_section_args(model), [])


if __name__ == '__main__':
    unittest.main()
