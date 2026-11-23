"""Writing a built block into hammerspace and routing the game to it.

``WriteModelBlock`` checks the block against its high- / low-poly partner and its target slot, writes
it and repoints the DOL directory records; ``_write_route`` is the one place that allocates, writes,
routes and frees. Also the slot operations built on it: slot blocks, equipment, animations, unpatch
and clear.
"""

import os
import struct
from dataclasses import replace

import slogger as _slogger
import HammerspaceHelper as hh
import DatAllocation
import DolRecords
import RouteOps
import LodPartnerGuard
import LodTextureSync
import SlotTarget
import UntanglePolicy

from BlockAssembly import ModelBlockBuild


def CheckLodPartner(
    build: ModelBlockBuild,
    target: 'SlotTarget.Target | None' = None,
    partner_block: bytes | None = None,
) -> None:
    """Refuse a block that would break its high-/low-poly partner model
    (see LodPartnerGuard: a low-poly model may only draw on bones its
    high-poly partner has), and warn when an added bone is placed differently
    in the two models (the low-poly model's placement is ignored).

    With a *target* (SlotTarget), the partner is the target slot's other
    model, whatever its name; a high-poly model used as the low variant too
    is its own partner, so there is nothing to check. Equipment (bats, gloves)
    has no partner model at all. *partner_block*: the partner's incoming
    block (``--lod-partner-block``: the run patches it right after this one),
    checked instead of the live partner."""
    if target is not None and (target.as_low or target.equipment):
        return
    chunk_number, file_index = target.route if target else (build.chunk_number, build.file_index)
    any_stem = target is not None
    if partner_block is not None and not _pairs_with(build.block, chunk_number, file_index, any_stem, partner_block):
        _slogger.warning(
            '[LOD] the incoming partner block is not the other model of this pair; checking the live partner',
            source='hammerspace.main',
        )
        partner_block = None
    errors = LodPartnerGuard.lod_partner_errors(build.block, chunk_number, file_index, any_stem, partner_block)
    if target is None:
        errors += LodTextureSync.low_bind_range_errors(build.block, chunk_number, file_index, partner_block)
    if errors:
        raise ValueError(' '.join(errors))
    for warning in LodPartnerGuard.lod_partner_warnings(
        build.block, chunk_number, file_index, any_stem, partner_block,
    ):
        _slogger.warning(f'[LOD] {warning}', source='hammerspace.main')


def _pairs_with(block: bytes, chunk_number: int, file_index: int, any_stem: bool, partner_block: bytes) -> bool:
    """Whether *partner_block* is the other model of *block*'s high-/low-poly pair at this entry."""
    own = LodPartnerGuard.act_summary(block)
    try:
        return own is not None and LodPartnerGuard.find_partner(
            chunk_number, file_index, own, any_stem, partner_block) is not None
    except (OSError, struct.error, ValueError):
        return False


def CheckTarget(build: ModelBlockBuild, target: 'SlotTarget.Target') -> None:
    """Refuse a block that may not go into *target*'s slot (SlotTarget.check); log its warnings."""
    for warning in SlotTarget.check(
        build.block, (build.chunk_number, build.file_index), target, build.validation_report,
    ):
        _slogger.warning(f'[Slot] {warning}', source='hammerspace.main')


def PreviewLodTextureSync(build: ModelBlockBuild, target: 'SlotTarget.Target | None' = None) -> None:
    """Dry-run counterpart of the texture assignment sync in WriteModelBlock:
    logs the differences and what would be changed, writes nothing. Skipped
    for a targeted write: the target's vanilla binds belong to another
    character, so the follow rule has nothing to compare with."""
    if target is not None:
        return
    if LodTextureSync.sync_low_block(build.block, build.chunk_number, build.file_index, dry_run=True) is build.block:
        LodTextureSync.sync_partner_of_high(
            build.chunk_number, build.file_index, build.block,
            LodPartnerGuard.read_current_block(build.chunk_number, build.file_index),
            dry_run=True,
        )


