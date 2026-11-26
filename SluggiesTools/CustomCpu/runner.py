"""Custom CPU level command line (``start.py --custom-cpu`` / ``--custom-cpu-off`` / ``--custom-cpu-save``).

  runner.py --apply FILE [--level N] [--button NAME] [--dry-run]
                         turn the custom level on with a settings file (.slgcpu), or
                         replace its settings
  runner.py --off [--dry-run]
                         turn it off (stock words and popup files back)
  runner.py --save FILE  write the deployed settings into FILE
  runner.py              say whether it is on, and its settings

``--apply`` reads one level column of the file (``settings.load``; ``--level`` 4, 5 or 6, default: the file's
only column, else 4). The button name comes from ``--button``, else the file's own ``button``, else the
deployed one, else "Custom Level". ``main.dol`` and ``dt_na.dat`` are written together (``Roster/runner.commit``).
A deploy that adds the DOL sections to a stock-layout DOL breaks Gecko code compatibility and asks first.
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
    from .. import gecko_compat
    from ..Dol import dolfile
    from ..GameOptions import game_options
    from ..Roster import datfile
    from ..Roster import runner as roster_runner
    from . import carry, engine, menu_files, settings
except ImportError:
    import gecko_compat
    from Dol import dolfile
    from GameOptions import game_options
    import datfile
    import runner as roster_runner
    from CustomCpu import carry, engine, menu_files, settings

SOURCE = 'custom-cpu'
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
OUTPUT_DIR = os.path.join(ROOT, '3_Output_Dat')
INPUT_DIR = os.path.join(ROOT, '1_Input')
WHAT = 'the custom CPU level'


class CustomCpuRunError(RuntimeError):
    pass


def _image(path: str) -> dolfile.DolImage | None:
    if not os.path.isfile(path):
        return None
    with open(path, 'rb') as f:
        return dolfile.DolImage(f.read())


def _files(output_dir: str, input_dir: str):
    dol_path = os.path.join(output_dir, 'main.dol')
    image = _image(dol_path)
    if image is None:
        raise CustomCpuRunError(f'{dol_path} is missing: run the normal pipeline first (menu [1])')
    vanilla = _image(os.path.join(input_dir, 'main.dol'))
    if vanilla is None:
        raise CustomCpuRunError(f'{os.path.join(input_dir, "main.dol")} is missing: the custom CPU level needs the '
                                'original main.dol there')
    dat_path = os.path.join(output_dir, 'dt_na.dat')
    if not os.path.isfile(dat_path):
        raise CustomCpuRunError(f'{dat_path} is missing: run the normal pipeline first (menu [1])')
    return image, vanilla, datfile.DatFile(dat_path)


def record_from_file(path: str, level: int | None = None, button: str | None = None,
                     current: engine.Record | None = None, vanilla: dolfile.DolImage | None = None) -> tuple:
    """``(Record, notes)`` for a settings file (module docstring)."""
    loaded = settings.load(path, level, settings.image_get(vanilla) if vanilla is not None else None)
    doc = settings.read_document(path)
    name = button or (doc.get('button') if isinstance(doc.get('button'), str) else None) \
        or (current.button if current is not None else None) or engine.DEFAULT_BUTTON
    problem = menu_files.name_problem(name)
    if problem:
        raise CustomCpuRunError(problem)
    return engine.Record(name, loaded.edits, loaded.extra, loaded.name), loaded.notes


def save(path: str, record: engine.Record) -> None:
    """A settings file for ``record``: the external tool's format, plus the button name."""
    doc = settings.document(record.edits, record.name or os.path.splitext(os.path.basename(path))[0], record.extra)
    doc['button'] = record.button
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def build(image: dolfile.DolImage, vanilla: dolfile.DolImage, dat, record: engine.Record | None) -> list[str]:
    """Deploy ``record`` on ``image`` / ``dat`` (None: turn the custom level off). In memory; returns log lines."""
    if record is None:
        log = carry.remove(image, dat, vanilla)
        return log + game_options.remove(image, [])          # drops or rebuilds sections that hold no roster
    log = carry.apply(image, dat, record, vanilla)
    odd = menu_files.unproven(record.button)
    if odd:
        log.append(f'custom CPU level: {" ".join(odd)} was never seen in the game; the button name may show gaps '
                   'there')
    return log


