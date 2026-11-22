"""The ACT (bone hierarchy) section of a hammerspace build.

``BuildACTBoneHierarchy`` clones the donor's ACT and patches the root scale and the bone geo ids into
it, or, when bones were added (``BoneHierarchyEdited``), rebuilds the section from the donor's parsed
bone table (``act_rebuild``).
"""

import struct

import slogger as _slogger
from InplacePatcher import root_scale as _root_scale
import act_rebuild

from SluggieModel import STADIUM_CHUNKS
from Donor import Donor, CloneACT
from HammerspaceValidate import _bone_geo_id_raw


def BuildACTBoneHierarchy(data: dict, donor: Donor) -> bytes:
    """Return the ACT (Bone Hierarchy) section bytes for a hammerspace build.

    Two routes:

    - **Clone route** (no ``BoneHierarchyEdited``): identical to the
      behaviour before bones could be added -- ``CloneACT`` verbatim, then
      ``_apply_root_scale_patch`` and ``_apply_geo_id_patches``. Every
      existing hammerspace build stays byte-identical.
    - **Rebuild route** (``BoneHierarchyEdited`` present): appends every
      user-added bone to the donor's own parsed bone table
      (``_rebuild_act_bone_hierarchy``) instead of leaving the ACT section a
      fixed-size verbatim clone, so bone count can change.

    Returns ``b''`` when the model has no ACT section.
    """
    act_bytes = CloneACT(donor)
    if not act_bytes:
        return b''

    model = data['SluggiesModel']
    if not model.get('BoneHierarchyEdited'):
        act_bytes = _apply_root_scale_patch(act_bytes, data, donor)
        act_bytes = _apply_geo_id_patches(act_bytes, data, donor)
        return act_bytes

    return _rebuild_act_bone_hierarchy(act_bytes, data, donor)


def _rebuild_act_bone_hierarchy(act_bytes: bytes, data: dict, donor: Donor) -> bytes:
    """Rebuild the ACT bone hierarchy instead of cloning it verbatim, so new
    leaf bones from ``BoneHierarchyEdited`` can be appended
    (``_docs/_docs_model_format/act_section.html#appending-bones``).

    Parses the donor ACT bytes with ``act_rebuild`` -- the same machinery
    ``probe_act_rebuild_identity.py`` validates as byte-identical across the
    full player corpus -- which preserves every donor bone's own tree links, SRT block
    and header/name/tail bytes exactly. Every ``BoneHierarchyEdited`` entry
    with ``UserAdded`` set is then appended in ``BoneId`` order via
    ``act_rebuild.append_leaf_bone`` (always a new leaf at the end of
    its parent's child chain, the only safe topology change), fed back in so
    each append sees the previous one's result -- the same incremental
    pattern Dolphin-validated with 24 repeated appends (Mario, 2026-09-18).

    GeoId ownership: a **donor** bone's ``GeoIdEdited``/custom-submesh claim
    still goes through ``_apply_root_scale_patch``/``_apply_geo_id_patches``
    on the *rebuilt* bytes below -- their ACT-relative offset math
    (``orientationPTR``, and the bone-table ``geo_file_id_raw`` field
    addressed purely by bone id) is unaffected by appending trailing bones,
    since append-only growth never moves an existing bone's table slot or
    SRT position (verified: every donor bone's ``orientationPTR`` sits before
    any new bone's SRT in bone-table order). A **new** bone has no donor
    ``GeoIdFieldOffset`` to patch through, so its ``GeoId`` is instead owned
    directly here: a custom submesh naming a new bone as ``HostBoneId`` sets
    that bone's ``geo_file_id_raw`` on the in-memory ``BoneRecord`` before
    the final byte serialization.
    """
    model = data['SluggiesModel']
    bone_hierarchy_edited = model['BoneHierarchyEdited']

    parsed = act_rebuild.parse_act(act_bytes)
    act_rebuild.validate_mirror_table(parsed)

    new_entries = sorted(
        (b for b in bone_hierarchy_edited if b.get('UserAdded')),
        key=lambda b: int(b['BoneId']),
    )

    is_stadium = model.get('ChunkNumber') in STADIUM_CHUNKS
    expected_id = parsed.bone_count
    for entry in new_entries:
        bone_id = int(entry['BoneId'])
        if bone_id != expected_id:
            raise ValueError(
                f"BoneHierarchyEdited: new bone id {bone_id} is not contiguous with the "
                f"existing bone count ({expected_id} expected); new bone ids must be "
                "N, N+1, ... starting at the donor bone count"
            )
        parent_id = entry.get('ParentBoneId')
        if parent_id is None and not is_stadium:
            raise ValueError(
                f"new bone {bone_id} has no ParentBoneId; new bones may not be roots outside stadiums"
            )
        parent_id = None if parent_id is None else int(parent_id)
        if parent_id is not None and parent_id >= expected_id:
            raise ValueError(
                f"new bone {bone_id}'s parent {parent_id} does not exist yet; a new bone's "
                "parent must already be present (a donor bone, or an earlier new bone in "
                "BoneHierarchyEdited order)"
            )

        srt_type = int(entry.get('SRTType', 0))
        srt_blob = (
            act_rebuild.pack_srt_blob(
                srt_type, entry['Scale'], entry['Quaternion'], entry['Translation'],
            )
            if srt_type else None
        )

        parsed = act_rebuild.append_leaf_bone(
            parsed, parent_id, srt_blob,
            geo_file_id_raw=0xFFFF if entry.get('Skinned') else int(entry.get('GeoId', 0xFFFF)),
            inheritance=1 if entry.get('InheritTransform', True) else 0,
            priority=int(entry.get('DrawPriority', 0)),
            track_id=int(entry.get('TrackId', 0xFFFF)),
            mirror_bone_id=int(entry.get('MirrorBoneId', bone_id)),
            mirror_role=int(entry.get('MirrorRole', 3)),
        )
        expected_id += 1

    custom_submeshes = model.get('CustomSubmeshes') or []
    if custom_submeshes:
        donor_submesh_count = len(model.get('Submeshes') or [])
        donor_bone_count = parsed.bone_count - len(new_entries)
        bones_by_id = {b.id: b for b in parsed.bones}
        for index, cs in enumerate(custom_submeshes):
            host_bone_id = int(cs['HostBoneId'])
            if host_bone_id < donor_bone_count:
                continue  # donor host bone: handled by _apply_geo_id_patches below
            bone = bones_by_id.get(host_bone_id)
            if bone is None:
                raise ValueError(
                    f"custom submesh '{cs.get('CustomSubmeshId', '?')}': host bone "
                    f"{host_bone_id} does not exist in the rebuilt ACT bone hierarchy"
                )
            if bone.geo_file_id_raw != 0xFFFF:
                raise ValueError(
                    f"custom submesh '{cs.get('CustomSubmeshId', '?')}': host bone "
                    f"{host_bone_id} already owns GeoId {bone.geo_file_id_raw}, expected 0xFFFF"
                )
            bone.geo_file_id_raw = donor_submesh_count + index

    act_bytes = act_rebuild.rebuild_act_bytes(parsed)
    act_bytes = _apply_root_scale_patch(act_bytes, data, donor)
    act_bytes = _apply_geo_id_patches(act_bytes, data, donor)
    return act_bytes


