"""Pure-Python encoding for custom submeshes (PLAN_AddSubmesh.md Phase 6 step 2).

bpy-free, like HostBones.py and TemplateSources.py, so the geometry and
quantization rules can be unit-tested without Blender. ExportSluggies.py
reads the Blender scene and hands plain tuples/lists to the functions here.
"""

import base64
import math
import struct
from dataclasses import dataclass

try:  # inside Blender this module is part of the addon package
    from . import TemplateSources
except ImportError:  # imported flat by the unit tests
    import TemplateSources


@dataclass(frozen=True)
class Triangle:
    """One exported triangle: Blender vertex indices, the loop (face-corner)
    indices they came from, and the source polygon."""
    vertices: tuple
    loops: tuple
    polygon_index: int


def triangles_from_loop_triangles(loop_triangles):
    """Step 2.1: triangulate without touching the scene.

    *loop_triangles* is ``mesh.loop_triangles`` (or anything shaped like it:
    ``.vertices``, ``.loops``, ``.polygon_index``). Blender already splits
    every n-gon into these, so reading them triangulates the export while the
    user's mesh keeps its quads and n-gons. Winding is Blender's own, the
    same order ``encode_mesh_hammerspace`` writes for donor ``FacesData``.
    """
    return [
        Triangle(
            vertices=tuple(int(v) for v in tri.vertices),
            loops=tuple(int(loop) for loop in tri.loops),
            polygon_index=int(tri.polygon_index),
        )
        for tri in loop_triangles
    ]


def compact_vertex_indices(triangles):
    """Keep only the vertices the triangles reference.

    Loose vertices and edges have no triangle and would only use up position
    slots (a `rigid:` template can fix positions to u8 indices). Referenced
    vertices keep their relative Blender order. Returns
    ``(source_vertex_indices, remapped_faces)``: the Blender vertex index for
    each exported position, and each triangle's vertex triplet rewritten to
    index into that list.
    """
    used = sorted({v for tri in triangles for v in tri.vertices})
    new_index = {old: new for new, old in enumerate(used)}
    faces = [tuple(new_index[v] for v in tri.vertices) for tri in triangles]
    return used, faces


# ---------------------------------------------------------------------------
# Step 2.2: world position is authoritative
# ---------------------------------------------------------------------------
# Matrices are 4x4 nested lists indexed [row][column] and applied to column
# vectors, the same convention as mathutils (``M @ v``), so Blender matrices
# convert with ``[list(row) for row in matrix]``.

IDENTITY4 = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0),
             (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))

_SINGULAR_EPSILON = 1e-12


def mat4_mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def mat4_inverse(m):
    """General 4x4 inverse (Gauss-Jordan with partial pivoting). Raises
    ValueError for a singular matrix, e.g. an object scaled to zero."""
    work = [[float(v) for v in row] + [1.0 if r == c else 0.0 for c in range(4)]
            for r, row in enumerate(m)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda r: abs(work[r][col]))
        if abs(work[pivot][col]) < _SINGULAR_EPSILON:
            raise ValueError('matrix is singular (zero scale on an axis?)')
        work[col], work[pivot] = work[pivot], work[col]
        scale = work[col][col]
        work[col] = [v / scale for v in work[col]]
        for r in range(4):
            if r != col and work[r][col] != 0.0:
                factor = work[r][col]
                work[r] = [rv - factor * cv for rv, cv in zip(work[r], work[col])]
    return [row[4:] for row in work]


def transform_point(m, p):
    return tuple(
        m[r][0] * p[0] + m[r][1] * p[1] + m[r][2] * p[2] + m[r][3]
        for r in range(3)
    )


