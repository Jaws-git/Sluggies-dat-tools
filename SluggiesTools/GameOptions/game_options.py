"""Game options: small gameplay patches in ``main.dol`` (StartTools menu [8], ``start.py --game-options``).

Each option replaces stock instructions with branches to stubs in the DOL
hammerspace text section (``Roster/dol_hammerspace.py``), so it works with or
without a roster injection. Stubs read game state only through ``r13``
globals, never through fixed heap addresses: the roster expansion raises the
MEM1 arena start, so the heap addresses that stock-game Gecko codes use point
at other data in an expanded game.

Incompatible with the community Gecko codes these options port ("CPU vs CPU V2", "CPU vs CPU human
management"): with them enabled in Dolphin, CPU vs CPU does not start, even on a stock roster
(2026-10-08). They hook the same sites, so Dolphin overwrites our branches, and they use stock heap
addresses that any DOL hammerspace moves. Users must disable them (``_docs/RosterGuide.md``).

An option's on/off state is stored in the DOL itself: the option is on when
all its hook sites branch into our text section. The roster runner resets
``main.dol`` to ``1_Input`` before every injection. It calls ``detect`` before
the reset and ``apply`` afterwards, so a roster re-run keeps the options.
Turning an option off writes the stock words back. On a roster-built DOL its
stub stays behind as unused bytes until the next roster run rebuilds the
section; without a roster (sections holding only option stubs) ``remove``
rebuilds the sections from the options still on, or drops them and restores
the stock arena start when none is, so the DOL is stock again.
"""

from dataclasses import dataclass
from typing import Callable

try:
    from ..Dol import dolfile
    from ..Dol.ppc import Asm, branch_target, ha, lo, one
    from ..Roster import dol_hammerspace
except ImportError:
    from Dol import dolfile
    from Dol.ppc import Asm, branch_target, ha, lo, one
    import dol_hammerspace


class GameOptionError(dolfile.DolError):
    pass


@dataclass(frozen=True)
class Hook:
    site: int
    stock: int
    stub: Callable[[int, int], Asm]   # (stub address, site) -> the stub; it ends by branching to site + 4


@dataclass(frozen=True)
class Option:
    key: str
    title: str
    hooks: tuple[Hook, ...]
    group: str | None = None    # options of one group hook the same sites: only the one whose stub is there is on


# ---------------------------------------------------------------- CPU vs CPU

# The match settings (*(r13-0xB00)) say who controls what: +0x18..+0x1B are the teams of
# controllers 1-4, and 0xFF means the CPU / not playing. 0x80063D4C (`lbz r0,0x18(r8)`, r8 = the
# settings) runs in the 2D controller-icon objects, on the select screens. Port of the community
# Gecko code "CPU vs CPU V2" (C2 0x80063D4C): while controller 1 holds exactly BUTTONS, all four
# controllers are set to the CPU. The Gecko code read controller 1's buttons at the stock heap
# address 0x81317BB0; the stub follows the controller manager *(r13-0x2F8) instead, whose +0
# points at the 0x2C-byte controller entries. The per-frame update (0x8050BEC0) stores the held
# buttons at entry +0x14 and +0x00 (accessors 0x8045D408/+0x14 held, 0x8045D41C/+0x16 pressed,
# 0x8045D430/+0x18 repeat). It ORs four direction bits (0x000F) from an analog axis pair into the
# held word, so the stub tests that both BUTTONS bits are held instead of an exact match: an exact
# compare failed whenever that axis was off-center (Dolphin, mouse-aimed pointer, 2026-10-08).
CPU_SITE, CPU_STOCK = 0x80063D4C, 0x88080018       # lbz r0,0x18(r8)
SETTINGS_PADS = 0x18
PAD_MANAGER = -0x2F8                               # r13
BUTTONS = 0x1800                                   # A (0x0800) + Minus (0x1000)


