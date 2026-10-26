"""The GUI's "Character grid" tab: the exhibition draft grid as 3_Output_Dat holds it.

Reading runs ``start.py --roster-state`` in a background process (spinner
meanwhile, the rest of the GUI stays usable). Clicking a square pops it out:
the grid darkens and the square's slots show in a row. Clicking a slot pops
that out over the square. A click outside the popped-out box, or ``Esc``,
goes back one level. One-member squares open straight at the slot. The
logic lives in ``gui_grid`` (no Dear PyGui needed); this module only draws.

Each level is a borderless window over a full-viewport dim window, so the
dim layers stack. The dim windows never come to the front when clicked.

Portraits are the crops ``--roster-state`` cut from the output's icon bank
(``_gui/icons``): front portraits on the grid, side portraits on
the swatch row (the game uses them on wheels), both enlarged on the slot
level. They are scaled up by whole factors with nearest-neighbour, so the
pixels stay sharp (the grid's 1.5x draws the 2x texture smaller).
Portraits that are not the slot's own (a lower key, the template's, the Mii
or "?" icon) are marked. The textures are released and rebuilt on every
re-read.

Edits are staged. The slot level's **Rename...** (a text box
with a live "fits the name plate" line), **Select .sluggie...** and **Select
lowpoly (L_) .sluggie...** (a Low model only; it joins the slot's pending High
pick, so both are patched in one go), **Clear
slot**, **Stats...** and **Voice...**, **Set size...** (below Rename...: one factor on the character's size
scale, 1 = its normal size, written as a one-character stat edit file, ``gui_grid.size_doc``, and staged like the
Stat Editor's), and the square level's **Set voice (all)...** (colour
wheels only; both pick from a list) and the Equipment row's tiles (Bat, Left glove, Right glove, Extra bat;
each has **Select...** for a bat/glove ``.sluggie`` and **Reset**) and **Replace animation...** (below Adopt stats...:
one or more ``.anm`` banks, e.g. the Blender add-on's export, staged together; each whole bank is replaced) run a staging check
(``start.py --apply-slots edits.json
--dry-run``: the pending edits plus the new one, the new edit's build check),
then a confirm dialog with what the edit does (``gui_grid.slot_dialog``); Stage
adds it to the pending list (``gui_grid.PendingEdits``, GUI memory only).
The slot level's two portraits are image buttons too: a click picks
an image file, and the icon dialog shows it, the 48x51 result (fit mode,
"Trim transparent border", redrawn in-process with ``Roster/icon_import``) and
the slot's other view; OK writes the result into
``_gui/slot/staged_icons`` and stages an ``icon`` edit on it.
Slots and squares with pending edits get an orange border, the slot level
lists the pending edits and previews pending portraits. Stat edits bridged in
from the Stat Editor (staged game-wide) mark the characters their file names
with a green border; a slot with both gets a border orange on the left half
and green on the right (baked into the portrait texture: a Dear PyGui frame
border has one colour). While the output game is Gecko compatible
(``gecko_compat``), slots whose pending edits break that compatibility get red
in place of orange, and a red line in their tooltips and the slot level
(``PendingEdits.gecko``, from the staging check: ``slot_cli.gecko_check``); slots the pending edits leave over
the player memory get the same red marks (``PendingEdits.memory``, from the staging check's ``memory_guard``
record). **Patch Game (N)**
runs the full dry run, shows one summary (``gui_grid.summary_dialog``) and on
its Patch Game button the chain itself (one roster rebuild at most); on success the list is
cleared, on a failure it is kept. **Discard pending** / **Discard all** drop
edits. While a command runs the edit buttons are disabled and a spinner
shows over the open level's box; the re-read afterwards reopens the same
slot. Clicks and ``Esc`` do not move the levels while the file dialog or a
dialog is up (a running check alone does not hold them).

**Save roster...** writes the game's whole roster into a roster pack
(``start.py --save-roster``; with pending edits it asks first: they are not
in the game yet). **Load roster...** checks a pack and shows the per-slot
differences (``--load-roster FILE --dry-run``, ``gui_grid.load_dialog``;
"Only differing slots" filters them); Stage makes the load the one pending
edit (other pending edits are discarded first, it asks; slot edits wait
until it is written or discarded), and Patch Game checks the pack again and
loads it (``--load-roster FILE``), replacing the whole roster. The pack last saved or
loaded is the session's reference: slots that differ from it get a blue
border and a "changed since" line.

A column right of the grid holds the squares on no cell: Luigi (top row),
the item tile (second row) and the stadium tile (third row). On the stock
grid Luigi has no square (the game hands him a captain's square at runtime);
the reader lists his family as an off-grid square so his slots stay
reachable.

The item tile (a banana; "Edit item models") stages ``.sluggie`` files of
models outside the character and stadium directories (items, map objects,
props): ``item`` edits, game-wide like stat edits, which Patch Game writes as
the command line menu's plain patch (``--patch FILE``). The stadium tile (a
flag; "Patch stadiums") does the same for stadium models (``stadium`` edits).
With models pending a click lists them (Add..., Discard); else it opens the
picker straight away.

Copy / paste: right-clicking a one-member square on the grid or a swatch on
a colour wheel opens a menu with **Copy** and **Paste** (a square with
several characters shows only a disabled line). Copy earmarks the character
(teal border); Paste stages a ``copy`` edit
that makes the clicked slot a clone of it as the game holds it
(``slot_plan.plan_copy``), with the usual staging check and confirm dialog.
**Export as .sluggie** (below them) runs ``start.py --export-slot`` right
away (it only reads the game): the slot as the game holds it becomes a
``2_Output_Assets/Custom <name> NN`` folder of ``.sluggie`` + ``.glb``
files (``Roster/slot_export.py``);
pending edits of the slot are not in it (asks first).
**Export to .sluggiezip** (below it) asks where to save, then runs ``start.py --export-sluggiezip``: the slot as
the game holds it (models, bats and gloves, animations, portraits, name, stats, own voice) becomes one character
package (``Roster/slot_zip.py``). The slot level's **Import .sluggiezip package** (light blue, above Import .slgmodel...)
picks one, extracts it (``slot_zip.stage``) and stages every aspect it holds onto the slot in one staging check;
the confirm dialog (``gui_grid.package_dialog``) lists the staged aspects and the skipped ones (missing from the
package, or not for this slot). A voice pack the load created is removed again when it is not staged.
One shared popup window and one shared right-click handler serve every
portrait button (``dpg.popup`` would leave a window and a handler registry
behind on every redraw).
"""

import math
import os
import subprocess
import threading

import dearpygui.dearpygui as dpg
import numpy as np
from PIL import Image

import gecko_compat
import gui_grid
import memory_guard
import native_dialog

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


