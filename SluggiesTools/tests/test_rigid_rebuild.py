"""RigidRebuild: whole-blob rebuild of a donor rigid submesh
(PLAN_EditRigidMeshes.md Phases 1-3), on the synthetic donor."""
import base64
import copy
import pathlib
import struct
import sys
import unittest
from unittest import mock

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT_DIR / 'SluggiesTools'
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
ADDON_DIR = ROOT_DIR / 'BlenderAddonSrc'
for import_path in (ROOT_DIR, TOOLS_DIR, HAMMERSPACE_DIR, ADDON_DIR, pathlib.Path(__file__).resolve().parent):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import ExportMode  # noqa: E402
import FacialPoseRebuild as fpr  # noqa: E402
import GeometryRebuild  # noqa: E402
import HammerspaceMain as main  # noqa: E402
import start  # noqa: E402
import synthetic_donor  # noqa: E402
import test_skinned_rebuild  # noqa: E402
import texture_helper  # noqa: E402
from binfmt import color_entry_size, comp_size, decode_field  # noqa: E402
from drawlist import decodeDrawList  # noqa: E402

RIGID = synthetic_donor.RIGID_OWNER_SUBMESH          # 1
OWNER = synthetic_donor.RIGID_OWNER_BONE             # 5
FREE_BONE = 7
SURFACE = f'sm{RIGID}_ds{synthetic_donor.SURFACE_STATE_INDEX}'   # sm1_ds5
NEW_KEY = f'sm{RIGID}_new0'


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode('ascii')


def _u16s(values) -> str:
    return _b64(struct.pack(f'>{len(values)}H', *values))


def _fan(vertex_count: int) -> list[tuple[int, int, int]]:
    return [(0, corner, corner + 1) for corner in range(1, vertex_count - 1)]


def _identity_rebuild(model: dict, host_bone: int = OWNER) -> dict:
    """A RigidRebuild carrying the donor rigid submesh's own data."""
    sub = model['Submeshes'][RIGID]
    faces = sub['FacesCount']
    return {
        'HostBoneId': host_bone,
        'VertexBufferData': sub['VertexBuffer']['VertexBufferData'],
        'UVChannels': [
            {
                'UVChannelIndex': uv['UVChannelIndex'],
                'UVChannelData': uv['UVChannelData'],
                'UVFacesData': uv['UVFacesData'],
            }
            for uv in sub['UVChannels']
        ],
        'FacesCount': faces,
        'FacesData': sub['FacesData'],
        'FaceSurfaceTable': [SURFACE],
        'FaceSurfaceIndices': _u16s([0] * faces),
        'Reason': ['topology'],
    }


def _resized_rebuild(model: dict, vertex_count: int) -> dict:
    """A rebuild with *vertex_count* fan vertices, one UV per vertex."""
    faces = _fan(vertex_count)
    flat = [index for face in faces for index in face]
    positions = struct.pack(
        f'>{vertex_count * 3}h',
        *(value for vertex in range(vertex_count) for value in (vertex % 61 * 7 - 200, vertex % 13 * 15, vertex % 7 * 29)),
    )
    uvs = struct.pack(f'>{vertex_count * 2}h', *(value for vertex in range(vertex_count) for value in (vertex, vertex * 2)))
    rebuild = _identity_rebuild(model)
    rebuild.update({
        'VertexBufferData': _b64(positions),
        'FacesCount': len(faces),
        'FacesData': _u16s(flat),
        'FaceSurfaceIndices': _u16s([0] * len(faces)),
        'UVChannels': [
            {'UVChannelIndex': channel, 'UVChannelData': _b64(uvs), 'UVFacesData': _u16s(flat)}
            for channel in range(synthetic_donor.UV_CHANNEL_COUNT)
        ],
    })
    return rebuild


def _with_normals(data: dict) -> None:
    """Give the donor rigid submesh a 3 x int16 normal array (the .sluggie
    dict only; a rebuilt blob carries the rebuild's own normals)."""
    sub = data['SluggiesModel']['Submeshes'][RIGID]
    count = synthetic_donor.RIGID_VERTEX_COUNT
    sub['NormalBuffer'] = {
        'NormalDataPtrFieldOffset': '0x0', 'NormalCountFieldOffset': '0x0', 'NormalBufferOffset': '0x0',
        'NormalBufferLength': count * 6, 'NormalBufferCompCount': 3,
        'NormalBufferQuantizeInfo': 62, 'NormalAmbientPct': 0.0,
        'NormalBufferData': _b64(struct.pack(f'>{count * 3}h', *(16384 if k % 3 == 2 else 0 for k in range(count * 3)))),
        'NormalFacesData': sub['FacesData'],
    }


def _resized_rebuild_with_normals(model: dict, vertex_count: int) -> dict:
    rebuild = _resized_rebuild(model, vertex_count)
    normals = struct.pack(f'>{vertex_count * 3}h', *(value for vertex in range(vertex_count) for value in (vertex * 100, 0, 16000)))
    rebuild['NormalBufferData'] = _b64(normals)
    rebuild['NormalFacesData'] = rebuild['FacesData']
    return rebuild


FACIAL_POSES = 3


def _rigid_facial_section() -> bytes:
    """A ptr7 section with one object on the donor rigid submesh: 3 x int16
    positions on vertices 0-1 and 3 x int16 normals on entries 0 and 2."""
    header = (struct.pack('>HHHHIII', FACIAL_POSES, 1, 2, 0, 0x34, 0x14, 0)
              + bytes([0, RIGID, 1, 0x32]) + bytes(12)
              + bytes([0, RIGID, 2, 0x32]) + bytes(12))
    head = fpr.FacialObject(FACIAL_POSES, [
        fpr.FacialAttribute(bytes([RIGID, 1, 3, 2]), fpr.runs_from_indices([0, 1]),
                            [struct.pack('>6h', *([pose] * 6)) for pose in range(FACIAL_POSES)]),
        fpr.FacialAttribute(bytes([RIGID, 2, 3, 2]), fpr.runs_from_indices([0, 2]),
                            [struct.pack('>6h', *([10 + pose] * 6)) for pose in range(FACIAL_POSES)]),
    ])
    return fpr.serialize_section(header, [head])


def _rigid_facial_poses(vertices, normal_entries=None, pose_count: int = FACIAL_POSES) -> list:
    entry = {
        'ObjectIndex': 0, 'Vertices': _u16s(vertices),
        'PoseDeltas': [
            _b64(struct.pack(f'>{3 * len(vertices)}h', *(pose * 1000 + vertex * 10 + c for vertex in vertices for c in range(3))))
            for pose in range(1, pose_count)
        ],
    }
    if normal_entries is not None:
        entry['NormalEntries'] = _u16s(normal_entries)
        entry['NormalPoseDeltas'] = [
            _b64(struct.pack(f'>{3 * len(normal_entries)}h', *(pose * 100 + n for n in normal_entries for _c in range(3))))
            for pose in range(1, pose_count)
        ]
    return [entry]


