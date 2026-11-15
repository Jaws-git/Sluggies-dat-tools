"""patch_skn_inplace.py — SKN/SK1/SK2/SKAcc in-place patching helpers.

All pointer fields inside the SKN section are uint32 BE values relative to
SKN.absolute (the ``SKNOffset`` stored in the JSON ``SkinData`` block).

In-place patching (same vertex layout):
  ``patchSKNInPlace``        — write *Edited source/weight arrays at original offsets
  ``patchSKNInPlaceResized`` — write shortened arrays at their original offsets when vertex counts changed
  ``restoreSKNInPlace``      — restore original source/weight bytes at original offsets
  ``restoreSKNBlockInPlace`` — full block restore (pointer fields + data bytes)
"""

import contextlib
import os
import sys
import struct

# Resolve project directories relative to this file so the module works
# regardless of the current working directory.
_THIS_DIR  = os.path.dirname(os.path.abspath(__file__))   # .../SluggiesTools/InplacePatcher
_TOOLS_DIR = os.path.dirname(_THIS_DIR)                    # .../SluggiesTools
_ROOT_DIR  = os.path.dirname(_TOOLS_DIR)                   # project root

# Ensure SluggiesTools/ (slogger, …) and this directory are importable.
for _p in (_TOOLS_DIR, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import slogger as _slogger
_slogger.configure()

import root_scale as _root_scale
from binfmt import comp_size as _comp_size
from binfmt import decode_field as _decode_field
from binfmt import skin_bone_ids, skn_direct_entry_problem

OUTPUT_DAT = os.path.join(_ROOT_DIR, '3_Output_Dat', 'dt_na.dat')
INPUT_DAT = os.path.join(_ROOT_DIR, '1_Input', 'dt_na.dat')


def _to_bytes(data) -> bytes:
    """Decode a binary field: ``x:`` hex string (legacy: base64 or ``z:`` zlib) or a list of byte values."""
    return _decode_field(data)


def _original_flush_bytes(skin_data: dict):
    """The donor flush-index array: ``FlushIndData`` from the .sluggie, else read from the INPUT DAT at its
    original offset. Never from the output DAT: a resized patch moves the array there, so a second patch (or an
    unpatch) would read whatever now sits at the old offset. None when neither source has it."""
    size = int(skin_data.get('FlushIndSize') or 0) * 2
    data = skin_data.get('FlushIndData')
    if data:
        raw = _to_bytes(data)
        if len(raw) == size:
            return raw
    flush_abs_str = skin_data.get('FlushIndAbsolutePtr')
    if not size or not flush_abs_str or flush_abs_str == 'null' or not os.path.isfile(INPUT_DAT):
        return None
    with open(INPUT_DAT, 'rb') as f_r:
        f_r.seek(int(flush_abs_str, 16))
        raw = f_r.read(size)
    return raw if len(raw) == size else None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _scaled_bind_pose(entry: dict, skin_data: dict, factors) -> bytes | None:
    """Return the effective bind-pose bytes for *entry*, scaled by *factors*.

    The effective payload is ``BindPoseDataEdited`` when present, else the
    original ``BindPoseData``. When *factors* is None or a no-op unit scale the
    payload is returned unchanged. The vertex count used for scaling matches the
    payload (``VertexCntEdited`` for edited data, else ``VertexCnt``); any
    truncation is handled inside ``root_scale.scale_bind_pose_data``. Returns
    None when the entry carries no bind-pose data at all.
    """
    edited = entry.get('BindPoseDataEdited')
    if edited is not None:
        payload = edited
        vertex_cnt = int(entry.get('VertexCntEdited', entry.get('VertexCnt', 0)))
    else:
        payload = entry.get('BindPoseData')
        if payload is None:
            return None
        vertex_cnt = int(entry.get('VertexCnt', 0))
    data = _to_bytes(payload)
    if factors is None or factors == (1.0, 1.0, 1.0):
        return data
    quant_info = int(skin_data.get('QuantizeInfo', 0))
    vertex_offset = int(entry.get('VertexOffset', 0))
    return _root_scale.scale_bind_pose_data(
        data, quant_info, vertex_cnt, vertex_offset, factors
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def skn_edit_problems(skin_data: dict, bone_ids=None) -> list[str]:
    """Reasons the in-place SKN edits in *skin_data* would crash or break the
    game (skn_section.html#runtime-limits); empty when they are safe.

    Checks each SK1/SK2 entry's effective vertex count (``VertexCntEdited``
    or ``VertexCnt``) against the skinning loop's minimum and the locked-cache
    buffer size, and that an SK1 ``BoneIndexEdited`` names a bone of the
    skeleton (*bone_ids*; without it, a bone the model's own skin uses). Any
    bone gets a skinning matrix (Dolphin, 2026-10-24)."""
    stride = 6 * _comp_size(int(skin_data.get('QuantizeInfo', 0)))
    problems = []
    for kind in ('SK1', 'SK2'):
        for index, entry in enumerate(skin_data.get(f'{kind}s', [])):
            count = int(entry.get('VertexCntEdited', entry.get('VertexCnt', 0)))
            problem = skn_direct_entry_problem(
                kind, count, int(entry.get('VertexOffset', 0)), stride)
            if problem:
                problems.append(f'{kind}[{index}]: {problem}')
    allowed = skin_bone_ids(skin_data) | {int(b) for b in (bone_ids or ())}
    for index, entry in enumerate(skin_data.get('SK1s', [])):
        bone = entry.get('BoneIndexEdited')
        if bone is not None and int(bone) not in allowed:
            problems.append(f'SK1[{index}]: bone {bone} is not a bone of this model')
    return problems


@contextlib.contextmanager
def _output_dat(dat):
    """The handle the SKN writes go through: *dat* (the caller's guarded handle, see ``dat_writes.GuardedDat``)
    or, without one, the output DAT opened for the call."""
    if dat is not None:
        yield dat
    else:
        with open(OUTPUT_DAT, 'r+b') as f:
            yield f


def _effective(entry: dict, edited_key: str, orig_key: str):
    """The edited field when the entry has one (an empty array included), else the original."""
    edited = entry.get(edited_key)
    return edited if edited is not None else entry.get(orig_key)


def _resized_arrays(skin_data: dict, factors) -> list[tuple[str, int, int, bytes]]:
    """``(label, absolute offset, original length, new bytes)`` of every SKN source, weight and destination-index
    array of a resized patch. Each array stays at its original offset."""
    arrays = []

    def add(label, entry, pointer_key, orig_key, data):
        arrays.append((label, int(entry[pointer_key], 16), len(_to_bytes(entry[orig_key])), data))

    for index, entry in enumerate(skin_data.get('SK1s', [])):
        add(f'SK1[{index}] source', entry, 'VertexArrAbsolutePtr', 'BindPoseData',
            _scaled_bind_pose(entry, skin_data, factors))
    for index, entry in enumerate(skin_data.get('SK2s', [])):
        add(f'SK2[{index}] source', entry, 'VertexArrAbsolutePtr', 'BindPoseData',
            _scaled_bind_pose(entry, skin_data, factors))
        add(f'SK2[{index}] weights', entry, 'WeightArrAbsolutePtr', 'WeightData',
            _to_bytes(_effective(entry, 'WeightDataEdited', 'WeightData')))
    for index, entry in enumerate(skin_data.get('SKAccs', [])):
        add(f'SKAcc[{index}] source', entry, 'VertexArrAbsolutePtr', 'BindPoseData',
            _scaled_bind_pose(entry, skin_data, factors))
        add(f'SKAcc[{index}] destination indices', entry, 'DestArrAbsolutePtr', 'DestIndexData',
            _to_bytes(_effective(entry, 'DestIndexDataEdited', 'DestIndexData')))
        add(f'SKAcc[{index}] weights', entry, 'WeightArrAbsolutePtr', 'WeightData',
            _to_bytes(_effective(entry, 'WeightDataEdited', 'WeightData')))
    return arrays


def patchSKNInPlaceResized(skin_data: dict, factors=None, dry_run: bool = False, dat=None) -> int:
    """Write SKN source arrays whose vertex counts changed, each at its original offset.

    Reads the edited data from the inline ``*Edited`` fields of each SK entry (``BindPoseDataEdited``,
    ``WeightDataEdited``, ``DestIndexDataEdited``, ``VertexCntEdited``). An edited array may be shorter than
    the original (the Blender exporter only removes vertices from an entry), never longer: every array keeps
    its place, so it keeps the 32-byte alignment the game's cache-line code expects
    (``Hammerspace/ModelFormat.ARRAY_ALIGNMENT``; all 35,460 arrays of the 397 vanilla skinned models are at
    0 mod 32, checked 2026-11-15) and the flush-index array does not move. The unused rest of a shortened
    array is zeroed. An array that would grow is refused: that takes a Hammerspace export.

    The pointer fields are written with their original values, which also puts back the layout of a patch made
    by an earlier version of this function (it packed the arrays 4-byte aligned behind the structs and moved
    the flush-index array).

    The dest buffer (gplVertexArr / gplDestArr) is **not** updated — the
    draw list remains valid and vertices whose SK entry has shrunk will have
    their dest slots zeroed by the game's memory-clear step each frame.

    Called from the non-hammerspace patch path when any SK entry carries a
    ``VertexCntEdited`` key.  ``restoreSKNBlockInPlace`` is used to undo.

    Returns total SK1+SK2 vertex count on success, -1 on failure (nothing written).
    ``dry_run``: the size checks only, nothing read from or written to the DAT.
    ``dat``: an open handle to write through instead of opening the output DAT.
    """
    skn_abs     = int(skin_data['SKNOffset'], 16)
    orig_sk1s   = skin_data.get('SK1s',   [])
    orig_sk2s   = skin_data.get('SK2s',   [])
    orig_skaccs = skin_data.get('SKAccs', [])

    arrays = _resized_arrays(skin_data, factors)
    grown = [f"{label} ({orig_len} -> {len(data)} B)" for label, _offset, orig_len, data in arrays
             if len(data) > orig_len]
    if grown:
        _slogger.error(
            "SKN in-place resize: an array would grow past its original size, which cannot be patched in place "
            f"(re-export from Blender; a grown skin array is a Hammerspace export): {'; '.join(grown)}.",
            source="patch_skn_inplace",
        )
        return -1
    total_verts = sum(e.get('VertexCntEdited', e['VertexCnt']) for e in orig_sk1s + orig_sk2s)
    if dry_run:
        return total_verts

    flush_abs_str = skin_data.get('FlushIndAbsolutePtr')
    has_flush = bool(skin_data.get('FlushIndSize')) and flush_abs_str not in (None, '', 'null')

    with _output_dat(dat) as f:
        # A patch by an earlier version moved the flush-index array and wrote over its original bytes: put
        # both back. Checked before the first write, so a failure leaves the DAT untouched.
        flush_restore = None
        if has_flush:
            flush_field = int(skin_data['FlushIndArrFieldOffset'], 16)
            flush_rel = int(flush_abs_str, 16) - skn_abs
            f.seek(flush_field)
            if f.read(4) != struct.pack('>I', flush_rel):
                flush_bytes = _original_flush_bytes(skin_data)
                if flush_bytes is None:
                    _slogger.error(
                        "SKN in-place resize: an earlier patch moved the flush-index array, and the donor array "
                        f"is neither in the .sluggie (FlushIndData) nor readable from {INPUT_DAT}. Aborting.",
                        source="patch_skn_inplace",
                    )
                    return -1
                flush_restore = (flush_field, flush_rel, flush_bytes)

        for _label, offset, orig_len, data in arrays:
            if orig_len:
                f.seek(offset)
                f.write(data + bytes(orig_len - len(data)))
        if flush_restore is not None:
            flush_field, flush_rel, flush_bytes = flush_restore
            f.seek(flush_field)
            f.write(struct.pack('>I', flush_rel))
            f.seek(skn_abs + flush_rel)
            f.write(flush_bytes)

        # --- SK1 entries ---
        # vertexArr pointer (original value), bone index and vertexCnt. vertexOffset is preserved
        # (BindPoseDataEdited includes the original prefix bytes).
        # gplVertexArr is left unchanged — dest buffer layout is unchanged.
        for orig in orig_sk1s:
            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va);        f.write(struct.pack('>I', int(orig['VertexArrAbsolutePtr'], 16) - skn_abs))
            f.seek(va + 0x08); f.write(struct.pack('>H', orig.get('BoneIndexEdited', orig['BoneIndex'])))
            f.seek(va + 0x0A); f.write(struct.pack('>H', orig.get('VertexCntEdited', orig['VertexCnt'])))
            f.seek(va + 0x0C); f.write(bytes([orig.get('VertexOffset', 0)]))

        # --- SK2 entries ---
        for orig in orig_sk2s:
            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va); f.write(struct.pack('>I', int(orig['VertexArrAbsolutePtr'], 16) - skn_abs))
            f.seek(int(orig['WeightArrFieldOffset'], 16))
            f.write(struct.pack('>I', int(orig['WeightArrAbsolutePtr'], 16) - skn_abs))
            f.seek(va + 0x10); f.write(struct.pack('>H', orig.get('VertexCntEdited', orig['VertexCnt'])))
            f.seek(va + 0x12); f.write(bytes([orig.get('VertexOffset', 0)]))

        # --- SKAcc entries ---
        # gplDestArr is left unchanged.
        for orig in orig_skaccs:
            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va); f.write(struct.pack('>I', int(orig['VertexArrAbsolutePtr'], 16) - skn_abs))
            f.seek(int(orig['DestArrFieldOffset'], 16))
            f.write(struct.pack('>I', int(orig['DestArrAbsolutePtr'], 16) - skn_abs))
            f.seek(int(orig['WeightArrFieldOffset'], 16))
            f.write(struct.pack('>I', int(orig['WeightArrAbsolutePtr'], 16) - skn_abs))
            f.seek(va + 0x12); f.write(struct.pack('>H', orig.get('VertexCntEdited', orig['VertexCnt'])))

    return total_verts


