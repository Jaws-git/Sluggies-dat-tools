"""``start.py --export-slot 0xNN``: export a character slot as the game holds it into ``2_Output_Models``.

The GUI's "Export as .sluggie" (character grid, right-click menu). Writes a folder that looks like a regular
model export -- the High and Low model, the bats and gloves, the ``anm`` files and the portraits the slot shows
(``icon/``) -- under ``2_Output_Models/Custom <name> <NN>``; ``NN`` counts up from 01 past the folders and file
names already there.

Each model is the slot's **current** block (``3_Output_Dat``), read with the regular export reader as if it lay
where its *base model* lies in ``1_Input``: the vanilla file the slot's model directory started from (a stock
slot's own, a new ID's model source). So ``ChunkNumber`` / ``FileIndex`` / ``ModelOffset`` are the base model's
and the ``.sluggie`` can go into any slot the base model fits (``--patch-slot``, ``--target-id``), not just the
one it came from. The block itself rides along as ``DonorEntry`` (``{"Offset", "Data"}``: the whole DOL entry,
archive prefix included): it is the donor every patch of this file builds from (``HammerspaceMain.donor_entry``),
because the bytes in ``1_Input`` at those offsets are the base model's, not this one's. A slot export therefore
always goes through Hammerspace (``start._needs_hammerspace``; the in-place patcher refuses it). ``SlotExport``
records where it came from (informational only).

Folders are named as in a regular export (``78277664_mario.gpl``, the High/Low partner and bundled-gear lookups
``slot_plan._partner`` / ``gear.find_gear`` pair them by name); the ``.sluggie`` files get the folder's character
name and number behind the geo stem (``Custom Fire Mario 01`` -> ``78277664_mario_FireMario01.gpl.sluggie``), so
a bare file name stays unique in ``2_Output_Models``.

Reads the game files only; writes nothing but the new folder.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
_HS_DIR = os.path.join(_TOOLS_DIR, 'Hammerspace')
for _path in (_TOOLS_DIR, _HERE, _HS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import slogger  # noqa: E402

try:
    from . import gear, model_icons, names, slots, state, state_cli
except ImportError:
    import gear
    import model_icons
    import names
    import slots
    import state
    import state_cli

SOURCE = 'roster.slot_export'
ROOT = os.path.normpath(os.path.join(_TOOLS_DIR, '..'))
MODELS_DIR = os.path.join(ROOT, '2_Output_Models')
RESULT_FILE = 'slot_export.json'           # in 3_Output_Dat/_gui: the folder the last export wrote (the GUI reads it)
FOLDER_PREFIX = 'Custom '
DONOR_KEY = 'DonorEntry'                   # HammerspaceMain.DONOR_ENTRY_KEY
META_KEY = 'SlotExport'
MAX_NUMBER = 999
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class SlotExportError(RuntimeError):
    pass


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


# --------------------------------------------------------------------------
# Names (pure)
# --------------------------------------------------------------------------

def safe_name(text: str | None) -> str:
    """``text`` as a Windows folder name part: no reserved characters, single spaces, no trailing dots."""
    cleaned = ' '.join(_UNSAFE.sub(' ', text or '').split()).rstrip(' .')
    return cleaned


def folder_name(character: str, number: int) -> str:
    """``Custom Mario 01``."""
    return f'{FOLDER_PREFIX}{character} {number:02d}'


def tag(character: str, number: int) -> str:
    """The export's tag in its .sluggie names: the folder's character name without spaces and punctuation, then
    ``NN`` (``Fire Mario``, 1 -> ``FireMario01``)."""
    return (''.join(ch for ch in character if ch.isalnum()) or 'Slot') + f'{number:02d}'


def sluggie_name(model_name: str, export_tag: str) -> str:
    """A model's ``.sluggie`` name with the export's tag behind its geo stem: ``78277664_mario.gpl`` ->
    ``78277664_mario_FireMario01.gpl.sluggie`` (``L_`` prefix and extension stay where they are). The model
    folder keeps the regular name: the High/Low partner lookup pairs folders by their geo stem."""
    stem, dot, rest = model_name.partition('.')
    return f'{stem}_{export_tag}{dot}{rest}.sluggie'


def pick_number(character: str, model_names: list[str], taken_names: set[str], exists) -> int:
    """The lowest ``NN`` (from 01) whose folder ``exists(folder)`` says is free and whose ``.sluggie`` names are
    not in ``taken_names`` (every file name under ``2_Output_Models``, case-folded)."""
    for number in range(1, MAX_NUMBER + 1):
        if exists(folder_name(character, number)):
            continue
        names = {sluggie_name(name, tag(character, number)).casefold() for name in model_names}
        if not names & taken_names:
            return number
    raise SlotExportError(f'{folder_name(character, MAX_NUMBER)} and every lower number are taken')


def taken_names(models_dir: str) -> set[str]:
    """Every file name under ``models_dir``, case-folded (Windows names ignore case)."""
    return {name.casefold() for _root, _dirs, files in os.walk(models_dir) for name in files}


def character_name(char: dict) -> str:
    """The folder's character name as the grid shows it (``gui_grid.name_of``): a spare row's usual name while
    its table text is the stock "#N/A", else the English name, else ``Slot 0xNN``."""
    for text in (char.get('default_name'), (char.get('name') or {}).get('en')):
        text = safe_name(text)
        if text and text != names.UNNAMED:
            return text
    return f'Slot {_hex(char["id"])}'


# --------------------------------------------------------------------------
# Reading a block with the export reader
# --------------------------------------------------------------------------

class ShiftedFile:
    """A DAT file read with shifted offsets: absolute offset ``pos`` reads the file at ``pos + shift``. A slot file
    at output offset ``o`` is read as if it lay at its base offset ``b`` with ``shift = o - b``. The export reader
    seeks to ``absolute + position``, and some vanilla files are read past their DOL entry's end (Wario's file 5):
    the bytes behind the entry are the DAT's, as in the regular export."""

    def __init__(self, handle, shift: int):
        self.handle, self.shift = handle, shift

    def seek(self, pos, whence=0):
        if whence == 0:
            pos += self.shift
        return self.handle.seek(pos, whence) - self.shift

    def tell(self):
        return self.handle.tell() - self.shift

    def read(self, size=-1):
        return self.handle.read(size)


