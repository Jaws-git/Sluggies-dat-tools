# Hammerspace Agenda

Mario Super Sluggers is a Wii game (not GameCube)
Apparently it still uses the GX format.

## concept
In order to change game models beyond what the in-place patching can provide, a concept called "Hammerspace" will be introduced.

1. First a buffer of user-specified length is created at the end of the dt_na.dat file, initially filled with 0-bytes.
2. Next, a complete model data block is created from the information present in a selected .sluggies file, following the established schema in overview.html
3. Following some consistency checks, the new data block is then written to the hammerspace buffer as one contiguous unit.
4. Lastly, the model pointer in main.dol is patched to point to the start of the new hammerspace section. Models can be identified by file_index/chunknumber in the sluggies file.

The user will then manually copy-replace the original dt_na.dat and main.dol in their game files and run emulation via dolphin.

All of the hammerspace-specific code should live in the .\Hammerspace\ folder. Some code duplication is acceptable. In-Place patching must not be affected by hammerspace changes!

Some helper methods already exist in HammerspaceHelper.py but may need to be updated to fit the new concept.

## not supported

- texture patching will not be supported (alternative via dolphin custom texture loader exisits)
- skeleton hierarchy and animation patching is out of scope for now


## vertex & face count changes

### Truly independent (no vertex/face references):

- Bone Data (ACT) — only contains the hierarchy tree, per-bone SRT poses, and animation track links. Nothing points into vertex/face data, so a full vertex/face count change leaves it completely untouched.
- Texture Data — just raw image pixels and palette entries. No vertex or face indices anywhere in this section. (The draw list in the Mesh section references texture indices, but the texture section itself is self-contained.)
- shaders/display_state - there is currently little value in maniplulating these

All data that will not be different in hammerspace anyway should be copied from the original dt_na.dat file in the 1_Input folder.

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
[2] draw lists                                              │
    │                                                        │
    ▼                                                        ▼
[3] GPL section (full layout, size known)      [4] SKN section
    │                                                        │
    ▼                                                        │
[5] ACT (verbatim copy)                                     │
[6] TEX (verbatim copy)                                     │
    │                                                        │
    └──────────────────┬─────────────────────────────────────┘
                       ▼
                  [7] file header
                       │
                       ▼
                  [8] full block
                       │
              [9] find hammerspace slot
                       │
             [10] write to dt_na.dat
                       │
             [11] patch main.dol


### GPL order of implementation

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
  +0x34  gplVertexArr uint32  GPL-relative → first output vertex byte in pos array  ← MUST RECALCULATE
  +0x38  boneIndex    uint16
  +0x3a  vertexCnt    uint16
  +0x3c  vertexOffset uint8   byte prefix to skip at start of source array
  +0x3d  padding ×3

SK2 struct (0x74 bytes each)
  +0x00  matrix placeholder  0x60 bytes  (two matrices, zeroed)
  +0x60  srcArrPtr    uint32  SKN-relative
  +0x64  weightArrPtr uint32  SKN-relative → per-vertex weight pair array (vertexCnt × 2 uint8)
  +0x68  gplVertexArr uint32  GPL-relative → first output vertex byte  ← MUST RECALCULATE
  +0x6c  boneIndex1   uint16
  +0x6e  boneIndex2   uint16
  +0x70  vertexCnt    uint16
  +0x72  vertexOffset uint8
  +0x73  padding

SKAcc struct (0x44 bytes each)
  +0x00  matrix placeholder  0x30 bytes  (zeroed)
  +0x30  srcArrPtr      uint32  SKN-relative → source bind-pose array
  +0x34  destIdxArrPtr  uint32  SKN-relative → uint16 dest-index array
  +0x38  gplDestArr     uint32  GPL-relative → base output vertex for index arithmetic  ← MUST RECALCULATE
  +0x3c  weightArrPtr   uint32  SKN-relative → per-vertex weight array (vertexCnt uint8)
  +0x40  boneIndex      uint16
  +0x42  vertexCnt      uint16

