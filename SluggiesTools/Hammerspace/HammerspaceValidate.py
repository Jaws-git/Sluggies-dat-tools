"""Checks of a ``.sluggie`` before a hammerspace build.

Everything here runs before a section is built and before any DAT or DOL write: the export contract,
custom submeshes, added bones, skin bones, rigid and skinned rebuild entries and their facial poses.
A check raises ``ValueError`` with the text the user sees. Also the small helpers the checks share with
the builders (effective bone ids, the position formats a rebuild may carry).
"""

import re
import struct

from binfmt import (
    color_entry_size as _color_entry_size,
    comp_size as _vb_comp_size,
    decode_field as _decode,
    skin_bone_ids as _skin_bone_ids,
)
import FacialPoseRebuild as _facial

from SluggieModel import (
    RigidRebuild,
    SkinnedRebuild,
    STADIUM_CHUNKS,
    _CUSTOM_SUBMESH_POSITION_FORMAT,
    _CUSTOM_SUBMESH_POSITION_QUANTIZE_RANGE,
    _CUSTOM_SUBMESH_FLOAT_POSITION_QUANTIZE,
    _CUSTOM_SUBMESH_POSITION_QUANTIZE_VALUES,
    _custom_submesh_position_stride,
    _CUSTOM_SUBMESH_NORMAL_FORMAT,
    _CUSTOM_SUBMESH_UV_FORMAT,
    _CUSTOM_SUBMESH_COLOR_FORMATS,
    _u16_values,
    _SKINNED_REBUILD_COMP_COUNT,
    _SKINNED_REBUILD_INFLUENCE,
    _SKINNED_REBUILD_SUBMESH,
    SectionModes,
)
from SubmeshTemplates import (
    _CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES,
    _CUSTOM_SUBMESH_REJECTED_DERIVED_TYPE6,
    _CUSTOM_SUBMESH_NORMAL_STRIDE,
    _CUSTOM_SUBMESH_UV_STRIDE,
    _CUSTOM_SUBMESH_COLOR_STRIDE,
    _CUSTOM_SUBMESH_BUILTIN_TEMPLATES,
    builtin_template_names,
    _custom_submesh_state_records_sha256,
    _custom_submesh_type3_descriptors,
    _custom_submesh_builtin_layer_count,
)


# ---------------------------------------------------------------------------
# CustomSubmeshes validation
# ---------------------------------------------------------------------------
# Cross-checks a CustomSubmeshes entry against the rest of the .sluggie
# contract before any DAT/DOL write. This runs ahead of ParseSluggie, which
# is a pure parse; GPL/ACT/TEX assembly comes later.

def _bone_geo_id_raw(bone: dict) -> int:
    """Literal ACT GeoId sentinel (0xFFFF = mesh-free), independent of the
    BoneHierarchy[].GeoId field, which normalises skinned bones to 0."""
    if bone.get('GeoIdRaw') is not None:
        return int(bone['GeoIdRaw'])
    if bone.get('Skinned'):
        return 0xFFFF
    return int(bone.get('GeoId', 0xFFFF))


def _effective_geo_id_raw(bone: dict) -> int:
    """The GeoId a bone holds after this build: its ``GeoIdEdited`` (a donor
    rigid-submesh retarget, written into the ACT by ``_apply_geo_id_patches``
    before any custom-submesh claim) when set, else the literal donor value.
    This is what ``HostBones.classify_host_bones`` shows in Blender, so a bone
    freed by a pending retarget is claimable here too."""
    edited = bone.get('GeoIdEdited')
    if edited is not None:
        return int(edited)
    return _bone_geo_id_raw(bone)


def _bones_by_id(model: dict) -> dict[int, dict]:
    """Every bone this build knows, by id: the donor ``BoneHierarchy`` (which
    carries ``GeoIdEdited`` retargets) overlaid with ``BoneHierarchyEdited``
    (which carries user-added bones and edited placements). A donor bone's
    ``GeoIdEdited`` is kept when the edited list lacks it: the exporter writes
    the two lists in separate passes."""
    bones = {int(b['BoneId']): dict(b) for b in model.get('BoneHierarchy') or []}
    for edited in model.get('BoneHierarchyEdited') or []:
        bone_id = int(edited['BoneId'])
        merged = dict(edited)
        donor = bones.get(bone_id)
        if donor is not None and donor.get('GeoIdEdited') is not None and merged.get('GeoIdEdited') is None:
            merged['GeoIdEdited'] = donor['GeoIdEdited']
        bones[bone_id] = merged
    return bones


def _custom_submesh_effective_state(states: list, upto_index: int, display_state_id: int):
    """Last state of *display_state_id* at or before *upto_index* in *states*
    (the cumulative-state walk that resolves GX state carried into a
    draw)."""
    effective = None
    for state in states[:upto_index + 1]:
        if int(state.get('DisplayStateId', -1)) == display_state_id:
            effective = state
    return effective


def _validate_custom_submesh_indexed_array(
    fail, label: str, use_compression: bool, data_field, faces_field, stride: int,
) -> None:
    """Structurally validate one (data, face-index) buffer pair.

    Confirms the data buffer decodes to a whole number of *stride*-byte
    entries within the uint16 index range, and that every face-index value
    addresses an existing entry -- the "index/count limits are within
    uint16" and "quantized coordinates are within the representable range"
    checks. Values already decoded
    from a properly strided buffer are always in-range for their own field
    width; a misaligned/truncated buffer is the actual failure mode this
    guards against.
    """
    if data_field is None:
        return
    data_bytes = _decode(data_field, use_compression)
    if not data_bytes or len(data_bytes) % stride:
        fail(f'{label} length {len(data_bytes)} is not a whole number of {stride}-byte entries')
        return
    entry_count = len(data_bytes) // stride
    if entry_count > 0xFFFF:
        fail(f'{label} entry count {entry_count} exceeds the uint16 index range')
    if faces_field is None:
        return
    faces_bytes = _decode(faces_field, use_compression)
    if not faces_bytes or len(faces_bytes) % 6:
        fail(f'{label} face-index buffer length {len(faces_bytes)} is not a whole number of uint16 triplets')
        return
    for index in struct.unpack(f'>{len(faces_bytes) // 2}H', faces_bytes):
        if index >= entry_count:
            fail(f'{label} face index {index} is out of range for {entry_count} entries')
            return


def _custom_submesh_rigid_surfaces(model: dict) -> dict:
    """Map every rigid (CompCount 3) donor submesh's SurfaceId to
    ``(that submesh's DisplayStates list, the surface's index within it)``,
    first-seen-wins. Shared by ``_validate_custom_submeshes``
    and ``PatchGPLAppendSubmesh``."""
    rigid_surfaces: dict[str, tuple[list, int]] = {}
    for sub in model.get('Submeshes') or []:
        if int((sub.get('VertexBuffer') or {}).get('VertexBufferCompCount', 0)) != 3:
            continue
        states = sub.get('DisplayStates') or []
        for index, state in enumerate(states):
            surface_id = state.get('SurfaceId')
            if surface_id and surface_id not in rigid_surfaces:
                rigid_surfaces[surface_id] = (states, index)
    return rigid_surfaces


