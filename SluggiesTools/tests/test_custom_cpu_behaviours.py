"""The custom CPU level's behaviour code (SluggiesTools/CustomCpu/behaviours.py): each stub run in an
interpreter against stand-ins for the game's data and functions, and its hooks in the DOL."""

import random
import unittest
from itertools import permutations

from SluggiesTools.CustomCpu import behaviours as bh
from SluggiesTools.CustomCpu import engine, pitching, settings
from SluggiesTools.Dol import dolfile, ppc
from SluggiesTools.GameOptions import game_options as go
from SluggiesTools.tests.ppc_sim import Sim
from SluggiesTools.tests.test_custom_cpu import game_dol

CODE = 0x807B8000                       # where the stubs are assembled
R13 = 0x80700000
R2 = 0x80710000
STACK = 0x80680000
RETURN = 0x80001000                     # LR on entry: a stop
AI, BATTER, PITCHER, TICK_OBJ, GAME, CTL, LINEUP, RECORDS, STAMINA, METERS, RNG_OBJ = (
    0x80610000 + 0x1000 * i for i in range(11))
EVERY = {b.feature: True for b in bh.BEHAVIOURS}


def const(edits=None):
    return lambda key: settings.value(settings.BY_KEY[key], edits or {})


def machine(stub, flag: int = 1) -> Sim:
    sim = Sim()
    sim.load(stub.base, stub.assemble())
    sim.w8(bh.FLAG, flag)
    sim.r[1], sim.r[2], sim.r[13], sim.lr = STACK, R2, R13, RETURN
    for offset, at in ((bh.TICK, TICK_OBJ), (bh.CTL, CTL), (bh.LINEUP, LINEUP), (bh.RECORDS, RECORDS),
                       (bh.STAMINA, STAMINA), (bh.METER, METERS), (bh.RNG, RNG_OBJ)):
        sim.w32(R13 + offset, at)
    return sim


