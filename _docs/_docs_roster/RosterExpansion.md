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
[9] / `start.py --roster`): `dol_hammerspace`, `layout_file`, `ids`,
`wheels`, `icons`, `grid`, `names`. Before every run, `reset.py` resets the
roster to vanilla against `1_Input` (no record of earlier runs is kept):

- `main.dol` is rebuilt from the input DOL, keeping the output's directory
  records. Those records are the only DOL bytes the other tools write (model
  patches, the untangler). The roster's own three records go back to the input
  too: select layout, icon bank, name table.
- In `dt_na.dat` the roster writes only past the stock end, into the copies
  those three records point at. The reset zeroes them unless another record
  still routes there. The file and its `fst.bin` size stay grown.

## Character IDs

| IDs | Meaning |
|---|---|
| `0x00`–`0x46` | stock players (71) |
| `0x47`–`0x4C` | the six unused characters (spare rows; own model data in dt_na dirs 89–94) |
| `0x4D`–`0x64` | Miis: wheel groups `0x0C` (male) and `0x0D` (female), species `0x29`/`0x2A`, dirs 95–106 and 107–118. A Mii is a base body plus a head built at runtime from the console's Mii data. |
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

**The moved tables** (row size in bytes where it is not 1):

| Address | Contents |
|---|---|
| `0x80631550` | selector rows (8; see Colour wheels) |
| `0x806CE9A0` | stats: 8-byte header, then `0x8E`-byte rows; bytes 0–1 are the row's own ID, chemistry follows |
| `0x80628400`, `0x806288BC`, `0x80628924`, `0x80628EB0`, `0x806289F0` | pitching: windup, star pitch, stamina, change-up, throw floats |
| `0x806291D8` (`0x24`), `0x8062A00C`, `0x8062A15C`, `0x8062A228`, `0x8062A424` | character floats, trajectory and other per-ID parameters (not decoded) |
| `0x8062A688` (`0x28`), `0x8062B678`, `0x8062B874`, `0x8062BA08` | catch range, throw variant, hitbox |
| `0x8062EB50` | size scale |
| `0x806B4970` | own character data flag (below) |
| `0x806250E8`, `0x80624F30`, `0x80624D98`, `0x80624C00`, `0x806252B0` | ice-block, pitch-charge, bat-charge and two effect scales |
| `0x80709408` (bss, `0xC`) | model handles |

The colour-wheel table alone is built by 111 `lis`/`addi` pairs; none points
into the middle of a table and no `lis` is shared with other data, so a table
moves by rewriting every pair. A table that is not grown shows up only as a
bug on new IDs (missing ice block, a ball never released, missing charge
circles), so this list was found table by table; a sweep for further
101-row tables found none. Not per-ID, despite earlier readings:
`0x80630A28` (49-byte pattern records) and `0x80672970` (an effect factory).
The character manager also keeps per-ID heap records (`mulli id,0x68` at
`0x80366FE0`).

**What follows the ID.** On the select screen and in game (*Dolphin*
2026-09-24/25):

| Property | Comes from |
|---|---|
| wheel membership and order | selector byte 2 (species), then character ID order |
| selectable | selector byte 6 (plus the unlock check for IDs `0x36`–`0x46`) |
| swatch colour | selector byte 7 |
| model | the ID's model directory (below) |
| icon, stats, name | the character ID |
| voice, on the select screen and on the field | the species (byte 2): a variant moved to another species' wheel speaks with that species' voice (see Stats, size and voice) |
| everything on the field | the ID after the own-data substitution: an ID without own data is Peach as a whole character |

The select screen does not go through that substitution: it shows the raw
ID's preview model, so a missing own-data byte is only visible once the game
starts.

**Model routing.** A character's model directory is ID + `0x12`
(`addi r4,rN,0x12` at `0x8036625C`, `0x803664E8` (model loader entries),
`0x80376480` and `0x804A519C`/`0x804A5224` (the select-screen model task)).
Directories 18–118 are the 101 character directories (15 files of 48-byte
records each); 119 and up hold other content. A variant is a complete model
of its own, not a palette swap: Red to Pink Yoshi (`0x42`–`0x46`) are dirs
84–88, each a full block. The unused IDs' records point at their wheel host's
block until the untangler splits them. For new IDs these five sites read a
halfword `dirmap[id]`, which gives a new ID its template's directory. Two
tables in the model loader `0x8036629C`, `0x806B49D8` (8-byte rows) and
`0x806B4D00` (12-byte rows), are indexed by directory − `0x12`, not by the
picked ID; they only matter once an ID gets its own directory. The external
tool found that an ID with its own directory needs its own copy of **every**
file: sharing the template's copies made two same-template characters
invisible when both were loaded.