def WriteModelBlock(
    build: ModelBlockBuild,
    model_name: str,
    output_offset: int | None = None,
    target: 'SlotTarget.Target | None' = None,
    partner_block: bytes | None = None,
) -> int:
    """Write an assembled block to hammerspace and patch its output references.

    *target* (SlotTarget) writes the block to another character's slot instead
    of the route it was built from: the route patch, the sharers, the zeroed
    vanilla block and the LOD checks all use the target, and the source route
    is left alone. With ``target.as_low`` the block also becomes the slot's
    low-poly model, as a copy of its own (WriteModelBlock is called again for
    file 1). *partner_block*: see CheckLodPartner."""
    if not build.validation_report.get('valid'):
        raise ValueError('refusing to write a model block with a failed validation report')
    if target is not None:
        CheckTarget(build, target)
    CheckLodPartner(build, target, partner_block)
    chunk_number, file_index = target.route if target else (build.chunk_number, build.file_index)
    if target is None:
        # A low-poly model binds textures by index into its high-poly partner's
        # TEX; its binds must follow the partner's retargets (LodTextureSync).
        synced_block = LodTextureSync.sync_low_block(build.block, chunk_number, file_index)
        if synced_block is not build.block:
            build = replace(build, block=synced_block)
    high_before = LodPartnerGuard.read_current_block(chunk_number, file_index)
    new_offset = _write_route(
        build.block, chunk_number, file_index, model_name, output_offset,
        f' | Modes: {build.section_modes.as_dict()}'
        + (f' | Source: chunk {build.chunk_number}, file {build.file_index} | Slot: {target.describe()}'
           if target else ''),
        # a slot's bat or glove is its own: the vanilla block's other users keep it
        share_route=not (target is not None and target.equipment),
    )
    if target is None:
        LodTextureSync.sync_partner_of_high(chunk_number, file_index, build.block, high_before)
    elif target.as_low and file_index != SlotTarget.LOW_FILE:
        WriteModelBlock(build, model_name, target=replace(target, file_index=SlotTarget.LOW_FILE))
    # Last, and never fatal: the game files are complete at this point, and a dump that cannot
    # be written (disk full, the file open in another program) must not look like a failed patch.
    try:
        DatAllocation.writeDebugDumps(
            model_name,
            build.original_offset,
            build.original_length,
            build.block,
            original_block=build.donor_block,
        )
    except OSError as exc:
        _slogger.warning(f'Debug dumps not written | Model: {model_name} | {exc}', source='hammerspace.main')
    return new_offset


