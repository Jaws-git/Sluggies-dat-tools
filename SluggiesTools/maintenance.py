"""Maintenance checks: problems in the working folders that make the tools act on the wrong file, and models of
the output game that are too big for the memory the game gives them (``game_memory``).

No Dear PyGui here; the GUI's Maintenance tab (``gui_maintenance``) runs :func:`scan` on a worker thread and
lists what it returns. Each check is a function ``(root_dir) -> list[Problem]`` in :data:`CHECKS`.

Duplicate models: a ``.sluggie`` is one model of the game, named by ``ChunkNumber`` (model directory),
``FileIndex`` and ``ModelOffset`` (archive containers hold several models under one directory file). Two
``.sluggie`` files with the same three (a copied model folder, or "x - Copy.sluggie" next to the original) leave
the patcher guessing: a bare file name finds several files, and the PNG and untangled-texture
lookups take whichever they meet first. The export itself writes some blocks twice, once per directory that shares
them (map objects, "Various A/B"): those files share a name and offset but not the directory, so they are not
reported. Slot exports (``Roster/slot_export.py``, "Custom <name> NN" folders) carry the identity of the model they
started from on purpose, with a name of their own and their own donor block: they are not reported either.
"""

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass

MODELS_FOLDER = '2_Output_Models'

# The export writes these three keys first; reading the head of the file avoids parsing every model in full.
_IDENTITY = re.compile(r'"ChunkNumber"\s*:\s*(-?\d+)\s*,\s*"FileIndex"\s*:\s*(-?\d+)\s*,'
                       r'\s*"ModelOffset"\s*:\s*"([^"]*)"')
_HEAD_BYTES = 4096
# Slot exports write this key right after ModelLength (Roster/slot_export.py META_KEY).
_SLOT_EXPORT = re.compile(r'"ModelLength"\s*:\s*\d+\s*,\s*"SlotExport"\s*:')


@dataclass(frozen=True)
class Problem:
    check: str               # the check's title, e.g. "Duplicate models"
    summary: str             # one line: what is wrong
    paths: tuple             # the files involved, absolute
    advice: str              # what to do about it


def model_identity(path):
    """``(ChunkNumber, FileIndex, ModelOffset)`` of a ``.sluggie``, or None when the file cannot be read or lacks
    them."""
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            head = handle.read(_HEAD_BYTES)
            match = _IDENTITY.search(head)
            if match:
                return int(match.group(1)), int(match.group(2)), match.group(3).lower()
            handle.seek(0)
            model = json.load(handle)['SluggiesModel']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    chunk, index, offset = model.get('ChunkNumber'), model.get('FileIndex'), model.get('ModelOffset')
    if isinstance(chunk, int) and isinstance(index, int) and isinstance(offset, str):
        return chunk, index, offset.lower()
    return None


def is_slot_export(path):
    """Whether a ``.sluggie`` is a slot export (read from the file head)."""
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return _SLOT_EXPORT.search(handle.read(_HEAD_BYTES)) is not None
    except (OSError, ValueError):
        return False


def _sluggies(models_dir):
    return sorted(os.path.join(root, f) for root, _dirs, files in os.walk(models_dir)
                  for f in files if f.lower().endswith('.sluggie'))


