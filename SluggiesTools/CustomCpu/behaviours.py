"""The custom CPU level's behaviour code: stubs hooked into the game's CPU decisions, one per behaviour switch.

Ported from the Slugout 0.8 patcher and reduced to one level: where that build picks a value or a path per
level, the custom level has one (its level 4 column for the constants; for the paths, the one its gates leave
reachable). Reference: ``_docs/_docs_custom_cpu/CustomCpuLevel.md``; tests: ``tests/test_custom_cpu_behaviours.py``.

A behaviour is hooked only while its switch is on; off, its sites hold the game's own words. Every stub starts
with the **gate**: the ``FLAG`` byte (1 during a custom match, ``engine``) and, where the game keeps one, the
CPU's own level at the hardest. So levels 1-4 run the stock instruction and go back, and a level changed from the
pause menu during a custom match falls back to the game's code too.

``build`` assembles the enabled behaviours for a load address (inside the engine's block) and returns the words
for their hook sites. A builder gets ``(base, const, image)``: the load address, the settings' values by key and
the DOL the code is for (the pitch model reads the characters' stats from it). ``Behaviour.relies``: other words of the game a stub's register use or return path rests on.

The behaviours:

* **Never bunts** (FUN_800C4824, the CPU batter's bunt decision at an at-bat's start; r3 = the batter AI). Its
  first word branches to the stub, which clears +0x31 (the at-bat-start flag) and +0x3B (the bunt) and returns
  before the frame is made: no roll.
* **Cursed ball** (FUN_800BA1B8, the batter's hit power). The pitch-quality multiplier applies when
  ``(pitcher +0x140 == 3 and +0x13B != 0) or +0x1BD == 3``; +0x140 == 3 is written on the human pitch path only.
  The stub replaces the ``bne`` after that compare and goes on to the +0x13B test whatever +0x140 holds, so the
  CPU's charged pitches are cursed too.
* **Charge timing** (FUN_800C4FF4, the CPU batter per frame; r29 = the batter AI, r30 = the batter, r31 = the
  pitcher). In place of the charge start block: start charging at once (+0xBF = +0xB6 = 1), and on flight frame
  2 set the charged frames +0x94 to ``(full +0x90 + last full +0x92) / 2 - frames left before the swing``, so
  the charge is mid-window on the swing's frame whatever the pitch speed.
* **Star swings** (FUN_800C5778, once per pitch; r26 = the batter AI, r29 = the batter, charge power u16 at
  +0xA6). The stub replaces the ``extsb r25,r0`` that makes the chance from the class and level tables: a batter
  with charge power under ``star_always`` whose team has 2 or more stars always star-swings; else the chance
  is 0 from ``star_never`` up, ``star_rare_pct`` from ``star_rare`` up, else ``star_top`` minus charge power. The
  game's star swing tables are not used then.
* **No star pitches** (FUN_800C710C, the CPU pitcher's star pitch decision; r3 = the brain, tier at +0x1A):
  returns 0 before its frame, so the callers pick a normal pitch.
* **Substitutions** (FUN_800C3C88 picks the relief pitcher, FUN_80134BE8 runs the change). The pitch stub
  replaces the game's three tiers after its candidate passes: score = curve stat times
  ``clamp(stamina / max stamina, 1/2, 1)``, the catcher dropped unless alone; up to 3 candidates within 225/256
  of the best; with 2 or 3, only those whose pitching leaves the best field chemistry stay; one is drawn with
  the game's random call. The field stub then swaps the fielders at 1B..RF into the best arrangement by
  chemistry (``SEARCH``: every permutation, pruned), fewest moves first, nothing moved on a tie.
* **Batter track** (FUN_800C4FF4 judges ball or strike, FUN_800C4AB4 moves the bat). The game's batter judges
  once, 7 frames out, on the ball's forecast x at its own z (pitcher +0xD4), while the umpire calls a strike
  when the ball is inside [-0.55, 0.55] where it crosses z = 1.12 or z = 0.32: a pitch still moving sideways
  is judged wrong. The take stub skips that judgement and decides on the swing frame (AI +0x45 + 1): take when
  the AI holds (+0x37), else ``FORECAST`` the ball to both planes and take unless either x is in the zone. The
  track stub aims the bat at the forecast for the batter's z instead of +0xD4 (whose last step interpolates
  with a mirrored fraction). Only for the plain batter AI (vtable ``BAT_VTABLE``) and no star pitch.
  ``FORECAST`` runs the game's flight model from the ball's current state, a frame at a time: drag while
  z <= +0xAC, the frame-rate decay, the sideways step +0x94 held constant. That tool's "mind game" (a batter
  that guesses from remembered pitches) belongs to another of its levels and is not ported.
* **Rundowns**: ``rundown.py``. **The pitch model**: ``pitching.py``. **Fielding against items** (with the dive
  rule ``dive.py``): ``item_defense.py``. **Item use at bat**: ``items.py``. The last two share a small block
  (``defense_block.py``), placed in front of the behaviours' code when either is on.
"""

from dataclasses import dataclass
from typing import Callable

