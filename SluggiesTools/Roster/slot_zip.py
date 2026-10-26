"""Character packages (``.sluggiezip``): every aspect of one slot in one file, staged onto any slot at once.

**Export** (``start.py --export-sluggiezip 0xNN FILE``; the character grid's right-click "Export to .sluggiezip")
reads the slot as the game holds it (``3_Output_Dat``; the game files are only read) and writes one zip (deflated,
like roster and voice packs):

* ``manifest.json``: what the package holds (``MANIFEST`` below), every path relative to the zip root;
* ``files/``: the folder "Export as .sluggie" writes (``slot_export.write_export``): the High and Low model folders
  (``.sluggie`` + ``.glb`` + textures, portraits in the High folder's ``icon/``), the bats and gloves, ``anm/``;
* ``stats.json``: the slot's live stat values (``slot_cli.FileEnv.stat_snapshot``: every field, the size included,
  and its chemistry), named by field, so they fit any roster;
* ``voice.sluggiesvoice``: the slot's own custom voice clips as a voice pack (``Sound/sound_packs``), only when it
  has any.

``manifest.json``::

    {"format": "sluggiezip", "version": 1,
     "character": {"id": "0x66", "name": "Fire Mario", "exported": "2026-10-26"},
     "name": "Fire Mario",                                   # null: the slot had no name of its own
     "model": {"high": "files/...gpl.sluggie", "low": "files/L_...gpl.sluggie"},
     "equipment": {"2": {"file": "files/...", "changed": true}, ...},       # slot files 2-5
     "animations": {"6": {"file": "files/anm/...anm", "changed": false}, ...},   # banks 6-14
     "portraits": {"front": "files/.../icon/FrontIcon.png", "side": "..."},
     "stats": {"source": "0x00", "values": "stats.json"},
     "voice": {"source": "0x00", "own": true, "pack": "voice.sluggiesvoice"}}

``changed``: the file differs from the vanilla one its directory started from (``state_cli.add_vanilla_flags``;
unknown counts as changed). Unchanged gear and banks ride along (a complete export) but are not staged: a model
patch brings the base's own files anyway.

**Load** (``stage``; the slot level's "Import .sluggiezip package") extracts the package into
``_gui/slot/staged_packages`` and turns each aspect into the edits the slot buttons stage (one staging check for
all of them, so one confirm dialog): the model pair (``patch``, its bundled-gear lookup off), the changed bats /
gloves (``equip``) and animation banks (``anim``), the portraits (``icon``), the name (``rename``), the stats source
(``stats``) plus the values as a stat edit file for the target (``stat_edits``, made for the current output
``main.dol``), and the own voice (``char_voice``; custom clips become a voice pack in ``_gui/Sound_Packs``). An
aspect whose file is missing from the archive, or that the target cannot take, is skipped with a note; the rest is
staged. Chemistry is written in the target's row only, towards stock characters (new IDs differ per roster).
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import posixpath
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
for _path in (_TOOLS_DIR, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import slogger  # noqa: E402

try:
    from . import ids, names, slot_export, slots, state_cli
except ImportError:
    import ids
    import names
    import slot_export
    import slots
    import state_cli

SOURCE = 'roster.slot_zip'
EXTENSION = '.sluggiezip'
FORMAT = 'sluggiezip'
VERSION = 1
MANIFEST = 'manifest.json'
FILES_DIR = 'files'
STATS_FILE = 'stats.json'
VOICE_FILE = 'voice.sluggiesvoice'
RESULT_FILE = 'sluggiezip_export.json'     # in _gui: the last export's path, or why it failed (the GUI reads it)
STAGED_REL = os.path.join('_gui', 'slot', 'staged_packages')
STAT_REL = os.path.join('_gui', 'stat')    # gui_grid.STAT_STAGING_REL: the Stat Editor tab prunes unused files
STAT_PREFIX = 'stat_edits_package_'        # starts with StatEditor/editor_install.RECEIVED_PREFIX
STAT_ORIGIN = 'sluggiezip'                 # the stat edit file's "origin" (gui_grid names the package from it)
ICON_VIEWS = ('front', 'side')
GEAR_FILES = (2, 3, 4, 5)
ANIM_FILES = tuple(range(6, 15))


class PackageError(RuntimeError):
    pass


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def manifest_of(char: dict, record: dict, snapshot: dict | None, voice: dict | None, today: str) -> dict:
    """The package's ``manifest.json`` (module docstring) from what ``write_export`` wrote (``record``), the slot's
    stat snapshot and its voice (``voice_of``)."""
    def path(rel):
        return posixpath.join(FILES_DIR, rel.replace(os.sep, '/'))

    flags = {e['file']: e.get('vanilla') for e in (char.get('equipment') or {}).values()}
    flags.update({e['file']: e.get('vanilla') for e in char.get('animations') or []})
    name = ((char.get('name') or {}).get('en') or '').strip()
    out = {'format': FORMAT, 'version': VERSION,
           'character': {'id': _hex(char['id']), 'name': slot_export.character_name(char), 'exported': today},
           'name': name if name and name != names.UNNAMED else None}
    if record.get(0):
        out['model'] = {'high': path(record[0][0])}
        if record.get(1):
            out['model']['low'] = path(record[1][0])
    for key, files in (('equipment', GEAR_FILES), ('animations', ANIM_FILES)):
        out[key] = {str(f): {'file': path(record[f][0]), 'changed': flags.get(f) is not True}
                    for f in files if record.get(f)}
    out['portraits'] = {view: path(p) for view, p in (record.get('portraits') or {}).items()}
    if snapshot is not None:
        out['stats'] = {'source': _hex(char['stats']) if char.get('stats') is not None else None,
                        'values': STATS_FILE}
    if voice is not None:
        out['voice'] = {'source': voice['source'], 'own': voice['own'],
                        'pack': VOICE_FILE if voice.get('clips') else None}
    return out


def voice_of(char: dict, derived) -> dict | None:
    """``{"source", "own", "clips", "pack"}`` of the slot's voice: its own voice entry in the derived config
    (``character_voices``: a stock voice and / or custom clips, whose RWAVs ``derived.clips`` holds), else the
    voice its square gives it (``own`` False). None when the state names none."""
    entry = None
    if derived is not None:
        entry = next((e for e in derived.config.get('character_voices') or []
                      if int(str(e.get('id')), 16) == char['id']), None)
    if entry is not None:
        clips = {m: derived.clips[n] for m, n in (entry.get('clips') or {}).items() if n in derived.clips}
        return {'source': entry.get('voice'), 'own': True, 'clips': clips,
                'pack': entry.get('pack') or char.get('char_pack')}
    if char.get('char_voice') is not None:
        return {'source': _hex(char['char_voice']), 'own': True, 'clips': {}, 'pack': None}
    if char.get('voice') is not None:
        return {'source': _hex(char['voice']), 'own': False, 'clips': {}, 'pack': None}
    return None


def voice_pack_bytes(voice: dict, name: str) -> bytes:
    """The ``.sluggiesvoice`` zip of the slot's custom clips (``sound_packs.read_pack_file`` reads it back)."""
    import io
    from Sound import sound_packs
    pack_name = voice.get('pack') or name
    if sound_packs.check_name(pack_name) is not None:
        pack_name = 'Character'
    doc = {'format': sound_packs.FORMAT, 'name': pack_name, 'voice': voice.get('source'),
           'clips': {m: {'rwav': m + '.rwav'} for m in sound_packs.MOMENT_KEYS if m in voice['clips']}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(sound_packs.PACK_FILE, json.dumps(doc, ensure_ascii=False, indent=1))
        for moment in doc['clips']:
            zf.writestr(moment + '.rwav', voice['clips'][moment])
    return buf.getvalue()


def write_package(path: str, folder: str, manifest: dict, snapshot: dict | None, voice: bytes | None) -> str:
    """Zip the export ``folder`` and the rest into ``path`` (``.tmp`` first, so a failure leaves no half file)."""
    if not path.lower().endswith(EXTENSION):
        path += EXTENSION
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
            for root, _dirs, files in os.walk(folder):
                for name in sorted(files):
                    full = os.path.join(root, name)
                    zf.write(full, posixpath.join(FILES_DIR, os.path.relpath(full, folder).replace(os.sep, '/')))
            if snapshot is not None:
                zf.writestr(STATS_FILE, json.dumps(snapshot, indent=1))
            if voice is not None:
                zf.writestr(VOICE_FILE, voice)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path


def export(cid: int, path: str, output_dir: str = state_cli.OUTPUT_DIR, today: str | None = None) -> str:
    """Write slot ``cid`` as the package ``path``; returns the path written."""
    try:
        from . import derive, slot_cli
    except ImportError:
        import derive
        import slot_cli
    today = today or datetime.date.today().isoformat()
    dat_path = os.path.join(output_dir, 'dt_na.dat')
    if not os.path.isfile(dat_path):
        raise PackageError(f'{dat_path} is missing')
    work = os.path.join(state_cli.gui_dir(output_dir), f'.sluggiezip_{os.getpid()}')
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    try:
        with open(dat_path, 'rb') as dat_handle:
            char, entries, portraits = slot_export.read_slot(cid, dat_handle, output_dir)
            record = {}
            folder = slot_export.write_export(cid, char, entries, portraits, work, today, record=record)
        flagged = {'characters': [copy.deepcopy(char)]}
        try:
            state_cli.add_vanilla_flags(flagged)
            char = flagged['characters'][0]
        except Exception as exc:                 # unknown flags: every file counts as changed
            slogger.warning(f'vanilla files could not be told apart ({exc}): every file is staged on load',
                            source=SOURCE)
        image, dat = state_cli._open(output_dir)
        snapshot = slot_cli.FileEnv(image, dat).stat_snapshot(cid)
        if snapshot is None:
            slogger.warning('stat values left out: the stat tables cannot be read', source=SOURCE)
        derived = None
        try:
            derived = derive.derive(image, dat, state_cli.open_sound(output_dir))
        except Exception as exc:                 # the voice falls back to the state's voice source
            slogger.warning(f'custom voice clips left out: {exc}', source=SOURCE)
        voice = voice_of(char, derived)
        if voice is not None and voice['own'] and char.get('char_clip_ids') and not voice['clips']:
            slogger.warning('custom voice clips left out: they cannot be read from 3_Output_Dat/sound_NA',
                            source=SOURCE)
        manifest = manifest_of(char, record, snapshot, voice, today)
        if 'model' not in manifest:
            raise PackageError(f'{_hex(cid)}: no High model was written')
        pack = voice_pack_bytes(voice, manifest['character']['name']) if voice and voice['clips'] else None
        return write_package(path, folder, manifest, snapshot, pack)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------
# Load
# --------------------------------------------------------------------------

@dataclass
class Staged:
    """What ``stage`` made of a package: the edits (one staging check), what was skipped and why, the folder it was
    extracted to and the voice pack it created (None: none, or one with the same clips existed)."""
    edits: list = field(default_factory=list)
    skipped: list = field(default_factory=list)       # ["Voice: ...", ...]
    folder: str = ''
    created_pack: str | None = None
    manifest: dict = field(default_factory=dict)


def read_manifest(zf: zipfile.ZipFile, label: str) -> dict:
    try:
        doc = json.loads(zf.read(MANIFEST).decode('utf-8'))
    except KeyError:
        raise PackageError(f'{label} is not a character package ({MANIFEST} missing)') from None
    except (UnicodeDecodeError, ValueError) as exc:
        raise PackageError(f'{label}: {MANIFEST} is not valid JSON: {exc}') from None
    if not isinstance(doc, dict) or doc.get('format') != FORMAT:
        raise PackageError(f'{label} is not a character package')
    if not isinstance(doc.get('version'), int) or doc['version'] > VERSION:
        raise PackageError(f'{label} is package version {doc.get("version")!r}: this version of Sluggies Tools reads '
                           f'up to {VERSION}')
    return doc


def extract(path: str, staged_dir: str) -> tuple[dict, str]:
    """``(manifest, folder)``: the package unpacked into a fresh folder under ``staged_dir``. Member names that
    leave the folder are refused."""
    label = os.path.basename(path)
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageError(f'{label} is not a character package: {exc}') from None
    with zf:
        manifest = read_manifest(zf, label)
        stem = slot_export.safe_name(os.path.splitext(label)[0]) or 'package'
        folder, n = os.path.join(staged_dir, stem), 2
        while os.path.exists(folder):
            folder, n = os.path.join(staged_dir, f'{stem} {n}'), n + 1
        root = os.path.abspath(folder)
        for info in zf.infolist():
            target = os.path.abspath(os.path.join(root, *info.filename.split('/')))
            if not target.startswith(root + os.sep):
                raise PackageError(f'{label}: unexpected member {info.filename!r}')
        os.makedirs(folder)
        zf.extractall(folder)
    return manifest, folder


def _file(folder: str, rel) -> str | None:
    """The extracted file ``rel`` (a manifest path), or None when the archive does not hold it."""
    if not isinstance(rel, str) or not rel:
        return None
    path = os.path.join(folder, *rel.split('/'))
    return path if os.path.isfile(path) else None


def _number(text) -> int | None:
    try:
        return int(str(text), 16)
    except (TypeError, ValueError):
        return None


def stat_doc(snapshot: dict, source: int, target: int, main_dol_sha1: str, label: str) -> tuple[dict, list[str]]:
    """The stat edit file (``StatEditor/apply.py``) that gives ``target`` the snapshot's values (exported from
    ``source``), for the ``main.dol`` with ``main_dol_sha1``; and the values left out (they do not fit). Chemistry:
    the target's row towards stock characters; the exported character's own pair becomes the target's own."""
    from StatEditor import fields
    groups, left_out = {}, []
    for group, values in (snapshot.get('fields') or {}).items():
        for name, text in (values or {}).items():
            try:
                f = fields.character_field(group, name)
                raw = bytes.fromhex(text)
                value = f.unpack(raw) if len(raw) == f.size else None
            except (fields.FieldError, TypeError, ValueError):
                value, f = None, None
            if f is None or value is None or f.problem(value):
                left_out.append(f'{group}.{name}')
                continue
            groups.setdefault(group, {})[name] = value
    chemistry = {}
    for text, pair in (snapshot.get('chemistry') or {}).items():
        other = _number(text)
        if other is None or not isinstance(pair, list) or len(pair) != 2:
            continue
        if other == source:
            other = target                             # the exported character's own pair
        elif other == target or other >= fields.CHEM_COLUMNS:
            continue                                   # the pair with the target stays; new IDs differ per roster
        if fields.CHEMISTRY.problem(pair[0]) is None:
            chemistry[_hex(other)] = pair[0]
    if chemistry:
        groups['chemistry'] = chemistry
    doc = {'format': 'sluggies-stat-edits', 'version': 1, 'main_dol_sha1': main_dol_sha1, 'origin': STAT_ORIGIN,
           'package': label, 'characters': {_hex(target): groups}}
    return doc, left_out


def write_stat_file(root_dir: str, doc: dict, now: float | None = None) -> str:
    folder = os.path.join(root_dir, STAT_REL)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(time.time() if now is None else now))
    path, n = os.path.join(folder, f'{STAT_PREFIX}{stamp}.json'), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f'{STAT_PREFIX}{stamp}-{n}.json'), n + 1
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1)
    return path


