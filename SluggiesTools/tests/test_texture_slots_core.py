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


if __name__ == '__main__':
    unittest.main()
