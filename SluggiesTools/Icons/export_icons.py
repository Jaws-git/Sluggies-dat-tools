import csv
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from PIL import Image

ICONS_DIR = os.path.dirname(__file__)
TOOLS_DIR = os.path.normpath(os.path.join(ICONS_DIR, '..'))
ROOT_DIR = os.path.normpath(os.path.join(TOOLS_DIR, '..'))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

# Step 2.2 – Initialize universal logger in child process.
import slogger as _slogger
_slogger.configure()

from base import File
from helper import bti, itb
from tpl import TEXPalette
from Icons import layout2d

# --- Guide-proven DOL directory table address (direct group-entry record) ---
# The group-entry record for group 119, entry 2 sits at this DOL file offset.
# This is the address used by the discord icon-modding guide and is confirmed
# working for both reads and writes on a live game.
# The record is 48 bytes (12 x u32): for each of EN/SP/FR it stores
# [dat_fname_ptr, length, offset, alloc].
# See DOL_Directory_Table_Investigation.md for background.
ICON_ENTRY_DOL_OFFSET = 0x68DE88

# --- Old pointer-chain approach (commented out, may need to be restored) ---
# The old code walked a pointer array at 0x69C828..0x69CAD8 to locate
# directory entries indirectly.  This produced correct reads but is ~58 KB
# away from the guide's proven address.  If the new direct-read approach
# causes any regression, restore the old constants and _load_dol_dirs() below.
# DIRS_START = 0x69C828
# DIRS_END = 0x69CAD8
# DIR_PTR_PTRS = range(DIRS_START, DIRS_END, 4)
# DAT_FNAME_PTR = 0x8067F658
# ICON_DIR_INDEX = 119
# ICON_FILE_INDEX = 2
ICON_TEX_LOCAL_OFFSET = 0x20

SIDE_START = 0x49
SIDE_END = 0x4E
FRONT_START = 0x4F
FRONT_END = 0x8B

EXPECTED_WIDTH = 1024
EXPECTED_HEIGHT = 256
EXPECTED_FORMAT = 0x09
PALETTE_FORMAT_IA8 = 0x00
PALETTE_FORMAT_RGB5A3 = 0x02
EXPECTED_PALETTE_FORMATS = frozenset((PALETTE_FORMAT_IA8, PALETTE_FORMAT_RGB5A3))
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

INPUT_DOL = os.path.join(ROOT_DIR, '1_Input', 'main.dol')
INPUT_DAT = os.path.join(ROOT_DIR, '1_Input', 'dt_na.dat')
OUTPUT_ROOT = os.path.join(ROOT_DIR, '2_Output_Models', '_ICONS')

DIR_SHEETS = 'sheets (EDIT BASE.PNG)'
DIR_KNOWN_FALLBACK = 'known_fallback'
DIR_LEGACY_RAW = 'raw'   # .tpl/.bin dumps of older exports; deleted on the next export
DIR_METADATA = 'metadata'
DIR_ROSTER = 'roster_pages'
# The roster expansion's own icon pages (menu [10]): CMPR, sized to the portraits a roster configuration holds,
# so their Dolphin dump names change with the configuration.
ROSTER_PAGES = ((0x92, 'side'), (0x93, 'front'))
ROSTER_PAGE_FORMAT = 0x0E

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

SIDE_CHARACTER_NAMES = (
    'Mario', 'Luigi', 'DonkeyKong', 'DiddyKong', 'Peach', 'Daisy', 'Yoshi',
    'BabyMario', 'BabyLuigi', 'Bowser', 'Wario', 'Waluigi', 'Koopa', 'Toad',
    'Boo', 'Toadette', 'ShyGuy', 'Birdo', 'MontyMole', 'BowserJr', 'Paratroopa',
    'Pianta', 'RedPianta', 'YellowPianta', 'Noki', 'RedNoki', 'GreenNoki',
    'HammerBro', 'Toadsworth', 'BlueToad', 'YellowToad', 'GreenToad',
    'PurpleToad', 'Magikoopa', 'RedMagikoopa', 'GreenMagikoopa',
    'YellowMagikoopa', 'KingBoo', 'PeteyPiranha', 'DixieKong', 'Goomba',
    'Paragoomba', 'RedKoopa', 'GreenParatroopa', 'BlueShyGuy', 'YellowShyGuy',
    'GreenShyGuy', 'GrayShyGuy', 'DryBones', 'GreenDryBones', 'DarkBones',
    'BlueDryBones', 'FireBro', 'BoomerangBro', 'Wiggler', 'Blooper', 'FunkyKong',
    'TinyKong', 'Kritter', 'BlueKritter', 'RedKritter', 'BrownKritter',
    'KingKRool', 'BabyPeach', 'BabyDaisy', 'BabyDK', 'RedYoshi', 'BlueYoshi',
    'YellowYoshi', 'LightBlueYoshi', 'PinkYoshi',
)

