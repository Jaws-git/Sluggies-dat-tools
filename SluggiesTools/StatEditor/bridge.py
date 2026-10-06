"""``stat_bridge.json``: where the stat editor finds the stats of a modded game.

``build(image, vanilla, dat, ...)`` describes ``3_Output_Dat/main.dol`` for
the Sluggers Stat Editor's Bridge Mode (format v1):

* ``files``: the DOL/DAT paths and the DOL's SHA-1 (an edit file made from
  this bridge is valid only for that DOL).
* ``dol_sections``: the DOL header's used slots (address -> file offset).
* ``characters``: every ID that exists, stock ``0x00``-``0x64`` plus the
  roster's new IDs, with the name the game shows, the family (base character
  of its species, for grouped dropdowns), stats and model source, ``kind``.
* ``tables``: the nine per-ID tables the editor edits, where they are now
  (``relocate.pair_address``: a roster run with an ``ids`` key moves them),
  with header, row size, rows and ``moved``.
* ``chemistry``: stock x stock is the stock row's column; stock x new and new
  x stock both read the **new** ID's row, column = the stock ID (one byte,
  symmetric); new x new is a separate directional matrix (row ID -> column ID,
  ``ids.new_by_new_chemistry``), found through the chemistry hook's code.
* ``globals``: the global tables (``fields.GLOBAL_TABLES``); they never move.
* ``baseline``: the rows the roster produces without stat edits, base64 per
  table and ID, for every row that differs from the vanilla row (new IDs:
  always); the new x new matrix as a whole. In Bridge Mode these are the
  editor's defaults; IDs without an entry use its own vanilla defaults.
* ``focus``: an ID to preselect, or None.

The baseline comes from ``1_Input/main.dol`` through the ids step's own row
builders (``ids.extended_table``, ``ids.new_by_new_chemistry``), so it is
exactly what a roster run writes before any stat edit.
"""

import base64
import hashlib
from dataclasses import dataclass

try:
    from ..Dol import dolfile, inventory, relocate
    from ..Dol.ppc import branch_target
    from ..Roster import grid, ids, state, wheels
    from . import fields
except ImportError:
    from Dol import dolfile, inventory, relocate
    from Dol.ppc import branch_target
    from Roster import grid, ids, state, wheels
    from StatEditor import fields

FORMAT = 'sluggies-stat-bridge'
VERSION = 1
CHEMISTRY_HOOK = 0x8015C880          # ids.HookBuilder.chemistry: the match chemistry hook site
HOOK_SCAN_WORDS = 40                 # the stub is 30 words


class BridgeError(ValueError):
    pass


def _hex(value: int, width: int = 2) -> str:
    return f'0x{value:0{width}X}'


@dataclass(frozen=True)
class TableLayout:
    name: str
    address: int
    header: int
    row_size: int
    rows: int
    moved: bool

    def row_address(self, cid: int) -> int:
        if not 0 <= cid < self.rows:
            raise BridgeError(f'{self.name}: no row for 0x{cid:02X} ({self.rows} rows)')
        return self.address + self.header + self.row_size * cid


@dataclass
class Roster:
    """What the roster manifest says about stats: new IDs (with their sources) and ``stock_stats``."""
    manifest: dict | None
    new: list
    stock_stats: dict[int, int]

    @property
    def ids_moved(self) -> bool:
        return bool(self.new)


def read_roster(image: dolfile.DolImage) -> Roster:
    """The roster's new IDs and stats sources from the manifest (``derive`` reads them the same way)."""
    try:
        mf = state._manifest(image)
    except state.StateError as exc:
        raise BridgeError(str(exc)) from exc
    if mf is None:
        return Roster(None, [], {})
    stats_of = {cid: src for cid, src in mf.get('stats') or []}
    own = {cid: source for cid, _directory, source in mf.get('model_dirs') or []}
    new = [ids.NewId(cid, template, wheel, swatch,
                     ids.ModelSpec(own[cid]) if cid in own else None, stats_of.get(cid))
           for cid, template, wheel, swatch in mf.get('ids') or []]
    stock_stats = {cid: src for cid, src in stats_of.items() if cid < ids.PLAYER_END}
    return Roster(mf, sorted(new, key=lambda c: c.id), stock_stats)


