"""Gecko code compatibility of ``main.dol``: one check for every tool that writes it.

Community Gecko codes made for the stock game (e.g. "CPU vs CPU V2", "CPU vs CPU human management") read and
write the MEM1 heap at fixed stock addresses. Our DOL hammerspace (``Roster/dol_hammerspace.py``: two sections
above the boot stacks) raises the MEM1 arena start, which moves that heap, so those codes stop working
(``_docs/_docs_roster/RosterExpansion.md``, "DOL hammerspace"). A ``main.dol`` is compatible while it has no such
sections and its arena start is stock.

Only two writers add the sections: the roster runner (``Roster/runner.py``: steps that need DOL code, the
manifest, game options carried over) and the game options (``GameOptions/runner.py``). Both check the bytes
before and after with :func:`breaks` and ask (:func:`confirm`) right before they write. A planned chain (slot
edits, roster switch, pack load) runs other writes before its roster run, so the dispatcher asks once up front
instead (:func:`guard_chain`) and lets the chain's commands through with :data:`ACCEPT_ENV`.

The question is asked only when the change breaks compatibility, i.e. the file is compatible now and will not
be afterwards; a game already incompatible is not asked again. In the GUI it is a ``danger`` popup (red Confirm,
shown even with "Skip confirmation popups" on); dry runs only record it in :func:`check_path` for the GUI's
summary dialogs.
"""

import json
import os
import sys
from dataclasses import dataclass

try:                                       # as SluggiesTools.gecko_compat (start.py, the GUI, the exe)
    from . import slogger
    from .Dol import dolfile
    from .Roster import dol_hammerspace
except ImportError:                        # as a bare module, SluggiesTools on sys.path
    _HERE = os.path.dirname(os.path.abspath(__file__))
    for _path in (_HERE, os.path.join(_HERE, 'Roster')):
        if _path not in sys.path:
            sys.path.insert(0, _path)
    import slogger
    from Dol import dolfile
    import dol_hammerspace

SOURCE = 'gecko'
OUTPUT_FOLDER = '3_Output_Dat'
INPUT_FOLDER = '1_Input'
# Set by the dispatcher (``--accept-gecko-break``, or after its own question) for the commands it runs.
ACCEPT_ENV = 'SLUGGIES_GECKO_ACCEPTED'
WARNING = ('Gecko code compatibility: this change moves the game memory (DOL hammerspace). Gecko codes made '
           'for the stock game (e.g. CPU vs CPU) will stop working with these files.')
QUESTION = WARNING + '\nContinue anyway? (y/n) '
# A pending grid edit that breaks it (tooltips, the slot level).
PENDING_WARNING = ('Gecko code compatibility break: once written, some Gecko codes made for the stock game (e.g. CPU vs '
                   'CPU) may stop working.')


@dataclass(frozen=True)
class Status:
    compatible: bool
    detail: str


def status(dol: bytes) -> Status:
    """Whether a ``main.dol`` keeps the stock MEM1 arena start (no DOL hammerspace), and why not."""
    try:
        image = dolfile.DolImage(dol)
        hs = dol_hammerspace.DolHammerspace.open(image)
        arena = dol_hammerspace.arena_values(image)
    except RuntimeError as exc:            # DolError and its subclasses (one per import path)
        return Status(False, f'main.dol could not be read ({exc})')
    if hs is not None:
        what = 'roster code and tables' if hs.has_data else 'game option code (CPU vs CPU, player memory)'
        return Status(False, f'DOL hammerspace with {what}; MEM1 arena start 0x{hs.arena_low:08X}')
    moved = [v for v in arena if v not in dol_hammerspace.STOCK_ARENA_VALUES]
    if moved:
        return Status(False, f'MEM1 arena start moved to 0x{moved[0]:08X} (another DOL extension)')
    return Status(True, 'stock memory layout / no DOL hammerspace')


def status_of_file(path: str) -> Status | None:
    """:func:`status` of a ``main.dol`` file, None when there is none."""
    if not os.path.isfile(path):
        return None
    with open(path, 'rb') as f:
        return status(f.read())


def breaks(before: bytes, after: bytes) -> bool:
    """Whether writing ``after`` over ``before`` breaks Gecko code compatibility (compatible now, not after)."""
    return status(before).compatible and not status(after).compatible


def check_path(root_dir: str) -> str:
    """Where dry runs record a break for the GUI's summary dialogs (``_gui/gecko_check.json``)."""
    return os.path.join(root_dir, '_gui', 'gecko_check.json')