SIDE_PAGE_CHARACTER_IDS = (
    0x00, 0x01, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x08, 0x09,
    0x0A, 0x0B, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13, 0x14,
    0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F, 0x20,
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x27, 0x28, 0x29, 0x2A, 0x2B,
    0x2C, 0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37,
    0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E, 0x3E, 0x3F, 0x3F, 0x40, 0x40,
    0x41, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46,
)

# Per-character portraits: <model folder>/<HP model folder>/icon/{Front,Side}Icon.png, next to its tex/.
# The bank's source tables are layout elements 0 (normal_a), 1 (side) and 2 (front); each holds one track
# whose records are keyed by character ID and name a resource row (+0x06): page and UV rect.
MODELS_ROOT = os.path.join(ROOT_DIR, '2_Output_Models')
CHARACTER_ICON_DIR = 'icon'
CHARACTER_ICON_FILES = {'front': 'FrontIcon.png', 'side': 'SideIcon.png'}
CHARACTER_ICON_ELEMENTS = {'side': 1, 'front': 2}
SOURCE_RECORD_RESOURCE = 0x06
CHARACTER_DIR_OFFSET = 0x12                # a character's model directory is its ID + 0x12
OWN_ICON_IDS = range(0x00, 0x4D)           # players and the six unused characters (dirs 18-94); Miis share one icon
# A prefix match: exports made before 2026-10-04 carry the game's leftover byte after '.gpl' (binfmt.clean_geo_name).
HP_MODEL_FOLDER = re.compile(r'\d+_(?!L_).+\.gpl')

