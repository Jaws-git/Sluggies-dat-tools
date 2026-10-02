"""Development injector for the roster expansion (``start.py --roster-dev``, StartTools menu [10]).

Runs every roster-expansion step built so far, in plan order, on the files in
``3_Output_Dat`` (the normal pipeline's output), so the expansion can be
tested on its own while it is being built.

* It first takes out the previous injection (each step's recorded changes,
  newest first, from ``3_Output_Dat/roster_dev/report.json``), so repeated
  runs never stack.
* ``--remove`` only takes the previous injection out.
* ``--dry-run`` runs everything in memory and writes nothing.
* ``--config`` picks the roster preset; the default is ``1_Input/roster.json``,
  else the built-in development preset next to this file.
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
    from . import ledger, steps
except ImportError:
    from Dol import dolfile
    import ledger
    import steps

SOURCE = 'roster.dev'
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
OUTPUT_DIR = os.path.join(ROOT, '3_Output_Dat')
REPORT_NAME = os.path.join('roster_dev', 'report.json')
USER_CONFIG = os.path.join(ROOT, '1_Input', 'roster.json')
DEV_PRESET = os.path.join(_HERE, 'dev_preset.json')
REPORT_VERSION = 1


class RosterDevError(RuntimeError):
    pass


def load_config(path: str | None) -> tuple[dict, str]:
    for candidate in ([path] if path else [USER_CONFIG, DEV_PRESET]):
        if candidate and os.path.isfile(candidate):
            with open(candidate, encoding='utf-8') as f:
                return json.load(f), candidate
    raise RosterDevError(f'roster config not found: {path or USER_CONFIG}')


def _display_path(path: str) -> str:
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:  # another drive
        return path


def _read_report(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        report = json.load(f)
    if report.get('version') != REPORT_VERSION:
        raise RosterDevError(f'{path}: unknown report version {report.get("version")!r}')
    return report


def remove_previous(report: dict | None, dol: bytearray, dat: ledger.DatFile | None) -> list[str]:
    """Undo a previous run's steps (newest first). Returns log lines."""
    if not report:
        return []
    log = []
    for step in reversed(report['steps']):
        outcome = ledger.undo_diff(dol, step['dol'])
        if step.get('dat'):
            if dat is None:
                raise RosterDevError('the previous injection changed dt_na.dat, which is missing now')
            dat_outcome = dat.undo(step['dat'])
            if dat_outcome != outcome:
                outcome = f'{outcome} (dt_na.dat: {dat_outcome})'
        log.append(f'removed previous step {step["key"]}: {outcome}')
    return log


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
    previous = _read_report(report_path)

    log = remove_previous(previous, dol_bytes, dat)
    report = {'version': REPORT_VERSION, 'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'steps': [],
              'not_built': [], 'log': []}
    if not remove_only:
        config, config_source = load_config(config_path)
        report['config'] = _display_path(config_source)
        image = dolfile.DolImage(bytes(dol_bytes))
        ctx = steps.RosterContext(dol=image, dat=dat, config=config)
        built = steps.implemented()
        if not built:
            log.append('no roster steps implemented yet')
        for step in built:
            before = image.to_bytes()
            lines = step.apply(ctx) or []
            entry = {'key': step.key, 'phase': step.phase, 'title': step.title, 'log': list(lines),
                     'dol': ledger.diff_bytes(before, image.to_bytes()),
                     'dat': dat.take_records() if dat is not None else []}
            report['steps'].append(entry)
            log += [f'[{step.key}] {line}' for line in lines]
        report['not_built'] = [f'Phase {p}: {t}' for _k, p, t in steps.not_built()]
        dol_bytes = bytearray(image.to_bytes())
    report['log'] = log
    report['dol_sha1'] = hashlib.sha1(dol_bytes).hexdigest()

    if not dry_run:
        tmp = dol_path + '.roster_tmp'
        with open(tmp, 'wb') as f:
            f.write(dol_bytes)
        if dat is not None:
            dat.flush()
        os.replace(tmp, dol_path)
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        if remove_only:
            if os.path.isfile(report_path):
                os.remove(report_path)
        else:
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=1)
                f.write('\n')
    return report


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Roster expansion development injector (menu [10]).')
    parser.add_argument('--config', help='roster preset JSON (default: 1_Input/roster.json, else the dev preset)')
    parser.add_argument('--remove', action='store_true', help='only take the previous injection out')
    parser.add_argument('--dry-run', action='store_true', help='run in memory, write nothing')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        report = run(args.output_dir, args.config, args.remove, args.dry_run)
    except (RosterDevError, ledger.LedgerError, dolfile.DolError) as exc:
        slogger.error(str(exc), source=SOURCE)
        return 1
    for line in report['log']:
        slogger.info(line, source=SOURCE)
    for line in report['not_built']:
        slogger.info(f'not built yet: {line}', source=SOURCE)
    what = 'removed' if args.remove else 'injected'
    slogger.info(f'roster expansion {what}{" (dry run, nothing written)" if args.dry_run else ""}; '
                 f'main.dol sha1 {report["dol_sha1"]}', source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