def _validate_template_source(
    model: dict, template_source, fail, *, uv_channel_count: int, has_normals: bool,
    is_stadium: bool, rigid_surfaces: dict, layers_must_match: bool,
) -> str | None:
    """The per-kind checks a ``TemplateSource`` must pass, shared by custom
    submeshes and the new surfaces of a
    rebuilt rigid submesh. Each
    problem goes to *fail*; the kind (``rigid`` / ``derived`` / ``builtin``)
    is returned, or None when the source is malformed.

    *uv_channel_count* and *has_normals* describe the submesh the template
    draws: a built-in's bound texture layers (its own count capped by what
    host submesh 0 binds) must equal the custom submesh's UV channel count
    (*layers_must_match*), while a new surface on a donor submesh only needs
    the host to have at least that many channels (its Type 3 is cut to the
    layers the group binds).
    """
    donor_submeshes = model.get('Submeshes') or []
    submesh0 = donor_submeshes[0] if donor_submeshes else None
    kind, sep, argument = str(template_source).partition(':')
    if not sep or kind not in ('rigid', 'derived', 'builtin') or not argument:
        fail(
            f'TemplateSource {template_source!r} must be rigid:<SurfaceId>, '
            'derived:<SurfaceId> or builtin:<name>'
        )
        return None

    if kind == 'rigid':
        found = rigid_surfaces.get(argument)
        if found is None:
            fail(f"rigid: surface {argument!r} does not exist on a rigid submesh")
        else:
            states, index = found
            lighting = _custom_submesh_effective_state(states, index, 7)
            mode = lighting.get('ShaderMode') if lighting else None
            if mode in _CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES:
                fail(
                    f"rigid: surface {argument!r} has hand/visibility-role "
                    f'Type-7 {mode!r}; only plain lit surfaces are allowed'
                )
    elif kind == 'derived':
        if submesh0 is None or int((submesh0.get('VertexBuffer') or {}).get('VertexBufferCompCount', 0)) != 6:
            fail('derived: sources require a donor skinned submesh 0')
        else:
            states = submesh0.get('DisplayStates') or []
            matches = [i for i, s in enumerate(states) if s.get('SurfaceId') == argument]
            if not matches:
                fail(f"derived: surface {argument!r} does not exist on submesh 0")
            elif int(states[matches[0]].get('PrimListLength') or 0) <= 0:
                fail(f"derived: surface {argument!r} draws no primitives")
            else:
                index = matches[0]
                type7 = _custom_submesh_effective_state(states, index, 7)
                type7_mode = type7.get('ShaderMode') if type7 else None
                if type7_mode != 'Spec':
                    fail(
                        f"derived: surface {argument!r} has effective Type-7 "
                        f'{type7_mode!r}; only plain Spec surfaces are allowed'
                    )
                type6 = _custom_submesh_effective_state(states, index, 6)
                type6_mode = type6.get('ShaderMode') if type6 else None
                if type6_mode == _CUSTOM_SUBMESH_REJECTED_DERIVED_TYPE6:
                    fail(
                        f"derived: surface {argument!r} uses Type-6 "
                        f'{_CUSTOM_SUBMESH_REJECTED_DERIVED_TYPE6}, which never '
                        'occurs on rigid submeshes'
                    )
    elif kind == 'builtin':
        template = _CUSTOM_SUBMESH_BUILTIN_TEMPLATES.get(argument)
        if template is None:
            fail(
                f'builtin: unknown template {argument!r}; known: '
                f'{sorted(_CUSTOM_SUBMESH_BUILTIN_TEMPLATES)}'
            )
        elif _custom_submesh_state_records_sha256(template['States']) != template['Sha256']:
            fail(f"builtin: stored bytes of {argument!r} do not match their recorded hash")
        elif not template.get('VerifiedInGame'):
            fail(
                f'builtin: template {argument!r} is not verified in game yet '
                '; verified: '
                f'{sorted(builtin_template_names(verified_only=True))}'
            )
        elif template.get('StadiumOnly') and not is_stadium:
            fail(f'builtin: template {argument!r} is for stadium models only')
        elif not template.get('Normals', True) and has_normals:
            fail(
                f'builtin: template {argument!r} draws without normals, like '
                'stadium surfaces; export the submesh again without NormalBufferData'
            )
        else:
            # Type 3 comes from the channels the submesh carries while Type 4
            # follows the T1 records the template emits, so the two have to
            # agree. The emitted count is the template's own layer count
            # capped by what host submesh 0 binds -- a 2-layer built-in
            # legitimately degrades to the 1-layer form on a host with no
            # specular binding.
            bound_layers = _custom_submesh_builtin_layer_count(model, argument)
            if layers_must_match and bound_layers != uv_channel_count:
                fail(
                    f'builtin: template {argument!r} binds {bound_layers} texture '
                    f'layer(s) on this model but the custom submesh has '
                    f'{uv_channel_count} UV channel(s)'
                )
            elif bound_layers > uv_channel_count:
                fail(
                    f'builtin: template {argument!r} binds {bound_layers} texture '
                    f'layer(s) on this model but the host submesh has only '
                    f'{uv_channel_count} UV channel(s)'
                )
    return kind


def _validate_new_surface_entry(
    model: dict, entry: dict, fail_surface, *, uv_channel_count: int, has_normals: bool,
    is_stadium: bool, rigid_surfaces: dict, additional_names: set,
) -> None:
    """The checks one appended-surface entry (``RigidRebuild.NewSurfaces[]``
    or ``CustomSubmeshes[].AdditionalSurfaces[]``) must pass beyond its key:
    template source (the host only needs at least the layers the group
    binds), texture assignment and specular strength."""
    _validate_template_source(
        model, entry.get('TemplateSource', ''), fail_surface,
        uv_channel_count=uv_channel_count,
        has_normals=has_normals,
        is_stadium=is_stadium,
        rigid_surfaces=rigid_surfaces,
        layers_must_match=False,
    )
    assignment = entry.get('TextureAssignment') or {}
    donor_index = assignment.get('DonorTextureIndex')
    additional_name = assignment.get('AdditionalTextureFileName')
    if (donor_index is None) == (additional_name is None):
        fail_surface('TextureAssignment must set exactly one of DonorTextureIndex or AdditionalTextureFileName')
    elif donor_index is not None and not (
        isinstance(donor_index, int) and not isinstance(donor_index, bool) and 0 <= donor_index <= 0xFFFF
    ):
        fail_surface(f'TextureAssignment.DonorTextureIndex {donor_index!r} must be a uint16 texture index')
    elif additional_name is not None and additional_name not in additional_names:
        fail_surface(
            f'TextureAssignment.AdditionalTextureFileName {additional_name!r} has no '
            'AdditionalTextureDescriptors entry'
        )
    strength = entry.get('SpecularStrength')
    if strength is not None and not (
        isinstance(strength, int) and not isinstance(strength, bool) and 0 <= strength <= 255
    ):
        fail_surface(f'SpecularStrength {strength!r} must be an integer 0..255')


def _validate_custom_submesh_additional_surfaces(
    model: dict, cs: dict, cs_id, fail, faces_count: int | None, use_compression: bool, *,
    is_stadium: bool, rigid_surfaces: dict,
) -> None:
    """``AdditionalSurfaces`` keys are
    ``<CustomSubmeshId>_new<K>`` and unique, each entry passes the shared
    appended-surface checks, and ``FaceSurfaceIndices`` (one uint16 per
    triangle) stays inside ``0..len(AdditionalSurfaces)`` and uses every
    listed surface."""
    surfaces = cs.get('AdditionalSurfaces')
    if surfaces is None:
        surfaces = []
    if not isinstance(surfaces, list) or not all(isinstance(entry, dict) for entry in surfaces):
        fail('AdditionalSurfaces must be a list of objects')
        surfaces = []
    keys = [entry.get('SurfaceKey') for entry in surfaces]
    if len(set(keys)) != len(keys):
        fail('AdditionalSurfaces keys are not unique')
    additional_names = {
        descriptor.get('TextureFileName')
        for descriptor in model.get('AdditionalTextureDescriptors') or []
    }
    key_pattern = re.compile(rf'^{re.escape(str(cs_id))}_new(\d+)$')
    for entry in surfaces:
        key = entry.get('SurfaceKey')

        def fail_surface(message: str, _key=key) -> None:
            fail(f'additional surface {_key!r}: {message}')

        if not isinstance(key, str) or key_pattern.match(key) is None:
            fail_surface(f'key must match {cs_id}_new<K>')
        _validate_new_surface_entry(
            model, entry, fail_surface,
            uv_channel_count=len(cs.get('UVChannels') or []),
            has_normals=cs.get('NormalBufferData') is not None,
            is_stadium=is_stadium,
            rigid_surfaces=rigid_surfaces,
            additional_names=additional_names,
        )

    indices_field = cs.get('FaceSurfaceIndices')
    if indices_field is None:
        if surfaces:
            fail('AdditionalSurfaces need FaceSurfaceIndices to say which faces use them')
        return
    indices_raw = _decode(indices_field, use_compression)
    if len(indices_raw) % 2:
        fail(f'FaceSurfaceIndices length {len(indices_raw)} is not a whole number of uint16 values')
        return
    indices = _u16_values(indices_raw)
    if faces_count is not None and len(indices) != faces_count:
        fail(f'FaceSurfaceIndices holds {len(indices)} entries for FacesCount {faces_count}')
    used = set(indices)
    for value in sorted(used):
        if value > len(surfaces):
            fail(
                f'FaceSurfaceIndices value {value} is out of range for {len(surfaces)} '
                'additional surface(s) (0 is the primary surface)'
            )
            break
    for position, key in enumerate(keys, start=1):
        if position not in used:
            fail(f'additional surface {key!r} is used by no face')