class CharacterGridTab:
    def __init__(self, app):
        self.app = app                     # gui.SluggiesGui: command prefix, root, queue, log
        self.loader = gui_grid.StateLoader(os.path.join(app.root_dir, gui_grid.STATE_REL))
        self.nav = gui_grid.GridNav()
        self.popups = []                   # [(dim window, box window)], bottom first
        self.textures = {}                 # (crop path, scale) -> texture tag; released on every re-read
        self.slot_buttons = []             # the slot level's working buttons (disabled while a command runs)
        self._name_handlers = []           # click handlers of the slot level's name (deleted with the levels)
        self.action = None                 # the action in progress (file dialog to confirm dialog), or None
        self.confirm = None                # the open dialog window
        self._after_save = None            # runs once the pack chosen in the file dialog is saved
        self.confirm_default = None        # what Enter does in it (its OK / Close button), or None
        self.stat_offer = None             # the stat edit file being offered (a Set size... file too), or None
        self.pending = gui_grid.PendingEdits()
        self.work = None                   # what the tab's own running command does (status text), or None
        self.edits_path = os.path.join(app.root_dir, gui_grid.EDITS_REL)
        self.plan_path = os.path.join(app.root_dir, gui_grid.SLOT_PLAN_REL)
        self.pack_plan_path = os.path.join(app.root_dir, gui_grid.PACK_PLAN_REL)
        self.switch_plan_path = os.path.join(app.root_dir, gui_grid.SWITCH_PLAN_REL)
        self.pack_dir = os.path.join(app.root_dir, gui_grid.PACK_DIR_REL)
        self.reference = None              # gui_grid.Reference: the pack last saved / loaded (session only)
        self.gecko_compatible = False      # the output main.dol still works with stock-game Gecko codes (each read)
        self.close_play_shown = None       # the close play level the slider was last set to (CPU vs CPU tab)
        self.mercy_rule_shown = None       # the mercy rule lead the input was last set to (Extras tab)
        self.copied = None                 # the character Copy earmarked (session only), or None
        self._menu_targets = {}            # portrait button -> the square's members, for the right-click menu
        self.on_pending_changed = []       # callbacks other tabs register (the Voices tab shows pending clips)
        self.on_state_read = []            # callbacks run after every successful read
        self._package = None               # (path, slot_zip.Staged) of the package being checked, until staged / closed

    # ------------------------------------------------------------------ build
    def build(self):
        with dpg.tab(label='Character grid', tag='grid_tab'):
            with dpg.group(horizontal=True):
                self.refresh_button = dpg.add_button(label='Refresh', callback=lambda: self.request_read())
                deploy = bool(self.app.settings.get(gui_grid.DEPLOY_SETTING, False))
                dpg.add_button(label=gui_grid.patch_label(0, deploy), tag='grid_patch_game', enabled=False,
                               width=PATCH_W, callback=lambda: self._on_patch_game())
                dpg.bind_item_theme('grid_patch_game', 'primary_theme')
                with dpg.tooltip('grid_patch_game'):
                    dpg.add_text('Write every pending edit into 3_Output_Dat in one go (one roster rebuild at most); '
                                 'a summary shows first.', wrap=420)
                dpg.add_button(label='Discard all', tag='grid_discard_all', enabled=False,
                               callback=lambda: self._on_discard_all())
                dpg.add_spacer(width=12)
                dpg.add_button(label='Save roster...', tag='grid_save_pack', callback=lambda: self._on_save_pack())
                with dpg.tooltip('grid_save_pack'):
                    dpg.add_text('Save the whole roster of 3_Output_Dat (grid, names, voices, stats, own model '
                                 'directories, patched models, portraits) into one roster pack file.', wrap=420)
                dpg.add_button(label='Load roster...', tag='grid_load_pack', callback=lambda: self._on_load_pack())
                with dpg.tooltip('grid_load_pack'):
                    dpg.add_text('Stage a roster pack: shows what differs from the game first; "Patch Game" then '
                                 'replaces the whole roster of 3_Output_Dat with it.', wrap=420)
                dpg.add_spacer(width=12)
                dpg.add_checkbox(label=gui_grid.DEPLOY_LABEL, tag='grid_deploy',
                                 default_value=deploy,
                                 callback=lambda _sender, value: self._on_deploy_toggle(value))
                with dpg.tooltip('grid_deploy'):
                    dpg.add_text('After a successful Patch Game, copy dt_na.dat, main.dol, fst.bin and the sound '
                                 'files (sound_NA) from 3_Output_Dat into the game directory '
                                 '(3_Output_Dat/CopyFilesToGameDir.bat). The '
                                 'game directory is asked for the first time and kept in 3_Output_Dat/sluggiespath.',
                                 wrap=420)
                dpg.add_spacer(width=12)
                dpg.add_loading_indicator(tag='grid_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='grid_status')
            dpg.add_text('', tag='grid_note', color=_WARN, wrap=900)
            dpg.add_text('', tag='grid_reference', color=_CHANGED, wrap=900, show=False)
            dpg.add_child_window(tag='grid_cells', border=False, horizontal_scrollbar=True)
        with dpg.theme(tag='grid_dim_theme'):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (0, 0, 0, 120))
                dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
        with dpg.theme(tag='grid_box_theme'):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (38, 40, 46, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Border, (120, 200, 140, 255))
                dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 2)
                dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 6)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, PAD, PAD)
        with dpg.theme(tag='grid_pending_theme'):
            for kind in (dpg.mvImageButton, dpg.mvButton):
                with dpg.theme_component(kind):
                    dpg.add_theme_color(dpg.mvThemeCol_Border, _PENDING)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, BORDER)
        with dpg.theme(tag='grid_gecko_theme'):              # pending edits that break Gecko compatibility
            for kind in (dpg.mvImageButton, dpg.mvButton):
                with dpg.theme_component(kind):
                    dpg.add_theme_color(dpg.mvThemeCol_Border, _GECKO)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, BORDER)
        with dpg.theme(tag='grid_stats_theme'):
            for kind in (dpg.mvImageButton, dpg.mvButton):
                with dpg.theme_component(kind):
                    dpg.add_theme_color(dpg.mvThemeCol_Border, _STATS)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, BORDER)
        with dpg.theme(tag='grid_both_theme'):               # pending edits and stat edits
            with dpg.theme_component(dpg.mvImageButton):     # the split border is in the texture (_split_texture)
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0)
            with dpg.theme_component(dpg.mvButton):          # no portrait: orange border, green label
                dpg.add_theme_color(dpg.mvThemeCol_Border, _PENDING)
                dpg.add_theme_color(dpg.mvThemeCol_Text, _STATS)
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, BORDER)
        with dpg.theme(tag='grid_both_gecko_theme'):         # the same, with a Gecko compatibility break
            with dpg.theme_component(dpg.mvImageButton):
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0)
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Border, _GECKO)
                dpg.add_theme_color(dpg.mvThemeCol_Text, _STATS)
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, BORDER)
        with dpg.theme(tag='grid_changed_theme'):
            for kind in (dpg.mvImageButton, dpg.mvButton):
                with dpg.theme_component(kind):
                    dpg.add_theme_color(dpg.mvThemeCol_Border, _CHANGED)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 3)
        with dpg.theme(tag='grid_copied_theme'):
            for kind in (dpg.mvImageButton, dpg.mvButton):
                with dpg.theme_component(kind):
                    dpg.add_theme_color(dpg.mvThemeCol_Border, _COPIED)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 3)
        with dpg.window(tag='grid_context', popup=True, show=False, autosize=True, no_saved_settings=True):
            pass
        with dpg.theme(tag='grid_context_theme'):            # no box: the menu's buttons float by themselves
            with dpg.theme_component(dpg.mvAll):
                for col in (dpg.mvThemeCol_WindowBg, dpg.mvThemeCol_PopupBg, dpg.mvThemeCol_Border,
                            dpg.mvThemeCol_BorderShadow):
                    dpg.add_theme_color(col, (0, 0, 0, 0))
                dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0)
                dpg.add_theme_style(dpg.mvStyleVar_PopupBorderSize, 0)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 4, 4)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 10, 6)
            with dpg.theme_component(dpg.mvButton):          # Dear PyGui's default blues, opaque (nothing behind)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (29, 151, 236, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (0, 119, 200, 255))
            with dpg.theme_component(dpg.mvButton, enabled_state=False):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (60, 62, 68, 235))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (60, 62, 68, 235))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (60, 62, 68, 235))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (150, 150, 150, 255))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 10, 6)
        dpg.bind_item_theme('grid_context', 'grid_context_theme')
        with dpg.item_handler_registry(tag='grid_context_handlers'):
            dpg.add_item_clicked_handler(button=dpg.mvMouseButton_Right, callback=self._on_context)
        dpg.bind_item_theme('grid_discard_all', 'danger_theme')
        with dpg.theme(tag='grid_warn_theme'):               # like primary_theme, in yellow (Discard pending)
            for state, colors in (
                (True, ((dpg.mvThemeCol_Button, (200, 160, 30, 255)),
                        (dpg.mvThemeCol_ButtonHovered, (225, 185, 50, 255)),
                        (dpg.mvThemeCol_ButtonActive, (165, 130, 20, 255)),
                        (dpg.mvThemeCol_Text, (25, 25, 25, 255)))),
                (False, ((dpg.mvThemeCol_Button, (82, 78, 62, 255)),
                         (dpg.mvThemeCol_Text, (150, 150, 150, 255)))),
            ):
                with dpg.theme_component(dpg.mvButton, enabled_state=state):
                    for col, value in colors:
                        dpg.add_theme_color(col, value)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
        with dpg.theme(tag='grid_light_blue_theme'):         # whole-character files: .sluggiezip and .slgmodel
            for state, colors in (
                (True, ((dpg.mvThemeCol_Button, (110, 180, 235, 255)),
                        (dpg.mvThemeCol_ButtonHovered, (140, 200, 245, 255)),
                        (dpg.mvThemeCol_ButtonActive, (85, 155, 215, 255)),
                        (dpg.mvThemeCol_Text, (20, 28, 38, 255)))),
                (False, ((dpg.mvThemeCol_Button, (66, 76, 88, 255)),
                         (dpg.mvThemeCol_Text, (150, 150, 150, 255)))),
            ):
                with dpg.theme_component(dpg.mvButton, enabled_state=state):
                    for col, value in colors:
                        dpg.add_theme_color(col, value)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
        dpg.add_texture_registry(tag='grid_textures')
        self.app.add_file_dialog('grid_sluggie_dialog', ('.sluggie',))
        self.app.add_file_dialog('grid_image_dialog', ('Images{' + ','.join(gui_grid.IMAGE_EXTENSIONS) + '}', '.*'))
        self.app.add_file_dialog('grid_anim_dialog', ('.anm', '.*'))
        for tag in ('grid_package_save_dialog', 'grid_package_load_dialog'):
            self.app.add_file_dialog(tag, (gui_grid.PACKAGE_EXTENSION, '.*'))
        dpg.add_texture_registry(tag='grid_dialog_textures')    # the icon dialog's; released when it closes
        for tag in ('grid_pack_save_dialog', 'grid_pack_load_dialog'):
            self.app.add_file_dialog(tag, (gui_grid.PACK_EXTENSION,), color=(120, 180, 255, 255),
                                     default_filename='roster')
        with dpg.handler_registry():
            dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=self._on_mouse_click)
            dpg.add_key_press_handler(dpg.mvKey_Escape, callback=self._on_escape)
            # on release, so a held Enter cannot also accept the next dialog
            dpg.add_key_release_handler(dpg.mvKey_Return, callback=self._on_enter)
            dpg.add_key_release_handler(dpg.mvKey_NumPadEnter, callback=self._on_enter)

    def build_cpu_vs_cpu(self):
        """The Gameplay tab's "CPU vs CPU" inner tab: the buttons stage game option edits ("Patch Game" writes
        them). Its state comes from the grid's read, so it is built here (inside the Gameplay tab's tab bar)."""
        with dpg.tab(label='CPU vs CPU', tag='cpu_vs_cpu_tab'):
            dpg.add_text('The buttons stage the change; "Patch Game" writes every pending edit.', color=_DIM)
            dpg.add_button(label='Discard pending CPU vs CPU edits', tag='cpu_vs_cpu_discard', enabled=False,
                           callback=lambda: self._on_cpu_vs_cpu_discard())
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_button(label=gui_grid.CPU_VS_CPU_ENABLE, tag='grid_cpu_vs_cpu',
                               callback=lambda: self._on_cpu_vs_cpu())
                with dpg.tooltip('grid_cpu_vs_cpu'):
                    dpg.add_text('Stage CPU vs CPU on (hold Minus on controller starting a match). Turning it off '
                                 'also turns off its management and the close play odds.', wrap=420)
                dpg.add_text('', tag='grid_cpu_vs_cpu_status')
            with dpg.group(horizontal=True):
                dpg.add_button(label=gui_grid.CPU_MANAGEMENT_ENABLE, tag='grid_cpu_management',
                               callback=lambda: self._on_cpu_management())
                with dpg.tooltip('grid_cpu_management'):
                    dpg.add_text('In a CPU vs CPU match, controller 1 manages the fielding team (pause to make '
                                 'defensive changes). Needs CPU vs CPU on (or staged on) first.', wrap=420)
                dpg.add_text('', tag='grid_cpu_management_status')
            dpg.add_spacer(height=4)
            dpg.add_text('Close play odds (CPU vs CPU)')
            with dpg.group(horizontal=True):
                dpg.add_slider_int(tag='grid_close_play_odds', width=160, min_value=0, max_value=100,
                                   default_value=gui_grid.CLOSE_PLAY_GECKO, format='%d%%',
                                   callback=lambda _s, value: self._on_close_play_slider(value))
                with dpg.tooltip('grid_close_play_odds'):
                    dpg.add_text('Port of "CPU Closeplay Overhaul" [C^2]. In CPU vs CPU matches the close plays get '
                                 'these odds (steps of 10%; 50% is the Gecko code); a player with the baserunning '
                                 'ability always gets 100%. Human matches keep the stock close plays.', wrap=420)
                    dpg.add_text('Needs CPU vs CPU on (or staged on) first. It adds code to main.dol, like CPU vs '
                                 'CPU itself.', wrap=420)
                dpg.add_button(label=gui_grid.CLOSE_PLAY_STAGE, tag='grid_close_play_stage',
                               callback=lambda: self._on_close_play(True))
                dpg.add_button(label=gui_grid.CLOSE_PLAY_DISABLE, tag='grid_close_play_disable',
                               callback=lambda: self._on_close_play(False))
                dpg.add_text('', tag='grid_close_play_status')
            dpg.add_spacer(height=4)
            dpg.add_text('CPU fixes (every match; no added code, so Gecko codes keep working)')
            for key, _label, tip in gui_grid.CPU_FIXES:
                with dpg.group(horizontal=True):
                    dpg.add_button(label=gui_grid.cpu_fix_button(None, None, key)[0], tag=f'grid_fix_{key}',
                                   width=260, callback=lambda _s, _a, u: self._on_cpu_fix(u), user_data=key)
                    with dpg.tooltip(f'grid_fix_{key}'):
                        dpg.add_text(tip, wrap=420)
                    dpg.add_text('', tag=f'grid_fix_{key}_status')
            # the tool's CPU vs CPU patch and Dolphin's CPU vs CPU Gecko codes must not both be active
            dpg.add_text('Disable all CPU vs CPU Gecko codes in Dolphin now!', tag='grid_cpu_gecko_warning',
                         color=_WARN, show=False)
            with dpg.group(horizontal=True):
                dpg.add_loading_indicator(tag='cpu_vs_cpu_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='cpu_vs_cpu_status')

    def build_extras(self):
        """The Gameplay tab's "Extras" inner tab: community Gecko codes as toggles that stage game option edits, like
        the CPU vs CPU tab's CPU fixes ("Patch Game" writes them)."""
        with dpg.tab(label='Extras', tag='extras_tab'):
            dpg.add_text('The buttons stage the change; "Patch Game" writes every pending edit.', color=_DIM)
            dpg.add_spacer(height=4)
            for title, extras in (('Characters and chemistry', gui_grid.EXTRAS), ('99-0 tools', gui_grid.EXTRAS_99_0),
                                  ('Miscellaneous', gui_grid.EXTRAS_MISC)):
                if extras is not gui_grid.EXTRAS:
                    dpg.add_spacer(height=4)
                dpg.add_text(title)
                for key, _label, tip in extras:
                    with dpg.group(horizontal=True):
                        dpg.add_button(label=gui_grid.cpu_fix_button(None, None, key)[0], tag=f'grid_fix_{key}',
                                       width=300, callback=lambda _s, _a, u: self._on_cpu_fix(u), user_data=key)
                        with dpg.tooltip(f'grid_fix_{key}'):
                            dpg.add_text(tip, wrap=420)
                        dpg.add_text('', tag=f'grid_fix_{key}_status')
            with dpg.group(horizontal=True):
                dpg.add_input_int(tag='grid_mercy_rule_runs', width=120, min_value=1, max_value=99,
                                  min_clamped=True, max_clamped=True, default_value=gui_grid.MERCY_RULE_STOCK,
                                  callback=lambda: self._show_cpu_vs_cpu())
                with dpg.tooltip('grid_mercy_rule_runs'):
                    dpg.add_text('Port of "Mercy Rule Modifier" [ZPL]: with the mercy rule on in the match settings, '
                                 'a match ends once a team leads by this many runs (1-99; stock 10). Plain patch: no '
                                 'added code, so Gecko codes keep working.', wrap=420)
                dpg.add_button(label=gui_grid.MERCY_RULE_STAGE, tag='grid_mercy_rule_stage',
                               callback=lambda: self._on_mercy_rule(True))
                dpg.add_button(label=gui_grid.MERCY_RULE_RESET, tag='grid_mercy_rule_reset',
                               callback=lambda: self._on_mercy_rule(False))
                dpg.add_text('', tag='grid_mercy_rule_status')
            dpg.add_text('Disable the matching Gecko codes in Dolphin now!', tag='grid_extras_gecko_warning',
                         color=_WARN, show=False)
            with dpg.group(horizontal=True):
                dpg.add_loading_indicator(tag='extras_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='extras_status')

    def build_options(self):
        """The Options tab: game options written into main.dol right away (player memory). Its state comes from the
        grid's read, so the tab lives here."""
        with dpg.tab(label='Options', tag='options_tab'):
            dpg.add_text('Pending player changes. Please commit or discard them before changing the game options.',
                         tag='options_pending', color=_ERROR, wrap=600, show=False)
            dpg.add_text('Player memory')
            dpg.add_checkbox(label='I have manually increased the MEM2 limit in Dolphin to 128 MB',
                             tag='grid_big_memory', callback=lambda _s, value: self._on_big_memory(value))
            with dpg.tooltip('grid_big_memory'):
                dpg.add_text('Unlocks player memory levels of +512 KB to +4 MB. They need Dolphin\'s "Emulated '
                             'Memory Size Override" (Config > Advanced) with MEM2 at 128 MB: the game heap then '
                             'grows by 64 MB.', wrap=420)
                dpg.add_text('The game checks the memory size when it starts. Without the override (or on a real '
                             'Wii) it keeps the stock heaps and uses +48 KB player memory, so models bigger than '
                             'that will crash there.', wrap=420)
            with dpg.group(horizontal=True):
                dpg.add_combo(gui_grid.player_heap_choices(), tag='grid_player_heap', width=260,
                              default_value=gui_grid.PLAYER_HEAP_STOCK_LABEL,
                              callback=lambda _s, value: self._on_player_heap(value))
                with dpg.tooltip('grid_player_heap'):
                    dpg.add_text('Each player on the field gets a fixed memory area for its High model, Low model '
                                 'and bat or glove (850 KB in the stock game). A player whose files do not fit '
                                 'crashes the game when it is loaded onto the field. Raising it lets bigger custom '
                                 'models play.', wrap=420)
                    dpg.add_text('The extra memory comes from the game heap that the stadium, effects and '
                                 'animations share (13 players x the increase). +48 KB fits a cautious estimate of '
                                 'the stock console memory, +64 KB goes slightly past it, +128 KB well past it: '
                                 'that may crash in a heavy scene, for example when a run is scored.', wrap=420)
                    dpg.add_text('Levels marked (experimental) have only been tried at Wario City; they may '
                                 'cause problems elsewhere.', wrap=420)
                dpg.add_text('', tag='grid_player_heap_status')
            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)
            dpg.add_text('Team autopicker')
            with dpg.group(horizontal=True):
                dpg.add_button(label=gui_grid.AUTOPICKER_ENABLE, tag='options_autopicker', width=260,
                               callback=lambda: self._on_autopicker())
                with dpg.tooltip('options_autopicker'):
                    dpg.add_text('Writes the Gecko code that the MSS-AutoTeam autopicker asks for straight into '
                                 'main.dol, so it no longer has to be enabled in Dolphin. It keeps the game from '
                                 'resetting the lineup the autopicker writes; without the autopicker the lineup '
                                 'screens keep their stock defaults.', wrap=420)
                    dpg.add_text('Three instructions are changed in place: it adds no code area, so Gecko codes '
                                 'made for the stock game keep working.', wrap=420)
                dpg.add_text('', tag='options_autopicker_status')
            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)
            dpg.add_text('Unused character strike zones')
            with dpg.group(horizontal=True):
                dpg.add_button(label=gui_grid.STRIKE_ZONE_ENABLE, tag='options_strike_zone', width=260,
                               callback=lambda: self._on_strike_zone())
                with dpg.tooltip('options_strike_zone'):
                    dpg.add_text('Writes the Gecko code "Fix Unused Character Strikezone Heights" into main.dol: the '
                                 'strike zone heights of the unused Toad (0.8), Pianta (0.95), Kritter (1.15) and '
                                 'Koopa (0.9).', wrap=420)
                    dpg.add_text('It follows the roster: new characters that play with one of their stats get the '
                                 'fixed height too, and it stays on through roster changes. It adds no code area, '
                                 'so Gecko codes made for the stock game keep working.', wrap=420)
                dpg.add_text('', tag='options_strike_zone_status')
            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)
            dpg.add_text('Cheats')
            dpg.add_text(gui_grid.CHEATS_NOTE, color=_DIM, wrap=600)
            for key, _label, tip in gui_grid.CHEATS:
                with dpg.group(horizontal=True):
                    dpg.add_button(label=gui_grid.cheat_button(None, key)[0], tag=f'options_cheat_{key}', width=260,
                                   callback=lambda _s, _a, k: self._on_cheat(k), user_data=key)
                    with dpg.tooltip(f'options_cheat_{key}'):
                        dpg.add_text(tip, wrap=420)
                    dpg.add_text('', tag=f'options_cheat_{key}_status')
            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)
            dpg.add_button(label='Copy to Dolphin now', tag='options_copy_now', callback=lambda: self._on_copy_now())
            with dpg.tooltip('options_copy_now'):
                dpg.add_text('Copy output files back to the game folder and overwrite.', wrap=420)
            with dpg.group(horizontal=True):
                dpg.add_loading_indicator(tag='options_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='options_status')
            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)
            dpg.add_text('UI Options')
            dpg.add_checkbox(label=gui_grid.SKIP_CONFIRM_LABEL, tag='options_skip_confirm',
                             default_value=self.app.skip_confirmations(),
                             callback=lambda _s, value: self._on_skip_confirm(value))
            with dpg.tooltip('options_skip_confirm'):
                dpg.add_text('Do not show the confirmation popups (after staging checks and dry runs, before '
                             'writing, discarding or closing): each one goes on as if OK was clicked. Popups that '
                             'report a refusal or an error still show; skipped ones leave their title and warnings '
                             'in the log.', wrap=420)

    def _on_skip_confirm(self, value):
        try:
            self.app.settings.set(gui_grid.SKIP_CONFIRM_SETTING, bool(value))
        except OSError as exc:
            self.app.log_line(f'[options] could not save the setting: {exc}', _WARN)

    # ------------------------------------------------------------------ reading
    def request_read(self):
        if not self.loader.start():
            return                          # one runs; the refresh is queued
        self._show_status()
        command = [*self.app.command_prefix, '--roster-state']
        env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')

        def work():
            try:
                done = subprocess.run(command, cwd=self.app.root_dir, env=env, stdin=subprocess.DEVNULL,
                                      capture_output=True, creationflags=_NO_WINDOW)
                code, output = done.returncode, done.stdout.decode('utf-8', errors='replace')
            except OSError as exc:
                code, output = 1, f'[Error] could not start the grid reader: {exc}'
            self.app.output_queue.put(('grid_state', code, output))
        threading.Thread(target=work, daemon=True).start()

    def on_read_done(self, code, output):
        again = self.loader.finish(code, output)
        self._read_gecko()
        if self.loader.status == self.loader.FAILED:
            self.app.log_line(f'[character grid] {self.loader.message}', _WARN)
        elif self.loader.state is not None:
            for warning in self.loader.state.get('warnings', []):
                self.app.log_line(f'[character grid] warning: {warning}', _WARN)
            self.nav.refresh(self.loader.state)
            if self.copied is not None and self.copied not in gui_grid.characters(self.loader.state):
                self.copied = None
        self._clear_popups()
        dpg.delete_item('grid_cells', children_only=True)
        self._release_textures()
        self._show_status()
        self._draw_grid()
        self._draw_popups()
        for callback in self.on_state_read:
            callback()
        if again:
            self.request_read()

    def _read_gecko(self):
        """Whether the output game still works with stock-game Gecko codes: only then do pending edits that break
        it get the red marks (on an incompatible game there is nothing left to break)."""
        try:
            status = gecko_compat.status_of_file(os.path.join(self.app.root_dir, gecko_compat.OUTPUT_FOLDER,
                                                              'main.dol'))
        except OSError:
            status = None
        self.gecko_compatible = status is not None and status.compatible

    def _gecko(self, cid):
        """A slot whose pending edits break Gecko code compatibility of a compatible game (red marks)."""
        return self.gecko_compatible and self.pending.breaks_gecko(cid)

    def _gecko_square(self, index):
        return self.gecko_compatible and self.pending.square_gecko(self.loader.state, index)

    def _red(self, cid):
        """Red marks in place of orange: a Gecko code compatibility break or a slot over the player memory."""
        return self._gecko(cid) or self.pending.over_memory(cid)

    def _red_square(self, index):
        return self._gecko_square(index) or self.pending.square_memory(self.loader.state, index)

    def _show_status(self):
        """Spinner and status line: while the tab's own command runs (check, Patch Game) or the grid is read. The
        Options tab and the Gameplay tab's CPU vs CPU tab show them too (their status comes from the read)."""
        busy = self.work is not None or self.loader.status == self.loader.RUNNING
        for spinner, status in (('grid_spinner', 'grid_status'), ('options_spinner', 'options_status'),
                                ('cpu_vs_cpu_spinner', 'cpu_vs_cpu_status'), ('extras_spinner', 'extras_status')):
            if dpg.does_item_exist(spinner):
                dpg.configure_item(spinner, show=busy)
                dpg.set_value(status, self.work or self.loader.message)
        self._show_popup_spinner()
        state = self.loader.state
        level = gui_grid.player_heap_level(state)
        if gui_grid.player_heap_is_big(level):
            dpg.set_value('grid_big_memory', True)     # a 128 MB level is on: its items must stay listed
        self._player_heap_items()
        dpg.set_value('grid_player_heap', gui_grid.player_heap_choice(level))
        text, enabled = gui_grid.player_heap_status(state)
        dpg.set_value('grid_player_heap_status', text)
        dpg.configure_item('grid_player_heap_status', color=_OK if enabled else _WARN if enabled is False else _DIM)
        label, _enable = gui_grid.autopicker_button(state)
        dpg.configure_item('options_autopicker', label=label)
        text, on = gui_grid.autopicker_status(state)
        dpg.set_value('options_autopicker_status', text)
        dpg.configure_item('options_autopicker_status', color=_OK if on else _DIM)
        dpg.configure_item('options_strike_zone', label=gui_grid.strike_zone_button(state)[0])
        text, on = gui_grid.strike_zone_status(state)
        dpg.set_value('options_strike_zone_status', text)
        dpg.configure_item('options_strike_zone_status', color=_OK if on else _DIM)
        for key, _label, _tip in gui_grid.CHEATS:
            dpg.configure_item(f'options_cheat_{key}', label=gui_grid.cheat_button(state, key)[0])
            text, on = gui_grid.cheat_status(state, key)
            dpg.set_value(f'options_cheat_{key}_status', text)
            dpg.configure_item(f'options_cheat_{key}_status', color=_OK if on else _DIM)
        self._show_cpu_vs_cpu()
        dpg.set_value('grid_note', gui_grid.stock_luigi_note(state) if state else '')
        dpg.configure_item('grid_reference', show=self.reference is not None)
        if self.reference is not None:
            dpg.set_value('grid_reference', self.reference.line(state))

    # ------------------------------------------------------------------ level 0
    def _draw_grid(self):
        dpg.delete_item('grid_cells', children_only=True)
        state = self.loader.state
        if state is None:
            return
        cols, rows = state['shape']
        dpg.configure_item('grid_cells', height=max(rows, EXTRA_ROWS) * ROW_PITCH + 16)
        off_grid = list(state.get('off_grid') or [])
        for r in range(max(rows, EXTRA_ROWS)):
            with dpg.group(horizontal=True, horizontal_spacing=CELL_GAP, parent='grid_cells'):
                if r < rows:
                    for c in range(cols):
                        self._grid_cell(state['cells'][r * cols + c])
                else:                             # a grid with fewer rows than the column beside it
                    dpg.add_spacer(width=cols * CELL[0] + (cols - 1) * CELL_GAP)
                if (r == 0 and off_grid) or r in (1, 2):
                    dpg.add_spacer(width=OFF_GRID_GAP - CELL_GAP)
                if r == 0:                        # squares on no cell (stock Luigi)
                    for index in off_grid:
                        self._grid_cell(index)
                elif r == 1:
                    self._item_cell(gui_grid.ITEM)
                elif r == 2:
                    self._item_cell(gui_grid.STADIUM)

    def _item_cell(self, op):
        """The item tile (a banana) or the stadium tile (a flag): a click stages model .sluggie files
        (``_on_items``). Orange border while models of the tile are pending."""
        tile = gui_grid.MODEL_FILE_TILES[op]
        files = gui_grid.staged_item_files(self.pending, op)
        with dpg.group():
            dpg.add_spacer(width=CELL[0], height=1)
            w, h = GRID_ICON
            texture = self._tile_texture(op)
            indent = max(0, (CELL[0] - w - 2 * FRAME) // 2)
            if texture is None:
                button = dpg.add_button(label=tile.label, width=w + 2 * FRAME, height=h + 2 * FRAME,
                                        indent=indent, callback=lambda: self._on_items(op))
            else:
                button = dpg.add_image_button(texture, width=w, height=h, indent=indent,
                                              callback=lambda: self._on_items(op))
            self._caption(tile.label + (f' ({len(files)})' if files else ''), CELL[0],
                          color=_PENDING if files else None)
        if files:
            dpg.bind_item_theme(button, 'grid_pending_theme')
        with dpg.tooltip(button):
            dpg.add_text(tile.tip)
            for n, line in enumerate(gui_grid.item_tooltip(self.pending, op)):
                dpg.add_text(line, color=_PENDING if files and n else _DIM, wrap=420)

    def _tile_texture(self, op):
        """The item / stadium tile's texture (``gui_grid.banana_icon`` / ``flag_icon``), made once per read like
        the portraits."""
        name, draw = TILE_ICONS[op]
        key = (name,)
        if key not in self.textures:
            try:
                image = draw(GRID_ICON)
            except (OSError, ValueError) as exc:
                self.app.log_line(f'[character grid] the {name} tile picture could not be drawn: {exc}', _WARN)
                return None
            self.textures[key] = dpg.add_static_texture(image.width, image.height, _rgba(image),
                                                        parent='grid_textures')
        return self.textures[key]

    def _grid_cell(self, index):
        state = self.loader.state
        with dpg.group():
            dpg.add_spacer(width=CELL[0], height=1)     # fixes the cell width
            if index is None:
                self._empty_cell()
                return
            head = state['squares'][index]['head']
            pending = self.pending.square_pending(state, index)
            stats = self.pending.square_stats(state, index)
            gecko = self._red_square(index)
            button = self._portrait_button(head, gui_grid.FRONT, GRID_SCALE, CELL[0],
                                           index, lambda _s, _a, u: self._open_square(u),
                                           badge=gui_grid.slot_count(state, index), both=pending and stats,
                                           gecko=gecko)
            self._add_menu(button, state['squares'][index]['members'])
            self._caption(gui_grid.square_label(state, index), CELL[0],
                          gui_grid.is_fallback(state, head, gui_grid.FRONT))
        changed = self.reference is not None and self.reference.square_changed(state, index)
        copied = self.copied is not None and self.copied in state['squares'][index]['members']
        if pending or stats:
            if not (pending and stats):                  # both: _portrait_button drew the split border
                dpg.bind_item_theme(button, self._pending_theme(pending, gecko))
        elif copied:
            dpg.bind_item_theme(button, 'grid_copied_theme')
        elif changed:
            dpg.bind_item_theme(button, 'grid_changed_theme')
        with dpg.tooltip(button):
            for line in gui_grid.square_tooltip(state, index):
                dpg.add_text(line)
            if copied:
                dpg.add_text(f'{gui_grid.name_of(state, self.copied)}: copied (right-click a slot, Paste)',
                             color=_COPIED)
            if pending or stats:
                for cid in state['squares'][index]['members']:
                    for line in self.pending.summary(cid):
                        dpg.add_text(f'{gui_grid.name_of(state, cid)}: {line}', color=_PENDING)
                    for line in self.pending.stat_summary(cid):
                        dpg.add_text(f'{gui_grid.name_of(state, cid)}: {line}', color=_STATS)
                    if self._gecko(cid):
                        dpg.add_text(f'{gui_grid.name_of(state, cid)}: {gecko_compat.PENDING_WARNING}', color=_GECKO,
                                     wrap=520)
                    if self.pending.memory_line(cid):
                        dpg.add_text(self.pending.memory_line(cid), color=_GECKO, wrap=520)
            if changed:
                for cid in state['squares'][index]['members']:
                    fields = self.reference.changed(state, cid)
                    if fields:
                        dpg.add_text(f'{gui_grid.name_of(state, cid)}: changed since the pack: {", ".join(fields)}',
                                     color=_CHANGED)

    def _empty_cell(self):
        w, h = GRID_ICON[0] + 2 * FRAME, GRID_ICON[1] + 2 * FRAME
        with dpg.drawlist(width=w, height=h, indent=(CELL[0] - w) // 2):
            dpg.draw_rectangle((1, 1), (w - 1, h - 1), color=(70, 70, 74, 255), fill=(40, 40, 44, 255))
        self._caption('empty', CELL[0], color=_EMPTY)

    # ------------------------------------------------------------------ portraits
    def _texture(self, path, scale, badge=None, split=None, gecko=False):
        """A static texture of a crop, scaled by a whole factor (nearest-neighbour); None when unreadable.
        ``badge``: a number drawn into the bottom right corner (the grid's slot count). ``split``: the display size
        ``(w, h)`` of a button with both markers (``_split_texture``; ``gecko``: its pending half red)."""
        key = (path, scale, badge, split, gecko)
        if key not in self.textures:
            try:
                with Image.open(path) as png:
                    image = png.convert('RGBA')
            except OSError:
                return None
            if scale != 1:
                image = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
            if badge is not None:
                self._draw_badge(image, str(badge), scale)
            if split is not None:
                image = self._split_texture(image, split, _GECKO if gecko else _PENDING)
            data = np.asarray(image, dtype=np.float32).ravel() / 255.0
            self.textures[key] = dpg.add_static_texture(image.width, image.height, data, parent='grid_textures')
        return self.textures[key]

    @staticmethod
    def _split_texture(image, size, left_color=_PENDING):
        """``image`` at its display ``size`` inside the image button's frame padding, with the border of a slot that
        has both pending edits and stat edits: the left half orange (``left_color``: red for a Gecko code
        compatibility break), the right half green. Drawn 1:1 with no frame
        padding (``grid_both_theme``); the padding's inner pixels stay transparent, so hover shows as usual."""
        w, h = size
        if image.size != (w, h):
            image = image.resize((w, h), Image.Resampling.BILINEAR)
        out = np.zeros((h + 2 * FRAME, w + 2 * FRAME, 4), dtype=np.uint8)
        out[FRAME:FRAME + h, FRAME:FRAME + w] = np.asarray(image)
        ring = np.ones(out.shape[:2], dtype=bool)
        ring[BORDER:-BORDER, BORDER:-BORDER] = False
        left = np.zeros_like(ring)
        left[:, :out.shape[1] // 2] = True
        out[ring & left] = left_color
        out[ring & ~left] = _STATS
        return Image.fromarray(out, 'RGBA')

    def _draw_badge(self, image, text, scale):
        """A small light number on a dark rounded box in the image's bottom right corner."""
        from PIL import ImageDraw, ImageFont
        try:
            font = ImageFont.truetype(os.path.join(self.app.root_dir, FONT_REL), BADGE_FONT * scale)
            font.set_variation_by_axes([700, 100])
        except (OSError, ValueError, AttributeError):
            font = ImageFont.load_default()
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        pad = scale
        w, h = right - left + 4 * pad, bottom - top + 2 * pad
        x0, y0 = image.width - w - pad, image.height - h - pad
        draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=2 * pad, fill=(20, 20, 24, 215))
        draw.text((x0 + 2 * pad - left, y0 + pad - top), text, font=font, fill=(255, 255, 255, 255))

    def _release_textures(self):
        dpg.delete_item('grid_textures', children_only=True)
        self.textures = {}

    def _portrait_texture(self, cid, view, scale, badge=None, split=None, gecko=False):
        state = self.loader.state
        path = gui_grid.icon_file(state, self.loader.state_path, cid, view) if state else None
        return self._texture(path, scale, badge, split, gecko) if path else None

    def _portrait_button(self, cid, view, scale, cell_w, user_data, callback, badge=None, both=False, gecko=False):
        """The portrait as an image button, centred in ``cell_w``; a plain button where there is none. A
        fractional ``scale`` draws the next whole-factor texture smaller (less blur than scaling up). ``both``:
        pending edits and stat edits, the split border (``grid_both_theme`` bound here; ``gecko``: the pending
        half red, ``grid_both_gecko_theme``)."""
        w, h = round(ICON[0] * scale), round(ICON[1] * scale)
        texture = self._portrait_texture(cid, view, math.ceil(scale), badge, (w, h) if both else None,
                                         both and gecko)
        indent = max(0, (cell_w - w - 2 * FRAME) // 2)
        if texture is None:
            label = 'no portrait' + (f' ({badge})' if badge is not None else '')
            button = dpg.add_button(label=label, width=w + 2 * FRAME, height=h + 2 * FRAME, indent=indent,
                                    user_data=user_data, callback=callback)
        elif both:
            button = dpg.add_image_button(texture, width=w + 2 * FRAME, height=h + 2 * FRAME, indent=indent,
                                          user_data=user_data, callback=callback)
        else:
            button = dpg.add_image_button(texture, width=w, height=h, indent=indent, user_data=user_data,
                                          callback=callback)
        if both:
            dpg.bind_item_theme(button, 'grid_both_gecko_theme' if gecko else 'grid_both_theme')
        return button

    @staticmethod
    def _pending_theme(pending, gecko):
        """The border of a button with one marker: pending edits (red when they break Gecko code compatibility)
        or stat edits."""
        return ('grid_gecko_theme' if gecko else 'grid_pending_theme') if pending else 'grid_stats_theme'

    def _portrait_slot_button(self, cid, view, preview=None):
        """The slot level's enlarged portrait as an image button (``preview``: a pending portrait's PNG instead,
        with the pending border); a click replaces that view. A plain button where there is none."""
        texture = self._texture(preview, SLOT_SCALE) if preview else self._portrait_texture(cid, view, SLOT_SCALE)
        usable = gui_grid.can_replace_portrait(cid) and self.pending.pack is None
        callback = lambda: self._on_portrait(cid, view)
        if texture is None:
            button = dpg.add_button(label='no portrait', width=PORTRAIT[0] + 2 * FRAME,
                                    height=PORTRAIT[1] + 2 * FRAME, callback=callback)
        else:
            button = dpg.add_image_button(texture, width=PORTRAIT[0], height=PORTRAIT[1], callback=callback)
        dpg.configure_item(button, enabled=usable and not self._locked())
        if preview:
            dpg.bind_item_theme(button, 'grid_pending_theme')
        self.slot_buttons.append((button, usable))
        with dpg.tooltip(button):
            tip = gui_grid.portrait_tip(cid, view)
            if gui_grid.can_replace_portrait(cid) and self.pending.pack is not None:
                tip = 'A roster pack load is pending: press "Patch Game" (or Discard it) first.'
            dpg.add_text(tip, wrap=420)

    def _alt_icon_link(self, cid):
        """``[Set Side Alt Icon]`` beside the Side label: a click picks the alternate side portrait (the side view
        facing the other way; the game mirrors the side cell without one). Its tooltip explains it and shows the
        alternate portrait the slot has, or the pending one."""
        state = self.nav.state or self.loader.state
        can = gui_grid.can_replace_portrait(cid)
        usable = can and self.pending.pack is None and not self._locked()
        pending = self.pending.portrait(cid, gui_grid.SIDE_ALT)
        link = dpg.add_text(gui_grid.SET_ALT_TEXT, color=_PENDING if pending else (_CHANGED if usable else _EMPTY))
        if usable:
            with dpg.item_handler_registry() as handlers:
                dpg.add_item_clicked_handler(callback=lambda *_: self._on_portrait(cid, gui_grid.SIDE_ALT))
            dpg.bind_item_handler_registry(link, handlers)
            self._name_handlers.append(handlers)
        with dpg.tooltip(link):
            if can and self.pending.pack is not None:
                dpg.add_text('A roster pack load is pending: press "Patch Game" (or Discard it) first.', wrap=420)
            else:
                dpg.add_text(gui_grid.portrait_tip(cid, gui_grid.SIDE_ALT), wrap=420)
            shown = pending or (gui_grid.icon_file(state, self.loader.state_path, cid, gui_grid.SIDE_ALT)
                                if state and gui_grid.has_alt_icon(state, cid) else None)
            texture = self._texture(shown, SLOT_SCALE) if shown else None
            if texture is not None:
                dpg.add_text('Pending alternate side portrait:' if pending else 'Its alternate side portrait:',
                             color=_PENDING if pending else _DIM)
                dpg.add_image(texture, width=PORTRAIT[0], height=PORTRAIT[1])
            elif can:
                dpg.add_text('No alternate side portrait: the side one is mirrored.', color=_DIM)

    @staticmethod
    def _caption(text, cell_w, fallback=False, color=None):
        """A centred label under a portrait; a fallback portrait is marked with ``*`` in the warning colour."""
        text = _fit(text, cell_w - (14 if fallback else 0)) + (' *' if fallback else '')
        kwargs = {'color': _WARN if fallback else color} if (fallback or color) else {}
        dpg.add_text(text, indent=max(0, (cell_w - 7 * len(text)) // 2), **kwargs)

    # ------------------------------------------------------------------ levels 1-2
    def _open_square(self, index):
        if self.nav.level != gui_grid.GRID or self.loader.state is None:
            return
        self.nav.state = self.loader.state
        self.nav.open_square(index)
        self._draw_popups()

    def _open_slot(self, cid):
        if self.nav.level != gui_grid.SQUARE:
            return
        self.nav.open_slot(cid)
        self._draw_popups()

    def _levels_held(self):
        """A file dialog or a dialog is up: clicks and Esc leave the levels alone. A running check alone does not
        hold them (its dialog opens over whatever level is shown when it is done)."""
        return self.action is not None and (self.confirm is not None or self.work is None)

    def _on_mouse_click(self, _sender, _app_data):
        if not self.popups or self._levels_held():
            return
        if dpg.is_item_shown('grid_context') and _inside('grid_context', dpg.get_mouse_pos(local=False)):
            return                            # a click on the right-click menu: the level stays
        box = self.popups[-1][1]
        if self.nav.click(_inside(box, dpg.get_mouse_pos(local=False))):
            self._draw_popups()

    def _on_escape(self, *_):
        if self.confirm is not None:
            self._end_action()
            return
        if self._levels_held():
            return
        if self.nav.back():
            self._draw_popups()

    def _on_enter(self, *_):
        """Enter presses the open dialog's default button (OK / Stage / Patch Game / Discard; Close when that is
        all there is), unless it is disabled; Esc is Cancel (``_on_escape``)."""
        if self.confirm is not None and self.confirm_default is not None:
            self.confirm_default()

    def on_viewport_resize(self):
        self._draw_popups()

    def _clear_popups(self):
        for dim, box in self.popups:
            for item in (box, dim):
                if dpg.does_item_exist(item):
                    dpg.delete_item(item)
        self.popups = []
        for registry in self._name_handlers:
            if dpg.does_item_exist(registry):
                dpg.delete_item(registry)
        self._name_handlers = []

    def _draw_popups(self):
        self._clear_popups()
        self.slot_buttons = []
        state = self.nav.state
        if state is None or self.nav.square_index() is None:
            return
        for level in self.nav.levels():
            dim = dpg.add_window(no_title_bar=True, no_resize=True, no_move=True, no_collapse=True,
                                 no_scrollbar=True, no_saved_settings=True,
                                 pos=(0, 0), width=dpg.get_viewport_client_width(),
                                 height=dpg.get_viewport_client_height())
            dpg.bind_item_theme(dim, 'grid_dim_theme')
            box = self._square_box() if level == gui_grid.SQUARE else self._slot_box()
            dpg.focus_item(box)
            self.popups.append((dim, box))
        self._popup_spinner()
        if self.confirm is not None and dpg.does_item_exist(self.confirm):
            dpg.focus_item(self.confirm)

    def _popup_spinner(self):
        """A spinner centred on the top level's box, shown while a command runs (``_popup_busy``). Added last, so
        it draws over the box's (disabled) buttons; it goes with the box when the levels are redrawn."""
        box = self.popups[-1][1]
        cfg = dpg.get_item_configuration(box)
        dpg.add_loading_indicator(tag='grid_popup_spinner', parent=box, style=1, radius=POPUP_SPINNER_RADIUS,
                                  show=self._popup_busy(), color=(90, 200, 120, 255),
                                  secondary_color=(60, 120, 80, 255),
                                  pos=(max(0, (cfg['width'] - POPUP_SPINNER_PX) // 2),
                                       max(0, (cfg['height'] - POPUP_SPINNER_PX) // 2)))

    def _popup_busy(self):
        """A command runs: the tab's own (check, export, Patch Game), another tab's, or the re-read after one."""
        return self.app.busy or self.work is not None or self.loader.status == self.loader.RUNNING

    def _show_popup_spinner(self):
        if dpg.does_item_exist('grid_popup_spinner'):
            dpg.configure_item('grid_popup_spinner', show=self._popup_busy())

    def _box(self, width, height):
        """A level's window, centred. Taller than the viewport: the GUI window grows down to fit (not when
        maximized, at most to the bottom of the screen's work area); what still does not fit scrolls."""
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        vh += self._grow_viewport(height + 2 * BOX_MARGIN - vh)
        height = min(height, max(LINE, vh - 2 * BOX_MARGIN))
        box = dpg.add_window(no_title_bar=True, no_resize=True, no_move=True, no_collapse=True,
                             no_saved_settings=True, width=width, height=height,
                             pos=(max(0, (vw - width) // 2), max(0, (vh - height) // 2)))
        dpg.bind_item_theme(box, 'grid_box_theme')
        return box

    @staticmethod
    def _grow_viewport(missing):
        """Make the GUI window ``missing`` px taller (fewer when the screen runs out); returns the px gained.
        Windows only; nothing for a maximized window. The resize callback redraws the levels afterwards."""
        if missing <= 0:
            return 0
        hwnd = native_dialog.find_owner_window(dpg.get_viewport_title())
        room = native_dialog.room_below(hwnd) if hwnd else None
        grow = min(missing, room or 0)
        if grow > 0:
            dpg.set_viewport_height(dpg.get_viewport_height() + grow)
        return grow

    def _square_box(self):
        state = self.nav.state
        index = self.nav.square_index()
        sq = state['squares'][index]
        members = sq['members']
        per_row = swatches_per_row(len(members), dpg.get_viewport_client_width())
        rows = -(-len(members) // per_row)
        width = max(2 * PAD + per_row * (SWATCH[0] + GAP) - GAP, 360)
        box = self._box(width, 2 * PAD + rows * (SWATCH[1] + GAP) + 60 + LINE + BUTTON_H + GAP)
        kind = 'Stock square' if sq['kind'] == 'stock' else 'New square'
        dpg.add_text(f'{kind}: {gui_grid.name_of(state, sq["head"])}', parent=box)
        for start in range(0, len(members), per_row):
            with dpg.group(horizontal=True, horizontal_spacing=GAP, parent=box):
                for cid in members[start:start + per_row]:
                    fallback = gui_grid.is_fallback(state, cid, gui_grid.SIDE)
                    pending, stats = self.pending.has(cid), self.pending.has_stats(cid)
                    gecko = self._red(cid)
                    with dpg.group():
                        dpg.add_spacer(width=SWATCH[0], height=1)
                        button = self._portrait_button(cid, gui_grid.SIDE, SWATCH_SCALE, SWATCH[0], cid,
                                                       lambda _s, _a, u: self._open_slot(u), both=pending and stats,
                                                       gecko=gecko)
                        self._add_menu(button, [cid])
                        self._caption(gui_grid.name_of(state, cid), SWATCH[0], fallback)
                        self._caption(gui_grid.hex_id(cid), SWATCH[0], color=_DIM)
                    changed = self.reference.changed(state, cid) if self.reference is not None else []
                    if pending or stats:
                        if not (pending and stats):      # both: _portrait_button drew the split border
                            dpg.bind_item_theme(button, self._pending_theme(pending, gecko))
                    elif cid == self.nav.slot:
                        dpg.bind_item_theme(button, 'primary_theme')
                    elif cid == self.copied:
                        dpg.bind_item_theme(button, 'grid_copied_theme')
                    elif changed:
                        dpg.bind_item_theme(button, 'grid_changed_theme')
                    if fallback or pending or stats or changed or cid == self.copied:
                        with dpg.tooltip(button):
                            if cid == self.copied:
                                dpg.add_text('Copied (right-click another slot, Paste)', color=_COPIED)
                            if fallback:
                                dpg.add_text(f'Side portrait: {gui_grid.icon_note(state, cid, gui_grid.SIDE)}')
                            for line in self.pending.summary(cid):
                                dpg.add_text(line, color=_PENDING)
                            for line in self.pending.stat_summary(cid):
                                dpg.add_text(line, color=_STATS)
                            if self._gecko(cid):
                                dpg.add_text(gecko_compat.PENDING_WARNING, color=_GECKO, wrap=420)
                            if self.pending.memory_line(cid):
                                dpg.add_text(self.pending.memory_line(cid), color=_GECKO, wrap=420)
                            if changed:
                                dpg.add_text(f'Changed since the pack: {", ".join(changed)}', color=_CHANGED)
        dpg.add_text(f'Voice: {gui_grid.name_of(state, sq["voice"])}', color=_DIM, parent=box)
        with dpg.group(horizontal=True, parent=box):             # the button on its own row, below the label
            self._voice_button(index, label=VOICE_ALL)
        return box

    def _voice_button(self, index, width=0, label=VOICE):
        """The square's Set voice (all)... button (square level, colour wheels only)."""
        usable = self.pending.pack is None
        button = dpg.add_button(label=label, width=width, height=BUTTON_H, callback=lambda: self._on_value('voice', index),
                                enabled=not self._locked() and usable)
        dpg.bind_item_theme(button, 'primary_theme')
        self.slot_buttons.append((button, usable))
        with dpg.tooltip(button):
            dpg.add_text(VOICE_TIP if usable else 'A roster pack load is pending: press "Patch Game" (or Discard it) '
                         'first.', wrap=420)

    def _slot_box(self):
        """The slot enlarged: front and side portrait, facts, and the slot buttons."""
        state = self.nav.state
        cid = self.nav.slot
        details = [(line, _DIM) for line in gui_grid.slot_details(state, cid)]
        if self.nav.skipped:                  # one-member square: its square-level facts show here too
            sq = state['squares'][self.nav.square_index()]
            details.append((f'{"Stock" if sq["kind"] == "stock" else "New"} square, '
                            f'voice: {gui_grid.name_of(state, sq["voice"])}', _DIM))
        for view in (gui_grid.FRONT, gui_grid.SIDE):
            details.append((f'{view.capitalize()} portrait: {gui_grid.icon_note(state, cid, view)}',
                            _WARN if gui_grid.is_fallback(state, cid, view) else _DIM))
        changed = self.reference.changed(state, cid) if self.reference is not None else []
        if changed:
            details.append((f'Changed since the roster pack you {self.reference.label}: {", ".join(changed)}',
                            _CHANGED))
        details += [(line, _PENDING) for line in self.pending.lines(cid)]
        details += [(line, _STATS) for line in self.pending.stat_summary(cid)]
        if self._gecko(cid):
            details.append((gecko_compat.PENDING_WARNING, _GECKO))
        if self.pending.memory_line(cid):
            details.append((self.pending.memory_line(cid), _GECKO))
        # the Side label row carries [Set Side Alt Icon], which is wider than the portrait: the details column
        # gives way to it (7 px per character, as _caption assumes)
        side_label = ('Side (pending)' if self.pending.portrait(cid, gui_grid.SIDE)
                      else 'Side *' if gui_grid.is_fallback(state, cid, gui_grid.SIDE) else 'Side')
        side_row_w = 7 * len(side_label) + GAP + 7 * len(gui_grid.SET_ALT_TEXT)
        left_w = (PORTRAIT[0] + 2 * FRAME + GAP) + max(PORTRAIT[0] + 2 * FRAME, side_row_w) + GAP
        text_w = SLOT_W - 2 * PAD - left_w - GAP
        # left column: the portraits, then Select .sluggie... / Select lowpoly (L_) .sluggie... below them, then
        # Import .sluggiezip package / Import .slgmodel...
        left = PORTRAIT[1] + 2 * FRAME + LINE + GAP + 4 * BUTTON_H + 8 * TEXT_SPACING_Y
        body = max(left, sum(_text_height(line, text_w) + TEXT_SPACING_Y for line, _c in details))
        tiles = gui_grid.equipment_tiles(state, cid, self.pending)
        # title + separator, body, spacer, Equipment row, the two button rows and the busy line (item spacing 2 px)
        box = self._box(SLOT_W, 2 * PAD + LINE + GAP + body + GAP + (EQUIP_BLOCK + GAP if tiles else 0)
                        + 2 * (BUTTON_H + TEXT_SPACING_Y) + LINE)
        title = dpg.add_text(gui_grid.name_of(state, cid), parent=box)
        with dpg.item_handler_registry() as handlers:        # clicking the name renames, like the button
            dpg.add_item_clicked_handler(callback=lambda *_: self._on_rename(cid))
        dpg.bind_item_handler_registry(title, handlers)
        self._name_handlers.append(handlers)
        with dpg.tooltip(title):
            dpg.add_text('Click to rename this slot')
        dpg.add_separator(parent=box)
        usable = self.pending.pack is None
        busy_tip = 'A roster pack load is pending: press "Patch Game" (or Discard it) first.'
        with dpg.group(horizontal=True, horizontal_spacing=GAP, parent=box):
            with dpg.group() as left_column:            # portraits, then the Select buttons below them
                with dpg.group(horizontal=True, horizontal_spacing=GAP):
                    for view in (gui_grid.FRONT, gui_grid.SIDE):
                        with dpg.group():
                            preview = self.pending.portrait(cid, view)
                            self._portrait_slot_button(cid, view, preview)
                            fallback = gui_grid.is_fallback(state, cid, view)
                            with dpg.group(horizontal=True, horizontal_spacing=GAP):
                                if preview:
                                    dpg.add_text(f'{view.capitalize()} (pending)', color=_PENDING)
                                else:
                                    dpg.add_text(view.capitalize() + (' *' if fallback else ''),
                                                 color=_WARN if fallback else _DIM)
                                if view == gui_grid.SIDE:
                                    self._alt_icon_link(cid)
                dpg.add_spacer(height=GAP)
            with dpg.group():
                for line, color in details:
                    dpg.add_text(line, color=color, wrap=text_w)
        with dpg.group(parent=left_column):             # Select lowpoly (L_) .sluggie... below Select .sluggie...
            for label, low, tip in (
                    (SELECT, False, 'Stage an exported model (and its High/Low partner) for this slot; a confirm '
                                    'dialog shows what changes first. "Patch Game" writes the pending edits.'),
                    (SELECT_LOW, True, 'Stage an exported Low model (L_) for this slot. With a High model pending '
                                       'for the slot (either button first) it becomes that model\'s Low partner, '
                                       'so High and Low are patched in one go; it must be the same character\'s '
                                       'Low model. A High model is refused here.')):
                select = dpg.add_button(label=label, width=SELECT_W, height=BUTTON_H,
                                        callback=(lambda _s, _a, lo: self._on_select(cid, lo)), user_data=low,
                                        enabled=not self._locked() and usable)
                dpg.bind_item_theme(select, 'primary_theme')
                self.slot_buttons.append((select, usable))
                with dpg.tooltip(select):
                    dpg.add_text(tip if usable else busy_tip, wrap=420)
            dpg.add_spacer(height=GAP)
            package_button = dpg.add_button(label=LOAD_PACKAGE, width=SELECT_W, height=BUTTON_H,
                                            callback=lambda *_: self._on_load_package(cid),
                                            enabled=not self._locked() and usable)
            dpg.bind_item_theme(package_button, 'grid_light_blue_theme')
            self.slot_buttons.append((package_button, usable))
            with dpg.tooltip(package_button):
                dpg.add_text('Stage every aspect of a character package (.sluggiezip, right-click a character, '
                             '"Export to .sluggiezip") for this slot at once: models, changed bats, gloves and '
                             'animations, portraits, name, stats and voice. An aspect the package does not hold is '
                             'skipped; a confirm dialog lists both first. "Patch Game" writes the pending edits.'
                             if usable else busy_tip, wrap=420)
            import_button = dpg.add_button(label=IMPORT_SLG, width=SELECT_W, height=BUTTON_H,
                                           callback=lambda *_: self._on_import_slgmodel(cid),
                                           enabled=not self._locked() and usable)
            dpg.bind_item_theme(import_button, 'grid_light_blue_theme')
            self.slot_buttons.append((import_button, usable))
            with dpg.tooltip(import_button):
                dpg.add_text('Stage a character file of the Sluggers Characters patcher (.slgmodel: finished '
                             'High/Low models built on a stock player, portraits, voice clips) for this slot. A '
                             'new ID takes the whole character (name, portraits, voice pack, the base\'s stats); a '
                             'stock slot the models only. Dropping the file onto this window does the same. '
                             '"Patch Game" writes the pending edits.' if usable else busy_tip, wrap=420)
        dpg.add_spacer(height=GAP, parent=box)
        if tiles:
            self._equipment_row(box, cid, tiles)
            dpg.add_spacer(height=GAP, parent=box)
        tips = {RENAME: 'Stage a new name for this slot (one name for English, French and Spanish; it must fit the '
                        'name plate; blank resets it). "Patch Game" writes the pending edits.',
                CLEAR: "Stage a return to this slot's baseline (stock: vanilla models and portraits; new ID: its "
                       "template's files and the open-slot look); a confirm dialog shows what changes first.",
                STATS: "Stage another stock player's stats for this slot (stats, pitching, fielding, chemistry; its "
                       'model, size, voice and name stay). "Patch Game" writes the pending edits.',
                VOICE: CHAR_VOICE_TIP,
                ANIM: 'Stage animation banks (.anm, e.g. exported from Blender) for this slot; select several files '
                      'to stage them together. Every animation of a bank is replaced at once. It must fit the slot\'s skeleton (checked); the stock .anm '
                      'puts the bank back. "Patch Game" writes the pending edits.',
                SIZE: 'Stage a size for this character: one factor on its size scale, in game and on the select '
                      'screens (1 = its normal size; the whole model, skeleton and mesh). "Patch Game" writes the '
                      'pending edits.',
                DISCARD: "Drop this slot's pending edits (nothing was written for them yet)."}
        actions = {RENAME: lambda: self._on_rename(cid), CLEAR: lambda: self._start_preview(cid, None),
                   STATS: lambda: self._on_value('stats', cid), VOICE: lambda: self._on_value(gui_grid.CHAR_VOICE, cid),
                   ANIM: lambda: self._on_anim(cid), SIZE: lambda: self._on_size(cid),
                   DISCARD: lambda: self._on_discard(cid)}
        def slot_button(label, theme='primary_theme'):
            # a staged pack load replaces the roster: slot edits wait until it is written or discarded
            usable = (self.pending.has(cid) if label == DISCARD else self.pending.pack is None
                      and (label != SIZE or gui_grid.size_of(state, cid) is not None))
            button = dpg.add_button(label=label, width=SLOT_BUTTON_W, height=BUTTON_H, callback=actions[label],
                                    enabled=not self._locked() and usable)
            dpg.bind_item_theme(button, theme)
            self.slot_buttons.append((button, usable))
            tip = tips[label] if usable or label == DISCARD else (
                'The size scale of this character could not be read (Refresh, or check 1_Input/main.dol).'
                if label == SIZE and self.pending.pack is None
                else 'A roster pack load is pending: press "Patch Game" (or Discard it) first.')
            with dpg.tooltip(button):
                dpg.add_text(tip, wrap=420)

        # same width and spacing as the Equipment tiles (Select... + Reset), so the columns line up
        with dpg.group(horizontal=True, horizontal_spacing=GAP, parent=box):
            for label in SLOT_BUTTONS:       # Voice...: the slot's own voice (a stock voice or a voice pack)
                slot_button(label, 'grid_warn_theme' if label == CLEAR else 'primary_theme')
        with dpg.group(horizontal=True, horizontal_spacing=GAP, parent=box):
            slot_button(SIZE)                # under Rename...
            slot_button(ANIM)                # under Adopt stats...
            dpg.add_spacer(width=SLOT_BUTTON_W)   # Set voice (all)... is a colour-wheel (square level) button only
            slot_button(DISCARD, 'grid_warn_theme')
        dpg.add_text(BUSY_TEXT if self.app.busy else '', parent=box, tag='grid_slot_busy', color=_WARN)
        return box

    def _equipment_row(self, box, cid, tiles):
        """The Equipment row of the slot level: a tile per bat / glove file (state, Select..., Reset)."""
        dpg.add_text('Equipment', parent=box, color=_DIM)
        usable = self.pending.pack is None
        tile_w = (SLOT_W - 2 * PAD - (len(tiles) - 1) * GAP) // len(tiles)
        with dpg.group(horizontal=True, horizontal_spacing=GAP, parent=box):
            for tile in tiles:
                color = {gui_grid.PENDING_STATE: _PENDING, gui_grid.MODIFIED: _CHANGED,
                         gui_grid.EMPTY: _EMPTY}.get(tile['state'], _OK)
                with dpg.group():
                    dpg.add_text(tile['label'], color=_DIM)
                    text = dpg.add_text(_fit(tile['text'], tile_w), color=color)
                    with dpg.group(horizontal=True, horizontal_spacing=4):
                        select = dpg.add_button(label='Select...', width=EQUIP_BUTTON_W, height=BUTTON_H,
                                                callback=lambda _s, _a, u: self._on_equip_select(*u),
                                                user_data=(cid, tile['file']), enabled=not self._locked() and usable)
                        dpg.bind_item_theme(select, 'primary_theme')
                        reset = dpg.add_button(label='Reset', width=EQUIP_BUTTON_W - 8, height=BUTTON_H,
                                               callback=lambda _s, _a, u: self._on_equip_reset(*u),
                                               user_data=(cid, tile['file']),
                                               enabled=not self._locked() and usable and tile['can_reset'])
                        self.slot_buttons.append((select, usable))
                        self.slot_buttons.append((reset, usable and tile['can_reset']))
                with dpg.tooltip(text):
                    for line in [tile['text']] + tile['tip']:
                        dpg.add_text(line, wrap=420)

    # ------------------------------------------------------------------ edits
    def _locked(self):
        return self.app.busy or self.action is not None

    def set_busy(self, busy):
        """The app started / finished a command: the edit buttons follow (like the other action buttons)."""
        locked = busy or self.action is not None
        for button, usable in self.slot_buttons:
            if dpg.does_item_exist(button):
                dpg.configure_item(button, enabled=usable and not locked)
        if dpg.does_item_exist('grid_slot_busy'):
            dpg.set_value('grid_slot_busy', BUSY_TEXT if busy else '')
        self._show_popup_spinner()
        if dpg.does_item_exist('grid_patch_game'):
            count = len(self.pending)
            # one width for both labels (the longer one's), so the row does not shift when Deploy is ticked
            size = dpg.get_text_size(gui_grid.patch_label(count, True))
            dpg.configure_item('grid_patch_game', label=gui_grid.patch_label(count, self._deploying()),
                               width=max(PATCH_W, int(size[0]) + 20) if size else PATCH_W,
                               enabled=bool(count) and not locked)
            dpg.configure_item('grid_discard_all', enabled=bool(count) and not locked)
            for tag in ('grid_save_pack', 'grid_load_pack'):
                dpg.configure_item(tag, enabled=not locked)
        options_locked = self._options_locked()
        if dpg.does_item_exist('options_pending'):
            dpg.configure_item('options_pending', show=bool(len(self.pending)))
            for tag in ('grid_big_memory', 'grid_player_heap', 'options_autopicker', 'options_strike_zone',
                        'options_copy_now', *(f'options_cheat_{key}' for key, _l, _t in gui_grid.CHEATS)):
                dpg.configure_item(tag, enabled=not options_locked)
        self._show_cpu_vs_cpu()

    def _options_locked(self):
        """The Options tab writes main.dol right away, so it waits until pending player edits are committed or
        discarded (Patch Game would otherwise mix them)."""
        return self._locked() or bool(len(self.pending))

    def _cpu_vs_cpu_locked(self):
        """The CPU vs CPU buttons stage edits: not while the tab is busy or a roster pack load is pending."""
        return self._locked() or self.confirm is not None or self.pending.pack is not None

    def _show_cpu_vs_cpu(self):
        """The CPU vs CPU tab's labels and buttons: the game's state with the pending game option edits on top
        (the management button is usable only while CPU vs CPU is on or staged on)."""
        if not dpg.does_item_exist('grid_cpu_vs_cpu'):
            return
        state, pending = self.loader.state, self.pending
        staged = gui_grid.staged_game_options(pending)
        for tag, key, status in (('grid_cpu_vs_cpu', 'cpu_vs_cpu', gui_grid.cpu_vs_cpu_status),
                                 ('grid_cpu_management', 'cpu_management', gui_grid.cpu_management_status)):
            text, enabled = status(state, pending)
            dpg.set_value(f'{tag}_status', text)
            dpg.configure_item(f'{tag}_status', color=_PENDING if key in staged else _OK if enabled
                               else _WARN if enabled is False else _DIM)
        locked = self._cpu_vs_cpu_locked()
        dpg.configure_item('grid_cpu_vs_cpu', label=gui_grid.cpu_vs_cpu_button(state, pending)[0], enabled=not locked)
        label, _enable, usable = gui_grid.cpu_management_button(state, pending)
        dpg.configure_item('grid_cpu_management', label=label, enabled=usable and not locked)
        level = gui_grid.close_play_level(state, pending)
        if level is not None and level != self.close_play_shown:    # follow the game / staged level, not each redraw
            dpg.set_value('grid_close_play_odds', level)
        self.close_play_shown = level
        text, enabled = gui_grid.close_play_status(state, pending)
        dpg.set_value('grid_close_play_status', text)
        dpg.configure_item('grid_close_play_status', color=_PENDING if '(pending' in text else _OK if enabled
                           else _WARN if enabled is False else _DIM)
        stage, disable = gui_grid.close_play_buttons(state, pending,
                                                     gui_grid.close_play_snap(dpg.get_value('grid_close_play_odds')))
        dpg.configure_item('grid_close_play_stage', enabled=stage and not locked)
        dpg.configure_item('grid_close_play_disable', enabled=disable and not locked)
        for key, _label, _tip in gui_grid.CPU_FIXES + gui_grid.EXTRA_TOGGLES:
            if not dpg.does_item_exist(f'grid_fix_{key}'):
                continue
            dpg.configure_item(f'grid_fix_{key}', label=gui_grid.cpu_fix_button(state, pending, key)[0],
                               enabled=not locked)
            text, enabled = gui_grid.cpu_fix_status(state, pending, key)
            dpg.set_value(f'grid_fix_{key}_status', text)
            dpg.configure_item(f'grid_fix_{key}_status', color=_PENDING if key in staged else _OK if enabled
                               else _DIM)
        dpg.configure_item('cpu_vs_cpu_discard', enabled=bool(staged) and not locked)
        dpg.configure_item('grid_cpu_gecko_warning', show=gui_grid.cpu_gecko_warning(state, pending))
        if dpg.does_item_exist('grid_extras_gecko_warning'):
            dpg.configure_item('grid_extras_gecko_warning', show=gui_grid.extras_gecko_warning(state, pending))
        if dpg.does_item_exist('grid_mercy_rule_runs'):
            level = gui_grid.mercy_rule_level(state, pending)
            if level != self.mercy_rule_shown:      # follow the game / staged lead, not each redraw
                dpg.set_value('grid_mercy_rule_runs', level)
            self.mercy_rule_shown = level
            text, enabled = gui_grid.mercy_rule_status(state, pending)
            dpg.set_value('grid_mercy_rule_status', text)
            dpg.configure_item('grid_mercy_rule_status', color=_PENDING if '(pending' in text else _OK if enabled
                               else _DIM)
            stage, reset = gui_grid.mercy_rule_buttons(
                state, pending, gui_grid.mercy_rule_clamp(dpg.get_value('grid_mercy_rule_runs')))
            dpg.configure_item('grid_mercy_rule_stage', enabled=stage and not locked)
            dpg.configure_item('grid_mercy_rule_reset', enabled=reset and not locked)

    def _pending_changed(self):
        """Redraw what shows the pending list: the grid's markers, the open levels, the buttons."""
        self._prune_staged_icons()
        self._prune_staged_clips()
        self._draw_grid()
        self._draw_popups()
        self.set_busy(self.app.busy)
        for callback in self.on_pending_changed:
            callback()

    def _prune_staged_clips(self):
        """Delete staged clips (``_gui/slot/staged_clips``) that no pending edit uses any more."""
        gui_grid.prune_staged_clips(os.path.join(self.app.root_dir, gui_grid.STAGED_CLIPS_REL),
                                    gui_grid.staged_clip_files(self.pending))

    def _on_select(self, cid, low=False):
        """Select .sluggie... / Select lowpoly (L_) .sluggie... (``low``: a Low model pick)."""
        if self._locked():
            return
        self.action = (cid, 'low' if low else None)
        self.set_busy(False)
        self._pick_sluggie('Select a lowpoly (L_) .sluggie for this slot' if low else 'Select a .sluggie for this slot')

    def _pick_sluggie(self, title):
        """Open the .sluggie picker in the folder the last pick came from (else the models folder)."""
        if not self.app.pick_files('grid_sluggie_dialog', title,
                                   [('Sluggie files', '*.sluggie'), ('Character files', '*.slgmodel')],
                                   self._on_sluggie_paths, on_cancel=self._end_action, remember=SLUGGIE_DIR_SETTING):
            self._end_action()

    def _on_sluggie_paths(self, paths):
        cid = self.action[0] if self.action else None
        equip = self.action[2] if self.action and len(self.action) == 3 and self.action[1] == 'equip' else None
        path = next((p for p in paths if p and p.lower().endswith(('.sluggie', gui_grid.SLGMODEL_EXTENSION))
                     and os.path.isfile(p)), None)
        if cid is None or path is None:
            self.app.log_line(f'[character grid] no .sluggie file chosen: {paths[0]}', _WARN)
            self._end_action()
            return
        low = self.action[1:] == ('low',)
        self.action = None
        if path.lower().endswith(gui_grid.SLGMODEL_EXTENSION) and equip is None:
            self._start_preview(cid, None, slgmodel=path)
            return
        self._start_preview(cid, path, equip=None if equip is None else (equip, path), low=low)

    # ------------------------------------------------------------------ character files (.slgmodel)
    def _on_import_slgmodel(self, cid):
        """Import .slgmodel...: a Sluggers Characters patcher character file for the slot."""
        if self._locked() or self.pending.pack is not None:
            return
        self.action = (cid, gui_grid.SLGMODEL)
        self.set_busy(False)
        if not self.app.pick_files('grid_slgmodel_dialog', 'Select a character file (.slgmodel) for this slot',
                                   gui_grid.SLGMODEL_FILTERS, self._on_slgmodel_paths, on_cancel=self._end_action,
                                   remember=SLGMODEL_DIR_SETTING):
            self._end_action()

    def _on_slgmodel_paths(self, paths):
        cid = self.action[0] if isinstance(self.action, tuple) and self.action[1:] == (gui_grid.SLGMODEL,) else None
        path = next((p for p in paths if p and p.lower().endswith(gui_grid.SLGMODEL_EXTENSION) and os.path.isfile(p)),
                    None)
        if cid is None or path is None:
            self.app.log_line(f'[character grid] no .slgmodel file chosen: {paths[0]}', _WARN)
            self._end_action()
            return
        self.action = None
        self._start_preview(cid, None, slgmodel=path)

    def on_files_dropped(self, paths, _point):
        """Files dropped onto the window (``file_drop``) while a slot is open on this tab: a ``.slgmodel`` or a
        ``.sluggie`` is staged for that slot as its button would. False when the drop is not for this tab."""
        tab_bar = dpg.get_value('main_tab_bar')
        if tab_bar not in ('grid_tab', dpg.get_alias_id('grid_tab')):
            return False
        cid = self.nav.slot
        if cid is None or self.nav.level != gui_grid.SLOT:
            self.app.log_line('[character grid] open a slot first, then drop the file onto the window', _WARN)
            return True
        if self._locked() or self.pending.pack is not None:
            return True
        path = next((p for p in paths if p.lower().endswith((gui_grid.SLGMODEL_EXTENSION, '.sluggie'))
                     and os.path.isfile(p)), None)
        if path is None:
            self.app.log_line(f'[character grid] {os.path.basename(paths[0])} is not a .slgmodel or .sluggie file',
                              _WARN)
            return True
        if len(paths) > 1:
            self.app.log_line(f'[character grid] {len(paths)} files dropped; staging {os.path.basename(path)} only.')
        self.action = None
        if path.lower().endswith(gui_grid.SLGMODEL_EXTENSION):
            self._start_preview(cid, None, slgmodel=path)
        else:
            self._start_preview(cid, path)
        return True

    # ------------------------------------------------------------------ animations
    def _on_anim(self, cid):
        """Replace animation...: pick one or more .anm banks; one staging check finds each one's bank (6-14) and
        checks it, and one confirm dialog stages them all."""
        if self._locked() or self.pending.pack is not None:
            return
        self.action = (cid, gui_grid.ANIM)
        self.set_busy(False)
        if not self.app.pick_files('grid_anim_dialog', 'Select animation banks (.anm) for this slot',
                                   gui_grid.ANIM_FILTERS, self._on_anim_paths, on_cancel=self._end_action,
                                   multi=True, remember=ANIM_DIR_SETTING):
            self._end_action()

    def _on_anim_paths(self, paths):
        cid = self.action[0] if isinstance(self.action, tuple) and self.action[1:] == (gui_grid.ANIM,) else None
        chosen = list(dict.fromkeys(os.path.abspath(p) for p in paths if p and os.path.isfile(p)))
        if cid is None or not chosen:
            self.app.log_line(f'[character grid] no animation file chosen: {paths[0]}', _WARN)
            self._end_action()
            return
        self.action = None
        self._start_preview(cid, None, anim=chosen)

    # ------------------------------------------------------------------ equipment
    def _on_equip_select(self, cid, file):
        """Select... on an equipment tile: pick a bat / glove .sluggie for slot file ``file``."""
        if self._locked() or self.pending.pack is not None:
            return
        self.action = (cid, 'equip', file)
        self.set_busy(False)
        label = gui_grid.EQUIP_LABELS[file].lower()
        self._pick_sluggie(f'Select a {label} .sluggie for this slot')

    def _on_equip_reset(self, cid, file):
        """Reset on an equipment tile: drop its pending edit, or stage the return to the baseline."""
        if self._locked() or self.pending.pack is not None:
            return
        if self.pending.equip_edit(cid, file) is not None:
            self.pending.edits = [e for e in self.pending.edits
                                  if not (int(e['id'], 16) == cid and e['op'] in gui_grid.EQUIP_OPS
                                          and e.get('file') == file)]
            self.pending.sections = [sec for sec in self.pending.sections
                                     if not (int(sec['target'], 16) == cid and sec['action'] in gui_grid.EQUIP_OPS
                                             and (sec.get('edit') or {}).get('file') == file)]
            self._pending_changed()
            return
        self._start_preview(cid, None, equip=(file, None))

    # ------------------------------------------------------------------ portraits
    def _on_portrait(self, cid, view):
        """A click on a slot-level portrait: pick an image, then the icon dialog."""
        if self._locked() or not gui_grid.can_replace_portrait(cid) or self.pending.pack is not None:
            return
        self.action = (cid, 'icon', view)
        self.set_busy(False)
        if not self.app.pick_files('grid_image_dialog', f'Select an image for the {gui_grid.view_label(view)} portrait',
                                   gui_grid.IMAGE_FILTERS, self._on_image_paths, on_cancel=self._end_action,
                                   remember=IMAGE_DIR_SETTING):
            self._end_action()

    def _on_image_paths(self, paths):
        action = self.action if isinstance(self.action, tuple) and len(self.action) == 3 else None
        path = next((p for p in paths if p), None)
        if action is None or path is None:
            self._end_action()
            return
        cid, _kind, view = action
        self._icon_dialog(cid, view, path)

    def _icon_dialog(self, cid, view, path):
        """The source image, the 48x51 result (3x, nearest) beside the slot's other view, fit and trim; each change
        redraws the result in-process. OK stages the edit (after the staging check)."""
        state = self.nav.state or self.loader.state
        preview = gui_grid.IconPreview(path)
        other = gui_grid.SIDE if view != gui_grid.SIDE else gui_grid.FRONT
        label = gui_grid.view_label(view)
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        win = self.confirm = dpg.add_window(
            label=f'{label.capitalize()} portrait of {gui_grid.name_of(state, cid)} ({gui_grid.hex_id(cid)})',
            modal=True, no_collapse=True, no_saved_settings=True, autosize=True,
            pos=(max(0, (vw - CONFIRM_W) // 2), max(0, vh // 8)), on_close=lambda *_: self._end_action())
        result_tex = dpg.add_dynamic_texture(PORTRAIT[0], PORTRAIT[1], [0.0] * (PORTRAIT[0] * PORTRAIT[1] * 4),
                                             parent='grid_dialog_textures')
        with dpg.group(horizontal=True, horizontal_spacing=2 * GAP, parent=win):
            with dpg.group():
                source = _source_thumbnail(preview)
                if source is not None:
                    tex = dpg.add_static_texture(source.width, source.height, _rgba(source),
                                                 parent='grid_dialog_textures')
                    dpg.add_image(tex, width=source.width, height=source.height, border_color=(90, 90, 96, 255))
                else:
                    with dpg.drawlist(width=PORTRAIT[0], height=PORTRAIT[1]):
                        dpg.draw_rectangle((1, 1), (PORTRAIT[0] - 1, PORTRAIT[1] - 1), color=(120, 120, 120, 255))
                dpg.add_text('Your image', color=_DIM)
            with dpg.group():
                dpg.add_image(result_tex, width=PORTRAIT[0], height=PORTRAIT[1], border_color=_PENDING)
                dpg.add_text(f'New {label} (48x51)', color=_PENDING)
            with dpg.group():
                other_path = self.pending.portrait(cid, other) or gui_grid.icon_file(
                    state, self.loader.state_path, cid, other)
                other_tex = self._dialog_texture(other_path)
                if other_tex is not None:
                    dpg.add_image(other_tex, width=PORTRAIT[0], height=PORTRAIT[1], border_color=(90, 90, 96, 255))
                dpg.add_text(f'{other.capitalize()} (kept)', color=_DIM)
        dpg.add_spacer(height=GAP, parent=win)
        labels = [label for _mode, label in gui_grid.FIT_CHOICES]
        dpg.add_radio_button(labels, parent=win, horizontal=True, default_value=labels[0],
                             callback=lambda _s, label: change(fit=dict((l, m) for m, l in gui_grid.FIT_CHOICES)[label]))
        dpg.add_checkbox(label='Trim transparent border', parent=win, default_value=True,
                         callback=lambda _s, ticked: change(trim=ticked))
        messages = dpg.add_group(parent=win)
        dpg.add_spacer(height=GAP, parent=win)
        with dpg.group(horizontal=True, parent=win):
            ok = dpg.add_button(label='OK', width=110, height=BUTTON_H, callback=lambda: accept())
            dpg.bind_item_theme(ok, 'primary_theme')
            dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())

        def change(fit=None, trim=None):
            if fit is not None:
                preview.fit = fit
            if trim is not None:
                preview.trim = trim
            image = preview.result()[0]
            if image is not None:
                big = image.resize(PORTRAIT, Image.Resampling.NEAREST)
            else:
                big = Image.new('RGBA', PORTRAIT, (0, 0, 0, 0))
            dpg.set_value(result_tex, _rgba(big))
            dpg.delete_item(messages, children_only=True)
            for text, kind in preview.lines():
                dpg.add_text(text, parent=messages, wrap=CONFIRM_W, color=_LINE_COLORS[kind])
            dpg.configure_item(ok, enabled=preview.ok)

        def accept():
            if not preview.ok:
                return
            folder = os.path.join(self.app.root_dir, gui_grid.STAGED_ICONS_REL)
            try:
                staged = preview.save(folder, cid, view)
            except OSError as exc:
                self.app.log_line(f'[character grid] could not write the portrait into {folder}: {exc}', _WARN)
                return
            self._end_action()
            self._start_preview(cid, None, icon=preview.edit(cid, view, staged))
        change()
        self.confirm_default = accept
        self.set_busy(self.app.busy)

    def _dialog_texture(self, path):
        if not path:
            return None
        try:
            with Image.open(path) as png:
                image = png.convert('RGBA').resize(PORTRAIT, Image.Resampling.NEAREST)
        except OSError:
            return None
        return dpg.add_static_texture(image.width, image.height, _rgba(image), parent='grid_dialog_textures')

    def _on_rename(self, cid):
        """The rename dialog: a text box with a live "fits / too long" line; OK runs the staging check."""
        if self._locked():
            return
        state = self.nav.state or self.loader.state
        font = os.path.join(self.app.root_dir, FONT_REL)
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        self.action = (cid, 'rename')
        win = self.confirm = dpg.add_window(label=f'Rename {gui_grid.name_of(state, cid)} ({gui_grid.hex_id(cid)})',
                                            modal=True, no_collapse=True, no_saved_settings=True, autosize=True,
                                            pos=(max(0, (vw - CONFIRM_W) // 2), max(0, vh // 5)),
                                            on_close=lambda *_: self._end_action())
        dpg.add_text('One name is used for English, French and Spanish. It must fit the name plate '
                     '(no shrinking). Leave it blank to reset the name.', parent=win, wrap=CONFIRM_W)
        status, ok = 'grid_rename_status', 'grid_rename_ok'

        def changed(_s=None, text=None):
            text = (text if text is not None else dpg.get_value('grid_rename_text')).strip()
            problem = gui_grid.name_problem(text, font) if text else None
            dpg.set_value(status, problem or ('Resets the name' if not text else 'Fits the name plate'))
            dpg.configure_item(status, color=_ERROR if problem else _OK if text else _DIM)
            dpg.configure_item(ok, enabled=problem is None)

        dpg.add_input_text(tag='grid_rename_text', parent=win, width=CONFIRM_W - 20, hint='New name',
                           default_value=gui_grid.rename_prefill(state, cid), callback=changed)
        dpg.add_text('', tag=status, parent=win)
        dpg.add_spacer(height=GAP, parent=win)
        with dpg.group(horizontal=True, parent=win):
            dpg.add_button(label='OK', tag=ok, width=110, height=BUTTON_H,
                           callback=lambda: self._on_rename_ok(cid))
            dpg.bind_item_theme(ok, 'primary_theme')
            dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())
        changed()
        self.confirm_default = lambda: dpg.get_item_configuration(ok).get('enabled') and self._on_rename_ok(cid)
        dpg.focus_item('grid_rename_text')
        self.set_busy(self.app.busy)

    def _on_value(self, op, key):
        """The Stats / Voice dialog: the current assignment and a list to pick from; OK runs the staging check.
        ``key``: the slot's ID (stats) or the square's index (voice; the edit goes on the square's head)."""
        if self._locked():
            return
        state = self.nav.state or self.loader.state
        packs = []
        if op == 'voice':
            cid = state['squares'][key]['head']
            packs = self._voice_packs()
            choices = gui_grid.voice_choices(state, key) + gui_grid.square_pack_choices(state, packs)
            title = f'Voice of the square of {gui_grid.name_of(state, cid)}'
            lines = [gui_grid.voice_text(state, key), gui_grid.voice_reach(state, key)]
            if packs:
                lines.append('A voice pack (Voices tab) is staged as the own voice of every member of the square '
                             '(each member\'s Voice... shows it); the square\'s voice stays as it is.')
        elif op == gui_grid.CHAR_VOICE:
            cid = key
            packs = self._voice_packs()
            choices = [(label, (source, pack)) for label, source, pack in gui_grid.char_voice_choices(state, cid, packs)]
            title = f'Own voice of {gui_grid.name_of(state, cid)} ({gui_grid.hex_id(cid)})'
            lines = [gui_grid.char_voice_text(state, cid),
                     'The slot speaks with the picked voice as its own, on the select screen and on the field, whatever '
                     'its square speaks with; the other members of its square keep theirs. A voice pack (Voices tab) '
                     'puts its custom clips on top of a stock voice.']
        else:
            cid = key
            choices = gui_grid.stats_choices(state, cid)
            title = f'Stats of {gui_grid.name_of(state, cid)} ({gui_grid.hex_id(cid)})'
            lines = [gui_grid.stats_text(state, cid),
                     'The slot plays with the picked stock player\'s stats: stats, pitching, fielding and chemistry. '
                     'Its model, size, voice and name stay.']
        pending = self.pending.value_edit(cid, op)
        if pending is not None:
            lines.append(gui_grid._edit_title(pending))
        if op == gui_grid.CHAR_VOICE:
            current = gui_grid.char_voice_current(state, cid, self.pending)
        else:
            current = pending.get('source') if pending is not None else None
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        self.action = (cid, op)
        win = self.confirm = dpg.add_window(label=title, modal=True, no_collapse=True, no_saved_settings=True,
                                            autosize=True, pos=(max(0, (vw - CONFIRM_W) // 2), max(0, vh // 5)),
                                            on_close=lambda *_: self._end_action())
        for line in lines:
            dpg.add_text(line, parent=win, wrap=CONFIRM_W, color=_PENDING if line.startswith('Pending') else _DIM)
        labels = [label for label, _value in choices]
        dpg.add_combo(labels, tag='grid_value_choice', parent=win, width=CONFIRM_W - 20,
                      default_value=labels[gui_grid.choice_index(choices, current)])
        dpg.add_spacer(height=GAP, parent=win)

        def ok():
            label = dpg.get_value('grid_value_choice')
            source = next((value for text, value in choices if text == label), None)
            self._end_action()
            if op == 'voice' and isinstance(source, tuple) and source[0] == gui_grid.SQUARE_PACK:
                if not self._locked():
                    self._check_and_confirm(cid, gui_grid.square_pack_edits(state, key, source[1]), gui_grid.CHAR_VOICE)
            elif op == gui_grid.CHAR_VOICE:
                source, pack = source if source is not None else (None, None)
                self._start_preview(cid, None, value=(op, source, pack))
            else:
                self._start_preview(cid, None, value=(op, source))
        with dpg.group(horizontal=True, parent=win):
            button = dpg.add_button(label='OK', width=110, height=BUTTON_H, callback=lambda: ok())
            dpg.bind_item_theme(button, 'primary_theme')
            dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())
        self.confirm_default = ok
        self.set_busy(self.app.busy)

    def _on_rename_ok(self, cid):
        text = dpg.get_value('grid_rename_text').strip()
        self._end_action()
        self._start_preview(cid, None, rename=text)

    def _on_size(self, cid):
        """The Set size... dialog: a factor (1 = the character's normal size; a refused one gets a red line); OK writes a one-character stat edit file (``gui_grid.size_doc``) and runs its staging check."""
        state = self.nav.state or self.loader.state
        sizes = gui_grid.size_of(state, cid)
        if self._locked() or sizes is None:
            return
        pending = gui_grid.pending_size(self.pending, cid)
        current = gui_grid.size_factor(sizes)
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        self.action = (cid, 'size')
        win = self.confirm = dpg.add_window(label=f'Size of {gui_grid.name_of(state, cid)} ({gui_grid.hex_id(cid)})',
                                            modal=True, no_collapse=True, no_saved_settings=True, autosize=True,
                                            pos=(max(0, (vw - CONFIRM_W) // 2), max(0, vh // 5)),
                                            on_close=lambda *_: self._end_action())
        dpg.add_text('One factor for the whole model (skeleton and mesh), in game and on the select screens: 1 is '
                     'the character\'s normal size, 1.2 makes it 20% bigger, 0.8 20% smaller. Its hitbox and '
                     'fielding reach stay as they are.', parent=win, wrap=CONFIRM_W)
        dpg.add_text(gui_grid.size_text(state, cid), parent=win, color=_DIM)
        if pending is not None:
            dpg.add_text(f'Pending: {pending:g}x', parent=win, color=_PENDING)
        status, ok = 'grid_size_status', 'grid_size_ok'

        def changed(_s=None, text=None):
            text = text if text is not None else dpg.get_value('grid_size_text')
            _value, problem = gui_grid.parse_size(text)
            dpg.set_value(status, problem or '')         # only a refusal shows
            dpg.configure_item(status, color=_ERROR)
            dpg.configure_item(ok, enabled=problem is None)

        shown = pending if pending is not None else current
        dpg.add_input_text(tag='grid_size_text', parent=win, width=160, hint='Factor, e.g. 1.2',
                           default_value=f'{shown:.4g}', callback=changed)
        dpg.add_text('', tag=status, parent=win)
        dpg.add_spacer(height=GAP, parent=win)
        with dpg.group(horizontal=True, parent=win):
            dpg.add_button(label='OK', tag=ok, width=110, height=BUTTON_H, callback=lambda: self._on_size_ok(cid))
            dpg.bind_item_theme(ok, 'primary_theme')
            dpg.add_button(label='Normal size', width=110, height=BUTTON_H,
                           callback=lambda: (dpg.set_value('grid_size_text', '1'), changed(text='1')))
            dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())
        changed()
        self.confirm_default = lambda: dpg.get_item_configuration(ok).get('enabled') and self._on_size_ok(cid)
        dpg.focus_item('grid_size_text')
        self.set_busy(self.app.busy)

    def _on_size_ok(self, cid):
        value, problem = gui_grid.parse_size(dpg.get_value('grid_size_text'))
        state = self.nav.state or self.loader.state
        self._end_action()
        if problem is not None:
            return
        try:
            path = gui_grid.write_size_file(self.app.root_dir, gui_grid.size_doc(state, cid, value))
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write the size edit file: {exc}', _WARN)
            return
        self.offer_stat_edits(path)

    def _start_preview(self, cid, sluggie, rename=None, value=None, icon=None, equip=None, copy_from=None,
                       anim=None, low=False, slgmodel=None):
        """The staging check (the pending edits plus this one; this one's build check); the confirm dialog
        opens when it is done. ``low``: ``sluggie`` is a Low model pick (``slot_plan`` refuses a High model and joins
        it to the slot's pending High pick); ``rename``: the new name text (blank resets), a rename edit instead of patch/clear;
        ``value``: ``(op, source)``, a stats or voice edit (source None: back to the default); ``icon``: a
        finished icon edit (``IconPreview.edit``); ``equip``: ``(slot file, .sluggie or None)``, an equipment edit
        (None: reset that file); ``copy_from``: the copied character, a paste (the slot becomes its clone); ``anim``:
        a list of ``.anm`` files, one animation bank edit each (staged together); ``slgmodel``: a character file of
        the Sluggers Characters patcher (``.slgmodel``), an import edit."""
        if self._locked():
            return
        kind = (gui_grid.SLGMODEL if slgmodel is not None
                else gui_grid.COPY if copy_from is not None else gui_grid.ANIM if anim is not None
                else 'equip' if equip is not None and equip[1] else 'equip_clear' if equip is not None
                else 'icon' if icon is not None else 'rename' if rename is not None else value[0] if value
                else 'patch' if sluggie else 'clear')
        view = icon['view'] if icon is not None else None
        gear_file = equip[0] if equip is not None else None
        if slgmodel is not None:
            edit = {'op': gui_grid.SLGMODEL, 'id': gui_grid.hex_id(cid), 'file': os.path.abspath(slgmodel)}
        elif copy_from is not None:
            edit = gui_grid.copy_edit(cid, copy_from)
        elif anim is not None:
            edit = [{'op': gui_grid.ANIM, 'id': gui_grid.hex_id(cid), 'anm': path} for path in anim]
        elif equip is not None:
            edit = ({'op': 'equip', 'id': gui_grid.hex_id(cid), 'file': equip[0], 'sluggie': equip[1],
                     'origin': 'user'} if equip[1] else
                    {'op': 'equip_clear', 'id': gui_grid.hex_id(cid), 'file': equip[0]})
        elif icon is not None:
            edit = icon
        elif rename is not None:
            edit = {'op': 'rename', 'id': gui_grid.hex_id(cid), 'text': rename}
        elif value is not None and value[0] == gui_grid.CHAR_VOICE:
            edit = gui_grid.char_voice_edit(cid, value[1], value[2])
        elif value is not None:
            edit = {'op': value[0], 'id': gui_grid.hex_id(cid), 'source': value[1]}
        elif sluggie:
            edit = {'op': 'patch', 'id': gui_grid.hex_id(cid), ('low' if low else 'file'): sluggie}
        else:
            edit = {'op': 'clear', 'id': gui_grid.hex_id(cid)}
        self._check_and_confirm(cid, edit, kind, sluggie, view, gear_file, anm=anim)

    def stage_edit(self, edit, kind, moment=None):
        """Another tab stages an edit (the Voices tab's clip swaps): the staging check, then the confirm dialog, like
        the slot buttons. False when the tab is busy or a roster pack load is pending."""
        if self._locked() or self.pending.pack is not None:
            return False
        self._check_and_confirm(int(edit['id'], 16), edit, kind, None, None, None, moment)
        return True

    def _check_and_confirm(self, cid, edit, kind, sluggie=None, view=None, gear_file=None, moment=None, anm=None):
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging(edit))
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write {self.edits_path}: {exc}', _WARN)
            return
        self.action = (cid, sluggie)
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the edit...',
                         lambda code, output: self._show_confirm(cid, sluggie, code, output, kind, view,
                                                                 gear_file, moment, anm)):
            self._end_action()

    def _voice_packs(self):
        """``[(name, base voice, clip count)]`` of the custom voice packs in _gui/Sound_Packs (for the Voice dialog)."""
        try:
            from Sound import sound_packs
            packs, _problems = sound_packs.list_packs(self.app.root_dir)
        except Exception as exc:                 # a damaged pack folder must not break the dialog
            self.app.log_line(f'[character grid] voice packs could not be listed: {exc}', _WARN)
            return []
        return [(p.name, p.voice, p.count) for p in packs]

    def _show_confirm(self, cid, sluggie, code, output, kind='patch', view=None, gear_file=None, moment=None,
                      anm=None):
        state = self.nav.state or self.loader.state
        plan = gui_grid.load_plan(self.plan_path)
        if kind == gui_grid.PACKAGE:
            path, staged = self._package
            dialog = gui_grid.with_memory_warning(
                gui_grid.package_dialog(state, cid, path, plan, code, output, staged.skipped),
                memory_guard.read_details(self.app.root_dir), only=gui_grid.new_targets(plan))
            self._dialog(dialog, lambda: self._stage_package(plan), ok_label='Stage')
            return
        dialog = gui_grid.with_memory_warning(
            gui_grid.slot_dialog(state, cid, sluggie is not None, plan, code, output, self.pending, kind=kind,
                                 view=view, gear_file=gear_file, moment=moment, anm=anm),
            memory_guard.read_details(self.app.root_dir), only=gui_grid.new_targets(plan))
        if (kind in gui_grid.VALUE_KINDS + ('equip_clear',) and dialog.can_apply and code == 0 and plan is not None
                and any(int(s['target'], 16) == cid for s in plan['edits']) and not dialog.offer_stat_reset
                and not dialog.memory_over):
            self._stage(plan)                  # a valid rename / stats / voice pick needs no second confirmation
            return
        extra = (('Stage + clear stat edits', lambda: self._stage_with_stat_reset(cid, plan))
                 if dialog.offer_stat_reset else None)
        self._dialog(dialog, lambda: self._stage(plan), ok_label='Stage', extra=extra)

    # ------------------------------------------------------------------ copy / paste
    def _add_menu(self, button, members):
        """Give a portrait button the right-click menu (``_on_context``) for ``members`` (its square's, or one)."""
        self._menu_targets = {b: m for b, m in self._menu_targets.items() if dpg.does_item_exist(b)}
        self._menu_targets[button] = list(members)
        dpg.bind_item_handler_registry(button, 'grid_context_handlers')

    def _on_context(self, _sender, app_data):
        """A right click on a portrait button: fill the shared menu window and open it at the mouse."""
        button = app_data[1] if isinstance(app_data, (list, tuple)) else app_data
        members = self._menu_targets.get(button)
        state = self.loader.state
        if members is None or state is None or self.confirm is not None:
            return
        dpg.delete_item('grid_context', children_only=True)
        actions = {gui_grid.COPY_LABEL: lambda: self._on_copy(members[0]),
                   gui_grid.PASTE_LABEL: lambda: self._on_paste(members[0]),
                   gui_grid.EXPORT_LABEL: lambda: self._on_export(members[0]),
                   gui_grid.EXPORT_PACKAGE_LABEL: lambda: self._on_export_package(members[0])}
        entries = gui_grid.context_menu(state, members, self.copied, self.pending.pack is not None, self._locked())
        sizes = [dpg.get_text_size(label) for label, _enabled, _why in entries]
        width = max((s[0] for s in sizes if s), default=0)
        width = int(width) + 20 if width else 0              # + FramePadding x2: one width for every button
        for label, enabled, why in entries:
            item = dpg.add_button(label=label, parent='grid_context', enabled=enabled, width=width,
                                  callback=(lambda _s, _a, act: self._menu_choice(act)),
                                  user_data=actions.get(label))
            if why:
                with dpg.tooltip(item):
                    dpg.add_text(why, wrap=360)
        dpg.configure_item('grid_context', show=True, pos=dpg.get_mouse_pos(local=False))

    def _menu_choice(self, action):
        dpg.configure_item('grid_context', show=False)
        if action is not None:
            action()

    def _on_copy(self, cid):
        if self._locked():
            return
        self.copied = cid
        self.app.log_line(f'[character grid] copied {gui_grid.name_of(self.loader.state, cid)} ({gui_grid.hex_id(cid)}): '
                          'right-click another slot and choose Paste.', _COPIED)
        self._show_status()
        self._pending_changed()

    def _on_paste(self, cid):
        if self._locked() or self.copied is None or self.copied == cid or self.pending.pack is not None:
            return
        self._start_preview(cid, None, copy_from=self.copied)

    def _on_export(self, cid):
        """Export as .sluggie: with pending edits on the slot ask first (they are not in the game yet)."""
        if self._locked():
            return
        if self.pending.has(cid):
            self.action = 'ask'
            self._dialog(gui_grid.export_pending_dialog(self.loader.state, cid, self.pending),
                         lambda: (self._end_action(), self._export(cid)), ok_label='Export without them')
            return
        self._export(cid)

    def _export(self, cid):
        who = f'{gui_grid.name_of(self.loader.state, cid)} ({gui_grid.hex_id(cid)})'

        def done(code, _output):
            result = gui_grid.export_result(self.app.root_dir)
            toast = gui_grid.export_toast(code, result)
            if toast is None:
                self.app.log_line(f'[character grid] {who} was not exported: see the log above.', _WARN)
                self._dialog(gui_grid.export_failed_dialog(self.loader.state, cid, result), self._end_action)
                return
            self.app.log_line(f'[character grid] {who} exported to {result["folder"]}', _OK)
            self._end_action()
            self.app.toast.ok(toast)
        self._run([gui_grid.export_command(cid)], f'Exporting {who}...', done)

    # ------------------------------------------------------------------ character packages (.sluggiezip)
    def _on_export_package(self, cid):
        """Export to .sluggiezip: with pending edits on the slot ask first (they are not in the game yet), then the
        Save As dialog."""
        if self._locked():
            return
        if self.pending.has(cid):
            self.action = 'ask'
            self._dialog(gui_grid.export_pending_dialog(self.loader.state, cid, self.pending),
                         lambda: (self._end_action(), self._pick_package_save(cid)), ok_label='Export without them')
            return
        self._pick_package_save(cid)

    def _pick_package_save(self, cid):
        self.action = (cid, gui_grid.PACKAGE)
        self.set_busy(self.app.busy)
        if not self.app.pick_files('grid_package_save_dialog', 'Export the character as',
                                   gui_grid.PACKAGE_FILTERS, lambda paths: self._on_package_save_paths(cid, paths),
                                   on_cancel=self._end_action, save=True,
                                   default_ext=gui_grid.PACKAGE_EXTENSION.lstrip('.'), remember=PACKAGE_DIR_SETTING,
                                   default_name=gui_grid.package_name(self.loader.state, cid)):
            self._end_action()

    def _on_package_save_paths(self, cid, paths):
        path = paths[0] if paths else None
        self._end_action()
        if not path:
            return
        if not path.lower().endswith(gui_grid.PACKAGE_EXTENSION):
            path += gui_grid.PACKAGE_EXTENSION
        who = f'{gui_grid.name_of(self.loader.state, cid)} ({gui_grid.hex_id(cid)})'

        def done(code, _output):
            result = gui_grid.package_export_result(self.app.root_dir)
            written = (result or {}).get('path') if code == 0 else None
            if not written or not os.path.isfile(written):
                self.app.log_line(f'[character grid] {who} was not exported: see the log above.', _WARN)
                self.action = 'ask'
                self._dialog(gui_grid.package_export_failed_dialog(self.loader.state, cid, result), self._end_action)
                return
            self.app.log_line(f'[character grid] {who} exported to {written}', _OK)
            self.app.toast.ok(f'{os.path.basename(written)} has been created')
        self._run([gui_grid.package_export_command(cid, path)], f'Exporting {who}...', done)

    def _on_load_package(self, cid):
        """Import .sluggiezip package...: pick a package; every aspect it holds is staged for the slot at once."""
        if self._locked() or self.pending.pack is not None:
            return
        self.action = (cid, gui_grid.PACKAGE)
        self.set_busy(False)
        if not self.app.pick_files('grid_package_load_dialog', 'Select a character package (.sluggiezip) for this slot',
                                   gui_grid.PACKAGE_FILTERS, self._on_package_paths, on_cancel=self._end_action,
                                   remember=PACKAGE_DIR_SETTING):
            self._end_action()

    def _on_package_paths(self, paths):
        cid = self.action[0] if isinstance(self.action, tuple) and self.action[1:] == (gui_grid.PACKAGE,) else None
        path = next((p for p in paths if p and os.path.isfile(p)), None)
        if cid is None or path is None:
            self.app.log_line(f'[character grid] no character package chosen: {(paths or [None])[0] or "(none)"}', _WARN)
            self._end_action()
            return
        self.action = None
        from Roster import slot_zip
        root = self.app.root_dir
        slot_zip.prune(os.path.join(root, slot_zip.STAGED_REL), gui_grid.staged_package_files(self.pending))
        font = os.path.join(root, FONT_REL)
        try:
            staged = slot_zip.stage(path, cid, self.nav.state or self.loader.state, root,
                                    name_problem=lambda text: gui_grid.name_problem(text, font),
                                    can_portrait=gui_grid.can_replace_portrait)
        except (slot_zip.PackageError, OSError) as exc:
            self.app.log_line(f'[character grid] {os.path.basename(path)} was not loaded: {exc}', _WARN)
            self.action = 'ask'
            self._dialog(gui_grid.SlotDialog('Package not loaded', [(str(exc), gui_grid.ERROR),
                                                                    ('Nothing was staged.', gui_grid.TEXT)]),
                         self._end_action)
            return
        self._package = (path, staged)
        if not staged.edits:
            self.action = 'ask'
            self._dialog(gui_grid.SlotDialog(f'{os.path.basename(path)}: nothing to stage',
                                             [(f'Skipped: {text}', gui_grid.WARN) for text in staged.skipped]),
                         self._end_action)
            return
        self._check_and_confirm(cid, staged.edits, gui_grid.PACKAGE)

    def package_stat_files(self) -> list[str]:
        """The stat edit file of the package being checked (the Stat Editor tab keeps it until it is staged)."""
        if self._package is None:
            return []
        return [e['file'] for e in self._package[1].edits if e['op'] == gui_grid.STAT_EDITS]

    def _stage_package(self, plan):
        """Stage on a package's confirm dialog: its voice pack (if it made one) stays."""
        path = self._package[0] if self._package else None
        self._package = None
        self._stage(plan)
        if path:
            self.app.log_line(f'[character grid] {os.path.basename(path)} staged; "Patch Game" writes it.', _PENDING)

    def forget_copy(self):
        """Drop the copied character (a tab switch does this)."""
        if self.copied is None:
            return
        self.copied = None
        self._show_status()
        self._pending_changed()

    def _stage_with_stat_reset(self, cid, plan):
        """Stage + clear stat edits: one more staging check with the slot's stat edits cleared behind the edit
        just checked (its build check passed, so it is not run again); staged straight away when it passes."""
        self._end_action()
        edits = {'edits': [dict(e, checked=True) for e in plan.get('merged') or []] + [gui_grid.stat_reset_edit(cid)]}
        try:
            gui_grid.write_edits(self.edits_path, edits)
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write {self.edits_path}: {exc}', _WARN)
            return
        self.action = (cid, None)

        def done(code, output):
            state = self.nav.state or self.loader.state
            checked = gui_grid.load_plan(self.plan_path)
            if code == 0 and checked is not None and not checked['refused']:
                self._stage(checked)
                return
            self._dialog(gui_grid.slot_dialog(state, cid, False, checked, code, output, self.pending,
                                              kind=gui_grid.STAT_RESET), self._end_action)
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the edit...', done):
            self._end_action()

    def offer_stat_edits(self, path, quiet_refusal=False):
        """The staging check of a stat edit file the Stat Editor sent (the pending edits plus this one), then its
        confirm dialog; Stage adds it to the pending list. ``quiet_refusal`` (a file left from an earlier session):
        a refused or empty file only gets a log line. False when the tab is busy (the caller tries again later)."""
        if self._locked() or self.confirm is not None:
            return False
        if self.pending.pack is not None:
            self.action = 'ask'
            self._dialog(gui_grid.SlotDialog('Stat edits: refused', [
                ('A roster pack load is staged: it replaces the whole roster, so stat values cannot be staged on top '
                 'of it.', gui_grid.ERROR),
                ('Patch Game or discard the load first, then open the Stat Editor again. Nothing was staged.',
                 gui_grid.TEXT)]), self._end_action)
            return True
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging(gui_grid.stat_edit(path)))
        except OSError as exc:
            self.app.log_line(f'[stat editor] could not write {self.edits_path}: {exc}', _WARN)
            return True
        self.action = 'stat_edits'
        self.stat_offer = path                 # the Stat Editor tab keeps it while it is offered

        def show(code, output):
            state = self.nav.state or self.loader.state
            plan = gui_grid.load_plan(self.plan_path)
            dialog = gui_grid.stat_edits_dialog(state, plan, code, output, path)
            if quiet_refusal and not dialog.can_apply:
                self._end_action()
                why = dialog.lines[0][0] if dialog.lines else dialog.title.removeprefix('Stat edits: ')
                self.app.log_line(f'[stat editor] the edit file left from an earlier session was dropped: {why}',
                                  _WARN)
                return
            self._dialog(dialog, lambda: self._stage(plan), ok_label='Stage')
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the stat edits...', show):
            self._end_action()
            return False
        return True

    def offer_gameplay(self, edit, replace=False, on_staged=None, confirm=False):
        """The staging check of a gameplay item (the Gameplay tab: its staged values, a loaded settings file, a
        reset): the pending edits plus this one (``replace``: without the pending gameplay edits, which a load
        replaces). It is staged straight away when the check passes without warnings, unless ``confirm``; else its
        dialog asks. ``on_staged`` runs once it is staged. False when the tab is busy or a roster pack load is
        pending."""
        if self._locked() or self.confirm is not None or self.pending.pack is not None:
            return False
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging(
                edit, drop_op=gui_grid.GAMEPLAY if replace else None))
        except OSError as exc:
            self.app.log_line(f'[gameplay] could not write {self.edits_path}: {exc}', _WARN)
            return False
        self.action = 'gameplay'

        def stage(plan):
            self._stage(plan)
            if on_staged is not None:
                on_staged()

        def show(code, output):
            state = self.nav.state or self.loader.state
            plan = gui_grid.load_plan(self.plan_path)
            dialog = gui_grid.gameplay_dialog(state, plan, code, output, edit['file'])
            if dialog.can_apply and not confirm and not any(k == gui_grid.WARN for _t, k in dialog.lines):
                stage(plan)
                return
            self._dialog(dialog, lambda: stage(plan), ok_label='Stage')
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the gameplay settings...', show):
            self._end_action()
            return False
        return True

    # ------------------------------------------------------------------ item and stadium models
    def _on_items(self, op):
        """The item / stadium tile (``op``): with pending models of the tile a dialog lists them (Add..., Discard);
        else the picker."""
        if self._locked():
            return
        tile = gui_grid.MODEL_FILE_TILES[op]
        if self.pending.pack is not None:
            self.action = 'ask'
            self._dialog(gui_grid.SlotDialog(f'{tile.title}: refused', [
                (f'A roster pack load is staged: Patch Game or discard it first, then stage the {tile.noun}s.',
                 gui_grid.ERROR)]), self._end_action)
            return
        if not gui_grid.staged_item_files(self.pending, op):
            self._pick_items(op)
            return
        self.action = 'ask'
        self._dialog(gui_grid.items_dialog(self.pending, op), lambda: (self._end_action(), self._pick_items(op)),
                     ok_label='Add...', extra=(f'Discard {tile.label.lower()}', lambda: self._discard_items(op)))

    def _pick_items(self, op):
        self.action = 'items'
        self.set_busy(False)
        if not self.app.pick_files('grid_sluggie_dialog', f'Select {gui_grid.MODEL_FILE_TILES[op].noun} .sluggie '
                                   'files', gui_grid.ITEM_FILTERS, lambda paths: self._on_item_paths(paths, op),
                                   on_cancel=self._end_action, multi=True, remember=MODEL_FILE_DIRS[op]):
            self._end_action()

    def _on_item_paths(self, paths, op):
        chosen = list(dict.fromkeys(os.path.abspath(p) for p in paths
                                    if p and p.lower().endswith('.sluggie') and os.path.isfile(p)))
        if self.action != 'items' or not chosen:
            self.app.log_line(f'[character grid] no .sluggie file chosen: {paths[0]}', _WARN)
            self._end_action()
            return
        self.action = None
        self._offer_items(chosen, op)

    def _offer_items(self, paths, op):
        """The staging check of picked item / stadium models (the pending edits plus these), then their confirm
        dialog."""
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging([gui_grid.item_edit(p, op) for p in paths]))
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write {self.edits_path}: {exc}', _WARN)
            return
        self.action = 'items'

        def show(code, output):
            plan = gui_grid.load_plan(self.plan_path)
            dialog = gui_grid.item_dialog(self.nav.state or self.loader.state, plan, code, output, paths, op)
            self._dialog(dialog, lambda: self._stage(plan), ok_label='Stage')
        if not self._run([gui_grid.preview_command(self.edits_path)],
                         f'Checking the {gui_grid.MODEL_FILE_TILES[op].noun}s...', show):
            self._end_action()

    def _discard_items(self, op):
        self._end_action()
        self.pending.discard_op(op)
        self._pending_changed()

    def _stage(self, plan):
        self._end_action()
        self.pending.accept(plan, gecko_compat.read_details(self.app.root_dir),     # the staging check's results
                            memory_guard.read_details(self.app.root_dir))
        self.app.log_line(f'[character grid] {len(self.pending)} pending edit(s); "Patch Game" writes them.', _PENDING)
        self._pending_changed()

    def _on_discard(self, cid):
        if self._locked():
            return
        self.pending.discard(cid)
        self._pending_changed()

    def config_risks(self, stats_lost=False, stats_reason=gui_grid.STATS_REPLACED):
        """Why the current configuration would be lost by an export / roster injection (empty: vanilla, nothing
        pending, or the grid not read yet). ``stats_lost``: every stat edit goes (``stats_reason``: the untangle
        export replaces main.dol, a reset clears them); else only stat edits of new IDs are at risk
        (``gui_grid.stat_edit_risk``)."""
        risks = []
        state = self.loader.state
        if state is not None and state.get('kind') != 'stock':
            risks.append('The game files hold a custom roster configuration.')
        if len(self.pending):
            risks.append(f'{len(self.pending)} pending grid edit{"s" if len(self.pending) != 1 else ""} '
                         '(not written to the game yet).')
        stat_risk = gui_grid.stat_edit_risk(state, stats_lost, stats_reason)
        if stat_risk:
            risks.append(stat_risk)
        gameplay_risk = gui_grid.gameplay_risk(state) if stats_lost and stats_reason == gui_grid.STATS_REPLACED \
            else None                                # a roster run (a reset included) carries them
        if gameplay_risk:
            risks.append(gameplay_risk)
        return risks

    def config_guard(self, then, title, action, stats_lost=False, stats_reason=gui_grid.STATS_REPLACED):
        """Run ``then``; when the configuration is not vanilla, has pending grid edits or stat edits at risk ask
        first: OK (go on, pending edits are dropped) / Save Configuration (roster pack, then go on) / Cancel.
        ``stats_lost``, ``stats_reason``: ``config_risks``."""
        risks = self.config_risks(stats_lost, stats_reason)
        if not risks:
            then()
            return
        if self.confirm is not None:
            return                                   # another dialog is up
        lines = [(f'{action} replaces the current configuration, which would be lost:', gui_grid.TEXT)]
        lines += [(f'  {risk}', gui_grid.WARN) for risk in risks]
        lines.append(('"Save Configuration" stores the game as it is in a roster pack first (pending edits and '
                      'stat values are not part of it).', gui_grid.TEXT))

        def proceed():
            self._end_action()
            self.pending.clear()
            self._pending_changed()
            then()

        def save():
            self._end_action()
            self._pick_pack(save=True, after=proceed)
        self.action = 'ask'
        self._dialog(gui_grid.SlotDialog(title, lines, True), proceed, ok_label='OK',
                     extra=('Save Configuration', save))

    def switch_roster(self, path):
        """Roster Size tab, "Inject roster": pending edits are discarded first (asks), then the switch is planned
        and built in memory (``--roster --config FILE --dry-run``) and its dialog lists what the slots keep and
        lose; Inject runs the chain (``Roster/migrate.py``)."""
        if self._locked():
            return

        def check():
            self.action = 'switch'
            if not self._run([gui_grid.switch_command(path, dry_run=True)], 'Planning the roster switch...',
                             lambda code, output: self._show_switch(path, code, output)):
                self._end_action()
        self.confirm_discard(check, 'Inject the roster and discard the pending edits?',
                             'Injecting a roster rebuilds the grid, so the pending edits would no longer fit.')

    def _show_switch(self, path, code, output):
        plan = gui_grid.switch_plan(self.switch_plan_path)
        dialog = gui_grid.with_gecko_warning(gui_grid.switch_dialog(plan, code, output, path), self._gecko_break())
        accept = dialog.gecko_break

        def save():
            self._end_action()
            self._pick_pack(save=True, after=lambda: self._switch(path, accept))
        self._dialog(dialog, lambda: self._switch(path, accept), ok_label='Inject', extra=('Save Configuration', save))

    def _gecko_break(self):
        """Whether the dry run that just ended recorded a Gecko code compatibility break (``gecko_compat``)."""
        return gecko_compat.read_check(self.app.root_dir)

    def _switch(self, path, accept_gecko=False):
        self._end_action()
        self.pending.clear()
        self._pending_changed()

        def done(code, _output):
            if code == 0:
                self.app.log_line(f'[character grid] roster {os.path.basename(path)} injected; slots on both grids '
                                  'kept their customisations.', _OK)
            else:
                self.app.log_line('[character grid] the roster switch stopped at a failed step: see the log above; '
                                  'the re-read shows what landed.', _WARN)
            self.set_busy(self.app.busy)
        self._run([gui_grid.switch_command(path, accept_gecko=accept_gecko)], 'Injecting the roster...', done)

    def _on_discard_all(self):
        if self._locked() or not len(self.pending):
            return
        self.confirm_discard(lambda: None, 'Discard all pending edits?')

    def confirm_discard(self, then, title='Discard the pending edits?', reason=''):
        """Run ``then``; with pending edits ask first (Discard drops them). Presets, closing, Discard all."""
        if not len(self.pending):
            then()
            return
        if self.confirm is not None:
            return                                   # another dialog is up
        count = len(self.pending)
        lines = ([(reason, gui_grid.TEXT)] if reason else []) + [
            (f'{count} pending edit{"s" if count != 1 else ""} (nothing written for them yet):', gui_grid.TEXT)]
        state = self.loader.state
        for cid, text in self.pending.titles():
            who = '' if cid is None else f'{gui_grid.edit_who(state, int(cid, 16))}: '
            lines.append((f'  {who}{text}', gui_grid.WARN))
        lines.append(('Discard drops them.', gui_grid.TEXT))

        def discard():
            self._end_action()
            self.pending.clear()
            self._pending_changed()
            then()
        self.action = 'ask'
        self._dialog(gui_grid.SlotDialog(title, lines, True), discard, ok_label='Discard')

    def _on_patch_game(self):
        """The full dry run (fresh read, every build check), then the summary."""
        if self._locked() or not len(self.pending):
            return
        if self.pending.pack is not None:
            self._patch_game_pack()
            return
        try:
            gui_grid.write_edits(self.edits_path, self.pending.to_file())
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write {self.edits_path}: {exc}', _WARN)
            return
        self.action = 'patch_game'
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking every pending edit...',
                         self._show_summary):
            self._end_action()

    def _show_summary(self, code, output):
        state = self.nav.state or self.loader.state
        dialog = gui_grid.with_gecko_warning(gui_grid.summary_dialog(state, gui_grid.load_plan(self.plan_path), code,
                                                                     output), self._gecko_break())
        dialog = gui_grid.with_memory_warning(dialog, memory_guard.read_details(self.app.root_dir))
        accept, accept_memory = dialog.gecko_break, dialog.memory_over
        self._dialog(dialog, lambda: self._patch_game(accept, accept_memory), ok_label=self._patch_ok_label())

    def _patch_game(self, accept_gecko=False, accept_memory=False):
        self._end_action()
        deploy = self._deploying()

        def done(code, _output):
            if code == 0:
                self.pending.clear()
                self._prune_staged_icons()
                self._prune_staged_clips()
                self.app.log_line('[character grid] Patch Game done: every pending edit is written.', _OK)
                if deploy:
                    self._deploy(patched=True)          # its toast says how the copy went
                else:
                    self.app.toast.ok('Game patched: every pending edit is written.')
            else:
                self.app.log_line('[character grid] Patch Game stopped at a failed step: the pending edits are '
                                  'kept; the re-read shows what landed. Fix the cause and run Patch Game again.',
                                  _WARN)
                self.app.toast.error('Patch Game failed: the pending edits are kept.')
            self.set_busy(self.app.busy)
        self._run([gui_grid.apply_command(self.edits_path, accept_gecko, accept_memory)], 'Patching the game...', done)

    # ------------------------------------------------------------------ roster packs
    def _on_save_pack(self):
        """Save roster...: with pending edits ask first (they are not in the game yet), then the file dialog."""
        if self._locked():
            return
        if len(self.pending):
            self.action = 'ask'
            self._dialog(gui_grid.save_pending_dialog(self.loader.state, self.pending),
                         lambda: (self._end_action(), self._pick_pack(save=True)), ok_label='Save without them')
            return
        self._pick_pack(save=True)

    def _on_load_pack(self):
        """Load roster...: pending edits are discarded first (asks), then the file dialog."""
        if self._locked():
            return
        self.confirm_discard(lambda: self._pick_pack(save=False), 'Load a roster pack and discard the pending edits?',
                             'Loading a roster pack replaces the whole roster, so the pending edits would no '
                             'longer fit.')

    def _pick_pack(self, save, after=None):
        self.action = 'pack'
        self._after_save = after
        self.set_busy(self.app.busy)
        if save:
            os.makedirs(self.pack_dir, exist_ok=True)
        filters = [('Roster packs', '*' + gui_grid.PACK_EXTENSION), ('All files', '*.*')]
        on_paths = self._on_pack_save_paths if save else self._on_pack_load_paths
        if not self.app.pick_files('grid_pack_save_dialog' if save else 'grid_pack_load_dialog',
                                   'Save the roster as' if save else 'Load a roster pack', filters, on_paths,
                                   on_cancel=self._end_action, initial_dir=self.pack_dir, save=save,
                                   default_ext=gui_grid.PACK_EXTENSION.lstrip('.'), remember=PACK_DIR_SETTING):
            self._end_action()

    def _on_pack_save_paths(self, paths):
        path = paths[0] if paths else None
        after, self._after_save = self._after_save, None
        self._end_action()
        if not path:
            return
        path = gui_grid.with_extension(path)

        def done(code, _output):
            fingerprints = gui_grid.pack_fingerprints(path) if code == 0 else None
            if fingerprints is None:
                self.app.log_line('[character grid] the roster pack was not saved: see the log above.', _WARN)
                return
            self.reference = gui_grid.Reference(f'saved ({os.path.basename(path)})', fingerprints)
            self.app.log_line(f'[character grid] roster saved to {path}', _OK)
            self._refresh_markers()
            if after is not None:
                after()
        self._run([gui_grid.save_command(path)], 'Saving the roster pack...', done)

    def _on_pack_load_paths(self, paths):
        path = next((p for p in paths if p and os.path.isfile(p)), None)
        self._end_action()
        if path is None:
            self.app.log_line(f'[character grid] no roster pack chosen: {(paths or [None])[0] or "(none)"}', _WARN)
            return
        self.action = 'pack'
        if not self._run([gui_grid.load_command(path, dry_run=True)], 'Comparing the pack with the game...',
                         lambda code, output: self._show_load(path, code, output)):
            self._end_action()

    def _show_load(self, path, code, output, writing=False):
        """The load dialog: Stage adds the load to the pending list; ``writing`` (Patch Game's summary after a
        fresh dry run): Patch Game writes it."""
        state = self.loader.state
        plan = gui_grid.load_pack_plan(self.pack_plan_path)
        if writing and code == 0 and plan is not None and plan.get('ok') and not plan.get('commands'):
            self.pending.clear()                 # the game holds the pack's roster already: nothing stays pending
            self._pending_changed()
        breaks = writing and self._gecko_break()        # staging writes nothing: Patch Game's summary warns
        memory = memory_guard.read_details(self.app.root_dir)

        def build(only=True):
            return gui_grid.with_memory_warning(gui_grid.with_gecko_warning(
                gui_grid.load_dialog(state, plan, code, output, path, only, writing), breaks), memory)
        dialog = build()
        self._dialog(dialog, (lambda: self._load_pack(path, plan, dialog.gecko_break, dialog.memory_over)) if writing
                     else (lambda: self._stage_pack(path, plan)),
                     ok_label=self._patch_ok_label() if writing else 'Stage', rebuild=build,
                     toggle='Only differing slots')

    def _stage_pack(self, path, plan):
        self._end_action()
        self.pending.stage_pack(path, plan or {})
        self.app.log_line(f'[character grid] roster pack {os.path.basename(path)} staged; "Patch Game" loads it.',
                          _PENDING)
        self._pending_changed()

    def _patch_game_pack(self):
        """Patch Game with a staged pack load: check the pack against the game again, then the summary."""
        path = self.pending.pack['path']
        if not os.path.isfile(path):
            self.app.log_line(f'[character grid] the staged roster pack is gone: {path}', _WARN)
            return
        self.action = 'patch_game'
        if not self._run([gui_grid.load_command(path, dry_run=True)], 'Checking the roster pack...',
                         lambda code, output: self._show_load(path, code, output, writing=True)):
            self._end_action()

    def _load_pack(self, path, plan, accept_gecko=False, accept_memory=False):
        self._end_action()
        deploy = self._deploying()

        def done(code, _output):
            if code == 0:
                self.pending.clear()
                self.reference = gui_grid.Reference(f'loaded ({os.path.basename(path)})',
                                                    (plan or {}).get('pack_fingerprints') or {})
                self.app.log_line(f'[character grid] Patch Game done: roster pack {os.path.basename(path)} '
                                  'loaded.', _OK)
                if deploy:
                    self._deploy(patched=True)
                else:
                    self.app.toast.ok(f'Game patched: roster pack {os.path.basename(path)} loaded.')
            else:
                self.app.log_line('[character grid] loading the roster pack stopped at a failed step: the load stays '
                                  'pending; the re-read shows what landed. Run Patch Game again once the cause is '
                                  'fixed.', _WARN)
                self.app.toast.error('Patch Game failed: the roster pack load stays pending.')
            self._refresh_markers()
            self.set_busy(self.app.busy)
        self._run([gui_grid.load_command(path, accept_gecko=accept_gecko, accept_memory=accept_memory)],
                  'Loading the roster pack...', done)

    # ------------------------------------------------------------------ deploy
    def _deploying(self):
        return dpg.does_item_exist('grid_deploy') and bool(dpg.get_value('grid_deploy'))

    def _on_deploy_toggle(self, value):
        """The checkbox is remembered in the GUI settings; the Patch Game label follows it."""
        try:
            self.app.settings.set(gui_grid.DEPLOY_SETTING, bool(value))
        except OSError as exc:
            self.app.log_line(f'[character grid] could not save the setting: {exc}', _WARN)
        self.set_busy(self.app.busy)

    def _patch_ok_label(self):
        return gui_grid.PATCH_DEPLOY_LABEL if self._deploying() else gui_grid.PATCH_LABEL

    def _deploy(self, patched=False):
        """After a successful Patch Game with "Deploy patched files to game directory" ticked (``patched``), or
        the Options tab's copy: copy the game files with CopyFilesToGameDir.bat. The game directory it reads
        (3_Output_Dat/sluggiespath) is asked for here when it is missing or no game folder, so the script never
        waits for input; Cancel or a blank / wrong answer stops before the copy. Every outcome ends in a toast."""
        not_copied = 'Game patched, but the files were not copied' if patched else 'Files not copied'
        script = os.path.join(self.app.root_dir, gui_grid.DEPLOY_SCRIPT_REL)
        if not os.path.isfile(script):
            self.app.log_line(f'[character grid] files not copied: {script} is missing.', _WARN)
            self.app.toast.error(f'{not_copied}: {os.path.basename(script)} is missing.')
            return
        path_file = os.path.join(self.app.root_dir, gui_grid.GAME_DIR_FILE_REL)
        stored = gui_grid.read_game_dir(path_file)
        if gui_grid.game_dir_problem(stored) is None:
            self._copy_files(script, stored, patched)
            return

        def answered(text):
            game_dir = gui_grid.clean_game_dir(text)
            problem = gui_grid.game_dir_problem(game_dir)
            if problem is not None:
                self.app.log_line(f'[character grid] files not copied: {problem}', _WARN)
                self.app.toast.error(f'{not_copied}: {problem}', hint=None)
                return
            try:
                gui_grid.write_game_dir(path_file, game_dir)
            except (OSError, UnicodeError) as exc:
                self.app.log_line(f'[character grid] files not copied: could not write {path_file}: {exc}', _WARN)
                self.app.toast.error(f'{not_copied}: the game directory could not be stored.')
                return
            self._copy_files(script, game_dir, patched)

        def cancelled():
            self.app.log_line('[character grid] files not copied: the game directory prompt was cancelled', _WARN)
            self.app.toast.warn(f'{not_copied}: the game directory prompt was cancelled.')
        lines = ['Enter the path to your unpacked copy of Mario Super Sluggers (US): its main folder, the one '
                 'holding DATA\\files and DATA\\sys. It is kept in 3_Output_Dat\\sluggiespath for the next time.']
        if stored:
            lines.append(f'The stored one cannot be used: {gui_grid.game_dir_problem(stored)}')
        self.app.ask_text('Game directory', '\n\n'.join(lines), answered, cancelled, initial=stored)

    def _on_copy_now(self):
        """Options tab: copy the current 3_Output_Dat game files to the game directory without patching first."""
        if not self._options_locked():
            self._deploy()

    def _copy_files(self, script, game_dir, patched=False):
        def done(code, _output):
            if code == 0:
                self.app.log_line(f'[character grid] game files copied to {game_dir}.', _OK)
                self.app.toast.ok('Game patched and the files copied to the game directory.' if patched
                                  else 'Game files copied to the game directory.')
            else:
                self.app.log_line('[character grid] copying the files to the game directory failed: see the log '
                                  'above.', _WARN)
                self.app.toast.error('Game patched, but copying the files to the game directory failed.' if patched
                                     else 'Copying the files to the game directory failed.')
            self.set_busy(self.app.busy)
        self._run([gui_grid.deploy_command(script)], 'Copying the files to the game directory...', done)

    def _on_cpu_vs_cpu(self):
        """Stage CPU vs CPU on or off ("Patch Game" writes it); turning it off stages management off too."""
        if self._cpu_vs_cpu_locked():
            return
        enable = gui_grid.cpu_vs_cpu_button(self.loader.state, self.pending)[1]
        self.offer_game_options(gui_grid.cpu_vs_cpu_edits(enable, self.loader.state, self.pending),
                                'CPU vs CPU ' + ('on' if enable else 'off'))

    def _on_cpu_management(self):
        """Stage CPU vs CPU management on or off, like _on_cpu_vs_cpu; only while CPU vs CPU is on or staged on."""
        if self._cpu_vs_cpu_locked():
            return
        _label, enable, usable = gui_grid.cpu_management_button(self.loader.state, self.pending)
        if usable:
            self.offer_game_options([gui_grid.cpu_management_edit(enable)],
                                    'CPU vs CPU management ' + ('on' if enable else 'off'))

    def _on_close_play_slider(self, value):
        """The slider snaps to the levels (steps of 10%); nothing is staged until Stage is clicked."""
        dpg.set_value('grid_close_play_odds', gui_grid.close_play_snap(value))
        self._show_cpu_vs_cpu()

    def _on_close_play(self, enable):
        """Stage the slider's close play odds, or turn them off; only while CPU vs CPU is on or staged on."""
        if self._cpu_vs_cpu_locked():
            return
        percent = gui_grid.close_play_snap(dpg.get_value('grid_close_play_odds'))
        stage, disable = gui_grid.close_play_buttons(self.loader.state, self.pending, percent)
        edit = gui_grid.close_play_edit(self.loader.state, self.pending, percent if enable else None)
        if edit is not None and (stage if enable else disable):
            self.offer_game_options([edit], f'close play odds {percent}%' if enable else 'close play odds off')

    def _on_mercy_rule(self, enable):
        """Stage the input's mercy rule lead, or the stock one (10 runs)."""
        if self._cpu_vs_cpu_locked():
            return
        runs = gui_grid.mercy_rule_clamp(dpg.get_value('grid_mercy_rule_runs'))
        stage, reset = gui_grid.mercy_rule_buttons(self.loader.state, self.pending, runs)
        edit = gui_grid.mercy_rule_edit(self.loader.state, self.pending, runs if enable else None)
        if edit is not None and (stage if enable else reset):
            self.offer_game_options([edit], f'mercy rule {runs} runs' if enable else 'stock mercy rule')

    def _on_cpu_fix(self, key):
        """Stage a CPU fix (or an extra) on or off, like _on_cpu_vs_cpu; on with its companion (999 runs)."""
        if self._cpu_vs_cpu_locked():
            return
        label, _enable = gui_grid.cpu_fix_button(self.loader.state, self.pending, key)
        self.offer_game_options(gui_grid.cpu_fix_edits(self.loader.state, self.pending, key), label.lower())

    def _on_cpu_vs_cpu_discard(self):
        if self._cpu_vs_cpu_locked():
            return
        self.pending.discard_op(gui_grid.GAME_OPTION)
        self._pending_changed()
        self.app.log_line('[character grid] the pending CPU vs CPU edits were discarded.', _DIM)

    def offer_game_options(self, edits, what):
        """The staging check of game option edits (the CPU vs CPU tab): the pending edits plus these. Staged
        straight away when the check passes (an edit back to what the game has drops the pending one of that
        option); else its dialog says why."""
        if self._cpu_vs_cpu_locked():
            return False
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging(edits))
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write {self.edits_path}: {exc}', _WARN)
            return False
        self.action = 'game_option'

        def show(code, output):
            state = self.nav.state or self.loader.state
            plan = gui_grid.load_plan(self.plan_path)
            dialog = gui_grid.game_option_dialog(state, plan, code, output)
            if dialog.can_apply:
                self._stage(plan)
                self.app.log_line(f'[character grid] staged: {what}; "Patch Game" writes it.', _PENDING)
                return
            self._dialog(dialog, self._end_action)
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the game options...', show):
            self._end_action()
            return False
        return True

    def _player_heap_items(self):
        dpg.configure_item('grid_player_heap', items=gui_grid.player_heap_choices(dpg.get_value('grid_big_memory')))

    def _on_big_memory(self, checked):
        """The 128 MB checkbox only unlocks the combo's big levels; with one of them on it stays ticked."""
        if self._options_locked():
            dpg.set_value('grid_big_memory', not checked)
            return
        if not checked and gui_grid.player_heap_is_big(gui_grid.player_heap_level(self.loader.state)):
            dpg.set_value('grid_big_memory', True)
            self.app.log_line('[options] a 128 MB player memory level is on: choose a stock-memory level first.',
                              _WARN)
        self._player_heap_items()

    def _on_player_heap(self, choice):
        """Set the player memory level in main.dol right away, like the CPU vs CPU buttons."""
        current = gui_grid.player_heap_choice(gui_grid.player_heap_level(self.loader.state))
        command = gui_grid.player_heap_command(choice)
        if self._options_locked() or command is None or choice == current:
            dpg.set_value('grid_player_heap', current)
            return
        stock = choice == gui_grid.PLAYER_HEAP_STOCK_LABEL
        self._run_game_option(command, not stock, 'raised player memory' if stock else f'player memory {choice}')

    def _on_autopicker(self):
        """Turn the autopicker patch on or off in main.dol right away, like the player memory combo."""
        if self._options_locked() or self.loader.state is None:
            return
        _label, enable = gui_grid.autopicker_button(self.loader.state)
        self._run_game_option(gui_grid.autopicker_command(enable), enable, 'the autopicker patch')

    def _on_strike_zone(self):
        """Turn the unused characters' strike zone fix on or off in main.dol right away, like the autopicker patch."""
        if self._options_locked() or self.loader.state is None:
            return
        _label, enable = gui_grid.strike_zone_button(self.loader.state)
        self._run_game_option(gui_grid.strike_zone_command(enable), enable, 'the strike zone fix')

    def _on_cheat(self, key):
        """Turn a cheat on or off in main.dol right away, like the autopicker patch."""
        if self._options_locked() or self.loader.state is None:
            return
        label, enable = gui_grid.cheat_button(self.loader.state, key)
        self._run_game_option(gui_grid.cheat_command(key, enable), enable, label.split(' ', 1)[1])

    def _run_game_option(self, command, enable, name):
        verb = 'enabling' if enable else 'disabling'

        def done(code, _output):
            if code != 0:
                self.app.log_line(f'[character grid] {verb} {name} failed (see the log above).', _WARN)
            self.set_busy(self.app.busy)
        self._run([command], f'{verb.capitalize()} {name}...', done)

    def _refresh_markers(self):
        self._show_status()
        self._draw_grid()
        self._draw_popups()

    def _run(self, steps, work, on_done):
        """Run a chain of the tab's own with the spinner and ``work`` as the status line until it is done (the
        re-read after a writing chain keeps the spinner going). False when another command runs."""
        def done(code, output):
            self.work = None
            self._show_status()
            on_done(code, output)
        self.work = work
        if not self.app.run_chain(steps, on_done=done):
            self.work = None
            return False
        self._show_status()
        return True

    def _dialog(self, dialog, on_ok, ok_label='OK', rebuild=None, toggle=None, extra=None):
        """A modal dialog: its lines, then ``ok_label`` / Cancel (``dialog.can_apply``) or Close. ``toggle``: a
        checkbox label (ticked at first); changing it redraws the lines from ``rebuild(ticked)``. Long line lists
        scroll. ``extra``: (label, callback) for a middle button. With "Skip confirmation popups" on
        (``gui_grid.dialog_skippable``) it is not shown: OK runs (Close for an info dialog), the log gets the title
        and its warnings. A Gecko code compatibility break (``dialog.gecko_break``) is always shown, OK as a red
        Confirm that Enter does not press; a player memory warning (``dialog.memory_over``) likewise, OK as a red
        "... anyway" (``gui_grid.anyway_label``)."""
        if self.app.skip_confirmations() and gui_grid.dialog_skippable(dialog):
            self.app.log_line(f'[character grid] {dialog.title} ({"OK" if dialog.can_apply else "popup"} skipped)',
                              _DIM)
            for text, kind in dialog.lines:
                if kind in (gui_grid.WARN, gui_grid.ERROR):
                    self.app.log_line(f'[character grid]   {text.strip()}', _LINE_COLORS[kind])
            (on_ok if dialog.can_apply else self._end_action)()
            return
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        self.confirm = dpg.add_window(label=dialog.title, modal=True, no_collapse=True, no_saved_settings=True,
                                      autosize=True, pos=(max(0, (vw - CONFIRM_W) // 2), max(0, vh // 8)),
                                      on_close=lambda *_: self._end_action())
        if toggle:
            dpg.add_checkbox(label=toggle, default_value=True, parent=self.confirm,
                             callback=lambda _s, ticked: fill(rebuild(ticked)))
        if toggle or len(dialog.lines) > DIALOG_LINES:
            body = dpg.add_child_window(parent=self.confirm, width=CONFIRM_W + 24, height=min(vh // 2, 420))
        else:
            body = dpg.add_group(parent=self.confirm)

        def fill(content):
            dpg.delete_item(body, children_only=True)
            for text, kind in content.lines:
                dpg.add_text(text, parent=body, wrap=CONFIRM_W, color=_LINE_COLORS[kind])
        fill(dialog)
        dpg.add_spacer(height=GAP, parent=self.confirm)
        with dpg.group(horizontal=True, parent=self.confirm):
            if dialog.can_apply:
                label = ('Confirm' if dialog.gecko_break else gui_grid.anyway_label(ok_label) if dialog.memory_over
                         else ok_label)
                ok = dpg.add_button(label=label, width=110 if not dialog.memory_over else 130, height=BUTTON_H,
                                    callback=lambda: on_ok())
                dpg.bind_item_theme(ok, 'danger_theme' if dialog.danger else 'primary_theme')
                if extra:
                    dpg.add_button(label=extra[0], width=170, height=BUTTON_H, callback=lambda: extra[1]())
                dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())
            else:
                dpg.add_button(label='Close', width=110, height=BUTTON_H, callback=lambda: self._end_action())
        self.confirm_default = on_ok if dialog.can_apply and not dialog.danger else self._end_action
        self.set_busy(self.app.busy)

    def _end_action(self):
        """Close the file dialog / the open dialog; the levels respond to clicks again."""
        if self.confirm is not None and dpg.does_item_exist(self.confirm):
            dpg.delete_item(self.confirm)
        if dpg.does_item_exist('grid_dialog_textures'):
            dpg.delete_item('grid_dialog_textures', children_only=True)
        self.confirm, self.confirm_default, self.action = None, None, None
        self.stat_offer = None
        if self._package is not None:          # a package closed without Stage: the voice pack it made goes again
            from Roster import slot_zip
            slot_zip.remove_pack(self.app.root_dir, self._package[1].created_pack)
            self._package = None
        self.set_busy(self.app.busy)

    def _prune_staged_icons(self):
        """Delete normalised portraits (``_gui/slot/staged_icons``) that no pending edit uses any more."""
        gui_grid.prune_staged_icons(os.path.join(self.app.root_dir, gui_grid.STAGED_ICONS_REL),
                                    gui_grid.staged_icon_files(self.pending))



def _rgba(image):
    """An RGBA image as Dear PyGui texture data (floats 0-1)."""
    return np.asarray(image.convert('RGBA'), dtype=np.float32).ravel() / 255.0


def _source_thumbnail(preview):
    return None if preview.source is None else gui_grid.source_thumbnail(preview.source.image)


def swatches_per_row(count: int, viewport_width: int) -> int:
    """Swatches per row so the box fits the window (40 px margin), rows balanced (10 -> 5 + 5)."""
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
