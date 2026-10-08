"""The GUI's "Stat Editor" tab: Philenarion's Sluggers Stat Editor, launched from Sluggies Tools.

* **Path:** the folder that holds the editor (``editor_install.find_install``: a folder of
  ``sluggers-stat-editor-v*`` releases, one release, or a source folder with ``editor.py``), remembered in the GUI
  settings. Without one the tab links to the editor's website.
* **Check for updates** asks GitHub for the latest release (worker thread) and offers its page; nothing is
  downloaded or installed.
* **Open in Bridge Mode** writes ``Bridge/stat_bridge.json`` (``start.py --stat-bridge-export``, in the log) and
  starts the editor as a child process; **Open Standalone** removes a left bridge first, so the editor runs as it
  does on its own (its Gecko codes target vanilla addresses). One editor at a time: both buttons are disabled while
  it runs.
* **Receiving:** when the editor exits, a ``Bridge/stat_edits.json`` (Send to Sluggies) is moved into
  ``3_Output_Dat/_gui/stat`` and offered for staging (``CharacterGridTab.offer_stat_edits``: the staging check, then
  the confirm dialog); the bridge files are deleted either way. Staged values are pending edits like the grid's:
  Patch Game writes them.
* **Leftovers** (a crash on either side): at start-up a left ``stat_bridge.json`` is deleted and a left
  ``stat_edits.json`` is offered; one that no longer fits the game is dropped with a log line.

``tick`` runs every frame: it offers received files once the grid tab is free, prunes received files no pending
edit uses any more, and keeps the buttons' state current.
"""

import os
import subprocess
import threading
import webbrowser

import dearpygui.dearpygui as dpg

import gui_grid
from StatEditor import editor_install as ei

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_TEXT = (220, 220, 220, 255)
_DIM = (170, 170, 170, 255)
_WARN = (255, 210, 90, 255)
_OK = (120, 220, 140, 255)
_ERROR = (255, 120, 110, 255)
_PENDING = (255, 170, 70, 255)
_LINK = (110, 170, 255, 255)
BRIDGE, STANDALONE = 'Bridge Mode', 'Standalone'
_CLOSE_DIALOG = 'stat_editor_close_dialog'
_LOG_TAIL = 8                                # lines of the editor's output shown after a failed run


