"""The GUI's remembered settings: one small JSON object in ``_gui/settings.json``.

A missing or broken file reads as no settings; a failed write is reported to the caller, the value stays for the
session and the next ``set`` writes the file again.
"""

import json
import os
import threading

SETTINGS_REL = os.path.join('_gui', 'settings.json')


class Settings:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()        # set() is called from the render thread and from callback threads
        self._unsaved = False                # the last write failed: the file is behind self.data
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.data = data if isinstance(data, dict) else {}

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        """Store ``value`` and write the file (OSError when it cannot be written)."""
        with self._lock:
            if self.data.get(key) == value and not self._unsaved:
                return
            self.data[key] = value
            self._unsaved = True
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            # One temp name per thread and process: two GUIs on one folder must not share it.
            tmp = f'{self.path}.{os.getpid()}.{threading.get_ident()}.tmp'
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            self._unsaved = False