def _new_surface(template='builtin:rigid_spec_v1', texture=None, **extra) -> dict:
    entry = {
        'SurfaceKey': NEW_KEY,
        'MaterialName': 'brim',
        'TemplateSource': template,
        'TextureAssignment': texture if texture is not None else {'DonorTextureIndex': 1},
    }
    entry.update(extra)
    return entry


# --- block readers ----------------------------------------------------------

def _gpl_section(block: bytes) -> bytes:
    gpl, act = struct.unpack_from('>II', block, 4)
    return block[gpl:act]


def _descriptors(gpl: bytes) -> list[tuple[int, int]]:
    count, pointer = struct.unpack_from('>II', gpl, 0x0C)
    return [struct.unpack_from('>II', gpl, pointer + 8 * index) for index in range(count)]


def _blob(gpl: bytes, index: int) -> dict:
    start, name_ptr = _descriptors(gpl)[index]
    pos_h, col_h, uv_h, nor_h, dsp_h = struct.unpack_from('>5I', gpl, start)

    def array(header, stride_of):
        pointer, count, quantize, comps = struct.unpack_from('>IHBB', gpl, start + header)
        if not count:
            return None
        return quantize, comps, gpl[start + pointer:start + pointer + count * stride_of(quantize, comps)]

    ds_ptr, n_ds = struct.unpack_from('>IH', gpl, start + dsp_h + 4)
    records, lists = [], []
    for k in range(n_ds):
        record = start + ds_ptr + k * 0x10
        records.append(gpl[record:record + 8])
        pl_ptr, pl_size = struct.unpack_from('>II', gpl, record + 8)
        lists.append(gpl[start + pl_ptr:start + pl_ptr + pl_size] if pl_size else b'')
    return {
        'start': start,
        'name': gpl[name_ptr:gpl.index(b'\x00', name_ptr)],
        'positions': array(pos_h, lambda q, c: comp_size(q) * c),
        'color': array(col_h, lambda q, c: color_entry_size(q)) if col_h else None,
        'records': records,
        'lists': lists,
    }


def _record_faces(blob: dict, k: int) -> list[tuple[int, int, int]]:
    setting = None
    for record in blob['records'][:k + 1]:
        if record[0] == 3:
            setting = int.from_bytes(record[4:8], 'big')
    faces = decodeDrawList(blob['lists'][k], main._custom_submesh_type3_descriptors(setting))
    # Strips reorder and rotate the triangles; compare them as sorted cyclic
    # rotations starting at the smallest corner (winding kept).
    return sorted(_rotated(tuple(vertex['position'] for vertex in face)) for face in faces)


def _rotated(tri: tuple[int, int, int]) -> tuple[int, int, int]:
    k = min(range(3), key=lambda i: tri[i])
    return (tri[k], tri[(k + 1) % 3], tri[(k + 2) % 3])


def _type3(blob: dict, k: int) -> int:
    return int.from_bytes(blob['records'][k][4:8], 'big')


# --- validator ----------------------------------------------------------------

