"""Where the Sluggers Stat Editor is deployed and how Sluggies Tools talks to it (no Dear PyGui; ``gui_tab`` draws).

The GUI's path setting may name:

* a folder that holds release folders (``sluggers-stat-editor-v4.3``, ...): the highest version is used, so a newer
  release unpacked next to the old one is picked up without changing the setting;
* a release folder (``sluggers-stat-editor.exe``; a build's ``dist`` folder counts too);
* a source folder (``editor.py``: a checkout of the editor's repository, or a release's ``Source-Code``).

Versions are decimal numbers, not dotted tuples (the release history runs 3.6, 4.0, 4.01, 4.02, 4.1, ...), so
``4.02 < 4.1``. Bridge Mode support: a release ships a ``Bridge/`` folder next to the exe (and the module in
``Source-Code``); a source folder has ``sluggies_bridge.py`` next to ``editor.py`` (its ``Bridge/`` is gitignored
and may not exist yet). The exchange files live in that ``Bridge/`` folder: Sluggies writes ``stat_bridge.json``
before it launches the editor, the editor writes ``stat_edits.json`` on **Send to Sluggies**.

Bridge files live for one editor session: Sluggies deletes a leftover ``stat_bridge.json`` at start-up, before
every launch and when the editor exits; a ``stat_edits.json`` is moved out of ``Bridge/`` into the GUI's staging
folder (``take_edits``) before it is checked, so a later start of the editor outside Sluggies runs Standalone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

EXE = 'sluggers-stat-editor.exe'
SOURCE = 'editor.py'
BRIDGE_MODULE = 'sluggies_bridge.py'
SOURCE_CODE = 'Source-Code'               # a release's copy of the sources
BRIDGE_FOLDER = 'Bridge'
BRIDGE_FILE = 'stat_bridge.json'
EDITS_FILE = 'stat_edits.json'
VERSION_FOLDER = re.compile(r'^sluggers-stat-editor-v(\d+(?:\.\d+)?)$', re.IGNORECASE)
WEBSITE = 'https://philenarion.github.io/Sluggers-Stat-Editor/'
RELEASES_PAGE = 'https://github.com/Philenarion/Sluggers-Stat-Editor/releases'
LATEST_RELEASE_API = 'https://api.github.com/repos/Philenarion/Sluggers-Stat-Editor/releases/latest'
SETTING = 'stat_editor_path'               # the GUI settings key (``gui_settings``)
RECEIVED_PREFIX = 'stat_edits_'            # received edit files in the staging folder: stat_edits_<time>.json
STAGING_REL = os.path.join('3_Output_Dat', '_gui', 'stat')
LOG_NAME = 'editor.log'                    # the editor's stdout / stderr of the last run, in the staging folder
RELEASE, SOURCE_KIND = 'release', 'source'


def parse_version(text: str | None) -> Decimal | None:
    """``v4.3`` / ``4.3`` / ``sluggers-stat-editor-v4.02`` -> ``Decimal('4.3')`` / ``Decimal('4.02')``; None when it
    is not a version."""
    if not text:
        return None
    match = VERSION_FOLDER.match(text) or re.fullmatch(r'v?(\d+(?:\.\d+)?)', text.strip(), re.IGNORECASE)
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def version_text(version: Decimal | None) -> str:
    return 'unknown version' if version is None else f'v{version}'


@dataclass(frozen=True)
class Install:
    """One deployed stat editor: ``folder`` holds the exe (``release``) or ``editor.py`` (``source``)."""
    folder: str
    kind: str
    version: Decimal | None

    @property
    def program(self) -> str:
        return os.path.join(self.folder, EXE if self.kind == RELEASE else SOURCE)

    @property
    def bridge_folder(self) -> str:
        """Where the editor looks for the bridge: next to the exe, or next to ``editor.py`` (its ``bridge_folder``)."""
        return os.path.join(self.folder, BRIDGE_FOLDER)

    @property
    def bridge_path(self) -> str:
        return os.path.join(self.bridge_folder, BRIDGE_FILE)

    @property
    def edits_path(self) -> str:
        return os.path.join(self.bridge_folder, EDITS_FILE)

    @property
    def bridge_mode(self) -> bool:
        """Whether this editor has Bridge Mode."""
        if self.kind == SOURCE_KIND:
            return os.path.isfile(os.path.join(self.folder, BRIDGE_MODULE))
        return (os.path.isdir(self.bridge_folder)
                or os.path.isfile(os.path.join(self.folder, SOURCE_CODE, BRIDGE_MODULE)))

    def describe(self) -> str:
        kind = 'release' if self.kind == RELEASE else 'source (python editor.py)'
        mode = 'Bridge Mode supported' if self.bridge_mode else 'no Bridge Mode (Standalone only)'
        return f'{kind}, {version_text(self.version)}, {mode}'

    def command(self, python: list[str] | None) -> list[str] | None:
        """The command line that starts it (a source folder needs ``python``; None when there is none)."""
        if self.kind == RELEASE:
            return [self.program]
        return None if not python else [*python, self.program]


def _release_in(folder: str) -> Install | None:
    if os.path.isfile(os.path.join(folder, EXE)):
        return Install(folder, RELEASE, parse_version(os.path.basename(folder)))
    return None


def find_install(path: str | None) -> Install | None:
    """The stat editor the GUI's path setting names (see the module text); None when there is none. A file (the exe,
    ``editor.py``) stands for its folder."""
    if not path:
        return None
    path = os.path.abspath(os.path.expanduser(path.strip().strip('"')))
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if not os.path.isdir(path):
        return None
    found = _release_in(path)
    if found is not None:
        return found
    if os.path.isfile(os.path.join(path, SOURCE)):
        return Install(path, SOURCE_KIND, None)
    best = None
    try:
        names = os.listdir(path)
    except OSError:
        return None
    for name in names:
        version = parse_version(name) if VERSION_FOLDER.match(name) else None
        candidate = _release_in(os.path.join(path, name)) if version is not None else None
        if candidate is not None and (best is None or version > best.version):
            best = candidate
    return best


def setting_for(picked: str) -> str:
    """What the setting stores for a picked exe / ``editor.py`` / folder: a versioned release folder's parent, so a
    newer release unpacked beside it is found later; otherwise the folder itself."""
    folder = os.path.abspath(picked)
    if os.path.isfile(folder):
        folder = os.path.dirname(folder)
    if VERSION_FOLDER.match(os.path.basename(folder)) and _release_in(folder) is not None:
        return os.path.dirname(folder)
    return folder


def python_command(frozen: bool | None = None, which=shutil.which) -> list[str] | None:
    """How to run a source folder's ``editor.py``: this Python when Sluggies runs from source; from the packaged
    exe a Python on PATH (``pythonw``/``python``, else the ``py`` launcher). None when there is none."""
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    if not frozen:
        return [sys.executable]
    for name in ('pythonw', 'python'):
        found = which(name)
        if found:
            return [found]
    found = which('py')
    return [found, '-3'] if found else None


# --------------------------------------------------------------------------
# Bridge files
# --------------------------------------------------------------------------

def _regular_file(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)          # lstat: a link is never "the file"
    except OSError:
        return False


def remove_bridge(install: Install) -> bool:
    """Delete a left ``Bridge/stat_bridge.json`` (only that path, only a regular file); True when one was removed."""
    path = install.bridge_path
    if not _regular_file(path):
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    return True


def take_edits(install: Install, staging_dir: str, now: float | None = None) -> str | None:
    """Move ``Bridge/stat_edits.json`` (a regular file) into ``staging_dir`` as ``stat_edits_<time>.json`` and return
    the new path; None when there is none. The staged copy stays valid after ``Bridge/`` is cleaned."""
    source = install.edits_path
    if not _regular_file(source):
        return None
    os.makedirs(staging_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(time.time() if now is None else now))
    target = os.path.join(staging_dir, f'{RECEIVED_PREFIX}{stamp}.json')
    n = 2
    while os.path.exists(target):
        target = os.path.join(staging_dir, f'{RECEIVED_PREFIX}{stamp}-{n}.json')
        n += 1
    shutil.copyfile(source, target)
    try:
        os.remove(source)
    except OSError:
        pass                    # the copy is what is staged; a left original is moved again on the next exit
    return target


def received_files(staging_dir: str) -> list[str]:
    try:
        names = sorted(os.listdir(staging_dir))
    except OSError:
        return []
    return [os.path.join(staging_dir, n) for n in names if n.startswith(RECEIVED_PREFIX) and n.endswith('.json')]


def prune_received(staging_dir: str, keep) -> list[str]:
    """Delete received edit files no pending edit (or waiting offer) uses any more; returns the removed paths."""
    keep = {os.path.normcase(os.path.abspath(p)) for p in keep}
    removed = []
    for path in received_files(staging_dir):
        if os.path.normcase(os.path.abspath(path)) in keep:
            continue
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    return removed


# --------------------------------------------------------------------------
# Updates
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Release:
    version: Decimal
    tag: str
    page: str


def parse_latest_release(data) -> Release:
    """The GitHub "latest release" answer -> its version, tag and page; ValueError when it holds no version."""
    if isinstance(data, (bytes, str)):
        data = json.loads(data)
    tag = (data or {}).get('tag_name') if isinstance(data, dict) else None
    version = parse_version(tag)
    if version is None:
        raise ValueError(f'the latest release has no version tag ({tag!r})')
    return Release(version, tag, data.get('html_url') or RELEASES_PAGE)


def fetch_latest_release(timeout: float = 10.0) -> Release:
    """Ask GitHub for the latest published release (blocking; the GUI calls it on a worker thread)."""
    import urllib.request
    request = urllib.request.Request(LATEST_RELEASE_API, headers={
        'Accept': 'application/vnd.github+json', 'User-Agent': 'Sluggies-Tools'})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return parse_latest_release(answer.read())


def update_text(install: Install | None, latest: Release) -> tuple[str, bool]:
    """The update check's line and whether a newer release exists."""
    if install is None or install.version is None:
        what = 'a source folder' if install is not None else 'no stat editor'
        return f'Latest release: {latest.tag}. The deployed editor is {what}, so its version is unknown.', False
    if latest.version > install.version:
        return f'A newer release exists: {latest.tag} (deployed: {version_text(install.version)}).', True
    return f'Up to date: {version_text(install.version)} is the latest release.', False
