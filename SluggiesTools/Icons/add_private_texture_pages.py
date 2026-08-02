import argparse
import os
import shutil
import struct
from dataclasses import dataclass

try:
    from . import clone_icon_bank as cib
except ImportError:
    import clone_icon_bank as cib


SIDE_PAGE = 0x92
FRONT_PAGE = 0x93
PRIVATE_TEXTURE_COUNT = 0x94
DESCRIPTOR_SIZE = 0x20
DESCRIPTOR_TABLE_OFFSET = 0x24
SIDE_DESCRIPTOR_OFFSET = DESCRIPTOR_TABLE_OFFSET + SIDE_PAGE * DESCRIPTOR_SIZE
FRONT_DESCRIPTOR_OFFSET = DESCRIPTOR_TABLE_OFFSET + FRONT_PAGE * DESCRIPTOR_SIZE
DESCRIPTOR_TEMPLATE_PAGE = 0x91
SIDE_IMAGE_OFFSET = 0x93880
FRONT_IMAGE_OFFSET = 0xD3A80
CMPR_IMAGE_LENGTH = 0x20000
RELOCATED_ICON_TABLE = 0x113C80
CMPR_FORMAT = 0x0E
TEXTURE_HEIGHT = 256
TEXTURE_WIDTH = 1024
STOCK_ICON_TABLE_LENGTH = cib.STOCK_BANK_LENGTH - cib.STOCK_ICON_TABLE


class PrivateTexturePageError(RuntimeError):
    pass


@dataclass(frozen=True)
class PrivatePageResult:
    destination_offset: int
    destination_length: int
    output_dat_size: int
    upgraded_in_place: bool
    already_configured: bool
    dry_run: bool
    fst_updated: bool


def _descriptor_offset(page_id: int) -> int:
    return DESCRIPTOR_TABLE_OFFSET + page_id * DESCRIPTOR_SIZE


def _build_cmpr_descriptor(template: bytes, image_offset: int) -> bytes:
    if len(template) != DESCRIPTOR_SIZE:
        raise PrivateTexturePageError('texture descriptor template is truncated')

    descriptor = bytearray(template)
    struct.pack_into('>IIHH', descriptor, 0, image_offset, 0, TEXTURE_HEIGHT, TEXTURE_WIDTH)
    descriptor[0x17] = CMPR_FORMAT
    struct.pack_into('>H', descriptor, 0x18, 0)
    descriptor[0x1A] = 0
    return bytes(descriptor)


def add_private_texture_pages(expanded_bank: bytes) -> bytes:
    if len(expanded_bank) != cib.EXPANDED_BANK_LENGTH:
        raise PrivateTexturePageError(
            f'expanded bank length is 0x{len(expanded_bank):X}; '
            f'expected 0x{cib.EXPANDED_BANK_LENGTH:X}'
        )

    texture_section, icon_table = struct.unpack_from('>II', expanded_bank, 0)
    texture_count = struct.unpack_from('>H', expanded_bank, 0x20)[0]
    if (texture_section, icon_table, texture_count) != (
        cib.STOCK_TEXTURE_SECTION,
        cib.STOCK_ICON_TABLE,
        cib.STOCK_TEXTURE_COUNT,
    ):
        raise PrivateTexturePageError(
            'bank is not a plain Step 1 clone: '
            f'texture=0x{texture_section:X}, icon_table=0x{icon_table:X}, '
            f'texture_count=0x{texture_count:X}'
        )

    relocated_end = RELOCATED_ICON_TABLE + STOCK_ICON_TABLE_LENGTH
    if relocated_end > len(expanded_bank):
        raise PrivateTexturePageError(
            f'relocated icon table would end at 0x{relocated_end:X}, beyond bank end 0x{len(expanded_bank):X}'
        )
    if SIDE_IMAGE_OFFSET + CMPR_IMAGE_LENGTH > FRONT_IMAGE_OFFSET:
        raise PrivateTexturePageError('side and front CMPR image regions overlap')
    if FRONT_IMAGE_OFFSET + CMPR_IMAGE_LENGTH > RELOCATED_ICON_TABLE:
        raise PrivateTexturePageError('front CMPR image region overlaps relocated icon table')

    bank = bytearray(expanded_bank)
    stock_icon_table = bytes(bank[cib.STOCK_ICON_TABLE:cib.STOCK_BANK_LENGTH])
    template_offset = _descriptor_offset(DESCRIPTOR_TEMPLATE_PAGE)
    template = bytes(bank[template_offset:template_offset + DESCRIPTOR_SIZE])

    bank[cib.STOCK_ICON_TABLE:RELOCATED_ICON_TABLE] = bytes(
        RELOCATED_ICON_TABLE - cib.STOCK_ICON_TABLE
    )
    bank[RELOCATED_ICON_TABLE:relocated_end] = stock_icon_table
    struct.pack_into('>I', bank, 0x04, RELOCATED_ICON_TABLE)
    struct.pack_into('>H', bank, 0x20, PRIVATE_TEXTURE_COUNT)
    bank[SIDE_DESCRIPTOR_OFFSET:SIDE_DESCRIPTOR_OFFSET + DESCRIPTOR_SIZE] = (
        _build_cmpr_descriptor(template, SIDE_IMAGE_OFFSET)
    )
    bank[FRONT_DESCRIPTOR_OFFSET:FRONT_DESCRIPTOR_OFFSET + DESCRIPTOR_SIZE] = (
        _build_cmpr_descriptor(template, FRONT_IMAGE_OFFSET)
    )

    validate_private_texture_pages(bytes(bank), stock_icon_table)
    return bytes(bank)


