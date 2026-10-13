"""Sound effects: the archive's WAVE sounds that are not voices, and the user's replacements for them.

**Which sounds.** ``MY2.brsar`` holds 853 wave sounds (vanilla US archive, 2026-10-12). The voices are the waves
of the voice banks, groups ``group_v<NNN>`` (one per voice) and ``group_sel_v`` (the select-screen name calls); the
other 345 are the effects (``list_effects``): bat and ball sounds (``group_bb``), menus (``group_stay``), stadiums,
minigames, Challenge mode, Toy Field. All are DSP-ADPCM, 299 mono 32 kHz, 23 mono 44.1 kHz, 22 mono looped, one
stereo. The 119 sequence sounds (``bb_hit_m``, ...) play instrument banks and are not wave sounds: not listed.

**Replacing.** ``encode`` brings a user's sound file (any WAV; MP3, OGG, FLAC, ... through ffmpeg,
``Sound/clips.read_wav_frames``) to the stock wave's shape: its channel count (``brstm_write.fit_channels``), its
rate (``brstm_write.resample``), its loop (a WAV ``smpl`` loop is kept; else a looping stock wave loops from the
start). Each channel is DSP-ADPCM encoded twice, with coefficients trained on the sound (``brstm_write.train_coefs``)
and with the stock wave's, and the closer one is kept. Length is free up to ``MAX_SECONDS``; a wave's group loads
into the game's sound memory whole, so a much bigger wave is noted.

**The library.** Replacements live in ``Sound_FX/`` beside ``1_Input`` (user data, like ``Sound_Packs``): one
``<sound name>.rwav`` per replaced sound and ``effects.json``::

    {"format": 1, "effects": {"bb_swing_m": {"rwav": "bb_swing_m.rwav", "origin": "C:/.../swoosh.wav",
                                             "seconds": 0.62}}}

keyed by the stock sound's name (INFO IDs never move; names are what a reader sees). ``apply_library`` puts every
library wave into an archive (``brsar_write.replace_waves``): the roster runner calls it on every run, after the
voice steps, so "Patch Game" keeps them; ``sync_output`` writes ``3_Output_Dat/sound_NA/MY2.brsar`` right away
(the GUI's Sound FX tab), the library's waves in and the stock ones back for the sounds it no longer holds.
"""

import json
import os
import re
from dataclasses import dataclass

import numpy as np

from Sound import brsar, brsar_write, brstm_write, clips, dsp_adpcm

LIBRARY_DIR = 'Sound_FX'                  # beside 1_Input, like Sound_Packs
LIBRARY_FILE = 'effects.json'
FORMAT = 1
VOICE_GROUP_RE = re.compile(r'group_v\d+|group_sel_v')
MAX_SECONDS = 30.0                        # longest stock effect: about 2.5 s; longer sounds are cut
BIG_FACTOR = 2.0                          # a wave this many times the stock one's size is noted


class EffectError(ValueError):
    pass


@dataclass
class Effect:
    sound_id: int               # INFO ID
    name: str
    file_id: int
    group: str                  # the archive group its file loads with
    channels: int
    rate: int
    samples: int
    loop: bool
    wave_bytes: int             # the RWAV's size
    shared: int = 0             # other sounds that play the same wave

    @property
    def seconds(self):
        return self.samples / self.rate if self.rate else 0.0


def voice_files(archive):
    """File IDs of the voice banks (files of ``group_v<NNN>`` / ``group_sel_v`` groups)."""
    return {item.file_id for g in archive.groups() if VOICE_GROUP_RE.fullmatch(g.name or '') for item in g.items}


class _Waves:
    """The RWAV each wave sound plays, reading each file once."""
    def __init__(self, archive):
        self.archive = archive
        self.files, self.groups = archive.files(), archive.groups()
        self.parts = {}

    def index(self, sound):
        r, _w = self._parts(sound.file_id)
        return brsar.rwsd_wave_index(r, sound.wave_entry)

    def rwav(self, sound):
        r, w = self._parts(sound.file_id)
        return brsar.rwar_wave_bytes(w, brsar.rwsd_wave_index(r, sound.wave_entry))

    def _parts(self, file_id):
        if file_id not in self.parts:
            parts = self.archive.file_bytes(file_id, self.groups, self.files)
            if parts is None:
                raise EffectError(f'file {file_id} is not stored in the sound archive')
            self.parts[file_id] = parts
        return self.parts[file_id]


