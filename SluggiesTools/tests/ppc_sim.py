"""A small PowerPC interpreter for tests of injected stubs: integer and single-precision float instructions, a
sparse big-endian memory and Python stand-ins for the game's functions."""

import struct

from SluggiesTools.Dol import ppc

M32 = 0xFFFFFFFF


def s32(v: int) -> int:
    return v - (1 << 32) if v & 0x80000000 else v


def f32(v: float) -> float:
    """``v`` rounded to single precision."""
    return struct.unpack('>f', struct.pack('>f', v))[0]


class Sim:
    """``calls``: {address: fn(sim)}; a branch there runs ``fn`` and returns to LR. ``run`` executes until the
    program counter is one of ``stops`` and returns it."""

    def __init__(self):
        self.mem: dict[int, int] = {}
        self.r = [0] * 32
        self.f = [0.0] * 32
        self.lr = self.ctr = self.steps = 0
        self.cr = [0] * 8                    # per field: the last compare, as -1 / 0 / 1
        self.calls: dict = {}

    # -- memory --------------------------------------------------------------------------------------------------
    def load(self, at: int, blob: bytes) -> None:
        self.mem.update({at + i: b for i, b in enumerate(blob)})

    def read(self, at: int, n: int) -> bytes:
        return bytes(self.mem.get((at + i) & M32, 0) for i in range(n))

    def u8(self, at): return self.mem.get(at & M32, 0)
    def u16(self, at): return int.from_bytes(self.read(at, 2), 'big')
    def s16(self, at): return ppc.signed16(self.u16(at))
    def u32(self, at): return int.from_bytes(self.read(at, 4), 'big')
    def w8(self, at, v): self.mem[at & M32] = v & 0xFF
    def w16(self, at, v): self.load(at & M32, (v & 0xFFFF).to_bytes(2, 'big'))
    def w32(self, at, v): self.load(at & M32, (v & M32).to_bytes(4, 'big'))
    def float(self, at): return struct.unpack('>f', self.read(at, 4))[0]
    def wfloat(self, at, v): self.load(at & M32, struct.pack('>f', v))

    # -- execution -----------------------------------------------------------------------------------------------
    def run(self, pc: int, stops, limit: int = 5_000_000) -> int:
        stops = set(stops)
        for _ in range(limit):
            if pc in stops:
                return pc
            if pc in self.calls:
                self.calls[pc](self)
                pc = self.lr
                continue
            pc = self.step(self.u32(pc), pc)
            self.steps += 1
        raise AssertionError('the code did not reach a stop')

    def _compare(self, field: int, a: int, b: int) -> None:
        self.cr[field] = (a > b) - (a < b)

    def _ea(self, a: int, imm: int) -> int:
        return ((self.r[a] if a else 0) + imm) & M32

    def step(self, w: int, pc: int) -> int:
        r, op = self.r, w >> 26
        d, a, b, imm = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, ppc.signed16(w)
        if op in (14, 15):
            r[d] = self._ea(a, imm << 16 if op == 15 else imm)
        elif op == 7:
            r[d] = (s32(r[a]) * imm) & M32
        elif op == 11:
            self._compare(d >> 2, s32(r[a]), imm)
        elif op == 10:
            self._compare(d >> 2, r[a], w & 0xFFFF)
        elif op == 24:
            r[a] = r[d] | (w & 0xFFFF)
        elif op == 27:
            r[a] = r[d] ^ ((w & 0xFFFF) << 16)
        elif op == 28:
            r[a] = r[d] & (w & 0xFFFF)
            self._compare(0, s32(r[a]), 0)
        elif op in (32, 34, 35, 40, 42):
            at = self._ea(a, imm)
            r[d] = {32: self.u32, 34: self.u8, 35: self.u8, 40: self.u16}.get(op, lambda x: self.s16(x) & M32)(at)
            if op == 35:
                r[a] = at
        elif op in (36, 37, 38, 39, 44):
            at = (r[a] + imm) & M32 if op in (37, 39) else self._ea(a, imm)
            {36: self.w32, 37: self.w32, 44: self.w16}.get(op, self.w8)(at, r[d])
            if op in (37, 39):
                r[a] = at
        elif op in (46, 47):
            at = self._ea(a, imm)
            for i, reg in enumerate(range(d, 32)):
                if op == 46:
                    r[reg] = self.u32(at + 4 * i)
                else:
                    self.w32(at + 4 * i, r[reg])
        elif op == 21:
            sh, mb, me = b, (w >> 6) & 31, (w >> 1) & 31
            value = ((r[d] << sh) | (r[d] >> (32 - sh))) & M32 if sh else r[d]
            bits = range(mb, me + 1) if mb <= me else [*range(0, me + 1), *range(mb, 32)]
            r[a] = value & sum(1 << (31 - i) for i in bits)
        elif op == 18:
            if w & 1:
                self.lr = pc + 4
            return ppc.branch_target(w, pc)
        elif op == 16:
            target = pc + ppc.signed16(w & 0xFFFC)
            if w & 1:
                self.lr = pc + 4
            if d == 16:
                self.ctr = (self.ctr - 1) & M32
                return target if self.ctr else pc + 4
            if d not in (4, 12):
                raise AssertionError(f'branch {w:08X} at 0x{pc:08X}')
            state = self.cr[a >> 2]
            bit = (state < 0, state > 0, state == 0)[a & 3]
            return target if bit == (d == 12) else pc + 4
        elif op == 19:
            xo = (w >> 1) & 0x3FF
            if xo == 16 and d == 20:
                return self.lr
            if xo == 528 and d == 20:
                if w & 1:
                    self.lr = pc + 4
                return self.ctr
            if xo != 150:
                raise AssertionError(f'instruction {w:08X} at 0x{pc:08X}')
        elif op == 31:
            self._x(w, pc, d, a, b)
        elif op in (48, 50, 52, 54):
            at = self._ea(a, imm)
            if op == 48:
                self.f[d] = self.float(at)
            elif op == 50:
                self.f[d] = struct.unpack('>d', self.read(at, 8))[0]
            elif op == 52:
                self.wfloat(at, self.f[d])
            else:
                self.load(at, struct.pack('>d', self.f[d]))
        elif op in (59, 63):
            self._float(w, pc, op, d, a, b)
        else:
            raise AssertionError(f'instruction {w:08X} at 0x{pc:08X}')
        return pc + 4

    def _float(self, w: int, pc: int, op: int, d: int, a: int, b: int) -> None:
        f, c = self.f, (w >> 6) & 31
        if op == 59:
            xo = (w >> 1) & 31
            if xo not in (18, 20, 21, 25, 29, 30):
                raise AssertionError(f'instruction {w:08X} at 0x{pc:08X}')
            f[d] = f32({18: lambda: f[a] / f[b], 20: lambda: f[a] - f[b], 21: lambda: f[a] + f[b],
                        25: lambda: f[a] * f[c], 29: lambda: f[a] * f[c] + f[b],
                        30: lambda: f[b] - f[a] * f[c]}[xo]())
            return
        if (w >> 1) & 31 == 29:                          # fmadd, double precision
            f[d] = f[a] * f[c] + f[b]
            return
        xo = (w >> 1) & 0x3FF
        if xo == 32:
            self._compare(d >> 2, f[a], f[b])
        elif xo == 72:
            f[d] = f[b]
        elif xo == 40:
            f[d] = -f[b]
        elif xo == 264:
            f[d] = abs(f[b])
        elif xo == 15:                                   # the integer in the low word, as the register's bits
            f[d] = struct.unpack('>d', struct.pack('>q', max(-1 << 31, min((1 << 31) - 1, int(f[b]))) & M32))[0]
        else:
            raise AssertionError(f'instruction {w:08X} at 0x{pc:08X}')

    def _x(self, w: int, pc: int, d: int, a: int, b: int) -> None:
        r, xo = self.r, (w >> 1) & 0x3FF
        index = ((r[a] if a else 0) + r[b]) & M32
        if xo == 0:
            self._compare(d >> 2, s32(r[a]), s32(r[b]))
        elif xo == 32:
            self._compare(d >> 2, r[a], r[b])
        elif xo == 40:
            r[d] = (r[b] - r[a]) & M32
        elif xo == 266:
            r[d] = (r[a] + r[b]) & M32
        elif xo == 235:
            r[d] = (s32(r[a]) * s32(r[b])) & M32
        elif xo == 459:
            r[d] = r[a] // r[b] if r[b] else 0
        elif xo == 444:
            r[a] = r[d] | r[b]
        elif xo == 316:
            r[a] = r[d] ^ r[b]
        elif xo == 954:
            r[a] = ((r[d] & 0xFF) ^ 0x80) - 0x80 & M32
        elif xo == 87:
            r[d] = self.u8(index)
        elif xo == 23:
            r[d] = self.u32(index)
        elif xo == 663:
            self.wfloat(index, self.f[d])
        elif xo == 535:
            self.f[d] = self.float(index)
        elif xo == 215:
            self.w8(index, r[d])
        elif xo == 151:
            self.w32(index, r[d])
        elif xo in (339, 467):
            spr = (w >> 11) & 0x3FF
            name = {0x100: 'lr', 0x120: 'ctr'}.get(spr)
            if name is None:
                raise AssertionError(f'special register in {w:08X} at 0x{pc:08X}')
            if xo == 339:
                r[d] = getattr(self, name)
            else:
                setattr(self, name, r[d])
        elif xo not in (54, 598, 982):                   # cache and sync instructions do nothing here
            raise AssertionError(f'instruction {w:08X} at 0x{pc:08X}')
