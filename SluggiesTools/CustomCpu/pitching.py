"""The custom CPU level's pitch model (the "Pitchers learn each batter" switch). Ported from the
Slugout 0.8 patcher and reduced to one level: one parameter block and one set of pitch-mix tables, from
the custom level's settings.

Brain = CBComPitch (r3 / r30 in its methods); pitcher = *(r13-0x1580), or *(r13-0x1584) when that is 0;
rand(n) = FUN_80165C14(*(r13-0x1578), n); uniform(lo, hi) = FUN_80165CFC. Every decision stub starts with the
gate: a custom match, the brain at the game's level 4, not the tutorial brain; else the stock code runs, with
the same random calls.

**The model.** Additive scores, learned during one game and zeroed at every match start (``reset_stub``). One
state block per batting team (so in CPU vs CPU each pitcher learns the other side), each with a score set per
batter handedness: 13 factor scores = pitch type (fastball / changeup / plain curve / charged curve), zone
band measured where the ball really crossed (deep in / edge in / just off / far off), curve direction (toward
/ away), break size (small / medium / large). A choice is ``pick``: an index drawn with P ~ prior * 2^(score /
temperature), so with every score 0 it is the prior. The priors: the type mix per pitcher (``tables`` /
``type_priors``: fastballs by charge pitch speed, changeups by how high the changeup floats, a share of plain
curves, the rest charged curves steered onto their target), strikes deep / edge 50 / 50, balls far off, charged
curves at their planned bend.

**Learning.** After a pitch the reward: strikeout, swinging miss, taken strike and foul positive; contact
negative by its timing and sweet-spot accuracy; a taken ball, a walk and a hit batter negative; strikeout and
contact scaled by the situation (the batter's charge power band, runners and outs). Each factor used by the
pitch moves by ALPHA * (reward - baseline - the factors' sum), clamped. Ball or strike comes from opponent
stats kept apart (chase rate, chase-strikeout rate) against the cost of a ball at this count; with 0-1 balls a
FLOOR % roll throws a ball first, with 3 balls at most CAP3[strikes] % balls. A pitching change fades the
scores and raises the temperatures for some pitches.

**Aim.** The pitcher starts walking to his spot at once (``move_stub`` / ``wait_stub``) and throws when he is
there. A pitch on the batter's side must clear his hit box by the margin at his live position, else it goes to
the other side; every hit batter widens that margin for the rest of the game.

**Hooks** (``SITES``; ``LINKED`` ones are calls): TYPE (the pitch type), LOC (frame 1: the side, the learning
update, the temperatures), DECIDE (the aim), CLAMP (aims outside the stock range pass unclamped), STEER (a
marked charged curve is steered onto its target by at most a human's input a frame), PLANE1 / PLANE2 (what
the umpire saw as the ball passed), CONTACT, HBP, RESET, MOVE, WAIT. The stubs' own docstrings have the
registers.

**Tables built from the characters' stats** (``read_stats``, for every row of the per-character tables as the
DOL holds them now, found through the game's own lis / addi pairs so a roster's moved tables are read):
``PRI[id]`` the type priors, ``PERID[id]`` hit-box edge, charge power band and "weak next batter", ``Bc[id]``
the planned charged-curve bend. They are as good as the stats were at the deploy: ``build`` runs at every
custom CPU deploy and roster run; stats another tool edits afterwards are picked up by the next one.
"""

import struct

try:
    from ..Dol import inventory
    from ..Dol.dolfile import DolError
    from ..Dol.ppc import Asm, branch_word, d_form, ha, lo, x_form
    from .behaviour_base import gate
except ImportError:
    from Dol import inventory
    from Dol.dolfile import DolError
    from Dol.ppc import Asm, branch_word, d_form, ha, lo, x_form
    from CustomCpu.behaviour_base import gate

FEATURE = "cpu-pitching"
ROWS_STOCK, ROWS_MOVED = 0x65, 0x100         # rows of the per-character tables: the game's, a roster's


class PitchingError(DolError):
    pass


# offsets in a stats row
STAT_STAR_SWING, STAT_SLAP_POWER, STAT_CHARGE_POWER = 9, 16, 18
STAT_CURVEBALL, STAT_CHARGE_PITCH, STAT_CURVE = 32, 34, 36

L4 = 3
LEVEL, TRAINING, KIND, AIM = 0x1A, 0x21, 0x1B, 4
PITCHER, PITCHER_ALT, RNG, TUTORIAL = -0x1580, -0x1584, -0x1578, -0x1D3C
CHAR_ID, CURVE_DIR, TERTILE, LATE, CURSOR = 0x2A, 0x140, 0x137, 0x190, 0x8C
RAND, UNIFORM, REMAP = 0x80165C14, 0x80165CFC, 0x804B9ACC

TYPE, TYPE_ORIG, TYPE_PICKED = 0x800C73A4, 0x806DE2C8, 0x800C7420
LOC, LOC_ORIG, LOC_DONE = 0x800C6BA8, 0x838DEA80, 0x800C6E1C
DECIDE, DECIDE_ORIG = 0x800C691C, 0x38600001
MOVE_AT, MOVE_ORIG, MOVE_RET = 0x800C6E58, 0x2C00001E, 0x800C6E5C
WAIT_AT, WAIT_ORIG, WAIT_RET = 0x800C67C8, 0xA81E0018, 0x800C67CC
WAIT_GO, BRAIN_RET = 0x800C67DC, 0x800C6920
READY, BATTER_BUSY = 0x28, 0xDA
BOX_EPS, REDECIDE_MAX = 0.02, 3
CLAMP, CLAMP_ORIG = 0x8015DCE4, branch_word(0x8015DCE4, REMAP, link=True)
CONTACT_AT, CONTACT_ORIG = 0x800BD408, 0x806DEA80
HBP_AT, HBP_ORIG = 0x80163630, 0x981F00D1
RESET_AT, RESET_ORIG = 0x800C3684, 0x98A3001A

EDGE = 0.55
E_LO, E_HI, E_HI5 = -0.12, 0.08, 0.12
B_TERTILE, B_LATE = (0.20, 0.25, 0.30), 1.3
B = [b * m for b in B_TERTILE for m in (1.0, B_LATE)]

FB_CUT, FB_TOP, FB_LOW = 150, 35, 3
CU_TIERS = ((40, 30), (27, 10))
CU_FAST_MULT, CU_BASE = 1.3, 4
PLAIN6 = 15

STATS_ROW = 0x8E
COUNT, STRIKES, BALLS, OUTS = -0x1D38, 7, 8, 9


BALL_LO, BALL_HI, BALL_HI5 = 0.75, 0.90, 0.94


BAND_LO = (0.43, 0.49, 0.58, BALL_LO)
BAND_HI = (0.49, 0.545, 0.72, BALL_HI)
BAND_T = (0.49, 0.5501, 0.74)
BRK_T = (0.18, 0.32)
BRK_SCALE = (0.40, 0.70, 1.00)
BRK_PRI = (0.05, 0.15, 0.80)
DIR_PRI = (0.5, 0.5)
DEEP_PRI = (0.5, 0.5)
P_FIRST, P_TAKEN, P_CHASE = 40, 75, 20
MARGIN = 0.15



BEND_MARGIN, BEND_CAP = 0.85, 1.0
LOOPY_SLAP = 40



R_K, R_MISS, R_TAKEN, R_FOUL, R_BALL, R_WALK = 10.0, 2.0, 1.0, 0.5, -1.5, -25.0




R_HBP, HBP_STEP, HBP_CAP = -100.0, 0.25, 1.0

W_T, W_S = 2.0, 4.0
CP_MULT = (0.8, 1.0, 1.4, 1.8)
RUN_MULT = (1.0, 1.3, 1.5, 1.7)
C0, Q0 = 0.30, 0.15
COST_B = (1.0, 1.5, 3.0)
WALK, W_CAREFUL = 25.0, 12.0
S0 = (-0.2, -0.2, 2.5)
DCLAMP, FADE, HOT_N = 12.0, 0.5, 10.0
T_MIN = 0.05




# The model's parameters, one block of 4-byte values (f32, the INT_PARAMS s32): the stubs read them by offset.
PARAM_NAMES = ["ALPHA", "T", "TB", "N0", "P0_PLAIN", "FAR_HI", "PRI_JUST", "PRI_FAR", "FLOOR", "CAP3", "CAP3_1", "CAP3_2",
               "FORCE3"]
PARAM_FIXED = {"P0_PLAIN": 0.40, "PRI_JUST": 0.05, "PRI_FAR": 0.95, "FORCE3": 0}




PSIZE = 4 * len(PARAM_NAMES)
P_ = {n: 4 * i for i, n in enumerate(PARAM_NAMES)}
INT_PARAMS = {"FLOOR", "CAP3", "CAP3_1", "CAP3_2", "FORCE3"}
assert P_["CAP3_1"] == P_["CAP3"] + 4 and P_["CAP3_2"] == P_["CAP3"] + 8


