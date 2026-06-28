import argparse
import csv
import hashlib
import json
import os
import shutil
import sys

from PIL import Image

TOOLS_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.normpath(os.path.join(TOOLS_DIR, '..'))

DEFAULT_ICONS_ROOT = os.path.join(ROOT_DIR, '2_Output_Models', '_ICONS')
INPUT_DAT = os.path.join(ROOT_DIR, '1_Input', 'dt_na.dat')
OUTPUT_DIR = os.path.join(ROOT_DIR, '3_Output_Dat')
OUTPUT_DAT = os.path.join(OUTPUT_DIR, 'dt_na.dat')

EXPECTED_WIDTH = 1024
EXPECTED_HEIGHT = 256
EXPECTED_FORMAT = 0x09
EXPECTED_PALETTE_FORMAT = 0x02
EXPECTED_PALETTE_ENTRIES = 256
EXPECTED_IMAGE_LEN = 0x40000
EXPECTED_PALETTE_LEN = 0x200



class IconPatchError(Exception):
    pass


def _normalize_rel(path):
    return path.replace('/', os.sep).replace('\\', os.sep)


def _parse_hex_int(value, field_name):
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        raise IconPatchError(f'invalid {field_name}: expected int/hex string, got {type(value).__name__}')
    v = value.strip()
    if v.lower().startswith('0x'):
        return int(v, 16)
    return int(v)


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _resolve_paths(source):
    if not source:
        source = DEFAULT_ICONS_ROOT

    source = os.path.normpath(source)

    if os.path.isfile(source):
        if os.path.basename(source).lower() != 'manifest.json':
            raise IconPatchError('if SOURCE is a file, it must be manifest.json')
        manifest_path = source
        metadata_dir = os.path.dirname(manifest_path)
        icons_root = os.path.dirname(metadata_dir)
    elif os.path.isdir(source):
        maybe_manifest = os.path.join(source, 'manifest.json')
        maybe_metadata_manifest = os.path.join(source, 'metadata', 'manifest.json')

        if os.path.exists(maybe_manifest):
            metadata_dir = source
            manifest_path = maybe_manifest
            icons_root = os.path.dirname(metadata_dir)
        elif os.path.exists(maybe_metadata_manifest):
            icons_root = source
            metadata_dir = os.path.join(source, 'metadata')
            manifest_path = maybe_metadata_manifest
        else:
            raise IconPatchError(
                'could not find manifest.json. Expected one of:\n'
                f'  {maybe_manifest}\n'
                f'  {maybe_metadata_manifest}'
            )
    else:
        raise IconPatchError(f'SOURCE does not exist: {source}')

    pages_csv = os.path.join(metadata_dir, 'pages.csv')
    if not os.path.exists(pages_csv):
        raise IconPatchError(f'missing pages.csv: {pages_csv}')

    return {
        'icons_root': icons_root,
        'metadata_dir': metadata_dir,
        'manifest_path': manifest_path,
        'pages_csv': pages_csv,
    }


