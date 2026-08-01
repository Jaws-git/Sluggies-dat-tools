# Texture Modding Notes (TEX section)

This document summarizes how textures are stored and referenced in Sluggers model blocks, how they map to materials/meshes, and what a future hammerspace texture patch path would need.

## Scope clarification (2026-07 player icon findings)

This file documents MODEL TEX behavior (the TEX section inside a model block).

Player icons are a separate system and do not use per-model TEX descriptors. New reverse engineering confirms the icon render path is driven by character-id routing tables and icon resource rows in the icon bank (`dt_na.dat` group 119, entry 2), not by a model's TEX section.

Implication:

- "hammerspace-first" in this document applies to model TEX growth/repacking.
- It does NOT mean player icon edits always require hammerspace.
- Current project policy for icons remains in-place spritesheet replacement unless explicitly opting into icon-bank expansion.

See: `_docs/_docs_model_format/Player_Icon_System_Ground_Truth.md` for the icon-specific ground truth offsets, resolver flow, and tested hook points.

## Current status in this repository

- Export extracts TEX metadata into each .sluggie under `TextureDescriptors` and `TEXHeader`.
- In-place patching does not patch TEX at all (only vertex/UV/shader mode and skin data).
- Hammerspace currently clones TEX verbatim from the input model block.
- Blender import uses png files only for viewport material preview (from `tex/<TextureIndex>.png`).

So, texture replacement/addition is not implemented yet in either patch path.

## 1) How the game stores and references textures in general

At model-block level, the file header has a TEX section pointer:

- Model header +0x0C -> TEX section offset (relative to model block start)

Inside TEX:

- +0x00: uint16 texture count
- +0x02: uint16 CLUT count
- +0x04: array of texture descriptors (0x20 bytes each)

Per descriptor (relative to TEX start):

- +0x00: image data pointer
- +0x04: palette data pointer (0 if none)
- +0x08/+0x0A: height/width
- +0x17: image format
- +0x18/+0x1A: palette entry count / palette format
- other bytes include LOD and unknown fields that are preserved by export

Important pointer convention:

- TEX descriptor pointers are section-relative (relative to TEX section start), not absolute file offsets.

In the .sluggie export, each texture also stores absolute source offsets and lengths (`ImageDataOffset`, `ImageDataLength`, optional palette offset/length) so raw texture payload size can be validated or rebuilt.

## 2) How textures are assigned to materials/meshes

Texture assignment is driven from GPL draw/display state, not from TEX itself.

Runtime binding path:

- Display state type 1 (`id == 1`) packs:
  - texture index (which TEX descriptor to sample)
  - UV channel coordinate index (`texture0..texture7` selection)
  - wrap S / wrap T
- Display state type 3 controls which vertex attributes are present per draw command, including `texture0..texture7` index streams.

Tooling representation:

- Export records per-face primary texture indices as `FaceTextureIndices`.
- Export records per-UV-channel binding metadata (`TextureIndex`, `WrapS`, `WrapT`) in `UVChannels`.
- Blender import creates one material slot per used texture index and assigns polygons by `FaceTextureIndices`.
- For preview image loading, importer looks for `tex/<TextureIndex>.png` next to the .sluggie.

Implication:

- TEX is the storage pool.
- GPL draw state decides which texture index each draw batch/face actually uses.
- Adding a new texture without updating bindings will not make it visible.

## 3) What hammerspace would need to support changed or additional textures

If TEX length changes, all later block offsets shift. A complete hammerspace texture workflow therefore needs a rebuild, not an in-place write.

### Required build steps

1. Add external texture payload input
- Read already-converted texture payload bytes from files in the model output folder.
- Do not perform png->game-format conversion in Sluggies tools.
- Suggested source location: the model's texture folder near the .sluggie.

2. Extend .sluggie editable texture payload fields
- Keep existing descriptor metadata (format, size, palette fields, unknown bytes).
- Add optional edited payload fields (image bytes and optional palette bytes) and possibly descriptor edits.
- Validate payload size against descriptor width/height/format rules.

3. Build TEX section bytes from descriptor list + payloads
- Write TEX header counts.
- Write N descriptors (0x20 each).
- Repack image/palette payload blobs.
- Recompute descriptor data pointers (section-relative).
- Preserve unknown descriptor bytes unless intentionally edited.
- Keep stable alignment/padding strategy (recommend 0x20 alignment for payload blocks for safety/consistency).

