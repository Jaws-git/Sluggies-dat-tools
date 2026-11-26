"""Smoke test of the built release: ``python smoke_test.py PATH/TO/sluggies-dat-tools.exe``.

``--help`` alone only proves the dispatcher starts. Every real command runs a loose ``.py`` file through
``--_run-script``, and a module such a script imports is in the frozen runtime only when ``start.py``'s import
graph reaches it. So this runs a few commands of different subsystems in a release without game files: each
has to end with its own clean message (the files are missing), never with a traceback or a missing module.

Run it on a fresh ``build.py`` output (its ``1_Input`` holds no game files, so nothing can be written).
"""

import subprocess
import sys
from pathlib import Path

RELEASE_OUTPUT = object()            # stands for "<the release folder>/3_Output_Dat" in the expected text
# (arguments, a text the output has to hold or None, exit codes that are fine)
COMMANDS = (
    (('--help',), 'usage', (0,)),
    (('--game-options',), 'main.dol', (0, 1)),                    # GameOptions/, Dol/
    (('--roster-state',), None, (0, 1)),                          # Roster/ reader: numpy, pillow
    # Hammerspace/. Its "missing ..." path has to lie in the release: a child script that takes the bundled
    # copy of a module instead of the loose file resolves 3_Output_Dat somewhere else.
    (('--defrag-hammerspace', '--dry-run'), RELEASE_OUTPUT, (0, 1)),
    # HammerspaceMain.py as a child script: its sibling modules (Donor, BlockAssembly, RouteWriter, ...) import.
    (('--_run-script', 'SluggiesTools/Hammerspace/HammerspaceMain.py', '--help'), 'usage', (0,)),
    (('--export-sounds',), None, (0, 1)),                         # Sound/
    (('--apply-gameplay', 'reset', '--dry-run'), None, (0, 1)),   # Gameplay/
    (('--custom-cpu-off', '--dry-run'), None, (0, 1)),            # CustomCpu/
    (('--save-roster', 'smoke_test.sluggiesroster'), None, (0, 1)),   # Roster/ packs
    (('--patch', 'smoke_test_missing.sluggie'), None, (0, 1)),    # model lookup, texture_helper
)
BROKEN = ('Traceback (most recent call last)', 'ModuleNotFoundError', 'ImportError', 'No module named')


def run(executable: Path) -> list[str]:
    problems = []
    for args, expected, codes in COMMANDS:
        try:
            result = subprocess.run([str(executable), *args], cwd=executable.parent, input='\n', text=True,
                                    capture_output=True, encoding='utf-8', errors='replace', timeout=300)
        except subprocess.TimeoutExpired:
            problems.append(f'{" ".join(args)}: no result within 300 s')
            continue
        output = result.stdout + result.stderr
        if expected is RELEASE_OUTPUT:
            expected = str(executable.parent / '3_Output_Dat')
        found = [text for text in BROKEN if text in output]
        if found:
            problems.append(f'{" ".join(args)}: {found[0]}\n{output[-2000:]}')
        elif result.returncode not in codes:
            problems.append(f'{" ".join(args)}: exit code {result.returncode}\n{output[-2000:]}')
        elif expected and expected not in output:
            problems.append(f'{" ".join(args)}: "{expected}" is not in the output\n{output[-2000:]}')
        else:
            print(f'ok   {" ".join(args)} (exit {result.returncode})')
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        print(f'not found: {executable}')
        return 2
    problems = run(executable)
    for problem in problems:
        print(f'FAIL {problem}')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
