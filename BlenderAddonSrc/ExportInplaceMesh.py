"""Export, in-place mesh fields: the binary field codec and quantization helpers every export module uses,
the edited vertex buffer, UV channels, normals and colours of a donor submesh, the full ``MeshData``
encode for Hammerspace (``encode_mesh_hammerspace``) and the length checks against the source ``.sluggie``.

Also holds ``ExportCancelled``, the exception every export phase raises to stop the export. First in the
import order of the ``Export*`` modules: it imports none of them, and never ``ExportSluggies``.
"""

import math
import struct
from . import CustomSubmeshExport
from . import FieldCodec


def _to_bytes(data) -> bytes:
    """Decode a binary field: ``x:`` hex string (legacy: base64 or ``z:`` zlib) or a list of byte values."""
    return FieldCodec.decode_field(data)


def _from_bytes(raw: bytes, use_compression: bool = True):
    """Encode binary data as an ``x:`` hex string, or a list of byte values in a --debug file."""
    return FieldCodec.encode_field(raw, use_compression)


def _pack_quantized_component(value, divisor, context):
    """*context* names the component in the error text: a string, or a
    function returning one, called only when the value is refused (building
    the text for every component of every vertex and loop, each with an RNA
    name read, was a large part of the encode loops)."""
    value = float(value)
    if not math.isfinite(value):
        context = context() if callable(context) else context
        raise ValueError(f"{context}: coordinate/component is not finite ({value})")
    quantized = round(value * divisor)
    if not -32768 <= quantized <= 32767:
        context = context() if callable(context) else context
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


# Blender stores imported normals at lower precision than the donor's
# quantized ints, so an untouched normal re-quantizes up to a few steps off
# (measured: <= 0.02 deg, up to 5 steps at shift 14). Anything this close to
# its donor value is round-trip noise, not an edit.
_NORMAL_ROUNDTRIP_MIN_COS = math.cos(math.radians(0.1))


def _decode_donor_components(record, comp_size, divisor, is_float):
    fmt = '>f' if is_float else '>h'
    values = [
        struct.unpack_from(fmt, record, offset)[0]
        for offset in range(0, len(record), comp_size)
    ]
    return values if is_float else [value / divisor for value in values]


def _normal_is_donor_roundtrip(normal, donor_normal):
    """True when *normal* is *donor_normal* after Blender's precision loss.

    A zero-length donor normal (unused/degenerate vertex) always counts:
    Blender cannot represent it and substitutes an arbitrary unit normal.
    """
    donor_length = math.sqrt(sum(c * c for c in donor_normal))
    if donor_length < 1e-8:
        return True
    length = math.sqrt(sum(c * c for c in normal))
    if length < 1e-8:
        return False
    cos = sum(a * b for a, b in zip(normal, donor_normal)) / (length * donor_length)
    return cos >= _NORMAL_ROUNDTRIP_MIN_COS


def encode_vertex_buffer_edited(obj, comp_count, quant_info, use_custom_normals=False,
                                use_compression=True, donor_data=None):
    """Re-quantize edited vertex positions (and normals if comp_count==6)
    back into the original binary format and return an encoded binary field (hex string or byte list).

    With *donor_data* (the donor VertexBufferData) and an unchanged vertex
    count, interleaved normals keep their donor bytes: always when
    *use_custom_normals* is off, and otherwise when Blender's normal is only
    round-trip noise away from the donor value.
    """
    mesh = obj.data
    fmt_nibble = quant_info >> 4
    shift = quant_info & 0xF
    divisor = 1 << shift
    is_float = fmt_nibble in [4, 7, 0xa]
    comp_size = 4 if is_float else 2
    stride = comp_count * comp_size

    donor_raw = _to_bytes(donor_data) if (donor_data is not None and comp_count >= 6) else None
    if donor_raw is not None and len(donor_raw) != len(mesh.vertices) * stride:
        donor_raw = None

    custom_normals = _get_custom_split_normals(obj) if (use_custom_normals and comp_count >= 6) else None
    basis_key = None
    if mesh.shape_keys:
        basis_key = mesh.shape_keys.key_blocks.get("Basis")

    raw_bytes = bytearray()
    for v in mesh.vertices:
        position = basis_key.data[v.index].co if basis_key is not None else v.co
        comps = [position.x, position.y, position.z]
        donor_normal_bytes = None
        if comp_count >= 6:
            if custom_normals is not None:
                n = custom_normals[v.index]
                comps += [n.x, n.y, n.z]
            else:
                comps += [v.normal.x, v.normal.y, v.normal.z]
            if donor_raw is not None:
                record = donor_raw[v.index * stride:(v.index + 1) * stride]
                normal_bytes = record[3 * comp_size:6 * comp_size]
                if custom_normals is None or _normal_is_donor_roundtrip(
                    comps[3:6],
                    _decode_donor_components(normal_bytes, comp_size, divisor, is_float),
                ):
                    donor_normal_bytes = normal_bytes
                    comps = comps[:3]
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
                    lambda v=v, name=component_names[component_index]: f"{obj.name} vertex {v.index} {name}",
                )
        if donor_normal_bytes is not None:
            raw_bytes += donor_normal_bytes

    return _from_bytes(bytes(raw_bytes), use_compression)


