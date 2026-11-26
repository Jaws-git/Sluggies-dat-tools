"""What the custom submesh, rigid rebuild and body rebuild exports share.

bpy-free and at the bottom of the three: CustomSubmeshExport.py,
RigidRebuildExport.py and SkinnedRebuildExport.py import it, it imports none
of them. Each of those keeps what is its own (the canonical custom submesh
formats, the rigid path decision, weights and the interleaved body records)
and re-exports the names below that it used to define, so
``CustomSubmeshExport.quantize_int16`` or ``RigidRebuildExport.route_faces``
still resolve.

Sections:

- fields and index buffers;
- int16 quantization and the position range (the worst-vertex scan, the
  first format that fits, a donor's candidate formats), and the float32
  formats;
- per-loop attributes pooled by value: normals, UVs, colours;
- donor submesh facts and the rebuild reasons;
- face -> surface routing;
- facial pose keys and the texts both facial encoders write;
- in-place fields a rebuild drops, and rebuilds kept from an earlier export.

Geometry arguments are ``CustomSubmeshExport.BoneLocalGeometry`` objects
(``positions``, ``source_vertices``, ``faces``, ``triangles``, ``to_bone``);
triangles are ``CustomSubmeshExport.Triangle`` objects. Nothing here needs
the classes themselves.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:  # inside Blender this module is part of the addon package
    from . import FieldCodec
    from .HostBones import GEO_ID_FREE
except ImportError:  # imported flat by the unit tests
    import FieldCodec
    from HostBones import GEO_ID_FREE


# ---------------------------------------------------------------------------
# Fields and index buffers
# ---------------------------------------------------------------------------

def encode_field(raw, use_compression=True):
    """``x:`` hex string, or a byte list in a --debug file, following the
    .sluggie's UseCompression flag."""
    return FieldCodec.encode_field(raw, use_compression)


def index_buffer(indices, use_compression):
    """A big-endian uint16 index array as a field."""
    return encode_field(struct.pack(f'>{len(indices)}H', *indices), use_compression)


def decode_field(value) -> bytes:
    """The bytes of a field, empty for a missing one."""
    return FieldCodec.decode_field(value) if value else b''


def u16s(raw: bytes) -> Tuple[int, ...]:
    return struct.unpack(f'>{len(raw) // 2}H', raw) if raw else ()


# ---------------------------------------------------------------------------
# Normals: inverse-transpose of the object-to-bone matrix
# ---------------------------------------------------------------------------
# Matrices are 4x4 nested lists indexed [row][column] (see
# CustomSubmeshExport, which holds the rest of the matrix helpers).

SINGULAR_EPSILON = 1e-12


def mat3_determinant(m):
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def normal_matrix(m):
    """Inverse-transpose of *m*'s upper 3x3, for transforming normals.
    Raises ValueError when the 3x3 is singular."""
    det = mat3_determinant(m)
    if abs(det) < SINGULAR_EPSILON:
        raise ValueError('matrix is singular (zero scale on an axis?)')
    # inverse-transpose == cofactor matrix / determinant
    cof = [[0.0] * 3 for _ in range(3)]
    for r in range(3):
        for c in range(3):
            r1, r2 = [i for i in range(3) if i != r]
            c1, c2 = [i for i in range(3) if i != c]
            minor = m[r1][c1] * m[r2][c2] - m[r1][c2] * m[r2][c1]
            cof[r][c] = (-minor if (r + c) % 2 else minor) / det
    return cof


def transform_normals(normals, m, nm=None):
    """Transform normals by *m*'s inverse-transpose and renormalize. A normal
    that collapses to zero length (a degenerate input) comes back as +Z.
    *nm*: ``normal_matrix(m)`` when the caller already has it (a call per vertex)."""
    if nm is None:
        nm = normal_matrix(m)
    result = []
    for n in normals:
        t = [nm[r][0] * n[0] + nm[r][1] * n[1] + nm[r][2] * n[2] for r in range(3)]
        length = (t[0] * t[0] + t[1] * t[1] + t[2] * t[2]) ** 0.5
        result.append((t[0] / length, t[1] / length, t[2] / length) if length > 1e-12 else (0.0, 0.0, 1.0))
    return result


# ---------------------------------------------------------------------------
# int16 quantization and the position range
# ---------------------------------------------------------------------------

# The coarsest s16 position format any export may pick: shift 0, +/-32767.
COARSEST_POSITION_QUANTIZE = 0x30
U16_LIMIT = 0xFFFF


def int16_divisor(quantize_info):
    """Fixed-point divisor for an int16 QuantizeInfo. Float formats never
    occur in the canonical rigid contract and are rejected."""
    if (quantize_info >> 4) in (4, 7, 0xA):
        raise ValueError(f'QuantizeInfo {quantize_info} is a float format; custom submeshes use int16')
    return 1 << (quantize_info & 0xF)


def int16_range(quantize_info):
    """Smallest and largest coordinate representable in *quantize_info*."""
    divisor = int16_divisor(quantize_info)
    return -32768 / divisor, 32767 / divisor


def fits_int16(value, divisor):
    return math.isfinite(value) and -32768 <= round(value * divisor) <= 32767


