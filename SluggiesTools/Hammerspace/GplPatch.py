"""Patches over a cloned donor GPL that keep every blob where it is.

Material states, excluded submeshes, same-size position arrays, edited UV / normal / colour arrays and
their rebuilt primitive lists. Each takes the GPL bytes and returns them patched; the field offsets in
the ``.sluggie`` are the donor's, so these run before a blob is replaced or appended.
"""

import struct

import slogger as _slogger
from binfmt import (
    color_entry_size as _color_entry_size,
    comp_size as _vb_comp_size,
    decode_field as _decode,
    shader_mode_bytes as _shader_mode_bytes,
)

from SluggieModel import _hex, excluded_submesh_indices
from Donor import Donor


def _position_edits(model: dict) -> list[tuple[int, dict, bytes]]:
    """Return validated unchanged-count position edits as (index, submesh, bytes)."""
    use_compression = model.get('UseCompression', True)
    edits = []
    for submesh_index, submesh in enumerate(model.get('Submeshes', [])):
        if submesh.get('RigidRebuild'):
            # Rebuilt as a whole blob; the validator
            # refuses in-place edit fields on the same submesh.
            continue
        vertex_buffer = submesh.get('VertexBuffer', {})
        encoded_edit = vertex_buffer.get('VertexBufferDataEdited')
        if encoded_edit is None:
            continue
        original = _decode(vertex_buffer['VertexBufferData'], use_compression)
        edited = _decode(encoded_edit, use_compression)
        if edited == original:
            continue

        comp_count = vertex_buffer.get('VertexBufferCompCount')
        quantize_info = vertex_buffer.get('VertexBufferQuantizeInfo')
        if comp_count not in (3, 6):
            raise ValueError(
                f'sub{submesh_index}: position-only edit has unsupported component '
                f'count {comp_count}; expected 3 or 6')
        if not isinstance(quantize_info, int) or (quantize_info >> 4) not in (0, 1, 2, 3, 4, 7, 0xA):
            raise ValueError(
                f'sub{submesh_index}: position-only edit has unsupported quantization '
                f'{quantize_info!r}')
        stride = comp_count * _vb_comp_size(quantize_info)
        if len(original) % stride:
            raise ValueError(
                f'sub{submesh_index}: donor position length {len(original)} is not '
                f'divisible by stride {stride}')
        if len(edited) != len(original):
            raise ValueError(
                f'sub{submesh_index}: position-only edit changed byte length from '
                f'{len(original)} to {len(edited)} (stride {stride}); topology/count '
                'changes require a later milestone')
        recorded_length = vertex_buffer.get('VertexBufferLength')
        if recorded_length is not None and recorded_length != len(original):
            raise ValueError(
                f'sub{submesh_index}: recorded VertexBufferLength {recorded_length} '
                f'does not match donor payload length {len(original)}')

        original_face_count = submesh.get('FacesCount')
        edited_face_count = submesh.get('FacesCountEdited', original_face_count)
        if edited_face_count != original_face_count:
            raise ValueError(
                f'sub{submesh_index}: position-only edit changed face count from '
                f'{original_face_count} to {edited_face_count}')
        encoded_faces = submesh.get('FacesDataEdited')
        if encoded_faces is not None and _decode(encoded_faces, use_compression) != _decode(
            submesh['FacesData'], use_compression
        ):
            raise ValueError(
                f'sub{submesh_index}: position-only edit changed face indices/order')
        edits.append((submesh_index, submesh, edited))
    return edits