def _write_route(block: bytes, chunk_number: int, file_index: int, model_name: str,
                 output_offset: int | None = None, log_suffix: str = '', share_route: bool = True) -> int:
    """Write *block* to hammerspace and route ``(chunk_number, file_index)`` (and its sharers) to it; the route's
    vanilla block is zeroed when nothing else uses it, a replaced hammerspace block when it is unrouted.
    ``share_route=False`` routes only this entry (equipment targets: other routes that share the block keep it)."""
    current_offset, current_length = DolRecords.readOutputDolEntry(chunk_number, file_index)
    replacing_hammerspace_block = current_offset >= hh.BASE_SIZE
    if replacing_hammerspace_block:
        _slogger.info(
            f'Model already in hammerspace at 0x{current_offset:08X}; '
            'keeping the old route live until replacement commits',
            source='hammerspace.main',
        )

    # Live hammerspace blocks can end in zero padding, which the zero-byte scan
    # would otherwise treat as free. Placing the new block over the old block's
    # zero tail let the zeroRange below wipe the new block's header.
    reserved_ranges = list(DolRecords.routedHammerspaceRanges())
    if replacing_hammerspace_block:
        reserved_ranges.append((current_offset, current_length))
    if output_offset is None:
        new_offset = DatAllocation.findFreeMemoryChunk(len(block), reserved_ranges=reserved_ranges)
        if new_offset == -1:
            if not DatAllocation.ensureOutputDat():
                raise RuntimeError('Unable to prepare output dt_na.dat')
            current_size = os.path.getsize(hh.OUTPUT_DAT)
            next_region_start = (current_size + hh.HS_ALIGN_BYTES - 1) & ~(hh.HS_ALIGN_BYTES - 1)
            required_size = next_region_start + len(block) + hh.HS_BUFFER_BYTES
            if not DatAllocation.ensureOutputDat(required_size):
                raise RuntimeError('Unable to prepare output dt_na.dat')
            new_offset = DatAllocation.findFreeMemoryChunk(len(block), reserved_ranges=reserved_ranges)
            if new_offset == -1:
                raise RuntimeError('No contiguous hammerspace region found after expansion')
    else:
        new_offset = output_offset
        if new_offset < hh.BASE_SIZE:
            raise ValueError(
                f'explicit hammerspace offset 0x{new_offset:08X} is before '
                f'BASE_SIZE 0x{hh.BASE_SIZE:08X}'
            )
        if new_offset % hh.HS_ALIGN_BYTES:
            raise ValueError(
                f'explicit hammerspace offset 0x{new_offset:08X} is not '
                f'{hh.HS_ALIGN_BYTES}-byte aligned'
            )
        for reserved_offset, reserved_length in reserved_ranges:
            if (
                new_offset < reserved_offset + reserved_length
                and reserved_offset < new_offset + len(block)
            ):
                raise ValueError(
                    f'explicit hammerspace block 0x{new_offset:08X}+{len(block):,} '
                    f'overlaps a live routed range '
                    f'0x{reserved_offset:08X}+{reserved_length:,}'
                )

    if replacing_hammerspace_block and (
        new_offset < current_offset + current_length
        and current_offset < new_offset + len(block)
    ):
        raise RuntimeError(
            f'new hammerspace block 0x{new_offset:08X}+{len(block):,} overlaps the '
            f'live block 0x{current_offset:08X}+{current_length:,} it replaces; refusing to '
            'write, because zeroing the old block would corrupt the new one'
        )

    found_sharers = DolRecords.findSharedEntries(chunk_number, file_index) if share_route else []
    shared_entries = UntanglePolicy.independent_sharers(chunk_number, file_index, found_sharers)
    kept = [route for route in found_sharers if route not in shared_entries]
    if kept:
        _slogger.info(
            'Unused-character split: route(s) '
            + ', '.join(f'({c},{i})' for c, i in kept)
            + ' share this block but keep their own route',
            source='hammerspace.main',
        )
    # Every record this write will route is read before the first byte is written, so a record
    # that cannot be routed stops the run with nothing changed.
    routes = [(chunk_number, file_index)] + [tuple(route) for route in shared_entries]
    previous = {}
    for route in routes:
        entry = DolRecords.readOutputDolEntry(*route)
        if entry[0] == -1:
            entry = DolRecords.readDolEntry(*route)
        if entry[0] == -1:
            raise RuntimeError(f'chunk {route[0]}, file {route[1]} has no directory record in main.dol')
        previous[route] = entry

    DatAllocation.writeModelBlock(block, new_offset)
    routed = []
    try:
        for route in routes:
            DolRecords.patchDolEntry(*route, new_offset, len(block))
            routed.append(route)
    except Exception as exc:
        # Put the records already switched back on their old block (a re-run finds the sharers
        # by that offset), then free the new block again.
        _slogger.error(
            f'Hammerspace Log: Route write failed | Model: {model_name} | {type(exc).__name__}: {exc} | '
            'undoing the routes already written',
            source='hammerspace.main',
        )
        for route in routed:
            try:
                DolRecords.patchDolEntry(*route, *previous[route])
            except Exception as undo_exc:
                _slogger.error(
                    f'Could not restore the route of chunk {route[0]}, file {route[1]}: {undo_exc}',
                    source='hammerspace.main',
                )
        try:
            RouteOps.zeroRangeIfUnrouted(new_offset, len(block))
        except OSError as zero_exc:
            _slogger.error(f'Could not free the new block at 0x{new_offset:08X}: {zero_exc}',
                           source='hammerspace.main')
        raise

    DatAllocation.patchFstFileSize(os.path.getsize(hh.OUTPUT_DAT))    # warns by itself when there is no fst.bin
    RouteOps.zeroOriginalModel(chunk_number, file_index)
    if replacing_hammerspace_block and current_offset != new_offset:
        RouteOps.zeroRangeIfUnrouted(current_offset, current_length)
        _slogger.info(
            f'Hammerspace Log: Replaced | Model: {model_name} | '
            f'Old address: 0x{current_offset:08X} | '
            f'Old size: {current_length / (1024 * 1024):.2f} MB',
            source='hammerspace.main',
        )
    _slogger.info(
        f'Hammerspace Log: Written | Model: {model_name} | Chunk: {chunk_number} | '
        f'File: {file_index} | Address: 0x{new_offset:08X} | '
        f'Size: {len(block) / (1024 * 1024):.2f} MB' + log_suffix,
        source='hammerspace.main',
    )
    return new_offset


