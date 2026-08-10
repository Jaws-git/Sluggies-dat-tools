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
