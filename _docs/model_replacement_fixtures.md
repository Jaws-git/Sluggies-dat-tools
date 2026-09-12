# Model Replacement Fixtures

Milestone 0.1 uses two player `entry00` donors. Their machine-readable metrics
are recorded in `meta/model_replacement_fixture_matrix.json`.

| Fixture | Selection role |
| --- | --- |
| Shy Guy (`140106432_heyho.gpl`) | Small control donor; skinned, uses SKAcc, and has multiple display states. |
| Mario (`78277664_mario.gpl`) | Facial-pose control; adds a recognized non-zero `ptr7` section. |

Together these fixtures cover the required skinning and display-state paths
without retaining five equivalent player donors. `ptr6` and `ptr8` are zero in
the selected player entry00 models; the matrix records that explicitly.

## Regenerate the matrix

Run from the repository root after exporting models from the same input DAT:

```powershell
python SluggiesTools/build_model_fixture_matrix.py `
  "2_Output_Models/34 Shy Guy/140106432_heyho.gpl/140106432_heyho.gpl.sluggie" `
  "2_Output_Models/18 Mario/78277664_mario.gpl/78277664_mario.gpl.sluggie"
```

## Edited Blender fixtures

The matrix reserves named slots for these small edits. They remain
`not_created` until their Blender exports are made and checked in or assigned a
stable local fixture location:

- position-only edit
- UV-only edit
- same-count reskin
- vertex-count increase
- new face
- material reassignment
- new PNG texture

**Recorded position-only fixture:**
`2_Output_Models/18 Mario/78277664_mario.gpl/78277664_mario.gpl.sluggie`.
Mario submeshes 0 and 1 have visible edits with original byte lengths retained;
the unchanged hammerspace control and both edits pass in Dolphin.

### What To Supply

Position-only and UV-only fixtures are currently producible through the
supported Blender workflow. For UV coverage, provide one donor-slot value edit
and one split-seam edit if practical; use a checker texture for the Dolphin
test. Complete same-FourCC material reassignment is active for donor surfaces.
Mario submesh0 `sm0_ds16` -> `sm0_ds14` is the selected different-texture probe:
move all 20 ds16 faces, preserving its primitive list while changing its local
texture binding from slot 1 to slot 0. Build-only validation passes; keep the
fixture status planned until this exact edit passes in Dolphin. Partial-surface,
shared-binding, and cross-FourCC moves remain unsupported.

Mario submesh0 ds6 -> ds5 is the primitive-bearing Type-1 probe. Despite the
raw ds6 setting `11110001`, both batches use the effective `Spec` shader mode:
ds6 inherits it from ds5. Move all 594 ds6 faces to ds5. The rebuild keeps ds6's
primitive payload and Type-1 command ID, changing its local texture binding to
`11110000`. Build-only validation passes with zero size delta; Dolphin validation
is pending.

The reverse ds5 -> ds6 assignment does not remove or move ds5's Type-7 command:
the alias-in-place rebuild leaves `Spec` at ds5 and copies ds6's texture-1
binding backward into ds0, the source-local setter for ds5. Both batches then
execute as `Spec` with texture 1. Structural validation passes with zero size
delta, so this direction is not guarded; manual Dolphin validation remains the
runtime gate.

**Failed visibility-role probe:** the current saved assignment moves all 562
Mario submesh0 ds9 (`RhSp`) faces to ds5 (`Spec`), not ds14. The generated GPL
kept the ds9 primitive pointer and payload unchanged, changed ds9's setting to
`Spec`, and changed its local texture setter. It validated structurally with zero
size delta, but the right hand remained invisible in game even where ordinary
`Spec` geometry and an unedited `RhSp` hand were visible. This proves the hand
role is not disabled by replacing the FourCC in its existing display-state slot.
All `RhSp`/`LhSp`/`Spec` cross-mode face reassignments are guarded again pending
a verified primitive/state relocation mechanism.

The failed build also copied target Type-1 pad bytes (`000408` -> `000008`).
Those bytes are opaque and not part of the documented texture binding setting;
texture aliases now preserve source pad bytes and copy only the setting word.

The same-count reskin, vertex-count increase, and new-face slots must remain
planned for now. Do not hand-edit binary data to fill those slots. Supply them
only when their editing/import paths have been implemented, or when you have a
known-good `.sluggie` produced by a supported tool.

