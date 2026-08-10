# Hammerspace Build Modes

`HammerspaceMain.py` can assemble and validate a model without changing
`dt_na.dat`, `main.dol`, or `fst.bin`:

```powershell
python SluggiesTools/Hammerspace/HammerspaceMain.py path/to/model.sluggie --dry-run
```

GPL and SKN can independently use donor bytes or their current builders:

```powershell
python SluggiesTools/Hammerspace/HammerspaceMain.py path/to/model.sluggie `
  --dry-run --gpl build --skn build
```

Section defaults and currently supported values are:

| Section | Modes |
| --- | --- |
| GPL | `clone`, `build` |
| ACT | `clone` |
| TEX | `clone` |
| SKN | `clone`, `build` |
| trailing ptr6/ptr7/ptr8 | `clone` |

All flags accept `clone|build` so unsupported requests receive a specific
error instead of silently falling back to cloned donor data. Without
`--dry-run`, a valid assembled block is written through the separate
`WriteModelBlock` operation.

## Milestone 0.4 manual confirmation

Run the unchanged Peach fixture through the corrected GPL/SKN builders first:

```powershell
python SluggiesTools/Hammerspace/HammerspaceMain.py `
  "2_Output_Models\22 Peach\93430528_peach.gpl\93430528_peach.gpl.sluggie" `
  --dry-run --gpl build --skn build
```

Proceed only when the report says `"valid": true` with empty errors. Write the
same assembled modes to the output DAT with:

```powershell
python SluggiesTools/Hammerspace/HammerspaceMain.py `
  "2_Output_Models\22 Peach\93430528_peach.gpl\93430528_peach.gpl.sluggie" `
  --gpl build --skn build
```

Launch the extracted game from the output files in Dolphin. Verify Peach at
character select, then exercise idle, batting, pitching, running, and fielding.
This confirms the corrected mem-clear range and section alignment. The future
topology flush-index superset needs a separate manual test once topology rebuild
is enabled; this unchanged test intentionally preserves Peach's vanilla flush
array.

The dry-run report contains section modes, sizes, header pointers, assembled
and original sizes, size delta, and validation errors. This is intentionally a
small assembly-level report; deeper binary checks belong to Milestone 0.3.