def effect_sounds(archive):
    """The archive's effect sounds: wave sounds of archived files outside the voice banks, in INFO ID order."""
    voices = voice_files(archive)
    files = archive.files()
    return [s for s in archive.sounds() if s.type == brsar.WAVE and s.file_id not in voices
            and 0 <= s.file_id < len(files) and files[s.file_id].positions and not files[s.file_id].external_path]


def list_effects(archive):
    """``[Effect]`` of the archive."""
    sounds = effect_sounds(archive)
    waves = _Waves(archive)
    group_name = {}
    for g in waves.groups:
        for item in g.items:
            group_name.setdefault(item.file_id, g.name)
    users = {}
    effect_files = {s.file_id for s in sounds}
    for s in archive.sounds():
        if s.type == brsar.WAVE and s.file_id in effect_files:
            key = (s.file_id, waves.index(s))
            users[key] = users.get(key, 0) + 1
    out = []
    for s in sounds:
        rv = waves.rwav(s)
        wave = brsar.parse_rwav(rv)
        out.append(Effect(s.index, s.name, s.file_id, group_name.get(s.file_id, ''), len(wave.channels), wave.rate,
                          wave.samples, wave.loop is not None, len(rv), users[(s.file_id, waves.index(s))] - 1))
    return out


# ------------------------------------------------------------------------------------------------ encoding

def _encode_channel(x, stock_coefs):
    """int16 samples -> ``(coefs, encoded bytes, decoded int16)``: trained coefficients or the stock wave's,
    whichever reproduces the sound better."""
    best = None
    for coefs in (brstm_write.train_coefs(x), list(stock_coefs)):
        encoded, _ = dsp_adpcm.encode(x, coefs)
        decoded = dsp_adpcm.decode([dsp_adpcm.Lane(encoded, len(x), coefs)])[0]
        error = int(((decoded.astype(np.int64) - x.astype(np.int64)) ** 2).sum())
        if best is None or error < best[0]:
            best = (error, coefs, encoded, decoded)
    return best[1:]


