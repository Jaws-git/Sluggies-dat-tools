import argparse
import hashlib
import json
import os
import shutil
import struct
import tempfile
from dataclasses import dataclass

try:
    from . import install_runtime_hooks as hooks
    from . import patch_color_wheel as color_wheel
except ImportError:
    import install_runtime_hooks as hooks
    import patch_color_wheel as color_wheel


cib = hooks.resources.sources.pages.cib
pages = hooks.resources.sources.pages
artwork = hooks.resources.artwork
sources = hooks.resources.sources
resources = hooks.resources
REPORT_PATH = os.path.join(
    cib.ROOT,
    '2_Output_Models',
    '_ICONS',
    'metadata',
    'add_custom_icons_report.json',
)
DIAGNOSTIC_STAGES = {
    'a': 'plain zero-padded stock bank, directory entry, and FST size',
    'b': 'stage a plus private-page descriptors and relocated icon table',
    'c': 'stage b plus private CMPR artwork',
    'd': 'stage c plus relocated stock source tables',
    'e': 'stage d plus unreferenced expanded resource rows',
    'f': 'stage e plus custom source records (activates private pages)',
    'g': 'stage f plus color-wheel rows',
    'h': 'stage g plus inactive hook stubs, state, and custom rows',
    'i': 'stage h plus lower lookup hook',
    'j': 'stage i plus key hook',
    'k': 'stage j plus final row hook (complete patch)',
}
LEGACY_FOUR_BANK_LENGTH = 0x118C90
LEGACY_FOUR_COUNT = 4
LEGACY_FRONT_TABLE_OFFSET = 0x8A310
LEGACY_RESOURCE_COUNT = sources.STOCK_RESOURCE_COUNT + LEGACY_FOUR_COUNT * 2
LEGACY_ICON_TABLE_END = 0x5008


class CustomIconInstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class CustomIconPlan:
    routes: list[sources.CharacterRoute]
    entries: list[artwork.ArtworkEntry]
    fit_mode: str
    color_entries: list[color_wheel.ColorWheelEntry]
    bank: bytes
    dol: bytes
    dat_source: str
    destination_offset: int
    projected_dat_size: int
    side_sha256: str
    front_sha256: str
    changed_dol_regions: int
    existing_expansion: bool
    already_configured: bool
    fst_available: bool


@dataclass(frozen=True)
class CustomIconResult:
    character_count: int
    destination_offset: int
    bank_length: int
    output_dat_size: int
    side_sha256: str
    front_sha256: str
    changed_dol_regions: int
    already_configured: bool
    dry_run: bool
    report_written: bool
    fst_available: bool
    fst_updated: bool


def _read_icon_entry_bytes(dol: bytes) -> cib.IconEntry:
    start = cib.ICON_ENTRY_DOL_OFFSET
    raw = dol[start:start + cib.ICON_ENTRY_SIZE]
    if len(raw) != cib.ICON_ENTRY_SIZE:
        raise CustomIconInstallError('DOL is truncated before the icon directory entry')
    words = struct.unpack('>12I', raw)
    lengths = (words[1], words[5], words[9])
    offsets = (words[2], words[6], words[10])
    allocations = (words[3], words[7], words[11])
    if len(set(lengths)) != 1 or len(set(offsets)) != 1 or len(set(allocations)) != 1:
        raise CustomIconInstallError('icon directory language slots disagree')
    return cib.IconEntry(
        (words[0], words[4], words[8]),
        offsets[0],
        lengths[0],
        allocations[0],
    )


def _patch_icon_entry_bytes(dol: bytes, destination: int) -> bytes:
    if destination % cib.ALIGNMENT:
        raise CustomIconInstallError(
            f'icon destination 0x{destination:X} is not {cib.ALIGNMENT}-byte aligned'
        )
    entry = _read_icon_entry_bytes(dol)
    words = []
    for filename_pointer in entry.filename_pointers:
        words.extend((
            filename_pointer,
            cib.EXPANDED_BANK_LENGTH,
            destination,
            cib.EXPANDED_BANK_LENGTH,
        ))
    updated = bytearray(dol)
    struct.pack_into('>12I', updated, cib.ICON_ENTRY_DOL_OFFSET, *words)
    return bytes(updated)


