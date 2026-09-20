"""PLAN_AddBones.md Phase 0, probe P1 - ACT rebuild identity (synthetic half).

Round-trip checks for ``act_rebuild``'s parse/rebuild/append pair, built from
ACT bytes this module synthesizes itself.

The matching sweep over every real donor in ``1_Input/dt_na.dat`` deliberately
does **not** live here -- the game assets are gitignored, so a test that reads
them can only ever skip in CI while silently rotting locally. It is a manual
probe instead: ``SluggiesTools/probe_act_rebuild_identity.py``. Run it after
touching ``act_rebuild.py``.
"""
from __future__ import annotations

import os
import struct
import sys
import unittest

TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
HAMMERSPACE_DIR = os.path.join(TOOLS_DIR, 'Hammerspace')
for _path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import act_rebuild  # noqa: E402


class ACTRebuildSyntheticTests(unittest.TestCase):
    """Round-trip checks that don't need the (gitignored) game assets."""

    def _build_minimal_act(self, bone_count=2, with_user_data=True, geo_name=b'test.gpl'):
        # Two bones: root (id 0, no SRT, is root) and a child (id 1, has SRT),
        # parented via act_rebuild's table_off(id) = HEADER_SIZE + id*BONE_RECORD_SIZE
        # convention (bone 1's table offset is 0x20 + 1*0x1C = 0x3C).
        bones = [
            act_rebuild.BoneRecord(
                orientation_ptr=0, prev=0, next=0, parent=0, first_child=0x3C,
                geo_file_id_raw=0xFFFF, id=0, inheritance=0, priority=0, pad_half=0,
            ),
        ]
        srt_blobs = []
        if bone_count > 1:
            bones.append(act_rebuild.BoneRecord(
                orientation_ptr=1, prev=0, next=0, parent=0x20, first_child=0,
                geo_file_id_raw=0xFFFF, id=1, inheritance=1, priority=0, pad_half=0,
            ))
            srt_blobs.append(b'\x00' * 0x34)
        bones = bones[:bone_count]

        user_data = []
        if with_user_data:
            track_payload = struct.pack(f'>{bone_count}H', *([0xFFFF] * bone_count))
            if len(track_payload) % 4:
                track_payload += b'\x00' * (4 - len(track_payload) % 4)
            user_data.append(act_rebuild.UserDataDescriptor(kind=3, count=0, data_ptr=0x0C, payload=track_payload))
            mirror_payload = bytes(
                byte for bone_id in range(bone_count) for byte in (bone_id, 3)
            )
            if len(mirror_payload) % 4:
                mirror_payload += b'\x00' * (4 - len(mirror_payload) % 4)
            user_data.append(act_rebuild.UserDataDescriptor(kind=2, count=0, data_ptr=0x0C, payload=mirror_payload))

        name_gap = geo_name + b'\x00'
        total_length = (
            act_rebuild.HEADER_SIZE
            + bone_count * act_rebuild.BONE_RECORD_SIZE
            + len(srt_blobs) * act_rebuild.SRT_RECORD_SIZE
            + len(name_gap)
            + sum(act_rebuild.DESCRIPTOR_HEADER_SIZE + len(d.payload) for d in user_data)
        )
        parsed = act_rebuild.ACTParsed(
            version_num=0x7B7960, actor_id=0, bone_count=bone_count,
            tree_unknown=0, root_ptr=0x20, skin_file_id=0, pad16=0,
            bones=bones, srt_blobs=srt_blobs, name_gap=name_gap, tail_gap=b'',
            user_data=user_data, total_length=total_length,
        )
        return act_rebuild.rebuild_act_bytes(parsed)

    def test_round_trip_with_user_data(self):
        built = self._build_minimal_act(with_user_data=True)
        parsed = act_rebuild.parse_act(built)
        act_rebuild.validate_mirror_table(parsed)
        rebuilt = act_rebuild.rebuild_act_bytes(parsed)
        self.assertEqual(built, rebuilt)

    def test_round_trip_without_user_data(self):
        built = self._build_minimal_act(with_user_data=False)
        parsed = act_rebuild.parse_act(built)
        rebuilt = act_rebuild.rebuild_act_bytes(parsed)
        self.assertEqual(built, rebuilt)
        self.assertEqual(parsed.user_data, [])

    def test_broken_mirror_table_is_refused(self):
        built = self._build_minimal_act(bone_count=2, with_user_data=True)
        parsed = act_rebuild.parse_act(built)
        # Corrupt bone 0's mirror entry to point at bone 1, but leave bone 1
        # pointing at itself -- not an involution.
        mirror = next(d for d in parsed.user_data if d.kind == act_rebuild.KIND_MIRROR)
        corrupted_payload = bytearray(mirror.payload)
        corrupted_payload[0] = 1
        mirror.payload = bytes(corrupted_payload)
        with self.assertRaises(act_rebuild.ACTMirrorTableError):
            act_rebuild.validate_mirror_table(parsed)

    def test_truncated_bone_table_is_rejected(self):
        with self.assertRaises(act_rebuild.ACTParseError):
            act_rebuild.parse_act(b'\x00' * 0x24)

    def test_append_leaf_bone_extends_parent_child_chain_and_user_data(self):
        built = self._build_minimal_act(bone_count=2, with_user_data=True)
        parsed = act_rebuild.parse_act(built)
        srt_blob = struct.pack('>4x3f4f3f8x', 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.3, 0.0, 0.0)
        appended = act_rebuild.append_leaf_bone(parsed, parent_id=1, srt_blob=srt_blob)

        self.assertEqual(appended.bone_count, 3)
        new_bone = next(b for b in appended.bones if b.id == 2)
        self.assertEqual(new_bone.parent, 0x3C)  # table_off(1)
        self.assertEqual(new_bone.first_child, 0)
        self.assertEqual(new_bone.geo_file_id_raw, 0xFFFF)
        parent = next(b for b in appended.bones if b.id == 1)
        self.assertEqual(parent.first_child, 0x58)  # table_off(2)

        rebuilt_bytes = act_rebuild.rebuild_act_bytes(appended)
        reparsed = act_rebuild.parse_act(rebuilt_bytes)
        act_rebuild.validate_mirror_table(reparsed)  # must still be a clean involution
        self.assertEqual(reparsed.bone_count, 3)

        track = next(d for d in reparsed.user_data if d.kind == act_rebuild.KIND_TRACK)
        self.assertEqual(struct.unpack_from('>H', track.payload, 2 * 2)[0], 0xFFFF)
        mirror = next(d for d in reparsed.user_data if d.kind == act_rebuild.KIND_MIRROR)
        self.assertEqual((mirror.payload[4], mirror.payload[5]), (2, 3))

    def test_append_leaf_bone_on_donor_without_user_data(self):
        """F4: ``userDataSize = 0`` is a normal shipped state (dozens of models,
        e.g. chunk 136's obstacles), so appending needs no per-bone array to
        extend and the new bone is simply trackless with no mirror entry."""
        built = self._build_minimal_act(bone_count=2, with_user_data=False)
        parsed = act_rebuild.parse_act(built)
        self.assertEqual(parsed.user_data, [])

        srt_blob = struct.pack('>4x3f4f3f8x', 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.3, 0.0, 0.0)
        appended = act_rebuild.append_leaf_bone(
            parsed, parent_id=1, srt_blob=srt_blob, geo_file_id_raw=3,
            # These have nowhere to go without the tables and must be ignored,
            # not written somewhere or raised over.
            track_id=0xFFFF, mirror_bone_id=2, mirror_role=3,
        )
        self.assertEqual(appended.bone_count, 3)
        self.assertEqual(appended.user_data, [])

        rebuilt_bytes = act_rebuild.rebuild_act_bytes(appended)
        reparsed = act_rebuild.parse_act(rebuilt_bytes)
        self.assertEqual(reparsed.bone_count, 3)
        self.assertEqual(reparsed.user_data, [])
        # userDataSize/userDataPtr must both stay 0, not point past the name.
        self.assertEqual(struct.unpack_from('>II', rebuilt_bytes, 0x18), (0, 0))
        # The geo name (and its filler) survive verbatim after the shifted ptr.
        self.assertEqual(reparsed.name_gap, parsed.name_gap)
        self.assertEqual(
            struct.unpack_from('>I', rebuilt_bytes, 0x10)[0],
            act_rebuild.HEADER_SIZE + 3 * act_rebuild.BONE_RECORD_SIZE
            + 2 * act_rebuild.SRT_RECORD_SIZE,
        )
        new_bone = next(b for b in reparsed.bones if b.id == 2)
        self.assertEqual(new_bone.parent, 0x3C)  # table_off(1)
        self.assertEqual(new_bone.geo_file_id_raw, 3)
        parent = next(b for b in reparsed.bones if b.id == 1)
        self.assertEqual(parent.first_child, 0x58)  # table_off(2)

    def test_append_leaf_bone_passes_through_kind4_without_per_bone_tables(self):
        """F5's kind-4 blob is bone-count-independent, so a donor carrying only
        that (no kind-3/kind-2) is still the F4 no-tables case."""
        built = self._build_minimal_act(bone_count=2, with_user_data=False)
        parsed = act_rebuild.parse_act(built)
        kind4 = act_rebuild.UserDataDescriptor(
            kind=4, count=1, data_ptr=0x0C, payload=struct.pack('>HH', 7, 2),
        )
        parsed.user_data = [kind4]
        parsed.total_length += act_rebuild.DESCRIPTOR_HEADER_SIZE + len(kind4.payload)

        appended = act_rebuild.append_leaf_bone(parsed, parent_id=1, srt_blob=b'\x00' * 0x34)
        self.assertEqual([d.kind for d in appended.user_data], [4])
        self.assertEqual(appended.user_data[0].payload, kind4.payload)
        reparsed = act_rebuild.parse_act(act_rebuild.rebuild_act_bytes(appended))
        self.assertEqual(reparsed.bone_count, 3)
        self.assertEqual([(d.kind, d.payload) for d in reparsed.user_data],
                         [(4, kind4.payload)])

    def test_append_leaf_bone_rejects_track_without_mirror(self):
        """One of the two per-bone tables without the other is ambiguous (which
        array does the new entry belong to?), so it stays refused."""
        built = self._build_minimal_act(bone_count=2, with_user_data=True)
        parsed = act_rebuild.parse_act(built)
        parsed.user_data = [d for d in parsed.user_data if d.kind == act_rebuild.KIND_TRACK]
        with self.assertRaises(ValueError) as caught:
            act_rebuild.append_leaf_bone(parsed, parent_id=1, srt_blob=b'\x00' * 0x34)
        self.assertIn('kind-3', str(caught.exception))

    def test_append_leaf_bone_rejects_unknown_parent(self):
        built = self._build_minimal_act(bone_count=2, with_user_data=True)
        parsed = act_rebuild.parse_act(built)
        with self.assertRaises(ValueError):
            act_rebuild.append_leaf_bone(parsed, parent_id=99, srt_blob=b'\x00' * 0x34)


if __name__ == '__main__':
    unittest.main()