def WriteSlotBlocks(character_id: str, high: bytes | None, low: bytes | None, high_as_low: bool = False) -> bool:
    """Write finished model blocks (a roster pack's, ``start.py --write-slot-blocks``) into a slot as they are: no
    ``.sluggie``, no rebuild. ``high`` / ``low``: the slot's file 0 / file 1, or None to keep the one it has.
    Refused before anything is written when a block fails ``BlockValidator`` or the pair breaks the slot rules
    (``SlotTarget.slot_pair_problems``: roles, the slot's skeleton, the Low model's partner and bones;
    ``high_as_low``: a second High model may be the Low model, for a character file import)."""
    try:
        cid, chunk_number = SlotTarget.resolve_dir(character_id)
    except (SlotTarget.TargetError, OSError) as exc:
        _slogger.error(f'Slot write refused | {exc}', source='hammerspace.main')
        return False
    where = f'0x{cid:02X} (chunk {chunk_number})'
    if chunk_number not in SlotTarget.character_dirs() and not DolRecords.isOwnDir(chunk_number):
        _slogger.error(f'Slot write refused | {where} is not a character directory', source='hammerspace.main')
        return False
    blocks = {SlotTarget.HIGH_FILE: high, SlotTarget.LOW_FILE: low}
    for file_index, block in blocks.items():
        if block is None:
            continue
        errors = SlotTarget.block_errors(block, LodPartnerGuard._vanilla_block(chunk_number, file_index))
        if errors:
            _slogger.error(f'Slot write refused | {where}, file {file_index}: the block fails validation: '
                           + '; '.join(errors[:3]), source='hammerspace.main')
            return False
    after = {f: b if b is not None else LodPartnerGuard.read_current_block(chunk_number, f) for f, b in blocks.items()}
    try:
        problems, warnings = SlotTarget.slot_pair_problems(after[SlotTarget.HIGH_FILE], after[SlotTarget.LOW_FILE],
                                                           SlotTarget.vanilla_summaries(chunk_number), high_as_low)
    except SlotTarget.TargetError as exc:
        problems, warnings = [str(exc)], []
    for warning in warnings:
        _slogger.warning(f'[Slot] {where}: {warning}', source='hammerspace.main')
    if problems:
        _slogger.error(f'Slot write refused | {where}: ' + '; '.join(problems), source='hammerspace.main')
        return False
    for file_index, block in blocks.items():
        if block is not None:
            _write_route(block, chunk_number, file_index, f'0x{cid:02X} file {file_index}',
                         log_suffix=f' | Slot: {where} | pack block')
    return True