**IDs `0x80` and up.** Team and record code (`0x8018A7D4`–`0x80196414`) loads
IDs with `extsb`, so these IDs turn negative there. *Dolphin*: the shipped
presets (new IDs up to `0xF3`) pass drafting, random picks and full games.
Whether an ID of `0x80` or more was on a team in those games, and the results
and records screens, were not checked specifically.

**Where new IDs do not appear.** The screens behind `0x801C32A0` and
`0x8018BED0` scan only IDs `0..0x46` / `0..0x4C`. Toy Field, Minigames and
Free practice (the colour-wheel select screen) are out of scope: they get
only the patches that keep shared code from overrunning, untested.

**Save data.** The save's unlock bytes cover IDs `0x36`–`0x46` only, and every
other ID counts as unlocked, so new IDs are always available
([`../SaveData.md`](../SaveData.md)).

**Own character data.** `0x806B4970[id]` is a flag, not a selectable table:
`0x80367060` returns `own_data[id] ? id : 4`, so an ID with 0 there plays as
**Peach (ID 4)** on the field (model, animations, stats, voice; *Dolphin*
2026-09-25). The spare rows need it set to 1; new IDs copy their template's.

## Stats, size and voice

Static analysis of `main.dol`, 2026-10-05, with the external tool's voice
routing notes (`_docs/Custom Character Sounds Pipeline - Rosalina Luma
Larry.txt`) as the starting point.