def _cpu_vs_cpu_stub(base: int, site: int) -> Asm:
    # r5 and r10 are set by the stock code right after the site (li r5,0 / li r10,0); cr0 is
    # compared again before its next use.
    a = Asm(base)
    a.lwz(10, PAD_MANAGER, 13).lwz(10, 0, 10).lha(5, 0, 10)
    a.andi_(5, 5, BUTTONS).cmpwi(5, BUTTONS).bne('back')
    a.li(5, -1).stw(5, SETTINGS_PADS, 8)           # controllers 1-4 -> CPU
    a.label('back')
    a.word(CPU_STOCK)
    a.b(site + 4)
    return a


# ------------------------------------------------------ CPU vs CPU management

# Port of the community Gecko code "CPU vs CPU human management". While a CPU vs CPU match runs
# (settings +0x18 == 0xFF), controller 1 is handed the fielding team, so a human can pause and
# make defensive changes for it. *(r13-0x1620) is a 0x1C-byte object (vtable 0x80644474, made at
# 0x80142700) whose +4..+7 hold the team each controller manages (0xFF = none); the Gecko code
# wrote its +4 at the stock heap address 0x900D5BD0. The game object (r3 at all three sites,
# 0x900D5C2C = its +0x34 in the stock game) has +0x2A batting team, +0x2B fielding team,
# +0x30 state. Found in a stock-game Dolphin save state taken mid-match (2026-10-04).
# The stubs use r11/r12: at every site both are dead or overwritten before they are read.
TEAM_MAP = -0x1620                                 # r13
SETTINGS = -0xB00                                  # r13
GAME_DEFENSE_FLAG = 0x34
STATE_DEFENSE_CHANGE = 0x1D
INIT_SITE, INIT_STOCK = 0x8012DB7C, 0x9803002A     # stb r0,0x2A(r3)   match init: batting team
FLIP_SITE, FLIP_STOCK = 0x80137584, 0x98C3002A     # stb r6,0x2A(r3)   half-inning flip
STATE_SITE, STATE_STOCK = 0x8012DB08, 0x98830030   # stb r4,0x30(r3)   game state setter


def _if_cpu_vs_cpu(a: Asm, skip: str) -> None:
    a.lwz(12, SETTINGS, 13).lbz(11, SETTINGS_PADS, 12)
    a.cmpwi(11, 0xFF).bne(skip)


def _hand_fielding_team(batting_reg: int, stock: int):
    def stub(base: int, site: int) -> Asm:
        a = Asm(base)
        _if_cpu_vs_cpu(a, 'back')
        a.lwz(12, TEAM_MAP, 13).cmpwi(12, 0).beq('back')
        a.xori(11, batting_reg, 1).stb(11, 4, 12)    # controller 1 -> fielding team
        a.label('back')
        a.word(stock)
        a.b(site + 4)
        return a
    return stub


def _defense_change_stub(base: int, site: int) -> Asm:
    # The Gecko code cleared game +0x34 on every switch to state 0x1D; here only in CPU vs CPU,
    # so normal matches run stock code.
    a = Asm(base)
    a.cmpwi(4, STATE_DEFENSE_CHANGE).bne('back')
    _if_cpu_vs_cpu(a, 'back')
    a.li(11, 0).stb(11, GAME_DEFENSE_FLAG, 3)
    a.label('back')
    a.word(STATE_STOCK)
    a.b(site + 4)
    return a