SEEN, ZONE, SWUNG, CONTACT, HBP, CZONE, CFRAME, CTYPE = range(8)
XACT, XAIM, CF = 0x08, 0x0C, 0x10
PEND, PB, PK, PTYPE, PDIR, PHAND, PBAND, PBRK = range(0x14, 0x1C)
PMULT, LASTP, PREV = 0x1C, 0x20, 0x22
HOT, BT, SW, B2, K2, S_, MEAN, TEMP, TEMPB = 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38, 0x44, 0x48, 0x4C
SC = 0x50
N_SC, HAND = 13, 13 * 4
SC_TYPE, SC_BAND, SC_DIR, SC_BRK = 0, 4, 8, 10
SC_END = SC + 2 * HAND
WCOST = SC_END
HMARG = WCOST + 4
SIZE = HMARG + 8
TEAM, G_PTR = 0x2A, -0x163C
LINEUP, ROSTER, RUNNERS = -0x15A8, -0x2C8, 0x807098C8
PERID_ROW = 8


MARK = 0x18C

STEER, STEER_ORIG = 0x80162394, 0xC0228EB0
STEER_RET, STEER_TAIL = 0x80162568, 0x80162538
OPEN, CLOSE = 0x80627AC4, 0x80627AAC
STEER_RANGE = 0x806279E0
STAT_RANGE = 0x806318E0 + 3 * 0xC
TF_REF = (0x8016250C, 0x80162514)
TF_STEER = 8
CHARFLOATS_REF = (0x800BDA1C, 0x800BDA20)
TRAJ_REF = (0x800BAEAC, 0x800BAEB0)
SELECTOR_REF = (0x8015C944, 0x8015C948)
HBP_BOX = 0x8062A0D8
DIRS, ACTIVE = 0x80626628, 0x80626570
MAGIC = -0x7140
CURVE_NOW, KIND_ROW, SPEED_DIV = 0x126, 0x134, -0x7340
Z_RELEASE, Z_PLATE = 17.5, 0.4
ZONE_FLAG, SWING_FLAG = 0xEE, 0xCE
BATTER, BATTER_ALT = -0x1D78, -0x1D7C
PLANE1, PLANE2 = 0x8016291C, 0x801629D0
PLANE1_ORIG, PLANE2_ORIG = 0x38000001, 0x38000002
LEFTY, BOX_X = 0xB3, 0x44
BIG = 2.0 ** 23

CONSTS = [("EDGE", EDGE), ("ZERO", 0.0), ("ONE", 1.0), ("TARGET", 0.0), ("SMIN", 0.0), ("SMAX", 0.0),
          ("X2P23", BIG), ("XBIAS", 127 * BIG), ("XMAX", 30.0), ("ALMOST", 0.9999), ("TMIN", T_MIN),
          ("HUNDRED", 100.0), ("CENT", 0.01), ("R_K", R_K), ("R_MISS", R_MISS), ("R_TAKEN", R_TAKEN),
          ("R_FOUL", R_FOUL), ("R_BALL", R_BALL), ("R_WALK", R_WALK), ("W_T", W_T), ("W_S", W_S),
          ("R_HBP", R_HBP), ("HBP_STEP", HBP_STEP), ("HBP_CAP", HBP_CAP),
          ("C0", C0), ("Q0", Q0), ("WALK", WALK), ("W_CAREFUL", W_CAREFUL), ("DCLAMP", DCLAMP), ("FADE", FADE),
          ("HOT_N", HOT_N), ("HOT_GAIN", 1.0 / HOT_N), ("MARGIN", MARGIN),
          ("P0_FIRST", 1 - P_FIRST / 100), ("P0_TAKEN", 1 - P_TAKEN / 100), ("P0_CHASE", 1 - P_CHASE / 100),
          *((f"CP{i}", v) for i, v in enumerate(CP_MULT)), *((f"RUN{i}", v) for i, v in enumerate(RUN_MULT)),
          *((f"COSTB{i}", v) for i, v in enumerate(COST_B)), *((f"S0_{i}", v) for i, v in enumerate(S0)),
          *((f"BLO{i}", v) for i, v in enumerate(BAND_LO)), *((f"BHI{i}", v) for i, v in enumerate(BAND_HI)),
          *((f"BANDT{i}", v) for i, v in enumerate(BAND_T)), *((f"BRKT{i}", v) for i, v in enumerate(BRK_T)),
          *((f"BRKS{i}", v) for i, v in enumerate(BRK_SCALE)), *((f"BRKP{i}", v) for i, v in enumerate(BRK_PRI)),
          *((f"DEEP{i}", v) for i, v in enumerate(DEEP_PRI)), *((f"DIR{i}", v) for i, v in enumerate(DIR_PRI)), *((f"B{i}", v) for i, v in enumerate(B))]
K = {n: 4 * i for i, (n, _) in enumerate(CONSTS)}
K_TIMING = 4 * len(CONSTS)
K_TARGET = K["TARGET"]


def fb_chance(charge, curve_speed, m):
    if charge <= curve_speed:
        return 0
    return m["FB_TOP"] if charge >= m["FB_CUT"] else m["FB_LOW"]


def peak(curve_speed, cu_mult, cu_arc):
    """The changeup's relative peak height: arc * 10000 / changeup speed^2 (0 for an empty row)."""
    speed = curve_speed * cu_mult
    return cu_arc * 10000 / speed ** 2 if speed > 0 else 0.0


def cu_chance(curve_speed, cu_mult, cu_arc):
    if curve_speed <= 0:
        return 0
    p = peak(curve_speed, cu_mult, cu_arc)
    (top_cut, top), (mid_cut, mid) = CU_TIERS
    if p >= top_cut:
        return top
    if p >= mid_cut or cu_mult > CU_FAST_MULT:
        return mid
    return CU_BASE


def tables(stats, m):
    """stats {id: dict(charge, curve_speed, cu_mult, cu_arc)} -> (FB bytes, CU bytes), ids 0..max (0 if missing).
    m: the mix {FB_CUT, FB_TOP, FB_LOW}."""
    n = max(stats) + 1 if stats else 0
    fb, cu = bytearray(n), bytearray(n)
    for cid, s in stats.items():
        fb[cid] = fb_chance(s["charge"], s["curve_speed"], m)
        cu[cid] = cu_chance(s["curve_speed"], s["cu_mult"], s["cu_arc"])
        assert fb[cid] + cu[cid] <= 100, cid
    return bytes(fb), bytes(cu)


def type_priors(fb, cu, plain):
    """PRI[id]: (fastball, changeup, plain curve, charged curve) / 100 (the old type roll's thresholds)."""
    out = []
    for f, u in zip(fb, cu):
        p = min(plain, 100 - f - u)
        out.append((f / 100, u / 100, p / 100, max(0, 100 - f - u - p) / 100))
    return out


def table_at(read, ref):
    """A table's address from the game's own lis / addi (or subi) pair (a roster run repoints them when it moves the table: ``Roster/ids.py``)."""
    hi, low = (struct.unpack(">I", read(a, 4))[0] for a in ref)
    if hi >> 26 != 15 or low >> 26 != 14:
        raise PitchingError(f"0x{ref[0]:08X}: not lis / addi ({hi:08X} {low:08X})")
    return (((hi & 0xFFFF) << 16) + ((low & 0xFFFF) ^ 0x8000) - 0x8000) & 0xFFFFFFFF


def throwfloats(read):
    """The per-character steering table's address (FUN_80162358's lis / addi)."""
    return table_at(read, TF_REF)


def read_stats(read, ids, stats_rows, changeup):
    """The table inputs for `ids` through read(address, n) -> bytes; an unreadable row is left out."""
    def u16(addr):
        return struct.unpack(">H", read(addr, 2))[0]
    tf, cf, traj, sel = (table_at(read, r) for r in (TF_REF, CHARFLOATS_REF, TRAJ_REF, SELECTOR_REF))
    out = {}
    for cid in ids:
        row = stats_rows + cid * STATS_ROW
        try:
            mult, arc = struct.unpack(">ff", read(changeup + cid * 8, 8))
            steer = struct.unpack(">f", read(tf + cid * 0xC + TF_STEER, 4))[0]
            f4 = struct.unpack(">f", read(cf + cid * 0x24 + 0x10, 4))[0]
            family = read(sel + cid * 8 + 2, 1)[0]
            reach = read(HBP_BOX + 3 * family + 1, 1)[0]
            out[cid] = dict(charge=u16(row + STAT_CHARGE_PITCH), curve_speed=u16(row + STAT_CURVEBALL),
                            cu_mult=mult, cu_arc=arc, curve=u16(row + STAT_CURVE), steer=steer,
                            charge_power=u16(row + STAT_CHARGE_POWER), slap_power=u16(row + STAT_SLAP_POWER),
                            star_swing=read(row + STAT_STAR_SWING, 1)[0], hit_curve=read(traj + cid * 2 + 1, 1)[0],
                            f4=f4, reach=reach)
        except (DolError, struct.error):
            continue
    return out


