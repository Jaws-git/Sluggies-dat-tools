"""Verified binary-format rules shared by model builders and validators."""

from __future__ import annotations


CACHE_LINE_SIZE = 32

ARRAY_ALIGNMENT = {
    'skinned_position': CACHE_LINE_SIZE,
    'primitive_list': CACHE_LINE_SIZE,
    'skn_source': CACHE_LINE_SIZE,
    'skn_weight': CACHE_LINE_SIZE,
    'skn_destination_index': CACHE_LINE_SIZE,
    'skn_flush_index': CACHE_LINE_SIZE,
    # The GX texture image-base register drops the
    # low 5 address bits, and every vanilla section start and TEX payload is
    # at 0 mod 32 relative to the model block.
    'section_start': CACHE_LINE_SIZE,
    'texture_payload': CACHE_LINE_SIZE,
}


def align_up(value: int, alignment: int = CACHE_LINE_SIZE) -> int:
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError(f'alignment must be a positive power of two, got {alignment}')
    return (value + alignment - 1) & ~(alignment - 1)


def align_array_offset(offset: int, array_kind: str) -> int:
    return align_up(offset, ARRAY_ALIGNMENT[array_kind])


def pad_array(data: bytes, array_kind: str) -> bytes:
    size = align_array_offset(len(data), array_kind)
    return data + b'\x00' * (size - len(data))


def is_array_aligned(offset: int, array_kind: str) -> bool:
    return offset % ARRAY_ALIGNMENT[array_kind] == 0


def compute_mem_clear_range(
    direct_write_offsets: set[int],
    accumulation_write_offsets: set[int],
    vertex_stride: int,
) -> tuple[int, int]:
    """Return the position-data-relative range cleared before SKAcc writes."""
    accumulation_only = accumulation_write_offsets - direct_write_offsets
    if not accumulation_only:
        return 0, 0
    start = min(accumulation_only)
    span = max(accumulation_only) + vertex_stride - start
    return start, align_up(span)
