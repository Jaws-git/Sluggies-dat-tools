"""The fielding decision's state block and the route-target table, shared by ``item_defense``, ``dive`` and
``items``: their offsets, and the addresses of the build in progress (``ADDR``, ``ROUTE``: set by
``place`` before the stubs are assembled).

Overview: ``_docs/_docs_custom_cpu/CustomCpuLevel.md``. Tests: ``tests/test_custom_cpu_items.py``.

The notes below come with the code, which was written for a build with three custom levels. Here there is one:
read "level 6", "level 5 / 6" and tests of the flag's value as "a custom match" (``one_level.py``; the gate is
``behaviour_base.gate``). Dated remarks in quotes are the original author's design decisions; a "dump" is a RAM
dump taken during that work and "the log" a debugging log, neither of which is part of this repo. [C] marks a
fact read from the game's code, [I] / INFERRED / UNMEASURED one that was not measured.

The level 6 attack decision's debug block (the author, 2026-09-27: one RAM dump must show why the CPU does or doesn't
attack). A fixed 64-byte block, WRITTEN by the stubs of ``item_defense.py`` every frame they run for the chaser
and READ by the dive rule (``dive.py``: PHASE, CHASER, FRAME) and the route stubs. Present whenever
``item_defense`` or ``items`` is in the build (``behaviours.build`` places it).

Layout (big-endian; offsets are the constants below). The writer writes FRAME first each frame it runs; a reader
treats the block as this frame's only when FRAME == the frame it logs (else the stub didn't run: gated upstream).
  FRAME   s16  frames since contact (*(r13-0x1684)+2) when the stubs last ran for the chaser. Written first by the
               header stub at 0x800E28CC (the top of FUN_800E2830, before its gates: runs every frame 800E21EC calls
               800E2830, i.e. ball loose, not held, a chaser, no pass target) and again by the decision stub. FRAME
               stale = 800E2830 didn't run that frame (ball held / not loose / no chaser / pass target).
               Frame order [C]: 800E246C (dive press, ``dive.py``) runs BEFORE 800E2830 in 800E21EC, so a reader in
               800E246C sees the previous frame's block; the 8011F748 hook and ROUTE (800D5A38's fielder updates) run
               after it and see this frame's.
  CHASER  s8   the chaser's fielder index (0..8), -1 none
  REASON  u8   why no attack / what it did this frame (REASONS below). The header writes the gate reasons 2-5 / 13
               (and 16 on the POW jump); past the gates the decision stub overwrites it.
  PHASE   u8   the attack sequence (PHASES below). 1-3 = the attack is planned for CHASER this frame (he goes to the
               spot, no catch / run-catch / dive commit), 4 = pressed this frame or already in the action (+0x265).
  PRESS   u8   1 = the attack press (ai+0xD4 = 1) was written this frame
  T       s8   the first path frame (from now) the game's hit test FUN_8011F2B0 would connect from his spot, -1 none
  MATE_ID s8   the nearest teammate's index, -1 none
  MATE_D  f32  the nearest teammate's distance
  SPOT_X / SPOT_Z f32  the attack spot the route sends him to
  SPOT_D  f32  his distance to the attack spot
  BEST_D  f32  the closest ground distance the predicted ball comes to his current spot in the scan window
  BEST_Y  f32  the ball height at that frame
  REACH   f32  his hit reach (F+0x1C + ball radius game+0x420)
  FACE    f32  the angle between his facing and the direction to the ball (radians), when set
  AUX0    s32  the frames he needs to the attack spot, (int)(SPOT_D / F+0xEC) (0 when there; -1 not computed)
  AUX1    u32  the stub of ``dive.py`` (runs BEFORE the decision stub each frame): (frame << 16) | flags, written only when
               a level 6 CPU dive press came; bit 0 = dive suppressed for the attack. The decision stub must not
               clear it.
  T_SPOT  s8   the first path frame the hit test connects from the attack spot (the plan's test), -1 none
  FLAGS   u8   bit 0: 8011F748's catch / run-catch / dive block skipped for the plan (hook 0x8011FCD4); bit 1: a
               committed catch cancelled for the plan (ROUTE); bit 2: his facing written toward the ball (TURN); bit 3: the ball comes through his reach where he stands: S = here (the author: no move).
               Cleared by the header each frame.
  M_FRAME s16  the path frame of the landing / pickup point B the spot is built from (-1 none)
  JUMP    u8   the fly-ball peak jump this frame (the normal play; JUMPS below), 0 = not evaluated. Cleared by the header.
  JUMP_D  f32  his predicted distance to the ball at the jump's peak frame (the reach test), when evaluated
"""

FRAME, CHASER, REASON, PHASE, PRESS, T, MATE_ID = 0x00, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07
MATE_D, SPOT_X, SPOT_Z, SPOT_D, BEST_D, BEST_Y, REACH, FACE = 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24
AUX0, AUX1 = 0x28, 0x2C
T_SPOT, FLAGS, M_FRAME = 0x30, 0x31, 0x32
JUMP, JUMP_D = 0x34, 0x38
SIZE = 0x40
JUMPS = {0: "", 1: "JUMPED (fly, catch at the peak)", 2: "no jump: not a fly", 3: "no jump: the ball out of his reach at the peak",
         4: "no jump: waiting (the ball still above his peak reach)", 5: "no jump: too late (the ball already below)"}


REASONS = {
    0: "attacked (press written)",
    1: "not level 6 / not the CPU chaser",
    2: "gate: in the air",
    3: "gate: busy (8012BA58)",
    4: "gate: reaction delay",
    5: "gate: ball held / not loose",
    6: "gate: chemistry off or ai+0x36C set",
    7: "no teammate within the distance",
    8: "moving to the attack spot",
    9: "turning to face the ball",
    10: "in place, waiting: the hit is more than 3 frames out",
    11: "no frame connects from the attack spot (ball out of reach / height band): the normal play",
    12: "no attack spot (the ball never comes down to his height)",
    13: "already attacking (+0x265 != 0)",
    14: "committed catch took over (planned, but a catch already committed that can't be cancelled)",
    15: "the attack spot can't be reached before the ball: the normal play",
    16: "POW jump (overrides everything)",
    17: "no attack: a POW pending (the batting slot holds one unused, or one is in flight)",
    18: "no attack: the nearest teammate can't act (stunned / knocked down / in the air)",
    255: "past the gates, the decision stub pending (never left after a frame's stubs ran)",
}

PHASES = {0: "none", 1: "moving to the spot", 2: "turning", 3: "set, waiting", 4: "attacking"}
ROUTE_N, ROUTE_SIZE = 9, 12
BLOCK_ALIGN = 32
ADDR = None
ROUTE = None


def blob(base: int) -> bytes:
    """The block (at the next ``BLOCK_ALIGN`` boundary from ``base``) and the route table behind it."""
    return bytes(-base % BLOCK_ALIGN) + bytes(SIZE) + bytes([0x80]) * (ROUTE_N * ROUTE_SIZE)


def place(base: int) -> None:
    """Point ``ADDR`` / ``ROUTE`` at ``blob(base)`` loaded at ``base``."""
    global ADDR, ROUTE
    ADDR = base + (-base % BLOCK_ALIGN)
    ROUTE = ADDR + SIZE
