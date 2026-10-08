"""The GUI's remembered settings: one small JSON object in ``_gui/settings.json``.

A missing or broken file reads as no settings; a failed write is reported to the caller and changes nothing else.
"""

import json
import os

SETTINGS_REL = os.path.join('_gui', 'settings.json')


class Settings:
    def __init__(self, path: str):
        self.path = path
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
        if self.data.get(key) == value:
            return
        self.data[key] = value
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
