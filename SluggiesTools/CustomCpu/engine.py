"""The custom CPU level in ``main.dol``: match-start hook, popup stubs and the settings record.

Ported from the Slugout 0.8 patcher's level and menu code, reduced to one level and one button, and with
snapshot swaps instead of build-time stock bytes. Reference: ``_docs/_docs_custom_cpu/CustomCpuLevel.md``; tests:
``tests/test_custom_cpu.py``.

**Level.** The popup's 5th button stores match settings +0x10 = 4, a value the game never writes. The CPU
manager's constructor (FUN_800C364C) reads the level with ``lwz r6,0x10(r7)`` at ``HOOK`` and copies it to both
sides, so CPU vs CPU plays it on both. ``HOOK`` becomes ``bl`` to the manager stub (LR is saved before the
site; r0, r3, r4, r5, r7 are live and kept, the stub uses r6, r8-r12, ctr and cr0): level 4 -> level 0 (the
game's level 4) with ``FLAG`` = 1, any other level -> ``FLAG`` = 0. The tier table's entry 4 (``TIER``) is set
to 3, the hardest, for code that reads the settings again.

**Swaps.** When ``FLAG`` differs from the ``SWAPPED`` byte the stub walks the swap table: at a custom match's
start it saves the live bytes of every record into the record's backup and writes the custom bytes; at any
other match's start after one it writes the backups back. Levels 1-4 therefore run on whatever the DOL holds
(other tools' edits included), and the custom level on its settings alone. Each record gets ``dcbst`` / ``sync``
/ ``icbi`` / ``isync``, for the two code words among them (the fielding group's dive gates).

**Popup.** Dialog 0x71 ("CPU Level", table entry ``ENTRY``) uses 4 of its 6 slots. The exhibition's open call
(``OPEN_SITE``) goes through a stub that fills the 5th slot with {``CHOICE``, our message}, opens the popup
and empties the slot again, so the other openers of dialog 0x71 (minigames) keep 4 buttons. The exhibition's
answer (``ANSWER_SITE``, ids >= 0x16 leave the level alone) goes through a stub that stores level 4 for
``CHOICE``. A button's picture is resource row choice + 0x22 (``menu_files``).

**Behaviours.** The behaviour switches that are on have their code in the block too (``behaviours.build``) and
their hook sites branch to it; a site whose switch is off holds the game's word.

**Where it lives.** Everything is one block, the contents of a DOL section of its own behind the DOL
hammerspace's data section (``dol_hammerspace.DolHammerspace.set_cpu``): header, the three stubs, the behaviour
code, the swap table with its backups, and the settings record (zlib JSON), so the DOL describes its own custom
level. The roster's data section holds nothing of it: "data past the marker" is how the tools tell a roster
DOL. The block is built for its address, which follows the data section's end: every deploy and every roster
run builds it anew. ``FLAG`` and ``SWAPPED`` are fixed bytes in the text section's marker
(``dol_hammerspace.CUSTOM_CPU_FLAG``), zero at boot, so code of other features can test them. A block in the
text section (where an earlier version put it) is still read, and moved by the next deploy.
"""

import json
import struct
import zlib
from dataclasses import dataclass, field

try:
    from ..Dol import dolfile
    from ..Dol.ppc import Asm, branch_target, ha, lo, one
    from ..Roster import dol_hammerspace
    from . import behaviours, settings
except ImportError:
    from Dol import dolfile
    from Dol.ppc import Asm, branch_target, ha, lo, one
    import dol_hammerspace
    from CustomCpu import behaviours, settings

FLAG = dol_hammerspace.CUSTOM_CPU_FLAG
SWAPPED = FLAG + 1

HOOK, HOOK_STOCK = 0x800C3678, 0x80C70010               # lwz r6,0x10(r7)
LEVEL_OFF = 0x10                                         # the CPU level in the match settings
CUSTOM_LEVEL = 4
TIER, TIER_STOCK, TIER_NEW = 0x8063FB88, 0, 3            # entry 4 of the level -> tier table (3,2,1,0,0)

