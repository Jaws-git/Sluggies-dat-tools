"""The custom CPU level's item use at bat (the "Aims batting items at fielders" switch). Ported from the
Slugout 0.8 patcher.

Overview: ``_docs/_docs_custom_cpu/CustomCpuLevel.md``. Tests: ``tests/test_custom_cpu_items.py``.

The notes below come with the code, which was written for a build with three custom levels. Here there is one:
read "level 6", "level 5 / 6" and tests of the flag's value as "a custom match" (``one_level.py``; the gate is
``behaviour_base.gate``). Dated remarks in quotes are the original author's design decisions; a "dump" is a RAM
dump taken during that work and "the log" a debugging log, neither of which is part of this repo. [C] marks a
fact read from the game's code, [I] / INFERRED / UNMEASURED one that was not measured.

Level 6 CPU items (the author, 2026-09-27): at level 6 the CPU's item cursor plays its real item and times it on the
fielder who gets the ball. Base items only (Shell 0, Fireball 1, Bob-omb 2, POW 3, Banana 4, Boo 5);
anything else (no item -1, custom ids 6+) is played as the Shell, as stock. One item per at-bat, used on the play.
Custom level only: every stub checks the flag byte (``one_level.FLAG``) and the CPU at the hardest
row (COjyamaCom +0x26 == 3, level 4 underneath); anything else runs the stock instructions. No shared table is edited.

The CPU item AI is GmOjyama::COjyamaCom (object at *(r13-0x350), vtable
0x806CC3B8): +0x0C item id, +0x10/+0x14 cursor (x, z), +0x18 timer, +0x24 slot, +0x25 target fielder, +0x26 level
row, +0x27 item column, +0x28 triggered, +0x2A aim-at-landing-point.

THE PLAY (the author, 2026-09-27, after the 18:24 dump: "start over", one model for every play, only what a human
thrower can see: the ball's flight, each fielder's spot and run speed; his table of the model, approved):
FRAMES [C]: the ball path (game +0x42C + i * 0x14: x, y, z, dist, h) is re-predicted every frame by 800B38C8 from the
ball now: path[i] = i frames FROM NOW (flight, bounces, roll). The wall-crossing frame X (+0x2A4A, FUN_800B5974: the
first path index past the wall distance) is frames from now too. E = frames since contact (*(r13-0x1684)+2).
- THE MEET (meet(), the spec): the first frame K >= 1 (before X when X > 0) at which some fielder (0..8) can take the
  ball: it is at most his catch height (+0x190) + JUMP_REACH up, and his run to within reach of it takes <= K frames:
  max(distance - reach, 0) / speed (+0xEC; 0: 1.0, as FUN_801142B8) + the reaction left, max(+0x225 >> 1,
  HUMAN_REACT) - E (>= 0). The reach (the defense logs of the author's dumps): CATCH_REACH 5.0 for a ball moving at most
  SLOW_BALL a frame on the ground (a catch, +0x2AC 1, starts 4.9 - 6.1 m out and draws the ball in over ~10 frames),
  else BODY_REACH 1.25 (a fast ball: a jump catch, +0x2AC 6, within 0.86 - 1.25 m). That fielder is the target, K
  the meet frame, M the ball then. Nobody (a home run over everyone): no item this frame (log type 3).
  T = K - MEET_LEAD (15, the author: caught in the air or on the ground alike; >= 0; the Boo: K); P = where he is at T:
  running straight from where he is toward M, stopping at the reach (the logs: fielders stop 4.9 - 6 m from the ball
  and take it from there), at the rate of his run time e: F + (M - F) (1 - reach / d) min(T / e, 1). Log path 4.
- THE WALL (kept; a buddy-jump plan, FUN_80125238 plan 8: ball +0x2A71 set (to the wall), +0x2A79 clear, not bounced,
  and either +0x2A75 > 1 (the home-run call of FUN_800B5974) or ball height at the wall-crossing frame +0x2A4A > 3.5
  with the landing point past the wall distance +0x29D8): his spot J = the wall point W (+0x408/+0x40C) moved 5.0
  toward the ball (+0x2A75 > 1) or along the wall normal (+0x410/+0x414), then his radius (+0x1C) toward him, as
  FUN_80125238 does. The jumper = the outfielder (6..8) with the smallest FUN_801142B8 ETA to J. With a
  wall-crossing frame X > 0: K = the first frame before X the ball comes down to BUDDY_Y (25 m) within BUDDY_R
  (10 m) of J (a buddy catch possible; the author's 19:14 dump: the jump starts ~40 frames before the catch, and a high
  ball is caught well before the wall), else X; T = max(K - HR_LEAD, 0) (52, the author: before any buddy jump can start;
  45 until the LF's jump in his 19:30 dump started 46 frames before a 24 m catch); P = where he is running (his spot
  + his direction * speed * T, as the chaser's: stock's aim), not past J (the author's 19:32 dump: the CPU fielder stood
  5.5 m from the plan's J; a human stands where he likes). No crossing frame:
  his jump = the first path index k >= 1 with the ball within 5.0 of J (800E246C), T = max(k - HR_LEAD, his ETA).
  Boo: T = X. No plan: the meet.
Release (the frame the fire flag is set = the throw; timer = T - lag, floored at 0) and aim point C per item, with
the item's row (manager slot +0x33D, 0/1) and 60/50 Hz values read from the stock item tables at build time:
- Shell / Fireball: C = P, lag = D / speed, D = |P - S|, S = ORIGFN(P): where the game launches it (its own camera
  projection, see OBJ_FN). They arrive at P at T. The shot list may put C up to EXT past P instead (higher up the
  screen: S follows the cursor, so a shorter flight and a later release)
- Bob-omb: C = P (centred: the author's 20:11 dump, the CF 5.8 m past the predicted spot; the edge placement, 6.0 -
  EDGE_MARGIN toward the cursor, left 2.5 m on his far side), lag = lob
  frames + fuse + 1 (it explodes at T: FUN_8044f740 stops it and sets the fuse on landing, the state-2 update counts
  it down and blasts the frame after it reaches 0; no blast on contact).
- POW: the same with its burst radius 20.0; lag = lob frames (it bursts on landing, at T).
- Banana (the author: "the one at 12:00 is the one we try to put on the target"): C = P - 5.0 BANANA12, so the 12:00
  banana lands on P; lag = lob frames + BANANA_EARLY (it lands a little before T and waits).
- Boo: C = where the cursor already is (it appears at the ball); lag = Boo frames - BOO_END_AFTER + BOO_EARLY (750 ms:
  45 / 38 frames at 60 / 50 Hz; the author, 2026-09-28), so it is thrown 750 ms before the throw that would end its 90-frame
  effect just after T (it also ends when a fielder has the ball, as stock).
Tighter aim at level 6: no random aim offset and no timer jitter or lead table (the aim is the exact spot), and the
trigger runs from the first frame the slot is armed (the stock one waits until the ball falls, ball +0x384 vy < 0).
LATE THROWS on balls in the air (the author): the release is the frame the timer hits 0 AND the cursor was within 3.0 of
C (0x80450A94) AND the slot's 10-frame countdown (+0x3A8, reloaded to 10 while unarmed, 0x804565BC) is 0. So at
level 6 the decision (1) measures T from now (E above; the old code took +0x2A46 as if decided at contact, one frame
late per frame of arming delay), (2) clears the countdown byte (manager + slot * 0xC + 0x3A8), and (3) sets the
cursor step to max(SPEED6, (|cursor - C| - 2.5) / max(timer, 1)) (SPEED6 = 2.0 / 2.4 at 50 Hz, stock level 4 0.7 /
0.84) so it is in the gate by the release. LATE (timer < 0, the author: "throw for all too late cases"): it goes at once,
every item (the spot still has to be good).

HOOKS (each `b` to a stub; the stock instruction + branch back when the gate fails):
  RESET 0x8045584C lwz r4,-0x34C(r13)  (FUN_80455598 at-bat setup, right after COjyamaCom's reset FUN_8044FDF0 was
        called with the item forced to 0 by `li r5,0` at 0x80455838; r29 = the manager): clears ACTIVE; at level 6
        (and a COjyamaCom with a slot) sets COM +0x0C = the manager's item (+0x338; not 0..5 -> 0) and +0x27 its
        column (min(id, 4)). Done after the call instead of at 0x80455838 so the level row (+0x26, set inside the
        reset) can gate it; the snapshot copy at -0x34C right after copies the real id too.
  DECIDE 0x8045031C cmpwi r0,5  (FUN_80450208 update, the Boo check before the trigger; r30 = COM, r1 frame with
        the target at 8(r1)): at level 6 skips stock's Boo branch; triggered by the stock fallback -> 0x804505D4
        (the stock countdown); a level 6 decision (ACTIVE) is made again every frame until the item goes (the author:
        re-pick the fielder each frame); slot not armed yet (manager + slot * 0xC + 0x33E, FUN_8045630c: items usable, INFERRED to be
        from contact) -> 0x804505EC and wait; else computes T, P, the timer and C (above), sets +0x18/+0x25/+0x28/+0x2A, 8(r1), AIM, ACTIVE
        and goes to 0x804505EC (the aim call). Calls FUN_800AC748, FUN_801142B8 (the wall), sqrt FUN_8053C74C, ORIGFN
        (LR is saved by FUN_80450208; r29 is the ball, as stock).
  AIM   0x80450A68 fsubs f30,f2,f1  (FUN_80450794 aim/move; f1/f0 = cursor, f31 = step factor): at level 6 with
        ACTIVE and +0x28 set, the cursor heads for C (no offset) at the step set by DECIDE; the 3.0 gate stays.
        A SPOT A HUMAN COULD AIM AT (the author: a high-fly POW's cursor went off screen and it never threw; items
        can't be thrown past the walls): each frame, if FUN_804570C8 says C is bad (off screen, past the wall /
        in the stands, or by a base), C = the first good spot of C0 (the planned C) turned around P by TURNS,
        at RINGS of its distance, else P; kept while good. The Bob-omb / POW edge point (6 / 20 m from the
        fielder) keeps its distance when any direction works, so he stays just inside the blast. Up to 32
        checks in a frame, only while C is bad. Timing unchanged.
        FRIENDLY FIRE (the author; Bob-omb, POW, Banana only): a spot is also bad if the blast + RUNNER_PAD (Banana: its
        5 m spread + BANANA_HIT + RUNNER_PAD) reaches one of our runners anywhere on his path to the base he runs to
        (or back to), the base included (the author: bombed its runner on his way to 2nd). No good spot and P would hit him too: no throw that frame (r31 = 0, the fire flag).
        NOTHING ON SCREEN (the author: on a home run the cursor went off the top of the screen): P is not throwable
        either -> the cursor steps toward P if the step stays ON SCREEN (ONSTEPFN: the camera projection only; the
        full check also refuses the bases), else it holds where it is (a human's cursor stops at the screen edge);
        a cursor left off screen (the author's 19:21 dump: the camera turned to left field) heads toward the ball on the
        ground (path[0]; the camera follows the ball), not the target (his 20:17 dump: it followed an off-screen
        target out); no throw that frame.
        SHOT LIST (the author, 2026-09-28, after his 15:04 dump: the middle Fireball, thrown at the CF 64 m out, hit the SS
        42 m short): a Shell / Fireball decision (DECIDE, SHOTS in its own section) scores up to NK candidate shots at
        T + OFF[k] along the target's path (k = 0: the plan's P; the rest led at max(speed, top speed), capped as
        before; the fly-ball landing and a plan without a fielder: P only). Each: S = ORIGFN(P), the travel L / speed,
        the release rel = t - fixed - travel, and the other fielders its path(s) meet (one Shell path; a Fireball's
        three: the middle one to P, and +-FIRE_SPREAD degrees (its table +0x14 = 10; the 15:04 dump's side fireballs
        flew ~10 degrees off), the side ones out to RANGE): fielder i (not the target, and not whoever will be within
        CATCH_R of P when it gets there) at his spot + his run (direction * speed) by the time it passes him is hit
        within HIT_R of the path. Pick: a release still ahead (rel >= 0) first; Shell: the most other fielders hit,
        then the fastest (the shortest travel: it waits longest); Fireball: only shots with nobody else hit, the
        fastest; all late: the least late. A Fireball with no clear shot holds (no throw; re-decided next frame).
        HIT_R, CATCH_R and a Shell going on after a hit are UNMEASURED.
        LINE (the author: items keep going past the cursor): a bad spot for a Shell / Fireball (record speed > 0) first
        tries the spots on the
        line from the launch origin S to P, closest to P first; it passes through P at the planned time.
        NO WRONG-SPOT FALLBACKS (the author, 2026-09-27, after the 16:18 dump: a home-run banana slid back toward where
        the fielder had been and went 2.7 s early; earlier the inward steps threw short): a spot is only ever one
        that still lands the item on him (C0, C0 turned around P at full / smaller radius, P; a Banana's other
        four banana positions; a Shell / Fireball's line to him). None good: the cursor heads there while it can
        stay on screen and holds, with no throw (r31 = 0) until a good spot comes on screen, then it throws at once.
        BANANA: a bad Banana spot first tries the cursor spots that land another of the five bananas on B (the
        12:00 banana's spot); then the search above.
  FLIGHT 0x80453764 lwz r12,0(r3)  (FUN_80453764, the per-object item update called by the dispatch FUN_80455394;
        LOG BUILDS ONLY): a type 6 log record per frame for each in-use (+0x37) Shell / Fireball object (active id,
        manager +0x394, 0 / 1) while it flies (+0x3C: 1 / 2): its position and velocity, so a dump shows where it
        really went and how fast; and on the first frame of a Bob-omb blast / POW burst a type 7 record (the burst
        centre) plus a type 8 record per fielder (his spot, height, airborne; FLOGFN, which the AIM stub also calls
        after each cursor step for the target fielder)
        (the step per frame and the closest pass to the target spot). Object positions are in field space, the
        fielders' and the ball's (the launch FUN_804539D4 flips the cursor's z) [INFERRED]. Not built here:
        ``build`` places no log (``LOG_ENTRIES`` 0), so the FLIGHT hook and ``flog_sub`` are unused.

CONFIRMED (code + stock data): item ids; Shell speed 0.4 / 0.7 per frame (row 0/1; 50 Hz 0.48/0.84, 0x80631008 +8,
straight, normalised); Fireball speed 0.4 / 0.6 (0x8063129C); Bob-omb lob 60 / 30 frames (0x80631058 +0x34), fuse
45 (+0x28), blast radius 6.0 horizontal (+0x0C, FUN_80117408 state 3), blast 30 frames; POW lob 60 / 30
(0x806311D8 +0x2C), burst on landing, radius 20.0 + the fielder's radius (+0x08, FUN_80117E34 state 2), 12 frames;
Banana lob 60 / 30 (0x80631148 +0x20), 5 bananas, spread 5.0 (+0x14), leader +x; Boo 90 frames (60 Hz; 75 at 50 Hz,
0x807A1640). INFERRED: the item flies from S to the cursor (FUN_804539D4); the Fireball's ground speed is its table
speed (it arcs and bounces); followers at angles 72 * k degrees (cos x, sin z); the Bob-omb's fuse runs from
landing (CONFIRMED in code since); the jumper choice. (Wrong until 2026-09-27: that the path is indexed by E.)
UNMEASURED: EDGE_MARGIN, BOO_END_AFTER, SHOT_LAG (frames between the fire flag and the item's first step), HUMAN_REACT
and where a human fielder stops (the reach: measured on the CPU's fielders), the Shell / Fireball speeds in flight (the FLIGHT log records them: compare its step per frame with records()). Timing is to the frame model above (about +-1 frame), not measured.
"""

import math
import struct
import sys

try:
    from ..Dol.ppc import Asm, branch_word, d_form, ha, lo
    from . import defense_block as dbg
    from . import one_level
except ImportError:
    from Dol.ppc import Asm, branch_word, d_form, ha, lo
    from CustomCpu import defense_block as dbg
    from CustomCpu import one_level

FEATURE = "cpu-items"
LEVEL6 = 2
HARDEST = 3
COM_VTABLE = 0x806CC3B8

RESET, RESET_ORIG = 0x8045584C, 0x808DFCB4
DECIDE, DECIDE_ORIG = 0x8045031C, 0x2C000005
AIM, AIM_ORIG = 0x80450A68, 0xEFC20828
TRIGGERED, FALLBACK, AIM_CALL = 0x804505D4, 0x8045045C, 0x804505EC
AIM_BACK = 0x80450A78
FLIGHT, FLIGHT_ORIG = 0x80453764, 0x81830000
CLASS_FN, BALLPOS_FN, CHASE_FN, SQRT_FN = 0x800B0BFC, 0x800AC748, 0x800CDBBC, 0x8053C74C
TEAMS = (-0x1D14, -0x1D10, -0x1D18, -0x1D1C)