def cp_band(cp):
    return 0 if cp < 40 else 1 if cp < 60 else 2 if cp < 80 else 3


def loopy(s):
    """The loopy star-swing threat hit-curve flag 1 AND slap power < LOOPY_SLAP."""
    return s.get("hit_curve", 0) == 1 and s.get("slap_power", 0) < LOOPY_SLAP


def weak_next(s):
    """A truly weak next batter (the slugger exception): charge power < 60, no star swing, not the loopy swing."""
    return s.get("charge_power", 0) < 60 and s.get("star_swing", 0) == 0 and not loopy(s)


def hbp_edge0(s):
    """E0: the hit box edge |x| at box_x 0 (edge = E0 - box_x): |F4| - (reach + 1) / 100."""
    return abs(s.get("f4", -9.0)) - (s.get("reach", 0) + 1) / 100


def perid(stats, n):
    """PERID rows 0..n (n: the default row for an unknown id: never HBP-unsafe, CP band 1, not weak)."""
    out = bytearray()
    for cid in range(n):
        s = stats.get(cid, {})
        out += struct.pack(">fBBxx", hbp_edge0(s), cp_band(s.get("charge_power", 50)), int(weak_next(s)) if s else 0)
    return bytes(out + struct.pack(">fBBxx", 9.0, 1, 0))


def f32at(dol, addr):
    return struct.unpack(">f", dol.read(addr, 4))[0]


def timing_table(read):
    """TIMING[type][frame] (4 x 16 f32): the timing accuracy of a contact on that swing frame (docstring)."""
    out = []
    for t in range(4):
        row = [0.0] * 16
        if t < 3:
            act = struct.unpack(">15h", read(ACTIVE + (t != 0) * 0x1E, 30))
            dirs = struct.unpack(">30h", read(DIRS + t * 0x3C, 0x3C))
            mids = [(dirs[2 * f] + dirs[2 * f + 1]) / 2 for f in range(15)]
            frames = [f for f in range(15) if act[f]]
            p = frames[0]
            for f0, f1 in zip(frames, frames[1:]):
                if mids[f0] <= 0 <= mids[f1]:
                    p = f0 + (0 - mids[f0]) / (mids[f1] - mids[f0]) if mids[f1] != mids[f0] else f0
                    break
            d = max(abs(f - p) for f in frames)
            for f in frames:
                row[f] = max(0.0, 1 - abs(f - p) / d)
        out += row
    return out


def flight_consts(dol, hz=0):
    """The game's numbers the bend model uses (Hz byte 0, 60 Hz)."""
    r2 = 0x8079EDC0
    row = struct.unpack(">7h", dol.read(0x806277DC + 1 * 0xE + hz * 0xEE, 14))
    smin, smax = struct.unpack(">HH", dol.read(STAT_RANGE, 4))
    return dict(div=f32at(dol, r2 + SPEED_DIV + hz * 4), decay=f32at(dol, r2 - 0x7338 + hz * 4),
                plate=f32at(dol, 0x8079794C), accel=row[3] * 0.001, drag_at=row[4], open=f32at(dol, OPEN),
                close=f32at(dol, CLOSE), lo=f32at(dol, STEER_RANGE + 8), hi=f32at(dol, STEER_RANGE + 12),
                smin=smin, smax=smax, perfect=f32at(dol, 0x80627BA8))


def steer_per_frame(curve, steer, k):
    """g: the most a human's steer input (+-1) moves +0xE8 in one frame (FUN_80162358)."""
    span = k["smax"] - k["smin"]
    t = min(max((curve - k["smin"]) / span, 0.0), 1.0) if span else 1.0
    return (k["lo"] + (k["hi"] - k["lo"]) * t) * steer


def max_bend(g, speed, k, z0=Z_RELEASE, zp=Z_PLATE):
    """The sideways bend at the plate of a pitch at `speed` steered one way by g every frame of the window
    (FUN_80160938's order each frame: drag, steer, decay, move; the plate crossing interpolated)."""
    if speed <= 0:
        return 0.0
    v, z, e, d = -speed / k["div"], z0, 0.0, 0.0
    thr = (100 - k["drag_at"]) * k["plate"] / 100
    while True:
        if z <= thr and v - v * k["accel"] < -0.05:
            v -= v * k["accel"]
        if k["open"] <= z0 - z <= k["close"]:
            e += g
        v *= k["decay"]
        zn = z + v
        if zn <= zp:
            return d + e * (z - zp) / (z - zn)
        d, z = d + e, zn


def perfect_speed(charge, k):
    """FUN_8015EFE0 with +0x13B == 3: (int)(charge pitch speed * 1.2f) & 0xFF."""
    return int(struct.unpack(">f", struct.pack(">f", charge * k["perfect"]))[0]) & 0xFF


def bends(stats, k):
    """Bc[id]: the planned charged-curve bend (m), BEND_MARGIN of the fresh max at a perfect charge (<= BEND_CAP)."""
    n = max(stats) + 1 if stats else 0
    out = [0.0] * n
    for cid, s in stats.items():
        if s.get("curve") and s.get("steer"):
            g = steer_per_frame(s["curve"] & 0xFF, s["steer"], k)
            out[cid] = min(BEND_CAP, BEND_MARGIN * max_bend(g, perfect_speed(s["charge"], k), k))
    return out



def _or(a, ra, rs, rb):
    return a.word(x_form(rs, ra, rb, 444))


def _lfd(a, ft, d, ra):
    return a.word(d_form(50, ft, ra, d))


def _lfsx(a, ft, ra, rb):
    return a.word(x_form(ft, ra, rb, 535))


def _stfsx(a, fs, ra, rb):
    return a.word(x_form(fs, ra, rb, 663))


def _fabs(a, t, b):
    return a.word((63 << 26) | (t << 21) | (b << 11) | (264 << 1))


def _fmadd(a, t, fa, fc, fb):
    """Double precision t = fa * fc + fb."""
    return a.word((63 << 26) | (t << 21) | (fa << 16) | (fb << 11) | (fc << 6) | (29 << 1))


def _xori(a, ra, rs, imm):
    return a.word(d_form(26, rs, ra, imm))


def _gate(a, stock, reg):
    """Branch to `stock` unless a custom match, at level 4 with the normal brain (r30 = brain). Uses r0, `reg`
    (free at the site: its stock instruction writes it) and cr0."""
    gate(a, reg, stock)
    a.lbz(0, LEVEL, 30).cmplwi(0, L4).bne(stock)
    a.lwz(0, TUTORIAL, 13).cmpwi(0, 0).bne(stock)


def _slot(a, rd, rt, state, tag):
    """rd = the state block of the batting team (G+0x2A; team 0 without G). Uses rt (r0 allowed) and cr0."""
    a.lwz(rd, G_PTR, 13).li(rt, 0).cmpwi(rd, 0).beq(tag)
    a.lbz(rt, TEAM, rd)
    a.label(tag)
    a.andi_(rt, rt, 1).mulli(rt, rt, SIZE)
    a.load_addr(rd, state).add(rd, rd, rt)


def _params(a, rd, rt, params, tag):
    """rd = the parameter block."""
    a.load_addr(rd, params)


def _batter(a, rd, tag):
    a.lwz(rd, BATTER, 13).cmpwi(rd, 0).bne(tag)
    a.lwz(rd, BATTER_ALT, 13)
    a.label(tag)


def _hand(a, rd, rb, tag):
    """rd = the hand block offset in a state block (SC + hand * HAND); rb = the batter (may be 0). Uses cr0."""
    a.li(rd, 0).cmpwi(rb, 0).beq(tag)
    a.lbz(rd, LEFTY, rb).andi_(rd, rd, 1).mulli(rd, rd, HAND)
    a.label(tag)
    a.addi(rd, rd, SC)


def _count_above(a, rd, fv, kbase, rk, n, tag):
    """rd = how many of the n thresholds consts[kbase + 4i] (rk = consts) fv is at or above. Uses r3, f3, cr0."""
    a.li(rd, 0).li(3, kbase)
    a.label(tag)
    _lfsx(a, 3, rk, 3).fcmpo(fv, 3).blt(tag + "_x")
    a.addi(rd, rd, 1).addi(3, 3, 4).cmpwi(rd, n).blt(tag)
    a.label(tag + "_x")


