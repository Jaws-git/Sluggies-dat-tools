# Roster Expansion Guide

The roster expansion adds characters to Mario Super Sluggers' exhibition draft:
the six unused characters on their families' colour wheels, wheels of up to 10
members, new character IDs, a larger draft grid with new squares, and your own
icons and names. It works on the output of the normal pipeline
(`3_Output_Dat/main.dol`, `dt_na.dat`, `fst.bin`) and can be removed again.

The binary details are in [`_docs_roster/RosterExpansion.md`](_docs_roster/RosterExpansion.md).

## Quick start

1. The expansion works on `3_Output_Dat`. Menu [1] builds it and offers the
   roster choice itself; model patches can come before or after.
2. Menu **[7] Roster expansion** (also part of menu **[1]**, between the model
   and the icon export) lists every `.json` file in
   `1_Input/_RosterConfigurations/`, alphabetically, with a number to pick
   it, **[r]** to reset the roster to vanilla and stop, and Enter to
   skip. The shipped
   presets:

   | File | What you get |
   |---|---|
   | `01_Stock_Roster.json` | No new content. |
   | `02_Stock_and_Unused.json` | The six unused characters (Black Yoshi, White Yoshi, Black Toad, Black Pianta, Black Kritter, Black Koopa) on their families' wheels, with the icons from `1_Input/_Icons`. White Yoshi sits on the Koopa wheel, so no wheel goes above 7 and `main.dol` stays compatible with Gecko codes made for the stock game. |
   | `03_Unuseds_and_8_color_slots.json` | Preset 02, every colour wheel filled to 8, and a new wheel of 3-4 for each of the 30 characters without one, all with **open slots** (123 new IDs). |
   | `04_Unuseds_and_10_color_slots.json` | Preset 02, every colour wheel filled to 10, and a new wheel of 3 for each of the 30 characters without one, all with open slots (123 new IDs). |
   | `05_Extra_Columns_12x4_grid.json` | Wheels of up to 8 on a 12×4 grid: 7 new squares, and the remaining IDs spread evenly over the new squares and the characters without a wheel (153 new IDs). |
   | `06_Extra_Columns_Max_12x5_grid.json` | Wheels of up to 8 and a new wheel of 3 for each character without one, on a 12×5 grid: Luigi gets his own square, and the remaining IDs fill 19 new squares with 2-3 open slots each (153 new IDs). |

   Your own configurations go into the same folder and show up in the list.

3. Copy `main.dol`, `dt_na.dat` **and `fst.bin`** into the game.

From the command line: `python start.py --roster --config <file>` (or
`--roster --remove`). In the GUI: the **Roster Size** tab.

Every choice replaces the previous roster, so you can switch presets freely,
and **your slots come along**. Character by character:

- **On the old and the new grid:** the slot keeps what you gave it: models
  and gear in a new ID, its name, portraits, stats source, square voice and
  stat edits (renamed or re-statted stock characters too). Where it sits
  comes from the new preset, so a new ID can end up on another wheel.
- **Only on the old grid:** it loses everything. A new ID is gone; an unused
  character (0x47–0x4C) gets its models, gear and stat edits back to their
  baseline, so it is clean when a later preset brings it back.
- **Only on the new grid:** an open slot (see below).

The GUI lists what is kept, reset and dropped before anything is written
(`--dry-run` prints it). **Start fresh** (`--fresh`) injects the preset as it
is: only models patched into stock slots and stat edits stay.

**[r]** / **Reset to vanilla** (`--remove`) only resets the roster, and since
2026-10-06 the stat edits with it. It works like the model unpatcher: the
original bytes come from `1_Input/main.dol`, so `1_Input` must hold the
original game files. Your model patches and untangled routes stay as they
are; the roster's copies in `dt_na.dat`'s extra space are zeroed, and the
file keeps its size.

