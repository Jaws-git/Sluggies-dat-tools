"""The GUI's "Custom CPU" tab: one optional custom CPU level, a 5th button on the exhibition CPU level popup
(``CustomCpu/``; its model without Dear PyGui is ``CustomCpu/view.py``).

* **Custom CPU level** is the main switch, on a row of its own under the Patch Game row; while it is off,
  everything below it is greyed out and cannot be edited. **Patch Game** writes what the tab shows into ``3_Output_Dat``
  (``start.py --custom-cpu`` with the tab's settings, or ``--custom-cpu-off``), then copies the files into the
  game directory when "Deploy patched files to game directory" is ticked (the checkbox at the end of the row
  shares the grid's value; the button then reads "Patch Game & copy files"). Nothing is written before that.
  It writes this tab's level only; the grid's pending edits stay pending.
* **Button name**: the caption shown for the button, 1-25 characters, checked as typed.
* A **section list** on the left (a ``*`` marks sections with values that differ from stock level 4) and the
  section's **value table**: Setting | Custom | Stock (level 4) | Reset.
* **Save settings...** / **Load settings...** write and read ``.slgcpu`` files (the Slugout's format); a file
  with more than one level column asks which one to load. **Discard pending edits** goes back to the deployed
  level, **Reset all** to stock level 4.

Value colours: orange differs from the deployed level (Patch Game will change it), blue differs from stock level
4, red a problem (Patch Game is refused; the lines are under the table).
"""

import os

import dearpygui.dearpygui as dpg

import gui_grid
from CustomCpu import menu_files, settings, view

_TEXT = (220, 220, 220, 255)
_DIM = (170, 170, 170, 255)
_WARN = (255, 210, 90, 255)
_OK = (120, 220, 140, 255)
_ERROR = (255, 120, 110, 255)
_EDITED = (255, 170, 70, 255)
_CHANGED = (130, 190, 255, 255)
_OFF = (115, 115, 115, 255)       # the page below the main switch while it is off (the disabled items' grey)
MARK_COLORS = {view.PROBLEM: _ERROR, view.EDITED: _EDITED, view.CHANGED: _CHANGED}
LOCK_TEXT = 'A roster pack load is pending: press "Patch Game" (or Discard it) on the Character grid tab first.'
SECTION_W = 210
VALUE_W = 110
PATCH_W = 170                    # the Patch Game button; widened to its label once text sizes are known
TURN_OFF = ' (turn off)'


