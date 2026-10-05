"""The Windows "Open" dialog (comdlg32 ``GetOpenFileNameW``) through ctypes.

The GUI uses it instead of Dear PyGui's built-in file dialog on Windows: it is
the dialog users know, and Explorer sorts names numerically, so the model
folders list as 1, 2, ..., 10 instead of 1, 10, 100, 2. Dear PyGui's dialog
stays the fallback: on other systems, when ``SLUGGIES_NATIVE_DIALOG=0`` is set,
and when the native dialog fails to open.

``ask_open_files`` blocks until the dialog closes, so the GUI calls it on a
worker thread. With the GUI window as owner the dialog is modal to it, and
the GUI keeps rendering underneath.
"""

import ctypes
import os
import platform
from ctypes import wintypes

_OFN_NOCHANGEDIR = 0x00000008       # the dialog must not move the process's working directory
_OFN_ALLOWMULTISELECT = 0x00000200
_OFN_PATHMUSTEXIST = 0x00000800
_OFN_FILEMUSTEXIST = 0x00001000
_OFN_EXPLORER = 0x00080000
_BUFFER_CHARS = 1 << 16             # room for a long multi-selection


class NativeDialogError(Exception):
    """The native dialog could not be shown; use the fallback."""


def enabled(env=None, system=None) -> bool:
    """True when the native dialog should be tried (Windows, not switched off)."""
    env = os.environ if env is None else env
    system = platform.system() if system is None else system
    return system == 'Windows' and env.get('SLUGGIES_NATIVE_DIALOG', '1').strip() not in ('0', 'false', 'no', 'off')


def filter_string(filters) -> str:
    """``[('Sluggie files', '*.sluggie'), ...]`` -> the double-NUL-terminated filter list."""
    return ''.join(f'{label} ({pattern})\0{pattern}\0' for label, pattern in filters) + '\0'


def parse_selection(raw: str) -> list:
    """The file buffer after OK -> full paths. One file: its full path. Several (Explorer style): the
    directory, then the bare names, NUL-separated, ending in an empty string."""
    parts = []
    for part in raw.split('\0'):
        if not part:
            break
        parts.append(part)
    if len(parts) <= 1:
        return parts
    folder, names = parts[0], parts[1:]
    return [os.path.join(folder, name) for name in names]


class _OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ('lStructSize', wintypes.DWORD),
        ('hwndOwner', wintypes.HWND),
        ('hInstance', wintypes.HINSTANCE),
        ('lpstrFilter', ctypes.c_void_p),
        ('lpstrCustomFilter', wintypes.LPWSTR),
        ('nMaxCustFilter', wintypes.DWORD),
        ('nFilterIndex', wintypes.DWORD),
        ('lpstrFile', ctypes.c_void_p),
        ('nMaxFile', wintypes.DWORD),
        ('lpstrFileTitle', wintypes.LPWSTR),
        ('nMaxFileTitle', wintypes.DWORD),
        ('lpstrInitialDir', wintypes.LPCWSTR),
        ('lpstrTitle', wintypes.LPCWSTR),
        ('Flags', wintypes.DWORD),
        ('nFileOffset', wintypes.WORD),
        ('nFileExtension', wintypes.WORD),
        ('lpstrDefExt', wintypes.LPCWSTR),
        ('lCustData', wintypes.LPARAM),
        ('lpfnHook', ctypes.c_void_p),
        ('lpTemplateName', wintypes.LPCWSTR),
        ('pvReserved', ctypes.c_void_p),
        ('dwReserved', wintypes.DWORD),
        ('FlagsEx', wintypes.DWORD),
    ]


def find_owner_window(title: str):
    """This process's top-level window with ``title`` (the GUI viewport), or None."""
    try:
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return hwnd if pid.value == os.getpid() else None
    except (OSError, AttributeError):
        return None


def ask_open_files(title: str, initial_dir: str, filters, multi: bool = False, owner=None):
    """Show the dialog. Returns the chosen paths (a list, one entry unless ``multi``), or None when
    the user cancels. Raises NativeDialogError when the dialog cannot be shown."""
    try:
        comdlg32 = ctypes.WinDLL('comdlg32')
        ole32 = ctypes.WinDLL('ole32')
    except (OSError, AttributeError) as exc:
        raise NativeDialogError(f'comdlg32 not available: {exc}') from exc
    # The Explorer-style dialog hosts shell COM objects: give this (worker) thread an apartment.
    initialized = ole32.CoInitializeEx(None, 0x2) in (0, 1)       # COINIT_APARTMENTTHREADED; S_OK / S_FALSE
    try:
        filter_buffer = ctypes.create_unicode_buffer(filter_string(filters))
        file_buffer = ctypes.create_unicode_buffer(_BUFFER_CHARS)
        ofn = _OPENFILENAMEW()
        ofn.lStructSize = ctypes.sizeof(ofn)
        ofn.hwndOwner = owner
        ofn.lpstrFilter = ctypes.addressof(filter_buffer)
        ofn.nFilterIndex = 1
        ofn.lpstrFile = ctypes.addressof(file_buffer)
        ofn.nMaxFile = _BUFFER_CHARS
        ofn.lpstrInitialDir = initial_dir if initial_dir and os.path.isdir(initial_dir) else None
        ofn.lpstrTitle = title
        ofn.Flags = (_OFN_EXPLORER | _OFN_FILEMUSTEXIST | _OFN_PATHMUSTEXIST | _OFN_NOCHANGEDIR
                     | (_OFN_ALLOWMULTISELECT if multi else 0))
        if comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
            return parse_selection(ctypes.wstring_at(ctypes.addressof(file_buffer), _BUFFER_CHARS))
        error = comdlg32.CommDlgExtendedError()
        if error:
            raise NativeDialogError(f'GetOpenFileNameW failed (CommDlgExtendedError 0x{error:04X})')
        return None
    finally:
        if initialized:
            ole32.CoUninitialize()