CHOICE = 0x46                                            # no dialog's choice id
ROW_BASE = 0x22
ART_ROW = CHOICE + ROW_BASE                              # 0x68: the button's picture row
MESSAGE = 0x124                                          # our caption: the popup table's stock message count
DIALOG_TABLE, DIALOG_FIRST, DIALOG_SIZE, DIALOG = 0x806D2D30, 0x5E, 0x38, 0x71
ENTRY = DIALOG_TABLE + (DIALOG - DIALOG_FIRST) * DIALOG_SIZE        # 0x806D3158
SLOT5 = ENTRY + 8 + 4 * 8                                           # 0x806D3180
ENTRY_STOCK = (3, 0x2E, 0x12, 0xAE, 0x13, 0xAF, 0x14, 0xB0, 0x15, 0xB1, -1, 0, -1, 0)
POPUP_OPEN = 0x80471864
OPEN_DIALOG = (0x802CCC20, 0x38800071)                   # li r4,0x71
OPEN_SITE, OPEN_STOCK = 0x802CCC30, 0x481A4C35           # bl POPUP_OPEN
ANSWER_SITE, ANSWER_STOCK = 0x802CCCA8, 0x40840044       # bge cr1,ANSWER_DONE
ANSWER_LOW, ANSWER_DONE = 0x802CCCAC, 0x802CCCEC
SETTINGS_R13 = -0xB00
STOCK_STORES = {0x802CCCB0: 3, 0x802CCCC0: 2, 0x802CCCD0: 1, 0x802CCCE0: 0}

MAGIC = b'SLGCPU\x00\x01'
HEADER = 0x20                    # magic, u32 capacity, u32 record offset, u32 record length, 3 x u32 stub offset
CAPACITY = 0x1000
RECORD_VERSION = 1
MAX_RUN = 32                     # a record is flushed at its first and last byte: one or two cache lines
DEFAULT_BUTTON = 'Custom Level'


class CustomCpuError(dolfile.DolError):
    pass


@dataclass
class Record:
    """What the DOL stores of the custom level: the button's caption and the settings."""
    button: str = DEFAULT_BUTTON
    edits: dict = field(default_factory=dict)            # {key: value} (settings.py)
    extra: dict = field(default_factory=dict)            # file keys this version does not know, kept
    name: str = ''                                       # the settings' own name (a loaded file's)

    def encode(self) -> bytes:
        doc = {'version': RECORD_VERSION, 'button': self.button, 'name': self.name, 'settings': self.edits,
               'extra': self.extra}
        return zlib.compress(json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), 9)

    @classmethod
    def decode(cls, blob: bytes) -> 'Record':
        try:
            doc = json.loads(zlib.decompress(blob).decode('utf-8'))
            return cls(str(doc['button']), dict(doc['settings']), dict(doc.get('extra') or {}),
                       str(doc.get('name') or ''))
        except (zlib.error, ValueError, KeyError, TypeError) as exc:
            raise CustomCpuError(f'the custom CPU settings stored in main.dol cannot be read ({exc})') from exc


# -- code ----------------------------------------------------------------------------------------------------------

def swap_table(records: list[tuple[int, bytes]]) -> bytes:
    """``u32 address, u32 length, custom bytes, backup`` per record (both padded to 4), then a zero word."""
    out = bytearray()
    for at, custom in records:
        if not 0 < len(custom) <= MAX_RUN:
            raise CustomCpuError(f'swap record 0x{at:08X}: {len(custom)} bytes (1..{MAX_RUN})')
        pad = bytes(-len(custom) % 4)
        out += struct.pack('>II', at, len(custom)) + custom + pad + bytes(len(custom)) + pad
    return bytes(out) + bytes(4)


def manager_stub(base: int, table: int) -> Asm:
    a = Asm(base)
    a.lwz(6, LEVEL_OFF, 7)
    a.lis(9, ha(FLAG)).addi(9, 9, lo(FLAG))
    a.li(8, 0).cmpwi(6, CUSTOM_LEVEL).bne('flag')
    a.li(8, 1)
    a.label('flag')
    a.stb(8, 0, 9)
    a.lbz(10, SWAPPED - FLAG, 9).cmpw(8, 10).beq('done')         # the tables already are what this match needs
    a.stb(8, SWAPPED - FLAG, 9)
    a.lis(10, ha(table)).addi(10, 10, lo(table))
    a.label('record')                                            # r10 = the record, r8 = 1 swap in / 0 restore
    a.lwz(11, 0, 10).cmpwi(11, 0).beq('done')
    a.lwz(12, 4, 10).mtctr(12)
    a.addi(9, 12, 3).rlwinm(9, 9, 0, 0, 29).add(9, 10, 9).addi(9, 9, 7)      # r9 = the backup - 1
    a.cmpwi(8, 0).beq('restore')
    a.mr(12, 9).addi(9, 11, -1)                                  # live -> backup
    a.label('save').lbzu(6, 1, 9).stbu(6, 1, 12).bdnz('save')
    a.lwz(12, 4, 10).mtctr(12)
    a.addi(9, 10, 7)                                             # then custom -> live
    a.label('restore')                                           # (restore: backup -> live)
    a.addi(12, 11, -1)
    a.label('copy').lbzu(6, 1, 9).stbu(6, 1, 12).bdnz('copy')
    a.dcbst(0, 11).dcbst(0, 12).sync().icbi(0, 11).icbi(0, 12).isync()       # r11 / r12: first / last byte
    a.lwz(12, 4, 10).addi(12, 12, 3).rlwinm(12, 12, 0, 0, 29)
    a.add(10, 10, 12).add(10, 10, 12).addi(10, 10, 8)
    a.b('record')
    a.label('done')
    a.lwz(6, LEVEL_OFF, 7)                                       # the site's own load
    a.cmpwi(8, 0).beq('out')
    a.li(6, 0)                                                   # the custom level plays level 4 underneath
    a.label('out')
    a.blr()
    return a


