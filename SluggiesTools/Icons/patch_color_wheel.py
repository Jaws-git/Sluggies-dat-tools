import argparse
import json
import os
import shutil
from dataclasses import dataclass

try:
    from . import clone_icon_bank as cib
except ImportError:
    import clone_icon_bank as cib


DESCRIPTION_PATH = os.path.join(cib.ICONS_DIR, 'icon_characters.json')
REPORT_PATH = os.path.join(
    cib.ROOT,
    '2_Output_Models',
    '_ICONS',
    'metadata',
    'custom_icon_color_wheel.json',
)
COLOR_WHEEL_OFFSET = 0x0062D650
COLOR_WHEEL_STRIDE = 0x08
COLOR_WHEEL_COUNT = 101
INITIAL_CHARACTER_COUNT = 6
UNUSED_CHARACTER_IDS = set(range(0x47, 0x4D))
COLOR_WHEEL_FIELDS = (
    'species',
    'captain',
    'model',
    'is_captain',
    'flags',
    'variant',
    'icon_valid',
    'icon_slot',
)


class ColorWheelPatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ColorWheelEntry:
    name: str
    char_id: int
    row: bytes


@dataclass(frozen=True)
class ColorWheelResult:
    character_count: int
    changed_count: int
    already_configured: bool
    dry_run: bool
    report_written: bool


def _parse_u8(value, field_name: str) -> int:
    try:
        parsed = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise ColorWheelPatchError(f'invalid {field_name}: {value!r}') from exc
    if not 0 <= parsed <= 0xFF:
        raise ColorWheelPatchError(f'{field_name} is outside u8 range: {parsed}')
    return parsed


