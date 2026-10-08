"""Dear PyGui front end for start.py.

Every button runs the same ``start.py`` command line a user would type, as a
child process, so the GUI, the CLI and StartTools.bat share one code path and
one set of logs. Child output is shown in the log pane and echoed to the
console window. The child's stdin is a pipe: an interactive prompt (y/n,
value, file name, ...) announced by ``slogger.ask`` opens a popup that sends
the answer, so the console never has to be shown; the console's input row can
answer too.

The "Character grid" tab (``gui_character_grid``, logic in ``gui_grid``)
shows the draft grid of 3_Output_Dat. It reads it with ``start.py
--roster-state`` in the background at start, when the tab is opened, on
Refresh and after every command chain that can change the game files.

The "Stat Editor" tab (``StatEditor/gui_tab``) launches Philenarion's Sluggers
Stat Editor (Bridge Mode or Standalone) and stages the values it sends back as
pending edits of the character grid.

The "Maintenance" tab (``gui_maintenance``, checks in ``maintenance``) lists
problems in the working folders, such as duplicate model files, on Scan.

Fonts: on Windows the GUI upgrades to the system Segoe UI when present;
otherwise it uses the Open Sans TTF already bundled with the release (the same
one the roster name plates are drawn with). When neither file is available it
keeps Dear PyGui's built-in ProggyClean font.
"""

import ntpath
import os
import platform
import queue
import re
import subprocess
import sys
import threading

import dearpygui.dearpygui as dpg
import gui_character_grid
import gui_grid
import gui_maintenance
import gui_settings
import native_dialog
import slogger
from StatEditor import gui_tab as stat_editor_tab

_MAX_LOG_LINES = 3000
_LOG_COLOR = (220, 220, 220, 255)
_PROMPT_COLOR = (255, 210, 90, 255)
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_PRIMARY_SIZE = (170, 44)
_VIEWPORT_TITLE = 'Sluggies Tools'
_VIEWPORT_SIZE = (1440, 800)    # fits the 12x5 grid, a grid note line and the "Show console" checkbox unscrolled
_CONSOLE_HEIGHT = 240           # the window grows by this when the console is shown
_CLOSE_DIALOG = 'close_running_dialog'
_PROMPT_DIALOG = 'prompt_dialog'
_PROMPT_FIELD = 'prompt_dialog_field'
_MARKER_LINE = re.compile(re.escape(slogger.PROMPT_MARKER) + r'[^\n]*\n')

# Font sizes for the UI. 16pt matches Dear PyGui's default at 1x DPI. Open
# Sans is slightly wider than the built-in ProggyClean at the same point
# size, so it is registered one size smaller to keep labels and the 24px
# quick-send buttons fitting.
_FONT_SIZE_SEGOE_UI = 16
_FONT_SIZE_OPEN_SANS = 15
_WINDOWS = platform.system() == 'Windows'
_FONT_TAG = 'ui_font'
# Relative to the directory that holds SluggiesTools/ -- both the repo root and
# the portable release carry the TTF at this path.
_BUNDLED_FONT_REL = os.path.join('SluggiesTools', 'Roster', 'fonts', 'OpenSans.ttf')