class SmallStubTests(unittest.TestCase):
    def test_bunt_returns_without_a_bunt_at_the_custom_level_only(self):
        for flag, tier, ours in ((1, 3, True), (0, 3, False), (1, 2, False)):
            sim = machine(bh.bunt_stub(CODE), flag)
            sim.r[3] = AI
            sim.w8(AI + bh.AI_TIER, tier)
            sim.w8(AI + bh.AI_START, 1)
            sim.w8(AI + bh.AI_BUNT, 3)
            stop = sim.run(CODE, {RETURN, bh.BUNT + 4})
            self.assertEqual(stop, RETURN if ours else bh.BUNT + 4, (flag, tier))
            self.assertEqual((sim.u8(AI + bh.AI_START), sim.u8(AI + bh.AI_BUNT)), (0, 0) if ours else (1, 3))
            self.assertEqual(sim.r[1], STACK if ours else STACK - 0x20)      # the stock word made the frame

    def test_cursed_ball_skips_the_human_pitch_test(self):
        for flag, value, stop in ((1, 0, bh.CURSED_CHARGED), (1, 3, bh.CURSED_CHARGED), (0, 3, bh.CURSED_CHARGED),
                                  (0, 0, bh.CURSED_NOT), (0, 1, bh.CURSED_NOT)):
            sim = machine(bh.cursed_stub(CODE), flag)
            sim.r[0] = value                             # pitcher +0x140, loaded before the site
            self.assertEqual(sim.run(CODE, {bh.CURSED_CHARGED, bh.CURSED_NOT}), stop, (flag, value))
            self.assertEqual(sim.r[0], value)

    def charge(self, flag=1, tier=3, charging=0, frame=2, swing_at=30, full=60, last=100, frames=7):
        sim = machine(bh.charge_stub(CODE), flag)
        sim.r[3] = sim.r[29] = AI
        sim.r[30], sim.r[31] = BATTER, PITCHER
        sim.w8(AI + bh.AI_TIER, tier)
        sim.w8(AI + 0x34, 0x5A)
        sim.w8(AI + bh.AI_SWING_AT, swing_at)
        sim.w8(BATTER + bh.B_CHARGE, charging)
        sim.w16(BATTER + bh.B_FULL, full)
        sim.w16(BATTER + bh.B_LAST, last)
        sim.w16(BATTER + bh.B_FRAMES, frames)
        sim.w16(TICK_OBJ + bh.TICK_FRAME, frame)
        stop = sim.run(CODE, {bh.CHARGE_AFTER, bh.CHARGE + 4})
        return sim, stop

    def test_charge_starts_at_once_and_is_mid_window_at_the_swing(self):
        sim, stop = self.charge(charging=0, frame=1)
        self.assertEqual(stop, bh.CHARGE_AFTER)
        self.assertEqual((sim.u8(BATTER + bh.B_CHARGE), sim.u8(BATTER + bh.B_SWING_TYPE)), (1, 1))
        self.assertEqual(sim.u16(BATTER + bh.B_FRAMES), 7)                   # not flight frame 2: left alone
        sim, _stop = self.charge(charging=1, frame=2, swing_at=30)
        self.assertEqual(sim.u16(BATTER + bh.B_FRAMES), (60 + 100) // 2 - (30 + 1 - 2))
        sim, _stop = self.charge(charging=1, frame=2, swing_at=120)          # more frames left than the middle
        self.assertEqual(sim.u16(BATTER + bh.B_FRAMES), 0)
        sim, _stop = self.charge(charging=1, frame=2, swing_at=0)            # no swing frame yet
        self.assertEqual(sim.u16(BATTER + bh.B_FRAMES), 7)
        sim, _stop = self.charge(charging=2, frame=2)                        # swung: frozen
        self.assertEqual((sim.u8(BATTER + bh.B_CHARGE), sim.u16(BATTER + bh.B_FRAMES)), (2, 7))

    def test_charge_is_stock_off_the_custom_level(self):
        for flag, tier in ((0, 3), (1, 1)):
            sim, stop = self.charge(flag=flag, tier=tier)
            self.assertEqual(stop, bh.CHARGE + 4)
            self.assertEqual(sim.r[0], 0x5A)                                 # the site's own load
            self.assertEqual((sim.u8(BATTER + bh.B_CHARGE), sim.u16(BATTER + bh.B_FRAMES)), (0, 7))

    def swing(self, charge_power, meter, edits=None, flag=1, tier=3, table_sum=0x1F0):
        sim = machine(bh.swing_stub(CODE, const(edits)), flag)
        sim.r[0], sim.r[25], sim.r[26], sim.r[29] = table_sum, 0x77, AI, BATTER
        sim.r[3], sim.r[4] = RNG_OBJ, 100
        sim.w8(AI + bh.AI_TIER, tier)
        sim.w16(BATTER + bh.CHARGE_POWER, charge_power)
        sim.w8(GAME + 0x2C, 1)
        sim.w16(METERS + 2, meter)
        sim.w16(METERS, 500)                             # the other side's meter is not read

        def team_of(s):
            s.r[0] = s.r[4] = s.r[12] = 0xDEAD           # a call clobbers the volatile registers
            s.r[3] = GAME
        sim.calls[bh.TEAM_OF] = team_of
        self.assertEqual(sim.run(CODE, {bh.SWING_BACK}), bh.SWING_BACK)
        self.assertEqual((sim.r[3], sim.r[4]), (RNG_OBJ, 100))               # the rand call's arguments
        return ppc.signed16(sim.r[25] & 0xFFFF)

    def test_star_swing_chance_follows_charge_power(self):
        for edits in ({}, {'star_always': 50, 'star_never': 85, 'star_rare': 75, 'star_rare_pct': 4,
                           'star_top': 90}):
            values = [int(const(edits)(k)) for k in ('star_always', 'star_never', 'star_rare', 'star_rare_pct',
                                                     'star_top')]
            for charge_power in (1, 40, 49, 50, 59, 60, 74, 75, 84, 85, 98, 150):
                for meter in (0, 99, 100, 500):
                    self.assertEqual(self.swing(charge_power, meter, edits),
                                     bh.star_chance(charge_power, *values, meter), (edits, charge_power, meter))
        self.assertEqual(self.swing(40, 0), 40)                              # 80 - charge power
        self.assertEqual(self.swing(40, 100), 100)                           # 2 stars: always
        self.assertEqual(self.swing(60, 500), 0)

    def test_star_swing_is_the_table_sum_off_the_custom_level(self):
        self.assertEqual(self.swing(40, 500, flag=0), -16)                   # extsb of the sum 0x1F0
        self.assertEqual(self.swing(40, 500, tier=2), -16)

    def test_no_star_pitches(self):
        for flag, tier, ours in ((1, 3, True), (0, 3, False), (1, 0, False)):
            sim = machine(bh.star_pitch_stub(CODE), flag)
            sim.r[3] = AI
            sim.w8(AI + bh.BRAIN_TIER, tier)
            stop = sim.run(CODE, {RETURN, bh.STAR_PITCH + 4})
            self.assertEqual(stop, RETURN if ours else bh.STAR_PITCH + 4)
            self.assertEqual((sim.r[3], sim.r[1]), (0, STACK) if ours else (AI, STACK - 0x30))


# -- substitutions: the game's lineup, chemistry and stamina as plain data --------------------------------------

def weight(chem, a, b) -> int:
    return bh.WEIGHT.get(chem[a][b], 1)


def field_key(chem, at: dict) -> int:
    """The chemistry key of ``at`` ({position: slot})."""
    return (64 * (weight(chem, at[7], at[6]) + weight(chem, at[7], at[8])) + 8 * weight(chem, at[3], at[5])
            + weight(chem, at[5], at[2]) + weight(chem, at[4], at[2]))


def best_field(chem, at: dict) -> tuple[int, int]:
    """(the best key, the fewest moved players reaching it) over every arrangement of positions 2..8."""
    now = [at[p] for p in range(2, 9)]
    best = (field_key(chem, at), 0)
    for perm in permutations(now):
        key, moves = field_key(chem, dict(zip(range(2, 9), perm))), sum(x != y for x, y in zip(perm, now))
        if key > best[0] or (key == best[0] and moves < best[1]):
            best = (key, moves)
    return best


class Game:
    """One side's lineup (9 entries: slot, position), a chemistry table and stats, in a ``Sim``."""
    TEAM, SIDE = 2, 1

    def __init__(self, sim: Sim, rng: random.Random, chem_values=(0, 1, 2)):
        self.sim, self.swaps, self.rolls = sim, [], []
        slots = list(range(9))
        rng.shuffle(slots)
        self.at = dict(enumerate(slots))                 # {position: slot}
        self.chem = [[1] * 9 for _ in range(9)]
        for a in range(9):
            for b in range(a):
                self.chem[a][b] = self.chem[b][a] = rng.choice(chem_values)
        self.curve, self.stamina, self.max_stamina = {}, {}, {}
        self.pick = 0
        sim.w8(GAME + 0x2B, self.TEAM)
        sim.w8(GAME + 0x2C, self.SIDE)
        sim.w8(GAME + 0x2D, self.SIDE)
        sim.w32(CTL + bh.CTL_LEVEL + 4 * self.SIDE, 0)
        sim.w32(CTL + bh.CTL_LEVEL + 4 * (1 - self.SIDE), 2)
        self.write_lineup()
        sim.calls.update({bh.TEAM_OF: self.team_of, bh.CHEM: self.chemistry, bh.SWAP: self.swap,
                          bh.POS_OF: self.pos_of, bh.MAX_STAMINA: self.max_of, bh.RAND: self.rand})

    def write_lineup(self) -> None:
        for i, (pos, slot) in enumerate(sorted(self.at.items(), key=lambda kv: kv[1])):
            entry = LINEUP + self.SIDE * 0x28 + 4 * i
            self.sim.w16(entry + 0xC, slot)
            self.sim.w16(entry + 0xE, pos)

    def stats(self, slot: int, curve: int, stamina: int, max_stamina: int) -> None:
        self.curve[slot], self.stamina[slot], self.max_stamina[slot] = curve, stamina, max_stamina
        self.sim.w16(RECORDS + self.TEAM * 0x4FE + slot * 0x8E + 0x24, curve)
        self.sim.w16(STAMINA + 0x358 + self.TEAM * 0x120 + slot * 0x20, stamina)

    def _clobber(self, s: Sim) -> None:
        for reg in (0, 4, 5, 6, 7, 8, 9, 10, 11, 12):
            s.r[reg] = 0xBAD0BAD0
        s.ctr = 0xBAD

    def team_of(self, s):
        self._clobber(s)
        s.r[3] = GAME

    def chemistry(self, s):
        team, a, b = s.r[3], s.r[4], s.r[5]
        assert team == self.TEAM
        self._clobber(s)
        s.r[3] = self.chem[a][b]

    def swap(self, s):
        lineup, side, a, b = s.r[3:7]
        assert (lineup, side) == (LINEUP, self.SIDE) and 2 <= a <= 8 and 2 <= b <= 8
        self.at[a], self.at[b] = self.at[b], self.at[a]
        self.swaps.append((a, b))
        self.write_lineup()
        self._clobber(s)

    def pos_of(self, s):
        lineup, side, slot = s.r[3:6]
        assert (lineup, side) == (LINEUP, self.SIDE)
        self._clobber(s)
        s.r[3] = next(p for p, x in self.at.items() if x == slot)

    def max_of(self, s):
        team, slot = s.r[3:5]
        assert team == self.TEAM
        self._clobber(s)
        s.r[3] = self.max_stamina[slot]

    def rand(self, s):
        assert s.r[3] == RNG_OBJ
        self.rolls.append(s.r[4])
        self._clobber(s)
        s.r[3] = self.pick % self.rolls[-1]


def subs(flag: int = 1):
    """(pitch stub, field stub, search) addresses in a machine."""
    blob, hooks = bh.BY_FEATURE['cpu-subs'].build(CODE, const())
    sim = machine(ppc.Asm(CODE), flag)
    sim.load(CODE, blob)
    return sim, ppc.branch_target(hooks[bh.PITCH], bh.PITCH), ppc.branch_target(hooks[bh.FIELD], bh.FIELD)


SAVED = {reg: 0x51000000 + reg for reg in range(14, 32)}


class FieldTests(unittest.TestCase):
    def run_field(self, seed, chem_values=(0, 1, 2), flag=1, level=0):
        sim, _pitch, field = subs(flag)
        game = Game(sim, random.Random(seed), chem_values)
        sim.w32(CTL + bh.CTL_LEVEL + 4 * game.SIDE, level)
        for reg, value in SAVED.items():
            sim.r[reg] = value
        sim.r[31] = GAME                                 # the task: its side is at +0x2D too
        before = dict(game.at)
        self.assertEqual(sim.run(field, {bh.FIELD_BACK}), bh.FIELD_BACK)
        self.assertEqual((sim.r[0], sim.r[1], sim.r[31], sim.lr), (3, STACK, GAME, RETURN))
        self.assertEqual([sim.r[reg] for reg in range(14, 31)], [SAVED[reg] for reg in range(14, 31)])
        return game, before

    def test_field_reaches_the_best_chemistry_with_the_fewest_moves(self):
        for seed in (1, 2, 3):
            game, before = self.run_field(seed)
            key, moves = best_field(game.chem, before)
            self.assertEqual(field_key(game.chem, game.at), key, seed)
            self.assertEqual(sum(game.at[p] != before[p] for p in range(2, 9)), moves, seed)
            self.assertLessEqual(len(game.swaps), 6)
            self.assertEqual((game.at[0], game.at[1]), (before[0], before[1]))       # pitcher and catcher stay

    def test_field_stays_on_a_tie(self):
        game, before = self.run_field(4, chem_values=(1,))                   # all neutral: nothing is better
        self.assertEqual((game.swaps, game.at), ([], before))

    def test_field_is_stock_off_the_custom_level(self):
        for flag, level in ((0, 0), (1, 1)):
            game, before = self.run_field(1, flag=flag, level=level)
            self.assertEqual((game.swaps, game.at), ([], before))


class PitchTests(unittest.TestCase):
    def run_pitch(self, seed, candidates, stats, pick=0, flag=1, level=0):
        """``candidates``: positions whose players may come in; ``stats``: {position: (curve, stamina, max)}."""
        sim, pitch, _field = subs(flag)
        game = Game(sim, random.Random(seed))
        game.pick = pick
        sim.w32(CTL + bh.CTL_LEVEL + 4 * game.SIDE, level)
        for pos in range(9):
            game.stats(game.at[pos], *stats.get(pos, (50, 100, 100)))
        for reg, value in SAVED.items():
            sim.r[reg] = value
        sim.r[29], sim.r[30], sim.r[31] = CTL, GAME, game.TEAM
        sim.r[3] = 0x7700 | len(candidates)              # the count is the low byte
        for i, pos in enumerate(candidates):
            sim.w8(STACK + 0x20 + i, game.at[pos])
        sim.w16(CTL + 0x18, 0xFFFF)
        stop = sim.run(pitch, {bh.PITCH_RET, bh.PITCH_BACK})
        return sim, game, stop

    def score(self, game, slot) -> int:
        top = game.max_stamina[slot]
        factor = 256 if top <= 0 else max(min((game.stamina[slot] << 8) // top, 256), 128)
        return (max(game.curve[slot], 1) * factor) >> 8

    def expected_pool(self, game, candidates) -> list[int]:
        """The slots the draw is among, in order (the stub's rules in plain code)."""
        scores = {}
        for pos in candidates:
            if pos == 1 and len(candidates) > 1:
                continue
            scores[game.at[pos]] = self.score(game, game.at[pos])
        best = max(scores.values())
        order = [game.at[pos] for pos in candidates if game.at[pos] in scores]
        pool = sorted((s for s in order if scores[s] >= (best * bh.BAND) >> 8),
                      key=lambda s: (-scores[s], order.index(s)))[:bh.TOP]
        if len(pool) < 2:
            return pool
        keys = []
        for slot in pool:
            at = dict(game.at)
            pos = next(p for p, x in at.items() if x == slot)
            at[pos] = at[0]                              # the old pitcher takes his position
            keys.append(best_field(game.chem, at)[0])
        return [slot for slot, key in zip(pool, keys) if key == max(keys)]

    def check(self, seed, candidates, stats, pick=0):
        sim, game, stop = self.run_pitch(seed, candidates, stats, pick)
        self.assertEqual(stop, bh.PITCH_RET)
        pool = self.expected_pool(game, candidates)
        self.assertEqual(game.rolls, [len(pool)], (seed, candidates))
        chosen = pool[pick % len(pool)]
        self.assertEqual(sim.u16(CTL + 0x18), next(p for p, x in game.at.items() if x == chosen))
        self.assertEqual((sim.r[3], sim.r[1]), (1, STACK))
        self.assertEqual([sim.r[reg] for reg in range(20, 32)],
                         [SAVED[reg] for reg in range(20, 29)] + [CTL, GAME, game.TEAM])
        return game, pool

    def test_pitch_draws_among_the_best_by_curve_and_stamina(self):
        # One clear best: the others are outside the band.
        _game, pool = self.check(1, [3, 4, 5, 6], {3: (40, 100, 100), 4: (90, 100, 100), 5: (60, 100, 100),
                                                    6: (70, 100, 100)})
        self.assertEqual(len(pool), 1)
        # A tired ace scores half: the fresh arm wins.
        game, pool = self.check(2, [2, 7], {2: (100, 10, 100), 7: (80, 100, 100)})
        self.assertEqual(pool, [game.at[7]])
        # The catcher is dropped, unless he is the only candidate.
        game, pool = self.check(3, [1, 4], {1: (99, 100, 100), 4: (20, 100, 100)})
        self.assertEqual(pool, [game.at[4]])
        game, pool = self.check(3, [1], {1: (99, 100, 100)})
        self.assertEqual(pool, [game.at[1]])

    def test_pitch_keeps_the_candidates_that_leave_the_best_field(self):
        stats = {pos: (80, 100, 100) for pos in range(9)}
        for seed, pick in ((5, 0), (6, 1), (7, 2)):
            self.check(seed, [2, 5, 8], stats, pick)

    def test_pitch_without_candidates_returns_none(self):
        sim, game, stop = self.run_pitch(1, [], {})
        self.assertEqual((stop, sim.r[3], sim.r[1], game.rolls, sim.u16(CTL + 0x18)),
                         (bh.PITCH_RET, 0, STACK, [], 0xFFFF))

    def test_pitch_is_stock_off_the_custom_level(self):
        for flag, level in ((0, 0), (1, 3)):
            sim, game, stop = self.run_pitch(1, [3, 4], {}, flag=flag, level=level)
            self.assertEqual((stop, game.rolls, sim.r[1]), (bh.PITCH_BACK, [], STACK))
            self.assertEqual(sim.r[11], (game.TEAM * 0x4FE) & 0xFFFFFFFF)    # the site's own mulli


# -- batter track: the ball's flight as plain numbers ------------------------------------------------------------

DECAY_60 = 0.995


class Ball:
    """The pitch's state as the pitcher object holds it."""

    def __init__(self, x, z, vx, vz, step=0.0, drag=0.0, limit=0.0, human=False, extra=0.0, prev=None):
        self.x, self.z, self.vx, self.vz, self.step, self.drag, self.limit = x, z, vx, vz, step, drag, limit
        self.human, self.extra = human, extra
        self.prev = prev or (x - vx - step, z - vz)

    def write(self, sim: Sim) -> None:
        for offset, value in ((0x38, self.x), (0x40, self.z), (0x44, self.prev[0]), (0x4C, self.prev[1]),
                              (0x50, self.vx), (0x58, self.vz), (0x94, self.step), (0xB4, self.drag),
                              (0xAC, self.limit), (0x188, self.extra), (0xD4, 9.5)):
            sim.wfloat(PITCHER + offset, value)
        sim.w32(PITCHER + 0x34, 0 if self.human else 1)

    def at_plane(self, plane: float):
        """x where the ball crosses z = ``plane`` (None: never), by the game's flight model."""
        x, z, vx, vz = self.x, self.z, self.vx, self.vz
        px, pz = self.prev
        if z >= plane:
            for _ in range(bh.FORECAST_FRAMES):
                if z <= self.limit:
                    slowed = vz - (self.drag + (self.extra if self.human else 0.0)) * vz
                    if slowed < -0.05:
                        vz, vx = slowed, vx - self.drag * vx
                vx, vz = vx * DECAY_60, vz * DECAY_60
                px, pz = x, z
                x, z = x + self.step + vx, z + vz
                if z < plane:
                    break
            else:
                return None
        if pz - z <= 0:
            return None
        return px + (x - px) * (pz - plane) / (pz - z)


def track_machine(ball: Ball, flag=1, tier=3, vtable=bh.BAT_VTABLE, star=0):
    stubs = bh.track_stubs(CODE)
    sim = machine(stubs, flag)
    ball.write(sim)
    sim.w8(PITCHER + bh.P_STAR, star)
    sim.w32(AI, vtable)
    sim.w8(AI + bh.AI_TIER, tier)
    sim.w8(AI + bh.AI_DECOY, 0x2A)
    sim.w8(AI + 0x3F, 0x11)
    sim.w32(R13 + bh.BATTER_PTR, 0)                      # the first pointer is empty: the other one is used
    sim.w32(R13 + bh.BATTER_PTR_ALT, BATTER)
    sim.wfloat(BATTER + 0x40, 0.72)
    sim.w8(R13 + bh.HZ, 1)
    sim.wfloat(R2 + bh.DECAY + 4, DECAY_60)
    sim.wfloat(R2 + bh.SPEED_FLOOR, -0.05)
    sim.wfloat(R2 + bh.F_ZERO, 0.0)
    for i, value in enumerate((-0.55, 0.55, 1.12, 0.32)):
        sim.wfloat(bh.ZONE + 4 * i, value)
    return sim, stubs.labels


class TrackTests(unittest.TestCase):
    BALLS = {
        'middle': Ball(0.0, 6.0, 0.0, -0.4),
        'outside': Ball(0.8, 6.0, 0.0, -0.4),
        'breaks out late': Ball(0.2, 3.0, 0.0, -0.4, step=0.06),          # a strike at z = 1.12 only
        'breaks in late': Ball(0.75, 3.0, 0.0, -0.4, step=-0.04),         # a strike at z = 0.32 only
        'drag': Ball(-0.3, 12.0, 0.01, -0.45, step=0.004, drag=0.002, limit=18.0, human=True, extra=0.001),
        'past the first plane': Ball(0.3, 0.9, 0.0, -0.4, step=0.02),
    }

    def test_forecast_follows_the_flight_model(self):
        for name, ball in self.BALLS.items():
            for plane in (1.12, 0.72, 0.32):
                sim, labels = track_machine(ball)
                sim.r[3], sim.f[1] = PITCHER, plane
                sim.run(labels['forecast'], {RETURN})
                self.assertEqual(sim.r[3], 1, name)
                self.assertAlmostEqual(sim.f[1], ball.at_plane(plane), places=4, msg=f'{name} at {plane}')
        sim, labels = track_machine(Ball(0.0, 6.0, 0.0, 0.0))                # a ball that stands still
        sim.r[3], sim.f[1] = PITCHER, 1.12
        sim.run(labels['forecast'], {RETURN})
        self.assertEqual(sim.r[3], 0)

    def take(self, ball, frame=31, swing_at=30, hold=0, **gate):
        sim, labels = track_machine(ball, **gate)
        sim.r[29], sim.r[30], sim.r[31] = AI, frame, PITCHER
        sim.w8(AI + bh.AI_SWING_AT, swing_at)
        sim.w8(AI + bh.AI_HOLD, hold)
        stop = sim.run(labels['take_stub'], {bh.SWING_CHECK, bh.TAKE_BACK})
        return sim, stop, sim.u8(AI + bh.AI_NO_SWING)

    def test_take_is_the_umpires_two_plane_rule_on_the_swing_frame(self):
        for name, ball in self.BALLS.items():
            xs = [ball.at_plane(1.12), ball.at_plane(0.32)]
            strike = any(-0.55 <= x <= 0.55 for x in xs)
            _sim, stop, taken = self.take(ball)
            self.assertEqual((stop, taken), (bh.SWING_CHECK, 0 if strike else 1), (name, xs))
        self.assertEqual([self.take(self.BALLS[k])[2] for k in ('middle', 'outside', 'breaks out late',
                                                                'breaks in late')], [0, 1, 0, 0])
        self.assertEqual(self.take(self.BALLS['outside'], frame=30)[1:], (bh.SWING_CHECK, 0))    # not yet
        self.assertEqual(self.take(self.BALLS['outside'], swing_at=0, frame=1)[2], 0)
        self.assertEqual(self.take(self.BALLS['middle'], hold=1)[2], 1)

    def test_take_is_stock_off_the_custom_level(self):
        for gate in ({'flag': 0}, {'tier': 2}, {'vtable': 0x80640000}, {'star': 1}):
            sim, stop, taken = self.take(self.BALLS['outside'], **gate)
            self.assertEqual((stop, taken, sim.r[3]), (bh.TAKE_BACK, 0, 0x11), gate)

    def track(self, ball, **gate):
        sim, labels = track_machine(ball, **gate)
        sim.r[28], sim.r[29], sim.r[30], sim.r[0] = AI, BATTER, PITCHER, 0x99
        self.assertEqual(sim.run(labels['track_stub'], {bh.TRACK_BACK}), bh.TRACK_BACK)
        self.assertEqual(sim.r[0], 0x2A)                                      # the decoy byte, loaded again
        return sim.f[3]

    def test_track_aims_the_bat_at_the_forecast_for_the_batters_z(self):
        for name, ball in self.BALLS.items():
            self.assertAlmostEqual(self.track(ball), ball.at_plane(0.72), places=4, msg=name)
        self.assertAlmostEqual(self.track(Ball(0.0, 6.0, 0.0, 0.0)), 9.5)    # no crossing: the game's own value
        for gate in ({'flag': 0}, {'tier': 0}, {'vtable': 0x80640000}, {'star': 1}):
            self.assertAlmostEqual(self.track(self.BALLS['outside'], **gate), 9.5)


# -- the DOL ------------------------------------------------------------------------------------------------------

class DeployTests(unittest.TestCase):
    def setUp(self):
        self.image = dolfile.DolImage(game_dol(behaviour_sites=True))
        self.stock = self.image.to_bytes()

    def words(self) -> dict:
        return {site: self.image.u32(site) for site in bh.SITES}

    def test_every_behaviour_switch_has_code(self):
        self.assertEqual(set(bh.BY_FEATURE), {s.key for s in settings.SWITCHES if s.gated})

    def test_hooks_branch_into_the_block_and_stubs_return_to_the_game(self):
        engine.apply_dol(self.image, engine.Record('Custom Level', dict(EVERY)))
        base = engine.block_address(self.image)
        capacity = engine._header(self.image, base)[0]
        for site in bh.SITES:
            target = ppc.branch_target(self.image.u32(site), site)
            self.assertTrue(base <= target < base + capacity, hex(site))
            self.assertEqual(self.image.u32(site) & 1, int(site in pitching.LINKED))     # a call only there
        self.assertEqual(engine.hooked(self.image), list(EVERY))
        self.assertEqual(engine.detect(self.image).edits, EVERY)
        # The stock path of a one-word hook: the game's word, then back behind the site.
        for site, stock in ((bh.BUNT, bh.BUNT_STOCK), (bh.CHARGE, bh.CHARGE_STOCK),
                            (bh.STAR_PITCH, bh.STAR_PITCH_STOCK)):
            at = ppc.branch_target(self.image.u32(site), site)
            code = [self.image.u32(at + 4 * i) for i in range(40)]
            i = code.index(stock)
            self.assertEqual(ppc.branch_target(code[i + 1], at + 4 * (i + 1)), site + 4)

    def test_a_switch_off_leaves_its_sites_stock(self):
        engine.apply_dol(self.image, engine.Record('Custom Level', {'cpu-no-bunts': True}))
        self.assertEqual(engine.hooked(self.image), ['cpu-no-bunts'])
        self.assertEqual([s for s, w in self.words().items() if w != bh.SITES[s]], [bh.BUNT])
        engine.apply_dol(self.image, engine.Record('Custom Level', {'cpu-cursed-ball': True}))      # a redeploy
        self.assertEqual(engine.hooked(self.image), ['cpu-cursed-ball'])
        self.assertEqual(self.image.u32(bh.BUNT), bh.BUNT_STOCK)

    def test_the_block_grows_and_shrinks_where_it_is(self):
        engine.apply_dol(self.image, engine.Record('Custom Level', {}))
        base = engine.block_address(self.image)
        log = engine.apply_dol(self.image, engine.Record('Custom Level', dict(EVERY)))
        self.assertEqual(engine.block_address(self.image), base)
        self.assertIn(f'behaviour code hooked for {len(EVERY)} switches', '\n'.join(log))
        self.assertGreater(engine._header(self.image, base)[0], engine.CAPACITY)
        engine.apply_dol(self.image, engine.Record('Custom Level', {}))      # smaller again
        self.assertEqual(engine._header(self.image, base)[0], engine.CAPACITY)
        self.assertEqual((engine.block_address(self.image), engine.hooked(self.image)), (base, []))
        self.assertEqual(self.words(), bh.SITES)

    def test_off_puts_every_word_back(self):
        engine.apply_dol(self.image, engine.Record('Custom Level', dict(EVERY)))
        self.assertTrue(engine.unhook(self.image))
        self.assertEqual(self.words(), bh.SITES)
        go.remove(self.image, [])
        self.assertEqual(self.image.to_bytes(), self.stock)

    def test_another_patch_at_a_site_refuses_only_while_its_switch_is_on(self):
        self.image.write_word(bh.CURSED, 0x60000000)                         # the "cursed ball for all" patch
        with self.assertRaises(engine.CustomCpuError) as caught:
            engine.apply_dol(self.image, engine.Record('Custom Level', {'cpu-cursed-ball': True}))
        self.assertIn('Charged pitches weaken your hits', str(caught.exception))
        engine.apply_dol(self.image, engine.Record('Custom Level', {'cpu-no-bunts': True}))
        self.assertEqual(self.image.u32(bh.CURSED), 0x60000000)
        engine.unhook(self.image)
        self.assertEqual(self.image.u32(bh.CURSED), 0x60000000)              # not ours: left alone

    def test_a_changed_word_a_stub_rests_on_refuses(self):
        self.image.write_word(bh.SUBS_RELIES[0][0], 0x60000000)
        with self.assertRaises(engine.CustomCpuError):
            engine.apply_dol(self.image, engine.Record('Custom Level', {'cpu-subs': True}))

    def test_behaviours_survive_a_section_rebuild(self):
        go.apply(self.image, ['close_play_50'])
        record = engine.Record('Custom Level', dict(EVERY, star_top=90))
        engine.apply_dol(self.image, record)
        go.remove(self.image, ['close_play_50'])
        self.assertEqual(engine.detect(self.image), record)
        self.assertEqual(engine.hooked(self.image), list(EVERY))
        base = engine.block_address(self.image)
        target = ppc.branch_target(self.image.u32(bh.SWING), bh.SWING)
        expected = bh.swing_stub(target, const(record.edits)).assemble()
        self.assertEqual(self.image.read(target, len(expected)), expected)
        self.assertGreater(target, base)


if __name__ == '__main__':
    unittest.main()
