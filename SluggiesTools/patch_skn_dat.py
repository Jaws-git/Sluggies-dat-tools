"""skn_patch.py — SKN/SK1/SK2/SKAcc pointer and source-array patching.

All pointer fields inside the SKN section are uint32 BE values relative to
SKN.absolute (the ``SKNOffset`` stored in the JSON ``SkinData`` block).

dest buffer relocation
---------------------------------
When the vertex (position) destination buffer is moved to hammerspace,
``patchSKNForNewDestBuffer`` adjusts:
  - SKN.memClrPtr / memClrSze
  - SK1/SK2 gplVertexArr  (shifted by delta)
  - SKAcc gplDestArr       (shifted by delta)

source-array rebuild for vertex-count changes
--------------------------------------------------------
When ``SkinDataEdited`` is present (written by the Blender exporter with
Hammerspace Mode enabled), ``patchSKNBlockInPlace`` writes all source
arrays directly into the contiguous variable-data region of the original
SKN block (the space after the SK struct section) and updates every SK
struct's pointer/count fields in-place:
  - Packs bind-pose source/weight/dest-index arrays sequentially with
    4-byte alignment, starting at ``SKNOffset + struct_section_size``.
  - Patches every SK struct's ``vertexArr``, ``weightArr``, ``destArr``
    pointer fields (relative to SKN.absolute) to the new positions.
  - Rewrites ``vertexCnt`` and clears ``vertexOffset`` for each entry.
  - Recalculates each SK1/SK2 ``gplVertexArr`` for the new sequential
    vertex allocation (using ``new_dest_abs`` passed from the caller).
  - Copies the original flush-index bytes from their current file
    position, appends them at the end of the new data region, and
    updates the ``flushIndArr`` header pointer accordingly.
  - Zero-pads the remainder of the original block size.
  The Blender exporter guarantees the edited block fits (edit size ≤
  original size) before writing ``SkinDataEdited`` to the JSON, so no
  overflow check is needed at the patcher level beyond an assertion.

On restore (--unpatch):
    skn_patch.restoreSKNPointers(skin_data)   ← gplVertexArr, memClr fields
    skn_patch.restoreSKNBlockInPlace(skin_data)  ← vertexArr, vertexCnt,
                                                   vertexOffset, data bytes

``restoreSKNPointers`` is idempotent and handles the dest-buffer side.
``restoreSKNBlockInPlace`` undoes the source-array pointer/count/data
changes made by ``patchSKNBlockInPlace``.
"""

import base64
import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import Hammerspace as _hs


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