def pick_fn(at, consts):
    """pick(r3 scores f32[n], r4 priors f32[n] (overwritten: the weights), r5 n, f1 T) -> r3 the index drawn with
    P ~ prior * 2^(score / T) (2^x: the float exponent trick, x clamped to +-30). Uses r0 r3-r10, f0-f6; LR saved;
    uniform(0, 1) (the game RNG) once."""
    a = Asm(at)
    a.stwu(1, -0x20, 1).mflr(0).stw(0, 0x24, 1)
    a.mr(8, 3).mr(9, 4).slwi(10, 5, 2)
    a.load_addr(7, consts)
    a.lfs(0, K["TMIN"], 7).fcmpo(1, 0).bge("t_ok").fmr(1, 0)
    a.label("t_ok")
    a.lfs(4, K["ONE"], 7).fdivs(4, 4, 1)
    a.lfs(5, K["ZERO"], 7)
    a.li(6, 0)
    a.label("w")
    _lfsx(a, 1, 8, 6).fmuls(1, 1, 4)
    a.lfs(2, K["XMAX"], 7).fcmpo(1, 2).ble("w_hi").fmr(1, 2)
    a.label("w_hi")
    a.fneg(2, 2).fcmpo(1, 2).bge("w_lo").fmr(1, 2)
    a.label("w_lo")
    a.lfs(2, K["X2P23"], 7).lfs(3, K["XBIAS"], 7)
    _fmadd(a, 1, 1, 2, 3)
    a.fctiwz(1, 1).stfd(1, 8, 1).lwz(0, 0xC, 1).stw(0, 8, 1).lfs(1, 8, 1)
    _lfsx(a, 2, 9, 6).fmuls(1, 1, 2)
    _stfsx(a, 1, 9, 6).fadds(5, 5, 1)
    a.addi(6, 6, 4).cmpw(6, 10).blt("w")
    a.lfs(6, K["ALMOST"], 7).fmuls(5, 5, 6)
    a.lfs(1, K["ZERO"], 7).lfs(2, K["ONE"], 7)
    a.lwz(3, RNG, 13).bl(UNIFORM)
    a.fmuls(1, 1, 5)
    a.fsubs(6, 6, 6).li(3, 0).li(6, 0)
    a.label("c")
    _lfsx(a, 2, 9, 6).fadds(6, 6, 2).fcmpo(1, 6).blt("out")
    a.addi(6, 6, 4).addi(3, 3, 1).cmpw(6, 10).blt("c")
    a.addi(3, 3, -1)
    a.label("out")
    a.lwz(0, 0x24, 1).mtlr(0).addi(1, 1, 0x20).blr()
    return a


def type_stub(at, pri, n, state, pick):
    a = Asm(at)
    _gate(a, "stock", 3)
    a.lha(4, CHAR_ID, 31).li(0, 0).cmplwi(4, n).bge("picked")
    a.stwu(1, -0x20, 1)
    a.slwi(4, 4, 4)
    a.load_addr(5, pri)
    a.add(4, 4, 5)
    for i in range(4):
        a.lwz(0, 4 * i, 4).stw(0, 8 + 4 * i, 1)
    _slot(a, 3, 0, state, "slot")
    _batter(a, 7, "bat")
    _hand(a, 6, 7, "hand")
    a.lfs(1, TEMP, 3).add(3, 3, 6)
    a.addi(4, 1, 8).li(5, 4).bl(pick)
    a.addi(1, 1, 0x20)
    a.cmpwi(3, 0).li(0, 1).beq("picked")
    a.cmpwi(3, 1).li(0, 2).beq("picked")
    a.cmpwi(3, 2).li(0, 0).beq("picked")
    a.li(0, 1).li(5, 1).sth(5, MARK, 31)
    a.label("picked")
    a.b(TYPE_PICKED)
    a.label("stock")
    a.word(TYPE_ORIG)
    a.b(TYPE + 4)
    return a


def _add1(a, off, rbase, fone):
    a.lfs(2, off, rbase).fadds(2, 2, fone).stfs(2, off, rbase)


def loc_stub(at, consts, state, params):
    """Frame 1: the side, then the learning update for the last pitch, the pitching change and the temperatures.
    r31 = state, r29 = params, r28 = consts (restored by the epilogue); a 0x20 frame (8..11: the used factors)."""
    a = Asm(at)
    _gate(a, "stock", 28)
    a.lwz(3, RNG, 13).li(4, 2).bl(RAND)
    a.load_addr(28, consts).lfs(0, K["EDGE"], 28)
    a.cmpwi(3, 0).bne("side").fneg(0, 0)
    a.label("side")
    a.stfs(0, AIM, 30)
    a.stwu(1, -0x20, 1)
    _slot(a, 31, 3, state, "slot")
    _params(a, 29, 3, params, "lvl")
    a.lbz(0, PEND, 31).cmpwi(0, 0).beq("clear")

    a.lwz(5, COUNT, 13).lbz(6, STRIKES, 5).lbz(7, BALLS, 5)
    _or(a, 6, 6, 7)
    a.lfs(13, PMULT, 31)
    a.lbz(0, HBP, 31).cmpwi(0, 0).beq("no_hbp")
    a.lbz(7, PHAND, 31).slwi(7, 7, 2).addi(7, 7, HMARG).add(7, 7, 31)
    a.lfs(0, 0, 7).lfs(1, K["HBP_STEP"], 28).fadds(0, 0, 1)
    a.lfs(1, K["HBP_CAP"], 28).fcmpo(0, 1).ble("hm_ok").fmr(0, 1)
    a.label("hm_ok")
    a.stfs(0, 0, 7)
    a.lfs(1, K["R_HBP"], 28).b("have_r")
    a.label("no_hbp")
    a.lbz(0, CONTACT, 31).cmpwi(0, 0).beq("no_contact")
    a.cmpwi(6, 0).beq("in_play")
    a.lfs(1, K["R_FOUL"], 28).b("have_r")
    a.label("in_play")
    a.lbz(3, CTYPE, 31).lbz(4, CFRAME, 31).slwi(3, 3, 4).add(3, 3, 4).slwi(3, 3, 2).addi(3, 3, K_TIMING)
    _lfsx(a, 2, 28, 3)
    a.lfs(3, K["ONE"], 28).lbz(0, CZONE, 31).cmpwi(0, 2).beq("sweet")
    a.lfs(0, CF, 31).lfs(4, K["HUNDRED"], 28).fsubs(0, 0, 4)
    _fabs(a, 0, 0)
    a.lfs(4, K["CENT"], 28).fnmsubs(3, 0, 4, 3)
    a.lfs(0, K["ZERO"], 28).fcmpo(3, 0).bge("sweet").fmr(3, 0)
    a.label("sweet")
    a.lfs(4, K["W_T"], 28).fmuls(2, 2, 4).lfs(4, K["W_S"], 28).fmadds(1, 3, 4, 2)
    a.fmuls(1, 1, 13).fneg(1, 1).b("have_r")
    a.label("no_contact")
    a.lbz(0, SEEN, 31).cmpwi(0, 0).beq("clear")
    a.lbz(0, SWUNG, 31).cmpwi(0, 0).bne("swing")
    a.lbz(0, ZONE, 31).cmpwi(0, 0).bne("taken")
    a.lfs(1, K["R_BALL"], 28)
    a.lbz(0, PB, 31).cmpwi(0, 3).bne("have_r")
    a.lfs(1, K["R_WALK"], 28).b("have_r")
    a.label("swing")
    a.lfs(1, K["R_MISS"], 28).b("k2")
    a.label("taken")
    a.lfs(1, K["R_TAKEN"], 28)
    a.label("k2")
    a.lbz(0, PK, 31).cmpwi(0, 2).bne("have_r")
    a.lfs(1, K["R_K"], 28).fmuls(1, 1, 13)
    a.label("have_r")

    a.lbz(0, SEEN, 31).cmpwi(0, 0).bne("measured")
    a.lbz(9, PBAND, 31).lbz(8, PBRK, 31)
    a.li(10, 0).cmplwi(9, 1).bgt("classified").li(10, 1).b("classified")
    a.label("measured")
    a.lbz(10, ZONE, 31)
    a.lfs(2, XACT, 31)
    _fabs(a, 2, 2)
    _count_above(a, 9, 2, K["BANDT0"], 28, 3, "band")
    a.lfs(2, XACT, 31).lfs(3, XAIM, 31).fsubs(2, 2, 3)
    _fabs(a, 2, 2)
    _count_above(a, 8, 2, K["BRKT0"], 28, 2, "brk")
    a.label("classified")

    a.lbz(3, SWUNG, 31).lbz(4, CONTACT, 31)
    _or(a, 5, 3, 4)
    a.li(7, 0).lfs(12, K["ONE"], 28)
    a.cmpwi(10, 0).bne("strike_side")
    _add1(a, BT, 31, 12)
    a.cmpwi(5, 0).beq("no_chase")
    _add1(a, SW, 31, 12)
    a.li(7, 2)
    a.label("no_chase")
    a.lbz(0, PK, 31).cmpwi(0, 2).bne("prev")
    _add1(a, B2, 31, 12)
    a.cmpwi(3, 0).beq("prev").cmpwi(4, 0).bne("prev")
    _add1(a, K2, 31, 12)
    a.b("prev")
    a.label("strike_side")
    a.cmpwi(5, 0).bne("s_value").li(7, 1)
    a.label("s_value")
    a.lbz(3, PK, 31).slwi(3, 3, 2).addi(4, 3, S_)
    _lfsx(a, 2, 31, 4)
    a.addi(3, 3, K["S0_0"])
    _lfsx(a, 3, 28, 3)
    a.fsubs(3, 1, 3).fsubs(3, 3, 2).lfs(4, P_["ALPHA"], 29).fmadds(2, 3, 4, 2)
    _stfsx(a, 2, 31, 4)
    a.label("prev")
    a.stb(7, PREV, 31)

    a.lbz(3, PHAND, 31).mulli(3, 3, HAND).addi(3, 3, SC)
    a.lbz(0, PTYPE, 31).stb(0, 8, 1).addi(0, 9, SC_BAND).stb(0, 9, 1).li(6, 2)
    a.lbz(7, PDIR, 31).cmplwi(7, 1).bgt("n_used")
    a.addi(0, 7, SC_DIR).stb(0, 10, 1).addi(0, 8, SC_BRK).stb(0, 11, 1).li(6, 4)
    a.label("n_used")
    for phase in (0, 1):
        if phase:
            a.lfs(3, MEAN, 31).fsubs(4, 1, 3).fsubs(4, 4, 2)
            a.lbz(0, HBP, 31).cmpwi(0, 0).bne("c_hbp")
            a.lfs(5, K["DCLAMP"], 28).fcmpo(4, 5).ble("c_hi").fmr(4, 5)
            a.label("c_hi")
            a.fneg(5, 5).fcmpo(4, 5).bge("c_lo").fmr(4, 5)
            a.label("c_lo")
            a.lfs(5, P_["ALPHA"], 29).fsubs(6, 1, 3).fmadds(3, 6, 5, 3).stfs(3, MEAN, 31)
            a.b("c_go")
            a.label("c_hbp")
            a.lfs(5, P_["ALPHA"], 29)
            a.label("c_go")
            a.fmuls(4, 4, 5)
        else:
            a.lfs(2, K["ZERO"], 28)
        a.li(7, 0)
        a.label(f"f{phase}")
        a.addi(4, 1, 8).lbzx(4, 4, 7).slwi(4, 4, 2).add(4, 4, 3)
        _lfsx(a, 5, 31, 4)
        if phase:
            a.fadds(5, 5, 4)
            _stfsx(a, 5, 31, 4)
        else:
            a.fadds(2, 2, 5)
        a.addi(7, 7, 1).cmpw(7, 6).blt(f"f{phase}")
    a.label("clear")
    a.li(0, 0)
    for off in range(0, PEND + 1, 4):
        a.stw(0, off, 31)

    a.lwz(4, PITCHER, 13).cmpwi(4, 0).bne("p_have").lwz(4, PITCHER_ALT, 13)
    a.label("p_have")
    a.lha(5, CHAR_ID, 4).addi(5, 5, 1).lha(6, LASTP, 31).cmpw(5, 6).beq("temps")
    a.sth(5, LASTP, 31).cmpwi(6, 0).beq("temps")
    a.lfs(2, K["FADE"], 28).li(7, SC)
    a.label("fade")
    _lfsx(a, 3, 31, 7).fmuls(3, 3, 2)
    _stfsx(a, 3, 31, 7)
    a.addi(7, 7, 4).cmpwi(7, SC_END).blt("fade")
    a.lfs(2, K["HOT_N"], 28).stfs(2, HOT, 31)
    a.label("temps")
    a.lfs(2, HOT, 31).lfs(3, K["HOT_GAIN"], 28).lfs(4, K["ONE"], 28).fmadds(3, 2, 3, 4)
    a.lfs(5, P_["T"], 29).fmuls(5, 5, 3).stfs(5, TEMP, 31)
    a.lfs(5, P_["TB"], 29).fmuls(5, 5, 3).stfs(5, TEMPB, 31)
    a.fsubs(2, 2, 4).lfs(5, K["ZERO"], 28).fcmpo(2, 5).bge("hot").fmr(2, 5)
    a.label("hot")
    a.stfs(2, HOT, 31)
    a.addi(1, 1, 0x20)
    a.b(LOC_DONE)
    a.label("stock")
    a.word(LOC_ORIG)
    a.b(LOC + 4)
    return a