# ------------------------------------------------------------ player memory
#
# Every player on the field (batter, 3 runners, 9 fielders) gets its own MEM expanded heap of
# 0xD4800 = 870,400 bytes, made from the game heap (heap 3) by the loop in fn_80364880 (callee
# 0x803762A0). It holds that player's High model, Low model and bat or glove; a player whose files
# don't fit crashes the game when it is loaded onto the field (Dolphin RAM dumps and a Mario texture
# series, 2026-10-10: High 761,472 + Low 77,984 + glove 26,944 worked 8/8, High 777,856 crashed 7/7
# and ran with the heap at +32 KB). The stub replaces `addi r5,r14,0x4800` (r5 = the heap size).
# Each extra KB costs 13 KB of heap 3; +48 KB is the most the 2026-10-10 budget allows on stock
# memory (worst stadium, the roster's 10 largest animation banks, a run scored, 512 KB margin).
# +64 and +128 KB go past that budget: development levels, they may crash at a run scored.
#
# Big-memory levels (Dolphin's "Emulated Memory Size Override" with MEM2 at 128 MB): the game ignores
# the extra memory, because its heap sizes are fixed. The MEM2 root heap (0x9000086C) takes its size
# from fn_8039A85C (`lis r3,0x330; blr`, 0x3300000); heap 3 is entry 3 of the heap table at
# 0x8062F048 ({u32 size, u8 parent, u8 flag} x 4: 0x120100, 0x20000, 0xA00000, 0x3000000), read by
# `lwz r4,0(r28)` at 0x803A58E8. The big levels grow both by 64 MB and give each player heap far more.
# Every stub checks the MEM2 size the OS reports (*0x8000311C, 0x04000000 stock, 0x08000000 with the
# override; the dumps of 2026-10-10) and keeps the stock heaps (player heap at +48 KB) without it, so
# a DOL with a big level still boots on a real Wii or a Dolphin without the override. With the
# override, MEM2 above 0x9330086C is empty up to the IOS area at 0x97FC0000; the grown root heap ends
# at 0x9730086C.
HEAP_SITE, HEAP_STOCK_WORD = 0x8036518C, 0x38AE4800    # addi r5,r14,0x4800
PLAYER_HEAP_STOCK = 0xD4800
PLAYER_HEAP_FIELDERS = 13
PLAYER_HEAP_LEVELS_KB = (32, 48, 64, 128)
PLAYER_HEAP_SAFE_KB = 48                                # the stock-memory ceiling (fallback of the big levels)
BIG_HEAP_LEVELS_KB = (512, 1024, 2048, 4096)
ROOT_SIZE_SITE, ROOT_SIZE_STOCK_WORD = 0x8039A85C, 0x3C600330    # lis r3,0x330
TABLE_READ_SITE, TABLE_READ_STOCK_WORD = 0x803A58E8, 0x809C0000  # lwz r4,0(r28)
HEAP_TABLE = 0x8062F048
GAME_HEAP_ENTRY = HEAP_TABLE + 3 * 8
MEM2_SIZE_GLOBAL = 0x8000311C
MEM2_BIG = 0x08000000
BIG_EXTRA = 0x04000000                                  # added to the root heap and to heap 3


def player_heap_key(extra_kb: int) -> str:
    return f'player_heap_{extra_kb}'


def big_heap_key(extra_kb: int) -> str:
    return f'player_heap_big_{extra_kb}'


def _if_big_mem2(a: Asm, skip: str) -> None:
    """Fall through when the OS reports MEM2 of 128 MB or more, else branch to ``skip`` (uses r11, r12, cr0)."""
    a.lis(12, ha(MEM2_SIZE_GLOBAL)).lwz(12, lo(MEM2_SIZE_GLOBAL), 12)
    a.lis(11, MEM2_BIG >> 16).cmplw(12, 11).blt(skip)


def _player_heap_stub(size: int, big_size: int | None = None):
    def stub(base: int, site: int) -> Asm:
        a = Asm(base)
        a.lis(5, ha(size)).addi(5, 5, lo(size))
        if big_size is not None:
            _if_big_mem2(a, 'back')
            a.lis(5, ha(big_size)).addi(5, 5, lo(big_size))
            a.label('back')
        a.b(site + 4)
        return a
    return stub


def _root_size_stub(base: int, site: int) -> Asm:
    # fn_8039A85C is a leaf getter: r11, r12 and cr0 are free.
    a = Asm(base)
    a.lis(3, 0x330)
    _if_big_mem2(a, 'back')
    a.addis(3, 3, BIG_EXTRA >> 16)
    a.label('back')
    a.b(site + 4)
    return a


