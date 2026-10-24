"""Pure-Python side of the skinned-body rebuild export
(``Submeshes[0].SkinnedRebuild``).

bpy-free, like RigidRebuildExport.py, so the decision rules and the
encoding can be unit-tested outside Blender. ExportSluggies.py reads the
scene and hands plain lists here.

The skinned donor submesh (CompCount 6, the body) takes the rebuild path
when its topology changed, when it carries an Object Mode transform, when
any vertex's set of skinning bones changed (vertex groups added, removed
or reassigned), when faces moved in a way the slot-preserving paths
cannot store (a partial move, a move between surfaces with different
shaders, or an Add-material surface), or when its positions no longer fit
the donor's position format. Otherwise the in-place fields
apply (position, UV, colour and weight-value edits on the donor's own
vertices and bones).

What the entry carries, in the donor's own formats (positions that outgrew
the donor's s16 range step to a coarser shift, ``VertexBufferQuantizeInfo``;
the patcher moves the SKN and normal headers with it): one interleaved
position + normal record per vertex in armature (model) space, triangles,
per-loop colours and UVs, the face -> surface routing, and ``(vertex, bone,
weight)`` influences, plus ``FacialPoses``: the body's
facial shape keys as sparse delta records, so the patcher can rebuild the
donor's blink and mouth poses for the new vertex order. The patcher assigns
position slots, builds the SKN and re-encodes the draw lists; without
``FacialPoses`` it drops the poses that address submesh 0.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:  # inside Blender this module is part of the addon package
    from . import CustomSubmeshExport as cse
    from . import RigidRebuildExport as rre
except ImportError:  # imported flat by the unit tests
    import CustomSubmeshExport as cse
    import RigidRebuildExport as rre

REASON_TOPOLOGY = rre.REASON_TOPOLOGY
REASON_TRANSFORM = rre.REASON_TRANSFORM
REASON_SURFACES = rre.REASON_SURFACES
REASON_POSITION_RANGE = rre.REASON_POSITION_RANGE
REASON_WEIGHTS = 'skin_weights'      # a vertex's set of skinning bones changed

SKINNED_COMP_COUNT = 6
INFLUENCE = struct.Struct('>HHf')    # (vertex, bone, weight)
IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


@dataclass
class DonorSkinnedSubmesh:
    """What the rebuild needs to know about the skinned donor submesh."""
    index: int
    position_quantize: int
    normal_format: Optional[Tuple[int, int]]
    color_format: Optional[Tuple[int, int]]
    uv_formats: Dict[int, Tuple[int, int]]
    uv_mirrored: bool
    faces: List[Tuple[int, int, int]]
    face_surfaces: List[str]
    drawable_surfaces: Dict[str, int]
    facial: bool                                    # a ptr7 facial object references it
    skinned_bones: set = field(default_factory=set)  # bones the donor's own skin uses

    @property
    def owner_key(self) -> str:
        return f'sm{self.index}'


def is_skinned_submesh(sub: dict) -> bool:
    return int((sub.get('VertexBuffer') or {}).get('VertexBufferCompCount', 0)) == SKINNED_COMP_COUNT


def skinned_bone_ids(skin_data: Optional[dict]) -> set:
    """Bones the donor's SK1/SK2/SKAcc entries skin to (mirrors
    SluggiesTools/binfmt.skin_bone_ids; the add-on can't import it)."""
    bones = set()
    for entry in (skin_data or {}).get('SK1s', []):
        bones.add(int(entry['BoneIndex']))
    for entry in (skin_data or {}).get('SK2s', []):
        bones.add(int(entry['BoneIndex1']))
        bones.add(int(entry['BoneIndex2']))
    for entry in (skin_data or {}).get('SKAccs', []):
        bones.add(int(entry['BoneIndex']))
    return bones


def added_bone_ids(bone_hierarchy_edited) -> set:
    """Ids of the bones an export appends (``BoneHierarchyEdited`` entries
    with ``UserAdded``)."""
    return {
        int(b['BoneId']) for b in (bone_hierarchy_edited or []) if b.get('UserAdded') and 'BoneId' in b
    }


