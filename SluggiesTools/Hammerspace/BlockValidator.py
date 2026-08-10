"""Structural validator for Super Sluggers model blocks.

This module validates one assembled model block and returns a compact,
machine-readable report. It is intentionally section-oriented and suitable
for BuildModelBlock dry-run validation.
"""

from __future__ import annotations

import math
import struct

from ModelFormat import compute_mem_clear_range, is_array_aligned

GPL_MAGIC = 0x00B749E0
_VECTOR_QUANTIZE_FORMATS = {0, 3, 4, 7, 0xA}
_COLOR_QUANTIZE_FORMATS = {0, 1, 2, 3, 4, 5}


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('>I', data, offset)[0]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from('>H', data, offset)[0]


def _u8(data: bytes, offset: int) -> int:
    return data[offset]


def _comp_size(quantize_info: int) -> int:
    return 4 if (quantize_info >> 4) in (4, 7, 0xA) else 2


def _color_stride(quantize_info: int) -> int:
    return {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}.get(quantize_info >> 4, 2)


def _vector_quantize_range(quantize_info: int) -> tuple[float, float] | None:
    fmt = quantize_info >> 4
    if fmt not in (0, 3):
        return None
    shift = quantize_info & 0x0F
    scale = float(1 << shift)
    return (-32768.0 / scale, 32767.0 / scale)


def _validate_vector_quantize_info(state: _ValidationState, quantize_info: int, label: str) -> bool:
    fmt = quantize_info >> 4
    if fmt not in _VECTOR_QUANTIZE_FORMATS:
        state.fail(f'{label} uses unsupported quantize format nibble {fmt}')
        return False
    return True


def _validate_color_quantize_info(state: _ValidationState, quantize_info: int, label: str) -> bool:
    fmt = quantize_info >> 4
    if fmt not in _COLOR_QUANTIZE_FORMATS:
        state.fail(f'{label} uses unsupported color quantize format nibble {fmt}')
        return False
    return True


def _validate_quantized_payload_values(
    state: _ValidationState,
    payload: bytes,
    count: int,
    comp_count: int,
    quantize_info: int,
    label: str,
) -> None:
    fmt = quantize_info >> 4
    if fmt in (0, 3):
        value_range = _vector_quantize_range(quantize_info)
        if value_range is None:
            return
        min_value, max_value = value_range
        shift = quantize_info & 0x0F
        scale = float(1 << shift)
        stride = comp_count * 2
        for row in range(count):
            row_off = row * stride
            for component in range(comp_count):
                raw = struct.unpack_from('>h', payload, row_off + component * 2)[0]
                value = raw / scale
                if value < min_value or value > max_value:
                    state.fail(
                        f'{label} value out of representable range at index {row}, '
                        f'component {component}: {value} not in '
                        f'[{min_value}, {max_value}]'
                    )
                    return
        return

    if fmt in (4, 7, 0xA):
        stride = comp_count * 4
        for row in range(count):
            row_off = row * stride
            for component in range(comp_count):
                value = struct.unpack_from('>f', payload, row_off + component * 4)[0]
                if not math.isfinite(value):
                    state.fail(
                        f'{label} has non-finite float at index {row}, '
                        f'component {component}: {value}'
                    )
                    return


def _tpl_payload_len(fmt: int, width: int, height: int) -> int | None:
    bits_per_pixel = {
        0: 4,
        1: 8,
        2: 8,
        3: 16,
        4: 16,
        5: 16,
        6: 32,
        8: 4,
        9: 8,
        10: 16,
        14: 4,
    }
    bpp = bits_per_pixel.get(fmt)
    if bpp is None:
        return None
    return (width * height * bpp) >> 3


_ATTR_KEYS = (
    'position',
    'lighting',
    'color0',
    'color1',
    'texture0',
    'texture1',
    'texture2',
    'texture3',
    'texture4',
    'texture5',
    'texture6',
    'texture7',
)


def _descriptors_from_type3(setting: int, label: str, state: _ValidationState) -> list[dict]:
    descriptors = []
    setting >>= 2  # skip position matrix field
    for key in _ATTR_KEYS:
        mode = setting & 0b11
        setting >>= 2
        if mode == 0b00:
            continue
        if mode == 0b01:
            state.fail(f'{label} uses unsupported direct descriptor for {key}')
            continue
        descriptors.append({
            'key': key,
            'index_size': 1 + (mode & 0b01),
        })
    return descriptors


