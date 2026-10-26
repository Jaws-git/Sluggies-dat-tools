"""Game options: small gameplay patches in ``main.dol`` (StartTools menu [8], ``start.py --game-options``).

Each option replaces stock instructions with branches to stubs in the DOL
hammerspace text section (``Roster/dol_hammerspace.py``), so it works with or
without a roster injection. An option made of plain patches (``Patch``: a
word written in place, e.g. the team autopicker's) needs no stub and no
sections, so it keeps the stock memory layout and Gecko code compatibility. Stubs read game state only through ``r13``
globals, never through fixed heap addresses: the roster expansion raises the
MEM1 arena start, so the heap addresses that stock-game Gecko codes use point
at other data in an expanded game.

Incompatible with the community Gecko codes these options port ("CPU vs CPU V2", "CPU vs CPU human
management"): with them enabled in Dolphin, CPU vs CPU does not start, even on a stock roster
(2026-10-08). They hook the same sites, so Dolphin overwrites our branches, and they use stock heap
addresses that any DOL hammerspace moves. Users must disable them (``_docs/RosterGuide.md``). The same
move breaks the released MSS-AutoTeam autopicker (stadium / captains at the stock match settings
0x811F7698); its patched source follows *(r13-0xB00) instead (``Roster/autopicker_export.py``).

An option's on/off state is stored in the DOL itself: the option is on when
all its hook sites branch into our text section and all its patch sites hold
the patched words. The roster runner resets
``main.dol`` to ``1_Input`` before every injection. It calls ``detect`` before
the reset and ``apply`` afterwards, so a roster re-run keeps the options.
Turning an option off writes the stock words back. On a roster-built DOL its
stub stays behind as unused bytes until the next roster run rebuilds the
section; without a roster (sections holding only option stubs) ``remove``
rebuilds the sections from the options still on, or drops them and restores
the stock arena start when none is, so the DOL is stock again.
"""

import struct
from dataclasses import dataclass
from typing import Callable

try:
    from ..Dol import dolfile, inventory, relocate
    from ..Dol.ppc import Asm, branch_target, ha, lo, one
    from ..Roster import dol_hammerspace, ids
except ImportError:
    from Dol import dolfile, inventory, relocate
    from Dol.ppc import Asm, branch_target, ha, lo, one
    import dol_hammerspace
    import ids


class GameOptionError(dolfile.DolError):
    pass


@dataclass(frozen=True)
class Hook:
    site: int
    stock: int
    stub: Callable[[int, int], Asm]   # (stub address, site) -> the stub; it ends by branching to site + 4


@dataclass(frozen=True)
class Patch:
    site: int
    stock: int
    new: int                          # written in place: no stub, no DOL hammerspace


@dataclass(frozen=True)
class Floor:
    table: str                        # a per-ID table (Dol/inventory): found where the roster put it, new IDs included
    offset: int                       # an f32 in every character's row
    value: float                      # rows below it are raised to it; off puts the roster's own value back


@dataclass(frozen=True)
class RowValue:
    table: str                        # a per-ID table, found like a Floor's
    offset: int                       # an f32 in every character's row
    values: tuple[tuple[int, float], ...]   # (stock ID, value): every character whose row the roster copied from
    #                                         that ID (itself, a new ID, a stock ID adopting its stats) gets it


@dataclass(frozen=True)
class RowByte:
    table: str                        # a per-ID table, found like a Floor's
    offset: int                       # a u8 in the row; with diagonal + the row's own ID (its chemistry towards itself)
    value: int
    ids: tuple[int, ...] | None = None    # the characters (by ID) whose rows get it; None: every character, new IDs too
    diagonal: bool = False


@dataclass(frozen=True)
class Option:
    key: str
    title: str
    hooks: tuple[Hook, ...]
    group: str | None = None    # options of one group hook the same sites: only the one whose stub is there is on
    patches: tuple[Patch, ...] = ()
    floors: tuple[Floor, ...] = ()
    values: tuple[RowValue, ...] = ()
    bytes_: tuple[RowByte, ...] = ()


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


# ------------------------------------------------------- CPU vs CPU close plays
#
# Port of the community Gecko code "CPU Closeplay Overhaul" [C^2] (C2 0x80156348): 50/50 close play odds in CPU
# vs CPU, 100% for a player with the baserunning ability. The close play code (0x80156A5C-0x80156AA0) adds
# CLOSE_PLAY_TABLE[row][column] (s16; row *(r13-0x1658), column from the byte table 0x80797264) to a base from
# 0x806263D8 and, on the frame the play's counter reaches that sum, rolls the game's random(100) (0x80165C80).
# The Gecko code wrote row 0 column 3 every time fn_8015619C reached the site: 2 when the byte +0x1A1 of the
# player record r31 (*(0x807098C8 + 4 * the player's +0x16)) is set, else 1 or 60 from the low bit of a stock
# heap halfword (0x900D5BFC, the game object +4). The stub draws from the time base instead (mftb modulo 100,
# no heap address), so the odds of the 1 can be set; 50% is the Gecko code. Outside CPU vs CPU it writes the
# stock value back (the Gecko code left its last value there for the next human match). Static data and r13 /
# register reads only: a roster run or a player memory level moves none of it. The stub uses r0, r11, r12 and
# cr0: r0 is loaded by the stock instruction, r11 and r12 are set again before they are read, and cr0 is set by
# the `cmpw` right after the site.
CLOSE_PLAY_SITE, CLOSE_PLAY_STOCK = 0x80156348, 0xA8030002     # lha r0,2(r3)
CLOSE_PLAY_TABLE = 0x80624B18                                  # s16 [4 rows][4 columns]
CLOSE_PLAY_ENTRY = CLOSE_PLAY_TABLE + 3 * 2                    # row 0, column 3 (0x80624B1E)
CLOSE_PLAY_STOCK_VALUE = 0x14
CLOSE_PLAY_ABILITY, CLOSE_PLAY_ABILITY_VALUE = 0x1A1, 2        # r31 + 0x1A1: the baserunning ability
CLOSE_PLAY_HIT, CLOSE_PLAY_MISS = 1, 60                        # the Gecko code's two values
CLOSE_PLAY_LEVELS = tuple(range(0, 101, 10))                   # % of draws that write CLOSE_PLAY_HIT
CLOSE_PLAY_GECKO = 50


