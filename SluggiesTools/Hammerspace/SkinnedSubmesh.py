"""The rebuilt skinned body (``SkinnedRebuild``) and the facial-pose section.

``PatchGPLReplaceSkinnedSubmesh`` replaces submesh 0's blob and returns the SKN structures for its new
position buffer; the layout itself comes from ``SkinnedRebuild.py``. The facial rebuilders give a
rebuilt or edited submesh its ptr7 objects for the new vertex order (``FacialPoseRebuild.py``).
"""

import struct

import slogger as _slogger
import drawlist
from binfmt import color_entry_size as _color_entry_size, comp_size as _vb_comp_size, decode_field as _decode
import SkinnedRebuild as _skinned
import FacialPoseRebuild as _facial
import facial_pose_edits as _facial_edits

from SluggieModel import (
    UVChannel,
    ColorChannel,
    SkinnedRebuild,
    SK1,
    SK2,
    SKAcc,
    SkinningData,
    SluggieParsed,
    _CUSTOM_SUBMESH_NORMAL_FORMAT,
    _SKINNED_REBUILD_COMP_COUNT,
    _SKINNED_REBUILD_SUBMESH,
)
from SubmeshTemplates import _custom_submesh_type3_descriptors, _custom_submesh_active_type3
from HammerspaceValidate import (
    _custom_submesh_rigid_surfaces,
    _rebuild_position_quantize,
    _follow_position_shift,
    _widened_position_note,
    _facial_pose_submeshes,
)
from RigidSubmesh import (
    _bindable_color_channel,
    _serialize_submesh_blob,
    _rigid_rebuild_donor_records,
    _rigid_rebuild_widen_type3,
    _rigid_rebuild_new_surface_group,
    _gpl_blob_color_array,
    _splice_blobs_before_user_data,
)


# ---------------------------------------------------------------------------
# SkinnedRebuild: submesh 0 rebuilt from arbitrary geometry and weights
# ---------------------------------------------------------------------------
# The skinned body is rebuilt the way the external tool's build_model.py
# does it, but as a fresh blob spliced before GPLUserData (like a
# RigidRebuild) and with the SKN rebuilt from the canonical layout
# (SkinnedRebuild.layout_skin): every vertex has one direct SK1/SK2 write,
# entries sit on their own cache lines, sources mirror the position buffer,
# memClr is 0/0. Facial poses (ptr7) that address submesh 0 are rebuilt for
# the new vertex order from the rebuild's FacialPoses, or
# dropped when the rebuild carries none.

def _rebuild_primitive_lists(label: str, records: list[list], faces_by_record: dict[int, list]) -> list[bytes]:
    """One encoded, 32-byte-padded primitive list (greedy triangle strips
    plus a GX_TRIANGLES block for the leftovers, as vanilla) per
    display-state record (``b''`` for a record that draws nothing), each
    restricted to the attributes its active Type-3 layout draws."""
    primitive_lists = []
    for record_index, _record in enumerate(records):
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
                f"{label}: record {record_index} draws without {', '.join(undrawn)} "
                '(its Type-3 layout lacks the attribute), so those values are not used there',
                source='hammerspace.main',
            )
        restricted = [[{key: vertex.get(key, 0) for key in drawn} for vertex in face] for face in record_faces]
        raw = drawlist.encodeDrawList(restricted, descriptors, strips=True) + b'\x00'
        primitive_lists.append(raw + b'\x00' * ((-len(raw)) % 32))
    return primitive_lists


def _build_skinned_submesh_blob(
    mesh_name: str, records: list[list], primitive_lists: list[bytes], *,
    position_buffer: bytes, slot_count: int, write_back_end: int, vertex_quantize_info: int,
    uv_channels: list, color_channel: 'ColorChannel | None', normal_header: tuple | None,
) -> tuple[bytes, int]:
    """Serialize a rebuilt skinned (CompCount 6) submesh as a self-contained
    GPL blob, the external tool's ``build_body`` layout as a fresh blob:
    headers first, then the position buffer on a 32-byte boundary, a gap up
    to the aligned SK1/SK2 write-back end, the colour and UV arrays, the
    display-state records and the 32-byte-aligned primitive lists. The
    normal header points at ``position + 6`` with ``slot_count`` entries
    (interleaved normals); *normal_header* is ``(quantize_info, comp_count,
    ambient)`` from the donor, or None for a donor without one. Returns
    ``(blob, name_off)`` like ``_build_rigid_submesh_blob``."""
    if len(primitive_lists) != len(records):
        raise ValueError(f'{len(primitive_lists)} primitive lists for {len(records)} display-state records')
    return _serialize_submesh_blob(
        mesh_name,
        [(state_id, pad, mode, primitive_bytes)
         for (state_id, pad, mode), primitive_bytes in zip(records, primitive_lists)],
        position_data=position_buffer,
        position_count=slot_count,
        position_quantize=vertex_quantize_info,
        position_comp_count=_SKINNED_REBUILD_COMP_COUNT,
        uv_channels=uv_channels,
        color_channel=color_channel,
        normal_header=(slot_count, *normal_header) if normal_header is not None else None,
        aligned=True,
        position_reserve=write_back_end,
    )


