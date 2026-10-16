"""What the Sound FX, Voices and Music tabs share besides ``gui_volume``: the sound-file pickers, clip playback and
the worker-thread GIL helper.

* ``FilePicks``: a tab's built-in file dialogs (used when the Windows one is off, ``gui.pick_files``).
  ``pick_sound`` picks one sound file, starting where the last one came from (a GUI settings key per tab).
* ``ClipPlayer``: ``winsound`` cannot play asynchronously from memory, so a WAV goes through one reused temp file.
* ``render_turns``: for a worker thread running long numpy loops that hold the GIL.
"""

import contextlib
import os
import sys
import tempfile

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

    def add_dialog(self, tag, extensions, save=False):
        """A hidden built-in file dialog ``tag`` listing ``extensions`` (and every file)."""
        self.app.add_file_dialog(tag, (*extensions, '.*'), color=_EXTENSION_COLOR,
                                 default_filename='pack' if save else '')

    def pick(self, tag, title, filters, on_files, **kwargs):
        """``gui.pick_files`` with built-in dialog ``tag`` as its fallback."""
        self.app.pick_files(tag, title, filters, on_files, on_cancel=lambda: None, **kwargs)

    def pick_sound(self, tag, title, setting, starts, on_path, rwav=False):
        """``pick_one`` for a sound file."""
        self.pick_one(tag, title, sound_filters(rwav), setting, starts, on_path)

    def pick_one(self, tag, title, filters, setting, starts, on_path):
        """Pick one existing file for ``on_path(path)``, starting in the folder the last one came from (GUI
        settings key ``setting``), else in the first existing folder of ``starts``, else in 2_Output_Assets."""
        def picked(paths):
            if os.path.isfile(paths[0]):
                on_path(paths[0])
        self.pick(tag, title, filters, picked, remember=setting,
                  initial_dir=next((d for d in starts if os.path.isdir(d)), None))


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