def _validate_custom_submeshes(model: dict) -> None:
    """Reject an invalid CustomSubmeshes
    entry before any DAT/DOL write. Runs ahead of ParseSluggie so a bad
    entry never reaches GPL/ACT/TEX assembly. Each error names the
    offending CustomSubmeshId so a failed patch is actionable."""
    custom_submeshes = model.get('CustomSubmeshes') or []
    if not custom_submeshes:
        return

    errors: list[str] = []
    if not model.get('UseHammerspace'):
        raise ValueError(
            'CustomSubmeshes require UseHammerspace; re-export the model from Blender'
        )

    use_compression = model.get('UseCompression', True)
    # A custom submesh may host on a user-added bone, which has no
    # BoneHierarchy entry at all (it doesn't exist in the donor ACT), so the
    # bone set is the donor list overlaid with BoneHierarchyEdited; a bone's
    # ownership is its *effective* GeoId, so a bone a donor rigid submesh is
    # leaving (GeoIdEdited 0xFFFF) in this same export is free here, exactly
    # as Blender's free-bone list shows it.
    bones_by_id = _bones_by_id(model)

    rigid_surfaces = _custom_submesh_rigid_surfaces(model)
    is_stadium = model.get('ChunkNumber') in STADIUM_CHUNKS

    claimed_bones: dict[int, str] = {}
    for cs in custom_submeshes:
        cs_id = cs.get('CustomSubmeshId', '<missing CustomSubmeshId>')

        def fail(message: str, _cs_id=cs_id) -> None:
            errors.append(f"custom submesh '{_cs_id}': {message}")

        host_bone_id = cs.get('HostBoneId')
        if not isinstance(host_bone_id, int) or isinstance(host_bone_id, bool) or not (0 <= host_bone_id <= 0xFFFF):
            fail(f'HostBoneId {host_bone_id!r} must be a uint16 bone index')
        else:
            bone = bones_by_id.get(host_bone_id)
            if bone is None:
                fail(f'host bone {host_bone_id} does not exist in BoneHierarchy')
            else:
                raw = _effective_geo_id_raw(bone)
                if raw != 0xFFFF:
                    fail(
                        f'host bone {host_bone_id} already owns submesh {raw}; '
                        'GeoId is a single uint16 per bone'
                    )
                elif host_bone_id in claimed_bones:
                    fail(
                        f'host bone {host_bone_id} is also claimed by custom submesh '
                        f"'{claimed_bones[host_bone_id]}'"
                    )
                else:
                    claimed_bones[host_bone_id] = cs_id

        kind = _validate_template_source(
            model, cs.get('TemplateSource', ''), fail,
            uv_channel_count=len(cs.get('UVChannels') or []),
            has_normals=cs.get('NormalBufferData') is not None,
            is_stadium=is_stadium,
            rigid_surfaces=rigid_surfaces,
            layers_must_match=True,
        )

        if kind in ('derived', 'builtin'):
            uv_channel_count = len(cs.get('UVChannels') or [])
            if uv_channel_count not in (1, 2):
                fail(f'{kind}: UV channel count must be 1 or 2, got {uv_channel_count}')

        faces_count = cs.get('FacesCount')
        if not isinstance(faces_count, int) or isinstance(faces_count, bool) or not (0 <= faces_count <= 0xFFFF):
            fail(f'FacesCount {faces_count!r} must be a uint16 value')
            faces_count = None

        position_quantize = cs.get(
            'VertexBufferQuantizeInfo', _CUSTOM_SUBMESH_POSITION_FORMAT[1]
        )
        if (
            not isinstance(position_quantize, int) or isinstance(position_quantize, bool)
            or position_quantize not in _CUSTOM_SUBMESH_POSITION_QUANTIZE_VALUES
        ):
            fail(
                f'VertexBufferQuantizeInfo {position_quantize!r} must be an s16 position '
                f'format between {_CUSTOM_SUBMESH_POSITION_QUANTIZE_RANGE[0]} and '
                f'{_CUSTOM_SUBMESH_POSITION_QUANTIZE_RANGE[-1]} (high nibble 3) or '
                f'{_CUSTOM_SUBMESH_FLOAT_POSITION_QUANTIZE} (float)'
            )
            position_quantize = _CUSTOM_SUBMESH_POSITION_FORMAT[1]
        position_stride = _custom_submesh_position_stride(position_quantize)

        vertex_bytes = _decode(cs['VertexBufferData'], use_compression) if cs.get('VertexBufferData') else b''
        vertex_count = None
        if not vertex_bytes or len(vertex_bytes) % position_stride:
            fail(f'VertexBufferData length {len(vertex_bytes)} is not a whole number of rigid position entries')
        else:
            vertex_count = len(vertex_bytes) // position_stride
            if vertex_count > 0xFFFF:
                fail(f'vertex count {vertex_count} exceeds the uint16 index range')

        if faces_count is not None:
            faces_bytes = _decode(cs['FacesData'], use_compression) if cs.get('FacesData') is not None else b''
            if len(faces_bytes) != faces_count * 6:
                fail(
                    f'FacesData length {len(faces_bytes)} does not match FacesCount '
                    f'{faces_count} (expected {faces_count * 6} bytes)'
                )
            elif vertex_count is not None and faces_bytes:
                for vertex_index in struct.unpack(f'>{len(faces_bytes) // 2}H', faces_bytes):
                    if vertex_index >= vertex_count:
                        fail(f'face index {vertex_index} is out of range for {vertex_count} vertices')
                        break

        if cs.get('NormalBufferData') is not None:
            _validate_custom_submesh_indexed_array(
                fail, 'NormalBufferData', use_compression,
                cs.get('NormalBufferData'), cs.get('NormalFacesData'),
                _CUSTOM_SUBMESH_NORMAL_STRIDE,
            )
        if cs.get('ColorChannelData') is not None:
            _validate_custom_submesh_indexed_array(
                fail, 'ColorChannelData', use_compression,
                cs.get('ColorChannelData'), cs.get('ColorFacesData'),
                _CUSTOM_SUBMESH_COLOR_STRIDE,
            )
        for uv in cs.get('UVChannels') or []:
            _validate_custom_submesh_indexed_array(
                fail, f"UVChannels[{uv.get('UVChannelIndex')}]", use_compression,
                uv.get('UVChannelData'), uv.get('UVFacesData'),
                _CUSTOM_SUBMESH_UV_STRIDE,
            )

        texture_assignment = cs.get('TextureAssignment') or {}
        donor_index = texture_assignment.get('DonorTextureIndex')
        additional_name = texture_assignment.get('AdditionalTextureFileName')
        if (donor_index is None) == (additional_name is None):
            fail(
                'TextureAssignment must set exactly one of DonorTextureIndex or '
                'AdditionalTextureFileName'
            )
        elif donor_index is not None and not (isinstance(donor_index, int) and 0 <= donor_index <= 0xFFFF):
            fail(f'TextureAssignment.DonorTextureIndex {donor_index!r} must be a uint16 texture index')

        color_format = cs.get('ColorChannelQuantizeInfo')
        if color_format is not None and (
            isinstance(color_format, bool) or color_format not in _CUSTOM_SUBMESH_COLOR_FORMATS
        ):
            fail(
                f'ColorChannelQuantizeInfo {color_format!r} must be one of '
                f'{sorted(_CUSTOM_SUBMESH_COLOR_FORMATS)} (0 RGB565, 48 RGBA4444)'
            )

        strength = cs.get('SpecularStrength')
        if strength is not None and not (
            isinstance(strength, int) and not isinstance(strength, bool) and 0 <= strength <= 255
        ):
            fail(f'SpecularStrength {strength!r} must be an integer 0..255')

        _validate_custom_submesh_additional_surfaces(
            model, cs, cs_id, fail, faces_count, use_compression,
            is_stadium=is_stadium, rigid_surfaces=rigid_surfaces,
        )

    if errors:
        raise ValueError('; '.join(errors))


# Track ids a new bone may read its animation from (besides 0xFFFF, no track).
# Stock skeletons and banks use 0x00-0x8C, 0x100-0x127 and 0x180-0x199 plus
# flag-style ids at 0x4000 and up (every High model and bank 6-14 of
# dt_na.dat, surveyed 2026-10-12), so no stock animation drives an id in this
# range. Whether the game animates a bone from such an id is untested
# (2026-10-12). The Blender add-on hands them out (HostBones.ADDED_TRACK_IDS).
ADDED_TRACK_IDS = range(0xC0, 0x100)


def _added_bone_ids(model: dict) -> set[int]:
    """Ids of the bones ``BoneHierarchyEdited`` appends (``UserAdded``)."""
    return {
        int(b['BoneId']) for b in (model.get('BoneHierarchyEdited') or []) if b.get('UserAdded')
    }


def _allowed_skin_bones(model: dict) -> set[int]:
    """Bones a skin may use: every bone of the skeleton (``BoneHierarchy`` and
    ``BoneHierarchyEdited``) plus the donor's own skin bones. The game builds
    a skinning matrix for any bone the SKN names: an appended mesh-free bone
    (Dolphin, 2026-10-12) and vanilla bones the donor never skins, mesh-free
    or owning a rigid mesh (Mario High bones 49 and 55, Dolphin 2026-10-24)."""
    hierarchy = (model.get('BoneHierarchy') or []) + (model.get('BoneHierarchyEdited') or [])
    return {int(b['BoneId']) for b in hierarchy if 'BoneId' in b} | _skin_bone_ids(model.get('SkinData'))


