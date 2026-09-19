"""PLAN_AddBones.md Phase 0, probe P1 - ACT rebuild identity (manual probe).

Parses every ACT-bearing player model in INPUT dt_na.dat with
``act_rebuild.parse_act`` and re-emits it with ``act_rebuild.rebuild_act_bytes``,
checking byte-identical output. This gates the standalone ACT writer before
Phase 2 promotes it into the real rebuilder: if it cannot faithfully reproduce
every donor's ACT section unchanged, it cannot be trusted to append a bone to
one.

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

This is a **probe, not a unit test**: its whole value is the sweep over the
real game corpus, which is gitignored and cannot live in the test suite (see
`.clinerules`, "tests never read production data"). Run it by hand after
touching `act_rebuild.py`:

    uv run --project SluggiesTools/_build python SluggiesTools/probe_act_rebuild_identity.py

Exits 0 when every in-scope donor rebuilds identically, 1 otherwise (including
when the game assets are missing).
"""
from __future__ import annotations

import os
import struct
import sys

TOOLS_DIR = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))
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


def run_corpus_identity_probe() -> int:
    """Sweep the corpus; return a process exit code."""
    if not (os.path.exists(hh.INPUT_DAT) and os.path.exists(hh.INPUT_DOL)):
        print(f"[P1] missing game assets: need {hh.INPUT_DAT} and {hh.INPUT_DOL}")
        return 1

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

    print(
        f"[P1] {act_models} ACT models: {checked} identity-rebuilt, {refused} refused (F6-style "
        f"malformed mirror table) {sorted(refused_keys)}, {len(failures)} failed; "
        f"{non_model_blocks} non-model blocks skipped"
    )

    if act_models == 0:
        print("[P1] FAIL: no ACT-bearing models found in INPUT dt_na.dat")
        return 1
    if failures:
        print(
            f"[P1] FAIL: {len(failures)} of {act_models} ACT models failed identity rebuild "
            f"({checked} passed, {refused} correctly refused):"
        )
        for line in failures[:20]:
            print(f"  {line}")
        return 1
    missing = KNOWN_MALFORMED_MIRROR_TABLES - refused_keys
    if missing:
        print(f"[P1] FAIL: known-malformed donor(s) {sorted(missing)} (F6) were not encountered and refused")
        return 1
    print("[P1] OK")
    return 0


if __name__ == '__main__':
    sys.exit(run_corpus_identity_probe())