**Open slots** are new IDs with nothing assigned yet. They show the "empty slot"
icon (`1_Input/_Icons/empty_slot_side.png` / `empty_slot_front.png`, which you
can edit) and the name "Empty slot", and play as a template character: a wheel's
open slots as that wheel's own character, the new squares' as Peach (the
game's fallback character). You can give them your own icon and name in a
configuration, or put an exported model into them (see "Putting a model into
a slot" below).

To make your own configuration, copy a preset in
`1_Input/_RosterConfigurations/` under a new name and edit it.

## Putting a model into a slot

Once a roster is built, a slot (one character ID) can take an exported model
without editing a configuration:

```
python start.py --patch-slot 0xE2 "2_Output_Assets/27 Bowser/114968608_koopa.gpl/114968608_koopa.gpl.sluggie"
python start.py --clear-slot 0xE2
```

The slot decides where the model goes, not the `.sluggie`'s own chunk. Picking
the High model's file or its Low partner's (folder name with `_L_`) patches both when the other one is in its
sibling folder. When that folder holds several `.sluggie` files (variants saved under new names), the partner is
the one of the same name without the leading offset and `L_` (`78277664_mario_red.gpl.sluggie` and
`78711424_L_mario_red.gpl.sluggie`), else the exported file named after its folder. Bundled bats and gloves
likewise come from the exported files. `--dry-run` prints what would happen and builds and checks the
models, but writes nothing.

| Slot | What `--patch-slot` does |
|---|---|
| New ID (`0x66`–`0xFE`) | Gets its own copy of the source character's files (model, animations, bat); a slot that already holds that character's files keeps them, so earlier patches stay. On a new grid square it takes the source's stats, and the square's voice becomes the source's if none is set yet. An "Empty slot" takes the source's name. |
| Stock character | Model only (stats, voice and name stay). The source must have the same skeleton; otherwise use a new ID. |

Both kinds keep the slot's portraits (portrait edits, a paste or
`--clear-slot` change them). The one exception is a new ID that still shows the
"Empty slot" portraits: it takes the source's exported portraits
(`icon/SideIcon.png` and `icon/FrontIcon.png` in its High model's folder),
unless a portrait edit for it is staged before the patch. A High model without a Low partner is used as
the Low model too, which loads it twice on the field. A Low model alone is
refused on a new ID; on a stock character it is accepted only when the slot's
High model is its own partner.

`--clear-slot` gives a stock character its vanilla models, portraits and
own stats back, and a new ID a fresh copy of its template's files, the
template's stats, the "Empty slot" name and portraits. The square's voice
stays.

## Replacing a slot's animations

Each character has nine animation banks (files 6-14 of its model folder;
`44/28/17/12/4/9/24/2/5` animations, exported to the `anm` folder). A slot can
take banks exported from Blender (**Export Sluggers .anm**) through
**Replace animation...** on the character grid; select several `.anm` files in
its file dialog to stage them together (one check, one confirm dialog; two
files of the same bank: the later one wins):

```
python start.py --write-slot-animation 0x13 7 "2_Output_Assets/18 Mario/anm/7 custom.anm"
```

The whole bank is replaced; its animation count picks the file. It must fit the
skeleton the slot holds **now**: an `.anm` that moves a bone the slot's model
does not have is refused ("the slot's skeleton has no bone for ..."). With added
bones (see the Blender guide), patch the `.sluggie` first and the animation
second, in that order. Recolours that share a bank keep theirs. The stock bank
is restored with `--unpatch --target-id 0xNN --target-file 7`, and clearing a
slot resets every bank. A bank larger than the largest stock bank of that file
gets a memory warning.

## Renaming a slot

Every slot can be renamed, stock characters included:

```
python start.py --rename-slot 0x0D "Little Toad"
python start.py --rename-slot 0x0D ""
```

One name is used for English, French and Spanish, in the name tables and on
the select screen's name plate. The name must fit the plate (115 x 16 pixels at
the stock font size; roughly 12-14 letters). A longer name is refused;
it is never shrunk. A blank name resets the slot: a stock character gets its
stock name back, a new ID the "Empty slot" name. Renaming rebuilds the roster
once, like a portrait change. Miis (`0x4D`–`0x65`) have no name plate and
cannot be renamed.

Putting a model into an "Empty slot" names it after the source character,
unless you renamed the slot first. A rename after the patch wins.

## Voice and stats

Any square can speak with another character's voice, and any slot can play
with another character's stats:

```
python start.py --set-voice 0x0D 0x09
python start.py --set-stats 0x0D 0x09
python start.py --set-voice 0x0D -
python start.py --set-stats 0x0D -
```

`--set-voice` takes any slot of the square, then the character whose voice
the square takes; `-` (or `default`) gives the square its own voice back.
The voice belongs to the square, and it is the same on the select screen and
on the field:

- A **stock square** speaks as one species, so the voice changes for every
  member of its colour wheel (the unused characters and new IDs on that
  wheel included). The character's model, wheel and everything else stay;
  only the voice changes. Two stock squares can swap voices.
- A **new square**'s voice reaches only its members without a colour wheel.
  Members that are on a wheel keep their wheel's voice. A new square cannot
  use a voice that its stock square gave away and no other square took; the
  change that would cause this is refused (give the new square another voice
  first).
- A voice is picked per character family: picking Blue Toad gives the
  square Toad's voice.

A **character voice** belongs to one slot, whatever its square speaks with:

```
python start.py --set-char-voice 0x8F 0x09
python start.py --set-char-voice 0x8F -
```

The slot then speaks with that family's voice on the select screen and on
the field, while the other members of its square keep theirs. A **voice
pack** (a folder in `_gui/Sound_Packs/`, made on the GUI's Voices tab from your own
WAV files) can go on top of a stock voice, so a custom character gets its own
clips:

```
python start.py --set-char-voice 0x8F pack:Rosalina
python start.py --set-char-voice 0x8F 0x09+pack:Rosalina
python start.py --set-species-clip 0x00 v1 my_cheer.wav
python start.py --set-species-clip 0x00 v1 -
```

The first gives the slot the pack's clips on top of the pack's base voice (the
stock voice its unfilled moments come from), the second on top of Bowser's.
`--set-species-clip` replaces one voice moment of a stock voice (here Mario's
short cheer) for every character who speaks with that voice; `-` puts the
stock clip back. The 12 voice moments are `v1` short cheer, `v3` big effort,
`v4` talking line, `v5` long cheer, `v6` hit by something, `v7` and `v8` two
more long cheers, `v10` stagger, `v11` small hurt, `v12` falling, `v13` the
select-screen name call, `v14` throw grunt. A WAV of any format is made mono,
32 kHz and at most 2.5 s long, then encoded like the game's clips (a few
seconds per clip). All of this needs the
game's sound archive: copy the game's `files/sound_NA` folder (`MY2.brsar`
and `stream/`) into `1_Input/sound_NA/`. The voice's clips are copied into
the slot's voice bank, and the rebuild writes the grown archive to
`3_Output_Dat/sound_NA/MY2.brsar`, which goes into the game with the other
files (`CopyFilesToGameDir.bat` copies it). Each copied voice adds about
100–250 KB to the bank of the slot's species, which the game loads whenever
that species plays; the Maintenance tab's "Sound memory" check warns when a
bank grows past what is known to work. `-` gives the slot its square's voice
back; clearing a slot does so too, and copying a character brings its voice
along. Miis cannot take a voice.

