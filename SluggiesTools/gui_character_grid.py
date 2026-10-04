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
SLOT_BUTTONS = (('Rename...', 5), ('Select .sluggie...', 4), ('Clear slot', 4), ('Stats...', 7))
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_WARN = (255, 210, 90, 255)
_DIM = (230, 230, 230, 255)
_EMPTY = (110, 110, 110, 255)


class CharacterGridTab:
    def __init__(self, app):
        self.app = app                     # gui.SluggiesGui: command prefix, root, queue, log
        self.loader = gui_grid.StateLoader(os.path.join(app.root_dir, gui_grid.STATE_REL))
        self.nav = gui_grid.GridNav()
        self.popups = []                   # [(dim window, box window)], bottom first
        self.textures = {}                 # (crop path, scale) -> texture tag; released on every re-read

    # ------------------------------------------------------------------ build
    def build(self):
        with dpg.tab(label='Character grid', tag='grid_tab'):
            with dpg.group(horizontal=True):
                self.refresh_button = dpg.add_button(label='Refresh', callback=lambda: self.request_read())
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
        with dpg.theme(tag='grid_empty_theme'):
            with dpg.theme_component(dpg.mvButton, enabled_state=False):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (45, 45, 48, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, _EMPTY)
        dpg.add_texture_registry(tag='grid_textures')
        with dpg.handler_registry():
            dpg.add_mouse_click_handler(callback=self._on_mouse_click)
            dpg.add_key_press_handler(dpg.mvKey_Escape, callback=self._on_escape)

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
        dpg.configure_item('grid_spinner', show=self.loader.status == self.loader.RUNNING)
        dpg.set_value('grid_status', self.loader.message)
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
        with dpg.tooltip(button):
            for line in gui_grid.square_tooltip(state, index):
                dpg.add_text(line)

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

    def _portrait_image(self, cid, view):
        """The slot level's enlarged portrait; a framed placeholder where there is none."""
        texture = self._portrait_texture(cid, view, SLOT_SCALE)
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
        if not self.popups:
            return
        box = self.popups[-1][1]
        if self.nav.click(_inside(box, dpg.get_mouse_pos(local=False))):
            self._draw_popups()

    def _on_escape(self, *_):
        if self.nav.back():
            self._draw_popups()

    def on_viewport_resize(self):
        self._draw_popups()

    def _clear_popups(self):
        for dim, box in self.popups:
            for item in (box, dim):
                if dpg.does_item_exist(item):
                    dpg.delete_item(item)
        self.popups = []

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
                    if cid == self.nav.slot:
                        dpg.bind_item_theme(button, 'primary_theme')
                    if fallback:
                        with dpg.tooltip(button):
                            dpg.add_text(f'Side portrait: {gui_grid.icon_note(state, cid, gui_grid.SIDE)}')
        dpg.add_text(f'Voice: {gui_grid.name_of(state, sq["voice"])}', parent=box, color=_DIM)
        return box

    def _slot_box(self):
        """The slot enlarged: front and side portrait, facts, and the slot buttons (placeholders until their
        phase)."""
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
        text_w = SLOT_W - 2 * PAD - 2 * (PORTRAIT[0] + GAP) - GAP
        body = max(PORTRAIT[1] + LINE, LINE * sum(1 + len(line) * 7 // text_w for line, _c in details))
        box = self._box(SLOT_W, 2 * PAD + 2 * LINE + body + GAP + BUTTON_H + LINE)
        dpg.add_text(gui_grid.name_of(state, cid), parent=box)
        dpg.add_separator(parent=box)
        with dpg.group(horizontal=True, horizontal_spacing=GAP, parent=box):
            for view in (gui_grid.FRONT, gui_grid.SIDE):
                with dpg.group():
                    self._portrait_image(cid, view)
                    fallback = gui_grid.is_fallback(state, cid, view)
                    dpg.add_text(view.capitalize() + (' *' if fallback else ''), color=_WARN if fallback else _DIM)
            with dpg.group():
                for line, color in details:
                    dpg.add_text(line, color=color, wrap=text_w)
        dpg.add_spacer(height=GAP, parent=box)
        with dpg.group(horizontal=True, parent=box):
            for label, phase in SLOT_BUTTONS:
                button = dpg.add_button(label=label, height=BUTTON_H, enabled=False)
                dpg.bind_item_theme(button, 'grid_empty_theme')
                with dpg.tooltip(button):
                    dpg.add_text(f'Comes with plan Phase {phase}')
        return box


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
