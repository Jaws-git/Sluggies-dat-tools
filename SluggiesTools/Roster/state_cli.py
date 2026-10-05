"""``start.py --roster-state``: read the draft grid from 3_Output_Dat for the GUI's character grid.

Writes ``3_Output_Dat/_gui/roster_state.json`` (``state.read_state``), the
portrait crops it names into ``3_Output_Dat/_gui/icons`` (``state_icons``;
crops already there are kept, so an unchanged bank decodes nothing), and
logs the grid as text, one line per row. Reads only; never writes the game
files.

``--derive`` (``start.py --roster-derive``) instead writes the derived
config (``derive.py``) to ``3_Output_Dat/_gui/derived/roster.json`` plus its
portraits; ``start.py --roster --state`` that file rebuilds the roster from it.
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
    from . import datfile, derive, state, state_icons
except ImportError:
    from Dol import dolfile
    import datfile
    import derive
    import state
    import state_icons

SOURCE = 'roster.state'
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
OUTPUT_DIR = os.path.join(ROOT, '3_Output_Dat')
GUI_DIR = '_gui'
STATE_FILE = 'roster_state.json'
ICON_DIR = 'icons'
DERIVED_DIR = 'derived'


def state_path(output_dir: str = OUTPUT_DIR) -> str:
    return os.path.join(output_dir, GUI_DIR, STATE_FILE)


def derived_dir(output_dir: str = OUTPUT_DIR) -> str:
    return os.path.join(output_dir, GUI_DIR, DERIVED_DIR)


def _open(output_dir: str):
    dol_path = os.path.join(output_dir, 'main.dol')
    dat_path = os.path.join(output_dir, 'dt_na.dat')
    if not os.path.isfile(dol_path):
        raise state.StateError(f'{dol_path} is missing: run menu [1] (or the All-In-One Export) first')
    with open(dol_path, 'rb') as f:
        image = dolfile.DolImage(f.read())
    return image, datfile.DatFile(dat_path) if os.path.isfile(dat_path) else None


def run_derive(output_dir: str = OUTPUT_DIR) -> tuple[derive.Derived, str]:
    """Write the derived config of the output files; returns it and the JSON's path."""
    image, dat = _open(output_dir)
    derived = derive.derive(image, dat)
    return derived, derive.write(derived, derived_dir(output_dir))


def run(output_dir: str = OUTPUT_DIR) -> dict:
    image, dat = _open(output_dir)
    result = state.read_state(image, dat)
    result['icon_dir'] = ICON_DIR
    result['icon_crops'] = None
    if result['icons_read']:
        crops = [ref for c in result['characters'] for ref in (c['icon'] or {}).values() if ref]
        try:
            decoded, written = state_icons.write_crops(state_icons.read_bank(image, dat), crops,
                                                       os.path.join(output_dir, GUI_DIR, ICON_DIR))
        except state_icons.IconStateError as exc:
            result['warnings'].append(f'no portraits: {exc}')
            result['icons_read'] = False
            for c in result['characters']:
                c['icon'] = None
        else:
            result['icon_crops'] = {'crops': len({ref['file'] for ref in crops}), 'pages_decoded': decoded,
                                    'written': written}
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
    parser.add_argument('--derive', action='store_true',
                        help='write the derived config (read -> rebuild) to 3_Output_Dat/_gui/derived instead')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.derive:
        return main_derive(args.output_dir)
    try:
        result = run(args.output_dir)
    except (RuntimeError, ValueError) as exc:
        slogger.error(str(exc), source=SOURCE)
        return 1
    cols, rows = result['shape']
    slogger.info(f'{result["kind"]} grid {cols}x{rows}: {len(result["squares"])} squares, '
                 f'{len(result["characters"])} characters'
                 + ('' if result['names_read'] else ' (no names: dt_na.dat missing)'), source=SOURCE)
    crops = result.get('icon_crops')
    if crops:
        slogger.info(f'portraits: {crops["crops"]} crops, {crops["written"]} new '
                     f'({crops["pages_decoded"]} pages decoded)', source=SOURCE)
    for line in state.text_grid(result):
        slogger.info(line, source=SOURCE)
    for warning in result['warnings']:
        slogger.warning(warning, source=SOURCE)
    slogger.info(f'state written to {os.path.relpath(state_path(args.output_dir), ROOT)}', source=SOURCE)
    return 0


def main_derive(output_dir: str) -> int:
    try:
        derived, path = run_derive(output_dir)
    except (RuntimeError, ValueError) as exc:
        slogger.error(str(exc), source=SOURCE)
        return 1
    config = derived.config
    parts = [f'{len(config[k])} {k}' for k in ('ids', 'wheels', 'wheel_order', 'stock_icons') if k in config]
    if 'grid' in config:
        parts.append('grid {}x{} with {} new squares'.format(*config['grid']['shape'], len(config['grid']['squares'])))
    slogger.info('derived config: ' + (', '.join(parts) or 'stock roster (nothing to carry)')
                 + f'; {len(derived.portraits)} portraits', source=SOURCE)
    for warning in derived.warnings:
        slogger.warning(warning, source=SOURCE)
    rel = os.path.relpath(path, ROOT)
    slogger.info(f'written to {rel}; rebuild with: python start.py --roster --state {rel}', source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
