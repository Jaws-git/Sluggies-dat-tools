"""The custom CPU level's item code (SluggiesTools/CustomCpu/item_defense.py, dive.py, items.py): their settings,
a few stubs run in the test interpreter, and their place in the DOL."""

import struct
import unittest

from SluggiesTools.CustomCpu import behaviour_base as base
from SluggiesTools.CustomCpu import behaviours, defense_block, dive, engine, item_defense, items, settings
from SluggiesTools.Dol import dolfile, ppc
from SluggiesTools.tests.ppc_sim import Sim
from SluggiesTools.tests.test_custom_cpu import game_dol

SHARED, CODE = 0x807C2000, 0x807C3000
R13, STACK, RETURN = 0x80700000, 0x80680000, 0x80001000
FIELDER, GAME, TICK_OBJ, RNG_OBJ, VTABLE = (0x80610000 + 0x1000 * i for i in range(5))
ABILITY_FN = 0x80002000


def const(edits=None):
    return lambda key: settings.value(settings.BY_KEY[key], edits or {})


def image() -> dolfile.DolImage:
    return dolfile.DolImage(game_dol(behaviour_sites=True))


def defense(edits=None) -> tuple[bytes, dict]:
    defense_block.place(SHARED)
    return item_defense.build(CODE, const(edits), image())


def item_use(edits=None) -> tuple[bytes, dict]:
    defense_block.place(SHARED)
    return items.build(CODE, const(edits), image())


def f32(blob: bytes, address: int) -> float:
    return round(struct.unpack_from('>f', blob, address - CODE)[0], 4)


class SettingsTests(unittest.TestCase):
    def test_pass_distance_and_boo_error_rate_reach_the_fielders_data(self):
        blob, _hooks = defense()
        cbase = item_defense.BLUR + item_defense.B_SIZE + 24
        self.assertEqual(f32(blob, cbase + item_defense.C_MATE), 20.0)
        k = item_defense.blur_k(0.40)
        rows = [f32(blob, item_defense.BLUR + item_defense.B_KROW + 4 * i) for i in range(6)]
        self.assertEqual(rows, [round(1 / k, 4), round(k * k, 4)] * 3)
        blob, _hooks = defense({'items_pass': 6.5, 'boo_error_rate': 10})
        self.assertEqual(f32(blob, cbase + item_defense.C_MATE), 6.5)
        k = item_defense.blur_k(0.10)                    # fewer fielders off by more than their reach
        self.assertEqual(f32(blob, item_defense.BLUR + item_defense.B_KROW + 4), round(k * k, 4))
        self.assertGreater(k, item_defense.blur_k(0.40))

    def test_item_settings_reach_the_data_and_the_code(self):
        plain, _hooks = item_use()
        self.assertEqual([f32(plain, items.DATA + items.SPEEDS + 4 * i) for i in range(2)], [2.0, 2.4])
        self.assertEqual(f32(plain, items.DATA + items.KFFE), items.EDGE_MARGIN + 1.5)
        edits = {'items_cursor': 1.0, 'items_runner_pad': 3.0}
        blob, _hooks = item_use(edits)
        self.assertEqual([f32(blob, items.DATA + items.SPEEDS + 4 * i) for i in range(2)], [1.0, 1.2])
        self.assertEqual(f32(blob, items.DATA + items.KFFE), items.EDGE_MARGIN + 3.0)
        self.assertEqual(blob[:items.DATA - CODE], plain[:items.DATA - CODE])        # data only: the same code
        for key, value, words in (('items_error_free', 100, [ppc.one(0, lambda a: a.li(11, 100))]),
                                  ('items_timing_error', 7, [ppc.one(0, lambda a: a.li(4, 15)),
                                                             ppc.one(0, lambda a: a.li(11, 7))]),
                                  ('items_aim_error', 1.0, [ppc.one(0, lambda a: a.li(4, 9)),
                                                            ppc.one(0, lambda a: a.li(11, 4))]),
                                  ('items_lead', 22, [ppc.one(0, lambda a: a.addi(3, 3, -22))])):
            blob, _hooks = item_use({key: value})
            changed = {struct.unpack_from('>I', blob, i)[0] for i in range(0, items.DATA - CODE, 4)
                       if blob[i:i + 4] != plain[i:i + 4]}
            self.assertTrue(changed, key)
            self.assertTrue(changed & set(words) or key == 'items_lead', (key, [f'{w:08X}' for w in changed]))
            self.assertEqual(len(blob), len(plain))

    def test_another_games_jump_table_is_refused(self):
        img = image()
        img.write(item_defense.JUMP_TABLE + 4, struct.pack('>f', 0.0))       # no gravity: the game's never is
        defense_block.place(SHARED)
        with self.assertRaises(dolfile.DolError):
            item_defense.build(CODE, const(), img)


