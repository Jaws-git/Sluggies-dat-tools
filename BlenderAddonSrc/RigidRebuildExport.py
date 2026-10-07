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


def donor_rigid_submesh(index: int, sub: dict, facial_submeshes: Iterable[int] = ()) -> DonorRigidSubmesh:
    vb = sub['VertexBuffer']
    if int(vb.get('VertexBufferCompCount', 0)) != RIGID_COMP_COUNT:
        raise ValueError(f'submesh {index} is not a rigid submesh (CompCount {vb.get("VertexBufferCompCount")})')
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
    return DonorRigidSubmesh(
        index=index,
        position_quantize=int(vb['VertexBufferQuantizeInfo']),
        normal_format=normal_format,
        color_format=color_format,
        uv_formats=uv_formats,
        uv_mirrored=uv_mirrored,
        faces=faces,
        face_surfaces=face_surfaces,
        drawable_surfaces=drawable,
        facial=index in set(facial_submeshes),
    )


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
) -> dict:
    """Assemble ``Submeshes[i].RigidRebuild`` from bone-local geometry and
    per-loop attributes (indexed by Blender loop index), in the donor's own
    formats. *loop_uvs_by_channel* maps each donor UV channel to its per-loop
    coordinates, or None when the Blender layer is missing. *infos* collects
    expected, harmless notes (reported at INFO level)."""
    warnings = warnings if warnings is not None else []
    infos = infos if infos is not None else []
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
        normal_data, normal_indices = cse.encode_loop_normals(object_name, geometry, loop_normals, donor.normal_format)
        _check_u16(object_name, 'normals', len(normal_indices) and max(normal_indices) + 1)
        entry['NormalBufferData'] = cse.encode_field(normal_data, use_base64)
        entry['NormalFacesData'] = _index_buffer(normal_indices, use_base64)
    if donor.color_format:
        loops = [loop for tri in geometry.triangles for loop in tri.loops]
        colors = [cse.WHITE] * len(loops) if loop_colors is None else [loop_colors[loop] for loop in loops]
        color_data, color_indices = cse.dedupe_records([encode_color_entry(donor.color_format[1], c) for c in colors])
        _check_u16(object_name, 'colors', len(color_data) // _COLOR_ENTRY_SIZE[donor.color_format[1] >> 4])
        entry['ColorChannelData'] = cse.encode_field(color_data, use_base64)
        entry['ColorFacesData'] = _index_buffer(color_indices, use_base64)

    encoded_uvs: Dict[int, Tuple[bytes, list]] = {}
    channels = sorted(donor.uv_formats)
    for channel in channels:
        loop_uvs = loop_uvs_by_channel.get(channel)
        mirror_source = None
        if donor.uv_mirrored and channel == 1:
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
            if donor.uv_formats[channel] != donor.uv_formats[mirror_source]:
                uv_data, uv_indices = cse.encode_loop_uvs(
                    object_name, geometry, loop_uvs_by_channel[mirror_source], donor.uv_formats[channel],
                )
        else:
            uv_data, uv_indices = cse.encode_loop_uvs(object_name, geometry, loop_uvs, donor.uv_formats[channel])
        _check_u16(object_name, f'UVs on channel {channel}', len(uv_indices) and max(uv_indices) + 1)
        encoded_uvs[channel] = (uv_data, uv_indices)
    entry['UVChannels'] = [
        {
            'UVChannelIndex': channel,
            'UVChannelData': cse.encode_field(encoded_uvs[channel][0], use_base64),
            'UVFacesData': _index_buffer(encoded_uvs[channel][1], use_base64),
        }
        for channel in channels
    ]
    entry['FacesCount'] = faces_count
    entry['FacesData'] = cse.encode_field(faces_data, use_base64)
    entry['FaceSurfaceTable'] = list(routing.table)
    entry['FaceSurfaceIndices'] = _index_buffer(routing.indices, use_base64)
    if new_surfaces:
        entry['NewSurfaces'] = [dict(surface) for surface in new_surfaces]
    entry['Reason'] = list(reasons)
    return entry


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
        rebuild = sub.get('RigidRebuild')
        if not rebuild or index in exported:
            continue
        name = sub.get('MeshName') or f'submesh {index}'
        label = f"{name} (submesh {index}, not selected)"
        fix = 'select it and export again, or re-import the model to drop the earlier edit'
        host = int(rebuild.get('HostBoneId', -1))
        owners = [bone_id for bone_id, bone in bone_by_id.items() if _donor_geo_raw(bone) == index]
        if host not in bone_by_id:
            result.errors.append(f'{label}: its earlier rebuild hosts it on unknown bone {host}; {fix}.')
            continue
        if host in custom_hosts:
            result.errors.append(
                f"{label}: its earlier rebuild moved it to bone_{host}, which this export gives to "
                f"custom submesh '{custom_hosts[host]}'; {fix}.")
            continue
        if host not in owners:
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
            f'{label}: kept its earlier rigid rebuild (bone_{host}'
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
