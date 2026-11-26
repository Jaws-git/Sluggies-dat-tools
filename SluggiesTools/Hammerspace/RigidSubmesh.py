"""Rigid submesh blobs: appended custom submeshes and rebuilt donor submeshes.

``PatchGPLAppendSubmesh`` appends the blobs of the ``CustomSubmeshes``;
``PatchGPLReplaceRigidSubmeshes`` replaces the blob of every submesh with a ``RigidRebuild``. Both work
on a cloned donor GPL and leave the donor's other blobs as they are. ``_serialize_submesh_blob`` is the
one writer of a submesh blob (the skinned layout is its ``aligned`` form).
"""

import struct
from dataclasses import replace

import slogger as _slogger
import drawlist
from binfmt import (
    align4 as _align4,
    color_entry_size as _color_entry_size,
    comp_size as _vb_comp_size,
    shader_mode_bytes as _shader_mode_bytes,
)
from ModelFormat import align_up as _align32

from SluggieModel import (
    DrawState,
    NormalBuffer,
    UVChannel,
    ColorChannel,
    Submesh,
    CustomSubmesh,
    RigidRebuildNewSurface,
    RigidRebuild,
    SluggieParsed,
    STADIUM_CHUNKS,
    _CUSTOM_SUBMESH_POSITION_FORMAT,
    _CUSTOM_SUBMESH_NORMAL_FORMAT,
    _CUSTOM_SUBMESH_UV_FORMAT,
    _CUSTOM_SUBMESH_COLOR_FORMATS,
)
from SubmeshTemplates import (
    _CUSTOM_SUBMESH_TYPE4_BY_UV_COUNT,
    _custom_submesh_texture_layer,
    _custom_submesh_type3_descriptors,
    _custom_submesh_type3_setting,
    _custom_submesh_index_size_for,
    _custom_submesh_derived_records,
    _custom_submesh_builtin_records,
    _resolve_custom_submesh_records,
    _custom_submesh_active_type3,
    _custom_submesh_descriptors,
    _custom_submesh_patch_layer0_texture,
    _custom_submesh_patch_specular_strength,
)
from HammerspaceValidate import (
    _custom_submesh_rigid_surfaces,
    _rebuild_position_quantize,
    _widened_position_note,
)


# ---------------------------------------------------------------------------
# CustomSubmeshes GPL append
# ---------------------------------------------------------------------------
# Builds the display-state list and raw GPL blob for each parsed
# CustomSubmesh and appends them to an already-cloned/patched GPL section,
# following the same clone + append + repoint approach as PatchGPLUVRebuild.
# _validate_custom_submeshes has already rejected anything malformed before
# this runs, so the helpers below assume a valid, already-checked entry.

def _custom_submesh_faces(cs: 'CustomSubmesh', descriptors: list[dict]) -> list[list[dict]]:
    """Zip a CustomSubmesh's per-attribute face-index buffers into the
    [v0, v1, v2]-per-triangle structure drawlist.encodeDrawList expects,
    keyed by the active Type-3 descriptor layout."""

    uv_by_channel = {uv.channel_index: uv for uv in cs.uv_channels}

    def _indices(key: str) -> tuple[int, ...]:
        if key == 'position':
            raw = cs.faces_data
        elif key == 'lighting':
            raw = cs.normal_faces_data or b''
        elif key in ('color0', 'color1'):
            raw = cs.color_faces_data or b''
        elif key.startswith('texture'):
            channel = uv_by_channel.get(int(key[len('texture'):]))
            raw = channel.uv_faces_data if channel else b''
        else:
            raw = b''
        return struct.unpack(f'>{len(raw) // 2}H', raw) if raw else ()

    per_key_indices = {descriptor['key']: _indices(descriptor['key']) for descriptor in descriptors}

    faces = []
    for face_index in range(cs.faces_count):
        triangle = []
        for vertex_slot in range(3):
            flat_index = face_index * 3 + vertex_slot
            vertex = {
                key: (indices[flat_index] if flat_index < len(indices) else 0)
                for key, indices in per_key_indices.items()
            }
            triangle.append(vertex)
        faces.append(triangle)
    return faces


def _donor_palette_name(model: dict) -> str:
    """Return the model's TPL palette name, as carried by every donor UV
    header (``L_mario.tpl``, ``mario.tpl``, ...).

    A custom submesh has no donor UV header to inherit from, but the name is
    per-model rather than per-submesh, so any donor channel answers for all of
    them. Returns '' when the model has no donor UV channel to read.
    """
    for submesh in model.get('Submeshes') or []:
        for uv in submesh.get('UVChannels') or []:
            name = uv.get('PaletteName')
            if name:
                return name
    return ''


def _bindable_color_channel(records: list[list], color_channel: 'ColorChannel | None') -> 'ColorChannel | None':
    """``color_channel`` with a one-entry array written twice when a Type-3
    record of ``records`` reads color0 per vertex; unchanged otherwise.

    The renderer binds a colour array only when it has two or more entries
    (fn_8052E99C, 0x8052EBFC); one entry becomes a material colour. That is
    right for a vertex format without color0 (the Mii body's one-entry array),
    but a mesh that reads color0 per vertex with one entry (painted in one
    colour, or not at all) then indexes the previous mesh's array: a stadium
    cube drawn after Yoshi Park's field markings came out nearly transparent
    (Dolphin, 2026-10-18), and so did a skinned-rebuilt Koopa body (2026-10-24).
    A duplicate entry keeps every index valid and the colour the same."""
    if color_channel is None or len(color_channel.color_data) != _color_entry_size(color_channel.quantize_info):
        return color_channel
    reads_vertex_color = any(
        state_id == 3 and any(d['key'] == 'color0' for d in _custom_submesh_type3_descriptors(int(mode, 16)))
        for state_id, _pad, mode in records
    )
    if not reads_vertex_color:
        return color_channel
    return replace(color_channel, color_data=color_channel.color_data * 2)


def _assemble_rigid_submesh(
    submesh_index: int, mesh_name: str, records: list[list], primitive_lists: list[bytes],
    *, vertex_data: bytes, vertex_quantize_info: int, uv_channels: list,
    color_channel: 'ColorChannel | None', normal_buffer: 'NormalBuffer | None',
    faces_count: int, faces_data: bytes,
) -> 'Submesh':
    """Assemble already-resolved rigid geometry and display states into a
    Submesh dataclass, ready for _build_rigid_submesh_blob. ``records`` are
    ``[state_id, pad_bytes, shader_mode]`` lists and ``primitive_lists`` the
    encoded (32-byte padded) list per record, ``b''`` for a record that draws
    nothing. Fields the blob writer never reads (file-offset metadata that
    only matters for in-place patching of donor submeshes) are left at
    0/empty. Shared by custom submeshes and rebuilt donor rigid submeshes."""
    if len(primitive_lists) != len(records):
        raise ValueError(
            f'sub{submesh_index}: {len(primitive_lists)} primitive lists for '
            f'{len(records)} display-state records'
        )
    draw_states = [
        DrawState(
            display_state_id=state_id,
            display_state_pad_bytes=pad,
            prim_list_data=primitive_bytes,
            active_descriptors=[],
            shader_mode=mode,
        )
        for (state_id, pad, mode), primitive_bytes in zip(records, primitive_lists)
    ]
    color_channel = _bindable_color_channel(records, color_channel)
    return Submesh(
        submesh_index=submesh_index,
        mesh_name=mesh_name,
        faces_count=faces_count,
        faces_data=faces_data,
        vertex_data=vertex_data,
        vertex_comp_count=_CUSTOM_SUBMESH_POSITION_FORMAT[0],
        vertex_quantize_info=vertex_quantize_info,
        uv_channels=uv_channels,
        color_channels=[color_channel] if color_channel is not None else [],
        draw_states=draw_states,
        normal_buffer=normal_buffer,
        preserve_source_layout=False,
    )


