"""PLAN_AddBones.md Phase 3 - validation.

Exercises ``HammerspaceMain._validate_bone_hierarchy_edited`` directly against
synthetic ``BoneHierarchy``/``BoneHierarchyEdited`` JSON -- this validator
operates purely at the ``.sluggie`` data level (no ACT bytes involved), so
unlike the Phase 2 rebuild tests it needs no donor DAT/DOL assets and runs
unconditionally.

One rejection test per Phase 3 rule, plus an acceptance test, plus a
dedicated fixture for each of rule 3's named topology cases (reparent,
mid-chain swap, mid-chain insertion) and rule 4's donor-onto-new case.
"""
from __future__ import annotations

import copy
import os
import sys
import unittest

TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
HAMMERSPACE_DIR = os.path.join(TOOLS_DIR, 'Hammerspace')
for _path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import HammerspaceMain as hammerspace  # noqa: E402


def _donor_bone(bone_id, parent_id, **overrides):
    bone = {
        'BoneId': bone_id,
        'GeoId': 0xFFFF,
        'ParentBoneId': parent_id,
        'Skinned': False,
        'TrackId': 0xFFFF,
        'MirrorBoneId': bone_id,
        'MirrorRole': 3,
        'SRTType': 0xC,
        'DrawPriority': 0,
        'InheritTransform': True,
        'Translation': [0.0, 0.0, 0.0],
        'Scale': [1.0, 1.0, 1.0],
        'Quaternion': [1.0, 0.0, 0.0, 0.0],
        'HeadPosition': [0.0, 0.0, 0.0],
        'VertexInfluences': [],
    }
    bone.update(overrides)
    return bone


def _new_bone(bone_id, parent_id, **overrides):
    bone = {
        'BoneId': bone_id,
        'GeoId': 0xFFFF,
        'ParentBoneId': parent_id,
        'Skinned': False,
        'TrackId': 0xFFFF,
        'MirrorBoneId': bone_id,
        'MirrorRole': 3,
        'SRTType': 0xC,
        'DrawPriority': 0,
        'InheritTransform': True,
        'UserAdded': True,
        'Translation': [0.0, 0.0, 0.3],
        'Scale': [1.0, 1.0, 1.0],
        'Quaternion': [1.0, 0.0, 0.0, 0.0],
        'VertexInfluences': [],
    }
    bone.update(overrides)
    return bone


def _base_model():
    """4-bone donor: 0 (root) -> 1, 2 (children of 0); 3 (child of 1)."""
    donor = [
        _donor_bone(0, None),
        _donor_bone(1, 0),
        _donor_bone(2, 0),
        _donor_bone(3, 1),
    ]
    edited = []
    for b in donor:
        eb = copy.deepcopy(b)
        eb.pop('HeadPosition', None)
        eb['UserAdded'] = False
        edited.append(eb)
    return {
        'SluggiesModel': {
            'UseHammerspace': True,
            'BoneHierarchy': donor,
            'BoneHierarchyEdited': edited,
        }
    }


def _validate(data):
    hammerspace._validate_bone_hierarchy_edited(data['SluggiesModel'])


