"""Manage texture slots helpers (BlenderAddonSrc/TextureSlotsCore.py), bpy-free."""

import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BLENDER_ADDON_DIR = ROOT / 'BlenderAddonSrc'
if str(BLENDER_ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_ADDON_DIR))

import TextureSlotsCore as core  # noqa: E402

BOO = os.path.join('assets', '32 Boo')
HIGH = os.path.join(BOO, '134010752_teresa.gpl', '134010752_teresa.gpl.sluggie')
LOW = os.path.join(BOO, '134164672_L_teresa.gpl', '134164672_L_teresa.gpl.sluggie')
KING_HIGH = os.path.join('assets', '55 King Boo', '213952768_king_teresa.gpl', 'x.sluggie')


class TextureSlotsTests(unittest.TestCase):
    def test_lists_descriptors_then_the_high_models_additions(self):
        model = {
            'TextureDescriptors': [
                {'TextureIndex': 0, 'TextureFileName': 'a.png'},
                {'TextureIndex': 1, 'TextureFileName': 'b.png'},
            ],
            'AdditionalTextureDescriptors': [
                {'TextureFileName': 'a_orig.png', 'TemplateTextureIndex': 0},
                {'TextureFileName': 'Bow.png', 'TemplateTextureIndex': 0},
            ],
        }
        self.assertEqual(core.texture_slots(model), [
            core.TextureSlot(0, 'a.png', False),
            core.TextureSlot(1, 'b.png', False),
            core.TextureSlot(2, 'a_orig.png', True),
            core.TextureSlot(3, 'Bow.png', True),
        ])

    def test_repeated_addition_names_list_no_additions(self):
        model = {
            'TextureDescriptors': [{'TextureIndex': 0, 'TextureFileName': 'a.png'}],
            'AdditionalTextureDescriptors': [{'TextureFileName': 'n.png'}, {'TextureFileName': 'n.png'}],
        }
        self.assertEqual([slot.index for slot in core.texture_slots(model)], [0])

    def test_slot_for_file_matches_exactly(self):
        slots = [core.TextureSlot(0, 'a.png', False), core.TextureSlot(3, 'Bow.png', True)]
        self.assertEqual(core.slot_for_file(slots, 'Bow.png'), 3)
        self.assertIsNone(core.slot_for_file(slots, 'bow.png'))
        self.assertIsNone(core.slot_for_file(slots, ''))


class LodPairTests(unittest.TestCase):
    def test_low_folder_pairs_with_its_sibling_high_folder(self):
        self.assertTrue(core.is_lod_pair(HIGH, LOW))

    def test_order_matters(self):
        self.assertFalse(core.is_lod_pair(LOW, HIGH))

    def test_other_character_folder_does_not_pair(self):
        self.assertFalse(core.is_lod_pair(KING_HIGH, LOW))

    def test_same_suffix_in_another_parent_does_not_pair(self):
        other = os.path.join('elsewhere', '134010752_teresa.gpl', 'x.sluggie')
        self.assertFalse(core.is_lod_pair(other, LOW))

    def test_is_low_path(self):
        self.assertTrue(core.is_low_path(LOW))
        self.assertFalse(core.is_low_path(HIGH))

    def test_missing_path_does_not_pair(self):
        self.assertFalse(core.is_lod_pair('', LOW))

    def test_slots_show_the_refilled_png(self):
        model = {
            'TextureDescriptors': [
                {'TextureIndex': 0, 'TextureFileName': 'a.png'},
                {'TextureIndex': 1, 'TextureFileName': 'b.png'},
            ],
            'TextureReplacements': [{'TextureIndex': 1, 'TextureFileName': 'new.png'}],
        }
        self.assertEqual(core.texture_slots(model), [
            core.TextureSlot(0, 'a.png', False),
            core.TextureSlot(1, 'new.png', False),
        ])


def _setter(texture_index, layer=0):
    return {'DisplayStateId': 1, 'ShaderMode': f'{(layer << 13) | texture_index:08x}'}


def _drawn(surface_id):
    return {'DisplayStateId': 7, 'SurfaceId': surface_id, 'FaceCount': 4}