def _skinned_rebuild_skinning_data(
    layout: '_skinned.SkinLayout', position_buffer: bytes, quantize_info: int,
) -> SkinningData:
    """The SKN structures of a canonical layout, ready for
    ``BuildSKNSkinningData``: SK1/SK2 on their cache-line destinations
    (sources mirror the position buffer), SKAcc supplements by bone, memClr
    0/0, the flush array one index per SKAcc-touched line."""
    stride = layout.stride
    sk1s, sk2s = [], []
    for entry in layout.direct:
        source = _skinned.entry_source(layout, entry, position_buffer)
        if entry.kind == 'SK1':
            sk1s.append(SK1(
                bone_index=entry.bones[0],
                vertex_cnt=entry.vertex_count,
                vertex_offset=entry.vertex_offset,
                bind_pose_data=source,
                vertex_arr_absolute_ptr=0,
                gpl_vertex_arr_value=entry.dest,
            ))
        else:
            sk2s.append(SK2(
                bone_index1=entry.bones[0],
                bone_index2=entry.bones[1],
                vertex_cnt=entry.vertex_count,
                vertex_offset=entry.vertex_offset,
                bind_pose_data=source,
                weight_data=bytes(unit for pair in entry.weights for unit in pair),
                vertex_arr_absolute_ptr=0,
                weight_arr_absolute_ptr=0,
                gpl_vertex_arr_value=entry.dest,
            ))
    sk_accs = [
        SKAcc(
            bone_index=entry.bone,
            vertex_cnt=len(entry.members),
            bind_pose_data=b''.join(
                position_buffer[slot * stride:(slot + 1) * stride] for slot, _vertex, _units in entry.members
            ),
            dest_index_data=b''.join(struct.pack('>H', slot) for slot, _vertex, _units in entry.members),
            weight_data=bytes(units for _slot, _vertex, units in entry.members),
            vertex_arr_absolute_ptr=0,
            dest_arr_absolute_ptr=0,
            gpl_dest_arr_value=0,
            weight_arr_absolute_ptr=0,
        )
        for entry in layout.accumulations
    ]
    return SkinningData(
        skn_offset=0,
        mem_clr_ptr_value=0,
        mem_clr_size=0,
        flush_ind_absolute_ptr=None,
        flush_ind_size=len(layout.flush),
        flush_ind_data=b''.join(struct.pack('>H', index) for index in layout.flush),
        quantize_info=quantize_info,
        sk1s=sk1s,
        sk2s=sk2s,
        sk_accs=sk_accs,
        preserve_source_layout=False,
    )


