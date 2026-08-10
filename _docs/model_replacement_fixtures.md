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
- same-count reskin
- vertex-count increase
- new face
- material reassignment
- new PNG texture

**Recorded position-only fixture:** `_docs/meta/78277664_mario.gpl.sluggie`.
All three Mario vertex buffers have obvious edits with their original byte
lengths retained; no topology, skinning, material, texture, or facial edit
markers are present.

### What To Supply

Only the position-only fixture is currently producible through the documented
Blender workflow. Make a copy of either selected donor `.sluggie`, import that
copy, move one or a few existing vertices without changing topology, and export
back onto the same copied file. Record its relative path and set
`edited_blender_fixtures.position_only` to `ready` in the matrix.

The other five slots must remain planned fixtures for now. The current Blender
guide explicitly prohibits skinning changes, adding vertices/faces, and material
slot changes; the current hammerspace builder also rejects ACT/TEX/trailing
build modes. Do not hand-edit binary data to fill those slots. Supply them only
when their editing/import paths have been implemented, or when you have a
known-good `.sluggie` produced by a supported tool.

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