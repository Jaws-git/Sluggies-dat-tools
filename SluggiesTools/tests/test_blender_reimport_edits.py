"""A re-import of an exported .sluggie restores the edits the next export would
otherwise drop: colours, standalone normals, face surfaces, root-bone scale,
shader mode / specular strength, texture assignments and skin weights.

The importer needs bpy, so its bpy-free helpers are loaded from the source.
"""
import ast
import math
import pathlib
import struct
import sys
import unittest

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
IMPORTER_PATH = ROOT_DIR / 'BlenderAddonSrc' / 'ImportSluggies.py'
if str(ROOT_DIR / 'BlenderAddonSrc') not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / 'BlenderAddonSrc'))
import FieldCodec  # noqa: E402  (bpy-free)

_HELPERS = (
    '_to_bytes', '_from_bytes', '_has_edited_data', '_edited_submesh_view',
    '_edited_face_surface_ids', '_rigid_rebuild_view', '_assigned_surface_texture_files',
    '_restore_root_bone_scale', '_set_surface_material_metadata',
    '_submesh_vertex_ranges', '_skin_bytes', '_decode_skin_totals',
    '_decode_model_skin_edit', '_edited_skin_influences', '_edit_object_influences',
)


def _load():
    tree = ast.parse(IMPORTER_PATH.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body
             if (isinstance(node, ast.FunctionDef) and node.name in _HELPERS)
             or (isinstance(node, ast.Assign)
                 and any(getattr(t, 'id', None) == '_INPLACE_SKIN_EDIT_KEYS' for t in node.targets))]
    namespace = {'struct': struct, 'math': math, 'FieldCodec': FieldCodec}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(IMPORTER_PATH), 'exec'), namespace)
    return namespace


H = _load()


def enc(raw):
    return FieldCodec.encode_field(bytes(raw), False)


def vb(count, comp_count=3, quant=0x40, edited=None):
    buffer = {'VertexBufferLength': count * comp_count * 4, 'VertexBufferCompCount': comp_count,
              'VertexBufferQuantizeInfo': quant, 'VertexBufferData': enc(b'\0' * count * comp_count * 4)}
    if edited is not None:
        buffer['VertexBufferDataEdited'] = edited
    return buffer


class _Ui:
    def update(self, **_kwargs):
        pass


class _Material(dict):
    def id_properties_ui(self, _name):
        return _Ui()


class EditedViewTests(unittest.TestCase):
    def test_colours_normals_and_face_surfaces_are_promoted(self):
        submesh = {
            'VertexBuffer': vb(3), 'FacesCount': 2,
            'ColorChannels': [{'ColorChannelIndex': 0, 'ColorChannelData': 'donor',
                               'ColorFacesData': 'donor_faces',
                               'ColorChannelDataEdited': 'edit', 'ColorFacesDataEdited': 'edit_faces'}],
            'NormalBuffer': {'NormalBufferData': 'n', 'NormalFacesData': 'nf',
                             'NormalBufferDataEdited': 'n2', 'NormalFacesDataEdited': 'nf2'},
            'DisplayStates': [{'SurfaceId': 'a'}, {'SurfaceId': 'b'}],
            'FaceSurfaceIdsEdited': enc(struct.pack('>2H', 1, 0)),
        }
        self.assertTrue(H['_has_edited_data'](submesh))
        view = H['_edited_submesh_view'](submesh)
        self.assertEqual(view['ColorChannels'][0]['ColorChannelData'], 'edit')
        self.assertEqual(view['ColorChannels'][0]['ColorFacesData'], 'edit_faces')
        self.assertEqual(view['NormalBuffer']['NormalBufferData'], 'n2')
        self.assertEqual(view['NormalBuffer']['NormalFacesData'], 'nf2')
        self.assertEqual(view['_FaceSurfaceIds'], ['b', 'a'])
        # the donor submesh itself is left alone
        self.assertEqual(submesh['ColorChannels'][0]['ColorChannelData'], 'donor')

    def test_face_surface_ids_that_do_not_fit_are_ignored(self):
        submesh = {'FacesCount': 3, 'DisplayStates': [{'SurfaceId': 'a'}],
                   'FaceSurfaceIdsEdited': enc(struct.pack('>2H', 0, 0))}
        self.assertIsNone(H['_edited_face_surface_ids'](submesh))
        submesh = {'FacesCount': 1, 'DisplayStates': [{'SurfaceId': 'a'}],
                   'FaceSurfaceIdsEdited': enc(struct.pack('>H', 5))}
        self.assertIsNone(H['_edited_face_surface_ids'](submesh))

    def test_unedited_submesh_makes_no_edit_object(self):
        self.assertFalse(H['_has_edited_data']({'VertexBuffer': vb(3),
                                                'ColorChannels': [{'ColorChannelData': 'x'}]}))


