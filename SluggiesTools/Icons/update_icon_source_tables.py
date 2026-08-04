import argparse
import json
import os
import shutil
import struct
from dataclasses import dataclass

_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
import sys as _sys
if _TOOLS_DIR not in _sys.path:
    _sys.path.insert(0, _TOOLS_DIR)
import slogger as _slogger
_slogger.configure()

try:
    from . import add_private_texture_pages as pages
except ImportError:
    import add_private_texture_pages as pages


DESCRIPTION_PATH = os.path.join(pages.cib.ICONS_DIR, 'icon_characters.json')
REPORT_PATH = os.path.join(
    pages.cib.ROOT,
    '2_Output_Models',
    '_ICONS',
    'metadata',
    'custom_icon_source_tables.json',
)
INITIAL_CHARACTER_COUNT = 6
ICON_DESCRIPTOR_OFFSET = pages.RELOCATED_ICON_TABLE + 0x14
RESOURCE_POINTER_FIELD = 0x04
NORMAL_A_POINTER_FIELD = 0x08
SIDE_POINTER_FIELD = 0x0C
FRONT_POINTER_FIELD = 0x10
NORMAL_A_OFFSET = 0x87520
SIDE_TABLE_OFFSET = 0x88B78
FRONT_TABLE_OFFSET = 0x8A3B0
SOURCE_HEADER_SIZE = 0x28
SOURCE_RECORD_SIZE = 0x50
SOURCE_FIRST_RECORD_FLAG = 0x0100
STOCK_NORMAL_A_COUNT = 71
STOCK_SIDE_COUNT = 71
STOCK_FRONT_COUNT = 72
RESOURCE_TABLE_OFFSET = 0x118000
STOCK_RESOURCE_COUNT = 0x98
SIDE_RESOURCE_BASE = STOCK_RESOURCE_COUNT
FRONT_RESOURCE_BASE = STOCK_RESOURCE_COUNT + INITIAL_CHARACTER_COUNT


class IconSourceTableError(RuntimeError):
    pass


@dataclass(frozen=True)
class CharacterRoute:
    name: str
    char_id: int
    donor_id: int
    donor_side_resource: int
    donor_front_resource: int
    side_resource: int
    front_resource: int


@dataclass(frozen=True)
class SourceTableResult:
    character_count: int
    destination_offset: int
    side_count: int
    front_count: int
    already_configured: bool
    dry_run: bool
    report_written: bool


def _parse_u16(value, field_name: str) -> int:
    try:
        parsed = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise IconSourceTableError(f'invalid {field_name}: {value!r}') from exc
    if not 0 <= parsed <= 0xFFFF:
        raise IconSourceTableError(f'{field_name} is outside u16 range: {parsed}')
    return parsed


