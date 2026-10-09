"""PLAN_ModelReplacements.md Milestone 4 (export side): the bpy-free
SkinnedRebuildExport module is imported directly; the bpy glue in
ExportSluggies.py / ImportSluggies.py / SluggiesToolsPanel.py is checked
with the AST or by executing single helpers against small fakes."""
import ast
import base64
import pathlib
import struct
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADDON_DIR = ROOT / 'BlenderAddonSrc'
TOOLS_DIR = ROOT / 'SluggiesTools'
EXPORTER_PATH = ADDON_DIR / 'ExportSluggies.py'
IMPORTER_PATH = ADDON_DIR / 'ImportSluggies.py'
PANEL_PATH = ADDON_DIR / 'SluggiesToolsPanel.py'
for path in (ADDON_DIR, TOOLS_DIR, TOOLS_DIR / 'Hammerspace', pathlib.Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import CustomSubmeshExport as cse  # noqa: E402
import FieldCodec  # noqa: E402
import HammerspaceMain as main  # noqa: E402
import RigidRebuildExport as rre  # noqa: E402
import SkinnedRebuildExport as sre  # noqa: E402
import synthetic_donor  # noqa: E402

BONE_A = synthetic_donor.SKINNED_BONE      # 3
BONE_B = synthetic_donor.SKN_ACC_BONE      # 6
SURFACE = f'sm0_ds{synthetic_donor.SURFACE_STATE_INDEX}'
N = synthetic_donor.SKINNED_VERTEX_COUNT


def _load(path, names, namespace=None):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
        or isinstance(node, ast.Assign) and any(getattr(t, 'id', None) in names for t in node.targets)
    ]
    namespace = dict(namespace or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def _donor_body():
    model = synthetic_donor.build_sluggie()['SluggiesModel']
    sub = model['Submeshes'][0]
    return model, sub, sre.donor_skinned_submesh(0, sub, model['SkinData'])


class DonorFactsTests(unittest.TestCase):
    def test_synthetic_body_facts(self):
        model, sub, donor = _donor_body()
        self.assertEqual(donor.owner_key, 'sm0')
        self.assertEqual(donor.position_quantize, synthetic_donor.SKINNED_QUANTIZE_INFO)
        self.assertEqual(donor.skinned_bones, {BONE_A, BONE_B})
        self.assertEqual(donor.drawable_surfaces, {SURFACE: synthetic_donor.SURFACE_STATE_INDEX})
        self.assertEqual(len(donor.faces), sub['FacesCount'])
        self.assertTrue(donor.uv_mirrored)
        self.assertIsNone(donor.color_format)
        self.assertFalse(donor.facial)
        self.assertTrue(sre.is_skinned_submesh(sub))
        self.assertFalse(sre.is_skinned_submesh(model['Submeshes'][1]))

    def test_rigid_submesh_is_refused(self):
        model = synthetic_donor.build_sluggie()['SluggiesModel']
        with self.assertRaisesRegex(ValueError, 'not the skinned submesh'):
            sre.donor_skinned_submesh(1, model['Submeshes'][1], model['SkinData'])

    def test_facial_flag_and_warning(self):
        model, sub, _donor = _donor_body()
        donor = sre.donor_skinned_submesh(0, sub, model['SkinData'], facial_submeshes={0})
        self.assertTrue(donor.facial)
        warning = sre.facial_warning('body', donor, 3)
        self.assertIn('no facial animation', warning)
        self.assertIn('shape keys', warning)
        self.assertIsNone(sre.facial_warning('body', _donor, 0))

    def test_position_format_error(self):
        _model, _sub, donor = _donor_body()
        self.assertIsNone(sre.donor_position_format_error(donor, 'body'))
        donor.position_quantize = 0x40
        self.assertIn('not int16', sre.donor_position_format_error(donor, 'body'))


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.donor = sre.DonorSkinnedSubmesh(
            index=0, position_quantize=59, normal_format=(6, 59), color_format=None, uv_formats={0: (2, 62)},
            uv_mirrored=False, faces=[(0, 1, 2)] * 4, face_surfaces=['sm0_ds5'] * 2 + ['sm0_ds9'] * 2,
            drawable_surfaces={'sm0_ds5': 5, 'sm0_ds9': 9, 'sm0_ds11': 11}, facial=False, skinned_bones={3},
        )
        self.modes = [None] * 5 + ['Spec'] * 4 + ['RhSp'] * 2 + ['Spec']
        self.slots = [rre.SlotSurface('a', 'sm0_ds5'), rre.SlotSurface('b', 'sm0_ds9'), rre.SlotSurface('c', 'sm0_ds11')]
        self.tris = [cse.Triangle((0, 1, 2), (3 * i, 3 * i + 1, 3 * i + 2), i) for i in range(4)]

    def _routing(self, polygon_slots):
        return rre.route_faces('body', self.donor, self.tris, polygon_slots, self.slots, False)

    def test_unchanged_and_complete_same_shader_moves_keep_the_old_path(self):
        self.assertFalse(sre.surfaces_need_rebuild(self._routing([0, 0, 1, 1]), self.donor, self.modes))
        # sm0_ds5 (Spec) -> sm0_ds11 (Spec), complete.
        self.assertFalse(sre.surfaces_need_rebuild(self._routing([2, 2, 1, 1]), self.donor, self.modes))

    def test_partial_cross_shader_and_new_surface_moves_need_the_rebuild(self):
        self.assertTrue(sre.surfaces_need_rebuild(self._routing([0, 2, 1, 1]), self.donor, self.modes))   # partial
        self.assertTrue(sre.surfaces_need_rebuild(self._routing([1, 1, 1, 1]), self.donor, self.modes))   # Spec -> RhSp
        slots = self.slots + [rre.SlotSurface('new', 'sm0_new0', new_surface=True, owner='sm0')]
        routing = rre.route_faces('body', self.donor, self.tris, [0, 0, 1, 3], slots, False)
        self.assertTrue(sre.surfaces_need_rebuild(routing, self.donor, self.modes))

    def test_decide_reasons(self):
        self.assertEqual(sre.decide_reasons(topology_changed=False, moved=False, surfaces_changed=False), [])
        self.assertEqual(
            sre.decide_reasons(topology_changed=True, moved=True, surfaces_changed=True),
            ['topology', 'object_transform', 'surfaces'],
        )


class WeightTests(unittest.TestCase):
    def test_remap_moves_unskinned_bones_to_the_nearest_skinned_ancestor(self):
        parent_of = {0: None, 1: 0, 2: 1, 3: 1, 4: 1, 5: 2, 6: 3, 7: 4, 8: 0}
        remap = sre.remap_influences(
            [{3: 1.0}, {5: 0.5, 2: 0.5}, {6: 0.25, 7: 0.75}, {8: 1.0}, {}],
            allowed={3, 6}, parent_of=parent_of,
        )
        # 5 -> 2 -> 1 -> 0: no skinned ancestor, dropped; 7 -> 4 -> 1 -> 0: dropped.
        self.assertEqual(remap.weights, [{3: 1.0}, {}, {6: 0.25}, {}, {}])
        self.assertEqual(remap.remapped, {})
        self.assertEqual(remap.dropped, {5: 1, 2: 1, 7: 1, 8: 1})
        self.assertEqual(remap.unweighted, [1, 3, 4])
        remap = sre.remap_influences([{5: 1.0, 3: 1.0}], allowed={1, 3}, parent_of=parent_of)
        self.assertEqual(remap.weights, [{1: 1.0, 3: 1.0}])
        self.assertEqual(remap.remapped, {(5, 1): 1})
        report = sre.remap_report(remap, 'body')
        self.assertEqual(len(report), 1)
        self.assertIn('bone_5', report[0])
        self.assertIn('bone_1', report[0])
        self.assertIn('vertex(es) have no weight', sre.unweighted_error('body', [4]))
        self.assertIsNone(sre.unweighted_error('body', []))

    def test_encode_influences_normalizes_per_exported_vertex(self):
        raw = FieldCodec.decode_field(sre.encode_influences([{3: 2.0, 6: 2.0}, {3: 0.5}], [1, 0]))
        records = list(struct.iter_unpack('>HHf', raw))
        self.assertEqual(records, [(0, 3, 1.0), (1, 3, 0.5), (1, 6, 0.5)])


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.model, self.sub, self.donor = _donor_body()
        divisor = 1 << (synthetic_donor.SKINNED_QUANTIZE_INFO & 0xF)
        raw = base64.b64decode(self.sub['VertexBuffer']['VertexBufferData'])
        stride = synthetic_donor.SKINNED_VERTEX_STRIDE
        self.local = [tuple(v / divisor for v in struct.unpack_from('>3h', raw, i * stride)) for i in range(N)]
        faces = [(0, k, k + 1) for k in range(1, N - 1)]
        self.triangles = [cse.Triangle(face, (3 * i, 3 * i + 1, 3 * i + 2), i) for i, face in enumerate(faces)]
        self.geometry = cse.bone_local_geometry(self.local, self.triangles, cse.IDENTITY4, cse.IDENTITY4, sre.IDENTITY)
        uv_raw = base64.b64decode(self.sub['UVChannels'][0]['UVChannelData'])
        coords = [struct.unpack_from('>2h', uv_raw, i * 4) for i in range(len(uv_raw) // 4)]
        self.loop_uvs = [(coords[v][0], 1.0 - coords[v][1]) for face in faces for v in face]
        self.routing = rre.route_faces(
            'body', self.donor, self.geometry.triangles, [0] * len(faces), [rre.SlotSurface('body_mat', SURFACE)], False,
        )
        self.loop_normals = [(0.0, 0.0, 1.0)] * (3 * len(faces))
        # As the exporter glue does: mean loop normal per vertex, then unit length.
        self.normals = cse.transform_normals(
            sre.vertex_normals(self.geometry, self.loop_normals, N), self.geometry.to_bone,
        )
        self.weights = [{BONE_A: 1.0}] * N

    def _entry(self, **overrides):
        kwargs = dict(
            object_name='body', donor=self.donor, geometry=self.geometry, normals=self.normals,
            loop_uvs_by_channel={0: self.loop_uvs, 1: self.loop_uvs}, loop_colors=None,
            routing=self.routing, vertex_weights=self.weights, new_surfaces=[], reasons=['topology'], warnings=[],
        )
        kwargs.update(overrides)
        return sre.build_skinned_rebuild_entry(**kwargs)

    def test_vertex_normals_average_the_loops(self):
        loop_normals = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)] * len(self.triangles)
        normals = sre.vertex_normals(self.geometry, loop_normals, N)
        self.assertEqual(len(normals), N)
        self.assertEqual(normals[0][0], len(self.triangles) * 1.0)    # vertex 0 is corner 0 of every fan triangle
        self.assertEqual(normals[N - 1], (0.0, 0.0, 1.0))             # last vertex is corner 2 once

    def test_entry_round_trips_through_the_patcher(self):
        entry = self._entry()
        self.assertEqual(entry['FacesCount'], N - 2)
        records = FieldCodec.decode_field(entry['VertexBufferData'])
        self.assertEqual(len(records), N * synthetic_donor.SKINNED_VERTEX_STRIDE)
        donor_raw = base64.b64decode(self.sub['VertexBuffer']['VertexBufferData'])
        for vertex in range(N):          # positions survive, normals are +Z (0, 0, 1 << shift)
            offset = vertex * 12
            self.assertEqual(records[offset:offset + 6], donor_raw[offset:offset + 6])
            self.assertEqual(struct.unpack_from('>3h', records, offset + 6), (0, 0, 1 << (synthetic_donor.SKINNED_QUANTIZE_INFO & 0xF)))
        self.assertEqual(len(FieldCodec.decode_field(entry['Influences'])), N * 8)
        self.assertNotIn('ColorChannelData', entry)
        self.assertEqual(entry['UVChannels'][0]['UVChannelData'], entry['UVChannels'][1]['UVChannelData'])
        self.model['UseHammerspace'] = True
        self.sub['SkinnedRebuild'] = entry
        main._validate_skinned_rebuild(self.model)
        with synthetic_donor.donor_environment() as env:
            data = env.reload()
            data['SluggiesModel']['UseHammerspace'] = True
            data['SluggiesModel']['Submeshes'][0]['SkinnedRebuild'] = entry
            result = main.BuildModelBlock(
                data, main.SectionModes(gpl='build', skn='build'), sluggie_path=env.sluggie_path,
            )
            self.assertTrue(result.validation_report['valid'], result.validation_report.get('errors'))

    def test_out_of_range_positions_name_the_donor_format(self):
        far = cse.bone_local_geometry(
            [(x + 40000.0, y, z) for x, y, z in self.local], self.triangles, cse.IDENTITY4, cse.IDENTITY4, sre.IDENTITY,
        )
        with self.assertRaisesRegex(ValueError, 'armature origin'):
            self._entry(geometry=far)

    def test_colors_use_the_donor_format(self):
        self.donor.color_format = (4, 48)
        loops = [loop for tri in self.geometry.triangles for loop in tri.loops]
        entry = self._entry(loop_colors={loop: (1.0, 0.0, 0.0, 1.0) for loop in loops})
        self.assertEqual(FieldCodec.decode_field(entry['ColorChannelData']), bytes.fromhex('f00f'))
        self.assertEqual(len(FieldCodec.decode_field(entry['ColorFacesData'])), 2 * 3 * (N - 2))

    def test_unset_colors_are_written_white_and_reported(self):
        # Blender fills color0 with (0, 0, 0, 0) on corners joined from an
        # object without the attribute; the game draws them invisible.
        self.donor.color_format = (4, 48)
        loops = [loop for tri in self.geometry.triangles for loop in tri.loops]
        colors = {loop: (1.0, 0.0, 0.0, 1.0) for loop in loops}
        for loop in loops[:3]:
            colors[loop] = (0.0, 0.0, 0.0, 0.0)
        infos = []
        entry = self._entry(loop_colors=colors, infos=infos)
        self.assertEqual(FieldCodec.decode_field(entry['ColorChannelData']), bytes.fromhex('fffff00f'))
        self.assertEqual(len(infos), 1)
        self.assertIn('3 face corner(s)', infos[0])
        self.assertIn('opaque white', infos[0])

    def test_new_surfaces_and_reasons_are_carried(self):
        slots = [rre.SlotSurface('body_mat', SURFACE), rre.SlotSurface('belt', 'sm0_new0', new_surface=True, owner='sm0')]
        routing = rre.route_faces('body', self.donor, self.geometry.triangles, [0, 0, 1, 1, 1], slots, False)
        surface = {'SurfaceKey': 'sm0_new0', 'MaterialName': 'belt', 'TemplateSource': 'builtin:rigid_spec_v1',
                   'TextureAssignment': {'DonorTextureIndex': 1}}
        entry = self._entry(routing=routing, new_surfaces=[surface], reasons=['surfaces'])
        self.assertEqual(entry['NewSurfaces'], [surface])
        self.assertEqual(entry['Reason'], ['surfaces'])
        self.assertEqual(struct.unpack('>5H', FieldCodec.decode_field(entry['FaceSurfaceIndices'])), (0, 0, 1, 1, 1))

    def test_mode_reason(self):
        self.assertEqual(sre.mode_reason('body', ['topology']), 'body rebuild of body (topology)')


class _Mat(dict):
    def __init__(self, name, **props):
        super().__init__(props)
        self.name = name


class GlueTests(unittest.TestCase):
    def test_drop_facial_edits_for_submesh(self):
        helpers = _load(EXPORTER_PATH, {'_drop_facial_edits_for_submesh'})
        data = {'SluggiesModel': {
            'FacialPoseData': {'Objects': [{'ObjectIndex': 0, 'SubmeshIndex': 0}, {'ObjectIndex': 1, 'SubmeshIndex': 2}]},
            'FacialPoseDataEdited': {'Objects': [{'ObjectIndex': 0}, {'ObjectIndex': 1}]},
        }}
        helpers['_drop_facial_edits_for_submesh'](data, 0)
        self.assertEqual(data['SluggiesModel']['FacialPoseDataEdited'], {'Objects': [{'ObjectIndex': 1}]})
        helpers['_drop_facial_edits_for_submesh'](data, 2)
        self.assertNotIn('FacialPoseDataEdited', data['SluggiesModel'])

    def test_vertex_bone_weights(self):
        helpers = _load(EXPORTER_PATH, {'_vertex_bone_weights', '_parse_bone_group_name'})
        groups = [SimpleNamespace(index=0, name='bone_3'), SimpleNamespace(index=1, name='bone_6'), SimpleNamespace(index=2, name='other')]
        vertices = [
            SimpleNamespace(groups=[SimpleNamespace(group=0, weight=0.5), SimpleNamespace(group=1, weight=0.5)]),
            SimpleNamespace(groups=[SimpleNamespace(group=2, weight=1.0), SimpleNamespace(group=0, weight=0.0)]),
        ]
        obj = SimpleNamespace(vertex_groups=groups, data=SimpleNamespace(vertices=vertices))
        self.assertEqual(helpers['_vertex_bone_weights'](obj), [{3: 0.5, 6: 0.5}, {}])

    def test_execute_wiring(self):
        source = EXPORTER_PATH.read_text(encoding='utf-8')
        execute = source[source.index('    def execute(self, context):'):]
        self.assertLess(execute.index('_rigid_rebuild_plan('), execute.index('_skinned_rebuild_plan('))
        self.assertLess(execute.index('SkinnedRebuildExport.mode_reason('), execute.index('while True:'))
        self.assertIn('SkinnedRebuildExport.strip_in_place_edits(target_submesh)', execute)
        self.assertIn('target_submesh["SkinnedRebuild"] = entry', execute)
        self.assertIn('if plan is not None or skinned_plan is not None:', execute)
        # A rebuilt body never writes SkinDataEdited and its shape keys are not encoded.
        self.assertLess(execute.index('if skinned_plans:\n'), execute.index('elif skinned_donor_objects(candidates, data):'))
        self.assertIn('_drop_facial_edits_for_submesh(data, skinned_plan.submesh_index)', execute)
        self.assertIn('body rebuild (', execute)
        # The slot-preserving branches drop a stale entry.
        self.assertEqual(execute.count('target_submesh.pop("SkinnedRebuild", None)'), 3)

    def test_importer_and_panel_wiring(self):
        importer = IMPORTER_PATH.read_text(encoding='utf-8')
        self.assertIn('rebuild = view.get("RigidRebuild") or view.get("SkinnedRebuild")', importer)
        self.assertIn('influences=skinned_influences', importer)
        self.assertIn('submesh.get("RigidRebuild") or submesh.get("SkinnedRebuild") or {}', importer)
        panel = PANEL_PATH.read_text(encoding='utf-8')
        self.assertIn('_add_material_mesh_kind(obj)', panel)
        self.assertNotIn('New materials on skinned submeshes are not supported yet', panel)

    def test_importer_view_and_influences(self):
        helpers = _load(IMPORTER_PATH, {'_skinned_rebuild_influences', '_edited_submesh_view', '_rigid_rebuild_view', '_has_edited_data'},
                        {'struct': struct, '_to_bytes': lambda value: FieldCodec.decode_field(value),
                         '_from_bytes': lambda raw: FieldCodec.encode_field(raw, True)})
        raw = struct.pack('>HHf', 0, 3, 1.0) + struct.pack('>HHf', 1, 6, 0.5)
        sub = {
            'VertexBuffer': {'VertexBufferData': 'AA==', 'VertexBufferCompCount': 6},
            'FacesData': 'AA==', 'FacesCount': 1, 'UVChannels': [{'UVChannelIndex': 0, 'UVChannelData': 'x', 'UVFacesData': 'y'}],
            'SkinnedRebuild': {
                'VertexBufferData': 'QUFB', 'Influences': FieldCodec.encode_field(raw, True),
                'FacesData': 'QkJC', 'FacesCount': 1, 'FaceSurfaceTable': ['sm0_ds5'],
                'FaceSurfaceIndices': FieldCodec.encode_field(struct.pack('>H', 0), True),
                'UVChannels': [{'UVChannelIndex': 0, 'UVChannelData': 'u', 'UVFacesData': 'v'}],
            },
        }
        self.assertTrue(helpers['_has_edited_data'](sub))
        self.assertEqual(helpers['_skinned_rebuild_influences'](sub), [(0, 3, 1.0), (1, 6, 0.5)])
        view = helpers['_edited_submesh_view'](sub)
        self.assertEqual(view['VertexBuffer']['VertexBufferData'], 'QUFB')
        self.assertEqual(view['FacesData'], 'QkJC')
        self.assertEqual(view['UVChannels'][0]['UVChannelData'], 'u')
        self.assertEqual(view['_FaceSurfaceIds'], ['sm0_ds5'])

    def test_carry_unselected_body_rebuild_keeps_its_png(self):
        model = {
            'BoneHierarchy': [{'BoneId': 0, 'GeoIdRaw': 0xFFFF}],
            'Submeshes': [{'MeshName': 'body', 'SkinnedRebuild': {'NewSurfaces': [
                {'SurfaceKey': 'sm0_new0', 'TemplateSource': 'builtin:rigid_spec_v1',
                 'TextureAssignment': {'AdditionalTextureFileName': 'belt.png'}},
            ]}}],
        }
        previous = [{'TextureFileName': 'belt.png', 'TemplateTextureIndex': 1}]
        carried = rre.carry_unselected_rebuilds(model, [], previous)
        self.assertEqual(carried.errors, [])
        self.assertEqual(carried.additions, previous)
        self.assertIn('body rebuild', carried.messages[0])
        self.assertNotIn('GeoIdEdited', model['BoneHierarchy'][0])


if __name__ == '__main__':
    unittest.main()
