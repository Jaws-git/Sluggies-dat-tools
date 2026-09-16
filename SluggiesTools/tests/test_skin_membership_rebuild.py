import pathlib
import struct
import sys
import unittest
from unittest import mock


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import GeometryRebuild as gr
import HammerspaceMain as main


STRIDE = 12  # QuantizeInfo 9 -> comp size 2 -> 6 components * 2 bytes


def _u16_list(values):
    return list(b''.join(struct.pack('>H', value) for value in values))


def _records(slots):
    return [byte for slot in slots for byte in [slot + 1] * STRIDE]


def _membership_model(sk1s, sk2s, skaccs, *, faces_edited=None, n_verts=24):
    """Donor layout (stride 12, each entry on its own cache lines):
    SK1 bone 10   -> slots 0-7   (gplVertexArr 0x00 + 0)
    SK1 bone 11   -> slots 11-15 (gplVertexArr 0x80 + 4; slots 8-10 unused)
    SK2 (20,21)   -> slots 16-19 (gplVertexArr 0xC0 + 0)
    SKAcc bone 30 -> accumulation-only slots 22-23"""
    vertex_buffer = _records(range(n_verts))
    submesh = {
        'FacesData': [0, 0, 0, 1, 0, 2],
        'VertexBuffer': {
            'VertexBufferData': vertex_buffer,
            'VertexBufferDataEdited': list(vertex_buffer),
            'VertexBufferCompCount': 6,
            'VertexBufferQuantizeInfo': 0x39,
        },
    }
    if faces_edited is not None:
        submesh['FacesDataEdited'] = faces_edited
    return {'SluggiesModel': {
        'UseBase64': False,
        'Submeshes': [submesh],
        'SkinData': {
            'QuantizeInfo': 9,
            'SK1s': [
                {'BoneIndex': 10, 'VertexCnt': 8, 'GplVertexArrValue': 0x00,
                 'VertexOffset': 0, 'BindPoseData': _records(range(0, 8))},
                {'BoneIndex': 11, 'VertexCnt': 5, 'GplVertexArrValue': 0x80,
                 'VertexOffset': 4, 'BindPoseData': _records(range(11, 16))},
            ],
            'SK2s': [
                {'BoneIndex1': 20, 'BoneIndex2': 21, 'VertexCnt': 4,
                 'GplVertexArrValue': 0xC0, 'VertexOffset': 0,
                 'BindPoseData': _records(range(16, 20)),
                 'WeightData': [100, 156, 101, 155, 102, 154, 103, 153]},
            ],
            'SKAccs': _donor_skacc(),
            'FlushIndSize': 1,
            'FlushIndData': [0x00, 0x16],
        },
        'SkinDataEdited': {
            'MembershipEdited': True,
            'QuantizeInfo': 9,
            'SK1s': sk1s,
            'SK2s': sk2s,
            'SKAccs': skaccs,
            'FlushIndSize': 1,
        },
    }}


def _sk1(bone, slots, *, with_indices=True):
    slots = list(slots)
    entry = {'BoneIndex': bone, 'VertexCnt': len(slots), 'GplVertexArrValue': 0,
             'BindPoseData': _records(slots)}
    if with_indices:
        entry['VertexIndices'] = _u16_list(slots)
    return entry


def _sk2(bones, slots, weights, *, with_indices=True):
    slots = list(slots)
    entry = {'BoneIndex1': bones[0], 'BoneIndex2': bones[1], 'VertexCnt': len(slots),
             'GplVertexArrValue': 0, 'BindPoseData': _records(slots),
             'WeightData': weights}
    if with_indices:
        entry['VertexIndices'] = _u16_list(slots)
    return entry


def _donor_skacc():
    return [{'BoneIndex': 30, 'VertexCnt': 2, 'GplDestArrValue': 0,
             'BindPoseData': _records([22, 23]), 'WeightData': [128, 128],
             'DestIndexData': _u16_list([22, 23])}]


def _unchanged_direct_entries():
    return (
        [_sk1(10, range(0, 8)), _sk1(11, range(11, 16))],
        [_sk2((20, 21), range(16, 20), [128] * 8)],
    )


class MembershipEditDispatchTests(unittest.TestCase):
    """A membership-only edit must NOT go through the permuting topology
    rebuild: layout_skin_membership_edit lays it out without reordering
    vertices, so prim lists and facial pose data stay valid."""

    def test_membership_only_edit_is_not_routed_through_permutation(self):
        data = _membership_model(*_unchanged_direct_entries(), _donor_skacc())
        with (
            mock.patch.object(gr, '_rebuild_skinning') as rebuild_skinning,
            mock.patch.object(gr, '_rebuild_submesh') as rebuild_submesh,
        ):
            changed = gr.rebuild_edited_geometry(data)

        self.assertFalse(changed)
        rebuild_skinning.assert_not_called()
        rebuild_submesh.assert_not_called()


