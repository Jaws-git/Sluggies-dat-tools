import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BLENDER_ADDON_DIR = ROOT / 'BlenderAddonSrc'
IMPORTER_PATH = BLENDER_ADDON_DIR / 'ImportSluggies.py'
if str(BLENDER_ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_ADDON_DIR))

from HostBones import (  # noqa: E402
    BONE_METADATA_VERSION,
    GEO_ID_FREE,
    BoneRecord,
    HostBoneChoice,
    RigidRetarget,
    SceneClaims,
    STATUS_ALLOWED,
    STATUS_DRIVES_SKINNING,
    STATUS_EXCLUDED,
    STATUS_RECOMMENDED,
    bone_metadata_is_current,
    bone_records_from_hierarchy,
    classify_host_bones,
    compute_rigid_retargets,
    order_host_bone_choices,
    reassignment_choices,
    skn_bone_ids,
)



def _rec(bone_id, parent_id, geo_id_raw=GEO_ID_FREE, skinned=False):
    return BoneRecord(bone_id=bone_id, parent_id=parent_id, geo_id_raw=geo_id_raw, skinned=skinned)


class ClassifyHostBonesTests(unittest.TestCase):
    def test_donor_mesh_owner_excluded(self):
        records = [_rec(1, None, geo_id_raw=3)]
        choices = classify_host_bones(records)
        self.assertEqual(choices[0].status, STATUS_EXCLUDED)

    def test_mesh_free_non_root_non_skinned_is_recommended(self):
        records = [_rec(1, None), _rec(2, 1)]
        choices = classify_host_bones(records)
        self.assertEqual(choices[1].status, STATUS_RECOMMENDED)

    def test_mesh_free_root_is_allowed(self):
        records = [_rec(1, None)]
        choices = classify_host_bones(records)
        self.assertEqual(choices[0].status, STATUS_ALLOWED)

    def test_skn_bone_tagged_drives_skinning_not_excluded(self):
        records = [_rec(1, None), _rec(2, 1, skinned=True)]
        choices = classify_host_bones(records)
        self.assertEqual(choices[1].status, STATUS_DRIVES_SKINNING)

    def test_retarget_frees_old_owner_and_occupies_new_bone(self):
        records = [_rec(1, None, geo_id_raw=0), _rec(2, 1)]
        claims = SceneClaims(retargets=(RigidRetarget(submesh_index=0, from_bone_id=1, to_bone_id=2),))
        choices = {c.bone_id: c for c in classify_host_bones(records, claims)}
        self.assertEqual(choices[1].status, STATUS_ALLOWED)
        self.assertEqual(choices[2].status, STATUS_EXCLUDED)

    def test_custom_submesh_claim_excludes_bone(self):
        records = [_rec(1, None), _rec(2, 1)]
        claims = SceneClaims(custom_submesh_bone_ids=frozenset({2}))
        choices = {c.bone_id: c for c in classify_host_bones(records, claims)}
        self.assertEqual(choices[2].status, STATUS_EXCLUDED)

    def test_single_bone_prop_yields_only_excluded(self):
        records = [_rec(1, None, geo_id_raw=0)]
        choices = classify_host_bones(records)
        self.assertTrue(all(c.status == STATUS_EXCLUDED for c in choices))
        ordered = order_host_bone_choices(choices, records)
        self.assertEqual(ordered, [])


class OrderHostBoneChoicesTests(unittest.TestCase):
    def test_groups_ordered_recommended_allowed_driving_skinning_excluded_dropped(self):
        records = [
            _rec(1, None),            # root, mesh-free -> allowed
            _rec(2, 1),                # leaf, mesh-free -> recommended
            _rec(3, 1, skinned=True),  # skinned -> drives_skinning
            _rec(4, 1, geo_id_raw=5),  # owned -> excluded
        ]
        choices = classify_host_bones(records)
        ordered = order_host_bone_choices(choices, records)
        self.assertEqual([c.bone_id for c in ordered], [2, 1, 3])

    def test_recommended_group_lists_leaves_before_inner_bones(self):
        # bone 2 is inner (parent of 3); bone 3 is a leaf.
        records = [_rec(1, None), _rec(2, 1), _rec(3, 2)]
        choices = classify_host_bones(records)
        ordered = order_host_bone_choices(choices, records)
        self.assertEqual([c.bone_id for c in ordered if c.status == STATUS_RECOMMENDED], [3, 2])


