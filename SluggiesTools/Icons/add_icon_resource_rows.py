import argparse
import json
import os
import struct
from dataclasses import dataclass

try:
    from . import prepare_icon_artwork as artwork
    from . import update_icon_source_tables as sources
except ImportError:
    import prepare_icon_artwork as artwork
    import update_icon_source_tables as sources


RESOURCE_ROW_SIZE = 0x14
RESOURCE_HEADER_SIZE = 0x08
STOCK_RESOURCE_TABLE_LENGTH = (
    RESOURCE_HEADER_SIZE + sources.STOCK_RESOURCE_COUNT * RESOURCE_ROW_SIZE
)
CUSTOM_RESOURCE_COUNT = sources.INITIAL_CHARACTER_COUNT * 2
EXPANDED_RESOURCE_COUNT = sources.STOCK_RESOURCE_COUNT + CUSTOM_RESOURCE_COUNT
EXPANDED_RESOURCE_TABLE_OFFSET = sources.RESOURCE_TABLE_OFFSET
ICON_TABLE_END_FIELD = 0x10
STOCK_ICON_TABLE_END = 0x4F68
EXPANDED_ICON_TABLE_END = STOCK_ICON_TABLE_END + CUSTOM_RESOURCE_COUNT * RESOURCE_ROW_SIZE
REPORT_PATH = os.path.join(
    sources.pages.cib.ROOT,
    '2_Output_Models',
    '_ICONS',
    'metadata',
    'custom_icon_resource_rows.json',
)


class IconResourceRowError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResourceRowResult:
    character_count: int
    destination_offset: int
    resource_table_offset: int
    resource_count: int
    already_configured: bool
    dry_run: bool
    report_written: bool


def _resource_table_info(bank: bytes, offset: int) -> tuple[int, int]:
    if offset < 0 or offset + RESOURCE_HEADER_SIZE > len(bank):
        raise IconResourceRowError(f'resource table offset 0x{offset:X} is outside the bank')
    count, total_length = struct.unpack_from('>II', bank, offset)
    expected_length = RESOURCE_HEADER_SIZE + count * RESOURCE_ROW_SIZE
    if total_length != expected_length:
        raise IconResourceRowError(
            f'invalid resource table at 0x{offset:X}: length=0x{total_length:X}, '
            f'count={count}'
        )
    if offset + total_length > len(bank):
        raise IconResourceRowError(f'resource table at 0x{offset:X} is truncated')
    return count, total_length


def _resource_row(bank: bytes, table_offset: int, resource_id: int) -> bytes:
    count, _ = _resource_table_info(bank, table_offset)
    if not 0 <= resource_id < count:
        raise IconResourceRowError(
            f'resource ID 0x{resource_id:X} is outside table count 0x{count:X}'
        )
    start = table_offset + RESOURCE_HEADER_SIZE + resource_id * RESOURCE_ROW_SIZE
    return bank[start:start + RESOURCE_ROW_SIZE]


def _match_entries(
    routes: list[sources.CharacterRoute],
    entries: list[artwork.ArtworkEntry],
) -> list[tuple[sources.CharacterRoute, artwork.ArtworkEntry]]:
    entries_by_id = {entry.char_id: entry for entry in entries}
    if len(entries_by_id) != len(entries):
        raise IconResourceRowError('artwork layout contains duplicate character IDs')
    if set(entries_by_id) != {route.char_id for route in routes}:
        raise IconResourceRowError('source routes and artwork layout have different character IDs')
    return [(route, entries_by_id[route.char_id]) for route in routes]


def _custom_row(
    donor_row: bytes,
    page_id: int,
    entry: artwork.ArtworkEntry,
) -> bytes:
    if len(donor_row) != RESOURCE_ROW_SIZE:
        raise IconResourceRowError('donor resource row is truncated')
    x = entry.resource_x
    y = entry.resource_y
    if x < 0 or y < 0 or x + artwork.ICON_WIDTH > artwork.ATLAS_WIDTH or y + artwork.ICON_HEIGHT > artwork.ATLAS_HEIGHT:
        raise IconResourceRowError(f'{entry.name} resource rectangle is outside the atlas')
    row = bytearray(donor_row)
    struct.pack_into(
        '>HHffff',
        row,
        0,
        page_id,
        0,
        y / artwork.ATLAS_HEIGHT,
        x / artwork.ATLAS_WIDTH,
        (y + artwork.ICON_HEIGHT) / artwork.ATLAS_HEIGHT,
        (x + artwork.ICON_WIDTH) / artwork.ATLAS_WIDTH,
    )
    return bytes(row)


