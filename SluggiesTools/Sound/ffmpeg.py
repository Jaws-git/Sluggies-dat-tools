"""Finding ffmpeg (for sound files that aren't WAVs) and installing it with winget.

ffmpeg has no official Windows installer; users get it from winget (``Gyan.FFmpeg``), Chocolatey, Scoop or a
zip unpacked by hand. ``find`` tries PATH first, then the folders those package managers link it into: a winget
install adds its Links folder to PATH only for programs started after it, so a GUI that is already open would
miss it on PATH alone.
"""

import glob
import os
import shutil
import subprocess

WINGET_ID = 'Gyan.FFmpeg'
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _candidates():
    local = os.environ.get('LOCALAPPDATA', '')
    program_data = os.environ.get('ProgramData', r'C:\ProgramData')
    home = os.path.expanduser('~')
    if local:
        yield os.path.join(local, 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe')
        yield from sorted(glob.glob(os.path.join(local, 'Microsoft', 'WinGet', 'Packages', WINGET_ID + '_*',
                                                 '*', 'bin', 'ffmpeg.exe')), reverse=True)
    yield os.path.join(os.environ.get('SCOOP', os.path.join(home, 'scoop')), 'shims', 'ffmpeg.exe')
    yield os.path.join(program_data, 'chocolatey', 'bin', 'ffmpeg.exe')


def find():
    """Path of an ffmpeg executable, or None."""
    found = shutil.which('ffmpeg')
    if found:
        return found
    return next((path for path in _candidates() if os.path.isfile(path)), None)


def version(path):
    """The version word of ``ffmpeg -version`` (e.g. ``'8.0-full_build-www.gyan.dev'``), or None when it won't run."""
    try:
        out = subprocess.run([path, '-version'], capture_output=True, text=True, timeout=15,
                             creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    words = out.split()
    return words[2] if len(words) > 2 and words[:2] == ['ffmpeg', 'version'] else None


def winget():
    """Path of winget, or None (it ships with Windows 10 1809+/11 as the "App Installer")."""
    return shutil.which('winget')


def winget_install():
    """Run ``winget install Gyan.FFmpeg`` (winget asks for elevation itself). ``(ok, output)``."""
    command = [winget() or 'winget', 'install', '--id', WINGET_ID, '--exact', '--source', 'winget',
               '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity']
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              creationflags=_NO_WINDOW)
    except OSError as exc:
        return False, str(exc)
    return done.returncode == 0, (done.stdout + done.stderr).strip()


# ------------------------------------------------------------------------------------------------ decoding

# what the file pickers offer besides WAV; ffmpeg reads many more, and any file is tried
AUDIO_EXTENSIONS = ('.mp3', '.ogg', '.oga', '.opus', '.flac', '.m4a', '.aac', '.wma', '.aif', '.aiff', '.webm',
                    '.mp4', '.mka')
DECODE_TIMEOUT = 300


class FfmpegError(ValueError):
    pass


def decode_to_wav(path):
    """Any sound file ffmpeg reads -> the bytes of a 32-bit float WAV (all channels, the file's own rate).

    Written to a temporary file, not a pipe: on a pipe ffmpeg can't go back to fill in the WAV's chunk sizes."""
    import tempfile
    name = os.path.basename(path)
    exe = find()
    if exe is None:
        raise FfmpegError(f'{name} is not a WAV file; reading other sound files needs ffmpeg (install it on the '
                          'Maintenance tab), or save it as a WAV')
    handle, target = tempfile.mkstemp(prefix='sluggies_decode_', suffix='.wav')
    os.close(handle)
    try:
        try:
            done = subprocess.run([exe, '-nostdin', '-v', 'error', '-y', '-i', os.path.abspath(path), '-vn', '-sn', '-dn',
                                   '-map_metadata', '-1', '-c:a', 'pcm_f32le', '-f', 'wav', target],
                                  capture_output=True, text=True, encoding='utf-8', errors='replace',
                                  timeout=DECODE_TIMEOUT, creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise FfmpegError(f'ffmpeg took longer than {DECODE_TIMEOUT} s to read {name}') from exc
        except OSError as exc:
            raise FfmpegError(f'ffmpeg could not be started: {exc}') from exc
        if done.returncode != 0:
            detail = done.stderr.strip().splitlines()
            raise FfmpegError(f'ffmpeg could not read {name}' + (f': {detail[-1]}' if detail else ''))
        with open(target, 'rb') as f:
            return f.read()
    finally:
        try:
            os.remove(target)
        except OSError:
            pass
