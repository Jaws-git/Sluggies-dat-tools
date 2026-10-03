"""DAT hammerspace for roster-expansion files, on the in-memory DOL and the recorded ``ledger.DatFile``.

The same rules as the model patcher's allocator
(``HammerspaceHelper.allocateHammerspace``): space comes from the region past
``BASE_SIZE``, 32-aligned, never overlapping a range that a DOL directory
record routes into hammerspace (those blocks are live even where their bytes
are zero), and the file grows when no zero run is long enough. The
difference: the routes come from the run's ``DolImage`` and the bytes from
the ``DatFile`` (pending writes included), so steps of one run see each
other's copies before anything is on disk.

Because every file a step places here is DOL-routed, the model patcher and
the icon pipeline see it as reserved too.
"""

import struct

import numpy as np

try:
    from ..Hammerspace import HammerspaceHelper as hh
except ImportError:
    from Hammerspace import HammerspaceHelper as hh

BASE_SIZE = hh.BASE_SIZE
ALIGN = hh.HS_ALIGN_BYTES
BUFFER = hh.HS_BUFFER_BYTES
RECORD_SIZE = 48
LANG_SLOTS = ((1, 2, 3), (5, 6, 7), (9, 10, 11))    # (length, offset, alloc) word indices: en, sp, fr
LANGS = ('en', 'sp', 'fr')
SCAN_CHUNK = 1 << 20


class DatHammerspaceError(RuntimeError):
    pass


def dol_base_address(file_offset: int) -> int:
    """Load address of a DOL file offset in the data sections that hold the directory records (D4/D5)."""
    return file_offset + hh._DOL_BASE


def dir_pointers(image) -> list[int]:
    table = dol_base_address(hh._DIRS_START)
    return list(struct.unpack('>' + 'I' * hh._DIRS_COUNT, image.read(table, 4 * hh._DIRS_COUNT)))


def iter_records(image):
    """Yield ``(chunk, file index, record address, 12 words)`` for every DAT record, each directory bounded
    by the next directory's start (as ``HammerspaceHelper._iterDirRecords`` does on a DOL file)."""
    pointers = dir_pointers(image)
    starts = set(pointers)
    for chunk, start in enumerate(pointers):
        record, index = start, 0
        while True:
            if index and record in starts:
                break
            if not image.is_mapped(record, RECORD_SIZE):
                break
            words = struct.unpack('>12I', image.read(record, RECORD_SIZE))
            if words[0] != hh._DAT_FNAME_PTR:
                break
            yield chunk, index, record, words
            index += 1
            record += RECORD_SIZE


def routed_ranges(image, base: int | None = None) -> list[tuple[int, int]]:
    """Every ``(offset, length)`` a record (any language) routes past ``base`` (default ``BASE_SIZE``)."""
    base = BASE_SIZE if base is None else base
    ranges = set()
    for _c, _i, _r, words in iter_records(image):
        for length_word, offset_word, _alloc in LANG_SLOTS:
            if words[offset_word] >= base and words[length_word]:
                ranges.add((words[offset_word], words[length_word]))
    return sorted(ranges)


def read_record(image, address: int) -> list[int]:
    words = list(struct.unpack('>12I', image.read(address, RECORD_SIZE)))
    if words[0::4] != [hh._DAT_FNAME_PTR] * 3:
        raise DatHammerspaceError(f'0x{address:08X} is not a dt_na.dat directory record')
    return words


def slot(words: list[int], lang: str) -> tuple[int, int, int]:
    """``(offset, length, alloc)`` of one language slot."""
    length_word, offset_word, alloc_word = LANG_SLOTS[LANGS.index(lang)]
    return words[offset_word], words[length_word], words[alloc_word]


def set_slot(words: list[int], lang: str, offset: int, length: int) -> None:
    length_word, offset_word, alloc_word = LANG_SLOTS[LANGS.index(lang)]
    words[length_word], words[offset_word], words[alloc_word] = length, offset, length


def write_record(image, address: int, words: list[int]) -> None:
    image.write(address, struct.pack('>12I', *words))


def _busy_blocks(chunk: bytes) -> np.ndarray:
    """One flag per 32-byte block: True when any byte is non-zero."""
    blocks = np.frombuffer(chunk, dtype='>u8').reshape(-1, ALIGN // 8)
    return blocks.any(axis=1)


def _scan(dat, length: int, reserved, base: int) -> tuple[int | None, int]:
    """``(first fitting offset or None, start of the zero run that reaches the end of the file)``.

    The last, partial block counts as zero-padded, so a fitting run may end
    up to 31 bytes past the end (``allocate`` grows the file over it)."""
    start = -(-base // ALIGN) * ALIGN
    end = -(-dat.size // ALIGN) * ALIGN
    need = -(-length // ALIGN)
    spans = sorted((o, o + n) for o, n in reserved if n > 0)
    run_start = start // ALIGN                              # global block index where the current zero run began
    pos = start
    while pos < end:
        size = min(SCAN_CHUNK, end - pos)
        busy = _busy_blocks(dat.read_padded(pos, size)).copy()
        first = pos // ALIGN
        for lo, hi in spans:                                # reserved blocks count as busy
            if hi <= pos or lo >= pos + size:
                continue
            busy[max(lo, pos) // ALIGN - first:-(-min(hi, pos + size) // ALIGN) - first] = True
        edges = np.concatenate(([run_start - 1 - first], np.flatnonzero(busy)))
        gaps = np.diff(edges) - 1
        hit = np.flatnonzero(gaps >= need)
        if hit.size:
            return int(first + edges[hit[0]] + 1) * ALIGN, end
        run_start = first + int(edges[-1]) + 1
        pos += size
    if end // ALIGN - run_start >= need:
        return run_start * ALIGN, end
    return None, max(run_start * ALIGN, start)


def find_free(dat, length: int, reserved, base: int | None = None) -> int | None:
    """First 32-aligned run of ``length`` zero bytes past ``base`` that overlaps no reserved range."""
    return _scan(dat, length, reserved, BASE_SIZE if base is None else base)[0]


def allocate(dat, length: int, reserved, base: int | None = None) -> int:
    """A free offset for ``length`` bytes; grows the file (zero-filled) when nothing fits."""
    base = BASE_SIZE if base is None else base
    found, trailing = _scan(dat, length, reserved, base)
    if found is not None:
        if found + length > dat.size:
            dat.grow(found + length + BUFFER)
        return found
    # The zero run at the end of the file continues into the grown part, so a rerun over the
    # same (zeroed) space finds the same offsets.
    dat.grow(trailing + length + BUFFER)
    return trailing
