import os
import shutil
import struct

BASE_SIZE      = 715046144      # ~715 MB
CHUNK_SIZE     = 1024 * 1024   # 1 MB read buffer
HS_BUFFER_BYTES = 4 * 1024 * 1024  # 4 MiB safety buffer appended after every write
OUTPUT_DAT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '3_Output_Dat', 'dt_na.dat'))

_ROOT         = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
INPUT_DAT     = os.path.join(_ROOT, '1_Input',      'dt_na.dat')
INPUT_DOL     = os.path.join(_ROOT, '1_Input',     'main.dol')
OUTPUT_DOL    = os.path.join(_ROOT, '3_Output_Dat', 'main.dol')

# DOL directory table constants (US version)
_DOL_BASE      = 0x80003f00
_DIRS_START    = 0x69C828
_DIRS_END      = 0x69CAD8
_DIRS_COUNT    = (_DIRS_END - _DIRS_START) // 4
_DAT_FNAME_PTR = 0x8067f658
_ENTRY_SIZE    = 48   # 12 × uint32 BE


if __name__ == '__main__':
    print("\nHammerspace creation mode.")
    print("This creates some extra space to put additional model data that may not fit inside the base game memory.\n")
    print("Value range: 0-3580")
    print("  - 0: Restore original file size, no hammerspace (most compatible with original hardware)")
    print("  - 359: Add 359MB (1GB total) (can hold several extra models, depending on complexity)")
    print("  - 1024: Add 1GB (1.7GB total) (plenty of space for nearly anything)")
    print("  - 3580: maximum, creates 4GB total. You can't go higher than this")

    # user input checks
    raw = input("Enter value: ").strip()
    try:
        value = int(raw)
    except ValueError:
        print("ERROR: Input is not an integer. Aborting.")
        raise SystemExit(1)

    if value < 0:
        print("ERROR: Value must be 0 or greater. Aborting.")
        raise SystemExit(1)

    if value > 3580:
        print("Value exceeds maximum, clamping to 3580.")
        value = 3580

    # size change logic
    MB = 1000000
    target_size = BASE_SIZE + value * MB

    if not os.path.exists(OUTPUT_DAT):
        if not os.path.exists(INPUT_DAT):
            print(f"ERROR: Output file not found and input file is also missing: {INPUT_DAT}")
            raise SystemExit(1)
        os.makedirs(os.path.dirname(OUTPUT_DAT), exist_ok=True)
        print(f"Output file not found. Copying input file to output folder...")
        shutil.copy2(INPUT_DAT, OUTPUT_DAT)
        print(f"Copied {INPUT_DAT} -> {OUTPUT_DAT}")

    current_size = os.path.getsize(OUTPUT_DAT)
    print(f"Current file size : {current_size:,} bytes")
    print(f"Target file size  : {target_size:,} bytes")

    if current_size == target_size:
        print("File is already the correct size. Nothing to do.")
        raise SystemExit(0)

    with open(OUTPUT_DAT, 'ab' if target_size > current_size else 'r+b') as f:
        if target_size > current_size:
            f.write(b'\x00' * (target_size - current_size))
            print(f"Appended {target_size - current_size:,} zero bytes.")
        else:
            # Check whether the region about to be removed contains non-zero data.
            remove_start  = target_size
            remove_length = current_size - target_size
            non_zero_offset = -1
            f.seek(remove_start)
            scanned = 0
            while scanned < remove_length:
                chunk = f.read(min(CHUNK_SIZE, remove_length - scanned))
                if not chunk:
                    break
                for i, byte in enumerate(chunk):
                    if byte != 0:
                        non_zero_offset = remove_start + scanned + i
                        break
                if non_zero_offset != -1:
                    break
                scanned += len(chunk)

            if non_zero_offset != -1:
                print(f"\nWARNING: Non-zero data found at offset 0x{non_zero_offset:08X} "
                      f"within the {remove_length:,} bytes that would be removed.")
                print("This may be hammerspace model data that has not been removed yet.")
                answer = input("Continue and permanently discard this data? [y/N]: ").strip().lower()
                if answer != 'y':
                    print("Aborted. No changes were made.")
                    raise SystemExit(0)

            f.truncate(target_size)
            print(f"Trimmed {remove_length:,} bytes from end of file.")

    print("Done.")



