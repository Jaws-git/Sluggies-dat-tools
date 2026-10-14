"""What the Sound FX, Voices and Music tabs share besides ``gui_volume``: the sound-file pickers, clip playback and
the worker-thread GIL helper.

* ``FilePicks``: a tab's built-in file dialogs (used when the Windows one is off, ``gui.pick_files``); their
  result goes to the callback the last pick stored. ``pick_sound`` picks one sound file, starting where the last
  one came from (a GUI settings key per tab).
* ``ClipPlayer``: ``winsound`` cannot play asynchronously from memory, so a WAV goes through one reused temp file.
* ``render_turns``: for a worker thread running long numpy loops that hold the GIL.
"""

import contextlib
import os
import sys
import tempfile

import dearpygui.dearpygui as dpg

from Sound import ffmpeg

MISSING_ARCHIVE = ('1_Input/sound_NA/MY2.brsar is missing: copy the game\'s files/sound_NA folder '
                   '(MY2.brsar and stream/) into 1_Input/sound_NA.')
_EXTENSION_COLOR = (120, 180, 255, 255)


def sound_extensions(rwav=False):
    """The file extensions a Replace... takes: WAV (an encoded ``.rwav`` too when ``rwav``) and what ffmpeg reads."""
    from Sound import clips
    return clips.WAV_EXTENSIONS + ((clips.RWAV_EXTENSION,) if rwav else ()) + ffmpeg.AUDIO_EXTENSIONS


def sound_filters(rwav=False):
    """The Windows file dialog's filters for a Replace... pick."""
    from Sound import clips
    wavs = ';'.join('*' + e for e in clips.WAV_EXTENSIONS)
    return ([('Sound files', ';'.join('*' + e for e in sound_extensions(rwav))), ('WAV files', wavs)]
            + ([('Encoded clips', '*' + clips.RWAV_EXTENSION)] if rwav else []) + [('All files', '*.*')])


class FilePicks:
    def __init__(self, app):
        self.app = app                       # gui.SluggiesGui
        self._pending = None                 # on_files of the pick the built-in dialog serves

    def add_dialog(self, tag, extensions, save=False):
        """A hidden built-in file dialog ``tag`` listing ``extensions`` (and every file)."""
        start = self.app.models_dir if os.path.isdir(self.app.models_dir) else self.app.root_dir
        with dpg.file_dialog(directory_selector=False, show=False, modal=True, tag=tag, width=760, height=460,
                             callback=self._chosen, cancel_callback=lambda *_: None, default_path=start,
                             default_filename='pack' if save else ''):
            for ext in extensions:
                dpg.add_file_extension(ext, color=_EXTENSION_COLOR)
            dpg.add_file_extension('.*')

    def _chosen(self, _sender, app_data):
        app_data = app_data or {}
        paths = list(app_data.get('selections', {}).values()) or [app_data.get('file_path_name')]
        callback, self._pending = self._pending, None
        if callback is not None:
            callback([p for p in paths if p])

    def pick(self, tag, title, filters, on_files, **kwargs):
        """``gui.pick_files`` with built-in dialog ``tag`` as its fallback."""
        self._pending = on_files
        if not self.app.pick_files(tag, title, filters, on_files, on_cancel=lambda: None, **kwargs):
            self._pending = None

    def pick_sound(self, tag, title, setting, starts, on_path, rwav=False):
        """``pick_one`` for a sound file."""
        self.pick_one(tag, title, sound_filters(rwav), setting, starts, on_path)

    def pick_one(self, tag, title, filters, setting, starts, on_path):
        """Pick one existing file for ``on_path(path)``, starting in the folder the last one came from (GUI
        settings key ``setting``), else in the first existing folder of ``starts``, else in 2_Output_Assets."""
        if self.app.picking:
            return
        start = next((d for d in (self.app.settings.get(setting), *starts) if d and os.path.isdir(d)),
                     self.app.models_dir)
        if os.path.isdir(start):
            dpg.configure_item(tag, default_path=start)

        def picked(paths):
            path = paths[0] if paths else None
            if not path or not os.path.isfile(path):
                return
            try:
                self.app.settings.set(setting, os.path.dirname(os.path.abspath(path)))
            except OSError:
                pass                          # not remembered; the pick itself goes on
            on_path(path)
        self.pick(tag, title, filters, picked, initial_dir=start)


class ClipPlayer:
    def __init__(self, prefix):
        self.prefix = prefix
        self.path = None                     # the temp WAV winsound plays from (one, reused)

    def play(self, wav_bytes):
        """Play ``wav_bytes`` (a WAV file) asynchronously, stopping what still plays. Windows only; raises
        ``RuntimeError`` / ``OSError``."""
        import winsound
        if self.path is None:
            handle, self.path = tempfile.mkstemp(prefix=self.prefix, suffix='.wav')
            os.close(handle)
        winsound.PlaySound(None, winsound.SND_PURGE)              # stop what still plays: the file is rewritten
        with open(self.path, 'wb') as handle:
            handle.write(wav_bytes)
        winsound.PlaySound(self.path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


@contextlib.contextmanager
def render_turns():
    """Around long numpy work on a worker thread that holds the GIL: give the render thread more turns meanwhile."""
    interval = sys.getswitchinterval()
    sys.setswitchinterval(0.0005)
    try:
        yield
    finally:
        sys.setswitchinterval(interval)
