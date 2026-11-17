import ast
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BLENDER_ADDON_DIR = ROOT / 'BlenderAddonSrc'
if str(BLENDER_ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_ADDON_DIR))
for _path in (ROOT / 'SluggiesTools', ROOT / 'SluggiesTools' / 'Hammerspace'):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import HammerspaceMain as hammerspace  # noqa: E402
import TemplateSources  # noqa: E402
from TemplateSources import (  # noqa: E402
    BUILTIN_TEMPLATES,
    BUILTIN_TEMPLATE_NAMES,
    HAND_VISIBILITY_ROLES,
    TemplateSourceChoice,
    TemplateSourceMaterial,
    BUILTIN_DEFAULT_SPECULAR_STRENGTH,
    STADIUM_CHUNKS,
    build_template_source_choices,
    builtin_template_layers,
    builtin_template_normals,
    is_stadium_chunk,
    next_new_surface_key,
    template_surface_id,
)


# What a non-stadium model is offered: the stadium-only built-ins are hidden.
BUILTIN_CHOICES = [
    f'builtin:{name}' for name in BUILTIN_TEMPLATE_NAMES
    if not BUILTIN_TEMPLATES[name].stadium_only
]
STADIUM_BUILTINS = ['builtin:stadium_shdw_opaque_v1', 'builtin:stadium_shdw_cutout_v1']


def _mat(surface_id, comp_count, shader_mode):
    return TemplateSourceMaterial(surface_id=surface_id, comp_count=comp_count, shader_mode=shader_mode)


class BuildTemplateSourceChoicesTests(unittest.TestCase):
    def test_builtin_always_present_even_with_no_materials(self):
        choices = build_template_source_choices([])
        self.assertEqual([c.template_source for c in choices], BUILTIN_CHOICES)

    def test_rigid_surface_with_plain_shader_mode_is_offered(self):
        choices = build_template_source_choices([_mat('sm1_ds5', 3, 'Spec')])
        kinds = [c.kind for c in choices]
        self.assertIn('rigid', kinds)
        self.assertIn('rigid:sm1_ds5', [c.template_source for c in choices])

    def test_hand_visibility_role_rigid_surface_is_excluded(self):
        for role in HAND_VISIBILITY_ROLES:
            with self.subTest(role=role):
                choices = build_template_source_choices([_mat('sm1_ds5', 3, role)])
                self.assertNotIn('rigid:sm1_ds5', [c.template_source for c in choices])

    def test_skinned_surface_with_spec_shader_is_offered_as_derived(self):
        choices = build_template_source_choices([_mat('sm0_ds5', 6, 'Spec')])
        self.assertIn('derived:sm0_ds5', [c.template_source for c in choices])

    def test_skinned_surface_with_non_spec_shader_is_not_a_derived_source(self):
        choices = build_template_source_choices([_mat('sm0_ds5', 6, 'Shdw')])
        self.assertNotIn('derived:sm0_ds5', [c.template_source for c in choices])

    def test_listing_order_is_builtin_then_rigid_then_derived(self):
        """Built-ins first, so the dialog's first item -- the one
        it preselects -- is builtin:rigid_spec_v1 even here, where the model
        has both a copyable rigid surface and a derivable skinned one."""
        choices = build_template_source_choices([
            _mat('sm0_ds5', 6, 'Spec'),
            _mat('sm1_ds5', 3, 'Spec'),
        ])
        self.assertEqual(
            [c.template_source for c in choices],
            BUILTIN_CHOICES + ['rigid:sm1_ds5', 'derived:sm0_ds5'],
        )

    def test_builtin_spec_is_the_first_choice_whatever_the_model_offers(self):
        for materials in (
            [],
            [_mat('sm1_ds5', 3, 'Spec')],
            [_mat('sm0_ds5', 6, 'Spec')],
            [_mat('sm0_ds5', 6, 'Spec'), _mat('sm1_ds5', 3, 'Spec')],
        ):
            with self.subTest(len(materials)):
                choices = build_template_source_choices(materials)
                self.assertEqual(choices[0].template_source, 'builtin:rigid_spec_v1')

    def test_surface_ids_sort_by_their_numbers(self):
        choices = build_template_source_choices(
            [_mat(surface, 3, 'Spec') for surface in ('sm1_ds10', 'sm10_ds1', 'sm1_ds2', 'sm2_ds1', 'other')])
        self.assertEqual(
            [c.template_source for c in choices][len(BUILTIN_CHOICES):],
            ['rigid:other', 'rigid:sm1_ds2', 'rigid:sm1_ds10', 'rigid:sm2_ds1', 'rigid:sm10_ds1'],
        )
        # Mixed digit / text parts never compare an int with a str.
        self.assertEqual(sorted(['a1', '1a', 'a', '10', '2'], key=TemplateSources.natural_sort_key),
                         ['1a', '2', '10', 'a', 'a1'])

    def test_duplicate_surface_ids_are_deduplicated(self):
        choices = build_template_source_choices([
            _mat('sm1_ds5', 3, 'Spec'),
            _mat('sm1_ds5', 3, 'Spec'),
        ])
        rigid_choices = [c for c in choices if c.kind == 'rigid']
        self.assertEqual(len(rigid_choices), 1)

    def test_other_comp_counts_are_ignored(self):
        choices = build_template_source_choices([_mat('sm2_ds1', 4, 'Spec')])
        self.assertEqual([c.template_source for c in choices], BUILTIN_CHOICES)

    def test_stadium_lists_its_builtins_first(self):
        """A stadium preselects the opaque stadium built-in; the character
        built-ins stay available after it (rigid_spec_v1 also works there)."""
        choices = build_template_source_choices([_mat('sm1_ds4', 3, '11110020')], stadium=True)
        self.assertEqual(
            [c.template_source for c in choices],
            STADIUM_BUILTINS + BUILTIN_CHOICES + ['rigid:sm1_ds4'],
        )

    def test_stadium_builtins_are_hidden_elsewhere(self):
        for template_source in STADIUM_BUILTINS:
            self.assertNotIn(template_source, BUILTIN_CHOICES)
            self.assertNotIn(
                template_source,
                [c.template_source for c in build_template_source_choices([])],
            )

    def test_template_source_choice_property(self):
        self.assertEqual(TemplateSourceChoice('rigid', 'sm1_ds5').template_source, 'rigid:sm1_ds5')


