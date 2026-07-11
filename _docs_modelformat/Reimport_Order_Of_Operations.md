# Order of Operations 

## vertex & face count changes

### Truly independent (no vertex/face references):

- Bone Data (ACT) — only contains the hierarchy tree, per-bone SRT poses, and animation track links. Nothing points into vertex/face data, so a full vertex/face count change leaves it completely untouched.
- Texture Data — just raw image pixels and palette entries. No vertex or face indices anywhere in this section. (The draw list in the Mesh section references texture indices, but the texture section itself is self-contained.)
- shaders/display_state - there is currently little value in maniplulating these

All data — even data that will not be different in hammerspace — MUST come from the .sluggies file only; the importer must not read model data back from the original dt_na.dat in 1_Input. The sluggies schema must carry everything required. (The only exceptions are the raw TPL texture payload and the ACT skeleton section, which are both cloned until Milestone 3.)

### Needs partial or full recalculation:

Mesh Data (GEO + GPL)
- Vertex position/normal arrays: Number of positions count field + raw buffer size
Color array: same
- UV coord arrays: Number of coords count + buffer size
- Draw list (GPL): every triangle command contains per-loop attribute indices (position slot, UV slot, color slot). All of these must be rebuilt from scratch since both the vertex count and the index values themselves change.

Skinning Data (SKN)
- SK1/SK2: vertexCnt fields, source arrays, and crucially gplVertexArr (the dest byte-offset into the runtime vertex buffer). If any vertex's position in the global dest buffer shifts (because earlier vertices were inserted/removed), all subsequent gplVertexArr values are wrong.
- SKAcc: same gplDestArr pointer, plus destIndexData if the dest-slot numbering shifts.
- memClr pointer and size in the SKN header: describes the range of the runtime vertex buffer to zero each frame — needs updating if the skinned vertex range grows.
- Flush index array: may reference specific dest-slot indices.


## Main.dol

How the main.dol Table Works
The DOL contains a flat array of 48-byte entries (12 × 4-byte words each). The dirs table in export.py parses it, and the layout per entry is:

DOL file offset	Field	Notes
file_ptr + 0	DAT_FNAME_PTR (0x8067f658)	Wii RAM pointer to the string "dt_na.dat"
file_ptr + 4	len_en	Byte length of the model block
file_ptr + 8	offset_en	Byte offset within dt_na.dat
file_ptr + 20	len_sp	Spanish variant
file_ptr + 24	offset_sp	
file_ptr + 36	len_fr	French variant
file_ptr + 40	offset_fr	
All three language variants point to the same offset in practice (the code comments this out and uses only 'en').

1. The new model block is written at some offset X >= BASE_SIZE within the extended dt_na.dat
2. Patching main.dol means writing X into file_ptr + 8 (and the length into file_ptr + 4)
3. Everything inside the model block uses offsets relative to X — the GPL, ACT, TEX, SKN section pointers in the model header are all block-internal offsets, so the block is fully self-contained and relocatable

The entire mechanism is: open file by name → seek to offset → read length bytes.




## Dependencies / Order of Operations

sluggies JSON
    │
    ▼
[1] vertex arrays ──────────────────────────────────────────┐
    │                                                        │
    ▼                                                        │
[2] import the sluggies draw lists (PrimListDataEdited)     │
    │                                                        │
    ▼                                                        ▼
[3] GPL section (full layout, size known)      [4] SKN section
    │                                                        │
    ▼                                                        │
[5] ACT (verbatim copy — skeleton exc., until Milestone 3)  │
[6] TEX (verbatim copy — TPL exception, until Milestone 3)  │
    │                                                        │
    └──────────────────┬─────────────────────────────────────┘
                       ▼
        [7] file header (incl. ptr6/ptr7/ptr8)
                       │
                       ▼
                  [8] full block
                       │
              [9] find hammerspace slot
                       │
             [10] write to dt_na.dat
                       │
             [11] patch main.dol

Notes:
- [2] means importing the draw lists provided by the Blender exporter (PrimListDataEdited) — the importer does not build draw lists itself, it only lays them out. Contract (refined 2026-07-09): edited arrays are used whenever they are IN-PLACE COMPATIBLE with the original (same byte length for GPL arrays / same vertex count for SK payloads — covers position-only edits without a prim-list rebuild). Edited arrays in EXPANDED per-loop form (UVChannelDataEdited) are only consumable together with PrimListDataEdited; without it the importer uses the original compact array and prints a notice. Topology changes (different counts) REQUIRE PrimListDataEdited.
- Structural reskin (weights moved between vertex groups, SK entries added/removed/resized, total vertex count unchanged): supported WITHOUT PrimListDataEdited. When SkinDataEdited's entry sets or per-entry vertex counts differ from SkinData, the SK entry lists are built wholly from SkinDataEdited (it carries recomputed GplVertexArrValue/GplDestArrValue, counts, payloads, dest indices); memClr and the GPL scratch reservation are recalculated from the new coverage. The flush index array is still copied from the original (rebuild = Milestone 2).
- [7] The model block header also contains ptr6/ptr7/ptr8, pointing to trailing sub-sections that live after the SKN section. Their data must come from the .sluggies file, be laid out after SKN, and the three header pointers must be recomputed for the new layout.