Variable data region (immediately after all structs, in this order):
  SK1[0] source data (4-aligned) … SK1[n] source data
  SK2[0] source data (4-aligned), SK2[0] weight data (4-aligned) … per SK2
  SKAcc[0] source (4-aligned), SKAcc[0] destIdx (4-aligned), SKAcc[0] weights (4-aligned) … per SKAcc
  flush index array (uint16 × flushIndSize, 4-aligned)
```

#### Key challenge: gplVertexArr / gplDestArr recalculation

These fields are GPL-section-relative byte offsets pointing to where each SK entry writes its transformed vertex data into the GPL position array at runtime. They must be updated whenever the GPL section is rebuilt because the position array moves to a new GPL-relative address.

Recalculation formula (per SK entry, per submesh i):
```
within_array_offset = gplVertexArr_original − old_pos_gpl_off[i]
new_gplVertexArr    = new_pos_gpl_off[i] + within_array_offset
```

`new_pos_gpl_off[i]` — parse the new GPL bytes produced by BuildGPLMeshData:
  read GEO_DESC_OFF + i×8 → DOLayoutPtr (uint32 BE at +0x00)
  read gpl_bytes at DOLayoutPtr + 0x00 → posHeaderOff (DOLayout→PosHeader offset)
  read gpl_bytes at DOLayoutPtr + posHeaderOff + 0x00 → pos_data_off (PosHeader rawPtr)
  result: DOLayoutPtr + posHeaderOff + pos_data_off

`old_pos_gpl_off[i]` — same parse on INPUT dat original GPL, using parsed.model_offset:
  GPL section starts at model_offset + 0x20 (confirmed by model block header at +0x04)
  apply the same GEO desc → DOLayout → PosHeader → rawPtr chain

#### memClr range

The game zeros the entire SK output region of the GPL position buffer once per frame so that SKAcc accumulations start from zero.

- `memClrPtr` = smallest `gplVertexArr` across all SK1/SK2/SKAcc entries (the first byte of the output range)
- `memClrSize` = (largest `gplVertexArr + vertexCnt × vertex_stride`) − memClrPtr, where `vertex_stride = _vb_comp_size(quantize_info) × 6` (pos/normal interleaved, so 6 components)

#### Dependency graph

```
parsed.skinning (sluggies JSON)
  │
  ├─[SKN-1] Variable-data layout pass  (no deps, fully standalone)
  │    Copy bind_pose_data, weight_data, dest_index_data from parsed entries
  │    (prefer *Edited variants). Copy flush index data verbatim from INPUT
  │    dat at parsed.skinning.flush_ind_absolute_ptr.
  │    Lay out in fixed order: SK1 srcs → SK2 src+wt → SKAcc src+destIdx+wt → flush.
  │    Record SKN-relative offset for every sub-array.
  │
  ├─[SKN-2] SK1 / SK2 / SKAcc struct headers  (depends on SKN-1)
  │    Pack all fields except gplVertexArr / gplDestArr (leave those as 0).
  │    srcArrPtr, weightArrPtr, destIdxArrPtr  ← from SKN-1 offsets.
  │    boneIndex, vertexCnt, vertexOffset      ← verbatim from parsed.
  │    Matrix placeholder bytes                ← zero (runtime fills).
  │
  ├─[SKN-3] gplVertexArr / gplDestArr recalculation  (depends on SKN-2 + gpl_bytes)
  │    Parse old_pos_gpl_off[i] from INPUT dat (model_offset + 0x20 + descriptor chain).
  │    Parse new_pos_gpl_off[i] from gpl_bytes (same descriptor chain).
  │    Patch gplVertexArr / gplDestArr fields in struct bytes produced by SKN-2.
  │
  └─[SKN-4] memClrPtr / memClrSize + SKN header + full assembly  (depends on SKN-3)
       Compute memClrPtr = min(all new gplVertexArr values).
       Compute memClrSize from max SK output range end − memClrPtr.
       Write 0x24-byte SKN header with all counts and pointers.
       Concatenate: header + SK1 structs + SK2 structs + SKAcc structs + variable data.
