import base64
import copy
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


import synthetic_donor  # noqa: E402

BLENDER_ADDON_DIR = TOOLS_DIR.parent / 'BlenderAddonSrc'
if str(BLENDER_ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_ADDON_DIR))
import TemplateSources  # noqa: E402


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
        for name, template in tsf.BUILTIN_TEMPLATES.items():
            with self.subTest(name):
                self.assertEqual(
                    tsf.state_records_sha256(template['States']), template['Sha256'],
                )

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

    def test_one_layer_builtin_stays_one_layer_on_a_two_layer_host(self):
        """The probe builder agrees with the patch-time builder: Type 4 follows
        the template's emitted T1 records, not the host's bindings."""
        records = tsf.builtin_rigid_state_records(_skinned_submesh0(), 'rigid_shdw_v1')
        self.assertEqual([r[0] for r in records], [1, 4, 3, 6, 7])
        self.assertEqual(records[1], (4, '000000', 'fffffff0'))
        self.assertEqual(records[-1][2], 'Shdw')
        # The same host gives the 2-layer built-in both channels.
        spec = tsf.builtin_rigid_state_records(_skinned_submesh0(), 'rigid_spec_v1')
        self.assertEqual([r[0] for r in spec], [1, 1, 4, 3, 6, 7])
        self.assertEqual(spec[2], (4, '000000', 'ffffff10'))

    def test_unverified_builtins_still_build_for_probe_7(self):
        """The probe fixture must be able to build an unverified template --
        that is how PLAN_EditRigidMeshes.md Phase 0 probe 7 verifies it. Only
        the patch-time validator and the dialogs refuse them."""
        unverified = [
            name for name, template in tsf.BUILTIN_TEMPLATES.items()
            if not template['VerifiedInGame']
        ]
        self.assertTrue(unverified, 'expected at least one unverified built-in')
        for name in unverified:
            with self.subTest(name):
                records = tsf.builtin_rigid_state_records(_skinned_submesh0(), name)
                self.assertEqual(records[-1][2], tsf.BUILTIN_TEMPLATES[name]['ShaderMode'])

    def test_every_entry_carries_the_provenance_its_capture_probe_needs(self):
        """Decision 9: each entry is a byte-exact capture of one whole vanilla
        rigid draw list. The tie back to that vanilla export is re-checked by
        ``SluggiesTools/probe_builtin_template_captures.py`` -- the exports are
        gitignored, so it is a hand-run probe, not a test. What is checkable
        here is that every entry names a source the probe can actually find."""
        for name, template in tsf.BUILTIN_TEMPLATES.items():
            with self.subTest(name):
                provenance = template['Provenance']
                self.assertEqual(
                    sorted(provenance),
                    ['IdenticalRigidLists', 'MeshName', 'Model', 'SurfaceId'],
                )
                self.assertGreaterEqual(int(provenance['IdenticalRigidLists']), 1)
                self.assertIn('/', provenance['Model'])
                folder, _sep, model = provenance['Model'].partition('/')
                self.assertTrue(folder and model)
                self.assertTrue(provenance['MeshName'])
                # The probe reads the source's LAST display state, so the
                # surface id must be the one the stored record list ends on.
                self.assertEqual(
                    provenance['SurfaceId'].rsplit('_', 1)[-1],
                    f'ds{len(template["States"]) - 1}',
                )