try:
    from ..Dol.ppc import Asm, branch_word, ha, lo
    from . import defense_block, item_defense, items, pitching, rundown, settings
    from .behaviour_base import FLAG, HARDEST, RAND, RNG, TEAM_OF
    from .behaviour_base import gate as _gate
except ImportError:
    from Dol.ppc import Asm, branch_word, ha, lo
    from CustomCpu import defense_block, item_defense, items, pitching, rundown, settings
    from CustomCpu.behaviour_base import FLAG, HARDEST, RAND, RNG, TEAM_OF
    from CustomCpu.behaviour_base import gate as _gate

__all__ = ['FLAG', 'HARDEST', 'RAND', 'RNG', 'TEAM_OF', 'BEHAVIOURS', 'BY_FEATURE', 'SITES', 'build', 'enabled']


# -- never bunts ---------------------------------------------------------------------------------------------------

BUNT, BUNT_STOCK = 0x800C4824, 0x9421FFE0                # stwu r1,-0x20(r1)
AI_TIER, AI_START, AI_BUNT = 0x39, 0x31, 0x3B


def bunt_stub(base: int, const=None) -> Asm:
    a = Asm(base)
    _gate(a, 12, 'stock')
    a.lbz(12, AI_TIER, 3).cmpwi(12, HARDEST).bne('stock')
    a.li(0, 0).stb(0, AI_START, 3).stb(0, AI_BUNT, 3).blr()
    a.label('stock')
    a.word(BUNT_STOCK)
    a.b(BUNT + 4)
    return a


# -- cursed ball ---------------------------------------------------------------------------------------------------

CURSED, CURSED_STOCK = 0x800BA38C, 0x40820010            # bne 0x800BA39C, after lbz r0,0x140(r3); cmplwi r0,3
CURSED_COMPARE = (0x800BA388, 0x28000003)
CURSED_CHARGED, CURSED_NOT = 0x800BA390, 0x800BA39C      # the +0x13B test / the +0x1BD test


def cursed_stub(base: int, const=None) -> Asm:
    a = Asm(base)
    _gate(a, 12, 'stock')
    a.b(CURSED_CHARGED)
    a.label('stock')
    a.cmplwi(0, 3).bne('not')                            # the gate's compare replaced the site's
    a.b(CURSED_CHARGED)
    a.label('not')
    a.b(CURSED_NOT)
    return a


# -- charge timing -------------------------------------------------------------------------------------------------

CHARGE, CHARGE_STOCK = 0x800C5058, 0x88030034            # lbz r0,0x34(r3): the charge start block's first word
CHARGE_AFTER = 0x800C50F0                                # past the block; r0, r3, r4, r5 are loaded again there
TICK, TICK_FRAME = -0x1684, 4                            # r13: the pitch object, its flight frame (s16)
AI_SWING_AT = 0x45                                       # the swing frame, set on flight frame 1
B_CHARGE, B_SWING_TYPE, B_FRAMES, B_FULL, B_LAST = 0xBF, 0xB6, 0x94, 0x90, 0x92


def charge_stub(base: int, const=None) -> Asm:
    a = Asm(base)
    _gate(a, 4, 'stock')
    a.lbz(0, AI_TIER, 29).cmpwi(0, HARDEST).bne('stock')
    a.lbz(0, B_CHARGE, 30).cmpwi(0, 0).bne('fix')
    a.li(0, 1).stb(0, B_CHARGE, 30).stb(0, B_SWING_TYPE, 30)         # start now, as the stock block does
    a.label('fix')
    a.lbz(0, B_CHARGE, 30).cmpwi(0, 1).bne('out')                    # building
    a.lwz(3, TICK, 13).lha(4, TICK_FRAME, 3).cmpwi(4, 2).bne('out')
    a.lbz(5, AI_SWING_AT, 29).cmpwi(5, 0).beq('out')
    a.addi(5, 5, 1).subf(5, 4, 5)                                    # frames left before the swing
    a.lha(0, B_FULL, 30).lha(3, B_LAST, 30).add(0, 0, 3).rlwinm(0, 0, 31, 1, 31)     # the full window's middle
    a.subf(0, 5, 0).cmpwi(0, 0).bge('set')
    a.li(0, 0)
    a.label('set')
    a.sth(0, B_FRAMES, 30)
    a.label('out')
    a.b(CHARGE_AFTER)
    a.label('stock')
    a.word(CHARGE_STOCK)
    a.b(CHARGE + 4)
    return a


# -- star swings ---------------------------------------------------------------------------------------------------

SWING, SWING_STOCK = 0x800C5934, 0x7C190774              # extsb r25,r0: the chance, compared with rand(100) after
SWING_BACK = SWING + 4
SWING_RELIES = ((0x800C5930, 0x7C190214), (0x800C5938, 0x480A02DD), (0x800C593C, 0x7C03C800))
CHARGE_POWER = 0xA6                                      # the batter's, u16 (stat edits and chemistry included)
METER = -0x1540                                          # r13: the teams' star meters (s16 per side)
TWO_STARS = 100


