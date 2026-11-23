"""The GUI's "Character grid" tab: the exhibition draft grid as 3_Output_Dat holds it.

Reading runs ``start.py --roster-state`` in a background process (spinner
meanwhile, the rest of the GUI stays usable). Clicking a square pops it out:
the grid darkens and the square's slots show in a row. Clicking a slot pops
that out over the square. A click outside the popped-out box, or ``Esc``,
goes back one level. One-member squares open straight at the slot. The
logic lives in ``gui_grid`` (no Dear PyGui needed); this module only draws.

``CharacterGridTab`` is put together from mixin classes, one module each: ``gui_options_tabs`` (the CPU vs CPU,
Extras and Options tabs), ``gui_grid_textures`` (portrait and tile textures), ``gui_grid_dialogs`` (the modal
dialogs), ``gui_grid_actions`` (file picks, drops, the right-click menu) and ``gui_grid_staging`` (staging checks,
Patch Game, roster packs, deploy). They share the tab's state and no method name is defined twice. This module
keeps the tab's construction, the reader, the grid and its pop-out levels; the sizes, labels and colours are in
``gui_grid_layout`` and are imported here under their old names.

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

import chain_marker
import gecko_compat
import gui_grid
import native_dialog
from gui_grid_layout import (
    ANIM, ANIM_DIR_SETTING, BADGE_FONT, BORDER, BOX_MARGIN, BUSY_TEXT, BUTTON_H, CELL, CELL_GAP,
    CHAR_VOICE_TIP, CLEAR, CONFIRM_W, DIALOG_LINES, DISCARD, EQUIP_BLOCK, EQUIP_BUTTON_W, EQUIP_PAIR_W,
    EXTRA_ROWS, FONT_REL, FRAME, GAP, GRID_ICON, GRID_SCALE, ICON, IMAGE_DIR_SETTING, IMPORT_SLG,
    ITEM_DIR_SETTING, LINE, LOAD_PACKAGE, MODEL_FILE_DIRS, OFF_GRID_GAP, PACKAGE_DIR_SETTING,
    PACK_DIR_SETTING, PAD, PATCH_W, POPUP_SPINNER_PX, POPUP_SPINNER_RADIUS, PORTRAIT, RENAME, ROW_PITCH,
    SELECT, SELECT_LOW, SELECT_W, SIZE, SLGMODEL_DIR_SETTING, SLOT_BUTTONS, SLOT_BUTTON_W, SLOT_SCALE,
    SLOT_W, SLUGGIE_DIR_SETTING, STADIUM_DIR_SETTING, STATS, SWATCH, SWATCH_SCALE, TEXT_SPACING_Y,
    TILE_ICONS, VOICE, VOICE_ALL, VOICE_TIP, _CHANGED, _COPIED, _DIM, _EMPTY, _ERROR, _GECKO, _LINE_COLORS,
    _NO_WINDOW, _OK, _PENDING, _STATS, _WARN, _draws, _fit, _inside, _rgba, _source_thumbnail, _text_height,
    swatches_per_row,
)
from gui_options_tabs import OptionsTabsMixin
from gui_grid_textures import GridTexturesMixin
from gui_grid_dialogs import GridDialogsMixin
from gui_grid_actions import GridActionsMixin
from gui_grid_staging import GridStagingMixin

READ_TIMEOUT = 60                # s: a --roster-state read that takes longer is stopped and reported as failed


class CharacterGridTab(OptionsTabsMixin, GridTexturesMixin, GridDialogsMixin, GridActionsMixin, GridStagingMixin):
    draw_lock = threading.RLock()        # see _draws; one tab per window

    def __init__(self, app):
        self.app = app                     # gui.SluggiesGui: command prefix, root, queue, log
        self.loader = gui_grid.StateLoader(os.path.join(app.root_dir, gui_grid.STATE_REL))
        self.reader = None                 # (Popen, job or None) of a running --roster-state read
        self.nav = gui_grid.GridNav()
        self.popups = []                   # [(dim window, box window)], bottom first
        self.textures = {}                 # texture key -> texture tag (``_texture``, ``_tile_texture``)
        self._retired = {}                 # the last read's textures, until the redraw took what it still shows
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
        self._menu_prune_at = 128          # size of _menu_targets at which deleted buttons are dropped from it
        self._badge_fonts = {}             # scale -> the badge font, loaded once
        self.on_pending_changed = []       # callbacks other tabs register (the Voices tab shows pending clips)
        self.on_state_read = []            # callbacks run after every successful read
        self._package = None               # (path, slot_zip.Staged) of the package being checked, until staged / closed
        self._chain_marker_logged = None   # the unfinished-chain marker last written to the console

    # ------------------------------------------------------------------ build
    def build(self):
        with dpg.tab(label='Character grid', tag='grid_tab'):
            with dpg.group(horizontal=True):
                dpg.add_button(label='Refresh', callback=lambda: self.request_read())
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
                                 'files (sound_NA) from 3_Output_Dat into the game directory. The '
                                 'game directory is asked for the first time and kept in 3_Output_Dat/sluggiespath.',
                                 wrap=420)
                dpg.add_spacer(width=12)
                dpg.add_loading_indicator(tag='grid_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='grid_status')
            with dpg.group(horizontal=True, tag='grid_chain_marker', show=False):
                # a chain that failed or was cut off midway (chain_marker.py): nothing was rolled back
                dpg.add_button(label='Dismiss', callback=lambda: self._dismiss_chain_marker())
                dpg.add_text('', tag='grid_chain_marker_text', color=_WARN, wrap=820)
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
        self.app.add_file_dialog('grid_slgmodel_dialog', ('.slgmodel', '.*'))   # Import .slgmodel...'s fallback
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


    # ------------------------------------------------------------------ reading
    def request_read(self, if_changed=False):
        """Read the grid from the game files in the background. Not while a chain writes them (its end reads
        anyway; a read in between sees half-written files and can hold ``main.dol`` open when the chain wants
        to replace it). ``if_changed`` (a tab switch): not when the files are those of the loaded state."""
        if self.app.writing():
            return
        signature = gui_grid.state_signature(self.app.root_dir)
        if if_changed and self.loader.current(signature):
            return
        if not self.loader.start(signature):
            return                          # one runs; the refresh is queued
        self._show_status()
        command = [*self.app.command_prefix, '--roster-state']
        env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')

        def work():
            job = None
            try:
                process = subprocess.Popen(command, cwd=self.app.root_dir, env=env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           creationflags=_NO_WINDOW, start_new_session=os.name != 'nt')
                job = self.app.attach_job(process)
                self.reader = (process, job)
                try:
                    stdout, _stderr = process.communicate(timeout=READ_TIMEOUT)
                    code, output = process.returncode, stdout.decode('utf-8', errors='replace')
                except subprocess.TimeoutExpired:
                    self.app.end_process_tree(process, job)
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass                       # a child outside the job still holds the pipe
                    code, output = 1, (f'[Error] the grid reader did not finish within {READ_TIMEOUT} s and was '
                                       'stopped')
            except OSError as exc:
                code, output = 1, f'[Error] could not start the grid reader: {exc}'
            finally:
                self.reader = None
                if job is not None:
                    job.close()
            self.app.output_queue.put(('grid_state', code, output))
        threading.Thread(target=work, daemon=True).start()

    def _show_chain_marker(self):
        """The note of a chain that did not finish, above the grid; logged once per marker."""
        marker = chain_marker.read(self.app.root_dir)
        if dpg.does_item_exist('grid_chain_marker'):
            dpg.configure_item('grid_chain_marker', show=marker is not None)
            dpg.set_value('grid_chain_marker_text', chain_marker.message(marker) if marker else '')
        if marker is not None and marker != self._chain_marker_logged:
            self.app.log_line(f'[character grid] {chain_marker.message(marker)}', _WARN)
        self._chain_marker_logged = marker

    def _dismiss_chain_marker(self):
        chain_marker.clear(self.app.root_dir)
        self._show_chain_marker()

    def stop_reader(self):
        """End a running grid read (the window closes): it must not go on running unattended."""
        reader = self.reader
        if reader is not None:
            self.app.end_process_tree(*reader)

    @_draws
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
        self._retire_textures()
        self._show_status()
        self._draw_grid()                    # both redraws delete what they drew before
        self._draw_popups()
        self._drop_retired_textures()
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
        self._show_chain_marker()
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
    @_draws
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

    @_draws
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

    @_draws
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
                      and (label != SIZE or gui_grid.size_factor(gui_grid.size_of(state, cid)) is not None))
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


    def _refresh_markers(self):
        self._show_status()
        self._draw_grid()
        self._draw_popups()


# Names of gui_grid_layout that are reached through this module.
__all__ = [
    'ANIM_DIR_SETTING', 'BADGE_FONT', 'CONFIRM_W', 'DIALOG_LINES', 'EQUIP_PAIR_W', 'FONT_REL',
    'IMAGE_DIR_SETTING', 'ITEM_DIR_SETTING', 'MODEL_FILE_DIRS', 'PACKAGE_DIR_SETTING', 'PACK_DIR_SETTING',
    'SLGMODEL_DIR_SETTING', 'SLUGGIE_DIR_SETTING', 'STADIUM_DIR_SETTING', 'TILE_ICONS', '_ERROR',
    '_LINE_COLORS', '_rgba', '_source_thumbnail',
]
