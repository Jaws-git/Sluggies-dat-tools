"""skn_patch.py — Phase 4: SKN/SK1/SK2/SKAcc pointer patching for skinned meshes.

All pointer fields inside the SKN section are uint32 BE values relative to
SKN.absolute (the ``SKNOffset`` stored in the JSON ``SkinData`` block).

Usage from patch_dat.py
-----------------------
On expand (dest buffer moved to hammerspace):
    skn_patch.patchSKNForNewDestBuffer(skin_data, new_dest_abs)
    # new_dest_abs = absolute file offset where the vertex destination buffer
    # now lives (i.e., the vertex section of the hammerspace blob for submesh 0).

On restore (--unpatch):
    skn_patch.restoreSKNPointers(skin_data)

Phase 6 note
------------
Source arrays (SK1/SK2/SKAcc ``vertexArr``, ``weightArr``, SKAcc ``destArr``)
are NOT moved in Phase 4 — they remain at their original file locations.
Phase 6 will extend these functions to also relocate source data when bone
weights and vertex assignments are changed in Blender.
"""

import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import HammerspaceHelpers as _hs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