def encode(path, stock_rwav, gain_db=0.0):
    """A sound file -> ``(RWAV bytes, notes)`` in the shape of the stock wave ``stock_rwav`` (its bytes), its
    level changed by ``gain_db`` decibels (``clips.apply_gain``)."""
    try:
        stock = brsar.parse_rwav(bytes(stock_rwav))
    except (brsar.BrsarError, IndexError, ValueError) as exc:
        raise EffectError(f'the stock wave is not readable: {exc}') from exc
    if stock.format != brsar.ADPCM:
        raise EffectError('only DSP-ADPCM effects can be replaced (this stock wave is PCM)')
    rate, x, wav_loop = clips.read_wav_frames(path)
    x, notes = clips.apply_gain(x, gain_db)
    channels = len(stock.channels)
    x, note = brstm_write.fit_channels(x, channels)
    if note:
        notes.append(note)
    if rate != stock.rate:
        x = brstm_write.resample(x, rate, stock.rate)
        notes.append(f'resampled from {rate:,} Hz to {stock.rate:,} Hz')
    limit = int(MAX_SECONDS * stock.rate)
    if len(x) > limit:
        notes.append(f'cut from {len(x) / stock.rate:.1f} s to {MAX_SECONDS:.0f} s')
        x = x[:limit]
    if np.abs(x).max(initial=0) > 1.0:
        notes.append('peaks above full scale were clipped')
    samples = [brstm_write.to_int16(x[:, c]) for c in range(channels)]
    n = len(samples[0])
    if not n:
        raise EffectError('the sound has no samples in it')
    loop_start = None
    if wav_loop is not None:
        scale = stock.rate / rate
        start = int(wav_loop[0] * scale) // dsp_adpcm.FRAME_SAMPLES * dsp_adpcm.FRAME_SAMPLES
        end = min(n, int(round(wav_loop[1] * scale)))
        if start < end:
            loop_start, n = start, end
            notes.append(f'loops from {start / stock.rate:.2f} s (the WAV\'s loop)')
    elif stock.loop is not None:
        loop_start = 0
        notes.append('loops from the start (the stock sound loops; a WAV smpl loop sets another point)')
    lanes = []
    for c in range(channels):
        coefs, encoded, decoded = _encode_channel(samples[c][:n], stock.channels[c].coefs)
        context = (0, 0, 0)
        if loop_start is not None:
            context = (encoded[loop_start // dsp_adpcm.FRAME_SAMPLES * dsp_adpcm.FRAME_BYTES],
                       int(decoded[loop_start - 1]) if loop_start >= 1 else 0,
                       int(decoded[loop_start - 2]) if loop_start >= 2 else 0)
        lanes.append((coefs, encoded, context))
    rwav = brsar_write.rwav_from_channels(stock_rwav, stock.rate, n, lanes, loop_start)
    if len(rwav) > BIG_FACTOR * len(stock_rwav):
        notes.append(f'{len(rwav) / len(stock_rwav):.1f} times the stock wave\'s size ({len(rwav):,} bytes): its '
                     'group loads into sound memory whole; test it in the game')
    return rwav, notes


# ------------------------------------------------------------------------------------------------ the library

@dataclass
class Entry:
    name: str                   # the stock sound's name
    rwav: str                   # file name in the library folder
    origin: str | None = None   # the user's file
    seconds: float | None = None

    def to_json(self):
        out = {'rwav': self.rwav}
        if self.origin:
            out['origin'] = self.origin
        if self.seconds is not None:
            out['seconds'] = self.seconds
        return out


def library_dir(root_dir):
    return os.path.join(root_dir, LIBRARY_DIR)


def _file_name(name):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', name) + clips.RWAV_EXTENSION


def load_library(root_dir):
    """``{sound name: Entry}`` of ``Sound_FX/effects.json`` (empty without one)."""
    path = os.path.join(library_dir(root_dir), LIBRARY_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            doc = json.load(handle)
    except OSError as exc:
        raise EffectError(f'{path}: {exc.strerror or exc}') from exc
    except ValueError as exc:
        raise EffectError(f'{path}: not valid JSON: {exc}') from exc
    if not isinstance(doc, dict) or doc.get('format') != FORMAT or not isinstance(doc.get('effects', {}), dict):
        raise EffectError(f'{path}: not a sound effect library of format {FORMAT}')
    out = {}
    for name, entry in (doc.get('effects') or {}).items():
        rwav = entry.get('rwav') if isinstance(entry, dict) else None
        if not isinstance(rwav, str) or not rwav or os.path.basename(rwav) != rwav:
            raise EffectError(f'{path}: {name}: "rwav" must be a plain file name in {LIBRARY_DIR}/')
        seconds = entry.get('seconds')
        out[name] = Entry(name, rwav, entry.get('origin'), float(seconds) if isinstance(seconds, (int, float)) else None)
    return out


def save_library(root_dir, entries):
    folder = library_dir(root_dir)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, LIBRARY_FILE)
    doc = {'format': FORMAT, 'effects': {name: entries[name].to_json() for name in sorted(entries)}}
    with open(path + '.tmp', 'w', encoding='utf-8') as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=1)
    os.replace(path + '.tmp', path)


def entry_path(root_dir, entry):
    return os.path.join(library_dir(root_dir), entry.rwav)