class StatEditorTab:
    def __init__(self, app):
        self.app = app                       # gui.SluggiesGui
        self.settings = app.settings         # gui_settings.Settings, shared with the other tabs
        self.staging_dir = os.path.join(app.root_dir, ei.STAGING_REL)
        self.bridge_out = os.path.join(app.root_dir, '3_Output_Dat', 'main.dol')
        self.install = ei.find_install(self.settings.get(ei.SETTING))
        self.process = None                  # the running editor
        self.mode = None                     # BRIDGE / STANDALONE while it runs
        self.launched = None                 # the Install it was started from (the path setting may change)
        self.log_file = None
        self.received = []                   # [(path, quiet)] waiting to be offered
        self.offering = None                 # the path whose check / dialog is up
        self.exporting = False               # the bridge export runs
        self.latest = None                   # editor_install.Release after an update check
        self.checking = False
        self._pruned = None                  # the kept set the last prune ran with
        self._shown = None                   # the button state last applied
        self._started = False                # the start-up cleanup ran (first frame: the log pane exists then)

    # ------------------------------------------------------------------ build
    def build(self):
        with dpg.tab(label='Stat Editor', tag='stat_tab'):
            dpg.add_text("Philenarion's Sluggers Stat Editor: stats, chemistry, sizes, pitching, star and handicap "
                         'values.', wrap=900)
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_text('Stat Editor folder:')
                dpg.add_input_text(tag='stat_path', width=620, default_value=self.settings.get(ei.SETTING) or '',
                                   hint='folder with sluggers-stat-editor-v*, a release folder, or a source folder',
                                   callback=lambda _s, text: self._on_path(text))
                dpg.add_button(label='Browse...', tag='stat_browse', callback=lambda: self._on_browse())
            dpg.add_text('', tag='stat_found', wrap=900)
            with dpg.group(horizontal=True, tag='stat_site_row'):
                dpg.add_text('Get the Stat Editor:', color=_DIM)
                dpg.add_button(label=ei.WEBSITE, tag='stat_site', small=True,
                               callback=lambda: self._open_url(ei.WEBSITE))
                dpg.bind_item_theme('stat_site', self._link_theme())
            with dpg.group(horizontal=True, tag='stat_update_row'):
                dpg.add_button(label='Check for updates', tag='stat_update', callback=lambda: self._on_check())
                dpg.add_text('', tag='stat_update_text')
                dpg.add_button(label='Open the download page', tag='stat_download', show=False,
                               callback=lambda: self._open_url(self.latest.page if self.latest else ei.RELEASES_PAGE))
            with dpg.file_dialog(directory_selector=False, show=False, modal=True, tag='stat_path_dialog', width=760,
                                 height=460, callback=self._on_browse_chosen, default_path=self.app.root_dir):
                dpg.add_file_extension('Stat Editor{.exe,.py}', color=(120, 220, 120, 255))
                dpg.add_file_extension('.*')
            dpg.add_separator()
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_button(label='Open in Bridge Mode', tag='stat_open_bridge', width=190, height=44,
                               callback=lambda: self._on_open_bridge())
                dpg.bind_item_theme('stat_open_bridge', 'primary_theme')
                dpg.add_button(label='Open Standalone', tag='stat_open_standalone', height=44,
                               callback=lambda: self._on_open_standalone())
            dpg.add_text('', tag='stat_open_reason', color=_WARN, wrap=900)
            dpg.add_text('Bridge Mode: edit player stats at any roster size in Stat Editor. Stage changes in Sluggies Tools.', wrap=900, color=_DIM)
            dpg.add_text('Standalone: Assumes vanilla roster. Gecko codes only.', wrap=900, color=_DIM)
            dpg.add_text('', tag='stat_running', color=_OK)
            dpg.add_separator()
            dpg.add_text('', tag='stat_pending_head')
            dpg.add_group(tag='stat_pending_list')
            dpg.add_button(label='Discard stat edits', tag='stat_discard', callback=lambda: self._on_discard())
            dpg.bind_item_theme('stat_discard', 'grid_warn_theme')
        self._show_found()

    @staticmethod
    def _link_theme():
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (60, 70, 90, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, _LINK)
        return theme

    # ------------------------------------------------------------------ path
    def _on_path(self, text):
        install = ei.find_install(text)
        self.install = install
        if install is not None or not text.strip():
            self._save_setting(text.strip())
        self.latest = None
        self._show_found()

    def _save_setting(self, value):
        try:
            self.settings.set(ei.SETTING, value)
        except OSError as exc:
            self.app.log_line(f'[stat editor] could not save the setting: {exc}', _WARN)

    def _on_browse(self):
        start = self.install.folder if self.install else self.app.root_dir
        filters = [('Stat Editor (sluggers-stat-editor.exe, editor.py)', f'{ei.EXE};{ei.SOURCE}'),
                   ('All files', '*.*')]
        self.app.pick_files('stat_path_dialog', 'Select the Stat Editor (sluggers-stat-editor.exe or editor.py)',
                            filters, self._on_browse_paths, initial_dir=start)

    def _on_browse_chosen(self, _sender, app_data):
        app_data = app_data or {}
        self._on_browse_paths(list(app_data.get('selections', {}).values()) or [app_data.get('file_path_name')])

    def _on_browse_paths(self, paths):
        path = next((p for p in paths if p), None)
        if not path:
            return
        install = ei.find_install(path)
        if install is None:
            self.app.log_line(f'[stat editor] no Stat Editor at {path}: pick sluggers-stat-editor.exe or editor.py',
                              _WARN)
            return
        value = ei.setting_for(install.program)
        dpg.set_value('stat_path', value)
        self._on_path(value)

    def _show_found(self):
        install = self.install
        text = dpg.get_value('stat_path').strip()
        if install is not None:
            dpg.set_value('stat_found', f'Found: {install.program} ({install.describe()})')
            dpg.configure_item('stat_found', color=_OK if install.bridge_mode else _WARN)
        else:
            dpg.set_value('stat_found', f'No Stat Editor found in {text}.' if text else
                          'Not set: enter or browse to the folder where you unpacked the Stat Editor.')
            dpg.configure_item('stat_found', color=_WARN)
        dpg.configure_item('stat_site_row', show=install is None)
        dpg.configure_item('stat_update_row', show=install is not None)
        if install is not None and self.latest is None and not self.checking:
            dpg.set_value('stat_update_text', '')
            dpg.configure_item('stat_download', show=False)
        self._shown = None

    def _open_url(self, url):
        try:
            webbrowser.open(url)
        except Exception as exc:              # no browser: say where to go instead
            self.app.log_line(f'[stat editor] could not open a browser ({exc}); the page is {url}', _WARN)

    # ------------------------------------------------------------------ updates
    def _on_check(self):
        if self.checking:
            return
        self.checking = True
        dpg.set_value('stat_update_text', 'Asking GitHub for the latest release...')
        dpg.configure_item('stat_update_text', color=_DIM)

        def work():
            try:
                result, error = ei.fetch_latest_release(), None
            except Exception as exc:          # offline, rate limit, a changed answer: say so, nothing else
                result, error = None, str(exc)
            self.app.output_queue.put(('call', self._check_done, result, error))
        threading.Thread(target=work, daemon=True).start()

    def _check_done(self, release, error):
        self.checking = False
        if release is None:
            dpg.set_value('stat_update_text', f'The update check failed: {error}')
            dpg.configure_item('stat_update_text', color=_WARN)
            dpg.configure_item('stat_download', show=True)
            return
        self.latest = release
        text, newer = ei.update_text(self.install, release)
        dpg.set_value('stat_update_text', text)
        dpg.configure_item('stat_update_text', color=_PENDING if newer else _OK)
        dpg.configure_item('stat_download', show=newer or self.install is None or self.install.version is None)

    # ------------------------------------------------------------------ launching
    @property
    def running(self):
        return self.process is not None

    def _open_problem(self, bridge):
        """Why an Open button is disabled now, or None."""
        if self.install is None:
            return 'Set the Stat Editor folder first.'
        if self.running:
            return f'The Stat Editor is open ({self.mode}); close it to open it again.'
        if self.exporting:
            return 'Writing the bridge file...'
        if self.app.busy:
            return 'A command is running (see the log).'
        if self.install.kind == ei.SOURCE_KIND and ei.python_command() is None:
            return 'This is a source folder (editor.py) and no Python was found on PATH to run it.'
        if not bridge:
            return None
        if not self.install.bridge_mode:
            return ('This Stat Editor has no Bridge Mode (no Bridge folder next to the exe, or no sluggies_bridge.py '
                    'next to editor.py). Only Standalone works with it.')
        if not os.path.isfile(self.bridge_out):
            return '3_Output_Dat/main.dol is missing: run the export first.'
        if self.app.grid_tab.pending.pack is not None:
            return 'A roster pack load is staged: Patch Game or discard it before editing stats.'
        if self.received or self.offering:
            return 'The values the Stat Editor sent are being checked.'
        return None

    def _on_open_bridge(self):
        if self._open_problem(True):
            return
        install = self.install
        ei.remove_bridge(install)
        self.exporting = True

        def done(code, _output):
            self.exporting = False
            if code != 0 or not os.path.isfile(install.bridge_path):
                self.app.log_line('[stat editor] the bridge file was not written (see the log above); the editor '
                                  'was not started.', _WARN)
                return
            if not self._launch(install, BRIDGE):
                ei.remove_bridge(install)
        if not self.app.run_chain([('--stat-bridge-export', install.bridge_path)], on_done=done):
            self.exporting = False

    def _on_open_standalone(self):
        if self._open_problem(False):
            return
        if ei.remove_bridge(self.install):
            self.app.log_line('[stat editor] removed a left bridge file, so the editor starts Standalone.', _DIM)
        self._launch(self.install, STANDALONE)

    def _launch(self, install, mode):
        command = install.command(ei.python_command())
        if command is None:
            self.app.log_line('[stat editor] no Python found to run editor.py.', _WARN)
            return False
        os.makedirs(self.staging_dir, exist_ok=True)
        try:
            self.log_file = open(os.path.join(self.staging_dir, ei.LOG_NAME), 'w', encoding='utf-8', errors='replace')
            self.process = subprocess.Popen(command, cwd=install.folder, stdin=subprocess.DEVNULL,
                                            stdout=self.log_file, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW)
        except OSError as exc:
            self._close_log()
            self.process = None
            self.app.log_line(f'[stat editor] could not start {install.program}: {exc}', _WARN)
            return False
        self.mode, self.launched = mode, install
        self.app.log_line(f'[stat editor] started ({mode}): {install.program}', _OK)
        process = self.process
        threading.Thread(target=lambda: self.app.output_queue.put(('call', self._on_exit, process,
                                                                   process.wait())), daemon=True).start()
        return True

    def _close_log(self):
        if self.log_file is not None:
            try:
                self.log_file.close()
            except OSError:
                pass
            self.log_file = None

    def _on_exit(self, process, code):
        if process is not self.process:
            return
        mode, install = self.mode, self.launched
        self.process, self.mode, self.launched = None, None, None
        self._close_log()
        self.app.log_line(f'[stat editor] closed ({mode}, exit code {code}).', _DIM if code == 0 else _WARN)
        if code != 0:
            for line in self._log_tail():
                self.app.log_line(f'[stat editor]   {line}', _WARN)
        if mode == BRIDGE and install is not None:
            self._receive(install, quiet=False)

    def _log_tail(self):
        try:
            with open(os.path.join(self.staging_dir, ei.LOG_NAME), encoding='utf-8', errors='replace') as f:
                return [line.rstrip() for line in f.readlines()[-_LOG_TAIL:] if line.strip()]
        except OSError:
            return []

    def _receive(self, install, quiet):
        """Move a sent edit file into the staging folder and queue it for its offer; delete the bridge files."""
        try:
            path = ei.take_edits(install, self.staging_dir)
        except OSError as exc:
            self.app.log_line(f'[stat editor] could not take {install.edits_path}: {exc}', _WARN)
            path = None
        ei.remove_bridge(install)
        if path is None:
            if not quiet:
                self.app.log_line('[stat editor] nothing was sent ("Send to Sluggies" was not used); nothing staged.',
                                  _DIM)
            return
        self.app.log_line(f'[stat editor] received {os.path.basename(path)}' + (' (left from an earlier session)'
                                                                                 if quiet else '') + '.', _PENDING)
        self.received.append((path, quiet))

    def _startup_cleanup(self):
        install = self.install
        if install is None:
            return
        if os.path.isfile(install.edits_path):
            self._receive(install, quiet=True)
        elif ei.remove_bridge(install):
            self.app.log_line('[stat editor] removed a bridge file left from an earlier session.', _DIM)

    # ------------------------------------------------------------------ pending
    def _on_discard(self):
        grid = self.app.grid_tab
        if grid._locked() or not gui_grid.staged_stat_files(grid.pending):
            return
        grid.pending.discard(gui_grid.GAME_WIDE)
        grid._pending_changed()
        self.app.log_line('[stat editor] the staged stat edits were discarded.', _DIM)

    def _show_pending(self):
        files = gui_grid.staged_stat_files(self.app.grid_tab.pending)
        dpg.set_value('stat_pending_head', f'{len(files)} staged stat edit file{"s" if len(files) != 1 else ""}'
                      + (' ("Patch Game" on the Character grid tab writes them):' if files else '.'))
        dpg.configure_item('stat_pending_head', color=_PENDING if files else _DIM)
        dpg.delete_item('stat_pending_list', children_only=True)
        for section in self.app.grid_tab.pending.sections:
            if section['action'] == gui_grid.STAT_EDITS:
                name = os.path.basename((section.get('edit') or {}).get('file') or '?')
                what = (section.get('effects') or {}).get(gui_grid.STAT_EDITS) or 'changed values'
                dpg.add_text(f'  {name}: {what}', parent='stat_pending_list', color=_PENDING)

    # ------------------------------------------------------------------ every frame
    def tick(self):
        if not self._started:
            self._started = True
            self._startup_cleanup()
        grid = self.app.grid_tab
        if self.offering is not None and grid.action is None and not self.app.busy:
            self.offering = None                       # its check and dialog are done
        if self.received and self.offering is None and not self.app.busy:
            path, quiet = self.received[0]
            if grid.offer_stat_edits(path, quiet_refusal=quiet):
                self.received.pop(0)
                self.offering = path
        staged = gui_grid.staged_stat_files(grid.pending)
        keep = frozenset([*staged, *(p for p, _q in self.received), *([self.offering] if self.offering else [])])
        if keep != self._pruned and not self.app.busy:
            ei.prune_received(self.staging_dir, keep)
            self._pruned = keep
        shown = (self._open_problem(True), self._open_problem(False), self.running, self.mode, tuple(staged),
                 len(grid.pending.sections), grid._locked(), self.checking)
        if shown != self._shown:
            self._shown = shown
            self._apply(shown)

    def _apply(self, shown):
        bridge_problem, standalone_problem, running, mode, staged, _n, locked, checking = shown
        dpg.configure_item('stat_open_bridge', enabled=bridge_problem is None)
        dpg.configure_item('stat_open_standalone', enabled=standalone_problem is None)
        reason = bridge_problem if standalone_problem is None or bridge_problem == standalone_problem else \
            standalone_problem
        dpg.set_value('stat_open_reason', '' if running else (reason or ''))
        dpg.set_value('stat_running', f'The Stat Editor is open ({mode}).' + (
            ' Use "Send to Sluggies" in it to bring the changes back.' if mode == BRIDGE else '') if running else '')
        dpg.configure_item('stat_discard', show=bool(staged), enabled=not locked)
        dpg.configure_item('stat_update', enabled=not checking)
        dpg.configure_item('stat_browse', enabled=not running)
        dpg.configure_item('stat_path', enabled=not running)
        self._show_pending()

    # ------------------------------------------------------------------ closing
    def confirm_close(self, then):
        """Closing Sluggies Tools: while the editor is open in Bridge Mode ask first (what it sends after this is
        offered on the next start only if the game files are unchanged by then)."""
        if self.mode != BRIDGE:
            then()
            return
        if dpg.does_item_exist(_CLOSE_DIALOG):
            return

        def proceed():
            dpg.delete_item(_CLOSE_DIALOG)
            then()
        vw = dpg.get_viewport_client_width()
        with dpg.window(tag=_CLOSE_DIALOG, label='Close while the Stat Editor is open?', modal=True, no_collapse=True,
                        no_saved_settings=True, autosize=True, pos=(max(0, (vw - 600) // 2), 120),
                        on_close=lambda *_: dpg.delete_item(_CLOSE_DIALOG)):
            for line in ('The Stat Editor is still open in Bridge Mode.',
                         'Sluggies Tools cannot stage what it sends after closing. A sent file is offered on the next',
                         'start of Sluggies Tools, and only if the game files have not changed by then.'):
                dpg.add_text(line, color=_TEXT)
            dpg.add_spacer(height=6)
            with dpg.group(horizontal=True):
                ok = dpg.add_button(label='Close anyway', width=130, height=30, callback=proceed)
                dpg.bind_item_theme(ok, 'primary_theme')
                dpg.add_button(label='Cancel', width=110, height=30, callback=lambda: dpg.delete_item(_CLOSE_DIALOG))
