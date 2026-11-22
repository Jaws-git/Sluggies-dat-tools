"""Pure-Python encoding for custom submeshes.

bpy-free, like HostBones.py and TemplateSources.py, so the geometry and
quantization rules can be unit-tested without Blender. ExportSluggies.py
reads the Blender scene and hands plain tuples/lists to the functions here.
"""

import struct
from dataclasses import dataclass

try:  # inside Blender this module is part of the addon package
    from . import FieldCodec  # noqa: F401  (reached as CustomSubmeshExport.FieldCodec)
    from . import RebuildCommon as rc
    from . import TemplateSources
except ImportError:  # imported flat by the unit tests
    import FieldCodec  # noqa: F401
    import RebuildCommon as rc
    import TemplateSources


@dataclass(frozen=True)
class Triangle:
    """One exported triangle: Blender vertex indices, the loop (face-corner)
    indices they came from, and the source polygon."""
    vertices: tuple
    loops: tuple
    polygon_index: int


def triangles_from_loop_triangles(loop_triangles):
    """Triangulate without touching the scene.

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
# world position is authoritative
# ---------------------------------------------------------------------------
# Matrices are 4x4 nested lists indexed [row][column] and applied to column
# vectors, the same convention as mathutils (``M @ v``), so Blender matrices
# convert with ``[list(row) for row in matrix]``.

IDENTITY4 = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0),
             (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))

_SINGULAR_EPSILON = rc.SINGULAR_EPSILON


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


# The normal transform lives in RebuildCommon (the shared normal encoder uses it).
mat3_determinant = rc.mat3_determinant
normal_matrix = rc.normal_matrix


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
    can see a pose or animation frame. Raises ValueError for a bone whose
    parent is not in the list."""
    bones = {int(b['BoneId']): b for b in bone_hierarchy}
    for bone_id, bone in bones.items():
        parent_id = bone.get('ParentBoneId')
        if parent_id is not None and int(parent_id) not in bones:
            raise ValueError(f'bone {bone_id} has unknown parent {parent_id}')
    absolute = {}
    remaining = list(bones)
    for _ in range(len(remaining) + 1):
        pending = []
        for bone_id in remaining:
            bone = bones[bone_id]
            parent_id = bone.get('ParentBoneId')
            parent_id = None if parent_id is None else int(parent_id)
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
    bind-local space:
    ``p_bone = host_bind^-1 @ arm_world^-1 @ obj_world @ v.co``.

    ``obj_world`` already carries Object Mode location/rotation/scale, delta
    transforms, parenting and constraints, and dividing out ``arm_world``
    cancels the importer's 90 degree X rotation and any move of the whole
    armature."""
    return mat4_mul(mat4_mul(mat4_inverse(host_bind), mat4_inverse(arm_world)), obj_world)


def keep_offset_world_matrix(obj_world, arm_world, b_old, b_new):
    """New object world matrix for Reassign to new bone's 'Keep offset to
    bone' placement: the mesh keeps
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


transform_normals = rc.transform_normals


def is_mirrored(m):
    """True when *m* flips handedness (negative determinant, e.g. a negative
    scale on one axis), which turns every face inside out."""
    return mat3_determinant(m) < 0.0


def orient_triangles(triangles, mirrored):
    """Reverse every triangle's winding when the transform is
    mirrored, keeping vertices and loops paired."""
    if not mirrored:
        return list(triangles)
    return [
        Triangle(tri.vertices[::-1], tri.loops[::-1], tri.polygon_index)
        for tri in triangles
    ]


def unsupported_modifier_names(modifiers):
    """Names of modifiers export does not evaluate. Takes
    ``(name, type)`` pairs; only ``ARMATURE`` is expected on a custom submesh."""
    return [name for name, kind in modifiers if kind != 'ARMATURE']