R2 = 0x8079EDC0
ZERO, ONE, FLY_Y, EPS = 0x27C0, 0x27D0, 0x27CC, 0x27E0
ORIGIN_X, ORIGIN_Z = 0x6090, 0x6094








OBJ_FN, START_FN, CLIP_FN = 0x80456ABC, 0x804577B8, 0x80454090
ORIGFN = None
ROUTE_T, ROUTE_N, ROUTE_SZ = None, 9, 12







POWFN, POWB = None, None
PQX, PQZ, PRJ = 0, 36, 72
PD, PT, PTX, PTZ, PTR, PCX, PCZ, PBX, PBZ, PHALF, PBEST = (108 + 4 * i for i in range(11))
POWB_SIZE = PBEST + 4





CAM_FN, PROJ_FN = 0x80251D00, 0x80252030
ONSTEPFN = None
FLIGHT_EVERY = 8

BALL, MANAGER, COM, HZ = -0x1D94, -0x354, -0x350, -0x1658
TEAMS = (-0x1D14, -0x1D10, -0x1D18, -0x1D1C)
PATH, PATH_STEP, PATH_MAX = 0x42C, 0x14, 0x1DF
ARMED = 0x33E

SHELL, FIRE, BOMB, POW, BANANA, BOO = range(6)
SPEED6 = (2.0, 2.4)
EDGE_MARGIN = 2.5


BOO_END_AFTER = 1
BOO_EARLY_MS = 750
BOO_EARLY = tuple(int(BOO_EARLY_MS * hz / 1000 + 0.5) for hz in (60, 50))
SHOT_LAG = 0
BOMB_FUSE_EXTRA = 1
MEET_LEAD = 15

KIND_AT, KIND_EDGE, KIND_BANANA, KIND_STAY = range(4)


FIELDERS = 0x80708D78
OUTFIELD = (6, 7, 8)
ETA_FN = 0x801142B8
PLAYSTATE = -0x1684
SPOT_BACK = 5.0
WALL_Y = 3.5
JUMP_D = 5.0
BUDDY_Y, BUDDY_R = 25.0, 10.0

















WALL_IN, IN_AIR_Y = 1.0, 0.5
CHASE_STEP, CHASE_MAX, CHASE_SLACK, CHASE_GROUND = 5, PATH_MAX + 1, 15, 2.0






HOP_D, HOP_MARGIN = 3.7, 5






DASH_NUM, DASH_DEN, CPU_SLOT, BUDDY_CAP = 5, 7, 4, 2.0





RAND_FN, RNG = 0x80165C14, -0x1578
L5_PERFECT, L5_T, L5_K, L5_STEP = 75, 30, 10, 0.25

HR_LEAD = 65






GATE_IN = 2.5
ONSCREEN_FN = 0x804570C8





TURNS = (0, 45, -45, 90, -90, 135, -135, 180)
RINGS = (1.0, 0.75, 0.5, 0.25)
RUNNERS = 0x807098C8
BASES = 0x80625798

R_ON, R_FROM, R_TO, R_DIR = 0x176, 0x17D, 0x17E, 0x183
RUNNER_PAD = 1.5
AT4 = {}
AT5 = {}


L4K = None
L5K = None
BANANA_HIT = 1.5
BANANA_EARLY = 10
LINE_STEP, LINE_TRIES = 0.1, 9




ACTIVE, AIMX, AIMZ, TSAVE, RECP, UX, UZ, SCR, SPEEDS = 0, 4, 8, 0xC, 0x10, 0x14, 0x18, 0x20, 0x28
ELAP, CSPEED, JX, JZ, BEST, BETA, LOOP, CAND = 0x30, 0x34, 0x38, 0x3C, 0x40, 0x44, 0x48, 0x4C
KSPOT, KWALLY, KJUMP, KGATE, MAGIC, JPTR = 0x50, 0x54, 0x58, 0x5C, 0x60, 0x68
PX, PZ, SI, SK, KBACK, C0X, C0Z, ROT, RAD = 0x70, 0x74, 0x78, 0x7C, 0x80, 0x84, 0x88, 0x8C, 0xCC
SUBLR, KFFE, KFFB, RI, KF, KR2, BAN5 = 0xE0, 0xE4, 0xE8, 0xEC, 0xF0, 0xF4, 0xF8
FX, FZ, BSI, TARR, BSK, KSTEP, KLINE = 0x120, 0x124, 0x128, 0x12C, 0x130, 0x144, 0x148
LOGP, LOGLR, DPATH, QRES, HELD, HRWHY = 0x150, 0x154, 0x158, 0x159, 0x15A, 0x15B
K12X, K12Z, FPTR = 0x160, 0x164, 0x168
SX, SZ, LASTOBJ, SF, LASTF, LAUNCHX = 0x170, 0x174, 0x178, 0x17C, 0x180, 0x184
KBUDY, KBUDR2, KGROUND, KWALLIN, KGROUNDY, BETA2, MPTR, KHOPD, KBUDCAP = (0x188, 0x18C, 0x190, 0x194, 0x198,
                                                                        0x19C, 0x1A0, 0x1A4, 0x1A8)
L5T, L5X, L5Z, KL5STEP, L5SAVE = 0x1AC, 0x1B0, 0x1B4, 0x1B8, 0x1BC
KBCD, KBCY, KBCR2, KBCW, RECS = 0x1D0, 0x1D4, 0x1D8, 0x1DC, 0x1E0










BCLEAR_D, BCLEAR_Y, BCLEAR_R, BCLEAR_WALL = 2.5, 2.5, 3.5, 4.0
LATEFIX, LCX, LCZ = 0x15C, 0x16C, 0x1CC
KW = 0xDC

OFF = (0, 10, 20, 30, 45, 60, 80, 100)
HIT_R, RANGE, CATCH_R, FIRE_SPREAD = 1.2, 120.0, 3.0, 10.0
SHOT_SECTION = 0x800
K_HIT, K_RANGE, K_CATCH, K_COS, K_SIN, K_OFF = 0, 4, 8, 12, 16, 20
SH_PTR = K_OFF + 4 * len(OFF)
CPX, CPZ, CSX, CSZ, CL, CREL, CTRAV, CHITS, CT = (SH_PTR + 4 + 4 * i for i in range(9))
BPX, BPZ, BSX, BSZ, BREL, BTRAV, BHITS, BT, BOK = (CT + 4 + 4 * i for i in range(9))
WX, WZ, WR, SSCR = BOK + 4, BOK + 8, BOK + 12, BOK + 16






EXT, EXT_MISS = (15.0, 30.0, 60.0), 0.5
K_EXT = SSCR + 8
K_MISS, EI, XCX, XCZ, XSX, XSZ, XL = (K_EXT + 4 * len(EXT) + 4 * i for i in range(7))
CAX, CAZ, BAX, BAZ = XL + 4, XL + 8, XL + 12, XL + 16
SHOTB_SIZE = BAZ + 4
SHOTB = None









BANANA12 = (0.30901699, 0.95105652)



HR_WHY_CODES = (1, 2, 3, 4, 4, 4, 4, 5, 6, 7)

















LOG_ENTRIES = 0
LOG_REC, LOG_MAGIC = 32, b"L6IL"
LOGFN = None
FLOGFN = None
REC = 16
DATA = None


def _f(dol, a):
    return struct.unpack(">f", dol.read(a, 4))[0]


def records(dol):
    """[(speed, fixed, dist, kind)] for Hz 0..1, row 0..1, item 0..5 (index (hz * 2 + row) * 6 + item), from the
    stock tables in `dol`."""
    out = []
    for hz in range(2):
        for row in range(2):
            shell = 0x80631008 + hz * 0x28 + row * 0x14
            fire = 0x8063129C + hz * 0x40 + row * 0x20
            bomb = 0x80631058 + hz * 0x78 + row * 0x3C
            pow_ = 0x806311D8 + hz * 0x60 + row * 0x30
            ban = 0x80631148 + hz * 0x48 + row * 0x24
            boo = struct.unpack(">h", dol.read(0x807A1640 + hz * 4, 2))[0]
            out += [(_f(dol, shell + 8), SHOT_LAG, 0.0, KIND_AT),
                    (_f(dol, fire), SHOT_LAG, 0.0, KIND_AT),
                    (0.0, int(_f(dol, bomb + 0x34)) + int(_f(dol, bomb + 0x28)) + BOMB_FUSE_EXTRA,
                     _f(dol, bomb + 0xC) - EDGE_MARGIN,
                     KIND_EDGE),
                    (0.0, int(_f(dol, pow_ + 0x2C)), _f(dol, pow_ + 8) - EDGE_MARGIN, KIND_EDGE),
                    (0.0, int(_f(dol, ban + 0x20)), _f(dol, ban + 0x14), KIND_BANANA),
                    (0.0, boo - BOO_END_AFTER + BOO_EARLY[hz], 0.0, KIND_STAY)]
    return out


def bomb_clear(a):
    """BOMB CLEAR's leaf "bclr" (docstring): r11 = the item record, r12 = the data block, r29 = the game (the path);
    AIMX / AIMZ = P in, C out. Uses r0, r5-r10, f0-f13, SCR."""
    a.label("bclr")
    a.lwz(5, TSAVE, 12).lwz(0, 4, 11).add(5, 5, 0)
    a.cmpwi(5, 1).bge("bc_t1").li(5, 60)
    a.label("bc_t1")
    a.cmpwi(5, PATH_MAX).ble("bc_t2").li(5, PATH_MAX)
    a.label("bc_t2")

    a.li(6, 0)
    a.lfs(0, 0x29D8, 29).lfs(1, FLY_Y, 2).fcmpo(0, 1).ble("bc_nw")
    a.lfs(1, KBCW, 12).fsubs(1, 0, 1).fmuls(1, 1, 1).stfs(1, SCR, 12)
    a.lfs(1, KWALLIN, 12).fsubs(13, 0, 1).fmuls(13, 13, 13)
    a.li(7, 1).addi(8, 29, PATH_STEP)
    a.label("bc_w")
    a.cmpw(7, 5).bgt("bc_nw")
    a.lfs(11, PATH, 8).lfs(12, PATH + 8, 8).fmuls(11, 11, 11).fmadds(11, 12, 12, 11).fcmpo(11, 13).bge("bc_wall")
    a.addi(7, 7, 1).addi(8, 8, PATH_STEP).b("bc_w")
    a.label("bc_wall")
    a.addi(5, 7, -1).li(6, 1)
    a.label("bc_nw")
    a.lfs(1, AIMX, 12).lfs(2, AIMZ, 12)
    a.fmuls(5, 1, 1).fmadds(5, 2, 2, 5).lfs(0, ONE, 2).fcmpo(5, 0).blt("bc_ret")
    a.frsqrte(5, 5).fmuls(6, 1, 5).fmuls(7, 2, 5)
    a.lfs(9, KBCY, 12).lfs(10, KBCR2, 12)
    a.li(10, -1)
    a.label("bc_c")
    a.lfs(8, KBCD, 12)
    a.fmr(3, 1).fmr(4, 2)
    a.cmpwi(10, 0).blt("bc_test").bne("bc_1")
    a.fnmsubs(3, 8, 7, 1).fmadds(4, 8, 6, 2).b("bc_test")
    a.label("bc_1")
    a.cmpwi(10, 1).bne("bc_2")
    a.fmadds(3, 8, 7, 1).fnmsubs(4, 8, 6, 2).b("bc_test")
    a.label("bc_2")
    a.cmpwi(10, 2).bne("bc_3")
    a.fmadds(3, 8, 6, 1).fmadds(4, 8, 7, 2).b("bc_test")
    a.label("bc_3")
    a.cmpwi(10, 4).blt("bc_s")
    a.fadds(8, 8, 8)
    a.cmpwi(10, 5).blt("bc_s")
    a.lfs(0, KBCD, 12).fadds(8, 8, 0)
    a.label("bc_s")
    a.fnmsubs(3, 8, 6, 1).fnmsubs(4, 8, 7, 2)
    a.label("bc_test")
    a.cmpwi(6, 0).beq("bc_tp")
    a.fmuls(11, 3, 3).fmadds(11, 4, 4, 11).lfs(12, SCR, 12).fcmpo(11, 12).bge("bc_bad")
    a.label("bc_tp")
    a.li(7, 1).addi(8, 29, PATH_STEP)
    a.label("bc_k")
    a.cmpw(7, 5).bgt("bc_ok")
    a.lfs(11, PATH + 4, 8).fcmpo(11, 9).bgt("bc_n")
    a.lfs(11, PATH, 8).fsubs(11, 11, 3).lfs(12, PATH + 8, 8).fsubs(12, 12, 4)
    a.fmuls(11, 11, 11).fmadds(11, 12, 12, 11).fcmpo(11, 10).blt("bc_bad")
    a.label("bc_n")
    a.addi(7, 7, 1).addi(8, 8, PATH_STEP).b("bc_k")
    a.label("bc_ok")
    a.stfs(3, AIMX, 12).stfs(4, AIMZ, 12)
    a.label("bc_ret")
    a.blr()
    a.label("bc_bad")
    a.addi(10, 10, 1).cmpwi(10, 6).blt("bc_c")
    a.blr()


def bomb_clear_model(P, path, frames, wall=0.0):
    """BOMB CLEAR's spec: path(k) = the ball (x, y, z) k frames from now, frames = min(T or 60, PATH_MAX), wall = the
    game's wall distance (+0x29D8; <= 20: none). -> (C, moved). frsqrte's ~3% ignored (tests allow it)."""
    near_wall = False
    if wall > 20.0:
        for k in range(1, frames + 1):
            x, y, z = path(k)
            if x * x + z * z >= (wall - WALL_IN) ** 2:
                frames, near_wall = k - 1, True
                break

    def clear(cx, cz):
        if near_wall and cx * cx + cz * cz >= (wall - BCLEAR_WALL) ** 2:
            return False
        for k in range(1, frames + 1):
            x, y, z = path(k)
            if y <= BCLEAR_Y and (x - cx) ** 2 + (z - cz) ** 2 < BCLEAR_R ** 2:
                return False
        return True
    px, pz = P
    if px * px + pz * pz < 1.0 or clear(px, pz):
        return (px, pz), False
    n = math.hypot(px, pz)
    ux, uz = px / n, pz / n
    d = BCLEAR_D
    for dx, dz in ((-uz * d, ux * d), (uz * d, -ux * d), (ux * d, uz * d), (-ux * d, -uz * d), (-ux * 2 * d, -uz * 2 * d),
                   (-ux * 3 * d, -uz * 3 * d)):
        c = (px + dx, pz + dz)
        if clear(*c):
            return c, True
    return (px, pz), False


def data_block(dol, logp=0):
    blob = bytearray(RECS)
    struct.pack_into(">I", blob, LOGP, logp)
    struct.pack_into(">2f", blob, SPEEDS, *SPEED6)
    struct.pack_into(">4fQ", blob, KSPOT, SPOT_BACK, WALL_Y, JUMP_D, GATE_IN, 0x4330000080000000)
    for i, deg in enumerate(TURNS):
        struct.pack_into(">2f", blob, ROT + i * 8, math.cos(math.radians(deg)), math.sin(math.radians(deg)))
    struct.pack_into(f">{len(RINGS)}f", blob, RAD, *RINGS)
    struct.pack_into(">2f", blob, KFFE, EDGE_MARGIN + RUNNER_PAD, BANANA_HIT + RUNNER_PAD)
    struct.pack_into(">2f", blob, K12X, *BANANA12)
    struct.pack_into(">5f", blob, KBUDY, BUDDY_Y, BUDDY_R * BUDDY_R, CHASE_GROUND, WALL_IN, IN_AIR_Y)
    struct.pack_into(">2f", blob, KHOPD, HOP_D, BUDDY_CAP)
    struct.pack_into(">f", blob, KL5STEP, L5_STEP)
    struct.pack_into(">f", blob, KLINE, LINE_STEP)
    struct.pack_into(">4f", blob, KBCD, BCLEAR_D, BCLEAR_Y, BCLEAR_R * BCLEAR_R, BCLEAR_WALL)
    for k in range(5):
        struct.pack_into(">2f", blob, BAN5 + k * 8, math.cos(math.radians(72 * k)), math.sin(math.radians(72 * k)))
    for s, fx, d, k in records(dol):
        blob += struct.pack(">fifI", s, fx, d, k)
    global L4K, L5K
    L4K = L5K = None
    me = sys.modules[__name__]
    (sp4, sp5, sp6), (pad4, pad5, pad6) = one_level.pair(me, "SPEED6"), one_level.pair(me, "RUNNER_PAD")
    if sp4 != sp6 or sp5 != sp6 or pad4 != pad6 or pad5 != pad6:
        L4K = len(blob)
        L5K = L4K + 16
        assert L5K + 16 < 0x8000
        blob += struct.pack(">4f", *sp4, EDGE_MARGIN + pad4, BANANA_HIT + pad4)
        blob += struct.pack(">4f", *sp5, EDGE_MARGIN + pad5, BANANA_HIT + pad5)
    return bytes(blob)


