"""Stat editor bridge command line (``start.py --stat-bridge-export`` / ``--apply-stat-edits``).

  cli.py --export FILE [--focus 0xNN]     write the bridge for 3_Output_Dat to FILE
  cli.py --apply FILE [FILE ...] [--dry-run]
                                          write the stat editor's edit files into 3_Output_Dat/main.dol;
                                          an item reset:0xNN clears that character's stat edits;
                                          copy:FILE writes a paste's stat snapshot

``--export`` reads ``3_Output_Dat/main.dol`` (+ ``dt_na.dat`` for names) and
``1_Input/main.dol`` (the baseline rows) and never writes the game files.
``--apply`` checks every file first (``apply.prepare``: the DOL hash, IDs,
fields, ranges), lists the changes per character and table, then writes
``main.dol`` (nothing with ``--dry-run``, or when any file is refused).
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
    from . import apply, bridge
except ImportError:
    from Dol import dolfile
    from Roster import datfile
    from StatEditor import apply, bridge

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


def character_names(image: dolfile.DolImage, dat) -> dict[int, str]:
    """``{ID: name the game shows}`` (empty without ``dt_na.dat``)."""
    if dat is None:
        return {}
    roster = bridge.read_roster(image)
    return {int(c['id'], 16): c['name'] for c in bridge.character_list(image, dat, roster) if c['name']}


def check(image: dolfile.DolImage, dat, items: list[str],
          input_dir: str = INPUT_DIR) -> tuple[apply.Prepared, dict[int, str]]:
    """The items checked against ``image`` (``apply.prepare``), and the names for messages. An item is an edit
    file's path, or ``reset:0xNN`` (clear that character's stat edits; reads ``input_dir/main.dol``)."""
    parsed = [apply.parse_item(item) for item in items]
    documents = [p if isinstance(p, apply.Reset) else apply.Copy(p.path, apply.read_file(p.path))
                 if isinstance(p, apply.Copy) else apply.read_file(p) for p in parsed]
    vanilla = None
    if any(isinstance(p, apply.Reset) for p in parsed):
        vanilla = _read_dol(os.path.join(input_dir, 'main.dol'), 'clearing stat edits needs the original main.dol')
    names = character_names(image, dat)
    files = sum(isinstance(p, str) for p in parsed)
    labels = []
    for n, p in enumerate(parsed, 1):
        if isinstance(p, apply.Reset):
            labels.append(f'clearing the stat edits of {names.get(p.cid, _hex(p.cid))} ({_hex(p.cid)})')
        elif isinstance(p, apply.Copy):
            labels.append(f'the pasted stat values ({os.path.basename(p.path)})')
        else:
            labels.append(os.path.basename(p) if files == 1 else f'{os.path.basename(p)} (file {n})')
    return apply.prepare(image, documents, labels, names, vanilla), names


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


def apply_files(items: list[str], output_dir: str = OUTPUT_DIR, dry_run: bool = False,
                input_dir: str = INPUT_DIR) -> tuple[apply.Prepared, dict[int, str]]:
    """Check the items (edit files, ``reset:0xNN``) and write them into ``output_dir/main.dol`` (not with
    ``dry_run``)."""
    dol_path = os.path.join(output_dir, 'main.dol')
    dat_path = os.path.join(output_dir, 'dt_na.dat')
    image = _read_dol(dol_path, 'run the normal pipeline first (menu [1])')
    dat = datfile.DatFile(dat_path) if os.path.isfile(dat_path) else None
    prepared, names = check(image, dat, items, input_dir)
    if prepared.changes and not dry_run:
        apply.write(image, prepared)
        tmp = dol_path + '.stats_tmp'
        with open(tmp, 'wb') as f:
            f.write(image.to_bytes())
        os.replace(tmp, dol_path)
    return prepared, names


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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--export', metavar='FILE', help='write stat_bridge.json to FILE')
    mode.add_argument('--apply', nargs='+', metavar='FILE', help="write the stat editor's edit files "
                      '(stat_edits.json) into main.dol, in order (a later one wins); an item reset:0xNN clears '
                      "that character's stat edits at its place in the order")
    parser.add_argument('--focus', type=_id, metavar='0xNN', help='--export: the character the editor preselects')
    parser.add_argument('--dry-run', action='store_true', help='--apply: check and list the changes, write nothing')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help=argparse.SUPPRESS)
    parser.add_argument('--input-dir', default=INPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.focus is not None and not args.export:
        parser.error('--focus goes with --export')
    if args.dry_run and not args.apply:
        parser.error('--dry-run goes with --apply')
    if args.apply:
        return _main_apply(args)
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


def _main_apply(args) -> int:
    try:
        items = [p if p.lower().startswith(apply.RESET_PREFIX)
                 else apply.COPY_PREFIX + os.path.abspath(p[len(apply.COPY_PREFIX):])
                 if p.lower().startswith(apply.COPY_PREFIX) else os.path.abspath(p) for p in args.apply]
        prepared, names = apply_files(items, args.output_dir, args.dry_run, args.input_dir)
    except (RuntimeError, ValueError, OSError) as exc:          # EditFileError, BridgeError
        slogger.error(f'refused, nothing written: {exc}', source=SOURCE)
        return 1
    for line in apply.describe(prepared, names):
        slogger.info(line, source=SOURCE)
    for warning in prepared.warnings:
        slogger.warning(warning, source=SOURCE)
    same = f'; {prepared.same} already as given' if prepared.same else ''
    if not prepared.changes:
        slogger.info(f'nothing to change: main.dol holds these values already{same}', source=SOURCE)
    elif args.dry_run:
        slogger.info(f'dry run: {apply.summary(prepared)} would change{same}; nothing written', source=SOURCE)
    else:
        slogger.info(f'written: {apply.summary(prepared)}{same}', source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
