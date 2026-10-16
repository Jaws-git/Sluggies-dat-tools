"""Player memory check before a write: a planned change that leaves a character over the player heap is stopped.

``game_memory`` checks the files the output game holds (a warning after each command, the Maintenance tab); this
module checks what a planned chain will write, before it writes: slot edits (``Roster/slot_memory.py`` projects
each touched slot's files from the planned commands), roster pack loads, and a lower player memory level
(``GameOptions/runner.py``). The limit is the selected level's heap (``game_memory.PlayerHeap.selected``: a
``player_heap_big_*`` level counts with its full size).

The sizes of blocks a chain builds from ``.sluggie`` files come from the chain's build checks: each
``--validate-only`` records its block's length here (:func:`record_size`, ``_gui/build_sizes.json``, keyed by the
file's path, modification time and size), so the check after the build checks knows them, and a staging dry run
that skips the build checks of edits staged earlier still knows theirs.

A dry run records what it found for the GUI's dialogs (:func:`check_path`, like ``gecko_compat``); a writing run
asks first (:func:`confirm`: a ``danger`` popup in the GUI, never skipped), unless the user accepted it already
(``--accept-memory-over``: :data:`ACCEPT_ENV`).
"""

import json
import os
import sys

try:                                       # as SluggiesTools.memory_guard (start.py, the GUI, the exe)
    from . import game_memory, slogger
except ImportError:                        # as a bare module, SluggiesTools on sys.path
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    import game_memory
    import slogger

SOURCE = 'memory'
GUI_FOLDER = '_gui'
SIZES_FILE = 'build_sizes.json'
CHECK_FILE = 'memory_check.json'
BUILD = 'build'                  # a projected file the chain builds: ``{'build': path of the .sluggie}``
# Set by the dispatcher (``--accept-memory-over``, or after its own question) for the commands it runs.
ACCEPT_ENV = 'SLUGGIES_MEMORY_ACCEPTED'
QUESTION_TAIL = ('A player whose files do not fit its player memory crashes the game when it is loaded onto the '
                 'field. Write it anyway? (y/n) ')
# A dialog's line above its verdict (the GUI's summary and staging dialogs).
WARNING = ('Player memory: the game crashes when this character is loaded onto the field. Raise the player memory '
           '(Options tab) or make the models smaller.')


def root_of(path: str) -> str:
    """The repository root of a file under it (``3_Output_Dat/main.dol`` -> the folder holding ``3_Output_Dat``)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(path)))


# --------------------------------------------------------------------------
# Block sizes of the build checks
# --------------------------------------------------------------------------

def sizes_path(root_dir: str) -> str:
    return os.path.join(root_dir, GUI_FOLDER, SIZES_FILE)


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _stamp(path: str) -> list | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_size]


def _read_sizes(root_dir: str) -> dict:
    try:
        with open(sizes_path(root_dir), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_size(root_dir: str, path: str, length: int) -> None:
    """A build check built ``path`` into a ``length``-byte block. Entries of files that changed or are gone are
    dropped on the way."""
    data = {k: v for k, v in _read_sizes(root_dir).items()
            if isinstance(v, dict) and v.get('stamp') == _stamp(k)}
    stamp = _stamp(path)
    if stamp is None:
        return
    data[_key(path)] = {'stamp': stamp, 'length': int(length)}
    target = sizes_path(root_dir)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, target)


def lookup_size(root_dir: str, path: str) -> int | None:
    """The block length a build check recorded for ``path`` as it is now, or None (never built, or changed since)."""
    entry = _read_sizes(root_dir).get(_key(path))
    if not isinstance(entry, dict) or entry.get('stamp') != _stamp(path):
        return None
    return entry.get('length')


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

def heap_text(heap: game_memory.PlayerHeap) -> str:
    """``the 984,064-byte player memory (+128 KB)``."""
    extra = (heap.selected - game_memory.game_options.PLAYER_HEAP_STOCK) // 1024
    level = 'stock' if extra <= 0 else (f'+{extra // 1024} MB' if extra >= 1024 and extra % 1024 == 0
                                        else f'+{extra} KB')
    return f'the {heap.selected:,}-byte player memory ({level})'


def need_text(c: game_memory.CharacterNeed) -> str:
    return (f'High {c.high:,} + Low {c.low:,} + bat/glove {c.gear:,} + {game_memory.BLOCK_OVERHEAD} = '
            f'{c.need:,} bytes')


def over_text(name: str, c: game_memory.CharacterNeed, heap: game_memory.PlayerHeap) -> str | None:
    """The line for a character that does not fit the selected level's heap, or None when it fits."""
    over = c.need - heap.selected_usable
    if over <= 0:
        return None
    return f'{name} needs {need_text(c)}: {over:,} bytes more than {heap_text(heap)}.'


def _files(raw: dict, root_dir: str) -> tuple[dict[int, int], list[str]]:
    """A projected slot's ``{index: length}`` (build refs resolved) and the ``.sluggie`` files whose size is not
    known."""
    files, unknown = {}, []
    for index, value in (raw or {}).items():
        if isinstance(value, dict) and BUILD in value:
            length = lookup_size(root_dir, value[BUILD])
            if length is None:
                unknown.append(value[BUILD])
            files[int(index)] = length
        else:
            files[int(index)] = value
    return files, unknown