def validate_private_texture_pages(
    bank: bytes,
    expected_icon_table: bytes | None = None,
    require_blank: bool = True,
) -> None:
    if len(bank) != cib.EXPANDED_BANK_LENGTH:
        raise PrivateTexturePageError('configured bank has the wrong length')
    texture_section, icon_table = struct.unpack_from('>II', bank, 0)
    texture_count = struct.unpack_from('>H', bank, 0x20)[0]
    if (texture_section, icon_table, texture_count) != (
        cib.STOCK_TEXTURE_SECTION,
        RELOCATED_ICON_TABLE,
        PRIVATE_TEXTURE_COUNT,
    ):
        raise PrivateTexturePageError('configured bank header does not match Step 2')

    for descriptor_offset, image_offset in (
        (SIDE_DESCRIPTOR_OFFSET, SIDE_IMAGE_OFFSET),
        (FRONT_DESCRIPTOR_OFFSET, FRONT_IMAGE_OFFSET),
    ):
        descriptor = bank[descriptor_offset:descriptor_offset + DESCRIPTOR_SIZE]
        data_ptr, palette_ptr, height, width = struct.unpack_from('>IIHH', descriptor)
        palette_count = struct.unpack_from('>H', descriptor, 0x18)[0]
        if (data_ptr, palette_ptr, height, width, descriptor[0x17], palette_count, descriptor[0x1A]) != (
            image_offset,
            0,
            TEXTURE_HEIGHT,
            TEXTURE_WIDTH,
            CMPR_FORMAT,
            0,
            0,
        ):
            raise PrivateTexturePageError(
                f'invalid private descriptor at 0x{descriptor_offset:X}'
            )

    for image_offset in (SIDE_IMAGE_OFFSET, FRONT_IMAGE_OFFSET):
        if require_blank and any(bank[image_offset:image_offset + CMPR_IMAGE_LENGTH]):
            raise PrivateTexturePageError(f'private image region at 0x{image_offset:X} is not blank')

    relocated = bank[RELOCATED_ICON_TABLE:RELOCATED_ICON_TABLE + STOCK_ICON_TABLE_LENGTH]
    if expected_icon_table is not None and relocated != expected_icon_table:
        raise PrivateTexturePageError('relocated icon table does not match the stock table')


def _read_bank(dat_path: str, entry: cib.IconEntry) -> bytes:
    with open(dat_path, 'rb') as dat:
        dat.seek(entry.offset)
        bank = dat.read(entry.length)
    if len(bank) != entry.length:
        raise PrivateTexturePageError('icon bank is truncated')
    return bank


def _is_configured(bank: bytes) -> bool:
    if len(bank) != cib.EXPANDED_BANK_LENGTH:
        return False
    return (
        struct.unpack_from('>I', bank, 0x04)[0] == RELOCATED_ICON_TABLE
        and struct.unpack_from('>H', bank, 0x20)[0] == PRIVATE_TEXTURE_COUNT
    )


