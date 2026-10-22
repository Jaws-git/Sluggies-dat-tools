"""Gameplay settings command line (``start.py --apply-gameplay`` / ``--save-gameplay``).

  cli.py --apply ITEM [ITEM ...] [--dry-run] [--report FILE]
                         write gameplay edit / settings files (or an AI Gameplay Tool
                         file) into 3_Output_Dat/main.dol, in order; the item reset
                         puts every gameplay value back to 1_Input
  cli.py --save FILE [--name NAME]
                         write the gameplay settings 3_Output_Dat/main.dol holds into FILE

``--apply`` checks every item first (``apply.prepare``), lists the changes per
table, then writes ``main.dol`` (nothing with ``--dry-run``, or when an item is
refused). ``--report`` writes the check as JSON (``apply.report``), also for a
refusal (``{"error": ...}``). ``--save`` only reads the game files.
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
    from . import apply, legacy
except ImportError:
    from Dol import dolfile
    from Gameplay import apply, legacy

SOURCE = 'gameplay'
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
OUTPUT_DIR = os.path.join(ROOT, '3_Output_Dat')
INPUT_DIR = os.path.join(ROOT, '1_Input')


def _read_dol(path: str) -> dolfile.DolImage | None:
    if not os.path.isfile(path):
        return None
    with open(path, 'rb') as f:
        return dolfile.DolImage(f.read())


def _output_dol(output_dir: str) -> tuple[str, dolfile.DolImage]:
    path = os.path.join(output_dir, 'main.dol')
    image = _read_dol(path)
    if image is None:
        raise apply.EditFileError(f'{path} is missing: run the normal pipeline first (menu [1])')
    return path, image


def check(image: dolfile.DolImage, items: list[str], input_dir: str = INPUT_DIR) -> apply.Prepared:
    """The items checked against ``image`` (``apply.prepare``)."""
    parsed = [apply.parse_item(item) for item in items]
    documents = [p if isinstance(p, apply.Reset) else apply.read_file(p) for p in parsed]
    files = sum(not isinstance(p, apply.Reset) for p in parsed)
    labels = []
    for n, (p, doc) in enumerate(zip(parsed, documents), 1):
        if isinstance(p, apply.Reset):
            labels.append('resetting the gameplay settings')
        else:
            name = os.path.basename(p) + (f' ({legacy.TITLE})' if legacy.is_legacy(doc) else '')
            labels.append(name if files == 1 else f'{name} (file {n})')
    vanilla = _read_dol(os.path.join(input_dir, 'main.dol'))
    return apply.prepare(image, documents, labels, vanilla)


def apply_items(items: list[str], output_dir: str = OUTPUT_DIR, dry_run: bool = False,
                input_dir: str = INPUT_DIR) -> apply.Prepared:
    """Check the items and write them into ``output_dir/main.dol`` (not with ``dry_run``)."""
    dol_path, image = _output_dol(output_dir)
    prepared = check(image, items, input_dir)
    if prepared.changes and not dry_run:
        apply.write(image, prepared)
        tmp = dol_path + '.gameplay_tmp'
        with open(tmp, 'wb') as f:
            f.write(image.to_bytes())
        os.replace(tmp, dol_path)
    return prepared


def save(path: str, output_dir: str = OUTPUT_DIR, name: str = '') -> dict:
    """Write the settings ``output_dir/main.dol`` holds to ``path``; returns the document."""
    _dol_path, image = _output_dol(output_dir)
    doc = apply.settings_document(image, name or os.path.splitext(os.path.basename(path))[0])
    _write_json(path, doc)
    return doc


def _write_json(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Gameplay settings (CPU behaviour, star boosts, item odds) of '
                                                 '3_Output_Dat/main.dol.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', nargs='+', metavar='ITEM', help='gameplay edit / settings files or AI Gameplay '
                      'Tool files, in order (a later one wins); the item reset puts every value back to 1_Input')
    mode.add_argument('--save', metavar='FILE', help='write the gameplay settings the game holds into FILE')
    parser.add_argument('--name', default='', help='--save: the name stored in the file (default: the file name)')
    parser.add_argument('--dry-run', action='store_true', help='--apply: check and list the changes, write nothing')
    parser.add_argument('--report', metavar='FILE', help='--apply: write the check as JSON into FILE')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help=argparse.SUPPRESS)
    parser.add_argument('--input-dir', default=INPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if (args.dry_run or args.report) and not args.apply:
        parser.error('--dry-run and --report go with --apply')
    if args.name and not args.save:
        parser.error('--name goes with --save')
    if args.save:
        try:
            doc = save(args.save, args.output_dir, args.name)
        except (RuntimeError, ValueError, OSError) as exc:
            slogger.error(str(exc), source=SOURCE)
            return 1
        slogger.info(f'gameplay settings "{doc["name"]}" saved to {args.save}', source=SOURCE)
        return 0
    items = [p if isinstance(apply.parse_item(p), apply.Reset) else os.path.abspath(p) for p in args.apply]
    try:
        prepared = apply_items(items, args.output_dir, args.dry_run, args.input_dir)
    except (RuntimeError, ValueError, OSError) as exc:          # EditFileError, DolError
        if args.report:
            _write_json(args.report, {'error': str(exc)})
        slogger.error(f'refused, nothing written: {exc}', source=SOURCE)
        return 1
    if args.report:
        _write_json(args.report, apply.report(prepared))
    for line in apply.describe(prepared):
        slogger.info(line, source=SOURCE)
    for note in prepared.notes:
        slogger.info(note, source=SOURCE)
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