class MaterialAndModelTests(unittest.TestCase):
    def test_edited_shader_mode_and_specular_win(self):
        mat = _Material()
        H['_set_surface_material_metadata'](mat, {
            'DisplayStateId': 7, 'ShaderMode': 'Spec', 'ShaderModeEdited': 'Shdw',
            'DisplayStateParamBytes': '100000', 'DisplayStateParamBytesEdited': '400000'})
        self.assertEqual(mat['ShaderMode'], 'Shdw')
        self.assertEqual(mat['SpecularStrength'], 0x40)

    def test_unedited_material_keeps_donor_values(self):
        mat = _Material()
        H['_set_surface_material_metadata'](mat, {
            'DisplayStateId': 7, 'ShaderMode': 'Spec', 'DisplayStateParamBytes': '100000'})
        self.assertEqual((mat['ShaderMode'], mat['SpecularStrength']), ('Spec', 0x10))

    def test_root_bone_scale_is_restored(self):
        arm = {'RootBoneScale': (1.0, 1.0, 1.0)}
        H['_restore_root_bone_scale'](arm, [1.5, 1.5, 2.0])
        self.assertEqual(arm['RootBoneScale'], (1.5, 1.5, 2.0))
        for bad in (None, [1.0, 2.0], ['x', 1, 1], [float('nan'), 1, 1]):
            arm = {'RootBoneScale': (1.0, 1.0, 1.0)}
            H['_restore_root_bone_scale'](arm, bad)
            self.assertEqual(arm['RootBoneScale'], (1.0, 1.0, 1.0))

    def test_texture_assignments_name_donor_and_added_pngs(self):
        model = {
            'TextureDescriptors': [{'TextureIndex': 0}, {'TextureIndex': 1}],
            'AdditionalTextureDescriptors': [{'TextureFileName': 'new.png'}],
            'DesiredTextureAssignments': {'s0': 1, 's1': 2, 's2': 9},
        }
        files = H['_assigned_surface_texture_files'](model, {0: 'a.png', 1: 'b.png'})
        self.assertEqual(files, {'s0': 'b.png', 's1': 'new.png'})


