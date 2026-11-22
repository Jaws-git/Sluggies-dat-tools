"""The donor model of a hammerspace build: where it is and how its bytes are read.

Every block is built from a donor, the vanilla model of the route (or the DOL entry a slot export
embeds). ``Donor`` reads it, with the absolute offsets a ``.sluggie`` carries; the ``Clone*`` functions
copy one section each; ``_resolve_donor_route`` finds the donor's DOL entry, the model inside it and
the archive or prefix around the model that the new block has to carry.
"""

import struct
from dataclasses import dataclass, replace

import slogger as _slogger
import HammerspaceHelper as hh
from binfmt import decode_field as _decode
from ArchiveContainer import parse_archive_container

from SluggieModel import _hex


def _source_dat_path(absolute_offset: int) -> str:
    """Return the DAT containing data at an exported absolute offset."""
    return hh.OUTPUT_DAT if absolute_offset >= hh.BASE_SIZE else hh.INPUT_DAT


# A slot export (Roster/slot_export.py) carries the DOL entry it was read
# from: ``DonorEntry`` = {"Offset": its first byte's address, "Data": the
# entry}. Such a .sluggie is built from that entry instead of the DAT files.
DONOR_ENTRY_KEY = 'DonorEntry'


def donor_entry(model: dict) -> tuple[int, bytes] | None:
    """``(offset, bytes)`` of a ``.sluggie``'s embedded donor entry, or None (the donor is in ``1_Input``)."""
    entry = model.get(DONOR_ENTRY_KEY)
    if entry is None:
        return None
    try:
        offset, data = _hex(entry['Offset']), _decode(entry['Data'], model.get('UseCompression', True))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'{DONOR_ENTRY_KEY} is unreadable: {exc}') from exc
    if not data:
        raise ValueError(f'{DONOR_ENTRY_KEY} holds no bytes')
    return offset, bytes(data)


@dataclass(frozen=True)
class Donor:
    """The donor model of a build, and where its bytes are read from. Every donor read of the section
    clones and patches goes through :meth:`read`, with the absolute offsets a ``.sluggie`` carries.

    ``base`` / ``data``: the donor's DOL entry, read once (:meth:`load`), or the entry a slot export
    embeds (:meth:`of_entry`, ``embedded``). ``data`` is None when the entry is not held in memory
    (:meth:`in_dat`, or an entry that could not be read whole); every read then goes to the DAT that
    holds the offset, as does a read outside a loaded entry. An embedded entry is never left: a read
    outside it is an error. ``model_offset`` / ``model_length``: the model inside the entry.
    """
    base:         int
    data:         bytes | None
    model_offset: int
    model_length: int
    embedded:     bool = False

    @classmethod
    def in_dat(cls, model_offset: int, model_length: int = 0) -> 'Donor':
        """A donor model read straight from the DAT files, nothing kept in memory."""
        return cls(model_offset, None, model_offset, model_length)

    @classmethod
    def load(cls, entry_offset: int, entry_length: int) -> 'Donor':
        """The DOL entry at ``entry_offset``, read once from the DAT that holds it. When it cannot be
        read whole nothing is kept, and the reads go to the file."""
        data = None
        if entry_length > 0:
            try:
                with open(_source_dat_path(entry_offset), 'rb') as source:
                    source.seek(entry_offset)
                    data = source.read(entry_length)
            except OSError:
                data = None
            if data is not None and len(data) != entry_length:
                data = None
        return cls(entry_offset, data, entry_offset, entry_length)

    @classmethod
    def of_entry(cls, base: int, data: bytes) -> 'Donor':
        """A slot export's embedded donor entry (``donor_entry``)."""
        return cls(base, data, base, len(data), embedded=True)

    def for_model(self, model_offset: int, model_length: int) -> 'Donor':
        """The same source, for the model at ``model_offset`` inside it."""
        return replace(self, model_offset=model_offset, model_length=model_length)

    def read(self, absolute_offset: int, length: int) -> bytes:
        """Up to ``length`` donor bytes at ``absolute_offset`` (fewer at the end of the source)."""
        if self.embedded:
            if not self.base <= absolute_offset < self.base + len(self.data):
                raise ValueError(f'donor read at 0x{absolute_offset:08X} is outside the embedded donor entry '
                                 f'0x{self.base:08X}+{len(self.data):,}')
            start = absolute_offset - self.base
            return self.data[start:] if length < 0 else self.data[start:start + length]
        if self.data is not None:
            start = absolute_offset - self.base
            if 0 <= start and start + length <= len(self.data):
                return self.data[start:start + length]
        with open(_source_dat_path(absolute_offset), 'rb') as source:
            source.seek(absolute_offset)
            return source.read(length)

    def header(self) -> tuple[int, ...]:
        """The eight words of the model's block header: 0, GPL, ACT, TEX, SKN, ptr6, ptr7, ptr8
        (block-relative section offsets, 0 where the model has no such section)."""
        return struct.unpack('>8I', self.read(self.model_offset, 0x20))

    def gpl_absolute(self) -> int:
        """Absolute offset of the model's GPL section (what the .sluggie's GPL field offsets count from)."""
        raw = self.read(self.model_offset + 0x04, 4)
        if len(raw) != 4:
            raise IOError(f'Could not read donor GPL offset at 0x{self.model_offset + 4:08X}')
        return self.model_offset + struct.unpack('>I', raw)[0]

    def act_absolute(self) -> int:
        """Absolute offset of the model's ACT section, 0 when the model has none.

        This is the reference point used to convert the ``.sluggie``'s absolute
        ``SRTOffset`` (``ACT.absolute + orientationPTR``) into an ACT-section-relative
        offset, which stays valid after the hammerspace block is relocated.
        """
        hdr = self.read(self.model_offset, 0x20)
        if len(hdr) < 0x20:
            return 0
        act_off = struct.unpack_from('>I', hdr, 0x08)[0]
        return self.model_offset + act_off if act_off else 0