def install_voice_pack(root_dir: str, path: str) -> tuple[str, bool]:
    """``(pack name, created)``: the package's voice pack as a pack folder in ``_gui/Sound_Packs``. A pack of the
    same name with the same clips is reused; another one keeps its name and the package's gets ``Name 2``, ...."""
    from Sound import sound_packs
    pack, files = sound_packs.read_pack_file(path)
    name, n = pack.name, 1
    while True:
        existing = None
        try:
            existing = sound_packs.find_pack(root_dir, name)
        except (sound_packs.PackError, ValueError, OSError):
            existing = None
        if existing is None and not os.path.exists(sound_packs.pack_folder(root_dir, name)):
            return sound_packs.import_pack(root_dir, path, name).name, True
        if existing is not None and existing.voice == pack.voice and set(existing.clips) == set(pack.clips) and all(
                existing.rwav_bytes(m) == files[c.rwav] for m, c in pack.clips.items()):
            return name, False
        n += 1
        name = f'{pack.name[:44]} {n}'


def stage(path: str, cid: int, state: dict, root_dir: str, name_problem=None, can_portrait=None,
          sound_archive: bool | None = None) -> Staged:
    """Extract package ``path`` and make the edits that put it into slot ``cid`` (module docstring). ``state``: the
    GUI's read (target facts, ``sizes.main_dol_sha1``); ``name_problem(text)``: why a name does not fit the plate
    (None: fits); ``can_portrait(cid)``: whether the slot has portraits of its own; ``sound_archive``: whether
    ``1_Input/sound_NA/MY2.brsar`` is there (an own voice needs it; None: look)."""
    label = os.path.basename(path)
    if sound_archive is None:
        from Sound import brsar
        sound_archive = os.path.isfile(os.path.join(root_dir, '1_Input', brsar.ARCHIVE))
    manifest, folder = extract(path, os.path.join(root_dir, STAGED_REL))
    out = Staged(folder=folder, manifest=manifest)
    target = _hex(cid)
    char = next((c for c in state.get('characters') or [] if c.get('id') == cid), {})
    source = _number((manifest.get('character') or {}).get('id'))

    model = manifest.get('model') or {}
    high, low = _file(folder, model.get('high')), _file(folder, model.get('low'))
    if high:
        edit = {'op': 'patch', 'id': target, 'file': high, 'gear': False}
        if low:
            edit['low'] = low
        out.edits.append(edit)
    else:
        out.skipped.append('Models: the High model is not in the package')

    for key, op, what in (('equipment', 'equip', 'Equipment'), ('animations', 'anim', 'Animations')):
        missing = []
        for number, item in sorted((manifest.get(key) or {}).items(), key=lambda kv: int(kv[0])):
            if not isinstance(item, dict) or not item.get('changed', True):
                continue
            found = _file(folder, item.get('file'))
            if found is None:
                missing.append(number)
            elif op == 'equip':
                out.edits.append({'op': 'equip', 'id': target, 'file': int(number), 'sluggie': found, 'origin': 'user'})
            else:
                out.edits.append({'op': 'anim', 'id': target, 'anm': found})
        if missing:
            out.skipped.append(f'{what}: file{"s" if len(missing) > 1 else ""} {", ".join(missing)} not in the package')

    portraits = manifest.get('portraits') or {}
    if portraits and can_portrait is not None and not can_portrait(cid):
        out.skipped.append('Portraits: this slot shows the Mii icon (no portraits of its own)')
    else:
        for view in ICON_VIEWS:
            found = _file(folder, portraits.get(view))
            if found is None:
                out.skipped.append(f'{view.capitalize()} portrait: not in the package')
                continue
            out.edits.append({'op': 'icon', 'id': target, 'view': view, 'file': found, 'fit': 'strict',
                              'trim': False, 'origin': label})

    name = manifest.get('name')
    if isinstance(name, str) and name.strip():
        problem = name_problem(name) if name_problem is not None else None
        if problem:
            out.skipped.append(f'Name: {name!r}: {problem}')
        else:
            out.edits.append({'op': 'rename', 'id': target, 'text': name})
    else:
        out.skipped.append('Name: the package has none of its own')

    stats = manifest.get('stats') or {}
    values = _file(folder, stats.get('values'))
    sha1 = ((state.get('sizes') or {}).get('main_dol_sha1'))
    if values is None:
        out.skipped.append('Stats: not in the package')
    elif ids.PLAYER_END <= cid < ids.FIRST_NEW:
        out.skipped.append('Stats: a Mii has no stats rows of its own')
    else:
        stats_source = _number(stats.get('source'))
        if stats_source is not None and 0 <= stats_source < ids.PLAYER_END:
            out.edits.append({'op': 'stats', 'id': target, 'source': _hex(stats_source)})
        if not sha1:
            out.skipped.append('Stat values: the stat tables of 3_Output_Dat/main.dol could not be read (Refresh)')
        else:
            try:
                with open(values, 'r', encoding='utf-8') as f:
                    snapshot = json.load(f)
                doc, left_out = stat_doc(snapshot, source if source is not None else cid, cid, sha1, label)
            except (OSError, ValueError, AttributeError) as exc:
                out.skipped.append(f'Stat values: {STATS_FILE} cannot be read ({exc})')
            else:
                if left_out:
                    out.skipped.append(f'Stat values: {", ".join(left_out)} left out (they do not fit)')
                out.edits.append({'op': 'stat_edits', 'id': _hex(0xFF), 'file': write_stat_file(root_dir, doc)})

    voice = manifest.get('voice')
    if not isinstance(voice, dict):
        out.skipped.append('Voice: not in the package')
    else:
        voice_source = voice.get('source')
        pack_file = _file(folder, voice.get('pack')) if voice.get('pack') else None
        if voice.get('pack') and pack_file is None:
            out.skipped.append('Voice: the custom clips are not in the package (the stock voice stays)')
        shown = char.get('char_voice') if char.get('char_voice') is not None else char.get('voice')
        speaks_already = (not voice.get('own') and pack_file is None and _number(voice_source) == shown)
        if speaks_already:
            pass                                   # it speaks with that voice on its square already
        elif not sound_archive:
            out.skipped.append('Voice: needs the sound archive: copy the game\'s files/sound_NA/MY2.brsar into '
                               '1_Input/sound_NA first')
        elif pack_file is not None:
            from Sound import sound_packs
            try:
                pack_name, created = install_voice_pack(root_dir, pack_file)
            except (sound_packs.PackError, OSError, ValueError) as exc:
                out.skipped.append(f'Voice: the voice pack cannot be stored ({exc})')
            else:
                out.created_pack = pack_name if created else None
                out.edits.append({'op': 'char_voice', 'id': target, 'source': voice_source, 'pack': pack_name})
        elif voice_source is not None:
            out.edits.append({'op': 'char_voice', 'id': target, 'source': voice_source})
    return out