**Additional-texture Phase 0 fixture (runtime passed):**
`Debug/fixtures/78277664_mario.gpl.additional_texture_ds5.sluggie` appends
`newmariotexture.png` as Mario TEX index 5. It clones texture 0's CMPR
descriptor fields, encodes a 512x512 base image, and changes submesh 0 DS0's
Type-1 setting from `0x11110000` to `0x11110005`. DS5 inherits that layer-0
binding and retains its `Spec` mode and 1,438-face primitive data. DS6 installs
texture 1 afterward, limiting the intended change to the DS5 batch.

The installed output entry is `0x2BE6E620`, length `564864`, SHA-256
`62c4e8c741b15792ec0ee0c3c436d01d25c04573924a360dec27376ee35ff06e`.
Structural validation passes, all five donor image payloads are byte-identical,
TEX count is 6, ptr7 relocated to `0x87540`, and all shared Mario DOL entries
route to the new block. Dolphin character select, static scene, and animated
gameplay all pass; Mario displays the new DS5 texture exactly as expected.

**Shared additional-texture Phase 0 fixture (runtime passed):**
`Debug/fixtures/78277664_mario.gpl.additional_texture_sm0_ds5_sm0_ds9.sluggie`
routes both submesh 0 DS5 and DS9 to the same appended TEX index 5. DS5 starts
from texture 0 (512x512 CMPR), effective shader `Spec`, inherited setter DS0;
DS9 starts from texture 3 (256x128 CMPR), effective shader `RhSp`, local setter
DS7. The appended descriptor still clones texture 0.

The installed output entry is `0x2BE6E620`, length `564864`, SHA-256
`bcd046ad467784730609de9f8c46a2468adbe3dbb5f52a1a533f9d47b0e34762`.
Structural validation passes with TEX count 6; DS0 and DS7 are both
`0x11110005`; all other GPL bytes remain donor-identical; ptr7 remains correctly
relocated; and all shared Mario DOL entries route to this block. Dolphin
character select, static scene, and animated gameplay all pass. Both DS5 and
DS9 display the shared new texture correctly and unaffected surfaces remain
correct.

**Unaligned additional-texture payload fixture (runtime passed):**
`Debug/fixtures/78277664_mario.gpl.additional_texture_sm0_ds5_sm0_ds9_alignment.sluggie`
appends two distinct CMPR textures. Slot 5 is the 505x505
`customdimensionedtexture.png` payload (129,032 bytes, 8 modulo 32) assigned to
DS5. Slot 6 is the 512x512 `newmariotexture.png` assigned to DS9. Consecutive
packing places slot 5 at aligned TEX-relative pointer `0x3C900` and slot 6 at
deliberately unaligned pointer `0x5C108` (8 modulo 32).

The installed output entry is `0x2BE6E620`, length `693952`, SHA-256
`e317c4a678473a2fed633db690bb8699b1b8eaae752bd61dc220970ead01a6b8`.
Structural validation passes with TEX count 7; DS0 is `0x11110005`; DS7 is
`0x11110006`; all unrelated GPL bytes remain donor-identical; and all shared
Mario DOL entries route to this block. Dolphin character select, static scene,
and animated gameplay all pass. DS5 displays the NPOT image, DS9 displays the
second image from the deliberately unaligned pointer, and unaffected surfaces
remain correct. Individual TEX image payload starts therefore do not require
32-byte alignment in this tested path.

The two unchanged-donor control tests do require manual game evidence now. For
both Shy Guy and Mario, perform a normal all-clone write to a disposable output
DAT, then record `pass` or `fail` for all three matrix fields:

1. `character_select`: character renders and selects normally.
2. `static_scene`: character renders in a non-animated scene.
3. `animated_gameplay`: character survives normal batting/fielding motion.

Add a short `notes` field to the fixture's `manual_control_test` object for any
failure. The fixture-matrix generator preserves existing manual control results
and edited-fixture metadata when it is rerun for the same donor path.

## Completed Clone Control

Peach (`93430528_peach.gpl`) is recorded as a supplemental all-clone control in
the matrix. Character select, a static scene, and animated gameplay all passed.
The Shy Guy and Mario donor entries remain pending separate control runs; Peach
results are deliberately kept separate rather than attributed to either donor.

## Manual control test

For each unchanged donor, relocate it with the current clone path and set the
three matrix results from `pending` to `pass` or `fail`:

1. Open character select and inspect the character.
2. Load a static scene containing the character.
3. Play an animated gameplay scene and exercise normal fielding/batting motion.

Record a failure note beside the failed result before changing later rebuild
code. These runs are the known-good control for subsequent milestones.