def star_chance(charge_power: int, always: int, never: int, rare: int, rare_pct: int, top: int,
                meter: int = 0) -> int:
    """The star swing chance (%) the stub leaves in r25."""
    if charge_power < always and meter >= TWO_STARS:
        return 100
    if charge_power >= never:
        return 0
    if charge_power >= rare:
        return rare_pct
    return top - charge_power


def swing_stub(base: int, const) -> Asm:
    always, never, rare, rare_pct, top = (int(const(k)) for k in ('star_always', 'star_never', 'star_rare',
                                                                   'star_rare_pct', 'star_top'))
    a = Asm(base)
    a.word(SWING_STOCK)
    _gate(a, 12, 'back')
    a.lbz(0, AI_TIER, 26).cmpwi(0, HARDEST).bne('back')
    a.lhz(0, CHARGE_POWER, 29)
    a.cmplwi(0, always).bge('chance')
    a.bl(TEAM_OF)                                        # LR is saved in the function's frame
    a.lbz(0, 0x2C, 3).slwi(0, 0, 1)
    a.lwz(12, METER, 13).add(12, 12, 0).lha(11, 0, 12)
    a.lwz(3, RNG, 13).li(4, 100)                         # the rand call's arguments, loaded before the site
    a.cmpwi(11, TWO_STARS).blt('chance')
    a.li(25, 100).b(SWING_BACK)
    a.label('chance')
    a.lhz(0, CHARGE_POWER, 29)
    a.cmplwi(0, never).blt('below')
    a.li(25, 0).b(SWING_BACK)
    a.label('below')
    a.cmplwi(0, rare).blt('low')
    a.li(25, rare_pct).b(SWING_BACK)
    a.label('low')
    a.li(12, top).subf(25, 0, 12)
    a.label('back')
    a.b(SWING_BACK)
    return a


# -- no star pitches -----------------------------------------------------------------------------------------------

STAR_PITCH, STAR_PITCH_STOCK = 0x800C710C, 0x9421FFD0    # stwu r1,-0x30(r1)
STAR_PITCH_RELIES = ((0x800C7110, 0x7C0802A6),)
BRAIN_TIER = 0x1A


def star_pitch_stub(base: int, const=None) -> Asm:
    a = Asm(base)
    _gate(a, 12, 'stock')
    a.lbz(12, BRAIN_TIER, 3).cmpwi(12, HARDEST).bne('stock')
    a.li(3, 0).blr()
    a.label('stock')
    a.word(STAR_PITCH_STOCK)
    a.b(STAR_PITCH + 4)
    return a


# -- substitutions -------------------------------------------------------------------------------------------------

PITCH, PITCH_STOCK = 0x800C3E24, 0x1D7F04FE              # mulli r11,r31,0x4FE, after the two candidate passes
PITCH_BACK, PITCH_RET = 0x800C3E28, 0x800C3F70           # the game's tiers / the function's epilogue
FIELD, FIELD_STOCK = 0x80134D04, 0x38000003              # li r0,3, after the CPU's pitcher swap call
FIELD_BACK = FIELD + 4
SUBS_RELIES = ((PITCH_RET, 0x80010044), (0x80134D00, 0x48025E51))

POS_OF = 0x8015A96C                                      # (lineup, side, slot) -> position
SWAP = 0x8015AB50                                        # (lineup, side, position a, position b)
CHEM = 0x8015C800                                        # (roster team, slot a, slot b) -> 0 bad, 1, 2 good
MAX_STAMINA = 0x8015C914                                 # (roster team, slot)
CTL, LINEUP, RECORDS, STAMINA = -0x1D58, -0x15A8, -0x2C8, -0x1570      # r13
CTL_LEVEL = 8                                            # the control's level per side (u32; 0: level 4)
TOP, BAND = 3, 225                                       # the pool: up to 3 within 225/256 of the best score
ORDER = (7, 6, 8, 3, 5, 2, 4)                            # the search's loop order: CF, LF, RF, 2B, SS, 1B, 3B
WEIGHT = {0: 0, 1: 1, 2: 3}                              # chemistry -> pair weight

PF = 0xA0                                                # the pitch stub's frame: scores at 8, the pool at 0x30
PSO, PTMP, PKEYS, PBEST, PREGS = 0x34, 0x40, 0x50, 0x60, 0x70


