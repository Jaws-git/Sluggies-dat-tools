"""DOL-extension smoke test: add one marker data section to ``main.dol``.

The new section goes into the first free data slot and is appended to the end
of the file. It loads above both boot stacks (``SECTION_ADDRESS``, the stock
``__ArenaLo``), and every OSInit arena-start constant (``dol_map``) is raised
to the section end, so the heap starts after it on every loader-free boot
path. A loader that supplies its own arena start through ``0x8000310C`` or
``0x80000030`` bypasses those constants; the Dolphin check reads both.

Marker layout (all big-endian, the size is a multiple of 32):

  +0x00         ``MARKER_MAGIC`` (16 bytes)
  +0x10         u32 section address, u32 section size, u32 new arena start, u32 0
  +0x20 ..      one u32 per word holding that word's own address
  size - 0x10   ``MARKER_END`` (16 bytes)

Any heap or stack write into the section shows up as a word that no longer
equals its address.
"""

import argparse
import json
import os
import struct
from dataclasses import dataclass

try:
    from . import dol_map
except ImportError:
    import dol_map

_slogger = dol_map._slogger

SECTION_ADDRESS = 0x807B6E80
DEFAULT_SIZE = 0x1000
ALIGNMENT = 0x20
MEM1_END = 0x81800000
MARKER_MAGIC = b'SLUGGIES DOL P10'
MARKER_END = b'END OF P10 BLOCK'
MARKER_HEADER_SIZE = 0x20

OUTPUT_DIR = os.path.join(dol_map.ROOT, '3_Output_Dat', 'p10_smoke')
OUTPUT_DOL = os.path.join(OUTPUT_DIR, 'main.dol')
REPORT_PATH = os.path.join(OUTPUT_DIR, 'p10_smoke_report.json')

# Low-memory words that let a loader override the arena start (P2).
LOADER_ARENA_LO_WORDS = (0x8000310C, 0x80000030)


class DolSmokeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SmokeResult:
    dol: bytes
    slot: dol_map.Slot
    arena_start: int
    arena_patches: dict[int, tuple[int, int]]


def _align(value: int) -> int:
    return (value + ALIGNMENT - 1) & ~(ALIGNMENT - 1)


def build_marker(address: int, size: int, arena_start: int) -> bytes:
    if size % ALIGNMENT or size < MARKER_HEADER_SIZE + len(MARKER_END):
        raise DolSmokeError(f'marker size 0x{size:X} must be a multiple of 0x{ALIGNMENT:X} and at least 0x40')
    body_words = (size - MARKER_HEADER_SIZE - len(MARKER_END)) // 4
    body_start = address + MARKER_HEADER_SIZE
    return (
        MARKER_MAGIC
        + struct.pack('>IIII', address, size, arena_start, 0)
        + struct.pack(f'>{body_words}I', *(body_start + index * 4 for index in range(body_words)))
        + MARKER_END
    )


def _lis_addi_value(high_word: int, low_word: int) -> tuple[int, int] | None:
    """Return (register, value) if the words are ``lis rX`` + ``addi rX, rX``."""
    if high_word >> 26 != 15 or (high_word >> 16) & 31 != 0:
        return None
    register = (high_word >> 21) & 31
    if low_word >> 26 != 14 or (low_word >> 21) & 31 != register or (low_word >> 16) & 31 != register:
        return None
    low = low_word & 0xFFFF
    value = ((high_word & 0xFFFF) << 16) + (low - 0x10000 if low & 0x8000 else low)
    return register, value & 0xFFFFFFFF


def _lis_addi_words(register: int, value: int) -> tuple[int, int]:
    high = ((value + 0x8000) >> 16) & 0xFFFF
    return (
        (15 << 26) | (register << 21) | high,
        (14 << 26) | (register << 21) | (register << 16) | (value & 0xFFFF),
    )