### Hammerspace Helpers

def ensureOutputDat(required_total_size: int = 0) -> bool:
    """Ensure OUTPUT_DAT exists and is at least ``required_total_size`` bytes.

    If the file is missing it is copied from INPUT_DAT first.  If it is
    smaller than ``required_total_size`` it is extended with zero bytes.

    Returns True on success, False if the input file is also missing."""

    if not os.path.exists(OUTPUT_DAT):
        if not os.path.exists(INPUT_DAT):
            print(f"ERROR: OUTPUT dt_na.dat not found and INPUT dt_na.dat is also missing: {INPUT_DAT}")
            return False
        os.makedirs(os.path.dirname(OUTPUT_DAT), exist_ok=True)
        print(f"OUTPUT dt_na.dat not found. Copying from input ...")
        shutil.copy2(INPUT_DAT, OUTPUT_DAT)
        print(f"Copied {INPUT_DAT} -> {OUTPUT_DAT}")

    if required_total_size > 0:
        current_size = os.path.getsize(OUTPUT_DAT)
        if current_size < required_total_size:
            expand_by = required_total_size - current_size
            print(f"Expanding OUTPUT dt_na.dat by {expand_by:,} bytes "
                  f"(current: {current_size:,} → target: {required_total_size:,}) ...")
            with open(OUTPUT_DAT, 'ab') as f:
                f.write(b'\x00' * expand_by)
            print(f"Expansion complete. New size: {os.path.getsize(OUTPUT_DAT):,} bytes")

    return True


def findFreeMemoryChunk(dataLength: int) -> int:
    """Scan the hammerspace region of the dat file for a contiguous run of
    ``dataLength`` zero bytes.  Returns the file offset of the first such run,
    or -1 if no fitting space is found."""

    if dataLength <= 0:
        return -1

    if not os.path.exists(OUTPUT_DAT):
        print(f"ERROR: File not found: {OUTPUT_DAT}")
        return -1

    file_size = os.path.getsize(OUTPUT_DAT)

    if file_size <= BASE_SIZE:
        print("No hammerspace region present (file is not larger than BASE_SIZE).")
        return -1

    with open(OUTPUT_DAT, 'rb') as f:
        f.seek(BASE_SIZE)

        run_start = -1   # file offset where the current zero-run started
        run_length = 0   # length of the current zero-run
        read_offset = BASE_SIZE

        while read_offset < file_size:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break

            for i, byte in enumerate(chunk):
                if byte == 0:
                    if run_length == 0:
                        run_start = read_offset + i
                    run_length += 1
                    if run_length >= dataLength:
                        return run_start
                else:
                    run_start = -1
                    run_length = 0

            read_offset += len(chunk)

    return -1


def writeModelBlock(data: bytes, offset: int) -> None:
    """Write raw model data into the dat file at the given offset.

    Ensures OUTPUT_DAT exists (copying from INPUT_DAT if necessary) and that
    the file is large enough to hold ``offset + len(data) + HS_BUFFER_BYTES``,
    expanding it with zero bytes when needed.

    ``offset`` should be a value returned by ``findFreeMemoryChunk``."""

    required_size = offset + len(data) + HS_BUFFER_BYTES
    if not ensureOutputDat(required_size):
        raise FileNotFoundError(f"Cannot write model block: OUTPUT_DAT missing and INPUT_DAT not found ({INPUT_DAT})")

    with open(OUTPUT_DAT, 'r+b') as f:
        f.seek(offset)
        f.write(data)