`--set-stats` lets a slot play with another stock character's (`0x00`–`0x4C`)
stats: batting, pitching, fielding and chemistry. Its model, size, voice and
name stay. Chemistry follows the stats: a stock character with Bowser's
stats gets on with others as Bowser does, both ways. `-` gives a stock
character its own stats back, and a new ID its template's. Putting a model
into a new ID on a new square sets its stats to the model's character; a
stats change after that wins. Clearing a slot also resets its stats, but not
the square's voice.

Each change rebuilds the roster once (several changes in one edits file
share that rebuild). Miis cannot take other stats; their squares are not on
the grid.

## Portraits

Either portrait of a slot (front: the draft grid; side: the colour wheel and
the batting order) can take any image:

```
python start.py --set-icon 0x0D front my_toad.png
python start.py --set-icon 0xE1 side art.jpg --fit cover --no-trim
```

- **Formats:** PNG, JPEG, BMP, GIF, TGA and WEBP, up to 4096x4096. The
  file's content decides, not its extension; anything else (TIFF, PSD, a
  renamed text file) is refused. An animated GIF or WEBP gives its first
  frame.
- **Fitting to 48x51:** `--fit contain` (default) scales the image to fit
  inside, with transparent bars; `cover` fills the portrait and crops the
  overflow; `strict` takes only images of exactly 48x51. First the
  transparent border is cropped away, so a character on a large transparent
  canvas fills the portrait (`--no-trim` keeps it). Images smaller than
  48x51 are scaled up and look blurry (warned).
- **Transparency** is on/off only: alpha 128 and up is opaque, the rest
  fully transparent. Soft edges and half-transparent shadows are rounded
  (warned when much of the art has them).
- **The other portrait** stays as the slot shows it now. A slot without
  portraits of its own (a new ID showing its template's, a stock character's
  stock art) keeps that picture as its own from then on; stock art is
  re-encoded once, which can shift colours slightly.

Putting a model into a slot replaces both portraits with the model's
exported ones, so a portrait change staged before it is dropped; one staged
after it wins. Clearing a slot resets both portraits. Miis (`0x4D`–`0x65`)
show the Mii icon and cannot take portraits.

## Bats and gloves

Each character directory has four equipment files besides the models: the
**bat**, the **left glove**, the **right glove** and an **extra bat** slot
(file 5, which only Peach, with a second bat, and Wario fill; the game's use
of it is not confirmed). The slot's **Equipment** row in the GUI shows each one as
Original, Modified or Empty, with **Select...** (pick an exported bat or glove
`.sluggie`) and **Reset**. From the command line:

```
python start.py --patch-slot 0x0D "2_Output_Assets/22 Peach/94021600/94021632_bat.gpl/94021632_bat.gpl.sluggie"
python start.py --patch-slot 0x0D "<peach bat>.sluggie" --equipment 5
python start.py --clear-slot 0x0D --equipment bat
python start.py --clear-slot 0x0D --equipment all
```

- The file is chosen by what was exported: a bat goes into the bat slot (or,
  with `--equipment 5`, the extra slot), a glove only into its own hand. A
  glove into a bat slot, or a left glove into the right hand, is refused.
- **Only that slot changes.** Most characters share their vanilla bats and
  gloves; replacing one never touches the others.
- The bat or glove needs the same skeleton as the slot's own (an empty extra
  slot takes anything); bones you added are fine.
- `--equipment N|all` on `--clear-slot` resets just those files; a plain
  `--clear-slot` resets the models and every changed bat and glove.
- A model patched into a **new ID** takes the bats and gloves found in its
  character folder (`2_Output_Assets/<ID> <name>/`) along. Files that are not
  found stay as the slot had them; two candidates for one file refuse the
  patch. Gear you picked yourself for the same file wins. `--no-gear` turns
  this off.
- Roster packs save changed bats and gloves too (older packs load fine and
  leave equipment as it is).

## Copying a character to another slot

Any character on the grid can be copied onto another slot, to move a
finished character to another grid position or to fill the field with
clones. In the **Character grid** tab, right-click a character (a square
with one character on the grid, or a character on an opened square's
colour wheel) and choose **Copy**. It gets a teal border. Right-click another slot and
choose **Paste**: the usual dialog shows what changes, and **Stage** adds
it to the pending edits. A square with several characters shows only
"Cannot copy multiple characters at once"; open it and right-click one of
them. The copied character stays marked, so it can be pasted onto several
slots.

```
python start.py --copy-slot 0x09 0xE1 --dry-run
python start.py --copy-slot 0x09 0xE1
```

The slot becomes a clone of the character **as the game holds it now**.
Pending edits of the copied character are not copied: patch them first.

- **Models, bats and gloves:** all of them, including the unchanged ones,
  as finished blocks (no `.sluggie` needed). A new ID gets its own model
  directory. A stock slot keeps its own animations, so it takes only a
  character with the same skeleton (Red Toad onto Blue Toad works,
  Bowser onto a Toad is refused); paste onto a new ID instead. A bat or
  glove that does not fit the slot is left as it is, with a warning.