# -- stubs ------------------------------------------------------------------------------------------------------

def machine(blob: bytes, flag: int = 1) -> Sim:
    sim = Sim()
    sim.load(CODE, blob)
    sim.w8(base.FLAG, flag)
    sim.r[1], sim.r[13], sim.lr = STACK, R13, RETURN
    sim.w32(R13 + dive.TICK, TICK_OBJ)
    sim.w32(R13 + dive.GAME, GAME)
    sim.w32(R13 + item_defense.RNG, RNG_OBJ)
    sim.w16(TICK_OBJ + 2, 50)                            # frames since contact
    sim.w32(item_defense.FIELDERS[0], FIELDER)
    sim.w32(FIELDER, VTABLE)
    sim.w32(VTABLE + 0x3C, ABILITY_FN)
    sim.w8(FIELDER + dive.F_ID, 4)
    return sim


class DiveTests(unittest.TestCase):
    def press(self, flag=1, level=0, planned=None, plan=0, normal_at=None):
        """Run the grounder hook's entry: (the dive is pressed, the block's AUX1 word)."""
        blob, hooks = defense()
        hook = dive.HOOKS[1][0]
        sim = machine(blob, flag)
        sim.r[30] = FIELDER
        sim.w8(FIELDER + dive.F_LEVEL, level)
        if planned is not None:
            frame, phase, chaser = planned
            sim.w16(defense_block.ADDR + defense_block.FRAME, frame)
            sim.w8(defense_block.ADDR + defense_block.PHASE, phase)
            sim.w8(defense_block.ADDR + defense_block.CHASER, chaser)
        sim.wfloat(FIELDER + dive.F_VREACH, 2.0)
        sim.wfloat(FIELDER + dive.F_HREACH, 1.0)
        sim.w16(GAME + dive.G_BOUNCED, 1)
        for i in range(40):                              # the ball's path: 10 m away, at catch height
            sim.wfloat(GAME + dive.P_X + i * dive.STRIDE, 0.0 if i == normal_at else 10.0)
            sim.wfloat(GAME + dive.P_H + i * dive.STRIDE, 1.0)

        def planner(s: Sim) -> None:
            s.w32(s.r[5], 6)                             # the dive would catch it on path frame 6
            s.r[3] = plan

        def run(s: Sim) -> None:
            s.wfloat(s.r[7], 0.5)                        # the run covers half a metre by then

        def ability(s: Sim) -> None:
            s.r[3] = 0
        sim.calls.update({dive.PLANNER: planner, dive.RUN: run, ABILITY_FN: ability})
        stop = sim.run(ppc.branch_target(hooks[hook], hook), {hook + 4, dive.OUT})
        self.assertEqual(sim.r[1], STACK)
        if stop == hook + 4:
            self.assertEqual(sim.r[0], 2)                # the site's own li r0,2: the press
        return stop == hook + 4, sim.u32(defense_block.ADDR + defense_block.AUX1)

    def test_off_the_custom_level_every_requested_dive_is_pressed(self):
        self.assertEqual(self.press(flag=0, plan=1), (True, 0))
        self.assertEqual(self.press(level=1, plan=1), (True, 0))             # a human side's fielder

    def test_a_dive_only_when_it_catches_and_the_run_does_not(self):
        self.assertEqual(self.press(plan=0), (True, 50 << 16))
        self.assertEqual(self.press(plan=2)[0], False)                       # the dive would fall short
        self.assertEqual(self.press(plan=3)[0], False)                       # the run alone gets there
        self.assertEqual(self.press(plan=0, normal_at=3)[0], False)          # an ordinary catch first
        self.assertEqual(self.press(plan=0, normal_at=30)[0], True)          # too late to matter

    def test_no_dive_while_the_attack_is_planned_for_this_fielder(self):
        self.assertEqual(self.press(planned=(49, 2, 4)), (False, (50 << 16) | 1))    # last frame's plan
        self.assertEqual(self.press(planned=(49, 2, 5))[0], True)            # another fielder's
        self.assertEqual(self.press(planned=(49, 0, 4))[0], True)            # no attack phase
        self.assertEqual(self.press(planned=(40, 2, 4))[0], True)            # a stale block