def _strip_legacy_source_records(
    bank: bytes,
    offset: int,
    custom_ids: set[int],
    expected_count: int,
) -> bytes:
    _, count, stride = sources._source_table_info(bank, offset)
    if count != expected_count + len(custom_ids):
        raise CustomIconInstallError(
            f'legacy source table at 0x{offset:X} has count {count}; '
            f'expected {expected_count + len(custom_ids)}'
        )
    records = []
    for index in range(count):
        start = offset + sources.SOURCE_HEADER_SIZE + index * stride
        record = bytearray(bank[start:start + stride])
        if struct.unpack_from('>H', record, 0x02)[0] not in custom_ids:
            records.append(record)
    if len(records) != expected_count:
        raise CustomIconInstallError(
            'legacy source table does not contain the expected custom IDs'
        )
    for index, record in enumerate(records):
        flags = struct.unpack_from('>H', record, 0x00)[0]
        flags = (
            flags & ~sources.SOURCE_FIRST_RECORD_FLAG
            if index == 0
            else flags | sources.SOURCE_FIRST_RECORD_FLAG
        )
        struct.pack_into('>H', record, 0x00, flags)
    table = bytearray(bank[offset:offset + sources.SOURCE_HEADER_SIZE])
    table.extend(b''.join(records))
    struct.pack_into('>I', table, 0x08, len(table))
    struct.pack_into('>H', table, 0x24, len(records))
    return bytes(table)