def _validate_primitive_indices(
    state: _ValidationState,
    prim_bytes: bytes,
    descriptors: list[dict],
    index_limits: dict[str, int],
    label: str,
) -> None:
    if not prim_bytes:
        return
    if not descriptors:
        state.fail(f'{label} has primitive data but no active Type-3 descriptors')
        return

    record_width = sum(d['index_size'] for d in descriptors)
    if record_width <= 0:
        state.fail(f'{label} computed non-positive primitive record width')
        return

    width_by_key = {descriptor['key']: descriptor['index_size'] for descriptor in descriptors}
    for key, width in width_by_key.items():
        limit = index_limits.get(key, 0)
        if limit <= 0:
            continue
        capacity = 1 << (8 * width)
        if limit > capacity:
            state.fail(
                f'{label} descriptor {key} uses u{width * 8} indices but '
                f'array has {limit} entries; requires wider index width'
            )

    cursor = 0
    primitive_count = 0
    while cursor < len(prim_bytes):
        prim_type = prim_bytes[cursor]
        cursor += 1
        if prim_type == 0:
            break
        primitive_count += 1
        if cursor + 2 > len(prim_bytes):
            state.fail(f'{label} truncated primitive header at byte {cursor - 1}')
            return
        vertex_count = (prim_bytes[cursor] << 8) | prim_bytes[cursor + 1]
        cursor += 2
        bytes_needed = vertex_count * record_width
        if cursor + bytes_needed > len(prim_bytes):
            state.fail(
                f'{label} primitive 0x{prim_type:02X} overruns list: '
                f'need {bytes_needed} bytes for {vertex_count} vertices, '
                f'have {len(prim_bytes) - cursor}'
            )
            return

        for vertex_index in range(vertex_count):
            for descriptor in descriptors:
                key = descriptor['key']
                idx_size = descriptor['index_size']
                idx = 0
                for _ in range(idx_size):
                    idx = (idx << 8) | prim_bytes[cursor]
                    cursor += 1
                limit = index_limits.get(key, 0)
                if limit <= 0:
                    state.fail(f'{label} references missing array for attribute {key}')
                    return
                if idx >= limit:
                    state.fail(
                        f'{label} attribute {key} index {idx} out of range '
                        f'(limit {limit - 1}) at vertex {vertex_index}'
                    )
                    return

    trailing = prim_bytes[cursor:]
    if trailing and any(byte != 0 for byte in trailing):
        state.warn(f'{label} has non-zero trailing bytes after primitive terminator')
    state.facts.setdefault('primitive_lists_validated', 0)
    state.facts['primitive_lists_validated'] += primitive_count


class _ValidationState:
    def __init__(self, block: bytes):
        self.block = block
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.facts: dict = {
            'section_pointers': {},
            'section_ranges': {},
            'section_sizes': {},
            'alignment': {
                'checked': 0,
                'failed': 0,
            },
        }

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def in_bounds(self, offset: int, size: int, label: str) -> bool:
        if offset < 0 or size < 0 or offset + size > len(self.block):
            self.fail(
                f'{label} out of bounds: off=0x{offset:X}, size=0x{size:X}, '
                f'block=0x{len(self.block):X}'
            )
            return False
        return True

    def check_array_alignment(self, offset: int, array_kind: str, label: str) -> bool:
        self.facts['alignment']['checked'] += 1
        ok = is_array_aligned(offset, array_kind)
        if not ok:
            self.facts['alignment']['failed'] += 1
            self.fail(f'{label} not 32-byte aligned: off=0x{offset:X}')
        return ok


def _validate_header(state: _ValidationState) -> dict[str, int]:
    block = state.block
    if len(block) < 0x20:
        state.fail(f'block too short for model header: {len(block)} bytes')
        return {}

    try:
        first_word = _u32(block, 0x00)
        if first_word != 0:
            state.fail(f'header first word is 0x{first_word:08X}, expected 0')
        pointers = {
            'GPL': _u32(block, 0x04),
            'ACT': _u32(block, 0x08),
            'TEX': _u32(block, 0x0C),
            'SKN': _u32(block, 0x10),
            'ptr6': _u32(block, 0x14),
            'ptr7': _u32(block, 0x18),
            'ptr8': _u32(block, 0x1C),
        }
    except struct.error as exc:
        state.fail(f'header parse failed: {type(exc).__name__}: {exc}')
        return {}

    state.facts['section_pointers'] = pointers.copy()
    if not pointers['GPL']:
        state.fail('GPL pointer is zero (required section missing)')

    populated = [value for value in pointers.values() if value]
    valid_populated = [value for value in populated if 0x20 <= value < len(block)]
    for section, pointer in pointers.items():
        if pointer and not (0x20 <= pointer < len(block)):
            state.fail(f'{section} pointer outside block: 0x{pointer:X}')

    if populated != sorted(populated):
        state.fail(f'section pointers out of order: {pointers}')

    starts = sorted(set(valid_populated + [len(block)]))
    section_ranges: dict[str, tuple[int, int]] = {}
    for section, pointer in pointers.items():
        if not pointer:
            continue
        if pointer not in valid_populated:
            continue
        next_pointer = min(candidate for candidate in starts if candidate > pointer)
        section_ranges[section] = (pointer, next_pointer)
        state.facts['section_ranges'][section] = {'start': pointer, 'end': next_pointer}
        state.facts['section_sizes'][section] = next_pointer - pointer

    return pointers


