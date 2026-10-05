"""Targeted patches: a character's model written into another character's slot.

GUI character grid, Phase 4a. A ``.sluggie`` is still built from its own
donor (``ChunkNumber``/``FileIndex`` in ``1_Input``); only the route it is
written to changes. The target is a character ID (``--target-id 0xNN``),
resolved on the output DOL by ``Roster/slots.py``; the model's role picks the
file: a high-poly model goes to file 0, its ``L_`` partner to file 1. Every
step that reads or writes a route (``zeroOriginalModel``, the sharers, the
LOD partner checks, ``--unpatch``) uses the target, never the source, whose
own route still belongs to its own character.

Rules (refused with ``TargetError`` unless noted):

* Only character models: the source directory in the character range
  (18-118), the target there too or a new ID's own model directory (roster
  ``model_dirs``), file 0 or 1, and the block's role must match the file it
  came from.
* No container: a source whose DOL entry carries bytes around the model
  (an archive or prefix) cannot move to another route.
* **Skeleton guard**: the target directory's animations were made for its
  own skeleton, so the source's vanilla skeleton must have the target's bone
  count and parent chain (an own model directory: its source's skeleton,
  ``hh.vanillaRoute``). Bones the
  ``.sluggie`` adds come after them and are fine. Different rest poses are
  only a warning.
* **``L_`` alone:** the slot's current high-poly model must be the ``L_``
  model's own partner (same geo-name stem). ``L_`` models bind textures by
  index into their high-poly partner's TEX (Dolphin, 2026-09-27), so an
  ``L_`` under another character's HP would read the wrong textures.
* **HP as the low variant** (``as_low``, for an HP without an ``L_``
  partner): the HP block is written to file 1 as well, as a copy of its own.
* An HP whose slot keeps another character's ``L_`` is allowed, with a
  warning (patch the ``L_`` partner next, or use ``as_low``).
"""

from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass

import HammerspaceHelper as hh
import LodPartnerGuard

_ROSTER_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'Roster'))

HIGH_FILE, LOW_FILE = 0, 1
CHARACTER_FILES = (HIGH_FILE, LOW_FILE)


class TargetError(ValueError):
    pass


def _roster():
    """``Roster/slots.py`` and ``ids`` (loaded on first use: only targeted patches need them)."""
    if _ROSTER_DIR not in sys.path:
        sys.path.insert(0, _ROSTER_DIR)
    import ids
    import slots
    return slots, ids


def character_dirs() -> range:
    _slots, ids = _roster()
    return range(ids.MODEL_DIR_BASE, ids.MODEL_DIR_BASE + ids.STOCK_IDS)


@dataclass(frozen=True)
class Target:
    chunk_number: int
    file_index: int
    character_id: int | None = None
    as_low: bool = False

    @property
    def route(self) -> tuple[int, int]:
        return self.chunk_number, self.file_index

    @property
    def low_route(self) -> tuple[int, int]:
        return self.chunk_number, LOW_FILE

    def describe(self) -> str:
        who = f'0x{self.character_id:02X} ' if self.character_id is not None else ''
        return f'{who}(chunk {self.chunk_number}, file {self.file_index})'


def resolve_dir(character_id: int | str) -> tuple[int, int]:
    """``(character id, model directory)`` of a slot, read from the output DOL (input DOL before any output)."""
    slots, _ids = _roster()
    from Dol import dolfile
    try:
        cid = slots.parse_id(character_id)
        dol_path = hh.OUTPUT_DOL if os.path.exists(hh.OUTPUT_DOL) else hh.INPUT_DOL
        with open(dol_path, 'rb') as dol:
            image = dolfile.DolImage(dol.read())
        return cid, slots.model_dir(image, cid)
    except slots.SlotError as exc:
        raise TargetError(str(exc)) from exc


def role_file(file_index: int) -> int:
    """The file a model of this role goes to (characters keep HP at 0, ``L_`` at 1)."""
    if file_index not in CHARACTER_FILES:
        raise TargetError(f'file {file_index} is not a character model (only file 0, the high-poly model, '
                          'and file 1, its L_ partner, can go into a slot)')
    return file_index


def make_target(character_id: int | str, source: tuple[int, int], as_low: bool = False) -> Target | None:
    """The target for a ``.sluggie`` exported from ``source``; None when the slot is the source's own route."""
    cid, chunk = resolve_dir(character_id)
    file_index = role_file(source[1])
    if as_low and file_index != HIGH_FILE:
        raise TargetError('only a high-poly model can be used as the low variant too')
    if (chunk, file_index) == tuple(source) and not as_low:
        return None
    return Target(chunk, file_index, cid, as_low)