def _uv_layer_name(all_channels, target_ch_ind):
    """Return the Blender UV layer name for *target_ch_ind*: ``"uv<position>"``,
    the name ImportMesh gives each channel's layer."""
    for enum_ind, ch in enumerate(all_channels):
        if ch.get('UVChannelIndex', enum_ind) == target_ch_ind:
            return f'uv{enum_ind}'
    return f'uv{target_ch_ind}'


def _legacy_uv_layer_name(all_channels, target_ch_ind):
    """Return the UV layer name older imports used: the channel's PaletteName,
    or ``"uv<position>"`` when it was empty or already taken."""
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


def _find_uv_layer(mesh, all_channels, target_ch_ind):
    """Return *mesh*'s UV layer for *target_ch_ind*, falling back to the
    PaletteName-based name of .blend files imported before 2026-10-11, and
    for the first channel ("uv0") to the render-active (else active) layer
    when no other channel claims that layer's name."""
    name = _uv_layer_name(all_channels, target_ch_ind)
    layer = mesh.uv_layers.get(name)
    if layer is None:
        layer = mesh.uv_layers.get(_legacy_uv_layer_name(all_channels, target_ch_ind))
    if layer is None and name == 'uv0':
        active = mesh.uv_layers.active_render or mesh.uv_layers.active
        if active is not None:
            claimed = set()
            for ch in all_channels:
                idx = ch.get('UVChannelIndex', 0)
                if idx != target_ch_ind:
                    claimed.add(_uv_layer_name(all_channels, idx))
                    claimed.add(_legacy_uv_layer_name(all_channels, idx))
            if active.name not in claimed:
                layer = active
    return layer


def encode_uv_channel_edited(obj, json_channel, use_compression=True, all_uv_channels=None):
    """Re-quantize Blender UV layer back into the game's ST coordinate format.

    Writes each Blender UV value back into its ORIGINAL slot position, using
    UVFacesData to look up which slot index the draw list expects for each face
    loop. This preserves the original coord-array layout so the unmodified draw
    list in the .dat file keeps working correctly.

    Returns uv_channel_data_enc, or None if the matching UV layer is not found.
    Warns (via returned string list) when a slot receives two conflicting values
    (i.e. the user split a UV seam that was previously shared).
    """
    ch_ind = json_channel.get("UVChannelIndex", 0)
    mesh_data = obj.data
    if all_uv_channels is not None:
        uv_layer = _find_uv_layer(mesh_data, all_uv_channels, ch_ind)
    else:
        uv_layer = (mesh_data.uv_layers.get(f"uv{ch_ind}")
                    or mesh_data.uv_layers.get(json_channel.get("PaletteName", "") or f"uv{ch_ind}"))
        if uv_layer is None and ch_ind == 0:
            uv_layer = mesh_data.uv_layers.active_render or mesh_data.uv_layers.active
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
                    lambda slot_index=slot_index, component_index=component_index:
                        f"{obj.name} UV{ch_ind} slot {slot_index} component {component_index}",
                )

    return _from_bytes(bytes(raw_bytes), use_compression), conflicts