def patchSKNInPlace(skin_data: dict, factors=None, dat=None) -> bool:
    """Overwrite SK1/SK2/SKAcc bind-pose source and weight arrays in-place.

    Called when UseHammerspace=False and the model has skin data.  Vertex
    count must be unchanged — only values are updated.

    Reads 'BindPoseDataEdited' (SK1/SK2/SKAcc) and 'WeightDataEdited'
    (SK2/SKAcc) written by the Blender exporter's encode_skin_weights_inplace
    and writes them directly to their original absolute file positions.

    Returns True if any bytes were written.
    ``dat``: an open handle to write through instead of opening the output DAT (also in the restore functions).
    """
    wrote_any = False
    with _output_dat(dat) as f:
        for sk1 in skin_data.get('SK1s', []):
            bone_idx_edited = sk1.get('BoneIndexEdited')
            if bone_idx_edited is not None:
                # SK1 layout: vertexArr at +0x30, gplVertexArr +0x34, boneIndex +0x38.
                va = int(sk1['VertexArrFieldOffset'], 16)
                f.seek(va + 0x08)
                f.write(struct.pack('>H', bone_idx_edited))
                wrote_any = True

            src_edited = sk1.get('BindPoseDataEdited')
            if src_edited is not None or factors is not None:
                scaled = _scaled_bind_pose(sk1, skin_data, factors)
                if scaled is not None:
                    f.seek(int(sk1['VertexArrAbsolutePtr'], 16))
                    f.write(scaled)
                    wrote_any = True

        for sk2 in skin_data.get('SK2s', []):
            src_edited = sk2.get('BindPoseDataEdited')
            if src_edited is not None or factors is not None:
                scaled = _scaled_bind_pose(sk2, skin_data, factors)
                if scaled is not None:
                    f.seek(int(sk2['VertexArrAbsolutePtr'], 16))
                    f.write(scaled)
                    wrote_any = True
            wt_edited = sk2.get('WeightDataEdited')
            if wt_edited:
                f.seek(int(sk2['WeightArrAbsolutePtr'], 16))
                f.write(_to_bytes(wt_edited))
                wrote_any = True

        for skacc in skin_data.get('SKAccs', []):
            src_edited = skacc.get('BindPoseDataEdited')
            if src_edited is not None or factors is not None:
                scaled = _scaled_bind_pose(skacc, skin_data, factors)
                if scaled is not None:
                    f.seek(int(skacc['VertexArrAbsolutePtr'], 16))
                    f.write(scaled)
                    wrote_any = True
            wt_edited = skacc.get('WeightDataEdited')
            if wt_edited:
                f.seek(int(skacc['WeightArrAbsolutePtr'], 16))
                f.write(_to_bytes(wt_edited))
                wrote_any = True

    return wrote_any


