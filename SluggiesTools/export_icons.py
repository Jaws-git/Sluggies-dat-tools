import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
from datetime import datetime, timezone

from PIL import Image

from base import File
from helper import bti, itb
from tpl import TEXPalette

# An array of FILE_POINTER[]'s in the US DOL.
DIRS_START = 0x69C828
DIRS_END = 0x69CAD8
DIR_PTR_PTRS = range(DIRS_START, DIRS_END, 4)
DAT_FNAME_PTR = 0x8067F658

ICON_DIR_INDEX = 119
ICON_FILE_INDEX = 2
ICON_TEX_LOCAL_OFFSET = 0x20

SIDE_START = 0x49
SIDE_END = 0x4E
FRONT_START = 0x4F
FRONT_END = 0x8B

EXPECTED_WIDTH = 1024
EXPECTED_HEIGHT = 256
EXPECTED_FORMAT = 0x09
EXPECTED_PALETTE_FORMAT = 0x02
EXPECTED_PALETTE_ENTRIES = 256
EXPECTED_IMAGE_LEN = 0x40000
EXPECTED_PALETTE_LEN = 0x200

GRID_COLUMNS = 21
GRID_X_STRIDE = 49
GRID_Y_STRIDE = 52
GRID_X_PAD = 1
GRID_Y_PAD = 1
GRID_CELL_WIDTH = 48
GRID_CELL_HEIGHT = 51

TOOLS_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.normpath(os.path.join(TOOLS_DIR, '..'))
INPUT_DOL = os.path.join(ROOT_DIR, '1_Input', 'main.dol')
INPUT_DAT = os.path.join(ROOT_DIR, '1_Input', 'dt_na.dat')
OUTPUT_ROOT = os.path.join(ROOT_DIR, '2_Output_Models', '_ICONS')

DIR_SHEETS = 'sheets (EDIT BASE.PNG)'
DIR_KNOWN_FALLBACK = 'known_fallback'
DIR_RAW = 'raw'
DIR_METADATA = 'metadata'

DIR_NAMES = {
    "4F": "Mario",
    "5A": "Waluigi",
    "5B": "Koopa",
    "5C": "ToadRed",
    "5D": "Boo",
    "5E": "Toadette",
    "5F": "ShyGuyRed",
    "6A": "HammerBro",
    "6B": "Toadsworth",
    "6C": "ToadBlue",
    "6D": "ToadYellow",
    "6E": "ToadGreen",
    "6F": "ToadPurple",
    "7A": "ShyGuyYellow",
    "7B": "ShyGuyGreen",
    "7C": "ShyGuyBlack",
    "7D": "DryBones",
    "7E": "DryBonesDark",
    "7F": "Wiggler",
    "8A": "BabyDK",
    "8B": "YoshiRed",
    "50": "Luigi",
    "51": "DK",
    "52": "DKJunior",
    "53": "Peach",
    "54": "Daisy",
    "55": "YoshiGreen",
    "56": "BabyMario",
    "57": "BabyLuigi",
    "58": "Bowser",
    "59": "Wario",
    "60": "Birdo",
    "61": "Monty",
    "62": "BowserJR",
    "63": "Parakoopa",
    "64": "PiantaBlue",
    "65": "PiantaRed",
    "66": "PiantaYellow",
    "67": "NokiBlue",
    "68": "NokiRed",
    "69": "NokiGreen",
    "70": "MagikoopaBlue",
    "71": "MagikoopaRed",
    "72": "MagikoopaGreen",
    "73": "MagikoopaRYellow",
    "74": "KingBoo",
    "75": "PeteyPiranha",
    "76": "DixieKong",
    "77": "Goomba",
    "78": "ParaGoomba",
    "79": "ShyGuyBlue",
    "80": "Blooper",
    "81": "FunkyKong",
    "82": "TinyKong",
    "83": "KritterGreen",
    "84": "KritterBlue",
    "85": "KritterRed",
    "86": "KritterBrown",
    "87": "KRool",
    "88": "BabyPeach",
    "89": "BabyDaisy", 
    }