def ignored_state_warnings(object_name, host_bone_id, shape_key_names, material_names,
                           exported_material_name, vertex_group_names):
    """Step 3: scene state a custom submesh may carry that export ignores.

    - Shape keys: only the undeformed mesh (the Basis) is read, so any other
      key never reaches the game. A lone Basis key is harmless.
    - Materials: one surface per custom submesh; faces on any other slot are
      exported with *exported_material_name*.
    - Vertex groups: only ``bone_<host>`` places the mesh; every other group
      is dropped.

    Only for ``SluggiesCustomSubmesh`` objects. Donor rigid meshes (heads in
    particular) legitimately carry facial shape keys and must not get this.
    """
    warnings = []
    extra_keys = list(shape_key_names)[1:]
    if extra_keys:
        warnings.append(
            f"{object_name}: shape key(s) {', '.join(extra_keys)} are ignored; custom "
            "submeshes export only the Basis shape."
        )
    extra_materials = [
        name for name in dict.fromkeys(material_names) if name != exported_material_name
    ]
    if extra_materials:
        warnings.append(
            f"{object_name}: extra material(s) {', '.join(extra_materials)} are ignored; "
            f"every face is exported with '{exported_material_name}'."
        )
    host_group = f"bone_{host_bone_id}"
    extra_groups = [name for name in vertex_group_names if name != host_group]
    if extra_groups:
        warnings.append(
            f"{object_name}: vertex group(s) {', '.join(extra_groups)} are ignored; "
            f"the mesh follows only its host bone ({host_group})."
        )
    return warnings


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
    """The world-to-bone transform on plain data: undeformed object-local vertex
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
# position range check and quantization
# ---------------------------------------------------------------------------
# Canonical rigid attribute formats (CompCount, QuantizeInfo). Mirrors SluggieModel._CUSTOM_SUBMESH_*_FORMAT: the builder writes
# these headers for every CustomSubmeshes entry whatever its template source,
# so the exporter must encode in exactly these formats.
POSITION_FORMAT = (3, 59)
NORMAL_FORMAT = (3, 62)
UV_FORMAT = (2, 62)
COLOR_FORMAT = (4, 48)
COLOR_STRIDE = 2                 # bytes of one RGBA4444 entry (not the component count)

# Adaptive position quantization. Every candidate is an s16 format (high nibble
# 3), so the stride stays 6 bytes per vertex whichever one is picked; only the
# low nibble -- the fixed-point shift -- moves. They run from the canonical 59
# (shift 11, +/-16, the finest) down to 48 (shift 0, +/-32767, the coarsest).
#
# `select_position_format` always takes the first that fits, so a mesh inside
# +/-16 still encodes byte-identically to the canonical contract; only a mesh
# that the canonical format would have rejected outright trades precision for
# range. Stadium props (the Gesso boss and friends) are the reason this exists:
# they sit whole multiples of a player character away from their host bone.
#
# Nothing coarser than 59 is ever picked for a mesh that fits, and nothing
# finer than 59 is offered at all -- the donor round-trip anchor depends on
# 59 staying the default, and rigid donor buffers use it too.
POSITION_QUANTIZE_CANDIDATES = tuple(range(POSITION_FORMAT[1], rc.COARSEST_POSITION_QUANTIZE - 1, -1))  # 59, 58, ... 48


# int16 quantization is shared with the donor rebuilds (RebuildCommon).
int16_divisor = rc.int16_divisor
int16_range = rc.int16_range
_fits_int16 = rc.fits_int16


def check_position_range(object_name, host_bone_id, geometry, quantize_info=POSITION_FORMAT[1]):
    """Reject a mesh whose bone-local positions don't fit *one*
    position format, before quantizing anything.

    A mesh moved far from its host bone overflows int16 (about +/-16 units for
    QuantizeInfo 59). The error names the vertex that overshoots most (by its
    Blender index), how far it is from the host bone, and what to do about it.

    Export goes through ``select_position_format`` instead, which walks the
    candidate formats and only lands here -- on the coarsest one -- when the
    mesh fits none of them.
    """
    worst = rc.worst_out_of_range_vertex(geometry, quantize_info)
    if worst is None:
        return
    low, high = int16_range(quantize_info)
    position = geometry.positions[worst]
    distance = sum(v * v for v in position) ** 0.5
    raise ValueError(
        f'{object_name}: vertex {geometry.source_vertices[worst]} is at bone-local '
        f'({position[0]:.4f}, {position[1]:.4f}, {position[2]:.4f}), {distance:.4f} units from '
        f'host bone {host_bone_id}; positions must stay within {low:g}..{high:g} on every axis. '
        'Move the mesh closer to its host bone or choose a host bone nearer to the mesh.'
    )


position_format_fits = rc.position_format_fits


def select_position_format(
    object_name, host_bone_id, geometry, candidates=POSITION_QUANTIZE_CANDIDATES,
):
    """The most precise s16 position format that holds
    every bone-local coordinate, as ``(CompCount, QuantizeInfo)``.

    A mesh that fits the canonical +/-16 gets the canonical 59 and encodes
    exactly as it always has. A mesh further from its host bone -- a stadium
    prop the size of several player characters -- steps down one shift at a
    time, buying 2x the range for half the precision, instead of being
    rejected. The chosen QuantizeInfo travels with the submesh in its
    ``VertexBufferQuantizeInfo`` field, so the builder writes the matching
    header and the game decodes it with the right divisor.

    Raises ValueError naming the worst vertex when not even the coarsest
    candidate fits.
    """
    quantize_info = rc.first_fitting_format(geometry, candidates)
    if quantize_info is not None:
        return (POSITION_FORMAT[0], quantize_info)
    coarsest = candidates[-1]
    try:
        check_position_range(object_name, host_bone_id, geometry, coarsest)
    except ValueError as error:
        raise ValueError(
            f'{error} QuantizeInfo {coarsest} is already the widest position '
            'format available, so no loss of precision can bring this mesh in range.'
        ) from None
    raise AssertionError('coarsest candidate reported a fit it does not have')


# Shared with the donor rebuilds (RebuildCommon).
donor_position_candidates = rc.donor_position_candidates
widened_position_info = rc.widened_position_info
quantize_int16 = rc.quantize_int16
quantize_int16_lazy = rc.quantize_int16_lazy


def same_vertex_data(a, b):
    """Whether two per-vertex values (a position or a normal, any sequence type) are exactly equal."""
    return tuple(a) == tuple(b)


def encode_positions(object_name, geometry, position_format=POSITION_FORMAT):
    """Big-endian int16 position buffer in the template's position
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
    values = rc.quantize_int16_all((value for position in geometry.positions for value in position), divisor)
    if values is None:          # a refused component: walk again to name it
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
# per-loop normals, colors and UVs
# ---------------------------------------------------------------------------

