import bpy
import json
import os
import base64
import math
import struct
import mathutils
from functools import lru_cache
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper


def _to_bytes(data) -> bytes:
    """Decode binary data that is either a base64 string or a list of byte values."""
    if isinstance(data, list):
        return bytes(data)
    return base64.b64decode(data)


def _texture_file_map(texture_descriptors):
    """Return {TextureIndex: filename} from sluggie metadata.

    Falls back to the legacy numeric file name when TextureFileName is absent.
    """
    mapping = {}
    for tex in texture_descriptors or []:
        tex_idx = tex.get("TextureIndex")
        if tex_idx is None:
            continue
        mapping[tex_idx] = tex.get("TextureFileName") or f"{tex_idx}.png"
    return mapping


def _texture_file_map_from_sibling_sluggie(current_sluggie_path, model):
    """Return a fallback texture file map from a sibling .sluggie when needed.

    Some lowpoly (_L) exports have no local TextureDescriptors, but their sibling
    export folder still has the canonical hashed TextureFileName mapping.
    """
    current_dir = os.path.dirname(current_sluggie_path)
    parent_dir = os.path.dirname(current_dir)
    if not parent_dir or not os.path.isdir(parent_dir):
        return {}

    target_chunk = model.get("ChunkNumber")
    target_submesh_count = len(model.get("Submeshes", []))

    candidates = []
    for sibling_name in sorted(os.listdir(parent_dir)):
        sibling_dir = os.path.join(parent_dir, sibling_name)
        if not os.path.isdir(sibling_dir) or sibling_dir == current_dir:
            continue
        try:
            sluggies = [
                name for name in os.listdir(sibling_dir)
                if name.lower().endswith('.sluggie')
            ]
        except OSError:
            continue
        for slug_name in sluggies:
            candidates.append(os.path.join(sibling_dir, slug_name))

    best_map = {}
    best_score = -1
    for candidate_path in candidates:
        try:
            with open(candidate_path, 'r') as handle:
                candidate_root = json.load(handle)
        except (OSError, ValueError):
            continue
        candidate_model = candidate_root.get("SluggiesModel", {})
        candidate_desc = candidate_model.get("TextureDescriptors", [])
        candidate_map = _texture_file_map(candidate_desc)
        if not candidate_map:
            continue

        score = 0
        if candidate_model.get("ChunkNumber") == target_chunk:
            score += 4
        if len(candidate_model.get("Submeshes", [])) >= target_submesh_count:
            score += 2
        if candidate_model.get("TEXHeader"):
            score += 1
        if score > best_score:
            best_score = score
            best_map = candidate_map

    return best_map


def _compute_bone_absolute_matrices(bone_list):
    """Return a {BoneId: mathutils.Matrix} dict of absolute world-space transforms.

    For non-skinned submeshes the game stores vertices in bone-local space and
    applies bone.absolute_transform (= S@R@T accumulated up the parent chain in
    row-vector convention) as a bind-shape matrix to map them to world space.
    We replicate that here in Blender column-vector convention:
      game row-vector:   local = S_row @ R_row @ T_row
      Blender col-vector: local = T_col @ R_col @ S_col  (reversed order)
      absolute_blender = parent_abs_blender @ child_local_blender
    """
    bone_map = {b['BoneId']: b for b in bone_list}
    abs_mats = {}  # BoneId -> mathutils.Matrix

    # Topological pass: resolve parents before children.  Guard against cycles.
    remaining = list(bone_map.keys())
    for _ in range(len(remaining) + 1):
        still_remaining = []
        for bone_id in remaining:
            bone     = bone_map[bone_id]
            parent_id = bone.get('ParentBoneId')
            if parent_id is not None and parent_id not in abs_mats:
                still_remaining.append(bone_id)
                continue
            trans = bone['Translation']
            scale = bone['Scale']
            quat  = bone['Quaternion']   # stored as [-game_qw, game_qx, game_qy, game_qz]
            T = mathutils.Matrix.Translation(trans)
            # Negate w to recover game_qw; the stored negation was an artefact of the
            # row-vector SRT convention.  With correct w, to_matrix() gives R_col.
            R = mathutils.Quaternion((-quat[0], quat[1], quat[2], quat[3])).to_matrix().to_4x4()
            S = mathutils.Matrix([
                [scale[0], 0,       0,       0],
                [0,        scale[1], 0,      0],
                [0,        0,       scale[2], 0],
                [0,        0,       0,        1],
            ])
            local_mat = T @ R @ S
            if parent_id is not None:
                abs_mats[bone_id] = abs_mats[parent_id] @ local_mat
            else:
                abs_mats[bone_id] = local_mat
        remaining = still_remaining
        if not remaining:
            break

    return abs_mats