class MembershipLayoutTests(unittest.TestCase):
    """PLAN_ModelReplacements.md 3.4: lay out a same-count reskin in place,
    following the donor destination layout rules."""

    def _layout(self, data):
        with mock.patch.object(gr._slogger, 'info'), mock.patch.object(gr._slogger, 'warning'):
            return gr.layout_skin_membership_edit(data)

    def test_whole_group_reassignment_splits_into_donor_placed_runs(self):
        # bone 11's whole group moves to bone 10: one exporter entry, two runs
        sk1s = [_sk1(10, list(range(0, 8)) + list(range(11, 16)))]
        sk2s = [_sk2((20, 21), range(16, 20), [128] * 8)]
        data = _membership_model(sk1s, sk2s, _donor_skacc())

        self.assertTrue(self._layout(data))

        ske = data['SluggiesModel']['SkinDataEdited']
        placements = [(e['BoneIndex'], e['VertexCnt'], e['GplVertexArrValue'], e['VertexOffset'])
                      for e in ske['SK1s']]
        self.assertEqual(placements, [(10, 8, 0x00, 0), (10, 5, 0x80, 4)])
        self.assertEqual(ske['SK1s'][1]['BindPoseData'], _records(range(11, 16)))
        self.assertEqual(ske['SK1s'][1]['VertexIndices'], _u16_list(range(11, 16)))
        self.assertEqual((ske['SK2s'][0]['GplVertexArrValue'], ske['SK2s'][0]['VertexOffset']),
                         (0xC0, 0))
        self.assertTrue(ske['LayoutRebuiltByImporter'])
        # SKAcc writes unchanged -> donor flush bytes are kept verbatim
        self.assertEqual(ske['FlushIndData'], [0x00, 0x16])
        self.assertEqual(ske['FlushIndSize'], 1)

    def test_sk2_weights_follow_their_vertices_into_slot_order(self):
        sk1s, _ = _unchanged_direct_entries()
        sk2s = [_sk2((20, 21), [18, 19, 16, 17], [1, 2, 3, 4, 5, 6, 7, 8])]
        data = _membership_model(sk1s, sk2s, _donor_skacc())

        self._layout(data)

        sk2 = data['SluggiesModel']['SkinDataEdited']['SK2s'][0]
        self.assertEqual(sk2['BindPoseData'], _records(range(16, 20)))
        self.assertEqual(sk2['WeightData'], [5, 6, 7, 8, 1, 2, 3, 4])

    def test_members_are_recovered_by_value_without_vertex_indices(self):
        sk1s = [_sk1(10, list(range(0, 8)) + list(range(11, 16)), with_indices=False)]
        sk2s = [_sk2((20, 21), range(16, 20), [128] * 8, with_indices=False)]
        data = _membership_model(sk1s, sk2s, _donor_skacc())

        self._layout(data)

        ske = data['SluggiesModel']['SkinDataEdited']
        self.assertEqual([e['VertexIndices'] for e in ske['SK1s']],
                         [_u16_list(range(0, 8)), _u16_list(range(11, 16))])

    def test_partial_group_move_sharing_a_cache_line_is_rejected(self):
        # slot 3 moves from bone 10 to bone 11 -> bone 10 splits mid-line
        sk1s = [_sk1(10, [0, 1, 2, 4, 5, 6, 7]), _sk1(11, [3] + list(range(11, 16)))]
        sk2s = [_sk2((20, 21), range(16, 20), [128] * 8)]
        data = _membership_model(sk1s, sk2s, _donor_skacc())

        with self.assertRaisesRegex(ValueError, 'needs vertex reordering.*cache line'):
            self._layout(data)

    def test_accumulation_slot_on_direct_entry_line_is_rejected(self):
        # slot 20 becomes accumulation-only, but shares the SK2 run's cache line
        skaccs = _donor_skacc()
        skaccs[0].update(VertexCnt=3, BindPoseData=_records([20, 22, 23]),
                         WeightData=[255, 128, 128], DestIndexData=_u16_list([20, 22, 23]))
        data = _membership_model(*_unchanged_direct_entries(), skaccs)

        with self.assertRaisesRegex(ValueError, 'SKAcc-only clear range'):
            self._layout(data)

    def test_changed_skacc_writes_get_conservative_flush_indices(self):
        skaccs = _donor_skacc()
        skaccs[0].update(VertexCnt=3, BindPoseData=_records([21, 22, 23]),
                         WeightData=[255, 128, 128], DestIndexData=_u16_list([21, 22, 23]))
        sk1s = [_sk1(10, range(0, 8)), _sk1(11, range(11, 16))]
        sk2s = [_sk2((20, 21), range(16, 20), [128] * 8)]
        data = _membership_model(sk1s, sk2s, skaccs, n_verts=26)
        # move the accumulation tail off the SK2 run's last cache line
        for entry in (data['SluggiesModel']['SkinData']['SKAccs'][0], skaccs[0]):
            destinations = [slot + 2 for slot in struct.unpack(
                f">{entry['VertexCnt']}H", bytes(entry['DestIndexData']))]
            entry['DestIndexData'] = _u16_list(destinations)
            entry['BindPoseData'] = _records(destinations)

        self._layout(data)

        written = {slot * STRIDE for slot in (23, 24, 25)}
        expected = gr.conservative_flush_indices(
            written, STRIDE, *gr.compute_mem_clear_range(set(), written, STRIDE), 26)
        ske = data['SluggiesModel']['SkinDataEdited']
        self.assertEqual(ske['FlushIndData'], _u16_list(expected))
        self.assertEqual(ske['FlushIndSize'], len(expected))

    def test_topology_edit_combined_with_membership_edit_is_rejected(self):
        data = _membership_model(*_unchanged_direct_entries(), _donor_skacc(),
                                 faces_edited=[0, 0, 0, 2, 0, 1])
        with self.assertRaisesRegex(ValueError, 'topology edit is not supported'):
            self._layout(data)

    def test_without_membership_flag_is_a_noop(self):
        data = _membership_model(*_unchanged_direct_entries(), _donor_skacc())
        ske = data['SluggiesModel']['SkinDataEdited']
        del ske['MembershipEdited']

        self.assertFalse(self._layout(data))
        self.assertNotIn('LayoutRebuiltByImporter', ske)
        self.assertNotIn('FlushIndData', ske)


