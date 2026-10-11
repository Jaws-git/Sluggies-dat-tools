"""The GUI's "Music" tab: the game's stream sounds (music, jingles), one flat list.

Every ``STRM`` sound of the sound archive is one row: its name, the ``.brstm`` it streams from, length, channels
and rate (from the stream header only), **Play**, **Stop**, **Replace...** and **Reset**. The output comes first:
the archive is ``3_Output_Dat/sound_NA/MY2.brsar`` when it exists (else ``1_Input``'s), and each track plays from
``3_Output_Dat/sound_NA/stream/<file>`` when that exists, else from ``1_Input/sound_NA/stream``. A track whose
output file differs from the input one shows as replaced.

* **Play** decodes the stream on a worker thread into a temp WAV (kept until the track changes, so a second
  Play starts at once) and plays it through ``winsound``; **Stop** stops every playback, the Sounds tab's clips
  included (winsound plays one sound at a time).
* **Replace...** encodes a WAV (other sound files through ffmpeg, ``Sound/clips``) into the stock stream's layout (``Sound/brstm_write``: same channels, rate and
  blocks; a WAV ``smpl`` loop is kept, else a looping stock track loops from the start) on a worker thread and
  writes ``3_Output_Dat/sound_NA/stream/<file>`` directly; CopyFilesToGameDir.bat ("Patch Game & copy files")
  copies it into the game. **Reset** writes the stock file there again (so the next copy restores the game's).

Elsewhere than Windows the playback buttons are disabled. The list is read once on a worker thread; Refresh
reads it again.
"""

import atexit
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass

import dearpygui.dearpygui as dpg

from Sound import ffmpeg

_DIM = (200, 200, 200, 255)
_FAINT = (140, 140, 140, 255)
_WARN = (255, 210, 90, 255)
_OK = (120, 220, 140, 255)
_ERROR = (255, 120, 110, 255)
_CHANGED = (110, 170, 255, 255)
_WINDOWS = os.name == 'nt'
BUTTON_H = 28
WAV_FILTERS = [('Sound files', ';'.join('*' + e for e in ('.wav', '.wave') + ffmpeg.AUDIO_EXTENSIONS)),
               ('WAV files', '*.wav;*.wave'), ('All files', '*.*')]
WAV_DIR_SETTING = 'music_last_wav_dir'          # the GUI settings key: the folder the last Replace... WAV came from


@dataclass
class Track:
    sound_id: int               # the sound's INFO ID
    name: str
    external: str               # 'stream/xxx.brstm', '' when the sound names no file
    stock: str = None           # the .brstm under 1_Input/sound_NA, or None (missing)
    output: str = None          # the .brstm under 3_Output_Dat/sound_NA, or None (none written)
    replaced: bool = False      # the output file differs from the stock one
    info: object = None         # Sound.brstm.Info of the played file, or None (missing or unreadable)
    problem: str = None         # why the header could not be read

    @property
    def path(self):
        """The file that plays: the output one when there is one."""
        return self.output or self.stock


def _same_file(a, b):
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, 'rb') as fa, open(b, 'rb') as fb:
        return fa.read() == fb.read()


def read_tracks(root):
    """``(tracks, archive path)``: every stream sound of the output archive (else the input one) as a
    ``Track``, in INFO ID order."""
    from Sound import brsar, brstm
    input_dir = os.path.join(root, '1_Input', 'sound_NA')
    output_dir = os.path.join(root, '3_Output_Dat', 'sound_NA')
    archive_path = os.path.join(output_dir, 'MY2.brsar')
    if not os.path.isfile(archive_path):
        archive_path = os.path.join(input_dir, 'MY2.brsar')
    archive = brsar.Brsar.from_file(archive_path)
    files = archive.files()
    tracks = []
    for sound in archive.sounds():
        if sound.type != brsar.STRM:
            continue
        external = files[sound.file_id].external_path.replace('\\', '/')
        track = Track(sound_id=sound.index, name=sound.name, external=external)
        if external:
            stock = os.path.join(input_dir, *external.split('/'))
            output = os.path.join(output_dir, *external.split('/'))
            track.stock = stock if os.path.isfile(stock) else None
            track.output = output if os.path.isfile(output) else None
            track.replaced = track.output is not None and (track.stock is None or not _same_file(output, stock))
        if track.path:
            try:
                with open(track.path, 'rb') as handle:
                    track.info = brstm.info(handle.read(brstm.INFO_BYTES))
            except (OSError, brstm.BrstmError, IndexError, ValueError) as exc:
                track.problem = str(exc)
        tracks.append(track)
    return sorted(tracks, key=lambda t: t.sound_id), archive_path