def _upgrade_legacy_four_bank(
    bank: bytes,
    routes: list[sources.CharacterRoute],
) -> bytes:
    if len(bank) != LEGACY_FOUR_BANK_LENGTH:
        raise CustomIconInstallError('legacy icon bank has the wrong length')
    if len(routes) != sources.INITIAL_CHARACTER_COUNT:
        raise CustomIconInstallError('six-character routes are required for legacy migration')
    if struct.unpack_from('>I', bank, 0x04)[0] != pages.RELOCATED_ICON_TABLE:
        raise CustomIconInstallError('legacy icon table is not relocated')
    if struct.unpack_from('>H', bank, 0x20)[0] != pages.PRIVATE_TEXTURE_COUNT:
        raise CustomIconInstallError('legacy private texture pages are not configured')

    legacy_ids = {route.char_id for route in routes[:LEGACY_FOUR_COUNT]}
    normal_length, normal_count, _ = sources._source_table_info(
        bank, sources.NORMAL_A_OFFSET
    )
    if normal_count != sources.STOCK_NORMAL_A_COUNT:
        raise CustomIconInstallError('legacy normal_a source count is invalid')
    normal = bank[sources.NORMAL_A_OFFSET:sources.NORMAL_A_OFFSET + normal_length]
    legacy_side_length = sources._source_table_info(
        bank, sources.SIDE_TABLE_OFFSET
    )[0]
    legacy_front_length = sources._source_table_info(
        bank, LEGACY_FRONT_TABLE_OFFSET
    )[0]
    side = _strip_legacy_source_records(
        bank, sources.SIDE_TABLE_OFFSET, legacy_ids, sources.STOCK_SIDE_COUNT
    )
    front = _strip_legacy_source_records(
        bank, LEGACY_FRONT_TABLE_OFFSET, legacy_ids, sources.STOCK_FRONT_COUNT
    )
    resource_count, _ = resources._resource_table_info(
        bank, sources.RESOURCE_TABLE_OFFSET
    )
    if resource_count != LEGACY_RESOURCE_COUNT:
        raise CustomIconInstallError(
            f'legacy resource count is 0x{resource_count:X}; '
            f'expected 0x{LEGACY_RESOURCE_COUNT:X}'
        )
    icon_table_end = struct.unpack_from(
        '>I', bank, pages.RELOCATED_ICON_TABLE + resources.ICON_TABLE_END_FIELD
    )[0]
    if icon_table_end != LEGACY_ICON_TABLE_END:
        raise CustomIconInstallError(
            f'legacy icon-table end is 0x{icon_table_end:X}; '
            f'expected 0x{LEGACY_ICON_TABLE_END:X}'
        )

    updated = bytearray(bank)
    updated.extend(bytes(cib.EXPANDED_BANK_LENGTH - len(updated)))
    updated[
        sources.SIDE_TABLE_OFFSET:
        sources.SIDE_TABLE_OFFSET + legacy_side_length
    ] = bytes(legacy_side_length)
    updated[
        LEGACY_FRONT_TABLE_OFFSET:
        LEGACY_FRONT_TABLE_OFFSET + legacy_front_length
    ] = bytes(legacy_front_length)
    updated[
        sources.FRONT_TABLE_OFFSET:
        sources.FRONT_TABLE_OFFSET + len(front)
    ] = bytes(
        len(front)
    )
    updated[sources.NORMAL_A_OFFSET:sources.NORMAL_A_OFFSET + len(normal)] = normal
    updated[sources.SIDE_TABLE_OFFSET:sources.SIDE_TABLE_OFFSET + len(side)] = side
    updated[sources.FRONT_TABLE_OFFSET:sources.FRONT_TABLE_OFFSET + len(front)] = front
    sources._write_signed_pointer(updated, sources.NORMAL_A_POINTER_FIELD, sources.NORMAL_A_OFFSET)
    sources._write_signed_pointer(updated, sources.SIDE_POINTER_FIELD, sources.SIDE_TABLE_OFFSET)
    sources._write_signed_pointer(updated, sources.FRONT_POINTER_FIELD, sources.FRONT_TABLE_OFFSET)

    stock_resources = bytearray(
        bank[
            sources.RESOURCE_TABLE_OFFSET:
            sources.RESOURCE_TABLE_OFFSET + resources.STOCK_RESOURCE_TABLE_LENGTH
        ]
    )
    struct.pack_into(
        '>II', stock_resources, 0,
        sources.STOCK_RESOURCE_COUNT,
        resources.STOCK_RESOURCE_TABLE_LENGTH,
    )
    updated[sources.RESOURCE_TABLE_OFFSET:] = bytes(
        len(updated) - sources.RESOURCE_TABLE_OFFSET
    )
    updated[
        sources.RESOURCE_TABLE_OFFSET:
        sources.RESOURCE_TABLE_OFFSET + len(stock_resources)
    ] = stock_resources
    updated[-8:] = bank[-8:]
    struct.pack_into(
        '>I', updated,
        pages.RELOCATED_ICON_TABLE + resources.ICON_TABLE_END_FIELD,
        resources.STOCK_ICON_TABLE_END,
    )
    sources._write_signed_pointer(
        updated, sources.RESOURCE_POINTER_FIELD, sources.RESOURCE_TABLE_OFFSET
    )
    sources.validate_relocated_source_tables(bytes(updated))
    return sources.append_custom_source_records(bytes(updated), routes)


def _remove_legacy_four_hooks(
    dol: bytes,
    stock_dol: bytes,
    routes: list[sources.CharacterRoute],
) -> bytes:
    row_length = LEGACY_FOUR_COUNT * hooks.CUSTOM_ROW_STRIDE
    row_offset = hooks._vaddr_to_file(dol, hooks.CUSTOM_ROWS_ADDRESS, row_length)
    legacy_rows = dol[row_offset:row_offset + row_length]
    legacy_patches = hooks.build_hook_patches(
        stock_dol, routes[:LEGACY_FOUR_COUNT], legacy_rows
    )
    updated = bytearray(dol)
    for patch in legacy_patches:
        offset = hooks._vaddr_to_file(dol, patch.address, len(patch.patched))
        current = dol[offset:offset + len(patch.patched)]
        if current == patch.stock:
            continue
        if current != patch.patched:
            raise CustomIconInstallError(
                f'legacy {patch.name} bytes do not match the generated four-character patch'
            )
        updated[offset:offset + len(patch.stock)] = patch.stock
    return bytes(updated)