def _custom_submesh_to_submesh(
    cs: 'CustomSubmesh', submesh_index: int, records: list[list],
    primitive_lists: list[bytes], palette_name: str = '',
) -> 'Submesh':
    """Assemble a CustomSubmesh's already-resolved geometry and display
    states into a Submesh dataclass, ready for _build_rigid_submesh_blob
    (see _assemble_rigid_submesh). ``primitive_lists`` holds one encoded
    list per record, ``b''`` for a record that draws nothing.

    ``palette_name`` is the donor model's TPL name, which every UV header
    must carry: a model with no embedded TEX section resolves its textures
    through this string alone, so an empty one crashes the game on the first
    frame the submesh is actually drawn."""
    uv_channels = [
        UVChannel(
            channel_index=uv.channel_index,
            palette_name=palette_name,
            texture_index=0,
            wrap_s=0,
            wrap_t=0,
            uv_data=uv.uv_data,
            uv_faces_data=uv.uv_faces_data,
            comp_count=_CUSTOM_SUBMESH_UV_FORMAT[0],
            quantize_info=_CUSTOM_SUBMESH_UV_FORMAT[1],
        )
        for uv in sorted(cs.uv_channels, key=lambda channel: channel.channel_index)
    ]
    color_channel = None
    if cs.color_data:
        color_channel = ColorChannel(
            channel_index=0,
            color_data=cs.color_data,
            color_faces_data=cs.color_faces_data or b'',
            comp_count=_CUSTOM_SUBMESH_COLOR_FORMATS[cs.color_quantize_info],
            quantize_info=cs.color_quantize_info,
        )
    normal_buffer = None
    if cs.normal_data:
        normal_buffer = NormalBuffer(
            comp_count=_CUSTOM_SUBMESH_NORMAL_FORMAT[0],
            quantize_info=_CUSTOM_SUBMESH_NORMAL_FORMAT[1],
            ambient_pct=0.0,
            normal_data=cs.normal_data,
        )
    return _assemble_rigid_submesh(
        submesh_index, cs.mesh_name, records, primitive_lists,
        vertex_data=cs.vertex_data,
        vertex_quantize_info=cs.vertex_quantize_info,
        uv_channels=uv_channels,
        color_channel=color_channel,
        normal_buffer=normal_buffer,
        faces_count=cs.faces_count,
        faces_data=cs.faces_data,
    )


def _build_custom_submesh(
    model: dict, cs: 'CustomSubmesh', rigid_surfaces: dict, submesh_index: int,
    texture_index_by_file_name: dict[str, int] | None = None,
) -> 'Submesh':
    """Resolve one CustomSubmesh's template, geometry and texture binding
    into a Submesh dataclass ready for _build_rigid_submesh_blob.

    ``texture_index_by_file_name`` maps an ``AdditionalTextureFileName`` to
    its final TEX index (The caller builds
    the TEX plan first, then this patches the cloned Type-1 state with that
    final index -- there is no placeholder to re-patch later)."""
    records, drawing_index, kind = _resolve_custom_submesh_records(model, cs, rigid_surfaces)
    descriptors = _custom_submesh_descriptors(cs, records, drawing_index, kind)
    texture_index = cs.texture_assignment.donor_texture_index
    if texture_index is None:
        file_name = cs.texture_assignment.additional_texture_file_name
        mapping = texture_index_by_file_name or {}
        if file_name not in mapping:
            raise ValueError(
                f"custom submesh '{cs.custom_submesh_id}': no resolved TEX index "
                f"for AdditionalTextureFileName {file_name!r}; the TEX plan must "
                'be built before the GPL append'
            )
        texture_index = mapping[file_name]
    _custom_submesh_patch_layer0_texture(records, drawing_index, texture_index)
    if cs.specular_strength is not None and not _custom_submesh_patch_specular_strength(
        records, drawing_index, cs.specular_strength,
    ):
        _slogger.info(
            f"custom submesh '{cs.custom_submesh_id}': template {cs.template_source} "
            'has no specular Type-7 shader; SpecularStrength '
            f'{cs.specular_strength} ignored',
            source='hammerspace.main',
        )
    faces = _custom_submesh_faces(cs, descriptors)

    # Every Add-material surface is an
    # appended canonical group, built exactly as for a rebuilt donor submesh,
    # with Type 3 generated from the primary surface's attribute layout and
    # index widths so all surfaces index the same shared arrays.
    label = f"custom submesh '{cs.custom_submesh_id}'"
    attribute_sizes = {descriptor['key']: descriptor['index_size'] for descriptor in descriptors}
    drawing_index_by_surface = {0: drawing_index}
    for position, surface in enumerate(cs.additional_surfaces, start=1):
        group = _rigid_rebuild_new_surface_group(
            model, label, surface, rigid_surfaces, attribute_sizes,
            len(cs.uv_channels), texture_index_by_file_name,
        )
        records.extend(group)
        drawing_index_by_surface[position] = len(records) - 1
        _slogger.info(
            f"{label}: appended surface '{surface.surface_key}' ('{surface.material_name}', "
            f'template {surface.template_source}) as record {len(records) - 1}',
            source='hammerspace.main',
        )
    surface_choice = cs.face_surface_indices or (0,) * cs.faces_count
    faces_by_record: dict[int, list] = {}
    for face, choice in zip(faces, surface_choice):
        faces_by_record.setdefault(drawing_index_by_surface[choice], []).append(face)

    primitive_lists = []
    for record_index in range(len(records)):
        record_faces = faces_by_record.get(record_index)
        if not record_faces:
            primitive_lists.append(b'')
            continue
        if record_index == drawing_index:
            layout = descriptors
        else:
            layout = _custom_submesh_type3_descriptors(_custom_submesh_active_type3(records, record_index))
        drawn = {descriptor['key'] for descriptor in layout}
        restricted = [[{key: vertex.get(key, 0) for key in drawn} for vertex in face] for face in record_faces]
        raw = drawlist.encodeDrawList(restricted, layout, strips=True) + b'\x00'
        primitive_lists.append(raw + b'\x00' * ((-len(raw)) % 32))
    if cs.additional_surfaces:
        key_by_record = {drawing_index: cs.template_source}
        key_by_record.update({
            drawing_index_by_surface[position]: surface.surface_key
            for position, surface in enumerate(cs.additional_surfaces, start=1)
        })
        _slogger.info(
            f'{label}: faces per surface ' + ', '.join(
                f'{key_by_record[index]}: {len(faces_by_record.get(index, []))}'
                for index in sorted(key_by_record)
            ),
            source='hammerspace.main',
        )
    return _custom_submesh_to_submesh(
        cs, submesh_index, records, primitive_lists, _donor_palette_name(model),
    )


