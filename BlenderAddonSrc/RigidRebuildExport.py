"""Pure-Python side of the rigid rebuild export (PLAN_EditRigidMeshes.md Phase 4).

bpy-free, like CustomSubmeshExport.py, so the decision rules and the encoding
can be unit-tested outside Blender. ExportSluggies.py reads the scene and
hands plain lists here.

A donor rigid (CompCount 3) submesh takes one of two paths per export
(decision 1):

- the slot-preserving in-place / Milestone 3 fields when its topology equals
  the donor's, its world-space positions equal the mesh-local ones and every
  face still uses its imported surface;
- a whole-blob ``RigidRebuild`` otherwise -- changed topology, an Object Mode
  transform, a host-bone change that keeps the world position, or faces moved
  between surfaces (including onto a new Add-material surface).

The rebuild keeps the donor's formats (decision 3), pools equal values
(decision 5), mirrors UV channel 1 from channel 0 when the donor channels are
identical (decision 6) and names surfaces by key (decisions 7-8).

A submesh with facial poses (ptr7 objects: every vanilla head) carries them
through the rebuild as ``FacialPoses`` (2026-10-10): per facial object the
rebuilt vertices its shape keys move with ``x y z`` int16 deltas per pose,
and the rebuilt normal-array entries they tilt with ``nx ny nz`` deltas.
Normal entries of facially animated vertices are pooled per vertex, never
shared with another vertex, so each entry has one delta.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:  # inside Blender this module is part of the addon package
    from . import CustomSubmeshExport as cse
    from . import FieldCodec
except ImportError:  # imported flat by the unit tests
    import CustomSubmeshExport as cse
    import FieldCodec

REASON_TOPOLOGY = 'topology'
REASON_TRANSFORM = 'object_transform'
REASON_HOST = 'host_bone_world'
REASON_SURFACES = 'surfaces'

NEW_SURFACE_KEY_RE = re.compile(r'^sm(\d+)_new(\d+)$')
RIGID_COMP_COUNT = 3
_U16_LIMIT = 0xFFFF

# Bytes per colour entry by quantize-format nibble (mirrors binfmt.color_entry_size).
_COLOR_ENTRY_SIZE = {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}

REASSIGN_HINT = (
    'Use Reassign to new bone in the Sluggies sidebar, which puts every vertex in '
    'one bone_<id> group.'
)


def _decode(value) -> bytes:
    return FieldCodec.decode_field(value) if value else b''


def _u16s(raw: bytes) -> Tuple[int, ...]:
    return struct.unpack(f'>{len(raw) // 2}H', raw) if raw else ()


def is_rigid_submesh(sub: dict) -> bool:
    """A donor rigid submesh: 3 position components, one owner bone."""
    return int((sub.get('VertexBuffer') or {}).get('VertexBufferCompCount', 0)) == RIGID_COMP_COUNT


# ---------------------------------------------------------------------------
# Donor facts
# ---------------------------------------------------------------------------

@dataclass
class DonorRigidSubmesh:
    """What the rebuild needs to know about the donor submesh."""
    index: int
    position_quantize: int
    normal_format: Optional[Tuple[int, int]]        # (CompCount, QuantizeInfo) or None
    color_format: Optional[Tuple[int, int]]         # (CompCount, QuantizeInfo) or None
    uv_formats: Dict[int, Tuple[int, int]]          # channel -> (CompCount, QuantizeInfo)
    uv_mirrored: bool                               # channel 1 is a byte copy of channel 0
    faces: List[Tuple[int, int, int]]
    face_surfaces: List[str]                        # imported SurfaceId per donor face
    drawable_surfaces: Dict[str, int]               # SurfaceId -> state index, can draw
    facial: bool                                    # a ptr7 facial object references it

    @property
    def owner_key(self) -> str:
        return f'sm{self.index}'


def facial_pose_submeshes(model: dict) -> set:
    objects = (model.get('FacialPoseData') or {}).get('Objects') or []
    return {
        int(entry['SubmeshIndex']) for entry in objects
        if isinstance(entry, dict) and entry.get('SubmeshIndex') is not None
    }


def _effective(states: Sequence[dict], upto: int, state_id: int) -> Optional[dict]:
    found = None
    for state in states[:upto + 1]:
        if int(state.get('DisplayStateId', -1)) == state_id:
            found = state
    return found


def donor_submesh_facts(index: int, sub: dict) -> dict:
    """What any rebuild needs to know about a donor submesh: attribute
    formats, the donor faces with their imported surfaces, and the surfaces
    that can draw. Shared by the rigid and skinned rebuilds."""
    vb = sub['VertexBuffer']
    normals = sub.get('NormalBuffer') if isinstance(sub.get('NormalBuffer'), dict) else None
    normal_format = None
    if normals and normals.get('NormalBufferData'):
        normal_format = (int(normals.get('NormalBufferCompCount', 3)), int(normals['NormalBufferQuantizeInfo']))
    colors = sub.get('ColorChannels') or []
    color_format = None
    if colors:
        color_format = (int(colors[0].get('ColorChannelCompCount', 4)), int(colors[0]['ColorChannelQuantizeInfo']))
    uv_channels = sub.get('UVChannels') or []
    uv_formats = {
        int(uv['UVChannelIndex']): (int(uv.get('UVChannelCompCount', 2)), int(uv['UVChannelQuantizeInfo']))
        for uv in uv_channels
    }
    by_index = {int(uv['UVChannelIndex']): uv for uv in uv_channels}
    uv_mirrored = (
        set(by_index) == {0, 1}
        and _decode(by_index[0].get('UVChannelData')) == _decode(by_index[1].get('UVChannelData'))
        and _decode(by_index[0].get('UVFacesData')) == _decode(by_index[1].get('UVFacesData'))
    )
    flat = _u16s(_decode(sub.get('FacesData')))
    faces = [tuple(flat[i:i + 3]) for i in range(0, len(flat) - len(flat) % 3, 3)]
    states = sub.get('DisplayStates') or []
    face_surfaces: List[str] = []
    for k, state in enumerate(states):
        count = state.get('FaceCount')
        if count is None:
            face_surfaces = []
            break
        face_surfaces += [state.get('SurfaceId') or f'sm{index}_ds{k}'] * int(count)
    if len(face_surfaces) != len(faces):
        face_surfaces = []
    drawable = {
        state['SurfaceId']: k for k, state in enumerate(states)
        if state.get('SurfaceId')
        and _effective(states, k, 3) is not None
        and _effective(states, k, 7) is not None
    }
    return dict(
        index=index,
        position_quantize=int(vb['VertexBufferQuantizeInfo']),
        normal_format=normal_format,
        color_format=color_format,
        uv_formats=uv_formats,
        uv_mirrored=uv_mirrored,
        faces=faces,
        face_surfaces=face_surfaces,
        drawable_surfaces=drawable,
    )


def donor_rigid_submesh(index: int, sub: dict, facial_submeshes: Iterable[int] = ()) -> DonorRigidSubmesh:
    vb = sub['VertexBuffer']
    if int(vb.get('VertexBufferCompCount', 0)) != RIGID_COMP_COUNT:
        raise ValueError(f'submesh {index} is not a rigid submesh (CompCount {vb.get("VertexBufferCompCount")})')
    return DonorRigidSubmesh(facial=index in set(facial_submeshes), **donor_submesh_facts(index, sub))


def donor_position_format_error(donor: DonorRigidSubmesh, object_name: str) -> Optional[str]:
    """Decision 3: only int16 donor positions can be rebuilt."""
    if (donor.position_quantize >> 4) == 3:
        return None
    return (
        f'{object_name}: this submesh stores positions in format {donor.position_quantize} '
        f'(not int16), which the rigid rebuild does not support; keep its topology, '
        'transform and surfaces unchanged.'
    )


# ---------------------------------------------------------------------------
# The path decision (decision 1)
# ---------------------------------------------------------------------------

def vertex_bone_problem(object_name: str, vertex_bone_ids: Sequence[Iterable[int]]) -> Optional[str]:
    """G2: *vertex_bone_ids* holds, per vertex, the bone ids of its
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