def mat3_determinant(m):
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def normal_matrix(m):
    """Inverse-transpose of *m*'s upper 3x3, for transforming normals
    (step 2.2 item 5). Raises ValueError when the 3x3 is singular."""
    det = mat3_determinant(m)
    if abs(det) < _SINGULAR_EPSILON:
        raise ValueError('matrix is singular (zero scale on an axis?)')
    # inverse-transpose == cofactor matrix / determinant
    cof = [[0.0] * 3 for _ in range(3)]
    for r in range(3):
        for c in range(3):
            r1, r2 = [i for i in range(3) if i != r]
            c1, c2 = [i for i in range(3) if i != c]
            minor = m[r1][c1] * m[r2][c2] - m[r1][c2] * m[r2][c1]
            cof[r][c] = (-minor if (r + c) % 2 else minor) / det
    return cof


def quaternion_matrix(w, x, y, z):
    """Rotation matrix of a unit quaternion, as mathutils ``to_matrix`` builds it."""
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y), 0.0],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x), 0.0],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y), 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def bone_local_matrix(bone):
    """``T @ R @ S`` for one BoneHierarchy entry, the same local bind matrix
    ``ImportSluggies._compute_bone_absolute_matrices`` builds. ``Quaternion``
    is stored as ``[-qw, qx, qy, qz]``, so w is negated back first."""
    tx, ty, tz = bone['Translation']
    sx, sy, sz = bone['Scale']
    qw, qx, qy, qz = bone['Quaternion']
    rotation = quaternion_matrix(-qw, qx, qy, qz)
    translation = [[1.0, 0.0, 0.0, tx], [0.0, 1.0, 0.0, ty],
                   [0.0, 0.0, 1.0, tz], [0.0, 0.0, 0.0, 1.0]]
    scale = [[sx, 0.0, 0.0, 0.0], [0.0, sy, 0.0, 0.0],
             [0.0, 0.0, sz, 0.0], [0.0, 0.0, 0.0, 1.0]]
    return mat4_mul(mat4_mul(translation, rotation), scale)


def bone_absolute_matrices(bone_hierarchy):
    """``{BoneId: absolute bind matrix}`` in armature space, a bpy-free port of
    ``ImportSluggies._compute_bone_absolute_matrices`` (the matrix the
    importer places rigid submeshes with). Rest/bind data only: nothing here
    can see a pose or animation frame."""
    bones = {int(b['BoneId']): b for b in bone_hierarchy}
    absolute = {}
    remaining = list(bones)
    for _ in range(len(remaining) + 1):
        pending = []
        for bone_id in remaining:
            bone = bones[bone_id]
            parent_id = bone.get('ParentBoneId')
            if parent_id is not None and parent_id not in absolute:
                pending.append(bone_id)
                continue
            local = bone_local_matrix(bone)
            absolute[bone_id] = local if parent_id is None else mat4_mul(absolute[parent_id], local)
        remaining = pending
        if not remaining:
            break
    return absolute


def object_to_bone_matrix(obj_world, arm_world, host_bind):
    """Combined matrix taking object-local vertex coordinates to host-bone
    bind-local space (step 2.2 items 3-4):
    ``p_bone = host_bind^-1 @ arm_world^-1 @ obj_world @ v.co``.

    ``obj_world`` already carries Object Mode location/rotation/scale, delta
    transforms, parenting and constraints, and dividing out ``arm_world``
    cancels the importer's 90 degree X rotation and any move of the whole
    armature."""
    return mat4_mul(mat4_mul(mat4_inverse(host_bind), mat4_inverse(arm_world)), obj_world)


def keep_offset_world_matrix(obj_world, arm_world, b_old, b_new):
    """New object world matrix for Reassign to new bone's 'Keep offset to
    bone' placement (PLAN_EditRigidMeshes.md Phase 6 step 2): the mesh keeps
    the same offset from its host bone, moving from bind matrix ``b_old`` to
    ``b_new`` (both in armature space, rest pose):
    ``M_obj' = A @ B_new @ B_old^-1 @ A^-1 @ M_obj``.

    Bytes are unchanged by this placement (only ``GeoIdEdited`` moves), so
    this is viewport-only: it keeps Blender's rest-pose preview matching what
    the importer would show after a re-import onto the new bone.
    """
    m = mat4_mul(arm_world, b_new)
    m = mat4_mul(m, mat4_inverse(b_old))
    m = mat4_mul(m, mat4_inverse(arm_world))
    return mat4_mul(m, obj_world)