def build_final_bank(
    bank: bytes,
    routes: list[sources.CharacterRoute],
    entries: list[artwork.ArtworkEntry],
    side_payload: bytes,
    front_payload: bytes,
) -> bytes:
    icon_table = struct.unpack_from('>I', bank, 0x04)[0]
    if icon_table == cib.STOCK_ICON_TABLE:
        bank = pages.add_private_texture_pages(bank)
    elif pages._is_configured(bank):
        pages.validate_private_texture_pages(bank, require_blank=False)
    else:
        raise CustomIconInstallError(
            f'unsupported expanded-bank icon table pointer 0x{icon_table:X}'
        )

    bank = artwork.apply_cmpr_payloads(bank, side_payload, front_payload)
    if not sources._is_updated(bank):
        bank = sources.update_icon_source_tables(bank, routes)
    if resources._is_updated(bank):
        resources.validate_icon_resource_rows(bank, routes, entries)
    else:
        bank = resources.add_icon_resource_rows(bank, routes, entries)
    resources.validate_icon_resource_rows(bank, routes, entries)
    return bank


def build_diagnostic_bank(
    bank: bytes,
    routes: list[sources.CharacterRoute],
    entries: list[artwork.ArtworkEntry],
    side_payload: bytes,
    front_payload: bytes,
    stage: str,
) -> bytes:
    if stage not in DIAGNOSTIC_STAGES:
        raise CustomIconInstallError(f'unknown diagnostic stage: {stage!r}')
    if stage == 'a':
        return bank

    bank = pages.add_private_texture_pages(bank)
    if stage == 'b':
        return bank

    bank = artwork.apply_cmpr_payloads(bank, side_payload, front_payload)
    if stage == 'c':
        return bank

    bank = sources.relocate_icon_source_tables(bank)
    if stage == 'd':
        return bank

    bank = resources.add_icon_resource_rows(bank, routes, entries)
    if stage == 'e':
        return bank

    bank = sources.append_custom_source_records(bank, routes)
    resources.validate_icon_resource_rows(bank, routes, entries)
    return bank


def patch_final_dol(
    dol: bytes,
    stock_dol: bytes,
    destination: int,
    routes: list[sources.CharacterRoute],
    color_entries: list[color_wheel.ColorWheelEntry],
    custom_rows: bytes,
) -> tuple[bytes, int, list[hooks.HookPatch]]:
    current_entry = _read_icon_entry_bytes(dol)
    directory_changed = int(
        current_entry.offset != destination
        or current_entry.length != cib.EXPANDED_BANK_LENGTH
        or current_entry.allocation != cib.EXPANDED_BANK_LENGTH
    )
    dol = _patch_icon_entry_bytes(dol, destination)
    dol, color_changed = color_wheel.patch_color_wheel(dol, stock_dol, color_entries)
    dol, hook_regions_changed, hook_patches = hooks.patch_runtime_hooks(
        dol, stock_dol, routes, custom_rows
    )
    color_wheel.validate_color_wheel(dol, color_entries)
    hooks.validate_runtime_hooks(dol, hook_patches)
    entry = _read_icon_entry_bytes(dol)
    if (
        entry.offset != destination
        or entry.length != cib.EXPANDED_BANK_LENGTH
        or entry.allocation != cib.EXPANDED_BANK_LENGTH
    ):
        raise CustomIconInstallError('final DOL icon directory entry is invalid')
    changed_regions = directory_changed + color_changed + hook_regions_changed
    return dol, changed_regions, hook_patches


