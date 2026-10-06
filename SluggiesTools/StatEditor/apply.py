"""``stat_edits.json`` -> ``main.dol``: write the values the stat editor sent back from Bridge Mode.

An edit file (format ``sluggies-stat-edits`` v1, written by the editor's
**Send to Sluggies**) names values by character ID and field key
(``fields.py``), never by address. ``prepare`` checks and places them:

* **Valid only for the DOL it was made from** (D6): its ``main_dol_sha1``
  must equal the current ``main.dol``'s. Several files (several editor
  sessions on the same game, staged one after the other) are all checked
  against that one DOL before anything is written; a later file wins where
  two set the same value.
* Characters must exist in the roster (stock ``0x00``-``0x64`` plus its new
  IDs), fields and global keys must be known, and every value must fit its
  storage (``fields.Value.problem``; chemistry 0-2). Anything else refuses
  the whole set.
* **Chemistry** lands in one of three regions (``carry.chem_address``): the
  stock row's column, the new ID's row for a stock x new pair (one byte for
  both directions: a file giving both with different values is refused),
  or the new x new matrix.
* Values the game holds already are counted, not written.

Warnings (written anyway): a value outside the editor's own input range
(``fields.limit_problem``), and a character left with both a fielding and a
baserunning ability (the editor: "giving a character both will crash").

``write`` applies a ``Prepared`` set to the image; ``describe`` gives the
dry-run lines per character and table.
"""

import hashlib
import json
import struct
from dataclasses import dataclass, field

try:
    from ..Dol import dolfile
    from . import bridge, carry, fields
except ImportError:
    from Dol import dolfile
    from StatEditor import bridge, carry, fields

FORMAT = 'sluggies-stat-edits'
VERSION = 1
GROUPS = fields.CHARACTER_GROUPS + ('chemistry',)


class EditFileError(ValueError):
    pass


@dataclass(frozen=True)
class Change:
    who: int | None                 # the character the file names (chemistry: the row ID); None: a global table
    group: str                      # stats / pitching / size / chemistry, or the global table's key
    key: str                        # the field key (chemistry: the column ID; globals: "row / column")
    address: int
    raw: bytes                      # the bytes to store
    before: object
    after: object
    field: fields.Field | None = None


@dataclass
class Prepared:
    changes: list[Change] = field(default_factory=list)
    same: int = 0                   # values the files give that the game holds already
    warnings: list[str] = field(default_factory=list)
    files: int = 0

    @property
    def characters(self) -> list[int]:
        return sorted({c.who for c in self.changes if c.who is not None})

    @property
    def global_values(self) -> int:
        return sum(1 for c in self.changes if c.who is None)


