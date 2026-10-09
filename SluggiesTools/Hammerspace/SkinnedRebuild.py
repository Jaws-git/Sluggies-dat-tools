"""Canonical skin layout for a rebuilt skinned submesh 0 (PLAN_ModelReplacements.md
Milestone 4.4/4.5).

Pure Python, no slogger, no dataclasses from HammerspaceMain, so the layout
rules can be unit-tested on their own. HammerspaceMain turns the result into
its SK1/SK2/SKAcc structures and the GPL blob.

The layout follows the external tool's proven ``layout_skin`` and the vanilla
rules recorded in ``skn_section.html``:

- every vertex gets exactly one direct write: one bone -> SK1, two bones ->
  SK2 (pair sorted by bone id), further influences -> SKAcc supplements by
  bone. No accumulation-only slots, so memClr is 0/0;
- entries are placed in key order, each starting on a fresh 32-byte cache
  line (``dest = align32(cursor)``, ``vertex_offset`` = offset of the first
  whole vertex in that line), so no two SK1/SK2 entries share a line and the
  slot order of the position buffer *is* the entry order;
- an entry whose source would exceed the locked-cache cap (8180 bytes SK1,
  4088 bytes SK2) is split; an entry under 3 vertices is padded with repeats
  of its first vertex (extra slots with the same bind data and weight that no
  primitive references);
- the flush array lists one vertex index per cache line an SKAcc write
  touches: the first slot starting in that line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from binfmt import SKN_MAX_SOURCE_BYTES, SKN_MIN_DIRECT_VERTICES

CACHE_LINE = 32
WEIGHT_UNITS = 256


def align32(value: int) -> int:
    return (value + CACHE_LINE - 1) & ~(CACHE_LINE - 1)


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

def quantize_weights(influences) -> list[tuple[int, int]]:
    """``[(bone, weight), ...]`` -> ``[(bone, units), ...]`` with the units
    summing to 256 by the largest-remainder rule (ties: larger weight, then
    lower bone id). Repeated bones are merged, non-positive weights dropped,
    zero-unit results dropped. Sorted by units descending, then bone id."""
    merged: dict[int, float] = {}
    for bone, weight in influences:
        weight = float(weight)
        if not math.isfinite(weight):
            raise ValueError(f'bone {bone} has a non-finite weight {weight}')
        if weight > 0:
            merged[int(bone)] = merged.get(int(bone), 0.0) + weight
    if not merged:
        raise ValueError('vertex has no positive skin weight')
    total = sum(merged.values())
    items = sorted(merged.items(), key=lambda item: (-item[1], item[0]))
    exact = [weight / total * WEIGHT_UNITS for _bone, weight in items]
    units = [int(value) for value in exact]
    missing = WEIGHT_UNITS - sum(units)
    for index in sorted(range(len(units)), key=lambda i: (-(exact[i] - units[i]), i))[:missing]:
        units[index] += 1
    result = [(bone, n) for (bone, _weight), n in zip(items, units) if n]
    return sorted(result, key=lambda item: (-item[1], item[0]))


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

@dataclass
class DirectEntry:
    """One SK1 or SK2 entry in destination order. ``slots`` lists
    ``(slot, vertex)`` for every slot it writes, padding repeats included;
    ``weights`` (SK2 only) holds one ``(units_lo, units_hi)`` pair per slot
    for ``bones`` in ascending bone order."""
    kind: str                      # 'SK1' | 'SK2'
    bones: tuple                   # (bone,) or (lo, hi)
    dest: int                      # gplVertexArr: position-data-relative, cache-line aligned
    vertex_offset: int             # first whole vertex inside the first line
    slots: list                    # [(slot, vertex)]
    weights: list = field(default_factory=list)

    @property
    def vertex_count(self) -> int:
        return len(self.slots)


@dataclass
class AccumulationEntry:
    bone: int
    members: list                  # [(slot, vertex, units)]


@dataclass
class SkinLayout:
    stride: int
    slot_of_vertex: list           # slot per input vertex
    slot_count: int
    direct: list                   # [DirectEntry], destination order
    accumulations: list            # [AccumulationEntry], by bone
    flush: list                    # vertex indices to flush
    write_back_end: int            # position-data-relative end of the SK1/SK2 write-back

    @property
    def padded_slots(self) -> int:
        return self.slot_count - len(self.slot_of_vertex)


def _shape_entry(kind: str, members: list, stride: int) -> list[list]:
    """Split *members* to the source-size cap, then pad short pieces with
    repeats of their first member (the external tool's shape_entries)."""
    cap = (SKN_MAX_SOURCE_BYTES[kind] - (stride - 4)) // stride   # worst-case vertex offset is stride - 4
    pieces = [members[i:i + cap] for i in range(0, len(members), cap)]
    return [
        piece + [piece[0]] * (SKN_MIN_DIRECT_VERTICES - len(piece))
        if len(piece) < SKN_MIN_DIRECT_VERTICES else piece
        for piece in pieces
    ]


def flush_indices(acc_slots, stride: int, slot_count: int) -> list[int]:
    """One vertex index per cache line an SKAcc write touches: the first
    slot starting in that line."""
    lines = sorted({
        line
        for slot in acc_slots
        for line in range(slot * stride // CACHE_LINE, (slot * stride + stride - 1) // CACHE_LINE + 1)
    })
    indices = sorted({-(-(line * CACHE_LINE) // stride) for line in lines})
    return [index for index in indices if index < slot_count]


def layout_skin(vertex_units: list, stride: int) -> SkinLayout:
    """Assign position-buffer slots and SK entries.

    *vertex_units* holds, per vertex, its quantized ``[(bone, units), ...]``
    (see :func:`quantize_weights`); every vertex needs at least one."""
    sk1: dict[int, list] = {}
    sk2: dict[tuple, list] = {}
    acc: dict[int, list] = {}
    for vertex, units in enumerate(vertex_units):
        if not units:
            raise ValueError(f'vertex {vertex} has no skin influence')
        if len(units) == 1:
            sk1.setdefault(units[0][0], []).append((vertex, None))
            continue
        (b1, w1), (b2, w2) = units[0], units[1]
        if b1 > b2:
            b1, w1, b2, w2 = b2, w2, b1, w1
        sk2.setdefault((b1, b2), []).append((vertex, (w1, w2)))
        for bone, n in units[2:]:
            acc.setdefault(bone, []).append((vertex, n))

    entries = [('SK1', (bone,), piece) for bone, members in sorted(sk1.items())
               for piece in _shape_entry('SK1', members, stride)]
    entries += [('SK2', pair, piece) for pair, members in sorted(sk2.items())
                for piece in _shape_entry('SK2', members, stride)]

    slot_of_vertex: list = [None] * len(vertex_units)
    cursor = 0
    direct: list[DirectEntry] = []
    for kind, bones, members in entries:
        line = align32(cursor)
        first = -(-line // stride)              # first whole vertex slot at or after the line start
        slots = []
        weights = []
        for k, (vertex, pair_weights) in enumerate(members):
            slot = first + k
            if slot_of_vertex[vertex] is None:
                slot_of_vertex[vertex] = slot
            slots.append((slot, vertex))
            if pair_weights is not None:
                weights.append(pair_weights)
        direct.append(DirectEntry(kind, bones, line, first * stride - line, slots, weights))
        cursor = (first + len(members)) * stride
    slot_count = cursor // stride
    if slot_count > 0xFFFF:
        raise ValueError(f'{slot_count} position slots exceed the uint16 index range')

    accumulations = [
        AccumulationEntry(bone, [(slot_of_vertex[vertex], vertex, n) for vertex, n in members])
        for bone, members in sorted(acc.items())
    ]
    acc_slots = [slot for entry in accumulations for slot, _vertex, _units in entry.members]
    write_back_end = max(
        (entry.dest + align32(entry.vertex_offset + entry.vertex_count * stride) for entry in direct),
        default=0,
    )
    return SkinLayout(
        stride=stride,
        slot_of_vertex=slot_of_vertex,
        slot_count=slot_count,
        direct=direct,
        accumulations=accumulations,
        flush=flush_indices(acc_slots, stride, slot_count),
        write_back_end=write_back_end,
    )


def build_position_buffer(layout: SkinLayout, vertex_records: bytes) -> bytes:
    """The rebuilt position buffer: ``slot_count`` records of ``stride``
    bytes, each written slot holding its vertex's record (position and
    normal interleaved), gap slots zero."""
    stride = layout.stride
    if len(vertex_records) != len(layout.slot_of_vertex) * stride:
        raise ValueError(
            f'vertex records are {len(vertex_records)} bytes, expected '
            f'{len(layout.slot_of_vertex)} x {stride}'
        )
    buffer = bytearray(layout.slot_count * stride)
    for entry in layout.direct:
        for slot, vertex in entry.slots:
            buffer[slot * stride:(slot + 1) * stride] = vertex_records[vertex * stride:(vertex + 1) * stride]
    return bytes(buffer)


def entry_source(layout: SkinLayout, entry: DirectEntry, position_buffer: bytes) -> bytes:
    """An SK1/SK2 entry's bind-pose source records (no vertex-offset prefix)."""
    stride = layout.stride
    return b''.join(position_buffer[slot * stride:(slot + 1) * stride] for slot, _vertex in entry.slots)


def summary(layout: SkinLayout) -> str:
    sk1 = sum(1 for entry in layout.direct if entry.kind == 'SK1')
    sk2 = len(layout.direct) - sk1
    return (
        f'{len(layout.slot_of_vertex)} vertices in {layout.slot_count} slots '
        f'({layout.padded_slots} padding/gap), {sk1} SK1 / {sk2} SK2 / '
        f'{len(layout.accumulations)} SKAcc entries, {len(layout.flush)} flush indices'
    )