def remove_pack(root_dir: str, name: str | None) -> None:
    """Delete a voice pack ``stage`` created (the package was not staged after all)."""
    if not name:
        return
    from Sound import sound_packs
    shutil.rmtree(sound_packs.pack_folder(root_dir, name), ignore_errors=True)


def prune(staged_dir: str, keep: set[str]) -> None:
    """Delete extracted packages no pending edit uses any more (``keep``: paths the pending edits name)."""
    try:
        names_ = os.listdir(staged_dir)
    except OSError:
        return
    kept = [os.path.normcase(os.path.abspath(p)) for p in keep if p]
    for name in names_:
        folder = os.path.normcase(os.path.abspath(os.path.join(staged_dir, name)))
        if not any(p.startswith(folder + os.sep) for p in kept):
            shutil.rmtree(folder, ignore_errors=True)


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def result_path(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(state_cli.gui_dir(output_dir), RESULT_FILE)


def write_result(output_dir: str, result: dict) -> None:
    """The GUI's summary of the last export: ``id``, then ``path`` (written) or ``error`` (aborted)."""
    path = result_path(output_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Export a character slot as a .sluggiezip package (GUI character '
                                                 'grid).')
    parser.add_argument('id', metavar='0xNN', help='the slot (character ID)')
    parser.add_argument('path', help='the package to write (.sluggiezip)')
    parser.add_argument('--output-dir', default=state_cli.OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = result_path(args.output_dir)
    if os.path.exists(result):
        os.remove(result)                        # a failed export leaves no stale result behind
    try:
        cid = slots.parse_id(args.id)
        path = export(cid, args.path, args.output_dir)
    except (RuntimeError, ValueError, OSError) as exc:     # PackageError, SlotExportError, SlotError, StateError
        slogger.error(f'package export failed, nothing written: {exc}', source=SOURCE)
        try:
            write_result(args.output_dir, {'id': args.id, 'error': str(exc)})
        except OSError:
            pass
        return 1
    write_result(args.output_dir, {'id': _hex(cid), 'path': path})
    slogger.info(f'{_hex(cid)} exported to {path}', source=SOURCE)
    return 0


if __name__ == '__main__':
    sys.exit(main())