def load_color_wheel_entries(
    description_path: str = DESCRIPTION_PATH,
) -> list[ColorWheelEntry]:
    try:
        with open(description_path, 'r', encoding='utf-8') as description_file:
            description = json.load(description_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ColorWheelPatchError(
            f'could not read description file {description_path}: {exc}'
        ) from exc

    characters = description.get('characters')
    if not isinstance(characters, list) or len(characters) != INITIAL_CHARACTER_COUNT:
        raise ColorWheelPatchError(
            f'initial implementation requires exactly {INITIAL_CHARACTER_COUNT} characters'
        )

    entries = []
    seen_ids = set()
    for index, character in enumerate(characters):
        if not isinstance(character, dict):
            raise ColorWheelPatchError(f'character {index} is not an object')
        name = character.get('name')
        if not isinstance(name, str) or not name:
            raise ColorWheelPatchError(f'character {index} has no valid name')
        char_id = _parse_u8(character.get('char_id'), f'{name}.char_id')
        if char_id not in UNUSED_CHARACTER_IDS:
            raise ColorWheelPatchError(
                f'{name}.char_id 0x{char_id:02X} is not an unused character slot'
            )
        if char_id in seen_ids:
            raise ColorWheelPatchError(f'duplicate character ID 0x{char_id:02X}')
        seen_ids.add(char_id)

        color_wheel = character.get('color_wheel')
        if not isinstance(color_wheel, dict):
            raise ColorWheelPatchError(f'{name}.color_wheel is not an object')
        missing_fields = [field for field in COLOR_WHEEL_FIELDS if field not in color_wheel]
        if missing_fields:
            raise ColorWheelPatchError(
                f'{name}.color_wheel is missing: {", ".join(missing_fields)}'
            )
        row = bytes(
            _parse_u8(color_wheel[field], f'{name}.color_wheel.{field}')
            for field in COLOR_WHEEL_FIELDS
        )
        if row[6] != 1:
            raise ColorWheelPatchError(f'{name}.color_wheel.icon_valid must be 1')
        entries.append(ColorWheelEntry(name, char_id, row))
    return entries


def _row_offset(char_id: int, table_offset: int = COLOR_WHEEL_OFFSET) -> int:
    if not 0 <= char_id < COLOR_WHEEL_COUNT:
        raise ColorWheelPatchError(f'character ID 0x{char_id:X} is outside the table')
    return table_offset + char_id * COLOR_WHEEL_STRIDE


def patch_color_wheel(
    dol: bytes,
    stock_dol: bytes,
    entries: list[ColorWheelEntry],
    table_offset: int = COLOR_WHEEL_OFFSET,
) -> tuple[bytes, int]:
    table_end = table_offset + COLOR_WHEEL_COUNT * COLOR_WHEEL_STRIDE
    if len(dol) < table_end or len(stock_dol) < table_end:
        raise ColorWheelPatchError('DOL is truncated before the color-wheel table')

    updated = bytearray(dol)
    changed_count = 0
    for entry in entries:
        offset = _row_offset(entry.char_id, table_offset)
        current_row = dol[offset:offset + COLOR_WHEEL_STRIDE]
        stock_row = stock_dol[offset:offset + COLOR_WHEEL_STRIDE]
        if current_row == entry.row:
            continue
        if current_row != stock_row:
            raise ColorWheelPatchError(
                f'unexpected row for {entry.name} at 0x{offset:X}: '
                f'found {current_row.hex()}, expected stock {stock_row.hex()} '
                f'or configured {entry.row.hex()}'
            )
        updated[offset:offset + COLOR_WHEEL_STRIDE] = entry.row
        changed_count += 1

    validate_color_wheel(bytes(updated), entries, table_offset)
    return bytes(updated), changed_count


def validate_color_wheel(
    dol: bytes,
    entries: list[ColorWheelEntry],
    table_offset: int = COLOR_WHEEL_OFFSET,
) -> None:
    for entry in entries:
        offset = _row_offset(entry.char_id, table_offset)
        if dol[offset:offset + COLOR_WHEEL_STRIDE] != entry.row:
            raise ColorWheelPatchError(f'{entry.name} color-wheel row is not configured')


def install_color_wheel(dry_run: bool = False) -> ColorWheelResult:
    entries = load_color_wheel_entries()
    try:
        with open(cib.INPUT_DOL, 'rb') as stock_file:
            stock_dol = stock_file.read()
        target_path = cib.OUTPUT_DOL if os.path.exists(cib.OUTPUT_DOL) else cib.INPUT_DOL
        with open(target_path, 'rb') as target_file:
            current_dol = target_file.read()
    except OSError as exc:
        raise ColorWheelPatchError(f'could not read DOL: {exc}') from exc

    updated_dol, changed_count = patch_color_wheel(current_dol, stock_dol, entries)
    already_configured = changed_count == 0
    if dry_run or already_configured:
        return ColorWheelResult(
            len(entries), changed_count, already_configured, dry_run, False
        )

    os.makedirs(os.path.dirname(cib.OUTPUT_DOL), exist_ok=True)
    if not os.path.exists(cib.OUTPUT_DOL):
        shutil.copy2(cib.INPUT_DOL, cib.OUTPUT_DOL)
    with open(cib.OUTPUT_DOL, 'r+b') as output_file:
        for entry in entries:
            offset = _row_offset(entry.char_id)
            output_file.seek(offset)
            output_file.write(entry.row)

    report = {
        'table_offset': f'0x{COLOR_WHEEL_OFFSET:X}',
        'row_stride': COLOR_WHEEL_STRIDE,
        'characters': [
            {
                'name': entry.name,
                'char_id': f'0x{entry.char_id:02X}',
                'row_offset': f'0x{_row_offset(entry.char_id):X}',
                'row_hex': entry.row.hex(),
                'icon_valid': entry.row[6],
                'icon_slot': entry.row[7],
            }
            for entry in entries
        ],
    }
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, 'w', encoding='utf-8') as report_file:
        json.dump(report, report_file, indent=2)
        report_file.write('\n')
    return ColorWheelResult(len(entries), changed_count, False, False, True)


def _print_result(result: ColorWheelResult) -> None:
    action = 'Already configured' if result.already_configured else ('Dry run' if result.dry_run else 'Written')
    print(f'{action}: color-wheel rows for {result.character_count} custom characters')
    print(f'  table:   0x{COLOR_WHEEL_OFFSET:X}')
    print(f'  changed: {result.changed_count}')
    if result.report_written:
        print(f'  report:  {os.path.relpath(REPORT_PATH, cib.ROOT)}')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Patch custom character rows in the DOL color-wheel table.'
    )
    parser.add_argument('--dry-run', action='store_true', help='validate and report without writing files')
    args = parser.parse_args()
    try:
        result = install_color_wheel(dry_run=args.dry_run)
    except (ColorWheelPatchError, OSError) as exc:
        parser.exit(1, f'ERROR: {exc}\n')
    _print_result(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())