def _validate_bone_hierarchy_edited(model: dict) -> None:
    """Reject an invalid ``BoneHierarchyEdited`` before any DAT/DOL write,
    alongside ``_validate_custom_submeshes``. Each error names the offending
    bone(s) so a failed patch is actionable. The rules are the ones in
    ``_docs/_docs_model_format/act_section.html#appending-bones``.

    Operates purely on the ``.sluggie`` JSON (``BoneHierarchy``/
    ``BoneHierarchyEdited``/``CustomSubmeshes``/``SkinData``/
    ``SkinDataEdited``), not the parsed ACT bytes -- this is the data-level
    gate ahead of ``_rebuild_act_bone_hierarchy``, which is
    itself only reachable once this passes. Sibling-chain order (the last
    unsafe topology change, "sibling chain reordered") has no representation in
    the ``.sluggie`` schema at all (only ``ParentBoneId`` per bone), so it
    cannot be checked at this layer; it is enforced by construction instead
    -- the rebuilder only ever appends a donor bone's own ``prev``/``next``
    unchanged and links a new leaf onto the tail of its parent's chain.
    """
    bone_hierarchy_edited = model.get('BoneHierarchyEdited')
    if not bone_hierarchy_edited:
        return

    errors: list[str] = []

    def fail(message: str) -> None:
        errors.append(message)

    # Rule 1: Hammerspace flag set.
    if not model.get('UseHammerspace'):
        raise ValueError(
            'BoneHierarchyEdited requires UseHammerspace; re-export the model from Blender'
        )

    donor_bones = model.get('BoneHierarchy') or []
    donor_by_id = {int(b['BoneId']): b for b in donor_bones}
    donor_count = len(donor_bones)

    edited_ids = [int(b['BoneId']) for b in bone_hierarchy_edited]
    if len(set(edited_ids)) != len(edited_ids):
        fail('BoneHierarchyEdited has duplicate BoneId values')
    edited_by_id = {int(b['BoneId']): b for b in bone_hierarchy_edited}

    new_entries = [b for b in bone_hierarchy_edited if b.get('UserAdded')]
    new_ids = sorted(int(b['BoneId']) for b in new_entries)

    # Rule 5: new bone ids are exactly N, N+1, ... contiguous from the donor count.
    expected_new_ids = list(range(donor_count, donor_count + len(new_ids)))
    if new_ids != expected_new_ids:
        fail(
            f'new bone ids must be contiguous starting at the donor bone count '
            f'{donor_count} (got {new_ids}, expected {expected_new_ids})'
        )

    # Rule 6: total bone count <= 255 (the kind-2 mirror table stores u8 ids).
    total_bone_count = donor_count + len(new_ids)
    if total_bone_count > 255:
        fail(f'total bone count {total_bone_count} exceeds the 255-bone cap (the mirror table stores u8 bone ids)')

    # CustomSubmeshes may legitimately move a donor bone's GeoId away from
    # 0xFFFF; that is not a Rule 2 violation.
    claimed_donor_bones = {
        int(cs['HostBoneId'])
        for cs in model.get('CustomSubmeshes') or []
        if isinstance(cs.get('HostBoneId'), int) and int(cs['HostBoneId']) < donor_count
    }

    # Rule 2: BoneHierarchyEdited is append-only against BoneHierarchy.
    for bone_id, donor_bone in donor_by_id.items():
        edited_bone = edited_by_id.get(bone_id)
        if edited_bone is None:
            fail(f'donor bone {bone_id} is missing from BoneHierarchyEdited (donor bones cannot be deleted)')
            continue
        if edited_bone.get('UserAdded'):
            fail(f'donor bone {bone_id} is marked UserAdded in BoneHierarchyEdited')
            continue

        donor_geo_raw = _bone_geo_id_raw(donor_bone)
        edited_geo_raw = _bone_geo_id_raw(edited_bone)
        if donor_geo_raw != edited_geo_raw:
            retargeted = donor_bone.get('GeoIdEdited') is not None and int(donor_bone['GeoIdEdited']) == edited_geo_raw
            if not retargeted and bone_id not in claimed_donor_bones:
                fail(
                    f'donor bone {bone_id} GeoId changed from {donor_geo_raw} to {edited_geo_raw} '
                    'without a CustomSubmeshes claim or GeoIdEdited retarget'
                )

        donor_mirror_id = donor_bone.get('MirrorBoneId')
        edited_mirror_id = edited_bone.get('MirrorBoneId')
        if donor_mirror_id is not None and donor_mirror_id != edited_mirror_id:
            fail(
                f'donor bone {bone_id} mirror pair changed from {donor_mirror_id} to {edited_mirror_id}'
            )
        donor_mirror_role = donor_bone.get('MirrorRole')
        edited_mirror_role = edited_bone.get('MirrorRole')
        if donor_mirror_role is not None and donor_mirror_role != edited_mirror_role:
            fail(
                f'donor bone {bone_id} mirror role changed from {donor_mirror_role} to {edited_mirror_role}'
            )

        # Rule 3: donor topology is byte-identical except for new leaves.
        donor_parent = donor_bone.get('ParentBoneId')
        edited_parent = edited_bone.get('ParentBoneId')
        donor_parent_id = None if donor_parent is None else int(donor_parent)
        edited_parent_id = None if edited_parent is None else int(edited_parent)
        if donor_parent_id != edited_parent_id:
            if edited_parent_id is not None and edited_parent_id in {int(b['BoneId']) for b in new_entries}:
                fail(
                    f'new bone {edited_parent_id} was inserted between donor bones '
                    f'{donor_parent_id} and {bone_id}; new bones can only hang off the '
                    'tree as leaves (inserting one reparents the donor child and breaks its animation)'
                )
            elif (
                edited_parent_id is not None
                and edited_parent_id in donor_by_id
                and donor_by_id[edited_parent_id].get('ParentBoneId') is not None
                and int(donor_by_id[edited_parent_id]['ParentBoneId']) == bone_id
            ):
                fail(f'donor bones {bone_id} and {edited_parent_id} swapped their parent/child relation')
            else:
                fail(f'donor bone {bone_id} was reparented from {donor_parent_id} to {edited_parent_id}')

    # Rule 4: every new bone is a leaf of the donor tree -- no donor bone may
    # name a new bone as its parent (independent of how rule 3's diff reads
    # the exporter's ParentBoneId, so a donor-onto-new parenting is caught
    # even if the rule-3 diff above is somehow bypassed).
    new_id_set = {int(b['BoneId']) for b in new_entries}
    for bone in bone_hierarchy_edited:
        if bone.get('UserAdded'):
            continue
        parent_id = bone.get('ParentBoneId')
        if parent_id is not None and int(parent_id) in new_id_set:
            fail(
                f"donor bone {bone['BoneId']} names new bone {int(parent_id)} as its parent; "
                'new bones must be leaves'
            )

    # Stadiums take new root and child bones (Dolphin 2026-10-18); elsewhere only children.
    is_stadium = model.get('ChunkNumber') in STADIUM_CHUNKS

    # Rules 7-8: every new bone has an existing parent (a stadium's may be a
    # root instead) and the user-contract defaults (self-mirrored, role 3),
    # and either no track or its own ADDED_TRACK_IDS id that no other bone reads.
    track_users: dict[int, list[int]] = {}
    for bone in bone_hierarchy_edited:
        track = bone.get('TrackId')
        if track is not None and int(track) != 0xFFFF:
            track_users.setdefault(int(track), []).append(int(bone['BoneId']))
    for entry in new_entries:
        bone_id = int(entry['BoneId'])
        parent_id = entry.get('ParentBoneId')
        if parent_id is None:
            if not is_stadium:
                fail(f'new bone {bone_id} has no ParentBoneId; new bones may not be roots outside stadiums')
        elif int(parent_id) not in edited_by_id:
            fail(f"new bone {bone_id}'s parent {int(parent_id)} does not exist")

        track_id = entry.get('TrackId')
        if track_id is None or (int(track_id) != 0xFFFF and int(track_id) not in ADDED_TRACK_IDS):
            fail(
                f'new bone {bone_id} has TrackId {track_id!r}, expected 0xFFFF (no track) or '
                f'0x{ADDED_TRACK_IDS[0]:X}-0x{ADDED_TRACK_IDS[-1]:X} (its own animation track)'
            )
        elif int(track_id) != 0xFFFF and len(track_users[int(track_id)]) > 1:
            others = sorted(b for b in track_users[int(track_id)] if b != bone_id)
            fail(f'new bone {bone_id} shares TrackId 0x{int(track_id):X} with bone(s) {others}')
        mirror_id = entry.get('MirrorBoneId')
        if mirror_id is None or int(mirror_id) != bone_id:
            fail(f'new bone {bone_id} has MirrorBoneId {mirror_id!r}, expected its own id {bone_id}')
        mirror_role = entry.get('MirrorRole')
        if mirror_role is None or int(mirror_role) != 3:
            fail(f'new bone {bone_id} has MirrorRole {mirror_role!r}, expected 3')

    # Rule 9: donor mirror table is a clean boneCount-long involution, or absent.
    mirror_entries = {
        bone_id: (donor_bone.get('MirrorBoneId'), donor_bone.get('MirrorRole'))
        for bone_id, donor_bone in donor_by_id.items()
    }
    present = {bid: pair for bid, pair in mirror_entries.items() if pair[0] is not None}
    if present and len(present) != donor_count:
        fail(
            f'donor mirror table is present on {len(present)} of {donor_count} bones; '
            'it must cover every bone or none'
        )
    else:
        for bone_id, (mirror_id, _role) in present.items():
            mirror_id = int(mirror_id)
            if mirror_id not in donor_by_id:
                fail(f'donor bone {bone_id} mirror id {mirror_id} does not exist')
                continue
            target = donor_by_id[mirror_id].get('MirrorBoneId')
            if target is None or int(target) != bone_id:
                fail(
                    f'donor mirror table is not an involution: bone {bone_id} -> {mirror_id} -> {target} '
                    '(malformed donor mirror table; refused rather than guessed at)'
                )

    # Rule 10: every SKN record names a donor bone or an added bone (an added
    # bone may drive skinning, Dolphin 2026-10-12).
    known_ids = set(range(donor_count)) | set(new_ids)
    for skin_key in ('SkinData', 'SkinDataEdited'):
        skin_data = model.get(skin_key)
        if not skin_data:
            continue
        for sk1 in skin_data.get('SK1s') or []:
            if int(sk1['BoneIndex']) not in known_ids:
                fail(f"{skin_key}: SK1 entry names bone {sk1['BoneIndex']}, which is neither a donor nor an added bone")
        for sk2 in skin_data.get('SK2s') or []:
            for bone_field in ('BoneIndex1', 'BoneIndex2'):
                if int(sk2[bone_field]) not in known_ids:
                    fail(f"{skin_key}: SK2 entry names bone {sk2[bone_field]}, which is neither a donor nor an added bone")
        for skacc in skin_data.get('SKAccs') or []:
            if int(skacc['BoneIndex']) not in known_ids:
                fail(f"{skin_key}: SKAcc entry names bone {skacc['BoneIndex']}, which is neither a donor nor an added bone")

    if errors:
        raise ValueError('; '.join(errors))


