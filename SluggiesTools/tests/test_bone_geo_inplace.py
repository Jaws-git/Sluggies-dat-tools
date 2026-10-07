"""Keep offset to bone reassignments patch in place through `GeoIdEdited`
(PLAN_EditRigidMeshes.md Phase 7 step 3).

The chain under test: the exporter's retarget rule (`HostBones.
compute_rigid_retargets`) writes `GeoIdEdited` on the old and new owner,
`bone_geo_inplace.bone_geo_patches` turns that into two u16 word writes at
the bones' `GeoIdFieldOffset`, and applying them to the synthetic donor's
block changes exactly those two words. `--unpatch` plans the reverse.
"""

import pathlib
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
for import_path in (
    ROOT / 'SluggiesTools' / 'InplacePatcher',
    ROOT / 'SluggiesTools' / 'Hammerspace',
    ROOT / 'SluggiesTools',
    ROOT / 'BlenderAddonSrc',
    ROOT / 'SluggiesTools' / 'tests',
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import bone_geo_inplace as _bone_geo
import HostBones
import synthetic_donor as donor


def _abort_collector(messages):
    def abort(message):
        messages.append(message)
        raise AssertionError(message)
    return abort


def _apply(dat: bytes, patches) -> bytes:
    out = bytearray(dat)
    for _bone_id, offset, raw in patches:
        out[offset:offset + len(raw)] = raw
    return bytes(out)


def _cap_like_hierarchy():
    """Mario's cap in E1 terms: bone 54 owns submesh 1, bone 49 is free."""
    return [
        {'BoneId': 53, 'GeoIdRaw': 0xFFFF, 'Skinned': True, 'GeoIdFieldOffset': '0x4ac6c64'},
        {'BoneId': 54, 'GeoIdRaw': 1, 'Skinned': False, 'GeoIdFieldOffset': '0x4ac6c7c'},
        {'BoneId': 49, 'GeoIdRaw': 0xFFFF, 'Skinned': True, 'GeoIdFieldOffset': '0x4ac6c1c'},
    ]


class BoneGeoPlannerTests(unittest.TestCase):
    def test_keep_offset_retarget_plans_two_words(self):
        bones = _cap_like_hierarchy()
        retargets, issues = HostBones.compute_rigid_retargets(
            {1: 54}, {53: HostBones.GEO_ID_FREE, 54: 1, 49: HostBones.GEO_ID_FREE},
            {1: 49}, {53, 54, 49})
        self.assertEqual(issues, [])
        self.assertEqual([(r.submesh_index, r.from_bone_id, r.to_bone_id) for r in retargets],
                         [(1, 54, 49)])
        by_id = {b['BoneId']: b for b in bones}
        for r in retargets:
            by_id[r.from_bone_id]['GeoIdEdited'] = HostBones.GEO_ID_FREE
            by_id[r.to_bone_id]['GeoIdEdited'] = r.submesh_index

        patches = _bone_geo.bone_geo_patches(bones, False, _abort_collector([]))
        self.assertEqual(patches, [
            (54, 0x4ac6c7c, struct.pack('>H', 0xFFFF)),
            (49, 0x4ac6c1c, struct.pack('>H', 1)),
        ])

    def test_unpatch_restores_donor_words_for_edited_bones_only(self):
        # Regression: the in-place --unpatch used to compare the donor word
        # with itself and so never restored the ACT words.
        bones = _cap_like_hierarchy()
        bones[1]['GeoIdEdited'] = 0xFFFF
        bones[2]['GeoIdEdited'] = 1
        patches = _bone_geo.bone_geo_patches(bones, True, _abort_collector([]))
        self.assertEqual(patches, [
            (54, 0x4ac6c7c, struct.pack('>H', 1)),
            (49, 0x4ac6c1c, struct.pack('>H', 0xFFFF)),
        ])

    def test_unpatch_ignores_a_missing_offset(self):
        bones = _cap_like_hierarchy()
        bones[2]['GeoIdEdited'] = 1
        bones[2]['GeoIdFieldOffset'] = None
        self.assertEqual(_bone_geo.bone_geo_patches(bones, True, _abort_collector([])), [])

    def test_edited_word_equal_to_donor_is_not_written(self):
        bones = _cap_like_hierarchy()
        bones[1]['GeoIdEdited'] = 1
        self.assertEqual(_bone_geo.bone_geo_patches(bones, False, _abort_collector([])), [])

    def test_missing_field_offset_aborts_on_patch(self):
        bones = _cap_like_hierarchy()
        bones[2]['GeoIdEdited'] = 1
        bones[2]['GeoIdFieldOffset'] = None
        messages = []
        with self.assertRaises(AssertionError):
            _bone_geo.bone_geo_patches(bones, False, _abort_collector(messages))
        self.assertIn('GeoIdFieldOffset', messages[0])
        self.assertIn('49', messages[0])

    def test_out_of_range_value_aborts(self):
        bones = _cap_like_hierarchy()
        bones[2]['GeoIdEdited'] = 0x10000
        messages = []
        with self.assertRaises(AssertionError):
            _bone_geo.bone_geo_patches(bones, False, _abort_collector(messages))
        self.assertIn('out of range', messages[0])


class SyntheticDonorInPlaceTests(unittest.TestCase):
    """Apply the planned words to the synthetic donor block and check the ACT
    records: only the two GeoId words change, and the reverse plan built from
    the donor words restores the block."""

    def setUp(self):
        self.data, self.dat, _length = donor.build_donor()
        self.bones = self.data['SluggiesModel']['BoneHierarchy']
        self.by_id = {int(b['BoneId']): b for b in self.bones}

    def _word(self, dat: bytes, bone_id: int) -> int:
        offset = int(self.by_id[bone_id]['GeoIdFieldOffset'], 16)
        return struct.unpack_from('>H', dat, offset)[0]

    def test_donor_block_matches_hierarchy(self):
        self.assertEqual(self._word(self.dat, donor.RIGID_OWNER_BONE), donor.RIGID_OWNER_SUBMESH)
        for bone_id in donor.BONE_PARENTS:
            if bone_id != donor.RIGID_OWNER_BONE:
                self.assertEqual(self._word(self.dat, bone_id), 0xFFFF)

    def test_keep_offset_move_changes_exactly_two_words(self):
        target = 7  # a leaf with no geometry
        self.assertEqual(self._word(self.dat, target), 0xFFFF)
        self.by_id[donor.RIGID_OWNER_BONE]['GeoIdEdited'] = HostBones.GEO_ID_FREE
        self.by_id[target]['GeoIdEdited'] = donor.RIGID_OWNER_SUBMESH

        patches = _bone_geo.bone_geo_patches(self.bones, False, _abort_collector([]))
        patched = _apply(self.dat, patches)

        self.assertEqual(self._word(patched, donor.RIGID_OWNER_BONE), 0xFFFF)
        self.assertEqual(self._word(patched, target), donor.RIGID_OWNER_SUBMESH)
        differing = [i for i in range(len(self.dat)) if self.dat[i] != patched[i]]
        expected = sorted(
            int(self.by_id[b]['GeoIdFieldOffset'], 16) + k
            for b in (donor.RIGID_OWNER_BONE, target) for k in (0, 1))
        self.assertEqual(differing, expected)

        # --unpatch plans the donor words back and gives the vanilla block.
        restore = _bone_geo.bone_geo_patches(self.bones, True, _abort_collector([]))
        self.assertEqual(sorted(b for b, _o, _r in restore), sorted((donor.RIGID_OWNER_BONE, target)))
        self.assertEqual(_apply(patched, restore), self.dat)


if __name__ == '__main__':
    unittest.main()
