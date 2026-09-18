"""Shared low-level binary/format primitives for the Sluggies toolchain.

These are the small, exactly-duplicated helpers that the exporter, the
hammerspace builder and the in-place patcher all need: GX quantize-format
sizing, 4-byte padding, big-endian scalar reads/writes, and the ``.sluggie``
binary-field codec.

Everything here is **big-endian** (Wii/GameCube), matching the rest of the
toolchain.  Keep this module dependency-free so any child script can import it
regardless of where it sits in the package.
"""

from __future__ import annotations

import base64
import struct

# GX vector quantize formats whose components are 4 bytes wide; everything
# else in the model data is 2 bytes per component.
_WIDE_VECTOR_FORMATS = (4, 7, 0xA)

# Bytes per vertex-color entry, keyed by the color quantize format nibble
# (0=RGB565, 1=RGB8, 2=RGBA8, 3=RGBA4444, 4=RGB8, 5=RGBA8).
_COLOR_ENTRY_SIZE = {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}


def comp_size(quantize_info: int) -> int:
    """Bytes per vector component from the quantize-format nibble."""
    return 4 if (quantize_info >> 4) in _WIDE_VECTOR_FORMATS else 2


def color_entry_size(quantize_info: int) -> int:
    """Bytes per vertex-color entry from the color quantize-format nibble."""
    return _COLOR_ENTRY_SIZE.get(quantize_info >> 4, 2)


def align4(data: bytes) -> bytes:
    """Zero-pad *data* up to the next 4-byte boundary."""
    return data + b'\x00' * ((4 - len(data) % 4) % 4)


def u8(data: bytes, offset: int) -> int:
    return data[offset]


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from('>H', data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('>I', data, offset)[0]


def itb(val: int, n: int) -> bytes:
    """int to bytes, big-endian, *n* bytes wide."""
    return val.to_bytes(n, 'big')


def bti(b: bytes) -> int:
    """bytes to int, big-endian."""
    return int.from_bytes(b, 'big')


def decode_field(value, use_base64: bool) -> bytes | None:
    """Decode a binary field from a ``.sluggie`` JSON value.

    With ``UseBase64`` the value is a base64 string, otherwise a list of byte
    ints (``--debug`` export mode).  ``None`` passes through unchanged so
    callers can probe optional fields.
    """
    if value is None:
        return None
    if use_base64:
        return base64.b64decode(value)
    return bytes(value)


def encode_field(data: bytes, use_base64: bool):
    """Encode a binary field for a ``.sluggie`` JSON value; inverse of
    :func:`decode_field`."""
    if use_base64:
        return base64.b64encode(data).decode('ascii')
    return list(data)
