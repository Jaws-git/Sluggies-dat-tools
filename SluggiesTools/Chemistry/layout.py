"""Where the Chemistry tab's overview draws each character (numpy, deterministic, no Dear PyGui).

``arrange(ids, good, miis, kept)`` gives every ID a position, in units of one
node box (``BOX_W`` x ``BOX_H``; the tab scales them to pixels):

1. **Groups.** The characters that are not Miis are grouped by good chemistry
   (``groups``: Louvain local moving on the good pairs, the best of ``RUNS``
   seeded runs by modularity). Nothing is premade: the groups come from the
   pairs given, with a fixed seed, so the same game gives the same picture.
   Bad chemistry takes no part.
2. **Group layout.** Every group is a disc sized for its members; the discs
   are pulled towards the groups they share good pairs with and pushed apart
   until none overlap. The picture is made wider than tall (``WIDE``: most
   screens are): the discs start on a flattened spiral, are held to the
   middle more firmly up and down than sideways, and move apart sideways
   first. A last pass closes the gaps, so no group stays far out while
   there is room nearer the middle.
3. **Fine adjustment.** Members are seeded around their group's centre, then
   a force layout runs per node (repulsion, attraction along good pairs, a
   pull towards the group centre), cooled to a stop; a last pass moves
   overlapping boxes apart.
4. **Miis last.** They are placed on rings around the finished picture, each
   as near as possible to the partners it has good chemistry with, so the
   picture without Miis is the same with them.

**Kept positions**: IDs in ``kept`` stay where they are. An ID that is new is
put on the nearest free spot next to its good partners, moving nothing else.
``load`` / ``save`` keep the positions in a JSON file keyed by character ID.

``focus_ring`` places one character's partners for the focus view: good on
the left arcs, bad on the right.
"""

import json
import math
import os
from dataclasses import dataclass, field

import numpy as np

BOX_W, BOX_H = 1.5, 1.0             # a node's box (portrait + name), the unit of every distance here
RUNS = 40                           # seeded grouping runs
SWEEPS = 30                         # local-moving sweeps per run, at most
PITCH = 1.55                        # mean distance of neighbours inside a group
GROUP_GAP = 0.4                     # free space between two group discs
ITERATIONS = 260                    # force steps of the fine adjustment
GOLDEN = 2.399963229728653
WIDE = 2.2                          # how much freer the groups are sideways than up and down
COMPACT_STEPS, COMPACT_RATE, COMPACT_SIDE = 200, 0.06, 0.3    # the group layout's last pass: every disc towards
#                                                             the middle, more gently sideways (keeps it wide)
FORMAT, VERSION = 'sluggies-chemistry-layout', 7      # 2: wide, 3: compact (older kept positions are dropped)
EPS = 1e-5                          # boxes closer than this to touching do not count as overlapping


@dataclass
class Layout:
    positions: dict = field(default_factory=dict)     # {ID: (x, y)}
    groups: dict = field(default_factory=dict)        # {ID: group index}; Miis and late IDs without a group: -1

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """``(left, top, right, bottom)`` around every node box."""
        if not self.positions:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [p[0] for p in self.positions.values()]
        ys = [p[1] for p in self.positions.values()]
        return (min(xs) - BOX_W / 2, min(ys) - BOX_H / 2, max(xs) + BOX_W / 2, max(ys) + BOX_H / 2)


# -- grouping ----------------------------------------------------------------------------------------------------------

def _adjacency(ids: list[int], pairs) -> np.ndarray:
    index = {cid: i for i, cid in enumerate(ids)}
    a = np.zeros((len(ids), len(ids)))
    for x, y in pairs:
        if x in index and y in index and x != y:
            a[index[x], index[y]] = a[index[y], index[x]] = 1.0
    return a


def _local_moving(a: np.ndarray, deg: np.ndarray, m2: float, rng) -> np.ndarray:
    n = len(a)
    comm = np.arange(n)
    tot = deg.copy()
    for _sweep in range(SWEEPS):
        moved = False
        for i in rng.permutation(n):
            if deg[i] == 0:
                continue
            ci = comm[i]
            tot[ci] -= deg[i]
            gain = np.bincount(comm, weights=a[i], minlength=n) - tot * deg[i] / m2
            best = int(np.argmax(gain))
            if gain[best] > gain[ci] + 1e-12:
                comm[i] = best
                moved = True
            tot[comm[i]] += deg[i]
        if not moved:
            break
    return comm