def _apply_root_scale_patch(act_bytes: bytes, data: dict, donor: Donor) -> bytes:
    """Apply the root-bone SRT scale patch to a cloned ACT section.

    Reads the model's ``BoneHierarchy`` and ``RootBoneScaleEdited`` from the
    ``.sluggies`` data, computes the root-bone SRT scale patch (3 big-endian
    floats) via ``root_scale.hammerspace_root_scale_patch``, and writes it into
    the in-memory ACT section bytes at the ACT-section-relative offset
    (``orientationPTR + 0x04``).

    Because the hammerspace block is written to a new absolute offset, the
    absolute ``SRTOffset`` recorded in the ``.sluggie`` is not valid for the
    output. The ACT section, however, is cloned verbatim, so the SRT offset
    relative to the ACT section start is stable. Writing the scale at that
    relative offset keeps it correct regardless of hammerspace block size
    changes or relocation.

    Returns the (possibly modified) ACT section bytes. When there is no
    ``RootBoneScaleEdited`` (or no bone hierarchy / no ACT section), the original
    ``act_bytes`` are returned unchanged.
    """
    model = data['SluggiesModel']
    bone_hierarchy = model.get('BoneHierarchyEdited') or model.get('BoneHierarchy')
    if not bone_hierarchy or not act_bytes:
        return act_bytes
    if model.get('RootBoneScaleEdited') is None:
        return act_bytes  # nothing to write; don't touch the source DAT at all
    act_section_absolute = donor.act_absolute()
    if not act_section_absolute:
        return act_bytes
    patch = _root_scale.hammerspace_root_scale_patch(
        model, bone_hierarchy, act_section_absolute, _hs_abort
    )
    if patch is None:
        return act_bytes
    bone_id, scale_relative, raw = patch
    if scale_relative < 0 or scale_relative + len(raw) > len(act_bytes):
        raise ValueError(
            f'root-bone SRT scale for bone {bone_id} at ACT+0x{scale_relative:X} '
            f'({len(raw)} bytes) exceeds the cloned ACT section size '
            f'0x{len(act_bytes):X}; the .sluggie SRTOffset metadata does not match '
            f'this model ACT layout'
        )
    patched = bytearray(act_bytes)
    patched[scale_relative:scale_relative + len(raw)] = raw
    _slogger.info(
        f'[ACT] root-bone SRT scale: bone {bone_id} wrote {raw.hex()} at '
        f'ACT+0x{scale_relative:X} (section-relative; stable across hammerspace '
        f'relocation)',
        source='hammerspace.main',
    )
    return bytes(patched)