def duplicate_models(root_dir):
    """One problem per model with more than one ``.sluggie`` (same :func:`model_identity`), plus one
    per name shared with a ``.sluggie`` whose model cannot be read."""
    models_dir = os.path.join(root_dir, MODELS_FOLDER)
    by_model, by_name, unreadable = defaultdict(list), defaultdict(list), []
    for path in _sluggies(models_dir):
        if is_slot_export(path):
            continue
        by_name[os.path.basename(path)].append(path)
        identity = model_identity(path)
        if identity is None:
            unreadable.append(path)
        else:
            by_model[identity].append(path)

    advice = (f'Keep one file: delete the others or move them out of {MODELS_FOLDER} '
              '(a backup folder outside it keeps them safe).')
    problems = []
    for (chunk, index, offset), paths in sorted(by_model.items()):
        if len(paths) > 1:
            problems.append(Problem(
                'Duplicate models',
                f'{len(paths)} files hold the same model (directory {chunk}, file {index}, offset {offset}); '
                'the patcher cannot tell which one you mean.',
                tuple(paths), advice))
    for name in sorted({os.path.basename(path) for path in unreadable}):
        paths = by_name[name]
        if len(paths) > 1:
            problems.append(Problem(
                'Duplicate models',
                f"'{name}' exists {len(paths)} times and at least one copy cannot be read.",
                tuple(paths), advice))
    return problems


OUTPUT_FOLDER = '3_Output_Dat'


def _output_game(root_dir):
    """The output ``main.dol`` read for the memory checks, or None when there is none yet."""
    try:                             # here, so the duplicate check works without the game modules
        from SluggiesTools import game_memory
    except ImportError:
        import game_memory
    path = os.path.join(root_dir, OUTPUT_FOLDER, 'main.dol')
    return (game_memory, game_memory.load(path), path) if os.path.isfile(path) else None


def _model_paths(root_dir, names, directory, fallback):
    folder = names.get(directory)
    path = os.path.join(root_dir, MODELS_FOLDER, folder) if folder else None
    return (path,) if path and os.path.isdir(path) else (fallback,)


def player_memory(root_dir):
    """One problem per character whose High + Low + bat/glove don't fit the player memory of the output game."""
    game = _output_game(root_dir)
    if game is None:
        return []
    gm, image, dol_path = game
    heap, names = gm.player_heap(image), gm.folder_names(root_dir)
    problems = []
    for directory, c in sorted(gm.characters(image).items()):
        text = gm.problem_text(c, heap, gm.describe(directory, names))
        if text:
            advice = ('Make its textures smaller (or fewer), or raise the player memory on the Options tab.'
                      if heap.big_size is None else
                      'Play it only in Dolphin with the 128 MB MEM2 override, or make its textures smaller.')
            problems.append(Problem('Player memory', text, _model_paths(root_dir, names, directory, dol_path),
                                    advice))
    return problems


def stadium_memory(root_dir):
    """One problem per stadium file bigger than the largest stock stadium model (untested territory)."""
    game = _output_game(root_dir)
    if game is None:
        return []
    gm, image, dol_path = game
    names = gm.folder_names(root_dir)
    problems = []
    for directory, files in sorted(gm.stadiums(image).items()):
        for index, length in sorted(files.items()):
            if length > gm.STADIUM_TESTED_MAX:
                problems.append(Problem(
                    'Stadium memory',
                    f'{gm.describe(directory, names)} file {index} is {length:,} bytes, '
                    f'{length - gm.STADIUM_TESTED_MAX:,} more than the largest stock stadium model '
                    f'(Wario City, {gm.STADIUM_TESTED_MAX:,}).',
                    _model_paths(root_dir, names, directory, dol_path),
                    'A stadium has no fixed size limit: the extra bytes come out of the game memory every match '
                    'shares (about 4 MB free at the first pitch in Wario City). Sizes past the stock maximum are '
                    'untested; test a full match with several runs scored.'))
    return problems


CHECKS = (
    ('Duplicate models', duplicate_models),
    ('Player memory', player_memory),
    ('Stadium memory', stadium_memory),
)


def scan(root_dir):
    """Run every check; returns ``(problems, errors)``, ``errors`` being ``(check title, message)`` for a check
    that failed instead of finishing."""
    problems, errors = [], []
    for title, check in CHECKS:
        try:
            problems.extend(check(root_dir))
        except Exception as exc:             # a broken check must not hide the others' results
            errors.append((title, f'{type(exc).__name__}: {exc}'))
    return problems, errors