def _validate_gpl(state: _ValidationState, skn_positions: list[int], prim_offsets: list[int], non_pos_ranges: list[tuple[str, int, int]]) -> None:
    if 'GPL' not in state.facts['section_ranges']:
        return

    gpl_start, _ = state.facts['section_ranges']['GPL']['start'], state.facts['section_ranges']['GPL']['end']
    block = state.block

    try:
        magic = _u32(block, gpl_start)
        if magic != GPL_MAGIC:
            state.fail(f'GPL magic mismatch: 0x{magic:08X}')
        submesh_count = _u32(block, gpl_start + 0x0C)
        descriptor_ptr = _u32(block, gpl_start + 0x10)
    except struct.error as exc:
        state.fail(f'GPL header parse failed: {type(exc).__name__}: {exc}')
        return

    desc_off = gpl_start + descriptor_ptr
    if not state.in_bounds(desc_off, submesh_count * 8, 'GPL descriptor table'):
        return

    submesh_facts = []

    for submesh_index in range(submesh_count):
        descriptor_off = desc_off + submesh_index * 8
        try:
            layout_rel = _u32(block, descriptor_off)
        except struct.error as exc:
            state.fail(f'GPL descriptor[{submesh_index}] parse failed: {type(exc).__name__}: {exc}')
            continue
        layout_off = gpl_start + layout_rel
        if not state.in_bounds(layout_off, 0x18, f'GPL submesh[{submesh_index}] layout'):
            continue

        try:
            pos_h = _u32(block, layout_off + 0x00)
            col_h = _u32(block, layout_off + 0x04)
            uv_h = _u32(block, layout_off + 0x08)
            nor_h = _u32(block, layout_off + 0x0C)
            dsp_h = _u32(block, layout_off + 0x10)
            uv_count = _u8(block, layout_off + 0x14)
        except struct.error as exc:
            state.fail(f'GPL submesh[{submesh_index}] layout fields failed: {type(exc).__name__}: {exc}')
            continue

        def hdr8(rel_off: int, label: str) -> tuple[int, int, int, int] | None:
            abs_off = layout_off + rel_off
            if not state.in_bounds(abs_off, 8, label):
                return None
            try:
                return _u32(block, abs_off), _u16(block, abs_off + 4), _u8(block, abs_off + 6), _u8(block, abs_off + 7)
            except struct.error as exc:
                state.fail(f'{label} parse failed: {type(exc).__name__}: {exc}')
                return None

        pos = hdr8(pos_h, f'GPL submesh[{submesh_index}] position header')
        col = hdr8(col_h, f'GPL submesh[{submesh_index}] color header')
        nor = hdr8(nor_h, f'GPL submesh[{submesh_index}] normal header')
        pos_count = 0
        pos_cc = 0
        pos_q = 0
        if pos:
            pos_rel, pos_count, pos_q, pos_cc = pos
            _validate_vector_quantize_info(state, pos_q, f'GPL submesh[{submesh_index}] position header')
            if pos_cc not in (3, 6):
                state.fail(
                    f'GPL submesh[{submesh_index}] position component count {pos_cc} '
                    f'is unsupported (expected 3 or 6)'
                )
            pos_stride = _comp_size(pos_q) * pos_cc
            pos_size = pos_count * pos_stride
            pos_abs = layout_off + pos_rel
            if pos_rel:
                if state.in_bounds(pos_abs, pos_size, f'GPL submesh[{submesh_index}] position array'):
                    _validate_quantized_payload_values(
                        state,
                        block[pos_abs: pos_abs + pos_size],
                        pos_count,
                        pos_cc,
                        pos_q,
                        f'GPL submesh[{submesh_index}] position array',
                    )
                    if pos_cc == 6:
                        state.check_array_alignment(pos_abs, 'skinned_position', f'GPL submesh[{submesh_index}] skinned position array')
                        skn_positions.append(pos_abs)
                    elif submesh_index > 0:
                        non_pos_ranges.append((f'sub{submesh_index}.pos', pos_abs, pos_abs + pos_size))

        col_count = 0
        if col:
            col_rel, col_count, col_q, _ = col
            _validate_color_quantize_info(state, col_q, f'GPL submesh[{submesh_index}] color header')
            col_size = col_count * _color_stride(col_q)
            if col_rel:
                col_abs = layout_off + col_rel
                if state.in_bounds(col_abs, col_size, f'GPL submesh[{submesh_index}] color array'):
                    non_pos_ranges.append((f'sub{submesh_index}.col', col_abs, col_abs + col_size))

        nor_count = 0
        nor_cc = 0
        nor_q = 0
        if nor:
            nor_rel, nor_count, nor_q, nor_cc = nor
            _validate_vector_quantize_info(state, nor_q, f'GPL submesh[{submesh_index}] normal header')
            if nor_cc not in (0, 3, 6):
                state.fail(
                    f'GPL submesh[{submesh_index}] normal component count {nor_cc} '
                    f'is unsupported (expected 0, 3, or 6)'
                )
            nor_size = nor_count * _comp_size(nor_q) * nor_cc
            if nor_rel:
                nor_abs = layout_off + nor_rel
                if state.in_bounds(nor_abs, nor_size, f'GPL submesh[{submesh_index}] normal array'):
                    _validate_quantized_payload_values(
                        state,
                        block[nor_abs: nor_abs + nor_size],
                        nor_count,
                        nor_cc,
                        nor_q,
                        f'GPL submesh[{submesh_index}] normal array',
                    )

        uv_counts = [0] * 8
        for uv_index in range(uv_count):
            uv_hdr_rel = uv_h + uv_index * 0x10
            uv = hdr8(uv_hdr_rel, f'GPL submesh[{submesh_index}] uv[{uv_index}] header')
            if not uv:
                continue
            uv_rel, uv_count_entries, uv_q, uv_cc = uv
            _validate_vector_quantize_info(state, uv_q, f'GPL submesh[{submesh_index}] uv[{uv_index}] header')
            if uv_cc not in (1, 2):
                state.fail(
                    f'GPL submesh[{submesh_index}] uv[{uv_index}] component count {uv_cc} '
                    f'is unsupported (expected 1 or 2)'
                )
            if uv_index < 8:
                uv_counts[uv_index] = uv_count_entries
            uv_size = uv_count_entries * _comp_size(uv_q) * uv_cc
            if uv_rel:
                uv_abs = layout_off + uv_rel
                if state.in_bounds(uv_abs, uv_size, f'GPL submesh[{submesh_index}] uv[{uv_index}] array'):
                    _validate_quantized_payload_values(
                        state,
                        block[uv_abs: uv_abs + uv_size],
                        uv_count_entries,
                        uv_cc,
                        uv_q,
                        f'GPL submesh[{submesh_index}] uv[{uv_index}] array',
                    )
                    non_pos_ranges.append((f'sub{submesh_index}.uv{uv_index}', uv_abs, uv_abs + uv_size))

        dsp_header_abs = layout_off + dsp_h
        if not state.in_bounds(dsp_header_abs, 0x0A, f'GPL submesh[{submesh_index}] display-state header'):
            continue
        try:
            ds_table_rel = _u32(block, dsp_header_abs + 0x04)
            ds_count = _u16(block, dsp_header_abs + 0x08)
        except struct.error as exc:
            state.fail(f'GPL submesh[{submesh_index}] display-state header parse failed: {type(exc).__name__}: {exc}')
            continue
        ds_table_abs = layout_off + ds_table_rel
        if not state.in_bounds(ds_table_abs, ds_count * 0x10, f'GPL submesh[{submesh_index}] display-state table'):
            continue

        active_descriptors: list[dict] = []
        lighting_limit = nor_count if nor_count else (pos_count if pos_cc == 6 else 0)
        index_limits = {
            'position': pos_count,
            'lighting': lighting_limit,
            'color0': col_count,
            'color1': col_count,
            'texture0': uv_counts[0],
            'texture1': uv_counts[1],
            'texture2': uv_counts[2],
            'texture3': uv_counts[3],
            'texture4': uv_counts[4],
            'texture5': uv_counts[5],
            'texture6': uv_counts[6],
            'texture7': uv_counts[7],
        }

        for ds_index in range(ds_count):
            ds_abs = ds_table_abs + ds_index * 0x10
            try:
                ds_id = _u8(block, ds_abs + 0x00)
                setting = _u32(block, ds_abs + 0x04)
                prim_rel = _u32(block, ds_abs + 0x08)
                prim_len = _u32(block, ds_abs + 0x0C)
            except struct.error as exc:
                state.fail(f'GPL submesh[{submesh_index}] ds[{ds_index}] parse failed: {type(exc).__name__}: {exc}')
                continue

            if ds_id == 3:
                active_descriptors = _descriptors_from_type3(
                    setting,
                    f'GPL submesh[{submesh_index}] ds[{ds_index}]',
                    state,
                )

            if prim_rel and prim_len:
                prim_abs = layout_off + prim_rel
                if state.in_bounds(prim_abs, prim_len, f'GPL submesh[{submesh_index}] ds[{ds_index}] primitive list'):
                    state.check_array_alignment(prim_abs, 'primitive_list', f'GPL submesh[{submesh_index}] ds[{ds_index}] primitive list')
                    prim_offsets.append(prim_abs)
                    non_pos_ranges.append((f'sub{submesh_index}.ds{ds_index}.prim', prim_abs, prim_abs + prim_len))
                    _validate_primitive_indices(
                        state,
                        block[prim_abs: prim_abs + prim_len],
                        active_descriptors,
                        index_limits,
                        f'GPL submesh[{submesh_index}] ds[{ds_index}]',
                    )

        submesh_facts.append({
            'position_count': pos_count,
            'position_comp_count': pos_cc,
            'position_quantize_info': pos_q,
            'normal_count': nor_count,
            'normal_comp_count': nor_cc,
            'normal_quantize_info': nor_q,
        })

    state.facts['gpl_submesh_layout'] = submesh_facts