def allowed_skin_bones(bone_hierarchy) -> set:
    """Bones the rebuilt body may skin to: every bone of the skeleton
    (``BoneHierarchyEdited`` or ``BoneHierarchy``). The game builds a skinning
    matrix for any bone the SKN names: an appended mesh-free bone (Dolphin,
    2026-10-12) and vanilla bones the donor never skins, mesh-free or owning a
    rigid mesh (Mario High, bones 49 and 55, Dolphin 2026-10-24)."""
    return {int(b['BoneId']) for b in (bone_hierarchy or []) if 'BoneId' in b}


def newly_skinned_bones(vertex_weights: Sequence[dict], donor: 'DonorSkinnedSubmesh', added: set) -> List[int]:
    """Original bones the rebuilt body weights that the donor's own skin never
    uses (added bones excluded), for the export log."""
    used = {bone for weights in vertex_weights for bone in weights}
    return sorted(used - set(donor.skinned_bones) - set(added))


def donor_skinned_submesh(
    index: int, sub: dict, skin_data: Optional[dict], facial_submeshes: Iterable[int] = (),
) -> DonorSkinnedSubmesh:
    if not is_skinned_submesh(sub):
        raise ValueError(
            f'submesh {index} is not the skinned submesh '
            f'(CompCount {(sub.get("VertexBuffer") or {}).get("VertexBufferCompCount")})'
        )
    return DonorSkinnedSubmesh(
        facial=index in set(facial_submeshes),
        skinned_bones=skinned_bone_ids(skin_data),
        **rre.donor_submesh_facts(index, sub),
    )


def donor_position_format_error(donor: DonorSkinnedSubmesh, object_name: str) -> Optional[str]:
    """Only int16 donor positions can be rebuilt (the SKN sources mirror them)."""
    if (donor.position_quantize >> 4) == 3:
        return None
    return (
        f'{object_name}: this submesh stores positions in format {donor.position_quantize} '
        f'(not int16), which the body rebuild does not support; keep its topology, '
        'transform and surfaces unchanged.'
    )


def decide_reasons(
    *, topology_changed: bool, moved: bool, surfaces_changed: bool, weights_changed: bool = False,
    out_of_range: bool = False,
) -> List[str]:
    reasons = []
    if topology_changed:
        reasons.append(REASON_TOPOLOGY)
    if moved:
        reasons.append(REASON_TRANSFORM)
    if surfaces_changed:
        reasons.append(REASON_SURFACES)
    if weights_changed:
        reasons.append(REASON_WEIGHTS)
    if out_of_range:
        reasons.append(REASON_POSITION_RANGE)
    return reasons


def surfaces_need_rebuild(
    routing: 'rre.SurfaceRouting', donor: DonorSkinnedSubmesh, effective_modes: Sequence[Optional[str]],
) -> bool:
    """Whether the face -> surface changes need the rebuild. A complete move
    of a donor surface onto another donor surface with the same effective
    Type-7 shader keeps the slot-preserving path (proven in
    Dolphin); anything else -- a new surface, a partial move, or a move
    between shaders -- moves real primitives and needs the rebuild."""
    if routing.new_keys:
        return True
    if not routing.changed or not donor.face_surfaces:
        return False
    targets: Dict[str, set] = {}
    for face, index in enumerate(routing.indices):
        if face >= len(donor.face_surfaces):
            return True
        targets.setdefault(donor.face_surfaces[face], set()).add(routing.table[index])
    for source, moved_to in targets.items():
        if len(moved_to) > 1:
            return True
        target = next(iter(moved_to))
        if target == source:
            continue
        source_mode = effective_modes[donor.drawable_surfaces[source]] if source in donor.drawable_surfaces else None
        target_mode = effective_modes[donor.drawable_surfaces[target]] if target in donor.drawable_surfaces else None
        if source_mode != target_mode:
            return True
    return False


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

