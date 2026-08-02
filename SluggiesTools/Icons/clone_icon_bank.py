import argparse
import os
import shutil
import struct
import sys
from dataclasses import dataclass

ICONS_DIR = os.path.dirname(__file__)
TOOLS_DIR = os.path.normpath(os.path.join(ICONS_DIR, '..'))
ROOT = os.path.normpath(os.path.join(TOOLS_DIR, '..'))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from Hammerspace import HammerspaceHelper as hh


ICON_ENTRY_DOL_OFFSET = 0x68DE88
ICON_ENTRY_SIZE = 0x30
STOCK_BANK_OFFSET = 0x167E7420
STOCK_BANK_LENGTH = 0x985F0
EXPANDED_BANK_LENGTH = 0x118CE0
STOCK_TEXTURE_SECTION = 0x20
STOCK_ICON_TABLE = 0x93680
STOCK_TEXTURE_COUNT = 0x92
ALIGNMENT = 0x20

INPUT_DAT = os.path.join(ROOT, '1_Input', 'dt_na.dat')
INPUT_DOL = os.path.join(ROOT, '1_Input', 'main.dol')
OUTPUT_DAT = os.path.join(ROOT, '3_Output_Dat', 'dt_na.dat')
OUTPUT_DOL = os.path.join(ROOT, '3_Output_Dat', 'main.dol')


class IconBankCloneError(RuntimeError):
    pass


@dataclass(frozen=True)
class IconEntry:
    filename_pointers: tuple[int, int, int]
    offset: int
    length: int
    allocation: int


@dataclass(frozen=True)
class CloneResult:
    source_offset: int
    source_length: int
    destination_offset: int
    destination_length: int
    output_dat_size: int
    already_expanded: bool
    dry_run: bool
    fst_updated: bool


def _align_up(value: int, alignment: int = ALIGNMENT) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def read_icon_entry(dol_path: str) -> IconEntry:
    try:
        with open(dol_path, 'rb') as dol:
            dol.seek(ICON_ENTRY_DOL_OFFSET)
            raw = dol.read(ICON_ENTRY_SIZE)
    except OSError as exc:
        raise IconBankCloneError(f'could not read DOL: {dol_path}: {exc}') from exc

    if len(raw) != ICON_ENTRY_SIZE:
        raise IconBankCloneError(
            f'DOL is too short for icon entry at 0x{ICON_ENTRY_DOL_OFFSET:X}: {dol_path}'
        )

    words = struct.unpack('>12I', raw)
    filename_pointers = (words[0], words[4], words[8])
    lengths = (words[1], words[5], words[9])
    offsets = (words[2], words[6], words[10])
    allocations = (words[3], words[7], words[11])
    if len(set(lengths)) != 1 or len(set(offsets)) != 1 or len(set(allocations)) != 1:
        raise IconBankCloneError(
            'icon entry EN/SP/FR fields disagree: '
            f'offsets={offsets}, lengths={lengths}, allocations={allocations}'
        )

    return IconEntry(filename_pointers, offsets[0], lengths[0], allocations[0])


def validate_stock_bank(dat_path: str, entry: IconEntry) -> None:
    if entry.offset != STOCK_BANK_OFFSET or entry.length != STOCK_BANK_LENGTH:
        raise IconBankCloneError(
            'unsupported stock icon entry: '
            f'offset=0x{entry.offset:X}, length=0x{entry.length:X}; '
            f'expected offset=0x{STOCK_BANK_OFFSET:X}, length=0x{STOCK_BANK_LENGTH:X}'
        )
    if entry.allocation != entry.length:
        raise IconBankCloneError(
            f'stock icon allocation 0x{entry.allocation:X} does not match length 0x{entry.length:X}'
        )

    try:
        dat_size = os.path.getsize(dat_path)
        with open(dat_path, 'rb') as dat:
            dat.seek(entry.offset)
            header = dat.read(0x24)
    except OSError as exc:
        raise IconBankCloneError(f'could not read DAT: {dat_path}: {exc}') from exc

    if entry.offset + entry.length > dat_size:
        raise IconBankCloneError(
            f'stock icon bank ends at 0x{entry.offset + entry.length:X}, '
            f'beyond DAT size 0x{dat_size:X}'
        )
    if len(header) != 0x24:
        raise IconBankCloneError('stock icon bank header is truncated')

    texture_section, icon_table = struct.unpack_from('>II', header)
    texture_count = struct.unpack_from('>H', header, 0x20)[0]
    if (texture_section, icon_table, texture_count) != (
        STOCK_TEXTURE_SECTION,
        STOCK_ICON_TABLE,
        STOCK_TEXTURE_COUNT,
    ):
        raise IconBankCloneError(
            'unexpected stock icon bank header: '
            f'texture=0x{texture_section:X}, icon_table=0x{icon_table:X}, '
            f'texture_count=0x{texture_count:X}'
        )


def build_expanded_clone(dat_path: str, entry: IconEntry) -> bytes:
    validate_stock_bank(dat_path, entry)
    with open(dat_path, 'rb') as dat:
        dat.seek(entry.offset)
        stock = dat.read(entry.length)
    if len(stock) != entry.length:
        raise IconBankCloneError(
            f'expected 0x{entry.length:X} stock bytes, read 0x{len(stock):X}'
        )
    return stock + bytes(EXPANDED_BANK_LENGTH - len(stock))


