"""Stat editor bridge command line (``start.py --stat-bridge-export FILE [--focus 0xNN]``).

  cli.py --export FILE [--focus 0xNN]   write the bridge for 3_Output_Dat to FILE

Reads ``3_Output_Dat/main.dol`` (+ ``dt_na.dat`` for names) and
``1_Input/main.dol`` (the baseline rows); never writes the game files.
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
for _path in (_TOOLS_DIR, os.path.join(_TOOLS_DIR, 'Roster')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import slogger  # noqa: E402

try:
    from ..Dol import dolfile
    from ..Roster import datfile
    from . import bridge
except ImportError:
    from Dol import dolfile
    from Roster import datfile
    from StatEditor import bridge

SOURCE = 'stat-bridge'
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
OUTPUT_DIR = os.path.join(ROOT, '3_Output_Dat')
INPUT_DIR = os.path.join(ROOT, '1_Input')


def _read_dol(path: str, what: str) -> dolfile.DolImage:
    if not os.path.isfile(path):
        raise bridge.BridgeError(f'{path} is missing: {what}')
    with open(path, 'rb') as f:
        return dolfile.DolImage(f.read())


def export(path: str, output_dir: str = OUTPUT_DIR, input_dir: str = INPUT_DIR, focus: int | None = None) -> dict:
    """Write the bridge for ``output_dir``'s game files to ``path``; returns it."""
    dol_path = os.path.abspath(os.path.join(output_dir, 'main.dol'))
    dat_path = os.path.abspath(os.path.join(output_dir, 'dt_na.dat'))
    image = _read_dol(dol_path, 'run the normal pipeline first (menu [1])')
    vanilla = _read_dol(os.path.join(input_dir, 'main.dol'), 'the baseline needs the original main.dol in 1_Input')
    dat = datfile.DatFile(dat_path) if os.path.isfile(dat_path) else None
    result = bridge.build(image, vanilla, dat, dol_path=dol_path, dat_path=dat_path if dat else '', focus=focus)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return result


def _id(text: str) -> int:
    try:
        value = int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{text!r} is not an ID (e.g. 0x66)') from None
    if not 0 <= value <= 0xFE:
        raise argparse.ArgumentTypeError(f'{text} is outside 0x00-0xFE')
    return value


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Sluggers Stat Editor bridge for 3_Output_Dat.')
    parser.add_argument('--export', required=True, metavar='FILE', help='write stat_bridge.json to FILE')
    parser.add_argument('--focus', type=_id, metavar='0xNN', help='the character the editor preselects')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help=argparse.SUPPRESS)
    parser.add_argument('--input-dir', default=INPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        result = export(args.export, args.output_dir, args.input_dir, args.focus)
    except (RuntimeError, ValueError, OSError) as exc:
        slogger.error(str(exc), source=SOURCE)
        return 1
    moved = [n for n, t in result['tables'].items() if t['moved']]
    new = sum(1 for c in result['characters'] if c['kind'] == 'new')
    slogger.info(f'{len(result["characters"])} characters ({new} new); per-ID tables '
                 + (f'moved ({result["tables"]["stats"]["rows"]} rows)' if moved else 'in place (101 rows)')
                 + f'; baseline rows for {len(set(result["baseline"]) - {"new_x_new"})} tables'
                 + (', new x new chemistry matrix' if result['chemistry']['new_x_new'] else ''), source=SOURCE)
    if not result['files']['dt_na_dat']:
        slogger.warning('dt_na.dat is missing: the bridge has no character names', source=SOURCE)
    slogger.info(f'bridge written to {args.export}', source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