def _serialize_submesh_blob(
    mesh_name: str, states: list[tuple], *, position_data: bytes, position_count: int, position_quantize: int,
    position_comp_count: int, uv_channels: list, color_channel: 'ColorChannel | None',
    normal_header: tuple | None, normal_data: bytes = b'', aligned: bool = False, position_reserve: int = 0,
) -> tuple[bytes, int]:
    """One submesh as a self-contained GPL blob: the layout struct and its headers, the mesh and
    palette names, the position, colour, UV and normal arrays, the display-state records and the
    primitive lists (each on a 32-byte boundary and padded to one). Returns ``(blob, name_off)``;
    every offset in the blob counts from its start, which is where the descriptor points.

    ``states``: ``(state id, pad bytes, shader mode, primitive list bytes)`` per display state.
    ``normal_header``: ``(count, quantize, comp count, ambient)``, or None for no lighting header;
    with no ``normal_data`` the normals are the second half of each position record.
    ``aligned`` is the skinned layout: positions on a 32-byte boundary with at least
    ``position_reserve`` bytes (the SKN write-back range), and each later array on a 4-byte one.
    Without it the arrays follow the names directly, each padded to four bytes (rigid submeshes)."""
    def align(offset: int, alignment: int) -> int:
        return (offset + alignment - 1) & ~(alignment - 1)

    pos_hdr, col_hdr, uv_hdr = 0x18, 0x20, 0x28
    nor_hdr = uv_hdr + len(uv_channels) * 0x10
    dsp_hdr = nor_hdr + 0x0c
    cursor = dsp_hdr + 0x0c
    payloads: list[tuple[int, bytes]] = []

    def place(data: bytes, reserve: int = 0) -> int:
        nonlocal cursor
        offset = cursor
        payloads.append((offset, data))
        cursor += max(len(data), reserve)
        return offset

    name_off = place(mesh_name.encode('ascii', errors='replace') + b'\x00')
    palette_offs = [place((uv.palette_name or '').encode('ascii', errors='replace') + b'\x00') for uv in uv_channels]
    if aligned:
        cursor = align(cursor, 32)
        pos_off = place(position_data, position_reserve)
    else:
        pos_off = place(_align4(position_data))
    col_off = 0
    if color_channel is not None:
        if aligned:
            cursor = align(cursor, 4)
        col_off = place(_align4(color_channel.color_data))
    uv_offs = []
    for uv in uv_channels:
        if aligned:
            cursor = align(cursor, 4)
        uv_offs.append(place(_align4(uv.uv_data)))
    if normal_data:
        nor_off = place(_align4(normal_data))
    else:
        nor_off = pos_off + 3 * _vb_comp_size(position_quantize)
    if aligned:
        cursor = align(cursor, 4)
    states_off = cursor
    cursor += len(states) * 0x10
    list_offs, list_sizes = [], []
    for _state_id, _pad, _mode, primitive_bytes in states:
        if primitive_bytes:
            cursor = align(cursor, 32)
            padded = primitive_bytes + b'\x00' * ((-len(primitive_bytes)) % 32)
            list_offs.append(place(padded))
            list_sizes.append(len(padded))
        else:
            list_offs.append(0)
            list_sizes.append(0)

    blob = bytearray(cursor)
    for offset, data in payloads:
        blob[offset:offset + len(data)] = data
    struct.pack_into('>5IB', blob, 0, pos_hdr, col_hdr, uv_hdr, nor_hdr if normal_header is not None else 0,
                     dsp_hdr, len(uv_channels))
    struct.pack_into('>IHBB', blob, pos_hdr, pos_off, position_count, position_quantize, position_comp_count)
    if color_channel is not None:
        struct.pack_into('>IHBB', blob, col_hdr, col_off,
                         len(color_channel.color_data) // _color_entry_size(color_channel.quantize_info),
                         color_channel.quantize_info, color_channel.comp_count)
    for j, uv in enumerate(uv_channels):
        uv_stride = _vb_comp_size(uv.quantize_info) * uv.comp_count
        struct.pack_into('>IHBBII', blob, uv_hdr + j * 0x10, uv_offs[j],
                         len(uv.uv_data) // uv_stride if uv_stride else 0, uv.quantize_info, uv.comp_count,
                         palette_offs[j], 0)
    if normal_header is not None:
        normal_count, normal_quantize, normal_comp_count, ambient = normal_header
        struct.pack_into('>IHBBf', blob, nor_hdr, nor_off, normal_count, normal_quantize, normal_comp_count, ambient)
    struct.pack_into('>IIH', blob, dsp_hdr, next((offset for offset in list_offs if offset), 0), states_off,
                     len(states))
    for k, (state_id, pad, mode, _primitive_bytes) in enumerate(states):
        at = states_off + k * 0x10
        blob[at] = state_id
        blob[at + 0x01:at + 0x04] = pad[:3].ljust(3, b'\x00')        # renderer parameters, not padding
        blob[at + 0x04:at + 0x08] = _shader_mode_bytes(mode)
        struct.pack_into('>II', blob, at + 0x08, list_offs[k], list_sizes[k])
    return bytes(blob), name_off


def _build_rigid_submesh_blob(sub: 'Submesh') -> tuple[bytes, int]:
    """Serialize one brand-new rigid (CompCount 3) submesh as a self-contained
    GPL blob, ready to be appended to an existing GPL section.

    No skinning (custom submeshes never carry SK1/SK2/SKAcc) and no donor
    layout to keep (a new submesh has no donor bytes). Returns (blob_bytes, name_off), where name_off
    is relative to the start of blob_bytes -- which is also the DOLayout's
    own start, matching the GEO descriptor's DOLayoutPtr convention.
    """
    position_stride = _vb_comp_size(sub.vertex_quantize_info) * sub.vertex_comp_count
    normals = sub.normal_buffer if sub.normal_buffer and sub.normal_buffer.normal_data else None
    normal_header = None
    if normals is not None:
        normal_stride = _vb_comp_size(normals.quantize_info) * normals.comp_count
        normal_header = (len(normals.normal_data) // normal_stride if normal_stride else 0,
                         normals.quantize_info, normals.comp_count, normals.ambient_pct)
    return _serialize_submesh_blob(
        sub.mesh_name,
        [(ds.display_state_id, ds.display_state_pad_bytes, ds.shader_mode, ds.prim_list_data)
         for ds in sub.draw_states],
        position_data=sub.vertex_data,
        position_count=len(sub.vertex_data) // position_stride if position_stride else 0,
        position_quantize=sub.vertex_quantize_info,
        position_comp_count=sub.vertex_comp_count,
        uv_channels=sub.uv_channels,
        color_channel=sub.color_channels[0] if sub.color_channels else None,
        normal_header=normal_header,
        normal_data=normals.normal_data if normals is not None else b'',
    )


def _submesh_position_half_extents(sub) -> tuple[float, float, float]:
    """Largest absolute X/Y/Z of a submesh's (bone-local) position array."""
    comp = _vb_comp_size(sub.vertex_quantize_info)
    count = len(sub.vertex_data) // (comp * 3)
    code = 'f' if comp == 4 else 'h'
    values = struct.unpack(f'>{count * 3}{code}', sub.vertex_data[:count * 3 * comp])
    divisor = 1 << (sub.vertex_quantize_info & 0xF)
    return tuple(
        max((abs(v) for v in values[axis::3]), default=0.0) / divisor
        for axis in range(3)
    )


def _submesh_position_extents(sub) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """``(min xyz, max xyz)`` of a submesh's (bone-local) position array."""
    comp = _vb_comp_size(sub.vertex_quantize_info)
    count = len(sub.vertex_data) // (comp * 3)
    code = 'f' if comp == 4 else 'h'
    values = struct.unpack(f'>{count * 3}{code}', sub.vertex_data[:count * 3 * comp])
    divisor = 1 if comp == 4 else 1 << (sub.vertex_quantize_info & 0xF)
    return (
        tuple(min(values[axis::3], default=0.0) / divisor for axis in range(3)),
        tuple(max(values[axis::3], default=0.0) / divisor for axis in range(3)),
    )


def _update_gpl_bounds_boxes(gpl_bytes: bytes, rebuilt_submeshes) -> tuple[bytes, list[int]]:
    """Rewrite the culling box of every rebuilt submesh in a stadium's
    GPLUserData bounds table (see ``_gpl_user_data_bounds_descriptor``).

    The game culls mesh i with box i in bone-local space, and every vanilla
    box is its mesh's exact ``min xyz, max xyz`` (checked on Yoshi Park and
    Wario City, 2026-11-26). A rebuilt mesh that grew, moved on its bone or
    changed bones would otherwise keep the donor's box and vanish at some
    camera angles. Returns the section and the submesh indices whose box
    was written (none where the model has no reachable table)."""
    _magic, user_data_len, user_data_ptr, count, _desc_ptr = struct.unpack_from('>5I', gpl_bytes, 0)
    if not user_data_ptr or not user_data_len or user_data_ptr + user_data_len > len(gpl_bytes):
        return gpl_bytes, []
    offset = _gpl_user_data_bounds_descriptor(gpl_bytes[user_data_ptr:user_data_ptr + user_data_len], count)
    if offset is None:
        return gpl_bytes, []
    out = bytearray(gpl_bytes)
    written = []
    for sub in rebuilt_submeshes:
        if not 0 <= sub.submesh_index < count:
            continue
        low, high = _submesh_position_extents(sub)
        struct.pack_into('>6f', out, user_data_ptr + offset + 8 + 24 * sub.submesh_index, *low, *high)
        written.append(sub.submesh_index)
    return bytes(out), written