def _validate_tex(state: _ValidationState) -> None:
    if 'TEX' not in state.facts['section_ranges']:
        return

    tex_start = state.facts['section_ranges']['TEX']['start']
    tex_end = state.facts['section_ranges']['TEX']['end']
    block = state.block

    if not state.in_bounds(tex_start, 4, 'TEX header'):
        return
    try:
        texture_count = _u16(block, tex_start + 0x00)
        clut_count = _u16(block, tex_start + 0x02)
    except struct.error as exc:
        state.fail(f'TEX header parse failed: {type(exc).__name__}: {exc}')
        return

    state.facts['tex_texture_count'] = texture_count
    state.facts['tex_clut_count'] = clut_count

    descriptor_table = tex_start + 4
    if not state.in_bounds(descriptor_table, texture_count * 0x20, 'TEX descriptor table'):
        return

    for index in range(texture_count):
        desc_abs = descriptor_table + index * 0x20
        if not state.in_bounds(desc_abs, 0x20, f'TEX descriptor[{index}]'):
            continue
        try:
            image_ptr = _u32(block, desc_abs + 0x00)
            palette_ptr = _u32(block, desc_abs + 0x04)
            height = _u16(block, desc_abs + 0x08)
            width = _u16(block, desc_abs + 0x0A)
            fmt = _u8(block, desc_abs + 0x17)
            palette_entries = _u16(block, desc_abs + 0x18)
        except struct.error as exc:
            state.fail(f'TEX descriptor[{index}] parse failed: {type(exc).__name__}: {exc}')
            continue

        image_len = _tpl_payload_len(fmt, width, height)
        if image_len is None:
            state.fail(f'TEX descriptor[{index}] uses unsupported format {fmt}')
            continue

        image_abs = tex_start + image_ptr
        if image_ptr and not state.in_bounds(
            image_abs,
            image_len,
            f'TEX descriptor[{index}] image payload',
        ):
            continue

        if palette_ptr and palette_entries:
            palette_abs = tex_start + palette_ptr
            palette_len = palette_entries * 2
            state.in_bounds(
                palette_abs,
                palette_len,
                f'TEX descriptor[{index}] palette payload',
            )
            if palette_abs < tex_start or palette_abs >= tex_end:
                state.fail(
                    f'TEX descriptor[{index}] palette pointer outside TEX section: '
                    f'0x{palette_abs:X}'
                )

        if image_abs < tex_start or image_abs >= tex_end:
            state.fail(
                f'TEX descriptor[{index}] image pointer outside TEX section: '
                f'0x{image_abs:X}'
            )


