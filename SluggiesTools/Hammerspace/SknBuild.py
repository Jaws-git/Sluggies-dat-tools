"""The SKN (skinning data) section of a hammerspace build.

``BuildSKNSkinningData`` serializes the parsed skinning structures; ``_scale_skn_bind_pose`` applies a
root-bone scale to a built or cloned section.
"""

import struct

import slogger as _slogger
from binfmt import comp_size as _vb_comp_size
from ModelFormat import align_array_offset, compute_mem_clear_range, pad_array
from InplacePatcher import root_scale as _root_scale

from SluggieModel import SkinningData, SluggieParsed


def _source_blob_for_skn(entry: object, vertex_stride: int) -> bytes:
    blob = entry.bind_pose_data
    expected_len = getattr(entry, 'vertex_offset', 0) + entry.vertex_cnt * vertex_stride
    if len(blob) < expected_len:
        blob = b'\x00' * (expected_len - len(blob)) + blob
    return blob


def _scale_skn_bind_pose(skn_bytes: bytes, factors: tuple[float, float, float] | None) -> bytes:
    """Scale the bind-pose vertex positions in a SKN section by *factors*.

    Parses the SKN header and each SK1/SK2/SKAcc struct to locate the bind-pose
    source arrays, then rescales their position components via
    ``root_scale.scale_bind_pose_data`` (normals and the per-array prefix are
    copied through unchanged). Works on both freshly-built and verbatim-cloned
    SKN sections because both share the same header/struct layout:

        header (0x24): SK1Cnt, SK2Cnt, SKAccCnt, quantizeInfo, SK1Ptr, SK2Ptr,
                       SKAccPtr, memClrPtr, memClrSze, flushIndArr, flushIndSze
        SK1  (0x40):   vertexArr @ +0x30, vertexCnt @ +0x3a, vertexOffset @ +0x3c
        SK2  (0x74):   vertexArr @ +0x60, vertexCnt @ +0x70, vertexOffset @ +0x72
        SKAcc(0x44):   vertexArr @ +0x30, vertexCnt @ +0x42 (no vertex offset)

    All pointer fields are relative to the SKN section start (byte 0 of
    *skn_bytes*). Returns *skn_bytes* unchanged when *factors* is None or a
    no-op scale, or when the section is too short to hold a header.
    """
    if not skn_bytes or len(skn_bytes) < 0x24:
        return skn_bytes
    if factors is None or factors == (1.0, 1.0, 1.0):
        return skn_bytes
    n_sk1 = struct.unpack_from('>H', skn_bytes, 0x00)[0]
    n_sk2 = struct.unpack_from('>H', skn_bytes, 0x02)[0]
    n_acc = struct.unpack_from('>H', skn_bytes, 0x04)[0]
    quantize_info = struct.unpack_from('B', skn_bytes, 0x06)[0]
    sk1_ptr = struct.unpack_from('>I', skn_bytes, 0x08)[0]
    sk2_ptr = struct.unpack_from('>I', skn_bytes, 0x0c)[0]
    skacc_ptr = struct.unpack_from('>I', skn_bytes, 0x10)[0]
    stride = 6 * _vb_comp_size(quantize_info)
    out = bytearray(skn_bytes)

    def _scale_at(arr_rel: int, vertex_cnt: int, vertex_offset: int) -> None:
        if vertex_cnt <= 0 or vertex_offset < 0:
            return
        start = arr_rel + vertex_offset
        end = min(start + vertex_cnt * stride, len(out))
        if end <= start:
            return
        blob = bytes(out[start:end])
        out[start:end] = _root_scale.scale_bind_pose_data(
            blob, quantize_info, vertex_cnt, 0, factors
        )

    for i in range(n_sk1):
        b = sk1_ptr + i * 0x40
        if b + 0x40 > len(skn_bytes):
            break
        arr = struct.unpack_from('>I', skn_bytes, b + 0x30)[0]
        cnt = struct.unpack_from('>H', skn_bytes, b + 0x3a)[0]
        off = struct.unpack_from('B', skn_bytes, b + 0x3c)[0]
        _scale_at(arr, cnt, off)
    for i in range(n_sk2):
        b = sk2_ptr + i * 0x74
        if b + 0x74 > len(skn_bytes):
            break
        arr = struct.unpack_from('>I', skn_bytes, b + 0x60)[0]
        cnt = struct.unpack_from('>H', skn_bytes, b + 0x70)[0]
        off = struct.unpack_from('B', skn_bytes, b + 0x72)[0]
        _scale_at(arr, cnt, off)
    for i in range(n_acc):
        b = skacc_ptr + i * 0x44
        if b + 0x44 > len(skn_bytes):
            break
        arr = struct.unpack_from('>I', skn_bytes, b + 0x30)[0]
        cnt = struct.unpack_from('>H', skn_bytes, b + 0x42)[0]
        _scale_at(arr, cnt, 0)
    return bytes(out)