def pitch_stub(base: int, search: int) -> Asm:
    """On entry r3 = the candidate count (low byte), their slots are bytes at r1+0x20, r29 = the control,
    r30 = the game object, r31 = the roster team."""
    a = Asm(base)
    _gate(a, 12, 'stock')
    a.lbz(12, 0x2D, 30).slwi(12, 12, 2).add(12, 12, 29).lwz(12, CTL_LEVEL, 12)
    a.cmpwi(12, 0).beq('ours')
    a.label('stock')
    a.word(PITCH_STOCK)
    a.b(PITCH_BACK)
    a.label('ours')
    a.stwu(1, -PF, 1).stmw(20, PREGS, 1)
    a.clrlwi(20, 3, 24)                                              # r20 = the candidate count
    a.li(3, 0).cmpwi(20, 0).beq('ret')
    a.addi(21, 1, PF + 0x20)                                         # r21 = the candidates' slots
    a.lwz(22, LINEUP, 13).lbz(23, 0x2D, 30)                          # r22 = the lineup, r23 = the side
    a.mulli(24, 31, 0x4FE).lwz(0, RECORDS, 13).add(24, 24, 0)        # r24 = the team's roster records
    a.mulli(25, 31, 0x120).lwz(0, STAMINA, 13).add(25, 25, 0)        # r25 = the team's stamina rows
    a.li(26, 0)
    a.label('score')
    a.lbzx(27, 21, 26)                                               # r27 = the slot
    a.cmpwi(20, 1).beq('rate')                                       # the only candidate: the catcher may pitch
    a.mr(3, 22).mr(4, 23).mr(5, 27).bl(POS_OF)
    a.cmpwi(3, 1).bne('rate')
    a.li(28, -1).b('store')                                          # the catcher: dropped
    a.label('rate')
    a.mulli(3, 27, 0x8E).add(3, 3, 24)
    a.lhz(28, 0x24, 3)                                               # the curve stat
    a.cmpwi(28, 1).bge('pos').li(28, 1)
    a.label('pos')
    a.mr(3, 31).mr(4, 27).bl(MAX_STAMINA)
    a.slwi(0, 27, 5).add(4, 25, 0).lhz(4, 0x358, 4)
    a.li(0, 256)
    a.cmpwi(3, 0).ble('scale')
    a.slwi(4, 4, 8).divwu(0, 4, 3)
    a.cmplwi(0, 256).ble('capped').li(0, 256)
    a.label('capped')
    a.cmplwi(0, 128).bge('scale').li(0, 128)
    a.label('scale')
    a.mullw(28, 28, 0).rlwinm(28, 28, 24, 8, 31)                     # score * factor / 256
    a.label('store')
    a.slwi(0, 26, 2).addi(3, 1, 8).stwx(28, 3, 0)
    a.addi(26, 26, 1).cmpw(26, 20).blt('score')
    a.li(27, 0)                                                      # r27 = the pool's size
    a.label('pick')
    a.li(3, -1).li(4, -1).li(5, 0).addi(6, 1, 8)
    a.label('max')
    a.slwi(0, 5, 2).lwzx(0, 6, 0)
    a.cmpw(0, 3).ble('next')
    a.mr(3, 0).mr(4, 5)
    a.label('next')
    a.addi(5, 5, 1).cmpw(5, 20).blt('max')
    a.cmpwi(4, 0).blt('chosen')
    a.cmpwi(27, 0).bne('band')
    a.mulli(26, 3, BAND).rlwinm(26, 26, 24, 8, 31)                   # r26 = the band's floor
    a.label('band')
    a.cmpw(3, 26).blt('chosen')
    a.lbzx(0, 21, 4).addi(5, 1, 0x30).stbx(0, 5, 27).addi(27, 27, 1)
    a.slwi(0, 4, 2).li(5, -1).stwx(5, 6, 0)
    a.cmpwi(27, TOP).blt('pick')
    a.label('chosen')
    a.li(3, 0).cmpwi(27, 0).beq('ret')
    a.cmpwi(27, 2).blt('roll')
    # 2 or 3 in the pool: keep those whose pitching leaves the best field. slot of each position first.
    a.li(0, -1).stw(0, PSO, 1).stw(0, PSO + 4, 1).stb(0, PSO + 8, 1)
    a.mulli(6, 23, 0x28).add(6, 6, 22).addi(7, 1, PSO).li(3, 0)
    a.label('entry')
    a.slwi(4, 3, 2).add(4, 4, 6).lha(5, 0xE, 4).lhz(0, 0xC, 4)
    a.cmplwi(5, 8).bgt('eskip').stbx(0, 7, 5)
    a.label('eskip')
    a.addi(3, 3, 1).cmpwi(3, 9).blt('entry')
    for pos in range(9):                                             # a position without a player: plain draw
        a.lbz(0, PSO + pos, 1).cmplwi(0, 9).bge('roll')
    a.li(26, 0)
    a.label('cand')
    a.lwz(0, PSO, 1).stw(0, PTMP, 1).lwz(0, PSO + 4, 1).stw(0, PTMP + 4, 1).lbz(0, PSO + 8, 1).stb(0, PTMP + 8, 1)
    a.addi(5, 1, 0x30).lbzx(5, 5, 26).mr(3, 22).mr(4, 23).bl(POS_OF)
    a.cmplwi(3, 8).bgt('nopos')
    a.lbz(0, PTMP, 1).addi(4, 1, PTMP).stbx(0, 4, 3)                 # the old pitcher takes his position
    a.label('nopos')
    a.addi(3, 1, PTMP + 2).mr(4, 31).addi(5, 1, PBEST).bl(search)
    a.slwi(0, 26, 2).addi(4, 1, PKEYS).stwx(3, 4, 0)
    a.addi(26, 26, 1).cmpw(26, 27).blt('cand')
    a.li(3, -1).li(5, 0).addi(4, 1, PKEYS)
    a.label('kmax')
    a.slwi(0, 5, 2).lwzx(0, 4, 0).cmpw(0, 3).ble('knext').mr(3, 0)
    a.label('knext')
    a.addi(5, 5, 1).cmpw(5, 27).blt('kmax')
    a.li(5, 0).li(6, 0).addi(7, 1, 0x30)
    a.label('keep')
    a.slwi(0, 5, 2).lwzx(0, 4, 0).cmpw(0, 3).bne('drop')
    a.lbzx(0, 7, 5).stbx(0, 7, 6).addi(6, 6, 1)
    a.label('drop')
    a.addi(5, 5, 1).cmpw(5, 27).blt('keep')
    a.mr(27, 6)
    a.label('roll')
    a.lwz(3, RNG, 13).mr(4, 27).bl(RAND)
    a.addi(5, 1, 0x30).lbzx(5, 5, 3)
    a.mr(3, 22).mr(4, 23).bl(POS_OF)
    a.sth(3, 0x18, 29).li(3, 1)                                      # as the game: the position to bring in
    a.label('ret')
    a.lmw(20, PREGS, 1).addi(1, 1, PF)
    a.b(PITCH_RET)
    return a


