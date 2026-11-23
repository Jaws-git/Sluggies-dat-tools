"""``encode_skin_hammerspace`` and its phase functions, lifted from ``ExportSluggies.py`` (no bpy needed) and
run on a small donor: bone 1 and bone 5 own an SK1 entry each, bones 2 + 3 an SK2 entry, bone 4 an SKAcc that
supplements vertex 3 and alone drives vertex 4."""

import ast
import pathlib
import struct
import sys
import unittest
from types import SimpleNamespace

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import addon_sources  # noqa: E402
EXPORT_PATH = addon_sources.EXPORT
if str(EXPORT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(EXPORT_PATH.parent))
import FieldCodec  # noqa: E402
import SkinnedRebuildExport  # noqa: E402
from SkinWeights import MAX_BONE_INFLUENCES_PER_VERTEX, quantize_skin_weights  # noqa: E402

NAMES = {
    'math', 'struct', 'dataclass',
    '_to_bytes', '_from_bytes', '_pack_quantized_component', '_comp_size_skin', '_skn_block_size',
    'skinned_donor_objects', '_skin_component_packer', '_submesh_vertex_ranges', '_DonorSkinIndex',
    '_index_donor_skin', '_SkinGroups', '_vertex_skin_influences', '_changed_weight_units', '_replay_donor_roles',
    '_assign_edited_roles', '_donor_aligned_weight_edits',
    '_classify_skin_vertices', '_report_skin_classification', '_restore_duplicate_dest_skaccs',
    '_encode_skin_source', '_build_skin_entries', '_skin_size_message', 'encode_skin_hammerspace',
    '_get_vgroup_weight', '_parse_bone_group_name', 'encode_skin_weights_inplace',
}


def _binds_name(node):
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return node.name in NAMES
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any((a.asname or a.name) in NAMES for a in node.names)
    return False


def _export_functions():
    tree = ast.parse(EXPORT_PATH.read_text(encoding='utf-8'))
    namespace = {
        'FieldCodec': FieldCodec, 'SkinnedRebuildExport': SkinnedRebuildExport,
        'quantize_skin_weights': quantize_skin_weights,
        'MAX_BONE_INFLUENCES_PER_VERTEX': MAX_BONE_INFLUENCES_PER_VERTEX,
    }
    body = [node for node in tree.body if _binds_name(node)]
    exec(compile(ast.Module(body=body, type_ignores=[]), str(EXPORT_PATH), 'exec'), namespace)
    missing = NAMES - set(namespace)
    assert not missing, missing
    return SimpleNamespace(**namespace)


EXPORT = _export_functions()

QUANT = 0x08                    # s16, 8 fraction bits
STRIDE = 12                     # position + normal, 2 bytes each
VERTEX_COUNT = 6
VB_OFFSET = 100


def _field(raw):
    return FieldCodec.encode_field(bytes(raw), False)


def _bytes(value):
    return FieldCodec.decode_field(value)


def _donor():
    return {'SluggiesModel': {
        'UseCompression': False,
        'Submeshes': [{'VertexBuffer': {
            'VertexBufferOffset': VB_OFFSET, 'VertexBufferCompCount': 6, 'VertexBufferQuantizeInfo': QUANT,
            'VertexBufferLength': VERTEX_COUNT * STRIDE,
        }}],
        'SkinData': {
            'QuantizeInfo': QUANT,
            'SK1s': [
                {'BoneIndex': 1, 'VertexCnt': 2, 'GplVertexArrValue': 0, 'BindPoseData': _field(bytes(2 * STRIDE))},
                {'BoneIndex': 5, 'VertexCnt': 1, 'GplVertexArrValue': 5 * STRIDE,
                 'BindPoseData': _field(bytes(STRIDE))},
            ],
            'SK2s': [{
                'BoneIndex1': 2, 'BoneIndex2': 3, 'VertexCnt': 2, 'GplVertexArrValue': 2 * STRIDE,
                'BindPoseData': _field(bytes(2 * STRIDE)), 'WeightData': _field([128, 128, 200, 46]),
            }],
            'SKAccs': [{
                'BoneIndex': 4, 'VertexCnt': 2, 'GplDestArrValue': 0, 'BindPoseData': _field(bytes(2 * STRIDE)),
                'DestIndexData': _field(struct.pack('>2H', 3, 4)), 'WeightData': _field([10, 255]),
            }],
        },
    }}