def _apply_nonskinned_transform(obj, submesh_index, bone_list, abs_bone_mats):
    """Set *obj*.matrix_world to the absolute transform of the non-skinned bone
    that owns *submesh_index*, if one exists.  No-op for skinned submeshes."""
    for bd in bone_list:
        if not bd.get('Skinned') and bd.get('GeoId') == submesh_index:
            mat = abs_bone_mats.get(bd['BoneId'])
            if mat is not None:
                obj.matrix_world = mat
            break


def decode_faces(submesh):
    """Decode base64 uint16 BE face data back to a list of [i,j,k] triplets."""
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

    positions = []
    normals = []
    for i in range(num_verts):
        off = i * stride
        comps = [
            struct.unpack_from(comp_fmt, raw, off + j * comp_size)[0] / divisor
            for j in range(comp_count)
        ]
        positions.append((comps[0], comps[1], comps[2]))
        if comp_count >= 6:
            normals.append((comps[3], comps[4], comps[5]))

    return positions, normals


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

    normals = []
    for i in range(num_normals):
        off = i * stride
        comps = [
            struct.unpack_from(comp_fmt, raw, off + j * comp_size)[0] / divisor
            for j in range(comp_count)
        ]
        normals.append((comps[0], comps[1], comps[2]))
    return normals


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


def add_facial_shape_keys(obj, submesh_index, facial_data, facial_edited=None):
    """Add facial expression keys; Blender Basis represents pose zero."""
    if not facial_data:
        return 0
    facial_objects = [
        entry for entry in facial_data.get("Objects", [])
        if entry.get("SubmeshIndex") == submesh_index
    ]
    if not facial_objects:
        return 0

    expression_objects = [
        facial_object for facial_object in facial_objects
        if facial_object.get("PoseCount", facial_data.get("PoseCount", 0)) > 1
    ]
    if not expression_objects:
        return 0
    quant_info = obj.get("VertexBufferQuantizeInfo", 0)
    edited_lookup = _facial_edit_lookup(facial_edited)
    basis_key = obj.shape_key_add(name="Basis", from_mix=False)

    for facial_object in facial_objects:
        object_index = facial_object.get("ObjectIndex")
        position = facial_object.get("Position", {})
        pose_zero_edit = edited_lookup.get((object_index, 0))
        if pose_zero_edit is None:
            continue
        indices = _facial_vertex_indices(position)
        coordinates = _decode_facial_pose(
            pose_zero_edit, quant_info,
            position.get("ComponentCount", 3),
            position.get("ComponentSize", 2),
        )
        if len(indices) != len(coordinates):
            continue
        for vertex_index, coordinate in zip(indices, coordinates):
            if vertex_index < len(basis_key.data):
                basis_key.data[vertex_index].co = coordinate

    shape_key_count = 0
    for facial_object in expression_objects:
        object_index = facial_object.get("ObjectIndex")
        position = facial_object.get("Position", {})
        indices = _facial_vertex_indices(position)
        original_poses = position.get("PoseData", [])
        pose_count = facial_object.get("PoseCount", facial_data.get("PoseCount", 0))
        for pose_index in range(1, pose_count):
            key = obj.shape_key_add(
                name=f"facial_object_{object_index}_pose_{pose_index}",
                from_mix=False,
            )
            key.value = 0.0
            position = facial_object.get("Position", {})
            pose_data = edited_lookup.get(
                (object_index, pose_index),
                original_poses[pose_index] if pose_index < len(original_poses) else None,
            )
            if pose_data is None:
                continue
            coordinates = _decode_facial_pose(
                pose_data, quant_info,
                position.get("ComponentCount", 3),
                position.get("ComponentSize", 2),
            )
            if len(indices) != len(coordinates):
                continue
            for vertex_index, coordinate in zip(indices, coordinates):
                if vertex_index < len(key.data):
                    basis = basis_key.data[vertex_index].co
                    key.data[vertex_index].co = tuple(
                        basis[axis] + coordinate[axis] for axis in range(3)
                    )
            shape_key_count += 1

    obj["FacialShapeKeyCount"] = shape_key_count
    return shape_key_count


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

    coords = []
    for i in range(num_coords):
        off = i * stride
        s = struct.unpack_from(comp_fmt, raw, off)[0] / divisor
        t = struct.unpack_from(comp_fmt, raw, off + comp_size)[0] / divisor
        coords.append((s, t))

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


