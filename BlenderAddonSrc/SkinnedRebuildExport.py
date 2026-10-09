"""Pure-Python side of the skinned-body rebuild export
(PLAN_ModelReplacements.md Milestone 4, ``Submeshes[0].SkinnedRebuild``).

bpy-free, like RigidRebuildExport.py, so the decision rules and the
encoding can be unit-tested outside Blender. ExportSluggies.py reads the
scene and hands plain lists here.

The skinned donor submesh (CompCount 6, the body) takes the rebuild path
when its topology changed, when it carries an Object Mode transform, or when
faces moved in a way the slot-preserving paths cannot store (a partial
move, a move between surfaces with different shaders, or an Add-material
surface). Otherwise the Milestone 3 fields apply (position, UV, colour and
weight edits on the donor's own vertices).

What the entry carries, in the donor's own formats: one interleaved
position + normal record per vertex in armature (model) space, triangles,
per-loop colours and UVs, the face -> surface routing, and ``(vertex, bone,
weight)`` influences. The patcher assigns position slots, builds the SKN and
re-encodes the draw lists; facial poses that address submesh 0 are dropped
by it.
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


def decide_reasons(*, topology_changed: bool, moved: bool, surfaces_changed: bool) -> List[str]:
    reasons = []
    if topology_changed:
        reasons.append(REASON_TOPOLOGY)
    if moved:
        reasons.append(REASON_TRANSFORM)
    if surfaces_changed:
        reasons.append(REASON_SURFACES)
    return reasons


def surfaces_need_rebuild(
    routing: 'rre.SurfaceRouting', donor: DonorSkinnedSubmesh, effective_modes: Sequence[Optional[str]],
) -> bool:
    """Whether the face -> surface changes need the rebuild. A complete move
    of a donor surface onto another donor surface with the same effective
    Type-7 shader keeps the slot-preserving path (Milestone 2.4, proven in
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
    """Move every weight on a bone the donor's skin does not use to its
    nearest ancestor that it does use (the external tool's ``to_allowed``):
    such bones have no skinning matrix, so weights on them leave vertices
    invisible or misplaced. A bone with no allowed ancestor loses its
    weight. *parent_of* maps bone id -> parent id (None at the root)."""
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
            f'{object_name}: {count} vertex weight(s) on bone_{bone}, which the original model '
            f'does not skin to, were moved to its nearest skinned ancestor bone_{target}.'
        )
    for bone, count in sorted(remap.dropped.items()):
        lines.append(
            f'{object_name}: {count} vertex weight(s) on bone_{bone} were dropped: neither it nor '
            'any ancestor is a bone the original model skins to.'
        )
    return lines


def unweighted_error(object_name: str, unweighted: Sequence[int]) -> Optional[str]:
    if not unweighted:
        return None
    return (
        f'{object_name}: {len(unweighted)} vertex(es) have no weight on any bone the original '
        f'model skins to. Assign them to bone_<id> vertex groups of skinned bones.'
    )


def encode_influences(vertex_weights: Sequence[dict], source_vertices: Sequence[int], use_base64: bool = True):
    """``Influences``: ``(exported vertex, bone, weight)`` records for the
    exported vertices (``source_vertices[i]`` is the Blender index of
    exported vertex ``i``), weights normalized per vertex."""
    raw = bytearray()
    for index, blender_vertex in enumerate(source_vertices):
        influences = vertex_weights[blender_vertex]
        total = sum(influences.values())
        for bone, weight in sorted(influences.items()):
            raw += INFLUENCE.pack(index, bone, weight / total)
    return cse.encode_field(bytes(raw), use_base64)


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
    """The donor's int16 position format bounds the body (about +/-16 units
    for format 59); the error names the vertex that overshoots most."""
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
        f'({position[0]:.4f}, {position[1]:.4f}, {position[2]:.4f}) in armature space; the donor '
        f'stores body positions in format {quantize_info}, so every axis must stay within '
        f'{low:g}..{high:g}. Scale the body down or move it closer to the armature origin.'
    )


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
    use_base64: bool = True,
    warnings: Optional[List[str]] = None,
    infos: Optional[List[str]] = None,
) -> dict:
    """Assemble ``Submeshes[0].SkinnedRebuild``. *normals* are per exported
    vertex (unit, target space); *vertex_weights* per Blender vertex
    ``{bone: weight}`` on skinned bones only (see :func:`remap_influences`)."""
    warnings = warnings if warnings is not None else []
    infos = infos if infos is not None else []
    range_error = position_range_error(object_name, geometry, donor.position_quantize)
    if range_error:
        raise ValueError(range_error)
    faces_data, faces_count = cse.encode_faces(object_name, geometry.faces)
    if len(routing.indices) != faces_count:
        raise ValueError(f'{object_name}: {len(routing.indices)} surface indices for {faces_count} faces')
    entry = {
        'VertexBufferData': cse.encode_field(encode_vertex_records(object_name, geometry, normals, donor.position_quantize), use_base64),
        'Influences': encode_influences(vertex_weights, geometry.source_vertices, use_base64),
    }
    if donor.color_format:
        entry.update(rre.encode_color_channel(object_name, donor.color_format, geometry, loop_colors, use_base64, infos))
    entry['UVChannels'] = rre.encode_uv_channels(
        object_name, donor.uv_formats, donor.uv_mirrored, geometry, loop_uvs_by_channel,
        use_base64, warnings, infos,
    )
    entry['FacesCount'] = faces_count
    entry['FacesData'] = cse.encode_field(faces_data, use_base64)
    entry['FaceSurfaceTable'] = list(routing.table)
    entry['FaceSurfaceIndices'] = cse.encode_field(struct.pack(f'>{len(routing.indices)}H', *routing.indices), use_base64)
    if new_surfaces:
        entry['NewSurfaces'] = [dict(surface) for surface in new_surfaces]
    entry['Reason'] = list(reasons)
    return entry


def facial_warning(object_name: str, donor: DonorSkinnedSubmesh, shape_key_count: int) -> Optional[str]:
    """The rebuild renumbers the vertices, so the donor's facial poses on
    this submesh cannot follow; the patcher drops them."""
    if not donor.facial:
        return None
    note = ' Its shape keys are not exported.' if shape_key_count else ''
    return (
        f"{object_name}: this model's blink and mouth poses animate the body's vertices, which "
        f'the rebuild renumbers, so the patched model will have no facial animation.{note}'
    )


def mode_reason(object_name: str, reasons: Sequence[str]) -> str:
    return f'body rebuild of {object_name} ({", ".join(reasons)})'


strip_in_place_edits = rre.strip_in_place_edits
