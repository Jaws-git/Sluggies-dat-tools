"""``start.py --export-autopicker FILE``: the output game's character names for the MSS-AutoTeam autopicker.

The autopicker (``_Sluggers Characters Beta External Tool/Autopicker Source``) keys its teams by character ID:
its character list holds one name per ID, and every ID from the list's length up is one of its Miis. Its stock
list ends at 0x4C, so the new IDs of a roster expansion (0x66 and up) counted as Miis; its patched source reads
an optional ``characters.json`` beside its ``options.json`` instead. This writes that file from 3_Output_Dat:
the English name of every character the grid offers (``state.read_state``, the off-grid Luigi family included),
one entry per ID up to the highest, an empty string for the IDs in between (the autopicker hides those). The
autopicker looks names up, so two characters sharing a name get their ID appended. Reads only.
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
for _path in (_TOOLS_DIR, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import slogger  # noqa: E402

try:
    from . import state, state_cli
except ImportError:
    import state
    import state_cli

SOURCE = 'roster.autopicker'


def character_names(roster: dict) -> list[str]:
    """One name per ID from 0x00 to the highest ID the roster offers; '' for IDs it does not."""
    by_id = {c['id']: c for c in roster['characters']}
    names = {cid: state.display_name(c) for cid, c in by_id.items()}
    holders: dict[str, list[int]] = {}
    for cid, name in names.items():
        holders.setdefault(name, []).append(cid)
    for name, cids in holders.items():
        if len(cids) > 1:
            for cid in cids:
                names[cid] = f'{name} (0x{cid:02X})'
    top = max(by_id) if by_id else -1
    return [names.get(cid, '') for cid in range(top + 1)]


def write(path: str, output_dir: str = state_cli.OUTPUT_DIR) -> list[str]:
    """Write the autopicker's ``characters.json`` for the output game to ``path``; returns the list."""
    image, dat = state_cli._open(output_dir)
    roster = state.read_state(image, dat)
    if not roster.get('names_read', True):
        slogger.warning('no names read (dt_na.dat missing): the list holds IDs only', source=SOURCE)
    chars = character_names(roster)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'characters': chars}, f, ensure_ascii=False, indent=1)
    return chars


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description="Write the MSS-AutoTeam autopicker's characters.json for 3_Output_Dat.")
    parser.add_argument('--out', required=True, metavar='FILE', help='where to write characters.json')
    parser.add_argument('--output-dir', default=state_cli.OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        chars = write(args.out, args.output_dir)
    except (RuntimeError, ValueError, OSError) as exc:
        slogger.error(str(exc), source=SOURCE)
        return 1
    offered = sum(1 for c in chars if c)
    slogger.info(f'{offered} characters (IDs up to 0x{len(chars) - 1:02X}, {len(chars) - offered} hidden) written '
                 f'to {args.out}; Mii IDs start at {len(chars)}', source=SOURCE)
    return 0


if __name__ == '__main__':
    sys.exit(main())
