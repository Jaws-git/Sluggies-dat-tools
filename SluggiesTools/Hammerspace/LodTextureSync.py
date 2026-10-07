"""Compare a low-poly (``L_*``) model's texture binds with its high-poly
partner's, and optionally bring them in step.

``L_*`` models have no TEX section: their Type-1 display states bind texture
indices into the high-poly model's TEX. When the high-poly model moves a
surface to another texture, the low-poly model keeps showing the old one at
a distance. That is cosmetic only. A 2026-09-27 theory that some mismatched
binds crash the game was withdrawn on 2026-10-07: a Dolphin re-test on Tiny
Kong (High retargeted to an appended texture, Low vanilla or partly
mirrored, small block) never crashed, while the same pair with a large
unbound texture crashed on load with no bind change at all. The September
crashes were the scene memory budget (see
``_docs/_docs_model_format/texture_section.html#low-poly-binds``).

The follow rule (an L bind that was in step with HP takes HP's new index;
one that differs is L's own assignment and is kept) is not a reliable
cosmetic mirror either: the same Type-1 param bytes can name different
surfaces in the two models (Tiny Kong's body: HP slot 07 draws the surface
L draws through slot 09). So AUTO_SYNC stays off and the patcher only logs
the differences at info level.

Binds are matched by submesh (same index and mesh name in both models) and
by the record's three param bytes plus texture layer. Custom submeshes
appended to HP have no L counterpart and are never touched.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import HammerspaceHelper as hh
import LodPartnerGuard
import slogger as _slogger

_GPL_MAGIC = 0x00B749E0
_DS_RECORD_SIZE = 0x10
_TEXTURE_BIND_ID = 1
_TEXTURE_INDEX_MASK = 0x1FFF

# Write the planned L bind edits. Off: the follow rule can pick the wrong
# surface (see the module docstring); with it off, differences are only logged.
AUTO_SYNC = False


@dataclass(frozen=True)
class TextureBind:
    submesh: int
    mesh_name: str
    display_state: int
    params: bytes
    layer: int
    texture: int
    setting_offset: int  # block-relative offset of the 32-bit setting word

    @property
    def key(self) -> tuple[int, bytes, int]:
        return self.submesh, self.params, self.layer


@dataclass(frozen=True)
class BindDifference:
    bind: TextureBind  # the low-poly bind
    high_texture: int
    vanilla_high_texture: int | None
    mirrored: bool
    new_texture: int | None = None  # what L was set to, when mirrored


@dataclass
class BindSyncPlan:
    edits: list[tuple[int, int]] = field(default_factory=list)  # (setting offset, new setting)
    differences: list[BindDifference] = field(default_factory=list)


def _u32(block: bytes, offset: int) -> int:
    return struct.unpack_from('>I', block, offset)[0]


def _cstr(block: bytes, offset: int) -> str:
    end = block.find(b'\x00', offset)
    return block[offset:end if end != -1 else len(block)].decode('ascii', errors='replace')


def texture_binds(block: bytes) -> list[TextureBind]:
    """Every Type-1 (texture bind) display state of a model block's GPL
    section. Empty when the block has no readable GPL section."""
    try:
        gpl = _u32(block, 0x04)
        if not gpl or _u32(block, gpl) != _GPL_MAGIC:
            return []
        count = _u32(block, gpl + 0x0C)
        descriptors = gpl + _u32(block, gpl + 0x10)
        binds = []
        for submesh in range(count):
            base = gpl + _u32(block, descriptors + submesh * 8)
            name = _cstr(block, gpl + _u32(block, descriptors + submesh * 8 + 4))
            display = base + _u32(block, base + 0x10)
            states = base + _u32(block, display + 4)
            state_count = struct.unpack_from('>H', block, display + 8)[0]
            for index in range(state_count):
                record = states + index * _DS_RECORD_SIZE
                if block[record] != _TEXTURE_BIND_ID:
                    continue
                setting = _u32(block, record + 4)
                binds.append(TextureBind(
                    submesh, name, index, bytes(block[record + 1:record + 4]),
                    (setting >> 13) & 0x7, setting & _TEXTURE_INDEX_MASK, record + 4,
                ))
        return binds
    except (struct.error, IndexError):
        return []


def _unique_textures(binds: list[TextureBind]) -> tuple[dict, dict[int, str]]:
    """``key -> texture`` for keys bound to one texture only (ambiguous keys
    are left out), and ``submesh -> mesh name``."""
    seen: dict[tuple, set[int]] = {}
    names = {}
    for bind in binds:
        seen.setdefault(bind.key, set()).add(bind.texture)
        names[bind.submesh] = bind.mesh_name
    return {key: next(iter(textures)) for key, textures in seen.items() if len(textures) == 1}, names


def plan_bind_sync(
    low_block: bytes,
    high_after: bytes,
    high_before: bytes | None,
    high_vanilla: bytes | None,
    low_vanilla: bytes | None = None,
) -> BindSyncPlan:
    """The L bind edits that keep *low_block* in step with a high-poly model
    changing from *high_before* to *high_after* (see the module docstring).
    Where HP returns to its vanilla index, a following L bind returns to L's
    own vanilla index (*low_vanilla*), which can differ from HP's."""
    after, after_names = _unique_textures(texture_binds(high_after))
    before, _ = _unique_textures(texture_binds(high_before)) if high_before else ({}, {})
    vanilla, vanilla_names = _unique_textures(texture_binds(high_vanilla)) if high_vanilla else ({}, {})
    low_binds = texture_binds(low_block)
    low_unique, _ = _unique_textures(low_binds)
    low_original, _ = _unique_textures(texture_binds(low_vanilla)) if low_vanilla else ({}, {})

    plan = BindSyncPlan()
    for bind in low_binds:
        if bind.key not in low_unique or bind.key not in after:
            continue
        if after_names.get(bind.submesh) != bind.mesh_name:
            continue  # submesh indices don't describe the same mesh
        if vanilla_names and vanilla_names.get(bind.submesh) != bind.mesh_name:
            continue
        high_texture = after[bind.key]
        vanilla_texture = vanilla.get(bind.key)
        high_is_vanilla = high_texture == vanilla_texture
        target = low_original.get(bind.key, high_texture) if high_is_vanilla else high_texture
        if bind.texture == target:
            continue
        follows = bind.texture in (before.get(bind.key), vanilla_texture)
        if not follows and high_is_vanilla:
            continue  # HP is vanilla here: L's own or vanilla divergence, not an HP edit
        if follows:
            setting = _u32(low_block, bind.setting_offset)
            plan.edits.append((bind.setting_offset, (setting & ~_TEXTURE_INDEX_MASK) | target))
        plan.differences.append(BindDifference(bind, high_texture, vanilla_texture, follows, target))
    return plan


