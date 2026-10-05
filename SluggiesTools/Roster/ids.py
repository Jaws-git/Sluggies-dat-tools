"""Uncapped character IDs: new IDs 0x66-0xFE that play as a template character.

Config (``ids`` in the roster preset)::

    "ids": [
      {"id": "0x66", "template": "0x00", "wheel": "0x00", "swatch": "blue"}
    ]

* ``id``: optional; the next free ID from 0x66 when left out.
* ``template``: a stock player ID (0x00-0x4C). The new ID copies its row in
  every per-ID table unless ``model`` or ``stats`` names another source.
* ``wheel``: whose colour wheel the new ID joins (default: the template). A
  host without a wheel gets a new wheel group (0x0E and up). ``null``: no
  wheel at all, a square-only character (it must be on a new grid square,
  ``grid``): it keeps the template's row with wheel group 0 and stays off
  the roster's species lists, as the external tool's square characters do.
* ``swatch``: the wheel swatch colour (name or 0-10); default: the template's.
* ``model``: ``{"from": "0xNN"}``, an own model directory holding that stock
  character's files (``model_dirs`` step).
* ``stats``: a stock player ID (0x00-0x4C) whose stats the new ID plays with;
  default: the template.

Which character each row comes from (``_docs/_docs_roster/RosterExpansion.md``,
"Stats, size and voice"): the selector row and the own-data flag from the
template; the ``BODY_TABLES`` rows (size and effect scales) and the model
handles from the model source (the own directory's source, else the
template); every other row, chemistry included, from the stats source. The
voice is the selector row's species: a square's voice (``grid`` step) can
change it for square-only IDs.

Stock characters' stats (``stock_stats``)::

    "stock_stats": [{"id": "0x00", "stats": "0x09"}]

A stock player ID (0x00-0x4C) plays with another stock player's stats: its
rows of the stats tables (as for ``ids[].stats``) become the source's, its
body rows, selector row and own-data flag stay. Chemistry stays symmetric:
the pair (A, B) reads the pair (stats source of A, stats source of B) of the
stock table, in every row (``restat_rows``). Without an ``ids`` key the rows
are rewritten where they are; with one, in the moved tables.

An empty ``ids`` list still moves every table (101 rows, nothing added): the
identity relocation, which must play exactly like vanilla.

What a non-empty list does (plan §2.2 B, the external tool's design):
1. every per-ID table (inventory status ``moved``, plus the model handles)
   moves to the DOL hammerspace data section with 256 rows;
2. hooks: roster list, availability bound, FUN_80071bb0 family path,
   select-screen model task, model resolver and directory map, portrait
   tests and template alias, ID pool, team list test, chemistry (match and
   select screens), charge-effect stack copies.

Toy Field-only features (the slot-machine faces) are skipped; shared code
gets the same patches as on the exhibition draft.
"""

import struct
from dataclasses import dataclass

try:
    from ..Dol import dolfile, inventory, relocate
    from ..Dol.ppc import Asm, one
    from . import dol_hammerspace, steps
except ImportError:
    from Dol import dolfile, inventory, relocate
    from Dol.ppc import Asm, one
    import dol_hammerspace
    import steps

STOCK_IDS = 0x65            # IDs 0x00-0x64; 0x65 is the "no character" sentinel
FIRST_NEW = 0x66
MAX_ID = 0xFE               # 0xFF ends ID lists
ID_BOUND = 0xFF             # the game's ID range checks are widened to this
ROWS = 0x100                # rows of every moved per-ID table
PLAYER_END = 0x4D           # stock player IDs are 0x00-0x4C
WHEEL_MAX = 10              # the roster struct's species lists; the wheels step lifts the game's caps
MODEL_DIR_BASE = 0x12
STATS_ROW, CHEM_BASE, NEUTRAL = 0x8E, 0x28, 1
SWATCHES = {'red': 0, 'blue': 1, 'yellow': 2, 'green': 3, 'purple': 4, 'black': 5, 'brown': 6,
            'lightblue': 7, 'pink': 8, 'white': 9, 'orange': 10}
# Model-handle rows the stock init code (0x80155624) leaves as they are; other templates get (1, 2, 4).
MODEL_REQUESTS = {0x12: (1, 0, 0), 0x21: (1, 1, 1), 0x22: (1, 1, 1), 0x23: (1, 1, 1), 0x24: (1, 1, 1),
                  0x26: (0, 0, 0)}
HANDLE_TABLE = 'model_handles'
# Rows that size the body and its effects: they follow the ID's model. Every other moved table except the selector
# and the own-data flag holds what the character plays like and follows the stats source.
BODY_TABLES = frozenset({'sizescale', 'hitbox', 'icescale', 'pitchchargescale', 'batchargescale',
                         'effectscale_c00', 'effectscale_2b0'})
TEMPLATE_TABLES = frozenset({'selector', 'hasmodel'})
STOCK_STATS_KEY = 'stock_stats'


class IdConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    """``ids[].model``: the ID's own model directory (``model_dirs`` step)."""
    source: int                     # whose files the directory holds (a stock player ID)
    routes: tuple | None = None     # existing DAT routes per file ((offset, length) x 3 languages), or None: copy


@dataclass(frozen=True)
class NewId:
    id: int
    template: int
    wheel: int | None               # None: square-only (no wheel)
    swatch: int | None
    model: ModelSpec | None = None  # None: the template's model directory
    stats: int | None = None        # None: the template's stats

    @property
    def model_source(self) -> int:
        """Whose model the ID shows (its own directory's source, else the template)."""
        return self.model.source if self.model else self.template

    @property
    def stats_source(self) -> int:
        """Whose stats the ID plays with."""
        return self.template if self.stats is None else self.stats

    def row_source(self, table: str) -> int:
        """The stock ID whose row of ``table`` this ID copies."""
        if table in TEMPLATE_TABLES:
            return self.template
        return self.model_source if table in BODY_TABLES else self.stats_source


def id_ranges(values) -> str:
    """``0x47-0x4C, 0x66`` for a set of IDs (log lines)."""
    out, run = [], []
    for v in sorted(set(values)):
        if run and v == run[-1] + 1:
            run.append(v)
            continue
        if run:
            out.append(f'0x{run[0]:02X}' + (f'-0x{run[-1]:02X}' if len(run) > 1 else ''))
        run = [v]
    if run:
        out.append(f'0x{run[0]:02X}' + (f'-0x{run[-1]:02X}' if len(run) > 1 else ''))
    return ', '.join(out)


