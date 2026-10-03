# Roster Expansion: Format and Code Reference

How SluggiesTools extends Mario Super Sluggers' (US, `RMBE01`) character
roster: new character IDs, colour wheels of up to 10, a larger exhibition
draft grid, icons and names. Addresses are US `main.dol` virtual addresses;
all values are big-endian. The user-facing workflow is in
[`../RosterGuide.md`](../RosterGuide.md).

**Sources and dates.** Static analysis of `main.dol` and `dt_na.dat`
(September–October 2026), cross-checked against the Sluggers Characters Beta
tool (a third-party patcher whose code we may reuse). Its word-level patches
were compared site by site with our build (`SluggiesTools/Dol/site_inventory.json`,
687 sites). Everything marked *Dolphin* was confirmed in game on 2026-10-03
unless another date is given.

Code: `SluggiesTools/Roster/` (one module per step, run in this order by menu
[10] / `start.py --roster-dev`): `dol_hammerspace`, `layout_file`, `ids`,
`wheels`, `icons`, `grid`, `names`. `runner.py` records every DOL byte and
`dt_na.dat` write of a run in `3_Output_Dat/roster_dev/report.json` and undoes
the previous run before the next one.

## Character IDs

| IDs | Meaning |
|---|---|
| `0x00`–`0x46` | stock players (71) |
| `0x47`–`0x4C` | the six unused characters (spare rows; own model data in dt_na dirs 89–94) |
| `0x4D`–`0x64` | Miis (two groups of 12) |
| `0x65` | "no character" sentinel |
| `0x66`–`0xFE` | new IDs (153) |
| `0xFF` | ends ID lists |

A new ID plays as its **template**, a stock character whose row it copies in
every per-ID table. 24 tables move to the DOL hammerspace data section with
256 rows each (selector, stats, pitching, catching, hitbox, size and effect
scales, model handles and others). The game's ID range checks are widened to
`0xFF`, and about 15 hooks (roster list, family path, model resolver and
directory map, portrait tests, chemistry, team list, charge-effect stack
copies) let new IDs through. Two tables stay where they are on purpose:
`0x806314E8` (read only by a `< 0x4D` loop) and `0x80630A28`.

**Own character data.** `0x806B4970[id]` is a flag, not a selectable table:
`0x80367060` returns `own_data[id] ? id : 4`, so an ID with 0 there plays as
**Peach (ID 4)** on the field (model, animations, stats, voice; *Dolphin*
2026-09-25). The spare rows need it set to 1; new IDs copy their template's.

## DOL hammerspace

Two sections added to `main.dol`: code at `0x807B7000` (0x9000 bytes) and data
from `0x807C0000`. OSInit's arena-low constants (two `lis`/`addi` pairs) move
up behind the data section; arena low may rise to `0x808BF000`. Each section
starts with a 16-byte magic so later runs can reopen and extend it.

## DAT hammerspace

Grown files are copied past the stock end of `dt_na.dat` (715,046,144 bytes)
into the first 32-aligned zero run that no DOL record uses; the file grows
when nothing fits, and `fst.bin` gets the new size (offset `0x14`). A DOL
directory record is 48 bytes: three language slots of (length, offset,
allocation). **The slots are English, French, Spanish** (slot 2 holds
"Toad rouge", slot 3 "Toad rojo" in the name table).

Files the expansion moves: the select-screen layout (dir 119 file 19 = dir 0
file 1591, record `0x806920B8`, three copies of `0x138E50` bytes), the icon
bank (dir 0 file 1574, record `0x68DE88` in file offsets) and the name table
(dir 121 file 5). Dir 119 files stay resident in the MEM2 game heap during a
match, so their growth counts against the match's memory.

## Colour wheels

**Selector rows** (`0x80631550`, 8 bytes per ID): wheel group, host,
species, captain flag, flags, slot, **selectable**, swatch. A wheel is all
selectable rows of one species. The roster builder `0x8006BA6C` scans IDs
below `0x4D` into the roster struct: count at `X + 0x4D + species`, list at
`X + 0x76 + species × 10` (10 per species). New IDs are appended by a hook at
its end.

