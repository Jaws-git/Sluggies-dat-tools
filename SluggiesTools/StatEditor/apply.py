"""``stat_edits.json`` -> ``main.dol``: write the values the stat editor sent back from Bridge Mode.

An edit file (format ``sluggies-stat-edits`` v1, written by the editor's
**Send to Sluggies**) names values by character ID and field key
(``fields.py``), never by address. ``prepare`` checks and places them:

* **Valid only for the DOL it was made from**: its ``main_dol_sha1``
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

``Reset`` steps (``reset:0xNN``, from the GUI's "clear stat edits") put one
character's fields and chemistry back to what the roster writes without stat
edits (``carry.reset_spots``), at their place in the order: a later file sets
values on top of a reset, a later reset clears what an earlier file set.

``Copy`` steps (``copy:<file>``, from the character grid's paste) write a
snapshot of one character's live values (``slot_plan.plan_copy``, format
``sluggies-stat-copy``) onto another: every field, and its chemistry in both
directions (the target's own pair with the source stays; the source's self
pair becomes the target's). A snapshot names values by field and ID, so it
fits any DOL that has both characters (no hash check); IDs the roster no
longer has are skipped.

Warnings (written anyway): a value outside the editor's own input range
(``fields.limit_problem``; not for the roster's own values a reset writes back), and a character left with both a fielding and a
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
    reset: bool = False             # from a reset or a copy (``Reset`` / ``Copy``): no value the user typed


@dataclass(frozen=True)
class Reset:
    """A step that clears one character's stat edits: every field and every chemistry byte of ``cid`` goes back to
    what the roster writes without stat edits (``carry.reset_spots``). Written ``reset:0xNN`` in a command line."""
    cid: int

    @property
    def item(self) -> str:
        return f'{RESET_PREFIX}{_hex(self.cid)}'


RESET_PREFIX = 'reset:'
COPY_PREFIX = 'copy:'
COPY_FORMAT = 'sluggies-stat-copy'


@dataclass(frozen=True)
class Copy:
    """A step that writes a copy snapshot (``copy:<file>``, module docstring); ``doc`` once it is read."""
    path: str
    doc: dict | None = None

    @property
    def item(self) -> str:
        return f'{COPY_PREFIX}{self.path}'


def parse_item(text: str) -> 'Reset | Copy | str':
    """A command-line item: ``reset:0xNN`` gives a ``Reset``, ``copy:<file>`` a ``Copy``, anything else is an
    edit file's path."""
    if text.lower().startswith(COPY_PREFIX):
        return Copy(text[len(COPY_PREFIX):])
    if not text.lower().startswith(RESET_PREFIX):
        return text
    return Reset(_id(text[len(RESET_PREFIX):], text))


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
        place = carry.placement(image)
        if place is None:
            raise EditFileError('main.dol does not hold the stat tables where Sluggies Tools expects them')
        self.image = image
        self.layouts, self.present, self.matrix = place.layouts, set(place.present), place.matrix

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


def _parse_reset(reset: Reset, placer: _Placer, vanilla: dolfile.DolImage) -> dict[int, Change]:
    placer.character(_hex(reset.cid), reset.item)
    out = {}
    for group, key, at, raw, f in carry.reset_spots(placer.image, vanilla, reset.cid):
        now = placer.image.read(at, len(raw))
        before, after = (f.unpack(now), f.unpack(raw)) if f is not None else (now[0], raw[0])
        out[at] = Change(reset.cid, group, key, at, raw, before, after, f, reset=True)
    return out


def _parse_copy(step: Copy, placer: _Placer, label: str) -> dict[int, Change]:
    doc = step.doc or {}
    if doc.get('format') != COPY_FORMAT:
        raise EditFileError(f'{label} is not a stat copy snapshot (format {doc.get("format")!r})')
    target = placer.character(doc.get('target'), label)
    source = _id(doc.get('source'), label)
    out: dict[int, Change] = {}
    for group, values in (doc.get('fields') or {}).items():
        for name, text in (values or {}).items():
            try:
                f = fields.character_field(group, name)
                raw = bytes.fromhex(text)
            except (fields.FieldError, TypeError, ValueError) as exc:
                raise EditFileError(f'{label}: {group}.{name}: {exc}') from None
            if len(raw) != f.size:
                raise EditFileError(f'{label}: {group}.{name}: {len(raw)} bytes, the field has {f.size}')
            at = placer.layouts[f.table].row_address(target) + f.offset
            out[at] = Change(target, f.group, f.name, at, raw, f.unpack(placer.image.read(at, f.size)), f.unpack(raw),
                             f, reset=True)
    for text, pair in (doc.get('chemistry') or {}).items():
        other = _id(text, label)
        if other == target or other not in placer.present:
            continue                                   # the pair of source and target stays; gone IDs are skipped
        if other == source:
            other = target                             # the source's self pair: the target's own
        if not (isinstance(pair, list) and len(pair) == 2):
            raise EditFileError(f'{label}: chemistry {text}: not a [row, column] pair')
        for a, b, value in ((target, other, pair[0]), (other, target, pair[1])):
            if not isinstance(value, int) or not 0 <= value <= 0xFF:      # the game's own bytes, copied as they are
                raise EditFileError(f'{label}: chemistry {text}: {value!r} is not a byte')
            at = carry.chem_address(a, b, placer.layouts, placer.matrix)
            if at not in out:                          # stock x new is one byte: the target's row value wins
                out[at] = Change(a, 'chemistry', _hex(b), at, bytes([value]), placer.image.read(at, 1)[0], value,
                                 reset=True)
    return out


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
        problem = c.field is not None and not c.reset and fields.limit_problem(c.field, c.after)
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


def prepare(image: dolfile.DolImage, documents: list, labels: list[str] | None = None,
            names: dict | None = None, vanilla: dolfile.DolImage | None = None) -> Prepared:
    """Check ``documents`` (parsed edit files and ``Reset`` steps, in staging order) against ``image`` and place
    every value (module docstring); a later document wins per value, so a reset clears what earlier files set and a
    later file sets values on top of a reset. ``vanilla`` (``1_Input/main.dol``): the baseline a ``Reset`` needs.
    ``names``: ``{ID: name}`` for messages. Raises ``EditFileError`` (``bridge.BridgeError`` for a roster that
    cannot be read)."""
    labels = labels or [f'edit file {n}' for n in range(1, len(documents) + 1)]
    names = names or {}
    sha1 = hashlib.sha1(image.data).hexdigest()
    for doc, label in zip(documents, labels):
        if not isinstance(doc, (Reset, Copy)):
            _check_header(doc, sha1, label)
    if vanilla is None and any(isinstance(doc, Reset) for doc in documents):
        raise EditFileError('clearing stat edits needs the original main.dol in 1_Input')
    placer = _Placer(image)
    planned: dict[int, Change] = {}
    for doc, label in zip(documents, labels):
        try:
            if isinstance(doc, Reset):
                planned.update(_parse_reset(doc, placer, vanilla))
            elif isinstance(doc, Copy):
                planned.update(_parse_copy(doc, placer, label))
            else:
                planned.update(_parse(doc, placer, label))
        except bridge.BridgeError as exc:
            raise EditFileError(f'{label}: {exc}') from exc
    prepared = Prepared(files=sum(not isinstance(doc, (Reset, Copy)) for doc in documents))
    for at, change in sorted(planned.items()):
        if image.read(at, len(change.raw)) == change.raw:
            prepared.same += not change.reset          # a reset names every spot: only the edited ones count
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
