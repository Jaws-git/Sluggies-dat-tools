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

The same-count reskin, vertex-count increase, new-face, and new-PNG-texture
slots must remain planned for now. Do not hand-edit binary data to fill those
slots. Supply them only when their editing/import paths have been implemented,
or when you have a known-good `.sluggie` produced by a supported tool.

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