@dataclass
class InfluenceRemap:
    weights: list                       # per vertex {bone: weight}, on allowed bones only
    remapped: Dict[Tuple[int, int], int]   # (bone, target) -> vertex count
    dropped: Dict[int, int]             # bone -> vertex count (no allowed ancestor)
    unweighted: List[int]               # vertex indices left without any influence


def remap_influences(vertex_weights: Sequence[dict], allowed: set, parent_of: dict) -> InfluenceRemap:
    """Move every weight on a bone outside *allowed* (``allowed_skin_bones``:
    the bones of the skeleton) to its nearest allowed ancestor (the external
    tool's ``to_allowed``). A bone with no allowed ancestor (a ``bone_<id>``
    group for a bone this model does not have) loses its weight. *parent_of*
    maps bone id -> parent id (None at the root)."""
    cache: Dict[int, Optional[int]] = {}

    def to_allowed(bone: int) -> Optional[int]:
        if bone in cache:
            return cache[bone]
        node = bone
        seen = set()
        while node is not None and node not in allowed and node not in seen:
            seen.add(node)
            node = parent_of.get(node)
        cache[bone] = node if node in allowed else None
        return cache[bone]

    weights, remapped, dropped, unweighted = [], {}, {}, []
    for vertex, influences in enumerate(vertex_weights):
        merged: Dict[int, float] = {}
        for bone, weight in influences.items():
            if not (weight > 0):
                continue
            target = to_allowed(int(bone))
            if target is None:
                dropped[int(bone)] = dropped.get(int(bone), 0) + 1
                continue
            if target != bone:
                remapped[(int(bone), target)] = remapped.get((int(bone), target), 0) + 1
            merged[target] = merged.get(target, 0.0) + float(weight)
        if not merged:
            unweighted.append(vertex)
        weights.append(merged)
    return InfluenceRemap(weights, remapped, dropped, unweighted)


def remap_report(remap: InfluenceRemap, object_name: str) -> List[str]:
    lines = []
    for (bone, target), count in sorted(remap.remapped.items()):
        lines.append(
            f'{object_name}: {count} vertex weight(s) on bone_{bone}, which can\'t be skinned to, '
            f'were moved to its nearest skinnable ancestor bone_{target}.'
        )
    for bone, count in sorted(remap.dropped.items()):
        lines.append(
            f'{object_name}: {count} vertex weight(s) on bone_{bone} were dropped: this model has '
            'no such bone.'
        )
    return lines


def unweighted_error(
    object_name: str, unweighted: Sequence[int], vertex_weights: Optional[Sequence[dict]] = None,
) -> Optional[str]:
    """Refusal text for *unweighted* (vertices without an allowed influence).
    With *vertex_weights*, vertices weighted only to unskinnable bones are
    reported by bone (:func:`unskinnable_error`) rather than as unweighted."""
    if not unweighted:
        return None
    if vertex_weights is None:
        bare, by_bone = list(unweighted), {}
    else:
        bare, by_bone = split_unweighted(unweighted, vertex_weights)
    parts = []
    if by_bone:
        parts.append(unskinnable_error(object_name, by_bone))
    if bare:
        parts.append(
            f'{object_name}: {len(bare)} vertex(es) have no weight on any bone of this model. '
            'Assign them to bone_<id> vertex groups.'
        )
    return ' '.join(parts)


def split_unweighted(unweighted: Sequence[int], vertex_weights: Sequence[dict]) -> Tuple[List[int], Dict[int, int]]:
    """Split *unweighted* vertices into the ones with no positive weight at
    all and, per bone, how many of the others are weighted to it (all of
    their bones are unskinnable, or they would not be unweighted)."""
    bare: List[int] = []
    by_bone: Dict[int, int] = {}
    for vertex in unweighted:
        bones = [int(bone) for bone, weight in vertex_weights[vertex].items() if weight > 0]
        if not bones:
            bare.append(vertex)
        for bone in bones:
            by_bone[bone] = by_bone.get(bone, 0) + 1
    return bare, by_bone


