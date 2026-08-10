# Alignment Investigation

Empirical analysis of byte-alignment requirements for model arrays in the
Mario Super Sluggers format. Re-run on 2026-08-10 with
`SluggiesTools/Debug/alignment_verify.py`: **1,422 model blocks**, including
**337 skinned blocks**.

---

## Summary Table

| Data Type | Location | Count Analyzed | Required Alignment | Evidence |
|-----------|----------|---------------:|-------------------:|----------|
| **Skinned position buffer** | GPL section | 189 | **32-byte** | 100% at mod32=0 |
| **Prim list data** | GPL section | 9,465 | **32-byte** | 100% at mod32=0 |
| **SKN source (bind-pose) arrays** | SKN section | 14,436 | **32-byte** | 100% at mod32=0 |
| **SKN weight arrays** | SKN section | 9,703 | **32-byte** | 100% at mod32=0 |
| **SKN dest-index arrays** | SKN section | 3,323 | **32-byte** | 100% at mod32=0 |
| **SKN flush-index array** | SKN section | 173 | **32-byte** | 100% at mod32=0 |
| Non-skinned position buffer | GPL section | 3,055 | None (byte) | mod32 values span 0–28; mod4 spans 0–3 |
| Color data | GPL section | 3,244 | None (byte) | All mod32 values 0–31 present |
| Normal data (standalone) | GPL section | 3,055 | None (byte) | All mod32 values 0–31 present |
| UV data | GPL section | 4,562 | None (byte) | All mod32 values 0–31 present |
| DOLayout blob start | GPL section | 3,244 | None (byte) | mod32 spans 0–28; mod4 spans 0–3 |
| SKN memClrPtr | Position-data-relative | 165 non-zero | None* | mod32 = {0, 4, 8, 12, 20} |

\* memClrSize is always a multiple of 32 (consistent with 32-byte chunk clearing),
but memClrPtr itself is not always 32-aligned.  See discussion below.

---

## Detailed Findings

### GPL Section

#### Skinned Position Data (cc=6, interleaved pos+normal)

- **189 / 189 submeshes are 32-byte aligned (mod32 = 0)**
- This is the only GPL data array with a strict alignment requirement.
- **Reason:** The SKN deformer writes transformed vertices to this buffer
  using the PowerPC `dcbz` instruction, which zeroes an entire 32-byte cache
  line.  `dcbz` faults on non-aligned addresses.

#### Non-Skinned Position Data (cc=3)

- 3,055 submeshes analyzed.
- mod32 values observed: {0, 4, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 27, 28}
- mod4 values observed: {0, 1, 2, 3}
- **No alignment requirement at all** — data appears at arbitrary byte offsets.
- These buffers are read-only (GPU vertex fetch); the GX hardware has no
  alignment constraint for indexed attribute arrays.

#### Color, Normal, UV Data

- All three types show every possible mod32 value (0–31) across the dataset.
- mod4 values include {0, 1, 2, 3} — not even 4-byte aligned.
- **No alignment requirement.**
- These are all GPU-read arrays accessed via GX indexed attribute fetch.

#### Prim List (Display List) Data

- **9,465 / 9,465 prim lists are 32-byte aligned (mod32 = 0)**
- mod4 = {0} — also trivially 4-byte aligned.
- **Reason:** GX display lists (prim lists) must be 32-byte aligned because
  the GPU's command processor reads them via DMA in 32-byte bursts.  This is
  a documented Nintendo GX hardware requirement.

#### DOLayout Blob Start Offsets

- mod32 values: {0, 4, 8, 9, 10, ...} — not aligned.
- mod4 values: {0, 1, 2, 3} — not even 4-byte aligned.
- The blob start (DOLayout struct) has no alignment requirement; it contains
  only pointer/count header fields read by the CPU.

---

### SKN Section

#### Source (Bind-Pose), Weight, and DestIndex Arrays

All three array types in the SKN section are **always 32-byte aligned**:

| Array Type | Count | mod32 values |
|-----------|------:|:-------------|
| Source (bind-pose) | 14,436 | {0} |
| Weight | 9,703 | {0} |
| DestIndex | 3,323 | {0} |
| Flush Index | 173 | {0} |

- **Reason:** The CPU skinning deformer uses `dcbz` (or paired-single loads
  that benefit from cache-line alignment) when processing these arrays.
  The Broadway CPU's L1 data cache has 32-byte lines; misaligned access to
  these hot-path arrays would cause performance degradation or faults.

#### memClrPtr

- 165 non-zero ranges analyzed.
- mod32 values observed: {0, 4, 8}
- memClrSize is **always** a multiple of 32.
- **memClrPtr does NOT require 32-byte alignment.**
- It points to a small tail region within the skinned position buffer
  (vertices not covered by any SK deformer entry) that gets zeroed each
  frame.  The clear likely uses a regular store loop rather than `dcbz`.
- The position buffer START (where `dcbz` is used) is still 32-aligned.

---

## Implications for BuildGPLMeshData

The current builder applies 32-byte alignment to:
1. Skinned position data → **REQUIRED** (dcbz target)
2. Blob (DOLayout) boundaries → needed so that DOLayout-relative prim list
   offsets are also 32-aligned in GPL-absolute terms
3. Prim lists → explicitly 32-byte aligned within each blob (GX DMA)

Non-skinned position data, color, normal, and UV arrays are placed with **no
alignment padding**, matching the original game layout.

### Current Builder Behavior (minimal-overhead)

```
Skinned position buffer:  32-byte aligned  (CRITICAL — dcbz)
Prim list data:           32-byte aligned  (CRITICAL — GX DMA)
Blob (DOLayout) starts:   32-byte aligned  (enables prim list alignment)
All other GPL data:       No alignment     (matches original)
SKN source/weight/dest:   32-byte aligned  (CRITICAL — CPU deformer)
SKN flush index:          32-byte aligned  (matches original)
```

### Alignment is not sufficient for unchanged SKN layout

The Peach in-game isolation test on 2026-08-10 showed that valid 32-byte
alignment, bounds, counts, and byte-identical decoded arrays were not enough.
Interleaving each SK2 source with its weights caused broad vertex explosions.
Using the donor-observed order (all SK2 sources, flush list, then all SK2
weights) reduced the explosions, and restoring one additional 0x20-byte donor
gap after SK1[22] eliminated them. The resulting rebuilt model block was
byte-identical to the donor.

For unchanged topology, preserve `array_absolute_ptr - SKNOffset` for every
SKN source, weight, destination-index, and flush array rather than recomputing a
minimal aligned layout. Canonical packing remains necessary for edited topology
but requires separate in-game validation.

Size overhead vs. original: **+80 bytes** for the tiny_kong test model
(4 submeshes).  The overhead comes from blob boundary padding and the skinned
position pre-alignment within the first blob.

---

## Methodology

Analysis performed by parsing all 1,260 `.sluggie` files' `ModelOffset` to
locate model blocks in `dt_na.dat`, then reading GPL and SKN binary headers
to extract absolute offsets of every data array.  Each offset was checked
for `mod 32` and `mod 4` alignment.  Script: `_tmp_alignment.py`.