def clear_check(root_dir: str) -> None:
    try:
        os.remove(check_path(root_dir))
    except OSError:
        pass


def record(root_dir: str, what: str, **details) -> None:
    """A dry run found a break: log the warning and record it (and ``details``) for the GUI."""
    slogger.warning(f'{WARNING} ({what})', source=SOURCE)
    path = check_path(root_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'breaks': True, 'what': what, **details}, f)


def read_details(root_dir: str) -> dict:
    """What the last dry run recorded (``{'breaks': True, ...}``), ``{}`` when it found no break."""
    try:
        with open(check_path(root_dir), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get('breaks') else {}


def read_check(root_dir: str) -> bool:
    """Whether the last dry run recorded a break."""
    return bool(read_details(root_dir))


def accepted() -> bool:
    return bool(os.environ.get(ACCEPT_ENV))


def accept() -> None:
    """The user agreed: the commands this process runs from now on (its children) don't ask again."""
    os.environ[ACCEPT_ENV] = '1'


def confirm(what: str) -> bool:
    """Ask before a break (GUI: a ``danger`` popup that is never skipped). True when the user agreed or had
    already agreed (:data:`ACCEPT_ENV`); a closed input counts as no."""
    if accepted():
        slogger.info(f'Gecko code compatibility given up for {what} (accepted earlier)', source=SOURCE)
        return True
    try:
        answer = slogger.ask(QUESTION, kind='danger')
    except EOFError:
        answer = ''
    if answer.strip().lower() in ('y', 'yes'):
        slogger.info(f'Gecko code compatibility given up for {what} (confirmed)', source=SOURCE)
        accept()
        return True
    slogger.warning(f'Cancelled: {what} would break Gecko code compatibility; nothing was written.', source=SOURCE)
    return False


def _option(args: tuple, name: str) -> list[str]:
    """The values after ``name`` in a command line, up to the next flag."""
    if name not in args:
        return []
    out = []
    for value in args[args.index(name) + 1:]:
        if value.startswith('--'):
            break
        out.append(value)
    return out


def _after(args: tuple, root_dir: str) -> bytes | None:
    """``main.dol`` as one planned ``start.py`` command would leave it, built in memory; None for commands that
    never add DOL hammerspace (model patches, stat edits, roster resets, re-reads, ...)."""
    output_dir = os.path.join(root_dir, OUTPUT_FOLDER)
    if args[:1] == ('--roster',) and '--remove' not in args:
        try:
            from .Roster import runner
        except ImportError:
            import runner
        state, config = _option(args, '--state'), _option(args, '--config')
        if not (state or config):
            return None
        absolute = lambda p: p if os.path.isabs(p) else os.path.join(root_dir, p)   # noqa: E731
        result = runner.run(output_dir, config_path=absolute(config[0]) if config else None, dry_run=True,
                            input_dir=os.path.join(root_dir, INPUT_FOLDER),
                            state_path=absolute(state[0]) if state else None)
        return result['dol_bytes']
    if args[:1] == ('--game-options',) and _option(args, '--on'):
        try:
            from .GameOptions import runner as options_runner
        except ImportError:
            from GameOptions import runner as options_runner
        return options_runner.build(output_dir, _option(args, '--on'), _option(args, '--off'))[1]
    return None


def chain_breaks(commands, root_dir: str) -> bool:
    """Whether the planned ``start.py`` commands (in order) would break Gecko code compatibility of the output
    ``main.dol``; False when it is not compatible now (nothing left to break). A command whose in-memory build
    fails counts as not breaking: it fails the same way when it runs, and its own write-point check still asks."""
    now = status_of_file(os.path.join(root_dir, OUTPUT_FOLDER, 'main.dol'))
    if now is None or not now.compatible:
        return False
    for args in commands:
        try:
            after = _after(tuple(args), root_dir)
        except (RuntimeError, ValueError, OSError):
            continue
        if after is not None and not status(after).compatible:
            return True
    return False


def guard_chain(commands, what: str, root_dir: str, dry_run: bool = False) -> bool:
    """The check for a planned chain, before its first command: True to run it. Asks once; the chain's
    commands then pass their own write-point checks (:data:`ACCEPT_ENV`). A dry run only records a break."""
    if accepted() and not dry_run:
        return True
    if not chain_breaks(commands, root_dir):
        return True
    if dry_run:
        record(root_dir, what)
        return True
    return confirm(what)
