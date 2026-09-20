"""Standalone ACT section parser/rebuilder for PLAN_AddBones.md Phase 0.

This is the "throwaway ACT writer" called for in Phase 0: parse an ACT
section into a fully-decoded representation and re-emit it byte-for-byte
(P1 - Rebuild identity), or re-emit it with one new leaf bone appended
(P2-P4, via ``append_leaf_bone``). Phase 2 promotes this into the real
``BuildACTBoneHierarchy`` rebuilder once these probes pass.

Layout (see PLAN_AddBones.md F1, plus the 2026-09-18 empirical survey that
pinned down the pieces F1 left implicit):

    0x00                       header (0x20 bytes)
    0x20                       bone table: 0x1C x boneCount, bone-id order
    0x20 + 0x1C*N              SRT array: 0x34 per non-null-orientation bone,
                                bone-table order, tightly packed (no gaps)
    srt_end (== geoNamePtr)    name gap: geo name string + whatever filler
                                precedes user data (or the section end, if
                                there is none) -- see below
    userDataPtr                user data: chained 0x0C-byte descriptors
                                (present only when userDataSize != 0)
    userDataPtr + userDataSize tail gap: filler to the section end

``srt_end`` (always observed equal to the header's ``geoNamePtr``) is
solid and load-bearing: it is what shifts when bone count changes, so it is
fully recomputed here. The two "gap" regions are not: their exact byte
count varies per donor in ways that don't reduce to one alignment formula
(observed: align32 after a null-terminated name, no gap at all when the
next-section pointer already lands on a 32 boundary, align4 after a name on
a padding-free single-bone stub, and others). That padding is decided by
whatever sits *after* the ACT section in the whole model block, not by
anything internal to ACT -- so rather than guess a formula, this module
parses each gap as an opaque byte blob (bounded by the header's own
``geoNamePtr``/``userDataPtr``/section-length values) and reproduces it
verbatim. Phase 2 does not need to invent a general formula either: bone
count is append-only, so ``userDataPtr`` only moves by exactly the
descriptor-payload growth from the mirror/track arrays, and the model
assembler (F9) already owns inter-section alignment.

Each user-data descriptor is ``u32 size, u16 kind, u16 count, u32 dataPtr``
(0x0C bytes) immediately followed by its own payload; ``size`` includes the
0x0C header. ``dataPtr`` is always observed as 0x0C (payload starts right
after the header) and ``count`` is always observed as 0 for kind 2/3 -- ACT
computes their array lengths from ``boneCount``, not from this field. The
chain is walked by adding ``size`` until the running total equals
``userDataSize`` (PLAN_AddBones.md F3).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Sequence

HEADER_SIZE = 0x20
BONE_RECORD_SIZE = 0x1C
SRT_RECORD_SIZE = 0x34
DESCRIPTOR_HEADER_SIZE = 0x0C
SECTION_ALIGN = 32

KIND_TRACK = 3
KIND_MIRROR = 2


class ACTParseError(ValueError):
    """Raised when an ACT section does not fit this module's layout model."""


class ACTMirrorTableError(ValueError):
    """Raised when a model's kind-2 mirror table is not a clean involution.

    PLAN_AddBones.md F6: two donors (37/1, 51/1) have oversized, non-involution
    mirror tables and are out of scope for the rebuilder until understood.
    """


def _align_up(value: int, align: int) -> int:
    return (value + align - 1) & ~(align - 1)


@dataclass
class BoneRecord:
    orientation_ptr: int
    prev: int
    next: int
    parent: int
    first_child: int
    geo_file_id_raw: int
    id: int
    inheritance: int
    priority: int
    pad_half: int

    def pack(self) -> bytes:
        return struct.pack(
            '>IIIIIHHBBH',
            self.orientation_ptr, self.prev, self.next, self.parent, self.first_child,
            self.geo_file_id_raw, self.id, self.inheritance, self.priority, self.pad_half,
        )


