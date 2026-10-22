"""The toast: a short note at the bottom of the window that closes itself (Dear PyGui has none).

One ``Toast`` per GUI (``App.toast``); every tab shows its notes through it: ``ok`` for a finished action,
``error`` for a failed one (its text says where to look, usually the log), ``warn`` for a stopped one, ``show``
for any other color. A click closes the toast sooner; a new toast replaces the open one. ``tick`` runs once per
frame: it closes the toast when its time is up and keeps it centered when the window is resized.
"""

import time

import dearpygui.dearpygui as dpg

TEXT = (220, 220, 220, 255)
OK = (120, 220, 140, 255)
WARN = (255, 210, 90, 255)
ERROR = (255, 120, 110, 255)
SECONDS = 4.0
ERROR_SECONDS = 7.0             # long enough to read the hint where to look
LOG_HINT = 'Check the log (Show console) for details.'

_WINDOW = 'toast_window'
_HANDLERS = 'toast_handlers'
_WIDTH = 640
_BOTTOM = 70                    # the gap to the window's bottom edge: clears the "Show console" row


class Toast:
    def __init__(self):
        self.until = None       # time.monotonic() at which the open toast closes itself

    def show(self, text, color=TEXT, seconds=SECONDS):
        self.close()
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        width = min(_WIDTH, max(200, vw - 32))
        with dpg.window(tag=_WINDOW, no_title_bar=True, no_move=True, no_resize=True, no_collapse=True,
                        no_saved_settings=True, no_focus_on_appearing=True, autosize=True, min_size=(20, 20),
                        pos=(max(0, (vw - width) // 2), max(0, vh - _BOTTOM - 35))):
            label = dpg.add_text(text, wrap=width - 16, color=color)
        with dpg.item_handler_registry(tag=_HANDLERS):       # a window takes no clicked handler
            dpg.add_item_clicked_handler(callback=lambda *_: self.close())
        dpg.bind_item_handler_registry(label, _HANDLERS)
        self.until = time.monotonic() + seconds

    def ok(self, text):
        self.show(text, OK)

    def warn(self, text):
        self.show(text, WARN, ERROR_SECONDS)

    def error(self, text, hint=LOG_HINT):
        """A failed action: ``text`` plus ``hint`` (where to look; None for none)."""
        self.show(f'{text} {hint}' if hint else text, ERROR, ERROR_SECONDS)

    def close(self):
        self.until = None
        for tag in (_WINDOW, _HANDLERS):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)

    def tick(self):
        if self.until is None:
            return
        if time.monotonic() >= self.until:
            self.close()
            return
        # centered on its real size (known once it has been drawn) and kept there when the window is resized
        width, height = dpg.get_item_rect_size(_WINDOW)
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        dpg.set_item_pos(_WINDOW, (max(0, (vw - width) // 2), max(0, vh - height - _BOTTOM)))