def add_icon_resource_rows(
    bank: bytes,
    routes: list[sources.CharacterRoute],
    entries: list[artwork.ArtworkEntry],
) -> bytes:
    side_count = sources._source_table_info(bank, sources.SIDE_TABLE_OFFSET)[1]
    custom_sources_present = side_count == sources.STOCK_SIDE_COUNT + len(routes)
    if custom_sources_present:
        sources.validate_updated_source_tables(bank, routes)
    else:
        sources.validate_relocated_source_tables(bank)
    pairs = _match_entries(routes, entries)
    count, total_length = _resource_table_info(bank, sources.RESOURCE_TABLE_OFFSET)
    if count != sources.STOCK_RESOURCE_COUNT:
        raise IconResourceRowError(
            f'stock resource count is 0x{count:X}; expected 0x{sources.STOCK_RESOURCE_COUNT:X}'
        )

    custom_rows = []
    for route, entry in pairs:
        donor_row = _resource_row(
            bank, sources.RESOURCE_TABLE_OFFSET, route.donor_side_resource
        )
        custom_rows.append(_custom_row(donor_row, sources.pages.SIDE_PAGE, entry))
    for route, entry in pairs:
        donor_row = _resource_row(
            bank, sources.RESOURCE_TABLE_OFFSET, route.donor_front_resource
        )
        custom_rows.append(_custom_row(donor_row, sources.pages.FRONT_PAGE, entry))

    original_table = bank[
        sources.RESOURCE_TABLE_OFFSET:sources.RESOURCE_TABLE_OFFSET + total_length
    ]
    expanded_table = bytearray(original_table)
    expanded_table.extend(b''.join(custom_rows))
    struct.pack_into('>II', expanded_table, 0, EXPANDED_RESOURCE_COUNT, len(expanded_table))
    expanded_end = EXPANDED_RESOURCE_TABLE_OFFSET + len(expanded_table)
    if expanded_end + 8 != len(bank):
        raise IconResourceRowError('expanded resource table does not leave the expected 8-byte tail')
    icon_table_end = struct.unpack_from(
        '>I', bank, sources.pages.RELOCATED_ICON_TABLE + ICON_TABLE_END_FIELD
    )[0]
    if icon_table_end != STOCK_ICON_TABLE_END:
        raise IconResourceRowError(
            f'icon-table end is 0x{icon_table_end:X}; expected 0x{STOCK_ICON_TABLE_END:X}'
        )

    updated = bytearray(bank)
    updated[EXPANDED_RESOURCE_TABLE_OFFSET:expanded_end] = expanded_table
    struct.pack_into(
        '>I',
        updated,
        sources.pages.RELOCATED_ICON_TABLE + ICON_TABLE_END_FIELD,
        EXPANDED_ICON_TABLE_END,
    )
    sources._write_signed_pointer(
        updated,
        sources.RESOURCE_POINTER_FIELD,
        EXPANDED_RESOURCE_TABLE_OFFSET,
    )
    if custom_sources_present:
        validate_icon_resource_rows(bytes(updated), routes, entries)
    else:
        sources.validate_relocated_source_tables(
            bytes(updated),
            resource_offset=EXPANDED_RESOURCE_TABLE_OFFSET,
            resource_count=EXPANDED_RESOURCE_COUNT,
        )
        _validate_resource_rows_data(bytes(updated), routes, entries)
    return bytes(updated)


def _validate_resource_table_shape(bank: bytes) -> None:
    count, total_length = _resource_table_info(bank, EXPANDED_RESOURCE_TABLE_OFFSET)
    if count != EXPANDED_RESOURCE_COUNT:
        raise IconResourceRowError('expanded resource count is incorrect')
    if EXPANDED_RESOURCE_TABLE_OFFSET + total_length != len(bank) - 8:
        raise IconResourceRowError('expanded resource table end is incorrect')
    icon_table_end = struct.unpack_from(
        '>I', bank, sources.pages.RELOCATED_ICON_TABLE + ICON_TABLE_END_FIELD
    )[0]
    if icon_table_end != EXPANDED_ICON_TABLE_END:
        raise IconResourceRowError(
            f'expanded icon-table end is 0x{icon_table_end:X}; '
            f'expected 0x{EXPANDED_ICON_TABLE_END:X}'
        )


def _validate_resource_rows_data(
    bank: bytes,
    routes: list[sources.CharacterRoute],
    entries: list[artwork.ArtworkEntry],
) -> None:
    _validate_resource_table_shape(bank)

    for route, entry in _match_entries(routes, entries):
        for resource_id, page_id in (
            (route.side_resource, sources.pages.SIDE_PAGE),
            (route.front_resource, sources.pages.FRONT_PAGE),
        ):
            row = _resource_row(bank, EXPANDED_RESOURCE_TABLE_OFFSET, resource_id)
            actual_page, reserved, v1, u1, v2, u2 = struct.unpack('>HHffff', row)
            expected = (
                page_id,
                0,
                entry.resource_y / artwork.ATLAS_HEIGHT,
                entry.resource_x / artwork.ATLAS_WIDTH,
                (entry.resource_y + artwork.ICON_HEIGHT) / artwork.ATLAS_HEIGHT,
                (entry.resource_x + artwork.ICON_WIDTH) / artwork.ATLAS_WIDTH,
            )
            if (actual_page, reserved, v1, u1, v2, u2) != expected:
                raise IconResourceRowError(f'{entry.name} resource row 0x{resource_id:X} is invalid')