def table_layouts(image: dolfile.DolImage, roster: Roster) -> dict[str, TableLayout]:
    """The editor's per-ID tables as they are in ``image``."""
    out = {}
    for name in fields.CHARACTER_TABLES:
        table = inventory.table(name)
        at = relocate.pair_address(image, *table.all_pairs[0])
        moved = at != table.address
        rows = ids.ROWS if moved and roster.new else ids.STOCK_IDS
        out[name] = TableLayout(name, at, table.header, table.row_size, rows, moved)
    return out


def new_by_new_address(image: dolfile.DolImage, stats: TableLayout) -> int:
    """The new x new chemistry matrix of a roster with new IDs: the second address the chemistry hook's stub loads
    into r12 (the first is the stats rows)."""
    word = image.u32(CHEMISTRY_HOOK)
    if word == inventory.site('chemistry_hook', CHEMISTRY_HOOK).stock:
        raise BridgeError('the roster has new IDs, but the chemistry hook is missing: rebuild the roster (menu [7])')
    stub = branch_target(word, CHEMISTRY_HOOK)
    if stub is None:
        raise BridgeError(f'chemistry hook 0x{CHEMISTRY_HOOK:08X}: {word:08X} is neither stock nor a branch')
    loads = []
    words = [image.u32(stub + 4 * k) for k in range(HOOK_SCAN_WORDS) if image.is_mapped(stub + 4 * k, 4)]
    for hi, lo in zip(words, words[1:]):
        if hi >> 16 == 0x3D80 and lo >> 16 == 0x398C:                  # lis r12,hi ; addi r12,r12,lo
            low = lo & 0xFFFF
            loads.append((((hi & 0xFFFF) << 16) + (low - 0x10000 if low & 0x8000 else low)) & 0xFFFFFFFF)
    loads = loads[:2]                     # the scan may run on into the select-screen stubs, which load the same
    if len(loads) != 2 or loads[0] != stats.address + stats.header:
        raise BridgeError(f'chemistry hook stub 0x{stub:08X} does not load the stats rows and one matrix '
                          f'({", ".join(_hex(a, 8) for a in loads) or "no addresses"})')
    return loads[1]


def baseline_rows(vanilla: dolfile.DolImage, roster: Roster) -> dict[str, list[bytes]]:
    """Every editor table's rows as a roster run writes them before any stat edit (character rows only)."""
    rows_out = ids.ROWS if roster.new else ids.STOCK_IDS + 1
    out = {}
    for name in fields.CHARACTER_TABLES:
        table = inventory.table(name)
        blob, _log = ids.extended_table(vanilla, table, roster.new, rows_out, roster.stock_stats)
        body = blob[table.header:]
        count = ids.ROWS if roster.new else ids.STOCK_IDS
        out[name] = [body[i * table.row_size:(i + 1) * table.row_size] for i in range(count)]
    return out


def vanilla_rows(vanilla: dolfile.DolImage, name: str) -> list[bytes]:
    table = inventory.table(name)
    base = table.address + table.header
    return [vanilla.read(base + i * table.row_size, table.row_size) for i in range(ids.STOCK_IDS)]


def new_by_new_baseline(vanilla: dolfile.DolImage, roster: Roster) -> bytes:
    stats = inventory.table('stats')
    blob = vanilla.read(stats.address, stats.header + (ids.STOCK_IDS + 1) * ids.STATS_ROW)
    return ids.new_by_new_chemistry(blob, stats.header, roster.new)


