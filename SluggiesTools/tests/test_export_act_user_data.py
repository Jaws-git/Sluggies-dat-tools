"""PLAN_AddBones.md Phase 1 - export the missing ACT facts to .sluggie.

Round-trip tests for the new export.py fields: BoneHierarchy's per-bone
MirrorBoneId/MirrorRole (decoded from the kind-2 ACT user-data entry, F3)
and the model-level ACTUserData object (which kinds are present, plus every
kind-4 entry preserved verbatim as an opaque blob, F5).

Builds a synthetic ACT section with ``act_rebuild`` (already exercised by
test_act_rebuild_identity.py) and feeds it through the *real* act.py parser
(``ACTLayout``) rather than a hand-rolled duplicate, so this test exercises
the same code path export.py itself uses. No game assets required.

export.py itself is never imported (CLAUDE.md: "Never import export.py for
side effects -- it prompts interactively"; in fact the whole module runs an
export the moment it's imported, not just at the bottom). Instead the four
functions under test are lifted out of its source with ``ast`` and exec'd
into a throwaway namespace -- this exercises the exact same code as a real
export, without ever letting the module-level script body run.
"""
from __future__ import annotations

import ast
import base64
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
from act import ACTLayout  # noqa: E402
from binfmt import encode_field as _encode_field  # noqa: E402


def _load_export_functions(*names):
    """Exec just the named top-level function defs from export.py's source
    into a fresh namespace, skipping every module-level side effect."""
    export_path = os.path.join(TOOLS_DIR, 'export.py')
    with open(export_path, 'r', encoding='utf-8') as f:
        source = f.read()
    tree = ast.parse(source, filename=export_path)
    wanted = set(names)
    module = ast.Module(body=[
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ], type_ignores=[])
    ns = {'struct': struct, 'base64': base64, 'DEBUG_DONT_USE_BASE64': False,
          '_encode_field': _encode_field}

    class _StubLogger:
        def warning(self, *args, **kwargs):
            pass
    ns['_slogger'] = _StubLogger()

    exec(compile(module, export_path, 'exec'), ns)
    missing = wanted - ns.keys()
    if missing:
        raise AssertionError(f"export.py no longer defines: {sorted(missing)}")
    return ns


export = type('export', (), _load_export_functions(
    '_encode_bytes', '_read_act_user_data_descriptors', '_extract_mirror_table',
    'extract_act_user_data', 'extract_bone_data',
))


def _build_synthetic_act_bytes(bone_count=3, kind4_track_ids=(5, 7)):
    """Build a small ACT section: a root bone (id 0) with two children
    (ids 1, 2), a kind-3 track table, a kind-2 mirror table where bone 1
    mirrors bone 2 (and vice versa) and bone 0 self-mirrors, and a kind-4
    entry carrying ``kind4_track_ids`` (F5's opaque '(u16 track_id, u16=2)'
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
        # Build a donor with no user data at all (F4's legal, shipped state).
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

    def test_act_user_data_present_kinds_and_kind4_blob_round_trip(self):
        act_bytes = _build_synthetic_act_bytes(kind4_track_ids=(5, 7, 42))
        model = _make_model(act_bytes)

        act_user_data = export.extract_act_user_data(model)
        self.assertEqual(act_user_data['PresentKinds'], [3, 2, 4])
        self.assertEqual(len(act_user_data['Kind4Entries']), 1)
        entry = act_user_data['Kind4Entries'][0]
        self.assertEqual(entry['Count'], 3)
        self.assertEqual(entry['DataPtr'], 0x0C)
        payload = base64.b64decode(entry['Payload'])
        self.assertEqual(payload, b''.join(struct.pack('>HH', tid, 2) for tid in (5, 7, 42)))

        # Full round trip through JSON, as the .sluggie file itself does.
        reloaded = json.loads(json.dumps(act_user_data))
        self.assertEqual(reloaded, act_user_data)


if __name__ == '__main__':
    unittest.main()
