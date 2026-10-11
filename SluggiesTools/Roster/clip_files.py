"""Custom voice clips in a roster config: how ``character_voices[].clips`` and ``species_clips`` name their files,
and how the steps read them.

A clip entry maps voice moments (``Sound/sound_packs.MOMENT_KEYS``: ``v1 v3 ... v14``) to clip files::

    "clips": {"v1": "3f2a9c...e1.rwav", "v13": "C:/sounds/name_call.wav"}

A relative name is looked up in the run's clip folder (``RosterContext.clip_dir``: ``clips/`` beside a derived
config, the preset's own folder for a hand-written one); an absolute path is taken as it is. An ``.rwav`` is the
encoded clip, copied into the archive byte for byte; any other file is read as a WAV and encoded with the
archive's coefficients (``Sound/clips.load_clip``). Derived configs, roster packs and the planner name clips by
content: ``<sha1 of the RWAV, 16 hex digits>.rwav`` (``file_name``), so equal clips share one file.
"""

import hashlib
import os

try:
    from ..Sound import brsar, clips, sound_packs
    from . import ids
except ImportError:
    from Sound import brsar, clips, sound_packs
    import ids

CLIP_DIR = 'clips'                            # beside a derived roster.json (derive.write), like icons/
SPECIES_KEY = 'species_clips'                 # [{"id": base character, "clips": {...}}]: a whole species' clips


class ClipConfigError(ValueError):
    pass


def file_name(rwav: bytes) -> str:
    return hashlib.sha1(rwav).hexdigest()[:16] + clips.RWAV_EXTENSION


def parse_clips(value, where: str) -> dict[int, str]:
    """A config's ``clips`` object -> ``{slot: file}`` (slot = the moment's index in the clip row)."""
    if not isinstance(value, dict) or not value:
        raise ClipConfigError(f'{where}.clips: expected an object {{"v1": "file", ...}} with at least one moment')
    out = {}
    for moment, name in value.items():
        if moment not in sound_packs.SLOT_OF:
            raise ClipConfigError(f'{where}.clips: {moment!r} is not a voice moment '
                                  f'({", ".join(sound_packs.MOMENT_KEYS)})')
        if not isinstance(name, str) or not name.strip():
            raise ClipConfigError(f'{where}.clips.{moment}: expected a clip file name')
        out[sound_packs.SLOT_OF[moment]] = name
    return out


def parse_species_clips(config: dict, heads: bytes) -> dict[int, dict[int, str]]:
    """``species_clips`` -> ``{species: {slot: file}}``."""
    entries = config.get(SPECIES_KEY) or []
    if not isinstance(entries, list):
        raise ClipConfigError(f'"{SPECIES_KEY}" must be a list of {{"id", "clips"}} entries')
    species_of = {heads[s]: s for s in range(len(heads))}
    names = ', '.join(f'0x{c:02X}' for c in sorted(species_of))
    out: dict[int, dict[int, str]] = {}
    for n, entry in enumerate(entries):
        where = f'{SPECIES_KEY}[{n}]'
        if not isinstance(entry, dict) or entry.get('id') is None:
            raise ClipConfigError(f'{where}: expected {{"id": "0xNN", "clips": {{...}}}}')
        cid = ids._number(entry['id'], f'{where}.id')
        if cid not in species_of:
            raise ClipConfigError(f'{where}.id: 0x{cid:02X} is not the base character of a stock square (one of {names})')
        if species_of[cid] in out:
            raise ClipConfigError(f'{where}: 0x{cid:02X} is listed twice')
        out[species_of[cid]] = parse_clips(entry.get('clips'), where)
    return out


class ClipLoader:
    """Reads a run's clip files once each (``load(file) -> RWAV bytes``), WAVs encoded with the archive's
    coefficients (``sound``: the archive bytes, read only when a WAV needs them)."""

    def __init__(self, clip_dir: str | None, sound: bytes | None):
        self.clip_dir, self.sound = clip_dir, sound
        self._cache: dict[str, bytes] = {}
        self._templates = None
        self.notes: list[str] = []

    def path(self, name: str) -> str:
        if os.path.isabs(name) or self.clip_dir is None:
            return name
        return os.path.join(self.clip_dir, name)

    def templates(self):
        if self._templates is None:
            if self.sound is None:
                raise ClipConfigError('encoding a WAV clip needs the game\'s sound archive: copy files/sound_NA/MY2.brsar '
                                      'into 1_Input/sound_NA first')
            self._templates = clips.voice_templates(brsar.Brsar(self.sound))
        return self._templates

    def load(self, name: str, where: str) -> bytes:
        path = self.path(name)
        key = os.path.normcase(os.path.abspath(path))
        if key not in self._cache:
            if not os.path.isfile(path):
                raise ClipConfigError(f'{where}: clip file {path} not found')
            try:
                data, notes = clips.load_clip(path, self.templates)
            except clips.ClipError as exc:
                raise ClipConfigError(f'{where}: {exc}') from exc
            self.notes += [f'{os.path.basename(path)}: {note}' for note in notes]
            self._cache[key] = data
        return self._cache[key]


def sound_name(rwav: bytes, slot: int, file_id: int, prefix: str = 'zzc') -> str:
    """The archive name of a custom clip: ``zzc<content hash>_s<slot>_f<file>`` (one sound per file, slot and
    content, however many characters share it; ``zzs`` for a species clip, which another step adds)."""
    return f'{prefix}{hashlib.sha1(rwav).hexdigest()[:12]}_s{slot}_f{file_id}'