class ComputeRigidRetargetsTests(unittest.TestCase):
    def test_simple_retarget(self):
        retargets, issues = compute_rigid_retargets(
            owner_bone_by_submesh={0: 1},
            bone_geo_raw={1: 0, 2: GEO_ID_FREE},
            target_bone_by_submesh={0: 2},
            known_bone_ids={1, 2},
        )
        self.assertEqual(retargets, [RigidRetarget(submesh_index=0, from_bone_id=1, to_bone_id=2)])
        self.assertEqual(issues, [])

    def test_no_change_when_target_matches_current_owner(self):
        retargets, issues = compute_rigid_retargets(
            owner_bone_by_submesh={0: 1},
            bone_geo_raw={1: 0},
            target_bone_by_submesh={0: 1},
            known_bone_ids={1},
        )
        self.assertEqual(retargets, [])
        self.assertEqual(issues, [])

    def test_unknown_target_bone_is_an_issue(self):
        retargets, issues = compute_rigid_retargets(
            owner_bone_by_submesh={0: 1},
            bone_geo_raw={1: 0},
            target_bone_by_submesh={0: 99},
            known_bone_ids={1},
        )
        self.assertEqual(retargets, [])
        self.assertEqual(issues[0].reason, "unknown_bone")

    def test_two_submeshes_claiming_same_target_bone_conflict(self):
        retargets, issues = compute_rigid_retargets(
            owner_bone_by_submesh={0: 1, 1: 2},
            bone_geo_raw={1: 0, 2: 1, 3: GEO_ID_FREE},
            target_bone_by_submesh={0: 3, 1: 3},
            known_bone_ids={1, 2, 3},
        )
        self.assertEqual(len(retargets), 1)
        self.assertEqual(retargets[0].submesh_index, 0)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].reason, "target_claimed_by_other_submesh")

    def test_target_already_owned_by_a_different_submesh_is_an_issue(self):
        retargets, issues = compute_rigid_retargets(
            owner_bone_by_submesh={0: 1},
            bone_geo_raw={1: 0, 2: 7},
            target_bone_by_submesh={0: 2},
            known_bone_ids={1, 2},
        )
        self.assertEqual(retargets, [])
        self.assertEqual(issues[0].reason, "target_occupied")
        self.assertEqual(issues[0].detail, 7)


class ReassignmentChoicesTests(unittest.TestCase):
    """PLAN_EditRigidMeshes.md Phase 6: host-bone choices for the Reassign to
    new bone dialog."""

    def test_single_bone_prop_yields_an_empty_list(self):
        # The only bone in the model is the donor owner of the submesh being
        # reassigned; moving it "to itself" is not offered.
        records = [_rec(1, None, geo_id_raw=0)]
        ordered = reassignment_choices(records, moving_submesh_index=0)
        self.assertEqual(ordered, [])

    def test_original_owner_reappears_after_a_move(self):
        # bone 1 (root) originally owned submesh 0; the object was already
        # moved onto bone 2 (a retarget records this). Reopening the dialog
        # must offer bone 1 again, but not bone 2 (the object's current bone).
        records = [_rec(1, None, geo_id_raw=0), _rec(2, 1)]
        claims = SceneClaims(retargets=(RigidRetarget(submesh_index=0, from_bone_id=1, to_bone_id=2),))
        ordered = reassignment_choices(records, claims, moving_submesh_index=0)
        self.assertEqual([c.bone_id for c in ordered], [1])

    def test_bone_claimed_by_another_custom_submesh_is_absent(self):
        records = [_rec(1, None, geo_id_raw=0), _rec(2, 1), _rec(3, 1)]
        claims = SceneClaims(custom_submesh_bone_ids=frozenset({2}))
        ordered = reassignment_choices(records, claims, moving_submesh_index=0)
        self.assertEqual([c.bone_id for c in ordered], [3])

    def test_moving_a_custom_submesh_excludes_its_own_current_bone(self):
        records = [_rec(1, None), _rec(2, 1)]
        claims = SceneClaims(custom_submesh_bone_ids=frozenset({2}))
        ordered = reassignment_choices(records, claims, moving_custom_bone_id=2)
        self.assertEqual([c.bone_id for c in ordered], [1])

    def test_moving_a_custom_submesh_does_not_free_another_ones_bone(self):
        records = [_rec(1, None), _rec(2, 1), _rec(3, 1)]
        claims = SceneClaims(custom_submesh_bone_ids=frozenset({2, 3}))
        ordered = reassignment_choices(records, claims, moving_custom_bone_id=2)
        self.assertEqual([c.bone_id for c in ordered], [1])

    def test_no_scene_claims_defaults_to_empty(self):
        records = [_rec(1, None, geo_id_raw=0), _rec(2, 1)]
        ordered = reassignment_choices(records, moving_submesh_index=0)
        self.assertEqual([c.bone_id for c in ordered], [2])