def load_character_routes(description_path: str = DESCRIPTION_PATH) -> list[CharacterRoute]:
    try:
        with open(description_path, 'r', encoding='utf-8') as description_file:
            description = json.load(description_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise IconSourceTableError(f'could not read description file {description_path}: {exc}') from exc

    characters = description.get('characters')
    if not isinstance(characters, list) or len(characters) != INITIAL_CHARACTER_COUNT:
        raise IconSourceTableError(
            f'initial implementation requires exactly {INITIAL_CHARACTER_COUNT} characters'
        )

    routes = []
    seen_ids = set()
    seen_donor_ids = set()
    for index, character in enumerate(characters):
        if not isinstance(character, dict):
            raise IconSourceTableError(f'character {index} is not an object')
        name = character.get('name')
        if not isinstance(name, str) or not name:
            raise IconSourceTableError(f'character {index} has no valid name')
        char_id = _parse_u16(character.get('char_id'), f'{name}.char_id')
        donor_id = _parse_u16(character.get('donor_id'), f'{name}.donor_id')
        if char_id in seen_ids:
            raise IconSourceTableError(f'duplicate character ID 0x{char_id:02X}')
        if donor_id in seen_donor_ids:
            raise IconSourceTableError(f'duplicate donor ID 0x{donor_id:02X}')
        seen_ids.add(char_id)
        seen_donor_ids.add(donor_id)
        routes.append(
            CharacterRoute(
                name,
                char_id,
                donor_id,
                _parse_u16(
                    character.get('donor_side_resource'),
                    f'{name}.donor_side_resource',
                ),
                _parse_u16(
                    character.get('donor_front_resource'),
                    f'{name}.donor_front_resource',
                ),
                SIDE_RESOURCE_BASE + index,
                FRONT_RESOURCE_BASE + index,
            )
        )
    return routes


def _signed_pointer(bank: bytes, field_offset: int) -> int:
    relative = struct.unpack_from('>i', bank, ICON_DESCRIPTOR_OFFSET + field_offset)[0]
    return ICON_DESCRIPTOR_OFFSET + relative


def _write_signed_pointer(bank: bytearray, field_offset: int, target: int) -> None:
    relative = target - ICON_DESCRIPTOR_OFFSET
    struct.pack_into('>i', bank, ICON_DESCRIPTOR_OFFSET + field_offset, relative)


def _source_table_info(bank: bytes, offset: int) -> tuple[int, int, int]:
    if offset < 0 or offset + SOURCE_HEADER_SIZE > len(bank):
        raise IconSourceTableError(f'source table offset 0x{offset:X} is outside the bank')
    total_length = struct.unpack_from('>I', bank, offset + 0x08)[0]
    count, stride = struct.unpack_from('>HH', bank, offset + 0x24)
    expected_length = SOURCE_HEADER_SIZE + count * stride
    if stride != SOURCE_RECORD_SIZE or total_length != expected_length:
        raise IconSourceTableError(
            f'invalid source table at 0x{offset:X}: length=0x{total_length:X}, '
            f'count={count}, stride=0x{stride:X}'
        )
    if offset + total_length > len(bank):
        raise IconSourceTableError(f'source table at 0x{offset:X} is truncated')
    return total_length, count, stride


def _find_donor_record(table: bytes, donor_id: int, expected_resource: int) -> bytes:
    total_length, count, stride = _source_table_info(table, 0)
    matches = []
    for index in range(count):
        start = SOURCE_HEADER_SIZE + index * stride
        record = table[start:start + stride]
        if struct.unpack_from('>H', record, 0x02)[0] == donor_id:
            matches.append(record)
    if len(matches) != 1:
        raise IconSourceTableError(
            f'donor 0x{donor_id:02X} has {len(matches)} source records; expected exactly one'
        )
    actual_resource = struct.unpack_from('>H', matches[0], 0x06)[0]
    if actual_resource != expected_resource:
        raise IconSourceTableError(
            f'donor 0x{donor_id:02X} resource is 0x{actual_resource:X}; '
            f'description expects 0x{expected_resource:X}'
        )
    if total_length != len(table):
        raise IconSourceTableError('source table slice length does not match its header')
    return matches[0]


def _expand_source_table(
    table: bytes,
    routes: list[CharacterRoute],
    view: str,
) -> bytes:
    total_length, count, stride = _source_table_info(table, 0)
    existing_ids = {
        struct.unpack_from('>H', table, SOURCE_HEADER_SIZE + index * stride + 0x02)[0]
        for index in range(count)
    }
    records = []
    for route in routes:
        if route.char_id in existing_ids:
            raise IconSourceTableError(
                f'custom character 0x{route.char_id:02X} already exists in {view} table'
            )
        donor_resource = (
            route.donor_side_resource if view == 'side' else route.donor_front_resource
        )
        custom_resource = route.side_resource if view == 'side' else route.front_resource
        record = bytearray(_find_donor_record(table, route.donor_id, donor_resource))
        struct.pack_into('>HHH', record, 0x02, route.char_id, 0x0400, custom_resource)
        records.append(bytes(record))

    all_records = [
        bytearray(table[SOURCE_HEADER_SIZE + index * stride:SOURCE_HEADER_SIZE + (index + 1) * stride])
        for index in range(count)
    ]
    all_records.extend(bytearray(record) for record in records)
    all_records.sort(key=lambda record: struct.unpack_from('>H', record, 0x02)[0], reverse=True)
    for index, record in enumerate(all_records):
        flags = struct.unpack_from('>H', record, 0x00)[0]
        flags = (
            flags & ~SOURCE_FIRST_RECORD_FLAG
            if index == 0
            else flags | SOURCE_FIRST_RECORD_FLAG
        )
        struct.pack_into('>H', record, 0x00, flags)

    expanded = bytearray(table[:SOURCE_HEADER_SIZE])
    expanded.extend(b''.join(all_records))
    struct.pack_into('>I', expanded, 0x08, len(expanded))
    struct.pack_into('>H', expanded, 0x24, count + len(records))
    return bytes(expanded)


def _extract_source_tables(bank: bytes) -> tuple[bytes, bytes, bytes]:
    normal_offset = _signed_pointer(bank, NORMAL_A_POINTER_FIELD)
    side_offset = _signed_pointer(bank, SIDE_POINTER_FIELD)
    front_offset = _signed_pointer(bank, FRONT_POINTER_FIELD)
    tables = []
    for offset, expected_count in (
        (normal_offset, STOCK_NORMAL_A_COUNT),
        (side_offset, STOCK_SIDE_COUNT),
        (front_offset, STOCK_FRONT_COUNT),
    ):
        total_length, count, _ = _source_table_info(bank, offset)
        if count != expected_count:
            raise IconSourceTableError(
                f'stock source table at 0x{offset:X} has count {count}; expected {expected_count}'
            )
        tables.append(bank[offset:offset + total_length])
    return tables[0], tables[1], tables[2]


def relocate_icon_source_tables(bank: bytes) -> bytes:
    pages.validate_private_texture_pages(bank, require_blank=False)
    normal_a, side, front = _extract_source_tables(bank)

    if NORMAL_A_OFFSET + len(normal_a) != SIDE_TABLE_OFFSET:
        raise IconSourceTableError('normal_a does not end at the guide side-table offset')
    if SIDE_TABLE_OFFSET + len(side) > FRONT_TABLE_OFFSET:
        raise IconSourceTableError('stock side table overlaps the guide front-table offset')
    if FRONT_TABLE_OFFSET + len(front) > pages.SIDE_IMAGE_OFFSET:
        raise IconSourceTableError('stock front table overlaps the private side image')

    old_normal_offset = _signed_pointer(bank, NORMAL_A_POINTER_FIELD)
    old_resource_offset = _signed_pointer(bank, RESOURCE_POINTER_FIELD)
    if old_resource_offset != RESOURCE_TABLE_OFFSET:
        raise IconSourceTableError(
            f'resource table resolved to 0x{old_resource_offset:X}; expected 0x{RESOURCE_TABLE_OFFSET:X}'
        )
    resource_count = struct.unpack_from('>I', bank, RESOURCE_TABLE_OFFSET)[0]
    if resource_count != STOCK_RESOURCE_COUNT:
        raise IconSourceTableError(
            f'resource count is {resource_count}; expected {STOCK_RESOURCE_COUNT}'
        )

    updated = bytearray(bank)
    updated[old_normal_offset:old_resource_offset] = bytes(old_resource_offset - old_normal_offset)
    updated[NORMAL_A_OFFSET:NORMAL_A_OFFSET + len(normal_a)] = normal_a
    updated[SIDE_TABLE_OFFSET:SIDE_TABLE_OFFSET + len(side)] = side
    updated[FRONT_TABLE_OFFSET:FRONT_TABLE_OFFSET + len(front)] = front
    _write_signed_pointer(updated, NORMAL_A_POINTER_FIELD, NORMAL_A_OFFSET)
    _write_signed_pointer(updated, SIDE_POINTER_FIELD, SIDE_TABLE_OFFSET)
    _write_signed_pointer(updated, FRONT_POINTER_FIELD, FRONT_TABLE_OFFSET)

    validate_relocated_source_tables(bytes(updated))
    return bytes(updated)


def append_custom_source_records(
    bank: bytes,
    routes: list[CharacterRoute],
) -> bytes:
    resource_offset = _signed_pointer(bank, RESOURCE_POINTER_FIELD)
    resource_count = struct.unpack_from('>I', bank, resource_offset)[0]
    validate_relocated_source_tables(
        bank,
        resource_offset=resource_offset,
        resource_count=resource_count,
    )
    normal_length, _, _ = _source_table_info(bank, NORMAL_A_OFFSET)
    side_length, _, _ = _source_table_info(bank, SIDE_TABLE_OFFSET)
    front_length, _, _ = _source_table_info(bank, FRONT_TABLE_OFFSET)
    side = bank[SIDE_TABLE_OFFSET:SIDE_TABLE_OFFSET + side_length]
    front = bank[FRONT_TABLE_OFFSET:FRONT_TABLE_OFFSET + front_length]
    expanded_side = _expand_source_table(side, routes, 'side')
    expanded_front = _expand_source_table(front, routes, 'front')
    if SIDE_TABLE_OFFSET + len(expanded_side) != FRONT_TABLE_OFFSET:
        raise IconSourceTableError('expanded side table does not end at the guide front-table offset')
    if FRONT_TABLE_OFFSET + len(expanded_front) > pages.SIDE_IMAGE_OFFSET:
        raise IconSourceTableError('expanded front table overlaps the private side image')

    updated = bytearray(bank)
    updated[SIDE_TABLE_OFFSET:SIDE_TABLE_OFFSET + len(expanded_side)] = expanded_side
    updated[FRONT_TABLE_OFFSET:FRONT_TABLE_OFFSET + len(expanded_front)] = expanded_front
    validate_updated_source_tables(
        bytes(updated),
        routes,
        resource_offset=resource_offset,
        resource_count=resource_count,
    )
    return bytes(updated)


def update_icon_source_tables(bank: bytes, routes: list[CharacterRoute]) -> bytes:
    return append_custom_source_records(relocate_icon_source_tables(bank), routes)


def _record_for_character(table: bytes, char_id: int) -> bytes:
    _, count, stride = _source_table_info(table, 0)
    matches = []
    for index in range(count):
        start = SOURCE_HEADER_SIZE + index * stride
        record = table[start:start + stride]
        if struct.unpack_from('>H', record, 0x02)[0] == char_id:
            matches.append(record)
    if len(matches) != 1:
        raise IconSourceTableError(
            f'character 0x{char_id:02X} has {len(matches)} records; expected one'
        )
    return matches[0]


def _validate_record_order(table: bytes, view: str) -> None:
    _, count, stride = _source_table_info(table, 0)
    records = [
        table[SOURCE_HEADER_SIZE + index * stride:SOURCE_HEADER_SIZE + (index + 1) * stride]
        for index in range(count)
    ]
    character_ids = [struct.unpack_from('>H', record, 0x02)[0] for record in records]
    if character_ids != sorted(character_ids, reverse=True):
        raise IconSourceTableError(f'{view} source records are not in descending character-ID order')
    first_flags = struct.unpack_from('>H', records[0], 0x00)[0]
    if first_flags & SOURCE_FIRST_RECORD_FLAG:
        raise IconSourceTableError(f'{view} first source record has the continuation flag')
    if any(
        not struct.unpack_from('>H', record, 0x00)[0] & SOURCE_FIRST_RECORD_FLAG
        for record in records[1:]
    ):
        raise IconSourceTableError(f'{view} source record is missing the continuation flag')


def validate_relocated_source_tables(
    bank: bytes,
    resource_offset: int = RESOURCE_TABLE_OFFSET,
    resource_count: int = STOCK_RESOURCE_COUNT,
) -> None:
    pages.validate_private_texture_pages(bank, require_blank=False)
    if _signed_pointer(bank, NORMAL_A_POINTER_FIELD) != NORMAL_A_OFFSET:
        raise IconSourceTableError('normal_a descriptor pointer is incorrect')
    if _signed_pointer(bank, SIDE_POINTER_FIELD) != SIDE_TABLE_OFFSET:
        raise IconSourceTableError('side descriptor pointer is incorrect')
    if _signed_pointer(bank, FRONT_POINTER_FIELD) != FRONT_TABLE_OFFSET:
        raise IconSourceTableError('front descriptor pointer is incorrect')
    if _signed_pointer(bank, RESOURCE_POINTER_FIELD) != resource_offset:
        raise IconSourceTableError('resource descriptor pointer changed unexpectedly')
    actual_resource_count = struct.unpack_from('>I', bank, resource_offset)[0]
    if actual_resource_count != resource_count:
        raise IconSourceTableError(
            f'resource count is {actual_resource_count}; expected {resource_count}'
        )
    counts = (
        _source_table_info(bank, NORMAL_A_OFFSET)[1],
        _source_table_info(bank, SIDE_TABLE_OFFSET)[1],
        _source_table_info(bank, FRONT_TABLE_OFFSET)[1],
    )
    if counts != (STOCK_NORMAL_A_COUNT, STOCK_SIDE_COUNT, STOCK_FRONT_COUNT):
        raise IconSourceTableError(f'unexpected relocated source counts: {counts}')


def validate_updated_source_tables(
    bank: bytes,
    routes: list[CharacterRoute],
    resource_offset: int = RESOURCE_TABLE_OFFSET,
    resource_count: int = STOCK_RESOURCE_COUNT,
) -> None:
    pages.validate_private_texture_pages(bank, require_blank=False)
    if _signed_pointer(bank, NORMAL_A_POINTER_FIELD) != NORMAL_A_OFFSET:
        raise IconSourceTableError('normal_a descriptor pointer is incorrect')
    if _signed_pointer(bank, SIDE_POINTER_FIELD) != SIDE_TABLE_OFFSET:
        raise IconSourceTableError('side descriptor pointer is incorrect')
    if _signed_pointer(bank, FRONT_POINTER_FIELD) != FRONT_TABLE_OFFSET:
        raise IconSourceTableError('front descriptor pointer is incorrect')
    if _signed_pointer(bank, RESOURCE_POINTER_FIELD) != resource_offset:
        raise IconSourceTableError('resource descriptor pointer changed unexpectedly')
    actual_resource_count = struct.unpack_from('>I', bank, resource_offset)[0]
    if actual_resource_count != resource_count:
        raise IconSourceTableError(
            f'resource count is {actual_resource_count}; expected {resource_count}'
        )

    normal_length, normal_count, _ = _source_table_info(bank, NORMAL_A_OFFSET)
    side_length, side_count, _ = _source_table_info(bank, SIDE_TABLE_OFFSET)
    front_length, front_count, _ = _source_table_info(bank, FRONT_TABLE_OFFSET)
    if (normal_count, side_count, front_count) != (
        STOCK_NORMAL_A_COUNT,
        STOCK_SIDE_COUNT + len(routes),
        STOCK_FRONT_COUNT + len(routes),
    ):
        raise IconSourceTableError(
            f'unexpected source counts: normal={normal_count}, side={side_count}, front={front_count}'
        )
    if NORMAL_A_OFFSET + normal_length != SIDE_TABLE_OFFSET:
        raise IconSourceTableError('normal_a and side tables are not contiguous')
    if SIDE_TABLE_OFFSET + side_length != FRONT_TABLE_OFFSET:
        raise IconSourceTableError('side and front tables are not contiguous')

    side_table = bank[SIDE_TABLE_OFFSET:SIDE_TABLE_OFFSET + side_length]
    front_table = bank[FRONT_TABLE_OFFSET:FRONT_TABLE_OFFSET + front_length]
    _validate_record_order(side_table, 'side')
    _validate_record_order(front_table, 'front')
    for route in routes:
        side_record = _record_for_character(side_table, route.char_id)
        front_record = _record_for_character(front_table, route.char_id)
        if struct.unpack_from('>HH', side_record, 0x04) != (0x0400, route.side_resource):
            raise IconSourceTableError(f'{route.name} side record is invalid')
        if struct.unpack_from('>HH', front_record, 0x04) != (0x0400, route.front_resource):
            raise IconSourceTableError(f'{route.name} front record is invalid')


def _is_updated(bank: bytes) -> bool:
    if (
        len(bank) != pages.cib.EXPANDED_BANK_LENGTH
        or _signed_pointer(bank, NORMAL_A_POINTER_FIELD) != NORMAL_A_OFFSET
        or _signed_pointer(bank, SIDE_POINTER_FIELD) != SIDE_TABLE_OFFSET
        or _signed_pointer(bank, FRONT_POINTER_FIELD) != FRONT_TABLE_OFFSET
    ):
        return False
    try:
        counts = (
            _source_table_info(bank, NORMAL_A_OFFSET)[1],
            _source_table_info(bank, SIDE_TABLE_OFFSET)[1],
            _source_table_info(bank, FRONT_TABLE_OFFSET)[1],
        )
    except IconSourceTableError:
        return False
    return counts == (
        STOCK_NORMAL_A_COUNT,
        STOCK_SIDE_COUNT + INITIAL_CHARACTER_COUNT,
        STOCK_FRONT_COUNT + INITIAL_CHARACTER_COUNT,
    )


def _configured_bank_from_current_output() -> tuple[bytes, int, bool]:
    stock_entry = pages.cib.read_icon_entry(pages.cib.INPUT_DOL)
    pages.cib.validate_stock_bank(pages.cib.INPUT_DAT, stock_entry)
    output_entry = (
        pages.cib.read_icon_entry(pages.cib.OUTPUT_DOL)
        if os.path.exists(pages.cib.OUTPUT_DOL)
        else stock_entry
    )
    if output_entry.length == pages.cib.EXPANDED_BANK_LENGTH:
        if not os.path.exists(pages.cib.OUTPUT_DAT):
            raise IconSourceTableError('expanded output DOL has no output DAT')
        bank = pages._read_bank(pages.cib.OUTPUT_DAT, output_entry)
        if not pages._is_configured(bank):
            bank = pages.add_private_texture_pages(bank)
        return bank, output_entry.offset, True
    if output_entry.offset == pages.cib.STOCK_BANK_OFFSET and output_entry.length == pages.cib.STOCK_BANK_LENGTH:
        plain = pages.cib.build_expanded_clone(pages.cib.INPUT_DAT, stock_entry)
        return pages.add_private_texture_pages(plain), pages.cib._choose_destination(pages.cib.OUTPUT_DAT), False
    raise IconSourceTableError('output icon entry is not stock or a supported expanded bank')


def install_icon_source_tables(dry_run: bool = False) -> SourceTableResult:
    routes = load_character_routes()
    bank, destination, existing_expansion = _configured_bank_from_current_output()
    if _is_updated(bank):
        resource_offset = _signed_pointer(bank, RESOURCE_POINTER_FIELD)
        if resource_offset < 0 or resource_offset + 4 > len(bank):
            raise IconSourceTableError('resource descriptor pointer is outside the bank')
        resource_count = struct.unpack_from('>I', bank, resource_offset)[0]
        validate_updated_source_tables(
            bank,
            routes,
            resource_offset=resource_offset,
            resource_count=resource_count,
        )
        return SourceTableResult(
            len(routes),
            destination,
            STOCK_SIDE_COUNT + len(routes),
            STOCK_FRONT_COUNT + len(routes),
            True,
            dry_run,
            False,
        )
    updated = update_icon_source_tables(bank, routes)
    if dry_run:
        return SourceTableResult(
            len(routes),
            destination,
            STOCK_SIDE_COUNT + len(routes),
            STOCK_FRONT_COUNT + len(routes),
            False,
            True,
            False,
        )

    if existing_expansion:
        with open(pages.cib.OUTPUT_DAT, 'r+b') as dat:
            dat.seek(destination)
            dat.write(updated)
    else:
        pages.cib.hh.writeModelBlock(updated, destination)
        if not os.path.exists(pages.cib.OUTPUT_DOL):
            os.makedirs(os.path.dirname(pages.cib.OUTPUT_DOL), exist_ok=True)
            shutil.copy2(pages.cib.INPUT_DOL, pages.cib.OUTPUT_DOL)
        pages.cib.patch_direct_icon_entry(pages.cib.OUTPUT_DOL, destination, len(updated))
        pages.cib.hh.patchFstFileSize(os.path.getsize(pages.cib.OUTPUT_DAT))

    report = {
        'descriptor_offset': f'0x{ICON_DESCRIPTOR_OFFSET:X}',
        'tables': {
            'normal_a': {'offset': f'0x{NORMAL_A_OFFSET:X}', 'count': STOCK_NORMAL_A_COUNT},
            'side': {'offset': f'0x{SIDE_TABLE_OFFSET:X}', 'count': STOCK_SIDE_COUNT + len(routes)},
            'front': {'offset': f'0x{FRONT_TABLE_OFFSET:X}', 'count': STOCK_FRONT_COUNT + len(routes)},
        },
        'characters': [
            {
                'name': route.name,
                'char_id': f'0x{route.char_id:02X}',
                'donor_id': f'0x{route.donor_id:02X}',
                'side_resource': f'0x{route.side_resource:02X}',
                'front_resource': f'0x{route.front_resource:02X}',
            }
            for route in routes
        ],
    }
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, 'w', encoding='utf-8') as report_file:
        json.dump(report, report_file, indent=2)
        report_file.write('\n')
    return SourceTableResult(
        len(routes),
        destination,
        STOCK_SIDE_COUNT + len(routes),
        STOCK_FRONT_COUNT + len(routes),
        False,
        False,
        True,
    )


