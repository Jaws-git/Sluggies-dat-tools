# Icon Guide

The character-select icons (portraits) of Mario Super Sluggers live in one
icon bank in `dt_na.dat`. There are two kinds:

- **Stock icons** sit on shared sheets: one indexed image per view (side and
  front) with a palette per page, so most characters are palette variants of
  the same image. You edit them as PNG sheets and reimport them.
- **Roster icons**, for the six unused characters and new character IDs, come
  from single PNGs that the roster expansion packs into two extra pages.

## Stock icons: export, edit, reimport

1. **Export:** menu **[3]** (`python start.py --export-icons`) reads the
   original game in `1_Input`. Menu [1] also exports, but from
   `3_Output_Dat`, after the roster choice (see below). An export warns
   before it overwrites sheets you have edited; your `.act` palettes are
   kept.
2. **Edit** in `2_Output_Models/_ICONS/sheets (EDIT BASE.PNG)/side/` and
   `/front/`:
   - `BASE.png` is the shared indexed image of that view. Each pixel value is
     a palette index, so keep the image indexed (or greyscale) and its size
     (1024×256).
   - `<view>_page_<HEX>_t<DEC>_<Character>.act` is one page's palette
     (Adobe Color Table). Change a character's colours here; the matching
     `.png` shows that page with its palette, for reference.
3. **Reimport:** menu **[7]** (`python start.py --patch-icons`) writes the
   images and palettes into `3_Output_Dat/dt_na.dat` (copied from `1_Input`
   if missing). `--patch-icons --dry-run` only checks and reports. The report
   is `2_Output_Models/_ICONS/metadata/reimport_report.json`.

**With the roster expansion:** a roster configuration with icons builds its
own copy of the bank from the stock one. So reimport stock icon edits
**before** you pick the roster configuration (menu [9]), and use a menu [3]
export for them. Edits reimported while a roster configuration with icons is
injected only show after the next roster run (from a menu [3] export) or are
lost on it (from a menu [1] export, whose sheets point at the roster's copy).

## Roster icons: unused and new characters

Icons for the six unused characters and for new character IDs come from the
roster expansion (menu **[9]**, also part of menu **[1]**; see
[RosterGuide.md](RosterGuide.md)):

1. Put a side and a front portrait PNG per character into `1_Input/_Icons/`.
   They are fitted into 48×51 (`fit`: `contain`, `cover` or `strict`).
2. Name them in the character's entry of a roster configuration in
   `1_Input/_RosterConfigurations/`:
   `"icon": {"side": "x_side.png", "front": "x_front.png"}`. Preset
   `02_Stock_and_Unused.json` already does this for the six unused characters
   with the PNGs shipped in `1_Input/_Icons/`; the open slots of presets 03
   and 04 use `empty_slot_side.png` / `empty_slot_front.png`.
3. Run menu [9] (or [1]), pick that configuration, then copy `main.dol`,
   `dt_na.dat` and `fst.bin` into the game.

No Gecko code is needed: the unused characters become selectable through
their wheel entries.

## Per-character PNGs

Every icon export also writes each character's own portraits as plain RGBA
PNGs (48×51, not indexed) into that character's model folder, next to `tex/`:

```
2_Output_Models/18 Mario/78277664_mario.gpl/icon/FrontIcon.png
2_Output_Models/18 Mario/78277664_mario.gpl/icon/SideIcon.png
```

- The exporter finds the owner in the icon bank itself: the side and front
  tables key each portrait by character ID, and a character's model folder
  is its ID + `0x12` (Mario `0x00` → folder 18).
- Only characters with a portrait of their own get them: folders 18–88, and
  the unused characters (89–94) once a roster configuration gives them icons
  (menu [1]). Miis share one generic icon and are skipped, as are new
  roster IDs (they have no folder of their own).
- Export the 3D models first: a character without a model folder is skipped
  (the log lists it). Menu [1] does both in the right order.
- Each export shows the bank it read: exporting from `1_Input` (menu [3])
  removes the unused characters' PNGs that a menu [1] export wrote.
- They are for reference only; editing them changes nothing in the game.

## Dolphin's custom textures

`2_Output_Models/_ICONS/dolphin_icon_names.txt` lists the name Dolphin dumps
each icon page under. An edited copy of a page with exactly that name in
Dolphin's `Load/Textures/RMBE01/` replaces it in game, without patching.

Menu [1] also writes the roster's two icon pages to
`2_Output_Models/_ICONS/roster_pages/`, each already named the way Dolphin
dumps it (`tex1_WxH_<hash>_14.png`). Menu [3] reads `1_Input` and does not
see them; `python start.py --export-icons --use-output` exports the current
output. The roster pages' names depend on the configuration (which portraits
it packs decides the page's content and size), so export again after
changing it.
