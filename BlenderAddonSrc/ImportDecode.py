"""Import, decoding: the binary fields of a ``.sluggie`` as plain Python values (faces, vertex buffers,
normals, UVs, colours, facial poses, skin edits) and the views that pick the edited or rebuilt data of a
submesh over the donor's (``_edited_submesh_view``, ``_rigid_rebuild_view``, surface face ranges).

No ``bpy`` and no ``mathutils``: everything here runs outside Blender, on damaged input too. First in the
import order of the ``Import*`` modules; never imports ``ImportSluggies``.
"""

import struct

try:
    from . import FieldCodec
except ImportError:  # imported flat by the unit tests
    import FieldCodec


def _to_bytes(data) -> bytes:
    """Decode a binary field: ``x:`` hex string (legacy: base64 or ``z:`` zlib) or a list of byte values."""
    return FieldCodec.decode_field(data)


GEO_ID_FREE = 0xFFFF


def decode_faces(submesh):
    """Decode encoded uint16 BE face data back to a list of [i,j,k] triplets."""
    faces_data = submesh.get("FacesData")
    face_count = submesh.get("FacesCount", 0)
    if not faces_data or not face_count:
        return []
    raw = _to_bytes(faces_data)
    flat = list(struct.unpack(f'>{face_count * 3}H', raw))
    return [flat[i*3:i*3+3] for i in range(face_count)]


def decode_vertex_buffer(vb):
    """Decode a VertexBuffer dict into (positions, normals) lists of tuples.

    For skinned meshes CompCount==6 the raw buffer interleaves [X,Y,Z,NX,NY,NZ]
    per vertex. For non-skinned meshes CompCount==3 only [X,Y,Z] is stored.
    Returns normals as an empty list when CompCount < 6.
    """
    raw = _to_bytes(vb["VertexBufferData"])
    quant = vb["VertexBufferQuantizeInfo"]
    comp_count = vb["VertexBufferCompCount"]
    fmt_nibble = quant >> 4
    divisor = 1 << (quant & 0xF)

    if fmt_nibble in [4, 7, 0xa]:
        comp_fmt, comp_size = '>f', 4
    else:  # 0, 3 -> signed int16
        comp_fmt, comp_size = '>h', 2

    stride = comp_count * comp_size
    num_verts = len(raw) // stride

    flat = _unpack_components(raw, num_verts, comp_count, comp_fmt, comp_size)
    starts = range(0, len(flat), comp_count)
    positions = [(flat[o] / divisor, flat[o + 1] / divisor, flat[o + 2] / divisor) for o in starts]
    normals = []
    if comp_count >= 6:
        normals = [(flat[o + 3] / divisor, flat[o + 4] / divisor, flat[o + 5] / divisor) for o in starts]

    return positions, normals


def _unpack_components(raw, records, comp_count, comp_fmt, comp_size, minimum=3):
    """The first *records* records of *raw* as one flat tuple of components
    (one ``struct.unpack`` for the buffer, not one call per component)."""
    if records and comp_count < minimum:
        raise IndexError(f'{comp_count} components per record, at least {minimum} expected')
    count = records * comp_count
    return struct.unpack(f'>{count}{comp_fmt[-1]}', raw[:count * comp_size])


def decode_normal_buffer(normal_buffer):
    """Decode a NormalBuffer dict (non-skinned standalone normal array) into a
    list of (nx, ny, nz) float tuples.

    Same quantization rules as the interleaved CompCount==6 vertex normals:
    high nibble picks float vs signed int16, low nibble is the right-shift
    exponent of the divisor.
    """
    raw = _to_bytes(normal_buffer["NormalBufferData"])
    quant = normal_buffer.get("NormalBufferQuantizeInfo", 0)
    comp_count = normal_buffer.get("NormalBufferCompCount", 3)
    fmt_nibble = quant >> 4
    divisor = 1 << (quant & 0xF)

    if fmt_nibble in [4, 7, 0xa]:
        comp_fmt, comp_size = '>f', 4
    else:  # 0, 3 -> signed int16
        comp_fmt, comp_size = '>h', 2

    stride = comp_count * comp_size
    num_normals = len(raw) // stride

    flat = _unpack_components(raw, num_normals, comp_count, comp_fmt, comp_size)
    return [(flat[o] / divisor, flat[o + 1] / divisor, flat[o + 2] / divisor)
            for o in range(0, len(flat), comp_count)]