class PlacedBlock(io.BytesIO):
    """A DOL entry's bytes alone, read with absolute offsets as if it lay at ``base`` in a DAT (tests)."""

    def __init__(self, base: int, data: bytes):
        super().__init__(data)
        self.base = base

    def seek(self, pos, whence=0):
        if whence == 0:
            if pos < self.base:
                raise ValueError(f'read at 0x{pos:08X} is before the block at 0x{self.base:08X}')
            pos -= self.base
        return super().seek(pos, whence) + self.base

    def tell(self):
        return super().tell() + self.base


def _exporter():
    """The export reader (``export.py``: importable since its script body moved into ``main()``)."""
    import export
    return export


def read_entry(base: int, length: int, source):
    """The analyzed entry (``MaybeArchive``) of the ``length`` bytes at ``base`` in ``source`` (a file read with
    absolute offsets: ``ShiftedFile``, ``PlacedBlock``); ``.child`` is its ``Model0``, ``Archive`` or ``ANM``
    (None: nothing the export writes)."""
    export = _exporter()
    entry = export.MaybeArchive(source, base, length, 'unnamed')
    entry.analyze()
    if entry.child is not None and not isinstance(entry.child, export.ANM):
        entry.child.analyze()
    return entry


def models_of(entry) -> list:
    """The ``Model0`` objects an analyzed entry exports: the model itself, or an archive's readable members."""
    export = _exporter()
    child = entry.child
    if isinstance(child, export.Archive):
        return [child.files[i] for i in child.success]
    if isinstance(child, export.Model0):
        return [child]
    return []


def document(model, chunk: int, file_index: int, entry_base: int, entry_bytes: bytes, meta: dict) -> dict:
    """The ``.sluggie`` of ``model`` (one model of the entry at ``entry_base``): the regular export's document plus
    ``SlotExport`` (after ``ModelLength``), ``UseHammerspace`` and ``DonorEntry`` (the whole entry)."""
    export = _exporter()
    from binfmt import encode_field
    base = export.model_document(model, chunk, file_index, model.absolute, model.length)['SluggiesModel']
    out = {}
    for key, value in base.items():
        out[key] = value
        if key == 'ModelLength':
            out[META_KEY] = meta
    out['UseHammerspace'] = True
    out[DONOR_KEY] = {'Offset': hex(entry_base), 'Data': encode_field(entry_bytes, base.get('UseBase64', True))}
    return {'SluggiesModel': out}


