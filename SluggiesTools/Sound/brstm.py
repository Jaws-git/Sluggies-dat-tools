"""Reader for ``.brstm`` music streams (RSTM 1.0, ``sound_NA/stream/``).

Layout (big-endian): header ``RSTM`` with the HEAD, ADPC and DATA chunk offsets and sizes at 0x10. HEAD
(refs relative to HEAD + 8): stream info ``{u8 codec (0 PCM8, 1 PCM16, 2 DSP-ADPCM), u8 loop, u8 channels,
u16 rate, u32 loop start, samples, data offset, blocks, block size, block samples, last block size, last block
samples, last block padded, ADPC interval, ADPC entry bytes}``; a channel table whose entries point at each
channel's ADPCM info ``{16 x s16 coefs, gain, ps, h1, h2, loop ps, loop h1, loop h2}``. ADPC: the decoder
history ``(h1, h2)`` per block and channel. DATA: blocks of ``block size`` bytes per channel, channels
interleaved block by block, the last block padded.

Every block starts from its ADPC history, as the game's player does, so the blocks of a stream decode side by
side (``dsp_adpcm.decode``).
"""

import struct
from dataclasses import dataclass

import numpy as np

from Sound import dsp_adpcm
from Sound.dsp_adpcm import Lane

PCM8, PCM16, ADPCM = 0, 1, 2


class BrstmError(Exception):
    pass


def u16(b, o): return struct.unpack_from('>H', b, o)[0]
def u32(b, o): return struct.unpack_from('>I', b, o)[0]
def s16(b, o): return struct.unpack_from('>h', b, o)[0]


@dataclass
class Stream:
    codec: int
    rate: int
    channels: int
    samples: int
    loop: tuple                 # (start, end) samples, end exclusive, or None
    lanes: list                 # ADPCM: [[Lane per block] per channel]; PCM: [[bytes per block] per channel]
    block_samples: int
    last_block_samples: int


def parse(b):
    if b[:4] != b'RSTM' or u16(b, 4) != 0xFEFF:
        raise BrstmError('not a BRSTM file (no RSTM magic)')
    head, _, adpc, adpc_size, data, _ = struct.unpack_from('>6I', b, 0x10)
    base = head + 8
    si = base + u32(b, base + 4)
    codec, looped, nch = b[si], b[si + 1], b[si + 2]
    rate = u16(b, si + 4)
    (loop_start, samples, data_offset, blocks, block_size, block_samples, last_size, last_samples,
     last_padded, _interval, _entry) = struct.unpack_from('>11I', b, si + 8)
    if b[data:data + 4] != b'DATA' or data_offset != data + 0x20:
        raise BrstmError('DATA chunk not where the stream info says')
    coefs, history = [], []
    if codec == ADPCM:
        ct = base + u32(b, base + 0x14)
        if b[ct] != nch:
            raise BrstmError(f'{b[ct]} channel infos for {nch} channels')
        for c in range(nch):
            ao = base + u32(b, base + u32(b, ct + 4 + 8 * c + 4) + 4)
            coefs.append([s16(b, ao + 2 * k) for k in range(16)])
            history.append((s16(b, ao + 0x24), s16(b, ao + 0x26)))
        if not adpc or b[adpc:adpc + 4] != b'ADPC':
            raise BrstmError('ADPCM stream without an ADPC chunk')
    lanes = [[] for _ in range(nch)]
    for k in range(blocks):
        last = k == blocks - 1
        stride = last_padded if last else block_size
        size = last_size if last else block_size
        count = last_samples if last else block_samples
        o = data_offset + k * block_size * nch
        for c in range(nch):
            chunk = b[o + c * stride:o + c * stride + size]
            if codec == ADPCM:
                h1, h2 = (s16(b, adpc + 8 + 4 * (k * nch + c)), s16(b, adpc + 10 + 4 * (k * nch + c)))
                lanes[c].append(Lane(chunk, count, coefs[c], h1, h2))
            else:
                lanes[c].append(chunk)
    loop = (loop_start, samples) if looped else None
    return Stream(codec=codec, rate=rate, channels=nch, samples=samples, loop=loop, lanes=lanes,
                  block_samples=block_samples, last_block_samples=last_samples)


def decode(stream):
    """``[int16 array per channel]`` of a parsed stream."""
    if stream.codec == ADPCM:
        flat = [lane for channel in stream.lanes for lane in channel]
        decoded = dsp_adpcm.decode(flat)
        out, i = [], 0
        for channel in stream.lanes:
            parts = decoded[i:i + len(channel)]
            i += len(channel)
            out.append(np.concatenate(parts) if parts else np.zeros(0, np.int16))
        return out
    out = []
    for channel in stream.lanes:
        if stream.codec == PCM16:
            parts = [np.frombuffer(chunk, dtype='>i2').astype(np.int16) for chunk in channel]
        elif stream.codec == PCM8:
            parts = [np.frombuffer(chunk, dtype=np.int8).astype(np.int16) << 8 for chunk in channel]
        else:
            raise BrstmError(f'unknown codec {stream.codec}')
        samples = np.concatenate(parts) if parts else np.zeros(0, np.int16)
        out.append(samples[:stream.samples])
    return out