def transform_normals(normals, m):
    """Transform normals by *m*'s inverse-transpose and renormalize. A normal
    that collapses to zero length (a degenerate input) comes back as +Z."""
    nm = normal_matrix(m)
    result = []
    for n in normals:
        t = [nm[r][0] * n[0] + nm[r][1] * n[1] + nm[r][2] * n[2] for r in range(3)]
        length = (t[0] * t[0] + t[1] * t[1] + t[2] * t[2]) ** 0.5
        result.append((t[0] / length, t[1] / length, t[2] / length) if length > 1e-12 else (0.0, 0.0, 1.0))
    return result


def is_mirrored(m):
    """True when *m* flips handedness (negative determinant, e.g. a negative
    scale on one axis), which turns every face inside out."""
    return mat3_determinant(m) < 0.0


def orient_triangles(triangles, mirrored):
    """Step 2.2 item 6: reverse every triangle's winding when the transform is
    mirrored, keeping vertices and loops paired."""
    if not mirrored:
        return list(triangles)
    return [
        Triangle(tri.vertices[::-1], tri.loops[::-1], tri.polygon_index)
        for tri in triangles
    ]


def unsupported_modifier_names(modifiers):
    """Step 2.2 item 7: names of modifiers export does not evaluate. Takes
    ``(name, type)`` pairs; only ``ARMATURE`` is expected on a custom submesh."""
    return [name for name, kind in modifiers if kind != 'ARMATURE']


@dataclass
class BoneLocalGeometry:
    """A custom submesh in host-bone bind-local space, ready to quantize.

    ``positions[i]`` is the position of exported vertex ``i``;
    ``source_vertices[i]`` is its Blender vertex index. ``faces`` index into
    ``positions`` and are already wound for the game; ``triangles`` are the
    same faces with their Blender loops, for per-loop attributes.
    ``to_bone`` is the combined object-to-bone matrix (normals use its
    inverse-transpose)."""
    positions: list
    source_vertices: list
    faces: list
    triangles: list
    to_bone: list
    mirrored: bool


def bone_local_geometry(vertex_cos, triangles, obj_world, arm_world, host_bind):
    """Steps 2.2 items 3-6 on plain data: undeformed object-local vertex
    coordinates plus the rest-pose object/armature world matrices and the
    host bone's bind matrix in, bone-local positions with game winding out."""
    to_bone = object_to_bone_matrix(obj_world, arm_world, host_bind)
    if abs(mat3_determinant(to_bone)) < _SINGULAR_EPSILON:
        raise ValueError(
            'object is scaled to zero on an axis; its faces and normals collapse. '
            'Give it a non-zero scale.'
        )
    mirrored = is_mirrored(to_bone)
    oriented = orient_triangles(triangles, mirrored)
    source_vertices, faces = compact_vertex_indices(oriented)
    positions = [transform_point(to_bone, vertex_cos[i]) for i in source_vertices]
    return BoneLocalGeometry(positions, source_vertices, faces, oriented, to_bone, mirrored)


# ---------------------------------------------------------------------------
# Steps 2.3-2.4: position range check and quantization
# ---------------------------------------------------------------------------
# Canonical rigid attribute formats (CompCount, QuantizeInfo), PLAN_AddSubmesh.md
# F6/F9. Mirrors HammerspaceMain._CUSTOM_SUBMESH_*_FORMAT: the builder writes
# these headers for every CustomSubmeshes entry whatever its template source,
# so the exporter must encode in exactly these formats.
POSITION_FORMAT = (3, 59)
NORMAL_FORMAT = (3, 62)
UV_FORMAT = (2, 62)
COLOR_FORMAT = (4, 48)


def int16_divisor(quantize_info):
    """Fixed-point divisor for an int16 QuantizeInfo. Float formats never
    occur in the canonical rigid contract and are rejected."""
    if (quantize_info >> 4) in (4, 7, 0xA):
        raise ValueError(f'QuantizeInfo {quantize_info} is a float format; custom submeshes use int16')
    return 1 << (quantize_info & 0xF)