def _table_read_stub(base: int, site: int) -> Asm:
    # r11 is unused in the loop and r12 is loaded again right after the site; cr0 is set again before use.
    a = Asm(base)
    a.lwz(4, 0, 28)
    _if_big_mem2(a, 'back')
    a.lis(11, ha(GAME_HEAP_ENTRY)).addi(11, 11, lo(GAME_HEAP_ENTRY))
    a.cmplw(28, 11).bne('back')
    a.addis(4, 4, BIG_EXTRA >> 16)
    a.label('back')
    a.b(site + 4)
    return a


def _player_heap_option(kb: int) -> Option:
    note = '' if kb <= PLAYER_HEAP_SAFE_KB else ', past the stock-memory budget'
    return Option(player_heap_key(kb),
                  f'Player memory +{kb} KB (each player heap {PLAYER_HEAP_STOCK + kb * 1024:,} bytes{note})',
                  (Hook(HEAP_SITE, HEAP_STOCK_WORD, _player_heap_stub(PLAYER_HEAP_STOCK + kb * 1024)),),
                  group='player_heap')


def _big_heap_option(kb: int) -> Option:
    safe = PLAYER_HEAP_STOCK + PLAYER_HEAP_SAFE_KB * 1024
    return Option(big_heap_key(kb),
                  f'Player memory +{kb} KB with MEM2 at 128 MB in Dolphin (each player heap '
                  f'{PLAYER_HEAP_STOCK + kb * 1024:,} bytes; without the override +{PLAYER_HEAP_SAFE_KB} KB)',
                  (Hook(HEAP_SITE, HEAP_STOCK_WORD, _player_heap_stub(safe, PLAYER_HEAP_STOCK + kb * 1024)),
                   Hook(ROOT_SIZE_SITE, ROOT_SIZE_STOCK_WORD, _root_size_stub),
                   Hook(TABLE_READ_SITE, TABLE_READ_STOCK_WORD, _table_read_stub)),
                  group='player_heap')


OPTIONS = (
    Option('cpu_vs_cpu', 'CPU vs CPU (hold A + Minus on controller 1 while confirming)',
           (Hook(CPU_SITE, CPU_STOCK, _cpu_vs_cpu_stub),)),
    Option('cpu_management', 'CPU vs CPU management (controller 1 manages the fielding team)',
           (Hook(INIT_SITE, INIT_STOCK, _hand_fielding_team(0, INIT_STOCK)),
            Hook(FLIP_SITE, FLIP_STOCK, _hand_fielding_team(6, FLIP_STOCK)),
            Hook(STATE_SITE, STATE_STOCK, _defense_change_stub))),
) + tuple(_player_heap_option(kb) for kb in PLAYER_HEAP_LEVELS_KB) \
  + tuple(_big_heap_option(kb) for kb in BIG_HEAP_LEVELS_KB)
BY_KEY = {o.key: o for o in OPTIONS}


def _option(key: str) -> Option:
    if key not in BY_KEY:
        raise GameOptionError(f'unknown game option {key!r} (known: {", ".join(BY_KEY)})')
    return BY_KEY[key]


def _hooked(image: dolfile.DolImage, hook: Hook) -> bool:
    if not image.is_mapped(hook.site, 4):
        return False
    target = branch_target(image.u32(hook.site), hook.site)
    return target is not None and dol_hammerspace.TEXT_BASE <= target < dol_hammerspace.TEXT_LIMIT


def _stub_current(image: dolfile.DolImage, hook: Hook) -> bool:
    """The hook's stub matches what this version of the tool would write (an older version's stub doesn't)."""
    target = branch_target(image.u32(hook.site), hook.site)
    expected = hook.stub(target, hook.site).assemble()
    return image.is_mapped(target, len(expected)) and image.read(target, len(expected)) == expected


def is_on(image: dolfile.DolImage, key: str) -> bool:
    option = _option(key)
    if option.group:    # the sites are shared: the stub there tells which option of the group it is
        return all(_hooked(image, h) and _stub_current(image, h) for h in option.hooks)
    return all(_hooked(image, h) for h in option.hooks)


