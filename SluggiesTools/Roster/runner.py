"""The roster expansion (``start.py --roster``, StartTools menu [10]).

Runs every roster step (``steps.STEPS``, in order) with one roster
configuration (``--config``, e.g. from ``1_Input/_RosterConfigurations``) on
the files in ``3_Output_Dat`` (the normal pipeline's output).

* It first takes out the previous injection (the changes recorded in
  ``3_Output_Dat/roster_dev/report.json``), so repeated runs never stack.
* ``--remove`` only takes the previous injection out.
* ``--dry-run`` runs everything in memory and writes nothing.
"""

import argparse
import hashlib
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
for _path in (_TOOLS_DIR, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import slogger  # noqa: E402

try:
    from ..Dol import dolfile
    from . import dol_hammerspace, ledger, steps
except ImportError:
    from Dol import dolfile
    import dol_hammerspace
    import ledger
    import steps

SOURCE = 'roster'
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
OUTPUT_DIR = os.path.join(ROOT, '3_Output_Dat')
REPORT_NAME = os.path.join('roster_dev', 'report.json')
REPORT_VERSION = 1
INPUT_FST = os.path.join(ROOT, '1_Input', 'fst.bin')
FST_DAT_SIZE_OFFSET = 1 * 12 + 8       # dt_na.dat is FST entry 1; its size word (as HammerspaceHelper)


class RosterDevError(RuntimeError):
    pass


def load_config(path: str | None) -> tuple[dict, str]:
    if not path:
        raise RosterDevError('no roster configuration given (--config PATH, e.g. a file in 1_Input/_RosterConfigurations)')
    if not os.path.isfile(path):
        raise RosterDevError(f'roster configuration not found: {path}')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f), path
    except ValueError as exc:
        raise RosterDevError(f'{path} is not valid JSON: {exc}') from exc


def _display_path(path: str) -> str:
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:  # another drive
        return path