def UnpatchTarget(target: 'SlotTarget.Target') -> bool:
    """Return a slot to its own baseline: the vanilla block of a stock slot
    (both files with ``as_low``). Refused when the restored model would break
    the slot's other model (positional LOD pairing). A split route (unused
    character) gets a verbatim copy of its vanilla block: its untangled
    texture bytes come from its own ``.sluggie``, which a slot unpatch does
    not have."""
    if DolRecords.isOwnDir(target.chunk_number):
        _slogger.error(
            f'Slot unpatch refused | Slot: {target.describe()} | a new ID\'s own model directory has no vanilla '
            'block of its own; clear the slot instead (start.py --clear-slot), which gives it a fresh copy of its '
            'template\'s files',
            source='hammerspace.main',
        )
        return False
    routes = [target.route] + ([target.low_route] if target.as_low else [])
    if not target.as_low and not target.equipment:
        errors = LodPartnerGuard.lod_partner_unpatch_errors(*target.route, any_stem=True)
        if errors:
            _slogger.error(
                f'Slot unpatch refused | Slot: {target.describe()} | restoring its original model '
                'would break its partner model: ' + ' '.join(errors),
                source='hammerspace.main',
            )
            return False
    success = True
    for chunk_number, file_index in routes:
        if UntanglePolicy.is_split(chunk_number, file_index):
            _slogger.info(
                f'[Untangle] slot ({chunk_number},{file_index}) gets a verbatim copy of its vanilla block',
                source='hammerspace.main',
            )
        ok, removed_offset, removed_length = RouteOps.removeModelFromHammerspace(chunk_number, file_index)
        if not ok:
            _slogger.error(
                f'Slot unpatch stopped | Slot: {target.describe()} | chunk {chunk_number}, file {file_index} '
                'could not be restored; the files after it were left as they are',
                source='hammerspace.main',
            )
            return False
        _slogger.info(
            f'Hammerspace Log: Removed | Slot: chunk {chunk_number}, file {file_index} | '
            f'Address: 0x{removed_offset:08X} | Size: {removed_length / (1024 * 1024):.2f} MB',
            source='hammerspace.main',
        )
    return success


def ClearTarget(character_id: str) -> bool:
    """Return a stock slot's both models (HP and ``L_``) to their vanilla blocks (``start.py --clear-slot``).

    Both files are restored together, so the pair stays consistent and no LOD
    check is needed. A route that is already the vanilla one only gets its
    bytes repaired when they differ (an in-place patch). A new ID's own model
    directory is refused: the roster rebuild resets it."""
    try:
        cid, chunk_number = SlotTarget.resolve_dir(character_id)
    except (SlotTarget.TargetError, OSError) as exc:
        _slogger.error(f'Slot clear refused | {exc}', source='hammerspace.main')
        return False
    target = SlotTarget.Target(chunk_number, SlotTarget.HIGH_FILE, cid, as_low=True)
    if DolRecords.isOwnDir(chunk_number) or chunk_number not in SlotTarget.character_dirs():
        _slogger.error(f'Slot clear refused | Slot: {target.describe()} | not a stock character directory',
                       source='hammerspace.main')
        return False
    success = True
    for file_index in SlotTarget.CHARACTER_FILES:
        current = DolRecords.readOutputDolEntry(chunk_number, file_index)
        vanilla = DolRecords.readDolEntry(chunk_number, file_index)
        if current == vanilla and not UntanglePolicy.is_split(chunk_number, file_index):
            block = LodPartnerGuard.read_current_block(chunk_number, file_index)
            if block == LodPartnerGuard._vanilla_block(chunk_number, file_index):
                _slogger.info(f'Slot clear | chunk {chunk_number}, file {file_index} is already vanilla',
                              source='hammerspace.main')
                continue
        ok, removed_offset, removed_length = RouteOps.removeModelFromHammerspace(chunk_number, file_index)
        success = success and ok
        if not ok:
            _slogger.error(
                f'Slot clear stopped | Slot: {target.describe()} | file {file_index} could not be restored; '
                'the files after it were left as they are',
                source='hammerspace.main',
            )
            break
        if ok:
            _slogger.info(
                f'Hammerspace Log: Removed | Slot: 0x{cid:02X} (chunk {chunk_number}, file {file_index}) | '
                f'Address: 0x{removed_offset:08X} | Size: {removed_length / (1024 * 1024):.2f} MB',
                source='hammerspace.main',
            )
    return success


