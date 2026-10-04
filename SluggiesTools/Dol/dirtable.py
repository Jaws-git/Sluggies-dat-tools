"""The dt_na.dat directory pointer table, stock or moved by the roster (own model directories, GUI grid 4b).

The game reads the table through the ``dtna_directories`` lis/low pairs
(inventory). The stock table holds 172 directory pointers at ``0x806A0728``.
A roster with own model directories moves the table into its DOL data
section, behind a small header, and appends one pointer per new directory:

    MAGIC (8 bytes) | u32 directory count | pointers (count x u32)

``table(image)`` finds it from the pairs, so any reader (the roster steps,
``HammerspaceHelper`` on the output DOL file) sees the new directories
without the manifest. The stock table stays where it was, unchanged; only the
game stops reading it.
"""

import struct

try:
    from . import dolfile, inventory, relocate
except ImportError:
    import dolfile
    import inventory
    import relocate

NAME = 'dtna_directories'
MAGIC = b'SLGDIRS\x01'
HEADER = len(MAGIC) + 4


class DirTableError(dolfile.DolError):
    pass


def _stock() -> inventory.Table:
    return inventory.table(NAME)


def table(image: dolfile.DolImage) -> tuple[int, int]:
    """``(address, directory count)`` of the table the game reads."""
    stock = _stock()
    lis_site, low_site = stock.all_pairs[0]
    if not image.is_mapped(lis_site, 4) or not image.is_mapped(low_site, 4):
        return stock.address, stock.rows
    try:
        at = relocate.pair_address(image, lis_site, low_site)
    except relocate.RelocationError:
        return stock.address, stock.rows
    if at == stock.address:
        return at, stock.rows
    if not image.is_mapped(at - HEADER, HEADER) or image.read(at - HEADER, len(MAGIC)) != MAGIC:
        raise DirTableError(f'the directory table was moved to 0x{at:08X}, but not by this tool')
    count = image.u32(at - 4)
    if count < stock.rows:
        raise DirTableError(f'moved directory table at 0x{at:08X} lists {count} directories, fewer than stock')
    return at, count


def pointers(image: dolfile.DolImage) -> list[int]:
    """Every directory's record address, stock directories first."""
    at, count = table(image)
    return list(struct.unpack(f'>{count}I', image.read(at, 4 * count)))


def is_moved(image: dolfile.DolImage) -> bool:
    return table(image)[0] != _stock().address


def blob(pointer_list: list[int]) -> bytes:
    """The moved table with its header; the table itself starts at ``HEADER`` into it."""
    return MAGIC + struct.pack('>I', len(pointer_list)) + struct.pack(f'>{len(pointer_list)}I', *pointer_list)


def move(image: dolfile.DolImage, put, pointer_list: list[int], refs=()) -> int:
    """Store ``pointer_list`` (stock directories first) with ``put(bytes, align) -> address`` and point the
    game's pairs at it. Returns the table address."""
    stock = _stock()
    if is_moved(image):
        raise DirTableError('the directory table is already moved')
    if len(pointer_list) < stock.rows:
        raise DirTableError('a moved directory table must keep every stock directory')
    at = put(blob(pointer_list), 4) + HEADER
    relocate.relocate_table(image, stock.all_pairs, stock.address, stock.length, at, refs)
    return at