def CloneGPL(donor: Donor) -> bytes:
    """Clone the GPL section verbatim from the model's donor.

    No pointer fixups needed (all internal GPL pointers are GPL-section-relative
    or DOLayout-relative).
    """
    _, gpl_off, act_off, tex_off, skn_off, *_ = donor.header()
    # GPL ends where the next present section starts
    gpl_len = (act_off or tex_off or skn_off or donor.model_length) - gpl_off
    data = donor.read(donor.model_offset + gpl_off, gpl_len)
    _slogger.info(f"[CloneGPL] {gpl_len:,} bytes from block+0x{gpl_off:X}", source="hammerspace.main")
    return data


def CloneACT(donor: Donor) -> bytes:
    """Clone the ACT section verbatim from the model's donor.

    Returns the raw ACT bytes unchanged, or b'' if the model has no ACT section.
    """
    _, _, act_off, tex_off, skn_off, *_ = donor.header()
    if not act_off:
        _slogger.info("[CloneACT] No ACT section", source="hammerspace.main")
        return b''
    act_len = (tex_off or skn_off or donor.model_length) - act_off
    data = donor.read(donor.model_offset + act_off, act_len)
    _slogger.info(f"[CloneACT] {act_len:,} bytes from block+0x{act_off:X}", source="hammerspace.main")
    return data


def CloneTEX(donor: Donor) -> bytes:
    """Clone the TEX section verbatim from the model's donor.

    Returns the raw TEX bytes unchanged, or b'' if the model has no TEX section.
    """
    _, _, _, tex_off, skn_off, *trailing_offsets = donor.header()
    if not tex_off:
        _slogger.info("[CloneTEX] No TEX section", source="hammerspace.main")
        return b''
    next_off = min(
        [offset for offset in [skn_off, *trailing_offsets] if offset > tex_off]
        + [donor.model_length]
    )
    tex_len = next_off - tex_off
    data = donor.read(donor.model_offset + tex_off, tex_len)
    _slogger.info(f"[CloneTEX] {tex_len:,} bytes from block+0x{tex_off:X}", source="hammerspace.main")
    return data


