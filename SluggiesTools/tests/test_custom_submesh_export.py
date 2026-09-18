"""PLAN_AddSubmesh.md Phase 6 step 2: custom submesh export encoding.

The math and encoding live in the bpy-free BlenderAddonSrc/CustomSubmeshExport.py
and are imported directly; the thin bpy glue in ExportSluggies.py is checked
structurally with the AST.
"""

import ast
import base64
import json
import math
import pathlib
import struct
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
BLENDER_ADDON_DIR = ROOT / 'BlenderAddonSrc'
EXPORTER_PATH = BLENDER_ADDON_DIR / 'ExportSluggies.py'
REAL_MARIO_SLUGGIE = (
    ROOT / '2_Output_Models' / '18 Mario' / '78277664_mario.gpl' / '78277664_mario.gpl.sluggie'
)
if str(BLENDER_ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_ADDON_DIR))

import CustomSubmeshExport as cse  # noqa: E402


def _exporter_function(name):
    tree = ast.parse(EXPORTER_PATH.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f'{name} not found in ExportSluggies.py')


def _load_exporter_function(name):
    function = _exporter_function(name)
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(EXPORTER_PATH), 'exec'), namespace)
    return namespace[name]


def _attribute_names(node):
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _loop_tri(vertices, loops, polygon_index):
    return SimpleNamespace(vertices=vertices, loops=loops, polygon_index=polygon_index)


class TriangulateTests(unittest.TestCase):
    def test_loop_triangles_become_triangles(self):
        # A quad (polygon 0, loops 0-3) split by Blender into two triangles.
        tris = cse.triangles_from_loop_triangles([
            _loop_tri([0, 1, 2], [0, 1, 2], 0),
            _loop_tri([0, 2, 3], [0, 2, 3], 0),
        ])
        self.assertEqual(tris, [
            cse.Triangle((0, 1, 2), (0, 1, 2), 0),
            cse.Triangle((0, 2, 3), (0, 2, 3), 0),
        ])

    def test_winding_is_kept(self):
        tri = cse.triangles_from_loop_triangles([_loop_tri([2, 1, 0], [5, 4, 3], 1)])[0]
        self.assertEqual(tri.vertices, (2, 1, 0))
        self.assertEqual(tri.loops, (5, 4, 3))

    def test_compact_drops_unreferenced_vertices(self):
        tris = [cse.Triangle((4, 2, 7), (0, 1, 2), 0), cse.Triangle((7, 2, 9), (3, 4, 5), 1)]
        used, faces = cse.compact_vertex_indices(tris)
        self.assertEqual(used, [2, 4, 7, 9])
        self.assertEqual(faces, [(1, 0, 2), (2, 0, 3)])

    def test_compact_empty(self):
        self.assertEqual(cse.compact_vertex_indices([]), ([], []))

    def test_exporter_glue_reads_loop_triangles_without_operators(self):
        fn = _exporter_function('_custom_submesh_triangles')
        names = _attribute_names(fn)
        self.assertIn('calc_loop_triangles', names)
        self.assertIn('loop_triangles', names)
        self.assertNotIn('ops', names)


class CustomSubmeshExportGuardTests(unittest.TestCase):
    def test_message_requires_both_export_options_and_names_objects(self):
        message = _load_exporter_function(
            '_custom_submesh_export_toggles_required_message'
        )(['CustomHat', 'CustomCape'])

        self.assertIn('Hammerspace Mode', message)
        self.assertIn('Reimport textures', message)
        self.assertIn('CustomHat, CustomCape', message)

    def test_execute_cancels_selected_custom_submeshes_when_either_option_is_off(self):
        tree = ast.parse(EXPORTER_PATH.read_text(encoding='utf-8'))
        export_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == 'SLUGGIES_OT_export'
        )
        execute = next(
            node for node in export_class.body
            if isinstance(node, ast.FunctionDef) and node.name == 'execute'
        )
        guard = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.If)
            and 'custom_submesh_candidates' in ast.unparse(node.test)
            and 'self.use_hammerspace and self.reimport_textures' in ast.unparse(node.test)
        )
        guard_body = ast.unparse(ast.Module(body=guard.body, type_ignores=[]))
        self.assertIn('_custom_submesh_export_toggles_required_message', guard_body)
        self.assertIn("return {'CANCELLED'}", guard_body)


def _execute_source():
    tree = ast.parse(EXPORTER_PATH.read_text(encoding='utf-8'))
    export_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'SLUGGIES_OT_export'
    )
    return ast.unparse(next(
        node for node in export_class.body
        if isinstance(node, ast.FunctionDef) and node.name == 'execute'
    ))


class _FakeMaterial:
    def __init__(self, name, surface_id):
        self.name = name
        self._props = {'SurfaceId': surface_id}

    def get(self, key, default=None):
        return self._props.get(key, default)


class _FakeCustomObject:
    def __init__(self, custom_submesh_id, materials):
        self._props = {'CustomSubmeshId': custom_submesh_id}
        self.material_slots = [SimpleNamespace(material=m) for m in materials]

    def get(self, key, default=None):
        return self._props.get(key, default)


