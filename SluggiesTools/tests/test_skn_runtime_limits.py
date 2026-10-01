"""SKN runtime-limit guards outside the block validator
(``_docs/_docs_model_format/skn_section.html#runtime-limits``): the in-place
patcher's pre-write check and the hammerspace skin-bone check."""
import pathlib
import sys
import unittest


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
for import_path in (TOOLS_DIR, TOOLS_DIR / 'Hammerspace', TOOLS_DIR / 'InplacePatcher'):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import HammerspaceMain as main
import patch_skn_inplace as skn
from binfmt import skin_bone_ids, skn_direct_entry_problem


def _skin(sk1_counts=(3,), sk2_counts=(3,), quantize_info=0x30):
    """Stride 12 (comp size 2): SK1s on bones 10, 11, ...; SK2 pair (20, 21);
    one SKAcc on bone 30."""
    return {
        'QuantizeInfo': quantize_info,
        'SK1s': [{'BoneIndex': 10 + i, 'VertexCnt': n, 'VertexOffset': 0}
                 for i, n in enumerate(sk1_counts)],
        'SK2s': [{'BoneIndex1': 20, 'BoneIndex2': 21, 'VertexCnt': n, 'VertexOffset': 4}
                 for n in sk2_counts],
        'SKAccs': [{'BoneIndex': 30, 'VertexCnt': 2}],
    }


class SharedRuleTests(unittest.TestCase):
    def test_minimum_is_three_vertices(self):
        self.assertIsNotNone(skn_direct_entry_problem('SK1', 2, 0, 12))
        self.assertIsNone(skn_direct_entry_problem('SK1', 3, 0, 12))

    def test_skin_bone_ids_covers_every_entry_kind(self):
        self.assertEqual(skin_bone_ids(_skin()), {10, 20, 21, 30})
        self.assertEqual(skin_bone_ids(None), set())


class InPlaceSknEditProblemsTests(unittest.TestCase):
    def test_unedited_skin_has_no_problems(self):
        self.assertEqual(skn.skn_edit_problems(_skin()), [])

    def test_shrinking_an_entry_below_three_vertices_is_refused(self):
        skin = _skin()
        skin['SK2s'][0]['VertexCntEdited'] = 1
        problems = skn.skn_edit_problems(skin)
        self.assertEqual(len(problems), 1)
        self.assertIn('SK2[0]: SK2 entry has 1 vertices', problems[0])

    def test_entry_over_the_buffer_is_refused(self):
        skin = _skin(sk1_counts=(682,))      # 682 x 12 = 8184 > 8180
        self.assertIn('at most 8180', skn.skn_edit_problems(skin)[0])

    def test_float_stride_is_used_for_the_size_limit(self):
        # quantize nibble 4 = float: stride 24, so 341 x 24 = 8184 > 8180
        skin = _skin(sk1_counts=(341,), quantize_info=0x40)
        self.assertIn('8184 bytes', skn.skn_edit_problems(skin)[0])

    def test_sk1_bone_edit_to_an_unskinned_bone_is_refused(self):
        skin = _skin()
        skin['SK1s'][0]['BoneIndexEdited'] = 5
        self.assertIn('bone 5 is not one of the bones', skn.skn_edit_problems(skin)[0])

    def test_sk1_bone_edit_to_another_skinned_bone_is_allowed(self):
        skin = _skin()
        skin['SK1s'][0]['BoneIndexEdited'] = 21
        self.assertEqual(skn.skn_edit_problems(skin), [])


class HammerspaceSkinBoneTests(unittest.TestCase):
    def test_edited_skin_on_donor_skinned_bones_passes(self):
        edited = _skin()
        edited['SK1s'][0]['BoneIndex'] = 30          # moved onto the SKAcc bone
        main._validate_skin_bones({'SkinData': _skin(), 'SkinDataEdited': edited})

    def test_edited_skin_on_a_bone_the_donor_never_skins_is_refused(self):
        edited = _skin()
        edited['SK2s'][0]['BoneIndex2'] = 7
        with self.assertRaisesRegex(ValueError, r"bone\(s\) \[7\], which the donor's skin never uses"):
            main._validate_skin_bones({'SkinData': _skin(), 'SkinDataEdited': edited})

    def test_no_skin_edit_is_a_noop(self):
        main._validate_skin_bones({'SkinData': _skin()})


if __name__ == '__main__':
    unittest.main()