class MembershipEditAssemblyTests(unittest.TestCase):
    """HammerspaceMain must treat a membership-edited SkinDataEdited as the
    complete structure (gained/lost entries), not splice payload into the
    donor's entry list by BoneIndex — which would silently drop a brand-new
    entry and keep a stale entry for a bone that lost every vertex."""

    def _model(self):
        return {'SluggiesModel': {
            'UseBase64': False,
            'SkinData': {
                'QuantizeInfo': 9,
                'SK1s': [{
                    'BoneIndex': 10, 'VertexCnt': 2, 'GplVertexArrValue': 0,
                    'BindPoseData': [0] * 24,
                }],
                'SK2s': [],
                'SKAccs': [],
                'FlushIndSize': 1,
                'FlushIndData': [0xAA, 0xAA],
            },
            'SkinDataEdited': {
                'MembershipEdited': True,
                'QuantizeInfo': 9,
                'SK1s': [
                    {'BoneIndex': 10, 'VertexCnt': 1, 'GplVertexArrValue': 0,
                     'BindPoseData': [1] * 12},
                    {'BoneIndex': 20, 'VertexCnt': 1, 'GplVertexArrValue': 12,
                     'BindPoseData': [2] * 12},
                ],
                'SK2s': [],
                'SKAccs': [],
            },
            'Submeshes': [{
                'FacesCount': 0,
                'FacesData': [],
                'FaceTextureIndices': [],
                'VertexBuffer': {
                    'VertexBufferData': [0] * 12,
                    'VertexBufferCompCount': 6,
                    'VertexBufferQuantizeInfo': 0,
                },
                'UVChannels': [],
                'ColorChannels': [],
                'DisplayStates': [{
                    'DisplayStateId': 7,
                    'PrimListData': [0xAA],
                    'PrimListDataEdited': [0xBB],
                    'ShaderMode': '00000000',
                    'PrimListPtrFieldOffset': '0x0',
                    'PrimListSizeFieldOffset': '0x0',
                    'PrimListAbsoluteOffset': '0x0',
                    'PrimListLength': 1,
                    'DisplayStatePadBytes': '000000',
                }],
            }],
        }}

    def test_membership_edit_reflects_gained_and_lost_bones(self):
        parsed = main.ParseSluggie(self._model())

        bones = {sk.bone_index: sk.vertex_cnt for sk in parsed.skinning.sk1s}
        # Donor had only bone 10 (2 verts); the edit moves one vertex to a
        # brand-new bone 20 and shrinks bone 10 to 1 — both must show up.
        self.assertEqual(bones, {10: 1, 20: 1})

    def test_membership_edit_uses_laid_out_flush_index_data(self):
        data = self._model()
        ske = data['SluggiesModel']['SkinDataEdited']
        ske['SK1s'][1]['VertexOffset'] = 0
        ske.update(FlushIndSize=2, FlushIndData=[0xBB, 0xBB, 0xCC, 0xCC])
        # no topology edit: the membership pass alone must select edited flush data
        del data['SluggiesModel']['Submeshes'][0]['DisplayStates'][0]['PrimListDataEdited']

        parsed = main.ParseSluggie(data)

        self.assertEqual(parsed.skinning.flush_ind_size, 2)
        self.assertEqual(parsed.skinning.flush_ind_data, b'\xBB\xBB\xCC\xCC')
        self.assertEqual([sk.vertex_offset for sk in parsed.skinning.sk1s], [0, 0])


if __name__ == '__main__':
    unittest.main()
