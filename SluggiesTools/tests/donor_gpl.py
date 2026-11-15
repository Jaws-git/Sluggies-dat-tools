"""The GPL (mesh data) section of the synthetic donor (`synthetic_donor.py`), written from its `.sluggie`.

Test support only. The patcher never writes a GPL section from nothing: it clones the donor's and
patches or replaces what changed. A test donor has no donor of its own, so this lays one out in the
simplest valid way:

    header (0x14) | one 8-byte descriptor per submesh | submesh blobs, each 32-byte aligned | user data

and inside a blob (offsets relative to the blob start):

    layout (0x18) | position header | colour header | UV headers | normal header | display header
    | mesh name | palette names | positions | colours | UVs | normals | display states | primitive lists

A skinned submesh (six components per vertex) keeps its normals in the position array, which then
starts on a 32-byte boundary and is long enough for every vertex the SKN section writes. Primitive
lists start on a 32-byte boundary and are padded to one.
"""
from __future__ import annotations

import struct

import HammerspaceMain as main
from binfmt import shader_mode_bytes
from ModelFormat import compute_mem_clear_range

GPL_MAGIC = 0x00B749E0
HEADER_SIZE = 0x14


def _align32(offset: int) -> int:
    return (offset + 31) & ~31


def _count(data: bytes, comp_count: int, quantize_info: int) -> int:
    stride = main._vb_comp_size(quantize_info) * comp_count
    return len(data) // stride if stride else 0


def _skin_write_end(skinning) -> int:
    """End of the range the SKN section writes in submesh 0's position array."""
    if not skinning:
        return 0
    stride = main._vb_comp_size(skinning.quantize_info) * 6
    direct = {
        entry.gpl_vertex_arr_value + entry.vertex_offset + index * stride
        for entry in (*skinning.sk1s, *skinning.sk2s)
        for index in range(entry.vertex_cnt)
    }
    accumulated = {
        entry.gpl_dest_arr_value + destination * stride
        for entry in skinning.sk_accs
        for destination in struct.unpack(f'>{entry.vertex_cnt}H', entry.dest_index_data)
    }
    clear_ptr, clear_size = compute_mem_clear_range(direct, accumulated, stride)
    return max([clear_ptr + clear_size] + [offset + stride for offset in direct | accumulated])


def _blob(sub, min_position_bytes: int) -> tuple[bytes, int]:
    """One submesh blob and the offset of its name in it."""
    uvs, states = sub.uv_channels, sub.draw_states
    interleaved = sub.vertex_comp_count == 6
    normals = sub.normal_buffer
    color = sub.color_channels[0] if sub.color_channels else None

    pos_hdr, col_hdr, uv_hdr = 0x18, 0x20, 0x28
    nor_hdr = uv_hdr + len(uvs) * 0x10
    dsp_hdr = nor_hdr + 0x0C
    cursor = dsp_hdr + 0x0C
    chunks: list[tuple[int, bytes]] = []

    def place(data: bytes) -> int:
        nonlocal cursor
        at = cursor
        chunks.append((at, data))
        cursor += len(data)
        return at

    name_at = place(sub.mesh_name.encode('ascii', errors='replace') + b'\x00')
    palette_at = [place((uv.palette_name or '').encode('ascii', errors='replace') + b'\x00') for uv in uvs]
    if interleaved:
        cursor = _align32(cursor)
    positions = main._align4(sub.vertex_data)
    positions += b'\x00' * max(0, min_position_bytes - len(positions))
    pos_at = place(positions)
    col_at = place(main._align4(color.color_data)) if color else 0
    uv_at = [place(main._align4(uv.uv_data)) for uv in uvs]
    position_count = _count(sub.vertex_data, sub.vertex_comp_count, sub.vertex_quantize_info)
    if interleaved and normals:
        nor_at, normal_count = pos_at + main._vb_comp_size(sub.vertex_quantize_info) * 3, position_count
    elif normals and normals.normal_data:
        nor_at = place(main._align4(normals.normal_data))
        normal_count = _count(normals.normal_data, normals.comp_count, normals.quantize_info)
    else:
        nor_at = normal_count = 0
    states_at = cursor
    cursor += len(states) * 0x10
    lists_at = []
    for state in states:
        if state.prim_list_data:
            cursor = _align32(cursor)
            lists_at.append(place(state.prim_list_data + b'\x00' * (-len(state.prim_list_data) % 32)))
        else:
            lists_at.append(0)

    blob = bytearray(cursor)
    for at, data in chunks:
        blob[at:at + len(data)] = data
    has_lighting = bool(normals and (interleaved or normals.normal_data))
    struct.pack_into('>5IB', blob, 0, pos_hdr, col_hdr, uv_hdr, nor_hdr if has_lighting else 0, dsp_hdr, len(uvs))
    struct.pack_into('>IHBB', blob, pos_hdr, pos_at, position_count, sub.vertex_quantize_info, sub.vertex_comp_count)
    if color:
        struct.pack_into('>IHBB', blob, col_hdr, col_at,
                         len(color.color_data) // main._color_entry_size(color.quantize_info),
                         color.quantize_info, color.comp_count)
    for index, uv in enumerate(uvs):
        struct.pack_into('>IHBBII', blob, uv_hdr + index * 0x10, uv_at[index],
                         _count(uv.uv_data, uv.comp_count, uv.quantize_info), uv.quantize_info, uv.comp_count,
                         palette_at[index], 0)
    if has_lighting:
        struct.pack_into('>IHBBf', blob, nor_hdr, nor_at, normal_count, normals.quantize_info, normals.comp_count,
                         normals.ambient_pct)
    struct.pack_into('>IIH', blob, dsp_hdr, next((at for at in lists_at if at), 0), states_at, len(states))
    for index, state in enumerate(states):
        at = states_at + index * 0x10
        blob[at] = state.display_state_id
        blob[at + 1:at + 4] = state.display_state_pad_bytes[:3].ljust(3, b'\x00')
        blob[at + 4:at + 8] = shader_mode_bytes(state.shader_mode)
        struct.pack_into('>II', blob, at + 8, lists_at[index],
                         _align32(len(state.prim_list_data)) if state.prim_list_data else 0)
    return bytes(blob), name_at


def build_gpl(data: dict) -> bytes:
    """The GPL section for the model in ``data`` (a `.sluggie` dict)."""
    model = data['SluggiesModel']
    parsed = main.ParseSluggie(data)
    submeshes = parsed.mesh.submeshes
    skin_end = _skin_write_end(parsed.skinning)

    gpl = bytearray(_align32(HEADER_SIZE + len(submeshes) * 8))
    for index, sub in enumerate(submeshes):
        blob, name_at = _blob(sub, skin_end if index == 0 else 0)
        struct.pack_into('>II', gpl, HEADER_SIZE + index * 8, len(gpl), len(gpl) + name_at)
        gpl += blob
        gpl += b'\x00' * (-len(gpl) % 32)

    raw_user_data = model.get('GPLUserData')
    user_data = main._decode(raw_user_data, model.get('UseCompression', True)) if raw_user_data else b''
    struct.pack_into('>5I', gpl, 0, GPL_MAGIC, model.get('GPLUserDataLength', 0),
                     len(gpl) if user_data else 0, len(submeshes), HEADER_SIZE)
    return bytes(gpl + user_data)
