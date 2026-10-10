"""RIFF WAVE writing: 16-bit PCM, any channel count and rate, with an optional ``smpl`` loop chunk.

The game's sounds decode to 16-bit PCM, so a 16-bit WAV holds them without loss. Looping sounds keep their
loop points in a ``smpl`` chunk (one forward loop; sample editors and game-audio tools read it; players ignore it).
"""

import os
import struct

import numpy as np


def wav_bytes(rate, channels, loop=None):
    """``channels``: one int16 array per channel, all of one length. ``loop``: ``(start, end)`` sample indexes
    (``end`` exclusive, as the game stores it) or None."""
    if not channels:
        raise ValueError('at least one channel')
    length = len(channels[0])
    if any(len(c) != length for c in channels):
        raise ValueError('channels differ in length')
    frames = np.empty((length, len(channels)), dtype='<i2')
    for i, channel in enumerate(channels):
        frames[:, i] = np.asarray(channel, dtype=np.int16)
    data = frames.tobytes()
    block_align = 2 * len(channels)
    fmt = struct.pack('<HHIIHH', 1, len(channels), rate, rate * block_align, block_align, 16)
    chunks = [(b'fmt ', fmt), (b'data', data)]
    if loop is not None:
        start, end = loop
        smpl = struct.pack('<9I', 0, 0, 1_000_000_000 // rate, 60, 0, 0, 0, 1, 0)
        smpl += struct.pack('<6I', 0, 0, int(start), max(int(start), int(end) - 1), 0, 0)
        chunks.append((b'smpl', smpl))
    body = b''.join(cid + struct.pack('<I', len(payload)) + payload + (b'\0' if len(payload) & 1 else b'')
                    for cid, payload in chunks)
    return b'RIFF' + struct.pack('<I', 4 + len(body)) + b'WAVE' + body


def write_wav(path, rate, channels, loop=None):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(wav_bytes(rate, channels, loop))