def _primlist_position_indices(prim_raw: bytes, active_descriptors: list) -> set:
    """Parse a raw GX primitive stream and return the set of position attribute
    indices referenced by any vertex in any primitive block.

    Implemented inline so the addon stays independent of SluggiesTools/drawlist.py.
    Primitive types (TRIANGLES / TRIANGLE_STRIP / QUADS / etc.) are all handled
    identically — only the vertex count and per-vertex byte stride matter.
    """
    if not prim_raw or not active_descriptors:
        return set()

    # Locate 'position' within the per-vertex attribute stream.
    pos_byte_off = 0
    pos_size     = 0
    byte_off     = 0
    for d in active_descriptors:
        if d.get('key') == 'position':
            pos_byte_off = byte_off
            pos_size     = d.get('index_size', 2)
            break
        byte_off += d.get('index_size', 2)
    if pos_size == 0:
        return set()

    vertex_stride = sum(d.get('index_size', 2) for d in active_descriptors)
    indices = set()
    pos = 0
    while pos < len(prim_raw) and prim_raw[pos] != 0:
        pos += 1  # skip primitive-type opcode
        if pos + 2 > len(prim_raw):
            break
        vert_count = (prim_raw[pos] << 8) | prim_raw[pos + 1]
        pos += 2
        for _ in range(vert_count):
            if pos + vertex_stride > len(prim_raw):
                break
            idx = int.from_bytes(
                prim_raw[pos + pos_byte_off : pos + pos_byte_off + pos_size], 'big'
            )
            indices.add(idx)
            pos += vertex_stride
    return indices


def _apply_drawlist_vcol(mesh, display_states):
    """Create a 'drawlist_regions' vertex colour attribute on *mesh* that
    visualises which GX draw-list block references each vertex.

    Only Type-7 display states that carry both PrimListData and VertexStreamLayout
    are considered.  Their grayscale brightness is spaced evenly across [step, 1]
    where step = 1 / total_blocks, so the first block is the darkest non-black
    shade and the last block is pure white.  Vertices not referenced by any
    draw list remain black (0, 0, 0).
    """
    blocks = [
        ds for ds in display_states
        if ds.get('DisplayStateId') == 7
        and ds.get('PrimListData') is not None
        and ds.get('VertexStreamLayout')
    ]
    if not blocks:
        return

    total = len(blocks)
    step  = 1.0 / total

    n_verts = len(mesh.vertices)
    vert_brightness = [0.0] * n_verts

    for i, ds in enumerate(blocks):
        brightness = (i + 1) * step
        prim_raw   = _to_bytes(ds['PrimListData'])
        for vi in _primlist_position_indices(prim_raw, ds['VertexStreamLayout']):
            if vi < n_verts:
                vert_brightness[vi] = brightness

    layer = mesh.color_attributes.new(
        name='drawlist_regions', type='FLOAT_COLOR', domain='POINT'
    )
    for vi in range(n_verts):
        v = vert_brightness[vi]
        layer.data[vi].color = (v, v, v, 1.0)


def _has_edited_data(submesh):
    """Return True when *submesh* contains any edited mesh data."""
    vb = submesh.get("VertexBuffer") or {}
    return bool(vb.get("VertexBufferDataEdited") or submesh.get("FacesDataEdited"))


def _edited_submesh_view(submesh):
    """Return a shallow copy of *submesh* with all *Edited fields promoted to
    their primary counterparts so existing decode_* helpers can be reused
    without modification."""
    view = dict(submesh)

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

    return view


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


def _create_material(mat_name, uv_layer_name, image, wrap_s=1):
    """Build a UV Map -> Mapping -> Image Texture -> Diffuse BSDF -> Material Output node tree."""
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    out_node = nodes.new('ShaderNodeOutputMaterial')
    out_node.location = (800, 0)

    bsdf_node = nodes.new('ShaderNodeBsdfDiffuse')
    bsdf_node.location = (550, 0)

    tex_node = nodes.new('ShaderNodeTexImage')
    tex_node.location = (200, 0)
    if image:
        tex_node.image = image
    tex_node.extension = _GX_WRAP.get(wrap_s, 'REPEAT')

    mapping_node = nodes.new('ShaderNodeMapping')
    mapping_node.location = (-150, 0)

    uv_node = nodes.new('ShaderNodeUVMap')
    uv_node.location = (-450, 0)
    uv_node.uv_map = uv_layer_name

    links.new(uv_node.outputs['UV'], mapping_node.inputs['Vector'])
    links.new(mapping_node.outputs['Vector'], tex_node.inputs['Vector'])
    links.new(tex_node.outputs['Color'], bsdf_node.inputs['Color'])
    links.new(bsdf_node.outputs['BSDF'], out_node.inputs['Surface'])

    return mat


