"""The GUI's "Sound FX" tab: the game's sound effects (the archive's wave sounds that are not voices), one list.

Every effect sound of ``1_Input/sound_NA/MY2.brsar`` (``Sound/effects.list_effects``) is one row: its INFO ID,
name, archive group, length, format and wave size, **Play**, **Replace...** and **Reset**; a filter box
narrows the list by name or group. Replaced sounds come from the library ``Sound_FX/`` (``effects.json`` and one
``.rwav`` per sound) and show blue.

* **Play** decodes the wave the game gets (the library's when replaced, else the stock one) and plays it through
  ``winsound``; **Play stock** (replaced rows) the stock one. The effects are short (about 2.5 s at most), so
  there is no Stop button; a Play stops what still plays (winsound plays one sound at a time).
* **Replace...** encodes a WAV (MP3, OGG, FLAC, ... through ffmpeg) in the stock wave's shape (``effects.encode``:
  its channels, rate and loop) on a worker thread, stores it in the library and writes
  ``3_Output_Dat/sound_NA/MY2.brsar`` right away (``effects.sync_output``); "Patch Game" keeps it (the roster
  runner applies the library on every run). **Reset** drops it from the library and puts the stock wave back.
* **Replace gain** (the toolbar, dB) scales the file Replace... encodes (``gui_volume``).
* **Set volume** sets the sound's volume byte (``gui_volume``; written right away, no staging).

Elsewhere than Windows the playback buttons are disabled. The list is read once on a worker thread; Refresh
reads it again.
"""

import os
import threading

import dearpygui.dearpygui as dpg

import gui_audio
import gui_volume

_DIM = (200, 200, 200, 255)
_FAINT = (140, 140, 140, 255)
_WARN = (255, 210, 90, 255)
_OK = (120, 220, 140, 255)
_ERROR = (255, 120, 110, 255)
_CHANGED = (110, 170, 255, 255)
_WINDOWS = os.name == 'nt'
BUTTON_H = 26
WAV_DIR_SETTING = 'effects_last_wav_dir'       # the GUI settings key: the folder the last Replace... file came from


def _effects():
    from Sound import effects
    return effects


