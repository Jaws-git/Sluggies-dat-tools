"""The "last chain did not finish" marker: ``3_Output_Dat/chain_incomplete.json``.

A chain (slot edits, a roster pack load, a roster switch) is several ``start.py`` commands that each rewrite the
game files. When one fails, or the run is killed, the steps before it stay applied and the files are in a state
no single command produced. Nothing is rolled back; the marker only says so, until the same kind of chain
finishes or the user dismisses it:

* ``start.py`` writes it before a chain's first writing step, updates it after each step and removes it when
  the chain finished (``run_chain_commands``);
* ``start.py`` warns about it at the start of every later command, and the GUI's character grid shows it
  (``message``) with a Dismiss button.

No game file is read or written here.
"""

import json
import os
import time

MARKER_REL = os.path.join('3_Output_Dat', 'chain_incomplete.json')
CHILD_ENV = 'SLUGGIES_CHAIN_STEP'            # set for a chain's own steps: they do not warn about their chain


def path(root_dir: str) -> str:
    return os.path.join(root_dir, MARKER_REL)


def is_writing(args) -> bool:
    """Whether a chain command can change files (a build check and a dry run do not)."""
    return '--validate-only' not in args and '--dry-run' not in args


def begin(label: str, commands, start: int = 1, total: int | None = None) -> dict:
    """The marker of a chain about to run ``commands`` (lists of ``start.py`` arguments); not written yet."""
    commands = [list(args) for args in commands]
    return {
        'label': label,
        'started': time.strftime('%Y-%m-%d %H:%M:%S'),
        'first': start,
        'total': len(commands) + start - 1 if total is None else total,
        'steps': [' '.join(args) for args in commands],
        'done': 0,                           # steps of ``steps`` that finished
        'failed': None,                      # the step number that failed (None: the run was cut off)
    }


def write(root_dir: str, marker: dict) -> None:
    """Store the marker (through a temporary file). Raises ``OSError``."""
    target = path(root_dir)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(marker, f, indent=1)
    os.replace(tmp, target)


def read(root_dir: str) -> dict | None:
    """The stored marker, or None when there is none. An unreadable file still counts as a marker."""
    try:
        with open(path(root_dir), encoding='utf-8') as f:
            marker = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {'label': 'A chain', 'steps': [], 'done': 0, 'failed': None, 'first': 1, 'total': 0, 'started': ''}
    return marker if isinstance(marker, dict) else {'label': 'A chain', 'steps': []}


def clear(root_dir: str, label: str | None = None) -> bool:
    """Remove the marker (``label``: only one of that kind of chain). True when one was removed."""
    marker = read(root_dir)
    if marker is None or (label is not None and marker.get('label') != label):
        return False
    try:
        os.remove(path(root_dir))
    except OSError:
        return False
    return True


def message(marker: dict) -> str:
    """One paragraph for the user: what ran, where it stopped, what that means."""
    label = marker.get('label') or 'A chain'
    steps = marker.get('steps') or []
    done = int(marker.get('done') or 0)
    first = int(marker.get('first') or 1)
    failed = marker.get('failed')
    when = f' started {marker["started"]}' if marker.get('started') else ''
    if failed is not None:
        stopped = f'step {failed} of {marker.get("total") or first + len(steps) - 1} failed'
    else:
        stopped = 'it was cut off before it finished'
    applied = f'{done} writing step{"s" if done != 1 else ""} had been applied'
    at = steps[done] if done < len(steps) else ''
    return (f'{label}{when} did not finish: {stopped}, {applied}'
            + (f' (stopped at: start.py {at})' if at else '')
            + '. The game files in 3_Output_Dat are in an in-between state: run the same action again, load a '
              'roster pack, or put a backup back.')
