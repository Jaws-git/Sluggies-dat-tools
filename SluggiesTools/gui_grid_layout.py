"""Character grid tab, shared by its modules: the sizes, button labels, setting names and colours of the
grid, the ``_draws`` lock for the methods that rebuild it, and small helpers (``_rgba``, ``_inside``,
``_text_height``, ``_fit``, ``swatches_per_row``).

``gui_character_grid`` and the mixin modules (``gui_options_tabs``, ``gui_grid_textures``,
``gui_grid_dialogs``, ``gui_grid_actions``, ``gui_grid_staging``) import from here; this module imports
none of them. ``gui_character_grid`` re-exports every name.
"""

import functools
import os
import subprocess

import dearpygui.dearpygui as dpg
import numpy as np

import gui_grid


ICON = (48, 51)                  # the game's portrait size
GRID_SCALE, SWATCH_SCALE, SLOT_SCALE = 1.5, 2, 3
FRAME = 4                        # image button frame padding (each side)
GRID_ICON = (round(ICON[0] * GRID_SCALE), round(ICON[1] * GRID_SCALE))
CELL = (104, GRID_ICON[1] + 2 * FRAME + 24)
CELL_GAP = 2                     # horizontal space between grid cells
ROW_PITCH = CELL[1] + 8          # cell height plus the spacing Dear PyGui puts between a cell's items and rows
OFF_GRID_GAP = 24                # space between the grid and the column beside it (stock Luigi, item tiles)
# Vertical gap after every text item in the whole GUI (gui.py's global theme; ImGui's default is 4). It spaces
# separate add_text lines only: the lines of one wrapped text stay at the font's line height. Recommended
# maximum: 10 -- above that, dialogs with many lines scroll early and the grid captions drift apart.
TEXT_SPACING_Y = 2
SWATCH = (128, SWATCH_SCALE * ICON[1] + 2 * FRAME + 2 * (18 + TEXT_SPACING_Y))
PAD = 16
GAP = 8
BOX_MARGIN = 20                  # free space above and below a level's box
LINE = 22 + TEXT_SPACING_Y       # text line height (Segoe UI 16 pt, with spacing)
SLOT_W = 720                     # level 2 may be wider than level 1: each level is its own window
PORTRAIT = (SLOT_SCALE * ICON[0], SLOT_SCALE * ICON[1])
BUTTON_H = 32
EQUIP_BLOCK = 3 * LINE + BUTTON_H + 2 * GAP     # the Equipment row: heading, tile label + state, buttons
EQUIP_BUTTON_W = 76
BADGE_FONT = 9                   # the grid's slot-count number, in portrait pixels (scaled with the texture)
RENAME, SELECT, CLEAR, DISCARD = 'Rename...', 'Select .sluggie...', 'Clear slot', 'Discard pending'
SELECT_LOW = 'Select lowpoly (L_) .sluggie...'   # beside Select .sluggie...: a Low model pick (joins a pending High)
IMPORT_SLG = 'Import .slgmodel...'   # below them: a Sluggers Characters patcher character file
SLGMODEL_DIR_SETTING = 'grid_last_slgmodel_dir'  # the GUI settings key: the folder the last character file came from
STATS, VOICE = 'Adopt stats...', 'Voice...'
ANIM = 'Replace animation...'       # on the row below, under Adopt stats...
SIZE = 'Set size...'                # the row below's first column, under Rename...
ANIM_DIR_SETTING = 'grid_last_anim_dir'   # the GUI settings key: the folder the last .anm came from
LOAD_PACKAGE = 'Import .sluggiezip package...'   # above Import .slgmodel...: every aspect of a package at once
PACKAGE_DIR_SETTING = 'grid_last_package_dir'    # the GUI settings key: the folder the last package was saved / loaded in
SLUGGIE_DIR_SETTING = 'grid_last_sluggie_dir'   # the GUI settings key: the folder the last slot/equipment .sluggie came from
IMAGE_DIR_SETTING = 'grid_last_image_dir'       # the GUI settings key: the folder the last portrait image came from
PACK_DIR_SETTING = 'grid_last_pack_dir'         # the GUI settings key: the folder the last roster pack was loaded / saved in
ITEM_DIR_SETTING = 'grid_last_item_dir'         # the GUI settings key: the folder the last item model .sluggie came from
STADIUM_DIR_SETTING = 'grid_last_stadium_dir'   # the same for the last stadium model .sluggie
MODEL_FILE_DIRS = {gui_grid.ITEM: ITEM_DIR_SETTING, gui_grid.STADIUM: STADIUM_DIR_SETTING}
EXTRA_ROWS = 3                      # the column right of the grid: Luigi, the item tile, the stadium tile
TILE_ICONS = {gui_grid.ITEM: ('banana', gui_grid.banana_icon), gui_grid.STADIUM: ('flag', gui_grid.flag_icon)}
VOICE_ALL = 'Set voice (all)...'    # the square level (colour wheel): the voice reaches every member
SLOT_BUTTONS = (RENAME, STATS, VOICE, CLEAR)     # the row below: Set size..., Replace animation..., Discard pending
EQUIP_PAIR_W = 2 * EQUIP_BUTTON_W - 8 + 4   # a tile's Select... + Reset buttons and the gap between them
SLOT_BUTTON_W = EQUIP_PAIR_W      # the bottom row's buttons match that, so they align under the tiles
SELECT_W = (SLOT_W - 2 * PAD) // 3  # Select .sluggie... and Select lowpoly (L_) .sluggie... above the Equipment row
VOICE_TIP = ('Stage another voice for this square (a stock square: its whole species; a new square: its members '
             'without a wheel). "Patch Game" writes the pending edits.')