def restoreSKNInPlace(skin_data: dict, dat=None) -> bool:
    """Restore SK1/SK2/SKAcc bind-pose source and weight arrays to original values.

    Mirror of ``patchSKNInPlace`` for the --unpatch path.  Reads the original
    ``BindPoseData`` and ``WeightData`` fields (stored by the exporter) and
    writes them back to the absolute file positions recorded in the JSON.

    Safe to call unconditionally for any skinned model; entries that lack
    the original data fields are silently skipped.

    Returns True if any bytes were written.
    """
    wrote_any = False
    with _output_dat(dat) as f:
        for sk1 in skin_data.get('SK1s', []):
            # Always restore original SK1 bone index when present.
            if sk1.get('BoneIndex') is not None:
                va = int(sk1['VertexArrFieldOffset'], 16)
                f.seek(va + 0x08)
                f.write(struct.pack('>H', sk1['BoneIndex']))
                wrote_any = True

            src = sk1.get('BindPoseData')
            if src:
                f.seek(int(sk1['VertexArrAbsolutePtr'], 16))
                f.write(_to_bytes(src))
                wrote_any = True

        for sk2 in skin_data.get('SK2s', []):
            src = sk2.get('BindPoseData')
            if src:
                f.seek(int(sk2['VertexArrAbsolutePtr'], 16))
                f.write(_to_bytes(src))
                wrote_any = True
            wt = sk2.get('WeightData')
            if wt:
                f.seek(int(sk2['WeightArrAbsolutePtr'], 16))
                f.write(_to_bytes(wt))
                wrote_any = True

        for skacc in skin_data.get('SKAccs', []):
            src = skacc.get('BindPoseData')
            if src:
                f.seek(int(skacc['VertexArrAbsolutePtr'], 16))
                f.write(_to_bytes(src))
                wrote_any = True
            wt = skacc.get('WeightData')
            if wt:
                f.seek(int(skacc['WeightArrAbsolutePtr'], 16))
                f.write(_to_bytes(wt))
                wrote_any = True

    return wrote_any