def PatchGPLMaterialStates(gpl_bytes: bytes, data: dict, donor: Donor) -> bytes:
    """Patch aliased Type-7 material bytes over an otherwise verbatim donor GPL."""

    gpl_absolute = donor.gpl_absolute()
    patched = bytearray(gpl_bytes)

    for submesh_index, submesh in enumerate(data['SluggiesModel'].get('Submeshes', [])):
        for state_index, state in enumerate(submesh.get('DisplayStates', [])):
            if not (
                state.get('MaterialStateAliasedByImporter')
                or state.get('ShaderModeEdited') is not None
                or state.get('DisplayStateParamBytesEdited') is not None
            ):
                continue
            setting_field = _hex(state['ShaderModeFieldOffset'])
            state_relative = setting_field - 4 - gpl_absolute
            if state_relative < 0 or state_relative + 8 > len(patched):
                raise ValueError(
                    f'sub{submesh_index} ds{state_index}: material-state record '
                    f'offset 0x{state_relative:X} is outside cloned GPL size '
                    f'0x{len(patched):X}')
            pad = bytes.fromhex(
                state.get('DisplayStateParamBytesEdited') or state.get('DisplayStateParamBytes', '000000')
            )
            setting = state.get('ShaderModeEdited') or state.get('ShaderMode', '')
            setting_bytes = _shader_mode_bytes(setting)
            patched[state_relative + 1:state_relative + 4] = pad[:3].ljust(3, b'\x00')
            patched[state_relative + 4:state_relative + 8] = setting_bytes
            _slogger.info(
                f'[GPL] patched material state sub{submesh_index} ds{state_index} '
                f'at GPL+0x{state_relative:X}',
                source='hammerspace.main',
            )

    return bytes(patched)


def PatchGPLExcludeSubmeshes(gpl_bytes: bytes, model: dict, donor: Donor) -> bytes:
    """Empty the primitive lists of every excluded submesh over a cloned
    donor GPL: pointer and size 0 in each display state record, which the
    game skips (a list pointer of 0 draws nothing). Vertex data, display
    state records, counts and the owner bone stay, so no index moves.
    Before any blob is replaced or relocated (the field offsets are the
    donor's)."""

    indices = excluded_submesh_indices(model)
    if not indices:
        return gpl_bytes
    gpl_absolute = donor.gpl_absolute()
    patched = bytearray(gpl_bytes)
    submeshes = model.get('Submeshes', [])
    for submesh_index in indices:
        emptied = 0
        for state_index, state in enumerate(submeshes[submesh_index].get('DisplayStates', [])):
            if not int(state.get('PrimListLength') or 0):
                continue
            for key in ('PrimListPtrFieldOffset', 'PrimListSizeFieldOffset'):
                relative = _hex(state[key]) - gpl_absolute
                if relative < 0 or relative + 4 > len(patched):
                    raise ValueError(
                        f'sub{submesh_index} ds{state_index}: {key} 0x{relative:X} is outside '
                        f'cloned GPL size 0x{len(patched):X}')
                patched[relative:relative + 4] = b'\0\0\0\0'
            emptied += 1
        _slogger.info(
            f'[GPL] excluded sub{submesh_index} ({submeshes[submesh_index].get("MeshName", "")}): '
            f'{emptied} primitive list(s) emptied',
            source='hammerspace.main',
        )
    return bytes(patched)


def PatchGPLPositionArrays(gpl_bytes: bytes, model: dict, donor: Donor) -> bytes:
    """Patch validated same-size position arrays over an otherwise cloned GPL."""

    gpl_absolute = donor.gpl_absolute()
    patched = bytearray(gpl_bytes)
    for submesh_index, submesh, edited in _position_edits(model):
        position_absolute = _hex(submesh['VertexBuffer']['VertexBufferOffset'])
        position_relative = position_absolute - gpl_absolute
        if position_relative < 0 or position_relative + len(edited) > len(patched):
            raise ValueError(
                f'sub{submesh_index}: position array range GPL+0x{position_relative:X} '
                f'..0x{position_relative + len(edited):X} exceeds cloned GPL size '
                f'0x{len(patched):X}')
        patched[position_relative:position_relative + len(edited)] = edited
        _slogger.info(
            f'[GPL] patched position array sub{submesh_index} at '
            f'GPL+0x{position_relative:X} ({len(edited):,} bytes)',
            source='hammerspace.main',
        )
    return bytes(patched)