FF = 0xB0                                                # the search's and the field stub's frame
SLOTS, USED, BEST, AT, W = 0x08, 0x14, 0x1C, 0x24, 0x30


def search_sub(base: int) -> Asm:
    """SEARCH(r3 = the 7 slots at 1B..RF, r4 = roster team, r5 = out: the best field, 7 bytes in ``ORDER``)
    -> r3 = the best key, r4 = its moves. Saves LR and r14-r31; r0, r3-r12, ctr and cr are clobbered."""
    a = Asm(base)
    a.stwu(1, -FF, 1).mflr(0).stw(0, FF + 4, 1).stmw(14, 0x68, 1)
    a.mr(28, 4).mr(27, 5).li(6, 0)
    a.label('copy')
    a.lbzx(0, 3, 6).addi(7, 6, SLOTS + 2).stbx(0, 1, 7).addi(6, 6, 1).cmpwi(6, 7).blt('copy')
    a.li(20, 0)                                                      # the 7x7 pair weights
    a.label('wi')
    a.addi(21, 20, 1)
    a.label('wj')
    a.addi(4, 1, SLOTS + 2).lbzx(5, 4, 21).lbzx(4, 4, 20).mr(3, 28).bl(CHEM)
    a.cmpwi(3, 2).bne('notgood').li(3, 3).b('wset')
    a.label('notgood')
    a.cmpwi(3, 0).beq('wset').li(3, 1)
    a.label('wset')
    a.addi(6, 1, W).slwi(0, 20, 3).add(0, 0, 21).stbx(3, 6, 0).slwi(0, 21, 3).add(0, 0, 20).stbx(3, 6, 0)
    a.addi(21, 21, 1).cmpwi(21, 7).blt('wj')
    a.addi(20, 20, 1).cmpwi(20, 6).blt('wi')
    a.li(0, 0).stw(0, USED, 1).sth(0, USED + 4, 1).stb(0, USED + 6, 1)
    for d, pos in enumerate(ORDER):
        a.li(0, pos - 2).stb(0, BEST + d, 1)                         # the current field starts as the best

    def wat(i, j):
        return W + i * 8 + j
    a.lbz(3, wat(5, 4), 1).lbz(0, wat(5, 6), 1).add(3, 3, 0).slwi(23, 3, 6)          # r23 = its key
    a.lbz(3, wat(1, 3), 1).slwi(3, 3, 3).add(23, 23, 3)
    a.lbz(3, wat(3, 0), 1).add(23, 23, 3).lbz(3, wat(2, 0), 1).add(23, 23, 3)
    a.li(24, 0)                                                      # r24 = its moves
    a.addi(21, 1, W).addi(22, 1, USED)

    def w(rt, ri, rj):
        a.slwi(0, ri, 3).add(0, 0, rj).lbzx(rt, 21, 0)

    def level(d):                                                    # loop d's player is r14 + d
        rc = 14 + d
        a.li(rc, 0)
        a.label(f'loop{d}')
        a.cmpwi(rc, 7).bge(f'end{d}')
        a.lbzx(0, 22, rc).cmpwi(0, 0).bne(f'next{d}')
        if d == 6:
            w(3, 18, 19)
            w(4, 20, 19)
            a.add(3, 3, 4).add(3, 3, 26)                             # the key
            a.cmpw(3, 23).blt(f'next{d}')
            a.li(4, 0)
            for dd, pos in enumerate(ORDER):                         # the moves (cr1: cr0 keeps the key compare)
                a.cmpwi(14 + dd, pos - 2, cr=1).beq(f'same{dd}', cr=1).addi(4, 4, 1)
                a.label(f'same{dd}')
            a.bgt('accept')
            a.cmpw(4, 24).bge(f'next{d}')
            a.label('accept')
            a.mr(23, 3).mr(24, 4)
            for dd in range(7):
                a.stb(14 + dd, BEST + dd, 1)
        else:
            if d == 2:                                               # the outfield is set: bound, or prune
                w(25, 14, 15)
                w(3, 14, 16)
                a.add(25, 25, 3).slwi(26, 25, 6)
                a.addi(3, 26, 63).cmpw(3, 23).blt(f'next{d}')
            if d == 4:                                               # 2B and SS too
                w(3, 17, 18)
                a.slwi(3, 3, 3).slwi(26, 25, 6).add(26, 26, 3)
                a.addi(3, 26, 7).cmpw(3, 23).blt(f'next{d}')
            a.li(0, 1).stbx(0, 22, rc)
            level(d + 1)
            a.li(0, 0).stbx(0, 22, rc)
        a.label(f'next{d}')
        a.addi(rc, rc, 1).b(f'loop{d}')
        a.label(f'end{d}')

    level(0)
    for d in range(7):
        a.lbz(0, BEST + d, 1).stb(0, d, 27)
    a.mr(3, 23).mr(4, 24)
    a.lmw(14, 0x68, 1).lwz(0, FF + 4, 1).mtlr(0).addi(1, 1, FF)
    a.blr()
    return a


