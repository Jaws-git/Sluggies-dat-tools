"""Music replacement: a user's WAV (or MP3, OGG, ... through ffmpeg, ``Sound/clips``) -> a ``.brstm`` in the
layout of the stock stream it replaces.

The stock streams (``sound_NA/stream``, 2026-10-11) are all stereo DSP-ADPCM, 32,000 or 44,100 Hz, in blocks of
8,192 bytes (14,336 samples) per channel. A replacement keeps the stock file's channel count, rate and block
layout (``build`` copies its HEAD chunk and patches the counts, the loop and the per-channel ADPCM info), so the
sound archive's STRM entry needs no change: only the ``.brstm`` file is written.

Encoding is built for tracks of minutes, not the voice clips' 2.5 s (``dsp_adpcm.encode`` takes about a minute
per minute of stereo audio):

* **Coefficients** are trained per channel (``train_coefs``): 8 predictor pairs clustered on the 14-sample
  frames' prediction error (Lloyd iterations over the frames' correlation sums).
* **Blocks side by side.** A stream's blocks each carry their decoder history in the ADPC chunk, so every block
  of every channel is one lane of a batch (``encode_blocks``), all encoded in one pass over the 1,024 frames of a
  block, each frame trying the 8 predictors at 3 scales around the one its residual needs (24 candidates, not
  128). A lane starts from the history the source samples suggest; ``encode_channel`` then re-encodes the lanes
  whose real start (the decoded end of the block before) differs, frame by frame only until the encoder falls
  back into its first-pass track, until every block starts where the one before it ends: the stream also plays
  through without a seam when the player decodes blocks back to back.
"""

import struct

import numpy as np

from Sound import brstm, dsp_adpcm

FRAME = dsp_adpcm.FRAME_SAMPLES
SCALE_STEPS = np.array([-1, 0, 1], dtype=np.int64)  # scales tried around the residual's own
RESYNC_FRAMES = 64                                  # frames re-encoded before checking for the first-pass track
COEF_LIMIT = 4.0                                    # trained predictor taps stay within +-COEF_LIMIT (int32 math)
TRAIN_FRAMES = 60000                                # frames the training sees at most (evenly spread)


class MusicError(ValueError):
    pass


# ------------------------------------------------------------------------------------------------ input

