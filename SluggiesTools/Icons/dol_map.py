"""Read-only map of ``main.dol``: section slots, bss, stack and arena start.

DOL header (all big-endian):

  +0x00  18 x u32  file offset of text slots T0..T6, then data slots D0..D10
  +0x48  18 x u32  load address of each slot
  +0x90  18 x u32  size of each slot; a slot with size 0 is unused
  +0xD8  u32  bss address, u32 bss size, u32 entry point

The header's bss range is the whole zero-initialised span. CodeWarrior's
``.sdata``/``.sdata2`` sections (D6/D7 in this game) sit inside it; the real
bss pieces are listed in the ``_bss_init_info`` table that ``__init_data``
walks.

Memory above bss is not free. ``__init_registers`` loads the initial stack
pointer with ``lis r1`` / ``ori r1``, and the stack grows down to the end of
bss. ``OSInit`` then sets the MEM1 arena start (``__ArenaLo``) from two
``lis``/``addi`` constants unless the loader supplied one (see
``ARENA_LO_SITES``). A new DOL section must load at or above the arena start,
and the arena start must then be raised past it.
"""

import argparse
import os
import struct
from dataclasses import dataclass

_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
import sys as _sys
if _TOOLS_DIR not in _sys.path:
    _sys.path.insert(0, _TOOLS_DIR)
import slogger as _slogger
_slogger.configure()

ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
INPUT_DOL = os.path.join(ROOT, '1_Input', 'main.dol')

HEADER_SIZE = 0x100
TEXT_SLOTS = 7
DATA_SLOTS = 11
SLOT_COUNT = TEXT_SLOTS + DATA_SLOTS

# How far past a ``lis`` the matching low-half instruction may sit.
PAIR_WINDOW = 8
# How far above the end of bss to look for stack and arena constants.
POST_BSS_WINDOW = 0x20000

# US main.dol. Each site is the ``lis`` of a lis/addi pair in OSInit that
# produces a MEM1 arena start. OSInit takes them only when the MEM1 arena
# start in low memory (0x8000310C) and BootInfo arenaLo (0x80000030) are
# both 0.
ARENA_LO_SITES = {
    # __ArenaLo: end of the 0x2000-byte debugger stack. Used when there is
    # no BI2 debug-flag pointer, or the flag is 2 or more.
    0x80595FC4: 0x807B6E80,
    # _stack_addr rounded up to 32. Used when the BI2 debug flag is 0 or 1
    # (no debugger): the debugger stack then becomes arena.
    0x80596014: 0x807B4E80,
}
# The same two constants again in OSInit's MEM2 arena-start code. They only
# take effect when __ArenaLo lies in MEM2, but a patch keeps them in step.
MEM2_PATH_ARENA_LO_SITES = {
    0x8059606C: 0x807B6E80,
    0x805960A0: 0x807B4E80,
}
# The variable OSSetMEM1ArenaLo writes (r13 - 0x2370); stock value -1.
MEM1_ARENA_LO_VARIABLE = 0x80793E50


class DolMapError(RuntimeError):
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


@dataclass(frozen=True)
class AddressConstant:
    """A ``lis`` + low-half pair that builds a full 32-bit address."""
    lis_site: int
    low_site: int
    register: int
    value: int


# addi and the D-form loads/stores (lwz..sthu, lfs..stfd): rD, d(rA).
_LOW_HALF_OPCODES = frozenset((14, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 48, 50, 52, 54))
# Of those, the ones whose rD field is not a destination GPR (stores, FPR loads).
_NO_GPR_WRITE_OPCODES = frozenset((36, 37, 38, 39, 44, 45, 48, 50, 52, 54))


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('>I', data, offset)[0]


def _signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def parse_header(dol: bytes) -> DolHeader:
    if len(dol) < HEADER_SIZE:
        raise DolMapError('DOL header is truncated')
    slots = []
    for index in range(SLOT_COUNT):
        slot = Slot(index, _u32(dol, index * 4), _u32(dol, 0x48 + index * 4), _u32(dol, 0x90 + index * 4))
        if slot.used and slot.file_offset + slot.size > len(dol):
            raise DolMapError(f'DOL section {slot.name} is truncated')
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
        raise DolMapError(f'0x{address:08X} is not file-backed')
    return _u32(dol, offset)