def field_stub(base: int, search: int) -> Asm:
    """r31 = the task (kept); r0 and r3-r12 are free at the site."""
    a = Asm(base)
    _gate(a, 3, 'stock')
    a.lwz(3, CTL, 13).lbz(4, 0x2D, 31).slwi(4, 4, 2).add(3, 3, 4).lwz(3, CTL_LEVEL, 3)
    a.cmpwi(3, 0).beq('ours')
    a.label('stock')
    a.word(FIELD_STOCK).b(FIELD_BACK)
    a.label('ours')
    a.stwu(1, -FF, 1).mflr(0).stw(0, FF + 4, 1).stmw(14, 0x68, 1)
    a.lbz(30, 0x2D, 31).lwz(29, LINEUP, 13)                          # r30 = the side, r29 = the lineup
    a.bl(TEAM_OF).lbz(28, 0x2B, 3)                                   # r28 = the roster team
    a.li(0, -1).stw(0, SLOTS, 1).stw(0, SLOTS + 4, 1).stb(0, SLOTS + 8, 1)
    a.mulli(27, 30, 0x28).add(27, 27, 29)
    a.addi(26, 1, SLOTS).li(3, 0)
    a.label('entry')
    a.slwi(4, 3, 2).add(4, 4, 27).lha(5, 0xE, 4).lhz(6, 0xC, 4)
    a.cmplwi(5, 8).bgt('skip').stbx(6, 26, 5)
    a.label('skip')
    a.addi(3, 3, 1).cmpwi(3, 9).blt('entry')
    for pos in range(2, 9):                                          # a position without a player: leave it
        a.lbz(0, SLOTS + pos, 1).cmplwi(0, 9).bge('out')
    a.addi(3, 1, SLOTS + 2).mr(4, 28).addi(5, 1, BEST).bl(search)
    a.mr(24, 4)
    a.cmpwi(24, 0).beq('out')
    a.li(0, 0x0001).sth(0, AT, 1).li(0, 0x0203).sth(0, AT + 2, 1)    # the player at each position now
    a.li(0, 0x0405).sth(0, AT + 4, 1).li(0, 6).stb(0, AT + 6, 1)
    for i in range(6):                                               # selection sort: one swap per position
        pos = i + 2
        a.lbz(15, BEST + ORDER.index(pos), 1)
        a.li(14, i).addi(16, 1, AT)
        a.label(f'find{i}')
        a.lbzx(0, 16, 14).cmpw(0, 15).beq(f'found{i}')
        a.addi(14, 14, 1).cmpwi(14, 7).blt(f'find{i}')
        a.b('out')
        a.label(f'found{i}')
        a.cmpwi(14, i).beq(f'placed{i}')
        a.mr(3, 29).mr(4, 30).li(5, pos).addi(6, 14, 2).bl(SWAP)
        a.lbz(0, AT + i, 1).stbx(0, 16, 14).stb(15, AT + i, 1)
        a.label(f'placed{i}')
    a.label('out')
    a.lmw(14, 0x68, 1).lwz(0, FF + 4, 1).mtlr(0).addi(1, 1, FF)
    a.word(FIELD_STOCK).b(FIELD_BACK)
    return a


def _subs(base: int, const, image=None) -> tuple[bytes, dict[int, int]]:
    field_at = base + len(pitch_stub(base, base))
    search = field_at + len(field_stub(field_at, field_at))
    blob = pitch_stub(base, search).assemble() + field_stub(field_at, search).assemble() \
        + search_sub(search).assemble()
    return blob, {PITCH: branch_word(PITCH, base), FIELD: branch_word(FIELD, field_at)}


# -- batter track --------------------------------------------------------------------------------------------------

