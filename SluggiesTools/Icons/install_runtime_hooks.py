import argparse
import hashlib
import json
import os
import shutil
import struct
from dataclasses import dataclass

try:
    from . import add_icon_resource_rows as resources
except ImportError:
    import add_icon_resource_rows as resources


LOWER_HOOK = 0x8050A5AC
LOWER_STUB = 0x80004C6C
LOWER_RETURN = LOWER_HOOK + 4
LOWER_STOCK_WORD = 0x9421FFA0
KEY_HOOK = 0x80519478
KEY_STUB = 0x8000576C
KEY_RETURN = KEY_HOOK + 4
KEY_CALLEE = 0x8050E510
KEY_STOCK_WORD = 0x4BFF5099
ROW_HOOK = 0x8051952C
ROW_STUB = 0x80005DB0
ROW_RETURN = ROW_HOOK + 4
ROW_STOCK_WORD = 0xA0BF0000
STATE_ADDRESS = 0x80004E00
PENDING_ID_ADDRESS = STATE_ADDRESS
RESOLVED_ROW_ID_ADDRESS = STATE_ADDRESS + 4
CUSTOM_ROWS_ADDRESS = STATE_ADDRESS + 8
CUSTOM_ROW_STRIDE = resources.RESOURCE_ROW_SIZE * 2
REPORT_PATH = os.path.join(
    resources.sources.pages.cib.ROOT,
    '2_Output_Models',
    '_ICONS',
    'metadata',
    'custom_icon_runtime_hooks.json',
)


class RuntimeHookError(RuntimeError):
    pass


@dataclass(frozen=True)
class HookPatch:
    name: str
    address: int
    stock: bytes
    patched: bytes


@dataclass(frozen=True)
class RuntimeHookResult:
    character_count: int
    changed_region_count: int
    lower_stub_size: int
    key_stub_size: int
    row_stub_size: int
    already_configured: bool
    dry_run: bool
    report_written: bool


class _PpcBuilder:
    def __init__(self):
        self.words = []
        self.labels = {}
        self.fixups = []

    def emit(self, word: int) -> None:
        self.words.append(word & 0xFFFFFFFF)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise RuntimeHookError(f'duplicate PPC label: {name}')
        self.labels[name] = len(self.words)

    def branch(self, label: str, link: bool = False) -> None:
        self.fixups.append((len(self.words), label, 'b', link))
        self.emit(0)

    def beq(self, label: str) -> None:
        self.fixups.append((len(self.words), label, 'beq', False))
        self.emit(0)

    def finish(self, base_address: int) -> bytes:
        words = list(self.words)
        for index, label, kind, link in self.fixups:
            if label not in self.labels:
                raise RuntimeHookError(f'undefined PPC label: {label}')
            source = base_address + index * 4
            target = base_address + self.labels[label] * 4
            words[index] = (
                _branch_word(source, target, link)
                if kind == 'b'
                else _beq_word(source, target)
            )
        return struct.pack(f'>{len(words)}I', *words)


def _signed_range(value: int, bits: int) -> bool:
    return -(1 << (bits - 1)) <= value < (1 << (bits - 1))


def _branch_word(source: int, target: int, link: bool = False) -> int:
    displacement = target - source
    if displacement % 4 or not _signed_range(displacement, 26):
        raise RuntimeHookError(
            f'PPC branch from 0x{source:08X} to 0x{target:08X} is out of range'
        )
    return 0x48000000 | (displacement & 0x03FFFFFC) | int(link)


def _beq_word(source: int, target: int) -> int:
    displacement = target - source
    if displacement % 4 or not _signed_range(displacement, 16):
        raise RuntimeHookError(
            f'PPC beq from 0x{source:08X} to 0x{target:08X} is out of range'
        )
    return 0x41820000 | (displacement & 0xFFFC)


def _lis(register: int, value: int) -> int:
    return 0x3C000000 | (register << 21) | (value & 0xFFFF)


def _ori(target: int, source: int, value: int) -> int:
    return 0x60000000 | (source << 21) | (target << 16) | (value & 0xFFFF)


def _li(register: int, value: int) -> int:
    return 0x38000000 | (register << 21) | (value & 0xFFFF)


def _cmpwi(register: int, value: int) -> int:
    return 0x2C000000 | (register << 16) | (value & 0xFFFF)


def _lwz(target: int, base: int, offset: int) -> int:
    return 0x80000000 | (target << 21) | (base << 16) | (offset & 0xFFFF)


def _stw(source: int, base: int, offset: int) -> int:
    return 0x90000000 | (source << 21) | (base << 16) | (offset & 0xFFFF)


def _load_address(builder: _PpcBuilder, register: int, address: int) -> None:
    builder.emit(_lis(register, address >> 16))
    builder.emit(_ori(register, register, address))


