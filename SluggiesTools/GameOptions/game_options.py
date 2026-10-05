"""Game options: small gameplay patches in ``main.dol`` (StartTools menu [8], ``start.py --game-options``).

Each option replaces stock instructions with branches to stubs in the DOL
hammerspace text section (``Roster/dol_hammerspace.py``), so it works with or
without a roster injection. Stubs read game state only through ``r13``
globals, never through fixed heap addresses: the roster expansion raises the
MEM1 arena start, so the heap addresses that stock-game Gecko codes use point
at other data in an expanded game.

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
    from ..Dol.ppc import Asm, branch_target, one
    from ..Roster import dol_hammerspace
except ImportError:
    from Dol import dolfile
    from Dol.ppc import Asm, branch_target, one
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


# ---------------------------------------------------------------- CPU vs CPU

# The match settings (*(r13-0xB00)) say who controls what: +0x18..+0x1B are the teams of
# controllers 1-4, and 0xFF means the CPU / not playing. 0x80063D4C (`lbz r0,0x18(r8)`, r8 = the
# settings) runs in the 2D controller-icon objects, on the select screens. Port of the community
# Gecko code "CPU vs CPU V2" (C2 0x80063D4C): while controller 1 holds exactly BUTTONS, all four
# controllers are set to the CPU. The Gecko code read controller 1's buttons at the stock heap
# address 0x81317BB0; the stub follows the controller manager *(r13-0x2F8) instead, whose +0
# points at the 0x2C-byte controller entries (button word at +0, as the Gecko code read it).
CPU_SITE, CPU_STOCK = 0x80063D4C, 0x88080018       # lbz r0,0x18(r8)
SETTINGS_PADS = 0x18
PAD_MANAGER = -0x2F8                               # r13
BUTTONS = 0x1800                                   # KPAD A (0x0800) + Minus (0x1000)


def _cpu_vs_cpu_stub(base: int, site: int) -> Asm:
    # r5 and r10 are set by the stock code right after the site (li r5,0 / li r10,0); cr0 is
    # compared again before its next use.
    a = Asm(base)
    a.lwz(10, PAD_MANAGER, 13).lwz(10, 0, 10).lha(5, 0, 10)
    a.cmpwi(5, BUTTONS).bne('back')
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


OPTIONS = (
    Option('cpu_vs_cpu', 'CPU vs CPU (hold A + Minus on controller 1 while confirming)',
           (Hook(CPU_SITE, CPU_STOCK, _cpu_vs_cpu_stub),)),
    Option('cpu_management', 'CPU vs CPU management (controller 1 manages the fielding team)',
           (Hook(INIT_SITE, INIT_STOCK, _hand_fielding_team(0, INIT_STOCK)),
            Hook(FLIP_SITE, FLIP_STOCK, _hand_fielding_team(6, FLIP_STOCK)),
            Hook(STATE_SITE, STATE_STOCK, _defense_change_stub))),
)
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


def is_on(image: dolfile.DolImage, key: str) -> bool:
    return all(_hooked(image, h) for h in _option(key).hooks)


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
    for key in keys:
        option = _option(key)
        if is_on(image, key):
            log.append(f'{option.title}: already on')
            continue
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
