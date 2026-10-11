"""Voice packs: the 12 voice moments of one voice, stock (a species' clips in the archive) or custom (a folder
of the user's clips).

A **stock pack** is a stock character's voice: the species' clip row in the DOL (``Roster/voices.py``, 12 sound
INFO IDs, ``MOMENTS`` order) and those sounds in ``MY2.brsar``. ``stock_packs`` lists them from the input files.

A **custom pack** is a folder ``Sound_Packs/<name>/`` holding ``pack.json`` and one ``<moment>.rwav`` (the
encoded clip, ``Sound/clips.py``; what the game gets) plus ``<moment>.wav`` (the prepared clip, for playing it
back) per moment the user filled::

    {"format": 1, "name": "Rosalina", "voice": "0x0B",
     "clips": {"v1": {"rwav": "v1.rwav", "wav": "v1.wav", "origin": "C:/.../cheer.wav", "seconds": 0.8}}}

``voice`` is the stock voice the pack is based on (the moments it does not fill keep that voice's clips; None:
the slot's own species' voice). A pack travels as one ``.sluggiesvoice`` file (the folder zipped). The character
grid assigns a pack to a slot as its own voice (``Roster/char_voices``: ``{"id", "voice", "clips", "pack"}``).
"""

import io
import json
import os
import posixpath
import re
import shutil
import struct
import zipfile
from dataclasses import dataclass, field

from Sound import brsar, clips

# the 12 voice moments of a clip row, in table order (slot = index); roles per the external tool's voice_clips.MOMENTS
MOMENTS = (('v1', 'Cheer'), ('v3', 'Charge'), ('v4', 'Talking line'),
           ('v5', 'Cheer 2'), ('v6', 'Getting hit'), ('v7', 'Cheer 3'),
           ('v8', 'Stepping up to bat'), ('v10', 'Stagger'), ('v11', 'Hurt'),
           ('v12', 'Falling / Failure'), ('v13', 'Character select'), ('v14', 'Throw'))
MOMENT_KEYS = tuple(k for k, _role in MOMENTS)
SLOT_OF = {k: i for i, (k, _role) in enumerate(MOMENTS)}
ROLE_OF = dict(MOMENTS)
NAME_CALL = 'v13'

PACK_DIR = 'Sound_Packs'                  # beside 1_Input, like Roster_Packs
PACK_FILE = 'pack.json'
PACK_EXTENSION = '.sluggiesvoice'
FORMAT = 1
NAME_RE = re.compile(r'^[^<>:"/\\|?*\x00-\x1f]{1,48}$')


class PackError(ValueError):
    pass


def moment_label(key):
    return f'{key}: {ROLE_OF[key]}'


def check_name(name):
    """Why ``name`` cannot name a pack (a folder name), or None."""
    if not isinstance(name, str) or not name.strip():
        return 'a pack needs a name'
    if name != name.strip() or name.endswith('.'):
        return 'a pack name cannot start or end with a space or a dot'
    if not NAME_RE.match(name):
        return 'a pack name is at most 48 characters without < > : " / \\ | ? *'
    if name.upper().split('.')[0] in ('CON', 'PRN', 'AUX', 'NUL') or re.match(r'^(COM|LPT)\d$', name.upper()):
        return f'{name!r} is a reserved file name'
    return None


# ------------------------------------------------------------------------------------------------ custom packs

@dataclass
class Clip:
    moment: str
    rwav: str                      # file name in the pack folder
    wav: str | None = None         # the prepared WAV beside it (playback), or None
    origin: str | None = None      # the user's file
    seconds: float | None = None

    def to_json(self):
        out = {'rwav': self.rwav}
        if self.wav:
            out['wav'] = self.wav
        if self.origin:
            out['origin'] = self.origin
        if self.seconds is not None:
            out['seconds'] = self.seconds
        return out


