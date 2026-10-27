"""The GUI's "Gameplay" tab: CPU behaviour, star boosts and item odds in ``main.dol`` (``Gameplay/``).

Three inner tabs (CPU Behaviour, Star Boosts, Item Odds) of value tables, filled from the game files: the values
``3_Output_Dat/main.dol`` holds, with the pending gameplay edits on top; ``1_Input/main.dol`` is "vanilla"
(``Gameplay/view.py``). Without an output game the tab shows the 1_Input values read-only. The "CPU vs CPU" and
"Extras" inner tabs (game option toggles) are built by the grid (``CharacterGridTab.build_cpu_vs_cpu`` /
``build_extras``).

* **Typing** a value makes it a draft (yellow); **Stage** (per inner tab) writes the inner tab's drafts into an edit
  file in ``_gui/gameplay`` and stages it like a grid edit (``CharacterGridTab.offer_gameplay``: the staging check,
  a dialog only for warnings or a refusal). Pending values are orange, values that differ from 1_Input blue,
  a failed cross-field check red with its line under the table. The tooltip shows the layers.
* **Reset section** stages a section's 1_Input values; **Reset gameplay settings** stages a ``reset`` of every table
  (star boosts included, also those the Stat Editor set); **Discard pending gameplay edits** drops them.
* **Save settings...** writes what the game holds (``--save-gameplay``; pending edits are not in the game yet, so it
  asks first); **Load settings...** checks a file (``--apply-gameplay FILE --dry-run --report``; our settings files
  and the AI Gameplay Tool's ``cpu_behavior.json``), shows what differs per inner tab, and stages a copy of it in
  place of the pending gameplay edits.
* **Patch Game (N)** runs the grid's Patch Game: every pending edit, grid and gameplay, in one chain. The
  "Deploy patched files to game directory" checkbox at the end of the row shares the grid's value (``source``).

While a roster pack load is pending the tab is locked, as the sound tabs are.
"""

import os

import dearpygui.dearpygui as dpg

import gui_grid
from Gameplay import tables, view
from StatEditor import fields

_TEXT = (220, 220, 220, 255)
_DIM = (170, 170, 170, 255)
_WARN = (255, 210, 90, 255)
_OK = (120, 220, 140, 255)
_ERROR = (255, 120, 110, 255)
_PENDING = (255, 170, 70, 255)
_DRAFT = (255, 230, 120, 255)
_CHANGED = (130, 190, 255, 255)
MARK_COLORS = {view.PROBLEM: _ERROR, view.DRAFT: _DRAFT, view.PENDING: _PENDING, view.CHANGED: _CHANGED}
LOCK_TEXT = 'A roster pack load is pending: press "Patch Game" (or Discard it) first.'
GECKO_NOTE = ('Gecko codes win: if the codes are still active in Dolphin, they overwrite these values. Turn them off now.')
CELL_W = {'u8': 64, 's8': 64, 's16': 76, 'f32': 84}
CHOICE_W = 110
PATCH_W = 170


