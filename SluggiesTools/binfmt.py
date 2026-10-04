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
import re
import struct
import zlib

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


# SKN runtime limits on SK1/SK2 (direct-write) entries. See
# _docs/_docs_model_format/skn_section.html#runtime-limits. Every vanilla
# skinned block obeys them (180 unique blocks, 10,246 SK1/SK2 entries:
# minimum count 3 for both kinds, maximum source bytes exactly 8180 / 4088).
SKN_MIN_DIRECT_VERTICES = 3
SKN_MAX_SOURCE_BYTES = {'SK1': 8180, 'SK2': 4088}


def skn_direct_entry_problem(kind: str, vertex_count: int, vertex_offset: int,
                             stride: int) -> str | None:
    """Why an SK1/SK2 entry would break the game's skinning loop, or None.

    The loop runs ``vertex_count - 1`` iterations through a count register,
    so a 1-vertex entry wraps to 2**32 iterations and reads past the
    locked-cache buffer; the entry's source (``vertex_offset + count *
    stride`` bytes) must also fit that buffer."""
    if vertex_count < SKN_MIN_DIRECT_VERTICES:
        return (f'{kind} entry has {vertex_count} vertices; the game needs at least '
                f'{SKN_MIN_DIRECT_VERTICES}')
    source = vertex_offset + vertex_count * stride
    if source > SKN_MAX_SOURCE_BYTES[kind]:
        return (f'{kind} entry source is {source} bytes ({vertex_count} vertices); '
                f'the game allows at most {SKN_MAX_SOURCE_BYTES[kind]}')
    return None


def skin_bone_ids(skin_data: dict | None) -> set[int]:
    """Bones a ``.sluggie`` SkinData/SkinDataEdited dict's SK1/SK2/SKAcc
    entries skin to (``BoneIndex``/``BoneIndex1``/``BoneIndex2``)."""
    bones: set[int] = set()
    for entry in (skin_data or {}).get('SK1s') or []:
        bones.add(int(entry['BoneIndex']))
    for entry in (skin_data or {}).get('SK2s') or []:
        bones.update((int(entry['BoneIndex1']), int(entry['BoneIndex2'])))
    for entry in (skin_data or {}).get('SKAccs') or []:
        bones.add(int(entry['BoneIndex']))
    return bones


# Prefix of a zlib-compressed binary field: "z:" + base64(zlib(bytes)). ':' is
# outside the base64 alphabet, so a field is self-describing and plain-base64,
# compressed and mixed files all decode with the same code. The Blender add-on
# keeps its own copy of this codec (BlenderAddonSrc/FieldCodec.py).
ZLIB_FIELD_PREFIX = 'z:'
ZLIB_FIELD_LEVEL = 6


def decode_field(value, use_base64: bool = True) -> bytes | None:
    """Decode a binary field from a ``.sluggie`` JSON value.

    A string is base64, or ``"z:"`` + base64 of zlib-compressed bytes; a
    list is raw byte ints (``--debug`` export mode). ``use_base64`` is kept
    for the callers' signature: the value's own type decides.  ``None``
    passes through unchanged so callers can probe optional fields.
    """
    if value is None:
        return None
    if isinstance(value, str):
        if value.startswith(ZLIB_FIELD_PREFIX):
            return zlib.decompress(base64.b64decode(value[len(ZLIB_FIELD_PREFIX):]))
        return base64.b64decode(value)
    return bytes(value)


def encode_field(data: bytes, use_base64: bool = True):
    """Encode a binary field for a ``.sluggie`` JSON value; inverse of
    :func:`decode_field`. With base64, the zlib form is used whenever it is
    shorter than plain base64 (tiny fields stay plain)."""
    if not use_base64:
        return list(data)
    data = bytes(data)
    plain = base64.b64encode(data).decode('ascii')
    packed = ZLIB_FIELD_PREFIX + base64.b64encode(
        zlib.compress(data, ZLIB_FIELD_LEVEL)).decode('ascii')
    return packed if len(packed) < len(plain) else plain


# ACT geo names are "<stem>.gpl" (or ".tpl"). The game's own data leaves 34 of
# them (10 distinct names, all 12 or 16 characters long) without their NUL
# terminator: one leftover byte follows the extension before the zero padding,
# e.g. "nokonoko.gpl" + 0xB0, or "mii_male.gpl" + 'p' (the Mii ".gplp").
_GEO_NAME = re.compile(r'.+?\.(?:gpl|tpl)')


def clean_geo_name(name: str) -> str:
    """``name`` cut after its first ``.gpl``/``.tpl`` extension, dropping the
    leftover byte of an unterminated game string; unchanged without one."""
    match = _GEO_NAME.match(name)
    return match.group(0) if match else name