class ExportIconsError(Exception):
    pass


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _load_dol_dirs(dol_path):
    with open(dol_path, 'rb') as dol:
        dir_ptrs = []
        for addr in DIR_PTR_PTRS:
            dol.seek(addr, 0)
            dir_ptrs.append(bti(dol.read(4)) - 0x80003F00)

        dirs = {}
        dir_count = (DIRS_END - DIRS_START) // 4
        for dir_ind in range(dir_count):
            dirs[dir_ind] = []
            file_ptr = dir_ptrs[dir_ind]
            while file_ptr not in dir_ptrs[:dir_ind] + dir_ptrs[dir_ind + 1:]:
                dol.seek(file_ptr, 0)
                file_data = [bti(dol.read(4)) for _ in range(12)]
                if file_data[0] != DAT_FNAME_PTR:
                    break
                offset_en = file_data[2]
                len_en = file_data[1]
                offset_sp = file_data[6]
                len_sp = file_data[5]
                offset_fr = file_data[10]
                len_fr = file_data[9]
                dirs[dir_ind].append({'en': [offset_en, len_en], 'sp': [offset_sp, len_sp], 'fr': [offset_fr, len_fr]})
                file_ptr += 12 * 4

    return dirs


def _write_single_tpl(tpl_path, desc, image_data, tlut_data):
    data_length = desc._data_length()
    has_palette = desc.paletteDataPtr > 0

    if len(image_data) != data_length:
        raise ExportIconsError(
            f'image payload length mismatch for texture: expected {data_length}, got {len(image_data)}'
        )

    out = open(tpl_path, 'wb')

    # TEXPalette header.
    out.write(itb(0x0020AF30, 4))
    out.write(itb(1, 4))
    out.write(itb(0xC, 4))

    # TEXDescriptor pointers.
    out.write(itb(0x14, 4))
    out.write(itb(0x38 if has_palette else 0, 4))

    # TEXHeader.
    out.write(itb(desc.height, 2))
    out.write(itb(desc.width, 2))
    out.write(itb(desc.format, 4))
    out.write(itb(0x40 + (0x20 if has_palette else 0), 4))
    out.write(itb(desc.wrapS, 4))
    out.write(itb(desc.wrapT, 4))
    out.write(itb(desc.minFilter, 4))
    out.write(itb(desc.magFilter, 4))
    out.write(itb(desc.LODBias, 4))
    out.write(itb(desc.edgeLODEnable, 1))
    out.write(itb(desc.minLOD, 1))
    out.write(itb(desc.maxLOD, 1))
    out.write(itb(desc.unpacked, 1))

    palette_data_pad = 0
    if has_palette:
        palette_data_len = desc.paletteEntries * 2
        if len(tlut_data) != palette_data_len:
            raise ExportIconsError(
                f'palette payload length mismatch for texture: expected {palette_data_len}, got {len(tlut_data)}'
            )

        palette_data_offset = 0x60 + data_length
        mod = palette_data_offset % 0x20
        if mod != 0:
            palette_data_pad = 0x20 - mod
            palette_data_offset += palette_data_pad

        out.write(itb(desc.paletteEntries, 2))
        out.write(itb(1, 1))
        out.write(itb(0, 1))
        out.write(itb(desc.paletteFormat, 4))
        out.write(itb(palette_data_offset, 4))

    out.write(itb(0, 0x8 + (0x14 if has_palette else 0)))
    out.write(image_data)
    if has_palette:
        out.write(itb(0, palette_data_pad))
        out.write(tlut_data)

    out.close()


def _decode_tpl_to_png(tpl_path, png_path):
    subprocess.run(['wimgt', 'decode', '-q', '-o', '-d', png_path, tpl_path], check=True)


