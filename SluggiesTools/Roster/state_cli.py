"""``start.py --roster-state``: read the draft grid from 3_Output_Dat for the GUI's character grid.

Writes ``3_Output_Dat/_gui/roster_state.json`` (``state.read_state``) and
logs the grid as text, one line per row. Reads only; never writes the game
files.
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
    from ..Dol import dolfile
    from . import datfile, state
except ImportError:
    from Dol import dolfile
    import datfile
    import state

SOURCE = 'roster.state'
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
OUTPUT_DIR = os.path.join(ROOT, '3_Output_Dat')
GUI_DIR = '_gui'
STATE_FILE = 'roster_state.json'


def state_path(output_dir: str = OUTPUT_DIR) -> str:
    return os.path.join(output_dir, GUI_DIR, STATE_FILE)


def run(output_dir: str = OUTPUT_DIR) -> dict:
    dol_path = os.path.join(output_dir, 'main.dol')
    dat_path = os.path.join(output_dir, 'dt_na.dat')
    if not os.path.isfile(dol_path):
        raise state.StateError(f'{dol_path} is missing: run menu [1] (or the All-In-One export) first')
    with open(dol_path, 'rb') as f:
        image = dolfile.DolImage(f.read())
    dat = datfile.DatFile(dat_path) if os.path.isfile(dat_path) else None
    result = state.read_state(image, dat)
    path = state_path(output_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return result


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Read the draft grid from 3_Output_Dat (GUI character grid).')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        result = run(args.output_dir)
    except (RuntimeError, ValueError) as exc:
        slogger.error(str(exc), source=SOURCE)
        return 1
    cols, rows = result['shape']
    slogger.info(f'{result["kind"]} grid {cols}x{rows}: {len(result["squares"])} squares, '
                 f'{len(result["characters"])} characters'
                 + ('' if result['names_read'] else ' (no names: dt_na.dat missing)'), source=SOURCE)
    for line in state.text_grid(result):
        slogger.info(line, source=SOURCE)
    for warning in result['warnings']:
        slogger.warning(warning, source=SOURCE)
    slogger.info(f'state written to {os.path.relpath(state_path(args.output_dir), ROOT)}', source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