def quantize_int16(value, divisor, context):
    """Quantize one component to int16, rejecting (never clamping) a value
    that is not finite or doesn't fit. *context* names what is being encoded."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{context}: value is not finite ({value})')
    quantized = round(value * divisor)
    if not -32768 <= quantized <= 32767:
        raise ValueError(
            f'{context}: value {value:.6f} is outside the representable range '
            f'{-32768 / divisor:g}..{32767 / divisor:g}'
        )
    return quantized


def quantize_int16_lazy(value, divisor, make_context):
    """``quantize_int16`` for inner loops: *make_context* is called for the error text only when the value is
    refused (building that text for every component of every vertex was a large part of the loop)."""
    try:
        return quantize_int16(value, divisor, '')
    except ValueError:
        return quantize_int16(value, divisor, make_context())


def quantize_int16_all(values, divisor):
    """Every value of *values* quantized, or None when one is refused: the
    fast path of the encoders, which then run their per-component loop only
    to name the bad component (building that text for every component was a
    large part of the loop)."""
    try:
        return [quantize_int16(value, divisor, '') for value in values]
    except ValueError:
        return None


# Float formats: components are stored as big-endian float32 with no
# fixed-point shift (many stadium meshes: positions 0x40, some UVs 0x40).
# Nibbles 7 and 0xA are four bytes wide too (binfmt._WIDE_VECTOR_FORMATS) but
# occur in no exported model, so only 4 is written.
FLOAT_FORMAT_NIBBLES = (4, 7, 0xA)
FLOAT32_FORMAT_NIBBLE = 4
FLOAT32_MAX = 3.4028234663852886e38


def is_float_format(quantize_info) -> bool:
    """Whether *quantize_info* stores four-byte components (no int16 divisor)."""
    return (int(quantize_info) >> 4) in FLOAT_FORMAT_NIBBLES


def is_float32_format(quantize_info) -> bool:
    """Whether *quantize_info* is the float32 format the rebuilds can write."""
    return (int(quantize_info) >> 4) == FLOAT32_FORMAT_NIBBLE


def float32_value(value, context):
    """*value* as a float a float32 can hold, rejecting (never clamping) one
    that is not finite or too large. *context* names what is being encoded."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{context}: value is not finite ({value})')
    if abs(value) > FLOAT32_MAX:
        raise ValueError(f'{context}: value {value:g} is outside the float32 range')
    return value


def float32_all(values):
    """Every value of *values* as a float32-safe float, or None when one is
    refused (the encoders then walk again to name it)."""
    try:
        return [float32_value(value, '') for value in values]
    except ValueError:
        return None


def encode_float_positions(object_name, geometry) -> bytes:
    """Big-endian float32 position buffer (three components per vertex) for a
    rebuilt donor submesh whose position format is float32. No range to
    outgrow and no quantization step."""
    if len(geometry.positions) > U16_LIMIT:
        raise ValueError(
            f'{object_name}: {len(geometry.positions)} vertices exceed the uint16 index range'
        )
    values = float32_all(value for position in geometry.positions for value in position)
    if values is None:          # a refused component: walk again to name it
        values = []
        for index, position in enumerate(geometry.positions):
            vertex = geometry.source_vertices[index]
            for axis, value in zip('xyz', position):
                values.append(float32_value(value, f'{object_name} vertex {vertex} {axis}'))
    return struct.pack(f'>{len(values)}f', *values)


def position_extent(geometry):
    """``(lowest, highest)`` coordinate of *geometry* over all axes (0.0 is
    always included), or None when a coordinate is not finite."""
    low = high = 0.0
    for position in geometry.positions:
        for value in position:
            if not math.isfinite(value):
                return None
            if value < low:
                low = value
            elif value > high:
                high = value
    return low, high


def _extent_fits(extent, quantize_info):
    # Rounding is monotonic, so the two extremes decide for every coordinate.
    divisor = int16_divisor(quantize_info)
    return extent is not None and fits_int16(extent[0], divisor) and fits_int16(extent[1], divisor)


def position_format_fits(geometry, quantize_info):
    """True when every bone-local coordinate is representable in
    *quantize_info*, using the same rounding as the quantizer."""
    return _extent_fits(position_extent(geometry), quantize_info)


def worst_out_of_range_vertex(geometry, quantize_info) -> Optional[int]:
    """The exported index of the vertex that overshoots the range of
    *quantize_info* most (the first one on a tie), or None when every
    position fits. The callers word the error."""
    divisor = int16_divisor(quantize_info)
    low, high = int16_range(quantize_info)
    worst = None  # (overshoot, exported index)
    for index, position in enumerate(geometry.positions):
        if all(fits_int16(v, divisor) for v in position):
            continue
        overshoot = max(
            max(v - high, low - v) if math.isfinite(v) else math.inf
            for v in position
        )
        if worst is None or overshoot > worst[0]:
            worst = (overshoot, index)
    return None if worst is None else worst[1]


def first_fitting_format(geometry, candidates) -> Optional[int]:
    """The first QuantizeInfo of *candidates* (finest first) that holds every
    position, or None when none does. The positions are walked once, not
    once per candidate."""
    extent = position_extent(geometry)
    for quantize_info in candidates:
        if _extent_fits(extent, quantize_info):
            return quantize_info
    return None


def donor_position_candidates(donor_quantize):
    """The s16 position formats a rebuilt donor submesh may use, finest
    first: the donor's own, then one shift coarser at a time down to 48.
    A mesh that still fits the donor's format keeps it byte for byte; one
    that outgrew it (a stadium prop stretched past +/-4 in format 61) trades
    precision for range instead of being refused."""
    return tuple(range(int(donor_quantize), COARSEST_POSITION_QUANTIZE - 1, -1))