def bone_list(bones: Iterable[int]) -> str:
    names = [f'bone_{bone}' for bone in sorted(bones)]
    return names[0] if len(names) == 1 else f'{", ".join(names[:-1])} and {names[-1]}'


def unskinnable_error(object_name: str, by_bone: Dict[int, int]) -> str:
    """Text for vertices weighted only to unskinnable bones (*by_bone*: bone
    -> vertex count, from :func:`split_unweighted`)."""
    vertices = ', '.join(f'bone_{bone}: {count}' for bone, count in sorted(by_bone.items()))
    return (
        f"{object_name}: {bone_list(by_bone)} can't be used for skinning (this model has no "
        f"such bone). Vertices weighted only to {'it' if len(by_bone) == 1 else 'them'}: "
        f"{vertices}. Please move the weights to a bone of this model."
    )


def encode_influences(vertex_weights: Sequence[dict], source_vertices: Sequence[int], use_compression: bool = True):
    """``Influences``: ``(exported vertex, bone, weight)`` records for the
    exported vertices (``source_vertices[i]`` is the Blender index of
    exported vertex ``i``), weights normalized per vertex."""
    raw = bytearray()
    for index, blender_vertex in enumerate(source_vertices):
        influences = vertex_weights[blender_vertex]
        total = sum(influences.values())
        for bone, weight in sorted(influences.items()):
            raw += INFLUENCE.pack(index, bone, weight / total)
    return cse.encode_field(bytes(raw), use_compression)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def vertex_normals(geometry: 'cse.BoneLocalGeometry', loop_normals, vertex_count: int):
    """Per exported vertex, the mean of its loops' normals (object space,
    unnormalized; ``transform_normals`` renormalizes). A vertex with no loop
    keeps +Z. *loop_normals* is indexed by Blender loop index."""
    sums = [[0.0, 0.0, 0.0] for _ in range(vertex_count)]
    for tri in geometry.triangles:
        for vertex, loop in zip(tri.vertices, tri.loops):
            normal = loop_normals[loop]
            for axis in range(3):
                sums[vertex][axis] += normal[axis]
    result = []
    for exported, blender_vertex in enumerate(geometry.source_vertices):
        total = sums[blender_vertex]
        if all(abs(v) < 1e-12 for v in total):
            total = [0.0, 0.0, 1.0]
        result.append(tuple(total))
    return result


def position_range_error(object_name: str, geometry: 'cse.BoneLocalGeometry', quantize_info: int) -> Optional[str]:
    """An int16 position format bounds the body (about +/-16 units for
    format 59); the error names the vertex that overshoots most."""
    divisor = cse.int16_divisor(quantize_info)
    low, high = cse.int16_range(quantize_info)
    worst = None
    for index, position in enumerate(geometry.positions):
        if all(cse._fits_int16(v, divisor) for v in position):
            continue
        overshoot = max(
            max(v - high, low - v) if math.isfinite(v) else math.inf
            for v in position
        )
        if worst is None or overshoot > worst[0]:
            worst = (overshoot, index)
    if worst is None:
        return None
    position = geometry.positions[worst[1]]
    return (
        f'{object_name}: vertex {geometry.source_vertices[worst[1]]} is at '
        f'({position[0]:.4f}, {position[1]:.4f}, {position[2]:.4f}) in armature space; even '
        f'position format {quantize_info}, the widest available, keeps every axis within '
        f'{low:g}..{high:g}. Scale the body down or move it closer to the armature origin.'
    )


def select_body_position_format(object_name: str, geometry: 'cse.BoneLocalGeometry', donor_quantize: int) -> int:
    """The most precise s16 format, from the donor's own down to 48, that
    holds every body position (the interleaved normals fit any of them).
    Raises ValueError naming the worst vertex when none does."""
    candidates = cse.donor_position_candidates(donor_quantize)
    for quantize_info in candidates:
        if cse.position_format_fits(geometry, quantize_info):
            return quantize_info
    raise ValueError(position_range_error(object_name, geometry, candidates[-1]))