class SkinnedDonorObjectsTests(unittest.TestCase):
    """A custom-submesh-only export must not touch the model's skinning.

    Regression: exporting with no skinned mesh selected purged SkinDataEdited
    and rewrote it with empty SK1/SK2/SKAcc lists, which the patcher then
    rejected with "position-only SKN edit changed SK1s count from 3 to 0"
    (Gesso, 2026-09-18).
    """

    DATA = {'SluggiesModel': {'Submeshes': [
        {'VertexBuffer': {'VertexBufferCompCount': 6, 'VertexBufferOffset': '0x26be3460'}},
        {'VertexBuffer': {'VertexBufferCompCount': 3, 'VertexBufferOffset': '0x26be4000'}},
    ]}}

    def setUp(self):
        self.fn = _load_exporter_function('skinned_donor_objects')

    def test_skinned_donor_object_is_matched_by_vertex_buffer_offset(self):
        obj = {'VertexBufferOffset': '0x26be3460'}
        self.assertEqual(self.fn([obj], self.DATA), {id(obj): (0, obj)})

    def test_rigid_donor_object_is_not_a_skinned_one(self):
        self.assertEqual(self.fn([{'VertexBufferOffset': '0x26be4000'}], self.DATA), {})

    def test_custom_submesh_objects_never_match(self):
        # Custom submeshes carry no VertexBufferOffset -- they are new
        # submeshes, not edits to a donor one.
        self.assertEqual(self.fn([{'SluggiesCustomSubmesh': True}], self.DATA), {})

    def test_no_candidates_at_all(self):
        self.assertEqual(self.fn([], self.DATA), {})

    def test_empty_skin_data_edited_is_recognised_only_against_a_skinned_donor(self):
        fn = _load_exporter_function('_skin_data_edited_is_empty')
        donor = {'SK1s': [{'BoneIndex': 0}], 'SK2s': [], 'SKAccs': []}
        empty = {'QuantizeInfo': 8, 'SK1s': [], 'SK2s': [], 'SKAccs': []}
        self.assertTrue(fn({'SluggiesModel': {'SkinData': donor, 'SkinDataEdited': empty}}))
        # A real edit always keeps at least one entry.
        self.assertFalse(fn({'SluggiesModel': {'SkinData': donor, 'SkinDataEdited': donor}}))
        # Nothing to heal without both halves, or on an unskinned model.
        self.assertFalse(fn({'SluggiesModel': {'SkinData': donor}}))
        self.assertFalse(fn({'SluggiesModel': {'SkinDataEdited': empty}}))
        self.assertFalse(fn({'SluggiesModel': {
            'SkinData': {'SK1s': [], 'SK2s': [], 'SKAccs': []}, 'SkinDataEdited': empty,
        }}))

    def test_an_empty_skin_data_edited_is_dropped_on_a_custom_submesh_only_export(self):
        source = _execute_source()
        self.assertIn('if _skin_data_edited_is_empty(data):', source)
        self.assertLess(
            source.index('if _skin_data_edited_is_empty(data):'),
            source.rindex('_purge_skn_edited(data)'),
        )

    def test_skin_encode_and_purge_only_run_when_a_skinned_mesh_is_present(self):
        source = _execute_source()
        guard = 'if skinned_donor_objects(candidates, data):'
        self.assertIn(guard, source)
        for call in ('_purge_skn_edited(data)', 'encode_skin_hammerspace(',
                     'encode_skin_weights_inplace('):
            self.assertLess(source.index(guard), source.index(call), call)

    def test_hammerspace_encoder_returns_early_without_a_skinned_object(self):
        tree = ast.parse(EXPORTER_PATH.read_text(encoding='utf-8'))
        encoder = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == 'encode_skin_hammerspace'
        )
        # The early return must come before anything writes SkinDataEdited.
        source = ast.unparse(encoder)
        write = "data['SluggiesModel']['SkinDataEdited'] ="
        self.assertIn(write, source)
        self.assertLess(source.index('if not obj_to_sub'), source.index(write))


class ExportExecuteWiringTests(unittest.TestCase):
    """Phase 6 step 2: SLUGGIES_OT_export.execute writes CustomSubmeshes."""

    def test_custom_submesh_material_picks_its_own_surface(self):
        fn = _load_exporter_function('_custom_submesh_material')
        own = _FakeMaterial('Hat_mat', 'custom1_ds0')
        obj = _FakeCustomObject('custom1', [None, _FakeMaterial('donor', 'sm0_ds5'), own])
        self.assertIs(fn(obj), own)
        self.assertIsNone(fn(_FakeCustomObject('custom1', [_FakeMaterial('other', 'custom10_ds0x')])))

    def test_merge_keeps_donor_order_and_appends_each_png_once(self):
        fn = _load_exporter_function('_merge_texture_additions')
        donor = [{'TextureFileName': 'body_new.png', 'TemplateTextureIndex': 0}]
        custom = [
            {'TextureFileName': 'Hat.png', 'TemplateTextureIndex': 0},
            {'TextureFileName': 'body_new.png', 'TemplateTextureIndex': 2},
            {'TextureFileName': 'Hat.png', 'TemplateTextureIndex': 0},
        ]
        merged = fn(donor, custom)
        self.assertEqual([a['TextureFileName'] for a in merged], ['body_new.png', 'Hat.png'])
        self.assertIs(merged[0], donor[0])
        self.assertEqual(len(donor), 1)  # input not mutated

    def test_execute_encodes_each_selected_custom_submesh(self):
        source = _execute_source()
        for call in ('_custom_submesh_material(obj)', '_custom_submesh_template_texture_index(',
                     '_resolve_custom_submesh_texture_changes(', 'encode_custom_submesh('):
            self.assertIn(call, source)
        # Texture assignments are resolved before any entry is encoded.
        self.assertLess(source.index('_resolve_custom_submesh_texture_changes('),
                        source.index('encode_custom_submesh('))
        # ... and only after the Hammerspace/Reimport toggle guard.
        self.assertLess(source.index('_custom_submesh_export_toggles_required_message'),
                        source.index('encode_custom_submesh('))

    def test_execute_writes_merges_and_counts_custom_submeshes(self):
        source = _execute_source()
        self.assertIn("data['SluggiesModel']['CustomSubmeshes'] = custom_submesh_entries", source)
        self.assertIn("data['SluggiesModel'].pop('CustomSubmeshes', None)", source)
        self.assertIn('written += len(custom_submesh_entries)', source)
        self.assertLess(source.index('written += len(custom_submesh_entries)'),
                        source.index('if written == 0'))
        self.assertLess(source.index('_merge_texture_additions(additions, custom_additions)'),
                        source.index("data['SluggiesModel']['AdditionalTextureDescriptors'] = additions"))

    def test_execute_copies_external_textures_only_after_validation(self):
        source = _execute_source()
        copy_index = source.index('shutil.copyfile(source_path, target_path)')
        # Every cancel path that can still fire comes before the copy ...
        self.assertLess(source.index('if written == 0'), copy_index)
        self.assertLess(source.index('encode_skin_hammerspace('), copy_index)
        # ... and the .sluggie that names the copied files is written after it.
        self.assertLess(copy_index, source.index('json.dump(data, f, indent=2)'))


def _translation(x, y, z):
    return [[1.0, 0.0, 0.0, x], [0.0, 1.0, 0.0, y], [0.0, 0.0, 1.0, z], [0.0, 0.0, 0.0, 1.0]]