def _skinned_rebuild_to_blob(
    model: dict, rebuild: 'SkinnedRebuild', rigid_surfaces: dict,
    texture_index_by_file_name: dict[str, int] | None = None,
    donor_color_array: tuple[int, int, bytes] | None = None,
) -> tuple[bytes, int, SkinningData, '_skinned.SkinLayout']:
    """Lay the rebuilt body out, re-encode its draw lists and serialize the
    blob and the matching SKN structures. Returns ``(blob, name_off,
    skinning, layout)``."""
    sub_index = _SKINNED_REBUILD_SUBMESH
    sub = model['Submeshes'][sub_index]
    vb = sub['VertexBuffer']
    states = sub.get('DisplayStates') or []
    label = f'sub{sub_index}'
    donor_quantize = int(vb['VertexBufferQuantizeInfo'])
    quantize = _rebuild_position_quantize(sub, rebuild)
    stride = _SKINNED_REBUILD_COMP_COUNT * _vb_comp_size(quantize)
    vertex_count = len(rebuild.vertex_data) // stride

    # Weights -> canonical slots and SK entries.
    per_vertex: list[list] = [[] for _ in range(vertex_count)]
    for vertex, bone, weight in rebuild.influences:
        per_vertex[vertex].append((bone, weight))
    units = [_skinned.quantize_weights(influences) for influences in per_vertex]
    layout = _skinned.layout_skin(units, stride)
    position_buffer = _skinned.build_position_buffer(layout, rebuild.vertex_data)

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
        color_quantize, color_comp_count, payload = donor_color_array
        color_channel = ColorChannel(
            channel_index=0, color_data=payload, color_faces_data=b'',
            comp_count=color_comp_count, quantize_info=color_quantize,
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

    # Donor records with index widths widened where the new counts need it.
    # The lighting attribute indexes the interleaved normals, so it has one
    # entry per position slot.
    records = _rigid_rebuild_donor_records(sub)
    color_channel = _bindable_color_channel(records, color_channel)
    counts = {'position': layout.slot_count, 'lighting': layout.slot_count}
    if color_channel is not None and rebuild.color_data is not None:
        counts['color0'] = len(color_channel.color_data) // _color_entry_size(color_channel.quantize_info)
    for uv in uv_channels:
        counts[f'texture{uv.channel_index}'] = len(uv.uv_data) // (_vb_comp_size(uv.quantize_info) * uv.comp_count)
    attribute_sizes = _rigid_rebuild_widen_type3(sub_index, records, counts)

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
            f"[SkinnedRebuild] {label}: appended new surface '{surface.surface_key}' "
            f"('{surface.material_name}', template {surface.template_source}) as record {len(records) - 1}",
            source='hammerspace.main',
        )

    # Faces: position and lighting index the slot; colours and UVs their
    # per-loop pooled entries.
    positions = struct.unpack(f'>{len(rebuild.faces_data) // 2}H', rebuild.faces_data)
    colors = (
        struct.unpack(f'>{len(rebuild.color_faces_data) // 2}H', rebuild.color_faces_data)
        if rebuild.color_faces_data else None
    )
    uv_indices = {
        f'texture{uv.channel_index}': struct.unpack(f'>{len(uv.uv_faces_data) // 2}H', uv.uv_faces_data)
        for uv in rebuild.uv_channels
    }
    faces_by_record: dict[int, list] = {}
    for face, table_index in zip(range(rebuild.faces_count), rebuild.face_surface_indices):
        triangle = []
        for corner in range(3):
            flat = face * 3 + corner
            slot = layout.slot_of_vertex[positions[flat]]
            vertex = {'position': slot, 'lighting': slot}
            if colors is not None:
                vertex['color0'] = colors[flat]
            for key, indices in uv_indices.items():
                vertex[key] = indices[flat]
            triangle.append(vertex)
        record_index = drawing_index_by_key[rebuild.face_surface_table[table_index]]
        faces_by_record.setdefault(record_index, []).append(triangle)
    primitive_lists = _rebuild_primitive_lists(f'[SkinnedRebuild] {label}', records, faces_by_record)

    donor_faces = {index: int(state.get('FaceCount') or 0) for index, state in enumerate(states)}
    key_by_record = {value: key for key, value in drawing_index_by_key.items()}
    surface_summary = ', '.join(
        f"{key_by_record.get(index, f'record {index}')}: "
        f'{donor_faces.get(index, 0)} -> {len(faces_by_record.get(index, []))} faces'
        for index in sorted(set(faces_by_record) | {k for k, v in donor_faces.items() if v})
    )
    donor_slots = int(vb.get('VertexBufferLength', 0)) // stride if stride else 0
    _slogger.info(
        f"[SkinnedRebuild] {label} '{sub.get('MeshName', '')}': {vertex_count} vertices, "
        f'{rebuild.faces_count} faces (donor {donor_slots} slots, {sub.get("FacesCount")} faces)'
        + (f" ({', '.join(rebuild.reasons)})" if rebuild.reasons else '') + f'; {surface_summary}'
        + _widened_position_note(sub, rebuild),
        source='hammerspace.main',
    )
    _slogger.info(f'[SkinnedRebuild] skin: {_skinned.summary(layout)}', source='hammerspace.main')

    normal_header = None
    donor_normals = sub.get('NormalBuffer') if isinstance(sub.get('NormalBuffer'), dict) else None
    if donor_normals:
        normal_header = (
            _follow_position_shift(int(donor_normals.get('NormalBufferQuantizeInfo', donor_quantize)), donor_quantize, quantize),
            int(donor_normals.get('NormalBufferCompCount', _SKINNED_REBUILD_COMP_COUNT)),
            float(donor_normals.get('NormalAmbientPct', 0.0)),
        )
    blob, name_off = _build_skinned_submesh_blob(
        sub.get('MeshName', ''), records, primitive_lists,
        position_buffer=position_buffer,
        slot_count=layout.slot_count,
        write_back_end=layout.write_back_end,
        vertex_quantize_info=quantize,
        uv_channels=uv_channels,
        color_channel=color_channel,
        normal_header=normal_header,
    )
    skinning = _skinned_rebuild_skinning_data(
        layout, position_buffer,
        _follow_position_shift(int((model.get('SkinData') or {}).get('QuantizeInfo', donor_quantize)), donor_quantize, quantize),
    )
    return blob, name_off, skinning, layout