def _validate_skin_bones(model: dict) -> None:
    """Refuse a ``SkinDataEdited`` that skins to a bone the skeleton does not
    have (``_allowed_skin_bones``)."""
    edited = model.get('SkinDataEdited')
    if not edited:
        return
    extra = sorted(_skin_bone_ids(edited) - _allowed_skin_bones(model))
    if extra:
        raise ValueError(
            f"SkinDataEdited skins to bone(s) {extra}, which this model's skeleton does not have. "
            'Weight those vertices to bones of this model')


_RIGID_REBUILD_M3_FIELDS = (
    'FacesDataEdited', 'FacesCountEdited', 'FaceTextureIndicesEdited', 'FaceSurfaceIdsEdited',
)
_RIGID_REBUILD_M3_CHANNEL_FIELDS = {
    'VertexBuffer': ('VertexBufferDataEdited',),
    'NormalBuffer': ('NormalBufferDataEdited', 'NormalFacesDataEdited'),
    'UVChannels': ('UVChannelDataEdited', 'UVFacesDataEdited'),
    'ColorChannels': ('ColorChannelDataEdited', 'ColorFacesDataEdited'),
}
_RIGID_REBUILD_NEW_SURFACE_KEY_RE = re.compile(r'^sm(\d+)_new(\d+)$')
#: int16 position formats: GX format nibble 3 (s16), any fixed-point shift.
_RIGID_REBUILD_POSITION_FORMAT_NIBBLE = 3
#: The ``VertexBufferQuantizeInfo`` a rebuild entry may carry: any s16 format
#: (shift 0..15). The exporter writes one, coarser than the donor's, only when
#: the mesh outgrew the donor's range; s16 keeps the 2-byte components, so
#: only the fixed-point shift in the headers changes.
_REBUILD_POSITION_QUANTIZE_VALUES = tuple(range(0x30, 0x40))


def _rebuild_position_quantize_error(rebuild: dict) -> str | None:
    """Why a rebuild entry's own ``VertexBufferQuantizeInfo`` is unusable, or
    None (absent: the donor's format applies)."""
    value = rebuild.get('VertexBufferQuantizeInfo')
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value not in _REBUILD_POSITION_QUANTIZE_VALUES:
        return (
            f'VertexBufferQuantizeInfo {value!r} must be an s16 position format '
            f'({_REBUILD_POSITION_QUANTIZE_VALUES[0]}..{_REBUILD_POSITION_QUANTIZE_VALUES[-1]})'
        )
    return None


def _rebuild_position_quantize(sub: dict, rebuild: 'RigidRebuild | SkinnedRebuild') -> int:
    """The position format a rebuilt submesh is written in: the rebuild's own
    when the exporter widened it, else the donor's."""
    if rebuild.position_quantize is not None:
        return rebuild.position_quantize
    return int(sub['VertexBuffer']['VertexBufferQuantizeInfo'])


def _follow_position_shift(header_quantize: int, donor_quantize: int, quantize: int) -> int:
    """*header_quantize* (the body's normal header or the SKN header, which
    read the interleaved position + normal records) with its fixed-point
    shift moved to *quantize*'s when it shared the donor position shift. The
    type nibble is kept: the SKN header's is 0 while the GPL one's is 3."""
    if (header_quantize & 0xF) != (donor_quantize & 0xF):
        return header_quantize
    return (header_quantize & 0xF0) | (quantize & 0xF)


def _widened_position_note(sub: dict, rebuild: 'RigidRebuild | SkinnedRebuild') -> str:
    """Log suffix naming a position format that differs from the donor's."""
    donor = int(sub['VertexBuffer']['VertexBufferQuantizeInfo'])
    used = _rebuild_position_quantize(sub, rebuild)
    if used == donor:
        return ''
    return f'; positions in format {used} (donor {donor}), range +/-{32768 >> (used & 0xF):g}'


def _rigid_rebuild_in_place_edit_fields(sub: dict) -> list[str]:
    """Names of the in-place edit fields present on *sub*; a
    rebuilt submesh may carry none of them."""
    present = [field for field in _RIGID_REBUILD_M3_FIELDS if sub.get(field) is not None]
    for container, fields in _RIGID_REBUILD_M3_CHANNEL_FIELDS.items():
        entries = sub.get(container)
        if isinstance(entries, dict):
            entries = [entries]
        for entry in entries or []:
            present += [f'{container}.{field}' for field in fields if entry.get(field) is not None]
    return present


def _rigid_rebuild_drawable_surfaces(states: list) -> dict[str, int]:
    """``{SurfaceId: state index}`` of every donor record that can draw: it
    inherits a Type-3 attribute layout and a Type-7 shader. Type 7 is
    only required where the submesh uses it: item and stadium submeshes often
    have none, and their surfaces still draw. A record that draws donor faces
    before the submesh's first Type 7 draws with the Type 7 the previous mesh
    left (stadium draw state carries over, Dolphin 2026-10-18), so it can
    draw too."""
    needs_type7 = any(int(state.get('DisplayStateId', -1)) == 7 for state in states)
    return {
        state['SurfaceId']: index
        for index, state in enumerate(states)
        if state.get('SurfaceId')
        and _custom_submesh_effective_state(states, index, 3) is not None
        and (not needs_type7 or _custom_submesh_effective_state(states, index, 7) is not None
             or int(state.get('FaceCount') or 0) > 0)
    }


def _facial_pose_submeshes(model: dict) -> set[int]:
    """Submesh indices a ptr7 facial object references (their pose data holds
    donor vertex references, so their topology is fixed)."""
    objects = (model.get('FacialPoseData') or {}).get('Objects') or []
    return {
        int(entry['SubmeshIndex']) for entry in objects
        if isinstance(entry, dict) and entry.get('SubmeshIndex') is not None
    }


def _validate_rebuild_geometry(
    rebuild: dict, fail, use_compression: bool, stride: int, record_label: str,
) -> tuple[int | None, int | None]:
    """The checks a ``RigidRebuild`` and a ``SkinnedRebuild`` share for ``FacesCount``,
    ``VertexBufferData`` (``stride`` bytes per vertex, named ``record_label`` in the message) and
    ``FacesData``. Returns ``(faces_count, vertex_count)``, each None where it is unusable."""
    faces_count = rebuild.get('FacesCount')
    if not isinstance(faces_count, int) or isinstance(faces_count, bool) or not (1 <= faces_count <= 0xFFFF):
        fail(f'FacesCount {faces_count!r} must be a uint16 value of at least 1')
        faces_count = None

    vertex_bytes = _decode(rebuild['VertexBufferData'], use_compression) if rebuild.get('VertexBufferData') else b''
    vertex_count = None
    if not vertex_bytes or len(vertex_bytes) % stride:
        fail(f'VertexBufferData length {len(vertex_bytes)} is not a whole number of {stride}-byte {record_label}')
    else:
        vertex_count = len(vertex_bytes) // stride
        if vertex_count > 0xFFFF:
            fail(f'vertex count {vertex_count} exceeds the uint16 index range')

    if faces_count is not None:
        faces_bytes = _decode(rebuild['FacesData'], use_compression) if rebuild.get('FacesData') is not None else b''
        if len(faces_bytes) != faces_count * 6:
            fail(
                f'FacesData length {len(faces_bytes)} does not match FacesCount '
                f'{faces_count} (expected {faces_count * 6} bytes)'
            )
        elif vertex_count is not None:
            for vertex_index in struct.unpack(f'>{len(faces_bytes) // 2}H', faces_bytes):
                if vertex_index >= vertex_count:
                    fail(f'face index {vertex_index} is out of range for {vertex_count} vertices')
                    break
    return faces_count, vertex_count