@dataclass
class UserDataDescriptor:
    kind: int
    count: int
    data_ptr: int
    payload: bytes

    def pack(self) -> bytes:
        size = DESCRIPTOR_HEADER_SIZE + len(self.payload)
        return struct.pack('>IHHI', size, self.kind, self.count, self.data_ptr) + self.payload


@dataclass
class ACTParsed:
    version_num: int
    actor_id: int
    bone_count: int
    tree_unknown: int
    root_ptr: int
    skin_file_id: int
    pad16: int
    bones: list[BoneRecord]
    srt_blobs: list[bytes]          # one per bone with orientation_ptr != 0, bone-table order
    name_gap: bytes                 # geo name string + filler, verbatim (see module docstring)
    tail_gap: bytes                 # filler after user data (or after name_gap) to the section end
    user_data: list[UserDataDescriptor] = field(default_factory=list)
    total_length: int = 0


def parse_act(act_bytes: bytes) -> ACTParsed:
    if len(act_bytes) < HEADER_SIZE:
        raise ACTParseError(f"ACT section too short for header: {len(act_bytes)} bytes")

    version_num, actor_id, bone_count = struct.unpack_from('>IHH', act_bytes, 0x00)
    tree_unknown, root_ptr = struct.unpack_from('>II', act_bytes, 0x08)
    geo_name_ptr, = struct.unpack_from('>I', act_bytes, 0x10)
    skin_file_id, pad16 = struct.unpack_from('>HH', act_bytes, 0x14)
    user_data_size, user_data_ptr = struct.unpack_from('>II', act_bytes, 0x18)

    bone_table_start = HEADER_SIZE
    bone_table_end = bone_table_start + bone_count * BONE_RECORD_SIZE
    if bone_table_end > len(act_bytes):
        raise ACTParseError(f"bone table (boneCount={bone_count}) runs past the section end")

    bones: list[BoneRecord] = []
    for i in range(bone_count):
        off = bone_table_start + i * BONE_RECORD_SIZE
        rec = struct.unpack_from('>IIIIIHHBBH', act_bytes, off)
        bones.append(BoneRecord(*rec))

    srt_blobs: list[bytes] = []
    offset = bone_table_end
    for bone in bones:
        if bone.orientation_ptr == 0:
            continue
        if bone.orientation_ptr != offset:
            raise ACTParseError(
                f"bone {bone.id}: orientationPTR 0x{bone.orientation_ptr:X} != expected "
                f"0x{offset:X} (SRT records are not tightly packed in bone-table order)"
            )
        blob = act_bytes[offset:offset + SRT_RECORD_SIZE]
        if len(blob) != SRT_RECORD_SIZE:
            raise ACTParseError(f"bone {bone.id}: SRT record truncated at 0x{offset:X}")
        srt_blobs.append(blob)
        offset += SRT_RECORD_SIZE
    srt_end = offset

    if geo_name_ptr != srt_end:
        raise ACTParseError(
            f"geoNamePtr 0x{geo_name_ptr:X} != computed SRT-blob end 0x{srt_end:X}; "
            "the geo name string is not where this module expects it"
        )

    descriptors: list[UserDataDescriptor] = []
    if user_data_size:
        if user_data_ptr < srt_end:
            raise ACTParseError(
                f"userDataPtr 0x{user_data_ptr:X} is before the SRT blob end 0x{srt_end:X}"
            )
        name_gap = act_bytes[srt_end:user_data_ptr]

        pos = user_data_ptr
        end_ud = user_data_ptr + user_data_size
        while pos < end_ud:
            if pos + DESCRIPTOR_HEADER_SIZE > len(act_bytes):
                raise ACTParseError(f"user data descriptor at 0x{pos:X} runs past the section end")
            size, kind, count, data_ptr = struct.unpack_from('>IHHI', act_bytes, pos)
            if size < DESCRIPTOR_HEADER_SIZE:
                raise ACTParseError(f"user data descriptor at 0x{pos:X} has size {size} < header size")
            if data_ptr != DESCRIPTOR_HEADER_SIZE:
                raise ACTParseError(
                    f"user data descriptor at 0x{pos:X}: dataPtr 0x{data_ptr:X} != 0x{DESCRIPTOR_HEADER_SIZE:X} "
                    "(payload is not immediately after the descriptor header)"
                )
            payload = act_bytes[pos + DESCRIPTOR_HEADER_SIZE:pos + size]
            if len(payload) != size - DESCRIPTOR_HEADER_SIZE:
                raise ACTParseError(f"user data descriptor at 0x{pos:X}: payload truncated")
            descriptors.append(UserDataDescriptor(kind, count, data_ptr, payload))
            pos += size
        if pos != end_ud:
            raise ACTParseError(
                f"user data descriptor chain ended at 0x{pos:X}, expected 0x{end_ud:X} "
                "(userDataPtr + userDataSize)"
            )
        tail_gap = act_bytes[end_ud:]
    else:
        if user_data_ptr != 0:
            raise ACTParseError(f"userDataSize is 0 but userDataPtr is 0x{user_data_ptr:X}, expected 0")
        name_gap = act_bytes[srt_end:]
        tail_gap = b''

    if HEADER_SIZE + bone_count * BONE_RECORD_SIZE + sum(len(b) for b in srt_blobs) + len(name_gap) \
            + sum(DESCRIPTOR_HEADER_SIZE + len(d.payload) for d in descriptors) + len(tail_gap) \
            != len(act_bytes):
        raise ACTParseError("parsed region lengths do not sum to the ACT section length")

    return ACTParsed(
        version_num=version_num, actor_id=actor_id, bone_count=bone_count,
        tree_unknown=tree_unknown, root_ptr=root_ptr,
        skin_file_id=skin_file_id, pad16=pad16,
        bones=bones, srt_blobs=srt_blobs, name_gap=name_gap, tail_gap=tail_gap,
        user_data=descriptors, total_length=len(act_bytes),
    )