def find_address_constants(dol: bytes, header: DolHeader, low: int, high: int) -> list[AddressConstant]:
    """Find ``lis rX`` + ``addi``/``ori``/D-form load-store on rX that build ``low <= value < high``.

    The pair must sit in one text slot within ``PAIR_WINDOW`` instructions,
    and the ``lis`` target must not be redefined in between by another ``lis``.
    """
    found = []
    for slot in header.used_slots:
        if not slot.is_text:
            continue
        pending: dict[int, tuple[int, int]] = {}
        for rel in range(0, slot.size - 3, 4):
            site = slot.address + rel
            word = _u32(dol, slot.file_offset + rel)
            opcode = word >> 26
            rd, ra, imm = (word >> 21) & 31, (word >> 16) & 31, word & 0xFFFF
            if opcode == 15 and ra == 0:
                pending[rd] = (imm << 16, site)
                continue
            if (opcode == 18 and word & 1) or opcode == 19:  # calls, blr/bctr: registers are dead
                pending.clear()
                continue
            if opcode == 24:  # ori rA, rS, imm: the source is the rS field
                source, register = rd, ra
            elif opcode in _LOW_HALF_OPCODES:
                source, register = ra, rd
            else:
                continue
            if source in pending and site - pending[source][1] <= PAIR_WINDOW * 4:
                base, lis_site = pending[source]
                value = base | imm if opcode == 24 else (base + _signed16(imm)) & 0xFFFFFFFF
                if low <= value < high:
                    found.append(AddressConstant(lis_site, site, register, value))
            if opcode not in _NO_GPR_WRITE_OPCODES:
                pending.pop(register, None)
    return found


def initial_stack_pointer(dol: bytes, header: DolHeader) -> AddressConstant | None:
    """Return the first ``lis r1`` / ``ori|addi r1, r1`` pair in the entry point's slot."""
    entry_slot = slot_at(header, header.entry)
    if entry_slot is None:
        return None
    for constant in find_address_constants(dol, header, 0x80000000, 0x81800000):
        if constant.register == 1 and entry_slot.address <= constant.lis_site < entry_slot.end:
            word = read_word(dol, header, constant.lis_site)
            if (word >> 21) & 31 == 1:
                return constant
    return None


def check_arena_lo_sites(dol: bytes, header: DolHeader,
                         sites: dict[int, int] = ARENA_LO_SITES) -> dict[int, int | None]:
    """Decode each known arena-start pair; ``None`` if the site no longer holds a lis/addi."""
    decoded: dict[int, int | None] = dict.fromkeys(sites)
    for constant in find_address_constants(dol, header, 0, 0x100000000):
        if constant.lis_site in decoded and decoded[constant.lis_site] is None:
            decoded[constant.lis_site] = constant.value
    return decoded


def main() -> int:
    parser = argparse.ArgumentParser(description='Print the section map and boot memory layout of main.dol.')
    parser.add_argument('--dol', default=INPUT_DOL)
    args = parser.parse_args()

    with open(args.dol, 'rb') as handle:
        dol = handle.read()
    header = parse_header(dol)

    _slogger.info(f'{args.dol}: 0x{header.file_size:X} bytes, entry 0x{header.entry:08X}')
    for slot in header.slots:
        if slot.used:
            _slogger.info(
                f'  {slot.name:<3} file 0x{slot.file_offset:06X}  '
                f'0x{slot.address:08X}-0x{slot.end:08X}  size 0x{slot.size:X}'
            )
        else:
            _slogger.info(f'  {slot.name:<3} unused')
    free = ', '.join(slot.name for slot in header.free_slots) or 'none'
    _slogger.info(f'free slots: {free}')
    _slogger.info(
        f'bss 0x{header.bss_address:08X} + 0x{header.bss_size:X} = end 0x{header.bss_end:08X}; '
        f'highest loaded address 0x{header.highest_loaded:08X}'
    )

    stack = initial_stack_pointer(dol, header)
    if stack is not None:
        _slogger.info(
            f'initial stack pointer 0x{stack.value:08X} (set at 0x{stack.lis_site:08X}); '
            f'stack 0x{header.bss_end:08X}-0x{stack.value:08X}, 0x{stack.value - header.bss_end:X} bytes'
        )
    else:
        _slogger.warning('initial stack pointer not found')

    all_sites = ARENA_LO_SITES | MEM2_PATH_ARENA_LO_SITES
    for site, value in check_arena_lo_sites(dol, header, all_sites).items():
        expected = all_sites[site]
        state = 'stock' if value == expected else f'CHANGED (stock 0x{expected:08X})'
        shown = 'no lis/addi pair' if value is None else f'0x{value:08X}'
        _slogger.info(f'arena start constant at 0x{site:08X}: {shown} {state}')

    window_end = header.highest_loaded + POST_BSS_WINDOW
    _slogger.info(f'code constants in 0x{header.highest_loaded:08X}-0x{window_end:08X}:')
    for constant in find_address_constants(dol, header, header.highest_loaded, window_end):
        _slogger.info(
            f'  0x{constant.lis_site:08X}/0x{constant.low_site:08X}  r{constant.register} = 0x{constant.value:08X}'
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