# The donor's bones per vertex, as Blender weights.
DONOR_WEIGHTS = [{1: 1.0}, {1: 1.0}, {2: 0.5, 3: 0.5}, {2: 0.78, 3: 0.18, 4: 0.04}, {4: 255 / 256}, {5: 1.0}]
GROUP_NAMES = ['bone_1', 'bone_2', 'bone_3', 'bone_4', 'bone_5', 'bone_6', 'bone_7', 'bone_8', 'bone_77', 'Helper']


class _Groups(list):
    def get(self, name):
        return next((group for group in self if group.name == name), None)


class _Group:
    def __init__(self, name, index, obj):
        self.name, self.index, self._obj = name, index, obj

    def weight(self, vertex_index):
        for element in self._obj.data.vertices[vertex_index].groups:
            if element.group == self.index:
                return element.weight
        raise RuntimeError('vertex is not in the group')


class _Object:
    """Stands in for a Blender mesh object: custom properties, vertex groups and vertices."""

    def __init__(self, weights, name='Body'):
        self.name = name
        self.vertex_groups = _Groups(_Group(n, i, self) for i, n in enumerate(GROUP_NAMES))
        self.data = SimpleNamespace(vertices=[
            SimpleNamespace(
                index=i, co=SimpleNamespace(x=float(i), y=0.5, z=-1.0), normal=SimpleNamespace(x=0.0, y=0.0, z=1.0),
                groups=[SimpleNamespace(group=GROUP_NAMES.index(self._group(key)), weight=w)
                        for key, w in vertex.items()],
            )
            for i, vertex in enumerate(weights)
        ])

    @staticmethod
    def _group(key):
        return key if isinstance(key, str) else f'bone_{key}'

    def __contains__(self, key):
        return key == 'VertexBufferOffset'

    def __getitem__(self, key):
        return {'VertexBufferOffset': VB_OFFSET}[key]


def _weights(**changes):
    """DONOR_WEIGHTS with ``v<index>=<weights>`` replaced."""
    weights = [dict(vertex) for vertex in DONOR_WEIGHTS]
    for key, value in changes.items():
        weights[int(key[1:])] = value
    return weights


def _encode(weights):
    data = _donor()
    warnings = []
    result = EXPORT.encode_skin_hammerspace([_Object(weights)], data, warnings)
    return result, warnings, data['SluggiesModel'].get('SkinDataEdited')


def _classify(weights):
    data = _donor()
    skin = data['SluggiesModel']['SkinData']
    obj = _Object(weights)
    donor = EXPORT._index_donor_skin(skin, STRIDE)
    return EXPORT._classify_skin_vertices({id(obj): (0, obj)}, skin, donor, [0])


def _members(entries):
    """An SK group's entries without the object reference."""
    return [entry[:-1] for entry in entries]


class DonorIndexTests(unittest.TestCase):
    def setUp(self):
        self.index = EXPORT._index_donor_skin(_donor()['SluggiesModel']['SkinData'], STRIDE)

    def test_every_vertex_knows_its_donor_bones(self):
        self.assertEqual(self.index.membership, {0: {1}, 1: {1}, 2: {2, 3}, 3: {2, 3, 4}, 4: {4}, 5: {5}})
        self.assertEqual(self.index.skinned_bone_ids, {1, 2, 3, 4, 5})

    def test_vertices_map_to_entries_not_to_bones(self):
        self.assertEqual(self.index.sk1_entry_of, {0: 0, 1: 0, 5: 1})
        self.assertEqual(self.index.sk2_entry_of, {2: 0, 3: 0})
        self.assertEqual(self.index.first_sk1_entry_by_bone, {1: 0, 5: 1})
        self.assertEqual(self.index.first_sk2_entry_by_pair, {(2, 3): 0})

    def test_accumulation_weights_are_the_donor_bytes(self):
        self.assertEqual(self.index.skacc_weight, {(4, 3): 10, (4, 4): 255})

    def test_submesh_ranges(self):
        submeshes = _donor()['SluggiesModel']['Submeshes'] * 2
        self.assertEqual(EXPORT._submesh_vertex_ranges(submeshes), ([0, 6], [6, 6]))


