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
    from .. import game_memory, gecko_compat, memory_guard
    from ..Dol import dolfile
    from . import game_options
except ImportError:
    import game_memory
    import gecko_compat
    import memory_guard
    from Dol import dolfile
    from GameOptions import game_options

SOURCE = 'game-options'
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
OUTPUT_DIR = os.path.join(ROOT, '3_Output_Dat')


class GameOptionsRunError(RuntimeError):
    pass


def build(output_dir: str = OUTPUT_DIR, on=(), off=(), input_dir: str | None = None) -> tuple[bytes, bytes, list[str]]:
    """``(current, new, log)``: ``main.dol`` before and after the change, built in memory. ``input_dir`` (default:
    ``1_Input`` beside ``output_dir``) holds the stock ``main.dol`` that turning a floor option off reads."""
    dol_path = os.path.join(output_dir, 'main.dol')
    if not os.path.isfile(dol_path):
        raise GameOptionsRunError(f'{dol_path} is missing: run the normal pipeline first (menu [1])')
    both = set(on) & set(off)
    if both:
        raise GameOptionsRunError(f'turned both on and off: {", ".join(sorted(both))}')
    with open(dol_path, 'rb') as f:
        current = f.read()
    image = dolfile.DolImage(current)
    vanilla_path = os.path.join(input_dir or os.path.join(os.path.dirname(os.path.abspath(output_dir)), '1_Input'),
                                'main.dol')
    vanilla = None
    if os.path.isfile(vanilla_path):
        with open(vanilla_path, 'rb') as f:
            vanilla = dolfile.DolImage(f.read())
    log = game_options.remove(image, off, vanilla) + game_options.apply(image, on)
    on_now = game_options.detect(image)
    for option in game_options.OPTIONS:          # a group's levels: only the one that is on, or one line
        if option.group is None or option.key in on_now:
            log.append(f'{option.key}: {"on" if option.key in on_now else "off"} - {option.title}')
    off_groups = {o.group for o in game_options.OPTIONS if o.group} - {game_options.BY_KEY[k].group for k in on_now}
    log += [f'{group}: off (every level)' for group in sorted(off_groups)]
    return current, image.to_bytes(), log


def memory_problems(output_dir: str, current: bytes, data: bytes) -> list[str]:
    """Lines for the characters a lower player memory level leaves without room (``memory_guard``)."""
    return memory_guard.option_problems(current, data, game_memory.folder_names(os.path.dirname(
        os.path.abspath(output_dir))))


def run(output_dir: str = OUTPUT_DIR, on=(), off=(), dry_run: bool = False, input_dir: str | None = None) -> list[str]:
    """Turn options on / off. A change that breaks Gecko code compatibility (``gecko_compat``), or a lower player
    memory level some character no longer fits (``memory_guard``), asks first; cancelled, it raises
    GameOptionsRunError and writes nothing."""
    current, data, log = build(output_dir, on, off, input_dir)
    if not dry_run and gecko_compat.breaks(current, data) and not gecko_compat.confirm('the game options'):
        raise GameOptionsRunError('cancelled: the game options would break Gecko code compatibility; '
                                      'nothing was written')
    over = memory_problems(output_dir, current, data) if data != current else []
    if over and not dry_run and not memory_guard.confirm('the player memory level', over):
        raise GameOptionsRunError('cancelled: a character would not fit the lower player memory; nothing was '
                                  'written')
    if data != current and not dry_run:
        dol_path = os.path.join(output_dir, 'main.dol')
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
        if args.dry_run:
            root = os.path.dirname(os.path.abspath(args.output_dir))
            current, data = build(args.output_dir, args.on, args.off)[:2]
            if gecko_compat.breaks(current, data):
                gecko_compat.record(root, 'the game options')
            over = memory_problems(args.output_dir, current, data)
            if over:
                memory_guard.record(root, 'the player memory level', [{'text': line} for line in over])
    except (RuntimeError, ValueError) as exc:
        slogger.error(str(exc), source=SOURCE)
        return 1
    for line in log:
        slogger.info(line, source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