- **Name, portraits, stats:** the name in all three languages, both
  portraits (stock art is re-encoded once and looks the same), the stats
  source and every stat value, including Stat Editor edits and chemistry
  in both directions. The pasted slot's own chemistry with the copied
  character stays.
- **Voice:** it belongs to the square, so the square keeps its voice. Only
  a slot that is alone on a new square takes the copied character's
  voice.
- A paste drops the slot's earlier pending edits. A later model patch or
  clear of the same slot replaces the paste.
- Miis keep their name, stats and the Mii icon; they cannot be pasted onto
  a new ID.

## Exporting a slot as `.sluggie` files

Right-click a character in the **Character grid** tab and choose **Export
as .sluggie** (below Copy and Paste) to save the slot **as the game holds it
now** as a model export you can edit in Blender and patch into other slots:

```
python start.py --export-slot 0x00
```

- **Where:** a new folder `2_Output_Assets/Custom <name> NN`, laid out like a
  regular export: the High and Low model, the bats and gloves, the `anm`
  files, and the portraits the slot shows (`icon/` in the High model's
  folder). `NN` starts at `01` and counts up when that folder or one of its
  file names is already taken.
- **Names:** the folders keep the base model's names (`78277664_mario.gpl`);
  the `.sluggie` files get the folder's name and number behind the geo name
  (`78277664_mario_FireMario01.gpl.sluggie`), so each file name is unique in
  `2_Output_Assets` and can be patched by name.
- **Base model:** each file counts as the vanilla file its slot started
  from: a stock character's own, or a new ID's model source. So the export
  goes into any slot that model fits, like a regular export of it (a stock
  slot needs the same skeleton), not just the one it came from.
- **Own donor:** each `.sluggie` carries the slot's block it was read from
  (`DonorEntry`), and every patch builds from that block. It therefore always
  goes through Hammerspace, also after Blender exports it (add-on 0.8.4 or
  newer). Files are larger than a regular export (the block rides along).
- **Not in it:** pending edits of the slot (it asks first; patch them first
  to include them), the name, stats and voice. Pasting (above) moves those.

## Several changes at once

Several changes can go in one run with an edits file, a JSON list of
`patch`, `clear`, `copy`, `rename`, `voice`, `stats`, `icon`, `equip` and
`equip_clear` edits (a `copy`
edit's `source` is the character to clone; a `rename`
edit's `text` may be blank; a `voice` or `stats` edit's `source` may be
`null` for the default; an `icon` edit has `view`, `file` and optionally
`fit` and `trim`; an `equip` edit has `sluggie` and optionally `file`, 2-5;
an `equip_clear` edit has `file`):

```
{"edits": [{"op": "patch", "id": "0xE2", "file": "2_Output_Assets/27 Bowser/114968608_koopa.gpl/114968608_koopa.gpl.sluggie"},
           {"op": "clear", "id": "0x1D"},
           {"op": "rename", "id": "0x0D", "text": "Little Toad"},
           {"op": "voice", "id": "0x0D", "source": "0x09"},
           {"op": "stats", "id": "0x0D", "source": null},
           {"op": "icon", "id": "0x0D", "view": "front", "file": "my_toad.png", "fit": "contain"}]}
```

```
python start.py --apply-slots edits.json --dry-run
python start.py --apply-slots edits.json
```

A later edit for the same slot replaces an earlier one of the same kind (a
later voice edit replaces an earlier one of the same square, a later icon
edit the earlier one of the same view; a clear also drops the slot's earlier
renames, stats and icon edits, since it resets them). A Low model picked
after a High model of the same character for the same slot joins it as a
pair. If any edit is refused, nothing is written and the output names the
refused edits.

In the GUI, the **Character grid** tab does the same: click a square, then a
slot, and use **Rename...** (or click the slot's name), **Select .sluggie...**,
**Clear slot**, **Adopt stats...** or **Voice...** (the slot's own voice: a stock
character's voice or a voice pack from the Voices tab, whatever its square
speaks with; the slot view shows the voice in force, "(square)" or "(own)").
The square view has **Set voice (all)...** for the whole square (a one-member
square shows it on the slot view too). Clicking the slot view's front
or side portrait picks an image for it: a dialog shows your image, the 48x51
result beside the other portrait, the fit choices (**Fit inside**, **Fill
and crop**, **Exact 48x51**) and **Trim transparent border**, with warnings
and refusals. The rename dialog tells you
live whether the name fits the plate; **Stats...** and **Voice...** show the
current assignment and a list to pick from. A dialog shows what
will change (models and their sizes, directory, stats, voice, name,
portraits, warnings) and whether the checks passed. **Stage** does not write
anything yet. It adds the change to a list of pending edits:

- slots and squares with pending edits get an orange border, and the slot
  view lists them and previews pending portraits;
- **Discard pending** (slot view) and **Discard all** drop pending edits;
- the green **Patch Game (N)** button checks all pending edits again, shows
  one summary, and its **Patch Game** button writes them all in one run.

When that run fails part-way, the pending edits are kept and the grid shows
what landed; fix the cause and press **Patch Game** again. Pending edits live
only while the GUI is open: closing it, or applying a roster preset, asks
first.

Each command first reads the roster back from `3_Output_Dat`, applies the
changes and rebuilds the roster from that once (so your other slots, names
and portraits are kept), then patches the models. Every model is built and
checked before anything is written; a refused change writes nothing.

## Saving and loading a roster

A **roster pack** (`.sluggiesroster`) holds a whole roster in one file: the
grid, names, square voices, stats sources, the new IDs' own model
directories, every model patched into a slot (the finished High and Low
blocks), and every portrait of its own.

```
python start.py --save-roster my_roster.sluggiesroster
python start.py --load-roster my_roster.sluggiesroster --dry-run
python start.py --load-roster my_roster.sluggiesroster
```

- **Save** reads `3_Output_Dat` and writes the pack; the game files are not
  changed. It stores only the models that differ from the vanilla ones in
  `1_Input`. After an untangle export (menu [1]) that includes most stock
  characters, whose texture bytes the untangling changed, so a pack is a
  few MB.
- **Load** first checks the pack and lists, slot by slot, what differs from
  the game: the same, differs (High / Low model, model directory, front /
  side portrait, name, stats, square voice, square), only in the game, or
  only in the pack. With `--dry-run` it stops there. Otherwise it replaces
  the whole roster: one roster rebuild with the pack's roster when that
  differs, stock slots whose models differ go back to vanilla first, then
  the pack's models are written into their slots as they are (no
  `.sluggie` is needed). Every model of the pack is checked before
  anything is written; a damaged or unfitting pack writes nothing.