class UnchangedMembershipTests(unittest.TestCase):
    def test_the_donor_structure_is_replayed(self):
        result, warnings, edited = _encode(DONOR_WEIGHTS)
        self.assertEqual(result, (True, None))
        self.assertEqual(warnings, [])
        self.assertEqual([(e['BoneIndex'], e['VertexCnt'], e['GplVertexArrValue']) for e in edited['SK1s']],
                         [(1, 2, 0), (5, 1, 5 * STRIDE)])
        self.assertEqual(struct.unpack('>2H', _bytes(edited['SK1s'][0]['VertexIndices'])), (0, 1))
        sk2 = edited['SK2s'][0]
        self.assertEqual((sk2['BoneIndex1'], sk2['BoneIndex2'], sk2['VertexCnt']), (2, 3, 2))
        skacc = edited['SKAccs'][0]
        self.assertEqual((skacc['BoneIndex'], skacc['VertexCnt']), (4, 2))
        self.assertEqual(struct.unpack('>2H', _bytes(skacc['DestIndexData'])), (3, 4))
        self.assertEqual(edited['FlushIndSize'], 0)
        self.assertEqual(edited['QuantizeInfo'], QUANT)

    def test_unchanged_weights_leave_no_weight_edit_on_the_donor_entries(self):
        data = _donor()
        EXPORT.encode_skin_hammerspace([_Object(DONOR_WEIGHTS)], data, [])
        skin = data['SluggiesModel']['SkinData']
        self.assertFalse(any('WeightDataEdited' in e for e in skin['SK2s'] + skin['SKAccs']))

    def test_weights_that_round_to_the_donor_units_are_unchanged(self):
        """Vertex 3 holds 200 + 46 + 10 = 256 units; the import's weights are units / 256."""
        weights = _weights(v3={2: 200.4 / 256, 3: 45.6 / 256, 4: 10 / 256})
        result, _warnings, edited = _encode(weights)
        self.assertEqual(result, (True, None))
        self.assertEqual(_bytes(edited['SK2s'][0]['WeightData']), bytes([128, 128, 200, 46]))

    def test_a_donor_total_other_than_256_is_kept(self):
        """Original vertices sum to 255 or 257 here and there; untouched, they keep their bytes."""
        data = _donor()
        data['SluggiesModel']['SkinData']['SK2s'][0]['WeightData'] = _field([128, 128, 200, 45])
        EXPORT.encode_skin_hammerspace(
            [_Object(_weights(v3={2: 200 / 256, 3: 45 / 256, 4: 10 / 256}))], data, [])
        model = data['SluggiesModel']
        self.assertEqual(_bytes(model['SkinDataEdited']['SK2s'][0]['WeightData']), bytes([128, 128, 200, 45]))
        self.assertNotIn('WeightDataEdited', model['SkinData']['SK2s'][0])

    def test_weights_all_scaled_alike_are_unchanged(self):
        result, _warnings, edited = _encode(_weights(v2={2: 0.2, 3: 0.2}))
        self.assertEqual(result, (True, None))
        self.assertEqual(_bytes(edited['SK2s'][0]['WeightData']), bytes([128, 128, 200, 46]))

    def test_a_vertex_with_more_bones_than_the_limit_is_kept_while_it_has_its_donor_bones(self):
        """Original models hold up to five bones on a vertex."""
        data = _donor()
        skin = data['SluggiesModel']['SkinData']
        skin['SK2s'][0]['WeightData'] = _field([128, 128, 100, 100])
        for bone, units in ((6, 20), (7, 20), (8, 6)):
            skin['SKAccs'].append({
                'BoneIndex': bone, 'VertexCnt': 1, 'GplDestArrValue': 0, 'BindPoseData': _field(bytes(STRIDE)),
                'DestIndexData': _field(struct.pack('>H', 3)), 'WeightData': _field([units]),
            })
        weights = _weights(v3={2: 100 / 256, 3: 100 / 256, 4: 10 / 256, 'bone_6': 20 / 256, 'bone_7': 20 / 256,
                               'bone_8': 6 / 256})
        warnings = []
        result = EXPORT.encode_skin_hammerspace([_Object(weights)], data, warnings)
        self.assertEqual((result, warnings), ((True, None), []))
        self.assertEqual([e['BoneIndex'] for e in data['SluggiesModel']['SkinDataEdited']['SKAccs']], [4, 6, 7, 8])