def _mirrored_skn_source_layout(skn: SkinningData, vertex_stride: int) -> list[tuple[int, bytes]] | None:
    """Return [(gplVertexArr, source blob)] when SK1/SK2 sources can mirror the
    position buffer: every destination on a cache-line boundary and no two
    source blobs overlapping. Otherwise None."""
    layout = sorted(
        ((sk.gpl_vertex_arr_value, _source_blob_for_skn(sk, vertex_stride))
         for sk in (*skn.sk1s, *skn.sk2s)),
        key=lambda item: item[0],
    )
    if not layout or any(gva % 32 for gva, _ in layout):
        return None
    for (gva, blob), (next_gva, _) in zip(layout, layout[1:]):
        if gva + len(blob) > next_gva:
            return None
    return layout


def _layout_skn_variable_data(skn: SkinningData, vertex_stride: int, var_data_offset: int) -> tuple[bytearray, list[int], list[int], list[int], list[int], list[int], list[int], bytes, int]:
    var_data = bytearray()
    var_cursor = var_data_offset

    def _source_relative(absolute_ptr: int) -> int:
        return absolute_ptr - skn.skn_offset if absolute_ptr else 0

    source_layout = []
    if skn.preserve_source_layout and skn.skn_offset:
        source_layout.extend(
            (_source_relative(entry.vertex_arr_absolute_ptr), _source_blob_for_skn(entry, vertex_stride))
            for entry in skn.sk1s
        )
        source_layout.extend(
            (_source_relative(entry.vertex_arr_absolute_ptr), _source_blob_for_skn(entry, vertex_stride))
            for entry in skn.sk2s
        )
        if skn.flush_ind_size and skn.flush_ind_data:
            source_layout.append(
                (_source_relative(skn.flush_ind_absolute_ptr or 0), skn.flush_ind_data)
            )
        source_layout.extend(
            (_source_relative(entry.weight_arr_absolute_ptr), entry.weight_data)
            for entry in skn.sk2s
        )
        for entry in skn.sk_accs:
            source_layout.extend((
                (_source_relative(entry.vertex_arr_absolute_ptr), entry.bind_pose_data),
                (_source_relative(entry.dest_arr_absolute_ptr), entry.dest_index_data),
                (_source_relative(entry.weight_arr_absolute_ptr), entry.weight_data),
            ))

    source_layout_valid = bool(source_layout) and all(
        offset >= var_data_offset and offset % 32 == 0
        for offset, _ in source_layout
    )
    if source_layout_valid:
        ordered = sorted(source_layout)
        source_layout_valid = all(
            offset + len(blob) <= next_offset
            for (offset, blob), (next_offset, _) in zip(ordered, ordered[1:])
        )

    if source_layout_valid:
        layout_end = align_array_offset(
            max(offset + len(blob) for offset, blob in source_layout), 'skn_source')
        var_data = bytearray(layout_end - var_data_offset)
        for offset, blob in source_layout:
            start = offset - var_data_offset
            var_data[start:start + len(blob)] = blob

        sk1_src_off = [_source_relative(entry.vertex_arr_absolute_ptr) for entry in skn.sk1s]
        sk2_src_off = [_source_relative(entry.vertex_arr_absolute_ptr) for entry in skn.sk2s]
        sk2_wt_off = [_source_relative(entry.weight_arr_absolute_ptr) for entry in skn.sk2s]
        acc_src_off = [_source_relative(entry.vertex_arr_absolute_ptr) for entry in skn.sk_accs]
        acc_dest_off = [_source_relative(entry.dest_arr_absolute_ptr) for entry in skn.sk_accs]
        acc_wt_off = [_source_relative(entry.weight_arr_absolute_ptr) for entry in skn.sk_accs]
        flush_bytes = skn.flush_ind_data
        flush_off = _source_relative(skn.flush_ind_absolute_ptr or 0) if flush_bytes else 0
        var_cursor = layout_end
    else:
        mirror_layout = _mirrored_skn_source_layout(skn, vertex_stride)
        if mirror_layout is not None:
            # Donor rule (361/361 skinned models): SK1/SK2 source arrays mirror
            # the skinned position buffer, src = var_data_offset + gplVertexArr.
            # The runtime relies on it beyond the per-entry pointers: facial
            # poses on skinned vertices were written 0x20 bytes off (Luigi
            # neck stretched to the floor) once packing dropped donor gap lines.
            mirror_end = max(gva + len(blob) for gva, blob in mirror_layout)
            var_data = bytearray(align_array_offset(mirror_end, 'skn_source'))
            for gva, blob in mirror_layout:
                var_data[gva:gva + len(blob)] = blob
            sk1_src_off = [var_data_offset + sk.gpl_vertex_arr_value for sk in skn.sk1s]
            sk2_src_off = [var_data_offset + sk.gpl_vertex_arr_value for sk in skn.sk2s]
            var_cursor += len(var_data)
        else:
            _slogger.warning(
                '[SKN] SK1/SK2 destinations are not cache-line exclusive; source arrays '
                'packed sequentially instead of mirroring the position buffer (donor '
                'layout rule) — facial poses on skinned vertices may break in-game',
                source='hammerspace.main',
            )
            sk1_src_off = []
            for sk in skn.sk1s:
                sk1_src_off.append(var_cursor)
                chunk = pad_array(_source_blob_for_skn(sk, vertex_stride), 'skn_source')
                var_data.extend(chunk)
                var_cursor += len(chunk)

            sk2_src_off = []
            for sk in skn.sk2s:
                sk2_src_off.append(var_cursor)
                chunk = pad_array(_source_blob_for_skn(sk, vertex_stride), 'skn_source')
                var_data.extend(chunk)
                var_cursor += len(chunk)

        flush_off = 0
        flush_bytes = skn.flush_ind_data
        if skn.flush_ind_size and flush_bytes:
            flush_off = var_cursor
            chunk = pad_array(flush_bytes, 'skn_flush_index')
            var_data.extend(chunk)
            var_cursor += len(chunk)

        sk2_wt_off = []
        for sk in skn.sk2s:
            sk2_wt_off.append(var_cursor)
            chunk = pad_array(sk.weight_data, 'skn_weight')
            var_data.extend(chunk)
            var_cursor += len(chunk)

        acc_src_off = []
        acc_dest_off = []
        acc_wt_off = []
        for sk in skn.sk_accs:
            acc_src_off.append(var_cursor)
            chunk = pad_array(sk.bind_pose_data, 'skn_source')
            var_data.extend(chunk)
            var_cursor += len(chunk)

            acc_dest_off.append(var_cursor)
            chunk = pad_array(sk.dest_index_data, 'skn_destination_index')
            var_data.extend(chunk)
            var_cursor += len(chunk)

            acc_wt_off.append(var_cursor)
            chunk = pad_array(sk.weight_data, 'skn_weight')
            var_data.extend(chunk)
            var_cursor += len(chunk)

    return (
        var_data,
        sk1_src_off,
        sk2_src_off,
        sk2_wt_off,
        acc_src_off,
        acc_dest_off,
        acc_wt_off,
        flush_bytes,
        flush_off,
    )


