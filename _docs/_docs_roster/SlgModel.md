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

## `character.json`

| Key | Meaning |
|---|---|
| `format` | `1`. Any other value is refused (written by a newer patcher). |
| `name` | Character name. Required. |
| `base` | The stock player it is built on, by the patcher's English name (`"Daisy"`, `"Red Shy Guy"`; case and spacing ignored). Must be a stock player (IDs below `ids.PLAYER_END`); Miis and unknown names are refused. The name table is `slgmodel.BASE_NAMES`, index = character ID. |
| `author`, `from`, `notes` | Free text. |
| `left_out` | List (or string) of what the file does not carry, in the author's words. |
| `glb_sha1` | SHA-1 of `model.glb` at export. A differing `model.glb` means it was edited after export; the file is refused because its blocks are out of date. |
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

Size, colour swatch, stat values, chemistry, bat, items, abilities and
animations: the base character's apply.