class RepaintedWeightTests(unittest.TestCase):
    """Bones unchanged, weights changed: the Blender weights are written in the donor's roles."""

    def _export(self, weights, data=None):
        data = data or _donor()
        warnings = []
        result = EXPORT.encode_skin_hammerspace([_Object(weights)], data, warnings)
        return result, warnings, data['SluggiesModel']

    def test_a_repainted_pair_is_written(self):
        result, warnings, model = self._export(_weights(v2={2: 0.25, 3: 0.75}))
        self.assertEqual(result, (True, 'Skin weights changed on 1 vertex(es) that keep their bones.'))
        self.assertEqual(warnings, [])
        # in SkinDataEdited, and on the donor entry in the donor's order for the builder
        self.assertEqual(_bytes(model['SkinDataEdited']['SK2s'][0]['WeightData']), bytes([64, 192, 200, 46]))
        self.assertEqual(_bytes(model['SkinData']['SK2s'][0]['WeightDataEdited']), bytes([64, 192, 200, 46]))
        self.assertNotIn('WeightDataEdited', model['SkinData']['SKAccs'][0])
        self.assertEqual(_bytes(model['SkinData']['SK2s'][0]['WeightData']), bytes([128, 128, 200, 46]))

    def test_the_donor_pair_order_is_kept_on_the_donor_entry(self):
        data = _donor()
        entry = data['SluggiesModel']['SkinData']['SK2s'][0]
        entry.update(BoneIndex1=3, BoneIndex2=2)                # bone 3 first, as some donors have it
        _result, _warnings, model = self._export(_weights(v2={2: 0.25, 3: 0.75}), data)
        self.assertEqual(_bytes(model['SkinData']['SK2s'][0]['WeightDataEdited'])[:2], bytes([192, 64]))
        edited = model['SkinDataEdited']['SK2s'][0]
        self.assertEqual((edited['BoneIndex1'], edited['BoneIndex2']), (2, 3))
        self.assertEqual(_bytes(edited['WeightData'])[:2], bytes([64, 192]))

    def test_a_supplement_is_repainted_with_its_pair(self):
        """The weights are normalized to 256 units; the third bone stays an accumulation."""
        _result, _warnings, model = self._export(_weights(v3={2: 0.5, 3: 0.25, 4: 0.25}))
        self.assertEqual(_bytes(model['SkinData']['SK2s'][0]['WeightDataEdited']), bytes([128, 128, 128, 64]))
        self.assertEqual(_bytes(model['SkinData']['SKAccs'][0]['WeightDataEdited']), bytes([64, 255]))
        self.assertEqual(_bytes(model['SkinDataEdited']['SKAccs'][0]['WeightData']), bytes([64, 255]))

    def test_unnormalized_weights_are_normalized(self):
        _result, _warnings, model = self._export(_weights(v2={2: 0.2, 3: 0.6}))
        self.assertEqual(_bytes(model['SkinData']['SK2s'][0]['WeightDataEdited'])[:2], bytes([64, 192]))

    def test_a_share_that_rounds_to_nothing_keeps_its_record(self):
        """The entries stay the donor's, so the layout does."""
        _result, _warnings, model = self._export(_weights(v3={2: 0.5, 3: 0.5, 4: 0.0001}))
        self.assertEqual(_bytes(model['SkinData']['SKAccs'][0]['WeightDataEdited']), bytes([0, 255]))
        skacc = model['SkinDataEdited']['SKAccs'][0]
        self.assertEqual(struct.unpack('>2H', _bytes(skacc['DestIndexData'])), (3, 4))
        self.assertEqual(_bytes(skacc['WeightData']), bytes([0, 255]))

    def test_a_full_weight_on_one_pair_bone_is_stored_as_255(self):
        _result, _warnings, model = self._export(_weights(v2={2: 1.0, 3: 0.0001}))
        self.assertEqual(_bytes(model['SkinData']['SK2s'][0]['WeightDataEdited'])[:2], bytes([255, 0]))

    def test_a_vertex_written_twice_by_one_entry_gets_its_weight_once(self):
        data = _donor()
        skin = data['SluggiesModel']['SkinData']
        skin['SKAccs'][0].update(VertexCnt=3, BindPoseData=_field(bytes(3 * STRIDE)),
                                 DestIndexData=_field(struct.pack('>3H', 3, 4, 4)), WeightData=_field([10, 255, 1]))
        skin['SKAccs'].append({
            'BoneIndex': 6, 'VertexCnt': 1, 'GplDestArrValue': 0, 'BindPoseData': _field(bytes(STRIDE)),
            'DestIndexData': _field(struct.pack('>H', 4)), 'WeightData': _field([1]),
        })
        _result, warnings, model = self._export(_weights(v4={4: 0.5, 'bone_6': 0.5}), data)
        self.assertEqual(warnings, [])
        self.assertEqual(_bytes(model['SkinData']['SKAccs'][0]['WeightDataEdited']), bytes([10, 128, 0]))
        self.assertEqual(_bytes(model['SkinData']['SKAccs'][1]['WeightDataEdited']), bytes([128]))
        edited = {e['BoneIndex']: e for e in model['SkinDataEdited']['SKAccs']}
        self.assertEqual(struct.unpack('>3H', _bytes(edited[4]['DestIndexData'])), (3, 4, 4))
        self.assertEqual(_bytes(edited[4]['WeightData']), bytes([10, 128, 0]))
        self.assertEqual(_bytes(edited[6]['WeightData']), bytes([128]))

    def test_bind_pose_is_position_and_normal_of_each_member(self):
        _result, _warnings, edited = _encode(DONOR_WEIGHTS)
        self.assertEqual(struct.unpack('>6h', _bytes(edited['SK1s'][1]['BindPoseData'])),
                         (5 * 256, 128, -256, 0, 0, 256))

    def test_a_position_out_of_range_names_its_vertex(self):
        data = _donor()
        obj = _Object(DONOR_WEIGHTS)
        obj.data.vertices[4].co.y = 500.0
        with self.assertRaisesRegex(ValueError, 'Body vertex 4 SKN y.*outside int16'):
            EXPORT.encode_skin_hammerspace([obj], data, [])
        self.assertNotIn('SkinDataEdited', data['SluggiesModel'])

    def test_groups_of_other_bones_are_ignored_with_one_warning(self):
        weights = _weights(v0={1: 1.0, 'bone_77': 0.5, 'Helper': 0.5}, v1={1: 1.0, 'bone_77': 0.2})
        result, warnings, edited = _encode(weights)
        self.assertEqual(result, (True, None))
        self.assertEqual(warnings, ["Vertex group 'bone_77' does not resolve to a donor-skinned bone; ignored."])
        self.assertEqual(edited['SK1s'][0]['VertexCnt'], 2)

    def test_nothing_is_written_without_a_skinned_object(self):
        data = _donor()
        custom = {'SluggiesCustomSubmesh': True}
        self.assertEqual(EXPORT.encode_skin_hammerspace([custom], data, []), (True, None))
        self.assertNotIn('SkinDataEdited', data['SluggiesModel'])

    def test_an_unskinned_model_is_not_an_error(self):
        data = {'SluggiesModel': {'Submeshes': []}}
        self.assertEqual(EXPORT.encode_skin_hammerspace([], data, []), (True, None))


