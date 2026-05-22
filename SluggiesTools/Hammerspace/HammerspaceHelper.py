import os
import struct

BASE_SIZE = 715046144  # ~715 MB
CHUNK_SIZE = 1024 * 1024  # 1 MB read buffer
OUTPUT_DAT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '3_Output_Dat', 'dt_na.dat'))


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
        print(f"ERROR: File not found: {OUTPUT_DAT}")
        raise SystemExit(1)

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
            f.truncate(target_size)
            print(f"Trimmed {current_size - target_size:,} bytes from end of file.")

    print("Done.")



### Hammerspace Helpers

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


def patchPointerField(file_offset: int, new_absolute_target: int, relative_base: int) -> None:
    """Write a relative uint32 BE pointer into the dat file.

    Computes ``new_absolute_target - relative_base`` and writes the result as a
    big-endian uint32 at ``file_offset``."""

    relative_value = new_absolute_target - relative_base
    with open(OUTPUT_DAT, 'r+b') as f:
        f.seek(file_offset)
        f.write(struct.pack('>I', relative_value))


def removeChunk(name: str, pointerFieldOffset: int = None, relativeBase: int = None) -> int:
    """Overwrite a named chunk with zero bytes and optionally restore the original pointer.

    The original pointer value is recovered from the ``ooffset=<value>`` tag
    stored inside the chunk immediately after the start marker.  If both
    ``pointerFieldOffset`` and ``relativeBase`` are provided, ``patchPointerField``
    is called to write the original absolute offset back as a relative pointer.
    Pass neither (or ``None``) to erase the chunk without touching any pointer.

    Returns 0 on success, -1 if the chunk was not found or could not be parsed."""

    if not os.path.exists(OUTPUT_DAT):
        print(f"ERROR: File not found: {OUTPUT_DAT}")
        return -1

    start_marker = b'SLUGSTART' + name.encode('utf-8')
    end_marker   = b'\x00' * 8 + b'SLUGEND'

    with open(OUTPUT_DAT, 'r+b') as f:
        data = f.read()

    start_pos = data.find(start_marker)
    if start_pos == -1:
        return -1

    # Parse the ooffset tag to recover the original absolute data offset.
    tag_start = start_pos + len(start_marker)
    sep_pos   = data.find(b'\x00\x00', tag_start)
    if sep_pos == -1:
        return -1
    tag_str = data[tag_start:sep_pos].decode('utf-8')
    if not tag_str.startswith('ooffset='):
        return -1
    original_offset = int(tag_str[len('ooffset='):])

    end_pos = data.find(end_marker, sep_pos + 2)
    if end_pos == -1:
        return -1

    erase_start = start_pos
    erase_end   = end_pos + len(end_marker)

    with open(OUTPUT_DAT, 'r+b') as f:
        f.seek(erase_start)
        f.write(b'\x00' * (erase_end - erase_start))

    if pointerFieldOffset is not None and relativeBase is not None:
        patchPointerField(pointerFieldOffset, original_offset, relativeBase)
    return 0


def findChunk(name: str) -> tuple[int, int]:
    """Locate a named chunk and return ``(data_start_offset, total_length)``.

    ``data_start_offset`` is the absolute file offset of the first payload byte
    (past the ``SLUGSTART<name>`` marker, the ``ooffset=<value>`` tag, and the
    two separator zero bytes).  ``total_length`` spans from the first byte of
    the start marker through the last byte of the end marker.

    Returns ``(-1, -1)`` if the chunk is not found."""

    if not os.path.exists(OUTPUT_DAT):
        print(f"ERROR: File not found: {OUTPUT_DAT}")
        return -1, -1

    start_marker = b'SLUGSTART' + name.encode('utf-8')
    end_marker   = b'\x00' * 8 + b'SLUGEND'

    with open(OUTPUT_DAT, 'rb') as f:
        data = f.read()

    start_pos = data.find(start_marker)
    if start_pos == -1:
        return -1, -1

    # Skip past the ooffset=<value> tag by finding the \x00\x00 separator.
    sep_pos = data.find(b'\x00\x00', start_pos + len(start_marker))
    if sep_pos == -1:
        return -1, -1
    data_start = sep_pos + 2

    end_pos = data.find(end_marker, data_start)
    if end_pos == -1:
        return -1, -1

    return data_start, end_pos + len(end_marker) - start_pos


def writeNewMemoryChunk(name: str, data: bytes, originalOffset: int,
                        pointerFieldOffset: int, relativeBase: int) -> int:
    """Write a named chunk into the hammerspace region of the dat file.

    ``originalOffset`` is the original absolute file offset of the data being
    replaced.  It is stored in the chunk as ``ooffset=<value>`` so that
    ``removeChunk`` can later restore it.

    After writing the chunk, ``patchPointerField`` is called to update the
    pointer at ``pointerFieldOffset`` to point to the new data in hammerspace,
    using ``relativeBase`` as the base for the relative offset calculation.

    If a chunk with the same name already exists it is first erased via
    ``removeChunk``.  The total space required is::

        len("SLUGSTART<name>") + len("ooffset=<value>") + 2 + len(data) + len("\\x00"*8 + "SLUGEND")

    Returns the file offset of the first byte of the new chunk on success,
    or -1 on failure."""

    if not os.path.exists(OUTPUT_DAT):
        print(f"ERROR: File not found: {OUTPUT_DAT}")
        return -1

    start_marker   = b'SLUGSTART' + name.encode('utf-8')
    offset_tag     = f'ooffset={originalOffset}'.encode('utf-8')
    end_marker     = b'\x00' * 8 + b'SLUGEND'

    total_length = len(start_marker) + len(offset_tag) + 2 + len(data) + len(end_marker)

    # Remove pre-existing chunk with the same name, if any.
    _, existing_len = findChunk(name)
    if existing_len != -1:
        if removeChunk(name, pointerFieldOffset, relativeBase) == -1:
            print(f"ERROR: Failed to remove existing chunk '{name}'.")
            return -1

    # Find a free region large enough for the framed payload.
    offset = findFreeMemoryChunk(total_length)
    if offset == -1:
        print(f"ERROR: Not enough free hammerspace to write chunk '{name}' ({total_length} bytes required).")
        return -1

    payload = start_marker + offset_tag + b'\x00\x00' + data + end_marker

    with open(OUTPUT_DAT, 'r+b') as f:
        f.seek(offset)
        f.write(payload)

    chunk_data_abs = offset + len(start_marker) + len(offset_tag) + 2
    patchPointerField(pointerFieldOffset, chunk_data_abs, relativeBase)
    return offset