- A pack fits any output made from the same `1_Input`: a new ID's own
  model directory is copied afresh from `1_Input` and gets the pack's
  models. Loading into a game that already holds the same roster does
  nothing. Game options (CPU vs CPU) are not part of a pack and stay as
  they are.
- **CPU vs CPU and the Gecko codes:** the game options replace the
  community Gecko codes "CPU vs CPU V2" and "CPU vs CPU human management".
  Disable both Gecko codes in Dolphin before using the options: with them
  on, matches start as normal human vs CPU matches, even on a stock roster.
- **The red Gecko warning:** Gecko codes made for the stock game (CPU vs
  CPU and others) stop working once `main.dol` gets the tools' own code
  area. That happens with game options and with roster changes such as new
  characters, grid or wheel changes, a slot's own voice, and voice pack
  clips. Renames, portraits of stock characters, **Adopt stats...** and
  square voice changes on stock characters, and model patches keep the codes
  working. Before the first
  such change, the tools warn in red: Patch Game's and Inject roster's
  summaries show a red line and a red **Confirm**, and other actions open a
  red popup. The warning appears even with "Skip confirmation popups" on.
  On the grid, slots whose staged edits cause the break get a red border in
  place of the orange one, and a red line in their tooltip and slot details.
  The red marks only show while the output game is still compatible.
  **Cancel** writes nothing. Maintenance → Scan shows whether the output
  game still works with these Gecko codes. The All-In-One Export leaves the
  game options off; turn them on in the Options tab.
- **Player memory** (Options tab, or `--game-options --on player_heap_32`,
  `_48`, `_64` or `_128`): every player on the field gets a fixed memory area for its
  High model, Low model and bat or glove, 870,400 bytes (850 KB) in the
  stock game. A player whose three files plus about 400 bytes don't fit
  crashes the game as soon as it is loaded onto the field (character select
  is not affected). Batters carry the smaller bat, so an oversized character
  may only crash once it has to field. The option raises the area by 32, 48,
  64 or 128 KB per player. The extra memory comes from the game heap that
  the stadium, effects and animations share, 13 times the increase (13
  players can be on the field). +48 KB fits a cautious estimate of what stock
  console memory leaves room for; +64 KB goes slightly past it, +128 KB well
  past it (the GUI marks it risky), which may crash in a heavy scene, for
  example when a run is scored. Tested in Dolphin (2026-10-10): +32 KB let a
  Mario whose High model crashed every time at 850 KB play; +48 KB held a
  Mario that fills it to within 4 KB, which +32 KB crashed with; +128 KB
  played a full match in Mario Stadium. Only one level can be on; choosing
  another replaces it. The details are in `_docs_roster/RosterExpansion.md`
  (*Game memory*).
- **Memory warnings:** after a patch, a slot edit, a roster load or a
  Player memory change, the log warns about every character whose High +
  Low + bat/glove no longer fits the player memory of the output game (on
  a 128 MB level: when it fits only with the override). The GUI's
  **Maintenance** tab checks every character the same way (**Player
  memory**) and lists stadium models bigger than the largest stock one
  (Wario City, 2,655,048 bytes; **Stadium memory**). A stadium has no fixed
  limit, its extra bytes come out of the memory the whole match shares, so
  that one is a note about untested sizes, not a known crash.
- **Memory check before writing:** slot edits (patches, High/Low picks,
  bats and gloves, paste, clear) and roster pack loads are checked before
  anything is written. After the build checks, the tool adds up what each
  changed slot will hold (High + Low + largest bat/glove + about 400 bytes,
  with the sizes the build checks measured) and compares it with the
  player memory level selected in the Options tab. A 128 MB level counts
  with its full size, since choosing it means you run the game with the
  override. A slot that won't fit gets red lines in the staging dialog and
  in Patch Game's summary, and the OK button becomes a red **Stage
  anyway** / **Patch anyway**. From the command line the tool asks before
  writing (`--accept-memory-over` skips the question). Lowering the player
  memory level in the Options tab asks the same way when a character that
  fits now would no longer fit. To get past a refusal, raise the player
  memory or make the models smaller.
