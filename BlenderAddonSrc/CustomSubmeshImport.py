"""What the importer rebuilds from a .sluggie's own additions.

``CustomSubmeshes`` entries (written by ``CustomSubmeshExport.build_custom_submesh_entry``)
decode back into bone-local meshes with per-corner UVs, colours and normals, and the
Add Bone bones of ``BoneHierarchyEdited`` back into the records the importer turns into
Blender bones. Without this a re-import silently dropped both, so a later export from that
scene removed them from the file (user report, 2026-10-18).

Pure (no bpy): the unit tests check it against the exporter's encoder.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

try:  # inside Blender this module is part of the addon package
    from . import FieldCodec
except ImportError:  # imported flat by the unit tests
    import FieldCodec

# The fixed attribute formats of a custom submesh (CustomSubmeshExport.NORMAL_FORMAT,
# UV_FORMAT, COLOR_FORMAT); positions carry their own VertexBufferQuantizeInfo.
NORMAL_QUANTIZE = 62
UV_QUANTIZE = 62


@dataclass
class CustomSubmeshMesh:
    """One ``CustomSubmeshes`` entry, decoded. Every ``loop_*`` list has one
    value per triangle corner in face order (3 per face); None when the entry
    carries no such attribute or its index buffer does not fit."""
    custom_submesh_id: str
    name: str
    host_bone_id: int
    template_source: str
    positions: List[Tuple[float, float, float]]   # bone-local
    faces: List[Tuple[int, int, int]]
    loop_uvs: Optional[List[Tuple[float, float]]]  # Blender (u, v): v = 1 - t
    loop_colors: Optional[List[Tuple[float, float, float, float]]]
    loop_normals: Optional[List[Tuple[float, float, float]]]  # bone-local
    face_surfaces: List[int]                       # 0 = primary, k = k-th AdditionalSurfaces
    texture_assignment: dict
    specular_strength: Optional[int]
    additional_surfaces: List[dict] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


def _raw(value) -> bytes:
    return FieldCodec.decode_field(value) if value else b''


def _u16s(value) -> List[int]:
    raw = _raw(value)
    return list(struct.unpack(f'>{len(raw) // 2}H', raw)) if raw else []


def _s16_records(value, components: int, divisor: int) -> List[tuple]:
    raw = _raw(value)
    count = len(raw) // (2 * components)
    flat = struct.unpack(f'>{count * components}h', raw[:count * components * 2])
    return [tuple(v / divisor for v in flat[i * components:(i + 1) * components]) for i in range(count)]


def _per_corner(records, indices, corners, what, problems):
    """The record each corner points at, or None (with a note) when the index
    buffer does not cover every corner or points outside the records."""
    if not records or not indices:
        return None
    if len(indices) != corners or max(indices) >= len(records):
        problems.append(f'{what}: {len(indices)} indices for {corners} face corners '
                        f'({len(records)} values); not imported')
        return None
    return [records[i] for i in indices]


def decode_rgba4444(raw: bytes) -> List[Tuple[float, float, float, float]]:
    colors = []
    for (value,) in struct.iter_unpack('>H', raw[:len(raw) // 2 * 2]):
        colors.append(tuple(((value >> shift) & 0xF) / 15.0 for shift in (12, 8, 4, 0)))
    return colors


def decode_custom_submesh(entry: dict) -> CustomSubmeshMesh:
    """Decode one ``CustomSubmeshes`` entry. Raises ValueError when the
    geometry itself (positions, faces) is unusable."""
    name = str(entry.get('MeshName') or entry.get('CustomSubmeshId') or 'custom')
    quantize = int(entry.get('VertexBufferQuantizeInfo', 59))
    if (quantize >> 4) != 3:
        raise ValueError(f'{name}: position format {quantize} is not the int16 custom submesh format')
    positions = _s16_records(entry.get('VertexBufferData'), 3, 1 << (quantize & 0xF))
    flat = _u16s(entry.get('FacesData'))
    count = int(entry.get('FacesCount', len(flat) // 3))
    if count <= 0 or len(flat) < count * 3:
        raise ValueError(f'{name}: FacesData holds {len(flat) // 3} triangles, FacesCount says {count}')
    faces = [tuple(flat[3 * k:3 * k + 3]) for k in range(count)]
    if positions and max(flat[:count * 3]) >= len(positions):
        raise ValueError(f'{name}: a face points past the {len(positions)} vertices')
    corners = 3 * count
    problems: List[str] = []

    loop_uvs = None
    uv_channels = sorted(entry.get('UVChannels') or [], key=lambda c: int(c.get('UVChannelIndex', 0)))
    if uv_channels:  # channel 1, when present, mirrors channel 0 (the specular layer)
        channel = uv_channels[0]
        sts = _per_corner(_s16_records(channel.get('UVChannelData'), 2, 1 << (UV_QUANTIZE & 0xF)),
                          _u16s(channel.get('UVFacesData')), corners, f'{name} UVs', problems)
        loop_uvs = None if sts is None else [(s, 1.0 - t) for s, t in sts]

    loop_colors = _per_corner(decode_rgba4444(_raw(entry.get('ColorChannelData'))),
                              _u16s(entry.get('ColorFacesData')), corners, f'{name} colours', problems)
    loop_normals = _per_corner(_s16_records(entry.get('NormalBufferData'), 3, 1 << (NORMAL_QUANTIZE & 0xF)),
                               _u16s(entry.get('NormalFacesData')), corners, f'{name} normals', problems)

    additional = [dict(s) for s in entry.get('AdditionalSurfaces') or []]
    face_surfaces = [0] * count
    if additional:
        indices = _u16s(entry.get('FaceSurfaceIndices'))
        if len(indices) == count and max(indices) <= len(additional):
            face_surfaces = indices
        else:
            problems.append(f'{name}: FaceSurfaceIndices do not fit {count} faces; '
                            'every face imported on the main surface')
    strength = entry.get('SpecularStrength')
    return CustomSubmeshMesh(
        custom_submesh_id=str(entry.get('CustomSubmeshId') or ''),
        name=name,
        host_bone_id=int(entry['HostBoneId']),
        template_source=str(entry.get('TemplateSource') or ''),
        positions=positions,
        faces=faces,
        loop_uvs=loop_uvs,
        loop_colors=loop_colors,
        loop_normals=loop_normals,
        face_surfaces=face_surfaces,
        texture_assignment=dict(entry.get('TextureAssignment') or {}),
        specular_strength=None if strength is None else int(strength),
        additional_surfaces=additional,
        problems=problems,
    )


def added_bone_entries(model: dict) -> List[dict]:
    """The Add Bone bones of ``BoneHierarchyEdited`` in id order (the order
    they were created in, which the exporter assigns ids by), each one's parent
    already present (a donor bone or an earlier added bone)."""
    edited = model.get('BoneHierarchyEdited') or []
    donor_ids = {int(b['BoneId']) for b in model.get('BoneHierarchy') or []}
    known = set(donor_ids)
    added = []
    for bone in sorted((b for b in edited if b.get('UserAdded')), key=lambda b: int(b['BoneId'])):
        parent = bone.get('ParentBoneId')
        if parent is not None and int(parent) not in known:
            continue
        known.add(int(bone['BoneId']))
        added.append(bone)
    return added
