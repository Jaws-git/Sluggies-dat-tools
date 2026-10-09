"""GeometryRebuild.py — slot-preserving preprocessing passes.

Rewrites a .sluggie JSON model dict in place before ParseSluggie, for edits
that keep the donor's vertex slots (PLAN_ModelReplacements.md Milestones
2-3): complete surface reassignment (``rebuild_surface_assignments``),
UV / normal / colour edits with compaction and primitive-list rewrites
(``rebuild_edited_uvs``), and texture rebinding
(``apply_desired_texture_assignments``).

Topology changes, vertex reordering and skin membership changes no longer
pass through here: a rigid submesh is rebuilt whole (``RigidRebuild``), the
skinned body by ``SkinnedRebuild`` (``Hammerspace/SkinnedRebuild.py``), which
lays the skin out canonically. The former whole-model converter
(``rebuild_edited_geometry``, ``_rebuild_submesh``, ``_rebuild_skinning``)
and the same-count membership layout (``layout_skin_membership_edit``) were
deleted on 2026-10-09; they were never part of the build.

Verified format facts this pass relies on (see Debug/m2_*.py probes):
  * SK1/SK2 BindPoseData is byte-identical to the vertex buffer content at
    the entry's slot run — slot membership can be recovered by value-matching
    records against the edited vertex buffer.
  * Every SK1/SK2 entry writes a contiguous run: gplVertexArr + vo + k*stride.
  * lighting index == position index for interleaved (cc=6) skinned meshes.
  * FacesData order encodes the original display-state batching (faces are
    appended state by state on export), so original faces can be matched
    back to their state; new faces are routed to the first prim-list state
    bound to their FaceTextureIndices texture.
  * Flush index array ~ one entry per 32-byte cache line touched by SKAcc
    writes, excluding lines fully covered by the memClr region.  The exact
    original generator has minor variations; we emit a safe superset
    (every written slot start + geometric ceil slot for straddled lines).
"""

import os
import struct
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
from binfmt import (
    color_entry_size as _color_entry_size,
    comp_size as _comp_size,
    decode_field as _dec,
    encode_field as _enc,
    SKN_MAX_SOURCE_BYTES,
    SKN_MIN_DIRECT_VERTICES,
)
from compact_channel import compact_channel as _compact_channel
from drawlist import (computeRequiredDescriptors, decodeDrawList,
                      encodeDrawList, patchType3Setting)
from ModelFormat import (CACHE_LINE_SIZE, align_up, compute_mem_clear_range,
                         conservative_flush_indices)

import slogger as _slogger

_u16 = struct.Struct('>H')


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _u16s(data: bytes) -> list[int]:
    return list(struct.unpack(f'>{len(data)//2}H', data))


def _setting_int(shader_mode: str) -> int:
    if len(shader_mode) == 8 and all(c in '0123456789abcdefABCDEF' for c in shader_mode):
        return int(shader_mode, 16)
    return int.from_bytes(shader_mode.encode('ascii')[:4].ljust(4, b'\x00'), 'big')


