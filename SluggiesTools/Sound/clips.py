"""Custom voice clips: a user's WAV file -> an RWAV the sound archive can hold (``brsar_write``).

The stock voice clips are mono DSP-ADPCM at 32,000 Hz, 0.1-2.4 s long (the vanilla archive, 2026-10-10). A
custom clip is brought to that shape once, at intake (``prepare``): any RIFF WAVE (PCM 8/16/24/32-bit, float
32/64, WAVE_FORMAT_EXTENSIBLE) is mixed down to mono, resampled to ``RATE`` (linear, as the external tool's
``voice_clips.convert`` does) and cut at ``MAX_SECONDS``. Then it is DSP-ADPCM encoded with the coefficients of
a stock voice wave (``encode``: ``brsar_write.encode_clip`` over the templates ``voice_templates`` picks from the
archive, the one that reproduces the clip best wins). The encoded RWAV is what the roster config, roster packs
and the derive round trip carry (``Roster/char_voices``): it is copied into the archive byte for byte, never
re-encoded, like the portraits' kept CMPR blocks.

``load_clip`` reads either a finished ``.rwav`` (checked: mono DSP-ADPCM) or a WAV (prepared and encoded), so
configs written by hand may name WAV files, and the GUI, which encodes at intake, names RWAVs.
"""

import os
import struct

import numpy as np

from Sound import brsar, brsar_write, dsp_adpcm, wavfile

RATE = 32000                      # the stock voice clips' sample rate
MAX_SECONDS = 2.5                 # longest stock voice clip: 2.419 s; longer clips are cut
RWAV_EXTENSION = '.rwav'
WAV_EXTENSIONS = ('.wav', '.wave')
SELECT_GROUP = 'group_sel_v'      # the archive group of the select-screen name calls: one wave per voice
TEMPLATE_COUNT = 8                # stock waves whose coefficient sets a custom clip tries


class ClipError(ValueError):
    pass


# ------------------------------------------------------------------------------------------------ WAV in

def read_wav(path_or_bytes):
    """``(rate, mono float64 samples in -1..1)`` of a RIFF WAVE file (path or bytes)."""
    rate, x, _loop = read_wav_frames(path_or_bytes)
    return rate, x.mean(axis=1)


def read_wav_frames(path_or_bytes):
    """``(rate, float64 samples in -1..1 shaped (samples, channels), loop)`` of a RIFF WAVE file (path or
    bytes). ``loop``: ``(start, end)`` (``end`` exclusive) of the first ``smpl`` loop, or None."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        b, name = bytes(path_or_bytes), 'the WAV data'
    else:
        name = os.path.basename(path_or_bytes)
        try:
            with open(path_or_bytes, 'rb') as handle:
                b = handle.read()
        except OSError as exc:
            raise ClipError(f'{name}: {exc.strerror or exc}') from exc
    if len(b) < 12 or b[:4] != b'RIFF' or b[8:12] != b'WAVE':
        raise ClipError(f'{name} is not a WAV file')
    fmt = data = smpl = None
    o = 12
    while o + 8 <= len(b):
        cid, size = b[o:o + 4], struct.unpack_from('<I', b, o + 4)[0]
        body = b[o + 8:o + 8 + size]
        if cid == b'fmt ':
            fmt = body
        elif cid == b'data':
            data = body
        elif cid == b'smpl':
            smpl = body
        o += 8 + size + (size & 1)
    if fmt is None or data is None or len(fmt) < 16:
        raise ClipError(f'{name} has no sound in it')
    tag, channels, rate = struct.unpack_from('<HHI', fmt, 0)
    bits = struct.unpack_from('<H', fmt, 14)[0]
    if tag == 0xFFFE and len(fmt) >= 26:                 # extensible: the sub-format's first two bytes
        tag = struct.unpack_from('<H', fmt, 24)[0]
    width = bits // 8
    if channels < 1 or width < 1 or rate < 1000:
        raise ClipError(f'{name} is not a sound this can read ({channels} channels, {bits}-bit, {rate} Hz)')
    n = len(data) // (width * channels)
    raw = data[:n * width * channels]
    if tag == 1 and width == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float64) - 128) / 128
    elif tag == 1 and width == 2:
        x = np.frombuffer(raw, '<i2').astype(np.float64) / 32768
    elif tag == 1 and width == 3:
        a = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float64) / (1 << 23)
    elif tag == 1 and width == 4:
        x = np.frombuffer(raw, '<i4').astype(np.float64) / (1 << 31)
    elif tag == 3 and width in (4, 8):
        x = np.frombuffer(raw, '<f4' if width == 4 else '<f8').astype(np.float64)
    else:
        raise ClipError(f'{name} is a kind of WAV this cannot read (format {tag}, {bits}-bit): save it as a '
                        'plain 16-bit WAV')
    if not n:
        raise ClipError(f'{name} has no sound in it')
    loop = None
    if smpl is not None and len(smpl) >= 36 + 24 and struct.unpack_from('<I', smpl, 28)[0]:
        start, end = struct.unpack_from('<2I', smpl, 36 + 8)
        if start < end < n:
            loop = (start, end + 1)
    return rate, x.reshape(-1, channels), loop


def prepare(rate, samples):
    """Mono float samples at ``rate`` -> ``(int16 samples at RATE, notes)``: resampled, cut at ``MAX_SECONDS``,
    peaks clipped. ``notes`` say what was done."""
    x = np.asarray(samples, dtype=np.float64)
    if not len(x):
        raise ClipError('the clip has no sound in it')
    notes = []
    seconds = len(x) / rate
    if rate != RATE:
        t = np.arange(int(round(seconds * RATE))) / RATE
        x = np.interp(t, np.arange(len(x)) / rate, x)
        notes.append(f'resampled from {rate:,} Hz to {RATE:,} Hz')
    limit = int(MAX_SECONDS * RATE)
    if len(x) > limit:
        x = x[:limit]
        notes.append(f'cut from {seconds:.2f} s to {MAX_SECONDS} s (the longest a voice clip can be)')
    peak = float(np.abs(x).max()) if len(x) else 0.0
    if peak > 1.0:
        notes.append('peaks above full scale were clipped')
    out = np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16)
    if not len(out):
        raise ClipError('the clip has no sound in it')
    return out, notes


# ------------------------------------------------------------------------------------------------ encode

def voice_templates(archive, count=TEMPLATE_COUNT):
    """Coefficient templates for custom clips: mono ADPCM waves of the archive's select-screen name-call file
    (one wave per stock voice), else of the first wave sound's file."""
    groups = archive.groups()
    group = next((g for g in groups if g.name == SELECT_GROUP), None)
    file_id = group.items[0].file_id if group is not None and group.items else None
    if file_id is None:
        first = next((s for s in archive.sounds() if s.type == brsar.WAVE), None)
        if first is None:
            raise ClipError('the sound archive holds no wave sound to take coefficients from')
        file_id = first.file_id
    parts = archive.file_bytes(file_id)
    if parts is None:
        raise ClipError(f'file {file_id} of the sound archive is not stored in it')
    out = []
    for rv in brsar_write.rwar_split(parts[1]):
        wi = brsar.u32(rv, 0x10) + 8
        if rv[wi] == brsar.ADPCM and rv[wi + 2] == 1:
            out.append(rv)
        if len(out) == count:
            break
    if not out:
        raise ClipError('the sound archive holds no mono DSP-ADPCM wave to take coefficients from')
    return out


