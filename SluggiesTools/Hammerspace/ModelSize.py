"""Size of a patched model in the output dt_na.dat, and whether its player still fits the player memory.

Verification tool, like CompareBlocks.py. A ``.sluggie`` is named after the original block's offset in the
INPUT dt_na.dat (``272147520_tiny_kong.gpl.sluggie``); that offset finds the model's directory record in the
INPUT main.dol, and the same record in the OUTPUT main.dol gives where the patched block now lives and its
length. Reported per model:

  * the record's length and offset in the output (hammerspace when past ``BASE_SIZE``)
  * the original length, and the debug dumps in ``3_Output_Dat/SluggDebugg/`` (``writeDebugDumps``) when present
  * for a character directory: High + Low + largest bat/glove against the output DOL's player heap
    (``game_memory``, the same rule as the Maintenance tab's "Player memory" check)

Usage:
  py SluggiesTools/Hammerspace/ModelSize.py <name or path of a .sluggie> [...]
  e.g. py SluggiesTools/Hammerspace/ModelSize.py 272147520_tiny_kong.gpl.sluggie
"""

import os
import re
import sys

_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import slogger as _slogger
_slogger.configure()

import game_memory
from Hammerspace import HammerspaceHelper as hh
from Roster import dat_hammerspace

DEBUG_DIR = os.path.join(os.path.dirname(hh.OUTPUT_DAT), 'SluggDebugg')
FILE_NAMES = {game_memory.HIGH: 'High', game_memory.LOW: 'Low'}


def original_offset(name: str) -> int:
    match = re.match(r'(\d+)_', os.path.basename(name))
    if not match:
        raise SystemExit(f'{name}: the name does not start with the original block offset (e.g. 272147520_...)')
    return int(match.group(1))


def find_record(image, offset: int) -> tuple[int, int]:
    """``(directory, file index)`` of the record whose English slot points at ``offset``."""
    for chunk, index, _address, words in dat_hammerspace.iter_records(image):
        if dat_hammerspace.slot(list(words), 'en')[0] == offset:
            return chunk, index
    raise SystemExit(f'no INPUT main.dol record points at offset {offset} (0x{offset:X})')


def output_slot(image, directory: int, index: int) -> tuple[int, int]:
    for chunk, i, _address, words in dat_hammerspace.iter_records(image):
        if (chunk, i) == (directory, index):
            offset, length, _alloc = dat_hammerspace.slot(list(words), 'en')
            return offset, length
    raise SystemExit(f'directory {directory} file {index} is missing from the OUTPUT main.dol')


def debug_sizes(sluggie_name: str) -> dict[str, int]:
    out = {}
    for kind in ('Original', 'Hammerspace'):
        path = os.path.join(DEBUG_DIR, f'{sluggie_name}_{kind}.SluggDebugg')
        if os.path.exists(path):
            out[kind] = os.path.getsize(path)
    return out


def report(name: str, in_image, out_image, heap, chars, names) -> None:
    sluggie_name = os.path.basename(name)
    if not sluggie_name.endswith('.sluggie'):
        sluggie_name += '.sluggie'
    directory, index = find_record(in_image, original_offset(sluggie_name))
    _orig_offset, orig_length = output_slot(in_image, directory, index)
    offset, length = output_slot(out_image, directory, index)
    where = 'hammerspace' if offset >= hh.BASE_SIZE else 'in place'
    label = FILE_NAMES.get(index, f'file {index}')

    print(f'{sluggie_name}  ({game_memory.describe(directory, names)}, directory {directory} {label})')
    print(f'  output:   {length:>10,} bytes at 0x{offset:08X} ({where})')
    print(f'  original: {orig_length:>10,} bytes  ({length - orig_length:+,})')
    dumps = debug_sizes(sluggie_name)
    for kind, size in dumps.items():
        note = '' if kind != 'Hammerspace' or size == length else '  (differs from the output record: stale dump?)'
        print(f'  debug {kind}: {size:,} bytes{note}')

    c = chars.get(directory)
    if c is None:
        print('  not a character directory: no player memory check')
        return
    margin = heap.usable - c.need
    print(f'  player:   High {c.high:,} + Low {c.low:,} + bat/glove {c.gear:,} + {game_memory.BLOCK_OVERHEAD}'
          f' = {c.need:,} bytes')
    print(f'  heap:     {heap.size:,} ({heap.level or "stock"}), usable {heap.usable:,}  ->  '
          f'{"fits" if margin >= 0 else "OVER"} by {abs(margin):,} bytes')
    problem = game_memory.problem_text(c, heap, game_memory.describe(directory, names))
    if problem:
        print(f'  {problem}')


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    in_image = game_memory.load(hh.INPUT_DOL)
    out_image = game_memory.load(hh.OUTPUT_DOL)
    heap = game_memory.player_heap(out_image)
    chars = game_memory.characters(out_image)
    names = game_memory.folder_names(hh._ROOT)
    for name in argv:
        report(name, in_image, out_image, heap, chars, names)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
