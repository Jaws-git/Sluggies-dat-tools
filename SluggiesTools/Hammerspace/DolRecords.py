"""The model directory records of ``main.dol``: directory pointers, reading and patching one record
(``readDolEntry``, ``readOutputDolEntry``, ``patchDolEntry``), and which records point where
(``findSharedEntries``, ``liveRoutesInto``, ``routedHammerspaceRanges``).

Paths and table constants are read from ``HammerspaceHelper`` at call time (tests replace them there).
"""

import os
import struct
import sys

# Loaded as a top-level module by the patchers and as ``Hammerspace.DolRecords`` by the icon tools and the
# roster: the siblings come from the same place, so a caller and this module share one set of constants.
if __package__:
    from . import HammerspaceHelper as hh
    from . import DatAllocation
else:
    import HammerspaceHelper as hh
    import DatAllocation

import slogger as _slogger                     # HammerspaceHelper put SluggiesTools on sys.path


def _readDirPtrs() -> list[int]:
    """Read the directory pointer table from the INPUT main.dol.

    Returns a list of DOL file offsets (virtual address minus DOL base),
    one per directory entry. Kept while the file is unchanged: every
    entry lookup asks for the table."""

    stat = os.stat(hh.INPUT_DOL)
    key = (os.path.abspath(hh.INPUT_DOL), stat.st_mtime_ns, stat.st_size)
    if key not in _DIR_PTRS_CACHE:
        with open(hh.INPUT_DOL, 'rb') as dol:
            dol.seek(hh._DIRS_START)
            raw = dol.read(hh._DIRS_END - hh._DIRS_START)
        ptrs = [word - hh._DOL_BASE for word in struct.unpack(f'>{hh._DIRS_COUNT}I', raw)]
        _DIR_PTRS_CACHE.clear()
        _DIR_PTRS_CACHE[key] = ptrs
    return list(_DIR_PTRS_CACHE[key])


_DIR_PTRS_CACHE: dict = {}


_EXTRA_DIRS_CACHE: dict = {}


def _extraDirStarts(dol_path: str) -> list[int]:
    """File offsets of the directories past the stock table in ``dol_path``: own model directories of new
    IDs, whose records live in the roster's DOL data section behind a moved directory table
    (``Dol/dirtable.py``). Empty for the input DOL, a DOL without them, or a file that is no DOL."""
    if not os.path.exists(dol_path) or os.path.normcase(os.path.abspath(dol_path)) ==             os.path.normcase(os.path.abspath(hh.INPUT_DOL)):
        return []
    stat = os.stat(dol_path)
    key = (os.path.abspath(dol_path), stat.st_mtime_ns, stat.st_size)
    if key not in _EXTRA_DIRS_CACHE:
        from Dol import dirtable, dolfile
        try:
            with open(dol_path, 'rb') as dol:
                image = dolfile.DolImage(dol.read())
            pointers = dirtable.pointers(image)[hh._DIRS_COUNT:]
            starts = [image.offset_of(ptr, hh._ENTRY_SIZE) for ptr in pointers]
        except (dolfile.DolError, struct.error, ValueError):
            starts = []
        _EXTRA_DIRS_CACHE.clear()
        _EXTRA_DIRS_CACHE[key] = starts
    return _EXTRA_DIRS_CACHE[key]


def _outputDirPtrs() -> list[int]:
    """Record offsets of every directory the OUTPUT main.dol has: the stock table (input DOL), then the own
    model directories of new IDs (``_extraDirStarts``). Only the stock ones without an output DOL."""
    return _readDirPtrs() + _extraDirStarts(hh.OUTPUT_DOL)


def isOwnDir(chunk_number: int) -> bool:
    """Whether ``chunk_number`` is past the stock directory table: an own model directory of a new ID
    (roster ``model_dirs``), whose files are hammerspace copies of a stock directory's."""
    return chunk_number >= hh._DIRS_COUNT


