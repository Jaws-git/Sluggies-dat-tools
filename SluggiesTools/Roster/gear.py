"""Equipment of a character slot: bats and gloves.

Every character directory has four equipment files besides its High and Low
models: file 2 the bat, 3 the left glove, 4 the right glove and 5, which most
characters leave as a placeholder (Peach holds a second bat there, Wario another
model).
This module is the pure part: the role table, which source file may go into
which target file, the placeholder test, and the lookup of the gear that
belongs to an exported model (``find_gear``, "bundled gear" for new IDs).
No game files are read.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

BAT, GLOVE_L, GLOVE_R, EXTRA = 'bat', 'glove_l', 'glove_r', 'extra'
ROLES = {2: BAT, 3: GLOVE_L, 4: GLOVE_R, 5: EXTRA}        # file index -> role
FILES = tuple(ROLES)
FILE_OF = {role: file for file, role in ROLES.items()}
LABELS = {BAT: 'Bat', GLOVE_L: 'Left glove', GLOVE_R: 'Right glove', EXTRA: 'Extra bat (file 5)'}
# What may go where: a bat-type block fits file 2 and file 5; a glove only its own hand.
_CLASS = {2: 'bat', 3: 'glove_l', 4: 'glove_r', 5: 'bat'}
_CLASS_NAMES = {'bat': 'a bat', 'glove_l': 'a left glove', 'glove_r': 'a right glove'}
PLACEHOLDER_MAX = 0x40
SOURCE_FOLDER = re.compile(r'^\d+_.+\.gpl.?$', re.IGNORECASE)


class GearError(ValueError):
    pass


def label(file_index: int) -> str:
    return LABELS[ROLES[file_index]]


def parse_file(value) -> int:
    """An equipment file as a number (2-5) or a role name (``bat``, ``glove_l``, ``glove_r``, ``extra``)."""
    if isinstance(value, str):
        text = value.strip().lower()
        if text in FILE_OF:
            return FILE_OF[text]
        try:
            value = int(text, 0)
        except ValueError as exc:
            raise GearError(f'{value!r} is not an equipment file (2-5, or {", ".join(FILE_OF)})') from exc
    if value not in ROLES:
        raise GearError(f'file {value} is not an equipment file (2 bat, 3 left glove, 4 right glove, 5 extra bat)')
    return value


def target_file(source_file: int, override: int | None = None) -> int:
    """The file a block exported from ``source_file`` goes to (``override``: a bat into the extra slot or back).
    A role mismatch is refused with a message naming both roles."""
    if source_file not in ROLES:
        raise GearError(f'file {source_file} is not equipment (2 bat, 3 left glove, 4 right glove, 5 extra bat)')
    file = source_file if override is None else parse_file(override)
    if _CLASS[file] != _CLASS[source_file]:
        raise GearError(f'a block exported from file {source_file} ({label(source_file)}) is '
                        f'{_CLASS_NAMES[_CLASS[source_file]]}, and cannot go into file {file} ({label(file)}): '
                        f'that takes {_CLASS_NAMES[_CLASS[file]]}')
    return file


def is_placeholder(block: bytes | None) -> bool:
    """The empty equipment file most characters ship in slot 5: at most 64 bytes, nothing past the archive
    header's first two words (a shared 64-byte block, or the 32 zero bytes of the Mii directories)."""
    return block is not None and len(block) <= PLACEHOLDER_MAX and not any(block[8:])


@dataclass(frozen=True)
class Gear:
    """One equipment ``.sluggie`` found next to a model."""
    file: int                       # its FileIndex (2-5)
    path: str


def _read(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f).get('SluggiesModel') or {}
    except (OSError, ValueError):
        return {}


def character_folder(model_path: str) -> str:
    """The character's output folder (``2_Output_Models/22 Peach``) of a model ``.sluggie`` or its folder."""
    path = os.path.abspath(model_path)
    folder = os.path.dirname(path) if os.path.isfile(path) else path
    return os.path.dirname(folder)


def find_gear(model_path: str, chunk: int) -> tuple[dict[int, Gear], dict[int, list[str]]]:
    """``(found, ambiguous)`` for the model at ``model_path``: the equipment ``.sluggie`` per file (2-5) in its
    character folder whose ``ChunkNumber`` is ``chunk``, and the files with several candidates (nothing is guessed:
    they are not in ``found``)."""
    root = character_folder(model_path)
    candidates: dict[int, list[str]] = {}
    try:
        top = sorted(os.listdir(root))
    except OSError:
        return {}, {}
    for name in top:
        folder = os.path.join(root, name)
        if not name.isdigit() or not os.path.isdir(folder):
            continue
        for sub in sorted(os.listdir(folder)):
            sub_dir = os.path.join(folder, sub)
            if not SOURCE_FOLDER.match(sub) or not os.path.isdir(sub_dir):
                continue
            for item in sorted(os.listdir(sub_dir)):
                if not item.lower().endswith('.sluggie'):
                    continue
                path = os.path.join(sub_dir, item)
                model = _read(path)
                if model.get('ChunkNumber') == chunk and model.get('FileIndex') in ROLES:
                    candidates.setdefault(model['FileIndex'], []).append(path)
    found = {f: Gear(f, paths[0]) for f, paths in candidates.items() if len(paths) == 1}
    ambiguous = {f: paths for f, paths in candidates.items() if len(paths) > 1}
    return found, ambiguous