def apply_edits(block: bytes, plan: BindSyncPlan) -> bytes:
    patched = bytearray(block)
    for offset, setting in plan.edits:
        struct.pack_into('>I', patched, offset, setting)
    return bytes(patched)


def _shared_live_range(chunk_number: int, file_index: int, offset: int, length: int) -> bool:
    """Whether another output-DOL route still loads this range too, e.g. an
    unused character's re-tangled route on its owner's vanilla block (see
    UntanglePolicy). Writing into it would change that model as well, so the
    sync skips it (logged)."""
    others = [route for route in hh.liveRoutesInto(offset, length) if route != (chunk_number, file_index)]
    if others:
        _slogger.warning(
            f'[LOD] texture assignment sync skipped for chunk {chunk_number}, file {file_index}: '
            f'its block at 0x{offset:08X} is shared with route(s) '
            + ', '.join(f'({c},{i})' for c, i in others),
            source='hammerspace.main',
        )
    return bool(others)


def _vanilla_block(chunk_number: int, file_index: int) -> bytes | None:
    offset, length = hh.readDolEntry(chunk_number, file_index)
    if offset == -1 or length <= 0:
        return None
    with open(hh.INPUT_DAT, 'rb') as dat:
        dat.seek(offset)
        block = dat.read(length)
    return block if len(block) == length else None


def describe(plan: BindSyncPlan, high_name: str, low_name: str, chunk_number: int, dry_run: bool) -> str:
    lines = []
    for difference in plan.differences:
        bind = difference.bind
        slot = (
            f"sub{bind.submesh} '{bind.mesh_name}' ds{bind.display_state} "
            f'(Type-1 params {bind.params.hex()}, layer {bind.layer})'
        )
        if difference.mirrored and not AUTO_SYNC:
            action = (
                f'{low_name} not changed (the automatic sync is off; the follow '
                f'rule would set {difference.new_texture})'
            )
        elif difference.mirrored:
            action = f'{"would set" if dry_run else "set"} {low_name} to {difference.new_texture}'
        else:
            action = (
                f'kept: {low_name} was not in step with {high_name} here '
                f'(vanilla {high_name}: {difference.vanilla_high_texture}), so its '
                'index is treated as its own assignment'
            )
        lines.append(
            f'  {slot}: {high_name} texture {difference.high_texture}, '
            f'{low_name} texture {bind.texture} -> {action}'
        )
    return (
        f'[LOD] Texture assignments differ between {high_name} and {low_name} '
        f'(chunk {chunk_number}). A low-poly model binds textures by index into '
        'the high-poly TEX, so at a distance these surfaces show the texture '
        'listed for the low-poly model (cosmetic only).\n' + '\n'.join(lines)
    )


def _pair_names(high: bytes, low: bytes) -> tuple[str, str]:
    high_summary = LodPartnerGuard.act_summary(high)
    low_summary = LodPartnerGuard.act_summary(low)
    return (
        high_summary.geo_name if high_summary else 'high-poly model',
        low_summary.geo_name if low_summary else 'low-poly model',
    )


