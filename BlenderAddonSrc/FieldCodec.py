"""Binary-field codec for .sluggie JSON values.

bpy-free. Mirrors SluggiesTools/binfmt.py (decode_field/encode_field); the
add-on keeps its own copy because it must not import SluggiesTools.

A field is a base64 string, "z:" + base64 of zlib-compressed bytes, or a
list of byte ints (--debug exports). ':' is outside the base64 alphabet, so
every value says how it is encoded and old, compressed and mixed files all
decode the same way.
"""

import base64
import zlib

ZLIB_FIELD_PREFIX = 'z:'
ZLIB_FIELD_LEVEL = 6


def decode_field(value):
    """Bytes of a binary field; None passes through."""
    if value is None:
        return None
    if isinstance(value, str):
        if value.startswith(ZLIB_FIELD_PREFIX):
            return zlib.decompress(base64.b64decode(value[len(ZLIB_FIELD_PREFIX):]))
        return base64.b64decode(value)
    return bytes(value)


def encode_field(raw, use_base64=True):
    """Encode bytes for a binary field. With base64, the zlib form is used
    whenever it is shorter than plain base64 (tiny fields stay plain)."""
    if not use_base64:
        return list(raw)
    raw = bytes(raw)
    plain = base64.b64encode(raw).decode('ascii')
    packed = ZLIB_FIELD_PREFIX + base64.b64encode(
        zlib.compress(raw, ZLIB_FIELD_LEVEL)).decode('ascii')
    return packed if len(packed) < len(plain) else plain
