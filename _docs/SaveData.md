# Save Data (`gamedata`)

The layout of Mario Super Sluggers' save file (US, `RMBE01`) and how the game
uses it for character unlocks. Addresses are US `main.dol` virtual addresses.
Offsets are big-endian.

**Source and date:** static analysis of `main.dol` (2026-09-25). It was
checked against a real Dolphin save that has challenge mode completed in slot
0 and two unused slots. Items marked *(inferred)* have not been confirmed with
a debugger.

## File

- **NAND path:** `title/00010000/524d4245/data/gamedata`. In Dolphin this is
  `Wii/title/00010000/524d4245/data/gamedata`.
- **Size:** `0x6020` bytes, fixed. The file is **not encrypted**.
- **Other files in the same folder:** `oekaki_0`, `oekaki_1` and `oekaki_2`
  (`0x8080` bytes each), and `banner.bin`.
- **File names:** a pointer table at `0x806D5CD4` lists the names
  (`oekaki_0..2`, `gamedata`, `banner.bin`). The NAND path builder
  `0x80496318(index)` looks them up by index; `gamedata` is index 3.
- **Layout source:** the init/reset routine `0x804961A0` clears the whole
  `0x6020`-byte image (at `base + 0x18180` in its owner object) and builds
  every block below.

## Layout

| File offset | Size | Contents | Init |
|---|---|---|---|
| `0x0000` | `0x20` | global header: 9 flag bytes, bytes 1 and 2 set to 1 | `0x8045ACDC` |
| `0x0020` | `0x1BA0` | global records (minigame high-score tables), seeded from `0x806EE938` (`0x1B80` bytes) | `0x80485944` |
| `0x1BC0` + *k* × `0x16C0` | `0x16C0` each | save slot *k* (*k* = 0, 1, 2) | loop at `0x80496200` |
| `0x6000` | `0x20` | checksum word, then zero padding | – |

### Global records

These are `0xB0`-byte records holding scores and a **character ID stored as a
u32**, where `0x65` means "no character". The character ID is the only
character-related data in the global block.

### Save slot

| Slot offset | Size | Contents | Init |
|---|---|---|---|
| `+0x000` | 8 | timestamp (`OSGetTime`, `0x8059FE7C`) | – |
| `+0x020` | `0x2B0` | progress/unlock block | `0x8045AD1C` |
| `+0x2E0` | `0x10D5` | 43 records of `0x64` bytes, then 9 bytes. Purpose not decoded: all zero in all three slots of the test save | `0x80485A70` |
| `+0x13C0` | `0x2E8` | challenge-mode struct (bytes `+0x146`…`+0x14E` initialised to `0xFF`) | memset at `0x80496228` |
| `+0x16A8` | `0x18` | unused (zero) | – |

## Character unlocks (progress block)

At runtime the progress object lives at `*(r13-0x300)`. It is `0x2B9` bytes,
allocated at `0x8045C590`: a 9-byte header, then the slot's `0x2B0`-byte
progress block at `+9`. *(Inferred: the header and block are built by the
same constructors as the file's global header and slot block, and the saved
bytes match.)*

- **Unlockable characters:** the list at `0x8062C930` holds 17 character IDs,
  **`0x36`…`0x46`**. These are the last 17 regular player IDs, including the
  Yoshi colours `0x42`–`0x46`.
- **State:** one byte per list entry at progress object `+0x1C6[17]`, which is
  slot block `+0x1BD`. A value of 0 means locked. In the test save, the
  finished slot has all 17 bytes set to 1 and the unused slots have all 0.
- **Getter:** `0x8045C62C(progress, id)` returns the state byte for `id`, or
  **2 for any ID not in the list**.
- **Setter:** `0x8045C5F0(progress, id, value)`.
- **Consequence:** every ID outside `0x36`…`0x46` is always unlocked,
  whatever the save contains. That includes `0x00`–`0x35`, the unused IDs
  `0x47`–`0x4C` and the Mii IDs `0x4D`–`0x64`.
- **Other data:** the block also holds a 21 × 17 byte table at `+0x29`,
  seeded from `0x80631328`. It uses the same 17 columns, so it is probably
  unlock-condition progress (not decoded).
- **Effect on the character select:** the roster builder (`0x8006BC20`) runs
  the 17 list IDs through the getter and clears a character's availability
  when its byte is 0. Apart from that, availability comes from the
  colour-wheel table's selectable byte (row byte 6), which is DOL data, not
  save data.

## Challenge mode

- **Manager:** `*(r13-0x1224)` is a `0x200`-byte challenge-mode manager.
  - It is created at `0x801D6BCC` (constructor `0x801D7B74`).
  - It is freed at `0x801D7348` and the pointer is cleared at `0x801D7350`.
  - Its `+0` points at the `0x2E8`-byte challenge struct.
- **Challenge struct:** lives at `*(r13-0xE80)`, allocated at `0x802552C4`,
  and is saved at slot `+0x13C0`.
- **`+0xD[0x4D]`:** recruit state per character ID, 2 = recruited. It covers
  IDs `0x00`–`0x4C` only, not the Mii IDs.
  - In the finished test slot, `0x00`–`0x46` are 2 and `0x47`–`0x4C` are 0.
  - `0x801E8DD4` and `0x801E8E3C` loop over it to count recruited characters.
- **Roster builder:** `0x8006BA6C` reads the recruit array only when its
  mode argument `r4` is non-zero (`0x8006BA90`); otherwise it uses the
  colour-wheel selectable byte. *(Inferred: the non-zero mode is used only by
  challenge mode, since the manager exists only there.)*

## What the save does not contain

- Nothing is indexed by character-select grid square or by species.
- No chemistry or star-player data. Both are static game data.
- Character IDs appear only as values (minigame records, u32) and as indices
  into the challenge recruit array. Unlock state is indexed by position in
  the 17-entry list.
