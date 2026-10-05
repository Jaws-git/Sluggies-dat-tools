"""Cross-model guard between a character's high-poly model and its low-poly
(``L_*``) partner.

A low-poly model may only draw geometry on a bone that its high-poly partner
also has. Confirmed in Dolphin on 2026-09-27 with Mario:

- ``L_mario`` with a new bone 91 (HP Mario still at 91 bones, 0-90) and a
  custom submesh on existing bone 49: loads.
- the same, but the custom submesh on the new bone 91: crashes on model load.
- bone 91 added to HP Mario too, ``L_mario``'s submesh on bone 91: loads.

The game poses both models of a pair from the high-poly skeleton, so a bone
past its end is harmless until something is drawn with it. The reverse
direction is fine: the high-poly model drawing on a new bone its low-poly
partner lacks was Dolphin-verified on 2026-09-18.

The low-poly model's own placement of a bone is ignored. Confirmed in Dolphin
on 2026-09-27: with bone 91 moved to HP Mario's arm while ``L_mario``'s bone
91 stayed on the head, ``L_mario``'s submesh on bone 91 followed the arm. A
new bone whose parent or SRT differs between the two models is therefore
reported as a (non-fatal) warning.

The two models are exported and patched independently, so neither
``.sluggie`` knows the other's state. This module reads the partner's
*current* block (the output DOL route, or the input files before anything
has been patched) and checks the pair as it would stand after the write.

Partners are the two entries of one chunk at file indices ``n`` (high-poly)
and ``n + 1`` (low-poly), confirmed by their ACT geo names: stripping the
``L_`` prefix and the extension must give the same stem. Names are read
through ``binfmt.clean_geo_name`` (some game strings carry a leftover byte,
e.g. the Mii ``mii_male.gplp``). All 66 vanilla pairs follow this layout.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

import HammerspaceHelper as hh
import slogger as _slogger
from binfmt import clean_geo_name
from act_rebuild import BONE_RECORD_SIZE, HEADER_SIZE as ACT_HEADER_SIZE, SRT_RECORD_SIZE

_GEO_ID_NONE = 0xFFFF
_LOW_POLY_PREFIX = 'L_'
_SRT_FORMAT = '>B3x3f4f3f'  # type, scale, raw quaternion, translation (act_rebuild.pack_srt_blob)
# The Blender exporter rounds SRT components to 6 decimals, so a bone placed
# identically in both models can still differ by float noise.
_SRT_TOLERANCE = 1e-4


@dataclass(frozen=True)
class ActSummary:
    geo_name: str
    bone_count: int
    drawn_bones: dict[int, int]  # bone id -> GeoId, for bones that own a submesh
    parents: dict[int, int | None]  # bone id -> parent bone id (None for a root)
    srts: dict[int, tuple | None]  # bone id -> unpacked _SRT_FORMAT, or None without an SRT

    @property
    def is_low_poly(self) -> bool:
        return self.geo_name.startswith(_LOW_POLY_PREFIX)

    @property
    def stem(self) -> str:
        name = self.geo_name[len(_LOW_POLY_PREFIX):] if self.is_low_poly else self.geo_name
        return name.split('.', 1)[0]


def act_summary(block: bytes) -> ActSummary | None:
    """Geo name, bone count and geometry-owning bones of a model block's ACT
    section, or None when the block has no readable ACT section."""
    if len(block) < 0x20:
        return None
    act = struct.unpack_from('>I', block, 0x08)[0]
    if not act or act + ACT_HEADER_SIZE > len(block):
        return None
    bone_count = struct.unpack_from('>H', block, act + 0x06)[0]
    geo_name_ptr = struct.unpack_from('>I', block, act + 0x10)[0]
    table_end = act + ACT_HEADER_SIZE + bone_count * BONE_RECORD_SIZE
    if table_end > len(block) or not geo_name_ptr or act + geo_name_ptr >= len(block):
        return None

    name_start = act + geo_name_ptr
    name_end = block.find(b'\x00', name_start)
    if name_end == -1:
        return None
    geo_name = clean_geo_name(block[name_start:name_end].decode('latin-1'))

    drawn, parents, srts = {}, {}, {}
    for index in range(bone_count):
        record = act + ACT_HEADER_SIZE + index * BONE_RECORD_SIZE
        orientation_ptr, _prev, _next, parent_ptr, _child, geo_id, bone_id = struct.unpack_from(
            '>IIIIIHH', block, record,
        )
        if geo_id != _GEO_ID_NONE:
            drawn[bone_id] = geo_id
        parents[bone_id] = (parent_ptr - ACT_HEADER_SIZE) // BONE_RECORD_SIZE if parent_ptr else None
        srt_at = act + orientation_ptr
        srts[bone_id] = (
            struct.unpack_from(_SRT_FORMAT, block, srt_at)
            if orientation_ptr and srt_at + SRT_RECORD_SIZE <= len(block) else None
        )
    return ActSummary(geo_name, bone_count, drawn, parents, srts)


def read_current_block(chunk_number: int, file_index: int) -> bytes | None:
    """The model block the game currently loads for an entry: the output DOL
    route into the output DAT, or the input files when nothing is patched yet.
    Returns None for an entry that does not exist."""
    if os.path.exists(hh.OUTPUT_DOL) and os.path.exists(hh.OUTPUT_DAT):
        offset, length = hh.readOutputDolEntry(chunk_number, file_index)
        dat_path = hh.OUTPUT_DAT
    else:
        offset, length = hh.readDolEntry(chunk_number, file_index)
        dat_path = hh.INPUT_DAT
    if offset == -1 or length <= 0:
        return None
    with open(dat_path, 'rb') as dat:
        dat.seek(offset)
        block = dat.read(length)
    return block if len(block) == length else None


def _entry_exists(chunk_number: int, file_index: int) -> bool:
    """Whether the chunk has a model entry at this index (entries are a
    contiguous run of 48-byte records tagged with the DAT filename pointer).
    The DOL lays the chunks' runs out back to back, so an index past the end
    of this chunk's run would read the next chunk's first entry; the next
    directory pointer bounds it."""
    has_output = os.path.exists(hh.OUTPUT_DOL)
    dir_ptrs = hh._outputDirPtrs() if has_output else hh._readDirPtrs()
    if not (0 <= chunk_number < len(dir_ptrs)) or file_index < 0:
        return False
    entry = dir_ptrs[chunk_number] + file_index * hh._ENTRY_SIZE
    later_runs = [ptr for ptr in dir_ptrs if ptr > dir_ptrs[chunk_number]]
    if later_runs and entry + hh._ENTRY_SIZE > min(later_runs):
        return False
    dol_path = hh.OUTPUT_DOL if has_output else hh.INPUT_DOL
    with open(dol_path, 'rb') as dol:
        dol.seek(entry)
        raw = dol.read(4)
    return len(raw) == 4 and struct.unpack('>I', raw)[0] == hh._DAT_FNAME_PTR


def find_partner(
    chunk_number: int, file_index: int, own: ActSummary, any_stem: bool = False,
) -> tuple[int, ActSummary] | None:
    """``(file_index, summary)`` of the other model of a high-/low-poly pair,
    or None when the model has no partner. ``any_stem``: pair by position
    only (a model patched into another character's slot, SlotTarget, pairs
    with that slot's other model whatever its name)."""
    partner_index = file_index + (-1 if own.is_low_poly else 1)
    if not _entry_exists(chunk_number, partner_index):
        return None
    block = read_current_block(chunk_number, partner_index)
    partner = act_summary(block) if block else None
    if partner is None or partner.is_low_poly == own.is_low_poly or (partner.stem != own.stem and not any_stem):
        return None
    return partner_index, partner


def _pair(
    new_block: bytes, chunk_number: int, file_index: int, any_stem: bool = False,
) -> tuple[ActSummary, int, ActSummary] | None:
    """``(own, partner_index, partner)`` for *new_block* as this entry, or
    None when the model has no partner or the partner cannot be read (logged)."""
    own = act_summary(new_block)
    if own is None:
        return None
    try:
        found = find_partner(chunk_number, file_index, own, any_stem)
    except (OSError, struct.error, ValueError) as exc:
        _slogger.warning(
            f'[LOD] could not read the high-/low-poly partner of {own.geo_name} '
            f'(chunk {chunk_number}): {exc}; partner bone check skipped',
            source='hammerspace.main',
        )
        return None
    if found is None:
        return None
    return (own, *found)


def lod_partner_errors(new_block: bytes, chunk_number: int, file_index: int, any_stem: bool = False) -> list[str]:
    """Blocking errors for making *new_block* the live model of this entry,
    given the partner model's current state. Empty when the pair stays valid,
    the model has no partner, or the partner cannot be read (logged)."""
    pair = _pair(new_block, chunk_number, file_index, any_stem)
    if pair is None:
        return []
    own, partner_index, partner = pair

    high, low = (partner, own) if own.is_low_poly else (own, partner)
    missing = sorted(bone for bone in low.drawn_bones if bone >= high.bone_count)
    if not missing:
        return []

    bones = ', '.join(f'{bone} (submesh {low.drawn_bones[bone]})' for bone in missing)
    high_range = f'{high.bone_count} bones, 0-{high.bone_count - 1}'
    if own.is_low_poly:
        return [
            f'{low.geo_name} draws geometry on bone(s) {bones}, but its high-poly '
            f'partner {high.geo_name} (chunk {chunk_number}, file {partner_index}) '
            f'currently has only {high_range}. The game poses both models from the '
            'high-poly skeleton, so this crashes on model load. Add the same bone(s) '
            f'to {high.geo_name} and patch it first, or host the geometry on an '
            'existing bone.'
        ]
    return [
        f'This {high.geo_name} would have only {high_range}, but its low-poly '
        f'partner {low.geo_name} (chunk {chunk_number}, file {partner_index}) '
        f'currently draws geometry on bone(s) {bones}. The game poses both models '
        'from the high-poly skeleton, so that would crash on model load. Keep those '
        f'bone(s) in {high.geo_name}, or first re-patch {low.geo_name} with its '
        'geometry on an existing bone.'
    ]


def srt_matches(a: tuple | None, b: tuple | None) -> bool:
    if a is None or b is None:
        return a is b
    return a[0] == b[0] and all(abs(x - y) <= _SRT_TOLERANCE for x, y in zip(a[1:], b[1:]))


def _vanilla_block(chunk_number: int, file_index: int) -> bytes | None:
    """The INPUT block an entry started from (an own model directory: its source's, ``hh.vanillaRoute``)."""
    route = hh.vanillaRoute(chunk_number, file_index)
    if route is None or not os.path.exists(hh.INPUT_DAT):
        return None
    offset, length = hh.readDolEntry(*route)
    if offset == -1 or length <= 0:
        return None
    with open(hh.INPUT_DAT, 'rb') as dat:
        dat.seek(offset)
        return dat.read(length)


def _vanilla_bone_count(chunk_number: int, file_index: int) -> int | None:
    block = _vanilla_block(chunk_number, file_index)
    summary = act_summary(block) if block else None
    return summary.bone_count if summary else None


def lod_partner_warnings(new_block: bytes, chunk_number: int, file_index: int, any_stem: bool = False) -> list[str]:
    """Non-fatal warnings for added bones (ids past the vanilla skeleton,
    present in both models) whose parent or SRT differs between the pair:
    the low-poly model is posed with the high-poly model's bone, so its own
    placement of that bone has no effect."""
    pair = _pair(new_block, chunk_number, file_index, any_stem)
    if pair is None:
        return []
    own, partner_index, partner = pair
    try:
        vanilla_count = _vanilla_bone_count(chunk_number, file_index)
    except (OSError, struct.error, ValueError):
        vanilla_count = None
    if vanilla_count is None:
        return []

    high, low = (partner, own) if own.is_low_poly else (own, partner)
    warnings = []
    for bone in range(vanilla_count, min(high.bone_count, low.bone_count)):
        differences = []
        if high.parents.get(bone) != low.parents.get(bone):
            differences.append(
                f'parent {high.parents.get(bone)} in {high.geo_name} vs '
                f'{low.parents.get(bone)} in {low.geo_name}'
            )
        if not srt_matches(high.srts.get(bone), low.srts.get(bone)):
            differences.append('different SRT (position/rotation/scale)')
        if not differences:
            continue
        if bone in low.drawn_bones:
            effect = (
                f"{low.geo_name}'s geometry on it (submesh {low.drawn_bones[bone]}) "
                f"follows {high.geo_name}'s bone {bone}"
            )
        else:
            effect = f"anything {low.geo_name} draws on it would follow {high.geo_name}'s bone {bone}"
        warnings.append(
            f'Added bone {bone} differs between {high.geo_name} and {low.geo_name} '
            f'(chunk {chunk_number}, partner file {partner_index}): '
            f'{"; ".join(differences)}. The game poses the low-poly model from the '
            f"high-poly skeleton, so {low.geo_name}'s own placement of bone {bone} "
            f'is ignored: {effect}.'
        )
    return warnings


def lod_partner_unpatch_errors(chunk_number: int, file_index: int, any_stem: bool = False) -> list[str]:
    """Blocking errors for restoring this entry's vanilla model."""
    vanilla = _vanilla_block(chunk_number, file_index)
    if not vanilla:
        return []
    return lod_partner_errors(vanilla, chunk_number, file_index, any_stem)