def _apply_hook_patch_subset(
    dol: bytes,
    stock_dol: bytes,
    patches: list[hooks.HookPatch],
    enabled_names: set[str],
) -> tuple[bytes, int]:
    updated = bytearray(dol)
    changed_count = 0
    for patch in patches:
        if patch.name not in enabled_names:
            continue
        current_offset = hooks._vaddr_to_file(dol, patch.address, len(patch.patched))
        stock_offset = hooks._vaddr_to_file(stock_dol, patch.address, len(patch.stock))
        current = dol[current_offset:current_offset + len(patch.patched)]
        stock = stock_dol[stock_offset:stock_offset + len(patch.stock)]
        if stock != patch.stock:
            raise hooks.RuntimeHookError(f'stock bytes changed for {patch.name}')
        if current == patch.patched:
            continue
        if current != patch.stock:
            raise hooks.RuntimeHookError(
                f'unexpected bytes in {patch.name} at 0x{patch.address:08X}'
            )
        updated[current_offset:current_offset + len(patch.patched)] = patch.patched
        changed_count += 1
    return bytes(updated), changed_count


def patch_diagnostic_dol(
    stock_dol: bytes,
    destination: int,
    routes: list[sources.CharacterRoute],
    color_entries: list[color_wheel.ColorWheelEntry],
    custom_rows: bytes,
    stage: str,
) -> tuple[bytes, int, list[hooks.HookPatch]]:
    if stage not in DIAGNOSTIC_STAGES:
        raise CustomIconInstallError(f'unknown diagnostic stage: {stage!r}')

    dol = _patch_icon_entry_bytes(stock_dol, destination)
    changed_regions = 1
    if stage >= 'g':
        dol, color_changed = color_wheel.patch_color_wheel(
            dol, stock_dol, color_entries
        )
        changed_regions += color_changed

    hook_patches = hooks.build_hook_patches(stock_dol, routes, custom_rows)
    enabled_names = set()
    if stage >= 'h':
        enabled_names.update({
            'lower stub',
            'state and custom rows',
            'key stub',
            'row stub',
        })
    if stage >= 'i':
        enabled_names.add('lower hook')
    if stage >= 'j':
        enabled_names.add('key hook')
    if stage >= 'k':
        enabled_names.add('row hook')
    dol, hook_changed = _apply_hook_patch_subset(
        dol, stock_dol, hook_patches, enabled_names
    )
    changed_regions += hook_changed
    return dol, changed_regions, hook_patches


def _validate_description_sets(
    routes: list[sources.CharacterRoute],
    entries: list[artwork.ArtworkEntry],
    color_entries: list[color_wheel.ColorWheelEntry],
) -> None:
    route_ids = {route.char_id for route in routes}
    if route_ids != {entry.char_id for entry in entries}:
        raise CustomIconInstallError('route and artwork character IDs differ')
    if route_ids != {entry.char_id for entry in color_entries}:
        raise CustomIconInstallError('route and color-wheel character IDs differ')


def _read_bank(path: str, entry: cib.IconEntry) -> bytes:
    try:
        with open(path, 'rb') as dat_file:
            dat_file.seek(entry.offset)
            bank = dat_file.read(entry.length)
    except OSError as exc:
        raise CustomIconInstallError(f'could not read DAT {path}: {exc}') from exc
    if len(bank) != entry.length:
        raise CustomIconInstallError('current icon bank is truncated')
    return bank


def _fst_source() -> str | None:
    if os.path.exists(cib.hh._FST_OUTPUT):
        return cib.hh._FST_OUTPUT
    if os.path.exists(cib.hh._FST_INPUT):
        return cib.hh._FST_INPUT
    return None