def _facial_vertex_indices(attribute):
    indices = []
    for run in attribute.get("Runs", []):
        first = run.get("FirstVertex", 0)
        count = run.get("VertexCount", 0)
        indices.extend(range(first, first + count))
    return indices


def _decode_facial_pose(data, quant_info, component_count=3, component_size=2):
    """Decode one facial XYZ array using its matched GPL position quantization."""
    raw = _to_bytes(data)
    if component_size != 2 or component_count < 3:
        return []
    divisor = 1 << (quant_info & 0xF)
    stride = component_count * component_size
    return [
        tuple(struct.unpack_from('>3h', raw, offset)[axis] / divisor for axis in range(3))
        for offset in range(0, len(raw), stride)
    ]


def _facial_edit_lookup(facial_edited):
    lookup = {}
    for facial_object in (facial_edited or {}).get("Objects", []):
        object_index = facial_object.get("ObjectIndex")
        for pose_edit in facial_object.get("PositionPoseEdits", []):
            lookup[(object_index, pose_edit.get("PoseIndex"))] = pose_edit.get("PoseData")
    return lookup


def _decode_rebuild_facial_poses(rebuild_poses, quant_info, record_stride=12):
    """``SkinnedRebuild.FacialPoses`` (12-byte ``x y z nx ny nz`` records) or
    ``RigidRebuild.FacialPoses`` (6-byte ``x y z`` records) ->
    ``[(object_index, [[(vertex, (dx, dy, dz)), ...] per pose after pose
    zero])]``, deltas in the rebuild's vertex order and position format
    (the body's normal components are dropped; a rigid entry's normal
    lists are not read, Blender derives shape-key normals itself)."""
    divisor = 1 << (quant_info & 0xF)
    result = []
    for entry in rebuild_poses or []:
        raw = _to_bytes(entry.get("Vertices")) if entry.get("Vertices") else b""
        vertices = [value for (value,) in struct.iter_unpack('>H', raw)]
        poses = []
        for delta in entry.get("PoseDeltas") or []:
            raw_delta = _to_bytes(delta)
            if len(raw_delta) != len(vertices) * record_stride:
                poses.append([])
                continue
            poses.append([
                (vertex, tuple(struct.unpack_from('>3h', raw_delta, k * record_stride)[axis] / divisor for axis in range(3)))
                for k, vertex in enumerate(vertices)
            ])
        result.append((entry.get("ObjectIndex"), poses))
    return result