TAKE, TAKE_STOCK = 0x800C5160, 0x887D003F                # lbz r3,0x3F(r29): r29 = AI, r30 = frame, r31 = pitcher
TAKE_BACK, SWING_CHECK = 0x800C5164, 0x800C5194
TRACK, TRACK_STOCK = 0x800C4B9C, 0xC07E00D4              # lfs f3,0xD4(r30): r28 = AI, r29 = batter, r30 = pitcher
TRACK_BACK = 0x800C4BA0
TRACK_RELIES = ((0x800C5194, 0x887D0045), (0x800C4B98, 0x881C0038), (0x800C4BA0, 0x2C000000))
BAT_VTABLE = 0x806404D0                                  # the plain CPU batter (not Toy Field, minigames, training)
AI_NO_SWING, AI_HOLD, AI_DECOY = 0x32, 0x37, 0x38
P_STAR = 0x141                                           # the pitch is a star pitch
ZONE = 0x80627A90                                        # f32: x low, x high, plane z 1.12, plane z 0.32
ZONE_PLANES = (8, 12)
BATTER_PTR, BATTER_PTR_ALT = -0x1D78, -0x1D7C            # r13
HZ, DECAY, SPEED_FLOOR, F_ZERO = -0x1658, -0x7338, -0x7120, -0x7150     # r13 byte; r2: f32[Hz], -0.05, 0.0
FORECAST_FRAMES = 300


def forecast(a: Asm) -> Asm:
    """Label ``forecast``, a leaf: r3 = pitcher, f1 = plane z -> f1 = x there and r3 = 1, or r3 = 0 when
    the ball never crosses it. Uses r0, r4, ctr, cr0 and f0-f13."""
    a.label('forecast')
    a.lfs(2, 0x38, 3).lfs(3, 0x40, 3)                                # x, z
    a.fcmpo(3, 1).bge('sim')
    a.lfs(4, 0x44, 3).lfs(5, 0x4C, 3).b('cross')                     # already past: the last frame's line
    a.label('sim')
    a.lfs(6, 0x50, 3).lfs(7, 0x58, 3)                                # vx, vz
    a.lfs(8, 0x94, 3).lfs(9, 0xB4, 3)                                # the sideways step, the drag
    a.lfs(10, F_ZERO, 2)
    a.lwz(0, 0x34, 3).cmpwi(0, 0).bne('drag')
    a.lbz(0, 0x140, 3).cmplwi(0, 1).beq('drag')
    a.lfs(10, 0x188, 3)                                              # a human pitcher's extra drag
    a.label('drag')
    a.fadds(10, 9, 10)
    a.lfs(11, 0xAC, 3)                                               # drag applies up to this z
    a.lbz(0, HZ, 13).slwi(0, 0, 2).addi(4, 2, DECAY)
    a.lfsx(12, 4, 0)
    a.lfs(13, SPEED_FLOOR, 2)
    a.li(0, FORECAST_FRAMES).mtctr(0)
    a.label('frame')
    a.fcmpo(3, 11).bgt('free')
    a.fnmsubs(0, 10, 7, 7)                                           # vz less its drag,
    a.fcmpo(0, 13).bge('free')                                       # while that stays faster than the floor
    a.fmr(7, 0).fnmsubs(6, 9, 6, 6)
    a.label('free')
    a.fmuls(6, 6, 12).fmuls(7, 7, 12)
    a.fmr(4, 2).fmr(5, 3)                                            # the previous position
    a.fadds(2, 2, 8).fadds(2, 2, 6).fadds(3, 3, 7)
    a.fcmpo(3, 1).blt('cross')
    a.bdnz('frame')
    a.li(3, 0).blr()
    a.label('cross')                                                 # (f4, f5) -> (f2, f3) crosses the plane
    a.fsubs(0, 5, 3).lfs(9, F_ZERO, 2).fcmpo(0, 9).ble('never')
    a.fsubs(6, 5, 1).fdivs(6, 6, 0).fsubs(7, 2, 4)
    a.fmadds(1, 7, 6, 4)
    a.li(3, 1).blr()
    a.label('never')
    a.li(3, 0).blr()
    return a


def _track_gate(a: Asm, ai: int, pitcher: int, stock: str) -> None:
    _gate(a, 3, stock)
    a.lbz(0, AI_TIER, ai).cmpwi(0, HARDEST).bne(stock)
    a.lwz(3, 0, ai).load_addr(4, BAT_VTABLE).cmpw(3, 4).bne(stock)
    a.lbz(0, P_STAR, pitcher).cmpwi(0, 0).bne(stock)