def patch_arena_start(dol: bytearray, header: dol_map.DolHeader, sites: dict[int, int],
                      new_value: int) -> dict[int, tuple[int, int]]:
    """Rewrite each ``lis``/``addi`` pair from its stock value to ``new_value``.

    Returns ``{site: (old value, new value)}``. A site that already holds
    ``new_value`` is left alone; any other value is refused.
    """
    patched = {}
    for site, stock in sites.items():
        offset = dol_map.vaddr_to_file(header, site, 8)
        if offset is None:
            raise DolSmokeError(f'arena-start site 0x{site:08X} is not file-backed')
        high_word, low_word = struct.unpack_from('>II', dol, offset)
        decoded = _lis_addi_value(high_word, low_word)
        if decoded is None:
            raise DolSmokeError(f'0x{site:08X} is not a lis/addi pair')
        register, value = decoded
        if value == new_value:
            patched[site] = (value, value)
            continue
        if value != stock:
            raise DolSmokeError(
                f'arena-start constant at 0x{site:08X} is 0x{value:08X}; expected stock 0x{stock:08X}'
            )
        struct.pack_into('>II', dol, offset, *_lis_addi_words(register, new_value))
        patched[site] = (value, new_value)
    return patched


def add_data_section(dol: bytes, address: int, payload: bytes,
                     arena_sites: dict[int, int]) -> tuple[bytes, dol_map.Slot]:
    """Append ``payload`` as a new data section loading at ``address``.

    If a section with the same address and contents already exists the DOL is
    returned unchanged.
    """
    header = dol_map.parse_header(dol)
    if address % ALIGNMENT or len(payload) % ALIGNMENT:
        raise DolSmokeError(f'section address and size must be multiples of 0x{ALIGNMENT:X}')
    end = address + len(payload)
    for slot in header.used_slots:
        if slot.address == address and dol[slot.file_offset:slot.file_offset + slot.size] == payload:
            return dol, slot
        if slot.address < end and address < slot.end:
            raise DolSmokeError(f'0x{address:08X}-0x{end:08X} overlaps {slot.name}')
    if address < header.highest_loaded:
        raise DolSmokeError(
            f'0x{address:08X} is below the highest loaded address 0x{header.highest_loaded:08X}'
        )
    stacks_end = max(arena_sites.values())
    if address < stacks_end:
        raise DolSmokeError(
            f'0x{address:08X} is below 0x{stacks_end:08X}, the highest stock arena start; '
            'the boot stacks live below it'
        )
    if end > MEM1_END:
        raise DolSmokeError(f'section end 0x{end:08X} is past MEM1')
    free_data = [slot for slot in header.free_slots if not slot.is_text]
    if not free_data:
        raise DolSmokeError('no free data slot in the DOL header')
    slot_index = free_data[0].index
    file_offset = _align(len(dol))

    updated = bytearray(dol) + bytes(file_offset - len(dol)) + payload
    struct.pack_into('>I', updated, slot_index * 4, file_offset)
    struct.pack_into('>I', updated, 0x48 + slot_index * 4, address)
    struct.pack_into('>I', updated, 0x90 + slot_index * 4, len(payload))
    return bytes(updated), dol_map.Slot(slot_index, file_offset, address, len(payload))


def build_smoke_dol(dol: bytes, address: int = SECTION_ADDRESS, size: int = DEFAULT_SIZE,
                    arena_sites: dict[int, int] | None = None) -> SmokeResult:
    if arena_sites is None:
        arena_sites = dol_map.ARENA_LO_SITES | dol_map.MEM2_PATH_ARENA_LO_SITES
    arena_start = _align(address + size)
    marker = build_marker(address, size, arena_start)
    extended, slot = add_data_section(dol, address, marker, arena_sites)
    updated = bytearray(extended)
    patches = patch_arena_start(updated, dol_map.parse_header(extended), arena_sites, arena_start)
    validate_smoke_dol(bytes(updated), address, size, arena_sites)
    return SmokeResult(bytes(updated), slot, arena_start, patches)