class EditedMembershipTests(unittest.TestCase):
    def test_a_moved_vertex_is_refused(self):
        data = _donor()
        with self.assertRaisesRegex(ValueError, 'not routed to the body rebuild'):
            EXPORT.encode_skin_hammerspace([_Object(_weights(v0={5: 1.0}))], data, [])
        self.assertNotIn('SkinDataEdited', data['SluggiesModel'])

    def test_a_vertex_that_lost_its_bones_is_named(self):
        with self.assertRaisesRegex(
                ValueError, r'1 vertex\(es\) have no valid donor bone influence: Body vertex 1\. '
                            r'Ignored vertex groups \(not donor-skinned bones\): bone_77\.'):
            _encode(_weights(v1={'bone_77': 1.0}))

    def test_a_vertex_without_donor_bones_may_stay_unweighted(self):
        data = _donor()
        del data['SluggiesModel']['SkinData']['SK1s'][1]         # vertex 5 has no coverage in the donor
        warnings = []
        result = EXPORT.encode_skin_hammerspace([_Object(_weights(v5={}))], data, warnings)
        self.assertEqual((result, warnings), ((True, None), []))

    def test_one_bone_goes_to_that_bones_first_entry(self):
        groups = _classify(_weights(v0={5: 1.0}))
        self.assertTrue(groups.membership_edited)
        self.assertEqual({key: _members(entries) for key, entries in groups.sk1.items()},
                         {1: [(0, 0), (0, 5)], 0: [(0, 1)]})

    def test_a_new_pair_gets_a_new_entry_with_fresh_weights(self):
        groups = _classify(_weights(v0={1: 0.75, 2: 0.25}))
        self.assertEqual(_members(groups.sk2[('new_sk2', 1, 2)]), [(0, 0, 192, 64)])

    def test_a_third_bone_becomes_an_accumulation(self):
        groups = _classify(_weights(v2={2: 0.5, 3: 0.25, 5: 0.25}))
        self.assertEqual(_members(groups.sk2[0]), [(0, 2, 128, 64), (0, 3, 200, 46)])
        self.assertEqual(_members(groups.skacc[5]), [(0, 2, 64, 2)])

    def test_an_accumulation_only_vertex_stays_one(self):
        groups = _classify(_weights(v4={4: 0.5, 1: 0.5}))
        self.assertEqual(_members(groups.skacc[1]), [(0, 4, 128, 4)])
        self.assertEqual(_members(groups.skacc[4]), [(0, 3, 10, 3), (0, 4, 128, 4)])
        self.assertNotIn(4, [entry[1] for entries in groups.sk1.values() for entry in entries])

    def test_more_bones_than_the_limit_are_cut_to_the_strongest(self):
        weights = _weights(v0={1: 0.5, 2: 0.2, 3: 0.15, 4: 0.1, 5: 0.05})
        groups = _classify(weights)
        self.assertEqual(groups.overweighted, [('Body', 0, 5)])
        self.assertNotIn(5, {bone for bone, entries in groups.skacc.items() if any(e[1] == 0 for e in entries)})
        warnings = []
        EXPORT._report_skin_classification(groups, warnings)
        self.assertEqual(warnings, [SkinnedRebuildExport.limited_warning('Body', [0])])