def _meet(a, rt, ra, scratch=None):
    """rt = ra - MEET_LEAD, this match's level's (one_level.pair). Differing: via rt itself (rt != ra) or `scratch`
    (clobbered, with cr0)."""
    ml4, ml5, ml6 = one_level.pair(sys.modules[__name__], "MEET_LEAD")
    if ml4 == ml5 == ml6:
        a.addi(rt, ra, -MEET_LEAD)
    elif rt != ra:
        one_level.pick(a, rt, -ml4, -ml5, -ml6).add(rt, rt, ra)
    else:
        one_level.pick(a, scratch, -ml4, -ml5, -ml6).add(rt, rt, scratch)


def _extsb(ra, rs):
    return (31 << 26) | (rs << 21) | (ra << 16) | (954 << 1)


FCTIWZ_F1 = (63 << 26) | (1 << 21) | (1 << 11) | (15 << 1)
FSUB_F2_F2_F3 = (63 << 26) | (2 << 21) | (2 << 16) | (3 << 11) | (20 << 1)
XORIS_R3 = d_form(27, 3, 3, 0x8000)


def _gate(a, reg, skip, cr=0):
    """Branch to `skip` unless a custom match is on. Clobbers `reg` and crN."""
    assert cr == 0, "the gate sets cr0"
    one_level.gate(a, reg, skip, "cpu-items")


def _data(a, reg):
    a.lis(reg, ha(DATA)).addi(reg, reg, lo(DATA))


def reset_stub(at):
    a = Asm(at)
    _data(a, 12)
    a.li(0, 0).stw(0, ACTIVE, 12)
    _gate(a, 12, "out")
    a.lwz(3, COM, 13).cmpwi(3, 0).beq("out")
    a.lwz(0, 0, 3).lis(12, ha(COM_VTABLE)).addi(12, 12, lo(COM_VTABLE)).cmpw(0, 12).bne("out")
    a.lbz(0, 0x24, 3).word(_extsb(0, 0)).cmpwi(0, 0).blt("out")
    a.lbz(0, 0x26, 3).cmpwi(0, HARDEST).bne("out")
    a.lwz(5, 0x338, 29).cmplwi(5, BOO).ble("id")
    a.li(5, SHELL)
    a.label("id")
    a.stw(5, 0x0C, 3).cmpwi(5, 4).ble("col")
    a.li(5, 4)
    a.label("col")
    a.stb(5, 0x27, 3)
    a.label("out")
    a.word(RESET_ORIG)
    a.b(RESET + 4)
    return a


def _hr_block(a):
    """Home run (the buddy-jump plan, FUN_80125238 plan 8): r3 = T, r4 = the jumper, f1/f2 = his spot -> "common";
    anything else -> "fly" (the cases as before). r12 = the data block on entry."""
    a.lbz(0, 0x2A71, 29).cmpwi(0, 0).beq("hf0")
    a.lbz(0, 0x2A79, 29).cmpwi(0, 0).bne("hf1")
    a.lha(0, 0x2A4E, 29).cmpwi(0, 0).bne("hf2")
    a.lbz(0, 0x2A75, 29).cmplwi(0, 1).ble("hr_b")
    a.mr(3, 29).bl(BALLPOS_FN)
    _data(a, 12)
    a.lfs(1, 0, 3).lfs(2, 8, 3).lfs(3, 0x408, 29).lfs(4, 0x40C, 29)
    a.fsubs(1, 1, 3).fsubs(2, 2, 4).stfs(1, UX, 12).stfs(2, UZ, 12)
    a.fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.lfs(0, EPS, 2).fcmpo(1, 0).ble("hf3")
    a.bl(SQRT_FN)
    _data(a, 12)
    a.lfs(0, KSPOT, 12).fdivs(5, 0, 1).lfs(3, UX, 12).lfs(4, UZ, 12)
    a.b("hr_spot")
    a.label("hr_b")
    a.lha(3, 0x2A4A, 29).cmpwi(3, 0).blt("hr_p8")
    a.cmpwi(3, PATH_MAX).ble("hr_f")
    a.li(3, PATH_MAX)
    a.label("hr_f")
    a.mulli(3, 3, PATH_STEP).add(3, 3, 29).lfs(1, PATH + 4, 3).lfs(0, KWALLY, 12).fcmpo(1, 0).ble("hr_p8")
    a.lfs(1, 0x400, 29).lfs(2, 0x404, 29).fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.lfs(2, 0x29D8, 29).fmuls(2, 2, 2).fcmpo(1, 2).ble("hr_p8")
    a.label("hr_wn")
    a.lfs(5, KSPOT, 12).lfs(3, 0x410, 29).lfs(4, 0x414, 29)
    a.label("hr_spot")
    a.lfs(1, 0x408, 29).lfs(2, 0x40C, 29).fmadds(1, 3, 5, 1).fmadds(2, 4, 5, 2)
    a.stfs(1, JX, 12).stfs(2, JZ, 12)
    a.li(0, -1).stw(0, BEST, 12).li(0, 0x7FFF).stw(0, BETA, 12).li(0, OUTFIELD[0]).stw(0, LOOP, 12)
    a.li(0, 0x7FFF).stw(0, BETA2, 12).li(0, 0).stw(0, MPTR, 12)
    a.label("hr_loop")
    _data(a, 12)
    a.lwz(5, LOOP, 12).slwi(6, 5, 2).lis(7, ha(FIELDERS)).addi(7, 7, lo(FIELDERS)).add(7, 7, 6)
    a.lwz(3, 0x24, 7).cmpwi(3, 0).bne("hr_have")
    a.lwz(3, 0x48, 7).cmpwi(3, 0).bne("hr_have")
    a.lwz(3, 0, 7).cmpwi(3, 0).beq("hr_next")
    a.label("hr_have")
    a.stw(3, CAND, 12).lfs(1, JX, 12).lfs(2, JZ, 12).bl(ETA_FN)
    _data(a, 12)
    a.lwz(0, BETA, 12).cmpw(3, 0).bge("hr_2nd")
    a.lwz(0, BEST, 12).cmpwi(0, 0).blt("hr_1st")
    a.lwz(0, BETA, 12).stw(0, BETA2, 12).lwz(0, JPTR, 12).stw(0, MPTR, 12)
    a.label("hr_1st")
    a.stw(3, BETA, 12).lwz(0, LOOP, 12).stw(0, BEST, 12).lwz(0, CAND, 12).stw(0, JPTR, 12)
    a.b("hr_next")
    a.label("hr_2nd")
    a.lwz(0, BETA2, 12).cmpw(3, 0).bge("hr_next")
    a.stw(3, BETA2, 12).lwz(0, CAND, 12).stw(0, MPTR, 12)
    a.label("hr_next")
    a.lwz(5, LOOP, 12).addi(5, 5, 1).stw(5, LOOP, 12).cmpwi(5, OUTFIELD[-1]).ble("hr_loop")
    a.lwz(0, BEST, 12).cmpwi(0, 0).blt("hf7")
    a.lwz(3, JPTR, 12).lfs(1, 4, 3).lfs(2, 0xC, 3).lfs(3, JX, 12).lfs(4, JZ, 12)
    a.fsubs(1, 1, 3).fsubs(2, 2, 4).stfs(1, UX, 12).stfs(2, UZ, 12).fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.lfs(0, EPS, 2).fcmpo(1, 0).ble("hr_jf")
    a.bl(SQRT_FN)
    _data(a, 12)
    a.lwz(3, JPTR, 12).lfs(0, 0x1C, 3).fdivs(5, 0, 1).lfs(3, UX, 12).lfs(4, UZ, 12)
    a.lfs(1, JX, 12).lfs(2, JZ, 12).fmadds(1, 3, 5, 1).fmadds(2, 4, 5, 2).stfs(1, JX, 12).stfs(2, JZ, 12)
    a.label("hr_jf")



    if ROUTE_T is not None:
        a.lwz(3, JPTR, 12).lbz(4, 0x21C, 3).cmplwi(4, ROUTE_N - 1).bgt("hr_jo")
        a.load_addr(5, ROUTE_T).mulli(4, 4, ROUTE_SZ).add(5, 5, 4)
        a.lwz(6, PLAYSTATE, 13).cmpwi(6, 0).beq("hr_jo")
        a.lha(6, 2, 6).lwz(7, 8, 5).subf(6, 7, 6).cmplwi(6, 1).bgt("hr_jo")
        a.lfs(1, 0, 5).stfs(1, JX, 12).lfs(2, 4, 5).stfs(2, JZ, 12)
        a.label("hr_jo")






    a.lha(7, 0x2A4A, 29).cmpwi(7, 0).ble("hr_scan")




    a.lwz(0, 0x0C, 30).cmplwi(0, FIRE).bgt("hk_go")
    a.li(5, 1).addi(6, 29, PATH_STEP).lfs(3, JX, 12).lfs(4, JZ, 12).lfs(0, KJUMP, 12).fmuls(0, 0, 0)
    a.label("hj")
    a.cmpw(5, 7).bge("hk_x")
    a.cmpwi(5, PATH_MAX).bgt("hk_x")
    a.lfs(1, PATH, 6).fsubs(1, 1, 3).lfs(2, PATH + 8, 6).fsubs(2, 2, 4).fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.fcmpo(1, 0).ble("hk_y")
    a.addi(5, 5, 1).addi(6, 6, PATH_STEP).b("hj")
    a.label("hk_go")


    a.li(5, 1).addi(6, 29, PATH_STEP).lfs(3, JX, 12).lfs(4, JZ, 12)
    a.label("hk")
    a.cmpw(5, 7).bge("hk_x")
    a.cmpwi(5, PATH_MAX).bgt("hk_x")
    a.lfs(0, PATH + 4, 6).lfs(1, PATH + 4 - PATH_STEP, 6).fcmpo(0, 1).bge("hk_n")
    a.lfs(1, KBUDY, 12).fcmpo(0, 1).bgt("hk_n")
    a.lfs(1, PATH, 6).fsubs(1, 1, 3).lfs(2, PATH + 8, 6).fsubs(2, 2, 4).fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.lfs(2, KBUDR2, 12).fcmpo(1, 2).ble("hk_y")
    a.label("hk_n")
    a.addi(5, 5, 1).addi(6, 6, PATH_STEP).b("hk")
    a.label("hk_y")
    a.mr(7, 5)
    a.label("hk_x")



    a.lwz(0, 0x0C, 30).cmplwi(0, FIRE).bgt("hk_lob")
    _meet(a, 3, 7)
    a.b("hk_c")
    a.label("hk_lob")
    a.addi(3, 7, -HR_LEAD)
    _data(a, 12)



    a.lwz(0, 0x0C, 30).cmplwi(0, BANANA).bne("hk_nb")




    a.lwz(4, JPTR, 12).lfs(1, 4, 4).lfs(3, JX, 12).fsubs(1, 1, 3).lfs(2, 0xC, 4).lfs(3, JZ, 12).fsubs(2, 2, 3)
    a.fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.lfs(0, EPS, 2).fcmpo(1, 0).bgt("hb_q")
    a.lfs(1, ZERO, 2).b("hb_d")
    a.label("hb_q")
    a.bl(SQRT_FN)
    a.label("hb_d")
    _data(a, 12)
    a.lwz(4, JPTR, 12).lfs(2, 0xE4, 4).lfs(3, 0xEC, 4).fcmpo(2, 3).bge("hb_v")
    a.fmr(2, 3)
    a.label("hb_v")
    a.lfs(0, ZERO, 2).fcmpo(2, 0).bgt("hb_s")
    a.lfs(2, -0x75FC, 2)
    a.label("hb_s")
    a.fdivs(1, 1, 2)
    a.word(FCTIWZ_F1).word(d_form(54, 1, 12, SCR)).lwz(3, SCR + 4, 12)
    a.label("hk_nb")

    a.lwz(4, MPTR, 12).cmpwi(4, 0).beq("hk_c")
    a.stw(3, SF, 12)
    a.lfs(1, 4, 4).lfs(3, JX, 12).fsubs(1, 1, 3).lfs(2, 0xC, 4).lfs(3, JZ, 12).fsubs(2, 2, 3)
    a.fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.lfs(0, EPS, 2).fcmpo(1, 0).bgt("hk_q")
    a.lfs(1, ZERO, 2).b("hk_d")
    a.label("hk_q")
    a.bl(SQRT_FN)
    a.label("hk_d")
    _data(a, 12)
    a.lwz(4, MPTR, 12).lfs(0, KHOPD, 12).fsubs(1, 1, 0)
    a.lfs(2, 0xE4, 4).lfs(3, 0xEC, 4).lfs(0, KBUDCAP, 12).fmuls(3, 3, 0).fcmpo(2, 3).bge("hk_v")
    a.fmr(2, 3)
    a.label("hk_v")
    a.lfs(0, ZERO, 2).fcmpo(2, 0).bgt("hk_s")
    a.lfs(2, -0x75FC, 2)
    a.label("hk_s")
    a.fdivs(1, 1, 2)
    a.word(FCTIWZ_F1).word(d_form(54, 1, 12, SCR)).lwz(6, SCR + 4, 12)
    a.addi(6, 6, -HOP_MARGIN).lwz(3, SF, 12)
    a.cmpw(6, 3).bge("hk_c")
    a.mr(3, 6)
    a.label("hk_c")
    a.cmpwi(3, 0).bge("hr_c1")
    a.li(3, 0)
    a.label("hr_c1")
    a.b("hr_eta")
    a.label("hr_scan")
    a.li(5, 1).addi(6, 29, PATH_STEP)
    a.lfs(3, JX, 12).lfs(4, JZ, 12).lfs(0, KJUMP, 12).fmuls(0, 0, 0)
    a.label("hr_fl")
    a.cmpwi(5, PATH_MAX).bgt("hf8")
    a.lfs(1, PATH, 6).lfs(2, PATH + 8, 6).fsubs(1, 1, 3).fsubs(2, 2, 4).fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.fcmpo(1, 0).ble("hr_found")
    a.addi(5, 5, 1).addi(6, 6, PATH_STEP).b("hr_fl")
    a.label("hr_found")
    a.lwz(0, BETA, 12).cmpw(0, 5).bgt("hf9")
    a.addi(3, 5, -HR_LEAD)
    a.lwz(0, 0x0C, 30).cmplwi(0, FIRE).bgt("hr_lob")
    _meet(a, 3, 5)
    a.label("hr_lob")
    a.lwz(0, 0x0C, 30).cmplwi(0, BANANA).bne("hr_nb")
    a.lwz(3, BETA, 12)
    a.label("hr_nb")
    a.lwz(0, BETA, 12).cmpw(3, 0).bge("hr_eta")
    a.mr(3, 0)
    a.label("hr_eta")
    a.lwz(5, 0x0C, 30).cmpwi(5, BOO).bne("hr_go")
    a.lha(6, 0x2A4A, 29).cmpwi(6, 0).blt("hr_go")
    a.mr(3, 6)
    a.label("hr_go")
    a.li(0, 0).stb(0, DPATH, 12)
    a.lfs(1, JX, 12).stfs(1, UX, 12).lfs(2, JZ, 12).stfs(2, UZ, 12)
    a.lwz(4, JPTR, 12).lfs(1, 4, 4).stfs(1, JX, 12).lfs(2, 0xC, 4).stfs(2, JZ, 12)
    a.mr(6, 3).mr(3, 4).b("ch_t0")




    a.label("hr_p8")
    for fid in OUTFIELD:
        a.load_addr(7, FIELDERS + 4 * fid)
        a.lwz(3, 0x24, 7).cmpwi(3, 0).bne(f"p8h{fid}")
        a.lwz(3, 0x48, 7).cmpwi(3, 0).bne(f"p8h{fid}")
        a.lwz(3, 0, 7).cmpwi(3, 0).beq(f"p8n{fid}")
        a.label(f"p8h{fid}")
        a.lbz(0, 0x22C, 3).cmpwi(0, 8).beq("hr_p8y")
        a.label(f"p8n{fid}")
    _data(a, 11)
    a.li(0, 4).stb(0, HRWHY, 11).b("fly")
    a.label("hr_p8y")
    _data(a, 12)
    a.b("hr_wn")
    for k, why in enumerate(HR_WHY_CODES):
        a.label(f"hf{k}")
        _data(a, 11)
        a.li(0, why).stb(0, HRWHY, 11).b("fly")


