"""``main.dol`` image: header parsing, address mapping, verified writes and new sections.

DOL header (all big-endian):

  +0x00  18 x u32  file offset of text slots T0..T6, then data slots D0..D10
  +0x48  18 x u32  load address of each slot
  +0x90  18 x u32  size of each slot; a slot with size 0 is unused
  +0xD8  u32  bss address, u32 bss size, u32 entry point

``parse_header`` is read-only (``Icons/dol_map.py`` re-exports it).
``DolImage`` is the mutable form used by patch steps: every word write can
check the stock value first, and sections can be added, resized, removed or
merged. Sections this module adds are appended at the end of the file, 32-byte
aligned, as the DOL loader expects.
"""

import struct
from dataclasses import dataclass

HEADER_SIZE = 0x100
TEXT_SLOTS = 7
DATA_SLOTS = 11
SLOT_COUNT = TEXT_SLOTS + DATA_SLOTS
SECTION_ALIGN = 0x20


class DolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Slot:
    index: int
    file_offset: int
    address: int
    size: int

    @property
    def name(self) -> str:
        if self.index < TEXT_SLOTS:
            return f'T{self.index}'
        return f'D{self.index - TEXT_SLOTS}'

    @property
    def is_text(self) -> bool:
        return self.index < TEXT_SLOTS

    @property
    def used(self) -> bool:
        return self.size != 0

    @property
    def end(self) -> int:
        return self.address + self.size


@dataclass(frozen=True)
class DolHeader:
    slots: tuple[Slot, ...]
    bss_address: int
    bss_size: int
    entry: int
    file_size: int

    @property
    def used_slots(self) -> list[Slot]:
        return [slot for slot in self.slots if slot.used]

    @property
    def free_slots(self) -> list[Slot]:
        return [slot for slot in self.slots if not slot.used]

    @property
    def bss_end(self) -> int:
        return self.bss_address + self.bss_size

    @property
    def highest_loaded(self) -> int:
        """End of the highest byte the loader writes or zeroes."""
        return max([self.bss_end] + [slot.end for slot in self.used_slots])


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('>I', data, offset)[0]


def align_up(value: int, alignment: int = SECTION_ALIGN) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def parse_header(dol: bytes) -> DolHeader:
    if len(dol) < HEADER_SIZE:
        raise DolError('DOL header is truncated')
    slots = []
    for index in range(SLOT_COUNT):
        slot = Slot(index, _u32(dol, index * 4), _u32(dol, 0x48 + index * 4), _u32(dol, 0x90 + index * 4))
        if slot.used and slot.file_offset + slot.size > len(dol):
            raise DolError(f'DOL section {slot.name} is truncated')
        slots.append(slot)
    bss_address, bss_size, entry = struct.unpack_from('>III', dol, 0xD8)
    return DolHeader(tuple(slots), bss_address, bss_size, entry, len(dol))


def slot_at(header: DolHeader, address: int, size: int = 1) -> Slot | None:
    for slot in header.used_slots:
        if slot.address <= address and address + size <= slot.end:
            return slot
    return None


def vaddr_to_file(header: DolHeader, address: int, size: int = 1) -> int | None:
    slot = slot_at(header, address, size)
    return None if slot is None else slot.file_offset + address - slot.address


def region_of(header: DolHeader, address: int) -> str:
    """Name what backs ``address``: a slot name, ``bss`` or ``unmapped``."""
    slot = slot_at(header, address)
    if slot is not None:
        return slot.name
    if header.bss_address <= address < header.bss_end:
        return 'bss'
    return 'unmapped'


def read_word(dol: bytes, header: DolHeader, address: int) -> int:
    offset = vaddr_to_file(header, address, 4)
    if offset is None:
        raise DolError(f'0x{address:08X} is not file-backed')
    return _u32(dol, offset)