- **Player memory with 128 MB MEM2** (Options tab: tick "I have manually
  increased the MEM2 limit in Dolphin to 128 MB"; or `player_heap_big_512`,
  `_1024`, `_2048`, `_4096`): levels of +512 KB to +4 MB per player, for
  Dolphin with **Config > Advanced > Emulated Memory Size Override** and MEM2
  at 128 MB. The game ignores extra memory on its own, so these levels also
  grow its main MEM2 heaps by 64 MB. When the game starts it checks the
  memory size: without the override (or on a real Wii) it keeps the stock
  heaps and uses +48 KB player memory, so the game still starts, but models
  bigger than that crash there. Tested in Dolphin (2026-10-10): +4 MB played
  a full game with the override; without it the game started and played on
  +48 KB, and a model too big for +48 KB crashed.
- Slots that are only in the game leave the grid. A model patched into a
  stock slot among them stays in its directory (it is not on the grid any
  more).

In the GUI, the **Character grid** tab has **Save roster...** and **Load
roster...** (packs go to the `_gui/Roster_Packs` folder by default). Saving with
pending edits asks first: they are not written to the game yet, so they are
not in the pack (press **Patch Game** first to include them). Loading shows
the differences (tick off **Only differing slots** to see every slot);
**Stage** makes the load a pending edit like the others (slots it changes get
the orange border), and **Patch Game** checks the pack again and replaces the
roster with it. A pack load replaces everything, so other pending edits are
discarded first (it asks), and the slot buttons wait until the load is
written or discarded.
For the rest of the session, slots that differ from the pack you saved or
loaded last get a blue border and a "changed since" line.

## Configuration files

All keys are optional. A missing or `null` key leaves that part stock.

```json
{
  "version": 1,
  "ids": [
    {"id": "0x66", "template": "0x06", "swatch": "purple",
     "icon": {"side": "purple_yoshi_side.png", "front": "purple_yoshi_front.png"},
     "name": "Purple Yoshi"},
    {"id": "0x67", "template": "0x04", "wheel": null,
     "icon": {"side": "empty_slot_side.png", "front": "empty_slot_front.png"}, "name": "Empty slot"}
  ],
  "wheels": [
    {"id": "0x47", "wheel": "0x06", "swatch": "black",
     "icon": {"side": "black_yoshi_side.png", "front": "black_yoshi_front.png"},
     "name": {"en": "Black Yoshi", "fr": "Yoshi noir", "sp": "Yoshi negro"}}
  ],
  "wheel_order": [["0x06", "0x66", "0x47"]],
  "grid": {"shape": [12, 4], "squares": [["0x67"]]}
}
```

IDs are numbers or hex strings (`"0x66"`).

### `ids`: new characters (`0x66`–`0xFE`)

| Key | Meaning |
|---|---|
| `id` | The new ID. Optional (the next free one), but needed when the entry has an `icon` or `name`, or sits on a grid square. |
| `template` | A stock character (`0x00`–`0x4C`). The new ID copies its model, animations, stats and voice. |
| `stats` | A stock character (`0x00`–`0x4C`) whose stats the new ID plays with instead of the template's. |
| `wheel` | Whose colour wheel it joins; default the template. A character without a wheel gets one. `null`: no wheel; the ID must then be on a new grid square. |
| `swatch` | Wheel swatch colour: `red`, `blue`, `yellow`, `green`, `purple`, `black`, `brown`, `lightblue`, `pink`, `white`, `orange`, or 0–10. Default: the template's. |
| `icon` | Own portrait: `{"side": "…png", "front": "…png"}` from `1_Input/_Icons` (the open slots use `empty_slot_side.png` / `empty_slot_front.png`), or `{"model": "<.sluggie or model folder>"}`: that model's exported `icon/SideIcon.png` and `icon/FrontIcon.png` (a Low model uses its High partner's folder; a relative path counts from the repository root; both files must exist). Optional `fit` (`contain`, `cover`, `strict`) and `like` (a stock ID whose icon records are copied). Without it the template's portrait shows. |
| `name` | A string, or `{"en": …, "fr": …, "sp": …}`; missing languages use English. Without it the ID shows "-" once any character in the config has a name, and otherwise no name text and the template's name plate. |

### `wheels`: the unused characters (`0x47`–`0x4C`)

| Key | Meaning |
|---|---|
| `id` | `0x47`–`0x4C`. |
| `wheel` | Whose wheel it joins; default its stock family. |
| `swatch` | As above; default its stock swatch. |
| `icon`, `name` | As for `ids`. |

With a `wheels` key the config owns all six rows: unlisted ones go back to
stock (not selectable).

### `stock_icons`: new portraits for stock characters (`0x00`–`0x46`)

A list of `{"id": …, "icon": …}`; `icon` as for `ids` (no `like`: the
character keeps its own icon records, which are only pointed at the new art).

```json
"stock_icons": [{"id": "0x01", "icon": {"model": "2_Output_Assets/19 Luigi/82188352_luigi.gpl"}}]
```

The unused characters (`0x47`–`0x4C`) take their icon on their `wheels`
entry instead.

### `stock_stats`: other stats for stock characters (`0x00`–`0x4C`)