def _i2f(a, src, fd):
    """fd = (double) (s32) r`src`, via SCR / MAGIC in the data block at r12. Uses r0, r11, f0."""
    a.word(d_form(27, src, 0, 0x8000))
    a.lis(11, 0x4330).stw(11, SCR, 12).stw(0, SCR + 4, 12)
    a.word(d_form(50, fd, 12, SCR)).word(d_form(50, 0, 12, MAGIC))
    a.word((63 << 26) | (fd << 21) | (fd << 16) | (0 << 11) | (20 << 1))


def _i2f4(a, src, fd):
    """fd = (double) (s32) r`src`, via SCR / MAGIC in the data block at r4. Uses r0, r11, f0."""
    a.word(d_form(27, src, 0, 0x8000))
    a.lis(11, 0x4330).stw(11, SCR, 4).stw(0, SCR + 4, 4)
    a.word(d_form(50, fd, 4, SCR)).word(d_form(50, 0, 4, MAGIC))
    a.word((63 << 26) | (fd << 21) | (fd << 16) | (0 << 11) | (20 << 1))


def _fielder_ptr(a, tag):
    """r3 = the fielder object for index r5 (+0x24, then +0x48, then the base table, as stock), or 0. Uses r6, r7."""
    a.slwi(6, 5, 2).lis(7, ha(FIELDERS)).addi(7, 7, lo(FIELDERS)).add(7, 7, 6)
    a.lwz(3, 0x24, 7).cmpwi(3, 0).bne(tag)
    a.lwz(3, 0x48, 7).cmpwi(3, 0).bne(tag)
    a.lwz(3, 0, 7)
    a.label(tag)


def decide_stub(at):
    a = Asm(at)
    _gate(a, 3, "stock")
    a.lbz(3, 0x26, 30).cmpwi(3, HARDEST).bne("stock")


    a.lbz(0, 0x28, 30).cmpwi(0, 0).beq("fresh")
    _data(a, 12)
    a.lwz(0, ACTIVE, 12).cmpwi(0, 0).beq("trig")
    a.label("fresh")
    _data(a, 11)
    a.li(0, 0).stb(0, HRWHY, 11).stb(0, LATEFIX, 11)
    a.lwz(5, MANAGER, 13).lbz(6, 0x24, 30).mulli(6, 6, 0xC).add(5, 5, 6)
    a.lbz(0, ARMED, 5).cmpwi(0, 0).beq("wait")
    a.lwz(29, BALL, 13)
    a.lwz(3, PLAYSTATE, 13).lha(0, 2, 3).cmpwi(0, 0).bge("elap")
    a.li(0, 0)
    a.label("elap")
    _data(a, 12)
    a.stw(0, ELAP, 12)
    _hr_block(a)






    a.label("fly")
    a.lha(0, 0x2A4E, 29).cmpwi(0, 0).bne("chase")
    a.mr(3, 29).li(4, -1).bl(CLASS_FN).cmpwi(3, 4).bge("chase")
    _data(a, 12)
    a.li(0, 1).stb(0, DPATH, 12)
    a.lha(3, 0x2A44, 29).lwz(0, ELAP, 12).subf(3, 0, 3)
    a.lwz(0, 0x0C, 30).cmpwi(0, BOO).beq("fl_t")
    _meet(a, 3, 3, scratch=5)
    a.label("fl_t")
    a.cmpwi(3, 0).bge("fl_t0")
    a.li(3, 0)
    a.label("fl_t0")
    a.stw(3, CAND, 12).bl("wallk").cmpwi(3, 0).bge("fl_w")
    a.lwz(3, CAND, 12)
    a.label("fl_w")










    a.stw(3, CAND, 12)
    a.lwz(0, 0x0C, 30).cmplwi(0, BANANA).beq("fb_go").cmplwi(0, FIRE).bgt("fl_nb")
    a.label("fb_go")
    a.li(0, -1).stw(0, BEST, 12).li(0, 0x7FFF).stw(0, BETA, 12).li(0, 0).stw(0, LOOP, 12)
    a.label("fb_l")
    _data(a, 12)
    a.lwz(5, LOOP, 12)
    _fielder_ptr(a, "fb_p")
    a.cmpwi(3, 0).beq("fb_n")
    _data(a, 12)
    a.stw(3, FPTR, 12)
    a.lfs(1, 4, 3).lfs(3, 0x400, 29).fsubs(1, 1, 3).lfs(2, 0xC, 3).lfs(3, 0x404, 29).fsubs(2, 2, 3)
    a.fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.lfs(0, EPS, 2).fcmpo(1, 0).bgt("fb_q")
    a.lfs(1, ZERO, 2).b("fb_d")
    a.label("fb_q")
    a.bl(SQRT_FN)
    a.label("fb_d")
    _data(a, 12)
    a.lwz(3, FPTR, 12).lfs(2, 0xE4, 3).lfs(3, 0xEC, 3).lfs(0, KBUDCAP, 12).fmuls(3, 3, 0).fcmpo(2, 3).bge("fb_v")
    a.fmr(2, 3)
    a.label("fb_v")
    a.lfs(0, ZERO, 2).fcmpo(2, 0).bgt("fb_s")
    a.lfs(2, -0x75FC, 2)
    a.label("fb_s")
    a.fdivs(1, 1, 2)
    a.word(FCTIWZ_F1).word(d_form(54, 1, 12, SCR)).lwz(6, SCR + 4, 12)
    a.lwz(0, BETA, 12).cmpw(6, 0).bge("fb_n")
    a.stw(6, BETA, 12).lwz(0, LOOP, 12).stw(0, BEST, 12).lwz(0, FPTR, 12).stw(0, JPTR, 12)
    a.label("fb_n")
    _data(a, 12)
    a.lwz(5, LOOP, 12).addi(5, 5, 1).stw(5, LOOP, 12).cmpwi(5, 8).ble("fb_l")
    a.lwz(4, BEST, 12).cmpwi(4, 0).blt("nomeet")
    a.lwz(3, JPTR, 12).lfs(1, 4, 3).lfs(3, 0x400, 29).fsubs(1, 1, 3).lfs(2, 0xC, 3).lfs(3, 0x404, 29).fsubs(2, 2, 3)
    a.stfs(1, UX, 12).stfs(2, UZ, 12).fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.lfs(0, EPS, 2).fcmpo(1, 0).ble("nomeet")
    a.bl(SQRT_FN)
    _data(a, 12)
    a.lwz(3, JPTR, 12).lfs(4, 0x1C, 3).fsubs(5, 1, 4)
    a.fdivs(6, 4, 1)
    a.lfs(7, 0xE4, 3).lfs(8, 0xEC, 3).fcmpo(7, 8).bge("fb_w")
    a.fmr(7, 8)
    a.label("fb_w")
    a.lfs(0, ZERO, 2).fcmpo(7, 0).bgt("fb_x")
    a.lfs(7, -0x75FC, 2)
    a.label("fb_x")
    a.fdivs(5, 5, 7)
    a.word((63 << 26) | (5 << 21) | (5 << 11) | (15 << 1)).word(d_form(54, 5, 12, SCR)).lwz(3, SCR + 4, 12)
    a.lfs(1, 0x400, 29).lfs(7, UX, 12).fmadds(1, 7, 6, 1)
    a.lfs(2, 0x404, 29).lfs(7, UZ, 12).fmadds(2, 7, 6, 2)
    a.stfs(1, LCX, 12).stfs(2, LCZ, 12)
    a.lwz(3, CAND, 12)
    a.label("fb_t")
    a.lwz(4, BEST, 12).li(0, 1).stb(0, 0x2A, 30).b("common")
    a.label("fl_nb")
    a.lfs(1, 0x400, 29).lfs(2, 0x404, 29)
    a.li(0, 1).stb(0, 0x2A, 30).li(4, -1).b("common")
    a.label("chase")
    _data(a, 12)



    a.li(0, PATH_MAX + 1).stw(0, KW, 12)
    a.lfs(0, 0x29D8, 29).lfs(1, FLY_Y, 2).fcmpo(0, 1).ble("kw_x")
    a.lfs(1, KWALLIN, 12).fsubs(0, 0, 1).fmuls(0, 0, 0)
    a.li(5, 1).addi(6, 29, PATH_STEP)
    a.label("kw_l")
    a.cmpwi(5, PATH_MAX).bgt("kw_x")
    a.lfs(1, PATH, 6).lfs(2, PATH + 8, 6).fmuls(1, 1, 1).fmadds(1, 2, 2, 1).fcmpo(1, 0).bge("kw_y")
    a.addi(5, 5, 1).addi(6, 6, PATH_STEP).b("kw_l")
    a.label("kw_y")
    a.cmpwi(5, 2).bge("kw_s").li(5, 2)
    a.label("kw_s")
    a.stw(5, KW, 12)
    a.label("kw_x")
    a.li(0, 9999).stw(0, BETA, 12).li(0, -1).stw(0, BEST, 12).li(0, 0).stw(0, CAND, 12)
    a.label("cs_f")
    _data(a, 12)
    a.lwz(5, CAND, 12)
    _fielder_ptr(a, "cs_p")
    a.cmpwi(3, 0).beq("cs_nf")
    _data(a, 12)
    a.stw(3, FPTR, 12).li(0, 1).stw(0, LOOP, 12)
    a.label("cs_k")
    _data(a, 12)
    a.lwz(5, LOOP, 12).cmpwi(5, CHASE_MAX).bge("cs_nf")
    a.mr(6, 5).cmpwi(6, PATH_MAX).ble("cs_kk")
    a.li(6, PATH_MAX)
    a.label("cs_kk")
    a.lwz(7, KW, 12).addi(7, 7, -1).cmpw(6, 7).ble("cs_kw")
    a.mr(6, 7)
    a.label("cs_kw")
    a.mulli(6, 6, PATH_STEP).add(6, 6, 29)
    a.lha(0, 0x2A4E, 29).cmpwi(0, 0).bne("cs_any")
    a.lfs(0, PATH + 4, 6).lfs(1, KGROUND, 12).fcmpo(0, 1).bgt("cs_next")
    a.label("cs_any")
    a.lwz(3, FPTR, 12).lfs(1, PATH, 6).lfs(2, PATH + 8, 6).bl(ETA_FN)
    _data(a, 12)
    a.lwz(4, FPTR, 12).lbz(0, 0x2C9, 4).cmplwi(0, CPU_SLOT).bge("cs_cpu")
    a.mulli(3, 3, DASH_NUM).li(0, DASH_DEN).divwu(3, 3, 0)
    a.label("cs_cpu")
    a.lwz(5, LOOP, 12).addi(0, 5, CHASE_SLACK).cmpw(3, 0).bge("cs_next")
    a.lwz(0, BETA, 12).cmpw(3, 0).bge("cs_nf")
    a.stw(3, BETA, 12).lwz(0, CAND, 12).stw(0, BEST, 12).b("cs_nf")
    a.label("cs_next")
    a.lwz(5, LOOP, 12).addi(5, 5, CHASE_STEP).stw(5, LOOP, 12).b("cs_k")
    a.label("cs_nf")
    _data(a, 12)
    a.lwz(5, CAND, 12).addi(5, 5, 1).stw(5, CAND, 12).cmpwi(5, 8).ble("cs_f")
    a.lwz(4, BEST, 12).cmpwi(4, 0).blt("nomeet")
    a.lwz(3, BETA, 12)
    a.li(0, 2).stb(0, DPATH, 12)
    a.stw(3, BETA, 12).stw(4, BEST, 12)
    a.mr(5, 3).cmpwi(5, 1).bge("ch_lo")
    a.li(5, 1)
    a.label("ch_lo")
    a.cmpwi(5, PATH_MAX).ble("ch_hi")
    a.li(5, PATH_MAX)
    a.label("ch_hi")
    a.lwz(7, KW, 12).addi(7, 7, -1).cmpw(5, 7).ble("ch_kw")
    a.mr(5, 7)
    a.label("ch_kw")
    a.mulli(5, 5, PATH_STEP).add(5, 5, 29).lfs(1, PATH, 5).lfs(2, PATH + 8, 5)
    a.stfs(1, UX, 12).stfs(2, UZ, 12)
    a.lwz(5, BEST, 12)
    _fielder_ptr(a, "ch_f")
    a.cmpwi(3, 0).beq("nomeet")
    _data(a, 12)
    a.stw(3, JPTR, 12).lfs(1, 4, 3).stfs(1, JX, 12).lfs(2, 0xC, 3).stfs(2, JZ, 12)
    a.lwz(6, BETA, 12).lwz(0, 0x0C, 30).cmpwi(0, BOO).beq("ch_t")
    _meet(a, 6, 6, scratch=5)
    a.label("ch_t")
    a.stw(6, CAND, 12).bl("wallk").cmpwi(3, 0).blt("ch_nw")
    a.stw(3, CAND, 12)
    a.label("ch_nw")
    a.lwz(6, CAND, 12).lwz(3, JPTR, 12)
    a.cmpwi(6, 0).bge("ch_t0")
    a.li(6, 0)
    a.label("ch_t0")
    a.lfs(0, UX, 12).stfs(0, LCX, 12).lfs(0, UZ, 12).stfs(0, LCZ, 12)
    a.stw(6, CAND, 12)
    a.lfs(0, ZERO, 2)
    a.lbz(7, 0x251, 3).addi(7, 7, -1).cmplwi(7, 8).ble("ch_l")
    _i2f(a, 6, 4)
    a.lwz(3, JPTR, 12).lfs(3, 0xE4, 3)
    a.lbz(0, LATEFIX, 12).cmpwi(0, 0).beq("ch_sp")
    a.lfs(5, 0xEC, 3).fcmpo(5, 3).ble("ch_sp")
    a.fmr(3, 5)
    a.label("ch_sp")
    a.fmuls(0, 3, 4)
    a.label("ch_l")
    a.stfs(0, SF, 12)
    a.lfs(1, UX, 12).lfs(3, JX, 12).fsubs(1, 1, 3)
    a.lfs(2, UZ, 12).lfs(4, JZ, 12).fsubs(2, 2, 4)
    a.fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.lfs(0, EPS, 2).fcmpo(1, 0).bgt("ch_sq")
    a.lfs(1, ZERO, 2).b("ch_d")
    a.label("ch_sq")
    a.bl(SQRT_FN)
    a.label("ch_d")
    _data(a, 12)
    a.lfs(0, SF, 12).fcmpo(0, 1).ble("ch_lead")
    a.lfs(1, UX, 12).lfs(2, UZ, 12).b("ch_go")
    a.label("ch_lead")



    a.lfs(5, EPS, 2).fcmpo(1, 5).ble("ch_hd")
    a.lfs(3, UX, 12).lfs(5, JX, 12).fsubs(3, 3, 5).fdivs(3, 3, 1)
    a.lfs(4, UZ, 12).lfs(5, JZ, 12).fsubs(4, 4, 5).fdivs(4, 4, 1).b("ch_dir")
    a.label("ch_hd")
    a.lwz(3, JPTR, 12).lfs(3, 0x94, 3).lfs(4, 0x98, 3)
    a.label("ch_dir")
    a.lfs(1, JX, 12).fmadds(1, 3, 0, 1).lfs(2, JZ, 12).fmadds(2, 4, 0, 2)
    a.label("ch_go")
    a.lwz(3, CAND, 12).lwz(4, BEST, 12)
    a.li(0, 0).stb(0, 0x2A, 30).b("common")
    a.label("wallk")
    a.li(3, -1)
    a.lfs(0, 0x29D8, 29).lfs(1, KWALLIN, 12).fsubs(0, 0, 1).fmuls(0, 0, 0)
    a.li(5, 1).addi(6, 29, PATH_STEP)
    a.label("wk_l")
    a.cmpwi(5, PATH_MAX).bgt("wk_x")
    a.lfs(1, PATH + 4, 6).lfs(2, KGROUNDY, 12).fcmpo(1, 2).ble("wk_x")
    a.lfs(1, PATH, 6).lfs(2, PATH + 8, 6).fmuls(1, 1, 1).fmadds(1, 2, 2, 1).fcmpo(1, 0).bge("wk_y")
    a.addi(5, 5, 1).addi(6, 6, PATH_STEP).b("wk_l")
    a.label("wk_y")
    a.mr(3, 5)
    a.label("wk_x")
    a.blr()
    a.label("nomeet")
    a.li(0, 0).stb(0, 0x28, 30)
    a.li(3, 3).li(4, 0).bl(LOGFN)
    a.b("wait")
    a.label("common")
    a.lis(11, ha(one_level.FLAG)).lbz(11, lo(one_level.FLAG), 11)
    a.cmpwi(11, 1).beq("c_err").cmpwi(11, 4).bne("c_go")
    a.label("c_err")
    _data(a, 12)
    a.stw(3, L5SAVE, 12).stw(4, L5SAVE + 4, 12).stfs(1, L5SAVE + 8, 12).stfs(2, L5SAVE + 12, 12)
    a.lwz(0, ACTIVE, 12).cmpwi(0, 0).bne("c_have")
    a.li(0, 0).stw(0, L5T, 12).stw(0, L5X, 12).stw(0, L5Z, 12)
    perf4 = getattr(sys.modules[__name__], "AT4", {}).get("L5_PERFECT", L5_PERFECT)
    t4 = getattr(sys.modules[__name__], "AT4", {}).get("L5_T", L5_T)
    k4 = getattr(sys.modules[__name__], "AT4", {}).get("L5_K", L5_K)
    a.lwz(3, RNG, 13).li(4, 100).bl(RAND_FN)
    one_level.pick(a, 11, perf4, L5_PERFECT, L5_PERFECT).cmpw(3, 11).blt("c_have")
    a.lwz(3, RNG, 13)
    one_level.pick(a, 4, 2 * t4 + 1, 2 * L5_T + 1, 2 * L5_T + 1).bl(RAND_FN)
    _data(a, 12)
    one_level.pick(a, 11, t4, L5_T, L5_T).subf(3, 11, 3).stw(3, L5T, 12)
    for off in (L5X, L5Z):
        a.lwz(3, RNG, 13)
        one_level.pick(a, 4, 2 * k4 + 1, 2 * L5_K + 1, 2 * L5_K + 1).bl(RAND_FN)
        _data(a, 12)
        one_level.pick(a, 11, k4, L5_K, L5_K).subf(3, 11, 3)
        _i2f(a, 3, 1)
        a.lfs(0, KL5STEP, 12).fmuls(1, 1, 0).stfs(1, off, 12)
    a.label("c_have")
    _data(a, 12)
    a.lwz(3, L5SAVE, 12).lwz(0, L5T, 12).add(3, 3, 0).lwz(4, L5SAVE + 4, 12)
    a.lfs(1, L5SAVE + 8, 12).lfs(0, L5X, 12).fadds(1, 1, 0)
    a.lfs(2, L5SAVE + 12, 12).lfs(0, L5Z, 12).fadds(2, 2, 0)
    a.label("c_go")
    a.stw(4, 8, 1).stb(4, 0x25, 30).li(0, 1).stb(0, 0x28, 30)
    _data(a, 12)
    a.stw(3, TARR, 12)
    a.stfs(1, AIMX, 12).stfs(2, AIMZ, 12).stfs(1, PX, 12).stfs(2, PZ, 12)
    a.lwz(5, 0x0C, 30).cmplwi(5, BOO).ble("item")
    a.li(5, SHELL)
    a.label("item")
    a.lbz(6, HZ, 13).cmplwi(6, 1).ble("hz")
    a.li(6, 1)
    a.label("hz")
    a.lwz(7, MANAGER, 13).lbz(8, 0x24, 30).word(_extsb(8, 8)).cmpwi(8, 0).bge("slot")
    a.li(8, 0)
    a.label("slot")
    a.mulli(8, 8, 0xC).add(7, 7, 8).lbz(7, 0x33D, 7).cmplwi(7, 1).ble("row")
    a.li(7, 1)
    a.label("row")
    a.slwi(6, 6, 1).add(6, 6, 7).mulli(6, 6, 6).add(6, 6, 5).slwi(6, 6, 4).addi(6, 6, RECS).add(11, 12, 6)
    a.stw(11, RECP, 12)
    a.lwz(0, 4, 11).subf(3, 0, 3).stw(3, TSAVE, 12)
    a.lfs(3, 0, 11).lfs(0, ZERO, 2).fcmpo(3, 0).ble("aim")
    a.cmplwi(5, FIRE).bgt("one_shot")
    a.lwz(6, 0x0C, 30).cmplwi(6, FIRE).bgt("one_shot")
    a.lis(12, ha(SHOTB + SH_PTR)).lwz(12, lo(SHOTB + SH_PTR), 12).cmpwi(12, 0).beq("one_shot")
    a.mr(3, 5).lbz(4, 0x25, 30).word(_extsb(4, 4)).mtctr(12).word(0x4E800421)
    _data(a, 12)
    a.lwz(11, RECP, 12).cmpwi(3, 0).bne("aim")
    a.li(0, 0).stb(0, 0x28, 30)
    a.li(3, 3).li(4, 0).bl(LOGFN)
    a.b("wait")
    a.label("one_shot")
    _data(a, 12)
    a.lwz(11, RECP, 12).lwz(5, 0x0C, 30).cmplwi(5, BOO).ble("os_i").li(5, SHELL)
    a.label("os_i")
    a.mr(3, 5).li(4, 0).bl(ORIGFN)
    _data(a, 12)
    a.stfs(1, SX, 12).stfs(2, SZ, 12).lfs(4, PX, 12).lfs(5, PZ, 12)
    a.fsubs(4, 4, 1).fsubs(5, 5, 2).fmuls(4, 4, 4).fmadds(1, 5, 5, 4)
    a.bl(SQRT_FN)
    _data(a, 12)
    a.lwz(11, RECP, 12).lfs(3, 0, 11).fdivs(1, 1, 3)
    a.word(FCTIWZ_F1).word(d_form(54, 1, 12, SCR)).lwz(0, SCR + 4, 12)
    a.lwz(3, TSAVE, 12).subf(3, 0, 3).stw(3, TSAVE, 12)
    a.label("aim")
    a.lwz(0, 12, 11)
    a.cmpwi(0, KIND_EDGE).beq("edge")
    a.cmpwi(0, KIND_BANANA).beq("banana")
    a.cmpwi(0, KIND_STAY).beq("stay")
    a.b("timer")
    a.label("edge")
    a.lwz(0, 0x0C, 30).cmpwi(0, BOMB).beq("bomb")

    a.lfs(1, AIMX, 12).lfs(2, AIMZ, 12).lfs(3, 0x10, 30).lfs(4, 0x14, 30)
    a.fsubs(3, 3, 1).fsubs(4, 4, 2).stfs(3, UX, 12).stfs(4, UZ, 12)
    a.fmuls(1, 3, 3).fmadds(1, 4, 4, 1)
    a.lfs(0, EPS, 2).fcmpo(1, 0).bgt("unit")
    a.lfs(0, ONE, 2).stfs(0, UX, 12).lfs(0, ZERO, 2).stfs(0, UZ, 12).lfs(1, ONE, 2)
    a.label("unit")
    a.bl(SQRT_FN)
    _data(a, 12)
    a.lwz(11, RECP, 12).lfs(5, 8, 11).fdivs(5, 5, 1)
    a.lfs(3, UX, 12).lfs(4, UZ, 12).lfs(1, AIMX, 12).lfs(2, AIMZ, 12)
    a.fmadds(1, 3, 5, 1).fmadds(2, 4, 5, 2).stfs(1, AIMX, 12).stfs(2, AIMZ, 12)
    a.lwz(0, 0x0C, 30).cmpwi(0, POW).bne("timer")
    a.lbz(3, 0x25, 30).word(_extsb(3, 3)).bl(POWFN)
    _data(a, 12)
    a.lwz(11, RECP, 12)
    a.b("timer")
    a.label("banana")
    _data(a, 12)
    a.lwz(11, RECP, 12).lfs(5, 8, 11).lfs(3, K12X, 12).lfs(4, K12Z, 12)
    a.lfs(1, PX, 12).fnmsubs(1, 5, 3, 1).stfs(1, AIMX, 12)
    a.lfs(2, PZ, 12).fnmsubs(2, 5, 4, 2).stfs(2, AIMZ, 12)
    a.lwz(3, TSAVE, 12).addi(3, 3, -BANANA_EARLY).stw(3, TSAVE, 12)
    a.b("timer")
    a.label("stay")
    a.lfs(1, 0x10, 30).stfs(1, AIMX, 12).lfs(2, 0x14, 30).stfs(2, AIMZ, 12)
    a.b("timer")
    a.label("bomb")
    a.bl("bclr")
    _data(a, 12)
    a.lwz(11, RECP, 12)
    a.b("timer")
    bomb_clear(a)
    a.label("timer")





    a.lwz(3, TSAVE, 12).cmpwi(3, 0).bge("tm_ok")


    a.lwz(0, 0x0C, 30).cmplwi(0, BANANA).bne("tm_nb")
    a.li(0, 0).stb(0, 0x28, 30)
    a.li(3, 3).li(4, 0).bl(LOGFN)
    a.b("wait")
    a.label("tm_nb")
    a.lbz(0, LATEFIX, 12).cmpwi(0, 0).bne("tm_ok")
    a.lbz(0, DPATH, 12).cmpwi(0, 1).beq("tm_ok")
    a.lwz(0, 0x0C, 30).cmpwi(0, BOO).beq("tm_ok")
    a.lwz(11, RECP, 12).lfs(3, 0, 11).lfs(0, ZERO, 2).fcmpo(3, 0).bgt("tm_ok")
    a.li(0, 1).stb(0, LATEFIX, 12)
    a.lfs(1, LCX, 12).stfs(1, UX, 12).lfs(1, LCZ, 12).stfs(1, UZ, 12)
    a.lwz(6, 4, 11).lwz(0, 12, 11).cmpwi(0, KIND_BANANA).bne("tm_b")
    a.addi(6, 6, BANANA_EARLY)
    a.label("tm_b")
    a.lwz(3, JPTR, 12).b("ch_t0")
    a.label("tm_ok")
    a.lfs(1, AIMX, 12).stfs(1, C0X, 12).lfs(1, AIMZ, 12).stfs(1, C0Z, 12)




    a.lwz(3, TSAVE, 12).cmpwi(3, 0).bge("set")
    a.li(3, 0)
    a.label("set")
    a.stw(3, 0x18, 30).li(0, 1).stw(0, ACTIVE, 12)
    a.lwz(5, MANAGER, 13).lbz(6, 0x24, 30).mulli(6, 6, 0xC).add(5, 5, 6)
    a.li(0, 0).stb(0, 0x3A8, 5)

    a.lfs(1, AIMX, 12).lfs(2, AIMZ, 12).lfs(3, 0x10, 30).lfs(4, 0x14, 30)
    a.fsubs(1, 3, 1).fsubs(2, 4, 2).fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.bl(SQRT_FN)
    _data(a, 12)
    a.lfs(0, KGATE, 12).fsubs(1, 1, 0)
    a.lwz(3, 0x18, 30).cmpwi(3, 1).bge("t1")
    a.li(3, 1)
    a.label("t1")
    a.word(XORIS_R3).lis(0, 0x4330).stw(0, SCR, 12).stw(3, SCR + 4, 12)
    a.word(d_form(50, 2, 12, SCR)).word(d_form(50, 3, 12, MAGIC)).word(FSUB_F2_F2_F3).fdivs(1, 1, 2)
    a.lbz(0, HZ, 13).cmplwi(0, 1).ble("hz2")
    a.li(0, 1)
    a.label("hz2")
    a.slwi(0, 0, 2).add(4, 12, 0)
    sp4, sp5, sp6 = one_level.pair(sys.modules[__name__], "SPEED6")
    if sp4 != sp6 or sp5 != sp6:
        one_level.pick(a, 5, L4K - SPEEDS, L5K - SPEEDS, 0).add(4, 4, 5)
    a.lfs(0, SPEEDS, 4).fcmpo(1, 0).bge("fast")
    a.fmr(1, 0)
    a.label("fast")
    a.stfs(1, CSPEED, 12)
    a.lbz(4, DPATH, 12).lbz(5, HRWHY, 12).slwi(5, 5, 4).add(4, 4, 5).li(3, 1).bl(LOGFN)
    a.b(AIM_CALL)
    a.label("trig")
    a.li(3, 4).li(4, 0).bl(LOGFN)
    a.b(TRIGGERED)
    a.label("wait")
    a.li(0, 0x7FFF).stw(0, 0x18, 30)
    a.b(AIM_CALL)
    a.label("stock")
    a.word(DECIDE_ORIG)
    a.b(DECIDE + 4)
    return a