class BoneHierarchyValidationTests(unittest.TestCase):
    def test_no_bone_hierarchy_edited_is_a_no_op(self):
        data = _base_model()
        data['SluggiesModel']['BoneHierarchyEdited'] = None
        _validate(data)  # must not raise

    def test_acceptance_one_new_leaf_on_a_donor_bone(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'].append(_new_bone(4, 1))
        _validate(data)  # must not raise

    def test_acceptance_new_bone_chained_onto_another_new_bone(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'].append(_new_bone(4, 1))
        model['BoneHierarchyEdited'].append(_new_bone(5, 4))
        _validate(data)  # must not raise

    def test_rule1_hammerspace_flag_required(self):
        data = _base_model()
        data['SluggiesModel']['UseHammerspace'] = False
        data['SluggiesModel']['BoneHierarchyEdited'].append(_new_bone(4, 1))
        with self.assertRaisesRegex(ValueError, 'Hammerspace Mode'):
            _validate(data)

    def test_rule2_donor_bone_deleted(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'] = [b for b in model['BoneHierarchyEdited'] if b['BoneId'] != 3]
        with self.assertRaisesRegex(ValueError, 'donor bone 3 is missing'):
            _validate(data)

    def test_rule2_donor_geo_id_changed_without_claim(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'][2]['GeoId'] = 5
        with self.assertRaisesRegex(ValueError, 'GeoId changed'):
            _validate(data)

    def test_rule2_donor_mirror_pair_changed(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'][2]['MirrorBoneId'] = 1
        with self.assertRaisesRegex(ValueError, 'mirror pair changed'):
            _validate(data)

    def test_rule3_donor_bone_reparented(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'][2]['ParentBoneId'] = 3  # bone 2: 0 -> 3
        with self.assertRaisesRegex(ValueError, 'reparented from 0 to 3'):
            _validate(data)

    def test_rule3_donor_bones_swap_parent_child(self):
        data = _base_model()
        model = data['SluggiesModel']
        # Donor: 1's parent is 0, 3's parent is 1. Swap so 1's parent becomes 3.
        model['BoneHierarchyEdited'][1]['ParentBoneId'] = 3  # bone 1: 0 -> 3
        model['BoneHierarchyEdited'][3]['ParentBoneId'] = 0  # bone 3: 1 -> 0 (already reparented above)
        with self.assertRaisesRegex(ValueError, 'swapped their parent/child relation'):
            _validate(data)

    def test_rule3_new_bone_inserted_mid_chain(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'].append(_new_bone(4, 1))
        model['BoneHierarchyEdited'][3]['ParentBoneId'] = 4  # bone 3: 1 -> new bone 4
        with self.assertRaisesRegex(ValueError, 'was inserted between donor bones'):
            _validate(data)

    def test_rule4_donor_bone_names_new_bone_as_parent(self):
        # Same data shape as the mid-chain-insertion case, but this asserts
        # rule 4's own independent message fires (both rules currently flag
        # the same case; this locks in rule 4's wording specifically).
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'].append(_new_bone(4, 1))
        model['BoneHierarchyEdited'][3]['ParentBoneId'] = 4
        with self.assertRaisesRegex(ValueError, 'new bones must be leaves'):
            _validate(data)

    def test_rule5_new_bone_ids_not_contiguous(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'].append(_new_bone(9, 1))
        with self.assertRaisesRegex(ValueError, 'contiguous starting at the donor bone count'):
            _validate(data)

    def test_rule6_total_bone_count_exceeds_cap(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'] += [_new_bone(4 + i, 1) for i in range(252)]
        with self.assertRaisesRegex(ValueError, '255-bone cap'):
            _validate(data)

    def test_rule7_new_bone_has_no_parent(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'].append(_new_bone(4, None))
        with self.assertRaisesRegex(ValueError, 'may not be roots'):
            _validate(data)

    def test_rule7_new_bone_parent_does_not_exist(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'].append(_new_bone(4, 99))
        with self.assertRaisesRegex(ValueError, "parent 99 does not exist"):
            _validate(data)

    def test_rule8_new_bone_has_a_track(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'].append(_new_bone(4, 1, TrackId=7))
        with self.assertRaisesRegex(ValueError, 'TrackId 7'):
            _validate(data)

    def test_rule8_new_bone_mirrors_another_bone(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'].append(_new_bone(4, 1, MirrorBoneId=1))
        with self.assertRaisesRegex(ValueError, 'MirrorBoneId 1'):
            _validate(data)

    def test_rule8_new_bone_has_wrong_mirror_role(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'].append(_new_bone(4, 1, MirrorRole=1))
        with self.assertRaisesRegex(ValueError, 'MirrorRole 1'):
            _validate(data)

    def test_rule9_donor_mirror_table_not_an_involution(self):
        data = _base_model()
        model = data['SluggiesModel']
        # Break the involution on the donor side itself (bone 2 mirrors bone 1,
        # but bone 1 still mirrors itself) -- present on both BoneHierarchy and
        # BoneHierarchyEdited so rule 2's "unchanged mirror pair" check doesn't
        # fire first.
        model['BoneHierarchy'][2]['MirrorBoneId'] = 1
        model['BoneHierarchyEdited'][2]['MirrorBoneId'] = 1
        with self.assertRaisesRegex(ValueError, 'not an involution'):
            _validate(data)

    def test_rule10_skin_data_references_a_new_bone(self):
        data = _base_model()
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'].append(_new_bone(4, 1))
        model['SkinData'] = {'SK1s': [{'BoneIndex': 4}], 'SK2s': [], 'SKAccs': []}
        with self.assertRaisesRegex(ValueError, 'SK1 entry names bone 4'):
            _validate(data)


if __name__ == '__main__':
    unittest.main()