def encode_vertex_records(object_name: str, geometry: 'cse.BoneLocalGeometry', normals, quantize_info: int) -> bytes:
    """``VertexBufferData``: one ``x y z nx ny nz`` int16 record per exported
    vertex in the donor's position format. *normals* are per exported
    vertex, already in the target space and unit length."""
    divisor = cse.int16_divisor(quantize_info)
    if len(geometry.positions) > 0xFFFF:
        raise ValueError(f'{object_name}: {len(geometry.positions)} vertices exceed the uint16 index range')
    values = []
    for index, (position, normal) in enumerate(zip(geometry.positions, normals)):
        vertex = geometry.source_vertices[index]
        for axis, value in zip('xyz', position):
            values.append(cse.quantize_int16(value, divisor, f'{object_name} vertex {vertex} {axis}'))
        for axis, value in zip(('nx', 'ny', 'nz'), normal):
            values.append(cse.quantize_int16(value, divisor, f'{object_name} vertex {vertex} {axis}'))
    return struct.pack(f'>{len(values)}h', *values)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def build_skinned_rebuild_entry(
    object_name: str,
    donor: DonorSkinnedSubmesh,
    geometry: 'cse.BoneLocalGeometry',
    normals,
    loop_uvs_by_channel: Dict[int, Optional[Sequence[Tuple[float, float]]]],
    loop_colors,
    routing: 'rre.SurfaceRouting',
    vertex_weights: Sequence[dict],
    new_surfaces: Sequence[dict],
    reasons: Sequence[str],
    use_compression: bool = True,
    warnings: Optional[List[str]] = None,
    infos: Optional[List[str]] = None,
) -> dict:
    """Assemble ``Submeshes[0].SkinnedRebuild``. *normals* are per exported
    vertex (unit, target space); *vertex_weights* per Blender vertex
    ``{bone: weight}`` on skinned bones only (see :func:`remap_influences`)."""
    warnings = warnings if warnings is not None else []
    infos = infos if infos is not None else []
    position_quantize = select_body_position_format(object_name, geometry, donor.position_quantize)
    widened = cse.widened_position_info(object_name, donor.position_quantize, position_quantize)
    if widened:
        infos.append(widened)
    faces_data, faces_count = cse.encode_faces(object_name, geometry.faces)
    if len(routing.indices) != faces_count:
        raise ValueError(f'{object_name}: {len(routing.indices)} surface indices for {faces_count} faces')
    entry = {
        'VertexBufferData': cse.encode_field(encode_vertex_records(object_name, geometry, normals, position_quantize), use_compression),
        'Influences': encode_influences(vertex_weights, geometry.source_vertices, use_compression),
    }
    if widened:
        entry['VertexBufferQuantizeInfo'] = position_quantize
    if donor.color_format:
        entry.update(rre.encode_color_channel(object_name, donor.color_format, geometry, loop_colors, use_compression, infos))
    entry['UVChannels'] = rre.encode_uv_channels(
        object_name, donor.uv_formats, donor.uv_mirrored, geometry, loop_uvs_by_channel,
        use_compression, warnings, infos,
    )
    entry['FacesCount'] = faces_count
    entry['FacesData'] = cse.encode_field(faces_data, use_compression)
    entry['FaceSurfaceTable'] = list(routing.table)
    entry['FaceSurfaceIndices'] = cse.encode_field(struct.pack(f'>{len(routing.indices)}H', *routing.indices), use_compression)
    if new_surfaces:
        entry['NewSurfaces'] = [dict(surface) for surface in new_surfaces]
    entry['Reason'] = list(reasons)
    return entry


# The facial helpers are shared with the rigid rebuild (RigidRebuildExport):
# the key naming, the plain-data shape keys and the "no shape keys" warning.
facial_warning = rre.facial_warning
FACIAL_KEY_NAME = rre.FACIAL_KEY_NAME
FacialObjectKeys = rre.FacialObjectKeys


# ---------------------------------------------------------------------------
# Facial poses
# ---------------------------------------------------------------------------

