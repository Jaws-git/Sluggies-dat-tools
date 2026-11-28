"""What the GUI's Chemistry tab shows (no Dear PyGui): pairs, marks, mismatches, drafts and the item writer.

A ``Snapshot`` holds the roster's characters and three layers of chemistry,
each ``{(a, b): (a towards b, b towards a)}`` with ``a < b`` (``read.pairs``),
all read from the game files:

* ``current``: ``3_Output_Dat/main.dol`` (without it the tab shows
  ``1_Input`` read-only, ``readonly``);
* ``vanilla``: what the roster holds without stat edits (``read.baseline``
  from ``1_Input/main.dol``; for a stock game that is 1_Input's chemistry);
* ``pending``: ``current`` with the pending stat items written on top
  (``StatEditor/apply.prepare`` in staging order: Stat Editor files, clears,
  pastes' snapshots and chemistry items), so the tab shows what Patch Game
  will write. Pending pastes (``pastes``: ``(target, source)``) come last,
  whatever their place in the order: the chain writes a paste's stat values
  after every other stat item, and they bring the source's chemistry as the
  game holds it now.

On top of them the tab keeps **drafts**: ``{(a, b): value}`` clicked but not
staged. Chemistry is edited as one value per unordered pair; a pair whose two
directions differ in the game (``mismatches``) has no single value until an
edit gives it one. A pair's marks: ``DRAFT``, ``PENDING`` (Patch Game will
change it), ``CHANGED`` (differs from the unedited roster), ``MISMATCH``.
"""

import json
import os
from dataclasses import dataclass, field

try:
    from ..Dol import dolfile
    from ..Gameplay.view import load_image
    from ..Roster import datfile
    from ..StatEditor import apply, bridge, carry
    from . import read
except ImportError:
    from Dol import dolfile
    from Gameplay.view import load_image
    from Roster import datfile
    from StatEditor import apply, bridge, carry
    from Chemistry import read

DRAFT, PENDING, CHANGED, MISMATCH = 'draft', 'pending', 'changed', 'mismatch'
ITEM_STEM = 'chemistry'
NEUTRAL_PAIR = (read.NEUTRAL, read.NEUTRAL)


@dataclass
class Snapshot:
    characters: list = field(default_factory=list)       # read.Character, in ID order
    current: dict = field(default_factory=dict)
    vanilla: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)
    readonly: bool = True
    source: str = ''                                      # what the values come from (the tab's status line)
    error: str | None = None                              # the pending items could not be read / checked
    warnings: list = field(default_factory=list)          # of the pending items' check

    @property
    def usable(self) -> bool:
        return bool(self.characters)

    @property
    def ids(self) -> list[int]:
        return [c.id for c in self.characters]

    def character(self, cid: int):
        if not hasattr(self, '_by_id') or len(self._by_id) != len(self.characters):
            self._by_id = {c.id: c for c in self.characters}
        return self._by_id.get(cid)

    def label(self, cid: int) -> str:
        c = self.character(cid)
        return c.label if c is not None else f'0x{cid:02X}'


def key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _dat(path: str | None):
    try:
        return datfile.DatFile(path) if path and os.path.isfile(path) else None
    except OSError:
        return None


def _layers(image: dolfile.DolImage, dat_path: str | None):
    """``(placement, characters, pairs)`` of ``image``; ``(None, [], {})`` when it does not map the stat tables."""
    place = carry.placement(image)
    if place is None:
        return None, [], {}
    try:
        people = read.characters(image, _dat(dat_path), place)
    except (datfile.DatFileError, OSError, ValueError, KeyError, IndexError):
        people = read.characters(image, None, place)     # a DAT that does not hold the names where expected
    return place, people, read.matrix(image, place)


def apply_pastes(pending: dict, current: dict, pastes) -> None:
    """Write the pending pastes onto ``pending``: the target gets its source's chemistry towards every other
    character, both directions (``StatEditor/apply``'s ``Copy``: the pair of target and source stays)."""
    people = sorted({cid for pair in current for cid in pair})
    for target, source in pastes:
        if target == source or not any(target in pair for pair in current):
            continue
        for other in people:
            if other in (target, source) or key(source, other) not in current:
                continue
            ab, ba = current[key(source, other)]
            towards, back = (ab, ba) if source < other else (ba, ab)       # source towards other, other towards source
            pending[key(target, other)] = (towards, back) if target < other else (back, towards)