@dataclass
class CustomPack:
    name: str
    folder: str
    voice: int | None = None       # the stock voice the pack is based on (a base character), or None
    clips: dict = field(default_factory=dict)   # {moment: Clip}

    @property
    def count(self):
        return len(self.clips)

    def rwav_path(self, moment):
        return os.path.join(self.folder, self.clips[moment].rwav)

    def wav_path(self, moment):
        clip = self.clips[moment]
        return os.path.join(self.folder, clip.wav) if clip.wav else None

    def rwav_bytes(self, moment):
        with open(self.rwav_path(moment), 'rb') as handle:
            return handle.read()

    def to_json(self):
        return {'format': FORMAT, 'name': self.name, 'voice': None if self.voice is None else f'0x{self.voice:02X}',
                'clips': {m: self.clips[m].to_json() for m in MOMENT_KEYS if m in self.clips}}


def packs_dir(root_dir):
    return os.path.join(root_dir, PACK_DIR)


def pack_folder(root_dir, name):
    return os.path.join(packs_dir(root_dir), name)


def _voice_number(value, where):
    if value is None:
        return None
    try:
        number = int(value, 16) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise PackError(f'{where}: "voice" {value!r} is not a character ID') from exc
    if not 0 <= number <= 0xFF:
        raise PackError(f'{where}: "voice" {value!r} is not a character ID')
    return number


def _parse(doc, folder, name_hint):
    where = os.path.join(os.path.basename(folder), PACK_FILE)
    if not isinstance(doc, dict) or doc.get('format') != FORMAT:
        raise PackError(f'{where}: not a voice pack of format {FORMAT}')
    name = doc.get('name') if isinstance(doc.get('name'), str) and doc.get('name').strip() else name_hint
    pack = CustomPack(name, folder, _voice_number(doc.get('voice'), where))
    entries = doc.get('clips') or {}
    if not isinstance(entries, dict):
        raise PackError(f'{where}: "clips" must be an object')
    for moment, entry in entries.items():
        if moment not in SLOT_OF:
            raise PackError(f'{where}: {moment!r} is not a voice moment ({", ".join(MOMENT_KEYS)})')
        if not isinstance(entry, dict) or not isinstance(entry.get('rwav'), str):
            raise PackError(f'{where}: clip {moment} needs an "rwav" file name')
        for key in ('rwav', 'wav'):
            value = entry.get(key)
            if value is not None and (not isinstance(value, str) or os.path.basename(value) != value or not value):
                raise PackError(f'{where}: clip {moment}: {key!r} must be a plain file name in the pack folder')
        seconds = entry.get('seconds')
        pack.clips[moment] = Clip(moment, entry['rwav'], entry.get('wav'), entry.get('origin'),
                                  float(seconds) if isinstance(seconds, (int, float)) else None)
    return pack


def load_pack(folder):
    """The pack in ``folder`` (its ``pack.json``; every named clip file must exist and be a mono ADPCM RWAV)."""
    path = os.path.join(folder, PACK_FILE)
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            doc = json.load(handle)
    except OSError as exc:
        raise PackError(f'{path}: {exc.strerror or exc}') from exc
    except ValueError as exc:
        raise PackError(f'{path}: not valid JSON: {exc}') from exc
    pack = _parse(doc, folder, os.path.basename(os.path.normpath(folder)))
    for moment, clip in pack.clips.items():
        try:
            data = pack.rwav_bytes(moment)
        except OSError as exc:
            raise PackError(f'{pack.name}: clip {moment}: {exc.strerror or exc}') from exc
        clips.check_rwav(data, f'{pack.name} {clip.rwav}')
    return pack