def _rebuild_loop_buffer_check(fail, use_compression: bool, faces_count: int | None):
    """The per-loop array check of a rebuild: the indexed array itself, and three loops per face."""
    def check(label: str, data_field, faces_field, stride: int) -> None:
        _validate_custom_submesh_indexed_array(fail, label, use_compression, data_field, faces_field, stride)
        if faces_count is not None and faces_field is not None:
            loops = len(_decode(faces_field, use_compression)) // 2
            if loops != faces_count * 3:
                fail(f'{label} face-index buffer holds {loops} loops, expected {faces_count * 3}')
    return check


def _validate_rebuild_colors_and_uvs(sub: dict, rebuild: dict, fail, check_loop_buffer) -> dict:
    """A rebuild carries the donor's colour channel and exactly the donor's UV channels.
    Returns the donor UV channels by index."""
    donor_colors = sub.get('ColorChannels') or []
    if donor_colors:
        if rebuild.get('ColorChannelData') is None or rebuild.get('ColorFacesData') is None:
            fail('must carry ColorChannelData and ColorFacesData: the donor submesh has a color channel')
        else:
            stride = _color_entry_size(int(donor_colors[0].get('ColorChannelQuantizeInfo', 48)))
            check_loop_buffer('ColorChannelData', rebuild.get('ColorChannelData'), rebuild.get('ColorFacesData'), stride)
    elif rebuild.get('ColorChannelData') is not None or rebuild.get('ColorFacesData') is not None:
        fail('carries colors but the donor submesh has no color channel')

    donor_uv_by_index = {int(uv['UVChannelIndex']): uv for uv in sub.get('UVChannels') or []}
    rebuild_uv_by_index = {}
    for uv in rebuild.get('UVChannels') or []:
        channel = uv.get('UVChannelIndex')
        if not isinstance(channel, int) or channel in rebuild_uv_by_index:
            fail(f'UVChannels has a missing or repeated UVChannelIndex {channel!r}')
            continue
        rebuild_uv_by_index[channel] = uv
    if set(rebuild_uv_by_index) != set(donor_uv_by_index):
        fail(
            f'UVChannels {sorted(rebuild_uv_by_index)} must be the donor channel set '
            f'{sorted(donor_uv_by_index)}'
        )
    for channel, uv in rebuild_uv_by_index.items():
        donor_uv = donor_uv_by_index.get(channel)
        if donor_uv is None:
            continue
        stride = int(donor_uv.get('UVChannelCompCount', 2)) * _vb_comp_size(
            int(donor_uv.get('UVChannelQuantizeInfo', _CUSTOM_SUBMESH_UV_FORMAT[1]))
        )
        check_loop_buffer(f'UVChannels[{channel}]', uv.get('UVChannelData'), uv.get('UVFacesData'), stride)
    return donor_uv_by_index


def _validate_rebuild_surfaces(
    model: dict, index: int, sub: dict, rebuild: dict, fail, use_compression: bool, faces_count: int | None,
    *, uv_channel_count: int, has_normals: bool, is_stadium: bool, rigid_surfaces: dict, additional_names: set,
) -> None:
    """``FaceSurfaceTable``, ``FaceSurfaceIndices``, ``NewSurfaces`` and ``Reason`` of a rebuild."""
    drawable = _rigid_rebuild_drawable_surfaces(sub.get('DisplayStates') or [])
    table = rebuild.get('FaceSurfaceTable')
    if not isinstance(table, list) or not table or not all(isinstance(key, str) and key for key in table):
        fail('FaceSurfaceTable must be a non-empty list of surface keys')
        table = []
    elif len(set(table)) != len(table):
        fail('FaceSurfaceTable lists a surface key twice')

    new_surfaces = rebuild.get('NewSurfaces') or []
    if not isinstance(new_surfaces, list) or not all(isinstance(entry, dict) for entry in new_surfaces):
        fail('NewSurfaces must be a list of objects')
        new_surfaces = []
    new_keys = [entry.get('SurfaceKey') for entry in new_surfaces]
    if len(set(new_keys)) != len(new_keys):
        fail('NewSurfaces keys are not unique')

    for key in table:
        if key in drawable or key in new_keys:
            continue
        fail(
            f'FaceSurfaceTable key {key!r} is neither a drawing surface of this submesh '
            f'({sorted(drawable)}) nor a NewSurfaces key'
        )

    indices_raw = _decode(rebuild['FaceSurfaceIndices'], use_compression) if rebuild.get('FaceSurfaceIndices') is not None else b''
    used_keys: set[str] = set()
    if len(indices_raw) % 2:
        fail(f'FaceSurfaceIndices length {len(indices_raw)} is not a whole number of uint16 values')
    else:
        indices = struct.unpack(f'>{len(indices_raw) // 2}H', indices_raw)
        if faces_count is not None and len(indices) != faces_count:
            fail(f'FaceSurfaceIndices holds {len(indices)} entries for FacesCount {faces_count}')
        for value in indices:
            if value >= len(table):
                fail(f'FaceSurfaceIndices value {value} is out of range for {len(table)} table entries')
                break
            used_keys.add(table[value])

    for entry in new_surfaces:
        key = entry.get('SurfaceKey')

        def fail_surface(message: str, _key=key) -> None:
            fail(f'new surface {_key!r}: {message}')

        match = _RIGID_REBUILD_NEW_SURFACE_KEY_RE.match(str(key))
        if match is None or int(match.group(1)) != index:
            fail_surface(f'key must match sm{index}_new<K>')
        if key not in table:
            fail_surface('is not listed in FaceSurfaceTable')
        elif key not in used_keys:
            fail_surface('is used by no face')
        _validate_new_surface_entry(
            model, entry, fail_surface,
            uv_channel_count=uv_channel_count,
            has_normals=has_normals,
            is_stadium=is_stadium,
            rigid_surfaces=rigid_surfaces,
            additional_names=additional_names,
        )

    reasons = rebuild.get('Reason')
    if reasons is not None and not (
        isinstance(reasons, list) and all(isinstance(reason, str) for reason in reasons)
    ):
        fail('Reason must be a list of strings')


