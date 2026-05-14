"""drawlist.py — GX primitive list encoder / decoder.

A primitive list is the raw binary blob stored at each DODisplayState's
primitiveListPtr.  It is a packed stream of GX draw calls:

    [type: u8] [vertex_count: u16 BE] [vertex_data ...]  ...  [0x00 terminator]

Each vertex is a sequence of per-attribute index values whose byte width (1 or
2) and order are defined by the active Type-3 display state's descriptor list:

    descriptors = [{'key': str, 'direct': bool, 'index_size': int}, ...]

Attribute keys and their order match those produced by DODisplayState.updateState():
    position, lighting, color0, color1, texture0 … texture7

Public API
----------
decodeDrawList(raw_bytes, descriptors)
    → list of triangular faces as vertex-dict lists

encodeDrawList(faces, descriptors)
    → raw bytes (single GX_TRIANGLES block, no terminator)

rebuildDrawList(original_raw_bytes, descriptors, new_faces)
    → raw bytes replacing all GX primitives in original with new_faces
"""

import struct

# GX primitive type identifiers
GX_TRIANGLES      = 0x90
GX_TRIANGLESTRIP  = 0x98
GX_QUADS          = 0x80

# Maximum vertex count per GX primitive block (uint16 limit)
_MAX_VERTS_PER_BLOCK = 0xFFFF

# Bit-shift positions of each attribute within a Type-3 display-state setting word.
# Two bits per attribute: 00=off, 01=direct, 10=1-byte indexed, 11=2-byte indexed.
# Bits 0-1 are the position matrix (skipped in practice).
_ATTR_BIT_SHIFT = {
    'position': 2,  'lighting': 4,  'color0': 6,   'color1': 8,
    'texture0': 10, 'texture1': 12, 'texture2': 14, 'texture3': 16,
    'texture4': 18, 'texture5': 20, 'texture6': 22, 'texture7': 24,
}


def computeRequiredDescriptors(faces: list, descriptors: list):
    """Scan *faces* and widen any 1-byte attribute that has indices > 255 to 2-byte.

    Returns *(new_descriptors, upgraded_keys)* where *upgraded_keys* is the
    set of attribute key names that were widened (empty when no upgrade needed).
    """
    if not faces:
        return list(descriptors), set()
    max_by_key: dict[str, int] = {}
    for face in faces:
        for vertex in face:
            for key, idx in vertex.items():
                if idx > max_by_key.get(key, 0):
                    max_by_key[key] = idx
    new_descs = []
    upgraded: set[str] = set()
    for d in descriptors:
        new_d = dict(d)
        if d['index_size'] == 1 and max_by_key.get(d['key'], 0) > 255:
            new_d['index_size'] = 2
            upgraded.add(d['key'])
        new_descs.append(new_d)
    return new_descs, upgraded


def patchType3Setting(old_setting: int, upgraded_keys) -> int:
    """Return an updated Type-3 display-state setting word with the index-size
    bits for each key in *upgraded_keys* flipped from 1-byte (0b10) to
    2-byte (0b11) by setting the LSB of the relevant 2-bit field."""
    new_setting = old_setting
    for key in upgraded_keys:
        shift = _ATTR_BIT_SHIFT.get(key)
        if shift is not None:
            new_setting |= (1 << shift)  # 10 → 11
    return new_setting


def _vertex_size(descriptors: list) -> int:
    """Return the byte size of one vertex record given an active descriptor list."""
    return sum(d['index_size'] for d in descriptors)