def close_play_key(percent: int) -> str:
    return f'close_play_{percent}'


def _close_play_stub(percent: int):
    def stub(base: int, site: int) -> Asm:
        a = Asm(base)
        _if_cpu_vs_cpu(a, 'stock')
        a.li(12, CLOSE_PLAY_ABILITY_VALUE)
        a.lbz(11, CLOSE_PLAY_ABILITY, 31).cmpwi(11, 0).bne('store')
        a.mftb(11).li(0, 100).divwu(12, 11, 0).mullw(12, 12, 0).subf(11, 12, 11)     # r11 = time base % 100
        a.li(12, CLOSE_PLAY_HIT).cmplwi(11, percent).blt('store')
        a.li(12, CLOSE_PLAY_MISS).b('store')
        a.label('stock')
        a.li(12, CLOSE_PLAY_STOCK_VALUE)
        a.label('store')
        a.lis(11, ha(CLOSE_PLAY_ENTRY)).sth(12, lo(CLOSE_PLAY_ENTRY), 11)
        a.word(CLOSE_PLAY_STOCK)
        a.b(site + 4)
        return a
    return stub


def _close_play_option(percent: int) -> Option:
    note = ' (the Gecko code)' if percent == CLOSE_PLAY_GECKO else ''
    return Option(close_play_key(percent),
                  f'CPU vs CPU close play odds {percent}%{note}, 100% with the baserunning ability',
                  (Hook(CLOSE_PLAY_SITE, CLOSE_PLAY_STOCK, _close_play_stub(percent)),), group='close_play')


# ------------------------------------------------------------ CPU vs CPU fixes
#
# Ports of community Gecko codes as plain patches: no stub and no DOL hammerspace, so they keep the stock memory
# layout and Gecko code compatibility, and they don't need the CPU vs CPU option (it only makes them matter).
#
# "CPU Restore Triple Take Homeruns" [C^2] (C2 0x802500B8 / 0x802529B0): set r0 = 1 before `cmpwi r0,0`, so the
# `beq` after it never branches. The test before it: r3 (r4) == 2 and *(*(r13-0x1D78), or *(r13-0x1D7C)) +0x34
# == 0, the game object's flag that CPU vs CPU sets. Here the `beq` becomes a nop: the same, since r0 is loaded
# again before its next read on both ways on.
TRIPLE_TAKE_PATCHES = (Patch(0x802500BC, 0x41820024, 0x60000000),    # beq 0x802500E0 -> nop
                       Patch(0x802529B4, 0x41820024, 0x60000000))    # beq 0x802529D8 -> nop
# "CPU Restore Dynamic Strikeout Camera" (C2 0x802504A0: li r0,0): the same flag, read in place.
STRIKEOUT_CAMERA_PATCHES = (Patch(0x802504A0, 0x80050034, 0x38000000),)     # lwz r0,0x34(r5) -> li r0,0
# "CPUs Can Dive and Jump in Outfield" [dementatino] (04 0x800E2750): states 2-8 instead of 2-5 (r0 = r29 - 2).
OUTFIELD_DIVE_PATCHES = (Patch(0x800E2750, 0x28000003, 0x28000006),)       # cmplwi r0,3 -> cmplwi r0,6
# "CPU Remove Precharge" [philenarion and The Fun Guy] wrote 8.0 to six stock addresses: the "charge" value (f32
# +0, the stat editor's pitching "charge") of the pitchwindup rows of IDs 0x08, 0x09, 0x0F, 0x27, 0x3F and 0x40,
# the only stock rows below 8.0 (7.0, and 5.0 for 0x0F). A roster run with new IDs moves that table, so the
# option raises every character's row below 8.0 wherever the table is (new IDs' rows too) instead.
PRECHARGE_FLOORS = (Floor('pitchwindup', 0, 8.0),)


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
PLAYER_HEAP_LEVELS_KB = (32, 48, 64, 128, 200)
PLAYER_HEAP_SAFE_KB = 48                                # the stock-memory ceiling (fallback of the big levels)
BIG_HEAP_LEVELS_KB = (512, 1024, 2048, 4096, 5120)
# Experimental levels (2026-10-11): +200 KB and +5 MB worked at Wario City; other stadiums and heavy
# scenes are untested. +5 MB works although 13 x 5 MB = 65 MB is more than the 64 MB BIG_EXTRA adds to
# heap 3, so the stock heap 3 has slack. +6 MB crashed instantly with the MEM2 override on (13 x 6 MB =
# 78 MB); +256 KB and +7/+8 MB were offered the same day and dropped.
EXPERIMENTAL_HEAP_LEVELS_KB = frozenset((200, 5120))
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
    if kb in EXPERIMENTAL_HEAP_LEVELS_KB:
        note += ', experimental'
    return Option(player_heap_key(kb),
                  f'Player memory +{kb} KB (each player heap {PLAYER_HEAP_STOCK + kb * 1024:,} bytes{note})',
                  (Hook(HEAP_SITE, HEAP_STOCK_WORD, _player_heap_stub(PLAYER_HEAP_STOCK + kb * 1024)),),
                  group='player_heap')


def _big_heap_option(kb: int) -> Option:
    safe = PLAYER_HEAP_STOCK + PLAYER_HEAP_SAFE_KB * 1024
    note = ', experimental' if kb in EXPERIMENTAL_HEAP_LEVELS_KB else ''
    return Option(big_heap_key(kb),
                  f'Player memory +{kb} KB with MEM2 at 128 MB in Dolphin (each player heap '
                  f'{PLAYER_HEAP_STOCK + kb * 1024:,} bytes; without the override +{PLAYER_HEAP_SAFE_KB} KB{note})',
                  (Hook(HEAP_SITE, HEAP_STOCK_WORD, _player_heap_stub(safe, PLAYER_HEAP_STOCK + kb * 1024)),
                   Hook(ROOT_SIZE_SITE, ROOT_SIZE_STOCK_WORD, _root_size_stub),
                   Hook(TABLE_READ_SITE, TABLE_READ_STOCK_WORD, _table_read_stub)),
                  group='player_heap')