def modularity(a: np.ndarray, comm: np.ndarray) -> float:
    m2 = a.sum()
    if m2 == 0:
        return 0.0
    deg = a.sum(1)
    same = comm[:, None] == comm[None, :]
    return float((a * same).sum() / m2 - (np.bincount(comm, weights=deg) ** 2).sum() / m2 ** 2)


def groups(ids: list[int], good, seed: int = 0, runs: int = RUNS) -> dict[int, int]:
    """``{ID: group index}`` by good chemistry (module docstring). Groups are numbered by size, the largest first
    (ties: the lowest member ID first); a character without a good pair is a group of its own."""
    ids = sorted(ids)
    if not ids:
        return {}
    a = _adjacency(ids, good)
    deg, m2 = a.sum(1), a.sum()
    best, best_q = np.arange(len(ids)), -1.0
    if m2 > 0:
        for run in range(runs):
            comm = _local_moving(a, deg, m2, np.random.RandomState(seed + run))
            q = modularity(a, comm)
            if q > best_q + 1e-12:
                best, best_q = comm, q
    members: dict[int, list[int]] = {}
    for cid, c in zip(ids, best):
        members.setdefault(int(c), []).append(cid)
    order = sorted(members.values(), key=lambda m: (-len(m), m[0]))
    return {cid: g for g, m in enumerate(order) for cid in m}


# -- geometry ----------------------------------------------------------------------------------------------------------

def overlaps(p, q) -> bool:
    """Two node boxes at ``p`` and ``q`` overlap."""
    return abs(p[0] - q[0]) < BOX_W - EPS and abs(p[1] - q[1]) < BOX_H - EPS


def overlapping(positions: dict) -> list[tuple[int, int]]:
    """The pairs of IDs whose boxes overlap."""
    ids = sorted(positions)
    if len(ids) < 2:
        return []
    p = np.array([positions[c] for c in ids])
    dx = np.abs(p[:, None, 0] - p[None, :, 0])
    dy = np.abs(p[:, None, 1] - p[None, :, 1])
    hit = np.triu((dx < BOX_W - EPS) & (dy < BOX_H - EPS), 1)
    return [(ids[i], ids[j]) for i, j in zip(*np.nonzero(hit))]


def _free(spot, placed: np.ndarray) -> bool:
    if not len(placed):
        return True
    return not np.any((np.abs(placed[:, 0] - spot[0]) < BOX_W - EPS) & (np.abs(placed[:, 1] - spot[1]) < BOX_H - EPS))


def _spiral_spot(target, placed: np.ndarray) -> tuple[float, float]:
    """The first free spot on a spiral around ``target``."""
    k = 0
    while True:
        r = 0.35 * math.sqrt(k)
        spot = (target[0] + r * math.cos(k * GOLDEN), target[1] + r * math.sin(k * GOLDEN))
        if _free(spot, placed):
            return spot
        k += 1


def _ring_spot(centre, radius: float, angle: float, placed: np.ndarray) -> tuple[float, float]:
    """The free spot on the ring nearest to ``angle``; a wider ring when this one is full."""
    while True:
        step = 0.5 * BOX_H / radius
        for k in range(int(math.pi / step) + 1):
            for sign in ((1,) if k == 0 else (1, -1)):
                at = angle + sign * k * step
                spot = (centre[0] + radius * math.cos(at), centre[1] + radius * math.sin(at))
                if _free(spot, placed):
                    return spot
        radius += BOX_H * 1.25