# --------------------------------------------------------------------------
# The slot
# --------------------------------------------------------------------------

def directory_files(model_dir: int) -> list[tuple[int, int, int]]:
    """``(file index, offset, length)`` of every file of ``model_dir`` in the output DOL."""
    import HammerspaceHelper as hh
    files = [(index, words[2], words[1]) for chunk, index, _record, words in hh._iterDirRecords(hh.OUTPUT_DOL)
             if chunk == model_dir]
    return sorted(files)


def base_entry(model_dir: int, file_index: int) -> tuple[int, int, int, int]:
    """``(chunk, file index, offset, length)`` of the ``1_Input`` file a slot file started from."""
    import HammerspaceHelper as hh
    route = hh.vanillaRoute(model_dir, file_index)
    if route is None:
        raise SlotExportError(f'directory {model_dir} is an own model directory whose source the roster manifest '
                              'does not name')
    offset, length = hh.readDolEntry(*route)
    if offset == -1 or length <= 0:
        raise SlotExportError(f'file {file_index} of directory {route[0]} is not in 1_Input/main.dol')
    return route[0], route[1], offset, length


class Entry:
    """One file of the slot, read and analyzed."""

    def __init__(self, file_index: int, chunk: int, base: int, data: bytes, source):
        self.file_index, self.chunk, self.base, self.data = file_index, chunk, base, data
        self.entry = read_entry(base, len(data), source)
        self.models = models_of(self.entry)

    @property
    def is_anm(self) -> bool:
        return isinstance(self.entry.child, _exporter().ANM)


def read_slot(cid: int, dat_handle, output_dir: str = state_cli.OUTPUT_DIR):
    """``(character, entries, portraits)`` of slot ``cid`` as the output files hold it; ``dat_handle``: the output
    ``dt_na.dat`` opened for reading, which the entries read from until they are written."""
    image, dat = state_cli._open(output_dir)
    st = state.read_state(image, dat)
    char = next((c for c in st['characters'] if c['id'] == cid), None)
    if char is None:
        raise SlotExportError(f'{_hex(cid)} is not a slot of this roster (not on any square)')
    model_dir = slots.model_dir(image, cid)
    entries = []
    for file_index, offset, length in directory_files(model_dir):
        if length <= 0:
            continue
        data = dat.read(offset, length)
        if file_index in gear.ROLES and gear.is_placeholder(data):
            continue
        chunk, _file, base, _base_length = base_entry(model_dir, file_index)
        _exporter().set_log_dir_index(chunk)
        try:
            entries.append(Entry(file_index, chunk, base, data, ShiftedFile(dat_handle, offset - base)))
        except Exception as exc:                 # the export reader's own errors: one unreadable file is skipped
            if file_index in (0, 1):
                raise SlotExportError(f'file {file_index} of the slot ({len(data):,} bytes) cannot be read: '
                                      f'{type(exc).__name__}: {exc}') from exc
            slogger.warning(f'file {file_index} skipped: it cannot be read ({type(exc).__name__}: {exc})',
                            source=SOURCE)
    if not any(e.file_index == 0 and e.models for e in entries):
        raise SlotExportError(f'{_hex(cid)} has no readable High model (file 0 of directory {model_dir})')
    try:
        from . import slot_cli
    except ImportError:
        import slot_cli
    env = slot_cli.FileEnv(image, dat)
    portraits = {view: env.shown_portrait(char, view) for view in ('front', 'side')}
    return char, entries, portraits