def _per_loop_normals(mesh, loop_indices):
    """Per-loop normals for *loop_indices*.

    Uses Blender loop normals (custom split normals when the mesh has them,
    geometric face normals otherwise). Loops with a degenerate (zero-length)
    custom normal — e.g. new loops created by mesh edits before the user
    re-applies smooth shading — fall back to the face normal so something
    usable is always exported.
    """
    result = []
    polygon_of_loop = None               # built for the first degenerate normal only (a loop does not name its face)
    for loop_idx in loop_indices:
        n = mesh.loops[loop_idx].normal
        if n.length_squared < 1e-8:
            if polygon_of_loop is None:
                polygon_of_loop = {
                    index: polygon.index for polygon in mesh.polygons for index in polygon.loop_indices}
            n = mesh.polygons[polygon_of_loop[loop_idx]].normal
        result.append((n[0], n[1], n[2]))
    return result


def _per_loop_buffer(records, what, use_compression=True):
    """Encode one record per loop as (data, index buffer), the per-loop
    contract of UV/normal/color edits. Up to 65,535 loops this is the identity
    layout; a bigger mesh (a stadium ground, 2026-10-18) can't index its loops
    with u16, so equal records are stored once. Every patcher reader looks a
    loop's value up through the index buffer and compacts by value, so both
    layouts give the same result."""
    if len(records) <= 0xFFFF:
        data, indices = b''.join(records), range(len(records))
    else:
        slot_of = {}
        indices = [slot_of.setdefault(record, len(slot_of)) for record in records]
        if len(slot_of) > 0xFFFF:
            raise ValueError(
                f"{what}: {len(slot_of):,} different values on {len(records):,} face corners; "
                "the game indexes at most 65,535 per mesh. Split the mesh or reduce its seams."
            )
        data = b''.join(slot_of)
    return (
        _from_bytes(data, use_compression),
        _from_bytes(struct.pack(f'>{len(indices)}H', *indices), use_compression),
    )


def _per_loop_records(data, faces, stride):
    """Inverse of _per_loop_buffer: the record each loop points at."""
    raw, index_raw = _to_bytes(data), _to_bytes(faces)
    indices = struct.unpack(f'>{len(index_raw) // 2}H', index_raw)
    return [raw[index * stride:(index + 1) * stride] for index in indices]


def encode_normal_edits(obj, json_normal_buffer, loop_indices, use_compression=True):
    """Export per-loop edited normals aligned with FacesDataEdited (plan 3.3, item 1).

    Mirrors the per-loop UV contract of item 3.2: one quantized normal per loop
    in FacesDataEdited loop order (no deduplication) plus an identity per-loop
    index buffer. The patcher compacts the buffer like UVChannelDataEdited.

    Records may declare more components than the three normal axes (CompCount 6
    interleaves [NX, NY, NZ, X, Y, Z]). Blender only carries the normal, so the
    trailing components are copied verbatim from the donor record each loop
    referenced. Returns None when that donor mapping is unavailable.

    With an unchanged loop layout, a loop whose normal is only Blender
    round-trip noise away from its donor record keeps that record verbatim,
    so an untouched mesh exports byte-identical normals.
    """
    normal_data = bytearray()
    comp = json_normal_buffer.get("NormalBufferCompCount", 3)
    quant = json_normal_buffer.get("NormalBufferQuantizeInfo", 0)
    is_float = (quant >> 4) in [4, 7, 0xa]
    divisor = 1 << (quant & 0xF)
    comp_size = 4 if is_float else 2
    stride = comp * comp_size

    # Per-loop donor records, available only with an unchanged loop layout.
    donor_records = None
    donor_raw = _to_bytes(json_normal_buffer.get("NormalBufferData") or b"")
    donor_faces = json_normal_buffer.get("NormalFacesData")
    if donor_raw and donor_faces is not None and not len(donor_raw) % stride:
        donor_indices = list(struct.unpack(
            f'>{len(_to_bytes(donor_faces)) // 2}H', _to_bytes(donor_faces)))
        donor_count = len(donor_raw) // stride
        if len(donor_indices) == len(loop_indices) and all(
            index < donor_count for index in donor_indices
        ):
            donor_records = [
                donor_raw[index * stride:(index + 1) * stride]
                for index in donor_indices
            ]
    # Trailing components can only be preserved with an unchanged loop layout.
    if comp > 3 and donor_records is None:
        return None

    for loop_position, n in enumerate(_per_loop_normals(obj.data, loop_indices)):
        if donor_records is not None:
            record = donor_records[loop_position]
            if _normal_is_donor_roundtrip(
                n, _decode_donor_components(record[:3 * comp_size], comp_size, divisor, is_float)
            ):
                normal_data += record
                continue
        for val in n[:comp]:
            if is_float:
                if not math.isfinite(float(val)):
                    raise ValueError(
                        f"{obj.name}: per-loop normal component is not finite ({val})"
                    )
                normal_data += struct.pack('>f', val)
            else:
                normal_data += _pack_quantized_component(
                    val, divisor, lambda: f"{obj.name} per-loop normal component"
                )
        if comp > 3:
            normal_data += donor_records[loop_position][3 * comp_size:]

    if len(normal_data) != len(loop_indices) * stride:
        return None
    return _per_loop_buffer(
        [bytes(normal_data[k * stride:(k + 1) * stride]) for k in range(len(loop_indices))],
        f"{obj.name} normals", use_compression,
    )


