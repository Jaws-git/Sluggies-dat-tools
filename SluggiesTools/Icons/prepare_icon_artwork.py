import argparse
import hashlib
import json
import os
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

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
METADATA_DIR = os.path.join(pages.cib.ROOT, '2_Output_Models', '_ICONS', 'metadata')
LAYOUT_REPORT_PATH = os.path.join(METADATA_DIR, 'custom_icon_atlas_layout.json')
ATLAS_WIDTH = pages.TEXTURE_WIDTH
ATLAS_HEIGHT = pages.TEXTURE_HEIGHT
ICON_WIDTH = 48
ICON_HEIGHT = 51
ARTWORK_X_OFFSET = 8
SLOT_X_STRIDE = 64
INITIAL_CHARACTER_COUNT = 6
TPL_MAGIC = 0x0020AF30
FIT_MODES = ('contain', 'cover', 'strict')
DEFAULT_FIT_MODE = 'contain'


class IconArtworkError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtworkEntry:
    name: str
    char_id: int
    side_path: str
    front_path: str
    resource_x: int
    resource_y: int


@dataclass(frozen=True)
class ArtworkResult:
    character_count: int
    side_sha256: str
    front_sha256: str
    destination_offset: int
    dry_run: bool
    report_written: bool


def _parse_u8(value, field_name: str) -> int:
    try:
        parsed = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise IconArtworkError(f'invalid {field_name}: {value!r}') from exc
    if not 0 <= parsed <= 0xFF:
        raise IconArtworkError(f'{field_name} is outside u8 range: {parsed}')
    return parsed


