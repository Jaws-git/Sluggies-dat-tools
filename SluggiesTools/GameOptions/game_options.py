"""Game options: small gameplay patches in ``main.dol`` (StartTools menu [10], ``start.py --game-options``).

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
Turning an option off writes the stock words back. Its stub stays behind as
unused bytes until the next roster run rebuilds the section.
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


OPTIONS = (
    Option('cpu_vs_cpu', 'CPU vs CPU (hold A + Minus on controller 1 while confirming)',
           (Hook(CPU_SITE, CPU_STOCK, _cpu_vs_cpu_stub),)),
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


def remove(image: dolfile.DolImage, keys) -> list[str]:
    """Turn off the options in ``keys``: their hook sites get the stock words back."""
    log = []
    for key in keys:
        option = _option(key)
        hooked = [h for h in option.hooks if _hooked(image, h)]
        for h in hooked:
            image.write_word(h.site, h.stock)
        log.append(f'{option.title}: ' + ('off' if hooked else 'already off'))
    return log
