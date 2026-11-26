# `.slgmodel` character files

Reference for the character file format of the Sluggers Characters patcher
(a third-party modding tool) as SluggiesTools reads it. User guide:
[RosterGuide.md, "Importing a character file"](../RosterGuide.md#importing-a-character-file-slgmodel).
Reader: `SluggiesTools/Roster/slgmodel.py`; tests: `SluggiesTools/tests/test_slgmodel.py`.

A `.slgmodel` carries one finished custom character: game model blocks,
portraits, voice clips and a glTF copy of the model. It is the equivalent of a
High + Low `.sluggie` pair whose blocks are already built. No game bytes travel
in the file: the blocks are deltas against the clean game.

## Container

A zip, at most 64 MB packed and 64 MB unpacked (`MAX_FILE`). Only these members
are allowed; anything else refuses the file:

| Member | Content |
|---|---|
| `character.json` | Metadata (below). Required. |
| `models/0.slgd` | Model file 0 of the character's directory (High model) as an `SLGD1` delta. Required. |
| `models/1.slgd` | Model file 1 (Low model). Optional: without it the High model doubles as the Low model. |
| `model.glb` | The same model as glTF 2.0 on the base's skeleton, for Blender. Not read for the game model. |
| `side.png`, `front.png` | Select-screen portraits, 48x51. Optional per view. |
| `voice/<slot>.wav` | The character's own voice clips, slots `v1 v3 v4 v5 v6 v7 v8 v10 v11 v12 v13 v14` (the 12 moments of `Sound/sound_packs.MOMENTS`, in that order). |
| `portable_assets/...`, `community/...` | Optional extension folders (below). Any member under them is allowed. |

## Extension folders

Slugout 0.8 (the patcher's later name) appends these to a `format` 1 file
without changing `format` (seen in its source, 2026-11-26). A file has
`portable_assets/` only when the character has such an asset.

| Member | Content | Read |
|---|---|---|
| `portable_assets/bat.slgd` | The character's own bat: file 2 of its directory (the whole equipment entry, model at the pointer at +4) as an `SLGD1` delta. | Yes: written into the slot's file 2 with the models. A bat that does not rebuild or does not fit the slot is left out with a warning. |
| `portable_assets/bat_project.json`, `bat_project/*` | The bat's source model and settings. | No |
| `portable_assets/animations/<6-14>.anm` | The animation banks the author changed: each the whole bank of that directory file, stored raw. | Yes (below). |
| `portable_assets/native_facial.glb` | Edits of the base's facial poses, applied by the patcher at patch time (not contained in the model blocks). | No (warning) |
| `portable_assets/shape_keys.glb`, `expressions.slgexpr` | Blender shape keys. | No (warning) |
| `portable_assets/manifest.json` | Sizes and SHA-256 of the members above. | No |
| `community/stats.csv` | The stat values the author changed (below). | Yes, on a new ID. |
| `community/chemistry.csv` | Chemistry towards named partners (below). | Yes, on a new ID. |
| `community/manifest.json` | Schema and which of the two files exist. | No |

### Animation banks

A bank in the file is the same thing the animation edit writes
(`Roster/anim.py`): one whole ANM bank, which replaces every animation of that
file. The patcher checks a bank the same way this tool does (magic, the
file's sequence count, track IDs the stock bank or the model's skeleton has).

- Each bank is written into its file of the slot after the models
  (`--write-slot-animation`), on a new ID and a stock slot alike; only that
  slot's route changes.
- A bank is left out with a warning when it is not a readable bank, when its
  sequence count is another file's, or when it animates track IDs that
  neither the slot's stock bank nor a skeleton has (the vanilla High model of
  the slot's directory, plus the file's own High model).
- A bank equal to the slot's stock one is written only where the slot holds
  a custom bank (the write returns that file to its baseline).
- A bank bigger than any stock bank of its file is written with a warning.
  The patcher's Blender round trip appends changed keys, so such banks grow
  (it caps the growth at 512 KB per edit); none was available to test.

Checked on 2026-11-26 with the nine Rosalina banks the patcher ships as
deltas (`models/work/rosalina/anim/`), rebuilt from the clean game: all nine
read, match their file by sequence count and pass the fit check on Daisy's
directory with no errors or warnings. They are the same size as Daisy's stock
banks. The patcher's per-file SHA-256 (`portable_assets/manifest.json`) is not
checked here.

### `community/stats.csv` and `chemistry.csv`

UTF-8 CSV, at most 256 KB and 512 rows each. Only the values the author
changed are listed; everything else is the base's. A file that cannot be read
is left out with a warning, the character still loads.

`stats.csv` has the columns `character, section, field, value`:

| Section | Fields | Written as (`StatEditor/fields.py`) |
|---|---|---|
| `stats` | The 25 named stats of the stats row, `pitching arm` … `curse ball`. | The `stats` field of the same name (same row offsets). |
| `table_stats` | `hit trajectory`, `hit curve`, `stamina`, `star pitch type` | `stats`: `traj`, `hit curve`, `stamina`, `star pitch type` |
| `table_stats` | `changeup speed mult`, `changeup arc height`, `windup charge frames` | `pitching`: `Speed Mult`, `Height`, `charge` |
| `table_stats` | `catch normal`, `catch any direction`, `catch centered`, `catch max height`, `catch dive` | `size`: `regular`, `facing away`, `safer catch`, `height#5`, `dive` |
| `table_stats` | `body radius`, `body height` | `size`: `width`, `height#13` |
| `table_stats` | `pitch steering`, `strike zone height` | Not written: they live in two per-character tables (the patcher's `throwfloats` +8 at `0x806289F0`, `charfloats` +0x1C at `0x806291D8`) that have no stat field here. |
| `scale` | `scale`: one factor (the patcher allows 0.75–1.7). | `size` `Gameplay` and `Select Screens`: the base's size as the roster gives it, times the factor. |

`chemistry.csv` has the columns `character, partner, chemistry`: the partner
by name (the patcher's stock names, or another custom character's name) and
`bad` / `neutral` / `good` (0 / 1 / 2). The file gives one value per pair; it
is written in both directions. A partner the roster does not have is left out
with a warning; a custom partner is found by its slot name.

The mapping is in `Roster/slg_stats.py`. It was read from the patcher's source
(2026-11-26) and checked in a dry run of the stat apply step on a new ID; no
file written by the patcher itself was available.

How the values are written:

- Only a **new ID** takes them: one stat copy snapshot
  (`--apply-stat-edits copy:<file>`) after the roster rebuild, on top of the
  stats the slot plays with. From then on they are stat edits of that slot.
  A stock slot keeps its stats (warning).
- A value that selects something (arms, class, weight, captain, star pitch
  and swing, the two abilities, trajectory, hit curve, star pitch type) is
  left out when it is outside the game's own list (the stat editor's range):
  the patcher allows more for moves that exist only in its own code, e.g.
  star pitch up to 20. Other values outside the stat editor's range are
  written with a warning.

## `character.json`

| Key | Meaning |
|---|---|
| `format` | `1`. Any other value is refused (written by a newer patcher). |
| `name` | Character name. Required. |
| `base` | The stock player it is built on, by the patcher's English name (`"Daisy"`, `"Red Shy Guy"`; case and spacing ignored). Must be a stock player (IDs below `ids.PLAYER_END`); Miis and unknown names are refused. The name table is `slgmodel.BASE_NAMES`, index = character ID. |
| `author`, `from`, `notes` | Free text. |
| `left_out` | List (or string) of what the file does not carry, in the author's words. |
| `glb_sha1` | SHA-1 of `model.glb` at export. A differing `model.glb` means it was edited after export; the file loads with a warning that the blocks lack those edits. |
| `blocks` | `[0]` or `[0, 1]`: the model files present. |
| `voice` | Whether the file carries voice clips. |
| `voice_slots` | `{slot: "own" \| "base"}`. `base` plays the base character's clip. A slot with a WAV member counts as `own`. |

## `SLGD1` delta

Each `models/<N>.slgd` is zlib-compressed:

```
"SLGD1" | u32 length (BE) | SHA-1 (20 bytes) | ops...
```

| Op | Encoding | Effect |
|---|---|---|
| `0` COPY | varint source, varint offset, varint length | Copy bytes from a clean game file |
| `1` LITERAL | varint length, then the bytes | Append the bytes |

Varints are little-endian base-128 (7 bits per byte, high bit = more).
Sources, by index: `0` = `main.dol`, `1` = `dt_na.dat`, `2` = `sound_NA/MY2.brsar`
(`DELTA_SOURCES`, read from `1_Input`). The rebuilt block must have the stated
length (at most 16 MB, `MAX_BLOCK`) and SHA-1; a modded `1_Input` fails this
check.

## What the format does not carry

Colour swatch, items and abilities: the base character's apply. The same goes
for the size, bat, animations, face edits, stat values and chemistry of a file
without the extension folders, and for the face edits and shape keys of a
file with them.