# Stadium Type 6 values: 00000374 draws a texture's transparent texels opaque
# (black), 00000570 cuts them out (Yoshi Park, Dolphin, 2026-10-01). Character
# surfaces pair 00000374 with cut-out textures too, so this is stadium-only.
_STADIUM_OPAQUE_TYPE6 = '00000374'


def _custom_submesh_png_name(model: dict, cs) -> str | None:
    """File name of the PNG a custom submesh's layer 0 binds."""
    assignment = cs.texture_assignment
    if assignment.additional_texture_file_name:
        return assignment.additional_texture_file_name
    for descriptor in model.get('TextureDescriptors') or []:
        if descriptor.get('TextureIndex') == assignment.donor_texture_index:
            return descriptor.get('TextureFileName')
    return None


def _warn_stadium_custom_submesh_alpha(model: dict, parsed, sluggie_path) -> None:
    """Warn when a stadium custom submesh binds a PNG with transparent pixels
    but its template draws with the opaque Type 6, which shows them black."""
    if model.get('ChunkNumber') not in STADIUM_CHUNKS or sluggie_path is None:
        return
    import texture_helper as _tex
    from PIL import Image

    rigid_surfaces = _custom_submesh_rigid_surfaces(model)
    for cs in parsed.custom_submeshes:
        records, drawing_index, _kind = _resolve_custom_submesh_records(model, cs, rigid_surfaces)
        type6 = next(
            (mode for state_id, _pad, mode in reversed(records[:drawing_index + 1]) if state_id == 6),
            None,
        )
        name = _custom_submesh_png_name(model, cs)
        if type6 != _STADIUM_OPAQUE_TYPE6 or not name:
            continue
        try:
            with Image.open(_tex.resolve_texture_path(sluggie_path, name)) as image:
                low, _high = image.convert('RGBA').getchannel('A').getextrema()
        except (OSError, ValueError):
            continue  # the texture plan reports unreadable PNGs
        if low < 255:
            _slogger.warning(
                f"custom submesh '{cs.custom_submesh_id}': {name} has transparent "
                f'pixels, but template {cs.template_source} draws with Type 6 '
                f'{_STADIUM_OPAQUE_TYPE6}, which shows them opaque black in a '
                'stadium. Use a template whose Type 6 is 00000570 (a cut-out '
                'stadium surface, e.g. rigid:sm1_ds42 in Yoshi Park day).',
                source='hammerspace.main',
            )


def _gpl_user_data_bounds_descriptor(user_data: bytes, donor_count: int) -> int | None:
    """Offset of the per-mesh bounds descriptor the game reads, or None.

    GPLUserData is a chain of descriptors ``u32 length, u16 kind, u16 count,
    payload``. The game (``fn_804FF654``) walks it from the start by
    ``length`` alone, with no alignment padding, until the stated size is
    used up; kind 1 is the per-mesh culling-box table (box i -> mesh i,
    ``6 x f32`` each). Walking the same way finds exactly the table the game
    uses: sta04/sta04c pad their kind-0 length so the walk misses their
    kind-1 table, and the game never culls there, so it must stay unreached.
    Character user data has no kind-1 descriptor with the mesh count.
    """
    offset = 0
    while offset + 8 <= len(user_data):
        length, kind, count = struct.unpack_from('>IHH', user_data, offset)
        if length < 8 or offset + length > len(user_data):
            return None
        if kind == 1:
            if count == donor_count and length == 8 + 24 * count:
                return offset
            return None
        offset += length
    return None


def _extend_gpl_bounds_table(user_data: bytes, donor_count: int, new_submeshes) -> bytes | None:
    """Append one box per new submesh to a stadium's GPLUserData bounds table.

    The table is the kind-1 descriptor of the user-data chain (see
    ``_gpl_user_data_bounds_descriptor``): first in sta03/sta03b,
    sta05/sta05b, sta08b, sta09/sta09c, after a kind-0 descriptor in
    sta02b, sta04b, sta06/sta06b and sta07. The game culls each mesh with
    its box in bone-local space (vanilla boxes equal the bone-local vertex
    extents) and reads box i for mesh i without a count check, so a new
    mesh without a box gets garbage bounds. A new submesh gets ``-h .. +h``
    from its largest absolute bone-local coordinate per axis, which always
    encloses it. Every other descriptor stays byte-identical.

    Returns the extended user data, or None when no reachable bounds table
    with *donor_count* boxes exists (characters, box-less stadiums); those
    are left alone.
    """
    offset = _gpl_user_data_bounds_descriptor(user_data, donor_count)
    if offset is None:
        return None
    length = 8 + 24 * donor_count
    boxes = bytearray()
    for sub in new_submeshes:
        hx, hy, hz = _submesh_position_half_extents(sub)
        boxes += struct.pack('>6f', -hx, -hy, -hz, hx, hy, hz)
    table = bytearray(user_data[offset:offset + length]) + boxes
    struct.pack_into('>IHH', table, 0, len(table), 1, donor_count + len(new_submeshes))
    return user_data[:offset] + bytes(table) + user_data[offset + length:]


