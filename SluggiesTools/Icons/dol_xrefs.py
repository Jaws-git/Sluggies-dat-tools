"""Static cross-references of ``main.dol`` to the character-ID tables and ID bounds.

Read-only. It answers two questions for relocating or extending the
colour-wheel table and the character ID space:

* which code builds the address of each known per-character table
  (``lis`` + low half, like ``dol_map.find_address_constants``, but tracking
  callee-saved registers across calls and branches, and ``mr`` copies);
* where the code compares against, or loops up to, the ID-space bounds.

Character IDs (US ``main.dol``):

  0x00-0x46  player characters
  0x47-0x4C  unused player IDs (full rows and model directories, not selectable)
  0x4D-0x64  Miis (colour-wheel groups 0x0C male / 0x0D female)
  0x65       "no character" sentinel in team and record code

The game inlines two range helpers: *is player* (``0 <= id < 0x4D``) and
*is Mii* (``0x4D <= id <= 0x64``). ``classify_compare`` recognises both.
"""

import argparse
import json
import os
import struct
from dataclasses import asdict, dataclass

try:
    from . import dol_map
except ImportError:
    import dol_map

_slogger = dol_map._slogger

INPUT_DOL = dol_map.INPUT_DOL

PLAYER_ID_END = 0x4D     # is-player: id < 0x4D
MII_ID_LAST = 0x64       # is-Mii: 0x4D <= id <= 0x64
NO_CHARACTER = 0x65      # sentinel, and the row count of every per-ID table
BOUND_VALUES = (0x47, PLAYER_ID_END, MII_ID_LAST, NO_CHARACTER)

# How far past a ``lis`` its low half may sit. Larger than dol_map's window
# because the lis is often hoisted to the top of a loop or function.
REF_WINDOW = 256
# How far past an ``li`` the ``mtctr`` of a counted loop may sit.
LOOP_WINDOW = 8
# How far around a bound compare to look for the other half of a range check.
RANGE_WINDOW = 3


@dataclass(frozen=True)
class IdTable:
    name: str
    address: int
    stride: int
    note: str
    rows: int = NO_CHARACTER

    @property
    def end(self) -> int:
        return self.address + self.stride * self.rows


# Tables indexed by character ID. Found statically in September 2026: every
# data label whose size is 101 rows of its access stride, kept only where the
# Mii rows (0x4D-0x64) are uniform and the Yoshi variants differ, as a
# per-character table would be. The .data ones are file-backed and move with
# a relocation; the bss one is runtime state.
ID_TABLES = (
    IdTable('color_wheel', 0x80631550, 8, 'colour-wheel rows (group, host, species, ..., selectable, colour)'),
    IdTable('own_data', 0x806B4970, 1, '1 = has its own character data; 0 makes 0x80367060 substitute ID 4'),
    IdTable('random_pool', 0x806314E8, 1, 'same bytes as own_data; 0x8046CCA4 draws random team members from it'),
    IdTable('model_load', 0x806B49D8, 8, 'read by the model loader 0x8036629C'),
    IdTable('flags_80630A28', 0x80630A28, 1, ''),
    IdTable('params_80628400', 0x80628400, 12, 'floats'),
    IdTable('params_806288BC', 0x806288BC, 1, ''),
    IdTable('params_80628924', 0x80628924, 2, ''),
    IdTable('params_806289F0', 0x806289F0, 12, 'floats'),
    IdTable('params_80628EB0', 0x80628EB0, 8, 'floats'),
    IdTable('params_806291D8', 0x806291D8, 0x24, 'floats'),
    IdTable('params_8062A00C', 0x8062A00C, 2, ''),
    IdTable('params_8062A15C', 0x8062A15C, 2, ''),
    IdTable('params_8062A228', 0x8062A228, 5, ''),
    IdTable('params_8062A424', 0x8062A424, 6, ''),
    IdTable('params_8062A688', 0x8062A688, 0x28, 'floats'),
    IdTable('params_8062B678', 0x8062B678, 5, ''),
    IdTable('params_8062B874', 0x8062B874, 4, ''),
    IdTable('params_8062BA08', 0x8062BA08, 8, 'floats'),
    IdTable('params_8062EB50', 0x8062EB50, 8, 'floats, read after the own_data substitution'),
    IdTable('runtime_80709408', 0x80709408, 12, 'bss, read after the own_data substitution'),
)
# Indexed by species (colour-wheel byte 2), listed for its xrefs.
SPECIES_BASE_TABLE = IdTable('species_base_id', 0x80631878, 1, 'species index -> base character ID', rows=0x2B)