# ----------------------------------------------------------- team autopicker
#
# The Gecko code the MSS-AutoTeam autopicker asks for, written into main.dol instead (three plain words, so
# the DOL keeps the stock memory layout): nop the `bl 0x80069CC0` and `bl Select::CExhiMemOrdTask::Notify`
# in fn_80080010 (the order screen's update), and branch over fn_8006AEA4's lineup reset to its exit
# (0x8006BA58), so the lineup the autopicker writes into RAM survives. The autopicker itself still writes
# its lineup stub at run time (0x800021F0, reached by a `b` at 0x8006AED4).
AUTOPICKER_PATCHES = (Patch(0x800802B4, 0x4BFE9A0D, 0x60000000),     # bl 0x80069CC0 -> nop
                      Patch(0x800802B8, 0x482491B5, 0x60000000),     # bl 0x802C946C -> nop
                      Patch(0x8006AED8, 0x98C3000C, 0x48000B80))     # stb r6,0xC(r3) -> b 0x8006BA58

# ------------------------------------------------- unused character strike zones
#
# "Fix Unused Character Strikezone Heights" (04 0x80629C38 / 0x80629C5C / 0x80629C80 / 0x80629CA4) writes the f32
# +0x1C of the charfloats rows (0x806291D8, 0x24 bytes per ID) of the unused characters 0x49 Toad, 0x4A Pianta,
# 0x4B Kritter and 0x4C Koopa (stock 1.1, 1.3, 0.15, 1.0). That table is a stats table of the roster
# (``ids.is_stats_table``): a run with new IDs moves it, a new ID copies its stats source's row, and a stock ID
# that adopts stats takes its source's row (in place without new IDs). So the fix follows the table and goes to
# every character whose row came from one of the four.
STRIKE_ZONE_FIX = (RowValue('charfloats', 0x1C, ((0x49, 0.8), (0x4A, 0.95), (0x4B, 1.15), (0x4C, 0.9))),)

# ------------------------------------------------------- characters and chemistry
#
# Ports of the community Gecko codes of the "Characters and Chemistry" section (Gameplay tab, Extras).
#
# "Duplicate Characters" [LlamaTrauma] (five 04 lines): plain patches. 0x802C9210/14 turn `cmpwi r24,0; beq` into
# `cmpwi r24,0xFF; bne` (r24 is 0 there, so the branch is always taken), 0x80074DA4 and 0x80073AF8 drop a
# `beq cr1` after a compare with fn_802D9910's result, and 0x80071B10 replaces the "already on the team" check
# (`bl 0x800638A8`) with `li r3,0`.
DUPLICATE_CHARACTER_PATCHES = (Patch(0x802C9210, 0x2C180000, 0x2C1800FF),     # cmpwi r24,0 -> cmpwi r24,0xFF
                               Patch(0x802C9214, 0x4182000C, 0x4082000C),     # beq +0xC -> bne +0xC
                               Patch(0x80074DA4, 0x418601E4, 0x60000000),     # beq cr1,0x80074F88 -> nop
                               Patch(0x80071B10, 0x4BFF1D99, 0x38600000),     # bl 0x800638A8 -> li r3,0
                               Patch(0x80073AF8, 0x41861490, 0x60000000))     # beq cr1,0x80074F88 -> nop
# "Duplicate Captains" [LittleCoaks] (C2 0x80071B0C): in the team check loop (r27 = the slot 0-8, r29 = slot * 0x10,
# r0 = the team's 0x10-byte slots), every slot but the first gets the first slot's character (s16 +0), so a team is
# nine of its captain. Its stub returns to 0x80071B10, which "Duplicate Characters" patches: the two combine.
DUPLICATE_CAPTAIN_SITE, DUPLICATE_CAPTAIN_STOCK = 0x80071B0C, 0x7C60EA14      # add r3,r0,r29


def _duplicate_captain_stub(base: int, site: int) -> Asm:
    # r5 is free: the Gecko code's own choice (fn_800638A8 takes r3/r4 only).
    a = Asm(base)
    a.word(DUPLICATE_CAPTAIN_STOCK)
    a.cmpwi(27, 0).beq('back')
    a.mr(5, 0).lha(5, 0, 5).sth(5, 0, 3)           # slot r27 = slot 0's character (lha's base can't be r0)
    a.label('back')
    a.b(site + 4)
    return a


# "Requires all characters are captains" [philenarion] (08 0x806CE9AF, 0x65 bytes 0x8E apart): the "captain" byte
# (+7) of every stats row (0x806CE9A0, 0x8E bytes per ID) set to 1. The stats table moves with a roster run's new IDs,
# so the option follows the table and sets every character's row, new IDs too.
ALL_CAPTAINS = (RowByte('stats', 0x07, 1),)
# "Captains Have Chemistry With Themselves" [LittleCoaks] (twelve 00 lines): chemistry 2 (stock 1) of the twelve
# stock captains towards themselves (stats row +0x28 + own ID). New IDs' chemistry with themselves is in the roster's
# own new-by-new table (Roster/ids.new_by_new_chemistry), which the option leaves alone.
CAPTAIN_IDS = (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x09, 0x0A, 0x0B, 0x11, 0x13)
CAPTAIN_SELF_CHEMISTRY = (RowByte('stats', 0x28, 2, CAPTAIN_IDS, diagonal=True),)