def validate_mirror_table(parsed: ACTParsed) -> None:
    """Raise ACTMirrorTableError unless the kind-2 mirror table (if present)
    is a clean, boneCount-long involution (PLAN_AddBones.md F3, F6)."""
    mirror_descriptors = [d for d in parsed.user_data if d.kind == KIND_MIRROR]
    if not mirror_descriptors:
        return
    if len(mirror_descriptors) > 1:
        raise ACTMirrorTableError("more than one kind-2 (mirror table) descriptor")
    payload = mirror_descriptors[0].payload
    if len(payload) < parsed.bone_count * 2:
        raise ACTMirrorTableError(
            f"mirror table payload ({len(payload)} bytes) is shorter than boneCount*2 "
            f"({parsed.bone_count * 2})"
        )
    pairs = [
        (payload[2 * i], payload[2 * i + 1]) for i in range(parsed.bone_count)
    ]
    for bone_id, (mirror_id, _role) in enumerate(pairs):
        if mirror_id >= parsed.bone_count:
            raise ACTMirrorTableError(
                f"bone {bone_id}: mirror id {mirror_id} is outside boneCount {parsed.bone_count}"
            )
        target = pairs[mirror_id][0]
        if target != bone_id:
            raise ACTMirrorTableError(
                f"mirror table is not an involution: bone {bone_id} -> {mirror_id} -> {target}"
            )
    # A clean involution must consume exactly boneCount pairs; extra trailing
    # bytes beyond that (F6's oversized tables) are the other symptom of the
    # same malformation.
    if len(payload) != _align_up(parsed.bone_count * 2, 4):
        raise ACTMirrorTableError(
            f"mirror table payload is {len(payload)} bytes, expected "
            f"{_align_up(parsed.bone_count * 2, 4)} (align4 of boneCount*2 pairs)"
        )


