"""Player and stadium memory checks on the output game (``3_Output_Dat/main.dol``).

Every player on the field gets its own fixed heap for its High model (file 0), Low model (file 1) and one bat or
glove (files 2-5): 870,400 bytes in the stock game, more with a ``player_heap_*`` game option. A player whose
files don't fit crashes the game when it is loaded onto the field (``_docs/_docs_roster/RosterExpansion.md``,
"Game memory"). A stadium's model gets a heap sized to the file instead, so a stadium has no fixed cap; a big one
only takes more of the game heap, which every scene shares.

Used by the dispatcher (``start.py``: a warning after any command that changed a character's files or the player
heap) and the GUI's Maintenance tab (``maintenance.CHECKS``: every character and stadium).
"""

import os
import sys
from dataclasses import dataclass

try:                                       # as SluggiesTools.game_memory (start.py, the GUI, the exe)
    from .Dol import dolfile
    from .GameOptions import game_options
    from .Roster import dat_hammerspace, ids, slots
except ImportError:                        # as a bare module, SluggiesTools on sys.path
    _HERE = os.path.dirname(os.path.abspath(__file__))
    for _path in (_HERE, os.path.join(_HERE, 'Roster')):
        if _path not in sys.path:
            sys.path.insert(0, _path)
    from Dol import dolfile
    from GameOptions import game_options
    import dat_hammerspace
    import ids
    import slots

OUTPUT_FOLDER = '3_Output_Dat'
MODELS_FOLDER = '2_Output_Models'
HEAP_HEAD = 0x50                 # the expanded heap's own header inside its size
# 8 small blocks (132 bytes), 11 block headers (176) and up to 32 bytes of alignment per model/gear block (RAM
# dumps, 2026-10-10: High 761,472 + Low 77,984 + glove 26,944 fitted the stock 870,320, 16,384 more did not).
BLOCK_OVERHEAD = 404
HIGH, LOW, GEAR_FILES = 0, 1, (2, 3, 4, 5)
STADIUM_DIRS = range(7, 16)      # Mario Stadium .. Bowser Jr Playroom (Toy Field, dir 16, is not checked)
# The largest stadium model of the stock game (Wario City, dir 9 file 0). Bigger ones are untested: each byte
# more comes out of the game heap, about 4.1 MB free at Wario City's first pitch (RAM dump 2026-10-10).
STADIUM_TESTED_MAX = 2_655_048


@dataclass(frozen=True)
class PlayerHeap:
    size: int                    # the heap a player gets on stock console memory
    big_size: int | None = None  # with a player_heap_big_* level and Dolphin's MEM2 at 128 MB
    level: str = ''              # the game option key ('' = stock)

    @property
    def usable(self) -> int:
        return self.size - HEAP_HEAD


@dataclass(frozen=True)
class CharacterNeed:
    directory: int
    high: int
    low: int
    gear: int

    @property
    def need(self) -> int:
        return self.high + self.low + self.gear + BLOCK_OVERHEAD


def player_heap(image) -> PlayerHeap:
    """The player heap the game options in ``image`` set."""
    on = set(game_options.detect(image))
    stock = game_options.PLAYER_HEAP_STOCK
    for kb in game_options.PLAYER_HEAP_LEVELS_KB:
        if game_options.player_heap_key(kb) in on:
            return PlayerHeap(stock + kb * 1024, level=game_options.player_heap_key(kb))
    for kb in game_options.BIG_HEAP_LEVELS_KB:
        if game_options.big_heap_key(kb) in on:
            return PlayerHeap(stock + game_options.PLAYER_HEAP_SAFE_KB * 1024, stock + kb * 1024,
                              game_options.big_heap_key(kb))
    return PlayerHeap(stock)


def _lengths(image) -> dict[int, dict[int, int]]:
    out: dict[int, dict[int, int]] = {}
    for chunk, index, _record, words in dat_hammerspace.iter_records(image):
        out.setdefault(chunk, {})[index] = dat_hammerspace.slot(list(words), 'en')[1]
    return out


def character_dirs(image) -> list[int]:
    """The model directories players load from: the stock IDs' and the roster's own directories."""
    dirs = {cid + ids.MODEL_DIR_BASE for cid in range(ids.STOCK_IDS)}
    try:
        dirs |= {directory for directory, _source in slots.own_dirs(image).values()}
    except slots.SlotError:
        pass                     # a DOL the roster tool did not build: the stock directories only
    return sorted(dirs)


def characters(image) -> dict[int, CharacterNeed]:
    """``{model directory: its High, Low and largest gear file}`` for every character directory."""
    lengths = _lengths(image)
    out = {}
    for directory in character_dirs(image):
        files = lengths.get(directory, {})
        if HIGH in files and LOW in files:
            out[directory] = CharacterNeed(directory, files[HIGH], files[LOW],
                                           max((files.get(i, 0) for i in GEAR_FILES), default=0))
    return out


def stadiums(image) -> dict[int, dict[int, int]]:
    """``{stadium directory: {file: length}}``."""
    lengths = _lengths(image)
    return {d: lengths[d] for d in STADIUM_DIRS if d in lengths}


def load(dol_path):
    with open(dol_path, 'rb') as f:
        return dolfile.DolImage(f.read())


def folder_names(root_dir) -> dict[int, str]:
    """``{directory: "18 Mario"}`` from the export folder names, for messages."""
    out = {}
    try:
        for name in os.listdir(os.path.join(root_dir, MODELS_FOLDER)):
            head = name.split(' ', 1)[0]
            if head.isdigit() and ' ' in name:
                out.setdefault(int(head), name)
    except OSError:
        pass
    return out


def describe(directory: int, names: dict[int, str]) -> str:
    return names.get(directory, f'model directory {directory}')


def problem_text(c: CharacterNeed, heap: PlayerHeap, name: str) -> str | None:
    """The warning for one character, or None when it fits."""
    if c.need <= heap.usable:
        return None
    files = f'High {c.high:,} + Low {c.low:,} + bat/glove {c.gear:,} + {BLOCK_OVERHEAD} = {c.need:,} bytes'
    over = c.need - heap.usable
    if heap.big_size is not None and c.need <= heap.big_size - HEAP_HEAD:
        return (f'{name} needs {files}: {over:,} bytes more than the {heap.size:,}-byte player memory a game '
                f'without Dolphin\'s 128 MB MEM2 override gets. It plays only with the override '
                f'({heap.big_size:,} bytes).')
    size = heap.big_size if heap.big_size is not None else heap.size
    over = c.need - (size - HEAP_HEAD)
    return (f'{name} needs {files}: {over:,} bytes more than the {size:,}-byte player memory, so the game '
            f'crashes when it loads onto the field (often only once it fields: gloves are bigger than bats).')


def snapshot(dol_path):
    """``(player heap, {directory: CharacterNeed})`` of an output DOL, or None when it can't be read."""
    try:
        image = load(dol_path)
        return player_heap(image), characters(image)
    except Exception:            # noqa: BLE001 - a warning helper must never fail a command
        return None


def changed_problems(before, after, names: dict[int, str]) -> list[str]:
    """Warnings for the characters that no longer fit and whose files or player memory changed between two
    snapshots (all of them when there is no ``before``)."""
    if after is None:
        return []
    heap, chars = after
    old_heap, old_chars = before if before is not None else (None, {})
    out = []
    for directory, c in sorted(chars.items()):
        if before is not None and old_heap == heap and old_chars.get(directory) == c:
            continue
        text = problem_text(c, heap, describe(directory, names))
        if text:
            out.append(text)
    return out