# ``addi rX, rId, 0x12``: model directory = character ID + 18.
MODEL_DIR_SITES = (0x8036625C, 0x803664E8, 0x80366530)
MODEL_DIR_OFFSET = 0x12
# ``li r0, 0x97`` in the icon resolver: the Mii icon row (Mii path, IDs 0x4D-0x64).
ICON_FALLBACK_ROW_SITE = 0x80395EBC
ICON_FALLBACK_ROW = 0x97

_VOLATILE = frozenset((0,) + tuple(range(3, 13)))
# D-form instructions whose low 16 bits complete a lis address: addi, ori and
# the integer/float loads and stores.
_LOW_HALF_OPCODES = frozenset((14, 24) + tuple(range(32, 46)) + tuple(range(48, 56)))
_UPDATE_OPCODES = frozenset((33, 35, 37, 39, 41, 43, 45, 49, 51, 53, 55))
_RD_WRITE_OPCODES = frozenset((7, 8, 12, 13, 14, 15, 32, 33, 34, 35, 40, 41, 42, 43))
_RA_WRITE_OPCODES = frozenset((20, 21, 23, 24, 25, 26, 27, 28, 29))
# Opcode-31 extended opcodes that write rA (logical ops, shifts, extends).
_X31_RA_WRITES = frozenset((24, 26, 28, 60, 124, 284, 316, 412, 444, 476, 536, 792, 824, 922, 954))
# Opcode-31 extended opcodes that write no GPR (compares, stores, mtspr, cache ops).
_X31_NO_WRITE = frozenset((0, 32, 4, 54, 86, 144, 150, 151, 183, 215, 247, 278, 407, 439, 467, 470,
                           598, 662, 663, 695, 727, 759, 854, 918, 982, 1014))
_X31_UPDATE_STORES = frozenset((183, 247, 439, 695, 759))
_MTCTR = 0x7C0903A6
_BLR = 0x4E800020
_NOP = 0x60000000


class DolXrefError(RuntimeError):
    pass


@dataclass(frozen=True)
class AddressRef:
    """A ``lis`` plus the low-half instruction that completes the address."""
    lis_site: int
    low_site: int
    register: int
    value: int


@dataclass(frozen=True)
class Compare:
    site: int
    crf: int
    register: int
    value: int
    logical: bool


@dataclass(frozen=True)
class CountedLoop:
    li_site: int
    register: int
    count: int


def _signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def _text_words(dol: bytes, header: dol_map.DolHeader):
    for slot in header.used_slots:
        if slot.is_text:
            for rel in range(0, slot.size - 3, 4):
                yield slot, slot.address + rel, struct.unpack_from('>I', dol, slot.file_offset + rel)[0]


def _gpr_writes(word: int) -> tuple[int, ...] | None:
    """Return the GPRs ``word`` writes; ``None`` means all of r``rD``..r31 (``lmw``)."""
    opcode = word >> 26
    rd, ra = (word >> 21) & 31, (word >> 16) & 31
    if word == _NOP:
        return ()
    if opcode == 46:
        return None
    if opcode in _RD_WRITE_OPCODES:
        return (rd, ra) if opcode in _UPDATE_OPCODES else (rd,)
    if opcode in _RA_WRITE_OPCODES:
        return (ra,)
    if opcode in _UPDATE_OPCODES:
        return (ra,)
    if opcode == 31:
        xo = (word >> 1) & 0x3FF
        if xo in _X31_RA_WRITES:
            return (ra,)
        if xo in _X31_UPDATE_STORES:
            return (ra,)
        if xo in _X31_NO_WRITE:
            return ()
        return (rd,)
    return ()