def CloneSKN(donor: Donor) -> bytes:
    """Clone the SKN section verbatim, excluding ptr6/ptr7/ptr8 sections."""
    _, _, _, _, skn_off, *trailing_offsets = donor.header()
    if not skn_off:
        _slogger.info("[CloneSKN] No SKN section", source="hammerspace.main")
        return b''
    skn_end = min(
        [offset for offset in trailing_offsets if offset > skn_off]
        + [donor.model_length]
    )
    skn_len = skn_end - skn_off
    data = donor.read(donor.model_offset + skn_off, skn_len)
    _slogger.info(f"[CloneSKN] {skn_len:,} bytes from block+0x{skn_off:X}", source="hammerspace.main")
    return data


def CloneTrailingSections(donor: Donor) -> tuple[bytes, int]:
    """Clone the contiguous ptr6/ptr7/ptr8 tail and return its original offset."""
    offsets = [offset for offset in donor.header()[5:] if 0 < offset < donor.model_length]
    if not offsets:
        _slogger.info("[CloneTrailing] No ptr6/ptr7/ptr8 sections", source="hammerspace.main")
        return b'', 0
    start = min(offsets)
    data = donor.read(donor.model_offset + start, donor.model_length - start)
    _slogger.info(f"[CloneTrailing] {len(data):,} bytes from block+0x{start:X}", source="hammerspace.main")
    return data, start


def CloneHEADER(donor: Donor) -> bytes:
    """Clone the 0x20-byte model block header verbatim from the donor.

    The header contains block-relative pointers to GPL, ACT, TEX, SKN, and
    any ptr6/ptr7/ptr8 sub-sections.  When all sections are cloned at their
    original sizes and reassembled in the same order, these pointers remain
    valid without any fixups.
    """
    data = donor.read(donor.model_offset, 0x20)
    _slogger.info(f"[CloneHEADER] 0x20 bytes from offset 0x{donor.model_offset:08X}", source="hammerspace.main")
    return data


def _is_absolute_offset_key(key) -> bool:
    return isinstance(key, str) and ('Offset' in key or key.endswith('AbsolutePtr'))


def _rebase_absolute_offsets(node, key, start: int, end: int, delta: int):
    """Shift every absolute offset in ``[start, end]`` by ``delta``, in place."""
    if isinstance(node, dict):
        for child_key, child in node.items():
            node[child_key] = _rebase_absolute_offsets(child, child_key, start, end, delta)
        return node
    if isinstance(node, list):
        for index, child in enumerate(node):
            node[index] = _rebase_absolute_offsets(child, key, start, end, delta)
        return node
    if not _is_absolute_offset_key(key) or isinstance(node, bool):
        return node
    if isinstance(node, int) and start <= node <= end:
        return node + delta
    if isinstance(node, str) and node.lower().startswith('0x'):
        try:
            value = int(node, 16)
        except ValueError:
            return node
        if start <= value <= end:
            return hex(value + delta)
    return node


def _rebase_hammerspace_resident_source(
    model: dict,
    source_model_offset: int,
    source_model_length: int,
    original_offset: int,
    original_length: int,
) -> int:
    """Point a schema exported from a hammerspace copy back at its vanilla donor.

    ``export.py --untangle`` clones the unused-character directories (89-94)
    into hammerspace and exports them from there, so their schemas carry
    hammerspace offsets. That copy does not survive patching: WriteModelBlock
    zeroes the block it replaces, and --unpatch restores the vanilla route.
    The clone was a verbatim copy of the INPUT DOL entry, so the vanilla block
    is the same donor. Every absolute offset inside the model range is shifted
    onto it, and the archive prefix, if any, is kept like any vanilla donor's.
    """
    if source_model_length == original_length:
        prefix_size = 0
    else:
        with open(hh.INPUT_DAT, 'rb') as source:
            source.seek(original_offset)
            container = source.read(original_length)
        layout = parse_archive_container(container)
        members = [
            member for member in (layout.members if layout else ())
            if member.length == source_model_length
        ]
        if len(members) != 1:
            raise ValueError(
                f'schema was exported from hammerspace (0x{source_model_offset:08X}) and '
                f'its {source_model_length:,}-byte model cannot be matched to exactly one '
                f'slot of the vanilla DOL entry 0x{original_offset:08X}+{original_length:,}; '
                're-export the model')
        prefix_size = members[0].offset
    vanilla_model_offset = original_offset + prefix_size
    delta = vanilla_model_offset - source_model_offset
    _rebase_absolute_offsets(
        model, None, source_model_offset, source_model_offset + source_model_length, delta)
    _slogger.info(
        f'[Container] schema was exported from a hammerspace copy at '
        f'0x{source_model_offset:08X}; using the vanilla donor at '
        f'0x{vanilla_model_offset:08X} instead',
        source='hammerspace.main',
    )
    return vanilla_model_offset