def _print_result(result: SourceTableResult) -> None:
    action = 'Already configured' if result.already_configured else ('Dry run' if result.dry_run else 'Written')
    summary = (
        f'{action}: icon source tables for {result.character_count} characters\n'
        f'  bank:     0x{result.destination_offset:08X}\n'
        f'  normal_a: 0x{NORMAL_A_OFFSET:X} ({STOCK_NORMAL_A_COUNT} records)\n'
        f'  side:     0x{SIDE_TABLE_OFFSET:X} ({result.side_count} records)\n'
        f'  front:    0x{FRONT_TABLE_OFFSET:X} ({result.front_count} records)'
    )
    summary += (
        f'\n  resources: side 0x{SIDE_RESOURCE_BASE:02X}-0x{SIDE_RESOURCE_BASE + result.character_count - 1:02X}, '
        f'front 0x{FRONT_RESOURCE_BASE:02X}-0x{FRONT_RESOURCE_BASE + result.character_count - 1:02X}'
    )
    if result.report_written:
        summary += f'\n  report:   {os.path.relpath(REPORT_PATH, pages.cib.ROOT)}'
    _slogger.info(summary, source='icons.update_icon_source_tables')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Relocate and extend the icon source tables for custom characters.'
    )
    parser.add_argument('--dry-run', action='store_true', help='validate and report without writing files')
    args = parser.parse_args()
    try:
        result = install_icon_source_tables(dry_run=args.dry_run)
    except (IconSourceTableError, pages.PrivateTexturePageError, pages.cib.IconBankCloneError, OSError) as exc:
        parser.exit(1, f'ERROR: {exc}\n')
    _print_result(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())