def save_pack(pack):
    os.makedirs(pack.folder, exist_ok=True)
    path = os.path.join(pack.folder, PACK_FILE)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(pack.to_json(), handle, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def list_packs(root_dir):
    """``(packs, problems)``: every readable pack folder under ``Sound_Packs`` by name, and why the others are not."""
    folder = packs_dir(root_dir)
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return [], []
    packs, problems = [], []
    for name in names:
        sub = os.path.join(folder, name)
        if not os.path.isdir(sub) or not os.path.isfile(os.path.join(sub, PACK_FILE)):
            continue
        try:
            packs.append(load_pack(sub))
        except (PackError, clips.ClipError) as exc:
            problems.append(f'{name}: {exc}')
    return packs, problems


def find_pack(root_dir, name):
    """The custom pack named ``name``, or None when there is none."""
    folder = pack_folder(root_dir, name)
    if check_name(name) is not None or not os.path.isfile(os.path.join(folder, PACK_FILE)):
        return None
    return load_pack(folder)


def create_pack(root_dir, name, voice=None):
    problem = check_name(name)
    if problem:
        raise PackError(problem)
    folder = pack_folder(root_dir, name)
    if os.path.exists(folder):
        raise PackError(f'a pack named {name!r} exists already')
    pack = CustomPack(name, folder, voice)
    save_pack(pack)
    return pack


def set_clip(pack, moment, rwav, wav=None, origin=None, seconds=None):
    """Put a clip into the pack folder (``rwav`` bytes, ``wav`` bytes or None) and save ``pack.json``."""
    if moment not in SLOT_OF:
        raise PackError(f'{moment!r} is not a voice moment')
    clips.check_rwav(rwav, f'{moment} of {pack.name}')
    os.makedirs(pack.folder, exist_ok=True)
    rwav_name, wav_name = moment + clips.RWAV_EXTENSION, moment + '.wav'
    with open(os.path.join(pack.folder, rwav_name), 'wb') as handle:
        handle.write(rwav)
    if wav is not None:
        with open(os.path.join(pack.folder, wav_name), 'wb') as handle:
            handle.write(wav)
    elif os.path.isfile(os.path.join(pack.folder, wav_name)):
        os.remove(os.path.join(pack.folder, wav_name))
    if seconds is None:
        seconds = round(clips.rwav_seconds(rwav), 3)
    pack.clips[moment] = Clip(moment, rwav_name, wav_name if wav is not None else None, origin, seconds)
    save_pack(pack)
    return pack.clips[moment]


def remove_clip(pack, moment):
    clip = pack.clips.pop(moment, None)
    if clip is None:
        return False
    for name in (clip.rwav, clip.wav):
        if name and os.path.isfile(os.path.join(pack.folder, name)):
            os.remove(os.path.join(pack.folder, name))
    save_pack(pack)
    return True


def set_voice(pack, voice):
    pack.voice = voice
    save_pack(pack)


def rename_pack(root_dir, pack, new_name):
    problem = check_name(new_name)
    if problem:
        raise PackError(problem)
    target = pack_folder(root_dir, new_name)
    if os.path.normcase(os.path.abspath(target)) != os.path.normcase(os.path.abspath(pack.folder)) and os.path.exists(target):
        raise PackError(f'a pack named {new_name!r} exists already')
    os.rename(pack.folder, target)
    pack.folder, pack.name = target, new_name
    save_pack(pack)
    return pack


def delete_pack(pack):
    shutil.rmtree(pack.folder)


# ------------------------------------------------------------------------------------------------ one file

def export_pack(pack, path):
    """The pack folder as one ``.sluggiesvoice`` zip."""
    if not path.lower().endswith(PACK_EXTENSION):
        path += PACK_EXTENSION
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(PACK_FILE, json.dumps(pack.to_json(), ensure_ascii=False, indent=1))
        for moment in MOMENT_KEYS:
            clip = pack.clips.get(moment)
            if clip is None:
                continue
            for name in (clip.rwav, clip.wav):
                if name and os.path.isfile(os.path.join(pack.folder, name)):
                    zf.write(os.path.join(pack.folder, name), name)
    os.replace(tmp, path)
    return path


def read_pack_file(path):
    """``(pack, {file name: bytes})`` of a ``.sluggiesvoice`` zip, checked but not stored."""
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackError(f'{os.path.basename(path)} is not a voice pack: {exc}') from exc
    with zf:
        names = zf.namelist()
        if PACK_FILE not in names:
            raise PackError(f'{os.path.basename(path)} is not a voice pack ({PACK_FILE} missing)')
        try:
            doc = json.loads(zf.read(PACK_FILE).decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as exc:
            raise PackError(f'{PACK_FILE} in the pack is not valid JSON: {exc}') from exc
        files = {}
        for name in names:
            if name == PACK_FILE or name.endswith('/'):
                continue
            if posixpath.dirname(name) or posixpath.basename(name) != name:
                raise PackError(f'unexpected file in the pack: {name}')
            files[name] = zf.read(name)
    stem = os.path.basename(path)
    stem = stem[:-len(PACK_EXTENSION)] if stem.lower().endswith(PACK_EXTENSION) else os.path.splitext(stem)[0]
    pack = _parse(doc, stem, stem)
    for moment, clip in pack.clips.items():
        if clip.rwav not in files:
            raise PackError(f'{pack.name}: clip {moment} ({clip.rwav}) is missing from the pack')
        clips.check_rwav(files[clip.rwav], f'{pack.name} {clip.rwav}')
        if clip.wav and clip.wav not in files:
            clip.wav = None
    return pack, files


def import_pack(root_dir, path, name=None):
    """Store a ``.sluggiesvoice`` file as a pack folder (``name``: the folder name; default the pack's name)."""
    pack, files = read_pack_file(path)
    name = name or pack.name
    problem = check_name(name)
    if problem:
        raise PackError(problem)
    folder = pack_folder(root_dir, name)
    if os.path.exists(folder):
        raise PackError(f'a pack named {name!r} exists already')
    os.makedirs(folder)
    for clip in pack.clips.values():
        for file_name in (clip.rwav, clip.wav):
            if file_name and file_name in files:
                with open(os.path.join(folder, file_name), 'wb') as handle:
                    handle.write(files[file_name])
    pack.folder, pack.name = folder, name
    save_pack(pack)
    return pack


# ------------------------------------------------------------------------------------------------ stock packs

@dataclass
class StockClip:
    moment: str
    sound_id: int                  # INFO ID
    name: str
    file_id: int
    seconds: float
    wave_bytes: int


@dataclass
class StockPack:
    species: int
    head: int                      # the base character (the pack's name in the GUI)
    clips: list                    # [StockClip], MOMENTS order
    family_file: int | None
    family_wave_bytes: int

    @property
    def prefix(self):
        """The stock sound names' prefix (``mario`` of ``mario_v1``)."""
        name = self.clips[0].name if self.clips else ''
        return name.rsplit('_v', 1)[0] if '_v' in name else name


def stock_packs(image, archive):
    """Every stock voice as the input files hold it: ``[StockPack]`` by species (``Roster/voices`` clip rows;
    the archive's sounds)."""
    try:
        from Roster import voices
    except ImportError:
        from SluggiesTools.Roster import voices
    sounds = archive.sounds()
    files, groups = archive.files(), archive.groups()
    waves = {}

    def wave_of(sound):
        if sound.index not in waves:
            parts = archive.file_bytes(sound.file_id, groups, files)
            rv = brsar.rwar_wave_bytes(parts[1], brsar.rwsd_wave_index(parts[0], sound.wave_entry))
            wave = brsar.parse_rwav(rv)
            waves[sound.index] = (wave.samples / wave.rate if wave.rate else 0.0, len(rv))
        return waves[sound.index]

    heads = image.read(voices.HEAD_LIST, voices.SPECIES)
    out = []
    for s in range(voices.SPECIES):
        row = struct.unpack('>12I', voices.clip_row(image, s))
        entries = []
        for k, (moment, _role) in enumerate(MOMENTS):
            sid = row[k]
            if 0 <= sid < len(sounds) and sounds[sid].type == brsar.WAVE:
                seconds, size = wave_of(sounds[sid])
                entries.append(StockClip(moment, sid, sounds[sid].name, sounds[sid].file_id, round(seconds, 3), size))
            else:
                entries.append(StockClip(moment, sid, f'sound {sid}', -1, 0.0, 0))
        family = entries[0].file_id if entries and entries[0].file_id >= 0 else None
        parts = archive.file_bytes(family, groups, files) if family is not None else None
        out.append(StockPack(s, heads[s], entries, family, len(parts[1]) if parts else 0))
    return out


def stock_clip_wav(archive, sound_id):
    """A stock wave sound decoded to WAV bytes (playback)."""
    sound =archive.sounds()[sound_id]
    parts = archive.file_bytes(sound.file_id)
    rv = brsar.rwar_wave_bytes(parts[1], brsar.rwsd_wave_index(parts[0], sound.wave_entry))
    return clips.rwav_wav_bytes(rv)


def pack_wave_bytes(pack):
    """The wave bytes a custom pack adds to a family file when every one of its clips is copied in."""
    total = 0
    for moment in pack.clips:
        try:
            total += os.path.getsize(pack.rwav_path(moment))
        except OSError:
            pass
    return total
