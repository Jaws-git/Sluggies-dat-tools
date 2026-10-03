# Roster Expansion Guide

The roster expansion adds characters to Mario Super Sluggers' exhibition draft:
the six unused characters on their families' colour wheels, wheels of up to 10
members, new character IDs, a larger draft grid with new squares, and your own
icons and names. It works on the output of the normal pipeline
(`3_Output_Dat/main.dol`, `dt_na.dat`, `fst.bin`) and can be removed again.

The binary details are in [`_docs_roster/RosterExpansion.md`](_docs_roster/RosterExpansion.md).

## Quick start

1. Build your normal output first (menu [1], model patches, icons). The
   expansion is applied on top of it.
2. Menu **[10] Roster expansion** lists every `.json` file in
   `1_Input/_RosterConfigurations/`, alphabetically, with a number to pick
   it, and **[r]** to take the previous injection out and stop. The shipped
   presets:

   | File | What you get |
   |---|---|
   | `01_Stock_Roster.json` | No new content. |
   | `02_Stock_and_Unused.json` | The six unused characters (Black Yoshi, White Yoshi, Black Toad, Black Pianta, Black Kritter, Black Koopa) on their families' wheels, with the icons from `1_Input/_Icons`. |
   | `03_Unuseds_and_10_slot_colors.json` | Preset 02, and every colour wheel filled to 10 with **open slots** (63 new IDs). |
   | `04_all_in_one_12x5_grid.json` | Preset 03 on a 12×5 grid: Luigi gets his own square, and 19 new squares hold open slots. |

   Your own configurations go into the same folder and show up in the list.

3. Copy `main.dol`, `dt_na.dat` **and `fst.bin`** into the game.

Every choice first removes the previous injection, so you can switch presets
freely. If the normal pipeline wrote fresh files in between, the old injection
counts as already removed.

**Open slots** are new IDs with nothing assigned yet. They show an "empty slot"
icon and the name "Empty slot", and play as a template character: a wheel's
open slots as that wheel's own character, the new squares' as Peach (the
game's fallback character). Assigning models, sounds and art to them is the
next step of the project; for now you can give them your own icon and name.

To make your own configuration, copy a preset in
`1_Input/_RosterConfigurations/` under a new name and edit it.

## Configuration files

All keys are optional. A missing or `null` key leaves that part stock.

```json
{
  "version": 1,
  "ids": [
    {"id": "0x66", "template": "0x06", "swatch": "purple",
     "icon": {"side": "purple_yoshi_side.png", "front": "purple_yoshi_front.png"},
     "name": "Purple Yoshi"},
    {"id": "0x67", "template": "0x02", "wheel": null, "icon": "placeholder", "name": "Empty slot"}
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
| `wheel` | Whose colour wheel it joins; default the template. A character without a wheel gets one. `null`: no wheel; the ID must then be on a new grid square. |
| `swatch` | Wheel swatch colour: `red`, `blue`, `yellow`, `green`, `purple`, `black`, `brown`, `lightblue`, `pink`, `white`, `orange`, or 0–10. Default: the template's. |
| `icon` | Own portrait: `{"side": "…png", "front": "…png"}` from `1_Input/_Icons`, or `"placeholder"` for the built-in "empty slot" portrait. Optional `fit` (`contain`, `cover`, `strict`) and `like` (a stock ID whose icon records are copied). Without it the template's portrait shows. |
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

### `wheel_order`

Lists of IDs, one list per wheel: those members come first, in that order.

### `grid`: the exhibition draft grid

| Key | Meaning |
|---|---|
| `shape` | `[columns, rows]`: 11×4, 12×4, 10×5, 11×5 or 12×5. Default: the smallest that holds the 41 stock squares and your new ones. |
| `squares` | New squares, each a list of 1–10 IDs; the first is shown on the square. On the draft screen they leave their family wheel and form the square's own wheel. |
| `order` | Every cell in reading order (a flat list or one list per row): a stock square by its character's ID, a new square by its first member, `null` for an empty cell. Default: the stock 10×4 block in place, the new squares and Luigi in the free cells (left column, right column, bottom row). |

`"grid": {}` gives the 41 stock squares on 11 columns: Luigi gets his own
square, so a captain's square is no longer handed to Luigi. Cells without a
square are hidden and skipped by the pointer and the D-pad.

## Limits and costs

- At most 10 members per wheel, 60 squares, IDs `0x66`–`0xFE` (153).
- The expansion adds data the game keeps in memory during a match: the icon
  bank grows by about 1.4 KB per distinct portrait (identical portraits are
  stored once), the select-screen layout by up to a few tens of KB.
- Only the exhibition draft is covered. Toy Field, minigames and Free practice
  use another character screen, which is not expanded.