def PatchGPLUVArrays(gpl_bytes: bytes, model: dict, donor: Donor) -> bytes:
    """Patch same-size edited UV/normal arrays over an otherwise cloned donor GPL."""

    gpl_absolute = donor.gpl_absolute()
    use_compression = model.get('UseCompression', True)
    patched = bytearray(gpl_bytes)
    for submesh_index, submesh in enumerate(model.get('Submeshes', [])):
        if not submesh.get('UVArraysEditedByImporter') \
                and not submesh.get('NormalArraysEditedByImporter') \
                and not submesh.get('ColorArraysEditedByImporter'):
            continue
        for uv in submesh.get('UVChannels', []):
            encoded = uv.get('UVChannelDataEdited')
            if encoded is None:
                continue
            original = _decode(uv['UVChannelData'], use_compression)
            edited = _decode(encoded, use_compression)
            if edited == original:
                continue
            if len(edited) != len(original):
                raise ValueError(
                    f'sub{submesh_index} uv{uv["UVChannelIndex"]}: compact UV '
                    f'length changed from {len(original)} to {len(edited)}; '
                    'requires GPL serialization')
            uv_absolute = _hex(uv['UVChannelOffset'])
            uv_relative = uv_absolute - gpl_absolute
            if uv_relative < 0 or uv_relative + len(edited) > len(patched):
                raise ValueError(
                    f'sub{submesh_index} uv{uv["UVChannelIndex"]}: array range '
                    f'GPL+0x{uv_relative:X}..0x{uv_relative + len(edited):X} '
                    f'exceeds cloned GPL size 0x{len(patched):X}')
            patched[uv_relative:uv_relative + len(edited)] = edited
            _slogger.info(
                f'[GPL] patched UV array sub{submesh_index} '
                f'uv{uv["UVChannelIndex"]} at GPL+0x{uv_relative:X} '
                f'({len(edited):,} bytes)',
                source='hammerspace.main',
            )
        nb = submesh.get('NormalBuffer')
        encoded = nb.get('NormalBufferDataEdited') if nb else None
        if encoded is not None:
            original = _decode(nb['NormalBufferData'], use_compression)
            edited = _decode(encoded, use_compression)
            if edited != original:
                stride = (
                    nb['NormalBufferCompCount']
                    * _vb_comp_size(nb['NormalBufferQuantizeInfo'])
                )
                if len(edited) % stride or len(edited) // stride > 0xFFFF:
                    raise ValueError(
                        f'sub{submesh_index}: compact normal count '
                        f'{len(edited) // stride} is invalid for record size {stride}')
                if len(edited) != len(original):
                    raise ValueError(
                        f'sub{submesh_index}: compact normal length changed from '
                        f'{len(original)} to {len(edited)}; requires GPL serialization')
                normal_absolute = _hex(nb['NormalBufferOffset'])
                normal_relative = normal_absolute - gpl_absolute
                if normal_relative < 0 or normal_relative + len(edited) > len(patched):
                    raise ValueError(
                        f'sub{submesh_index}: normal array range '
                        f'GPL+0x{normal_relative:X}..0x{normal_relative + len(edited):X} '
                        f'exceeds cloned GPL size 0x{len(patched):X}')
                patched[normal_relative:normal_relative + len(edited)] = edited
                _slogger.info(
                    f'[GPL] patched normal array sub{submesh_index} at '
                    f'GPL+0x{normal_relative:X} ({len(edited):,} bytes)',
                    source='hammerspace.main',
                )

        if submesh.get('ColorArraysEditedByImporter'):
            color_channels = submesh.get('ColorChannels', [])
            original = _decode(color_channels[0]['ColorChannelData'], use_compression)
            for cc in color_channels[1:]:
                other = _decode(cc['ColorChannelData'], use_compression)
                if len(other) != len(original):
                    raise ValueError(
                        f'sub{submesh_index}: color channels report different '
                        f'donor array sizes ({len(original)} vs {len(other)})')
            for cc in color_channels:
                encoded = cc.get('ColorChannelDataEdited')
                if encoded is None:
                    continue
                edited = _decode(encoded, use_compression)
                if edited == original:
                    continue
                if len(edited) != len(original):
                    raise ValueError(
                        f'sub{submesh_index} color{cc["ColorChannelIndex"]}: '
                        'compact color length changed from '
                        f'{len(original)} to {len(edited)}; requires GPL serialization')
                color_absolute = _hex(cc['ColorChannelOffset'])
                color_relative = color_absolute - gpl_absolute
                if color_relative < 0 or color_relative + len(edited) > len(patched):
                    raise ValueError(
                        f'sub{submesh_index} color{cc["ColorChannelIndex"]}: '
                        f'array range GPL+0x{color_relative:X}..0x{color_relative + len(edited):X} '
                        f'exceeds cloned GPL size 0x{len(patched):X}')
                patched[color_relative:color_relative + len(edited)] = edited
                _slogger.info(
                    f'[GPL] patched color array sub{submesh_index} '
                    f'color{cc["ColorChannelIndex"]} at GPL+0x{color_relative:X} '
                    f'({len(edited):,} bytes)',
                    source='hammerspace.main',
                )
                break
    return bytes(patched)