def _app_base_dir():
    """Directory that holds SluggiesTools/: the repo in source builds, the
    application folder next to the executable in frozen builds (the same ROOT
    start.py uses)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bundled_font_path():
    return os.path.join(_app_base_dir(), _BUNDLED_FONT_REL)


# The system font candidate is a Windows path, so it is built and inspected
# with ntpath (not os.path) to behave the same when tests run on Linux CI.
def _system_fonts_dir():
    if not _WINDOWS:
        return ''
    return ntpath.join(os.environ.get('SystemRoot', r'C:\Windows'), 'Fonts')


def _font_candidates():
    """(path, label) pairs in preference order: system Segoe UI, then the
    bundled Open Sans."""
    candidates = []
    system_dir = _system_fonts_dir()
    if system_dir:
        candidates.append((ntpath.join(system_dir, 'segoeui.ttf'), 'Segoe UI'))
    candidates.append((_bundled_font_path(), 'Open Sans'))
    return candidates


def _existing_font_paths(exists=os.path.isfile):
    """Every candidate that exists, in preference order; empty keeps Dear
    PyGui's built-in ProggyClean. The existence check is injectable so the
    strategy is testable without touching the filesystem."""
    return [path for path, _label in _font_candidates() if path and exists(path)]


def _font_size_for(path):
    # ntpath.basename splits on both '\' and '/'.
    if path and ntpath.basename(path).lower() == 'segoeui.ttf':
        return _FONT_SIZE_SEGOE_UI
    return _FONT_SIZE_OPEN_SANS


class SluggiesGui:
    ROSTER_SKIP = '(no roster changes)'
    ROSTER_RESET = '(reset roster to vanilla)'

    def __init__(self, command_prefix, root_dir):
        self.command_prefix = list(command_prefix)
        self.root_dir = root_dir
        self.config_dir = os.path.join(root_dir, '1_Input', '_RosterConfigurations')
        self.models_dir = os.path.join(root_dir, '2_Output_Models')
        self.output_queue = queue.Queue()
        self.process = None
        self.partial = ''
        self.pending = []
        self.chain = []
        self.current = None                # the running step's arguments
        self.on_chain_done = None          # run_chain's callback: (exit code, the chain's output)
        self.chain_output = []
        self.action_buttons = []
        self.picking = False               # a native file dialog is open
        self.console_grow = 0              # px the window grew when the console was shown
        self.settings = gui_settings.Settings(os.path.join(root_dir, gui_settings.SETTINGS_REL))   # one for every tab
        self.grid_tab = gui_character_grid.CharacterGridTab(self)
        self.stat_tab = stat_editor_tab.StatEditorTab(self)
        self.maintenance_tab = gui_maintenance.MaintenanceTab(self)

    # ------------------------------------------------------------------ run
    def run_command(self, *args):
        self.run_chain([args])

    @property
    def busy(self):
        return self.process is not None

    def run_chain(self, steps, on_done=None):
        """Run several commands in order, stopping at the first failure. ``on_done(code, output)`` runs after the
        last step (or the failed one) with the whole chain's output. False when a command already runs."""
        if self.process is not None:
            self._log_line('A command is already running.', _PROMPT_COLOR)
            return False
        self.pending = [step if isinstance(step, tuple) else tuple(step) for step in steps]   # keeps a ShellStep
        self.chain = list(self.pending)
        self.on_chain_done, self.chain_output = on_done, []
        self._start_next()
        return True

    def _start_next(self):
        args = self.current = self.pending.pop(0)
        command = list(args) if isinstance(args, gui_grid.ShellStep) else [*self.command_prefix, *args]
        self._log_line('> ' + ' '.join(args), _PROMPT_COLOR)
        env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8', **{slogger.PROMPT_ENV: '1'})
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self.root_dir,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=_NO_WINDOW,
            )
        except OSError as exc:
            self._log_line(f'Could not start command: {exc}', _PROMPT_COLOR)
            self.pending = []
            self._set_busy(False)
            self._chain_done(1)
            return
        self._set_busy(True)
        threading.Thread(target=self._pump_output, args=(self.process,), daemon=True).start()

    def _pump_output(self, process):
        stream = process.stdout
        while True:
            chunk = stream.read1(4096) if hasattr(stream, 'read1') else stream.read(1)
            if not chunk:
                break
            text = chunk.decode('utf-8', errors='replace')
            try:
                sys.stdout.write(_MARKER_LINE.sub('', text))
                sys.stdout.flush()
            except (OSError, ValueError, UnicodeEncodeError):
                pass
            self.output_queue.put(text)
        self.output_queue.put(('exit', process.wait()))

    def stop_command(self):
        if self.process is not None:
            self.process.terminate()

    def send_input(self, text):
        process = self.process
        if process is None or process.stdin is None:
            return
        self._log_line(f'< {text}', _PROMPT_COLOR)
        self._close_prompt()                       # answered, from the popup or the console's input row
        try:
            process.stdin.write((text + '\n').encode('utf-8'))
            process.stdin.flush()
        except OSError:
            pass

    # ------------------------------------------------------------------ prompt
    def _open_prompt(self, kind, prompt):
        """A command waits for an answer (``slogger.ask``): ask in a modal popup so the console need not be shown.
        ``kind`` is one of ``slogger.PROMPT_KINDS``; Stop command (or the popup's close button) ends the command."""
        step = ' '.join(os.path.basename(a) if os.path.isabs(a) else a for a in self.current or ())
        self.ask_text(f'Input needed: {step}' if step else 'Input needed',
                      prompt.strip() or 'The command waits for an answer.', self.send_input, self.stop_command,
                      kind=kind, cancel_label='Stop command')

    def ask_text(self, title, text, on_answer, on_cancel, kind='text', initial='', cancel_label='Cancel'):
        """A modal popup asking for an answer; ``kind`` (``slogger.PROMPT_KINDS``) picks Yes / No, Continue or a
        field with OK (file / save: plus Browse...). The popup closes first, then ``on_answer(text)`` or, for
        ``cancel_label`` and the close button, ``on_cancel()`` runs. One at a time: a new one replaces it."""
        self._close_prompt()

        def answer(value):
            self._close_prompt()
            on_answer(value)

        def cancel():
            self._close_prompt()
            on_cancel()
        vw = dpg.get_viewport_client_width()
        with dpg.window(tag=_PROMPT_DIALOG, label=title, modal=True, no_collapse=True, no_saved_settings=True,
                        autosize=True, pos=(max(0, (vw - 620) // 2), 120), on_close=lambda *_: cancel()):
            dpg.add_text(text, wrap=600)
            dpg.add_spacer(height=6)
            if kind in ('text', 'file', 'save'):
                with dpg.group(horizontal=True):
                    dpg.add_input_text(tag=_PROMPT_FIELD, default_value=initial, width=500 if kind == 'text' else 420,
                                       on_enter=True, callback=lambda: answer(dpg.get_value(_PROMPT_FIELD)))
                    if kind != 'text' and native_dialog.enabled():
                        dpg.add_button(label='Browse...', callback=lambda: self._prompt_browse(kind == 'save'))
                dpg.add_spacer(height=6)
            with dpg.group(horizontal=True):
                if kind == 'yesno':
                    yes = dpg.add_button(label='Yes', width=110, height=30, callback=lambda: answer('y'))
                    dpg.add_button(label='No', width=110, height=30, callback=lambda: answer('n'))
                elif kind == 'key':
                    yes = dpg.add_button(label='Continue', width=110, height=30, callback=lambda: answer(''))
                else:
                    yes = dpg.add_button(label='OK', width=110, height=30,
                                         callback=lambda: answer(dpg.get_value(_PROMPT_FIELD)))
                dpg.bind_item_theme(yes, 'primary_theme')
                dpg.add_button(label=cancel_label, height=30, callback=cancel)
        if dpg.does_item_exist(_PROMPT_FIELD):
            dpg.focus_item(_PROMPT_FIELD)

    def _close_prompt(self):
        if dpg.does_item_exist(_PROMPT_DIALOG):
            dpg.delete_item(_PROMPT_DIALOG)

    def _prompt_browse(self, save):
        """Fill the prompt's field from the Windows "Open" / "Save As" dialog, started in the field's folder."""
        if self.picking:
            return
        self.picking = True
        current = dpg.get_value(_PROMPT_FIELD).strip().strip('"')
        initial = next((d for d in (os.path.dirname(current), self.root_dir) if d and os.path.isdir(d)),
                       self.root_dir)
        owner = native_dialog.find_owner_window(_VIEWPORT_TITLE)

        def work():
            try:
                result = native_dialog.ask_open_files('Choose a file', initial, [('All files', '*.*')],
                                                      owner=owner, save=save)
            except Exception as exc:          # no dialog: the path is typed into the field instead
                result = None
                self.output_queue.put(('call', self._log_line, f'Windows file dialog failed ({exc}).', _PROMPT_COLOR))
            self.output_queue.put(('call', self._browse_done, result))
        threading.Thread(target=work, daemon=True).start()

    def _browse_done(self, result):
        self.picking = False
        if result and dpg.does_item_exist(_PROMPT_FIELD):
            dpg.set_value(_PROMPT_FIELD, result[0])

    # ------------------------------------------------------------------ files
    def pick_files(self, fallback_tag, title, filters, on_files, on_cancel=None, multi=False, initial_dir=None,
                   save=False, default_ext=None):
        """Ask for files: the Windows "Open" dialog (``save``: "Save As") when available (``native_dialog``),
        else the Dear PyGui dialog ``fallback_tag``, whose own callbacks handle the result. ``on_files(paths)`` /
        ``on_cancel()`` run on the GUI thread. ``initial_dir``: where it starts (default 2_Output_Models). False
        when a native dialog is already open."""
        if not native_dialog.enabled():
            dpg.show_item(fallback_tag)
            return True
        if self.picking:
            return False
        self.picking = True
        initial = next((d for d in (initial_dir, self.models_dir) if d and os.path.isdir(d)), self.root_dir)
        owner = native_dialog.find_owner_window(_VIEWPORT_TITLE)

        def work():
            try:
                result, error = native_dialog.ask_open_files(title, initial, filters, multi, owner, save,
                                                             default_ext), None
            except Exception as exc:          # any failure: the built-in dialog takes over
                result, error = None, str(exc)
            self.output_queue.put(('files', fallback_tag, on_files, on_cancel, result, error))
        threading.Thread(target=work, daemon=True).start()
        return True

    def _files_done(self, fallback_tag, on_files, on_cancel, result, error):
        self.picking = False
        if error is not None:
            self._log_line(f'Windows file dialog failed ({error}); using the built-in one.', _PROMPT_COLOR)
            dpg.show_item(fallback_tag)
        elif result:
            on_files(result)
        elif on_cancel is not None:
            on_cancel()

    # ------------------------------------------------------------------ log
    def _drain_queue(self):
        while True:
            try:
                item = self.output_queue.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, tuple) and item[0] == 'grid_state':
                self.grid_tab.on_read_done(item[1], item[2])
            elif isinstance(item, tuple) and item[0] == 'files':
                self._files_done(*item[1:])
            elif isinstance(item, tuple) and item[0] == 'call':     # a worker thread's result, run on the GUI thread
                item[1](*item[2:])
            elif isinstance(item, tuple):
                self._finish_process(item[1])
            else:
                if self.on_chain_done is not None:
                    self.chain_output.append(item)
                self._append_text(item)

    def _append_text(self, text):
        text = text.replace('\r\n', '\n')
        buffer = self.partial + text
        *lines, self.partial = buffer.split('\n')
        for line in lines:
            request = slogger.parse_prompt_marker(line)
            if request is not None:
                self._open_prompt(*request)        # the command waits for an answer
                continue
            self._log_line(line.rsplit('\r', 1)[-1])
        self.partial = self.partial.rsplit('\r', 1)[-1]
        dpg.set_value('log_pending', self.partial)

    def log_line(self, line, color=_LOG_COLOR):
        self._log_line(line, color)

    def _log_line(self, line, color=_LOG_COLOR):
        dpg.add_text(line, parent='log_window', before='log_pending', color=color)
        children = dpg.get_item_children('log_window', 1)
        for stale in children[:-_MAX_LOG_LINES - 1]:
            dpg.delete_item(stale)
        dpg.set_y_scroll('log_window', 1.0e9)

    def show_console(self, show=True):
        """Show or hide the console (log, input row and its buttons); the checkbox follows. The GUI window grows
        down by _CONSOLE_HEIGHT to make room and shrinks back by what it grew when the console is hidden."""
        dpg.set_value('show_console', show)
        if show == dpg.get_item_configuration('console_area')['show']:
            return
        dpg.configure_item('console_area', show=show)
        if show:
            self.console_grow = self._grow_for_console()
            dpg.set_y_scroll('log_window', 1.0e9)
        elif self.console_grow:
            dpg.set_viewport_height(max(_VIEWPORT_SIZE[1], dpg.get_viewport_height() - self.console_grow))
            self.console_grow = 0

    @staticmethod
    def _grow_for_console():
        """Make the GUI window _CONSOLE_HEIGHT taller; returns the px gained. Without room below it first moves up
        (never above the screen top). Nothing for a maximized window (Windows: ``room_below`` gives None)."""
        hwnd = native_dialog.find_owner_window(_VIEWPORT_TITLE)
        if hwnd:
            room = native_dialog.room_below(hwnd)
            if room is None:
                return 0
            if room < _CONSOLE_HEIGHT:
                x, y = dpg.get_viewport_pos()
                up = min(_CONSOLE_HEIGHT - room, max(0, int(y)))
                dpg.set_viewport_pos([x, y - up])
                room += up
            grow = min(_CONSOLE_HEIGHT, room)
        else:
            grow = _CONSOLE_HEIGHT
        dpg.set_viewport_height(dpg.get_viewport_height() + grow)
        return grow

    def clear_console(self):
        """Empty the console view only; log files are untouched. The pending partial line (an open prompt) stays."""
        for item in dpg.get_item_children('log_window', 1):
            if dpg.get_item_alias(item) != 'log_pending':
                dpg.delete_item(item)

    def _finish_process(self, code):
        self._close_prompt()                       # an unanswered prompt dies with its command
        if self.partial:
            self._log_line(self.partial)
            self.partial = ''
            dpg.set_value('log_pending', '')
        self._log_line(f'[finished, exit code {code}]', _PROMPT_COLOR)
        self.process = None
        if code == 0 and self.pending:
            self._start_next()
            return
        if self.pending:
            self._log_line('Remaining steps skipped because a step failed.', _PROMPT_COLOR)
            self.pending = []
        self._set_busy(False)
        if gui_grid.chain_writes(self.chain):
            self.grid_tab.request_read()          # the game files may have changed: re-read the grid
        self._chain_done(code)

    def _chain_done(self, code):
        callback, self.on_chain_done = self.on_chain_done, None
        output, self.chain_output = ''.join(self.chain_output), []
        if callback is not None:
            callback(code, output)

    def _set_busy(self, busy):
        for button in self.action_buttons:
            dpg.configure_item(button, enabled=not busy)
        dpg.configure_item('stop_button', enabled=busy)
        dpg.configure_item('roster_spinner', show=busy)
        step = ' '.join(os.path.basename(a) if os.path.isabs(a) else a for a in self.current or ())
        dpg.set_value('roster_status', f'Running: {step}' if busy else '')
        self.grid_tab.set_busy(busy)

    # ------------------------------------------------------------------ ui
    def _action(self, label, callback, tip=None, primary=False):
        if primary:
            button = dpg.add_button(label=label, callback=callback, width=_PRIMARY_SIZE[0], height=_PRIMARY_SIZE[1])
            dpg.bind_item_theme(button, 'primary_theme')
        else:
            button = dpg.add_button(label=label, callback=callback)
        self.action_buttons.append(button)
        if tip:
            with dpg.tooltip(button):
                dpg.add_text(tip)
        return button

    def _build_themes(self):
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (60, 255, 90, 255))
            with dpg.theme_component(dpg.mvText):
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, gui_character_grid.TEXT_SPACING_Y)
        dpg.bind_theme(global_theme)
        with dpg.theme(tag='primary_theme'):
            for state, colors in (
                (True, ((dpg.mvThemeCol_Button, (46, 140, 64, 255)),
                        (dpg.mvThemeCol_ButtonHovered, (60, 170, 80, 255)),
                        (dpg.mvThemeCol_ButtonActive, (36, 110, 50, 255)))),
                (False, ((dpg.mvThemeCol_Button, (70, 80, 72, 255)),
                         (dpg.mvThemeCol_Text, (150, 150, 150, 255)))),
            ):
                with dpg.theme_component(dpg.mvButton, enabled_state=state):
                    for col, value in colors:
                        dpg.add_theme_color(col, value)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)

    def _build_full_tab(self):
        with dpg.tab(label='All-In-One Export'):
            dpg.add_text('1) Export all models with untangled textures (overwrites 3_Output_Dat/dt_na.dat and main.dol)')
            dpg.add_text('2) Apply the chosen roster preset')
            dpg.add_text('3) Turn on CPU vs CPU and CPU vs CPU management (hold minus on game start button)')
            dpg.add_text('4) Write the character icons (FrontIcon/SideIcon) from 3_Output_Dat into the model folders')
            dpg.add_text('A failing step stops the rest.')
            dpg.add_spacer(height=6)
            with dpg.group(horizontal=True):
                self._action('Start', self._on_full_export, primary=True)
                dpg.add_text('Roster preset:')
                dpg.add_combo([], tag='full_roster', width=420)
                dpg.add_button(label='Refresh', callback=self._refresh_configs)
            dpg.add_text('', tag='full_roster_hint', color=_PROMPT_COLOR, wrap=700)

    def _on_full_export(self):
        choice = dpg.get_value('full_roster')
        steps = [('--export', '--untangle')]
        if choice == self.ROSTER_RESET:
            steps.append(('--roster', '--remove'))
        elif choice and choice != self.ROSTER_SKIP:
            steps.append(('--roster', '--config', os.path.join(self.config_dir, choice), '--fresh'))
        steps.append(('--game-options', '--on', 'cpu_vs_cpu', 'cpu_management'))
        steps.append(('--export-icons', '--use-output'))

        def start():
            self.show_console(True)          # a long run: its progress shows in the console
            self.run_chain(steps)
        self._config_guard(start, 'Start the all-in-one export and lose the configuration?',
                           'The all-in-one export', stats_lost=True)

    def _build_export_tab(self):
        with dpg.tab(label='Export 3D'):
            dpg.add_text('Export all models from 1_Input to 2_Output_Models.')
            dpg.add_checkbox(label='Untangle (overwrites 3_Output_Dat/dt_na.dat and main.dol)', tag='exp_untangle')
            dpg.add_checkbox(label='Also write .glb files', tag='exp_glb', default_value=True)
            dpg.add_checkbox(label='Skip textures', tag='exp_notex')
            dpg.add_checkbox(label='Debug (raw byte arrays instead of base64)', tag='exp_debug')
            icons = dpg.add_checkbox(label='Export Icons', tag='exp_icons', default_value=True)
            with dpg.tooltip(icons):
                dpg.add_text('Write FrontIcon.png and SideIcon.png into each character model folder.')
            self._action('Export models', self._on_export, primary=True)

    def _on_export(self):
        args = ['--export']
        for tag, flag in (('exp_untangle', '--untangle'), ('exp_glb', '--glb'),
                          ('exp_notex', '--notex'), ('exp_debug', '--debug')):
            if dpg.get_value(tag):
                args.append(flag)
        if dpg.get_value('exp_icons'):
            self.run_chain([tuple(args), ('--export-icons',)])
        else:
            self.run_command(*args)

    def _build_roster_tab(self):
        with dpg.tab(label='Roster Size'):
            dpg.add_text('Inject a roster configuration into 3_Output_Dat.')
            with dpg.group(horizontal=True):
                dpg.add_combo([], tag='roster_config', width=420)
                dpg.add_button(label='Refresh', callback=self._refresh_configs)
            dpg.add_text('', tag='roster_hint', color=_PROMPT_COLOR, wrap=700)
            dpg.add_text('Slots on both the old and the new grid keep their customisations (models, equipment, '
                         'names, portraits, stats, voices, stat edits); slots leaving the grid are reset; new '
                         'slots are empty.', wrap=700)
            fresh = dpg.add_checkbox(label='Start fresh (keep no slot customisations)', tag='roster_fresh')
            with dpg.tooltip(fresh):
                dpg.add_text('Inject the preset as it is: new IDs lose their models, names and portraits; stock '
                             'slots keep patched models and stat edits.')
            dpg.add_checkbox(label='Dry run (validate without writing)', tag='roster_dry')
            self._action('Inject roster', self._on_roster, primary=True)
            self._action('Reset to vanilla',
                         lambda: self._config_guard(lambda: self.run_command('--roster', '--remove'),
                                                       'Reset to vanilla and lose the configuration?',
                                                       'Resetting to vanilla', stats_lost=True,
                                                       stats_reason=gui_grid.STATS_RESET),
                         'Grid, new IDs, names, portraits, voices, stats sources and stat edits back to 1_Input; '
                         'models patched into stock slots stay.')
            self._action('Repair unused characters', lambda: self.run_command('--resplit-unused'),
                         'Repair: give unused-character routes (dirs 89-94) their own block copies again.')
            with dpg.group(horizontal=True):
                dpg.add_loading_indicator(tag='roster_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='roster_status')
        self._refresh_configs()

    def _refresh_configs(self):
        try:
            names = sorted(n for n in os.listdir(self.config_dir) if n.lower().endswith('.json'))
        except OSError:
            names = []
        missing = '' if names else f'No roster .json files found in {self.config_dir}'
        for tag in ('roster_hint', 'full_roster_hint'):
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, missing)
        dpg.configure_item('roster_config', items=names)
        if names and dpg.get_value('roster_config') not in names:
            dpg.set_value('roster_config', names[0])
        if dpg.does_item_exist('full_roster'):
            full_items = [self.ROSTER_SKIP, self.ROSTER_RESET, *names]
            dpg.configure_item('full_roster', items=full_items)
            if dpg.get_value('full_roster') not in full_items:
                default = '02_Stock_and_Unused.json'
                dpg.set_value('full_roster', default if default in names else self.ROSTER_SKIP)

    def _on_roster(self):
        name = dpg.get_value('roster_config')
        if not name:
            self._log_line('No roster configuration selected.', _PROMPT_COLOR)
            return
        path = os.path.join(self.config_dir, name)
        fresh = dpg.get_value('roster_fresh')
        if dpg.get_value('roster_dry'):
            self.run_command(*gui_grid.switch_command(path, dry_run=True, fresh=fresh))
        elif fresh:
            self._config_guard(lambda: self.run_command(*gui_grid.switch_command(path, fresh=True)),
                               'Inject the roster and lose the configuration?', 'Injecting a roster (start fresh)')
        else:
            self.grid_tab.switch_roster(path)

    def _config_guard(self, then, title, action, stats_lost=False, stats_reason=gui_grid.STATS_REPLACED):
        """Export / injection replace the game files: asks first when the configuration is not vanilla, the
        grid has pending edits or stat edits are at risk (OK / Save Configuration / Cancel). ``stats_lost``: every
        stat edit goes (``stats_reason`` says why)."""
        self.grid_tab.config_guard(then, title, action, stats_lost, stats_reason)

    def _on_close_request(self, *_):
        """The window's close button: while a command runs ask first (closing stops it), then with pending grid
        edits, then while the Stat Editor is open in Bridge Mode."""
        def close():
            def stop():
                self.stop_command()                  # only once every question is answered with OK
                dpg.stop_dearpygui()
            self.grid_tab.confirm_discard(lambda: self.stat_tab.confirm_close(stop),
                                          'Close and discard the pending edits?',
                                          'There are edits that "Patch Game" has not written yet.')
        if not self.busy:
            close()
            return
        if dpg.does_item_exist(_CLOSE_DIALOG):
            return                                   # already asking

        def proceed():
            dpg.delete_item(_CLOSE_DIALOG)
            close()
        steps = len(self.pending) + 1
        lines = ['A command is still running:',
                 '  ' + ' '.join(self.current or ()),
                 *([f'  ({steps - 1} more step{"s" if steps != 2 else ""} queued after it)'] if steps > 1 else []),
                 'Closing stops it now. Files it is writing (2_Output_Models, 3_Output_Dat) may be left',
                 'incomplete; run it again afterwards.']
        vw = dpg.get_viewport_client_width()
        with dpg.window(tag=_CLOSE_DIALOG, label='Close while a command is running?', modal=True, no_collapse=True,
                        no_saved_settings=True, autosize=True, pos=(max(0, (vw - 560) // 2), 120),
                        on_close=lambda *_: dpg.delete_item(_CLOSE_DIALOG)):
            for i, line in enumerate(lines):
                dpg.add_text(line, color=_PROMPT_COLOR if i == 1 else _LOG_COLOR)
            dpg.add_spacer(height=6)
            with dpg.group(horizontal=True):
                ok = dpg.add_button(label='OK', width=110, height=30, callback=proceed)
                dpg.bind_item_theme(ok, 'primary_theme')
                dpg.add_button(label='Cancel', width=110, height=30,
                               callback=lambda: dpg.delete_item(_CLOSE_DIALOG))

    def _on_tab(self, _sender, tab):
        self.grid_tab.forget_copy()
        if (dpg.get_item_alias(tab) in ('grid_tab', 'options_tab')
                and self.grid_tab.loader.status != gui_grid.StateLoader.RUNNING):
            self.grid_tab.request_read()

    def _on_send(self, *_):
        text = dpg.get_value('stdin_field')
        dpg.set_value('stdin_field', '')
        self.send_input(text)

    def _setup_fonts(self):
        """Register the UI font (Segoe UI on Windows, else bundled Open Sans);
        without a file the built-in ProggyClean stays the default. Must run
        inside a font registry before setup_dearpygui(); a failed registration
        moves on to the next font (Segoe UI -> Open Sans -> ProggyClean)
        instead of killing the window."""
        paths = _existing_font_paths()
        if not paths:
            slogger.info('No UI font found; using the built-in ProggyClean font',
                         source='gui')
            return
        for path in paths:
            try:
                with dpg.font_registry():
                    dpg.add_font(path, _font_size_for(path), tag=_FONT_TAG)
                if dpg.does_item_exist(_FONT_TAG):
                    dpg.bind_font(_FONT_TAG)
                    name = os.path.basename(path)
                    slogger.info(f'UI font registered: {name} at {_font_size_for(path)}pt',
                                 source='gui')
                    return
            except Exception:
                pass
            slogger.warning(f'Could not register UI font {path}', source='gui')
        slogger.warning('No UI font could be registered; '
                        'falling back to the built-in ProggyClean font',
                        source='gui')

    def build(self):
        dpg.create_context()
        self._build_themes()
        self._setup_fonts()
        with dpg.window(tag='main_window'):
            with dpg.tab_bar(tag='main_tab_bar', callback=self._on_tab):
                self._build_full_tab()
                self._build_export_tab()
                self._build_roster_tab()
                self.grid_tab.build()
                self.stat_tab.build()
                self.grid_tab.build_options()
                self.maintenance_tab.build()
            dpg.add_separator()
            # hidden by default; a command that waits for an answer asks in a popup instead (_open_prompt)
            dpg.add_checkbox(label='Show console', tag='show_console', default_value=False,
                             callback=lambda _sender, value: self.show_console(value))
            with dpg.group(tag='console_area', show=False):
                with dpg.child_window(tag='log_window', height=-34, border=True):
                    dpg.add_text('', tag='log_pending', color=_PROMPT_COLOR)
                with dpg.group(horizontal=True):
                    dpg.add_input_text(tag='stdin_field', width=-250, hint='Answer to a prompt (y/n, value, ...)',
                                       on_enter=True, callback=self._on_send)
                    dpg.add_button(label='Send', callback=self._on_send)
                    dpg.add_button(label='y', width=24, callback=lambda: self.send_input('y'))
                    dpg.add_button(label='n', width=24, callback=lambda: self.send_input('n'))
                    dpg.add_button(label='Stop', tag='stop_button', enabled=False, callback=self.stop_command)
                    dpg.add_button(label='Clear', callback=self.clear_console)
        # the close button asks first while a command runs or grid edits are pending
        dpg.create_viewport(title=_VIEWPORT_TITLE, width=_VIEWPORT_SIZE[0], height=_VIEWPORT_SIZE[1],
                            disable_close=True)
        dpg.set_exit_callback(self._on_close_request)
        dpg.set_primary_window('main_window', True)
        dpg.set_viewport_resize_callback(lambda *_: self.grid_tab.on_viewport_resize())
        dpg.setup_dearpygui()
        dpg.show_viewport()
        self.grid_tab.request_read()

    def run(self):
        self.build()
        while dpg.is_dearpygui_running():
            self._drain_queue()
            self.stat_tab.tick()
            dpg.render_dearpygui_frame()
        self.stop_command()
        dpg.destroy_context()


def run_gui(command_prefix, root_dir):
    """Open the window and block until it is closed."""
    SluggiesGui(command_prefix, root_dir).run()