class BoneRecordsFromHierarchyTests(unittest.TestCase):
    def test_reads_geoidraw_and_skinning_from_skin_data(self):
        hierarchy = [
            {"BoneId": 0, "ParentBoneId": None, "GeoIdRaw": GEO_ID_FREE, "Skinned": True},
            {"BoneId": 1, "ParentBoneId": 0, "GeoIdRaw": 2, "Skinned": False},
            {"BoneId": 2, "ParentBoneId": 0, "GeoIdRaw": GEO_ID_FREE, "Skinned": True},
        ]
        skin_data = {"SK1s": [{"BoneIndex": 2}], "SK2s": [], "SKAccs": []}
        records = bone_records_from_hierarchy(hierarchy, skin_data)
        # export.py's "Skinned" only means GeoIdRaw == 0xFFFF; it is ignored.
        self.assertEqual(records[0], BoneRecord(0, None, GEO_ID_FREE, False))
        self.assertEqual(records[1], BoneRecord(1, 0, 2, False))
        self.assertEqual(records[2], BoneRecord(2, 0, GEO_ID_FREE, True))

    def test_no_skin_data_means_no_skinning_bones(self):
        hierarchy = [{"BoneId": 0, "ParentBoneId": None, "GeoIdRaw": GEO_ID_FREE, "Skinned": True}]
        self.assertFalse(bone_records_from_hierarchy(hierarchy)[0].skinned)


class SknBoneIdsTests(unittest.TestCase):
    def test_collects_sk1_sk2_and_skacc_bones(self):
        skin_data = {
            "SK1s": [{"BoneIndex": 3}],
            "SK2s": [{"BoneIndex1": 4, "BoneIndex2": 5}],
            "SKAccs": [{"BoneIndex": 6}, {"BoneIndex": 3}],
        }
        self.assertEqual(skn_bone_ids(skin_data), {3, 4, 5, 6})

    def test_empty_or_missing_skin_data(self):
        self.assertEqual(skn_bone_ids(None), set())
        self.assertEqual(skn_bone_ids({"SK1s": None}), set())


class BoneMetadataVersionTests(unittest.TestCase):
    def test_missing_or_version_1_metadata_needs_reimport(self):
        self.assertFalse(bone_metadata_is_current(None))
        self.assertFalse(bone_metadata_is_current(1))
        self.assertFalse(bone_metadata_is_current('garbage'))

    def test_current_version_is_accepted(self):
        self.assertTrue(bone_metadata_is_current(BONE_METADATA_VERSION))


#: A miniature model shaped like a real player export, in ``.sluggie`` JSON
#: form: one rigid mesh owner on a leaf (a cap on the head), one SKN-driving
#: bone (a hand), one free leaf, one free inner bone and two parentless roots.
#: It stands in for the real Mario/Luigi/Noki exports this class used to read
#: -- those are gitignored working files, so tests must not depend on them
#: (the F7 survey numbers they asserted live in PLAN_AddSubmesh.md F7). What
#: is worth testing is the composition, which a synthetic donor exercises just
#: as well as a 91-bone one.
MINIATURE_MODEL = {
    'SluggiesModel': {
        'BoneHierarchy': [
            {'BoneId': 0, 'ParentBoneId': None, 'GeoIdRaw': 0xFFFF},  # root
            {'BoneId': 1, 'ParentBoneId': None, 'GeoIdRaw': 0xFFFF},  # second root
            {'BoneId': 2, 'ParentBoneId': 0, 'GeoIdRaw': 0xFFFF},     # spine (inner)
            {'BoneId': 3, 'ParentBoneId': 2, 'GeoIdRaw': 0xFFFF},     # hand, drives skinning
            {'BoneId': 4, 'ParentBoneId': 2, 'GeoIdRaw': 0xFFFF},     # head (inner, free)
            {'BoneId': 5, 'ParentBoneId': 4, 'GeoIdRaw': 0},          # cap: rigid owner
            {'BoneId': 6, 'ParentBoneId': 4, 'GeoIdRaw': 0xFFFF},     # free leaf
        ],
        'SkinData': {'SK1s': [{'BoneIndex': 3}], 'SK2s': [], 'SKAccs': []},
    },
}


