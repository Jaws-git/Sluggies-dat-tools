"""Decode GX texture data to RGBA with numpy: the formats the icon bank uses (GUI character grid).

``decode(fmt, data, width, height, palette=b'', palette_format=0)`` returns a
``(height, width, 4)`` uint8 array. Supported:

* ``CMPR`` (0x0E): the roster's packed portrait pages;
* ``C8`` (0x09) with an ``IA8`` (0), ``RGB565`` (1) or ``RGB5A3`` (2)
  palette: the stock icon pages (side/front sheets use RGB5A3, the Mii icon
  page IA8);
* ``IA8`` (0x03) texels.

Results match wimgt's decoder exactly (``Icons/tests/test_gx_decode.py``,
fixtures generated once with wimgt). Big-endian, as everything in the game.
"""

import numpy as np

IA8 = 0x03
C8 = 0x09
CMPR = 0x0E
PALETTE_IA8, PALETTE_RGB565, PALETTE_RGB5A3 = 0, 1, 2
FORMATS = (IA8, C8, CMPR)
PALETTE_FORMATS = (PALETTE_IA8, PALETTE_RGB565, PALETTE_RGB5A3)

# (block width, block height, bits per texel)
_BLOCKS = {IA8: (4, 4, 16), C8: (8, 4, 8), CMPR: (8, 8, 4)}


class GxDecodeError(ValueError):
    pass


def data_length(fmt: int, width: int, height: int) -> int:
    """Bytes of image data for a ``width`` x ``height`` texture (sides padded to whole blocks)."""
    if fmt not in _BLOCKS:
        raise GxDecodeError(f'texture format 0x{fmt:02X} is not supported')
    bw, bh, bits = _BLOCKS[fmt]
    return (-(-width // bw) * bw) * (-(-height // bh) * bh) * bits // 8


def _expand(value, bits: int):
    """An n-bit channel to 8 bits as ``round(v * 255 / (2**n - 1))``, as wimgt does (bit replication, the GX
    hardware's way, is off by one for a few values)."""
    top = (1 << bits) - 1
    value = value.astype(np.uint32)
    return ((value * 510 + top) // (2 * top)).astype(np.uint8)


def _u16(data: bytes, count: int):
    return np.frombuffer(data, dtype='>u2', count=count).astype(np.uint16)


def _rgb565(values):
    return np.stack([_expand(values >> 11, 5), _expand((values >> 5) & 0x3F, 6), _expand(values & 0x1F, 5),
                     np.full(values.shape, 255, np.uint8)], axis=-1)


def _rgb5a3(values):
    opaque = (values & 0x8000) != 0
    r = np.where(opaque, _expand((values >> 10) & 0x1F, 5), _expand((values >> 8) & 0x0F, 4))
    g = np.where(opaque, _expand((values >> 5) & 0x1F, 5), _expand((values >> 4) & 0x0F, 4))
    b = np.where(opaque, _expand(values & 0x1F, 5), _expand(values & 0x0F, 4))
    a = np.where(opaque, np.uint8(255), _expand((values >> 12) & 0x07, 3))
    return np.stack([r, g, b, a], axis=-1).astype(np.uint8)


def _ia8(values):
    i = (values & 0xFF).astype(np.uint8)
    return np.stack([i, i, i, (values >> 8).astype(np.uint8)], axis=-1)


def palette_colors(palette: bytes, palette_format: int):
    """``(entries, 4)`` RGBA colours of a palette."""
    count = len(palette) // 2
    values = _u16(palette, count)
    if palette_format == PALETTE_IA8:
        return _ia8(values)
    if palette_format == PALETTE_RGB565:
        return _rgb565(values)
    if palette_format == PALETTE_RGB5A3:
        return _rgb5a3(values)
    raise GxDecodeError(f'palette format {palette_format} is not supported')


def _untile(texels, width: int, height: int, bw: int, bh: int):
    """Block-ordered texels (``(blocks, bh, bw, ...)``, row-major blocks) to a cropped ``(height, width, ...)``."""
    pw, ph = -(-width // bw) * bw, -(-height // bh) * bh
    rest = texels.shape[3:]
    grid = texels.reshape(ph // bh, pw // bw, bh, bw, *rest).swapaxes(1, 2).reshape(ph, pw, *rest)
    return grid[:height, :width]


def _cmpr(data: bytes, width: int, height: int):
    pw, ph = -(-width // 8) * 8, -(-height // 8) * 8
    blocks = np.frombuffer(data, dtype=np.uint8, count=pw * ph // 2).reshape(-1, 8)   # 4x4 sub-blocks
    c0 = blocks[:, 0].astype(np.uint16) << 8 | blocks[:, 1]
    c1 = blocks[:, 2].astype(np.uint16) << 8 | blocks[:, 3]
    p0, p1 = _rgb565(c0).astype(np.int32), _rgb565(c1).astype(np.int32)
    four = (c0 > c1)[:, None]
    p2 = np.where(four, (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four, (p0 + 2 * p1) // 3, 0)
    p2[:, 3] = 255
    p3[:, 3] = np.where(four[:, 0], 255, 0)
    colors = np.stack([p0, p1, p2, p3], axis=1).astype(np.uint8)              # (sub-blocks, 4, 4)
    shifts = np.array([6, 4, 2, 0], np.uint8)
    index = (blocks[:, 4:8, None] >> shifts) & 3                                # (sub-blocks, 4 rows, 4 cols)
    sub = colors[np.arange(len(blocks))[:, None, None], index]                 # (sub-blocks, 4, 4, 4)
    # each 8x8 tile holds its four 4x4 sub-blocks in the order TL, TR, BL, BR
    tiles = sub.reshape(-1, 2, 2, 4, 4, 4).swapaxes(2, 3).reshape(-1, 8, 8, 4)
    return _untile(tiles, width, height, 8, 8)


def decode(fmt: int, data: bytes, width: int, height: int, palette: bytes = b'', palette_format: int = 0):
    """The texture as a ``(height, width, 4)`` RGBA uint8 array."""
    length = data_length(fmt, width, height)
    if len(data) < length:
        raise GxDecodeError(f'{width}x{height} format 0x{fmt:02X} needs 0x{length:X} bytes, got 0x{len(data):X}')
    if fmt == CMPR:
        return np.ascontiguousarray(_cmpr(data, width, height))
    bw, bh, _bits = _BLOCKS[fmt]
    if fmt == C8:
        if not palette:
            raise GxDecodeError('a C8 texture needs its palette')
        colors = palette_colors(palette, palette_format)
        index = np.frombuffer(data, dtype=np.uint8, count=length)
        if int(index.max(initial=0)) >= len(colors):
            raise GxDecodeError(f'C8 index {int(index.max())} is beyond the {len(colors)}-entry palette')
        texels = colors[index].reshape(-1, bh, bw, 4)
    else:                                                                       # IA8
        texels = _ia8(_u16(data, length // 2)).reshape(-1, bh, bw, 4)
    return np.ascontiguousarray(_untile(texels, width, height, bw, bh))