def restoreSKNBlockInPlace(skin_data: dict, dat=None) -> bool:
    """Restore SK struct pointer/count fields and source-data bytes after
    a ``patchSKNInPlaceResized`` call (the --unpatch path of a resized skin).

    Handles:
    - ``vertexArr``, ``weightArr``, ``destArr`` relative pointer fields
    - ``vertexCnt`` and ``vertexOffset`` for each SK entry
    - ``flushIndArr`` header pointer
    - Original data bytes written back at their original absolute positions

    Note: ``memClrPtr``, ``memClrSze``, ``gplVertexArr``, and ``gplDestArr``
    are already restored by ``restoreSKNPointers`` (called from
    ``restoreSubmeshFromHammerspace``) and are therefore not touched here.

    Returns True if any bytes were written.
    """
    skn_abs   = int(skin_data['SKNOffset'], 16)
    wrote_any = False

    with _output_dat(dat) as f:

        # Restore flushIndArr header pointer.
        flush_abs_str = skin_data.get('FlushIndAbsolutePtr')
        if flush_abs_str and flush_abs_str not in (None, 'null'):
            f.seek(int(skin_data['FlushIndArrFieldOffset'], 16))
            f.write(struct.pack('>I', int(flush_abs_str, 16) - skn_abs))
            # The resized patch's blob covered the array's original bytes.
            flush_bytes = _original_flush_bytes(skin_data)
            if flush_bytes is not None:
                f.seek(int(flush_abs_str, 16))
                f.write(flush_bytes)
            else:
                _slogger.warning(
                    "SKN restore: the donor flush-index array was not found (no FlushIndData, no input DAT); "
                    "its pointer is restored but its bytes are not.",
                    source="patch_skn_inplace",
                )
            wrote_any = True

        # --- SK1 entries ---
        for orig in skin_data.get('SK1s', []):
            orig_src = int(orig['VertexArrAbsolutePtr'], 16)
            va       = int(orig['VertexArrFieldOffset'], 16)

            f.seek(va);        f.write(struct.pack('>I', orig_src - skn_abs))
            f.seek(va + 0x08); f.write(struct.pack('>H', orig['BoneIndex']))
            f.seek(va + 0x0A); f.write(struct.pack('>H', orig['VertexCnt']))
            f.seek(va + 0x0C); f.write(bytes([orig['VertexOffset']]))

            f.seek(orig_src); f.write(_to_bytes(orig['BindPoseData']))
            wrote_any = True

        # --- SK2 entries ---
        for orig in skin_data.get('SK2s', []):
            orig_src = int(orig['VertexArrAbsolutePtr'], 16)
            orig_wt  = int(orig['WeightArrAbsolutePtr'], 16)
            va       = int(orig['VertexArrFieldOffset'], 16)

            f.seek(va);                                    f.write(struct.pack('>I', orig_src - skn_abs))
            f.seek(int(orig['WeightArrFieldOffset'], 16)); f.write(struct.pack('>I', orig_wt  - skn_abs))
            f.seek(va + 0x10); f.write(struct.pack('>H', orig['VertexCnt']))
            f.seek(va + 0x12); f.write(bytes([orig['VertexOffset']]))

            f.seek(orig_src); f.write(_to_bytes(orig['BindPoseData']))
            f.seek(orig_wt);  f.write(_to_bytes(orig['WeightData']))
            wrote_any = True

        # --- SKAcc entries ---
        for orig in skin_data.get('SKAccs', []):
            orig_src  = int(orig['VertexArrAbsolutePtr'], 16)
            orig_dest = int(orig['DestArrAbsolutePtr'],   16)
            orig_wt   = int(orig['WeightArrAbsolutePtr'], 16)
            va        = int(orig['VertexArrFieldOffset'],  16)

            f.seek(va);                                    f.write(struct.pack('>I', orig_src  - skn_abs))
            f.seek(int(orig['DestArrFieldOffset'],   16)); f.write(struct.pack('>I', orig_dest - skn_abs))
            f.seek(int(orig['WeightArrFieldOffset'], 16)); f.write(struct.pack('>I', orig_wt   - skn_abs))
            f.seek(va + 0x12); f.write(struct.pack('>H', orig['VertexCnt']))
            # SKAcc has no vertexOffset field at this position.

            f.seek(orig_src);  f.write(_to_bytes(orig['BindPoseData']))
            f.seek(orig_dest); f.write(_to_bytes(orig['DestIndexData']))
            f.seek(orig_wt);   f.write(_to_bytes(orig['WeightData']))
            wrote_any = True

    return wrote_any

