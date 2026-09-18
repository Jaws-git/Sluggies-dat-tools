import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BLENDER_ADDON_DIR = ROOT / 'BlenderAddonSrc'
if str(BLENDER_ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_ADDON_DIR))

from TemplateSources import (  # noqa: E402
    BUILTIN_TEMPLATES,
    BUILTIN_TEMPLATE_NAMES,
    HAND_VISIBILITY_ROLES,
    TemplateSourceChoice,
    TemplateSourceMaterial,
    build_template_source_choices,
    builtin_template_layers,
    next_new_surface_key,
)


def _mat(surface_id, comp_count, shader_mode):
    return TemplateSourceMaterial(surface_id=surface_id, comp_count=comp_count, shader_mode=shader_mode)


class BuildTemplateSourceChoicesTests(unittest.TestCase):
    def test_builtin_always_present_even_with_no_materials(self):
        choices = build_template_source_choices([])
        self.assertEqual([c.template_source for c in choices], ['builtin:rigid_spec_v1'])

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
        """Decision 9: built-ins first, so the dialog's first item -- the one
        it preselects -- is builtin:rigid_spec_v1 even here, where the model
        has both a copyable rigid surface and a derivable skinned one."""
        choices = build_template_source_choices([
            _mat('sm0_ds5', 6, 'Spec'),
            _mat('sm1_ds5', 3, 'Spec'),
        ])
        self.assertEqual(
            [c.template_source for c in choices],
            ['builtin:rigid_spec_v1', 'rigid:sm1_ds5', 'derived:sm0_ds5'],
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

    def test_duplicate_surface_ids_are_deduplicated(self):
        choices = build_template_source_choices([
            _mat('sm1_ds5', 3, 'Spec'),
            _mat('sm1_ds5', 3, 'Spec'),
        ])
        rigid_choices = [c for c in choices if c.kind == 'rigid']
        self.assertEqual(len(rigid_choices), 1)

    def test_other_comp_counts_are_ignored(self):
        choices = build_template_source_choices([_mat('sm2_ds1', 4, 'Spec')])
        self.assertEqual([c.template_source for c in choices], ['builtin:rigid_spec_v1'])

    def test_template_source_choice_property(self):
        self.assertEqual(TemplateSourceChoice('rigid', 'sm1_ds5').template_source, 'rigid:sm1_ds5')


if __name__ == '__main__':
    unittest.main()


class BuiltinTemplateMetadataTests(unittest.TestCase):
    """Decision 9: five built-in shader modes, but only the ones Phase 0
    probe 7 has confirmed in game are offered by the dialogs."""

    def test_registry_covers_the_five_shader_modes(self):
        self.assertEqual(
            sorted(t.shader_mode for t in BUILTIN_TEMPLATES.values()),
            ['GhSp', 'LhSp', 'RhSp', 'Shdw', 'Spec'],
        )

    def test_only_verified_templates_are_offered(self):
        self.assertEqual(BUILTIN_TEMPLATE_NAMES, ('rigid_spec_v1',))
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


class NextNewSurfaceKeyTests(unittest.TestCase):
    """PLAN_EditRigidMeshes.md decision 8: Add material's SurfaceId
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