def _emit_clear_state(builder: _PpcBuilder, base_register: int = 12) -> None:
    builder.emit(_li(0, 0))
    builder.emit(_stw(0, base_register, 0))
    builder.emit(_stw(0, base_register, 4))


def _build_lower_stub(routes: list[resources.sources.CharacterRoute]) -> bytes:
    builder = _PpcBuilder()
    _load_address(builder, 12, STATE_ADDRESS)
    for index, route in enumerate(routes):
        builder.emit(_cmpwi(3, route.char_id))
        builder.beq(f'route_{index}')
    _emit_clear_state(builder)
    builder.branch('done')
    for index, route in enumerate(routes):
        builder.label(f'route_{index}')
        builder.emit(_stw(3, 12, 0))
        builder.emit(_li(3, route.donor_id))
        builder.branch('done')
    builder.label('done')
    builder.emit(LOWER_STOCK_WORD)
    builder.emit(_branch_word(LOWER_STUB + len(builder.words) * 4, LOWER_RETURN))
    return builder.finish(LOWER_STUB)


def _build_key_stub(routes: list[resources.sources.CharacterRoute]) -> bytes:
    builder = _PpcBuilder()
    builder.emit(_branch_word(KEY_STUB, KEY_CALLEE, link=True))
    _load_address(builder, 12, STATE_ADDRESS)
    builder.emit(_lwz(11, 12, 0))
    builder.emit(_cmpwi(11, 0))
    builder.beq('no_pending')
    for index, route in enumerate(routes):
        builder.emit(_cmpwi(11, route.char_id))
        builder.beq(f'route_{index}')
    builder.branch('clear')
    for index, route in enumerate(routes):
        builder.label(f'route_{index}')
        builder.emit(_cmpwi(24, route.donor_side_resource))
        builder.beq('accept')
        builder.emit(_cmpwi(24, route.donor_front_resource))
        builder.beq('accept')
        builder.branch('clear')
    builder.label('accept')
    builder.emit(_stw(24, 12, 4))
    builder.branch('return')
    builder.label('no_pending')
    builder.emit(_li(0, 0))
    builder.emit(_stw(0, 12, 4))
    builder.branch('return')
    builder.label('clear')
    _emit_clear_state(builder)
    builder.label('return')
    builder.emit(_branch_word(KEY_STUB + len(builder.words) * 4, KEY_RETURN))
    return builder.finish(KEY_STUB)


def _custom_row_address(index: int, front: bool) -> int:
    return CUSTOM_ROWS_ADDRESS + index * CUSTOM_ROW_STRIDE + (
        resources.RESOURCE_ROW_SIZE if front else 0
    )


def _build_row_stub(routes: list[resources.sources.CharacterRoute]) -> bytes:
    builder = _PpcBuilder()
    _load_address(builder, 12, STATE_ADDRESS)
    builder.emit(_lwz(11, 12, 0))
    builder.emit(_cmpwi(11, 0))
    builder.beq('clear')
    builder.emit(_lwz(0, 12, 4))
    for index, route in enumerate(routes):
        builder.emit(_cmpwi(11, route.char_id))
        builder.beq(f'route_{index}')
    builder.branch('clear')
    for index, route in enumerate(routes):
        builder.label(f'route_{index}')
        builder.emit(_cmpwi(0, route.donor_side_resource))
        builder.beq(f'side_{index}')
        builder.emit(_cmpwi(0, route.donor_front_resource))
        builder.beq(f'front_{index}')
        builder.branch('clear')
        builder.label(f'side_{index}')
        _load_address(builder, 31, _custom_row_address(index, False))
        builder.branch('clear')
        builder.label(f'front_{index}')
        _load_address(builder, 31, _custom_row_address(index, True))
        builder.branch('clear')
    builder.label('clear')
    _emit_clear_state(builder)
    builder.emit(ROW_STOCK_WORD)
    builder.emit(_branch_word(ROW_STUB + len(builder.words) * 4, ROW_RETURN))
    return builder.finish(ROW_STUB)


def _dol_sections(dol: bytes) -> list[tuple[int, int, int]]:
    if len(dol) < 0xE4:
        raise RuntimeHookError('DOL header is truncated')
    sections = []
    for index in range(18):
        file_offset = struct.unpack_from('>I', dol, index * 4)[0]
        address = struct.unpack_from('>I', dol, 0x48 + index * 4)[0]
        size = struct.unpack_from('>I', dol, 0x90 + index * 4)[0]
        if file_offset and size:
            if file_offset + size > len(dol):
                raise RuntimeHookError(f'DOL section {index} is truncated')
            sections.append((file_offset, address, size))
    return sections