def _slot_halves() -> tuple[int, int, int]:
    hi = ha(SLOT5)
    return hi, SLOT5 - (hi << 16), SLOT5 + 4 - (hi << 16)


def open_stub(base: int) -> Asm:
    """Around the exhibition's popup open: the 5th slot filled, then emptied. r11 / r12 only (the call's
    arguments stay); r0 is free at a call."""
    hi, lo0, lo1 = _slot_halves()
    a = Asm(base)
    a.stwu(1, -0x10, 1).mflr(0).stw(0, 0x14, 1)
    a.lis(12, hi).li(11, CHOICE).stw(11, lo0, 12).li(11, MESSAGE).stw(11, lo1, 12)
    a.bl(POPUP_OPEN)
    a.lis(12, hi).li(11, -1).stw(11, lo0, 12).li(11, 0).stw(11, lo1, 12)
    a.lwz(0, 0x14, 1).mtlr(0).addi(1, 1, 0x10).blr()
    return a


def answer_stub(base: int) -> Asm:
    """In place of ``bge cr1,ANSWER_DONE`` (cr1: the answer r0 against 0x16). r0 and r4 are loaded again at
    ``ANSWER_DONE``."""
    a = Asm(base)
    a.blt('low', cr=1)
    a.cmpwi(0, CHOICE, cr=1).bne('out', cr=1)
    a.lwz(4, SETTINGS_R13, 13).li(0, CUSTOM_LEVEL).stw(0, LEVEL_OFF, 4)
    a.label('out')
    a.b(ANSWER_DONE)
    a.label('low')
    a.b(ANSWER_LOW)
    return a


@dataclass
class Block:
    blob: bytes
    manager: int
    open: int
    answer: int
    hooks: dict = field(default_factory=dict)            # {site: word} of the behaviours in the block


def build_block(base: int, record: bytes, swaps: list[tuple[int, bytes]], capacity: int | None = None,
                edits: dict | None = None, image: dolfile.DolImage | None = None) -> Block:
    """The block's bytes for load address ``base``. ``capacity`` None: ``CAPACITY``, or what it needs.
    ``edits``: the settings, for the behaviour code of the switches that are on; ``image``: the DOL the block
    is for (``behaviours.build``)."""
    table = swap_table(swaps)
    at = base + HEADER
    code = b''
    starts = []
    for build in (None, open_stub, answer_stub):
        starts.append(at + len(code))
        if build is None:               # the manager stub needs the table's address: placed after the code
            size = len(manager_stub(at, at).assemble())
            code += bytes(size)
        else:
            code += build(at + len(code)).assemble()
    extra, hooks = behaviours.build(at + len(code), edits or {}, image)
    code += extra
    table_at = at + len(code)
    code = manager_stub(at, table_at).assemble() + code[starts[1] - at:]
    body = code + table
    record_at = HEADER + len(body)
    used = record_at + len(record)
    capacity = capacity or max(CAPACITY, dolfile.align_up(used, 0x400))
    if used > capacity:
        raise CustomCpuError(f'the custom CPU block needs 0x{used:X} bytes, its space holds 0x{capacity:X}')
    header = MAGIC + struct.pack('>6I', capacity, record_at, len(record), *(s - base for s in starts))
    blob = header + body + record
    return Block(blob + bytes(capacity - len(blob)), *starts, hooks)


# -- reading -------------------------------------------------------------------------------------------------------

