"""Nintendo DSP-ADPCM (GameCube / Wii) decoding, many independent streams at once.

A DSP-ADPCM stream is a run of 8-byte frames: one header byte (predictor index in the high nibble, scale
exponent in the low one) and 14 signed 4-bit samples. Each sample is predicted from the two before it with
one of 8 coefficient pairs (the stream's own, in 1/2048 units)::

    sample = clamp16((nibble * 2**scale * 2048 + 1024 + c1 * h1 + c2 * h2) >> 11);  h2, h1 = h1, sample

The recurrence is sequential within a stream, so one stream decodes sample by sample. Independent streams
(a sound archive's waves, a music stream's blocks, which each start from a stored history) are decoded side
by side instead: ``decode`` runs the recurrence once per sample position over a whole batch of lanes with
numpy. Bit-exact with the plain loop (``decode_reference``); the tests compare the two.

Nibble counts: sound archives give wave lengths in nibbles of the stream (header nibbles included);
``nibbles_to_samples`` converts them.

Encoding (``encode``): per 14-sample frame, every predictor pair and scale (8 x 16 candidates) is tried closed
loop on the decoder's own history, each sample rounded to the nearest nibble, and the candidate with the least
squared error wins (the first on a tie). The candidates run side by side in numpy; ``encode_reference`` is the
plain loop, bit-exact with it. The coefficients are given (a stock wave's, chosen by ``brsar_write``); no
coefficient training here.
"""

import time

import numpy as np

FRAME_BYTES = 8
YIELD_FRAMES = 16                 # encode: release the GIL every so many frames (GUI stays responsive)
FRAME_SAMPLES = 14
FRAME_NIBBLES = 16

# Elements of the per-batch sample table (lanes x samples, int64) decoded together: bounds the memory.
BATCH_ELEMENTS = 1 << 23


class Lane:
    """One independent DSP-ADPCM stream: ``data`` starts at a frame boundary and holds at least the frames of
    its ``samples``; ``coefs`` are the 16 coefficients (8 pairs c1, c2); ``h1``/``h2`` the history before the
    first sample (0, 0 for a stream start; a music block's stored history)."""

    __slots__ = ('data', 'samples', 'coefs', 'h1', 'h2')

    def __init__(self, data, samples, coefs, h1=0, h2=0):
        if len(coefs) != 16:
            raise ValueError(f'16 coefficients expected, got {len(coefs)}')
        self.data = bytes(data)
        self.samples = int(samples)
        self.coefs = [int(c) for c in coefs]
        self.h1 = int(h1)
        self.h2 = int(h2)

    @property
    def frames(self):
        return (self.samples + FRAME_SAMPLES - 1) // FRAME_SAMPLES


def nibbles_to_samples(nibbles):
    """Sample count of a stream ``nibbles`` nibbles long (the first two nibbles of every 16 are the frame
    header): the DSP's nibble-address-to-sample conversion."""
    return nibble_to_sample(nibbles)


def nibble_to_sample(nibble):
    """Sample index of a nibble position (loop points are given in nibbles)."""
    nibble = int(nibble)
    return nibble // FRAME_NIBBLES * FRAME_SAMPLES + max(0, nibble % FRAME_NIBBLES - 2)


