"""Small PowerPC (Gekko) assembler for hook stubs, with labels and forward branches.

Build code at a fixed address; labels resolve when ``assemble`` runs::

    a = Asm(0x807B7000)
    a.cmpwi('r4', 0x65, cr=1).beq('reject', cr=1)
    a.b(0x804A5094)
    a.label('reject')
    a.b(0x804A5794)
    code = a.assemble()

Registers are ``'r3'``/``'f1'`` strings or plain ints. Branch targets are
absolute addresses or label names. Only the instructions the roster
expansion emits are here; add more as needed, with a test against a known
encoding.
"""

import struct


class PpcError(ValueError):
    pass


def _r(reg) -> int:
    value = int(reg[1:]) if isinstance(reg, str) else int(reg)
    if not 0 <= value < 32:
        raise PpcError(f'bad register {reg!r}')
    return value


def ha(value: int) -> int:
    """High half for ``lis`` paired with a signed low half (``addi``/loads)."""
    return ((value + 0x8000) >> 16) & 0xFFFF


def lo(value: int) -> int:
    return value & 0xFFFF


def signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def d_form(op: int, rt, ra, imm: int) -> int:
    return (op << 26) | (_r(rt) << 21) | (_r(ra) << 16) | (imm & 0xFFFF)


def x_form(rt, ra, rb, xo: int, rc: int = 0) -> int:
    return (31 << 26) | (_r(rt) << 21) | (_r(ra) << 16) | (_r(rb) << 11) | (xo << 1) | rc


def branch_word(source: int, target: int, link: bool = False) -> int:
    offset = target - source
    if offset % 4 or not -0x2000000 <= offset < 0x2000000:
        raise PpcError(f'b from 0x{source:08X} to 0x{target:08X} is out of range')
    return (18 << 26) | (offset & 0x03FFFFFC) | int(link)


def bc_word(source: int, target: int, bo: int, bi: int, link: bool = False) -> int:
    offset = target - source
    if offset % 4 or not -0x8000 <= offset < 0x8000:
        raise PpcError(f'bc from 0x{source:08X} to 0x{target:08X} is out of range')
    return (16 << 26) | (bo << 21) | (bi << 16) | (offset & 0xFFFC) | int(link)


def branch_target(word: int, source: int) -> int | None:
    """Target of an I-form ``b``/``bl`` (absolute bit clear), else ``None``."""
    if word >> 26 != 18 or word & 2:
        return None
    offset = word & 0x03FFFFFC
    if offset & 0x02000000:
        offset -= 0x04000000
    return (source + offset) & 0xFFFFFFFF


# (BO, condition bit within a CR field)
CONDITIONS = {'beq': (12, 2), 'bne': (4, 2), 'blt': (12, 0), 'bge': (4, 0), 'bgt': (12, 1), 'ble': (4, 1)}


