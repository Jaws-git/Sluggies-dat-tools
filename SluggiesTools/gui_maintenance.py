"""The GUI's "Maintenance" tab: problems in the working folders, found by ``maintenance.scan``.

**Scan** runs every check in ``maintenance.CHECKS`` on a worker thread (read-only, no child process) and lists
the problems it finds, each with its files (relative to the tools folder) and what to do. On Windows every file
has a **Show** button that opens Explorer on it. The scan changes and deletes nothing.

The **Sound** section at the top says whether ffmpeg is installed (``Sound/ffmpeg``) and, when it isn't
and winget is, offers to install it.

The **Hammerspace** section shows how much of the output ``dt_na.dat`` past the stock end is gaps
(``Hammerspace/Defrag.analyze``, read-only) and offers **Defragment** (``start.py --defrag-hammerspace``, run
through the command runner), which reports the file size before and after.

**Dolphin texture name** encodes an image (picked, or on Windows dropped onto its field from Explorer: ``file_drop``) in the chosen GX format the way the patchers do
(``texture_helper.dolphin_texture_name``: wimgt) and shows the file name Dolphin's texture dump would give it,
in a read-only field with a Copy button. An image already named like a Dolphin dump uses the format in its name.
"""

import os
import platform
import re
import subprocess
import threading

import dearpygui.dearpygui as dpg

import gui_audio
import gui_grid
import maintenance
import texture_helper
from Hammerspace import Defrag
from Sound import ffmpeg

# GX image formats by their wimgt names, and the palette formats indexed ones need (texture_helper)
_FORMATS = {name.removeprefix('TPL.'): code for code, name in texture_helper.WIMGT_IMAGE_TARGETS.items()}
_PALETTES = {name.removeprefix('P-'): code for code, name in texture_helper.WIMGT_PALETTE_TOKENS.items()}
_INDEXED = (0x8, 0x9, 0xA)
DOLPHIN_DIR_SETTING = 'maintenance_last_dolphin_image_dir'   # the GUI settings key: the folder the last image came from

_DIM = (170, 170, 170, 255)
_WARN = (255, 210, 90, 255)
_OK = (120, 220, 140, 255)
_ERROR = (255, 120, 110, 255)
_WINDOWS = os.name == 'nt'
_MOVED = re.compile(r'moved (\d+) block')


def _mb(n):
    return f'{n / (1024 * 1024):,.2f} MB'


