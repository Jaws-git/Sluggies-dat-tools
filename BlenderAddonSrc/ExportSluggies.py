import bpy
import json
import math
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


def _pack_quantized_component(value, divisor, context):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{context}: coordinate/component is not finite ({value})")
    quantized = round(value * divisor)
    if not -32768 <= quantized <= 32767:
        minimum = -32768 / divisor
        maximum = 32767 / divisor
        raise ValueError(
            f"{context}: value {value} quantizes to {quantized}, outside int16 "
            f"range [-32768, 32767] (representable coordinates {minimum}..{maximum})"
        )
    return struct.pack('>h', quantized)

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
    basis_key = None
    if mesh.shape_keys:
        basis_key = mesh.shape_keys.key_blocks.get("Basis")

    raw_bytes = bytearray()
    for v in mesh.vertices:
        position = basis_key.data[v.index].co if basis_key is not None else v.co
        comps = [position.x, position.y, position.z]
        if comp_count >= 6:
            if custom_normals is not None:
                n = custom_normals[v.index]
                comps += [n.x, n.y, n.z]
            else:
                comps += [v.normal.x, v.normal.y, v.normal.z]
        component_names = ('x', 'y', 'z', 'nx', 'ny', 'nz')
        for component_index, val in enumerate(comps):
            if is_float:
                if not math.isfinite(float(val)):
                    raise ValueError(
                        f"{obj.name} vertex {v.index} {component_names[component_index]}: "
                        f"coordinate/component is not finite ({val})"
                    )
                raw_bytes += struct.pack('>f', val)
            else:
                raw_bytes += _pack_quantized_component(
                    val,
                    divisor,
                    f"{obj.name} vertex {v.index} {component_names[component_index]}",
                )

    return _from_bytes(bytes(raw_bytes), use_base64)


def _facial_vertex_indices(attribute):
    indices = []
    for run in attribute.get("Runs", []):
        first = run.get("FirstVertex", 0)
        count = run.get("VertexCount", 0)
        indices.extend(range(first, first + count))
    return indices


def _encode_facial_key(
    key, pose_zero_data, source_pose_data, indices, pose_index, quant_info,
    component_count, component_size,
):
    if component_size != 2 or component_count < 3:
        return None
    divisor = 1 << (quant_info & 0xF)
    stride = component_count * component_size
    pose_zero_raw = _to_bytes(pose_zero_data)
    raw = bytearray(_to_bytes(source_pose_data))
    if len(raw) != len(indices) * stride or len(pose_zero_raw) != len(indices) * stride:
        return None
    for mapped_index, vertex_index in enumerate(indices):
        coordinate = key.data[vertex_index].co
        if pose_index == 0:
            values = coordinate
        else:
            imported_pose_zero = struct.unpack_from(
                '>3h', pose_zero_raw, mapped_index * stride
            )
            values = tuple(
                coordinate[axis] - imported_pose_zero[axis] / divisor
                for axis in range(3)
            )
        for component, value in enumerate(values):
            quantized = max(-32768, min(32767, round(value * divisor)))
            struct.pack_into(
                '>h', raw, mapped_index * stride + component * component_size,
                quantized,
            )
    return bytes(raw)


def encode_facial_pose_edits(
    obj, facial_data, submesh_index, use_base64=True, facial_edited=None
):
    """Return sparse position-pose edits for one submesh's Blender shape keys."""
    if not facial_data or not obj.data.shape_keys:
        return []
    key_blocks = obj.data.shape_keys.key_blocks
    basis_key = key_blocks.get("Basis")
    if basis_key is None:
        return []
    quant_info = obj.get("VertexBufferQuantizeInfo", 0)
    edited_lookup = {
        (facial_object.get("ObjectIndex"), pose.get("PoseIndex")): pose.get("PoseData")
        for facial_object in (facial_edited or {}).get("Objects", [])
        for pose in facial_object.get("PositionPoseEdits", [])
    }
    object_edits = []

    for facial_object in facial_data.get("Objects", []):
        if facial_object.get("SubmeshIndex") != submesh_index:
            continue
        position = facial_object.get("Position", {})
        indices = _facial_vertex_indices(position)
        if not indices or max(indices) >= len(obj.data.vertices):
            continue
        original_poses = position.get("PoseData", [])
        object_index = facial_object.get("ObjectIndex")
        pose_count = facial_object.get("PoseCount", facial_data.get("PoseCount", 0))
        pose_zero_data = edited_lookup.get(
            (object_index, 0), original_poses[0] if original_poses else None
        )
        if pose_zero_data is None:
            continue
        pose_edits = []
        for pose_index in range(pose_count):
            key = basis_key if pose_index == 0 else key_blocks.get(
                f"facial_object_{facial_object.get('ObjectIndex')}_pose_{pose_index}"
            )
            if key is None or pose_index >= len(original_poses):
                continue
            source_pose_data = edited_lookup.get(
                (object_index, pose_index), original_poses[pose_index]
            )
            edited_raw = _encode_facial_key(
                key, pose_zero_data, source_pose_data, indices, pose_index,
                quant_info, position.get("ComponentCount", 3),
                position.get("ComponentSize", 2),
            )
            if edited_raw is None:
                continue
            if edited_raw != _to_bytes(original_poses[pose_index]):
                pose_edits.append({
                    "PoseIndex": pose_index,
                    "PoseData": _from_bytes(edited_raw, use_base64),
                })
        if pose_edits:
            object_edits.append({
                "ObjectIndex": facial_object.get("ObjectIndex"),
                "PositionPoseEdits": pose_edits,
            })
    return object_edits


def update_facial_pose_edits(candidates, data, warnings):
    """Replace edits for selected facial submeshes and preserve unselected edits."""
    model = data.get("SluggiesModel", {})
    facial_data = model.get("FacialPoseData")
    if not facial_data:
        model.pop("FacialPoseDataEdited", None)
        return

    submeshes = model.get("Submeshes", [])
    use_base64 = model.get("UseBase64", True)
    selected = {}
    for obj in candidates:
        submesh_index = next((
            index for index, submesh in enumerate(submeshes)
            if str(obj.get("VertexBufferOffset"))
            == str(submesh.get("VertexBuffer", {}).get("VertexBufferOffset"))
        ), None)
        if submesh_index is not None:
            selected[submesh_index] = obj

    selected_object_indices = {
        entry.get("ObjectIndex") for entry in facial_data.get("Objects", [])
        if entry.get("SubmeshIndex") in selected
    }
    retained = [
        entry for entry in model.get("FacialPoseDataEdited", {}).get("Objects", [])
        if entry.get("ObjectIndex") not in selected_object_indices
    ]
    generated = []
    for submesh_index, obj in selected.items():
        if any(entry.get("SubmeshIndex") == submesh_index
               for entry in facial_data.get("Objects", [])):
            if not obj.data.shape_keys:
                warnings.append(
                    f"{obj.name}: facial shape keys are missing; existing facial edits cleared."
                )
            generated.extend(encode_facial_pose_edits(
                obj, facial_data, submesh_index, use_base64,
                model.get("FacialPoseDataEdited"),
            ))

    edits = retained + generated
    if edits:
        model["FacialPoseDataEdited"] = {"Objects": edits}
    else:
        model.pop("FacialPoseDataEdited", None)