def _build_skn_struct_bytes(skn: SkinningData, sk1_src_off: list[int], sk2_src_off: list[int], sk2_wt_off: list[int], acc_src_off: list[int], acc_dest_off: list[int], acc_wt_off: list[int]) -> tuple[bytes, bytes, bytes]:

    SK1_SIZE = 0x40
    SK2_SIZE = 0x74
    SKACC_SIZE = 0x44
    n_sk1 = len(skn.sk1s)
    n_sk2 = len(skn.sk2s)
    n_acc = len(skn.sk_accs)

    sk1_bytes = bytearray(n_sk1 * SK1_SIZE)
    for i, sk in enumerate(skn.sk1s):
        b = i * SK1_SIZE
        struct.pack_into('>I', sk1_bytes, b + 0x30, sk1_src_off[i])
        struct.pack_into('>I', sk1_bytes, b + 0x34, sk.gpl_vertex_arr_value)
        struct.pack_into('>H', sk1_bytes, b + 0x38, sk.bone_index)
        struct.pack_into('>H', sk1_bytes, b + 0x3a, sk.vertex_cnt)
        struct.pack_into('B', sk1_bytes, b + 0x3c, sk.vertex_offset)

    sk2_bytes = bytearray(n_sk2 * SK2_SIZE)
    for i, sk in enumerate(skn.sk2s):
        b = i * SK2_SIZE
        struct.pack_into('>I', sk2_bytes, b + 0x60, sk2_src_off[i])
        struct.pack_into('>I', sk2_bytes, b + 0x64, sk2_wt_off[i])
        struct.pack_into('>I', sk2_bytes, b + 0x68, sk.gpl_vertex_arr_value)
        struct.pack_into('>H', sk2_bytes, b + 0x6c, sk.bone_index1)
        struct.pack_into('>H', sk2_bytes, b + 0x6e, sk.bone_index2)
        struct.pack_into('>H', sk2_bytes, b + 0x70, sk.vertex_cnt)
        struct.pack_into('B', sk2_bytes, b + 0x72, sk.vertex_offset)

    acc_bytes = bytearray(n_acc * SKACC_SIZE)
    for i, sk in enumerate(skn.sk_accs):
        b = i * SKACC_SIZE
        struct.pack_into('>I', acc_bytes, b + 0x30, acc_src_off[i])
        struct.pack_into('>I', acc_bytes, b + 0x34, acc_dest_off[i])
        struct.pack_into('>I', acc_bytes, b + 0x38, sk.gpl_dest_arr_value)
        struct.pack_into('>I', acc_bytes, b + 0x3c, acc_wt_off[i])
        struct.pack_into('>H', acc_bytes, b + 0x40, sk.bone_index)
        struct.pack_into('>H', acc_bytes, b + 0x42, sk.vertex_cnt)

    return bytes(sk1_bytes), bytes(sk2_bytes), bytes(acc_bytes)