def _length(info):
    if info is None or not info.rate:
        return ''
    seconds = info.samples / info.rate
    return f'{int(seconds // 60)}:{seconds % 60:04.1f}'


class MusicTab:
    def __init__(self, app):
        self.app = app                       # gui.SluggiesGui
        self.loading = False
        self.tracks = None                   # [Track], once read
        self.archive_path = None             # the archive the list came from
        self.error = None                    # why the list could not be read
        self.working = None                  # the Track being encoded, or None
        self._cache = {}                     # sound_id -> decoded temp WAV
        self._cache_dir = None
        self._request = 0                    # bumped by every Play and Stop: a decode that finishes late is dropped
        self._decoding = 0                   # decodes running
        self._fallback = None                # on_files of the pick the built-in dialog serves

    # ------------------------------------------------------------------ build
    def build(self):
        with dpg.tab(label='Music', tag='music_tab'):
            with dpg.group(horizontal=True):
                dpg.add_button(label='Refresh', tag='music_refresh', callback=lambda: self.refresh())
                dpg.add_button(label='Stop', tag='music_stop_all', enabled=_WINDOWS, callback=lambda: self.stop())
                dpg.add_spacer(width=12)
                dpg.add_loading_indicator(tag='music_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='music_status')
            dpg.add_text('', tag='music_info', color=_DIM, wrap=1200)
            dpg.add_separator()
            with dpg.child_window(tag='music_list', border=True):
                dpg.add_text('Reading...', color=_FAINT)
        start = self.app.models_dir if os.path.isdir(self.app.models_dir) else self.app.root_dir
        with dpg.file_dialog(directory_selector=False, show=False, modal=True, tag='music_wav_dialog', width=760,
                             height=460, callback=self._on_fallback_chosen, cancel_callback=lambda *_: None,
                             default_path=start):
            for ext in ('.wav',) + ffmpeg.AUDIO_EXTENSIONS:
                dpg.add_file_extension(ext, color=(120, 180, 255, 255))
            dpg.add_file_extension('.*')

    def _on_fallback_chosen(self, _sender, app_data):
        app_data = app_data or {}
        paths = list(app_data.get('selections', {}).values()) or [app_data.get('file_path_name')]
        callback, self._fallback = self._fallback, None
        if callback is not None:
            callback([p for p in paths if p])

    def on_open(self):
        """The tab was opened: read the list the first time."""
        if self.tracks is None and not self.loading and self.error is None:
            self.refresh()

    # ------------------------------------------------------------------ reading
    def refresh(self):
        if self.loading or self.working:
            return
        self.loading = True
        dpg.configure_item('music_refresh', enabled=False)
        self._spinner()
        self._status('Reading the music list...', _DIM)
        root = self.app.root_dir

        def work():
            tracks = archive_path = error = None
            try:
                if not any(os.path.isfile(os.path.join(root, top, 'sound_NA', 'MY2.brsar'))
                           for top in ('3_Output_Dat', '1_Input')):
                    error = ('1_Input/sound_NA/MY2.brsar is missing: copy the game\'s files/sound_NA folder '
                             '(MY2.brsar and stream/) into 1_Input/sound_NA.')
                else:
                    tracks, archive_path = read_tracks(root)
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
            self.app.output_queue.put(('call', self._read_done, tracks, archive_path, error))
        threading.Thread(target=work, daemon=True).start()

    def _read_done(self, tracks, archive_path, error):
        self.loading = False
        dpg.configure_item('music_refresh', enabled=True)
        self._spinner()
        self.tracks, self.archive_path, self.error = tracks, archive_path, error
        self._cache.clear()                  # the files may have changed: decode again on the next Play
        if error:
            self._status(error, _ERROR)
        else:
            missing = sum(1 for t in tracks if t.path is None)
            replaced = sum(1 for t in tracks if t.replaced)
            self._status(f'{len(tracks)} music tracks' + (f', {replaced} replaced' if replaced else '')
                         + (f', {missing} stream file(s) missing in 1_Input/sound_NA/stream' if missing else '')
                         + '.', _WARN if missing else _OK)
        self._info_line()
        self._draw_list()

    def _info_line(self):
        source = os.path.relpath(self.archive_path, self.app.root_dir).replace(os.sep, '/') if self.archive_path \
            else 'the sound archive'
        dpg.set_value('music_info', f'The game\'s music and jingles: the stream sounds of {source}, played from '
                      '3_Output_Dat/sound_NA/stream when a file is there, else from 1_Input/sound_NA/stream. '
                      'Replace... encodes a WAV (or MP3, OGG, FLAC, ... when ffmpeg is installed) into 3_Output_Dat/sound_NA/stream ("Patch Game & copy files" copies '
                      'it into the game). Stop stops every playback.')

    def _status(self, text, color):
        dpg.set_value('music_status', text)
        dpg.configure_item('music_status', color=color)

    def _spinner(self):
        dpg.configure_item('music_spinner', show=self.loading or self._decoding > 0 or self.working is not None)

    # ------------------------------------------------------------------ the list
    def _draw_list(self):
        if not dpg.does_item_exist('music_list'):
            return
        dpg.delete_item('music_list', children_only=True)
        parent = 'music_list'
        if not self.tracks:
            dpg.add_text('(not read: see the status line)' if self.error else 'No stream sounds in the archive.',
                         parent=parent, color=_FAINT)
            return
        with dpg.table(parent=parent, header_row=True, borders_innerH=True, resizable=False, row_background=True,
                       policy=dpg.mvTable_SizingStretchProp) as table:
            dpg.add_table_column(label='ID', init_width_or_weight=0.4)
            dpg.add_table_column(label='Track', init_width_or_weight=2.0)
            dpg.add_table_column(label='File', init_width_or_weight=2.6)
            dpg.add_table_column(label='Length', init_width_or_weight=0.7)
            dpg.add_table_column(label='Format', init_width_or_weight=1.4)
            dpg.add_table_column(label='', init_width_or_weight=2.6)
        busy = self.working is not None
        for track in self.tracks:
            info = track.info
            with dpg.table_row(parent=table):
                dpg.add_text(str(track.sound_id), color=_DIM)
                if track is self.working:
                    dpg.add_text(f'{track.name}  (encoding...)', color=_WARN)
                else:
                    dpg.add_text(track.name, color=_CHANGED if track.replaced else None)
                if track.path is None:
                    dpg.add_text(f'{track.external or "(no file)"}  (missing)', color=_WARN)
                elif track.replaced:
                    dpg.add_text(f'{track.external}  (replaced)', color=_CHANGED)
                else:
                    dpg.add_text(track.external, color=_DIM)
                dpg.add_text(_length(info))
                if info is not None:
                    channels = {1: 'mono', 2: 'stereo'}.get(info.channels, f'{info.channels} ch')
                    dpg.add_text(f'{channels}, {info.rate} Hz' + (', loops' if info.loop else ''), color=_DIM)
                else:
                    dpg.add_text(track.problem or '', color=_ERROR if track.problem else _FAINT)
                with dpg.group(horizontal=True, horizontal_spacing=4):
                    play = dpg.add_button(label='Play', width=54, height=BUTTON_H,
                                          enabled=_WINDOWS and track.path is not None and info is not None,
                                          callback=lambda _s, _a, t: self.play(t), user_data=track)
                    dpg.add_button(label='Stop', width=54, height=BUTTON_H, enabled=_WINDOWS,
                                   callback=lambda: self.stop())
                    replace = dpg.add_button(label='Replace...', width=84, height=BUTTON_H,
                                             enabled=not busy and track.path is not None and info is not None,
                                             callback=lambda _s, _a, t: self._on_replace(t), user_data=track)
                    reset = dpg.add_button(label='Reset', width=54, height=BUTTON_H,
                                           enabled=not busy and track.replaced and track.stock is not None,
                                           callback=lambda _s, _a, t: self._on_reset(t), user_data=track)
                    with dpg.tooltip(play):
                        dpg.add_text('Play the track once (decoded the first time, which takes a moment)'
                                     if _WINDOWS else 'Playback needs Windows')
                    with dpg.tooltip(replace):
                        dpg.add_text('Pick a WAV (or MP3, OGG, FLAC, ... with ffmpeg): it is encoded in the stock track\'s format (channels, rate) and '
                                     'written to 3_Output_Dat/sound_NA/stream. A loop in the WAV (smpl chunk) is '
                                     'kept; else a looping stock track loops from the start. Takes a few seconds '
                                     'per minute of music.', wrap=380)
                    with dpg.tooltip(reset):
                        dpg.add_text('Write the stock track to 3_Output_Dat/sound_NA/stream again, so the next '
                                     'copy into the game restores it.', wrap=380)

    def _track(self, sound_id):
        return next((t for t in self.tracks or [] if t.sound_id == sound_id), None)

    # ------------------------------------------------------------------ playback
    def stop(self):
        """Stop every playback (and drop a Play still decoding)."""
        self._request += 1
        if not _WINDOWS:
            return
        import winsound
        try:
            winsound.PlaySound(None, 0)
        except RuntimeError:
            pass
        if self.working is None:
            self._status('Stopped.', _DIM)

    def play(self, track):
        if not _WINDOWS or track.path is None:
            return
        self._request += 1
        request = self._request
        cached = self._cache.get(track.sound_id)
        if cached and os.path.isfile(cached):
            self._start(track, cached)
            return
        self._decoding += 1
        self._spinner()
        if self.working is None:
            self._status(f'Decoding {track.name}...', _DIM)
        if self._cache_dir is None:
            self._cache_dir = tempfile.mkdtemp(prefix='sluggies_music_')
            atexit.register(shutil.rmtree, self._cache_dir, True)
        target = os.path.join(self._cache_dir, f'{track.sound_id}_{request}.wav')
        path = track.path

        def work():
            error = None
            # the decoder holds the GIL in long numpy calls: give the render thread more turns meanwhile
            import sys
            interval = sys.getswitchinterval()
            sys.setswitchinterval(0.0005)
            try:
                from Sound import brstm, wavfile
                with open(path, 'rb') as handle:
                    stream = brstm.parse(handle.read())
                wavfile.write_wav(target, stream.rate, brstm.decode(stream))
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
            finally:
                sys.setswitchinterval(interval)
            self.app.output_queue.put(('call', self._decoded, track, path, target, request, error))
        threading.Thread(target=work, daemon=True).start()

    def _decoded(self, track, path, target, request, error):
        self._decoding -= 1
        self._spinner()
        if error:
            self._status(f'Could not decode {track.name}: {error}', _ERROR)
            self.app.log_line(f'[music] could not decode {path}: {error}', _WARN)
            return
        if path == track.path:               # not replaced meanwhile
            self._cache[track.sound_id] = target
        if request != self._request:         # stopped, or another track asked for meanwhile
            return
        self._start(track, target)

    def _start(self, track, path):
        import winsound
        try:
            winsound.PlaySound(None, 0)
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except RuntimeError as exc:
            self._status(f'Could not play {track.name}: {exc}', _ERROR)
            self.app.log_line(f'[music] could not play {track.name}: {exc}', _WARN)
            return
        if self.working is None:
            self._status(f'Playing {track.name} ({_length(track.info)}).', _OK)

    # ------------------------------------------------------------------ replacing
    def _output_path(self, track):
        return os.path.join(self.app.root_dir, '3_Output_Dat', 'sound_NA', *track.external.split('/'))

    def _on_replace(self, track):
        if self.working is not None or self.app.picking or track.path is None:
            return
        start = next((d for d in (self.app.settings.get(WAV_DIR_SETTING),
                                  os.path.join(self.app.models_dir, '_SOUNDS', 'stream')) if d and os.path.isdir(d)),
                     self.app.models_dir)
        if os.path.isdir(start):
            dpg.configure_item('music_wav_dialog', default_path=start)

        def picked(paths):
            path = paths[0] if paths else None
            if not path or not os.path.isfile(path):
                return
            try:
                self.app.settings.set(WAV_DIR_SETTING, os.path.dirname(os.path.abspath(path)))
            except OSError:
                pass                          # not remembered; the replace itself goes on
            self._encode(track, path)
        self._fallback = picked
        if not self.app.pick_files('music_wav_dialog', f'Replace the music track {track.name}', WAV_FILTERS,
                                   picked, on_cancel=lambda: None, initial_dir=start):
            self._fallback = None

    def _encode(self, track, wav_path):
        self.working = track
        self._spinner()
        self._status(f'Encoding {os.path.basename(wav_path)} for {track.name}...', _DIM)
        self._draw_list()
        layout = track.stock or track.path   # the stock file gives the layout
        target = self._output_path(track)

        def work():
            notes, error, seconds = [], None, None
            import sys
            import time
            interval = sys.getswitchinterval()
            sys.setswitchinterval(0.0005)
            started = time.time()
            try:
                from Sound import brstm_write
                with open(layout, 'rb') as handle:
                    original = handle.read()
                data, notes = brstm_write.encode_wav(wav_path, original)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target + '.tmp', 'wb') as handle:
                    handle.write(data)
                os.replace(target + '.tmp', target)
                seconds = time.time() - started
            except Exception as exc:
                error = str(exc) if isinstance(exc, (ValueError, OSError)) else f'{type(exc).__name__}: {exc}'
            finally:
                sys.setswitchinterval(interval)
            self.app.output_queue.put(('call', self._encoded, track, wav_path, notes, error, seconds))
        threading.Thread(target=work, daemon=True).start()

    def _encoded(self, track, wav_path, notes, error, seconds):
        self.working = None
        self._spinner()
        if error:
            self._status(f'Refused: {error}', _ERROR)
            self.app.log_line(f'[music] {track.name}: {os.path.basename(wav_path)} refused: {error}', _WARN)
            self._draw_list()
            return
        for note in notes:
            self.app.log_line(f'[music] {track.name}: {note}', _DIM)
        self.app.log_line(f'[music] {track.name} replaced by {os.path.basename(wav_path)} '
                          f'({seconds:.1f} s): {os.path.relpath(self._output_path(track), self.app.root_dir)}', _OK)
        self._status(f'{track.name} replaced ({seconds:.1f} s).' + (f' ({"; ".join(notes)})' if notes else ''), _OK)
        self._reread(track)

    def _on_reset(self, track):
        if self.working is not None or track.stock is None:
            return
        target = self._output_path(track)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(track.stock, target)
        except OSError as exc:
            self._status(f'Could not reset {track.name}: {exc}', _ERROR)
            return
        self._status(f'{track.name} reset to the stock track.', _OK)
        self._reread(track)

    def _reread(self, track):
        """After the output file of ``track`` changed: its state, header and decoded cache."""
        from Sound import brstm
        self._cache.pop(track.sound_id, None)
        output = self._output_path(track)
        track.output = output if os.path.isfile(output) else None
        track.replaced = track.output is not None and (track.stock is None or not _same_file(output, track.stock))
        track.info, track.problem = None, None
        try:
            with open(track.path, 'rb') as handle:
                track.info = brstm.info(handle.read(brstm.INFO_BYTES))
        except (OSError, brstm.BrstmError, IndexError, ValueError) as exc:
            track.problem = str(exc)
        self._draw_list()