def _read_report(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            report = json.load(f)
    except ValueError as exc:
        raise RosterDevError(
            f'{path} is unreadable ({exc}), probably cut off by an interrupted run, so the previous injection '
            'cannot be taken out. Restore 3_Output_Dat from the normal pipeline (or clean copies of main.dol, '
            'dt_na.dat and fst.bin), delete that report.json, then run the roster expansion again.') from exc
    if report.get('version') != REPORT_VERSION:
        raise RosterDevError(f'{path}: unknown report version {report.get("version")!r}')
    return report


def remove_previous(report: dict | None, dol: bytearray, dat: ledger.DatFile | None) -> list[str]:
    """Undo a previous run's steps (newest first). Returns log lines.

    A file the normal pipeline regenerated since (back to the bytes the run started from) counts as
    already undone as a whole, before any per-step check."""
    if not report:
        return []
    steps_ = report['steps']
    dol_done = ledger.run_already_undone(bytes(dol), [s['dol'] for s in steps_])
    dat_run = report.get('dat_run')                       # one record for the run (older reports: per step)
    dat_lists = [s['dat'] for s in steps_ if s.get('dat')]
    if (dat_run or dat_lists) and dat is None:
        raise RosterDevError('the previous injection changed dt_na.dat, which is missing now')
    log = []
    if dat_run:
        log.append(f'removed previous dt_na.dat writes: {dat.undo_run(dat_run)}')
    dat_done = bool(dat_lists) and dat.run_already_undone(dat_lists)
    for step in reversed(steps_):
        outcome = 'already undone' if dol_done else ledger.undo_diff(dol, step['dol'])
        if step.get('dat'):
            dat_outcome = 'already undone' if dat_done else dat.undo(step['dat'])
            if dat_outcome != outcome:
                outcome = f'{outcome} (dt_na.dat: {dat_outcome})'
        log.append(f'removed previous step {step["key"]}: {outcome}')
    return log


def patch_fst(output_dir: str, dat_size: int) -> str:
    """Write the grown ``dt_na.dat`` size into the output ``fst.bin`` (copied from 1_Input first if missing)."""
    fst = os.path.join(output_dir, 'fst.bin')
    if not os.path.isfile(fst):
        if not os.path.isfile(INPUT_FST):
            return f'fst.bin not found: the disc file table still has the old dt_na.dat size (0x{dat_size:X} needed)'
        with open(INPUT_FST, 'rb') as src, open(fst, 'wb') as dst:
            dst.write(src.read())
    with open(fst, 'r+b') as f:
        f.seek(FST_DAT_SIZE_OFFSET)
        old = int.from_bytes(f.read(4), 'big')
        if old >= dat_size:
            return f'fst.bin: dt_na.dat size 0x{old:X} already covers it'
        f.seek(FST_DAT_SIZE_OFFSET)
        f.write(dat_size.to_bytes(4, 'big'))
    return f'fst.bin: dt_na.dat size 0x{old:X} -> 0x{dat_size:X}'


def run(output_dir: str = OUTPUT_DIR, config_path: str | None = None, remove_only: bool = False,
        dry_run: bool = False) -> dict:
    dol_path = os.path.join(output_dir, 'main.dol')
    dat_path = os.path.join(output_dir, 'dt_na.dat')
    report_path = os.path.join(output_dir, REPORT_NAME)
    if not os.path.isfile(dol_path):
        raise RosterDevError(f'{dol_path} is missing: run the normal pipeline first (menu [1], icons, '
                             'model patches); the roster expansion builds on its output')
    with open(dol_path, 'rb') as f:
        dol_bytes = bytearray(f.read())
    dat = ledger.DatFile(dat_path) if os.path.isfile(dat_path) else None
    log = []
    try:
        previous = _read_report(report_path)
    except RosterDevError:
        # Without our DOL sections no injection is in the files (e.g. clean copies were put back): the
        # unreadable report describes nothing that is still there, so it is set aside.
        if dol_hammerspace.DolHammerspace.open(dolfile.DolImage(bytes(dol_bytes))) is not None:
            raise
        if not dry_run:
            os.replace(report_path, report_path + '.unreadable')
        log.append(f'previous report unreadable, but main.dol holds no roster injection: set aside as '
                   f'{os.path.basename(report_path)}.unreadable')
        previous = None

    log += remove_previous(previous, dol_bytes, dat)
    report = {'version': REPORT_VERSION, 'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'steps': [], 'log': []}
    if not remove_only:
        config, config_source = load_config(config_path)
        report['config'] = _display_path(config_source)
        image = dolfile.DolImage(bytes(dol_bytes))
        ctx = steps.RosterContext(dol=image, dat=dat, config=config)
        dat_writes = []
        for step in steps.all_steps():
            before = image.to_bytes()
            lines = step.apply(ctx) or []
            writes = dat.take_raw() if dat is not None else []
            dat_writes.append(writes)
            entry = {'key': step.key, 'title': step.title, 'log': list(lines),
                     'dol': ledger.diff_bytes(before, image.to_bytes()), 'dat_writes': len(writes)}
            report['steps'].append(entry)
            log += [f'[{step.key}] {line}' for line in lines]
        report['dat_run'] = dat.run_record(dat_writes) if dat is not None else []
        dol_bytes = bytearray(image.to_bytes())
    report['log'] = log
    report['dol_sha1'] = hashlib.sha1(dol_bytes).hexdigest()

    if not dry_run:
        # The new report is complete on disk before any output file changes, and swapped in last.
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        report_tmp = report_path + '.tmp'
        if not remove_only:
            with open(report_tmp, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=1)
                f.write('\n')
        tmp = dol_path + '.roster_tmp'
        with open(tmp, 'wb') as f:
            f.write(dol_bytes)
        if dat is not None:
            grown = dat.grown
            dat.flush()
            if grown:
                log.append(patch_fst(output_dir, dat.size))
        os.replace(tmp, dol_path)
        if remove_only:
            if os.path.isfile(report_path):
                os.remove(report_path)
        else:
            os.replace(report_tmp, report_path)
    return report


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Roster expansion (menu [10]).')
    parser.add_argument('--config', help='the roster configuration JSON (e.g. from 1_Input/_RosterConfigurations)')
    parser.add_argument('--remove', action='store_true', help='only take the previous injection out')
    parser.add_argument('--dry-run', action='store_true', help='run in memory, write nothing')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        report = run(args.output_dir, args.config, args.remove, args.dry_run)
    except (RuntimeError, ValueError) as exc:     # every step's errors (DolError, config errors, ...)
        slogger.error(str(exc), source=SOURCE)
        return 1
    for line in report['log']:
        slogger.info(line, source=SOURCE)
    what = 'removed' if args.remove else 'injected'
    slogger.info(f'roster expansion {what}{" (dry run, nothing written)" if args.dry_run else ""}; '
                 f'main.dol sha1 {report["dol_sha1"]}', source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