def patchSKNSourceArrays(skin_data: dict, skin_data_edited: dict,
                         new_dest_abs: int) -> int:
    """Rebuild SK1/SK2/SKAcc source arrays in hammerspace for a new vertex layout.

    Called from ``patch_dat.writeExpandedMesh`` **after**
    ``patchSKNForNewDestBuffer`` when ``SkinDataEdited`` is present in the JSON
    (i.e. Blender exported with Hammerspace Mode and vertex count changed).

    What it does
    ------------
    1. Packs all bind-pose source data (XYZ+normal per vertex), weight arrays
       (SK2/SKAcc), and dest-index arrays (SKAcc) into a single ``sknsrc_<hex>``
       hammerspace chunk.
    2. Patches ``vertexArr``, ``weightArr``, and ``destArr`` pointer fields in
       each SK struct to point into the new chunk (relative to SKN.absolute).
    3. Rewrites ``vertexCnt`` from the edited data and clears ``vertexOffset``
       to 0 for SK1 and SK2 (``SkinDataEdited`` source data has no prefix).
    4. Overwrites the ``gplVertexArr`` values written by ``patchSKNForNewDestBuffer``
       with the correct sequential allocation for the new vertex counts:

           SK1[0] starts at byte 0 of the dest buffer
           SK1[1] starts at byte  SK1[0].VertexCnt * vert_size
           …
           SK2[0] starts after all SK1 vertices, and so on.

       gplVertexArr is stored as ``(new_dest_abs + byte_offset) - gpl_base``
       so that ``gplVertexArr // vertexSize`` gives the correct GPL-relative
       global vertex index (consistent with how the game and the export tools
       compute vertex indices from SK entries).

    SKAcc ``gplDestArr`` is left as-is (``patchSKNForNewDestBuffer`` already
    applies the correct delta shift).  ``destArr`` entries are expected to hold
    Blender-local vertex indices, which equal global vertex indices for
    single-submesh skinned models.

    Constraint: the number of SK1, SK2, and SKAcc entries must match between
    the original ``SkinData`` and ``SkinDataEdited``.  If they differ the
    function prints a warning and returns -1.

    Parameters
    ----------
    skin_data        : original SkinData dict (field offsets, original values)
    skin_data_edited : SkinDataEdited dict written by the Blender exporter
    new_dest_abs     : absolute file offset of the new dest buffer start
                       (= data_abs + offsets['verts'] from writeExpandedMesh)

    Returns
    -------
    Total SK1+SK2 vertex count on success, -1 on failure.
    """
    skn_abs  = int(skin_data['SKNOffset'], 16)
    gpl_base = int(skin_data['GplBaseOffset'], 16) if skin_data.get('GplBaseOffset') else 0
    cs       = _comp_size(skin_data['QuantizeInfo'])
    vs       = 6 * cs   # bytes per vertex (XYZ + normal, 6 components)

    new_sk1s    = skin_data_edited.get('SK1s',   [])
    new_sk2s    = skin_data_edited.get('SK2s',   [])
    new_skaccs  = skin_data_edited.get('SKAccs', [])
    orig_sk1s   = skin_data.get('SK1s',   [])
    orig_sk2s   = skin_data.get('SK2s',   [])
    orig_skaccs = skin_data.get('SKAccs', [])

    if len(new_sk1s) != len(orig_sk1s):
        print(f"  SKN source rebuild: SK1 count changed "
              f"({len(orig_sk1s)} \u2192 {len(new_sk1s)}). Unsupported \u2014 skipping.")
        return -1
    if len(new_sk2s) != len(orig_sk2s):
        print(f"  SKN source rebuild: SK2 count changed "
              f"({len(orig_sk2s)} \u2192 {len(new_sk2s)}). Unsupported \u2014 skipping.")
        return -1
    if len(new_skaccs) != len(orig_skaccs):
        print(f"  SKN source rebuild: SKAcc count changed "
              f"({len(orig_skaccs)} \u2192 {len(new_skaccs)}). Unsupported \u2014 skipping.")
        return -1

    n_sk1  = len(orig_sk1s)
    n_sk2  = len(orig_sk2s)

    # Build blob and record byte offsets for each section.
    # Layout: [sk1_src...] [sk2_src, sk2_wt ...] [skacc_src, skacc_dest, skacc_wt ...]
    blob    = bytearray()
    offsets = []     # blob byte offset for each section, in write order

    for e in new_sk1s:
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['BindPoseData'])))

    for e in new_sk2s:
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['BindPoseData'])))
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['WeightData'])))

    for e in new_skaccs:
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['BindPoseData'])))
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['DestIndexData'])))
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['WeightData'])))

    blob = bytes(blob)

    # Choose the primary pointer for writeNewMemoryChunk (SK1[0], else SK2[0], else SKAcc[0]).
    if orig_sk1s:
        primary_orig  = int(orig_sk1s[0]['VertexArrAbsolutePtr'], 16)
        primary_field = int(orig_sk1s[0]['VertexArrFieldOffset'],  16)
    elif orig_sk2s:
        primary_orig  = int(orig_sk2s[0]['VertexArrAbsolutePtr'], 16)
        primary_field = int(orig_sk2s[0]['VertexArrFieldOffset'],  16)
    else:
        primary_orig  = int(orig_skaccs[0]['VertexArrAbsolutePtr'], 16)
        primary_field = int(orig_skaccs[0]['VertexArrFieldOffset'],  16)

    chunk_name  = f'sknsrc_{skn_abs:x}'
    chunk_start = _hs.writeNewMemoryChunk(
        chunk_name, blob, primary_orig, primary_field, skn_abs)
    if chunk_start == -1:
        print(f"  ERROR: patchSKNSourceArrays \u2014 writeNewMemoryChunk failed for '{chunk_name}'.")
        return -1

    data_abs, _ = _hs.findChunk(chunk_name)
    if data_abs == -1:
        print(f"  ERROR: patchSKNSourceArrays \u2014 could not locate '{chunk_name}' after writing.")
        return -1

    # Patch all SK struct fields.
    cumulative = 0   # vertex count accumulated so far (SK1+SK2 sequential)

    with open(_hs.OUTPUT_DAT, 'r+b') as f:

        # --- SK1 entries ---
        for i, (orig, ed) in enumerate(zip(orig_sk1s, new_sk1s)):
            src_abs = data_abs + offsets[i]
            new_gpl = (new_dest_abs + cumulative * vs) - gpl_base
            cumulative += ed['VertexCnt']

            # vertexArr  (relative ptr to source data, stored as src_abs - skn_abs)
            f.seek(int(orig['VertexArrFieldOffset'], 16))
            f.write(struct.pack('>I', src_abs - skn_abs))

            # gplVertexArr  (sequential dest allocation)
            f.seek(int(orig['GplVertexArrFieldOffset'], 16))
            f.write(struct.pack('>I', new_gpl))

            # vertexCnt (+0x0A from vertexArr field) and vertexOffset (+0x0C, set to 0)
            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va + 0x0A); f.write(struct.pack('>H', ed['VertexCnt']))
            f.seek(va + 0x0C); f.write(b'\x00')

        # --- SK2 entries ---
        for i, (orig, ed) in enumerate(zip(orig_sk2s, new_sk2s)):
            src_abs = data_abs + offsets[n_sk1 + i * 2]
            wt_abs  = data_abs + offsets[n_sk1 + i * 2 + 1]
            new_gpl = (new_dest_abs + cumulative * vs) - gpl_base
            cumulative += ed['VertexCnt']

            f.seek(int(orig['VertexArrFieldOffset'],    16)); f.write(struct.pack('>I', src_abs - skn_abs))
            f.seek(int(orig['WeightArrFieldOffset'],    16)); f.write(struct.pack('>I', wt_abs  - skn_abs))
            f.seek(int(orig['GplVertexArrFieldOffset'], 16)); f.write(struct.pack('>I', new_gpl))

            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va + 0x10); f.write(struct.pack('>H', ed['VertexCnt']))
            f.seek(va + 0x12); f.write(b'\x00')

        # --- SKAcc entries ---
        # gplDestArr is left as-is (already updated by patchSKNForNewDestBuffer).
        # Only vertexCnt and the three data-array pointers are updated here.
        for i, (orig, ed) in enumerate(zip(orig_skaccs, new_skaccs)):
            src_abs  = data_abs + offsets[n_sk1 + n_sk2 * 2 + i * 3]
            dest_abs = data_abs + offsets[n_sk1 + n_sk2 * 2 + i * 3 + 1]
            wt_abs   = data_abs + offsets[n_sk1 + n_sk2 * 2 + i * 3 + 2]

            f.seek(int(orig['VertexArrFieldOffset'], 16)); f.write(struct.pack('>I', src_abs  - skn_abs))
            f.seek(int(orig['DestArrFieldOffset'],   16)); f.write(struct.pack('>I', dest_abs - skn_abs))
            f.seek(int(orig['WeightArrFieldOffset'], 16)); f.write(struct.pack('>I', wt_abs   - skn_abs))

            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va + 0x12); f.write(struct.pack('>H', ed['VertexCnt']))

    return cumulative   # SK1 + SK2 total (SKAcc does not add sequential dest slots)


