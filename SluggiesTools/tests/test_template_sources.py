import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BLENDER_ADDON_DIR = ROOT / 'BlenderAddonSrc'
if str(BLENDER_ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_ADDON_DIR))

from TemplateSources import (  # noqa: E402
    HAND_VISIBILITY_ROLES,
    TemplateSourceChoice,
    TemplateSourceMaterial,
    build_template_source_choices,
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
        self.assertEqual(choices[0].template_source, 'rigid:sm1_ds5')

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

    def test_resolution_order_is_rigid_then_derived_then_builtin(self):
        choices = build_template_source_choices([
            _mat('sm0_ds5', 6, 'Spec'),
            _mat('sm1_ds5', 3, 'Spec'),
        ])
        self.assertEqual(
            [c.template_source for c in choices],
            ['rigid:sm1_ds5', 'derived:sm0_ds5', 'builtin:rigid_spec_v1'],
        )

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