def _root_scale_changes_act(data: dict, donor: Donor) -> bool:
    """Whether the file's root-bone scale changes the donor's ACT (``root_scale_applied`` of the build report)."""
    act_bytes = CloneACT(donor)
    return bool(act_bytes) and _apply_root_scale_patch(act_bytes, data, donor) != act_bytes


def _hs_abort(message: str) -> None:
    """Log and raise for a root-scale patch error in the hammerspace pipeline."""
    _slogger.error(message, source='hammerspace.main')
    raise ValueError(message)


def _apply_geo_id_patches(act_bytes: bytes, data: dict, donor: Donor) -> bytes:
    """Patch bone ``GeoId`` fields in a cloned ACT section:
    each custom submesh's host bone
    gets ``GeoId = <new submesh index>``, and every ``GeoIdEdited`` bone (the
    donor rigid-submesh retargeting the in-place patcher already supports,
    via ``BoneHierarchy[].GeoIdEdited``) gets its edited value written too.
    Hammerspace silently ignored ``GeoIdEdited`` before this.

    Follows the same pattern as ``_apply_root_scale_patch``: hammerspace's ACT
    section is always cloned verbatim, so a donor bone's absolute
    ``GeoIdFieldOffset`` converted to an ACT-section-relative offset (via
    ``_act_section_absolute``) stays valid regardless of where the
    hammerspace block is relocated. New submesh indices are assigned in
    ``model['CustomSubmeshes']`` order starting at ``len(model['Submeshes'])``,
    matching ``PatchGPLAppendSubmesh``'s own ``donor_count + index`` rule, so
    the GPL append and the ACT ownership patch always agree.

    ``GeoIdEdited`` retargets are written first, using the same skip-if-no-op
    rule as ``InplacePatcher/patch_inplace.py`` (compare against
    ``_bone_geo_id_raw``, the literal un-edited ACT sentinel). Custom submesh
    claims are written second and read the already-patched bytes, so a
    retarget that frees a bone in the same build (``GeoIdEdited = 0xFFFF``)
    makes that bone claimable by a custom submesh in the same call, and a
    retarget that assigns a bone away from ``0xFFFF`` makes it correctly
    unclaimable.

    Returns the (possibly modified) ACT section bytes. When there is neither
    a ``CustomSubmeshes`` entry nor a ``GeoIdEdited`` bone (or no ACT
    section), the original ``act_bytes`` are returned unchanged.
    ``_validate_custom_submeshes`` has already checked that every host bone
    exists, is unclaimed (``GeoIdRaw == 0xFFFF``) and is claimed by exactly
    one custom submesh, so the only new failure mode for custom submeshes
    here is missing/stale ``GeoIdFieldOffset`` metadata. A stale field is
    tolerated only for the known pre-fix off-by-8 export.py bug (see the
    fallback below); anything else is rejected rather than guessed.
    """

    model = data['SluggiesModel']
    bone_hierarchy = model.get('BoneHierarchy') or []
    custom_submeshes = model.get('CustomSubmeshes') or []
    geo_id_edited_bones = [b for b in bone_hierarchy if b.get('GeoIdEdited') is not None]
    if not custom_submeshes and not geo_id_edited_bones:
        return act_bytes
    if not act_bytes:
        return act_bytes
    act_section_absolute = donor.act_absolute()
    if not act_section_absolute:
        return act_bytes

    bones_by_id = {int(b['BoneId']): b for b in bone_hierarchy}
    donor_count = len(model.get('Submeshes') or [])
    patched = bytearray(act_bytes)

    for bone in geo_id_edited_bones:
        bone_id = int(bone['BoneId'])
        target_geo_raw = int(bone['GeoIdEdited'])
        if target_geo_raw < 0 or target_geo_raw > 0xFFFF:
            raise ValueError(
                f'bone {bone_id}: GeoIdEdited value {target_geo_raw} is out of '
                'range (0..65535)'
            )
        if target_geo_raw == _bone_geo_id_raw(bone):
            continue  # no-op retarget, nothing to write

        field_off_hex = bone.get('GeoIdFieldOffset')
        if not field_off_hex:
            raise ValueError(
                f'bone {bone_id}: GeoIdEdited is set but GeoIdFieldOffset metadata '
                'is missing; re-export the model with the latest SluggiesTools '
                'export.py'
            )
        act_relative = int(field_off_hex, 16) - act_section_absolute
        if act_relative < 0 or act_relative + 2 > len(patched):
            raise ValueError(
                f'bone {bone_id}: GeoIdFieldOffset 0x{int(field_off_hex, 16):X} '
                f'(ACT+0x{act_relative:X}) falls outside the cloned ACT section '
                f'(0x{len(patched):X} bytes); the .sluggie\'s GeoIdFieldOffset '
                'metadata does not match this model\'s ACT layout'
            )

        struct.pack_into('>H', patched, act_relative, target_geo_raw)
        _slogger.info(
            f'[ACT] bone {bone_id} GeoId -> {target_geo_raw} at ACT+0x{act_relative:X} '
            '(GeoIdEdited retarget; section-relative, stable across hammerspace '
            'relocation)',
            source='hammerspace.main',
        )

    for index, cs in enumerate(custom_submeshes):
        cs_id = cs.get('CustomSubmeshId', '<missing CustomSubmeshId>')
        host_bone_id = int(cs['HostBoneId'])
        if host_bone_id not in bones_by_id:
            # A host bone with no BoneHierarchy entry
            # is a user-added bone -- _rebuild_act_bone_hierarchy already owns
            # its GeoId directly (it has no donor GeoIdFieldOffset to patch
            # through), so there is nothing for this loop to do here.
            continue
        new_submesh_index = donor_count + index
        bone = bones_by_id[host_bone_id]

        field_off_hex = bone.get('GeoIdFieldOffset')
        if not field_off_hex:
            raise ValueError(
                f"custom submesh '{cs_id}': host bone {host_bone_id} is missing "
                "GeoIdFieldOffset metadata; re-export the model with the latest "
                "SluggiesTools export.py"
            )
        act_relative = int(field_off_hex, 16) - act_section_absolute
        if act_relative < 0 or act_relative + 2 > len(patched):
            raise ValueError(
                f"custom submesh '{cs_id}': host bone {host_bone_id} GeoIdFieldOffset "
                f"0x{int(field_off_hex, 16):X} (ACT+0x{act_relative:X}) falls outside "
                f"the cloned ACT section (0x{len(patched):X} bytes); the .sluggie's "
                "GeoIdFieldOffset metadata does not match this model's ACT layout"
            )

        current = struct.unpack_from('>H', patched, act_relative)[0]
        if current != 0xFFFF:
            # Exports written before the export.py GeoIdFieldOffset fix (see
            # that file's extract_bone_hierarchy comment) recorded
            # bl.absolute + 0x0C instead of the real geoFileIdRaw field at
            # bl.absolute + 0x14, an 8-byte offset bug. Older .sluggie files
            # on disk still carry the stale offset. Try the corrected
            # location, but only accept it if it actually reads the unowned
            # sentinel; otherwise fail rather than guess.
            fallback_relative = act_relative + 8
            if (
                fallback_relative + 2 <= len(patched)
                and struct.unpack_from('>H', patched, fallback_relative)[0] == 0xFFFF
            ):
                _slogger.warning(
                    f"custom submesh '{cs_id}': host bone {host_bone_id} GeoIdFieldOffset "
                    f"at ACT+0x{act_relative:X} reads 0x{current:04X}, not 0xFFFF; using "
                    f"ACT+0x{fallback_relative:X} instead (compensating for the pre-fix "
                    "export.py off-by-8 GeoIdFieldOffset bug)",
                    source='hammerspace.main',
                )
                act_relative = fallback_relative
                current = 0xFFFF
            else:
                raise ValueError(
                    f"custom submesh '{cs_id}': host bone {host_bone_id} GeoId field at "
                    f"ACT+0x{act_relative:X} reads 0x{current:04X}, not 0xFFFF; it may "
                    "already have been claimed by another patch, or GeoIdFieldOffset "
                    "metadata is stale"
                )

        struct.pack_into('>H', patched, act_relative, new_submesh_index)
        _slogger.info(
            f"[ACT] custom submesh '{cs_id}': bone {host_bone_id} GeoId -> "
            f'{new_submesh_index} at ACT+0x{act_relative:X} (section-relative; '
            'stable across hammerspace relocation)',
            source='hammerspace.main',
        )

    return bytes(patched)
