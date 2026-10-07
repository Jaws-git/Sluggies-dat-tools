"""Hand-run: every shipped roster preset end to end on a scratch copy of ``1_Input`` (real files, about 1.5 GB of disk).

For each preset (in menu order, each replacing the previous injection, as the menu does):
- the run succeeds and a second run gives the same main.dol and dt_na.dat;
- a few result words: the grid's square count, the moved icon / name / layout records.
At the end ``--remove`` must give back the input main.dol byte for byte and a dt_na.dat whose stock range is the
input's (the hammerspace tail may stay, zeroed). Prints one line per check; exit code 1 on any failure.

    python SluggiesTools/Roster/probe_presets.py [--keep]
"""

import hashlib
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from SluggiesTools.Dol import dolfile  # noqa: E402
from SluggiesTools.Icons import layout2d  # noqa: E402
from SluggiesTools.Roster import dat_hammerspace as dhs  # noqa: E402
from SluggiesTools.Roster import grid, icons, names, runner  # noqa: E402

PRESETS = ('01_Stock_Roster.json', '02_Stock_and_Unused.json', '03_Unuseds_and_8_color_slots.json',
           '04_Unuseds_and_10_color_slots.json', '05_Extra_Columns_12x4_grid.json',
           '06_Extra_Columns_Max_12x5_grid.json')
PRESET_DIR = os.path.join(ROOT, '1_Input', '_RosterConfigurations')
INPUT = os.path.join(ROOT, '1_Input')


def sha(path: str, length: int | None = None) -> str:
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        remaining = length
        while True:
            chunk = f.read(1 << 22 if remaining is None else min(1 << 22, remaining))
            if not chunk:
                break
            h.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
                if not remaining:
                    break
    return h.hexdigest()


def tail_is_zero(path: str, start: int) -> bool:
    with open(path, 'rb') as f:
        f.seek(start)
        while chunk := f.read(1 << 22):
            if any(chunk):
                return False
    return True


def main() -> int:
    keep = '--keep' in sys.argv
    work = tempfile.mkdtemp(prefix='sluggies_presets_')
    failures = 0

    def check(ok: bool, what: str) -> None:
        nonlocal failures
        print(('ok    ' if ok else 'FAIL  ') + what)
        failures += not ok

    try:
        for name in ('main.dol', 'dt_na.dat', 'fst.bin'):
            shutil.copy2(os.path.join(INPUT, name), work)
        dol, dat = os.path.join(work, 'main.dol'), os.path.join(work, 'dt_na.dat')
        stock_dol, stock_size = sha(dol), os.path.getsize(dat)
        stock_dat = sha(dat)
        for preset in PRESETS:
            path = os.path.join(PRESET_DIR, preset)
            runner.run(work, path)
            first = sha(dol), sha(dat)
            runner.run(work, path)
            check((sha(dol), sha(dat)) == first, f'{preset}: a second run gives the same files')
            with open(dol, 'rb') as f:
                image = dolfile.DolImage(f.read())
            squares = image.u32(grid.COUNT_LOOP_B) & 0xFFFF
            check(squares == (60 if preset.startswith('04') else 0x29),
                  f'{preset}: square count word {squares}')
            icon = dhs.slot(dhs.read_record(image, icons.ICON_RECORD), 'en')[0]
            check((icon >= dhs.BASE_SIZE) == (not preset.startswith('01')), f'{preset}: icon bank at 0x{icon:08X}')
            text = dhs.slot(dhs.read_record(image, names.name_record(image)), 'en')[0]
            check((text >= dhs.BASE_SIZE) == (preset[:2] in ('03', '04')), f'{preset}: name table at 0x{text:08X}')
            layout = dhs.slot(dhs.read_record(image, dhs.dol_base_address(layout2d.CSS_LAYOUT_DOL_RECORD)), 'en')
            with open(dat, 'rb') as f:
                f.seek(layout[0])
                rows = len(layout2d.Layout(f.read(layout[1])).rows)
            check(rows == (483 + 0xFF - 0x66 if preset[:2] in ('03', '04') else 483), f'{preset}: select layout rows {rows}')
        runner.run(work, remove_only=True)
        check(sha(dol) == stock_dol, 'remove: main.dol is the input DOL')
        check(sha(dat, stock_size) == stock_dat and tail_is_zero(dat, stock_size),
              'remove: dt_na.dat is the input file plus a zeroed tail')
    finally:
        if keep:
            print(f'kept {work}')
        else:
            shutil.rmtree(work, ignore_errors=True)
    print('all checks passed' if not failures else f'{failures} checks failed')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