# Type-3 attribute bit positions (mirrors SluggiesTools/drawlist._ATTR_BIT_SHIFT;
# the add-on can't import SluggiesTools).
_TYPE3_SHIFTS = {
    'position': 2, 'lighting': 4, 'color0': 6, 'color1': 8,
    'texture0': 10, 'texture1': 12, 'texture2': 14, 'texture3': 16,
    'texture4': 18, 'texture5': 20, 'texture6': 22, 'texture7': 24,
}
_U8_ENTRY_LIMIT = 0x100      # a u8 index addresses 256 entries
_U16_ENTRY_LIMIT = 0xFFFF    # one short of the 0x10000 a u16 index addresses, like the other uint16 count checks
MESH_NAME_MAX_LENGTH = 31    # donor names are short ("body", "head"); keep new ones short too
WHITE = rc.WHITE


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
    resolves the template (SubmeshTemplates._resolve_custom_submesh_records):

    - `rigid:` draws with the template surface's active Type-3 layout, index
      widths included.
    - `derived:` uses two UV channels when a layer-1 (specular) texture is
      bound at or before the submesh-0 surface, else one.
    - `builtin:` uses two UV channels when submesh 0 binds a layer-1 texture
      anywhere, capped by the template's own layer count -- the 1-layer
      `Shdw` built-in exports one channel whatever the host binds, matching
      SubmeshTemplates._custom_submesh_builtin_records.

    `derived:`/`builtin:` get one color channel and normals, the canonical
    rigid attribute set -- except a built-in flagged without normals
    (the stadium ones: stadium surfaces have none).
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
    normals = True
    if kind == 'builtin':
        uv_count = min(uv_count, TemplateSources.builtin_template_layers(argument))
        normals = TemplateSources.builtin_template_normals(argument)
    return AttributePlan(normals, True, uv_count, {})


