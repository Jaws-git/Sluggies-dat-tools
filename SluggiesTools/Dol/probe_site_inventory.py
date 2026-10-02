"""Hand-run probe: build ``site_inventory.json`` from the clean ``main.dol`` and the external tool.

Not part of the test suite: it reads ``1_Input/main.dol`` and imports the
Sluggers Characters Beta tool's scripts (``_Sluggers Characters Beta External
Tool/``, local-only, read-only). Run it after changing the curated site lists
below, then review the JSON diff::

    python SluggiesTools/Dol/probe_site_inventory.py [--check-only]

What it records, per site: the address and the stock word read from our
clean DOL. Where the external tool asserts a stock word, the probe checks it
against ours (P12 gate). Where the tool writes a plain constant (not a branch
into its own code), the tool's word is kept as ``tool`` for comparison with
our ports.

Groups come from two sources:

* *traced*: the tool's own patch routines (``wheel7.apply``,
  ``charbuild.id_limit_sites``, ``select_chemistry.apply``,
  ``char_names.patch_code``, ``charge_scale.apply`` and
  ``gridcells.add_grid_square`` in its 11x4 stock-heads mode) run on a copy of
  the clean DOL that records the first value of every word they write;
* *curated*: the steps inside ``charbuild._build`` that can't run on their
  own, transcribed with the stock words the tool asserts.

Per-ID tables carry the xref pairs from our scanner (``dol_xrefs``), checked
against the tool's ``hilo_pairs.tsv``, plus pairs neither scan finds
(``extra_pairs``).
"""

