"""Character icons: each character's own portraits as PNGs in its model folder (``start.py --export-icons``).

Writes ``icon/FrontIcon.png`` and ``icon/SideIcon.png`` (48x51 RGBA) into
each character's high-poly model folder, next to its ``tex/``. The owner of a
portrait is found through the icon bank's side and front source tables
(layout elements 1/2, key = character ID, record +0x06 = resource row); a
character's model folder is its ID + 0x12. Pages are decoded with
``gx_decode``, so no external tool is needed.
"""

import argparse
import os
import re
import struct
import sys

from PIL import Image

ICONS_DIR = os.path.dirname(__file__)
TOOLS_DIR = os.path.normpath(os.path.join(ICONS_DIR, '..'))
ROOT_DIR = os.path.normpath(os.path.join(TOOLS_DIR, '..'))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

# Step 2.2 – Initialize universal logger in child process.
import slogger as _slogger
_slogger.configure()

from helper import bti
from Icons import gx_decode, layout2d

# The icon bank's DOL directory record (group 119, entry 2): 12 x u32, for each of EN/SP/FR
# [dat_fname_ptr, length, offset, alloc]. The roster expansion moves the bank but keeps this record.
ICON_ENTRY_DOL_OFFSET = 0x68DE88

GRID_CELL_WIDTH = 48
GRID_CELL_HEIGHT = 51

INPUT_DOL = os.path.join(ROOT_DIR, '1_Input', 'main.dol')
INPUT_DAT = os.path.join(ROOT_DIR, '1_Input', 'dt_na.dat')

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


class ExportIconsError(Exception):
    pass


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def _extract_icon_entry(dol_path):
    """(offset, length) of the icon bank in dt_na.dat: the English slot of the DOL record."""
    with open(dol_path, 'rb') as dol:
        dol.seek(ICON_ENTRY_DOL_OFFSET, 0)
        words = [bti(dol.read(4)) for _ in range(12)]
    en_offset, en_length = words[2], words[1]
    if en_offset <= 0 or en_length <= 0:
        raise ExportIconsError(
            f'invalid icon entry at DOL offset 0x{ICON_ENTRY_DOL_OFFSET:X}: '
            f'offset=0x{en_offset:X}, length=0x{en_length:X}'
        )
    return en_offset, en_length


def _read_bank(dol_path, dat_path):
    offset, length = _extract_icon_entry(dol_path)
    with open(dat_path, 'rb') as dat:
        dat.seek(offset)
        data = dat.read(length)
    if len(data) != length:
        raise ExportIconsError(f'the icon bank 0x{offset:08X}+0x{length:X} runs past the end of {dat_path}')
    return data


def _page_descriptors(bank_bytes):
    """{page: descriptor fields} of every page whose format ``gx_decode`` reads."""
    count = struct.unpack_from('>H', bank_bytes, layout2d.TEXTURE_OFFSET_BASE)[0]
    table = layout2d.TEXTURE_OFFSET_BASE + 4
    pages = {}
    for page in range(count):
        d = table + page * layout2d.TEXTURE_DESCRIPTOR_SIZE
        image_at, palette_at, height, width = struct.unpack_from('>IIHH', bank_bytes, d)
        fmt = bank_bytes[d + 0x17]
        if fmt not in gx_decode.FORMATS:
            continue
        pages[page] = {'format': fmt, 'width': width, 'height': height, 'image_at': image_at,
                       'palette_at': palette_at, 'entries': struct.unpack_from('>H', bank_bytes, d + 0x18)[0],
                       'palette_format': bank_bytes[d + 0x1A]}
    return pages


def _decode_page(bank_bytes, desc):
    """One page as an RGBA ``PIL.Image``."""
    base = layout2d.TEXTURE_OFFSET_BASE
    length = gx_decode.data_length(desc['format'], desc['width'], desc['height'])
    image = bank_bytes[base + desc['image_at']:base + desc['image_at'] + length]
    palette = b''
    if desc['format'] == gx_decode.C8:
        palette = bank_bytes[base + desc['palette_at']:base + desc['palette_at'] + 2 * desc['entries']]
    pixels = gx_decode.decode(desc['format'], image, desc['width'], desc['height'], palette, desc['palette_format'])
    return Image.fromarray(pixels, 'RGBA')


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
                problems.append(f'0x{char_id:02X} {view}: page 0x{page:02X} is not a decodable icon page')
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
    model folder, and remove ones whose character has no own key in this bank. ``page_images`` maps page to
    its RGBA image. Returns ``(written, skipped)``: one row per PNG, and {reason: [dir index, ...]}."""
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


def export_character_icons(dol_path, dat_path, models_root=MODELS_ROOT):
    """Read the icon bank the DOL points at and write every character's icons. Returns ``(written, skipped)``."""
    bank_bytes = _read_bank(dol_path, dat_path)
    pages = _page_descriptors(bank_bytes)
    cells, problems = _character_icon_cells(
        bank_bytes, {page: (d['width'], d['height']) for page, d in pages.items()})
    for problem in problems:
        _slogger.warning(f'Character icon skipped: {problem}', source='icons.export_icons')
    used = {page for views in cells.values() for page, _rect in views.values()}
    page_images = {page: _decode_page(bank_bytes, pages[page]) for page in sorted(used)}
    return _export_character_icons(models_root, cells, page_images)


def main():
    parser = argparse.ArgumentParser(
        description='Write each character\'s FrontIcon.png/SideIcon.png into its model folder in 2_Output_Models.'
    )
    parser.add_argument('--dol-path', default=INPUT_DOL, help='path to main.dol source')
    parser.add_argument('--dat-path', default=INPUT_DAT, help='path to dt_na.dat source')
    args = parser.parse_args()

    input_dol = os.path.normpath(args.dol_path)
    input_dat = os.path.normpath(args.dat_path)
    for path, what in ((input_dol, 'DOL'), (input_dat, 'DAT')):
        if not os.path.exists(path):
            raise ExportIconsError(f'missing input {what}: {path}')

    _slogger.info(f'Exporting character icons from {os.path.relpath(input_dol, ROOT_DIR)} / '
                  f'{os.path.relpath(input_dat, ROOT_DIR)}...', source='icons.export_icons')
    written, skipped = export_character_icons(input_dol, input_dat)
    _slogger.info(f'Character icons: {len({row["dir"] for row in written})} model folders '
                  f'(<model>/{CHARACTER_ICON_DIR}/FrontIcon.png, SideIcon.png)', source='icons.export_icons')
    for reason, dirs in sorted(skipped.items()):
        _slogger.info(f'Character icons skipped ({reason}): dirs {", ".join(str(d) for d in dirs)}',
                      source='icons.export_icons')


if __name__ == '__main__':
    try:
        main()
    except (ExportIconsError, layout2d.Layout2dError, gx_decode.GxDecodeError) as exc:
        _slogger.error(str(exc), source='icons.export_icons')
        raise SystemExit(1)