def patchSKNForNewDestBuffer(skin_data: dict, new_dest_abs: int,
                             new_dest_size: int = None) -> None:
    """Patch all SKN fields that reference the runtime destination buffer.

    Call this after the vertex (position) destination buffer has been written
    to hammerspace so the SKN system knows where to write skinned positions.

    ``skin_data``     — the ``SkinData`` dict from the model-level .sluggie JSON.
    ``new_dest_abs``  — new absolute file offset of the start of the memClr region
                        (= absolute address of submesh 0's vertex blob section).
    ``new_dest_size`` — optional new memClr size; pass when vertex count changed.
                        Omit (or pass None) to keep the original size unchanged.

    Fields patched:
    - SKN +0x14 (memClrPtr)           → new_dest_abs (relative to SKN.absolute)
    - SKN +0x18 (memClrSze)           → new_dest_size  (only if provided)
    - SK1 +0x34 (gplVertexArr)        → original + delta, for every SK1 entry
    - SK2 +0x68 (gplVertexArr)        → original + delta, for every SK2 entry
    - SKAcc +0x38 (gplDestArr)        → original + delta, for every SKAcc entry

    The delta is ``new_dest_abs - original_dest_abs``.  Adding it to the raw
    gplVertexArr / gplDestArr values adjusts each SK entry's destination pointer
    to the same byte position within the new (hammerspace) dest buffer.
    """
    skn_abs      = int(skin_data['SKNOffset'], 16)
    old_dest_abs = int(skin_data['MemClrAbsolutePtr'], 16)
    delta        = new_dest_abs - old_dest_abs

    # Patch SKN.memClrPtr (relative to SKN.absolute)
    _hs.patchPointerField(
        int(skin_data['MemClrPtrFieldOffset'], 16),
        new_dest_abs,
        skn_abs
    )

    # Optionally update SKN.memClrSze (direct uint32 write, not a pointer)
    if new_dest_size is not None:
        with open(_hs.OUTPUT_DAT, 'r+b') as f:
            f.seek(int(skin_data['MemClrSzeFieldOffset'], 16))
            f.write(struct.pack('>I', new_dest_size))

    # Shift gplVertexArr / gplDestArr for all SK entries by the same delta.
    # These values are byte offsets relative to SKN.absolute that each SK
    # entry uses to locate its output slot(s) in the destination buffer.
    with open(_hs.OUTPUT_DAT, 'r+b') as f:
        for sk1 in skin_data.get('SK1s', []):
            new_val = sk1['GplVertexArrValue'] + delta
            f.seek(int(sk1['GplVertexArrFieldOffset'], 16))
            f.write(struct.pack('>I', new_val))

        for sk2 in skin_data.get('SK2s', []):
            new_val = sk2['GplVertexArrValue'] + delta
            f.seek(int(sk2['GplVertexArrFieldOffset'], 16))
            f.write(struct.pack('>I', new_val))

        for skacc in skin_data.get('SKAccs', []):
            new_val = skacc['GplDestArrValue'] + delta
            f.seek(int(skacc['GplDestArrFieldOffset'], 16))
            f.write(struct.pack('>I', new_val))


