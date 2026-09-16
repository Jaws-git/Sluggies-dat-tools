import base64
import copy
import json
import pathlib
import struct
import sys
import unittest


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR, TOOLS_DIR / 'tests'):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import build_template_source_fixture as tsf
import build_add_submesh_fixture as probe
from test_add_submesh_fixture import _gpl_positions


MODELS_DIR = TOOLS_DIR.parent / '2_Output_Models'
REAL_BOO = MODELS_DIR / '32 Boo' / '134010752_teresa.gpl' / '134010752_teresa.gpl.sluggie'
REAL_MARIO = MODELS_DIR / '18 Mario' / '78277664_mario.gpl' / '78277664_mario.gpl.sluggie'
TOADETTE_SLUGGIES = sorted((MODELS_DIR / '33 Toadette').glob('135708512_kinopico.gpl*/*.sluggie'))


def _state(surface_id, state_id, mode, pad='000000', prim=0):
    return {
        'SurfaceId': surface_id,
        'DisplayStateId': state_id,
        'DisplayStatePadBytes': pad,
        'ShaderMode': mode,
        'PrimListLength': prim,
        'PrimListData': 'AAA=' if prim else '',
        'FaceCount': 1 if prim else 0,
    }


def _skinned_submesh0():
    """Mirrors Boo's body list: two Spec surfaces with different layer-0 textures."""
    return {
        'MeshName': 'body',
        'VertexBuffer': {'VertexBufferCompCount': 6, 'VertexBufferQuantizeInfo': 59, 'VertexBufferData': 'AAA='},
        'UVChannels': [
            {'UVChannelIndex': 0, 'PaletteName': 'x.tpl', 'TextureIndex': 1, 'WrapS': 1, 'WrapT': 1},
            {'UVChannelIndex': 1, 'PaletteName': 'x.tpl', 'TextureIndex': 2, 'WrapS': 0, 'WrapT': 0},
        ],
        'DisplayStates': [
            _state('sm0_ds0', 1, '11110000', '000008'),
            _state('sm0_ds1', 1, '11002002'),
            _state('sm0_ds2', 4, 'ffffff10'),
            _state('sm0_ds3', 3, '00003cbc'),
            _state('sm0_ds4', 6, '00000374', '010000'),
            _state('sm0_ds5', 7, 'Spec', '320064', prim=64),
            _state('sm0_ds6', 1, '11110001', '000108'),
            _state('sm0_ds7', 6, '00000174', '010000'),
            _state('sm0_ds8', 7, 'Spec', '1e0064', prim=32),
            _state('sm0_ds9', 7, 'RhSp', '460064', prim=32),
            _state('sm0_ds10', 6, '00000375', '010000'),
            _state('sm0_ds11', 7, 'Spec', '320064', prim=32),
        ],
    }


def _bone(bone_id, geo=0xFFFF, parent=0):
    return {'BoneId': bone_id, 'GeoIdRaw': geo, 'ParentBoneId': parent, 'GeoIdFieldOffset': '0x1000'}


def _model():
    return {'SluggiesModel': {
        'ChunkNumber': 32, 'FileIndex': 0, 'ModelOffset': '0x1000', 'ModelLength': 0x10000,
        'UseBase64': True,
        'Submeshes': [_skinned_submesh0()],
        'BoneHierarchy': [_bone(0, parent=None), _bone(1), _bone(2), _bone(3, geo=0)],
        'SkinData': {'SK1s': [{'BoneIndex': 1}], 'SK2s': [], 'SKAccs': []},
    }}


class Type3Tests(unittest.TestCase):
    def test_generator_reproduces_vanilla_type3_values(self):
        for value in (0x28A8, 0x2828, 0x3CBC, 0x3CA8):
            with self.subTest(value=hex(value)):
                self.assertEqual(tsf.type3_setting(probe._type3_descriptors(value)), value)

    def test_index_width_follows_count(self):
        self.assertEqual((tsf.index_size_for(256), tsf.index_size_for(257)), (1, 2))