def rebuild_act_bytes(parsed: ACTParsed) -> bytes:
    """Re-emit an ACT section from its parsed representation.

    Recomputes the bone table and SRT blob offsets from scratch -- this is
    the layout math that shifts once Phase 2 lets bone count change, so it
    is what this probe exists to validate. The name/tail gaps are not
    recomputed (see the module docstring on why there is no one alignment
    formula for them); they are reproduced verbatim from the parse. Bone
    table tree pointers (prev/next/parent/firstChild) are likewise carried
    through unchanged, since Phase 0 does not alter topology.
    """
    if len(parsed.bones) != parsed.bone_count:
        raise ACTParseError("bone list length does not match bone_count")

    bone_table_end = HEADER_SIZE + parsed.bone_count * BONE_RECORD_SIZE

    srt_iter = iter(parsed.srt_blobs)
    srt_chunks: list[bytes] = []
    bone_chunks: list[bytes] = []
    offset = bone_table_end
    for bone in parsed.bones:
        if bone.orientation_ptr == 0:
            bone_chunks.append(bone.pack())
            continue
        blob = next(srt_iter, None)
        if blob is None or len(blob) != SRT_RECORD_SIZE:
            raise ACTParseError(f"bone {bone.id}: missing/short SRT blob during rebuild")
        rebuilt_bone = BoneRecord(
            orientation_ptr=offset, prev=bone.prev, next=bone.next, parent=bone.parent,
            first_child=bone.first_child, geo_file_id_raw=bone.geo_file_id_raw, id=bone.id,
            inheritance=bone.inheritance, priority=bone.priority, pad_half=bone.pad_half,
        )
        bone_chunks.append(rebuilt_bone.pack())
        srt_chunks.append(blob)
        offset += SRT_RECORD_SIZE
    if next(srt_iter, None) is not None:
        raise ACTParseError("more SRT blobs than non-null-orientation bones")
    srt_end = offset

    header = bytearray(HEADER_SIZE)
    struct.pack_into('>I', header, 0x00, parsed.version_num)
    struct.pack_into('>H', header, 0x04, parsed.actor_id)
    struct.pack_into('>H', header, 0x06, parsed.bone_count)
    struct.pack_into('>I', header, 0x08, parsed.tree_unknown)
    struct.pack_into('>I', header, 0x0C, parsed.root_ptr)
    struct.pack_into('>I', header, 0x10, srt_end)  # geoNamePtr
    struct.pack_into('>H', header, 0x14, parsed.skin_file_id)
    struct.pack_into('>H', header, 0x16, parsed.pad16)

    body = bytearray()
    body += b''.join(bone_chunks)
    body += b''.join(srt_chunks)
    body += parsed.name_gap

    if parsed.user_data:
        user_data_ptr = srt_end + len(parsed.name_gap)
        user_data_bytes = b''.join(d.pack() for d in parsed.user_data)
        body += user_data_bytes
        user_data_size = len(user_data_bytes)
    else:
        user_data_ptr = 0
        user_data_size = 0
    body += parsed.tail_gap

    struct.pack_into('>I', header, 0x18, user_data_size)
    struct.pack_into('>I', header, 0x1C, user_data_ptr)

    out = bytes(header) + bytes(body)
    if len(out) != parsed.total_length:
        raise ACTParseError(
            f"rebuilt ACT length 0x{len(out):X} != parsed total length 0x{parsed.total_length:X}"
        )
    return out


MAX_BONE_ID = 0xFF  # F3: mirror table ids are u8