def _validate_ptr7_facial(state: _ValidationState, gpl_submeshes: list[dict]) -> None:
    ptr7_range = state.facts['section_ranges'].get('ptr7')
    if not ptr7_range:
        return

    start = ptr7_range['start']
    end = ptr7_range['end']
    length = end - start
    if length < 0x14:
        state.fail(f'ptr7 facial section too short: 0x{length:X}')
        return

    section = state.block[start:end]

    def _read_u16(offset: int) -> int:
        if offset < 0 or offset + 2 > len(section):
            raise ValueError(f'ptr7 facial u16 outside section at 0x{offset:X}')
        return struct.unpack_from('>H', section, offset)[0]

    def _read_u32(offset: int) -> int:
        if offset < 0 or offset + 4 > len(section):
            raise ValueError(f'ptr7 facial u32 outside section at 0x{offset:X}')
        return struct.unpack_from('>I', section, offset)[0]

    try:
        max_pose_count = _read_u16(0x00)
        object_count = _read_u16(0x02)
        attribute_type_count = _read_u16(0x04)
        object_table_offset = _read_u32(0x08)
    except (ValueError, struct.error) as exc:
        state.fail(f'ptr7 facial header parse failed: {type(exc).__name__}: {exc}')
        return

    if max_pose_count == 0:
        state.fail('ptr7 facial pose count is zero')
    if object_count == 0:
        state.fail('ptr7 facial object count is zero')
        return
    if attribute_type_count == 0:
        state.fail('ptr7 facial attribute type count is zero')

    if object_table_offset + object_count * 12 > len(section):
        state.fail(
            f'ptr7 facial object table outside section: '
            f'off=0x{object_table_offset:X}, count={object_count}'
        )
        return

    validated_objects = 0
    for object_index in range(object_count):
        table_off = object_table_offset + object_index * 12
        try:
            pose_count = _read_u16(table_off)
            attribute_count = _read_u16(table_off + 2)
            attribute_record_size = _read_u32(table_off + 4)
            object_data_offset = _read_u32(table_off + 8)
        except (ValueError, struct.error) as exc:
            state.fail(f'ptr7 facial object[{object_index}] table parse failed: {type(exc).__name__}: {exc}')
            continue

        if pose_count == 0:
            state.fail(f'ptr7 facial object[{object_index}] has zero pose count')
            continue
        if pose_count > max_pose_count:
            state.warn(
                f'ptr7 facial object[{object_index}] pose count {pose_count} '
                f'exceeds section maximum {max_pose_count}; accepting per-object layout'
            )
        if attribute_count == 0:
            state.fail(f'ptr7 facial object[{object_index}] has zero attributes')
            continue

        expected_record_size = 0x0C + pose_count * 4
        if attribute_record_size != expected_record_size:
            state.fail(
                f'ptr7 facial object[{object_index}] attribute record size '
                f'0x{attribute_record_size:X} does not match expected '
                f'0x{expected_record_size:X}'
            )
            continue

        object_end = object_data_offset + attribute_count * attribute_record_size
        if object_end > len(section):
            state.fail(
                f'ptr7 facial object[{object_index}] attribute records outside section: '
                f'off=0x{object_data_offset:X}, end=0x{object_end:X}'
            )
            continue

        record_offsets = [
            object_data_offset + attr_index * attribute_record_size
            for attr_index in range(attribute_count)
        ]

        try:
            run_offsets = [_read_u32(record_off + 8) for record_off in record_offsets]
            pose_offsets = [
                _read_u32(record_off + 0x0C + pose_index * 4)
                for record_off in record_offsets
                for pose_index in range(pose_count)
            ]
        except (ValueError, struct.error) as exc:
            state.fail(f'ptr7 facial object[{object_index}] offset parse failed: {type(exc).__name__}: {exc}')
            continue

        if not pose_offsets:
            state.fail(f'ptr7 facial object[{object_index}] has no pose offsets')
            continue

        min_pose_offset = min(pose_offsets)

        for attr_index, record_off in enumerate(record_offsets):
            label = f'ptr7 facial object[{object_index}] attribute[{attr_index}]'
            try:
                entry_count = _read_u32(record_off)
                submesh_index = section[record_off + 4]
                attribute_kind = section[record_off + 5]
                component_count = section[record_off + 6]
                component_size = section[record_off + 7]
                run_list_offset = _read_u32(record_off + 8)
                local_pose_offsets = [
                    _read_u32(record_off + 0x0C + pose_index * 4)
                    for pose_index in range(pose_count)
                ]
            except (ValueError, struct.error, IndexError) as exc:
                state.fail(f'{label} parse failed: {type(exc).__name__}: {exc}')
                continue

            if component_count not in (3, 6):
                state.fail(f'{label} component count {component_count} is unsupported')
                continue
            if component_size not in (1, 2, 4):
                state.fail(f'{label} component size {component_size} is unsupported')
                continue
            if attribute_kind > attribute_type_count:
                state.fail(
                    f'{label} attribute kind {attribute_kind} outside declared '
                    f'0..{attribute_type_count}'
                )
                continue

            run_end = (
                run_offsets[attr_index + 1]
                if attr_index + 1 < attribute_count
                else min_pose_offset
            )
            if run_list_offset > run_end or run_end > len(section):
                state.fail(
                    f'{label} run-list bounds invalid: '
                    f'0x{run_list_offset:X}..0x{run_end:X}'
                )
                continue
            if ((run_end - run_list_offset) % 4) != 0:
                state.fail(f'{label} run-list length is not 4-byte aligned')
                continue

            expanded_vertex_count = 0
            max_vertex_index = -1
            try:
                for run_off in range(run_list_offset, run_end, 4):
                    first_vertex = _read_u16(run_off)
                    vertex_count = _read_u16(run_off + 2)
                    expanded_vertex_count += vertex_count
                    if vertex_count:
                        max_vertex_index = max(max_vertex_index, first_vertex + vertex_count - 1)
            except (ValueError, struct.error) as exc:
                state.fail(f'{label} run-list parse failed: {type(exc).__name__}: {exc}')
                continue

            if expanded_vertex_count != entry_count:
                state.fail(
                    f'{label} run-list expands to {expanded_vertex_count} vertices but '
                    f'entry count is {entry_count}'
                )
                continue

            target_quantize_info = None
            target_limit = None
            if attribute_kind in (1, 2):
                if submesh_index >= len(gpl_submeshes):
                    state.fail(
                        f'{label} references submesh {submesh_index}, '
                        f'but GPL has {len(gpl_submeshes)} submeshes'
                    )
                    continue
                submesh = gpl_submeshes[submesh_index]
                if attribute_kind == 1:
                    target_limit = submesh['position_count']
                    target_quantize_info = submesh['position_quantize_info']
                    expected_components = submesh['position_comp_count']
                    if expected_components in (3, 6) and component_count != expected_components:
                        state.fail(
                            f'{label} component count {component_count} does not match '
                            f'submesh position component count {expected_components}'
                        )
                        continue
                else:
                    target_limit = submesh['normal_count']
                    target_quantize_info = submesh['normal_quantize_info']
                    if target_limit == 0 and submesh['position_comp_count'] == 6:
                        target_limit = submesh['position_count']
                        target_quantize_info = submesh['position_quantize_info']
                    if component_count != 3:
                        state.fail(f'{label} normal component count must be 3')
                        continue

                if target_limit is None or target_limit <= 0:
                    state.fail(f'{label} references missing destination array')
                    continue
                if max_vertex_index >= target_limit:
                    state.fail(
                        f'{label} vertex index {max_vertex_index} out of range '
                        f'(limit {target_limit - 1})'
                    )
                    continue

            pose_size = entry_count * component_count * component_size
            for pose_index, pose_offset in enumerate(local_pose_offsets):
                if pose_offset + pose_size > len(section):
                    state.fail(
                        f'{label} pose[{pose_index}] outside section: '
                        f'off=0x{pose_offset:X}, size=0x{pose_size:X}'
                    )
                    break

                if target_quantize_info is None:
                    continue

                target_range = _vector_quantize_range(target_quantize_info)
                if target_range is None:
                    continue
                min_value, max_value = target_range

                pose_bytes = section[pose_offset: pose_offset + pose_size]
                if component_size == 2:
                    value_scale = float(1 << (target_quantize_info & 0x0F))
                    for value_index in range(entry_count * component_count):
                        raw = struct.unpack_from('>h', pose_bytes, value_index * 2)[0]
                        value = raw / value_scale
                        if value < min_value or value > max_value:
                            state.fail(
                                f'{label} pose[{pose_index}] value {value} out of '
                                f'range [{min_value}, {max_value}] at element {value_index}'
                            )
                            break
                elif component_size == 4:
                    for value_index in range(entry_count * component_count):
                        value = struct.unpack_from('>f', pose_bytes, value_index * 4)[0]
                        if not math.isfinite(value) or value < min_value or value > max_value:
                            state.fail(
                                f'{label} pose[{pose_index}] value {value} out of '
                                f'range [{min_value}, {max_value}] at element {value_index}'
                            )
                            break

        validated_objects += 1

    state.facts['ptr7_facial_objects_validated'] = validated_objects