class DerivedTemplateTests(unittest.TestCase):
    def test_first_spec_surface_copies_its_effective_states(self):
        self.assertEqual(tsf.derive_rigid_state_records(_skinned_submesh0(), 'sm0_ds5'), [
            (1, '000008', '11110000'),
            (1, '000000', '11002002'),
            (4, '000000', 'ffffff10'),
            (3, '000000', '00000000'),
            (6, '010000', '00000374'),
            (7, '320064', 'Spec'),
        ])

    def test_later_surface_uses_the_latest_layer_type6_and_type7(self):
        records = tsf.derive_rigid_state_records(_skinned_submesh0(), 'sm0_ds8')
        self.assertEqual(records[0], (1, '000108', '11110001'))
        self.assertEqual(records[1], (1, '000000', '11002002'))
        self.assertEqual(records[4], (6, '010000', '00000174'))
        self.assertEqual(records[5], (7, '1e0064', 'Spec'))

    def test_hand_role_surface_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "RhSp"):
            tsf.derive_rigid_state_records(_skinned_submesh0(), 'sm0_ds9')

    def test_type6_375_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '00000375'):
            tsf.derive_rigid_state_records(_skinned_submesh0(), 'sm0_ds11')

    def test_missing_or_non_drawing_surface_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            tsf.derive_rigid_state_records(_skinned_submesh0(), 'sm0_ds99')
        with self.assertRaisesRegex(ValueError, 'draws no primitives'):
            tsf.derive_rigid_state_records(_skinned_submesh0(), 'sm0_ds4')

    def test_rigid_submesh_is_not_a_derived_source(self):
        sub = _skinned_submesh0()
        sub['VertexBuffer']['VertexBufferCompCount'] = 3
        with self.assertRaisesRegex(ValueError, 'skinned submesh 0'):
            tsf.derive_rigid_state_records(sub, 'sm0_ds5')

    def test_without_layer1_binding_the_one_channel_form_is_used(self):
        sub = _skinned_submesh0()
        del sub['DisplayStates'][1]
        records = tsf.derive_rigid_state_records(sub, 'sm0_ds5')
        self.assertEqual([r[0] for r in records], [1, 4, 3, 6, 7])
        self.assertEqual(records[1], (4, '000000', 'fffffff0'))


class BuiltinTemplateTests(unittest.TestCase):
    def test_stored_bytes_match_recorded_hash(self):
        template = tsf.BUILTIN_RIGID_SPEC_V1
        self.assertEqual(tsf.state_records_sha256(template['States']), template['Sha256'])

    def test_binds_host_submesh0_textures(self):
        records = tsf.builtin_rigid_state_records(_skinned_submesh0(), 'rigid_spec_v1')
        self.assertEqual(records[0], (1, '000008', '11110000'))
        self.assertEqual(records[1], (1, '000000', '11002002'))
        self.assertEqual(records[5], (7, '640064', 'Spec'))

    def test_without_layer1_binding_drops_the_state(self):
        sub = _skinned_submesh0()
        del sub['DisplayStates'][1]
        records = tsf.builtin_rigid_state_records(sub, 'rigid_spec_v1')
        self.assertEqual([r[0] for r in records], [1, 4, 3, 6, 7])
        self.assertEqual(records[1][2], 'fffffff0')

    def test_tampered_or_unknown_template_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unknown template'):
            tsf.builtin_rigid_state_records(_skinned_submesh0(), 'nope')
        original = tsf.BUILTIN_TEMPLATES['rigid_spec_v1']
        tampered = dict(original, States=original['States'][:-1] + ((7, '640064', 'RhSp'),))
        tsf.BUILTIN_TEMPLATES['rigid_spec_v1'] = tampered
        try:
            with self.assertRaisesRegex(ValueError, 'recorded hash'):
                tsf.builtin_rigid_state_records(_skinned_submesh0(), 'rigid_spec_v1')
        finally:
            tsf.BUILTIN_TEMPLATES['rigid_spec_v1'] = original

    @unittest.skipUnless(TOADETTE_SLUGGIES, 'Toadette export not present in this checkout')
    def test_capture_still_matches_vanilla_source(self):
        model = json.loads(TOADETTE_SLUGGIES[0].read_text(encoding='utf-8'))['SluggiesModel']
        provenance = tsf.BUILTIN_RIGID_SPEC_V1['Provenance']
        source = next(sub for sub in model['Submeshes'] if sub['MeshName'] == provenance['MeshName'])
        self.assertEqual(source['DisplayStates'][-1]['SurfaceId'], provenance['SurfaceId'])
        self.assertEqual(
            tuple(tsf._record(state) for state in source['DisplayStates']),
            tsf.BUILTIN_RIGID_SPEC_V1['States'],
        )


