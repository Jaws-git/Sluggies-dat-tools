"""Copy the patched game files from ``3_Output_Dat`` into the unpacked game (``start.py --deploy-game-files``).

The portable counterpart of ``3_Output_Dat/CopyFilesToGameDir.bat`` (kept for running by hand on Windows):
the GUI's "Deploy patched files to game directory" and "Copy files now" run this, so they work on every
system. The same files go to the same places:

    dt_na.dat                    -> DATA/files/dt_na.dat
    main.dol                     -> DATA/sys/main.dol
    fst.bin                      -> DATA/sys/fst.bin
    sound_NA/MY2.brsar           -> DATA/files/sound_NA/MY2.brsar
    sound_NA/stream/*.brstm      -> DATA/files/sound_NA/stream/

A file that is not in ``3_Output_Dat`` is skipped. Each file is written next to its target under a
temporary name and then swapped in, so a copy that fails halfway leaves the game's file as it was.

The game directory comes from ``--game-dir`` or the ``sluggiespath`` file next to the game files (the one
the batch file and the GUI use); this script never asks for it.

Exit code 0: every file found was copied. 1: nothing or not everything was copied; the reason is logged.
"""

import argparse
import glob
import os
import shutil
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import gui_grid  # noqa: E402  (the game directory rules the GUI uses; no Dear PyGui in it)
import slogger  # noqa: E402

SOURCE = 'deploy'
ROOT_DIR = os.path.dirname(_TOOLS_DIR)
TEMP_SUFFIX = '.sluggiestmp'

_FILES_DIR, _SYS_DIR = gui_grid.GAME_SUBDIRS
# (file in 3_Output_Dat, its place in the game directory)
FIXED_FILES = (
    ('dt_na.dat', os.path.join(_FILES_DIR, 'dt_na.dat')),
    ('main.dol', os.path.join(_SYS_DIR, 'main.dol')),
    ('fst.bin', os.path.join(_SYS_DIR, 'fst.bin')),
    (os.path.join('sound_NA', 'MY2.brsar'), os.path.join(_FILES_DIR, 'sound_NA', 'MY2.brsar')),
)
STREAM_DIR = os.path.join('sound_NA', 'stream')


def files_to_copy(output_dir: str) -> list[tuple[str, str]]:
    """``(relative source, relative target)`` of every game file present in ``output_dir``."""
    pairs = [(src, dst) for src, dst in FIXED_FILES if os.path.isfile(os.path.join(output_dir, src))]
    streams = glob.glob(os.path.join(glob.escape(os.path.join(output_dir, STREAM_DIR)), '*.brstm'))
    for path in sorted(streams):
        name = os.path.basename(path)
        if os.path.isfile(path):
            pairs.append((os.path.join(STREAM_DIR, name), os.path.join(_FILES_DIR, STREAM_DIR, name)))
    return pairs


def copy_one(source: str, target: str) -> None:
    """Copy ``source`` over ``target`` through a temporary file beside it; raises ``OSError``."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temp = target + TEMP_SUFFIX
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, target)
    except OSError:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def copy_game_files(output_dir: str, game_dir: str, log=None) -> tuple[list[str], list[str]]:
    """Copy every game file found in ``output_dir`` into ``game_dir``. Returns ``(copied, failed)``: the
    relative source names; ``log(severity, text)`` gets one line per file."""
    log = log or (lambda _severity, _text: None)
    copied, failed = [], []
    for src, dst in files_to_copy(output_dir):
        target = os.path.join(game_dir, dst)
        try:
            copy_one(os.path.join(output_dir, src), target)
        except OSError as exc:
            failed.append(src)
            log('error', f'Could not copy {src} to "{target}": {exc}. Is the game or Dolphin still running, '
                         'or is the file read-only?')
        else:
            copied.append(src)
            log('info', f'Copied {src} to "{target}"')
    return copied, failed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Copy the patched game files into the unpacked game.')
    parser.add_argument('--output-dir', default=os.path.join(ROOT_DIR, '3_Output_Dat'),
                        help='where the patched files are (default: 3_Output_Dat)')
    parser.add_argument('--game-dir', default=None,
                        help="the unpacked game's main folder (default: the one stored in the sluggiespath file)")
    args = parser.parse_args(argv)
    slogger.configure()

    path_file = os.path.join(args.output_dir, os.path.basename(gui_grid.GAME_DIR_FILE_REL))
    game_dir = gui_grid.clean_game_dir(args.game_dir) if args.game_dir else gui_grid.read_game_dir(path_file)
    if not game_dir:
        slogger.error(f'No game directory is stored in "{path_file}" (or given with --game-dir). Nothing was copied.',
                      source=SOURCE)
        return 1
    problem = gui_grid.game_dir_problem(game_dir)
    if problem is not None:
        slogger.error(f'Nothing was copied: {problem}', source=SOURCE)
        return 1

    def log(severity, text):
        (slogger.error if severity == 'error' else slogger.info)(text, source=SOURCE)

    copied, failed = copy_game_files(args.output_dir, game_dir, log)
    if failed:
        slogger.error(f'{len(failed)} file(s) could not be copied ({len(copied)} copied). The game directory is '
                      'incomplete.', source=SOURCE)
        return 1
    if not copied:
        slogger.error(f'None of dt_na.dat, main.dol, fst.bin or sound_NA/MY2.brsar is in "{args.output_dir}". '
                      'Nothing was copied.', source=SOURCE)
        return 1
    slogger.info(f'Done: {len(copied)} file(s) copied to "{game_dir}".', source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
