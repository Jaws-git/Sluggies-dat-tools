"""PLAN_AddBones.md Phase 2 - the ACT rebuilder.

Exercises ``HammerspaceMain.BuildACTBoneHierarchy``'s rebuild route (a
``BoneHierarchyEdited`` with at least one ``UserAdded`` bone) end to end
against a real donor, since Phase 4's Blender exporter (the thing that would
normally produce such a ``.sluggie``) doesn't exist yet. ``BoneHierarchy``
entries are read from the checked-in Mario ``.sluggie``, but that file
predates Phase 1's ``MirrorBoneId``/``MirrorRole``/``ACTUserData`` export
additions, so this test derives the ground-truth mirror/track table directly
from the real donor ACT bytes via ``act_rebuild`` instead of trusting stale
JSON -- the same source of truth Phase 1's exporter itself reads from.

Skipped entirely when 1_Input/dt_na.dat or main.dol are not present (both are
gitignored game assets, not part of the repo).
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest

TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
HAMMERSPACE_DIR = os.path.join(TOOLS_DIR, 'Hammerspace')
for _path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import act_rebuild  # noqa: E402
import HammerspaceHelper as hh  # noqa: E402
import HammerspaceMain as hammerspace  # noqa: E402

_INPUT_ASSETS_PRESENT = os.path.exists(hh.INPUT_DAT) and os.path.exists(hh.INPUT_DOL)

_MARIO_SLUGGIE = os.path.normpath(os.path.join(
    TOOLS_DIR, '..', '2_Output_Models', '18 Mario', '78277664_mario.gpl',
    '78277664_mario.gpl.sluggie',
))


def _load_mario_data() -> dict:
    with open(_MARIO_SLUGGIE, 'r', encoding='utf-8') as f:
        return json.load(f)


def _build_bone_hierarchy_edited(model: dict, act_parsed: act_rebuild.ACTParsed) -> list[dict]:
    """Derive a BoneHierarchyEdited array (all UserAdded=False) from the
    model's own BoneHierarchy plus the real donor ACT's decoded mirror/track
    table (act_rebuild ground truth, since this .sluggie predates Phase 1's
    own MirrorBoneId/MirrorRole export)."""
    mirror_desc = next(d for d in act_parsed.user_data if d.kind == act_rebuild.KIND_MIRROR)
    track_desc = next(d for d in act_parsed.user_data if d.kind == act_rebuild.KIND_TRACK)

    edited = []
    for bone in model['BoneHierarchy']:
        bone_id = int(bone['BoneId'])
        mirror_bone_id = mirror_desc.payload[2 * bone_id]
        mirror_role = mirror_desc.payload[2 * bone_id + 1]
        track_id, = __import__('struct').unpack_from('>H', track_desc.payload, 2 * bone_id)
        edited.append({
            'BoneId': bone_id,
            'GeoId': bone['GeoId'],
            'ParentBoneId': bone['ParentBoneId'],
            'Skinned': bone['Skinned'],
            'TrackId': track_id,
            'MirrorBoneId': mirror_bone_id,
            'MirrorRole': mirror_role,
            'SRTType': bone['SRTType'],
            'DrawPriority': bone['DrawPriority'],
            'InheritTransform': bone['InheritTransform'],
            'UserAdded': False,
            'Translation': bone['Translation'],
            'Scale': bone['Scale'],
            'Quaternion': bone['Quaternion'],
            'VertexInfluences': bone['VertexInfluences'],
        })
    return edited


@unittest.skipUnless(_INPUT_ASSETS_PRESENT, "requires 1_Input/dt_na.dat and main.dol")
@unittest.skipUnless(os.path.exists(_MARIO_SLUGGIE), "requires the checked-in Mario .sluggie fixture")
class ACTPhase2RebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = _load_mario_data()
        model = cls.data['SluggiesModel']
        cls.model_offset = hammerspace._hex(model['ModelOffset'])
        cls.model_length = model['ModelLength']
        cls.donor_act_bytes = hammerspace.CloneACT(cls.model_offset, cls.model_length)
        cls.donor_parsed = act_rebuild.parse_act(cls.donor_act_bytes)
        act_rebuild.validate_mirror_table(cls.donor_parsed)
        cls.donor_bone_count = cls.donor_parsed.bone_count

    def _fixture_data(self) -> dict:
        data = copy.deepcopy(self.data)
        model = data['SluggiesModel']
        model['BoneHierarchyEdited'] = _build_bone_hierarchy_edited(model, self.donor_parsed)
        return data

    def test_no_new_bones_is_byte_identical_to_clone_route(self):
        """BoneHierarchyEdited with every entry UserAdded=False (nothing to
        append) should rebuild to the exact same bytes CloneACT would
        produce -- the append_leaf_bone loop is simply never entered."""
        data = self._fixture_data()
        rebuilt = hammerspace.BuildACTBoneHierarchy(data, self.model_offset, self.model_length)
        self.assertEqual(rebuilt, self.donor_act_bytes)

    def test_single_new_leaf_bone_appended_to_spine(self):
        data = self._fixture_data()
        model = data['SluggiesModel']
        new_id = self.donor_bone_count
        model['BoneHierarchyEdited'].append({
            'BoneId': new_id,
            'GeoId': 0xFFFF,
            'ParentBoneId': 3,
            'Skinned': False,
            'TrackId': 0xFFFF,
            'MirrorBoneId': new_id,
            'MirrorRole': 3,
            'SRTType': 0xC,
            'DrawPriority': 0,
            'InheritTransform': True,
            'UserAdded': True,
            'Translation': [0.0, 0.0, 0.3],
            'Scale': [1.0, 1.0, 1.0],
            'Quaternion': [1.0, 0.0, 0.0, 0.0],
            'VertexInfluences': [],
        })

        rebuilt_bytes = hammerspace.BuildACTBoneHierarchy(data, self.model_offset, self.model_length)
        rebuilt = act_rebuild.parse_act(rebuilt_bytes)
        act_rebuild.validate_mirror_table(rebuilt)

        self.assertEqual(rebuilt.bone_count, self.donor_bone_count + 1)
        new_bone = next(b for b in rebuilt.bones if b.id == new_id)
        self.assertEqual(new_bone.parent, act_rebuild.HEADER_SIZE + 3 * act_rebuild.BONE_RECORD_SIZE)
        self.assertEqual(new_bone.geo_file_id_raw, 0xFFFF)  # unowned, per the user contract default

        mirror_desc = next(d for d in rebuilt.user_data if d.kind == act_rebuild.KIND_MIRROR)
        self.assertEqual((mirror_desc.payload[2 * new_id], mirror_desc.payload[2 * new_id + 1]), (new_id, 3))
        track_desc = next(d for d in rebuilt.user_data if d.kind == act_rebuild.KIND_TRACK)
        import struct
        self.assertEqual(struct.unpack_from('>H', track_desc.payload, 2 * new_id)[0], 0xFFFF)

        # Every donor bone's table record is untouched, except spine (bone 3)'s
        # previously-last child, whose `next` now correctly points at the new
        # bone appended to the tail of its child chain (F10).
        spine_old_last_child_id = None
        cur = next(b for b in self.donor_parsed.bones if b.id == 3).first_child
        while cur != 0:
            cur_id = (cur - act_rebuild.HEADER_SIZE) // act_rebuild.BONE_RECORD_SIZE
            spine_old_last_child_id = cur_id
            cur = next(b for b in self.donor_parsed.bones if b.id == cur_id).next

        for donor_bone in self.donor_parsed.bones:
            rebuilt_bone = next(b for b in rebuilt.bones if b.id == donor_bone.id)
            self.assertEqual(rebuilt_bone.prev, donor_bone.prev)
            if donor_bone.id == spine_old_last_child_id:
                self.assertEqual(
                    rebuilt_bone.next,
                    act_rebuild.HEADER_SIZE + new_id * act_rebuild.BONE_RECORD_SIZE,
                )
            else:
                self.assertEqual(rebuilt_bone.next, donor_bone.next)
            self.assertEqual(rebuilt_bone.parent, donor_bone.parent)
            self.assertEqual(rebuilt_bone.first_child, donor_bone.first_child)
            self.assertEqual(rebuilt_bone.geo_file_id_raw, donor_bone.geo_file_id_raw)

    def test_bulk_bones_appended_to_a_parent_with_existing_children(self):
        """Two new bones parented to the same donor bone exercise the
        'append to the end of an already-nonempty child chain' path, and a
        third parented to the first new bone exercises new-bone-parented-to-
        new-bone -- both PLAN_AddBones.md Phase 2's tree-link emitter must
        get right, not just a single leaf on a donor parent."""
        data = self._fixture_data()
        model = data['SluggiesModel']
        n0 = self.donor_bone_count
        base_bone = {
            'GeoId': 0xFFFF, 'Skinned': False, 'TrackId': 0xFFFF,
            'SRTType': 0xC, 'DrawPriority': 0, 'InheritTransform': True,
            'UserAdded': True, 'Translation': [0.0, 0.0, 0.3],
            'Scale': [1.0, 1.0, 1.0], 'Quaternion': [1.0, 0.0, 0.0, 0.0],
            'VertexInfluences': [],
        }
        model['BoneHierarchyEdited'] += [
            {**base_bone, 'BoneId': n0, 'ParentBoneId': 3, 'MirrorBoneId': n0, 'MirrorRole': 3},
            {**base_bone, 'BoneId': n0 + 1, 'ParentBoneId': 3, 'MirrorBoneId': n0 + 1, 'MirrorRole': 3},
            {**base_bone, 'BoneId': n0 + 2, 'ParentBoneId': n0, 'MirrorBoneId': n0 + 2, 'MirrorRole': 3},
        ]

        rebuilt_bytes = hammerspace.BuildACTBoneHierarchy(data, self.model_offset, self.model_length)
        rebuilt = act_rebuild.parse_act(rebuilt_bytes)
        act_rebuild.validate_mirror_table(rebuilt)

        self.assertEqual(rebuilt.bone_count, self.donor_bone_count + 3)
        by_id = {b.id: b for b in rebuilt.bones}

        def table_off(bone_id):
            return act_rebuild.HEADER_SIZE + bone_id * act_rebuild.BONE_RECORD_SIZE

        spine = by_id[3]
        # Walk the spine's child chain to its tail; the two new siblings must
        # be the last two entries, in id order, appended after every donor child.
        chain = []
        cur = spine.first_child
        while cur != 0:
            cur_id = (cur - act_rebuild.HEADER_SIZE) // act_rebuild.BONE_RECORD_SIZE
            chain.append(cur_id)
            cur = by_id[cur_id].next
        self.assertEqual(chain[-2:], [n0, n0 + 1])

        # n0+2 is n0's only child.
        self.assertEqual(by_id[n0].first_child, table_off(n0 + 2))
        self.assertEqual(by_id[n0 + 2].parent, table_off(n0))
        self.assertEqual(by_id[n0 + 2].prev, 0)
        self.assertEqual(by_id[n0 + 2].next, 0)


if __name__ == '__main__':
    unittest.main()