# --------------------------------------------------------------------- 99-0 tools
#
# Ports of the community Gecko codes of the "99-0 Tools" section (Gameplay tab, Extras). The score object
# *(r13-0x1544) (0xA4 bytes, made at 0x8017F620, set up by fn_8017F688): +0 the inning (from 1), +4 and +0x1E the
# two teams' arrays of 13 s16 (+0 the score, then the runs of innings 1-12), +0x38 / +0x52 two more such arrays,
# +0x74 the result, +0x8E the innings, +0x8F the last inning (the innings + 3 in the stock game, so at most 12).
#
# "Always away team" [LlamaTrauma] (04 0x802CEAA4): the match settings' +8 (*(r13-0xB00)) takes 0 instead of the
# low bit of a random draw (bl 0x80478A90), so the first team always bats first.
ALWAYS_AWAY_PATCHES = (Patch(0x802CEAA4, 0x546007FE, 0x38000000),)        # clrlwi r0,r3,31 -> li r0,0
# "Unlimited extra innings" [LittleCoaks] (04 0x8017F694, li r0,XX): the last inning XX instead of the innings + 3.
# Here XX = 99. Experimental: the score arrays hold 12 innings, so from the 13th inning on the runs of team 0 land in
# team 1's score and team 1's in the fields after its arrays (+0x6C.., the result at +0x74).
EXTRA_INNINGS_LAST = 99
EXTRA_INNINGS_PATCHES = (Patch(0x8017F694, 0x38040003, 0x38000000 | EXTRA_INNINGS_LAST),)   # addi r0,r4,3 -> li
# "999 runs" [LlamaTrauma]: the scoreboard's two-digit score boxes (fn_8032FDxx, a loop over the teams, r28 = the
# team, r27 = its score, r3 = its score array) show team 0's score in three digits and team 1's in one: team 0's box
# shows (score % 1000) / 10, team 1's (team 0's score % 10) * 10 + min(its score, 9). The Gecko code also has the
# "Always away team" line, so the first team (the one with three digits) is yours. It wrote its function over stock
# code at 0x80489FC8 (bl from 0x8032FE14); here it is a stub. Four patches lay out the digits: always two per box,
# and the tens digit of team 1's box and the box after the loop trade places (pane 4 <-> 5). The stub uses r0, r4,
# r5 and cr0: all are set again before they are read, and cr0 by the stock compare the stub ends with.
SCORE_999_SITE, SCORE_999_STOCK = 0x8032FE14, 0x2C1B0063      # cmpwi r27,99 (the stock score cap)
SCORE_999_PATCHES = (Patch(0x8032FE30, 0x2C1B000A, 0x2C1B0000),     # cmpwi r27,10 -> cmpwi r27,0: two digits
                     Patch(0x8032FEB4, 0x41800070, 0x7C000378),     # blt 0x8032FF24 -> or r0,r0,r0: tens digit too
                     Patch(0x8032FECC, 0x38000004, 0x38000005),     # li r0,4 -> li r0,5
                     Patch(0x8032FF34, 0x38C00005, 0x38C00004))     # li r6,5 -> li r6,4
SCORE_ENTRY = 0x1A                                             # team 1's score array is team 0's + 0x1A


def _score_999_stub(base: int, site: int) -> Asm:
    a = Asm(base)
    a.cmpwi(28, 1).bne('team0')
    a.lhz(4, 4 - SCORE_ENTRY, 3)                               # r3 + 4 is team 1's score: team 0's (-0x16)
    a.li(5, 10).divwu(0, 4, 5).mullw(0, 0, 5).subf(4, 0, 4).mulli(4, 4, 10)       # r4 = (team 0 % 10) * 10
    a.cmpwi(27, 9).ble('add')
    a.li(27, 9)
    a.label('add')
    a.add(27, 4, 27).b('back')
    a.label('team0')
    a.li(5, 1000).divw(0, 27, 5).mullw(0, 0, 5).subf(27, 0, 27)                   # r27 % 1000
    a.li(5, 10).divw(27, 27, 5)
    a.label('back')
    a.word(SCORE_999_STOCK)
    a.b(site + 4)
    return a


# ------------------------------------------------------------------ miscellaneous
#
# Ports of the community Gecko codes of the "Miscellaneous" section (Gameplay tab, Extras).
#
# "Mercy Rule Modifier" [ZPL] (04 0x8017F6A8, li r0,XX): fn_8017F688 sets the score object's +0xA2 to 10 when the
# match settings' +9 (the mercy rule) is on, else to 0; a match ends once a team leads by +0xA2 runs (0x80136EE4,
# 0x8015E474; 0 skips the check). One option per lead, a group on the one patch site; the stock 10 is "off".
MERCY_RULE_SITE, MERCY_RULE_STOCK = 0x8017F6A8, 0x3800000A     # li r0,10
MERCY_RULE_STOCK_RUNS = 10
MERCY_RULE_LEVELS = tuple(r for r in range(1, 100) if r != MERCY_RULE_STOCK_RUNS)


def mercy_rule_key(runs: int) -> str:
    return f'mercy_rule_{runs}'


def _mercy_rule_option(runs: int) -> Option:
    return Option(mercy_rule_key(runs), f'Mercy rule at a {runs}-run lead (stock {MERCY_RULE_STOCK_RUNS})', (),
                  group='mercy_rule', patches=(Patch(MERCY_RULE_SITE, MERCY_RULE_STOCK, 0x38000000 | runs),))


# "Day/Night Music Themes" [C^2] (C2 0x803A43F4): the stadium select rewrote the music table 0x8062E608 (u32 track
# per stadium index, the match settings' +5) on every confirm, with other tracks at night (settings +6) for Mario
# Stadium (0), Wario City (2), Yoshi Park (3), Peach Ice Garden (4) and DK Jungle (5), and the stock ones by day.
# Here the table's two reads (`lwzx r5` of table + index * 4, the index * 4 in r0) take the night track instead:
# the table stays stock, and a match that skips the stadium select (the autopicker) gets the right music too. The
# stubs use r12 and cr1: at the first site cr0 holds a compare the next instruction branches on; r12 is set again
# before it is read at both.
NIGHT_MUSIC_HOOKS = ((0x80389098, 0x7CA4002E), (0x803899C4, 0x7CA5002E))      # lwzx r5,r4,r0 / lwzx r5,r5,r0
SETTINGS_NIGHT = 6
NIGHT_MUSIC = ((0, 0x15), (2, 0x18), (3, 0x19), (4, 0x16), (5, 0x17))         # (stadium index, night track)