def plan_custom_icons(
    diagnostic_stage: str | None = None,
    fit_mode: str = artwork.DEFAULT_FIT_MODE,
) -> CustomIconPlan:
    if diagnostic_stage is not None and diagnostic_stage not in DIAGNOSTIC_STAGES:
        raise CustomIconInstallError(
            f'diagnostic stage must be one of: {", ".join(DIAGNOSTIC_STAGES)}'
        )
    routes = sources.load_character_routes()
    entries = artwork.load_artwork_entries()
    color_entries = color_wheel.load_color_wheel_entries()
    _validate_description_sets(routes, entries, color_entries)

    side_atlas, front_atlas = artwork.compose_atlases(entries, fit_mode)
    with tempfile.TemporaryDirectory(prefix='sluggies_custom_icons_') as work_dir:
        side_payload = artwork.encode_atlas_cmpr(side_atlas, work_dir, 'custom_side')
        front_payload = artwork.encode_atlas_cmpr(front_atlas, work_dir, 'custom_front')

    try:
        with open(cib.INPUT_DOL, 'rb') as stock_dol_file:
            stock_dol = stock_dol_file.read()
    except OSError as exc:
        raise CustomIconInstallError(f'could not read stock DOL: {exc}') from exc
    stock_entry = _read_icon_entry_bytes(stock_dol)
    cib.validate_stock_bank(cib.INPUT_DAT, stock_entry)

    output_dol_exists = diagnostic_stage is None and os.path.exists(cib.OUTPUT_DOL)
    output_dat_exists = diagnostic_stage is None and os.path.exists(cib.OUTPUT_DAT)
    if output_dol_exists != output_dat_exists:
        raise CustomIconInstallError(
            '3_Output_Dat must contain both main.dol and dt_na.dat, or neither'
        )
    dat_source = cib.OUTPUT_DAT if output_dat_exists else cib.INPUT_DAT
    if output_dol_exists:
        try:
            with open(cib.OUTPUT_DOL, 'rb') as output_dol_file:
                current_dol = output_dol_file.read()
        except OSError as exc:
            raise CustomIconInstallError(f'could not read output DOL: {exc}') from exc
    else:
        current_dol = stock_dol

    current_entry = _read_icon_entry_bytes(current_dol)
    existing_expansion = current_entry.length == cib.EXPANDED_BANK_LENGTH
    legacy_expansion = current_entry.length == LEGACY_FOUR_BANK_LENGTH
    if existing_expansion:
        if current_entry.allocation != cib.EXPANDED_BANK_LENGTH:
            raise CustomIconInstallError('expanded icon allocation has the wrong length')
        if current_entry.offset % cib.ALIGNMENT:
            raise CustomIconInstallError('expanded icon bank is unaligned')
        bank = _read_bank(dat_source, current_entry)
        destination = current_entry.offset
    elif legacy_expansion:
        if current_entry.allocation != LEGACY_FOUR_BANK_LENGTH:
            raise CustomIconInstallError('legacy icon allocation has the wrong length')
        if current_entry.offset % cib.ALIGNMENT:
            raise CustomIconInstallError('legacy icon bank is unaligned')
        bank = _upgrade_legacy_four_bank(
            _read_bank(dat_source, current_entry), routes
        )
        current_dol = _remove_legacy_four_hooks(current_dol, stock_dol, routes)
        destination = current_entry.offset
    elif (
        current_entry.offset == cib.STOCK_BANK_OFFSET
        and current_entry.length == cib.STOCK_BANK_LENGTH
        and current_entry.allocation == cib.STOCK_BANK_LENGTH
    ):
        bank = cib.build_expanded_clone(dat_source, current_entry)
        destination = (
            cib._choose_destination(cib.OUTPUT_DAT)
            if output_dat_exists
            else cib._align_up(os.path.getsize(cib.INPUT_DAT))
        )
    else:
        raise CustomIconInstallError(
            f'unsupported current icon entry 0x{current_entry.offset:X}/0x{current_entry.length:X}'
        )

    if diagnostic_stage is None:
        final_bank = build_final_bank(
            bank, routes, entries, side_payload, front_payload
        )
    else:
        final_bank = build_diagnostic_bank(
            bank,
            routes,
            entries,
            side_payload,
            front_payload,
            diagnostic_stage,
        )
    if diagnostic_stage is not None and diagnostic_stage < 'h':
        custom_rows = bytes(len(routes) * hooks.CUSTOM_ROW_STRIDE)
    else:
        custom_rows = hooks._build_custom_rows(final_bank, routes)
    if diagnostic_stage is None:
        final_dol, changed_regions, _ = patch_final_dol(
            current_dol, stock_dol, destination, routes, color_entries, custom_rows
        )
    else:
        final_dol, changed_regions, _ = patch_diagnostic_dol(
            stock_dol,
            destination,
            routes,
            color_entries,
            custom_rows,
            diagnostic_stage,
        )
    current_dat_size = os.path.getsize(dat_source)
    projected_dat_size = max(
        current_dat_size,
        destination + len(final_bank) + cib.hh.HS_BUFFER_BYTES,
    )
    current_bank_matches = existing_expansion and bank == final_bank
    already_configured = current_bank_matches and current_dol == final_dol
    return CustomIconPlan(
        routes,
        entries,
        fit_mode,
        color_entries,
        final_bank,
        final_dol,
        dat_source,
        destination,
        projected_dat_size,
        hashlib.sha256(side_payload).hexdigest(),
        hashlib.sha256(front_payload).hexdigest(),
        changed_regions,
        existing_expansion,
        already_configured,
        _fst_source() is not None,
    )