class RigidRebuildValidatorTests(unittest.TestCase):
    def setUp(self):
        self.model = synthetic_donor.build_sluggie()['SluggiesModel']
        self.model['UseHammerspace'] = True
        self.sub = self.model['Submeshes'][RIGID]
        self.sub['RigidRebuild'] = _identity_rebuild(self.model)

    def _refused(self, pattern: str):
        with self.assertRaisesRegex(ValueError, pattern):
            main._validate_rigid_rebuilds(self.model)

    def test_identity_entry_passes(self):
        main._validate_rigid_rebuilds(self.model)

    def test_requires_hammerspace(self):
        self.model['UseHammerspace'] = False
        self._refused('UseHammerspace')

    def test_skinned_submesh_is_refused(self):
        self.model['Submeshes'][0]['RigidRebuild'] = _identity_rebuild(self.model)
        self._refused(r'sub0: .*VertexBufferCompCount 3')

    def test_float_position_format_is_refused(self):
        self.sub['VertexBuffer']['VertexBufferQuantizeInfo'] = 0x40
        self._refused('not int16')

    def test_in_place_edit_fields_are_refused(self):
        self.sub['VertexBuffer']['VertexBufferDataEdited'] = self.sub['VertexBuffer']['VertexBufferData']
        self.sub['UVChannels'][0]['UVChannelDataEdited'] = self.sub['UVChannels'][0]['UVChannelData']
        self._refused('VertexBuffer.VertexBufferDataEdited, UVChannels.UVChannelDataEdited')

    def test_shader_mode_and_specular_edits_are_allowed(self):
        self.sub['DisplayStates'][5]['ShaderModeEdited'] = 'Spec'
        self.sub['DisplayStates'][5]['DisplayStateParamBytesEdited'] = '100064'
        main._validate_rigid_rebuilds(self.model)

    def test_facial_pose_submesh_without_poses_passes(self):
        # The patcher neutralizes the objects (older export, or no shape keys).
        self.model['FacialPoseData'] = {'Objects': [{'ObjectIndex': 0, 'SubmeshIndex': RIGID}]}
        main._validate_rigid_rebuilds(self.model)

    def test_facial_poses_are_checked_against_the_donor_object(self):
        _with_normals({'SluggiesModel': self.model})
        self.model['FacialPoseData'] = test_skinned_rebuild._facial_data_from_section(_rigid_facial_section())
        rebuild = self.sub['RigidRebuild'] = _resized_rebuild_with_normals(self.model, 5)
        rebuild['FacialPoses'] = _rigid_facial_poses([1, 3], [2])
        main._validate_rigid_rebuilds(self.model)
        rebuild['FacialPoses'] = _rigid_facial_poses([1, 3])            # normals may be left out (neutralized)
        main._validate_rigid_rebuilds(self.model)
        rebuild['FacialPoses'] = []
        self._refused(r'lacks facial object\(s\) \[0\]')
        rebuild['FacialPoses'] = _rigid_facial_poses([3, 1])
        self._refused('Vertices must be strictly ascending')
        rebuild['FacialPoses'] = _rigid_facial_poses([5])
        self._refused('vertex 5 is outside the 5 vertices')
        rebuild['FacialPoses'] = _rigid_facial_poses([1], [5])
        self._refused('normal entry 5 is outside the 5 normal entries')
        rebuild['FacialPoses'] = _rigid_facial_poses([1], pose_count=2)
        self._refused(r'PoseDeltas must hold 2 arrays')
        rebuild['FacialPoses'] = _rigid_facial_poses([1], [1])
        rebuild['FacialPoses'][0]['NormalPoseDeltas'][0] = 'AAA='
        self._refused(r'NormalPoseDeltas\[0\] is 2 bytes, expected 1 x 6')
        rebuild['FacialPoses'] = _rigid_facial_poses([1]) + [{'ObjectIndex': 1, 'Vertices': _u16s([0]), 'PoseDeltas': []}]
        self._refused('facial object 1, which the model does not have')
        del self.sub['NormalBuffer']
        rebuild = self.sub['RigidRebuild'] = _resized_rebuild(self.model, 5)
        rebuild['FacialPoses'] = _rigid_facial_poses([1], [1])
        self._refused('has a normal attribute on submesh 1 but the rebuild has no normal array')
        del self.model['FacialPoseData']['Objects'][0]['Normal']
        self._refused('carries NormalEntries but the rebuilt submesh has no normal array')
        rebuild['FacialPoses'] = _rigid_facial_poses([1])
        main._validate_rigid_rebuilds(self.model)

    def test_host_bone_must_be_the_effective_owner(self):
        self.sub['RigidRebuild']['HostBoneId'] = FREE_BONE
        self._refused(rf'HostBoneId {FREE_BONE} does not match the effective owner bone\(s\) \[{OWNER}\]')

    def test_host_bone_follows_a_geo_id_edited_retarget(self):
        bones = {int(b['BoneId']): b for b in self.model['BoneHierarchy']}
        bones[OWNER]['GeoIdEdited'] = 0xFFFF
        bones[FREE_BONE]['GeoIdEdited'] = RIGID
        self.sub['RigidRebuild']['HostBoneId'] = FREE_BONE
        main._validate_rigid_rebuilds(self.model)
        self.sub['RigidRebuild']['HostBoneId'] = OWNER
        self._refused(rf'effective owner bone\(s\) \[{FREE_BONE}\]')

    def test_unknown_host_bone_is_refused(self):
        self.sub['RigidRebuild']['HostBoneId'] = 99
        self._refused('host bone 99 does not exist')

    def test_faces_must_match_count_and_vertices(self):
        self.sub['RigidRebuild']['FacesCount'] = 7
        self._refused('FacesData length')
        self.sub['RigidRebuild']['FacesCount'] = 6
        self.sub['RigidRebuild']['FacesData'] = _u16s([0, 1, 9] * 6)
        self._refused('face index 9 is out of range')

    def test_surface_table_keys_must_exist(self):
        self.sub['RigidRebuild']['FaceSurfaceTable'] = ['sm1_ds2']
        self._refused("FaceSurfaceTable key 'sm1_ds2' is neither")

    def test_surface_indices_in_range_and_counted(self):
        self.sub['RigidRebuild']['FaceSurfaceIndices'] = _u16s([0, 0, 0, 0, 0, 3])
        self._refused('FaceSurfaceIndices value 3 is out of range')
        self.sub['RigidRebuild']['FaceSurfaceIndices'] = _u16s([0] * 5)
        self._refused('holds 5 entries for FacesCount 6')

    def test_new_surface_rules(self):
        rebuild = self.sub['RigidRebuild']
        rebuild['FaceSurfaceTable'] = [SURFACE, NEW_KEY]
        rebuild['FaceSurfaceIndices'] = _u16s([0, 0, 0, 1, 1, 1])
        rebuild['NewSurfaces'] = [_new_surface()]
        main._validate_rigid_rebuilds(self.model)

        rebuild['NewSurfaces'] = [_new_surface(SurfaceKey='sm0_new0')]
        rebuild['FaceSurfaceTable'] = [SURFACE, 'sm0_new0']
        self._refused(f'key must match sm{RIGID}_new<K>')

        rebuild['FaceSurfaceTable'] = [SURFACE, NEW_KEY]
        rebuild['NewSurfaces'] = [_new_surface()]
        rebuild['FaceSurfaceIndices'] = _u16s([0] * 6)
        self._refused('is used by no face')

        rebuild['FaceSurfaceIndices'] = _u16s([0, 0, 0, 1, 1, 1])
        rebuild['NewSurfaces'] = [_new_surface(template='builtin:rigid_shdw_v1')]
        self._refused('not verified in game')
        rebuild['NewSurfaces'] = [_new_surface(template='rigid:sm9_ds9')]
        self._refused('does not exist on a rigid submesh')
        rebuild['NewSurfaces'] = [_new_surface(texture={})]
        self._refused('exactly one of DonorTextureIndex or AdditionalTextureFileName')
        rebuild['NewSurfaces'] = [_new_surface(texture={'AdditionalTextureFileName': 'new.png'})]
        self._refused("'new.png' has no AdditionalTextureDescriptors entry")
        self.model['AdditionalTextureDescriptors'] = [{'TextureFileName': 'new.png', 'TemplateTextureIndex': 0}]
        main._validate_rigid_rebuilds(self.model)
        rebuild['NewSurfaces'] = [_new_surface(SpecularStrength=300)]
        self._refused('SpecularStrength 300')

    def test_uv_channel_set_must_match_the_donor(self):
        del self.sub['RigidRebuild']['UVChannels'][1]
        self._refused(r'UVChannels \[0\] must be the donor channel set \[0, 1\]')

    def test_normals_only_when_the_donor_has_them(self):
        self.sub['RigidRebuild']['NormalBufferData'] = _b64(bytes(6))
        self.sub['RigidRebuild']['NormalFacesData'] = _u16s([0] * 18)
        self._refused('donor submesh has no NormalBuffer')

    def test_combined_with_custom_submesh_on_a_freed_bone(self):
        """G10: a bone a rigid submesh leaves in this export is claimable."""
        bones = {int(b['BoneId']): b for b in self.model['BoneHierarchy']}
        bones[OWNER]['GeoIdEdited'] = 0xFFFF
        bones[FREE_BONE]['GeoIdEdited'] = RIGID
        self.sub['RigidRebuild']['HostBoneId'] = FREE_BONE
        self.model['CustomSubmeshes'] = [_custom_submesh(self.model, host_bone=OWNER)]
        main._validate_rigid_rebuilds(self.model)
        main._validate_custom_submeshes(self.model)
        self.model['CustomSubmeshes'][0]['HostBoneId'] = FREE_BONE
        with self.assertRaisesRegex(ValueError, f'host bone {FREE_BONE} already owns submesh {RIGID}'):
            main._validate_custom_submeshes(self.model)