def _night_music_stub(stock: int):
    def stub(base: int, site: int) -> Asm:
        a = Asm(base)
        a.word(stock)
        a.lwz(12, SETTINGS, 13).lbz(12, SETTINGS_NIGHT, 12)
        a.cmpwi(12, 0, cr=1).beq('back', cr=1)
        for stadium, track in NIGHT_MUSIC:
            a.cmpwi(0, stadium * 4, cr=1).bne(f'not{stadium}', cr=1)
            a.li(5, track).b('back')
            a.label(f'not{stadium}')
        a.label('back')
        a.b(site + 4)
        return a
    return stub


# "Challenge Mode - Press B+2 to get 999 Coins" [Master Kirby] (28 0x81317BB0 / 02 0x80E55C0A): while controller 1
# held exactly B + 2, the coins (s16 +0x12E of the challenge struct *(r13-0xE80), made once at 0x802552C4) at their
# stock heap address were set to 999. A roster expansion moves that struct (0x80E60C7C in a RAM dump of an expanded
# game, 2026-10-25). The stub runs at the end of the per-frame controller update (fn_8050B5A4's epilogue, whose
# `addi r11` the restore call after it reads), reads controller 1's held buttons like the CPU vs CPU stub, and sets
# the coins only while challenge mode runs (its manager *(r13-0x1224) exists). It uses r10, r12 and cr0: the
# epilogue reads none of them.
COINS_SITE, COINS_STOCK = 0x8050BF44, 0x396100B0       # addi r11,r1,0xB0
COINS_BUTTONS = 0x0500                                  # B (0x0400) + 2 (0x0100)
CHALLENGE_MANAGER = -0x1224                             # r13
CHALLENGE_STRUCT = -0xE80                               # r13
CHALLENGE_COINS = 0x12E
COINS_MAX = 999


def _challenge_coins_stub(base: int, site: int) -> Asm:
    a = Asm(base)
    a.lwz(12, PAD_MANAGER, 13).cmpwi(12, 0).beq('back')
    a.lwz(12, 0, 12).lhz(10, 0, 12)
    a.andi_(10, 10, COINS_BUTTONS).cmpwi(10, COINS_BUTTONS).bne('back')
    a.lwz(12, CHALLENGE_MANAGER, 13).cmpwi(12, 0).beq('back')
    a.lwz(12, CHALLENGE_STRUCT, 13).cmpwi(12, 0).beq('back')
    a.li(10, COINS_MAX).sth(10, CHALLENGE_COINS, 12)
    a.label('back')
    a.word(COINS_STOCK)
    a.b(site + 4)
    return a


# ------------------------------------------------------------------------- cheats
#
# Ports of the [TNTkryzt] codes of Dolphin's code list (Options tab, Cheats; untested, the list says they may not
# work). The four 06/04 codes put a function in the MEM1 tail (0x817FED14..) and branch to it from the site; here
# each function is a stub of the same instructions. The three save data codes wrote stock heap addresses every frame;
# the stubs write the same bytes through the r13 globals instead, from the end of the per-frame controller update
# (fn_8050B5A4, whose caller drops r3: after __restore_gpr every volatile register and cr0 is free).
#
# "Toy Field 9999 Coins" (04 0x804CA690): fn_804CA354 clamps the coins (r24 +0x20) to 0..max (r0 = r24 +0xA8)
# and stores the clamped r3; the patch stores the max.
TOY_FIELD_COIN_PATCHES = (Patch(0x804CA690, 0x90780020, 0x90180020),)     # stw r3,0x20(r24) -> stw r0,0x20(r24)
# "Homeruns Worth Double" (06 0x817FED64 / 04 0x8017FD94): fn_8017F924 adds a play's runs (s16 r28 +0x6C) to the
# score; the stub doubles them when the word r28 +0xA94 is 1. r11 is set again before it is read, cr0 before use.
DOUBLE_HR_SITE, DOUBLE_HR_STOCK = 0x8017FD94, 0xA81C006C    # lha r0,0x6C(r28)


def _double_hr_stub(base: int, site: int) -> Asm:
    a = Asm(base)
    a.word(DOUBLE_HR_STOCK)
    a.lwz(11, 0xA94, 28).cmpwi(11, 1).bne('back')
    a.mulli(0, 0, 2)
    a.label('back')
    a.b(site + 4)
    return a


# "Reset Out Count B+1" (06 0x817FED14 / 04 0x80132230): in fn_801320D8 (r30 = *(r13-0x1D38), +9 the outs), while
# the word r28 +0x34 is 1 and the halfword r30 +0x48 holds exactly B + 1 (0x0600), the outs are set to 0. The site
# follows a bctrl, so r11 is free; cr0 is set again right after it.
RESET_OUTS_SITE, RESET_OUTS_STOCK = 0x80132230, 0x881E0009  # lbz r0,9(r30)
RESET_OUTS_BUTTONS = 0x0600


def _reset_outs_stub(base: int, site: int) -> Asm:
    a = Asm(base)
    a.lwz(11, 0x34, 28).cmpwi(11, 1).bne('stock')
    a.lhz(11, 0x48, 30).cmpwi(11, RESET_OUTS_BUTTONS).bne('stock')
    a.li(0, 0).stb(0, 9, 30).b(site + 4)
    a.label('stock')
    a.word(RESET_OUTS_STOCK)
    a.b(site + 4)
    return a


# "Extremely Fast Movement P1" (06 0x817FED40 / 04 0x8011B630): fn_8011B600 (r3 = a player object, r5 its third
# argument) reads a speed f32 at r3 +0xEC; unless r5 is 0 or the word r5 -0x2A0 is 1, the stub writes 0.46875
# there first. r11 is unused in the prologue; cr0 is set again right after the site.
FAST_MOVEMENT_SITE, FAST_MOVEMENT_STOCK = 0x8011B630, 0xC3E300EC     # lfs f31,0xEC(r3)
FAST_MOVEMENT_SPEED = 0x3EF00000                                     # 0.46875