class DolImage:
    """A ``main.dol`` in memory, addressed by load address."""

    def __init__(self, data: bytes):
        self.data = bytearray(data)
        self.header = parse_header(self.data)

    def to_bytes(self) -> bytes:
        return bytes(self.data)

    # -- reading ---------------------------------------------------------
    def offset_of(self, address: int, size: int = 1) -> int:
        offset = vaddr_to_file(self.header, address, size)
        if offset is None:
            raise DolError(f'0x{address:08X} (+0x{size:X}) is not file-backed')
        return offset

    def read(self, address: int, size: int) -> bytes:
        offset = self.offset_of(address, size)
        return bytes(self.data[offset:offset + size])

    def u32(self, address: int) -> int:
        return _u32(self.data, self.offset_of(address, 4))

    def is_mapped(self, address: int, size: int = 1) -> bool:
        return slot_at(self.header, address, size) is not None

    # -- writing ---------------------------------------------------------
    def write(self, address: int, blob: bytes) -> None:
        offset = self.offset_of(address, len(blob))
        self.data[offset:offset + len(blob)] = blob

    def write_word(self, address: int, value: int) -> None:
        self.write(address, struct.pack('>I', value & 0xFFFFFFFF))

    def patch_word(self, address: int, expect: int, new: int) -> None:
        """Write ``new`` at ``address`` after checking the word there is ``expect``.

        A word that already holds ``new`` is refused too: patch steps run on
        a DOL their own unpatch has restored, so finding the patched value
        means the restore was incomplete.
        """
        got = self.u32(address)
        if got != expect:
            raise DolError(f'0x{address:08X}: expected stock word {expect:08X}, found {got:08X}')
        self.write_word(address, new)

    def patch_bytes(self, address: int, expect: bytes, new: bytes) -> None:
        if len(expect) != len(new):
            raise DolError(f'0x{address:08X}: stock and new bytes differ in length')
        got = self.read(address, len(expect))
        if got != expect:
            raise DolError(f'0x{address:08X}: expected stock bytes {expect.hex()}, found {got.hex()}')
        self.write(address, new)

    # -- sections --------------------------------------------------------
    def _store_header(self, slots: list[Slot]) -> None:
        for slot in slots:
            struct.pack_into('>I', self.data, slot.index * 4, slot.file_offset)
            struct.pack_into('>I', self.data, 0x48 + slot.index * 4, slot.address)
            struct.pack_into('>I', self.data, 0x90 + slot.index * 4, slot.size)
        self.header = parse_header(self.data)

    def section_at(self, address: int) -> Slot | None:
        """The used slot that loads exactly at ``address``."""
        return next((s for s in self.header.used_slots if s.address == address), None)

    def _check_free_range(self, address: int, size: int, ignore: Slot | None = None) -> None:
        if address % SECTION_ALIGN:
            raise DolError(f'section address 0x{address:08X} is not 32-byte aligned')
        for slot in self.header.used_slots:
            if slot is ignore or (ignore is not None and slot.index == ignore.index):
                continue
            if address < slot.end and slot.address < address + size:
                raise DolError(f'section 0x{address:08X}+0x{size:X} overlaps {slot.name}')
        if address < self.header.bss_end and self.header.bss_address < address + size:
            raise DolError(f'section 0x{address:08X}+0x{size:X} overlaps bss')

    def add_section(self, kind: str, address: int, blob: bytes) -> Slot:
        """Append a ``'text'`` or ``'data'`` section loaded at ``address``; returns its slot."""
        if kind not in ('text', 'data'):
            raise DolError(f'unknown section kind {kind!r}')
        if not blob:
            raise DolError('a section needs at least one byte (size 0 marks an unused slot)')
        indices = range(TEXT_SLOTS) if kind == 'text' else range(TEXT_SLOTS, SLOT_COUNT)
        free = [s for s in self.header.slots if s.index in indices and not s.used]
        if not free:
            raise DolError(f'no free {kind} slot in the DOL header')
        payload = bytes(blob) + b'\0' * (-len(blob) % SECTION_ALIGN)
        self._check_free_range(address, len(payload))
        offset = align_up(len(self.data))
        self.data += b'\0' * (offset - len(self.data)) + payload
        slot = Slot(free[0].index, offset, address, len(payload))
        self._store_header([slot])
        return self.header.slots[slot.index]

    def remove_section(self, slot: Slot) -> None:
        """Free ``slot``. Its bytes are cut off when they are the file's tail, else left unreferenced."""
        current = self.header.slots[slot.index]
        if not current.used:
            raise DolError(f'{current.name} is not in use')
        tail = align_up(current.file_offset + current.size) >= len(self.data)
        self._store_header([Slot(current.index, 0, 0, 0)])
        if tail:
            del self.data[current.file_offset:]
            self.header = parse_header(self.data)

    def replace_section(self, slot: Slot, blob: bytes) -> Slot:
        """Give ``slot`` new contents (any size) at the same load address and kind."""
        current = self.header.slots[slot.index]
        kind = 'text' if current.is_text else 'data'
        payload = bytes(blob) + b'\0' * (-len(blob) % SECTION_ALIGN)
        self._check_free_range(current.address, len(payload), ignore=current)
        self.remove_section(current)
        added = self.add_section(kind, current.address, payload)
        if added.index != current.index:  # keep the slot number stable
            self._store_header([Slot(added.index, 0, 0, 0), Slot(current.index, added.file_offset,
                                                               added.address, added.size)])
            added = self.header.slots[current.index]
        return added

    def merge_sections(self, slots: list[Slot], kind: str = 'text') -> Slot:
        """Replace ``slots`` with one section covering their whole range (zeros in the gaps)."""
        current = [self.header.slots[s.index] for s in slots]
        low = min(s.address for s in current)
        high = max(s.end for s in current)
        blob = bytearray(high - low)
        for s in current:
            blob[s.address - low:s.end - low] = self.data[s.file_offset:s.file_offset + s.size]
        for s in sorted(current, key=lambda s: s.file_offset, reverse=True):
            self.remove_section(s)
        return self.add_section(kind, low, bytes(blob))


class Space:
    """Sequential allocator for a section being built at ``base`` (contents in ``blob``)."""

    def __init__(self, base: int, limit: int, blob: bytes = b''):
        self.base, self.limit, self.blob = base, limit, bytearray(blob)

    @property
    def here(self) -> int:
        return self.base + len(self.blob)

    def put(self, data: bytes, align: int = 4) -> int:
        pad = -len(self.blob) % align
        if self.here + pad + len(data) > self.limit:
            raise DolError(f'section at 0x{self.base:08X} would pass its limit 0x{self.limit:08X}')
        self.blob += b'\0' * pad
        address = self.here
        self.blob += data
        return address

    def reserve(self, size: int, align: int = 4) -> int:
        return self.put(bytes(size), align)

    def write(self, address: int, data: bytes) -> None:
        """Overwrite bytes already allocated (e.g. a table filled in after its users were built)."""
        start = address - self.base
        if start < 0 or start + len(data) > len(self.blob):
            raise DolError(f'0x{address:08X}+0x{len(data):X} is outside the allocated part of 0x{self.base:08X}')
        self.blob[start:start + len(data)] = data