def _shot_i2f(a, src, fd):
    """fd = (double) (s32) r`src` via DATA's SCR / MAGIC at r30. Uses r0, r11, f0."""
    a.word(d_form(27, src, 0, 0x8000))
    a.lis(11, 0x4330).stw(11, SCR, 30).stw(0, SCR + 4, 30)
    a.word(d_form(50, fd, 30, SCR)).word(d_form(50, 0, 30, MAGIC))
    a.word((63 << 26) | (fd << 21) | (fd << 16) | (0 << 11) | (20 << 1))


def shots_sub(at):
    """SHOTS(r3 = item: Shell / Fireball, r4 = the target index or -1) -> r3 = 1 (PX / PZ / AIMX / AIMZ / SX / SZ /
    TSAVE / TARR set to the pick) or 0 (a Fireball with no clear shot). Docstring SHOT LIST. A normal call: clobbers
    r0, r3..r12, f0..f13, cr; keeps r14..r31 (r25..r31 saved), f14..f31."""
    a = Asm(at)
    a.stwu(1, -0x40, 1).mflr(0).stw(0, 0x44, 1)
    for k, r in enumerate(range(25, 32)):
        a.stw(r, 0x20 + 4 * k, 1)
    a.load_addr(31, SHOTB)
    _data(a, 30)
    a.mr(29, 3).mr(28, 4)
    a.li(0, 0).stw(0, BOK, 31)
    a.li(26, len(OFF))
    a.lbz(0, DPATH, 30).cmpwi(0, 1).beq("s_one")
    a.lwz(0, JPTR, 30).cmpwi(0, 0).bne("s_go")
    a.label("s_one")
    a.li(26, 1)
    a.label("s_go")
    a.li(27, 0)
    a.label("s_k")
    a.slwi(5, 27, 2).addi(5, 5, K_OFF).lwzx(5, 31, 5).lwz(0, TARR, 30).add(25, 0, 5)
    a.cmpwi(25, 1).blt("s_next")
    a.cmpwi(27, 0).bne("s_lead")
    a.lfs(1, PX, 30).lfs(2, PZ, 30).b("s_p")
    a.label("s_lead")
    a.lwz(3, JPTR, 30)
    a.lfs(1, JX, 30).lfs(2, JZ, 30)
    a.lbz(7, 0x251, 3).addi(7, 7, -1).cmplwi(7, 8).ble("s_p")
    a.lfs(3, 0xE4, 3).lfs(4, 0xEC, 3).fcmpo(4, 3).ble("s_sp").fmr(3, 4)
    a.label("s_sp")
    _shot_i2f(a, 25, 4)
    a.fmuls(3, 3, 4)
    a.lfs(5, LCX, 30).fsubs(5, 5, 1).lfs(6, LCZ, 30).fsubs(6, 6, 2)
    a.fmuls(7, 5, 5).fmadds(7, 6, 6, 7).fmuls(8, 3, 3).fcmpo(8, 7).ble("s_in")
    a.lfs(1, LCX, 30).lfs(2, LCZ, 30).b("s_p")
    a.label("s_in")
    a.stfs(3, WR, 31).stfs(1, CPX, 31).stfs(2, CPZ, 31)
    a.fmr(1, 7).lfs(0, EPS, 2).fcmpo(1, 0).bgt("s_sq2")
    a.lfs(1, CPX, 31).lfs(2, CPZ, 31).b("s_p")
    a.label("s_sq2")
    a.bl(SQRT_FN)
    a.lfs(3, WR, 31).fdivs(3, 3, 1)
    a.lfs(5, LCX, 30).lfs(1, CPX, 31).fsubs(5, 5, 1).fmadds(1, 5, 3, 1)
    a.lfs(6, LCZ, 30).lfs(2, CPZ, 31).fsubs(6, 6, 2).fmadds(2, 6, 3, 2)
    a.label("s_p")
    a.stfs(1, CPX, 31).stfs(2, CPZ, 31)
    a.mr(3, 29).li(4, 0).bl(ORIGFN)
    a.stfs(1, CSX, 31).stfs(2, CSZ, 31)
    a.lfs(3, CPX, 31).fsubs(3, 3, 1).lfs(4, CPZ, 31).fsubs(4, 4, 2)
    a.fmuls(1, 3, 3).fmadds(1, 4, 4, 1)
    a.lfs(0, EPS, 2).fcmpo(1, 0).bgt("s_sq").lfs(1, ZERO, 2).b("s_l")
    a.label("s_sq")
    a.bl(SQRT_FN)
    a.label("s_l")
    a.stfs(1, CL, 31)

    a.lfs(0, CPX, 31).stfs(0, CAX, 31).lfs(0, CPZ, 31).stfs(0, CAZ, 31)
    a.lfs(0, EPS, 2).fcmpo(1, 0).ble("x_done")
    a.li(0, 0).stw(0, EI, 31)
    a.label("x_k")
    a.lwz(5, EI, 31).slwi(5, 5, 2).add(5, 5, 31).lfs(5, K_EXT, 5)
    a.lfs(1, CPX, 31).lfs(3, CSX, 31).fsubs(3, 1, 3).lfs(2, CPZ, 31).lfs(4, CSZ, 31).fsubs(4, 2, 4)
    a.lfs(0, CL, 31).fdivs(3, 3, 0).fdivs(4, 4, 0)
    a.fmadds(1, 3, 5, 1).fmadds(2, 4, 5, 2).stfs(1, XCX, 31).stfs(2, XCZ, 31)
    a.fneg(2, 2).lwz(3, MANAGER, 13).bl(ONSCREEN_FN)
    a.clrlwi(3, 3, 24).cmpwi(3, 0).beq("x_n")
    a.lfs(1, XCX, 31).lfs(2, XCZ, 31).mr(3, 29).li(4, 0).bl(ORIGFN)
    a.stfs(1, XSX, 31).stfs(2, XSZ, 31)
    a.lfs(3, CPX, 31).fsubs(3, 3, 1).lfs(4, CPZ, 31).fsubs(4, 4, 2).fmuls(1, 3, 3).fmadds(1, 4, 4, 1)
    a.lfs(0, EPS, 2).fcmpo(1, 0).ble("x_n")
    a.bl(SQRT_FN)
    a.lfs(0, CL, 31).fcmpo(1, 0).bge("x_n")
    a.stfs(1, XL, 31)
    a.lfs(5, XSX, 31).lfs(6, XSZ, 31)
    a.lfs(3, CPX, 31).fsubs(3, 3, 5).lfs(4, CPZ, 31).fsubs(4, 4, 6)
    a.lfs(7, XCX, 31).fsubs(7, 7, 5).lfs(8, XCZ, 31).fsubs(8, 8, 6)
    a.fmuls(9, 3, 8).fnmsubs(9, 4, 7, 9).fmuls(9, 9, 9)
    a.fmuls(10, 7, 7).fmadds(10, 8, 8, 10)
    a.lfs(0, K_MISS, 31).fmuls(0, 0, 0).fmuls(0, 0, 10).fcmpo(9, 0).bgt("x_n")
    for src, dst in ((XSX, CSX), (XSZ, CSZ), (XL, CL), (XCX, CAX), (XCZ, CAZ)):
        a.lwz(0, src, 31).stw(0, dst, 31)
    a.label("x_n")
    a.lwz(5, EI, 31).addi(5, 5, 1).stw(5, EI, 31).cmpwi(5, len(EXT)).blt("x_k")
    a.label("x_done")
    a.lfs(1, CL, 31)
    a.lwz(11, RECP, 30).lfs(3, 0, 11).fdivs(1, 1, 3)
    a.word(FCTIWZ_F1).word(d_form(54, 1, 31, SSCR)).lwz(3, SSCR + 4, 31).stw(3, CTRAV, 31)
    a.lwz(0, 4, 11).subf(0, 0, 25).subf(0, 3, 0).stw(0, CREL, 31).stw(25, CT, 31)
    a.bl("s_eval").stw(3, CHITS, 31)
    a.cmplwi(29, FIRE).bne("s_cmp")
    a.cmpwi(3, 0).bne("s_next")
    a.label("s_cmp")
    a.lwz(0, BOK, 31).cmpwi(0, 0).beq("s_take")
    a.lwz(3, CREL, 31).lwz(4, BREL, 31)
    a.li(5, 0).cmpwi(3, 0).blt("s_v1").li(5, 1)
    a.label("s_v1")
    a.li(6, 0).cmpwi(4, 0).blt("s_v2").li(6, 1)
    a.label("s_v2")
    a.cmpw(5, 6).bgt("s_take").blt("s_next")
    a.cmpwi(5, 0).bne("s_both")
    a.cmpw(3, 4).bgt("s_take").b("s_next")
    a.label("s_both")
    a.cmplwi(29, FIRE).beq("s_fast")
    a.lwz(3, CHITS, 31).lwz(4, BHITS, 31).cmpw(3, 4).bgt("s_take").blt("s_next")
    a.label("s_fast")
    a.lwz(3, CTRAV, 31).lwz(4, BTRAV, 31).cmpw(3, 4).bge("s_next")
    a.label("s_take")
    for c_, b_ in ((CPX, BPX), (CPZ, BPZ), (CSX, BSX), (CSZ, BSZ), (CREL, BREL), (CTRAV, BTRAV), (CHITS, BHITS),
                   (CT, BT), (CAX, BAX), (CAZ, BAZ)):
        a.lwz(0, c_, 31).stw(0, b_, 31)
    a.li(0, 1).stw(0, BOK, 31)
    a.label("s_next")
    a.addi(27, 27, 1).cmpw(27, 26).blt("s_k")
    a.li(3, 0).lwz(0, BOK, 31).cmpwi(0, 0).beq("s_out")
    a.lfs(1, BPX, 31).stfs(1, PX, 30).lfs(2, BPZ, 31).stfs(2, PZ, 30)
    a.lfs(1, BAX, 31).stfs(1, AIMX, 30).lfs(2, BAZ, 31).stfs(2, AIMZ, 30)
    a.lfs(1, BSX, 31).stfs(1, SX, 30).lfs(1, BSZ, 31).stfs(1, SZ, 30)
    a.lwz(0, BREL, 31).stw(0, TSAVE, 30).lwz(0, BT, 31).stw(0, TARR, 30)
    a.li(3, 1)
    a.label("s_out")
    for k, r in enumerate(range(25, 32)):
        a.lwz(r, 0x20 + 4 * k, 1)
    a.lwz(0, 0x44, 1).mtlr(0).addi(1, 1, 0x40).blr()


    a.label("s_eval")
    a.li(3, 0)
    a.lfs(1, CL, 31).lfs(0, EPS, 2).fcmpo(1, 0).bgt("e_ok").blr()
    a.label("e_ok")
    a.mr(12, 3)
    a.lwz(11, RECP, 30).lfs(13, 0, 11)
    a.lwz(0, CREL, 31).cmpwi(0, 0).bge("e_rel").li(0, 0)
    a.label("e_rel")
    _shot_i2f(a, 0, 12)
    a.lfs(1, CPX, 31).lfs(2, CSX, 31).fsubs(1, 1, 2).lfs(3, CL, 31).fdivs(1, 1, 3)
    a.lfs(2, CPZ, 31).lfs(4, CSZ, 31).fsubs(2, 2, 4).fdivs(2, 2, 3)
    a.stfs(1, WX, 31).stfs(2, WZ, 31)
    a.li(10, 0)
    a.label("e_i")
    a.cmpw(10, 28).beq("e_inext")
    a.mr(5, 10)
    _fielder_ptr(a, "e_fp")
    a.cmpwi(3, 0).beq("e_inext")
    a.mr(9, 3)

    a.lfs(5, 0x94, 9).lfs(6, 0x98, 9).lfs(0, 0xE4, 9).fmuls(5, 5, 0).fmuls(6, 6, 0)
    a.lfs(0, CL, 31).fdivs(0, 0, 13).fadds(0, 0, 12)
    a.lfs(7, 4, 9).fmadds(7, 5, 0, 7).lfs(8, CPX, 31).fsubs(7, 7, 8)
    a.lfs(8, 0xC, 9).fmadds(8, 6, 0, 8).lfs(9, CPZ, 31).fsubs(8, 8, 9)
    a.fmuls(7, 7, 7).fmadds(7, 8, 8, 7).lfs(8, K_CATCH, 31).fmuls(8, 8, 8).fcmpo(7, 8).blt("e_inext")
    a.li(8, 0)
    a.label("e_p")
    a.lfs(1, WX, 31).lfs(2, WZ, 31).lfs(11, K_RANGE, 31)
    a.cmpwi(8, 0).bne("e_side")
    a.cmplwi(29, FIRE).bne("e_path")
    a.lfs(11, CL, 31).b("e_path")
    a.label("e_side")
    a.lfs(3, K_COS, 31).lfs(4, K_SIN, 31)
    a.cmpwi(8, 2).bne("e_rot").fneg(4, 4)
    a.label("e_rot")
    a.fmuls(9, 1, 3).fnmsubs(9, 2, 4, 9).fmuls(10, 1, 4).fmadds(10, 2, 3, 10)
    a.fmr(1, 9).fmr(2, 10)
    a.label("e_path")
    a.lfs(3, 4, 9).lfs(4, CSX, 31).fsubs(3, 3, 4).lfs(4, 0xC, 9).lfs(0, CSZ, 31).fsubs(4, 4, 0)
    a.fmuls(0, 3, 1).fmadds(0, 4, 2, 0)
    a.fdivs(0, 0, 13).fadds(0, 0, 12)
    a.fmadds(3, 5, 0, 3).fmadds(4, 6, 0, 4)
    a.fmuls(0, 3, 1).fmadds(0, 4, 2, 0)
    a.lfs(9, ZERO, 2).fcmpo(0, 9).ble("e_pnext").fcmpo(0, 11).bge("e_pnext")
    a.fmuls(0, 3, 2).fnmsubs(0, 4, 1, 0)
    a.word((63 << 26) | (0 << 21) | (0 << 11) | (264 << 1))
    a.lfs(9, K_HIT, 31).fcmpo(0, 9).bge("e_pnext")
    a.addi(12, 12, 1).b("e_inext")
    a.label("e_pnext")
    a.addi(8, 8, 1)
    a.cmplwi(29, FIRE).bne("e_inext")
    a.cmpwi(8, 3).blt("e_p")
    a.label("e_inext")
    a.addi(10, 10, 1).cmpwi(10, 9).blt("e_i")
    a.mr(3, 12).blr()
    return a