### GPL order of implementation

IMPORTANT — SK runtime scratch space: for skinned models, the CPU skinning deformer writes vertex slots that can extend BEYOND the last stored vertex of submesh 0's position array (163/325 original skinned models; verified by `SluggiesTools/Debug/scratch_space_probe.py`). The GPL builder must reserve `max(position_array_length, max_SK_write_end)` bytes for submesh 0's position buffer region before placing the next data array, or the per-frame memClr/SKAcc writes overwrite it at runtime (symptom: corrupted vertex colors / partial transparency). `max_SK_write_end` = max over SK1/SK2 of `gplVertexArr + vertexOffset + vertexCnt × stride` and over SKAcc of `gplDestArr + (max(destIndices) + 1) × stride`, plus the memClr region end.

GPL Header
  └─ GEO Descriptor (needs DOLayout ptr, name ptr)
       └─ DOLayout (needs all 5 sub-header offsets)
            ├─ Position/Color/Normal/UV Headers (need → raw data offsets)
            │    └─ [raw vertex / UV / color / normal bytes]  ← no deps
            └─ Display Header (needs → display state array offset)
                 └─ Display State ×N (needs → prim list offsets)
                      └─ [raw prim list bytes]  ← no deps


### SKN implementation plan

#### Section layout reference (from skn_section.html)

```
SKN Header (0x24 bytes)
  +0x00  numSK1       uint16
  +0x02  numSK2       uint16
  +0x04  numSKAcc     uint16
  +0x06  quantize_info uint8  (high nibble must be 0)
  +0x07  padding
  +0x08  sk1Ptr       uint32  SKN-relative → SK1 struct array
  +0x0c  sk2Ptr       uint32  SKN-relative → SK2 struct array
  +0x10  skAccPtr     uint32  SKN-relative → SKAcc struct array
  +0x14  memClrPtr    uint32  GPL-relative → start of runtime dest vertex buffer to zero
  +0x18  memClrSize   uint32  byte length of region to zero each frame
  +0x1c  flushIndPtr  uint32  SKN-relative → flush index array
  +0x20  flushIndSize uint32  number of flush indices

SK1 struct (0x40 bytes each)
  +0x00  matrix placeholder  0x30 bytes  (zeroed in file; runtime fills)
  +0x30  srcArrPtr    uint32  SKN-relative → source bind-pose pos/normal array
  +0x34  gplVertexArr uint32  position-data-relative → byte offset from the start of submesh 0's position array (verbatim for unchanged geometry; rebuild from the vertex → SK-entry mapping when vertex data changes)
  +0x38  boneIndex    uint16
  +0x3a  vertexCnt    uint16
  +0x3c  vertexOffset uint8   byte prefix to skip at start of source array
  +0x3d  padding ×3

SK2 struct (0x74 bytes each)
  +0x00  matrix placeholder  0x60 bytes  (two matrices, zeroed)
  +0x60  srcArrPtr    uint32  SKN-relative
  +0x64  weightArrPtr uint32  SKN-relative → per-vertex weight pair array (vertexCnt × 2 uint8)
  +0x68  gplVertexArr uint32  position-data-relative → byte offset from the start of submesh 0's position array (verbatim for unchanged geometry; rebuild when vertex data changes)
  +0x6c  boneIndex1   uint16
  +0x6e  boneIndex2   uint16
  +0x70  vertexCnt    uint16
  +0x72  vertexOffset uint8
  +0x73  padding

SKAcc struct (0x44 bytes each)
  +0x00  matrix placeholder  0x30 bytes  (zeroed)
  +0x30  srcArrPtr      uint32  SKN-relative → source bind-pose array
  +0x34  destIdxArrPtr  uint32  SKN-relative → uint16 dest-index array
  +0x38  gplDestArr     uint32  position-data-relative → base output vertex for index arithmetic (same semantics as gplVertexArr; verbatim for unchanged geometry)
  +0x3c  weightArrPtr   uint32  SKN-relative → per-vertex weight array (vertexCnt uint8)
  +0x40  boneIndex      uint16
  +0x42  vertexCnt      uint16

Variable data region (immediately after all structs, in this order):
  SK1[0] source data (4-aligned) … SK1[n] source data
  SK2[0] source data (4-aligned), SK2[0] weight data (4-aligned) … per SK2
  SKAcc[0] source (4-aligned), SKAcc[0] destIdx (4-aligned), SKAcc[0] weights (4-aligned) … per SKAcc
  flush index array (uint16 × flushIndSize, 4-aligned)
```