def _number(value, what: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            pass
    raise IdConfigError(f'{what}: {value!r} is not a number (use e.g. 102 or "0x66")')


def square_member_lists(config: dict) -> list:
    """The raw member lists of ``grid.squares`` (a square is a list of IDs or ``{"members": [...], ...}``)."""
    out = []
    for sq in (config.get('grid') or {}).get('squares') or []:
        out.append(sq.get('members') if isinstance(sq, dict) else sq)
    return out


def parse_ids(config: dict) -> list[NewId]:
    entries = config.get('ids') or []
    used: set[int] = set()
    out: list[NewId] = []
    for n, entry in enumerate(entries):
        where = f'ids[{n}]'
        if 'id' in entry and entry['id'] is not None:
            cid = _number(entry['id'], f'{where}.id')
        else:
            cid = next(i for i in range(FIRST_NEW, MAX_ID + 2) if i not in used)
        if not FIRST_NEW <= cid <= MAX_ID:
            raise IdConfigError(f'{where}: ID 0x{cid:02X} is outside 0x{FIRST_NEW:02X}-0x{MAX_ID:02X}')
        if cid in used:
            raise IdConfigError(f'{where}: ID 0x{cid:02X} is listed twice')
        if 'template' not in entry:
            raise IdConfigError(f'{where}: no template')
        template = _number(entry['template'], f'{where}.template')
        if not 0 <= template < PLAYER_END:
            raise IdConfigError(f'{where}: template 0x{template:02X} is not a stock player ID (0x00-0x4C)')
        if 'wheel' in entry and entry['wheel'] is None:
            wheel = None
            squares = [m for sq in square_member_lists(config) for m in sq or []]
            if cid not in {_number(m, f'grid.squares') for m in squares}:
                raise IdConfigError(f'{where}: 0x{cid:02X} has no wheel ("wheel": null), so it must be on a new '
                                    'grid square (grid.squares)')
        else:
            wheel = _number(entry.get('wheel', template), f'{where}.wheel')
        if wheel is not None and not (0 <= wheel < PLAYER_END or wheel in {i.id for i in out if i.wheel is not None}):
            raise IdConfigError(f'{where}: wheel 0x{wheel:02X} is neither a stock player ID nor an earlier new ID')
        swatch = entry.get('swatch')
        if isinstance(swatch, str) and swatch.lower() in SWATCHES:
            swatch = SWATCHES[swatch.lower()]
        elif swatch is not None:
            swatch = _number(swatch, f'{where}.swatch')
            if not 0 <= swatch <= 10:
                raise IdConfigError(f'{where}: swatch {swatch} is outside 0-10')
        stats = entry.get('stats')
        if stats is not None:
            stats = _number(stats, f'{where}.stats')
            if not 0 <= stats < PLAYER_END:
                raise IdConfigError(f'{where}.stats: 0x{stats:02X} is not a stock player ID (0x00-0x4C)')
        used.add(cid)
        out.append(NewId(cid, template, wheel, swatch, _parse_model(entry.get('model'), f'{where}.model'), stats))
    return sorted(out, key=lambda c: c.id)


def parse_stock_stats(config: dict) -> dict[int, int]:
    """``{stock id: stats source}`` from ``stock_stats``; an entry naming the ID itself drops out."""
    entries = config.get(STOCK_STATS_KEY) or []
    if not isinstance(entries, list):
        raise IdConfigError(f'"{STOCK_STATS_KEY}" must be a list of {{"id", "stats"}} entries')
    out: dict[int, int] = {}
    for n, entry in enumerate(entries):
        where = f'{STOCK_STATS_KEY}[{n}]'
        if not isinstance(entry, dict) or entry.get('id') is None or entry.get('stats') is None:
            raise IdConfigError(f'{where}: expected {{"id": "0xNN", "stats": "0xNN"}}')
        cid = _number(entry['id'], f'{where}.id')
        source = _number(entry['stats'], f'{where}.stats')
        for what, value in (('id', cid), ('stats', source)):
            if not 0 <= value < PLAYER_END:
                raise IdConfigError(f'{where}.{what}: 0x{value:02X} is not a stock player ID (0x00-0x4C)')
        if cid in out:
            raise IdConfigError(f'{where}: 0x{cid:02X} is listed twice')
        if source != cid:
            out[cid] = source
    return out


def _parse_route(value, where: str) -> tuple:
    """``[offset, length]`` (every language) or three of them (en, fr, sp) -> ((o, l),) * 3."""
    try:
        if len(value) == 2 and all(isinstance(v, (int, str)) for v in value):
            route = (_number(value[0], where), _number(value[1], where))
            return (route,) * 3
        if len(value) == 3:
            return tuple((_number(v[0], where), _number(v[1], where)) for v in value)
    except (TypeError, IndexError):
        pass
    raise IdConfigError(f'{where}: a route is [offset, length] or one per language ([[o, l], [o, l], [o, l]])')


def _parse_model(value, where: str) -> ModelSpec | None:
    if value is None:
        return None
    if not isinstance(value, dict) or 'from' not in value:
        raise IdConfigError(f'{where}: expected {{"from": "0xNN"}} (plus "routes" to keep existing copies)')
    source = _number(value['from'], f'{where}.from')
    if not 0 <= source < PLAYER_END:
        raise IdConfigError(f'{where}.from: 0x{source:02X} is not a stock player ID (0x00-0x4C)')
    routes = value.get('routes')
    if routes is not None:
        if not isinstance(routes, list) or not routes:
            raise IdConfigError(f'{where}.routes: expected one route per file')
        routes = tuple(_parse_route(r, f'{where}.routes[{i}]') for i, r in enumerate(routes))
    return ModelSpec(source, routes)


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

def _rows(image: dolfile.DolImage, table: inventory.Table) -> list[bytearray]:
    """The stock rows plus the row after the table (row 0x65, copied as it is)."""
    base = table.address + table.header
    return [bytearray(image.read(base + i * table.row_size, table.row_size)) for i in range(STOCK_IDS + 1)]


def selector_rows(rows: list[bytearray], new: list[NewId]) -> tuple[list[bytearray], list[str]]:
    """Selector (colour-wheel) rows for the new IDs; may give a host row a new wheel group."""
    log = []
    by_id = {}
    next_group = max(r[0] for r in rows[:STOCK_IDS]) + 1

    def row_of(cid):
        return rows[cid] if cid < len(rows) else by_id[cid]
    for c in new:
        if c.wheel is None:                               # square-only: the template's row, no wheel group
            row = bytearray(rows[c.template])
            row[0], row[3], row[6] = 0, 0, 1
            if c.swatch is not None:
                row[7] = c.swatch
            by_id[c.id] = row
            continue
        host = row_of(c.wheel)
        if host[0] == 0:
            host[0] = next_group
            log.append(f'0x{c.wheel:02X} gets wheel group 0x{next_group:02X}')
            next_group += 1
        row = bytearray(rows[c.template])
        row[0:3] = host[0:3]
        species = row[2]
        shown = [r[5] for r in rows[:STOCK_IDS] if r[2] == species and r[6]]
        earlier = [x for x in by_id.values() if x[2] == species]
        row[3] = 0
        row[5] = 1 + max(shown or [-1]) + len(earlier)   # unread by the game (wheel order is ID order)
        row[6] = 1
        if c.swatch is not None:
            row[7] = c.swatch
        by_id[c.id] = row
        members = len([r for r in rows[:STOCK_IDS] if r[2] == species and r[6]]) + len(earlier) + 1
        if members > WHEEL_MAX:
            raise IdConfigError(f'0x{c.id:02X}: the wheel of species 0x{species:02X} would have {members} '
                                f'members; a wheel holds at most {WHEEL_MAX}')
    return [by_id.get(i, bytearray(8)) for i in range(FIRST_NEW, ROWS)], log


def is_stats_table(name: str) -> bool:
    """Whether a moved table holds what a character plays like (``stats`` / ``stock_stats`` sources)."""
    return name not in BODY_TABLES | TEMPLATE_TABLES | {HANDLE_TABLE}


def restat_rows(name: str, rows: list[bytearray], vanilla: list[bytes], stock_stats: dict[int, int],
                new: list[NewId]) -> None:
    """``stock_stats`` on one table's rows (in place): each listed stock ID's row becomes its source's (the
    stats row keeps its own ID in bytes 0-1); in the stats table every row's chemistry towards a listed ID
    becomes the row's own stats source's chemistry towards that ID's source. ``vanilla``: the stock rows."""
    if not stock_stats or not is_stats_table(name):
        return
    for cid, source in stock_stats.items():
        rows[cid][:] = vanilla[source]
        if name == 'stats':
            rows[cid][0:2] = struct.pack('>H', cid)
    if name != 'stats':
        return
    by_id = {c.id: c.stats_source for c in new}
    for r, row in enumerate(rows):
        source_row = stock_stats.get(r, r) if r <= STOCK_IDS else by_id.get(r)
        if source_row is None:
            continue                                   # a row without a character: neutral chemistry
        for b, source in stock_stats.items():
            row[CHEM_BASE + b] = vanilla[source_row][CHEM_BASE + source]


def extended_table(image: dolfile.DolImage, table: inventory.Table, new: list[NewId],
                   rows_out: int, stock_stats: dict[int, int] | None = None) -> tuple[bytes, list[str]]:
    rows = _rows(image, table)
    vanilla = [bytes(r) for r in rows]
    log: list[str] = []
    head = bytearray(image.read(table.address, table.header))
    if rows_out > STOCK_IDS + 1:
        by_id = {c.id: c for c in new}
        if table.name == 'selector':
            added, log = selector_rows(rows, new)
        else:
            added = []
            for cid in range(FIRST_NEW, rows_out):
                c = by_id.get(cid)
                row = bytearray(rows[c.row_source(table.name)]) if c else bytearray(table.row_size)
                if table.name == 'stats':
                    row[0:2] = struct.pack('>H', cid)
                    if not c:   # no character: neutral chemistry
                        row[CHEM_BASE:CHEM_BASE + STOCK_IDS] = bytes([NEUTRAL]) * STOCK_IDS
                added.append(row)
        rows += added
        if table.name == 'stats':
            head[0] = min(rows_out, 0xFF)   # the stock row count; nothing reads it
    restat_rows(table.name, rows, vanilla, stock_stats or {}, new)
    return bytes(head) + b''.join(rows), log


def restat_in_place(image: dolfile.DolImage, stock_stats: dict[int, int]) -> list[str]:
    """``stock_stats`` without an ``ids`` key: the stock tables' rows rewritten where they are."""
    if not stock_stats:
        return []
    for table in moved_tables():
        if not is_stats_table(table.name):
            continue
        rows = _rows(image, table)
        vanilla = [bytes(r) for r in rows]
        restat_rows(table.name, rows, vanilla, stock_stats, [])
        image.write(table.address + table.header, b''.join(rows))
    return [stock_stats_line(stock_stats) + ' (tables in place)']


def stock_stats_line(stock_stats: dict[int, int]) -> str:
    return 'stock stats: ' + ', '.join(f'0x{c:02X} plays with 0x{s:02X}' for c, s in sorted(stock_stats.items()))


def handle_rows(new: list[NewId], rows_out: int) -> bytes:
    """Model handles (bss in the stock DOL, so zeros); new IDs get their model's requests (its own directory's
    source, else the template)."""
    rows = [(0, 0, 0)] * rows_out
    for c in new:
        rows[c.id] = MODEL_REQUESTS.get(c.model_source, (1, 2, 4))
    return b''.join(struct.pack('>III', *r) for r in rows)


def new_by_new_chemistry(stats: bytes, header: int, new: list[NewId]) -> bytes:
    """Chemistry between two new IDs (0x66-0xFF squared): their stats sources' pair."""
    n = ID_BOUND - FIRST_NEW + 1
    table = bytearray([NEUTRAL]) * (n * n)
    source = {c.id: c.stats_source for c in new}
    for a, ta in source.items():
        for b, tb in source.items():
            if a != b:
                table[(a - FIRST_NEW) * n + b - FIRST_NEW] = stats[header + ta * STATS_ROW + CHEM_BASE + tb]
    return bytes(table)


# --------------------------------------------------------------------------
# Hooks
# --------------------------------------------------------------------------

def _stock(group: str, address: int) -> int:
    return inventory.site(group, address).stock


def _branch_to(image: dolfile.DolImage, group: str, site: int, target: int, link: bool = False) -> None:
    image.patch_word(site, _stock(group, site), one(site, lambda a: a.b(target, link)))


def emit_alias(a: Asm, reg: str, template_of: int) -> None:
    """``reg`` = template_of[reg] for 0x66 <= reg <= 0xFF; r12 is saved around it. Clobbers cr0."""
    skip = f'alias_{reg}_{a.pc:x}'
    a.cmplwi(reg, STOCK_IDS).ble(skip)
    a.cmplwi(reg, ID_BOUND).bgt(skip)
    a.stwu('r1', -16, 'r1').stw('r12', 8, 'r1')
    a.load_addr('r12', template_of).lbzx(reg, 'r12', reg)
    a.lwz('r12', 8, 'r1').addi('r1', 'r1', 16)
    a.label(skip)


class HookBuilder:
    def __init__(self, image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace):
        self.image, self.hs = image, hs
        self.log: list[str] = []

    def stub(self, a: Asm) -> int:
        at = self.hs.code.put(a.assemble(), 4)
        if at != a.base:
            raise dolfile.DolError(f'stub built for 0x{a.base:08X} landed at 0x{at:08X}')
        return at

    def new_stub(self) -> Asm:
        self.hs.code.put(b'', 4)
        return Asm(self.hs.code.here)

    def roster_hook(self, new_ids_list: int, selector: int) -> None:
        """End of the roster builder FUN_8006ba6c: append each new ID to its species list (max 10)."""
        site = 0x8006BD58
        a = self.new_stub()
        a.load_addr('r6', new_ids_list)
        a.label('loop')
        a.lbz('r7', 0, 'r6').cmplwi('r7', STOCK_IDS).beq('done')
        a.load_addr('r8', selector).slwi('r9', 'r7', 3).add('r8', 'r8', 'r9')
        a.lbz('r0', 2, 'r8')                                  # species
        a.add('r10', 'r31', 'r0').lbz('r4', 0x4D, 'r10')      # X[0x4D + species] = count
        a.cmplwi('r4', 10).bge('next')
        a.mulli('r5', 'r0', 10).add('r5', 'r31', 'r5').add('r5', 'r5', 'r4')
        a.stb('r7', 0x76, 'r5')                               # X[0x76 + species * 10 + count] = id
        a.addi('r4', 'r4', 1).stb('r4', 0x4D, 'r10')
        a.label('next')
        a.addi('r6', 'r6', 1).b('loop')
        a.label('done')
        a.mr('r3', 'r31').b(site + 4)
        _branch_to(self.image, 'roster_hook', site, self.stub(a))

    def availability_bounds(self) -> None:
        for s in inventory.group('availability_bounds'):
            self.image.patch_word(s.address, s.stock, (s.stock & 0xFFFF0000) | ID_BOUND)

    def select_model_task(self) -> None:
        site = 0x804A508C          # cmpwi cr1,r4,0x65 in SelCharaMdl FUN_804a5018
        a = self.new_stub()
        a.cmpwi('r4', STOCK_IDS, cr=1).beq('reject', cr=1)
        a.cmpwi('r4', ID_BOUND, cr=1).bgt('reject', cr=1)
        a.b(0x804A5094)
        a.label('reject')
        a.b(0x804A5794)
        _branch_to(self.image, 'select_model_task', site, self.stub(a))

    def family_path(self) -> None:
        site = 0x80071BDC          # blt cr1 after cmpwi cr1,r5,0x4d in FUN_80071bb0
        a = self.new_stub()
        a.blt('family', cr=1)
        a.cmpwi('r5', STOCK_IDS, cr=1).ble('none', cr=1)
        a.label('family')
        a.b(0x80071BE8)
        a.label('none')
        a.b(0x80071BE0)
        _branch_to(self.image, 'family_path', site, self.stub(a))

    def model_resolver(self, template_of: int) -> None:
        site = 0x80367078          # mr r3,r4 ; blr  in FUN_80367060
        a = self.new_stub()
        a.mr('r3', 'r4')
        emit_alias(a, 'r3', template_of)
        a.blr()
        _branch_to(self.image, 'model_resolver', site, self.stub(a))

    def model_dirs(self, dirmap: int) -> None:
        for s in inventory.group('model_dir_sites'):
            reg = (s.stock >> 16) & 31
            if s.stock & 0xFFE0FFFF != 0x38800012:
                raise dolfile.DolError(f'0x{s.address:08X}: {s.stock:08X} is not addi r4,rN,0x12')
            a = self.new_stub()
            a.load_addr('r12', dirmap).slwi('r4', f'r{reg}', 1).lhzx('r4', 'r12', 'r4')
            a.b(s.address + 4)
            self.image.patch_word(s.address, s.stock, one(s.address, lambda b, t=self.stub(a): b.b(t)))

    def portrait_tests(self) -> None:
        """Let new IDs pass the inline "normal" test (cmpwi cr1,r0,0x4d; bge cr1,+8; li r3,1)."""
        bges = [s for s in inventory.group('portrait_normal_tests') if s.stock == 0x40840008]
        for s in bges:
            # the test before it must be cmpwi cr1,r0,0x4d (the stub compares r0)
            self.image.patch_word(s.address - 4, 0x2C80004D, 0x2C80004D)
            a = self.new_stub()
            a.blt('normal', cr=1)
            a.cmpwi('r0', STOCK_IDS, cr=1).ble('other', cr=1)
            a.label('normal')
            a.b(s.address + 4)
            a.label('other')
            a.b(s.address + 8)
            self.image.patch_word(s.address, s.stock, one(s.address, lambda b, t=self.stub(a): b.b(t)))

    def template_alias(self, template_of: int, portrait_of: int) -> None:
        """Portrait renderer entry and its two preview calls use ``portrait_of`` (the template's ID unless the
        icons step gives the ID its own art); the name-label rows use the template's ID."""
        site = 0x80395DD0          # mr r24,r4 at FUN_80395db0 entry
        a = self.new_stub()
        a.mr('r24', 'r4')
        emit_alias(a, 'r24', portrait_of)
        a.b(site + 4)
        _branch_to(self.image, 'portrait_renderer', site, self.stub(a))
        for s in inventory.group('portrait_preview_calls'):      # bl FUN_80395db0, r4 = id
            if s.stock != one(s.address, lambda b: b.bl(0x80395DB0)):
                raise dolfile.DolError(f'0x{s.address:08X}: {s.stock:08X} is not bl 0x80395DB0')
            a = self.new_stub()
            emit_alias(a, 'r4', portrait_of)
            a.b(0x80395DB0)
            self.image.patch_word(s.address, s.stock, one(s.address, lambda b, t=self.stub(a): b.bl(t)))
        for s in inventory.group('name_label_rows'):             # addi rD,r4,0x149
            rd = (s.stock >> 21) & 31
            a = self.new_stub()
            a.cmplwi('r4', STOCK_IDS).ble('stock').cmplwi('r4', ID_BOUND).bgt('stock')
            a.stwu('r1', -16, 'r1').stw('r12', 8, 'r1')
            a.load_addr('r12', template_of).lbzx('r12', 'r12', 'r4')
            a.addi(f'r{rd}', 'r12', 0x149)                          # (rA = r12: never the literal-0 form)
            a.lwz('r12', 8, 'r1').addi('r1', 'r1', 16).b(s.address + 4)
            a.label('stock')
            a.word(s.stock).b(s.address + 4)
            self.image.patch_word(s.address, s.stock, one(s.address, lambda b, t=self.stub(a): b.b(t)))

    def id_pool(self) -> None:
        """FUN_80431df0 gathers up to 0x65 IDs into a 0xCA-byte heap buffer: room for 0x100."""
        for s in inventory.group('id_limits'):
            if s.stock == 0x386000CA:          # li r3,0xCA
                self.image.patch_word(s.address, s.stock, 0x38600000 | 2 * (ID_BOUND + 1))
            elif s.stock == 0x2C170065:        # cmpwi r23,0x65
                self.image.patch_word(s.address, s.stock, 0x2C170000 | (ID_BOUND + 1))
            # else: a Toy Field slot-machine face test (Toy Field-only feature: skipped)

    def team_list(self) -> None:
        site, reg = 0x80320468, 'r3'    # bge cr1,+0x1C after cmpwi cr1,r3,0x4d (FUN_80320418)
        a = self.new_stub()
        a.blt('normal', cr=1)
        a.cmpwi(reg, STOCK_IDS, cr=1).ble('other', cr=1)
        a.label('normal')
        a.b(site + 4)
        a.label('other')
        a.b(site + 0x1C)
        _branch_to(self.image, 'id_list_tests', site, self.stub(a))

    def chemistry(self, stats_rows: int, new_chem: int) -> None:
        """Match chemistry FUN_8015c800: replaces add r3,r0,r6 (r0 = A's stats row, r6 = B's ID)."""
        n = ID_BOUND - FIRST_NEW + 1
        site = 0x8015C880
        a = self.new_stub()
        a.cmpwi('r6', STOCK_IDS).bge('new_b')
        a.add('r3', 'r0', 'r6').lbz('r3', CHEM_BASE, 'r3').blr()
        a.label('new_b')
        a.mr('r12', 'r0').lhz('r11', 0, 'r12')                   # A's ID (row bytes 0-1)
        a.cmpwi('r11', STOCK_IDS).bge('both')
        a.load_addr('r12', stats_rows)
        a.mulli('r3', 'r6', STATS_ROW).add('r12', 'r12', 'r3').add('r12', 'r12', 'r11')
        a.lbz('r3', CHEM_BASE, 'r12').blr()                      # B's row, column A
        a.label('both')
        a.addi('r11', 'r11', -FIRST_NEW).cmplwi('r11', n - 1).bgt('unknown')
        a.addi('r12', 'r6', -FIRST_NEW).cmplwi('r12', n - 1).bgt('unknown')
        a.mulli('r11', 'r11', n).add('r11', 'r11', 'r12')
        a.load_addr('r12', new_chem).lbzx('r3', 'r12', 'r11').blr()
        a.label('unknown')
        a.li('r3', NEUTRAL).blr()
        _branch_to(self.image, 'chemistry_hook', site, self.stub(a))

    def select_chemistry(self, stats_rows: int, new_chem: int) -> None:
        """The select/team screens read chemistry from stack copies of the stats rows (10 sites)."""
        n = ID_BOUND - FIRST_NEW + 1
        # (site, stock instruction, A's ID into r11, B's register, answer register)
        sites = [
            (0x80184F78, lambda a: a.lbzx('r0', 'r3', 'r0'), lambda a: a.lhz('r11', -CHEM_BASE, 'r3'), 'r0', 'r0'),
            (0x80185170, lambda a: a.lbzx('r0', 'r3', 'r0'), lambda a: a.lhz('r11', -CHEM_BASE, 'r3'), 'r0', 'r0'),
            (0x80187988, lambda a: a.lbz('r0', CHEM_BASE, 'r3'), lambda a: a.lhz('r11', 0, 'r30'), 'r0', 'r0'),
            (0x801886D4, lambda a: a.lbzx('r0', 'r3', 'r0'), lambda a: a.lhz('r11', -CHEM_BASE, 'r3'), 'r0', 'r0'),
            (0x80188A48, lambda a: a.lbzx('r0', 'r3', 'r0'), lambda a: a.lhz('r11', -CHEM_BASE, 'r3'), 'r0', 'r0'),
            (0x80069B80, lambda a: a.lbzx('r0', 'r3', 'r31'), lambda a: a.lhz('r11', -CHEM_BASE, 'r3'), 'r31', 'r0'),
            (0x80067978, lambda a: a.lbz('r3', CHEM_BASE, 'r26'),
             lambda a: a.subf('r11', 'r25', 'r26').lhz('r11', 0, 'r11'), 'r25', 'r3'),
            (0x80064EA4, lambda a: a.lbzx('r3', 'r3', 'r29'), lambda a: a.lhz('r11', -CHEM_BASE, 'r3'), 'r29', 'r3'),
            (0x80079500, lambda a: a.lbz('r3', CHEM_BASE, 'r25'),
             lambda a: a.subf('r11', 'r28', 'r25').lhz('r11', 0, 'r11'), 'r28', 'r3'),
            (0x8008952C, lambda a: a.lbzx('r3', 'r3', 'r29'), lambda a: a.lhz('r11', -CHEM_BASE, 'r3'), 'r29', 'r3'),
        ]
        for site, stock, a_id, b, out in sites:
            expect = one(site, stock)
            a = self.new_stub()
            a.cmplwi(b, STOCK_IDS - 1).bgt('new')
            stock(a)
            a.blr()
            a.label('new')
            a.stwu('r1', -16, 'r1').stw('r11', 8, 'r1').stw('r12', 12, 'r1')
            a_id(a)
            a.mr('r12', b)
            a.cmplwi('r11', STOCK_IDS - 1).bgt('both')
            a.mulli('r12', 'r12', STATS_ROW).add('r12', 'r12', 'r11')
            a.load_addr('r11', stats_rows).add('r12', 'r12', 'r11')
            a.lbz(out, CHEM_BASE, 'r12').b('done')
            a.label('both')
            a.addi('r11', 'r11', -FIRST_NEW).cmplwi('r11', n - 1).bgt('unknown')
            a.addi('r12', 'r12', -FIRST_NEW).cmplwi('r12', n - 1).bgt('unknown')
            a.mulli('r11', 'r11', n).add('r11', 'r11', 'r12')
            a.load_addr('r12', new_chem).lbzx(out, 'r12', 'r11').b('done')
            a.label('unknown')
            a.li(out, NEUTRAL)
            a.label('done')
            a.lwz('r11', 8, 'r1').lwz('r12', 12, 'r1').addi('r1', 'r1', 16).blr()
            stock_word = _stock('select_chemistry', site)
            if stock_word != expect:
                raise dolfile.DolError(f'select chemistry: 0x{site:08X} is {stock_word:08X}, expected {expect:08X}')
            self.image.patch_word(site, expect, one(site, lambda c, t=self.stub(a): c.bl(t)))
        for addr in (0x80069B50, 0x80069B60):       # FUN_80069b2c: cmpwi r3 / r4, 0x65
            s = inventory.site('select_chemistry', addr)
            self.image.patch_word(addr, s.stock, (s.stock & 0xFFFF0000) | (ID_BOUND + 1))

    def charge_scales(self, at: dict[str, int]) -> None:
        """The charge effects copy their scale table to the stack: point that copy at the moved table."""
        tables = {0x800F3478: ('pitchchargescale', 'r5'), 0x800F2EC0: ('batchargescale', 'r5'),
                  0x800FB1E0: ('effectscale_2b0', 'r3')}
        for s in inventory.group('charge_scale'):
            name, reg = tables[s.address]
            a = self.new_stub()
            a.load_addr(reg, at[name]).blr()
            self.image.patch_word(s.address, s.stock, one(s.address, lambda b, t=self.stub(a): b.bl(t)))


# --------------------------------------------------------------------------
# Step
# --------------------------------------------------------------------------

def moved_tables() -> list[inventory.Table]:
    return [t for t in inventory.tables('moved') if t.name not in ('head_list', 'dtna_directories')]


def apply_ids(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace, new: list[NewId],
              state: dict | None = None, stock_stats: dict[int, int] | None = None) -> list[str]:
    rows_out = ROWS if new else STOCK_IDS + 1
    log = [stock_stats_line(stock_stats)] if stock_stats else []
    refs = relocate.scan_refs(image)
    at: dict[str, int] = {}
    for table in moved_tables():
        if table.name == HANDLE_TABLE:
            blob = handle_rows(new, rows_out)
        else:
            blob, lines = extended_table(image, table, new, rows_out, stock_stats)
            log += lines
        at[table.name] = hs.data.put(blob, 32)
        changes = relocate.relocate_table(image, table.all_pairs, table.address, table.length,
                                          at[table.name], refs)
        log.append(f'{table.name:16} {len(changes):3} words -> 0x{at[table.name]:08X} ({rows_out} rows)')
    if state is not None:
        state['tables'] = {t.name: (at[t.name] + t.header, rows_out) for t in moved_tables()}
    if not new:
        return log + ['identity relocation (no new IDs configured): every table keeps its 101 rows']

    template_of = bytes(range(STOCK_IDS + 1)) + bytes(
        next((c.template for c in new if c.id == i), i) for i in range(FIRST_NEW, ROWS))
    template_of_at = hs.data.put(template_of, 4)
    portrait_of_at = hs.data.put(template_of, 4)     # the icons step sets an ID with own art to itself
    dirmap = [i + MODEL_DIR_BASE for i in range(STOCK_IDS)] + [0] * (ROWS - STOCK_IDS)
    for c in new:
        dirmap[c.id] = c.template + MODEL_DIR_BASE
    dirmap_at = hs.data.put(struct.pack(f'>{ROWS}H', *dirmap), 4)
    # The model resolver aliases a new ID to its template, except an ID with its own model directory: its own ID
    # selects that directory (dirmap, filled by the model_dirs step).
    if any(c.model for c in new):
        model_of = bytearray(template_of)
        for c in new:
            if c.model:
                model_of[c.id] = c.id
        model_of_at = hs.data.put(bytes(model_of), 4)
    else:
        model_of_at = template_of_at
    new_ids_list = hs.data.put(bytes(c.id for c in new if c.wheel is not None) + bytes([STOCK_IDS]), 4)
    stats = inventory.table('stats')
    # the stock table (left where it was): new-by-new chemistry pairs the stats sources' stock rows
    stats_blob = image.read(stats.address, stats.header + (STOCK_IDS + 1) * STATS_ROW)
    new_chem = hs.data.put(new_by_new_chemistry(stats_blob, stats.header, new), 4)
    stats_rows = at['stats'] + stats.header

    hooks = HookBuilder(image, hs)
    hooks.roster_hook(new_ids_list, at['selector'])
    hooks.availability_bounds()
    hooks.select_model_task()
    hooks.family_path()
    hooks.model_resolver(model_of_at)
    hooks.model_dirs(dirmap_at)
    hooks.portrait_tests()
    hooks.template_alias(template_of_at, portrait_of_at)
    if state is not None:
        state['portrait_of'] = portrait_of_at
        state['dirmap'] = dirmap_at
    hooks.id_pool()
    hooks.team_list()
    hooks.chemistry(stats_rows, new_chem)
    hooks.select_chemistry(stats_rows, new_chem)
    hooks.charge_scales(at)
    groups: dict[tuple, list[int]] = {}
    for c in new:
        groups.setdefault((c.template, c.wheel), []).append(c.id)
    log.append(f'{len(new)} new IDs: ' + '; '.join(
        f'{id_ranges(group)} (template 0x{template:02X}, '
        + (f'wheel 0x{wheel:02X})' if wheel is not None else 'square only)')
        for (template, wheel), group in groups.items()))
    return log


@steps.register('ids')
def apply(ctx: steps.RosterContext) -> list[str]:
    stock_stats = parse_stock_stats(ctx.config)
    ctx.state['stock_stats'] = stock_stats
    if 'ids' not in ctx.config:
        return ['no "ids" key in the roster config: tables stay in place'] + restat_in_place(ctx.dol, stock_stats)
    new = parse_ids(ctx.config)
    ctx.state['new_ids'] = new
    hs = dol_hammerspace.get(ctx)
    log = apply_ids(ctx.dol, hs, new, ctx.state, stock_stats)
    hs.commit()
    return log + [f'DOL hammerspace now: {hs.summary()}']
