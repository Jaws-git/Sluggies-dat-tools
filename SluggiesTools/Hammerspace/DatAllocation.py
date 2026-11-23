"""The output DAT as a file: creating it from the input, free space in hammerspace, block writes and zeroing.

Paths and sizes are read from ``HammerspaceHelper`` at call time (tests replace them there). Nothing here
reads ``main.dol``: the directory records are ``DolRecords``, the operations that need both are
``RouteOps``.
"""

import os
import shutil
import struct

# Loaded as a top-level module by the patchers and as ``Hammerspace.DatAllocation`` by the icon tools and the
# roster: the constants come from the HammerspaceHelper of the same kind, the one the caller holds.
if __package__:
    from . import HammerspaceHelper as hh
else:
    import HammerspaceHelper as hh

import slogger as _slogger                     # HammerspaceHelper put SluggiesTools on sys.path


# The backup of a running defragmentation (Defrag.py), beside the output DAT. While its info file exists the
# output files are half moved: Defrag restores them, and nothing else may read or write them before that.
DEFRAG_BACKUP_FOLDER = '_defrag_backup'


DEFRAG_BACKUP_INFO = 'defrag.json'


def defragBackupPending() -> bool:
    """Whether an interrupted defragmentation left its complete backup behind."""
    return os.path.isfile(os.path.join(os.path.dirname(hh.OUTPUT_DAT), DEFRAG_BACKUP_FOLDER, DEFRAG_BACKUP_INFO))


