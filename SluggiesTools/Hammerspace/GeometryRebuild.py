"""GeometryRebuild.py — Milestone 2 preprocessing pass.

Transforms a .sluggies JSON model dict whose submeshes carry topology edits
(changed vertex counts / faces) into builder-ready form:

  * rebuilds GX prim lists per display state  → DisplayState['PrimListDataEdited']
  * upgrades u8→u16 attribute index widths    → Type-3 DisplayState['ShaderModeEdited']
  * dedupes expanded per-loop UVs into compact arrays → UVChannelDataEdited (compact)
  * for the skinned submesh: derives a vertex permutation so every SK1/SK2
    entry writes a CONTIGUOUS slot run, rewrites VertexBufferDataEdited in
    slot order, and rewrites SkinDataEdited with correct GplVertexArrValue /
    GplDestArrValue / DestIndexData (the Blender exporter carries stale
    originals that overlap once counts change)
  * rebuilds the SKN flush index array         → SkinDataEdited['FlushIndData']

After this pass the regular ParseSluggie / BuildGPLMeshData /
BuildSKNSkinningData pipeline consumes the data unchanged.

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
from drawlist import (computeRequiredDescriptors, decodeDrawList,
                      encodeDrawList, patchType3Setting)
from ModelFormat import compute_mem_clear_range, conservative_flush_indices

import slogger as _slogger

_u16 = struct.Struct('>H')


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _dec(val, use_b64):
    if val is None:
        return None
    if use_b64:
        import base64
        return base64.b64decode(val)
    return bytes(val)


def _enc(data: bytes, use_b64):
    if use_b64:
        import base64
        return base64.b64encode(data).decode('ascii')
    return list(data)


def _comp_size(q: int) -> int:
    return 4 if (q >> 4) in (4, 7, 0xa) else 2


def _u16s(data: bytes) -> list[int]:
    return list(struct.unpack(f'>{len(data)//2}H', data))


def _setting_int(shader_mode: str) -> int:
    if len(shader_mode) == 8 and all(c in '0123456789abcdefABCDEF' for c in shader_mode):
        return int(shader_mode, 16)
    return int.from_bytes(shader_mode.encode('ascii')[:4].ljust(4, b'\x00'), 'big')


def _submesh_changed(sub: dict, use_b64) -> bool:
    fe = sub.get('FacesDataEdited')
    if fe is None:
        return False
    if _dec(fe, use_b64) != _dec(sub['FacesData'], use_b64):
        return True
    vbe = sub['VertexBuffer'].get('VertexBufferDataEdited')
    if vbe is not None and _dec(vbe, use_b64) != _dec(sub['VertexBuffer']['VertexBufferData'], use_b64):
        return True
    return False


# ---------------------------------------------------------------------------
# Skinned-vertex permutation
# ---------------------------------------------------------------------------

def _match_members(bind_pose: bytes, stride: int, value_map: dict, what: str) -> list[int]:
    """Resolve an SK entry's member vertices by value-matching bind-pose
    records against the edited vertex buffer.  Consumes entries from
    value_map (record bytes -> list of unclaimed blender vertex indices)."""
    n = len(bind_pose) // stride
    members = []
    for k in range(n):
        rec = bind_pose[k * stride:(k + 1) * stride]
        cands = value_map.get(rec)
        if not cands:
            raise ValueError(
                f'{what}: bind-pose record {k} not found in the edited vertex '
                f'buffer (or already claimed) — exporter/importer mismatch')
        members.append(cands.pop(0))
    return members


def _rebuild_skinning(model: dict, sub: dict, use_b64) -> dict | None:
    """Derive the slot permutation for the skinned submesh and rewrite
    SkinDataEdited in place.

    Returns {'perm': {blender_idx: slot}, 'n_verts': int} or None when the
    model has no skinning edits."""
    ske = model.get('SkinDataEdited')
    sk = model.get('SkinData')
    if not ske or not sk:
        return None

    vb = sub['VertexBuffer']
    q = vb['VertexBufferQuantizeInfo']
    stride = 6 * _comp_size(q)
    vb_e = _dec(vb.get('VertexBufferDataEdited') or vb['VertexBufferData'], use_b64)
    n_verts = len(vb_e) // stride

    # value → unclaimed blender indices (insertion order = vertex order)
    value_map: dict[bytes, list[int]] = {}
    for i in range(n_verts):
        value_map.setdefault(vb_e[i * stride:(i + 1) * stride], []).append(i)

    # Resolve members per entry (SK1 first, then SK2 — same order the
    # exporter used to build the bind pose payloads).
    sk1_members = []
    for e in ske.get('SK1s', []):
        bp = _dec(e.get('BindPoseDataEdited') or e['BindPoseData'], use_b64)
        sk1_members.append(_match_members(bp, stride, value_map,
                                          f"SK1 bone {e['BoneIndex']}"))
    sk2_members = []
    for e in ske.get('SK2s', []):
        bp = _dec(e.get('BindPoseDataEdited') or e['BindPoseData'], use_b64)
        sk2_members.append(_match_members(bp, stride, value_map,
                                          f"SK2 pair ({e['BoneIndex1']},{e['BoneIndex2']})"))

    # SKAcc members are matched against ALL vertices (their dest slots alias
    # SK1/SK2 slots), so use a fresh, non-consuming map.
    full_map: dict[bytes, list[int]] = {}
    for i in range(n_verts):
        full_map.setdefault(vb_e[i * stride:(i + 1) * stride], []).append(i)
    acc_members = []
    for e in ske.get('SKAccs', []):
        bp = _dec(e.get('BindPoseDataEdited') or e['BindPoseData'], use_b64)
        n = len(bp) // stride
        members = []
        for k in range(n):
            rec = bp[k * stride:(k + 1) * stride]
            cands = full_map.get(rec)
            if not cands:
                raise ValueError(
                    f"SKAcc bone {e['BoneIndex']}: bind-pose record {k} not "
                    f"found in the edited vertex buffer")
            # non-consuming: SKAcc targets a slot also claimed by SK1/SK2
            members.append(cands[0])
        acc_members.append(members)

    # Assign slots: contiguous run per SK1/SK2 entry, leftovers at the end.
    perm: dict[int, int] = {}
    cursor = 0
    sk1_starts = []
    for members in sk1_members:
        sk1_starts.append(cursor)
        for b in members:
            if b in perm:
                raise ValueError(f'vertex {b} claimed by two direct SK entries')
            perm[b] = cursor
            cursor += 1
    sk2_starts = []
    for members in sk2_members:
        sk2_starts.append(cursor)
        for b in members:
            if b in perm:
                raise ValueError(f'vertex {b} claimed by two direct SK entries')
            perm[b] = cursor
            cursor += 1
    for b in range(n_verts):          # acc-only / unskinned leftovers
        if b not in perm:
            perm[b] = cursor
            cursor += 1
    assert cursor == n_verts

    # Rewrite the vertex buffer in slot order.
    new_vb = bytearray(len(vb_e))
    for b, s in perm.items():
        new_vb[s * stride:(s + 1) * stride] = vb_e[b * stride:(b + 1) * stride]
    vb['VertexBufferDataEdited'] = _enc(bytes(new_vb), use_b64)

    # Rewrite SkinDataEdited entries: fixed gpl values, vo=0, slot-ordered payloads.
    for e, members, start in zip(ske.get('SK1s', []), sk1_members, sk1_starts):
        e['GplVertexArrValue'] = start * stride
        e['VertexOffset'] = 0
        e['BindPoseData'] = _enc(
            b''.join(bytes(new_vb[(start + k) * stride:(start + k + 1) * stride])
                     for k in range(len(members))), use_b64)
    for e, members, start in zip(ske.get('SK2s', []), sk2_members, sk2_starts):
        e['GplVertexArrValue'] = start * stride
        e['VertexOffset'] = 0
        e['BindPoseData'] = _enc(
            b''.join(bytes(new_vb[(start + k) * stride:(start + k + 1) * stride])
                     for k in range(len(members))), use_b64)
        # WeightData order already matches member order — unchanged.
    for e, members in zip(ske.get('SKAccs', []), acc_members):
        e['GplDestArrValue'] = 0
        e['DestIndexData'] = _enc(
            b''.join(_u16.pack(perm[b]) for b in members), use_b64)
        # BindPoseData order matches member/weight order — keep as exported.

    # ---- flush index array (safe superset — see module docstring) ----
    written = set()
    for e, members in zip(ske.get('SKAccs', []), acc_members):
        for b in members:
            written.add(perm[b] * stride)
    direct = set()
    for members, start in zip(sk1_members, sk1_starts):
        for k in range(len(members)):
            direct.add((start + k) * stride)
    for members, start in zip(sk2_members, sk2_starts):
        for k in range(len(members)):
            direct.add((start + k) * stride)
    only_acc = written - direct
    mcp, mcs = compute_mem_clear_range(direct, written, stride)
    flush_sorted = conservative_flush_indices(written, stride, mcp, mcs, n_verts)
    ske['FlushIndData'] = _enc(b''.join(_u16.pack(x) for x in flush_sorted), use_b64)
    ske['FlushIndSize'] = len(flush_sorted)
    ske['RebuiltByImporter'] = True

    _slogger.info(f'[M2] skinned submesh: {n_verts} verts permuted, '
          f'{len(only_acc)} acc-only slots, memClr 0x{mcp:X}/0x{mcs:X}, '
          f'flush {len(flush_sorted)} entries', source="geometry.rebuild")
    return {'perm': perm, 'n_verts': n_verts}


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
                for channel, edit in channel_edits.items():
                    key = f'texture{channel}'
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
                        for channel, edit in channel_edits.items():
                            key = f'texture{channel}'
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
                        for channel, edit in channel_edits.items():
                            key = f'texture{channel}'
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
                    for channel, edit in channel_edits.items():
                        key = f'texture{channel}'
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
        state_snapshots = [
            {
                'DisplayStateId': state['DisplayStateId'],
                'DisplayStatePadBytes': state.get('DisplayStatePadBytes', '000000'),
                'ShaderMode': state.get('ShaderModeEdited') or state.get('ShaderMode', ''),
                'VertexStreamLayout': state.get('VertexStreamLayout', []),
            }
            for state in display_states
        ]
        texture_layers = {}
        texture_contracts = []
        for state in display_states:
            if state['DisplayStateId'] == 1:
                setting = _setting_int(state.get('ShaderModeEdited') or state['ShaderMode'])
                layer = (setting >> 13) & 7
                texture_layers[layer] = (
                    setting & 0x1FFF,
                    (setting >> 20) & 0xF,
                    (setting >> 16) & 0xF,
                )
            texture_contracts.append(tuple(sorted(texture_layers.items())))

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
            if source_contract['DisplayStateId'] != 7 or target_contract['DisplayStateId'] != 7:
                raise ValueError(
                    f'sub{sub_idx}: complete reassignment requires Type-7 donor '
                    f'surfaces, got ds{source_state} type '
                    f'{source_contract["DisplayStateId"]} and ds{target_state} '
                    f'type {target_contract["DisplayStateId"]}')
            if source_contract['VertexStreamLayout'] != target_contract['VertexStreamLayout']:
                raise ValueError(
                    f'sub{sub_idx}: donor surfaces {source_state} and '
                    f'{target_state} use incompatible vertex stream layouts')
            if texture_contracts[source_state] != texture_contracts[target_state]:
                raise ValueError(
                    f'sub{sub_idx}: donor surfaces {source_state} and '
                    f'{target_state} use incompatible inherited texture bindings')

            source = display_states[source_state]
            source['DisplayStatePadBytes'] = target_contract['DisplayStatePadBytes']
            source['ShaderMode'] = target_contract['ShaderMode']
            source['MaterialStateAliasedByImporter'] = True
            aliased_faces += len(assigned)
            target_states[start:end] = [source_state] * len(assigned)
            _slogger.info(
                f'[M2.4] sub{sub_idx}: ds{source_state} adopts ds{target_state} '
                f'material state; preserved {len(assigned)} faces in their donor GX batch',
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


def rebuild_edited_uvs(data: dict) -> bool:
    """Prepare unchanged-topology UV edits and rebuild only required draw lists."""
    model = data['SluggiesModel']
    use_b64 = model.get('UseBase64', True)
    rebuilt = False

    for sub_idx, sub in enumerate(model.get('Submeshes', [])):
        face_count = sub.get('FacesCountEdited', sub.get('FacesCount', 0))
        if face_count != sub.get('FacesCount'):
            continue
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
            if len(original) % stride or len(expanded) % stride:
                raise ValueError(
                    f'sub{sub_idx} uv{channel}: payload length is not divisible '
                    f'by stride {stride}')
            if len(original_indices) != loop_count or len(expanded_indices) != loop_count:
                raise ValueError(
                    f'sub{sub_idx} uv{channel}: expected {loop_count} loop indices, '
                    f'got donor={len(original_indices)}, edited={len(expanded_indices)}')
            expanded_count = len(expanded) // stride
            donor_count = len(original) // stride
            if any(index >= expanded_count for index in expanded_indices):
                raise ValueError(
                    f'sub{sub_idx} uv{channel}: edited loop index exceeds '
                    f'coordinate count {expanded_count}')

            edited_records = [
                expanded[index * stride:(index + 1) * stride]
                for index in expanded_indices
            ]
            donor_slots = [None] * donor_count
            conflict = False
            for donor_index, record in zip(original_indices, edited_records):
                if donor_index >= donor_count:
                    raise ValueError(
                        f'sub{sub_idx} uv{channel}: donor loop index {donor_index} '
                        f'exceeds coordinate count {donor_count}')
                if donor_slots[donor_index] is None:
                    donor_slots[donor_index] = record
                elif donor_slots[donor_index] != record:
                    conflict = True
                    break

            if not conflict:
                for index, record in enumerate(donor_slots):
                    if record is None:
                        donor_slots[index] = original[index * stride:(index + 1) * stride]
                compact = b''.join(donor_slots)
                if compact == original:
                    uv.pop('UVChannelDataEdited', None)
                    uv.pop('UVFacesDataEdited', None)
                    continue
                new_indices = original_indices
                preserve_indices = True
            else:
                compact_records = [
                    original[index * stride:(index + 1) * stride]
                    for index in range(donor_count)
                ]
                split_indices = {}
                new_indices = []
                for donor_index, record in zip(original_indices, edited_records):
                    donor_record = compact_records[donor_index]
                    if record == donor_record:
                        new_indices.append(donor_index)
                        continue
                    split_key = (donor_index, record)
                    if split_key not in split_indices:
                        # Reuse the donor slot for the first edited value only when
                        # no loop still needs its original value.
                        slot_records = {
                            candidate
                            for index, candidate in zip(original_indices, edited_records)
                            if index == donor_index
                        }
                        if donor_record not in slot_records and not any(
                            key[0] == donor_index for key in split_indices
                        ):
                            compact_records[donor_index] = record
                            split_indices[split_key] = donor_index
                        else:
                            split_indices[split_key] = len(compact_records)
                            compact_records.append(record)
                    new_indices.append(split_indices[split_key])
                if len(compact_records) > 0xFFFF:
                    raise ValueError(
                        f'sub{sub_idx} uv{channel}: compact coordinate count '
                        f'{len(compact_records)} exceeds uint16')
                compact = b''.join(compact_records)
                preserve_indices = new_indices == original_indices

            uv['UVChannelDataEdited'] = _enc(compact, use_b64)
            uv['UVFacesDataEdited'] = _enc(
                b''.join(_u16.pack(index) for index in new_indices), use_b64
            )
            channel_edits[channel] = {
                'indices': new_indices,
                'preserve_indices': preserve_indices,
            }
            rebuilt = True
            _slogger.info(
                f'[M3.2] sub{sub_idx} uv{channel}: {expanded_count} expanded '
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
        for source_channel, source_edit in list(channel_edits.items()):
            source_uv = uv_by_channel[source_channel]
            for target_channel, target_uv in uv_by_channel.items():
                if target_channel == source_channel or target_channel in channel_edits:
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
                channel_edits[target_channel] = {
                    'indices': list(source_edit['indices']),
                    'preserve_indices': source_edit['preserve_indices'],
                }
                rebuilt = True
                _slogger.info(
                    f'[M3.2] sub{sub_idx} uv{target_channel}: mirrored uv'
                    f'{source_channel} edit because donor channels are identical',
                    source='geometry.rebuild',
                )

        changed_indices = {
            channel: edit for channel, edit in channel_edits.items()
            if not edit['preserve_indices']
        }
        if not changed_indices:
            if channel_edits:
                sub['UVArraysEditedByImporter'] = True
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
                    for channel, edit in changed_indices.items():
                        key = f'texture{channel}'
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
        sub['UVArraysEditedByImporter'] = True
        sub['UVPrimitiveListsRebuiltByImporter'] = True
        _slogger.info(
            f'[M3.2] sub{sub_idx}: rebuilt draw states {sorted(states_to_rebuild)}; '
            f'descriptor upgrades {sorted(upgraded)}; triangulated '
            f'{total_rebuilt_blocks} irreducible GX blocks',
            source='geometry.rebuild',
        )

    return rebuilt


def _rebuild_submesh(model: dict, sub: dict, sub_idx: int, use_b64,
                     perm_info: dict | None) -> None:
    """Rebuild prim lists, compact UVs, and descriptors for one changed submesh."""
    vb = sub['VertexBuffer']
    is_skinned = vb['VertexBufferCompCount'] == 6

    vb_q = vb['VertexBufferQuantizeInfo']
    pos_stride = vb['VertexBufferCompCount'] * _comp_size(vb_q)
    n_orig_verts = len(_dec(vb['VertexBufferData'], use_b64)) // pos_stride
    vb_edited = vb.get('VertexBufferDataEdited')
    n_new_verts = (len(_dec(vb_edited, use_b64)) // pos_stride) if vb_edited else n_orig_verts
    if n_new_verts > 0xFFFF:
        raise ValueError(f'sub{sub_idx}: {n_new_verts} vertices exceeds the uint16 limit')

    faces_e = _u16s(_dec(sub.get('FacesDataEdited') or sub['FacesData'], use_b64))
    n_faces = sub.get('FacesCountEdited', sub['FacesCount'])
    fti = _u16s(_dec(sub.get('FaceTextureIndicesEdited') or sub['FaceTextureIndices'], use_b64))

    if not is_skinned:
        # Static submeshes have separate normal arrays with per-loop indices
        # that the exporter does not re-emit.  New vertices are unsupported;
        # face reorganization of EXISTING vertices is handled via lookup.
        if n_new_verts != n_orig_verts:
            raise ValueError(
                f'sub{sub_idx}: vertex count changes on static (non-skinned) '
                f'submeshes are not supported yet (separate normal array '
                f'cannot be rebuilt from the sluggie data)')

    state_faces, face_lookup, first_state_by_tex, type3_index = \
        _decode_original_states(sub, use_b64)

    # ---- compact UVs (dedupe expanded per-loop data) ----
    uv_loop_indices = {}       # ch_ind -> per-loop compact index list
    for uv in sub.get('UVChannels', []):
        ch = uv['UVChannelIndex']
        ue = uv.get('UVChannelDataEdited')
        if ue is None:
            continue
        raw = _dec(ue, use_b64)
        uv_stride = uv['UVChannelCompCount'] * _comp_size(uv['UVChannelQuantizeInfo'])
        uvf = _u16s(_dec(uv['UVFacesDataEdited'], use_b64))
        compact: dict[bytes, int] = {}
        order: list[bytes] = []
        loop_idx = []
        for li in uvf:
            rec = raw[li * uv_stride:(li + 1) * uv_stride]
            if rec not in compact:
                compact[rec] = len(order)
                order.append(rec)
            loop_idx.append(compact[rec])
        if len(order) > 0xFFFF:
            raise ValueError(f'sub{sub_idx} uv{ch}: {len(order)} coords exceed uint16')
        uv['UVChannelDataEdited'] = _enc(b''.join(order), use_b64)
        uv['UVFacesDataEdited'] = _enc(b''.join(_u16.pack(i) for i in loop_idx), use_b64)
        uv_loop_indices[ch] = loop_idx
        _slogger.info(f'[M2] sub{sub_idx} uv{ch}: {len(uvf)} loops → {len(order)} compact coords', source="geometry.rebuild")

    # ---- per-position color fallback map from original decode ----
    color_by_pos: dict[int, int] = {}
    color_default = 0
    color_counts: dict[int, int] = {}
    for faces in state_faces.values():
        for f in faces:
            for v in f:
                if 'color0' in v:
                    color_by_pos.setdefault(v['position'], v['color0'])
                    color_counts[v['color0']] = color_counts.get(v['color0'], 0) + 1
    if color_counts:
        color_default = max(color_counts, key=color_counts.get)

    # per-position normal fallback (static meshes)
    normal_by_pos: dict[int, int] = {}
    if not is_skinned:
        for faces in state_faces.values():
            for f in faces:
                for v in f:
                    if 'lighting' in v:
                        normal_by_pos.setdefault(v['position'], v['lighting'])

    # ---- route each edited face to a display state and build vertex dicts ----
    perm = perm_info['perm'] if (is_skinned and perm_info) else None
    new_state_faces: dict[int, list] = {k: [] for k in state_faces}
    unmatched = 0
    for fi in range(n_faces):
        p = faces_e[fi * 3:fi * 3 + 3]
        key = (p[0], p[1], p[2])
        hit = face_lookup.get(key)
        if hit is not None:
            state_idx, orig_face = hit
        else:
            unmatched += 1
            state_idx = first_state_by_tex.get(fti[fi] if fi < len(fti) else 0)
            if state_idx is None:
                state_idx = next(iter(state_faces))
            orig_face = None

        face_verts = []
        for c in range(3):
            old_pos = p[c]
            new_pos = perm[old_pos] if perm else old_pos
            v = {'position': new_pos}
            if is_skinned:
                v['lighting'] = new_pos
            else:
                if orig_face is not None and 'lighting' in orig_face[c]:
                    v['lighting'] = orig_face[c]['lighting']
                elif old_pos in normal_by_pos:
                    v['lighting'] = normal_by_pos[old_pos]
            if orig_face is not None and 'color0' in orig_face[c]:
                v['color0'] = orig_face[c]['color0']
            elif color_by_pos:
                v['color0'] = color_by_pos.get(old_pos, color_default)
            for ch, loop_idx in uv_loop_indices.items():
                v[f'texture{ch}'] = loop_idx[fi * 3 + c]
            face_verts.append(v)
        new_state_faces[state_idx].append(face_verts)

    _slogger.info(f'[M2] sub{sub_idx}: {n_faces} faces routed '
          f'({n_faces - unmatched} matched to original states, {unmatched} new/changed)', source="geometry.rebuild")

    # ---- encode prim lists, upgrading index widths as needed ----
    all_faces = [f for fl in new_state_faces.values() for f in fl]
    upgraded_total: set[str] = set()
    for k, ds in enumerate(sub['DisplayStates']):
        if k not in new_state_faces:
            continue
        descs = [{'key': d['key'], 'direct': False, 'index_size': d['index_size']}
                 for d in ds['VertexStreamLayout']]
        new_descs, upgraded = computeRequiredDescriptors(all_faces, descs)
        upgraded_total |= upgraded
        raw = encodeDrawList(new_state_faces[k], new_descs)
        orig_pl = _dec(ds['PrimListData'], use_b64)
        if orig_pl and orig_pl[-1] == 0:
            raw += b'\x00'
        ds['PrimListDataEdited'] = _enc(raw, use_b64)
        ds['VertexStreamLayout'] = [
            {'key': d['key'], 'index_size': d['index_size']} for d in new_descs]

    if upgraded_total and type3_index is not None:
        t3 = sub['DisplayStates'][type3_index]
        old = _setting_int(t3['ShaderMode'])
        new = patchType3Setting(old, upgraded_total)
        if new != old:
            t3['ShaderModeEdited'] = f'{new:08x}'
            _slogger.info(f'[M2] sub{sub_idx}: index width upgraded for '
                  f'{sorted(upgraded_total)} — Type-3 setting '
                  f'0x{old:08X} → 0x{new:08X}', source="geometry.rebuild")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def rebuild_edited_geometry(data: dict) -> bool:
    """Run the Milestone 2 rebuild pass over a loaded .sluggies dict.

    Detects submeshes with topology edits and rewrites the dict in place
    into builder-ready form.  Returns True when any rebuild happened."""
    model = data['SluggiesModel']
    use_b64 = model.get('UseBase64', True)

    changed = [i for i, sub in enumerate(model.get('Submeshes', []))
               if _submesh_changed(sub, use_b64)]
    if not changed:
        return False

    _slogger.info(f'[M2] topology edits detected in submesh(es): {changed}', source="geometry.rebuild")

    perm_info = None
    for i in changed:
        sub = model['Submeshes'][i]
        if sub['VertexBuffer']['VertexBufferCompCount'] == 6:
            # Skinned submesh: permute vertices + rewrite skinning first,
            # faces must then be remapped through the permutation.
            perm_info = _rebuild_skinning(model, sub, use_b64)

    for i in changed:
        sub = model['Submeshes'][i]
        is_skinned = sub['VertexBuffer']['VertexBufferCompCount'] == 6
        _rebuild_submesh(model, sub, i, use_b64,
                         perm_info if is_skinned else None)

    return True
