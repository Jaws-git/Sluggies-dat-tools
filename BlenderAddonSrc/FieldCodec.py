"""Binary-field and file codec for .sluggie files.

bpy-free. Mirrors SluggiesTools/binfmt.py (decode_field/encode_field,
loads_sluggie/load_sluggie/dump_sluggie); the add-on keeps its own copy
because it must not import SluggiesTools.

A .sluggie file is JSON. A regular file is xz-compressed as a whole and its
binary fields are "x:" + hex; a --debug file (UseCompression false) is plain JSON
with binary fields as byte-int lists. Older files hold plain base64 fields
or "z:" + base64 of zlib-compressed bytes. ':' is outside the base64
alphabet, so every value says how it is encoded and old, new and mixed files
all decode the same way.
"""

import base64
import json
import lzma
import zlib

HEX_FIELD_PREFIX = 'x:'
ZLIB_FIELD_PREFIX = 'z:'
XZ_MAGIC = b'\xfd7zXZ\x00'
XZ_PRESET = 6


def decode_field(value):
    """Bytes of a binary field; None passes through."""
    if value is None:
        return None
    if isinstance(value, str):
        if value.startswith(HEX_FIELD_PREFIX):
            return bytes.fromhex(value[len(HEX_FIELD_PREFIX):])
        if value.startswith(ZLIB_FIELD_PREFIX):
            return zlib.decompress(base64.b64decode(value[len(ZLIB_FIELD_PREFIX):]))
        return base64.b64decode(value)
    return bytes(value)


def encode_field(raw, use_compression=True):
    """Encode bytes for a binary field: "x:" + hex, or a byte-int list when
    use_compression is false (--debug)."""
    if not use_compression:
        return list(raw)
    return HEX_FIELD_PREFIX + bytes(raw).hex()


def encode_compact(raw):
    """"z:" + base64 of zlib-compressed bytes, for buffers kept outside a
    .sluggie file (custom properties in the .blend), where no file-wide
    compression applies. decode_field reads it."""
    return ZLIB_FIELD_PREFIX + base64.b64encode(zlib.compress(bytes(raw), 6)).decode('ascii')


def loads_sluggie(raw):
    """The document of a .sluggie file's bytes: xz-compressed or plain JSON.
    A damaged xz stream raises json.JSONDecodeError like broken JSON does."""
    if raw.startswith(XZ_MAGIC):
        try:
            raw = lzma.decompress(raw)
        except lzma.LZMAError as exc:
            raise json.JSONDecodeError(f'damaged xz data: {exc}', '', 0) from exc
    return json.loads(raw.decode('utf-8-sig'))


def load_sluggie(path):
    """Read a .sluggie file (see loads_sluggie)."""
    with open(path, 'rb') as handle:
        return loads_sluggie(handle.read())


def dump_sluggie(document, path, text=None):
    """Write a .sluggie file: xz-compressed, or plain JSON for a --debug
    document (SluggiesModel.UseCompression false). text is the JSON to write
    (default: document indented by 2)."""
    if text is None:
        text = json.dumps(document, indent=2)
    raw = text.encode('utf-8')
    if (document.get('SluggiesModel') or {}).get('UseCompression', True):
        raw = lzma.compress(raw, preset=XZ_PRESET)
    with open(path, 'wb') as handle:
        handle.write(raw)