def int16_range(quantize_info):
    """Smallest and largest coordinate representable in *quantize_info*."""
    divisor = int16_divisor(quantize_info)
    return -32768 / divisor, 32767 / divisor


def _fits_int16(value, divisor):
    return math.isfinite(value) and -32768 <= round(value * divisor) <= 32767


def check_position_range(object_name, host_bone_id, geometry, quantize_info=POSITION_FORMAT[1]):
    """Step 2.3: reject a mesh whose bone-local positions don't fit the
    position format, before quantizing anything.

    A mesh moved far from its host bone overflows int16 (about +/-16 units for
    QuantizeInfo 59). The error names the vertex that overshoots most (by its
    Blender index), how far it is from the host bone, and what to do about it.
    """
    divisor = int16_divisor(quantize_info)
    low, high = int16_range(quantize_info)
    worst = None  # (overshoot, exported index)
    for index, position in enumerate(geometry.positions):
        if all(_fits_int16(v, divisor) for v in position):
            continue
        overshoot = max(
            max(v - high, low - v) if math.isfinite(v) else math.inf
            for v in position
        )
        if worst is None or overshoot > worst[0]:
            worst = (overshoot, index)
    if worst is None:
        return
    position = geometry.positions[worst[1]]
    distance = sum(v * v for v in position) ** 0.5
    raise ValueError(
        f'{object_name}: vertex {geometry.source_vertices[worst[1]]} is at bone-local '
        f'({position[0]:.4f}, {position[1]:.4f}, {position[2]:.4f}), {distance:.4f} units from '
        f'host bone {host_bone_id}; positions must stay within {low:g}..{high:g} on every axis. '
        'Move the mesh closer to its host bone or choose a host bone nearer to the mesh.'
    )