dedupe_records = rc.dedupe_records


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


_loop_order = rc.loop_order


def encode_loop_normals(object_name, geometry, loop_normals, normal_format=NORMAL_FORMAT):
    """Per-loop object-local normals to bone-local unit normals, quantized.
    *loop_normals* is indexed by Blender loop index."""
    return rc.encode_loop_normals(object_name, geometry, loop_normals, normal_format)


def encode_loop_uvs(object_name, geometry, loop_uvs, uv_format=UV_FORMAT):
    """Per-loop Blender UVs to game ST. The importer flips V (``t = 1 - v``),
    so export flips it back, as encode_mesh_hammerspace does."""
    return rc.encode_loop_uvs(object_name, geometry, loop_uvs, uv_format)


def encode_color_rgba4444(rgba):
    """One entry of the canonical ``(4, 48)`` color array: a big-endian 16-bit
    RGBA4444 value, matching the builder's 2-byte color stride
    (SubmeshTemplates._CUSTOM_SUBMESH_COLOR_STRIDE). QuantizeInfo 48 is
    RGBA4444 in the engine, not RGBA8 (confirmed in Dolphin)."""
    return rc.encode_color_entry(COLOR_FORMAT[1], rgba)


is_unset_color = rc.is_unset_color
fill_unset_colors = rc.fill_unset_colors


def encode_loop_colors(geometry, loop_colors, object_name='', infos=None):
    """Per-loop colors. *loop_colors* is indexed by Blender loop index, or
    None for a mesh without colors (every loop white, one pooled entry).
    Unset corners are written white (:func:`fill_unset_colors`)."""
    return rc.encode_loop_colors(geometry, loop_colors, COLOR_FORMAT[1], object_name, infos)


encode_field = rc.encode_field
_index_buffer = rc.index_buffer


@dataclass
class CustomSubmeshSurfaces:
    """The surface facts of a custom submesh in the shape ``route_faces``
    reads from a donor: its one primary surface, no imported
    per-face surfaces."""
    custom_submesh_id: str
    primary_surface_id: str

    @property
    def owner_key(self):
        return self.custom_submesh_id

    @property
    def drawable_surfaces(self):
        return {self.primary_surface_id: 0}

    @property
    def face_surfaces(self):
        return []


def route_custom_submesh_faces(object_name, custom_submesh_id, primary_surface_id, triangles, polygon_slots, slots):
    """``route_faces`` for a custom submesh: every face must use the primary ``<id>_ds0`` material or an
    Add-material surface owned by this custom submesh; ``new_keys`` lists
    the additional surfaces in first-use order."""
    donor = CustomSubmeshSurfaces(custom_submesh_id, primary_surface_id)
    return rc.route_faces(object_name, donor, triangles, polygon_slots, slots, topology_changed=True)


def custom_submesh_face_surface_indices(routing):
    """Per triangle, the ``CustomSubmeshes[].FaceSurfaceIndices`` value: 0
    for the primary surface, ``k`` for ``new_keys[k - 1]``."""
    position = {key: k for k, key in enumerate(routing.new_keys, start=1)}
    return [position.get(routing.table[index], 0) for index in routing.indices]


def sanitize_mesh_name(name):
    """``MeshName`` is embedded as an ASCII string in the GEO descriptor."""
    ascii_name = ''.join(c if 32 <= ord(c) < 127 else '_' for c in str(name))
    return ascii_name[:MESH_NAME_MAX_LENGTH] or 'custom'