def _validate_rigid_rebuilds(model: dict) -> None:
    """Reject an invalid
    ``Submeshes[i].RigidRebuild`` before any DAT/DOL write. Each error names
    ``sub<i>``."""
    submeshes = model.get('Submeshes') or []
    rebuilt = [(index, sub) for index, sub in enumerate(submeshes) if sub.get('RigidRebuild')]
    if not rebuilt:
        return
    if not model.get('UseHammerspace'):
        raise ValueError('RigidRebuild requires UseHammerspace; re-export the model from Blender')

    errors: list[str] = []
    use_compression = model.get('UseCompression', True)
    bones = _bones_by_id(model)
    rigid_surfaces = _custom_submesh_rigid_surfaces(model)
    is_stadium = model.get('ChunkNumber') in STADIUM_CHUNKS
    facial_submeshes = _facial_pose_submeshes(model)
    additional_names = {
        entry.get('TextureFileName') for entry in model.get('AdditionalTextureDescriptors') or []
    }

    for index, sub in rebuilt:
        rebuild = sub['RigidRebuild']

        def fail(message: str, _index=index) -> None:
            errors.append(f'sub{_index}: RigidRebuild {message}')

        if not isinstance(rebuild, dict):
            fail('must be an object')
            continue

        vb = sub.get('VertexBuffer') or {}
        comp_count = vb.get('VertexBufferCompCount')
        if comp_count != 3:
            fail(f'needs a rigid donor submesh (VertexBufferCompCount 3), got {comp_count!r}')
        quantize = vb.get('VertexBufferQuantizeInfo')
        if not isinstance(quantize, int) or isinstance(quantize, bool) \
                or (quantize >> 4) != _RIGID_REBUILD_POSITION_FORMAT_NIBBLE:
            fail(
                f'donor position format {quantize!r} is not int16 (high nibble '
                f'{_RIGID_REBUILD_POSITION_FORMAT_NIBBLE}); only int16 rigid positions can be rebuilt'
            )
            quantize = _CUSTOM_SUBMESH_POSITION_FORMAT[1]
        quantize_error = _rebuild_position_quantize_error(rebuild)
        if quantize_error:
            fail(quantize_error)
        position_stride = 3 * _vb_comp_size(quantize)

        in_place = _rigid_rebuild_in_place_edit_fields(sub)
        if in_place:
            fail('cannot be combined with in-place edit fields on the same submesh: ' + ', '.join(in_place))
        host_bone_id = rebuild.get('HostBoneId')
        if not isinstance(host_bone_id, int) or isinstance(host_bone_id, bool) or not (0 <= host_bone_id <= 0xFFFF):
            fail(f'HostBoneId {host_bone_id!r} must be a uint16 bone index')
        else:
            owners = sorted(
                bone_id for bone_id, bone in bones.items() if _effective_geo_id_raw(bone) == index
            )
            if host_bone_id not in bones:
                fail(f'host bone {host_bone_id} does not exist in BoneHierarchy')
            elif owners != [host_bone_id]:
                fail(
                    f'HostBoneId {host_bone_id} does not match the effective owner bone(s) '
                    f'{owners} of submesh {index} (GeoIdEdited where set, else GeoIdRaw)'
                )

        faces_count, vertex_count = _validate_rebuild_geometry(
            rebuild, fail, use_compression, position_stride, 'positions')
        _check_loop_buffer = _rebuild_loop_buffer_check(fail, use_compression, faces_count)

        donor_normals = sub.get('NormalBuffer') if isinstance(sub.get('NormalBuffer'), dict) else None
        normal_count = normal_stride = None
        if donor_normals and donor_normals.get('NormalBufferData'):
            if rebuild.get('NormalBufferData') is None or rebuild.get('NormalFacesData') is None:
                fail('must carry NormalBufferData and NormalFacesData: the donor submesh has a NormalBuffer')
            else:
                normal_stride = int(donor_normals.get('NormalBufferCompCount', 3)) * _vb_comp_size(
                    int(donor_normals.get('NormalBufferQuantizeInfo', _CUSTOM_SUBMESH_NORMAL_FORMAT[1]))
                )
                _check_loop_buffer('NormalBufferData', rebuild.get('NormalBufferData'), rebuild.get('NormalFacesData'), normal_stride)
                normal_count = len(_decode(rebuild['NormalBufferData'], use_compression) or b'') // normal_stride
        elif rebuild.get('NormalBufferData') is not None or rebuild.get('NormalFacesData') is not None:
            fail('carries normals but the donor submesh has no NormalBuffer')

        if index in facial_submeshes or rebuild.get('FacialPoses') is not None:
            # Facial poses (ptr7) on this submesh follow the rebuild through
            # FacialPoses; without them the patcher neutralizes the objects.
            _validate_rebuild_facial_poses(
                model, rebuild, fail, use_compression, index, object_error=_facial.rigid_object_error,
                vertex_count=vertex_count, position_stride=position_stride,
                normal_count=normal_count, normal_stride=normal_stride,
            )

        donor_uv_by_index = _validate_rebuild_colors_and_uvs(sub, rebuild, fail, _check_loop_buffer)
        _validate_rebuild_surfaces(
            model, index, sub, rebuild, fail, use_compression, faces_count,
            uv_channel_count=len(donor_uv_by_index),
            has_normals=bool(donor_normals and donor_normals.get('NormalBufferData')),
            is_stadium=is_stadium, rigid_surfaces=rigid_surfaces, additional_names=additional_names,
        )

    if errors:
        raise ValueError('; '.join(errors))


def _skinned_rebuild_draws_lighting(states: list) -> bool:
    """Whether any donor Type-3 state of the submesh declares a lighting
    (normal) attribute."""
    for state in states:
        if int(state.get('DisplayStateId', -1)) != 3:
            continue
        try:
            descriptors = _custom_submesh_type3_descriptors(int(state.get('ShaderMode', '0'), 16))
        except ValueError:
            continue
        if any(descriptor['key'] == 'lighting' for descriptor in descriptors):
            return True
    return False


def _validate_skinned_rebuild(model: dict) -> None:
    """Reject an invalid ``Submeshes[0].SkinnedRebuild`` before any write.
    Each error names ``sub<i>``."""
    submeshes = model.get('Submeshes') or []
    rebuilt = [(index, sub) for index, sub in enumerate(submeshes) if sub.get('SkinnedRebuild')]
    if not rebuilt:
        return
    if not model.get('UseHammerspace'):
        raise ValueError('SkinnedRebuild requires UseHammerspace; re-export the model from Blender')

    errors: list[str] = []
    use_compression = model.get('UseCompression', True)
    skin = model.get('SkinData') or {}
    skinned_bones = _allowed_skin_bones(model) if skin else set()
    rigid_surfaces = _custom_submesh_rigid_surfaces(model)
    is_stadium = model.get('ChunkNumber') in STADIUM_CHUNKS
    additional_names = {
        entry.get('TextureFileName') for entry in model.get('AdditionalTextureDescriptors') or []
    }

    for index, sub in rebuilt:
        rebuild = sub['SkinnedRebuild']

        def fail(message: str, _index=index) -> None:
            errors.append(f'sub{_index}: SkinnedRebuild {message}')

        if not isinstance(rebuild, dict):
            fail('must be an object')
            continue
        if index != _SKINNED_REBUILD_SUBMESH:
            fail(f'is only supported on submesh {_SKINNED_REBUILD_SUBMESH}, the one the SKN writes into')
        vb = sub.get('VertexBuffer') or {}
        comp_count = vb.get('VertexBufferCompCount')
        if comp_count != _SKINNED_REBUILD_COMP_COUNT:
            fail(f'needs the skinned donor submesh (VertexBufferCompCount 6), got {comp_count!r}')
        quantize = vb.get('VertexBufferQuantizeInfo')
        if not isinstance(quantize, int) or isinstance(quantize, bool) \
                or (quantize >> 4) != _RIGID_REBUILD_POSITION_FORMAT_NIBBLE:
            fail(
                f'donor position format {quantize!r} is not int16 (high nibble '
                f'{_RIGID_REBUILD_POSITION_FORMAT_NIBBLE}); only int16 skinned positions can be rebuilt'
            )
            quantize = _CUSTOM_SUBMESH_POSITION_FORMAT[1]
        stride = _SKINNED_REBUILD_COMP_COUNT * _vb_comp_size(quantize)
        if not skin:
            fail('needs a donor SkinData: the model has no SKN section to rebuild')
        else:
            skn_quantize = int(skin.get('QuantizeInfo', 0))
            if _vb_comp_size(skn_quantize) != _vb_comp_size(quantize) or (skn_quantize & 0xF) != (quantize & 0xF):
                fail(
                    f'SkinData QuantizeInfo {skn_quantize} does not match the position format '
                    f'{quantize} (the SKN sources mirror the position buffer)'
                )
        quantize_error = _rebuild_position_quantize_error(rebuild)
        if quantize_error:
            fail(quantize_error)
        if sub.get('RigidRebuild'):
            fail('cannot be combined with a RigidRebuild on the same submesh')
        in_place = _rigid_rebuild_in_place_edit_fields(sub)
        if in_place:
            fail('cannot be combined with in-place edit fields on the same submesh: ' + ', '.join(in_place))
        if model.get('SkinDataEdited'):
            fail('cannot be combined with SkinDataEdited: the rebuild carries its own weights')

        faces_count, vertex_count = _validate_rebuild_geometry(
            rebuild, fail, use_compression, stride, 'position+normal records')

        raw_influences = _decode(rebuild['Influences'], use_compression) if rebuild.get('Influences') is not None else b''
        if len(raw_influences) % _SKINNED_REBUILD_INFLUENCE.size:
            fail(
                f'Influences length {len(raw_influences)} is not a whole number of '
                f'{_SKINNED_REBUILD_INFLUENCE.size}-byte (vertex, bone, weight) records'
            )
        elif vertex_count is not None:
            weighted: set[int] = set()
            unknown_bones: set[int] = set()
            bad_weight = None
            for vertex, bone, weight in _SKINNED_REBUILD_INFLUENCE.iter_unpack(raw_influences):
                if vertex >= vertex_count:
                    fail(f'Influences names vertex {vertex}, outside the {vertex_count} vertices')
                    break
                if not (weight > 0) or weight in (float('inf'), float('-inf')):
                    if bad_weight is None:
                        bad_weight = (vertex, bone, weight)
                    continue
                if skin and bone not in skinned_bones:
                    unknown_bones.add(bone)
                weighted.add(vertex)
            if bad_weight is not None:
                fail(f'Influences vertex {bad_weight[0]} bone {bad_weight[1]} has a non-positive or non-finite weight {bad_weight[2]!r}')
            if unknown_bones:
                fail(
                    f"skins to bone(s) {sorted(unknown_bones)}, which this model's skeleton does not have. "
                    'Weight those vertices to bones of this model'
                )
            missing = sorted(set(range(vertex_count)) - weighted)
            if missing:
                shown = ', '.join(str(v) for v in missing[:10])
                more = f' (+{len(missing) - 10} more)' if len(missing) > 10 else ''
                fail(f'{len(missing)} vertex(es) have no skin influence: {shown}{more}')

        _check_loop_buffer = _rebuild_loop_buffer_check(fail, use_compression, faces_count)

        if rebuild.get('NormalBufferData') is not None or rebuild.get('NormalFacesData') is not None:
            fail('carries a standalone normal array; a skinned submesh keeps its normals interleaved in VertexBufferData')

        donor_uv_by_index = _validate_rebuild_colors_and_uvs(sub, rebuild, fail, _check_loop_buffer)
        _validate_rebuild_surfaces(
            model, index, sub, rebuild, fail, use_compression, faces_count,
            uv_channel_count=len(donor_uv_by_index),
            has_normals=_skinned_rebuild_draws_lighting(sub.get('DisplayStates') or []),
            is_stadium=is_stadium, rigid_surfaces=rigid_surfaces, additional_names=additional_names,
        )

        _validate_skinned_rebuild_facial_poses(model, rebuild, fail, use_compression, vertex_count, stride, index)

    if errors:
        raise ValueError('; '.join(errors))


