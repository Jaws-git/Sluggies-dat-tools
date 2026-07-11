# Reimporting sluggies models to hammerspace

In-Place model replacements are working fine so far, but theyy have one major limitation:
Model memory footprint can not change, an in-place replacement must stay within the bounds of the current memory block, or it would potentially overwrite another model's data in the dt_na.dat file

Hammerspace is supposed to solve this by providing an extended space at the end of the dat file to write new data blocks of almost any size. However, this process is much more involved that in-place patching since nearly every aspect of the model data needs to be recalculated from the edited sluggies data.

Mario Super Sluggers is a Wii game (not GameCube)
Apparently it still uses the GX format.

## First Milestone: Unedited Round Trip

Policy: even though original data COULD be preserved, we still recalculate everything necessary to make sure the process works before tackling the more complex Milestone 2. Cloning SKN (or integrating any other original data from 1_Input during reimport) is not viable — the data MUST come from the .sluggies file only. In turn, the .sluggies file/schema must actually offer all the data required to do so (e.g. flush index array data, ptr6/ptr7/ptr8 trailing section data).

In order to make sure the recalculation of model data works, we can do a "simple" test:
1. Export a model to .sluggies format
2. Load it in blender, make no changes, export back to sluggies file with "hammerspace" toggle on
3. Re-import sluggies file, converting all sluggies data back into game format
4. check the new hammerspace model block against the original model block using a STRUCTURAL comparison (parse both blocks and compare section-by-section at field/array level). A bit-by-bit match is not expected: rebuilt padding/alignment and potential quantization rounding errors make byte-identity unrealistic.
5. the model pointer in the main.dol is patched to point to the new hammer space

Excluding the raw (tpl) texture data and the ACT (skeleton) section, which will both be cloned from the original model block until Milestone 3.

Additional Milestone 1 tasks:
- Alignment verification: DONE (2026-07-09, SluggiesTools\Debug\alignment_verify.py, 1210 model blocks / 325 skinned). The 32-byte alignment requirement is CONFIRMED, not a misconception. Strictly 32-aligned in the original data (absolute file offset AND block-relative): model block starts, skinned (cc=6) position buffers, prim lists, SKN section starts, all SKN source/weight/dest-index/flush arrays; memClrSize is always a multiple of 32. NOT aligned (arbitrary byte offsets): static position buffers, color/UV/standalone-normal arrays, DOLayout starts, memClrPtr (mod32 ∈ {0,4,8}). The builder must keep 32-byte padding for the aligned categories; a structural comparison remains preferable over a bit-by-bit comparison since the rebuilt layout inserts padding the original does not have.
- Sanity check: DONE — HammerspaceMain aborts when the original block's sections are not in GPL → ACT → TEX → SKN order.
- memClr semantics: VERIFIED empirically (SluggiesTools\Debug\memclr_probe3.py, 324/325 exact): memClrPtr is position-data-relative (not GPL-relative) and the region covers exactly the SKAcc accumulation slots not written by any SK1/SK2 entry; size = slot span rounded up to 32. Docs corrected (skn_section.html, Reimport_Order_Of_Operations.md).

MILESTONE 1 STATUS: COMPLETE (2026-07-09). GPL and SKN are rebuilt from sluggies data only (flush index bytes + ptr6/7/8 trailing sections now carried in the schema; exporter extended, existing files backfillable via SluggiesTools\Debug\backfill_sluggie_fields.py). Structural comparator: SluggiesTools\Hammerspace\CompareBlocks.py. tiny_kong round trip: all structural checks passed. Original model region is NOT zeroed (pending Milestone 4).
In-game test issue FIXED: partial transparency on the skinned submesh was caused by missing SK runtime scratch space — the skinning deformer writes vertex slots beyond the stored position array of submesh 0 (163/325 models), and the rebuilt GPL packed the color array right there. The builder now reserves max_SK_write_end bytes after submesh 0's position data (see Reimport_Order_Of_Operations.md, GPL order of implementation), and CompareBlocks.py validates the scratch window against all data arrays.

## Second Milestone

Re-import of sluggies models to hammer space with changed vertex count, skinning (aka bone weights), and draw lists.
Still excluding raw (tpl) texture data and the ACT (skeleton) section, which will both be cloned from the original model block until Milestone 3

Additional Milestone 2 requirements:
- Rebuild the SKN flush index array — it may reference specific dest-slot indices and becomes invalid once vertex counts / dest slots change.
- Rebuild SKAcc dest_index_data for the same reason.
- Handle index-width overflow: if a rebuilt vertex/UV/color array grows past 255 entries, the draw-list attribute indices must switch from u8 to u16 and the vertex descriptors (index_size) must change accordingly. All array count fields are uint16 (65535 hard cap) and must be validated.

## Third Milestone (nice-to-have):

All of the previous milestones + (potentially modified) texture reimports (png->tpl) using wiimms texture converter. This means that an entire model datablock can be reconstructed from edited sluggies data and png texture files
Import of changed skeletons/bone structure/bone count/position/facing direction.

## Fourth Milestone: Fix the --unpatch path

removeModelFromHammerspace currently only restores the DOL pointer to the original offset — but the original model bytes in the OUTPUT dat were zeroed by zeroOriginalModel after the hammerspace write. After unpatching, the game would load zeros. Fix: --unpatch must also re-copy the original model block bytes from INPUT dt_na.dat back into the OUTPUT dat at the original offset.


## The process

Importer Calculation of model data needs to happen in a specific order due to interdependencies (e.g. GPL mesh data and SKN skinning data)
The Order:
See _docs_modelformat\Reimport_Order_Of_Operations.md

According to previous reverse engineering efforts, some (not all) data may need to be 32-byte aligned in order for cpu memory access to be able to read it. VERIFIED (see the alignment verification result in Milestone 1): skinned position buffers, prim lists, and all SKN data arrays are strictly 32-byte aligned in the original data; static position/color/UV/normal arrays are not aligned at all.

Tools to interact with hammerspace are available in SluggiesTools\Hammerspace\HammerspaceHelper.py
Recalciulation of model data will happen in SluggiesTools\Hammerspace\HammerspaceMain.py

HammerspaceMain.py already contains working Cloning methods for each section of the model data block, but the existing recalculation methods are not working properly and will need to be updated. Each milestone will aim to replace its respective targeted data clone methods with data recalculating methods. No build/clone CLI switch is required: clone calls are replaced with recalculation calls in a hard-coded manner, and the clone functions will not be called in the final version and will eventually be removed.

## documentation

Take documentation as a starting point but be wary that it may be incomplete in places or that some of the inferences may be incorrect at times. Still, it has served us well so far.

Old byte-alignment investigation: _docs_modelformat\Planning_Alignment_Investigation.md
Model block structure: _docs_modelformat\overview.html
Input files (always original unedited data): 1_Input\
Sluggies and texture exports: 2_Output_Models\
DAT and DOL outputs (manipulated): 3_Output_Dat\

## testing

Milestone 1 testing will happen as follows:
1. The user has already run a complete export (debug mode - no base64 encoding)
2. the user has already loaded the model 75 Tiny Kong (272147520_tiny_kong.gpl.sluggie) in blender and exported it back to the sluggies file without changes
3. the agent runs an import of model 75 Tiny Kong (272147520_tiny_kong.gpl.sluggie) and compares the results against the original data block (structural, section-by-section comparison — see Milestone 1)
4. if successful, the user will then copy the files back to the game folder for a manual in-game test

Milestone 2 and 3 testing:
As static comparison is not possible with edited data, the tests from here on out will all be manual in-game testing for visual glitches or exploding meshes e.t.c.