"""Archive-container handling for hammerspace model replacement.

Many DOL directory entries do not point at a single model but at an
**archive**: a container holding several models (and sometimes ANM blocks)
back to back.  Its layout is

::

    +0x00  fileCount : u32
    +0x04  offsets   : u32 * fileCount   (relative to the archive start,
                                          0 for an empty slot)
    ...    members, in ascending offset order

A member's length is the distance to the next populated offset, or the
distance to the end of the archive for the last one -- the same rule
``model0.Archive.analyze`` uses when exporting.

Replacing one member therefore moves every member after it.  This module
parses the table, rebuilds the container around one rewritten member and
shifts the trailing offsets by the size delta.  Members other than the
rewritten one are copied byte for byte, so anything this code does not
understand (ANM blocks, padding, unreferenced slack) survives untouched.

Only one member can be rewritten per call.  Editing several parts of the same
character needs a hammerspace read-back path; see ``PLAN_EditRigidMeshes.md``
Phase 12.
"""

import struct
from dataclasses import dataclass

# Model blocks want their hot data on 32-byte cache lines, and every vanilla
# archive places its members on 32-byte boundaries.  Keeping the size delta a
# multiple of this preserves the alignment of every member after the edit.
ARCHIVE_ALIGN = 32

# ``model0.MaybeArchive`` treats a first word above 1000 as "not a file count",
# so a plausible archive has at most this many slots.
ARCHIVE_MAX_SLOTS = 1000

__all__ = [
    'ARCHIVE_ALIGN',
    'ARCHIVE_MAX_SLOTS',
    'ArchiveLayout',
    'ArchiveMember',
    'parse_archive_container',
    'rebuild_archive_container',
]


@dataclass(frozen=True)
class ArchiveMember:
    """One populated slot: its index, byte offset into the archive, and size."""

    slot:   int
    offset: int
    length: int

    @property
    def end(self) -> int:
        return self.offset + self.length


@dataclass(frozen=True)
class ArchiveLayout:
    """A parsed archive header plus the extent of every populated slot."""

    file_count: int
    offsets:    tuple              # u32 per slot, 0 for an empty slot
    members:    tuple              # ArchiveMember, ascending by offset
    length:     int                # size of the container these came from

    @property
    def header_size(self) -> int:
        """Bytes occupied by the count and the offset table."""
        return 4 + 4 * self.file_count

    def member_at(self, offset: int):
        """Return the member starting at ``offset``, or None."""
        for member in self.members:
            if member.offset == offset:
                return member
        return None


def parse_archive_container(container: bytes):
    """Return an :class:`ArchiveLayout` for ``container``, or None.

    None means "these bytes are not an archive" -- a bare model block, an ANM
    block, or anything else whose first words do not form a usable offset
    table.  Callers treat that as the ordinary single-model case rather than
    an error, because most DOL entries really do point straight at a model.
    """

    length = len(container)
    if length < 8:
        return None

    file_count = struct.unpack_from('>I', container, 0)[0]
    if not 0 < file_count <= ARCHIVE_MAX_SLOTS:
        return None

    header_size = 4 + 4 * file_count
    if header_size > length:
        return None

    offsets = struct.unpack_from(f'>{file_count}I', container, 4)

    members = []
    populated = [
        (slot, offset) for slot, offset in enumerate(offsets) if offset != 0
    ]
    if not populated:
        return None

    previous = header_size - 1
    for slot, offset in populated:
        # Members sit after the table, inside the container, and in ascending
        # order -- the layout model0 requires to export the archive at all.
        if offset < header_size or offset >= length or offset <= previous:
            return None
        previous = offset

    ends = [offset for _, offset in populated[1:]] + [length]
    for (slot, offset), end in zip(populated, ends):
        members.append(ArchiveMember(slot=slot, offset=offset, length=end - offset))

    return ArchiveLayout(
        file_count=file_count,
        offsets=tuple(offsets),
        members=tuple(members),
        length=length,
    )


def rebuild_archive_container(
    container: bytes,
    member_offset: int,
    new_member: bytes,
    layout: ArchiveLayout = None,
    align: int = ARCHIVE_ALIGN,
) -> tuple:
    """Rebuild ``container`` with the member at ``member_offset`` replaced.

    Returns ``(block, report)``.  ``block`` is the whole container: the
    original header with the trailing offsets shifted, the members before the
    replaced one verbatim, ``new_member`` padded up to a multiple of ``align``,
    then the members after it verbatim.

    The replaced member keeps its own offset, so slots before it never move.
    The size delta is rounded up to ``align`` so every later member stays on
    the boundary it was written for.

    Raises ValueError when ``container`` is not an archive or when
    ``member_offset`` is not the start of a populated slot.
    """

    if layout is None:
        layout = parse_archive_container(container)
    if layout is None:
        raise ValueError('DOL entry is not an archive container')
    if layout.length != len(container):
        raise ValueError(
            f'layout describes {layout.length} bytes but container is {len(container)}'
        )

    member = layout.member_at(member_offset)
    if member is None:
        starts = ', '.join(f'0x{m.offset:X}' for m in layout.members)
        raise ValueError(
            f'0x{member_offset:X} is not the start of a populated archive slot '
            f'(slots start at {starts})'
        )

    # Round the delta up to the alignment so the members after the edit keep
    # their 32-byte boundaries; the member itself is zero-padded to match.
    raw_delta = len(new_member) - member.length
    delta = -(-raw_delta // align) * align
    span = member.length + delta
    padding = span - len(new_member)

    new_offsets = [
        0 if offset == 0 or slot == member.slot else
        offset + delta if offset > member.offset else offset
        for slot, offset in enumerate(layout.offsets)
    ]
    new_offsets[member.slot] = member.offset

    block = bytearray()
    block += struct.pack('>I', layout.file_count)
    block += struct.pack(f'>{layout.file_count}I', *new_offsets)
    # Everything between the table and the replaced member: table padding and
    # every earlier member, none of which moves.
    block += container[layout.header_size:member.offset]
    block += new_member
    block += bytes(padding)
    suffix = container[member.end:]
    block += suffix

    expected = layout.length + delta
    if len(block) != expected:
        raise ValueError(
            f'rebuilt archive is {len(block)} bytes, expected {expected}'
        )

    report = {
        'archive_file_count':        layout.file_count,
        'archive_populated_slots':   len(layout.members),
        'archive_member_slot':       member.slot,
        'archive_member_offset':     member.offset,
        'archive_member_original_size': member.length,
        'archive_member_new_size':   span,
        'archive_member_padding':    padding,
        'archive_slots_shifted':     sum(
            1 for m in layout.members if m.offset > member.offset
        ),
        'archive_suffix_size':       len(suffix),
        'archive_original_size':     layout.length,
        'archive_new_size':          len(block),
        'archive_size_delta':        delta,
    }
    return bytes(block), report