def track_stubs(base: int) -> Asm:
    """Labels ``take_stub`` and ``track_stub`` (LR is saved in both functions' frames)."""
    a = Asm(base)
    a.label('take_stub')
    _track_gate(a, 29, 31, 'take_stock')
    a.lbz(3, AI_SWING_AT, 29).cmpwi(3, 0).beq('take_done')
    a.addi(3, 3, 1).cmpw(30, 3).bne('take_done')                     # the swing frame only
    a.lbz(0, AI_HOLD, 29).cmpwi(0, 0).bne('take')
    for plane in ZONE_PLANES:
        a.mr(3, 31).lis(4, ha(ZONE)).lfs(1, lo(ZONE + plane), 4).bl('forecast')
        a.cmpwi(3, 0).beq('take_done')
        a.lis(4, ha(ZONE)).lfs(2, lo(ZONE), 4).lfs(3, lo(ZONE + 4), 4)
        a.fcmpo(1, 2).blt(f'ball{plane}')
        a.fcmpo(1, 3).ble('take_done')                               # in the zone on this plane: swing
        a.label(f'ball{plane}')
    a.label('take')
    a.li(0, 1).stb(0, AI_NO_SWING, 29)
    a.label('take_done')
    a.b(SWING_CHECK)
    a.label('take_stock')
    a.word(TAKE_STOCK)
    a.b(TAKE_BACK)

    a.label('track_stub')
    _track_gate(a, 28, 30, 'track_stock')
    a.lwz(4, BATTER_PTR, 13).cmpwi(4, 0).bne('batter').lwz(4, BATTER_PTR_ALT, 13)
    a.label('batter')
    a.lfs(1, 0x40, 4).mr(3, 30).bl('forecast')
    a.cmpwi(3, 0).beq('track_stock')
    a.fmr(3, 1).lbz(0, AI_DECOY, 28).b(TRACK_BACK)
    a.label('track_stock')
    a.word(TRACK_STOCK)
    a.lbz(0, AI_DECOY, 28).b(TRACK_BACK)                             # r0: the site before, clobbered by the gate
    forecast(a)
    return a


def _track(base: int, const, image=None) -> tuple[bytes, dict[int, int]]:
    a = track_stubs(base)
    return a.assemble(), {TAKE: branch_word(TAKE, a.labels['take_stub']),
                          TRACK: branch_word(TRACK, a.labels['track_stub'])}


# -- the list ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Behaviour:
    feature: str                                         # its switch (``settings``)
    hooks: tuple[tuple[int, int], ...]                   # (site, the game's word): replaced by a branch
    build: Callable                                      # (base, const, image) -> (code, {site: word})
    relies: tuple[tuple[int, int], ...] = ()             # (address, word) the code rests on


def _one(site: int, stub) -> Callable:
    def build(base: int, const, image=None) -> tuple[bytes, dict[int, int]]:
        return stub(base, const).assemble(), {site: branch_word(site, base)}
    return build


BEHAVIOURS = (
    Behaviour('cpu-no-bunts', ((BUNT, BUNT_STOCK),), _one(BUNT, bunt_stub)),
    Behaviour('cpu-cursed-ball', ((CURSED, CURSED_STOCK),), _one(CURSED, cursed_stub), (CURSED_COMPARE,)),
    Behaviour('cpu-charge-timing', ((CHARGE, CHARGE_STOCK),), _one(CHARGE, charge_stub)),
    Behaviour('cpu-star-swings', ((SWING, SWING_STOCK),), _one(SWING, swing_stub), SWING_RELIES),
    Behaviour('cpu-no-star-pitches', ((STAR_PITCH, STAR_PITCH_STOCK),), _one(STAR_PITCH, star_pitch_stub),
              STAR_PITCH_RELIES),
    Behaviour('cpu-subs', ((PITCH, PITCH_STOCK), (FIELD, FIELD_STOCK)), _subs, SUBS_RELIES),
    Behaviour('cpu-batter-track', ((TAKE, TAKE_STOCK), (TRACK, TRACK_STOCK)), _track, TRACK_RELIES),
    Behaviour(rundown.FEATURE, rundown.HOOKS, lambda base, const, image=None: rundown.build(base, const)),
    Behaviour(pitching.FEATURE, pitching.HOOKS, pitching.build_for),
    Behaviour(item_defense.FEATURE, item_defense.HOOKS, item_defense.build),
    Behaviour(items.FEATURE, items.HOOKS, items.build, items.RELIES),
)
BY_FEATURE = {b.feature: b for b in BEHAVIOURS}
SITES = {site: stock for b in BEHAVIOURS for site, stock in b.hooks}
assert set(BY_FEATURE) == {s.key for s in settings.SWITCHES if s.gated}, 'a behaviour switch without code'
SHARED_BLOCK = (item_defense.FEATURE, items.FEATURE)     # the behaviours that use ``defense_block``
assert len(SITES) == sum(len(b.hooks) for b in BEHAVIOURS), 'two behaviours hook one site'


def enabled(edits: dict) -> list[Behaviour]:
    return [b for b in BEHAVIOURS if (edits or {}).get(b.feature)]


def build(base: int, edits: dict, image=None) -> tuple[bytes, dict[int, int]]:
    """The enabled behaviours' code for load address ``base`` and ``{site: word}`` for their hooks. ``image``:
    the DOL it is built for."""
    def const(key: str):
        return settings.value(settings.BY_KEY[key], edits)
    code, hooks = b'', {}
    if any((edits or {}).get(feature) for feature in SHARED_BLOCK):
        defense_block.place(base)
        code = defense_block.blob(base)
        code += bytes(-len(code) % 4)
    for behaviour in enabled(edits):
        blob, words = behaviour.build(base + len(code), const, image)
        code += blob + bytes(-len(blob) % 4)
        hooks.update(words)
    return code, hooks