CHAR_VOICE_TIP = ('Stage a voice of this slot\'s own: another stock character\'s voice or a custom voice pack '
                  '(Voices tab), whatever its square speaks with. Its clips go into the slot\'s voice bank in the '
                  'sound archive (needs 1_Input/sound_NA/MY2.brsar). "Patch Game" writes the pending edits.')
FONT_REL = os.path.join('SluggiesTools', 'Roster', 'fonts', 'OpenSans.ttf')   # the name plate's font
CONFIRM_W = 680
PATCH_W = 230                    # the Patch Game button: fits "Patch Game & copy files (NNN)" until text sizes are known
DIALOG_LINES = 24                # a dialog with more lines scrolls
BUSY_TEXT = 'A command is running (see the log)...'
POPUP_SPINNER_RADIUS = 4         # the spinner over a level's box while a command runs
POPUP_SPINNER_PX = 64            # its size: radius x the font size (16 pt Segoe UI; 60 with Open Sans)
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_WARN = (255, 210, 90, 255)
_DIM = (230, 230, 230, 255)
_EMPTY = (110, 110, 110, 255)
_ERROR = (255, 120, 110, 255)
_OK = (120, 220, 140, 255)
_PENDING = (255, 170, 70, 255)
_STATS = (90, 215, 110, 255)     # pending Stat Editor values (bridged in)
_GECKO = (235, 60, 60, 255)      # pending edits that break Gecko code compatibility (gecko_compat)
BORDER = 3                       # the marker borders' width
_CHANGED = (110, 170, 255, 255)
_COPIED = (80, 215, 215, 255)
_LINE_COLORS = {gui_grid.TEXT: _DIM, gui_grid.WARN: _WARN, gui_grid.ERROR: _ERROR, gui_grid.OK: _OK}


def _draws(method):
    """For the methods that rebuild the grid or its pop-outs. The main thread rebuilds when a read or a chain
    ends, Dear PyGui's callback thread on a click, Esc or a resize; overlapping, they deleted each other's
    items, collided on the fixed tags or left a dim overlay nothing removed. One at a time now."""
    @functools.wraps(method)
    def locked(self, *args, **kwargs):
        with self.draw_lock:
            return method(self, *args, **kwargs)
    return locked


def _rgba(image):
    """An RGBA image as Dear PyGui texture data (floats 0-1)."""
    return np.asarray(image.convert('RGBA'), dtype=np.float32).ravel() / 255.0


def _source_thumbnail(preview):
    return None if preview.source is None else gui_grid.source_thumbnail(preview.source.image)


def swatches_per_row(count: int, viewport_width: int) -> int:
    """Swatches per row so the box fits the window (40 px margin), rows balanced (10 -> 5 + 5)."""
    if count <= 0:
        return 1                         # a square with no members: one (empty) row, no division by zero
    fit = max(1, (viewport_width - 40 - 2 * PAD + GAP) // (SWATCH[0] + GAP))
    rows = -(-count // fit)
    return -(-count // rows)


def _inside(item, pos) -> bool:
    if not dpg.does_item_exist(item):
        return False
    x, y = dpg.get_item_pos(item)
    w, h = dpg.get_item_rect_size(item)
    return x <= pos[0] < x + w and y <= pos[1] < y + h


def _text_height(text: str, wrap: int) -> int:
    """A wrapped text's height in px: measured with the GUI font, else estimated (about 7 px a character)."""
    try:
        size = dpg.get_text_size(text, wrap_width=wrap)
    except Exception:   # no context / fonts not built yet (tests, first frame)
        size = None
    if size and size[1] > 0:
        return int(size[1] + 0.5)
    return (LINE - TEXT_SPACING_Y) * (1 + len(text) * 7 // wrap)


def _fit(text: str, width: int) -> str:
    """Shorten a label to roughly fit a button (about 7 px a character)."""
    limit = max(4, (width - 12) // 7)
    return text if len(text) <= limit else text[:limit - 1] + '.'