def read_file(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        raise EditFileError(f'could not read the stat edit file {path}: {exc}') from exc
    if not isinstance(doc, dict):
        raise EditFileError(f'{path} is not a stat edit file')
    return doc


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


def _id(text, where: str) -> int:
    if not isinstance(text, str):
        raise EditFileError(f'{where}: {text!r} is not a character ID (e.g. "0x66")')
    try:
        value = int(text, 16) if text.lower().startswith('0x') else None
    except ValueError:
        value = None
    if value is None or not 0 <= value <= 0xFF:
        raise EditFileError(f'{where}: {text!r} is not a character ID (e.g. "0x66")')
    return value


def _check_header(doc: dict, sha1: str, label: str) -> None:
    if doc.get('format') != FORMAT:
        raise EditFileError(f'{label} is not a stat edit file (format {doc.get("format")!r})')
    if doc.get('version') != VERSION:
        raise EditFileError(f'{label} has format version {doc.get("version")!r}; this Sluggies Tools reads '
                            f'version {VERSION}: update Sluggies Tools or the stat editor')
    if doc.get('main_dol_sha1') != sha1:
        raise EditFileError(f'{label} was made for another state of main.dol (the game was patched while the stat '
                            'editor was open): open the stat editor again and redo the edits')
    for key, kind in (('characters', dict), ('globals', dict)):
        if not isinstance(doc.get(key, {}), kind):
            raise EditFileError(f'{label}: "{key}" is not an object')


def _value(spec: fields.Value, value, where: str):
    problem = spec.problem(value)
    if problem:
        raise EditFileError(f'{where}: {problem}')
    return value


def short(value):
    """``value`` as the editor shows it: an f32 as the shortest decimal that stores back to the same bytes."""
    if not isinstance(value, float):
        return value
    raw = struct.pack('>f', value)
    for digits in range(1, 10):
        text = f'{value:.{digits}g}'
        if struct.pack('>f', float(text)) == raw:
            number = float(text)
            return int(number) if number.is_integer() and abs(number) < 1e15 else number
    return value


class _Placer:
    """Turns one image's roster into addresses for edit-file keys."""

    def __init__(self, image: dolfile.DolImage):
        if not all(carry.table_mapped(image, name) for name in fields.CHARACTER_TABLES):
            raise EditFileError('main.dol does not hold the stat tables where Sluggies Tools expects them')
        self.image = image
        roster = bridge.read_roster(image)
        self.layouts = bridge.table_layouts(image, roster)
        self.present = set(carry.roster_ids(roster))
        self.matrix = bridge.new_by_new_address(image, self.layouts['stats']) if roster.new else None

    def character(self, text: str, where: str) -> int:
        cid = _id(text, where)
        if cid not in self.present:
            raise EditFileError(f'{where}: {_hex(cid)} is not a character of this roster')
        return cid

    def field_change(self, cid: int, f: fields.Field, value) -> Change:
        at = self.layouts[f.table].row_address(cid) + f.offset
        raw = f.pack(value)
        return Change(cid, f.group, f.name, at, raw, f.unpack(self.image.read(at, f.size)), f.unpack(raw), f)

    def chemistry_change(self, a: int, b: int, value) -> Change:
        at = carry.chem_address(a, b, self.layouts, self.matrix)
        return Change(a, 'chemistry', _hex(b), at, bytes([value]), self.image.read(at, 1)[0], value)

    def global_change(self, g: fields.GlobalField, value) -> Change:
        if not self.image.is_mapped(g.address, g.size):
            raise EditFileError(f'{g.table} {g.row} / {g.column}: 0x{g.address:08X} is not in main.dol')
        raw = g.pack(value)
        return Change(None, g.table, f'{g.row} / {g.column}', g.address, raw,
                      g.unpack(self.image.read(g.address, g.size)), g.unpack(raw))


def _parse(doc: dict, placer: _Placer, label: str) -> dict[int, Change]:
    out: dict[int, Change] = {}

    def put(change: Change, where: str):
        earlier = out.get(change.address)
        if earlier is not None and earlier.raw != change.raw:
            raise EditFileError(f'{where}: chemistry {_hex(earlier.who)} x {earlier.key} and {_hex(change.who)} x '
                                f'{change.key} are one byte in the game, but the file gives two values '
                                f'({earlier.after} / {change.after})')
        out[change.address] = change

    for text, groups in (doc.get('characters') or {}).items():
        where = f'{label}, {text}'
        cid = placer.character(text, where)
        if not isinstance(groups, dict):
            raise EditFileError(f'{where}: not an object of groups')
        for group, values in groups.items():
            if group not in GROUPS:
                raise EditFileError(f'{where}: unknown group {group!r} (known: {", ".join(GROUPS)})')
            if not isinstance(values, dict):
                raise EditFileError(f'{where}, {group}: not an object of values')
            for key, value in values.items():
                at = f'{where}, {group}.{key}'
                if group == 'chemistry':
                    other = placer.character(key, at)
                    put(placer.chemistry_change(cid, other, _value(fields.CHEMISTRY, value, at)), at)
                    continue
                try:
                    f = fields.character_field(group, key)
                except fields.FieldError as exc:
                    raise EditFileError(f'{where}: {exc}') from None
                put(placer.field_change(cid, f, _value(f, value, at)), at)

    for table, rows in (doc.get('globals') or {}).items():
        where = f'{label}, globals.{table}'
        if table not in fields.GLOBALS_BY_KEY:
            raise EditFileError(f'{where}: unknown global table (known: {", ".join(fields.GLOBALS_BY_KEY)})')
        if not isinstance(rows, dict):
            raise EditFileError(f'{where}: not an object of rows')
        for row, columns in rows.items():
            if not isinstance(columns, dict):
                raise EditFileError(f'{where}[{row!r}]: not an object of columns')
            for column, value in columns.items():
                try:
                    g = fields.global_field(table, row, column)
                except fields.FieldError as exc:
                    raise EditFileError(f'{label}: {exc}') from None
                at = f'{where}[{row!r}][{column!r}]'
                put(placer.global_change(g, _value(g, value, at)), at)
    return out


def _warnings(image: dolfile.DolImage, placer: _Placer, changes: list[Change], names: dict) -> list[str]:
    out = []
    for c in changes:
        problem = c.field is not None and fields.limit_problem(c.field, c.after)
        if problem:
            out.append(f'{_who(c.who, names)}: {c.group}.{c.key}: {problem}')
    by_key = {(c.who, c.group, c.key): c for c in changes}
    for cid in sorted({c.who for c in changes if c.group == 'stats' and c.key in fields.CRASH_PAIR}):
        values = []
        for name in fields.CRASH_PAIR:
            c = by_key.get((cid, 'stats', name))
            if c is None:
                f = fields.character_field('stats', name)
                c = placer.field_change(cid, f, f.unpack(image.read(placer.layouts[f.table].row_address(cid)
                                                                    + f.offset, f.size)))
            values.append(c.after)
        if all(values):
            out.append(f'{_who(cid, names)}: fielding ability {values[0]} and baserunning ability {values[1]}: the '
                       'stat editor warns that giving a character both will crash the game')
    return out


def _who(cid: int | None, names: dict) -> str:
    if cid is None:
        return 'global'
    name = names.get(cid)
    return f'{name} ({_hex(cid)})' if name else _hex(cid)


def prepare(image: dolfile.DolImage, documents: list[dict], labels: list[str] | None = None,
            names: dict | None = None) -> Prepared:
    """Check ``documents`` (parsed edit files, in staging order) against ``image`` and place every value (module
    docstring). ``names``: ``{ID: name}`` for messages. Raises ``EditFileError`` (``bridge.BridgeError`` for a
    roster that cannot be read)."""
    labels = labels or [f'edit file {n}' for n in range(1, len(documents) + 1)]
    names = names or {}
    sha1 = hashlib.sha1(image.data).hexdigest()
    for doc, label in zip(documents, labels):
        _check_header(doc, sha1, label)
    placer = _Placer(image)
    planned: dict[int, Change] = {}
    for doc, label in zip(documents, labels):
        planned.update(_parse(doc, placer, label))     # a later file wins
    prepared = Prepared(files=len(documents))
    for at, change in sorted(planned.items()):
        if image.read(at, len(change.raw)) == change.raw:
            prepared.same += 1
        else:
            prepared.changes.append(change)
    prepared.changes.sort(key=lambda c: (c.who is None, c.who or 0, c.group, c.address))
    prepared.warnings = _warnings(image, placer, prepared.changes, names)
    return prepared


def write(image: dolfile.DolImage, prepared: Prepared) -> None:
    for change in prepared.changes:
        image.write(change.address, change.raw)


def describe(prepared: Prepared, names: dict | None = None) -> list[str]:
    """One line per character (or global table) and group: ``Mario (0x00): stats: batting arm 7 -> 9, ...``."""
    names = names or {}
    lines, current, parts = [], None, []
    for c in prepared.changes:
        key = (c.who, c.group)
        if key != current and parts:
            lines.append(f'{_who(current[0], names)}: {current[1]}: ' + ', '.join(parts))
            parts = []
        current = key
        label = _who(int(c.key, 16), names) if c.group == 'chemistry' else c.key
        parts.append(f'{label} {short(c.before)} -> {short(c.after)}')
    if parts:
        lines.append(f'{_who(current[0], names)}: {current[1]}: ' + ', '.join(parts))
    return lines


def summary(prepared: Prepared) -> str:
    """``12 values on 3 characters, 2 global values`` (what a pending line shows)."""
    count = len(prepared.changes)
    chars = len(prepared.characters)
    text = f'{count} value{"s" if count != 1 else ""}'
    if chars:
        text += f' on {chars} character{"s" if chars != 1 else ""}'
    if prepared.global_values:
        text += f' ({prepared.global_values} global)'
    return text