def widened_position_info(object_name, donor_quantize, quantize_info):
    """The INFO line for a rebuilt donor submesh stored coarser than its
    donor, or None when it kept the donor's format."""
    if quantize_info == donor_quantize:
        return None
    _low, high = int16_range(quantize_info)
    return (
        f'{object_name}: positions reach past the original format {donor_quantize} '
        f'(+/-{int16_range(donor_quantize)[1]:.4g}), so they are stored in format {quantize_info} '
        f'(+/-{high:.4g}, steps of 1/{int16_divisor(quantize_info)} unit).'
    )


def check_u16(object_name: str, label: str, count: int) -> None:
    if count > U16_LIMIT:
        raise ValueError(f'{object_name}: {count} distinct {label} exceed the uint16 index range')


# ---------------------------------------------------------------------------
# Per-loop attributes, pooled by value
# ---------------------------------------------------------------------------

WHITE = (1.0, 1.0, 1.0, 1.0)

# Bytes per colour entry by quantize-format nibble (mirrors binfmt.color_entry_size).
COLOR_ENTRY_SIZE = {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}


def dedupe_records(records):
    """Pool identical encoded records, first occurrence first. Returns
    ``(data, indices)``: the concatenated unique records and, per input
    record, its index into them.

    Unlike the donor 3.2/3.3 per-loop edits, a new submesh has no donor slot
    layout to preserve, so shared values are pooled. That keeps u8-indexed
    `rigid:` templates usable for real meshes (a 12-triangle cube has 36
    loops but only 6 distinct normals)."""
    pool = {}
    data = bytearray()
    indices = []
    for record in records:
        index = pool.get(record)
        if index is None:
            index = pool[record] = len(pool)
            data += record
        indices.append(index)
    return bytes(data), indices


def loop_order(geometry):
    """The Blender loop of every exported face corner, face by face."""
    return [loop for tri in geometry.triangles for loop in tri.loops]


def loop_vertices(geometry) -> List[int]:
    """The exported vertex of every loop in ``loop_order`` order."""
    return [vertex for face in geometry.faces for vertex in face]


def encode_loop_normals(object_name, geometry, loop_normals, normal_format, pinned_vertices=None):
    """Per-loop object-local normals to bone-local unit normals, quantized
    and pooled. *loop_normals* is indexed by Blender loop index.

    With *pinned_vertices* (exported indices; a submesh with facial poses),
    the normal entries of those vertices are pooled per vertex: two loops
    share an entry only when they hold the same normal and belong to the
    same pinned vertex, or to no pinned vertex at all. A facial pose then
    gives every entry one delta. That reads ``geometry.faces`` next to the
    triangles; without it (None) only the triangles are read."""
    comp_count, quantize_info = normal_format
    divisor = int16_divisor(quantize_info)
    loops = loop_order(geometry)
    transformed = transform_normals([loop_normals[loop] for loop in loops], geometry.to_bone)
    if pinned_vertices is None:
        pinned, vertices = set(), [None] * len(loops)
    else:
        pinned, vertices = set(pinned_vertices), loop_vertices(geometry)
    pool: Dict[tuple, int] = {}
    data = bytearray()
    indices = []
    for loop, normal, vertex in zip(loops, transformed, vertices):
        values = quantize_int16_all(normal, divisor)
        if values is None:
            values = [quantize_int16(v, divisor, f'{object_name} loop {loop} normal') for v in normal]
        values += [0] * (comp_count - 3)
        record = struct.pack(f'>{comp_count}h', *values)
        key = (record, vertex if vertex in pinned else None)
        index = pool.get(key)
        if index is None:
            index = pool[key] = len(pool)
            data += record
        indices.append(index)
    return bytes(data), indices


def encode_loop_uvs(object_name, geometry, loop_uvs, uv_format):
    """Per-loop Blender UVs to game ST. The importer flips V (``t = 1 - v``),
    so export flips it back, as encode_mesh_hammerspace does. A float32
    channel (some stadium meshes) stores the coordinates as they are."""
    comp_count, quantize_info = uv_format
    records = []
    if is_float32_format(quantize_info):
        padding = [0.0] * (comp_count - 2)
        for loop in loop_order(geometry):
            u, v = loop_uvs[loop]
            s = float32_value(u, f'{object_name} loop {loop} UV u')
            t = float32_value(1.0 - v, f'{object_name} loop {loop} UV v (stored as 1 - v)')
            records.append(struct.pack(f'>{comp_count}f', s, t, *padding))
        return dedupe_records(records)
    divisor = int16_divisor(quantize_info)
    for loop in loop_order(geometry):
        u, v = loop_uvs[loop]
        quantized = quantize_int16_all((u, 1.0 - v), divisor)
        if quantized is not None:
            s, t = quantized
        else:
            s = quantize_int16(u, divisor, f'{object_name} loop {loop} UV u')
            t = quantize_int16(1.0 - v, divisor, f'{object_name} loop {loop} UV v (stored as 1 - v)')
        records.append(struct.pack(f'>{comp_count}h', s, t, *([0] * (comp_count - 2))))
    return dedupe_records(records)