def _fast_movement_stub(base: int, site: int) -> Asm:
    a = Asm(base)
    a.cmpwi(5, 0).beq('stock')
    a.lwz(11, -0x2A0, 5).cmpwi(11, 1).beq('stock')
    a.lis(11, FAST_MOVEMENT_SPEED >> 16).stw(11, 0xEC, 3)
    a.label('stock')
    a.word(FAST_MOVEMENT_STOCK)
    a.b(site + 4)
    return a


# "All Star Players" and "All MVP's" are one code (00 0x80E559A7 / 08 0x80E559A8, ten words): 41 bytes set to 1 at
# +0x197 of the progress object *(r13-0x300) (_docs/SaveData.md; 0x80E55810 in the stock game, located from the
# challenge struct's offset to it in a RAM dump of an expanded game, 2026-10-25, where those 41 bytes are 0 or 1).
STARS_SITE, STARS_STOCK = 0x8050BF9C, 0x80010154            # lwz r0,0x154(r1)
PROGRESS = -0x300                                           # r13
STARS_FIRST, STARS_WORDS = 0x197, 10


def _all_stars_stub(base: int, site: int) -> Asm:
    a = Asm(base)
    a.lwz(12, PROGRESS, 13).cmpwi(12, 0).beq('back')
    a.li(11, 1).stb(11, STARS_FIRST, 12)
    a.lis(11, 0x0101).addi(11, 11, 0x0101)
    for i in range(STARS_WORDS):
        a.stw(11, STARS_FIRST + 1 + 4 * i, 12)
    a.label('back')
    a.word(STARS_STOCK)
    a.b(site + 4)
    return a


# "Unlock All Items" (04 0x80E55DAC..0x80E55DB4): twelve item counts at +0x2D0 of the challenge struct
# *(r13-0xE80) (stock 0x80E55ADC, see the challenge coins) set to 99 each.
ITEMS_SITE, ITEMS_STOCK = 0x8050BFA0, 0x7C0803A6            # mtlr r0
CHALLENGE_ITEMS, ITEMS_WORDS, ITEMS_COUNT = 0x2D0, 3, 0x63


def _all_items_stub(base: int, site: int) -> Asm:
    a = Asm(base)
    a.word(ITEMS_STOCK)
    a.lwz(12, CHALLENGE_STRUCT, 13).cmpwi(12, 0).beq('back')
    a.lis(11, ITEMS_COUNT * 0x0101).addi(11, 11, ITEMS_COUNT * 0x0101)
    for i in range(ITEMS_WORDS):
        a.stw(11, CHALLENGE_ITEMS + 4 * i, 12)
    a.label('back')
    a.b(site + 4)
    return a


OPTIONS = (
    Option('cpu_vs_cpu', 'CPU vs CPU (hold A + Minus on controller 1 while confirming)',
           (Hook(CPU_SITE, CPU_STOCK, _cpu_vs_cpu_stub),)),
    Option('cpu_management', 'CPU vs CPU management (controller 1 manages the fielding team)',
           (Hook(INIT_SITE, INIT_STOCK, _hand_fielding_team(0, INIT_STOCK)),
            Hook(FLIP_SITE, FLIP_STOCK, _hand_fielding_team(6, FLIP_STOCK)),
            Hook(STATE_SITE, STATE_STOCK, _defense_change_stub))),
    Option('autopicker', 'Team autopicker (the MSS-AutoTeam Gecko code, in main.dol)', (),
           patches=AUTOPICKER_PATCHES),
    Option('triple_take', 'Triple take replays of CPU home runs (CPU vs CPU)', (), patches=TRIPLE_TAKE_PATCHES),
    Option('strikeout_camera', 'Dynamic strikeout camera (CPU vs CPU)', (), patches=STRIKEOUT_CAMERA_PATCHES),
    Option('outfield_dives', 'CPU fielders dive and jump in the outfield', (), patches=OUTFIELD_DIVE_PATCHES),
    Option('no_precharge', 'No CPU precharge (every pitch "charge" value at least 8.0)', (),
           floors=PRECHARGE_FLOORS),
    Option('unused_strike_zones', 'Strike zone heights of the unused characters (Toad, Pianta, Kritter, Koopa)', (),
           values=STRIKE_ZONE_FIX),
    Option('duplicate_characters', 'Duplicate characters (a character may be on a team more than once)', (),
           patches=DUPLICATE_CHARACTER_PATCHES),
    Option('duplicate_captains', 'Duplicate captains (both teams are nine of their captain)',
           (Hook(DUPLICATE_CAPTAIN_SITE, DUPLICATE_CAPTAIN_STOCK, _duplicate_captain_stub),)),
    Option('all_captains', 'Every character is a captain', (), bytes_=ALL_CAPTAINS),
    Option('captain_self_chemistry', 'Captains have chemistry with themselves', (), bytes_=CAPTAIN_SELF_CHEMISTRY),
    Option('always_away', 'Always away team (the first team bats first)', (), patches=ALWAYS_AWAY_PATCHES),
    Option('score_999', '999 runs on the scoreboard (the first team\'s score in three digits)',
           (Hook(SCORE_999_SITE, SCORE_999_STOCK, _score_999_stub),), patches=SCORE_999_PATCHES),
    Option('extra_innings', f'Unlimited extra innings (up to inning {EXTRA_INNINGS_LAST}, experimental)', (),
           patches=EXTRA_INNINGS_PATCHES),
    Option('night_music', 'Day/night music themes (other music at night in five stadiums)',
           tuple(Hook(site, stock, _night_music_stub(stock)) for site, stock in NIGHT_MUSIC_HOOKS)),
    Option('challenge_coins', 'Challenge mode: hold B + 2 for 999 coins',
           (Hook(COINS_SITE, COINS_STOCK, _challenge_coins_stub),)),
    Option('toy_field_coins', 'Toy Field 9999 coins (cheat)', (), patches=TOY_FIELD_COIN_PATCHES),
    Option('double_home_runs', 'Home runs worth double (cheat)',
           (Hook(DOUBLE_HR_SITE, DOUBLE_HR_STOCK, _double_hr_stub),)),
    Option('reset_outs', 'Reset the out count with B + 1 (cheat)',
           (Hook(RESET_OUTS_SITE, RESET_OUTS_STOCK, _reset_outs_stub),)),
    Option('fast_movement', 'Extremely fast movement, player 1 (cheat)',
           (Hook(FAST_MOVEMENT_SITE, FAST_MOVEMENT_STOCK, _fast_movement_stub),)),
    Option('all_stars', 'All star players / all MVPs (cheat)', (Hook(STARS_SITE, STARS_STOCK, _all_stars_stub),)),
    Option('all_items', 'Unlock all items (cheat)', (Hook(ITEMS_SITE, ITEMS_STOCK, _all_items_stub),)),
) + tuple(_close_play_option(p) for p in CLOSE_PLAY_LEVELS) \
  + tuple(_mercy_rule_option(r) for r in MERCY_RULE_LEVELS) \
  + tuple(_player_heap_option(kb) for kb in PLAYER_HEAP_LEVELS_KB) \
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