def apply_desired_texture_assignments(data: dict) -> bool:
    """Patch primary Type-1 bindings requested by SurfaceId."""
    model = data['SluggiesModel']
    assignments = model.get('DesiredTextureAssignments') or {}
    if not assignments:
        return False

    texture_count = len(model.get('TextureDescriptors') or []) + len(
        model.get('AdditionalTextureDescriptors') or []
    )
    surface_lookup = {}
    submesh_info = []

    for submesh_index, submesh in enumerate(model.get('Submeshes', [])):
        display_states = submesh.get('DisplayStates', [])
        active_primary_setter = None
        setter_consumers = {}
        state_setters = {}
        for state_index, display_state in enumerate(display_states):
            surface_id = display_state.get('SurfaceId') or f'sm{submesh_index}_ds{state_index}'
            if surface_id in surface_lookup:
                raise ValueError(f"duplicate SurfaceId '{surface_id}'")
            surface_lookup[surface_id] = (submesh_index, state_index)

            if display_state.get('DisplayStateId') == 1:
                setting = _setting_int(
                    display_state.get('ShaderModeEdited') or display_state['ShaderMode']
                )
                if ((setting >> 13) & 7) == 0:
                    active_primary_setter = state_index

            if int(display_state.get('FaceCount') or 0) > 0 \
                    or int(display_state.get('PrimListLength') or 0) > 0:
                if active_primary_setter is not None:
                    state_setters[state_index] = active_primary_setter
                    setter_consumers.setdefault(active_primary_setter, []).append(surface_id)

        submesh_info.append((state_setters, setter_consumers))

    requested = {}
    for surface_id, raw_texture_index in assignments.items():
        if surface_id not in surface_lookup:
            raise ValueError(f"DesiredTextureAssignments references unknown SurfaceId '{surface_id}'")
        if isinstance(raw_texture_index, bool) or not isinstance(raw_texture_index, int):
            raise ValueError(f"DesiredTextureAssignments['{surface_id}'] must be an integer")
        if raw_texture_index < 0 or raw_texture_index >= texture_count:
            raise ValueError(
                f"DesiredTextureAssignments['{surface_id}'] texture index "
                f'{raw_texture_index} is outside rebuilt TEX count {texture_count}'
            )
        submesh_index, state_index = surface_lookup[surface_id]
        state_setters, _ = submesh_info[submesh_index]
        if state_index not in state_setters:
            raise ValueError(f"DesiredTextureAssignments SurfaceId '{surface_id}' is not drawable")
        requested[surface_id] = raw_texture_index

    setter_requests = {}
    for surface_id, texture_index in requested.items():
        submesh_index, state_index = surface_lookup[surface_id]
        setter_index = submesh_info[submesh_index][0][state_index]
        setter_requests.setdefault((submesh_index, setter_index), {})[surface_id] = texture_index

    for (submesh_index, setter_index), requests in setter_requests.items():
        consumers = submesh_info[submesh_index][1][setter_index]
        requested_indices = set(requests.values())
        if len(requested_indices) != 1 or set(requests) != set(consumers):
            requested_text = ', '.join(
                f'{surface_id}={texture_index}'
                for surface_id, texture_index in sorted(requests.items())
            )
            raise ValueError(
                f'sub{submesh_index} ds{setter_index}: shared primary texture setter '
                f'is consumed by {consumers}; all consumers must request the same '
                f'texture (requested: {requested_text})'
            )

    use_b64 = model.get('UseBase64', True)
    pending_face_textures = []
    for submesh_index, submesh in enumerate(model.get('Submeshes', [])):
        display_states = submesh.get('DisplayStates', [])
        surface_indices = []
        decoded_state_faces = None
        for state_index, display_state in enumerate(display_states):
            face_count = display_state.get('FaceCount')
            if face_count is None and display_state.get('PrimListData'):
                if decoded_state_faces is None:
                    decoded_state_faces = _decode_original_states(submesh, use_b64)[0]
                face_count = len(decoded_state_faces.get(state_index, []))
            surface_indices.extend([state_index] * int(face_count or 0))
        encoded_surface_indices = submesh.get('FaceSurfaceIdsEdited')
        if encoded_surface_indices is not None:
            surface_indices = _u16s(_dec(encoded_surface_indices, use_b64))

        raw_face_textures = submesh.get('FaceTextureIndicesEdited')
        if raw_face_textures is None:
            raw_face_textures = submesh.get('FaceTextureIndices')
        if raw_face_textures is None:
            continue
        face_textures = _u16s(_dec(raw_face_textures, use_b64))
        if len(face_textures) != len(surface_indices):
            raise ValueError(
                f'sub{submesh_index}: face texture count {len(face_textures)} does not '
                f'match surface assignment count {len(surface_indices)}'
            )
        changed = False
        for face_index, state_index in enumerate(surface_indices):
            if state_index < 0 or state_index >= len(display_states):
                raise ValueError(
                    f'sub{submesh_index} face {face_index}: surface index {state_index} '
                    'is outside the display-state table'
                )
            surface_id = display_states[state_index].get('SurfaceId') \
                or f'sm{submesh_index}_ds{state_index}'
            if surface_id in requested and face_textures[face_index] != requested[surface_id]:
                face_textures[face_index] = requested[surface_id]
                changed = True
        if changed:
            pending_face_textures.append((submesh, face_textures))

    for (submesh_index, setter_index), requests in setter_requests.items():
        display_state = model['Submeshes'][submesh_index]['DisplayStates'][setter_index]
        old_setting = _setting_int(
            display_state.get('ShaderModeEdited') or display_state['ShaderMode']
        )
        texture_index = next(iter(requests.values()))
        display_state['ShaderModeEdited'] = f'{(old_setting & ~0x1FFF) | texture_index:08x}'

    for submesh, face_textures in pending_face_textures:
        submesh['FaceTextureIndicesEdited'] = _enc(
            b''.join(_u16.pack(index) for index in face_textures),
            use_b64,
        )

    return True


# ---------------------------------------------------------------------------
# Draw list / UV / color rebuild per submesh
# ---------------------------------------------------------------------------

def _decode_original_states(sub: dict, use_b64):
    """Decode every original prim list.  Returns
    (state_faces, face_lookup, first_state_by_tex, type3_index)

    state_faces:  {ds_index: decoded faces}
    face_lookup:  {(p0,p1,p2): (ds_index, face_dict_list)}  original routing
    first_state_by_tex: {tex_index: ds_index}
    type3_index:  display-state index of the Type-3 descriptor state
    """
    state_faces = {}
    face_lookup = {}
    first_state_by_tex = {}
    type3_index = None
    cur_tex = 0
    for k, ds in enumerate(sub['DisplayStates']):
        sid = ds['DisplayStateId']
        setting = _setting_int(ds['ShaderMode'])
        if sid == 1:
            coord = (setting >> 13) & 7
            if coord == 0:
                cur_tex = setting & 0x1FFF
        elif sid == 3:
            type3_index = k
        pl = ds.get('PrimListData')
        if not pl or not ds.get('VertexStreamLayout'):
            continue
        descs = [{'key': d['key'], 'direct': False, 'index_size': d['index_size']}
                 for d in ds['VertexStreamLayout']]
        faces = decodeDrawList(_dec(pl, use_b64), descs)
        state_faces[k] = faces
        first_state_by_tex.setdefault(cur_tex, k)
        for f in faces:
            key = (f[0]['position'], f[1]['position'], f[2]['position'])
            face_lookup.setdefault(key, (k, f))
    return state_faces, face_lookup, first_state_by_tex, type3_index


def _primitive_blocks(raw: bytes, descriptors: list, what: str) -> list[tuple[bytes, list]]:
    """Return each raw GX block and its decoded faces, excluding zero padding."""
    stride = sum(descriptor['index_size'] for descriptor in descriptors)
    if not stride:
        return []
    blocks = []
    offset = 0
    while offset < len(raw) and raw[offset] != 0:
        if offset + 3 > len(raw):
            raise ValueError(f'{what}: truncated primitive header at byte {offset}')
        vertex_count = int.from_bytes(raw[offset + 1:offset + 3], 'big')
        block_end = offset + 3 + vertex_count * stride
        if block_end > len(raw):
            raise ValueError(
                f'{what}: primitive at byte {offset} needs {block_end - offset} '
                f'bytes but only {len(raw) - offset} remain')
        block = raw[offset:block_end]
        blocks.append((block, decodeDrawList(block, descriptors)))
        offset = block_end
    return blocks