def _choices(data):
    """The host-bone pipeline as the add-on runs it: hierarchy + SkinData ->
    records -> classified -> ordered."""
    model = data['SluggiesModel']
    records = bone_records_from_hierarchy(model['BoneHierarchy'], model.get('SkinData'))
    ordered = order_host_bone_choices(classify_host_bones(records), records)
    return {c.bone_id: c.status for c in ordered}, len(records)


class ModelShapedHostBoneTests(unittest.TestCase):
    """The whole pipeline over a model-shaped donor, reproducing F7's rules."""

    def test_rigid_owner_is_never_offered(self):
        statuses, bone_count = _choices(MINIATURE_MODEL)
        self.assertEqual(bone_count, 7)
        self.assertNotIn(5, statuses)  # the cap owns a donor mesh (F2)

    def test_skn_bone_is_offered_but_flagged(self):
        statuses, _ = _choices(MINIATURE_MODEL)
        self.assertEqual(statuses[3], STATUS_DRIVES_SKINNING)

    def test_free_bones_are_recommended_and_roots_only_allowed(self):
        statuses, _ = _choices(MINIATURE_MODEL)
        self.assertEqual(statuses[4], STATUS_RECOMMENDED)
        self.assertEqual(statuses[6], STATUS_RECOMMENDED)
        self.assertEqual(statuses[2], STATUS_RECOMMENDED)
        # Parentless bones are legal hosts but ranked last (F7).
        self.assertEqual(statuses[0], STATUS_ALLOWED)
        self.assertEqual(statuses[1], STATUS_ALLOWED)
        safe = [b for b, s in statuses.items() if s in (STATUS_RECOMMENDED, STATUS_ALLOWED)]
        self.assertEqual(sorted(safe), [0, 1, 2, 4, 6])

    def test_a_single_bone_prop_offers_nothing(self):
        # F7: a prop's only bone already owns the prop mesh.
        statuses, bone_count = _choices({'SluggiesModel': {
            'BoneHierarchy': [{'BoneId': 0, 'ParentBoneId': None, 'GeoIdRaw': 0}],
        }})
        self.assertEqual((statuses, bone_count), ({}, 1))


class ImportSluggiesBoneMetadataAstTests(unittest.TestCase):
    """Confirms build_armature writes bone metadata on Bone (not EditBone),
    plus SluggiesBoneMetadataVersion on the armature object, without needing
    bpy to run the function."""

    def setUp(self):
        self.source = IMPORTER_PATH.read_text(encoding='utf-8')
        tree = ast.parse(self.source)
        self.build_armature = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == 'build_armature'
        )

    def _assigned_targets(self):
        targets = []
        for node in ast.walk(self.build_armature):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                        key = t.slice
                        if isinstance(key, ast.Constant):
                            targets.append((t.value.id, key.value))
        return targets

    def test_writes_geo_id_raw_and_skinned_on_bone_variable(self):
        targets = self._assigned_targets()
        self.assertIn(('b', 'SluggiesGeoIdRaw'), targets)
        self.assertIn(('b', 'SluggiesSkinned'), targets)

    def test_skinned_flag_comes_from_skin_data_not_bone_hierarchy(self):
        body = ast.get_source_segment(self.source, self.build_armature)
        self.assertIn('HostBones.skn_bone_ids(skin_data)', body)
        self.assertNotIn("bd.get('Skinned'", body)
        self.assertIn('HostBones.BONE_METADATA_VERSION', body)

    def test_importer_passes_skin_data_to_build_armature(self):
        self.assertIn('build_armature(base_name, bone_list, collection, model.get("SkinData"))', self.source)

    def test_writes_metadata_version_on_armature_object(self):
        targets = self._assigned_targets()
        self.assertIn(('arm_obj', 'SluggiesBoneMetadataVersion'), targets)

    def test_mode_set_object_precedes_metadata_writes(self):
        # The properties must land on Bone, which only exists once edit mode
        # is left (mode_set(mode='OBJECT')), not on EditBone.
        mode_set_line = None
        geo_id_line = None
        for node in ast.walk(self.build_armature):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == 'mode_set':
                    for kw in node.keywords:
                        if kw.arg == 'mode' and isinstance(kw.value, ast.Constant) and kw.value.value == 'OBJECT':
                            mode_set_line = node.lineno
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                            and t.value.id == 'b' and isinstance(t.slice, ast.Constant)
                            and t.slice.value == 'SluggiesGeoIdRaw'):
                        geo_id_line = node.lineno
        self.assertIsNotNone(mode_set_line)
        self.assertIsNotNone(geo_id_line)
        self.assertLess(mode_set_line, geo_id_line)


if __name__ == '__main__':
    unittest.main()