A list of `{"id": …, "stats": …}`: the character `id` plays with the stats of
the stock character `stats` (see "Voice and stats").

```json
"stock_stats": [{"id": "0x0D", "stats": "0x09"}]
```

### `stock_voices`: other voices for stock squares

A list of `{"id": …, "voice": …}`. `id` is a stock square, named by the
character it shows (`0x00` Mario, `0x0D` Toad, `0x01` Luigi, …); `voice` is
the character shown on the square whose voice it takes. The whole wheel of
that square speaks with it.

```json
"stock_voices": [{"id": "0x00", "voice": "0x09"}, {"id": "0x09", "voice": "0x00"}]
```

### `character_voices`: a voice of one slot's own

A list of `{"id": …, "voice": …}`. `id` is any slot on the grid (a stock
character, an unused character on a wheel, a new ID; not a Mii); `voice` is
the character shown on a stock square whose voice it takes. The slot speaks
with it on the select screen and on the field whatever its square speaks
with; the voice's clips are copied into the slot's voice bank in the sound
archive (needs `1_Input/sound_NA/MY2.brsar`).

```json
"character_voices": [{"id": "0x8F", "voice": "0x09"},
                     {"id": "0x90", "voice": "0x09", "clips": {"v1": "cheer.wav", "v13": "name.rwav"},
                      "pack": "Rosalina"}]
```

`clips` (optional) names a clip file per voice moment (`v1` … `v14`, see
"Voice and stats"): a WAV of any format (made mono, 32 kHz, at most 2.5 s and
encoded when the roster is built) or an encoded `.rwav`. A relative name is
looked up beside the config file (derived configs keep their clips in
`clips/`); an absolute path works too. With `clips`, `voice` is optional: the
moments without a clip keep the stock voice given, or the slot's own species
voice without one. `pack` is only a label (the voice pack's name, shown by the
GUI).

### `species_clips`: other clips for a stock voice

A list of `{"id": …, "clips": …}`. `id` is a stock square (by the character it
shows); `clips` as above. The moments named are replaced for every character
who speaks with that voice: the square's whole wheel, any square that took the
voice with `stock_voices`, and any character voice that copies it.

```json
"species_clips": [{"id": "0x00", "clips": {"v1": "mario_cheer.wav"}}]
```

### `wheel_order`

Lists of IDs, one list per wheel: those members come first, in that order.

### `grid`: the exhibition draft grid

| Key | Meaning |
|---|---|
| `shape` | `[columns, rows]`: 11×4, 12×4, 10×5, 11×5 or 12×5. Default: the smallest that holds the 41 stock squares and your new ones. |
| `squares` | New squares, each a list of 1–10 IDs; the first is shown on the square. On the draft screen they leave their family wheel and form the square's own wheel. A square may also be `{"members": [...], "voice": "0xNN"}`: its members without a wheel speak with that character's voice. |
| `order` | Every cell in reading order (a flat list or one list per row): a stock square by its character's ID, a new square by its first member, `null` for an empty cell. Default: the stock 10×4 block in place, the new squares and Luigi in the free cells (left column, right column, bottom row). |

`"grid": {}` gives the 41 stock squares on 11 columns: Luigi gets his own
square, so a captain's square is no longer handed to Luigi. Cells without a
square are hidden and skipped by the pointer and the D-pad.

## Limits and costs

- At most 10 members per wheel, 60 squares, IDs `0x66`–`0xFE` (153). The IDs
  are one byte, so not every character can reach 10: the 30 characters without
  a wheel would need 270 IDs on their own. Preset 03 uses 123, preset 04 142.
- The expansion adds data the game keeps in memory during a match. The icon
  bank: identical portraits are stored once, and the two portrait pages grow
  in power-of-two steps (7 portraits cost about 33 KB, all 159 of preset 04
  under 600 KB). The select-screen layout grows by up to a few tens of KB.
- Only the exhibition draft is covered. Toy Field, minigames and Free practice
  use another character screen, which is not expanded.
- A character voice copies 11 clips (about 100–250 KB) into the voice bank of
  the slot's species, which the game loads whenever that species plays. Several
  voices on one species add up; the largest bank known to work held about
  1.16 MB of clips (the Maintenance tab warns past that).
- A custom clip adds its own size (about 18 KB per second of audio) to the
  same bank; a voice pack's clips are added once per species that uses it.

## Voices tab: voice packs

The GUI's **Voices** tab lists the game's voice packs (one per stock voice: its
12 moments, with Play) and your custom packs (`_gui/Sound_Packs/<name>/`, one folder
per pack with the encoded clips and a copy of each prepared WAV).

