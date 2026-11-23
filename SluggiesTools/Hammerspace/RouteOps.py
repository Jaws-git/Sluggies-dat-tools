"""Operations on one route that need the DAT and its DOL records together: allocating hammerspace around
the routed ranges, zeroing a block nothing points at, and giving a record its original model back
(``removeModelFromHammerspace``, ``restoreSplitBaseline``, ``zeroOriginalModel``).

No build code: ``SlotTarget``, ``StadiumCollision``, ``RouteWriter`` and (lazily) ``UntanglePolicy`` call
into this module, so of those it imports ``UntanglePolicy`` only. Paths and sizes are read from
``HammerspaceHelper`` at call time (tests replace them there).
"""

import os
import struct

# Loaded as a top-level module by the patchers and as ``Hammerspace.RouteOps`` by the roster: the siblings
# come from the same place, so a caller and this module share one set of constants and caches.
if __package__:
    from . import HammerspaceHelper as hh
    from . import DatAllocation
    from . import DolRecords
    from . import UntanglePolicy
else:
    import HammerspaceHelper as hh
    import DatAllocation
    import DolRecords
    import UntanglePolicy

import slogger as _slogger                     # HammerspaceHelper put SluggiesTools on sys.path


def zeroRangeIfUnrouted(offset: int, length: int) -> bool:
    """Zero one OUTPUT_DAT range unless an output-DOL route still points into it.

    Returns True when the range was zeroed."""
    live = DolRecords.liveRoutesInto(offset, length)
    if live:
        routes = ', '.join(f'({c},{i})' for c, i in live)
        _slogger.warning(
            f"Not zeroing 0x{offset:08X} ({length:,} bytes): still used by live route(s) {routes}.",
            source="hammerspace.helper",
        )
        return False
    DatAllocation.zeroRange(offset, length)
    return True


def allocateHammerspace(length: int, reserved_ranges=None) -> int:
    """Return an aligned free hammerspace offset for ``length`` bytes.

    ``reserved_ranges`` defaults to every routed hammerspace range. When no
    free run exists, OUTPUT_DAT is grown past its current end and searched
    again, the same way WriteModelBlock does it."""
    if reserved_ranges is None:
        reserved_ranges = DolRecords.routedHammerspaceRanges()
    offset = DatAllocation.findFreeMemoryChunk(length, reserved_ranges=reserved_ranges)
    if offset != -1:
        return offset
    if not DatAllocation.ensureOutputDat():
        raise RuntimeError('Unable to prepare output dt_na.dat')
    current_size = os.path.getsize(hh.OUTPUT_DAT)
    region_start = (max(current_size, hh.BASE_SIZE) + hh.HS_ALIGN_BYTES - 1) & ~(hh.HS_ALIGN_BYTES - 1)
    if not DatAllocation.ensureOutputDat(region_start + length + hh.HS_BUFFER_BYTES):
        raise RuntimeError('Unable to prepare output dt_na.dat')
    offset = DatAllocation.findFreeMemoryChunk(length, reserved_ranges=reserved_ranges)
    if offset == -1:
        raise RuntimeError('No contiguous hammerspace region found after expansion')
    return offset


