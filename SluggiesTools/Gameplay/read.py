"""Every gameplay field's value as a ``main.dol`` holds it.

The GUI's "current" values come from ``3_Output_Dat/main.dol``, "vanilla" (the
reset target, the changed marks, the carry baseline) from ``1_Input/main.dol``.
A DOL whose sections do not map a table gives no values for it
(``missing_tables``).
"""

try:
    from ..Dol import dolfile
    from . import tables
except ImportError:
    from Dol import dolfile
    from Gameplay import tables


def value(image: dolfile.DolImage, f: tables.GlobalField):
    return f.unpack(image.read(f.address, f.size))


def values(image: dolfile.DolImage) -> dict:
    """``{field: value}`` for every field ``image`` maps."""
    return {f: value(image, f) for f in tables.FIELDS if image.is_mapped(f.address, f.size)}


def missing_tables(image: dolfile.DolImage) -> list[str]:
    """The keys of the tables ``image`` does not map in full."""
    return [t.key for t in tables.TABLES
            if not all(image.is_mapped(f.address, f.size) for f in tables.BY_TABLE[t.key])]


def nested(values_: dict, short=None) -> dict:
    """``{table: {row: {column: value}}}`` (the files' form) for ``{field: value}``; ``short`` formats values."""
    out: dict = {}
    for f in tables.FIELDS:
        if f in values_:
            v = values_[f]
            out.setdefault(f.table, {}).setdefault(f.row, {})[f.column] = short(v) if short else v
    return out