def _live_archive(chunk_number: int, file_index: int, vanilla: bytes, vanilla_layout, slot: int):
    """The archive the output DOL routes ``(chunk_number, file_index)`` to now, as ``(bytes, layout)``.

    Every patch of an archive member writes the whole archive, so a member
    patched earlier lives only in this copy; rebuilding the vanilla archive
    instead would drop it. None (rebuild the vanilla archive) when the live
    entry cannot be read or is not the same archive: other slots populated,
    or ``slot`` off the 32-byte grid.
    """
    offset, length = hh.readOutputDolEntry(chunk_number, file_index)
    if offset < 0 or length <= 0:
        return None
    try:
        with open(hh.OUTPUT_DAT, 'rb') as output:
            output.seek(offset)
            container = output.read(length)
    except OSError:
        return None
    layout = parse_archive_container(container) if len(container) == length else None
    if layout is None or layout.file_count != vanilla_layout.file_count or [
        bool(o) for o in layout.offsets
    ] != [bool(o) for o in vanilla_layout.offsets] or layout.offsets[slot] % hh.HS_ALIGN_BYTES:
        _slogger.warning(
            f'[Container] the output entry 0x{offset:08X}+{length:,} is not the same archive as the '
            'vanilla one; rebuilding the vanilla archive, so edits of its other members are dropped',
            source='hammerspace.main',
        )
        return None
    vanilla_members = {m.slot: vanilla[m.offset:m.end] for m in vanilla_layout.members}
    edited = [
        m.slot for m in layout.members
        if m.slot != slot and container[m.offset:m.end] != vanilla_members[m.slot]
    ]
    if edited:
        _slogger.info(
            f'[Container] carrying the live archive at 0x{offset:08X}+{length:,}: '
            f'slot(s) {", ".join(map(str, edited))} keep their earlier edits',
            source='hammerspace.main',
        )
    return container, layout


@dataclass
class _DonorRoute:
    """Where a build's donor model is, and what its DOL entry holds around it."""
    chunk_number:        int
    file_index:          int
    original_offset:     int            # the DOL entry (or the embedded donor entry)
    original_length:     int
    route_prefix:        bytes          # entry bytes before the model (not an archive)
    route_prefix_size:   int
    archive_layout:      object         # the entry's archive table, or None
    base_container:      bytes          # the archive the rebuilt member goes into
    base_layout:         object
    block_model_offset:  int            # where the member starts in that archive
    donor:               Donor          # the model inside that entry, and the reader of its bytes