def _stage_dat(plan: CustomIconPlan, directory: str) -> str:
    descriptor, path = tempfile.mkstemp(prefix='custom_icons_', suffix='.dat.tmp', dir=directory)
    os.close(descriptor)
    try:
        shutil.copy2(plan.dat_source, path)
        with open(path, 'r+b') as dat_file:
            dat_file.truncate(plan.projected_dat_size)
            dat_file.seek(plan.destination_offset)
            dat_file.write(plan.bank)
        return path
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise


def _stage_dol(plan: CustomIconPlan, directory: str) -> str:
    descriptor, path = tempfile.mkstemp(prefix='custom_icons_', suffix='.dol.tmp', dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as dol_file:
            dol_file.write(plan.dol)
        return path
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise


def _write_fst(new_size: int, pristine: bool = False) -> bool:
    source = cib.hh._FST_INPUT if pristine and os.path.exists(cib.hh._FST_INPUT) else _fst_source()
    if source is None:
        return False
    try:
        if source != cib.hh._FST_OUTPUT:
            os.makedirs(os.path.dirname(cib.hh._FST_OUTPUT), exist_ok=True)
            shutil.copy2(source, cib.hh._FST_OUTPUT)
        with open(cib.hh._FST_OUTPUT, 'r+b') as fst_file:
            fst_file.seek(cib.hh._FST_DAT_SIZE_OFF)
            fst_file.write(struct.pack('>I', new_size))
        return True
    except OSError:
        return False


def _write_report(
    plan: CustomIconPlan,
    fst_updated: bool,
    diagnostic_stage: str | None = None,
) -> None:
    report = {
        'diagnostic_stage': diagnostic_stage,
        'diagnostic_description': (
            DIAGNOSTIC_STAGES[diagnostic_stage] if diagnostic_stage else None
        ),
        'character_count': len(plan.routes),
        'icon_fit': plan.fit_mode,
        'destination_offset': f'0x{plan.destination_offset:08X}',
        'bank_length': f'0x{len(plan.bank):X}',
        'output_dat_size': plan.projected_dat_size,
        'fst_updated': fst_updated,
        'payload_sha256': {
            'side': plan.side_sha256,
            'front': plan.front_sha256,
        },
        'tables': {
            'normal_a': f'0x{sources.NORMAL_A_OFFSET:X}',
            'side': f'0x{sources.SIDE_TABLE_OFFSET:X}',
            'front': f'0x{sources.FRONT_TABLE_OFFSET:X}',
            'resources': f'0x{resources.EXPANDED_RESOURCE_TABLE_OFFSET:X}',
        },
        'characters': [
            {
                'name': route.name,
                'char_id': f'0x{route.char_id:02X}',
                'donor_id': f'0x{route.donor_id:02X}',
                'side_resource': f'0x{route.side_resource:02X}',
                'front_resource': f'0x{route.front_resource:02X}',
                'side_dol_row': f'0x{hooks._custom_row_address(index, False):08X}',
                'front_dol_row': f'0x{hooks._custom_row_address(index, True):08X}',
            }
            for index, route in enumerate(plan.routes)
        ],
    }
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, 'w', encoding='utf-8') as report_file:
        json.dump(report, report_file, indent=2)
        report_file.write('\n')