def _find_root_scale_armature(candidates, context):
    """Return the imported armature object that carries the ``RootBoneScale``
    custom property, or None.

    The importer parents every skinned mesh to the armature, so the primary
    lookup walks up each candidate's parent chain. A scene-wide fallback covers
    the edge case where the mesh was reparented or the armature is simply
    selected without its meshes.
    """
    for obj in candidates:
        node = obj
        while node is not None:
            if node.type == 'ARMATURE' and "RootBoneScale" in node:
                return node
            node = node.parent
    for obj in context.scene.objects:
        if obj.type == 'ARMATURE' and "RootBoneScale" in obj:
            return obj
    return None


def encode_root_bone_scale_edited(candidates, data, warnings, context):
    """Read the armature's ``RootBoneScale`` and, when it differs from the
    unit scale [1, 1, 1], write it to ``SluggiesModel.RootBoneScaleEdited``.

    The field is popped when the scale is unchanged so an untouched import
    never writes it back. Returns the written 3-element scale list, or None
    when nothing was written (no armature found, or scale already unit).
    """
    model = data.get("SluggiesModel", {})
    arm_obj = _find_root_scale_armature(candidates, context)
    if arm_obj is None:
        model.pop("RootBoneScaleEdited", None)
        return None

    scale = arm_obj["RootBoneScale"]
    if scale is None or len(scale) != 3:
        warnings.append(
            f"{arm_obj.name}: RootBoneScale must be a 3-element vector; "
            "root-bone scale export skipped."
        )
        model.pop("RootBoneScaleEdited", None)
        return None

    value = [float(scale[0]), float(scale[1]), float(scale[2])]
    if value == [1.0, 1.0, 1.0]:
        model.pop("RootBoneScaleEdited", None)
        return None

    model["RootBoneScaleEdited"] = value
    return value


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
    for slot_index, (qs, qt) in enumerate(output_slots):
        comps = [qs, qt] + [0.0] * (comp_count - 2)
        for component_index, val in enumerate(comps):
            if is_float:
                if not math.isfinite(float(val)):
                    raise ValueError(
                        f"{obj.name} UV{ch_ind} slot {slot_index} component "
                        f"{component_index}: value is not finite ({val})"
                    )
                raw_bytes += struct.pack('>f', float(val))
            else:
                raw_bytes += _pack_quantized_component(
                    int(val) / divisor,
                    divisor,
                    f"{obj.name} UV{ch_ind} slot {slot_index} component {component_index}",
                )

    return _from_bytes(bytes(raw_bytes), use_base64), conflicts


def _per_loop_normals(mesh, loop_indices):
    """Per-loop normals for *loop_indices*.

    Uses Blender loop normals (custom split normals when the mesh has them,
    geometric face normals otherwise). Loops with a degenerate (zero-length)
    custom normal — e.g. new loops created by mesh edits before the user
    re-applies smooth shading — fall back to the face normal so something
    usable is always exported.
    """
    result = []
    for loop_idx in loop_indices:
        n = mesh.loops[loop_idx].normal
        if n.length_squared < 1e-8:
            n = mesh.polygons[mesh.loops[loop_idx].polygon_index].normal
        result.append((n[0], n[1], n[2]))
    return result


def encode_normal_edits(obj, json_normal_buffer, loop_indices, use_base64=True):
    """Export per-loop edited normals aligned with FacesDataEdited (plan 3.3, item 1).

    Mirrors the per-loop UV contract of item 3.2: one quantized normal per loop
    in FacesDataEdited loop order (no deduplication) plus an identity per-loop
    index buffer. The patcher compacts the buffer like UVChannelDataEdited.

    Records may declare more components than the three normal axes (CompCount 6
    interleaves [NX, NY, NZ, X, Y, Z]). Blender only carries the normal, so the
    trailing components are copied verbatim from the donor record each loop
    referenced. Returns None when that donor mapping is unavailable.
    """
    normal_data = bytearray()
    comp = json_normal_buffer.get("NormalBufferCompCount", 3)
    quant = json_normal_buffer.get("NormalBufferQuantizeInfo", 0)
    is_float = (quant >> 4) in [4, 7, 0xa]
    divisor = 1 << (quant & 0xF)
    comp_size = 4 if is_float else 2
    stride = comp * comp_size

    donor_tails = None
    if comp > 3:
        donor_raw = _to_bytes(json_normal_buffer.get("NormalBufferData"))
        donor_faces = json_normal_buffer.get("NormalFacesData")
        if not donor_raw or donor_faces is None or len(donor_raw) % stride:
            return None
        donor_indices = list(struct.unpack(
            f'>{len(_to_bytes(donor_faces)) // 2}H', _to_bytes(donor_faces)))
        # Trailing components can only be preserved with an unchanged loop layout.
        if len(donor_indices) != len(loop_indices):
            return None
        donor_count = len(donor_raw) // stride
        if any(index >= donor_count for index in donor_indices):
            return None
        tail_offset = 3 * comp_size
        donor_tails = [
            donor_raw[index * stride + tail_offset:(index + 1) * stride]
            for index in donor_indices
        ]

    for loop_position, n in enumerate(_per_loop_normals(obj.data, loop_indices)):
        for val in n[:comp]:
            if is_float:
                if not math.isfinite(float(val)):
                    raise ValueError(
                        f"{obj.name}: per-loop normal component is not finite ({val})"
                    )
                normal_data += struct.pack('>f', val)
            else:
                normal_data += _pack_quantized_component(
                    val, divisor, f"{obj.name} per-loop normal component"
                )
        if donor_tails is not None:
            normal_data += donor_tails[loop_position]

    if len(normal_data) != len(loop_indices) * stride:
        return None
    normal_faces = _from_bytes(
        struct.pack(f'>{len(loop_indices)}H', *range(len(loop_indices))), use_base64
    )
    return _from_bytes(bytes(normal_data), use_base64), normal_faces


def _encode_color_entry(quant_info, rgba):
    """Encode one 0..1 (r, g, b, a) color into the big-endian entry layout that
    decode_color_channel (ImportSluggies) decodes for the channel's format."""
    fmt = quant_info >> 4
    r, g, b, a = (max(0.0, min(1.0, c)) for c in rgba)
    if fmt == 0:  # RGB565
        value = ((int(round(r * 31)) & 0x1F) << 11) \
              | ((int(round(g * 63)) & 0x3F) << 5) \
              | (int(round(b * 31)) & 0x1F)
        return value.to_bytes(2, 'big')
    if fmt == 3:  # RGBA4444
        value = ((int(round(r * 15)) & 0xF) << 12) \
              | ((int(round(g * 15)) & 0xF) << 8) \
              | ((int(round(b * 15)) & 0xF) << 4) \
              | (int(round(a * 15)) & 0xF)
        return value.to_bytes(2, 'big')
    if fmt in (1, 4):  # RGB8
        return bytes((int(round(r * 255)), int(round(g * 255)), int(round(b * 255))))
    if fmt in (2, 5):  # RGBA8
        return bytes((int(round(r * 255)), int(round(g * 255)),
                      int(round(b * 255)), int(round(a * 255))))
    raise ValueError(f"unsupported color channel format {fmt} (quantizeInfo=0x{quant_info:X})")


