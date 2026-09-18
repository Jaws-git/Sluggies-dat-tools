"""PLAN_AddBones.md Phase 0, probe P1 - ACT rebuild identity.

Parses every ACT-bearing model in INPUT dt_na.dat with ``act_rebuild.parse_act``
and re-emits it with ``act_rebuild.rebuild_act_bytes``, asserting byte-identical
output. This gates the standalone ACT writer before Phase 2 promotes it into
the real rebuilder: if it cannot faithfully reproduce every donor's ACT
section unchanged, it cannot be trusted to append a bone to one.

The two known-malformed donors (37/1, 51/1; PLAN_AddBones.md F6) are expected
to be refused via a non-involution kind-2 mirror table, not rebuilt.

Scoped to chunk_number 18-118 (Mario .. Black Male Mii in export.py's
folderNameMap): the playable-character models this plan actually concerns
itself with (see PLAN_AddBones.md's non-goals -- stadiums, low-LOD and
equipment entries are each "its own donor" and explicitly out of scope).
A wider sweep of every DOL directory turns up non-player ACT-shaped sections
(map props, scoreboard pieces, ...) whose bone records don't follow F1's
layout -- e.g. chunk 136 ("Scoreboards Items and Obstacles") has a bone
table entry with a garbage orientationPTR partway through, which F1's survey
never claimed to cover ("verified on every player model"). Chasing that
format is out of scope for this plan.

Skipped entirely when 1_Input/dt_na.dat or main.dol are not present (both are
gitignored game assets, not part of the repo).
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
import HammerspaceHelper as hh  # noqa: E402

# PLAN_AddBones.md F6: these two donors have oversized, non-involution kind-2
# mirror tables and must be refused rather than rebuilt.
KNOWN_MALFORMED_MIRROR_TABLES = {(37, 1), (51, 1)}

# export.py's folderNameMap: Mario (18) .. Black Male Mii (118). See module
# docstring for why the sweep is scoped to this range.
PLAYER_CHUNK_RANGE = range(18, 119)

_INPUT_ASSETS_PRESENT = os.path.exists(hh.INPUT_DAT) and os.path.exists(hh.INPUT_DOL)


def _iter_input_model_blocks():
    """Yield ``(chunk_number, file_index, offset, length)`` for every unique
    model block referenced from INPUT main.dol's en slot.

    This is ``export.py``'s ``load_dol_dirs`` directory walk, reimplemented
    here rather than imported -- ``export.py`` prompts interactively on
    import (see CLAUDE.md), so it cannot be imported for its function alone.
    The termination rule (stop once ``file_ptr`` collides with another
    directory's start address, not just on a bad sentinel) matters: several
    directory slots alias each other or share tails, and a sentinel-only
    check reads past them into unrelated DOL memory that happens to start
    with the same marker.
    """
    seen: set[int] = set()
    dir_ptrs = hh._readDirPtrs()
    with open(hh.INPUT_DOL, 'rb') as dol:
        for chunk_number, dir_ptr in enumerate(dir_ptrs):
            other_dir_ptrs = dir_ptrs[:chunk_number] + dir_ptrs[chunk_number + 1:]
            file_ptr = dir_ptr
            file_index = 0
            while file_ptr not in other_dir_ptrs:
                dol.seek(file_ptr)
                raw = dol.read(hh._ENTRY_SIZE)
                if len(raw) < hh._ENTRY_SIZE:
                    break
                words = struct.unpack('>12I', raw)
                if words[0] != hh._DAT_FNAME_PTR:
                    break
                offset, length = words[2], words[1]
                if offset > 0 and length > 0 and offset not in seen:
                    seen.add(offset)
                    yield chunk_number, file_index, offset, length
                file_ptr += hh._ENTRY_SIZE
                file_index += 1


def _safe_clone_act(offset: int, length: int) -> bytes | None:
    """Bounds-checked variant of ``HammerspaceMain.CloneACT``.

    Not every DOL directory entry is a GPL/ACT/TEX/SKN model block -- some
    directories (not in export.py's ``folderNameMap``) hold differently
    shaped data, so header field +0x08 is not an ``act_off`` at all there and
    ``CloneACT``'s unchecked arithmetic on it produces nonsense (multi-GB
    "lengths", offsets past EOF). Returns ``None`` for anything that doesn't
    look like a real, in-bounds ACT section rather than raising or reading
    garbage.
    """
    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(offset)
        hdr = f.read(0x20)
        if len(hdr) < 0x20:
            return None
        act_off = struct.unpack_from('>I', hdr, 0x08)[0]
        if not act_off or act_off >= length:
            return None
        tex_off, skn_off = struct.unpack_from('>II', hdr, 0x0C)
        next_off = tex_off or skn_off or length
        if next_off <= act_off or next_off > length:
            return None
        act_len = next_off - act_off
        f.seek(offset + act_off)
        data = f.read(act_len)
        if len(data) != act_len or len(data) < act_rebuild.HEADER_SIZE:
            return None
        # A real ACT's boneCount must fit its own bone table in the section
        # (and, per PLAN_AddBones.md F3, is capped at 255 by the mirror
        # table's u8 ids). This is the cheapest way to reject the remaining
        # non-model directory entries the DOL walk turns up (map props,
        # stadium items, etc.) that happen to pass the bounds check above.
        bone_count, = struct.unpack_from('>H', data, 0x06)
        if bone_count == 0 or bone_count > 255:
            return None
        if act_rebuild.HEADER_SIZE + bone_count * act_rebuild.BONE_RECORD_SIZE > act_len:
            return None
        return data


@unittest.skipUnless(_INPUT_ASSETS_PRESENT, "requires 1_Input/dt_na.dat and main.dol")
class ACTRebuildCorpusIdentityTests(unittest.TestCase):
    def test_full_corpus_rebuild_is_byte_identical(self):
        checked = 0
        refused = 0
        act_models = 0
        non_model_blocks = 0
        failures: list[str] = []
        refused_keys: set[tuple[int, int]] = set()

        for chunk_number, file_index, offset, length in _iter_input_model_blocks():
            if chunk_number not in PLAYER_CHUNK_RANGE:
                continue
            act_bytes = _safe_clone_act(offset, length)
            if act_bytes is None:
                non_model_blocks += 1
                continue
            if not act_bytes:
                continue
            act_models += 1
            key = (chunk_number, file_index)

            try:
                parsed = act_rebuild.parse_act(act_bytes)
                act_rebuild.validate_mirror_table(parsed)
            except act_rebuild.ACTMirrorTableError:
                # F6: a non-involution mirror table means this donor is out
                # of scope for the rebuilder. The plan names two (37/1,
                # 51/1); the corpus sweep may turn up palette-swap siblings
                # that share the same donor skeleton (e.g. other Magikoopa
                # recolors alongside 51/1) -- that is new information, not a
                # bug, so any detected malformed table is accepted here and
                # just recorded for visibility.
                refused += 1
                refused_keys.add(key)
                continue
            except act_rebuild.ACTParseError as exc:
                failures.append(f"{chunk_number}/{file_index}: parse error: {exc}")
                continue

            try:
                rebuilt = act_rebuild.rebuild_act_bytes(parsed)
            except act_rebuild.ACTParseError as exc:
                failures.append(f"{chunk_number}/{file_index}: rebuild error: {exc}")
                continue

            if rebuilt != act_bytes:
                shorter = min(len(rebuilt), len(act_bytes))
                first_diff = next((i for i in range(shorter) if rebuilt[i] != act_bytes[i]), shorter)
                failures.append(
                    f"{chunk_number}/{file_index}: rebuild mismatch, {len(rebuilt)} vs {len(act_bytes)} "
                    f"bytes, first diff at 0x{first_diff:X}"
                )
                continue

            checked += 1

        self.assertGreater(act_models, 0, "no ACT-bearing models found in INPUT dt_na.dat")
        print(
            f"[P1] {act_models} ACT models: {checked} identity-rebuilt, {refused} refused (F6-style "
            f"malformed mirror table) {sorted(refused_keys)}, {len(failures)} failed; "
            f"{non_model_blocks} non-model blocks skipped"
        )
        if failures:
            self.fail(
                f"{len(failures)} of {act_models} ACT models failed identity rebuild "
                f"({checked} passed, {refused} correctly refused):\n" + "\n".join(failures[:20])
            )
        missing = KNOWN_MALFORMED_MIRROR_TABLES - refused_keys
        self.assertFalse(
            missing,
            f"known-malformed donor(s) {sorted(missing)} (F6) were not encountered and refused",
        )


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

    def test_append_leaf_bone_rejects_unknown_parent(self):
        built = self._build_minimal_act(bone_count=2, with_user_data=True)
        parsed = act_rebuild.parse_act(built)
        with self.assertRaises(ValueError):
            act_rebuild.append_leaf_bone(parsed, parent_id=99, srt_blob=b'\x00' * 0x34)


if __name__ == '__main__':
    unittest.main()