def _vaddr_to_file(dol: bytes, address: int, size: int = 1) -> int:
    for file_offset, section_address, section_size in _dol_sections(dol):
        if section_address <= address and address + size <= section_address + section_size:
            return file_offset + address - section_address
    raise RuntimeHookError(
        f'virtual range 0x{address:08X}-0x{address + size:08X} is not file-backed'
    )


def _build_custom_rows(
    bank: bytes,
    routes: list[resources.sources.CharacterRoute],
) -> bytes:
    rows = []
    for route in routes:
        rows.append(resources._resource_row(
            bank, resources.EXPANDED_RESOURCE_TABLE_OFFSET, route.side_resource
        ))
        rows.append(resources._resource_row(
            bank, resources.EXPANDED_RESOURCE_TABLE_OFFSET, route.front_resource
        ))
    return b''.join(rows)


def build_hook_patches(
    stock_dol: bytes,
    routes: list[resources.sources.CharacterRoute],
    custom_rows: bytes,
) -> list[HookPatch]:
    expected_row_length = len(routes) * CUSTOM_ROW_STRIDE
    if len(custom_rows) != expected_row_length:
        raise RuntimeHookError(
            f'custom DOL rows are 0x{len(custom_rows):X} bytes; '
            f'expected 0x{expected_row_length:X}'
        )
    lower_stub = _build_lower_stub(routes)
    key_stub = _build_key_stub(routes)
    row_stub = _build_row_stub(routes)
    generated_regions = (
        ('lower stub', LOWER_STUB, lower_stub),
        ('state and custom rows', STATE_ADDRESS, bytes(8) + custom_rows),
        ('key stub', KEY_STUB, key_stub),
        ('row stub', ROW_STUB, row_stub),
    )
    patches = [
        HookPatch(
            'lower hook', LOWER_HOOK, struct.pack('>I', LOWER_STOCK_WORD),
            struct.pack('>I', _branch_word(LOWER_HOOK, LOWER_STUB)),
        ),
        HookPatch(
            'key hook', KEY_HOOK, struct.pack('>I', KEY_STOCK_WORD),
            struct.pack('>I', _branch_word(KEY_HOOK, KEY_STUB)),
        ),
        HookPatch(
            'row hook', ROW_HOOK, struct.pack('>I', ROW_STOCK_WORD),
            struct.pack('>I', _branch_word(ROW_HOOK, ROW_STUB)),
        ),
    ]
    for name, address, patched in generated_regions:
        offset = _vaddr_to_file(stock_dol, address, len(patched))
        stock = stock_dol[offset:offset + len(patched)]
        if any(stock):
            raise RuntimeHookError(
                f'{name} range at 0x{address:08X} is not zero-filled in the stock DOL'
            )
        patches.append(HookPatch(name, address, stock, patched))
    return patches


def patch_runtime_hooks(
    dol: bytes,
    stock_dol: bytes,
    routes: list[resources.sources.CharacterRoute],
    custom_rows: bytes,
) -> tuple[bytes, int, list[HookPatch]]:
    patches = build_hook_patches(stock_dol, routes, custom_rows)
    updated = bytearray(dol)
    changed_count = 0
    for patch in patches:
        current_offset = _vaddr_to_file(dol, patch.address, len(patch.patched))
        stock_offset = _vaddr_to_file(stock_dol, patch.address, len(patch.stock))
        current = dol[current_offset:current_offset + len(patch.patched)]
        stock = stock_dol[stock_offset:stock_offset + len(patch.stock)]
        if stock != patch.stock:
            raise RuntimeHookError(f'stock bytes changed for {patch.name}')
        if current == patch.patched:
            continue
        if current != patch.stock:
            raise RuntimeHookError(
                f'unexpected bytes in {patch.name} at 0x{patch.address:08X}'
            )
        updated[current_offset:current_offset + len(patch.patched)] = patch.patched
        changed_count += 1
    validate_runtime_hooks(bytes(updated), patches)
    return bytes(updated), changed_count, patches


def validate_runtime_hooks(dol: bytes, patches: list[HookPatch]) -> None:
    for patch in patches:
        offset = _vaddr_to_file(dol, patch.address, len(patch.patched))
        if dol[offset:offset + len(patch.patched)] != patch.patched:
            raise RuntimeHookError(f'{patch.name} is not configured')


def _prepared_bank_and_rows(
    routes: list[resources.sources.CharacterRoute],
) -> bytes:
    entries = resources.artwork.load_artwork_entries()
    bank, _, _ = resources.sources._configured_bank_from_current_output()
    if not resources.sources._is_updated(bank):
        bank = resources.sources.update_icon_source_tables(bank, routes)
    if not resources._is_updated(bank):
        bank = resources.add_icon_resource_rows(bank, routes, entries)
    resources.validate_icon_resource_rows(bank, routes, entries)
    return _build_custom_rows(bank, routes)