if __name__ == '__main__':
    unittest.main()


class BuiltinTemplateMetadataTests(unittest.TestCase):
    """Five built-in shader modes, but only the ones
    confirmed in game are offered by the dialogs."""

    def test_stadium_builtins_export_no_normals(self):
        for name, template in BUILTIN_TEMPLATES.items():
            with self.subTest(name):
                self.assertEqual(builtin_template_normals(name), not template.stadium_only)
        with self.assertRaisesRegex(ValueError, 'unknown template'):
            builtin_template_normals('nope')

    def test_stadium_chunks(self):
        self.assertEqual(list(STADIUM_CHUNKS), list(range(7, 17)))
        self.assertTrue(is_stadium_chunk(10))
        for chunk in (None, 5, 6, 17, 136, '6', '17', 'ten', '', 10.5, 17.0, True):
            self.assertFalse(is_stadium_chunk(chunk), chunk)
        # A whole number stored as a float or as text (a hand-edited file) is that number.
        for chunk in (10.0, '10', ' 16 '):
            self.assertTrue(is_stadium_chunk(chunk), chunk)

    def test_registry_covers_the_five_shader_modes(self):
        self.assertEqual(
            sorted({t.shader_mode for t in BUILTIN_TEMPLATES.values()}),
            ['GhSp', 'LhSp', 'RhSp', 'Shdw', 'Spec'],
        )

    def test_only_verified_templates_are_offered(self):
        self.assertEqual(BUILTIN_TEMPLATE_NAMES, (
            'rigid_spec_v1', 'rigid_shdw_v1', 'rigid_ghsp_v1', 'rigid_rhsp_v1', 'rigid_lhsp_v1',
            'stadium_shdw_opaque_v1', 'stadium_shdw_cutout_v1',
        ))
        for name, template in BUILTIN_TEMPLATES.items():
            with self.subTest(name):
                self.assertEqual(
                    name in BUILTIN_TEMPLATE_NAMES, template.verified_in_game,
                )

    def test_hand_visibility_roles_are_only_reachable_through_a_builtin(self):
        """`rigid:`/`derived:` keep excluding the roles, so each one has a
        named built-in instead."""
        for role in ('RhSp', 'LhSp', 'GhSp'):
            with self.subTest(role):
                self.assertIn(role, HAND_VISIBILITY_ROLES)
                self.assertIn(
                    role, [t.shader_mode for t in BUILTIN_TEMPLATES.values()],
                )

    def test_shdw_builtin_binds_one_layer(self):
        self.assertEqual(builtin_template_layers('rigid_shdw_v1'), 1)
        self.assertEqual(builtin_template_layers('rigid_spec_v1'), 2)

    def test_unknown_builtin_layer_lookup_raises(self):
        with self.assertRaisesRegex(ValueError, 'unknown template'):
            builtin_template_layers('rigid_nope_v1')

    def test_builtin_surfaces_start_at_specular_strength_50(self):
        self.assertEqual(BUILTIN_DEFAULT_SPECULAR_STRENGTH, 50)