def _apply_inplace_normal_edits(obj, normal_buffer, loop_indices, errors, use_compression=True):
    """Write in-place standalone normal edits. Returns True when loops that
    share a donor slot got different normals, which only Hammerspace can
    store."""
    normal_edits = encode_normal_edits(
        obj, normal_buffer, loop_indices, use_compression
    )
    if normal_edits is None:
        donor_loop_count = len(_to_bytes(
            normal_buffer.get("NormalFacesData")
        )) // 2
        errors.append(
            f"{obj.name}: standalone normal overwrite DROPPED — extended "
            f"normal records could not preserve their donor mapping "
            f"({donor_loop_count} donor loops, {len(loop_indices)} mesh loops). "
            f"The donor normals were kept; your normal edits are NOT in this "
            f"export. Keep the original topology or leave Overwrite Normals off."
        )
        normal_buffer.pop("NormalBufferDataEdited", None)
        normal_buffer.pop("NormalFacesDataEdited", None)
        return False

    norm_data, norm_faces = normal_edits
    donor_faces_raw = _to_bytes(normal_buffer["NormalFacesData"])
    norm_comp = normal_buffer.get("NormalBufferCompCount", 3)
    norm_quant = normal_buffer.get("NormalBufferQuantizeInfo", 0)
    norm_stride = norm_comp * _comp_size_skin(norm_quant)
    loop_records = _per_loop_records(norm_data, norm_faces, norm_stride)
    donor_count = len(donor_faces_raw) // 2
    donor_indices = [
        int.from_bytes(donor_faces_raw[k*2:k*2+2], 'big')
        for k in range(donor_count)
    ]
    # Conflict check: if loops sharing a donor slot have different edited
    # normals, compaction would grow the buffer.
    slot_values = {}
    normal_conflict = False
    for slot_idx, loop_pos in zip(donor_indices, range(len(loop_indices))):
        record = loop_records[loop_pos]
        if slot_idx in slot_values:
            if slot_values[slot_idx] != record:
                normal_conflict = True
                break
        else:
            slot_values[slot_idx] = record
    if normal_conflict:
        errors.append(
            f"{obj.name}: standalone normal buffer conflict — loops sharing "
            f"a donor slot have different edited normals. Normal overwrite "
            f"DROPPED (would exceed original buffer size); your normal edits "
            f"are NOT in this export."
        )
        normal_buffer.pop("NormalBufferDataEdited", None)
        normal_buffer.pop("NormalFacesDataEdited", None)
        return True
    normal_buffer["NormalBufferDataEdited"] = norm_data
    normal_buffer["NormalFacesDataEdited"] = norm_faces
    return False