def _resolve_donor_route(model: dict, carry_live_members: bool,
                         embedded: tuple[int, bytes] | None = None) -> _DonorRoute:
    """Find the donor model of a build: its DOL entry (or ``embedded``, the donor entry of a slot export:
    ``donor_entry(model)``), the model inside it, and the prefix or archive around the model that the new
    block has to carry. The entry is read once here; the route's ``donor`` serves every later read."""
    chunk_number = model['ChunkNumber']
    file_index = model['FileIndex']
    if embedded is not None:
        original_offset, original_length = embedded[0], len(embedded[1])
    else:
        original_offset, original_length = hh.readDolEntry(chunk_number, file_index)
    if original_offset == -1 or original_length <= 0:
        raise ValueError(
            f'Invalid donor DOL entry for chunk={chunk_number}, file_index={file_index}: '
            f'offset={original_offset}, length={original_length}'
        )
    entry = Donor.of_entry(*embedded) if embedded is not None else Donor.load(original_offset, original_length)
    source_model_offset = _hex(model.get('ModelOffset', original_offset))
    source_model_length = model.get('ModelLength', original_length)
    route_prefix = b''
    route_prefix_size = 0
    route_container = b''
    archive_layout = None
    # The archive the new member is put into, and where the member starts in
    # it: the vanilla archive unless the live one is carried (below).
    base_container = b''
    base_layout = None
    block_model_offset = 0
    if source_model_offset >= hh.BASE_SIZE and embedded is None:
        source_model_offset = _rebase_hammerspace_resident_source(
            model, source_model_offset, source_model_length,
            original_offset, original_length,
        )
    route_prefix_size = source_model_offset - original_offset
    if route_prefix_size < 0 or route_prefix_size + source_model_length > original_length:
        raise ValueError(
            f'schema model range 0x{source_model_offset:08X}+{source_model_length:,} '
            f'is outside donor DOL entry 0x{original_offset:08X}+{original_length:,}')
    route_suffix_size = original_length - route_prefix_size - source_model_length
    if route_prefix_size or route_suffix_size:
        # The DOL entry holds more than this model, so read all of it: the
        # bytes around the model have to be carried into the new block.
        route_container = entry.read(original_offset, original_length)
        if len(route_container) != original_length:
            raise IOError(
                f'could not read the {original_length} byte DOL entry '
                f'at 0x{original_offset:08X}')
        route_prefix = route_container[:route_prefix_size]
        archive_layout = parse_archive_container(route_container)
        if archive_layout is not None and archive_layout.member_at(route_prefix_size) is None:
            # An archive whose table does not list this model: rebuilding it
            # would shift members the schema knows nothing about.
            raise ValueError(
                f'model at 0x{source_model_offset:08X} is not a populated slot of '
                f'the archive at 0x{original_offset:08X}')
        if archive_layout is not None:
            member = archive_layout.member_at(route_prefix_size)
            if member.length != source_model_length:
                raise ValueError(
                    f'archive slot {member.slot} spans {member.length:,} bytes but the '
                    f'schema reports ModelLength={source_model_length:,}')
            if member.offset % hh.HS_ALIGN_BYTES:
                # The block lands on a 32-byte boundary, so a member off the
                # boundary would drag the model's hot data off it too.
                raise ValueError(
                    f'archive slot {member.slot} starts at +0x{member.offset:X}, which is '
                    f'not a multiple of {hh.HS_ALIGN_BYTES}')
            _slogger.info(
                f'[Container] DOL entry is an archive: {archive_layout.file_count} slots, '
                f'{len(archive_layout.members)} populated; rebuilding slot {member.slot} '
                f'at +0x{member.offset:X} and carrying the other '
                f'{len(archive_layout.members) - 1} member(s) along',
                source='hammerspace.main',
            )
            base_container, base_layout = route_container, archive_layout
            if carry_live_members and embedded is None:
                live = _live_archive(chunk_number, file_index, route_container, archive_layout, member.slot)
                if live is not None:
                    base_container, base_layout = live
            block_model_offset = base_layout.offsets[member.slot]
        else:
            if route_suffix_size:
                _slogger.warning(
                    f'[Container] DOL entry 0x{original_offset:08X}+{original_length:,} has '
                    f'{route_suffix_size:,} bytes after the model but is not a readable '
                    f'archive; those bytes are dropped from the rebuilt block',
                    source='hammerspace.main',
                )
            if route_prefix_size:
                _slogger.info(
                    f'[Container] preserving {route_prefix_size}-byte DOL entry prefix; '
                    f'inner model starts at +0x{route_prefix_size:X}',
                    source='hammerspace.main',
                )
    return _DonorRoute(
        chunk_number=chunk_number,
        file_index=file_index,
        original_offset=original_offset,
        original_length=original_length,
        route_prefix=route_prefix,
        route_prefix_size=route_prefix_size,
        archive_layout=archive_layout,
        base_container=base_container,
        base_layout=base_layout,
        block_model_offset=block_model_offset,
        donor=entry.for_model(source_model_offset, source_model_length),
    )
