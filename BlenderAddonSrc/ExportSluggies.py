import bpy
import json
import os
import base64
import struct
import subprocess
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ExportHelper


def _to_bytes(data) -> bytes:
    """Decode binary data that is either a base64 string or a list of byte values."""
    if isinstance(data, list):
        return bytes(data)
    return base64.b64decode(data)


def _from_bytes(raw: bytes, use_base64: bool = True):
    """Encode binary data as a base64 string or a list of byte values."""
    if use_base64:
        return base64.b64encode(raw).decode('ascii')
    return list(raw)

REQUIRED_PROPS = (
    "VertexBufferOffset",
    "VertexBufferLength",
    "VertexBufferCompCount",
    "VertexBufferQuantizeInfo",
)


def _get_custom_split_normals(obj):
    """Return per-vertex averaged custom split normals as a list of Vector, or None.

    Returns None when the mesh has no custom normals data (``has_custom_normals``
    is False).  When normals are present the loop normals are averaged per vertex
    so they map 1-to-1 with vertex indices, matching the interleaved XYZ+NxNyNz
    layout the game uses.
    """
    from mathutils import Vector
    mesh = obj.data
    if not mesh.has_custom_normals:
        return None
    vert_normals = [Vector((0.0, 0.0, 0.0)) for _ in mesh.vertices]
    vert_counts  = [0] * len(mesh.vertices)
    for loop in mesh.loops:
        vert_normals[loop.vertex_index] += Vector(loop.normal)
        vert_counts[loop.vertex_index]  += 1
    return [
        (vert_normals[i] / vert_counts[i]).normalized() if vert_counts[i] else Vector((0.0, 0.0, 1.0))
        for i in range(len(mesh.vertices))
    ]


def encode_vertex_buffer_edited(obj, comp_count, quant_info, use_custom_normals=False, use_base64=True):
    """Re-quantize edited vertex positions (and normals if comp_count==6)
    back into the original binary format and return a base64 string or byte list."""
    mesh = obj.data
    fmt_nibble = quant_info >> 4
    shift = quant_info & 0xF
    divisor = 1 << shift
    is_float = fmt_nibble in [4, 7, 0xa]

    custom_normals = _get_custom_split_normals(obj) if (use_custom_normals and comp_count >= 6) else None

    raw_bytes = bytearray()
    for v in mesh.vertices:
        comps = [v.co.x, v.co.y, v.co.z]
        if comp_count >= 6:
            if custom_normals is not None:
                n = custom_normals[v.index]
                comps += [n.x, n.y, n.z]
            else:
                comps += [v.normal.x, v.normal.y, v.normal.z]
        for val in comps:
            if is_float:
                raw_bytes += struct.pack('>f', val)
            else:
                raw_val = max(-32768, min(32767, round(val * divisor)))
                raw_bytes += struct.pack('>h', raw_val)

    return _from_bytes(bytes(raw_bytes), use_base64)


def _uv_layer_name(all_channels, target_ch_ind):
    """Return the Blender UV layer name for *target_ch_ind*, using the same
    deduplication logic that ImportSluggies applies when creating UV layers.

    When two UV channels share the same PaletteName the importer falls back
    to ``"uv<enumerate-index>"`` for the second one, so the export must
    resolve names the same way to find the right layer.
    """
    used = set()
    for enum_ind, ch in enumerate(all_channels):
        idx = ch.get('UVChannelIndex', enum_ind)
        palette = ch.get('PaletteName') or ''
        raw = palette or f'uv{enum_ind}'
        name = raw if raw not in used else f'uv{enum_ind}'
        used.add(name)
        if idx == target_ch_ind:
            return name
    return f'uv{target_ch_ind}'