def encode_color_entry(quant_info: int, rgba) -> bytes:
    """One colour entry in a channel's format (the same layout
    ExportInplaceMesh._encode_color_entry writes and ImportDecode decodes)."""
    fmt = quant_info >> 4
    r, g, b, a = (max(0.0, min(1.0, float(c))) for c in rgba)
    if fmt == 0:  # RGB565
        value = ((int(round(r * 31)) & 0x1F) << 11) | ((int(round(g * 63)) & 0x3F) << 5) | (int(round(b * 31)) & 0x1F)
        return value.to_bytes(2, 'big')
    if fmt == 3:  # RGBA4444
        value = ((int(round(r * 15)) & 0xF) << 12) | ((int(round(g * 15)) & 0xF) << 8) \
            | ((int(round(b * 15)) & 0xF) << 4) | (int(round(a * 15)) & 0xF)
        return value.to_bytes(2, 'big')
    if fmt in (1, 4):  # RGB8
        return bytes((int(round(r * 255)), int(round(g * 255)), int(round(b * 255))))
    if fmt in (2, 5):  # RGBA8
        return bytes((int(round(r * 255)), int(round(g * 255)), int(round(b * 255)), int(round(a * 255))))
    raise ValueError(f'unsupported color channel format {fmt} (quantizeInfo=0x{quant_info:X})')


def is_unset_color(rgba) -> bool:
    """Exactly (0, 0, 0, 0): black with alpha 0. Blender fills a colour
    attribute with it on the corners of geometry joined from an object that
    had no such attribute, and the game draws those corners invisible
    (vertex alpha 0; Mario body, 2026-10-09), so the exporters write white."""
    return all(float(c) <= 0.0 for c in rgba)


def fill_unset_colors(object_name, colors, infos=None):
    """*colors* with every unset corner (:func:`is_unset_color`) replaced by
    white, reporting the count to *infos* when any was replaced."""
    filled, replaced = [], 0
    for color in colors:
        if is_unset_color(color):
            filled.append(WHITE)
            replaced += 1
        else:
            filled.append(color)
    if replaced and infos is not None:
        infos.append(
            f'{object_name}: {replaced} face corner(s) had the vertex colour (0, 0, 0, 0), which '
            "Blender gives geometry joined from an object without a 'color0' attribute and which "
            'the game draws invisible; they were exported as opaque white.'
        )
    return filled


def encode_loop_colors(geometry, loop_colors, quant_info, object_name='', infos=None):
    """Per-loop colours pooled in the format *quant_info*. *loop_colors* is
    indexed by Blender loop index, or None for a mesh without colours (every
    loop white, one pooled entry). Unset corners are written white
    (:func:`fill_unset_colors`)."""
    loops = loop_order(geometry)
    colors = [WHITE] * len(loops) if loop_colors is None else [loop_colors[loop] for loop in loops]
    colors = fill_unset_colors(object_name, colors, infos)
    return dedupe_records([encode_color_entry(quant_info, c) for c in colors])


