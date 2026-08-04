"""patch_skn_inplace.py — SKN/SK1/SK2/SKAcc in-place patching helpers.

All pointer fields inside the SKN section are uint32 BE values relative to
SKN.absolute (the ``SKNOffset`` stored in the JSON ``SkinData`` block).

In-place patching (same vertex layout):
  ``patchSKNInPlace``        — write *Edited source/weight arrays at original offsets
  ``patchSKNInPlaceResized`` — repack variable-data region when vertex counts changed
  ``restoreSKNInPlace``      — restore original source/weight bytes at original offsets
  ``restoreSKNBlockInPlace`` — full block restore (pointer fields + data bytes)
"""

import base64
import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import slogger as _slogger
_slogger.configure()

OUTPUT_DAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '3_Output_Dat', 'dt_na.dat')


def _to_bytes(data) -> bytes:
    """Decode binary data that is either a base64 string or a list of byte values."""
    if isinstance(data, list):
        return bytes(data)
    return base64.b64decode(data)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _comp_size(quant_info: int) -> int:
    fmt = quant_info >> 4
    return 4 if fmt in [4, 7, 0xa] else 2


def _align4(data: bytes) -> bytes:
    r = len(data) % 4
    return data + b'\x00' * ((4 - r) % 4)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def skn_block_size(skin_data: dict, flush_ind_size: int = None) -> int:
    """Return the total byte length required for an SKN block.

    Accepts either a ``SkinData`` dict (produced by ``export.py``) or a
    ``SkinDataEdited`` dict (written by the Blender exporter).  Both carry
    ``BindPoseData``, ``WeightData``, and ``DestIndexData`` as base64 or
    byte-list payloads, so the same arithmetic applies to either form.

    Block layout (matching the order used by ``patchSKNSourceArrays``):

        SKN header              0x24 bytes
        SK1 structs             N × 0x40
        SK2 structs             N × 0x74
        SKAcc structs           N × 0x44
        per-SK1 source data     len(BindPoseData), 4-byte aligned
        per-SK2 source + wt     len(BindPoseData) + len(WeightData), each aligned
        per-SKAcc src/dest/wt   three arrays, each 4-byte aligned
        flush index array       flush_ind_size × 2 bytes, 4-byte aligned

    Parameters
    ----------
    skin_data :
        SkinData or SkinDataEdited dict.
    flush_ind_size :
        Number of flush indices.  When None the value is read from
        ``skin_data['FlushIndSize']``; when that key is also absent it
        defaults to 0.  ``SkinDataEdited`` dicts do not carry this field --
        pass the original ``SkinData['FlushIndSize']`` explicitly when
        computing the edited size.
    """
    sk1s   = skin_data.get('SK1s',   [])
    sk2s   = skin_data.get('SK2s',   [])
    skaccs = skin_data.get('SKAccs', [])
    if flush_ind_size is None:
        flush_ind_size = skin_data.get('FlushIndSize', 0)

    def _a4(n: int) -> int:
        return (n + 3) & ~3

    total  = 0x24                 # SKN header
    total += len(sk1s)   * 0x40  # SK1 structs
    total += len(sk2s)   * 0x74  # SK2 structs
    total += len(skaccs) * 0x44  # SKAcc structs

    for e in sk1s:
        total += _a4(len(_to_bytes(e['BindPoseData'])))

    for e in sk2s:
        total += _a4(len(_to_bytes(e['BindPoseData'])))
        total += _a4(len(_to_bytes(e['WeightData'])))

    for e in skaccs:
        total += _a4(len(_to_bytes(e['BindPoseData'])))
        total += _a4(len(_to_bytes(e['DestIndexData'])))
        total += _a4(len(_to_bytes(e['WeightData'])))

    if flush_ind_size:
        total += _a4(flush_ind_size * 2)

    return total