def _validate_trailing_sections(state: _ValidationState, gpl_submeshes: list[dict]) -> None:
    ranges = state.facts.get('section_ranges', {})
    skn = ranges.get('SKN')
    if skn:
        skn_start = skn['start']
        for key in ('ptr6', 'ptr7', 'ptr8'):
            rng = ranges.get(key)
            if not rng:
                continue
            if rng['start'] < skn_start:
                state.fail(f'{key} starts before SKN section')
            if rng['end'] <= rng['start']:
                state.fail(f'{key} section is empty')

    if 'ptr7' in ranges:
        _validate_ptr7_facial(state, gpl_submeshes)


def _validate_skn(state: _ValidationState, skn_write_ends: list[int], skn_stride_out: list[int]) -> None:
    if 'SKN' not in state.facts['section_ranges']:
        return

    skn_start = state.facts['section_ranges']['SKN']['start']
    skn_end = state.facts['section_ranges']['SKN']['end']
    block = state.block
    if not state.in_bounds(skn_start, 0x24, 'SKN header'):
        return

    try:
        n1 = _u16(block, skn_start + 0x00)
        n2 = _u16(block, skn_start + 0x02)
        na = _u16(block, skn_start + 0x04)
        quantize_info = _u8(block, skn_start + 0x06)
        sk1_ptr = _u32(block, skn_start + 0x08)
        sk2_ptr = _u32(block, skn_start + 0x0C)
        acc_ptr = _u32(block, skn_start + 0x10)
        memclr_ptr = _u32(block, skn_start + 0x14)
        memclr_size = _u32(block, skn_start + 0x18)
        flush_ptr = _u32(block, skn_start + 0x1C)
        flush_size = _u32(block, skn_start + 0x20)
    except struct.error as exc:
        state.fail(f'SKN header parse failed: {type(exc).__name__}: {exc}')
        return

    _validate_vector_quantize_info(state, quantize_info, 'SKN header quantize info')
    if flush_size > 0xFFFF:
        state.fail(f'SKN flush index count exceeds uint16 capacity: {flush_size}')

    stride = _comp_size(quantize_info) * 6
    skn_stride_out.append(stride)
    direct_writes: set[int] = set()
    accumulation_writes: set[int] = set()

    if sk1_ptr:
        state.in_bounds(skn_start + sk1_ptr, n1 * 0x40, 'SK1 struct array')
    if sk2_ptr:
        state.in_bounds(skn_start + sk2_ptr, n2 * 0x74, 'SK2 struct array')
    if acc_ptr:
        state.in_bounds(skn_start + acc_ptr, na * 0x44, 'SKAcc struct array')

    if flush_ptr and flush_size:
        flush_abs = skn_start + flush_ptr
        if state.in_bounds(flush_abs, flush_size * 2, 'SKN flush array'):
            state.check_array_alignment(flush_abs, 'skn_flush_index', 'SKN flush array')

    if memclr_size:
        skn_write_ends.append(memclr_ptr + memclr_size)

    for index in range(n1):
        struct_abs = skn_start + sk1_ptr + index * 0x40
        if not state.in_bounds(struct_abs, 0x40, f'SK1[{index}] struct'):
            continue
        try:
            src_ptr = _u32(block, struct_abs + 0x30)
            gva = _u32(block, struct_abs + 0x34)
            vertex_count = _u16(block, struct_abs + 0x3A)
            vertex_offset = _u8(block, struct_abs + 0x3C)
        except struct.error as exc:
            state.fail(f'SK1[{index}] parse failed: {type(exc).__name__}: {exc}')
            continue
        src_size = vertex_offset + vertex_count * stride
        src_abs = skn_start + src_ptr
        if state.in_bounds(src_abs, src_size, f'SK1[{index}] source array'):
            state.check_array_alignment(src_abs, 'skn_source', f'SK1[{index}] source array')
        direct_writes.update(gva + vertex_offset + i * stride for i in range(vertex_count))
        skn_write_ends.append(gva + vertex_offset + vertex_count * stride)

    for index in range(n2):
        struct_abs = skn_start + sk2_ptr + index * 0x74
        if not state.in_bounds(struct_abs, 0x74, f'SK2[{index}] struct'):
            continue
        try:
            src_ptr = _u32(block, struct_abs + 0x60)
            wt_ptr = _u32(block, struct_abs + 0x64)
            gva = _u32(block, struct_abs + 0x68)
            vertex_count = _u16(block, struct_abs + 0x70)
            vertex_offset = _u8(block, struct_abs + 0x72)
        except struct.error as exc:
            state.fail(f'SK2[{index}] parse failed: {type(exc).__name__}: {exc}')
            continue
        src_size = vertex_offset + vertex_count * stride
        src_abs = skn_start + src_ptr
        wt_abs = skn_start + wt_ptr
        if state.in_bounds(src_abs, src_size, f'SK2[{index}] source array'):
            state.check_array_alignment(src_abs, 'skn_source', f'SK2[{index}] source array')
        if state.in_bounds(wt_abs, vertex_count * 2, f'SK2[{index}] weight array'):
            state.check_array_alignment(wt_abs, 'skn_weight', f'SK2[{index}] weight array')
        direct_writes.update(gva + vertex_offset + i * stride for i in range(vertex_count))
        skn_write_ends.append(gva + vertex_offset + vertex_count * stride)

    for index in range(na):
        struct_abs = skn_start + acc_ptr + index * 0x44
        if not state.in_bounds(struct_abs, 0x44, f'SKAcc[{index}] struct'):
            continue
        try:
            src_ptr = _u32(block, struct_abs + 0x30)
            dst_ptr = _u32(block, struct_abs + 0x34)
            gda = _u32(block, struct_abs + 0x38)
            wt_ptr = _u32(block, struct_abs + 0x3C)
            vertex_count = _u16(block, struct_abs + 0x42)
        except struct.error as exc:
            state.fail(f'SKAcc[{index}] parse failed: {type(exc).__name__}: {exc}')
            continue

        src_abs = skn_start + src_ptr
        dst_abs = skn_start + dst_ptr
        wt_abs = skn_start + wt_ptr
        if state.in_bounds(src_abs, vertex_count * stride, f'SKAcc[{index}] source array'):
            state.check_array_alignment(src_abs, 'skn_source', f'SKAcc[{index}] source array')
        if state.in_bounds(dst_abs, vertex_count * 2, f'SKAcc[{index}] destination-index array'):
            state.check_array_alignment(dst_abs, 'skn_destination_index', f'SKAcc[{index}] destination-index array')
            max_dest = 0
            if vertex_count:
                try:
                    destinations = [_u16(block, dst_abs + i * 2) for i in range(vertex_count)]
                    max_dest = max(destinations)
                    accumulation_writes.update(gda + destination * stride for destination in destinations)
                except struct.error as exc:
                    state.fail(f'SKAcc[{index}] destination-index parse failed: {type(exc).__name__}: {exc}')
            skn_write_ends.append(gda + (max_dest + 1) * stride)
        if state.in_bounds(wt_abs, vertex_count, f'SKAcc[{index}] weight array'):
            state.check_array_alignment(wt_abs, 'skn_weight', f'SKAcc[{index}] weight array')

    expected_memclr = compute_mem_clear_range(direct_writes, accumulation_writes, stride)
    if (memclr_ptr, memclr_size) != expected_memclr:
        state.fail(
            f'SKN memClr range is 0x{memclr_ptr:X}/0x{memclr_size:X}, '
            f'expected position-data-relative 0x{expected_memclr[0]:X}/0x{expected_memclr[1]:X}'
        )

    if skn_end <= skn_start:
        state.fail('SKN section end is not greater than start')