def install_private_texture_pages(dry_run: bool = False) -> PrivatePageResult:
    stock_entry = cib.read_icon_entry(cib.INPUT_DOL)
    cib.validate_stock_bank(cib.INPUT_DAT, stock_entry)
    output_entry = cib.read_icon_entry(cib.OUTPUT_DOL) if os.path.exists(cib.OUTPUT_DOL) else stock_entry

    upgraded_in_place = output_entry.length == cib.EXPANDED_BANK_LENGTH
    if upgraded_in_place:
        if not os.path.exists(cib.OUTPUT_DAT):
            raise PrivateTexturePageError('output DOL points to an expanded bank but output DAT is missing')
        existing_bank = _read_bank(cib.OUTPUT_DAT, output_entry)
        if _is_configured(existing_bank):
            validate_private_texture_pages(existing_bank, require_blank=False)
            return PrivatePageResult(
                output_entry.offset,
                output_entry.length,
                os.path.getsize(cib.OUTPUT_DAT),
                True,
                True,
                dry_run,
                False,
            )
        configured_bank = add_private_texture_pages(existing_bank)
        destination = output_entry.offset
    elif output_entry.offset == cib.STOCK_BANK_OFFSET and output_entry.length == cib.STOCK_BANK_LENGTH:
        plain_clone = cib.build_expanded_clone(cib.INPUT_DAT, stock_entry)
        configured_bank = add_private_texture_pages(plain_clone)
        destination = cib._choose_destination(cib.OUTPUT_DAT)
        upgraded_in_place = False
    else:
        raise PrivateTexturePageError(
            f'output icon entry has unsupported offset/length: '
            f'0x{output_entry.offset:X}/0x{output_entry.length:X}'
        )

    current_size = (
        os.path.getsize(cib.OUTPUT_DAT)
        if os.path.exists(cib.OUTPUT_DAT)
        else os.path.getsize(cib.INPUT_DAT)
    )
    projected_size = max(
        current_size,
        destination + len(configured_bank) + cib.hh.HS_BUFFER_BYTES,
    )
    if dry_run:
        return PrivatePageResult(
            destination,
            len(configured_bank),
            projected_size,
            upgraded_in_place,
            False,
            True,
            False,
        )

    if upgraded_in_place:
        with open(cib.OUTPUT_DAT, 'r+b') as dat:
            dat.seek(destination)
            dat.write(configured_bank)
    else:
        cib.hh.writeModelBlock(configured_bank, destination)
        if not os.path.exists(cib.OUTPUT_DOL):
            os.makedirs(os.path.dirname(cib.OUTPUT_DOL), exist_ok=True)
            shutil.copy2(cib.INPUT_DOL, cib.OUTPUT_DOL)
        cib.patch_direct_icon_entry(cib.OUTPUT_DOL, destination, len(configured_bank))

    output_size = os.path.getsize(cib.OUTPUT_DAT)
    fst_updated = cib.hh.patchFstFileSize(output_size)
    return PrivatePageResult(
        destination,
        len(configured_bank),
        output_size,
        upgraded_in_place,
        False,
        False,
        fst_updated,
    )


def _print_result(result: PrivatePageResult) -> None:
    if result.already_configured:
        action = 'Already configured'
    elif result.dry_run:
        action = 'Dry run (in-place upgrade)' if result.upgraded_in_place else 'Dry run (new clone)'
    else:
        action = 'Upgraded in place' if result.upgraded_in_place else 'Created configured clone'
    print(f'{action}: private CMPR icon pages')
    print(f'  bank:       0x{result.destination_offset:08X} (0x{result.destination_length:X} bytes)')
    print(f'  side page:  0x{SIDE_PAGE:02X}, image 0x{SIDE_IMAGE_OFFSET:X}')
    print(f'  front page: 0x{FRONT_PAGE:02X}, image 0x{FRONT_IMAGE_OFFSET:X}')
    print(f'  icon table: 0x{RELOCATED_ICON_TABLE:X}')
    print(f'  output DAT: 0x{result.output_dat_size:X} bytes')
    if not result.dry_run and not result.already_configured and not result.fst_updated:
        print('  FST:        not updated (1_Input/fst.bin is unavailable)')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Add two blank private CMPR pages to the expanded icon bank.'
    )
    parser.add_argument('--dry-run', action='store_true', help='validate and report without writing files')
    args = parser.parse_args()
    try:
        result = install_private_texture_pages(dry_run=args.dry_run)
    except (cib.IconBankCloneError, PrivateTexturePageError, OSError) as exc:
        parser.exit(1, f'ERROR: {exc}\n')
    _print_result(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())