def _skinned_rebuild_facial_rebuilder(
    model: dict, rebuild: 'SkinnedRebuild', layout: '_skinned.SkinLayout',
):
    """The ptr7 rebuilder for a body that carries ``FacialPoses``:
    its objects are remapped onto the new position slots.
    None when the rebuild carries no poses (the caller clears the pointer)."""
    if not rebuild.facial_poses:
        return None
    position_buffer = _skinned.build_position_buffer(layout, rebuild.vertex_data)
    _slogger.info(
        f'[SkinnedRebuild] facial poses (ptr7) follow the new vertex order: {_facial.summary(rebuild.facial_poses)}',
        source='hammerspace.main',
    )
    return _facial.body_rebuilder(
        rebuild.facial_poses, layout.slot_of_vertex, position_buffer, layout.stride, _SKINNED_REBUILD_SUBMESH,
    )


def _rigid_rebuild_facial_rebuilders(model: dict, parsed: 'SluggieParsed') -> dict:
    """The ptr7 rebuilders of every rebuilt rigid submesh a facial object
    addresses: its objects are remapped onto the rebuilt position and normal
    arrays from the rebuild's ``FacialPoses``, or neutralized (entry 0, no
    movement) when the export carries none."""
    facial_submeshes = _facial_pose_submeshes(model)
    rebuilders = {}
    for sub in parsed.mesh.submeshes:
        rebuild = sub.rigid_rebuild
        index = sub.submesh_index
        if rebuild is None or index not in facial_submeshes:
            continue
        normal_stride = None
        if rebuild.normal_data is not None:
            donor_normals = model['Submeshes'][index].get('NormalBuffer') or {}
            normal_stride = int(donor_normals.get('NormalBufferCompCount', 3)) * _vb_comp_size(
                int(donor_normals.get('NormalBufferQuantizeInfo', _CUSTOM_SUBMESH_NORMAL_FORMAT[1]))
            )
        if rebuild.facial_poses is None:
            _slogger.warning(
                f'[RigidRebuild] sub{index}: facial poses (ptr7) address it by vertex index and the export '
                'carries no FacialPoses (older add-on, or the mesh lost its facial shape keys): its objects '
                'are neutralized, so this mesh has no blink or mouth animation',
                source='hammerspace.main',
            )
        else:
            _slogger.info(
                f'[RigidRebuild] sub{index}: facial poses (ptr7) follow the rebuilt arrays: '
                f'{_facial.rigid_summary(rebuild.facial_poses)}',
                source='hammerspace.main',
            )
        rebuilders[index] = _facial.rigid_rebuilder(
            rebuild.facial_poses, rebuild.vertex_data, rebuild.normal_data, normal_stride, index,
        )
    return rebuilders


def _facial_edit_rebuilders(model: dict, rebuilt: set) -> dict:
    """The ptr7 rebuilders for facial position-pose edits on submeshes that are not rebuilt (*rebuilt*: the
    submeshes with a rebuilder already): the add-on's shape-key edits (``FacialPoseDataEdited``) and pose zero
    following a moved vertex buffer, the arrays the in-place patcher writes (``facial_pose_edits``). The arrays
    keep their length, so the section is re-serialized with only those bytes changed."""
    try:
        changes = _facial_edits.changed_position_poses(model)
    except ValueError as exc:
        raise ValueError(f'facial pose edit refused: {exc}') from exc
    if not changes:
        return {}
    submesh_of = {
        int(entry['ObjectIndex']): entry.get('SubmeshIndex')
        for entry in (model.get('FacialPoseData') or {}).get('Objects') or []
    }
    edited = {submesh_of[o] for o, _p in changes if submesh_of.get(o) is not None} - rebuilt
    if not edited:
        return {}
    error = _facial.facial_data_error(model.get('FacialPoseData') or {})
    if error:
        raise ValueError(f'facial pose edits need the full FacialPoseData ({error}); re-export the model')
    _slogger.info(
        f'[FacialPoses] {len(changes)} edited position pose array(s) on submesh(es) {sorted(edited)}',
        source='hammerspace.main',
    )
    rebuild = _facial_edits.replace_position_poses(changes, _facial.POSITION_KIND, submesh_of.get)
    return {index: rebuild for index in edited}


