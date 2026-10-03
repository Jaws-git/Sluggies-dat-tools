import json
import os
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest

import numpy as np

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import glb_export
from helper import Object, quaternion_rotation_matrix, scaling_matrix, translation_matrix


def _pose(translation, quaternion_wxyz=(1.0, 0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
    """An SRT-shaped pose: helper stores the quaternion as [-w, x, y, z] and builds a
    row-vector transform S @ R @ T."""
    w, x, y, z = quaternion_wxyz
    pose = Object()
    pose.translation = list(translation)
    pose.quaternion = [-w, x, y, z]
    pose.scale = list(scale)
    pose.transform = np.matmul(np.matmul(scaling_matrix(pose.scale), quaternion_rotation_matrix(pose.quaternion)),
                               translation_matrix(pose.translation))
    return pose


def _bone(bone_id, parent, relative, geo, skinned, pose, influences, by_id):
    bone = Object()
    bone.id, bone.parent, bone.relative = bone_id, parent, relative
    bone.GEOID, bone.skinned, bone.pose = geo, skinned, pose
    bone.influences = influences
    transform, p = pose.transform, parent
    while p != -1:  # model0.model_data's absolute_transform: every parent, row-vector order
        transform = np.matmul(transform, by_id[p].pose.transform)
        p = by_id[p].parent
    bone.absolute_transform = transform
    by_id[bone_id] = bone
    return bone


def _vertex(position, lighting=None, color=None, uv=None):
    v = Object()
    v.position = position
    if lighting is not None:
        v.lighting = lighting
    v.colors = {} if color is None else {0: color}
    v.tex_coords = {} if uv is None else {0: uv}
    return v


def _mesh(triangles, layers, descriptors):
    mesh = Object()
    mesh.triangles, mesh.texture_layers, mesh.active_descriptors = triangles, layers, descriptors
    return mesh


def _model_data(export_tex=True):
    by_id = {}
    half_turn_y = (np.cos(np.pi / 4), 0.0, np.sin(np.pi / 4), 0.0)
    bones = [
        _bone(0, -1, 0, 0, True, _pose((0, 1, 0)), {0: 256, 1: 128, 2: 256}, by_id),
        _bone(1, 0, 1, 0, True, _pose((0, 2, 0), half_turn_y, (2, 2, 2)), {1: 128, 3: 256}, by_id),
        # not relative to its parent: a root node in glTF
        _bone(2, 1, 0, 1, False, _pose((5, 0, 0)), {0: 256, 1: 256, 2: 256}, by_id),
    ]
    skinned = Object()
    skinned.positions = [np.array(p, dtype=float) for p in ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))]
    skinned.normals = [np.array((0.0, 0.0, 2.0))] * 4
    skinned.colors = [[1.0, 0.5, 0.0]] * 4
    skinned.tex_coords = [np.array([(0, 0), (1, 0), (1, 1), (0, 1)], dtype=float)]
    skinned.position_normals = None
    skinned.meshes = [_mesh([[_vertex(0, 0, 0, 0), _vertex(1, 1, 1, 1), _vertex(2, 2, 2, 2)],
                             [_vertex(0, 0, 0, 0), _vertex(2, 2, 2, 2), _vertex(3, 3, 3, 3)]],
                            {0: 0}, ['position', 'lighting', 'color0', 'texture0'])]
    rigid = Object()
    rigid.positions = [np.array(p, dtype=float) for p in ((0, 0, 0), (0, 0, 1), (0, 1, 1))]
    rigid.normals, rigid.colors, rigid.position_normals = [], [], None
    rigid.tex_coords = [np.array([(0, 0), (0, 1), (1, 1)], dtype=float)]
    rigid.meshes = [_mesh([[_vertex(0, uv=0), _vertex(1, uv=1), _vertex(2, uv=2)]],
                          {0: 1}, ['position', 'texture0'])]
    data = Object()
    data.geometries = [skinned, rigid]
    data.textures = {0: 'opaque.png', 1: 'cutout.png'}
    data.bones = bones
    data.model = Object()
    data.model.SKN = object()
    data.export_tex = export_tex
    return data


