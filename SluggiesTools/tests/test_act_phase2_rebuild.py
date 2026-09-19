"""PLAN_AddBones.md Phase 2 - the ACT rebuilder.

Exercises ``HammerspaceMain.BuildACTBoneHierarchy``'s rebuild route (a
``BoneHierarchyEdited`` with at least one ``UserAdded`` bone) end to end,
since Phase 4's Blender exporter (the thing that would normally produce such
a ``.sluggie``) doesn't exist yet.

The donor ACT is **synthesized here** by ``act_rebuild.rebuild_act_bytes``,
not cloned out of ``1_Input/dt_na.dat``: the game assets and the
``2_Output_Models`` exports are both gitignored, so a test that reads them
skips in CI and rots locally the moment a working export changes (it did --
an unrelated Blender export left a ``CustomSubmeshes`` entry on the Mario
``.sluggie`` this module used to read, which patched its host bone's GeoId
and broke the byte-identity assertion). The full-corpus identity sweep over
the real donors lives in ``SluggiesTools/probe_act_rebuild_identity.py``
instead, as a probe you run by hand.

The synthetic donor is shaped like a real player skeleton: a root with two
child chains, a spine with three children (so appends land at the end of an
already-nonempty chain), a left/right mirror pair at two depths, one rigid
mesh owner, and a bone with no SRT record at all.
"""
from __future__ import annotations

import copy
import struct
import os
import sys
import unittest
from unittest import mock

TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
HAMMERSPACE_DIR = os.path.join(TOOLS_DIR, 'Hammerspace')
for _path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import act_rebuild  # noqa: E402
import HammerspaceMain as hammerspace  # noqa: E402

#: ``BoneId -> ParentBoneId`` for the synthetic donor skeleton.
DONOR_PARENTS = {0: None, 1: 0, 2: 1, 3: 1, 4: 1, 5: 3, 6: 4, 7: 2, 8: 0}
#: Bone 7 is the one rigid mesh owner (a cap on the head), like a real donor.
DONOR_GEO_OWNERS = {7: 0}
#: Bone 8 ships without an SRT record (``orientationPTR`` 0), which real
#: donors do too -- the rebuilder must carry that through untouched.
DONOR_BONES_WITHOUT_SRT = {8}
#: Left/right pairs mirror each other; every other bone mirrors itself.
DONOR_MIRRORS = {0: (0, 3), 1: (1, 3), 2: (2, 3), 3: (4, 1), 4: (3, 2),
                 5: (6, 1), 6: (5, 2), 7: (7, 3), 8: (8, 3)}
DONOR_TRACK_IDS = {0: 0, 1: 4, 2: 7, 3: 11, 4: 12, 5: 21, 6: 22, 7: 0xFFFF, 8: 0xFFFF}

#: Any in-bounds values: the donor bytes are injected, never read from a DAT.
MODEL_OFFSET = 0x1000
MODEL_LENGTH = 0x8000


def _table_off(bone_id: int) -> int:
    return act_rebuild.HEADER_SIZE + bone_id * act_rebuild.BONE_RECORD_SIZE


def _align4(payload: bytes) -> bytes:
    return payload + b'\x00' * (-len(payload) % 4)


