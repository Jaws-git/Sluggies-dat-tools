# Icon Guide

The character-select icons (portraits) of Mario Super Sluggers live in one
icon bank in `dt_na.dat`. Each character has a side and a front portrait.

## Character icons in the model folders

Model exports write each character's own portraits as RGBA PNGs (48×51)
into that character's model folder, next to `tex/`:

```
2_Output_Models/18 Mario/78277664_mario.gpl/icon/FrontIcon.png
2_Output_Models/18 Mario/78277664_mario.gpl/icon/SideIcon.png
```

- **GUI:** the **Export 3D** tab's **Export Icons** checkbox (on by
  default) writes them after the model export. The **All-In-One Export**
  always writes them, from `3_Output_Dat` after the roster choice.
- **StartTools:** menus [1] and [2] write them after the models; menu [3]
  writes only the icons. Command line: `python start.py --export-icons`
  (reads `1_Input`), `--export-icons --use-output` (reads `3_Output_Dat`).
- The exporter finds the owner in the icon bank itself: the side and front
  tables key each portrait by character ID, and a character's model folder
  is its ID + `0x12` (Mario `0x00` → folder 18).
- Only characters with a portrait of their own get them: folders 18–88, and
  the unused characters (89–94) once a roster configuration gives them icons
  (All-In-One Export or menu [1]). Miis share one generic icon and are
  skipped, as are new roster IDs (they have no folder of their own).
- A character without a model folder is skipped (the log lists it), so
  export the 3D models first.
- Each export shows the bank it read: exporting from `1_Input` removes the
  unused characters' PNGs that an export from `3_Output_Dat` wrote.
- Editing these PNGs changes nothing in the game by itself. A roster
  configuration can use them as a character's portraits
  (`"icon": {"model": "<model folder>"}`, see
  [RosterGuide.md](RosterGuide.md)), and the GUI's character grid does the
  same when you put a model into a slot.

## Roster icons: unused and new characters

Icons for the six unused characters and for new character IDs come from the
roster expansion (menu **[7]**, also part of menu **[1]**; see
[RosterGuide.md](RosterGuide.md)):

1. Put a side and a front portrait PNG per character into `1_Input/_Icons/`.
   They are fitted into 48×51 (`fit`: `contain`, `cover` or `strict`).
2. Name them in the character's entry of a roster configuration in
   `1_Input/_RosterConfigurations/`:
   `"icon": {"side": "x_side.png", "front": "x_front.png"}`. Preset
   `02_Stock_and_Unused.json` already does this for the six unused characters
   with the PNGs shipped in `1_Input/_Icons/`; the open slots of presets 03
   and 04 use `empty_slot_side.png` / `empty_slot_front.png`.
3. Run menu [7] (or [1]), pick that configuration, then copy `main.dol`,
   `dt_na.dat` and `fst.bin` into the game.

No Gecko code is needed: the unused characters become selectable through
their wheel entries.