def _get_loop_color(entry):
    """Read one color attribute entry back in the donor's own value space.

    ``entry.color`` converts between sRGB and scene-linear on top of the 8-bit
    BYTE_COLOR storage, which does not round-trip: 73 of the 256 donor byte
    values come back shifted by one, so every export of an untouched model
    would report a spurious color edit. ``color_srgb`` maps straight onto the
    stored bytes. Mirrors ``_set_loop_color`` in ImportMesh.
    """
    try:
        return entry.color_srgb
    except AttributeError:
        return entry.color


def _encode_color_entry(quant_info, rgba):
    """Encode one 0..1 (r, g, b, a) color into the big-endian entry layout that
    decode_color_channel (ImportDecode) decodes for the channel's format."""
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


def encode_color_edits(obj, json_channel, loop_indices, use_compression=True):
    """Export per-loop edited colors for one ColorChannels entry (plan 3.3, item 1).

    Reads the CORNER-domain color attribute the importer created ('color0' /
    'color1'), one entry per loop in FacesDataEdited loop order, plus an
    identity per-loop index buffer — the same per-loop contract as
    UVFacesDataEdited. Returns None when the attribute does not exist.
    Unset (0, 0, 0, 0) corners are written white, as on the rebuild paths
    (CustomSubmeshExport.fill_unset_colors).
    """
    mesh = obj.data
    ch_idx = json_channel.get("ColorChannelIndex", 0)
    attr = next((a for a in mesh.color_attributes if a.name == f"color{ch_idx}"), None)
    if attr is None:
        return None
    quant = json_channel.get("ColorChannelQuantizeInfo", 0)
    colors = CustomSubmeshExport.fill_unset_colors(
        obj.name, [_get_loop_color(attr.data[loop_idx]) for loop_idx in loop_indices],
    )
    return _per_loop_buffer(
        [_encode_color_entry(quant, color) for color in colors],
        f"{obj.name} color{ch_idx}", use_compression,
    )


