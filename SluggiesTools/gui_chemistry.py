"""The GUI's "Chemistry" tab: every character relationship of ``main.dol``, drawn and edited (``Chemistry/``).

The tab reads the chemistry of ``3_Output_Dat/main.dol`` (without it: ``1_Input`` read-only) with the pending stat
and chemistry edits on top (``Chemistry/view.py``) and shows it in two views on one canvas:

* **Overview**: one node per character ID, placed by ``Chemistry/layout.py`` (groups by good chemistry, Miis around
  them). Green lines are good chemistry, red lines bad. Drag pans, the wheel zooms around the cursor. Hovering a
  node dims everything but its own connections; a click opens it in the focus view.
* **Focus**: one character in the middle, its good partners on the left arcs, its bad ones on the right. A click on
  a partner makes that one the centre; the **Back** button above the centre returns to the view it was opened
  from.

* **Matrix**: every character against every other, ordered by group; a green, red or empty cell per pair. A click
  on a cell cycles neutral -> good -> bad; a click on a portrait opens that character in the focus view.

The **Good**, **Bad** and **Miis** toggles apply to all three (Bad and Miis are off at first: the good lines of
the named characters alone read better).
A character without a portrait of its own in the game (the unused colour variants while they are not on the grid)
is drawn with its family's portrait, darkened; a Mii is a grey box. The panel on the right lists every other character
for the selected one (search, filter) with Good / Neutral / Bad buttons: that is the editing. Its bulk buttons:
**Copy from...** (take another character's chemistry), **Clear all** (everything neutral) and **Apply to variants**
(the same chemistry for the colour variants of its family). Chemistry is always set for both directions; a pair
the game holds one-way (another tool wrote it) is shown in purple and listed, and any edit makes it two-way again.

**Export set...** writes the chemistry the tab shows (pending edits and changes included) as a chemistry set file;
**Import set...** turns a set's pairs into changes of this tab (pairs of characters the roster does not have are
skipped), to be staged like any other.

A click sets a **draft** (yellow). **Stage** writes the drafts as a new chemistry file in ``_gui/chemistry`` and
stages it like a grid edit (``CharacterGridTab.offer_chemistry``: a dialog only for warnings or a refusal), always
as its own item at the end of the pending list, so staging order decides against the Stat Editor's edits.
**Discard pending chemistry** drops this tab's pending items only. **Patch Game (N)** runs the grid's Patch Game.
Marks: draft yellow, pending orange, differs from the unedited roster blue (a line keeps its good / bad colour;
the mark is its outline).

Node positions are kept in ``_gui/chemistry/layout.json``: an edit never moves a node, a character new to the
roster is put next to its partners, **Re-arrange** computes everything again. While a roster pack load is pending
the tab is locked, as the Gameplay tab is.
"""

import os

import dearpygui.dearpygui as dpg
import numpy as np
from PIL import Image

import gui_grid
from Chemistry import layout, read, view

_TEXT = (220, 220, 220, 255)
_DIM = (170, 170, 170, 255)
_WARN = (255, 210, 90, 255)
_OK = (120, 220, 140, 255)
_ERROR = (255, 120, 110, 255)
_PENDING = (255, 170, 70, 255)
_DRAFT = (255, 230, 120, 255)
_CHANGED = (130, 190, 255, 255)
_ODD = (200, 130, 255, 255)                    # a one-way pair
_GOOD = (80, 200, 110)
_BAD = (230, 80, 80)
MARK_COLORS = {view.DRAFT: _DRAFT, view.PENDING: _PENDING, view.CHANGED: _CHANGED, view.MISMATCH: _ODD}
MARK_TEXT = {view.DRAFT: 'edited, not staged', view.PENDING: 'pending', view.CHANGED: 'differs from the unedited '
             'roster', view.MISMATCH: 'one-way in the game'}
LOCK_TEXT = 'A roster pack load is pending: press "Patch Game" (or Discard it) first.'
LAYOUT_FILE = 'layout.json'
UNIT = 86                                      # pixels per layout unit at zoom 1
ICON = (48, 51)                                # the game's portrait size
FONT = 14                                      # label size at zoom 1
PANEL_W = 400
PATCH_W = 170
OVERVIEW, FOCUS, MATRIX = 'overview', 'focus', 'matrix'
CELL = 24                                      # a matrix cell, in world pixels
SETS_DIR_REL = os.path.join('_gui', 'Chemistry_Sets')          # where Export / Import set... start
SETS_DIR_SETTING = 'chemistry_last_dir'
COPY_HINT = 'Copy from...'
FILTERS = ('All', 'Good', 'Bad', 'Neutral', 'Edited or pending')
VALUE_LABELS = ((read.GOOD, 'Good'), (read.NEUTRAL, 'Neutral'), (read.BAD, 'Bad'))
VALUE_THEMES = {read.GOOD: 'chem_good_theme', read.NEUTRAL: 'chem_neutral_theme', read.BAD: 'chem_bad_theme'}
GROUP_HUES = ((255, 120, 120), (120, 190, 255), (255, 210, 110), (150, 230, 150), (220, 150, 255), (120, 230, 220),
              (255, 160, 90), (190, 200, 120), (255, 150, 200), (160, 160, 255), (200, 170, 140), (140, 220, 180))


def _alpha(color, alpha):
    return (color[0], color[1], color[2], alpha)