SIDE_DIR_NAMES = {
    f'{page_index:02X}': SIDE_CHARACTER_NAMES[character_id]
    for page_index, character_id in enumerate(SIDE_PAGE_CHARACTER_IDS)
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


# --- Old _load_dol_dirs (commented out, may need to be restored) ---
# If the direct-read approach via ICON_ENTRY_DOL_OFFSET causes problems,
# uncomment this function and the constants above, then revert
# _extract_icon_entry() to call _load_dol_dirs() as before.
#
# def _load_dol_dirs(dol_path):
#     with open(dol_path, 'rb') as dol:
#         dir_ptrs = []
#         for addr in DIR_PTR_PTRS:
#             dol.seek(addr, 0)
#             dir_ptrs.append(bti(dol.read(4)) - 0x80003F00)
#
#         dirs = {}
#         dir_count = (DIRS_END - DIRS_START) // 4
#         for dir_ind in range(dir_count):
#             dirs[dir_ind] = []
#             file_ptr = dir_ptrs[dir_ind]
#             while file_ptr not in dir_ptrs[:dir_ind] + dir_ptrs[dir_ind + 1:]:
#                 dol.seek(file_ptr, 0)
#                 file_data = [bti(dol.read(4)) for _ in range(12)]
#                 if file_data[0] != DAT_FNAME_PTR:
#                     break
#                 offset_en = file_data[2]
#                 len_en = file_data[1]
#                 offset_sp = file_data[6]
#                 len_sp = file_data[5]
#                 offset_fr = file_data[10]
#                 len_fr = file_data[9]
#                 dirs[dir_ind].append({'en': [offset_en, len_en], 'sp': [offset_sp, len_sp], 'fr': [offset_fr, len_fr]})
#                 file_ptr += 12 * 4
#
#     return dirs


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


def _decode_texture_to_png(desc, image_data, tlut_data, png_path):
    """Decode one texture to ``png_path`` through a throwaway single-image TPL (wimgt only reads files)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tpl_path = os.path.join(temp_dir, 'texture.tpl')
        _write_single_tpl(tpl_path, desc, image_data, tlut_data)
        _decode_tpl_to_png(tpl_path, png_path)


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


def _ia8_to_rgb8(palette_bytes):
    if len(palette_bytes) != EXPECTED_PALETTE_LEN:
        raise ExportIconsError(
            f'palette data length mismatch: expected {EXPECTED_PALETTE_LEN}, got {len(palette_bytes)}'
        )

    rgb8_data = bytearray()
    for intensity, _alpha in zip(palette_bytes[0::2], palette_bytes[1::2]):
        rgb8_data.extend((intensity, intensity, intensity))
    return bytes(rgb8_data)


def _write_act_file(act_path, palette_bytes, palette_format):
    """
    Write an Adobe Color Table (ACT) file for use in Photoshop.
    
    ACT format:
    - 768 bytes: 256 RGB entries (3 bytes each)
    - Optional 2 bytes: number of colors (or 0x0100 for 256)
    - Optional 2 bytes: transparent index (or 0xFFFF for none)
    """
    if palette_format == PALETTE_FORMAT_IA8:
        rgb8_data = _ia8_to_rgb8(palette_bytes)
    elif palette_format == PALETTE_FORMAT_RGB5A3:
        rgb8_data = _rgb5a3_to_rgb8(palette_bytes)
    else:
        raise ExportIconsError(f'unsupported palette format: 0x{palette_format:02X}')
    
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
        and desc.paletteFormat in EXPECTED_PALETTE_FORMATS
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
    names = SIDE_DIR_NAMES if view == 'side' else DIR_NAMES
    if key in names:
        name_suffix = '_' + str(names[key])
    return f'{view}_page_{texture_index:02X}_t{texture_index:03d}{name_suffix}'


def _page_character_name(view, texture_index):
    names = SIDE_DIR_NAMES if view == 'side' else DIR_NAMES
    return names.get(f'{texture_index:02X}', '')


def _dolphin_texture_name(desc, image_data, tlut_data):
    """Return the Dolphin emulator dump filename for the page texture payload.

    Reuses tpl.TEXDescriptor hashing (XXH64 of the encoded image plus the
    trimmed TLUT) so the name matches what Dolphin names its texture dump.
    """
    return desc.dolphinTextureBasenameForPayload(image_data, tlut_data) + '.png'


def _write_dolphin_names_txt(path, dolphin_names):
    """Write the expected Dolphin dump filenames, one per exported page.

    Lines are sorted by character name (pages without a known character sort
    by their base name), then by view and page index.
    """
    ordered = sorted(
        dolphin_names,
        key=lambda entry: (
            entry['character_name'].lower() or entry['base_name'],
            entry['view'],
            entry['texture_index_dec'],
        ),
    )
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for entry in ordered:
            f.write(f"{entry['base_name']} -> {entry['dolphin_name']}\n")


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
            except PermissionError as exc:
                _slogger.warning(
                    f'Could not remove stale icon sheet {filepath}: {exc}; '
                    'the existing file may remain in this export.',
                    source='icons.export.cleanup',
                )


def _prepare_output_tree(root):
    generated_dirs = [
        DIR_SHEETS,
        DIR_KNOWN_FALLBACK,
        DIR_METADATA,
    ]
    dirs_to_delete = [
        DIR_KNOWN_FALLBACK,
        DIR_LEGACY_RAW,
        DIR_METADATA,
        DIR_ROSTER,
    ]

    for rel in dirs_to_delete:
        path = os.path.join(root, rel)
        if os.path.exists(path):
            try:
                shutil.rmtree(path)
            except PermissionError as exc:
                # If directory is locked, skip it; we'll just overwrite files inside
                _slogger.warning(
                    f'Could not remove old icon output directory {path}: {exc}; '
                    'stale files may remain in this export.',
                    source='icons.export.cleanup',
                )

    # Clean old per-page sheets but preserve BASE.png, BASE.act, and ACT files
    _clean_old_sheets(os.path.join(root, DIR_SHEETS, 'side'))
    _clean_old_sheets(os.path.join(root, DIR_SHEETS, 'front'))

    # Ensure sheets subdirectories exist (preserve existing edited files)
    ensure_dir(os.path.join(root, DIR_SHEETS, 'side'))
    ensure_dir(os.path.join(root, DIR_SHEETS, 'front'))
    ensure_dir(os.path.join(root, DIR_KNOWN_FALLBACK))
    ensure_dir(os.path.join(root, DIR_METADATA))
    ensure_dir(os.path.join(root, DIR_ROSTER))


def _extract_icon_entry(dol_path):
    """Read the icon bank entry (group 119, entry 2) directly from the DOL.

    Uses the guide-proven DOL file offset 0x68DE88 which points straight at
    the 48-byte group-entry record.  The record layout (12 x u32) is:
        [dat_fname_ptr, len_en, offset_en, alloc_en,
         dat_fname_ptr, len_sp, offset_sp, alloc_sp,
         dat_fname_ptr, len_fr, offset_fr, alloc_fr]
    We return the English (offset, length) pair.
    """
    with open(dol_path, 'rb') as dol:
        dol.seek(ICON_ENTRY_DOL_OFFSET, 0)
        words = [bti(dol.read(4)) for _ in range(12)]

    # English entry: words[2] = offset, words[1] = length
    en_offset = words[2]
    en_length = words[1]

    if en_offset <= 0 or en_length <= 0:
        raise ExportIconsError(
            f'invalid icon entry at DOL offset 0x{ICON_ENTRY_DOL_OFFSET:X}: '
            f'offset=0x{en_offset:X}, length=0x{en_length:X}'
        )

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
    if desc.paletteFormat not in EXPECTED_PALETTE_FORMATS:
        raise ExportIconsError(
            f'texture {texture_index:02X} palette format mismatch: 0x{desc.paletteFormat:02X} expected one of '
            f'{", ".join(f"0x{value:02X}" for value in sorted(EXPECTED_PALETTE_FORMATS))}'
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


def _export_one_page(root, entry_offset, tex_palette, desc, texture_index, view, pages_rows, cells_rows, dolphin_names):

    # dataLens uses next-pointer deltas and is unreliable when many descriptors
    # intentionally share the same image payload pointer. Derive sizes from the
    # descriptor fields so shared payload layouts validate correctly.
    image_len = desc._data_length()
    palette_len = desc.paletteEntries * 2 if desc.paletteDataPtr > 0 else 0
    _validate_descriptor(desc, image_len, palette_len, texture_index)

    image_data, tlut_data = desc._read_payload()

    base_name = _page_base_name(view, texture_index)

    dolphin_names.append({
        'view': view,
        'texture_index_dec': texture_index,
        'base_name': base_name,
        'character_name': _page_character_name(view, texture_index),
        'dolphin_name': _dolphin_texture_name(desc, image_data, tlut_data),
    })

    sheet_rel = os.path.join(DIR_SHEETS, view, f'{base_name}.png').replace('\\', '/')
    act_rel = os.path.join(DIR_SHEETS, view, f'{base_name}.act').replace('\\', '/')

    sheet_abs = os.path.join(root, sheet_rel)
    act_abs = os.path.join(root, act_rel)

    # Export ACT file for Photoshop palette import
    _write_act_file(act_abs, tlut_data, desc.paletteFormat)

    _decode_texture_to_png(desc, image_data, tlut_data, sheet_abs)

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
        'nonempty_cells_exported': nonempty_count,
    })

    _slogger.info(
        f'Exported {view} page 0x{texture_index:02X} ({base_name}): '
        f'{nonempty_count} non-empty cells',
        source='icons.export_icons',
    )
    return sheet_img


def _export_roster_pages(root, tex_palette, dolphin_names, page_images=None):
    """The roster expansion's icon pages, if this bank has them: each decoded to a PNG named like Dolphin's
    texture dump (``tex1_WxH_<hash>_14.png``), the name a Dolphin custom texture for that page must have.
    Each page's RGBA image goes into ``page_images``. Returns the number of pages written."""
    written = {}
    page_images = {} if page_images is None else page_images
    for texture_index, view in ROSTER_PAGES:
        if texture_index >= len(tex_palette.descriptors):
            continue
        desc = tex_palette.descriptors[texture_index]
        if desc.format != ROSTER_PAGE_FORMAT:
            continue
        image_data, _tlut = desc._read_payload()
        dolphin_name = _dolphin_texture_name(desc, image_data, b'')
        base_name = f'roster_{view}_page_{texture_index:02X}'
        dolphin_names.append({
            'view': view,
            'texture_index_dec': texture_index,
            'base_name': base_name,
            'character_name': '',
            'dolphin_name': dolphin_name,
        })
        if dolphin_name in written:                   # identical side and front pages: one texture for both
            page_images[texture_index] = page_images[written[dolphin_name][1]]
            _slogger.info(f'Roster {view} page 0x{texture_index:02X} is identical to the {written[dolphin_name][0]} '
                          f'page ({dolphin_name})', source='icons.export_icons')
            continue
        written[dolphin_name] = (view, texture_index)
        page_png = os.path.join(root, DIR_ROSTER, dolphin_name)
        _decode_texture_to_png(desc, image_data, b'', page_png)
        with Image.open(page_png) as page_img:
            page_images[texture_index] = page_img.convert('RGBA')
        _slogger.info(f'Exported roster {view} page 0x{texture_index:02X} ({desc.width}x{desc.height} CMPR) as '
                      f'{DIR_ROSTER}/{dolphin_name}', source='icons.export_icons')
    return len(written)


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


def _character_icon_cells(bank_bytes, page_sizes):
    """Each character's own portrait cells, read from the bank's side and front source tables.

    Returns ``(cells, problems)``: ``cells`` maps character ID to {view: (page, (x, y, w, h))} for IDs in
    ``OWN_ICON_IDS`` with their own key; the game's fallback to the nearest lower key is deliberately not
    followed. ``page_sizes`` maps texture index to (width, height). ``problems`` lists keys whose rect is
    not a 48x51 cell inside a known page."""
    bank = layout2d.parse_bank(bank_bytes)
    rows = layout2d.resource_rows(bank_bytes, bank)
    cells, problems = {}, []
    for view, element_index in CHARACTER_ICON_ELEMENTS.items():
        element = layout2d.parse_element(bank_bytes, bank, element_index)
        for record in (r for track in element.tracks for r in track.records):
            char_id, row = struct.unpack_from('>H', record, 2)[0], struct.unpack_from('>H', record, SOURCE_RECORD_RESOURCE)[0]
            if char_id not in OWN_ICON_IDS:
                continue
            if row >= len(rows):
                problems.append(f'0x{char_id:02X} {view}: resource row {row} does not exist')
                continue
            page, (v1, u1, v2, u2) = rows[row]
            if page not in page_sizes:
                problems.append(f'0x{char_id:02X} {view}: page 0x{page:02X} is not an exported icon page')
                continue
            width, height = page_sizes[page]
            x, y = round(u1 * width), round(v1 * height)
            w, h = round(u2 * width) - x, round(v2 * height) - y
            if (w, h) != (GRID_CELL_WIDTH, GRID_CELL_HEIGHT) or x < 0 or y < 0 or x + w > width or y + h > height:
                problems.append(f'0x{char_id:02X} {view}: rect ({x}, {y}, {w}x{h}) on page 0x{page:02X} '
                                f'is not a {GRID_CELL_WIDTH}x{GRID_CELL_HEIGHT} cell inside the page')
                continue
            cells.setdefault(char_id, {})[view] = (page, (x, y, w, h))
    return cells, problems


def _character_home_folder(models_root, dir_index):
    """The high-poly model folder (the one next to which tex/ lives) of model directory ``dir_index``:
    ``<models_root>/<dir_index> <name>/<offset>_<geo>.gpl``, not ``L_``, holding a .sluggie.
    Returns ``(path, None)`` or ``(None, reason)``."""
    if not os.path.isdir(models_root):
        return None, 'no model export'
    folders = [name for name in os.listdir(models_root)
               if name.split(' ', 1)[0] == str(dir_index) and os.path.isdir(os.path.join(models_root, name))]
    if not folders:
        return None, 'no model export'
    if len(folders) > 1:
        return None, 'several model folders'
    char_dir = os.path.join(models_root, folders[0])
    candidates = []
    for name in sorted(os.listdir(char_dir)):
        path = os.path.join(char_dir, name)
        if HP_MODEL_FOLDER.match(name) and os.path.isdir(path) and \
                any(f.endswith('.sluggie') for f in os.listdir(path)):
            candidates.append(path)
    if not candidates:
        return None, 'no model export'
    if len(candidates) > 1:
        return None, 'several high-poly model folders'
    return candidates[0], None


def _export_character_icons(models_root, cells, page_images):
    """Write ``icon/FrontIcon.png`` and ``icon/SideIcon.png`` (48x51 RGBA) into each character's high-poly
    model folder, and remove ones whose character has no own key in this bank. Returns
    ``(written, skipped)``: manifest rows, and {reason: [dir index, ...]}."""
    written, skipped = [], {}
    for char_id in OWN_ICON_IDS:
        dir_index = char_id + CHARACTER_DIR_OFFSET
        home, reason = _character_home_folder(models_root, dir_index)
        if home is None:
            skipped.setdefault(reason, []).append(dir_index)
            continue
        icon_dir = os.path.join(home, CHARACTER_ICON_DIR)
        views = cells.get(char_id, {})
        if not views:
            skipped.setdefault('no own icon', []).append(dir_index)
        for view, file_name in CHARACTER_ICON_FILES.items():
            path = os.path.join(icon_dir, file_name)
            if view not in views:
                if os.path.exists(path):
                    os.remove(path)
                continue
            page, (x, y, w, h) = views[view]
            ensure_dir(icon_dir)
            page_images[page].crop((x, y, x + w, y + h)).convert('RGBA').save(path, 'PNG')
            written.append({
                'dir': dir_index,
                'character_id': f'0x{char_id:02X}',
                'view': view,
                'page': f'0x{page:02X}',
                'rect': [x, y, w, h],
                'png': os.path.relpath(path, models_root).replace('\\', '/'),   # relative to 2_Output_Models
            })
        if os.path.isdir(icon_dir) and not os.listdir(icon_dir):
            os.rmdir(icon_dir)
    return written, skipped


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
    parser = argparse.ArgumentParser(
        description='Export character-select icon atlases and metadata from DOL/DAT.'
    )
    parser.add_argument('--dol-path', default=INPUT_DOL, help='path to main.dol source')
    parser.add_argument('--dat-path', default=INPUT_DAT, help='path to dt_na.dat source')
    args = parser.parse_args()

    input_dol = os.path.normpath(args.dol_path)
    input_dat = os.path.normpath(args.dat_path)

    if not os.path.exists(input_dol):
        raise ExportIconsError(f'missing input DOL: {input_dol}')
    if not os.path.exists(input_dat):
        raise ExportIconsError(f'missing input DAT: {input_dat}')

    _slogger.info('Starting player icon export...', source='icons.export_icons')

    if _check_existing_edits(OUTPUT_ROOT):
        prompt_msg = (
            'WARNING: A previous export already exists in:\n'
            f'  {os.path.relpath(OUTPUT_ROOT, ROOT_DIR)}\n'
            'Running a new export will overwrite BASE.png and all per-page sheet PNGs,\n'
            'losing any edits you have made to those files.\n'
            'Your ACT palette files will be preserved.'
        )
        _slogger.warning(prompt_msg, source='icons.export_icons')
        answer = input('Continue anyway? [y/n] ').strip().lower()
        _slogger.log_user_input('Overwrite confirm', answer, source='icons.export_icons')
        if answer != 'y':
            _slogger.info('Export cancelled.', source='icons.export_icons')
            raise SystemExit(0)

    _prepare_output_tree(OUTPUT_ROOT)

    entry_offset, entry_length = _extract_icon_entry(input_dol)

    dat_file, tex_palette = _load_texpalette(input_dat, entry_offset, entry_length)
    try:
        side_texture_indices, front_texture_indices = _discover_page_indices(tex_palette)

        pages_rows = []
        cells_rows = []
        dolphin_names = []
        page_images = {}

        # Export base indexed images (shared across all pages of each view)
        side_seed_desc = tex_palette.descriptors[side_texture_indices[0]]
        side_image_data, _ = side_seed_desc._read_payload()
        _export_base_indexed_image(OUTPUT_ROOT, side_image_data, 'side')

        front_seed_desc = tex_palette.descriptors[front_texture_indices[0]]
        front_image_data, _ = front_seed_desc._read_payload()
        _export_base_indexed_image(OUTPUT_ROOT, front_image_data, 'front')

        for texture_index in side_texture_indices:
            desc = tex_palette.descriptors[texture_index]
            page_images[texture_index] = _export_one_page(
                OUTPUT_ROOT,
                entry_offset,
                tex_palette,
                desc,
                texture_index,
                'side',
                pages_rows,
                cells_rows,
                dolphin_names,
            )

        for texture_index in front_texture_indices:
            desc = tex_palette.descriptors[texture_index]
            page_images[texture_index] = _export_one_page(
                OUTPUT_ROOT,
                entry_offset,
                tex_palette,
                desc,
                texture_index,
                'front',
                pages_rows,
                cells_rows,
                dolphin_names,
            )

        roster_pages = _export_roster_pages(OUTPUT_ROOT, tex_palette, dolphin_names, page_images)

        dat_file.seek(entry_offset)
        bank_bytes = dat_file.read(entry_length)
    finally:
        dat_file.close()

    icon_cells, icon_problems = _character_icon_cells(
        bank_bytes, {index: image.size for index, image in page_images.items()})
    for problem in icon_problems:
        _slogger.warning(f'Character icon skipped: {problem}', source='icons.export_icons')
    character_icons, icons_skipped = _export_character_icons(MODELS_ROOT, icon_cells, page_images)

    _copy_known_fallbacks(OUTPUT_ROOT)

    pages_rows.sort(key=lambda row: row['texture_index_dec'])
    cells_rows.sort(key=lambda row: (row['view'], row['texture_index_dec'], row['cell_dec']))

    pages_csv = os.path.join(OUTPUT_ROOT, DIR_METADATA, 'pages.csv')
    cells_csv = os.path.join(OUTPUT_ROOT, DIR_METADATA, 'cells.csv')
    manifest_json = os.path.join(OUTPUT_ROOT, DIR_METADATA, 'manifest.json')

    _write_pages_csv(pages_csv, pages_rows)
    _write_cells_csv(cells_csv, cells_rows)

    dolphin_names_txt = os.path.join(OUTPUT_ROOT, 'dolphin_icon_names.txt')
    _write_dolphin_names_txt(dolphin_names_txt, dolphin_names)

    manifest = {
        'generated_utc': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
        'source': {
            'dol_path': os.path.relpath(input_dol, ROOT_DIR).replace('\\', '/'),
            'dat_path': os.path.relpath(input_dat, ROOT_DIR).replace('\\', '/'),
            'dol_sha256': sha256_file(input_dol),
            'dat_sha256': sha256_file(input_dat),
            'group_index': 119,   # icon bank group
            'file_index': 2,     # icon bank entry within group
            'entry_offset': f'0x{entry_offset:X}',
            'entry_length': f'0x{entry_length:X}',
            'direct_texture_section_offset': f'0x{ICON_TEX_LOCAL_OFFSET:X}',
            'direct_texture_section_length': f'0x{entry_length - ICON_TEX_LOCAL_OFFSET:X}',
        },
        'tooling': {
            'exporter': 'SluggiesTools/Icons/export_icons.py',
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
            'palette_formats': [
                f'0x{value:02X}' for value in sorted(EXPECTED_PALETTE_FORMATS)
            ],
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
        'character_icons': character_icons,
    }

    with open(manifest_json, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)

    summary = (
        'Character-select icon export assets generated successfully:\n'
        f'  {os.path.relpath(OUTPUT_ROOT, ROOT_DIR)}\n'
        f'  Source DOL: {os.path.relpath(input_dol, ROOT_DIR)}\n'
        f'  Source DAT: {os.path.relpath(input_dat, ROOT_DIR)}\n'
        f'  Pages exported: {len(pages_rows)}\n'
        f'  Non-empty cells exported: {len(cells_rows)}\n'
        f'  Roster expansion icon pages: {roster_pages} (in {DIR_ROSTER}, named for Dolphin custom textures)\n'
        f'  Character icons: {len({row["dir"] for row in character_icons})} model folders '
        f'(<model>/{CHARACTER_ICON_DIR}/FrontIcon.png, SideIcon.png)\n'
        f'  Dolphin dump names: {os.path.relpath(dolphin_names_txt, ROOT_DIR)}'
    )
    _slogger.info(summary, source='icons.export_icons')
    for reason, dirs in sorted(icons_skipped.items()):
        _slogger.info(f'Character icons skipped ({reason}): dirs {", ".join(str(d) for d in dirs)}',
                      source='icons.export_icons')


if __name__ == '__main__':
    try:
        main()
    except ExportIconsError as exc:
        _slogger.error(str(exc), source='icons.export_icons')
        raise SystemExit(1)
    except FileNotFoundError as exc:
        _slogger.error(f'required external tool not found: {exc}', source='icons.export_icons')
        _slogger.error('Make sure Wiimm tools (wimgt) are installed and available on PATH.', source='icons.export_icons')
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        _slogger.error(f'external tool failed: {exc}', source='icons.export_icons')
        raise SystemExit(1)