def writeDebugDumps(
    sluggie_name:  str,
    model_offset:  int,
    model_length:  int,
    block:         bytes,
) -> None:
    """Write debug copies of the original and hammerspace model blocks.

    Creates ``3_Output_Dat/SluggDebugg/`` if it does not already exist, then
    writes two files named after ``sluggie_name``:

      * ``<sluggie_name>_Original.SluggDebugg``   — the original model block
        read verbatim from INPUT dt_na.dat at ``model_offset``.
      * ``<sluggie_name>_Hammerspace.SluggDebugg`` — the assembled hammerspace
        block passed as ``block``.

    Existing files with the same names are overwritten.
    """
    debug_dir = os.path.join(os.path.dirname(OUTPUT_DAT), 'SluggDebugg')
    os.makedirs(debug_dir, exist_ok=True)

    orig_path = os.path.join(debug_dir, f"{sluggie_name}_Original.SluggDebugg")
    with open(INPUT_DAT, 'rb') as f_in:
        f_in.seek(model_offset)
        orig_block = f_in.read(model_length)
    with open(orig_path, 'wb') as f_out:
        f_out.write(orig_block)
    print(f"[Debug] Original block    → {orig_path}")

    hs_path = os.path.join(debug_dir, f"{sluggie_name}_Hammerspace.SluggDebugg")
    with open(hs_path, 'wb') as f_out:
        f_out.write(block)
    print(f"[Debug] Hammerspace block → {hs_path}")


def appendHammerspaceLog(
    action:       str,
    model_name:   str,
    chunk_number: int,
    file_index:   int,
    dat_offset:   int,
    data_length:  int,
) -> None:
    """Append one entry to the persistent hammerspace activity log.

    Creates ``3_Output_Dat/SluggDebugg/HammerspaceLog.txt`` (and the
    ``SluggDebugg`` directory) if they do not yet exist, then appends the
    entry.  Existing content is never overwritten.

    Parameters
    ----------
    action :
        ``'Written'`` or ``'Removed'``.
    model_name :
        The sluggie filename (e.g. ``337568064_L_mii_male.gpl.sluggie``).
    chunk_number, file_index :
        DOL directory table coordinates of the model entry.
    dat_offset :
        Byte offset of the start of the model data block in dt_na.dat.
    data_length :
        Byte length of the model data block.
    """
    import datetime

    debug_dir = os.path.join(os.path.dirname(OUTPUT_DAT), 'SluggDebugg')
    os.makedirs(debug_dir, exist_ok=True)
    log_path  = os.path.join(debug_dir, 'HammerspaceLog.txt')

    size_mb   = data_length / (1024 * 1024)
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    entry = (
        f"Action:   {action}\n"
        f"Model:    {model_name}\n"
        f"Chunk:    {chunk_number}  |  File: {file_index}\n"
        f"Address:  0x{dat_offset:08X}\n"
        f"Size:     {size_mb:.2f} MB\n"
        f"Time:     {timestamp}\n"
        f"-----\n"
    )

    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(entry)

    print(f"[Log] Entry appended → {log_path}")


def _readDirPtrs() -> list[int]:
    """Read the directory pointer table from the INPUT main.dol.

    Returns a list of DOL file offsets (virtual address minus DOL base),
    one per directory entry."""

    ptrs = []
    with open(INPUT_DOL, 'rb') as dol:
        for addr in range(_DIRS_START, _DIRS_END, 4):
            dol.seek(addr)
            raw = struct.unpack('>I', dol.read(4))[0]
            ptrs.append(raw - _DOL_BASE)
    return ptrs


def readDolEntry(chunk_number: int, file_index: int) -> tuple[int, int]:
    """Read the offset and length for a model entry from the INPUT main.dol.

    Returns ``(offset_en, len_en)`` from the en language slot,
    or ``(-1, -1)`` if ``chunk_number`` is out of range."""

    dir_ptrs = _readDirPtrs()
    if not (0 <= chunk_number < len(dir_ptrs)):
        print(f"ERROR: chunk_number {chunk_number} out of range (0-{len(dir_ptrs) - 1})")
        return -1, -1

    entry_offset = dir_ptrs[chunk_number] + file_index * _ENTRY_SIZE
    with open(INPUT_DOL, 'rb') as dol:
        dol.seek(entry_offset + 4)
        len_en    = struct.unpack('>I', dol.read(4))[0]  # word[1]
        offset_en = struct.unpack('>I', dol.read(4))[0]  # word[2]
    return offset_en, len_en