def _build_donor_act_bytes() -> bytes:
    """Serialize the skeleton above into donor ACT bytes, deriving every tree
    link (prev/next/parent/firstChild) from ``DONOR_PARENTS``."""
    bone_ids = sorted(DONOR_PARENTS)
    children = {bone_id: [] for bone_id in bone_ids}
    for bone_id in bone_ids:
        parent_id = DONOR_PARENTS[bone_id]
        if parent_id is not None:
            children[parent_id].append(bone_id)

    bones = []
    srt_blobs = []
    for bone_id in bone_ids:
        parent_id = DONOR_PARENTS[bone_id]
        siblings = children[parent_id] if parent_id is not None else [bone_id]
        position = siblings.index(bone_id)
        has_srt = bone_id not in DONOR_BONES_WITHOUT_SRT
        bones.append(act_rebuild.BoneRecord(
            orientation_ptr=1 if has_srt else 0,  # rebuild_act_bytes recomputes the real pointer
            prev=_table_off(siblings[position - 1]) if position else 0,
            next=_table_off(siblings[position + 1]) if position + 1 < len(siblings) else 0,
            parent=_table_off(parent_id) if parent_id is not None else 0,
            first_child=_table_off(children[bone_id][0]) if children[bone_id] else 0,
            geo_file_id_raw=DONOR_GEO_OWNERS.get(bone_id, 0xFFFF),
            id=bone_id, inheritance=1, priority=0, pad_half=0,
        ))
        if has_srt:
            srt_blobs.append(act_rebuild.pack_srt_blob(
                0xC, [1.0, 1.0, 1.0], [1.0, 0.0, 0.0, 0.0], [0.0, 0.1 * bone_id, 0.0],
            ))

    bone_count = len(bone_ids)
    track_payload = _align4(struct.pack(
        f'>{bone_count}H', *(DONOR_TRACK_IDS[b] for b in bone_ids)))
    mirror_payload = _align4(bytes(
        byte for bone_id in bone_ids for byte in DONOR_MIRRORS[bone_id]))
    user_data = [
        act_rebuild.UserDataDescriptor(
            kind=act_rebuild.KIND_TRACK, count=0, data_ptr=0x0C, payload=track_payload),
        act_rebuild.UserDataDescriptor(
            kind=act_rebuild.KIND_MIRROR, count=0, data_ptr=0x0C, payload=mirror_payload),
    ]

    name_gap = b'synthetic.gpl\x00\x00\x00'
    total_length = (
        act_rebuild.HEADER_SIZE
        + bone_count * act_rebuild.BONE_RECORD_SIZE
        + len(srt_blobs) * act_rebuild.SRT_RECORD_SIZE
        + len(name_gap)
        + sum(act_rebuild.DESCRIPTOR_HEADER_SIZE + len(d.payload) for d in user_data)
    )
    parsed = act_rebuild.ACTParsed(
        version_num=0x7B7960, actor_id=0, bone_count=bone_count,
        tree_unknown=0, root_ptr=act_rebuild.HEADER_SIZE, skin_file_id=0, pad16=0,
        bones=bones, srt_blobs=srt_blobs, name_gap=name_gap, tail_gap=b'',
        user_data=user_data, total_length=total_length,
    )
    return act_rebuild.rebuild_act_bytes(parsed)


def _bone_hierarchy_edited(parsed: act_rebuild.ACTParsed) -> list[dict]:
    """A ``BoneHierarchyEdited`` array (all ``UserAdded=False``) describing the
    donor exactly, as Phase 1's exporter writes it: mirror/track values come
    from the donor's own decoded user-data tables."""
    mirror_desc = next(d for d in parsed.user_data if d.kind == act_rebuild.KIND_MIRROR)
    track_desc = next(d for d in parsed.user_data if d.kind == act_rebuild.KIND_TRACK)
    edited = []
    for bone in parsed.bones:
        track_id, = struct.unpack_from('>H', track_desc.payload, 2 * bone.id)
        edited.append({
            'BoneId': bone.id,
            'GeoId': bone.geo_file_id_raw,
            'ParentBoneId': DONOR_PARENTS[bone.id],
            'Skinned': False,
            'TrackId': track_id,
            'MirrorBoneId': mirror_desc.payload[2 * bone.id],
            'MirrorRole': mirror_desc.payload[2 * bone.id + 1],
            'SRTType': 0xC,
            'DrawPriority': 0,
            'InheritTransform': True,
            'UserAdded': False,
            'Translation': [0.0, 0.1 * bone.id, 0.0],
            'Scale': [1.0, 1.0, 1.0],
            'Quaternion': [1.0, 0.0, 0.0, 0.0],
            'VertexInfluences': [],
        })
    return edited