def quantized_positions(positions: Sequence[Sequence[float]], quantize_info: int) -> List[Tuple[int, int, int]]:
    divisor = cse.int16_divisor(quantize_info)
    return [tuple(int(round(float(v) * divisor)) for v in position) for position in positions]


def positions_moved(mesh_local: Sequence[Sequence[float]], geometry: cse.BoneLocalGeometry, quantize_info: int) -> bool:
    """Whether the bone-local positions differ from the mesh-local ones once
    both are quantized in the donor format. The importer places a rigid mesh
    at its owner bone's bind matrix, so an untouched object round-trips to
    equal values; an Object Mode move, or a host change that keeps the world
    position, does not."""
    local = quantized_positions([mesh_local[i] for i in geometry.source_vertices], quantize_info)
    bone = quantized_positions(geometry.positions, quantize_info)
    return local != bone


def decide_reasons(*, topology_changed: bool, moved: bool, host_changed: bool, surfaces_changed: bool) -> List[str]:
    reasons = []
    if topology_changed:
        reasons.append(REASON_TOPOLOGY)
    if moved:
        reasons.append(REASON_HOST if host_changed else REASON_TRANSFORM)
    if surfaces_changed:
        reasons.append(REASON_SURFACES)
    return reasons


# ---------------------------------------------------------------------------
# Face -> surface routing (slot hygiene, decisions 7-8)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SlotSurface:
    """What one material slot says about its surface."""
    material_name: str
    surface_id: Optional[str]          # donor SurfaceId or new surface key; None = no metadata
    new_surface: bool = False
    owner: Optional[str] = None        # SluggiesSurfaceOwner of a new surface