def set_effect(root_dir, name, rwav, origin=None):
    """Store ``rwav`` as the replacement of sound ``name``; returns the ``Entry``."""
    wave = brsar.parse_rwav(bytes(rwav))
    entries = load_library(root_dir)
    entry = Entry(name, _file_name(name), origin, round(wave.samples / wave.rate, 3) if wave.rate else None)
    os.makedirs(library_dir(root_dir), exist_ok=True)
    path = entry_path(root_dir, entry)
    with open(path + '.tmp', 'wb') as handle:
        handle.write(rwav)
    os.replace(path + '.tmp', path)
    entries[name] = entry
    save_library(root_dir, entries)
    return entry


def remove_effect(root_dir, name):
    entries = load_library(root_dir)
    entry = entries.pop(name, None)
    if entry is None:
        return False
    path = entry_path(root_dir, entry)
    if os.path.isfile(path):
        os.remove(path)
    save_library(root_dir, entries)
    return True


def library_waves(archive, root_dir, entries=None):
    """``({INFO ID: RWAV bytes}, problems)``: the library's replacements for the effect sounds of ``archive``.
    An entry naming no effect sound, or whose wave is unreadable or has another channel count, is a problem."""
    entries = load_library(root_dir) if entries is None else entries
    if not entries:
        return {}, []
    by_name = {s.name: s for s in effect_sounds(archive)}
    stock = _Waves(archive)
    waves, problems = {}, []
    for name, entry in sorted(entries.items()):
        sound = by_name.get(name)
        if sound is None:
            problems.append(f'{name}: no sound effect of that name in the archive')
            continue
        try:
            with open(entry_path(root_dir, entry), 'rb') as handle:
                rv = handle.read()
            wave = brsar.parse_rwav(rv)
            want = len(brsar.parse_rwav(stock.rwav(sound)).channels)
        except (OSError, brsar.BrsarError, IndexError, ValueError) as exc:
            problems.append(f'{name}: {exc}')
            continue
        if wave.format != brsar.ADPCM or len(wave.channels) != want:
            problems.append(f'{name}: {entry.rwav} is not a {want}-channel DSP-ADPCM wave')
            continue
        waves[sound.index] = rv
    return waves, problems


def apply_library(data, root_dir):
    """``(archive bytes, log lines)``: ``data`` with every library replacement in (``data`` as it is without any)."""
    try:
        archive = brsar.Brsar(data)
        waves, problems = library_waves(archive, root_dir)
    except EffectError as exc:
        return data, [f'sound effects not applied: {exc}']
    lines = [f'sound effect skipped: {p}' for p in problems]
    if not waves:
        return data, lines
    out = brsar_write.replace_waves(data, waves)
    names = archive.sounds()
    lines.append(f'sound effects: {len(waves)} replaced from {LIBRARY_DIR}/ '
                 f'({", ".join(names[sid].name for sid in sorted(waves))})')
    return out, lines


def sync_output(root_dir):
    """Bring ``3_Output_Dat/sound_NA/MY2.brsar`` (made from ``1_Input``'s when missing) in line with the library:
    each effect sound plays its library wave, or the stock wave when the library has none. Returns
    ``(sounds changed, problems)``."""
    input_path, output_path = brsar.archive_paths(root_dir)
    with open(input_path, 'rb') as handle:
        stock_bytes = handle.read()
    if os.path.isfile(output_path):
        with open(output_path, 'rb') as handle:
            base = handle.read()
    else:
        base = stock_bytes
    stock = brsar.Brsar(stock_bytes)
    current = brsar.Brsar(base)
    replaced, problems = library_waves(stock, root_dir)
    stock_waves, current_waves = _Waves(stock), _Waves(current)
    sounds = current.sounds()
    waves = {}
    for s in effect_sounds(stock):
        if s.index >= len(sounds) or sounds[s.index].name != s.name:
            problems.append(f'{s.name}: the output archive does not hold it under ID {s.index}')
            continue
        want = replaced.get(s.index) or stock_waves.rwav(s)
        if current_waves.rwav(sounds[s.index]) != want:
            waves[s.index] = want
    if waves or not os.path.isfile(output_path):
        out = brsar_write.replace_waves(base, waves)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path + '.tmp', 'wb') as handle:
            handle.write(out)
        os.replace(output_path + '.tmp', output_path)
    return len(waves), problems