def sync_low_block(block: bytes, chunk_number: int, file_index: int, dry_run: bool = False) -> bytes:
    """*block* (a low-poly model about to be written) with its binds brought
    in step with the high-poly partner's current state. Returns *block*
    unchanged for a high-poly model, a model without partner, or an
    unreadable partner (logged)."""
    own = LodPartnerGuard.act_summary(block)
    if own is None or not own.is_low_poly:
        return block
    try:
        found = LodPartnerGuard.find_partner(chunk_number, file_index, own)
        if found is None:
            return block
        high_index = found[0]
        high = LodPartnerGuard.read_current_block(chunk_number, high_index)
        vanilla = _vanilla_block(chunk_number, high_index)
        low_vanilla = _vanilla_block(chunk_number, file_index)
    except (OSError, struct.error, ValueError) as exc:
        _slogger.warning(
            f'[LOD] could not read the high-poly partner of {own.geo_name} '
            f'(chunk {chunk_number}): {exc}; texture assignment sync skipped',
            source='hammerspace.main',
        )
        return block
    if high is None:
        return block
    plan = plan_bind_sync(block, high, high, vanilla, low_vanilla)
    if plan.differences:
        _slogger.info(describe(plan, *_pair_names(high, block), chunk_number, dry_run),
                         source='hammerspace.main')
    return apply_edits(block, plan) if AUTO_SYNC else block


def sync_partner_of_high(
    chunk_number: int,
    file_index: int,
    high_after: bytes,
    high_before: bytes | None,
    dry_run: bool = False,
) -> BindSyncPlan | None:
    """Bring the live low-poly partner of a high-poly model in step with
    *high_after* (the high-poly block that is now, or would be, live),
    writing the changed setting words straight into the partner's current
    DAT range (in place or in hammerspace). Returns the plan, or None when
    there is no partner (or it cannot be read, logged)."""
    own = LodPartnerGuard.act_summary(high_after)
    if own is None or own.is_low_poly:
        return None
    try:
        found = LodPartnerGuard.find_partner(chunk_number, file_index, own)
        if found is None:
            return None
        low_index = found[0]
        low = LodPartnerGuard.read_current_block(chunk_number, low_index)
        vanilla = _vanilla_block(chunk_number, file_index)
        low_vanilla = _vanilla_block(chunk_number, low_index)
    except (OSError, struct.error, ValueError) as exc:
        _slogger.warning(
            f'[LOD] could not read the low-poly partner of {own.geo_name} '
            f'(chunk {chunk_number}): {exc}; texture assignment sync skipped',
            source='hammerspace.main',
        )
        return None
    if low is None:
        return None

    plan = plan_bind_sync(low, high_after, high_before, vanilla, low_vanilla)
    if plan.differences:
        _slogger.info(describe(plan, *_pair_names(high_after, low), chunk_number, dry_run),
                         source='hammerspace.main')
    if plan.edits and AUTO_SYNC and not dry_run:
        offset, length = hh.readOutputDolEntry(chunk_number, low_index)
        if length != len(low):
            raise RuntimeError(
                f'low-poly partner route (chunk {chunk_number}, file {low_index}) changed '
                'while syncing texture assignments'
            )
        if _shared_live_range(chunk_number, low_index, offset, length):
            return plan
        with open(hh.OUTPUT_DAT, 'r+b') as dat:
            for setting_offset, setting in plan.edits:
                dat.seek(offset + setting_offset)
                dat.write(struct.pack('>I', setting))
        expected = apply_edits(low, plan)
        with open(hh.OUTPUT_DAT, 'rb') as dat:
            dat.seek(offset)
            if dat.read(length) != expected:
                raise IOError(f'texture assignment sync verification failed at 0x{offset:08X}')
        _slogger.info(
            f'[LOD] Synced {len(plan.edits)} texture assignment(s) into the low-poly partner '
            f'(chunk {chunk_number}, file {low_index}) at 0x{offset:08X}',
            source='hammerspace.main',
        )
    return plan


def resync_live_low(chunk_number: int, file_index: int) -> None:
    """Re-apply the sync to a low-poly model's live block, in its current
    DAT range (used after the low-poly model itself is unpatched, which
    restores its vanilla binds)."""
    block = LodPartnerGuard.read_current_block(chunk_number, file_index)
    if block is None:
        return
    synced = sync_low_block(block, chunk_number, file_index)
    if synced == block:
        return
    offset, length = hh.readOutputDolEntry(chunk_number, file_index)
    if length != len(block):
        raise RuntimeError(f'low-poly route (chunk {chunk_number}, file {file_index}) changed during sync')
    if _shared_live_range(chunk_number, file_index, offset, length):
        return
    with open(hh.OUTPUT_DAT, 'r+b') as dat:
        dat.seek(offset)
        dat.write(synced)
    _slogger.info(
        f'[LOD] Re-synced texture assignments of the low-poly model '
        f'(chunk {chunk_number}, file {file_index}) at 0x{offset:08X}',
        source='hammerspace.main',
    )
