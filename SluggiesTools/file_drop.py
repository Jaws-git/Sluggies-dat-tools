"""Files dropped onto the GUI window from Explorer (``WM_DROPFILES``) through ctypes.

Dear PyGui has no drop support of its own, so on Windows ``install`` marks the
viewport window as a drop target (``DragAcceptFiles``) and subclasses its window
procedure (``SetWindowSubclass``). A drop calls ``on_drop(paths, (x, y))`` with
the full paths and the drop point in client coordinates, which are Dear PyGui's
screen coordinates (``get_item_rect_min``), so the GUI can tell which field the
files landed on.

``on_drop`` runs inside the window procedure, while Dear PyGui renders a frame:
it must not call Dear PyGui, only hand the drop over (the GUI's output queue).
"""

import ctypes
import os
import platform
from ctypes import wintypes

_WM_DROPFILES = 0x0233
_WM_COPYDATA = 0x004A
_WM_COPYGLOBALDATA = 0x0049
_MSGFLT_ALLOW = 1
_SUBCLASS_ID = 0x51D6

_SUBCLASSPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                   wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
_installed = {}          # hwnd -> the subclass procedure, kept alive as long as the window


def _api():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    shell32 = ctypes.WinDLL('shell32')
    comctl32 = ctypes.WinDLL('comctl32')
    shell32.DragAcceptFiles.argtypes = (wintypes.HWND, wintypes.BOOL)
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragQueryFileW.argtypes = (wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT)
    shell32.DragQueryPoint.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.POINT))
    shell32.DragFinish.argtypes = (wintypes.HANDLE,)
    comctl32.SetWindowSubclass.argtypes = (wintypes.HWND, _SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t)
    comctl32.DefSubclassProc.restype = ctypes.c_ssize_t
    comctl32.DefSubclassProc.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.ChangeWindowMessageFilterEx.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p)
    return user32, shell32, comctl32


def _read_drop(shell32, hdrop):
    """The dropped paths and the client-area drop point of the ``HDROP`` handle."""
    count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    paths = []
    for index in range(count):
        length = shell32.DragQueryFileW(hdrop, index, None, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        shell32.DragQueryFileW(hdrop, index, buffer, length + 1)
        paths.append(buffer.value)
    point = wintypes.POINT()
    shell32.DragQueryPoint(hdrop, ctypes.byref(point))
    return paths, (point.x, point.y)


def install(hwnd, on_drop) -> bool:
    """Accept files dropped onto window ``hwnd`` and report them to ``on_drop(paths, (x, y))``.
    False when it cannot be set up (not Windows, no window, the calls fail): the GUI works without it."""
    if platform.system() != 'Windows' or not hwnd:
        return False
    if hwnd in _installed:
        return True
    try:
        user32, shell32, comctl32 = _api()

        def procedure(window, message, wparam, lparam, _id, _data):
            if message == _WM_DROPFILES:
                hdrop = wintypes.HANDLE(wparam)
                try:
                    paths, point = _read_drop(shell32, hdrop)
                finally:
                    shell32.DragFinish(hdrop)
                try:
                    on_drop([os.path.normpath(path) for path in paths], point)
                except Exception:
                    pass             # an exception must not leave the window procedure
                return 0
            return comctl32.DefSubclassProc(window, message, wparam, lparam)

        callback = _SUBCLASSPROC(procedure)
        if not comctl32.SetWindowSubclass(hwnd, callback, _SUBCLASS_ID, 0):
            return False
        _installed[hwnd] = callback
        # an elevated GUI would otherwise not receive drops from a normal Explorer (UIPI)
        for message in (_WM_DROPFILES, _WM_COPYDATA, _WM_COPYGLOBALDATA):
            user32.ChangeWindowMessageFilterEx(hwnd, message, _MSGFLT_ALLOW, None)
        shell32.DragAcceptFiles(hwnd, True)
        return True
    except (OSError, AttributeError):
        return False