class CustomCpuTab:
    def __init__(self, app):
        self.app = app
        self.grid = app.grid_tab
        root = app.root_dir
        self.out_dol = os.path.join(root, '3_Output_Dat', 'main.dol')
        self.in_dol = os.path.join(root, '1_Input', 'main.dol')
        self.settings_dir = os.path.join(root, view.SETTINGS_DIR_REL)
        self.picture_path = os.path.join(root, menu_files.BUTTON_PICTURE_REL)
        self.snap = view.Snapshot()
        self.draft = view.Draft()
        self.touched = False                  # the draft holds edits the user made (a re-read keeps it then)
        self.section = settings.SECTIONS[0]
        self.cells = {}                       # {key: the row's value item}
        self._files = None
        self._shown = None
        self.grid.on_state_read.append(self.refresh)

    # ------------------------------------------------------------------ build
    def build(self):
        with dpg.tab(label='Custom CPU', tag='custom_cpu_tab'):
            dpg.add_text('One optional custom CPU level: a 5th button on the exhibition CPU level popup. Its settings '
                         'act only at that level; levels 1-4 play as they do without it. Every value starts at the '
                         'game\'s own level 4. Nothing is written before "Patch Game".', wrap=1100)
            with dpg.group(horizontal=True):
                dpg.add_button(label=gui_grid.PATCH_LABEL, tag='cc_deploy', width=PATCH_W,
                               callback=lambda: self._on_deploy())
                dpg.bind_item_theme('cc_deploy', 'primary_theme')
                dpg.add_button(label='Discard pending edits', tag='cc_discard', callback=lambda: self._on_discard())
                dpg.add_spacer(width=12)
                dpg.add_button(label='Save settings...', tag='cc_save', callback=lambda: self._pick(save=True))
                dpg.add_button(label='Load settings...', tag='cc_load', callback=lambda: self._pick(save=False))
                dpg.add_spacer(width=12)
                dpg.add_button(label='Reset all to stock level 4', tag='cc_reset_all',
                               callback=lambda: self._on_reset_all())
                dpg.bind_item_theme('cc_reset_all', 'grid_warn_theme')
                dpg.add_spacer(width=12)
                # the grid's checkbox value (source): one setting, ticking either ticks both
                dpg.add_checkbox(label=gui_grid.DEPLOY_LABEL, tag='cc_copy', source='grid_deploy',
                                 callback=lambda _sender, value: self.grid._on_deploy_toggle(value))
            with dpg.tooltip('cc_copy'):
                dpg.add_text('After a successful Patch Game or "Update button picture", copy the game files from '
                             '3_Output_Dat into the game directory (the same setting as on the Character grid '
                             'tab).', wrap=420)
            with dpg.tooltip('cc_deploy'):
                dpg.add_text('Writes the custom CPU level this tab shows into 3_Output_Dat. Edits pending on the '
                             'Character grid and Gameplay tabs are not written by this button.', wrap=420)
            with dpg.tooltip('cc_load'):
                dpg.add_text('Load a .slgcpu settings file (ours or the Slugout\'s). A file with several level '
                             'columns asks which one to load.', wrap=380)
            dpg.add_checkbox(label='Custom CPU level', tag='cc_on', callback=lambda _s, v: self._on_switch(v))
            with dpg.tooltip('cc_on'):
                dpg.add_text('On: the popup gets the 5th button and the settings below. Off: the stock popup and '
                             'CPU code; everything below is greyed out. "Patch Game" writes the change.', wrap=380)
            dpg.add_text('', tag='cc_source', color=_DIM, wrap=1100)
            dpg.add_text('', tag='cc_status', wrap=1100)
            dpg.add_group(tag='cc_detail')                    # everything the main switch greys out
            dpg.push_container_stack('cc_detail')
            with dpg.group(horizontal=True):
                dpg.add_text('Button name')
                dpg.add_input_text(tag='cc_button', width=300, callback=lambda _s, v: self._on_button(v))
                dpg.add_text('', tag='cc_button_note', color=_DIM)
            with dpg.tooltip('cc_button'):
                dpg.add_text(f'The caption shown while the 5th button is highlighted, 1-{menu_files.NAME_MAX} '
                             'characters, the same in every language.', wrap=380)
            with dpg.group(horizontal=True):
                dpg.add_button(label='Update button picture', tag='cc_picture',
                               callback=lambda: self._on_picture())
                dpg.add_text(f'The picture on the 5th button: {menu_files.BUTTON_PICTURE_REL} in the tools folder '
                             '(82 x 82 pixels). Edit the PNG, then press the button.', tag='cc_picture_note',
                             color=_DIM)
            with dpg.tooltip('cc_picture'):
                dpg.add_text(f'Reads {self.picture_path} again and writes it into the deployed custom level\'s '
                             'popup; the settings stay as deployed. Other sizes are scaled to 82 x 82. Without the '
                             'file the button shows the Level 4 picture. "Patch Game" also uses the file.',
                             wrap=420)
            with dpg.group(horizontal=True):
                for mark, label in ((view.EDITED, 'differs from the deployed level'),
                                    (view.CHANGED, 'differs from stock level 4'), (view.PROBLEM, 'problem')):
                    dpg.add_text('■', tag=f'cc_legend_{mark}', color=MARK_COLORS[mark])
                    dpg.add_text(label, tag=f'cc_legend_text_{mark}', color=_DIM)
                    dpg.add_spacer(width=8)
            with dpg.group(horizontal=True):
                with dpg.child_window(tag='cc_sections', width=SECTION_W, height=-70, border=True):
                    for n, section in enumerate(settings.SECTIONS):
                        dpg.add_selectable(label=section, tag=f'cc_sec_{n}', user_data=section,
                                           callback=lambda _s, _a, name: self._on_section(name))
                with dpg.child_window(tag='cc_body', height=-70, border=False):
                    with dpg.group(horizontal=True):
                        dpg.add_text('', tag='cc_section_title', color=_OK)
                        dpg.add_button(label='Reset section', small=True, tag='cc_reset_section',
                                       callback=lambda: self._on_reset_section())
                    dpg.add_group(tag='cc_rows')
            dpg.add_text('', tag='cc_problems', color=_ERROR, wrap=1100)
            dpg.add_text('', tag='cc_warnings', color=_WARN, wrap=1100)
            dpg.pop_container_stack()
        with dpg.theme(tag='cc_off_theme'):                   # bound to cc_detail while the main switch is off
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Text, _OFF)
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark, _OFF)
                for col in (dpg.mvThemeCol_FrameBg, dpg.mvThemeCol_FrameBgHovered, dpg.mvThemeCol_FrameBgActive,
                            dpg.mvThemeCol_Header, dpg.mvThemeCol_HeaderHovered, dpg.mvThemeCol_HeaderActive):
                    dpg.add_theme_color(col, (45, 46, 50, 255))
        for mark, color in MARK_COLORS.items():
            with dpg.theme(tag=f'cc_mark_{mark}'):
                with dpg.theme_component(dpg.mvAll):
                    dpg.add_theme_color(dpg.mvThemeCol_Text, color)
                    if mark == view.PROBLEM:
                        dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (90, 30, 30, 255))
        self.app.add_file_dialog('cc_save_dialog', (settings.EXTENSION,), color=(120, 220, 160, 255),
                                 default_filename='custom_level')
        self.app.add_file_dialog('cc_load_dialog', (settings.EXTENSION, '.*'), color=(120, 220, 160, 255))

    # ------------------------------------------------------------------ reading
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
        """Read the game files again; the draft follows the deployed level unless the user edited it."""
        if not dpg.does_item_exist('custom_cpu_tab'):
            return
        self._files = self._file_keys()
        self.snap = view.snapshot(self.out_dol, self.in_dol)
        if not self.touched or not view.pending(self.snap, self.draft):
            self.draft, self.touched = view.Draft.of(self.snap.deployed), False
        self._redraw()

    def _redraw(self):
        dpg.set_value('cc_on', self.draft.on)
        dpg.set_value('cc_button', self.draft.button)
        self._build_rows()
        self._update()

    def _build_rows(self):
        dpg.delete_item('cc_rows', children_only=True)
        self.cells = {}
        dpg.set_value('cc_section_title', self.section)
        with dpg.table(parent='cc_rows', header_row=True, policy=dpg.mvTable_SizingFixedFit, borders_innerH=True,
                       borders_outerH=True, borders_innerV=True, borders_outerV=True, row_background=True):
            for label in ('Setting', 'Custom', 'Stock (level 4)', '', ''):
                dpg.add_table_column(label=label)
            on = self.draft.on
            for row in view.rows(self.snap, self.draft, self.section):
                with dpg.table_row():
                    s = row.setting
                    label = dpg.add_text(s.label)
                    with dpg.tooltip(label):
                        dpg.add_text(s.tip, wrap=420)
                    with dpg.group(horizontal=True):
                        item = self._build_value(row)
                        if s.unit and not s.choices:
                            dpg.add_text(s.unit, color=_DIM if on else _OFF)
                    dpg.add_text(view.text(s, row.stock), color=_DIM if on else _OFF)
                    dpg.add_button(label='Reset', small=True, user_data=s.key, enabled=on,
                                   callback=lambda _s, _a, key: self._on_reset(key))
                    dpg.add_text(row.note, color=_OFF if not on else _WARN if row.note else _DIM)
                    self.cells[s.key] = item
                    dpg.bind_item_theme(item, f'cc_mark_{row.mark}' if row.mark and on else 0)

    def _build_value(self, row):
        s = row.setting
        free = self.draft.on and not row.locked
        if s.kind == 'switch' or s.boolean:
            return dpg.add_checkbox(default_value=bool(row.value), enabled=free, user_data=s.key,
                                    callback=lambda _s, v, key: self._on_value(key, bool(v), rebuild=True))
        if s.choices:
            return dpg.add_combo(items=list(s.choices), default_value=s.choices[int(row.value)], width=VALUE_W + 20,
                                 enabled=self.draft.on, user_data=s.key, callback=lambda _s, v, key: self._on_value(
                                     key, settings.BY_KEY[key].choices.index(v), rebuild=True))
        if s.integer:
            return dpg.add_input_int(default_value=int(row.value), width=VALUE_W, step=0, min_value=int(s.lo),
                                     max_value=int(s.hi), min_clamped=True, max_clamped=True, enabled=free,
                                     user_data=s.key, callback=lambda _s, v, key: self._on_value(key, int(v)))
        return dpg.add_input_float(default_value=float(row.value), width=VALUE_W, step=0, format='%.3f',
                                   min_value=float(s.lo), max_value=float(s.hi), min_clamped=True, max_clamped=True,
                                   enabled=free, user_data=s.key,
                                   callback=lambda _s, v, key: self._on_value(key, round(float(v), 4)))

    def _update(self):
        """Marks, section stars, status and problem lines (no table rebuild: a typed cell keeps its focus)."""
        for row in view.rows(self.snap, self.draft, self.section):
            item = self.cells.get(row.setting.key)
            if item is not None:
                dpg.bind_item_theme(item, f'cc_mark_{row.mark}' if row.mark and self.draft.on else 0)
        changed = view.changed_sections(self.snap, self.draft)
        for n, section in enumerate(settings.SECTIONS):
            dpg.configure_item(f'cc_sec_{n}', label=section + ('  *' if section in changed else ''))
            dpg.set_value(f'cc_sec_{n}', section == self.section)
        name = menu_files.name_problem(self.draft.button)
        dpg.set_value('cc_button_note', name or f'{len(self.draft.button)} of {menu_files.NAME_MAX} characters')
        dpg.configure_item('cc_button_note', color=_OFF if not self.draft.on else _ERROR if name else _DIM)
        dpg.bind_item_theme('cc_button', f'cc_mark_{view.PROBLEM}' if name and self.draft.on else 0)
        dpg.set_value('cc_problems', '\n'.join(view.problems(self.snap, self.draft)))
        dpg.set_value('cc_warnings', '\n'.join(view.warnings(self.draft)))
        self._shown = None

    # ------------------------------------------------------------------ editing
    def _edited(self, rebuild=False):
        self.touched = True
        if rebuild:
            self._build_rows()
        self._update()

    def _on_switch(self, value):
        self.draft.on = bool(value)
        self._edited(rebuild=True)                 # the rows are built enabled or greyed out

    def _on_button(self, value):
        self.draft.button = value
        self._edited()

    def _on_section(self, name):
        self.section = name
        self._build_rows()
        self._update()

    def _on_value(self, key, value, rebuild=False):
        view.set_value(self.snap, self.draft, key, value)
        # a switch changes what other rows hold without a value of their own; a lock holds rows
        self._edited(rebuild or key in settings.LOCKS)

    def _on_reset(self, key):
        view.reset(self.snap, self.draft, key)
        self._edited(rebuild=True)

    def _on_reset_section(self):
        view.reset_section(self.draft, self.section)
        self._edited(rebuild=True)

    def _on_reset_all(self):
        self.draft.edits, self.draft.name = {}, ''
        self._edited(rebuild=True)

    def _on_discard(self):
        self.draft, self.touched = view.Draft.of(self.snap.deployed), False
        self._redraw()

    # ------------------------------------------------------------------ deploy
    def _blocked(self):
        """Why nothing can be deployed now, or None."""
        if self.snap.readonly:
            return self.snap.source
        if self.snap.error:
            return self.snap.error
        if self.grid.pending.pack is not None:
            return LOCK_TEXT
        if self.app.busy or self.grid.action is not None:
            return 'A command or dialog is open.'
        return None

    def _on_deploy(self):
        if self._blocked() or view.problems(self.snap, self.draft) or not view.pending(self.snap, self.draft):
            return
        try:
            command = view.deploy_command(self.app.root_dir, self.draft)
        except OSError as exc:
            self.app.log_line(f'[custom cpu] could not write the settings to deploy: {exc}', _WARN)
            self.app.toast.error('The custom CPU level was not deployed.')
            return
        turned_off = not self.draft.on

        def done(code, _output):
            if code != 0:
                self.app.log_line('[custom cpu] not deployed: see the log above.', _WARN)
                self.app.toast.error('The custom CPU level was not deployed.')
                self.refresh()
                return
            self.touched = False
            self.refresh()
            self.app.toast.ok('Custom CPU level turned off.' if turned_off else 'Custom CPU level deployed.')
            if self.grid._deploying():
                self.grid._deploy(patched=True)
        if not self.app.run_chain([command], on_done=done):
            self.app.toast.warn('Not deployed: another command is running. Try again when it is done.')

    def _picture_blocked(self):
        """Why the button picture cannot be written now, or None."""
        if self._blocked():
            return self._blocked()
        if self.snap.deployed is None:
            return 'The custom CPU level is not deployed.'
        if not self.draft.on:
            return 'The custom CPU level is switched off on this tab.'
        return None

    def _on_picture(self):
        """Read the button picture again and write it into the deployed level's popup (its settings stay)."""
        if self._picture_blocked():
            return

        def done(code, _output):
            self.refresh()
            if code != 0:
                self.app.log_line('[custom cpu] the button picture was not written: see the log above.', _WARN)
                self.app.toast.error('The button picture was not updated.')
                return
            self.app.toast.ok('Button picture updated.')
            if self.grid._deploying():
                self.grid._deploy(patched=True)
        if not self.app.run_chain([('--custom-cpu-picture',)], on_done=done):
            self.app.toast.warn('Not updated: another command is running. Try again when it is done.')

    # ------------------------------------------------------------------ save / load
    def _pick(self, save):
        if self.app.busy or self.grid.action is not None:
            return
        try:
            os.makedirs(self.settings_dir, exist_ok=True)
        except OSError as exc:
            self.app.log_line(f'[custom cpu] could not create {self.settings_dir}: {exc}', _WARN)
        filters = [('CPU settings', '*' + settings.EXTENSION), ('All files', '*.*')]
        self.app.pick_files('cc_save_dialog' if save else 'cc_load_dialog',
                            'Save the CPU settings as' if save else 'Load CPU settings', filters,
                            self._on_save_paths if save else self._on_load_paths, initial_dir=self.settings_dir,
                            save=save, default_ext=settings.EXTENSION.lstrip('.'), remember=view.DIR_SETTING)

    def _on_save_paths(self, paths):
        path = paths[0] if paths else None
        if not path:
            return
        try:
            path = view.save_file(self.draft, path)
        except OSError as exc:
            self.app.log_line(f'[custom cpu] the settings were not saved: {exc}', _WARN)
            self.app.toast.error('CPU settings were not saved.')
            return
        self.app.log_line(f'[custom cpu] settings saved to {path}', _OK)
        self.app.toast.ok(f'CPU settings saved: {os.path.basename(path)}')

    def _on_load_paths(self, paths):
        path = next((p for p in paths if p and os.path.isfile(p)), None)
        if path is None:
            return
        try:
            levels = view.load_levels(path)
        except settings.SettingsError as exc:
            self._load_failed(exc)
            return
        if len(levels) > 1:
            self._ask_level(path, levels)
        else:
            self._load(path, levels[0] if levels else None)

    def _load_failed(self, exc):
        self.app.log_line(f'[custom cpu] not loaded: {exc}', _WARN)
        self.app.toast.error('The CPU settings file was not loaded.')

    def _ask_level(self, path, levels):
        tag = 'cc_level_dialog'
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)
        vw = dpg.get_viewport_client_width()
        with dpg.window(tag=tag, label='Which level of the file?', modal=True, no_collapse=True,
                        no_saved_settings=True, autosize=True, pos=(max(0, (vw - 520) // 2), 160),
                        on_close=lambda *_: dpg.delete_item(tag)):
            dpg.add_text(f'{os.path.basename(path)} holds settings for levels '
                         f'{", ".join(map(str, levels))}. The custom level takes one of them.', wrap=480)
            dpg.add_text('Level 4 loads exactly. Level 5 or 6 also turns the Slugout\'s defaults of that level '
                         '(its switches, tuned tables and constants) into values here.', color=_DIM, wrap=480)
            dpg.add_spacer(height=6)
            with dpg.group(horizontal=True):
                for level in levels:
                    dpg.add_button(label=f'Level {level}', width=110, height=30, user_data=level,
                                   callback=lambda _s, _a, lvl: (dpg.delete_item(tag), self._load(path, lvl)))
                dpg.add_button(label='Cancel', width=110, height=30, callback=lambda: dpg.delete_item(tag))

    def _load(self, path, level):
        try:
            notes = view.load_file(self.snap, self.draft, path, level)
        except settings.SettingsError as exc:
            self._load_failed(exc)
            return
        for note in notes:
            self.app.log_line(f'[custom cpu] {note}', _DIM)
        self.app.log_line(f'[custom cpu] loaded {os.path.basename(path)}; "Patch Game" writes it.', _EDITED)
        self.touched = True
        self._redraw()

    # ------------------------------------------------------------------ every frame
    def tick(self):
        if not dpg.does_item_exist('custom_cpu_tab'):
            return
        if self._files is None or (dpg.get_frame_count() % 30 == 0 and not self.app.busy
                                   and self._file_keys() != self._files):
            self.refresh()
        pending = view.pending(self.snap, self.draft)
        shown = (self._blocked(), pending, bool(view.problems(self.snap, self.draft)), self.draft.on,
                 bool(self.draft.edits), self.touched, self.snap.source, self.app.busy, self.grid._deploying())
        if shown != self._shown:
            self._shown = shown
            self._apply(shown)

    def _apply(self, shown):
        blocked, pending, bad, on, edits, _touched, source, busy, copying = shown
        dpg.set_value('cc_source', source)
        if blocked and not busy:
            status, color = blocked, _WARN
        elif bad:
            status, color = 'Patch Game is refused until the problems below are fixed.', _ERROR
        else:
            status, color = view.pending_text(self.snap, self.draft), _EDITED if pending else _DIM
        dpg.set_value('cc_status', status)
        dpg.configure_item('cc_status', color=color)
        label = (gui_grid.PATCH_DEPLOY_LABEL if copying else gui_grid.PATCH_LABEL) + (
            '' if on or not pending else TURN_OFF)
        size = dpg.get_text_size(label)              # None before the first frame
        dpg.configure_item('cc_deploy', enabled=blocked is None and pending and not bad, label=label,
                           width=max(PATCH_W, int(size[0]) + 20) if size else PATCH_W)
        dpg.configure_item('cc_picture', enabled=self._picture_blocked() is None)
        # the main switch off: everything below it is greyed out and cannot be edited
        dpg.bind_item_theme('cc_detail', 0 if on else 'cc_off_theme')
        dpg.configure_item('cc_button', enabled=on)
        dpg.configure_item('cc_picture_note', color=_DIM if on else _OFF)
        dpg.configure_item('cc_section_title', color=_OK if on else _OFF)
        for n in range(len(settings.SECTIONS)):
            dpg.configure_item(f'cc_sec_{n}', enabled=on)
        for mark, color in MARK_COLORS.items():
            dpg.configure_item(f'cc_legend_{mark}', color=color if on else _OFF)
            dpg.configure_item(f'cc_legend_text_{mark}', color=_DIM if on else _OFF)
        for tag, color in (('cc_problems', _ERROR), ('cc_warnings', _WARN)):
            dpg.configure_item(tag, color=color if on else _OFF)
        dpg.configure_item('cc_discard', enabled=pending and not busy)
        dpg.configure_item('cc_reset_all', enabled=edits)
        dpg.configure_item('cc_reset_section',
                           enabled=on and self.section in view.changed_sections(self.snap, self.draft))
        dpg.configure_item('cc_save', enabled=not busy)
        dpg.configure_item('cc_load', enabled=not busy)
