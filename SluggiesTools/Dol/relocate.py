"""Move a DOL data table by rewriting the ``lis``/low-half pairs that build its address.

Every pair keeps its offset into the table: a pair that built ``old + k``
builds ``new + k`` afterwards. The pairs come from the site inventory
(``site_inventory.json``: ``dol_xrefs`` checked against the external tool's
Ghidra pairs, plus ``extra_pairs`` no scan finds).

Refused:

* a pair whose words don't build an address inside the old table (the code
  changed since the inventory was made, or the table was already moved);
* a ``lis`` shared with an address outside the table (rewriting its high half
  would move that other address too);
* a ``lis`` whose uses inside the table would need two different high halves.
"""

from dataclasses import dataclass

try:
    from . import dolfile
    from .ppc import ha, lo, signed16
except ImportError:
    import dolfile
    from ppc import ha, lo, signed16

LIS_OPCODE = 15
ORI_OPCODE = 24


class RelocationError(dolfile.DolError):
    pass


@dataclass(frozen=True)
class WordChange:
    address: int
    old: int
    new: int


def _lis_parts(word: int, site: int) -> tuple[int, int]:
    if word >> 26 != LIS_OPCODE or (word >> 16) & 31:
        raise RelocationError(f'0x{site:08X}: {word:08X} is not lis')
    return (word >> 21) & 31, word & 0xFFFF


def pair_address(image: dolfile.DolImage, lis_site: int, low_site: int) -> int:
    """The address a lis/low pair builds in ``image``."""
    _reg, high = _lis_parts(image.u32(lis_site), lis_site)
    low_word = image.u32(low_site)
    if low_word >> 26 == ORI_OPCODE:
        return (high << 16) | (low_word & 0xFFFF)
    return ((high << 16) + signed16(low_word)) & 0xFFFFFFFF


def scan_refs(image: dolfile.DolImage) -> list[tuple[int, int, int]]:
    """Every lis/low pair in ``image``'s code as ``(lis_site, low_site, address)`` (about a second)."""
    try:
        from ..Icons import dol_xrefs
    except ImportError:
        import os
        import sys
        icons = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Icons')
        if icons not in sys.path:
            sys.path.insert(0, icons)
        import dol_xrefs
    refs = dol_xrefs.find_address_refs(bytes(image.data), image.header, 0x80000000, 0x81800000)
    return [(r.lis_site, r.low_site, r.value) for r in refs]


def relocate_table(image: dolfile.DolImage, pairs, old: int, length: int, new: int,
                   other_refs=()) -> list[WordChange]:
    """Point every pair into ``[old, old + length)`` at the same offset from ``new``.

    ``pairs``: ``(lis_site, low_site)`` tuples. ``other_refs``: every known
    pair of the whole DOL as ``(lis_site, low_site, address)``, used to refuse
    a ``lis`` that also builds an address outside the table.
    """
    pairs = sorted(set(pairs))
    lis_sites = {lis_site for lis_site, _ in pairs}
    for lis_site, low_site, address in other_refs:
        if lis_site in lis_sites and not old <= address < old + length and (lis_site, low_site) not in pairs:
            raise RelocationError(f'lis 0x{lis_site:08X} also builds 0x{address:08X} (0x{low_site:08X}), '
                                  f'outside the table at 0x{old:08X}')
    highs: dict[int, int] = {}
    lows: list[WordChange] = []
    for lis_site, low_site in pairs:
        address = pair_address(image, lis_site, low_site)
        if not old <= address < old + length:
            raise RelocationError(f'pair 0x{lis_site:08X}/0x{low_site:08X} builds 0x{address:08X}, '
                                  f'not an address in 0x{old:08X}+0x{length:X}')
        target = new + (address - old)
        low_word = image.u32(low_site)
        if low_word >> 26 == ORI_OPCODE:
            new_low, high = (low_word & 0xFFFF0000) | (target & 0xFFFF), target >> 16
        else:
            new_low, high = (low_word & 0xFFFF0000) | lo(target), ha(target)
        if highs.setdefault(lis_site, high) != high:
            raise RelocationError(f'lis 0x{lis_site:08X} would need two high halves')
        lows.append(WordChange(low_site, low_word, new_low))
    changes = list(lows)
    for lis_site, high in highs.items():
        word = image.u32(lis_site)
        changes.append(WordChange(lis_site, word, (word & 0xFFFF0000) | high))
    for change in changes:
        image.write_word(change.address, change.new)
    return sorted(changes, key=lambda c: c.address)