def find_address_refs(dol: bytes, header: dol_map.DolHeader, low: int, high: int,
                      window: int = REF_WINDOW) -> list[AddressRef]:
    """Find every ``lis`` + low half in code that builds an address in ``[low, high)``.

    Unlike ``dol_map.find_address_constants``, a ``lis`` into a callee-saved
    register (r13-r31) survives calls and unconditional branches, and ``mr``
    copies it. The scan is linear, so a pair must sit in address order.
    """
    found = []
    pending: dict[int, tuple[int, int]] = {}
    current_slot = None
    for slot, site, word in _text_words(dol, header):
        if slot is not current_slot:
            pending.clear()
            current_slot = slot
        opcode = word >> 26
        rd, ra, imm = (word >> 21) & 31, (word >> 16) & 31, word & 0xFFFF
        if opcode == 15 and ra == 0:
            pending[rd] = (imm << 16, site)
            continue
        if opcode == 18 or (opcode == 16 and word & 1):
            for register in _VOLATILE:
                pending.pop(register, None)
            continue
        if opcode == 19:
            if word & 1:
                for register in _VOLATILE:
                    pending.pop(register, None)
            elif (word >> 21) & 0x14 == 0x14:  # unconditional blr/bctr: the function ends
                pending.clear()
            continue
        if opcode == 31 and (word >> 1) & 0x3FF == 444 and rd == (word >> 11) & 31:  # mr ra, rs
            if rd in pending:
                pending[ra] = pending[rd]
            else:
                pending.pop(ra, None)
            continue
        if opcode in _LOW_HALF_OPCODES:
            source, target = (rd, ra) if opcode == 24 else (ra, rd)
            if source in pending and site - pending[source][1] <= window * 4:
                base, lis_site = pending[source]
                value = base | imm if opcode == 24 else (base + _signed16(imm)) & 0xFFFFFFFF
                if low <= value < high:
                    found.append(AddressRef(lis_site, site, target, value))
        writes = _gpr_writes(word)
        if writes is None:
            for register in range(rd, 32):
                pending.pop(register, None)
        else:
            for register in writes:
                pending.pop(register, None)
    return found


def function_start(dol: bytes, header: dol_map.DolHeader, address: int) -> int:
    """Walk back from ``address`` to the ``stwu r1`` prologue or the word after the previous ``blr``.

    A leaf function that follows a tail call (``b``) is merged with the one before it.
    """
    slot = dol_map.slot_at(header, address)
    if slot is None or not slot.is_text:
        raise DolXrefError(f'0x{address:08X} is not in a text section')
    site = address & ~3
    while site > slot.address:
        word = dol_map.read_word(dol, header, site)
        if word >> 16 == 0x9421:
            return site
        previous = dol_map.read_word(dol, header, site - 4)
        if previous == _BLR:
            return site
        site -= 4
    return slot.address


def find_compares(dol: bytes, header: dol_map.DolHeader, values) -> list[Compare]:
    """Find ``cmpwi``/``cmplwi`` against any of ``values``."""
    wanted = set(values)
    found = []
    for _slot, site, word in _text_words(dol, header):
        opcode = word >> 26
        if opcode not in (10, 11):
            continue
        value = word & 0xFFFF if opcode == 10 else _signed16(word & 0xFFFF)
        if value in wanted:
            found.append(Compare(site, (word >> 23) & 7, (word >> 16) & 31, value, opcode == 10))
    return found


def find_counted_loops(dol: bytes, header: dol_map.DolHeader, values) -> list[CountedLoop]:
    """Find ``li rX, value`` followed by ``mtctr rX`` within ``LOOP_WINDOW`` instructions."""
    wanted = set(values)
    found = []
    for slot, site, word in _text_words(dol, header):
        if word >> 26 != 14 or (word >> 16) & 31 != 0 or _signed16(word & 0xFFFF) not in wanted:
            continue
        register = (word >> 21) & 31
        for step in range(1, LOOP_WINDOW + 1):
            follow = site + 4 * step
            if follow + 4 > slot.end:
                break
            next_word = dol_map.read_word(dol, header, follow)
            if next_word == _MTCTR | (register << 21):
                found.append(CountedLoop(site, register, _signed16(word & 0xFFFF)))
                break
            writes = _gpr_writes(next_word)
            if writes is None or register in writes:
                break
    return found


def _compare_at(dol: bytes, header: dol_map.DolHeader, site: int) -> Compare | None:
    slot = dol_map.slot_at(header, site, 4)
    if slot is None or not slot.is_text:
        return None
    word = dol_map.read_word(dol, header, site)
    opcode = word >> 26
    if opcode not in (10, 11):
        return None
    value = word & 0xFFFF if opcode == 10 else _signed16(word & 0xFFFF)
    return Compare(site, (word >> 23) & 7, (word >> 16) & 31, value, opcode == 10)


def classify_compare(dol: bytes, header: dol_map.DolHeader, compare: Compare) -> str:
    """Name the ID range check a bound compare belongs to.

    ``is_player``   ``id >= 0`` then ``id < 0x4D``
    ``is_mii``      ``id >= 0x4D`` then ``id <= 0x64`` (reported for either compare)
    ``other``       anything else: loop bounds, equality tests, unrelated constants
    """
    def neighbours(direction):
        for step in range(1, RANGE_WINDOW + 1):
            other = _compare_at(dol, header, compare.site + direction * 4 * step)
            if other is not None and other.crf == compare.crf and other.register == compare.register:
                yield other

    if compare.value == PLAYER_ID_END:
        if any(other.value == 0 for other in neighbours(-1)):
            return 'is_player'
        if any(other.value == MII_ID_LAST for other in neighbours(1)):
            return 'is_mii'
    if compare.value == MII_ID_LAST and any(other.value == PLAYER_ID_END for other in neighbours(-1)):
        return 'is_mii'
    return 'other'