def snapshot(output_dol: str, input_dol: str, items: list[str] = (), output_dat: str | None = None,
             input_dat: str | None = None, pastes=()) -> Snapshot:
    """The layers for the tab (module docstring). ``items``: the pending ``--apply-stat-edits`` items in staging
    order; ``pastes``: the pending pastes as ``(target, source)``. The DAT paths give the names (optional)."""
    out, vanilla = load_image(output_dol), load_image(input_dol)
    snap = Snapshot()
    shown, dat_path = (out, output_dat) if out is not None else (vanilla, input_dat)
    if shown is None:
        snap.source = 'No main.dol in 3_Output_Dat or 1_Input: nothing to show.'
        return snap
    try:
        place, snap.characters, snap.current = _layers(shown, dat_path)
    except (bridge.BridgeError, dolfile.DolError) as exc:
        snap.source = f'The roster of main.dol cannot be read: {exc}'
        return snap
    if place is None:
        snap.source = 'main.dol does not hold the stat tables where Sluggies Tools expects them.'
        return snap
    snap.pending = dict(snap.current)
    snap.vanilla = dict(snap.current)
    if vanilla is not None:
        try:
            snap.vanilla = read.pairs(read.baseline(vanilla, place))
        except (bridge.BridgeError, dolfile.DolError, ValueError, IndexError):
            pass                                          # a 1_Input DOL that is not the stock game: no "changed" marks
    if out is None:
        snap.source = '3_Output_Dat/main.dol is missing: showing 1_Input/main.dol read-only. Run the export first.'
        return snap
    snap.readonly = False
    snap.source = 'Chemistry as 3_Output_Dat/main.dol holds it.'
    if items:
        try:
            names = {c.id: c.label for c in snap.characters}
            documents = [apply.load_item(item) for item in items]
            staged = dolfile.DolImage(out.to_bytes())
            prepared = apply.prepare(out, documents, [_item_label(i) for i in items], names, vanilla)
            apply.write(staged, prepared)
            snap.pending = read.matrix(staged, place)
            snap.warnings = list(prepared.warnings)
        except (apply.EditFileError, bridge.BridgeError, OSError, ValueError) as exc:
            snap.error = f'The pending stat and chemistry edits cannot be read: {exc}'
    apply_pastes(snap.pending, snap.current, pastes)
    return snap


def _item_label(item: str) -> str:
    parsed = apply.parse_item(item)
    if isinstance(parsed, apply.Reset):
        return f'clearing the stat edits of 0x{parsed.cid:02X}'
    if isinstance(parsed, apply.Chemistry):
        return f'the chemistry edits ({os.path.basename(parsed.path)})'
    if isinstance(parsed, apply.Copy):
        return f'the pasted stat values ({os.path.basename(parsed.path)})'
    return os.path.basename(parsed)


# -- values ------------------------------------------------------------------------------------------------------------

def base(snap: Snapshot, pair: tuple[int, int]) -> tuple[int, int]:
    """What the pair shows without a draft: ``(a towards b, b towards a)``."""
    return snap.pending.get(pair, snap.current.get(pair, NEUTRAL_PAIR))


def shown(snap: Snapshot, drafts: dict, pair: tuple[int, int]) -> tuple[int, int]:
    if pair in drafts:
        return (drafts[pair], drafts[pair])
    return base(snap, pair)


def value(snap: Snapshot, drafts: dict, a: int, b: int) -> int | None:
    """The pair's one value as the tab shows it; None while its two directions differ."""
    both = shown(snap, drafts, key(a, b))
    return both[0] if both[0] == both[1] else None


def set_draft(snap: Snapshot, drafts: dict, a: int, b: int, new: int) -> None:
    """A clicked value: a draft unless the pair shows it already without one. Self chemistry is not edited."""
    pair = key(a, b)
    if a == b or pair not in snap.current or new not in read.VALUES:
        return
    if base(snap, pair) == (new, new):
        drafts.pop(pair, None)
    else:
        drafts[pair] = new


def prune_drafts(snap: Snapshot, drafts: dict) -> None:
    """Drop drafts the game or the pending edits hold already, and those of IDs the roster no longer has."""
    for pair in [p for p, v in drafts.items() if p not in snap.current or base(snap, p) == (v, v)]:
        del drafts[pair]