#### gplVertexArr / gplDestArr semantics (corrected)

These fields are POSITION-DATA-RELATIVE byte offsets (relative to the start of submesh 0's position array), NOT GPL-section-relative — see skn_section.html. They do not change when the model block is relocated or when the GPL section is rebuilt with unchanged vertex data, so for the Milestone 1 round trip they are taken verbatim from the .sluggies file. When vertex data changes (Milestone 2), they must be rebuilt from the vertex → SK-entry mapping instead.

memClrPtr is ALSO position-data-relative (empirically verified — see "memClr range" below), so no field in the SKN section depends on the GPL layout at all; the SKN section can be built independently of the GPL build.

#### memClr range (empirically verified formula)

The game zeros the accumulation-only region of the GPL position buffer once per frame so that SKAcc accumulations start from zero. Slots written by SK1/SK2 are plain overwrites and need no clearing.

Verified against all 325 skinned models (324 exact matches — `SluggiesTools/Debug/memclr_probe3.py`); the earlier formulas in this doc and in skn_section.html were both wrong:

- Slot addresses are position-data-relative byte offsets (same base as `gplVertexArr`):
  - SK1/SK2 write slots: `gplVertexArr + vertexOffset + k × vertex_stride` (k = 0..vertexCnt−1)
  - SKAcc write slots: `gplDestArr + destIndex[k] × vertex_stride`
  - `vertex_stride = component_size(quantize_info) × 6` (component_size = 4 for float formats — high nibble 4/7/0xa — else 2)
- `only_acc` = SKAcc slots NOT covered by any SK1/SK2 slot
- `memClrPtr` = min(only_acc), POSITION-DATA-relative (NOT GPL-section-relative!) — layout-independent, needs no recomputation on relocation/GPL rebuild
- `memClrSize` = align32_up(max(only_acc) + vertex_stride − memClrPtr)
- Both 0 when only_acc is empty

#### Dependency graph

```
parsed.skinning (sluggies JSON)
  │
  ├─[SKN-1] Variable-data layout pass  (no deps, fully standalone)
  │    Take bind_pose_data, weight_data, dest_index_data AND the flush index
  │    array data from the .sluggies file only (prefer *Edited variants).
  │    No reads from the INPUT dat — the sluggies schema must carry the
  │    flush index array data.
  │    Milestone 2: rebuild the flush index array and dest_index_data when
  │    vertex counts / dest slots change.
  │    Lay out in fixed order: SK1 srcs → SK2 src+wt → SKAcc src+destIdx+wt → flush.
  │    Record SKN-relative offset for every sub-array.
  │
  ├─[SKN-2] SK1 / SK2 / SKAcc struct headers  (depends on SKN-1)
  │    Pack all fields except gplVertexArr / gplDestArr (leave those as 0).
  │    srcArrPtr, weightArrPtr, destIdxArrPtr  ← from SKN-1 offsets.
  │    boneIndex, vertexCnt, vertexOffset      ← verbatim from parsed.
  │    Matrix placeholder bytes                ← zero (runtime fills).
  │
  ├─[SKN-3] gplVertexArr / gplDestArr  (depends on SKN-2)
  │    Position-data-relative — take verbatim from the .sluggies file for
  │    unchanged geometry (Milestone 1). No relocation math is needed and
  │    no INPUT dat reads are allowed. When vertex data changes
  │    (Milestone 2), rebuild them from the vertex → SK-entry mapping.
  │    Patch the fields in the struct bytes produced by SKN-2.
  │
  └─[SKN-4] memClrPtr / memClrSize + SKN header + full assembly  (depends on SKN-3)
       Compute only_acc = SKAcc slots − SK1/SK2 slots (position-data-relative).
       memClrPtr = min(only_acc)  (position-data-relative — no GPL layout dependency).
       memClrSize = align32_up(max(only_acc) + stride − memClrPtr).
       (See "memClr range" above — empirically verified formula.)
       Write 0x24-byte SKN header with all counts and pointers.
       Concatenate: header + SK1 structs + SK2 structs + SKAcc structs + variable data.
```
