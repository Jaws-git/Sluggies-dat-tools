"""PLAN_EditRigidMeshes.md Phases 4, 5 and 8 (export side): the bpy-free
RigidRebuildExport module is imported directly; the bpy glue in
ExportSluggies.py / ImportSluggies.py is checked with the AST or by executing
single helpers against small fakes."""
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
for path in (ADDON_DIR, TOOLS_DIR, TOOLS_DIR / 'Hammerspace', pathlib.Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import CustomSubmeshExport as cse  # noqa: E402
import FieldCodec  # noqa: E402
import HammerspaceMain as main  # noqa: E402
import RigidRebuildExport as rre  # noqa: E402
import synthetic_donor  # noqa: E402

RIGID = synthetic_donor.RIGID_OWNER_SUBMESH
OWNER = synthetic_donor.RIGID_OWNER_BONE
SURFACE = f'sm{RIGID}_ds{synthetic_donor.SURFACE_STATE_INDEX}'


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode('ascii')


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


# --- donor facts and decision ---------------------------------------------------

class DonorFactsTests(unittest.TestCase):
    def setUp(self):
        self.model = synthetic_donor.build_sluggie()['SluggiesModel']
        self.sub = self.model['Submeshes'][RIGID]

    def test_synthetic_rigid_submesh_facts(self):
        donor = rre.donor_rigid_submesh(RIGID, self.sub)
        self.assertEqual(donor.position_quantize, synthetic_donor.RIGID_QUANTIZE_INFO)
        self.assertIsNone(donor.normal_format)
        self.assertIsNone(donor.color_format)
        self.assertEqual(donor.uv_formats, {0: (2, 0x30), 1: (2, 0x30)})
        self.assertTrue(donor.uv_mirrored)
        self.assertEqual(len(donor.faces), self.sub['FacesCount'])
        # The synthetic states carry no FaceCount, so the imported surface per
        # face is unknown; a real export fills it from the cumulative ranges.
        self.assertEqual(donor.face_surfaces, [])
        for state in self.sub['DisplayStates']:
            state['FaceCount'] = self.sub['FacesCount'] if state.get('SurfaceId') else 0
        self.assertEqual(set(rre.donor_rigid_submesh(RIGID, self.sub).face_surfaces), {SURFACE})
        self.assertEqual(donor.drawable_surfaces, {SURFACE: synthetic_donor.SURFACE_STATE_INDEX})
        self.assertFalse(donor.facial)
        self.assertEqual(donor.owner_key, f'sm{RIGID}')
        self.assertTrue(rre.donor_rigid_submesh(RIGID, self.sub, facial_submeshes={RIGID}).facial)

    def test_skinned_submesh_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'not a rigid submesh'):
            rre.donor_rigid_submesh(0, self.model['Submeshes'][0])
        self.assertFalse(rre.is_rigid_submesh(self.model['Submeshes'][0]))
        self.assertTrue(rre.is_rigid_submesh(self.sub))

    def test_float_position_format_error(self):
        donor = rre.donor_rigid_submesh(RIGID, self.sub)
        self.assertIsNone(rre.donor_position_format_error(donor, 'cap'))
        donor.position_quantize = 0x40
        self.assertIn('not int16', rre.donor_position_format_error(donor, 'cap'))

    def test_vertex_bone_problem_messages(self):
        self.assertIsNone(rre.vertex_bone_problem('cap', [{5}, {5}, {5}]))
        self.assertIn('2 vertices are in no bone_<id> group', rre.vertex_bone_problem('cap', [{5}, set(), set()]))
        self.assertIn('1 vertices are in several', rre.vertex_bone_problem('cap', [{5}, {5, 6}]))
        message = rre.vertex_bone_problem('cap', [{5}, {6}])
        self.assertIn('spans bones [5, 6]', message)
        self.assertIn('Reassign to new bone', message)

    def test_decide_reasons(self):
        self.assertEqual(rre.decide_reasons(topology_changed=False, moved=False, host_changed=False, surfaces_changed=False), [])
        self.assertEqual(
            rre.decide_reasons(topology_changed=True, moved=True, host_changed=False, surfaces_changed=True),
            ['topology', 'object_transform', 'surfaces'],
        )
        self.assertEqual(rre.decide_reasons(topology_changed=False, moved=True, host_changed=True, surfaces_changed=False), ['host_bone_world'])
        # A host change with Keep offset to bone leaves the bytes alone: no reason.
        self.assertEqual(rre.decide_reasons(topology_changed=False, moved=False, host_changed=True, surfaces_changed=False), [])


