"""The custom CPU level's pitch model (SluggiesTools/CustomCpu/pitching.py): its tables from the characters'
stats, its settings, the small stubs run in the test interpreter, and its place in the DOL."""

import struct
import unittest

from SluggiesTools.CustomCpu import behaviour_base as base
from SluggiesTools.CustomCpu import engine, settings
from SluggiesTools.CustomCpu import pitching as pm
from SluggiesTools.Dol import dolfile, inventory, ppc
from SluggiesTools.Roster import dol_hammerspace
from SluggiesTools.tests.ppc_sim import Sim
from SluggiesTools.tests.test_custom_cpu import PITCHERS, game_dol

CODE, DATA = 0x807C2000, 0x807D0000
R13, STACK, RETURN = 0x80700000, 0x80680000, 0x80001000
GAME, BATTER, PITCHER, BRAIN, RNG_OBJ = (0x80610000 + 0x1000 * i for i in range(5))
ON = {pm.FEATURE: True}


def const(edits=None):
    return lambda key: settings.value(settings.BY_KEY[key], edits or {})


def image() -> dolfile.DolImage:
    return dolfile.DolImage(game_dol(behaviour_sites=True))


def model(edits=None):
    """(the image, the model's data, its offsets) for the synthetic game."""
    img = image()
    stats = inventory.table('stats')
    data, at, _tf = pm.data_blob(img, const(edits), stats.address + stats.header,
                                 inventory.table('changeup').address, pm.ROWS_STOCK)
    return img, data, at


def floats(data: bytes, at: int, count: int) -> list[float]:
    return [round(x, 4) for x in struct.unpack_from(f'>{count}f', data, at)]


