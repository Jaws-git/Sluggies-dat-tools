"""Gameplay edit and settings files -> ``main.dol``: checked, then written in place.

Items, in staging order (a later one wins per field):

* an **edit file** (format ``sluggies-gameplay-edits`` v1, the GUI's staged
  edits): ``{"format", "version", "values": {table: {row: {column: value}}}}``
  with any subset of the fields (``tables.FIELD_LOOKUP`` keys, never
  addresses);
* a **settings file** (format ``sluggies-gameplay`` v1, ``--save-gameplay``):
  the same layout plus a ``name``, holding every field. A field it lacks (a
  file from an older version) keeps the game's value, with a note;
* an **AI Gameplay Tool file** (``legacy.py``), migrated to the values its
  Gecko codes wrote;
* ``reset``: every field back to ``1_Input/main.dol``'s value, at its place
  in the order.

Unknown keys, values outside their storage (``fields.Value.problem``) and
fields the DOL does not map refuse the whole set. The cross-field checks
(``tables.problems``) run on the result (the game's values with every item
on top): a problem refuses the set when an edit or settings file changes one
of its values; else (the game holds it already, or only a migrated file or a
reset sets it: the user's game ran with those values) it is a warning.

Values the game holds already are counted, not written.
"""

import json
from dataclasses import dataclass, field

try:
    from ..Dol import dolfile
    from ..StatEditor.fields import short
    from . import legacy, read, tables
except ImportError:
    from Dol import dolfile
    from StatEditor.fields import short
    from Gameplay import legacy, read, tables

EDITS_FORMAT = 'sluggies-gameplay-edits'
SETTINGS_FORMAT = 'sluggies-gameplay'
VERSION = 1
RESET_ITEM = 'reset'
EXTENSION = '.sluggiesgameplay'


class EditFileError(ValueError):
    pass


@dataclass(frozen=True)
class Reset:
    """Every field back to ``1_Input/main.dol``'s value."""

    @property
    def item(self) -> str:
        return RESET_ITEM


@dataclass(frozen=True)
class Change:
    field: tables.GlobalField
    raw: bytes
    before: object
    after: object
    origin: str                    # 'edit' (edit or settings file) / 'legacy' / 'reset'


@dataclass
class Prepared:
    changes: list[Change] = field(default_factory=list)
    same: int = 0                                          # values given that the game holds already
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)         # migration notes, fields a settings file lacks
    problems: list[tables.Problem] = field(default_factory=list)       # cross-field problems of the result
    legacy: bool = False                                   # an AI Gameplay Tool file is among the items
    values: dict = field(default_factory=dict)             # {field: (game value, value given)}: every field named


def parse_item(text: str) -> 'Reset | str':
    return Reset() if text.strip().lower() == RESET_ITEM else text