def _separate_discs(centres: np.ndarray, radii: np.ndarray, gap: float, rounds: int = 400,
                    wide: float = 1.0) -> np.ndarray:
    """Push overlapping discs apart; ``wide`` > 1 moves them further sideways than up and down per round."""
    bias = np.array([wide, 1.0 / wide]) if wide != 1.0 else None
    for _round in range(rounds):
        delta = centres[:, None] - centres[None, :]
        dist = np.hypot(delta[..., 0], delta[..., 1])
        need = radii[:, None] + radii[None, :] + gap
        np.fill_diagonal(dist, 1.0)
        push = np.where(dist < need, need - dist, 0.0)
        np.fill_diagonal(push, 0.0)
        if not push.any():
            break
        unit = delta / dist[..., None]
        same = dist < 1e-9                             # two discs on one point: push along x, by index
        if same.any():
            i, j = np.nonzero(same & ~np.eye(len(centres), dtype=bool))
            unit[i, j] = np.stack([np.sign(i - j), np.zeros(len(i))], axis=1)
        move = (unit * push[..., None]).sum(1) * 0.5
        centres = centres + (move * bias if bias is not None else move)
    return centres


def _separate_boxes(p: np.ndarray, rounds: int = 600) -> np.ndarray:
    """Move overlapping boxes apart along the axis of least overlap."""
    n = len(p)
    for _round in range(rounds):
        dx = p[:, None, 0] - p[None, :, 0]
        dy = p[:, None, 1] - p[None, :, 1]
        ox, oy = BOX_W - np.abs(dx), BOX_H - np.abs(dy)
        hit = (ox > EPS) & (oy > EPS)
        np.fill_diagonal(hit, False)
        if not hit.any():
            return p
        index = np.arange(n)
        sx = np.where(dx == 0, np.sign(index[:, None] - index[None, :]), np.sign(dx))
        sy = np.where(dy == 0, np.sign(index[:, None] - index[None, :]), np.sign(dy))
        along_x = hit & (ox / BOX_W <= oy / BOX_H)
        along_y = hit & ~along_x
        move = np.stack([(sx * ox * along_x).sum(1), (sy * oy * along_y).sum(1)], axis=1)
        p = p + move * 0.5 + np.sign(move) * 1e-3
    placed = np.empty((0, 2))                          # did not settle: place one by one (always ends)
    for i in range(n):
        spot = _spiral_spot(p[i], placed)
        placed = np.vstack([placed, spot])
    return placed


# -- the overview ------------------------------------------------------------------------------------------------------

