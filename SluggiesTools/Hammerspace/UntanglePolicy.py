"""Which DOL routes the untangler splits from their playable owners.

The unused characters (model dirs 89-94) ship without data blocks of their
own: every route in those directories points at a playable character's block
in the vanilla DOL. ``export.py --untangle`` gives each of them a verbatim
hammerspace copy, so that it can be edited independently (Dolphin-confirmed
2026-09-27: the game loads each split route's own copy, and the owner is
unaffected).

Everything here is a pure function of the INPUT ``main.dol``, so no state file
is needed:

- a **split route** is any non-empty route in an unused-character directory;
- its **owners** are the routes outside those directories that share its
  vanilla offset.

This module is the one home of the directory list. ``export.py`` and
``start.py`` import it. ``HammerspaceHelper`` is imported lazily, so the
dispatcher can use the list without pulling in the helper.
"""
from __future__ import annotations

#: Model directory indices of the six unused characters (character IDs
#: 0x47-0x4C; directory = ID + 18). See ``export.folderNameMap``.
UNUSED_CHARACTER_DIRS: tuple[int, ...] = (89, 90, 91, 92, 93, 94)


def _helper():
    """HammerspaceHelper, whether this module was loaded top-level or as
    ``Hammerspace.UntanglePolicy`` (the icon tools' package import)."""
    if __package__:
        from . import HammerspaceHelper
    else:
        import HammerspaceHelper
    return HammerspaceHelper


def is_split_dir(chunk_number) -> bool:
    """True when ``chunk_number`` is an unused-character directory."""
    return chunk_number in UNUSED_CHARACTER_DIRS


def _input_routes() -> dict[tuple[int, int], tuple[int, int]]:
    """Map ``(chunk, file_index)`` to the INPUT DOL's en ``(offset, length)``."""
    hh = _helper()

    return {
        (chunk, file_index): (words[2], words[1])
        for chunk, file_index, _record, words in hh._iterDirRecords(hh.INPUT_DOL)
    }


def split_routes() -> list[tuple[int, int]]:
    """Every split route, in the order the untangle export clones them."""
    routes = _input_routes()
    return [
        route
        for chunk in UNUSED_CHARACTER_DIRS
        for route in sorted(r for r in routes if r[0] == chunk)
        if routes[route][1] > 0
    ]


def is_split(chunk_number: int, file_index: int) -> bool:
    """True when the route is in an unused-character dir and holds data."""
    if not is_split_dir(chunk_number):
        return False
    route = _input_routes().get((chunk_number, file_index))
    return route is not None and route[1] > 0


def independent_sharers(chunk_number: int, file_index: int, sharers) -> list[tuple[int, int]]:
    """Filter ``findSharedEntries`` output down to routes that move with this one.

    A split route moves alone: its owners and the other unused characters on
    the same block keep their routes. An owner never drags a split route
    along either. Sharing between two non-split routes is left as it is.
    Pure on chunk numbers, so it also holds on an output where an older
    unpatch put a split route back onto the shared block.
    """
    if is_split_dir(chunk_number):
        return []
    return [route for route in sharers if not is_split_dir(route[0])]


def owner_routes(chunk_number: int, file_index: int) -> list[tuple[int, int]]:
    """Routes outside the unused-character dirs that share this route's vanilla offset.

    Empty for a route that is not split. The 64-byte file-5 placeholder at
    ``0x4B33140`` is shared by about 60 character dirs, so a placeholder
    route returns all of them.
    """
    if not is_split(chunk_number, file_index):
        return []
    routes = _input_routes()
    offset = routes[(chunk_number, file_index)][0]
    return sorted(
        route for route, (route_offset, length) in routes.items()
        if length > 0 and route_offset == offset and not is_split_dir(route[0])
    )


def retangled_routes() -> list[tuple[int, int]]:
    """Split routes that the OUTPUT DOL routes outside hammerspace.

    That happens after an unpatch by an older toolchain, or when an output
    was never untangled. Each such route reads its owner's block again.
    """
    hh = _helper()

    result = []
    for chunk, file_index in split_routes():
        offset, _length = hh.readOutputDolEntry(chunk, file_index)
        if offset == -1:
            return []   # no output DOL yet: nothing to repair
        if offset < hh.BASE_SIZE:
            result.append((chunk, file_index))
    return result


def resplit_retangled(find_model=None) -> tuple[list, list]:
    """Give every re-tangled split route a fresh baseline copy of its own.

    The baseline re-applies the untangled textures named by the route's
    exported `.sluggie` (``find_model(chunk, file_index)``, by default a search
    of ``2_Output_Models``); without one it is the vanilla bytes verbatim.
    Returns ``(repaired, failed)`` route lists. Owners are never touched.
    """
    hh = _helper()
    if __package__:
        from . import UntangledTextures
    else:
        import UntangledTextures
    find_model = find_model or UntangledTextures.find_sluggie_model

    repaired, failed = [], []
    for route in retangled_routes():
        baseline, _notes = UntangledTextures.split_baseline(*route, find_model(*route))
        success, _offset, _length = hh.restoreSplitBaseline(*route, baseline)
        (repaired if success else failed).append(route)
    return repaired, failed


if __name__ == '__main__':
    import sys

    import HammerspaceHelper  # noqa: F401 -- puts SluggiesTools on sys.path, configures logging
    import slogger as _slogger

    _repaired, _failed = resplit_retangled()
    if _repaired:
        _slogger.info(
            f'Re-split {len(_repaired)} unused-character route(s) from their playable owners: '
            + ', '.join(f'({c},{i})' for c, i in _repaired),
            source='untangle.policy',
        )
    else:
        _slogger.info(
            'Every unused-character route already has its own block; nothing to re-split.',
            source='untangle.policy',
        )
    if _failed:
        _slogger.error(
            'Could not re-split: ' + ', '.join(f'({c},{i})' for c, i in _failed),
            source='untangle.policy',
        )
    sys.exit(1 if _failed else 0)