_OWN_SOURCES_CACHE: dict = {}


def ownDirSource(chunk_number: int) -> int | None:
    """The stock directory an own model directory was copied from (roster manifest of the OUTPUT main.dol), or
    None (a stock directory, or one the manifest does not list)."""
    if not isOwnDir(chunk_number) or not os.path.exists(hh.OUTPUT_DOL):
        return None
    stat = os.stat(hh.OUTPUT_DOL)
    key = (stat.st_mtime_ns, stat.st_size)
    if key not in _OWN_SOURCES_CACHE:
        roster_dir = os.path.join(hh._HS_TOOLS_DIR, 'Roster')
        if roster_dir not in sys.path:
            sys.path.insert(0, roster_dir)
        import ids
        import slots
        from Dol import dolfile
        try:
            with open(hh.OUTPUT_DOL, 'rb') as dol:
                image = dolfile.DolImage(dol.read())
            sources = {directory: source + ids.MODEL_DIR_BASE
                       for directory, source in slots.own_dirs(image).values()}
        except (dolfile.DolError, ValueError, struct.error):
            sources = {}
        _OWN_SOURCES_CACHE.clear()
        _OWN_SOURCES_CACHE[key] = sources
    return _OWN_SOURCES_CACHE[key].get(chunk_number)


def vanillaRoute(chunk_number: int, file_index: int) -> tuple[int, int] | None:
    """The INPUT route whose vanilla block an entry started from: the entry itself for a stock directory, the
    same file of the source directory for an own model directory (None when its source is unknown)."""
    if not isOwnDir(chunk_number):
        return chunk_number, file_index
    source = ownDirSource(chunk_number)
    return None if source is None else (source, file_index)


def _iterDirRecords(dol_path: str):
    """Yield ``(chunk_number, file_index, record_offset, words)`` for every DOL file record.

    Each directory is bounded the way ``export.load_dol_dirs`` bounds it: its
    records end at the first word that is not the DAT file-name pointer, or
    where the next directory's records begin. Directories are contiguous runs
    in the DOL, so an unbounded walk would keep going into the following
    directories and report the same physical record again under other chunk
    numbers."""

    dir_ptrs = _readDirPtrs() + _extraDirStarts(dol_path)
    with open(dol_path, 'rb') as dol:
        for cidx, dir_ptr in enumerate(dir_ptrs):
            other_ptrs = set(dir_ptrs[:cidx] + dir_ptrs[cidx + 1:])
            fidx = 0
            record = dir_ptr
            while record not in other_ptrs:
                dol.seek(record)
                raw = dol.read(hh._ENTRY_SIZE)
                if len(raw) < hh._ENTRY_SIZE:
                    break
                words = struct.unpack('>12I', raw)
                if words[0] != hh._DAT_FNAME_PTR:
                    break
                yield cidx, fidx, record, words
                fidx += 1
                record += hh._ENTRY_SIZE


def findSharedEntries(chunk_number: int, file_index: int) -> list[tuple[int, int]]:
    """Find output-DOL entries that currently share the given entry's dat offset.

    The output state is authoritative here: untangle mode intentionally splits
    entries that shared offsets in the stock DOL. Returns ``(chunk_number,
    file_index)`` tuples, excluding the specified entry. Each directory is
    walked only over its own records (see ``_iterDirRecords``), so the given
    entry's own record is never reported again under another chunk number.
    """

    dol_path = hh.OUTPUT_DOL if os.path.exists(hh.OUTPUT_DOL) else hh.INPUT_DOL
    if dol_path == hh.OUTPUT_DOL:
        target_offset, _ = readOutputDolEntry(chunk_number, file_index)
    else:
        target_offset, _ = readDolEntry(chunk_number, file_index)
    if target_offset == -1:
        return []

    target_record = (_readDirPtrs() + _extraDirStarts(dol_path))[chunk_number] + file_index * hh._ENTRY_SIZE
    return [
        (cidx, fidx)
        for cidx, fidx, record, words in _iterDirRecords(dol_path)
        if words[2] == target_offset and record != target_record
    ]