def read_file(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        raise EditFileError(f'could not read the gameplay file {path}: {exc}') from exc
    if not isinstance(doc, dict):
        raise EditFileError(f'{path} is not a gameplay settings file')
    return doc


def _check_header(doc: dict, label: str) -> None:
    if doc.get('format') not in (EDITS_FORMAT, SETTINGS_FORMAT):
        raise EditFileError(f'{label} is not a gameplay settings file (format {doc.get("format")!r})')
    if doc.get('version') != VERSION:
        raise EditFileError(f'{label} has format version {doc.get("version")!r}; this Sluggies Tools reads version '
                            f'{VERSION}: update Sluggies Tools')
    if not isinstance(doc.get('values'), dict):
        raise EditFileError(f'{label}: "values" is not an object')
    if 'name' in doc and not isinstance(doc['name'], str):
        raise EditFileError(f'{label}: "name" is not a text')


def parse_values(values: dict, label: str) -> dict:
    """``{field: value}`` for a file's ``values``; unknown keys and unstorable values raise ``EditFileError``."""
    out = {}
    for table, rows in values.items():
        if table not in tables.TABLES_BY_KEY:
            raise EditFileError(f'{label}: unknown table {table!r} (known: {", ".join(tables.TABLES_BY_KEY)})')
        if not isinstance(rows, dict):
            raise EditFileError(f'{label}, {table}: not an object of rows')
        for row, columns in rows.items():
            if not isinstance(columns, dict):
                raise EditFileError(f'{label}, {table} / {row}: not an object of columns')
            for column, value in columns.items():
                try:
                    f = tables.field(table, row, column)
                except tables.FieldError as exc:
                    raise EditFileError(f'{label}: {exc}') from None
                problem = f.problem(value)
                if problem:
                    raise EditFileError(f'{label}, {tables.label(f)}: {problem}')
                out[f] = value
    return out


def prepare(image: dolfile.DolImage, documents: list, labels: list[str] | None = None,
            vanilla: dolfile.DolImage | None = None) -> Prepared:
    """Check ``documents`` (parsed files and ``Reset`` steps, in staging order) against ``image`` (module
    docstring). ``vanilla`` (``1_Input/main.dol``): needed by a reset and by an AI Gameplay Tool file. Raises
    ``EditFileError``."""
    labels = labels or [f'gameplay file {n}' for n in range(1, len(documents) + 1)]
    needs_vanilla = [label for doc, label in zip(documents, labels) if isinstance(doc, Reset) or legacy.is_legacy(doc)]
    if needs_vanilla and vanilla is None:
        raise EditFileError(f'{needs_vanilla[0]} needs the original main.dol in 1_Input')
    base = read.values(vanilla) if vanilla is not None else {}
    prepared = Prepared()
    planned: dict = {}                                     # field -> (value, origin)
    for doc, label in zip(documents, labels):
        if isinstance(doc, Reset):
            planned.update({f: (v, 'reset') for f, v in base.items() if image.is_mapped(f.address, f.size)})
            continue
        if legacy.is_legacy(doc):
            try:
                migrated = legacy.migrate(doc, base)
            except legacy.LegacyError as exc:
                raise EditFileError(f'{label} ({legacy.TITLE}): {exc}') from None
            prepared.legacy = True
            prepared.notes += [f'{label}: {note}' for note in migrated.notes]
            values, origin = migrated.values, 'legacy'
        else:
            _check_header(doc, label)
            values, origin = parse_values(doc['values'], label), 'edit'
            if doc['format'] == SETTINGS_FORMAT:
                lacking = [f for f in tables.FIELDS if f not in values]
                if lacking:
                    names = sorted({tables.TABLES_BY_KEY[f.table].title for f in lacking})
                    prepared.notes.append(f'{label}: {len(lacking)} value{"s" if len(lacking) != 1 else ""} not in '
                                          f'the file keep the game\'s value ({", ".join(names)})')
        for f, value in values.items():
            if not image.is_mapped(f.address, f.size):
                raise EditFileError(f'{label}, {tables.label(f)}: 0x{f.address:08X} is not in main.dol')
            planned[f] = (value, origin)

    current = read.values(image)
    result = dict(current)
    for f, (value, origin) in planned.items():
        raw = f.pack(value)
        after = f.unpack(raw)
        result[f] = after
        prepared.values[f] = (current[f], after)
        if image.read(f.address, f.size) == raw:
            prepared.same += origin != 'reset'             # a reset names every field: only edited ones count
        else:
            prepared.changes.append(Change(f, raw, current[f], after, origin))
    order = {f: i for i, f in enumerate(tables.FIELDS)}
    prepared.changes.sort(key=lambda c: order[c.field])
    prepared.problems = tables.problems(result)
    edited = {c.field for c in prepared.changes if c.origin == 'edit'}
    errors = [p.message for p in prepared.problems if edited & set(p.fields)]
    if errors:
        raise EditFileError('; '.join(errors))
    prepared.warnings = [p.message for p in prepared.problems]
    return prepared


def write(image: dolfile.DolImage, prepared: Prepared) -> None:
    for change in prepared.changes:
        image.write(change.field.address, change.raw)


def describe(prepared: Prepared) -> list[str]:
    """One line per table: ``Star usage odds: Balanced / Dire 95 -> 100, ...``."""
    lines, current, parts = [], None, []
    for c in prepared.changes:
        t = tables.TABLES_BY_KEY[c.field.table]
        if t.key != current and parts:
            lines.append(f'{tables.TABLES_BY_KEY[current].title}: ' + ', '.join(parts))
            parts = []
        current = t.key
        cell = tables.label(c.field).split(': ', 1)[1]
        parts.append(f'{cell} {short(c.before)} -> {short(c.after)}')
    if parts:
        lines.append(f'{tables.TABLES_BY_KEY[current].title}: ' + ', '.join(parts))
    return lines


def summary(prepared: Prepared) -> str:
    """``12 values in 3 tables``."""
    count = len(prepared.changes)
    n = len({c.field.table for c in prepared.changes})
    return f'{count} value{"s" if count != 1 else ""} in {n} table{"s" if n != 1 else ""}'


def report(prepared: Prepared) -> dict:
    """The dry run as JSON (``cli --report``, for the GUI's load dialog)."""
    return {'changes': [{'table': c.field.table, 'row': c.field.row, 'column': c.field.column,
                         'before': short(c.before), 'after': short(c.after), 'origin': c.origin}
                        for c in prepared.changes],
            'values': [{'table': f.table, 'row': f.row, 'column': f.column, 'before': short(before),
                        'after': short(after)} for f, (before, after) in sorted(
                            prepared.values.items(), key=lambda item: tables.FIELDS.index(item[0]))],
            'same': prepared.same, 'warnings': prepared.warnings, 'notes': prepared.notes,
            'legacy': prepared.legacy, 'summary': summary(prepared)}


def edits_document(values: dict) -> dict:
    """An edit file for ``{field: value}``."""
    return {'format': EDITS_FORMAT, 'version': VERSION, 'values': read.nested(values, short)}


def settings_document(image: dolfile.DolImage, name: str = '') -> dict:
    """A settings file with every field as ``image`` holds it. Raises ``EditFileError`` when ``image`` does not
    map a table."""
    missing = read.missing_tables(image)
    if missing:
        raise EditFileError(f'main.dol does not hold these gameplay tables where Sluggies Tools expects them: '
                            f'{", ".join(missing)}')
    return {'format': SETTINGS_FORMAT, 'version': VERSION, 'name': name,
            'values': read.nested(read.values(image), short)}