def _pick2(a, pick, scores_reg, scores_off, temp_off, n=2):
    """r3 = pick(scores_reg + scores_off, r1 + 0x10 (the priors), n, [r31 + temp_off])."""
    a.addi(3, scores_reg, scores_off).addi(4, 1, 0x10).li(5, n).lfs(1, temp_off, 31).bl(pick)


def aim_stub(at, consts, state, params, bend, perid_tab, n, pick, glide):
    """The pitch frame's placement. r31 = state (restored by the epilogue), r11 = params, r12 = consts; a 0x40
    frame: 0x08 scores, 0x10 priors, 0x20 hand block offset, 0x24 the batter's PERID row, 0x28 RISP, 0x2C the
    pitcher, 0x30 the side (1: +x), 0x34 curving (0 no, 1 plain, 2 charged), 0x38 dir. f13 = mult, f12 = |X|,
    f11 = the planned break."""
    a = Asm(at)
    _gate(a, "stock", 3)
    a.lbz(0, TRAINING, 30).cmpwi(0, 0).bne("done")
    a.lbz(0, KIND, 30).cmplwi(0, 2).beq("done")
    a.stwu(1, -0x40, 1)
    _slot(a, 31, 3, state, "slot")
    _params(a, 11, 3, params, "lvl")
    a.load_addr(12, consts)

    _batter(a, 7, "bat")
    _hand(a, 6, 7, "hand")
    a.stw(6, 0x20, 1)
    a.addi(0, 6, -SC).li(6, HAND)
    a.word(x_form(3, 0, 6, 459))
    a.stb(3, PHAND, 31)
    a.li(4, n).cmpwi(7, 0).beq("row")
    a.lha(4, CHAR_ID, 7).cmplwi(4, n).ble("row").li(4, n)
    a.label("row")
    a.slwi(4, 4, 3).load_addr(5, perid_tab).add(4, 4, 5).stw(4, 0x24, 1)

    a.lwz(5, COUNT, 13).lbz(6, STRIKES, 5).lbz(7, BALLS, 5).lbz(8, OUTS, 5)
    a.stb(6, PK, 31).stb(7, PB, 31)
    a.lbz(3, 4, 4).slwi(3, 3, 2).addi(3, 3, K["CP0"])
    _lfsx(a, 13, 12, 3)
    a.load_addr(3, RUNNERS).li(9, 0)
    for base, bit in ((2, 1), (3, 2)):
        a.lwz(4, 4 * base, 3).cmpwi(4, 0).beq(f"r{base}")
        a.lbz(4, 0x176, 4).cmpwi(4, 0).beq(f"r{base}").ori(9, 9, bit)
        a.label(f"r{base}")
    a.li(4, 0).stw(9, 0x28, 1).cmpwi(9, 0).beq("runm")
    a.li(4, 1).cmpwi(8, 2).bge("runm")
    a.li(4, 2).cmpwi(9, 2).blt("runm").li(4, 3)
    a.label("runm")
    a.slwi(4, 4, 2).addi(4, 4, K["RUN0"])
    _lfsx(a, 0, 12, 4)
    a.fmuls(13, 13, 0).stfs(13, PMULT, 31)

    a.lfs(2, P_["N0"], 11).lfs(3, K["C0"], 12).lfs(4, SW, 31).fmadds(4, 3, 2, 4)
    a.lfs(5, BT, 31).fadds(5, 5, 2).fdivs(9, 4, 5).fsubs(4, 9, 3)
    a.cmpwi(7, 3).bge("walk_cost")
    a.slwi(3, 7, 2).addi(3, 3, K["COSTB0"])
    _lfsx(a, 5, 12, 3)
    a.b("have_cost")
    a.label("walk_cost")
    a.load_addr(9, perid_tab + PERID_ROW * n)
    a.lwz(3, LINEUP, 13).cmpwi(3, 0).beq("next")
    a.lwz(4, G_PTR, 13).cmpwi(4, 0).beq("next")
    a.lbz(5, 0x2C, 4).add(6, 3, 5).lbz(6, 0x80, 6).cmplwi(6, 9).blt("idx").li(6, 0)
    a.label("idx")
    a.mulli(5, 5, 0x28).add(3, 3, 5).slwi(6, 6, 2).add(3, 3, 6).lha(6, 0xC, 3)
    a.lwz(3, ROSTER, 13).cmpwi(3, 0).beq("next")
    a.lbz(5, TEAM, 4).mulli(5, 5, 0x4FE).add(3, 3, 5).mulli(6, 6, STATS_ROW).add(3, 3, 6).lhz(6, 0, 3)
    a.cmplwi(6, n).bge("next")
    a.slwi(6, 6, 3).load_addr(9, perid_tab).add(9, 9, 6)
    a.label("next")
    a.lbz(3, 4, 9).slwi(3, 3, 2).addi(3, 3, K["CP0"])
    _lfsx(a, 5, 12, 3)
    a.lfs(0, K["WALK"], 12).fmuls(5, 5, 0)
    a.lbz(0, 5, 9).cmpwi(0, 0).beq("have_cost")
    a.lwz(0, 0x28, 1).cmpwi(0, 0).beq("have_cost")
    a.lwz(3, 0x24, 1).lbz(0, 4, 3).cmplwi(0, 2).blt("have_cost")
    a.lfs(5, K["W_CAREFUL"], 12)
    a.label("have_cost")
    a.stfs(5, WCOST, 31)
    a.lbz(0, PK, 31).cmpwi(0, 2).bne("dvb0")
    a.lfs(3, K["Q0"], 12).lfs(6, K2, 31).fmadds(6, 3, 2, 6)
    a.lfs(0, B2, 31).fadds(0, 0, 2).fdivs(6, 6, 0).fsubs(6, 6, 3)
    a.lfs(0, K["R_K"], 12).fmuls(6, 6, 0).fmuls(6, 6, 13).b("dvb")
    a.label("dvb0")
    a.lfs(6, K["R_MISS"], 12).fmuls(6, 6, 4)
    a.label("dvb")
    a.fmadds(6, 4, 5, 6)
    a.lbz(3, PK, 31).slwi(3, 3, 2).addi(4, 3, S_).addi(3, 3, K["S0_0"])
    _lfsx(a, 7, 12, 3)
    _lfsx(a, 8, 31, 4)
    a.fadds(8, 7, 8)
    a.fnmsubs(6, 9, 8, 6).lfs(3, K["C0"], 12).fmadds(6, 3, 7, 6)
    a.stfs(6, 0xC, 1).lfs(0, K["ZERO"], 12).stfs(0, 8, 1)

    kb = 12
    a.lbz(3, PB, 31).lbz(4, PK, 31)
    _or(a, 0, 3, 4)
    a.lfs(2, K["P0_FIRST"], kb).cmpwi(0, 0).beq("p0")
    a.lbz(0, PREV, 31)
    a.lfs(2, K["P0_TAKEN"], kb).cmpwi(0, 1).beq("p0")
    a.lfs(2, K["P0_CHASE"], kb).cmpwi(0, 2).bne("p0_plain").cmpwi(3, 3).blt("p0")
    a.label("p0_plain")
    a.lfs(2, P_["P0_PLAIN"], 11)
    a.label("p0")
    a.lfs(0, K["ONE"], 12).fsubs(0, 0, 2).stfs(0, 0x10, 1).stfs(2, 0x14, 1)
    a.lwz(3, RNG, 13).li(4, 100).bl(RAND)
    a.lbz(4, PB, 31).cmpwi(4, 1).bgt("cap")
    a.lwz(0, P_["FLOOR"], 11).cmpw(3, 0).blt("ball")
    a.b("bs")
    a.label("cap")
    a.cmpwi(4, 3).bne("bs")
    a.lbz(5, PK, 31).cmplwi(5, 2).ble("capk").li(5, 2)
    a.label("capk")
    a.slwi(5, 5, 2).add(5, 5, 11)
    a.lwz(0, P_["CAP3"], 5).cmpw(3, 0).bge("strike")
    a.lwz(0, P_["FORCE3"], 11).cmpwi(0, 0).bne("ball")
    a.label("bs")
    a.addi(3, 1, 8).addi(4, 1, 0x10).li(5, 2).lfs(1, TEMPB, 31).bl(pick)
    a.cmpwi(3, 0).beq("strike")
    a.label("ball")
    a.lfs(0, P_["PRI_JUST"], 11).stfs(0, 0x10, 1).lfs(0, P_["PRI_FAR"], 11).stfs(0, 0x14, 1)
    a.lwz(6, 0x20, 1).add(6, 6, 31)
    _pick2(a, pick, 6, 4 * (SC_BAND + 2), TEMP)
    a.addi(3, 3, 2).b("band")
    a.label("strike")
    a.lfs(0, K["DEEP0"], 12).stfs(0, 0x10, 1).lfs(0, K["DEEP1"], 12).stfs(0, 0x14, 1)
    a.lwz(6, 0x20, 1).add(6, 6, 31)
    _pick2(a, pick, 6, 4 * SC_BAND, TEMP)
    a.label("band")
    a.stb(3, PBAND, 31)
    a.slwi(3, 3, 2).addi(4, 3, K["BLO0"])
    _lfsx(a, 1, 12, 4)
    a.addi(4, 3, K["BHI0"])
    _lfsx(a, 2, 12, 4)
    a.cmpwi(3, 12).bne("uni").lfs(2, P_["FAR_HI"], 11)
    a.label("uni")
    a.lwz(3, RNG, 13).bl(UNIFORM).fmr(12, 1)

    a.lwz(4, PITCHER, 13).cmpwi(4, 0).bne("p_have").lwz(4, PITCHER_ALT, 13)
    a.label("p_have")
    a.stw(4, 0x2C, 1)
    a.lbz(0, KIND, 30).lha(5, MARK, 4).li(6, 0)
    a.li(3, 1).cmpwi(0, 3).beq("type")
    a.li(3, 2).cmpwi(0, 0).bne("fb")
    a.lbz(0, CURVE_DIR, 4).cmplwi(0, 4).beq("pc").cmplwi(0, 5).bne("type")
    a.label("pc")
    a.li(6, 1).b("type")
    a.label("fb")
    a.li(3, 0).cmpwi(5, 0).beq("type").li(3, 3).li(6, 2)
    a.label("type")
    a.stb(3, PTYPE, 31).stw(6, 0x34, 1)
    a.li(0, 0xFF).stb(0, PDIR, 31).stb(0, PBRK, 31)
    a.lfs(11, K["ZERO"], 12)
    a.cmpwi(6, 0).beq("side")

    a.lfs(0, K["DIR0"], 12).stfs(0, 0x10, 1).lfs(0, K["DIR1"], 12).stfs(0, 0x14, 1)
    a.lwz(6, 0x20, 1).add(6, 6, 31)
    _pick2(a, pick, 6, 4 * SC_DIR, TEMP)
    a.stb(3, PDIR, 31)
    a.lwz(0, 0x34, 1).cmpwi(0, 2).beq("charged")
    a.lwz(4, 0x2C, 1)
    a.lbz(5, TERTILE, 4).cmplwi(5, 2).ble("tertile").li(5, 2)
    a.label("tertile")
    a.slwi(5, 5, 3)
    a.lbz(0, LATE, 4).cmpwi(0, 0).beq("early").addi(5, 5, 4)
    a.label("early")
    a.addi(5, 5, K["B0"])
    _lfsx(a, 11, 12, 5)
    _count_above(a, 7, 11, K["BRKT0"], 12, 2, "pbrk")
    a.stb(7, PBRK, 31).b("side")
    a.label("charged")
    for i in range(3):
        a.lfs(0, K[f"BRKP{i}"], 12).stfs(0, 0x10 + 4 * i, 1)
    a.lwz(6, 0x20, 1).add(6, 6, 31)
    _pick2(a, pick, 6, 4 * SC_BRK, TEMP, n=3)
    a.stb(3, PBRK, 31)
    a.slwi(3, 3, 2).addi(3, 3, K["BRKS0"])
    _lfsx(a, 0, 12, 3)
    a.lwz(4, 0x2C, 1).lha(5, CHAR_ID, 4).slwi(5, 5, 2).load_addr(6, bend)
    _lfsx(a, 11, 6, 5)
    a.fmuls(11, 11, 0)

    a.label("side")
    a.lfs(0, AIM, 30).lfs(1, K["ZERO"], 12).li(5, 1).fcmpo(0, 1).bge("s").li(5, 0)
    a.label("s")
    a.lbz(0, PHAND, 31)
    a.word(x_form(5, 3, 0, 316))
    a.lbz(4, PDIR, 31)
    a.cmpwi(3, 0).bne("safe")
    a.fmr(2, 12)
    a.lwz(0, 0x34, 1).cmpwi(0, 0).beq("need")
    a.cmpwi(4, 0).beq("need")
    a.fadds(2, 2, 11)
    a.label("need")
    a.lfs(0, K["MARGIN"], 12)
    a.fadds(2, 2, 0)
    a.lbz(7, PHAND, 31).slwi(7, 7, 2).addi(7, 7, HMARG)
    _lfsx(a, 0, 31, 7)
    a.fadds(2, 2, 0)
    a.lwz(6, 0x24, 1).lfs(0, 0, 6)
    a.lwz(6, BATTER, 13).cmpwi(6, 0).bne("bx").lwz(6, BATTER_ALT, 13).cmpwi(6, 0).beq("safe")
    a.label("bx")
    a.lfs(1, BOX_X, 6).fsubs(0, 0, 1)
    a.fcmpo(2, 0).ble("safe")
    _xori(a, 5, 5, 1)
    a.li(3, 1)
    a.label("safe")
    a.stw(5, 0x30, 1)

    a.word(x_form(4, 6, 3, 316))

    a.fmr(1, 12)
    a.lwz(4, 0x2C, 1).lwz(0, 0x34, 1).cmpwi(0, 0).beq("sign")
    a.cmpwi(0, 2).beq("c_aim")
    a.li(7, 5).cmpwi(5, 0).beq("cd").li(7, 4)
    a.label("cd")
    a.cmpwi(6, 0).bne("cd_in")
    _xori(a, 7, 7, 1)
    a.label("cd_in")
    a.stb(7, CURVE_DIR, 4).b("bmag")
    a.label("c_aim")
    a.fmr(3, 12).cmpwi(5, 0).bne("tgt").fneg(3, 3)
    a.label("tgt")
    a.stfs(3, K_TARGET, 12)
    a.li(0, 0).stb(0, CURVE_DIR, 4)
    a.label("bmag")
    a.cmpwi(6, 0).beq("b_in")
    a.fadds(1, 1, 11).b("sign")
    a.label("b_in")
    a.fsubs(1, 1, 11)
    a.label("sign")
    a.cmpwi(5, 0).bne("store").fneg(1, 1)
    a.label("store")
    a.stfs(1, AIM, 30)
    a.li(0, 1).stb(0, PEND, 31)
    a.addi(1, 1, 0x40)
    a.label("done")
    a.load_addr(12, glide)
    a.li(0, 1).stb(0, 0, 12)
    a.lwz(6, BATTER, 13).cmpwi(6, 0).bne("g_bx").lwz(6, BATTER_ALT, 13).cmpwi(6, 0).beq("g_nb")
    a.label("g_bx")
    a.lfs(0, BOX_X, 6).stfs(0, 4, 12)
    a.label("g_nb")
    a.li(3, 0).b(DECIDE + 4)
    a.label("stock")
    a.word(DECIDE_ORIG)
    a.b(DECIDE + 4)
    return a