class BuiltinRegistryMirrorTests(unittest.TestCase):
    """The Blender add-on cannot import HammerspaceMain, so TemplateSources mirrors the
    patcher's registry by hand. These catch drift."""

    def test_addon_mirror_matches_the_registry(self):
        registry = hammerspace._CUSTOM_SUBMESH_BUILTIN_TEMPLATES
        self.assertEqual(sorted(TemplateSources.BUILTIN_TEMPLATES), sorted(registry))
        for name, template in registry.items():
            with self.subTest(name):
                mirror = TemplateSources.BUILTIN_TEMPLATES[name]
                self.assertEqual(mirror.layers, template['Layers'])
                self.assertEqual(mirror.shader_mode, template['ShaderMode'])
                self.assertEqual(mirror.verified_in_game, template['VerifiedInGame'])
                self.assertEqual(mirror.normals, template.get('Normals', True))
                self.assertEqual(mirror.stadium_only, template.get('StadiumOnly', False))
                self.assertTrue(mirror.description)
        self.assertEqual(
            TemplateSources.BUILTIN_TEMPLATE_NAMES,
            hammerspace.builtin_template_names(verified_only=True),
        )

    def test_stadium_and_specular_mirrors_match_the_tools(self):
        self.assertEqual(tuple(TemplateSources.STADIUM_CHUNKS), tuple(hammerspace.STADIUM_CHUNKS))
        self.assertEqual(
            TemplateSources.SPECULAR_SHADER_MODES,
            hammerspace._CUSTOM_SUBMESH_SPECULAR_SHADER_MODES,
        )

    def test_shader_mode_class_mirrors_match_the_in_place_patcher(self):
        # patch_inplace.py is a script (importing it runs a patch), so its two sets are read from the source.
        source = (ROOT / 'SluggiesTools' / 'InplacePatcher' / 'patch_inplace.py').read_text(encoding='utf-8')
        patcher = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id in ('_LIGHTING_MODES', '_NO_LIGHTING_MODES'):
                patcher[node.targets[0].id] = ast.literal_eval(node.value.args[0])
        self.assertEqual(set(TemplateSources.LIT_SHADER_MODES), patcher['_LIGHTING_MODES'])
        self.assertEqual(set(TemplateSources.UNLIT_SHADER_MODES), patcher['_NO_LIGHTING_MODES'])
        for modes in (TemplateSources.LIT_SHADER_MODES, TemplateSources.UNLIT_SHADER_MODES):
            self.assertEqual(len(set(modes)), len(modes))
            for mode in modes:
                self.assertIn(mode, TemplateSources.SHADER_MODE_DESCRIPTIONS)

    def test_hand_visibility_role_mirror_matches_the_tools(self):
        self.assertEqual(
            TemplateSources.HAND_VISIBILITY_ROLES,
            hammerspace._CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES,
        )


class TemplateSurfaceIdTests(unittest.TestCase):
    """The donor surface a new surface takes its starting specular strength
    from; built-ins have none."""

    def test_rigid_and_derived_name_their_donor_surface(self):
        self.assertEqual(template_surface_id('rigid:sm1_ds5'), 'sm1_ds5')
        self.assertEqual(template_surface_id('derived:sm0_ds4'), 'sm0_ds4')

    def test_builtin_and_malformed_sources_have_no_donor_surface(self):
        for source in ('builtin:rigid_spec_v1', 'sm1_ds5', '', 'other:sm1_ds5'):
            with self.subTest(source=source):
                self.assertEqual(template_surface_id(source), '')


class NextNewSurfaceKeyTests(unittest.TestCase):
    """Add material's SurfaceId
    allocator, `<owner>_new<K>`."""

    def test_first_key_for_donor_owner(self):
        self.assertEqual(next_new_surface_key('sm1', []), 'sm1_new0')

    def test_first_key_for_custom_owner(self):
        self.assertEqual(next_new_surface_key('custom0', []), 'custom0_new0')

    def test_skips_used_indices(self):
        self.assertEqual(
            next_new_surface_key('sm1', ['sm1_new0', 'sm1_new1']), 'sm1_new2')

    def test_gap_left_by_a_deleted_surface_is_reused(self):
        self.assertEqual(
            next_new_surface_key('sm1', ['sm1_new0', 'sm1_new2']), 'sm1_new1')

    def test_donor_and_custom_owners_stay_separate(self):
        self.assertEqual(
            next_new_surface_key('custom0', ['sm1_new0']), 'custom0_new0')

    def test_unrelated_surface_ids_are_ignored(self):
        self.assertEqual(
            next_new_surface_key('sm1', ['sm1_ds5', 'sm10_new0']), 'sm1_new0')

    def test_result_never_matches_a_donor_surface_id_pattern(self):
        key = next_new_surface_key('sm1', [])
        self.assertIsNone(re.match(r'^sm\d+_ds\d+$', key))