def _validate_facial_entry_list(
    fail, label: str, entry: dict, list_key: str, deltas_key: str, count: int | None, stride: int,
    pose_count: int, noun: str, plural: str,
) -> None:
    """One ``(indices, delta arrays)`` pair of a ``FacialPoses`` entry:
    ascending uint16 indices inside *count* entries, ``pose_count - 1``
    delta arrays of ``len(indices) * stride`` bytes."""
    raw_indices = _decode(entry.get(list_key)) if entry.get(list_key) is not None else b''
    if not raw_indices or len(raw_indices) % 2:
        fail(f'{label}: {list_key} must hold at least one uint16 index')
        return
    indices = struct.unpack(f'>{len(raw_indices) // 2}H', raw_indices)
    if any(b <= a for a, b in zip(indices, indices[1:])):
        fail(f'{label}: {list_key} must be strictly ascending')
    if count is not None and indices[-1] >= count:
        fail(f'{label}: {noun} {indices[-1]} is outside the {count} {plural}')
    deltas = entry.get(deltas_key)
    if not isinstance(deltas, list) or len(deltas) != pose_count - 1:
        fail(f'{label}: {deltas_key} must hold {pose_count - 1} arrays (the object has {pose_count} poses)')
        return
    for pose_index, delta in enumerate(deltas, start=1):
        raw = _decode(delta) if delta is not None else b''
        if len(raw) != len(indices) * stride:
            fail(
                f'{label}: {deltas_key}[{pose_index - 1}] is {len(raw)} bytes, expected '
                f'{len(indices)} x {stride}'
            )
            break


def _validate_rebuild_facial_poses(
    model: dict, rebuild: dict, fail, use_compression: bool, index: int, *, object_error, vertex_count: int | None,
    position_stride: int, normal_count: int | None = None, normal_stride: int | None = None,
) -> None:
    """``FacialPoses`` of a SkinnedRebuild or a RigidRebuild:
    one entry per facial object that addresses submesh *index*, each mapping
    ascending rebuilt vertices with one delta array per pose after pose
    zero; a rigid entry may add ``NormalEntries`` / ``NormalPoseDeltas``
    into the rebuilt normal array. *object_error* checks the donor object's
    attribute layout. Absent ``FacialPoses`` pass (the patcher drops or
    neutralizes the poses)."""
    poses = rebuild.get('FacialPoses')
    if poses is None:
        return
    if not isinstance(poses, list) or not all(isinstance(entry, dict) for entry in poses):
        fail('FacialPoses must be a list of objects')
        return
    facial = model.get('FacialPoseData') or {}
    error = _facial.facial_data_error(facial) if facial else 'the model has no FacialPoseData'
    if error:
        fail(f'FacialPoses: {error}; re-export the model')
        return
    by_index = {int(entry['ObjectIndex']): entry for entry in facial.get('Objects') or []}
    own_objects = {
        object_index for object_index, entry in by_index.items() if int(entry['SubmeshIndex']) == index
    }
    seen: set[int] = set()
    for entry in poses:
        object_index = entry.get('ObjectIndex')
        if not isinstance(object_index, int) or isinstance(object_index, bool) or object_index not in by_index:
            fail(f'FacialPoses names facial object {object_index!r}, which the model does not have')
            continue
        if object_index not in own_objects:
            fail(f'FacialPoses names facial object {object_index}, which does not address submesh {index}')
            continue
        if object_index in seen:
            fail(f'FacialPoses lists facial object {object_index} twice')
            continue
        seen.add(object_index)
        label = f'FacialPoses object {object_index}'
        obj = _facial.decode_objects({'Objects': [by_index[object_index]]}, lambda v: _decode(v, use_compression))[0]
        layout_error = object_error(obj, index)
        if layout_error:
            fail(f'{label}: {layout_error}')
            continue
        if normal_stride is None and any(a.kind == _facial.NORMAL_KIND and a.submesh_index == index for a in obj.attributes):
            fail(f'{label}: the donor object has a normal attribute on submesh {index} but the rebuild has no normal array')
            continue
        _validate_facial_entry_list(
            fail, label, entry, 'Vertices', 'PoseDeltas', vertex_count, position_stride, obj.pose_count,
            'vertex', 'vertices',
        )
        if entry.get('NormalEntries') is None and entry.get('NormalPoseDeltas') is None:
            continue
        if normal_stride is None:
            fail(f'{label}: carries NormalEntries but the rebuilt submesh has no normal array')
            continue
        _validate_facial_entry_list(
            fail, label, entry, 'NormalEntries', 'NormalPoseDeltas', normal_count, normal_stride, obj.pose_count,
            'normal entry', 'normal entries',
        )
    missing = sorted(own_objects - seen)
    if missing:
        fail(f'FacialPoses lacks facial object(s) {missing}, which address submesh {index}')


def _validate_skinned_rebuild_facial_poses(
    model: dict, rebuild: dict, fail, use_compression: bool, vertex_count: int | None, stride: int, index: int,
) -> None:
    """``FacialPoses`` of the skinned body: interleaved
    ``x y z nx ny nz`` records, no normal lists."""
    _validate_rebuild_facial_poses(
        model, rebuild, fail, use_compression, index, object_error=_facial.body_object_error,
        vertex_count=vertex_count, position_stride=stride,
    )


def _validate_section_modes(modes: SectionModes) -> None:
    for section, mode in modes.as_dict().items():
        if mode not in {'clone', 'build'}:
            raise ValueError(f"{section} mode must be 'clone' or 'build', got {mode!r}")


def _collect_missing_hammerspace_properties(model: dict) -> list[str]:
    """Return the subset of hammerspace rebuild properties that are missing."""
    missing: list[str] = []

    for key in ('ChunkNumber', 'FileIndex', 'ModelOffset', 'ModelLength'):
        if key not in model or model.get(key) in (None, ''):
            missing.append(key)

    if 'Submeshes' not in model or not isinstance(model.get('Submeshes'), list) or not model['Submeshes']:
        missing.append('Submeshes')
        return missing

    for sub_idx, sub in enumerate(model['Submeshes']):
        if not isinstance(sub, dict):
            missing.append(f'Submeshes[{sub_idx}]')
            continue

        vb = sub.get('VertexBuffer')
        if not isinstance(vb, dict):
            missing.append(f'Submeshes[{sub_idx}].VertexBuffer')
        else:
            for key in ('VertexBufferData', 'VertexBufferCompCount', 'VertexBufferQuantizeInfo'):
                if key not in vb or vb.get(key) in (None, ''):
                    missing.append(f'Submeshes[{sub_idx}].VertexBuffer.{key}')

        if 'FacesData' not in sub or sub.get('FacesData') in (None, ''):
            missing.append(f'Submeshes[{sub_idx}].FacesData')

        ds = sub.get('DisplayStates')
        if not isinstance(ds, list) or not ds:
            missing.append(f'Submeshes[{sub_idx}].DisplayStates')
            continue

        for ds_idx, display_state in enumerate(ds):
            if not isinstance(display_state, dict):
                missing.append(f'Submeshes[{sub_idx}].DisplayStates[{ds_idx}]')
                continue
            for key in ('DisplayStateId', 'PrimListLength', 'ShaderMode'):
                if key not in display_state or display_state.get(key) in (None, ''):
                    missing.append(f'Submeshes[{sub_idx}].DisplayStates[{ds_idx}].{key}')
            if (display_state.get('PrimListLength', 0) > 0
                    and display_state.get('PrimListData') in (None, '')):
                missing.append(f'Submeshes[{sub_idx}].DisplayStates[{ds_idx}].PrimListData')

    return missing


def _validate_hammerspace_contract(model: dict, modes: SectionModes) -> None:
    """Require the full export of a model before any section is rebuilt from it. An all-clone
    build copies the donor and needs none of it."""
    if modes == SectionModes():
        return

    missing = _collect_missing_hammerspace_properties(model)
    if missing:
        missing_list = ', '.join(missing)
        raise ValueError(
            'hammerspace rebuild is missing required rebuild properties: '
            f'{missing_list}; re-export the model from Blender/SluggiesTools before patching'
        )