def _rgb5a3_to_rgb8(palette_bytes):
    """
    Convert RGB5A3 palette (256 entries, 2 bytes each) to RGB8 (256 entries, 3 bytes each).
    
    RGB5A3 format:
    - If bit 15 = 1: RGB555A3 (5 bits R, 5 bits G, 5 bits B, 3 bits A)
    - If bit 15 = 0: RGB444A3 (4 bits R, 4 bits G, 4 bits B, 3 bits A)
    
    RGB8 ignores alpha and uses only R, G, B scaled to 8-bit values.
    """
    if len(palette_bytes) != EXPECTED_PALETTE_LEN:
        raise ExportIconsError(
            f'palette data length mismatch: expected {EXPECTED_PALETTE_LEN}, got {len(palette_bytes)}'
        )
    
    rgb8_data = bytearray()
    
    for i in range(0, len(palette_bytes), 2):
        # Read 2-byte big-endian value
        color_16 = int.from_bytes(palette_bytes[i:i+2], 'big')
        
        if color_16 & 0x8000:  # Bit 15 set = RGB555A3
            # Extract 5-bit components
            r5 = (color_16 >> 10) & 0x1F
            g5 = (color_16 >> 5) & 0x1F
            b5 = color_16 & 0x1F
            # Scale 5-bit (0-31) to 8-bit (0-255)
            r8 = (r5 << 3) | (r5 >> 2)
            g8 = (g5 << 3) | (g5 >> 2)
            b8 = (b5 << 3) | (b5 >> 2)
        else:  # RGB444A3
            # Extract 4-bit components
            r4 = (color_16 >> 8) & 0x0F
            g4 = (color_16 >> 4) & 0x0F
            b4 = color_16 & 0x0F
            # Scale 4-bit (0-15) to 8-bit (0-255)
            r8 = (r4 << 4) | r4
            g8 = (g4 << 4) | g4
            b8 = (b4 << 4) | b4
        
        rgb8_data.extend([r8, g8, b8])
    
    return bytes(rgb8_data)


def _write_act_file(act_path, palette_bytes):
    """
    Write an Adobe Color Table (ACT) file for use in Photoshop.
    
    ACT format:
    - 768 bytes: 256 RGB entries (3 bytes each)
    - Optional 2 bytes: number of colors (or 0x0100 for 256)
    - Optional 2 bytes: transparent index (or 0xFFFF for none)
    """
    rgb8_data = _rgb5a3_to_rgb8(palette_bytes)
    
    with open(act_path, 'wb') as f:
        # Write 256 RGB entries
        f.write(rgb8_data)
        # Write number of colors (256) and transparent index (none)
        f.write(itb(0x0100, 2))
        f.write(itb(0xFFFF, 2))


