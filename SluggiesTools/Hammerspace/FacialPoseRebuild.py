"""Facial pose section (ptr7) rebuild for rebuilt submeshes: the skinned
body (PLAN_ModelReplacements.md Milestone 4.7) and rigid submeshes under a
``RigidRebuild`` (2026-10-10).

Pure Python, no slogger, so the serialization rules can be unit-tested on
their own. HammerspaceMain decodes ``FacialPoseData`` from the ``.sluggie``,
hands each rebuilt submesh's new arrays and the exporter's ``FacialPoses``
here (one rebuilder per submesh), and splices the returned section bytes
into the block's trailing data.

Section layout (``facial_pose_section.html``, verified on every exported
model with a facial section): the header (up to the object table pointer),
the object table (12 bytes per object), then per object its attribute
records, its run lists (attribute order) and its pose arrays
(attribute-major, pose order), all packed without gaps; the section is
zero-padded to a multiple of 32 bytes. Pose zero of a position attribute is
an absolute copy of the mapped GPL records; later poses are displacements.
On the skinned body every position attribute carries interleaved
``x y z nx ny nz`` int16 records (no separate normal attribute exists on a
skinned submesh in the game's data). On a rigid submesh (every vanilla
head: 46 models, 2026-10-10 survey) an object carries a kind-1 position
attribute of ``x y z`` int16 records indexing the GPL position array and,
usually, a kind-2 normal attribute of ``nx ny nz`` int16 records indexing
the GPL normal array; both pose zeros are absolute copies of the mapped
records.

Runtime facts from the game's facial code (``main.dol``, applier at
0x805325B8, type resolver at 0x80532294, read 2026-10-09):

- a run list is read pair by pair **until a pair with count 0**; the entry
  count is not used as a bound. Every vanilla run list (169/169) ends with
  that terminator, so every rebuilt one must too;
- the 16-byte type descriptors after header +0x14 are
  ``(u16 index, u8 kind, u8 comps << 4 | size, ...)``; the loader stores the
  resolved target pointer at +8. A kind-1 attribute with 6 components is
  written at ``SK1[0].srcArrPtr + first_vertex * stride`` (the SKN source
  mirror, slot 0 assumed at the first SK1 entry); kind 1 with 3 components,
  kind 2 and kind 3 go to the GPL position, normal and colour arrays of
  submesh ``index``; kinds 4-7 to UV channels;
- **kind 0 targets SKAcc entry ``index``'s source array** (a supplement for
  facial vertices with a third bone). After the SKN rebuild those entry
  indices mean nothing, so kind-0 attributes and their type descriptors
  are dropped from a rebuilt body.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

CACHE_LINE = 32
ACCUMULATION_KIND = 0
POSITION_KIND = 1
NORMAL_KIND = 2
OBJECT_TABLE_ENTRY = 12
RECORD_FIXED = 0x0C
HEADER_FIXED = 0x14
TYPE_DESCRIPTOR = 16
RUN_TERMINATOR = b'\x00\x00\x00\x00'


def _align32(value: int) -> int:
    return (value + CACHE_LINE - 1) & ~(CACHE_LINE - 1)


@dataclass
class FacialAttribute:
    """One attribute record of a facial object: ``format_data`` is the 4
    format bytes (target submesh, kind, component count, component size),
    ``run_list`` the raw ``(first, count)`` uint16 pairs, ``poses`` one array
    per pose."""
    format_data: bytes
    run_list: bytes
    poses: list

    @property
    def submesh_index(self) -> int:
        return self.format_data[0]

    @property
    def kind(self) -> int:
        return self.format_data[1]

    @property
    def component_count(self) -> int:
        return self.format_data[2]

    @property
    def component_size(self) -> int:
        return self.format_data[3]

    @property
    def entry_count(self) -> int:
        return sum(count for _first, count in struct.iter_unpack('>HH', self.run_list))

    @property
    def entry_stride(self) -> int:
        return self.component_count * self.component_size


@dataclass
class FacialObject:
    pose_count: int
    attributes: list = field(default_factory=list)


@dataclass
class BodyPoses:
    """The exporter's ``FacialPoses`` entry for one facial object: the
    exported vertex indices it maps (ascending, unique) and one delta record
    array per pose after pose zero, in that vertex order."""
    object_index: int
    vertices: list
    deltas: list


@dataclass
class RigidPoses:
    """A ``RigidRebuild.FacialPoses`` entry: the rebuilt position-array
    indices a pose moves with one ``x y z`` int16 delta array per pose after
    pose zero, and the rebuilt normal-array entries a pose tilts with their
    ``nx ny nz`` delta arrays (``normal_entries`` None when the export
    carries none)."""
    object_index: int
    vertices: list
    deltas: list
    normal_entries: list | None = None
    normal_deltas: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Decoding the .sluggie's FacialPoseData
# ---------------------------------------------------------------------------

def decode_objects(facial_data: dict, decode) -> list[FacialObject]:
    """``FacialPoseData`` -> objects with their attributes in record order
    (the exporter stores Position, Normal and AuxiliaryAttributes apart;
    ``RecordOffset`` restores the original order). *decode* turns a field
    value into bytes."""
    objects = []
    for entry in facial_data.get('Objects') or []:
        records = []
        for attribute in [entry.get('Position'), entry.get('Normal')] + list(entry.get('AuxiliaryAttributes') or []):
            if not attribute:
                continue
            records.append((int(str(attribute['RecordOffset']), 16), FacialAttribute(
                format_data=bytes(decode(attribute['FormatData'])),
                run_list=bytes(decode(attribute['RunListData'])),
                poses=[bytes(decode(pose)) for pose in attribute['PoseData']],
            )))
        records.sort(key=lambda item: item[0])
        objects.append(FacialObject(
            pose_count=int(entry['PoseCount']),
            attributes=[attribute for _offset, attribute in records],
        ))
    return objects


def facial_data_error(facial_data: dict) -> str | None:
    """Why *facial_data* cannot be re-serialized (an export from before the
    decoder stored the raw fields), or None."""
    if not facial_data.get('HeaderData'):
        return 'FacialPoseData has no HeaderData'
    for entry in facial_data.get('Objects') or []:
        if entry.get('SubmeshIndex') is None:
            return f"facial object {entry.get('ObjectIndex')} is not matched to a submesh"
        if 'PoseCount' not in entry:
            return f"facial object {entry.get('ObjectIndex')} has no PoseCount"
        attributes = [entry.get('Position'), entry.get('Normal')] + list(entry.get('AuxiliaryAttributes') or [])
        if not entry.get('Position'):
            return f"facial object {entry.get('ObjectIndex')} has no Position attribute"
        for attribute in attributes:
            if not attribute:
                continue
            for key in ('RecordOffset', 'FormatData', 'RunListData', 'PoseData'):
                if attribute.get(key) is None:
                    return f"facial object {entry.get('ObjectIndex')} attribute lacks {key}"
            if len(attribute['PoseData']) != int(entry['PoseCount']):
                return (
                    f"facial object {entry.get('ObjectIndex')} attribute holds "
                    f"{len(attribute['PoseData'])} pose arrays for PoseCount {entry['PoseCount']}"
                )
    return None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def serialize_section(header: bytes, objects: list[FacialObject]) -> bytes:
    """The section bytes for *objects* behind *header* (the donor's bytes up
    to its object table, whose table pointer at +0x8 must equal
    ``len(header)``), zero-padded to 32 bytes. Vanilla sections re-serialize
    byte for byte."""
    if len(header) < 0x0C:
        raise ValueError(f'facial header is {len(header)} bytes, expected at least 12')
    table_offset = struct.unpack_from('>I', header, 0x08)[0]
    if table_offset != len(header):
        raise ValueError(f'facial header length {len(header)} does not match its object table pointer {table_offset}')
    out = bytearray(header)
    struct.pack_into('>H', out, 0x02, len(objects))
    table = bytearray()
    body = bytearray()
    data_offset = len(header) + OBJECT_TABLE_ENTRY * len(objects)
    for obj in objects:
        if not obj.attributes:
            raise ValueError('facial object has no attributes')
        record_size = RECORD_FIXED + obj.pose_count * 4
        object_offset = data_offset + len(body)
        table += struct.pack('>HHII', obj.pose_count, len(obj.attributes), record_size, object_offset)
        cursor = object_offset + record_size * len(obj.attributes)
        run_offsets = []
        for attribute in obj.attributes:
            run_offsets.append(cursor)
            cursor += len(attribute.run_list)
        pose_offsets = []
        for attribute in obj.attributes:
            if len(attribute.poses) != obj.pose_count:
                raise ValueError(f'facial attribute holds {len(attribute.poses)} pose arrays for pose count {obj.pose_count}')
            offsets = []
            for pose in attribute.poses:
                if len(pose) != attribute.entry_count * attribute.entry_stride:
                    raise ValueError(
                        f'facial pose array is {len(pose)} bytes for {attribute.entry_count} entries '
                        f'of {attribute.entry_stride} bytes'
                    )
                offsets.append(cursor)
                cursor += len(pose)
            pose_offsets.append(offsets)
        records = bytearray()
        for attribute, run_offset, offsets in zip(obj.attributes, run_offsets, pose_offsets):
            records += struct.pack('>I', attribute.entry_count) + attribute.format_data[:4] + struct.pack('>I', run_offset)
            records += struct.pack(f'>{len(offsets)}I', *offsets)
        body += records
        for attribute in obj.attributes:
            body += attribute.run_list
        for attribute in obj.attributes:
            for pose in attribute.poses:
                body += pose
    out += table + body
    out += bytes(_align32(len(out)) - len(out))
    return bytes(out)


# ---------------------------------------------------------------------------
# Rebuilding the body's objects
# ---------------------------------------------------------------------------

def runs_from_indices(indices) -> bytes:
    """``(first, count)`` uint16 runs covering *indices* (ascending, unique),
    each capped at 65535 entries, followed by the ``(0, 0)`` terminator the
    game stops at (it reads pairs until count 0)."""
    runs = []
    previous = None
    for index in indices:
        if previous is not None and index == previous + 1 and runs[-1][1] < 0xFFFF:
            runs[-1][1] += 1
        else:
            runs.append([index, 1])
        previous = index
    return b''.join(struct.pack('>HH', first, count) for first, count in runs) + RUN_TERMINATOR


def run_list_terminated(run_list: bytes) -> bool:
    """Whether *run_list* ends with a zero-count pair."""
    return len(run_list) >= 4 and len(run_list) % 4 == 0 and struct.unpack_from('>H', run_list, len(run_list) - 2)[0] == 0


def strip_type_descriptors(header: bytes, drop_kinds) -> bytes:
    """*header* without the type descriptors of *drop_kinds*: the fixed
    0x14 bytes, the kept 16-byte descriptors, the count at +0x4 and the
    object table pointer at +0x8 updated."""
    if len(header) < HEADER_FIXED or (len(header) - HEADER_FIXED) % TYPE_DESCRIPTOR:
        raise ValueError(f'facial header length {len(header)} is not 0x14 plus 16-byte type descriptors')
    kept = [
        header[offset:offset + TYPE_DESCRIPTOR]
        for offset in range(HEADER_FIXED, len(header), TYPE_DESCRIPTOR)
        if header[offset + 2] & 0x7F not in drop_kinds
    ]
    if not kept:
        raise ValueError('every facial type descriptor would be dropped')
    out = bytearray(header[:HEADER_FIXED]) + b''.join(kept)
    struct.pack_into('>H', out, 0x04, len(kept))
    struct.pack_into('>I', out, 0x08, len(out))
    return bytes(out)


def body_object_error(obj: FacialObject, submesh_index: int) -> str | None:
    """Why *obj*, which addresses the rebuilt submesh, cannot be rebuilt."""
    positions = [a for a in obj.attributes if a.kind == POSITION_KIND and a.submesh_index == submesh_index]
    if len(positions) != 1:
        return f'expected one position attribute on submesh {submesh_index}, found {len(positions)}'
    if (positions[0].component_count, positions[0].component_size) != (6, 2):
        return (
            f'position records have {positions[0].component_count} components of '
            f'{positions[0].component_size} bytes; the skinned body stores interleaved 6 x int16'
        )
    if any(a.kind == NORMAL_KIND and a.submesh_index == submesh_index for a in obj.attributes):
        return f'a separate normal attribute on submesh {submesh_index} is not supported'
    return None


def rebuild_body_object(
    obj: FacialObject, poses: BodyPoses, slot_of_vertex, position_buffer: bytes, stride: int,
    submesh_index: int,
) -> tuple[FacialObject, list[str]]:
    """*obj* with its position attribute remapped to the rebuilt body: runs
    name the position slots of ``poses.vertices`` (sorted), pose zero copies
    those slots' records from *position_buffer*, later poses carry
    ``poses.deltas`` in slot order. Attributes of other kinds or submeshes
    are kept verbatim (a warning names any that address the rebuilt
    submesh, since their meaning is unknown)."""
    error = body_object_error(obj, submesh_index)
    if error:
        raise ValueError(error)
    if len(poses.deltas) != obj.pose_count - 1:
        raise ValueError(f'{len(poses.deltas)} delta arrays for {obj.pose_count} poses')
    if not poses.vertices:
        raise ValueError('maps no vertex')
    slots = []
    for vertex in poses.vertices:
        if not 0 <= vertex < len(slot_of_vertex):
            raise ValueError(f'vertex {vertex} is outside the {len(slot_of_vertex)} rebuilt vertices')
        slots.append(slot_of_vertex[vertex])
    if len(set(slots)) != len(slots):
        raise ValueError('maps a vertex twice')
    for pose_index, delta in enumerate(poses.deltas, start=1):
        if len(delta) != len(poses.vertices) * stride:
            raise ValueError(
                f'pose {pose_index} delta array is {len(delta)} bytes for {len(poses.vertices)} '
                f'vertices of {stride} bytes'
            )
    order = sorted(range(len(slots)), key=lambda k: slots[k])
    sorted_slots = [slots[k] for k in order]
    pose_zero = b''.join(position_buffer[slot * stride:(slot + 1) * stride] for slot in sorted_slots)
    if len(pose_zero) != len(sorted_slots) * stride:
        raise ValueError('a mapped slot lies outside the position buffer')
    rebuilt_poses = [pose_zero] + [
        b''.join(delta[k * stride:(k + 1) * stride] for k in order) for delta in poses.deltas
    ]
    warnings = []
    attributes = []
    for attribute in obj.attributes:
        if attribute.kind == POSITION_KIND and attribute.submesh_index == submesh_index:
            attributes.append(FacialAttribute(
                format_data=attribute.format_data,
                run_list=runs_from_indices(sorted_slots),
                poses=rebuilt_poses,
            ))
            continue
        if attribute.kind == ACCUMULATION_KIND:
            warnings.append(
                f'a kind-0 attribute ({attribute.entry_count} entries) wrote the poses into the '
                f"donor's SKAcc entry {attribute.submesh_index}; the rebuilt skin has other entries, "
                'so it is dropped (facial vertices with a third bone lose that supplement)'
            )
            continue
        attributes.append(attribute)
    return FacialObject(pose_count=obj.pose_count, attributes=attributes), warnings


def rigid_object_error(obj: FacialObject, submesh_index: int) -> str | None:
    """Why *obj*, which addresses the rebuilt rigid submesh, cannot be
    rebuilt: it needs exactly one 3 x int16 position attribute there, at
    most one 3 x int16 normal attribute, and nothing else on that submesh."""
    on_submesh = [a for a in obj.attributes if a.submesh_index == submesh_index and a.kind != ACCUMULATION_KIND]
    positions = [a for a in on_submesh if a.kind == POSITION_KIND]
    if len(positions) != 1:
        return f'expected one position attribute on submesh {submesh_index}, found {len(positions)}'
    if (positions[0].component_count, positions[0].component_size) != (3, 2):
        return (
            f'position records have {positions[0].component_count} components of '
            f'{positions[0].component_size} bytes; a rigid submesh stores 3 x int16'
        )
    normals = [a for a in on_submesh if a.kind == NORMAL_KIND]
    if len(normals) > 1:
        return f'{len(normals)} normal attributes on submesh {submesh_index}'
    if normals and (normals[0].component_count, normals[0].component_size) != (3, 2):
        return (
            f'normal records have {normals[0].component_count} components of '
            f'{normals[0].component_size} bytes; a rigid submesh stores 3 x int16'
        )
    others = sorted({a.kind for a in on_submesh if a.kind not in (POSITION_KIND, NORMAL_KIND)})
    if others:
        return f'attribute kind(s) {others} on submesh {submesh_index} are not supported'
    return None


def _remapped_attribute(
    attribute: FacialAttribute, entries: list, deltas: list, buffer: bytes, stride: int, pose_count: int, label: str,
) -> FacialAttribute:
    """*attribute* mapped onto *entries* of the rebuilt array *buffer*
    (``stride`` bytes per record): runs from the sorted entries, pose zero a
    copy of their records, *deltas* (one array per later pose, in the given
    entry order) re-ordered to match."""
    if len(deltas) != pose_count - 1:
        raise ValueError(f'{label}: {len(deltas)} delta arrays for {pose_count} poses')
    if not entries:
        raise ValueError(f'{label}: maps no entry')
    if len(set(entries)) != len(entries):
        raise ValueError(f'{label}: maps an entry twice')
    if attribute.entry_stride != stride:
        raise ValueError(
            f'{label}: records are {attribute.entry_stride} bytes but the rebuilt array holds {stride}-byte entries'
        )
    for pose_index, delta in enumerate(deltas, start=1):
        if len(delta) != len(entries) * stride:
            raise ValueError(
                f'{label}: pose {pose_index} delta array is {len(delta)} bytes for {len(entries)} '
                f'entries of {stride} bytes'
            )
    order = sorted(range(len(entries)), key=lambda k: entries[k])
    sorted_entries = [entries[k] for k in order]
    if sorted_entries[0] < 0 or sorted_entries[-1] * stride + stride > len(buffer):
        raise ValueError(f'{label}: entry {sorted_entries[-1]} lies outside the {len(buffer) // stride} rebuilt entries')
    pose_zero = b''.join(buffer[entry * stride:(entry + 1) * stride] for entry in sorted_entries)
    return FacialAttribute(
        format_data=attribute.format_data,
        run_list=runs_from_indices(sorted_entries),
        poses=[pose_zero] + [b''.join(delta[k * stride:(k + 1) * stride] for k in order) for delta in deltas],
    )


def rebuild_rigid_object(
    obj: FacialObject, poses: RigidPoses | None, position_buffer: bytes, normal_buffer: bytes | None,
    normal_stride: int | None, submesh_index: int,
) -> tuple[FacialObject, list[str]]:
    """*obj* with its attributes on the rebuilt rigid submesh remapped: the
    position attribute onto ``poses.vertices`` of *position_buffer* (3 x
    int16 records), the normal attribute (when the object has one) onto
    ``poses.normal_entries`` of *normal_buffer*. *poses* None (an export
    without ``FacialPoses``) or a missing normal list leaves the attribute
    pointing at entry 0 with zero deltas, so the object stays well-formed
    but animates nothing (warning). Attributes of other submeshes are kept
    verbatim."""
    error = rigid_object_error(obj, submesh_index)
    if error:
        raise ValueError(error)
    warnings = []
    attributes = []
    later = obj.pose_count - 1
    for attribute in obj.attributes:
        if attribute.submesh_index != submesh_index or attribute.kind == ACCUMULATION_KIND:
            attributes.append(attribute)
            continue
        if attribute.kind == POSITION_KIND:
            if poses is None:
                warnings.append('no FacialPoses for it; its position poses are neutralized (entry 0, no movement)')
                entries, deltas = [0], [bytes(6)] * later
            else:
                entries, deltas = list(poses.vertices), list(poses.deltas)
            attributes.append(_remapped_attribute(attribute, entries, deltas, position_buffer, 6, obj.pose_count, 'positions'))
            continue
        # kind 2: the normal attribute
        if normal_buffer is None or not normal_stride:
            raise ValueError('has a normal attribute but the rebuilt submesh has no normal array')
        if poses is None or poses.normal_entries is None:
            warnings.append('no normal entries in FacialPoses; its normal poses are neutralized (entry 0, no tilt)')
            entries, deltas = [0], [bytes(normal_stride)] * later
        else:
            entries, deltas = list(poses.normal_entries), list(poses.normal_deltas)
        attributes.append(_remapped_attribute(attribute, entries, deltas, normal_buffer, normal_stride, obj.pose_count, 'normals'))
    return FacialObject(pose_count=obj.pose_count, attributes=attributes), warnings


def body_rebuilder(body_poses: list[BodyPoses], slot_of_vertex, position_buffer: bytes, stride: int, submesh_index: int):
    """The ``rebuild_section`` rebuilder for the skinned body: every object
    addressing *submesh_index* needs an entry in *body_poses*."""
    by_object = {poses.object_index: poses for poses in body_poses}

    def rebuild(obj: FacialObject, object_index: int) -> tuple[FacialObject, list[str]]:
        poses = by_object.get(object_index)
        if poses is None:
            raise ValueError(f'addresses submesh {submesh_index} but FacialPoses has no entry for it')
        return rebuild_body_object(obj, poses, slot_of_vertex, position_buffer, stride, submesh_index)
    return rebuild


def rigid_rebuilder(
    rigid_poses: list[RigidPoses] | None, position_buffer: bytes, normal_buffer: bytes | None,
    normal_stride: int | None, submesh_index: int,
):
    """The ``rebuild_section`` rebuilder for a rebuilt rigid submesh.
    *rigid_poses* None (no ``FacialPoses`` in the rebuild) neutralizes every
    object on it; a list must cover every such object."""
    by_object = None if rigid_poses is None else {poses.object_index: poses for poses in rigid_poses}

    def rebuild(obj: FacialObject, object_index: int) -> tuple[FacialObject, list[str]]:
        poses = None
        if by_object is not None:
            poses = by_object.get(object_index)
            if poses is None:
                raise ValueError(f'addresses submesh {submesh_index} but FacialPoses has no entry for it')
        return rebuild_rigid_object(obj, poses, position_buffer, normal_buffer, normal_stride, submesh_index)
    return rebuild


def rebuild_section(
    facial_data: dict, decode, rebuilders: dict, drop_kinds=frozenset(),
) -> tuple[bytes, list[str]]:
    """The whole section with every object that addresses a submesh in
    *rebuilders* (``{submesh index: rebuild(obj, object_index) -> (obj,
    warnings)}``, see ``body_rebuilder`` / ``rigid_rebuilder``) rebuilt and
    every other object re-serialized unchanged. *drop_kinds* names attribute
    kinds whose type descriptors and attributes are dropped: the kind-0
    (SKAcc supplement) ones when the SKN is rebuilt, since its entries are
    renumbered. Returns ``(bytes, warnings)``; warnings are prefixed with
    the object index."""
    error = facial_data_error(facial_data)
    if error:
        raise ValueError(error)
    objects = decode_objects(facial_data, decode)
    entries = (facial_data.get('Objects') or [])
    header = bytes(decode(facial_data['HeaderData']))
    if drop_kinds:
        header = strip_type_descriptors(header, set(drop_kinds))
    rebuilt = []
    warnings = []
    for entry, obj in zip(entries, objects):
        object_index = int(entry['ObjectIndex'])
        rebuilder = rebuilders.get(int(entry['SubmeshIndex']))
        if rebuilder is not None:
            try:
                obj, object_warnings = rebuilder(obj, object_index)
            except ValueError as exc:
                raise ValueError(f'facial object {object_index}: {exc}') from exc
            warnings.extend(f'facial object {object_index}: {message}' for message in object_warnings)
        if drop_kinds:
            obj = FacialObject(obj.pose_count, [a for a in obj.attributes if a.kind not in drop_kinds])
            if not obj.attributes:
                raise ValueError(f'facial object {object_index}: every attribute would be dropped')
        rebuilt.append(obj)
    return serialize_section(header, rebuilt), warnings


def rigid_summary(rigid_poses: list[RigidPoses] | None) -> str:
    if rigid_poses is None:
        return 'no FacialPoses (objects neutralized)'
    parts = []
    for poses in rigid_poses:
        normals = f', {len(poses.normal_entries)} normal entries' if poses.normal_entries is not None else ''
        parts.append(f'object {poses.object_index}: {len(poses.vertices)} vertices{normals}, {len(poses.deltas) + 1} poses')
    return '; '.join(parts)


def summary(body_poses: list[BodyPoses]) -> str:
    counts = []
    for poses in body_poses:
        counts.append(f'object {poses.object_index}: {len(poses.vertices)} vertices, {len(poses.deltas) + 1} poses')
    return '; '.join(counts)