def shot_block():
    blob = bytearray(SHOTB_SIZE)
    struct.pack_into(">5f", blob, K_HIT, HIT_R, RANGE, CATCH_R, math.cos(math.radians(FIRE_SPREAD)),
                     math.sin(math.radians(FIRE_SPREAD)))
    struct.pack_into(f">{len(OFF)}i", blob, K_OFF, *OFF)
    struct.pack_into(f">{len(EXT)}f", blob, K_EXT, *EXT)
    struct.pack_into(">f", blob, K_MISS, EXT_MISS)
    return bytes(blob)


def pow_sub(at):
    """POWFN(r3 = the target index or -1): AIMX / AIMZ = the POW's burst centre with the most other fielders in reach
    (docstring THE MOST FIELDERS IN A POW), starting from the edge C already in AIMX / AIMZ. A normal call: clobbers
    r0, r3..r12, f0..f13, cr; keeps r14..r31 (r26..r31 saved), f14..f31."""
    a = Asm(at)
    a.stwu(1, -0x40, 1).mflr(0).stw(0, 0x44, 1)
    for k, r in enumerate(range(26, 32)):
        a.stw(r, 0x20 + 4 * k, 1)
    a.load_addr(31, POWB)
    _data(a, 30)
    a.mr(29, 3)
    a.lwz(11, RECP, 30).lfs(0, 8, 11).stfs(0, PD, 31)
    a.lwz(3, TARR, 30).cmpwi(3, 0).bge("p_t").li(3, 0)
    a.label("p_t")
    a.mr(12, 30)
    _i2f(a, 3, 1)
    a.stfs(1, PT, 31)
    a.li(28, 0)
    a.label("j_loop")
    a.cmpw(28, 29).beq("j_skip")
    a.mr(5, 28)
    _fielder_ptr(a, "pj_fp")
    a.cmpwi(3, 0).beq("j_skip")
    a.mr(27, 3)
    a.lbz(4, 0x21C, 27).cmplwi(4, ROUTE_N - 1).bgt("j_head")
    a.load_addr(5, ROUTE_T).mulli(4, 4, ROUTE_SZ).add(5, 5, 4)
    a.lwz(6, PLAYSTATE, 13).cmpwi(6, 0).beq("j_head")
    a.lha(6, 2, 6).lwz(7, 8, 5).subf(6, 7, 6).cmplwi(6, 1).bgt("j_head")
    a.lfs(1, 0, 5).lfs(3, 4, 27).fsubs(1, 1, 3).stfs(1, PTX, 31)
    a.lfs(2, 4, 5).lfs(3, 0xC, 27).fsubs(2, 2, 3).stfs(2, PTZ, 31)
    a.fmuls(1, 1, 1).fmadds(1, 2, 2, 1).lfs(0, EPS, 2).fcmpo(1, 0).ble("j_at")
    a.bl(SQRT_FN)
    a.lfs(3, 0xE4, 27).lfs(4, 0xEC, 27).fcmpo(4, 3).ble("j_s").fmr(3, 4)
    a.label("j_s")
    a.lfs(0, PT, 31).fmuls(3, 3, 0).fcmpo(3, 1).bge("j_arr")
    a.fdivs(3, 3, 1)
    a.lfs(1, PTX, 31).lfs(4, 4, 27).fmadds(1, 1, 3, 4).lfs(2, PTZ, 31).lfs(5, 0xC, 27).fmadds(2, 2, 3, 5)
    a.b("j_store")
    a.label("j_arr")
    a.lfs(1, PTX, 31).lfs(3, 4, 27).fadds(1, 1, 3).lfs(2, PTZ, 31).lfs(3, 0xC, 27).fadds(2, 2, 3)
    a.b("j_store")
    a.label("j_at")
    a.lfs(1, 4, 27).lfs(2, 0xC, 27).b("j_store")
    a.label("j_head")
    a.lfs(0, 0xE4, 27).lfs(3, PT, 31).fmuls(0, 0, 3)
    a.lfs(1, 0x94, 27).lfs(4, 4, 27).fmadds(1, 1, 0, 4).lfs(2, 0x98, 27).lfs(5, 0xC, 27).fmadds(2, 2, 0, 5)
    a.label("j_store")
    a.slwi(6, 28, 2).add(6, 6, 31).stfs(1, PQX, 6).stfs(2, PQZ, 6)
    a.lfs(0, PD, 31).lfs(3, 0x1C, 27).fadds(0, 0, 3).stfs(0, PRJ, 6).b("j_next")
    a.label("j_skip")
    a.slwi(6, 28, 2).add(6, 6, 31).lfs(0, ONE, 2).fneg(0, 0).stfs(0, PRJ, 6)
    a.label("j_next")
    a.addi(28, 28, 1).cmpwi(28, 9).blt("j_loop")

    a.lfs(1, AIMX, 30).lfs(2, AIMZ, 30).stfs(1, PBX, 31).stfs(2, PBZ, 31).bl("p_cnt").mr(26, 3)
    a.lfs(1, PX, 30).lfs(2, PZ, 30).bl("p_cnt").cmpw(3, 26).ble("c_j")
    a.mr(26, 3).lfs(1, PX, 30).stfs(1, PBX, 31).lfs(2, PZ, 30).stfs(2, PBZ, 31)
    a.label("c_j")
    a.li(28, 0)
    a.label("cj_loop")
    a.slwi(6, 28, 2).add(6, 6, 31).lfs(0, PRJ, 6).lfs(3, ZERO, 2).fcmpo(0, 3).blt("cj_next")
    a.stfs(0, PTR, 31)
    a.lfs(1, PQX, 6).lfs(3, PX, 30).fsubs(1, 1, 3).stfs(1, PTX, 31)
    a.lfs(2, PQZ, 6).lfs(3, PZ, 30).fsubs(2, 2, 3).stfs(2, PTZ, 31)
    a.fmuls(1, 1, 1).fmadds(1, 2, 2, 1).lfs(0, EPS, 2).fcmpo(1, 0).ble("cj_next")
    a.bl(SQRT_FN)
    a.lfs(0, PD, 31).lfs(3, PTR, 31).fadds(4, 0, 3).fcmpo(1, 4).bgt("cj_next")
    a.fsubs(5, 1, 3).lfs(6, ZERO, 2).fcmpo(5, 6).bge("cj_lo").fmr(5, 6)
    a.label("cj_lo")
    a.fmr(7, 1).fcmpo(0, 7).bge("cj_hi").fmr(7, 0)
    a.label("cj_hi")
    a.fadds(5, 5, 7).lfs(6, PHALF, 31).fmuls(5, 5, 6).fdivs(5, 5, 1)
    a.lfs(1, PTX, 31).lfs(3, PX, 30).fmadds(1, 1, 5, 3).lfs(2, PTZ, 31).lfs(3, PZ, 30).fmadds(2, 2, 5, 3)
    a.stfs(1, PCX, 31).stfs(2, PCZ, 31)
    a.bl("p_cnt").cmpw(3, 26).ble("cj_next")
    a.mr(26, 3).lfs(1, PCX, 31).stfs(1, PBX, 31).lfs(2, PCZ, 31).stfs(2, PBZ, 31)
    a.label("cj_next")
    a.addi(28, 28, 1).cmpwi(28, 9).blt("cj_loop")
    a.lfs(1, PBX, 31).stfs(1, AIMX, 30).lfs(2, PBZ, 31).stfs(2, AIMZ, 30).stw(26, PBEST, 31)
    for k, r in enumerate(range(26, 32)):
        a.lwz(r, 0x20 + 4 * k, 1)
    a.lwz(0, 0x44, 1).mtlr(0).addi(1, 1, 0x40).blr()

    a.label("p_cnt")
    a.li(3, 0).li(4, 0).mr(5, 31)
    a.label("pc_l")
    a.lfs(0, PRJ, 5).lfs(3, ZERO, 2).fcmpo(0, 3).blt("pc_n")
    a.lfs(3, PQX, 5).fsubs(3, 1, 3).lfs(4, PQZ, 5).fsubs(4, 2, 4).fmuls(3, 3, 3).fmadds(3, 4, 4, 3)
    a.fmuls(0, 0, 0).fcmpo(3, 0).bgt("pc_n")
    a.addi(3, 3, 1)
    a.label("pc_n")
    a.addi(5, 5, 4).addi(4, 4, 1).cmpwi(4, 9).blt("pc_l")
    a.blr()
    return a