def encode_uv_channel_edited(obj, json_channel, use_base64=True, all_uv_channels=None):
    """Re-quantize Blender UV layer back into the game's ST coordinate format.

    Writes each Blender UV value back into its ORIGINAL slot position, using
    UVFacesData to look up which slot index the draw list expects for each face
    loop. This preserves the original coord-array layout so the unmodified draw
    list in the .dat file keeps working correctly.

    Returns uv_channel_data_b64, or None if the matching UV layer is not found.
    Warns (via returned string list) when a slot receives two conflicting values
    (i.e. the user split a UV seam that was previously shared).
    """
    ch_ind = json_channel.get("UVChannelIndex", 0)
    if all_uv_channels is not None:
        layer_name = _uv_layer_name(all_uv_channels, ch_ind)
    else:
        layer_name = json_channel.get("PaletteName", "") or f"uv{ch_ind}"

    mesh_data = obj.data
    uv_layer = mesh_data.uv_layers.get(layer_name)
    if uv_layer is None:
        return None

    quant_info = json_channel["UVChannelQuantizeInfo"]
    comp_count = json_channel["UVChannelCompCount"]
    expected_length = json_channel["UVChannelLength"]
    fmt_nibble = quant_info >> 4
    shift = quant_info & 0xF
    divisor = 1 << shift
    is_float = fmt_nibble in [4, 7, 0xa]
    comp_size = 4 if is_float else 2
    num_slots = expected_length // (comp_count * comp_size)

    # Decode original per-face UV slot indices from UVFacesData
    uv_faces_raw = _to_bytes(json_channel["UVFacesData"])
    n = len(uv_faces_raw) // 2
    flat = list(struct.unpack(f'>{n}H', uv_faces_raw))
    original_uv_faces = [flat[i * 3 : i * 3 + 3] for i in range(n // 3)]

    # Fill output slots using original slot assignments
    # Each slot is (qs, qt); None means the slot was never referenced by a loop.
    output_slots = [None] * num_slots
    conflicts = []

    for poly in mesh_data.polygons:
        face_idx = poly.index
        if face_idx >= len(original_uv_faces):
            continue
        uv_tri = original_uv_faces[face_idx]
        for loop_offset, loop_idx in enumerate(poly.loop_indices):
            uv_slot = uv_tri[loop_offset % 3]
            uv = uv_layer.data[loop_idx].uv
            s = uv.x
            t = 1.0 - uv.y  # undo Blender V-flip applied on import
            if is_float:
                qs, qt = s, t
            else:
                qs = round(s * divisor)
                qt = round(t * divisor)
            if output_slots[uv_slot] is None:
                output_slots[uv_slot] = (qs, qt)
            elif output_slots[uv_slot] != (qs, qt):
                conflicts.append(uv_slot)

    # Fall back to original data for any slot not touched by a loop.
    # Store raw quantized values (int16 for integer format, float for float format)
    # so they stay in the same units as active slots and re-encode correctly.
    if None in output_slots:
        orig_raw = _to_bytes(json_channel["UVChannelData"])
        for slot_idx, val in enumerate(output_slots):
            if val is None:
                off = slot_idx * comp_count * comp_size
                os_ = struct.unpack_from('>f' if is_float else '>h', orig_raw, off)[0]
                ot_ = struct.unpack_from('>f' if is_float else '>h', orig_raw, off + comp_size)[0]
                output_slots[slot_idx] = (os_, ot_)

    # Encode the coord array in original slot order
    raw_bytes = bytearray()
    for (qs, qt) in output_slots:
        comps = [qs, qt] + [0.0] * (comp_count - 2)
        for val in comps:
            if is_float:
                raw_bytes += struct.pack('>f', float(val))
            else:
                raw_bytes += struct.pack('>h', max(-32768, min(32767, int(val))))

    return _from_bytes(bytes(raw_bytes), use_base64), conflicts


def encode_mesh_hammerspace(obj, json_submesh, use_custom_normals=False, use_base64=True):
    """Encode all mesh data for hammerspace export (vertex count may differ from original).

    Returns a dict with:
      'VertexBufferDataEdited':    base64 string  — full re-quantized vertex buffer
      'FacesDataEdited':           base64 string  — uint16 BE triangulated face indices
      'FacesCountEdited':          int            — triangle count
      'FaceTextureIndicesEdited':  base64 string  — uint16 BE texture index per face
                                                    (derived from Blender material slots;
                                                    used to route faces to draw states)
      'UVEdits': {ch_ind: (uv_data_b64, uv_faces_b64), ...}
    """
    mesh = obj.data
    mesh.calc_loop_triangles()
    triangles = mesh.loop_triangles

    vb_data = encode_vertex_buffer_edited(
        obj,
        obj["VertexBufferCompCount"],
        obj["VertexBufferQuantizeInfo"],
        use_custom_normals=use_custom_normals,
        use_base64=use_base64,
    )

    face_flat = [vi for tri in triangles for vi in tri.vertices]
    faces_data = _from_bytes(struct.pack(f'>{len(face_flat)}H', *face_flat), use_base64)

    # Per-face texture index derived from Blender material slots.
    # Material names are "{obj_name}_mat{tex_idx}" (set during import).
    # This is used by _rebuild_display_states in patch_inplace.py to route faces to
    # the correct display state when face count differs from the original.
    mat_to_tex: dict[int, int] = {}
    for slot_idx, slot in enumerate(obj.material_slots):
        tex_idx = 0
        if slot.material is not None:
            try:
                tex_idx = int(slot.material.name.rsplit('_mat', 1)[1])
            except (IndexError, ValueError):
                pass
        mat_to_tex[slot_idx] = tex_idx
    face_tex_flat = [
        mat_to_tex.get(mesh.polygons[tri.polygon_index].material_index, 0)
        for tri in triangles
    ]
    face_tex_data = _from_bytes(
        struct.pack(f'>{len(face_tex_flat)}H', *face_tex_flat), use_base64
    )

    all_uv_channels = json_submesh.get('UVChannels', [])
    uv_edits = {}
    for json_channel in all_uv_channels:
        ch_ind = json_channel.get('UVChannelIndex', 0)
        layer_name = _uv_layer_name(all_uv_channels, ch_ind)
        uv_layer = mesh.uv_layers.get(layer_name)
        if uv_layer is None:
            continue

        quant_info_uv = json_channel['UVChannelQuantizeInfo']
        comp_count_uv = json_channel['UVChannelCompCount']
        fmt_nibble = quant_info_uv >> 4
        shift = quant_info_uv & 0xF
        divisor = 1 << shift
        is_float = fmt_nibble in [4, 7, 0xa]

        coords = []
        coord_map = {}
        uv_tri_indices = []
        for tri in triangles:
            tri_uvs = []
            for loop_idx in tri.loops:
                uv = uv_layer.data[loop_idx].uv
                s = uv.x
                t = 1.0 - uv.y  # undo Blender V-flip applied on import
                if is_float:
                    key = (float(s), float(t))
                    qs, qt = float(s), float(t)
                else:
                    qs = round(s * divisor)
                    qt = round(t * divisor)
                    key = (int(qs), int(qt))
                if key not in coord_map:
                    coord_map[key] = len(coords)
                    coords.append((qs, qt))
                tri_uvs.append(coord_map[key])
            uv_tri_indices.append(tri_uvs)

        raw_bytes = bytearray()
        for (qs, qt) in coords:
            for val in [qs, qt] + [0.0] * (comp_count_uv - 2):
                if is_float:
                    raw_bytes += struct.pack('>f', float(val))
                else:
                    raw_bytes += struct.pack('>h', max(-32768, min(32767, int(val))))
        uv_data_b64 = _from_bytes(bytes(raw_bytes), use_base64)

        uv_flat = [idx for tri in uv_tri_indices for idx in tri]
        uv_faces_b64 = _from_bytes(struct.pack(f'>{len(uv_flat)}H', *uv_flat), use_base64)

        uv_edits[ch_ind] = (uv_data_b64, uv_faces_b64)

    return {
        'VertexBufferDataEdited':   vb_data,
        'FacesDataEdited':          faces_data,
        'FacesCountEdited':         len(triangles),
        'FaceTextureIndicesEdited': face_tex_data,
        'UVEdits':                  uv_edits,
    }


def _comp_size_skin(quant_info):
    fmt = quant_info >> 4
    return 4 if fmt in [4, 7, 0xa] else 2


def _skn_block_size(skin_data: dict, flush_ind_size: int = None) -> int:
    """Calculate total byte size of an SKN block from a SkinData or SkinDataEdited dict.

    Mirrors the logic of patch_skn_dat.skn_block_size so the Blender exporter
    can compare original vs edited sizes without importing from SluggiesTools.
    """
    sk1s   = skin_data.get('SK1s',   [])
    sk2s   = skin_data.get('SK2s',   [])
    skaccs = skin_data.get('SKAccs', [])
    if flush_ind_size is None:
        flush_ind_size = skin_data.get('FlushIndSize', 0)

    def _a4(n):
        return (n + 3) & ~3

    total  = 0x24                 # SKN header
    total += len(sk1s)   * 0x40  # SK1 structs
    total += len(sk2s)   * 0x74  # SK2 structs
    total += len(skaccs) * 0x44  # SKAcc structs

    for e in sk1s:
        total += _a4(len(_to_bytes(e['BindPoseData'])))
    for e in sk2s:
        total += _a4(len(_to_bytes(e['BindPoseData'])))
        total += _a4(len(_to_bytes(e['WeightData'])))
    for e in skaccs:
        total += _a4(len(_to_bytes(e['BindPoseData'])))
        total += _a4(len(_to_bytes(e['DestIndexData'])))
        total += _a4(len(_to_bytes(e['WeightData'])))

    if flush_ind_size:
        total += _a4(flush_ind_size * 2)

    return total


def _skn_block_size_inplace_edited(skin_data: dict) -> int:
    """Like _skn_block_size but uses *Edited field lengths when present.

    Used by encode_skin_weights_inplace to check whether the edited variable-count
    layout still fits within the original SKN block.
    """
    sk1s   = skin_data.get('SK1s',   [])
    sk2s   = skin_data.get('SK2s',   [])
    skaccs = skin_data.get('SKAccs', [])
    flush_ind_size = skin_data.get('FlushIndSize', 0)

    def _a4(n):
        return (n + 3) & ~3

    total  = 0x24
    total += len(sk1s)   * 0x40
    total += len(sk2s)   * 0x74
    total += len(skaccs) * 0x44

    for e in sk1s:
        total += _a4(len(_to_bytes(e.get('BindPoseDataEdited') or e['BindPoseData'])))
    for e in sk2s:
        total += _a4(len(_to_bytes(e.get('BindPoseDataEdited') or e['BindPoseData'])))
        total += _a4(len(_to_bytes(e.get('WeightDataEdited')   or e['WeightData'])))
    for e in skaccs:
        total += _a4(len(_to_bytes(e.get('BindPoseDataEdited')  or e['BindPoseData'])))
        total += _a4(len(_to_bytes(e.get('DestIndexDataEdited') or e['DestIndexData'])))
        total += _a4(len(_to_bytes(e.get('WeightDataEdited')    or e['WeightData'])))

    if flush_ind_size:
        total += _a4(flush_ind_size * 2)

    return total


def _get_vgroup_weight(obj, group_name, vertex_index):
    """Return 0-1 weight for vertex_index in group_name, or 0.0 if absent."""
    vg = obj.vertex_groups.get(group_name)
    if vg is None:
        return 0.0
    try:
        return vg.weight(vertex_index)
    except RuntimeError:
        return 0.0


def encode_skin_weights_inplace(candidates, data, warnings, use_custom_normals=False):
    """Re-pack SK1/SK2/SKAcc bind-pose source data and SK2/SKAcc weight bytes
    from Blender vertex positions/normals and vertex groups (in-place mode).

    Supports variable vertex counts per entry: trailing vertices that have lost
    all bone influence are trimmed from the source arrays, reducing entry size.
    Middle-removed vertices (still needed to preserve dest-slot ordering for
    subsequent kept vertices) retain their original source data.

    For SKAcc entries, any vertex can be freely removed since dest indices are
    explicit; duplicate-dest entries are always preserved verbatim.

    Writes into each SK entry:
    - 'BindPoseDataEdited'     (SK1 / SK2 / SKAcc)
    - 'WeightDataEdited'       (SK2 / SKAcc)
    - 'DestIndexDataEdited'    (SKAcc only, when count changes)
    - 'VertexCntEdited'        (any type, when count changes)

    Returns True if any entries were written, False on size overflow.
    """
    skin_data = data["SluggiesModel"].get("SkinData")
    if not skin_data or not (skin_data.get("SK1s") or skin_data.get("SK2s") or skin_data.get("SKAccs")):
        return False
    quant_info  = skin_data["QuantizeInfo"]
    use_base64  = data["SluggiesModel"].get("UseBase64", True)
    vertex_size = 6 * _comp_size_skin(quant_info)
    fmt_nibble  = quant_info >> 4
    is_float    = fmt_nibble in [4, 7, 0xa]
    divisor     = 1 << (quant_info & 0xF)
    submeshes   = data["SluggiesModel"].get("Submeshes", [])

    # Build per-submesh (vtx_start, vtx_count) using cumulative vertex counts.
    # gplVertexArr is a byte offset from the start of the runtime dest buffer;
    # gplVertexArr // vertex_size gives a global vertex index that runs
    # sequentially across all submeshes (0, 1, 2, … total_verts-1).
    # This matches how extract_bone_data in export.py maps SK entries to Blender
    # vertices — do NOT subtract (VertexBufferOffset - gpl_base) here.
    sub_ranges = []
    cumulative = 0
    for sm in submeshes:
        vb = sm["VertexBuffer"]
        vb_cs     = _comp_size_skin(vb["VertexBufferQuantizeInfo"])
        vtx_count = vb["VertexBufferLength"] // (vb["VertexBufferCompCount"] * vb_cs)
        sub_ranges.append((cumulative, vtx_count, vb["VertexBufferOffset"]))
        cumulative += vtx_count

    # Map VertexBufferOffset → object for quick lookup
    obj_by_vb = {str(obj["VertexBufferOffset"]): obj for obj in candidates if "VertexBufferOffset" in obj}

    # Pre-compute custom split normals per object when requested
    custom_normals_cache = {}
    if use_custom_normals:
        for _obj in candidates:
            if "VertexBufferOffset" in _obj:
                _cn = _get_custom_split_normals(_obj)
                if _cn is not None:
                    custom_normals_cache[str(_obj["VertexBufferOffset"])] = _cn

    def resolve(global_vtx):
        for j, (start, count, vb_off) in enumerate(sub_ranges):
            if start <= global_vtx < start + count:
                return obj_by_vb.get(str(vb_off)), global_vtx - start
        return None, global_vtx

    def pack_val(v):
        if is_float:
            return struct.pack('>f', float(v))
        return struct.pack('>h', max(-32768, min(32767, round(float(v) * divisor))))

    def encode_vertex(obj, local_v):
        vd  = obj.data.vertices[local_v]
        _cn = custom_normals_cache.get(str(obj["VertexBufferOffset"]))
        nx, ny, nz = (
            (_cn[local_v].x, _cn[local_v].y, _cn[local_v].z)
            if _cn is not None
            else (vd.normal.x, vd.normal.y, vd.normal.z)
        )
        buf = bytearray()
        for val in [vd.co.x, vd.co.y, vd.co.z, nx, ny, nz]:
            buf.extend(pack_val(val))
        return bytes(buf)

    wrote_any = False

    # --- SK1 entries (source data only, no weights) ---
    # Re-encode in dest-slot order (position i → dest slot gplBase + vtx_off + i).
    # Trailing vertices with weight=0 are trimmed; middle-removed ones keep
    # their original source bytes so subsequent kept vertices stay at the
    # correct dest slot.
    for sk1 in skin_data.get("SK1s", []):
        bone_id      = sk1["BoneIndex"]
        bone_name    = f"bone_{bone_id}"
        n            = sk1["VertexCnt"]
        vtx_off      = sk1.get("VertexOffset", 0)
        global_start = (sk1["GplVertexArrValue"] + vtx_off) // vertex_size
        orig_src     = _to_bytes(sk1["BindPoseData"])

        last_kept  = -1
        slot_bytes = []
        for i in range(n):
            obj, local_v = resolve(global_start + i)
            if obj is None:
                # Vertex outside imported submeshes — treat as kept, preserve.
                slot_bytes.append(orig_src[vtx_off + i * vertex_size : vtx_off + (i + 1) * vertex_size])
                last_kept = i
            else:
                if _get_vgroup_weight(obj, bone_name, local_v) > 0:
                    slot_bytes.append(encode_vertex(obj, local_v))
                    last_kept = i
                else:
                    # Vertex removed — keep original for middle-ordering.
                    slot_bytes.append(orig_src[vtx_off + i * vertex_size : vtx_off + (i + 1) * vertex_size])

        new_count = last_kept + 1 if last_kept >= 0 else 0
        src = orig_src[:vtx_off] + b''.join(slot_bytes[:new_count])

        sk1["BindPoseDataEdited"] = _from_bytes(src, use_base64)
        if new_count != n:
            sk1["VertexCntEdited"] = new_count
        wrote_any = True

    # --- SK2 entries (source data + blend weights) ---
    # A vertex is "kept" if it still has at least one non-zero weight for
    # either bone in this pair.  Both weights zero → candidate for trailing trim.
    for sk2 in skin_data.get("SK2s", []):
        bone1        = sk2["BoneIndex1"]
        bone2        = sk2["BoneIndex2"]
        bone1_name   = f"bone_{bone1}"
        bone2_name   = f"bone_{bone2}"
        n            = sk2["VertexCnt"]
        vtx_off      = sk2.get("VertexOffset", 0)
        global_start = (sk2["GplVertexArrValue"] + vtx_off) // vertex_size
        orig_src     = _to_bytes(sk2["BindPoseData"])
        orig_wt      = _to_bytes(sk2["WeightData"])

        last_kept  = -1
        slot_src   = []
        slot_wt    = []
        for i in range(n):
            obj, local_v = resolve(global_start + i)
            if obj is None:
                slot_src.append(orig_src[vtx_off + i * vertex_size : vtx_off + (i + 1) * vertex_size])
                slot_wt.append(bytes([orig_wt[i * 2], orig_wt[i * 2 + 1]]))
                last_kept = i
            else:
                w1 = _get_vgroup_weight(obj, bone1_name, local_v)
                w2 = _get_vgroup_weight(obj, bone2_name, local_v)
                if w1 > 0 or w2 > 0:
                    slot_src.append(encode_vertex(obj, local_v))
                    slot_wt.append(bytes([
                        max(0, min(255, round(w1 * 256))),
                        max(0, min(255, round(w2 * 256))),
                    ]))
                    last_kept = i
                else:
                    # Both bones removed — keep original for middle-ordering.
                    slot_src.append(orig_src[vtx_off + i * vertex_size : vtx_off + (i + 1) * vertex_size])
                    slot_wt.append(bytes([orig_wt[i * 2], orig_wt[i * 2 + 1]]))

        new_count = last_kept + 1 if last_kept >= 0 else 0
        src = orig_src[:vtx_off] + b''.join(slot_src[:new_count])
        wt  = b''.join(slot_wt[:new_count])

        sk2["BindPoseDataEdited"] = _from_bytes(src, use_base64)
        sk2["WeightDataEdited"]   = _from_bytes(wt, use_base64)
        if new_count != n:
            sk2["VertexCntEdited"] = new_count
        wrote_any = True

    # --- SKAcc entries (source data + dest indices + weights) ---
    # Since dest indices are explicit, any vertex can be freely removed without
    # breaking the dest-slot ordering of other vertices.
    # Duplicate-dest entries are always preserved verbatim (same reasoning as
    # before: Blender stores only one weight per (bone, vertex) pair).
    for skacc in skin_data.get("SKAccs", []):
        bone_id   = skacc["BoneIndex"]
        bone_name = f"bone_{bone_id}"
        n         = skacc["VertexCnt"]
        dest_base = skacc["GplDestArrValue"] // vertex_size
        orig_src  = _to_bytes(skacc["BindPoseData"])
        orig_wt   = _to_bytes(skacc["WeightData"])
        dest_idxs = list(struct.unpack(f'>{n}H', _to_bytes(skacc["DestIndexData"])))

        dest_count = {}
        for di in dest_idxs:
            dest_count[di] = dest_count.get(di, 0) + 1
        dup_dests = {di for di, cnt in dest_count.items() if cnt > 1}

        new_src  = bytearray()
        new_dest = bytearray()
        new_wt   = bytearray()

        for i in range(n):
            di       = dest_idxs[i]
            global_v = dest_base + di
            obj, local_v = resolve(global_v)

            if di in dup_dests or obj is None:
                # Preserve verbatim — dup-dest or unknown vertex.
                new_src  += orig_src[i * vertex_size : (i + 1) * vertex_size]
                new_dest += struct.pack('>H', di)
                new_wt   += bytes([orig_wt[i]])
            else:
                w = _get_vgroup_weight(obj, bone_name, local_v)
                if w > 0:
                    new_src  += encode_vertex(obj, local_v)
                    new_dest += struct.pack('>H', di)
                    new_wt   += bytes([max(0, min(255, round(w * 256)))])
                # else: vertex removed from this SKAcc entry — skip entirely.

        new_count = len(new_dest) // 2
        skacc["BindPoseDataEdited"]  = _from_bytes(bytes(new_src),  use_base64)
        skacc["WeightDataEdited"]    = _from_bytes(bytes(new_wt),   use_base64)
        if new_count != n:
            skacc["DestIndexDataEdited"] = _from_bytes(bytes(new_dest), use_base64)
            skacc["VertexCntEdited"]     = new_count
        wrote_any = True

    # --- Size check ---
    # Edited size can never exceed original because we only shrink or keep equal.
    # This guard catches any unforeseen logic errors.
    orig_size = _skn_block_size(skin_data)
    edit_size = _skn_block_size_inplace_edited(skin_data)

    if edit_size > orig_size:
        overflow = edit_size - orig_size
        # Strip all edited fields so the patcher ignores this model.
        for sk1 in skin_data.get("SK1s", []):
            for k in ('BindPoseDataEdited', 'VertexCntEdited'):
                sk1.pop(k, None)
        for sk2 in skin_data.get("SK2s", []):
            for k in ('BindPoseDataEdited', 'WeightDataEdited', 'VertexCntEdited'):
                sk2.pop(k, None)
        for skacc in skin_data.get("SKAccs", []):
            for k in ('BindPoseDataEdited', 'WeightDataEdited', 'DestIndexDataEdited', 'VertexCntEdited'):
                skacc.pop(k, None)
        warnings.append(
            f"SKN block too large for in-place patch. "
            f"Original: {orig_size} B, Edited: {edit_size} B, "
            f"Overflow: +{overflow} B. Reduce vertex count or simplify bone influences."
        )
        return False

    return wrote_any


def encode_skin_hammerspace(candidates, data, warnings, use_custom_normals=False):
    """Rebuild SK1/SK2/SKAcc from Blender vertex groups for hammerspace export.

    Splitting rules:
      1 influence  → SK1
      2 influences → SK2
      3+ influences → SK2 (top 2 by weight) + SKAcc (remainder)

    Bone pairs in SK2 are stored with the lower BoneId first.
    Source data (bind-pose XYZ + NxNyNz) is encoded with SkinData.QuantizeInfo.
    Writes 'SkinDataEdited' at the model level.  Returns True if written.
    """
    skin_data = data["SluggiesModel"].get("SkinData")
    if not skin_data:
        return False

    submeshes  = data["SluggiesModel"].get("Submeshes", [])
    quant_info = skin_data["QuantizeInfo"]
    use_base64 = data["SluggiesModel"].get("UseBase64", True)
    cs         = _comp_size_skin(quant_info)
    fmt_nibble = quant_info >> 4
    is_float   = fmt_nibble in [4, 7, 0xa]
    divisor    = 1 << (quant_info & 0xF)

    def pack_val(v):
        if is_float:
            return struct.pack('>f', float(v))
        return struct.pack('>h', max(-32768, min(32767, round(float(v) * divisor))))

    # Map VertexBufferOffset → (submesh_idx, obj)
    obj_to_sub = {}
    for j, sm in enumerate(submeshes):
        vb_off = str(sm["VertexBuffer"]["VertexBufferOffset"])
        for obj in candidates:
            if "VertexBufferOffset" in obj and str(obj["VertexBufferOffset"]) == vb_off:
                obj_to_sub[id(obj)] = (j, obj)
                break

    # Only bones that already appear in the original SK data should feed the
    # rebuild.  Non-skinned bones own static submesh geometry and have NO SK
    # entries; their vertex groups must not be promoted to SK1/SK2/SKAcc entries
    # or the new block will exceed the original's size even with no mesh changes.
    skinned_bone_ids: set[int] = set()
    for _e in skin_data.get('SK1s',   []):
        skinned_bone_ids.add(_e['BoneIndex'])
    for _e in skin_data.get('SK2s',   []):
        skinned_bone_ids.add(_e['BoneIndex1'])
        skinned_bone_ids.add(_e['BoneIndex2'])
    for _e in skin_data.get('SKAccs', []):
        skinned_bone_ids.add(_e['BoneIndex'])

    # Validation sets used to prevent phantom entries caused by SKAcc dest-slot
    # aliasing SK1/SK2 source slots.  In act.py, SKAcc vertex indices use the
    # *dest* slot (gplDestArr + dests[i]), which can equal the *source* slot of
    # an SK1/SK2 entry.  When they coincide the same Blender vertex carries
    # weights from both bones → naive rebuild creates phantom SK2 pairs.
    #
    # Rule: only allow an SK2 pair that already existed in the original data.
    # Any pair whose (b_lo, b_hi) is absent from the original SK2 list is
    # treated as SK1 (dominant bone) + SKAcc (subordinate bone, if it was an
    # original SKAcc bone).  This exactly reconstructs SK1+SKAcc collisions.
    original_sk2_pairs: set[tuple[int, int]] = set()
    for _e in skin_data.get('SK2s', []):
        _b1, _b2 = _e['BoneIndex1'], _e['BoneIndex2']
        original_sk2_pairs.add((min(_b1, _b2), max(_b1, _b2)))

    original_skacc_bone_ids: set[int] = set(
        _e['BoneIndex'] for _e in skin_data.get('SKAccs', [])
    )
    # Bones that appear in SK1 take priority: if a bone is in both SK1 and SKAcc
    # (which happens when the same bone drives both a source-slot copy and an
    # accumulation pass), a single-influence vertex must go to SK1, not SKAcc.
    original_sk1_bone_ids: set[int] = set(
        _e['BoneIndex'] for _e in skin_data.get('SK1s', [])
    )
    # SKAcc-only bones: exclusively in SKAcc, not in any SK1 entry.
    skacc_only_bone_ids: set[int] = original_skacc_bone_ids - original_sk1_bone_ids

    # Pre-compute custom split normals per object when requested
    custom_normals_cache = {}
    if use_custom_normals:
        for _obj_id, (_, _obj) in obj_to_sub.items():
            _cn = _get_custom_split_normals(_obj)
            if _cn is not None:
                custom_normals_cache[_obj_id] = _cn

    sk1_groups  = {}   # bone_id → [(sub_idx, local_v, obj)]
    sk2_groups  = {}   # (b_lo, b_hi) → [(sub_idx, local_v, w_lo, w_hi, obj)]
    skacc_groups = {}  # bone_id → [(sub_idx, local_v, weight, dest_local_v, obj)]

    for _, (sub_idx, obj) in obj_to_sub.items():
        for v in obj.data.vertices:
            parsed = []
            for vge in v.groups:
                vg = obj.vertex_groups[vge.group]
                if not vg.name.startswith("bone_") or vge.weight <= 0.0:
                    continue
                try:
                    bone_id = int(vg.name[5:])
                except ValueError:
                    continue
                if bone_id not in skinned_bone_ids:
                    continue
                parsed.append((bone_id, vge.weight))
            parsed.sort(key=lambda x: -x[1])
            if not parsed:
                continue

            v_idx = v.index
            if len(parsed) == 1:
                b, w = parsed[0]
                if b in skacc_only_bone_ids:
                    # Single-influence vertex whose only bone is exclusively an
                    # SKAcc bone (no SK1 entry for this bone) — must go to SKAcc.
                    # Bones that appear in both SK1 and SKAcc stay in SK1 so we
                    # don't lose their source-slot contributions.
                    skacc_groups.setdefault(b, []).append((sub_idx, v_idx, w, v_idx, obj))
                else:
                    sk1_groups.setdefault(b, []).append((sub_idx, v_idx, obj))
            else:
                # SK2 from top 2, canonically ordered by bone id.
                # IMPORTANT: only form an SK2 pair if that exact (b_lo, b_hi)
                # pair already exists in the original SK2 data.  When an SKAcc
                # entry accumulates on top of an SK1 entry at the same dest
                # slot, the same Blender vertex carries weights for both bones,
                # making len(parsed)==2 even though the original structure is
                # SK1 + SKAcc (not SK2).  Blindly creating a new SK2 pair
                # produces an entry absent from the original → phantom SK2 →
                # block overflow.  If the pair is absent, fall back to:
                #   top bone    → SK1
                #   other bones → SKAcc  (only if bone is an original SKAcc bone)
                (b1, w1), (b2, w2) = parsed[0], parsed[1]
                b_lo, b_hi = (b1, b2) if b1 <= b2 else (b2, b1)
                w_lo = w1 if b1 <= b2 else w2
                w_hi = w2 if b1 <= b2 else w1

                if (b_lo, b_hi) in original_sk2_pairs:
                    sk2_groups.setdefault((b_lo, b_hi), []).append(
                        (sub_idx, v_idx, w_lo, w_hi, obj))
                    # SKAcc for any remaining influences (only original SKAcc bones)
                    for b, w in parsed[2:]:
                        if b in original_skacc_bone_ids:
                            skacc_groups.setdefault(b, []).append(
                                (sub_idx, v_idx, w, v_idx, obj))
                else:
                    # Phantom pair — reclassify as SK1 (dominant) + SKAcc (rest)
                    b_top, _ = parsed[0]
                    sk1_groups.setdefault(b_top, []).append((sub_idx, v_idx, obj))
                    for b, w in parsed[1:]:
                        if b in original_skacc_bone_ids:
                            skacc_groups.setdefault(b, []).append(
                                (sub_idx, v_idx, w, v_idx, obj))

    def encode_src(entries):
        raw = bytearray()
        for entry in entries:
            obj_ref = entry[-1]
            v       = obj_ref.data.vertices[entry[1]]
            _cn     = custom_normals_cache.get(id(obj_ref))
            nx, ny, nz = (_cn[v.index].x, _cn[v.index].y, _cn[v.index].z) if _cn is not None else (v.normal.x, v.normal.y, v.normal.z)
            for val in [v.co.x, v.co.y, v.co.z, nx, ny, nz]:
                raw += pack_val(val)
        return _from_bytes(bytes(raw), use_base64)

    # Build lookups so gplVertexArr / gplDestArr can be carried forward.
    # The original values remain correct for the hammerspace GPL because Blender
    # preserves vertex ordering (slot i in the Blender mesh == slot i in the GPL
    # position buffer).  Without these values the runtime CPU skinning writes all
    # SK1/SK2 output on top of each other at offset 0.
    _orig_sk1_gva = {
        e['BoneIndex']: e.get('GplVertexArrValue', 0)
        for e in skin_data.get('SK1s', [])
    }
    _orig_sk2_gva = {
        (min(e['BoneIndex1'], e['BoneIndex2']), max(e['BoneIndex1'], e['BoneIndex2'])):
            e.get('GplVertexArrValue', 0)
        for e in skin_data.get('SK2s', [])
    }
    _orig_skacc_gda = {
        e['BoneIndex']: e.get('GplDestArrValue', 0)
        for e in skin_data.get('SKAccs', [])
    }

    new_sk1s = [
        {"BoneIndex": b, "VertexCnt": len(e), "BindPoseData": encode_src(e),
         "GplVertexArrValue": _orig_sk1_gva.get(b, 0)}
        for b, e in sorted(sk1_groups.items())
    ]

    new_sk2s = []
    for (b_lo, b_hi), entries in sorted(sk2_groups.items()):
        wt = bytearray()
        for e in entries:
            wt.append(max(0, min(255, round(e[2] * 256))))
            wt.append(max(0, min(255, round(e[3] * 256))))
        new_sk2s.append({
            "BoneIndex1": b_lo, "BoneIndex2": b_hi,
            "VertexCnt": len(entries),
            "BindPoseData": encode_src(entries),
            "WeightData": _from_bytes(bytes(wt), use_base64),
            "GplVertexArrValue": _orig_sk2_gva.get((b_lo, b_hi), 0),
        })

    new_skaccs = []
    for bone_id, entries in sorted(skacc_groups.items()):
        wt   = bytearray()
        dest = bytearray()
        for e in entries:
            wt   += bytes([max(0, min(255, round(e[2] * 256)))])
            dest += struct.pack('>H', e[3])
        new_skaccs.append({
            "BoneIndex": bone_id, "VertexCnt": len(entries),
            "BindPoseData":   encode_src(entries),
            "WeightData":    _from_bytes(bytes(wt), use_base64),
            "DestIndexData": _from_bytes(bytes(dest), use_base64),
            "GplDestArrValue": _orig_skacc_gda.get(bone_id, 0),
        })

    flush_ind_size = skin_data.get('FlushIndSize', 0)

    candidate_edited = {
        "QuantizeInfo": quant_info,
        "SK1s":         new_sk1s,
        "SK2s":         new_sk2s,
        "SKAccs":       new_skaccs,
    }

    orig_size = _skn_block_size(skin_data)
    edit_size = _skn_block_size(candidate_edited, flush_ind_size=flush_ind_size)

    # Per-category vertex totals — used in both feedback paths.
    orig_sk1s   = skin_data.get('SK1s',   [])
    orig_sk2s   = skin_data.get('SK2s',   [])
    orig_skaccs = skin_data.get('SKAccs', [])
    orig_v1 = sum(e['VertexCnt'] for e in orig_sk1s)
    orig_v2 = sum(e['VertexCnt'] for e in orig_sk2s)
    orig_v3 = sum(e['VertexCnt'] for e in orig_skaccs)
    edit_v1 = sum(e['VertexCnt'] for e in new_sk1s)
    edit_v2 = sum(e['VertexCnt'] for e in new_sk2s)
    edit_v3 = sum(e['VertexCnt'] for e in new_skaccs)

    # The hammerspace rebuild always uses vertex_offset=0, so BindPoseData blobs
    # omit the VertexOffset prefix bytes present in original SK1/SK2 entries.
    # Add those bytes back to edit_size so the comparison reflects actual payload
    # content: when SK1/SK2/SKAcc entries and vertex counts are identical the sizes
    # compare as equal and no spurious shrinkage message is shown.
    def _a4_vo(n): return (n + 3) & ~3
    vo_adjustment = (
        sum(_a4_vo(e.get('VertexOffset', 0)) for e in orig_sk1s) +
        sum(_a4_vo(e.get('VertexOffset', 0)) for e in orig_sk2s)
    )
    edit_size_adj = edit_size + vo_adjustment

    def _sk_stats(n1, v1, n2, v2, n3, v3, total):
        return (
            f"{total} B  "
            f"SK1: {n1} entries / {v1} verts  "
            f"SK2: {n2} entries / {v2} verts  "
            f"SKAcc: {n3} entries / {v3} verts"
        )

    if edit_size > orig_size:
        overflow = edit_size - orig_size
        stats = (
            f"SKN block too large to patch in-place.\n"
            f"  Original: {_sk_stats(len(orig_sk1s), orig_v1, len(orig_sk2s), orig_v2, len(orig_skaccs), orig_v3, orig_size)}\n"
            f"  Edited:   {_sk_stats(len(new_sk1s), edit_v1, len(new_sk2s), edit_v2, len(new_skaccs), edit_v3, edit_size)}\n"
            f"  Overflow: +{overflow} B — reduce vertex count or simplify bone influences."
        )
        return False, stats

    effective_shrinkage = orig_size - edit_size_adj
    candidate_edited["FlushIndSize"] = flush_ind_size
    data["SluggiesModel"]["SkinDataEdited"] = candidate_edited

    if effective_shrinkage > 0:
        verts_match = (edit_v1 == orig_v1 and edit_v2 == orig_v2 and edit_v3 == orig_v3)
        msg = (
            f"SKN block shrunk by {effective_shrinkage} B; patcher will zero-pad the remainder.\n"
            f"  Original: {_sk_stats(len(orig_sk1s), orig_v1, len(orig_sk2s), orig_v2, len(orig_skaccs), orig_v3, orig_size)}\n"
            f"  Edited:   {_sk_stats(len(new_sk1s), edit_v1, len(new_sk2s), edit_v2, len(new_skaccs), edit_v3, edit_size)}"
        )
        if not verts_match:
            msg += (
                f"\n  Note: SKAcc vertex count differs from original — this is normal"
                f" when some SKAcc dest slots fall outside submesh 0 (e.g. Head/Accessory"
                f" submeshes). Those vertices are not part of the edited mesh and do not"
                f" affect main-mesh skinning. Hammerspace write can proceed."
            )
        return True, msg
    return True, None


def _purge_skn_edited(data):
    """Strip all *Edited keys from SkinData SK entries and remove SkinDataEdited.

    Called at the start of every export so stale fields from a previous run
    never bleed into the new output.
    """
    model = data.get("SluggiesModel", {})
    model.pop("SkinDataEdited", None)
    skin = model.get("SkinData")
    if not skin:
        return
    sk1_keys   = ("BindPoseDataEdited", "VertexCntEdited")
    sk2_keys   = ("BindPoseDataEdited", "WeightDataEdited", "VertexCntEdited")
    skacc_keys = ("BindPoseDataEdited", "WeightDataEdited", "DestIndexDataEdited", "VertexCntEdited")
    for e in skin.get("SK1s",   []):
        for k in sk1_keys:   e.pop(k, None)
    for e in skin.get("SK2s",   []):
        for k in sk2_keys:   e.pop(k, None)
    for e in skin.get("SKAccs", []):
        for k in skacc_keys: e.pop(k, None)


def detect_length_mismatches(obj, json_submesh):
    """Return a list of human-readable strings describing any buffer-length
    changes that would require Hammerspace Mode to export correctly.

    Checks vertex buffer and all UV channels.  An empty list means all lengths
    are compatible with in-place patching.
    """
    issues = []
    vb = json_submesh.get("VertexBuffer", {})
    comp_count = vb.get("VertexBufferCompCount", 3)
    quant_info = vb.get("VertexBufferQuantizeInfo", 0)
    fmt = quant_info >> 4
    cs  = 4 if fmt in [4, 7, 0xa] else 2
    expected_vb_len = vb.get("VertexBufferLength", 0)
    actual_vb_len   = len(obj.data.vertices) * comp_count * cs
    if actual_vb_len != expected_vb_len:
        orig_vcount = expected_vb_len // (comp_count * cs) if comp_count * cs else 0
        issues.append(
            f"vertex count changed: {orig_vcount} → {len(obj.data.vertices)} "
            f"(buffer {expected_vb_len} B → {actual_vb_len} B)"
        )

    for ch in json_submesh.get("UVChannels", []):
        ch_ind       = ch.get("UVChannelIndex", 0)
        uv_comp      = ch.get("UVChannelCompCount", 2)
        uv_quant     = ch.get("UVChannelQuantizeInfo", 0)
        uv_cs        = 4 if (uv_quant >> 4) in [4, 7, 0xa] else 2
        orig_uv_len  = ch.get("UVChannelLength", 0)
        orig_uv_cnt  = orig_uv_len // (uv_comp * uv_cs) if uv_comp * uv_cs else 0
        # Count distinct UV coords in the Blender layer via UVFacesData slot range
        uv_faces_raw = ch.get("UVFacesData")
        if uv_faces_raw:
            import struct as _st
            raw   = _to_bytes(uv_faces_raw)
            n     = len(raw) // 2
            slots = list(_st.unpack(f'>{n}H', raw))
            new_uv_cnt = max(slots) + 1 if slots else 0
        else:
            new_uv_cnt = orig_uv_cnt
        if new_uv_cnt != orig_uv_cnt:
            issues.append(
                f"UV channel {ch_ind} slot count changed: {orig_uv_cnt} → {new_uv_cnt}"
            )

    return issues


def validate_against_json(obj, json_submesh):
    """Return a list of mismatch descriptions, empty if everything matches."""
    vb = json_submesh.get("VertexBuffer", {})
    mismatches = []
    for prop in REQUIRED_PROPS:
        obj_val = obj.get(prop)
        json_val = vb.get(prop)
        if obj_val is None:
            mismatches.append(f"object missing custom property '{prop}'")
        elif str(obj_val) != str(json_val):
            mismatches.append(f"{prop}: object={obj_val}, json={json_val}")
    return mismatches


class SLUGGIES_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "sluggies.export_json"
    bl_label = "Export Sluggers intermediate"
    bl_description = "Write edited vertex data back into a Sluggers intermediate JSON file"
    bl_options = {"UNDO"}

    filename_ext = ".sluggie"
    filter_glob: StringProperty(default="*.sluggie", options={"HIDDEN"})  # type: ignore[valid-type]
    use_hammerspace: BoolProperty(  # type: ignore[valid-type]
        name="Hammerspace Mode",
        description=(
            "Allow vertex count changes. Encodes new face indices and dense UV "
            "coords for writeExpandedMesh() pointer patching. "
            "Leave off for simple in-place edits that preserve vertex count."
        ),
        default=False,
    )
    use_custom_normals: BoolProperty(  # type: ignore[valid-type]
        name="Include Custom Split Normals",
        description=(
            "Write Blender custom split normals back into the export. "
            "Only affects models that already store normals (CompCount=6). "
            "Leave off to keep the original normal values unchanged."
        ),
        default=False,
    )

    def execute(self, context):
        # --- load and sanity-check the target JSON ---
        try:
            with open(self.filepath, 'r') as f:
                content = f.read().strip()
        except Exception as e:
            self.report({"ERROR"}, f"Could not read file: {e}")
            return {"CANCELLED"}

        if not content:
            self.report({"ERROR"}, "Target JSON file is empty.")
            return {"CANCELLED"}

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            self.report({"ERROR"}, f"JSON parse error: {e}")
            return {"CANCELLED"}

        if "SluggiesModel" not in data:
            self.report({"ERROR"}, "JSON does not contain a 'SluggiesModel' entry.")
            return {"CANCELLED"}

        use_base64 = data["SluggiesModel"].get("UseBase64", True)
        submeshes = data["SluggiesModel"].get("Submeshes", [])

        # --- collect selected mesh objects that carry Sluggies custom properties ---
        candidates = [
            obj for obj in context.selected_objects
            if obj.type == 'MESH' and all(prop in obj for prop in REQUIRED_PROPS)
        ]

        if not candidates:
            self.report({"ERROR"},
                "No selected mesh objects with Sluggies custom properties found. "
                "Import the JSON first, then select the meshes you want to export.")
            return {"CANCELLED"}

        if len(candidates) > len(submeshes):
            self.report({"ERROR"},
                f"{len(candidates)} mesh(es) selected but the target JSON only defines "
                f"{len(submeshes)} submesh(es). Make sure you are exporting to the correct file.")
            return {"CANCELLED"}

        written = 0
        warnings = []

        for obj in candidates:
            # match by VertexBufferOffset (unique per submesh)
            target_submesh = next(
                (sm for sm in submeshes
                 if str(obj["VertexBufferOffset"]) == str(sm.get("VertexBuffer", {}).get("VertexBufferOffset"))),
                None
            )
            if target_submesh is None:
                warnings.append(
                    f"{obj.name}: no submesh with VertexBufferOffset="
                    f"{obj['VertexBufferOffset']} found in JSON — skipped."
                )
                continue

            if self.use_hammerspace:
                hs = encode_mesh_hammerspace(obj, target_submesh, use_custom_normals=self.use_custom_normals, use_base64=use_base64)
                target_submesh["VertexBuffer"]["VertexBufferDataEdited"] = hs['VertexBufferDataEdited']
                target_submesh["FacesDataEdited"] = hs['FacesDataEdited']
                target_submesh["FacesCountEdited"] = hs['FacesCountEdited']
                target_submesh["FaceTextureIndicesEdited"] = hs['FaceTextureIndicesEdited']
                for json_channel in target_submesh.get("UVChannels", []):
                    ch_ind = json_channel.get("UVChannelIndex", 0)
                    if ch_ind in hs['UVEdits']:
                        uv_data_b64, uv_faces_b64 = hs['UVEdits'][ch_ind]
                        json_channel["UVChannelDataEdited"] = uv_data_b64
                        json_channel["UVFacesDataEdited"] = uv_faces_b64
                    else:
                        layer_name = _uv_layer_name(target_submesh.get("UVChannels", []), ch_ind)
                        warnings.append(
                            f"{obj.name}: UV layer '{layer_name}' not found — UV channel {ch_ind} skipped."
                        )
            else:
                mismatches = validate_against_json(obj, target_submesh)
                if mismatches:
                    warnings.append(
                        f"{obj.name}: metadata mismatch ({'; '.join(mismatches)}) — skipped."
                    )
                    continue

                length_issues = detect_length_mismatches(obj, target_submesh)
                if length_issues:
                    self.report({"INFO"},
                        f"{obj.name}: buffer length change(s) detected but Hammerspace Mode is "
                        f"off — data written in-place (will be skipped when patching): "
                        + "; ".join(length_issues)
                    )

                edited_data = encode_vertex_buffer_edited(
                    obj,
                    obj["VertexBufferCompCount"],
                    obj["VertexBufferQuantizeInfo"],
                    use_custom_normals=self.use_custom_normals,
                    use_base64=use_base64,
                )
                target_submesh["VertexBuffer"]["VertexBufferDataEdited"] = edited_data
                # Clear any stale hammerspace face data so it can't mismatch the
                # in-place vertex buffer (which must stay at the original vertex count).
                target_submesh.pop("FacesDataEdited", None)
                target_submesh.pop("FacesCountEdited", None)
                target_submesh.pop("FaceTextureIndicesEdited", None)

                # Re-encode UV channels from Blender UV layers
                hammerspace_hint_shown = False
                _all_uv_ch = target_submesh.get("UVChannels", [])
                for json_channel in _all_uv_ch:
                    result = encode_uv_channel_edited(obj, json_channel, use_base64=use_base64, all_uv_channels=_all_uv_ch)
                    ch_ind = json_channel.get("UVChannelIndex", 0)
                    if result is None:
                        layer_name = _uv_layer_name(_all_uv_ch, ch_ind)
                        warnings.append(
                            f"{obj.name}: UV layer '{layer_name}' not found — UV channel {ch_ind} skipped."
                        )
                        continue
                    uv_data_b64, conflicts = result
                    if conflicts and not hammerspace_hint_shown:
                        warnings.append(
                            f"{obj.name}: UV seam conflict(s) detected "
                            f"- Did you remember to activate hammerspace mode?"
                        )
                        hammerspace_hint_shown = True
                    json_channel["UVChannelDataEdited"] = uv_data_b64
                    # UVFacesDataEdited is no longer written: the draw list indices
                    # are unchanged so UVFacesData still applies after patching.

            # Write back DisplayState shader modes (Type-7 FourCC codes) if edited.
            # Collect from DisplayStateShaderMode1/DisplayStateShaderMode1_Offset, ...
            edited_lookup = {}
            n = 1
            while f"DisplayStateShaderMode{n}" in obj:
                setting = str(obj[f"DisplayStateShaderMode{n}"])
                offset  = str(obj.get(f"DisplayStateShaderMode{n}_Offset") or "")
                if offset:
                    edited_lookup[offset] = setting
                n += 1
            for ds in target_submesh.get("DisplayStates", []):
                if ds.get("DisplayStateId") != 7:
                    continue
                off = ds.get("ShaderModeFieldOffset")
                original = ds.get("ShaderMode", "")
                if off in edited_lookup:
                    new_val = edited_lookup[off]
                    if new_val != original:
                        ds["ShaderModeEdited"] = new_val
                    else:
                        ds.pop("ShaderModeEdited", None)  # edited back to original — clear it
                else:
                    ds.pop("ShaderModeEdited", None)  # not in Blender props — clear any stale value

            written += 1

        # Skin data is model-level — purge stale edited fields, then re-encode.
        _purge_skn_edited(data)
        if self.use_hammerspace:
            skn_ok, skn_msg = encode_skin_hammerspace(
                candidates, data, warnings, use_custom_normals=self.use_custom_normals)
            if not skn_ok:
                self.report({"ERROR"}, skn_msg)
                return {"CANCELLED"}
            if skn_msg:
                self.report({"INFO"}, skn_msg)
        else:
            encode_skin_weights_inplace(candidates, data, warnings, use_custom_normals=self.use_custom_normals)

        for w in warnings:
            self.report({"WARNING"}, w)

        if written == 0:
            self.report({"ERROR"}, "No submeshes written. Check the warnings above.")
            return {"CANCELLED"}

        data["SluggiesModel"]["UseHammerspace"] = self.use_hammerspace

        with open(self.filepath, 'w') as f:
            json.dump(data, f, indent=2)

        filename = os.path.basename(self.filepath)
        context.window_manager.clipboard = filename
        subprocess.run(['clip'], input=filename.encode('utf-16-le'), check=False)
        self.report({"INFO"},
            f"Wrote edited vertex data for {written} submesh(es) to {self.filepath}")
        return {"FINISHED"}


def menu_func_export(self, context):
    self.layout.operator(SLUGGIES_OT_export.bl_idname, text="Sluggers intermediate (.sluggie)")


def register():
    bpy.utils.register_class(SLUGGIES_OT_export)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.utils.unregister_class(SLUGGIES_OT_export)
