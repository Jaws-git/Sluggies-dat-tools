"""The roster expansion (``start.py --roster``, StartTools menu [9], also run by menu [1]).

Runs every roster step (``steps.STEPS``, in order) with one roster
configuration (``--config``, e.g. from ``1_Input/_RosterConfigurations``) on
the files in ``3_Output_Dat`` (the normal pipeline's output).

* It first resets the roster to vanilla (``reset.py``, against ``1_Input``),
  so repeated runs never stack and no record of earlier runs is needed.
* ``--remove`` only resets the roster to vanilla.
* ``--state FILE`` instead of ``--config``: a derived config (``derive.py``,
  read -> rebuild) with its portraits in the ``icons`` folder beside it.
* Game options (``GameOptions/``, menu [10]) that are on before the reset are
  applied again afterwards.
* ``--dry-run`` runs everything in memory and writes nothing.
* After the steps it stores a manifest (``manifest.py``) of the facts only
  hook code holds, so ``state.py`` can read the roster back.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
for _path in (_TOOLS_DIR, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import slogger  # noqa: E402

try:
    from ..Dol import dolfile
    from ..GameOptions import game_options
    from . import datfile, derive, dol_hammerspace, manifest, model_dirs, reset, steps
except ImportError:
    from Dol import dolfile
    from GameOptions import game_options
    import datfile
    import derive
    import dol_hammerspace
    import manifest
    import model_dirs
    import reset
    import steps

SOURCE = 'roster'
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
OUTPUT_DIR = os.path.join(ROOT, '3_Output_Dat')
INPUT_DIR = os.path.join(ROOT, '1_Input')
LEGACY_REPORT_DIR = 'roster_dev'          # earlier versions kept an undo report here; no longer needed
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


def _remove_legacy_report(output_dir: str) -> list[str]:
    folder = os.path.join(output_dir, LEGACY_REPORT_DIR)
    if not os.path.isdir(folder):
        return []
    shutil.rmtree(folder, ignore_errors=True)
    return [f'removed the old undo report folder {LEGACY_REPORT_DIR} (no longer needed)']


def patch_fst(output_dir: str, dat_size: int) -> str:
    """Write the grown ``dt_na.dat`` size into the output ``fst.bin`` (copied from 1_Input first if missing)."""
    fst = os.path.join(output_dir, 'fst.bin')
    if not os.path.isfile(fst):
        input_fst = os.path.join(INPUT_DIR, 'fst.bin')
        if not os.path.isfile(input_fst):
            return f'fst.bin not found: the disc file table still has the old dt_na.dat size (0x{dat_size:X} needed)'
        with open(input_fst, 'rb') as src, open(fst, 'wb') as dst:
            dst.write(src.read())
    with open(fst, 'r+b') as f:
        f.seek(FST_DAT_SIZE_OFFSET)
        old = int.from_bytes(f.read(4), 'big')
        if old >= dat_size:
            return f'fst.bin: dt_na.dat size 0x{old:X} already covers it'
        f.seek(FST_DAT_SIZE_OFFSET)
        f.write(dat_size.to_bytes(4, 'big'))
    return f'fst.bin: dt_na.dat size 0x{old:X} -> 0x{dat_size:X}'


def write_manifest(ctx: steps.RosterContext) -> str:
    """Store the run's hook-only facts (``manifest.py``) in the DOL data section, for the grid reader."""
    hs = dol_hammerspace.get(ctx)
    blob = manifest.encode(manifest.build(ctx.state, ctx.config))
    at = hs.data.put(blob, 4)
    hs.commit()
    return f'[manifest] roster manifest at 0x{at:08X} (0x{len(blob):X} bytes)'