def _in_text(address: int | None) -> bool:
    """In the hammerspace text section (an earlier version's block) or where the custom CPU section goes."""
    return address is not None and dol_hammerspace.TEXT_BASE <= address < dol_hammerspace.ARENA_LO_MAX


def _legacy(base: int) -> bool:
    return base < dol_hammerspace.TEXT_LIMIT


def block_address(image: dolfile.DolImage) -> int | None:
    """Where the block is, from the manager hook; None when the custom level is off."""
    if not image.is_mapped(HOOK, 4):
        return None
    target = branch_target(image.u32(HOOK), HOOK)
    if not _in_text(target):
        return None
    base = target - HEADER
    if not image.is_mapped(base, HEADER) or image.read(base, len(MAGIC)) != MAGIC:
        return None
    return base


def _header(image: dolfile.DolImage, base: int) -> tuple[int, ...]:
    return struct.unpack('>6I', image.read(base + len(MAGIC), HEADER - len(MAGIC)))


def is_on(image: dolfile.DolImage) -> bool:
    return block_address(image) is not None


def detect(image: dolfile.DolImage) -> Record | None:
    """The custom level's record, or None when it is off."""
    base = block_address(image)
    if base is None:
        return None
    _capacity, record_at, length, *_stubs = _header(image, base)
    if not image.is_mapped(base + record_at, length):
        raise CustomCpuError('the custom CPU block in main.dol is damaged (its settings lie outside the section)')
    return Record.decode(image.read(base + record_at, length))


def outfield_dive_on(image: dolfile.DolImage) -> bool:
    """The global "outfield dive" game option's word is in ``image``."""
    return image.read(settings.GRID_DIVE_GATE, 4) == settings.OUTFIELD_DIVE_WORD


# -- writing -------------------------------------------------------------------------------------------------------

def _store_words(value: int) -> bytes:
    return Asm(0).lwz(4, SETTINGS_R13, 13).li(0, value).stw(0, LEVEL_OFF, 4).assemble()


def _span(image: dolfile.DolImage) -> tuple[int, int] | None:
    """The block's address range, None when the custom level is off."""
    base = block_address(image)
    return None if base is None else (base, base + _header(image, base)[0])


def _into(image: dolfile.DolImage, site: int, span) -> bool:
    """``site`` holds a branch into ``span``."""
    if span is None or not image.is_mapped(site, 4):
        return False
    target = branch_target(image.u32(site), site)
    return target is not None and span[0] <= target < span[1]


def hooked(image: dolfile.DolImage) -> list[str]:
    """The behaviour switches whose sites branch into the block."""
    span = _span(image)
    return [b.feature for b in behaviours.BEHAVIOURS if all(_into(image, site, span) for site, _w in b.hooks)]


def _check_behaviours(image: dolfile.DolImage, edits: dict) -> None:
    span = _span(image)
    for b in behaviours.enabled(edits):
        label = settings.BY_KEY[b.feature].label
        for site, stock in b.hooks + b.relies:
            if not image.is_mapped(site, 4):
                raise CustomCpuError(f'custom CPU level: 0x{site:08X} ("{label}") is not in this main.dol')
            got = image.u32(site)
            if got != stock and not ((site, stock) in b.hooks and _into(image, site, span)):
                raise CustomCpuError(f'custom CPU level: 0x{site:08X} ("{label}") holds 0x{got:08X}, expected the '
                                     f'stock 0x{stock:08X}; another patch changed it. Turn "{label}" off to '
                                     'deploy without it')


def _unhook_behaviours(image: dolfile.DolImage, span) -> None:
    for site, stock in behaviours.SITES.items():
        if _into(image, site, span):
            image.write_word(site, stock)