def new_surface_key_matches(owner_key: str, surface_id: str) -> bool:
    """Whether *surface_id* is an Add-material key of *owner_key*:
    ``sm<N>_new<K>`` on a donor submesh, ``<CustomSubmeshId>_new<K>`` on a
    custom submesh (PLAN_EditRigidMeshes.md decision 8)."""
    return re.match(rf'^{re.escape(owner_key)}_new\d+$', surface_id or '') is not None


@dataclass
class CustomSubmeshSurfaces:
    """The surface facts of a custom submesh in the shape ``route_faces``
    reads from a donor (Phase 9): its one primary surface, no imported
    per-face surfaces."""
    custom_submesh_id: str
    primary_surface_id: str

    @property
    def owner_key(self) -> str:
        return self.custom_submesh_id

    @property
    def drawable_surfaces(self) -> Dict[str, int]:
        return {self.primary_surface_id: 0}

    @property
    def face_surfaces(self) -> List[str]:
        return []


@dataclass
class SurfaceRouting:
    table: List[str]
    indices: List[int]                 # per exported triangle
    new_keys: List[str]                # new surface keys in table order
    changed: bool                      # any face off its imported surface, or a new surface in use
    counts_before: Dict[str, int]
    counts_after: Dict[str, int]
    warnings: List[str] = field(default_factory=list)


def route_faces(
    object_name: str,
    donor: DonorRigidSubmesh,
    triangles: Sequence[cse.Triangle],
    polygon_slots: Sequence[int],
    slots: Sequence[Optional[SlotSurface]],
    topology_changed: bool,
) -> SurfaceRouting:
    """Decide which surface each exported triangle draws through, from its
    polygon's material slot.

    - A face on an empty slot, on a material without surface metadata, or on
      another submesh's surface cancels the export (names object, material
      and face count).
    - Two slots holding the same surface merge, with a warning.
    - ``changed`` compares against the imported surface of the triangle's
      polygon when the topology is unchanged; with a changed topology the
      rebuild is already decided, and the surfaces are simply what the
      materials say.
    """
    warnings: List[str] = []
    slot_keys: List[Optional[str]] = []     # surface key per slot; None = unusable
    slot_problems: Dict[int, str] = {}      # why a slot is unusable
    seen: Dict[str, int] = {}
    for slot_index, slot in enumerate(slots):
        key = None
        if slot is None or not slot.surface_id:
            slot_problems[slot_index] = (
                f"material '{slot.material_name}' has no Sluggies SurfaceId" if slot is not None
                else 'an empty material slot'
            )
        elif slot.new_surface:
            if slot.owner != donor.owner_key:
                slot_problems[slot_index] = (
                    f"material '{slot.material_name}' belongs to {slot.owner or 'another submesh'}, "
                    f'not {donor.owner_key}'
                )
            elif not new_surface_key_matches(donor.owner_key, slot.surface_id):
                slot_problems[slot_index] = (
                    f"material '{slot.material_name}' has an invalid new-surface key {slot.surface_id!r}"
                )
            else:
                key = slot.surface_id
        elif slot.surface_id in donor.drawable_surfaces:
            key = slot.surface_id
        else:
            slot_problems[slot_index] = (
                f"material '{slot.material_name}' ({slot.surface_id}) is not a surface of this submesh"
            )
        if key is not None:
            if key in seen:
                warnings.append(
                    f"{object_name}: material slots {seen[key]} and {slot_index} both draw surface "
                    f'{key}; their faces are merged into one surface.'
                )
            else:
                seen[key] = slot_index
        slot_keys.append(key)

    table: List[str] = []
    table_index: Dict[str, int] = {}
    indices: List[int] = []
    problem_faces: Dict[int, int] = {}
    for tri in triangles:
        slot_index = polygon_slots[tri.polygon_index] if tri.polygon_index < len(polygon_slots) else -1
        key = slot_keys[slot_index] if 0 <= slot_index < len(slot_keys) else None
        if key is None:
            problem_faces[slot_index] = problem_faces.get(slot_index, 0) + 1
            continue
        if key not in table_index:
            table_index[key] = len(table)
            table.append(key)
        indices.append(table_index[key])
    errors = [
        f"{slot_problems.get(slot_index, 'an empty material slot')} ({count} face(s))"
        for slot_index, count in sorted(problem_faces.items())
    ]
    if errors:
        raise ValueError(
            f"{object_name}: every face must use one of this submesh's own materials "
            f"(imported, or added with Add material): " + '; '.join(errors)
        )

    new_keys = [key for key in table if key not in donor.drawable_surfaces]
    changed = bool(new_keys)
    if not changed and not topology_changed and donor.face_surfaces:
        for tri, index in zip(triangles, indices):
            if tri.polygon_index >= len(donor.face_surfaces) or donor.face_surfaces[tri.polygon_index] != table[index]:
                changed = True
                break
    counts_before: Dict[str, int] = {}
    for surface in donor.face_surfaces:
        counts_before[surface] = counts_before.get(surface, 0) + 1
    counts_after: Dict[str, int] = {}
    for index in indices:
        counts_after[table[index]] = counts_after.get(table[index], 0) + 1
    return SurfaceRouting(table, indices, new_keys, changed, counts_before, counts_after, warnings)


