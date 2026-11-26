# Custom CPU Level: Format and Code Reference

One optional extra CPU level for exhibition matches: a 5th button on the
"CPU Level" popup whose behaviour is set by the user. Code:
`SluggiesTools/CustomCpu/`, GUI tab `SluggiesTools/gui_custom_cpu.py`. User
guide: `_docs/CustomCpuGuide.md`. Addresses are for the US `main.dol`.

Status: every part below ran in Dolphin on 2026-11-25 and 2026-11-26 (popup,
value swaps and restore in three MEM1 dumps, a roster run and an option
change with the level on, all eleven behaviour switches in play), except
where a paragraph says otherwise.

The settings list and the behaviour code were ported in November 2026 from
the Slugout 0.8 patcher (a third-party Sluggers build tool with permission to
reuse), reduced from its three custom levels to one. This document describes
what our code does; it does not depend on that tool.

## Overview

- The popup's 5th button stores match settings +0x10 = 4, a value the game
  never writes. A hook in the CPU manager's constructor turns that into the
  game's level 4 (value 0) plus a run-time **flag byte**, so the custom level
  plays as level 4 underneath.
- Three kinds of setting (15 switches, 74 table values,
  36 constants):
  - **Table values** are bytes of the game's own CPU tables. They are
    swapped in at the start of a custom match and swapped back at the start
    of the next other match, so levels 1-4 never see them.
  - **Group switches** (4) replace a whole group of table bytes with tuned
    ones; the group's table values then edit that tuned base.
  - **Behaviour switches** (11) hook injected code into the game's CPU
    decisions. **Constants** are numbers of that code.
- Everything defaults to the clean game's level 4: with no switch on and no
  value changed, a custom match plays exactly like All-Star level.
- All of it lives in `main.dol` (one block in a DOL section of its own) plus
  two rebuilt `dt_na.dat` files for the popup. The settings are stored in
  the DOL, so the DOL describes its own custom level.

## Level and flag

Match settings: `*(r13-0xB00)` (r13 = `0x807961C0`), CPU level at +0x10
(3, 2, 1, 0 = level 1..4).

- `FUN_800C364C` builds the CPU manager at every match start. At
  `0x800C3678` it reads the level with `lwz r6,0x10(r7)` and copies it to
  both sides (`stw r6,0xC(r3)` / `stw r6,8(r3)`), so a CPU vs CPU match plays
  the custom level on both sides. LR is saved before the site.
- The site becomes `bl` to the **manager stub** (uses r6, r8-r12, ctr, cr0;
  r0, r3, r4, r5, r7 are live and kept): level 4 gives `FLAG` = 1 and r6 = 0,
  any other level gives `FLAG` = 0 and the stock load.
- The level-to-tier table at `0x8063FB78` (5 words: 3, 2, 1, 0, 0) gets its
  entry 4 (`0x8063FB88`) set to 3, the hardest tier, for code that maps the
  settings value again.
- `FLAG` = `0x807B7014` and `SWAPPED` = `0x807B7015`: two spare bytes of the
  DOL hammerspace text section's marker (`dol_hammerspace.CUSTOM_CPU_FLAG`).
  Zero in the file, so zero at boot. Fixed addresses, so code of other
  features can test them.

**Gate.** Every behaviour stub first tests `FLAG` (`lis; lbz; cmpwi 0; beq
stock`) and, where the game keeps one, the acting side's own level or tier
at the hardest. So levels 1-4 run the stock instruction and return, a human
side is never touched, and a level changed from the pause menu during a
custom match falls back to the game's code.

## Value swaps

The manager stub compares `FLAG` with `SWAPPED`. When they differ it walks
the **swap table**:

- at a custom match's start it saves the live bytes of every record into the
  record's backup, then writes the custom bytes;
- at the start of any other match after one, it writes the backups back.