def restoreSplitBaseline(chunk_number: int, file_index: int, baseline: bytes | None = None) -> tuple:
    """Return a split route (an unused character's, see UntanglePolicy) to its baseline.

    The baseline is a fresh copy in hammerspace, used by no other route:
    ``baseline`` when given (the vanilla entry with its untangled textures
    re-applied, see UntangledTextures.split_baseline), else the route's
    vanilla bytes verbatim. The vanilla block itself belongs to the playable
    owner and is never written. The block the route left is zeroed unless
    another route still uses it.

    Also repairs a route that an older unpatch sent back onto the shared
    vanilla block. Returns ``(success, removed_offset, removed_length)`` like
    ``removeModelFromHammerspace``; ``(True, 0, 0)`` when nothing was removed.
    """
    orig_offset, orig_length = DolRecords.readDolEntry(chunk_number, file_index)
    cur_offset, cur_length = DolRecords.readOutputDolEntry(chunk_number, file_index)
    if orig_offset == -1 or cur_offset == -1 or orig_length <= 0:
        _slogger.error(
            f"Cannot read DOL entry chunk={chunk_number}, file_index={file_index}.",
            source="hammerspace.helper",
        )
        return False, 0, 0

    with open(hh.INPUT_DAT, 'rb') as source:
        source.seek(orig_offset)
        vanilla = source.read(orig_length)
    if len(vanilla) != orig_length:
        _slogger.error(f"Short read of vanilla block at 0x{orig_offset:08X}.", source="hammerspace.helper")
        return False, 0, 0
    if baseline is not None:
        if len(baseline) != orig_length:
            _slogger.error(
                f"Baseline for ({chunk_number},{file_index}) is {len(baseline):,} bytes, "
                f"the vanilla entry {orig_length:,}.",
                source="hammerspace.helper",
            )
            return False, 0, 0
        vanilla = baseline

    on_own_copy = cur_offset >= hh.BASE_SIZE
    if on_own_copy and cur_length == orig_length:
        with open(hh.OUTPUT_DAT, 'rb') as output:
            output.seek(cur_offset)
            if output.read(cur_length) == vanilla:
                _slogger.info(
                    f"Split route ({chunk_number},{file_index}) already holds its "
                    f"baseline copy at 0x{cur_offset:08X}; nothing to do.",
                    source="hammerspace.helper",
                )
                return True, 0, 0

    new_offset = allocateHammerspace(orig_length)
    DatAllocation.writeModelBlock(vanilla, new_offset)
    DolRecords.patchDolEntry(chunk_number, file_index, new_offset, orig_length)
    DatAllocation.patchFstFileSize(os.path.getsize(hh.OUTPUT_DAT))
    _slogger.info(
        f"Split route ({chunk_number},{file_index}) now reads a fresh "
        f"{'untangled' if baseline is not None else 'verbatim'} copy of its "
        f"vanilla block 0x{orig_offset:08X} at 0x{new_offset:08X}.",
        source="hammerspace.helper",
    )

    if not on_own_copy:
        _slogger.info(
            f"The route was on the shared block 0x{cur_offset:08X}; that block belongs to "
            "its playable owner and was left untouched.",
            source="hammerspace.helper",
        )
        return True, 0, 0
    if zeroRangeIfUnrouted(cur_offset, cur_length):
        return True, cur_offset, cur_length
    return True, 0, 0


def _restore_original_model_bytes(orig_offset: int, orig_length: int) -> None:
    """Copy one original model range from INPUT DAT back into OUTPUT DAT."""
    if not os.path.exists(hh.INPUT_DAT):
        raise FileNotFoundError(f"Input dat not found: {hh.INPUT_DAT}")
    input_size = os.path.getsize(hh.INPUT_DAT)
    output_size = os.path.getsize(hh.OUTPUT_DAT)
    range_end = orig_offset + orig_length
    if range_end > input_size:
        raise ValueError(
            f"original model range 0x{orig_offset:08X}+{orig_length:,} exceeds "
            f"input dat size {input_size:,}")
    if range_end > output_size:
        raise ValueError(
            f"original model range 0x{orig_offset:08X}+{orig_length:,} exceeds "
            f"output dat size {output_size:,}")

    restored = 0
    with open(hh.INPUT_DAT, 'rb') as source, open(hh.OUTPUT_DAT, 'r+b') as target:
        source.seek(orig_offset)
        target.seek(orig_offset)
        while restored < orig_length:
            read_size = min(hh.CHUNK_SIZE, orig_length - restored)
            data = source.read(read_size)
            if len(data) != read_size:
                raise IOError(
                    f"short read restoring original model: expected {read_size:,}, "
                    f"got {len(data):,}")
            target.write(data)
            restored += len(data)

    with open(hh.INPUT_DAT, 'rb') as source, open(hh.OUTPUT_DAT, 'rb') as target:
        source.seek(orig_offset)
        target.seek(orig_offset)
        remaining = orig_length
        while remaining:
            read_size = min(hh.CHUNK_SIZE, remaining)
            if source.read(read_size) != target.read(read_size):
                raise IOError(
                    f"restored output bytes do not match input at model offset "
                    f"0x{orig_offset:08X}")
            remaining -= read_size