def restoreSKNPointers(skin_data: dict) -> None:
    """Restore all SKN pointer fields to their original (pre-hammerspace) values.

    Safe to call multiple times (idempotent — always writes the same original
    values).  Called during --unpatch for every submesh of a skinned model.

    Fields restored:
    - SKN +0x14 (memClrPtr)  → original value
    - SKN +0x18 (memClrSze)  → original size
    - SK1 +0x34 (gplVertexArr)   for every SK1
    - SK2 +0x68 (gplVertexArr)   for every SK2
    - SKAcc +0x38 (gplDestArr)   for every SKAcc

    If a ``sknsrc_<hex>`` hammerspace chunk was written by ``patchSKNSourceArrays``
    , it is erased and all source-array pointer fields, ``vertexCnt``,
    and ``vertexOffset`` are also restored to their original JSON values.
    """
    skn_abs          = int(skin_data['SKNOffset'], 16)
    original_dest_abs = int(skin_data['MemClrAbsolutePtr'], 16)

    # Restore SKN.memClrPtr
    _hs.patchPointerField(
        int(skin_data['MemClrPtrFieldOffset'], 16),
        original_dest_abs,
        skn_abs
    )

    with open(_hs.OUTPUT_DAT, 'r+b') as f:
        # Restore SKN.memClrSze
        f.seek(int(skin_data['MemClrSzeFieldOffset'], 16))
        f.write(struct.pack('>I', skin_data['MemClrSize']))

        # Restore gplVertexArr / gplDestArr for every SK entry
        for sk1 in skin_data.get('SK1s', []):
            f.seek(int(sk1['GplVertexArrFieldOffset'], 16))
            f.write(struct.pack('>I', sk1['GplVertexArrValue']))

        for sk2 in skin_data.get('SK2s', []):
            f.seek(int(sk2['GplVertexArrFieldOffset'], 16))
            f.write(struct.pack('>I', sk2['GplVertexArrValue']))

        for skacc in skin_data.get('SKAccs', []):
            f.seek(int(skacc['GplDestArrFieldOffset'], 16))
            f.write(struct.pack('>I', skacc['GplDestArrValue']))

    # if the sknsrc chunk exists, erase it and restore all source-array
    # pointer fields, vertexCnt, and vertexOffset to their original values.
    chunk_name = f'sknsrc_{skn_abs:x}'
    if _hs.findChunk(chunk_name)[0] != -1:
        _hs.removeChunk(chunk_name)   # erases chunk bytes; no pointer restored here

        with open(_hs.OUTPUT_DAT, 'r+b') as f:
            for orig in skin_data.get('SK1s', []):
                orig_src = int(orig['VertexArrAbsolutePtr'], 16)
                va = int(orig['VertexArrFieldOffset'], 16)
                f.seek(va);        f.write(struct.pack('>I', orig_src - skn_abs))
                f.seek(va + 0x0A); f.write(struct.pack('>H', orig['VertexCnt']))
                f.seek(va + 0x0C); f.write(bytes([orig['VertexOffset']]))

            for orig in skin_data.get('SK2s', []):
                orig_src = int(orig['VertexArrAbsolutePtr'],    16)
                orig_wt  = int(orig['WeightArrAbsolutePtr'],    16)
                va = int(orig['VertexArrFieldOffset'], 16)
                f.seek(va);                              f.write(struct.pack('>I', orig_src - skn_abs))
                f.seek(int(orig['WeightArrFieldOffset'], 16)); f.write(struct.pack('>I', orig_wt - skn_abs))
                f.seek(va + 0x10); f.write(struct.pack('>H', orig['VertexCnt']))
                f.seek(va + 0x12); f.write(bytes([orig['VertexOffset']]))

            for orig in skin_data.get('SKAccs', []):
                orig_src  = int(orig['VertexArrAbsolutePtr'],  16)
                orig_dest = int(orig['DestArrAbsolutePtr'],    16)
                orig_wt   = int(orig['WeightArrAbsolutePtr'],  16)
                va = int(orig['VertexArrFieldOffset'], 16)
                f.seek(va);                                    f.write(struct.pack('>I', orig_src  - skn_abs))
                f.seek(int(orig['DestArrFieldOffset'],   16)); f.write(struct.pack('>I', orig_dest - skn_abs))
                f.seek(int(orig['WeightArrFieldOffset'], 16)); f.write(struct.pack('>I', orig_wt   - skn_abs))
                f.seek(va + 0x12); f.write(struct.pack('>H', orig['VertexCnt']))

        print(f"  SKN source arrays restored (chunk '{chunk_name}' erased.).")