class PrepareFixtureTests(unittest.TestCase):
    def test_derived_and_builtin_cubes_are_appended_with_canonical_formats(self):
        source = _model()
        original = copy.deepcopy(source)
        data = tsf.prepare_template_source_fixture(
            source, [(1, 'derived:sm0_ds5'), (2, 'builtin:rigid_spec_v1')],
        )
        model = data['SluggiesModel']
        self.assertEqual(source, original)
        self.assertEqual(len(model['Submeshes']), 3)
        self.assertEqual(model['BoneHierarchy'][1]['GeoIdEdited'], 1)
        self.assertEqual(model['BoneHierarchy'][2]['GeoIdEdited'], 2)
        cubes = model['TemplateSourceFixture']['Cubes']
        self.assertTrue(cubes[0]['SknUsedHostBone'])
        self.assertEqual(cubes[1]['BuiltinProvenance']['SurfaceId'], 'sm1_ds5')

        for sub in model['Submeshes'][1:]:
            self.assertEqual(
                (sub['VertexBuffer']['VertexBufferCompCount'], sub['VertexBuffer']['VertexBufferQuantizeInfo']),
                tsf.POSITION_FORMAT,
            )
            self.assertEqual(sub['NormalBuffer']['NormalAmbientPct'], 0.0)
            self.assertEqual([uv['UVChannelIndex'] for uv in sub['UVChannels']], [0, 1])
            self.assertEqual([uv['TextureIndex'] for uv in sub['UVChannels']], [1, 2])
            self.assertEqual(base64.b64decode(sub['ColorChannels'][0]['ColorChannelData']), tsf.COLOR_WHITE)
            states = sub['DisplayStates']
            self.assertEqual(states[3]['ShaderMode'], '000028a8')
            self.assertEqual([s['PrimListLength'] > 0 for s in states], [False] * 5 + [True])
            self.assertEqual(states[5]['FaceCount'], 12)
            self.assertEqual(struct.unpack('>12H', base64.b64decode(sub['FaceTextureIndices'])), (0,) * 12)

    def test_host_bone_rules(self):
        with self.assertRaisesRegex(ValueError, 'already owns'):
            tsf.prepare_template_source_fixture(_model(), [(3, 'derived:sm0_ds5')])
        with self.assertRaisesRegex(ValueError, 'more than one cube'):
            tsf.prepare_template_source_fixture(_model(), [(1, 'derived:sm0_ds5'), (1, 'builtin:rigid_spec_v1')])
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            tsf.prepare_template_source_fixture(_model(), [(42, 'derived:sm0_ds5')])

    def test_source_spec_parsing(self):
        self.assertEqual(tsf.parse_source('derived:sm0_ds5'), ('derived', 'sm0_ds5'))
        for bad in ('sm0_ds5', 'foo:bar', 'rigid:'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    tsf.parse_source(bad)


class RealDonorTests(unittest.TestCase):
    def _build(self, source, cubes, name):
        fixture_path = TOOLS_DIR.parent / 'Debug' / 'fixtures' / name
        try:
            build, offset, data = tsf.build_template_source_fixture(source, fixture_path, cubes, 0.1, write=False)
        finally:
            if fixture_path.exists():
                fixture_path.unlink()
        self.assertIsNone(offset)
        report = build.validation_report
        self.assertTrue(report['valid'], report.get('errors'))
        self.assertEqual(report['section_alignment']['misaligned'], [])
        return build, data

    @unittest.skipUnless(REAL_BOO.is_file(), 'Boo export not present in this checkout')
    def test_boo_derived_and_builtin_cubes_build(self):
        build, data = self._build(
            REAL_BOO, [(29, 'derived:sm0_ds5'), (49, 'builtin:rigid_spec_v1')], '_test_template_sources_boo.sluggie',
        )
        cubes = data['SluggiesModel']['TemplateSourceFixture']['Cubes']
        self.assertEqual(cubes[0]['States'], [
            [1, '000008', '11110000'], [1, '000000', '11002002'], [4, '000000', 'ffffff10'],
            [3, '000000', '000028a8'], [6, '010000', '00000374'], [7, '320064', 'Spec'],
        ])
        self.assertEqual(cubes[1]['States'][5], [7, '640064', 'Spec'])
        block = build.block[int(build.validation_report.get('container_prefix_size', 0)):]
        for index in (1, 2):
            self.assertEqual({abs(v) for v in _gpl_positions(block, index)}, {205})

    @unittest.skipUnless(REAL_MARIO.is_file(), 'Mario export not present in this checkout')
    def test_mario_control_cubes_build(self):
        build, data = self._build(
            REAL_MARIO,
            [(49, 'rigid:sm1_ds5'), (85, 'derived:sm0_ds5'), (11, 'builtin:rigid_spec_v1')],
            '_test_template_sources_mario.sluggie',
        )
        cubes = data['SluggiesModel']['TemplateSourceFixture']['Cubes']
        self.assertEqual([c['SubmeshIndex'] for c in cubes], [3, 4, 5])
        self.assertEqual(len(build.validation_report['validator_facts']['gpl_submesh_layout']), 6)


if __name__ == '__main__':
    unittest.main()