def marks(snap: Snapshot, drafts: dict) -> dict[tuple[int, int], str]:
    """``{pair: mark}`` for every pair with one (the strongest wins: draft, mismatch, pending, changed)."""
    out = {}
    for pair, now in snap.current.items():
        if pair in drafts:
            out[pair] = DRAFT
            continue
        later = snap.pending.get(pair, now)
        if read.mismatch(later):
            out[pair] = MISMATCH
        elif later != now:
            out[pair] = PENDING
        elif later != snap.vanilla.get(pair, later):
            out[pair] = CHANGED
    return out


def mismatches(layer: dict) -> list[tuple[int, int, int, int]]:
    """``(a, b, a towards b, b towards a)`` for every pair of ``layer`` whose two directions differ."""
    return [(a, b, ab, ba) for (a, b), (ab, ba) in sorted(layer.items()) if ab != ba]


def connections(snap: Snapshot, drafts: dict, hidden=()) -> dict[tuple[int, int], int | None]:
    """``{pair: value}`` of every pair that is not neutral as shown (None: a mismatch), without the pairs of the
    ``hidden`` IDs."""
    hidden = set(hidden)
    out = {}
    for pair in snap.current:
        if pair[0] in hidden or pair[1] in hidden:
            continue
        both = shown(snap, drafts, pair)
        if both != NEUTRAL_PAIR:
            out[pair] = both[0] if both[0] == both[1] else None
    return out


def counts(found: dict) -> tuple[int, int, int]:
    """``(good, bad, mismatched)`` pairs of ``connections``' result."""
    values = list(found.values())
    return values.count(read.GOOD), values.count(read.BAD), values.count(None)


def partners(snap: Snapshot, drafts: dict, cid: int) -> dict:
    """``{other ID: value}`` of ``cid`` towards every other character (None: a mismatch), in ID order."""
    return {other: value(snap, drafts, cid, other) for other in snap.ids if other != cid}


def tooltip(snap: Snapshot, drafts: dict, a: int, b: int) -> str:
    """``unedited: bad / game: good / pending: neutral / edited, not staged: good`` (only the layers that differ)."""
    pair = key(a, b)

    def text(both):
        if both[0] == both[1]:
            return read.value_name(both[0])
        first, second = (a, b) if a < b else (b, a)
        return (f'{snap.label(first)} towards {snap.label(second)} {read.value_name(both[0])}, '
                f'{snap.label(second)} towards {snap.label(first)} {read.value_name(both[1])} (one-way)')
    now = snap.current.get(pair, NEUTRAL_PAIR)
    parts = [f'unedited: {text(snap.vanilla.get(pair, now))}']
    if now != snap.vanilla.get(pair, now):
        parts.append(f'game: {text(now)}')
    if base(snap, pair) != now:
        parts.append(f'pending: {text(base(snap, pair))}')
    if pair in drafts:
        parts.append(f'edited, not staged: {read.value_name(drafts[pair])}')
    return '  /  '.join(parts)


# -- staging -----------------------------------------------------------------------------------------------------------

