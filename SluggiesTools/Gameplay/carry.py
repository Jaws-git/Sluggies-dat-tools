"""Roster runs keep the gameplay settings: read them before the reset to vanilla, write them back afterwards.

A roster run rebuilds ``main.dol`` from ``1_Input`` (``Roster/reset.py``),
which resets every gameplay table. The runner therefore calls ``detect`` on
the DOL before the reset and ``apply`` on the rebuilt one, as for the game
options and stat edits. The tables never move (``tables.py``), so an edit is
simply the field's bytes where they differ from ``1_Input``.

``--roster --remove`` keeps them too; only the GUI's "Reset gameplay
settings" (``apply.Reset``) clears them. Star boosts are carried here, for
both tools that write them (the DOL does not record which one did), so the
stat edit carry (``StatEditor/carry.py``) skips them.
"""

try:
    from ..Dol import dolfile
    from . import tables
except ImportError:
    from Dol import dolfile
    from Gameplay import tables


def detect(image: dolfile.DolImage, vanilla: dolfile.DolImage) -> dict:
    """``{field: stored bytes}`` for every field that differs from ``vanilla`` (fields either DOL does not map are
    skipped)."""
    out = {}
    for f in tables.FIELDS:
        if image.is_mapped(f.address, f.size) and vanilla.is_mapped(f.address, f.size):
            raw = image.read(f.address, f.size)
            if raw != vanilla.read(f.address, f.size):
                out[f] = raw
    return out


def dropped(image: dolfile.DolImage, vanilla: dolfile.DolImage) -> list[str]:
    """A line when ``image`` holds edits of the tables that moved to the custom CPU level (``tables.MOVED``): the
    reset puts them back to ``1_Input`` and nothing carries them."""
    count = sum(a != b for at, n in tables.MOVED_RANGES if image.is_mapped(at, n) and vanilla.is_mapped(at, n)
                for a, b in zip(image.read(at, n), vanilla.read(at, n)))
    if not count:
        return []
    return [f'not carried: {count} value{"s" if count != 1 else ""} of the star usage, charge odds, star swing '
            'modifier and preferred pitch tables are back to 1_Input (they are custom CPU level settings now, on '
            'the Custom CPU tab)']


def apply(image: dolfile.DolImage, edits: dict) -> list[str]:
    """Write ``edits`` (``detect``) onto ``image``; log lines."""
    written = [f for f, raw in edits.items() if image.is_mapped(f.address, f.size)]
    for f in written:
        image.write(f.address, edits[f])
    if not written:
        return []
    titles = sorted({tables.TABLES_BY_KEY[f.table].title for f in written})
    return [f'carried: {len(written)} value{"s" if len(written) != 1 else ""} ({", ".join(titles)})']