So levels 1-4 run on whatever the DOL holds (other tools' edits included)
and the custom level on its settings alone. Two custom matches in a row do
not swap twice.

Record layout: `u32 address, u32 length, custom bytes, backup` (both padded
to 4 bytes), a zero word ends the table. The set of addresses is fixed:
41 runs of adjacent bytes, 116 bytes in all, at most 32 bytes
a run. After each record the stub issues `dcbst` / `sync` / `icbi` / `isync`
on its first and last byte, because two records are code words (the dive
gates below).

Checked in Dolphin, 2026-11-25: three MEM1 dumps of CPU vs CPU matches
(custom, All-Star, custom). The custom dumps had settings +0x10 = 4, flag
bytes `01 01` and every swap byte equal to the deployed settings; the
All-Star dump had level 0, flag `00 00` and every byte clean again.

## Popup

Dialog 0x71 ("CPU Level") has its table entry at `0x806D3158` (14 s32: 3,
0x2E, then six {choice id, message} slots; four are used: 0x12..0x15 with
messages 0xAE..0xB1). A button's picture is resource row choice + 0x22 of
the popup picture bank.

- **Open** (`0x802CCC30`, the exhibition's `bl 0x80471864`): a stub fills
  the 5th slot (`0x806D3180`) with choice `0x46` and message `0x124`, calls
  the popup open and empties the slot again. The other openers of dialog
  0x71 (minigames, `0x8042EA60`) therefore keep 4 buttons.
- **Answer** (`0x802CCCA8`, `bge cr1` after `cmpwi cr1,r0,0x16`): a stub
  stores level 4 for choice `0x46`; ids below 0x16 take the stock path.
- **Caption**: `dt_na.dat` dir 121 file 4, the popup message table (`01
  01`, u16 count, u32 offsets in 2-byte units, UTF-16BE text). Each of the
  three language copies has 292 messages; ours is appended as message
  `0x124`: the colour prefix of that copy's own All-Star caption (message
  0xB1), then the button name. Encoding: printable ASCII as fullwidth forms
  (U+FF01-FF5E), space as 0x0020, Latin-1 letters as themselves.
- **Picture**: dir 119 file 4, the popup picture bank. Row `0x68` (one past
  the stock rows) is added for the 5th button.
  - Stock buttons: 82 x 82 cells of one 512 x 1024 C8 image that texture
    pages 23..62 share, each page with a palette of its own (RGB5A3), so a
    cell looks right only through its own row's page (Level 1-4: rows
    `0x34`..`0x37`, pages 47..50). A row's four floats are top, left,
    bottom, right as fractions of the page.
  - With `1_Input/_Icons/Custom_CPU_Button.png` (any image; scaled to
    82 x 82): each language copy gets one more texture page, 128 x 128
    RGB5A3, the picture in its top left cell, and row `0x68` points at it
    (each copy grows from 0xADAEC to 0xB5B20 bytes). Checked in Dolphin, 2026-11-26: the picture
    sits and scales like the stock buttons.
  - Without the file: row `0x68` is a copy of the Level 4 row `0x37`, no
    new texture.
  - The file is read at every deploy and roster run;
    `--custom-cpu-picture` (the tab's "Update button picture") redeploys
    the deployed record to pick up an edited file.
- Both files are rebuilt from their stock copies at every deploy, written
  to DAT hammerspace and routed by their directory records. Turning the
  level off puts the `1_Input` records back and zeroes the copies. A roster
  run's reset keeps records it does not own, so they survive it.

Button name: 1-25 characters, no leading or trailing space. Every printable
ASCII character draws; of Latin-1, the letters the game's own text uses
(`¡ª º ¿ Á É Í Ú à á â ç è é ê í î ï ñ ó ô ù ú û`) and `Ä Ö Ü ä ö ü ß Ñ`
were seen to draw (Dolphin, 2026-11-25, also 25 x `W` for the width); other
Latin-1 characters are accepted with a warning. Not looked at: the French
and Spanish console languages.

## Where it lives in main.dol

One **block**, the contents of a DOL section of its own (see *DOL
hammerspace* in `_docs/_docs_roster/RosterExpansion.md`):

| Part | Contents |
|---|---|
| header (0x20) | magic `SLGCPU\0\1`, u32 capacity, record offset, record length, three stub offsets |
| stubs | manager, popup open, popup answer |
| behaviour code | the shared item block (when an item switch is on), then each enabled behaviour's code and data |
| swap table | records with their backups |
| record | zlib-compressed JSON: version, button name, settings (only values that differ from the default), unknown keys of a loaded file, the settings' name |

- The block is found through the manager hook: the `bl` target at
  `0x800C3678` minus 0x20 must carry the magic. The record is the only
  state: there is no side file.
- The block is position-dependent, and its section sits behind the roster's
  data section, so it is rebuilt at every deploy and at every roster run
  (`CustomCpu/carry.py`: the record is read before the roster reset and
  everything is built from it after the last roster step). The game
  options' section rebuild carries it the same way.
- Size: 0x1000 with no behaviour switch, 0xA000 with all eleven on a roster
  with 0x100-row tables (2026-11-26).
- A DOL with only the custom level is not a roster: the roster's data
  section stays empty.
- Turning the level on creates DOL sections on a stock-layout DOL and so
  breaks compatibility with Gecko codes made for the stock game; the runner
  asks first, as the game options do.
- A build from before 2026-11-26 kept the block in the hammerspace text
  section; such a block is still read, and the next deploy moves it.

A behaviour's hook sites hold a branch into the block while its switch is
on, and the game's own word otherwise. A deploy refuses a switch whose site
(or a word its code rests on) is neither stock nor ours, naming the switch;
sites of switches that are off are not looked at.

## Settings

Keys are those of `.slgcpu` files. Keys starting with `sluggies_` are
tables this tool added (they were Gameplay tab fields before 2026-11-25).

### Group switches

| Switch | Key | Settings section | What it does |
|---|---|---|---|
| Batters read your pitches | `cpu-perfect-batting` | Batting | Batters read the pitch type, track the ball and lay off balls (the Batting settings). |
| Pitchers charge, curve and move the ball | `cpu-perfect-pitching` | Pitching | Perfect-charge fastballs, curves that break late, rarely the same spot twice (the Pitching settings). |
| Fielders dive, jump and react | `cpu-perfect-fielding` | Fielding | Fielders go for dives and jump catches, clear obstacles and react to teammates (the Fielding settings). |
| Runners tap and press fast | `cpu-perfect-running` | Running | Runners land their sprint taps and press quickly in close plays (the Running settings). |

With a group on, its tuned bytes are the base and the group's table values
edit them; a value given while its group is on stays explicit even when it
equals the clean game's. The tuned bytes:

| Group | Address | Clean bytes | Tuned bytes |
|---|---|---|---|
| `cpu-perfect-batting` | `0x80797227` | `55` | `64` |
| `cpu-perfect-batting` | `0x80797223` | `2d` | `64` |
| `cpu-perfect-batting` | `0x80797233` | `55` | `4f` |
| `cpu-perfect-batting` | `0x806248F4` | `3d4ccccd` | `00000000` |
| `cpu-perfect-batting` | `0x80624984` | `285f0502` | `00640000` |
| `cpu-perfect-batting` | `0x8062490E` | `fe02` | `0000` |
| `cpu-perfect-batting` | `0x80624916` | `fc04` | `0000` |
| `cpu-perfect-batting` | `0x80797253` | `1e` | `00` |
| `cpu-perfect-batting` | `0x80797218` | `280a5a281e0a280a` | `645a646464506450` |
| `cpu-perfect-pitching` | `0x8062475F` | `00141e280a` | `0000000064` |
| `cpu-perfect-pitching` | `0x807971E7` | `0a` | `00` |
| `cpu-perfect-pitching` | `0x807971EB` | `55` | `64` |
| `cpu-perfect-pitching` | `0x807971EF` | `46` | `64` |
| `cpu-perfect-fielding` | `0x80624A14` | `5f` | `64` |
| `cpu-perfect-fielding` | `0x80624A19` | `62` | `64` |
| `cpu-perfect-fielding` | `0x80624A1E` | `5f` | `64` |
| `cpu-perfect-fielding` | `0x80624A23` | `63` | `64` |
| `cpu-perfect-fielding` | `0x80624A28` | `5a` | `64` |
| `cpu-perfect-fielding` | `0x80624A2D` | `5a` | `64` |
| `cpu-perfect-fielding` | `0x80624A32` | `5a` | `64` |
| `cpu-perfect-fielding` | `0x80624A37` | `5a` | `64` |
| `cpu-perfect-fielding` | `0x80624A68` | `3c` | `64` |
| `cpu-perfect-fielding` | `0x80624A6D` | `50` | `64` |
| `cpu-perfect-fielding` | `0x80797258` | `5a` | `00` |
| `cpu-perfect-fielding` | `0x800E2750` | `28000003` | `28000006` |
| `cpu-perfect-fielding` | `0x800E25B8` | `2c040014` | `2c040000` |
| `cpu-perfect-running` | `0x80624A7E` | `005a` | `0064` |
| `cpu-perfect-running` | `0x80624AA6` | `005a` | `0064` |
| `cpu-perfect-running` | `0x80624B1E` | `0014` | `000c` |
| `cpu-perfect-running` | `0x80624B26` | `0010` | `000a` |
| `cpu-perfect-running` | `0x80797280` | `63636363` | `64646464` |

The two code words: `0x800E2750` (`cmplwi r0,3` to `cmplwi r0,6`: dives and
jumps for grounders and liners) and `0x800E25B8` (`cmpwi r4,20` to
`cmpwi r4,0`: dives for fly balls). The global game option "outfield dives"
writes the first word for every level; while it is on, the custom level
keeps it on too.

### Behaviour switches

| Switch | Key | Hook sites | What it does |
|---|---|---|---|
| Pitchers learn each batter | `cpu-pitching` | `0x800C73A4`, `0x800C6BA8`, `0x800C691C`, `0x8015DCE4`, `0x80162394`, `0x8016291C`, `0x801629D0`, `0x800BD408`, `0x80163630`, `0x800C3684`, `0x800C6E58`, `0x800C67C8` | Pitchers work the edges of the zone and learn during the game what gets each batter out. Each pitcher's starting pitch mix is worked out from the characters' stats when you deploy (and at every roster run): after changing stats with the stat editor (Bridge), deploy again. |
| Batters swing fully charged | `cpu-charge-timing` | `0x800C5058` | Batters start charging at once and are at full charge when they swing. |
| Batters see where the pitch really crosses | `cpu-batter-track` | `0x800C5160`, `0x800C4B9C` | Batters judge ball or strike where the pitch crosses the plate, so breaking balls don't fool them. |
| Never bunts | `cpu-no-bunts` | `0x800C4824` | The CPU never bunts. |
| Charged pitches weaken your hits | `cpu-cursed-ball` | `0x800BA38C` | The CPU's charged pitches are cursed balls, like yours: they cut the batter's hit power. |
| Star swings by hitter strength | `cpu-star-swings` | `0x800C5934` | Weak hitters always star-swing with 2 or more stars; strong hitters rarely or never (the Star swings settings; the star swing odds tables are not used then). |
| Never throws star pitches | `cpu-no-star-pitches` | `0x800C710C` | The CPU pitcher never throws a star pitch. |
| Smart rundown throws | `cpu-rundown` | `0x800E06C8`, `0x800E0990` | In a rundown it throws ahead at once and times throws behind the runner to get the out. |
| Aims batting items at fielders | `cpu-items` | `0x8045584C`, `0x8045031C`, `0x80450A68` | Uses its batting item, aimed and timed to hit the fielder about to get the ball (the Items settings add error to that). |
| Fielders handle items | `cpu-item-defense` | `0x800E28CC`, `0x800E2A18`, `0x8012179C`, `0x8011FCD4`, `0x801257F8`, `0x80125FFC`, `0x80113318`, `0x80459C74`, `0x802FEC84`, `0x800E2648`, `0x800E2808` | Fielders jump POWs, pass when a teammate is near, run around items and ignore Boo. |
| Brings in its best relief pitchers | `cpu-subs` | `0x800C3E24`, `0x80134D04` | When it changes pitchers it picks from its best, then moves fielders for better chemistry. |

### Batting

| Setting | Key | Range | Default | Stored as | What it does |
|---|---|---|---|---|---|
| Pitch read | `pitch_read` | 0..100% | 85% | u8 at `0x80797227` | Chance the batter reads the pitch type right. A wrong read widens its timing error. |
| Star-pitch read | `star_pitch_read` | 0..100% | 45% | u8 at `0x80797223` | The same, against star pitches. |
| Tracks the ball | `track` | 0..100% | 85% | u8 at `0x80797233` | Chance the bat follows the ball. When it doesn't, the batter swings where it guessed the pitch would go, usually a whiff. This is the main sweet-spot rate. |
| Aim error | `aim_error` | 0..0.3 plate widths | 0.05 plate widths | f32 at `0x806248F4` | How far off the bat's aim can be while tracking. |
| Timing error, right read | `timing_right` | 0..10 ± frames | 2 ± frames | pair at `0x8062490E` | Random early or late swing after a right read. |
| Timing error, wrong read | `timing_wrong` | 0..20 ± frames | 4 ± frames | pair at `0x80624916` | Random early or late swing after a wrong read. |
| Takes the first pitch | `takes_first` | 0..100% | 40% | u8 at `0x80624984` | Chance of taking an at-bat's first pitch, wherever it is. |
| Lays off balls | `lays_off` | 0..100% | 95% | u8 at `0x80624985` | Chance of taking a pitch outside the zone. |
| Takes a strike (0-1 strikes) | `takes_strike` | 0..100% | 5% | u8 at `0x80624986` | Chance of taking a hittable strike with 0 or 1 strikes. |
| Takes a strike (2 strikes) | `takes_strike2` | 0..100% | 2% | u8 at `0x80624987` | Chance of taking a hittable strike with 2 strikes. |
| Fooled by the Wario decoy | `decoy` | 0..100% | 30% | u8 at `0x80797253` | Chance a decoy star pitch makes the batter track the fake ball. |
| Charge swings: Balance, 0-1 strikes | `charge_balance0` | 0..100% | 40% | u8 at `0x80797218` | Chance a Balance hitter charges the swing with 0 or 1 strikes (rolled only when the CPU does not star-swing). |
| Charge swings: Balance, 2 strikes | `charge_balance1` | 0..100% | 10% | u8 at `0x80797219` | Chance a Balance hitter charges the swing with 2 strikes (rolled only when the CPU does not star-swing). |
| Charge swings: Power, 0-1 strikes | `charge_power0` | 0..100% | 90% | u8 at `0x8079721A` | Chance a Power hitter charges the swing with 0 or 1 strikes (rolled only when the CPU does not star-swing). |
| Charge swings: Power, 2 strikes | `charge_power1` | 0..100% | 40% | u8 at `0x8079721B` | Chance a Power hitter charges the swing with 2 strikes (rolled only when the CPU does not star-swing). |
| Charge swings: Speed, 0-1 strikes | `charge_speed0` | 0..100% | 30% | u8 at `0x8079721C` | Chance a Speed hitter charges the swing with 0 or 1 strikes (rolled only when the CPU does not star-swing). |
| Charge swings: Speed, 2 strikes | `charge_speed1` | 0..100% | 10% | u8 at `0x8079721D` | Chance a Speed hitter charges the swing with 2 strikes (rolled only when the CPU does not star-swing). |
| Charge swings: Technique, 0-1 strikes | `charge_technique0` | 0..100% | 40% | u8 at `0x8079721E` | Chance a Technique hitter charges the swing with 0 or 1 strikes (rolled only when the CPU does not star-swing). |
| Charge swings: Technique, 2 strikes | `charge_technique1` | 0..100% | 10% | u8 at `0x8079721F` | Chance a Technique hitter charges the swing with 2 strikes (rolled only when the CPU does not star-swing). |

### Pitching

| Setting | Key | Range | Default | Stored as | What it does |
|---|---|---|---|---|---|
| Perfect-charge fastball | `perfect_fastball` | 0..100% | 10% | fastball at `0x8062475F` | Chance a charged fastball gets a perfect charge (the game's own perfect charge). The rest keep the game's mix of 50 / 70 / 100% charges. |
| Repeats a location | `repeat_location` | 0..100% | 10% | u8 at `0x807971E7` | Chance of throwing to the same spot as the last pitch. |
| Curves a plain pitch | `curve` | 0..100% | 85% | u8 at `0x807971EB` | Chance a plain pitch curves. |
| Late break | `late_break` | 0..100% | 70% | u8 at `0x807971EF` | Chance a curve breaks late. |

### Fielding

Frame values have a 60 Hz and a 50 Hz site; the 50 Hz value is 5/6 of the
60 Hz one, rounded down.

| Setting | Key | Range | Default | Stored as | What it does |
|---|---|---|---|---|---|
| Tries a dive or jump | `dive_try` | 0..100% | 95% | u8 at `0x80624A14`, `0x80624A19` (+2 more) | Chance a fielder goes for a dive or jump catch (the game's is 95-99% by class; one value for all). |
| Clears obstacles | `obstacle` | 0..100% | 90% | u8 at `0x80624A28`, `0x80624A2D` (+2 more) | Chance a fielder jumps over or dodges an obstacle in its path. |
| Chemistry reaction (far) | `chem_far` | 0..100% | 60% | u8 at `0x80624A68` | Chance a fielder reacts to a good-chemistry teammate. |
| Chemistry reaction (near) | `chem_near` | 0..100% | 80% | u8 at `0x80624A6D` | The same, with the teammate close. |
| Reaction delay | `reaction_delay` | 0..30 frames | 10 frames | frames at `0x806249E8`, `0x806249ED` | Extra frames before a fielder starts moving after contact. |
| Throw hold (infield) | `throw_hold_in` | 0..10 frames | 2 frames | frames at `0x806249F4`, `0x806249FE` | Frames an infielder holds the ball before throwing. |
| Throw hold (outfield) | `throw_hold_out` | 0..10 frames | 2 frames | frames at `0x806249F9`, `0x80624A03` | Frames an outfielder holds the ball before throwing. |
| Recover from a deflection | `replan` | 1..30 frames | 6 frames | frames at `0x80624A08`, `0x80624A0D` | Frames before a fielder re-plans its chase after the ball bounces off someone (at least 1: 0 never fires). |
| Hard throw on close plays | `hard_throw` | 0..100% | 90% | u8 at `0x80797258` | Chance of the CPU-only harder throw on a close play. |

### Running

| Setting | Key | Range | Default | Stored as | What it does |
|---|---|---|---|---|---|
| Good steal jump | `steal_jump` | 0..100% | 30% | u8 at `0x8079727F` | Chance a stealing runner gets a good jump. |
| Sprint taps land | `sprint_taps` | 0..100% | 90% | u16 at `0x80624A7E`, `0x80624AA6` | Chance the runner's sprint taps count. |
| Close-play press delay | `press_delay` | 0..40 frames | 20 frames | frames16 at `0x80624B1E`, `0x80624B26` | Frames before the runner presses in a close-play contest. |
| Presses in a close play | `always_presses` | 0..100% | 99% | u8 at `0x80797280`, `0x80797281` (+2 more) | Chance the runner presses at all in a close play. |

### Star usage

20 bytes at `0x80624778`: `class * 5 + situation`, class 0..3 = Balance,
Power, Speed, Technique; situation 0..4 = normal with 0-1 strikes, normal
with 2 strikes, special with 0-1 strikes, special with 2 strikes, dire.
Special: runners on 2nd / 3rd (not 1st) or bases loaded. Dire: 2 strikes, 2
outs, bottom of the final inning. Keys `sluggies_star_usage_<class>_<situation>`
(`normal_0_1`, `normal_2`, `special_0_1`, `special_2`, `dire`), 0..127 %.

The star swing chance is that byte plus a modifier, `s8` at `0x80797210 +
tier * 2 + (0 normal, 1 special)`. Only the hardest tier's row is a setting
(`sluggies_star_modifier_normal` at `0x80797216`, default -20;
`sluggies_star_modifier_special` at `0x80797217`, default 5); the other
three rows hold the clean game's values during a custom match. The game
sign-extends the sum as a byte (`extsb` at `0x800C5934`), so odds plus
modifier above 127 would never star-swing: the settings check refuses that.
The same 16-byte run holds the charge swing odds of the Batting section.

### Preferred pitch

16 bytes at `0x807971C0`: 8 pitch types (`class * 2 + (0: 0-1 strikes, 1: 2
strikes)`; 0 curveball, 1 fastball, 2 changeup), then 8 chances in %. Keys
`sluggies_pitch_<class><0|1>` and `sluggies_pitch_chance_<class><0|1>`. When
the roll fails the CPU throws a random pitch. With "Pitchers learn each
batter" on, the pitch model picks the type instead.

### Pitch model

| Setting | Key | Range | Default | Stored as | What it does |
|---|---|---|---|---|---|
| Learning speed | `model_learning` | 0..0.5 | 0.04 | constant of "Pitchers learn each batter" | How fast the pitcher learns what beats this batter (0: never learns). |
| Randomness: pitch choice | `model_randomness` | 0.05..5 | 1 | constant of "Pitchers learn each batter" | How random its pitch type, location and break are (higher: more random, harder to read). |
| Randomness: ball or strike | `model_randomness_bs` | 0.05..5 | 0.25 | constant of "Pitchers learn each batter" | How random its ball-or-strike choice is. |
| Reads the batter over | `model_patience` | 1..50 pitches | 4 pitches | constant of "Pitchers learn each batter" | How many pitches its read of the batter's habits (chasing, taking) is averaged over: higher changes its mind slower. |
| Balls at 0-1 balls, at least | `model_floor` | 0..50% | 5% | constant of "Pitchers learn each batter" | A ball this often first, before the model picks, with 0 or 1 balls. |
| Never walk the batter | `never_walk` | 0..1 | off | constant of "Pitchers learn each batter" | On: with 3 balls every pitch is a strike, so it never walks a batter (the 3-ball rates below are held at 0). |
| Balls at 3-0 | `cap3_0` | 0..100% | 5% | constant of "Pitchers learn each batter" | With 3 balls and 0 strikes: the ball rate. |
| Balls at 3-1 | `cap3_1` | 0..100% | 5% | constant of "Pitchers learn each batter" | With 3 balls and 1 strike. |
| Balls at 3-2 | `cap3_2` | 0..100% | 5% | constant of "Pitchers learn each batter" | With 3 balls and 2 strikes. |
| How far outside and inside the strike zone it throws | `far_ball` | 0.76..1.2 plate widths | 0.94 plate widths | constant of "Pitchers learn each batter" | The outside edge of the band its balls land in (the band starts at 0.75, past the sweet spot). |

"Never walk the batter" holds the three 3-ball rates at 0 while it is on.

### Pitch mix

| Setting | Key | Range | Default | Stored as | What it does |
|---|---|---|---|---|---|
| Fast pitcher charge pitch threshold | `fastball_speed` | 0..300 | 150 | constant of "Pitchers learn each batter" | Pitchers with at least this charge pitch speed start out throwing fastballs more. |
| Fastballs (fast pitchers) | `fastball_top` | 0..70% | 35% | constant of "Pitchers learn each batter" | Starting fastball share for those pitchers (at most 70: changeups can take up to 30). |
| Fastballs (the others) | `fastball_low` | 0..70% | 3% | constant of "Pitchers learn each batter" | Starting fastball share for everyone else (at most 70, as above). |
| Plain curves | `plain_curves` | 0..100% | 15% | constant of "Pitchers learn each batter" | Starting share of plain curves; the rest after fastballs and changeups are charged curves it steers onto the edge. |
| First pitch in the zone | `zone_first` | 0..100% | 40% | constant of "Pitchers learn each batter" | Before it learns: chance an at-bat's first pitch goes in the zone. |
| In the zone after a taken strike | `zone_after_take` | 0..100% | 75% | constant of "Pitchers learn each batter" | Before it learns: chance of a strike after the batter took one. |
| In the zone after a chase | `zone_after_chase` | 0..100% | 20% | constant of "Pitchers learn each batter" | Before it learns: chance of a strike after the batter chased a ball. |
| Hit-by-pitch margin | `hbp_margin` | 0..0.5 m | 0.15 m | constant of "Pitchers learn each batter" | How far clear of the batter's hit box a pitch on his side must stay. |

### Star swings

| Setting | Key | Range | Default | Stored as | What it does |
|---|---|---|---|---|---|
| Always star-swing below charge power | `star_always` | 0..150 | 60 | constant of "Star swings by hitter strength" | A batter with charge power under this always star-swings when the team has 2 or more stars. |
| Never at charge power | `star_never` | 0..150 | 60 | constant of "Star swings by hitter strength" | Charge power at or above this never star-swings. |
| Rarely from charge power | `star_rare` | 0..150 | 60 | constant of "Star swings by hitter strength" | From this charge power up to the "never" line, only at the rare rate. |
| The rare rate | `star_rare_pct` | 0..100% | 2% | constant of "Star swings by hitter strength" | The chance in that range. |
| Below that: this minus charge power | `star_top` | 0..200 | 80 | constant of "Star swings by hitter strength" | Below the rare line the chance is this number minus charge power (80: charge power 60 gets 20%). |

"Rarely from" may not be above "Never at".

### Rundowns

| Setting | Key | Range | Default | Stored as | What it does |
|---|---|---|---|---|---|
| Early throw, first throw | `rundown_first` | 0..100% | 90% | constant of "Smart rundown throws" | Chance the first throw to the base behind a runner heading back goes early. |
| Early throw, other throws | `rundown_early` | 0..100% | 70% | constant of "Smart rundown throws" | Chance a later throw to the base behind a runner heading back goes early. |
| Early throw lead | `rundown_lead` | 0..60 frames | 20 frames | constant of "Smart rundown throws" | How many frames before the deadline (the last moment the throw still gets him out) an early throw goes. |
| Throw deadline margin | `rundown_buffer` | 0..60 frames | 10 frames | constant of "Smart rundown throws" | The ball must reach the base this many frames ahead of the runner's best time. |
| Max early throws | `rundown_rolled` | 0..50 | 10 | constant of "Smart rundown throws" | How many throws of one rundown may go early; after that each goes at the deadline. |

### Items

| Setting | Key | Range | Default | Stored as | What it does |
|---|---|---|---|---|---|
| Error-free item at-bats | `items_error_free` | 0..100% | 75% | constant of "Aims batting items at fielders" | Share of at-bats with no added item error; the others roll the error below once for the whole at-bat. |
| Item timing error | `items_timing_error` | 0..30 ± frames | 30 ± frames | constant of "Aims batting items at fielders" | Frames early or late, in an at-bat with added error. |
| Item aim error | `items_aim_error` | 0..10 ± m | 2.5 ± m | constant of "Aims batting items at fielders" | Metres off target on each axis, in an at-bat with added error (steps of 0.25 m). |
| Hit before the catch | `items_lead` | 0..60 frames | 15 frames | constant of "Aims batting items at fielders" | The item lands this many frames before the fielder reaches the ball. |
| Cursor speed | `items_cursor` | 0.5..5 m / frame | 2 m / frame | constant of "Aims batting items at fielders" | How fast the item cursor moves (the game's level 4: 0.7). |
| Keep clear of own runners | `items_runner_pad` | 0..10 m | 1.5 m | constant of "Aims batting items at fielders" | How far outside a blast its own runners must stay. |
| Pass distance vs items | `items_pass` | 0..20 m | 20 m | constant of "Fielders handle items" | A fielder facing an item passes to a teammate within this many metres (the game's pass flight tops out at 20). |
| Boo Error Rate | `boo_error_rate` | 0..100% | 40% | constant of "Fielders handle items" | While a Boo hides the ball, each fielder misjudges where it is by a random amount; this share of fielders are off by more than their catch reach and aren't likely to make the play. |

## Behaviour code

Registers and offsets are the game's; "AI" is the acting CPU object.

### Never bunts

`FUN_800C4824` is the CPU batter's bunt decision at an at-bat's start (r3 =
the batter AI, tier at +0x39). Its first word (`stwu r1,-0x20(r1)`) branches
to the stub, which clears +0x31 (the at-bat-start flag) and +0x3B (the bunt)
and returns before the frame is made: no roll.

### Charged pitches weaken your hits

`FUN_800BA1B8` (the batter's hit power) applies the pitch-quality multiplier
only when `(pitcher +0x140 == 3 and +0x13B != 0) or +0x1BD == 3`, and
+0x140 == 3 is written on the human pitch path only: a built-in CPU
handicap. The stub replaces the `bne` at `0x800BA38C` and goes on to the
+0x13B test whatever +0x140 holds, so the CPU's charged pitches count too.

### Batters swing fully charged

`FUN_800C4FF4` runs the CPU batter each frame (r29 = AI, r30 = batter, r31 =
pitcher). Charge: batter +0xBF 1 = building, +0x94 counts charged frames,
+0x90 is full, +0x92 the last full frame. The stock CPU starts about 8
frames before release. The stub replaces the start block at `0x800C5058`:
it starts charging at once (+0xBF = +0xB6 = 1), and on flight frame 2 sets
+0x94 to `(+0x90 + +0x92) / 2` minus the frames left before the swing (AI
+0x45 + 1 - frame), so the charge is mid-window on the swing's frame
whatever the pitch speed.

### Star swings by hitter strength

`FUN_800C5778` decides once per pitch (r26 = AI, r29 = batter, charge power
u16 at +0xA6). The stub replaces the `extsb r25,r0` at `0x800C5934` that
makes the chance from the tables above. A batter with charge power under
`star_always` whose team has 2 or more stars (meter s16 at `*(r13-0x1540)`
+ side * 2, 100 = 2 stars) always star-swings. Otherwise the chance is 0
from `star_never` up, `star_rare_pct` from `star_rare` up, else `star_top`
minus charge power. The star usage tables are not used then.

### Never throws star pitches

`FUN_800C710C` is the CPU pitcher's star pitch decision (r3 = the brain,
tier at +0x1A). Its first word branches to a stub that returns 0 before the
frame, so the callers pick a normal pitch.

### Brings in its best relief pitchers

- **Pitcher** (`0x800C3E24` in `FUN_800C3C88`, after its two candidate
  passes; candidate slots at r1+0x20, r29 = control, r31 = roster team).
  Score = curve stat (roster record +0x24) times `clamp(stamina / max
  stamina, 1/2, 1)`. The catcher is dropped unless he is the only candidate.
  Up to 3 candidates within 225/256 of the best form the pool; with 2 or 3,
  only those whose pitching leaves the best field chemistry stay; one is
  drawn with the game's random call. Result as stock: control +0x18 = his
  position.
- **Fielders** (`0x80134D04` in `FUN_80134BE8`, after the CPU's pitcher
  swap). The seven players at 1B..RF get pair weights from
  `FUN_8015C800` (good 3, neutral 1, bad 0). Key = 64 x (CF-LF + CF-RF) + 8
  x (2B-SS) + (SS-1B + 3B-1B). Every permutation is searched (pruned);
  the highest key wins, then the fewest players moved, and a tie keeps the
  field. Swaps go through the game's own `FUN_8015AB50`.

### Batters see where the pitch really crosses

The game's batter judges ball or strike once, 7 frames out, from the ball's
forecast x at the batter's own z (pitcher +0xD4), while the umpire calls a
strike when the ball is inside [-0.55, 0.55] where it crosses z = 1.12 or z
= 0.32 (`0x80627A90`: low, high, the two planes). A pitch still moving
sideways is judged wrong.

- **Take** (`0x800C5160` in `FUN_800C4FF4`): the stock judgement is
  skipped. On the swing frame (AI +0x45 + 1): take when the AI holds
  (+0x37), else forecast the ball to both planes and take unless either x
  is in the zone.
- **Track** (`0x800C4B9C` in `FUN_800C4AB4`): the bat's x target is the
  forecast for the batter's z instead of +0xD4, whose last step
  interpolates with a mirrored fraction.
- **Forecast**: the game's flight model from the ball's current state, one
  frame at a time: drag while z <= +0xAC (and the speed stays above the
  floor), the frame-rate decay, the sideways step +0x94 held constant; the
  plane crossing interpolated.
- Only for the plain batter AI (vtable `0x806404D0`) and not on star
  pitches.

### Smart rundown throws

`FUN_800E05E8` plans one non-forced runner between bases: throw base at
ai+0x29C (base + 10 = hold for the cover, -1 none), carry base at ai+0x28C
(9 = chase and tag), margin at ai+0x27C. Its two branches (`0x800E06C8` in
the rundown zone, `0x800E0990` otherwise) go to one body, for a runner
advancing, heading back, or stopped between bases:

1. Each time a different fielder holds the ball a throw is counted and a
   roll made: under `rundown_first` % for the first throw, `rundown_early` %
   for the others, the throw is "early".
2. The game's own planner `FUN_800E0F98` gives the plan: to the next base
   for a runner advancing or stopped (with the cover fielder's arrival time
   held at "on the base" for the call, so it throws at once), to the base
   behind for a runner heading back.
3. Chase: the holder chases and tags a runner stopped in the rundown zone,
   or any runner he reaches before the runner reaches the base he is
   heading for (Ball Dash counted).
4. Advancing, under 3/8 of the leg: hold instead of throwing ahead.
5. Heading back: the throw waits until windup + flight + 18 +
   `rundown_buffer` + 1 frames no longer beats the runner's best time to
   his safe line. An early roll throws `rundown_lead` frames sooner, for the
   first `rundown_rolled` throws. Without an early roll it also throws once
   the runner cannot stop short of the tag any more.
6. A holder with Ball Dash carries the ball to the base behind when the
   dash gets him there first.

Its state (roll, throw count) is 44 bytes behind the body.

### Pitchers learn each batter

12 hooks into the CPU pitcher (brain `CBComPitch`): pitch type
(`0x800C73A4`), location and learning update (`0x800C6BA8`), aim
(`0x800C691C`), aim clamp (`0x8015DCE4`), steering (`0x80162394`), the two
umpire planes (`0x8016291C`, `0x801629D0`), contact (`0x800BD408`), hit
batter (`0x80163630`), match-start reset (`0x800C3684`), and the walk to
the spot (`0x800C6E58`, `0x800C67C8`).

- **Model.** Scores learned during one game and zeroed at every match
  start. One state block per batting team, each with a score set per batter
  handedness: 13 factor scores = pitch type (fastball, changeup, plain
  curve, charged curve), zone band where the ball really crossed (deep in,
  edge in, just off, far off), curve direction, break size. A choice is an
  index drawn with P ~ prior x 2^(score / temperature); with every score 0
  it is the prior.
- **Priors per pitcher**, built from the characters' stats: fastballs
  `fastball_top` % for charge pitch speed >= `fastball_speed`, else
  `fastball_low` % (0 when not faster than his curveball); changeups 30 /
  10 / 4 % by how high the changeup floats; `plain_curves` %; the rest
  charged curves steered onto their target.
- **Learning.** Reward after each pitch: strikeout +10, swinging miss +2,
  taken strike +1, foul +0.5, contact negative by timing and sweet-spot
  accuracy, taken ball -1.5, walk -25, hit batter -100; strikeout and
  contact scaled by the batter's charge power and the runners. Each factor
  the pitch used moves by `model_learning` x the clamped error.
- **Ball or strike** from the opponent's chase and chase-strikeout rates
  against the cost of a ball at this count; with 0-1 balls a `model_floor`
  % roll throws a ball first; with 3 balls at most `cap3_*` % balls.
- **Aim.** The pitcher starts walking to his spot at once and throws when
  he is there. A pitch on the batter's side must clear his hit box by
  `hbp_margin` at his live position, else it goes to the other side; each
  hit batter widens the margin by 0.25 m (up to 1.0) for the rest of the
  game.
- **Tables from stats.** Three per-character tables (type priors, hit-box
  edge with charge power band, planned bend) are computed from the DOL's
  stats when the block is built, through the game's own `lis` / `addi`
  pairs to the stats, changeup, trajectory, character float, throw float
  and selector tables: 0x65 rows on the stock tables, 0x100 when a roster
  moved them. Stats edited afterwards (stat editor bridge) are picked up by
  the next custom CPU deploy or roster run, not before.

### Fielders handle items

Hooks in the chaser's decision `FUN_800E2830` (`0x800E28CC`, `0x800E2A18`),
the run-catch predictor (`0x8012179C`), the catch block (`0x8011FCD4`), the
two movers' route targets (`0x801257F8`, `0x80125FFC`, shared mover
`0x80113318`), the Boo's and the Peach Ice Garden night star's freeze call
(`0x80459C74`, `0x802FEC84`), and the two dive presses (`0x800E2648`,
`0x800E2808`). Only fielders of a CPU side (fielder level byte +0x2E == 0).

- **POW**: the fielder jumps when the POW in flight lands within 2 frames.
- **Pass ("attack")**: with a teammate within `items_pass` metres (or
  within 50 m toward home plate) the chaser runs to a spot on the ball's
  track, stops, faces the ball and presses when the game's own hit test
  will connect within 3 frames; the ball then goes to that teammate.
- **Fly ball without a teammate near**: a jump timed so the catch is at the
  jump's peak, only when the ball is within half his catch reach.
- **Route**: an item within 8 m in his path makes him run around it (2.5 m
  to the side).
- **Boo and night star**: CPU fielders are not frozen. While a Boo hides
  the ball each fielder's idea of the ball is off by a random amount (two
  normal draws); `boo_error_rate` % of fielders are off by more than their
  catch reach and skip their catch.
- **Dive**: a requested dive is pressed only when the game's dive planner
  says it catches the ball, no ordinary catch gets there first, and no pass
  is planned for that fielder.

A 0x40-byte state block and a 9 x 12-byte route table are shared with the
item code below (`CustomCpu/defense_block.py`).

### Aims batting items at fielders

Hooks in the CPU item AI (`GmOjyama::COjyamaCom`, object at `*(r13-0x350)`):
at-bat reset (`0x8045584C`), decision (`0x8045031C`), aim (`0x80450A68`).
Base items only (Shell, Fireball, Bob-omb, POW, Banana, Boo).

- **Plan**: every frame it finds the first frame some fielder can take the
  ball (run time, reach, reaction) and where he will be `items_lead` frames
  before that; for a ball going to the wall, the outfielder who would jump
  for it.
- **Release and aim per item**, from the game's item tables read at build
  time: Shell and Fireball arrive at that spot then (a shot list also
  scores other fielders in the path; a Fireball only goes with nobody else
  in the way); a Bob-omb explodes and a POW bursts there then; a Banana
  lands just before; a Boo is thrown 750 ms ahead.
- **Aim limits**: only spots a human could aim at (on screen, inside the
  walls); nothing that would reach its own runners within
  `items_runner_pad`; no good spot means no throw that frame.
- **Added error**: in `100 - items_error_free` % of at-bats a timing error
  of up to +-`items_timing_error` frames and an aim error of up to
  +-`items_aim_error` metres per axis is rolled once for the at-bat.

### How the code was checked

- `SluggiesTools/tests/ppc_sim.py` is a small PowerPC interpreter; the
  tests run the stubs against stand-ins for the game's data and functions
  (`test_custom_cpu*.py`). Run there: the manager stub's swap logic, every
  small stub, the relief pitcher choice and fielder search (against a
  brute-force reference), the ball forecast (against a plain flight
  model), the rundown body, the pitch model's draw and small stubs, the
  dive rule and the Boo stubs.
- Not run there, only in the game: the pitch model's location / learning
  and aim stubs, the items' decision, aim and shot routines, the fielders'
  decision and route code.
- All hook sites and relied-on words were compared with the US `main.dol`.

## Settings files (`.slgcpu`)

JSON: `{"format": "sluggers-cpu", "version": 1, "name": ..., "settings":
{key: {"4": value}}, "button": ...}`. This is the Slugout 0.8 settings
format, so files go both ways; `button` (the caption) is our own extra key.

- A value is given per level column ("4", "5", "6"). The custom level is
  column 4. Loading column 5 or 6 of a file turns that tool's defaults for
  the level (groups and behaviours on, its level constants) into explicit
  values, so the result plays like that column.
- Keys this version does not know are kept in the record and written back
  on save.
- Not tried: a file written by that tool itself (the tests use files in its
  format written here).

## Interaction with other features

- **Gameplay tab.** Star usage, charge swing odds, star swing modifiers and
  preferred pitch were global Gameplay tab tables until 2026-11-25. They
  are custom level settings now: gameplay settings files and AI Gameplay
  Tool files load without them (with a note), and a custom CPU deploy or a
  roster run puts their bytes in the DOL back to `1_Input`.
- **CPU vs CPU close play option** (`close_play_<n>`): its stub writes the
  static entry `0x80624B1E`, which is also the custom "close-play press
  delay". Outside CPU vs CPU the stub writes nothing while `FLAG` is set,
  so the custom value holds for the match. In a CPU vs CPU match at the
  custom level the option's draw wins. Not tried in Dolphin.
- **Outfield dives option**: see *Group switches*; the block is rebuilt
  when the option changes.
- **Roster runs, game options**: keep the level (see *Where it lives*).
- **Challenge mode, minigames, Toy Field**: untouched; the button exists in
  the exhibition popup only.

## Command line

```
py start.py --custom-cpu FILE [--cpu-level 4|5|6] [--cpu-button NAME] [--dry-run]
py start.py --custom-cpu-off [--dry-run]
py start.py --custom-cpu-save FILE
py start.py --custom-cpu-picture [--dry-run]  # read the button picture again
py SluggiesTools/CustomCpu/runner.py          # status: on / off and the settings
```

`main.dol` and `dt_na.dat` of `3_Output_Dat` are written together; a deploy
needs the original `main.dol` in `1_Input`.

## Modules

| Module | Role |
|---|---|
| `settings.py` | every setting as data, checks, `.slgcpu` files, the bytes a custom match swaps in |
| `engine.py` | the block, the manager / popup stubs, hooks on and off, the record |
| `behaviours.py`, `behaviour_base.py` | the list of behaviours, the gate, the small stubs |
| `rundown.py`, `pitching.py`, `item_defense.py`, `dive.py`, `items.py`, `defense_block.py`, `one_level.py` | the larger behaviours |
| `menu_files.py` | the two popup files in `dt_na.dat` |
| `carry.py` | detect / apply / remove across roster runs and section rebuilds |
| `runner.py` | command line |
| `view.py` | the GUI tab's model (no Dear PyGui) |
