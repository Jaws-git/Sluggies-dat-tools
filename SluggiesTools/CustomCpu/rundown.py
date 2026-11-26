"""The custom CPU level's rundown plan (the "Smart rundown throws" switch). Ported from the
Slugout 0.8 patcher with one value per constant, without its debugging log. Tests:
``tests/test_custom_cpu_rundown.py``.

FUN_800E05E8 plans one non-forced runner between bases: it writes ai+0x29C[r] (throw base; base + 10 = hold for
the cover; -1 none), ai+0x28C[r] (carry base; 9 = chase and tag) and ai+0x27C[r] (margin in frames, < 0: the out
is makeable). r27 = ai, r29 = 4 * r, r30 = the runner, r31 = the field. Its two branches start by reading the
runner's direction +0x183 (1 advancing, 2 stopped, 3 heading back): ``HOOK_IN`` (in the rundown zone) and
``HOOK_OUT`` (everything else). Both become a branch to a small stub: no custom match -> the stock instruction
and back; else the shared **body**, which ends at the function's epilogue.

The body, for a runner advancing, heading back, or stopped between bases (+0x138 < 0); others get no plan:

1. **Possession.** Each time a different fielder holds the ball (field+0x2A4C) a throw is counted and a roll
   made with the game's random call: under ``rundown_first`` % for the first throw of a rundown,
   ``rundown_early`` % for the others -> "early". A rundown is one play: the count resets when the play frame
   goes back or jumps more than ``NEW_RUNDOWN`` frames.
2. **Plan.** The game's own FUN_800E0F98(ai, r, dir, &throw, &carry) -> margin: to the next base (dir 1) for a
   runner advancing or stopped, with the cover fielder's arrival time +0x1DC held at -1 for the call (so it
   throws at once instead of waiting for a cover that is still running); to the base behind (dir 0) for a
   runner heading back.
3. **Chase.** The holder chases and tags (carry 9, throw -1) a runner stopped in the rundown zone, or any
   runner he reaches before the runner's own frames to the base he is heading for: the game's run-time estimate
   to the point within tag reach of the runner's safe line, divided by the Ball Dash factor when the holder has
   that ability.
4. **Advancing, under 3/8 of the leg:** hold (throw base + 10) instead of throwing ahead.
5. **Heading back:** the throw waits (base + 10) until a deadline: windup + the planner's flight to the base
   + ``THROW_LAG`` + ``rundown_buffer`` + 1 frames no longer beats the runner's best time to his safe line
   (cap + mashing for a human, the cap for a CPU runner). An "early" roll throws ``rundown_lead`` frames before
   that, for the first ``rundown_rolled`` throws. Without an early roll it also throws once the runner is
   committed (he cannot stop short of the tag any more), inside a CPU runner's no-turn line, or when the out is
   slipping away anyway.
6. **Ball Dash carry** (heading back): a holder with the ability carries the ball to the base behind when the
   dash gets him there before the runner.

The body's state (the roll, the throw count, temporaries) is ``SCRATCH_SIZE`` bytes right behind its code.
"""

import struct

try:
    from ..Dol.ppc import Asm, branch_word
    from .behaviour_base import RAND, RNG, gate
except ImportError:
    from Dol.ppc import Asm, branch_word
    from CustomCpu.behaviour_base import RAND, RNG, gate

FEATURE = 'cpu-rundown'
EPILOGUE = 0x800E0F60
PLAN_ONE, RUN_FRAMES = 0x800E0F98, 0x80114350
HOOK_IN, STOCK_IN = 0x800E06C8, 0x88BE0183               # lbz r5,0x183(r30)
HOOK_OUT, STOCK_OUT = 0x800E0990, 0x881E0183             # lbz r0,0x183(r30)
HOOKS = ((HOOK_IN, STOCK_IN), (HOOK_OUT, STOCK_OUT))
THROW, CARRY, MARGIN = 0x29C, 0x28C, 0x27C
HOLDER_TABLES = (0x80708D9C, 0x80708DC0, 0x80708D78)     # fielder id -> object, tried in this order
BASE_XZ = 0x80625798                                     # f32 x, z per base
BALL_DASH = 10                                           # the fielding ability

