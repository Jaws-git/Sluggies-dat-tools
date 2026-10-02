"""Grow a function's stack frame so a stack buffer in it gets room for more entries.

Port of the external tool's ``wheel7.grow_frame``. Every r1-relative offset
at or above ``threshold`` (the end of the buffer being grown) moves up by
``grow`` bytes, and so do the ``stwu r1,-N(r1)`` prologue and the frame size
the epilogue adds back. Anything below the threshold (the buffer and what
sits under it) stays. The function must not use r1 in an X-form
instruction, since such an offset can't be adjusted; that is refused.

The scan covers ``[start, last]`` word by word, so ``last`` must be the
function's final instruction. A function with two buffers is grown twice,
higher buffer first.
"""

try:
    from . import dolfile
    from .ppc import signed16
except ImportError:
    import dolfile
    from ppc import signed16

# D-form loads/stores (32..55, incl. lmw/stmw and FP), addi (14), addic/addic. (12, 13)
D_FORM_OPCODES = frozenset(range(32, 56)) | {12, 13, 14}
# psq_l(u)/psq_st(u): 12-bit offsets
PSQ_OPCODES = frozenset((56, 57, 60, 61))
STWU_R1_HIGH = 0x9421


class FrameError(dolfile.DolError):
    pass


def grow_frame(image: dolfile.DolImage, start: int, last: int, threshold: int,
               grow: int = 0x10) -> list[tuple[int, int, int]]:
    """Shift r1 offsets ``>= threshold`` in ``[start, last]`` by ``grow``; returns ``(address, old, new)``."""
    if grow % 8:
        raise FrameError('frames must stay 8-byte aligned')
    first = image.u32(start)
    if first >> 16 != STWU_R1_HIGH:
        raise FrameError(f'0x{start:08X}: {first:08X} is not stwu r1,-N(r1)')
    frame = -signed16(first)
    changes = []
    for address in range(start, last + 4, 4):
        word = image.u32(address)
        opcode, rd, ra = word >> 26, (word >> 21) & 31, (word >> 16) & 31
        new = None
        if address == start:
            new = (word & 0xFFFF0000) | ((-(frame + grow)) & 0xFFFF)
        elif opcode in D_FORM_OPCODES and ra == 1:
            offset = signed16(word)
            # The epilogue's ``addi r1,r1,frame`` is the offset of the caller's frame: it moves too.
            if offset >= threshold:
                if offset + grow >= 0x8000:
                    raise FrameError(f'0x{address:08X}: offset 0x{offset:X} + 0x{grow:X} does not fit')
                new = (word & 0xFFFF0000) | (offset + grow)
        elif opcode in PSQ_OPCODES and ra == 1:
            offset = word & 0xFFF
            offset = offset - 0x1000 if offset & 0x800 else offset
            if offset >= threshold:
                if offset + grow >= 0x800:
                    raise FrameError(f'0x{address:08X}: psq offset 0x{offset:X} + 0x{grow:X} does not fit')
                new = (word & 0xFFFFF000) | (offset + grow)
        elif opcode == 31 and 1 in (ra, (word >> 11) & 31, rd):
            raise FrameError(f'0x{address:08X}: {word:08X} uses r1 in an X-form instruction')
        if new is not None and new != word:
            image.write_word(address, new)
            changes.append((address, word, new))
    return changes