def decodeDrawList(raw_bytes: bytes, descriptors: list) -> list:
    """Parse raw GX primitive list bytes into a list of triangular faces.

    Each face is a list of 3 vertex dicts, where each dict maps attribute key
    (e.g. ``'position'``, ``'texture0'``) to its per-vertex index value.

    Parameters
    ----------
    raw_bytes:
        The raw bytes of one PrimitiveList (one DODisplayState's primitive data).
    descriptors:
        The active attribute descriptor list produced by the most recent Type-3
        DODisplayState.  Each entry: ``{'key': str, 'direct': bool,
        'index_size': int}`` where ``index_size`` is 1 or 2.

    Returns
    -------
    list of faces, each face a list of 3 vertex dicts.
    """
    data = raw_bytes
    pos  = 0
    faces = []

    while pos < len(data) and data[pos] != 0:
        primitive = data[pos];  pos += 1
        if pos + 2 > len(data):
            break
        vertex_count = (data[pos] << 8) | data[pos + 1];  pos += 2

        # Read vertex_count raw vertices
        vertices = []
        for _ in range(vertex_count):
            vertex = {}
            for entry in descriptors:
                key        = entry['key']
                idx_size   = entry['index_size']
                idx = 0
                for _ in range(idx_size):
                    idx = (idx << 8) | data[pos];  pos += 1
                vertex[key] = idx
            vertices.append(vertex)

        # Convert to triangles
        match primitive:
            case 0x90:  # GX_TRIANGLES
                for i in range(0, len(vertices), 3):
                    if i + 2 < len(vertices):
                        # Winding is reversed on import (see gpl.py); keep consistent
                        faces.append([vertices[i + 2], vertices[i + 1], vertices[i]])
            case 0x98:  # GX_TRIANGLESTRIP
                order = False
                for i in range(len(vertices) - 2):
                    if order:
                        faces.append([vertices[i], vertices[i + 1], vertices[i + 2]])
                    else:
                        faces.append([vertices[i + 2], vertices[i + 1], vertices[i]])
                    order = not order
            case 0x80:  # GX_QUADS
                for i in range(0, len(vertices), 4):
                    if i + 3 < len(vertices):
                        faces.append([vertices[i + 2], vertices[i + 1], vertices[i]])
                        faces.append([vertices[i], vertices[i + 3], vertices[i + 2]])
            case _:
                print(f"WARNING drawlist: unsupported primitive type 0x{primitive:02X}, skipping {vertex_count} vertices")

    return faces


def encodeDrawList(faces: list, descriptors: list) -> bytes:
    """Encode a list of triangular faces as a GX_TRIANGLES primitive list.

    Emits one or more ``GX_TRIANGLES`` blocks (splitting at the uint16 vertex
    count limit if needed).  Does NOT append a zero terminator byte; callers
    that require one should append ``b'\\x00'`` themselves.

    Parameters
    ----------
    faces:
        List of triangles.  Each triangle is a list of 3 vertex dicts mapping
        attribute key → index value.  The winding expected here matches the
        output of ``decodeDrawList`` (i.e. already accounting for the import
        reversal).
    descriptors:
        Active attribute descriptor list (same format as ``decodeDrawList``).

    Returns
    -------
    bytes — raw GX_TRIANGLES primitive data.
    """
    if not faces:
        return b''

    out = bytearray()

    # Flatten all triangle vertices; each triangle contributes 3 vertices.
    # We must emit in reverse-winding order relative to what decodeDrawList
    # produced, to undo the reversal applied on decode.
    all_vertices = []
    for face in faces:
        # face is [v2, v1, v0] as stored by decode; re-reverse for emit
        all_vertices.extend([face[2], face[1], face[0]])

    # Split into blocks of at most _MAX_VERTS_PER_BLOCK (must be a multiple of 3)
    block_size = (_MAX_VERTS_PER_BLOCK // 3) * 3
    for block_start in range(0, len(all_vertices), block_size):
        block = all_vertices[block_start : block_start + block_size]
        out.append(GX_TRIANGLES)
        out += struct.pack('>H', len(block))
        for vertex in block:
            for entry in descriptors:
                key      = entry['key']
                idx_size = entry['index_size']
                idx      = vertex.get(key, 0)
                max_idx  = (1 << (idx_size * 8)) - 1
                if idx > max_idx:
                    raise ValueError(
                        f"Index {idx} for attribute '{key}' overflows the "
                        f"{idx_size}-byte field used by the original draw list "
                        f"(max {max_idx}). The edited mesh has too many "
                        f"vertices/UV coords for this submesh."
                    )
                out += idx.to_bytes(idx_size, 'big')

    return bytes(out)


def rebuildDrawList(original_raw_bytes: bytes, descriptors: list, new_faces: list) -> bytes:
    """Replace all GX primitives in *original_raw_bytes* with *new_faces*.

    The original bytes are only used to detect the presence of a 0x00
    terminator at the end; if the original ended with one it is preserved.
    All actual primitive data is discarded and replaced with a single
    ``GX_TRIANGLES`` block built from *new_faces*.

    Parameters
    ----------
    original_raw_bytes:
        The raw bytes of the original PrimitiveList (used for terminator
        detection only).
    descriptors:
        Active attribute descriptor list for this primitive list.
    new_faces:
        New triangle list in the same format as ``encodeDrawList``.

    Returns
    -------
    bytes — new primitive list bytes.
    """
    new_data = encodeDrawList(new_faces, descriptors)

    # Preserve terminator byte if original had one
    had_terminator = len(original_raw_bytes) > 0 and original_raw_bytes[-1] == 0x00
    if had_terminator:
        new_data += b'\x00'

    return new_data