```

#### Additional notes

- Skinned meshes always write into submesh 0's position array. Confirm: the first SK entry's `gplVertexArr_original` must fall within `[old_pos_gpl_off[0], old_pos_gpl_off[0] + pos_data_len)`.
- The flush index array content is preserved verbatim (same as ACT/TEX copy approach); only its SKN-relative pointer in the header changes.
- SKAcc duplicate-destination entries (twist/blend deformers) require no special handling — `dest_index_data` is preserved verbatim, and the recalculation only touches the `gplDestArr` base pointer.
- Matrix placeholder bytes (0x30 or 0x60 per struct) are always zero in the file; the runtime transforms fill them each frame.
- `vertexOffset` is preserved verbatim from `parsed.skinning.sk1s[i].vertex_offset` etc.



## the problems with vertex count changes

Here's the full chain of what breaks:

Why topology changes are fundamentally incompatible with the current approach
The game format imposes a hard constraint: SK1/SK2 entries use gplVertexArr as a contiguous-range start. For vertex slot s to be skinned by SK1 bone B, the face index must equal exactly gplVertexArr_B / stride + i for some i in [0, vertexCnt). The slot written and the slot referenced by the face must be the same number.

What Blender does when you add vertices: New vertices are appended at the end of the mesh — indices 2415, 2416, 2417. FacesDataEdited emits those numbers as face indices. The exported VertexBufferDataEdited stores vertex 2415's data at byte position 2415 * stride in the buffer.

What the SK1 entry would do: With GplVertexArrValue = 1200 (bone 5's original start, slot 100) and VertexCnt = 53, the game writes source verts 0..52 sequentially to dest slots 100..152 — not to slot 2415. The face referencing index 2415 reads whatever garbage is at that position.

So the two things are disconnected: the face says "look at slot 2415", the SK entry writes to "slots 100..152". My fix of copying the original GplVertexArrValue is correct for deformation-only edits (which was its intent) but silently wrong for topology changes.

Recomputing GplVertexArrValue from scratch doesn't save you either — even if you compute min(global_vertex_index) from the group, that gives you 100 again (the original vertices start there), and the new vertex at 2415 still won't be covered.

What would actually be needed
A vertex reordering pass at export time, coordinated across all four data types:

Data	What changes
VertexBufferDataEdited	Output vertices sorted by SK group membership, not Blender vertex index
FacesDataEdited	Rewritten with the new reordered positions
UV / color channel data	All slot arrays reordered to match
GplVertexArrValue	Computed from scratch from the new contiguous ranges
Concretely, for bone 5 with original verts 100..149 + new verts 2415..2417:

Assign them new slots 100..152 in the output buffer (contiguous)
Output vertex buffer with vertex 2415's data at byte position 150 * stride
Rewrite any face index 2415 → 150
Set GplVertexArrValue = 100 * stride, VertexCnt = 53
This touches encode_vertex_buffer_edited, encode_uv_channel_edited, and the face encoding — all need to operate on the same vertex index remapping table.

Current state / practical limit
Edit type	Works?
Vertex position/normal changes	✅ fully supported
Weight changes between existing vertices	✅ supported
Removing faces + trailing verts (per bone)	✅ handled by trailing trim in encode_skin_hammerspace
Removing a vert from the middle of a bone's range	⚠️ only correct if the face is also removed and you don't care about stale data at that slot
Adding new vertices/faces to a bone	❌ face indices reference positions outside any SK entry's contiguous range
Adding a brand-new bone	❌ no space in the SK block, and no contiguous vertex range allocated
The vertex reordering pass is theoretically buildable but touches essentially every encoder in the export pipeline. It's a non-trivial feature addition, not a small fix.