class InPlaceWeightTests(unittest.TestCase):
    """``encode_skin_weights_inplace``: the donor's bytes for untouched vertices, normalized weights for
    repainted ones (the rule of the Hammerspace encoder)."""

    def _export(self, weights, data=None):
        data = data or _donor()
        self.assertTrue(EXPORT.encode_skin_weights_inplace([_Object(weights)], data, []))
        skin = data['SluggiesModel']['SkinData']
        return skin['SK2s'][0], skin['SKAccs'][0]

    def test_an_untouched_model_writes_the_donor_weights(self):
        sk2, skacc = self._export(DONOR_WEIGHTS)
        self.assertEqual(_bytes(sk2['WeightDataEdited']), bytes([128, 128, 200, 46]))
        self.assertEqual(_bytes(skacc['WeightDataEdited']), bytes([10, 255]))
        self.assertNotIn('VertexCntEdited', skacc)

    def test_a_donor_total_other_than_256_is_kept(self):
        data = _donor()
        data['SluggiesModel']['SkinData']['SK2s'][0]['WeightData'] = _field([128, 128, 200, 45])
        sk2, skacc = self._export(_weights(v3={2: 200 / 256, 3: 45 / 256, 4: 10 / 256}), data)
        self.assertEqual(_bytes(sk2['WeightDataEdited']), bytes([128, 128, 200, 45]))
        self.assertEqual(_bytes(skacc['WeightDataEdited']), bytes([10, 255]))

    def test_repainted_weights_are_normalized(self):
        """0.2 and 0.6 used to be written as 51 and 154, a sum of 205."""
        sk2, _skacc = self._export(_weights(v2={2: 0.2, 3: 0.6}))
        self.assertEqual(_bytes(sk2['WeightDataEdited']), bytes([64, 192, 200, 46]))

    def test_weights_all_scaled_alike_are_unchanged(self):
        sk2, _skacc = self._export(_weights(v2={2: 0.2, 3: 0.2}))
        self.assertEqual(_bytes(sk2['WeightDataEdited']), bytes([128, 128, 200, 46]))

    def test_a_supplement_shares_the_256_units_with_its_pair(self):
        sk2, skacc = self._export(_weights(v3={2: 0.5, 3: 0.25, 4: 0.25}))
        self.assertEqual(_bytes(sk2['WeightDataEdited']), bytes([128, 128, 128, 64]))
        self.assertEqual(_bytes(skacc['WeightDataEdited']), bytes([64, 255]))

    def test_a_removed_supplement_leaves_its_share_to_the_pair(self):
        sk2, skacc = self._export(_weights(v3={2: 0.75, 3: 0.25}))
        self.assertEqual(_bytes(sk2['WeightDataEdited']), bytes([128, 128, 192, 64]))
        self.assertEqual(skacc['VertexCntEdited'], 1)
        self.assertEqual(_bytes(skacc['WeightDataEdited']), bytes([255]))
        self.assertEqual(struct.unpack('>H', _bytes(skacc['DestIndexDataEdited'])), (4,))

    def test_a_bone_the_vertex_does_not_have_in_the_donor_takes_no_share(self):
        """It has no record on this vertex, so the units go to the bones that do."""
        sk2, _skacc = self._export(_weights(v2={2: 0.25, 3: 0.25, 5: 0.5}))
        self.assertEqual(_bytes(sk2['WeightDataEdited']), bytes([128, 128, 200, 46]))

    def test_a_vertex_written_twice_by_one_entry_keeps_its_records(self):
        data = _donor()
        data['SluggiesModel']['SkinData']['SKAccs'][0].update(
            VertexCnt=3, BindPoseData=_field(bytes(3 * STRIDE)),
            DestIndexData=_field(struct.pack('>3H', 3, 4, 4)), WeightData=_field([10, 255, 1]))
        _sk2, skacc = self._export(_weights(v4={4: 1.0}), data)
        self.assertEqual(_bytes(skacc['WeightDataEdited']), bytes([10, 255, 1]))


