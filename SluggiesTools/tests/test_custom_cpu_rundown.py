"""The custom CPU level's rundown plan (SluggiesTools/CustomCpu/rundown.py), run in the test interpreter against
stand-ins for the game's planner, fielders and runner."""

import struct
import unittest

from SluggiesTools.CustomCpu import behaviour_base as base
from SluggiesTools.CustomCpu import rundown as rd
from SluggiesTools.CustomCpu import settings
from SluggiesTools.Dol import ppc
from SluggiesTools.tests.ppc_sim import Sim, s32

CODE = 0x807B8000
R13, R2, STACK = 0x80700000, 0x80710000, 0x80680000
AI, RUNNER, FIELD, HOLDER, COVER, VTABLE, PLAY_OBJ, GAME, SETTINGS, RNG_OBJ = (
    0x80610000 + 0x1000 * i for i in range(10))
ABILITY_FN = 0x80002000
RUNNER_INDEX = 1
HOLDER_ID, COVER_ID = 5, 7
UNSET = 0x5A5A5A5A


class Play:
    """A rundown moment: one runner, the ball holder, the game's functions as plain values."""

    def __init__(self, edits=None, flag=1, direction=3, frac=0.5, vel=-0.15, ability=0, human=True,
                 plan=(-30, 0, -1), flight=20, windup=6, run_frames=40, back=40, to=40, rand=0, holder=HOLDER_ID,
                 zone=0, stopped_for=-1):
        self.plan, self.flight, self.windup, self.run_frames, self.rand = plan, flight, windup, run_frames, rand
        self.ability = ability
        self.calls, self.rolls, self.eta_seen = [], [], None
        blob, self.hooks = rd.build(CODE, lambda key: settings.value(settings.BY_KEY[key], edits or {}))
        self.scratch = CODE + len(rd.body(CODE, CODE, lambda key: 0))
        sim = self.sim = Sim()
        sim.load(CODE, blob)
        sim.w8(base.FLAG, flag)
        sim.r[1], sim.r[2], sim.r[13] = STACK, R2, R13
        for offset, value in ((rd.ONE, 1.0), (rd.F_ZERO, 0.0), (rd.K_MAIN, 1.3), (rd.K_ALT, 1.15),
                              (rd.TAG_REACH, 1.8), (rd.ON_BASE, 0.8), (rd.NO_TURN, 0.2)):
            sim.wfloat(R2 + offset, value)
        sim.load(R2 + rd.MAGIC, struct.pack('>Q', 0x4330000080000000))
        for offset, at in ((rd.PLAY, PLAY_OBJ), (rd.GAME, GAME), (rd.SETTINGS, SETTINGS), (base.RNG, RNG_OBJ)):
            sim.w32(R13 + offset, at)
        sim.w16(PLAY_OBJ, 100)
        sim.w8(GAME + 0x2C, 1)
        sim.w8(SETTINGS + 0x24 + 1, 0 if human else 1)
        for b in range(4):
            sim.wfloat(rd.BASE_XZ + 8 * b, 10.0 * b)
            sim.wfloat(rd.BASE_XZ + 8 * b + 4, 5.0 * b)
        sim.w32(rd.HOLDER_TABLES[0] + 4 * HOLDER_ID, HOLDER)
        sim.w32(rd.HOLDER_TABLES[1] + 4 * COVER_ID, COVER)          # found in the second table
        sim.w32(HOLDER, VTABLE)
        sim.w32(VTABLE + 0x3C, ABILITY_FN)
        sim.w16(COVER + rd.ETA, 33)
        sim.w16(AI + rd.COVER + 2 * 2, COVER_ID)                    # the cover of base 2
        sim.w8(AI + 0x2D4 + RUNNER_INDEX, zone)
        sim.w16(FIELD + 0x2A4C, holder)
        sim.w8(RUNNER + 0x183, direction)
        sim.w8(RUNNER + 0x17D, 1)                                   # from 1st
        sim.w8(RUNNER + 0x17E, 2)                                   # to 2nd
        sim.w16(RUNNER + 0x138, stopped_for)
        sim.w16(RUNNER + rd.R_BACK, back)
        sim.w16(RUNNER + rd.R_ESC_TO, to)
        for offset, value in ((rd.R_TOP, 0.15), (rd.R_TOP_K, 1.0), (rd.R_MASH, 0.02), (rd.R_TURN, 0.005),
                              (rd.R_TURN_K, 1.0), (rd.R_VEL, vel), (rd.R_PAST, frac), (rd.R_FRAC, frac),
                              (rd.R_LEG, 27.0), (rd.R_SAFE, 0.03)):
            sim.wfloat(RUNNER + offset, value)
        for slot in (rd.THROW, rd.CARRY, rd.MARGIN):
            sim.w32(AI + slot + 4 * RUNNER_INDEX, UNSET)
        sim.calls.update({rd.PLAN_ONE: self.plan_one, rd.RUN_FRAMES: self.value('run_frames'),
                          rd.WINDUP: self.value('windup'), rd.FLIGHT: self.value('flight'),
                          base.RAND: self.roll, ABILITY_FN: self.value('ability')})

    def _clobber(self) -> None:
        for reg in (0, *range(4, 13)):
            self.sim.r[reg] = 0xBAD0BAD0
        self.sim.f[:14] = [1e9] * 14
        self.sim.ctr = 0xBAD

    def value(self, name: str):
        def call(sim: Sim) -> None:
            self.calls.append(name)
            self._clobber()
            value = getattr(self, name)
            sim.r[3] = value.pop(0) if isinstance(value, list) else value      # a list: one value per call
        return call

    def roll(self, sim: Sim) -> None:
        assert (sim.r[3], sim.r[4]) == (RNG_OBJ, 100)
        self.rolls.append(self.rand)
        self._clobber()
        sim.r[3] = self.rand

    def plan_one(self, sim: Sim) -> None:
        ai, index, direction, throw_at, carry_at = sim.r[3:8]
        assert (ai, index) == (AI, RUNNER_INDEX)
        self.calls.append(('plan', direction))
        self.eta_seen = sim.s16(COVER + rd.ETA)
        margin, throw, carry = self.plan
        sim.w32(throw_at, throw)
        sim.w32(carry_at, carry)
        self._clobber()
        sim.r[3] = margin & 0xFFFFFFFF

    def run(self, hook: int = rd.HOOK_OUT) -> tuple:
        """(throw, carry, margin) the body committed (``UNSET`` each when it wrote nothing)."""
        sim = self.sim
        sim.r[4], sim.r[27], sim.r[29], sim.r[30], sim.r[31] = RUNNER_INDEX, AI, 4 * RUNNER_INDEX, RUNNER, FIELD
        stop = sim.run(ppc.branch_target(self.hooks[hook], hook), {rd.EPILOGUE, hook + 4})
        self.stop = stop
        return tuple(s32(sim.u32(AI + slot + 4 * RUNNER_INDEX)) for slot in (rd.THROW, rd.CARRY, rd.MARGIN))