def evaluate(section: dict, heap: game_memory.PlayerHeap, root_dir: str) -> tuple[list[dict], list[str]]:
    """``(problems, notes)`` of a projection (``Roster/slot_memory.project``: ``{"0xNN": {"name", "before",
    "after"}}``) against ``heap``: one problem per slot that will not fit (``{"id", "name", "need", "over",
    "text"}``), notes for slots whose size is not known (a block nobody built yet: not checked)."""
    problems, notes = [], []
    for key, slot in sorted((section or {}).items()):
        after, unknown = _files(slot.get('after'), root_dir)
        name = slot.get('name') or key
        if unknown:
            notes.append(f'{name}: player memory not checked, the size of '
                         f'{", ".join(os.path.basename(p) for p in unknown)} is not known (no build check)')
            continue
        c = game_memory.need_of(after)
        text = over_text(name, c, heap)
        if text:
            problems.append({'id': key, 'name': name, 'need': c.need, 'over': c.need - heap.selected_usable,
                             'text': text})
    return problems, notes


def option_problems(current: bytes, new: bytes, names: dict[int, str]) -> list[str]:
    """Lines for the characters that fit the player memory ``current`` (a ``main.dol``) selects but not the one
    ``new`` selects (a lower level; characters over it already are not counted)."""
    try:
        from .Dol import dolfile
    except ImportError:
        from Dol import dolfile
    try:
        before, after = dolfile.DolImage(current), dolfile.DolImage(new)
        old_heap, heap = game_memory.player_heap(before), game_memory.player_heap(after)
        if heap.selected >= old_heap.selected:
            return []
        chars = game_memory.characters(after)
    except Exception:            # noqa: BLE001 - an unreadable DOL is left to the options' own checks
        return []
    out = []
    for directory, c in sorted(chars.items()):
        if c.need <= old_heap.selected_usable:
            text = over_text(game_memory.describe(directory, names), c, heap)
            if text:
                out.append(text)
    return out


# --------------------------------------------------------------------------
# Record (dry runs), ask (writing runs)
# --------------------------------------------------------------------------

def check_path(root_dir: str) -> str:
    """Where dry runs record what they found for the GUI's dialogs (``_gui/memory_check.json``)."""
    return os.path.join(root_dir, GUI_FOLDER, CHECK_FILE)


def clear_check(root_dir: str) -> None:
    try:
        os.remove(check_path(root_dir))
    except OSError:
        pass


def record(root_dir: str, what: str, problems: list[dict]) -> None:
    """A dry run found characters over the player memory: log them and record them for the GUI."""
    for p in problems:
        slogger.warning(f'Player memory ({what}): {p["text"]}', source=SOURCE)
    path = check_path(root_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'over': True, 'what': what, 'problems': problems}, f, ensure_ascii=False)


def read_details(root_dir: str) -> dict:
    """What the last dry run recorded (``{'over': True, 'problems': [...]}``), ``{}`` when everything fits."""
    try:
        with open(check_path(root_dir), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get('over') else {}


def accepted() -> bool:
    return bool(os.environ.get(ACCEPT_ENV))


def accept() -> None:
    """The user agreed: the commands this process runs from now on (its children) don't ask again."""
    os.environ[ACCEPT_ENV] = '1'


def confirm(what: str, lines: list[str]) -> bool:
    """Ask before writing characters over the player memory (GUI: a ``danger`` popup that is never skipped). True
    when the user agreed or had already agreed (:data:`ACCEPT_ENV`); a closed input counts as no."""
    if accepted():
        slogger.warning(f'Player memory: written over it for {what} (accepted earlier): ' + ' '.join(lines),
                        source=SOURCE)
        return True
    try:
        answer = slogger.ask('Player memory: ' + ' '.join(lines) + '\n' + QUESTION_TAIL, kind='danger')
    except EOFError:
        answer = ''
    if answer.strip().lower() in ('y', 'yes'):
        slogger.warning(f'Player memory: written over it for {what} (confirmed)', source=SOURCE)
        accept()
        return True
    slogger.error(f'Cancelled: {what} would leave a character over the player memory; nothing was written.',
                  source=SOURCE)
    return False


def guard(section: dict | None, what: str, root_dir: str, dry_run: bool = False) -> bool:
    """The check of a planned chain's projection, after its build checks and before its first write: True to go
    on. A dry run only records what it found; a writing run asks once (or goes on when accepted)."""
    if not section:
        return True
    dol_path = os.path.join(root_dir, game_memory.OUTPUT_FOLDER, 'main.dol')
    try:
        heap = game_memory.player_heap(game_memory.load(dol_path))
    except Exception as exc:     # noqa: BLE001 - no readable DOL: the chain's own steps fail on it
        slogger.warning(f'Player memory not checked: {dol_path} cannot be read ({exc})', source=SOURCE)
        return True
    problems, notes = evaluate(section, heap, root_dir)
    for note in notes:
        slogger.warning(note, source=SOURCE)
    if not problems:
        return True
    if dry_run:
        record(root_dir, what, problems)
        return True
    return confirm(what, [p['text'] for p in problems])