# the runner
R_TOP, R_TOP_K, R_MASH, R_TURN, R_TURN_K, R_BACK = 0xE0, 0x114, 0x104, 0xDC, 0x11C, 0x142
R_VEL, R_PAST, R_FRAC, R_LEG = 0xC4, 0x9C, 0xA8, 0xAC
R_ESC_TO, R_SAFE = 0x140, 0x94
TAG_REACH, ON_BASE, NO_TURN, ONE = -0x7A58, -0x7A9C, -0x703C, -0x7094       # r2 floats
K_MAIN, K_ALT, F_ZERO, MAGIC = -0x7430, -0x742C, -0x7600, -0x7AB0           # r2: dash factors, 0.0, int -> float
WINDUP, FLIGHT = 0x8011A66C, 0x800CA018
GAME, SETTINGS, PLAY = -0x163C, -0x1D58, -0x1684         # r13
DASH_OFF, DASH_ALT = -0x644, -0x94                       # r13
BALL_ZONE = 0x2A6B
CHASE = 9
FORWARD_MIN = 0.375
COVER, ETA = 0x2EE, 0x1DC
NEW_RUNDOWN = 120
THROW_LAG = 18

# the scratch block: f32 R', f32 k, f32 FORWARD_MIN, the cover's ETA, the state (last play frame, last holder,
# throws, the roll), the runner index, two temporaries
S_COVER_ETA, S_STATE, S_R4, S_DL, SCRATCH_SIZE = 12, 16, 32, 36, 44
SCRATCH = struct.pack('>fffi', 0.0, 1.0, FORWARD_MIN, 0) + struct.pack('>7i', 0x7FFFFFFF, -1, -1, 0, 0, 0, 0)
assert len(SCRATCH) == SCRATCH_SIZE


def _to_float(a: Asm, ft: int, r: int) -> None:
    """ft = (float) r (s32). Uses r1+0x10 (the function's own int / float temporary), r0 and f2; changes r."""
    a.xoris(r, r, 0x8000)
    a.stw(r, 0x14, 1).lis(0, 0x4330).stw(0, 0x10, 1)
    a.lfd(ft, 0x10, 1)
    a.lfd(2, MAGIC, 2)
    a.fsubs(ft, ft, 2)


def _speeds(a: Asm) -> None:
    """f4 = the runner's top-speed cap, f5 = the cap + the most mashing adds (r30 = the runner)."""
    a.lfs(4, R_TOP, 30).lfs(5, R_TOP_K, 30).fmuls(4, 4, 5)
    a.lfs(5, R_MASH, 30).fadds(5, 4, 5)


def _fielder(a: Asm, none: str, got: str) -> None:
    """r3 = the fielder with id r4 (s16; < 0 none), at label ``got``; ``none`` if there is none. Uses r3-r5."""
    a.cmpwi(4, 0).blt(none)
    a.clrlwi(5, 4, 24).slwi(5, 5, 2)
    a.load_addr(3, HOLDER_TABLES[0]).lwzx(3, 3, 5).cmpwi(3, 0).bne(got)
    a.load_addr(3, HOLDER_TABLES[1]).lwzx(3, 3, 5).cmpwi(3, 0).bne(got)
    a.slwi(5, 4, 2)
    a.load_addr(3, HOLDER_TABLES[2]).lwzx(3, 3, 5).cmpwi(3, 0).beq(none)
    a.label(got)


def _holder(a: Asm, none: str, got: str) -> None:
    """r3 = the ball holder (r31 = the field), as ``_fielder``."""
    a.lha(4, 0x2A4C, 31)
    _fielder(a, none, got)