def _vanilla_summary(route: tuple[int, int]) -> LodPartnerGuard.ActSummary | None:
    route = hh.vanillaRoute(*route)
    if route is None:
        return None
    offset, length = hh.readDolEntry(*route)
    if offset == -1 or length <= 0:
        return None
    with open(hh.INPUT_DAT, 'rb') as dat:
        dat.seek(offset)
        return LodPartnerGuard.act_summary(dat.read(length))


def skeleton_problems(source: LodPartnerGuard.ActSummary, target: LodPartnerGuard.ActSummary) -> tuple[list[str], list[str]]:
    """(errors, warnings) for posing *source*'s skeleton with *target*'s animations."""
    if source.bone_count != target.bone_count:
        return [f'{source.geo_name} has {source.bone_count} bones, {target.geo_name} {target.bone_count}'], []
    parents = [b for b in range(source.bone_count) if source.parents.get(b) != target.parents.get(b)]
    if parents:
        shown = ', '.join(f'bone {b} (parent {source.parents.get(b)} vs {target.parents.get(b)})'
                          for b in parents[:4])
        more = f' and {len(parents) - 4} more' if len(parents) > 4 else ''
        return [f'the parent chains differ: {shown}{more}'], []
    moved = [b for b in range(source.bone_count)
             if not LodPartnerGuard.srt_matches(source.srts.get(b), target.srts.get(b))]
    if moved:
        return [], [f'{len(moved)} of {source.bone_count} bones of {source.geo_name} rest differently from '
                    f'{target.geo_name}\'s (e.g. bone {moved[0]}); the slot animates them with '
                    f'{target.geo_name}\'s animations']
    return [], []


def check(block: bytes, source: tuple[int, int], target: Target, report: dict | None = None) -> list[str]:
    """Refuse (``TargetError``) a block that may not go to *target*; returns warnings (module docstring)."""
    chars = character_dirs()
    if source[0] not in chars:
        raise TargetError(f'chunk {source[0]} is not a character directory ({chars.start}-{chars.stop - 1}): '
                          'stadiums, props and bats cannot go into a slot')
    if target.chunk_number not in chars and not hh.isOwnDir(target.chunk_number):
        raise TargetError(f'chunk {target.chunk_number} is not a character directory')
    role_file(source[1])
    role_file(target.file_index)
    if report and ('container_prefix_size' in report or 'archive_member_slot' in report):
        raise TargetError('the source model sits inside a container entry and cannot move to another route')

    own = LodPartnerGuard.act_summary(block)
    if own is None:
        raise TargetError('the built block has no readable ACT section')
    if own.is_low_poly != (source[1] == LOW_FILE):
        raise TargetError(f'{own.geo_name} is not the {"low" if source[1] else "high"}-poly model of '
                          f'chunk {source[0]}')

    # Same role on both sides: a high-poly model written as the low variant
    # too is still posed (and animated) as the slot's high-poly model.
    problems, warnings = _skeleton(source, (target.chunk_number, source[1]))
    if problems:
        raise TargetError(
            f'the skeletons do not match ({problems[0]}). A stock slot keeps its own animations, so only a '
            'model with the same skeleton fits; put this model on a new ID instead.')

    if own.is_low_poly:
        high = LodPartnerGuard.read_current_block(target.chunk_number, HIGH_FILE)
        high_summary = LodPartnerGuard.act_summary(high) if high else None
        if high_summary is None or high_summary.is_low_poly or high_summary.stem != own.stem:
            current = high_summary.geo_name if high_summary else 'nothing readable'
            raise TargetError(
                f'{own.geo_name} binds its textures by index into its own high-poly model, but the slot '
                f'{target.describe()} currently has {current} there. Patch the high-poly model into the '
                'slot first (or use the high-poly model as the low variant too).')
    elif not target.as_low:
        low = LodPartnerGuard.read_current_block(target.chunk_number, LOW_FILE)
        low_summary = LodPartnerGuard.act_summary(low) if low else None
        if low_summary is not None and low_summary.stem != own.stem:
            warnings.append(
                f'the slot\'s low-poly model is still {low_summary.geo_name}, which binds textures into '
                f'{own.geo_name}\'s TEX by index; patch {own.geo_name}\'s L_ partner into the slot next, '
                'or use the high-poly model as the low variant too')
    return warnings


def _skeleton(source: tuple[int, int], target: tuple[int, int]) -> tuple[list[str], list[str]]:
    try:
        src, dst = _vanilla_summary(source), _vanilla_summary(target)
    except (OSError, struct.error, ValueError) as exc:
        raise TargetError(f'could not read the skeletons to compare: {exc}') from exc
    if src is None or dst is None:
        raise TargetError('could not read the skeletons to compare (no ACT section in a vanilla block)')
    return skeleton_problems(src, dst)