def run(output_dir: str = OUTPUT_DIR, record: engine.Record | None = None, dry_run: bool = False,
        input_dir: str = INPUT_DIR) -> tuple[bytes, bytes, list[str]]:
    """``build`` on the files of ``output_dir``, then write them (not with ``dry_run``). Returns ``(main.dol
    before, after, log)``. A change that breaks Gecko code compatibility asks first; cancelled, it raises."""
    image, vanilla, dat = _files(output_dir, input_dir)
    current = image.to_bytes()
    log = build(image, vanilla, dat, record)
    data = image.to_bytes()
    if dry_run:
        return current, data, log
    if gecko_compat.breaks(current, data) and not gecko_compat.confirm(WHAT):
        raise CustomCpuRunError('cancelled: the custom CPU level would break Gecko code compatibility; nothing '
                                'was written')
    if data != current or dat.pending or dat.grown:
        try:
            log += roster_runner.commit(output_dir, data, dat, None)
        except roster_runner.RosterDevError as exc:
            raise CustomCpuRunError(str(exc)) from exc
    return current, data, log


def status(output_dir: str = OUTPUT_DIR) -> list[str]:
    image = _image(os.path.join(output_dir, 'main.dol'))
    if image is None:
        return [f'{os.path.join(output_dir, "main.dol")} is missing']
    record = engine.detect(image)
    if record is None:
        return ['custom CPU level: off']
    return ([f'custom CPU level: on, button "{record.button}"'
             + (f', settings "{record.name}"' if record.name else '')]
            + [f'custom CPU setting: {line}' for line in settings.describe(record.edits)])


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='The custom CPU level of 3_Output_Dat (a 5th CPU level button).')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', metavar='FILE', help='turn the custom level on with this settings file (.slgcpu)')
    mode.add_argument('--off', action='store_true', help='turn the custom level off')
    mode.add_argument('--save', metavar='FILE', help='write the deployed settings into FILE')
    parser.add_argument('--level', type=int, choices=settings.LEVELS, help='--apply: the file\'s level column')
    parser.add_argument('--button', help='--apply: the button name (1-25 characters)')
    parser.add_argument('--dry-run', action='store_true', help='run in memory, write nothing')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help=argparse.SUPPRESS)
    parser.add_argument('--input-dir', default=INPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if (args.level or args.button) and not args.apply:
        parser.error('--level and --button go with --apply')
    try:
        if args.save:
            image = _image(os.path.join(args.output_dir, 'main.dol'))
            record = engine.detect(image) if image is not None else None
            if record is None:
                raise CustomCpuRunError('the custom CPU level is off: nothing to save')
            save(args.save, record)
            slogger.info(f'custom CPU settings saved to {args.save}', source=SOURCE)
            return 0
        if not args.apply and not args.off:
            for line in status(args.output_dir):
                slogger.info(line, source=SOURCE)
            return 0
        record = None
        if args.apply:
            deployed = engine.detect(_files(args.output_dir, args.input_dir)[0])
            record, notes = record_from_file(os.path.abspath(args.apply), args.level, args.button, deployed,
                                             _image(os.path.join(args.input_dir, 'main.dol')))
            for note in notes:
                slogger.info(note, source=SOURCE)
        current, data, log = run(args.output_dir, record, args.dry_run, args.input_dir)
        if args.dry_run and gecko_compat.breaks(current, data):
            gecko_compat.record(os.path.dirname(os.path.abspath(args.output_dir)), WHAT)
    except (RuntimeError, ValueError, OSError) as exc:
        slogger.error(f'refused, nothing written: {exc}', source=SOURCE)
        return 1
    for line in log:
        slogger.info(line, source=SOURCE)
    if args.dry_run:
        slogger.info('dry run: nothing written', source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