def PatchGPLUVRebuild(gpl_bytes: bytes, model: dict, donor: Donor) -> bytes:
    """Append resized UV/list payloads and redirect pointers over cloned GPL."""

    gpl_absolute = donor.gpl_absolute()
    use_compression = model.get('UseCompression', True)
    patched = bytearray(gpl_bytes)

    def append_payload(payload: bytes, alignment: int) -> int:
        padding = (-len(patched)) % alignment
        if padding:
            patched.extend(b'\x00' * padding)
        offset = len(patched)
        patched.extend(payload)
        return offset

    for submesh_index, submesh in enumerate(model.get('Submeshes', [])):
        if not submesh.get('UVArraysEditedByImporter') \
                and not submesh.get('NormalArraysEditedByImporter') \
                and not submesh.get('ColorArraysEditedByImporter'):
            continue
        submesh_relative = _hex(submesh['SubmeshOffset']) - gpl_absolute
        if not 0 <= submesh_relative < len(gpl_bytes):
            raise ValueError(
                f'sub{submesh_index}: donor layout offset GPL+0x{submesh_relative:X} '
                f'is outside cloned GPL size 0x{len(gpl_bytes):X}')

        for uv in submesh.get('UVChannels', []):
            encoded = uv.get('UVChannelDataEdited')
            if encoded is None:
                continue
            original = _decode(uv['UVChannelData'], use_compression)
            edited = _decode(encoded, use_compression)
            if edited == original:
                continue
            stride = uv['UVChannelCompCount'] * _vb_comp_size(uv['UVChannelQuantizeInfo'])
            if len(edited) % stride:
                raise ValueError(
                    f'sub{submesh_index} uv{uv["UVChannelIndex"]}: edited length '
                    f'{len(edited)} is not divisible by stride {stride}')
            if len(edited) // stride > 0xFFFF:
                raise ValueError(
                    f'sub{submesh_index} uv{uv["UVChannelIndex"]}: coordinate '
                    f'count {len(edited) // stride} exceeds uint16')
            data_offset = append_payload(edited, 4)
            pointer_field = _hex(uv['UVDataPtrFieldOffset']) - gpl_absolute
            count_field = _hex(uv['UVCountFieldOffset']) - gpl_absolute
            if pointer_field < 0 or count_field < 0 or count_field + 2 > len(gpl_bytes):
                raise ValueError(
                    f'sub{submesh_index} uv{uv["UVChannelIndex"]}: header fields '
                    'are outside cloned GPL')
            struct.pack_into('>I', patched, pointer_field, data_offset - submesh_relative)
            struct.pack_into('>H', patched, count_field, len(edited) // stride)
            _slogger.info(
                f'[GPL] appended UV array sub{submesh_index} '
                f'uv{uv["UVChannelIndex"]} at GPL+0x{data_offset:X} '
                f'({len(edited):,} bytes)',
                source='hammerspace.main',
            )

        nb = submesh.get('NormalBuffer')
        encoded = nb.get('NormalBufferDataEdited') if nb else None
        if encoded is not None:
            original = _decode(nb['NormalBufferData'], use_compression)
            edited = _decode(encoded, use_compression)
            if edited != original:
                if int((submesh.get('VertexBuffer') or {}).get('VertexBufferCompCount', 3)) == 6:
                    # Never detach a skinned submesh's normals from its
                    # interleaved position buffer (pos + 6): the SKN deformer
                    # rewrites that buffer every frame, a standalone copy
                    # freezes the lighting.  GeometryRebuild keeps the donor
                    # layout for these, so this is a defensive guard.
                    raise ValueError(
                        f'sub{submesh_index}: normals of a skinned (CompCount 6) '
                        'submesh are interleaved with its positions and cannot be '
                        'appended as a standalone array')
                stride = (
                    nb['NormalBufferCompCount']
                    * _vb_comp_size(nb['NormalBufferQuantizeInfo'])
                )
                if len(edited) % stride:
                    raise ValueError(
                        f'sub{submesh_index}: compact normal length '
                        f'{len(edited)} is not divisible by stride {stride}')
                if len(edited) // stride > 0xFFFF:
                    raise ValueError(
                        f'sub{submesh_index}: normal count '
                        f'{len(edited) // stride} exceeds uint16')
                data_offset = append_payload(edited, 4)
                pointer_field = _hex(nb['NormalDataPtrFieldOffset']) - gpl_absolute
                count_field = _hex(nb['NormalCountFieldOffset']) - gpl_absolute
                if pointer_field < 0 or count_field < 0 \
                        or count_field + 2 > len(gpl_bytes):
                    raise ValueError(
                        f'sub{submesh_index}: normal header fields are outside '
                        'cloned GPL')
                struct.pack_into('>I', patched, pointer_field, data_offset - submesh_relative)
                struct.pack_into('>H', patched, count_field, len(edited) // stride)
                _slogger.info(
                    f'[GPL] appended normal array sub{submesh_index} at '
                    f'GPL+0x{data_offset:X} ({len(edited):,} bytes)',
                    source='hammerspace.main',
                )

        if submesh.get('ColorArraysEditedByImporter'):
            color_channels = submesh.get('ColorChannels', [])
            color_appended = False
            for cc in color_channels:
                encoded = cc.get('ColorChannelDataEdited')
                if encoded is None:
                    continue
                original = _decode(cc['ColorChannelData'], use_compression)
                edited = _decode(encoded, use_compression)
                if edited == original:
                    continue
                entry_size = _color_entry_size(cc['ColorChannelQuantizeInfo'])
                if len(edited) % entry_size:
                    raise ValueError(
                        f'sub{submesh_index} color{cc["ColorChannelIndex"]}: '
                        f'edited length {len(edited)} is not divisible by '
                        f'entry size {entry_size}')
                if len(edited) // entry_size > 0xFFFF:
                    raise ValueError(
                        f'sub{submesh_index} color{cc["ColorChannelIndex"]}: '
                        f'color count {len(edited) // entry_size} exceeds uint16')
                if not color_appended:
                    data_offset = append_payload(edited, 4)
                    pointer_field = _hex(cc['ColorDataPtrFieldOffset']) - gpl_absolute
                    count_field = _hex(cc['ColorCountFieldOffset']) - gpl_absolute
                    if pointer_field < 0 or count_field < 0 \
                            or count_field + 2 > len(gpl_bytes):
                        raise ValueError(
                            f'sub{submesh_index}: color header fields are outside '
                            'cloned GPL')
                    struct.pack_into('>I', patched, pointer_field,
                                 data_offset - submesh_relative)
                    struct.pack_into('>H', patched, count_field,
                                 len(edited) // entry_size)
                    color_appended = True
                    _slogger.info(
                        f'[GPL] appended color array sub{submesh_index} at '
                        f'GPL+0x{data_offset:X} ({len(edited):,} bytes)',
                        source='hammerspace.main',
                    )
                break

        for state_index, state in enumerate(submesh.get('DisplayStates', [])):
            edited_primitive = state.get('PrimListDataEdited')
            if edited_primitive is not None:
                raw_primitive = _decode(edited_primitive, use_compression)
                if raw_primitive:
                    padded = raw_primitive + b'\x00' * ((-len(raw_primitive)) % 32)
                    primitive_offset = append_payload(padded, 32)
                    primitive_relative = primitive_offset - submesh_relative
                    primitive_size = len(padded)
                else:
                    primitive_relative = 0
                    primitive_size = 0
                pointer_field = _hex(state['PrimListPtrFieldOffset']) - gpl_absolute
                size_field = _hex(state['PrimListSizeFieldOffset']) - gpl_absolute
                if pointer_field < 0 or size_field < 0 or size_field + 4 > len(gpl_bytes):
                    raise ValueError(
                        f'sub{submesh_index} ds{state_index}: primitive header '
                        'fields are outside cloned GPL')
                struct.pack_into('>I', patched, pointer_field, primitive_relative)
                struct.pack_into('>I', patched, size_field, primitive_size)
                _slogger.info(
                    f'[GPL] appended primitive list sub{submesh_index} ds{state_index} '
                    f'({primitive_size:,} bytes)',
                    source='hammerspace.main',
                )
            if state.get('ShaderModeEdited') is not None:
                setting_field = _hex(state['ShaderModeFieldOffset']) - gpl_absolute
                setting = state['ShaderModeEdited']
                setting_bytes = _shader_mode_bytes(setting)
                patched[setting_field:setting_field + 4] = setting_bytes

    return bytes(patched)