def pow_best(P, D, edge, T, fielders, target, route=None):
    """The spec for POWFN: fielders {j: (x, z, radius, heading x, heading z, E4, EC)}; route {j: (x, z)} fresh move
    targets. -> (C, count). f32 as the stub."""
    F = f32
    q = {}
    t = F(max(T, 0))
    for j, (x, z, r, hx, hz, e4, ec) in fielders.items():
        if j == target:
            continue
        if route and j in route:
            tx, tz = F(route[j][0] - x), F(route[j][1] - z)
            d2 = F(F(tx * tx) + F(tz * tz))
            if d2 <= 1e-4:
                Q = (x, z)
            else:
                d = F(math.sqrt(d2))
                s = F(F(max(e4, ec)) * t)
                Q = (F(x + tx), F(z + tz)) if s >= d else (F(tx * F(s / d) + x), F(tz * F(s / d) + z))
        else:
            m = F(e4 * t)
            Q = (F(hx * m + x), F(hz * m + z))
        q[j] = (Q, F(D + r))

    def count(c):
        return sum(1 for Q, R in q.values() if F(F((c[0] - Q[0]) ** 2) + F((c[1] - Q[1]) ** 2)) <= F(R * R))
    best, n = edge, count(edge)
    if count(P) > n:
        best, n = P, count(P)
    for j in range(9):
        if j not in q:
            continue
        Q, R = q[j]
        lx, lz = F(Q[0] - P[0]), F(Q[1] - P[1])
        l2 = F(F(lx * lx) + F(lz * lz))
        if l2 <= 1e-4:
            continue
        L = F(math.sqrt(l2))
        if L > F(D + R):
            continue
        a = F(F(F(max(F(L - R), 0.0)) + F(min(D, L))) * 0.5)
        k = F(a / L)
        c = (F(lx * k + P[0]), F(lz * k + P[1]))
        if count(c) > n:
            best, n = c, count(c)
    return best, n


def aim_stub(at):
    a = Asm(at)
    _gate(a, 4, "stock")
    a.lbz(4, 0x26, 30).cmpwi(4, HARDEST).bne("stock")
    a.lbz(4, 0x28, 30).cmpwi(4, 0).beq("stock")
    _data(a, 4)
    a.lwz(0, ACTIVE, 4).cmpwi(0, 0).beq("stock")
    a.li(0, 0).stb(0, HELD, 4)



    a.bl("goodq").cmpwi(3, 1).beq("on")


    _data(a, 4)
    a.lwz(11, RECP, 4).lfs(1, 0, 11).lfs(0, ZERO, 2).fcmpo(1, 0).ble("sh_no")
    a.li(0, 1).stw(0, SI, 4)
    a.label("sh_loop")
    _data(a, 4)
    a.lwz(6, SI, 4)
    _i2f4(a, 6, 5)
    a.lfs(0, KLINE, 4).fmuls(5, 5, 0).stfs(5, SF, 4)
    a.lfs(1, PX, 4).lfs(2, PZ, 4).lfs(3, SX, 4).lfs(4, SZ, 4)
    a.fsubs(6, 3, 1).fmadds(1, 6, 5, 1).fsubs(7, 4, 2).fmadds(2, 7, 5, 2)
    a.lwz(3, 0x0C, 30).cmplwi(3, BOO).ble("sh_it")
    a.li(3, SHELL)
    a.label("sh_it")
    a.li(4, 0).bl(ORIGFN)
    _data(a, 4)
    a.lfs(5, SF, 4).lfs(3, PX, 4).lfs(4, PZ, 4)
    a.fsubs(6, 1, 3).fmadds(1, 6, 5, 3).fsubs(7, 2, 4).fmadds(2, 7, 5, 4).stfs(1, AIMX, 4).stfs(2, AIMZ, 4)
    a.bl("goodq").cmpwi(3, 1).beq("on")
    _data(a, 4)
    a.lwz(6, SI, 4).addi(6, 6, 1).stw(6, SI, 4).cmpwi(6, LINE_TRIES).ble("sh_loop")
    a.label("sh_no")


    _data(a, 4)
    a.lwz(11, RECP, 4).lwz(0, 12, 11).cmpwi(0, KIND_BANANA).bne("ring0")
    a.li(0, 0).stw(0, SI, 4)
    a.label("bn5")
    _data(a, 4)
    a.lwz(6, SI, 4).slwi(6, 6, 3).add(6, 4, 6).lfs(6, BAN5, 6).lfs(7, BAN5 + 4, 6)
    a.lwz(11, RECP, 4).lfs(5, 8, 11)
    a.lfs(3, K12X, 4).lfs(1, C0X, 4).fmadds(1, 5, 3, 1).lfs(3, K12Z, 4).lfs(2, C0Z, 4).fmadds(2, 5, 3, 2)
    a.fnmsubs(1, 5, 6, 1).fnmsubs(2, 5, 7, 2).stfs(1, AIMX, 4).stfs(2, AIMZ, 4)
    a.bl("goodq").cmpwi(3, 1).beq("on")
    _data(a, 4)
    a.lwz(6, SI, 4).addi(6, 6, 1).stw(6, SI, 4).cmpwi(6, 5).blt("bn5")
    a.b("hold")

    a.label("ring0")
    _data(a, 4)
    a.li(0, 0).stw(0, SK, 4)
    a.label("ring")
    a.li(0, 0).stw(0, SI, 4)
    a.label("dir")
    _data(a, 4)
    a.lwz(5, SK, 4).slwi(5, 5, 2).add(5, 4, 5).lfs(5, RAD, 5)
    a.lwz(6, SI, 4).slwi(6, 6, 3).add(6, 4, 6).lfs(6, ROT, 6).lfs(7, ROT + 4, 6)
    a.lfs(1, C0X, 4).lfs(3, PX, 4).fsubs(1, 1, 3).lfs(2, C0Z, 4).lfs(4, PZ, 4).fsubs(2, 2, 4)
    a.lwz(0, 0x0C, 30).cmpwi(0, BOMB).bne("r_nb")
    a.lwz(11, RECP, 4).lfs(1, 8, 11).lfs(2, ZERO, 2)
    a.label("r_nb")
    a.fmuls(8, 1, 6).fnmsubs(8, 2, 7, 8)
    a.fmuls(9, 1, 7).fmadds(9, 2, 6, 9)
    a.fmadds(1, 8, 5, 3).fmadds(2, 9, 5, 4).stfs(1, AIMX, 4).stfs(2, AIMZ, 4)
    a.bl("goodq").cmpwi(3, 1).beq("on")
    _data(a, 4)
    a.lwz(6, SI, 4).addi(6, 6, 1).stw(6, SI, 4).cmpwi(6, len(TURNS)).blt("dir")
    a.lwz(5, SK, 4).addi(5, 5, 1).stw(5, SK, 4).cmpwi(5, len(RINGS)).blt("ring")
    a.label("in_no")
    _data(a, 4)
    a.lfs(1, PX, 4).stfs(1, AIMX, 4).lfs(1, PZ, 4).stfs(1, AIMZ, 4)
    a.bl("goodq").cmpwi(3, 1).beq("on")
    a.cmpwi(3, 2).bne("n_scr")
    a.li(31, 0).b("on")
    a.label("hold")
    _data(a, 4)
    a.lfs(1, C0X, 4).stfs(1, AIMX, 4).lfs(1, C0Z, 4).stfs(1, AIMZ, 4)
    a.label("n_scr")
    _data(a, 4)
    a.li(31, 0)
    a.lfs(1, AIMX, 4).lfs(3, 0x10, 30).fsubs(1, 1, 3).stfs(1, UX, 4)
    a.lfs(2, AIMZ, 4).lfs(3, 0x14, 30).fsubs(2, 2, 3).stfs(2, UZ, 4)
    a.fmuls(1, 1, 1).fmadds(1, 2, 2, 1).lfs(0, EPS, 2).fcmpo(1, 0).ble("on")
    a.bl(SQRT_FN)
    _data(a, 4)
    a.lfs(0, CSPEED, 4).fcmpo(1, 0).bge("n_d")
    a.fmr(0, 1)
    a.label("n_d")
    a.fdivs(5, 0, 1).lfs(3, UX, 4).lfs(4, UZ, 4).lfs(1, 0x10, 30).lfs(2, 0x14, 30)
    a.fmadds(1, 3, 5, 1).fmadds(2, 4, 5, 2).fneg(2, 2).bl(ONSTEPFN)
    a.cmpwi(3, 0).bne("on")
    a.lfs(1, 0x10, 30).lfs(2, 0x14, 30).fneg(2, 2).bl(ONSTEPFN)
    a.cmpwi(3, 0).beq("back_in")
    _data(a, 4)
    a.li(31, 0).li(0, 1).stb(0, HELD, 4)
    a.lfs(2, 0x10, 30).lfs(3, 0x14, 30).b("tgt")
    a.label("back_in")
    a.lwz(3, BALL, 13).lfs(2, PATH, 3).lfs(3, PATH + 8, 3)
    _data(a, 4)
    a.li(31, 0).b("tgt")
    a.label("on")
    _data(a, 4)
    a.lfs(2, AIMX, 4).lfs(3, AIMZ, 4)
    a.label("tgt")
    a.lfs(1, 0x10, 30).lfs(0, 0x14, 30)
    a.fsubs(30, 2, 1).fsubs(29, 3, 0)
    a.lfs(1, CSPEED, 4)
    a.fmuls(31, 31, 1)
    a.lbz(5, HELD, 4).lbz(6, QRES, 4).slwi(5, 5, 1).slwi(6, 6, 2).clrlwi(4, 31, 31).add(4, 4, 5).add(4, 4, 6)
    a.li(3, 5).bl(LOGFN)
    if LOG_ENTRIES:
        a.lbz(5, 0x25, 30).cmplwi(5, 8).bgt("nof").li(4, 0).bl(FLOGFN)
        a.label("nof")
    a.b(AIM_BACK)
    a.label("stock")
    a.word(AIM_ORIG)
    a.b(AIM + 4)
    _goodq(a)
    return a


def _goodq(a):
    """Subroutine "goodq": is C (AIMX / AIMZ) a good spot? r3 = 1 good, 0 not throwable (FUN_804570C8), 2 a Bob-omb /
    POW / Banana there would reach one of our runners on the field (+0x176) anywhere on his path: from where he is
    to his to-base (+0x17E) when advancing (+0x183 1), to his from-base (+0x17D) when retreating (3), or where he
    stands. Returns r4 = the data block; uses r0, r3, r5..r7, r11, f0..f13 and the SUBLR slot."""
    a.label("goodq")
    a.mflr(0)
    _data(a, 4)
    a.stw(0, SUBLR, 4)
    a.lfs(1, AIMX, 4).lfs(2, AIMZ, 4).fneg(2, 2).lwz(3, MANAGER, 13).bl(ONSCREEN_FN)
    a.clrlwi(3, 3, 24).cmpwi(3, 0).beq("q0")
    _data(a, 4)
    a.lwz(11, RECP, 4).lwz(0, 12, 11)
    pad4, pad5, pad6 = one_level.pair(sys.modules[__name__], "RUNNER_PAD")
    kb = 4
    if pad4 != pad6 or pad5 != pad6:
        one_level.pick(a, 3, L4K + 8 - KFFE, L5K + 8 - KFFE, 0).add(3, 3, 4)
        kb = 3
    a.cmpwi(0, KIND_EDGE).bne("q_nb")
    a.lfs(5, KFFE, kb).b("q_r")
    a.label("q_nb")
    a.cmpwi(0, KIND_BANANA).bne("q1")
    a.lfs(5, KFFB, kb)
    a.label("q_r")
    a.lfs(0, 8, 11).fadds(5, 5, 0).fmuls(5, 5, 5).stfs(5, KR2, 4)
    a.lwz(3, 0x18, 30).lwz(0, 4, 11).add(3, 3, 0).cmpwi(3, 0).bge("q_k")
    a.li(3, 0)
    a.label("q_k")
    a.word(XORIS_R3).lis(0, 0x4330).stw(0, SCR, 4).stw(3, SCR + 4, 4)
    a.word(d_form(50, 2, 4, SCR)).word(d_form(50, 3, 4, MAGIC)).word(FSUB_F2_F2_F3).stfs(2, KF, 4)
    a.li(0, 0).stw(0, RI, 4)
    a.label("q_loop")
    _data(a, 4)
    a.lwz(5, RI, 4).slwi(6, 5, 2).lis(7, ha(RUNNERS)).addi(7, 7, lo(RUNNERS)).add(7, 7, 6).lwz(3, 0, 7)
    a.cmpwi(3, 0).beq("q_next")
    a.lbz(0, R_ON, 3).cmpwi(0, 0).beq("q_next")


    a.lfs(1, 4, 3).lfs(2, 0xC, 3).fmr(3, 1).fmr(4, 2)
    a.lbz(0, R_DIR, 3).cmpwi(0, 1).beq("q_to")
    a.cmpwi(0, 3).bne("q_seg")
    a.lbz(6, R_FROM, 3).b("q_base")
    a.label("q_to")
    a.lbz(6, R_TO, 3)
    a.label("q_base")
    a.cmplwi(6, 3).ble("q_bi")
    a.li(6, 0)
    a.label("q_bi")
    a.slwi(6, 6, 3).lis(7, ha(BASES)).addi(7, 7, lo(BASES)).add(7, 7, 6).lfs(3, 0, 7).lfs(4, 4, 7)
    a.label("q_seg")
    a.lfs(5, AIMX, 4).lfs(6, AIMZ, 4)
    a.fsubs(7, 3, 1).fsubs(8, 4, 2).fsubs(9, 5, 1).fsubs(10, 6, 2)
    a.fmuls(11, 7, 7).fmadds(11, 8, 8, 11).fmuls(12, 9, 7).fmadds(12, 10, 8, 12)
    a.lfs(0, ZERO, 2).lfs(13, EPS, 2).fcmpo(11, 13).ble("q_t0")
    a.fdivs(13, 12, 11).fcmpo(13, 0).blt("q_t0")
    a.lfs(0, ONE, 2).fcmpo(13, 0).bgt("q_t")
    a.fmr(0, 13).b("q_t")
    a.label("q_t0")
    a.lfs(0, ZERO, 2)
    a.label("q_t")
    a.fmadds(1, 7, 0, 1).fmadds(2, 8, 0, 2)
    a.fsubs(1, 5, 1).fsubs(2, 6, 2).fmuls(1, 1, 1).fmadds(1, 2, 2, 1)
    a.lfs(0, KR2, 4).fcmpo(1, 0).ble("q2")
    a.label("q_next")
    a.lwz(5, RI, 4).addi(5, 5, 1).stw(5, RI, 4).cmpwi(5, 3).ble("q_loop")
    a.label("q1")
    a.li(3, 1).b("q_out")
    a.label("q2")
    a.li(3, 2).b("q_out")
    a.label("q0")
    a.li(3, 0)
    a.label("q_out")
    _data(a, 4)
    a.stb(3, QRES, 4)
    a.lwz(0, SUBLR, 4).mtlr(0).blr()