def _group_centres(sizes: list[int], weight: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Disc centres and radii for groups of ``sizes`` members; ``weight[g, h]``: good pairs between two groups."""
    count = len(sizes)
    radii = np.array([PITCH * 0.46 * math.sqrt(s) + 0.5 for s in sizes])   # as tight as the members end up
    spread = float(radii.mean()) * 1.6 + GROUP_GAP
    centres = np.array([[spread * math.sqrt(g) * math.cos(g * GOLDEN) * WIDE,
                         spread * math.sqrt(g) * math.sin(g * GOLDEN) / WIDE] for g in range(count)])
    centres = _separate_discs(centres, radii, GROUP_GAP, wide=WIDE)
    hold = np.array([1.0 / WIDE, WIDE])                # the pull to the middle: weak sideways, firm up and down
    total = weight.sum(1)
    area = radii ** 2
    for step in range(200):
        rate = 0.25 * (1 - step / 200)
        middle = (centres * area[:, None]).sum(0) / area.sum()
        pull = (middle - centres) * 2.0 * hold                            # a group without pairs outside: the middle
        linked = total > 0
        pull[linked] = (((weight[linked] @ centres) / total[linked, None] - centres[linked]) * np.array([0.6, 1.0])
                        + 0.35 * (middle - centres[linked]) * hold)
        centres = _separate_discs(centres + rate * pull, radii, GROUP_GAP, rounds=30, wide=WIDE)
    centres = _separate_discs(centres, radii, GROUP_GAP, wide=WIDE)
    # close the gaps: no group stays far out while there is room. Discs are moved apart exactly here (the sideways
    # bias above pushes them further than they need, which is what leaves gaps)
    squeeze = np.array([COMPACT_SIDE, 1.0])
    for _step in range(COMPACT_STEPS):
        middle = (centres * area[:, None]).sum(0) / area.sum()
        centres = _separate_discs(centres + COMPACT_RATE * (middle - centres) * squeeze, radii, GROUP_GAP, rounds=30)
    centres = _separate_discs(centres, radii, GROUP_GAP)
    return centres - (centres * area[:, None]).sum(0) / area.sum(), radii


def _core(ids: list[int], good, seed: int) -> tuple[dict, dict]:
    """Positions and groups of the characters that are not Miis (steps 1 to 3)."""
    ids = sorted(ids)
    if not ids:
        return {}, {}
    group_of = groups(ids, good, seed)
    count = max(group_of.values()) + 1
    g = np.array([group_of[c] for c in ids])
    a = _adjacency(ids, good)
    sizes = [int((g == k).sum()) for k in range(count)]
    onehot = np.eye(count)[g]
    weight = onehot.T @ a @ onehot
    np.fill_diagonal(weight, 0.0)
    centres, radii = _group_centres(sizes, weight)

    p = np.zeros((len(ids), 2))
    for k in range(count):
        for rank, i in enumerate(np.nonzero(g == k)[0]):
            r = PITCH * 0.62 * math.sqrt(rank)
            p[i] = centres[k] + (r * math.cos(rank * GOLDEN), r * math.sin(rank * GOLDEN))
    same = g[:, None] == g[None, :]
    pull = a * np.where(same, 1.0, 0.12)               # pairs across groups only turn members towards each other
    home, reach = centres[g], radii[g]
    for step in range(ITERATIONS):
        heat = 0.35 * (1 - step / ITERATIONS) + 0.01
        delta = p[:, None] - p[None, :]
        dist = np.hypot(delta[..., 0], delta[..., 1])
        np.fill_diagonal(dist, 1.0)
        near = dist < 1e-6                             # two nodes on one point: a fixed, index-based direction
        if near.any():
            i, j = np.nonzero(near & ~np.eye(len(ids), dtype=bool))
            delta[i, j] = np.stack([np.sign(i - j) * 1e-3, np.zeros(len(i))], axis=1)
            dist[i, j] = 1e-3
        unit = delta / dist[..., None]
        # nodes of other groups repel at short range only: at long range a large group would push a small one
        # away from the place the group layout gave it
        repel = np.where(dist < np.where(same, 4 * PITCH, 1.4 * PITCH), PITCH ** 2 / dist, 0.0)
        np.fill_diagonal(repel, 0.0)
        force = (unit * (repel - pull * dist ** 2 / PITCH)[..., None]).sum(1)
        out = p - home
        far = np.hypot(out[:, 0], out[:, 1])
        force -= out * (0.35 + 2.0 * np.maximum(far - reach, 0.0) / np.maximum(far, 1e-6))[:, None]
        size = np.hypot(force[:, 0], force[:, 1])
        p = p + force / np.maximum(size, 1e-9)[:, None] * np.minimum(size, heat)[:, None]
    p = _separate_boxes(p)
    return {c: (float(x), float(y)) for c, (x, y) in zip(ids, p)}, group_of


def _picture(positions: dict) -> tuple[tuple[float, float], float]:
    """Centre and radius of the placed nodes."""
    if not positions:
        return (0.0, 0.0), 0.0
    p = np.array(list(positions.values()))
    centre = p.mean(0)
    return (float(centre[0]), float(centre[1])), float(np.hypot(*(p - centre).T).max())


def _partners(cid: int, good) -> set[int]:
    return {b if a == cid else a for a, b in good if cid in (a, b) and a != b}


def _place_miis(positions: dict, miis: list[int], good) -> None:
    core = dict(positions)
    centre, _radius = _picture(core)
    spots = np.array(list(core.values())) if core else np.empty((0, 2))
    far = np.hypot(*(spots - centre).T) if core else np.empty(0)
    bearing = np.arctan2(spots[:, 1] - centre[1], spots[:, 0] - centre[0]) if core else np.empty(0)

    def ring(angle: float) -> float:
        """Just outside the picture in this direction."""
        off = np.abs((bearing - angle + math.pi) % (2 * math.pi) - math.pi)
        sector = far[off < 0.6]
        return float(sector.max() if len(sector) else np.median(far)) + BOX_W * 1.3
    placed = np.array(list(positions.values())) if positions else np.empty((0, 2))
    for n, cid in enumerate(sorted(miis)):
        near = [core[o] for o in sorted(_partners(cid, good)) if o in core]
        angle = n * GOLDEN
        if near:
            tx, ty = np.mean(near, axis=0) - centre
            if math.hypot(tx, ty) > 1e-6:
                angle = math.atan2(ty, tx)
        spot = _ring_spot(centre, ring(angle), angle, placed) if core else _spiral_spot(centre, placed)
        positions[cid] = spot
        placed = np.vstack([placed, spot])


def _place_late(positions: dict, late: list[int], good, group_of: dict) -> None:
    """IDs that are new next to kept positions: beside their good partners, moving nothing."""
    for cid in sorted(late):
        placed = np.array(list(positions.values())) if positions else np.empty((0, 2))
        near = [positions[o] for o in sorted(_partners(cid, good)) if o in positions]
        if not near:
            near = [positions[o] for o in sorted(positions) if group_of.get(o, -1) == group_of.get(cid, -2)]
        if near:
            target = tuple(np.mean(near, axis=0))
        else:
            centre, radius = _picture(positions)
            target = (centre[0] + radius + BOX_W, centre[1])
        positions[cid] = _spiral_spot(target, placed)


def arrange(ids, good, miis=(), kept: dict | None = None, seed: int = 0) -> Layout:
    """The overview's layout (module docstring). ``good``: the good pairs ``(a, b)``; ``miis``: the IDs placed last;
    ``kept``: ``{ID: (x, y)}`` of an earlier layout (IDs the roster no longer has are dropped)."""
    ids = sorted(set(ids))
    mii_set = set(miis) & set(ids)
    core = [c for c in ids if c not in mii_set]
    good = [(a, b) for a, b in good if a != b]
    kept = {c: (float(p[0]), float(p[1])) for c, p in (kept or {}).items() if c in set(ids)}
    if not kept:
        positions, group_of = _core(core, good, seed)
        _place_miis(positions, sorted(mii_set), good)
    else:
        positions = dict(kept)
        group_of = groups(core, good, seed)
        _place_late(positions, [c for c in core if c not in positions], good, group_of)
        _place_miis(positions, [c for c in sorted(mii_set) if c not in positions], good)
    return Layout(positions, {c: group_of.get(c, -1) for c in ids})


# -- the focus view ----------------------------------------------------------------------------------------------------

def focus_ring(good: list[int], bad: list[int], radius: float = 4.0) -> dict[int, tuple[float, float]]:
    """Positions around ``(0, 0)`` for one character's partners: good on the left arcs, bad on the right, in the
    order given; a side with more partners than an arc holds gets further arcs outside."""
    out = {}
    reach = math.sin(math.radians(75))                 # each side's arc spans 150 degrees around the x axis
    pitch = BOX_H * 1.12                               # rows of one arc are a box height apart, so none overlap
    for side, members in ((-1, list(good)), (1, list(bad))):
        r, start = radius, 0
        while start < len(members):
            room = int(2 * r * reach / pitch) + 1
            row = members[start:start + room]
            for k, cid in enumerate(row):
                y = (k - (len(row) - 1) / 2) * pitch
                out[cid] = (side * math.sqrt(r * r - y * y), y)
            start += room
            r += BOX_W * 1.25
    return out


# -- kept positions ----------------------------------------------------------------------------------------------------

def load(path: str) -> dict[int, tuple[float, float]]:
    """The kept positions of ``path``; empty when it is missing or not a layout file."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            doc = json.load(f)
        if doc.get('format') != FORMAT or doc.get('version') != VERSION:
            return {}
        return {int(k, 16): (float(v[0]), float(v[1])) for k, v in doc['positions'].items()
                if math.isfinite(float(v[0])) and math.isfinite(float(v[1]))}
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return {}


def save(path: str, positions: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    doc = {'format': FORMAT, 'version': VERSION,
           'positions': {f'0x{c:02X}': [round(x, 6), round(y, 6)] for c, (x, y) in sorted(positions.items())}}
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, path)
