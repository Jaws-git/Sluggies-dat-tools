Note: "main.dol hammerspace" (appending a new data section to the DOL and raising the arena start past it) is viable: confirmed in Dolphin on 2026-09-24 (boot, character select and a full exhibition game with the new section intact). The data cave approach below (finding free/unused bytes in the middle of the DOL) is therefore no longer needed. These notes are kept for reference only.

[The Fun Guy]
from 626844 to 626A5C theres a large amount of normally unused data for horizontal angles. it’s a copy of everything from MSSB, and seemingly only used in like a weird dev mode or something? there’s quite a lot of spaces that are like this in varying sizes, this is the largest one i can think of

[The Fun Guy]
from like 807a4e90~ onwards theres just a buuuuuuuuunch of 0’s for a very long time lol, and ghidra just kind of stops around there
---

## Notes (address interpretation)

The quotes above give addresses without saying whether they are DOL file offsets or runtime (virtual) addresses. For the data sections D4/D5: `vaddr = file + 0x80003F00`.

**`807a4e90` onwards: not a usable file-backed cave.** bss ends at `0x807A4E80`, so `0x807A4E90` is past the end of every DOL section. It is not stored in main.dol, and it is not free at runtime either: `__init_registers` sets the initial stack pointer to `0x807B4E80` (`lis/ori r1` at `0x8000421C`), so `0x807A4E80`–`0x807B4E80` is the 64 KB main-thread stack, growing down toward bss. `0x807A4E80` holds the stack-end canary `0xDEADBABE`. The zeros seen in Ghidra are the rarely-reached deep end of the stack. The MEM1 arena (heap) starts above it, at `0x807B4E80` or `0x807B6E80` depending on the boot path (set in `OSInit` at `0x80596014` / `0x80595FC4`; verified statically on 2026-09-24). Nothing can be placed there by patching the DOL, and data copied in at runtime can be overwritten by a deep call chain.

**`626844`–`626A5C`: ambiguous, never verified.** Both readings fall inside D4 (file `0x61F4C0`–`0x635E00`, vaddr `0x806233C0`–`0x80639D00`):

| Reading | File range | Vaddr range | What is there |
|---|---|---|---|
| File offset | `0x626844`–`0x626A5C` | `0x8062A744`–`0x8062A95C` | f32 values such as 115.0, 60, 160, 140. Plausible angle/distance tables, but no clean boundary at either end. |
| Vaddr (`0x80626844`) | `0x622944`–`0x622B5C` | `0x80626844`–`0x80626A5C` | s16 values such as -700 and -500, starting right after zero padding. |

The contributor worked in Ghidra and wrote the other address as a vaddr (`807a4e90`), so "626844" is probably vaddr `0x80626844` with the `80` prefix dropped. This is unconfirmed. Before using the range, confirm that nothing references it: no load or `lis/addi` pair targets it at runtime, and a write breakpoint in Dolphin shows it untouched in normal play.