_DELTA_AXES = ('x', 'y', 'z', 'nx', 'ny', 'nz')


def encode_facial_poses(
    object_name: str, objects: Sequence[FacialObjectKeys], geometry: 'cse.BoneLocalGeometry',
    quantize_info: int, use_compression: bool = True,
    warnings: Optional[List[str]] = None, infos: Optional[List[str]] = None,
) -> List[dict]:
    """``FacialPoses``: per facial object, the exported vertices whose
    shape keys move them (position or normal, after quantization) and one
    delta record array per pose after pose zero, ``x y z nx ny nz`` int16 in
    the body's position format (*quantize_info*: the entry's own
    ``VertexBufferQuantizeInfo`` when it has one, else the donor's). Deltas are the shape key's displacement
    from the Basis key, taken into armature space like the positions; a
    missing key leaves its pose at the rest shape. An object that moves no
    vertex keeps one zero entry so the section stays well-formed."""
    warnings = warnings if warnings is not None else []
    infos = infos if infos is not None else []
    divisor = cse.int16_divisor(quantize_info)
    matrix = geometry.to_bone
    result = []
    for facial_object in objects:
        pose_indices = range(1, facial_object.pose_count)
        missing = [
            pose for pose, positions in zip(pose_indices, facial_object.pose_positions) if positions is None
        ]
        if missing:
            warnings.append(
                f'{object_name}: facial object {facial_object.object_index} has no shape key for pose(s) '
                f"{', '.join(str(pose) for pose in missing)}; those poses keep the rest shape."
            )
        mapped: List[int] = []
        records: List[List[tuple]] = [[] for _ in pose_indices]
        for exported, blender_vertex in enumerate(geometry.source_vertices):
            rows = []
            for k, pose in enumerate(pose_indices):
                positions = facial_object.pose_positions[k]
                if positions is None:
                    rows.append((0,) * 6)
                    continue
                rest = cse.transform_point(matrix, facial_object.basis_positions[blender_vertex])
                posed = cse.transform_point(matrix, positions[blender_vertex])
                values = [posed[axis] - rest[axis] for axis in range(3)]
                normals = facial_object.pose_normals[k]
                if normals is not None and facial_object.basis_normals is not None:
                    basis_normal, pose_normal = cse.transform_normals(
                        [facial_object.basis_normals[blender_vertex], normals[blender_vertex]], matrix,
                    )
                    values += [pose_normal[axis] - basis_normal[axis] for axis in range(3)]
                else:
                    values += [0.0, 0.0, 0.0]
                rows.append(tuple(
                    cse.quantize_int16(
                        value, divisor,
                        f'{object_name} facial object {facial_object.object_index} pose {pose} '
                        f'vertex {blender_vertex} {axis}',
                    )
                    for value, axis in zip(values, _DELTA_AXES)
                ))
            if any(any(row) for row in rows):
                mapped.append(exported)
                for k, row in enumerate(rows):
                    records[k].append(row)
        if not mapped:
            warnings.append(
                f'{object_name}: facial object {facial_object.object_index} moves no vertex in any '
                'shape key; it is kept with one unmoving entry.'
            )
            mapped = [0]
            records = [[(0,) * 6] for _ in pose_indices]
        infos.append(
            f'{object_name}: facial object {facial_object.object_index}: {len(mapped)} vertex(es) '
            f'animated over {facial_object.pose_count - 1} pose(s).'
        )
        result.append({
            'ObjectIndex': facial_object.object_index,
            'Vertices': cse.encode_field(struct.pack(f'>{len(mapped)}H', *mapped), use_compression),
            'PoseDeltas': [
                cse.encode_field(struct.pack(f'>{len(rows) * 6}h', *(v for row in rows for v in row)), use_compression)
                for rows in records
            ],
        })
    return result


def mode_reason(object_name: str, reasons: Sequence[str]) -> str:
    return f'body rebuild of {object_name} ({", ".join(reasons)})'


strip_in_place_edits = rre.strip_in_place_edits
