"""Clone a model block from INPUT dt_na.dat into the hammerspace region of
OUTPUT dt_na.dat, then redirect the OUTPUT main.dol entry to the new location.

Called as a subprocess by patch.py when UseHammerspace is True.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import HammerspaceHelper as hh

_ROOT     = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
INPUT_DAT = os.path.join(_ROOT, '1_Input', 'dt_na.dat')


def cloneModelToHammerspace(chunk_number: int, file_index: int) -> bool:
    """Copy a model block from INPUT dt_na.dat to hammerspace in OUTPUT dt_na.dat,
    then redirect the OUTPUT main.dol entry to the new location.

    Returns True on success, False on any error.
    """

    # Step 1 — read offset and length from INPUT main.dol
    print(f"[1] Reading DOL entry  chunk={chunk_number}, file_index={file_index} ...")
    offset, length = hh.readDolEntry(chunk_number, file_index)
    if offset == -1:
        print("    ERROR: Could not read DOL entry.")
        return False
    print(f"    offset = 0x{offset:08X}  ({offset:,})")
    print(f"    length = 0x{length:08X}  ({length:,} bytes)")

    # Step 2 — verify enough contiguous free space exists in hammerspace
    print(f"[2] Scanning for {length:,} free bytes in hammerspace ...")
    new_offset = hh.findFreeMemoryChunk(length)
    if new_offset == -1:
        print(f"    ERROR: No contiguous zero region of {length:,} bytes found.")
        print(f"    Run HammerspaceHelper to extend OUTPUT dt_na.dat first.")
        return False
    print(f"    free region at 0x{new_offset:08X}  ({new_offset:,})")

    # Step 3 — read raw model bytes from INPUT dt_na.dat
    print(f"[3] Reading {length:,} bytes from INPUT dt_na.dat ...")
    if not os.path.exists(INPUT_DAT):
        print(f"    ERROR: Input dat not found: {INPUT_DAT}")
        return False
    with open(INPUT_DAT, 'rb') as f:
        f.seek(offset)
        data = f.read(length)
    if len(data) != length:
        print(f"    ERROR: Expected {length} bytes, got {len(data)}.")
        return False
    print(f"    Read OK.")

    # Step 4 — write the clone into hammerspace
    print(f"[4] Writing clone to OUTPUT dt_na.dat at 0x{new_offset:08X} ...")
    hh.writeModelBlock(data, new_offset)
    print(f"    Write OK.")

    # Step 5 — patch OUTPUT main.dol to point to the clone
    # All three language slots (en/sp/fr) are updated for consistency,
    # though the US game only reads the en slot.
    print(f"[5] Patching OUTPUT main.dol entry ...")
    hh.patchDolEntry(chunk_number, file_index, new_offset, length)

    print()
    print(f"Done. tiny_kong model cloned to hammerspace at 0x{new_offset:08X}.")
    print(f"Load the game and verify tiny_kong renders correctly in-game.")
    return True
