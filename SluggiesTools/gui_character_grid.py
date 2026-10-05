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

Edits are staged (decision 14). The slot level's **Rename...** (a text box
with a live "fits the name plate" line), **Select .sluggie...** and
**Clear slot** run a staging check (``start.py --apply-slots edits.json
--dry-run``: the pending edits plus the new one, the new edit's build check),
then a confirm dialog with what the edit does (``gui_grid.slot_dialog``); Stage
adds it to the pending list (``gui_grid.PendingEdits``, GUI memory only).
Slots and squares with pending edits get an orange border, the slot level
lists the pending edits and previews pending portraits. **Patch Game (N)**
runs the full dry run, shows one summary (``gui_grid.summary_dialog``) and on
its Patch Game button the chain itself (one roster rebuild at most); on success the list is
cleared, on a failure it is kept. **Discard pending** / **Discard all** drop
edits. While a command runs the edit buttons are disabled; the re-read
afterwards reopens the same slot. Clicks and ``Esc`` do not move the levels
while the file dialog, a check or a dialog is up.

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
LINE = 26                        # text line height (Segoe UI 16 pt, with spacing)
SLOT_W = 720                     # level 2 may be wider than level 1: each level is its own window
PORTRAIT = (SLOT_SCALE * ICON[0], SLOT_SCALE * ICON[1])
BUTTON_H = 32
RENAME, SELECT, CLEAR, DISCARD = 'Rename...', 'Select .sluggie...', 'Clear slot', 'Discard pending'
SLOT_BUTTONS = ((RENAME, None), (SELECT, None), (CLEAR, None), ('Stats...', 7), (DISCARD, None))  # (label, phase)
FONT_REL = os.path.join('SluggiesTools', 'Roster', 'fonts', 'OpenSans.ttf')   # the name plate's font
CONFIRM_W = 680
BUSY_TEXT = 'A command is running (see the log)...'
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_WARN = (255, 210, 90, 255)
_DIM = (230, 230, 230, 255)
_EMPTY = (110, 110, 110, 255)
_ERROR = (255, 120, 110, 255)
_OK = (120, 220, 140, 255)
_PENDING = (255, 170, 70, 255)
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
        self.confirm_default = None        # what Enter does in it (its OK / Close button), or None
        self.pending = gui_grid.PendingEdits()
        self.work = None                   # what the tab's own running command does (status text), or None
        self.edits_path = os.path.join(app.root_dir, gui_grid.EDITS_REL)
        self.plan_path = os.path.join(app.root_dir, gui_grid.SLOT_PLAN_REL)

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
                dpg.add_loading_indicator(tag='grid_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='grid_status')
            dpg.add_text('', tag='grid_note', color=_WARN, wrap=900)
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
        with dpg.theme(tag='grid_empty_theme'):
            with dpg.theme_component(dpg.mvButton, enabled_state=False):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (45, 45, 48, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, _EMPTY)
        dpg.add_texture_registry(tag='grid_textures')
        with dpg.file_dialog(directory_selector=False, show=False, modal=True, tag='grid_sluggie_dialog',
                             width=760, height=460, callback=self._on_sluggie_chosen,
                             cancel_callback=lambda *_: self._end_action(),
                             default_path=self.app.models_dir if os.path.isdir(self.app.models_dir)
                             else self.app.root_dir):
            dpg.add_file_extension('.sluggie', color=(120, 220, 120, 255))
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
        dpg.set_value('grid_note', gui_grid.stock_luigi_note(state) if state else '')

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
                                           index, lambda _s, _a, u: self._open_square(u))
            self._caption(gui_grid.square_label(state, index), CELL[0],
                          gui_grid.is_fallback(state, head, gui_grid.FRONT))
        pending = self.pending.square_pending(state, index)
        if pending:
            dpg.bind_item_theme(button, 'grid_pending_theme')
        with dpg.tooltip(button):
            for line in gui_grid.square_tooltip(state, index):
                dpg.add_text(line)
            if pending:
                for cid in state['squares'][index]['members']:
                    for line in self.pending.summary(cid):
                        dpg.add_text(f'{gui_grid.name_of(state, cid)}: {line}', color=_PENDING)

    def _empty_cell(self):
        w, h = GRID_ICON[0] + 2 * FRAME, GRID_ICON[1] + 2 * FRAME
        with dpg.drawlist(width=w, height=h, indent=(CELL[0] - w) // 2):
            dpg.draw_rectangle((1, 1), (w - 1, h - 1), color=(70, 70, 74, 255), fill=(40, 40, 44, 255))
        self._caption('empty', CELL[0], color=_EMPTY)

    # ------------------------------------------------------------------ portraits
    def _texture(self, path, scale):
        """A static texture of a crop, scaled by a whole factor (nearest-neighbour); None when unreadable."""
        key = (path, scale)
        if key not in self.textures:
            try:
                with Image.open(path) as png:
                    image = png.convert('RGBA')
            except OSError:
                return None
            if scale != 1:
                image = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
            data = np.asarray(image, dtype=np.float32).ravel() / 255.0
            self.textures[key] = dpg.add_static_texture(image.width, image.height, data, parent='grid_textures')
        return self.textures[key]

    def _release_textures(self):
        dpg.delete_item('grid_textures', children_only=True)
        self.textures = {}

    def _portrait_texture(self, cid, view, scale):
        state = self.loader.state
        path = gui_grid.icon_file(state, self.loader.state_path, cid, view) if state else None
        return self._texture(path, scale) if path else None

    def _portrait_button(self, cid, view, scale, cell_w, user_data, callback):
        """The portrait as an image button, centred in ``cell_w``; a plain button where there is none. A
        fractional ``scale`` draws the next whole-factor texture smaller (less blur than scaling up)."""
        w, h = round(ICON[0] * scale), round(ICON[1] * scale)
        texture = self._portrait_texture(cid, view, math.ceil(scale))
        indent = max(0, (cell_w - w - 2 * FRAME) // 2)
        if texture is None:
            return dpg.add_button(label='no portrait', width=w + 2 * FRAME, height=h + 2 * FRAME, indent=indent,
                                  user_data=user_data, callback=callback)
        return dpg.add_image_button(texture, width=w, height=h, indent=indent, user_data=user_data,
                                    callback=callback)

    def _portrait_image(self, cid, view, preview=None):
        """The slot level's enlarged portrait (``preview``: a pending portrait's PNG instead); a framed
        placeholder where there is none."""
        texture = self._texture(preview, SLOT_SCALE) if preview else self._portrait_texture(cid, view, SLOT_SCALE)
        if preview and texture is not None:
            dpg.add_image(texture, width=PORTRAIT[0], height=PORTRAIT[1], border_color=_PENDING)
            return
        if texture is not None:
            dpg.add_image(texture, width=PORTRAIT[0], height=PORTRAIT[1], border_color=(90, 90, 96, 255))
            return
        with dpg.drawlist(width=PORTRAIT[0], height=PORTRAIT[1]):
            dpg.draw_rectangle((1, 1), (PORTRAIT[0] - 1, PORTRAIT[1] - 1), color=(120, 120, 120, 255),
                               fill=(30, 30, 34, 255))
            dpg.draw_text((10, PORTRAIT[1] // 2 - 8), 'no portrait', color=_EMPTY, size=14)

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
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        box = dpg.add_window(no_title_bar=True, no_resize=True, no_move=True, no_collapse=True,
                             no_saved_settings=True, width=width, height=height,
                             pos=(max(0, (vw - width) // 2), max(0, (vh - height) // 2)))
        dpg.bind_item_theme(box, 'grid_box_theme')
        return box

    def _square_box(self):
        state = self.nav.state
        index = self.nav.square_index()
        sq = state['squares'][index]
        members = sq['members']
        per_row = swatches_per_row(len(members), dpg.get_viewport_client_width())
        rows = -(-len(members) // per_row)
        width = max(2 * PAD + per_row * (SWATCH[0] + GAP) - GAP, 360)
        box = self._box(width, 2 * PAD + rows * (SWATCH[1] + GAP) + 60)
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
                    if self.pending.has(cid):
                        dpg.bind_item_theme(button, 'grid_pending_theme')
                    elif cid == self.nav.slot:
                        dpg.bind_item_theme(button, 'primary_theme')
                    if fallback or self.pending.has(cid):
                        with dpg.tooltip(button):
                            if fallback:
                                dpg.add_text(f'Side portrait: {gui_grid.icon_note(state, cid, gui_grid.SIDE)}')
                            for line in self.pending.summary(cid):
                                dpg.add_text(line, color=_PENDING)
        dpg.add_text(f'Voice: {gui_grid.name_of(state, sq["voice"])}', parent=box, color=_DIM)
        return box

    def _slot_box(self):
        """The slot enlarged: front and side portrait, facts, and the slot buttons (later phases' buttons are
        placeholders)."""
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
        details += [(line, _PENDING) for line in self.pending.lines(cid)]
        text_w = SLOT_W - 2 * PAD - 2 * (PORTRAIT[0] + GAP) - GAP
        body = max(PORTRAIT[1] + LINE, LINE * sum(1 + len(line) * 7 // text_w for line, _c in details))
        box = self._box(SLOT_W, 2 * PAD + 2 * LINE + body + GAP + BUTTON_H + 2 * LINE)
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
                    self._portrait_image(cid, view, preview)
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
        self.slot_buttons = []
        tips = {RENAME: 'Stage a new name for this slot (one name for English, French and Spanish; it must fit the '
                        'name plate; blank resets it). "Patch Game" writes the pending edits.',
                SELECT: 'Stage an exported model (and its High/Low partner) for this slot; a confirm dialog shows '
                        'what changes first. "Patch Game" writes the pending edits.',
                CLEAR: "Stage a return to this slot's baseline (stock: vanilla models and portraits; new ID: its "
                       "template's files and the open-slot look); a confirm dialog shows what changes first.",
                DISCARD: "Drop this slot's pending edits (nothing was written for them yet)."}
        actions = {RENAME: lambda: self._on_rename(cid), SELECT: lambda: self._on_select(cid), CLEAR: lambda: self._start_preview(cid, None),
                   DISCARD: lambda: self._on_discard(cid)}
        with dpg.group(horizontal=True, parent=box):
            for label, phase in SLOT_BUTTONS:
                if phase is not None:
                    button = dpg.add_button(label=label, height=BUTTON_H, enabled=False)
                    dpg.bind_item_theme(button, 'grid_empty_theme')
                    tip = f'Comes with plan Phase {phase}'
                else:
                    button = dpg.add_button(label=label, height=BUTTON_H, callback=actions[label],
                                            enabled=not self._locked() and (label != DISCARD or self.pending.has(cid)))
                    dpg.bind_item_theme(button, 'primary_theme')
                    self.slot_buttons.append((button, label != DISCARD or self.pending.has(cid)))
                    tip = tips[label]
                with dpg.tooltip(button):
                    dpg.add_text(tip, wrap=420)
        dpg.add_text(BUSY_TEXT if self.app.busy else '', parent=box, tag='grid_slot_busy', color=_WARN)
        return box

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

    def _pending_changed(self):
        """Redraw what shows the pending list: the grid's markers, the open levels, the buttons."""
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
        path = next((p for p in paths if p and p.lower().endswith('.sluggie') and os.path.isfile(p)), None)
        if cid is None or path is None:
            self.app.log_line(f'[character grid] no .sluggie file chosen: {paths[0] or "(none)"}', _WARN)
            self._end_action()
            return
        self.action = None
        self._start_preview(cid, path)

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

    def _on_rename_ok(self, cid):
        text = dpg.get_value('grid_rename_text').strip()
        self._end_action()
        self._start_preview(cid, None, rename=text)

    def _start_preview(self, cid, sluggie, rename=None):
        """The staging check (the pending edits plus this one; this one's build check); the confirm dialog
        opens when it is done. ``rename``: the new name text (blank resets), a rename edit instead of patch/clear."""
        if self._locked():
            return
        if rename is not None:
            edit = {'op': 'rename', 'id': gui_grid.hex_id(cid), 'text': rename}
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
                         lambda code, output: self._show_confirm(cid, sluggie, code, output, rename is not None)):
            self._end_action()

    def _show_confirm(self, cid, sluggie, code, output, rename=False):
        state = self.nav.state or self.loader.state
        plan = gui_grid.load_plan(self.plan_path)
        dialog = gui_grid.slot_dialog(state, cid, sluggie is not None, plan, code, output, self.pending,
                                      rename=rename)
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
        for edit in self.pending.edits:
            cid = int(edit['id'], 16)
            name = gui_grid.name_of(state, cid) if state else edit['id']
            lines.append((f'  {name} ({edit["id"]}): {gui_grid._edit_title(edit).removeprefix("Pending: ")}',
                          gui_grid.WARN))
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
                self.app.log_line('[character grid] Patch Game done: every pending edit is written.', _OK)
            else:
                self.app.log_line('[character grid] Patch Game stopped at a failed step: the pending edits are '
                                  'kept; the re-read shows what landed. Fix the cause and run Patch Game again.',
                                  _WARN)
            self.set_busy(self.app.busy)
        self._run([gui_grid.apply_command(self.edits_path)], 'Patching the game...', done)

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

    def _dialog(self, dialog, on_ok, ok_label='OK'):
        """A modal dialog: its lines, then ``ok_label`` / Cancel (``dialog.can_apply``) or Close."""
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        self.confirm = dpg.add_window(label=dialog.title, modal=True, no_collapse=True, no_saved_settings=True,
                                      autosize=True, pos=(max(0, (vw - CONFIRM_W) // 2), max(0, vh // 5)),
                                      on_close=lambda *_: self._end_action())
        for text, kind in dialog.lines:
            dpg.add_text(text, parent=self.confirm, wrap=CONFIRM_W, color=_LINE_COLORS[kind])
        dpg.add_spacer(height=GAP, parent=self.confirm)
        with dpg.group(horizontal=True, parent=self.confirm):
            if dialog.can_apply:
                ok = dpg.add_button(label=ok_label, width=110, height=BUTTON_H, callback=lambda: on_ok())
                dpg.bind_item_theme(ok, 'primary_theme')
                dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())
            else:
                dpg.add_button(label='Close', width=110, height=BUTTON_H, callback=lambda: self._end_action())
        self.confirm_default = on_ok if dialog.can_apply else self._end_action
        self.set_busy(self.app.busy)

    def _end_action(self):
        """Close the file dialog / the open dialog; the levels respond to clicks again."""
        if self.confirm is not None and dpg.does_item_exist(self.confirm):
            dpg.delete_item(self.confirm)
        self.confirm, self.confirm_default, self.action = None, None, None
        self.set_busy(self.app.busy)



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