def _compute_skn_mem_clear_range(skn: SkinningData, vertex_stride: int) -> tuple[int, int]:

    direct_writes = set()
    for entry in (*skn.sk1s, *skn.sk2s):
        direct_writes.update(
            entry.gpl_vertex_arr_value + entry.vertex_offset + index * vertex_stride
            for index in range(entry.vertex_cnt)
        )
    accumulation_writes = set()
    for entry in skn.sk_accs:
        destinations = struct.unpack(f'>{entry.vertex_cnt}H', entry.dest_index_data)
        accumulation_writes.update(
            entry.gpl_dest_arr_value + destination * vertex_stride
            for destination in destinations
        )
    return compute_mem_clear_range(direct_writes, accumulation_writes, vertex_stride)


def BuildSKNSkinningData(parsed: SluggieParsed) -> bytes:
    """Build the SKN (Skinning Data) section.

    memClrPtr, gplVertexArr, and gplDestArr are pos-data-relative byte offsets from
    the start of submesh 0's position buffer) and are preserved verbatim from
    parsed.skinning — they do not change when the model block is relocated or
    the GPL section is rebuilt with the same vertex data.

    NOTE: This function builds the SKN section from scratch and does NOT
    include any trailing sub-sections (ptr6/ptr7/ptr8 data that lives after
    the SKN section in the original block).  If the original model has non-
    zero ptr6/ptr7/ptr8 pointers, that data must be appended separately to
    the returned bytes.

    Returns the complete SKN section as a byte string, or b'' for non-skinned
    models.
    """

    skn = parsed.skinning
    if not skn:
        return b''

    vertex_stride = _vb_comp_size(skn.quantize_info) * 6  # pos + normal, interleaved

    n_sk1 = len(skn.sk1s)
    n_sk2 = len(skn.sk2s)
    n_acc = len(skn.sk_accs)

    SKN_HDR_SIZE  = 0x24
    SK1_SIZE      = 0x40
    SK2_SIZE      = 0x74
    SKACC_SIZE    = 0x44

    SK1_ARR_OFF   = SKN_HDR_SIZE
    SK2_ARR_OFF   = SK1_ARR_OFF   + n_sk1 * SK1_SIZE
    SKACC_ARR_OFF = SK2_ARR_OFF   + n_sk2 * SK2_SIZE
    VAR_DATA_OFF  = align_array_offset(SKACC_ARR_OFF + n_acc * SKACC_SIZE, 'skn_source')

    var_data, sk1_src_off, sk2_src_off, sk2_wt_off, acc_src_off, acc_dest_off, acc_wt_off, flush_bytes, flush_off = _layout_skn_variable_data(
        skn,
        vertex_stride,
        VAR_DATA_OFF,
    )
    sk1_bytes, sk2_bytes, acc_bytes = _build_skn_struct_bytes(
        skn,
        sk1_src_off,
        sk2_src_off,
        sk2_wt_off,
        acc_src_off,
        acc_dest_off,
        acc_wt_off,
    )
    new_memClrPtr, new_memClrSize = _compute_skn_mem_clear_range(skn, vertex_stride)
    if (new_memClrPtr, new_memClrSize) != (skn.mem_clr_ptr_value, skn.mem_clr_size):
        _slogger.info(
            f'[SKN] memClr recalculated: 0x{skn.mem_clr_ptr_value:X}/0x{skn.mem_clr_size:X}'
            f' -> 0x{new_memClrPtr:X}/0x{new_memClrSize:X}',
            source='hammerspace.main',
        )

    skn_hdr = bytearray(SKN_HDR_SIZE)
    struct.pack_into('>H', skn_hdr, 0x00, n_sk1)
    struct.pack_into('>H', skn_hdr, 0x02, n_sk2)
    struct.pack_into('>H', skn_hdr, 0x04, n_acc)
    struct.pack_into('B',  skn_hdr, 0x06, skn.quantize_info)
    struct.pack_into('>I', skn_hdr, 0x08, SK1_ARR_OFF)
    struct.pack_into('>I', skn_hdr, 0x0c, SK2_ARR_OFF)
    struct.pack_into('>I', skn_hdr, 0x10, SKACC_ARR_OFF)
    struct.pack_into('>I', skn_hdr, 0x14, new_memClrPtr)
    struct.pack_into('>I', skn_hdr, 0x18, new_memClrSize)
    struct.pack_into('>I', skn_hdr, 0x1c, flush_off if flush_bytes else 0)
    struct.pack_into('>I', skn_hdr, 0x20, skn.flush_ind_size)

    struct_end = SKN_HDR_SIZE + n_sk1 * SK1_SIZE + n_sk2 * SK2_SIZE + n_acc * SKACC_SIZE
    align_pad = VAR_DATA_OFF - struct_end
    return bytes(skn_hdr) + sk1_bytes + sk2_bytes + acc_bytes + b'\x00' * align_pad + bytes(var_data)