def quantize_int16(value, divisor, context):
    """Quantize one component to int16, rejecting (never clamping) a value
    that is not finite or doesn't fit. *context* names what is being encoded."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{context}: value is not finite ({value})')
    quantized = round(value * divisor)
    if not -32768 <= quantized <= 32767:
        raise ValueError(
            f'{context}: value {value:.6f} is outside the representable range '
            f'{-32768 / divisor:g}..{32767 / divisor:g}'
        )
    return quantized


def encode_positions(object_name, geometry, position_format=POSITION_FORMAT):
    """Step 2.4: big-endian int16 position buffer in the template's position
    format (canonical ``(3, 59)``). Run ``check_position_range`` first for the
    friendlier whole-mesh error; this still rejects each bad component with
    object, Blender vertex, axis, value and range."""
    comp_count, quantize_info = position_format
    if comp_count != 3:
        raise ValueError(f'position CompCount {comp_count} is not supported for custom submeshes')
    divisor = int16_divisor(quantize_info)
    if len(geometry.positions) > 0xFFFF:
        raise ValueError(
            f'{object_name}: {len(geometry.positions)} vertices exceed the uint16 index range'
        )
    values = []
    for index, position in enumerate(geometry.positions):
        vertex = geometry.source_vertices[index]
        for axis, value in zip('xyz', position):
            values.append(quantize_int16(value, divisor, f'{object_name} vertex {vertex} {axis}'))
    return struct.pack(f'>{len(values)}h', *values)


def encode_faces(object_name, faces):
    """``FacesData``: big-endian uint16 vertex-index triplets, and the count."""
    if not faces:
        raise ValueError(f'{object_name}: mesh has no faces to export')
    if len(faces) > 0xFFFF:
        raise ValueError(f'{object_name}: {len(faces)} triangles exceed the uint16 FacesCount')
    flat = [index for face in faces for index in face]
    return struct.pack(f'>{len(flat)}H', *flat), len(faces)


# ---------------------------------------------------------------------------
# Step 2.5: per-loop normals, colors and UVs
# ---------------------------------------------------------------------------

# Type-3 attribute bit positions (mirrors SluggiesTools/drawlist._ATTR_BIT_SHIFT;
# the add-on can't import SluggiesTools).
_TYPE3_SHIFTS = {
    'position': 2, 'lighting': 4, 'color0': 6, 'color1': 8,
    'texture0': 10, 'texture1': 12, 'texture2': 14, 'texture3': 16,
    'texture4': 18, 'texture5': 20, 'texture6': 22, 'texture7': 24,
}
_U8_ENTRY_LIMIT = 0x100      # a u8 index addresses 256 entries
_U16_ENTRY_LIMIT = 0xFFFF
MESH_NAME_MAX_LENGTH = 31    # donor names are short ("body", "head"); keep new ones short too
WHITE = (1.0, 1.0, 1.0, 1.0)


@dataclass
class AttributePlan:
    """Which vertex attributes a template source draws with.

    ``entry_limits`` maps an attribute key (``position``, ``lighting``,
    ``color0``, ``texture0``, ...) to the most entries its index can address.
    A `rigid:` template fixes those widths in its cloned Type-3 state;
    `derived:`/`builtin:` regenerate Type 3 from the actual counts, so only
    the uint16 limit applies."""
    normals: bool
    color: bool
    uv_channels: int
    entry_limits: dict


def _type1_layer(shader_mode):
    return (int(shader_mode, 16) >> 13) & 7


def _rigid_attribute_plan(model, surface_id, template_source):
    for submesh in model.get('Submeshes') or []:
        if int((submesh.get('VertexBuffer') or {}).get('VertexBufferCompCount', 0)) != 3:
            continue
        states = submesh.get('DisplayStates') or []
        for index, state in enumerate(states):
            if state.get('SurfaceId') != surface_id:
                continue
            setting = None
            for earlier in states[:index + 1]:
                if int(earlier.get('DisplayStateId', -1)) == 3:
                    setting = int(earlier['ShaderMode'], 16)
            if setting is None:
                raise ValueError(f'{template_source}: template surface has no Type-3 attribute layout')
            limits = {}
            for key, shift in _TYPE3_SHIFTS.items():
                mode = (setting >> shift) & 0b11
                if mode == 0b00:
                    continue
                if mode == 0b01:
                    raise ValueError(
                        f'{template_source}: template uses a direct {key} attribute, '
                        'which custom submeshes do not support'
                    )
                limits[key] = _U8_ENTRY_LIMIT if mode == 0b10 else _U16_ENTRY_LIMIT
            if 'position' not in limits or 'color1' in limits:
                raise ValueError(
                    f'{template_source}: template attribute layout 0x{setting:08x} is not supported'
                )
            textures = sorted(key for key in limits if key.startswith('texture'))
            if textures != [f'texture{i}' for i in range(len(textures))] or len(textures) not in (1, 2):
                raise ValueError(
                    f'{template_source}: template draws {len(textures)} UV channel(s) '
                    f'({", ".join(textures) or "none"}); custom submeshes support 1 or 2'
                )
            return AttributePlan('lighting' in limits, 'color0' in limits, len(textures), limits)
    raise ValueError(f'{template_source}: surface not found on a rigid donor submesh')


def attribute_plan(model, template_source):
    """The attributes a custom submesh must export for *template_source*,
    resolved against the target .sluggie the same way the hammerspace builder
    resolves the template (HammerspaceMain._resolve_custom_submesh_records):

    - `rigid:` draws with the template surface's active Type-3 layout, index
      widths included.
    - `derived:` uses two UV channels when a layer-1 (specular) texture is
      bound at or before the submesh-0 surface, else one.
    - `builtin:` uses two UV channels when submesh 0 binds a layer-1 texture
      anywhere, capped by the template's own layer count -- the 1-layer
      `Shdw` built-in exports one channel whatever the host binds, matching
      HammerspaceMain._custom_submesh_builtin_records.

    `derived:`/`builtin:` always get normals and one color channel, the
    canonical rigid attribute set (F9).
    """
    kind, _sep, argument = str(template_source).partition(':')
    if kind == 'rigid' and argument:
        return _rigid_attribute_plan(model, argument, template_source)
    if kind not in ('derived', 'builtin') or not argument:
        raise ValueError(f'unrecognized TemplateSource {template_source!r}')
    submeshes = model.get('Submeshes') or []
    states = (submeshes[0].get('DisplayStates') if submeshes else None) or []
    if kind == 'derived':
        index = next((i for i, s in enumerate(states) if s.get('SurfaceId') == argument), None)
        if index is None:
            raise ValueError(f'{template_source}: surface not found on submesh 0')
        states = states[:index + 1]
    has_layer1 = any(
        int(s.get('DisplayStateId', -1)) == 1 and _type1_layer(s['ShaderMode']) == 1
        for s in states
    )
    uv_count = 2 if has_layer1 else 1
    if kind == 'builtin':
        uv_count = min(uv_count, TemplateSources.builtin_template_layers(argument))
    return AttributePlan(True, True, uv_count, {})


def dedupe_records(records):
    """Pool identical encoded records, first occurrence first. Returns
    ``(data, indices)``: the concatenated unique records and, per input
    record, its index into them.

    Unlike the donor 3.2/3.3 per-loop edits, a new submesh has no donor slot
    layout to preserve, so shared values are pooled. That keeps u8-indexed
    `rigid:` templates usable for real meshes (a 12-triangle cube has 36
    loops but only 6 distinct normals)."""
    pool = {}
    data = bytearray()
    indices = []
    for record in records:
        index = pool.get(record)
        if index is None:
            index = pool[record] = len(pool)
            data += record
        indices.append(index)
    return bytes(data), indices


def _check_entry_limit(object_name, template_source, key, label, count, plan):
    limit = plan.entry_limits.get(key, _U16_ENTRY_LIMIT)
    if count > limit:
        width = 'u8' if limit == _U8_ENTRY_LIMIT else 'uint16'
        advice = (' Simplify the mesh or pick a derived:/builtin: template source.'
                  if limit == _U8_ENTRY_LIMIT else '')
        raise ValueError(
            f'{object_name}: {count} distinct {label} exceed the {width} index of '
            f'{template_source} (max {limit}).{advice}'
        )


def _loop_order(geometry):
    return [loop for tri in geometry.triangles for loop in tri.loops]


def encode_loop_normals(object_name, geometry, loop_normals, normal_format=NORMAL_FORMAT):
    """Per-loop object-local normals to bone-local unit normals, quantized.
    *loop_normals* is indexed by Blender loop index."""
    comp_count, quantize_info = normal_format
    divisor = int16_divisor(quantize_info)
    loops = _loop_order(geometry)
    transformed = transform_normals([loop_normals[loop] for loop in loops], geometry.to_bone)
    records = []
    for loop, normal in zip(loops, transformed):
        values = [quantize_int16(v, divisor, f'{object_name} loop {loop} normal') for v in normal]
        values += [0] * (comp_count - 3)
        records.append(struct.pack(f'>{comp_count}h', *values))
    return dedupe_records(records)


def encode_loop_uvs(object_name, geometry, loop_uvs, uv_format=UV_FORMAT):
    """Per-loop Blender UVs to game ST. The importer flips V (``t = 1 - v``),
    so export flips it back, as encode_mesh_hammerspace does."""
    comp_count, quantize_info = uv_format
    divisor = int16_divisor(quantize_info)
    records = []
    for loop in _loop_order(geometry):
        u, v = loop_uvs[loop]
        s = quantize_int16(u, divisor, f'{object_name} loop {loop} UV u')
        t = quantize_int16(1.0 - v, divisor, f'{object_name} loop {loop} UV v (stored as 1 - v)')
        records.append(struct.pack(f'>{comp_count}h', s, t, *([0] * (comp_count - 2))))
    return dedupe_records(records)


def encode_color_rgba8(rgba):
    """One entry of the canonical ``(4, 48)`` color array: four bytes R G B A,
    matching the builder's 4-byte color stride
    (HammerspaceMain._CUSTOM_SUBMESH_COLOR_STRIDE)."""
    return bytes(int(round(max(0.0, min(1.0, float(c))) * 255)) for c in rgba)


def encode_loop_colors(geometry, loop_colors):
    """Per-loop colors. *loop_colors* is indexed by Blender loop index, or
    None for a mesh without colors (every loop white, one pooled entry)."""
    loops = _loop_order(geometry)
    colors = [WHITE] * len(loops) if loop_colors is None else [loop_colors[loop] for loop in loops]
    return dedupe_records([encode_color_rgba8(c) for c in colors])


def encode_field(raw, use_base64=True):
    """Base64 string or byte list, following the .sluggie's UseBase64 flag."""
    if use_base64:
        return base64.b64encode(raw).decode('ascii')
    return list(raw)