def _chase(a: Asm, scratch: int) -> None:
    """Falls through to ``no_chase``, or sets throw -1 / carry 9 / the margin r28 and goes to ``commit``."""
    a.load_addr(9, scratch)
    a.lfs(1, ONE, 2).stfs(1, 4, 9)
    a.lwz(0, DASH_OFF, 13).cmpwi(0, 0).bne('ch_k')
    _holder(a, 'no_chase', 'ch_h1')
    a.lwz(12, 0, 3).lwz(12, 0x3C, 12).mtctr(12).bctrl()              # the holder's fielding ability
    a.clrlwi(3, 3, 24).cmplwi(3, BALL_DASH).bne('ch_k')
    a.lfs(1, K_MAIN, 2).lwz(0, DASH_ALT, 13).cmpwi(0, 0).beq('ch_k1')
    a.lfs(1, K_ALT, 2)
    a.label('ch_k1')
    a.load_addr(9, scratch)
    a.stfs(1, 4, 9)
    a.label('ch_k')
    a.lfs(13, R_LEG, 30).lfs(0, F_ZERO, 2).fcmpo(13, 0).ble('no_chase')
    a.lbz(4, 0x17D, 30).cmplwi(4, 3).ble('ch_f').li(4, 0)
    a.label('ch_f')
    a.lbz(5, 0x17E, 30).clrlwi(5, 5, 30)
    a.lbz(0, 0x183, 30).cmplwi(0, 2).bne('ch_go')
    a.rlwinm(6, 29, 30, 2, 31).add(6, 6, 27).lbz(6, 0x2D4, 6).cmpwi(6, 0).bne('ch_now')
    a.label('ch_go')
    a.lbz(0, 0x183, 30)
    a.cmplwi(0, 3).beq('ch_dback')
    a.mr(6, 4).mr(4, 5).mr(5, 6).lha(7, R_ESC_TO, 30).b('ch_e')      # advancing: he escapes to the next base
    a.label('ch_dback')
    a.lha(7, R_BACK, 30).b('ch_e')
    a.label('ch_now')
    a.li(28, -1).li(0, -1).stw(0, 0xC, 1).li(0, CHASE).stw(0, 0x8, 1).b('commit')
    a.label('ch_e')                                                  # r4 = his base, r5 = the other, r7 = frames
    _to_float(a, 6, 7)
    a.load_addr(9, scratch)
    a.stfs(6, 0, 9)
    a.slwi(4, 4, 3).load_addr(3, BASE_XZ).add(4, 3, 4)
    a.slwi(5, 5, 3).add(5, 3, 5)
    a.lfs(7, 0, 4).lfs(8, 4, 4).lfs(9, 0, 5).lfs(10, 4, 5)
    a.lfs(11, R_SAFE, 30).lfs(12, TAG_REACH, 2).lfs(13, R_LEG, 30).fdivs(12, 12, 13).fadds(11, 11, 12)
    a.fsubs(9, 9, 7).fmadds(1, 9, 11, 7)
    a.fsubs(10, 10, 8).fmadds(2, 10, 11, 8)                          # the point within tag reach of his line
    _holder(a, 'no_chase', 'ch_h2')
    a.bl(RUN_FRAMES)
    _to_float(a, 1, 3)
    a.load_addr(9, scratch)
    a.lfs(3, 4, 9).fdivs(1, 1, 3)
    a.lfs(0, 0, 9).fsubs(1, 1, 0)
    a.fmr(3, 1).lfs(0, F_ZERO, 2).fcmpo(3, 0).bge('no_chase')
    a.fctiwz(1, 1)
    a.stfd(1, 0x10, 1)
    a.lwz(28, 0x14, 1)
    a.li(0, -1).stw(0, 0xC, 1).li(0, CHASE).stw(0, 0x8, 1).b('commit')
    a.label('no_chase')


def _possession(a: Asm, scratch: int, first: int, early: int) -> None:
    """A new play or rundown: reset; a new holder: count a throw and roll. Uses r0, r3-r9 and cr0."""
    state = scratch + S_STATE
    a.lwz(3, PLAY, 13).cmpwi(3, 0).beq('st_done')
    a.lha(3, 0, 3)
    a.load_addr(9, state)
    a.lwz(4, 0, 9).stw(3, 0, 9)
    a.cmpw(3, 4).blt('st_new')
    a.addi(4, 4, NEW_RUNDOWN).cmpw(3, 4).ble('st_same')
    a.label('st_new')
    a.li(0, -1).stw(0, 4, 9).stw(0, 8, 9)
    a.label('st_same')
    a.lha(5, 0x2A4C, 31).cmpwi(5, 0).blt('st_done')
    a.lwz(6, 4, 9).cmpw(5, 6).beq('st_done')
    a.stw(5, 4, 9).lwz(6, 8, 9).addi(6, 6, 1).stw(6, 8, 9)
    a.lwz(3, RNG, 13).li(4, 100).bl(RAND)
    a.load_addr(9, state)
    a.lwz(6, 8, 9).li(4, early).cmpwi(6, 0).bne('st_pct').li(4, first)
    a.label('st_pct')
    a.li(0, 1).cmpw(3, 4).blt('st_roll').li(0, 0)
    a.label('st_roll')
    a.load_addr(9, state)
    a.stw(0, 12, 9)
    a.label('st_done')


