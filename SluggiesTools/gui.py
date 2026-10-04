"""Dear PyGui front end for start.py.

Every button runs the same ``start.py`` command line a user would type, as a
child process, so the GUI, the CLI and StartTools.bat share one code path and
one set of logs. Child output is shown in the log pane and echoed to the
console window. The child's stdin is a pipe, so the interactive y/n prompts in
the export, icon and hammerspace tools can be answered from the input row.

Fonts: on Windows the GUI upgrades to the system Segoe UI when present;
otherwise it uses the Open Sans TTF already bundled with the release (the same
one the roster name plates are drawn with). When neither file is available it
keeps Dear PyGui's built-in ProggyClean font.
"""

import ntpath
import os
import platform
import queue
import subprocess
import sys
import threading

import dearpygui.dearpygui as dpg
import slogger

_MAX_LOG_LINES = 3000
_LOG_COLOR = (220, 220, 220, 255)
_PROMPT_COLOR = (255, 210, 90, 255)
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_PRIMARY_SIZE = (170, 44)

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


def _select_font_path(exists=os.path.isfile):
    """The preferred existing candidate, or None."""
    paths = _existing_font_paths(exists)
    return paths[0] if paths else None


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
        self.action_buttons = []

    # ------------------------------------------------------------------ run
    def run_command(self, *args):
        self.run_chain([args])

    def run_chain(self, steps):
        """Run several commands in order, stopping at the first failure."""
        if self.process is not None:
            self._log_line('A command is already running.', _PROMPT_COLOR)
            return
        self.pending = [tuple(step) for step in steps]
        self._start_next()

    def _start_next(self):
        args = self.pending.pop(0)
        command = [*self.command_prefix, *args]
        self._log_line('> ' + ' '.join(args), _PROMPT_COLOR)
        env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
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
                sys.stdout.write(text)
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
        try:
            process.stdin.write((text + '\n').encode('utf-8'))
            process.stdin.flush()
        except OSError:
            pass

    # ------------------------------------------------------------------ log
    def _drain_queue(self):
        while True:
            try:
                item = self.output_queue.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, tuple):
                self._finish_process(item[1])
            else:
                self._append_text(item)

    def _append_text(self, text):
        text = text.replace('\r\n', '\n')
        buffer = self.partial + text
        *lines, self.partial = buffer.split('\n')
        for line in lines:
            self._log_line(line.rsplit('\r', 1)[-1])
        self.partial = self.partial.rsplit('\r', 1)[-1]
        dpg.set_value('log_pending', self.partial)

    def _log_line(self, line, color=_LOG_COLOR):
        dpg.add_text(line, parent='log_window', before='log_pending', color=color)
        children = dpg.get_item_children('log_window', 1)
        for stale in children[:-_MAX_LOG_LINES - 1]:
            dpg.delete_item(stale)
        dpg.set_y_scroll('log_window', 1.0e9)

    def _finish_process(self, code):
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

    def _set_busy(self, busy):
        for button in self.action_buttons:
            dpg.configure_item(button, enabled=not busy)
        dpg.configure_item('stop_button', enabled=busy)

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
        with dpg.tab(label='All-In-One export'):
            dpg.add_text('1) Export all models with untangled textures (overwrites 3_Output_Dat/dt_na.dat and main.dol)')
            dpg.add_text('2) Apply the chosen roster preset')
            dpg.add_text('3) Turn on CPU vs CPU and CPU vs CPU management')
            dpg.add_text('4) Export the player icons from 3_Output_Dat')
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
            steps.append(('--roster', '--config', os.path.join(self.config_dir, choice)))
        steps.append(('--game-options', '--on', 'cpu_vs_cpu', 'cpu_management'))
        steps.append(('--export-icons', '--use-output'))
        self.run_chain(steps)

    def _build_export_tab(self):
        with dpg.tab(label='Export'):
            dpg.add_text('Export all models from 1_Input to 2_Output_Models.')
            dpg.add_checkbox(label='Untangle (overwrites 3_Output_Dat/dt_na.dat and main.dol)', tag='exp_untangle')
            dpg.add_checkbox(label='Also write .glb files', tag='exp_glb')
            dpg.add_checkbox(label='Skip textures', tag='exp_notex')
            dpg.add_checkbox(label='Debug (raw byte arrays instead of base64)', tag='exp_debug')
            self._action('Export models', self._on_export, primary=True)

    def _on_export(self):
        args = ['--export']
        for tag, flag in (('exp_untangle', '--untangle'), ('exp_glb', '--glb'),
                          ('exp_notex', '--notex'), ('exp_debug', '--debug')):
            if dpg.get_value(tag):
                args.append(flag)
        self.run_command(*args)

    def _build_icons_tab(self):
        with dpg.tab(label='Icons'):
            dpg.add_text('Character-select icon atlases.')
            dpg.add_checkbox(label='Read DOL/DAT from 3_Output_Dat instead of 1_Input', tag='ico_use_output')
            self._action('Export icons', self._on_export_icons, primary=True)
            dpg.add_separator()
            dpg.add_checkbox(label='Dry run (validate without writing)', tag='ico_dry')
            self._action('Patch icons', self._on_patch_icons, primary=True)

    def _on_export_icons(self):
        args = ['--export-icons']
        if dpg.get_value('ico_use_output'):
            args.append('--use-output')
        self.run_command(*args)

    def _on_patch_icons(self):
        args = ['--patch-icons']
        if dpg.get_value('ico_dry'):
            args.append('--dry-run')
        self.run_command(*args)

    def _build_roster_tab(self):
        with dpg.tab(label='Roster'):
            dpg.add_text('Inject a roster configuration into 3_Output_Dat.')
            with dpg.group(horizontal=True):
                dpg.add_combo([], tag='roster_config', width=420)
                dpg.add_button(label='Refresh', callback=self._refresh_configs)
            dpg.add_text('', tag='roster_hint', color=_PROMPT_COLOR, wrap=700)
            dpg.add_checkbox(label='Dry run (validate without writing)', tag='roster_dry')
            with dpg.group(horizontal=True):
                self._action('Inject roster', self._on_roster, primary=True)
                self._action('Reset to vanilla', lambda: self.run_command('--roster', '--remove'))
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
                dpg.set_value('full_roster', self.ROSTER_SKIP)

    def _on_roster(self):
        name = dpg.get_value('roster_config')
        if not name:
            self._log_line('No roster configuration selected.', _PROMPT_COLOR)
            return
        args = ['--roster', '--config', os.path.join(self.config_dir, name)]
        if dpg.get_value('roster_dry'):
            args.append('--dry-run')
        self.run_command(*args)

    def _build_patch_tab(self):
        with dpg.tab(label='Patch'):
            dpg.add_text('Patch or unpatch .sluggie and/or .png files.')
            dpg.add_listbox([], tag='patch_files', num_items=6, width=-1)
            self.patch_paths = []
            with dpg.group(horizontal=True):
                dpg.add_button(label='Add files...', callback=lambda: dpg.show_item('patch_dialog'))
                dpg.add_button(label='Clear', callback=self._clear_patch_files)
            with dpg.group(horizontal=True):
                self._action('Patch', lambda: self._on_patch(False), primary=True)
                self._action('Unpatch', lambda: self._on_patch(True))
        with dpg.file_dialog(directory_selector=False, show=False, tag='patch_dialog', width=700, height=420,
                             callback=self._on_files_chosen, default_path=self.models_dir
                             if os.path.isdir(self.models_dir) else self.root_dir):
            dpg.add_file_extension('.sluggie', color=(120, 220, 120, 255))
            dpg.add_file_extension('.png', color=(120, 180, 255, 255))
            dpg.add_file_extension('.*')

    def _on_files_chosen(self, _sender, app_data):
        for path in app_data.get('selections', {}).values():
            if path not in self.patch_paths:
                self.patch_paths.append(path)
        dpg.configure_item('patch_files', items=self.patch_paths)

    def _clear_patch_files(self):
        self.patch_paths = []
        dpg.configure_item('patch_files', items=[])

    def _on_patch(self, unpatch):
        if not self.patch_paths:
            self._log_line('Add at least one file first.', _PROMPT_COLOR)
            return
        self.run_command('--unpatch' if unpatch else '--patch', *self.patch_paths)

    def _build_maintenance_tab(self):
        with dpg.tab(label='Maintenance'):
            self._action('Hammerspace', lambda: self.run_command('--hammerspace'),
                         'Change the available memory space in the output dt_na.dat.')
            self._action('Re-split unused characters', lambda: self.run_command('--resplit-unused'),
                         'Repair: give unused-character routes (dirs 89-94) their own block copies again.')

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
            with dpg.tab_bar():
                self._build_full_tab()
                self._build_export_tab()
                self._build_icons_tab()
                self._build_roster_tab()
                self._build_patch_tab()
                self._build_maintenance_tab()
            dpg.add_separator()
            with dpg.child_window(tag='log_window', height=-34, border=True):
                dpg.add_text('', tag='log_pending', color=_PROMPT_COLOR)
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag='stdin_field', width=-250, hint='Answer to a prompt (y/n, value, ...)',
                                   on_enter=True, callback=self._on_send)
                dpg.add_button(label='Send', callback=self._on_send)
                dpg.add_button(label='y', width=24, callback=lambda: self.send_input('y'))
                dpg.add_button(label='n', width=24, callback=lambda: self.send_input('n'))
                dpg.add_button(label='Enter', callback=lambda: self.send_input(''))
                dpg.add_button(label='Stop', tag='stop_button', enabled=False, callback=self.stop_command)
        dpg.create_viewport(title='Sluggies Tools', width=900, height=700)
        dpg.set_primary_window('main_window', True)
        dpg.setup_dearpygui()
        dpg.show_viewport()

    def run(self):
        self.build()
        while dpg.is_dearpygui_running():
            self._drain_queue()
            dpg.render_dearpygui_frame()
        self.stop_command()
        dpg.destroy_context()


def run_gui(command_prefix, root_dir):
    """Open the window and block until it is closed."""
    SluggiesGui(command_prefix, root_dir).run()