def encode_color_channel(
    object_name: str, color_format, geometry, loop_colors, use_compression: bool, infos: Optional[List[str]] = None,
) -> dict:
    """``ColorChannelData`` / ``ColorFacesData`` for a rebuilt donor submesh:
    per-loop colours pooled in the donor channel's format (white when the
    mesh has no colour attribute; unset (0, 0, 0, 0) corners written white,
    see :func:`fill_unset_colors`). Shared by the rigid and skinned rebuilds."""
    color_data, color_indices = encode_loop_colors(geometry, loop_colors, color_format[1], object_name, infos)
    check_u16(object_name, 'colors', len(color_data) // COLOR_ENTRY_SIZE[color_format[1] >> 4])
    return {
        'ColorChannelData': encode_field(color_data, use_compression),
        'ColorFacesData': index_buffer(color_indices, use_compression),
    }


def encode_uv_channels(
    object_name: str, uv_formats: Dict[int, Tuple[int, int]], uv_mirrored: bool, geometry,
    loop_uvs_by_channel: Dict[int, Optional[Sequence[Tuple[float, float]]]], use_compression: bool,
    warnings: List[str], infos: List[str],
) -> list:
    """The ``UVChannels`` list of a rebuilt donor submesh, one entry per donor
    channel in its own format. Channel 1 mirrors channel 0 when the donor
    channels are identical or when its Blender layer is missing.
    Shared by the rigid and skinned rebuilds."""
    encoded_uvs: Dict[int, Tuple[bytes, list]] = {}
    channels = sorted(uv_formats)
    for channel in channels:
        loop_uvs = loop_uvs_by_channel.get(channel)
        mirror_source = None
        if uv_mirrored and channel == 1:
            mirror_source = 0
            if loop_uvs is not None and loop_uvs_by_channel.get(0) is not None \
                    and list(loop_uvs) != list(loop_uvs_by_channel[0]):
                infos.append(
                    f'{object_name}: UV channel 1 mirrors channel 0 in the donor (specular), so its '
                    'separate edits are ignored and channel 0 is written to both.'
                )
        elif loop_uvs is None:
            if loop_uvs_by_channel.get(0) is None:
                raise ValueError(f'{object_name}: UV map for channel {channel} not found')
            mirror_source = 0
            warnings.append(
                f'{object_name}: UV map for channel {channel} not found; channel 0 is written to it.'
            )
        if mirror_source is not None:
            uv_data, uv_indices = encoded_uvs[mirror_source]
            if uv_formats[channel] != uv_formats[mirror_source]:
                uv_data, uv_indices = encode_loop_uvs(
                    object_name, geometry, loop_uvs_by_channel[mirror_source], uv_formats[channel],
                )
        else:
            uv_data, uv_indices = encode_loop_uvs(object_name, geometry, loop_uvs, uv_formats[channel])
        check_u16(object_name, f'UVs on channel {channel}', len(uv_indices) and max(uv_indices) + 1)
        encoded_uvs[channel] = (uv_data, uv_indices)
    return [
        {
            'UVChannelIndex': channel,
            'UVChannelData': encode_field(encoded_uvs[channel][0], use_compression),
            'UVFacesData': index_buffer(encoded_uvs[channel][1], use_compression),
        }
        for channel in channels
    ]


# ---------------------------------------------------------------------------
# Donor facts
# ---------------------------------------------------------------------------

@dataclass
class DonorSubmesh:
    """What any rebuild needs to know about its donor submesh. The rigid and
    the skinned donor classes derive from it."""
    index: int
    position_quantize: int
    normal_format: Optional[Tuple[int, int]]        # (CompCount, QuantizeInfo) or None
    color_format: Optional[Tuple[int, int]]         # (CompCount, QuantizeInfo) or None
    uv_formats: Dict[int, Tuple[int, int]]          # channel -> (CompCount, QuantizeInfo)
    uv_mirrored: bool                               # channel 1 is a byte copy of channel 0
    faces: List[Tuple[int, int, int]]
    face_surfaces: List[str]                        # imported SurfaceId per donor face
    drawable_surfaces: Dict[str, int]               # SurfaceId -> state index, can draw
    facial: bool                                    # a ptr7 facial object references it

    @property
    def owner_key(self) -> str:
        return f'sm{self.index}'


def facial_pose_submeshes(model: dict) -> set:
    objects = (model.get('FacialPoseData') or {}).get('Objects') or []
    return {
        int(entry['SubmeshIndex']) for entry in objects
        if isinstance(entry, dict) and entry.get('SubmeshIndex') is not None
    }


def _effective(states: Sequence[dict], upto: int, state_id: int) -> Optional[dict]:
    found = None
    for state in states[:upto + 1]:
        if int(state.get('DisplayStateId', -1)) == state_id:
            found = state
    return found


def donor_submesh_facts(index: int, sub: dict) -> dict:
    """What any rebuild needs to know about a donor submesh: attribute
    formats, the donor faces with their imported surfaces, and the surfaces
    that can draw. Shared by the rigid and skinned rebuilds."""
    vb = sub['VertexBuffer']
    normals = sub.get('NormalBuffer') if isinstance(sub.get('NormalBuffer'), dict) else None
    normal_format = None
    if normals and normals.get('NormalBufferData'):
        normal_format = (int(normals.get('NormalBufferCompCount', 3)), int(normals['NormalBufferQuantizeInfo']))
    colors = sub.get('ColorChannels') or []
    color_format = None
    if colors:
        color_format = (int(colors[0].get('ColorChannelCompCount', 4)), int(colors[0]['ColorChannelQuantizeInfo']))
    uv_channels = sub.get('UVChannels') or []
    uv_formats = {
        int(uv['UVChannelIndex']): (int(uv.get('UVChannelCompCount', 2)), int(uv['UVChannelQuantizeInfo']))
        for uv in uv_channels
    }
    by_index = {int(uv['UVChannelIndex']): uv for uv in uv_channels}
    uv_mirrored = (
        set(by_index) == {0, 1}
        and decode_field(by_index[0].get('UVChannelData')) == decode_field(by_index[1].get('UVChannelData'))
        and decode_field(by_index[0].get('UVFacesData')) == decode_field(by_index[1].get('UVFacesData'))
    )
    flat = u16s(decode_field(sub.get('FacesData')))
    faces = [tuple(flat[i:i + 3]) for i in range(0, len(flat) - len(flat) % 3, 3)]
    states = sub.get('DisplayStates') or []
    face_surfaces: List[str] = []
    for k, state in enumerate(states):
        count = state.get('FaceCount')
        if count is None:
            face_surfaces = []
            break
        face_surfaces += [state.get('SurfaceId') or f'sm{index}_ds{k}'] * int(count)
    if len(face_surfaces) != len(faces):
        face_surfaces = []
    # Type 7 is only required where the submesh uses it: item and stadium
    # submeshes often have none, and their surfaces still draw. A record that
    # draws donor faces before the submesh's first Type 7 draws with the one
    # the previous mesh left (stadiums, Daisy Cruiser sta08), so it draws too.
    needs_type7 = any(int(state.get('DisplayStateId', -1)) == 7 for state in states)
    drawable = {
        state['SurfaceId']: k for k, state in enumerate(states)
        if state.get('SurfaceId')
        and _effective(states, k, 3) is not None
        and (not needs_type7 or _effective(states, k, 7) is not None
             or int(state.get('FaceCount') or 0) > 0)
    }
    return dict(
        index=index,
        position_quantize=int(vb['VertexBufferQuantizeInfo']),
        normal_format=normal_format,
        color_format=color_format,
        uv_formats=uv_formats,
        uv_mirrored=uv_mirrored,
        faces=faces,
        face_surfaces=face_surfaces,
        drawable_surfaces=drawable,
    )


def donor_position_format_error(
    donor: DonorSubmesh, object_name: str, rebuild_name: str, float_positions: bool = False,
) -> Optional[str]:
    """Int16 donor positions can be rebuilt, and float32 ones where the path
    writes them (*float_positions*: the rigid rebuild; the body's interleaved
    records are int16 only). *rebuild_name* is how the text calls the path
    (``rigid rebuild``, ``body rebuild``)."""
    if (donor.position_quantize >> 4) == 3:
        return None
    if float_positions and is_float32_format(donor.position_quantize):
        return None
    return (
        f'{object_name}: this submesh stores positions in format {donor.position_quantize}, '
        f'which the {rebuild_name} does not support; keep its topology, '
        'transform and surfaces unchanged.'
    )


# ---------------------------------------------------------------------------
# Rebuild reasons
# ---------------------------------------------------------------------------

REASON_TOPOLOGY = 'topology'
REASON_TRANSFORM = 'object_transform'
REASON_SURFACES = 'surfaces'
REASON_POSITION_RANGE = 'position_range'   # positions outgrew the donor's s16 format


def rebuild_reasons(
    *, topology_changed: bool, moved_reason: Optional[str], surfaces_changed: bool,
    own_reasons: Sequence[str] = (), out_of_range: bool = False,
) -> List[str]:
    """The ``Reason`` list of a rebuild entry, in its fixed order: topology,
    *moved_reason* (how this path names a move, or None when nothing moved),
    surfaces, the path's *own_reasons*, the position range."""
    reasons = []
    if topology_changed:
        reasons.append(REASON_TOPOLOGY)
    if moved_reason:
        reasons.append(moved_reason)
    if surfaces_changed:
        reasons.append(REASON_SURFACES)
    reasons.extend(own_reasons)
    if out_of_range:
        reasons.append(REASON_POSITION_RANGE)
    return reasons


# ---------------------------------------------------------------------------
# Face -> surface routing (slot hygiene)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SlotSurface:
    """What one material slot says about its surface."""
    material_name: str
    surface_id: Optional[str]          # donor SurfaceId or new surface key; None = no metadata
    new_surface: bool = False
    owner: Optional[str] = None        # SluggiesSurfaceOwner of a new surface


def new_surface_key_matches(owner_key: str, surface_id: str) -> bool:
    """Whether *surface_id* is an Add-material key of *owner_key*:
    ``sm<N>_new<K>`` on a donor submesh, ``<CustomSubmeshId>_new<K>`` on a
    custom submesh."""
    return re.match(rf'^{re.escape(owner_key)}_new\d+$', surface_id or '') is not None


@dataclass
class SurfaceRouting:
    table: List[str]
    indices: List[int]                 # per exported triangle
    new_keys: List[str]                # new surface keys in table order
    changed: bool                      # any face off its imported surface, or a new surface in use
    counts_before: Dict[str, int]
    counts_after: Dict[str, int]
    warnings: List[str] = field(default_factory=list)


def route_faces(
    object_name: str,
    donor,
    triangles: Sequence,
    polygon_slots: Sequence[int],
    slots: Sequence[Optional[SlotSurface]],
    topology_changed: bool,
) -> SurfaceRouting:
    """Decide which surface each exported triangle draws through, from its
    polygon's material slot.

    *donor* is anything with ``owner_key``, ``drawable_surfaces`` and
    ``face_surfaces``: a :class:`DonorSubmesh`, or a custom submesh's
    ``CustomSubmeshExport.CustomSubmeshSurfaces``.

    - A face on an empty slot, on a material without surface metadata, or on
      another submesh's surface cancels the export (names object, material
      and face count).
    - Two slots holding the same surface merge, with a warning.
    - ``changed`` compares against the imported surface of the triangle's
      polygon when the topology is unchanged; with a changed topology the
      rebuild is already decided, and the surfaces are simply what the
      materials say.
    """
    warnings: List[str] = []
    slot_keys: List[Optional[str]] = []     # surface key per slot; None = unusable
    slot_problems: Dict[int, str] = {}      # why a slot is unusable
    seen: Dict[str, int] = {}
    for slot_index, slot in enumerate(slots):
        key = None
        if slot is None or not slot.surface_id:
            slot_problems[slot_index] = (
                f"material '{slot.material_name}' has no Sluggies SurfaceId" if slot is not None
                else 'an empty material slot'
            )
        elif slot.new_surface:
            if slot.owner != donor.owner_key:
                slot_problems[slot_index] = (
                    f"material '{slot.material_name}' belongs to {slot.owner or 'another submesh'}, "
                    f'not {donor.owner_key}'
                )
            elif not new_surface_key_matches(donor.owner_key, slot.surface_id):
                slot_problems[slot_index] = (
                    f"material '{slot.material_name}' has an invalid new-surface key {slot.surface_id!r}"
                )
            else:
                key = slot.surface_id
        elif slot.surface_id in donor.drawable_surfaces:
            key = slot.surface_id
        else:
            slot_problems[slot_index] = (
                f"material '{slot.material_name}' ({slot.surface_id}) is not a surface of this submesh"
            )
        if key is not None:
            if key in seen:
                warnings.append(
                    f"{object_name}: material slots {seen[key]} and {slot_index} both draw surface "
                    f'{key}; their faces are merged into one surface.'
                )
            else:
                seen[key] = slot_index
        slot_keys.append(key)

    table: List[str] = []
    table_index: Dict[str, int] = {}
    indices: List[int] = []
    problem_faces: Dict[int, int] = {}
    for tri in triangles:
        slot_index = polygon_slots[tri.polygon_index] if tri.polygon_index < len(polygon_slots) else -1
        key = slot_keys[slot_index] if 0 <= slot_index < len(slot_keys) else None
        if key is None:
            problem_faces[slot_index] = problem_faces.get(slot_index, 0) + 1
            continue
        if key not in table_index:
            table_index[key] = len(table)
            table.append(key)
        indices.append(table_index[key])
    errors = [
        f"{slot_problems.get(slot_index, 'an empty material slot')} ({count} face(s))"
        for slot_index, count in sorted(problem_faces.items())
    ]
    if errors:
        raise ValueError(
            f"{object_name}: every face must use one of this submesh's own materials "
            f"(imported, or added with Add material): " + '; '.join(errors)
        )

    new_keys = [key for key in table if key not in donor.drawable_surfaces]
    changed = bool(new_keys)
    if not changed and not topology_changed and donor.face_surfaces:
        for tri, index in zip(triangles, indices):
            if tri.polygon_index >= len(donor.face_surfaces) or donor.face_surfaces[tri.polygon_index] != table[index]:
                changed = True
                break
    counts_before: Dict[str, int] = {}
    for surface in donor.face_surfaces:
        counts_before[surface] = counts_before.get(surface, 0) + 1
    counts_after: Dict[str, int] = {}
    for index in indices:
        counts_after[table[index]] = counts_after.get(table[index], 0) + 1
    return SurfaceRouting(table, indices, new_keys, changed, counts_before, counts_after, warnings)


def surface_report(object_name: str, routing: SurfaceRouting) -> str:
    keys = list(dict.fromkeys(list(routing.counts_before) + routing.table))
    parts = [f'{key} {routing.counts_before.get(key, 0)} -> {routing.counts_after.get(key, 0)}' for key in keys]
    return f'{object_name}: faces per surface ' + ', '.join(parts)


# ---------------------------------------------------------------------------
# Facial poses (ptr7) on a rebuilt submesh
# ---------------------------------------------------------------------------
# The importer names a facial object's shape keys FACIAL_KEY_NAME, and the
# exporter reads them back as FacialObjectKeys. The rigid and the body
# encoder differ in their records (separate position and normal arrays
# against one six-component record) and keep their own loops; the texts
# they write are here.

FACIAL_KEY_NAME = 'facial_object_{object}_pose_{pose}'


@dataclass
class FacialObjectKeys:
    """One facial object's shape keys as plain data, per Blender vertex:
    ``pose_positions[k]`` / ``pose_normals[k]`` belong to pose ``k + 1``
    (None when the key is missing or normals are unavailable); the Basis
    key gives ``basis_positions`` / ``basis_normals``."""
    object_index: int
    pose_count: int
    pose_positions: list
    pose_normals: list
    basis_positions: list
    basis_normals: Optional[list] = None


def facial_warning(object_name: str, donor, shape_key_count: int, carried: bool = False) -> Optional[str]:
    """The rebuild renumbers the vertices, so the donor's facial poses on
    this submesh can only follow through the mesh's shape keys
    (``FacialPoses``); without them the patcher drops (body) or neutralizes
    (rigid submesh) the poses."""
    if not donor.facial or carried:
        return None
    if shape_key_count:
        return f'{object_name}: Shapekeys without a Basis key - Model has no facial expressions'
    return f'{object_name}: No shapekeys found - Model has no facial expressions'


def facial_missing_poses_warning(object_name: str, facial_object: FacialObjectKeys) -> Optional[str]:
    """The warning for poses of *facial_object* that have no shape key (they
    keep the rest shape), or None when every pose has one."""
    missing = [
        pose for pose, positions in zip(range(1, facial_object.pose_count), facial_object.pose_positions)
        if positions is None
    ]
    if not missing:
        return None
    return (
        f'{object_name}: facial object {facial_object.object_index} has no shape key for pose(s) '
        f"{', '.join(str(pose) for pose in missing)}; those poses keep the rest shape."
    )


def facial_unmoving_warning(object_name: str, facial_object: FacialObjectKeys) -> str:
    """The warning for a facial object whose shape keys move no vertex."""
    return (
        f'{object_name}: facial object {facial_object.object_index} moves no vertex in any '
        'shape key; it is kept with one unmoving entry.'
    )


def facial_animated_info(
    object_name: str, facial_object: FacialObjectKeys, vertex_count: int, normal_note: str = '',
) -> str:
    """The info line of one encoded facial object."""
    return (
        f'{object_name}: facial object {facial_object.object_index}: {vertex_count} vertex(es){normal_note} '
        f'animated over {facial_object.pose_count - 1} pose(s).'
    )


# ---------------------------------------------------------------------------
# In-place fields a rebuild drops, and rebuilds kept from an earlier export
# ---------------------------------------------------------------------------

# Every in-place edit field a rebuilt submesh must not carry
# (the patcher refuses the mix).
IN_PLACE_SUBMESH_FIELDS = ('FacesDataEdited', 'FacesCountEdited', 'FaceTextureIndicesEdited', 'FaceSurfaceIdsEdited')
IN_PLACE_CHANNEL_FIELDS = {
    'VertexBuffer': ('VertexBufferDataEdited',),
    'NormalBuffer': ('NormalBufferDataEdited', 'NormalFacesDataEdited'),
    'UVChannels': ('UVChannelDataEdited', 'UVFacesDataEdited'),
    'ColorChannels': ('ColorChannelDataEdited', 'ColorFacesDataEdited'),
}


def strip_in_place_edits(sub: dict) -> None:
    """Remove every in-place edit field from a submesh dict, in place."""
    for key in IN_PLACE_SUBMESH_FIELDS:
        sub.pop(key, None)
    for container, keys in IN_PLACE_CHANNEL_FIELDS.items():
        entries = sub.get(container)
        if isinstance(entries, dict):
            entries = [entries]
        for entry in entries or []:
            for key in keys:
                entry.pop(key, None)


def bone_geo_id_raw(bone: dict) -> int:
    """Literal ACT GeoId of a BoneHierarchy entry (GEO_ID_FREE when mesh-free)."""
    if bone.get('GeoIdRaw') is not None:
        return int(bone['GeoIdRaw'])
    if bone.get('Skinned'):
        return GEO_ID_FREE
    return int(bone.get('GeoId', GEO_ID_FREE))


@dataclass
class CarriedRebuilds:
    """What :func:`carry_unselected_rebuilds` decided."""
    additions: List[dict]       # AdditionalTextureDescriptors entries to keep
    messages: List[str]         # one info line per kept rebuild
    errors: List[str]           # conflicts that must stop the export


def carry_unselected_rebuilds(
    model: dict,
    exported_indices: Iterable[int],
    previous_additions: Sequence[dict],
    recover_addition=None,
) -> CarriedRebuilds:
    """Keep a ``RigidRebuild`` from an earlier export consistent when its
    mesh is not part of this export.

    Edits on unselected submeshes stay in the ``.sluggie`` (that is why
    ``ExportMode.stale_hammerspace_submeshes`` forces Hammerspace for them),
    but two model-level facts a rebuild depends on are re-derived from the
    selection on every export: ``BoneHierarchy[].GeoIdEdited`` and
    ``AdditionalTextureDescriptors``. Without this, a cap rebuilt onto
    another bone with a new-PNG surface lost both, and the patcher refused
    the file (host bone mismatch, unknown texture). Call after this
    export's ``GeoIdEdited`` and custom submeshes are final.

    - The rebuild's host bone move is written again as ``GeoIdEdited`` (old
      owner free, host = the submesh), unless this export already gave
      either bone to another mesh: then it is an error that names the mesh.
    - Each ``AdditionalTextureFileName`` its new surfaces bind is kept from
      *previous_additions*. One missing there (an export from before this
      fix dropped it) is rebuilt by ``recover_addition(file_name,
      template_source)`` when given, which returns the
      ``TemplateTextureIndex`` or None when the PNG is gone; otherwise it is
      an error.
    """
    exported = set(exported_indices)
    bones = model.get('BoneHierarchy') or []
    bone_by_id = {int(b['BoneId']): b for b in bones if 'BoneId' in b}
    custom_hosts = {
        int(cs['HostBoneId']): str(cs.get('CustomSubmeshId', '?'))
        for cs in model.get('CustomSubmeshes') or [] if cs.get('HostBoneId') is not None
    }
    previous_by_name = {a.get('TextureFileName'): a for a in previous_additions or []}
    result = CarriedRebuilds([], [], [])

    def effective(bone: dict) -> int:
        edited = bone.get('GeoIdEdited')
        return int(edited) if edited is not None else bone_geo_id_raw(bone)

    for index, sub in enumerate(model.get('Submeshes') or []):
        rebuild = sub.get('RigidRebuild') or sub.get('SkinnedRebuild')
        if not rebuild or index in exported:
            continue
        name = sub.get('MeshName') or f'submesh {index}'
        label = f"{name} (submesh {index}, not selected)"
        fix = 'select it and export again, or re-import the model to drop the earlier edit'
        host = int(rebuild.get('HostBoneId', -1))
        owners = [bone_id for bone_id, bone in bone_by_id.items() if bone_geo_id_raw(bone) == index]
        rigid = 'HostBoneId' in rebuild            # a rebuilt body stays on the skeleton
        if rigid and host not in bone_by_id:
            result.errors.append(f'{label}: its earlier rebuild hosts it on unknown bone {host}; {fix}.')
            continue
        if rigid and host in custom_hosts:
            result.errors.append(
                f"{label}: its earlier rebuild moved it to bone_{host}, which this export gives to "
                f"custom submesh '{custom_hosts[host]}'; {fix}.")
            continue
        if rigid and host not in owners:
            current = effective(bone_by_id[host])
            if current not in (GEO_ID_FREE, index):
                result.errors.append(
                    f'{label}: its earlier rebuild moved it to bone_{host}, which this export gives to '
                    f'submesh {current}; {fix}.')
                continue
            taken = [o for o in owners if effective(bone_by_id[o]) not in (GEO_ID_FREE, index)]
            if taken:
                result.errors.append(
                    f'{label}: its original bone_{taken[0]} now carries submesh '
                    f'{effective(bone_by_id[taken[0]])}; {fix}.')
                continue
            for owner in owners:
                bone_by_id[owner]['GeoIdEdited'] = GEO_ID_FREE
            bone_by_id[host]['GeoIdEdited'] = index

        missing, kept = [], []
        for surface in rebuild.get('NewSurfaces') or []:
            file_name = (surface.get('TextureAssignment') or {}).get('AdditionalTextureFileName')
            if not file_name:
                continue
            addition = previous_by_name.get(file_name)
            if addition is None and recover_addition is not None:
                template_index = recover_addition(file_name, str(surface.get('TemplateSource') or ''))
                if template_index is not None:
                    addition = {'TextureFileName': file_name, 'TemplateTextureIndex': int(template_index)}
                    previous_by_name[file_name] = addition
            if addition is None:
                missing.append(file_name)
                continue
            kept.append(file_name)
            if all(a.get('TextureFileName') != file_name for a in result.additions):
                result.additions.append(dict(addition))
        if missing:
            result.errors.append(
                f"{label}: its earlier rebuild binds {', '.join(missing)}, which the file no longer "
                f'lists; {fix}.')
            continue
        result.messages.append(
            f'{label}: kept its earlier '
            + (f'rigid rebuild (bone_{host}' if rigid else 'body rebuild (')
            + (f", textures {', '.join(kept)}" if kept else '')
            + '). Select it to change it.')
    return result