- **Game voice pack**: **Replace...** on a moment picks a sound file (WAV, or
  MP3, OGG, FLAC, ... with ffmpeg; see "Sound tools: volume, gain, ffmpeg") or a `.rwav`,
  encodes it and stages a replacement for everyone who speaks with that voice
  (the character grid's pending list; **Patch Game** writes it). **Reset** drops
  the pending edit or stages the stock clip back. Replaced moments show blue,
  pending ones orange.
- **Custom pack**: **New pack...** creates one; **Replace...** fills a moment
  from a WAV at once (nothing to stage: a pack is a library), **Reset** empties
  it, the **Base voice** combo picks the stock voice its unfilled moments come
  from. **Rename...**, **Delete**, **Export...** (one `.sluggiesvoice` file,
  which **Import pack...** reads back) manage the pack. A pack reaches the game
  through the character grid: open a slot, **Voice...**, pick the pack. After a
  clip changed, **Stage for its slots** stages the pack again on every slot
  that speaks with it.

Roster packs (`.sluggiesroster`, format 3) carry the custom clips, so a saved
roster plays the same voices anywhere; loading one shows "own voice clips" and
"voice pack clips" among the differing fields.

## Sound FX tab

The GUI's **Sound FX** tab lists the game's 345 sound effects: the wave sounds
of the sound archive outside the voice banks (bat and ball, menus, stadiums,
minigames, Challenge mode). Each row shows the sound's ID, name, archive group,
length and format, with **Play**, **Replace...** and **Reset**; the
filter box narrows the list by name or group. Replaced sounds show blue.

- **Replace...** picks a WAV (or MP3, OGG, FLAC, ... with ffmpeg) and encodes
  it in the stock sound's shape: its channels (mono or stereo), its rate, its
  loop (a WAV `smpl` loop is kept; else a looping sound loops from the start).
  The result is kept in `_gui/Sound_FX/` (one `.rwav` per sound plus
  `effects.json`) and written into `3_Output_Dat/sound_NA/MY2.brsar` at once;
  **Patch Game & copy files** copies it into the game, and every **Patch Game**
  run puts the replacements back into the archive it rebuilds.
- **Reset** removes the replacement and puts the stock sound back.
- **Play stock** (replaced rows) plays the original for comparison.
- A sound's archive group loads into the game's sound memory whole, so keep a
  replacement near the stock length; the status line notes a wave more than
  twice the stock size. Sounds are cut at 30 s. As of 2026-10-12, replaced
  effects have not been tested in the game, so check yours in Dolphin.
- The 119 sequence sounds (for example the bat hits `bb_hit_m`) play
  instrument banks rather than one recording and are not listed.

## Music tab

The GUI's **Music** tab lists the game's 78 music tracks and jingles (the
stream sounds of the sound archive) in one list: name, `.brstm` file, length,
format, and **Play**, **Stop**, **Replace...**, **Reset**. It reads the output
first: `3_Output_Dat/sound_NA/MY2.brsar` when it exists, and each track from
`3_Output_Dat/sound_NA/stream/` when a file is there, else from
`1_Input/sound_NA/`. Replaced tracks show blue.

- **Play** plays a track once (the first Play of a track decodes it, a moment);
  any **Stop** stops every playback, voice clips included.
- **Replace...** picks a WAV (any rate, mono or stereo; or MP3, OGG, FLAC, ...
  with ffmpeg) and encodes it in the stock track's format (stereo, its 32,000 or 44,100 Hz, same block layout), so
  only the `.brstm` changes. It is written to `3_Output_Dat/sound_NA/stream/`
  at once (nothing to stage); **Patch Game & copy files** (CopyFilesToGameDir)
  copies it into the game. A loop in the WAV (`smpl` chunk, as the sound export
  writes) is kept; otherwise a track that loops in the game loops from the
  start. Encoding takes a few seconds per minute of music (a spinner shows
  meanwhile); quality is about that of the stock tracks.
- **Reset** writes the stock track to `3_Output_Dat/sound_NA/stream/` again, so
  the next copy restores the game's.
- Playback (all sound tabs) works on Windows only.

## Sound tools: volume, gain, ffmpeg

The **Voices**, **Sound FX** and **Music** tabs share these:

- **Volume** column and **Set volume**: every sound in the archive has a volume
  of 0–127 (127 is the loudest; it cannot make a sound louder than its
  recording). Most stock sounds use 107, music sits lower (36–117). The column
  shows the volume the game has now, blue with the stock value beside it when
  changed. **Set volume** writes it into `3_Output_Dat/sound_NA/MY2.brsar` at
  once (nothing to stage) and keeps it in `sound_volumes.json` in the tool
  folder, so every **Patch Game** run applies it again. Setting the stock value
  removes the entry. A character voice that copies a stock clip keeps that
  clip's stock volume.
- **Replace gain** (toolbar, dB, -24 to +24, default 0) makes the file the next
  **Replace...** encodes louder or quieter: +6 dB about doubles the level, -6 dB
  halves it. Peaks pushed past full scale are clipped, and the log says so. An
  encoded `.rwav` is taken as it is. To make a sound louder than the stock
  one, use the gain; the volume can only make it quieter.
- **ffmpeg** lets **Replace...** read MP3, OGG, FLAC and other formats besides
  WAV. The Maintenance tab's **Sound** section says whether it is installed and,
  if it isn't, offers **Install ffmpeg (winget)** (`winget install
  Gyan.FFmpeg`). A loop is read from WAV files only (their `smpl` chunk).

## Exporting the game's sounds

```
python start.py --export-sounds
```

(or the GUI's "Export sound files" checkbox) decodes every sound in
`1_Input/sound_NA/` into `2_Output_Assets/_SOUNDS/` as 16-bit WAV files, one
folder per archive group (`group_v001`... the voice banks, `group_sel_v` the
select-screen name calls, `group_bb` the baseball effects, `group_st*` the
stadiums), music and jingles in `stream/`. `index.json` lists every sound
with its ID, name, group, rate, length and loop. Looping sounds keep their
loop in the WAV, so an edited export can go back through **Replace...** with
its loop intact. Sequence sounds (such as the bat hits) have no single
recording and are listed in the index only.