def check_known_sites(dol: bytes, header: dol_map.DolHeader) -> dict[str, dict[int, bool]]:
    """Confirm the hardcoded ID arithmetic sites still hold their stock instructions."""
    def is_addi(site, value):
        word = dol_map.read_word(dol, header, site)
        return word >> 26 == 14 and (word >> 16) & 31 != 0 and word & 0xFFFF == value

    def is_li(site, value):
        word = dol_map.read_word(dol, header, site)
        return word >> 26 == 14 and (word >> 16) & 31 == 0 and word & 0xFFFF == value

    return {
        'model_dir': {site: is_addi(site, MODEL_DIR_OFFSET) for site in MODEL_DIR_SITES},
        'icon_fallback_row': {ICON_FALLBACK_ROW_SITE: is_li(ICON_FALLBACK_ROW_SITE, ICON_FALLBACK_ROW)},
    }


def table_refs(dol: bytes, header: dol_map.DolHeader, table: IdTable) -> list[AddressRef]:
    return find_address_refs(dol, header, table.address, table.end)


def build_report(dol: bytes, header: dol_map.DolHeader) -> dict:
    tables = {}
    for table in ID_TABLES + (SPECIES_BASE_TABLE,):
        refs = table_refs(dol, header, table)
        functions = sorted({function_start(dol, header, ref.low_site) for ref in refs})
        tables[table.name] = {
            'address': table.address,
            'stride': table.stride,
            'rows': table.rows,
            'region': dol_map.region_of(header, table.address),
            'note': table.note,
            'refs': [asdict(ref) for ref in refs],
            'interior_refs': sum(1 for ref in refs if ref.value != table.address),
            'functions': functions,
        }
    bounds = {}
    for compare in find_compares(dol, header, BOUND_VALUES):
        entry = asdict(compare)
        entry['kind'] = classify_compare(dol, header, compare)
        bounds.setdefault(compare.value, []).append(entry)
    loops = {}
    for loop in find_counted_loops(dol, header, BOUND_VALUES):
        loops.setdefault(loop.count, []).append(asdict(loop))
    return {'tables': tables, 'compares': bounds, 'counted_loops': loops,
            'known_sites': check_known_sites(dol, header)}


def main() -> int:
    parser = argparse.ArgumentParser(description='Cross-reference the character-ID tables and ID bounds in main.dol.')
    parser.add_argument('--dol', default=INPUT_DOL)
    parser.add_argument('--table', help='list every reference site of this table')
    parser.add_argument('--json', help='write the full report to this path')
    args = parser.parse_args()

    with open(args.dol, 'rb') as handle:
        dol = handle.read()
    header = dol_map.parse_header(dol)
    report = build_report(dol, header)

    _slogger.info('per-character tables (lis/low-half pairs):')
    for name, table in report['tables'].items():
        interior = f', {table["interior_refs"]} into the middle' if table['interior_refs'] else ''
        _slogger.info(
            f'  {name:<18} 0x{table["address"]:08X} {table["region"]:<3} {table["rows"]:3d} x 0x{table["stride"]:<2X} '
            f'{len(table["refs"]):3d} refs in {len(table["functions"]):3d} functions{interior}'
        )
    for value in BOUND_VALUES:
        compares = report['compares'].get(value, [])
        kinds: dict[str, int] = {}
        for compare in compares:
            kinds[compare['kind']] = kinds.get(compare['kind'], 0) + 1
        loops = len(report['counted_loops'].get(value, []))
        split = ', '.join(f'{kind} {count}' for kind, count in sorted(kinds.items()))
        _slogger.info(f'compares with 0x{value:X}: {len(compares)} ({split}); counted loops: {loops}')
    for group, sites in report['known_sites'].items():
        for site, ok in sites.items():
            _slogger.info(f'{group} at 0x{site:08X}: {"stock" if ok else "CHANGED"}')

    if args.table:
        table = report['tables'].get(args.table)
        if table is None:
            raise DolXrefError(f'unknown table {args.table!r}')
        for ref in table['refs']:
            _slogger.info(
                f'  lis 0x{ref["lis_site"]:08X}  low 0x{ref["low_site"]:08X}  r{ref["register"]} = 0x{ref["value"]:08X}'
            )
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, indent=1)
        _slogger.info(f'wrote {args.json}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