def _reuse_model(**extra):
    """Two submeshes drawing texture 0, submesh 1 also texture 1; the
    export re-pointed both texture-0 surfaces at new PNGs."""
    model = {
        'TextureDescriptors': [
            {'TextureIndex': i, 'TextureFileName': f'{i}.png', 'Format': 14}
            for i in range(3)
        ],
        'Submeshes': [
            {'DisplayStates': [_setter(0), _drawn('sm0_ds1')]},
            {'DisplayStates': [_setter(0), _drawn('sm1_ds1'), _setter(1), _drawn('sm1_ds3')]},
        ],
        'AdditionalTextureDescriptors': [
            {'TextureFileName': 'new.png', 'TemplateTextureIndex': 0},
            {'TextureFileName': 'other.png', 'TemplateTextureIndex': 0},
        ],
        'DesiredTextureAssignments': {'sm0_ds1': 3, 'sm1_ds1': 4},
    }
    model.update(extra)
    return model


class ReuseFreedTextureSlotsTests(unittest.TestCase):
    def test_first_png_fills_the_slot_and_the_rest_stay_appended(self):
        model = _reuse_model()
        messages = core.reuse_freed_texture_slots(model)
        self.assertEqual(model['TextureReplacements'], [{'TextureIndex': 0, 'TextureFileName': 'new.png'}])
        self.assertEqual(model['AdditionalTextureDescriptors'], [
            {'TextureFileName': 'other.png', 'TemplateTextureIndex': 0},
        ])
        self.assertEqual(model['DesiredTextureAssignments'], {'sm0_ds1': 0, 'sm1_ds1': 3})
        self.assertEqual(len(messages), 1)

    def test_last_addition_moved_drops_the_list(self):
        model = _reuse_model(
            AdditionalTextureDescriptors=[{'TextureFileName': 'new.png', 'TemplateTextureIndex': 0}],
            DesiredTextureAssignments={'sm0_ds1': 3, 'sm1_ds1': 3},
        )
        core.reuse_freed_texture_slots(model)
        self.assertNotIn('AdditionalTextureDescriptors', model)
        self.assertEqual(model['DesiredTextureAssignments'], {'sm0_ds1': 0, 'sm1_ds1': 0})

    def test_a_surface_still_showing_the_texture_keeps_it(self):
        model = _reuse_model(DesiredTextureAssignments={'sm0_ds1': 3})
        self.assertEqual(core.reuse_freed_texture_slots(model), [])
        self.assertNotIn('TextureReplacements', model)
        self.assertEqual(len(model['AdditionalTextureDescriptors']), 2)

    def test_other_layer_binding_keeps_the_texture(self):
        model = _reuse_model()
        model['Submeshes'][1]['DisplayStates'].insert(0, _setter(0, layer=1))
        self.assertEqual(core.reuse_freed_texture_slots(model), [])

    def test_custom_submesh_donor_binding_keeps_the_texture(self):
        model = _reuse_model(CustomSubmeshes=[{'TextureAssignment': {'DonorTextureIndex': 0}}])
        self.assertEqual(core.reuse_freed_texture_slots(model), [])

    def test_name_bound_png_is_not_moved(self):
        model = _reuse_model(CustomSubmeshes=[
            {'TextureAssignment': {'AdditionalTextureFileName': 'new.png'}},
        ])
        core.reuse_freed_texture_slots(model)
        # new.png stays appended for the custom submesh; other.png fills.
        self.assertEqual(model['TextureReplacements'], [{'TextureIndex': 0, 'TextureFileName': 'other.png'}])
        self.assertEqual(model['DesiredTextureAssignments'], {'sm0_ds1': 3, 'sm1_ds1': 0})

    def test_mipmapped_or_indexed_slot_is_not_refilled(self):
        for field, value in (('AdditionalMipCount', 2), ('Format', 0x9)):
            model = _reuse_model()
            model['TextureDescriptors'][0][field] = value
            self.assertEqual(core.reuse_freed_texture_slots(model), [], field)

    def test_stale_replacements_are_cleared(self):
        model = _reuse_model(
            DesiredTextureAssignments={},
            TextureReplacements=[{'TextureIndex': 0, 'TextureFileName': 'new.png'}],
        )
        core.reuse_freed_texture_slots(model)
        self.assertNotIn('TextureReplacements', model)

    def test_replaced_descriptors_rename_the_slot(self):
        descriptors = [{'TextureIndex': 0, 'TextureFileName': 'a.png'}]
        self.assertEqual(
            core.replaced_texture_descriptors(descriptors, [{'TextureIndex': 0, 'TextureFileName': 'n.png'}]),
            [{'TextureIndex': 0, 'TextureFileName': 'n.png'}],
        )
        self.assertEqual(descriptors[0]['TextureFileName'], 'a.png')


if __name__ == '__main__':
    unittest.main()
