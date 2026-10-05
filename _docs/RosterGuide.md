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
   | `02_Stock_and_Unused.json` | The six unused characters (Black Yoshi, White Yoshi, Black Toad, Black Pianta, Black Kritter, Black Koopa) on their families' wheels, with the icons from `1_Input/_Icons`. |
   | `03_Unuseds_and_10_slot_colors.json` | Preset 02, every colour wheel filled to 10, and a new wheel of 3 for each of the 30 characters without one, all with **open slots** (123 new IDs). |
   | `04_all_in_one_12x5_grid.json` | Preset 03 on a 12×5 grid: Luigi gets his own square, and 19 new squares hold open slots. |

   Your own configurations go into the same folder and show up in the list.

3. Copy `main.dol`, `dt_na.dat` **and `fst.bin`** into the game.

From the command line: `python start.py --roster --config <file>` (or
`--roster --remove`).

Every choice first resets the roster to vanilla, so you can switch presets
freely. **[r]** (`--remove`) only does that reset. It works like the model
unpatcher: the original bytes come from `1_Input/main.dol`, so `1_Input` must
hold the original game files. Your model patches and untangled routes stay as
they are; the roster's copies in `dt_na.dat`'s extra space are zeroed, and the
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
python start.py --patch-slot 0xE2 "2_Output_Models/27 Bowser/114968608_koopa.gpl/114968608_koopa.gpl.sluggie"
python start.py --clear-slot 0xE2
```

The slot decides where the model goes, not the `.sluggie`'s own chunk. Picking
the High model's file or its Low partner's (folder name with `_L_`) patches both when the other one is in its
sibling folder. `--dry-run` prints what would happen and builds and checks the
models, but writes nothing.

| Slot | What `--patch-slot` does |
|---|---|
| New ID (`0x66`–`0xFE`) | Gets its own copy of the source character's files (model, animations, bat); a slot that already holds that character's files keeps them, so earlier patches stay. On a new grid square it takes the source's stats, and the square's voice becomes the source's if none is set yet. An "Empty slot" takes the source's name. |
| Stock character | Model only (stats, voice and name stay). The source must have the same skeleton; otherwise use a new ID. |

Both kinds take the source's exported portraits (`icon/SideIcon.png` and
`icon/FrontIcon.png` in its High model's folder); when one is missing, the slot
keeps its portraits. A High model without a Low partner is used as
the Low model too, which loads it twice on the field. A Low model alone is
refused on a new ID; on a stock character it is accepted only when the slot's
High model is its own partner.

`--clear-slot` gives a stock character its vanilla models, portraits and
own stats back, and a new ID a fresh copy of its template's files, the
template's stats, the "Empty slot" name and portraits. The square's voice
stays.

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

## Several changes at once

Several changes can go in one run with an edits file, a JSON list of
`patch`, `clear`, `rename`, `voice` and `stats` edits (a `rename` edit's
`text` may be blank; a `voice` or `stats` edit's `source` may be `null` for
the default):

```
{"edits": [{"op": "patch", "id": "0xE2", "file": "2_Output_Models/27 Bowser/114968608_koopa.gpl/114968608_koopa.gpl.sluggie"},
           {"op": "clear", "id": "0x1D"},
           {"op": "rename", "id": "0x0D", "text": "Little Toad"},
           {"op": "voice", "id": "0x0D", "source": "0x09"},
           {"op": "stats", "id": "0x0D", "source": null}]}
```

```
python start.py --apply-slots edits.json --dry-run
python start.py --apply-slots edits.json
```

A later edit for the same slot replaces an earlier one of the same kind (a
later voice edit replaces an earlier one of the same square; a clear also
drops the slot's earlier renames and stats edits, since it resets both). A Low model picked
after a High model of the same character for the same slot joins it as a
pair. If any edit is refused, nothing is written and the output names the
refused edits.

In the GUI, the **Character grid** tab does the same: click a square, then a
slot, and use **Rename...** (or click the slot's name), **Select .sluggie...**,
**Clear slot** or **Stats...**. The square view has **Voice...** (a
one-member square shows it on the slot view). The rename dialog tells you
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
- Slots that are only in the game leave the grid. A model patched into a
  stock slot among them stays in its directory (it is not on the grid any
  more).

In the GUI, the **Character grid** tab has **Save roster...** and **Load
roster...** (packs go to the `Roster_Packs` folder by default). Saving with
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
"stock_icons": [{"id": "0x01", "icon": {"model": "2_Output_Models/19 Luigi/82188352_luigi.gpl"}}]
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
