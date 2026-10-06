"""Roster runs keep stat edits: read them before the reset to vanilla, write them onto the new layout.

A roster run rebuilds ``main.dol`` from ``1_Input`` (``Roster/reset.py``), so
every stat value written into the output would be lost. The runner therefore
calls ``detect`` on the DOL before the reset and ``apply`` on the rebuilt one,
as it does for the game options. No stat record is kept anywhere else: the
edits are re-derived from the DOL on every run.

* **Per-character fields** (``fields.CHARACTER_FIELDS``): a field is an edit
  where the live row differs from the row the roster produces without stat
  edits (``bridge.baseline_rows``: the stats sources' vanilla rows). Edits are
  keyed by character ID and field, so they follow the ID into a moved table,
  survive a stats-source change (the other fields take the new source's
  values) and are dropped, with a log line, for IDs the new roster no longer
  has.
* **Chemistry**, keyed ``(row ID, column ID)``: stock x stock is the stock
  row's column, stock x new and new x stock are one byte in the new ID's row
  (stored as ``(new, stock)``), new x new is the separate matrix. A stock ID
  stays stock and a new ID stays new, so an edit always lands in the same kind
  of region it was read from.
* **Global tables** (``fields.GLOBAL_FIELDS``): compared with ``1_Input``.

Tables or code a DOL does not map (synthetic test DOLs) are skipped.
"""

from dataclasses import dataclass, field

try:
    from ..Dol import dolfile, inventory
    from ..Roster import ids
    from . import bridge, fields
except ImportError:
    from Dol import dolfile, inventory
    from Roster import ids
    from StatEditor import bridge, fields

NEW_X_NEW = ids.ID_BOUND - ids.FIRST_NEW + 1


@dataclass
class StatEdits:
    rows: dict[int, dict[fields.Field, bytes]] = field(default_factory=dict)    # ID -> field -> stored bytes
    chemistry: dict[tuple[int, int], int] = field(default_factory=dict)        # (row ID, column ID) -> byte
    globals: dict[fields.GlobalField, bytes] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.rows or self.chemistry or self.globals)


def table_mapped(image: dolfile.DolImage, name: str) -> bool:
    table = inventory.table(name)
    lis_site, low_site = table.all_pairs[0]
    return (image.is_mapped(lis_site, 4) and image.is_mapped(low_site, 4)
            and image.is_mapped(table.address, table.header + table.row_size * ids.STOCK_IDS))


def roster_ids(roster: bridge.Roster) -> list[int]:
    return list(range(ids.STOCK_IDS)) + [c.id for c in roster.new]


def _chem_pairs(cid: int, live: bytes, base: bytes) -> dict[tuple[int, int], int]:
    out = {}
    for k in range(fields.CHEM_COLUMNS):
        at = fields.chemistry_offset(k)
        if live[at] != base[at]:
            out[(cid, k)] = live[at]
    return out


def detect(image: dolfile.DolImage, vanilla: dolfile.DolImage) -> StatEdits:
    """The stat edits in ``image`` (module docstring). Raises ``bridge.BridgeError`` for a DOL whose roster
    cannot be read."""
    edits = StatEdits()
    if all(table_mapped(d, name) for d in (image, vanilla) for name in fields.CHARACTER_TABLES):
        roster = bridge.read_roster(image)
        layouts = bridge.table_layouts(image, roster)
        baseline = bridge.baseline_rows(vanilla, roster)
        by_table: dict[str, list[fields.Field]] = {}
        for f in fields.CHARACTER_FIELDS:
            by_table.setdefault(f.table, []).append(f)
        for name, layout in layouts.items():
            for cid in roster_ids(roster):
                live = image.read(layout.row_address(cid), layout.row_size)
                base = baseline[name][cid]
                if live == base:
                    continue
                for f in by_table.get(name, []):
                    if live[f.offset:f.offset + f.size] != base[f.offset:f.offset + f.size]:
                        edits.rows.setdefault(cid, {})[f] = live[f.offset:f.offset + f.size]
                if name == 'stats':
                    edits.chemistry.update(_chem_pairs(cid, live, base))
        if roster.new:
            at = bridge.new_by_new_address(image, layouts['stats'])
            live = image.read(at, NEW_X_NEW * NEW_X_NEW)
            base = bridge.new_by_new_baseline(vanilla, roster)
            for a in roster.new:
                for b in roster.new:
                    i = (a.id - ids.FIRST_NEW) * NEW_X_NEW + b.id - ids.FIRST_NEW
                    if live[i] != base[i]:
                        edits.chemistry[(a.id, b.id)] = live[i]
    for g in fields.GLOBAL_FIELDS:
        if image.is_mapped(g.address, g.size) and vanilla.is_mapped(g.address, g.size):
            raw = image.read(g.address, g.size)
            if raw != vanilla.read(g.address, g.size):
                edits.globals[g] = raw
    return edits


def chem_address(a: int, b: int, layouts, matrix: int | None) -> int:
    stats = layouts['stats']
    if a < ids.STOCK_IDS:
        if b < ids.STOCK_IDS:
            return stats.row_address(a) + fields.chemistry_offset(b)
        a, b = b, a                                    # stock x new: the new ID's row, symmetric
    if b < ids.STOCK_IDS:
        return stats.row_address(a) + fields.chemistry_offset(b)
    return matrix + (a - ids.FIRST_NEW) * NEW_X_NEW + b - ids.FIRST_NEW


def apply(image: dolfile.DolImage, edits: StatEdits) -> list[str]:
    """Write ``edits`` onto ``image``'s roster (after the roster steps and the manifest); log lines."""
    if not edits:
        return []
    layouts, present = {}, set()
    if all(table_mapped(image, name) for name in fields.CHARACTER_TABLES):
        roster = bridge.read_roster(image)
        layouts = bridge.table_layouts(image, roster)
        present = set(roster_ids(roster))
    matrix = bridge.new_by_new_address(image, layouts['stats']) if layouts and roster.new else None
    fields_written, dropped = 0, set()
    for cid, values in sorted(edits.rows.items()):
        if cid not in present:
            dropped.add(cid)
            continue
        for f, raw in values.items():
            image.write(layouts[f.table].row_address(cid) + f.offset, raw)
            fields_written += 1
    chem_written, seen = 0, {}
    for (a, b), value in sorted(edits.chemistry.items()):
        gone = {a, b} - present
        if gone:
            dropped |= gone
            continue
        at = chem_address(a, b, layouts, matrix)
        if at in seen and seen[at] != value:          # never from detect: it reads a stock x new pair once
            raise bridge.BridgeError(f'chemistry 0x{a:02X} x 0x{b:02X}: both directions are one byte in the game, '
                                     f'but they differ ({seen[at]} / {value})')
        seen[at] = value
        image.write(at, bytes([value]))
        chem_written += 1
    global_written = 0
    for g, raw in edits.globals.items():
        if image.is_mapped(g.address, g.size):
            image.write(g.address, raw)
            global_written += 1
    out = []
    kept = sorted(cid for cid in edits.rows if cid in present)
    if fields_written or chem_written or global_written:
        out.append(f'carried: {fields_written} fields' + (f' on {ids.id_ranges(kept)}' if kept else '')
                   + f', {chem_written} chemistry values, {global_written} global values')
    if dropped:
        out.append(f'dropped (IDs no longer in the roster): {ids.id_ranges(dropped)}')
    return out