import argparse
import hashlib
import json
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
for _path in (_TOOLS_DIR, os.path.join(_TOOLS_DIR, 'Icons')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import dol_map  # noqa: E402
import dol_xrefs  # noqa: E402

EXTERNAL_SCRIPTS = os.path.join(ROOT, '_Sluggers Characters Beta External Tool',
                                'Sluggers Characters Beta 2.0', 'scripts')
INPUT_DOL = os.path.join(ROOT, '1_Input', 'main.dol')
OUTPUT_JSON = os.path.join(_HERE, 'site_inventory.json')
CLEAN_DOL_SHA1 = '08236f3164360f08075459f1f7a76b16e25fe734'
TOOL_CODE_START = 0x807B7000
STOCK_ROWS = 0x65

# (name, address, row size, header bytes, note). The tool's TABLES plus the
# tables it moves in other steps and P9's three it doesn't move.
PER_ID_TABLES = [
    ('selector', 0x80631550, 8, 0, 'colour-wheel rows'),
    ('stats', 0x806CE9A0, 0x8E, 8, 'stats rows behind an 8-byte header; chemistry at +0x28'),
    ('pitchwindup', 0x80628400, 12, 0, ''),
    ('starpitch', 0x806288BC, 1, 0, ''),
    ('stamina', 0x80628924, 2, 0, ''),
    ('changeup', 0x80628EB0, 8, 0, ''),
    ('traj', 0x8062A15C, 2, 0, ''),
    ('catchrange', 0x8062A688, 40, 0, ''),
    ('hitbox', 0x8062BA08, 8, 0, ''),
    ('sizescale', 0x8062EB50, 8, 0, 'model scale; winner scene reads it through an mr copy'),
    ('icescale', 0x806250E8, 4, 0, 'freeze ice block scale'),
    ('hasmodel', 0x806B4970, 1, 0, 'own character data flag; 0 substitutes ID 4'),
    ('charfloats', 0x806291D8, 0x24, 0, ''),
    ('perid2', 0x8062A00C, 2, 0, ''),
    ('perid6', 0x8062A424, 6, 0, ''),
    ('perid4', 0x8062B874, 4, 0, ''),
    ('throwfloats', 0x806289F0, 0xC, 0, ''),
    ('perid5a', 0x8062A228, 5, 0, ''),
    ('throwvariant', 0x8062B678, 5, 0, 'unextended, new IDs never release the ball'),
    ('pitchchargescale', 0x80624F30, 4, 0, 'read from a stack copy (charge_scale sites)'),
    ('batchargescale', 0x80624D98, 4, 0, 'read from a stack copy (charge_scale sites)'),
    ('effectscale_c00', 0x80624C00, 4, 0, ''),
    ('effectscale_2b0', 0x806252B0, 4, 0, 'read from a stack copy (charge_scale sites)'),
]
# Moved by other tool steps (not TABLES): (name, address, row size, rows, note)
OTHER_TABLES = [
    ('model_handles', 0x80709408, 0xC, STOCK_ROWS, 'bss; per-model-id runtime array'),
    ('head_list', 0x80631878, 1, 0x2B, 'species/head -> base character ID'),
    ('dtna_directories', 0x806A0728, 4, 172, 'dt_na.dat directory pointer table'),
]
# Per-ID tables P9 found that the tool leaves in place.
UNMOVED_TABLES = [
    ('random_pool', 0x806314E8, 1, STOCK_ROWS, 'copy of hasmodel; read only by a < 0x4D loop at 0x8046CCA4'),
    ('flags_80630A28', 0x80630A28, 1, STOCK_ROWS, ''),
    ('model_load', 0x806B49D8, 8, STOCK_ROWS, 'read by the model loader 0x8036629C'),
]
# Pairs a lis/low scan can't see (the high half is spilled to the stack).
# (table, lis site, low site): the winner scene FUN_80152dd4.
EXTRA_PAIRS = [
    ('model_handles', 0x80152E4C, 0x80152EA4),
]

ARENA_LO_PAIRS = [(0x80595FC4, 0x80595FC8), (0x80596014, 0x80596018),
                  (0x8059606C, 0x80596070), (0x805960A0, 0x805960A4)]

# charbuild._build steps that can't be traced on their own: (group, [(address, tool's stock word, note)])
CURATED = {
    'roster_hook': [(0x8006BD58, 0x7FE3FB78, 'mr r3,r31 at the end of the roster builder FUN_8006ba6c')],
    'availability_bounds': [
        (0x804302CC, 0x2C800064, 'cmpwi r0,0x64 (Mii range end) in FUN_80430184'),
        (0x804304C0, 0x2C800064, 'cmpwi r0,0x64 in FUN_80430184'),
        (0x80071B90, 0x2C040064, 'cmpwi r4,0x64 in FUN_80071abc')],
    'select_model_task': [(0x804A508C, 0x2C840065, 'cmpwi cr1,r4,0x65 in SelCharaMdl FUN_804a5018')],
    'chemistry_hook': [(0x8015C880, 0x7C603214, 'add r3,r0,r6 in FUN_8015c800')],
    'family_path': [(0x80071BDC, 0x4184000C, 'blt cr1 after cmpwi cr1,r5,0x4d in FUN_80071bb0')],
    'portrait_normal_tests': [
        (site + off, word, f'{"bge cr1,+8" if off == 4 else "li r3,1"} of the normal test at 0x{site:08X}')
        for site in (0x8006E640, 0x8007F4DC, 0x8042BBB4, 0x8006463C, 0x80088F5C, 0x8031DBFC)
        for off, word in ((4, 0x40840008), (8, 0x38600001))],
    'select_voice_test': [(0x804A55D4 + 4, 0x40840008, 'bge cr1,+8 of the select-voice normal test (optional)')],
    'id_list_tests': [(0x80320468, 0x4084001C, 'bge cr1,+0x1C (id in r3) in FUN_80320418')],
    'name_label_rows': [
        (0x8006E660, 0x38040149, 'addi r0,r4,0x149'), (0x8007F4FC, 0x38040149, 'addi r0,r4,0x149'),
        (0x8042BBD8, 0x38640149, 'addi r3,r4,0x149'), (0x8031DC1C, 0x38040149, 'addi r0,r4,0x149')],
    'portrait_preview_calls': [(0x80064664, None, 'bl FUN_80395db0, r4 = id'),
                               (0x80088F84, None, 'bl FUN_80395db0, r4 = id')],
    'portrait_renderer': [(0x80395DD0, 0x7C982378, 'mr r24,r4 at FUN_80395db0 entry'),
                          (0x80395E1C, 0x4080008C, 'bge 0x80395EA8 after cmpwi r24,0x4d')],
    'model_resolver': [(0x80367078, 0x7C832378, 'mr r3,r4 in FUN_80367060 (own-data alias)')],
    'model_dir_sites': [
        (0x8036625C, 0x38800012 | (26 << 16), 'addi r4,r26,0x12'),
        (0x803664E8, 0x38800012 | (4 << 16), 'addi r4,r4,0x12'),
        (0x80376480, 0x38800012 | (25 << 16), 'addi r4,r25,0x12'),
        (0x804A519C, None, 'addi r4,rN,0x12'),
        (0x804A5224, None, 'addi r4,rN,0x12')],
    'winner_scene_pairs': [
        (0x80152E4C, 0x3C008071, 'lis r0,0x8071 (model handles, spilled to 0x8C(r1))'),
        (0x80152EA4, 0x38849408, 'subi r4,r4,0x6BF8 -> 0x80709408'),
        (0x80152E6C, 0x3C008063, 'lis r0,0x8063 (sizescale, mr r4,r0)'),
        (0x80152EB4, 0x3884EB50, 'subi r4,r4,0x14B0 -> 0x8062EB50')],
}


class TraceDol:
    """The tool's ``Dol`` interface over our bytes, recording each word's first value."""

    def __init__(self, data: bytes):
        self.data = bytearray(data)
        h = self.data
        self.offs = list(struct.unpack('>18I', h[0x00:0x48]))
        self.addrs = list(struct.unpack('>18I', h[0x48:0x90]))
        self.sizes = list(struct.unpack('>18I', h[0x90:0xD8]))
        self.trace = None
        self.first: dict[int, int] = {}

    def _loc(self, addr, n=1):
        for o, a, s in zip(self.offs, self.addrs, self.sizes):
            if s and a <= addr and addr + n <= a + s:
                return o + addr - a
        raise KeyError(f'0x{addr:08X} (+{n}) not in any DOL section')

    def read(self, addr, n):
        o = self._loc(addr, n)
        return bytes(self.data[o:o + n])

    def u32(self, addr):
        return struct.unpack('>I', self.read(addr, 4))[0]

    def write(self, addr, blob):
        for a in range(addr & ~3, addr + len(blob), 4):
            if a not in self.first:
                self.first[a] = self.u32(a)
        o = self._loc(addr, len(blob))
        self.data[o:o + len(blob)] = blob

    def w32(self, addr, value):
        self.write(addr, struct.pack('>I', value & 0xFFFFFFFF))

    def changes(self) -> list[tuple[int, int, int]]:
        return sorted((a, old, self.u32(a)) for a, old in self.first.items() if self.u32(a) != old)


class _Space:
    def __init__(self, base, limit=0x81000000):
        self.base, self.limit, self.blob = base, limit, bytearray()

    @property
    def here(self):
        return self.base + len(self.blob)

    def put(self, data, align=32):
        self.blob += b'\0' * (-len(self.blob) % align)
        addr = self.here
        self.blob += data
        return addr


def _is_tool_branch(word: int, site: int) -> bool:
    if word >> 26 != 18:
        return False
    off = word & 0x03FFFFFC
    if off & 0x02000000:
        off -= 0x04000000
    return site + off >= TOOL_CODE_START


def _site(address: int, stock: int, tool: int | None = None, note: str = '') -> dict:
    entry = {'address': f'0x{address:08X}', 'stock': f'0x{stock:08X}'}
    if tool is not None and not _is_tool_branch(tool, address):
        entry['tool'] = f'0x{tool:08X}'
    if note:
        entry['note'] = note
    return entry


def _traced(clean: bytes, run) -> list[dict]:
    dol = TraceDol(clean)
    run(dol)
    return [_site(a, old, new) for a, old, new in dol.changes()]


def _tool_modules():
    if EXTERNAL_SCRIPTS not in sys.path:
        sys.path.insert(0, EXTERNAL_SCRIPTS)
    sys.dont_write_bytecode = True
    import charbuild, char_names, charge_scale, gridcells, hilo_refs, select_chemistry, wheel7  # noqa: E401
    return charbuild, char_names, charge_scale, gridcells, hilo_refs, select_chemistry, wheel7


def build_inventory(clean: bytes) -> tuple[dict, list[str]]:
    charbuild, char_names, charge_scale, gridcells, hilo_refs, select_chemistry, wheel7 = _tool_modules()
    header = dol_map.parse_header(clean)
    problems: list[str] = []
    word = lambda a: dol_map.read_word(clean, header, a)  # noqa: E731

    tool_pairs = list(hilo_refs.pairs())
    tables = []
    rows = ([(n, a, s, h, STOCK_ROWS, note, 'moved') for n, a, s, h, note in PER_ID_TABLES]
            + [(n, a, s, 0, r, note, 'moved') for n, a, s, r, note in OTHER_TABLES]
            + [(n, a, s, 0, r, note, 'unmoved') for n, a, s, r, note in UNMOVED_TABLES])
    for name, address, size, head, count, note, status in rows:
        end = address + head + size * count
        ours = {(r.lis_site, r.low_site) for r in dol_xrefs.find_address_refs(clean, header, address, end)}
        theirs = {(p[0], p[1]) for p in tool_pairs if address <= p[3] < end}
        extra = [(l, u) for t, l, u in EXTRA_PAIRS if t == name]
        for lis, low in sorted(theirs - ours):
            problems.append(f'{name}: tool pair 0x{lis:08X}/0x{low:08X} not found by dol_xrefs')
        entry = {'name': name, 'address': f'0x{address:08X}', 'row_size': size, 'header': head,
                 'rows': count, 'status': status,
                 'pairs': [[f'0x{l:08X}', f'0x{u:08X}'] for l, u in sorted(ours | theirs)],
                 'only_ours': [[f'0x{l:08X}', f'0x{u:08X}'] for l, u in sorted(ours - theirs)],
                 'extra_pairs': [[f'0x{l:08X}', f'0x{u:08X}'] for l, u in extra]}
        if note:
            entry['note'] = note
        tables.append(entry)

    groups: dict[str, list[dict]] = {}
    for group, sites in CURATED.items():
        out = []
        for address, expect, note in sites:
            got = word(address)
            if expect is not None and got != expect:
                problems.append(f'{group}: 0x{address:08X} holds {got:08X}, the tool expects {expect:08X}')
            out.append(_site(address, got, note=note))
        groups[group] = out
    groups['arena_lo'] = [_site(a, word(a)) for pair in ARENA_LO_PAIRS for a in pair]

    groups['id_limits'] = _traced(clean, charbuild.id_limit_sites)
    groups['wheel7'] = _traced(clean, lambda d: wheel7.apply(d, _Space(TOOL_CODE_START)))
    groups['select_chemistry'] = _traced(clean, lambda d: select_chemistry.apply(
        d, _Space(TOOL_CODE_START), 0x80800000, 0x66, 0x9A, 0x80810000, 0xFF))
    groups['char_names'] = _traced(clean, lambda d: char_names.patch_code(d, _Space(TOOL_CODE_START), 0xFE))
    at = {n: 0x80800000 + 0x1000 * i for i, (n, *_r) in enumerate(PER_ID_TABLES)}
    groups['charge_scale'] = _traced(clean, lambda d: charge_scale.apply(d, _Space(TOOL_CODE_START), at))

    def grid(d):
        stock_heads = list(clean[dol_map.vaddr_to_file(header, gridcells.HEAD_LIST):][:41])
        stock_map = list(clean[dol_map.vaddr_to_file(header, gridcells.STOCK_MAP):][:40])
        layout = [stock_heads[h] for h in stock_map] + [stock_heads[gridcells.LUIGI_HEAD]]
        gridcells.add_grid_square(d, _Space(TOOL_CODE_START), _Space(0x80800000), (), [], layout, True)
    try:
        groups['gridcells_11x4'] = _traced(clean, grid)
    except Exception as exc:  # the trace is a reference only: record why it is missing
        problems.append(f'gridcells trace failed: {exc!r}')

    inventory = {
        'game': 'RMBE01',
        'dol_sha1': CLEAN_DOL_SHA1,
        'source': 'SluggiesTools/Dol/probe_site_inventory.py (clean main.dol + Sluggers Characters Beta 2.0)',
        'tool_code_start': f'0x{TOOL_CODE_START:08X}',
        'tables': tables,
        'groups': groups,
    }
    return inventory, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dol', default=INPUT_DOL)
    parser.add_argument('--check-only', action='store_true', help='report problems, do not write the JSON')
    args = parser.parse_args()
    clean = open(args.dol, 'rb').read()
    sha1 = hashlib.sha1(clean).hexdigest()
    if sha1 != CLEAN_DOL_SHA1:
        print(f'{args.dol}: sha1 {sha1} is not the clean RMBE01 main.dol ({CLEAN_DOL_SHA1})')
        return 1
    inventory, problems = build_inventory(clean)
    counts = {g: len(s) for g, s in inventory['groups'].items()}
    print(f"{len(inventory['tables'])} tables, {sum(counts.values())} sites: {counts}")
    for p in problems:
        print('PROBLEM:', p)
    if not args.check_only:
        with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(inventory, f, indent=1)
            f.write('\n')
        print(f'wrote {OUTPUT_JSON}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