def encode_mesh_hammerspace(obj, json_submesh, use_custom_normals=False, use_compression=True):
    """Encode all mesh data for hammerspace export (vertex count may differ from original).

    Returns a dict with:
      'VertexBufferDataEdited':    binary field   — full re-quantized vertex buffer
      'FacesDataEdited':           binary field   — uint16 BE triangulated face indices
      'FacesCountEdited':          int            — triangle count
      'FaceTextureIndicesEdited':  binary field   — uint16 BE texture index per face
                                                    (derived from Blender material slots;
                                                    used to route faces to draw states)
      'UVEdits': {ch_ind: (uv_data_enc, uv_faces_enc), ...}
      'NormalEdits': (normal_data_enc, normal_faces_enc) or None — per-loop normals
                      when the submesh has a standalone NormalBuffer (plan 3.3)
      'ColorEdits': {ch_ind: (color_data_enc, color_faces_enc), ...} — per-loop
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
        use_compression=use_compression,
        donor_data=json_submesh.get("VertexBuffer", {}).get("VertexBufferData"),
    )

    face_flat = [vi for tri in triangles for vi in tri.vertices]
    if len(mesh.vertices) > 0x10000:
        raise ValueError(
            f"the mesh has {len(mesh.vertices):,} vertices; a submesh indexes at most 65,536. "
            "Split it into several meshes or reduce it")
    faces_data = _from_bytes(struct.pack(f'>{len(face_flat)}H', *face_flat), use_compression)

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
            if not 0 <= tex_idx <= 0xFFFF:
                raise ValueError(
                    f"material {slot.material.name}: TextureIndex {tex_idx} is not a texture index (0-65535)")
        mat_to_tex[slot_idx] = tex_idx
    face_tex_flat = [
        mat_to_tex.get(mesh.polygons[tri.polygon_index].material_index, 0)
        for tri in triangles
    ]
    face_tex_data = _from_bytes(
        struct.pack(f'>{len(face_tex_flat)}H', *face_tex_flat), use_compression
    )

    all_uv_channels = json_submesh.get('UVChannels', [])
    uv_edits = {}
    for json_channel in all_uv_channels:
        ch_ind = json_channel.get('UVChannelIndex', 0)
        uv_layer = _find_uv_layer(mesh, all_uv_channels, ch_ind)
        if uv_layer is None:
            continue

        quant_info_uv = json_channel['UVChannelQuantizeInfo']
        comp_count_uv = json_channel['UVChannelCompCount']
        fmt_nibble = quant_info_uv >> 4
        shift = quant_info_uv & 0xF
        divisor = 1 << shift
        is_float = fmt_nibble in [4, 7, 0xa]

        # Per-loop UV storage: each face-corner gets its own UV entry, in loop
        # order. The patcher compacts it against the donor slot layout, which
        # keeps the donor's duplicate values at their own indices (deduplicating
        # here once changed the array length and broke patching). Only meshes
        # past 65,535 corners store equal values once (_per_loop_buffer).
        coords = []
        for tri in triangles:
            for loop_idx in tri.loops:
                uv = uv_layer.data[loop_idx].uv
                s = uv.x
                t = 1.0 - uv.y  # undo Blender V-flip applied on import
                if is_float:
                    qs, qt = float(s), float(t)
                else:
                    qs = int(round(s * divisor))
                    qt = int(round(t * divisor))
                coords.append((qs, qt))

        records = []
        for coord_index, (qs, qt) in enumerate(coords):
            record = bytearray()
            for component_index, val in enumerate(
                [qs, qt] + [0.0] * (comp_count_uv - 2)
            ):
                if is_float:
                    if not math.isfinite(float(val)):
                        raise ValueError(
                            f"{obj.name} UV{ch_ind} loop {coord_index} component "
                            f"{component_index}: value is not finite ({val})"
                        )
                    record += struct.pack('>f', float(val))
                else:
                    record += _pack_quantized_component(
                        int(val) / divisor,
                        divisor,
                        lambda coord_index=coord_index, component_index=component_index, ch_ind=ch_ind:
                            f"{obj.name} UV{ch_ind} loop {coord_index} component {component_index}",
                    )
            records.append(bytes(record))
        uv_edits[ch_ind] = _per_loop_buffer(records, f"{obj.name} UV{ch_ind}", use_compression)

    # Per-loop normals (standalone NormalBuffer, non-skinned meshes) and per-loop
    # colors — same expanded per-loop contract as the UV edits above (plan 3.3).
    loop_indices = [loop_idx for tri in triangles for loop_idx in tri.loops]

    normal_edits = None
    normal_buffer = json_submesh.get("NormalBuffer")
    # Normals are only rewritten when the user opted in, matching in-place mode.
    if use_custom_normals and isinstance(normal_buffer, dict) and "NormalBufferData" in normal_buffer:
        normal_edits = encode_normal_edits(obj, normal_buffer, loop_indices, use_compression)

    color_edits = {}
    for json_channel in json_submesh.get("ColorChannels", []):
        ch_ind = json_channel.get("ColorChannelIndex", 0)
        encoded = encode_color_edits(obj, json_channel, loop_indices, use_compression)
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


def detect_length_mismatches(obj, json_submesh):
    """Return a list of human-readable strings describing any buffer-length
    changes that need a Hammerspace export.

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

    # UV edits need no check of their own here: a changed vertex count, a changed topology and a UV seam
    # conflict are each found elsewhere. (A check that stood here compared the donor's UV array length with
    # the donor's own highest face index, so a donor with an unused trailing UV slot failed it on every
    # export and always went to Hammerspace.)
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


class ExportCancelled(Exception):
    """Raised by an export phase to stop the export: ``execute`` reports each message as an error and returns
    CANCELLED. Deliberately not a ``ValueError``: the phases turn the encoders' ``ValueError`` into this, and
    their ``except ValueError`` blocks must let it through."""

    def __init__(self, messages):
        self.messages = [messages] if isinstance(messages, str) else list(messages)
        super().__init__("; ".join(self.messages))