def route_custom_submesh_faces(
    object_name: str,
    custom_submesh_id: str,
    primary_surface_id: str,
    triangles: Sequence[cse.Triangle],
    polygon_slots: Sequence[int],
    slots: Sequence[Optional[SlotSurface]],
) -> SurfaceRouting:
    """``route_faces`` for a custom submesh (PLAN_EditRigidMeshes.md Phase
    9): every face must use the primary ``<id>_ds0`` material or an
    Add-material surface owned by this custom submesh; ``new_keys`` lists
    the additional surfaces in first-use order."""
    donor = CustomSubmeshSurfaces(custom_submesh_id, primary_surface_id)
    return route_faces(object_name, donor, triangles, polygon_slots, slots, topology_changed=True)


def custom_submesh_face_surface_indices(routing: SurfaceRouting) -> List[int]:
    """Per triangle, the ``CustomSubmeshes[].FaceSurfaceIndices`` value: 0
    for the primary surface, ``k`` for ``new_keys[k - 1]``."""
    position = {key: k for k, key in enumerate(routing.new_keys, start=1)}
    return [position.get(routing.table[index], 0) for index in routing.indices]


def surface_report(object_name: str, routing: SurfaceRouting) -> str:
    keys = list(dict.fromkeys(list(routing.counts_before) + routing.table))
    parts = [f'{key} {routing.counts_before.get(key, 0)} -> {routing.counts_after.get(key, 0)}' for key in keys]
    return f'{object_name}: faces per surface ' + ', '.join(parts)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode_color_entry(quant_info: int, rgba) -> bytes:
    """One colour entry in the donor channel's format (the same layout
    ExportSluggies._encode_color_entry writes and ImportSluggies decodes)."""
    fmt = quant_info >> 4
    r, g, b, a = (max(0.0, min(1.0, float(c))) for c in rgba)
    if fmt == 0:  # RGB565
        value = ((int(round(r * 31)) & 0x1F) << 11) | ((int(round(g * 63)) & 0x3F) << 5) | (int(round(b * 31)) & 0x1F)
        return value.to_bytes(2, 'big')
    if fmt == 3:  # RGBA4444
        value = ((int(round(r * 15)) & 0xF) << 12) | ((int(round(g * 15)) & 0xF) << 8) \
            | ((int(round(b * 15)) & 0xF) << 4) | (int(round(a * 15)) & 0xF)
        return value.to_bytes(2, 'big')
    if fmt in (1, 4):  # RGB8
        return bytes((int(round(r * 255)), int(round(g * 255)), int(round(b * 255))))
    if fmt in (2, 5):  # RGBA8
        return bytes((int(round(r * 255)), int(round(g * 255)), int(round(b * 255)), int(round(a * 255))))
    raise ValueError(f'unsupported color channel format {fmt} (quantizeInfo=0x{quant_info:X})')


def _check_u16(object_name: str, label: str, count: int) -> None:
    if count > _U16_LIMIT:
        raise ValueError(f'{object_name}: {count} distinct {label} exceed the uint16 index range')


def _index_buffer(indices, use_base64: bool):
    return cse.encode_field(struct.pack(f'>{len(indices)}H', *indices), use_base64)