class BooTests(unittest.TestCase):
    def freeze(self, hook, flag=1, level=0):
        blob, hooks = defense()
        sim = machine(blob, flag)
        sim.w8(FIELDER + item_defense.F_LEVEL, level)
        called = []
        sim.calls[item_defense.FREEZE] = lambda s: called.append('freeze')

        def rand(s: Sim) -> None:
            called.append('rand')
            s.r[3] = 0x1234
        sim.calls[item_defense.RAND] = rand
        self.assertEqual(sim.run(ppc.branch_target(hooks[hook], hook), {hook + 4}), hook + 4)
        return called, sim

    def test_cpu_fielders_ignore_the_boo_and_the_night_star(self):
        for hook in (item_defense.BOO_HOOK, item_defense.STAR_HOOK):
            self.assertEqual(self.freeze(hook, flag=0)[0], ['freeze'])
            self.assertEqual(self.freeze(hook, level=1)[0], ['freeze'])      # a human side
            self.assertNotIn('freeze', self.freeze(hook)[0])

    def test_the_boo_draws_each_fielders_misjudgement_once(self):
        called, sim = self.freeze(item_defense.BOO_HOOK)
        self.assertEqual(called, ['rand'])
        blur = item_defense.BLUR
        self.assertEqual(sim.u8(blur + item_defense.B_DRAWN), 1)
        draws = [sim.float(blur + item_defense.B_Z + 4 * i) for i in range(18)]
        self.assertTrue(all(abs(z) < 6 for z in draws) and len(set(draws)) > 12, draws)      # 18 normals
        k2 = sim.float(blur + item_defense.B_KROW + 4)
        misses = [sim.u8(blur + item_defense.B_MISS + i) for i in range(9)]
        self.assertEqual(misses, [int(draws[2 * i] ** 2 + draws[2 * i + 1] ** 2 > k2) for i in range(9)])


# -- the DOL ----------------------------------------------------------------------------------------------------

class DeployTests(unittest.TestCase):
    def test_either_switch_brings_the_shared_block(self):
        for feature, module in ((item_defense.FEATURE, item_defense), (items.FEATURE, items)):
            img = image()
            engine.apply_dol(img, engine.Record('Custom Level', {feature: True}))
            self.assertEqual(engine.hooked(img), [feature])
            block_at = engine.block_address(img)
            self.assertTrue(block_at < defense_block.ADDR < defense_block.ROUTE < block_at + 0x400)
            self.assertEqual(defense_block.ADDR % defense_block.BLOCK_ALIGN, 0)
            self.assertEqual(img.read(defense_block.ROUTE, 12 * 9), b'\x80' * 108)
            for site, _stock in module.HOOKS:
                self.assertEqual(img.u32(site) >> 26, 18, hex(site))
            engine.unhook(img)
            self.assertEqual([hex(s) for s, stock in module.HOOKS if img.u32(s) != stock], [])

    def test_all_eleven_behaviours_fit_and_come_off(self):
        img = image()
        stock = {site: img.u32(site) for site in behaviours.SITES}
        every = {b.feature: True for b in behaviours.BEHAVIOURS}
        self.assertEqual(len(every), 11)
        log = engine.apply_dol(img, engine.Record('Custom Level', every))
        self.assertIn('behaviour code hooked for 11 switches', log[-1])
        self.assertEqual(engine.hooked(img), list(every))
        self.assertEqual(engine.detect(img).edits, every)
        engine.unhook(img)
        self.assertEqual({site: img.u32(site) for site in behaviours.SITES}, stock)


if __name__ == '__main__':
    unittest.main()