def patchSKNInPlaceResized(skin_data: dict) -> int:
    """Repack SKN source arrays in-place supporting variable vertex counts.

    Like ``patchSKNBlockInPlace`` but reads edited data from inline
    ``*Edited`` fields of each SK entry (``BindPoseDataEdited``,
    ``WeightDataEdited``, ``DestIndexDataEdited``, ``VertexCntEdited``)
    rather than a separate ``SkinDataEdited`` dict.

    The dest buffer (gplVertexArr / gplDestArr) is **not** updated — the
    draw list remains valid and vertices whose SK entry has shrunk will have
    their dest slots zeroed by the game's memory-clear step each frame.

    Called from the non-hammerspace patch path when any SK entry carries a
    ``VertexCntEdited`` key.  ``restoreSKNBlockInPlace`` is used to undo.

    Returns total SK1+SK2 vertex count on success, -1 on failure.
    """
    skn_abs     = int(skin_data['SKNOffset'], 16)
    orig_sk1s   = skin_data.get('SK1s',   [])
    orig_sk2s   = skin_data.get('SK2s',   [])
    orig_skaccs = skin_data.get('SKAccs', [])

    n_sk1  = len(orig_sk1s)
    n_sk2  = len(orig_sk2s)

    # Variable-data region starts immediately after the struct section.
    data_start_rel = 0x24 + n_sk1 * 0x40 + n_sk2 * 0x74 + len(orig_skaccs) * 0x44
    data_start_abs = skn_abs + data_start_rel

    def _eff(entry, edited_key, orig_key):
        """Return the edited field if present, else the original."""
        return entry.get(edited_key) or entry.get(orig_key)

    # Build source-array blob in the same order as patchSKNBlockInPlace /
    # skn_block_size:  [sk1_src…] [sk2_src sk2_wt…] [skacc_src skacc_dest skacc_wt…]
    blob    = bytearray()
    offsets = []   # blob-relative byte offset for each sub-array, in write order

    for e in orig_sk1s:
        offsets.append(len(blob))
        blob.extend(_align4(_to_bytes(_eff(e, 'BindPoseDataEdited', 'BindPoseData'))))

    for e in orig_sk2s:
        offsets.append(len(blob))
        blob.extend(_align4(_to_bytes(_eff(e, 'BindPoseDataEdited', 'BindPoseData'))))
        offsets.append(len(blob))
        blob.extend(_align4(_to_bytes(_eff(e, 'WeightDataEdited', 'WeightData'))))

    for e in orig_skaccs:
        offsets.append(len(blob))
        blob.extend(_align4(_to_bytes(_eff(e, 'BindPoseDataEdited', 'BindPoseData'))))
        offsets.append(len(blob))
        blob.extend(_align4(_to_bytes(_eff(e, 'DestIndexDataEdited', 'DestIndexData'))))
        offsets.append(len(blob))
        blob.extend(_align4(_to_bytes(_eff(e, 'WeightDataEdited', 'WeightData'))))

    # Flush-index array — content is unchanged; read from its current file position.
    flush_ind_size = skin_data.get('FlushIndSize', 0)
    flush_abs_str  = skin_data.get('FlushIndAbsolutePtr')
    flush_blob_rel = None
    if flush_ind_size and flush_abs_str and flush_abs_str not in (None, 'null'):
        flush_blob_rel = len(blob)
        with open(OUTPUT_DAT, 'rb') as f_r:
            f_r.seek(int(flush_abs_str, 16))
            flush_bytes = f_r.read(flush_ind_size * 2)
        blob.extend(_align4(flush_bytes))

    # Zero-pad to fill the original variable-data region exactly.
    orig_var_size = skn_block_size(skin_data) - data_start_rel
    pad = orig_var_size - len(blob)
    if pad < 0:
        _slogger.error(
            f"SKN in-place resize: edited data ({len(blob)} B) exceeds "
            f"original variable region ({orig_var_size} B). Aborting.",
            source="patch_skn_inplace",
        )
        return -1
    blob.extend(b'\x00' * pad)

    total_verts = 0

    with open(OUTPUT_DAT, 'r+b') as f:
        f.seek(data_start_abs)
        f.write(bytes(blob))

        # Update flushIndArr header pointer when the flush array moved.
        if flush_blob_rel is not None:
            new_flush_rel = data_start_rel + flush_blob_rel
            f.seek(int(skin_data['FlushIndArrFieldOffset'], 16))
            f.write(struct.pack('>I', new_flush_rel))

        # --- SK1 entries ---
        # Update vertexArr pointer and vertexCnt.  vertexOffset is preserved
        # (BindPoseDataEdited includes the original prefix bytes).
        # gplVertexArr is left unchanged — dest buffer layout is unchanged.
        for i, orig in enumerate(orig_sk1s):
            src_abs  = data_start_abs + offsets[i]
            new_cnt  = orig.get('VertexCntEdited', orig['VertexCnt'])
            total_verts += new_cnt

            f.seek(int(orig['VertexArrFieldOffset'], 16))
            f.write(struct.pack('>I', src_abs - skn_abs))
            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va + 0x0A); f.write(struct.pack('>H', new_cnt))
            f.seek(va + 0x0C); f.write(bytes([orig.get('VertexOffset', 0)]))

        # --- SK2 entries ---
        for i, orig in enumerate(orig_sk2s):
            src_abs = data_start_abs + offsets[n_sk1 + i * 2]
            wt_abs  = data_start_abs + offsets[n_sk1 + i * 2 + 1]
            new_cnt = orig.get('VertexCntEdited', orig['VertexCnt'])
            total_verts += new_cnt

            f.seek(int(orig['VertexArrFieldOffset'],  16)); f.write(struct.pack('>I', src_abs - skn_abs))
            f.seek(int(orig['WeightArrFieldOffset'],  16)); f.write(struct.pack('>I', wt_abs  - skn_abs))
            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va + 0x10); f.write(struct.pack('>H', new_cnt))
            f.seek(va + 0x12); f.write(bytes([orig.get('VertexOffset', 0)]))

        # --- SKAcc entries ---
        # gplDestArr is left unchanged.
        for i, orig in enumerate(orig_skaccs):
            src_abs  = data_start_abs + offsets[n_sk1 + n_sk2 * 2 + i * 3]
            darr_abs = data_start_abs + offsets[n_sk1 + n_sk2 * 2 + i * 3 + 1]
            wt_abs   = data_start_abs + offsets[n_sk1 + n_sk2 * 2 + i * 3 + 2]
            new_cnt  = orig.get('VertexCntEdited', orig['VertexCnt'])

            f.seek(int(orig['VertexArrFieldOffset'], 16)); f.write(struct.pack('>I', src_abs  - skn_abs))
            f.seek(int(orig['DestArrFieldOffset'],   16)); f.write(struct.pack('>I', darr_abs - skn_abs))
            f.seek(int(orig['WeightArrFieldOffset'], 16)); f.write(struct.pack('>I', wt_abs   - skn_abs))
            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va + 0x12); f.write(struct.pack('>H', new_cnt))

    return total_verts