def copyFileAtomic(source: str, target: str) -> None:
    """Copy ``source`` to ``target`` through a temporary file beside it, so an interrupted copy never leaves a
    truncated ``target`` that later runs take for a complete file. Raises ``OSError``."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temp = target + '.copying'
    try:
        shutil.copy2(source, temp)
        os.replace(temp, target)
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def outputDatProblem() -> str | None:
    """Why the existing OUTPUT dt_na.dat cannot be a copy of the input (it is shorter: an interrupted copy or a
    cut-off file), or None. A patched output is never shorter than the input: blocks are only added past it."""
    if not (os.path.exists(hh.OUTPUT_DAT) and os.path.exists(hh.INPUT_DAT)):
        return None
    output_size, input_size = os.path.getsize(hh.OUTPUT_DAT), os.path.getsize(hh.INPUT_DAT)
    if output_size >= input_size:
        return None
    return (f"{hh.OUTPUT_DAT} is {output_size:,} bytes, shorter than the input dt_na.dat ({input_size:,} bytes): it is "
            "incomplete (an interrupted copy?). Delete it, or restore it from a backup, and run again; a fresh "
            "copy of the input is then made.")


def ensureOutputDat(required_total_size: int = 0) -> bool:
    """Ensure OUTPUT_DAT exists and is at least ``required_total_size`` bytes.

    If the file is missing it is copied from INPUT_DAT first (through a temporary file).  If it is
    smaller than ``required_total_size`` it is extended with zero bytes.

    Returns True on success, False if the input file is also missing or the existing output is shorter than
    the input (``outputDatProblem``)."""

    if not os.path.exists(hh.OUTPUT_DAT):
        if not os.path.exists(hh.INPUT_DAT):
            _slogger.error(f"OUTPUT dt_na.dat not found and INPUT dt_na.dat is also missing: {hh.INPUT_DAT}", source="hammerspace.helper")
            return False
        _slogger.info("OUTPUT dt_na.dat not found. Copying from input ...", source="hammerspace.helper")
        copyFileAtomic(hh.INPUT_DAT, hh.OUTPUT_DAT)
        _slogger.info(f"Copied {hh.INPUT_DAT} -> {hh.OUTPUT_DAT}", source="hammerspace.helper")
    else:
        problem = outputDatProblem()
        if problem:
            _slogger.error(problem, source="hammerspace.helper")
            return False

    if required_total_size > 0:
        current_size = os.path.getsize(hh.OUTPUT_DAT)
        if current_size < required_total_size:
            expand_by = required_total_size - current_size
            _slogger.info(f"Expanding OUTPUT dt_na.dat by {expand_by:,} bytes "
                  f"(current: {current_size:,} → target: {required_total_size:,}) ...", source="hammerspace.helper")
            with open(hh.OUTPUT_DAT, 'ab') as f:
                f.write(b'\x00' * expand_by)
            _slogger.info(f"Expansion complete. New size: {os.path.getsize(hh.OUTPUT_DAT):,} bytes", source="hammerspace.helper")

    return True


def patchFstFileSize(new_size: int) -> bool:
    """Update the dt_na.dat file size in the disc's FST so the game can read beyond the original bounds.

    Writes ``new_size`` (big-endian uint32) into the FST entry for dt_na.dat.
    The output FST is written to ``3_Output_Dat/fst.bin``, copied from the
    reference copy in ``Sluggers/DATA/sys/fst.bin`` on first use.

    Returns True on success, False if no source FST can be found."""

    if not os.path.exists(hh._FST_OUTPUT):
        if not os.path.exists(hh._FST_INPUT):
            _slogger.warning(f"FST source not found: {hh._FST_INPUT}\n"
                            "Disc filesystem size will NOT be updated.\n"
                            "The game may not be able to read hammerspace data.", source="hammerspace.helper")
            return False
        copyFileAtomic(hh._FST_INPUT, hh._FST_OUTPUT)
        _slogger.info("Copied fst.bin to output folder.", source="hammerspace.helper")

    with open(hh._FST_OUTPUT, 'r+b') as f:
        f.seek(hh._FST_DAT_SIZE_OFF)
        old_size = struct.unpack('>I', f.read(4))[0]
        if old_size == new_size:
            return True
        f.seek(hh._FST_DAT_SIZE_OFF)
        f.write(struct.pack('>I', new_size))

    _slogger.info(f"Patched FST: dt_na.dat size 0x{old_size:08X} -> 0x{new_size:08X} "
          f"({new_size:,} bytes)", source="hammerspace.helper")
    return True


def _normalize_reserved_ranges(reserved_ranges) -> list[tuple[int, int]]:
    """Return sorted, merged ``(start, end)`` pairs for non-empty ranges."""
    spans = sorted(
        (int(offset), int(offset) + int(length))
        for offset, length in (reserved_ranges or ())
        if int(length) > 0
    )
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def findFreeMemoryChunk(dataLength: int, reserved_ranges=()) -> int:
    """Scan the hammerspace region of the dat file for a contiguous run of
    ``dataLength`` zero bytes.

    The returned offset is guaranteed to be aligned to ``HS_ALIGN_BYTES``
    so the model block base preserves 32-byte absolute alignment for
    cache-line-sensitive GPL/SKN data.

    ``reserved_ranges`` is an iterable of ``(offset, length)`` pairs that are
    in use even when their bytes are zero, for example live model blocks that
    end in zero padding. No byte of the returned run overlaps a reserved
    range. Without it, a zero tail of a live block counts as free space, and a
    new block can be placed over it.

    Returns the file offset of the first such run, or -1 if no fitting
    aligned space is found."""

    if dataLength <= 0:
        return -1

    if not os.path.exists(hh.OUTPUT_DAT):
        _slogger.error(f"File not found: {hh.OUTPUT_DAT}", source="hammerspace.helper")
        return -1

    file_size = os.path.getsize(hh.OUTPUT_DAT)

    if file_size <= hh.BASE_SIZE:
        _slogger.info("No hammerspace region present (file is not larger than BASE_SIZE).", source="hammerspace.helper")
        return -1

    def align_up(value: int) -> int:
        return (value + hh.HS_ALIGN_BYTES - 1) // hh.HS_ALIGN_BYTES * hh.HS_ALIGN_BYTES

    # Only the gaps between the reserved ranges can hold the run, so only they are read.
    gaps = []
    cursor = align_up(hh.BASE_SIZE)
    for start, end in _normalize_reserved_ranges(reserved_ranges):
        if start >= file_size:
            break
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < file_size:
        gaps.append((cursor, file_size))

    with open(hh.OUTPUT_DAT, 'rb') as f:
        for gap_start, gap_end in gaps:
            candidate = align_up(gap_start)
            while candidate + dataLength <= gap_end:
                used_end = _nonZeroEnd(f, candidate, dataLength)
                if used_end == -1:
                    return candidate
                # Every aligned offset up to the non-zero byte found would hold that byte too.
                candidate = align_up(used_end)

    return -1


def _nonZeroEnd(f, offset: int, length: int) -> int:
    """-1 when the ``length`` bytes of ``f`` at ``offset`` are all zero. Else the offset just past a non-zero byte
    in that range: the last one of the first piece read that has any (a small first piece, so a used region
    costs one short read per step)."""
    f.seek(offset)
    done = 0
    piece_size = min(4096, hh.CHUNK_SIZE)
    while done < length:
        want = min(piece_size, length - done)
        piece = f.read(want)
        used = len(piece.rstrip(b'\x00'))
        if used:
            return offset + done + used
        if len(piece) < want:
            return offset + length          # the file ends inside the range
        done += want
        piece_size = hh.CHUNK_SIZE
    return -1


def writeModelBlock(data: bytes, offset: int) -> None:
    """Write raw model data into the dat file at the given offset.

    Ensures OUTPUT_DAT exists (copying from INPUT_DAT if necessary) and that
    the file is large enough to hold ``offset + len(data) + HS_BUFFER_BYTES``,
    expanding it with zero bytes when needed.

    ``offset`` should be a value returned by ``findFreeMemoryChunk``."""

    required_size = offset + len(data) + hh.HS_BUFFER_BYTES
    if not ensureOutputDat(required_size):
        raise FileNotFoundError(f"Cannot write model block: OUTPUT_DAT missing and INPUT_DAT not found ({hh.INPUT_DAT})")

    with open(hh.OUTPUT_DAT, 'r+b') as f:
        f.seek(offset)
        f.write(data)

    with open(hh.OUTPUT_DAT, 'rb') as f:
        f.seek(offset)
        written = f.read(len(data))
    if written != data:
        raise IOError(
            f'model block verification failed at 0x{offset:08X}: '
            f'expected {len(data):,} bytes, read {len(written):,}'
        )


def zeroRange(offset: int, length: int) -> None:
    """Overwrite one validated OUTPUT_DAT range with zero bytes."""
    if offset < 0 or length < 0:
        raise ValueError(f'invalid zero range: offset={offset}, length={length}')
    output_size = os.path.getsize(hh.OUTPUT_DAT)
    if offset + length > output_size:
        raise ValueError(
            f'zero range 0x{offset:08X}+{length:,} exceeds output dat size '
            f'{output_size:,}'
        )
    zeroed = 0
    with open(hh.OUTPUT_DAT, 'r+b') as output:
        output.seek(offset)
        while zeroed < length:
            write_size = min(hh.CHUNK_SIZE, length - zeroed)
            output.write(b'\x00' * write_size)
            zeroed += write_size


def writeDebugDumps(
    sluggie_name:  str,
    model_offset:  int,
    model_length:  int,
    block:         bytes,
    original_block: bytes | None = None,
) -> None:
    """Write debug copies of the original and hammerspace model blocks.

    Creates ``3_Output_Dat/SluggDebugg/`` if it does not already exist, then
    writes two files named after ``sluggie_name``:

      * ``<sluggie_name>_Original.SluggDebugg``   — the original model block
        read verbatim from INPUT dt_na.dat at ``model_offset``.
      * ``<sluggie_name>_Hammerspace.SluggDebugg`` — the assembled hammerspace
        block passed as ``block``.

    ``original_block``: the original block when it is not in INPUT (a slot
    export's embedded donor entry).

    Existing files with the same names are overwritten.
    """
    debug_dir = os.path.join(os.path.dirname(hh.OUTPUT_DAT), 'SluggDebugg')
    os.makedirs(debug_dir, exist_ok=True)

    orig_path = os.path.join(debug_dir, f"{sluggie_name}_Original.SluggDebugg")
    if original_block is not None:
        orig_block = original_block
    else:
        with open(hh.INPUT_DAT, 'rb') as f_in:
            f_in.seek(model_offset)
            orig_block = f_in.read(model_length)
    with open(orig_path, 'wb') as f_out:
        f_out.write(orig_block)
    _slogger.info(f"[Debug] Original block    → {orig_path}", source="hammerspace.helper")

    hs_path = os.path.join(debug_dir, f"{sluggie_name}_Hammerspace.SluggDebugg")
    with open(hs_path, 'wb') as f_out:
        f_out.write(block)
    _slogger.info(f"[Debug] Hammerspace block → {hs_path}", source="hammerspace.helper")