def _read(glb):
    magic, version, length = struct.unpack_from('<III', glb, 0)
    json_len, json_type = struct.unpack_from('<II', glb, 12)
    gl = json.loads(glb[20:20 + json_len])
    bin_len, bin_type = struct.unpack_from('<II', glb, 20 + json_len)
    binary = glb[28 + json_len:28 + json_len + bin_len]
    header = dict(magic=magic, version=version, length=length, json_len=json_len, json_type=json_type,
                  bin_len=bin_len, bin_type=bin_type, total=len(glb))

    def accessor(i):
        a = gl['accessors'][i]
        view = gl['bufferViews'][a['bufferView']]
        dtype = {5126: '<f4', 5123: '<u2', 5125: '<u4'}[a['componentType']]
        width = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}[a['type']]
        return np.frombuffer(binary, dtype, a['count'] * width, view['byteOffset']).reshape(a['count'], width)
    return header, gl, accessor


class GlbExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tex_dir = os.path.join(self.tmp.name, 'tex')
        os.makedirs(tex_dir)
        from PIL import Image
        Image.new('RGBA', (4, 4), (255, 0, 0, 255)).save(os.path.join(tex_dir, 'opaque.png'))
        Image.new('RGBA', (4, 4), (0, 255, 0, 0)).save(os.path.join(tex_dir, 'cutout.png'))
        self.data = _model_data()
        path = glb_export.write_glb(self.data, self.tmp.name, 'model')
        with open(path, 'rb') as f:
            self.header, self.gl, self.accessor = _read(f.read())
        self.node_by_name = {n['name']: i for i, n in enumerate(self.gl['nodes'])}

    def test_container_is_valid_glb(self):
        h = self.header
        self.assertEqual((h['magic'], h['version'], h['length']), (0x46546C67, 2, h['total']))
        self.assertEqual((h['json_type'], h['bin_type']), (0x4E4F534A, 0x004E4942))
        self.assertEqual(h['json_len'] % 4, 0)
        self.assertEqual(h['bin_len'] % 4, 0)
        for view in self.gl['bufferViews']:
            self.assertEqual(view['byteOffset'] % 4, 0)
            self.assertLessEqual(view['byteOffset'] + view['byteLength'], h['bin_len'])

    def test_indices_stay_in_range(self):
        for mesh in self.gl['meshes']:
            for prim in mesh['primitives']:
                count = self.gl['accessors'][prim['attributes']['POSITION']]['count']
                for attr in prim['attributes'].values():
                    self.assertEqual(self.gl['accessors'][attr]['count'], count)
                self.assertLess(self.accessor(prim['indices']).max(), count)

    def test_triangle_corners_keep_their_order(self):
        prim = self.gl['meshes'][0]['primitives'][0]
        positions = self.accessor(prim['attributes']['POSITION'])
        corners = [tuple(positions[i]) for i in self.accessor(prim['indices']).reshape(-1)]
        expected = [tuple(self.data.geometries[0].positions[p]) for p in (0, 1, 2, 0, 2, 3)]
        self.assertEqual(corners, expected)

    def test_bone_world_matrices_match_absolute_transform(self):
        _, _, _, _, world = glb_export._bone_nodes(self.data.bones)
        for bone in self.data.bones:
            if bone.relative:
                np.testing.assert_allclose(world[bone.id], bone.absolute_transform.T, atol=1e-12)

    def test_hierarchy(self):
        nodes, scene = self.gl['nodes'], set(self.gl['scenes'][0]['nodes'])
        self.assertIn(self.node_by_name['bone_0'], scene)
        self.assertIn(self.node_by_name['bone_2'], scene)  # not relative: a root
        self.assertEqual(nodes[self.node_by_name['bone_0']].get('children'), [self.node_by_name['bone_1']])
        self.assertIn(self.node_by_name['submesh1'], nodes[self.node_by_name['bone_2']]['children'])
        self.assertIn(self.node_by_name['submesh0'], scene)
        self.assertEqual(nodes[self.node_by_name['submesh0']]['skin'], 0)
        self.assertNotIn('skin', nodes[self.node_by_name['submesh1']])

    def test_inverse_bind_matrices_undo_world(self):
        skin = self.gl['skins'][0]
        ibm = self.accessor(skin['inverseBindMatrices'])
        _, _, _, _, world = glb_export._bone_nodes(self.data.bones)
        joint_bone = {self.node_by_name[f'bone_{b}']: b for b in world}
        for joint, flat in zip(skin['joints'], ibm):
            np.testing.assert_allclose(flat.reshape(4, 4).T @ world[joint_bone[joint]], np.identity(4), atol=1e-5)

    def test_weights_are_normalised(self):
        prim = self.gl['meshes'][0]['primitives'][0]
        weights = self.accessor(prim['attributes']['WEIGHTS_0'])
        joints = self.accessor(prim['attributes']['JOINTS_0'])
        np.testing.assert_allclose(weights.sum(1), 1.0, atol=1e-6)
        positions = self.accessor(prim['attributes']['POSITION'])
        shared = [i for i, p in enumerate(positions) if tuple(p) == (1, 0, 0)][0]
        self.assertEqual(sorted(joints[shared][:2].tolist()),
                         sorted([self.node_by_name['bone_0'], self.node_by_name['bone_1']]))
        np.testing.assert_allclose(weights[shared][:2], [0.5, 0.5])

    def test_normals_are_unit_length(self):
        prim = self.gl['meshes'][0]['primitives'][0]
        np.testing.assert_allclose(np.linalg.norm(self.accessor(prim['attributes']['NORMAL']), axis=1), 1.0, atol=1e-6)
        self.assertNotIn('NORMAL', self.gl['meshes'][1]['primitives'][0]['attributes'])

    def test_textures_are_referenced_and_alpha_masks(self):
        self.assertEqual(sorted(i['uri'] for i in self.gl['images']), ['tex/cutout.png', 'tex/opaque.png'])
        materials = {m['name']: m for m in self.gl['materials']}
        self.assertNotIn('alphaMode', materials['tex0'])
        self.assertEqual(materials['tex1']['alphaMode'], 'MASK')
        self.assertNotIn('bufferView', self.gl['images'][0])

    def test_vertex_colour_is_a_custom_attribute(self):
        attrs = self.gl['meshes'][0]['primitives'][0]['attributes']
        self.assertIn('_COLOR0', attrs)
        self.assertNotIn('COLOR_0', attrs)
        np.testing.assert_allclose(self.accessor(attrs['_COLOR0'])[0], [1.0, 0.5, 0.0, 1.0])

    def test_notex_writes_no_images_or_materials(self):
        _, gl, _ = _read(glb_export.model_data_to_glb(_model_data(export_tex=False)))
        for key in ('images', 'textures', 'materials'):
            self.assertNotIn(key, gl)
        for mesh in gl['meshes']:
            for prim in mesh['primitives']:
                self.assertNotIn('material', prim)

    def test_bones_without_skn_still_get_a_skin(self):
        # Blender builds an armature only from skin joints (stadium and prop bones).
        data = _model_data()
        data.model.SKN = None
        _, gl, _ = _read(glb_export.model_data_to_glb(data))
        self.assertEqual(len(gl['skins'][0]['joints']), len(data.bones))
        for node in gl['nodes']:
            self.assertNotIn('skin', node)
        for mesh in gl['meshes']:
            for prim in mesh['primitives']:
                self.assertNotIn('JOINTS_0', prim['attributes'])

    def test_partner_textures_are_referenced_in_the_partner_folder(self):
        lod_dir = os.path.join(self.tmp.name, '2_L_model')
        os.makedirs(lod_dir)
        shutil.copytree(os.path.join(self.tmp.name, 'tex'), os.path.join(self.tmp.name, '1_model', 'tex'))
        data = _model_data()
        data.textures = {}  # an L_ model has no TEX section
        partner = ('1_model', {0: 'opaque.png', 1: 'cutout.png'})
        path = glb_export.write_glb(data, lod_dir, 'L_model', partner=partner)
        with open(path, 'rb') as f:
            _, gl, _ = _read(f.read())
        prefix = f'../{partner[0]}/tex/'
        self.assertEqual(sorted(i['uri'] for i in gl['images']), [prefix + 'cutout.png', prefix + 'opaque.png'])
        self.assertEqual({m['name']: m.get('alphaMode') for m in gl['materials']}, {'tex0': None, 'tex1': 'MASK'})

    def test_model_without_bones_is_a_plain_scene(self):
        data = _model_data()
        data.bones, data.model.SKN = [], None
        _, gl, _ = _read(glb_export.model_data_to_glb(data))
        self.assertNotIn('skins', gl)
        self.assertEqual([gl['nodes'][i]['name'] for i in gl['scenes'][0]['nodes']], ['submesh0', 'submesh1'])


if __name__ == '__main__':
    unittest.main()