NOTHING = (s32(UNSET),) * 3


class RundownTests(unittest.TestCase):
    def test_off_the_custom_level_the_site_runs_its_own_word(self):
        for hook, register in ((rd.HOOK_IN, 5), (rd.HOOK_OUT, 0)):
            play = Play(flag=0)
            self.assertEqual(play.run(hook), NOTHING)
            self.assertEqual((play.stop, play.sim.r[register], play.calls), (hook + 4, 3, []))

    def test_a_runner_on_his_base_gets_no_plan(self):
        for direction, stopped_for in ((0, -1), (2, 5)):
            play = Play(direction=direction, stopped_for=stopped_for)
            self.assertEqual(play.run(), NOTHING)
            self.assertEqual((play.stop, [c for c in play.calls if c != 'ability']), (rd.EPILOGUE, []))

    def test_advancing_throws_ahead_at_once_past_three_eighths_of_the_leg(self):
        play = Play(direction=1, frac=0.5, plan=(-12, 2, -1))
        self.assertEqual(play.run(rd.HOOK_IN), (2, -1, -12))
        self.assertIn(('plan', 1), play.calls)                               # to the next base
        self.assertEqual((play.eta_seen, play.sim.s16(COVER + rd.ETA)), (-1, 33))    # "on the base" for the call
        play = Play(direction=1, frac=0.3, plan=(-12, 2, -1))
        self.assertEqual(play.run(), (12, -1, -12))                          # held: base + 10

    def test_a_runner_stopped_in_the_rundown_zone_is_chased(self):
        play = Play(direction=2, zone=1)
        self.assertEqual(play.run(), (-1, rd.CHASE, -1))

    def test_chase_when_the_holder_beats_the_runner_with_ball_dash(self):
        play = Play(direction=1, to=25, run_frames=26, ability=rd.BALL_DASH, plan=(-12, 2, -1))
        self.assertEqual(play.run(), (-1, rd.CHASE, -5))                     # 26 / 1.3 - 25
        play = Play(direction=1, to=25, run_frames=26, ability=0, plan=(-12, 2, -1))
        self.assertEqual(play.run(), (2, -1, -12))                           # 26 - 25: he gets there first

    def test_heading_back_plans_the_base_behind_and_waits_for_the_deadline(self):
        # The runner's best time: 13.5 m / 0.17 = 79 frames. Ball: 6 + 20 + 18 + 10 + 1 = 55, early + 20 = 75.
        play = Play(plan=(-30, 1, -1), rand=0)
        self.assertEqual(play.run(), (11, -1, -30))                          # held
        self.assertIn(('plan', 0), play.calls)
        self.assertEqual(play.eta_seen, 33)                                  # the cover's real arrival time
        self.assertEqual(Play({'rundown_lead': 30}, plan=(-30, 1, -1), rand=0).run(), (1, -1, -30))
        self.assertEqual(Play({'rundown_buffer': 20}, plan=(-30, 1, -1), rand=0).run(), (1, -1, -30))
        self.assertEqual(Play(plan=(-30, 1, -1), rand=0, flight=30).run(), (1, -1, -30))
        # A CPU runner's best time is his cap alone: 13.5 / 0.15 = 90 frames.
        self.assertEqual(Play({'rundown_lead': 30}, plan=(-30, 1, -1), rand=0, human=False).run(), (11, -1, -30))

    def test_the_early_roll(self):
        # The first throw of a rundown rolls against rundown_first, the others against rundown_early.
        play = Play({'rundown_lead': 30}, plan=(-30, 1, -1), rand=80)
        self.assertEqual(play.run(), (1, -1, -30))                           # 80 < 90: early, thrown
        self.assertEqual(play.rolls, [80])
        play.run()                                                           # the same holder: no new roll
        self.assertEqual(play.rolls, [80])
        play.sim.w16(FIELD + 0x2A4C, COVER_ID)                               # the throw was caught
        play.sim.w32(rd.HOLDER_TABLES[0] + 4 * COVER_ID, HOLDER)
        self.assertEqual(play.run(), (11, -1, -30))                          # 80 >= 70: the hold rule, held
        self.assertEqual(play.rolls, [80, 80])
        self.assertEqual(Play({'rundown_lead': 30, 'rundown_first': 50}, plan=(-30, 1, -1), rand=80).run()[0], 11)
        self.assertEqual(Play({'rundown_lead': 30, 'rundown_rolled': 0}, plan=(-30, 1, -1), rand=0).run()[0], 11)
        play.sim.w16(PLAY_OBJ, 5)                                            # a new play: the first throw again
        self.assertEqual(play.run(), (1, -1, -30))

    def test_without_an_early_roll_it_throws_once_the_runner_is_committed(self):
        hold = {'rand': 99, 'plan': (-30, 1, -1)}
        self.assertEqual(Play(**hold).run()[0], 11)                          # far from the base: he could turn
        # At 0.3 m a frame he needs 9 m to stop. 0.45 of the leg left (12.15 m): he stops 3.15 m out, past the
        # 1.0 m tag line. 0.3 of the leg (8.1 m): he cannot. Neither is at the deadline yet (47 frames against 31).
        fast = dict(hold, vel=-0.3, windup=0, flight=2)
        self.assertEqual(Play(frac=0.45, **fast).run()[0], 11)
        self.assertEqual(Play(frac=0.3, **fast).run()[0], 1)
        self.assertEqual(Play(frac=0.3, **dict(fast, vel=0.1)).run()[0], 11)                    # moving forward
        # The out is slipping away: a CPU runner with a margin of 0 or more is thrown at.
        self.assertEqual(Play(human=False, rand=99, plan=(3, 1, -1)).run()[0], 1)

    def test_ball_dash_carries_the_ball_to_the_base_behind(self):
        # No chase (60 frames to the runner's line) but the dash reaches the base first:
        # 39 / 1.3 = 30 against 40 * 0.15 / 0.17 = 35.3.
        play = Play(ability=rd.BALL_DASH, run_frames=[60, 39], back=40, plan=(-30, 1, -1))
        self.assertEqual(play.run(), (-1, 1, -5))
        self.assertEqual(Play(ability=0, run_frames=[60, 39], back=40, plan=(-30, 1, -1)).run()[1], -1)


if __name__ == '__main__':
    unittest.main()