def _primitive_block_face_vertices(opcode: int, vertex_count: int) -> list[tuple[int, int, int]]:
    if opcode == 0x90:
        return [
            (index + 2, index + 1, index)
            for index in range(0, vertex_count - 2, 3)
        ]
    if opcode == 0x98:
        return [
            (index, index + 1, index + 2) if index % 2 else (index + 2, index + 1, index)
            for index in range(vertex_count - 2)
        ]
    if opcode == 0x80:
        result = []
        for index in range(0, vertex_count - 3, 4):
            result.extend(((index + 2, index + 1, index), (index, index + 3, index + 2)))
        return result
    raise ValueError(f'unsupported GX primitive opcode 0x{opcode:02X}')


def _rewrite_uv_primitive_blocks(
    raw: bytes,
    descriptors: list,
    face_start: int,
    channel_edits: dict,
    what: str,
) -> tuple[bytes, int, int]:
    """Patch UV indices in raw GX blocks; triangulate only irreducible blocks."""
    stride = sum(descriptor['index_size'] for descriptor in descriptors)
    descriptor_offsets = {}
    offset = 0
    for descriptor in descriptors:
        descriptor_offsets[descriptor['key']] = (offset, descriptor['index_size'])
        offset += descriptor['index_size']

    output = bytearray()
    data_offset = 0
    face_cursor = face_start
    rebuilt_blocks = 0
    while data_offset < len(raw) and raw[data_offset] != 0:
        if data_offset + 3 > len(raw):
            raise ValueError(f'{what}: truncated primitive header at byte {data_offset}')
        opcode = raw[data_offset]
        vertex_count = int.from_bytes(raw[data_offset + 1:data_offset + 3], 'big')
        block_end = data_offset + 3 + vertex_count * stride
        if block_end > len(raw):
            raise ValueError(f'{what}: primitive block exceeds payload bounds')
        block = bytearray(raw[data_offset:block_end])
        face_vertices = _primitive_block_face_vertices(opcode, vertex_count)
        assignments = {}
        conflict = False
        for local_face, vertex_indices in enumerate(face_vertices):
            global_face = face_cursor + local_face
            for corner, raw_vertex_index in enumerate(vertex_indices):
                for key, edit in channel_edits.items():
                    if key not in descriptor_offsets:
                        continue
                    desired = edit['indices'][global_face * 3 + corner]
                    assignment_key = (raw_vertex_index, key)
                    previous = assignments.get(assignment_key)
                    if previous is not None and previous != desired:
                        conflict = True
                    assignments[assignment_key] = desired

        if not conflict:
            for (raw_vertex_index, key), desired in assignments.items():
                attribute_offset, width = descriptor_offsets[key]
                maximum = (1 << (width * 8)) - 1
                if desired > maximum:
                    raise ValueError(
                        f'{what}: UV index {desired} exceeds {width}-byte '
                        f'{key} field (max {maximum})')
                field_offset = 3 + raw_vertex_index * stride + attribute_offset
                block[field_offset:field_offset + width] = desired.to_bytes(width, 'big')
            output.extend(block)
        elif opcode == 0x80:
            pending_quads = bytearray()

            def flush_quads():
                if not pending_quads:
                    return
                count = len(pending_quads) // stride
                output.append(0x80)
                output.extend(struct.pack('>H', count))
                output.extend(pending_quads)
                pending_quads.clear()

            vertex_payload = block[3:]
            for quad_index in range(vertex_count // 4):
                quad_data = bytearray(
                    vertex_payload[
                        quad_index * 4 * stride:(quad_index + 1) * 4 * stride
                    ]
                )
                quad_mappings = ((2, 1, 0), (0, 3, 2))
                quad_assignments = {}
                quad_conflict = False
                for local_face, vertex_indices in enumerate(quad_mappings):
                    global_face = face_cursor + quad_index * 2 + local_face
                    for corner, raw_vertex_index in enumerate(vertex_indices):
                        for key, edit in channel_edits.items():
                            if key not in descriptor_offsets:
                                continue
                            desired = edit['indices'][global_face * 3 + corner]
                            assignment_key = (raw_vertex_index, key)
                            previous = quad_assignments.get(assignment_key)
                            if previous is not None and previous != desired:
                                quad_conflict = True
                            quad_assignments[assignment_key] = desired
                if not quad_conflict:
                    for (raw_vertex_index, key), desired in quad_assignments.items():
                        attribute_offset, width = descriptor_offsets[key]
                        maximum = (1 << (width * 8)) - 1
                        if desired > maximum:
                            raise ValueError(
                                f'{what}: UV index {desired} exceeds {width}-byte '
                                f'{key} field (max {maximum})')
                        field_offset = raw_vertex_index * stride + attribute_offset
                        quad_data[field_offset:field_offset + width] = desired.to_bytes(width, 'big')
                    pending_quads.extend(quad_data)
                    continue

                flush_quads()
                quad_block = b'\x80\x00\x04' + bytes(quad_data)
                faces = [
                    [dict(vertex) for vertex in face]
                    for face in decodeDrawList(quad_block, descriptors)
                ]
                for local_face, face in enumerate(faces):
                    global_face = face_cursor + quad_index * 2 + local_face
                    for corner, vertex in enumerate(face):
                        for key, edit in channel_edits.items():
                            if key in vertex:
                                vertex[key] = edit['indices'][global_face * 3 + corner]
                output.extend(encodeDrawList(faces, descriptors))
                rebuilt_blocks += 1
            flush_quads()
        else:
            faces = [
                [dict(vertex) for vertex in face]
                for face in decodeDrawList(bytes(block), descriptors)
            ]
            for local_face, face in enumerate(faces):
                global_face = face_cursor + local_face
                for corner, vertex in enumerate(face):
                    for key, edit in channel_edits.items():
                        if key in vertex:
                            vertex[key] = edit['indices'][global_face * 3 + corner]
            output.extend(encodeDrawList(faces, descriptors))
            rebuilt_blocks += 1
        face_cursor += len(face_vertices)
        data_offset = block_end

    if output:
        output.append(0)
    return bytes(output), face_cursor, rebuilt_blocks


def rebuild_surface_assignments(data: dict) -> bool:
    """Rebuild primitive lists for unchanged faces reassigned to donor surfaces."""
    model = data['SluggiesModel']
    use_b64 = model.get('UseBase64', True)
    rebuilt = False

    for sub_idx, sub in enumerate(model.get('Submeshes', [])):
        encoded_assignments = sub.get('FaceSurfaceIdsEdited')
        if encoded_assignments is None:
            continue

        raw_assignments = _dec(encoded_assignments, use_b64)
        if len(raw_assignments) % 2:
            raise ValueError(
                f'sub{sub_idx}: FaceSurfaceIdsEdited has odd byte length '
                f'{len(raw_assignments)}; expected big-endian uint16 values')
        target_states = _u16s(raw_assignments)
        state_faces, _, _, _ = _decode_original_states(sub, use_b64)
        original_faces = [
            (state_idx, face)
            for state_idx, faces in state_faces.items()
            for face in faces
        ]
        if len(target_states) != len(original_faces):
            raise ValueError(
                f'sub{sub_idx}: FaceSurfaceIdsEdited has {len(target_states)} '
                f'entries but donor primitive lists contain {len(original_faces)} faces')

        original_states = [state_idx for state_idx, _ in original_faces]
        if target_states == original_states:
            _slogger.info(
                f'[M2.4] sub{sub_idx}: surface assignments unchanged; '
                'preserving donor primitive lists',
                source='geometry.rebuild',
            )
            continue

        display_states = sub['DisplayStates']
        state_snapshots = []
        active_type7_state = None
        for state_index, state in enumerate(display_states):
            if state['DisplayStateId'] == 7:
                active_type7_state = state_index
            effective_type7_mode = (
                display_states[active_type7_state].get('ShaderModeEdited')
                or display_states[active_type7_state].get('ShaderMode', '')
                if active_type7_state is not None else None
            )
            state_snapshots.append({
                'DisplayStateId': state['DisplayStateId'],
                'DisplayStateParamBytes': state.get('DisplayStateParamBytesEdited') or state.get('DisplayStateParamBytes', '000000'),
                'ShaderMode': state.get('ShaderModeEdited') or state.get('ShaderMode', ''),
                'EffectiveType7State': active_type7_state,
                'EffectiveType7Mode': effective_type7_mode,
                'VertexStreamLayout': state.get('VertexStreamLayout', []),
            })
        texture_layers = {}
        texture_layer_states = {}
        texture_contracts = []
        texture_state_contracts = []
        for state in display_states:
            if state['DisplayStateId'] == 1:
                setting = _setting_int(state.get('ShaderModeEdited') or state['ShaderMode'])
                layer = (setting >> 13) & 7
                texture_layers[layer] = (
                    setting & 0x1FFF,
                    (setting >> 20) & 0xF,
                    (setting >> 16) & 0xF,
                )
                texture_layer_states[layer] = len(texture_contracts)
            texture_contracts.append(tuple(sorted(texture_layers.items())))
            texture_state_contracts.append(dict(texture_layer_states))

        assignment_ranges = {}
        cursor = 0
        for state_idx, faces in state_faces.items():
            assignment_ranges[state_idx] = (cursor, cursor + len(faces))
            cursor += len(faces)

        aliased_faces = 0
        for source_state, (start, end) in assignment_ranges.items():
            assigned = target_states[start:end]
            if not assigned or all(target == source_state for target in assigned):
                continue
            target_set = set(assigned)
            if len(target_set) != 1:
                raise ValueError(
                    f'sub{sub_idx} ds{source_state}: partial surface reassignment '
                    'splits a donor GX batch; only complete donor-surface moves '
                    'are supported by the current MVP')
            target_state = next(iter(target_set))
            if target_state not in state_faces:
                raise ValueError(
                    f'sub{sub_idx} ds{source_state}: target display state '
                    f'{target_state} is not an existing drawable donor surface')
            source_contract = state_snapshots[source_state]
            target_contract = state_snapshots[target_state]
            if (source_contract['EffectiveType7State'] is None
                    or target_contract['EffectiveType7State'] is None):
                raise ValueError(
                    f'sub{sub_idx}: donor surfaces {source_state} and {target_state} '
                    'must both inherit a Type-7 shader state')
            if source_contract['VertexStreamLayout'] != target_contract['VertexStreamLayout']:
                raise ValueError(
                    f'sub{sub_idx}: donor surfaces {source_state} and '
                    f'{target_state} use incompatible vertex stream layouts')
            if source_contract['EffectiveType7Mode'] != target_contract['EffectiveType7Mode']:
                raise ValueError(
                    f'sub{sub_idx}: donor surfaces {source_state} and '
                    f'{target_state} use different shader modes (effective Type-7: '
                    f'({source_contract["EffectiveType7Mode"]} and '
                    f'{target_contract["EffectiveType7Mode"]})); only identical '
                    'effective shader reassignment is supported')

            source_texture_contract = dict(texture_contracts[source_state])
            target_texture_contract = dict(texture_contracts[target_state])
            differing_layers = {
                layer for layer in source_texture_contract.keys() | target_texture_contract.keys()
                if source_texture_contract.get(layer) != target_texture_contract.get(layer)
            }
            previous_drawable = max(
                (state_idx for state_idx in state_faces if state_idx < source_state),
                default=-1,
            )
            binding_aliases = []
            for layer in sorted(differing_layers):
                source_binding_state = texture_state_contracts[source_state].get(layer)
                target_binding_state = texture_state_contracts[target_state].get(layer)
                if source_binding_state is None or target_binding_state is None:
                    raise ValueError(
                        f'sub{sub_idx}: donor surfaces {source_state} and '
                        f'{target_state} have incompatible texture layer {layer}; '
                        'the target contract cannot be represented by the source states')
                if source_binding_state <= previous_drawable:
                    raise ValueError(
                        f'sub{sub_idx}: ds{source_state} inherits texture layer {layer} '
                        f'from shared ds{source_binding_state}; changing it would also '
                        'affect an earlier donor surface')
                binding_aliases.append((source_binding_state, target_binding_state, layer))

            source_type7_state = source_contract['EffectiveType7State']
            target_type7_state = target_contract['EffectiveType7State']
            if source_type7_state != target_type7_state:
                if source_type7_state != source_state:
                    raise ValueError(
                        f'sub{sub_idx}: ds{source_state} inherits its Type-7 state '
                        f'from shared ds{source_type7_state}; changing it would also '
                        'affect an earlier donor surface')
                source_type7 = display_states[source_type7_state]
                target_type7 = state_snapshots[target_type7_state]
                source_type7['DisplayStateParamBytes'] = target_type7['DisplayStateParamBytes']
                source_type7['ShaderMode'] = target_type7['ShaderMode']
                source_type7['MaterialStateAliasedByImporter'] = True
            for source_binding_state, target_binding_state, layer in binding_aliases:
                source_binding = display_states[source_binding_state]
                target_binding = display_states[target_binding_state]
                source_binding['ShaderMode'] = (
                    target_binding.get('ShaderModeEdited')
                    or target_binding.get('ShaderMode', '')
                )
                source_binding['MaterialStateAliasedByImporter'] = True
                _slogger.info(
                    f'[M2.4] sub{sub_idx}: ds{source_binding_state} adopts '
                    f'ds{target_binding_state} texture-layer {layer} binding',
                    source='geometry.rebuild',
                )
            aliased_faces += len(assigned)
            target_states[start:end] = [source_state] * len(assigned)
            _slogger.info(
                f'[M2.4] sub{sub_idx}: ds{source_state} adopts ds{target_state} '
                f'effective material state; preserved {len(assigned)} faces in '
                'their donor GX batch',
                source='geometry.rebuild',
            )

        if target_states == original_states:
            sub['SurfaceAssignmentsRebuiltByImporter'] = True
            _slogger.info(
                f'[M2.4] sub{sub_idx}: applied complete-surface reassignment '
                f'to {aliased_faces} faces without rebuilding primitive lists',
                source='geometry.rebuild',
            )
            rebuilt = True
            continue

        for face_idx, ((source_state, _), target_state) in enumerate(zip(original_faces, target_states)):
            if target_state not in state_faces:
                raise ValueError(
                    f'sub{sub_idx} face {face_idx}: target display state '
                    f'{target_state} is not an existing drawable donor surface')
            source_layout = sub['DisplayStates'][source_state].get('VertexStreamLayout', [])
            target_layout = sub['DisplayStates'][target_state].get('VertexStreamLayout', [])
            if source_layout != target_layout:
                raise ValueError(
                    f'sub{sub_idx} face {face_idx}: donor surfaces {source_state} '
                    f'and {target_state} use incompatible vertex stream layouts')

        chunks_by_target = {state_idx: [] for state_idx in state_faces}
        changed_states = set()
        assignment_offset = 0
        preserved_blocks = 0
        rebuilt_blocks = 0
        for source_state in state_faces:
            display_state = sub['DisplayStates'][source_state]
            descriptors = [
                {'key': descriptor['key'], 'direct': False,
                 'index_size': descriptor['index_size']}
                for descriptor in display_state['VertexStreamLayout']
            ]
            original = _dec(display_state['PrimListData'], use_b64)
            for block, block_faces in _primitive_blocks(
                original, descriptors, f'sub{sub_idx} ds{source_state}'
            ):
                block_targets = target_states[
                    assignment_offset:assignment_offset + len(block_faces)
                ]
                if len(block_targets) != len(block_faces):
                    raise ValueError(
                        f'sub{sub_idx} ds{source_state}: assignment data ended '
                        'inside a primitive block')
                assignment_offset += len(block_faces)
                unique_targets = list(dict.fromkeys(block_targets))
                if len(unique_targets) == 1:
                    target_state = unique_targets[0]
                    chunks_by_target[target_state].append(block)
                    preserved_blocks += 1
                else:
                    for target_state in unique_targets:
                        selected_faces = [
                            face for face, target in zip(block_faces, block_targets)
                            if target == target_state
                        ]
                        chunks_by_target[target_state].append(
                            encodeDrawList(selected_faces, descriptors)
                        )
                    rebuilt_blocks += 1

                for target_state in unique_targets:
                    if target_state != source_state:
                        changed_states.update((source_state, target_state))

        if assignment_offset != len(target_states):
            raise ValueError(
                f'sub{sub_idx}: consumed {assignment_offset} face assignments but '
                f'{len(target_states)} were supplied')

        for state_idx in changed_states:
            raw = b''.join(chunks_by_target[state_idx])
            if raw:
                raw += b'\x00'
            sub['DisplayStates'][state_idx]['PrimListDataEdited'] = _enc(raw, use_b64)

        moved_count = sum(
            source_state != target_state
            for (source_state, _), target_state in zip(original_faces, target_states)
        )
        _slogger.info(
            f'[M2.4] sub{sub_idx}: changed {len(changed_states)} donor surfaces; '
            f'reassigned {moved_count}/{len(original_faces)} faces; preserved '
            f'{preserved_blocks} GX blocks, rebuilt {rebuilt_blocks} split blocks',
            source='geometry.rebuild',
        )
        sub['SurfaceAssignmentsRebuiltByImporter'] = True
        rebuilt = True

    return rebuilt


def _compact_shared_color_array(
    what: str,
    donor_entries: list[bytes],
    donor_indices_by_channel: dict[str, list[int]],
    edited_by_channel: dict[str, list[bytes]],
    edited_indices_by_channel: dict[str, list[int]],
    loop_count: int,
):
    """Compact the single vertex-color array shared by color0/color1.

    Unlike UV channels (one array each), both color index fields reference
    the same entry array, so compaction dedupes entries while producing an
    independent per-loop index list for every active channel.  Donor entry
    order is preserved; a donor slot whose original entry is no longer
    referenced by any loop may be repurposed for a new value (UV-style),
    and remaining new values are appended after the donor entries.

    An edited channel's loop ``n`` takes the entry
    ``edited_by_channel[ch][edited_indices_by_channel[ch][n]]`` (Blender
    exports one entry per loop with an identity index list).  Donor indices
    only say which donor slots the unedited loops still reference.

    Returns (compact_bytes, indices_by_channel, preserve_indices).
    """
    if not donor_entries:
        raise ValueError(f'{what}: donor color array is empty')
    entry_size = len(donor_entries[0])
    for channel, indices in donor_indices_by_channel.items():
        if len(indices) != loop_count:
            raise ValueError(
                f'{what}: {channel} donor index list has {len(indices)} '
                f'entries, expected {loop_count}')
        if any(index >= len(donor_entries) for index in indices):
            raise ValueError(
                f'{what}: {channel} donor index '
                f'{max(indices)} exceeds entry count {len(donor_entries)}')

    slots = list(donor_entries)
    slot_for = {}
    for index, entry in enumerate(slots):
        slot_for.setdefault(entry, index)
    required = {entry for entries in edited_by_channel.values() for entry in entries}
    reused = set()

    def assign(entry: bytes) -> int:
        index = slot_for.get(entry)
        if index is not None:
            return index
        for candidate in range(len(donor_entries)):
            if candidate in reused:
                continue
            if donor_entries[candidate] in required:
                continue
            slots[candidate] = entry
            slot_for[entry] = candidate
            reused.add(candidate)
            return candidate
        slots.append(entry)
        slot_for[entry] = len(slots) - 1
        return len(slots) - 1

    indices_by_channel = {}
    preserve = True
    for channel, edited in edited_by_channel.items():
        donor_indices = donor_indices_by_channel[channel]
        edited_indices = edited_indices_by_channel[channel]
        if len(edited_indices) != loop_count:
            raise ValueError(
                f'{what}: {channel} edited index list has {len(edited_indices)} '
                f'entries, expected {loop_count}')
        if any(index >= len(edited) for index in edited_indices):
            raise ValueError(
                f'{what}: {channel} edited index {max(edited_indices)} '
                f'exceeds edited entry count {len(edited)}')
        new_indices = []
        for loop in range(loop_count):
            entry = edited[edited_indices[loop]]
            # Keep the loop's own donor slot when it still holds this value:
            # donor arrays can repeat a value (Mario's body: ffff twice), and
            # a value lookup would move those loops to the first copy.
            donor_index = donor_indices[loop]
            if slots[donor_index] == entry:
                new_indices.append(donor_index)
                continue
            index = assign(entry)
            new_indices.append(index)
        if new_indices != list(donor_indices):
            preserve = False
        indices_by_channel[channel] = new_indices
    for channel in donor_indices_by_channel:
        if channel not in indices_by_channel:
            indices_by_channel[channel] = list(donor_indices_by_channel[channel])
    if len(slots) > 0xFFFF:
        raise ValueError(
            f'{what}: compact entry count {len(slots)} exceeds uint16')
    return b''.join(slots), indices_by_channel, preserve


def rebuild_edited_uvs(data: dict) -> bool:
    """Prepare unchanged-topology UV edits and rebuild only required draw lists."""
    model = data['SluggiesModel']
    use_b64 = model.get('UseBase64', True)
    rebuilt = False

    for sub_idx, sub in enumerate(model.get('Submeshes', [])):
        if sub.get('RigidRebuild'):
            # Rebuilt as a whole blob by HammerspaceMain (PLAN_EditRigidMeshes.md);
            # its UV, normal and colour data travel inside that entry.
            continue
        face_count = sub.get('FacesCountEdited', sub.get('FacesCount', 0))
        if sub.get('FacesCountEdited') is not None and face_count != sub.get('FacesCount'):
            raise ValueError(
                f'sub{sub_idx}: face count changed from {sub.get("FacesCount")} to '
                f'{face_count}; a changed topology is only supported on rigid '
                'submeshes through RigidRebuild (re-export from Blender), not on '
                'the skinned body'
            )
        loop_count = face_count * 3
        channel_edits = {}

        for uv in sub.get('UVChannels', []):
            encoded_data = uv.get('UVChannelDataEdited')
            encoded_faces = uv.get('UVFacesDataEdited')
            if encoded_data is None or encoded_faces is None:
                continue
            channel = uv['UVChannelIndex']
            stride = uv['UVChannelCompCount'] * _comp_size(uv['UVChannelQuantizeInfo'])
            original = _dec(uv['UVChannelData'], use_b64)
            expanded = _dec(encoded_data, use_b64)
            if expanded == original:
                uv.pop('UVChannelDataEdited', None)
                uv.pop('UVFacesDataEdited', None)
                continue
            original_indices = _u16s(_dec(uv['UVFacesData'], use_b64))
            expanded_indices = _u16s(_dec(encoded_faces, use_b64))
            compact, new_indices, preserve_indices = _compact_channel(
                f'sub{sub_idx} uv{channel}',
                stride,
                original,
                original_indices,
                expanded,
                expanded_indices,
                loop_count,
            )
            if compact == original:
                uv.pop('UVChannelDataEdited', None)
                uv.pop('UVFacesDataEdited', None)
                continue

            uv['UVChannelDataEdited'] = _enc(compact, use_b64)
            uv['UVFacesDataEdited'] = _enc(
                b''.join(_u16.pack(index) for index in new_indices), use_b64
            )
            channel_edits[f'texture{channel}'] = {
                'indices': new_indices,
                'preserve_indices': preserve_indices,
            }
            rebuilt = True
            _slogger.info(
                f'[M3.2] sub{sub_idx} uv{channel}: {len(expanded) // stride} expanded '
                f'coords -> {len(compact) // stride} compact; '
                f'indices {"preserved" if preserve_indices else "rebuilt"}',
                source='geometry.rebuild',
            )

        # Donor-identical channels are commonly diffuse/specular coordinate
        # aliases. If Blender changes only one, preserve that donor relationship
        # unless the sibling carries its own meaningful edit.
        uv_by_channel = {
            uv['UVChannelIndex']: uv for uv in sub.get('UVChannels', [])
        }
        for source_key, source_edit in list(channel_edits.items()):
            source_channel = int(source_key.split('texture', 1)[1])
            source_uv = uv_by_channel[source_channel]
            for target_channel, target_uv in uv_by_channel.items():
                if target_channel == source_channel or f'texture{target_channel}' in channel_edits:
                    continue
                if (
                    _dec(target_uv['UVChannelData'], use_b64)
                    != _dec(source_uv['UVChannelData'], use_b64)
                    or _dec(target_uv['UVFacesData'], use_b64)
                    != _dec(source_uv['UVFacesData'], use_b64)
                ):
                    continue
                target_uv['UVChannelDataEdited'] = source_uv['UVChannelDataEdited']
                target_uv['UVFacesDataEdited'] = source_uv['UVFacesDataEdited']
                channel_edits[f'texture{target_channel}'] = {
                    'indices': list(source_edit['indices']),
                    'preserve_indices': source_edit['preserve_indices'],
                }
                rebuilt = True
                _slogger.info(
                    f'[M3.2] sub{sub_idx} uv{target_channel}: mirrored uv'
                    f'{source_channel} edit because donor channels are identical',
                    source='geometry.rebuild',
                )

        # M3.4: normal-buffer compaction — same helper, same donor-order
        # preservation as UVs.  The Blender per-loop normal array is deduped
        # against the donor normal layout; changed slots flow into the
        # primitive-list rewrite as the 'lighting' index.
        normal_buffer = sub.get('NormalBuffer')
        edited_normal = (
            normal_buffer.get('NormalBufferDataEdited') if normal_buffer else None
        )
        if edited_normal is not None:
            donor_norm = _dec(normal_buffer['NormalBufferData'], use_b64)
            expanded_norm = _dec(edited_normal, use_b64)
            if expanded_norm == donor_norm:
                normal_buffer.pop('NormalBufferDataEdited', None)
            else:
                normal_stride = (
                    normal_buffer['NormalBufferCompCount']
                    * _comp_size(normal_buffer['NormalBufferQuantizeInfo'])
                )
                if not donor_norm or len(donor_norm) % normal_stride:
                    raise ValueError(
                        f'sub{sub_idx}: donor normal array size '
                        f'{len(donor_norm)} is not divisible by record '
                        f'size {normal_stride}')
                donor_normal_indices = _u16s(
                    _dec(normal_buffer['NormalFacesData'], use_b64))
                if len(donor_normal_indices) != loop_count:
                    raise ValueError(
                        f'sub{sub_idx}: donor normal index list has '
                        f'{len(donor_normal_indices)} entries, expected '
                        f'{loop_count}')
                compact_norm, normal_indices, normal_preserve = _compact_channel(
                    f'sub{sub_idx} normal',
                    normal_stride,
                    donor_norm,
                    donor_normal_indices,
                    expanded_norm,
                    list(range(loop_count)),
                    loop_count,
                )
                if compact_norm == donor_norm:
                    normal_buffer.pop('NormalBufferDataEdited', None)
                else:
                    normal_buffer['NormalBufferDataEdited'] = _enc(compact_norm, use_b64)
                    channel_edits['lighting'] = {
                        'indices': normal_indices,
                        'preserve_indices': normal_preserve,
                    }
                    _slogger.info(
                        f'[M3.3] sub{sub_idx}: normal buffer compacted to '
                        f'{len(compact_norm) // normal_stride} unique normals',
                        source='geometry.rebuild',
                    )
                rebuilt = True

        # M3.3: vertex-color compaction.  Unlike UV channels, color0/color1
        # both index ONE shared DOColorHeader entry array, so compaction is
        # per-entry with independent index lists per channel.  Donor entry
        # order is preserved exactly as for UVs; a donor slot whose original
        # entry is no longer referenced by any loop may be repurposed for a
        # new value, and remaining new values are appended after the donor
        # entries.
        color_channels = sub.get('ColorChannels') or []
        edited_color_channels = [
            cc for cc in color_channels
            if cc.get('ColorChannelDataEdited') is not None
            and cc.get('ColorFacesDataEdited') is not None
        ]
        if edited_color_channels:
            entry_size = _color_entry_size(
                color_channels[0]['ColorChannelQuantizeInfo'])
            donor_color = _dec(color_channels[0]['ColorChannelData'], use_b64)
            for cc in color_channels[1:]:
                other = _dec(cc['ColorChannelData'], use_b64)
                if len(other) != len(donor_color):
                    raise ValueError(
                        f'sub{sub_idx}: color channels report different '
                        f'donor array sizes ({len(donor_color)} vs {len(other)})')
            donor_entries = [
                donor_color[i * entry_size:(i + 1) * entry_size]
                for i in range(len(donor_color) // entry_size)
            ]
            edited_by_channel = {}
            edited_indices_by_channel = {}
            donor_indices_by_channel = {}
            for cc in color_channels:
                key = f"color{cc['ColorChannelIndex']}"
                donor_indices_by_channel[key] = _u16s(
                    _dec(cc['ColorFacesData'], use_b64))
                if cc in edited_color_channels:
                    expanded = _dec(cc['ColorChannelDataEdited'], use_b64)
                    expanded_indices = _u16s(
                        _dec(cc['ColorFacesDataEdited'], use_b64))
                    if len(expanded) % entry_size:
                        raise ValueError(
                            f'sub{sub_idx} {key}: edited length '
                            f'{len(expanded)} is not divisible by entry '
                            f'size {entry_size}')
                    edited_by_channel[key] = [
                        expanded[i * entry_size:(i + 1) * entry_size]
                        for i in range(len(expanded) // entry_size)
                    ]
                    if len(expanded_indices) != loop_count:
                        raise ValueError(
                            f'sub{sub_idx} {key}: edited index list has '
                            f'{len(expanded_indices)} entries, expected '
                            f'{loop_count}')
                    edited_indices_by_channel[key] = expanded_indices
            compact_color, indices_by_key, color_preserve = _compact_shared_color_array(
                f'sub{sub_idx} color',
                donor_entries,
                donor_indices_by_channel,
                edited_by_channel,
                edited_indices_by_channel,
                loop_count,
            )
            # Same bytes but moved indices is still an edit (a loop now shows
            # another donor color), so both must match to drop it.
            if compact_color == donor_color and color_preserve:
                for cc in edited_color_channels:
                    cc.pop('ColorChannelDataEdited', None)
                    cc.pop('ColorFacesDataEdited', None)
            else:
                compact_encoded = _enc(compact_color, use_b64)
                for cc in color_channels:
                    key = f"color{cc['ColorChannelIndex']}"
                    if key not in indices_by_key:
                        continue
                    cc['ColorChannelDataEdited'] = compact_encoded
                    cc['ColorFacesDataEdited'] = _enc(
                        b''.join(
                            _u16.pack(index) for index in indices_by_key[key]
                        ),
                        use_b64,
                    )
                    channel_edits[key] = {
                        'indices': indices_by_key[key],
                        'preserve_indices': color_preserve,
                    }
                _slogger.info(
                    f"[M3.3] sub{sub_idx} color: {len(donor_entries)} donor "
                    f'entries -> {len(compact_color) // entry_size} compact; '
                    f'indices {"preserved" if color_preserve else "rebuilt"}',
                    source='geometry.rebuild',
                )
            rebuilt = True

        if any(key.startswith('texture') for key in channel_edits):
            sub['UVArraysEditedByImporter'] = True
        if 'lighting' in channel_edits:
            sub['NormalArraysEditedByImporter'] = True
        if any(key.startswith('color') for key in channel_edits):
            sub['ColorArraysEditedByImporter'] = True
        changed_indices = {}
        for key, edit in channel_edits.items():
            if not edit['preserve_indices']:
                changed_indices[key] = edit
        if not changed_indices:
            continue

        state_faces, _, _, type3_index = _decode_original_states(sub, use_b64)
        edited_state_faces = {}
        face_cursor = 0
        for state_index, faces in state_faces.items():
            edited_faces = []
            for local_face_index, face in enumerate(faces):
                global_face = face_cursor + local_face_index
                copied_face = [dict(vertex) for vertex in face]
                for corner, vertex in enumerate(copied_face):
                    for key, edit in changed_indices.items():
                        if key in vertex:
                            vertex[key] = edit['indices'][global_face * 3 + corner]
                edited_faces.append(copied_face)
            edited_state_faces[state_index] = edited_faces
            face_cursor += len(faces)
        if face_cursor != face_count:
            raise ValueError(
                f'sub{sub_idx}: decoded draw lists contain {face_cursor} faces, '
                f'expected {face_count}')
        all_faces = [face for faces in edited_state_faces.values() for face in faces]
        descriptor_state = sub['DisplayStates'][type3_index] if type3_index is not None else None
        base_descriptors = None
        for state_index in state_faces:
            layout = sub['DisplayStates'][state_index].get('VertexStreamLayout')
            if layout:
                base_descriptors = [
                    {'key': item['key'], 'direct': False, 'index_size': item['index_size']}
                    for item in layout
                ]
                break
        if base_descriptors is None:
            raise ValueError(f'sub{sub_idx}: UV edit has no active vertex descriptors')
        new_descriptors, upgraded = computeRequiredDescriptors(all_faces, base_descriptors)
        states_to_rebuild = set(state_faces) if upgraded else set()
        face_cursor = 0
        total_rebuilt_blocks = 0
        for state_index, faces in state_faces.items():
            state = sub['DisplayStates'][state_index]
            original_raw = _dec(state['PrimListData'], use_b64)
            if upgraded:
                raw = encodeDrawList(edited_state_faces[state_index], new_descriptors)
                if raw:
                    raw += b'\x00'
                next_face = face_cursor + len(faces)
                rebuilt_blocks = 0
            else:
                raw, next_face, rebuilt_blocks = _rewrite_uv_primitive_blocks(
                    original_raw,
                    base_descriptors,
                    face_cursor,
                    changed_indices,
                    f'sub{sub_idx} ds{state_index}',
                )
            if raw != original_raw:
                states_to_rebuild.add(state_index)
                state['PrimListDataEdited'] = _enc(raw, use_b64)
            total_rebuilt_blocks += rebuilt_blocks
            face_cursor = next_face
        if face_cursor != face_count:
            raise ValueError(
                f'sub{sub_idx}: decoded draw lists contain {face_cursor} faces, '
                f'expected {face_count}')
        for state_index in states_to_rebuild:
            state = sub['DisplayStates'][state_index]
            state['VertexStreamLayout'] = [
                {'key': item['key'], 'index_size': item['index_size']}
                for item in new_descriptors
            ]
        if upgraded:
            if descriptor_state is None:
                raise ValueError(
                    f'sub{sub_idx}: UV indices require descriptor widening but '
                    'no Type-3 state exists')
            old_setting = _setting_int(descriptor_state['ShaderMode'])
            descriptor_state['ShaderModeEdited'] = f'{patchType3Setting(old_setting, upgraded):08x}'
        if any(key.startswith('texture') for key in changed_indices):
            sub['UVPrimitiveListsRebuiltByImporter'] = True
        if 'lighting' in changed_indices:
            sub['NormalPrimitiveListsRebuiltByImporter'] = True
        if any(key.startswith('color') for key in changed_indices):
            sub['ColorPrimitiveListsRebuiltByImporter'] = True
        _slogger.info(
            f'[M3.2] sub{sub_idx}: rebuilt draw states {sorted(states_to_rebuild)}; '
            f'descriptor upgrades {sorted(upgraded)}; triangulated '
            f'{total_rebuilt_blocks} irreducible GX blocks',
            source='geometry.rebuild',
        )

    return rebuilt