class ChemistryTab:
    def __init__(self, app):
        self.app = app
        self.grid = app.grid_tab
        root = app.root_dir
        self.out_dol = os.path.join(root, '3_Output_Dat', 'main.dol')
        self.in_dol = os.path.join(root, '1_Input', 'main.dol')
        self.out_dat = os.path.join(root, '3_Output_Dat', 'dt_na.dat')
        self.in_dat = os.path.join(root, '1_Input', 'dt_na.dat')
        self.staging_dir = os.path.join(root, gui_grid.CHEMISTRY_STAGING_REL)
        self.layout_path = os.path.join(self.staging_dir, LAYOUT_FILE)
        self.snap = view.Snapshot()
        self.drafts = {}                      # {(a, b): value} clicked, not staged
        self.layout = layout.Layout()
        self.mode = OVERVIEW
        self.selected = None                  # the character of the focus view and the panel
        self.hover = None
        self.cameras = {}                     # {mode: (zoom, pan x, pan y)}; missing: fit the picture
        self.fit_zoom = 1.0
        self.canvas = (0, 0)
        self.node_pos = {}                    # {ID: (x, y)} of the drawn scene, in world pixels
        self.node_items = {}                  # {ID: (picture item, its tint or None for a box, label item)}
        self.edge_items = {}                  # {pair: (line item, colour, alpha, outline item or None)}
        self.rows = {}                        # {other ID: ({value: button}, mark text item)} of the panel
        self.order = []                       # the matrix view's IDs, in row / column order
        self.back = None                      # the focus view's Back button: (world rect, box item, text item)
        self.back_mode = OVERVIEW             # the view the focus view was opened from
        self.cross = None                     # the matrix view's hover bars (row item, column item)
        self.sets_dir = os.path.join(root, SETS_DIR_REL)
        self.textures = {}                    # {(path, mtime, size): texture}
        self._wheel = 0.0
        self._fitted = set()                  # the views whose camera is the automatic fit (no pan or zoom yet)
        self._drag = None                     # (mouse x, mouse y, pan x, pan y, moved) while the left button is down
        self._inputs = None                   # what the snapshot was taken with
        self._files = None
        self._shown = None                    # the button state last applied
        self._scene = None                    # what the drawn scene was built from
        self._panel = None                    # what the panel was built from
        self._zoom_drawn = None
        self._in_flight = set()               # staged files whose staging check runs (never pruned)
        self.grid.on_state_read.append(self.refresh)
        self.grid.on_pending_changed.append(self.refresh)

    # ------------------------------------------------------------------ build
    def build(self):
        dpg.add_texture_registry(tag='chem_textures')
        self._build_themes()
        with dpg.tab(label='Chemistry', tag='chemistry_tab'):
            dpg.add_text('Character chemistry as the game holds it: green lines are good chemistry, red lines bad. '
                         'Click a character, then set its chemistry with the buttons on the right. Changes are '
                         'staged; "Patch Game" writes every pending edit.', wrap=1100)
            with dpg.group(horizontal=True):
                dpg.add_button(label='Patch Game', tag='chem_patch', width=PATCH_W, callback=lambda: self._on_patch())
                dpg.bind_item_theme('chem_patch', 'primary_theme')
                dpg.add_spacer(width=12)
                dpg.add_button(label='Stage', tag='chem_stage', width=110, callback=lambda: self._on_stage())
                dpg.bind_item_theme('chem_stage', 'primary_theme')
                dpg.add_button(label='Revert changes', tag='chem_revert', callback=lambda: self._on_revert())
                dpg.add_button(label='Discard pending chemistry', tag='chem_discard',
                               callback=lambda: self._on_discard())
                dpg.add_spacer(width=12)
                dpg.add_button(label='Re-arrange', tag='chem_arrange', callback=lambda: self._on_arrange())
                dpg.add_spacer(width=12)
                dpg.add_button(label='Export set...', tag='chem_export', callback=lambda: self._pick_set(save=True))
                dpg.add_button(label='Import set...', tag='chem_import', callback=lambda: self._pick_set(save=False))
                dpg.add_spacer(width=12)
                dpg.add_checkbox(label=gui_grid.DEPLOY_LABEL, tag='chem_deploy', source='grid_deploy',
                                 callback=lambda _sender, value: self.grid._on_deploy_toggle(value))
            with dpg.tooltip('chem_stage'):
                dpg.add_text('Stage the changed pairs as one new pending edit. Edits are applied in the order they '
                             'were staged: where the Stat Editor and this tab set the same pair, the later one wins.',
                             wrap=380)
            with dpg.tooltip('chem_discard'):
                dpg.add_text('Drop the pending chemistry edits of this tab (the Stat Editor\'s stay).', wrap=360)
            with dpg.tooltip('chem_arrange'):
                dpg.add_text('Compute every position again from the chemistry the game holds. Positions are kept '
                             'otherwise: an edit never moves a character.', wrap=360)
            with dpg.tooltip('chem_deploy'):
                dpg.add_text('After a successful Patch Game, copy the game files from 3_Output_Dat into the game '
                             'directory (the same setting as on the Character grid tab).', wrap=420)
            with dpg.tooltip('chem_export'):
                dpg.add_text('Save the chemistry as shown here (pending edits and changes included) as a chemistry '
                             'set file, every pair of the roster.', wrap=380)
            with dpg.tooltip('chem_import'):
                dpg.add_text('Load a chemistry set file: its pairs become changes of this tab (yellow), which Stage '
                             'then stages. Pairs of characters this roster does not have are skipped.', wrap=380)
            with dpg.group(horizontal=True):
                dpg.add_button(label='Overview', tag='chem_mode_overview', width=100,
                               callback=lambda: self._set_mode(OVERVIEW))
                dpg.add_button(label='Focus', tag='chem_mode_focus', width=100, callback=lambda: self._set_mode(FOCUS))
                dpg.add_button(label='Matrix', tag='chem_mode_matrix', width=100,
                               callback=lambda: self._set_mode(MATRIX))
                dpg.add_spacer(width=12)
                for tag, label, on in (('chem_good', 'Good', True), ('chem_bad', 'Bad', False),
                                       ('chem_miis', 'Miis', False)):
                    dpg.add_checkbox(label=label, tag=tag, default_value=on, callback=lambda: self._redraw())
                dpg.add_spacer(width=12)
                dpg.add_button(label='Fit', tag='chem_fit', callback=lambda: self._fit())
                dpg.add_spacer(width=12)
                dpg.add_text('', tag='chem_counts', color=_DIM)
            dpg.add_text('', tag='chem_source', color=_DIM, wrap=1100)
            dpg.add_text('', tag='chem_status', wrap=1100)
            dpg.add_text('', tag='chem_odd', color=_ODD, wrap=1100, show=False)
            with dpg.group(horizontal=True):
                for mark in (view.DRAFT, view.PENDING, view.CHANGED, view.MISMATCH):
                    dpg.add_text('■', color=MARK_COLORS[mark])
                    dpg.add_text(MARK_TEXT[mark], color=_DIM)
                    dpg.add_spacer(width=8)
                dpg.add_text('', tag='chem_hover', color=_TEXT)
            with dpg.group(horizontal=True):
                with dpg.child_window(tag='chem_canvas_box', border=True, width=-(PANEL_W + 12), height=-36,
                                      no_scrollbar=True, no_scroll_with_mouse=True):
                    with dpg.drawlist(width=100, height=100, tag='chem_canvas'):
                        dpg.add_draw_node(tag='chem_world')
                with dpg.child_window(tag='chem_panel', border=True, width=PANEL_W, height=-36):
                    dpg.add_text('Click a character to see and edit its chemistry.', tag='chem_panel_head',
                                 wrap=PANEL_W - 30)
                    dpg.add_text('', tag='chem_panel_counts', color=_DIM)
                    with dpg.group(horizontal=True, tag='chem_bulk', show=False):
                        dpg.add_combo([], tag='chem_copy_from', default_value=COPY_HINT, width=150,
                                      callback=lambda _s, label: self._on_copy_from(label))
                        dpg.add_button(label='Clear all', tag='chem_clear_all', callback=lambda: self._on_clear_all())
                        dpg.add_button(label='Apply to variants', tag='chem_variants',
                                       callback=lambda: self._on_variants())
                    with dpg.tooltip('chem_copy_from'):
                        dpg.add_text('Give this character the chemistry another character has towards everyone else '
                                     '(the pair of the two stays).', wrap=340)
                    with dpg.tooltip('chem_clear_all'):
                        dpg.add_text('Set every pair of this character to neutral.', wrap=340)
                    with dpg.tooltip('chem_variants'):
                        dpg.add_text('Give the other characters of its family (colour variants) the chemistry this '
                                     'one has towards everyone outside the family.', wrap=340)
                    with dpg.group(horizontal=True):
                        dpg.add_input_text(tag='chem_search', hint='search', width=170,
                                           callback=lambda: self._rebuild_panel())
                        dpg.add_combo(FILTERS, tag='chem_filter', default_value=FILTERS[0], width=170,
                                      callback=lambda: self._rebuild_panel())
                    dpg.add_child_window(tag='chem_rows', border=False)
        with dpg.handler_registry():
            dpg.add_mouse_wheel_handler(callback=self._on_wheel)
        self.app.add_file_dialog('chem_save_dialog', (view.EXTENSION,), color=(120, 220, 160, 255),
                                 default_filename='chemistry')
        self.app.add_file_dialog('chem_load_dialog', (view.EXTENSION, '.json', '.*'), color=(120, 220, 160, 255))

    def _build_themes(self):
        for tag, base in (('chem_good_theme', (46, 140, 64)), ('chem_bad_theme', (170, 50, 50)),
                          ('chem_neutral_theme', (110, 110, 118)), ('chem_mode_theme', (70, 100, 150))):
            with dpg.theme(tag=tag):
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, base)
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, tuple(min(255, c + 25) for c in base))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, tuple(max(0, c - 20) for c in base))

    # ------------------------------------------------------------------ reading
    def _snapshot_inputs(self):
        pending = self.grid.pending
        return (tuple(gui_grid.staged_stat_items(pending)), tuple(gui_grid.pending_pastes(pending)))

    def _file_keys(self):
        out = []
        for path in (self.out_dol, self.in_dol):
            try:
                st = os.stat(path)
                out.append((st.st_size, st.st_mtime_ns))
            except OSError:
                out.append(None)
        return tuple(out)

    def refresh(self):
        """Take a new snapshot (a re-read, a pending change) and redraw."""
        if not dpg.does_item_exist('chemistry_tab'):
            return
        self._inputs = self._snapshot_inputs()
        self._files = self._file_keys()
        items, pastes = self._inputs
        self.snap = view.snapshot(self.out_dol, self.in_dol, list(items), self.out_dat, self.in_dat, list(pastes))
        view.prune_drafts(self.snap, self.drafts)
        view.prune_staging(self.staging_dir, [*gui_grid.staged_chemistry_files(self.grid.pending), *self._in_flight])
        if self.selected is not None and self.snap.character(self.selected) is None:
            self.selected = None
            if self.mode == FOCUS:
                self.mode = OVERVIEW
        self._arrange()
        self._scene = self._panel = self._shown = None
        self._redraw()

    def _good_pairs(self):
        return [pair for pair, both in self.snap.current.items() if both == (read.GOOD, read.GOOD)]

    def _arrange(self, fresh=False):
        """Positions for the roster's IDs: the kept ones, new IDs beside their partners; ``fresh``: all again."""
        ids = self.snap.ids
        if not ids:
            self.layout = layout.Layout()
            return
        kept = {} if fresh else (self.layout.positions or layout.load(self.layout_path))
        if not fresh and set(kept) >= set(ids) and self.layout.positions:
            return
        miis = [c.id for c in self.snap.characters if c.mii]
        self.layout = layout.arrange(ids, self._good_pairs(), miis, kept)
        if fresh or set(self.layout.positions) != set(kept):
            try:
                layout.save(self.layout_path, self.layout.positions)
            except OSError as exc:
                self.app.log_line(f'[chemistry] could not keep the positions in {self.layout_path}: {exc}', _WARN)
        self.cameras.pop(OVERVIEW, None)

    # ------------------------------------------------------------------ the scene
    def _toggles(self):
        return (dpg.get_value('chem_good'), dpg.get_value('chem_bad'), dpg.get_value('chem_miis'))

    def _hidden(self):
        """The IDs the Miis toggle hides (never the selected character)."""
        if dpg.get_value('chem_miis'):
            return set()
        return {c.id for c in self.snap.characters if c.mii and c.id != self.selected}

    def _portrait(self, cid):
        """``(texture, tint)`` for a character: its own portrait, else its family's, darkened (the game holds no
        portrait for it); ``(None, None)`` when there is neither (Miis)."""
        texture = self._texture(cid)
        if texture is not None:
            return texture, (255, 255, 255)
        me = self.snap.character(cid)
        if me is not None and me.family != cid:
            texture = self._texture(me.family)
            if texture is not None:
                return texture, (120, 120, 135)
        return None, None

    def _texture(self, cid):
        state = self.grid.loader.state
        path = gui_grid.icon_file(state, self.grid.loader.state_path, cid, 'front', check=False) if state else None
        if not path:
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (path, st.st_mtime_ns, st.st_size)
        if key not in self.textures:
            try:
                with Image.open(path) as png:
                    image = png.convert('RGBA')
            except OSError:
                return None
            data = np.asarray(image, dtype=np.float32).ravel() / 255.0
            self.textures[key] = dpg.add_static_texture(image.width, image.height, data, parent='chem_textures')
        return self.textures[key]

    def _text_width(self, text):
        size = dpg.get_text_size(text)
        base = dpg.get_text_size('M')
        if not size or not base or not base[1]:
            return len(text) * FONT * 0.5
        return size[0] * FONT / base[1]

    def _label(self, cid):
        text = self.snap.label(cid)
        return text if len(text) <= 17 else text[:15] + '..'

    def _redraw(self):
        """Draw the scene of the current view (nodes, lines) and update the panel and the counts."""
        if not dpg.does_item_exist('chem_world'):
            return
        snap = self.snap
        good_on, bad_on, _miis = self._toggles()
        hidden = self._hidden()
        found = view.connections(snap, self.drafts, hidden) if snap.usable else {}
        marks = view.marks(snap, self.drafts) if snap.usable else {}
        if self.mode == MATRIX and snap.usable:
            self._draw_matrix(found, marks, good_on, bad_on, hidden)
            self._after_redraw(found, hidden)
            return
        if self.mode == FOCUS and self.selected is not None:
            positions, edges = self._focus_scene(found, good_on, bad_on)
        else:
            self.mode = OVERVIEW
            positions = {c: (x * UNIT, y * UNIT) for c, (x, y) in self.layout.positions.items() if c not in hidden
                         and snap.character(c) is not None}
            edges = {pair: value for pair, value in found.items()
                     if (value == read.GOOD and good_on) or (value == read.BAD and bad_on)
                     or (value is None and (good_on or bad_on))}
        dpg.delete_item('chem_world', children_only=True)
        self.node_pos, self.node_items, self.edge_items, self.back = positions, {}, {}, None
        focus = self.mode == FOCUS
        for pair, value in sorted(edges.items(), key=lambda e: (e[1] != read.BAD, e[0])):     # bad first: good on top
            if pair[0] not in positions or pair[1] not in positions:
                continue
            p, q = positions[pair[0]], positions[pair[1]]
            color = _ODD[:3] if value is None else _GOOD if value == read.GOOD else _BAD
            alpha = 230 if focus else 120 if value == read.GOOD else 85
            outline = None
            mark = marks.get(pair)
            if mark in (view.DRAFT, view.PENDING, view.CHANGED):
                outline = dpg.draw_line(p, q, color=_alpha(MARK_COLORS[mark], 230), thickness=5, parent='chem_world')
                alpha = 255
            line = dpg.draw_line(p, q, color=_alpha(color, alpha), thickness=2 if outline or focus else 1,
                                 parent='chem_world')
            self.edge_items[pair] = (line, color, alpha, outline)
        for cid, (x, y) in positions.items():
            self._draw_node(cid, x, y, big=focus and cid == self.selected)
        self._after_redraw(found, hidden)

    def _after_redraw(self, found, hidden):
        snap = self.snap
        self._scene = (self.mode, self.selected)
        self._zoom_drawn = None
        self.hover = None
        good, bad, odd = view.counts(found)
        people = len(snap.characters) - len(hidden)
        dpg.set_value('chem_counts', f'{people} characters, {good} good pairs, {bad} bad pairs'
                      + (f', {odd} one-way' if odd else '') if snap.usable else '')
        self._show_mismatches()
        self._apply_camera()
        self._rebuild_panel()
        self._shown = None

    def _draw_matrix(self, found, marks, good_on, bad_on, hidden):
        """The matrix view: one row and one column per character (``view.matrix_order``), a cell per pair."""
        dpg.delete_item('chem_world', children_only=True)
        self.node_pos, self.node_items, self.edge_items, self.back = {}, {}, {}, None
        order = self.order = view.matrix_order(self.snap, self.layout.groups, hidden)
        index = {cid: i for i, cid in enumerate(order)}
        n = len(order)
        side = n * CELL
        dpg.draw_rectangle((0, 0), (side, side), color=(90, 90, 100, 255), fill=(34, 34, 40, 255), parent='chem_world')
        for i, cid in enumerate(order):                 # the diagonal, the portraits, a line where the group changes
            at = i * CELL
            dpg.draw_rectangle((at, at), (at + CELL, at + CELL), color=(0, 0, 0, 0), fill=(60, 60, 68, 255),
                               parent='chem_world')
            texture, tint = self._portrait(cid)
            for pmin in ((-CELL - 4, at), (at, -CELL - 4)):
                pmax = (pmin[0] + CELL, pmin[1] + CELL)
                if texture is not None:
                    dpg.draw_image(texture, (pmin[0] + 1, pmin[1]), (pmax[0] - 1, pmax[1]), color=_alpha(tint, 255),
                                   parent='chem_world')
                else:
                    dpg.draw_rectangle((pmin[0] + 2, pmin[1] + 2), (pmax[0] - 2, pmax[1] - 2),
                                       color=(150, 150, 160, 255), fill=(70, 70, 80, 255), rounding=3,
                                       parent='chem_world')
            if i and self.layout.groups.get(cid, -1) != self.layout.groups.get(order[i - 1], -1):
                dpg.draw_line((at, 0), (at, side), color=(110, 110, 125, 255), parent='chem_world')
                dpg.draw_line((0, at), (side, at), color=(110, 110, 125, 255), parent='chem_world')
        for (a, b), value in found.items():
            if a not in index or b not in index:
                continue
            if not ((value == read.GOOD and good_on) or (value == read.BAD and bad_on)
                    or (value is None and (good_on or bad_on))):
                continue
            fill = _alpha(_ODD[:3] if value is None else _GOOD if value == read.GOOD else _BAD, 235)
            mark = marks.get((a, b))
            edge = MARK_COLORS[mark] if mark in (view.DRAFT, view.PENDING, view.CHANGED) else (0, 0, 0, 0)
            for row, col in ((index[a], index[b]), (index[b], index[a])):
                dpg.draw_rectangle((col * CELL + 2, row * CELL + 2), ((col + 1) * CELL - 2, (row + 1) * CELL - 2),
                                   color=edge, fill=fill, thickness=2, rounding=2, parent='chem_world')
        for pair, mark in marks.items():                # a draft or pending edit to neutral: an empty marked cell
            if pair in found or mark not in (view.DRAFT, view.PENDING) or pair[0] not in index or pair[1] not in index:
                continue
            for row, col in ((index[pair[0]], index[pair[1]]), (index[pair[1]], index[pair[0]])):
                dpg.draw_rectangle((col * CELL + 2, row * CELL + 2), ((col + 1) * CELL - 2, (row + 1) * CELL - 2),
                                   color=MARK_COLORS[mark], thickness=2, rounding=2, parent='chem_world')
        bar = (255, 255, 255, 26)
        self.cross = (dpg.draw_rectangle((0, 0), (0, 0), color=(0, 0, 0, 0), fill=bar, show=False, parent='chem_world'),
                      dpg.draw_rectangle((0, 0), (0, 0), color=(0, 0, 0, 0), fill=bar, show=False, parent='chem_world'))

    def _cell_at(self, x, y):
        """``(row ID or None, column ID or None)`` under a canvas point of the matrix view (a header has one)."""
        wx, wy = self._to_world(x, y)
        n = len(self.order)
        col, row = int(wx // CELL), int(wy // CELL)
        in_col, in_row = 0 <= col < n and wx >= 0, 0 <= row < n and wy >= 0
        if in_col and in_row:
            return self.order[row], self.order[col]
        if in_row and -CELL - 4 <= wx < 0:
            return self.order[row], None
        if in_col and -CELL - 4 <= wy < 0:
            return None, self.order[col]
        return None, None

    def _matrix_hover(self, cell):
        if cell == self.hover:
            return
        self.hover = cell
        row, col = cell if cell else (None, None)
        index = {cid: i for i, cid in enumerate(self.order)}
        side = len(self.order) * CELL
        if self.cross:
            for item, cid, across in ((self.cross[0], row, True), (self.cross[1], col, False)):
                if cid is None or not dpg.does_item_exist(item):
                    if dpg.does_item_exist(item):
                        dpg.configure_item(item, show=False)
                    continue
                at = index[cid] * CELL
                dpg.configure_item(item, show=True, pmin=(-CELL - 4, at) if across else (at, -CELL - 4),
                                   pmax=(side, at + CELL) if across else (at + CELL, side))
        if row is not None and col is not None and row != col:
            dpg.set_value('chem_hover', f'{self.snap.label(row)} x {self.snap.label(col)}: '
                          + view.tooltip(self.snap, self.drafts, row, col) + '   (click: change)')
        elif row is not None or col is not None:
            cid = row if row is not None else col
            dpg.set_value('chem_hover', f'{self.snap.label(cid)} (0x{cid:02X})')
        else:
            dpg.set_value('chem_hover', '')

    def _matrix_click(self, cell):
        row, col = cell
        if row is not None and col is not None:
            if row != col and self._editable():
                view.cycle(self.snap, self.drafts, row, col)
                self._redraw()
        elif row is not None or col is not None:
            self._on_click(row if row is not None else col)

    def _focus_scene(self, found, good_on, bad_on):
        cid = self.selected
        good, bad, edges = [], [], {}
        for pair, value in found.items():
            if cid not in pair:
                continue
            other = pair[1] if pair[0] == cid else pair[0]
            side = value
            if value is None:                           # one-way: by the centre's own direction, else the other's
                both = view.shown(self.snap, self.drafts, pair)
                mine = both[0] if pair[0] == cid else both[1]
                theirs = both[1] if pair[0] == cid else both[0]
                side = mine if mine != read.NEUTRAL else theirs
            if (side == read.GOOD and not good_on) or (side != read.GOOD and not bad_on):
                continue
            (good if side == read.GOOD else bad).append(other)
            edges[pair] = value
        ring = layout.focus_ring(sorted(good), sorted(bad))
        positions = {other: (x * UNIT, y * UNIT) for other, (x, y) in ring.items()}
        positions[cid] = (0.0, 0.0)
        return positions, edges

    def _draw_node(self, cid, x, y, big=False):
        scale = 1.6 if big else 1.0
        w, h = ICON[0] * scale, ICON[1] * scale
        top = y - h / 2 - 8
        group = self.layout.groups.get(cid, -1)
        if self.mode == OVERVIEW and group >= 0:
            hue = GROUP_HUES[group % len(GROUP_HUES)]
            dpg.draw_rectangle((x - layout.BOX_W * UNIT / 2 + 4, y - layout.BOX_H * UNIT / 2 + 4),
                               (x + layout.BOX_W * UNIT / 2 - 4, y + layout.BOX_H * UNIT / 2 - 4),
                               color=_alpha(hue, 0), fill=_alpha(hue, 26), rounding=8, parent='chem_world')
        texture, tint = self._portrait(cid)
        if texture is not None:
            picture = dpg.draw_image(texture, (x - w / 2, top), (x + w / 2, top + h), color=_alpha(tint, 255),
                                     parent='chem_world')
        else:
            picture = dpg.draw_rectangle((x - w / 2, top), (x + w / 2, top + h), color=(150, 150, 160, 255),
                                         fill=(70, 70, 80, 255), rounding=6, parent='chem_world')
        if cid == self.selected:
            dpg.draw_rectangle((x - w / 2 - 3, top - 3), (x + w / 2 + 3, top + h + 3), color=_DRAFT, thickness=2,
                               rounding=4, parent='chem_world')
        text = self._label(cid)
        label = dpg.draw_text((x - self._text_width(text) / 2, top + h + 2), text, color=_TEXT, size=FONT,
                              parent='chem_world')
        self.node_items[cid] = (picture, tint, label)
        if big:                                         # 3. the way back, above the centre
            bw, bh = 84, 26
            rect = (x - bw / 2, top - bh - 12, x + bw / 2, top - 12)
            box = dpg.draw_rectangle(rect[:2], rect[2:], color=(150, 170, 210, 255), fill=(60, 80, 120, 255),
                                     rounding=5, parent='chem_world')
            caption = '< Back'
            item = dpg.draw_text((x - self._text_width(caption) / 2, rect[1] + (bh - FONT) / 2 - 1), caption,
                                 color=_TEXT, size=FONT, parent='chem_world')
            self.back = (rect, box, item)

    def _show_mismatches(self):
        odd = view.mismatches(self.snap.pending) if self.snap.usable else []
        if not odd:
            dpg.configure_item('chem_odd', show=False)
            return
        names = [f'{self.snap.label(a)} x {self.snap.label(b)}' for a, b, _ab, _ba in odd[:6]]
        more = f', +{len(odd) - 6} more' if len(odd) > 6 else ''
        dpg.set_value('chem_odd', f'{len(odd)} one-way pair{"s" if len(odd) != 1 else ""} (the two directions '
                      f'differ; another tool wrote them): {", ".join(names)}{more}. Setting a pair here makes it '
                      'two-way again.')
        dpg.configure_item('chem_odd', show=True)

    # ------------------------------------------------------------------ camera
    def _bounds(self):
        if self.mode == MATRIX and self.order:
            side = len(self.order) * CELL
            return (-CELL - 12.0, -CELL - 12.0, side + 8.0, side + 8.0)
        if not self.node_pos:
            return (-100.0, -100.0, 100.0, 100.0)
        xs = [p[0] for p in self.node_pos.values()]
        ys = [p[1] for p in self.node_pos.values()]
        half_w, half_h = layout.BOX_W * UNIT / 2 + 10, layout.BOX_H * UNIT / 2 + 14
        return (min(xs) - half_w, min(ys) - half_h, max(xs) + half_w, max(ys) + half_h)

    def _fit_camera(self):
        left, top, right, bottom = self._bounds()
        w, h = max(self.canvas[0], 50), max(self.canvas[1], 50)
        zoom = min(w / max(right - left, 1), h / max(bottom - top, 1), 1.6)
        return (zoom, w / 2 - (left + right) / 2 * zoom, h / 2 - (top + bottom) / 2 * zoom)

    def _zoom_limits(self):
        """Zoom limits follow the picture and the canvas: out to half of "everything fits", in to 3x."""
        return (min(self._fit_camera()[0] * 0.5, 0.5), 3.0)

    def _fit(self):
        self.cameras.pop(self.mode, None)
        self._apply_camera()

    def _move_camera(self, zoom, pan_x, pan_y):
        self.cameras[self.mode] = (zoom, pan_x, pan_y)
        self._fitted.discard(self.mode)
        self._apply_camera()

    def _apply_camera(self):
        if not dpg.does_item_exist('chem_world'):
            return
        if self.mode not in self.cameras:
            self.cameras[self.mode] = self._fit_camera()
            self._fitted.add(self.mode)
        zoom, pan_x, pan_y = self.cameras[self.mode]
        dpg.apply_transform('chem_world', dpg.create_translation_matrix([pan_x, pan_y])
                            * dpg.create_scale_matrix([zoom, zoom, 1]))
        if zoom != self._zoom_drawn:                    # the transform moves points; text keeps its size
            self._zoom_drawn = zoom
            size = FONT * zoom
            for _picture, _tint, label in self.node_items.values():
                dpg.configure_item(label, size=max(size, 1), show=size >= 7)
            if self.back and dpg.does_item_exist(self.back[2]):
                dpg.configure_item(self.back[2], size=max(size, 1))

    def _to_world(self, x, y):
        zoom, pan_x, pan_y = self.cameras.get(self.mode, (1.0, 0.0, 0.0))
        return ((x - pan_x) / zoom, (y - pan_y) / zoom)

    def _on_back(self, x, y):
        """Whether a canvas point is on the focus view's Back button."""
        if self.mode != FOCUS or not self.back:
            return False
        wx, wy = self._to_world(x, y)
        left, top, right, bottom = self.back[0]
        return left <= wx <= right and top <= wy <= bottom

    def _node_at(self, x, y):
        wx, wy = self._to_world(x, y)
        best, best_d = None, None
        for cid, (nx, ny) in self.node_pos.items():
            if abs(wx - nx) <= layout.BOX_W * UNIT / 2 - 4 and abs(wy - ny) <= layout.BOX_H * UNIT / 2 - 4:
                d = (wx - nx) ** 2 + (wy - ny) ** 2
                if best_d is None or d < best_d:
                    best, best_d = cid, d
        return best

    # ------------------------------------------------------------------ mouse
    def _on_wheel(self, _sender, delta):
        self._wheel += delta

    def _mouse(self):
        """Pan, zoom, hover and click on the canvas (polled every frame while the tab shows)."""
        wheel, self._wheel = self._wheel, 0.0
        if not self._showing():
            self._drag = None
            return
        hovered = bool(dpg.get_item_state('chem_canvas').get('hovered'))
        down = dpg.is_mouse_button_down(dpg.mvMouseButton_Left)
        mouse = dpg.get_mouse_pos(local=False)
        if self._drag is not None:
            start_x, start_y, pan_x, pan_y, moved = self._drag
            dx, dy = mouse[0] - start_x, mouse[1] - start_y
            moved = moved or abs(dx) + abs(dy) > 4
            if moved:
                self._move_camera(self.cameras[self.mode][0], pan_x + dx, pan_y + dy)
            if down:
                self._drag = (start_x, start_y, pan_x, pan_y, moved)
            else:
                self._drag = None
                if not moved and hovered:
                    if self.mode == MATRIX:
                        self._matrix_click(self._cell_at(*dpg.get_drawing_mouse_pos()))
                    elif self._on_back(*dpg.get_drawing_mouse_pos()):
                        self._set_mode(self.back_mode)
                    else:
                        self._on_click(self._node_at(*dpg.get_drawing_mouse_pos()))
            return
        if not hovered or self.grid.confirm is not None:
            self._matrix_hover(None) if self.mode == MATRIX else self._set_hover(None)
            return
        local = dpg.get_drawing_mouse_pos()
        if wheel and self.mode in self.cameras:
            zoom, pan_x, pan_y = self.cameras[self.mode]
            low, high = self._zoom_limits()
            new = min(max(zoom * (1.15 ** wheel), low), high)
            ratio = new / zoom
            self._move_camera(new, local[0] - (local[0] - pan_x) * ratio, local[1] - (local[1] - pan_y) * ratio)
        if dpg.is_mouse_button_clicked(dpg.mvMouseButton_Left) and self.mode in self.cameras:
            _zoom, pan_x, pan_y = self.cameras[self.mode]
            self._drag = (mouse[0], mouse[1], pan_x, pan_y, False)
            return
        if self.mode == MATRIX:
            cell = self._cell_at(*local)
            self._matrix_hover(cell if cell != (None, None) else None)
        else:
            over_back = self._on_back(*local)
            if self.back and dpg.does_item_exist(self.back[1]):
                dpg.configure_item(self.back[1], fill=(85, 110, 160, 255) if over_back else (60, 80, 120, 255))
            self._set_hover(None if over_back else self._node_at(*local))

    def _set_hover(self, cid):
        if cid == self.hover:
            return
        self.hover = cid
        focus = self.mode == FOCUS
        mine = {cid} if cid is not None else set()
        for pair, (line, color, alpha, outline) in self.edge_items.items():
            own = cid is None or cid in pair
            if cid is not None and own:
                mine.update(pair)
            if not focus:
                dpg.configure_item(line, color=_alpha(color, (255 if cid is not None else alpha) if own else 14),
                                   thickness=2 if (cid is not None and own) or outline else 1)
                if outline:
                    dpg.configure_item(outline, show=own)
        if not focus:
            for other, (picture, tint, label) in self.node_items.items():
                lit = cid is None or other in mine
                if tint is not None:
                    dpg.configure_item(picture, color=_alpha(tint, 255 if lit else 60))
                dpg.configure_item(label, color=_alpha(_TEXT, 255 if lit else 70))
        if cid is None:
            dpg.set_value('chem_hover', '')
        elif focus and self.selected is not None and cid != self.selected:
            dpg.set_value('chem_hover', f'{self.snap.label(self.selected)} x {self.snap.label(cid)}: '
                          + view.tooltip(self.snap, self.drafts, self.selected, cid))
        else:
            values = list(view.partners(self.snap, self.drafts, cid).values())
            dpg.set_value('chem_hover', f'{self.snap.label(cid)} (0x{cid:02X}): {values.count(read.GOOD)} good, '
                          f'{values.count(read.BAD)} bad')

    def _on_click(self, cid):
        if cid is None:
            return
        if self.mode != FOCUS:
            self.back_mode = self.mode
        self.selected = cid
        self.mode = FOCUS
        self.cameras.pop(FOCUS, None)
        self._redraw()

    def _set_mode(self, mode):
        if mode == FOCUS and self.selected is None:
            return
        if mode == FOCUS and self.mode != FOCUS:
            self.back_mode = self.mode
        self.mode = mode
        if mode == FOCUS:
            self.cameras.pop(FOCUS, None)
        self._redraw()

    # ------------------------------------------------------------------ the panel
    def _editable(self):
        return not self.snap.readonly and self.grid.pending.pack is None

    def _rebuild_panel(self):
        if not dpg.does_item_exist('chem_rows'):
            return
        dpg.delete_item('chem_rows', children_only=True)
        self.rows = {}
        cid = self.selected
        dpg.configure_item('chem_bulk', show=cid is not None and self.snap.usable)
        if cid is None or not self.snap.usable:
            dpg.set_value('chem_panel_head', 'Click a character to see and edit its chemistry.')
            dpg.set_value('chem_panel_counts', '')
            return
        family = len(view.variants(self.snap, cid))
        dpg.configure_item('chem_copy_from', items=[self._choice(c.id) for c in self.snap.characters if c.id != cid])
        dpg.set_value('chem_copy_from', COPY_HINT)
        dpg.configure_item('chem_variants', label=f'Apply to variants ({family})' if family else 'Apply to variants')
        mine = view.partners(self.snap, self.drafts, cid)
        marks = view.marks(self.snap, self.drafts)
        values = list(mine.values())
        dpg.set_value('chem_panel_head', f'{self.snap.label(cid)} (0x{cid:02X})')
        dpg.set_value('chem_panel_counts', f'{values.count(read.GOOD)} good, {values.count(read.BAD)} bad'
                      + (f', {values.count(None)} one-way' if None in values else ''))
        needle = (dpg.get_value('chem_search') or '').strip().lower()
        wanted = dpg.get_value('chem_filter')
        hidden = self._hidden()
        editable = self._editable()
        with dpg.table(parent='chem_rows', header_row=False, policy=dpg.mvTable_SizingFixedFit):
            dpg.add_table_column()
            dpg.add_table_column()
            dpg.add_table_column()
            for other, value in mine.items():
                label = self.snap.label(other)
                mark = marks.get(view.key(cid, other))
                if other in hidden or (needle and needle not in label.lower() and needle not in f'0x{other:02x}'):
                    continue
                if ((wanted == 'Good' and value != read.GOOD) or (wanted == 'Bad' and value != read.BAD)
                        or (wanted == 'Neutral' and value != read.NEUTRAL)
                        or (wanted == FILTERS[4] and mark not in (view.DRAFT, view.PENDING))):
                    continue
                with dpg.table_row():
                    name = dpg.add_selectable(label=label, width=150, callback=lambda _s, _a, o: self._on_click(o),
                                              user_data=other)
                    with dpg.tooltip(name):
                        tip = dpg.add_text(view.tooltip(self.snap, self.drafts, cid, other), wrap=380)
                    buttons = {}
                    with dpg.group(horizontal=True, horizontal_spacing=3):
                        for new, text in VALUE_LABELS:
                            buttons[new] = dpg.add_button(label=text, small=True, enabled=editable,
                                                          callback=lambda _s, _a, u: self._on_set(*u),
                                                          user_data=(other, new))
                    dot = dpg.add_text('')
                    self.rows[other] = (buttons, dot, tip)
                    self._show_row(other, value, mark)
        self._panel = (cid, needle, wanted)

    def _show_row(self, other, value, mark):
        buttons, dot, tip = self.rows[other]
        for new, button in buttons.items():
            dpg.bind_item_theme(button, VALUE_THEMES[new] if new == value else 0)
        dpg.set_value(dot, '■' if mark else '')
        dpg.configure_item(dot, color=MARK_COLORS.get(mark, _DIM))
        dpg.set_value(tip, view.tooltip(self.snap, self.drafts, self.selected, other))

    def _choice(self, cid):
        return f'{self.snap.label(cid)} (0x{cid:02X})'

    def _bulk(self, changed, what):
        """After a bulk edit of the selected character: redraw, say what happened."""
        self.app.log_line(f'[chemistry] {what}: {changed} pair{"s" if changed != 1 else ""} changed'
                          + (' (not staged yet: press Stage).' if changed else '.'), _DRAFT if changed else _DIM)
        self._redraw()

    def _on_copy_from(self, label):
        cid = self.selected
        source = next((c.id for c in self.snap.characters if self._choice(c.id) == label), None)
        if cid is None or source is None or not self._editable():
            return
        self._bulk(view.copy_from(self.snap, self.drafts, cid, source),
                   f'{self.snap.label(cid)} takes the chemistry of {self.snap.label(source)}')

    def _on_clear_all(self):
        cid = self.selected
        if cid is None or not self._editable():
            return
        self._bulk(view.clear_all(self.snap, self.drafts, cid), f'{self.snap.label(cid)}: every pair neutral')

    def _on_variants(self):
        cid = self.selected
        if cid is None or not self._editable():
            return
        self._bulk(view.apply_to_variants(self.snap, self.drafts, cid),
                   f'the variants of {self.snap.label(cid)} take its chemistry')

    # ------------------------------------------------------------------ chemistry sets
    def _pick_set(self, save):
        if not self.snap.usable or self.app.busy or self.grid.action is not None or (not save and self._blocked()):
            return
        try:
            os.makedirs(self.sets_dir, exist_ok=True)
        except OSError as exc:               # the dialog then starts elsewhere; the grid must not stay locked
            self.app.log_line(f'[chemistry] could not create {self.sets_dir}: {exc}', _WARN)
        self.grid.action = 'chemistry_file'
        filters = [('Chemistry set', '*' + view.EXTENSION), ('All files', '*.*')]
        if not self.app.pick_files('chem_save_dialog' if save else 'chem_load_dialog',
                                   'Save the chemistry set as' if save else 'Load a chemistry set', filters,
                                   self._on_export if save else self._on_import, on_cancel=self.grid._end_action,
                                   initial_dir=self.sets_dir, save=save, default_ext=view.EXTENSION.lstrip('.'),
                                   remember=SETS_DIR_SETTING):
            self.grid._end_action()

    def _on_export(self, paths):
        self.grid._end_action()
        path = view.save_path(paths[0]) if paths and paths[0] else None
        if not path:
            return
        try:
            written, left_out = view.export_set(path, self.snap, self.drafts)
        except OSError as exc:
            self.app.log_line(f'[chemistry] the set was not saved: {exc}', _WARN)
            self.app.toast.error('The chemistry set was not saved.')
            return
        self.app.log_line(f'[chemistry] set saved to {path}: {written} pairs'
                          + (f'; {left_out} left out (one-way, or a value that is none of bad / neutral / good)'
                             if left_out else ''), _OK)
        self.app.toast.ok(f'Chemistry set saved: {os.path.basename(path)}')

    def _on_import(self, paths):
        self.grid._end_action()
        path = next((p for p in paths or () if p and os.path.isfile(p)), None)
        if path is None:
            self.app.log_line('[chemistry] no chemistry set chosen.', _WARN)
            return
        try:
            changed, skipped = view.import_set(path, self.snap, self.drafts)
        except (ValueError, OSError) as exc:             # apply.EditFileError
            self.app.log_line(f'[chemistry] the set was not loaded: {exc}', _WARN)
            self.app.toast.error('The chemistry set was not loaded: see the console.')
            return
        name = os.path.basename(path)
        self.app.log_line(f'[chemistry] {name}: {changed} pair{"s" if changed != 1 else ""} differ from what is '
                          'shown' + (f'; {skipped} skipped (characters this roster does not have)' if skipped else '')
                          + ('. Press Stage to stage them.' if changed else '.'), _DRAFT if changed else _DIM)
        self.app.toast.ok(f'{name}: {changed} changed pair{"s" if changed != 1 else ""}'
                          + (', press Stage' if changed else ''))
        self._redraw()

    def _on_set(self, other, new):
        cid = self.selected
        if cid is None or not self._editable():
            return
        view.set_draft(self.snap, self.drafts, cid, other, new)
        scroll = dpg.get_y_scroll('chem_rows')
        self._redraw()
        dpg.set_y_scroll('chem_rows', scroll)

    # ------------------------------------------------------------------ staging
    def _blocked(self):
        """Why nothing can be staged now, or None."""
        if self.snap.readonly:
            return '3_Output_Dat/main.dol is missing: run the export first.'
        if self.grid.pending.pack is not None:
            return LOCK_TEXT
        if self.app.busy or self.grid.action is not None:
            return 'A command or dialog is open.'
        return None

    def _on_stage(self):
        if not self.drafts or self._blocked():
            return
        staged_pairs = dict(self.drafts)
        try:
            path = view.write_item(self.staging_dir, staged_pairs)
        except OSError as exc:
            self.app.log_line(f'[chemistry] could not write the staged changes: {exc}', _WARN)
            return
        n = len(staged_pairs)

        def staged():
            self._in_flight.discard(path)
            for pair, value in staged_pairs.items():
                if self.drafts.get(pair) == value:
                    del self.drafts[pair]
            self.app.log_line(f'[chemistry] staged {n} pair{"s" if n != 1 else ""}; "Patch Game" writes '
                              f'{"them" if n != 1 else "it"}.', _PENDING)
            self.refresh()
        self._in_flight.add(path)
        if not self.grid.offer_chemistry(path, on_staged=staged):
            self._in_flight.discard(path)
            try:
                os.remove(path)
            except OSError:
                pass                         # a leftover file in the staging folder harms nothing

    def _on_revert(self):
        if not self.drafts:
            return
        self.drafts.clear()
        self._redraw()

    def _on_discard(self):
        if self.app.busy or self.grid.action is not None or not gui_grid.staged_chemistry_files(self.grid.pending):
            return
        self.grid.pending.discard_stat_items(chemistry=True)
        self.grid._pending_changed()
        self.app.log_line('[chemistry] the pending chemistry edits were discarded.', _DIM)

    def _on_arrange(self):
        if not self.snap.usable:
            return
        self._arrange(fresh=True)
        self._redraw()

    def _on_patch(self):
        self.grid._on_patch_game()

    def _showing(self):
        """The Chemistry tab is the open one."""
        return dpg.get_value('main_tab_bar') in (dpg.get_alias_id('chemistry_tab'), 'chemistry_tab')

    # ------------------------------------------------------------------ every frame
    def tick(self):
        if not dpg.does_item_exist('chemistry_tab'):
            return
        if self._inputs is None or (dpg.get_frame_count() % 30 == 0 and not self.app.busy
                                    and (self._file_keys() != self._files
                                         or self._snapshot_inputs() != self._inputs)):
            self.refresh()
        size = dpg.get_item_state('chem_canvas_box').get('rect_size') if self._showing() else None
        if size and size[0] > 0 and size[1] > 0:
            canvas = (max(int(size[0]) - 16, 50), max(int(size[1]) - 16, 50))
            if canvas != self.canvas:
                self.canvas = canvas
                dpg.configure_item('chem_canvas', width=canvas[0], height=canvas[1])
                for mode in self._fitted:               # still the automatic fit: fit the new size
                    self.cameras.pop(mode, None)
                self._apply_camera()
        self._mouse()
        pending = self.grid.pending
        files = tuple(gui_grid.staged_chemistry_files(pending))
        shown = (self._blocked(), self.app.busy, self.grid.action is not None, len(pending), files,
                 len(self.drafts), self.snap.readonly, self.snap.error, self.snap.source, self.grid._deploying(),
                 self.mode, self.selected, tuple(self.snap.warnings))
        if shown != self._shown:
            self._shown = shown
            self._apply(shown)

    def _apply(self, shown):
        (blocked, busy, acting, count, files, drafts, readonly, error, source, deploy, mode, selected,
         warnings) = shown
        free = not busy and not acting
        dpg.set_value('chem_source', source)
        if error:
            status, color = error, _ERROR
        elif self.grid.pending.pack is not None:
            status, color = LOCK_TEXT, _WARN
        elif files:
            status = (f'{len(files)} pending chemistry edit{"s" if len(files) != 1 else ""}: "Patch Game" writes '
                      f'{"them" if len(files) != 1 else "it"}.')
            if warnings:
                status += ' ' + ' '.join(warnings[:3]) + (f' (+{len(warnings) - 3} more)' if len(warnings) > 3 else '')
            color = _PENDING
        elif drafts:
            status, color = (f'{drafts} changed pair{"s" if drafts != 1 else ""}, not staged yet: press Stage.',
                             _DRAFT)
        else:
            status, color = 'No pending chemistry edits.', _DIM
        dpg.set_value('chem_status', status)
        dpg.configure_item('chem_status', color=color)
        dpg.configure_item('chem_stage', label=f'Stage ({drafts})' if drafts else 'Stage',
                           enabled=blocked is None and drafts > 0)
        dpg.configure_item('chem_revert', enabled=drafts > 0)
        dpg.configure_item('chem_discard', enabled=free and bool(files))
        dpg.configure_item('chem_arrange', enabled=self.snap.usable)
        dpg.configure_item('chem_mode_focus', enabled=selected is not None)
        dpg.bind_item_theme('chem_mode_overview', 'chem_mode_theme' if mode == OVERVIEW else 0)
        dpg.bind_item_theme('chem_mode_focus', 'chem_mode_theme' if mode == FOCUS else 0)
        dpg.bind_item_theme('chem_mode_matrix', 'chem_mode_theme' if mode == MATRIX else 0)
        dpg.configure_item('chem_export', enabled=free and self.snap.usable)
        dpg.configure_item('chem_import', enabled=blocked is None)
        for tag in ('chem_copy_from', 'chem_clear_all', 'chem_variants'):
            dpg.configure_item(tag, enabled=self._editable())
        size = dpg.get_text_size(gui_grid.patch_label(count, True))
        dpg.configure_item('chem_patch', label=gui_grid.patch_label(count, deploy),
                           width=max(PATCH_W, int(size[0]) + 20) if size else PATCH_W, enabled=free and bool(count))
        editable = self._editable()
        for buttons, _dot, _tip in self.rows.values():
            for button in buttons.values():
                dpg.configure_item(button, enabled=editable)