def _rebuild_facial_section(model: dict, rebuilders: dict) -> bytes:
    """The whole ptr7 section re-serialized with the objects of every
    submesh in *rebuilders* rebuilt (the body's kind-0 SKAcc supplements
    dropped when the body is among them) and every other object unchanged."""
    facial = model.get('FacialPoseData') or {}
    use_compression = model.get('UseCompression', True)
    drop_kinds = {_facial.ACCUMULATION_KIND} if _SKINNED_REBUILD_SUBMESH in rebuilders else frozenset()
    section, warnings = _facial.rebuild_section(facial, lambda value: _decode(value, use_compression), rebuilders, drop_kinds)
    for message in warnings:
        _slogger.warning(f'[FacialPoses] {message}', source='hammerspace.main')
    donor_length = int(facial.get('SectionLength') or 0)
    _slogger.info(
        f'[FacialPoses] ptr7 section rebuilt for submesh(es) {sorted(rebuilders)}: '
        f'{donor_length:,} -> {len(section):,} bytes',
        source='hammerspace.main',
    )
    return section


def PatchGPLReplaceSkinnedSubmesh(
    gpl_bytes: bytes, model: dict, parsed: 'SluggieParsed',
    texture_index_by_file_name: dict[str, int] | None = None,
    donor_gpl_length: int | None = None,
) -> tuple[bytes, int, SkinningData | None, object]:
    """Rebuild submesh 0 from its ``SkinnedRebuild`` as a fresh blob before
    GPLUserData, repoint its descriptor and return the SKN structures that
    match the new position buffer and the ptr7 rebuilder for the body's
    facial objects (both ``None`` when there is no rebuild; the rebuilder is
    None when the rebuild carries no ``FacialPoses``). The donor blob is
    dropped when it leads the blob region (vanilla submesh 0 always does),
    else it stays in place unreferenced, as for a RigidRebuild."""
    sub = parsed.mesh.submeshes[_SKINNED_REBUILD_SUBMESH] if parsed.mesh.submeshes else None
    rebuild = sub.skinned_rebuild if sub is not None else None
    if rebuild is None:
        return gpl_bytes, (len(gpl_bytes) if donor_gpl_length is None else donor_gpl_length), None, None
    rigid_surfaces = _custom_submesh_rigid_surfaces(model)
    donor_color = None
    if rebuild.color_data is None:
        donor_color = _gpl_blob_color_array(gpl_bytes, _SKINNED_REBUILD_SUBMESH)
        if donor_color is not None:
            _slogger.info(
                f'[SkinnedRebuild] sub{_SKINNED_REBUILD_SUBMESH}: carrying the donor colour array '
                f'({len(donor_color[2]) // _color_entry_size(donor_color[0])} entries, '
                'not listed in the export) into the rebuilt blob',
                source='hammerspace.main',
            )
    blob, name_off, skinning, layout = _skinned_rebuild_to_blob(
        model, rebuild, rigid_surfaces, texture_index_by_file_name, donor_color,
    )
    out, length, dropped = _splice_blobs_before_user_data(
        gpl_bytes, [(_SKINNED_REBUILD_SUBMESH, blob, name_off)], donor_gpl_length,
    )
    _slogger.info(
        f'[GPL] replaced skinned submesh {_SKINNED_REBUILD_SUBMESH} with a {len(blob):,}-byte rebuilt blob'
        + (f'; dropped its {dropped[_SKINNED_REBUILD_SUBMESH]:,}-byte donor blob'
           if _SKINNED_REBUILD_SUBMESH in dropped else ''),
        source='hammerspace.main',
    )
    return out, length, skinning, _skinned_rebuild_facial_rebuilder(model, rebuild, layout)
