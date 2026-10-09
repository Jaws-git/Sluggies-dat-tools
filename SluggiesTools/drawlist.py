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

encodeDrawList(faces, descriptors, strips=False)
    → raw bytes (GX_TRIANGLES blocks, or GX_TRIANGLESTRIP blocks plus one
      GX_TRIANGLES block for the leftovers when *strips*; no terminator)

stripify(triangles)
    → the greedy triangle strips behind ``strips=True`` (pure, testable)

rebuildDrawList(original_raw_bytes, descriptors, new_faces)
    → raw bytes replacing all GX primitives in original with new_faces
"""

import struct
import slogger

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
                slogger.warning(
                    f"unsupported primitive type 0x{primitive:02X}, skipping {vertex_count} vertices",
                    source='drawlist'
                )

    return faces


def encodeDrawList(faces: list, descriptors: list, strips: bool = False) -> bytes:
    """Encode a list of triangular faces as a GX primitive list.

    Emits one or more ``GX_TRIANGLES`` blocks (splitting at the uint16 vertex
    count limit if needed), or, with *strips*, greedy ``GX_TRIANGLESTRIP``
    blocks (``stripify``) followed by one ``GX_TRIANGLES`` block holding the
    triangles no strip took -- the layout vanilla lists use, at roughly half
    the bytes. Does NOT append a zero terminator byte; callers that require
    one should append ``b'\\x00'`` themselves.

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

    def emit_block(primitive: int, vertices: list) -> None:
        out.append(primitive)
        out.extend(struct.pack('>H', len(vertices)))
        for vertex in vertices:
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
                out.extend(idx.to_bytes(idx_size, 'big'))

    if strips:
        keys = [entry['key'] for entry in descriptors]
        # Strip over the drawn attributes only: two corners are the same GX
        # vertex when every index the layout writes is equal.
        tuples = [tuple(tuple(vertex.get(key, 0) for key in keys) for vertex in face) for face in faces]
        by_tuple = {}
        for face, corners in zip(faces, tuples):
            for vertex, corner in zip(face, corners):
                by_tuple.setdefault(corner, vertex)
        strip_runs, leftovers = stripify(tuples)
        for run in strip_runs:
            # A strip's vertex sequence is already in GX order (decodeDrawList
            # turns it back into the input faces).
            emit_block(GX_TRIANGLESTRIP, [by_tuple[corner] for corner in run])
        faces = [[by_tuple[corner] for corner in tri] for tri in leftovers]
        if not faces:
            return bytes(out)

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
        emit_block(GX_TRIANGLES, all_vertices[block_start : block_start + block_size])

    return bytes(out)


# ---------------------------------------------------------------------------
# Triangle strips
# ---------------------------------------------------------------------------
# decodeDrawList reads a GX_TRIANGLESTRIP s[0..n] as the faces
#   even i: [s[i+2], s[i+1], s[i]]      odd i: [s[i], s[i+1], s[i+2]]
# so a face (a, b, c) in that decoded space (the space every caller works in)
# sits at an even position i when it holds the directed edge s[i+1] -> s[i]
# (third vertex s[i+2]) and at an odd position when it holds s[i] -> s[i+1].
# stripify walks those directed edges greedily; the decoder is the check.

# Strips shorter than this many triangles cost more than the triangles block
# would (one 3-byte header per block): a 2-triangle strip is 3 + 4 records
# against 6 records, already cheaper for any record over 3 bytes.
_MIN_STRIP_TRIANGLES = 2


def _normalize(tri):
    """The cyclic rotation of *tri* that starts with its smallest corner."""
    k = min(range(3), key=lambda i: tri[i])
    return (tri[k], tri[(k + 1) % 3], tri[(k + 2) % 3])


def stripify(triangles: list) -> tuple:
    """Greedy triangle strips over *triangles* (3-tuples of hashable
    corners, decoded-space winding). Returns ``(strips, leftovers)``:
    each strip a vertex sequence for one GX_TRIANGLESTRIP block, the
    leftovers the triangles for a GX_TRIANGLES block. Decoding the result
    gives the input triangles (as cyclic rotations, multiset-equal).

    Every triangle starts a strip from its least connected end: each strip
    begins at the unused triangle with the fewest unused neighbours, tries
    its three rotations and keeps the longest walk. A degenerate triangle
    (a repeated corner) or a triangle whose directed edge is shared by
    another (inconsistent winding) is left to the triangles block.
    """
    plain = []            # triangles that can be stripped
    leftovers = []
    for tri in triangles:
        tri = tuple(tri)
        if len(set(tri)) < 3:
            leftovers.append(tri)
        else:
            plain.append(tri)
    # directed edge -> owning triangle index; an edge owned twice means the
    # mesh folds back on itself there, and neither owner may be reached
    # through that edge.
    owner: dict = {}
    clashed: set = set()
    for index, (a, b, c) in enumerate(plain):
        for edge in ((a, b), (b, c), (c, a)):
            if edge in owner:
                clashed.add(edge)
            else:
                owner[edge] = index
    for edge in clashed:
        del owner[edge]
    third = {}
    for edge, index in owner.items():
        a, b, c = plain[index]
        third[edge] = c if edge == (a, b) else a if edge == (b, c) else b
    # undirected neighbour counts for the start heuristic
    neighbours = [0] * len(plain)
    for (a, b), index in owner.items():
        if (b, a) in owner:
            neighbours[index] += 1
    used = [False] * len(plain)

    def walk(start: int, rotation: int) -> list:
        """Vertex sequence of the strip starting with *start* rotated so
        that its corners are (s2, s1, s0) = rotation of the triangle."""
        a, b, c = plain[start]
        tri = (a, b, c) if rotation == 0 else (b, c, a) if rotation == 1 else (c, a, b)
        # position 0 is even: face [s2, s1, s0] = tri  ->  s0 = tri[2], s1 = tri[1], s2 = tri[0]
        sequence = [tri[2], tri[1], tri[0]]
        taken = [start]
        taken_set = {start}
        i = 1
        while len(sequence) < _MAX_VERTS_PER_BLOCK:
            u, v = sequence[-2], sequence[-1]
            edge = (v, u) if i % 2 == 0 else (u, v)
            index = owner.get(edge)
            if index is None or used[index] or index in taken_set:
                break
            sequence.append(third[edge])
            taken.append(index)
            taken_set.add(index)
            i += 1
        return sequence, taken

    order = sorted(range(len(plain)), key=lambda index: neighbours[index])
    strips = []
    for start in order:
        if used[start]:
            continue
        best = None
        for rotation in range(3):
            candidate = walk(start, rotation)
            if best is None or len(candidate[1]) > len(best[1]):
                best = candidate
        sequence, taken = best
        if len(taken) < _MIN_STRIP_TRIANGLES:
            used[start] = True
            leftovers.append(plain[start])
            continue
        for index in taken:
            used[index] = True
        strips.append(sequence)
    return strips, leftovers


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