def write_export(cid: int, char: dict, entries: list[Entry], portraits: dict, models_dir: str = MODELS_DIR,
                 today: str | None = None) -> str:
    """Write the export folder; returns its path. Written into a hidden folder first and renamed at the end, so a
    failure leaves nothing half-written."""
    export = _exporter()
    character = character_name(char)
    names = [m.name for e in entries for m in e.models]
    number = pick_number(character, names, taken_names(models_dir),
                         lambda folder: os.path.exists(os.path.join(models_dir, folder)))
    folder = os.path.join(models_dir, folder_name(character, number))
    export_tag = tag(character, number)
    work = os.path.join(models_dir, f'.slot_export_{os.getpid()}')
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)
    meta = {'Character': _hex(cid), 'Name': character,
            'Exported': today or datetime.date.today().isoformat()}
    try:
        high_folder = None
        for e in entries:
            export.set_log_dir_index(e.chunk)
            if e.is_anm:
                e.entry.child.dumpRaw(work, e.file_index)
                continue
            e.entry.child.toFile(work + os.sep, export_tex=True, export_glb=False)
            for model in e.models:
                model_folder = os.path.join(work, str(e.entry.child.absolute), model.name) \
                    if isinstance(e.entry.child, export.Archive) else os.path.join(work, model.name)
                if not os.path.isdir(model_folder):
                    raise SlotExportError(f'file {e.file_index}: the export reader wrote no folder for {model.name}')
                _check_textures(model, model_folder)
                export.write_model_document(document(model, e.chunk, e.file_index, e.base, e.data, meta),
                                            os.path.join(model_folder, sluggie_name(model.name, export_tag)))
                if e.file_index == 0 and high_folder is None:
                    high_folder = model_folder
        _write_portraits(high_folder, portraits)
        os.rename(work, folder)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return folder


def _check_textures(model, model_folder: str) -> None:
    """Every texture of a model with a TEX section has its PNG (``Model0.toFile`` logs a failure, never raises)."""
    palette = getattr(model, 'TEXPalette', None)
    if palette is None:
        return
    tex = os.path.join(model_folder, 'tex')
    expected = {d.dolphinTextureBasename() + '.png' for d in palette.descriptors}
    missing = sorted(n for n in expected if not os.path.isfile(os.path.join(tex, n)))
    if missing:
        raise SlotExportError(f'{model.name}: texture export failed for {", ".join(missing)} (is wimgt installed?)')


def _write_portraits(high_folder: str | None, portraits: dict) -> None:
    if high_folder is None:
        return
    missing = [view for view, image in portraits.items() if image is None]
    if missing:
        slogger.warning(f'portraits not written ({", ".join(missing)} unreadable): a slot this export is patched '
                        'into keeps its own', source=SOURCE)
        return
    icon_dir = os.path.join(high_folder, model_icons.ICON_SUBDIR)
    os.makedirs(icon_dir, exist_ok=True)
    for view, image in portraits.items():
        image.save(os.path.join(icon_dir, model_icons.FILES[view]))


def result_path(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(output_dir, state_cli.GUI_DIR, RESULT_FILE)


def run(cid: int, output_dir: str = state_cli.OUTPUT_DIR, models_dir: str = MODELS_DIR) -> str:
    dat_path = os.path.join(output_dir, 'dt_na.dat')
    if not os.path.isfile(dat_path):
        raise SlotExportError(f'{dat_path} is missing')
    with open(dat_path, 'rb') as dat_handle:
        char, entries, portraits = read_slot(cid, dat_handle, output_dir)
        folder = write_export(cid, char, entries, portraits, models_dir)
    path = result_path(output_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'id': _hex(cid), 'folder': folder}, f, indent=2)
    return folder


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Export a character slot as .sluggie files (GUI character grid).')
    parser.add_argument('id', metavar='0xNN', help='the slot (character ID)')
    parser.add_argument('--output-dir', default=state_cli.OUTPUT_DIR, help=argparse.SUPPRESS)
    parser.add_argument('--models-dir', default=MODELS_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    path = result_path(args.output_dir)
    if os.path.exists(path):
        os.remove(path)                          # a failed export leaves no stale result behind
    try:
        cid = slots.parse_id(args.id)
        folder = run(cid, args.output_dir, args.models_dir)
    except (RuntimeError, ValueError, OSError) as exc:     # SlotExportError, SlotError, StateError
        slogger.error(f'slot export failed, nothing written: {exc}', source=SOURCE)
        return 1
    files = sorted(os.path.relpath(os.path.join(r, f), folder) for r, _d, fs in os.walk(folder)
                   for f in fs if f.endswith('.sluggie'))
    slogger.info(f'{_hex(cid)} exported to {folder}', source=SOURCE)
    for name in files:
        slogger.info(f'  {name}', source=SOURCE)
    return 0


if __name__ == '__main__':
    sys.exit(main())