def patchSKNBlockInPlace(skin_data: dict, skin_data_edited: dict,
                         new_dest_abs: int) -> int:
    """Rewrite SKN source arrays in-place within the original SKN block.

    An alternative to ``patchSKNSourceArrays`` that does not use hammerspace.
    Source arrays are packed into the variable-data region that immediately
    follows the SK struct section within the SKN block, which is a contiguous
    self-contained allocation.  All internal pointers remain relative to
    ``SKNOffset`` so no external reference ever breaks.

    The Blender exporter's ``skn_block_size`` check guarantees that the
    edited layout fits within the original block before ``SkinDataEdited``
    is written to the JSON.

    Constraint: SK1, SK2, and SKAcc entry counts must be unchanged from the
    original ``SkinData``.  If they differ a warning is printed and -1 is
    returned.

    Parameters
    ----------
    skin_data        : original ``SkinData`` dict (field offsets and values)
    skin_data_edited : ``SkinDataEdited`` dict written by the Blender exporter
    new_dest_abs     : absolute file offset of the new dest buffer start
                       (= data_abs + offsets['verts'] from writeExpandedMesh)

    Returns
    -------
    Total SK1+SK2 vertex count on success, -1 on failure.
    """
    skn_abs  = int(skin_data['SKNOffset'], 16)
    gpl_base = int(skin_data['GplBaseOffset'], 16) if skin_data.get('GplBaseOffset') else 0
    cs       = _comp_size(skin_data['QuantizeInfo'])
    vs       = 6 * cs   # bytes per vertex (XYZ + normal, 6 components)

    new_sk1s    = skin_data_edited.get('SK1s',   [])
    new_sk2s    = skin_data_edited.get('SK2s',   [])
    new_skaccs  = skin_data_edited.get('SKAccs', [])
    orig_sk1s   = skin_data.get('SK1s',   [])
    orig_sk2s   = skin_data.get('SK2s',   [])
    orig_skaccs = skin_data.get('SKAccs', [])

    if len(new_sk1s) != len(orig_sk1s):
        print(f"  SKN in-place: SK1 count changed "
              f"({len(orig_sk1s)} \u2192 {len(new_sk1s)}). Unsupported \u2014 skipping.")
        return -1
    if len(new_sk2s) != len(orig_sk2s):
        print(f"  SKN in-place: SK2 count changed "
              f"({len(orig_sk2s)} \u2192 {len(new_sk2s)}). Unsupported \u2014 skipping.")
        return -1
    if len(new_skaccs) != len(orig_skaccs):
        print(f"  SKN in-place: SKAcc count changed "
              f"({len(orig_skaccs)} \u2192 {len(new_skaccs)}). Unsupported \u2014 skipping.")
        return -1

    n_sk1  = len(orig_sk1s)
    n_sk2  = len(orig_sk2s)

    # Variable-data region starts immediately after the struct section.
    data_start_rel = 0x24 + n_sk1 * 0x40 + n_sk2 * 0x74 + len(orig_skaccs) * 0x44
    data_start_abs = skn_abs + data_start_rel

    # Build source-array blob.  Layout matches skn_block_size / patchSKNSourceArrays:
    #   [sk1_src...] [sk2_src sk2_wt ...] [skacc_src skacc_dest skacc_wt ...]
    blob    = bytearray()
    offsets = []   # blob-relative byte offset for each section, in write order

    for e in new_sk1s:
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['BindPoseData'])))

    for e in new_sk2s:
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['BindPoseData'])))
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['WeightData'])))

    for e in new_skaccs:
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['BindPoseData'])))
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['DestIndexData'])))
        offsets.append(len(blob)); blob.extend(_align4(_to_bytes(e['WeightData'])))

    # Append flush-index array — content is unchanged, so copy from the
    # current file position before we overwrite anything.
    flush_ind_size = skin_data.get('FlushIndSize', 0)
    flush_abs_str  = skin_data.get('FlushIndAbsolutePtr')
    flush_blob_rel = None
    if flush_ind_size and flush_abs_str and flush_abs_str not in (None, 'null'):
        flush_blob_rel = len(blob)
        with open(_hs.OUTPUT_DAT, 'rb') as f_r:
            f_r.seek(int(flush_abs_str, 16))
            flush_bytes = f_r.read(flush_ind_size * 2)
        blob.extend(_align4(flush_bytes))

    # Zero-pad to fill the original variable-data region exactly.
    orig_var_size = skn_block_size(skin_data) - data_start_rel
    pad = orig_var_size - len(blob)
    if pad < 0:
        print(f"  SKN in-place: edited data ({len(blob)} B) exceeds "
              f"original variable region ({orig_var_size} B). "
              f"This should have been caught by the Blender exporter. Aborting.")
        return -1
    blob.extend(b'\x00' * pad)

    # Write the full data region and patch all SK struct fields.
    cumulative = 0   # vertex count accumulated across SK1+SK2 entries so far

    with open(_hs.OUTPUT_DAT, 'r+b') as f:
        f.seek(data_start_abs)
        f.write(bytes(blob))

        # Update flushIndArr header pointer if the flush array moved.
        if flush_blob_rel is not None:
            new_flush_rel = data_start_rel + flush_blob_rel
            f.seek(int(skin_data['FlushIndArrFieldOffset'], 16))
            f.write(struct.pack('>I', new_flush_rel))

        # --- SK1 entries ---
        for i, (orig, ed) in enumerate(zip(orig_sk1s, new_sk1s)):
            src_abs = data_start_abs + offsets[i]
            new_gpl = (new_dest_abs + cumulative * vs) - gpl_base
            cumulative += ed['VertexCnt']

            f.seek(int(orig['VertexArrFieldOffset'],    16)); f.write(struct.pack('>I', src_abs - skn_abs))
            f.seek(int(orig['GplVertexArrFieldOffset'], 16)); f.write(struct.pack('>I', new_gpl))

            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va + 0x0A); f.write(struct.pack('>H', ed['VertexCnt']))
            f.seek(va + 0x0C); f.write(b'\x00')   # vertexOffset = 0

        # --- SK2 entries ---
        for i, (orig, ed) in enumerate(zip(orig_sk2s, new_sk2s)):
            src_abs = data_start_abs + offsets[n_sk1 + i * 2]
            wt_abs  = data_start_abs + offsets[n_sk1 + i * 2 + 1]
            new_gpl = (new_dest_abs + cumulative * vs) - gpl_base
            cumulative += ed['VertexCnt']

            f.seek(int(orig['VertexArrFieldOffset'],    16)); f.write(struct.pack('>I', src_abs - skn_abs))
            f.seek(int(orig['WeightArrFieldOffset'],    16)); f.write(struct.pack('>I', wt_abs  - skn_abs))
            f.seek(int(orig['GplVertexArrFieldOffset'], 16)); f.write(struct.pack('>I', new_gpl))

            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va + 0x10); f.write(struct.pack('>H', ed['VertexCnt']))
            f.seek(va + 0x12); f.write(b'\x00')   # vertexOffset = 0

        # --- SKAcc entries ---
        # gplDestArr is left as-is (patchSKNForNewDestBuffer already shifted it).
        for i, (orig, ed) in enumerate(zip(orig_skaccs, new_skaccs)):
            src_abs  = data_start_abs + offsets[n_sk1 + n_sk2 * 2 + i * 3]
            darr_abs = data_start_abs + offsets[n_sk1 + n_sk2 * 2 + i * 3 + 1]
            wt_abs   = data_start_abs + offsets[n_sk1 + n_sk2 * 2 + i * 3 + 2]

            f.seek(int(orig['VertexArrFieldOffset'], 16)); f.write(struct.pack('>I', src_abs  - skn_abs))
            f.seek(int(orig['DestArrFieldOffset'],   16)); f.write(struct.pack('>I', darr_abs - skn_abs))
            f.seek(int(orig['WeightArrFieldOffset'], 16)); f.write(struct.pack('>I', wt_abs   - skn_abs))

            va = int(orig['VertexArrFieldOffset'], 16)
            f.seek(va + 0x12); f.write(struct.pack('>H', ed['VertexCnt']))
            # SKAcc has no vertexOffset field at this position.

    return cumulative   # SK1 + SK2 total vertex count


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
        with open(_hs.OUTPUT_DAT, 'rb') as f_r:
            f_r.seek(int(flush_abs_str, 16))
            flush_bytes = f_r.read(flush_ind_size * 2)
        blob.extend(_align4(flush_bytes))

    # Zero-pad to fill the original variable-data region exactly.
    orig_var_size = skn_block_size(skin_data) - data_start_rel
    pad = orig_var_size - len(blob)
    if pad < 0:
        print(f"  SKN in-place resize: edited data ({len(blob)} B) exceeds "
              f"original variable region ({orig_var_size} B). Aborting.")
        return -1
    blob.extend(b'\x00' * pad)

    total_verts = 0

    with open(_hs.OUTPUT_DAT, 'r+b') as f:
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
    with open(_hs.OUTPUT_DAT, 'r+b') as f:
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
    with open(_hs.OUTPUT_DAT, 'r+b') as f:
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

    with open(_hs.OUTPUT_DAT, 'r+b') as f:

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