class TableTests(unittest.TestCase):
    def test_type_priors_follow_the_pitchers_stats(self):
        _img, data, at = model()
        # fastball, changeup, plain curve, charged curve
        self.assertEqual(floats(data, at['pri'], 4), [0.35, 0.04, 0.15, 0.46])       # charge pitch speed 160
        self.assertEqual(floats(data, at['pri'] + 16, 4), [0.03, 0.30, 0.15, 0.52])  # a changeup that floats
        self.assertEqual(floats(data, at['pri'] + 32, 4), [0.0, 0.04, 0.15, 0.81])   # slower than his curveball
        self.assertEqual(floats(data, at['pri'] + 48, 4), [0.0, 0.0, 0.15, 0.85])    # an empty row
        self.assertEqual(at['perid'] - at['pri'], 16 * pm.ROWS_STOCK)

    def test_the_mix_settings_move_the_priors(self):
        _img, data, at = model({'fastball_speed': 110, 'fastball_top': 50, 'fastball_low': 10, 'plain_curves': 30})
        self.assertEqual(floats(data, at['pri'], 4), [0.5, 0.04, 0.3, 0.16])
        self.assertEqual(floats(data, at['pri'] + 16, 4), [0.5, 0.3, 0.2, 0.0])       # plain curves take the rest
        self.assertEqual(floats(data, at['pri'] + 32, 4), [0.0, 0.04, 0.3, 0.66])

    def test_per_character_rows(self):
        _img, data, at = model()
        rows = [struct.unpack_from('>fBB', data, at['perid'] + 8 * i) for i in range(pm.ROWS_STOCK + 1)]
        self.assertEqual([(band, weak) for _edge, band, weak in rows[:3]], [(3, 0), (0, 1), (2, 0)])
        self.assertAlmostEqual(rows[0][0], 0.5 - 0.21, places=5)                     # |F4| - (reach + 1) / 100
        self.assertEqual(rows[-1], (9.0, 1, 0))                                      # the row of an unknown id
        bends = floats(data, at['bend'], 3)
        self.assertTrue(0 < bends[0] < bends[1] <= pm.BEND_CAP)          # the slower pitch has more frames to bend
        self.assertEqual(bends[2], 0.0)                                              # no curve stat

    def test_settings_become_parameters_and_constants(self):
        edits = {'model_learning': 0.1, 'model_randomness': 2.0, 'model_randomness_bs': 0.5, 'model_patience': 8.0,
                 'model_floor': 12, 'cap3_0': 20, 'cap3_1': 10, 'cap3_2': 1, 'far_ball': 1.0, 'zone_first': 60,
                 'zone_after_take': 50, 'zone_after_chase': 10, 'hbp_margin': 0.3}
        _img, data, at = model(edits)

        def param(name):
            return struct.unpack_from('>i' if name in pm.INT_PARAMS else '>f', data, at['params'] + pm.P_[name])[0]
        self.assertEqual([round(param(n), 4) for n in pm.PARAM_NAMES],
                         [0.1, 2.0, 0.5, 8.0, 0.4, 1.0, 0.05, 0.95, 12, 20, 10, 1, 0])
        self.assertEqual([floats(data, pm.K[n], 1)[0] for n in ('P0_FIRST', 'P0_TAKEN', 'P0_CHASE', 'MARGIN')],
                         [0.4, 0.5, 0.9, 0.3])
        _img, data, at = model(dict(edits, never_walk=True))
        self.assertEqual([param(n) for n in ('CAP3', 'CAP3_1', 'CAP3_2')], [0, 0, 0])
        _img, data, at = model()
        self.assertEqual([round(param(n), 4) for n in ('ALPHA', 'FAR_HI', 'FLOOR', 'CAP3')], [0.04, 0.94, 5, 5])

    def test_tables_follow_a_roster_s_moved_tables(self):
        img = image()
        hs = dol_hammerspace.DolHammerspace.open_or_create(img)
        stats, changeup = inventory.table('stats'), inventory.table('changeup')
        row = bytearray(stats.header + pm.STATS_ROW * 0x80)
        struct.pack_into('>HH', row, stats.header + 0x70 * pm.STATS_ROW + pm.STAT_CURVEBALL, 100, 170)
        moved = {'stats': hs.data.put(bytes(row), 32), 'changeup': hs.data.put(bytes(8 * 0x80), 32)}
        hs.commit()
        for name, at in moved.items():
            lis_site, addi_site = inventory.table(name).pairs[0]
            img.write_word(lis_site, ppc.one(lis_site, lambda a: a.lis(3, ppc.ha(at))))
            img.write_word(addi_site, ppc.one(addi_site, lambda a: a.addi(3, 3, ppc.lo(at))))
        blob, _hooks = pm.build_for(CODE, const(), img)
        size = len(pm.code(CODE, {n: CODE for n in ('consts', 'params', 'pri', 'perid', 'bend', 'state', 'glide')},
                           pm.ROWS_MOVED, 0)[0])
        _data, at, _tf = pm.data_blob(img, const(), moved['stats'] + stats.header, moved['changeup'], pm.ROWS_MOVED)
        self.assertEqual(at['perid'] - at['pri'], 16 * pm.ROWS_MOVED)
        self.assertEqual(floats(blob, size + at['pri'] + 16 * 0x70, 4), [0.35, 0.04, 0.15, 0.46])    # a new id's row
        self.assertEqual(floats(blob, size + at['pri'], 4), [0.0, 0.0, 0.15, 0.85])    # id 0 is not in the new table
        self.assertEqual(floats(blob, size + at['pri'] + 16 * 0xF0, 4), [0.0, 0.0, 0.15, 0.85])   # past its end
        self.assertNotEqual(changeup.address, moved['changeup'])

    def test_a_game_with_other_flight_numbers_is_refused(self):
        img = image()
        img.write(pm.OPEN, struct.pack('>f', 9.0))
        with self.assertRaises(pm.PitchingError):
            pm.build_for(CODE, const(), img)


# -- stubs ------------------------------------------------------------------------------------------------------

def machine(stub: ppc.Asm, flag: int = 1) -> Sim:
    sim = Sim()
    sim.load(stub.base, stub.assemble())
    sim.w8(base.FLAG, flag)
    sim.r[1], sim.r[13], sim.lr = STACK, R13, RETURN
    for offset, at in ((pm.G_PTR, GAME), (pm.BATTER, BATTER), (pm.PITCHER, PITCHER), (pm.RNG, RNG_OBJ)):
        sim.w32(R13 + offset, at)
    return sim