def _patched(image: dolfile.DolImage, patch: Patch) -> bool:
    return image.is_mapped(patch.site, 4) and image.u32(patch.site) == patch.new


def _roster(image: dolfile.DolImage):
    """The roster's new IDs and stats sources (``StatEditor/bridge.read_roster``: the manifest). A DOL without roster
    data whose manifest cannot be read (another tool changed the grid code) has the stock IDs only."""
    try:
        from ..StatEditor import bridge
    except ImportError:
        from StatEditor import bridge
    try:
        return bridge.read_roster(image)
    except (RuntimeError, ValueError):     # BridgeError; DolError from a grid site the DOL does not map
        hs = dol_hammerspace.DolHammerspace.open(image)
        if hs is not None and hs.has_data:
            raise                       # a roster of ours whose new IDs cannot be read
        return bridge.Roster(None, [], {})


def _cells(image: dolfile.DolImage, name: str, offset: int) -> list[tuple[int, int, int]]:
    """``(character ID, row source, address)`` of the f32 at ``offset`` in every character's row of the per-ID table
    ``name`` (stock IDs and the roster's new IDs), in the table where ``image`` has it (its first lis/low pair, as
    ``StatEditor/bridge.table_layouts`` finds it). The row source is the stock ID whose row the roster copied there
    (``ids.NewId.row_source``; a stock ID that adopts stats takes its source's row of a stats table)."""
    try:
        table = inventory.table(name)
        at = relocate.pair_address(image, *table.all_pairs[0]) + table.header + offset
        roster = _roster(image)
        adopted = roster.stock_stats if ids.is_stats_table(name) else {}
        rows = [(cid, adopted.get(cid, cid)) for cid in range(ids.STOCK_IDS)]
        rows += [(c.id, c.row_source(name)) for c in roster.new]
        return [(cid, source, at + table.row_size * cid) for cid, source in rows]
    except (RuntimeError, ValueError) as exc:          # BridgeError, DolError, RelocationError (no lis pair)
        raise GameOptionError(f'{name}: the per-ID table could not be found ({exc})') from exc


def _floor_cells(image: dolfile.DolImage, floor: Floor) -> list[tuple[int, int]]:
    """``(character ID, address)`` of the floor's f32 in every character's row (``_cells``)."""
    return [(cid, a) for cid, _source, a in _cells(image, floor.table, floor.offset)]


def _value_cells(image: dolfile.DolImage, fix: 'RowValue') -> list[tuple[int, bytes, int]]:
    """``(character ID, the fixed f32's bytes, address)`` of every character whose row comes from a fixed ID."""
    values = dict(fix.values)
    return [(cid, struct.pack('>f', values[source]), a) for cid, source, a in _cells(image, fix.table, fix.offset)
            if source in values]


def _byte_cells(image: dolfile.DolImage, fix: RowByte) -> list[tuple[int, int]]:
    """``(character ID, address)`` of the fix's u8 in the rows of its characters (``_cells``; diagonal: the column of
    the row's own ID, stock IDs only)."""
    cells = [(cid, a) for cid, _source, a in _cells(image, fix.table, fix.offset)
             if fix.ids is None or cid in fix.ids]
    if fix.diagonal:
        cells = [(cid, a + cid) for cid, a in cells if cid < ids.STOCK_IDS]
    return cells


def _baseline(image: dolfile.DolImage, vanilla: dolfile.DolImage | None, name: str):
    """``(cid, offset) -> 4 bytes``: the table's rows as a roster run builds them from ``vanilla`` (no options, no
    stat edits)."""
    if vanilla is None:
        raise GameOptionError(f'{name}: turning this option off needs 1_Input/main.dol (the stock values)')
    roster = _roster(image)
    table = inventory.table(name)
    blob, _log = ids.extended_table(vanilla, table, roster.new, ids.ROWS if roster.new else ids.STOCK_IDS + 1,
                                    roster.stock_stats)
    return lambda cid, offset: blob[table.header + table.row_size * cid + offset:][:4]


def _f32(image: dolfile.DolImage, address: int) -> float:
    return struct.unpack('>f', image.read(address, 4))[0]


def _floored(image: dolfile.DolImage, floor: Floor) -> bool:
    try:
        cells = _floor_cells(image, floor)
    except GameOptionError:
        return False
    return all(_f32(image, a) >= floor.value for _cid, a in cells)


def _fixed(image: dolfile.DolImage, fix: 'RowValue') -> bool:
    try:
        cells = _value_cells(image, fix)
    except GameOptionError:
        return False
    return all(image.read(a, 4) == value for _cid, value, a in cells)


def _byte_set(image: dolfile.DolImage, fix: RowByte) -> bool:
    try:
        cells = _byte_cells(image, fix)
    except GameOptionError:
        return False
    return all(image.read(a, 1)[0] == fix.value for _cid, a in cells)