def _cell_rect(cell_index):
    x = (cell_index % GRID_COLUMNS) * GRID_X_STRIDE + GRID_X_PAD
    y = (cell_index // GRID_COLUMNS) * GRID_Y_STRIDE + GRID_Y_PAD
    return x, y, GRID_CELL_WIDTH, GRID_CELL_HEIGHT


def _clip_rect(x, y, w, h, max_w, max_h):
    if x >= max_w or y >= max_h:
        return x, y, 0, 0
    clipped_w = min(w, max_w - x)
    clipped_h = min(h, max_h - y)
    return x, y, max(0, clipped_w), max(0, clipped_h)


def _is_non_empty_cell(cell_img):
    alpha = cell_img.split()[-1]
    return alpha.getbbox() is not None


def _is_expected_icon_descriptor(desc):
    return (
        desc.width == EXPECTED_WIDTH
        and desc.height == EXPECTED_HEIGHT
        and desc.format == EXPECTED_FORMAT
        and desc.paletteFormat == EXPECTED_PALETTE_FORMAT
        and desc.paletteEntries == EXPECTED_PALETTE_ENTRIES
        and desc.paletteDataPtr > 0
    )


def _discover_page_indices(tex_palette):
    if len(tex_palette.descriptors) <= FRONT_START:
        raise ExportIconsError(
            f'icon texture table too short: expected descriptor index {FRONT_START}, '
            f'found {len(tex_palette.descriptors) - 1}'
        )

    side_seed = tex_palette.descriptors[SIDE_START]
    front_seed = tex_palette.descriptors[FRONT_START]

    side_image_ptr = side_seed.dataPtr
    front_image_ptr = front_seed.dataPtr

    side_indices = []
    front_indices = []

    for texture_index, desc in enumerate(tex_palette.descriptors):
        if not _is_expected_icon_descriptor(desc):
            continue
        if desc._data_length() != EXPECTED_IMAGE_LEN:
            continue

        if desc.dataPtr == side_image_ptr:
            side_indices.append(texture_index)
        elif desc.dataPtr == front_image_ptr:
            front_indices.append(texture_index)

    if not side_indices:
        raise ExportIconsError('no side icon descriptors found using side seed image reference')
    if not front_indices:
        raise ExportIconsError('no front icon descriptors found using front seed image reference')

    return sorted(side_indices), sorted(front_indices)


def _page_base_name(view, texture_index):
    key = f'{texture_index:02X}'
    name_suffix = ''
    if key in DIR_NAMES:
        name_suffix = '_' + str(DIR_NAMES[key])
    return f'{view}_page_{texture_index:02X}_t{texture_index:03d}{name_suffix}'


def _clean_old_sheets(sheets_dir):
    """
    Clean old per-page sheet PNG files while preserving:
    - BASE.png (user-edited indexed image)
    - *.act files (user-edited palettes)
    """
    if not os.path.exists(sheets_dir):
        return
    
    for filename in os.listdir(sheets_dir):
        filepath = os.path.join(sheets_dir, filename)
        if not os.path.isfile(filepath):
            continue
        
        # Skip BASE.png, BASE.act, and all other ACT files
        if filename in ('BASE.png', 'BASE.act') or filename.endswith('.act'):
            continue
        
        # Remove old per-page PNG files (match pattern like "front_page_*.png")
        if filename.endswith('.png'):
            try:
                os.remove(filepath)
            except PermissionError:
                pass


def _prepare_output_tree(root):
    generated_dirs = [
        DIR_SHEETS,
        DIR_KNOWN_FALLBACK,
        DIR_RAW,
        DIR_METADATA,
    ]
    dirs_to_delete = [
        DIR_KNOWN_FALLBACK,
        DIR_RAW,
        DIR_METADATA,
    ]

    for rel in dirs_to_delete:
        path = os.path.join(root, rel)
        if os.path.exists(path):
            try:
                shutil.rmtree(path)
            except PermissionError:
                # If directory is locked, skip it; we'll just overwrite files inside
                pass

    # Clean old per-page sheets but preserve BASE.png, BASE.act, and ACT files
    _clean_old_sheets(os.path.join(root, DIR_SHEETS, 'side'))
    _clean_old_sheets(os.path.join(root, DIR_SHEETS, 'front'))

    # Ensure sheets subdirectories exist (preserve existing edited files)
    ensure_dir(os.path.join(root, DIR_SHEETS, 'side'))
    ensure_dir(os.path.join(root, DIR_SHEETS, 'front'))
    ensure_dir(os.path.join(root, DIR_KNOWN_FALLBACK))
    ensure_dir(os.path.join(root, DIR_RAW, 'side'))
    ensure_dir(os.path.join(root, DIR_RAW, 'front'))
    ensure_dir(os.path.join(root, DIR_METADATA))


def _extract_icon_entry(dol_path):
    dirs = _load_dol_dirs(dol_path)
    if ICON_DIR_INDEX not in dirs:
        raise ExportIconsError(f'directory {ICON_DIR_INDEX} not found in DOL directory table')

    files = dirs[ICON_DIR_INDEX]
    if len(files) <= ICON_FILE_INDEX:
        raise ExportIconsError(
            f'directory {ICON_DIR_INDEX} does not contain file index {ICON_FILE_INDEX} (found {len(files)})'
        )

    en_offset, en_length = files[ICON_FILE_INDEX]['en']
    if en_offset <= 0 or en_length <= 0:
        raise ExportIconsError('invalid icon entry offset/length in DOL table')

    return en_offset, en_length


def _load_texpalette(dat_path, entry_offset, entry_length):
    tex_abs = entry_offset + ICON_TEX_LOCAL_OFFSET
    tex_len = entry_length - ICON_TEX_LOCAL_OFFSET
    if tex_len <= 0:
        raise ExportIconsError('icon entry has no direct TEX payload section')

    dat_file = open(dat_path, 'rb')
    dat_root = File(dat_file)
    tex_palette = dat_root.add_child(tex_abs, tex_len, TEXPalette, 'IconTEXPalette')
    tex_palette.analyze()
    return dat_file, tex_palette


def _validate_descriptor(desc, image_len, palette_len, texture_index):
    if desc.width != EXPECTED_WIDTH or desc.height != EXPECTED_HEIGHT:
        raise ExportIconsError(
            f'texture {texture_index:02X} dimensions mismatch: {desc.width}x{desc.height} expected '
            f'{EXPECTED_WIDTH}x{EXPECTED_HEIGHT}'
        )
    if desc.format != EXPECTED_FORMAT:
        raise ExportIconsError(
            f'texture {texture_index:02X} format mismatch: 0x{desc.format:02X} expected 0x{EXPECTED_FORMAT:02X}'
        )
    if desc.paletteFormat != EXPECTED_PALETTE_FORMAT:
        raise ExportIconsError(
            f'texture {texture_index:02X} palette format mismatch: 0x{desc.paletteFormat:02X} expected '
            f'0x{EXPECTED_PALETTE_FORMAT:02X}'
        )
    if desc.paletteEntries != EXPECTED_PALETTE_ENTRIES:
        raise ExportIconsError(
            f'texture {texture_index:02X} palette entries mismatch: {desc.paletteEntries} expected '
            f'{EXPECTED_PALETTE_ENTRIES}'
        )
    if image_len != EXPECTED_IMAGE_LEN:
        raise ExportIconsError(
            f'texture {texture_index:02X} image length mismatch: 0x{image_len:X} expected 0x{EXPECTED_IMAGE_LEN:X}'
        )
    if palette_len != EXPECTED_PALETTE_LEN:
        raise ExportIconsError(
            f'texture {texture_index:02X} palette length mismatch: 0x{palette_len:X} expected 0x{EXPECTED_PALETTE_LEN:X}'
        )


def _export_one_page(root, entry_offset, tex_palette, desc, texture_index, view, pages_rows, cells_rows):

    # dataLens uses next-pointer deltas and is unreliable when many descriptors
    # intentionally share the same image payload pointer. Derive sizes from the
    # descriptor fields so shared payload layouts validate correctly.
    image_len = desc._data_length()
    palette_len = desc.paletteEntries * 2 if desc.paletteDataPtr > 0 else 0
    _validate_descriptor(desc, image_len, palette_len, texture_index)

    image_data, tlut_data = desc._read_payload()

    base_name = _page_base_name(view, texture_index)

    tpl_rel = os.path.join(DIR_RAW, view, f'{base_name}.tpl').replace('\\', '/')
    sheet_rel = os.path.join(DIR_SHEETS, view, f'{base_name}.png').replace('\\', '/')
    raw_img_rel = os.path.join(DIR_RAW, view, f'{base_name}_image.bin').replace('\\', '/')
    raw_pal_rel = os.path.join(DIR_RAW, view, f'{base_name}_palette.bin').replace('\\', '/')
    act_rel = os.path.join(DIR_SHEETS, view, f'{base_name}.act').replace('\\', '/')

    tpl_abs = os.path.join(root, tpl_rel)
    sheet_abs = os.path.join(root, sheet_rel)
    raw_img_abs = os.path.join(root, raw_img_rel)
    raw_pal_abs = os.path.join(root, raw_pal_rel)
    act_abs = os.path.join(root, act_rel)

    with open(raw_img_abs, 'wb') as f:
        f.write(image_data)
    with open(raw_pal_abs, 'wb') as f:
        f.write(tlut_data)

    # Export ACT file for Photoshop palette import
    _write_act_file(act_abs, tlut_data)

    _write_single_tpl(tpl_abs, desc, image_data, tlut_data)
    _decode_tpl_to_png(tpl_abs, sheet_abs)

    sheet_img = Image.open(sheet_abs).convert('RGBA')

    rows = int(math.ceil(sheet_img.height / float(GRID_Y_STRIDE)))
    max_cells = GRID_COLUMNS * rows

    nonempty_count = 0
    for cell_index in range(max_cells):
        x, y, w, h = _cell_rect(cell_index)
        x, y, w, h = _clip_rect(x, y, w, h, sheet_img.width, sheet_img.height)
        if w <= 0 or h <= 0:
            continue

        cell_img = sheet_img.crop((x, y, x + w, y + h))
        if not _is_non_empty_cell(cell_img):
            continue

        nonempty_count += 1

        cells_rows.append({
            'view': view,
            'texture_index_dec': texture_index,
            'texture_index_hex': f'0x{texture_index:02X}',
            'cell_dec': cell_index,
            'cell_hex': f'0x{cell_index:02X}',
            'x': x,
            'y': y,
            'width': w,
            'height': h,
        })

    descriptor_abs = desc.absolute
    image_abs = tex_palette.absolute + desc.dataPtr
    palette_abs = tex_palette.absolute + desc.paletteDataPtr

    pages_rows.append({
        'view': view,
        'texture_index_dec': texture_index,
        'texture_index_hex': f'0x{texture_index:02X}',
        'width': desc.width,
        'height': desc.height,
        'image_format': f'0x{desc.format:02X}',
        'palette_format': f'0x{desc.paletteFormat:02X}',
        'palette_entries': desc.paletteEntries,
        'entry_local_descriptor_offset': f'0x{descriptor_abs - entry_offset:X}',
        'entry_local_image_offset': f'0x{image_abs - entry_offset:X}',
        'entry_local_palette_offset': f'0x{palette_abs - entry_offset:X}',
        'dt_na_descriptor_offset': f'0x{descriptor_abs:X}',
        'dt_na_image_offset': f'0x{image_abs:X}',
        'dt_na_palette_offset': f'0x{palette_abs:X}',
        'image_len': image_len,
        'palette_len': palette_len,
        'sheet_png': sheet_rel,
        'grid_png': '',
        'tpl_file': tpl_rel,
        'raw_image_file': raw_img_rel,
        'raw_palette_file': raw_pal_rel,
        'nonempty_cells_exported': nonempty_count,
    })


def _export_base_indexed_image(root, image_data, view):
    """
    Export the shared indexed image payload as a direct-indexed grayscale PNG.

    Each pixel value (0-255) IS the original C8 palette index stored directly —
    no palette lookup, no colour conversion, no quantization loss.

    The C8 format stores image data in 8x8 pixel tiles.  This function de-tiles
    the raw bytes into linear (row-major) order so the result looks correct in an
    image editor, while still encoding each index as a gray level identical to
    its value.  The reimporter performs the inverse tiling step.
    """
    if len(image_data) != EXPECTED_IMAGE_LEN:
        raise ExportIconsError(
            f'indexed image data length mismatch: expected {EXPECTED_IMAGE_LEN}, got {len(image_data)}'
        )

    # CI8 uses 8x4 pixel blocks (8 wide, 4 tall)
    pixels = bytearray(EXPECTED_WIDTH * EXPECTED_HEIGHT)
    src = 0
    for tile_y in range(EXPECTED_HEIGHT // 4):
        for tile_x in range(EXPECTED_WIDTH // 8):
            for block_row in range(4):
                for block_col in range(8):
                    dst_x = tile_x * 8 + block_col
                    dst_y = tile_y * 4 + block_row
                    pixels[dst_y * EXPECTED_WIDTH + dst_x] = image_data[src]
                    src += 1

    output_path = os.path.join(root, DIR_SHEETS, view, 'BASE.png')
    img = Image.frombytes('P', (EXPECTED_WIDTH, EXPECTED_HEIGHT), bytes(pixels))
    # Embed a grayscale ramp as the default palette so the image is viewable
    # without loading an ACT file. Each entry N maps to gray N (R=G=B=N).
    grayscale_ramp = bytearray()
    for i in range(256):
        grayscale_ramp.extend([i, i, i])
    img.putpalette(bytes(grayscale_ramp))
    img.save(output_path, 'PNG')

    # Write matching ACT file so the user can reload the grayscale ramp in
    # Photoshop to verify no index values were accidentally shifted during editing.
    act_path = os.path.join(root, DIR_SHEETS, view, 'BASE.act')
    with open(act_path, 'wb') as f:
        f.write(bytes(grayscale_ramp))   # 768 bytes: 256 × RGB
        f.write(b'\x01\x00')             # number of colors = 256
        f.write(b'\xff\xff')             # no transparent index


def _copy_known_fallbacks(root):
    pairs = [
        ('front', 0x8B, 73, 'front_page8B_cell49_t139.png'),
        ('side', 0x4E, 73, 'side_page4E_cell49_t078.png'),
    ]

    for view, page, cell_dec, output_name in pairs:
        base_name = _page_base_name(view, page)
        sheet_abs = os.path.join(root, DIR_SHEETS, view, f'{base_name}.png')
        if not os.path.exists(sheet_abs):
            continue
        sheet_img = Image.open(sheet_abs).convert('RGBA')
        x, y, w, h = _cell_rect(cell_dec)
        x, y, w, h = _clip_rect(x, y, w, h, sheet_img.width, sheet_img.height)
        if w <= 0 or h <= 0:
            continue
        cell_img = sheet_img.crop((x, y, x + w, y + h))
        dest_abs = os.path.join(root, DIR_KNOWN_FALLBACK, output_name)
        cell_img.save(dest_abs)


def _write_pages_csv(path, rows):
    fields = [
        'view',
        'texture_index_dec',
        'texture_index_hex',
        'width',
        'height',
        'image_format',
        'palette_format',
        'palette_entries',
        'entry_local_descriptor_offset',
        'entry_local_image_offset',
        'entry_local_palette_offset',
        'dt_na_descriptor_offset',
        'dt_na_image_offset',
        'dt_na_palette_offset',
        'image_len',
        'palette_len',
        'sheet_png',
        'grid_png',
        'tpl_file',
        'raw_image_file',
        'raw_palette_file',
        'nonempty_cells_exported',
    ]
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_cells_csv(path, rows):
    fields = [
        'view',
        'texture_index_dec',
        'texture_index_hex',
        'cell_dec',
        'cell_hex',
        'x',
        'y',
        'width',
        'height',
    ]
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _try_get_wimgt_version():
    try:
        result = subprocess.run(['wimgt', '--version'], capture_output=True, text=True, check=True)
        line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ''
        return line
    except Exception:
        return None


def _check_existing_edits(root):
    """
    Return True if a previous export with potentially user-edited files exists.
    Detected by the presence of BASE.png in either sheets subdirectory.
    """
    for view in ('side', 'front'):
        base_png = os.path.join(root, DIR_SHEETS, view, 'BASE.png')
        if os.path.exists(base_png):
            return True
    return False


def main():
    if not os.path.exists(INPUT_DOL):
        raise ExportIconsError(f'missing input DOL: {INPUT_DOL}')
    if not os.path.exists(INPUT_DAT):
        raise ExportIconsError(f'missing input DAT: {INPUT_DAT}')

    if _check_existing_edits(OUTPUT_ROOT):
        print('WARNING: A previous export already exists in:')
        print(f'  {os.path.relpath(OUTPUT_ROOT, ROOT_DIR)}')
        print('Running a new export will overwrite BASE.png and all per-page sheet PNGs,')
        print('losing any edits you have made to those files.')
        print('Your ACT palette files will be preserved.')
        print()
        answer = input('Continue anyway? [y/n] ').strip().lower()
        if answer != 'y':
            print('Export cancelled.')
            raise SystemExit(0)

    _prepare_output_tree(OUTPUT_ROOT)

    entry_offset, entry_length = _extract_icon_entry(INPUT_DOL)

    dat_file, tex_palette = _load_texpalette(INPUT_DAT, entry_offset, entry_length)
    try:
        side_texture_indices, front_texture_indices = _discover_page_indices(tex_palette)

        pages_rows = []
        cells_rows = []

        # Export base indexed images (shared across all pages of each view)
        side_seed_desc = tex_palette.descriptors[side_texture_indices[0]]
        side_image_data, _ = side_seed_desc._read_payload()
        _export_base_indexed_image(OUTPUT_ROOT, side_image_data, 'side')

        front_seed_desc = tex_palette.descriptors[front_texture_indices[0]]
        front_image_data, _ = front_seed_desc._read_payload()
        _export_base_indexed_image(OUTPUT_ROOT, front_image_data, 'front')

        for texture_index in side_texture_indices:
            desc = tex_palette.descriptors[texture_index]
            _export_one_page(
                OUTPUT_ROOT,
                entry_offset,
                tex_palette,
                desc,
                texture_index,
                'side',
                pages_rows,
                cells_rows,
            )

        for texture_index in front_texture_indices:
            desc = tex_palette.descriptors[texture_index]
            _export_one_page(
                OUTPUT_ROOT,
                entry_offset,
                tex_palette,
                desc,
                texture_index,
                'front',
                pages_rows,
                cells_rows,
            )

    finally:
        dat_file.close()

    _copy_known_fallbacks(OUTPUT_ROOT)

    pages_rows.sort(key=lambda row: row['texture_index_dec'])
    cells_rows.sort(key=lambda row: (row['view'], row['texture_index_dec'], row['cell_dec']))

    pages_csv = os.path.join(OUTPUT_ROOT, DIR_METADATA, 'pages.csv')
    cells_csv = os.path.join(OUTPUT_ROOT, DIR_METADATA, 'cells.csv')
    manifest_json = os.path.join(OUTPUT_ROOT, DIR_METADATA, 'manifest.json')

    _write_pages_csv(pages_csv, pages_rows)
    _write_cells_csv(cells_csv, cells_rows)

    manifest = {
        'generated_utc': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
        'source': {
            'dol_path': os.path.relpath(INPUT_DOL, ROOT_DIR).replace('\\', '/'),
            'dat_path': os.path.relpath(INPUT_DAT, ROOT_DIR).replace('\\', '/'),
            'dol_sha256': sha256_file(INPUT_DOL),
            'dat_sha256': sha256_file(INPUT_DAT),
            'group_index': ICON_DIR_INDEX,
            'file_index': ICON_FILE_INDEX,
            'entry_offset': f'0x{entry_offset:X}',
            'entry_length': f'0x{entry_length:X}',
            'direct_texture_section_offset': f'0x{ICON_TEX_LOCAL_OFFSET:X}',
            'direct_texture_section_length': f'0x{entry_length - ICON_TEX_LOCAL_OFFSET:X}',
        },
        'tooling': {
            'exporter': 'SluggiesTools/export_icons.py',
            'wimgt_version': _try_get_wimgt_version(),
        },
        'ranges': {
            'side_texture_indices': side_texture_indices,
            'front_texture_indices': front_texture_indices,
        },
        'expected_constraints': {
            'width': EXPECTED_WIDTH,
            'height': EXPECTED_HEIGHT,
            'image_format': f'0x{EXPECTED_FORMAT:02X}',
            'palette_format': f'0x{EXPECTED_PALETTE_FORMAT:02X}',
            'palette_entries': EXPECTED_PALETTE_ENTRIES,
            'image_len': EXPECTED_IMAGE_LEN,
            'palette_len': EXPECTED_PALETTE_LEN,
        },
        'cell_grid': {
            'columns': GRID_COLUMNS,
            'x_stride': GRID_X_STRIDE,
            'y_stride': GRID_Y_STRIDE,
            'cell_x_pad': GRID_X_PAD,
            'cell_y_pad': GRID_Y_PAD,
            'cell_width': GRID_CELL_WIDTH,
            'cell_height': GRID_CELL_HEIGHT,
            'formula': 'x=(cell%21)*49+1; y=(cell//21)*52+1; crop 48x51 clipped to sheet bounds',
        },
        'page_count': len(pages_rows),
        'cell_png_count': len(cells_rows),
        'pages': pages_rows,
    }

    with open(manifest_json, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)

    print('Character-select icon export assets generated successfully:')
    print(f'  {os.path.relpath(OUTPUT_ROOT, ROOT_DIR)}')
    print(f'  Pages exported: {len(pages_rows)}')
    print(f'  Non-empty cells exported: {len(cells_rows)}')


if __name__ == '__main__':
    try:
        main()
    except ExportIconsError as exc:
        print(f'ERROR: {exc}')
        raise SystemExit(1)
    except FileNotFoundError as exc:
        print(f'ERROR: required external tool not found: {exc}')
        print('Make sure Wiimm tools (wimgt) are installed and available on PATH.')
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        print(f'ERROR: external tool failed: {exc}')
        raise SystemExit(1)