def ClearEquipment(character_id: str, file_index: int) -> bool:
    """Return one equipment file (2 bat, 3 left glove, 4 right glove, 5 extra bat) or animation bank (6-14) of a
    slot to its baseline (``start.py --unpatch --target-id 0xNN --target-file N``): the vanilla route and bytes of a
    stock slot (the shared block other characters use is only restored, never touched for them), a fresh copy of
    the source's vanilla block for a new ID's own directory. A file that is at its baseline already is left
    alone."""
    try:
        cid, chunk_number = SlotTarget.resolve_dir(character_id)
    except (SlotTarget.TargetError, OSError) as exc:
        _slogger.error(f'Equipment reset refused | {exc}', source='hammerspace.main')
        return False
    if file_index not in SlotTarget.EQUIPMENT_FILES + SlotTarget.ANIMATION_FILES:
        _slogger.error(f'Equipment reset refused | file {file_index} is neither an equipment file (2-5) nor an '
                       'animation bank (6-14)', source='hammerspace.main')
        return False
    where = f'0x{cid:02X} (chunk {chunk_number}, file {file_index})'
    if not DolRecords.isOwnDir(chunk_number) and chunk_number not in SlotTarget.character_dirs():
        _slogger.error(f'Equipment reset refused | {where} is not a character directory', source='hammerspace.main')
        return False
    vanilla = LodPartnerGuard._vanilla_block(chunk_number, file_index)
    if vanilla is None:
        _slogger.error(f'Equipment reset refused | {where} has no vanilla block to return to',
                       source='hammerspace.main')
        return False
    current = LodPartnerGuard.read_current_block(chunk_number, file_index)
    if DolRecords.isOwnDir(chunk_number):
        if current == vanilla:
            _slogger.info(f'Equipment reset | {where} is already at its baseline', source='hammerspace.main')
            return True
        _write_route(vanilla, chunk_number, file_index, f'0x{cid:02X} file {file_index} (baseline)',
                     log_suffix=f' | Slot: {where} | baseline copy', share_route=False)
        return True
    if (DolRecords.readOutputDolEntry(chunk_number, file_index) == DolRecords.readDolEntry(chunk_number, file_index)
            and not UntanglePolicy.is_split(chunk_number, file_index)):
        # Still on its own vanilla route: nothing was written for this slot. The bytes are left alone (an untangle
        # export changes texture bytes in place; restoring the input's would undo that).
        _slogger.info(f'Equipment reset | {where} is on its vanilla route already', source='hammerspace.main')
        return True
    original = DolRecords.readDolEntry(chunk_number, file_index)
    if not UntanglePolicy.is_split(chunk_number, file_index) and DolRecords.liveRoutesInto(*original):
        # Other characters still load the vanilla bytes from their own routes: point this route back at them
        # without rewriting them (removeModelFromHammerspace would restore the input's bytes over theirs).
        old_offset, old_length = DolRecords.readOutputDolEntry(chunk_number, file_index)
        DolRecords.patchDolEntry(chunk_number, file_index, *original)
        if old_offset >= hh.BASE_SIZE:
            RouteOps.zeroRangeIfUnrouted(old_offset, old_length)
        _slogger.info(
            f'Hammerspace Log: Removed | Slot: {where} | Address: 0x{old_offset:08X} | '
            f'Size: {old_length / (1024 * 1024):.2f} MB | route back on the shared vanilla block',
            source='hammerspace.main',
        )
        return True
    ok, removed_offset, removed_length = RouteOps.removeModelFromHammerspace(chunk_number, file_index)
    if ok:
        _slogger.info(
            f'Hammerspace Log: Removed | Slot: {where} | Address: 0x{removed_offset:08X} | '
            f'Size: {removed_length / (1024 * 1024):.2f} MB',
            source='hammerspace.main',
        )
    return ok


