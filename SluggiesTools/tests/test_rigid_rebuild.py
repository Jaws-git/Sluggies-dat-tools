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
import GeometryRebuild  # noqa: E402
import HammerspaceMain as main  # noqa: E402
import start  # noqa: E402
import synthetic_donor  # noqa: E402
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
    return [tuple(vertex['position'] for vertex in face) for face in faces]


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

    def test_facial_pose_submesh_is_refused(self):
        self.model['FacialPoseData'] = {'Objects': [{'SubmeshIndex': RIGID}]}
        self._refused('facial poses')

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
        self.assertEqual(rebuilt['lists'][5][0], 0x90)
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

    def test_splice_keeps_a_uv_rebuild_tail_at_its_distance(self):
        """A payload PatchGPLUVRebuild appended past the donor length is
        addressed blob-relative from the existing blobs, so it must not move
        when the rebuilt blob goes in before GPLUserData."""
        gpl = bytearray(0x70)
        struct.pack_into('>5I', gpl, 0, 0, 16, 0x60, 1, 0x14)     # user data 16 bytes at 0x60
        struct.pack_into('>II', gpl, 0x14, 0x20, 0x24)             # one blob at 0x20
        gpl[0x20:0x60] = bytes(range(64))
        gpl[0x60:0x70] = b'U' * 16
        tail = b'T' * 32
        out, length = main._splice_blobs_before_user_data(bytes(gpl) + tail, [(0, b'N' * 40, 4)], 0x70)

        self.assertEqual(out[0x20:0x60], bytes(range(64)))          # old blob untouched
        self.assertEqual(out[0x60:0x70], bytes(16))                 # user data's old spot zeroed
        self.assertEqual(out[0x70:0x90], tail)                      # tail kept at its distance
        self.assertEqual(struct.unpack_from('>II', out, 0x14), (0xA0, 0xA4))   # next 32-byte boundary
        self.assertEqual(out[0xA0:0xC8], b'N' * 40)
        user_ptr = struct.unpack_from('>I', out, 8)[0]
        self.assertEqual(user_ptr, 0xE0)
        self.assertEqual(out[user_ptr:user_ptr + 16], b'U' * 16)
        self.assertEqual((length, len(out)), (0xF0, 0xF0))

    def test_splice_without_user_data_appends_at_the_end(self):
        gpl = bytearray(0x60)
        struct.pack_into('>5I', gpl, 0, 0, 0, 0, 1, 0x14)
        struct.pack_into('>II', gpl, 0x14, 0x20, 0x24)
        gpl[0x20:0x60] = bytes(range(64))
        out, length = main._splice_blobs_before_user_data(bytes(gpl), [(0, b'N' * 40, 4)], None)
        self.assertEqual(out[:0x14] + out[0x1C:0x60], gpl[:0x14] + gpl[0x1C:0x60])
        self.assertEqual(struct.unpack_from('>II', out, 0x14), (0x60, 0x64))
        self.assertEqual(struct.unpack_from('>I', out, 8)[0], 0)
        self.assertEqual((length, len(out)), (0xA0, 0xA0))

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

        with self.assertRaisesRegex(ValueError, 'RigidRebuild new surfaces .* require'):
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