def _index_buffer(indices, use_base64):
    return encode_field(struct.pack(f'>{len(indices)}H', *indices), use_base64)


def sanitize_mesh_name(name):
    """``MeshName`` is embedded as an ASCII string in the GEO descriptor."""
    ascii_name = ''.join(c if 32 <= ord(c) < 127 else '_' for c in str(name))
    return ascii_name[:MESH_NAME_MAX_LENGTH] or 'custom'


def build_custom_submesh_entry(
    object_name, custom_submesh_id, host_bone_id, template_source, plan,
    geometry, loop_normals, loop_uvs, loop_colors, texture_assignment,
    use_base64=True,
):
    """Assemble one ``CustomSubmeshes`` entry (sluggieschema.json) from
    bone-local geometry and per-loop attributes, running the 2.3 range check
    and 2.4 quantization on the way.

    When the template draws two UV channels, channel 1 mirrors channel 0
    exactly: it is the specular channel, as in donor rigid submeshes (F6).
    """
    check_position_range(object_name, host_bone_id, geometry)
    positions = encode_positions(object_name, geometry)
    faces_data, faces_count = encode_faces(object_name, geometry.faces)
    _check_entry_limit(object_name, template_source, 'position', 'vertices',
                       len(geometry.positions), plan)

    entry = {
        'CustomSubmeshId': custom_submesh_id,
        'MeshName': sanitize_mesh_name(object_name),
        'HostBoneId': int(host_bone_id),
        'TemplateSource': template_source,
        'VertexBufferData': encode_field(positions, use_base64),
    }

    if plan.normals:
        normal_data, normal_indices = encode_loop_normals(object_name, geometry, loop_normals)
        _check_entry_limit(object_name, template_source, 'lighting', 'normals',
                           len(normal_data) // (NORMAL_FORMAT[0] * 2), plan)
        entry['NormalBufferData'] = encode_field(normal_data, use_base64)
        entry['NormalFacesData'] = _index_buffer(normal_indices, use_base64)

    if plan.color:
        color_data, color_indices = encode_loop_colors(geometry, loop_colors)
        _check_entry_limit(object_name, template_source, 'color0', 'colors',
                           len(color_data) // COLOR_FORMAT[0], plan)
        entry['ColorChannelData'] = encode_field(color_data, use_base64)
        entry['ColorFacesData'] = _index_buffer(color_indices, use_base64)

    uv_data, uv_indices = encode_loop_uvs(object_name, geometry, loop_uvs)
    uv_count = len(uv_data) // (UV_FORMAT[0] * 2)
    uv_channels = []
    for channel in range(plan.uv_channels):
        _check_entry_limit(object_name, template_source, f'texture{channel}', 'UVs', uv_count, plan)
        uv_channels.append({
            'UVChannelIndex': channel,
            'UVChannelData': encode_field(uv_data, use_base64),
            'UVFacesData': _index_buffer(uv_indices, use_base64),
        })
    entry['UVChannels'] = uv_channels
    entry['FacesCount'] = faces_count
    entry['FacesData'] = encode_field(faces_data, use_base64)
    entry['TextureAssignment'] = dict(texture_assignment)
    return entry