**Voice is the species.** Every voice path takes the family from selector
byte 2 of the character ID (the ID in the player's stats row, bytes 0–1):

| Path | Code | What byte 2 picks |
|---|---|---|
| voice bank load | `0x803868AC`, `0x80233014`, `0x80233368` → `0x804B231C` | the sound group `0x80631F10[species]` (u32, 48 entries; `-1` for the Mii groups `0x29`/`0x2A`: no voice) |
| voice clip | wrapper `0x804B2898`, r5 = species; callers `0x80386FA4`, `0x80388694`, `0x80387104`, `0x804A5628` | the clip `0x80631FD0[species × 0x30 + slot × 4]` (12 sound INFO IDs per species) |
| family search | `0x80387028` (27 callers) | the first player of both teams whose byte 2 equals the requested family speaks |
| select-screen voice | `0x804A5628` (slot 10, `v13`) | as the clip row; all select voices are in one shared group |

So a character speaks with the species of its byte 2 everywhere, and the
game loads that species' bank for it: there is no separate per-ID voice. The
external tool gives its new characters own clips by a different route
(exact-ID tables behind hooks at `0x804B2898`/`0x804B2910`, the clips added
to the template family's bank in `MY2.brsar`); that needs sound archive
edits, which SluggiesTools does not make.

**Byte 2 is more than the voice.** It has about 83 readers. Besides wheels,
squares and voice, gameplay code tests a few species directly: `0x19`
(Magikoopa family; `0x800AEB4C`, `0x800B3278`, `0x80148100`, `0x8014A20C`,
`0x80153C04`), `0x16` (Noki family; `0x801650C4`), `0x24` (Kritter family,
together with ID `0x3E` K. Rool; `0x80387F3C`, `0x80388098`, `0x8038820C`,
`0x803888F4`), and `0x29` (Miis). What these branches do is not decoded. A
character whose byte 2 names one of these species while its model and
animations come from another (or the reverse) may behave oddly; untested.

**Square voice.** On the exhibition draft a new square's members are listed
by ID (the grid's member-list hook), not by species, and a square-only new ID
(wheel group 0) is on no species list. Its byte 2 is therefore free: the
roster sets it to the square's voice. Every other member keeps its byte 2:
a spare row or stock ID is on its species' list (roster builder
`0x8006BA6C`, and the Toy Field wheels), and a new ID with a wheel is
appended to its wheel's species list.

**Stats and size.** A new ID copies one row per moved table. The rows split
into what the character plays like and what belongs to its body:

| Group | Tables |
|---|---|
| stats | stats (`0x8E` rows, chemistry included), pitch windup, star pitch, stamina, change-up, trajectory, catch range, character floats (`0x806291D8`, incl. the strike-zone height at `+0x1C`), throw floats, throw variant, `perid2`/`perid4`/`perid5a`/`perid6` |
| body | size scale, hitbox (body cylinder radius/height), ice-block scale, pitch- and bat-charge scales, the two effect scales |

The body tables size the model and its effects (the size scale scales
skeleton and mesh together, `FUN_80367080` → `FUN_80382558`), so they follow
the ID's model: its own directory's source, else the template. Within the
vanilla colour families (same body, different stats) the body tables are
identical except for the two Paratroopas' size scale and hitbox. The
`perid*` and throw-variant rows are not decoded; some hold frame counts
(they pass through the 50/60 Hz converter `FUN_804B9BD4`) and may belong to
the animation set rather than to the stats. Chemistry: a new ID's stats row
holds its chemistry towards the stock IDs; two new IDs use their stats
sources' pair.

## DOL hammerspace

Two sections added to `main.dol`: code at `0x807B7000` (0x9000 bytes) and data
from `0x807C0000`. OSInit's four arena-start `lis`/`addi` pairs move up behind
the data section; arena low may rise to `0x808BF000`. Each section starts with
a 16-byte magic so later runs can reopen and extend it.

The stock DOL uses 10 of its 18 section slots (T0, T1, D0–D7); T2–T6 and
D8–D10 are free, and new sections are appended at the file end (`0x716F20`).
Memory above the loaded image:

| Range | What |
|---|---|
| `0x80706D00`–`0x807A4E80` | bss (the end is the highest loaded address) |
| `0x807A4E80`–`0x807B4E80` | main-thread stack (grows down; canary `0xDEADBABE` at its bottom). Not a cave, although it usually reads as zeros. |
| `0x807B4E80`–`0x807B6E80` | debugger stack. `0x80612D80` also uses `0x807B6E80` as a stack top, so its frame writes land just above it: new sections start at `0x807B7000`. |
| `0x807B7000`– | our text, then data section; the MEM1 arena follows |

**Arena start** (OSInit `0x80595EB4`): the low-memory value at `0x8000310C`,
else BootInfo `0x80000030`, else one of two code constants: `0x807B4E80`
(`lis`/`addi` at `0x80596014`, the retail path) or `0x807B6E80`
(`0x80595FC4`). `0x8059606C` and `0x805960A0` repeat both for a MEM2 arena;
all four are patched. *Dolphin* (2026-09-24): the boot leaves both low-memory
values at 0, so the patched constants decide (`bl OSSetMEM1ArenaLo` at
`0x8059602C` receives them). Real hardware and Riivolution are untested.
**Ceiling:** the game creates a fixed 15 MB MEM1 heap at arena low +
`0x4006C`, which must end below arena high, hence `0x808BF000` (measured by
the external tool in six RAM dumps): about 1 MB of DOL hammerspace. All
tables for 153 new IDs take about 80 KB, almost half of it stats.

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

**Selector rows** (`0x80631550`, 8 bytes per ID). A wheel is all selectable
rows of one species. Which code reads each byte:

| Byte | Meaning | Readers |
|---|---|---|
| 0 | wheel group; 0 = no wheel | 13, almost all zero tests ("has a wheel"); the base character's row needs it non-zero too |
| 1 | host character ID | 1 (`0x80234010`, never hit): it picks neither square, wheel nor voice |
| 2 | species `0x00`–`0x28` (`0x29`/`0x2A` Mii groups) | 84: the main key (wheel membership, voice) |
| 3 | captain flag | none |
| 4 | flags, 0 on every stock row | 4 zero tests |
| 5 | slot in the wheel | none: the order is character-ID order within the species |
| 6 | **selectable** | the roster builder |
| 7 | swatch colour | 3 |

Stock wheel groups (base ID, selectable/total members): `01` Koopa `0x0C`
(2/3), `02` Toad `0x0D` (5/6), `03` `0x10` (5/5), `04` Paratroopa `0x14`
(2/2), `05` Pianta `0x15` (3/4), `06` `0x18` (3/3), `07` `0x1B` (3/3), `08`
`0x21` (4/4), `09` `0x30` (4/4), `0A` Kritter `0x3A` (4/5), `0B` Yoshi `0x06`
(6/8). A character without a wheel gets the next free group, `0x0E` and up.
`0x80631878` (right after the table) maps species to its base character ID.

**A spare row** (`0x47`–`0x4C`) joins a wheel with byte 6 = 1, bytes 0 and 2
set to the host's, byte 7 the swatch, and its own-data byte set to 1.

The roster builder `0x8006BA6C` scans IDs below `0x4D` into the roster struct
(embedded in both select-screen objects, `0x210` bytes): availability per ID
at `X + 0x00` (`0x4D` bytes, from byte 6), count at `X + 0x4D + species`, list
at `X + 0x76 + species × 10` (10 per species). New IDs are appended by a hook
at its end. The wheel-list builders treat the Mii range as always available;
raising its upper bound `0x64` to `0xFF` (`0x804302CC`, `0x804304C0`,
`0x80071B90`) makes new IDs available the same way.

**Up to 10 members** (*Dolphin*): the member-list builders return the full
count but store at most 6 IDs, so a 7th member reads a stale stack word and
crashes (*Dolphin* 2026-09-25). There are two copies: `0x80071BB0` (exhibition,
cap `0x80071ECC`) and `0x80430184` (Toy Field, cap `0x804303CC`). Raising both
caps to 7 is enough for 7 members (*Dolphin*, random select included). For
10, the caps (`cmpwi …,6`) become 10; 19 functions' stack buffers that hold a
member list grow by `0x10`; four `stb r27,off(r4)` sites map member index 6+
to popup node 9+; popup element `0xB3` of the select layout gets frames for
7–10 members (its info block's last time becomes 8). Seven members only need
the caps.

**Swatches**: elements `0xAD`/`0xAE` have one key per swatch colour (time =
selector byte 7, four vertex colours at `+0x40`). The colours: 0 red, 1 blue,
2 yellow, 3 green, 4 purple, 5 black, 6 brown, 7 light blue, 8 pink, 9 white.
Time 10 is an unused white key, recoloured orange for swatch 10.

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
  `+0x24` count, `+0x26` stride `0x50`), records `0x50` bytes: `+0x00` flags
  (`0x0014` on the first record, `0x0114` on the others), `+0x02` character
  ID (the key frame), `+0x04` `0x0400`, `+0x06` resource row, then the
  record's own data. Records are sorted by ID, highest first; a wrong order or
  a wrong first-record flag makes resolved icons disappear. A view shows the record with the highest
  ID ≤ the character, so an ID without its own record shows a neighbour's
  icon (*Dolphin*: the unused characters showed Pink Yoshi `0x46`). Each
  table's last frame must cover the highest key. Side records differ only in
  byte `+0x26` (`0x02` for IDs 0x01, 0x08, 0x0B, 0x27, 0x3E–0x41, else `0x82`).
- **Resource table**: u32 row count, u32 length (8 + count × `0x14`), then
  the rows (`0x14` bytes): u16 page, u16 0, f32 v1, u1, v2, u2. The stock bank
  has 152 rows; 8 bytes follow the table at the end of the bank.
- **Icon record** (DOL file `0x68DE88`, 48 bytes): three language slots of
  (file name pointer, length, offset, allocation); all three point at the
  same bank.
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
- **Stock characters' portraits** (`stock_icons`, IDs `0x00`–`0x46`, which
  own keys in all three tables) are replaced by pointing their existing
  records at packed rows; no key is added and the records' own data stays.
  normal_a goes to the side row: in the stock bank normal_a is a side-view
  portrait, the same row as side except for IDs 0x01, 0x08, 0x0B, 0x27 and
  0x3E–0x41, where it is a separate, slightly different side view (2026-10-05).
  Where the game draws normal_a is not known. (New IDs' normal_a keys show
  the front row, as in the external tool.)
- New descriptors for pages `0x92`/`0x93` occupy file `0x1264`–`0x12A4`,
  which covers the first 36 bytes of page `0x86`'s palette at `0x1280`, so
  that palette moves.

**Stock rows 149–151** (the last three of 152) sit on palette pages `0x8F`,
`0x90` and `0x91` of the front sheet: 149 Pink Yoshi, 150 "?", 151 the Mii
icon (IA8, greyscale). The front table keys `0x46` to row 149 and `0x4D` to
row 150; no record uses row 151.

**Portrait renderer** `0x80395DB0`, three paths:

- player (`0 ≤ id < 0x4D`): the tracks are evaluated with key `id << 16`;
- Mii (`0x4D`–`0x64`): row `0x97` (151) is written into the runtime row slot
  `0x8071FF78` (`li r0,0x97` at `0x80395EBC`), then the slot table is reset;
- invalid (below 0 or above `0x64`): key `0x4D`, the "?" icon.

A slot value below 0 falls back to the resource of the keyframe the track
evaluation lands on (`obj+0xB8`), which is how an ID without its own key gets
its neighbour's icon. IDs ≥ `0x4D` take the Mii path at
`0x80395E1C`. A branch there sends IDs ≥ `0x66` down the normal track path;
a new ID without its own art is aliased to its template at the renderer entry
and its two preview calls.

## Exhibition draft grid

Only the exhibition team draft (layout element `0xBA`) grows. Toy Field,
minigames and Free practice use a second grid (element `0x97`, 41 squares)
that is not expanded, but both screens share the grid widget code, so both
screen objects grow.

| | Screen A: exhibition team draft | Screen B: colour-wheel select |
|---|---|---|
| used by (*Dolphin* 2026-09-24) | exhibition | Toy Field and Minigames (`0x80435E08`), Free practice (`0x804EA464`); `0x8038DF98` never ran |
| object ctor, heap size | `0x8006E9C4`, `0x67C` (allocated at `0x802C8CFC`) | `0x8042C424`, `0x6CC` |
| grid widget | `obj+0x338`, mode 0 | `obj+0x2A0`, mode 1 |
| element, squares | `0xBA`, 40 (10×4) | `0x97`, 41 (12 wide) |
| square order | `0x80623458` (40), copied to `obj+0x238` | `0x80630930` (41, the 12 captains first) |

The grid widget class: ctor `0x80067C00`, builder `0x80067EF4(grid, layout,
slot, mode)`, hit test `0x8006887C`, vtable `0x8063A960`. Both screen objects
live in MEM2, so growing them costs no MEM1. Square positions exist only as
layout tracks; there is no spacing constant in code. Screen A's cursor
positions: 0–3 specials, 4–`0x15` roster slots, `0x16`–`0x3D` the 40 grid
squares, `0x3E`–`0x47` the Mii rows, then buttons (so the `0x3E`–`0x47` range
tests in screen-A code are cursor ranges, not Yoshi IDs). The 12 captains are
listed at `0x806318A8`: `00 01 02 03 04 05 06 09 0A 0B 11 13`.

**Stock layout:** 40 squares (10×4) for 41 heads. The square → head map
`0x80623458` holds head indices; head *h* is species *h*, and the head list
`0x80631878` gives each head's character. The constructor `0x8006E9C4` hands
the square nearest column 5 of one captain's family to Luigi (head 1), unless
the captain is Luigi or Yoshi. A captain with a wheel therefore lost its
whole square (*Dolphin* 2026-10-03: with Mario as a captain, Mario's square
went to Luigi and his new IDs were unreachable). Yoshi is exempt because his
wheel would vanish too.

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

## 2D layout banks

The select screens, the icon bank and 75 other files in dir 0 are 2D layout
banks (`SluggiesTools/Icons/layout2d.py` reads and writes them; all 223 banks
the DOL routes to round-trip byte for byte):

- header `0x20` bytes: `+0x04` container offset, `+0x20` texture count, then
  `0x20`-byte texture descriptors and the texture data;
- container: screen size `640×448`, descriptor offset `0x14`, resource-section
  offset and the container endpoint (= the end of the resource section). The
  descriptor is an element count followed by signed, descriptor-relative
  pointers; **an element's index is its position in that list**, and elements
  are packed in index order;
- element: u16 flags, u32 sub-block count, u32 length, sub-block offsets.
  Sub-block 0 is a `0x10`-byte info block, the others are tracks, one per
  sprite;
- track: u16 record count, u16 size of the first record, then keyframe
  records: byte 0 continuation flag, byte 1 size in words, u16 key (the
  frame; the character ID in the icon source tables), u16 kind, u16 resource
  row. `0x3C`-byte sprite records hold x/y (s16) at `+0x08`, RGBA at `+0x2C`
  and x/y scale (f32) at `+0x34`;
- resource section: count, length, then `0x14`-byte rows (texture page and
  four UV floats).

Growing an element shifts every later element and the resource section, so
their pointers, the resource offset and the endpoint are rewritten; the
texture data before the container does not move. The select layout (dir 119
file 19) holds 204 elements and 191 textures; its three language copies have
identical elements and differ only in text textures. Elements `0x65` and
`0x5C` (two more 41-square grids) were never drawn in any visited menu.

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

## Game memory

Dir 119 files stay resident in the MEM2 game heap (`0x900AE64C`–`0x930AE5FC`)
during a match, so their growth reduces the match's headroom (the external
tool's crash dumps ran out at a scored run or at the VS screen capture). Costs
measured on the shipped presets: the select layout grows by about 22.7 KB
(grid, 10-member popup and name plates together; the popup alone 2,904
bytes, a 12×5 grid 6,288 bytes); the packed icon bank grows by 33,600 bytes
for 8 portraits and stays under 600 KB for 159.