class EffectsTab:
    def __init__(self, app):
        self.app = app                       # gui.SluggiesGui
        self.loading = False
        self.archive = None                  # Sound.brsar.Brsar of 1_Input/sound_NA/MY2.brsar, once read
        self.effects = None                  # [effects.Effect], once read
        self.library = {}                    # {sound name: effects.Entry}
        self.volumes = {}                    # {sound name: (volume in the game, stock volume)}
        self.error = None                    # why the list could not be read
        self.working = None                  # the name of the sound being encoded / written, or None
        self.player = gui_audio.ClipPlayer('sluggies_fx_')
        self.picks = gui_audio.FilePicks(app)

    # ------------------------------------------------------------------ build
    def build(self):
        with dpg.tab(label='Sound FX', tag='effects_tab'):
            with dpg.group(horizontal=True):
                dpg.add_button(label='Refresh', tag='effects_refresh', callback=lambda: self.refresh())
                dpg.add_input_text(tag='effects_filter', hint='Filter by name or group', width=260,
                                   callback=lambda: self._draw_list())
                dpg.add_spacer(width=12)
                gui_volume.add_gain_field('effects_gain')
                dpg.add_spacer(width=12)
                dpg.add_loading_indicator(tag='effects_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='effects_status')
            dpg.add_text('The game\'s sound effects: the wave sounds of the sound archive outside the voice banks '
                         '(bat and ball, menus, stadiums, minigames). Replace... encodes a WAV (or MP3, OGG, FLAC, ... '
                         'when ffmpeg is installed) in the stock sound\'s channels, rate and loop, keeps it in '
                         'Sound_FX/ and writes 3_Output_Dat/sound_NA/MY2.brsar ("Patch Game & copy files" copies it '
                         'into the game; "Patch Game" keeps the replacements). A sound\'s group loads into sound '
                         'memory whole: keep replacements near the stock length. The sequence sounds (built from '
                         'instrument banks, e.g. bb_hit_m) are not listed.', color=_DIM, wrap=1200)
            dpg.add_separator()
            with dpg.child_window(tag='effects_list', border=True):
                dpg.add_text('Reading...', color=_FAINT)
        self.picks.add_dialog('effects_wav_dialog', gui_audio.sound_extensions())

    def on_open(self):
        """The tab was opened: read the list the first time."""
        if self.effects is None and not self.loading and self.error is None:
            self.refresh()

    # ------------------------------------------------------------------ reading
    def refresh(self):
        if self.loading or self.working:
            return
        self.loading = True
        dpg.configure_item('effects_refresh', enabled=False)
        self._spinner()
        self._status('Reading the sound effects...', _DIM)
        root = self.app.root_dir

        def work():
            archive = found = library = error = None
            game_volumes = gui_volume.read_volumes(root)
            try:
                effects = _effects()
                from Sound import brsar
                path, _output = brsar.archive_paths(root)
                if not os.path.isfile(path):
                    error = gui_audio.MISSING_ARCHIVE
                else:
                    archive = brsar.Brsar.from_file(path)
                    found = effects.list_effects(archive)
                    library = effects.load_library(root)
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
            self.app.output_queue.put(('call', self._read_done, archive, found, library, error, game_volumes))
        threading.Thread(target=work, daemon=True).start()

    def _read_done(self, archive, found, library, error, game_volumes):
        self.loading = False
        self.volumes = game_volumes
        dpg.configure_item('effects_refresh', enabled=True)
        self._spinner()
        self.archive, self.effects, self.library, self.error = archive, found, library or {}, error
        if error:
            self._status(error, _ERROR)
        else:
            self._summary()
        self._draw_list()

    def _summary(self):
        known = {e.name for e in self.effects or []}
        replaced = sum(1 for name in self.library if name in known)
        unknown = len(self.library) - replaced
        self._status(f'{len(self.effects)} sound effects' + (f', {replaced} replaced' if replaced else '')
                     + (f'; {unknown} library entr{"ies" if unknown != 1 else "y"} name no sound of this archive'
                        if unknown else '') + '.', _WARN if unknown else _OK)

    def _status(self, text, color):
        dpg.set_value('effects_status', text)
        dpg.configure_item('effects_status', color=color)

    def _spinner(self):
        dpg.configure_item('effects_spinner', show=self.loading or self.working is not None)

    # ------------------------------------------------------------------ the list
    def _draw_list(self):
        if not dpg.does_item_exist('effects_list'):
            return
        dpg.delete_item('effects_list', children_only=True)
        parent = 'effects_list'
        if not self.effects:
            dpg.add_text('(not read: see the status line)' if self.error else 'No sound effects in the archive.',
                         parent=parent, color=_FAINT)
            return
        needle = (dpg.get_value('effects_filter') or '').strip().lower()
        shown = [e for e in self.effects if not needle or needle in e.name.lower() or needle in e.group.lower()]
        if not shown:
            dpg.add_text('No sound effect matches the filter.', parent=parent, color=_FAINT)
            return
        with dpg.table(parent=parent, header_row=True, borders_innerH=True, resizable=False, row_background=True,
                       policy=dpg.mvTable_SizingStretchProp) as table:
            dpg.add_table_column(label='ID', init_width_or_weight=0.4)
            dpg.add_table_column(label='Sound', init_width_or_weight=2.6)
            dpg.add_table_column(label='Group', init_width_or_weight=1.1)
            dpg.add_table_column(label='Length', init_width_or_weight=0.6)
            dpg.add_table_column(label='Format', init_width_or_weight=1.5)
            dpg.add_table_column(label='Volume', init_width_or_weight=0.8)
            dpg.add_table_column(label='', init_width_or_weight=3.8)
        busy = self.working is not None
        for effect in shown:
            entry = self.library.get(effect.name)
            with dpg.table_row(parent=table):
                dpg.add_text(str(effect.sound_id), color=_DIM)
                if effect.name == self.working:
                    dpg.add_text(f'{effect.name}  (encoding...)', color=_WARN)
                elif entry is not None:
                    origin = os.path.basename(entry.origin) if entry.origin else entry.rwav
                    seconds = f', {entry.seconds:.2f} s' if entry.seconds is not None else ''
                    dpg.add_text(f'{effect.name}  <- {origin}{seconds}', color=_CHANGED)
                else:
                    dpg.add_text(effect.name)
                dpg.add_text(effect.group.removeprefix('group_'), color=_DIM)
                dpg.add_text(f'{effect.seconds:.2f} s')
                channels = {1: 'mono', 2: 'stereo'}.get(effect.channels, f'{effect.channels} ch')
                dpg.add_text(f'{channels}, {effect.rate} Hz' + (', loops' if effect.loop else '')
                             + (f', {effect.wave_bytes // 1024 or 1} KB'), color=_DIM)
                gui_volume.add_volume_cell(self.volumes, effect.name)
                with dpg.group(horizontal=True, horizontal_spacing=4):
                    dpg.add_button(label='Play', width=50, height=BUTTON_H, enabled=_WINDOWS,
                                   callback=lambda _s, _a, e: self.play(e), user_data=effect)
                    dpg.add_button(label='Replace...', width=84, height=BUTTON_H, enabled=not busy,
                                   callback=lambda _s, _a, e: self._on_replace(e), user_data=effect)
                    dpg.add_button(label='Reset', width=54, height=BUTTON_H, enabled=not busy and entry is not None,
                                   callback=lambda _s, _a, e: self._on_reset(e), user_data=effect)
                    volume = dpg.add_button(label='Set volume', width=84, height=BUTTON_H, enabled=not busy,
                                            callback=lambda _s, _a, e: self._on_volume(e), user_data=effect)
                    with dpg.tooltip(volume):
                        dpg.add_text(gui_volume.TOOLTIP, wrap=360)
                    if entry is not None:
                        dpg.add_button(label='Play stock', width=84, height=BUTTON_H, enabled=_WINDOWS,
                                       callback=lambda _s, _a, e: self.play(e, stock=True), user_data=effect)

    # ------------------------------------------------------------------ playback
    def _wave(self, effect, stock=False):
        """The RWAV bytes the game plays for ``effect`` (the library's when replaced), or the stock one."""
        effects = _effects()
        entry = None if stock else self.library.get(effect.name)
        if entry is not None:
            with open(effects.entry_path(self.app.root_dir, entry), 'rb') as handle:
                return handle.read()
        from Sound import brsar_write
        return brsar_write.sound_rwav(self.archive, effect.sound_id)

    def play(self, effect, stock=False):
        if not _WINDOWS or self.archive is None:
            return
        from Sound import clips
        try:
            self.player.play(clips.rwav_wav_bytes(self._wave(effect, stock)))
        except Exception as exc:
            self._status(f'Could not play {effect.name}: {exc}', _ERROR)
            self.app.log_line(f'[sound fx] could not play {effect.name}: {exc}', _WARN)
            return
        if self.working is None:
            what = 'the stock ' if stock else ''
            self._status(f'Playing {what}{effect.name}' + (' (it loops in the game)' if effect.loop else '') + '.', _OK)

    # ------------------------------------------------------------------ replacing
    def _on_replace(self, effect):
        if self.working is not None or self.app.picking:
            return
        if self.app.busy:
            self._status('A command is running: try again when it is done.', _WARN)
            return
        sounds = os.path.join(self.app.models_dir, '_SOUNDS')
        self.picks.pick_sound('effects_wav_dialog', f'Replace the sound effect {effect.name}', WAV_DIR_SETTING,
                              (os.path.join(sounds, effect.group), sounds), lambda path: self._encode(effect, path))

    def _run(self, effect, message, job):
        """Run ``job()`` -> ``(notes, done message)`` on a worker thread, then write the output archive."""
        self.working = effect.name
        self._spinner()
        self._status(message, _DIM)
        self._draw_list()
        root = self.app.root_dir

        def work():
            notes, done, error, problems = [], None, None, []
            try:
                with gui_audio.render_turns():
                    notes, done = job()
                    _changed, problems = _effects().sync_output(root)
            except Exception as exc:
                error = str(exc) if isinstance(exc, (ValueError, OSError)) else f'{type(exc).__name__}: {exc}'
            library = None
            try:
                library = _effects().load_library(root)
            except Exception:
                pass
            self.app.output_queue.put(('call', self._ran, effect, notes, done, error, problems, library))
        threading.Thread(target=work, daemon=True).start()

    def _ran(self, effect, notes, done, error, problems, library):
        self.working = None
        self._spinner()
        if library is not None:
            self.library = library
        for problem in problems:
            self.app.log_line(f'[sound fx] {problem}', _WARN)
        if error:
            self._status(f'Refused: {error}', _ERROR)
            self.app.log_line(f'[sound fx] {effect.name}: {error}', _WARN)
        else:
            for note in notes:
                self.app.log_line(f'[sound fx] {effect.name}: {note}', _DIM)
            self.app.log_line(f'[sound fx] {done}', _OK)
            self._status(done + (f' ({"; ".join(notes)})' if notes else ''), _OK)
        self._draw_list()

    def _encode(self, effect, path):
        effects = _effects()
        root = self.app.root_dir
        stock = self._wave(effect, stock=True)
        gain = gui_volume.gain_of('effects_gain')

        def job():
            rwav, notes = effects.encode(path, stock, gain)
            effects.set_effect(root, effect.name, rwav, origin=path)
            return notes, (f'{effect.name} replaced by {os.path.basename(path)}{gui_volume.gain_label(gain)}: '
                           '3_Output_Dat/sound_NA/MY2.brsar written.')
        self._run(effect, f'Encoding {os.path.basename(path)} for {effect.name}...', job)

    def _on_reset(self, effect):
        if self.working is not None:
            return
        if self.app.busy:
            self._status('A command is running: try again when it is done.', _WARN)
            return
        effects = _effects()
        root = self.app.root_dir

        def job():
            effects.remove_effect(root, effect.name)
            return [], f'{effect.name} reset to the stock sound: 3_Output_Dat/sound_NA/MY2.brsar written.'
        self._run(effect, f'Resetting {effect.name}...', job)

    def _on_volume(self, effect):
        if self.working is not None:
            return
        def done(volume, stock):
            self.volumes[effect.name] = (volume, stock)
            self._draw_list()
        gui_volume.ask_volume(self.app, effect.name, self._status, 'sound fx', done)