def liveRoutesInto(offset: int, length: int) -> list[tuple[int, int]]:
    """Return output-DOL routes whose data overlaps ``offset``..``offset+length``.

    Checks every language slot. Uses the OUTPUT main.dol, or the INPUT one
    when no output exists yet."""

    if length <= 0:
        return []
    dol_path = hh.OUTPUT_DOL if os.path.exists(hh.OUTPUT_DOL) else hh.INPUT_DOL
    end = offset + length
    live = []
    for cidx, fidx, _record, words in _iterDirRecords(dol_path):
        for length_word, offset_word in ((1, 2), (5, 6), (9, 10)):
            route_offset, route_length = words[offset_word], words[length_word]
            if route_length > 0 and route_offset < end and offset < route_offset + route_length:
                live.append((cidx, fidx))
                break
    return live


def routedHammerspaceRanges() -> list[tuple[int, int]]:
    """Return every ``(offset, length)`` the OUTPUT main.dol routes into hammerspace.

    Scans all model entries (own model directories of new IDs included) and
    all three language slots (en, sp, fr). Only
    ranges starting at or after ``BASE_SIZE`` are returned, deduplicated and
    sorted. These blocks are live even where their bytes are zero, so callers
    pass them to ``findFreeMemoryChunk`` as reserved ranges. Returns an empty
    list when the output DOL does not exist yet.
    """

    if not os.path.exists(hh.OUTPUT_DOL):
        return []

    ranges: set[tuple[int, int]] = set()
    for _cidx, _fidx, _record, words in _iterDirRecords(hh.OUTPUT_DOL):
        for length_word, offset_word in ((1, 2), (5, 6), (9, 10)):
            offset, length = words[offset_word], words[length_word]
            if offset >= hh.BASE_SIZE and length > 0:
                ranges.add((offset, length))
    return sorted(ranges)


class DolEntryError(RuntimeError):
    """A directory record of main.dol could not be found or written."""


def _readDirRecord(dol_path: str, dir_ptr: int, file_index: int) -> tuple | None:
    """The twelve words of record ``file_index`` of the directory at ``dir_ptr``, or None when that
    position holds no directory record (negative or past-the-end index, short file, or one of
    the three language slots does not start with the DAT file-name pointer)."""
    if file_index < 0:
        return None
    with open(dol_path, 'rb') as dol:
        dol.seek(dir_ptr + file_index * hh._ENTRY_SIZE)
        raw = dol.read(hh._ENTRY_SIZE)
    if len(raw) < hh._ENTRY_SIZE:
        return None
    words = struct.unpack('>12I', raw)
    if words[0] != hh._DAT_FNAME_PTR or words[4] != hh._DAT_FNAME_PTR or words[8] != hh._DAT_FNAME_PTR:
        return None
    return words


def readDolEntry(chunk_number: int, file_index: int) -> tuple[int, int]:
    """Read the offset and length for a model entry from the INPUT main.dol.

    Returns ``(offset_en, len_en)`` from the en language slot,
    or ``(-1, -1)`` if ``chunk_number`` is out of range."""

    dir_ptrs = _readDirPtrs()
    if not (0 <= chunk_number < len(dir_ptrs)):
        _slogger.error(f"chunk_number {chunk_number} out of range (0-{len(dir_ptrs) - 1})", source="hammerspace.helper")
        return -1, -1

    words = _readDirRecord(hh.INPUT_DOL, dir_ptrs[chunk_number], file_index)
    if words is None:
        _slogger.error(
            f"chunk={chunk_number}, file_index={file_index} is not a directory record of the input main.dol",
            source="hammerspace.helper")
        return -1, -1
    return words[2], words[1]