def decode_uv_channel(uv_channel):
    """Decode a UVChannel dict into:
    - coords: list of (s, t) float tuples decoded from the raw ST buffer
    - uv_faces: list of [i0, i1, i2] UV index triplets, aligned face-for-face with FacesData
    """
    raw = _to_bytes(uv_channel["UVChannelData"])
    quant = uv_channel["UVChannelQuantizeInfo"]
    comp_count = uv_channel["UVChannelCompCount"]
    fmt_nibble = quant >> 4
    divisor = 1 << (quant & 0xF)

    if fmt_nibble in [4, 7, 0xa]:
        comp_fmt, comp_size = '>f', 4
    else:  # 0, 3 -> signed int16
        comp_fmt, comp_size = '>h', 2

    stride = comp_count * comp_size
    num_coords = len(raw) // stride

    flat = _unpack_components(raw, num_coords, comp_count, comp_fmt, comp_size, minimum=2)
    coords = [(flat[o] / divisor, flat[o + 1] / divisor) for o in range(0, len(flat), comp_count)]

    # Decode per-face UV indices (uint16 BE triplets, same encoding as FacesData)
    uv_faces = []
    uv_faces_data = uv_channel.get("UVFacesData")
    if uv_faces_data:
        uv_raw = _to_bytes(uv_faces_data)
        n = len(uv_raw) // 2
        flat = list(struct.unpack(f'>{n}H', uv_raw))
        uv_faces = [flat[i * 3 : i * 3 + 3] for i in range(n // 3)]

    return coords, uv_faces


def decode_color_channel(color_channel):
    """Decode a ColorChannel dict into a list of (r, g, b[, a]) float tuples and
    a list of [i0, i1, i2] color index triplets aligned face-for-face with FacesData."""
    raw = _to_bytes(color_channel["ColorChannelData"])
    quant = color_channel["ColorChannelQuantizeInfo"]
    fmt = quant >> 4
    entry_sizes = {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}
    entry_size = entry_sizes.get(fmt, 2)
    num_colors = len(raw) // entry_size

    colors = []
    for i in range(num_colors):
        num = int.from_bytes(raw[i * entry_size : i * entry_size + entry_size], 'big')
        if fmt == 0:  # RGB565
            b = (num & 0b11111) / 31.0
            g = ((num >> 5) & 0b111111) / 63.0
            r = ((num >> 11) & 0b11111) / 31.0
            colors.append((r, g, b, 1.0))
        elif fmt == 3:  # RGBA4444
            a = (num & 0xF) / 15.0
            b = ((num >> 4) & 0xF) / 15.0
            g = ((num >> 8) & 0xF) / 15.0
            r = ((num >> 12) & 0xF) / 15.0
            colors.append((r, g, b, a))
        elif fmt in (1, 4):  # RGB8
            r = ((num >> 16) & 0xFF) / 255.0
            g = ((num >> 8) & 0xFF) / 255.0
            b = (num & 0xFF) / 255.0
            colors.append((r, g, b, 1.0))
        elif fmt in (2, 5):  # RGBA8
            r = ((num >> 24) & 0xFF) / 255.0
            g = ((num >> 16) & 0xFF) / 255.0
            b = ((num >> 8) & 0xFF) / 255.0
            a = (num & 0xFF) / 255.0
            colors.append((r, g, b, a))
        else:
            colors.append((1.0, 1.0, 1.0, 1.0))

    color_faces = []
    faces_data = color_channel.get("ColorFacesData")
    if faces_data:
        cf_raw = _to_bytes(faces_data)
        n = len(cf_raw) // 2
        flat = list(struct.unpack(f'>{n}H', cf_raw))
        color_faces = [flat[i * 3 : i * 3 + 3] for i in range(n // 3)]

    return colors, color_faces


def decode_face_texture_indices(submesh, face_count):
    """Decode FaceTextureIndices into a list of int texture indices, one per face.

    Falls back to the TextureIndex of the first UV channel when the field is
    absent (e.g. files produced before this field was added).
    """
    data = submesh.get("FaceTextureIndices")
    if data:
        raw = _to_bytes(data)
        n = len(raw) // 2
        return list(struct.unpack(f'>{n}H', raw))
    fallback = (submesh.get("UVChannels") or [{}])[0].get("TextureIndex") or 0
    return [fallback] * face_count


def _has_edited_data(submesh):
    """Return True when *submesh* contains any edited mesh data."""
    vb = submesh.get("VertexBuffer") or {}
    normal_buffer = submesh.get("NormalBuffer")
    return bool(
        vb.get("VertexBufferDataEdited") or submesh.get("FacesDataEdited")
        or submesh.get("RigidRebuild") or submesh.get("SkinnedRebuild")
        or submesh.get("FaceSurfaceIdsEdited")
        or any(ch.get("UVChannelDataEdited") for ch in submesh.get("UVChannels") or [])
        or any(ch.get("ColorChannelDataEdited") for ch in submesh.get("ColorChannels") or [])
        or (isinstance(normal_buffer, dict) and normal_buffer.get("NormalBufferDataEdited"))
    )


_INPLACE_SKIN_EDIT_KEYS = ("BindPoseDataEdited", "WeightDataEdited", "VertexCntEdited",
                           "BoneIndexEdited", "DestIndexDataEdited")


def _submesh_vertex_ranges(model):
    """``[(global start, vertex count)]`` per submesh: the skinned position
    slots run on across the submeshes (as in the exporter's skin encoders)."""
    ranges, start = [], 0
    for submesh in model.get("Submeshes") or []:
        vb = submesh.get("VertexBuffer") or {}
        comp = 4 if (int(vb.get("VertexBufferQuantizeInfo", 0)) >> 4) in (4, 7, 0xa) else 2
        stride = int(vb.get("VertexBufferCompCount") or 3) * comp
        count = int(vb.get("VertexBufferLength") or 0) // stride if stride else 0
        ranges.append((start, count))
        start += count
    return ranges


def _skin_bytes(entry, key, edited):
    value = (entry.get(key + "Edited") if edited else None) or entry.get(key)
    return _to_bytes(value) if value else b""


def _decode_skin_totals(skin, edited):
    """``{global vertex: {bone: raw weight}}`` of a SkinData dict, the way
    the core parser sums SK1 (256), SK2 and SKAcc contributions. *edited*
    reads the in-place ``*Edited`` fields where present."""
    vs = 6 * (4 if (int(skin.get("QuantizeInfo", 0)) >> 4) in (4, 7, 0xa) else 2)
    totals = {}

    def add(bone, vertex, raw):
        slot = totals.setdefault(vertex, {})
        slot[bone] = slot.get(bone, 0) + raw

    def count(entry):
        if edited and entry.get("VertexCntEdited") is not None:
            return int(entry["VertexCntEdited"])
        return int(entry["VertexCnt"])

    for entry in skin.get("SK1s") or []:
        bone = entry.get("BoneIndexEdited") if edited and entry.get("BoneIndexEdited") is not None else entry["BoneIndex"]
        start = (int(entry.get("GplVertexArrValue", 0)) + int(entry.get("VertexOffset", 0))) // vs
        for i in range(count(entry)):
            add(int(bone), start + i, 256)
    for entry in skin.get("SK2s") or []:
        start = (int(entry.get("GplVertexArrValue", 0)) + int(entry.get("VertexOffset", 0))) // vs
        weights = _skin_bytes(entry, "WeightData", edited)
        for i in range(min(count(entry), len(weights) // 2)):
            add(int(entry["BoneIndex1"]), start + i, weights[2 * i])
            add(int(entry["BoneIndex2"]), start + i, weights[2 * i + 1])
    for entry in skin.get("SKAccs") or []:
        base = int(entry.get("GplDestArrValue", 0)) // vs
        weights = _skin_bytes(entry, "WeightData", edited)
        dests_raw = _skin_bytes(entry, "DestIndexData", edited)
        dests = struct.unpack(f'>{len(dests_raw) // 2}H', dests_raw[:len(dests_raw) // 2 * 2])
        for i in range(min(count(entry), len(weights), len(dests))):
            add(int(entry["BoneIndex"]), base + dests[i], weights[i])
    return totals


def _decode_model_skin_edit(edited, skin, ranges):
    """``{global vertex: {bone: raw weight}}`` of a model-level
    ``SkinDataEdited``, or None when it cannot be placed. Its entries name
    each member by its index inside its submesh (``VertexIndices``; SKAcc:
    ``DestIndexData``). The encoder keeps the donor's bone sets and groups
    the members by their donor entry (SK1: bone and GplVertexArrValue, SK2:
    bone pair and GplVertexArrValue, SKAcc: bone), so each index is placed
    among the slots of that donor entry."""
    if edited.get("MembershipEdited"):
        return None
    vs = 6 * (4 if (int(skin.get("QuantizeInfo", 0)) >> 4) in (4, 7, 0xa) else 2)

    def local_map(slots):
        mapping = {}
        for vertex in slots:
            for start, n in ranges:
                if start <= vertex < start + n:
                    if vertex - start in mapping:
                        raise LookupError("ambiguous vertex index")
                    mapping[vertex - start] = vertex
                    break
        return mapping

    def run_slots(entries):
        slots = set()
        for entry in entries:
            start = (int(entry.get("GplVertexArrValue", 0)) + int(entry.get("VertexOffset", 0))) // vs
            slots.update(range(start, start + int(entry["VertexCnt"])))
        return slots

    def indices(entry, key):
        raw = _to_bytes(entry.get(key)) if entry.get(key) else b""
        return struct.unpack(f'>{len(raw) // 2}H', raw[:len(raw) // 2 * 2])

    totals = {}

    def add(mapping, bone, local, raw):
        vertex = mapping[local]                    # KeyError: not a slot of the donor entry
        slot = totals.setdefault(vertex, {})
        slot[bone] = slot.get(bone, 0) + raw

    try:
        for entry in edited.get("SK1s") or []:
            bone, gpl = int(entry["BoneIndex"]), int(entry.get("GplVertexArrValue", 0))
            donors = [e for e in skin.get("SK1s") or []
                      if int(e["BoneIndex"]) == bone and int(e.get("GplVertexArrValue", 0)) == gpl]
            if not donors:
                return None
            mapping = local_map(run_slots(donors))
            for local in indices(entry, "VertexIndices"):
                add(mapping, bone, local, 256)
        for entry in edited.get("SK2s") or []:
            pair = {int(entry["BoneIndex1"]), int(entry["BoneIndex2"])}
            gpl = int(entry.get("GplVertexArrValue", 0))
            donors = [e for e in skin.get("SK2s") or []
                      if {int(e["BoneIndex1"]), int(e["BoneIndex2"])} == pair
                      and int(e.get("GplVertexArrValue", 0)) == gpl]
            if not donors:
                return None
            mapping = local_map(run_slots(donors))
            weights = _to_bytes(entry.get("WeightData")) if entry.get("WeightData") else b""
            for i, local in enumerate(indices(entry, "VertexIndices")):
                add(mapping, int(entry["BoneIndex1"]), local, weights[2 * i])
                add(mapping, int(entry["BoneIndex2"]), local, weights[2 * i + 1])
        for entry in edited.get("SKAccs") or []:
            bone = int(entry["BoneIndex"])
            slots = set()
            for donor in skin.get("SKAccs") or []:
                if int(donor["BoneIndex"]) == bone:
                    base = int(donor.get("GplDestArrValue", 0)) // vs
                    slots.update(base + d for d in indices(donor, "DestIndexData"))
            if not slots:
                return None
            mapping = local_map(slots)
            weights = _to_bytes(entry.get("WeightData")) if entry.get("WeightData") else b""
            for i, local in enumerate(indices(entry, "DestIndexData")):
                add(mapping, bone, local, weights[i])
    except (LookupError, ValueError, struct.error):
        return None
    return totals


def _edited_skin_influences(model):
    """``({submesh: [(vertex, bone, weight)]}, skinned bone ids)`` of the
    skinned bones as the file's skin edits leave them (in-place ``*Edited``
    SK fields, or a model-level ``SkinDataEdited``); None when the file has
    no skin edit or it cannot be decoded. Weights are raw / 256, as in the
    core parser's ``VertexInfluences``."""
    skin = model.get("SkinData") or {}
    model_edit = model.get("SkinDataEdited")
    inplace = any(key in entry for name in ("SK1s", "SK2s", "SKAccs")
                  for entry in skin.get(name) or [] for key in _INPLACE_SKIN_EDIT_KEYS)
    if not skin or not (inplace or model_edit):
        return None
    ranges = _submesh_vertex_ranges(model)
    try:
        if model_edit:
            totals = _decode_model_skin_edit(model_edit, skin, ranges)
            if totals is None:
                print("WARNING: SkinDataEdited could not be mapped to vertices; "
                      "the _edit objects show the donor skin weights")
                return None
        else:
            totals = _decode_skin_totals(skin, True)
    except (KeyError, ValueError, struct.error) as exc:
        print(f"WARNING: skin edits could not be decoded ({exc}); "
              "the _edit objects show the donor skin weights")
        return None
    skinned = {int(e["BoneIndex"]) for e in skin.get("SK1s") or []}
    skinned |= {int(e[k]) for e in skin.get("SK2s") or [] for k in ("BoneIndex1", "BoneIndex2")}
    skinned |= {int(e["BoneIndex"]) for e in skin.get("SKAccs") or []}
    skinned |= {bone for bones in totals.values() for bone in bones}
    per_submesh = {}
    for vertex, bones in sorted(totals.items()):
        for index, (start, n) in enumerate(ranges):
            if start <= vertex < start + n:
                per_submesh.setdefault(index, []).extend(
                    (vertex - start, bone, raw / 256.0) for bone, raw in sorted(bones.items()) if raw > 0)
                break
    return per_submesh, skinned


def _edit_object_influences(submesh_index, bone_list, skin_edit):
    """Influences for an ``_edit`` object of a submesh the skin edit touches:
    the donor's for non-skinned bones plus the edited skinned weights; None
    when the skin edit does not reach this submesh."""
    if skin_edit is None:
        return None
    per_submesh, skinned = skin_edit
    donor_skinned = False
    influences = []
    for bd in bone_list or []:
        bone_id = int(bd['BoneId'])
        for entry in bd.get('VertexInfluences', []):
            if entry['SubmeshIndex'] != submesh_index:
                continue
            if bone_id in skinned:
                donor_skinned = True
                continue
            raw = _to_bytes(entry['Influences'])
            influences.extend((v, bone_id, w) for v, w in struct.iter_unpack('>Hf', raw))
    if not donor_skinned and submesh_index not in per_submesh:
        return None
    return influences + per_submesh.get(submesh_index, [])


def _rigid_import_placement(submesh, donor_owner, effective_owner):
    """``(original_owner, make_edit)`` for importing a submesh.

    The donor object is the unedited "before" view, so a rigid one always
    sits on its donor bone. Every edit, a bone move included, shows on the
    ``_edit`` object, which sits on the effective owner. (Until 2026-10-07 a
    plain ``GeoIdEdited`` move without a ``RigidRebuild`` put the donor
    object on the new bone too, so both copies showed in the edited place.)
    """
    moved = donor_owner is not None and effective_owner != donor_owner
    return donor_owner, bool(_has_edited_data(submesh) or moved)


def _edited_submesh_view(submesh):
    """Return a shallow copy of *submesh* with all *Edited fields promoted to
    their primary counterparts so existing decode_* helpers can be reused
    without modification.

    A ``RigidRebuild`` is promoted the same way:
    its arrays replace the donor's, and ``_FaceSurfaceIds`` lists the surface
    key each face draws through (donor SurfaceId or new surface key)."""
    view = dict(submesh)
    rebuild = view.get("RigidRebuild") or view.get("SkinnedRebuild")
    if rebuild:
        return _rigid_rebuild_view(view, rebuild)

    vb = dict(view.get("VertexBuffer") or {})
    if vb.get("VertexBufferDataEdited"):
        vb["VertexBufferData"] = vb["VertexBufferDataEdited"]
    view["VertexBuffer"] = vb

    if view.get("FacesDataEdited"):
        view["FacesData"] = view["FacesDataEdited"]
    if view.get("FacesCountEdited") is not None:
        view["FacesCount"] = view["FacesCountEdited"]
    if view.get("FaceTextureIndicesEdited"):
        view["FaceTextureIndices"] = view["FaceTextureIndicesEdited"]

    edited_uvs = []
    for ch in view.get("UVChannels") or []:
        ch = dict(ch)
        if ch.get("UVChannelDataEdited"):
            ch["UVChannelData"] = ch["UVChannelDataEdited"]
        if ch.get("UVFacesDataEdited"):
            ch["UVFacesData"] = ch["UVFacesDataEdited"]
        edited_uvs.append(ch)
    view["UVChannels"] = edited_uvs

    # Per-loop colours and standalone normals: without these the `_edit`
    # object would show donor values and the next export would drop the edit.
    colors = []
    for ch in view.get("ColorChannels") or []:
        ch = dict(ch)
        if ch.get("ColorChannelDataEdited") and ch.get("ColorFacesDataEdited"):
            ch["ColorChannelData"] = ch["ColorChannelDataEdited"]
            ch["ColorFacesData"] = ch["ColorFacesDataEdited"]
        colors.append(ch)
    view["ColorChannels"] = colors

    normal_buffer = view.get("NormalBuffer")
    if (isinstance(normal_buffer, dict) and normal_buffer.get("NormalBufferDataEdited")
            and normal_buffer.get("NormalFacesDataEdited")):
        normal_buffer = dict(normal_buffer)
        normal_buffer["NormalBufferData"] = normal_buffer["NormalBufferDataEdited"]
        normal_buffer["NormalFacesData"] = normal_buffer["NormalFacesDataEdited"]
        view["NormalBuffer"] = normal_buffer

    # Faces moved to another donor surface: one DisplayStates index per face.
    face_surface_ids = _edited_face_surface_ids(view)
    if face_surface_ids is not None:
        view["_FaceSurfaceIds"] = face_surface_ids

    return view


def _edited_face_surface_ids(view):
    """SurfaceId of every face from ``FaceSurfaceIdsEdited``, or None when the
    field is absent or does not fit the faces / display states."""
    raw = _to_bytes(view.get("FaceSurfaceIdsEdited")) if view.get("FaceSurfaceIdsEdited") else b""
    if not raw:
        return None
    display_states = view.get("DisplayStates") or []
    indices = struct.unpack(f'>{len(raw) // 2}H', raw[:len(raw) // 2 * 2])
    if len(indices) != int(view.get("FacesCount") or 0):
        return None
    ids = []
    for ds_idx in indices:
        surface_id = display_states[ds_idx].get("SurfaceId") if ds_idx < len(display_states) else None
        if not surface_id:
            return None
        ids.append(surface_id)
    return ids


def _skinned_rebuild_influences(submesh):
    """``[(vertex, bone, weight)]`` of a SkinnedRebuild, or None."""
    rebuild = submesh.get("SkinnedRebuild") or {}
    raw = _to_bytes(rebuild.get("Influences")) if rebuild.get("Influences") else b""
    return list(struct.iter_unpack('>HHf', raw)) if raw else None


def _rigid_rebuild_view(view, rebuild):
    """The ``_edited_submesh_view`` of a submesh carrying a RigidRebuild or
    a SkinnedRebuild (same array shape; the skinned one keeps its normals
    interleaved in VertexBufferData)."""
    vb = dict(view.get("VertexBuffer") or {})
    vb["VertexBufferData"] = rebuild["VertexBufferData"]
    vb.pop("VertexBufferDataEdited", None)
    view["VertexBuffer"] = vb
    view["FacesData"] = rebuild["FacesData"]
    view["FacesCount"] = int(rebuild["FacesCount"])
    view["FaceTextureIndices"] = _from_bytes(struct.pack(f'>{view["FacesCount"]}H', *([0] * view["FacesCount"])))
    for key in ("FacesDataEdited", "FacesCountEdited", "FaceTextureIndicesEdited", "FaceSurfaceIdsEdited"):
        view.pop(key, None)

    normal_buffer = view.get("NormalBuffer")
    if isinstance(normal_buffer, dict) and rebuild.get("NormalBufferData"):
        normal_buffer = dict(normal_buffer)
        normal_buffer["NormalBufferData"] = rebuild["NormalBufferData"]
        normal_buffer["NormalFacesData"] = rebuild["NormalFacesData"]
        normal_buffer.pop("NormalBufferDataEdited", None)
        normal_buffer.pop("NormalFacesDataEdited", None)
        view["NormalBuffer"] = normal_buffer

    colors = []
    for ch in view.get("ColorChannels") or []:
        ch = dict(ch)
        if rebuild.get("ColorChannelData") and not colors:
            ch["ColorChannelData"] = rebuild["ColorChannelData"]
            ch["ColorFacesData"] = rebuild["ColorFacesData"]
        ch.pop("ColorChannelDataEdited", None)
        ch.pop("ColorFacesDataEdited", None)
        colors.append(ch)
    view["ColorChannels"] = colors

    rebuilt_uv = {int(uv["UVChannelIndex"]): uv for uv in rebuild.get("UVChannels") or []}
    uvs = []
    for ch in view.get("UVChannels") or []:
        ch = dict(ch)
        source = rebuilt_uv.get(int(ch.get("UVChannelIndex", 0)))
        if source is not None:
            ch["UVChannelData"] = source["UVChannelData"]
            ch["UVFacesData"] = source["UVFacesData"]
        ch.pop("UVChannelDataEdited", None)
        ch.pop("UVFacesDataEdited", None)
        uvs.append(ch)
    view["UVChannels"] = uvs

    table = list(rebuild.get("FaceSurfaceTable") or [])
    indices_raw = _to_bytes(rebuild.get("FaceSurfaceIndices"))
    indices = struct.unpack(f'>{len(indices_raw) // 2}H', indices_raw) if indices_raw else ()
    view["_FaceSurfaceIds"] = [table[i] for i in indices]
    return view


def _from_bytes(raw):
    """Field string for a raw buffer (the importer's decoders accept it)."""
    return FieldCodec.encode_field(raw, True)


_GX_WRAP = {0: 'EXTEND', 1: 'REPEAT', 2: 'MIRROR'}


def _surface_face_ranges(display_states):
    """Return list of (surface_id, start_face_idx, face_count) for draw states with faces.

    Includes ALL display-state types — not just Type-7 — because Type-1 (texture-bind)
    states can also carry primitive lists.  States that contribute zero faces are skipped
    (no material slot needed).  Returns None when any Type-7 state lacks FaceCount,
    signalling an old export that needs the legacy per-texture material path.
    """
    result = []
    cumulative = 0
    for ds_idx, ds in enumerate(display_states):
        fc = ds.get("FaceCount")
        if fc is None:
            if ds.get("DisplayStateId") == 7:
                return None  # Old Type-7 without FaceCount: fall back to legacy path
            continue         # Old non-Type-7 without FaceCount: assumed zero faces, skip
        if fc == 0:
            continue         # This state draws nothing; no material slot needed
        surface_id = ds.get("SurfaceId") or f"ds{ds_idx}"
        result.append((surface_id, cumulative, fc))
        cumulative += fc
    return result


def _surface_ranges_from_face_ids(face_surface_ids):
    """``(surface_id, start, count)`` runs of a per-face surface list, in the
    shape ``_surface_face_ranges`` returns; a surface may appear in several
    runs, and ``build_mesh`` gives it one material slot."""
    ranges = []
    for face_index, surface_id in enumerate(face_surface_ids):
        if ranges and ranges[-1][0] == surface_id:
            ranges[-1] = (surface_id, ranges[-1][1], ranges[-1][2] + 1)
        else:
            ranges.append((surface_id, face_index, 1))
    return ranges