def encode_color_channel(
    object_name: str, color_format, geometry, loop_colors, use_base64: bool, infos: Optional[List[str]] = None,
) -> dict:
    """``ColorChannelData`` / ``ColorFacesData`` for a rebuilt donor submesh:
    per-loop colours pooled in the donor channel's format (white when the
    mesh has no colour attribute; unset (0, 0, 0, 0) corners written white,
    see ``CustomSubmeshExport.fill_unset_colors``). Shared by the rigid and
    skinned rebuilds."""
    loops = [loop for tri in geometry.triangles for loop in tri.loops]
    colors = [cse.WHITE] * len(loops) if loop_colors is None else [loop_colors[loop] for loop in loops]
    colors = cse.fill_unset_colors(object_name, colors, infos)
    color_data, color_indices = cse.dedupe_records([encode_color_entry(color_format[1], c) for c in colors])
    _check_u16(object_name, 'colors', len(color_data) // _COLOR_ENTRY_SIZE[color_format[1] >> 4])
    return {
        'ColorChannelData': cse.encode_field(color_data, use_base64),
        'ColorFacesData': _index_buffer(color_indices, use_base64),
    }


def encode_uv_channels(
    object_name: str, uv_formats: Dict[int, Tuple[int, int]], uv_mirrored: bool, geometry,
    loop_uvs_by_channel: Dict[int, Optional[Sequence[Tuple[float, float]]]], use_base64: bool,
    warnings: List[str], infos: List[str],
) -> list:
    """The ``UVChannels`` list of a rebuilt donor submesh, one entry per donor
    channel in its own format. Channel 1 mirrors channel 0 when the donor
    channels are identical (decision 6) or when its Blender layer is missing.
    Shared by the rigid and skinned rebuilds."""
    encoded_uvs: Dict[int, Tuple[bytes, list]] = {}
    channels = sorted(uv_formats)
    for channel in channels:
        loop_uvs = loop_uvs_by_channel.get(channel)
        mirror_source = None
        if uv_mirrored and channel == 1:
            mirror_source = 0
            if loop_uvs is not None and loop_uvs_by_channel.get(0) is not None \
                    and list(loop_uvs) != list(loop_uvs_by_channel[0]):
                infos.append(
                    f'{object_name}: UV channel 1 mirrors channel 0 in the donor (specular), so its '
                    'separate edits are ignored and channel 0 is written to both.'
                )
        elif loop_uvs is None:
            if loop_uvs_by_channel.get(0) is None:
                raise ValueError(f'{object_name}: UV map for channel {channel} not found')
            mirror_source = 0
            warnings.append(
                f'{object_name}: UV map for channel {channel} not found; channel 0 is written to it.'
            )
        if mirror_source is not None:
            uv_data, uv_indices = encoded_uvs[mirror_source]
            if uv_formats[channel] != uv_formats[mirror_source]:
                uv_data, uv_indices = cse.encode_loop_uvs(
                    object_name, geometry, loop_uvs_by_channel[mirror_source], uv_formats[channel],
                )
        else:
            uv_data, uv_indices = cse.encode_loop_uvs(object_name, geometry, loop_uvs, uv_formats[channel])
        _check_u16(object_name, f'UVs on channel {channel}', len(uv_indices) and max(uv_indices) + 1)
        encoded_uvs[channel] = (uv_data, uv_indices)
    return [
        {
            'UVChannelIndex': channel,
            'UVChannelData': cse.encode_field(encoded_uvs[channel][0], use_base64),
            'UVFacesData': _index_buffer(encoded_uvs[channel][1], use_base64),
        }
        for channel in channels
    ]


def build_rigid_rebuild_entry(
    object_name: str,
    donor: DonorRigidSubmesh,
    host_bone_id: int,
    geometry: cse.BoneLocalGeometry,
    loop_normals,
    loop_uvs_by_channel: Dict[int, Optional[Sequence[Tuple[float, float]]]],
    loop_colors,
    routing: SurfaceRouting,
    new_surfaces: Sequence[dict],
    reasons: Sequence[str],
    use_base64: bool = True,
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
    facial_entries = None
    pinned_normal_data = None
    if facial_keys is not None:
        facial_entries, pinned_normal_data = encode_rigid_facial_poses(
            object_name, facial_keys, geometry, donor.position_quantize,
            donor.normal_format if loop_normals is not None else None, loop_normals,
            use_base64, warnings, infos,
        )
    position_format = (RIGID_COMP_COUNT, donor.position_quantize)
    try:
        cse.check_position_range(object_name, host_bone_id, geometry, donor.position_quantize)
    except ValueError as error:
        raise ValueError(
            f'{error} The donor stores positions in format {donor.position_quantize}, which a rebuilt '
            'donor submesh keeps.'
        ) from None
    positions = cse.encode_positions(object_name, geometry, position_format)
    faces_data, faces_count = cse.encode_faces(object_name, geometry.faces)
    if len(routing.indices) != faces_count:
        raise ValueError(f'{object_name}: {len(routing.indices)} surface indices for {faces_count} faces')

    entry = {
        'HostBoneId': int(host_bone_id),
        'VertexBufferData': cse.encode_field(positions, use_base64),
    }
    if donor.normal_format:
        if pinned_normal_data is not None:
            normal_data, normal_indices = pinned_normal_data
        else:
            normal_data, normal_indices = cse.encode_loop_normals(object_name, geometry, loop_normals, donor.normal_format)
        _check_u16(object_name, 'normals', len(normal_indices) and max(normal_indices) + 1)
        entry['NormalBufferData'] = cse.encode_field(normal_data, use_base64)
        entry['NormalFacesData'] = _index_buffer(normal_indices, use_base64)
    if donor.color_format:
        entry.update(encode_color_channel(object_name, donor.color_format, geometry, loop_colors, use_base64, infos))

    entry['UVChannels'] = encode_uv_channels(
        object_name, donor.uv_formats, donor.uv_mirrored, geometry, loop_uvs_by_channel,
        use_base64, warnings, infos,
    )
    entry['FacesCount'] = faces_count
    entry['FacesData'] = cse.encode_field(faces_data, use_base64)
    entry['FaceSurfaceTable'] = list(routing.table)
    entry['FaceSurfaceIndices'] = _index_buffer(routing.indices, use_base64)
    if new_surfaces:
        entry['NewSurfaces'] = [dict(surface) for surface in new_surfaces]
    entry['Reason'] = list(reasons)
    if facial_entries is not None:
        entry['FacialPoses'] = facial_entries
    return entry


# ---------------------------------------------------------------------------
# Facial poses (ptr7) on a rebuilt submesh
# ---------------------------------------------------------------------------
# Shared with the skinned body (PLAN_ModelReplacements.md Milestone 4.7):
# the importer names a facial object's shape keys FACIAL_KEY_NAME, and the
# exporter reads them back as FacialObjectKeys.

FACIAL_KEY_NAME = 'facial_object_{object}_pose_{pose}'
_AXES = ('x', 'y', 'z')


@dataclass
class FacialObjectKeys:
    """One facial object's shape keys as plain data, per Blender vertex:
    ``pose_positions[k]`` / ``pose_normals[k]`` belong to pose ``k + 1``
    (None when the key is missing or normals are unavailable); the Basis
    key gives ``basis_positions`` / ``basis_normals``."""
    object_index: int
    pose_count: int
    pose_positions: list
    pose_normals: list
    basis_positions: list
    basis_normals: Optional[list] = None


def facial_warning(object_name: str, donor, shape_key_count: int, carried: bool = False) -> Optional[str]:
    """The rebuild renumbers the vertices, so the donor's facial poses on
    this submesh can only follow through the mesh's shape keys
    (``FacialPoses``); without them the patcher drops (body) or neutralizes
    (rigid submesh) the poses."""
    if not donor.facial or carried:
        return None
    note = (
        ' Its shape keys lack the Basis key, so they are not exported.' if shape_key_count
        else ' Re-import the mesh to get its facial shape keys back.'
    )
    return (
        f"{object_name}: this model's blink and mouth poses animate this mesh's vertices, which "
        f'the rebuild renumbers, and the mesh has no facial shape keys to rebuild them from, so '
        f'the patched model will have no facial animation on it.{note}'
    )


def _loop_vertices(geometry: cse.BoneLocalGeometry) -> List[int]:
    """The exported vertex of every loop in ``cse._loop_order`` order."""
    return [vertex for face in geometry.faces for vertex in face]


def encode_facial_loop_normals(
    object_name: str, geometry: cse.BoneLocalGeometry, loop_normals, normal_format: Tuple[int, int],
    pinned_vertices,
) -> Tuple[bytes, List[int]]:
    """``cse.encode_loop_normals`` with the normal entries of
    *pinned_vertices* (exported indices) pooled per vertex: two loops share
    an entry only when they hold the same normal and belong to the same
    pinned vertex, or to no pinned vertex at all. A facial pose then gives
    every entry one delta."""
    comp_count, quantize_info = normal_format
    divisor = cse.int16_divisor(quantize_info)
    loops = [loop for tri in geometry.triangles for loop in tri.loops]
    transformed = cse.transform_normals([loop_normals[loop] for loop in loops], geometry.to_bone)
    pinned = set(pinned_vertices)
    pool: Dict[tuple, int] = {}
    data = bytearray()
    indices = []
    for loop, normal, vertex in zip(loops, transformed, _loop_vertices(geometry)):
        values = [cse.quantize_int16(v, divisor, f'{object_name} loop {loop} normal') for v in normal]
        values += [0] * (comp_count - 3)
        record = struct.pack(f'>{comp_count}h', *values)
        key = (record, vertex if vertex in pinned else None)
        index = pool.get(key)
        if index is None:
            index = pool[key] = len(pool)
            data += record
        indices.append(index)
    return bytes(data), indices


def encode_rigid_facial_poses(
    object_name: str, objects: Sequence[FacialObjectKeys], geometry: cse.BoneLocalGeometry,
    position_quantize: int, normal_format: Optional[Tuple[int, int]] = None, loop_normals=None,
    use_base64: bool = True, warnings: Optional[List[str]] = None, infos: Optional[List[str]] = None,
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
    divisor = cse.int16_divisor(position_quantize)
    normal_divisor = cse.int16_divisor(normal_format[1]) if normal_format is not None and loop_normals is not None else None
    matrix = geometry.to_bone
    per_object = []
    tilted: set = set()
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
        position_rows: List[List[tuple]] = []
        normal_rows: List[List[tuple]] = []
        for exported, blender_vertex in enumerate(geometry.source_vertices):
            positions_of_vertex, normals_of_vertex = [], []
            for k, pose in enumerate(pose_indices):
                positions = facial_object.pose_positions[k]
                if positions is None:
                    positions_of_vertex.append((0, 0, 0))
                    normals_of_vertex.append((0, 0, 0))
                    continue
                context = f'{object_name} facial object {facial_object.object_index} pose {pose} vertex {blender_vertex}'
                rest = cse.transform_point(matrix, facial_object.basis_positions[blender_vertex])
                posed = cse.transform_point(matrix, positions[blender_vertex])
                positions_of_vertex.append(tuple(
                    cse.quantize_int16(posed[axis] - rest[axis], divisor, f'{context} {name}')
                    for axis, name in enumerate(_AXES)
                ))
                normals = facial_object.pose_normals[k]
                if normal_divisor is not None and normals is not None and facial_object.basis_normals is not None:
                    basis_normal, pose_normal = cse.transform_normals(
                        [facial_object.basis_normals[blender_vertex], normals[blender_vertex]], matrix,
                    )
                    normals_of_vertex.append(tuple(
                        cse.quantize_int16(pose_normal[axis] - basis_normal[axis], normal_divisor, f'{context} n{name}')
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
            'Vertices': cse.encode_field(struct.pack(f'>{len(moved)}H', *moved), use_base64),
            'PoseDeltas': [
                cse.encode_field(struct.pack(f'>{3 * len(moved)}h', *(v for rows in vertex_rows for v in rows[k])), use_base64)
                for k in range(later)
            ],
        }
        normal_note = ''
        if normal_arrays is not None:
            _data, normal_indices = normal_arrays
            entry_vertex: Dict[int, int] = {}
            for index, vertex in zip(normal_indices, _loop_vertices(geometry)):
                if vertex in tilted and any(any(row) for row in normal_rows[vertex]):
                    entry_vertex[index] = vertex
            normal_entries = sorted(entry_vertex)
            entry_rows = [normal_rows[entry_vertex[index]] for index in normal_entries]
            if not normal_entries:
                normal_entries = [0]
                entry_rows = [[(0, 0, 0)] * later]
            entry['NormalEntries'] = cse.encode_field(struct.pack(f'>{len(normal_entries)}H', *normal_entries), use_base64)
            entry['NormalPoseDeltas'] = [
                cse.encode_field(struct.pack(f'>{3 * len(normal_entries)}h', *(v for rows in entry_rows for v in rows[k])), use_base64)
                for k in range(later)
            ]
            normal_note = f', {len(entry_vertex)} normal entr{"y" if len(entry_vertex) == 1 else "ies"}'
        if nothing_moves:
            warnings.append(
                f'{object_name}: facial object {facial_object.object_index} moves no vertex in any '
                'shape key; it is kept with one unmoving entry.'
            )
        infos.append(
            f'{object_name}: facial object {facial_object.object_index}: {len(moved)} vertex(es){normal_note} '
            f'animated over {later} pose(s).'
        )
        entries.append(entry)
    return entries, normal_arrays


# Every in-place (Milestone 3) edit field a rebuilt submesh must not carry
# (decision 1; the patcher refuses the mix).
IN_PLACE_SUBMESH_FIELDS = ('FacesDataEdited', 'FacesCountEdited', 'FaceTextureIndicesEdited', 'FaceSurfaceIdsEdited')
IN_PLACE_CHANNEL_FIELDS = {
    'VertexBuffer': ('VertexBufferDataEdited',),
    'NormalBuffer': ('NormalBufferDataEdited', 'NormalFacesDataEdited'),
    'UVChannels': ('UVChannelDataEdited', 'UVFacesDataEdited'),
    'ColorChannels': ('ColorChannelDataEdited', 'ColorFacesDataEdited'),
}


GEO_ID_FREE = 0xFFFF


@dataclass
class CarriedRebuilds:
    """What :func:`carry_unselected_rebuilds` decided."""
    additions: List[dict]       # AdditionalTextureDescriptors entries to keep
    messages: List[str]         # one info line per kept rebuild
    errors: List[str]           # conflicts that must stop the export


def _donor_geo_raw(bone: dict) -> int:
    if bone.get('GeoIdRaw') is not None:
        return int(bone['GeoIdRaw'])
    if bone.get('Skinned'):
        return GEO_ID_FREE
    return int(bone.get('GeoId', GEO_ID_FREE))


def carry_unselected_rebuilds(
    model: dict,
    exported_indices: Iterable[int],
    previous_additions: Sequence[dict],
    recover_addition=None,
) -> CarriedRebuilds:
    """Keep a ``RigidRebuild`` from an earlier export consistent when its
    mesh is not part of this export.

    Edits on unselected submeshes stay in the ``.sluggie`` (that is why
    ``ExportMode.stale_hammerspace_submeshes`` forces Hammerspace for them),
    but two model-level facts a rebuild depends on are re-derived from the
    selection on every export: ``BoneHierarchy[].GeoIdEdited`` and
    ``AdditionalTextureDescriptors``. Without this, a cap rebuilt onto
    another bone with a new-PNG surface lost both, and the patcher refused
    the file (host bone mismatch, unknown texture). Call after this
    export's ``GeoIdEdited`` and custom submeshes are final.

    - The rebuild's host bone move is written again as ``GeoIdEdited`` (old
      owner free, host = the submesh), unless this export already gave
      either bone to another mesh: then it is an error that names the mesh.
    - Each ``AdditionalTextureFileName`` its new surfaces bind is kept from
      *previous_additions*. One missing there (an export from before this
      fix dropped it) is rebuilt by ``recover_addition(file_name,
      template_source)`` when given, which returns the
      ``TemplateTextureIndex`` or None when the PNG is gone; otherwise it is
      an error.
    """
    exported = set(exported_indices)
    bones = model.get('BoneHierarchy') or []
    bone_by_id = {int(b['BoneId']): b for b in bones if 'BoneId' in b}
    custom_hosts = {
        int(cs['HostBoneId']): str(cs.get('CustomSubmeshId', '?'))
        for cs in model.get('CustomSubmeshes') or [] if cs.get('HostBoneId') is not None
    }
    previous_by_name = {a.get('TextureFileName'): a for a in previous_additions or []}
    result = CarriedRebuilds([], [], [])

    def effective(bone: dict) -> int:
        edited = bone.get('GeoIdEdited')
        return int(edited) if edited is not None else _donor_geo_raw(bone)

    for index, sub in enumerate(model.get('Submeshes') or []):
        rebuild = sub.get('RigidRebuild') or sub.get('SkinnedRebuild')
        if not rebuild or index in exported:
            continue
        name = sub.get('MeshName') or f'submesh {index}'
        label = f"{name} (submesh {index}, not selected)"
        fix = 'select it and export again, or re-import the model to drop the earlier edit'
        host = int(rebuild.get('HostBoneId', -1))
        owners = [bone_id for bone_id, bone in bone_by_id.items() if _donor_geo_raw(bone) == index]
        rigid = 'HostBoneId' in rebuild            # a rebuilt body stays on the skeleton
        if rigid and host not in bone_by_id:
            result.errors.append(f'{label}: its earlier rebuild hosts it on unknown bone {host}; {fix}.')
            continue
        if rigid and host in custom_hosts:
            result.errors.append(
                f"{label}: its earlier rebuild moved it to bone_{host}, which this export gives to "
                f"custom submesh '{custom_hosts[host]}'; {fix}.")
            continue
        if rigid and host not in owners:
            current = effective(bone_by_id[host])
            if current not in (GEO_ID_FREE, index):
                result.errors.append(
                    f'{label}: its earlier rebuild moved it to bone_{host}, which this export gives to '
                    f'submesh {current}; {fix}.')
                continue
            taken = [o for o in owners if effective(bone_by_id[o]) not in (GEO_ID_FREE, index)]
            if taken:
                result.errors.append(
                    f'{label}: its original bone_{taken[0]} now carries submesh '
                    f'{effective(bone_by_id[taken[0]])}; {fix}.')
                continue
            for owner in owners:
                bone_by_id[owner]['GeoIdEdited'] = GEO_ID_FREE
            bone_by_id[host]['GeoIdEdited'] = index

        missing, kept = [], []
        for surface in rebuild.get('NewSurfaces') or []:
            file_name = (surface.get('TextureAssignment') or {}).get('AdditionalTextureFileName')
            if not file_name:
                continue
            addition = previous_by_name.get(file_name)
            if addition is None and recover_addition is not None:
                template_index = recover_addition(file_name, str(surface.get('TemplateSource') or ''))
                if template_index is not None:
                    addition = {'TextureFileName': file_name, 'TemplateTextureIndex': int(template_index)}
                    previous_by_name[file_name] = addition
            if addition is None:
                missing.append(file_name)
                continue
            kept.append(file_name)
            if all(a.get('TextureFileName') != file_name for a in result.additions):
                result.additions.append(dict(addition))
        if missing:
            result.errors.append(
                f"{label}: its earlier rebuild binds {', '.join(missing)}, which the file no longer "
                f'lists; {fix}.')
            continue
        result.messages.append(
            f'{label}: kept its earlier '
            + (f'rigid rebuild (bone_{host}' if rigid else 'body rebuild (')
            + (f", textures {', '.join(kept)}" if kept else '')
            + '). Select it to change it.')
    return result


def strip_in_place_edits(sub: dict) -> None:
    """Remove every in-place edit field from a submesh dict, in place."""
    for key in IN_PLACE_SUBMESH_FIELDS:
        sub.pop(key, None)
    for container, keys in IN_PLACE_CHANNEL_FIELDS.items():
        entries = sub.get(container)
        if isinstance(entries, dict):
            entries = [entries]
        for entry in entries or []:
            for key in keys:
                entry.pop(key, None)


def mode_reason(object_name: str, reasons: Sequence[str]) -> str:
    """The export-mode reason line for one rebuilt submesh."""
    return f'rigid rebuild of {object_name} ({", ".join(reasons)})'