def _load_pages(pages_csv):
    with open(pages_csv, 'r', encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise IconPatchError('pages.csv has no rows')

    required = [
        'view',
        'texture_index_dec',
        'sheet_png',
        'dt_na_image_offset',
        'dt_na_palette_offset',
    ]
    missing = [k for k in required if k not in rows[0]]
    if missing:
        raise IconPatchError(f'pages.csv missing required columns: {missing}')

    pages = []
    for row in rows:
        pages.append(
            {
                'view': row['view'],
                'texture_index_dec': int(row['texture_index_dec']),
                'texture_index_hex': row.get('texture_index_hex', ''),
                'sheet_png': row['sheet_png'],
                'dt_na_image_offset': _parse_hex_int(row['dt_na_image_offset'], 'dt_na_image_offset'),
                'dt_na_palette_offset': _parse_hex_int(row['dt_na_palette_offset'], 'dt_na_palette_offset'),
                'raw_palette_file': row.get('raw_palette_file', ''),
            }
        )

    return pages


def _ensure_output_dat():
    if not os.path.exists(INPUT_DAT):
        raise IconPatchError(f'missing input DAT: {INPUT_DAT}')

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if not os.path.exists(OUTPUT_DAT):
        shutil.copy2(INPUT_DAT, OUTPUT_DAT)


def _read_grayscale_png_indexed_bytes(png_path):
    """
    Read a direct-indexed grayscale PNG and re-tile to GameCube C8 format.

    Each pixel value (0-255) in the PNG is directly a C8 palette index —
    no palette lookup, no wimgt, no quantization.  This is the exact inverse
    of the export de-tiling step.
    """
    img = Image.open(png_path)

    if img.mode not in ('P', 'L'):
        raise IconPatchError(
            f'BASE.png must be an indexed-colour (P) or grayscale (L) image; '
            f'got mode "{img.mode}" for {png_path}'
        )

    if img.size != (EXPECTED_WIDTH, EXPECTED_HEIGHT):
        raise IconPatchError(
            f'grayscale PNG dimensions mismatch: {img.size} expected {(EXPECTED_WIDTH, EXPECTED_HEIGHT)}'
        )

    pixels = img.tobytes()

    # Re-tile: linear (row-major) order → CI8 8x4 block tiling
    tiled = bytearray(EXPECTED_IMAGE_LEN)
    dst = 0
    for tile_y in range(EXPECTED_HEIGHT // 4):
        for tile_x in range(EXPECTED_WIDTH // 8):
            for block_row in range(4):
                for block_col in range(8):
                    src_x = tile_x * 8 + block_col
                    src_y = tile_y * 4 + block_row
                    tiled[dst] = pixels[src_y * EXPECTED_WIDTH + src_x]
                    dst += 1

    return bytes(tiled)


def _act_to_rgb5a3_using_raw_modes(icons_root, raw_palette_rel, act_rgb8):
    """
    Convert ACT RGB8 palette to RGB5A3, using the original raw palette binary
    to determine the encoding mode (RGB555 vs RGB4A3) and alpha per entry.

    For every entry, regardless of whether the user edited it:
    - RGB555 entries (original bit15=1): encode ACT colour as RGB555, alpha=full.
    - RGB4A3 entries (original bit15=0): encode ACT colour as RGB4A3,
      preserving the original 3-bit alpha value from the raw binary.

    Falls back to pure RGB555 encoding if the raw file is missing or invalid.
    """
    raw_path = ''
    if raw_palette_rel:
        raw_path = os.path.join(icons_root, _normalize_rel(raw_palette_rel))

    if not raw_path or not os.path.exists(raw_path):
        raise IconPatchError(
            f'raw palette binary not found for ACT conversion: "{raw_path}". '
            'Re-run --export-icons to regenerate the raw files.'
        )

    with open(raw_path, 'rb') as f:
        raw_bytes = f.read()

    if len(raw_bytes) != EXPECTED_PALETTE_LEN:
        raise IconPatchError(
            f'raw palette binary has unexpected size {len(raw_bytes)} '
            f'(expected {EXPECTED_PALETTE_LEN}): {raw_path}'
        )

    result = bytearray()
    for i in range(256):
        raw_word = int.from_bytes(raw_bytes[i*2 : i*2+2], 'big')
        r8 = act_rgb8[i*3]
        g8 = act_rgb8[i*3 + 1]
        b8 = act_rgb8[i*3 + 2]

        if raw_word & 0x8000:
            # Original was RGB555 — encode new colour as RGB555, full opacity.
            r5 = r8 >> 3
            g5 = g8 >> 3
            b5 = b8 >> 3
            word = (1 << 15) | (r5 << 10) | (g5 << 5) | b5
        else:
            # Original was RGB4A3 — encode new colour as RGB4A3,
            # preserving the original alpha value.
            orig_alpha3 = (raw_word >> 12) & 0x07
            r4 = r8 >> 4
            g4 = g8 >> 4
            b4 = b8 >> 4
            word = (orig_alpha3 << 12) | (r4 << 8) | (g4 << 4) | b4

        result.extend(word.to_bytes(2, 'big'))

    return bytes(result)


def _read_act_file(act_path):
    """
    Read an Adobe Color Table (ACT) file.
    
    ACT format:
    - 768 bytes: 256 RGB entries (3 bytes each)
    - Optional 2 bytes: number of colors
    - Optional 2 bytes: transparent index
    """
    if not os.path.exists(act_path):
        raise IconPatchError(f'ACT file not found: {act_path}')
    
    with open(act_path, 'rb') as f:
        data = f.read()
    
    if len(data) < 768:
        raise IconPatchError(
            f'ACT file too small: {act_path} (expected at least 768 bytes, got {len(data)})'
        )
    
    # Extract the first 768 bytes (256 RGB entries)
    rgb8_data = data[:768]
    return rgb8_data


def _find_act_file(sheets_dir, view, texture_index_dec):
    """
    Find an ACT file for a given page by matching the index prefix.
    Matches any file named {view}_page_{HEX}_t{DEC}[_anything].act.
    """
    prefix = f'{view}_page_{texture_index_dec:02X}_t{texture_index_dec:03d}'
    for filename in os.listdir(sheets_dir):
        if filename.startswith(prefix) and filename.endswith('.act'):
            return os.path.join(sheets_dir, filename)
    return None


def _collect_payloads(icons_root, pages):
    """
    Read BASE.png (shared indexed image) and per-page ACT palette files.

    All side pages share one BASE.png; all front pages share another.
    Each page has its own ACT file for its palette.
    """
    records = []

    pages_by_view = {}
    for page in pages:
        pages_by_view.setdefault(page['view'], []).append(page)

    for view, view_pages in pages_by_view.items():
        sheets_dir = os.path.join(icons_root, 'sheets (EDIT BASE.PNG)', view)
        base_png_path = os.path.join(sheets_dir, 'BASE.png')

        if not os.path.exists(base_png_path):
            raise IconPatchError(
                f'BASE.png not found for view "{view}": {base_png_path}'
            )

        base_image_data = _read_grayscale_png_indexed_bytes(base_png_path)

        for page in view_pages:
            act_path = _find_act_file(sheets_dir, view, page['texture_index_dec'])

            if not act_path:
                raise IconPatchError(
                    f'ACT palette file not found for page {page["texture_index_dec"]} ({view}): '
                    f'expected e.g. {view}_page_{page["texture_index_dec"]:02X}_t{page["texture_index_dec"]:03d}[_CharName].act'
                )

            rgb8_palette = _read_act_file(act_path)
            rgb5a3_palette = _act_to_rgb5a3_using_raw_modes(
                icons_root, page.get('raw_palette_file', ''), rgb8_palette
            )

            records.append({
                **page,
                'base_png': base_png_path,
                'act_path': act_path,
                'image_data': base_image_data,
                'palette_data': rgb5a3_palette,
                'image_sha256': _sha256_bytes(base_image_data),
                'palette_sha256': _sha256_bytes(rgb5a3_palette),
            })

    return records


def _plan_image_writes(records):
    """
    Deduplicate image writes by DAT offset.
    With the BASE.png workflow all pages of the same view always share one
    image, so this produces exactly one write per view (two total).
    """
    seen = {}
    writes = []
    for rec in records:
        offset = rec['dt_na_image_offset']
        if offset not in seen:
            seen[offset] = True
            writes.append({
                'dt_na_image_offset': offset,
                'image_data': rec['image_data'],
                'image_sha256': rec['image_sha256'],
                'member_texture_indices': sorted(
                    r['texture_index_dec'] for r in records
                    if r['dt_na_image_offset'] == offset
                ),
            })
    return sorted(writes, key=lambda w: w['dt_na_image_offset'])


def _write_dat(output_dat, image_writes, records):
    with open(output_dat, 'r+b') as f:
        f.seek(0, os.SEEK_END)
        dat_size = f.tell()

        for img in image_writes:
            offset = img['dt_na_image_offset']
            payload = img['image_data']
            if offset < 0 or offset + len(payload) > dat_size:
                raise IconPatchError(
                    f'image write out of bounds at 0x{offset:X} len 0x{len(payload):X} '
                    f'(dt_na.dat size 0x{dat_size:X})'
                )
            f.seek(offset)
            f.write(payload)

        for rec in records:
            offset = rec['dt_na_palette_offset']
            payload = rec['palette_data']
            if offset < 0 or offset + len(payload) > dat_size:
                raise IconPatchError(
                    f'palette write out of bounds at 0x{offset:X} len 0x{len(payload):X} '
                    f'(dt_na.dat size 0x{dat_size:X})'
                )
            f.seek(offset)
            f.write(payload)


def _build_report(source_info, records, image_writes, dry_run):
    return {
        'source': {
            'icons_root': source_info['icons_root'],
            'metadata_dir': source_info['metadata_dir'],
            'manifest_path': source_info['manifest_path'],
            'pages_csv': source_info['pages_csv'],
            'input_dat': INPUT_DAT,
            'output_dat': OUTPUT_DAT,
        },
        'mode': {
            'dry_run': dry_run,
        },
        'counts': {
            'pages_processed': len(records),
            'palette_writes': len(records),
            'image_writes': len(image_writes),
        },
        'image_writes': [
            {
                'dt_na_image_offset': f"0x{w['dt_na_image_offset']:X}",
                'member_texture_indices': w['member_texture_indices'],
                'image_sha256': w['image_sha256'],
            }
            for w in image_writes
        ],
        'palette_writes': [
            {
                'texture_index_dec': r['texture_index_dec'],
                'texture_index_hex': r['texture_index_hex'],
                'view': r['view'],
                'dt_na_palette_offset': f"0x{r['dt_na_palette_offset']:X}",
                'palette_sha256': r['palette_sha256'],
                'act_path': r.get('act_path', ''),
            }
            for r in sorted(records, key=lambda x: x['texture_index_dec'])
        ],
    }


def _write_report(metadata_dir, report):
    out_path = os.path.join(metadata_dir, 'reimport_report.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Reimport character-select icon sheets and patch dt_na.dat in place.'
    )
    parser.add_argument(
        'source',
        nargs='?',
        default=None,
        help=(
            'Path to icon root, metadata directory, or manifest.json. '
            'Default: 2_Output_Models/_ICONS'
        ),
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Validate and build report without writing dt_na.dat.',
    )

    args = parser.parse_args(argv)

    source_info = _resolve_paths(args.source)
    pages = _load_pages(source_info['pages_csv'])
    records = _collect_payloads(source_info['icons_root'], pages)
    image_writes = _plan_image_writes(records)

    if not args.dry_run:
        _ensure_output_dat()
        _write_dat(OUTPUT_DAT, image_writes, records)

    report = _build_report(
        source_info=source_info,
        records=records,
        image_writes=image_writes,
        dry_run=args.dry_run,
    )
    report_path = _write_report(source_info['metadata_dir'], report)

    label = 'Dry run complete.' if args.dry_run else 'Reimport complete.'
    print(f'Icon {label}')
    print(f"  Pages processed: {report['counts']['pages_processed']}")
    print(f"  Palette writes: {report['counts']['palette_writes']}")
    print(f"  Image writes: {report['counts']['image_writes']}")
    print(f"  Report: {report_path}")
    if args.dry_run:
        print('  No bytes were written.')
    else:
        print(f'  Output DAT: {OUTPUT_DAT}')


if __name__ == '__main__':
    try:
        main()
    except IconPatchError as exc:
        print(f'ERROR: {exc}')
        raise SystemExit(1)