def readOutputDolEntry(chunk_number: int, file_index: int) -> tuple[int, int]:
    """Read the offset and length for a model entry from the OUTPUT main.dol.

    Unlike ``readDolEntry`` (which reads from the unmodified input), this
    reflects the current patched state written by previous hammerspace runs.

    Returns ``(offset_en, len_en)`` from the en language slot,
    or ``(-1, -1)`` if the output DOL does not exist or the chunk is out of range."""

    if not os.path.exists(OUTPUT_DOL):
        return -1, -1

    dir_ptrs = _readDirPtrs()
    if not (0 <= chunk_number < len(dir_ptrs)):
        return -1, -1

    entry_offset = dir_ptrs[chunk_number] + file_index * _ENTRY_SIZE
    with open(OUTPUT_DOL, 'rb') as dol:
        dol.seek(entry_offset + 4)
        len_en    = struct.unpack('>I', dol.read(4))[0]  # word[1]
        offset_en = struct.unpack('>I', dol.read(4))[0]  # word[2]
    return offset_en, len_en


def patchDolEntry(chunk_number: int, file_index: int, new_offset: int, new_length: int) -> None:
    """Update the offset and length fields in the output main.dol for a model entry.

    Reads the directory pointer table from the INPUT main.dol (never modified) to
    locate the correct 48-byte entry, then writes ``new_offset`` and ``new_length``
    into all three language slots (en, sp, fr) of the OUTPUT main.dol.

    If the output main.dol does not yet exist, it is copied from the input first.

    Layout of each 48-byte entry (big-endian uint32 words):
      word[0]  = DAT_FNAME_PTR   (not modified)
      word[1]  = len_en          <- new_length
      word[2]  = offset_en       <- new_offset
      word[3,4] = unknown
      word[5]  = len_sp          <- new_length
      word[6]  = offset_sp       <- new_offset
      word[7,8] = unknown
      word[9]  = len_fr          <- new_length
      word[10] = offset_fr       <- new_offset
      word[11] = unknown
    """

    # Ensure output DOL exists
    if not os.path.exists(OUTPUT_DOL):
        if not os.path.exists(INPUT_DOL):
            print(f"ERROR: Input DOL not found: {INPUT_DOL}")
            return
        os.makedirs(os.path.dirname(OUTPUT_DOL), exist_ok=True)
        shutil.copy2(INPUT_DOL, OUTPUT_DOL)
        print(f"Copied main.dol to output folder.")

    dir_ptrs = _readDirPtrs()

    if not (0 <= chunk_number < len(dir_ptrs)):
        print(f"ERROR: chunk_number {chunk_number} out of range (0-{len(dir_ptrs) - 1})")
        return

    entry_offset = dir_ptrs[chunk_number] + file_index * _ENTRY_SIZE

    # (file_pos_in_dol, value) pairs for every field that must be updated
    patches = [
        (entry_offset +  4, new_length),  # len_en
        (entry_offset +  8, new_offset),  # offset_en
        (entry_offset + 20, new_length),  # len_sp
        (entry_offset + 24, new_offset),  # offset_sp
        (entry_offset + 36, new_length),  # len_fr
        (entry_offset + 40, new_offset),  # offset_fr
    ]

    with open(OUTPUT_DOL, 'r+b') as dol:
        for file_pos, value in patches:
            dol.seek(file_pos)
            dol.write(struct.pack('>I', value))

    print(f"Patched DOL entry: chunk={chunk_number}, file_index={file_index}, "
          f"offset=0x{new_offset:08X}, length=0x{new_length:X}")