def validate_smoke_dol(dol: bytes, address: int, size: int, arena_sites: dict[int, int]) -> None:
    header = dol_map.parse_header(dol)
    arena_start = _align(address + size)
    offset = dol_map.vaddr_to_file(header, address, size)
    if offset is None or dol[offset:offset + size] != build_marker(address, size, arena_start):
        raise DolSmokeError('marker section is missing or damaged')
    for site, value in dol_map.check_arena_lo_sites(dol, header, arena_sites).items():
        if value != arena_start:
            raise DolSmokeError(f'arena-start constant at 0x{site:08X} was not raised')
    used = sorted(header.used_slots, key=lambda slot: slot.address)
    for low, high in zip(used, used[1:]):
        if high.address < low.end:
            raise DolSmokeError(f'{low.name} and {high.name} overlap')


def _report(result: SmokeResult, source: str, output: str) -> dict:
    size = result.slot.size
    return {
        'source_dol': source,
        'output_dol': output,
        'section': {
            'slot': result.slot.name,
            'file_offset': f'0x{result.slot.file_offset:X}',
            'address': f'0x{result.slot.address:08X}',
            'end': f'0x{result.slot.end:08X}',
            'size': f'0x{size:X}',
        },
        'arena_start': f'0x{result.arena_start:08X}',
        'arena_constants': {
            f'0x{site:08X}': {'old': f'0x{old:08X}', 'new': f'0x{new:08X}'}
            for site, (old, new) in result.arena_patches.items()
        },
        'dolphin_checks': {
            'marker_start': f'0x{result.slot.address:08X} reads "{MARKER_MAGIC.decode()}"',
            'marker_body': f'every word from 0x{result.slot.address + MARKER_HEADER_SIZE:08X} holds its own address',
            'marker_end': f'0x{result.slot.end - len(MARKER_END):08X} reads "{MARKER_END.decode()}"',
            'loader_arena_words': [f'0x{word:08X}' for word in LOADER_ARENA_LO_WORDS],
            'mem1_arena_lo_variable': f'0x{dol_map.MEM1_ARENA_LO_VARIABLE:08X} should be 0x{result.arena_start:08X}',
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Build a main.dol with one extra marker data section (P10).')
    parser.add_argument('--dol', default=dol_map.INPUT_DOL, help='source DOL (default: stock 1_Input/main.dol)')
    parser.add_argument('--out', default=OUTPUT_DOL)
    parser.add_argument('--address', type=lambda text: int(text, 0), default=SECTION_ADDRESS)
    parser.add_argument('--size', type=lambda text: int(text, 0), default=DEFAULT_SIZE)
    parser.add_argument('--dry-run', action='store_true', help='build and validate without writing files')
    args = parser.parse_args()

    try:
        with open(args.dol, 'rb') as handle:
            source = handle.read()
        result = build_smoke_dol(source, args.address, args.size)
    except (OSError, DolSmokeError, dol_map.DolMapError) as exc:
        parser.exit(1, f'ERROR: {exc}\n')

    slot = result.slot
    _slogger.info(
        f'new section {slot.name}: file 0x{slot.file_offset:X}, 0x{slot.address:08X}-0x{slot.end:08X} '
        f'(0x{slot.size:X} bytes); DOL 0x{len(source):X} -> 0x{len(result.dol):X} bytes'
    )
    for site, (old, new) in result.arena_patches.items():
        _slogger.info(f'arena start at 0x{site:08X}: 0x{old:08X} -> 0x{new:08X}')
    if args.dry_run:
        _slogger.info('dry run: nothing written')
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'wb') as handle:
        handle.write(result.dol)
    report_path = os.path.join(os.path.dirname(os.path.abspath(args.out)), os.path.basename(REPORT_PATH))
    with open(report_path, 'w', encoding='utf-8') as handle:
        json.dump(_report(result, args.dol, args.out), handle, indent=2)
        handle.write('\n')
    _slogger.info(f'written {args.out}\nreport  {report_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
