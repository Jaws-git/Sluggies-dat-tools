"""Game options (``start.py --game-options``, StartTools menu [8]): turn options on or off in 3_Output_Dat/main.dol.

  runner.py --on cpu_vs_cpu      turn an option on
  runner.py --off cpu_vs_cpu     turn it off (stock instructions back)
  runner.py                      list the options and whether each is on

The options survive roster runs (menu [7]); menu [1]'s untangle export copies
a fresh ``main.dol`` from 1_Input, so options have to be turned on again after it.
"""

import argparse
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
    from . import game_options
except ImportError:
    from Dol import dolfile
    from GameOptions import game_options

SOURCE = 'game-options'
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
OUTPUT_DIR = os.path.join(ROOT, '3_Output_Dat')


class GameOptionsRunError(RuntimeError):
    pass


def run(output_dir: str = OUTPUT_DIR, on=(), off=(), dry_run: bool = False) -> list[str]:
    dol_path = os.path.join(output_dir, 'main.dol')
    if not os.path.isfile(dol_path):
        raise GameOptionsRunError(f'{dol_path} is missing: run the normal pipeline first (menu [1])')
    both = set(on) & set(off)
    if both:
        raise GameOptionsRunError(f'turned both on and off: {", ".join(sorted(both))}')
    with open(dol_path, 'rb') as f:
        current = f.read()
    image = dolfile.DolImage(current)
    log = game_options.remove(image, off) + game_options.apply(image, on)
    for option in game_options.OPTIONS:
        log.append(f'{option.key}: {"on" if game_options.is_on(image, option.key) else "off"} - {option.title}')
    data = image.to_bytes()
    if data != current and not dry_run:
        tmp = dol_path + '.options_tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, dol_path)
    return log


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Game options in 3_Output_Dat/main.dol (menu [8]).')
    parser.add_argument('--on', nargs='+', default=[], metavar='OPTION', help='turn these options on')
    parser.add_argument('--off', nargs='+', default=[], metavar='OPTION', help='turn these options off')
    parser.add_argument('--dry-run', action='store_true', help='run in memory, write nothing')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        log = run(args.output_dir, args.on, args.off, args.dry_run)
    except (RuntimeError, ValueError) as exc:
        slogger.error(str(exc), source=SOURCE)
        return 1
    for line in log:
        slogger.info(line, source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