def write_item(folder: str, drafts: dict) -> str:
    """Write ``drafts`` as a new ``sluggies-chemistry`` file into ``folder`` (never into an earlier one: every Stage
    is its own item, at its own place in the staging order); its path."""
    os.makedirs(folder, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(folder, f'{ITEM_STEM}_{n}.json')):
        n += 1
    path = os.path.join(folder, f'{ITEM_STEM}_{n}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(apply.chemistry_document(drafts), f, ensure_ascii=False, indent=1)
    return path


def item_of(path: str) -> str:
    """The ``--apply-stat-edits`` item of a chemistry file."""
    return apply.CHEMISTRY_PREFIX + path


def is_item(item: str | None) -> bool:
    return bool(item) and item.lower().startswith(apply.CHEMISTRY_PREFIX)


def item_path(item: str) -> str:
    return item[len(apply.CHEMISTRY_PREFIX):]


def prune_staging(folder: str, keep) -> None:
    """Delete staged chemistry files no pending edit uses any more."""
    keep = {os.path.normcase(os.path.abspath(p)) for p in keep}
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        if (name.startswith(ITEM_STEM + '_') and name.endswith('.json') and os.path.isfile(path)
                and os.path.normcase(os.path.abspath(path)) not in keep):
            try:
                os.remove(path)
            except OSError:
                pass


# -- bulk edits (all of them only set drafts) --------------------------------------------------------------------------

EXTENSION = '.sluggieschemistry'


def clear_all(snap: Snapshot, drafts: dict, cid: int) -> int:
    """Draft every pair of ``cid`` that is not neutral to neutral; how many pairs changed."""
    changed = 0
    for other, now in partners(snap, drafts, cid).items():
        if now != read.NEUTRAL:
            set_draft(snap, drafts, cid, other, read.NEUTRAL)
            changed += 1
    return changed


def copy_from(snap: Snapshot, drafts: dict, cid: int, source: int) -> int:
    """Draft ``cid``'s chemistry towards every other character to ``source``'s (the pair of the two stays, and so
    do pairs ``source`` holds one-way or with an unknown value); how many pairs changed."""
    if cid == source or snap.character(cid) is None or snap.character(source) is None:
        return 0
    theirs = partners(snap, drafts, source)
    changed = 0
    for other, now in partners(snap, drafts, cid).items():
        new = theirs.get(other)
        if other == source or new not in read.VALUES or new == now:
            continue
        set_draft(snap, drafts, cid, other, new)
        changed += 1
    return changed


def variants(snap: Snapshot, cid: int) -> list[int]:
    """The other characters of ``cid``'s family (colour variants share one)."""
    me = snap.character(cid)
    if me is None:
        return []
    return [c.id for c in snap.characters if c.family == me.family and c.id != cid]


def apply_to_variants(snap: Snapshot, drafts: dict, cid: int) -> int:
    """Draft the chemistry of every variant of ``cid`` towards the characters outside the family to ``cid``'s;
    how many pairs changed. Pairs inside the family stay."""
    family = set(variants(snap, cid))
    mine = partners(snap, drafts, cid)
    changed = 0
    for variant in sorted(family):
        for other, now in partners(snap, drafts, variant).items():
            new = mine.get(other)
            if other == cid or other in family or new not in read.VALUES or new == now:
                continue
            set_draft(snap, drafts, variant, other, new)
            changed += 1
    return changed


def cycle(snap: Snapshot, drafts: dict, a: int, b: int) -> None:
    """The matrix view's click: neutral -> good -> bad -> neutral (a one-way pair becomes neutral)."""
    now = value(snap, drafts, a, b)
    set_draft(snap, drafts, a, b, {read.NEUTRAL: read.GOOD, read.GOOD: read.BAD}.get(now, read.NEUTRAL))


def toggle(snap: Snapshot, drafts: dict, a: int, b: int, new: int) -> int:
    """The edit modes' drag: the pair gets ``new`` (good or bad, replacing the other), or neutral when it shows
    ``new`` already; the value it was given."""
    new = read.NEUTRAL if value(snap, drafts, a, b) == new else new
    set_draft(snap, drafts, a, b, new)
    return new


def export_set(path: str, snap: Snapshot, drafts: dict) -> tuple[int, int]:
    """Write the chemistry the tab shows (pending edits and drafts included) as a ``sluggies-chemistry`` file with
    every pair of the roster, neutral ones too, so that importing it gives this chemistry again. One-way pairs
    have no single value and are left out, as are values that are none of bad / neutral / good. ``(pairs written,
    pairs left out)``."""
    pairs, odd = {}, 0
    for pair in snap.current:
        both = shown(snap, drafts, pair)
        if both[0] == both[1] and both[0] in read.VALUES:
            pairs[pair] = both[0]
        else:
            odd += 1
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(apply.chemistry_document(pairs), f, ensure_ascii=False, indent=None, separators=(',', ':'))
    os.replace(tmp, path)
    return len(pairs), odd


def import_set(path: str, snap: Snapshot, drafts: dict) -> tuple[int, int]:
    """Draft the pairs of a ``sluggies-chemistry`` file: ``(pairs that change, pairs skipped because the roster
    does not have both characters)``. Raises ``apply.EditFileError`` for a file that is not one."""
    pairs = apply.chemistry_pairs(apply.read_file(path), os.path.basename(path))
    changed = skipped = 0
    for (a, b), new in pairs.items():
        if (a, b) not in snap.current:
            skipped += 1
            continue
        before = shown(snap, drafts, (a, b))
        set_draft(snap, drafts, a, b, new)
        changed += before != (new, new)
    return changed, skipped


def save_path(path: str) -> str:
    """``path`` with the chemistry set extension, unless it has one."""
    return path if os.path.splitext(path)[1] else path + EXTENSION


def matrix_order(snap: Snapshot, groups: dict, hidden=()) -> list[int]:
    """The IDs in the matrix view's order: by group (``layout.Layout.groups``), Miis and ungrouped IDs last."""
    hidden = set(hidden)
    return sorted((c.id for c in snap.characters if c.id not in hidden),
                  key=lambda cid: (groups.get(cid, -1) < 0, groups.get(cid, -1), cid))