class MaintenanceTab:
    def __init__(self, app):
        self.app = app                       # gui.SluggiesGui
        self.scanning = False
        self.dat_size_before = None          # dt_na.dat size when Defragment started
        self.picks = gui_audio.FilePicks(app)

    def build(self):
        with dpg.tab(label='Maintenance', tag='maintenance_tab'):
            dpg.add_text('Sound')
            with dpg.group(horizontal=True):
                dpg.add_text('ffmpeg: checking...', tag='maintenance_ffmpeg', color=_DIM)
                dpg.add_button(label='Install ffmpeg (winget)', tag='maintenance_ffmpeg_install', show=False,
                               callback=lambda: self.install_ffmpeg())
            dpg.add_text('ffmpeg lets the sound tools read files that aren\'t WAVs (MP3, OGG, FLAC, ...). '
                         'Installing runs "winget install Gyan.FFmpeg"; Windows may ask for permission.',
                         color=_DIM, wrap=1300)
            dpg.add_separator()
            dpg.add_text('Hammerspace')
            dpg.add_text('', tag='maintenance_hs', color=_DIM, wrap=1300)
            with dpg.group(horizontal=True):
                button = dpg.add_button(label='Defragment', tag='maintenance_defrag', width=170,
                                        callback=lambda: self.confirm_defrag())
                self.app.action_buttons.append(button)
                dpg.add_button(label='Refresh', callback=lambda: self.check_hammerspace())
            dpg.add_text('', tag='maintenance_defrag_report', wrap=1300)
            dpg.add_separator()
            self._build_dolphin_name()
            dpg.add_separator()
            dpg.add_text('Checks the working folders for problems that make the tools act on the wrong file, and '
                         'the output game for players and stadiums too big for the game\'s memory. Also shows whether the output '
                         'game still works with Gecko codes made for the stock game.', wrap=900)
            dpg.add_text(f'Checks: {", ".join(title for title, _check in maintenance.CHECKS)}.', color=_DIM)
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_button(label='Scan', tag='maintenance_scan', width=170, height=44,
                               callback=lambda: self.scan())
                dpg.bind_item_theme('maintenance_scan', 'primary_theme')
                dpg.add_text('', tag='maintenance_status')
            dpg.add_separator()
            dpg.add_child_window(tag='maintenance_results', border=False, height=-1)
        self.picks.add_dialog('maintenance_dolphin_dialog', gui_grid.IMAGE_EXTENSIONS)
        self.check_ffmpeg()
        self.check_hammerspace()

    # ------------------------------------------------------------------------------------------------ ffmpeg

    def check_ffmpeg(self):
        """Look for ffmpeg on a worker thread (running ``ffmpeg -version`` takes a moment)."""
        def work():
            path = ffmpeg.find()
            found = (path, ffmpeg.version(path)) if path else None
            self.app.output_queue.put(('call', self._ffmpeg_checked, found, ffmpeg.winget() is not None))
        threading.Thread(target=work, daemon=True).start()

    def _ffmpeg_checked(self, found, has_winget):
        if found is not None:
            path, version = found
            self._ffmpeg_status(f'ffmpeg: found, {version or "version unknown"} ({path})', _OK)
            dpg.configure_item('maintenance_ffmpeg_install', show=False)
        elif has_winget:
            self._ffmpeg_status('ffmpeg: not found.', _WARN)
            dpg.configure_item('maintenance_ffmpeg_install', show=True, enabled=True)
        else:
            self._ffmpeg_status('ffmpeg: not found, and winget isn\'t available to install it (get "App Installer" '
                                'from the Microsoft Store, or install ffmpeg by hand and put it on PATH).', _WARN)
            dpg.configure_item('maintenance_ffmpeg_install', show=False)

    def install_ffmpeg(self):
        dpg.configure_item('maintenance_ffmpeg_install', enabled=False)
        self._ffmpeg_status('ffmpeg: installing with winget (this can take a minute)...', _DIM)
        self.app.log_line('Installing ffmpeg: winget install Gyan.FFmpeg')

        def work():
            ok, output = ffmpeg.winget_install()
            self.app.output_queue.put(('call', self._ffmpeg_installed, ok, output))
        threading.Thread(target=work, daemon=True).start()

    def _ffmpeg_installed(self, ok, output):
        # winget redraws progress bars with \r; log only the lines that settled
        lines = [line.rsplit('\r', 1)[-1].strip() for line in output.splitlines()]
        for line in lines:
            if line and not set(line) <= set('-\\|/ █▒'):
                self.app.log_line('  ' + line)
        if not ok:
            self.app.log_line('winget did not finish the install (see above).')
        self.check_ffmpeg()

    def _ffmpeg_status(self, text, color):
        dpg.set_value('maintenance_ffmpeg', text)
        dpg.configure_item('maintenance_ffmpeg', color=color)

    # ------------------------------------------------------------------------------------------------ hammerspace

    def _dat_path(self):
        return os.path.join(self.app.root_dir, maintenance.OUTPUT_FOLDER, 'dt_na.dat')

    def check_hammerspace(self):
        """Measure the gaps on a worker thread (reads the whole hammerspace once)."""
        self._hs_status('Hammerspace: checking...', _DIM)

        def work():
            try:
                result, error = (Defrag.analyze(), Defrag.backup_pending()), None
            except Exception as exc:
                result, error = None, f'{type(exc).__name__}: {exc}'
            self.app.output_queue.put(('call', self._hammerspace_checked, result, error))
        threading.Thread(target=work, daemon=True).start()

    def _hammerspace_checked(self, result, error):
        if error is not None:
            self._hs_status(f'Hammerspace: could not be read ({error}).', _WARN)
            return
        layout, pending = result
        if pending:
            self._hs_status('An interrupted defragmentation left a backup: Defragment puts the files back from it '
                            'first.', _WARN)
            return
        text = (f'dt_na.dat {_mb(layout.dat_size)}: {len(layout.extents)} hammerspace block(s), '
                f'{_mb(layout.live_bytes)} in use of {_mb(layout.hammerspace_bytes)}')
        if layout.leftover_bytes:
            text += f', {_mb(layout.leftover_bytes)} unused leftovers'
        if layout.compact:
            self._hs_status(text + '. Already compact.', _OK)
        else:
            self._hs_status(f'{text}. Defragmenting moves {len(layout.moved)} block(s) and saves '
                            f'{_mb(layout.saved)}.', _WARN if layout.saved >= 1024 * 1024 else _DIM)

    def _hs_status(self, text, color):
        dpg.set_value('maintenance_hs', text)
        dpg.configure_item('maintenance_hs', color=color)

    def confirm_defrag(self):
        if self.app.busy:
            return
        self.app.ask_text(
            'Defragment hammerspace?',
            'Moves the hammerspace blocks of 3_Output_Dat/dt_na.dat together, repoints main.dol and shrinks the '
            'file. A backup is kept until every block checks out, and put back if anything fails. Copy '
            'dt_na.dat, main.dol and fst.bin to the game together afterwards.',
            lambda _answer: self.defrag(), lambda: None, kind='yesno')

    def defrag(self):
        path = self._dat_path()
        self.dat_size_before = os.path.getsize(path) if os.path.isfile(path) else None
        self._report('', _DIM)
        self.app.run_chain([('--defrag-hammerspace',)], on_done=self._defrag_done)

    def _defrag_done(self, code, output):
        path = self._dat_path()
        before, after = self.dat_size_before, os.path.getsize(path) if os.path.isfile(path) else None
        if code != 0:
            self._report('Defragmentation failed or was refused; the files are as they were (see the log).', _ERROR)
        elif Defrag.RESTORED_TEXT in output:
            self._report('An interrupted defragmentation was undone: the files are back as they were before it. '
                         'Click Defragment again.', _WARN)
        elif before is None or after is None:
            self._report('Defragmentation finished (see the log).', _OK)
        elif before == after and Defrag.COMPACT_TEXT in output:
            self._report(f'Already compact: dt_na.dat stays {before:,} bytes ({_mb(before)}).', _OK)
        else:
            moved = _MOVED.findall(output)
            self._report('\n'.join((
                f'Defragmented{f", {moved[-1]} block(s) moved" if moved else ""}.',
                f'  Before: {before:,} bytes ({_mb(before)})',
                f'  After:  {after:,} bytes ({_mb(after)})',
                f'  Saved:  {before - after:,} bytes ({_mb(before - after)})',
                'Copy dt_na.dat, main.dol and fst.bin to the game together.')), _OK)
        self.check_hammerspace()

    def _report(self, text, color):
        dpg.set_value('maintenance_defrag_report', text)
        dpg.configure_item('maintenance_defrag_report', color=color)

    # ------------------------------------------------------------------------------------------------ dolphin name

    def _build_dolphin_name(self):
        dpg.add_text('Dolphin texture name')
        with dpg.group(horizontal=True):
            if platform.system() == 'Windows':
                # also the drop field: an image dropped onto it from Explorer is used (on_files_dropped)
                dpg.add_button(label='Drop an image here, or click to pick one', tag='maintenance_dolphin',
                               width=400, height=60, callback=lambda: self.pick_dolphin_image())
            else:                            # file_drop is Windows-only: a plain picker button
                dpg.add_button(label='Calculate Dolphin texture name', tag='maintenance_dolphin',
                               callback=lambda: self.pick_dolphin_image())
            dpg.add_text('Format:')
            dpg.add_combo(list(_FORMATS), default_value='CMPR', tag='maintenance_dolphin_format', width=100,
                          callback=lambda: self._dolphin_palette_shown())
            dpg.add_text('Palette:', tag='maintenance_dolphin_palette_label', show=False)
            dpg.add_combo(list(_PALETTES), default_value='RGB5A3', tag='maintenance_dolphin_palette', width=100,
                          show=False)
        dpg.add_text('Encodes an image the way the patchers write it into the game (wimgt, in the format chosen '
                     'here) and gives the name Dolphin\'s texture dump would give it, e.g. for a texture pack. '
                     'A file already named like a Dolphin dump keeps the format in its name.',
                     color=_DIM, wrap=1300)
        with dpg.group(horizontal=True):
            dpg.add_input_text(tag='maintenance_dolphin_name', readonly=True, width=640, show=False)
            dpg.add_button(label='Copy', tag='maintenance_dolphin_copy', show=False,
                           callback=lambda: dpg.set_clipboard_text(dpg.get_value('maintenance_dolphin_name')))
        dpg.add_text('', tag='maintenance_dolphin_status', color=_DIM, wrap=1300)

    def _dolphin_palette_shown(self):
        indexed = _FORMATS[dpg.get_value('maintenance_dolphin_format')] in _INDEXED
        dpg.configure_item('maintenance_dolphin_palette_label', show=indexed)
        dpg.configure_item('maintenance_dolphin_palette', show=indexed)

    def pick_dolphin_image(self):
        self.picks.pick_one('maintenance_dolphin_dialog', 'Select an image to name for Dolphin',
                            gui_grid.IMAGE_FILTERS, DOLPHIN_DIR_SETTING, (), self._on_dolphin_path)

    def on_files_dropped(self, paths, point):
        """Files dropped onto the window (``file_drop``) at client ``point``: on the drop field, name the image."""
        tab_bar = dpg.get_value('main_tab_bar')
        if tab_bar not in ('maintenance_tab', dpg.get_alias_id('maintenance_tab')):
            return
        (left, top), (right, bottom) = (dpg.get_item_rect_min('maintenance_dolphin'),
                                        dpg.get_item_rect_max('maintenance_dolphin'))
        if not (left <= point[0] <= right and top <= point[1] <= bottom):
            return
        if not dpg.get_item_configuration('maintenance_dolphin')['enabled']:
            return                           # still encoding the last one
        images = [path for path in paths if path.lower().endswith(gui_grid.IMAGE_EXTENSIONS)]
        if not images:
            self._dolphin_status(f'{os.path.basename(paths[0])} is not an image '
                                 f'({", ".join(gui_grid.IMAGE_EXTENSIONS)}).', _ERROR)
            return
        if len(paths) > 1:
            self.app.log_line(f'[maintenance] {len(paths)} files dropped; naming the first image only.')
        self._on_dolphin_path(images[0])

    def _on_dolphin_path(self, path):
        gx_format = texture_helper.gx_format_from_dolphin_name(path)
        from_name = gx_format is not None
        if not from_name:
            gx_format = _FORMATS[dpg.get_value('maintenance_dolphin_format')]
        palette = _PALETTES[dpg.get_value('maintenance_dolphin_palette')] if gx_format in _INDEXED else None
        format_name = next(name for name, code in _FORMATS.items() if code == gx_format)
        if palette is not None:
            format_name += f' ({dpg.get_value("maintenance_dolphin_palette")} palette)'
        dpg.configure_item('maintenance_dolphin', enabled=False)
        self._dolphin_status(f'Encoding {os.path.basename(path)} as {format_name}...', _DIM)

        def work():
            try:
                result, error = texture_helper.dolphin_texture_name(path, gx_format, palette), None
            except Exception as exc:
                result, error = None, f'{type(exc).__name__}: {exc}'
            self.app.output_queue.put(('call', self._dolphin_done, path, format_name, from_name, result, error))
        threading.Thread(target=work, daemon=True).start()

    def _dolphin_done(self, path, format_name, from_name, name, error):
        dpg.configure_item('maintenance_dolphin', enabled=True)
        shown = error is None
        dpg.configure_item('maintenance_dolphin_name', show=shown)
        dpg.configure_item('maintenance_dolphin_copy', show=shown)
        if error is not None:
            self._dolphin_status(f'{os.path.basename(path)}: could not be encoded ({error}).', _ERROR)
            return
        dpg.set_value('maintenance_dolphin_name', name)
        source = 'the format in its file name' if from_name else 'the format chosen above'
        self._dolphin_status(f'{os.path.basename(path)} as {format_name} ({source}).', _OK)

    def _dolphin_status(self, text, color):
        dpg.set_value('maintenance_dolphin_status', text)
        dpg.configure_item('maintenance_dolphin_status', color=color)

    # ------------------------------------------------------------------------------------------------ scan

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
            try:
                gecko = maintenance.gecko_status(self.app.root_dir)
            except Exception as exc:         # noqa: BLE001 - shown as the line's text
                gecko = f'{type(exc).__name__}: {exc}'
            self.app.output_queue.put(('call', self._scan_done, result, error, gecko))
        threading.Thread(target=work, daemon=True).start()

    def _scan_done(self, result, error, gecko=None):
        self.scanning = False
        dpg.configure_item('maintenance_scan', enabled=True)
        dpg.delete_item('maintenance_results', children_only=True)
        self._add_gecko(gecko)
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

    def _add_gecko(self, gecko):
        """The first result line: whether the output game still works with stock-game Gecko codes."""
        if gecko is None:
            text, color = 'Gecko codes: no 3_Output_Dat/main.dol yet.', _DIM
        elif isinstance(gecko, str):
            text, color = f'Gecko codes: the check failed ({gecko})', _ERROR
        elif gecko.compatible:
            text, color = f'Gecko codes: compatible ({gecko.detail}).', _OK
        else:
            text, color = (f'Gecko codes: not compatible ({gecko.detail}). Gecko codes made for the stock game (e.g. '
                           'CPU vs CPU) may not work with these files.', _WARN)
        dpg.add_text(text, parent='maintenance_results', color=color, wrap=1300)
        dpg.add_spacer(height=6, parent='maintenance_results')

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