class StubTests(unittest.TestCase):
    def pick(self, scores, priors, draw, temperature=1.0):
        _img, data, _at = model()
        sim = machine(pm.pick_fn(CODE, DATA))
        sim.load(DATA, data)
        for i, (score, prior) in enumerate(zip(scores, priors)):
            sim.wfloat(STACK + 0x100 + 4 * i, score)
            sim.wfloat(STACK + 0x140 + 4 * i, prior)
        sim.r[3], sim.r[4], sim.r[5], sim.f[1] = STACK + 0x100, STACK + 0x140, len(scores), temperature

        def uniform(s: Sim) -> None:
            assert s.r[3] == RNG_OBJ and (s.f[1], s.f[2]) == (0.0, 1.0)
            s.f[1] = draw
        sim.calls[pm.UNIFORM] = uniform
        sim.run(CODE, {RETURN})
        self.assertEqual(sim.r[1], STACK)
        return sim.r[3]

    def test_pick_draws_by_prior_times_two_to_the_score(self):
        priors = [0.35, 0.05, 0.15, 0.45]
        self.assertEqual([self.pick([0.0] * 4, priors, u) for u in (0.0, 0.34, 0.36, 0.41, 0.54, 0.56, 0.9999)],
                         [0, 0, 1, 2, 2, 3, 3])                              # scores 0: the priors themselves
        # A score of +2 quadruples the changeup's weight: 0.35, 0.20, 0.15, 0.45 of 1.15.
        scores = [0.0, 2.0, 0.0, 0.0]
        self.assertEqual([self.pick(scores, priors, u) for u in (0.30, 0.31, 0.47, 0.49, 0.60, 0.62)],
                         [0, 1, 1, 2, 2, 3])
        # A higher temperature flattens the scores: 2^(2 / 2) doubles it only.
        self.assertEqual(self.pick(scores, priors, 0.44, temperature=2.0), 2)
        self.assertEqual(self.pick(scores, priors, 0.44), 1)
        self.assertEqual(self.pick([-100.0, 0.0], [0.5, 0.5], 0.01), 1)      # scores are clamped, not infinite

    def test_reset_zeroes_both_teams_state(self):
        sim = machine(pm.reset_stub(CODE, DATA))
        sim.load(DATA - 4, b'\xAA' * (2 * pm.SIZE + 8))
        sim.r[3], sim.r[5] = BRAIN, 2
        sim.run(CODE, {RETURN})
        self.assertEqual(sim.read(DATA, 2 * pm.SIZE), bytes(2 * pm.SIZE))
        self.assertEqual((sim.read(DATA - 4, 4), sim.read(DATA + 2 * pm.SIZE, 4)), (b'\xAA' * 4, b'\xAA' * 4))
        self.assertEqual(sim.u8(BRAIN + pm.LEVEL), 2)                        # the site's own store

    def test_the_umpire_planes_record_what_the_batting_team_saw(self):
        for team, caller, plane in ((0, pm.PLANE1 + 4, 1), (1, pm.PLANE2 + 4, 2)):
            sim = machine(pm.plane_stub(CODE, DATA), flag=0)                 # at every level: it only records
            sim.w8(GAME + pm.TEAM, team)
            sim.r[29], sim.lr = PITCHER, caller
            sim.w8(PITCHER + pm.ZONE_FLAG, 1)
            sim.wfloat(PITCHER + 0x38, 0.31)
            sim.wfloat(PITCHER + 0x5C, 0.12)
            sim.w8(BATTER + pm.SWING_FLAG, 1)
            sim.run(CODE, {caller})
            block = DATA + team * pm.SIZE
            self.assertEqual((sim.u8(block + pm.SEEN), sim.u8(block + pm.ZONE), sim.u8(block + pm.SWUNG)), (1, 1, 1))
            self.assertAlmostEqual(sim.float(block + pm.XACT), 0.31, places=6)
            self.assertAlmostEqual(sim.float(block + pm.XAIM), 0.12, places=6)
            self.assertEqual(sim.r[0], plane)                                # the site's own li r0
            self.assertEqual(sim.read(DATA + (1 - team) * pm.SIZE, pm.SIZE), bytes(pm.SIZE))

    def test_contact_and_hit_batter_are_recorded(self):
        sim = machine(pm.contact_stub(CODE, DATA))
        sim.r[29] = BATTER
        sim.w8(BATTER + 0xBA, 2)
        sim.w16(BATTER + 0x84, 40)                                           # past the table: held at 15
        sim.w8(BATTER + 0xB6, 1)
        sim.w32(BATTER + 0x60, 0x42C80000)
        sim.w32(R13 + pm.PITCHER, 0x80123456)
        sim.run(CODE, {RETURN})
        self.assertEqual([sim.u8(DATA + field) for field in (pm.CONTACT, pm.CZONE, pm.CFRAME, pm.CTYPE)],
                         [1, 2, 15, 1])
        self.assertEqual((sim.u32(DATA + pm.CF), sim.r[3]), (0x42C80000, 0x80123456))
        sim = machine(pm.hbp_stub(CODE, DATA))
        sim.r[0], sim.r[31] = 1, PITCHER
        sim.run(CODE, {RETURN})
        self.assertEqual((sim.u8(PITCHER + 0xD1), sim.u8(DATA + pm.HBP)), (1, 1))

    def test_clamp_lets_aims_outside_the_stock_range_through(self):
        for aim, remapped in ((0.0, True), (0.4, True), (-0.4, True), (0.55, False), (-0.9, False)):
            sim = machine(pm.clamp_stub(CODE))
            sim.f[1], sim.f[2], sim.f[3] = aim, -0.4, 0.4
            self.assertEqual(sim.run(CODE, {RETURN, pm.REMAP}), pm.REMAP if remapped else RETURN, aim)

    def test_the_type_pick_is_gated(self):
        for flag, level, tutorial in ((0, pm.L4, 0), (1, 2, 0), (1, pm.L4, 1)):
            sim = machine(pm.type_stub(CODE, DATA, pm.ROWS_STOCK, DATA + 0x4000, CODE + 0x1000), flag)
            sim.r[30], sim.r[31] = BRAIN, PITCHER
            sim.w8(BRAIN + pm.LEVEL, level)
            sim.w32(R13 + pm.TUTORIAL, tutorial)
            sim.w32(R13 + pm.COUNT, 0x80655555)
            self.assertEqual(sim.run(CODE, {pm.TYPE + 4, pm.TYPE_PICKED}), pm.TYPE + 4)
            self.assertEqual(sim.r[3], 0x80655555)                           # the site's own load
            self.assertEqual(sim.u16(PITCHER + pm.MARK), 0)

    def test_the_type_pick_maps_the_drawn_type(self):
        _img, data, at = model()
        for cid, drawn, kind, marked in ((0, 0, 1, 0), (0, 1, 2, 0), (0, 2, 0, 0), (0, 3, 1, 1), (0x70, None, 0, 0)):
            sim = machine(pm.type_stub(CODE, DATA + at['pri'], pm.ROWS_STOCK, DATA + at['state'], CODE + 0x1000))
            sim.load(DATA, data)
            sim.r[30], sim.r[31] = BRAIN, PITCHER
            sim.w8(BRAIN + pm.LEVEL, pm.L4)
            sim.w16(PITCHER + pm.CHAR_ID, cid)
            seen = {}

            def pick(s: Sim, drawn=drawn, seen=seen) -> None:
                seen['priors'] = [round(s.float(s.r[4] + 4 * i), 4) for i in range(4)]
                seen['n'] = s.r[5]
                s.r[3] = drawn
            sim.calls[CODE + 0x1000] = pick
            self.assertEqual(sim.run(CODE, {pm.TYPE_PICKED}), pm.TYPE_PICKED)
            self.assertEqual((sim.r[0], sim.u16(PITCHER + pm.MARK), sim.r[1]), (kind, marked, STACK), (cid, drawn))
            if drawn is None:
                self.assertEqual(seen, {})                                   # an id past the tables: a plain curve
            else:
                self.assertEqual(seen, {'priors': [0.35, 0.04, 0.15, 0.46], 'n': 4})

    def test_the_pitcher_walks_from_frame_one_and_throws_on_his_spot(self):
        for flag, compared in ((1, 1), (0, 0x1E)):
            sim = machine(pm.move_stub(CODE), flag)
            sim.r[0], sim.r[30] = compared, BRAIN
            sim.w8(BRAIN + pm.LEVEL, pm.L4)
            self.assertEqual(sim.run(CODE, {pm.MOVE_RET}), pm.MOVE_RET)
            self.assertEqual(sim.cr[0], 0)                                   # "the wait is over" on that frame

        def wait(frame, latched=0, cursor=0.2, aim=0.2, ready=1, flag=1):
            sim = machine(pm.wait_stub(CODE, DATA), flag)
            sim.r[30], sim.r[31] = BRAIN, frame
            sim.w8(BRAIN + pm.LEVEL, pm.L4)
            sim.w8(BRAIN + pm.READY, ready)
            sim.wfloat(BRAIN + pm.AIM, aim)
            sim.wfloat(PITCHER + pm.CURSOR, cursor)
            sim.w8(DATA, latched)
            sim.wfloat(DATA + 8, pm.BOX_EPS)
            stop = sim.run(CODE, {pm.BRAIN_RET, pm.WAIT_GO, pm.WAIT_RET})
            return stop, sim.r[3], sim.u8(DATA)
        self.assertEqual(wait(1, latched=1), (pm.BRAIN_RET, 0, 0))           # a new pitch: the latch cleared
        self.assertEqual(wait(5)[0], pm.WAIT_GO)                             # not decided yet: decide now
        self.assertEqual(wait(9, latched=1, cursor=0.1), (pm.BRAIN_RET, 0, 1))       # still walking
        self.assertEqual(wait(9, latched=1, ready=0), (pm.BRAIN_RET, 0, 1))
        self.assertEqual(wait(9, latched=1), (pm.BRAIN_RET, 1, 0))           # on the spot: the pitch goes
        self.assertEqual(wait(9, flag=0)[0], pm.WAIT_RET)