def load_artwork_entries(description_path: str = DESCRIPTION_PATH) -> list[ArtworkEntry]:
    try:
        with open(description_path, 'r', encoding='utf-8') as description_file:
            description = json.load(description_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise IconArtworkError(f'could not read description file {description_path}: {exc}') from exc

    characters = description.get('characters')
    if not isinstance(characters, list) or len(characters) != INITIAL_CHARACTER_COUNT:
        raise IconArtworkError(
            f'initial implementation requires exactly {INITIAL_CHARACTER_COUNT} characters'
        )

    artwork_dir_value = description.get('artwork_dir')
    if not isinstance(artwork_dir_value, str) or not artwork_dir_value:
        raise IconArtworkError('artwork_dir must be a non-empty string')
    artwork_dir = os.path.normpath(
        os.path.join(os.path.dirname(description_path), artwork_dir_value)
    )

    entries = []
    seen_ids = set()
    for slot, character in enumerate(characters):
        if not isinstance(character, dict):
            raise IconArtworkError(f'character {slot} is not an object')
        name = character.get('name')
        if not isinstance(name, str) or not name:
            raise IconArtworkError(f'character {slot} has no valid name')
        char_id = _parse_u8(character.get('char_id'), f'{name}.char_id')
        if char_id in seen_ids:
            raise IconArtworkError(f'duplicate character ID 0x{char_id:02X}')
        seen_ids.add(char_id)

        image_paths = []
        for field_name in ('side_png', 'front_png'):
            filename = character.get(field_name)
            if not isinstance(filename, str) or not filename or Path(filename).name != filename:
                raise IconArtworkError(f'{name}.{field_name} must be a plain filename')
            path = os.path.join(artwork_dir, filename)
            if not os.path.isfile(path):
                raise IconArtworkError(f'missing artwork: {path}')
            image_paths.append(path)

        entries.append(
            ArtworkEntry(
                name,
                char_id,
                image_paths[0],
                image_paths[1],
                slot * SLOT_X_STRIDE,
                0,
            )
        )
    return entries


def load_and_harden_image(
    path: str,
    fit_mode: str = DEFAULT_FIT_MODE,
) -> Image.Image:
    if fit_mode not in FIT_MODES:
        raise IconArtworkError(
            f'unknown icon fit mode {fit_mode!r}; expected one of: {", ".join(FIT_MODES)}'
        )
    try:
        with Image.open(path) as source:
            image = source.convert('RGBA')
            if fit_mode == 'strict' and image.size != (ICON_WIDTH, ICON_HEIGHT):
                raise IconArtworkError(
                    f'{path} is {source.width}x{source.height}; '
                    f'expected {ICON_WIDTH}x{ICON_HEIGHT}'
                )
            if fit_mode == 'contain' and image.size != (ICON_WIDTH, ICON_HEIGHT):
                contained = ImageOps.contain(
                    image,
                    (ICON_WIDTH, ICON_HEIGHT),
                    Image.Resampling.LANCZOS,
                )
                normalized = Image.new(
                    'RGBA', (ICON_WIDTH, ICON_HEIGHT), (0, 0, 0, 0)
                )
                normalized.alpha_composite(
                    contained,
                    (
                        (ICON_WIDTH - contained.width) // 2,
                        (ICON_HEIGHT - contained.height) // 2,
                    ),
                )
                image = normalized
            elif fit_mode == 'cover' and image.size != (ICON_WIDTH, ICON_HEIGHT):
                image = ImageOps.fit(
                    image,
                    (ICON_WIDTH, ICON_HEIGHT),
                    Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
    except OSError as exc:
        raise IconArtworkError(f'could not read artwork {path}: {exc}') from exc

    pixels = []
    for red, green, blue, alpha in image.getdata():
        if alpha >= 128:
            pixels.append((red, green, blue, 255))
        else:
            pixels.append((0, 0, 0, 0))
    image.putdata(pixels)
    return image


def compose_atlases(
    entries: list[ArtworkEntry],
    fit_mode: str = DEFAULT_FIT_MODE,
) -> tuple[Image.Image, Image.Image]:
    side_atlas = Image.new('RGBA', (ATLAS_WIDTH, ATLAS_HEIGHT), (0, 0, 0, 0))
    front_atlas = Image.new('RGBA', (ATLAS_WIDTH, ATLAS_HEIGHT), (0, 0, 0, 0))
    for entry in entries:
        artwork_x = entry.resource_x + ARTWORK_X_OFFSET
        artwork_y = entry.resource_y
        if artwork_x + ICON_WIDTH > ATLAS_WIDTH or artwork_y + ICON_HEIGHT > ATLAS_HEIGHT:
            raise IconArtworkError(f'{entry.name} artwork does not fit inside atlas')
        side_atlas.alpha_composite(
            load_and_harden_image(entry.side_path, fit_mode), (artwork_x, artwork_y)
        )
        front_atlas.alpha_composite(
            load_and_harden_image(entry.front_path, fit_mode), (artwork_x, artwork_y)
        )
    return side_atlas, front_atlas


def extract_cmpr_payload(tpl_data: bytes) -> bytes:
    if len(tpl_data) < 0x20:
        raise IconArtworkError('wimgt TPL output is truncated')
    magic, image_count, table_offset = struct.unpack_from('>III', tpl_data, 0)
    if magic != TPL_MAGIC or image_count != 1:
        raise IconArtworkError(
            f'unexpected TPL header: magic=0x{magic:08X}, images={image_count}'
        )
    if table_offset + 8 > len(tpl_data):
        raise IconArtworkError('TPL image table is outside the file')
    image_header_offset, palette_header_offset = struct.unpack_from('>II', tpl_data, table_offset)
    if palette_header_offset != 0 or image_header_offset + 12 > len(tpl_data):
        raise IconArtworkError('TPL is not a palette-free single image')
    height, width, image_format, payload_offset = struct.unpack_from(
        '>HHII', tpl_data, image_header_offset
    )
    if (width, height, image_format) != (ATLAS_WIDTH, ATLAS_HEIGHT, pages.CMPR_FORMAT):
        raise IconArtworkError(
            f'unexpected encoded image: {width}x{height}, format=0x{image_format:X}'
        )
    payload_end = payload_offset + pages.CMPR_IMAGE_LENGTH
    if payload_offset < image_header_offset + 12 or payload_end > len(tpl_data):
        raise IconArtworkError('TPL CMPR payload is outside the file')
    payload = tpl_data[payload_offset:payload_end]
    if len(payload) != pages.CMPR_IMAGE_LENGTH:
        raise IconArtworkError('TPL CMPR payload has the wrong length')
    return payload


def encode_atlas_cmpr(atlas: Image.Image, work_dir: str, name: str) -> bytes:
    png_path = os.path.join(work_dir, f'{name}.png')
    tpl_path = os.path.join(work_dir, f'{name}.tpl')
    atlas.save(png_path, 'PNG')
    try:
        subprocess.run(
            ['wimgt', 'encode', '-q', '-o', '-x', 'TPL.CMPR', '-d', tpl_path, png_path],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        _slogger.error('wimgt installation not found or not available on PATH', source='icons.prepare_icon_artwork')
        raise IconArtworkError('wimgt was not found on PATH') from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or f'exit code {exc.returncode}'
        raise IconArtworkError(f'wimgt failed to encode {name}: {detail}') from exc
    try:
        with open(tpl_path, 'rb') as tpl_file:
            return extract_cmpr_payload(tpl_file.read())
    except OSError as exc:
        raise IconArtworkError(f'could not read encoded TPL {tpl_path}: {exc}') from exc


def apply_cmpr_payloads(bank: bytes, side_payload: bytes, front_payload: bytes) -> bytes:
    pages.validate_private_texture_pages(bank, require_blank=False)
    if len(side_payload) != pages.CMPR_IMAGE_LENGTH:
        raise IconArtworkError('side CMPR payload has the wrong length')
    if len(front_payload) != pages.CMPR_IMAGE_LENGTH:
        raise IconArtworkError('front CMPR payload has the wrong length')
    updated = bytearray(bank)
    updated[
        pages.SIDE_IMAGE_OFFSET:pages.SIDE_IMAGE_OFFSET + pages.CMPR_IMAGE_LENGTH
    ] = side_payload
    updated[
        pages.FRONT_IMAGE_OFFSET:pages.FRONT_IMAGE_OFFSET + pages.CMPR_IMAGE_LENGTH
    ] = front_payload
    return bytes(updated)


def _layout_report(
    entries: list[ArtworkEntry],
    side_payload: bytes,
    front_payload: bytes,
    fit_mode: str,
) -> dict:
    return {
        'atlas': {'width': ATLAS_WIDTH, 'height': ATLAS_HEIGHT, 'format': 'CMPR'},
        'icon_fit': fit_mode,
        'artwork_x_offset': ARTWORK_X_OFFSET,
        'pages': {'side': pages.SIDE_PAGE, 'front': pages.FRONT_PAGE},
        'payload_sha256': {
            'side': hashlib.sha256(side_payload).hexdigest(),
            'front': hashlib.sha256(front_payload).hexdigest(),
        },
        'characters': [
            {
                'name': entry.name,
                'char_id': f'0x{entry.char_id:02X}',
                'resource_rect': {
                    'x': entry.resource_x,
                    'y': entry.resource_y,
                    'width': ICON_WIDTH,
                    'height': ICON_HEIGHT,
                },
                'artwork_position': {
                    'x': entry.resource_x + ARTWORK_X_OFFSET,
                    'y': entry.resource_y,
                },
            }
            for entry in entries
        ],
    }


def prepare_icon_artwork(
    dry_run: bool = False,
    fit_mode: str = DEFAULT_FIT_MODE,
) -> ArtworkResult:
    entries = load_artwork_entries()
    side_atlas, front_atlas = compose_atlases(entries, fit_mode)
    with tempfile.TemporaryDirectory(prefix='sluggies_icons_') as work_dir:
        side_payload = encode_atlas_cmpr(side_atlas, work_dir, 'custom_side')
        front_payload = encode_atlas_cmpr(front_atlas, work_dir, 'custom_front')

    stock_entry = pages.cib.read_icon_entry(pages.cib.INPUT_DOL)
    pages.cib.validate_stock_bank(pages.cib.INPUT_DAT, stock_entry)
    output_entry = (
        pages.cib.read_icon_entry(pages.cib.OUTPUT_DOL)
        if os.path.exists(pages.cib.OUTPUT_DOL)
        else stock_entry
    )
    if output_entry.length == pages.cib.EXPANDED_BANK_LENGTH:
        current_bank = pages._read_bank(pages.cib.OUTPUT_DAT, output_entry)
        if pages._is_configured(current_bank):
            configured_bank = current_bank
            destination = output_entry.offset
        else:
            configured_bank = pages.add_private_texture_pages(current_bank)
            destination = output_entry.offset
    elif output_entry.offset == pages.cib.STOCK_BANK_OFFSET and output_entry.length == pages.cib.STOCK_BANK_LENGTH:
        plain_clone = pages.cib.build_expanded_clone(pages.cib.INPUT_DAT, stock_entry)
        configured_bank = pages.add_private_texture_pages(plain_clone)
        destination = pages.cib._choose_destination(pages.cib.OUTPUT_DAT)
    else:
        raise IconArtworkError('output icon entry is not stock or a supported expanded bank')

    apply_cmpr_payloads(configured_bank, side_payload, front_payload)
    if dry_run:
        return ArtworkResult(
            len(entries),
            hashlib.sha256(side_payload).hexdigest(),
            hashlib.sha256(front_payload).hexdigest(),
            destination,
            True,
            False,
        )

    pages.install_private_texture_pages()
    output_entry = pages.cib.read_icon_entry(pages.cib.OUTPUT_DOL)
    bank = pages._read_bank(pages.cib.OUTPUT_DAT, output_entry)
    updated_bank = apply_cmpr_payloads(bank, side_payload, front_payload)
    with open(pages.cib.OUTPUT_DAT, 'r+b') as dat:
        dat.seek(output_entry.offset)
        dat.write(updated_bank)

    report = _layout_report(entries, side_payload, front_payload, fit_mode)
    os.makedirs(METADATA_DIR, exist_ok=True)
    with open(LAYOUT_REPORT_PATH, 'w', encoding='utf-8') as report_file:
        json.dump(report, report_file, indent=2)
        report_file.write('\n')
    return ArtworkResult(
        len(entries),
        report['payload_sha256']['side'],
        report['payload_sha256']['front'],
        output_entry.offset,
        False,
        True,
    )


def _print_result(result: ArtworkResult) -> None:
    action = 'Dry run' if result.dry_run else 'Written'
    summary = (
        f'{action}: {result.character_count} custom character icon pairs\n'
        f'  bank:         0x{result.destination_offset:08X}\n'
        f'  side CMPR:    {result.side_sha256}\n'
        f'  front CMPR:   {result.front_sha256}'
    )
    slot_positions = ','.join(str(index * SLOT_X_STRIDE) for index in range(result.character_count))
    summary += f'\n  slot layout:  x={slot_positions}; artwork x=slot+{ARTWORK_X_OFFSET}'
    if result.report_written:
        summary += f'\n  layout report: {os.path.relpath(LAYOUT_REPORT_PATH, pages.cib.ROOT)}'
    _slogger.info(summary, source='icons.prepare_icon_artwork')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Prepare custom icon artwork and encode the private pages as GX CMPR.'
    )
    parser.add_argument('--dry-run', action='store_true', help='validate and encode without writing DAT/DOL')
    parser.add_argument(
        '--icon-fit',
        choices=FIT_MODES,
        default=DEFAULT_FIT_MODE,
        help='fit source images into 48x51 slots (default: contain)',
    )
    args = parser.parse_args()
    try:
        result = prepare_icon_artwork(dry_run=args.dry_run, fit_mode=args.icon_fit)
    except (IconArtworkError, pages.PrivateTexturePageError, pages.cib.IconBankCloneError, OSError) as exc:
        parser.exit(1, f'ERROR: {exc}\n')
    _print_result(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())