def pack_srt_blob(
    srt_type: int,
    scale: Sequence[float],
    quaternion: Sequence[float],
    translation: Sequence[float],
) -> bytes:
    """Pack a full 0x34-byte SRT block from decoded scale/quaternion/translation.

    *quaternion* is ``[W, X, Y, Z]`` -- the decoded, sign-corrected convention
    used everywhere else in this codebase (``.sluggie``'s ``Quaternion``
    field, ``helper.SRT.analyze``). The raw ACT block stores it as
    ``[X, Y, Z, -W]`` (``helper.SRT.analyze``), so this re-packs the same
    rearrangement ``SRT.analyze`` undoes on read. Layout: type byte + 3 pad,
    scale (3f), quaternion (4f raw), translation (3f), 8 reserved zero bytes
    (PLAN_AddBones.md module docstring).
    """
    sx, sy, sz = scale
    w, x, y, z = quaternion
    tx, ty, tz = translation
    return struct.pack(
        '>B3x3f4f3f8x',
        srt_type, sx, sy, sz, x, y, z, -w, tx, ty, tz,
    )


def append_leaf_bone(
    parsed: ACTParsed,
    parent_id: int,
    srt_blob: bytes | None,
    *,
    geo_file_id_raw: int = 0xFFFF,
    inheritance: int = 1,
    priority: int = 0,
    track_id: int = 0xFFFF,
    mirror_bone_id: int | None = None,
    mirror_role: int = 3,
) -> ACTParsed:
    """Return a copy of *parsed* with one new leaf bone appended.

    Originally the Phase 0 P2 probe helper; promoted by Phase 2 into the
    general single-bone append step ``BuildACTBoneHierarchy``'s rebuild route
    calls once per ``BoneHierarchyEdited`` entry with ``UserAdded`` set. The
    new bone takes the next bone id (``parsed.bone_count``), is appended to
    the *end* of ``parent_id``'s child chain (F10 -- the only topology change
    this plan permits), gets ``srt_blob`` (exactly 0x34 bytes, e.g. from
    ``pack_srt_blob``) or no SRT at all when *srt_blob* is ``None`` (F8: a
    null-orientation bone is legal), ``track_id`` in the kind-3 array
    (default ``0xFFFF`` -- no track, per the user contract), and by default
    is its own mirror with role 3 (F3's "own_id, 3" convention for a plain
    bone).

    ``mirror_bone_id``/``mirror_role`` override the mirror entry. Overriding
    ``mirror_bone_id`` away from the new bone's own id exists only for the
    Phase 0 P5 probe, which deliberately breaks the involution to observe how
    the engine reacts to a corrupt mirror table -- Phase 3 rule 8 rejects
    this for any real build.

    Handles two donor shapes for the per-bone user-data arrays: exactly one
    kind-3 (track) plus one kind-2 (mirror) descriptor, which are both
    extended by one entry; or **no user data at all**, which F4 documents as
    a normal shipped state (dozens of models carry ``userDataSize = 0``) and
    where there is simply nothing to extend -- the new bone is then trackless
    and has no mirror entry, which F4 also shows is routine, and
    ``track_id``/``mirror_bone_id``/``mirror_role`` are ignored. Any other
    combination (one of the two without the other) is ambiguous and is
    refused rather than guessed at. Any other descriptor kind (F5's kind-4
    blob) is bone-count-independent and is passed through unchanged.
    """
    if srt_blob is not None and len(srt_blob) != SRT_RECORD_SIZE:
        raise ValueError(f"srt_blob must be {SRT_RECORD_SIZE} bytes, got {len(srt_blob)}")

    by_id = {b.id: b for b in parsed.bones}
    if parent_id not in by_id:
        raise ValueError(f"parent bone {parent_id} does not exist")

    new_id = parsed.bone_count
    if new_id > MAX_BONE_ID:
        raise ValueError(f"new bone id {new_id} exceeds the mirror table's u8 cap (F3)")

    def table_off(bone_id: int) -> int:
        return HEADER_SIZE + bone_id * BONE_RECORD_SIZE

    bones = [BoneRecord(**vars(b)) for b in parsed.bones]
    by_id = {b.id: b for b in bones}

    parent = by_id[parent_id]
    if parent.first_child == 0:
        parent.first_child = table_off(new_id)
        new_prev = 0
    else:
        cur_off = parent.first_child
        cur_id = (cur_off - HEADER_SIZE) // BONE_RECORD_SIZE
        while by_id[cur_id].next != 0:
            cur_off = by_id[cur_id].next
            cur_id = (cur_off - HEADER_SIZE) // BONE_RECORD_SIZE
        by_id[cur_id].next = table_off(new_id)
        new_prev = cur_off

    bones.append(BoneRecord(
        orientation_ptr=0 if srt_blob is None else 1,  # placeholder; rebuild_act_bytes recomputes it
        prev=new_prev, next=0, parent=table_off(parent_id), first_child=0,
        geo_file_id_raw=geo_file_id_raw, id=new_id,
        inheritance=inheritance, priority=priority, pad_half=0,
    ))
    srt_blobs = list(parsed.srt_blobs) + ([srt_blob] if srt_blob is not None else [])

    track_descs = [d for d in parsed.user_data if d.kind == KIND_TRACK]
    mirror_descs = [d for d in parsed.user_data if d.kind == KIND_MIRROR]
    if (len(track_descs), len(mirror_descs)) not in ((1, 1), (0, 0)):
        raise ValueError(
            "append_leaf_bone only supports donors with exactly one kind-3 (track) "
            "and one kind-2 (mirror) user-data descriptor, or with neither (F4)"
        )
    has_per_bone_tables = bool(track_descs)

    def extend_pairs(payload: bytes, extra: bytes) -> bytes:
        expected = _align_up(parsed.bone_count * 2, 4)
        if len(payload) != expected:
            raise ValueError(
                f"user data payload is {len(payload)} bytes, expected {expected} "
                f"(align4 of boneCount*2); not the plain shape this helper knows how to extend"
            )
        extended = payload[:parsed.bone_count * 2] + extra
        if len(extended) % 4:
            extended += b'\x00' * (4 - len(extended) % 4)
        return extended

    if not has_per_bone_tables:
        # F4: no user data at all -- no per-bone array to extend. Any other
        # descriptor kind (F5's kind-4 blob) still passes through verbatim.
        new_user_data = list(parsed.user_data)
    else:
        track = track_descs[0]
        new_track = UserDataDescriptor(
            kind=track.kind, count=track.count, data_ptr=track.data_ptr,
            payload=extend_pairs(track.payload, struct.pack('>H', track_id)),
        )
        mirror = mirror_descs[0]
        mirror_target = new_id if mirror_bone_id is None else mirror_bone_id
        new_mirror = UserDataDescriptor(
            kind=mirror.kind, count=mirror.count, data_ptr=mirror.data_ptr,
            payload=extend_pairs(mirror.payload, struct.pack('BB', mirror_target, mirror_role)),
        )
        new_user_data = [
            new_track if d.kind == KIND_TRACK else new_mirror if d.kind == KIND_MIRROR else d
            for d in parsed.user_data
        ]

    total_length = (
        HEADER_SIZE + len(bones) * BONE_RECORD_SIZE + len(srt_blobs) * SRT_RECORD_SIZE
        + len(parsed.name_gap)
        + sum(DESCRIPTOR_HEADER_SIZE + len(d.payload) for d in new_user_data)
        + len(parsed.tail_gap)
    )

    return ACTParsed(
        version_num=parsed.version_num, actor_id=parsed.actor_id, bone_count=new_id + 1,
        tree_unknown=parsed.tree_unknown, root_ptr=parsed.root_ptr,
        skin_file_id=parsed.skin_file_id, pad16=parsed.pad16,
        bones=bones, srt_blobs=srt_blobs, name_gap=parsed.name_gap, tail_gap=parsed.tail_gap,
        user_data=new_user_data, total_length=total_length,
    )