def removeModelFromHammerspace(chunk_number: int, file_index: int, split_baseline: bytes | None = None) -> tuple:
    """Remove a model block that was previously written to hammerspace.

    An unused-character route (UntanglePolicy) goes to restoreSplitBaseline
    instead, with ``split_baseline`` as its baseline bytes.

    Steps:
      1. Read the current offset and length from the OUTPUT main.dol.
        2. Restore the original model bytes from INPUT dt_na.dat.
        3. Restore the original offset and length from INPUT main.dol back
            into all language slots of the OUTPUT main.dol.
        4. Overwrite the obsolete hammerspace region with zero bytes.

    Returns ``(success, dat_offset, data_length)``:
      - *success*     — True on success, False on any error.
      - *dat_offset*  — byte offset of the removed block in dt_na.dat (0 on error).
      - *data_length* — byte length of the removed block (0 on error).
    """

    if not os.path.exists(hh.OUTPUT_DOL):
        _slogger.error(f"Output DOL not found: {hh.OUTPUT_DOL}", source="hammerspace.helper")
        return False, 0, 0
    if not os.path.exists(hh.OUTPUT_DAT):
        _slogger.error(f"Output dat not found: {hh.OUTPUT_DAT}", source="hammerspace.helper")
        return False, 0, 0

    if DolRecords.isOwnDir(chunk_number):
        _slogger.error(
            f"chunk={chunk_number} is a new ID's own model directory: it has no vanilla route to restore. The "
            "roster rebuild resets it (start.py --clear-slot).", source="hammerspace.helper")
        return False, 0, 0

    dir_ptrs = DolRecords._readDirPtrs()
    if not (0 <= chunk_number < len(dir_ptrs)):
        _slogger.error(f"chunk_number {chunk_number} out of range (0-{len(dir_ptrs) - 1})", source="hammerspace.helper")
        return False, 0, 0

    entry_offset = dir_ptrs[chunk_number] + file_index * hh._ENTRY_SIZE

    # Step 1 — read current offset and length from OUTPUT main.dol
    with open(hh.OUTPUT_DOL, 'rb') as dol:
        dol.seek(entry_offset + 4)
        cur_length = struct.unpack('>I', dol.read(4))[0]  # word[1] len_en
        cur_offset = struct.unpack('>I', dol.read(4))[0]  # word[2] offset_en

    if UntanglePolicy.is_split(chunk_number, file_index):
        _slogger.info(
            f"[1] chunk={chunk_number}, file_index={file_index} is an unused-character route "
            f"(currently 0x{cur_offset:08X}, {cur_length:,} bytes); restoring its own "
            "verbatim copy instead of the shared vanilla route.",
            source="hammerspace.helper",
        )
        return restoreSplitBaseline(chunk_number, file_index, split_baseline)

    shared = UntanglePolicy.independent_sharers(
        chunk_number, file_index, DolRecords.findSharedEntries(chunk_number, file_index))

    _slogger.info(f"[1] Current DOL entry: chunk={chunk_number}, file_index={file_index}, "
          f"offset=0x{cur_offset:08X} ({cur_offset:,}), "
          f"length=0x{cur_length:08X} ({cur_length:,} bytes)", source="hammerspace.helper")

    orig_offset, orig_length = DolRecords.readDolEntry(chunk_number, file_index)
    if orig_offset == -1:
        _slogger.error("Could not read original DOL entry from input.", source="hammerspace.helper")
        return False, 0, 0
    _slogger.info(f"[2] Original DOL entry: offset=0x{orig_offset:08X} ({orig_offset:,}), "
          f"length=0x{orig_length:08X} ({orig_length:,} bytes)", source="hammerspace.helper")

    # A prior broken unpatch may already have restored the DOL route while
    # leaving the original DAT range zeroed. Re-running unpatch repairs it.
    if cur_offset < hh.BASE_SIZE:
        if (cur_offset, cur_length) == (orig_offset, orig_length):
            try:
                _restore_original_model_bytes(orig_offset, orig_length)
            except (OSError, ValueError) as exc:
                _slogger.error(f"Could not restore original model bytes: {exc}", source="hammerspace.helper")
                return False, 0, 0
            _slogger.info(
                "DOL entry was already restored; original model bytes were repaired and verified.",
                source="hammerspace.helper",
            )
            return True, 0, 0
        _slogger.error(f"offset 0x{cur_offset:08X} is not in the hammerspace region "
              f"(BASE_SIZE=0x{hh.BASE_SIZE:08X}). Nothing to remove.", source="hammerspace.helper")
        return False, 0, 0

    dat_size = os.path.getsize(hh.OUTPUT_DAT)
    if cur_offset + cur_length > dat_size:
        _slogger.error(f"block at 0x{cur_offset:08X} + {cur_length:,} extends beyond "
              f"dat file size {dat_size:,}. Aborting.", source="hammerspace.helper")
        return False, 0, 0

    try:
        _restore_original_model_bytes(orig_offset, orig_length)
    except (OSError, ValueError) as exc:
        _slogger.error(f"Could not restore original model bytes: {exc}", source="hammerspace.helper")
        return False, 0, 0
    _slogger.info(
        f"[3] Restored and verified {orig_length:,} original model bytes at "
        f"0x{orig_offset:08X}.",
        source="hammerspace.helper",
    )

    _slogger.info("[4] Restoring original DOL entry ...", source="hammerspace.helper")
    DolRecords.patchDolEntry(chunk_number, file_index, orig_offset, orig_length)

    # Also restore entries that referenced the same hammerspace block before
    # the primary route was restored above.
    if shared:
        _slogger.info(f"    Restoring {len(shared)} shared chunk reference(s):", source="hammerspace.helper")
        for sc, si in shared:
            DolRecords.patchDolEntry(sc, si, orig_offset, orig_length)

    _slogger.info(f"[5] Zeroing {cur_length:,} bytes at 0x{cur_offset:08X} in OUTPUT dt_na.dat ...", source="hammerspace.helper")
    if zeroRangeIfUnrouted(cur_offset, cur_length):       # the DOL is restored: a route left here is a sharer's
        _slogger.info(f"    Zeroed {cur_length:,} bytes.", source="hammerspace.helper")

    _slogger.info(f"Done. chunk={chunk_number}, file_index={file_index} removed from hammerspace.", source="hammerspace.helper")
    return True, cur_offset, cur_length