def character_list(image: dolfile.DolImage, dat, roster: Roster) -> list[dict]:
    """Stock IDs 0x00-0x64 and the roster's new IDs, with names, families and sources."""
    text = state.read_names(image, dat) if dat is not None else None
    rows = state.selector_rows(image)
    heads = state.head_list(image, grid.SQUARE_HEADS)
    new = {c.id: c for c in roster.new}
    out = []
    for cid in list(range(ids.STOCK_IDS)) + sorted(new):
        name = text.get(cid) if text is not None else None
        default = (wheels.SPARE_NAMES.get(cid) if name is not None and name.get('en') == wheels.SPARE_TEXT
                   else None)
        species = rows[cid][2]
        c = new.get(cid)
        out.append({
            'id': _hex(cid),
            'name': None if name is None else state.display_name({'id': cid, 'name': name, 'default_name': default}),
            'family': _hex(heads[species] if species < grid.SQUARE_HEADS else cid),
            'stats_source': _hex(c.stats_source if c else roster.stock_stats.get(cid, cid)),
            'model_source': _hex(c.model_source if c else cid),
            'kind': 'new' if c else 'stock',
        })
    return out


def _place(image: dolfile.DolImage, address: int, size: int) -> dict:
    return {'address': _hex(address, 8), 'file_offset': image.offset_of(address, max(size, 1))}


def _globals(image: dolfile.DolImage) -> dict:
    out = {}
    for t in fields.GLOBAL_TABLES:
        if t.key == 'speed':
            out[t.key] = {'count': fields.SPEED_STEPS,
                          'Baserunning': _place(image, fields.SPEED_BASERUNNING, 4 * fields.SPEED_STEPS),
                          'Fielding': _place(image, fields.SPEED_FIELDING, 4 * fields.SPEED_STEPS)}
        elif t.key == 'handicap_params':
            out[t.key] = [[_place(image, a, 1) for a in row] for row in fields.HANDICAP_PARAMS]
        else:
            out[t.key] = dict(_place(image, t.address, t.size), size=t.size)
    return out


def build(image: dolfile.DolImage, vanilla: dolfile.DolImage, dat=None, *, dol_path: str = '',
          dat_path: str = '', focus: int | None = None) -> dict:
    """The bridge for ``image`` (module docstring). ``vanilla``: ``1_Input/main.dol``; ``dat``: anything with
    ``read(offset, size)`` (names; None leaves ``name`` None)."""
    roster = read_roster(image)
    layouts = table_layouts(image, roster)
    characters = character_list(image, dat, roster)
    if focus is not None and _hex(focus) not in {c['id'] for c in characters}:
        raise BridgeError(f'focus 0x{focus:02X} is not a character of this roster')

    tables = {}
    for name, t in layouts.items():
        size = t.header + t.row_size * t.rows
        tables[name] = dict(_place(image, t.address, size), header=t.header, row_size=t.row_size, rows=t.rows,
                            moved=t.moved)
    new_chem = new_by_new_address(image, layouts['stats']) if roster.new else None
    n = ids.ID_BOUND - ids.FIRST_NEW + 1
    chemistry = {'stock_columns': fields.CHEM_COLUMNS, 'stock_x_new': 'new_row',
                 'new_x_new': None if new_chem is None else dict(_place(image, new_chem, n * n),
                                                                 first_id=_hex(ids.FIRST_NEW), size=n)}

    baseline: dict = {}
    rows = baseline_rows(vanilla, roster)
    new_ids = {c.id for c in roster.new}
    for name in fields.CHARACTER_TABLES:
        stock = vanilla_rows(vanilla, name)
        entries = {_hex(cid): base64.b64encode(row).decode('ascii') for cid, row in enumerate(rows[name])
                   if (cid < ids.STOCK_IDS and row != stock[cid]) or cid in new_ids}
        if entries:
            baseline[name] = entries
    if new_chem is not None:
        baseline['new_x_new'] = base64.b64encode(new_by_new_baseline(vanilla, roster)).decode('ascii')

    return {
        'format': FORMAT,
        'version': VERSION,
        'files': {'main_dol': dol_path, 'dt_na_dat': dat_path,
                  'main_dol_sha1': hashlib.sha1(image.data).hexdigest()},
        'focus': None if focus is None else _hex(focus),
        'dol_sections': [{'name': s.name, 'file_offset': s.file_offset, 'address': _hex(s.address, 8),
                          'size': s.size} for s in image.header.used_slots],
        'characters': characters,
        'tables': tables,
        'chemistry': chemistry,
        'globals': _globals(image),
        'baseline': baseline,
    }
