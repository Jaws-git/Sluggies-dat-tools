"""Export of the ACT user-data facts to .sluggie (act_section.html#user-data).

Round-trip tests for the exported fields: BoneHierarchy's per-bone
MirrorBoneId/MirrorRole (decoded from the kind-2 ACT user-data entry)
and the model-level ACTUserData object (which kinds are present, plus every
kind-4 entry preserved verbatim as an opaque blob).

Builds a synthetic ACT section with ``act_rebuild`` (already exercised by
test_act_rebuild_identity.py) and feeds it through the *real* act.py parser
(``ACTLayout``) rather than a hand-rolled duplicate, so this test exercises
the same code path the export itself uses. No game assets required.

The functions under test are the document builders of ``sluggie_document.py``
(importing it has no side effects: no prompt, no command line, no logger set-up).
"""
from __future__ import annotations

import io
import json
import os
import struct
import sys
import unittest

TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import act_rebuild  # noqa: E402
import sluggie_document as export  # noqa: E402
from act import ACTLayout  # noqa: E402
from binfmt import decode_field  # noqa: E402


def _build_synthetic_act_bytes(bone_count=3, kind4_track_ids=(5, 7)):
    """Build a small ACT section: a root bone (id 0) with two children
    (ids 1, 2), a kind-3 track table, a kind-2 mirror table where bone 1
    mirrors bone 2 (and vice versa) and bone 0 self-mirrors, and a kind-4
    entry carrying ``kind4_track_ids`` (the opaque '(u16 track_id, u16=2)'
    shape)."""
    assert bone_count == 3, "this helper only builds the fixed 3-bone shape below"

    def table_off(bone_id):
        return act_rebuild.HEADER_SIZE + bone_id * act_rebuild.BONE_RECORD_SIZE

    bones = [
        act_rebuild.BoneRecord(
            orientation_ptr=0, prev=0, next=0, parent=0, first_child=table_off(1),
            geo_file_id_raw=0xFFFF, id=0, inheritance=0, priority=0, pad_half=0,
        ),
        act_rebuild.BoneRecord(
            orientation_ptr=1, prev=0, next=table_off(2), parent=table_off(0), first_child=0,
            geo_file_id_raw=0xFFFF, id=1, inheritance=1, priority=0, pad_half=0,
        ),
        act_rebuild.BoneRecord(
            orientation_ptr=1, prev=table_off(1), next=0, parent=table_off(0), first_child=0,
            geo_file_id_raw=0xFFFF, id=2, inheritance=1, priority=0, pad_half=0,
        ),
    ]
    srt_blobs = [b'\x00' * act_rebuild.SRT_RECORD_SIZE, b'\x00' * act_rebuild.SRT_RECORD_SIZE]

    track_payload = struct.pack('>3H', 0xFFFF, 0xFFFF, 0xFFFF)
    mirror_payload = bytes((0, 3,  2, 3,  1, 3))  # bone0->0, bone1->2, bone2->1
    kind4_payload = b''.join(struct.pack('>HH', tid, 2) for tid in kind4_track_ids)

    user_data = [
        act_rebuild.UserDataDescriptor(kind=3, count=0, data_ptr=0x0C, payload=track_payload),
        act_rebuild.UserDataDescriptor(kind=2, count=0, data_ptr=0x0C, payload=mirror_payload),
        act_rebuild.UserDataDescriptor(kind=4, count=len(kind4_track_ids), data_ptr=0x0C, payload=kind4_payload),
    ]

    name_gap = b'test.gpl\x00'
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


class _FakeModel:
    """Duck-types the subset of model0.Model that export.py's ACT/bone
    extractors touch: .ACT, .f, .bones, .GPL, .SKN."""
    def __init__(self, f, act):
        self.f = f
        self.ACT = act
        self.bones = act.bones()
        self.GPL = None
        self.SKN = None


def _make_model(act_bytes):
    # act.py's ACTLayout.analyze() unconditionally probes for a kind-3 track
    # table at userDataPtr+0x2/+0xC even when userDataSize is 0 (real donors
    # always ship large enough ACT sections that this garbage-range read
    # lands safely; the tiny sections built here don't), so pad well past
    # any offset that probe could reach.
    f = io.BytesIO(act_bytes + b'\x00' * 0x10000)
    act = ACTLayout(f, 0, len(act_bytes), "ACT")
    act.analyze()
    return _FakeModel(f, act)


class ExportACTUserDataTests(unittest.TestCase):
    def test_mirror_table_decoded_onto_bone_hierarchy(self):
        model = _make_model(_build_synthetic_act_bytes())
        bones = export.extract_bone_data(model)
        by_id = {b['BoneId']: b for b in bones}

        self.assertEqual((by_id[0]['MirrorBoneId'], by_id[0]['MirrorRole']), (0, 3))
        self.assertEqual((by_id[1]['MirrorBoneId'], by_id[1]['MirrorRole']), (2, 3))
        self.assertEqual((by_id[2]['MirrorBoneId'], by_id[2]['MirrorRole']), (1, 3))

    def test_mirror_table_absent_exports_null(self):
        # Build a donor with no user data at all (a legal, shipped state).
        bones = [
            act_rebuild.BoneRecord(
                orientation_ptr=0, prev=0, next=0, parent=0, first_child=0,
                geo_file_id_raw=0xFFFF, id=0, inheritance=0, priority=0, pad_half=0,
            ),
        ]
        name_gap = b'test.gpl\x00'
        total_length = act_rebuild.HEADER_SIZE + act_rebuild.BONE_RECORD_SIZE + len(name_gap)
        parsed = act_rebuild.ACTParsed(
            version_num=0x7B7960, actor_id=0, bone_count=1,
            tree_unknown=0, root_ptr=act_rebuild.HEADER_SIZE, skin_file_id=0, pad16=0,
            bones=bones, srt_blobs=[], name_gap=name_gap, tail_gap=b'',
            user_data=[], total_length=total_length,
        )
        act_bytes = act_rebuild.rebuild_act_bytes(parsed)
        model = _make_model(act_bytes)

        bone_list = export.extract_bone_data(model)
        self.assertIsNone(bone_list[0]['MirrorBoneId'])
        self.assertIsNone(bone_list[0]['MirrorRole'])
        self.assertIsNone(export.extract_act_user_data(model))
        # No user data, no track table: the bone has no track. (The reader used to take the ACT header,
        # at "user data pointer" 0, for the table: 0x79 here, the low half of the version word.)
        self.assertEqual(bone_list[0]['TrackId'], 0xFFFF)

    def test_act_user_data_present_kinds_and_kind4_blob_round_trip(self):
        act_bytes = _build_synthetic_act_bytes(kind4_track_ids=(5, 7, 42))
        model = _make_model(act_bytes)

        act_user_data = export.extract_act_user_data(model)
        self.assertEqual(act_user_data['PresentKinds'], [3, 2, 4])
        self.assertEqual(len(act_user_data['Kind4Entries']), 1)
        entry = act_user_data['Kind4Entries'][0]
        self.assertEqual(entry['Count'], 3)
        self.assertEqual(entry['DataPtr'], 0x0C)
        payload = decode_field(entry["Payload"])
        self.assertEqual(payload, b''.join(struct.pack('>HH', tid, 2) for tid in (5, 7, 42)))

        # Full round trip through JSON, as the .sluggie file itself does.
        reloaded = json.loads(json.dumps(act_user_data))
        self.assertEqual(reloaded, act_user_data)


if __name__ == '__main__':
    unittest.main()