def move_stub(at):
    """MOVE (docstring GLIDE): the mover from frame 1 at levels 5 / 6. r0 = the frame; r30 = the brain (the caller's)."""
    a = Asm(at)
    a.mr(5, 0)
    _gate(a, "m_stock", 6)
    a.cmpwi(5, 1).b(MOVE_RET)
    a.label("m_stock")
    a.cmpwi(5, 0x1E).b(MOVE_RET)
    return a


def wait_stub(at, glide):
    """WAIT (docstring GLIDE). r31 = the frame, r30 = the brain; r0, r3, r4, r12, f0, f1 free (the stock path rewrites
    r0 / r3 / r4 before any read)."""
    a = Asm(at)
    _gate(a, "w_stock", 4)
    a.load_addr(12, glide)
    a.cmpwi(31, 1).bne("w_latch")
    a.li(0, 0).stb(0, 0, 12).stb(0, 1, 12)
    a.b("w_hold")
    a.label("w_latch")
    a.lbz(0, 0, 12).cmpwi(0, 0).beq("w_decide")
    a.lwz(4, PITCHER, 13).cmpwi(4, 0).bne("w_p").lwz(4, PITCHER_ALT, 13)
    a.label("w_p")
    a.lfs(0, CURSOR, 4).lfs(1, AIM, 30).fcmpo(0, 1).bne("w_hold")
    a.lbz(0, READY, 30).cmpwi(0, 0).beq("w_hold")
    a.lwz(3, BATTER, 13).cmpwi(3, 0).bne("w_b").lwz(3, BATTER_ALT, 13).cmpwi(3, 0).beq("w_go")
    a.label("w_b")
    a.lbz(0, BATTER_BUSY, 3).cmpwi(0, 0).bne("w_hold")
    a.lfs(0, BOX_X, 3).lfs(1, 4, 12).fsubs(0, 0, 1)
    _fabs(a, 0, 0)
    a.lfs(1, 8, 12).fcmpo(0, 1).ble("w_go")
    a.lbz(0, 1, 12).cmplwi(0, REDECIDE_MAX).bge("w_go")
    a.addi(0, 0, 1).stb(0, 1, 12).li(0, 0).stb(0, 0, 12)
    a.b("w_decide")
    a.label("w_go")
    a.li(0, 0).stb(0, 0, 12).li(3, 1).b(BRAIN_RET)
    a.label("w_hold")
    a.li(3, 0).b(BRAIN_RET)
    a.label("w_decide")
    a.b(WAIT_GO)
    a.label("w_stock")
    a.word(WAIT_ORIG).b(WAIT_RET)
    return a