def WriteSlotEquipment(character_id: str, file_index: int, block: bytes) -> bool:
    """Write a finished equipment block (a roster pack's, ``start.py --write-slot-equipment``) into a slot as it is.
    Refused before anything is written when the model inside fails ``BlockValidator`` or breaks the skeleton rule
    (``SlotTarget.equipment_block_problems``). Only the slot's own route changes."""
    try:
        cid, chunk_number = SlotTarget.resolve_dir(character_id)
    except (SlotTarget.TargetError, OSError) as exc:
        _slogger.error(f'Slot write refused | {exc}', source='hammerspace.main')
        return False
    where = f'0x{cid:02X} (chunk {chunk_number}, file {file_index})'
    if chunk_number not in SlotTarget.character_dirs() and not DolRecords.isOwnDir(chunk_number):
        _slogger.error(f'Slot write refused | {where} is not a character directory', source='hammerspace.main')
        return False
    errors, warnings = SlotTarget.equipment_block_problems(block, chunk_number, file_index)
    for warning in warnings:
        _slogger.warning(f'[Slot] {where}: {warning}', source='hammerspace.main')
    if errors:
        _slogger.error(f'Slot write refused | {where}: ' + '; '.join(errors[:3]), source='hammerspace.main')
        return False
    _write_route(block, chunk_number, file_index, f'0x{cid:02X} file {file_index}',
                 log_suffix=f' | Slot: {where} | pack block', share_route=False)
    return True


def WriteSlotAnimation(character_id: str, file_index: int, bank: bytes) -> bool:
    """Write an animation bank (an ``.anm`` file, ``start.py --write-slot-animation``) into file ``file_index``
    (6-14) of a slot as it is. Refused before anything is written when the bank does not fit the slot
    (``Roster/anim.problems``: its sequence count, and track ids the slot's skeleton has no bone for). Only the
    slot's own route changes; a bank equal to the slot's vanilla one returns the route to its baseline instead
    (``ClearEquipment``)."""
    try:
        cid, chunk_number = SlotTarget.resolve_dir(character_id)
    except (SlotTarget.TargetError, OSError) as exc:
        _slogger.error(f'Slot write refused | {exc}', source='hammerspace.main')
        return False
    where = f'0x{cid:02X} (chunk {chunk_number}, file {file_index})'
    if chunk_number not in SlotTarget.character_dirs() and not DolRecords.isOwnDir(chunk_number):
        _slogger.error(f'Slot write refused | {where} is not a character directory', source='hammerspace.main')
        return False
    if file_index not in SlotTarget.ANIMATION_FILES:
        _slogger.error(f'Slot write refused | file {file_index} is not an animation bank (6-14)',
                       source='hammerspace.main')
        return False
    from Roster import anim
    vanilla = LodPartnerGuard._vanilla_block(chunk_number, file_index)
    try:
        if vanilla is None:
            raise anim.AnimError('the slot has no vanilla animation bank to compare with')
        new, stock = anim.read_bank(bank), anim.read_bank(vanilla)
    except anim.AnimError as exc:
        _slogger.error(f'Slot write refused | {where}: {exc}', source='hammerspace.main')
        return False
    skeleton = anim.skeleton_tracks(LodPartnerGuard._vanilla_block(chunk_number, SlotTarget.HIGH_FILE)) or frozenset()
    skeleton |= anim.skeleton_tracks(LodPartnerGuard.read_current_block(chunk_number, SlotTarget.HIGH_FILE)) or frozenset()
    errors, warnings = anim.problems(new, file_index, stock, skeleton)
    for warning in warnings:
        _slogger.warning(f'[Slot] {where}: {warning}', source='hammerspace.main')
    if errors:
        _slogger.error(f'Slot write refused | {where}: ' + '; '.join(errors), source='hammerspace.main')
        return False
    if bank == vanilla:
        _slogger.info(f'[Slot] {where}: the bank is the vanilla one: back to the baseline', source='hammerspace.main')
        return ClearEquipment(character_id, file_index)
    _write_route(bank, chunk_number, file_index, f'0x{cid:02X} file {file_index}',
                 log_suffix=f' | Slot: {where} | animation bank', share_route=False)
    return True