def build_custom_submesh_entry(
    object_name, custom_submesh_id, host_bone_id, template_source, plan,
    geometry, loop_normals, loop_uvs, loop_colors, texture_assignment,
    use_compression=True, warnings=None, specular_strength=None,
    additional_surfaces=None, face_surface_indices=None, infos=None,
):
    """Assemble one ``CustomSubmeshes`` entry (sluggieschema.json) from
    bone-local geometry and per-loop attributes, picking the position format
    (2.3) and quantizing (2.4) on the way. *specular_strength* (the surface
    material's ``SpecularStrength``) is written when given; without it the
    patcher keeps the template's own value.

    *additional_surfaces* (``AdditionalSurfaces`` entries, in order) and
    *face_surface_indices* (one value per exported triangle: 0 = primary,
    k = the k-th additional surface) describe Add-material surfaces on the
    custom submesh; both are written only
    when there is at least one additional surface.

    When the template draws two UV channels, channel 1 mirrors channel 0
    exactly: it is the specular channel, as in donor rigid submeshes.
    """
    position_format = select_position_format(object_name, host_bone_id, geometry)
    quantize_info = position_format[1]
    if warnings is not None and quantize_info != POSITION_FORMAT[1]:
        low, high = int16_range(quantize_info)
        steps_coarser = POSITION_FORMAT[1] - quantize_info
        warnings.append(
            f'{object_name}: sits too far from host bone {host_bone_id} for the usual '
            f'position precision, so it was quantized at QuantizeInfo {quantize_info} '
            f'({low:g}..{high:g} per axis, {1 / int16_divisor(quantize_info):g} units per step, '
            f'{2 ** steps_coarser}x coarser than normal). Graphical inconsistencies such as '
            'flickering may occur (seen in a stadium). Host it on a nearer bone, or move it '
            'closer to this one, to keep full precision.'
        )
    positions = encode_positions(object_name, geometry, position_format)
    faces_data, faces_count = encode_faces(object_name, geometry.faces)
    _check_entry_limit(object_name, template_source, 'position', 'vertices',
                       len(geometry.positions), plan)

    entry = {
        'CustomSubmeshId': custom_submesh_id,
        'MeshName': sanitize_mesh_name(object_name),
        'HostBoneId': int(host_bone_id),
        'TemplateSource': template_source,
        'VertexBufferData': encode_field(positions, use_compression),
        'VertexBufferQuantizeInfo': quantize_info,
    }

    if plan.normals:
        normal_data, normal_indices = encode_loop_normals(object_name, geometry, loop_normals)
        _check_entry_limit(object_name, template_source, 'lighting', 'normals',
                           len(normal_data) // (NORMAL_FORMAT[0] * 2), plan)
        entry['NormalBufferData'] = encode_field(normal_data, use_compression)
        entry['NormalFacesData'] = _index_buffer(normal_indices, use_compression)

    if plan.color:
        color_data, color_indices = encode_loop_colors(
            geometry, loop_colors, object_name, infos if infos is not None else warnings,
        )
        _check_entry_limit(object_name, template_source, 'color0', 'colors',
                           len(color_data) // COLOR_STRIDE, plan)
        entry['ColorChannelData'] = encode_field(color_data, use_compression)
        entry['ColorFacesData'] = _index_buffer(color_indices, use_compression)

    uv_data, uv_indices = encode_loop_uvs(object_name, geometry, loop_uvs)
    uv_count = len(uv_data) // (UV_FORMAT[0] * 2)
    uv_channels = []
    for channel in range(plan.uv_channels):
        _check_entry_limit(object_name, template_source, f'texture{channel}', 'UVs', uv_count, plan)
        uv_channels.append({
            'UVChannelIndex': channel,
            'UVChannelData': encode_field(uv_data, use_compression),
            'UVFacesData': _index_buffer(uv_indices, use_compression),
        })
    entry['UVChannels'] = uv_channels
    entry['FacesCount'] = faces_count
    entry['FacesData'] = encode_field(faces_data, use_compression)
    entry['TextureAssignment'] = dict(texture_assignment)
    if specular_strength is not None:
        entry['SpecularStrength'] = max(0, min(255, int(specular_strength)))
    if additional_surfaces:
        indices = list(face_surface_indices or [])
        if len(indices) != faces_count:
            raise ValueError(
                f'{object_name}: {len(indices)} face surface indices for {faces_count} faces'
            )
        if max(indices) > len(additional_surfaces) or min(indices) < 0:
            raise ValueError(f'{object_name}: a face surface index is out of range')
        entry['AdditionalSurfaces'] = [dict(surface) for surface in additional_surfaces]
        entry['FaceSurfaceIndices'] = _index_buffer(indices, use_compression)
    return entry