def clamp_stub(at):
    """Called in place of the remap (f1 aim, f2 / f3 = -0.4 / 0.4 input range, LR = 0x8015DCE8)."""
    a = Asm(at)
    a.fcmpo(1, 2).blt("out")
    a.fcmpo(1, 3).bgt("out")
    a.b(REMAP)
    a.label("out")
    a.blr()
    return a


def steer_stub(at, consts, tf):
    """FUN_80162358's CPU branch (r31 = pitcher; its frame: LR at 0x54, 0x43300000 at 0x28 / 0x30, f31 saved).
    Unmarked: 0.0, as stock. A level 5 / 6 charged curve: the human steer (+0xE8, added to the ball x every frame), driven
    closed loop: inside the window, E = (X - x) * -vz / (z - zp) - vx is the +0xE8 that ends the pitch on X from here;
    +0xE8 moves toward it by at most g (a human's full input) a frame. Outside the window, +0xE8 as it is."""
    a = Asm(at)
    a.lha(0, MARK, 31).cmpwi(0, 0).beq("stock")
    a.lfs(1, 0x70, 31).lfs(0, 0x40, 31).fsubs(6, 1, 0)
    a.lis(3, ha(OPEN)).lfs(0, lo(OPEN), 3).fcmpo(6, 0).blt("held")
    a.lis(3, ha(CLOSE)).lfs(0, lo(CLOSE), 3).fcmpo(6, 0).bgt("held")
    a.lhz(0, CURVE_NOW, 31).stw(0, 0x2C, 1)
    _lfd(a, 1, 0x28, 1)
    _lfd(a, 0, MAGIC, 2)
    a.fsubs(1, 1, 0)
    a.load_addr(5, consts).lfs(2, K["SMIN"], 5).lfs(3, K["SMAX"], 5)
    a.lbz(0, KIND_ROW, 31).slwi(0, 0, 3).load_addr(3, STEER_RANGE).add(3, 3, 0)
    a.lfs(4, 0, 3).lfs(5, 4, 3)
    a.bl(REMAP)
    a.lha(0, CHAR_ID, 31).mulli(0, 0, 0xC).load_addr(3, tf + TF_STEER)
    _lfsx(a, 0, 3, 0)
    a.fmuls(7, 1, 0)
    a.lfs(2, 0x40, 31).lfs(0, 0x64, 31).fsubs(2, 2, 0)
    a.lfs(0, -0x7150, 2).fcmpo(2, 0).ble("held")
    a.lfs(1, K_TARGET, 5).lfs(0, 0x38, 31).fsubs(1, 1, 0)
    a.lfs(0, 0x58, 31).fmuls(1, 1, 0).fneg(1, 1).fdivs(1, 1, 2)
    a.lfs(0, 0x50, 31).fsubs(1, 1, 0)
    a.lfs(0, 0xE8, 31).fsubs(1, 1, 0)
    a.fcmpo(1, 7).ble("up").fmr(1, 7)
    a.label("up")
    a.fneg(2, 7).fcmpo(1, 2).bge("down").fmr(1, 2)
    a.label("down")
    a.fadds(1, 0, 1).stfs(1, 0xE8, 31)
    a.lfs(0, -0x7150, 2)
    a.b(STEER_TAIL)
    a.label("held")
    a.lfs(1, 0xE8, 31).b(STEER_RET)
    a.label("stock")
    a.word(STEER_ORIG)
    a.b(STEER_RET)
    return a


def plane_stub(at, state):
    """Called in place of FUN_801627E0's `li r0,plane` at either umpire plane (r29 = pitcher; r0, r3, r4 and cr0 are
    dead there, LR was saved by its prologue). Every level and pitcher: it writes only the batting team's latch
    (docstring PLANE), then li r0,1 or 2 by the caller (LR)."""
    a = Asm(at)
    _slot(a, 3, 0, state, "slot")
    a.lbz(0, ZONE_FLAG, 29).stb(0, ZONE, 3)
    a.li(0, 1).stb(0, SEEN, 3)
    a.lwz(0, 0x38, 29).stw(0, XACT, 3).lwz(0, 0x5C, 29).stw(0, XAIM, 3)
    _batter(a, 4, "have")
    a.cmpwi(4, 0).beq("end")
    a.lbz(0, SWING_FLAG, 4).lbz(4, SWUNG, 3)
    _or(a, 0, 0, 4).stb(0, SWUNG, 3)
    a.label("end")
    a.mflr(3).load_addr(4, PLANE1 + 4).cmpw(3, 4)
    a.li(0, 2).bne("r").li(0, 1)
    a.label("r")
    a.blr()
    return a