def encode_color_edits(obj, json_channel, loop_indices, use_base64=True):
    """Export per-loop edited colors for one ColorChannels entry (plan 3.3, item 1).

    Reads the CORNER-domain color attribute the importer created ('color0' /
    'color1'), one entry per loop in FacesDataEdited loop order, plus an
    identity per-loop index buffer — the same per-loop contract as
    UVFacesDataEdited. Returns None when the attribute does not exist.
    """
    mesh = obj.data
    ch_idx = json_channel.get("ColorChannelIndex", 0)
    attr = next((a for a in mesh.color_attributes if a.name == f"color{ch_idx}"), None)
    if attr is None:
        return None
    quant = json_channel.get("ColorChannelQuantizeInfo", 0)
    color_data = bytearray()
    for loop_idx in loop_indices:
        color_data += _encode_color_entry(quant, attr.data[loop_idx].color)
    color_faces = _from_bytes(
        struct.pack(f'>{len(loop_indices)}H', *range(len(loop_indices))), use_base64
    )
    return _from_bytes(bytes(color_data), use_base64), color_faces


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
      'NormalEdits': (normal_data_b64, normal_faces_b64) or None — per-loop normals
                      when the submesh has a standalone NormalBuffer (plan 3.3)
      'ColorEdits': {ch_ind: (color_data_b64, color_faces_b64), ...} — per-loop
                     colors for channels whose color attribute exists (plan 3.3)
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
    # Prefer mat["TextureIndex"] (set by 2.2 import); fall back to name parsing.
    mat_to_tex: dict[int, int] = {}
    for slot_idx, slot in enumerate(obj.material_slots):
        tex_idx = 0
        if slot.material is not None:
            tex_prop = slot.material.get("TextureIndex")
            if tex_prop is not None:
                tex_idx = int(tex_prop)
            else:
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

        # Per-loop UV storage: each face-corner gets its own UV entry.
        # This avoids value-based deduplication which can lose entries when
        # the original game data contains duplicate quantized UV values at
        # different array indices (the game's original tools kept them
        # separate; merging them changes array length and breaks patching).
        coords = []
        uv_tri_indices = []
        for tri in triangles:
            tri_uvs = []
            for loop_idx in tri.loops:
                uv = uv_layer.data[loop_idx].uv
                s = uv.x
                t = 1.0 - uv.y  # undo Blender V-flip applied on import
                if is_float:
                    qs, qt = float(s), float(t)
                else:
                    qs = int(round(s * divisor))
                    qt = int(round(t * divisor))
                tri_uvs.append(len(coords))
                coords.append((qs, qt))
            uv_tri_indices.append(tri_uvs)

        raw_bytes = bytearray()
        for coord_index, (qs, qt) in enumerate(coords):
            for component_index, val in enumerate(
                [qs, qt] + [0.0] * (comp_count_uv - 2)
            ):
                if is_float:
                    if not math.isfinite(float(val)):
                        raise ValueError(
                            f"{obj.name} UV{ch_ind} loop {coord_index} component "
                            f"{component_index}: value is not finite ({val})"
                        )
                    raw_bytes += struct.pack('>f', float(val))
                else:
                    raw_bytes += _pack_quantized_component(
                        int(val) / divisor,
                        divisor,
                        f"{obj.name} UV{ch_ind} loop {coord_index} component {component_index}",
                    )
        uv_data_b64 = _from_bytes(bytes(raw_bytes), use_base64)

        uv_flat = [idx for tri in uv_tri_indices for idx in tri]
        uv_faces_b64 = _from_bytes(struct.pack(f'>{len(uv_flat)}H', *uv_flat), use_base64)

        uv_edits[ch_ind] = (uv_data_b64, uv_faces_b64)

    # Per-loop normals (standalone NormalBuffer, non-skinned meshes) and per-loop
    # colors — same expanded per-loop contract as the UV edits above (plan 3.3).
    loop_indices = [loop_idx for tri in triangles for loop_idx in tri.loops]

    normal_edits = None
    normal_buffer = json_submesh.get("NormalBuffer")
    # Normals are only rewritten when the user opted in, matching in-place mode.
    if use_custom_normals and isinstance(normal_buffer, dict) and "NormalBufferData" in normal_buffer:
        normal_edits = encode_normal_edits(obj, normal_buffer, loop_indices, use_base64)

    color_edits = {}
    for json_channel in json_submesh.get("ColorChannels", []):
        ch_ind = json_channel.get("ColorChannelIndex", 0)
        encoded = encode_color_edits(obj, json_channel, loop_indices, use_base64)
        if encoded is not None:
            color_edits[ch_ind] = encoded

    return {
        'VertexBufferDataEdited':   vb_data,
        'FacesDataEdited':          faces_data,
        'FacesCountEdited':         len(triangles),
        'FaceTextureIndicesEdited': face_tex_data,
        'UVEdits':                  uv_edits,
        'NormalEdits':              normal_edits,
        'ColorEdits':               color_edits,
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


def _parse_bone_group_name(name):
    """Return bone id from Blender vertex-group name bone_<id>, else None."""
    if not isinstance(name, str) or not name.startswith("bone_"):
        return None
    try:
        return int(name[5:])
    except ValueError:
        return None


def _detect_uniform_vertex_bone_id(obj):
    """Return a single bone id when all vertices map to exactly one bone_<id>."""
    uniform_id = None
    for vd in obj.data.vertices:
        ids_here = set()
        for g in vd.groups:
            if g.weight <= 0:
                continue
            vg_name = obj.vertex_groups[g.group].name
            parsed = _parse_bone_group_name(vg_name)
            if parsed is not None:
                ids_here.add(parsed)
        if len(ids_here) != 1:
            return None
        v_id = next(iter(ids_here))
        if uniform_id is None:
            uniform_id = v_id
        elif uniform_id != v_id:
            return None
    return uniform_id


def encode_unskinned_bone_reassignments(candidates, data, warnings):
    """Write BoneHierarchy.GeoIdEdited when a non-skinned submesh is retargeted.

    Detection rule: for a non-skinned submesh object, every vertex must belong
    to exactly one positive-weight bone_<id> group, and that id differs from
    the current owning bone id.
    """
    model = data.get("SluggiesModel", {})
    bone_list = model.get("BoneHierarchy")
    submeshes = model.get("Submeshes", [])
    if not bone_list or not submeshes:
        return False

    for bd in bone_list:
        bd.pop("GeoIdEdited", None)

    submesh_by_vb = {}
    for i, sm in enumerate(submeshes):
        vb = sm.get("VertexBuffer")
        if vb and "VertexBufferOffset" in vb:
            submesh_by_vb[str(vb["VertexBufferOffset"])] = i

    obj_by_submesh = {}
    for obj in candidates:
        if "VertexBufferOffset" not in obj:
            continue
        sub_idx = submesh_by_vb.get(str(obj["VertexBufferOffset"]))
        if sub_idx is not None:
            obj_by_submesh[sub_idx] = obj

    bone_by_id = {int(bd["BoneId"]): bd for bd in bone_list if "BoneId" in bd}
    owner_by_submesh = {
        int(bd["GeoId"]): bd
        for bd in bone_list
        if (not bd.get("Skinned")) and bd.get("GeoId") is not None and int(bd.get("GeoId", -1)) >= 0
    }

    def _geo_raw(bd):
        if bd.get("GeoIdEdited") is not None:
            return int(bd["GeoIdEdited"])
        if bd.get("GeoIdRaw") is not None:
            return int(bd["GeoIdRaw"])
        if bd.get("Skinned"):
            return 0xFFFF
        return int(bd.get("GeoId", 0xFFFF))

    wrote_any = False
    target_claims = {}

    for sub_idx, obj in obj_by_submesh.items():
        owner = owner_by_submesh.get(sub_idx)
        if owner is None:
            continue

        target_bone_id = _detect_uniform_vertex_bone_id(obj)
        if target_bone_id is None:
            continue

        from_bone_id = int(owner["BoneId"])
        if target_bone_id == from_bone_id:
            continue

        target = bone_by_id.get(target_bone_id)
        if target is None:
            warnings.append(
                f"{obj.name}: target group bone_{target_bone_id} is not present in BoneHierarchy; "
                f"non-skinned reassignment skipped."
            )
            continue

        if target_bone_id in target_claims and target_claims[target_bone_id] != sub_idx:
            warnings.append(
                f"{obj.name}: bone_{target_bone_id} is already requested by submesh "
                f"{target_claims[target_bone_id]}; one non-skinned bone can own only one GeoId."
            )
            continue

        target_geo_raw = _geo_raw(target)
        if target_geo_raw not in (0xFFFF, sub_idx):
            warnings.append(
                f"{obj.name}: bone_{target_bone_id} currently owns submesh {target_geo_raw}; "
                f"reassignment skipped to avoid GeoId collision."
            )
            continue

        owner["GeoIdEdited"] = 0xFFFF
        target["GeoIdEdited"] = sub_idx
        target_claims[target_bone_id] = sub_idx
        wrote_any = True

    return wrote_any


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

    skinned_bone_ids = set()
    for _e in skin_data.get("SK1s", []):
        skinned_bone_ids.add(_e["BoneIndex"])
    for _e in skin_data.get("SK2s", []):
        skinned_bone_ids.add(_e["BoneIndex1"])
        skinned_bone_ids.add(_e["BoneIndex2"])
    for _e in skin_data.get("SKAccs", []):
        skinned_bone_ids.add(_e["BoneIndex"])

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

    def pack_val(v, context):
        if is_float:
            if not math.isfinite(float(v)):
                raise ValueError(f"{context}: coordinate/component is not finite ({v})")
            return struct.pack('>f', float(v))
        return _pack_quantized_component(v, divisor, context)

    def encode_vertex(obj, local_v):
        vd  = obj.data.vertices[local_v]
        _cn = custom_normals_cache.get(str(obj["VertexBufferOffset"]))
        nx, ny, nz = (
            (_cn[local_v].x, _cn[local_v].y, _cn[local_v].z)
            if _cn is not None
            else (vd.normal.x, vd.normal.y, vd.normal.z)
        )
        buf = bytearray()
        for component, val in zip(
            ('x', 'y', 'z', 'nx', 'ny', 'nz'),
            (vd.co.x, vd.co.y, vd.co.z, nx, ny, nz),
        ):
            buf.extend(pack_val(val, f"{obj.name} vertex {local_v} {component}"))
        return bytes(buf)

    wrote_any = False

    # --- SK1 entries (source data only, no weights) ---
    # Re-encode in dest-slot order (position i → dest slot gplBase + vtx_off + i).
    # Trailing vertices with weight=0 are trimmed; middle-removed ones keep
    # their original source bytes so subsequent kept vertices stay at the
    # correct dest slot.
    for sk1 in skin_data.get("SK1s", []):
        bone_id      = sk1["BoneIndex"]
        n            = sk1["VertexCnt"]
        vtx_off      = sk1.get("VertexOffset", 0)
        global_start = (sk1["GplVertexArrValue"] + vtx_off) // vertex_size
        orig_src     = _to_bytes(sk1["BindPoseData"])

        # Detect full SK1 reassignment (all vertices moved from bone_A to one
        # other skinned bone via vertex-group rename/transfer).
        reassigned_bone = None
        seen_any = False
        unresolved = False
        candidate_ids = set()
        any_orig_weight = False
        for i in range(n):
            obj, local_v = resolve(global_start + i)
            if obj is None:
                unresolved = True
                continue
            seen_any = True
            if _get_vgroup_weight(obj, f"bone_{bone_id}", local_v) > 0:
                any_orig_weight = True

            ids_here = set()
            for g in obj.data.vertices[local_v].groups:
                if g.weight <= 0:
                    continue
                vg_name = obj.vertex_groups[g.group].name
                parsed = _parse_bone_group_name(vg_name)
                if parsed is not None and parsed in skinned_bone_ids:
                    ids_here.add(parsed)
            if len(ids_here) != 1:
                candidate_ids = set()
                break
            candidate_ids |= ids_here

        if seen_any and (not unresolved) and (not any_orig_weight) and len(candidate_ids) == 1:
            only_id = next(iter(candidate_ids))
            if only_id != bone_id:
                reassigned_bone = only_id

        target_bone_id = reassigned_bone if reassigned_bone is not None else bone_id
        target_bone_name = f"bone_{target_bone_id}"
        if reassigned_bone is not None:
            sk1["BoneIndexEdited"] = target_bone_id
        else:
            sk1.pop("BoneIndexEdited", None)

        last_kept  = -1
        slot_bytes = []
        for i in range(n):
            obj, local_v = resolve(global_start + i)
            if obj is None:
                # Vertex outside imported submeshes — treat as kept, preserve.
                slot_bytes.append(orig_src[vtx_off + i * vertex_size : vtx_off + (i + 1) * vertex_size])
                last_kept = i
            else:
                if _get_vgroup_weight(obj, target_bone_name, local_v) > 0:
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
            for k in ('BindPoseDataEdited', 'VertexCntEdited', 'BoneIndexEdited'):
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
    Writes 'SkinDataEdited' at the model level.  Returns (ok, message).
    """
    skin_data = data["SluggiesModel"].get("SkinData")
    if not skin_data:
        return True, None   # unskinned model — nothing to encode, not an error

    submeshes  = data["SluggiesModel"].get("Submeshes", [])
    quant_info = skin_data["QuantizeInfo"]
    use_base64 = data["SluggiesModel"].get("UseBase64", True)
    cs         = _comp_size_skin(quant_info)
    fmt_nibble = quant_info >> 4
    is_float   = fmt_nibble in [4, 7, 0xa]
    divisor    = 1 << (quant_info & 0xF)

    def pack_val(v, context):
        if is_float:
            if not math.isfinite(float(v)):
                raise ValueError(f"{context}: coordinate/component is not finite ({v})")
            return struct.pack('>f', float(v))
        return _pack_quantized_component(v, divisor, context)

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

    # --- Per-vertex SK classification maps from original SkinData ---
    # These allow definitive classification of each vertex without ambiguity.
    # The actual vertex DATA (positions, normals, weights) is still computed
    # from Blender; only the classification decision uses original metadata.
    _vertex_stride = 6 * cs  # 6 components (xyz + nxnynz) * component size

    # Compute cumulative vertex starts per submesh (for global ↔ local mapping)
    _sub_vtx_starts = []
    _sub_vtx_counts = []
    _cumulative = 0
    for _sm in submeshes:
        _vb = _sm["VertexBuffer"]
        _vb_cs = _comp_size_skin(_vb["VertexBufferQuantizeInfo"])
        _vtx_count = _vb["VertexBufferLength"] // (_vb["VertexBufferCompCount"] * _vb_cs)
        _sub_vtx_starts.append(_cumulative)
        _sub_vtx_counts.append(_vtx_count)
        _cumulative += _vtx_count

    # Maps target the original ENTRY INDEX, not the bone id: a bone may own
    # several SK entries (different gplVertexArr destinations) and merging them
    # would drop entries and corrupt the destination offsets.
    # SK1: global_vertex_idx → entry index (contiguous ranges from GplVertexArrValue)
    _sk1_vertex_map: dict[int, int] = {}
    for _idx, _e in enumerate(skin_data.get('SK1s', [])):
        _first = (_e['GplVertexArrValue'] + _e.get('VertexOffset', 0)) // _vertex_stride
        for _i in range(_e['VertexCnt']):
            _sk1_vertex_map[_first + _i] = _idx

    # SK2: global_vertex_idx → entry index (contiguous ranges)
    _sk2_vertex_map: dict[int, int] = {}
    for _idx, _e in enumerate(skin_data.get('SK2s', [])):
        _first = (_e['GplVertexArrValue'] + _e.get('VertexOffset', 0)) // _vertex_stride
        for _i in range(_e['VertexCnt']):
            _sk2_vertex_map[_first + _i] = _idx

    # Fallback targets for vertices outside every SK1/SK2 source range.
    _first_sk1_entry_by_bone: dict[int, int] = {}
    for _idx, _e in enumerate(skin_data.get('SK1s', [])):
        _first_sk1_entry_by_bone.setdefault(_e['BoneIndex'], _idx)

    # Pre-compute custom split normals per object when requested
    custom_normals_cache = {}
    if use_custom_normals:
        for _obj_id, (_, _obj) in obj_to_sub.items():
            _cn = _get_custom_split_normals(_obj)
            if _cn is not None:
                custom_normals_cache[_obj_id] = _cn

    sk1_groups  = {}   # SK1 entry index → [(sub_idx, local_v, obj)]
    sk2_groups  = {}   # SK2 entry index → [(sub_idx, local_v, w_lo, w_hi, obj)]
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
            global_idx = _sub_vtx_starts[sub_idx] + v_idx

            # --- Definitive per-vertex classification using original maps ---
            if global_idx in _sk2_vertex_map:
                # Vertex was originally in an SK2 entry — classify as SK2.
                sk2_entry_index = _sk2_vertex_map[global_idx]
                _sk2_entry = skin_data['SK2s'][sk2_entry_index]
                b_lo = min(_sk2_entry['BoneIndex1'], _sk2_entry['BoneIndex2'])
                b_hi = max(_sk2_entry['BoneIndex1'], _sk2_entry['BoneIndex2'])
                # Extract weights for the pair bones from Blender groups
                w_lo = 0.0
                w_hi = 0.0
                for b, w in parsed:
                    if b == b_lo:
                        w_lo = w
                    elif b == b_hi:
                        w_hi = w
                sk2_groups.setdefault(sk2_entry_index, []).append(
                    (sub_idx, v_idx, w_lo, w_hi, obj))
                # Any other bones with groups → SKAcc overlay
                for b, w in parsed:
                    if b != b_lo and b != b_hi and b in original_skacc_bone_ids:
                        skacc_groups.setdefault(b, []).append(
                            (sub_idx, v_idx, w, v_idx, obj))

            elif global_idx in _sk1_vertex_map:
                # Vertex was originally in an SK1 entry — classify as SK1.
                sk1_entry_index = _sk1_vertex_map[global_idx]
                sk1_bone = skin_data['SK1s'][sk1_entry_index]['BoneIndex']
                sk1_groups.setdefault(sk1_entry_index, []).append((sub_idx, v_idx, obj))
                # Any other bones with groups → SKAcc overlay
                for b, w in parsed:
                    if b != sk1_bone and b in original_skacc_bone_ids:
                        skacc_groups.setdefault(b, []).append(
                            (sub_idx, v_idx, w, v_idx, obj))

            else:
                # Vertex not in any SK1/SK2 source range — it's only an SKAcc
                # destination, or a newly added vertex (future editing support).
                # All influences go to SKAcc if the bone is a known SKAcc bone;
                # otherwise fall back to SK1 for known SK1 bones.
                for b, w in parsed:
                    if b in original_skacc_bone_ids:
                        skacc_groups.setdefault(b, []).append(
                            (sub_idx, v_idx, w, v_idx, obj))
                    elif b in original_sk1_bone_ids:
                        sk1_groups.setdefault(
                            _first_sk1_entry_by_bone[b], []).append(
                                (sub_idx, v_idx, obj))

    # A Blender vertex group holds only one weight per (vertex, bone), so donor
    # SKAcc entries that accumulate twice onto the same dest slot cannot survive
    # the round trip.  Rebuild those entries from the donor dest/weight arrays,
    # taking only the bind-pose source from Blender.
    _obj_by_sub = {sub_idx: obj for _, (sub_idx, obj) in obj_to_sub.items()}

    def _resolve_global_vertex(global_idx):
        for _sub_idx, _start in enumerate(_sub_vtx_starts):
            if _start <= global_idx < _start + _sub_vtx_counts[_sub_idx]:
                _obj = _obj_by_sub.get(_sub_idx)
                if _obj is None:
                    return None
                _local = global_idx - _start
                if _local >= len(_obj.data.vertices):
                    return None
                return _sub_idx, _local, _obj
        return None

    for _e in skin_data.get('SKAccs', []):
        _n = _e['VertexCnt']
        if _n == 0:
            continue
        _dests = list(struct.unpack(f'>{_n}H', _to_bytes(_e['DestIndexData'])))
        if len(set(_dests)) == _n:
            continue
        _weights = _to_bytes(_e['WeightData'])
        _dest_base = _e.get('GplDestArrValue', 0) // _vertex_stride
        _rebuilt = []
        for _di, _w in zip(_dests, _weights):
            _loc = _resolve_global_vertex(_dest_base + _di)
            if _loc is None:
                _rebuilt = None
                break
            _sub_idx, _local_v, _obj = _loc
            _rebuilt.append((_sub_idx, _local_v, _w / 256.0, _di, _obj))
        if _rebuilt is None:
            warnings.append(
                f"SKAcc bone {_e['BoneIndex']}: duplicate dest slots could not be "
                f"resolved to mesh vertices; entry left as rebuilt from Blender")
            continue
        skacc_groups[_e['BoneIndex']] = _rebuilt

    def encode_src(entries):
        raw = bytearray()
        for entry in entries:
            obj_ref = entry[-1]
            v       = obj_ref.data.vertices[entry[1]]
            _cn     = custom_normals_cache.get(id(obj_ref))
            nx, ny, nz = (_cn[v.index].x, _cn[v.index].y, _cn[v.index].z) if _cn is not None else (v.normal.x, v.normal.y, v.normal.z)
            for component, val in zip(
                ('x', 'y', 'z', 'nx', 'ny', 'nz'),
                (v.co.x, v.co.y, v.co.z, nx, ny, nz),
            ):
                raw += pack_val(
                    val,
                    f"{obj_ref.name} vertex {v.index} SKN {component}",
                )
        return _from_bytes(bytes(raw), use_base64)

    # Build lookups so gplVertexArr / gplDestArr can be carried forward.
    # The original values remain correct for the hammerspace GPL because Blender
    # preserves vertex ordering (slot i in the Blender mesh == slot i in the GPL
    # position buffer).  Without these values the runtime CPU skinning writes all
    # SK1/SK2 output on top of each other at offset 0.
    _orig_sk1s_list = skin_data.get('SK1s', [])
    _orig_sk2s_list = skin_data.get('SK2s', [])
    _orig_skacc_gda = {
        e['BoneIndex']: e.get('GplDestArrValue', 0)
        for e in skin_data.get('SKAccs', [])
    }

    new_sk1s = []
    for entry_index, entries in sorted(sk1_groups.items()):
        orig = _orig_sk1s_list[entry_index]
        new_sk1s.append({
            "BoneIndex": orig['BoneIndex'],
            "VertexCnt": len(entries),
            "BindPoseData": encode_src(entries),
            "GplVertexArrValue": orig.get('GplVertexArrValue', 0),
        })

    new_sk2s = []
    for entry_index, entries in sorted(sk2_groups.items()):
        orig = _orig_sk2s_list[entry_index]
        b_lo = min(orig['BoneIndex1'], orig['BoneIndex2'])
        b_hi = max(orig['BoneIndex1'], orig['BoneIndex2'])
        wt = bytearray()
        for e in entries:
            wt.append(max(0, min(255, round(e[2] * 256))))
            wt.append(max(0, min(255, round(e[3] * 256))))
        new_sk2s.append({
            "BoneIndex1": b_lo, "BoneIndex2": b_hi,
            "VertexCnt": len(entries),
            "BindPoseData": encode_src(entries),
            "WeightData": _from_bytes(bytes(wt), use_base64),
            "GplVertexArrValue": orig.get('GplVertexArrValue', 0),
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
        # Size growth is fine in hammerspace mode — the block is written to the
        # extended region of the dat file with no in-place size constraint.
        # (The hard overflow guard only applies to in-place mode, which uses
        # encode_skin_weights_inplace instead of this function.)
        overflow = edit_size - orig_size
        candidate_edited["FlushIndSize"] = flush_ind_size
        data["SluggiesModel"]["SkinDataEdited"] = candidate_edited
        msg = (
            f"SKN block grew by {overflow} B (hammerspace accommodates this).\n"
            f"  Original: {_sk_stats(len(orig_sk1s), orig_v1, len(orig_sk2s), orig_v2, len(orig_skaccs), orig_v3, orig_size)}\n"
            f"  Edited:   {_sk_stats(len(new_sk1s), edit_v1, len(new_sk2s), edit_v2, len(new_skaccs), edit_v3, edit_size)}"
        )
        return True, msg

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
    sk1_keys   = ("BindPoseDataEdited", "VertexCntEdited", "BoneIndexEdited")
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


def _find_new_materials(obj, json_submesh):
    """Return a list of (material_name, reason) for materials that are NOT donor surfaces.

    A material is a valid donor surface when:
      - new path (2.2+): it carries a SurfaceId that exists in the donor's display states, or
      - legacy path: its name ends with ``_mat{tex_idx}`` where tex_idx is a texture index
        present in the donor's FaceTextureIndices.

    Any other material (no SurfaceId, unknown SurfaceId, or unparseable legacy name)
    is a newly created surface and must be rejected for the MVP (plan step 2.4).
    """
    display_states = json_submesh.get("DisplayStates", [])
    donor_sids = {
        ds.get("SurfaceId") for ds in display_states if ds.get("SurfaceId")
    }

    # Legacy donor texture indices (old exports without SurfaceId/FaceCount).
    # FaceTextureIndices is a base64 uint16 BE array, one texture index per face.
    donor_tex_idxs = set()
    fti_raw = json_submesh.get("FaceTextureIndicesEdited") or json_submesh.get("FaceTextureIndices")
    if fti_raw:
        try:
            raw = _to_bytes(fti_raw)
            n = len(raw) // 2
            donor_tex_idxs = set(struct.unpack(f'>{n}H', raw))
        except Exception:
            donor_tex_idxs = set()

    new_materials = []
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        sid = mat.get("SurfaceId")
        if sid:
            if sid not in donor_sids:
                new_materials.append((mat.name, f"SurfaceId '{sid}' is not a donor surface"))
            continue
        # No SurfaceId: legacy material — must be named '{...}_mat{tex_idx}' with a
        # donor texture index.
        try:
            tex_idx = int(mat.name.rsplit('_mat', 1)[1])
        except (IndexError, ValueError):
            new_materials.append((mat.name, "no SurfaceId and not a donor material name"))
            continue
        if donor_tex_idxs and tex_idx not in donor_tex_idxs:
            new_materials.append((mat.name, f"texture index {tex_idx} is not a donor texture"))
    return new_materials


# Runtime testing found that even byte-minimal donor-surface reassignment can
# corrupt rendering. Keep the schema encoder available for future format probes,
# but do not allow Blender exports to activate the patcher path by default.
ENABLE_MATERIAL_REASSIGNMENT_EXPORT = False


def _encode_face_surface_assignment(obj, display_states, surf_mat, use_base64, warnings):
    """Return (data, changed) encoding which display state owns each face.

    data is a base64 uint16 BE array (one ds_idx per face) or None when unchanged.
    Returns (None, False) for old exports without FaceCount, or when face count
    differs from the original (topology change — handled by milestone 4).
    Validates duplicate, foreign, and missing SurfaceIds; appends to warnings.
    """
    # Build original face → ds_idx from FaceCount cumulative ranges
    original_ds_idx = []
    for di, ds in enumerate(display_states):
        fc = ds.get("FaceCount")
        if fc is None:
            return None, False  # Old export without FaceCount
        for _ in range(fc):
            original_ds_idx.append(di)

    if not original_ds_idx:
        return None, False

    # Donor surface IDs from the sluggie display states
    donor_sid_to_ds_idx = {
        ds.get("SurfaceId"): di
        for di, ds in enumerate(display_states)
        if ds.get("SurfaceId")
    }

    # Detect duplicate SurfaceIds across material slots
    sid_slot_first = {}
    for slot_idx, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None:
            continue
        sid = mat.get("SurfaceId")
        if not sid:
            continue
        if sid in sid_slot_first:
            warnings.append(
                f"{obj.name}: SurfaceId '{sid}' is on multiple material slots "
                f"({sid_slot_first[sid]} and {slot_idx}) — only slot "
                f"{sid_slot_first[sid]} will be used."
            )
        else:
            sid_slot_first[sid] = slot_idx
        if sid not in donor_sid_to_ds_idx:
            warnings.append(
                f"{obj.name}: material '{mat.name}' has SurfaceId '{sid}' "
                f"not found in the donor's display states — faces assigned to "
                f"it keep their original draw-state assignment."
            )

    # Build mat_slot_idx → ds_idx (first occurrence of each SurfaceId wins)
    seen_sids = set()
    mat_slot_to_ds_idx = {}
    for slot_idx, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None:
            continue
        sid = mat.get("SurfaceId")
        if sid and sid in donor_sid_to_ds_idx and sid not in seen_sids:
            mat_slot_to_ds_idx[slot_idx] = donor_sid_to_ds_idx[sid]
            seen_sids.add(sid)

    n_faces = len(obj.data.polygons)
    if n_faces != len(original_ds_idx):
        return None, False  # Topology changed — face assignment handled in milestone 4

    current_ds_idx = []
    has_unresolved = False
    for poly in obj.data.polygons:
        di = mat_slot_to_ds_idx.get(poly.material_index)
        if di is None:
            has_unresolved = True
            current_ds_idx.append(original_ds_idx[poly.index])
        else:
            current_ds_idx.append(di)

    if has_unresolved:
        warnings.append(
            f"{obj.name}: some faces have materials without a valid donor SurfaceId; "
            f"those faces keep their original draw-state assignment."
        )

    if current_ds_idx == original_ds_idx:
        return None, False

    raw = struct.pack(f'>{n_faces}H', *current_ds_idx)
    return _from_bytes(raw, use_base64), True


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
        name="Overwrite Normals",
        description=(
            "Overwrite both interleaved vertex-buffer normals (CompCount>=6) "
            "and standalone NormalBuffer arrays with Blender's current normals. "
            "Leave off to keep the original normal values unchanged."
        ),
        default=False,
    )
    reimport_textures: BoolProperty(  # type: ignore[valid-type]
        name="Reimport textures from tex folder",
        description=(
            "When patching, replace the model's existing texture payloads in "
            "dt_na.dat with the edited PNGs from the model's tex/ folder. "
            "Without Hammerspace Mode this is a strict in-place operation: "
            "no buffers are moved, resized, or added. With Hammerspace Mode "
            "the TEX section is rebuilt, so texture dimensions may change."
        ),
        default=False,
    )

    def execute(self, context):
        # NOTE: The former gate that rejected the combination of
        # reimport_textures + use_hammerspace has been removed. Hammerspace
        # now rebuilds the TEX section (BuildTEX), so texture re-import is
        # supported alongside hammerspace. The in-place patcher gate in
        # SluggiesTools/patch_inplace.py remains active.

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

            # --- Step 2.4 (MVP): reject newly created surfaces/materials ---
            # Only reassignment among imported donor surfaces is permitted.
            # Any material that is not a donor surface must abort the export.
            new_materials = _find_new_materials(obj, target_submesh)
            if new_materials:
                names = ", ".join(f"'{n}' ({r})" for n, r in new_materials)
                self.report({"ERROR"},
                    f"Error: creating new materials is currently not supported "
                    f"({obj.name}: {names}). "
                    f"Remove the new material(s) or reassign those faces to an existing donor surface."
                )
                return {"CANCELLED"}

            if self.use_hammerspace:
                try:
                    hs = encode_mesh_hammerspace(
                        obj,
                        target_submesh,
                        use_custom_normals=self.use_custom_normals,
                        use_base64=use_base64,
                    )
                except ValueError as exc:
                    self.report({"ERROR"}, f"{obj.name}: {exc}")
                    return {"CANCELLED"}
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

                # Per-loop normals (standalone NormalBuffer) and per-loop colors
                # (plan 3.3) — written only when the submesh actually has them.
                normal_buffer = target_submesh.get("NormalBuffer")
                if hs.get("NormalEdits") is not None and isinstance(normal_buffer, dict):
                    normal_data_b64, normal_faces_b64 = hs["NormalEdits"]
                    normal_buffer["NormalBufferDataEdited"] = normal_data_b64
                    normal_buffer["NormalFacesDataEdited"] = normal_faces_b64
                elif isinstance(normal_buffer, dict):
                    # Submesh has a NormalBuffer the exporter refused to encode —
                    # drop stale per-loop edits so the original NormalBuffer stays authoritative.
                    normal_buffer.pop("NormalBufferDataEdited", None)
                    normal_buffer.pop("NormalFacesDataEdited", None)
                for json_channel in target_submesh.get("ColorChannels", []):
                    ch_ind = json_channel.get("ColorChannelIndex", 0)
                    if ch_ind in hs["ColorEdits"]:
                        color_data_b64, color_faces_b64 = hs["ColorEdits"][ch_ind]
                        json_channel["ColorChannelDataEdited"] = color_data_b64
                        json_channel["ColorFacesDataEdited"] = color_faces_b64
                    else:
                        json_channel.pop("ColorChannelDataEdited", None)
                        json_channel.pop("ColorFacesDataEdited", None)
                        warnings.append(
                            f"{obj.name}: color attribute 'color{ch_ind}' not found — "
                            f"color channel {ch_ind} skipped."
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

                try:
                    edited_data = encode_vertex_buffer_edited(
                        obj,
                        obj["VertexBufferCompCount"],
                        obj["VertexBufferQuantizeInfo"],
                        use_custom_normals=self.use_custom_normals,
                        use_base64=use_base64,
                    )
                except ValueError as exc:
                    self.report({"ERROR"}, f"{obj.name}: {exc}")
                    return {"CANCELLED"}
                target_submesh["VertexBuffer"]["VertexBufferDataEdited"] = edited_data
                # Clear any stale hammerspace face data so it can't mismatch the
                # in-place vertex buffer (which must stay at the original vertex count).
                target_submesh.pop("FacesDataEdited", None)
                target_submesh.pop("FacesCountEdited", None)
                target_submesh.pop("FaceTextureIndicesEdited", None)
                inplace_normal_buffer = target_submesh.get("NormalBuffer")
                if (self.use_custom_normals
                        and isinstance(inplace_normal_buffer, dict)
                        and "NormalBufferData" in inplace_normal_buffer):
                    loop_indices = [
                        li for poly in obj.data.polygons for li in poly.loop_indices
                    ]
                    norm_data, norm_faces = encode_normal_edits(
                        obj, inplace_normal_buffer, loop_indices, use_base64
                    )
                    norm_data_raw = _to_bytes(norm_data)
                    donor_faces_raw = _to_bytes(inplace_normal_buffer["NormalFacesData"])
                    norm_comp = inplace_normal_buffer.get("NormalBufferCompCount", 3)
                    norm_quant = inplace_normal_buffer.get("NormalBufferQuantizeInfo", 0)
                    norm_stride = norm_comp * _comp_size_skin(norm_quant)
                    donor_count = len(donor_faces_raw) // 2
                    donor_indices = [
                        int.from_bytes(donor_faces_raw[k*2:k*2+2], 'big')
                        for k in range(donor_count)
                    ]
                    # Conflict check: if loops sharing a donor slot have different
                    # edited normals, compaction would grow the buffer.
                    slot_values = {}
                    normal_conflict = False
                    for slot_idx, loop_pos in zip(donor_indices, range(len(loop_indices))):
                        record = norm_data_raw[loop_pos * norm_stride:(loop_pos + 1) * norm_stride]
                        if slot_idx in slot_values:
                            if slot_values[slot_idx] != record:
                                normal_conflict = True
                                break
                        else:
                            slot_values[slot_idx] = record
                    if normal_conflict:
                        warnings.append(
                            f"{obj.name}: standalone normal buffer conflict — loops sharing "
                            f"a donor slot have different edited normals. Normal overwrite "
                            f"skipped (would exceed original buffer size). Use Hammerspace "
                            f"Mode for full normal editing support."
                        )
                        inplace_normal_buffer.pop("NormalBufferDataEdited", None)
                        inplace_normal_buffer.pop("NormalFacesDataEdited", None)
                    else:
                        inplace_normal_buffer["NormalBufferDataEdited"] = norm_data
                        inplace_normal_buffer["NormalFacesDataEdited"] = norm_faces
                elif isinstance(inplace_normal_buffer, dict):
                    inplace_normal_buffer.pop("NormalBufferDataEdited", None)
                    inplace_normal_buffer.pop("NormalFacesDataEdited", None)
                for json_channel in target_submesh.get("ColorChannels", []):
                    json_channel.pop("ColorChannelDataEdited", None)
                    json_channel.pop("ColorFacesDataEdited", None)

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
            # Prefer mat["ShaderMode"] on the surface material (new path, 2.2+).
            # Fall back to DS_{surface_id}_ShaderMode on the object (legacy path).
            surf_mat = {
                mat.get("SurfaceId"): mat
                for slot in obj.material_slots
                if (mat := slot.material) is not None and mat.get("SurfaceId")
            }
            for ds_idx, ds in enumerate(target_submesh.get("DisplayStates", [])):
                if ds.get("DisplayStateId") != 7:
                    continue
                original = ds.get("ShaderMode", "")
                surface_id = ds.get("SurfaceId") or f"ds{ds_idx}"
                mat = surf_mat.get(surface_id)
                if mat is not None:
                    new_val = str(mat.get("ShaderMode") or "")
                else:
                    prop_val = f"DS_{surface_id}_ShaderMode"
                    new_val = str(obj[prop_val]) if prop_val in obj else None
                if new_val is not None and new_val != original:
                    ds["ShaderModeEdited"] = new_val
                else:
                    ds.pop("ShaderModeEdited", None)

            # Export per-face draw-state assignment when faces have been moved.
            face_sid_data, face_sid_changed = _encode_face_surface_assignment(
                obj, target_submesh.get("DisplayStates", []),
                surf_mat, use_base64, warnings,
            )
            if face_sid_changed:
                if not ENABLE_MATERIAL_REASSIGNMENT_EXPORT:
                    self.report({"ERROR"},
                        f"{obj.name}: material reassignment is currently disabled. "
                        "Dolphin testing found unresolved runtime corruption even "
                        "for complete moves between compatible donor surfaces. "
                        "Restore every face to its originally imported material "
                        "before exporting."
                    )
                    return {"CANCELLED"}
                target_submesh["FaceSurfaceIdsEdited"] = face_sid_data
            else:
                target_submesh.pop("FaceSurfaceIdsEdited", None)

            written += 1

        # Skin data is model-level — purge stale edited fields, then re-encode.
        encode_unskinned_bone_reassignments(candidates, data, warnings)
        _purge_skn_edited(data)
        if self.use_hammerspace:
            try:
                skn_ok, skn_msg = encode_skin_hammerspace(
                    candidates,
                    data,
                    warnings,
                    use_custom_normals=self.use_custom_normals,
                )
            except ValueError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            if not skn_ok:
                self.report({"ERROR"}, skn_msg)
                return {"CANCELLED"}
            if skn_msg:
                self.report({"INFO"}, skn_msg)
        else:
            encode_skin_weights_inplace(candidates, data, warnings, use_custom_normals=self.use_custom_normals)

        update_facial_pose_edits(candidates, data, warnings)

        # Whole-model root-bone scale — model-level, written only when edited.
        root_scale = encode_root_bone_scale_edited(candidates, data, warnings, context)

        for w in warnings:
            self.report({"WARNING"}, w)

        if written == 0:
            self.report({"ERROR"}, "No submeshes written. Check the warnings above.")
            return {"CANCELLED"}

        data["SluggiesModel"]["UseHammerspace"] = self.use_hammerspace
        data["SluggiesModel"]["ReimportTextures"] = self.reimport_textures

        with open(self.filepath, 'w') as f:
            json.dump(data, f, indent=2)

        filename = os.path.basename(self.filepath)
        context.window_manager.clipboard = filename
        subprocess.run(['clip'], input=filename.encode('utf-16-le'), check=False)
        scale_note = (
            f" (root bone scale {root_scale[0]:.4f}, {root_scale[1]:.4f}, {root_scale[2]:.4f})"
            if root_scale else ""
        )
        self.report({"INFO"},
            f"Wrote edited vertex data for {written} submesh(es){scale_note} to {self.filepath}")
        return {"FINISHED"}


def menu_func_export(self, context):
    self.layout.operator(SLUGGIES_OT_export.bl_idname, text="Sluggers intermediate (.sluggie)")


def register():
    bpy.utils.register_class(SLUGGIES_OT_export)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.utils.unregister_class(SLUGGIES_OT_export)