def _custom_submesh(model: dict, host_bone: int) -> dict:
    sub = model['Submeshes'][RIGID]
    return {
        'CustomSubmeshId': 'custom0',
        'MeshName': 'cube',
        'HostBoneId': host_bone,
        'TemplateSource': 'builtin:rigid_spec_v1',
        'VertexBufferData': sub['VertexBuffer']['VertexBufferData'],
        'UVChannels': [
            {'UVChannelIndex': uv['UVChannelIndex'], 'UVChannelData': uv['UVChannelData'], 'UVFacesData': uv['UVFacesData']}
            for uv in sub['UVChannels']
        ],
        'FacesCount': sub['FacesCount'],
        'FacesData': sub['FacesData'],
        'TextureAssignment': {'DonorTextureIndex': 0},
    }


# --- builder --------------------------------------------------------------------

class RigidRebuildBuildTests(unittest.TestCase):
    def setUp(self):
        self.env = self.enterContext(synthetic_donor.donor_environment())
        self.data = self.env.reload()
        self.model = self.data['SluggiesModel']
        self.model['UseHammerspace'] = True
        self.donor_gpl = main.CloneGPL(self.env.model_offset, self.env.model_length)

    def _build(self, **modes):
        result = main.BuildModelBlock(
            self.data, main.SectionModes(gpl='build', **modes), sluggie_path=self.env.sluggie_path,
        )
        self.assertTrue(result.validation_report['valid'], result.validation_report.get('errors'))
        return result, _gpl_section(result.block)

    def _facial_build(self, poses):
        """Build with the donor rigid submesh carrying facial poses (one
        object, positions and normals) and a 5-vertex rebuild; returns the
        rebuilt ptr7 section and the GPL."""
        self.env = self.enterContext(synthetic_donor.donor_environment(_with_normals))
        self.data = self.env.reload()
        self.model = self.data['SluggiesModel']
        self.model['UseHammerspace'] = True
        section = _rigid_facial_section()
        self.model['FacialPoseData'] = test_skinned_rebuild._facial_data_from_section(section)
        self.model['TrailingSections'] = [
            {'HeaderFieldOffset': '0x18', 'OriginalPtr': '0x1000', 'Length': len(section), 'Data': _b64(section)},
        ]
        rebuild = _resized_rebuild_with_normals(self.model, 5)
        if poses is not None:
            rebuild['FacialPoses'] = poses
        self.model['Submeshes'][RIGID]['RigidRebuild'] = rebuild
        donor_header = bytearray(main.CloneHEADER(self.env.model_offset))
        struct.pack_into('>I', donor_header, 0x18, 0x1000)
        with mock.patch.object(main, 'CloneHEADER', return_value=bytes(donor_header)):
            result, gpl = self._build()
        ptr7 = struct.unpack_from('>I', result.block, 0x18)[0]
        self.assertNotEqual(ptr7, 0)
        self.assertEqual([e for e in result.validation_report['errors'] if 'ptr7' in e], [])
        return result.block[ptr7:], gpl, rebuild

    def test_facial_poses_follow_the_rebuilt_rigid_arrays(self):
        rebuilt, gpl, rebuild = self._facial_build(_rigid_facial_poses([1, 3], [2, 4]))
        self.assertEqual(struct.unpack_from('>HHH', rebuilt, 0), (FACIAL_POSES, 1, 2))      # descriptors kept
        vertices, poses = test_skinned_rebuild._parse_facial_runs(rebuilt, 0)
        self.assertEqual(vertices, [1, 3])
        positions = base64.b64decode(rebuild['VertexBufferData'])
        self.assertEqual(poses[0], positions[6:12] + positions[18:24])
        self.assertEqual(struct.unpack('>6h', poses[1]), (1010, 1011, 1012, 1030, 1031, 1032))
        # The normal attribute: second record of the object.
        table = struct.unpack_from('>I', rebuilt, 8)[0]
        _pc, _ac, record_size, data = struct.unpack_from('>HHII', rebuilt, table)
        record = data + record_size
        run = struct.unpack_from('>I', rebuilt, record + 8)[0]
        self.assertEqual(rebuilt[run:run + 12], struct.pack('>6H', 2, 1, 4, 1, 0, 0))
        normals = base64.b64decode(rebuild['NormalBufferData'])
        pose_offsets = struct.unpack_from(f'>{FACIAL_POSES}I', rebuilt, record + 0x0C)
        self.assertEqual(rebuilt[pose_offsets[0]:pose_offsets[0] + 12], normals[12:18] + normals[24:30])
        self.assertEqual(struct.unpack('>6h', rebuilt[pose_offsets[1]:pose_offsets[1] + 12]), (102,) * 3 + (104,) * 3)
        blob = _blob(gpl, RIGID)
        self.assertEqual(blob['positions'][2], positions)

    def test_rebuild_without_facial_poses_neutralizes_the_objects(self):
        rebuilt, _gpl, rebuild = self._facial_build(None)
        vertices, poses = test_skinned_rebuild._parse_facial_runs(rebuilt, 0)
        self.assertEqual(vertices, [0])
        self.assertEqual(poses, [base64.b64decode(rebuild['VertexBufferData'])[:6], bytes(6), bytes(6)])

    def test_identity_rebuild_replaces_only_the_rigid_descriptor(self):
        self.model['Submeshes'][RIGID]['RigidRebuild'] = _identity_rebuild(self.model)
        result, gpl = self._build()

        # Everything before the new blob is the donor section, apart from the
        # repointed descriptor; the old blob is still there, unreferenced.
        donor_descriptors = _descriptors(self.donor_gpl)
        new_descriptors = _descriptors(gpl)
        self.assertEqual(new_descriptors[0], donor_descriptors[0])
        self.assertNotEqual(new_descriptors[RIGID], donor_descriptors[RIGID])
        self.assertGreaterEqual(new_descriptors[RIGID][0], len(self.donor_gpl))
        self.assertEqual(new_descriptors[RIGID][0] % 32, 0)
        _count, table = struct.unpack_from('>II', gpl, 0x0C)
        slot = table + RIGID * 8
        self.assertEqual(gpl[:slot], self.donor_gpl[:slot])
        self.assertEqual(gpl[slot + 8:len(self.donor_gpl)], self.donor_gpl[slot + 8:])

        donor, rebuilt = _blob(self.donor_gpl, RIGID), _blob(gpl, RIGID)
        self.assertEqual(rebuilt['name'], donor['name'])
        self.assertEqual(rebuilt['positions'], donor['positions'])
        self.assertEqual(rebuilt['records'], donor['records'])
        self.assertEqual(_record_faces(rebuilt, 5), _fan(synthetic_donor.RIGID_VERTEX_COUNT))
        self.assertEqual(rebuilt['lists'][5][0], 0x98)      # a fan strips into one run
        self.assertEqual(len(rebuilt['lists'][5]) % 32, 0)
        self.assertEqual(result.validation_report['validator_facts']['act_geo_id_owners'][RIGID], [OWNER])

    def test_more_than_256_vertices_widen_the_type3_indices(self):
        self.model['Submeshes'][RIGID]['RigidRebuild'] = _resized_rebuild(self.model, 300)
        _result, gpl = self._build()
        rebuilt = _blob(gpl, RIGID)
        self.assertEqual(_type3(_blob(self.donor_gpl, RIGID), 3), 0x2808)
        self.assertEqual(_type3(rebuilt, 3), 0x3C0C)
        self.assertEqual(len(rebuilt['positions'][2]) // 6, 300)
        self.assertEqual(_record_faces(rebuilt, 5), _fan(300))

    def test_partial_move_appends_a_canonical_group(self):
        rebuild = _identity_rebuild(self.model)
        rebuild['FaceSurfaceTable'] = [SURFACE, NEW_KEY]
        rebuild['FaceSurfaceIndices'] = _u16s([0, 0, 0, 1, 1, 1])
        rebuild['NewSurfaces'] = [_new_surface(SpecularStrength=50)]
        self.model['Submeshes'][RIGID]['RigidRebuild'] = rebuild
        _result, gpl = self._build()

        donor, rebuilt = _blob(self.donor_gpl, RIGID), _blob(gpl, RIGID)
        self.assertEqual(rebuilt['records'][:6], donor['records'])
        appended = [(r[0], r[1:4].hex(), r[4:8]) for r in rebuilt['records'][6:]]
        self.assertEqual(appended, [
            (1, '000008', bytes.fromhex('11110001')),   # layer 0 rebound to texture 1
            (1, '000000', bytes.fromhex('11002001')),   # the host's own specular binding
            (4, '000000', bytes.fromhex('ffffff10')),
            (3, '000000', bytes.fromhex('00002808')),   # this submesh's attribute set
            (6, '010000', bytes.fromhex('00000374')),
            (7, '320064', b'Spec'),                     # specular strength 50
        ])
        fan = _fan(synthetic_donor.RIGID_VERTEX_COUNT)
        self.assertEqual(_record_faces(rebuilt, 5), fan[:3])
        self.assertEqual(_record_faces(rebuilt, 11), fan[3:])

    def test_emptied_donor_surface_keeps_its_record_with_a_null_list(self):
        rebuild = _identity_rebuild(self.model)
        rebuild['FaceSurfaceTable'] = [SURFACE, NEW_KEY]
        rebuild['FaceSurfaceIndices'] = _u16s([1] * 6)
        rebuild['NewSurfaces'] = [_new_surface()]
        self.model['Submeshes'][RIGID]['RigidRebuild'] = rebuild
        _result, gpl = self._build()
        rebuilt = _blob(gpl, RIGID)
        self.assertEqual(rebuilt['records'][5], _blob(self.donor_gpl, RIGID)['records'][5])
        self.assertEqual(rebuilt['lists'][5], b'')
        self.assertEqual(len(_record_faces(rebuilt, 11)), 6)

    def test_rigid_template_group_uses_the_template_surface_state(self):
        rebuild = _identity_rebuild(self.model)
        rebuild['FaceSurfaceTable'] = [SURFACE, NEW_KEY]
        rebuild['FaceSurfaceIndices'] = _u16s([0, 0, 0, 1, 1, 1])
        rebuild['NewSurfaces'] = [_new_surface(template=f'rigid:{SURFACE}', texture={'DonorTextureIndex': 0})]
        self.model['Submeshes'][RIGID]['RigidRebuild'] = rebuild
        _result, gpl = self._build()
        rebuilt = _blob(gpl, RIGID)
        self.assertEqual(len(rebuilt['records']), 12)
        self.assertEqual(rebuilt['records'][11][1:4].hex(), '320064')   # the donor surface's own pad
        self.assertEqual(rebuilt['records'][6][4:8], bytes.fromhex('11110000'))

    def test_retarget_moves_the_submesh_to_its_new_host(self):
        bones = {int(b['BoneId']): b for b in self.model['BoneHierarchy']}
        bones[OWNER]['GeoIdEdited'] = 0xFFFF
        bones[FREE_BONE]['GeoIdEdited'] = RIGID
        self.model['Submeshes'][RIGID]['RigidRebuild'] = _identity_rebuild(self.model, host_bone=FREE_BONE)
        result, _gpl = self._build()
        self.assertEqual(result.validation_report['validator_facts']['act_geo_id_owners'][RIGID], [FREE_BONE])

    def test_host_bone_without_matching_retarget_is_refused(self):
        self.model['Submeshes'][RIGID]['RigidRebuild'] = _identity_rebuild(self.model, host_bone=FREE_BONE)
        with self.assertRaisesRegex(ValueError, 'does not match the effective owner'):
            self._build()

    def test_rebuild_requires_gpl_build_mode(self):
        self.model['Submeshes'][RIGID]['RigidRebuild'] = _identity_rebuild(self.model)
        with self.assertRaisesRegex(ValueError, "RigidRebuild requires SectionModes.gpl='build'"):
            main.BuildModelBlock(self.data, main.SectionModes(), sluggie_path=self.env.sluggie_path)

    def test_rebuild_and_appended_custom_submesh_build_together(self):
        self.model['Submeshes'][RIGID]['RigidRebuild'] = _resized_rebuild(self.model, 12)
        self.model['CustomSubmeshes'] = [_custom_submesh(self.model, host_bone=FREE_BONE)]
        result, gpl = self._build()
        layout = result.validation_report['validator_facts']['gpl_submesh_layout']
        self.assertEqual(len(layout), 3)
        self.assertEqual(_record_faces(_blob(gpl, RIGID), 5), _fan(12))
        self.assertEqual(_blob(gpl, 2)['name'], b'cube')
        owners = result.validation_report['validator_facts']['act_geo_id_owners']
        self.assertEqual((owners[RIGID], owners[2]), ([OWNER], [FREE_BONE]))

    # --- Phase 11 combined cases (PLAN_EditRigidMeshes.md) ---------------------

    def test_rebuild_and_custom_submesh_share_one_appended_png(self):
        """A cap-style new surface and a custom submesh on the same new PNG:
        one AdditionalTextureDescriptors entry, two bindings to its index."""
        from PIL import Image
        Image.new('RGBA', (4, 4), (0, 0, 255, 255)).save(self.env.directory / 'tex' / 'shared.png')
        self.model.update({
            'ReimportTextures': True,
            'AdditionalTextureDescriptors': [{'TextureFileName': 'shared.png', 'TemplateTextureIndex': 0}],
        })
        rebuild = _identity_rebuild(self.model)
        rebuild['FaceSurfaceTable'] = [SURFACE, NEW_KEY]
        rebuild['FaceSurfaceIndices'] = _u16s([0, 0, 0, 1, 1, 1])
        rebuild['NewSurfaces'] = [_new_surface(texture={'AdditionalTextureFileName': 'shared.png'})]
        self.model['Submeshes'][RIGID]['RigidRebuild'] = rebuild
        custom = _custom_submesh(self.model, host_bone=FREE_BONE)
        custom['TextureAssignment'] = {'AdditionalTextureFileName': 'shared.png'}
        self.model['CustomSubmeshes'] = [custom]

        new_index = synthetic_donor.TEXTURE_COUNT
        plan = texture_helper.TexturePlan(entries=(texture_helper.TexturePlanEntry(
            texture_index=new_index, texture_file_name='shared.png',
            width=4, height=4, format=synthetic_donor.TEXTURE_FORMAT, format_name='CMPR',
            image_data=bytes([0x33] * synthetic_donor.TEXTURE_PAYLOAD_LENGTH), palette_data=b'',
            palette_entries=0, palette_format=None, template_texture_index=0,
        ),))
        with mock.patch.object(texture_helper, 'build_hammerspace_texture_plan', return_value=plan) as build_plan:
            result, gpl = self._build(tex='build')
        self.assertEqual(build_plan.call_count, 1)
        self.assertEqual(result.validation_report['validator_facts']['tex_texture_count'], new_index + 1)
        self.assertEqual(_blob(gpl, RIGID)['records'][6][4:8], bytes.fromhex(f'1111000{new_index}'))
        self.assertEqual(_blob(gpl, 2)['records'][0][4:8], bytes.fromhex(f'1111000{new_index}'))
        self.assertEqual(len(result.validation_report['validator_facts']['gpl_submesh_layout']), 3)

    def test_rebuild_with_a_skinned_position_edit_in_one_build(self):
        """Both paths at once: the rigid submesh is rebuilt while submesh 0
        takes the slot-preserving position edit."""
        skinned = self.model['Submeshes'][0]['VertexBuffer']
        # The synthetic donor leaves absolute offsets at 0; the in-place
        # position path needs the real one, read from the donor GPL.
        blob_start = _blob(self.donor_gpl, 0)['start']
        pos_header = struct.unpack_from('>I', self.donor_gpl, blob_start)[0]
        pos_ptr = struct.unpack_from('>I', self.donor_gpl, blob_start + pos_header)[0]
        skinned['VertexBufferOffset'] = f'0x{self.env.model_offset + 0x20 + blob_start + pos_ptr:x}'
        donor_positions = bytearray(decode_field(skinned['VertexBufferData']))
        donor_positions[0:2] = struct.pack('>h', 777)
        skinned['VertexBufferDataEdited'] = base64.b64encode(bytes(donor_positions)).decode('ascii')
        self.model['Submeshes'][RIGID]['RigidRebuild'] = _resized_rebuild(self.model, 9)
        self.assertEqual(start.hammerspace_section_args(self.model)[:2], ['--gpl', 'build'])

        result, gpl = self._build()
        self.assertEqual(_record_faces(_blob(gpl, RIGID), 5), _fan(9))
        self.assertEqual(_blob(gpl, 0)['positions'][2][:2], struct.pack('>h', 777))
        donor_blob0 = _blob(self.donor_gpl, 0)
        self.assertEqual(_blob(gpl, 0)['positions'][2][2:], donor_blob0['positions'][2][2:])

    def test_swap_chain_rebuild_moves_bone_and_custom_takes_the_freed_one(self):
        """G10 end to end: the rigid submesh leaves its bone for a free one
        and a new custom submesh is hosted on the bone it just freed."""
        bones = {int(b['BoneId']): b for b in self.model['BoneHierarchy']}
        bones[OWNER]['GeoIdEdited'] = 0xFFFF
        bones[FREE_BONE]['GeoIdEdited'] = RIGID
        self.model['Submeshes'][RIGID]['RigidRebuild'] = _identity_rebuild(self.model, host_bone=FREE_BONE)
        self.model['CustomSubmeshes'] = [_custom_submesh(self.model, host_bone=OWNER)]
        result, gpl = self._build()
        owners = result.validation_report['validator_facts']['act_geo_id_owners']
        self.assertEqual((owners[RIGID], owners[2]), ([FREE_BONE], [OWNER]))
        self.assertEqual(_blob(gpl, 2)['name'], b'cube')

    def test_untouched_model_reports_no_rebuild_and_builds_identically(self):
        """An export without edits takes no rebuild path and clones the GPL."""
        self.assertEqual(ExportMode.model_level_reasons(self.model), [])
        self.assertNotIn('RigidRebuild', self.model['Submeshes'][RIGID])
        result = main.BuildModelBlock(
            self.data, main.SectionModes(), sluggie_path=self.env.sluggie_path,
        )
        self.assertTrue(result.validation_report['valid'])
        self.assertEqual(_gpl_section(result.block), self.donor_gpl)

    def test_splice_keeps_a_uv_rebuild_tail_at_its_distance(self):
        """A payload PatchGPLUVRebuild appended past the donor length is
        addressed blob-relative from the existing blobs, so it must not move
        when the rebuilt blob goes in before GPLUserData. Replacing blob 1
        under a blob 0 that stays keeps the donor blob (nothing below it may
        shift)."""
        gpl = bytearray(0x90)
        struct.pack_into('>5I', gpl, 0, 0, 16, 0x80, 2, 0x14)     # user data 16 bytes at 0x80
        struct.pack_into('>4I', gpl, 0x14, 0x30, 0x34, 0x60, 0x64)  # blobs at 0x30 and 0x60
        gpl[0x30:0x60] = bytes(range(48))
        gpl[0x60:0x80] = bytes(range(100, 132))
        gpl[0x80:0x90] = b'U' * 16
        tail = b'T' * 32
        out, length, dropped = main._splice_blobs_before_user_data(bytes(gpl) + tail, [(1, b'N' * 40, 4)], 0x90)

        self.assertEqual(dropped, {})
        self.assertEqual(out[0x30:0x80], gpl[0x30:0x80])            # both donor blobs untouched
        self.assertEqual(struct.unpack_from('>II', out, 0x14), (0x30, 0x34))
        self.assertEqual(out[0x80:0x90], bytes(16))                 # user data's old spot zeroed
        self.assertEqual(out[0x90:0xB0], tail)                      # tail kept at its distance
        self.assertEqual(struct.unpack_from('>II', out, 0x1C), (0xC0, 0xC4))   # next 32-byte boundary
        self.assertEqual(out[0xC0:0xE8], b'N' * 40)
        user_ptr = struct.unpack_from('>I', out, 8)[0]
        self.assertEqual(user_ptr, 0x100)
        self.assertEqual(out[user_ptr:user_ptr + 16], b'U' * 16)
        self.assertEqual((length, len(out)), (0x110, 0x110))

    def test_splice_drops_the_leading_donor_blob(self):
        """The replaced blob leads the blob region, so its span is cut and
        user data and tail move up together; the tail keeps its distance to
        the (here: no) remaining blobs."""
        gpl = bytearray(0x70)
        struct.pack_into('>5I', gpl, 0, 0, 16, 0x60, 1, 0x14)     # user data 16 bytes at 0x60
        struct.pack_into('>II', gpl, 0x14, 0x20, 0x24)             # one blob at 0x20
        gpl[0x20:0x60] = bytes(range(64))
        gpl[0x60:0x70] = b'U' * 16
        tail = b'T' * 32
        out, length, dropped = main._splice_blobs_before_user_data(bytes(gpl) + tail, [(0, b'N' * 40, 4)], 0x70)

        self.assertEqual(dropped, {0: 64})
        self.assertEqual(out[0x20:0x30], bytes(16))                 # user data's old spot (moved up) zeroed
        self.assertEqual(out[0x30:0x50], tail)
        self.assertEqual(struct.unpack_from('>II', out, 0x14), (0x60, 0x64))   # next 32-byte boundary
        self.assertEqual(out[0x60:0x88], b'N' * 40)
        user_ptr = struct.unpack_from('>I', out, 8)[0]
        self.assertEqual(user_ptr, 0xA0)
        self.assertEqual(out[user_ptr:user_ptr + 16], b'U' * 16)
        self.assertEqual((length, len(out)), (0xB0, 0xB0))

    def test_splice_drop_keeps_the_remaining_blobs_mod32_residue(self):
        """A 44-byte span is cut by 32: the 12 leftover bytes stay as a
        zeroed gap so blob 1 moves by a multiple of 32 (its primitive lists
        and positions stay aligned); its descriptor follows."""
        gpl = bytearray(0x70)
        struct.pack_into('>5I', gpl, 0, 0, 0, 0, 2, 0x14)
        struct.pack_into('>4I', gpl, 0x14, 0x24, 0x28, 0x50, 0x54)  # spans 0x24-0x50 (44) and 0x50-0x70
        gpl[0x24:0x50] = bytes(range(44))
        gpl[0x50:0x70] = bytes(range(100, 132))
        out, length, dropped = main._splice_blobs_before_user_data(bytes(gpl), [(0, b'N' * 40, 4)], None)

        self.assertEqual(dropped, {0: 44})
        self.assertEqual(out[0x24:0x30], bytes(12))
        self.assertEqual(out[0x30:0x50], gpl[0x50:0x70])
        self.assertEqual(struct.unpack_from('>II', out, 0x1C), (0x30, 0x34))
        self.assertEqual(struct.unpack_from('>II', out, 0x14), (0x60, 0x64))
        self.assertEqual(out[0x60:0x88], b'N' * 40)
        self.assertEqual((length, len(out)), (0xA0, 0xA0))

    def test_splice_drops_every_replaced_blob_from_the_front(self):
        gpl = bytearray(0x64)
        struct.pack_into('>5I', gpl, 0, 0, 0, 0, 2, 0x14)
        struct.pack_into('>4I', gpl, 0x14, 0x24, 0x28, 0x44, 0x48)
        gpl[0x24:0x64] = bytes(range(64))
        out, length, dropped = main._splice_blobs_before_user_data(
            bytes(gpl), [(0, b'N' * 40, 4), (1, b'M' * 40, 8)], None)

        self.assertEqual(dropped, {0: 32, 1: 32})
        self.assertEqual(struct.unpack_from('>4I', out, 0x14), (0x40, 0x44, 0x80, 0x88))
        self.assertEqual(out[0x40:0x68], b'N' * 40)
        self.assertEqual(out[0x80:0xA8], b'M' * 40)
        self.assertEqual((length, len(out)), (0xC0, 0xC0))

    def test_splice_keeps_a_donor_blob_whose_span_holds_another_name(self):
        """Vanilla keeps some mesh names in a string table; a remaining
        descriptor's name pointer is the one GPL-absolute pointer that can
        reach into the dropped span, so such a blob stays."""
        gpl = bytearray(0x70)
        struct.pack_into('>5I', gpl, 0, 0, 0, 0, 2, 0x14)
        struct.pack_into('>4I', gpl, 0x14, 0x24, 0x28, 0x50, 0x40)  # blob 1's name sits in blob 0's span
        gpl[0x24:0x70] = bytes(range(76))
        out, length, dropped = main._splice_blobs_before_user_data(bytes(gpl), [(0, b'N' * 40, 4)], None)

        self.assertEqual(dropped, {})
        self.assertEqual(out[0x24:0x70], gpl[0x24:0x70])
        self.assertEqual(struct.unpack_from('>4I', out, 0x14), (0x80, 0x84, 0x50, 0x40))
        self.assertEqual((length, len(out)), (0xC0, 0xC0))

    def test_new_surface_on_an_appended_texture(self):
        tex_dir = self.env.directory / 'tex'
        from PIL import Image
        Image.new('RGBA', (4, 4), (255, 0, 0, 255)).save(tex_dir / 'new.png')
        self.model.update({
            'ReimportTextures': True,
            'AdditionalTextureDescriptors': [{'TextureFileName': 'new.png', 'TemplateTextureIndex': 0}],
        })
        rebuild = _identity_rebuild(self.model)
        rebuild['FaceSurfaceTable'] = [SURFACE, NEW_KEY]
        rebuild['FaceSurfaceIndices'] = _u16s([0, 0, 0, 1, 1, 1])
        rebuild['NewSurfaces'] = [_new_surface(texture={'AdditionalTextureFileName': 'new.png'})]
        self.model['Submeshes'][RIGID]['RigidRebuild'] = rebuild

        with self.assertRaisesRegex(ValueError, 'RigidRebuild.* new surfaces .* require'):
            main.BuildModelBlock(self.data, main.SectionModes(gpl='build'), sluggie_path=self.env.sluggie_path)

        plan = texture_helper.TexturePlan(entries=(texture_helper.TexturePlanEntry(
            texture_index=synthetic_donor.TEXTURE_COUNT, texture_file_name='new.png',
            width=4, height=4, format=synthetic_donor.TEXTURE_FORMAT, format_name='CMPR',
            image_data=bytes([0x11] * synthetic_donor.TEXTURE_PAYLOAD_LENGTH), palette_data=b'',
            palette_entries=0, palette_format=None, template_texture_index=0,
        ),))
        with mock.patch.object(texture_helper, 'build_hammerspace_texture_plan', return_value=plan):
            result, gpl = self._build(tex='build')
        rebuilt = _blob(gpl, RIGID)
        self.assertEqual(rebuilt['records'][6][4:8], bytes.fromhex(f'1111000{synthetic_donor.TEXTURE_COUNT}'))
        self.assertEqual(result.validation_report['validator_facts']['tex_texture_count'], synthetic_donor.TEXTURE_COUNT + 1)


class RigidRebuildCarriedColorTests(unittest.TestCase):
    """A donor colour array the export does not list (Mii body: Type 3 has
    no color0 but the blob points at a one-entry RGB565 array) is carried."""

    @staticmethod
    def _add_unlisted_color(data: dict) -> None:
        sub = data['SluggiesModel']['Submeshes'][RIGID]
        sub['ColorChannels'] = [{
            'ColorChannelIndex': 0,
            'ColorChannelData': _b64(bytes.fromhex('f81f')),
            'ColorFacesData': _u16s([0] * sub['FacesCount'] * 3),
            'ColorChannelCompCount': 3,
            'ColorChannelQuantizeInfo': 0,
            'ColorChannelOffset': '0x0',
        }]

    def test_unlisted_donor_color_array_is_carried_into_the_rebuilt_blob(self):
        with synthetic_donor.donor_environment(mutate=self._add_unlisted_color) as env:
            data = env.reload()
            model = data['SluggiesModel']
            model['UseHammerspace'] = True
            model['Submeshes'][RIGID]['ColorChannels'] = []
            model['Submeshes'][RIGID]['RigidRebuild'] = _identity_rebuild(model)
            result = main.BuildModelBlock(data, main.SectionModes(gpl='build'), sluggie_path=env.sluggie_path)
            self.assertTrue(result.validation_report['valid'], result.validation_report.get('errors'))
            rebuilt = _blob(_gpl_section(result.block), RIGID)
            self.assertEqual(rebuilt['color'], (0, 3, bytes.fromhex('f81f')))

    def test_listed_donor_color_requires_rebuilt_colors(self):
        with synthetic_donor.donor_environment(mutate=self._add_unlisted_color) as env:
            data = env.reload()
            model = data['SluggiesModel']
            model['UseHammerspace'] = True
            model['Submeshes'][RIGID]['RigidRebuild'] = _identity_rebuild(model)
            with self.assertRaisesRegex(ValueError, 'must carry ColorChannelData'):
                main._validate_rigid_rebuilds(model)
            model['Submeshes'][RIGID]['RigidRebuild'].update({
                'ColorChannelData': _b64(bytes.fromhex('07e0ffff')),
                'ColorFacesData': _u16s([1, 0, 1] * 6),
            })
            result = main.BuildModelBlock(data, main.SectionModes(gpl='build'), sluggie_path=env.sluggie_path)
            self.assertTrue(result.validation_report['valid'], result.validation_report.get('errors'))
            self.assertEqual(_blob(_gpl_section(result.block), RIGID)['color'], (0, 3, bytes.fromhex('07e0ffff')))


# --- the paths around the builder ------------------------------------------------

class RigidRebuildGuardTests(unittest.TestCase):
    def test_position_edits_skip_rebuilt_submeshes(self):
        model = {'UseBase64': True, 'Submeshes': [{
            'RigidRebuild': {'HostBoneId': 1},
            'VertexBuffer': {'VertexBufferData': 'AAA=', 'VertexBufferDataEdited': 'AAE='},
        }]}
        self.assertEqual(main._position_edits(model), [])

    def test_uv_rebuild_skips_rebuilt_submeshes_and_refuses_other_face_count_changes(self):
        skipped = {'SluggiesModel': {'Submeshes': [{
            'RigidRebuild': {'HostBoneId': 1}, 'FacesCount': 6, 'FacesCountEdited': 9,
            'UVChannels': [{'UVChannelIndex': 0, 'UVChannelDataEdited': 'AAA=', 'UVFacesDataEdited': 'AAA='}],
        }]}}
        self.assertFalse(GeometryRebuild.rebuild_edited_uvs(skipped))
        changed = {'SluggiesModel': {'Submeshes': [{'FacesCount': 6, 'FacesCountEdited': 9}]}}
        with self.assertRaisesRegex(ValueError, 'sub0: face count changed from 6 to 9'):
            GeometryRebuild.rebuild_edited_uvs(changed)
        legacy = {'SluggiesModel': {'Submeshes': [{'DisplayStates': []}]}}
        self.assertFalse(GeometryRebuild.rebuild_edited_uvs(legacy))

    def test_dispatcher_selects_gpl_build_and_tex_build_for_an_appended_texture(self):
        model = {'Submeshes': [{}, {'RigidRebuild': {'NewSurfaces': []}}]}
        self.assertEqual(start.hammerspace_section_args(model), ['--gpl', 'build'])
        model['Submeshes'][1]['RigidRebuild']['NewSurfaces'] = [
            {'TextureAssignment': {'AdditionalTextureFileName': 'new.png'}},
        ]
        self.assertEqual(start.hammerspace_section_args(model), ['--gpl', 'build', '--tex', 'build'])
        self.assertTrue(start._needs_hammerspace({'Submeshes': [{'RigidRebuild': {}}]}, 'x.sluggie'))

    def test_export_mode_treats_a_rebuild_as_a_hammerspace_only_edit(self):
        submeshes = [{'VertexBuffer': {'VertexBufferOffset': '0x10'}, 'RigidRebuild': {}}]
        self.assertEqual(ExportMode.stale_hammerspace_submeshes(submeshes, []), ['submesh 0'])
        self.assertEqual(ExportMode.stale_hammerspace_submeshes(submeshes, ['0x10']), [])

    def test_parse_sluggie_attaches_the_rebuild(self):
        data = synthetic_donor.build_sluggie()
        model = data['SluggiesModel']
        rebuild = _identity_rebuild(model)
        rebuild['NewSurfaces'] = [_new_surface(SpecularStrength=7)]
        rebuild['FaceSurfaceTable'] = [SURFACE, NEW_KEY]
        rebuild['FaceSurfaceIndices'] = _u16s([0, 1] * 3)
        model['Submeshes'][RIGID]['RigidRebuild'] = rebuild
        parsed = main.ParseSluggie(data)
        self.assertIsNone(parsed.mesh.submeshes[0].rigid_rebuild)
        got = parsed.mesh.submeshes[RIGID].rigid_rebuild
        self.assertEqual(got.host_bone_id, OWNER)
        self.assertEqual(got.face_surface_indices, (0, 1, 0, 1, 0, 1))
        self.assertEqual(got.face_surface_table, [SURFACE, NEW_KEY])
        self.assertEqual(len(got.uv_channels), 2)
        self.assertIsNone(got.normal_data)
        self.assertEqual(got.new_surfaces[0].surface_key, NEW_KEY)
        self.assertEqual(got.new_surfaces[0].texture_assignment.donor_texture_index, 1)
        self.assertEqual(got.new_surfaces[0].specular_strength, 7)
        self.assertEqual(got.reasons, ['topology'])


if __name__ == '__main__':
    unittest.main()