def _scale(x, y, z):
    return [[x, 0.0, 0.0, 0.0], [0.0, y, 0.0, 0.0], [0.0, 0.0, z, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _rot_x(angle):
    c, s = math.cos(angle), math.sin(angle)
    return [[1.0, 0.0, 0.0, 0.0], [0.0, c, -s, 0.0], [0.0, s, c, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _rot_z(angle):
    c, s = math.cos(angle), math.sin(angle)
    return [[c, -s, 0.0, 0.0], [s, c, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _mul(*matrices):
    result = [list(row) for row in cse.IDENTITY4]
    for m in matrices:
        result = cse.mat4_mul(result, m)
    return result


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def _f32_matrix(m):
    return [[_f32(v) for v in row] for row in m]


def _face_normal(a, b, c):
    e1 = [b[i] - a[i] for i in range(3)]
    e2 = [c[i] - a[i] for i in range(3)]
    return (e1[1] * e2[2] - e1[2] * e2[1],
            e1[2] * e2[0] - e1[0] * e2[2],
            e1[0] * e2[1] - e1[1] * e2[0])


# Importer: armature object rotated 90 degrees about X (game Y-up to Blender Z-up).
ARMATURE_WORLD = _rot_x(math.pi / 2)
HOST_BIND = _mul(_translation(0.2, 1.3, -0.1), _rot_z(0.4))

# A cube centred on its origin: 8 corners, 12 triangles wound counter-clockwise
# seen from outside.
CUBE_COS = [(-0.1, -0.1, -0.1), (0.1, -0.1, -0.1), (0.1, 0.1, -0.1), (-0.1, 0.1, -0.1),
            (-0.1, -0.1, 0.1), (0.1, -0.1, 0.1), (0.1, 0.1, 0.1), (-0.1, 0.1, 0.1)]
CUBE_FACES = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
              (2, 3, 7), (2, 7, 6), (1, 2, 6), (1, 6, 5), (0, 4, 7), (0, 7, 3)]


def _cube_triangles():
    return [cse.Triangle(face, tuple(3 * i + k for k in range(3)), i)
            for i, face in enumerate(CUBE_FACES)]


def _assert_points_close(test, a, b, places=9):
    test.assertEqual(len(a), len(b))
    for pa, pb in zip(a, b):
        for va, vb in zip(pa, pb):
            test.assertAlmostEqual(va, vb, places=places)


class MatrixHelperTests(unittest.TestCase):
    def test_inverse_round_trips(self):
        m = _mul(_translation(1, 2, 3), _rot_x(0.3), _rot_z(-1.1), _scale(2.0, 0.5, -3.0))
        product = cse.mat4_mul(m, cse.mat4_inverse(m))
        for r in range(4):
            for c in range(4):
                self.assertAlmostEqual(product[r][c], 1.0 if r == c else 0.0, places=12)

    def test_singular_matrix_rejected(self):
        with self.assertRaises(ValueError):
            cse.mat4_inverse(_scale(1.0, 0.0, 1.0))

    def test_bone_absolute_matrices_resolve_parent_before_child(self):
        # Child listed first: the parent must still be resolved first.
        bones = [
            {'BoneId': 1, 'ParentBoneId': 0, 'Translation': [0.0, 1.0, 0.0],
             'Scale': [1.0, 1.0, 1.0], 'Quaternion': [-1.0, 0.0, 0.0, 0.0]},
            {'BoneId': 0, 'ParentBoneId': None, 'Translation': [2.0, 0.0, 0.0],
             'Scale': [2.0, 2.0, 2.0], 'Quaternion': [-1.0, 0.0, 0.0, 0.0]},
        ]
        absolute = cse.bone_absolute_matrices(bones)
        self.assertEqual(cse.transform_point(absolute[1], (0.0, 0.0, 0.0)), (2.0, 2.0, 0.0))

    def test_quaternion_is_stored_with_negated_w(self):
        # Stored [-qw, qx, qy, qz]: 90 degrees about Z is w=cos45, z=sin45.
        half = math.sqrt(0.5)
        bone = {'Translation': [0.0, 0.0, 0.0], 'Scale': [1.0, 1.0, 1.0],
                'Quaternion': [-half, 0.0, 0.0, half]}
        _assert_points_close(
            self, [cse.transform_point(cse.bone_local_matrix(bone), (1.0, 0.0, 0.0))],
            [(0.0, 1.0, 0.0)],
        )


class KeepOffsetWorldMatrixTests(unittest.TestCase):
    """PLAN_EditRigidMeshes.md Phase 6 step 2: Reassign to new bone's
    'Keep offset to bone' placement math."""

    def _bind(self, tx, ty, tz, angle=0.0):
        return _mul(_translation(tx, ty, tz), _rot_z(angle))

    def test_moves_object_by_the_difference_between_bind_matrices(self):
        b_old = self._bind(0.2, 1.3, -0.1, 0.4)
        b_new = self._bind(-0.5, 0.6, 0.9, -0.2)
        obj_world = _mul(ARMATURE_WORLD, b_old, _translation(0.03, -0.01, 0.02))

        new_world = cse.keep_offset_world_matrix(obj_world, ARMATURE_WORLD, b_old, b_new)

        # The mesh keeps the same local offset to its host bone: re-deriving
        # that offset from the new bind matrix must reproduce it exactly.
        offset = _mul(cse.mat4_inverse(b_old), cse.mat4_inverse(ARMATURE_WORLD), obj_world)
        expected = _mul(ARMATURE_WORLD, b_new, offset)
        for r in range(4):
            for c in range(4):
                self.assertAlmostEqual(new_world[r][c], expected[r][c], places=9)

    def test_same_bone_is_a_no_op(self):
        b = self._bind(0.2, 1.3, -0.1, 0.4)
        obj_world = _mul(ARMATURE_WORLD, b, _translation(0.03, -0.01, 0.02))
        new_world = cse.keep_offset_world_matrix(obj_world, ARMATURE_WORLD, b, b)
        for r in range(4):
            for c in range(4):
                self.assertAlmostEqual(new_world[r][c], obj_world[r][c], places=9)

    def test_round_trip_back_to_the_original_bone_is_the_identity_move(self):
        b_old = self._bind(0.2, 1.3, -0.1, 0.4)
        b_new = self._bind(-0.5, 0.6, 0.9, -0.2)
        obj_world = _mul(ARMATURE_WORLD, b_old, _translation(0.03, -0.01, 0.02))

        moved = cse.keep_offset_world_matrix(obj_world, ARMATURE_WORLD, b_old, b_new)
        back = cse.keep_offset_world_matrix(moved, ARMATURE_WORLD, b_new, b_old)

        for r in range(4):
            for c in range(4):
                self.assertAlmostEqual(back[r][c], obj_world[r][c], places=9)


class WorldPositionTests(unittest.TestCase):
    """PLAN_AddSubmesh.md Phase 6 step 2 "Pure-math transform tests"."""

    def _geometry(self, cos, obj_world, arm_world=ARMATURE_WORLD):
        return cse.bone_local_geometry(cos, _cube_triangles(), obj_world, arm_world, HOST_BIND)

    def test_object_mode_edit_mode_and_mixed_moves_agree(self):
        placement = _mul(ARMATURE_WORLD, HOST_BIND, _translation(0.05, -0.02, 0.3))
        world_cos = [cse.transform_point(placement, co) for co in CUBE_COS]

        # Object Mode only: mesh data untouched, the object carries the move.
        a = self._geometry(CUBE_COS, placement)

        # Edit Mode only: object left at the armature's transform, vertices moved.
        inv_b = cse.mat4_inverse(ARMATURE_WORLD)
        b = self._geometry([cse.transform_point(inv_b, p) for p in world_cos], ARMATURE_WORLD)

        # Both: object moved, rotated and non-uniformly scaled; vertices edited to match.
        obj_world_c = _mul(_translation(0.4, -0.3, 0.25), _rot_z(0.7), _scale(1.5, 0.5, 2.0))
        inv_c = cse.mat4_inverse(obj_world_c)
        c = self._geometry([cse.transform_point(inv_c, p) for p in world_cos], obj_world_c)

        expected = [cse.transform_point(_translation(0.05, -0.02, 0.3), co) for co in CUBE_COS]
        _assert_points_close(self, a.positions, expected)
        _assert_points_close(self, b.positions, expected)
        _assert_points_close(self, c.positions, expected)

    def test_moving_the_whole_armature_does_not_change_bone_positions(self):
        obj_world = _mul(ARMATURE_WORLD, HOST_BIND, _translation(0.1, 0.0, 0.0))
        moved = _mul(_translation(5.0, -3.0, 2.0), _rot_z(1.2))
        a = self._geometry(CUBE_COS, obj_world)
        b = self._geometry(CUBE_COS, _mul(moved, obj_world), arm_world=_mul(moved, ARMATURE_WORLD))
        _assert_points_close(self, a.positions, b.positions)

    def test_rotation_and_non_uniform_scale_keep_normals_unit_and_perpendicular(self):
        obj_world = _mul(ARMATURE_WORLD, _translation(0.3, 0.1, 0.0), _rot_x(0.9), _scale(3.0, 0.25, 1.5))
        geometry = self._geometry(CUBE_COS, obj_world)
        self.assertFalse(geometry.mirrored)
        for tri in geometry.triangles:
            local_normal = _face_normal(*(CUBE_COS[v] for v in tri.vertices))
            (normal,) = cse.transform_normals([local_normal], geometry.to_bone)
            self.assertAlmostEqual(sum(v * v for v in normal), 1.0, places=9)
            pa, pb, pc = (cse.transform_point(geometry.to_bone, CUBE_COS[v]) for v in tri.vertices)
            for tip in (pb, pc):
                edge = [tip[i] - pa[i] for i in range(3)]
                self.assertAlmostEqual(sum(edge[i] * normal[i] for i in range(3)), 0.0, places=9)
            # ... and still points out of the face the way the geometry winds.
            self.assertGreater(sum(a * b for a, b in zip(normal, _face_normal(pa, pb, pc))), 0.0)

    def test_negative_scale_reverses_winding(self):
        plain = self._geometry(CUBE_COS, _mul(ARMATURE_WORLD, HOST_BIND))
        mirrored = self._geometry(CUBE_COS, _mul(ARMATURE_WORLD, HOST_BIND, _scale(-1.0, 1.0, 1.0)))
        self.assertFalse(plain.mirrored)
        self.assertTrue(mirrored.mirrored)
        for tri_plain, tri_mirrored in zip(plain.triangles, mirrored.triangles):
            self.assertEqual(tri_mirrored.vertices, tri_plain.vertices[::-1])
            self.assertEqual(tri_mirrored.loops, tri_plain.loops[::-1])
        # Every exported face still points away from the cube centre (the origin).
        for face in mirrored.faces:
            a, b, c = (mirrored.positions[v] for v in face)
            centre = [(a[i] + b[i] + c[i]) / 3 for i in range(3)]
            self.assertGreater(sum(n * p for n, p in zip(_face_normal(a, b, c), centre)), 0.0)

    def test_zero_scale_is_rejected(self):
        with self.assertRaises(ValueError):
            self._geometry(CUBE_COS, _mul(ARMATURE_WORLD, _scale(1.0, 0.0, 1.0)))

    def test_unsupported_modifiers_named(self):
        names = cse.unsupported_modifier_names(
            [('Armature', 'ARMATURE'), ('Mirror', 'MIRROR'), ('Subdivision', 'SUBSURF')]
        )
        self.assertEqual(names, ['Mirror', 'Subdivision'])


class WorldPositionGlueTests(unittest.TestCase):
    def test_edit_mode_is_flushed_without_operators(self):
        names = _attribute_names(_exporter_function('_custom_submesh_bone_local_geometry'))
        self.assertIn('update_from_editmode', names)
        self.assertNotIn('ops', names)  # no mode_set, no transform_apply

    def test_reads_undeformed_vertices_not_the_evaluated_mesh(self):
        names = _attribute_names(_exporter_function('_custom_submesh_bone_local_geometry'))
        self.assertIn('vertices', names)
        self.assertNotIn('evaluated_get', names)
        self.assertNotIn('to_mesh', names)

    def test_posed_armature_is_read_in_rest_pose_and_restored(self):
        fn = _exporter_function('_armature_rest_pose')
        source = ast.unparse(fn)
        self.assertIn("pose_position = 'REST'", source)
        self.assertIn('pose_position = previous', source)
        self.assertTrue(any(isinstance(n, ast.Try) and n.finalbody for n in ast.walk(fn)))


def _positions_geometry(positions, source_vertices=None):
    return cse.BoneLocalGeometry(
        positions=positions,
        source_vertices=source_vertices or list(range(len(positions))),
        faces=[], triangles=[], to_bone=[list(row) for row in cse.IDENTITY4], mirrored=False,
    )


class PositionRangeTests(unittest.TestCase):
    """Step 2.3: range check on bone-local positions (QuantizeInfo 59, +/-16)."""

    def test_in_range_positions_pass(self):
        low, high = cse.int16_range(59)
        self.assertEqual((low, high), (-16.0, 32767 / 2048))
        cse.check_position_range('Cube', 7, _positions_geometry([(0.0, 1.0, -2.0), (high, low, 0.0)]))

    def test_value_that_rounds_into_range_passes(self):
        # 32767.4 / 2048 rounds to 32767, so it is still representable.
        cse.check_position_range('Cube', 7, _positions_geometry([(32767.4 / 2048, 0.0, 0.0)]))

    def test_error_names_object_worst_vertex_distance_and_advice(self):
        geometry = _positions_geometry(
            [(0.0, 0.0, 0.0), (17.0, 0.0, 0.0), (0.0, -30.0, 1.0), (16.5, 0.0, 0.0)],
            source_vertices=[10, 11, 12, 13],
        )
        with self.assertRaises(ValueError) as caught:
            cse.check_position_range('Hat', 42, geometry)
        message = str(caught.exception)
        self.assertIn('Hat', message)
        self.assertIn('vertex 12', message)  # Blender index of the worst overshoot
        self.assertIn('(0.0000, -30.0000, 1.0000)', message)
        self.assertIn(f'{math.sqrt(901):.4f} units from host bone 42', message)
        self.assertIn('-16..15.9995', message)
        self.assertIn('closer to its host bone', message)
        self.assertIn('host bone nearer', message)

    def test_non_finite_position_is_worst(self):
        geometry = _positions_geometry([(100.0, 0.0, 0.0), (math.nan, 0.0, 0.0)])
        with self.assertRaises(ValueError) as caught:
            cse.check_position_range('Cube', 1, geometry)
        self.assertIn('vertex 1 ', str(caught.exception))

    def test_float_format_rejected(self):
        with self.assertRaises(ValueError):
            cse.int16_divisor(0x40)


class AdaptivePositionFormatTests(unittest.TestCase):
    """Step 2.3 (adaptive): a mesh too far from its host bone for the canonical
    +/-16 steps down to a coarser s16 shift instead of being rejected."""

    def test_candidates_are_s16_from_finest_to_coarsest(self):
        candidates = cse.POSITION_QUANTIZE_CANDIDATES
        self.assertEqual(candidates[0], cse.POSITION_FORMAT[1])
        self.assertEqual(candidates[0], 59)
        self.assertEqual(candidates[-1], 48)
        self.assertEqual(list(candidates), sorted(candidates, reverse=True))
        for quantize_info in candidates:
            self.assertEqual(quantize_info >> 4, 3, f'{quantize_info} is not an s16 format')

    def test_mesh_inside_the_canonical_range_keeps_59(self):
        geometry = _positions_geometry([(0.0, 1.0, -2.0), (15.9, -16.0, 0.0)])
        self.assertEqual(cse.select_position_format('Cube', 7, geometry), (3, 59))

    def test_a_far_mesh_steps_down_only_as_far_as_it_must(self):
        for half_extent, expected in ((17.0, 58), (40.0, 57), (100.0, 56), (5000.0, 50)):
            with self.subTest(half_extent=half_extent):
                geometry = _positions_geometry([(0.0, 0.0, 0.0), (half_extent, 0.0, 0.0)])
                comp_count, quantize_info = cse.select_position_format('Gesso', 3, geometry)
                self.assertEqual(comp_count, 3)
                self.assertEqual(quantize_info, expected)
                low, high = cse.int16_range(quantize_info)
                self.assertLessEqual(half_extent, high)
                # One step finer would not have held it.
                finer_low, finer_high = cse.int16_range(quantize_info + 1)
                self.assertGreater(half_extent, finer_high)

    def test_coarsest_candidate_reaches_32767(self):
        geometry = _positions_geometry([(0.0, 0.0, 0.0), (-32768.0, 32767.0, 0.0)])
        self.assertEqual(cse.select_position_format('Gesso', 3, geometry), (3, 48))

    def test_beyond_every_candidate_names_the_worst_vertex_and_says_it_is_the_widest(self):
        geometry = _positions_geometry(
            [(0.0, 0.0, 0.0), (40000.0, 0.0, 0.0)], source_vertices=[4, 5],
        )
        with self.assertRaises(ValueError) as caught:
            cse.select_position_format('Gesso', 3, geometry)
        message = str(caught.exception)
        self.assertIn('Gesso', message)
        self.assertIn('vertex 5', message)
        self.assertIn('host bone 3', message)
        self.assertIn('QuantizeInfo 48', message)
        self.assertIn('widest position format', message)

    def test_non_finite_position_is_rejected_by_every_candidate(self):
        geometry = _positions_geometry([(math.nan, 0.0, 0.0)])
        with self.assertRaises(ValueError):
            cse.select_position_format('Cube', 1, geometry)

    def test_chosen_format_actually_encodes(self):
        geometry = _positions_geometry([(0.0, 0.0, 0.0), (100.0, -50.0, 0.25)])
        position_format = cse.select_position_format('Gesso', 3, geometry)
        raw = cse.encode_positions('Gesso', geometry, position_format)
        divisor = cse.int16_divisor(position_format[1])
        self.assertEqual(
            struct.unpack('>6h', raw),
            (0, 0, 0, 100 * divisor, -50 * divisor, int(0.25 * divisor)),
        )


class PositionQuantizationTests(unittest.TestCase):
    """Step 2.4: quantize with the template position format."""

    def test_positions_encode_big_endian_int16(self):
        geometry = _positions_geometry([(0.1, -0.1, 15.5), (-16.0, 0.0, 1 / 2048)])
        raw = cse.encode_positions('Cube', geometry)
        self.assertEqual(struct.unpack('>6h', raw), (205, -205, 31744, -32768, 0, 1))

    def test_out_of_range_component_names_object_vertex_axis_value_and_range(self):
        geometry = _positions_geometry([(0.0, 0.0, 0.0), (0.0, 16.25, 0.0)], source_vertices=[3, 9])
        with self.assertRaises(ValueError) as caught:
            cse.encode_positions('Hat', geometry)
        message = str(caught.exception)
        self.assertIn('Hat vertex 9 y', message)
        self.assertIn('16.250000', message)
        self.assertIn('-16..15.9995', message)

    def test_values_are_rejected_not_clamped(self):
        with self.assertRaises(ValueError):
            cse.quantize_int16(-16.001, 2048, 'x')
        with self.assertRaises(ValueError):
            cse.quantize_int16(math.inf, 2048, 'x')

    def test_unsupported_position_format_rejected(self):
        geometry = _positions_geometry([(0.0, 0.0, 0.0)])
        with self.assertRaises(ValueError):
            cse.encode_positions('Cube', geometry, position_format=(6, 59))
        with self.assertRaises(ValueError):
            cse.encode_positions('Cube', geometry, position_format=(3, 0x40))

    def test_faces_encode_uint16_triplets_with_count(self):
        raw, count = cse.encode_faces('Cube', [(0, 1, 2), (2, 1, 300)])
        self.assertEqual(count, 2)
        self.assertEqual(struct.unpack('>6H', raw), (0, 1, 2, 2, 1, 300))

    def test_mesh_without_faces_rejected(self):
        with self.assertRaises(ValueError) as caught:
            cse.encode_faces('Empty', [])
        self.assertIn('Empty', str(caught.exception))


def _cube_loop_attributes(uv_for_corner=None):
    """Per-loop normals (object-local face normals), UVs and no colors for
    the cube, indexed by the loop numbering of _cube_triangles."""
    normals, uvs = [], []
    corner_uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    for face in CUBE_FACES:
        n = _face_normal(*(CUBE_COS[v] for v in face))
        length = math.sqrt(sum(c * c for c in n))
        for corner in range(3):
            normals.append(tuple(c / length for c in n))
            uvs.append(uv_for_corner(corner) if uv_for_corner else corner_uvs[corner])
    return normals, uvs, None


def _cube_geometry(obj_world=None, host_bind=HOST_BIND):
    world = obj_world if obj_world is not None else _mul(ARMATURE_WORLD, host_bind)
    return cse.bone_local_geometry(CUBE_COS, _cube_triangles(), world, ARMATURE_WORLD, host_bind)


def _unpack(field, fmt):
    raw = base64.b64decode(field)
    size = struct.calcsize('>' + fmt)
    return list(struct.unpack(f'>{len(raw) // size}{fmt}', raw))


def _mario_model():
    with open(REAL_MARIO_SLUGGIE, encoding='utf-8') as handle:
        return json.load(handle)


class AttributePlanTests(unittest.TestCase):
    def _model(self, submesh0_states, rigid_states=None):
        submeshes = [{'VertexBuffer': {'VertexBufferCompCount': 6}, 'DisplayStates': submesh0_states}]
        if rigid_states is not None:
            submeshes.append({'VertexBuffer': {'VertexBufferCompCount': 3}, 'DisplayStates': rigid_states})
        return {'Submeshes': submeshes}

    def test_rigid_plan_follows_template_type3(self):
        # 0x3ca8: position/lighting/color0 u8, texture0 u16 (0b11), texture1 u8.
        rigid = [{'DisplayStateId': 3, 'ShaderMode': '00003ca8'},
                 {'DisplayStateId': 7, 'ShaderMode': 'Spec', 'SurfaceId': 'sm1_ds1'}]
        plan = cse.attribute_plan(self._model([], rigid), 'rigid:sm1_ds1')
        self.assertEqual((plan.normals, plan.color, plan.uv_channels), (True, True, 2))
        self.assertEqual(plan.entry_limits['position'], 256)
        self.assertEqual(plan.entry_limits['texture0'], 0xFFFF)

    def test_rigid_plan_without_color_or_normals(self):
        # 0x0a08: position u8, texture0 u8, texture1 u8 only.
        rigid = [{'DisplayStateId': 3, 'ShaderMode': '00002808'},
                 {'DisplayStateId': 7, 'ShaderMode': 'Spec', 'SurfaceId': 'sm1_ds1'}]
        plan = cse.attribute_plan(self._model([], rigid), 'rigid:sm1_ds1')
        self.assertEqual((plan.normals, plan.color, plan.uv_channels), (False, False, 2))

    def test_rigid_plan_rejects_three_uv_channels_and_missing_surface(self):
        rigid = [{'DisplayStateId': 3, 'ShaderMode': '0000a808'},
                 {'DisplayStateId': 7, 'ShaderMode': 'Spec', 'SurfaceId': 'sm1_ds1'}]
        with self.assertRaises(ValueError):
            cse.attribute_plan(self._model([], rigid), 'rigid:sm1_ds1')
        with self.assertRaises(ValueError):
            cse.attribute_plan(self._model([], rigid), 'rigid:sm9_ds9')

    def test_derived_uv_count_follows_layer1_binding_before_the_surface(self):
        states = [{'DisplayStateId': 1, 'ShaderMode': '11110000'},
                  {'DisplayStateId': 7, 'ShaderMode': 'Spec', 'SurfaceId': 'sm0_ds1'},
                  {'DisplayStateId': 1, 'ShaderMode': '11002003'},
                  {'DisplayStateId': 7, 'ShaderMode': 'Spec', 'SurfaceId': 'sm0_ds3'}]
        self.assertEqual(cse.attribute_plan(self._model(states), 'derived:sm0_ds1').uv_channels, 1)
        plan = cse.attribute_plan(self._model(states), 'derived:sm0_ds3')
        self.assertEqual((plan.normals, plan.color, plan.uv_channels), (True, True, 2))

    def test_builtin_uv_count_follows_any_submesh0_layer1_binding(self):
        without = [{'DisplayStateId': 1, 'ShaderMode': '11110000'}]
        with_layer1 = without + [{'DisplayStateId': 1, 'ShaderMode': '11002004'}]
        self.assertEqual(cse.attribute_plan(self._model(without), 'builtin:rigid_spec_v1').uv_channels, 1)
        self.assertEqual(cse.attribute_plan(self._model(with_layer1), 'builtin:rigid_spec_v1').uv_channels, 2)

    def test_malformed_source_rejected(self):
        for source in ('', 'rigid:', 'other:x'):
            with self.assertRaises(ValueError):
                cse.attribute_plan(self._model([]), source)


class LoopAttributeTests(unittest.TestCase):
    """Step 2.5: per-loop normals, colors and UVs; UV1 mirrors UV0."""

    PLAN = cse.AttributePlan(normals=True, color=True, uv_channels=2, entry_limits={})

    def _entry(self, plan=None, geometry=None, loop_colors=None, **kwargs):
        normals, uvs, _ = _cube_loop_attributes(**kwargs)
        return cse.build_custom_submesh_entry(
            'Cube', 'custom0', 12, 'builtin:rigid_spec_v1', plan or self.PLAN,
            geometry or _cube_geometry(), normals, uvs, loop_colors,
            {'DonorTextureIndex': 0},
        )

    def test_dedupe_pools_identical_records_in_first_seen_order(self):
        data, indices = cse.dedupe_records([b'ab', b'cd', b'ab', b'ef', b'cd'])
        self.assertEqual(data, b'abcdef')
        self.assertEqual(indices, [0, 1, 0, 2, 1])

    def test_cube_normals_pool_to_six_unit_axis_normals(self):
        entry = self._entry()
        normals = _unpack(entry['NormalBufferData'], 'h')
        self.assertEqual(len(normals), 6 * 3)
        records = {tuple(normals[i:i + 3]) for i in range(0, len(normals), 3)}
        # QuantizeInfo 62: 14 fractional bits, so 1.0 == 16384.
        self.assertEqual(records, {(16384, 0, 0), (-16384, 0, 0), (0, 16384, 0),
                                   (0, -16384, 0), (0, 0, 16384), (0, 0, -16384)})
        self.assertEqual(len(_unpack(entry['NormalFacesData'], 'H')), 36)

    def test_normals_follow_the_object_rotation_into_bone_space(self):
        # Rotate the object 90 degrees about Z: the +X face normal becomes +Y.
        rotated = _cube_geometry(obj_world=_mul(ARMATURE_WORLD, HOST_BIND, _rot_z(math.pi / 2)))
        normals, uvs, _ = _cube_loop_attributes()
        data, indices = cse.encode_loop_normals('Cube', rotated, normals)
        values = struct.unpack(f'>{len(data) // 2}h', data)
        # Face 8 (vertices 1, 2, 6) is the +X face of the unrotated cube.
        first_loop_of_face8 = indices[8 * 3]
        self.assertEqual(values[first_loop_of_face8 * 3:first_loop_of_face8 * 3 + 3], (0, 16384, 0))

    def test_uvs_flip_v_and_channel1_mirrors_channel0(self):
        entry = self._entry()
        channel0, channel1 = entry['UVChannels']
        self.assertEqual((channel0['UVChannelIndex'], channel1['UVChannelIndex']), (0, 1))
        self.assertEqual(channel0['UVChannelData'], channel1['UVChannelData'])
        self.assertEqual(channel0['UVFacesData'], channel1['UVFacesData'])
        st = _unpack(channel0['UVChannelData'], 'h')
        records = {tuple(st[i:i + 2]) for i in range(0, len(st), 2)}
        # Blender (u, v) (0,0), (1,0), (1,1) -> ST (0,1), (1,1), (1,0) at 16384 per unit.
        self.assertEqual(records, {(0, 16384), (16384, 16384), (16384, 0)})

    def test_single_uv_channel_plan_writes_one_channel(self):
        plan = cse.AttributePlan(normals=True, color=True, uv_channels=1, entry_limits={})
        self.assertEqual(len(self._entry(plan=plan)['UVChannels']), 1)

    def test_uv_out_of_range_rejected_with_loop_details(self):
        with self.assertRaises(ValueError) as caught:
            self._entry(uv_for_corner=lambda corner: (9.0, 0.0))
        self.assertIn('Cube loop 0 UV u', str(caught.exception))

    def test_mesh_without_colors_exports_one_white_entry(self):
        entry = self._entry()
        self.assertEqual(base64.b64decode(entry['ColorChannelData']), b'\xff\xff\xff\xff')
        self.assertEqual(set(_unpack(entry['ColorFacesData'], 'H')), {0})

    def test_loop_colors_encode_rgba8(self):
        colors = [(1.0, 0.0, 0.0, 1.0) if loop < 18 else (0.0, 0.5, 1.0, 0.25) for loop in range(36)]
        entry = self._entry(loop_colors=colors)
        self.assertEqual(base64.b64decode(entry['ColorChannelData']), bytes.fromhex('ff0000ff' '0080ff40'))

    def test_plan_without_normals_or_color_omits_them(self):
        plan = cse.AttributePlan(normals=False, color=False, uv_channels=1, entry_limits={})
        entry = self._entry(plan=plan)
        for key in ('NormalBufferData', 'NormalFacesData', 'ColorChannelData', 'ColorFacesData'):
            self.assertNotIn(key, entry)

    def test_template_index_width_overflow_is_rejected_with_advice(self):
        # The limit applies to pooled entries: the cube has 36 UV loops but
        # only 3 distinct UVs, so a limit of 3 passes and 2 fails.
        plan = cse.AttributePlan(normals=True, color=True, uv_channels=1,
                                 entry_limits={'position': 256, 'texture0': 3})
        self._entry(plan=plan)
        plan.entry_limits['texture0'] = 2
        with self.assertRaises(ValueError) as caught:
            self._entry(plan=plan)
        message = str(caught.exception)
        self.assertIn('Cube: 3 distinct UVs', message)
        self.assertIn('builtin:rigid_spec_v1', message)

    def test_u8_overflow_suggests_a_regenerating_template(self):
        plan = cse.AttributePlan(normals=True, color=True, uv_channels=1,
                                 entry_limits={'position': 256})
        cse._check_entry_limit('Big', 'rigid:sm1_ds5', 'position', 'vertices', 256, plan)
        with self.assertRaises(ValueError) as caught:
            cse._check_entry_limit('Big', 'rigid:sm1_ds5', 'position', 'vertices', 257, plan)
        self.assertIn('u8 index of rigid:sm1_ds5', str(caught.exception))
        self.assertIn('derived:/builtin:', str(caught.exception))

    def test_entry_shape_matches_schema(self):
        entry = self._entry()
        self.assertEqual(entry['CustomSubmeshId'], 'custom0')
        self.assertEqual(entry['HostBoneId'], 12)
        self.assertEqual(entry['FacesCount'], 12)
        self.assertEqual(entry['TextureAssignment'], {'DonorTextureIndex': 0})
        self.assertEqual(len(_unpack(entry['VertexBufferData'], 'h')), 8 * 3)

    def test_mesh_name_is_ascii_and_short(self):
        self.assertEqual(cse.sanitize_mesh_name('Hüt'), 'H_t')
        self.assertEqual(len(cse.sanitize_mesh_name('x' * 100)), cse.MESH_NAME_MAX_LENGTH)
        self.assertEqual(cse.sanitize_mesh_name(''), 'custom')

    def test_list_encoding_when_base64_is_off(self):
        normals, uvs, _ = _cube_loop_attributes()
        entry = cse.build_custom_submesh_entry(
            'Cube', 'custom0', 12, 'builtin:rigid_spec_v1', self.PLAN, _cube_geometry(),
            normals, uvs, None, {'DonorTextureIndex': 0}, use_base64=False,
        )
        self.assertIsInstance(entry['VertexBufferData'], list)


class LoopAttributeGlueTests(unittest.TestCase):
    def test_encode_custom_submesh_uses_the_shared_helpers(self):
        fn = _exporter_function('encode_custom_submesh')
        source = ast.unparse(fn)
        for call in ('_detect_uniform_vertex_bone_id', 'attribute_plan',
                     '_custom_submesh_bone_local_geometry', '_custom_submesh_loop_attributes',
                     'build_custom_submesh_entry'):
            self.assertIn(call, source)
        # Geometry (which flushes Edit Mode) is read before loop attributes.
        self.assertLess(source.index('_custom_submesh_bone_local_geometry'),
                        source.index('_custom_submesh_loop_attributes('))

    def test_loop_attributes_use_render_uv_map_and_color_srgb_reader(self):
        source = ast.unparse(_exporter_function('_custom_submesh_loop_attributes'))
        self.assertIn('active_render', source)
        self.assertIn('_get_loop_color', source)
        self.assertIn('_per_loop_normals', source)


@unittest.skipUnless(REAL_MARIO_SLUGGIE.exists(), 'real Mario export not present')
class ExportedEntryBuildsTests(unittest.TestCase):
    """Exporter-produced entries go through the real hammerspace builder:
    validation, GPL append and GeoId patch, for every template source."""

    @classmethod
    def setUpClass(cls):
        tools_dir = ROOT / 'SluggiesTools'
        for path in (tools_dir, tools_dir / 'Hammerspace'):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        import HammerspaceMain
        cls.main = HammerspaceMain

    def _free_host_bone(self, model):
        skn_used = {int(e.get('BoneId', -1))
                    for e in (model.get('SkinData') or {}).get('SK1s', []) + (model.get('SkinData') or {}).get('SK2s', [])}
        return next(int(b['BoneId']) for b in model['BoneHierarchy']
                    if self.main._bone_geo_id_raw(b) == 0xFFFF and int(b['BoneId']) not in skn_used
                    and b.get('ParentBoneId') is not None)

    def _build(self, template_source, away=None, warnings=None):
        data = _mario_model()
        model = data['SluggiesModel']
        model['UseHammerspace'] = True
        host_bone_id = self._free_host_bone(model)
        host_bind = cse.bone_absolute_matrices(model['BoneHierarchy'])[host_bone_id]
        placement = _mul(ARMATURE_WORLD, host_bind)
        if away is not None:
            placement = _mul(placement, _translation(away, 0.0, 0.0))
        geometry = _cube_geometry(obj_world=placement, host_bind=host_bind)
        normals, uvs, colors = _cube_loop_attributes()
        plan = cse.attribute_plan(model, template_source)
        entry = cse.build_custom_submesh_entry(
            'CustomSubmesh_1', 'custom0', host_bone_id, template_source, plan, geometry,
            normals, uvs, colors, {'DonorTextureIndex': 0}, model.get('UseBase64', True),
            warnings,
        )
        model['CustomSubmeshes'] = [entry]
        modes = self.main.SectionModes(gpl='build', act='clone', tex='clone', skn='clone', trailing='clone')
        return self.main.BuildModelBlock(data, modes, sluggie_path=REAL_MARIO_SLUGGIE), entry, plan

    def test_every_template_source_builds_a_valid_block(self):
        for source in ('rigid:sm1_ds5', 'derived:sm0_ds5', 'builtin:rigid_spec_v1'):
            with self.subTest(source=source):
                build, entry, plan = self._build(source)
                self.assertTrue(build.validation_report['valid'], build.validation_report.get('errors'))
                self.assertEqual(len(entry['UVChannels']), plan.uv_channels)
                self.assertEqual(entry['VertexBufferQuantizeInfo'], 59)
                corners = _unpack(entry['VertexBufferData'], 'h')
                self.assertEqual(sorted(set(abs(v) for v in corners)), [205])

    def test_a_far_submesh_widens_its_format_warns_and_still_builds(self):
        warnings = []
        build, entry, _ = self._build('builtin:rigid_spec_v1', away=200.0, warnings=warnings)
        self.assertTrue(build.validation_report['valid'], build.validation_report.get('errors'))
        # The cube spans 200 +/- 0.1 on x, so it needs +/-256: shift 7, QuantizeInfo 55.
        self.assertEqual(entry['VertexBufferQuantizeInfo'], 55)
        divisor = cse.int16_divisor(55)
        corners = _unpack(entry['VertexBufferData'], 'h')
        decoded = sorted({v / divisor for v in corners})
        # Four distinct coordinates, each within half a quantization step of
        # the true cube -- the precision the wider range costs.
        self.assertEqual(len(decoded), 4)
        for got, want in zip(decoded, (-0.1, 0.1, 199.9, 200.1)):
            self.assertLessEqual(abs(got - want), 0.5 / divisor)
        self.assertEqual(len(warnings), 1)
        self.assertIn('QuantizeInfo 55', warnings[0])
        self.assertIn('CustomSubmesh_1', warnings[0])

    def test_a_near_submesh_warns_about_nothing(self):
        warnings = []
        self._build('builtin:rigid_spec_v1', warnings=warnings)
        self.assertEqual(warnings, [])


@unittest.skipUnless(REAL_MARIO_SLUGGIE.exists(), 'real Mario export not present')
class RoundTripAnchorTests(unittest.TestCase):
    """Mario's rigid `head`, placed the way the importer places it, must come
    back to its donor quantized position bytes exactly."""

    def test_imported_head_reproduces_donor_positions(self):
        with open(REAL_MARIO_SLUGGIE, encoding='utf-8') as handle:
            model = json.load(handle)['SluggiesModel']
        head_index = next(i for i, sm in enumerate(model['Submeshes']) if sm.get('MeshName') == 'head')
        vb = model['Submeshes'][head_index]['VertexBuffer']
        self.assertEqual((vb['VertexBufferCompCount'], vb['VertexBufferQuantizeInfo']), (3, 59))
        raw = base64.b64decode(vb['VertexBufferData'])
        donor = struct.unpack(f'>{len(raw) // 2}h', raw)
        divisor = 1 << (59 & 0xF)
        # Blender stores vertex coordinates and matrices as float32.
        cos = [tuple(_f32(donor[i + k] / divisor) for k in range(3)) for i in range(0, len(donor), 3)]

        owner = next(b for b in model['BoneHierarchy']
                     if not b.get('Skinned') and b.get('GeoId') == head_index)
        host_bind = cse.bone_absolute_matrices(model['BoneHierarchy'])[int(owner['BoneId'])]
        # _apply_nonskinned_transform sets matrix_world = bind before the
        # armature parent is evaluated, so the evaluated world matrix is
        # armature_world @ bind.
        obj_world = _f32_matrix(cse.mat4_mul(ARMATURE_WORLD, host_bind))
        arm_world = _f32_matrix(ARMATURE_WORLD)

        n = len(cos)
        triangles = [cse.Triangle((i, (i + 1) % n, (i + 2) % n), (0, 0, 0), i) for i in range(n)]
        geometry = cse.bone_local_geometry(cos, triangles, obj_world, arm_world, host_bind)
        self.assertEqual(geometry.source_vertices, list(range(n)))

        cse.check_position_range('head', int(owner['BoneId']), geometry)
        self.assertEqual(cse.encode_positions('head', geometry), raw)


if __name__ == '__main__':
    unittest.main()