def run(output_dir: str = OUTPUT_DIR, config_path: str | None = None, remove_only: bool = False,
        dry_run: bool = False, input_dir: str = INPUT_DIR, state_path: str | None = None) -> dict:
    """``state_path``: a derived config (``derive.write``) in place of ``config_path``."""
    icon_dir = None
    if state_path:
        config_path, icon_dir = state_path, derive.icon_dir_of(state_path)
    dol_path = os.path.join(output_dir, 'main.dol')
    dat_path = os.path.join(output_dir, 'dt_na.dat')
    vanilla_path = os.path.join(input_dir, 'main.dol')
    if not os.path.isfile(dol_path):
        raise RosterDevError(f'{dol_path} is missing: run the normal pipeline first (menu [1], icons, '
                             'model patches); the roster expansion builds on its output')
    if not os.path.isfile(vanilla_path):
        raise RosterDevError(f'{vanilla_path} is missing: resetting the roster to vanilla needs the original '
                             'main.dol there')
    config = config_source = None
    if not remove_only:
        config, config_source = load_config(config_path)
    with open(dol_path, 'rb') as f:
        current = f.read()
    with open(vanilla_path, 'rb') as f:
        vanilla = f.read()
    dat = datfile.DatFile(dat_path) if os.path.isfile(dat_path) else None
    options = game_options.detect(dolfile.DolImage(current))
    try:
        keep = model_dirs.config_routes(config) if config is not None else []
    except ValueError as exc:
        raise RosterDevError(str(exc)) from exc
    try:
        dol_bytes, log = reset.reset(current, vanilla, dat, keep)
    except reset.ResetError as exc:
        raise RosterDevError(str(exc)) from exc

    result = {'steps': [], 'log': []}
    if not remove_only:
        result['config'] = _display_path(config_source)
        image = dolfile.DolImage(dol_bytes)
        input_dat_path = os.path.join(input_dir, 'dt_na.dat')
        ctx = steps.RosterContext(dol=image, dat=dat, config=config, icon_dir=icon_dir,
                                  input_dol=dolfile.DolImage(vanilla),
                                  input_dat=datfile.DatFile(input_dat_path) if os.path.isfile(input_dat_path) else None)
        ctx.state[model_dirs.RESERVED_KEY] = list(keep)
        for step in steps.all_steps():
            lines = step.apply(ctx) or []
            result['steps'].append({'key': step.key, 'title': step.title, 'log': list(lines)})
            log += [f'[{step.key}] {line}' for line in lines]
        log.append(write_manifest(ctx))
        dol_bytes = image.to_bytes()
    if options:
        image = dolfile.DolImage(dol_bytes)
        log += [f'[game options] {line}' for line in game_options.apply(image, options)]
        dol_bytes = image.to_bytes()
    result['dol_sha1'] = hashlib.sha1(dol_bytes).hexdigest()

    if not dry_run:
        tmp = dol_path + '.roster_tmp'
        with open(tmp, 'wb') as f:
            f.write(dol_bytes)
        if dat is not None:
            grown = dat.grown
            dat.flush()
            if grown:
                log.append(patch_fst(output_dir, dat.size))
        os.replace(tmp, dol_path)
        log += _remove_legacy_report(output_dir)
    result['log'] = log
    return result


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Roster expansion (menu [9]).')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--config', help='the roster configuration JSON (e.g. from 1_Input/_RosterConfigurations)')
    source.add_argument('--state', help='a derived roster config (start.py --roster-derive), portraits beside it')
    parser.add_argument('--remove', action='store_true', help='only reset the roster to vanilla (1_Input)')
    parser.add_argument('--dry-run', action='store_true', help='run in memory, write nothing')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        report = run(args.output_dir, args.config, args.remove, args.dry_run, state_path=args.state)
    except (RuntimeError, ValueError) as exc:     # every step's errors (DolError, config errors, ...)
        slogger.error(str(exc), source=SOURCE)
        return 1
    for line in report['log']:
        slogger.info(line, source=SOURCE)
    what = 'reset to vanilla' if args.remove else 'injected'
    slogger.info(f'roster expansion {what}{" (dry run, nothing written)" if args.dry_run else ""}; '
                 f'main.dol sha1 {report["dol_sha1"]}', source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