def removeModelFromHammerspace(chunk_number: int, file_index: int) -> tuple:
    """Remove a model block that was previously written to hammerspace.

    Steps:
      1. Read the current offset and length from the OUTPUT main.dol.
      2. Verify the offset falls inside the hammerspace region (>= BASE_SIZE).
      3. Overwrite that region in OUTPUT dt_na.dat with zero bytes.
      4. Restore the original offset and length from INPUT main.dol back
         into all language slots of the OUTPUT main.dol.

    Returns ``(success, dat_offset, data_length)``:
      - *success*     — True on success, False on any error.
      - *dat_offset*  — byte offset of the removed block in dt_na.dat (0 on error).
      - *data_length* — byte length of the removed block (0 on error).
    """

    if not os.path.exists(OUTPUT_DOL):
        print(f"ERROR: Output DOL not found: {OUTPUT_DOL}")
        return False, 0, 0
    if not os.path.exists(OUTPUT_DAT):
        print(f"ERROR: Output dat not found: {OUTPUT_DAT}")
        return False, 0, 0

    dir_ptrs = _readDirPtrs()
    if not (0 <= chunk_number < len(dir_ptrs)):
        print(f"ERROR: chunk_number {chunk_number} out of range (0-{len(dir_ptrs) - 1})")
        return False, 0, 0

    entry_offset = dir_ptrs[chunk_number] + file_index * _ENTRY_SIZE

    # Step 1 — read current offset and length from OUTPUT main.dol
    with open(OUTPUT_DOL, 'rb') as dol:
        dol.seek(entry_offset + 4)
        cur_length = struct.unpack('>I', dol.read(4))[0]  # word[1] len_en
        cur_offset = struct.unpack('>I', dol.read(4))[0]  # word[2] offset_en

    print(f"[1] Current DOL entry: chunk={chunk_number}, file_index={file_index}, "
          f"offset=0x{cur_offset:08X} ({cur_offset:,}), "
          f"length=0x{cur_length:08X} ({cur_length:,} bytes)")

    # Step 2 — verify the offset is in the hammerspace region
    if cur_offset < BASE_SIZE:
        print(f"    ERROR: offset 0x{cur_offset:08X} is not in the hammerspace region "
              f"(BASE_SIZE=0x{BASE_SIZE:08X}). Nothing to remove.")
        return False, 0, 0

    # Step 3 — zero out the block in OUTPUT dt_na.dat
    dat_size = os.path.getsize(OUTPUT_DAT)
    if cur_offset + cur_length > dat_size:
        print(f"    ERROR: block at 0x{cur_offset:08X} + {cur_length:,} extends beyond "
              f"dat file size {dat_size:,}. Aborting.")
        return False, 0, 0

    print(f"[2] Zeroing {cur_length:,} bytes at 0x{cur_offset:08X} in OUTPUT dt_na.dat ...")
    zeroed = 0
    with open(OUTPUT_DAT, 'r+b') as f:
        f.seek(cur_offset)
        while zeroed < cur_length:
            write_size = min(CHUNK_SIZE, cur_length - zeroed)
            f.write(b'\x00' * write_size)
            zeroed += write_size
    print(f"    Zeroed {cur_length:,} bytes.")

    # Step 4 — read original offset and length from INPUT main.dol
    orig_offset, orig_length = readDolEntry(chunk_number, file_index)
    if orig_offset == -1:
        print("    ERROR: Could not read original DOL entry from input.")
        return False, 0, 0
    print(f"[3] Original DOL entry: offset=0x{orig_offset:08X} ({orig_offset:,}), "
          f"length=0x{orig_length:08X} ({orig_length:,} bytes)")

    # Step 5 — restore original values into all language slots of OUTPUT main.dol
    print(f"[4] Restoring original DOL entry ...")
    patchDolEntry(chunk_number, file_index, orig_offset, orig_length)

    print(f"\nDone. chunk={chunk_number}, file_index={file_index} removed from hammerspace.")
    return True, cur_offset, cur_length