def patchSKNInPlace(skin_data: dict) -> bool:
    """Overwrite SK1/SK2/SKAcc bind-pose source and weight arrays in-place.

    Called when UseHammerspace=False and the model has skin data.  Vertex
    count must be unchanged — only values are updated.

    Reads 'BindPoseDataEdited' (SK1/SK2/SKAcc) and 'WeightDataEdited'
    (SK2/SKAcc) written by the Blender exporter's encode_skin_weights_inplace
    and writes them directly to their original absolute file positions.

    Returns True if any bytes were written.
    """
    wrote_any = False
    with open(OUTPUT_DAT, 'r+b') as f:
        for sk1 in skin_data.get('SK1s', []):
            src_edited = sk1.get('BindPoseDataEdited')
            if src_edited:
                f.seek(int(sk1['VertexArrAbsolutePtr'], 16))
                f.write(_to_bytes(src_edited))
                wrote_any = True

        for sk2 in skin_data.get('SK2s', []):
            src_edited = sk2.get('BindPoseDataEdited')
            if src_edited:
                f.seek(int(sk2['VertexArrAbsolutePtr'], 16))
                f.write(_to_bytes(src_edited))
                wrote_any = True
            wt_edited = sk2.get('WeightDataEdited')
            if wt_edited:
                f.seek(int(sk2['WeightArrAbsolutePtr'], 16))
                f.write(_to_bytes(wt_edited))
                wrote_any = True

        for skacc in skin_data.get('SKAccs', []):
            src_edited = skacc.get('BindPoseDataEdited')
            if src_edited:
                f.seek(int(skacc['VertexArrAbsolutePtr'], 16))
                f.write(_to_bytes(src_edited))
                wrote_any = True
            wt_edited = skacc.get('WeightDataEdited')
            if wt_edited:
                f.seek(int(skacc['WeightArrAbsolutePtr'], 16))
                f.write(_to_bytes(wt_edited))
                wrote_any = True

    return wrote_any


def restoreSKNInPlace(skin_data: dict) -> bool:
    """Restore SK1/SK2/SKAcc bind-pose source and weight arrays to original values.

    Mirror of ``patchSKNInPlace`` for the --unpatch path.  Reads the original
    ``BindPoseData`` and ``WeightData`` fields (stored by the exporter) and
    writes them back to the absolute file positions recorded in the JSON.

    Safe to call unconditionally for any skinned model; entries that lack
    the original data fields are silently skipped.

    Returns True if any bytes were written.
    """
    wrote_any = False
    with open(OUTPUT_DAT, 'r+b') as f:
        for sk1 in skin_data.get('SK1s', []):
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


def restoreSKNBlockInPlace(skin_data: dict) -> bool:
    """Restore SK struct pointer/count fields and source-data bytes after
    a ``patchSKNBlockInPlace`` call.

    Called from the --unpatch path when ``SkinDataEdited`` is present and
    the SKN source arrays were written in-place (no ``sknsrc`` hammerspace
    chunk exists).

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

    with open(OUTPUT_DAT, 'r+b') as f:

        # Restore flushIndArr header pointer.
        flush_abs_str = skin_data.get('FlushIndAbsolutePtr')
        if flush_abs_str and flush_abs_str not in (None, 'null'):
            f.seek(int(skin_data['FlushIndArrFieldOffset'], 16))
            f.write(struct.pack('>I', int(flush_abs_str, 16) - skn_abs))
            wrote_any = True

        # --- SK1 entries ---
        for orig in skin_data.get('SK1s', []):
            orig_src = int(orig['VertexArrAbsolutePtr'], 16)
            va       = int(orig['VertexArrFieldOffset'], 16)

            f.seek(va);        f.write(struct.pack('>I', orig_src - skn_abs))
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