def patch_direct_icon_entry(dol_path: str, destination_offset: int, destination_length: int) -> None:
    if destination_offset % ALIGNMENT:
        raise IconBankCloneError(
            f'icon bank destination 0x{destination_offset:X} is not {ALIGNMENT}-byte aligned'
        )

    entry = read_icon_entry(dol_path)
    words = []
    for filename_pointer in entry.filename_pointers:
        words.extend((filename_pointer, destination_length, destination_offset, destination_length))

    with open(dol_path, 'r+b') as dol:
        dol.seek(ICON_ENTRY_DOL_OFFSET)
        dol.write(struct.pack('>12I', *words))


def _existing_expansion(output_dol: str, output_dat: str) -> CloneResult | None:
    if not os.path.exists(output_dol) or not os.path.exists(output_dat):
        return None

    entry = read_icon_entry(output_dol)
    if entry.offset == STOCK_BANK_OFFSET and entry.length == STOCK_BANK_LENGTH:
        return None
    if entry.length != EXPANDED_BANK_LENGTH or entry.allocation != EXPANDED_BANK_LENGTH:
        raise IconBankCloneError(
            'output DOL icon entry is neither stock nor this Step 1 expansion: '
            f'offset=0x{entry.offset:X}, length=0x{entry.length:X}, allocation=0x{entry.allocation:X}'
        )
    if entry.offset % ALIGNMENT:
        raise IconBankCloneError(f'existing expanded icon offset 0x{entry.offset:X} is unaligned')

    dat_size = os.path.getsize(output_dat)
    if entry.offset + entry.length > dat_size:
        raise IconBankCloneError('existing expanded icon entry extends beyond output DAT')

    return CloneResult(
        STOCK_BANK_OFFSET,
        STOCK_BANK_LENGTH,
        entry.offset,
        entry.length,
        dat_size,
        True,
        False,
        False,
    )


def _choose_destination(output_dat: str) -> int:
    if os.path.exists(output_dat) and os.path.getsize(output_dat) > hh.BASE_SIZE:
        destination = hh.findFreeMemoryChunk(EXPANDED_BANK_LENGTH + hh.HS_BUFFER_BYTES)
        if destination >= 0:
            return destination

    current_size = os.path.getsize(output_dat) if os.path.exists(output_dat) else os.path.getsize(INPUT_DAT)
    return _align_up(current_size)


def clone_icon_bank(dry_run: bool = False) -> CloneResult:
    stock_entry = read_icon_entry(INPUT_DOL)
    validate_stock_bank(INPUT_DAT, stock_entry)

    existing = _existing_expansion(OUTPUT_DOL, OUTPUT_DAT)
    if existing is not None:
        return CloneResult(**{**existing.__dict__, 'dry_run': dry_run})

    destination = _choose_destination(OUTPUT_DAT)
    projected_size = max(
        os.path.getsize(OUTPUT_DAT) if os.path.exists(OUTPUT_DAT) else os.path.getsize(INPUT_DAT),
        destination + EXPANDED_BANK_LENGTH + hh.HS_BUFFER_BYTES,
    )
    if dry_run:
        return CloneResult(
            stock_entry.offset,
            stock_entry.length,
            destination,
            EXPANDED_BANK_LENGTH,
            projected_size,
            False,
            True,
            False,
        )

    block = build_expanded_clone(INPUT_DAT, stock_entry)
    if not os.path.exists(OUTPUT_DOL):
        os.makedirs(os.path.dirname(OUTPUT_DOL), exist_ok=True)
        shutil.copy2(INPUT_DOL, OUTPUT_DOL)

    hh.writeModelBlock(block, destination)
    patch_direct_icon_entry(OUTPUT_DOL, destination, len(block))
    output_size = os.path.getsize(OUTPUT_DAT)
    fst_updated = hh.patchFstFileSize(output_size)

    return CloneResult(
        stock_entry.offset,
        stock_entry.length,
        destination,
        len(block),
        output_size,
        False,
        False,
        fst_updated,
    )


def _print_result(result: CloneResult) -> None:
    action = 'Already expanded' if result.already_expanded else ('Dry run' if result.dry_run else 'Expanded')
    print(f'{action}: icon bank group 119 entry 2')
    print(f'  source:      0x{result.source_offset:08X} (0x{result.source_length:X} bytes)')
    print(f'  destination: 0x{result.destination_offset:08X} (0x{result.destination_length:X} bytes)')
    print(f'  output DAT:  0x{result.output_dat_size:X} bytes')
    if not result.dry_run and not result.already_expanded and not result.fst_updated:
        print('  FST:         not updated (1_Input/fst.bin is unavailable)')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Clone and reserve the six-character expanded icon bank in hammerspace.'
    )
    parser.add_argument('--dry-run', action='store_true', help='validate and report without writing files')
    args = parser.parse_args()

    try:
        result = clone_icon_bank(dry_run=args.dry_run)
    except IconBankCloneError as exc:
        parser.exit(1, f'ERROR: {exc}\n')
    _print_result(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())