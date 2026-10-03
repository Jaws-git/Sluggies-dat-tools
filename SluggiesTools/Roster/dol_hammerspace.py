"""DOL hammerspace: a text and a data section above the boot stacks, with the MEM1 arena moved behind them.

Layout (US ``main.dol``; P2/P10 of the character-expansion plan, external tool):

  0x807A4E80-0x807B4E80  main-thread stack (bss end .. stack top)
  0x807B4E80-0x807B6E80  debugger stack; FUN_80612d80 also uses 0x807B6E80 as a stack top,
                         so its frame writes land just above it
  0x807B7000-TEXT_LIMIT  our text section (hook stubs)
  DATA_BASE-             our data section (tables), up to ARENA_LO_MAX
  arena low              end of the data section; OSInit takes it from four lis/addi pairs

The game makes a fixed 15 MB MEM1 heap at arena low + 0x4006C, which must end
below arena high, so arena low may rise to at most ``ARENA_LO_MAX`` (measured
by the external tool in six RAM dumps).

Each section starts with a 0x20-byte marker: a 16-byte magic, then the
section's used length (u32), so a later step can continue allocating where
the last one stopped. Both sections are always the last bytes of the file;
``commit`` rewrites them and the arena constants together.
"""

import struct

try:
    from ..Dol import dolfile, inventory
    from ..Dol.ppc import ha, lo, signed16
    from . import steps
except ImportError:
    from Dol import dolfile, inventory
    from Dol.ppc import ha, lo, signed16
    import steps

TEXT_BASE = 0x807B7000
TEXT_LIMIT = 0x807C0000
DATA_BASE = TEXT_LIMIT
ARENA_LO_MAX = 0x808BF000
MARKER_SIZE = 0x20
TEXT_MAGIC = b'SLUGGIES ROSTER\x01'
DATA_MAGIC = b'SLUGGIES ROSTER\x02'
STOCK_ARENA_VALUES = (0x807B6E80, 0x807B4E80)


class HammerspaceError(dolfile.DolError):
    pass


def _marker(magic: bytes, used: int) -> bytes:
    return magic + struct.pack('>I', used) + bytes(MARKER_SIZE - len(magic) - 4)


def arena_pairs() -> list[tuple[int, int]]:
    """The four OSInit lis/addi pairs that set the MEM1 arena start (inventory group ``arena_lo``)."""
    sites = [s.address for s in inventory.group('arena_lo')]
    return list(zip(sites[0::2], sites[1::2]))


def arena_values(image: dolfile.DolImage) -> list[int]:
    out = []
    for lis_site, addi_site in arena_pairs():
        lis_word, addi_word = image.u32(lis_site), image.u32(addi_site)
        if lis_word >> 26 != 15 or addi_word >> 26 != 14:
            raise HammerspaceError(f'arena pair 0x{lis_site:08X}/0x{addi_site:08X} is not lis/addi')
        out.append(((lis_word & 0xFFFF) << 16) + signed16(addi_word) & 0xFFFFFFFF)
    return out


def set_arena_low(image: dolfile.DolImage, value: int) -> None:
    if value > ARENA_LO_MAX:
        raise HammerspaceError(f'arena low 0x{value:08X} is above 0x{ARENA_LO_MAX:08X}: the game\'s 15 MB MEM1 '
                               'heap would run past arena high')
    for lis_site, addi_site in arena_pairs():
        image.write_word(lis_site, (image.u32(lis_site) & 0xFFFF0000) | ha(value))
        image.write_word(addi_site, (image.u32(addi_site) & 0xFFFF0000) | lo(value))