def contact_stub(at, state):
    """Called in place of FUN_800bd2f0's `lwz r3,-0x1580(r13)` after the contact score (r29 = batter; r0 and r3-r12
    are dead right after the bl it follows). Records the contact (docstring CONTACT), then the stock load."""
    a = Asm(at)
    _slot(a, 3, 4, state, "slot")
    a.li(0, 1).stb(0, CONTACT, 3)
    a.lbz(0, 0xBA, 29).stb(0, CZONE, 3)
    a.lha(0, 0x84, 29).cmplwi(0, 15).ble("f").li(0, 15)
    a.label("f")
    a.stb(0, CFRAME, 3)
    a.lbz(0, 0xB6, 29).cmplwi(0, 3).ble("t").li(0, 3)
    a.label("t")
    a.stb(0, CTYPE, 3)
    a.lwz(0, 0x60, 29).stw(0, CF, 3)
    a.word(CONTACT_ORIG)
    a.blr()
    return a


def hbp_stub(at, state):
    """Called in place of FUN_801634B8's `stb r0,0xD1(r31)` (r0 = 1; r3 is loaded next, r0 reloaded before use)."""
    a = Asm(at)
    a.word(HBP_ORIG)
    _slot(a, 3, 4, state, "slot")
    a.li(0, 1).stb(0, HBP, 3)
    a.blr()
    return a


def reset_stub(at, state):
    """Called in place of FUN_800C364C's `stb r5,0x1A(r3)` (the CPU manager at a match start; r8-r10 and ctr are
    free, as the manager stub before it): zero both state blocks, then the stock store."""
    a = Asm(at)
    assert state % 4 == 0 and SIZE % 4 == 0
    a.load_addr(9, state - 4).li(8, 2 * SIZE // 4).mtctr(8).li(8, 0)
    a.label("zero")
    a.stwu(8, 4, 9)
    a.bdnz("zero")
    a.word(RESET_ORIG)
    a.blr()
    return a


SITES = ((TYPE, TYPE_ORIG), (LOC, LOC_ORIG), (DECIDE, DECIDE_ORIG), (CLAMP, CLAMP_ORIG), (STEER, STEER_ORIG),
         (PLANE1, PLANE1_ORIG), (PLANE2, PLANE2_ORIG), (CONTACT_AT, CONTACT_ORIG), (HBP_AT, HBP_ORIG),
         (RESET_AT, RESET_ORIG), (MOVE_AT, MOVE_ORIG), (WAIT_AT, WAIT_ORIG))
LINKED = (CLAMP, PLANE1, PLANE2, CONTACT_AT, HBP_AT, RESET_AT)


# -- the build -----------------------------------------------------------------------------------------------------

STUBS = ("type", "location", "aim", "clamp", "steer", "planes", "contact", "hbp", "reset", "move", "wait")
HOOKS = SITES
RELIES = ()
FLOAT_CHECKS = ((OPEN, 8.0), (CLOSE, 15.0), (STEER_RANGE + 8, 0.0008), (STEER_RANGE + 12, 0.002))


def settings_values(const):
    """(params {name: value}, mix {FB_CUT, FB_TOP, FB_LOW}, plain curve %, consts overrides) from the custom
    level's settings (``const(key)``)."""
    walk = not const("never_walk")
    params = dict(PARAM_FIXED, ALPHA=float(const("model_learning")), T=float(const("model_randomness")),
                  TB=float(const("model_randomness_bs")), N0=float(const("model_patience")),
                  FAR_HI=float(const("far_ball")), FLOOR=int(const("model_floor")),
                  CAP3=int(const("cap3_0")) if walk else 0, CAP3_1=int(const("cap3_1")) if walk else 0,
                  CAP3_2=int(const("cap3_2")) if walk else 0)
    m = dict(FB_CUT=int(const("fastball_speed")), FB_TOP=int(const("fastball_top")),
             FB_LOW=int(const("fastball_low")))
    over = dict(P0_FIRST=1 - const("zone_first") / 100, P0_TAKEN=1 - const("zone_after_take") / 100,
                P0_CHASE=1 - const("zone_after_chase") / 100, MARGIN=float(const("hbp_margin")))
    return params, m, int(const("plain_curves")), over


def params_blob(params):
    return b"".join(struct.pack(">i", params[name]) if name in INT_PARAMS else struct.pack(">f", params[name])
                    for name in PARAM_NAMES)


def data_blob(image, const, stats_rows, changeup, n):
    """The model's data for a DOL: ``(bytes, {name: offset}, n, throwfloats address)``. ``n``: the per-character
    tables' rows."""
    for addr, want in FLOAT_CHECKS:
        got = f32at(image, addr)
        if abs(got - want) > 1e-6:
            raise PitchingError(f"0x{addr:08X} holds {got}, expected {want}: the pitch model's flight numbers "
                                "do not fit this main.dol")
    params, m, plain, over = settings_values(const)
    stats = read_stats(image.read, range(n), stats_rows, changeup)
    k = flight_consts(image)
    fb_bytes, cu_bytes = tables(stats, m)
    fb_bytes, cu_bytes = fb_bytes.ljust(n, b"\0"), cu_bytes.ljust(n, b"\0")
    bend_floats = (bends(stats, k) + [0.0] * n)[:n]
    timing = timing_table(image.read)
    vals = dict(CONSTS, SMIN=float(k["smin"]), SMAX=float(k["smax"]), **over)
    parts = (("consts", b"".join(struct.pack(">f", vals[name]) for name, _ in CONSTS)
              + struct.pack(f">{len(timing)}f", *timing)),
             ("params", params_blob(params)),
             ("pri", b"".join(struct.pack(">4f", *p) for p in type_priors(fb_bytes, cu_bytes, plain))),
             ("perid", perid(stats, n)),
             ("bend", struct.pack(f">{n}f", *bend_floats)),
             ("state", bytes(2 * SIZE)),
             ("glide", bytes(8) + struct.pack(">f", BOX_EPS)))
    blob, at = b"", {}
    for name, part in parts:
        blob += bytes(-len(blob) % 4)
        at[name] = len(blob)
        blob += part
    return blob + bytes(-len(blob) % 4), at, throwfloats(image.read)


def code(base, d, n, tf):
    """The stubs for load address ``base`` with the data addresses ``d``: ``(bytes, {site: word})``."""
    pick = base
    blob = pick_fn(base, d["consts"]).assemble()
    hooks = {}
    for name, sites, make in (
            ("type", (TYPE,), lambda at: type_stub(at, d["pri"], n, d["state"], pick)),
            ("location", (LOC,), lambda at: loc_stub(at, d["consts"], d["state"], d["params"])),
            ("aim", (DECIDE,), lambda at: aim_stub(at, d["consts"], d["state"], d["params"], d["bend"],
                                                   d["perid"], n, pick, d["glide"])),
            ("clamp", (CLAMP,), clamp_stub),
            ("steer", (STEER,), lambda at: steer_stub(at, d["consts"], tf)),
            ("planes", (PLANE1, PLANE2), lambda at: plane_stub(at, d["state"])),
            ("contact", (CONTACT_AT,), lambda at: contact_stub(at, d["state"])),
            ("hbp", (HBP_AT,), lambda at: hbp_stub(at, d["state"])),
            ("reset", (RESET_AT,), lambda at: reset_stub(at, d["state"])),
            ("move", (MOVE_AT,), move_stub),
            ("wait", (WAIT_AT,), lambda at: wait_stub(at, d["glide"]))):
        at = base + len(blob)
        blob += make(at).assemble()
        for site in sites:
            hooks[site] = branch_word(site, at, link=site in LINKED)
    return blob, hooks


def build(base, const, image, stats_rows, changeup, n):
    """The pitch model for load address ``base``: the stubs, then their data. ``(bytes, {site: word})``."""
    data, offsets, tf = data_blob(image, const, stats_rows, changeup, n)
    size = len(code(base, {name: base for name in offsets}, n, tf)[0])      # the code's length is fixed
    blob, hooks = code(base, {name: base + size + at for name, at in offsets.items()}, n, tf)
    assert len(blob) == size
    return blob + data, hooks


def build_for(base, const, image):
    """``build`` for the DOL ``image`` as it is now: its stats and changeup tables where the game's own code
    points (a roster moves them and gives them ``ROWS_MOVED`` rows)."""
    if image is None:
        raise PitchingError("the pitch model is built from a main.dol's character stats: none given")
    stats, changeup = inventory.table("stats"), inventory.table("changeup")
    stats_at = table_at(image.read, stats.pairs[0])
    n = ROWS_MOVED if stats_at != stats.address else ROWS_STOCK
    return build(base, const, image, stats_at + stats.header, table_at(image.read, changeup.pairs[0]), n)