class DuplicateDestTests(unittest.TestCase):
    """A donor SKAcc that accumulates twice onto one vertex cannot be held by Blender's vertex groups."""

    def _data(self):
        data = _donor()
        skacc = data['SluggiesModel']['SkinData']['SKAccs'][0]
        skacc.update(VertexCnt=3, BindPoseData=_field(bytes(3 * STRIDE)),
                     DestIndexData=_field(struct.pack('>3H', 3, 4, 4)), WeightData=_field([10, 255, 1]))
        return data

    def test_it_is_restored_from_the_donor(self):
        data = self._data()
        warnings = []
        EXPORT.encode_skin_hammerspace([_Object(DONOR_WEIGHTS)], data, warnings)
        skacc = data['SluggiesModel']['SkinDataEdited']['SKAccs'][0]
        self.assertEqual(struct.unpack('>3H', _bytes(skacc['DestIndexData'])), (3, 4, 4))
        self.assertEqual(_bytes(skacc['WeightData']), bytes([10, 255, 1]))
        self.assertEqual(warnings, [])

    def test_an_edited_bone_keeps_the_blender_entry(self):
        skin = self._data()['SluggiesModel']['SkinData']
        obj = _Object(DONOR_WEIGHTS)
        groups = {4: [(0, 4, 255, 4, obj)]}                     # vertex 3 no longer on bone 4
        warnings = []
        EXPORT._restore_duplicate_dest_skaccs(skin, groups, {id(obj): (0, obj)}, [0], [VERTEX_COUNT], STRIDE,
                                              warnings, {})
        self.assertEqual(_members(groups[4]), [(0, 4, 255, 4)])
        self.assertEqual(len(warnings), 1)
        self.assertIn('SKAcc bone 4: membership edited', warnings[0])