class BuiltinRegistryMirrorTests(unittest.TestCase):
    """G24/decision 9: the Blender addon cannot import HammerspaceMain, so
    TemplateSources mirrors the registry by hand. These catch drift."""

    def test_addon_mirror_matches_the_registry(self):
        registry = tsf.hammerspace._CUSTOM_SUBMESH_BUILTIN_TEMPLATES
        self.assertEqual(sorted(TemplateSources.BUILTIN_TEMPLATES), sorted(registry))
        for name, template in registry.items():
            with self.subTest(name):
                mirror = TemplateSources.BUILTIN_TEMPLATES[name]
                self.assertEqual(mirror.layers, template['Layers'])
                self.assertEqual(mirror.shader_mode, template['ShaderMode'])
                self.assertEqual(mirror.verified_in_game, template['VerifiedInGame'])
                self.assertTrue(mirror.description)
        self.assertEqual(
            TemplateSources.BUILTIN_TEMPLATE_NAMES,
            tsf.hammerspace.builtin_template_names(verified_only=True),
        )

    def test_hand_visibility_role_mirror_matches_the_tools(self):
        self.assertEqual(
            TemplateSources.HAND_VISIBILITY_ROLES,
            tsf.hammerspace._CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES,
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


class SyntheticDonorTests(unittest.TestCase):
    """The fixture builder end to end over the synthetic donor, for every
    template source kind (see synthetic_donor.py for why this does not read a
    real export)."""

    def setUp(self):
        self.env = self.enterContext(synthetic_donor.donor_environment())

    def _build(self, cubes, name):
        build, offset, data = tsf.build_template_source_fixture(
            self.env.sluggie_path, self.env.directory / name, cubes, 0.1, write=False,
        )
        self.assertIsNone(offset)
        report = build.validation_report
        self.assertTrue(report['valid'], report.get('errors'))
        self.assertEqual(report['section_alignment']['misaligned'], [])
        return build, data

    def test_every_template_source_kind_builds_one_cube(self):
        donor_count = len(self.env.data['SluggiesModel']['Submeshes'])
        build, data = self._build(
            [(2, 'derived:sm0_ds5'), (4, 'rigid:sm1_ds5'), (7, 'builtin:rigid_spec_v1')],
            '_test_template_sources.sluggie',
        )
        cubes = data['SluggiesModel']['TemplateSourceFixture']['Cubes']
        self.assertEqual(
            [c['SubmeshIndex'] for c in cubes],
            list(range(donor_count, donor_count + 3)),
        )
        self.assertEqual(
            len(build.validation_report['validator_facts']['gpl_submesh_layout']),
            donor_count + 3,
        )

    def test_derived_cube_reproduces_the_donor_surface_state_chain(self):
        """`derived:` rebuilds the canonical rigid record order out of the
        host submesh-0 surface's own effective states (F4): both Type-1
        texture layers verbatim, then the UV-count-specific Type-4, a
        regenerated Type-3, and the donor's Type-6 and Type-7."""
        _, data = self._build([(2, 'derived:sm0_ds5')], '_test_template_sources_derived.sluggie')
        states = data['SluggiesModel']['TemplateSourceFixture']['Cubes'][0]['States']
        donor_states = synthetic_donor.DISPLAY_STATE_TEMPLATE

        self.assertEqual([record[0] for record in states], [1, 1, 4, 3, 6, 7])
        # The two texture layers and the trailing Type-6/Type-7 are the
        # donor's own records, pad bytes included.
        self.assertEqual(tuple(states[0]), donor_states[0])
        self.assertEqual(tuple(states[1]), donor_states[1])
        self.assertEqual(tuple(states[4]), donor_states[4])
        self.assertEqual(tuple(states[5]), donor_states[5])
        self.assertEqual(states[5][2], 'Spec')

    def test_cube_corners_land_at_the_requested_half_extent(self):
        donor_count = len(self.env.data['SluggiesModel']['Submeshes'])
        build, _ = self._build(
            [(2, 'derived:sm0_ds5'), (4, 'builtin:rigid_spec_v1')],
            '_test_template_sources_cubes.sluggie',
        )
        report = build.validation_report
        prefix = int(report.get('container_prefix_size', 0))
        inner = int(report.get('inner_assembled_size', len(build.block) - prefix))
        block = build.block[prefix:prefix + inner]
        # 0.1 at QuantizeInfo 59 (divisor 2048) quantizes to 205.
        for index in range(donor_count, donor_count + 2):
            self.assertEqual({abs(value) for value in _gpl_positions(block, index)}, {205})


if __name__ == '__main__':
    unittest.main()