**Up to 10 members** (*Dolphin*): the member-list caps `0x80071ECC` and
`0x804303CC` (`cmpwi …,6`) become 10; 19 functions' stack buffers that hold a
member list grow by `0x10`; four `stb r27,off(r4)` sites map member index 6+
to popup node 9+; popup element `0xB3` of the select layout gets frames for
7–10 members (its info block's last time becomes 8). Seven members only need
the caps.

**Swatches**: elements `0xAD`/`0xAE` have one key per swatch colour (time =
selector byte 7, four vertex colours at `+0x40`); time 10 is an unused white
key, recoloured orange for swatch 10.

**Wheel order**: a hook after the roster hook rewrites a species list into a
configured order.

## Icons

**Icon bank** (dir 0 file 1574): a texture section, then a container.
Header `+0` `0x20`, `+4` container offset (end of the textures), `+0x20`
texture count; descriptors (0x20 bytes) from `+0x24`.

- **Descriptor image and palette offsets count from bank `+0x20`**, not from
  the file start: the stock offsets start at `0x1260` (file `0x1280`), the
  first 32-aligned byte after the descriptor table.
- The container's descriptor (container `+0x14`) holds signed pointers,
  relative to itself, to the resource table (`+0x04`) and to the three
  source tables normal_a (`+0x08`), side (`+0x0C`) and front (`+0x10`).
  Container `+0x10` is its end.
- **Source tables**: header `0x28` bytes (`+0x08` length, `+0x18` last frame,
  `+0x24` count), records `0x50` bytes: `+0x02` character ID (the key frame),
  `+0x06` resource row. Records are sorted by ID, highest first; every record
  but the first has flag `0x0100`. A view shows the record with the highest
  ID ≤ the character, so an ID without its own record shows a neighbour's
  icon (*Dolphin*: the unused characters showed Pink Yoshi `0x46`). Each
  table's last frame must cover the highest key. Side records differ only in
  byte `+0x26` (`0x02` for IDs 0x01, 0x08, 0x0B, 0x27, 0x3E–0x41, else `0x82`).
- **Resource rows** (`0x14` bytes): u16 page, u16 0, f32 v1, u1, v2, u2.
- **Keys alone are enough** to give an ID its own icon (*Dolphin*); no
  runtime hook is needed.
- Our bank: the stock texture section, then page `0x86`'s palette, then two
  CMPR pages (`0x92` side, `0x93` front) holding each distinct 48×51
  portrait once in a 52×52 cell (the smallest power-of-two page that fits),
  then the stock container with the rows appended. The source tables sit in
  free rows of a stock atlas at `0x87520` while they fit (about 400 new
  records), else after the pages; page images and source tables must stay
  inside the texture section (the external tool found that images placed
  after the container draw garbage). 8 icons cost 33,600 bytes over stock.
- New descriptors for pages `0x92`/`0x93` occupy file `0x1264`–`0x12A4`,
  which covers the first 36 bytes of page `0x86`'s palette at `0x1280`, so
  that palette moves.

**Portrait renderer** `0x80395DB0`: IDs ≥ `0x4D` take the Mii path at
`0x80395E1C`. A branch there sends IDs ≥ `0x66` down the normal track path;
a new ID without its own art is aliased to its template at the renderer entry
and its two preview calls.

## Exhibition draft grid

Only the exhibition team draft (layout element `0xBA`) grows. Toy Field,
minigames and Free practice use a second grid (element `0x97`, 41 squares)
that is not expanded, but both screens share the grid widget code, so both
screen objects grow.

**Stock layout:** 40 squares (10×4) for 41 heads. The square → head map
`0x80623458` holds head indices; head *h* is species *h*, and the head list
`0x80631878` gives each head's character. The constructor `0x8006E9C4` hands
the square nearest column 5 of one captain's family to Luigi (head 1), unless
the captain is Luigi or Yoshi. A captain with a wheel therefore lost its
whole square.

**Shapes:** 11×4, 12×4, 10×5, 11×5, 12×5 (at most 60; 12 columns reach the
screen edge, 5 rows the team bars). What changes:

- layout: element `0xBA` rebuilt (49 px pitch, right edge at stock column 9;
  5 rows at y = 105 + 48·row), team bars `0x9A`/`0x9B` stretched, roster slots
  `0xB9`/`0xB8` spread, banner `0xA0` moved, the screen shifted 20/40 px right;
- square counts in `0x80067EF4`, the widget's per-square arrays moved behind
  both screen objects (`0x67C` → `0xC30`, `0x6CC` → `0xB98` bytes);
- the map and per-head arrays moved into the object, filled from one table;
  **the captain swap removed** (Luigi has his own square; *Dolphin*: a
  captain Mario's other Marios stay draftable);
- heads 43+ for new squares (head list moved and grown); the member-list
  builder `0x80071BB0` lists a square's own members, and square members
  leave their family's wheel;
- 191 cursor-position constants, the D-pad's divide (`0x2E8BA2E9` for 11
  columns, `0x2AAAAAAB` for 12), bounds and wraps, the random-team pools;
- a picked square gets "decided" flag 2 (stock: 1 forever), so it shows the
  next member or goes dark;
- empty cells are moved off screen and skipped by the pointer and the D-pad.

A new ID that is only on a square has wheel group 0 and is kept off the
roster's species lists.

## Names

**Text** (dir 121 file 5, one table per language): `u8 1, u8 1, u16 count,
u32 offset[count]` (in UTF-16 units from the text start), then the UTF-16
strings. Message *i* is character *i*'s name; 71–76 are "#N/A", 77–100
"Mii", and 101–103 are formatting codes. New IDs `0x66`/`0x67` would land on
codes 102/103, so the table runs to `0xFE` and the codes move to 255/256;
their readers `0x80486E68`/`0x80486E88` (`lwz r0,0x198(r3)`) and
`0x8047C4C0`/`0x8047C4D8` (`lwz r5,0x19c(r5)`) are repointed. The name widgets
`0x80486EAC`, `0x80489F94` and `0x804923C0` show a name only for IDs below
`0x4D`; their test also passes IDs ≥ `0x66`.

**Name plates** (select screen, batting order and in-match bubbles): images,
resource row `id + 0x149` of the select layout (115×16, page 124). Four sites
compute that row (`0x8006E660`, `0x8007F4FC`, `0x8042BBD8`, `0x8031DC1C`); no
other layout bank has plates at row `0x149`. For new IDs they use rows after
the stock 483, on a new RGB5A3 page per language; adding a page pushes the
image data down 0x20 bytes, so every stock offset moves with it.

*Dolphin*: names show on the draft, the batting order and in the match, in
English, French and Spanish.