class SkinTests(unittest.TestCase):
    """Two submeshes of 2 and 3 vertices; float skin data (24-byte records)."""
    VS = 24

    def model(self, **skin_extra):
        skin = {'QuantizeInfo': 0x40,
                'SK1s': [{'BoneIndex': 1, 'VertexCnt': 2, 'GplVertexArrValue': 0}],
                'SK2s': [{'BoneIndex1': 1, 'BoneIndex2': 2, 'VertexCnt': 2,
                          'GplVertexArrValue': 2 * self.VS, 'WeightData': enc([128, 128, 64, 192])}],
                'SKAccs': [{'BoneIndex': 3, 'VertexCnt': 1, 'GplDestArrValue': 0,
                            'DestIndexData': enc(struct.pack('>H', 4)), 'WeightData': enc([32])}]}
        skin.update(skin_extra)
        return {'Submeshes': [{'VertexBuffer': vb(2, 6)}, {'VertexBuffer': vb(3, 6)}], 'SkinData': skin}

    def test_unedited_skin_gives_no_override(self):
        self.assertIsNone(H['_edited_skin_influences'](self.model()))

    def test_inplace_weight_edit_and_trim(self):
        model = self.model()
        model['SkinData']['SK2s'][0]['WeightDataEdited'] = enc([255, 0])
        model['SkinData']['SK2s'][0]['VertexCntEdited'] = 1
        per_submesh, skinned = H['_edited_skin_influences'](model)
        self.assertEqual(skinned, {1, 2, 3})
        self.assertEqual(sorted(per_submesh[0]), [(0, 1, 1.0), (1, 1, 1.0)])
        # submesh 1: vertex 0 keeps the edited SK2 weight, vertex 1 was trimmed,
        # vertex 2 keeps its SKAcc contribution
        self.assertEqual(sorted(per_submesh[1]), [(0, 1, 255 / 256)] + [(2, 3, 32 / 256)])

    def test_inplace_sk1_bone_reassignment(self):
        model = self.model()
        model['SkinData']['SK1s'][0]['BoneIndexEdited'] = 2
        model['SkinData']['SK1s'][0]['BindPoseDataEdited'] = enc(b'')
        per_submesh, _skinned = H['_edited_skin_influences'](model)
        self.assertEqual(sorted(per_submesh[0]), [(0, 2, 1.0), (1, 2, 1.0)])

    def test_model_level_skin_edit_maps_local_indices(self):
        model = self.model()
        model['SkinDataEdited'] = {
            'QuantizeInfo': 0x40,
            'SK1s': [{'BoneIndex': 1, 'VertexCnt': 2, 'GplVertexArrValue': 0,
                      'VertexIndices': enc(struct.pack('>2H', 0, 1))}],
            'SK2s': [{'BoneIndex1': 1, 'BoneIndex2': 2, 'VertexCnt': 2, 'GplVertexArrValue': 2 * self.VS,
                      'VertexIndices': enc(struct.pack('>2H', 0, 1)), 'WeightData': enc([10, 20, 30, 40])}],
            'SKAccs': [{'BoneIndex': 3, 'VertexCnt': 1, 'DestIndexData': enc(struct.pack('>H', 2)),
                        'WeightData': enc([32])}],
        }
        per_submesh, _skinned = H['_edited_skin_influences'](model)
        self.assertEqual(sorted(per_submesh[0]), [(0, 1, 1.0), (1, 1, 1.0)])
        self.assertEqual(sorted(per_submesh[1]), [(0, 1, 10 / 256), (0, 2, 20 / 256),
                                                  (1, 1, 30 / 256), (1, 2, 40 / 256), (2, 3, 32 / 256)])

    def test_unplaceable_model_level_edit_falls_back(self):
        model = self.model()
        model['SkinDataEdited'] = {'SK1s': [{'BoneIndex': 9, 'VertexCnt': 1,
                                             'VertexIndices': enc(struct.pack('>H', 0))}]}
        self.assertIsNone(H['_edited_skin_influences'](model))
        model['SkinDataEdited'] = {'MembershipEdited': True}
        self.assertIsNone(H['_edited_skin_influences'](model))

    def test_edit_object_keeps_non_skinned_donor_influences(self):
        bone_list = [
            {'BoneId': 1, 'VertexInfluences': [{'SubmeshIndex': 0, 'Influences': enc(struct.pack('>Hf', 0, 1.0))}]},
            {'BoneId': 7, 'VertexInfluences': [{'SubmeshIndex': 0, 'Influences': enc(struct.pack('>Hf', 1, 1.0))}]},
        ]
        skin_edit = ({0: [(0, 1, 0.5)]}, {1})
        self.assertEqual(H['_edit_object_influences'](0, bone_list, skin_edit), [(1, 7, 1.0), (0, 1, 0.5)])
        self.assertIsNone(H['_edit_object_influences'](3, bone_list, skin_edit))
        self.assertIsNone(H['_edit_object_influences'](0, bone_list, None))


if __name__ == '__main__':
    unittest.main()
