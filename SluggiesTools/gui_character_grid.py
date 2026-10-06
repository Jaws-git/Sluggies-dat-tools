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
(``3_Output_Dat/_gui/icons``): front portraits on the grid, side portraits on
the swatch row (the game uses them on wheels), both enlarged on the slot
level. They are scaled up by whole factors with nearest-neighbour, so the
pixels stay sharp (the grid's 1.5x draws the 2x texture smaller).
Portraits that are not the slot's own (a lower key, the template's, the Mii
or "?" icon) are marked. The textures are released and rebuilt on every
re-read.

Edits are staged. The slot level's **Rename...** (a text box
with a live "fits the name plate" line), **Select .sluggie...**, **Clear
slot** and **Stats...**, and the square level's **Voice...** (on the slot
level too for one-member squares; both pick from a list) and the Equipment row's tiles (Bat, Left glove, Right glove, Extra bat;
each has **Select...** for a bat/glove ``.sluggie`` and **Reset**) run a staging check (``start.py --apply-slots edits.json
--dry-run``: the pending edits plus the new one, the new edit's build check),
then a confirm dialog with what the edit does (``gui_grid.slot_dialog``); Stage
adds it to the pending list (``gui_grid.PendingEdits``, GUI memory only).
The slot level's two portraits are image buttons too: a click picks
an image file, and the icon dialog shows it, the 48x51 result (fit mode,
"Trim transparent border", redrawn in-process with ``Roster/icon_import``) and
the slot's other view; OK writes the result into
``3_Output_Dat/_gui/slot/staged_icons`` and stages an ``icon`` edit on it.
Slots and squares with pending edits get an orange border, the slot level
lists the pending edits and previews pending portraits. **Patch Game (N)**
runs the full dry run, shows one summary (``gui_grid.summary_dialog``) and on
its Patch Game button the chain itself (one roster rebuild at most); on success the list is
cleared, on a failure it is kept. **Discard pending** / **Discard all** drop
edits. While a command runs the edit buttons are disabled; the re-read
afterwards reopens the same slot. Clicks and ``Esc`` do not move the levels
while the file dialog, a check or a dialog is up.

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

On the stock grid Luigi has no square (the game hands him a captain's square
at runtime). The reader lists his family as an off-grid square, drawn to the
right of the grid's middle row, so his slots stay reachable.
"""

import math
import os
import subprocess
import threading

import dearpygui.dearpygui as dpg
import numpy as np
from PIL import Image

import gui_grid
import native_dialog

ICON = (48, 51)                  # the game's portrait size
GRID_SCALE, SWATCH_SCALE, SLOT_SCALE = 1.5, 2, 3
FRAME = 4                        # image button frame padding (each side)
GRID_ICON = (round(ICON[0] * GRID_SCALE), round(ICON[1] * GRID_SCALE))
CELL = (104, GRID_ICON[1] + 2 * FRAME + 24)
CELL_GAP = 2                     # horizontal space between grid cells
ROW_PITCH = CELL[1] + 8          # cell height plus the spacing Dear PyGui puts between a cell's items and rows
OFF_GRID_GAP = 24                # space between the grid and the squares beside it (stock Luigi)
SWATCH = (128, SWATCH_SCALE * ICON[1] + 2 * FRAME + 2 * 22)
PAD = 16
GAP = 8
BOX_MARGIN = 20                  # free space above and below a level's box
LINE = 26                        # text line height (Segoe UI 16 pt, with spacing)
SLOT_W = 720                     # level 2 may be wider than level 1: each level is its own window
PORTRAIT = (SLOT_SCALE * ICON[0], SLOT_SCALE * ICON[1])
BUTTON_H = 32
EQUIP_BLOCK = 3 * LINE + BUTTON_H + 2 * GAP     # the Equipment row: heading, tile label + state, buttons
EQUIP_BUTTON_W = 76
BADGE_FONT = 9                   # the grid's slot-count number, in portrait pixels (scaled with the texture)
RENAME, SELECT, CLEAR, DISCARD = 'Rename...', 'Select .sluggie...', 'Clear slot', 'Discard pending'
STATS, VOICE = 'Stats...', 'Voice...'
VOICE_ALL = 'Set voice (all)...'    # the square level (colour wheel): the voice reaches every member
SLOT_BUTTONS = (RENAME, CLEAR, STATS, DISCARD)  # Discard last
EQUIP_PAIR_W = 2 * EQUIP_BUTTON_W - 8 + 4   # a tile's Select... + Reset buttons and the gap between them
SLOT_BUTTON_W = EQUIP_PAIR_W      # the bottom row's buttons match that, so they align under the tiles
SELECT_W = (SLOT_W - 2 * PAD) // 3  # Select .sluggie... above the Equipment row
VOICE_TIP = ('Stage another voice for this square (a stock square: its whole species; a new square: its members '
             'without a wheel). "Patch Game" writes the pending edits.')
FONT_REL = os.path.join('SluggiesTools', 'Roster', 'fonts', 'OpenSans.ttf')   # the name plate's font
CONFIRM_W = 680
DIALOG_LINES = 24                # a dialog with more lines scrolls
BUSY_TEXT = 'A command is running (see the log)...'
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_WARN = (255, 210, 90, 255)
_DIM = (230, 230, 230, 255)
_EMPTY = (110, 110, 110, 255)
_ERROR = (255, 120, 110, 255)
_OK = (120, 220, 140, 255)
_PENDING = (255, 170, 70, 255)
_CHANGED = (110, 170, 255, 255)
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
        self.pending = gui_grid.PendingEdits()
        self.work = None                   # what the tab's own running command does (status text), or None
        self.edits_path = os.path.join(app.root_dir, gui_grid.EDITS_REL)
        self.plan_path = os.path.join(app.root_dir, gui_grid.SLOT_PLAN_REL)
        self.pack_plan_path = os.path.join(app.root_dir, gui_grid.PACK_PLAN_REL)
        self.pack_dir = os.path.join(app.root_dir, gui_grid.PACK_DIR_REL)
        self.reference = None              # gui_grid.Reference: the pack last saved / loaded (session only)

    # ------------------------------------------------------------------ build
    def build(self):
        with dpg.tab(label='Character grid', tag='grid_tab'):
            with dpg.group(horizontal=True):
                self.refresh_button = dpg.add_button(label='Refresh', callback=lambda: self.request_read())
                dpg.add_button(label='Patch Game (0)', tag='grid_patch_game', enabled=False,
                               callback=lambda: self._on_patch_game())
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
                dpg.add_loading_indicator(tag='grid_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='grid_status')
                dpg.add_spacer(width=12)
                dpg.add_button(label=gui_grid.CPU_VS_CPU_ENABLE, tag='grid_cpu_vs_cpu',
                               callback=lambda: self._on_cpu_vs_cpu())
                with dpg.tooltip('grid_cpu_vs_cpu'):
                    dpg.add_text('Turn CPU vs CPU (hold A + Minus on controller 1 while confirming the teams) and '
                                 'CPU vs CPU management (controller 1 manages the fielding team) on or off in '
                                 '3_Output_Dat/main.dol. A roster rebuild keeps them; a fresh export (menu [1]) '
                                 'does not.', wrap=420)
                dpg.add_text('', tag='grid_cpu_vs_cpu_status')
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
                    dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 3)
        with dpg.theme(tag='grid_changed_theme'):
            for kind in (dpg.mvImageButton, dpg.mvButton):
                with dpg.theme_component(kind):
                    dpg.add_theme_color(dpg.mvThemeCol_Border, _CHANGED)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 3)
        with dpg.theme(tag='grid_danger_theme'):             # like primary_theme, in red (Discard all)
            for state, colors in (
                (True, ((dpg.mvThemeCol_Button, (170, 50, 50, 255)),
                        (dpg.mvThemeCol_ButtonHovered, (200, 65, 65, 255)),
                        (dpg.mvThemeCol_ButtonActive, (135, 35, 35, 255)))),
                (False, ((dpg.mvThemeCol_Button, (80, 66, 66, 255)),
                         (dpg.mvThemeCol_Text, (150, 150, 150, 255)))),
            ):
                with dpg.theme_component(dpg.mvButton, enabled_state=state):
                    for col, value in colors:
                        dpg.add_theme_color(col, value)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
        dpg.bind_item_theme('grid_discard_all', 'grid_danger_theme')
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
        dpg.add_texture_registry(tag='grid_textures')
        with dpg.file_dialog(directory_selector=False, show=False, modal=True, tag='grid_sluggie_dialog',
                             width=760, height=460, callback=self._on_sluggie_chosen,
                             cancel_callback=lambda *_: self._end_action(),
                             default_path=self.app.models_dir if os.path.isdir(self.app.models_dir)
                             else self.app.root_dir):
            dpg.add_file_extension('.sluggie', color=(120, 220, 120, 255))
        with dpg.file_dialog(directory_selector=False, show=False, modal=True, tag='grid_image_dialog',
                             width=760, height=460, callback=self._on_image_chosen,
                             cancel_callback=lambda *_: self._end_action(),
                             default_path=self.app.models_dir if os.path.isdir(self.app.models_dir)
                             else self.app.root_dir):
            dpg.add_file_extension('Images{' + ','.join(gui_grid.IMAGE_EXTENSIONS) + '}', color=(120, 220, 120, 255))
            dpg.add_file_extension('.*')
        dpg.add_texture_registry(tag='grid_dialog_textures')    # the icon dialog's; released when it closes
        pack_dir =self.pack_dir if os.path.isdir(self.pack_dir) else self.app.root_dir
        for tag, callback in (('grid_pack_save_dialog', self._on_pack_save_chosen),
                              ('grid_pack_load_dialog', self._on_pack_load_chosen)):
            with dpg.file_dialog(directory_selector=False, show=False, modal=True, tag=tag, width=760, height=460,
                                 callback=callback, cancel_callback=lambda *_: self._end_action(),
                                 default_path=pack_dir, default_filename='roster'):
                dpg.add_file_extension(gui_grid.PACK_EXTENSION, color=(120, 180, 255, 255))
        with dpg.handler_registry():
            dpg.add_mouse_click_handler(callback=self._on_mouse_click)
            dpg.add_key_press_handler(dpg.mvKey_Escape, callback=self._on_escape)
            # on release, so a held Enter cannot also accept the next dialog
            dpg.add_key_release_handler(dpg.mvKey_Return, callback=self._on_enter)
            dpg.add_key_release_handler(dpg.mvKey_NumPadEnter, callback=self._on_enter)

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
        if self.loader.status == self.loader.FAILED:
            self.app.log_line(f'[character grid] {self.loader.message}', _WARN)
        elif self.loader.state is not None:
            for warning in self.loader.state.get('warnings', []):
                self.app.log_line(f'[character grid] warning: {warning}', _WARN)
            self.nav.refresh(self.loader.state)
        self._clear_popups()
        dpg.delete_item('grid_cells', children_only=True)
        self._release_textures()
        self._show_status()
        self._draw_grid()
        self._draw_popups()
        if again:
            self.request_read()

    def _show_status(self):
        """Spinner and status line: while the tab's own command runs (check, Patch Game) or the grid is read."""
        dpg.configure_item('grid_spinner', show=self.work is not None or self.loader.status == self.loader.RUNNING)
        dpg.set_value('grid_status', self.work or self.loader.message)
        state = self.loader.state
        text, enabled = gui_grid.cpu_vs_cpu_status(state)
        dpg.set_value('grid_cpu_vs_cpu_status', text)
        dpg.configure_item('grid_cpu_vs_cpu_status', color=_OK if enabled else _WARN if enabled is False else _DIM)
        dpg.configure_item('grid_cpu_vs_cpu', label=gui_grid.cpu_vs_cpu_button(state)[0])
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
        dpg.configure_item('grid_cells', height=rows * ROW_PITCH + 16)
        off_grid = list(state.get('off_grid') or [])
        beside = (rows - 1) // 2                  # squares on no cell (stock Luigi) sit right of the middle row
        for r in range(rows):
            with dpg.group(horizontal=True, horizontal_spacing=CELL_GAP, parent='grid_cells'):
                for c in range(cols):
                    self._grid_cell(state['cells'][r * cols + c])
                if r == beside and off_grid:
                    dpg.add_spacer(width=OFF_GRID_GAP - CELL_GAP)
                    for index in off_grid:
                        self._grid_cell(index)

    def _grid_cell(self, index):
        state = self.loader.state
        with dpg.group():
            dpg.add_spacer(width=CELL[0], height=1)     # fixes the cell width
            if index is None:
                self._empty_cell()
                return
            head = state['squares'][index]['head']
            button = self._portrait_button(head, gui_grid.FRONT, GRID_SCALE, CELL[0],
                                           index, lambda _s, _a, u: self._open_square(u),
                                           badge=gui_grid.slot_count(state, index))
            self._caption(gui_grid.square_label(state, index), CELL[0],
                          gui_grid.is_fallback(state, head, gui_grid.FRONT))
        pending = self.pending.square_pending(state, index)
        changed = self.reference is not None and self.reference.square_changed(state, index)
        if pending:
            dpg.bind_item_theme(button, 'grid_pending_theme')
        elif changed:
            dpg.bind_item_theme(button, 'grid_changed_theme')
        with dpg.tooltip(button):
            for line in gui_grid.square_tooltip(state, index):
                dpg.add_text(line)
            if pending:
                for cid in state['squares'][index]['members']:
                    for line in self.pending.summary(cid):
                        dpg.add_text(f'{gui_grid.name_of(state, cid)}: {line}', color=_PENDING)
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
    def _texture(self, path, scale, badge=None):
        """A static texture of a crop, scaled by a whole factor (nearest-neighbour); None when unreadable.
        ``badge``: a number drawn into the bottom right corner (the grid's slot count)."""
        key = (path, scale, badge)
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
            data = np.asarray(image, dtype=np.float32).ravel() / 255.0
            self.textures[key] = dpg.add_static_texture(image.width, image.height, data, parent='grid_textures')
        return self.textures[key]

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

    def _portrait_texture(self, cid, view, scale, badge=None):
        state = self.loader.state
        path = gui_grid.icon_file(state, self.loader.state_path, cid, view) if state else None
        return self._texture(path, scale, badge) if path else None

    def _portrait_button(self, cid, view, scale, cell_w, user_data, callback, badge=None):
        """The portrait as an image button, centred in ``cell_w``; a plain button where there is none. A
        fractional ``scale`` draws the next whole-factor texture smaller (less blur than scaling up)."""
        w, h = round(ICON[0] * scale), round(ICON[1] * scale)
        texture = self._portrait_texture(cid, view, math.ceil(scale), badge)
        indent = max(0, (cell_w - w - 2 * FRAME) // 2)
        if texture is None:
            label = 'no portrait' + (f' ({badge})' if badge is not None else '')
            return dpg.add_button(label=label, width=w + 2 * FRAME, height=h + 2 * FRAME, indent=indent,
                                  user_data=user_data, callback=callback)
        return dpg.add_image_button(texture, width=w, height=h, indent=indent, user_data=user_data,
                                    callback=callback)

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

    def _on_mouse_click(self, _sender, _app_data):
        if not self.popups or self.action is not None:
            return
        box = self.popups[-1][1]
        if self.nav.click(_inside(box, dpg.get_mouse_pos(local=False))):
            self._draw_popups()

    def _on_escape(self, *_):
        if self.confirm is not None:
            self._end_action()
            return
        if self.action is not None:
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
        if self.confirm is not None and dpg.does_item_exist(self.confirm):
            dpg.focus_item(self.confirm)

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
                    with dpg.group():
                        dpg.add_spacer(width=SWATCH[0], height=1)
                        button = self._portrait_button(cid, gui_grid.SIDE, SWATCH_SCALE, SWATCH[0], cid,
                                                       lambda _s, _a, u: self._open_slot(u))
                        self._caption(gui_grid.name_of(state, cid), SWATCH[0], fallback)
                        self._caption(gui_grid.hex_id(cid), SWATCH[0], color=_DIM)
                    changed = self.reference.changed(state, cid) if self.reference is not None else []
                    if self.pending.has(cid):
                        dpg.bind_item_theme(button, 'grid_pending_theme')
                    elif cid == self.nav.slot:
                        dpg.bind_item_theme(button, 'primary_theme')
                    elif changed:
                        dpg.bind_item_theme(button, 'grid_changed_theme')
                    if fallback or self.pending.has(cid) or changed:
                        with dpg.tooltip(button):
                            if fallback:
                                dpg.add_text(f'Side portrait: {gui_grid.icon_note(state, cid, gui_grid.SIDE)}')
                            for line in self.pending.summary(cid):
                                dpg.add_text(line, color=_PENDING)
                            if changed:
                                dpg.add_text(f'Changed since the pack: {", ".join(changed)}', color=_CHANGED)
        dpg.add_text(f'Voice: {gui_grid.name_of(state, sq["voice"])}', color=_DIM, parent=box)
        with dpg.group(horizontal=True, parent=box):             # the button on its own row, below the label
            self._voice_button(index, label=VOICE_ALL)
        return box

    def _voice_button(self, index, width=0, label=VOICE):
        """The square's Voice... button (square level; slot level of a one-member square)."""
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
        text_w = SLOT_W - 2 * PAD - 2 * (PORTRAIT[0] + 2 * FRAME + GAP) - GAP
        body = max(PORTRAIT[1] + 2 * FRAME + LINE, LINE * sum(1 + len(line) * 7 // text_w for line, _c in details))
        tiles = gui_grid.equipment_tiles(state, cid, self.pending)
        box = self._box(SLOT_W, 2 * PAD + 2 * LINE + body + GAP + BUTTON_H + 2 * LINE + BUTTON_H + GAP
                        + (EQUIP_BLOCK if tiles else 0) + (BUTTON_H + GAP if self.nav.skipped else 0))
        title = dpg.add_text(gui_grid.name_of(state, cid), parent=box)
        with dpg.item_handler_registry() as handlers:        # clicking the name renames, like the button
            dpg.add_item_clicked_handler(callback=lambda *_: self._on_rename(cid))
        dpg.bind_item_handler_registry(title, handlers)
        self._name_handlers.append(handlers)
        with dpg.tooltip(title):
            dpg.add_text('Click to rename this slot')
        dpg.add_separator(parent=box)
        with dpg.group(horizontal=True, horizontal_spacing=GAP, parent=box):
            for view in (gui_grid.FRONT, gui_grid.SIDE):
                with dpg.group():
                    preview = self.pending.portrait(cid, view)
                    self._portrait_slot_button(cid, view, preview)
                    fallback = gui_grid.is_fallback(state, cid, view)
                    if preview:
                        dpg.add_text(f'{view.capitalize()} (pending)', color=_PENDING)
                    else:
                        dpg.add_text(view.capitalize() + (' *' if fallback else ''),
                                     color=_WARN if fallback else _DIM)
            with dpg.group():
                for line, color in details:
                    dpg.add_text(line, color=color, wrap=text_w)
        dpg.add_spacer(height=GAP, parent=box)
        usable = self.pending.pack is None
        select = dpg.add_button(label=SELECT, width=SELECT_W, height=BUTTON_H, parent=box,
                                callback=lambda: self._on_select(cid), enabled=not self._locked() and usable)
        dpg.bind_item_theme(select, 'primary_theme')
        self.slot_buttons.append((select, usable))
        with dpg.tooltip(select):
            dpg.add_text('Stage an exported model (and its High/Low partner) for this slot; a confirm dialog shows '
                         'what changes first. "Patch Game" writes the pending edits.' if usable else
                         'A roster pack load is pending: press "Patch Game" (or Discard it) first.', wrap=420)
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
                DISCARD: "Drop this slot's pending edits (nothing was written for them yet)."}
        actions = {RENAME: lambda: self._on_rename(cid), CLEAR: lambda: self._start_preview(cid, None),
                   STATS: lambda: self._on_value('stats', cid), DISCARD: lambda: self._on_discard(cid)}
        # same width and spacing as the Equipment tiles (Select... + Reset), so the columns line up
        with dpg.group(horizontal=True, horizontal_spacing=GAP, parent=box):
            for label in SLOT_BUTTONS:
                # a staged pack load replaces the roster: slot edits wait until it is written or discarded
                usable = self.pending.has(cid) if label == DISCARD else self.pending.pack is None
                button = dpg.add_button(label=label, width=SLOT_BUTTON_W, height=BUTTON_H, callback=actions[label],
                                        enabled=not self._locked() and usable)
                dpg.bind_item_theme(button, 'grid_warn_theme' if label == DISCARD else 'primary_theme')
                self.slot_buttons.append((button, usable))
                tip = tips[label] if usable or label == DISCARD else (
                    'A roster pack load is pending: press "Patch Game" (or Discard it) first.')
                with dpg.tooltip(button):
                    dpg.add_text(tip, wrap=420)
        if self.nav.skipped:                     # one-member square: the square's Voice... on a row below
            with dpg.group(horizontal=True, horizontal_spacing=GAP, parent=box):
                self._voice_button(self.nav.square_index(), width=SLOT_BUTTON_W)
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
        if dpg.does_item_exist('grid_patch_game'):
            count = len(self.pending)
            dpg.configure_item('grid_patch_game', label=f'Patch Game ({count})', enabled=bool(count) and not locked)
            dpg.configure_item('grid_discard_all', enabled=bool(count) and not locked)
            for tag in ('grid_save_pack', 'grid_load_pack', 'grid_cpu_vs_cpu'):
                dpg.configure_item(tag, enabled=not locked)

    def _pending_changed(self):
        """Redraw what shows the pending list: the grid's markers, the open levels, the buttons."""
        self._prune_staged_icons()
        self._draw_grid()
        self._draw_popups()
        self.set_busy(self.app.busy)

    def _on_select(self, cid):
        if self._locked():
            return
        self.action = (cid, None)
        self.set_busy(False)
        if not self.app.pick_files('grid_sluggie_dialog', 'Select a .sluggie for this slot',
                                   [('Sluggie files', '*.sluggie')], self._on_sluggie_paths,
                                   on_cancel=self._end_action):
            self._end_action()

    def _on_sluggie_chosen(self, _sender, app_data):
        app_data = app_data or {}
        self._on_sluggie_paths(list(app_data.get('selections', {}).values()) or [app_data.get('file_path_name')])

    def _on_sluggie_paths(self, paths):
        cid = self.action[0] if self.action else None
        equip = self.action[2] if self.action and len(self.action) == 3 and self.action[1] == 'equip' else None
        path = next((p for p in paths if p and p.lower().endswith('.sluggie') and os.path.isfile(p)), None)
        if cid is None or path is None:
            self.app.log_line(f'[character grid] no .sluggie file chosen: {paths[0] or "(none)"}', _WARN)
            self._end_action()
            return
        self.action = None
        self._start_preview(cid, path, equip=None if equip is None else (equip, path))

    # ------------------------------------------------------------------ equipment
    def _on_equip_select(self, cid, file):
        """Select... on an equipment tile: pick a bat / glove .sluggie for slot file ``file``."""
        if self._locked() or self.pending.pack is not None:
            return
        self.action = (cid, 'equip', file)
        self.set_busy(False)
        label = gui_grid.EQUIP_LABELS[file].lower()
        if not self.app.pick_files('grid_sluggie_dialog', f'Select a {label} .sluggie for this slot',
                                   [('Sluggie files', '*.sluggie')], self._on_sluggie_paths,
                                   on_cancel=self._end_action):
            self._end_action()

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
        if not self.app.pick_files('grid_image_dialog', f'Select an image for the {view} portrait',
                                   gui_grid.IMAGE_FILTERS, self._on_image_paths, on_cancel=self._end_action,
                                   initial_dir=self.app.models_dir):
            self._end_action()

    def _on_image_chosen(self, _sender, app_data):
        app_data = app_data or {}
        self._on_image_paths(list(app_data.get('selections', {}).values()) or [app_data.get('file_path_name')])

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
        other = gui_grid.SIDE if view == gui_grid.FRONT else gui_grid.FRONT
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        win = self.confirm = dpg.add_window(
            label=f'{view.capitalize()} portrait of {gui_grid.name_of(state, cid)} ({gui_grid.hex_id(cid)})',
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
                dpg.add_text(f'New {view} (48x51)', color=_PENDING)
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
        if op == 'voice':
            cid = state['squares'][key]['head']
            choices = gui_grid.voice_choices(state, key)
            title = f'Voice of the square of {gui_grid.name_of(state, cid)}'
            lines = [gui_grid.voice_text(state, key), gui_grid.voice_reach(state, key)]
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

    def _start_preview(self, cid, sluggie, rename=None, value=None, icon=None, equip=None):
        """The staging check (the pending edits plus this one; this one's build check); the confirm dialog
        opens when it is done. ``rename``: the new name text (blank resets), a rename edit instead of patch/clear;
        ``value``: ``(op, source)``, a stats or voice edit (source None: back to the default); ``icon``: a
        finished icon edit (``IconPreview.edit``); ``equip``: ``(slot file, .sluggie or None)``, an equipment edit
        (None: reset that file)."""
        if self._locked():
            return
        kind = ('equip' if equip is not None and equip[1] else 'equip_clear' if equip is not None
                else 'icon' if icon is not None else 'rename' if rename is not None else value[0] if value
                else 'patch' if sluggie else 'clear')
        view = icon['view'] if icon is not None else None
        gear_file = equip[0] if equip is not None else None
        if equip is not None:
            edit = ({'op': 'equip', 'id': gui_grid.hex_id(cid), 'file': equip[0], 'sluggie': equip[1],
                     'origin': 'user'} if equip[1] else
                    {'op': 'equip_clear', 'id': gui_grid.hex_id(cid), 'file': equip[0]})
        elif icon is not None:
            edit = icon
        elif rename is not None:
            edit = {'op': 'rename', 'id': gui_grid.hex_id(cid), 'text': rename}
        elif value is not None:
            edit = {'op': value[0], 'id': gui_grid.hex_id(cid), 'source': value[1]}
        elif sluggie:
            edit = {'op': 'patch', 'id': gui_grid.hex_id(cid), 'file': sluggie}
        else:
            edit = {'op': 'clear', 'id': gui_grid.hex_id(cid)}
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging(edit))
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write {self.edits_path}: {exc}', _WARN)
            return
        self.action = (cid, sluggie)
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the edit...',
                         lambda code, output: self._show_confirm(cid, sluggie, code, output, kind, view,
                                                                 gear_file)):
            self._end_action()

    def _show_confirm(self, cid, sluggie, code, output, kind='patch', view=None, gear_file=None):
        state = self.nav.state or self.loader.state
        plan = gui_grid.load_plan(self.plan_path)
        dialog = gui_grid.slot_dialog(state, cid, sluggie is not None, plan, code, output, self.pending, kind=kind,
                                      view=view, gear_file=gear_file)
        if (kind in gui_grid.VALUE_KINDS + ('equip_clear',) and dialog.can_apply and code == 0 and plan is not None
                and any(int(s['target'], 16) == cid for s in plan['edits'])):
            self._stage(plan)                  # a valid rename / stats / voice pick needs no second confirmation
            return
        self._dialog(dialog, lambda: self._stage(plan), ok_label='Stage')

    def _stage(self, plan):
        self._end_action()
        self.pending.accept(plan)
        self.app.log_line(f'[character grid] {len(self.pending)} pending edit(s); "Patch Game" writes them.', _PENDING)
        self._pending_changed()

    def _on_discard(self, cid):
        if self._locked():
            return
        self.pending.discard(cid)
        self._pending_changed()

    def config_risks(self):
        """Why the current configuration would be lost by an export / roster injection (empty: vanilla, nothing
        pending, or the grid not read yet)."""
        risks = []
        state = self.loader.state
        if state is not None and state.get('kind') != 'stock':
            risks.append('The game files hold a custom roster configuration.')
        if len(self.pending):
            risks.append(f'{len(self.pending)} pending grid edit{"s" if len(self.pending) != 1 else ""} '
                         '(not written to the game yet).')
        return risks

    def config_guard(self, then, title, action):
        """Run ``then``; when the configuration is not vanilla or has pending grid edits ask first:
        OK (go on, pending edits are dropped) / Save Configuration (roster pack, then go on) / Cancel."""
        risks = self.config_risks()
        if not risks:
            then()
            return
        if self.confirm is not None:
            return                                   # another dialog is up
        lines = [(f'{action} replaces the current configuration, which would be lost:', gui_grid.TEXT)]
        lines += [(f'  {risk}', gui_grid.WARN) for risk in risks]
        lines.append(('"Save Configuration" stores the game as it is in a roster pack first (pending edits are '
                      'not part of it).', gui_grid.TEXT))

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
            who = '' if cid is None else f'{gui_grid.name_of(state, int(cid, 16)) if state else cid} ({cid}): '
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
        dialog = gui_grid.summary_dialog(state, gui_grid.load_plan(self.plan_path), code, output)
        self._dialog(dialog, self._patch_game, ok_label='Patch Game')

    def _patch_game(self):
        self._end_action()

        def done(code, _output):
            if code == 0:
                self.pending.clear()
                self._prune_staged_icons()
                self.app.log_line('[character grid] Patch Game done: every pending edit is written.', _OK)
            else:
                self.app.log_line('[character grid] Patch Game stopped at a failed step: the pending edits are '
                                  'kept; the re-read shows what landed. Fix the cause and run Patch Game again.',
                                  _WARN)
            self.set_busy(self.app.busy)
        self._run([gui_grid.apply_command(self.edits_path)], 'Patching the game...', done)

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
                                   default_ext=gui_grid.PACK_EXTENSION.lstrip('.')):
            self._end_action()

    def _on_pack_save_chosen(self, _sender, app_data):
        self._on_pack_save_paths([(app_data or {}).get('file_path_name')])

    def _on_pack_load_chosen(self, _sender, app_data):
        app_data = app_data or {}
        self._on_pack_load_paths(list(app_data.get('selections', {}).values()) or [app_data.get('file_path_name')])

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
        self._dialog(gui_grid.load_dialog(state, plan, code, output, path, writing=writing),
                     (lambda: self._load_pack(path, plan)) if writing else (lambda: self._stage_pack(path, plan)),
                     ok_label='Patch Game' if writing else 'Stage',
                     rebuild=lambda only: gui_grid.load_dialog(state, plan, code, output, path, only, writing),
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

    def _load_pack(self, path, plan):
        self._end_action()

        def done(code, _output):
            if code == 0:
                self.pending.clear()
                self.reference = gui_grid.Reference(f'loaded ({os.path.basename(path)})',
                                                    (plan or {}).get('pack_fingerprints') or {})
                self.app.log_line(f'[character grid] Patch Game done: roster pack {os.path.basename(path)} '
                                  'loaded.', _OK)
            else:
                self.app.log_line('[character grid] loading the roster pack stopped at a failed step: the load stays '
                                  'pending; the re-read shows what landed. Run Patch Game again once the cause is '
                                  'fixed.', _WARN)
            self._refresh_markers()
            self.set_busy(self.app.busy)
        self._run([gui_grid.load_command(path)], 'Loading the roster pack...', done)

    def _on_cpu_vs_cpu(self):
        """Turn CPU vs CPU and its management on or off in main.dol right away (not a staged edit: no roster data
        changes, and roster rebuilds keep game options). The re-read after it updates the label and the button."""
        if self._locked():
            return
        enable = gui_grid.cpu_vs_cpu_button(self.loader.state)[1]
        verb = 'enabling' if enable else 'disabling'

        def done(code, _output):
            if code != 0:
                self.app.log_line(f'[character grid] {verb} CPU vs CPU failed (see the log above).', _WARN)
            self.set_busy(self.app.busy)
        self._run([gui_grid.cpu_vs_cpu_command(enable)], f'{verb.capitalize()} CPU vs CPU...', done)

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
        scroll. ``extra``: (label, callback) for a middle button."""
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
                ok = dpg.add_button(label=ok_label, width=110, height=BUTTON_H, callback=lambda: on_ok())
                dpg.bind_item_theme(ok, 'primary_theme')
                if extra:
                    dpg.add_button(label=extra[0], width=170, height=BUTTON_H, callback=lambda: extra[1]())
                dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())
            else:
                dpg.add_button(label='Close', width=110, height=BUTTON_H, callback=lambda: self._end_action())
        self.confirm_default = on_ok if dialog.can_apply else self._end_action
        self.set_busy(self.app.busy)

    def _end_action(self):
        """Close the file dialog / the open dialog; the levels respond to clicks again."""
        if self.confirm is not None and dpg.does_item_exist(self.confirm):
            dpg.delete_item(self.confirm)
        if dpg.does_item_exist('grid_dialog_textures'):
            dpg.delete_item('grid_dialog_textures', children_only=True)
        self.confirm, self.confirm_default, self.action = None, None, None
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


def _fit(text: str, width: int) -> str:
    """Shorten a label to roughly fit a button (about 7 px a character)."""
    limit = max(4, (width - 12) // 7)
    return text if len(text) <= limit else text[:limit - 1] + '.'