def validate_icon_resource_rows(
    bank: bytes,
    routes: list[sources.CharacterRoute],
    entries: list[artwork.ArtworkEntry],
) -> None:
    sources.validate_updated_source_tables(
        bank,
        routes,
        resource_offset=EXPANDED_RESOURCE_TABLE_OFFSET,
        resource_count=EXPANDED_RESOURCE_COUNT,
    )
    _validate_resource_rows_data(bank, routes, entries)


def _is_updated(bank: bytes) -> bool:
    if sources._signed_pointer(bank, sources.RESOURCE_POINTER_FIELD) != EXPANDED_RESOURCE_TABLE_OFFSET:
        return False
    try:
        count, _ = _resource_table_info(bank, EXPANDED_RESOURCE_TABLE_OFFSET)
    except IconResourceRowError:
        return False
    return count == EXPANDED_RESOURCE_COUNT


def install_icon_resource_rows(dry_run: bool = False) -> ResourceRowResult:
    routes = sources.load_character_routes()
    entries = artwork.load_artwork_entries()
    bank, destination, existing_expansion = sources._configured_bank_from_current_output()
    if not sources._is_updated(bank):
        bank = sources.update_icon_source_tables(bank, routes)
    if _is_updated(bank):
        validate_icon_resource_rows(bank, routes, entries)
        return ResourceRowResult(
            len(routes), destination, EXPANDED_RESOURCE_TABLE_OFFSET,
            EXPANDED_RESOURCE_COUNT, True, dry_run, False,
        )

    updated = add_icon_resource_rows(bank, routes, entries)
    if dry_run:
        return ResourceRowResult(
            len(routes), destination, EXPANDED_RESOURCE_TABLE_OFFSET,
            EXPANDED_RESOURCE_COUNT, False, True, False,
        )

    if not existing_expansion:
        sources.install_icon_source_tables()
        output_entry = sources.pages.cib.read_icon_entry(sources.pages.cib.OUTPUT_DOL)
        destination = output_entry.offset
    with open(sources.pages.cib.OUTPUT_DAT, 'r+b') as dat:
        dat.seek(destination)
        dat.write(updated)

    report = {
        'resource_table_offset': f'0x{EXPANDED_RESOURCE_TABLE_OFFSET:X}',
        'resource_count': EXPANDED_RESOURCE_COUNT,
        'characters': [
            {
                'name': route.name,
                'char_id': f'0x{route.char_id:02X}',
                'side': {'resource_id': f'0x{route.side_resource:02X}', 'page': '0x92'},
                'front': {'resource_id': f'0x{route.front_resource:02X}', 'page': '0x93'},
                'resource_rect': {
                    'x': entry.resource_x,
                    'y': entry.resource_y,
                    'width': artwork.ICON_WIDTH,
                    'height': artwork.ICON_HEIGHT,
                },
            }
            for route, entry in _match_entries(routes, entries)
        ],
    }
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, 'w', encoding='utf-8') as report_file:
        json.dump(report, report_file, indent=2)
        report_file.write('\n')
    return ResourceRowResult(
        len(routes), destination, EXPANDED_RESOURCE_TABLE_OFFSET,
        EXPANDED_RESOURCE_COUNT, False, False, True,
    )


def _print_result(result: ResourceRowResult) -> None:
    action = 'Already configured' if result.already_configured else ('Dry run' if result.dry_run else 'Written')
    print(f'{action}: resource rows for {result.character_count} custom characters')
    print(f'  bank:           0x{result.destination_offset:08X}')
    print(f'  resource table: 0x{result.resource_table_offset:X} ({result.resource_count} rows)')
    print('  side rows:      0x98-0x9B on page 0x92')
    print('  front rows:     0x9C-0x9F on page 0x93')
    if result.report_written:
        print(f'  report:         {os.path.relpath(REPORT_PATH, sources.pages.cib.ROOT)}')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Relocate and extend the icon resource table with custom UV rows.'
    )
    parser.add_argument('--dry-run', action='store_true', help='validate and report without writing files')
    args = parser.parse_args()
    try:
        result = install_icon_resource_rows(dry_run=args.dry_run)
    except (
        IconResourceRowError,
        artwork.IconArtworkError,
        sources.IconSourceTableError,
        sources.pages.PrivateTexturePageError,
        sources.pages.cib.IconBankCloneError,
        OSError,
    ) as exc:
        parser.exit(1, f'ERROR: {exc}\n')
    _print_result(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())