def _group_outdated(image: dolfile.DolImage, option: Option) -> bool:
    """The group's sites branch into our section, but to no stub of the current version (an older tool wrote
    it): any option of the group may turn it off."""
    return (all(_hooked(image, h) for h in option.hooks)
            and not any(is_on(image, o.key) for o in OPTIONS if o.group == option.group))


def _group_others(key: str) -> list[str]:
    group = _option(key).group
    return [o.key for o in OPTIONS if group and o.group == group and o.key != key]


def detect(image: dolfile.DolImage) -> list[str]:
    """Keys of the options that are on in ``image``."""
    return [o.key for o in OPTIONS if is_on(image, o.key)]


def _check_stock(image: dolfile.DolImage, option: Option) -> None:
    for h in option.hooks:
        if not image.is_mapped(h.site, 4):
            raise GameOptionError(f'{option.title}: 0x{h.site:08X} is not in this main.dol')
        word = image.u32(h.site)
        if word != h.stock and not _hooked(image, h):
            raise GameOptionError(f'{option.title}: 0x{h.site:08X} holds 0x{word:08X}, expected the stock '
                                  f'0x{h.stock:08X}; another patch changed it')


def apply(image: dolfile.DolImage, keys) -> list[str]:
    """Turn on the options in ``keys`` (others stay as they are). Returns log lines."""
    log = []
    todo = []
    keys = list(keys)
    for key in keys:
        clash = [k for k in _group_others(key) if k in keys]
        if clash:
            raise GameOptionError(f'{key} and {", ".join(clash)} can\'t both be on (one replaces the other)')
    for key in keys:
        option = _option(key)
        for other in _group_others(key):   # a group's options replace each other
            if is_on(image, other):
                _unhook(image, BY_KEY[other])
                log.append(f'{BY_KEY[other].title}: off (replaced)')
        if is_on(image, key):
            if all(_stub_current(image, h) for h in option.hooks):
                log.append(f'{option.title}: already on')
                continue
            # Written by an older version: hook it again with the current stub (the old one stays
            # behind as unused bytes, like after remove on a roster DOL).
            _unhook(image, option)
            log.append(f'{option.title}: outdated stub replaced')
        _check_stock(image, option)
        todo.append(option)
    if not todo:
        return log
    hs = dol_hammerspace.DolHammerspace.open_or_create(image)
    for option in todo:
        for h in option.hooks:
            at = hs.code.here + (-hs.code.here % 4)
            if hs.code.put(h.stub(at, h.site).assemble(), 4) != at:
                raise GameOptionError(f'{option.title}: stub placement moved')
            image.write_word(h.site, one(h.site, lambda a, t=at: a.b(t)))
        log.append(f'{option.title}: on (' + ', '.join(f'0x{h.site:08X}' for h in option.hooks) + ')')
    hs.commit()
    return log


def _unhook(image: dolfile.DolImage, option: Option) -> bool:
    if option.group and not is_on(image, option.key) and not _group_outdated(image, option):
        return False    # the shared sites hold another option of the group
    hooked = [h for h in option.hooks if _hooked(image, h)]
    for h in hooked:
        image.write_word(h.site, h.stock)
    return bool(hooked)


def remove(image: dolfile.DolImage, keys) -> list[str]:
    """Turn off the options in ``keys``: their hook sites get the stock words back. Without a roster (sections
    holding only option stubs) the sections are rebuilt with the stubs of the options still on, or dropped with
    the arena start back to stock when none is; with a roster the old stubs stay until the next roster run."""
    log = [f'{_option(key).title}: ' + ('off' if _unhook(image, _option(key)) else 'already off') for key in keys]
    hs = dol_hammerspace.DolHammerspace.open(image)
    if hs is None or hs.has_data:
        return log
    still_on = detect(image)
    for key in still_on:
        _unhook(image, BY_KEY[key])
    dol_hammerspace.remove_sections(image)
    if still_on:
        apply(image, still_on)
        log.append('unused option stubs removed (the options still on were rebuilt)')
    else:
        log.append('no option is on: the added DOL sections are removed and the arena start is stock again')
    return log