def resample(x, rate, target):
    """``x`` (samples, channels) float at ``rate`` -> at ``target``: FFT band-limited (the spectrum cut or
    zero-extended), zero-padded at both ends against wrap-around."""
    if rate == target:
        return x
    pad = 4096
    padded = np.concatenate([np.zeros((pad, x.shape[1])), x, np.zeros((pad, x.shape[1]))])
    n = len(padded)
    m = int(round(n * target / rate))
    spectrum = np.fft.rfft(padded, axis=0)
    keep = min(len(spectrum), m // 2 + 1)
    out_spectrum = np.zeros((m // 2 + 1, x.shape[1]), dtype=complex)
    out_spectrum[:keep] = spectrum[:keep]
    out = np.fft.irfft(out_spectrum, n=m, axis=0) * (m / n)
    start = int(round(pad * target / rate))
    return out[start:start + int(round(len(x) * target / rate))]


def fit_channels(x, channels):
    """``x`` (samples, c) -> (samples, ``channels``): mono duplicated, other counts mixed down and duplicated
    (a stereo source into a stereo stream stays as it is)."""
    if x.shape[1] == channels:
        return x, None
    mono = x.mean(axis=1, keepdims=True)
    note = (f'{x.shape[1]} channels mixed down' if x.shape[1] > 1 else 'mono') + \
        (f' and copied to {channels} channels' if channels > 1 else '')
    return np.repeat(mono, channels, axis=1), note


def to_int16(x):
    return np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16)


# ------------------------------------------------------------------------------------------------ coefficients

def train_coefs(samples, count=8, rounds=20):
    """16 coefficients (8 pairs, 1/2048 units) for one channel of int16 samples.

    Lloyd clustering in prediction-error terms: every 14-sample frame keeps its correlation sums; a frame
    belongs to the pair that predicts it with the least squared error, and a pair is the least-squares
    predictor of its frames together. Seeded from the frames' own best predictors (energy-weighted, k-means++)."""
    x = np.asarray(samples, dtype=np.float64) / 32768
    frames = len(x) // FRAME
    if frames < count:
        return [0] * 16
    f = x[:frames * FRAME].reshape(frames, FRAME)
    p1 = np.concatenate([[0.0], x[:frames * FRAME - 1]]).reshape(frames, FRAME)
    p2 = np.concatenate([[0.0, 0.0], x[:frames * FRAME - 2]]).reshape(frames, FRAME)
    stats = np.stack([(f * f).sum(1), (p1 * p1).sum(1), (p2 * p2).sum(1), (p1 * p2).sum(1),
                      (p1 * f).sum(1), (p2 * f).sum(1)], 1)                      # r00 r11 r22 r12 b1 b2
    stats = stats[stats[:, 0] > 1e-9]
    if len(stats) > TRAIN_FRAMES:
        stats = stats[np.linspace(0, len(stats) - 1, TRAIN_FRAMES).astype(int)]
    if len(stats) < count:
        return [0] * 16
    own = np.clip(_solve(stats), -COEF_LIMIT, COEF_LIMIT)
    centers = _seed(own, np.sqrt(stats[:, 0]), count)
    for _ in range(rounds):
        label = np.argmin(_errors(stats, centers), axis=1)
        moved = False
        for k in range(count):
            mask = label == k
            if mask.any():
                c = np.clip(_solve(stats[mask].sum(0, keepdims=True))[0], -COEF_LIMIT, COEF_LIMIT)
                moved |= not np.allclose(c, centers[k])
                centers[k] = c
        if not moved:
            break
    coefs = np.clip(np.round(centers * 2048), -COEF_LIMIT * 2048, COEF_LIMIT * 2048 - 1).astype(int)
    return [int(v) for pair in coefs for v in pair]


def _solve(stats):
    """Least-squares 2-tap predictors (n, 2) of correlation sums (n, 6); a slight ridge keeps flat frames sane."""
    r11, r22, r12, b1, b2 = (stats[:, i] for i in range(1, 6))
    ridge = 1e-6 * (r11 + r22) + 1e-12
    r11, r22 = r11 + ridge, r22 + ridge
    det = r11 * r22 - r12 * r12
    return np.stack([(b1 * r22 - b2 * r12) / det, (b2 * r11 - b1 * r12) / det], 1)


def _errors(stats, centers):
    """Squared prediction error of every frame (n) under every pair (k): (n, k)."""
    a1, a2 = centers[:, 0][None], centers[:, 1][None]
    r00, r11, r22, r12, b1, b2 = (stats[:, i][:, None] for i in range(6))
    return r00 - 2 * (a1 * b1 + a2 * b2) + a1 * a1 * r11 + 2 * a1 * a2 * r12 + a2 * a2 * r22


def _seed(points, weights, count):
    rng = np.random.default_rng(0)
    centers = [points[int(np.argmax(weights))]]
    for _ in range(count - 1):                                 # k-means++ (deterministic)
        d = np.min([((points - c) ** 2).sum(1) for c in centers], axis=0) * weights
        total = d.sum()
        centers.append(points[rng.choice(len(points), p=d / total)] if total > 0 else points[0])
    return np.array(centers, dtype=np.float64)


# ------------------------------------------------------------------------------------------------ encoding

def encode_blocks(targets, coefs, h1, h2, frames=None, yield_every=16):
    """Closed-loop DSP-ADPCM encoding of many lanes at once.

    ``targets`` int (lanes, n) with n a multiple of 14; ``coefs`` (lanes, 16); ``h1``/``h2`` (lanes,) start
    histories. Encodes the first ``frames`` frames (all when None). Returns ``(bytes (lanes, frames * 8) uint8,
    states (lanes, frames, 2) int32: (h1, h2) after each frame)``."""
    import time
    lanes, n = targets.shape
    frames = n // FRAME if frames is None else frames
    coefs = np.asarray(coefs, dtype=np.int64)
    # the 24 candidates of a lane: predictor p at scale (own + step)
    preds = np.repeat(np.arange(8), len(SCALE_STEPS))
    c1 = np.ascontiguousarray(coefs[:, 2 * preds]).astype(np.int32)              # (lanes, 24)
    c2 = np.ascontiguousarray(coefs[:, 2 * preds + 1]).astype(np.int32)
    steps = np.tile(SCALE_STEPS, 8)
    t = np.asarray(targets, dtype=np.int32)
    out = np.zeros((lanes, frames * 8), np.uint8)
    states = np.zeros((lanes, frames, 2), np.int32)
    a1 = np.repeat(np.asarray(h1, np.int32)[:, None], len(preds), 1)
    a2 = np.repeat(np.asarray(h2, np.int32)[:, None], len(preds), 1)
    rows = np.arange(lanes)
    cf1 = coefs[:, 0::2].astype(np.float64)[:, :, None]                          # (lanes, 8, 1)
    cf2 = coefs[:, 1::2].astype(np.float64)[:, :, None]
    nib = np.empty((FRAME, lanes, len(preds)), np.int32)
    for f in range(frames):
        if f % yield_every == 0:
            time.sleep(0)                    # let a GUI's render thread run
        seg = t[:, f * FRAME:(f + 1) * FRAME]
        # each predictor's open-loop residual (from the true history) -> the scale it needs
        hist = np.concatenate([a2[:, :1], a1[:, :1], seg], axis=1).astype(np.float64)
        resid = np.abs(hist[None, :, 2:].transpose(1, 0, 2) - (cf1 * hist[:, None, 1:-1] + cf2 * hist[:, None, :-2])
                       / 2048).max(axis=2)                                        # (lanes, 8)
        need = np.ceil(np.log2(np.maximum(resid, 1.0) / 7.0))
        exps = np.clip(need.astype(np.int64)[:, preds] + steps, 0, 15)
        scale = (1 << exps).astype(np.int32)
        inv = 1.0 / scale
        err = np.zeros((lanes, len(preds)), np.int64)
        for i in range(FRAME):
            target = seg[:, i:i + 1]
            base = c1 * a1 + c2 * a2
            q = np.clip(np.rint((target - ((base + 1024) >> 11)) * inv), -8, 7).astype(np.int32)
            v = np.clip(((q * scale) << 11) + 1024 + base >> 11, -32768, 32767)
            d = (target - v).astype(np.int64)
            err += d * d
            nib[i] = q
            a2, a1 = a1, v
        best = np.argmin(err, axis=1)
        header = (preds[best] << 4) | exps[rows, best]
        chosen = nib[:, rows, best].T & 0xF                                       # (lanes, 14)
        block = out[:, f * 8:(f + 1) * 8]
        block[:, 0] = header
        block[:, 1:] = (chosen[:, 0::2] << 4) | chosen[:, 1::2]
        b1, b2 = a1[rows, best], a2[rows, best]
        states[:, f, 0], states[:, f, 1] = b1, b2
        a1 = np.repeat(b1[:, None], len(preds), 1)
        a2 = np.repeat(b2[:, None], len(preds), 1)
    return out, states


def encode_channel(samples, coefs, block_samples):
    """One channel's int16 samples -> ``(data per block [bytes], start history per block [(h1, h2)])``,
    seamless across block boundaries (see the module doc)."""
    x = np.asarray(samples, dtype=np.int32)
    blocks = max(1, -(-len(x) // block_samples))
    targets = np.zeros((blocks, block_samples), np.int32)
    targets.reshape(-1)[:len(x)] = x
    flat = targets.reshape(-1)
    starts = np.arange(blocks) * block_samples
    h1 = np.where(starts >= 1, flat[np.maximum(starts - 1, 0)], 0).astype(np.int32)
    h2 = np.where(starts >= 2, flat[np.maximum(starts - 2, 0)], 0).astype(np.int32)
    lane_coefs = np.repeat(np.asarray(coefs, np.int64)[None], blocks, 0)
    data, states = encode_blocks(targets, lane_coefs, h1, h2)
    for _ in range(blocks):
        true1, true2 = np.zeros(blocks, np.int32), np.zeros(blocks, np.int32)
        true1[1:], true2[1:] = states[:-1, -1, 0], states[:-1, -1, 1]
        redo = np.nonzero((true1 != h1) | (true2 != h2))[0]
        if not len(redo):
            break
        h1[redo], h2[redo] = true1[redo], true2[redo]
        head = min(RESYNC_FRAMES, data.shape[1] // 8)
        part, part_states = encode_blocks(targets[redo], lane_coefs[redo], h1[redo], h2[redo], frames=head)
        joined = (part_states[:, -1] == states[redo, head - 1]).all(axis=1)
        data[redo[joined], :head * 8] = part[joined]
        states[redo[joined], :head] = part_states[joined]
        full = redo[~joined]
        if len(full):
            data[full], states[full] = encode_blocks(targets[full], lane_coefs[full], h1[full], h2[full])
    return data, list(zip(h1.tolist(), h2.tolist()))


# ------------------------------------------------------------------------------------------------ writing

def _align(n, a=0x20):
    return (n + a - 1) // a * a


def build(original, channels, loop):
    """A ``.brstm`` with the stock file ``original``'s HEAD layout (channel count, rate, block size) holding
    ``channels`` (one int16 array per channel, at the original's rate). ``loop``: ``(start, end)`` or None."""
    info = brstm.info(original)
    if info.codec != brstm.ADPCM:
        raise MusicError('the stock stream is not DSP-ADPCM')
    if len(channels) != info.channels:
        raise MusicError(f'{len(channels)} channels for a {info.channels}-channel stream')
    b = bytes(original)
    head, head_size = struct.unpack_from('>2I', b, 0x10)
    base = head + 8
    si = base + brstm.u32(b, base + 4)
    block_size, block_samples = struct.unpack_from('>2I', b, si + 0x18)
    n = len(channels[0])
    if loop is not None:
        start = min(int(loop[0]) // FRAME * FRAME, n - 1)
        n = max(start + 1, min(n, int(loop[1])))
        loop = (start, n)
    blocks = -(-n // block_samples)
    last_samples = n - (blocks - 1) * block_samples
    last_size = dsp_adpcm.samples_to_bytes(last_samples)
    if block_samples % FRAME or block_size != block_samples // FRAME * 8:
        raise MusicError(f'unexpected block layout ({block_size} bytes for {block_samples} samples)')
    last_padded = _align(last_size)
    encoded = []
    for samples in channels:
        coefs = train_coefs(samples[:n])
        data, histories = encode_channel(samples[:n], coefs, block_samples)
        encoded.append((coefs, data, histories))
    new_head = bytearray(b[head:head + head_size])
    o = si - head
    new_head[o + 1] = 1 if loop else 0
    struct.pack_into('>11I', new_head, o + 8, loop[0] if loop else 0, n, 0, blocks, block_size, block_samples,
                     last_size, last_samples, last_padded, block_samples, 4)
    ct = base + brstm.u32(b, base + 0x14)
    for c, (coefs, data, histories) in enumerate(encoded):
        ao = base + brstm.u32(b, base + brstm.u32(b, ct + 4 + 8 * c + 4) + 4) - head
        flat = data.reshape(-1)
        dec = dsp_adpcm.Lane(bytes(flat[:dsp_adpcm.samples_to_bytes(n) + 8]), n, coefs)
        loop_ps = loop_h1 = loop_h2 = 0
        if loop:
            decoded = dsp_adpcm.decode([dec])[0]
            loop_ps = int(flat[loop[0] // FRAME * 8])
            loop_h1 = int(decoded[loop[0] - 1]) if loop[0] >= 1 else 0
            loop_h2 = int(decoded[loop[0] - 2]) if loop[0] >= 2 else 0
        struct.pack_into('>16h', new_head, ao, *coefs)
        struct.pack_into('>HHhhHhh', new_head, ao + 0x20, 0, int(flat[0]), 0, 0, loop_ps, loop_h1, loop_h2)
    adpc_body = bytearray()
    for k in range(blocks):
        for _coefs, _data, histories in encoded:
            adpc_body += struct.pack('>hh', *histories[k])
    adpc_size = _align(8 + len(adpc_body))
    adpc = b'ADPC' + struct.pack('>I', adpc_size) + bytes(adpc_body)
    adpc += bytes(adpc_size - len(adpc))
    body = bytearray()
    for k in range(blocks):
        last = k == blocks - 1
        for _coefs, data, _histories in encoded:
            chunk = data[k].tobytes()[:last_size if last else block_size]
            body += chunk + bytes((last_padded if last else block_size) - len(chunk))
    data_size = _align(0x20 + len(body))
    data_chunk = b'DATA' + struct.pack('>II', data_size, 0x18) + bytes(0x14) + bytes(body)
    data_chunk += bytes(data_size - len(data_chunk))
    head_off = 0x40
    adpc_off = head_off + len(new_head)
    data_off = adpc_off + len(adpc)
    total = data_off + len(data_chunk)
    header = bytearray(b[:0x40])
    struct.pack_into('>I', header, 8, total)
    struct.pack_into('>6I', header, 0x10, head_off, len(new_head), adpc_off, len(adpc), data_off, len(data_chunk))
    new_head[o + 0x10:o + 0x14] = struct.pack('>I', data_off + 0x20)
    out = bytes(header) + bytes(new_head) + adpc + data_chunk
    brstm.parse(out)                                          # the reader agrees with the layout
    return out


def encode_wav(path, original, gain_db=0.0):
    """A WAV file (or any sound file ffmpeg decodes) -> ``(brstm bytes, notes)`` replacing the stock stream
    ``original`` (its bytes), its level changed by ``gain_db`` decibels (``clips.apply_gain``).

    Loop: the WAV's ``smpl`` loop when it has one; else the whole track loops when the stock stream loops."""
    from Sound import clips
    info = brstm.info(original)
    rate, x, wav_loop = clips.read_wav_frames(path)
    x, notes = clips.apply_gain(x, gain_db)
    x, note = fit_channels(x, info.channels)
    if note:
        notes.append(note)
    if rate != info.rate:
        x = resample(x, rate, info.rate)
        notes.append(f'resampled from {rate:,} Hz to {info.rate:,} Hz')
    if np.abs(x).max(initial=0) > 1.0:
        notes.append('peaks above full scale were clipped')
    samples = [to_int16(x[:, c]) for c in range(info.channels)]
    if not len(samples[0]):
        raise MusicError('the WAV has no sound in it')
    if wav_loop is not None:
        r = info.rate / rate
        loop = (int(wav_loop[0] * r), int(round(wav_loop[1] * r)))
        notes.append(f'loops from {loop[0] / info.rate:.2f} s (the WAV\'s loop)')
    elif info.loop is not None:
        loop = (0, len(samples[0]))
        notes.append('loops from the start (the stock track loops; a WAV smpl loop sets another point)')
    else:
        loop = None
    return build(original, samples, loop), notes
