"""Pure-Python side of the rigid rebuild export.

bpy-free, like CustomSubmeshExport.py, so the decision rules and the encoding
can be unit-tested outside Blender. ExportSluggies.py reads the scene and
hands plain lists here.

A donor rigid (CompCount 3) submesh takes one of two paths per export:

- the slot-preserving in-place fields when its topology equals
  the donor's, its world-space positions equal the mesh-local ones and every
  face still uses its imported surface;
- a whole-blob ``RigidRebuild`` otherwise -- changed topology, an Object Mode
  transform, a host-bone change that keeps the world position, faces moved
  between surfaces (including onto a new Add-material surface), or positions
  that no longer fit the donor's position format.

The rebuild keeps the donor's formats -- except that positions which outgrew
the donor's s16 range step to a coarser shift (``VertexBufferQuantizeInfo``
on the entry), as custom submeshes do -- pools equal values,
mirrors UV channel 1 from channel 0 when the donor channels are identical
and names surfaces by key.

A submesh with facial poses (ptr7 objects: every vanilla head) carries them
through the rebuild as ``FacialPoses`` (2026-10-10): per facial object the
rebuilt vertices its shape keys move with ``x y z`` int16 deltas per pose,
and the rebuilt normal-array entries they tilt with ``nx ny nz`` deltas.
Normal entries of facially animated vertices are pooled per vertex, never
shared with another vertex, so each entry has one delta.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:  # inside Blender this module is part of the addon package
    from . import CustomSubmeshExport as cse
    from . import RebuildCommon as rc
except ImportError:  # imported flat by the unit tests
    import CustomSubmeshExport as cse
    import RebuildCommon as rc

REASON_TOPOLOGY = rc.REASON_TOPOLOGY
REASON_TRANSFORM = rc.REASON_TRANSFORM
REASON_HOST = 'host_bone_world'
REASON_SURFACES = rc.REASON_SURFACES
REASON_POSITION_RANGE = rc.REASON_POSITION_RANGE

RIGID_COMP_COUNT = 3

REASSIGN_HINT = (
    'Use Reassign to new bone in the Sluggies sidebar, which puts every vertex in '
    'one bone_<id> group.'
)


def is_rigid_submesh(sub: dict) -> bool:
    """A donor rigid submesh: 3 position components, one owner bone."""
    return int((sub.get('VertexBuffer') or {}).get('VertexBufferCompCount', 0)) == RIGID_COMP_COUNT


# ---------------------------------------------------------------------------
# Donor facts
# ---------------------------------------------------------------------------

@dataclass
class DonorRigidSubmesh(rc.DonorSubmesh):
    """What the rebuild needs to know about the donor submesh."""


# Shared with the body rebuild and kept importable from here (RebuildCommon).
facial_pose_submeshes = rc.facial_pose_submeshes
donor_submesh_facts = rc.donor_submesh_facts


def donor_rigid_submesh(index: int, sub: dict, facial_submeshes: Iterable[int] = ()) -> DonorRigidSubmesh:
    vb = sub['VertexBuffer']
    if int(vb.get('VertexBufferCompCount', 0)) != RIGID_COMP_COUNT:
        raise ValueError(f'submesh {index} is not a rigid submesh (CompCount {vb.get("VertexBufferCompCount")})')
    return DonorRigidSubmesh(facial=index in set(facial_submeshes), **donor_submesh_facts(index, sub))


def donor_position_format_error(donor: DonorRigidSubmesh, object_name: str) -> Optional[str]:
    """Only int16 donor positions can be rebuilt."""
    return rc.donor_position_format_error(donor, object_name, 'rigid rebuild')


# ---------------------------------------------------------------------------
# The path decision
# ---------------------------------------------------------------------------

def vertex_bone_problem(object_name: str, vertex_bone_ids: Sequence[Iterable[int]]) -> Optional[str]:
    """*vertex_bone_ids* holds, per vertex, the bone ids of its
    positive-weight ``bone_<id>`` groups. Returns the error text when the mesh
    does not sit on exactly one bone, or None."""
    ungrouped = sum(1 for ids in vertex_bone_ids if not set(ids))
    mixed = sum(1 for ids in vertex_bone_ids if len(set(ids)) > 1)
    bones = {next(iter(set(ids))) for ids in vertex_bone_ids if len(set(ids)) == 1}
    if not ungrouped and not mixed and len(bones) <= 1:
        return None
    parts = []
    if ungrouped:
        parts.append(f'{ungrouped} vertices are in no bone_<id> group')
    if mixed:
        parts.append(f'{mixed} vertices are in several bone_<id> groups')
    if len(bones) > 1:
        parts.append(f'the mesh spans bones {sorted(bones)}')
    return (
        f'{object_name}: a rigid mesh must follow exactly one bone, but '
        + ', '.join(parts) + f'. {REASSIGN_HINT}'
    )


def quantized_positions(
    positions: Sequence[Sequence[float]], quantize_info: int, object_name: str = '',
) -> List[Tuple[int, int, int]]:
    """Raises ValueError (with *object_name*) for a coordinate that is not a
    finite number: ``round`` gives a bare ValueError for NaN and an
    OverflowError for infinity."""
    divisor = rc.int16_divisor(quantize_info)
    try:
        return [tuple(int(round(float(v) * divisor)) for v in position) for position in positions]
    except (ValueError, OverflowError):
        raise ValueError(
            f'{object_name or "mesh"}: a vertex position is not a finite number (NaN or infinity)'
        ) from None


FLOAT_POSITION_TOLERANCE = 1e-4  # relative; float32 storage, matrix round trip noise is ~1e-6


def positions_moved(
    mesh_local: Sequence[Sequence[float]], geometry: cse.BoneLocalGeometry, quantize_info: int,
    object_name: str = '',
) -> bool:
    """Whether the bone-local positions differ from the mesh-local ones once
    both are quantized in the donor format. The importer places a rigid mesh
    at its owner bone's bind matrix, so an untouched object round-trips to
    equal values; an Object Mode move, or a host change that keeps the world
    position, does not. A float donor format (some stadium meshes) has no
    quantization step, so it compares with a small relative tolerance."""
    if (quantize_info >> 4) in (4, 7, 0xA):
        pairs = zip((mesh_local[i] for i in geometry.source_vertices), geometry.positions)
        return any(
            abs(float(a) - float(b)) > FLOAT_POSITION_TOLERANCE * max(1.0, abs(float(a)))
            for local_v, bone_v in pairs for a, b in zip(local_v, bone_v)
        )
    local = quantized_positions([mesh_local[i] for i in geometry.source_vertices], quantize_info, object_name)
    bone = quantized_positions(geometry.positions, quantize_info, object_name)
    return local != bone


def outgrew_position_format(geometry: cse.BoneLocalGeometry, quantize_info: int) -> bool:
    """Whether an s16 donor format can no longer hold the positions: the
    in-place paths keep the donor's header, so such a mesh needs a rebuild
    (which may widen the format). Float donor formats never outgrow."""
    return (quantize_info >> 4) == 3 and not rc.position_format_fits(geometry, quantize_info)


def decide_reasons(
    *, topology_changed: bool, moved: bool, host_changed: bool, surfaces_changed: bool,
    out_of_range: bool = False,
) -> List[str]:
    moved_reason = (REASON_HOST if host_changed else REASON_TRANSFORM) if moved else None
    return rc.rebuild_reasons(
        topology_changed=topology_changed, moved_reason=moved_reason, surfaces_changed=surfaces_changed,
        out_of_range=out_of_range,
    )


# ---------------------------------------------------------------------------
# Shared pieces, kept importable from here
# ---------------------------------------------------------------------------
# Routing and the colour / UV encoders serve the body and custom submeshes
# too and live in RebuildCommon; the custom submesh routing lives in
# CustomSubmeshExport.

SlotSurface = rc.SlotSurface
SurfaceRouting = rc.SurfaceRouting
new_surface_key_matches = rc.new_surface_key_matches
route_faces = rc.route_faces
surface_report = rc.surface_report
CustomSubmeshSurfaces = cse.CustomSubmeshSurfaces
route_custom_submesh_faces = cse.route_custom_submesh_faces
custom_submesh_face_surface_indices = cse.custom_submesh_face_surface_indices
encode_color_entry = rc.encode_color_entry
encode_color_channel = rc.encode_color_channel
encode_uv_channels = rc.encode_uv_channels


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def build_rigid_rebuild_entry(
    object_name: str,
    donor: DonorRigidSubmesh,
    host_bone_id: int,
    geometry: cse.BoneLocalGeometry,
    loop_normals,
    loop_uvs_by_channel: Dict[int, Optional[Sequence[Tuple[float, float]]]],
    loop_colors,
    routing: rc.SurfaceRouting,
    new_surfaces: Sequence[dict],
    reasons: Sequence[str],
    use_compression: bool = True,
    warnings: Optional[List[str]] = None,
    infos: Optional[List[str]] = None,
    facial_keys: Optional[Sequence['FacialObjectKeys']] = None,
) -> dict:
    """Assemble ``Submeshes[i].RigidRebuild`` from bone-local geometry and
    per-loop attributes (indexed by Blender loop index), in the donor's own
    formats. *loop_uvs_by_channel* maps each donor UV channel to its per-loop
    coordinates, or None when the Blender layer is missing. *infos* collects
    expected, harmless notes (reported at INFO level). *facial_keys* (the
    mesh's facial shape keys, one entry per ptr7 object on this submesh)
    adds ``FacialPoses`` and pins the normal entries of animated vertices."""
    warnings = warnings if warnings is not None else []
    infos = infos if infos is not None else []
    position_format = cse.select_position_format(
        object_name, host_bone_id, geometry, rc.donor_position_candidates(donor.position_quantize),
    )
    position_quantize = position_format[1]
    widened = rc.widened_position_info(object_name, donor.position_quantize, position_quantize)
    if widened:
        infos.append(widened)
    # The vertex and face counts are checked here, before the facial poses pack vertex indices as uint16.
    positions = cse.encode_positions(object_name, geometry, position_format)
    faces_data, faces_count = cse.encode_faces(object_name, geometry.faces)
    if len(routing.indices) != faces_count:
        raise ValueError(f'{object_name}: {len(routing.indices)} surface indices for {faces_count} faces')
    facial_entries = None
    pinned_normal_data = None
    if facial_keys is not None:
        facial_entries, pinned_normal_data = encode_rigid_facial_poses(
            object_name, facial_keys, geometry, position_quantize,
            donor.normal_format if loop_normals is not None else None, loop_normals,
            use_compression, warnings, infos,
        )

    entry = {
        'HostBoneId': int(host_bone_id),
        'VertexBufferData': rc.encode_field(positions, use_compression),
    }
    if widened:
        entry['VertexBufferQuantizeInfo'] = position_quantize
    if donor.normal_format:
        if pinned_normal_data is not None:
            normal_data, normal_indices = pinned_normal_data
        else:
            normal_data, normal_indices = cse.encode_loop_normals(object_name, geometry, loop_normals, donor.normal_format)
        rc.check_u16(object_name, 'normals', len(normal_indices) and max(normal_indices) + 1)
        entry['NormalBufferData'] = rc.encode_field(normal_data, use_compression)
        entry['NormalFacesData'] = rc.index_buffer(normal_indices, use_compression)
    if donor.color_format:
        entry.update(rc.encode_color_channel(object_name, donor.color_format, geometry, loop_colors, use_compression, infos))

    entry['UVChannels'] = rc.encode_uv_channels(
        object_name, donor.uv_formats, donor.uv_mirrored, geometry, loop_uvs_by_channel,
        use_compression, warnings, infos,
    )
    entry['FacesCount'] = faces_count
    entry['FacesData'] = rc.encode_field(faces_data, use_compression)
    entry['FaceSurfaceTable'] = list(routing.table)
    entry['FaceSurfaceIndices'] = rc.index_buffer(routing.indices, use_compression)
    if new_surfaces:
        entry['NewSurfaces'] = [dict(surface) for surface in new_surfaces]
    entry['Reason'] = list(reasons)
    if facial_entries is not None:
        entry['FacialPoses'] = facial_entries
    return entry


# ---------------------------------------------------------------------------
# Facial poses (ptr7) on a rebuilt submesh
# ---------------------------------------------------------------------------
# The key naming, the plain-data shape keys and the warnings are shared with
# the skinned body (RebuildCommon).

FACIAL_KEY_NAME = rc.FACIAL_KEY_NAME
FacialObjectKeys = rc.FacialObjectKeys
facial_warning = rc.facial_warning
_AXES = ('x', 'y', 'z')


def encode_facial_loop_normals(
    object_name: str, geometry: cse.BoneLocalGeometry, loop_normals, normal_format: Tuple[int, int],
    pinned_vertices,
) -> Tuple[bytes, List[int]]:
    """``cse.encode_loop_normals`` with the normal entries of
    *pinned_vertices* (exported indices) pooled per vertex: two loops share
    an entry only when they hold the same normal and belong to the same
    pinned vertex, or to no pinned vertex at all. A facial pose then gives
    every entry one delta. The entry count is checked here: the facial
    poses pack entry indices as uint16."""
    normal_data, normal_indices = rc.encode_loop_normals(
        object_name, geometry, loop_normals, normal_format, set(pinned_vertices),
    )
    rc.check_u16(object_name, 'normals', len(normal_indices) and max(normal_indices) + 1)
    return normal_data, normal_indices


def encode_rigid_facial_poses(
    object_name: str, objects: Sequence[FacialObjectKeys], geometry: cse.BoneLocalGeometry,
    position_quantize: int, normal_format: Optional[Tuple[int, int]] = None, loop_normals=None,
    use_compression: bool = True, warnings: Optional[List[str]] = None, infos: Optional[List[str]] = None,
) -> Tuple[List[dict], Optional[Tuple[bytes, List[int]]]]:
    """``RigidRebuild.FacialPoses`` plus the rebuilt normal arrays.

    Per facial object: the exported vertices whose shape keys move them
    (after quantization in the donor's position format) with one ``x y z``
    int16 delta array per pose after pose zero, and, when the donor has a
    normal array (*normal_format* with *loop_normals*), the rebuilt normal
    entries whose vertex the keys tilt with ``nx ny nz`` deltas in the
    normal format. Deltas are the key's displacement from the Basis key in
    bone space; the normal delta is the change of the per-vertex shape-key
    normal. A missing key leaves its pose at rest (warning); an object that
    moves nothing keeps one zero entry. Returns ``(entries, (normal_data,
    normal_indices) or None)``; the normal arrays pool animated vertices'
    entries per vertex (``encode_facial_loop_normals``)."""
    warnings = warnings if warnings is not None else []
    infos = infos if infos is not None else []
    divisor = rc.int16_divisor(position_quantize)
    normal_divisor = rc.int16_divisor(normal_format[1]) if normal_format is not None and loop_normals is not None else None
    matrix = geometry.to_bone
    pose_normal_matrix = None                             # of *matrix*, built for the first normal that needs it
    per_object = []
    tilted: set = set()
    for facial_object in objects:
        pose_indices = range(1, facial_object.pose_count)
        missing = rc.facial_missing_poses_warning(object_name, facial_object)
        if missing:
            warnings.append(missing)
        position_rows: List[List[tuple]] = []
        normal_rows: List[List[tuple]] = []
        for exported, blender_vertex in enumerate(geometry.source_vertices):
            positions_of_vertex, normals_of_vertex = [], []
            basis_position = facial_object.basis_positions[blender_vertex]
            rest = None                                   # the Basis position in bone space, once per vertex
            for k, pose in enumerate(pose_indices):
                positions = facial_object.pose_positions[k]
                if positions is None:
                    positions_of_vertex.append((0, 0, 0))
                    normals_of_vertex.append((0, 0, 0))
                    continue

                def context(pose=pose, facial_object=facial_object, blender_vertex=blender_vertex):
                    return (f'{object_name} facial object {facial_object.object_index} pose {pose} '
                            f'vertex {blender_vertex}')

                normals = facial_object.pose_normals[k]
                with_normals = (normal_divisor is not None and normals is not None
                                and facial_object.basis_normals is not None)
                # A key that leaves this vertex as the Basis has it: both deltas are exactly zero.
                if cse.same_vertex_data(positions[blender_vertex], basis_position) and (
                        not with_normals or cse.same_vertex_data(
                            normals[blender_vertex], facial_object.basis_normals[blender_vertex])):
                    positions_of_vertex.append((0, 0, 0))
                    normals_of_vertex.append((0, 0, 0))
                    continue
                if rest is None:
                    rest = cse.transform_point(matrix, basis_position)
                posed = cse.transform_point(matrix, positions[blender_vertex])
                positions_of_vertex.append(tuple(
                    rc.quantize_int16_lazy(posed[axis] - rest[axis], divisor,
                                            lambda name=name: f'{context()} {name}')
                    for axis, name in enumerate(_AXES)
                ))
                if with_normals:
                    if pose_normal_matrix is None:
                        pose_normal_matrix = cse.normal_matrix(matrix)
                    basis_normal, pose_normal = cse.transform_normals(
                        [facial_object.basis_normals[blender_vertex], normals[blender_vertex]], matrix,
                        pose_normal_matrix,
                    )
                    normals_of_vertex.append(tuple(
                        rc.quantize_int16_lazy(pose_normal[axis] - basis_normal[axis], normal_divisor,
                                                lambda name=name: f'{context()} n{name}')
                        for axis, name in enumerate(_AXES)
                    ))
                else:
                    normals_of_vertex.append((0, 0, 0))
            position_rows.append(positions_of_vertex)
            normal_rows.append(normals_of_vertex)
            if any(any(row) for row in normals_of_vertex):
                tilted.add(exported)
        per_object.append((facial_object, position_rows, normal_rows))

    normal_arrays = None
    if normal_divisor is not None:
        normal_arrays = encode_facial_loop_normals(object_name, geometry, loop_normals, normal_format, tilted)
    entries = []
    vertex_of_loop = rc.loop_vertices(geometry) if normal_arrays is not None else []
    for facial_object, position_rows, normal_rows in per_object:
        later = facial_object.pose_count - 1
        moved = [vertex for vertex, rows in enumerate(position_rows) if any(any(row) for row in rows)]
        nothing_moves = not moved
        if nothing_moves:
            moved = [0]
            vertex_rows = [[(0, 0, 0)] * later]
        else:
            vertex_rows = [position_rows[vertex] for vertex in moved]
        entry = {
            'ObjectIndex': facial_object.object_index,
            'Vertices': rc.encode_field(struct.pack(f'>{len(moved)}H', *moved), use_compression),
            'PoseDeltas': [
                rc.encode_field(struct.pack(f'>{3 * len(moved)}h', *(v for rows in vertex_rows for v in rows[k])), use_compression)
                for k in range(later)
            ],
        }
        normal_note = ''
        if normal_arrays is not None:
            _data, normal_indices = normal_arrays
            entry_vertex: Dict[int, int] = {}
            for index, vertex in zip(normal_indices, vertex_of_loop):
                if vertex in tilted and any(any(row) for row in normal_rows[vertex]):
                    entry_vertex[index] = vertex
            normal_entries = sorted(entry_vertex)
            entry_rows = [normal_rows[entry_vertex[index]] for index in normal_entries]
            if not normal_entries:
                normal_entries = [0]
                entry_rows = [[(0, 0, 0)] * later]
            entry['NormalEntries'] = rc.encode_field(struct.pack(f'>{len(normal_entries)}H', *normal_entries), use_compression)
            entry['NormalPoseDeltas'] = [
                rc.encode_field(struct.pack(f'>{3 * len(normal_entries)}h', *(v for rows in entry_rows for v in rows[k])), use_compression)
                for k in range(later)
            ]
            normal_note = f', {len(entry_vertex)} normal entr{"y" if len(entry_vertex) == 1 else "ies"}'
        if nothing_moves:
            warnings.append(rc.facial_unmoving_warning(object_name, facial_object))
        infos.append(rc.facial_animated_info(object_name, facial_object, len(moved), normal_note))
        entries.append(entry)
    return entries, normal_arrays


# The in-place fields a rebuild drops and the rebuilds kept from an earlier
# export serve the body too (RebuildCommon); kept importable from here.
IN_PLACE_SUBMESH_FIELDS = rc.IN_PLACE_SUBMESH_FIELDS
IN_PLACE_CHANNEL_FIELDS = rc.IN_PLACE_CHANNEL_FIELDS
GEO_ID_FREE = rc.GEO_ID_FREE
CarriedRebuilds = rc.CarriedRebuilds
bone_geo_id_raw = rc.bone_geo_id_raw
carry_unselected_rebuilds = rc.carry_unselected_rebuilds
strip_in_place_edits = rc.strip_in_place_edits


def mode_reason(object_name: str, reasons: Sequence[str]) -> str:
    """The export-mode reason line for one rebuilt submesh."""
    return f'rigid rebuild of {object_name} ({", ".join(reasons)})'