class ACTPhase2RebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.donor_act_bytes = _build_donor_act_bytes()
        cls.donor_parsed = act_rebuild.parse_act(cls.donor_act_bytes)
        act_rebuild.validate_mirror_table(cls.donor_parsed)
        cls.donor_bone_count = cls.donor_parsed.bone_count
        cls.data = {'SluggiesModel': {
            'BoneHierarchyEdited': _bone_hierarchy_edited(cls.donor_parsed),
        }}

    def _fixture_data(self) -> dict:
        return copy.deepcopy(self.data)

    def _build(self, data: dict) -> bytes:
        """Run the real rebuilder with the donor ACT injected in place of the
        ``CloneACT`` read of ``1_Input/dt_na.dat``."""
        with mock.patch.object(hammerspace, 'CloneACT', return_value=self.donor_act_bytes):
            return hammerspace.BuildACTBoneHierarchy(data, MODEL_OFFSET, MODEL_LENGTH)

    def test_no_new_bones_is_byte_identical_to_clone_route(self):
        """BoneHierarchyEdited with every entry UserAdded=False (nothing to
        append) should rebuild to the exact same bytes CloneACT would
        produce -- the append_leaf_bone loop is simply never entered."""
        self.assertEqual(self._build(self._fixture_data()), self.donor_act_bytes)

    def test_a_bone_without_an_srt_record_survives_the_rebuild(self):
        """A donor bone with orientationPTR 0 must come back with no SRT blob
        of its own, and every other bone's blob must still line up."""
        rebuilt = act_rebuild.parse_act(self._build(self._fixture_data()))
        self.assertEqual(len(rebuilt.srt_blobs), self.donor_bone_count - len(DONOR_BONES_WITHOUT_SRT))
        for bone_id in DONOR_BONES_WITHOUT_SRT:
            self.assertEqual(next(b for b in rebuilt.bones if b.id == bone_id).orientation_ptr, 0)
        self.assertEqual(rebuilt.srt_blobs, self.donor_parsed.srt_blobs)

    def test_single_new_leaf_bone_appended_to_spine(self):
        data = self._fixture_data()
        model = data['SluggiesModel']
        new_id = self.donor_bone_count
        model['BoneHierarchyEdited'].append({
            'BoneId': new_id,
            'GeoId': 0xFFFF,
            'ParentBoneId': 1,  # the spine, which already has three children
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

        rebuilt = act_rebuild.parse_act(self._build(data))
        act_rebuild.validate_mirror_table(rebuilt)

        self.assertEqual(rebuilt.bone_count, self.donor_bone_count + 1)
        new_bone = next(b for b in rebuilt.bones if b.id == new_id)
        self.assertEqual(new_bone.parent, _table_off(1))
        self.assertEqual(new_bone.geo_file_id_raw, 0xFFFF)  # unowned, per the user contract default

        mirror_desc = next(d for d in rebuilt.user_data if d.kind == act_rebuild.KIND_MIRROR)
        self.assertEqual((mirror_desc.payload[2 * new_id], mirror_desc.payload[2 * new_id + 1]), (new_id, 3))
        track_desc = next(d for d in rebuilt.user_data if d.kind == act_rebuild.KIND_TRACK)
        self.assertEqual(struct.unpack_from('>H', track_desc.payload, 2 * new_id)[0], 0xFFFF)

        # Every donor bone's table record is untouched, except the spine's
        # previously-last child, whose `next` now correctly points at the new
        # bone appended to the tail of its child chain (F10).
        spine_old_last_child_id = None
        cur = next(b for b in self.donor_parsed.bones if b.id == 1).first_child
        while cur != 0:
            cur_id = (cur - act_rebuild.HEADER_SIZE) // act_rebuild.BONE_RECORD_SIZE
            spine_old_last_child_id = cur_id
            cur = next(b for b in self.donor_parsed.bones if b.id == cur_id).next
        self.assertEqual(spine_old_last_child_id, 4)

        for donor_bone in self.donor_parsed.bones:
            rebuilt_bone = next(b for b in rebuilt.bones if b.id == donor_bone.id)
            self.assertEqual(rebuilt_bone.prev, donor_bone.prev)
            if donor_bone.id == spine_old_last_child_id:
                self.assertEqual(rebuilt_bone.next, _table_off(new_id))
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
            {**base_bone, 'BoneId': n0, 'ParentBoneId': 1, 'MirrorBoneId': n0, 'MirrorRole': 3},
            {**base_bone, 'BoneId': n0 + 1, 'ParentBoneId': 1, 'MirrorBoneId': n0 + 1, 'MirrorRole': 3},
            {**base_bone, 'BoneId': n0 + 2, 'ParentBoneId': n0, 'MirrorBoneId': n0 + 2, 'MirrorRole': 3},
        ]

        rebuilt = act_rebuild.parse_act(self._build(data))
        act_rebuild.validate_mirror_table(rebuilt)

        self.assertEqual(rebuilt.bone_count, self.donor_bone_count + 3)
        by_id = {b.id: b for b in rebuilt.bones}

        spine = by_id[1]
        # Walk the spine's child chain to its tail; the two new siblings must
        # be the last two entries, in id order, appended after every donor child.
        chain = []
        cur = spine.first_child
        while cur != 0:
            cur_id = (cur - act_rebuild.HEADER_SIZE) // act_rebuild.BONE_RECORD_SIZE
            chain.append(cur_id)
            cur = by_id[cur_id].next
        self.assertEqual(chain, [2, 3, 4, n0, n0 + 1])

        # n0+2 is n0's only child.
        self.assertEqual(by_id[n0].first_child, _table_off(n0 + 2))
        self.assertEqual(by_id[n0 + 2].parent, _table_off(n0))
        self.assertEqual(by_id[n0 + 2].prev, 0)
        self.assertEqual(by_id[n0 + 2].next, 0)


if __name__ == '__main__':
    unittest.main()