def zeroOriginalModel(chunk_number: int, file_index: int) -> None:
    """Zero out the original model data in OUTPUT dt_na.dat.

    Reads the original offset and length from the INPUT main.dol and
    overwrites that region with zeros in the OUTPUT dat.  This is useful
    for testing that the game truly loads from hammerspace rather than
    falling back to stale original data.

    Never zeroes for an unused-character route (UntanglePolicy): its vanilla
    range is its playable owner's block. Nothing to do for an own model
    directory (``isOwnDir``): it has no vanilla range of its own.

    Call this AFTER patchDolEntry (and shared entry patching) has redirected
    all DOL references away from the original location.

    The range is left alone when an output-DOL route still points into it,
    for example a playable character whose vanilla block an unused
    character's route was split from.
    """

    if DolRecords.isOwnDir(chunk_number):
        # Its files are hammerspace copies; WriteModelBlock zeroes the replaced copy itself.
        return

    if UntanglePolicy.is_split_dir(chunk_number):
        _slogger.info(
            f"Not zeroing the vanilla range of unused-character route ({chunk_number},{file_index}): "
            "it belongs to the playable owner.",
            source="hammerspace.helper",
        )
        return

    orig_offset, orig_length = DolRecords.readDolEntry(chunk_number, file_index)
    if orig_offset == -1:
        _slogger.error("Could not read original DOL entry for zeroing.", source="hammerspace.helper")
        return

    if not os.path.exists(hh.OUTPUT_DAT):
        _slogger.error("Output dat not found for zeroing.", source="hammerspace.helper")
        return

    live = DolRecords.liveRoutesInto(orig_offset, orig_length)
    if live:
        routes = ', '.join(f'({c},{i})' for c, i in live)
        _slogger.warning(
            f"Not zeroing original model data at 0x{orig_offset:08X} ({orig_length:,} bytes): "
            f"still used by live route(s) {routes}.",
            source="hammerspace.helper",
        )
        return

    _slogger.info(f"Zeroing original model data: 0x{orig_offset:08X}, {orig_length:,} bytes ...", source="hammerspace.helper")
    DatAllocation.zeroRange(orig_offset, orig_length)
    _slogger.info(f"    Zeroed {orig_length:,} bytes at original location.", source="hammerspace.helper")
