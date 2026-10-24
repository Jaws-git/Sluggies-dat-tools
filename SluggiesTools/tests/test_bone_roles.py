"""The Blender add-on's bone collections by original role
(BlenderAddonSrc/BoneRoles.py)."""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADDON_DIR = ROOT / 'BlenderAddonSrc'
if str(ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(ADDON_DIR))

import BoneRoles as R  # noqa: E402

FREE = R.GEO_ID_FREE
NONE = R.NO_TRACK


def roles(skinned=False, geo=FREE, track=NONE, parent=True, children=False, added=False):
    return R.bone_roles(skinned=skinned, geo_id_raw=geo, track_id=track,
                        has_parent=parent, has_children=children, user_added=added)


class BoneRoleTests(unittest.TestCase):
    # Bone ids in the comments are Mario High's (chunk 18 file 0).
    def test_skinning_bone(self):                       # 28, left hand
        self.assertEqual(roles(skinned=True, track=19, children=True), [R.SKINNING])

    def test_rigid_mesh_owner(self):                    # 55, cap
        self.assertEqual(roles(geo=2, track=70), [R.RIGID_MESH])

    def test_skinning_and_rigid_mesh_overlap(self):     # daisy_prototype fingers
        self.assertEqual(roles(skinned=True, geo=0), [R.SKINNING, R.RIGID_MESH])

    def test_tracked_joint_and_tip(self):               # 53 head; 49 hand attach point
        self.assertEqual(roles(track=10, children=True), [R.TRACKED_JOINT])
        self.assertEqual(roles(track=2), [R.TRACKED_TIP])

    def test_structural_and_end_tip(self):              # 6 pelvis fork; 48 finger tip
        self.assertEqual(roles(children=True), [R.STRUCTURAL])
        self.assertEqual(roles(), [R.END_TIP])

    def test_free_root_is_a_marker_with_or_without_a_track(self):   # 0, 86-90
        self.assertEqual(roles(parent=False, track=131), [R.MARKER])
        self.assertEqual(roles(parent=False), [R.MARKER])

    def test_root_with_children_is_not_a_marker(self):  # 1, main root
        self.assertEqual(roles(parent=False, track=0, children=True), [R.TRACKED_JOINT])

    def test_added_bone_takes_no_original_role(self):
        self.assertEqual(roles(added=True, parent=False), [R.ADDED])
        self.assertEqual(roles(added=True, skinned=True, geo=7), [R.SKINNING, R.RIGID_MESH, R.ADDED])
        self.assertEqual(roles(added=True, geo=7), [R.RIGID_MESH, R.ADDED])

    def test_every_role_is_ordered_and_only_tips_and_markers_hide(self):
        self.assertEqual(len(set(R.COLLECTION_ORDER)), 8)
        self.assertEqual(R.HIDDEN_BY_DEFAULT, {R.END_TIP, R.MARKER})


class _Collections(dict):
    def new(self, name):
        self[name] = type('Collection', (), {'is_visible': True})()
        return self[name]


class BoneCollectionTests(unittest.TestCase):
    def test_created_once_and_hidden_for_markers(self):
        arm = type('Armature', (), {'collections': _Collections()})()
        marker = R.bone_collection(arm, R.MARKER)
        self.assertFalse(marker.is_visible)
        self.assertIs(R.bone_collection(arm, R.MARKER), marker)
        self.assertTrue(R.bone_collection(arm, R.SKINNING).is_visible)


if __name__ == '__main__':
    unittest.main()