class PositionsMovedTests(unittest.TestCase):
    """The importer places a rigid mesh at A @ B_host; an untouched object
    requantizes to the donor values, a moved one does not."""

    def setUp(self):
        self.model = synthetic_donor.build_sluggie()['SluggiesModel']
        self.absolute = cse.bone_absolute_matrices(self.model['BoneHierarchy'])
        self.host = self.absolute[OWNER]
        self.arm_world = cse.quaternion_matrix(0.7071068, 0.7071068, 0.0, 0.0)   # the importer's 90 degree X
        divisor = 1 << (synthetic_donor.RIGID_QUANTIZE_INFO & 0xF)
        raw = base64.b64decode(self.model['Submeshes'][RIGID]['VertexBuffer']['VertexBufferData'])
        self.local = [tuple(v / divisor for v in struct.unpack_from('>3h', raw, i * 6)) for i in range(len(raw) // 6)]
        self.triangles = [cse.Triangle(face, (3 * i, 3 * i + 1, 3 * i + 2), i)
                          for i, face in enumerate((0, k, k + 1) for k in range(1, 7))]

    def _geometry(self, obj_world):
        return cse.bone_local_geometry(self.local, self.triangles, obj_world, self.arm_world, self.host)

    def test_imported_placement_is_not_a_move(self):
        obj_world = cse.mat4_mul(self.arm_world, self.host)
        self.assertFalse(rre.positions_moved(self.local, self._geometry(obj_world), synthetic_donor.RIGID_QUANTIZE_INFO))

    def test_object_mode_translation_is_a_move(self):
        obj_world = cse.mat4_mul(self.arm_world, self.host)
        obj_world[1][3] += 0.01
        self.assertTrue(rre.positions_moved(self.local, self._geometry(obj_world), synthetic_donor.RIGID_QUANTIZE_INFO))

    def test_keep_world_on_another_bone_is_a_move_and_keep_offset_is_not(self):
        new_host = self.absolute[7]
        kept_world = cse.bone_local_geometry(
            self.local, self.triangles, cse.mat4_mul(self.arm_world, self.host), self.arm_world, new_host,
        )
        self.assertTrue(rre.positions_moved(self.local, kept_world, synthetic_donor.RIGID_QUANTIZE_INFO))
        moved_obj = cse.keep_offset_world_matrix(cse.mat4_mul(self.arm_world, self.host), self.arm_world, self.host, new_host)
        kept_offset = cse.bone_local_geometry(self.local, self.triangles, moved_obj, self.arm_world, new_host)
        self.assertFalse(rre.positions_moved(self.local, kept_offset, synthetic_donor.RIGID_QUANTIZE_INFO))


# --- face routing ---------------------------------------------------------------

def _donor(face_surfaces, drawable):
    return rre.DonorRigidSubmesh(
        index=1, position_quantize=59, normal_format=None, color_format=None,
        uv_formats={0: (2, 62)}, uv_mirrored=False,
        faces=[(0, 1, 2)] * len(face_surfaces), face_surfaces=list(face_surfaces),
        drawable_surfaces=dict(drawable), facial=False,
    )


def _tris(count):
    return [cse.Triangle((0, 1, 2), (3 * i, 3 * i + 1, 3 * i + 2), i) for i in range(count)]


class RouteFacesTests(unittest.TestCase):
    def setUp(self):
        self.donor = _donor(['sm1_ds5'] * 2 + ['sm1_ds9'] * 2, {'sm1_ds5': 5, 'sm1_ds9': 9})
        self.slots = [rre.SlotSurface('cap_a', 'sm1_ds5'), rre.SlotSurface('cap_b', 'sm1_ds9')]

    def test_unchanged_assignment_does_not_trigger(self):
        routing = rre.route_faces('cap', self.donor, _tris(4), [0, 0, 1, 1], self.slots, False)
        self.assertFalse(routing.changed)
        self.assertEqual(routing.table, ['sm1_ds5', 'sm1_ds9'])
        self.assertEqual(routing.indices, [0, 0, 1, 1])
        self.assertEqual(routing.counts_before, routing.counts_after)

    def test_one_face_moved_triggers(self):
        routing = rre.route_faces('cap', self.donor, _tris(4), [0, 1, 1, 1], self.slots, False)
        self.assertTrue(routing.changed)
        self.assertEqual(routing.counts_after, {'sm1_ds5': 1, 'sm1_ds9': 3})
        self.assertIn('sm1_ds5 2 -> 1', rre.surface_report('cap', routing))

    def test_changed_topology_takes_the_materials_as_they_are(self):
        routing = rre.route_faces('cap', self.donor, _tris(3), [0, 0, 1], self.slots, True)
        self.assertFalse(routing.changed)
        self.assertEqual(routing.indices, [0, 0, 1])

    def test_new_surface_triggers_and_is_listed(self):
        slots = self.slots + [rre.SlotSurface('brim', 'sm1_new0', new_surface=True, owner='sm1')]
        routing = rre.route_faces('cap', self.donor, _tris(4), [0, 0, 1, 2], slots, False)
        self.assertTrue(routing.changed)
        self.assertEqual(routing.new_keys, ['sm1_new0'])
        self.assertEqual(routing.table, ['sm1_ds5', 'sm1_ds9', 'sm1_new0'])

    def test_unused_new_surface_is_not_listed(self):
        slots = self.slots + [rre.SlotSurface('brim', 'sm1_new0', new_surface=True, owner='sm1')]
        routing = rre.route_faces('cap', self.donor, _tris(4), [0, 0, 1, 1], slots, False)
        self.assertEqual(routing.new_keys, [])
        self.assertFalse(routing.changed)

    def test_duplicate_slots_merge_with_a_warning(self):
        slots = self.slots + [rre.SlotSurface('cap_a.001', 'sm1_ds5')]
        routing = rre.route_faces('cap', self.donor, _tris(4), [0, 2, 1, 1], slots, False)
        self.assertFalse(routing.changed)
        self.assertEqual(routing.indices, [0, 0, 1, 1])
        self.assertEqual(len(routing.warnings), 1)
        self.assertIn('slots 0 and 2 both draw surface sm1_ds5', routing.warnings[0])

    def test_slot_hygiene_errors(self):
        with self.assertRaisesRegex(ValueError, r'an empty material slot \(1 face'):
            rre.route_faces('cap', self.donor, _tris(4), [0, 0, 1, 2], self.slots + [None], False)
        with self.assertRaisesRegex(ValueError, "'plain' has no Sluggies SurfaceId"):
            rre.route_faces('cap', self.donor, _tris(4), [0, 0, 1, 2], self.slots + [rre.SlotSurface('plain', None)], False)
        with self.assertRaisesRegex(ValueError, "'head' \\(sm2_ds9\\) is not a surface of this submesh"):
            rre.route_faces('cap', self.donor, _tris(4), [0, 0, 1, 2], self.slots + [rre.SlotSurface('head', 'sm2_ds9')], False)
        with self.assertRaisesRegex(ValueError, "'other' belongs to sm2, not sm1"):
            rre.route_faces('cap', self.donor, _tris(4), [0, 0, 1, 2],
                            self.slots + [rre.SlotSurface('other', 'sm2_new0', new_surface=True, owner='sm2')], False)
        with self.assertRaisesRegex(ValueError, 'invalid new-surface key'):
            rre.route_faces('cap', self.donor, _tris(4), [0, 0, 1, 2],
                            self.slots + [rre.SlotSurface('odd', 'sm1_ds77', new_surface=True, owner='sm1')], False)


# --- the entry, end to end through the patcher --------------------------------------

class BuildEntryTests(unittest.TestCase):
    def setUp(self):
        self.model = synthetic_donor.build_sluggie()['SluggiesModel']
        self.sub = self.model['Submeshes'][RIGID]
        self.donor = rre.donor_rigid_submesh(RIGID, self.sub)
        absolute = cse.bone_absolute_matrices(self.model['BoneHierarchy'])
        divisor = 1 << (synthetic_donor.RIGID_QUANTIZE_INFO & 0xF)
        raw = base64.b64decode(self.sub['VertexBuffer']['VertexBufferData'])
        self.local = [tuple(v / divisor for v in struct.unpack_from('>3h', raw, i * 6)) for i in range(len(raw) // 6)]
        faces = [(0, k, k + 1) for k in range(1, 7)]
        triangles = [cse.Triangle(face, (3 * i, 3 * i + 1, 3 * i + 2), i) for i, face in enumerate(faces)]
        self.geometry = cse.bone_local_geometry(self.local, triangles, absolute[OWNER], cse.IDENTITY4, absolute[OWNER])
        # Per-loop UVs from the donor channel (UV = vertex index * 64 / 16, * 32 / 16 at shift 0).
        uv_raw = base64.b64decode(self.sub['UVChannels'][0]['UVChannelData'])
        coords = [struct.unpack_from('>2h', uv_raw, i * 4) for i in range(len(uv_raw) // 4)]
        self.loop_uvs = [(coords[v][0], 1.0 - coords[v][1]) for face in faces for v in face]
        self.routing = rre.route_faces(
            'head', self.donor, self.geometry.triangles, [0] * 6, [rre.SlotSurface('head_mat', SURFACE)], False,
        )

    def _entry(self, **overrides):
        kwargs = dict(
            object_name='head', donor=self.donor, host_bone_id=OWNER, geometry=self.geometry,
            loop_normals=None, loop_uvs_by_channel={0: self.loop_uvs, 1: self.loop_uvs}, loop_colors=None,
            routing=self.routing, new_surfaces=[], reasons=['topology'], warnings=[],
        )
        kwargs.update(overrides)
        return rre.build_rigid_rebuild_entry(**kwargs)

    def test_entry_round_trips_through_the_patcher(self):
        entry = self._entry()
        self.assertEqual(entry['FacesCount'], 6)
        self.assertEqual(entry['FaceSurfaceTable'], [SURFACE])
        self.assertEqual(entry['UVChannels'][0]['UVChannelData'], entry['UVChannels'][1]['UVChannelData'])
        self.assertEqual(FieldCodec.decode_field(entry['VertexBufferData']),
                         base64.b64decode(self.sub['VertexBuffer']['VertexBufferData']))
        self.assertNotIn('NormalBufferData', entry)
        self.assertNotIn('ColorChannelData', entry)
        self.model['UseHammerspace'] = True
        self.sub['RigidRebuild'] = entry
        main._validate_rigid_rebuilds(self.model)
        with synthetic_donor.donor_environment() as env:
            data = env.reload()
            data['SluggiesModel']['UseHammerspace'] = True
            data['SluggiesModel']['Submeshes'][RIGID]['RigidRebuild'] = entry
            result = main.BuildModelBlock(data, main.SectionModes(gpl='build'), sluggie_path=env.sluggie_path)
            self.assertTrue(result.validation_report['valid'], result.validation_report.get('errors'))

    def test_mirrored_channel_ignores_separate_edits_with_a_warning(self):
        warnings = []
        edited = [(u + 0.5, v) for u, v in self.loop_uvs]
        entry = self._entry(loop_uvs_by_channel={0: self.loop_uvs, 1: edited}, warnings=warnings)
        self.assertEqual(entry['UVChannels'][0]['UVChannelData'], entry['UVChannels'][1]['UVChannelData'])
        self.assertEqual(len(warnings), 1)
        self.assertIn('mirrors channel 0', warnings[0])

    def test_missing_second_layer_falls_back_to_channel_0(self):
        warnings = []
        self.donor.uv_mirrored = False
        entry = self._entry(loop_uvs_by_channel={0: self.loop_uvs, 1: None}, warnings=warnings)
        self.assertEqual(len(entry['UVChannels']), 2)
        self.assertEqual(entry['UVChannels'][0]['UVChannelData'], entry['UVChannels'][1]['UVChannelData'])
        self.assertTrue(any('channel 1 not found' in w for w in warnings))
        # A mirrored donor needs no warning: channel 1 is channel 0 by rule.
        self.donor.uv_mirrored = True
        self._entry(loop_uvs_by_channel={0: self.loop_uvs, 1: None}, warnings=(quiet := []))
        self.assertEqual(quiet, [])

    def test_independent_channels_encode_separately(self):
        self.donor.uv_mirrored = False
        edited = [(u + 1.0, v) for u, v in self.loop_uvs]
        entry = self._entry(loop_uvs_by_channel={0: self.loop_uvs, 1: edited})
        self.assertNotEqual(entry['UVChannels'][0]['UVChannelData'], entry['UVChannels'][1]['UVChannelData'])

    def test_out_of_range_positions_name_the_donor_format(self):
        far = cse.bone_local_geometry(
            [(x + 40.0, y, z) for x, y, z in self.local], self.geometry.triangles,
            cse.IDENTITY4, cse.IDENTITY4, cse.IDENTITY4,
        )
        with self.assertRaisesRegex(ValueError, 'format 59, which a rebuilt donor submesh keeps'):
            self._entry(geometry=far)

    def test_colors_and_normals_use_the_donor_formats(self):
        self.donor.color_format = (3, 0)       # RGB565
        self.donor.normal_format = (3, 62)
        loops = [loop for tri in self.geometry.triangles for loop in tri.loops]
        colors = {loop: (1.0, 0.0, 0.0, 1.0) for loop in loops}
        normals = {loop: (0.0, 0.0, 1.0) for loop in loops}
        entry = self._entry(loop_colors=colors, loop_normals=normals)
        self.assertEqual(FieldCodec.decode_field(entry['ColorChannelData']), bytes.fromhex('f800'))
        self.assertEqual(FieldCodec.decode_field(entry['ColorFacesData']), bytes(36))
        self.assertEqual(len(FieldCodec.decode_field(entry['NormalBufferData'])), 6)   # one pooled normal

    def test_new_surfaces_and_reasons_are_carried(self):
        slots = [rre.SlotSurface('head_mat', SURFACE), rre.SlotSurface('brim', 'sm1_new0', new_surface=True, owner='sm1')]
        routing = rre.route_faces('head', self.donor, self.geometry.triangles, [0, 0, 0, 1, 1, 1], slots, False)
        surface = {'SurfaceKey': 'sm1_new0', 'MaterialName': 'brim', 'TemplateSource': 'builtin:rigid_spec_v1',
                   'TextureAssignment': {'DonorTextureIndex': 1}, 'SpecularStrength': 50}
        entry = self._entry(routing=routing, new_surfaces=[surface], reasons=['surfaces'])
        self.assertEqual(entry['NewSurfaces'], [surface])
        self.assertEqual(entry['Reason'], ['surfaces'])
        self.assertEqual(struct.unpack('>6H', FieldCodec.decode_field(entry['FaceSurfaceIndices'])), (0, 0, 0, 1, 1, 1))

    def test_color_entry_formats(self):
        self.assertEqual(rre.encode_color_entry(0, (1.0, 0.0, 0.0, 1.0)), bytes.fromhex('f800'))
        self.assertEqual(rre.encode_color_entry(48, (1.0, 0.0, 0.0, 0.0)), bytes.fromhex('f000'))
        self.assertEqual(rre.encode_color_entry(0x20, (0.0, 1.0, 0.0, 1.0)), bytes.fromhex('00ff00ff'))

    def test_strip_in_place_edits(self):
        sub = {
            'FacesDataEdited': 'x', 'FacesCountEdited': 1, 'FaceSurfaceIdsEdited': 'x', 'FacesData': 'keep',
            'VertexBuffer': {'VertexBufferDataEdited': 'x', 'VertexBufferData': 'keep'},
            'NormalBuffer': {'NormalBufferDataEdited': 'x', 'NormalFacesDataEdited': 'x', 'NormalBufferData': 'keep'},
            'UVChannels': [{'UVChannelDataEdited': 'x', 'UVFacesDataEdited': 'x', 'UVChannelData': 'keep'}],
            'ColorChannels': [{'ColorChannelDataEdited': 'x', 'ColorFacesDataEdited': 'x', 'ColorChannelData': 'keep'}],
        }
        rre.strip_in_place_edits(sub)
        self.assertEqual(sub, {
            'FacesData': 'keep', 'VertexBuffer': {'VertexBufferData': 'keep'},
            'NormalBuffer': {'NormalBufferData': 'keep'}, 'UVChannels': [{'UVChannelData': 'keep'}],
            'ColorChannels': [{'ColorChannelData': 'keep'}],
        })
        self.assertEqual(rre.mode_reason('cap', ['topology', 'surfaces']), 'rigid rebuild of cap (topology, surfaces)')


# --- exporter glue (ExportSluggies.py) --------------------------------------------------

class _Mat(dict):
    def __init__(self, name, **props):
        super().__init__(props)
        self.name = name


def _obj(materials, comp_count=3, custom=False):
    props = {'SluggiesCustomSubmesh': True} if custom else {}
    return SimpleNamespace(
        name='obj', material_slots=[SimpleNamespace(material=m) for m in materials],
        get=props.get, __contains__=lambda key: key in props,
    )


class ExporterGlueTests(unittest.TestCase):
    def setUp(self):
        self.helpers = _load(EXPORTER_PATH, {
            '_find_new_materials', 'NEW_SURFACE_BODY_MESSAGE', '_empty_image_node_errors',
            '_partial_move_error', '_connected_image_texture_nodes',
        }, {'struct': struct, '_to_bytes': lambda value: base64.b64decode(value), 'RigidRebuildExport': rre})
        self.rigid_sub = {'VertexBuffer': {'VertexBufferCompCount': 3},
                          'DisplayStates': [{'SurfaceId': 'sm1_ds5', 'FaceCount': 2}]}
        self.skinned_sub = {'VertexBuffer': {'VertexBufferCompCount': 6},
                            'DisplayStates': [{'SurfaceId': 'sm0_ds5', 'FaceCount': 2}, {'SurfaceId': 'sm0_ds6', 'FaceCount': 2}]}

    def test_new_surface_accepted_on_its_own_rigid_submesh_only(self):
        find = self.helpers['_find_new_materials']
        own = _Mat('brim', SurfaceId='sm1_new0', SluggiesNewSurface=True, SluggiesSurfaceOwner='sm1')
        foreign = _Mat('other', SurfaceId='sm2_new0', SluggiesNewSurface=True, SluggiesSurfaceOwner='sm2')
        donor = _Mat('cap', SurfaceId='sm1_ds5')
        self.assertEqual(find(_obj([donor, own]), self.rigid_sub, 1), [])
        self.assertEqual(find(_obj([donor, foreign]), self.rigid_sub, 1), [('other', 'belongs to sm2, not sm1')])
        body = _Mat('body_new', SurfaceId='sm0_new0', SluggiesNewSurface=True, SluggiesSurfaceOwner='sm0')
        self.assertEqual(find(_obj([body], comp_count=6), self.skinned_sub, 0),
                         [('body_new', self.helpers['NEW_SURFACE_BODY_MESSAGE'])])

    def test_empty_image_node_errors(self):
        node_with_image = SimpleNamespace(type='TEX_IMAGE', image=object(), inputs=())
        node_without = SimpleNamespace(type='TEX_IMAGE', image=None, inputs=())

        def material(name, nodes, **props):
            output = SimpleNamespace(type='OUTPUT_MATERIAL', is_active_output=True,
                                     inputs={'Surface': SimpleNamespace(links=[SimpleNamespace(from_node=n) for n in nodes])})
            mat = _Mat(name, **props)
            mat.use_nodes = True
            mat.node_tree = SimpleNamespace(nodes=[output] + list(nodes))
            return mat

        errors = self.helpers['_empty_image_node_errors']
        donor_ok = material('cap', [node_with_image], SurfaceId='sm1_ds5')
        donor_empty = material('cap2', [node_without], SurfaceId='sm1_ds6')
        donor_no_node = material('cap3', [], SurfaceId='sm1_ds7')
        new_no_node = material('brim', [], SurfaceId='sm1_new0', SluggiesNewSurface=True)
        self.assertEqual(errors([_obj([donor_ok, donor_no_node])]), [])
        found = errors([_obj([donor_empty, new_no_node])])
        self.assertEqual(len(found), 2)
        self.assertIn("'cap2' on obj has an Image Texture node without an image", found[0])
        self.assertIn("'brim' on obj has no image", found[1])

    def test_partial_move_error_on_the_body(self):
        partial = self.helpers['_partial_move_error']
        a, b = _Mat('a', SurfaceId='sm0_ds5'), _Mat('b', SurfaceId='sm0_ds6')
        surf_mat = {'sm0_ds5': a, 'sm0_ds6': b}

        def body(material_indices):
            obj = _obj([a, b])
            obj.data = SimpleNamespace(polygons=[SimpleNamespace(material_index=i) for i in material_indices])
            return obj

        states = self.skinned_sub['DisplayStates']
        self.assertIsNone(partial(body([0, 0, 1, 1]), states, surf_mat))      # unchanged
        self.assertIsNone(partial(body([1, 1, 1, 1]), states, surf_mat))      # complete move
        message = partial(body([0, 1, 1, 1]), states, surf_mat)
        self.assertIn("faces of surface 'sm0_ds5' were moved only partly", message)
        self.assertIn('rigid submeshes only', message)

    def test_execute_wiring(self):
        source = EXPORTER_PATH.read_text(encoding='utf-8')
        execute = source[source.index('    def execute(self, context):'):]
        self.assertLess(execute.index('_empty_image_node_errors('), execute.index('_rigid_rebuild_plan('))
        self.assertLess(execute.index('_rigid_rebuild_plan('), execute.index('ExportMode.model_level_reasons('))
        self.assertLess(execute.index('RigidRebuildExport.mode_reason('), execute.index('while True:'))
        self.assertIn('RigidRebuildExport.strip_in_place_edits(target_submesh)', execute)
        self.assertIn('target_submesh["RigidRebuild"] = entry', execute)
        self.assertIn('_partial_move_error(', execute)
        self.assertIn('_find_new_materials(obj, target_submesh, submesh_index)', execute)
        self.assertIn('both belong to', execute)
        # A rebuilt submesh never gets FaceSurfaceIdsEdited; the pop precedes the encode.
        self.assertLess(execute.index('target_submesh.pop("FaceSurfaceIdsEdited", None)\n                    written += 1'),
                        execute.index('_encode_face_surface_assignment('))

    def test_texture_resolver_skips_new_surface_materials(self):
        helpers = _load(EXPORTER_PATH, {'_resolve_material_texture_changes', '_connected_image_texture_nodes',
                                        '_template_signature', '_TEMPLATE_UNLENT_FIELDS'},
                        {'json': __import__('json'), 'os': __import__('os')})
        new_surface = _Mat('brim', SurfaceId='sm1_new0', SluggiesNewSurface=True)
        new_surface.use_nodes = False
        additions, assignments, changed = helpers['_resolve_material_texture_changes'](
            [(_obj([new_surface]), {})], [{'TextureIndex': 0, 'TextureFileName': 'a.png'}], 'tex',
        )
        self.assertEqual((additions, assignments, changed), ([], {}, []))


# --- importer glue (ImportSluggies.py) ---------------------------------------------------

class ImporterGlueTests(unittest.TestCase):
    def setUp(self):
        self.helpers = _load(IMPORTER_PATH, {
            '_submesh_owner_bones', '_surface_ranges_from_face_ids', '_rigid_rebuild_view', '_from_bytes',
            '_has_edited_data', 'GEO_ID_FREE',
        }, {'struct': struct, 'FieldCodec': FieldCodec, '_to_bytes': FieldCodec.decode_field})

    def test_owner_bones_follow_geo_id_edited(self):
        owners = self.helpers['_submesh_owner_bones']
        bones = [{'BoneId': 5, 'GeoId': 1, 'Skinned': False}, {'BoneId': 7, 'GeoId': 0, 'Skinned': True}]
        self.assertEqual(owners(bones, 1), (5, 5))
        bones[0]['GeoIdEdited'] = 0xFFFF
        bones[1]['GeoIdEdited'] = 1
        self.assertEqual(owners(bones, 1), (5, 7))
        self.assertEqual(owners(bones, 0), (None, None))

    def test_face_runs_keep_one_slot_per_surface(self):
        ranges = self.helpers['_surface_ranges_from_face_ids'](['a', 'a', 'b', 'a'])
        self.assertEqual(ranges, [('a', 0, 2), ('b', 2, 1), ('a', 3, 1)])

    def test_rigid_rebuild_view_promotes_the_entry(self):
        faces = struct.pack('>6H', 0, 1, 2, 0, 2, 3)
        rebuild = {
            'HostBoneId': 7, 'VertexBufferData': _b64(bytes(24)), 'FacesCount': 2, 'FacesData': _b64(faces),
            'UVChannels': [{'UVChannelIndex': 0, 'UVChannelData': _b64(bytes(8)), 'UVFacesData': _b64(faces)}],
            'NormalBufferData': _b64(bytes(6)), 'NormalFacesData': _b64(faces),
            'ColorChannelData': _b64(bytes(2)), 'ColorFacesData': _b64(faces),
            'FaceSurfaceTable': ['sm1_ds5', 'sm1_new0'], 'FaceSurfaceIndices': _b64(struct.pack('>2H', 1, 0)),
        }
        sub = {
            'RigidRebuild': rebuild, 'FacesCount': 6, 'FacesData': 'donor', 'FacesDataEdited': 'stale',
            'VertexBuffer': {'VertexBufferData': 'donor', 'VertexBufferDataEdited': 'stale'},
            'NormalBuffer': {'NormalBufferData': 'donor', 'NormalFacesData': 'donor'},
            'ColorChannels': [{'ColorChannelData': 'donor', 'ColorFacesData': 'donor'}],
            'UVChannels': [{'UVChannelIndex': 0, 'UVChannelData': 'donor', 'UVFacesData': 'donor', 'UVChannelDataEdited': 'stale'}],
        }
        self.assertTrue(self.helpers['_has_edited_data'](sub))
        view = self.helpers['_rigid_rebuild_view'](dict(sub), rebuild)
        self.assertEqual(view['FacesCount'], 2)
        self.assertEqual(view['FacesData'], rebuild['FacesData'])
        self.assertEqual(view['VertexBuffer'], {'VertexBufferData': rebuild['VertexBufferData']})
        self.assertNotIn('FacesDataEdited', view)
        self.assertEqual(view['NormalBuffer']['NormalFacesData'], rebuild['NormalFacesData'])
        self.assertEqual(view['ColorChannels'][0]['ColorChannelData'], rebuild['ColorChannelData'])
        self.assertEqual(view['UVChannels'][0]['UVChannelData'], rebuild['UVChannels'][0]['UVChannelData'])
        self.assertNotIn('UVChannelDataEdited', view['UVChannels'][0])
        self.assertEqual(view['_FaceSurfaceIds'], ['sm1_new0', 'sm1_ds5'])
        self.assertEqual(FieldCodec.decode_field(view['FaceTextureIndices']), bytes(4))

    def test_import_operator_wiring(self):
        source = IMPORTER_PATH.read_text(encoding='utf-8')
        self.assertIn('face_surface_ids=face_surface_ids', source)
        self.assertIn('_new_surface_materials(', source)
        self.assertIn('_apply_nonskinned_transform(edit_obj, i, bone_list, abs_bone_mats, effective_owner)', source)
        self.assertIn("def add_vertex_groups(obj, submesh_index, bone_list, arm_obj, owner_bone_id=None)", source)


if __name__ == '__main__':
    unittest.main()