def PatchGPLAppendSubmesh(
    gpl_bytes: bytes, model: dict, parsed: 'SluggieParsed',
    texture_index_by_file_name: dict[str, int] | None = None,
    donor_gpl_length: int | None = None,
) -> bytes:
    """Append every parsed.custom_submeshes entry to a cloned/patched GPL
    section.

    ``texture_index_by_file_name`` resolves each custom submesh's
    ``AdditionalTextureFileName`` to its final TEX index; the caller builds
    the TEX plan before calling this.

    Unlike PatchGPLUVRebuild's pure tail-append, the GEO descriptor table
    must grow in place (Dolphin only proved that shape -- the table
    immediately following the header -- works at runtime), so every existing
    blob is relocated as a single unit and each existing descriptor's
    DOLayoutPtr/namePtr (the only GPL-section-absolute pointers referencing
    it) is adjusted by the resulting shift. Nothing *inside* any existing
    blob changes: every pointer there is DOLayout-relative (self-relative to
    the blob), and SKN's cache-line write targets are offsets into submesh
    0's own position array, not absolute GPL addresses, so relocating
    blobs as a unit doesn't affect SKN either (confirmed by an
    animated-skinning test in Dolphin). New blobs are inserted right after the existing
    ones and before GPLUserData.

    ``donor_gpl_length`` is the cloned donor GPL's size. Anything past it is
    a payload PatchGPLUVRebuild appended (normal/UV/color arrays, primitive
    lists) and is addressed DOLayout-relative from the existing blobs, so it
    must keep its distance to them: the whole span from the first blob to the
    GPL end moves as one unit, with GPLUserData's old spot left as a zeroed
    gap, and GPLUserData is re-emitted after the new blobs. (Moving the tail
    with GPLUserData instead shifted it by the new blobs' size, leaving every
    appended pointer dangling -- user report 2026-09-25, Goomba.) Without
    appended data the output is unchanged.
    """

    if not parsed.custom_submeshes:
        return gpl_bytes

    def _align32_residue(offset: int, residue: int) -> int:
        """Round *offset* up to the next value congruent to *residue* mod 32."""
        return offset + ((residue - offset) & 31)

    rigid_surfaces = _custom_submesh_rigid_surfaces(model)
    donor_count = len(model.get('Submeshes') or [])
    new_submeshes = [
        _build_custom_submesh(
            model, cs, rigid_surfaces, donor_count + index, texture_index_by_file_name,
        )
        for index, cs in enumerate(parsed.custom_submeshes)
    ]

    patched = bytearray(gpl_bytes)
    magic, user_data_len, user_data_ptr, old_count, desc_ptr = struct.unpack_from('>5I', patched, 0x00)
    old_descriptors = [
        struct.unpack_from('>II', patched, desc_ptr + i * 8)
        for i in range(old_count)
    ]
    old_blob_region_start = (
        min(pointer for entry in old_descriptors for pointer in entry)
        if old_descriptors else _align32(desc_ptr + old_count * 8)
    )
    tail_start = len(patched) if donor_gpl_length is None else donor_gpl_length
    insertion_point = user_data_ptr if user_data_ptr else tail_start
    if not (desc_ptr <= old_blob_region_start <= insertion_point <= tail_start <= len(patched)):
        raise ValueError('GPL section layout is not in the shape PatchGPLAppendSubmesh expects')

    pre_table            = bytes(patched[:desc_ptr])
    blobs_before_userdata = bytes(patched[old_blob_region_start:insertion_point])
    from_userdata_onward  = bytes(patched[insertion_point:tail_start])
    appended_tail         = bytes(patched[tail_start:])
    if appended_tail:
        # Keep blob -> tail distances intact (see docstring).
        blobs_before_userdata += b'\x00' * len(from_userdata_onward) + appended_tail

    new_count = old_count + len(new_submeshes)

    # A stadium-style per-submesh bounds table grows with the submesh count.
    new_user_data_len = user_data_len
    if user_data_ptr and user_data_len and len(from_userdata_onward) >= user_data_len:
        extended = _extend_gpl_bounds_table(
            from_userdata_onward[:user_data_len], old_count, new_submeshes,
        )
        if extended is not None:
            from_userdata_onward = extended + from_userdata_onward[user_data_len:]
            new_user_data_len = len(extended)
            _slogger.info(
                f'[GPL] extended the GPLUserData bounds table from {old_count} to '
                f'{new_count} submesh box(es)',
                source='hammerspace.main',
            )

    out = bytearray()
    out += pre_table
    for dolayout_ptr, name_ptr in old_descriptors:
        # Relocated below once new_blob_region_start is known (out is still
        # the old table's length here); placeholder-free since old entries'
        # final shift only depends on where the (already-sized) table ends.
        out += struct.pack('>II', dolayout_ptr, name_ptr)
    out += b'\x00' * (len(new_submeshes) * 8)  # reserved for new descriptors
    # The blob region doesn't generally start on a 32-byte boundary itself
    # (vanilla data packs it directly after the descriptor table); what does
    # need to hold is that a skinned submesh 0's own *absolute* position-array
    # alignment survives the relocation. The donor's blob-relative position
    # offset has the residue that fits the blob's GPL-relative base. Shifting every
    # existing blob's base by a multiple of 32 preserves that residue
    # relationship untouched; any other shift would silently misalign it.
    padded_len = _align32_residue(len(out), old_blob_region_start % 32)
    out += b'\x00' * (padded_len - len(out))

    new_blob_region_start = len(out)
    blob_shift = new_blob_region_start - old_blob_region_start
    assert blob_shift % 32 == 0, 'blob relocation must preserve mod-32 residue'
    for i, (dolayout_ptr, name_ptr) in enumerate(old_descriptors):
        struct.pack_into('>II', out, desc_ptr + i * 8, dolayout_ptr + blob_shift, name_ptr + blob_shift)

    out += blobs_before_userdata

    new_descriptor_entries = []
    for sub in new_submeshes:
        out += b'\x00' * ((-len(out)) % 32)
        blob_gpl_off = len(out)
        blob_bytes, name_off = _build_rigid_submesh_blob(sub)
        out += blob_bytes
        new_descriptor_entries.append((blob_gpl_off, blob_gpl_off + name_off))

    out += b'\x00' * ((-len(out)) % 32)
    new_user_data_off = len(out) if user_data_ptr else 0
    out += from_userdata_onward

    new_table_slot_start = desc_ptr + old_count * 8
    for i, (dolayout_ptr, name_ptr) in enumerate(new_descriptor_entries):
        struct.pack_into('>II', out, new_table_slot_start + i * 8, dolayout_ptr, name_ptr)

    struct.pack_into('>I', out, 0x0c, new_count)
    if user_data_ptr:
        struct.pack_into('>I', out, 0x04, new_user_data_len)
        struct.pack_into('>I', out, 0x08, new_user_data_off)

    for cs, sub in zip(parsed.custom_submeshes, new_submeshes):
        _slogger.info(
            f"[GPL] appended custom submesh '{cs.custom_submesh_id}' as submesh "
            f'{sub.submesh_index} (host bone {cs.host_bone_id}, template '
            f'{cs.template_source})',
            source='hammerspace.main',
        )

    return bytes(out)


# ---------------------------------------------------------------------------
# RigidRebuild: replace a donor rigid submesh's blob
# ---------------------------------------------------------------------------
# A donor rigid (CompCount 3) submesh whose edit the slot-preserving
# in-place patchers cannot represent -- changed topology, an Object Mode
# transform, a host-bone change that keeps the world position, or faces moved
# between surfaces -- is rebuilt from ``Submeshes[i].RigidRebuild`` with the
# custom-submesh serializer. The new blob goes before GPLUserData and
# descriptor i is repointed; the old blob is dropped when it leads the blob
# region and everything after it can shift as one unit (_drop_donor_blobs),
# otherwise it stays in place, unreferenced (shown harmless in Dolphin,
# 2026-09-26). The display states are the donor's own,
# with ShaderModeEdited / DisplayStateParamBytesEdited / texture reassignments
# applied: only the primitive lists are re-encoded (one list of triangle
# strips plus a GX_TRIANGLES block per drawing surface), plus the Type-3
# index widths where a count needs it. New
# surfaces are canonical ``T1 L0, [T1 L1], T4, T3, T6, T7`` groups appended
# after the last donor record, so no donor state index moves.

def _rigid_rebuild_face_vertices(rebuild: 'RigidRebuild') -> list[list[dict]]:
    """Per-triangle GX vertex dicts from the rebuild's per-loop index buffers
    (loop ``3 * face + corner`` of each buffer belongs to that face corner,
    as in the donor export)."""
    def _indices(raw: bytes | None):
        return struct.unpack(f'>{len(raw) // 2}H', raw) if raw else None

    positions = _indices(rebuild.faces_data)
    normals = _indices(rebuild.normal_faces_data)
    colors = _indices(rebuild.color_faces_data)
    uvs = {
        f'texture{uv.channel_index}': _indices(uv.uv_faces_data) for uv in rebuild.uv_channels
    }
    faces = []
    for face in range(rebuild.faces_count):
        triangle = []
        for corner in range(3):
            flat = face * 3 + corner
            vertex = {'position': positions[flat]}
            if normals is not None:
                vertex['lighting'] = normals[flat]
            if colors is not None:
                vertex['color0'] = colors[flat]
            for key, indices in uvs.items():
                if indices is not None:
                    vertex[key] = indices[flat]
            triangle.append(vertex)
        faces.append(triangle)
    return faces


def _rigid_rebuild_donor_records(sub: dict) -> list[list]:
    """The donor display states as mutable ``[state_id, pad_bytes, mode]``
    records, with ShaderModeEdited / DisplayStateParamBytesEdited (and the
    Type-1 rebinding apply_desired_texture_assignments wrote) applied."""
    return [
        [
            int(state['DisplayStateId']),
            bytes.fromhex(state.get('DisplayStateParamBytesEdited') or state.get('DisplayStateParamBytes', '000000')),
            state.get('ShaderModeEdited') or state.get('ShaderMode', ''),
        ]
        for state in sub.get('DisplayStates') or []
    ]