def log_sub(at):
    """The log subroutine: r3 = type, r4 = flags, r30 = COM; appends one record when LOGP is set. Uses r0, r5..r7,
    r11, r12 (= the data block on return) and f0; keeps everything else."""
    a = Asm(at)
    a.mflr(0)
    _data(a, 12)
    a.stw(0, LOGLR, 12)
    a.lwz(11, LOGP, 12).cmpwi(11, 0).beq("out")
    _slot(a, 5)
    a.stb(3, 0, 6).lwz(0, 0x0C, 30).stb(0, 1, 6).lbz(0, 0x25, 30).stb(0, 2, 6).stb(4, 3, 6)
    a.lwz(7, PLAYSTATE, 13).lha(0, 2, 7).sth(0, 4, 6).lwz(0, 0x18, 30).sth(0, 6, 6)
    a.lfs(0, AIMX, 12).stfs(0, 8, 6).lfs(0, AIMZ, 12).stfs(0, 12, 6)
    a.lfs(0, 0x10, 30).stfs(0, 16, 6).lfs(0, 0x14, 30).stfs(0, 20, 6)
    a.lfs(0, PX, 12).stfs(0, 24, 6).lfs(0, PZ, 12).stfs(0, 28, 6)
    a.label("out")
    a.lwz(0, LOGLR, 12).mtlr(0).blr()
    return a


def _slot(a, rc):
    """r6 = the next log record (r11 = the log); counts it. Uses r0, r6 and rc (the count)."""
    a.lwz(rc, 4, 11).addi(0, rc, 1).stw(0, 4, 11)
    a.lwz(6, 8, 11).addi(6, 6, -1).word((31 << 26) | (rc << 21) | (6 << 16) | (6 << 11) | (28 << 1))
    a.slwi(6, 6, 5).add(6, 6, 11).addi(6, 6, 16)


def _target_frame(a):
    """Record bytes 2 (target, COM +0x25, -1 without a COM) and 4..5 (frames since contact). Uses r0, r7."""
    a.li(0, -1).lwz(7, COM, 13).cmpwi(7, 0).beq("tg")
    a.lbz(0, 0x25, 7)
    a.label("tg")
    a.stb(0, 2, 6)
    a.li(0, 0).lwz(7, PLAYSTATE, 13).cmpwi(7, 0).beq("fr")
    a.lha(0, 2, 7)
    a.label("fr")
    a.sth(0, 4, 6)


def flog_sub(at):
    """FLOGFN (log builds): one type 8 record for fielder r5 (0..8; the chain +0x24 / +0x48 / base table, as
    _fielder_ptr), r4 = why. None there: nothing. Uses r0, r6..r8, r11, r12 (= the data block); keeps r3..r5, r9,
    r10 and every FPR."""
    a = Asm(at)
    _data(a, 12)
    a.lwz(11, LOGP, 12).cmpwi(11, 0).beq("out")
    a.slwi(6, 5, 2).lis(7, ha(FIELDERS)).addi(7, 7, lo(FIELDERS)).add(7, 7, 6)
    a.lwz(8, 0x24, 7).cmpwi(8, 0).bne("have")
    a.lwz(8, 0x48, 7).cmpwi(8, 0).bne("have")
    a.lwz(8, 0, 7).cmpwi(8, 0).beq("out")
    a.label("have")
    _slot(a, 7)
    a.li(0, 8).stb(0, 0, 6).stb(5, 1, 6).lbz(0, 0x22E, 8).stb(0, 3, 6).stb(4, 6, 6).li(0, 0).stb(0, 7, 6)
    _target_frame(a)
    for k, off in enumerate((4, 0xD4, 0xC, 0x1C)):
        a.lwz(0, off, 8).stw(0, 8 + 4 * k, 6)
    a.lwz(0, PX, 12).stw(0, 24, 6).lwz(0, PZ, 12).stw(0, 28, 6)
    a.label("out")
    a.blr()
    return a


def orig_sub(at):
    """ORIGFN: the launch start S for a throw at the cursor (f1, f2) (field x / z), as the game computes it (see
    OBJ_FN): r4 = the item object, or 0: r3 = the item (0..5), its pool's free object (none, or no manager: S = (0, 0),
    and the throw couldn't happen anyway). Returns f1, f2 = S. A normal call: clobbers r0, r3..r12, f0..f13, ctr, cr;
    keeps r14..r31, f14..f31."""
    a = Asm(at)
    a.stwu(1, -0x40, 1).mflr(0).stw(0, 0x44, 1)
    a.stfs(1, 0x20, 1).fneg(2, 2).stfs(2, 0x24, 1)
    a.lfs(0, ZERO, 2).stfs(0, 0x08, 1).stfs(0, 0x10, 1)
    a.cmpwi(4, 0).bne("obj")
    a.mr(4, 3).lwz(3, MANAGER, 13).cmpwi(3, 0).beq("out")
    a.bl(OBJ_FN).cmpwi(3, 0).beq("out")
    a.b("have")
    a.label("obj")
    a.mr(3, 4)
    a.label("have")
    a.stw(3, 0x28, 1)
    a.lwz(12, 0, 3).lwz(12, 0x3C, 12).mtctr(12).bctrl()
    a.lfs(2, 0x20, 1).lfs(3, 0x24, 1).addi(3, 1, 8).lwz(4, MANAGER, 13).li(5, 0).bl(START_FN)
    a.lfs(1, 0x20, 1).lfs(2, 0x24, 1).lwz(3, 0x28, 1).addi(4, 1, 8).bl(CLIP_FN)
    a.label("out")
    a.lfs(1, 0x08, 1).lfs(2, 0x10, 1)
    a.lwz(0, 0x44, 1).mtlr(0).addi(1, 1, 0x40).blr()
    return a


def onstep_sub(at):
    """ONSTEPFN: is (f1 x, f2 -z) on screen? r3 = 1 / 0: FUN_804570C8's camera projection and bounds only (not its
    ground / base test). A normal call: clobbers r0, r3..r12, f0..f13, ctr, cr."""
    a = Asm(at)
    a.stwu(1, -0x30, 1).mflr(0).stw(0, 0x34, 1)
    a.stfs(1, 0x10, 1).lfs(0, 0x2888, 2).stfs(0, 0x14, 1).stfs(2, 0x18, 1)
    a.li(3, 0).bl(CAM_FN)
    a.addi(4, 1, 0x10).addi(5, 1, 0x1C).bl(PROJ_FN)
    a.lfs(0, 0x288C, 2).lfs(1, 0x2890, 2).lfs(2, 0x1C, 1).lfs(3, 0x20, 1).li(3, 0)
    a.fcmpo(2, 0).ble("out").fcmpo(2, 1).bge("out").fcmpo(3, 0).ble("out").fcmpo(3, 1).bge("out")
    a.li(3, 1)
    a.label("out")
    a.lwz(0, 0x34, 1).mtlr(0).addi(1, 1, 0x30).blr()
    return a


def flight_stub(at):
    """FLIGHT (log builds only): FUN_80453764, the per-object item update the dispatch FUN_80455394 calls for each
    object of the active item (r3 = the object; tail-calls vtable +0x40). Shell / Fireball (manager +0x394) in use
    (+0x37, set by the launch FUN_80453824) and flying (+0x3C: Shell 1 (FUN_804592cc), Fireball 2 (FUN_804531bc)):
    one type-6 record, its position (+4) and velocity (+0x10). Bob-omb / POW: on the first frame of the burst state
    (Bob-omb +0x3B == 3, the blast FUN_80117408 uses; POW +0x3A == 2, FUN_80117E34's burst; LASTST holds last
    frame's state), a type-7 record (same layout) and a type-8 record for each fielder 0..8. A call site: r0,
    r4..r12, f0.. are free; r3 is kept.
    the author's 17:47 dump: flight records only every FLIGHT_EVERY frames (frames since contact); a burst once per object
    (LASTOBJ, until it leaves the burst state or 64 frames on); and LAUNCH, a type-9 record when the manager's last
    launch start (+0x374) changes: the real start, ORIGFN's for the cursor that throw used (start + direction +0x380,
    this object), and that cursor."""
    a = Asm(at)
    a.stwu(1, -0x20, 1).mflr(0).stw(0, 0x24, 1).stw(3, 0x08, 1)
    a.lwz(12, MANAGER, 13).cmpwi(12, 0).beq("out")
    _data(a, 11)
    a.lwz(0, 0x374, 12).lwz(5, LAUNCHX, 11).cmpw(0, 5).beq("fly")
    a.stw(0, LAUNCHX, 11)
    a.lfs(1, 0x374, 12).lfs(0, 0x380, 12).fadds(1, 1, 0).stfs(1, 0x10, 1)
    a.lfs(2, 0x37C, 12).lfs(0, 0x388, 12).fadds(2, 2, 0).stfs(2, 0x14, 1)
    a.mr(4, 3).bl(ORIGFN)
    _data(a, 12)
    a.lwz(11, LOGP, 12).cmpwi(11, 0).beq("fly")
    _slot(a, 5)
    a.li(0, 9).stb(0, 0, 6).lwz(7, MANAGER, 13).lwz(0, 0x394, 7).stb(0, 1, 6).li(0, 0).stb(0, 3, 6).sth(0, 6, 6)
    _target_frame(a)
    a.lwz(7, MANAGER, 13).lwz(0, 0x374, 7).stw(0, 8, 6).lwz(0, 0x37C, 7).stw(0, 12, 6)
    a.stfs(1, 16, 6).stfs(2, 20, 6).lwz(0, 0x10, 1).stw(0, 24, 6).lwz(0, 0x14, 1).stw(0, 28, 6)
    a.label("fly")
    a.lwz(3, 0x08, 1).lwz(12, MANAGER, 13)
    a.lwz(4, 0x394, 12).cmplwi(4, FIRE).bgt("burst")
    a.lbz(0, 0x37, 3).cmpwi(0, 0).beq("out")
    a.lbz(0, 0x3C, 3).addi(5, 4, 1).cmpw(0, 5).bne("out")
    a.lwz(7, PLAYSTATE, 13).lhz(0, 2, 7).andi_(0, 0, FLIGHT_EVERY - 1).bne("out")
    a.li(9, 6).b("rec")
    a.label("burst")
    a.cmplwi(4, POW).bgt("out")
    a.li(5, 3).lbz(0, 0x3B, 3).cmpwi(4, BOMB).beq("bs")
    a.li(5, 2).lbz(0, 0x3A, 3)
    a.label("bs")
    _data(a, 12)
    a.lwz(6, PLAYSTATE, 13).lha(6, 2, 6)
    a.cmpw(0, 5).beq("in_b")
    a.lwz(0, LASTOBJ, 12).cmpw(0, 3).bne("out")
    a.li(0, 0).stw(0, LASTOBJ, 12).b("out")
    a.label("in_b")
    a.lwz(0, LASTOBJ, 12).cmpw(0, 3).bne("new_b")
    a.lwz(0, LASTF, 12).subf(0, 0, 6).cmplwi(0, 64).blt("out")
    a.label("new_b")
    a.stw(3, LASTOBJ, 12).stw(6, LASTF, 12)
    a.li(9, 7)
    a.label("rec")
    _data(a, 12)
    a.lwz(11, LOGP, 12)
    _slot(a, 5)
    a.stb(9, 0, 6).stb(4, 1, 6).lbz(0, 0x36, 3).stb(0, 3, 6)
    _target_frame(a)
    a.sth(3, 6, 6)
    for k in range(6):
        a.lwz(0, 4 + 4 * k, 3).stw(0, 8 + 4 * k, 6)
    a.cmpwi(9, 7).bne("out")
    a.li(9, 0)
    a.label("fl")
    a.mr(5, 9).li(4, 1).bl(FLOGFN).addi(9, 9, 1).cmplwi(9, 8).ble("fl")
    a.label("out")
    a.lwz(3, 0x08, 1).lwz(0, 0x24, 1).mtlr(0).addi(1, 1, 0x20)
    a.word(FLIGHT_ORIG)
    a.b(FLIGHT + 4)
    return a


def f32(v):
    return struct.unpack(">f", struct.pack(">f", v))[0]


# -- the build -----------------------------------------------------------------------------------------------------

HOOKS = ((RESET, RESET_ORIG), (DECIDE, DECIDE_ORIG), (AIM, AIM_ORIG))
RELIES = ((0x80455838, 0x38A00000),)                     # li r5,0
DATA_ALIGN = 32


def configure(const):
    """Take this module's settings from the custom level (``const(key)``)."""
    global L5_PERFECT, L5_T, L5_K, MEET_LEAD, SPEED6, RUNNER_PAD
    L5_PERFECT, L5_T = int(const("items_error_free")), int(const("items_timing_error"))
    L5_K = int(round(const("items_aim_error") / L5_STEP))
    MEET_LEAD = int(const("items_lead"))
    speed = float(const("items_cursor"))
    SPEED6 = (speed, round(speed * 1.2, 4))
    RUNNER_PAD = float(const("items_runner_pad"))


def _code(base):
    """Sets the routines' addresses (``LOGFN`` ...) and returns ``(bytes, {site: word}, the shot routine)``."""
    global LOGFN, ORIGFN, ONSTEPFN, POWFN
    blob, hooks = b"", {}
    LOGFN = base
    blob += log_sub(LOGFN).assemble()
    ORIGFN = base + len(blob)
    blob += orig_sub(ORIGFN).assemble()
    ONSTEPFN = base + len(blob)
    blob += onstep_sub(ONSTEPFN).assemble()
    POWFN = base + len(blob)
    blob += pow_sub(POWFN).assemble()
    for site, make in ((RESET, reset_stub), (DECIDE, decide_stub), (AIM, aim_stub)):
        hooks[site] = branch_word(site, base + len(blob))
        blob += make(base + len(blob)).assemble()
    shots = base + len(blob)
    return blob + shots_sub(shots).assemble(), hooks, shots


def _place(base, image):
    """Sets ``DATA`` / ``SHOTB`` / ``POWB`` for data loaded at ``base``; returns a function giving the bytes
    for the shot routine's address."""
    global DATA, SHOTB, POWB, ROUTE_T
    ROUTE_T = dbg.ROUTE
    pad = bytes(-base % DATA_ALIGN)
    block = data_block(image, 0)
    block += bytes(-len(block) % 4)
    DATA = base + len(pad)
    SHOTB = DATA + len(block)
    shot = shot_block()
    shot += bytes(-len(shot) % 4)
    POWB = SHOTB + len(shot)
    powb = bytes(PHALF) + struct.pack(">f", 0.5) + bytes(POWB_SIZE - PHALF - 4)

    def data(shots):
        mine = bytearray(shot)
        struct.pack_into(">I", mine, SH_PTR, shots)
        return pad + block + bytes(mine) + powb
    return data


def build(base, const, image):
    """The routines and stubs, then their data, for load address ``base``: ``(bytes, {site: word})``. Needs
    ``defense_block.place`` first (the route table)."""
    configure(const)
    _place(base, image)                                  # any addresses: the code's length does not depend on them
    size = len(_code(base)[0])
    data = _place(base + size, image)
    blob, hooks, shots = _code(base)
    assert len(blob) == size
    return blob + data(shots), hooks