4. Reassemble full model block with new TEX length
- Recompute section starts in header:
  - gpl_off = 0x20
  - act_off = gpl_off + len(GPL)
  - tex_off = act_off + len(ACT)
  - skn_off = tex_off + len(TEX_new)
- Recompute ptr6/ptr7/ptr8 using existing relocation rule relative to SKN start:
  - new_ptr = new_skn_off + (old_ptr - old_skn_off)

5. Keep GPL texture references coherent
- Existing texture indices in draw state type 1 and `FaceTextureIndices` must remain valid.
- If adding new textures and remapping faces/materials, update draw-list/face mapping data accordingly.

6. Preserve unpatch behavior
- `--unpatch` should restore original DOL entry + original model block location/content as today.

### Why this must be hammerspace-first

In-place patching assumes fixed buffer lengths at fixed offsets. TEX growth changes section boundaries and therefore cannot be safely applied with current in-place logic.

Note: this statement is about model TEX sections only. Player icon data follows a different path (see the player icon ground-truth doc linked above).

## 4) Shared textures: how the game handles them

### Observation

Using Dolphin's custom texture loader, replacing a single texture file by hash simultaneously changes the appearance of multiple otherwise unrelated model variants. The three Monte costume variants (dirs 39, 40, 41 — `156474816_monte.gpl`, `160616544_monte.gpl`, `164758272_monte.gpl`) were used as the concrete test case.

### Mechanism: data duplication, not cross-block pointer sharing

Each model block is fully self-contained. There is no runtime pointer from one model's TEX section into another model's TEX section, and there is no shared texture pool in the file. Each model's TEX section physically embeds its own copy of every texture it uses.

When two models happen to use the same image, the identical pixel bytes are duplicated at different file offsets — one copy per model block. MD5 comparison of the raw image payloads confirms this:

| Texture | Dims    | Model 39 offset | Model 40 offset | Model 41 offset | Match?       |
|---------|---------|-----------------|-----------------|-----------------|--------------|
| 0       | 256×256 | `0x9557220`     | `0x994a4c0`     | `0x9d3d760`     | **identical** |
| 1       | 256×256 | unique          | unique          | unique          | different    |
| 2       | 1024×128| unique          | unique          | unique          | different    |
| 3       | 256×128 | unique          | unique          | unique          | different    |
| 4       | 64×64   | duplicate       | duplicate       | duplicate       | **identical** |

Texture 0 is the shared skin/face texture across costume variants. Texture 4 is a small specularity map that appears to be common across characters (or at least all Monte variants). Textures 1–3 are costume-specific.

### Why Dolphin's custom loader sees them as one texture

Dolphin identifies a texture by hashing the raw pixel data at the point the game calls `GX_LoadTexObj`. Because the three model blocks contain byte-for-byte identical copies, they produce the same hash. Dolphin therefore replaces all of them with the same custom texture file, making it appear as though the texture is "shared" at a higher level — but the sharing is entirely implicit, arising from duplicated data.

### Implications for texture modding

- **No single-point patch.** There is no one offset to write. A shared texture must be patched in every model block that contains a copy.
- **Diverging variants.** If a texture should become different per variant (e.g. give each costume a distinct face), each model's TEX section must receive a different payload. Because TEX size may change, this requires the hammerspace rebuild path for each affected model.
- **Specularity / universal maps.** Texture 4 (64×64) being identical across all Monte variants suggests there is a class of "universal" textures (specularity, environment, etc.) that the game ships as verbatim copies in every model that uses them. Replacing one in Dolphin replaces all, but patching one in the file only affects that one model block.
- **Identifying shared textures.** The reliable way to detect sharing is to MD5 the `ImageDataLength` bytes at `ImageDataOffset` across all model blocks for a character group and look for collisions.

## 5) Restriction: external conversion only

Texture conversion from png to game-ready encoded texture bytes is out of scope for Sluggies tools.

Required user workflow for texture modding should be:

1. Prepare already-converted texture data with external tools.
2. Place converted files in the model output folder (alongside that model's .sluggie workflow files).
3. Let hammerspace patching consume those pre-converted payload files and rebuild TEX/model block offsets.

This keeps Sluggies focused on model-block assembly and pointer correctness, while format conversion remains delegated to dedicated image/texture tools.