class GameplayTab:
    def __init__(self, app):
        self.app = app
        self.grid = app.grid_tab
        root = app.root_dir
        self.out_dol = os.path.join(root, '3_Output_Dat', 'main.dol')
        self.in_dol = os.path.join(root, '1_Input', 'main.dol')
        self.staging_dir = os.path.join(root, gui_grid.GAMEPLAY_STAGING_REL)
        self.settings_dir = os.path.join(root, gui_grid.GAMEPLAY_DIR_REL)
        self.report_path = os.path.join(root, gui_grid.GAMEPLAY_REPORT_REL)
        self.snap = view.Snapshot()
        self.drafts = {}                      # {field: value} typed, not staged
        self.cells = {}                       # {field: (input item, tooltip text item)}
        self.totals = {}                      # {item row key: text item}
        self.section_problems = {}            # {section key: text item}
        self._items = None                    # the pending items the snapshot was taken with
        self._files = None                    # the DOLs' (size, mtime) the snapshot was taken with
        self._shown = None                    # the button state last applied
        self._in_flight = set()               # staged files whose staging check runs (never pruned)
        self.grid.on_state_read.append(self.refresh)
        self.grid.on_pending_changed.append(self.refresh)

    # ------------------------------------------------------------------ build
    def build(self):
        with dpg.tab(label='Gameplay', tag='gameplay_tab'):
            dpg.add_text('CPU behaviour, star boosts and item odds, written into main.dol (no Gecko codes). Typed '
                         'values are staged per inner tab; "Patch Game" writes every pending edit.', wrap=1100)
            with dpg.group(horizontal=True):
                dpg.add_button(label='Patch Game', tag='gp_patch', width=PATCH_W, callback=lambda: self._on_patch())
                dpg.bind_item_theme('gp_patch', 'primary_theme')
                dpg.add_spacer(width=12)
                dpg.add_button(label='Reset gameplay settings', tag='gp_reset_all', callback=lambda: self._on_reset_all())
                dpg.bind_item_theme('gp_reset_all', 'grid_warn_theme')
                dpg.add_button(label='Discard pending gameplay edits', tag='gp_discard',
                               callback=lambda: self._on_discard())
                dpg.add_spacer(width=12)
                dpg.add_button(label='Save settings...', tag='gp_save', callback=lambda: self._on_save())
                dpg.add_button(label='Load settings...', tag='gp_load', callback=lambda: self._on_load())
                dpg.add_spacer(width=12)
                # the grid's checkbox value (source): one setting, ticking either ticks both
                dpg.add_checkbox(label=gui_grid.DEPLOY_LABEL, tag='gp_deploy', source='grid_deploy',
                                 callback=lambda _sender, value: self.grid._on_deploy_toggle(value))
            with dpg.tooltip('gp_deploy'):
                dpg.add_text('After a successful Patch Game, copy the game files from 3_Output_Dat into the game '
                             'directory (the same setting as on the Character grid tab).', wrap=420)
            with dpg.tooltip('gp_save'):
                dpg.add_text('Save the gameplay settings the game holds into a .sluggiesgameplay file.', wrap=360)
            with dpg.tooltip('gp_load'):
                dpg.add_text('Load a .sluggiesgameplay file, or a cpu_behavior.json of the AI Gameplay Tool. Shows '
                             'what differs first; Stage puts it in place of the pending gameplay edits.', wrap=360)
            with dpg.tooltip('gp_reset_all'):
                dpg.add_text('Stage every gameplay value back to 1_Input/main.dol, star boosts included.', wrap=360)
            dpg.add_text('', tag='gp_source', color=_DIM, wrap=1100)
            dpg.add_text('', tag='gp_status', wrap=1100)
            dpg.add_text(GECKO_NOTE, color=_WARN, wrap=1100)
            with dpg.group(horizontal=True):
                for mark, label in ((view.DRAFT, 'edited, not staged'), (view.PENDING, 'pending'),
                                    (view.CHANGED, 'differs from 1_Input'), (view.PROBLEM, 'problem')):
                    dpg.add_text('■', color=MARK_COLORS[mark])
                    dpg.add_text(label, color=_DIM)
                    dpg.add_spacer(width=8)
            with dpg.child_window(tag='gp_body', border=False, height=-36):
                with dpg.tab_bar(tag='gp_tabs'):
                    self.grid.build_cpu_vs_cpu()
                    for tab, title in tables.TABS:
                        with dpg.tab(label=title, tag=f'gp_tab_{tab}'):
                            with dpg.group(horizontal=True):
                                dpg.add_button(label='Stage', tag=f'gp_stage_{tab}', width=110,
                                               callback=lambda _s, _a, t: self._on_stage(t), user_data=tab)
                                dpg.bind_item_theme(f'gp_stage_{tab}', 'primary_theme')
                                dpg.add_button(label='Revert typed values', tag=f'gp_revert_{tab}',
                                               callback=lambda _s, _a, t: self._on_revert(t), user_data=tab)
                            for section, section_tab, title_ in tables.SECTIONS:
                                if section_tab == tab:
                                    self._build_section(section, title_)
                    self.grid.build_extras()
        self._build_themes()
        self.app.add_file_dialog('gp_save_dialog', (gui_grid.GAMEPLAY_EXTENSION,), color=(120, 220, 160, 255),
                                 default_filename='gameplay')
        self.app.add_file_dialog('gp_load_dialog', (gui_grid.GAMEPLAY_EXTENSION, '.json', '.*'),
                                 color=(120, 220, 160, 255))

    def _build_themes(self):
        for mark, color in MARK_COLORS.items():
            with dpg.theme(tag=f'gp_mark_{mark}'):
                with dpg.theme_component(dpg.mvAll):
                    dpg.add_theme_color(dpg.mvThemeCol_Text, color)
                    if mark == view.PROBLEM:
                        dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (90, 30, 30, 255))

    def _build_section(self, section, title):
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True):
            dpg.add_text(title, color=_OK)
            dpg.add_button(label='Reset section', small=True, tag=f'gp_reset_{section}',
                           callback=lambda _s, _a, s: self._on_reset_section(s), user_data=section)
            with dpg.tooltip(f'gp_reset_{section}'):
                dpg.add_text(f'Stage the 1_Input values of "{title}".', wrap=320)
        for t in tables.TABLES:
            if t.section == section:
                self._build_table(t)
        self.section_problems[section] = dpg.add_text('', color=_ERROR, wrap=1000)
        dpg.add_separator()

    def _build_table(self, t):
        if t.title.lower() != dict((s, x) for s, _tab, x in tables.SECTIONS).get(t.section, '').lower():
            dpg.add_text(t.title, color=_TEXT)
        if t.note:
            dpg.add_text(t.note, color=_DIM, wrap=1000)
        with dpg.table(header_row=True, policy=dpg.mvTable_SizingFixedFit, borders_innerH=True,
                       borders_outerH=True, borders_innerV=True, borders_outerV=True, row_background=True):
            dpg.add_table_column(label='')
            for label in t.column_labels:
                dpg.add_table_column(label=label)
            if t.key == 'item_odds':
                dpg.add_table_column(label='Total')
            for row, row_label in zip(t.rows, t.row_labels):
                with dpg.table_row():
                    dpg.add_text(row_label)
                    for column in t.columns:
                        self._build_cell(tables.FIELD_LOOKUP[(t.key, row, column)])
                    if t.key == 'item_odds':
                        self.totals[row] = dpg.add_text('')

    def _build_cell(self, f):
        if f.choices is not None:
            item = dpg.add_combo(items=self._choice_labels(f), width=CHOICE_W, callback=self._on_combo, user_data=f)
        elif f.kind == 'f32':
            item = dpg.add_input_float(width=CELL_W['f32'], step=0, format='%.3f', callback=self._on_value,
                                       user_data=f)
        else:
            _fmt, lo, hi = fields.KINDS[f.kind]
            item = dpg.add_input_int(width=CELL_W.get(f.kind, 76), step=0, min_value=lo, max_value=hi,
                                     min_clamped=True, max_clamped=True, callback=self._on_value, user_data=f)
        with dpg.tooltip(item):
            tip = dpg.add_text('', wrap=420)
        self.cells[f] = (item, tip)

    @staticmethod
    def _choice_labels(f):
        if f.table == 'star_boost':
            return [tables.STAR_BOOST_OPS[v] for v in f.choices]
        return [tables.PITCH_TYPES[v] for v in f.choices]

    def _choice_text(self, f, value):
        labels = self._choice_labels(f)
        return labels[f.choices.index(value)] if value in f.choices else str(value)

    def _text(self, f, value):
        return self._choice_text(f, value) if f.choices is not None else str(fields.short(value))

    # ------------------------------------------------------------------ reading
    def _pending_items(self):
        return tuple(gui_grid.staged_gameplay_items(self.grid.pending))

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
        if not dpg.does_item_exist('gameplay_tab'):
            return
        self._items = self._pending_items()
        self._files = self._file_keys()
        self.snap = view.snapshot(self.out_dol, self.in_dol, list(self._items))
        view.prune_drafts(self.snap, self.drafts)
        view.prune_staging(self.staging_dir, [i for i in self._items if i != gui_grid.GAMEPLAY_RESET]
                           + list(self._in_flight))
        self._redraw()
        self._shown = None

    def _redraw(self):
        snap = self.snap
        found = view.problems(snap, self.drafts) if snap.usable else []
        marks = view.marks(snap, self.drafts, found) if snap.usable else {}
        editable = not snap.readonly and self.grid.pending.pack is None
        for f, (item, tip) in self.cells.items():
            if f not in snap.current:
                dpg.configure_item(item, enabled=False)
                dpg.set_value(tip, 'Not in this main.dol.')
                continue
            value = self.drafts.get(f, view.base(snap, f))
            dpg.set_value(item, self._choice_text(f, value) if f.choices is not None else value)
            dpg.configure_item(item, enabled=editable)
            mark = marks.get(f)
            dpg.bind_item_theme(item, f'gp_mark_{mark}' if mark else 0)
            dpg.set_value(tip, view.tooltip(snap, self.drafts, f, lambda v, f=f: self._text(f, v)))
        self._redraw_checks(found)

    def _redraw_checks(self, found):
        shown = view.shown(self.snap, self.drafts)
        items = tables.TABLES_BY_KEY['item_odds']
        for row in items.rows:
            values = [shown.get(tables.FIELD_LOOKUP[('item_odds', row, c)]) for c in items.columns]
            if row in self.totals:
                total = sum(v for v in values if v is not None)
                dpg.set_value(self.totals[row], str(total) if None not in values else '?')
                dpg.configure_item(self.totals[row], color=_ERROR if total > tables.ROLL_LIMIT else _DIM)
        for section, item in self.section_problems.items():
            own = set(tables.section_fields(section))
            lines = [p.message for p in found if own & set(p.fields)]
            dpg.set_value(item, '\n'.join(lines))

    def _update_marks(self):
        """After typing: marks, tooltips and checks only (the typed cell keeps its text)."""
        snap = self.snap
        found = view.problems(snap, self.drafts)
        marks = view.marks(snap, self.drafts, found)
        for f, (item, tip) in self.cells.items():
            if f in snap.current:
                mark = marks.get(f)
                dpg.bind_item_theme(item, f'gp_mark_{mark}' if mark else 0)
                dpg.set_value(tip, view.tooltip(snap, self.drafts, f, lambda v, f=f: self._text(f, v)))
        self._redraw_checks(found)
        self._shown = None

    # ------------------------------------------------------------------ typing
    def _on_value(self, _sender, value, f):
        if self.snap.readonly or f not in self.snap.current:
            return
        view.set_draft(self.snap, self.drafts, f, value)
        self._update_marks()

    def _on_combo(self, _sender, label, f):
        labels = self._choice_labels(f)
        if label in labels:
            self._on_value(None, f.choices[labels.index(label)], f)

    def _on_revert(self, tab):
        own = set(tables.tab_fields(tab))
        for f in [f for f in self.drafts if f in own]:
            del self.drafts[f]
        self._redraw()
        self._shown = None

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

    def _stage_values(self, values, what, on_staged=None):
        if not values or self._blocked():
            return
        try:
            path = view.write_edits(self.staging_dir, values)
        except OSError as exc:
            self.app.log_line(f'[gameplay] could not write the staged values: {exc}', _WARN)
            return

        def staged():
            self._in_flight.discard(path)
            self.app.log_line(f'[gameplay] staged {what}; "Patch Game" writes it.', _PENDING)
            if on_staged is not None:
                on_staged()
        self._in_flight.add(path)
        if not self.grid.offer_gameplay(gui_grid.gameplay_edit(path), on_staged=staged):
            self._in_flight.discard(path)
            os.remove(path)

    def _on_stage(self, tab):
        own = set(tables.tab_fields(tab))
        values = {f: v for f, v in self.drafts.items() if f in own}
        n = len(values)

        def done():
            for f in values:
                self.drafts.pop(f, None)
        self._stage_values(values, f'{n} typed value{"s" if n != 1 else ""} ({dict(tables.TABS)[tab]})', done)

    def _on_reset_section(self, section):
        values = view.section_values(self.snap, section)
        title = next(t for s, _tab, t in tables.SECTIONS if s == section)
        if not values:
            self.app.log_line(f'[gameplay] {title}: the game (with the pending edits) holds the 1_Input values '
                              'already.', _DIM)
            return
        own = set(tables.section_fields(section))

        def done():
            for f in [f for f in self.drafts if f in own]:
                del self.drafts[f]
        self._stage_values(values, f'{title} back to 1_Input', done)

    def _on_reset_all(self):
        if self._blocked() or self.grid.confirm is not None:
            return
        n = view.changed_count(self.snap)
        lines = [('Stages every gameplay value back to 1_Input/main.dol: CPU behaviour, star boosts and item odds.',
                  gui_grid.TEXT),
                 ('This includes the star boosts the Stat Editor may have set (both tools write the same values).',
                  gui_grid.WARN),
                 (f'{n} value{"s" if n != 1 else ""} differ{"s" if n == 1 else ""} from 1_Input now (pending edits '
                  'included).', gui_grid.TEXT)]
        if self._pending_items():
            lines.append(('The pending gameplay edits are dropped by it.', gui_grid.TEXT))

        def ok():
            self.grid._end_action()
            self.drafts.clear()
            self.grid.offer_gameplay(gui_grid.gameplay_edit(gui_grid.GAMEPLAY_RESET),
                                     on_staged=lambda: self.app.log_line(
                                         '[gameplay] staged: every gameplay value back to 1_Input.', _PENDING))
        self.grid.action = 'ask'
        self.grid._dialog(gui_grid.SlotDialog('Reset the gameplay settings?', lines, True), ok, ok_label='Stage reset')

    def _on_discard(self):
        if self.app.busy or self.grid.action is not None or not self._pending_items():
            return
        self.grid.pending.discard_op(gui_grid.GAMEPLAY)
        self.grid._pending_changed()
        self.app.log_line('[gameplay] the pending gameplay edits were discarded.', _DIM)

    def _on_patch(self):
        self.grid._on_patch_game()

    # ------------------------------------------------------------------ save / load
    def _on_save(self):
        if self.snap.readonly or self.app.busy or self.grid.action is not None:
            return
        items = self._pending_items()
        if items or self.drafts:
            lines = []
            if items:
                lines.append((f'{len(items)} pending gameplay edit{"s" if len(items) != 1 else ""} (not written to '
                              'the game yet).', gui_grid.WARN))
            if self.drafts:
                lines.append((f'{len(self.drafts)} typed value{"s" if len(self.drafts) != 1 else ""} not staged.',
                              gui_grid.WARN))
            lines.append(('Save settings writes the values the game holds, so they are not in the file. To include '
                          'them, Cancel, stage them and run "Patch Game" first.', gui_grid.TEXT))
            self.grid.action = 'ask'
            self.grid._dialog(gui_grid.SlotDialog('Save the gameplay settings without them?', lines, True),
                              lambda: (self.grid._end_action(), self._pick(save=True)), ok_label='Save without them')
            return
        self._pick(save=True)

    def _on_load(self):
        if self._blocked() or self.grid.confirm is not None:
            return
        self._pick(save=False)

    def _pick(self, save):
        self.grid.action = 'gameplay_file'
        os.makedirs(self.settings_dir, exist_ok=True)
        if save:
            filters = [('Gameplay settings', '*' + gui_grid.GAMEPLAY_EXTENSION), ('All files', '*.*')]
        else:
            filters = [('Gameplay settings (.sluggiesgameplay, AI Gameplay Tool .json)',
                        f'*{gui_grid.GAMEPLAY_EXTENSION};*.json'), ('All files', '*.*')]
        on_paths = self._on_save_paths if save else self._on_load_paths
        if not self.app.pick_files('gp_save_dialog' if save else 'gp_load_dialog',
                                   'Save the gameplay settings as' if save else 'Load gameplay settings', filters,
                                   on_paths, on_cancel=self.grid._end_action, initial_dir=self.settings_dir, save=save,
                                   default_ext=gui_grid.GAMEPLAY_EXTENSION.lstrip('.'),
                                   remember=gui_grid.GAMEPLAY_DIR_SETTING):
            self.grid._end_action()

    def _on_save_paths(self, paths):
        path = paths[0] if paths else None
        self.grid._end_action()
        if not path:
            return
        path = gui_grid.gameplay_save_path(path)

        def done(code, _output):
            if code == 0:
                self.app.log_line(f'[gameplay] settings saved to {path}', _OK)
                self.app.toast.ok(f'Gameplay settings saved: {os.path.basename(path)}')
            else:
                self.app.log_line('[gameplay] the settings were not saved: see the log above.', _WARN)
                self.app.toast.error('Gameplay settings were not saved.')
        self.app.run_chain([gui_grid.gameplay_save_command(path)], on_done=done)

    def _on_load_paths(self, paths):
        path = next((p for p in paths if p and os.path.isfile(p)), None)
        self.grid._end_action()
        if path is None:
            self.app.log_line(f'[gameplay] no settings file chosen: {(paths or [None])[0] or "(none)"}', _WARN)
            return
        try:
            os.remove(self.report_path)
        except OSError:
            pass
        os.makedirs(os.path.dirname(self.report_path), exist_ok=True)
        self.grid.action = 'gameplay_file'
        if not self.app.run_chain([gui_grid.gameplay_report_command(path, self.report_path)],
                                  on_done=lambda code, _o: self._show_load(path, code)):
            self.grid._end_action()

    def _show_load(self, path, code):
        self.grid._end_action()
        report = gui_grid.load_gameplay_report(self.report_path)
        if code != 0 and (report is None or not report.get('error')):
            report = None
        labels = view.cell_labels()
        pending = len(self._pending_items())
        name = os.path.basename(path)

        def stage():
            self.grid._end_action()
            try:
                copy = view.copy_for_staging(self.staging_dir, path)
            except OSError as exc:
                self.app.log_line(f'[gameplay] could not copy {path}: {exc}', _WARN)
                return
            def staged():
                self._in_flight.discard(copy)
                self.app.log_line(f'[gameplay] staged the settings of {name}.', _PENDING)
            self._in_flight.add(copy)
            if not self.grid.offer_gameplay(gui_grid.gameplay_edit(copy), replace=True, on_staged=staged):
                self._in_flight.discard(copy)
                os.remove(copy)
        dialog = gui_grid.gameplay_load_dialog(report, name, labels, pending, True, view.LEGACY_TITLE)
        self.grid.action = 'ask'
        self.grid._dialog(dialog, stage, ok_label='Stage',
                          toggle='Only differing values' if report and not report.get('error') else None,
                          rebuild=lambda only: gui_grid.gameplay_load_dialog(report, name, labels, pending, only,
                                                                            view.LEGACY_TITLE))

    # ------------------------------------------------------------------ every frame
    def tick(self):
        if not dpg.does_item_exist('gameplay_tab'):
            return
        if self._items is None or (dpg.get_frame_count() % 30 == 0 and not self.app.busy
                                   and (self._file_keys() != self._files or self._pending_items() != self._items)):
            self.refresh()
        pending = self.grid.pending
        per_tab = tuple(sum(1 for f in self.drafts if tables.TABLES_BY_KEY[f.table].tab == tab)
                        for tab, _t in tables.TABS)
        shown = (self._blocked(), self.app.busy, self.grid.action is not None, len(pending), self._pending_items(),
                 per_tab, self.snap.readonly, self.snap.error, self.snap.source, self.grid._deploying())
        if shown != self._shown:
            self._shown = shown
            self._apply(shown, per_tab)

    def _apply(self, shown, per_tab):
        blocked, busy, acting, count, items, _per_tab, readonly, error, source, deploy = shown
        free = not busy and not acting
        dpg.set_value('gp_source', source)
        if error:
            status, color = error, _ERROR
        elif self.grid.pending.pack is not None:
            status, color = LOCK_TEXT, _WARN
        elif items:
            labels = [gui_grid.gameplay_item_label(i) for i in items]
            status = (f'{len(items)} pending gameplay edit{"s" if len(items) != 1 else ""} ('
                      + '; '.join(labels) + '): "Patch Game" writes them.')
            color = _PENDING
        else:
            status, color = 'No pending gameplay edits.', _DIM
        dpg.set_value('gp_status', status)
        dpg.configure_item('gp_status', color=color)
        stageable = blocked is None
        for (tab, _title), n in zip(tables.TABS, per_tab):
            dpg.configure_item(f'gp_stage_{tab}', label=f'Stage ({n})' if n else 'Stage', enabled=stageable and n > 0)
            dpg.configure_item(f'gp_revert_{tab}', enabled=n > 0)
        for section, _tab, _title in tables.SECTIONS:
            dpg.configure_item(f'gp_reset_{section}', enabled=stageable)
        dpg.configure_item('gp_reset_all', enabled=stageable)
        dpg.configure_item('gp_load', enabled=stageable)
        dpg.configure_item('gp_save', enabled=free and not readonly)
        dpg.configure_item('gp_discard', enabled=free and bool(items))
        # one width for both labels (the longer one's), so the row does not shift when Deploy is ticked
        size = dpg.get_text_size(gui_grid.patch_label(count, True))
        dpg.configure_item('gp_patch', label=gui_grid.patch_label(count, deploy),
                           width=max(PATCH_W, int(size[0]) + 20) if size else PATCH_W, enabled=free and bool(count))
        editable = not readonly and self.grid.pending.pack is None
        for f, (item, _tip) in self.cells.items():
            if f in self.snap.current:
                dpg.configure_item(item, enabled=editable)