# -- the DOL ----------------------------------------------------------------------------------------------------

class DeployTests(unittest.TestCase):
    def test_hooks_and_calls(self):
        img = image()
        log = engine.apply_dol(img, engine.Record('Custom Level', dict(ON)))
        self.assertIn('Pitchers learn each batter', log[-1])
        self.assertEqual(engine.hooked(img), [pm.FEATURE])
        base_at = engine.block_address(img)
        end = base_at + engine._header(img, base_at)[0]
        for site, _stock in pm.SITES:
            word = img.u32(site)
            self.assertTrue(base_at <= ppc.branch_target(word, site) < end, hex(site))
            self.assertEqual(word & 1, int(site in pm.LINKED), hex(site))     # a call where the stub returns
        self.assertGreaterEqual(base_at, dol_hammerspace.DATA_BASE)          # the custom CPU section
        self.assertEqual(len(dol_hammerspace.DolHammerspace.open(img).code.blob), dol_hammerspace.MARKER_SIZE)
        engine.unhook(img)
        self.assertEqual([hex(site) for site, stock in pm.SITES if img.u32(site) != stock], [])
        self.assertIsNone(dol_hammerspace.cpu_slot(img))

    def test_a_redeploy_takes_the_stats_as_they_are_now(self):
        img = image()
        engine.apply_dol(img, engine.Record('Custom Level', dict(ON)))
        before = img.read(dol_hammerspace.cpu_slot(img).address, dol_hammerspace.cpu_slot(img).size)
        stats = inventory.table('stats')
        img.write(stats.address + stats.header + pm.STAT_CHARGE_PITCH, struct.pack('>H', 100))   # id 0 slows down
        engine.apply_dol(img, engine.Record('Custom Level', dict(ON)))
        after = img.read(dol_hammerspace.cpu_slot(img).address, dol_hammerspace.cpu_slot(img).size)
        self.assertNotEqual(before, after)
        self.assertIn(struct.pack('>4f', 0.0, 0.04, 0.15, 0.81), after)      # his new prior row
        self.assertNotIn(struct.pack('>4f', 0.0, 0.04, 0.15, 0.81) * 2, before)
        self.assertEqual(PITCHERS[0][0], 160)


if __name__ == '__main__':
    unittest.main()