def readOutputDolEntry(chunk_number: int, file_index: int) -> tuple[int, int]:
    """Read the offset and length for a model entry from the OUTPUT main.dol.

    Unlike ``readDolEntry`` (which reads from the unmodified input), this
    reflects the current patched state written by previous hammerspace runs.
    Own model directories of new IDs (past the stock table) are included.

    Returns ``(offset_en, len_en)`` from the en language slot,
    or ``(-1, -1)`` if the output DOL does not exist or the chunk is out of range."""

    if not os.path.exists(hh.OUTPUT_DOL):
        return -1, -1

    dir_ptrs = _outputDirPtrs()
    if not (0 <= chunk_number < len(dir_ptrs)):
        return -1, -1

    words = _readDirRecord(hh.OUTPUT_DOL, dir_ptrs[chunk_number], file_index)
    if words is None:
        return -1, -1
    return words[2], words[1]


def patchDolEntry(chunk_number: int, file_index: int, new_offset: int, new_length: int) -> None:
    """Update the offset and length fields in the output main.dol for a model entry.

    Reads the directory pointer table from the INPUT main.dol (never modified),
    plus the own model directories of the OUTPUT main.dol, to locate the
    correct 48-byte entry, then writes ``new_offset`` and ``new_length``
    into all three language slots (en, sp, fr) of the OUTPUT main.dol.

    If the output main.dol does not yet exist, it is copied from the input first.

    Raises ``DolEntryError`` and writes nothing when the input main.dol is missing,
    ``chunk_number`` is out of range, or the position is not a directory record.

    Layout of each 48-byte entry (big-endian uint32 words):
      word[0]  = DAT_FNAME_PTR   (not modified)
      word[1]  = len_en          <- new_length
      word[2]  = offset_en       <- new_offset
      word[3]  = alloc_size_en   <- new_length
      word[4]  = DAT_FNAME_PTR   (not modified)
      word[5]  = len_sp          <- new_length
      word[6]  = offset_sp       <- new_offset
      word[7]  = alloc_size_sp   <- new_length
      word[8]  = DAT_FNAME_PTR   (not modified)
      word[9]  = len_fr          <- new_length
      word[10] = offset_fr       <- new_offset
      word[11] = alloc_size_fr   <- new_length
    """

    # Ensure output DOL exists
    if not os.path.exists(hh.OUTPUT_DOL):
        if not os.path.exists(hh.INPUT_DOL):
            raise DolEntryError(f"Input DOL not found: {hh.INPUT_DOL}")
        DatAllocation.copyFileAtomic(hh.INPUT_DOL, hh.OUTPUT_DOL)
        _slogger.info("Copied main.dol to output folder.", source="hammerspace.helper")

    dir_ptrs = _outputDirPtrs()

    if not (0 <= chunk_number < len(dir_ptrs)):
        raise DolEntryError(f"chunk_number {chunk_number} out of range (0-{len(dir_ptrs) - 1})")

    words = _readDirRecord(hh.OUTPUT_DOL, dir_ptrs[chunk_number], file_index)
    if words is None:
        raise DolEntryError(
            f"chunk={chunk_number}, file_index={file_index} is not a directory record of the output main.dol")

    # One record, one write: length, offset and allocation size of each language slot.
    record = struct.pack(
        '>12I',
        words[0], new_length, new_offset, new_length,   # en
        words[4], new_length, new_offset, new_length,   # sp
        words[8], new_length, new_offset, new_length,   # fr
    )
    with open(hh.OUTPUT_DOL, 'r+b') as dol:
        dol.seek(dir_ptrs[chunk_number] + file_index * hh._ENTRY_SIZE)
        dol.write(record)

    _slogger.info(f"Patched DOL entry: chunk={chunk_number}, file_index={file_index}, "
          f"offset=0x{new_offset:08X}, length=0x{new_length:X}", source="hammerspace.helper")