def add_custom_icons(
    dry_run: bool = False,
    diagnostic_stage: str | None = None,
    fit_mode: str = artwork.DEFAULT_FIT_MODE,
) -> CustomIconResult:
    plan = plan_custom_icons(
        diagnostic_stage=diagnostic_stage,
        fit_mode=fit_mode,
    )
    if dry_run or plan.already_configured:
        return CustomIconResult(
            len(plan.routes), plan.destination_offset, len(plan.bank),
            plan.projected_dat_size, plan.side_sha256, plan.front_sha256,
            plan.changed_dol_regions, plan.already_configured, dry_run, False,
            plan.fst_available, False,
        )

    output_dir = os.path.dirname(cib.OUTPUT_DAT)
    os.makedirs(output_dir, exist_ok=True)
    staged_dat = _stage_dat(plan, output_dir)
    staged_dol = _stage_dol(plan, output_dir)
    try:
        os.replace(staged_dat, cib.OUTPUT_DAT)
        os.replace(staged_dol, cib.OUTPUT_DOL)
    finally:
        for staged in (staged_dat, staged_dol):
            if os.path.exists(staged):
                os.remove(staged)
    fst_updated = _write_fst(
        plan.projected_dat_size,
        pristine=diagnostic_stage is not None,
    )
    _write_report(plan, fst_updated, diagnostic_stage=diagnostic_stage)
    return CustomIconResult(
        len(plan.routes), plan.destination_offset, len(plan.bank),
        plan.projected_dat_size, plan.side_sha256, plan.front_sha256,
        plan.changed_dol_regions, False, False, True, plan.fst_available, fst_updated,
    )


def _print_result(result: CustomIconResult) -> None:
    action = 'Already configured' if result.already_configured else ('Dry run' if result.dry_run else 'Installed')
    print(f'{action}: custom icons for {result.character_count} characters')
    print(f'  bank:       0x{result.destination_offset:08X} + 0x{result.bank_length:X}')
    print(f'  output DAT: 0x{result.output_dat_size:X} bytes')
    print(f'  side CMPR:  {result.side_sha256}')
    print(f'  front CMPR: {result.front_sha256}')
    print(f'  DOL regions requiring changes: {result.changed_dol_regions}')
    if result.report_written:
        print(f'  report:     {os.path.relpath(REPORT_PATH, cib.ROOT)}')
    if result.dry_run and not result.fst_available:
        print('WARNING: fst.bin was not found; write mode cannot update the disc filesystem size.')
    elif not result.dry_run and not result.already_configured and not result.fst_updated:
        print('WARNING: the disc filesystem size was not updated in fst.bin.')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Install the complete six-character custom icon pipeline.'
    )
    parser.add_argument('--dry-run', action='store_true', help='validate everything without writing files')
    parser.add_argument(
        '--diagnostic-stage',
        choices=tuple(DIAGNOSTIC_STAGES),
        help='build one cumulative boot-test stage (a-k) from pristine inputs',
    )
    parser.add_argument(
        '--icon-fit',
        choices=artwork.FIT_MODES,
        default=artwork.DEFAULT_FIT_MODE,
        help='fit source images into 48x51 slots (default: contain)',
    )
    args = parser.parse_args()
    try:
        result = add_custom_icons(
            dry_run=args.dry_run,
            diagnostic_stage=args.diagnostic_stage,
            fit_mode=args.icon_fit,
        )
    except (
        CustomIconInstallError,
        hooks.RuntimeHookError,
        color_wheel.ColorWheelPatchError,
        resources.IconResourceRowError,
        artwork.IconArtworkError,
        sources.IconSourceTableError,
        pages.PrivateTexturePageError,
        cib.IconBankCloneError,
        OSError,
    ) as exc:
        parser.exit(1, f'ERROR: {exc}\n')
    _print_result(result)
    if args.diagnostic_stage:
        print(
            f'  diagnostic: stage {args.diagnostic_stage} - '
            f'{DIAGNOSTIC_STAGES[args.diagnostic_stage]}'
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())