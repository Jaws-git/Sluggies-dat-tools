"""The "open slot" look: a new ID with no content of its own yet (presets, GUI "Clear slot").

An open slot shows the name ``SLOT_NAME`` and the "empty slot" portraits
``SLOT_ICON`` (``1_Input/_Icons``; drawn by ``write_slot_icons`` when missing,
so an edited one stays).
"""

import os

from PIL import Image, ImageDraw

try:
    from . import names
except ImportError:
    import names

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
ICON_DIR = os.path.join(ROOT, '1_Input', '_Icons')
SLOT_ICON = {'side': 'empty_slot_side.png', 'front': 'empty_slot_front.png'}
ICON_SIZE = (48, 51)
SLOT_NAME = {'en': 'Empty slot', 'fr': 'Emplacement vide', 'sp': 'Espacio vacío'}


def slot_portrait():
    """The "empty slot" portrait (48x51): a dark grey tile with a light border and the words EMPTY / SLOT."""
    img = Image.new('RGBA', ICON_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((1, 2, ICON_SIZE[0] - 2, ICON_SIZE[1] - 3), radius=7,
                           fill=(64, 64, 72, 255), outline=(200, 200, 210, 255), width=2)
    face = names.font(11)
    for word, y in (('EMPTY', 12), ('SLOT', 27)):
        x0, _y0, x1, _y1 = draw.textbbox((0, 0), word, font=face)
        draw.text(((ICON_SIZE[0] - (x1 - x0)) // 2 - x0, y - 3), word, font=face, fill=(255, 255, 255, 255))
    return img


def write_slot_icons(icon_dir: str = ICON_DIR) -> list[str]:
    """Draw the missing "empty slot" portraits into ``icon_dir``; returns the names written."""
    written = []
    os.makedirs(icon_dir, exist_ok=True)
    for name in SLOT_ICON.values():
        path = os.path.join(icon_dir, name)
        if not os.path.isfile(path):
            slot_portrait().save(path)
            written.append(name)
    return written


def slot_icon_paths(icon_dir: str = ICON_DIR) -> dict[str, str]:
    """``{view: path}`` of the "empty slot" portraits (drawn first when missing)."""
    write_slot_icons(icon_dir)
    return {view: os.path.join(icon_dir, name) for view, name in SLOT_ICON.items()}
