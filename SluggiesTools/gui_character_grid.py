"""The GUI's "Character grid" tab: the exhibition draft grid as 3_Output_Dat holds it.

Reading runs ``start.py --roster-state`` in a background process (spinner
meanwhile, the rest of the GUI stays usable). Clicking a square pops it out:
the grid darkens and the square's slots show in a row. Clicking a slot pops
that out over the square. A click outside the popped-out box, or ``Esc``,
goes back one level. One-member squares open straight at the slot. The
logic lives in ``gui_grid`` (no Dear PyGui needed); this module only draws.

Each level is a borderless window over a full-viewport dim window, so the
dim layers stack. The dim windows never come to the front when clicked.
"""

import os
import subprocess
import threading

import dearpygui.dearpygui as dpg

import gui_grid

CELL = (108, 40)
SWATCH = (128, 56)
PAD = 16
GAP = 8
LINE = 26                        # text line height (Segoe UI 16 pt, with spacing)
SLOT_W = 720                     # level 2 may be wider than level 1: each level is its own window
PORTRAIT = (144, 153)            # 48x51 portraits at 3x
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
        dpg.configure_item('grid_cells', height=rows * (CELL[1] + 4) + 12)
        for r in range(rows):
            with dpg.group(horizontal=True, parent='grid_cells'):
                for c in range(cols):
                    index = state['cells'][r * cols + c]
                    if index is None:
                        button = dpg.add_button(label='empty', width=CELL[0], height=CELL[1], enabled=False)
                        dpg.bind_item_theme(button, 'grid_empty_theme')
                        continue
                    button = dpg.add_button(label=_fit(gui_grid.square_label(state, index), CELL[0]),
                                            width=CELL[0], height=CELL[1], user_data=index,
                                            callback=lambda _s, _a, u: self._open_square(u))
                    with dpg.tooltip(button):
                        for line in gui_grid.square_tooltip(state, index):
                            dpg.add_text(line)

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
        box = self._box(width, 2 * PAD + rows * (SWATCH[1] + GAP) + 80)
        kind = 'Stock square' if sq['kind'] == 'stock' else 'New square'
        dpg.add_text(f'{kind}: {gui_grid.name_of(state, sq["head"])}', parent=box)
        for start in range(0, len(members), per_row):
            with dpg.group(horizontal=True, horizontal_spacing=GAP, parent=box):
                for cid in members[start:start + per_row]:
                    label = _fit(gui_grid.name_of(state, cid), SWATCH[0]) + '\n' + gui_grid.hex_id(cid)
                    button = dpg.add_button(label=label, width=SWATCH[0], height=SWATCH[1], user_data=cid,
                                            callback=lambda _s, _a, u: self._open_slot(u))
                    if cid == self.nav.slot:
                        dpg.bind_item_theme(button, 'primary_theme')
        dpg.add_text(f'Voice: {gui_grid.name_of(state, sq["voice"])}', parent=box, color=_DIM)
        return box

    def _slot_box(self):
        """The slot enlarged: portrait frames, facts, and the slot buttons (placeholders until their phase)."""
        state = self.nav.state
        cid = self.nav.slot
        details = gui_grid.slot_details(state, cid)
        if self.nav.skipped:                  # one-member square: its square-level facts show here too
            sq = state['squares'][self.nav.square_index()]
            details.append(f'{"Stock" if sq["kind"] == "stock" else "New"} square, '
                           f'voice: {gui_grid.name_of(state, sq["voice"])}')
        text_w = SLOT_W - 2 * PAD - 2 * (PORTRAIT[0] + GAP) - GAP
        body = max(PORTRAIT[1] + LINE, LINE * sum(1 + len(line) * 7 // text_w for line in details))
        box = self._box(SLOT_W, 2 * PAD + 2 * LINE + body + GAP + BUTTON_H + LINE)
        dpg.add_text(gui_grid.name_of(state, cid), parent=box)
        dpg.add_separator(parent=box)
        with dpg.group(horizontal=True, horizontal_spacing=GAP, parent=box):
            for label in ('Front', 'Side'):
                with dpg.group():
                    with dpg.drawlist(width=PORTRAIT[0], height=PORTRAIT[1]):
                        dpg.draw_rectangle((1, 1), (PORTRAIT[0] - 1, PORTRAIT[1] - 1), color=(120, 120, 120, 255),
                                           fill=(30, 30, 34, 255))
                        dpg.draw_text((10, PORTRAIT[1] // 2 - 8), 'portrait (Phase 2)', color=_EMPTY, size=14)
                    dpg.add_text(label, color=_DIM)
            with dpg.group():
                for line in details:
                    dpg.add_text(line, color=_DIM, wrap=text_w)
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