def _validate_scratch_window(
    state: _ValidationState,
    skinned_positions: list[int],
    skn_write_ends: list[int],
    non_position_ranges: list[tuple[str, int, int]],
) -> None:
    if not skinned_positions or not skn_write_ends:
        return
    window_start = min(skinned_positions)
    window_end = window_start + max(skn_write_ends)
    for label, start, end in non_position_ranges:
        if end > window_start and start < window_end:
            state.fail(
                f'scratch window overlaps {label}: '
                f'array=0x{start:X}..0x{end:X}, '
                f'window=0x{window_start:X}..0x{window_end:X}'
            )


def validate_model_block(block: bytes) -> dict:
    """Validate one assembled model block and return a structured report."""
    state = _ValidationState(block)
    pointers = _validate_header(state)
    if not pointers:
        return {
            'valid': False,
            'errors': state.errors,
            'warnings': state.warnings,
            'facts': state.facts,
        }

    skinned_positions: list[int] = []
    primitive_offsets: list[int] = []
    non_position_ranges: list[tuple[str, int, int]] = []
    skn_write_ends: list[int] = []
    skn_stride: list[int] = []

    try:
        _validate_gpl(state, skinned_positions, primitive_offsets, non_position_ranges)
        _validate_tex(state)
        _validate_skn(state, skn_write_ends, skn_stride)
        _validate_trailing_sections(state, state.facts.get('gpl_submesh_layout', []))
        _validate_scratch_window(state, skinned_positions, skn_write_ends, non_position_ranges)
    except Exception as exc:  # pragma: no cover - defensive trap for binary edge cases
        state.fail(f'unhandled validator exception: {type(exc).__name__}: {exc}')

    state.facts['skinned_position_offsets'] = skinned_positions
    state.facts['primitive_offsets'] = primitive_offsets
    state.facts['scratch_write_ends'] = skn_write_ends
    state.facts['skn_stride'] = skn_stride[0] if skn_stride else None

    return {
        'valid': not state.errors,
        'errors': state.errors,
        'warnings': state.warnings,
        'facts': state.facts,
    }
