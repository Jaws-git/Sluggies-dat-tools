"""Hand-run probe: the 2D layout writer round-trips every layout bank of the game.

Reads ``1_Input/main.dol`` and ``1_Input/dt_na.dat`` (so it is a probe, not a
test). For every distinct dt_na.dat range that any directory record (any
language slot) points at and that parses as a 2D layout bank, it checks that
``layout2d.Layout(data).to_bytes() == data``, and that rebuilding every
element from its own nodes (``set_nodes(k, node_blobs(k))``) also gives the
input back. Prints one summary line and every failure.

    python SluggiesTools/probe_layout_roundtrip.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import slogger  # noqa: E402
from Hammerspace import HammerspaceHelper as hh  # noqa: E402
from Icons import layout2d  # noqa: E402

SOURCE = 'probe.layout_roundtrip'


def main() -> int:
    slogger.configure()
    ranges = set()
    for _cidx, _fidx, _record, words in hh._iterDirRecords(hh.INPUT_DOL):
        for length_word, offset_word in ((1, 2), (5, 6), (9, 10)):
            if words[length_word] >= 0x24:
                ranges.add((words[offset_word], words[length_word]))
    banks = elements = failures = 0
    with open(hh.INPUT_DAT, 'rb') as dat:
        for offset, length in sorted(ranges):
            dat.seek(offset)
            data = dat.read(length)
            try:
                layout2d.parse_bank(data)
            except layout2d.Layout2dError:
                continue
            except Exception:  # noqa: BLE001 - a non-bank that happens to start with 0x20
                continue
            try:
                lay = layout2d.Layout(data)
            except layout2d.Layout2dError as exc:
                failures += 1
                slogger.error(f'0x{offset:08X}+0x{length:X}: does not parse as an editable bank: {exc}', source=SOURCE)
                continue
            banks += 1
            if lay.to_bytes() != data:
                failures += 1
                slogger.error(f'0x{offset:08X}+0x{length:X}: to_bytes differs from the input', source=SOURCE)
                continue
            for index in range(len(lay.elements)):
                lay.set_nodes(index, lay.node_blobs(index))
                elements += 1
            if lay.to_bytes() != data:
                failures += 1
                slogger.error(f'0x{offset:08X}+0x{length:X}: rebuilding the elements from their nodes changes '
                              'the bank', source=SOURCE)
    slogger.info(f'{banks} layout banks, {elements} elements rebuilt, {failures} failures', source=SOURCE)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
