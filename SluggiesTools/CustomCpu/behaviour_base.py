"""What every behaviour stub of the custom CPU level shares: the gate and a few addresses of the game."""

try:
    from ..Dol.ppc import Asm, ha, lo
    from ..Roster import dol_hammerspace
except ImportError:
    from Dol.ppc import Asm, ha, lo
    import dol_hammerspace

FLAG = dol_hammerspace.CUSTOM_CPU_FLAG       # 1 during a custom match (``engine``)
HARDEST = 3                                  # the CPU's tier at the game's level 4

RNG = -0x1578                                # r13: the random generator
RAND = 0x80165C14                            # (generator, n) -> 0..n-1
TEAM_OF = 0x801319BC                         # the game object: +0x2B roster team, +0x2C the side


def gate(a: Asm, reg: int, skip) -> Asm:
    """Branch to ``skip`` unless a custom match is on. Clobbers ``reg`` (not r0) and cr0."""
    return a.lis(reg, ha(FLAG)).lbz(reg, lo(FLAG), reg).cmpwi(reg, 0).beq(skip)