def encode(samples, templates, rate=RATE):
    """int16 samples -> RWAV bytes (``brsar_write.encode_clip``: the best of the templates' coefficient sets)."""
    try:
        return brsar_write.encode_clip(list(templates), rate, np.asarray(samples, dtype=np.int16))
    except (brsar.BrsarError, ValueError) as exc:
        raise ClipError(str(exc)) from exc


def encode_wav(path_or_bytes, templates):
    """A WAV -> ``(RWAV bytes, notes)``."""
    rate, x = read_wav(path_or_bytes)
    samples, notes = prepare(rate, x)
    return encode(samples, templates), notes


# ------------------------------------------------------------------------------------------------ RWAV

def check_rwav(data, name='the clip'):
    """Refuse anything but a mono DSP-ADPCM RWAV (what the voice banks hold); returns the ``Wave``."""
    if not isinstance(data, (bytes, bytearray)) or data[:4] != b'RWAV':
        raise ClipError(f'{name} is not an RWAV sound')
    try:
        brsar_write.rwav_layout(data)
        wave = brsar.parse_rwav(bytes(data))
    except Exception as exc:                      # BrsarError / WriteError (either import path), struct, index
        raise ClipError(f'{name} is not a mono DSP-ADPCM RWAV: {exc}') from exc
    if wave.format != brsar.ADPCM or len(wave.channels) != 1:
        raise ClipError(f'{name} is not a mono DSP-ADPCM RWAV')
    if wave.samples <= 0:
        raise ClipError(f'{name} has no sound in it')
    return wave


def rwav_seconds(data):
    wave = brsar.parse_rwav(bytes(data))
    return wave.samples / wave.rate if wave.rate else 0.0


def rwav_wav_bytes(data):
    """An RWAV decoded to a 16-bit WAV (for playing it back)."""
    wave = brsar.parse_rwav(bytes(data))
    channels = brsar.decode_waves([wave])[0]
    return wavfile.wav_bytes(wave.rate, channels, wave.loop)


def is_rwav_name(path):
    return str(path).lower().endswith(RWAV_EXTENSION)


def load_clip(path, templates):
    """A clip file -> ``(RWAV bytes, notes)``: a ``.rwav`` as it is (checked), anything else read as a WAV and
    encoded with ``templates`` (``voice_templates``; may be a callable returning them, so the archive is read only
    when a WAV needs it)."""
    name = os.path.basename(path)
    if is_rwav_name(path):
        try:
            with open(path, 'rb') as handle:
                data = handle.read()
        except OSError as exc:
            raise ClipError(f'{name}: {exc.strerror or exc}') from exc
        check_rwav(data, name)
        return data, []
    if callable(templates):
        templates = templates()
    if not templates:
        raise ClipError(f'{name}: encoding a WAV needs the game\'s sound archive (1_Input/sound_NA/MY2.brsar)')
    rwav, notes = encode_wav(path, templates)
    return rwav, notes