def check_stock(image: dolfile.DolImage, edits: dict | None = None) -> None:
    """Every word the hooks rely on is stock, or ours already. ``edits``: the behaviour switches to check
    the sites of."""
    _check_behaviours(image, edits or {})
    def word(site, stock, what, ours_ok=True):
        if not image.is_mapped(site, 4):
            raise CustomCpuError(f'custom CPU level: 0x{site:08X} is not in this main.dol')
        got = image.u32(site)
        if got != stock and not (ours_ok and _in_text(branch_target(got, site))):
            raise CustomCpuError(f'custom CPU level: 0x{site:08X} ({what}) holds 0x{got:08X}, expected the stock '
                                 f'0x{stock:08X}; another patch changed it')
    word(HOOK, HOOK_STOCK, 'the CPU level read')
    word(OPEN_SITE, OPEN_STOCK, 'the popup open call')
    word(ANSWER_SITE, ANSWER_STOCK, 'the popup answer')
    word(OPEN_DIALOG[0], OPEN_DIALOG[1], 'the dialog id', ours_ok=False)
    word(ANSWER_LOW, one(ANSWER_LOW, lambda a: a.b(0x802CCCE0)), 'the answer\'s low path', ours_ok=False)
    for site, value in STOCK_STORES.items():
        if image.read(site, 12) != _store_words(value):
            raise CustomCpuError(f'custom CPU level: the stock level store at 0x{site:08X} changed')
    entry = struct.unpack('>14i', image.read(ENTRY, DIALOG_SIZE))
    if entry != ENTRY_STOCK:
        raise CustomCpuError(f'custom CPU level: the CPU level dialog\'s table entry changed: {entry}')
    if image.read(TIER, 4) not in (struct.pack('>I', TIER_STOCK), struct.pack('>I', TIER_NEW)):
        raise CustomCpuError(f'custom CPU level: the tier table entry 0x{TIER:08X} is not stock')


def apply_dol(image: dolfile.DolImage, record: Record, stock_get=None) -> list[str]:
    """Turn the custom level on in ``image`` (or rewrite it) from ``record``: the block, the three hooks and
    the tier entry. ``stock_get``: the clean game's bytes (``settings.image_get`` of ``1_Input/main.dol``;
    None: the module's own copy). Needs no ``dt_na.dat``. Returns log lines."""
    problems = settings.check(record.edits, stock_get)
    if problems:
        raise CustomCpuError('custom CPU settings: ' + '; '.join(problems))
    check_stock(image, record.edits)
    swaps = settings.swap_records(record.edits, stock_get or settings.declared_get, outfield_dive_on(image))
    for at, custom in swaps:
        if not image.is_mapped(at, len(custom)):
            raise CustomCpuError(f'custom CPU level: 0x{at:08X} is not in this main.dol')
    blob = record.encode()
    hs = dol_hammerspace.DolHammerspace.open_or_create(image)
    _unhook_behaviours(image, _span(image))                  # the enabled ones are hooked again below
    _drop_legacy(image, hs)
    base = hs.cpu_base + dol_hammerspace.MARKER_SIZE
    block = build_block(base, blob, swaps, None, record.edits, image)
    hs.set_cpu(block.blob)
    where = 'its own DOL section'
    image.write_word(HOOK, one(HOOK, lambda a: a.bl(block.manager)))
    image.write_word(OPEN_SITE, one(OPEN_SITE, lambda a: a.bl(block.open)))
    image.write_word(ANSWER_SITE, one(ANSWER_SITE, lambda a: a.b(block.answer)))
    image.write_word(TIER, TIER_NEW)
    for site, word in block.hooks.items():
        image.write_word(site, word)
    hs.commit()
    log = [f'custom CPU level: on, button "{record.button}" ({where}, block at 0x{base:08X}, 0x{len(block.blob):X} '
           f'bytes; {len(swaps)} swap records, settings record 0x{len(blob):X} bytes)']
    log += [f'custom CPU setting: {line}' for line in settings.describe(record.edits)]
    on = [settings.BY_KEY[b.feature].label for b in behaviours.enabled(record.edits)]
    if on:
        log.append(f'custom CPU level: behaviour code hooked for {len(on)} switch{"es" if len(on) != 1 else ""} ('
                   + ', '.join(on) + ')')
    return log


def _drop_legacy(image: dolfile.DolImage, hs) -> None:
    """Take an earlier version's block out of the text section: cut off when it is the section's tail, else
    its magic cleared (the bytes stay until the sections are rebuilt)."""
    base = block_address(image)
    if base is None or not _legacy(base):
        return
    if base + _header(image, base)[0] == hs.code.here:
        del hs.code.blob[base - hs.code.base:]
    else:
        hs.write(base, bytes(len(MAGIC)))


def unhook(image: dolfile.DolImage) -> bool:
    """Turn the custom level off in ``image``: stock words back, its section dropped. Returns whether it was
    on."""
    base = block_address(image)
    if base is None:
        return False
    _unhook_behaviours(image, _span(image))
    image.write_word(HOOK, HOOK_STOCK)
    image.write_word(OPEN_SITE, OPEN_STOCK)
    image.write_word(ANSWER_SITE, ANSWER_STOCK)
    image.write_word(TIER, TIER_STOCK)
    hs = dol_hammerspace.DolHammerspace.open(image)
    _drop_legacy(image, hs)
    hs.set_cpu(None)
    hs.commit()
    return True