def body(base: int, scratch: int, const) -> Asm:
    first, early, lead, buffer, rolled = (int(const(k)) for k in ('rundown_first', 'rundown_early', 'rundown_lead',
                                                                   'rundown_buffer', 'rundown_rolled'))
    a = Asm(base)
    a.load_addr(9, scratch + S_R4)
    a.stw(4, 0, 9)
    _possession(a, scratch, first, early)
    a.lbz(0, 0x183, 30)
    a.cmplwi(0, 1).beq('plan').cmplwi(0, 3).beq('plan')
    a.cmplwi(0, 2).bne('out')
    a.lha(0, 0x138, 30).cmpwi(0, 0).bge('out')
    a.label('plan')
    a.li(28, 0).lbz(0, 0x183, 30).cmplwi(0, 3).beq('call')
    a.lbz(4, 0x17E, 30).slwi(4, 4, 1).add(4, 4, 27).lha(4, COVER, 4)         # not heading back: the cover's
    _fielder(a, 'call', 'cv_got')                                            # arrival time reads "on the base"
    a.mr(28, 3)
    a.load_addr(9, scratch + S_COVER_ETA)
    a.lha(0, ETA, 28).stw(0, 0, 9).li(0, -1).sth(0, ETA, 28)
    a.label('call')
    a.li(5, 1).lbz(0, 0x183, 30).cmplwi(0, 3).bne('dir')
    a.li(5, 0)
    a.label('dir')
    a.load_addr(9, scratch + S_R4)
    a.lwz(4, 0, 9)
    a.mr(3, 27).addi(6, 1, 0xC).addi(7, 1, 0x8).bl(PLAN_ONE)
    a.cmpwi(28, 0).beq('m')
    a.load_addr(9, scratch + S_COVER_ETA)
    a.lwz(0, 0, 9).sth(0, ETA, 28)
    a.label('m')
    a.mr(28, 3)
    _chase(a, scratch)
    a.lbz(0, 0x183, 30).cmplwi(0, 1).bne('fw_ok')
    a.load_addr(9, scratch)
    a.lfs(1, R_FRAC, 30).lfs(0, 8, 9).fcmpo(1, 0).bge('fw_ok')
    a.lwz(5, 0xC, 1).cmplwi(5, 3).bgt('fw_ok')
    a.addi(5, 5, 10).stw(5, 0xC, 1).b('commit')                      # advancing, not far yet: hold
    a.label('fw_ok')
    a.lbz(0, 0x183, 30).cmplwi(0, 3).bne('commit')
    # heading back: r8 = the holder's windup, r7 = a CPU runner
    a.lwz(5, 0xC, 1).cmplwi(5, 3).bgt('hb_ok')
    a.li(8, 0)
    _holder(a, 'hb_w', 'hb_got')
    a.bl(WINDUP).clrlwi(8, 3, 24)
    a.label('hb_w')
    a.li(7, 0).lwz(3, GAME, 13).cmpwi(3, 0).beq('hb_who')
    a.lbz(4, 0x2C, 3).lwz(3, SETTINGS, 13).cmpwi(3, 0).beq('hb_who')
    a.add(3, 3, 4).lbz(7, 0x24, 3)
    a.label('hb_who')
    a.load_addr(9, scratch + S_DL)
    a.stw(7, 0, 9).stw(8, 4, 9)
    _holder(a, 'hb_rule', 'dl_got')
    a.lwz(4, 0xC, 1).slwi(4, 4, 3).load_addr(3, BASE_XZ).add(3, 3, 4)
    a.lfs(1, 0, 3).lfs(2, 4, 3).bl(FLIGHT)
    a.load_addr(9, scratch + S_DL)
    a.lwz(7, 0, 9).lwz(8, 4, 9)
    a.add(3, 3, 8)
    a.addi(3, 3, buffer + THROW_LAG + 1)
    a.load_addr(9, scratch + S_STATE)
    a.li(10, 0).lwz(0, 8, 9).cmpwi(0, rolled).bge('dl_x')
    a.lwz(10, 12, 9).cmpwi(10, 0).beq('dl_x')
    a.addi(3, 3, lead)
    a.label('dl_x')                                                  # r3 = the ball's frames, r10 = an early roll
    _to_float(a, 1, 3)
    a.lfs(6, R_PAST, 30).lfs(3, R_LEG, 30).fmuls(6, 6, 3)
    _speeds(a)
    a.cmpwi(7, 0).beq('dl_v').fmr(5, 4)
    a.label('dl_v')
    a.fdivs(6, 6, 5)                                                 # the runner's best frames to his safe line
    a.fcmpo(1, 6).blt('dl_no').beq('dl_no')
    a.b('hb_ok')                                                     # the deadline (or no numbers): throw
    a.label('dl_no')
    a.cmpwi(10, 0).bne('hb_hold')
    a.label('hb_rule')
    a.cmpwi(7, 0).beq('hb_human')
    a.cmpwi(28, 0).bge('hb_ok').b('hb_line')
    a.label('hb_human')
    a.mr(6, 28)
    _to_float(a, 1, 6)
    a.lha(6, R_BACK, 30)
    _to_float(a, 6, 6)
    _speeds(a)
    a.fadds(1, 1, 6).fmuls(7, 6, 4).fdivs(7, 7, 5).fsubs(1, 1, 7)
    a.lfs(0, F_ZERO, 2).fcmpo(1, 0).bge('hb_ok')                     # the out is slipping away anyway
    a.label('hb_line')
    _to_float(a, 4, 8)
    a.lfs(3, R_VEL, 30).fneg(3, 3)
    a.lfs(9, R_FRAC, 30).lfs(7, R_PAST, 30)
    a.lfs(5, R_LEG, 30).fmuls(8, 3, 4)
    a.cmpwi(7, 0).beq('hb_phys')
    a.lbz(0, BALL_ZONE, 31).cmplwi(0, 3).bgt('hb_phys')
    a.fdivs(10, 8, 5).fsubs(9, 9, 10)
    a.lfs(10, NO_TURN, 2).fcmpo(9, 10).blt('hb_ok')                  # a CPU runner inside his no-turn line
    a.label('hb_phys')
    a.lfs(0, F_ZERO, 2).fcmpo(3, 0).ble('hb_hold')
    a.fmuls(7, 7, 5).fsubs(7, 7, 8)
    a.lfs(9, R_TURN, 30).lfs(10, R_TURN_K, 30).fmuls(9, 9, 10).fadds(9, 9, 9)
    a.fmuls(10, 3, 3).fdivs(10, 10, 9).fsubs(7, 7, 10)
    a.lfs(10, TAG_REACH, 2).lfs(11, ON_BASE, 2).fsubs(10, 10, 11)
    a.fcmpo(7, 10).ble('hb_ok')                                      # committed: he can't stop short of the tag
    a.label('hb_hold')
    a.lwz(5, 0xC, 1).addi(5, 5, 10).stw(5, 0xC, 1)
    a.label('hb_ok')
    # Ball Dash: the holder carries it to the base behind when the dash beats the runner
    a.lwz(0, DASH_OFF, 13).cmpwi(0, 0).bne('commit')
    _holder(a, 'commit', 'got')
    a.mr(31, 3)
    a.lwz(12, 0, 31).lwz(12, 0x3C, 12).mtctr(12).bctrl()
    a.clrlwi(3, 3, 24).cmplwi(3, BALL_DASH).bne('commit')
    a.lbz(4, 0x17D, 30).cmplwi(4, 3).ble('base').li(4, 0)
    a.label('base')
    a.slwi(4, 4, 3).load_addr(3, BASE_XZ).add(3, 3, 4)
    a.lfs(1, 0, 3).lfs(2, 4, 3)
    a.mr(3, 31).bl(RUN_FRAMES)
    _to_float(a, 1, 3)
    a.lfs(3, K_MAIN, 2).lwz(0, DASH_ALT, 13).cmpwi(0, 0).beq('k')
    a.lfs(3, K_ALT, 2)
    a.label('k')
    a.fdivs(1, 1, 3)
    a.lha(3, R_BACK, 30)
    _to_float(a, 0, 3)
    _speeds(a)
    a.fmuls(0, 0, 4).fdivs(0, 0, 5)
    a.fsubs(1, 1, 0).lfs(0, F_ZERO, 2).fcmpo(1, 0).bge('commit')
    a.fctiwz(1, 1)
    a.stfd(1, 0x10, 1)
    a.lwz(28, 0x14, 1)
    a.li(0, -1).stw(0, 0xC, 1)
    a.lbz(0, 0x17D, 30).cmplwi(0, 3).ble('carry').li(0, 0)
    a.label('carry')
    a.stw(0, 0x8, 1)
    a.label('commit')
    a.add(4, 27, 29).stw(28, MARGIN, 4)
    a.lwz(0, 0xC, 1).stw(0, THROW, 4).lwz(0, 0x8, 1).stw(0, CARRY, 4)
    a.label('out')
    a.b(EPILOGUE)
    return a


def hook_stub(base: int, hook: int, stock: int, body_at: int) -> Asm:
    """r12 and cr0 only: the function never reads r12 and both sites' next compare rewrites cr0."""
    a = Asm(base)
    gate(a, 12, 'stock')
    a.b(body_at)
    a.label('stock')
    a.word(stock).b(hook + 4)
    return a


def build(base: int, const) -> tuple[bytes, dict[int, int]]:
    """The body, its scratch block and the two hook stubs for load address ``base``; ``{site: word}``."""
    size = len(body(base, base, const))
    blob = body(base, base + size, const).assemble() + SCRATCH
    hooks = {}
    for hook, stock in HOOKS:
        at = base + len(blob)
        blob += hook_stub(at, hook, stock, base).assemble()
        hooks[hook] = branch_word(hook, at)
    return blob, hooks