def is_on(image: dolfile.DolImage, key: str) -> bool:
    option = _option(key)
    if not all(_patched(image, p) for p in option.patches):
        return False
    if not all(_floored(image, f) for f in option.floors) or not all(_fixed(image, v) for v in option.values):
        return False
    if not all(_byte_set(image, b) for b in option.bytes_):
        return False
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
    for p in option.patches:
        if not image.is_mapped(p.site, 4):
            raise GameOptionError(f'{option.title}: 0x{p.site:08X} is not in this main.dol')
        word = image.u32(p.site)
        if word not in (p.stock, p.new):
            raise GameOptionError(f'{option.title}: 0x{p.site:08X} holds 0x{word:08X}, expected the stock '
                                  f'0x{p.stock:08X}; another patch changed it')
    for f in option.floors:
        _floor_cells(image, f)              # raises when the table cannot be found
    for v in option.values:
        _value_cells(image, v)
    for b in option.bytes_:
        _byte_cells(image, b)


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
    # only options with stubs need the DOL hammerspace; plain patches keep the stock memory layout
    hs = dol_hammerspace.DolHammerspace.open_or_create(image) if any(o.hooks for o in todo) else None
    for option in todo:
        for h in option.hooks:
            at = hs.code.here + (-hs.code.here % 4)
            if hs.code.put(h.stub(at, h.site).assemble(), 4) != at:
                raise GameOptionError(f'{option.title}: stub placement moved')
            image.write_word(h.site, one(h.site, lambda a, t=at: a.b(t)))
        for p in option.patches:
            image.write_word(p.site, p.new)
    if hs is not None:
        hs.commit()
    for option in todo:     # floors after the commit: a roster's moved tables are in the data section it rewrites
        sites = [f'0x{s:08X}' for s in [h.site for h in option.hooks] + [p.site for p in option.patches]]
        for f in option.floors:
            raised = []
            for cid, a in _floor_cells(image, f):
                if _f32(image, a) < f.value:
                    image.write(a, struct.pack('>f', f.value))
                    raised.append(f'0x{cid:02X}')
            sites.append(f'{f.table} rows raised to {f.value:g}: {", ".join(raised) or "none"}')
        for v in option.values:
            cells = _value_cells(image, v)
            for _cid, value, a in cells:
                image.write(a, value)
            sites.append(f'{v.table} +0x{v.offset:X} set for ' + (', '.join(f'0x{cid:02X}' for cid, _v, _a in cells)
                                                                   or 'none'))
        for b in option.bytes_:
            cells = _byte_cells(image, b)
            for _cid, a in cells:
                image.write(a, bytes([b.value]))
            where = f'+0x{b.offset:X}' + (' + own ID' if b.diagonal else '')
            sites.append(f'{b.table} {where} = {b.value} for {len(cells)} characters')
        log.append(f'{option.title}: on (' + ', '.join(sites) + ')')
    return log


def _lower(image: dolfile.DolImage, floor: Floor, vanilla: dolfile.DolImage | None) -> bool:
    """Put back the roster's own value (``_baseline``: what a roster run writes without stat edits) in every row that
    holds the floor and whose own value is below it. A row a stat edit set to exactly the floor goes back too."""
    own = _baseline(image, vanilla, floor.table)
    lowered = False
    for cid, a in _floor_cells(image, floor):
        if _f32(image, a) == floor.value and struct.unpack('>f', own(cid, floor.offset))[0] < floor.value:
            image.write(a, own(cid, floor.offset))
            lowered = True
    return lowered


def _unfix(image: dolfile.DolImage, fix: 'RowValue', vanilla: dolfile.DolImage | None) -> bool:
    """Put back the roster's own value (``_baseline``) in every fixed row that still holds the fix."""
    own = _baseline(image, vanilla, fix.table)
    unfixed = False
    for cid, value, a in _value_cells(image, fix):
        if image.read(a, 4) == value and own(cid, fix.offset) != value:
            image.write(a, own(cid, fix.offset))
            unfixed = True
    return unfixed


def _unset(image: dolfile.DolImage, fix: RowByte, vanilla: dolfile.DolImage | None) -> bool:
    """Put back the roster's own byte (``_baseline``) in every row that still holds the fix's value."""
    own = _baseline(image, vanilla, fix.table)
    unset = False
    for cid, a in _byte_cells(image, fix):
        stock = own(cid, fix.offset + (cid if fix.diagonal else 0))[:1]
        if image.read(a, 1)[0] == fix.value and stock[0] != fix.value:
            image.write(a, stock)
            unset = True
    return unset


def _unhook(image: dolfile.DolImage, option: Option, vanilla: dolfile.DolImage | None = None) -> bool:
    if option.group and not is_on(image, option.key) and not _group_outdated(image, option):
        return False    # the shared sites hold another option of the group
    hooked = [h for h in option.hooks if _hooked(image, h)]
    for h in hooked:
        image.write_word(h.site, h.stock)
    patched = [p for p in option.patches if _patched(image, p)]
    for p in patched:
        image.write_word(p.site, p.stock)
    lowered = [f for f in option.floors if _lower(image, f, vanilla)]
    unfixed = [v for v in option.values if _unfix(image, v, vanilla)]
    unset = [b for b in option.bytes_ if _unset(image, b, vanilla)]
    return bool(hooked or patched or lowered or unfixed or unset)


def remove(image: dolfile.DolImage, keys, vanilla: dolfile.DolImage | None = None) -> list[str]:
    """Turn off the options in ``keys``: their hook sites get the stock words back, their floored rows the roster's
    own values (from ``vanilla``, ``1_Input/main.dol``). Without a roster (sections holding only option stubs) the
    sections are rebuilt with the stubs of the options still on, or dropped with the arena start back to stock when
    none is; with a roster the old stubs stay until the next roster run."""
    log = [f'{_option(key).title}: ' + ('off' if _unhook(image, _option(key), vanilla) else 'already off')
           for key in keys]
    hs = dol_hammerspace.DolHammerspace.open(image)
    if hs is None or hs.has_data:
        return log
    still_on = [key for key in detect(image) if BY_KEY[key].hooks]     # plain patches don't live in the sections
    for key in still_on:
        _unhook(image, BY_KEY[key])
    dol_hammerspace.remove_sections(image)
    if still_on:
        apply(image, still_on)
        log.append('unused option stubs removed (the options still on were rebuilt)')
    else:
        log.append('no option is on: the added DOL sections are removed and the arena start is stock again')
    return log