def samples_to_bytes(samples):
    """Bytes a stream of ``samples`` samples occupies, the last frame's unused nibbles included."""
    samples = int(samples)
    full, rest = divmod(samples, FRAME_SAMPLES)
    return full * FRAME_BYTES + (1 + (rest + 1) // 2 if rest else 0)


def decode_reference(lane):
    """The plain per-sample loop (int16 values in a list). Slow; the tests' oracle."""
    out, h1, h2 = [], lane.h1, lane.h2
    data, coefs = lane.data, lane.coefs
    offset = 0
    while len(out) < lane.samples:
        header = data[offset]
        scale = 1 << (header & 0xF)
        c1, c2 = coefs[2 * (header >> 4)], coefs[2 * (header >> 4) + 1]
        for k in range(FRAME_SAMPLES):
            if len(out) >= lane.samples:
                break
            byte = data[offset + 1 + k // 2]
            nibble = (byte >> 4) if k % 2 == 0 else (byte & 0xF)
            if nibble >= 8:
                nibble -= 16
            value = (((nibble * scale) << 11) + 1024 + c1 * h1 + c2 * h2) >> 11
            value = max(-32768, min(32767, value))
            out.append(value)
            h2, h1 = h1, value
        offset += FRAME_BYTES
    return out


def decode(lanes):
    """Decode every lane; returns one ``numpy.int16`` array per lane, in the lanes' order."""
    lanes = list(lanes)
    results = [None] * len(lanes)
    order = sorted(range(len(lanes)), key=lambda i: -lanes[i].samples)      # similar lengths share a batch
    start = 0
    while start < len(order):
        longest = max(1, lanes[order[start]].samples)
        width = max(1, min(len(order) - start, BATCH_ELEMENTS // longest))
        batch = order[start:start + width]
        for index, samples in zip(batch, _decode_batch([lanes[i] for i in batch])):
            results[index] = samples
        start += width
    return results


def _decode_batch(lanes):
    count = len(lanes)
    frames = max(1, max(lane.frames for lane in lanes))
    steps = frames * FRAME_SAMPLES
    raw = np.zeros((count, frames * FRAME_BYTES), dtype=np.uint8)
    coefs = np.zeros((count, 16), dtype=np.int64)
    h1 = np.zeros(count, dtype=np.int64)
    h2 = np.zeros(count, dtype=np.int64)
    for row, lane in enumerate(lanes):
        need = lane.frames * FRAME_BYTES
        chunk = lane.data[:need]
        if len(chunk) < samples_to_bytes(lane.samples):
            raise ValueError(f'lane {row}: {len(lane.data)} bytes hold fewer than {lane.samples} samples')
        raw[row, :len(chunk)] = np.frombuffer(chunk, dtype=np.uint8)
        coefs[row] = lane.coefs
        h1[row], h2[row] = lane.h1, lane.h2
    frame_bytes = raw.reshape(count, frames, FRAME_BYTES)
    header = frame_bytes[:, :, 0].astype(np.int64)
    predictor = header >> 4
    scale = np.int64(1) << (header & 0xF)
    rows = np.arange(count)[:, None]
    c1 = np.repeat(coefs[rows, 2 * predictor], FRAME_SAMPLES, axis=1)         # per sample position
    c2 = np.repeat(coefs[rows, 2 * predictor + 1], FRAME_SAMPLES, axis=1)
    body = frame_bytes[:, :, 1:].astype(np.int64)
    nibbles = np.empty((count, frames, FRAME_SAMPLES), dtype=np.int64)
    nibbles[:, :, 0::2] = body >> 4
    nibbles[:, :, 1::2] = body & 0xF
    nibbles[nibbles >= 8] -= 16
    base = ((nibbles * scale[:, :, None]) << 11) + 1024
    base = base.reshape(count, steps)
    out = np.empty((count, steps), dtype=np.int16)
    for k in range(steps):
        value = (base[:, k] + c1[:, k] * h1 + c2[:, k] * h2) >> 11
        np.clip(value, -32768, 32767, out=value)
        out[:, k] = value
        h2, h1 = h1, value
    return [out[row, :lane.samples].copy() for row, lane in enumerate(lanes)]


# ------------------------------------------------------------------------------------------------ encoding

def samples_to_nibbles(samples):
    """Length in nibbles of a stream of ``samples`` samples (the sound archive's wave length)."""
    samples = int(samples)
    full, rest = divmod(samples, FRAME_SAMPLES)
    return full * FRAME_NIBBLES + (rest + 2 if rest else 0)


def encode_reference(samples, coefs, h1=0, h2=0):
    """The plain per-frame search (``encode``'s oracle): ``(frame bytes, (h1, h2) after the last sample)``."""
    x = [int(v) for v in samples]
    frames = (len(x) + FRAME_SAMPLES - 1) // FRAME_SAMPLES
    x += [0] * (frames * FRAME_SAMPLES - len(x))
    out = bytearray()
    for f in range(frames):
        frame = x[f * FRAME_SAMPLES:(f + 1) * FRAME_SAMPLES]
        best = None
        for predictor in range(8):
            c1, c2 = coefs[2 * predictor], coefs[2 * predictor + 1]
            for scale_exp in range(16):
                scale = 1 << scale_exp
                a1, a2, err, nibbles = h1, h2, 0, []
                for t in frame:
                    base = c1 * a1 + c2 * a2
                    predicted = (base + 1024) >> 11
                    q = max(-8, min(7, round((t - predicted) / scale)))
                    v = max(-32768, min(32767, (((q * scale) << 11) + 1024 + base) >> 11))
                    err += (t - v) ** 2
                    nibbles.append(q & 0xF)
                    a2, a1 = a1, v
                if best is None or err < best[0]:
                    best = (err, predictor, scale_exp, nibbles, a1, a2)
        _err, predictor, scale_exp, nibbles, h1, h2 = best
        out.append((predictor << 4) | scale_exp)
        for k in range(0, FRAME_SAMPLES, 2):
            out.append((nibbles[k] << 4) | nibbles[k + 1])
    return bytes(out), (h1, h2)


def encode(samples, coefs, h1=0, h2=0):
    """16-bit samples -> ``(DSP-ADPCM frame bytes, (h1, h2) after the last sample)``. Whole 8-byte frames; a
    partial last frame is zero-padded (the padding counts in the error, as the reference does)."""
    if len(coefs) != 16:
        raise ValueError(f'16 coefficients expected, got {len(coefs)}')
    x = np.asarray(samples, dtype=np.int64)
    n = len(x)
    frames = (n + FRAME_SAMPLES - 1) // FRAME_SAMPLES
    padded = np.zeros(frames * FRAME_SAMPLES, np.int64)
    padded[:n] = x
    c1 = np.repeat(np.array(coefs[0::2], np.int64), 16)            # candidate k = predictor * 16 + scale
    c2 = np.repeat(np.array(coefs[1::2], np.int64), 16)
    scale = np.tile(np.int64(1) << np.arange(16, dtype=np.int64), 8)
    fscale = scale.astype(np.float64)
    out = bytearray()
    h1, h2 = int(h1), int(h2)
    for f in range(frames):
        if f % YIELD_FRAMES == 0:
            time.sleep(0)                    # let other threads (a GUI's render loop) run between frames
        frame = padded[f * FRAME_SAMPLES:(f + 1) * FRAME_SAMPLES]
        a1 = np.full(128, h1, np.int64)
        a2 = np.full(128, h2, np.int64)
        err = np.zeros(128, np.int64)
        nibbles = np.empty((FRAME_SAMPLES, 128), np.int64)
        for i in range(FRAME_SAMPLES):
            t = frame[i]
            base = c1 * a1 + c2 * a2
            predicted = (base + 1024) >> 11
            q = np.clip(np.round((t - predicted) / fscale), -8, 7).astype(np.int64)
            v = np.clip((((q * scale) << 11) + 1024 + base) >> 11, -32768, 32767)
            err += (t - v) ** 2
            nibbles[i] = q & 0xF
            a2, a1 = a1, v
        k = int(np.argmin(err))
        predictor, scale_exp = divmod(k, 16)
        out.append((predictor << 4) | scale_exp)
        column = nibbles[:, k]
        for j in range(0, FRAME_SAMPLES, 2):
            out.append(int((column[j] << 4) | column[j + 1]))
        h1, h2 = int(a1[k]), int(a2[k])
    return bytes(out), (h1, h2)