def install_runtime_hooks(dry_run: bool = False) -> RuntimeHookResult:
    routes = resources.sources.load_character_routes()
    custom_rows = _prepared_bank_and_rows(routes)
    try:
        with open(resources.sources.pages.cib.INPUT_DOL, 'rb') as stock_file:
            stock_dol = stock_file.read()
        target_path = (
            resources.sources.pages.cib.OUTPUT_DOL
            if os.path.exists(resources.sources.pages.cib.OUTPUT_DOL)
            else resources.sources.pages.cib.INPUT_DOL
        )
        with open(target_path, 'rb') as target_file:
            current_dol = target_file.read()
    except OSError as exc:
        raise RuntimeHookError(f'could not read DOL: {exc}') from exc

    updated_dol, changed_count, patches = patch_runtime_hooks(
        current_dol, stock_dol, routes, custom_rows
    )
    lower_size = next(len(patch.patched) for patch in patches if patch.name == 'lower stub')
    key_size = next(len(patch.patched) for patch in patches if patch.name == 'key stub')
    row_size = next(len(patch.patched) for patch in patches if patch.name == 'row stub')
    already_configured = changed_count == 0
    if dry_run or already_configured:
        return RuntimeHookResult(
            len(routes), changed_count, lower_size, key_size, row_size,
            already_configured, dry_run, False,
        )

    output_dol = resources.sources.pages.cib.OUTPUT_DOL
    os.makedirs(os.path.dirname(output_dol), exist_ok=True)
    if not os.path.exists(output_dol):
        shutil.copy2(resources.sources.pages.cib.INPUT_DOL, output_dol)
    with open(output_dol, 'r+b') as output_file:
        output_file.seek(0)
        output_file.write(updated_dol)

    report = {
        'hooks': [
            {
                'name': patch.name,
                'address': f'0x{patch.address:08X}',
                'size': len(patch.patched),
                'sha256': hashlib.sha256(patch.patched).hexdigest(),
            }
            for patch in patches
        ],
        'state': {
            'pending_id': f'0x{PENDING_ID_ADDRESS:08X}',
            'resolved_row_id': f'0x{RESOLVED_ROW_ID_ADDRESS:08X}',
            'custom_rows': f'0x{CUSTOM_ROWS_ADDRESS:08X}',
        },
        'characters': [
            {
                'name': route.name,
                'char_id': f'0x{route.char_id:02X}',
                'donor_id': f'0x{route.donor_id:02X}',
                'side_key': f'0x{route.donor_side_resource:02X}',
                'front_key': f'0x{route.donor_front_resource:02X}',
                'side_row_address': f'0x{_custom_row_address(index, False):08X}',
                'front_row_address': f'0x{_custom_row_address(index, True):08X}',
            }
            for index, route in enumerate(routes)
        ],
    }
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, 'w', encoding='utf-8') as report_file:
        json.dump(report, report_file, indent=2)
        report_file.write('\n')
    return RuntimeHookResult(
        len(routes), changed_count, lower_size, key_size, row_size,
        False, False, True,
    )


def _print_result(result: RuntimeHookResult) -> None:
    action = 'Already configured' if result.already_configured else ('Dry run' if result.dry_run else 'Written')
    print(f'{action}: donor-safe runtime hooks for {result.character_count} characters')
    print(f'  changed regions: {result.changed_region_count}')
    print(f'  lower stub:      0x{LOWER_STUB:08X} ({result.lower_stub_size} bytes)')
    print(f'  key stub:        0x{KEY_STUB:08X} ({result.key_stub_size} bytes)')
    print(f'  row stub:        0x{ROW_STUB:08X} ({result.row_stub_size} bytes)')
    print(f'  custom rows:     0x{CUSTOM_ROWS_ADDRESS:08X}-0x{CUSTOM_ROWS_ADDRESS + result.character_count * CUSTOM_ROW_STRIDE - 1:08X}')
    if result.report_written:
        print(f'  report:          {os.path.relpath(REPORT_PATH, resources.sources.pages.cib.ROOT)}')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Install donor-safe custom icon hooks and DOL resource rows.'
    )
    parser.add_argument('--dry-run', action='store_true', help='validate and generate without writing files')
    args = parser.parse_args()
    try:
        result = install_runtime_hooks(dry_run=args.dry_run)
    except (
        RuntimeHookError,
        resources.IconResourceRowError,
        resources.artwork.IconArtworkError,
        resources.sources.IconSourceTableError,
        resources.sources.pages.PrivateTexturePageError,
        resources.sources.pages.cib.IconBankCloneError,
        OSError,
    ) as exc:
        parser.exit(1, f'ERROR: {exc}\n')
    _print_result(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())