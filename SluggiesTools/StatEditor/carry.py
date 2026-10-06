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


@dataclass
class Placement:
    """Where ``image``'s roster keeps the editor's tables: its ``layouts`` (``bridge.table_layouts``), the IDs that
    exist (``present``, in roster order) and the new x new chemistry matrix (None without new IDs)."""
    roster: bridge.Roster
    layouts: dict
    present: list[int]
    matrix: int | None


def placement(image: dolfile.DolImage) -> Placement | None:
    """``image``'s ``Placement``; None when it does not map the tables (synthetic test DOLs). Raises
    ``bridge.BridgeError`` for a roster that cannot be read."""
    if not all(table_mapped(image, name) for name in fields.CHARACTER_TABLES):
        return None
    roster = bridge.read_roster(image)
    layouts = bridge.table_layouts(image, roster)
    matrix = bridge.new_by_new_address(image, layouts['stats']) if roster.new else None
    return Placement(roster, layouts, roster_ids(roster), matrix)


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
    place = (placement(image) if all(table_mapped(vanilla, name) for name in fields.CHARACTER_TABLES)
             else None)
    if place is not None:
        roster = place.roster
        baseline = bridge.baseline_rows(vanilla, roster)
        by_table: dict[str, list[fields.Field]] = {}
        for f in fields.CHARACTER_FIELDS:
            by_table.setdefault(f.table, []).append(f)
        for name, layout in place.layouts.items():
            for cid in place.present:
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
            live = image.read(place.matrix, NEW_X_NEW * NEW_X_NEW)
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


@dataclass(frozen=True)
class CharacterEdits:
    """One character's stat edits, for the plan dialogs: its edited fields (``group.name``) and the chemistry values
    that involve it (either direction)."""
    fields: tuple[str, ...]
    chemistry: int

    def text(self, limit: int = 4) -> str:
        """``3 fields (stamina, slap size, charge pitch speed), 2 chemistry values``."""
        parts = []
        if self.fields:
            names = [f.split('.', 1)[1] for f in self.fields]
            shown = ', '.join(names[:limit]) + (f', +{len(names) - limit} more' if len(names) > limit else '')
            parts.append(f'{len(names)} field{"s" if len(names) != 1 else ""} ({shown})')
        if self.chemistry:
            parts.append(f'{self.chemistry} chemistry value{"s" if self.chemistry != 1 else ""}')
        return ', '.join(parts)


def by_character(edits: StatEdits) -> dict[int, CharacterEdits]:
    """``{ID: CharacterEdits}`` for every character with an edited field or an edited chemistry value."""
    chem: dict[int, int] = {}
    for a, b in edits.chemistry:
        for cid in {a, b}:
            chem[cid] = chem.get(cid, 0) + 1
    out = {}
    for cid in sorted(set(edits.rows) | set(chem)):
        names = tuple(f'{f.group}.{f.name}' for f in sorted(edits.rows.get(cid, {}), key=lambda f: (f.table, f.offset)))
        out[cid] = CharacterEdits(names, chem.get(cid, 0))
    return out


def summary(edits: StatEdits) -> dict:
    """The JSON form the roster state carries (``state_cli``): ``{"characters": {"0xNN": {"fields": [...],
    "chemistry": n}}, "globals": n}``."""
    return {'characters': {f'0x{cid:02X}': {'fields': list(e.fields), 'chemistry': e.chemistry}
                           for cid, e in by_character(edits).items()},
            'globals': len(edits.globals)}


def reset_spots(image: dolfile.DolImage, vanilla: dolfile.DolImage, cid: int) -> list[tuple]:
    """Where ``cid``'s stat edits live and what the roster writes there without them: ``(group, key, address,
    baseline bytes, field)`` for every character field (``key`` the field name) and every chemistry byte that
    involves ``cid`` (``group`` 'chemistry', ``key`` the other ID, ``field`` None). Writing them all clears its
    edits; spots that hold the baseline already change nothing. Raises ``bridge.BridgeError``."""
    place = (placement(image) if all(table_mapped(vanilla, name) for name in fields.CHARACTER_TABLES)
             else None)
    if place is None:
        raise bridge.BridgeError('main.dol does not hold the stat tables where Sluggies Tools expects them')
    roster, layouts, present, matrix = place.roster, place.layouts, place.present, place.matrix
    if cid not in present:
        raise bridge.BridgeError(f'0x{cid:02X} is not a character of this roster')
    baseline = bridge.baseline_rows(vanilla, roster)
    out = [(f.group, f.name, layouts[f.table].row_address(cid) + f.offset,
            baseline[f.table][cid][f.offset:f.offset + f.size], f) for f in fields.CHARACTER_FIELDS]
    matrix_base = bridge.new_by_new_baseline(vanilla, roster) if roster.new else None
    stats = layouts['stats']
    seen = set()
    for other in present:
        for a, b in ((cid, other), (other, cid)):
            at = chem_address(a, b, layouts, matrix)
            if at in seen:
                continue
            seen.add(at)
            if a >= ids.FIRST_NEW and b >= ids.FIRST_NEW:
                raw = matrix_base[at - matrix:at - matrix + 1]
            else:
                row = (at - stats.address - stats.header) // stats.row_size
                raw = baseline['stats'][row][at - stats.row_address(row):][:1]
            out.append(('chemistry', f'0x{other:02X}', at, raw, None))
    return out


def apply(image: dolfile.DolImage, edits: StatEdits) -> list[str]:
    """Write ``edits`` onto ``image``'s roster (after the roster steps and the manifest); log lines."""
    if not edits:
        return []
    place = placement(image)
    layouts, present, matrix = (place.layouts, set(place.present), place.matrix) if place else ({}, set(), None)
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