class SizeMessageTests(unittest.TestCase):
    def _candidate(self):
        _result, _warnings, edited = _encode(DONOR_WEIGHTS)
        return edited

    def test_the_same_size_says_nothing(self):
        skin = _donor()['SluggiesModel']['SkinData']
        self.assertIsNone(EXPORT._skin_size_message(skin, self._candidate(), 0))

    def test_growth_is_reported_with_both_layouts(self):
        skin = _donor()['SluggiesModel']['SkinData']
        candidate = self._candidate()
        candidate['SK1s'].append(dict(candidate['SK1s'][1]))
        message = EXPORT._skin_size_message(skin, candidate, 0)
        self.assertTrue(message.startswith('SKN block grew by 76 B (hammerspace accommodates this).\n'))
        # 0x24 header + 2 * 0x40 + 0x74 + 0x44 structs + 96 bytes of data
        self.assertIn('Original: 444 B  SK1: 2 entries / 3 verts  SK2: 1 entries / 2 verts  '
                      'SKAcc: 1 entries / 2 verts', message)
        self.assertIn('Edited:   520 B  SK1: 3 entries / 4 verts', message)

    def test_shrinkage_counts_the_vertex_offset_prefix_as_kept(self):
        skin = _donor()['SluggiesModel']['SkinData']
        skin['SK1s'][0]['VertexOffset'] = 0
        skin['SK1s'][0]['BindPoseData'] = _field(bytes(2 * STRIDE + 8))
        self.assertEqual(EXPORT._skn_block_size(skin), 452)
        candidate = self._candidate()
        self.assertTrue(EXPORT._skin_size_message(skin, candidate, 0).startswith('SKN block shrunk by 8 B'))
        skin['SK1s'][0]['VertexOffset'] = 8                      # the 8 bytes were only the prefix
        self.assertIsNone(EXPORT._skin_size_message(skin, candidate, 0))

    def test_a_changed_vertex_count_adds_the_note(self):
        skin = _donor()['SluggiesModel']['SkinData']
        candidate = self._candidate()
        del candidate['SKAccs'][0]
        message = EXPORT._skin_size_message(skin, candidate, 0)
        self.assertIn('SKN block shrunk by', message)
        self.assertIn('Note: SKAcc vertex count differs from original', message)


if __name__ == '__main__':
    unittest.main()