class Asm:
    def __init__(self, base: int):
        if base % 4:
            raise PpcError(f'code base 0x{base:08X} is not word-aligned')
        self.base = base
        self.items: list = []
        self.labels: dict[str, int] = {}

    @property
    def pc(self) -> int:
        return self.base + 4 * len(self.items)

    def __len__(self) -> int:
        return 4 * len(self.items)

    def label(self, name: str) -> 'Asm':
        if name in self.labels:
            raise PpcError(f'duplicate label {name!r}')
        self.labels[name] = self.pc
        return self

    def word(self, value: int) -> 'Asm':
        self.items.append(lambda pc, value=value & 0xFFFFFFFF: value)
        return self

    def words(self, values) -> 'Asm':
        for value in values:
            self.word(value)
        return self

    def _target(self, target):
        if isinstance(target, str):
            def resolve(name=target):
                if name not in self.labels:
                    raise PpcError(f'undefined label {name!r}')
                return self.labels[name]
            return resolve
        return lambda value=target: value

    # -- integer arithmetic and logic ------------------------------------
    def lis(self, rt, imm): return self.word(d_form(15, rt, 0, imm))
    def addis(self, rt, ra, imm): return self.word(d_form(15, rt, ra, imm))
    def addi(self, rt, ra, imm): return self.word(d_form(14, rt, ra, imm))
    def li(self, rt, imm): return self.addi(rt, 0, imm)
    def mulli(self, rt, ra, imm): return self.word(d_form(7, rt, ra, imm))
    def ori(self, ra, rs, imm): return self.word(d_form(24, rs, ra, imm))
    def andi_(self, ra, rs, imm): return self.word(d_form(28, rs, ra, imm))
    def add(self, rt, ra, rb): return self.word(x_form(rt, ra, rb, 266))
    def subf(self, rt, ra, rb): return self.word(x_form(rt, ra, rb, 40))  # rt = rb - ra
    def mullw(self, rt, ra, rb): return self.word(x_form(rt, ra, rb, 235))
    def divwu(self, rt, ra, rb): return self.word(x_form(rt, ra, rb, 459))
    def mr(self, ra, rs): return self.word(x_form(rs, ra, rs, 444))  # or ra,rs,rs
    def extsb(self, ra, rs): return self.word(x_form(rs, ra, 0, 954))

    def rlwinm(self, ra, rs, sh, mb, me):
        return self.word((21 << 26) | (_r(rs) << 21) | (_r(ra) << 16) | (sh << 11) | (mb << 6) | (me << 1))

    def slwi(self, ra, rs, n): return self.rlwinm(ra, rs, n, 0, 31 - n)
    def srwi(self, ra, rs, n): return self.rlwinm(ra, rs, 32 - n, n, 31)
    def clrlwi(self, ra, rs, n): return self.rlwinm(ra, rs, 0, n, 31)

    def cmpwi(self, ra, imm, cr=0): return self.word((11 << 26) | (cr << 23) | (_r(ra) << 16) | (imm & 0xFFFF))
    def cmplwi(self, ra, imm, cr=0): return self.word((10 << 26) | (cr << 23) | (_r(ra) << 16) | (imm & 0xFFFF))
    def cmpw(self, ra, rb, cr=0): return self.word((31 << 26) | (cr << 23) | (_r(ra) << 16) | (_r(rb) << 11))
    def cmplw(self, ra, rb, cr=0):
        return self.word((31 << 26) | (cr << 23) | (_r(ra) << 16) | (_r(rb) << 11) | (32 << 1))

    # -- loads and stores ------------------------------------------------
    def lbz(self, rt, d, ra): return self.word(d_form(34, rt, ra, d))
    def lhz(self, rt, d, ra): return self.word(d_form(40, rt, ra, d))
    def lha(self, rt, d, ra): return self.word(d_form(42, rt, ra, d))
    def lwz(self, rt, d, ra): return self.word(d_form(32, rt, ra, d))
    def stb(self, rs, d, ra): return self.word(d_form(38, rs, ra, d))
    def sth(self, rs, d, ra): return self.word(d_form(44, rs, ra, d))
    def stw(self, rs, d, ra): return self.word(d_form(36, rs, ra, d))
    def stwu(self, rs, d, ra): return self.word(d_form(37, rs, ra, d))
    def lbzx(self, rt, ra, rb): return self.word(x_form(rt, ra, rb, 87))
    def lhzx(self, rt, ra, rb): return self.word(x_form(rt, ra, rb, 279))
    def lwzx(self, rt, ra, rb): return self.word(x_form(rt, ra, rb, 23))
    def stbx(self, rs, ra, rb): return self.word(x_form(rs, ra, rb, 215))
    def lfs(self, ft, d, ra): return self.word(d_form(48, ft, ra, d))
    def stfs(self, fs, d, ra): return self.word(d_form(52, fs, ra, d))

    def load_addr(self, rt, address: int) -> 'Asm':
        return self.lis(rt, ha(address)).addi(rt, rt, lo(address))

    # -- special registers -----------------------------------------------
    def mflr(self, rt): return self.word(x_form(rt, 0, 0, 339) | (0x100 << 11))
    def mtlr(self, rs): return self.word(x_form(rs, 0, 0, 467) | (0x100 << 11))
    def mtctr(self, rs): return self.word(x_form(rs, 0, 0, 467) | (0x120 << 11))

    # -- control flow ----------------------------------------------------
    def nop(self): return self.word(0x60000000)
    def blr(self): return self.word(0x4E800020)
    def bctrl(self): return self.word(0x4E800421)

    def b(self, target, link: bool = False) -> 'Asm':
        resolve = self._target(target)
        self.items.append(lambda pc: branch_word(pc, resolve(), link))
        return self

    def bl(self, target) -> 'Asm':
        return self.b(target, link=True)

    def bc(self, condition: str, target, cr: int = 0) -> 'Asm':
        if condition not in CONDITIONS:
            raise PpcError(f'unknown condition {condition!r}')
        bo, bit = CONDITIONS[condition]
        resolve = self._target(target)
        self.items.append(lambda pc: bc_word(pc, resolve(), bo, bit + 4 * cr))
        return self

    def beq(self, target, cr=0): return self.bc('beq', target, cr)
    def bne(self, target, cr=0): return self.bc('bne', target, cr)
    def blt(self, target, cr=0): return self.bc('blt', target, cr)
    def bge(self, target, cr=0): return self.bc('bge', target, cr)
    def bgt(self, target, cr=0): return self.bc('bgt', target, cr)
    def ble(self, target, cr=0): return self.bc('ble', target, cr)

    # -- output ----------------------------------------------------------
    def assemble(self) -> bytes:
        return b''.join(struct.pack('>I', item(self.base + 4 * i) & 0xFFFFFFFF)
                        for i, item in enumerate(self.items))

    def assemble_word(self) -> int:
        code = self.assemble()
        if len(code) != 4:
            raise PpcError(f'expected one instruction, got {len(code) // 4}')
        return struct.unpack('>I', code)[0]


def one(address: int, build) -> int:
    """Encode one instruction at ``address``: ``one(0x80001000, lambda a: a.bl(0x80002000))``."""
    a = Asm(address)
    build(a)
    return a.assemble_word()