def _rigid_rebuild_entry_counts(sub: dict, rebuild: 'RigidRebuild', color: 'ColorChannel | None') -> dict[str, int]:
    """Entries per vertex attribute the rebuild carries, keyed like Type-3
    descriptors (``position``, ``lighting``, ``color0``, ``texture<n>``)."""
    counts = {'position': len(rebuild.vertex_data) // (3 * _vb_comp_size(_rebuild_position_quantize(sub, rebuild)))}
    if rebuild.normal_data is not None:
        donor_normals = sub['NormalBuffer']
        stride = int(donor_normals.get('NormalBufferCompCount', 3)) * _vb_comp_size(int(donor_normals['NormalBufferQuantizeInfo']))
        counts['lighting'] = len(rebuild.normal_data) // stride
    if color is not None:
        counts['color0'] = len(color.color_data) // _color_entry_size(color.quantize_info)
    donor_uv_by_index = {int(uv['UVChannelIndex']): uv for uv in sub.get('UVChannels') or []}
    for uv in rebuild.uv_channels:
        donor_uv = donor_uv_by_index[uv.channel_index]
        stride = int(donor_uv.get('UVChannelCompCount', 2)) * _vb_comp_size(int(donor_uv['UVChannelQuantizeInfo']))
        counts[f'texture{uv.channel_index}'] = len(uv.uv_data) // stride
    return counts


def _rigid_rebuild_widen_type3(sub_index: int, records: list[list], counts: dict[str, int]) -> dict[str, int]:
    """Widen every donor Type-3 index field whose attribute now has more
    entries than its width addresses (u8 to u16 only where counts need
    it), in place. Returns the final index size per attribute key. A Type 3
    that draws an attribute the rebuild does not carry is an error."""
    final_sizes = {key: _custom_submesh_index_size_for(count) for key, count in counts.items()}
    for record in records:
        if record[0] != 3:
            continue
        descriptors = _custom_submesh_type3_descriptors(int(record[2], 16))
        missing = [d['key'] for d in descriptors if d['key'] not in counts]
        if missing:
            raise ValueError(
                f"sub{sub_index}: donor Type-3 {record[2]} draws {', '.join(missing)}, which the "
                'RigidRebuild does not carry'
            )
        upgraded = {
            d['key'] for d in descriptors
            if d['index_size'] == 1 and _custom_submesh_index_size_for(counts[d['key']]) == 2
        }
        if upgraded:
            widened = drawlist.patchType3Setting(int(record[2], 16), upgraded)
            _slogger.info(
                f'[RigidRebuild] sub{sub_index}: Type-3 {record[2]} -> {widened:08x}, '
                f"{', '.join(sorted(upgraded))} widened to uint16 indices",
                source='hammerspace.main',
            )
            record[2] = f'{widened:08x}'
        for descriptor in descriptors:
            final_sizes[descriptor['key']] = max(final_sizes[descriptor['key']], descriptor['index_size'])
    return final_sizes


def _rigid_rebuild_new_surface_group(
    model: dict, label: str, surface: 'RigidRebuildNewSurface', rigid_surfaces: dict,
    attribute_sizes: dict[str, int], host_uv_count: int,
    texture_index_by_file_name: dict[str, int] | None,
) -> list[list]:
    """The canonical record group appended for one new surface: Type
    1/6/7 from the template's effective state, Type 4 from the layers the
    group binds, Type 3 generated from the host submesh's attribute set and
    index widths (*attribute_sizes*, ``key -> index size``), layer 0 rebound
    to the resolved texture, specular strength applied when given. The last
    record is the drawing Type 7. Shared by rebuilt donor submeshes (*label*
    ``sub<N>``) and custom submeshes with additional surfaces."""
    kind, _sep, argument = surface.template_source.partition(':')
    if kind == 'rigid':
        states, surface_index = rigid_surfaces[argument]
        records = _custom_submesh_derived_records(states, states[surface_index]['SurfaceId'])
    elif kind == 'derived':
        submesh0 = (model.get('Submeshes') or [None])[0] or {}
        records = _custom_submesh_derived_records(submesh0.get('DisplayStates') or [], argument)
    elif kind == 'builtin':
        records = _custom_submesh_builtin_records(model, argument)
    else:
        raise ValueError(f"{label}: new surface '{surface.surface_key}' has malformed TemplateSource {surface.template_source!r}")

    # Bind no more layers than the host submesh has UV channels (and index
    # widths for), then let Type 4 follow the T1 records actually emitted.
    usable_layers = min(
        host_uv_count,
        sum(1 for key in attribute_sizes if key.startswith('texture')),
    )
    records = [
        record for record in records
        if record[0] != 1 or _custom_submesh_texture_layer(record[2])[0] < usable_layers
    ]
    layer_count = sum(1 for record in records if record[0] == 1)
    if layer_count == 0:
        raise ValueError(f"{label}: new surface '{surface.surface_key}' binds no texture layer")
    layout = [{'key': 'position', 'index_size': attribute_sizes['position']}]
    for key in ('lighting', 'color0'):
        if key in attribute_sizes:
            layout.append({'key': key, 'index_size': attribute_sizes[key]})
    for layer in range(layer_count):
        layout.append({'key': f'texture{layer}', 'index_size': attribute_sizes[f'texture{layer}']})
    for record in records:
        if record[0] == 4:
            record[2] = _CUSTOM_SUBMESH_TYPE4_BY_UV_COUNT[layer_count]
        elif record[0] == 3:
            record[2] = f'{_custom_submesh_type3_setting(layout):08x}'
    drawing_index = len(records) - 1
    if records[drawing_index][0] != 7:
        raise ValueError(f"{label}: new surface '{surface.surface_key}' template does not end in a Type-7 record")

    texture_index = surface.texture_assignment.donor_texture_index
    if texture_index is None:
        file_name = surface.texture_assignment.additional_texture_file_name
        mapping = texture_index_by_file_name or {}
        if file_name not in mapping:
            raise ValueError(
                f"{label}: new surface '{surface.surface_key}': no resolved TEX index for "
                f'AdditionalTextureFileName {file_name!r}; the TEX plan must be built before the GPL'
            )
        texture_index = mapping[file_name]
    _custom_submesh_patch_layer0_texture(records, drawing_index, texture_index)
    if surface.specular_strength is not None and not _custom_submesh_patch_specular_strength(
        records, drawing_index, surface.specular_strength,
    ):
        _slogger.info(
            f"{label}: new surface '{surface.surface_key}' template "
            f'{surface.template_source} has no specular Type-7 shader; SpecularStrength ignored',
            source='hammerspace.main',
        )
    return records


def _gpl_blob_color_array(gpl_bytes: bytes, submesh_index: int) -> tuple[int, int, bytes] | None:
    """``(quantize_info, comp_count, payload)`` of a GPL blob's colour array,
    or None when the blob has none. Rigid Mii submeshes whose Type 3 has no
    ``color0`` still point at a one-entry RGB565 array that the export does
    not list as a ColorChannel; the rebuild carries it over unchanged."""
    _magic, _len, _ptr, count, desc_ptr = struct.unpack_from('>5I', gpl_bytes, 0)
    if submesh_index >= count:
        return None
    start, _name_ptr = struct.unpack_from('>II', gpl_bytes, desc_ptr + submesh_index * 8)
    color_header = struct.unpack_from('>I', gpl_bytes, start + 0x04)[0]
    if not color_header:
        return None
    pointer, entries, quantize_info, comp_count = struct.unpack_from('>IHBB', gpl_bytes, start + color_header)
    if not pointer or not entries:
        return None
    payload = gpl_bytes[start + pointer:start + pointer + entries * _color_entry_size(quantize_info)]
    return quantize_info, comp_count, payload


def _rigid_rebuild_to_submesh(
    model: dict, sub_index: int, rebuild: 'RigidRebuild', rigid_surfaces: dict,
    texture_index_by_file_name: dict[str, int] | None = None,
    donor_color_array: tuple[int, int, bytes] | None = None,
) -> 'Submesh':
    """Assemble a rebuilt donor rigid
    submesh -- donor formats, donor states, one re-encoded primitive list
    (strips plus a GX_TRIANGLES block) per drawing surface, appended groups
    for new surfaces -- into a Submesh
    ready for _build_rigid_submesh_blob.

    *donor_color_array* is the donor blob's colour array for a submesh whose
    export lists no ColorChannel (see _gpl_blob_color_array)."""
    sub = model['Submeshes'][sub_index]
    states = sub.get('DisplayStates') or []
    label = f'sub{sub_index}'

    color_channel = None
    if rebuild.color_data is not None:
        donor_color = sub['ColorChannels'][0]
        color_channel = ColorChannel(
            channel_index=int(donor_color.get('ColorChannelIndex', 0)),
            color_data=rebuild.color_data,
            color_faces_data=rebuild.color_faces_data or b'',
            comp_count=int(donor_color['ColorChannelCompCount']),
            quantize_info=int(donor_color['ColorChannelQuantizeInfo']),
        )
    elif donor_color_array is not None:
        quantize_info, comp_count, payload = donor_color_array
        color_channel = ColorChannel(
            channel_index=0, color_data=payload, color_faces_data=b'',
            comp_count=comp_count, quantize_info=quantize_info,
        )
    normal_buffer = None
    if rebuild.normal_data is not None:
        donor_normals = sub['NormalBuffer']
        normal_buffer = NormalBuffer(
            comp_count=int(donor_normals.get('NormalBufferCompCount', 3)),
            quantize_info=int(donor_normals['NormalBufferQuantizeInfo']),
            ambient_pct=float(donor_normals.get('NormalAmbientPct', 0.0)),
            normal_data=rebuild.normal_data,
        )
    donor_uv_by_index = {int(uv['UVChannelIndex']): uv for uv in sub.get('UVChannels') or []}
    uv_channels = []
    for uv in sorted(rebuild.uv_channels, key=lambda channel: channel.channel_index):
        donor_uv = donor_uv_by_index[uv.channel_index]
        uv_channels.append(UVChannel(
            channel_index=uv.channel_index,
            palette_name=donor_uv.get('PaletteName', ''),
            texture_index=int(donor_uv.get('TextureIndex', 0)),
            wrap_s=int(donor_uv.get('WrapS', 0)),
            wrap_t=int(donor_uv.get('WrapT', 0)),
            uv_data=uv.uv_data,
            uv_faces_data=uv.uv_faces_data,
            comp_count=int(donor_uv.get('UVChannelCompCount', 2)),
            quantize_info=int(donor_uv['UVChannelQuantizeInfo']),
        ))

    # Donor records, with index widths widened where the new counts need it.
    records = _rigid_rebuild_donor_records(sub)
    counts = _rigid_rebuild_entry_counts(sub, rebuild, color_channel if rebuild.color_data is not None else None)
    attribute_sizes = _rigid_rebuild_widen_type3(sub_index, records, counts)

    # Surface key -> drawing record index: donor surfaces by SurfaceId, new
    # surfaces by their appended group's Type 7.
    drawing_index_by_key = {
        state['SurfaceId']: index for index, state in enumerate(states) if state.get('SurfaceId')
    }
    for surface in rebuild.new_surfaces:
        group = _rigid_rebuild_new_surface_group(
            model, label, surface, rigid_surfaces, attribute_sizes,
            len(rebuild.uv_channels), texture_index_by_file_name,
        )
        records.extend(group)
        drawing_index_by_key[surface.surface_key] = len(records) - 1
        _slogger.info(
            f"[RigidRebuild] {label}: appended new surface '{surface.surface_key}' "
            f"('{surface.material_name}', template {surface.template_source}) as record {len(records) - 1}",
            source='hammerspace.main',
        )

    # Route every triangle to its surface's record and encode one list per
    # drawing record; an emptied donor surface keeps its record with no list.
    faces = _rigid_rebuild_face_vertices(rebuild)
    faces_by_record: dict[int, list] = {}
    for face, table_index in zip(faces, rebuild.face_surface_indices):
        faces_by_record.setdefault(drawing_index_by_key[rebuild.face_surface_table[table_index]], []).append(face)
    primitive_lists = []
    for record_index, record in enumerate(records):
        record_faces = faces_by_record.get(record_index)
        if not record_faces:
            primitive_lists.append(b'')
            continue
        setting = _custom_submesh_active_type3(records, record_index)
        if setting is None:
            raise ValueError(f'{label}: record {record_index} draws faces but has no active Type-3 layout')
        descriptors = _custom_submesh_type3_descriptors(setting)
        drawn = {descriptor['key'] for descriptor in descriptors}
        undrawn = sorted(set(record_faces[0][0]) - drawn)
        if undrawn:
            _slogger.warning(
                f"[RigidRebuild] {label}: record {record_index} draws without {', '.join(undrawn)} "
                '(its Type-3 layout lacks the attribute), so those values are not used there',
                source='hammerspace.main',
            )
        restricted = [[{key: vertex.get(key, 0) for key in drawn} for vertex in face] for face in record_faces]
        raw = drawlist.encodeDrawList(restricted, descriptors, strips=True) + b'\x00'
        primitive_lists.append(raw + b'\x00' * ((-len(raw)) % 32))

    donor_faces = {index: int(state.get('FaceCount') or 0) for index, state in enumerate(states)}
    key_by_record = {value: key for key, value in drawing_index_by_key.items()}
    summary = ', '.join(
        f"{key_by_record.get(index, f'record {index}')}: "
        f'{donor_faces.get(index, 0)} -> {len(faces_by_record.get(index, []))} faces'
        for index in sorted(set(faces_by_record) | {k for k, v in donor_faces.items() if v})
    )
    _slogger.info(
        f"[RigidRebuild] {label} '{sub.get('MeshName', '')}': {counts['position']} vertices, "
        f"{rebuild.faces_count} faces, host bone {rebuild.host_bone_id}"
        + (f" ({', '.join(rebuild.reasons)})" if rebuild.reasons else '') + f'; {summary}'
        + _widened_position_note(sub, rebuild),
        source='hammerspace.main',
    )
    return _assemble_rigid_submesh(
        sub_index, sub.get('MeshName', ''), records, primitive_lists,
        vertex_data=rebuild.vertex_data,
        vertex_quantize_info=_rebuild_position_quantize(sub, rebuild),
        uv_channels=uv_channels,
        color_channel=color_channel,
        normal_buffer=normal_buffer,
        faces_count=rebuild.faces_count,
        faces_data=rebuild.faces_data,
    )


def _drop_donor_blobs(
    gpl_bytes: bytes, replaced: list[int], insertion_point: int,
) -> tuple[bytes, int, dict[int, int], dict[int, str]]:
    """Remove the donor blobs of the *replaced* submeshes from the front of
    the blob region, so a rebuilt block does not carry them unreferenced.

    A blob's span runs from its DOLayout to the next blob's (or to
    GPLUserData). A blob is dropped only when every blob below it is dropped
    too, so that everything that stays (the remaining blobs, the string
    table after the last blob, a PatchGPLUVRebuild tail) moves as one unit.
    Pointers inside a blob are unsigned and blob-relative, so a remaining
    blob can only address bytes above its own DOLayout, never the dropped
    span; the one GPL-absolute pointer that can reach back, a descriptor's
    name pointer, is checked. The cut is rounded down to a multiple of 32 so
    the remaining blobs keep their mod-32 residue (skinned positions and
    primitive lists stay aligned); the few leftover bytes stay as a zeroed
    gap.

    Returns ``(gpl, cut, dropped, kept)``: the trimmed section, the number of
    bytes removed (every GPL-absolute offset at or past the dropped span
    moves by ``-cut``), ``{submesh: span bytes}`` for the dropped blobs and
    ``{submesh: reason}`` for the ones kept."""
    _magic, _user_data_len, user_data_ptr, count, desc_ptr = struct.unpack_from('>5I', gpl_bytes, 0)
    descriptors = [struct.unpack_from('>II', gpl_bytes, desc_ptr + i * 8) for i in range(count)]
    by_start = sorted(range(count), key=lambda i: descriptors[i][0])
    replaced_set = set(replaced)
    dropped: dict[int, int] = {}
    kept: dict[int, str] = {}
    region_start = descriptors[by_start[0]][0] if by_start else insertion_point
    region_end = region_start
    for rank, index in enumerate(by_start):
        if index not in replaced_set:
            break
        next_start = descriptors[by_start[rank + 1]][0] if rank + 1 < count else insertion_point
        if next_start < region_end or next_start > insertion_point:
            kept[index] = 'donor blob span is out of order'
            break
        dropped[index] = next_start - descriptors[index][0]
        region_end = next_start
    for index in replaced_set - set(dropped) - set(kept):
        kept[index] = 'a donor blob that stays lies below it'
    if not dropped:
        return gpl_bytes, 0, dropped, kept

    offenders = [i for i in range(count) if i not in dropped and descriptors[i][1] < region_end]
    if offenders:
        reason = f'submesh {offenders[0]} keeps its name inside the dropped span'
        return gpl_bytes, 0, {}, {i: reason for i in replaced_set}

    cut = (region_end - region_start) & ~31
    if not cut:
        return gpl_bytes, 0, {}, {i: 'donor blob span is under 32 bytes' for i in replaced_set}
    out = bytearray(gpl_bytes[:region_start])
    out += b'\x00' * (region_end - region_start - cut)
    out += gpl_bytes[region_end:]
    for i, (layout_ptr, name_ptr) in enumerate(descriptors):
        if i in dropped:
            continue
        struct.pack_into(
            '>II', out, desc_ptr + i * 8,
            layout_ptr - cut if layout_ptr >= region_end else layout_ptr,
            name_ptr - cut if name_ptr >= region_end else name_ptr,
        )
    if user_data_ptr:
        struct.pack_into('>I', out, 0x08, user_data_ptr - cut)
    return bytes(out), cut, dropped, kept


def _splice_blobs_before_user_data(
    gpl_bytes: bytes, blobs: list[tuple[int, bytes, int]], donor_gpl_length: int | None,
) -> tuple[bytes, int, dict[int, int]]:
    """Insert *blobs* (``(submesh_index, blob_bytes, name_off)``) before
    GPLUserData, each on a 32-byte boundary, and repoint their descriptors.
    The replaced submeshes' donor blobs are dropped where
    :func:`_drop_donor_blobs` allows it (the lowest blobs of the section)
    and otherwise stay in place unreferenced. Nothing else moves: every
    pointer inside a blob is blob-relative. A payload PatchGPLUVRebuild
    appended past *donor_gpl_length* keeps its distance to the blobs that
    address it (GPLUserData's old spot is left as a zeroed gap, as in
    PatchGPLAppendSubmesh). Returns ``(gpl, length, dropped)`` where
    ``length`` is the whole new section, which has no appended tail any
    more, and ``dropped`` maps each dropped submesh to its donor span size."""
    _magic, _user_data_len, user_data_ptr, count, desc_ptr = struct.unpack_from('>5I', gpl_bytes, 0)
    tail_start = len(gpl_bytes) if donor_gpl_length is None else donor_gpl_length
    insertion_point = user_data_ptr if user_data_ptr else tail_start
    if not (desc_ptr <= insertion_point <= tail_start <= len(gpl_bytes)):
        raise ValueError('GPL section layout is not in the shape the rigid rebuild expects')
    gpl_bytes, cut, dropped, kept = _drop_donor_blobs(
        gpl_bytes, [index for index, _blob, _name in blobs], insertion_point,
    )
    for index, reason in sorted(kept.items()):
        _slogger.info(
            f'[GPL] submesh {index}: the donor blob stays in place unreferenced ({reason})',
            source='hammerspace.main',
        )
    tail_start -= cut
    insertion_point -= cut
    user_data_ptr = struct.unpack_from('>I', gpl_bytes, 0x08)[0]
    user_data_region = bytes(gpl_bytes[insertion_point:tail_start])
    appended_tail = bytes(gpl_bytes[tail_start:])
    out = bytearray(gpl_bytes[:insertion_point])
    if appended_tail:
        out += b'\x00' * len(user_data_region) + appended_tail
    for submesh_index, blob, name_off in blobs:
        if submesh_index >= count:
            raise ValueError(f'submesh {submesh_index} is outside the GPL descriptor table ({count} entries)')
        out += b'\x00' * ((-len(out)) % 32)
        blob_offset = len(out)
        out += blob
        struct.pack_into('>II', out, desc_ptr + submesh_index * 8, blob_offset, blob_offset + name_off)
    out += b'\x00' * ((-len(out)) % 32)
    if user_data_ptr:
        struct.pack_into('>I', out, 0x08, len(out))
    out += user_data_region
    return bytes(out), len(out), dropped


def PatchGPLReplaceRigidSubmeshes(
    gpl_bytes: bytes, model: dict, parsed: 'SluggieParsed',
    texture_index_by_file_name: dict[str, int] | None = None,
    donor_gpl_length: int | None = None,
) -> tuple[bytes, int]:
    """Rebuild every donor submesh
    that carries a RigidRebuild as a fresh blob before GPLUserData and point
    its descriptor at it; the old blob is dropped when it leads the blob
    region (_drop_donor_blobs), else it stays unreferenced. In a stadium
    with a culling-box table the rebuilt meshes' boxes follow their new
    extents (_update_gpl_bounds_boxes). Returns the new
    section and its length (the value to hand PatchGPLAppendSubmesh as
    ``donor_gpl_length``, since any UV-rebuild tail is folded in)."""
    rebuilt = [sub for sub in parsed.mesh.submeshes if sub.rigid_rebuild is not None]
    if not rebuilt:
        return gpl_bytes, (len(gpl_bytes) if donor_gpl_length is None else donor_gpl_length)
    rigid_surfaces = _custom_submesh_rigid_surfaces(model)
    blobs = []
    new_submeshes = []
    for sub in rebuilt:
        donor_color = None
        if sub.rigid_rebuild.color_data is None:
            donor_color = _gpl_blob_color_array(gpl_bytes, sub.submesh_index)
            if donor_color is not None:
                _slogger.info(
                    f'[RigidRebuild] sub{sub.submesh_index}: carrying the donor colour array '
                    f'({len(donor_color[2]) // _color_entry_size(donor_color[0])} entries, '
                    'not listed in the export) into the rebuilt blob',
                    source='hammerspace.main',
                )
        new_sub = _rigid_rebuild_to_submesh(
            model, sub.submesh_index, sub.rigid_rebuild, rigid_surfaces,
            texture_index_by_file_name, donor_color,
        )
        blob, name_off = _build_rigid_submesh_blob(new_sub)
        blobs.append((sub.submesh_index, blob, name_off))
        new_submeshes.append(new_sub)
    out, length, dropped = _splice_blobs_before_user_data(gpl_bytes, blobs, donor_gpl_length)
    out, boxed = _update_gpl_bounds_boxes(out, new_submeshes)
    if boxed:
        _slogger.info(
            f"[GPL] updated the GPLUserData culling box of rebuilt submesh(es) {', '.join(map(str, boxed))}",
            source='hammerspace.main',
        )
    for submesh_index, blob, _name_off in blobs:
        _slogger.info(
            f'[GPL] replaced submesh {submesh_index} with a {len(blob):,}-byte rebuilt blob'
            + (f'; dropped its {dropped[submesh_index]:,}-byte donor blob' if submesh_index in dropped else ''),
            source='hammerspace.main',
        )
    return out, length
