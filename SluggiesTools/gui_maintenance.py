"""The GUI's "Maintenance" tab: problems in the working folders, found by ``maintenance.scan``.

**Scan** runs every check in ``maintenance.CHECKS`` on a worker thread (read-only, no child process) and lists
the problems it finds, each with its files (relative to the tools folder) and what to do. On Windows every file
has a **Show** button that opens Explorer on it. Nothing is changed or deleted from here.
"""

import os
import subprocess
import threading

import dearpygui.dearpygui as dpg

import maintenance

_DIM = (170, 170, 170, 255)
_WARN = (255, 210, 90, 255)
_OK = (120, 220, 140, 255)
_ERROR = (255, 120, 110, 255)
_WINDOWS = os.name == 'nt'


class MaintenanceTab:
    def __init__(self, app):
        self.app = app                       # gui.SluggiesGui
        self.scanning = False

    def build(self):
        with dpg.tab(label='Maintenance', tag='maintenance_tab'):
            dpg.add_text('Checks the working folders for problems that make the tools act on the wrong file.',
                         wrap=900)
            dpg.add_text(f'Checks: {", ".join(title for title, _check in maintenance.CHECKS)}.', color=_DIM)
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_button(label='Scan', tag='maintenance_scan', width=170, height=44,
                               callback=lambda: self.scan())
                dpg.bind_item_theme('maintenance_scan', 'primary_theme')
                dpg.add_text('', tag='maintenance_status')
            dpg.add_separator()
            dpg.add_child_window(tag='maintenance_results', border=False, height=-1)

    def scan(self):
        if self.scanning:
            return
        self.scanning = True
        dpg.configure_item('maintenance_scan', enabled=False)
        dpg.set_value('maintenance_status', 'Scanning...')
        dpg.configure_item('maintenance_status', color=_DIM)

        def work():
            try:
                result, error = maintenance.scan(self.app.root_dir), None
            except Exception as exc:
                result, error = None, f'{type(exc).__name__}: {exc}'
            self.app.output_queue.put(('call', self._scan_done, result, error))
        threading.Thread(target=work, daemon=True).start()

    def _scan_done(self, result, error):
        self.scanning = False
        dpg.configure_item('maintenance_scan', enabled=True)
        dpg.delete_item('maintenance_results', children_only=True)
        if error is not None:
            self._status(f'The scan failed: {error}', _ERROR)
            return
        problems, failed = result
        for title, message in failed:
            dpg.add_text(f'{title}: the check failed ({message})', parent='maintenance_results', color=_ERROR,
                         wrap=1300)
        if not problems:
            self._status('No problems found.' if not failed else 'No problems found by the checks that ran.',
                         _OK if not failed else _WARN)
            return
        self._status(f'{len(problems)} problem{"s" if len(problems) != 1 else ""} found.', _WARN)
        for problem in problems:
            self._add_problem(problem)

    def _status(self, text, color):
        dpg.set_value('maintenance_status', text)
        dpg.configure_item('maintenance_status', color=color)

    def _add_problem(self, problem):
        parent = 'maintenance_results'
        dpg.add_text(f'{problem.check}: {problem.summary}', parent=parent, color=_WARN, wrap=1300)
        for path in problem.paths:
            with dpg.group(horizontal=True, parent=parent):
                if _WINDOWS:
                    dpg.add_button(label='Show', small=True, user_data=path,
                                   callback=lambda _s, _a, target: self._show(target))
                dpg.add_text('  ' + self._relative(path))
        dpg.add_text(problem.advice, parent=parent, color=_DIM, wrap=1300)
        dpg.add_spacer(height=6, parent=parent)

    def _relative(self, path):
        try:
            return os.path.relpath(path, self.app.root_dir)
        except ValueError:                   # another drive (Windows)
            return path

    def _show(self, path):
        """Open Explorer with the file selected (its folder when the file is gone)."""
        try:
            if os.path.exists(path):
                subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
            else:
                os.startfile(os.path.dirname(path))
        except OSError as exc:
            self.app.log_line(f'Could not open Explorer: {exc}')