@lru_cache(maxsize=None)
def _resolve_texture_image_path(sluggie_dir, tex_file):
    """Find a texture PNG for this import, falling back to sibling export folders.

    Lowpoly exports in this workspace often live in a sibling folder without a
    local tex/ directory, while the paired highpoly export keeps the PNG cache.
    """
    candidate_dirs = [os.path.join(sluggie_dir, 'tex')]

    parent_dir = os.path.dirname(sluggie_dir)
    if parent_dir and os.path.isdir(parent_dir):
        sibling_tex_dir = os.path.join(parent_dir, 'tex')
        candidate_dirs.append(sibling_tex_dir)
        try:
            for sibling_name in sorted(os.listdir(parent_dir)):
                sibling_dir = os.path.join(parent_dir, sibling_name)
                if os.path.isdir(sibling_dir):
                    candidate_dirs.append(os.path.join(sibling_dir, 'tex'))
        except OSError:
            pass

    for tex_dir in candidate_dirs:
        img_path = os.path.join(tex_dir, tex_file)
        if os.path.exists(img_path):
            return img_path
    return None


def build_mesh(name, positions, normals, faces, vb_meta, collection,
               uv_channels=None, color_channels=None,
               face_texture_indices=None, sluggie_dir=None,
               submesh_meta=None, prebuilt_materials=None,
               texture_file_map=None):
    """Create a Blender mesh object from a vertex list and link it to *collection*."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions, [], faces)
    mesh.update()

    n_normals = len(normals)
    if normals and n_normals == len(positions) and len(faces):
        max_face_idx = max(i for face in faces for i in face)
        if max_face_idx < n_normals:
            #mesh.use_auto_smooth = True
            normals_per_loop = [normals[i] for face in faces for i in face]
            mesh.normals_split_custom_set(normals_per_loop)
        # else: face indices exceed vertex buffer — skip custom normals silently

    # Per-loop normals from the standalone NormalBuffer (non-skinned meshes):
    # the donor values become custom split normals so unedited loops round-trip
    # unchanged through the Blender export (plan 3.3, item 1) — mirrors the
    # interleaved CompCount==6 path above, using NormalFacesData per-loop
    # indices instead of position indices.
    if not mesh.has_custom_normals and faces:
        normal_buffer = submesh_meta.get("NormalBuffer") if submesh_meta else None
        if (
            normal_buffer
            and normal_buffer.get("NormalBufferData")
            and normal_buffer.get("NormalFacesData")
        ):
            buffer_normals = decode_normal_buffer(normal_buffer)
            nf_raw = _to_bytes(normal_buffer["NormalFacesData"])
            nf_count = len(nf_raw) // 2
            nf_flat = list(struct.unpack(f'>{nf_count}H', nf_raw)) if nf_count else []
            loop_count = sum(len(f) for f in faces)
            if nf_count == loop_count and max(nf_flat) < len(buffer_normals):
                mesh.normals_split_custom_set([buffer_normals[i] for i in nf_flat])
            else:
                _report(
                    f"WARNING: {name}: NormalFacesData has {nf_count} loop indices "
                    f"but the mesh has {loop_count} loops; per-loop normals skipped"
                )

    uv_layer_names = {}  # UVChannelIndex -> actual Blender layer name
    if uv_channels:
        _used_uv_names = set()
        for ch_ind, uv_channel in enumerate(uv_channels):
            coords, uv_faces = decode_uv_channel(uv_channel)
            if not coords or not uv_faces:
                continue
            # Use palette name as UV layer name; fall back to "uv<index>".
            # Deduplicate: when two channels share the same PaletteName the
            # second one falls back to "uv<enumerate-index>" so both layers
            # get unique names and can be looked up correctly on re-export.
            raw_name = uv_channel.get("PaletteName") or f"uv{ch_ind}"
            layer_name = raw_name if raw_name not in _used_uv_names else f"uv{ch_ind}"
            _used_uv_names.add(layer_name)
            uv_layer_names[uv_channel.get("UVChannelIndex", ch_ind)] = layer_name
            uv_layer = mesh.uv_layers.new(name=layer_name)
            for poly in mesh.polygons:
                face_idx = poly.index
                if face_idx >= len(uv_faces):
                    continue
                uv_tri = uv_faces[face_idx]
                for loop_offset, loop_idx in enumerate(poly.loop_indices):
                    uv_idx = uv_tri[loop_offset % 3]
                    if uv_idx < len(coords):
                        s, t = coords[uv_idx]
                        # GX V increases downward; flip to match Blender convention
                        uv_layer.data[loop_idx].uv = (s, 1.0 - t)

    if color_channels:
        for color_channel in color_channels:
            ch_idx = color_channel.get("ColorChannelIndex", 0)
            colors, color_faces = decode_color_channel(color_channel)
            if not colors or not color_faces:
                continue
            layer_name = f"color{ch_idx}"
            vcol_layer = mesh.color_attributes.new(name=layer_name, type='BYTE_COLOR', domain='CORNER')
            for poly in mesh.polygons:
                face_idx = poly.index
                if face_idx >= len(color_faces):
                    continue
                col_tri = color_faces[face_idx]
                for loop_offset, loop_idx in enumerate(poly.loop_indices):
                    col_idx = col_tri[loop_offset % 3]
                    if col_idx < len(colors):
                        vcol_layer.data[loop_idx].color = colors[col_idx]

    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    # Store vertex buffer metadata as custom properties so the exporter can
    # match this object back to the correct submesh and re-quantize correctly.
    if vb_meta is not None:
        for prop in ("VertexBufferOffset", "VertexBufferLength",
                     "VertexBufferCompCount", "VertexBufferQuantizeInfo"):
            if prop in vb_meta:
                obj[prop] = vb_meta[prop]

    if submesh_meta is not None:
        # Compute once — needed for both shader-mode properties and material creation.
        display_states = submesh_meta.get("DisplayStates", [])
        surface_ranges = _surface_face_ranges(display_states)

        # Object-level shader mode properties are only written for the legacy path
        # (old exports without FaceCount).  When per-surface materials exist the
        # shader mode lives on mat["ShaderMode"] instead.
        if surface_ranges is None:
            for ds_idx, ds in enumerate(display_states):
                if ds.get("DisplayStateId") != 7:
                    continue
                surface_id = ds.get("SurfaceId") or f"ds{ds_idx}"
                prop_val = f"DS_{surface_id}_ShaderMode"
                obj[prop_val] = ds.get("ShaderMode", "")
                obj.id_properties_ui(prop_val).update(
                    description=(
                        f"Surface {surface_id} shader mode (legacy — re-export to get per-surface materials). "
                        "Known 4-char names: Spec=specular  Shdw=no-specular  SpRf=specular-reflection  "
                        "RhSp/LhSp=right/left-hand-specular  GhSp=ghost."
                    ))

        if display_states:
            _apply_drawlist_vcol(mesh, display_states)
    else:
        display_states = []
        surface_ranges = None

    # Store UV channel material binding metadata as custom properties
    if uv_channels:
        for uv_channel in uv_channels:
            ch_ind = uv_channel.get("UVChannelIndex", 0)
            prefix = f"UV{ch_ind}_"
            tex_idx = uv_channel.get("TextureIndex")
            wrap_s  = uv_channel.get("WrapS")
            wrap_t  = uv_channel.get("WrapT")
            if tex_idx is not None:
                obj[prefix + "TextureIndex"] = tex_idx
                obj.id_properties_ui(prefix + "TextureIndex").update(
                    description=f"TPL texture index bound to UV channel {ch_ind}")
            if wrap_s is not None:
                obj[prefix + "WrapS"] = wrap_s
                obj.id_properties_ui(prefix + "WrapS").update(
                    description=f"GX S-axis wrap mode for UV channel {ch_ind}")
            if wrap_t is not None:
                obj[prefix + "WrapT"] = wrap_t
                obj.id_properties_ui(prefix + "WrapT").update(
                    description=f"GX T-axis wrap mode for UV channel {ch_ind}")

    # Create Blender materials and assign per-face material indices.
    # New path: one material per Type-7 draw state (SurfaceId-keyed).
    # Legacy fallback: one material per unique texture index for old exports.
    if surface_ranges is not None and uv_channels and face_texture_indices:
        # Build texture-index → UV channel map for node tree setup
        tex_to_uv = {}
        for uv_ch in uv_channels:
            idx = uv_ch.get('TextureIndex')
            if idx is not None and idx not in tex_to_uv:
                tex_to_uv[idx] = uv_ch

        mat_slot = {}  # surface_id -> material slot index
        for slot_idx, (surface_id, start_fi, fc) in enumerate(surface_ranges):
            fi_tex = face_texture_indices[start_fi] if start_fi < len(face_texture_indices) else 0
            uv_ch = tex_to_uv.get(fi_tex) or (uv_channels[0] if uv_channels else None)
            ch_ind = uv_ch.get('UVChannelIndex', 0) if uv_ch else 0
            layer_name = uv_layer_names.get(ch_ind) or (uv_ch.get('PaletteName') if uv_ch else None) or f'uv{ch_ind}'
            wrap_s = uv_ch.get('WrapS', 1) if uv_ch else 1
            wrap_t = uv_ch.get('WrapT', 1) if uv_ch else 1

            if prebuilt_materials is not None and surface_id in prebuilt_materials:
                mat = prebuilt_materials[surface_id]
            elif sluggie_dir is not None:
                tex_file = (texture_file_map or {}).get(fi_tex, f'{fi_tex}.png')
                img_path = _resolve_texture_image_path(sluggie_dir, tex_file)
                image = bpy.data.images.load(img_path, check_existing=True) if img_path else None
                mat = _create_material(f'{name}_{surface_id}', layer_name, image, wrap_s)
                # Store surface identity and editable properties on the material
                ds_entry = next((ds for ds in display_states if ds.get("SurfaceId") == surface_id), {})
                mat["SurfaceId"] = surface_id
                mat["TextureIndex"] = fi_tex
                mat["WrapS"] = wrap_s
                mat["WrapT"] = wrap_t
                mat.id_properties_ui("SurfaceId").update(
                    description="Stable draw-state identity (sm{n}_ds{n}). Do not delete — export resolves this material by SurfaceId.")
                mat.id_properties_ui("TextureIndex").update(
                    description="TPL texture slot index active for this draw state.")
                mat.id_properties_ui("WrapS").update(
                    description="GX S-axis wrap mode for this draw state (0=extend, 1=repeat, 2=mirror).")
                mat.id_properties_ui("WrapT").update(
                    description="GX T-axis wrap mode for this draw state (0=extend, 1=repeat, 2=mirror).")
                if ds_entry.get("DisplayStateId") == 7:
                    mat["ShaderMode"] = ds_entry.get("ShaderMode", "")
                    mat.id_properties_ui("ShaderMode").update(
                        description="Type-7 shader mode. Known names: Spec=specular  Shdw=no-specular  SpRf=specular-reflection  RhSp/LhSp=right/left-hand-specular  GhSp=ghost.")
            else:
                continue

            mat_slot[surface_id] = slot_idx
            obj.data.materials.append(mat)

        # Assign faces to their draw-state material by cumulative face range
        for surface_id, start_fi, fc in surface_ranges:
            slot = mat_slot.get(surface_id, 0)
            for fi in range(start_fi, min(start_fi + fc, len(mesh.polygons))):
                mesh.polygons[fi].material_index = slot

    elif uv_channels and face_texture_indices and sluggie_dir is not None:
        # Legacy: per-texture materials (old exports without FaceCount)
        tex_to_uv = {}
        for uv_ch in uv_channels:
            idx = uv_ch.get('TextureIndex')
            if idx is not None and idx not in tex_to_uv:
                tex_to_uv[idx] = uv_ch

        unique_tex = sorted(set(face_texture_indices))
        mat_slot = {}  # TextureIndex -> material slot index
        for slot_idx, tex_idx in enumerate(unique_tex):
            mat_slot[tex_idx] = slot_idx
            uv_ch = tex_to_uv.get(tex_idx) or uv_channels[0]
            ch_ind = uv_ch.get('UVChannelIndex', 0)
            layer_name = uv_layer_names.get(ch_ind) or uv_ch.get('PaletteName') or f'uv{ch_ind}'
            wrap_s = uv_ch.get('WrapS', 1)
            tex_file = (texture_file_map or {}).get(tex_idx, f'{tex_idx}.png')
            img_path = _resolve_texture_image_path(sluggie_dir, tex_file)
            image = bpy.data.images.load(img_path, check_existing=True) if img_path else None
            mat = _create_material(f'{name}_mat{tex_idx}', layer_name, image, wrap_s)
            obj.data.materials.append(mat)

        for poly in mesh.polygons:
            fi = poly.index
            tex_idx = face_texture_indices[fi] if fi < len(face_texture_indices) else unique_tex[0]
            poly.material_index = mat_slot.get(tex_idx, 0)

    elif prebuilt_materials is not None and face_texture_indices:
        # Legacy prebuilt: reuse by tex_idx (old format, int keys)
        available_tex = sorted(t for t in set(face_texture_indices) if t in prebuilt_materials)
        mat_slot = {}
        for slot_idx, tex_idx in enumerate(available_tex):
            mat_slot[tex_idx] = slot_idx
            obj.data.materials.append(prebuilt_materials[tex_idx])

        fallback_slot = 0
        for poly in mesh.polygons:
            fi = poly.index
            tex_idx = face_texture_indices[fi] if fi < len(face_texture_indices) else (available_tex[0] if available_tex else 0)
            poly.material_index = mat_slot.get(tex_idx, fallback_slot)

    return obj


def build_armature(name, bone_list, collection):
    """Create a Blender Armature from a BoneHierarchy list and link it to *collection*.

    Bone names are ``bone_<id>``.  Tails are aimed at the first child's head
    when available; otherwise offset slightly along global Z so bones are
    visible in the viewport.  Returns the armature object.
    """
    arm_data = bpy.data.armatures.new(name + "_arm")
    arm_obj  = bpy.data.objects.new(name + "_arm", arm_data)
    collection.objects.link(arm_obj)

    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm_data.edit_bones

    bone_id_to_name = {}
    for bd in bone_list:
        eb = edit_bones.new(f"bone_{bd['BoneId']}")
        h = bd['HeadPosition']
        eb.head = (h[0], h[1], h[2])
        # Placeholder tail — overridden below when a child is found
        eb.tail = (h[0], h[1], h[2] + 0.05)
        bone_id_to_name[bd['BoneId']] = eb.name

    # Collect the first child head for each parent so we can aim tails
    first_child_head = {}
    for bd in bone_list:
        pid = bd['ParentBoneId']
        if pid is not None and pid not in first_child_head:
            first_child_head[pid] = bd['HeadPosition']

    for bd in bone_list:
        eb = edit_bones.get(bone_id_to_name.get(bd['BoneId'], ''))
        if eb is None:
            continue
        # Set parent relationship
        pid = bd['ParentBoneId']
        if pid is not None:
            parent_eb = edit_bones.get(bone_id_to_name.get(pid, ''))
            if parent_eb:
                eb.parent = parent_eb
        # Aim tail towards first child when available
        if bd['BoneId'] in first_child_head:
            ch = first_child_head[bd['BoneId']]
            eb.tail = (ch[0], ch[1], ch[2])

    bpy.ops.object.mode_set(mode='OBJECT')
    arm_obj.show_in_front = True
    arm_obj.display_type = 'WIRE'
    arm_obj.rotation_euler[0] = math.pi / 2
    bpy.context.view_layer.objects.active = prev_active
    return arm_obj


def _apply_import_rotation(obj):
    obj.matrix_world = obj.matrix_world @ mathutils.Matrix.Rotation(math.pi / 2, 4, 'X')


def add_vertex_groups(obj, submesh_index, bone_list, arm_obj):
    """Add vertex groups from BoneHierarchy for *submesh_index* and attach an
    Armature modifier pointing at *arm_obj*."""
    for bd in bone_list:
        for entry in bd.get('VertexInfluences', []):
            if entry['SubmeshIndex'] != submesh_index:
                continue
            group_name = f"bone_{bd['BoneId']}"
            vg = obj.vertex_groups.get(group_name)
            if vg is None:
                vg = obj.vertex_groups.new(name=group_name)
            num_verts = len(obj.data.vertices)
            raw = _to_bytes(entry['Influences'])
            for v_idx, weight in struct.iter_unpack('>Hf', raw):
                if v_idx < num_verts:
                    vg.add([v_idx], weight, 'REPLACE')

    mod = obj.modifiers.new(name="Armature", type='ARMATURE')
    mod.object = arm_obj


class SLUGGIES_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "sluggies.import_json"
    bl_label = "Import Sluggers intermediate"
    bl_description = "Import a Sluggers intermediate JSON file"
    bl_options = {"UNDO"}

    filename_ext = ".sluggie"
    filter_glob: StringProperty(default="*.sluggie", options={"HIDDEN"}) # type: ignore[valid-type]

    def execute(self, context):
        with open(self.filepath, 'r') as f:
            data = json.load(f)

        model = data["SluggiesModel"]
        model_number = model["ChunkNumber"]
        model_offset_hex = model["ModelOffset"]
        submeshes = model.get("Submeshes", [])
        texture_file_map = _texture_file_map(model.get("TextureDescriptors", []))
        sluggie_dir = os.path.dirname(self.filepath)
        if not texture_file_map:
            texture_file_map = _texture_file_map_from_sibling_sluggie(self.filepath, model)

        collection = context.collection

        # Build armature first so mesh objects can reference it immediately
        bone_list = model.get("BoneHierarchy")
        arm_obj = None
        abs_bone_mats = {}
        if bone_list:
            base_name = f"{model_number}_{model_offset_hex}"
            arm_obj = build_armature(base_name, bone_list, collection)
            abs_bone_mats = _compute_bone_absolute_matrices(bone_list)

        imported = 0
        for i, submesh in enumerate(submeshes):
            vb = submesh.get("VertexBuffer")
            if not vb:
                continue
            positions, normals = decode_vertex_buffer(vb)
            faces = decode_faces(submesh)
            uv_channels = submesh.get("UVChannels", [])
            color_channels = submesh.get("ColorChannels", [])
            face_texture_indices = decode_face_texture_indices(submesh, len(faces))
            mesh_name = f"{model_number}_{model_offset_hex}_submesh{i}"
            obj = build_mesh(mesh_name, positions, normals, faces, vb, collection,
                             uv_channels, color_channels,
                             face_texture_indices=face_texture_indices, sluggie_dir=sluggie_dir,
                             submesh_meta=submesh, texture_file_map=texture_file_map)
            if arm_obj is not None:
                add_vertex_groups(obj, i, bone_list, arm_obj)
                obj.parent = arm_obj
            else:
                _apply_import_rotation(obj)
            if bone_list:
                _apply_nonskinned_transform(obj, i, bone_list, abs_bone_mats)
            add_facial_shape_keys(
                obj, i, model.get("FacialPoseData"), model.get("FacialPoseDataEdited")
            )
            imported += 1

            # Import the edited version of this submesh when one exists
            if _has_edited_data(submesh):
                ev = _edited_submesh_view(submesh)
                ev_vb = ev.get("VertexBuffer")
                if ev_vb:
                    edit_positions, edit_normals = decode_vertex_buffer(ev_vb)
                    edit_faces = decode_faces(ev)
                    edit_uv_channels = ev.get("UVChannels", [])
                    edit_color_channels = ev.get("ColorChannels", [])
                    edit_fti = decode_face_texture_indices(ev, len(edit_faces))

                    # Collect materials from the original mesh for reuse.
                    # New format: keyed by SurfaceId. Legacy format: keyed by tex_idx (int).
                    orig_materials = {}
                    for slot in obj.material_slots:
                        mat = slot.material
                        if mat is None:
                            continue
                        sid = mat.get("SurfaceId")
                        if sid:
                            orig_materials[sid] = mat
                        else:
                            try:
                                orig_materials[int(mat.name.rsplit('_mat', 1)[1])] = mat
                            except (IndexError, ValueError):
                                pass

                    edit_obj = build_mesh(
                        f"{mesh_name}_edit",
                        edit_positions, edit_normals, edit_faces,
                        vb, collection,
                        edit_uv_channels, edit_color_channels,
                        face_texture_indices=edit_fti,
                        sluggie_dir=None,
                        submesh_meta=submesh,
                        prebuilt_materials=orig_materials or None,
                        texture_file_map=texture_file_map,
                    )
                    if arm_obj is not None:
                        add_vertex_groups(edit_obj, i, bone_list, arm_obj)
                        edit_obj.parent = arm_obj
                    else:
                        _apply_import_rotation(edit_obj)
                    if bone_list:
                        _apply_nonskinned_transform(edit_obj, i, bone_list, abs_bone_mats)
                    add_facial_shape_keys(
                        edit_obj, i, model.get("FacialPoseData"),
                        model.get("FacialPoseDataEdited")
                    )
                    imported += 1

        context.view_layer.update()
        self.report({"INFO"}, f"Imported {imported} submesh(es) from {self.filepath}")
        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(SLUGGIES_OT_import.bl_idname, text="Sluggers intermediate (.sluggie)")


def register():
    bpy.utils.register_class(SLUGGIES_OT_import)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(SLUGGIES_OT_import)