class DolHammerspace:
    """Our two sections in ``image``: allocate with ``code.put`` / ``data.put``, then ``commit``."""

    def __init__(self, image: dolfile.DolImage, code: dolfile.Space, data: dolfile.Space):
        self.image, self.code, self.data = image, code, data

    @staticmethod
    def _load(image: dolfile.DolImage, base: int, magic: bytes) -> bytes | None:
        slot = image.section_at(base)
        if slot is None:
            return None
        blob = image.read(base, slot.size)
        if blob[:len(magic)] != magic:
            raise HammerspaceError(f'the section at 0x{base:08X} is not a roster section')
        used = struct.unpack_from('>I', blob, len(magic))[0]
        if not MARKER_SIZE <= used <= slot.size:
            raise HammerspaceError(f'roster section at 0x{base:08X}: bad used length 0x{used:X}')
        return blob[:used]

    @classmethod
    def open(cls, image: dolfile.DolImage) -> 'DolHammerspace | None':
        text = cls._load(image, TEXT_BASE, TEXT_MAGIC)
        data = cls._load(image, DATA_BASE, DATA_MAGIC)
        if text is None and data is None:
            return None
        if text is None or data is None:
            raise HammerspaceError('only one of the two roster sections is present')
        return cls(image, dolfile.Space(TEXT_BASE, TEXT_LIMIT, text), dolfile.Space(DATA_BASE, ARENA_LO_MAX, data))

    @classmethod
    def create(cls, image: dolfile.DolImage) -> 'DolHammerspace':
        if cls.open(image) is not None:
            raise HammerspaceError('the roster sections already exist')
        values = arena_values(image)
        if any(v not in STOCK_ARENA_VALUES for v in values):
            raise HammerspaceError('arena start is not stock (' + ', '.join(f'0x{v:08X}' for v in values) +
                                   '); another DOL extension is installed')
        for slot in image.header.used_slots:
            if slot.end > TEXT_BASE:
                raise HammerspaceError(f'{slot.name} already loads at 0x{slot.address:08X}, above the boot stacks')
        hs = cls(image, dolfile.Space(TEXT_BASE, TEXT_LIMIT, _marker(TEXT_MAGIC, MARKER_SIZE)),
                 dolfile.Space(DATA_BASE, ARENA_LO_MAX, _marker(DATA_MAGIC, MARKER_SIZE)))
        hs.commit()
        return hs

    @classmethod
    def open_or_create(cls, image: dolfile.DolImage) -> 'DolHammerspace':
        return cls.open(image) or cls.create(image)

    @property
    def arena_low(self) -> int:
        return dolfile.align_up(self.data.here)

    def commit(self) -> None:
        """Write both sections (current contents) and point the arena start behind them."""
        if self.arena_low > ARENA_LO_MAX:
            raise HammerspaceError(f'data section would end at 0x{self.data.here:08X}, past 0x{ARENA_LO_MAX:08X}')
        self.code.write(TEXT_BASE, _marker(TEXT_MAGIC, len(self.code.blob)))
        self.data.write(DATA_BASE, _marker(DATA_MAGIC, len(self.data.blob)))
        # Both sections are the file's tail: drop them (last first, so the file shrinks) and append anew.
        old = [s for s in (self.image.section_at(TEXT_BASE), self.image.section_at(DATA_BASE)) if s is not None]
        for slot in sorted(old, key=lambda s: s.file_offset, reverse=True):
            self.image.remove_section(slot)
        self.image.add_section('text', TEXT_BASE, bytes(self.code.blob))
        self.image.add_section('data', DATA_BASE, bytes(self.data.blob))
        set_arena_low(self.image, self.arena_low)

    def write(self, address: int, blob: bytes) -> None:
        """Write to the DOL. Bytes inside our sections also go to their allocator buffer, which ``commit``
        writes back: a plain image write there would be lost on the next commit."""
        self.image.write(address, blob)
        for space in (self.code, self.data):
            if space.base <= address and address + len(blob) <= space.here:
                space.write(address, blob)

    def summary(self) -> str:
        return (f'text 0x{TEXT_BASE:08X}-0x{self.code.here:08X} (0x{len(self.code.blob):X} of '
                f'0x{TEXT_LIMIT - TEXT_BASE:X}), data 0x{DATA_BASE:08X}-0x{self.data.here:08X}, '
                f'arena low 0x{self.arena_low:08X} (limit 0x{ARENA_LO_MAX:08X})')


def get(ctx: steps.RosterContext) -> DolHammerspace:
    """The run's DOL hammerspace (opened, or created by the Phase 1 step)."""
    hs = ctx.state.get('hammerspace')
    if hs is None:
        hs = DolHammerspace.open_or_create(ctx.dol)
        ctx.state['hammerspace'] = hs
    return hs


@steps.register('dol_hammerspace')
def apply(ctx: steps.RosterContext) -> list[str]:
    hs = get(ctx)
    hs.commit()
    return [f'DOL hammerspace: {hs.summary()}']
