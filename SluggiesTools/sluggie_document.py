"""The ``.sluggie`` document of an exported model: one ``extract_*`` function per part of ``SluggiesModel``,
``model_document`` puts them together and ``write_model_document`` writes the file.

Every function reads an analyzed ``Model0`` (and its open file, ``model.f``); nothing here writes game files,
asks the user or reads the command line. Binary fields are written compressed ("x:" hex strings) unless
``set_compression(False)`` was called (the export's ``--debug``). ``export.py`` re-exports the public names.
"""
import json
import os
import re
import struct
import sys

import slogger as _slogger
from binfmt import (
    bti,
    color_entry_size as _color_entry_size,
    comp_size as _vb_comp_size,
    dump_sluggie as _dump_sluggie,
    encode_field as _encode_field,
    itb,
)

_HS_DIR = os.path.join(os.path.dirname(__file__), 'Hammerspace')
if _HS_DIR not in sys.path:
    sys.path.insert(0, _HS_DIR)
import UntanglePolicy  # noqa: E402

# Whether binary fields are written as "x:" hex strings (False: lists of byte ints, a plain-JSON ``.sluggie``).
_use_compression = True


def set_compression(enabled):
    """Choose how the documents built from here on store their binary fields (``UseCompression``)."""
    global _use_compression
    _use_compression = bool(enabled)


def _encode_bytes(data: bytes):
    """Encode binary data as an "x:" hex string, or a list of byte ints when compression is off."""
    return _encode_field(data, _use_compression)


def compact_faces_json(obj, indent=2):
    """Serialize obj to indented JSON, then collapse short numeric arrays and common repeating elements onto one line each."""
    _n = r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?'
    raw = json.dumps(obj, indent=indent)
    # Collapse 4-element numeric arrays (e.g. Quaternion) — must run before 3-element
    raw = re.sub(rf'\[\s*({_n}),\s*({_n}),\s*({_n}),\s*({_n})\s*\]',
                 r'[\1, \2, \3, \4]', raw, flags=re.DOTALL)
    # Collapse 3-element numeric arrays (e.g. Translation, Scale, HeadPosition, face indices)
    raw = re.sub(rf'\[\s*({_n}),\s*({_n}),\s*({_n})\s*\]',
                 r'[\1, \2, \3]', raw, flags=re.DOTALL)
    # Collapse VertexStreamLayout descriptor objects {"key": "...", "index_size": n} onto one line
    raw = re.sub(r'\{\s*"key":\s*("[\w]+")\s*,\s*"index_size":\s*(\d+)\s*\}',
                 r'{"key": \1, "index_size": \2}', raw, flags=re.DOTALL)
    return raw

def _mip_level_dimensions(width, height, level):
    """Return (w, h) for mip level `level`.

    Level zero is the base image. Each subsequent level halves both dimensions,
    clamped to a minimum of 1 pixel.
    """
    w = max(1, width // (1 << level))
    h = max(1, height // (1 << level))
    return w, h


def _mip_level_byte_length(fmt, width, height):
    """Return the encoded byte length of one mip level for a GX image format.

    Each format stores whole blocks (its storage unit: block size in pixels and
    bytes per block), so a level's size is rounded up to full blocks.
    """
    if fmt in (0, 8):  # I4, C4: 8x8 blocks / 32 bytes
        return ((width + 7) // 8) * ((height + 7) // 8) * 32
    if fmt in (1, 2, 9):  # I8, IA4, C8: 8x4 blocks / 32 bytes
        return ((width + 7) // 8) * ((height + 3) // 4) * 32
    if fmt in (3, 4, 5, 10):  # IA8, RGB565, RGB5A3, C14X2: 4x4 blocks / 32 bytes
        return ((width + 3) // 4) * ((height + 3) // 4) * 32
    if fmt == 6:  # RGBA8: 4x4 blocks / 64 bytes
        return ((width + 3) // 4) * ((height + 3) // 4) * 64
    if fmt == 14:  # CMPR: 4x4 blocks / 8 bytes
        return ((width + 3) // 4) * ((height + 3) // 4) * 8
    raise ValueError(f'unsupported GX image format code: {fmt}')


def _image_payload_length(fmt, width, height, additional_mip_count):
    """Return the exact stored image payload length (sum of all mip levels).

    Sums the encoded byte length of each level from zero through
    `additional_mip_count`, each at its halved dimensions. This is the
    authoritative length for in-place texture patching.
    """
    total = 0
    for level in range(additional_mip_count + 1):
        w, h = _mip_level_dimensions(width, height, level)
        total += _mip_level_byte_length(fmt, w, h)
    return total


def extract_tex_header(model):
    """Return TEXPalette section header fields, or None if no TEX section."""
    if not hasattr(model, 'TEXPalette') or not model.TEXPalette:
        return None
    return {"CLUTCount": model.TEXPalette.numCLUTsMaybe}


def extract_texture_descriptors(model, untangle_context=None):
    """Return a list of TEX descriptor dicts for a Model0 instance.

    Includes dimensions, format, palette info, and file offsets/lengths for
    every texture so patch_inplace.py can validate buffer sizes before writing.
    """
    if not hasattr(model, 'TEXPalette') or not model.TEXPalette:
        return []
    palette = model.TEXPalette
    result = []
    model_name_overrides = {}
    if untangle_context is not None:
        model_name_overrides = untangle_context.name_overrides.get(model.absolute, {})
    for tex_ind, desc in enumerate(palette.descriptors):
        img_offset = palette.absolute + desc.dataPtr
        img_length = palette.dataLens.get(desc.dataPtr, 0)
        # Preserve the two unknown byte regions that TEXDescriptor.analyze() discards.
        model.f.seek(desc.absolute + 0x10)
        unknown_10 = _encode_bytes(model.f.read(7))
        model.f.seek(desc.absolute + 0x1b)
        unknown_1b = _encode_bytes(model.f.read(5))
        # AdditionalMipCount is the byte at TEXDescriptor +0x16 (last byte of the
        # DescUnknownAt10 region). MipLevelCount = AdditionalMipCount + 1.
        model.f.seek(desc.absolute + 0x16)
        additional_mip_count = model.f.read(1)[0]
        entry = {
            "TextureIndex": tex_ind,
            "TextureFileName": model_name_overrides.get(tex_ind, desc.dolphinTextureBasename()) + '.png',
            "TextureDescriptorOffset": hex(desc.absolute),
            "Width": desc.width,
            "Height": desc.height,
            "Format": desc.format,
            "PaletteEntries": desc.paletteEntries,
            "PaletteFormat": desc.paletteFormat,
            "EdgeLODEnable": bool(desc.edgeLODEnable),
            "MinLOD": desc.minLOD,
            "MaxLOD": desc.maxLOD,
            "Unpacked": desc.unpacked,
            "DescUnknownAt10": unknown_10,
            "DescUnknownAt1B": unknown_1b,
            "AdditionalMipCount": additional_mip_count,
            "ImageDataOffset": hex(img_offset),
            "ImageDataLength": img_length,
            "ImagePayloadLength": _image_payload_length(
                desc.format, desc.width, desc.height, additional_mip_count
            ),
            "ImageDataCapacity": img_length
        }
        if desc.paletteDataPtr:
            pal_offset = palette.absolute + desc.paletteDataPtr
            entry["PaletteDataOffset"] = hex(pal_offset)
            entry["PaletteDataLength"] = palette.dataLens.get(desc.paletteDataPtr, 0)
        result.append(entry)
    return result

def extract_gpl_userdata(model):
    """Return (encoded_bytes_or_None, length) for the GPL user-data block."""
    if not hasattr(model, 'GPL') or not model.GPL:
        return None, 0
    gpl = model.GPL
    if not gpl.userDataPtr or not gpl.userDataSize:
        return None, 0
    model.f.seek(gpl.absolute + gpl.userDataPtr)
    raw = model.f.read(gpl.userDataSize)
    return _encode_bytes(raw), gpl.userDataSize


def extract_submeshes(model):
    """Return a list of submesh dicts with VertexBuffer, UV channel, and color channel info for a Model0 instance."""
    if not hasattr(model, 'GPL') or not model.GPL:
        return []
    submeshes = []
    for sm_idx, descriptor in enumerate(model.GPL.geoDescriptors):
        layout = descriptor.layout
        pos = layout.DOPositionHeader
        vb_offset = layout.absolute + pos.positionArrPtr
        vb_length = pos.numPositions * pos.compCount * _vb_comp_size(pos.quantizeInfo)
        model.f.seek(vb_offset)
        vb_data = _encode_bytes(model.f.read(vb_length))
        num_uv_channels = len(layout.DOTextureDataHeaders)
        faces_raw = []
        face_tex_indices = []  # per-face TextureIndex active on UV channel 0 (primary)
        uv_faces_raw = [[] for _ in range(num_uv_channels)]
        color_faces_raw = {0: [], 1: []}
        color_active = {0: False, 1: False}
        normal_faces_raw = []
        any_lighting = False
        tex_assignments = {}  # ch_ind -> {'index', 'wraps', 'wrapt'} from last Type-1 draw state
        display_states_gfx = layout.getTriangles()
        for display_state in display_states_gfx:
            state = display_state['state']
            active_descriptors = [d['key'] for d in state['descriptors']]
            # Record the last-seen texture assignment for each UV channel (Type-1 draw state)
            for ch_ind in range(num_uv_channels):
                key = 'texture' + str(ch_ind)
                if key in state and isinstance(state[key], dict) and 'index' in state[key]:
                    tex_assignments[ch_ind] = state[key]
            # Primary texture index for this draw batch (UV channel 0 / texture0)
            primary_assign = tex_assignments.get(0)
            current_primary_tex = primary_assign.get('index', 0) if primary_assign else 0
            has_color0 = 'color0' in active_descriptors
            has_color1 = 'color1' in active_descriptors
            has_lighting = 'lighting' in active_descriptors
            if has_lighting:
                any_lighting = True
            if has_color0:
                color_active[0] = True
            if has_color1:
                color_active[1] = True
            for triangle in display_state['triangles']:
                faces_raw.append([vertex['position'] for vertex in triangle])
                face_tex_indices.append(current_primary_tex)
                for ch_ind in range(num_uv_channels):
                    key = 'texture' + str(ch_ind)
                    if key in active_descriptors:
                        uv_faces_raw[ch_ind].append([vertex[key] for vertex in triangle])
                    else:
                        uv_faces_raw[ch_ind].append([0, 0, 0])
                color_faces_raw[0].append(
                    [vertex['color0'] for vertex in triangle] if has_color0 else [0, 0, 0]
                )
                color_faces_raw[1].append(
                    [vertex['color1'] for vertex in triangle] if has_color1 else [0, 0, 0]
                )
                normal_faces_raw.append(
                    [vertex['lighting'] for vertex in triangle] if has_lighting else [0, 0, 0]
                )
        face_count = len(faces_raw)
        # Pack position faces as big-endian uint16 triplets and encode them as a binary field
        flat = [idx for tri in faces_raw for idx in tri]
        faces_data = _encode_bytes(struct.pack(f'>{len(flat)}H', *flat))
        # Per-face primary texture index (uint16, one per face)
        face_tex_data = _encode_bytes(struct.pack(f'>{len(face_tex_indices)}H', *face_tex_indices))
        # Extract raw UV buffers, per-face UV indices, and texture assignment per channel
        uv_channels = []
        for ch_ind, tex_layer in enumerate(layout.DOTextureDataHeaders):
            uv_offset = layout.absolute + tex_layer.textureCoordsArrPtr
            uv_length = tex_layer.numTextureCoords * tex_layer.compCount * _vb_comp_size(tex_layer.quantizeInfo)
            model.f.seek(uv_offset)
            uv_raw = _encode_bytes(model.f.read(uv_length))
            uv_flat = [idx for tri in uv_faces_raw[ch_ind] for idx in tri]
            uv_faces_data = _encode_bytes(struct.pack(f'>{len(uv_flat)}H', *uv_flat))
            assignment = tex_assignments.get(ch_ind, {})
            uv_channels.append({
                "UVChannelIndex": ch_ind,
                "PaletteName": tex_layer.paletteName,
                "TextureIndex": assignment.get('index'),
                "WrapS": assignment.get('wraps'),
                "WrapT": assignment.get('wrapt'),
                "UVDataPtrFieldOffset": hex(tex_layer.absolute),
                "UVCountFieldOffset": hex(tex_layer.absolute + 4),
                "UVChannelOffset": hex(uv_offset),
                "UVChannelLength": uv_length,
                "UVChannelCompCount": tex_layer.compCount,
                "UVChannelQuantizeInfo": tex_layer.quantizeInfo,
                "UVFacesData": uv_faces_data,
                "UVChannelData": uv_raw
            })
        # Extract color buffer and per-face color indices for each active channel
        color_channels = []
        color_hdr = layout.DOColorHeader
        if color_hdr.colorArrPtr and (color_active[0] or color_active[1]):
            color_offset = layout.absolute + color_hdr.colorArrPtr
            color_length = color_hdr.numColors * _color_entry_size(color_hdr.quantizeInfo)
            model.f.seek(color_offset)
            color_raw = _encode_bytes(model.f.read(color_length))
            for ch_idx in [0, 1]:
                if not color_active[ch_idx]:
                    continue
                ch_flat = [idx for tri in color_faces_raw[ch_idx] for idx in tri]
                ch_faces_data = _encode_bytes(struct.pack(f'>{len(ch_flat)}H', *ch_flat))
                color_channels.append({
                    "ColorChannelIndex": ch_idx,
                    "ColorDataPtrFieldOffset": hex(color_hdr.absolute),
                    "ColorCountFieldOffset": hex(color_hdr.absolute + 4),
                    "ColorChannelOffset": hex(color_offset),
                    "ColorChannelLength": color_length,
                    "ColorChannelCompCount": color_hdr.compCount,
                    "ColorChannelQuantizeInfo": color_hdr.quantizeInfo,
                    "ColorChannelData": color_raw,
                    "ColorFacesData": ch_faces_data
                })
        # Build structural display-state info for pointer patching.
        # DODisplayState layout: [id:1][pad:3][setting:4][primitiveListPtr:4][primitiveListSize:4]
        # primitiveListPtr is relative to DOLayout.absolute (= SubmeshOffset).
        display_states_export = []
        for ds_idx, (ds_obj, display_state) in enumerate(zip(layout.DODisplayHeader.displayStates, display_states_gfx)):
            raw_prim = bytes(ds_obj.primitiveList.data)
            # Decode the 4-byte FourCC shader mode for Type-7 display states.
            # Setting field is at ds_obj.absolute + 4 (id=1B, pad=3B, then setting=4B).
            # Use ASCII only when all bytes are printable (32-126); otherwise hex.
            # This prevents control characters (e.g. \x00, \x11) from appearing as
            # raw unicode escapes in the JSON output.
            setting_bytes = itb(ds_obj.setting, 4)
            if all(32 <= b <= 126 for b in setting_bytes):
                setting_fourcc = setting_bytes.decode('ascii')
            else:
                setting_fourcc = setting_bytes.hex()
            display_states_export.append({
                "SurfaceId": f"sm{sm_idx}_ds{ds_idx}",
                "FaceCount": len(display_state.get('triangles', [])),
                "DisplayStateId": ds_obj.id,
                "DisplayStateParamBytes": ds_obj.pad_bytes.hex(),
                "DisplayStateParamBytesFieldOffset": hex(ds_obj.absolute + 1),
                "ShaderModeFieldOffset": hex(ds_obj.absolute + 4),
                "ShaderMode": setting_fourcc,
                "PrimListPtrFieldOffset": hex(ds_obj.absolute + 8),
                "PrimListSizeFieldOffset": hex(ds_obj.absolute + 12),
                "PrimListAbsoluteOffset": hex(layout.absolute + ds_obj.primitiveListPtr),
                "PrimListLength": ds_obj.primitiveListSize,
                "PrimListData": _encode_bytes(raw_prim),
                "VertexStreamLayout": [
                    {"key": d['key'], "index_size": d['index_size']}
                    for d in display_state['state']['descriptors']
                ]
            })
        # Extract normal buffer for non-skinned meshes (separate DOLightingHeader array)
        lh = layout.DOLightingHeader
        normal_buffer = None
        # A submesh has a standalone normal buffer ONLY when its DOLightingHeader
        # pointer is non-zero. When it is zero (e.g. stadium models, dirs 7-16),
        # DOLayout.analyze() still creates a DOLightingHeader at the DOLayout base,
        # so lh.normalsPtr misreads the DOPositionHeaderPtr (non-zero) and the
        # remaining fields decode as garbage (compCount/quantizeInfo from the
        # DOColorHeaderPtr bytes). Guarding on the layout pointer prevents
        # exporting a bogus NormalBuffer that later fails block validation.
        if layout.DOLightingHeaderPtr != 0 and lh.normalsPtr != 0:
            normal_abs_offset = layout.absolute + lh.normalsPtr
            normal_length = lh.numNormals * lh.compCount * _vb_comp_size(lh.quantizeInfo)
            model.f.seek(normal_abs_offset)
            raw_normals = model.f.read(normal_length)
            ambient_pct = struct.unpack('>f', lh.ambientPercentage)[0]
            normal_buffer = {
                "NormalDataPtrFieldOffset": hex(lh.absolute),
                "NormalCountFieldOffset": hex(lh.absolute + 4),
                "NormalBufferOffset": hex(normal_abs_offset),
                "NormalBufferLength": normal_length,
                "NormalBufferCompCount": lh.compCount,
                "NormalBufferQuantizeInfo": lh.quantizeInfo,
                "NormalAmbientPct": round(ambient_pct, 6),
                "NormalBufferData": _encode_bytes(raw_normals)
            }
            if any_lighting:
                # Per-loop normal indices (the 'lighting' draw-list index), aligned
                # face-for-face with FacesData — same pattern as ColorFacesData.
                normal_flat = [idx for tri in normal_faces_raw for idx in tri]
                normal_buffer["NormalFacesData"] = _encode_bytes(
                    struct.pack(f'>{len(normal_flat)}H', *normal_flat)
                )

        submesh_entry = {
            "MeshName": descriptor.n,
            "SubmeshOffset": hex(layout.absolute),
            "PositionDataPtrFieldOffset": hex(pos.absolute),
            "VertexCountFieldOffset": hex(pos.absolute + 4),
            "FacesCount": face_count,
            "FacesData": faces_data,
            "FaceTextureIndices": face_tex_data,
            "DisplayStates": display_states_export,
            "VertexBuffer": {
                "VertexBufferOffset": hex(vb_offset),
                "VertexBufferLength": vb_length,
                "VertexBufferCompCount": pos.compCount,
                "VertexBufferQuantizeInfo": pos.quantizeInfo,
                "VertexBufferData": vb_data
            },
            "UVChannels": uv_channels,
            "ColorChannels": color_channels
        }
        if normal_buffer is not None:
            submesh_entry["NormalBuffer"] = normal_buffer
        submeshes.append(submesh_entry)
    return submeshes

def extract_skin_data(model):
    """Return a SkinData dict for a Model0 instance, or None if unskinned.

    Exports all SKN/SK1/SK2/SKAcc structural fields needed by InplacePatcher/patch_skn_inplace.py:
    - Absolute offsets of every pointer field (for patching)
    - Current pointer target values (for restoring on --unpatch)
    - Raw source data bytes (bind-pose positions/normals per bone entry)
    - Raw weight and destination-index arrays

    SK1 struct fields (at SK1.absolute):
      +0x30 vertexArr   — source position+normal data, relative to SKN.absolute
      +0x34 gplVertexArr — dest byte offset within the runtime memClr region
    SK2 struct fields (at SK2.absolute):
      +0x60 vertexArr, +0x64 weightArr, +0x68 gplVertexArr
    SKAcc struct fields (at SKAcc.absolute):
      +0x30 vertexArr, +0x34 destArr (index array), +0x38 gplDestArr, +0x3C weightArr
    """
    if not model.SKN:
        return None
    skn = model.SKN
    skn_abs = skn.absolute
    comp_size = _vb_comp_size(skn.quantizeInfo)

    sk1s = []
    for sk1 in skn.SK1s:
        src_abs  = skn_abs + sk1.vertexArr
        src_size = sk1.vertexOffset + sk1.vertexCnt * 2 * 3 * comp_size
        model.f.seek(src_abs)
        src_data = model.f.read(src_size)
        sk1s.append({
            "BoneIndex":           sk1.boneIndex,
            "VertexCnt":           sk1.vertexCnt,
            "VertexOffset":        sk1.vertexOffset,
            "VertexArrFieldOffset":    hex(sk1.absolute + 0x30),
            "GplVertexArrFieldOffset": hex(sk1.absolute + 0x34),
            "VertexArrAbsolutePtr":    hex(src_abs),
            "GplVertexArrValue":       sk1.gplVertexArr,
            "BindPoseData":             _encode_bytes(src_data)
        })

    sk2s = []
    for sk2 in skn.SK2s:
        src_abs  = skn_abs + sk2.vertexArr
        src_size = sk2.vertexOffset + sk2.vertexCnt * 2 * 3 * comp_size
        model.f.seek(src_abs)
        src_data = model.f.read(src_size)
        wt_abs   = skn_abs + sk2.weightArr
        model.f.seek(wt_abs)
        wt_data  = model.f.read(sk2.vertexCnt * 2)  # 2 weight bytes per vertex
        sk2s.append({
            "BoneIndex1":          sk2.boneIndex1,
            "BoneIndex2":          sk2.boneIndex2,
            "VertexCnt":           sk2.vertexCnt,
            "VertexOffset":        sk2.vertexOffset,
            "VertexArrFieldOffset":    hex(sk2.absolute + 0x60),
            "WeightArrFieldOffset":    hex(sk2.absolute + 0x64),
            "GplVertexArrFieldOffset": hex(sk2.absolute + 0x68),
            "VertexArrAbsolutePtr":    hex(src_abs),
            "WeightArrAbsolutePtr":    hex(wt_abs),
            "GplVertexArrValue":       sk2.gplVertexArr,
            "BindPoseData":             _encode_bytes(src_data),
            "WeightData":              _encode_bytes(wt_data)
        })

    skaccs = []
    for skacc in skn.SKAccs:
        src_abs      = skn_abs + skacc.vertexArr
        src_size     = skacc.vertexCnt * 2 * 3 * comp_size  # no vertexOffset on SKAcc
        model.f.seek(src_abs)
        src_data     = model.f.read(src_size)
        dest_idx_abs = skn_abs + skacc.destArr
        model.f.seek(dest_idx_abs)
        dest_idx_data = model.f.read(skacc.vertexCnt * 2)   # uint16 dest indices
        wt_abs       = skn_abs + skacc.weightArr
        model.f.seek(wt_abs)
        wt_data      = model.f.read(skacc.vertexCnt)         # 1 weight byte per vertex
        skaccs.append({
            "BoneIndex":           skacc.boneIndex,
            "VertexCnt":           skacc.vertexCnt,
            "VertexArrFieldOffset":  hex(skacc.absolute + 0x30),
            "DestArrFieldOffset":    hex(skacc.absolute + 0x34),
            "GplDestArrFieldOffset": hex(skacc.absolute + 0x38),
            "WeightArrFieldOffset":  hex(skacc.absolute + 0x3C),
            "VertexArrAbsolutePtr":  hex(src_abs),
            "DestArrAbsolutePtr":    hex(dest_idx_abs),
            "GplDestArrValue":       skacc.gplDestArr,
            "WeightArrAbsolutePtr":  hex(wt_abs),
            "BindPoseData":          _encode_bytes(src_data),
            "DestIndexData":         _encode_bytes(dest_idx_data),
            "WeightData":            _encode_bytes(wt_data)
        })

    position_data_absolute = (
        model.GPL.geoDescriptors[0].layout.absolute
        + model.GPL.geoDescriptors[0].layout.DOPositionHeader.positionArrPtr
    )
    return {
        "SKNOffset":            hex(skn_abs),
        "GplBaseOffset":        hex(model.GPL.absolute) if model.GPL else None,
        "MemClrPtrFieldOffset": hex(skn_abs + 0x14),
        "MemClrSzeFieldOffset": hex(skn_abs + 0x18),
        "MemClrPtrValue":       skn.memClrPtr,
        "MemClrAbsolutePtr":    hex(position_data_absolute + skn.memClrPtr),
        "MemClrSize":           skn.memClrSze,
        "FlushIndArrFieldOffset": hex(skn_abs + 0x1C),
        "FlushIndAbsolutePtr":  hex(skn_abs + skn.flushIndArr) if skn.flushIndArr else None,
        "FlushIndSize":         skn.flushIndSze,
        "FlushIndData":         _extract_flush_data(model, skn, skn_abs),
        "QuantizeInfo":         skn.quantizeInfo,
        "SK1s":  sk1s,
        "SK2s":  sk2s,
        "SKAccs": skaccs
    }

def _extract_flush_data(model, skn, skn_abs):
    """Read the raw flush index array bytes (uint16 × flushIndSze), or None."""
    if not skn.flushIndArr or not skn.flushIndSze:
        return None
    model.f.seek(skn_abs + skn.flushIndArr)
    return _encode_bytes(model.f.read(skn.flushIndSze * 2))

def extract_trailing_sections(model):
    """Return a list of trailing sub-sections referenced by header ptr6/ptr7/ptr8.

    Each entry carries the header field offset (0x14/0x18/0x1c), the original
    block-relative pointer, and the raw section bytes so the hammerspace
    importer can rebuild the block from the .sluggies file alone."""
    sections = []
    all_ptrs = sorted(
        p for p in [model.gplPtr, model.ptr3, model.texPtr,
                    model.ptr5, model.ptr6, model.ptr7, model.ptr8]
        if 0 < p < model.length
    )
    for field_off, ptr in ((0x14, model.ptr6), (0x18, model.ptr7), (0x1c, model.ptr8)):
        if not ptr or ptr >= model.length:
            continue
        nxt = min([x for x in all_ptrs if x > ptr] + [model.length])
        if nxt <= ptr:
            continue
        model.f.seek(model.absolute + ptr)
        data = model.f.read(nxt - ptr)
        sections.append({
            "HeaderFieldOffset": hex(field_off),
            "OriginalPtr":       hex(ptr),
            "Length":            nxt - ptr,
            "Data":              _encode_bytes(data),
        })
    return sections


def extract_facial_pose_data(model):
    """Decode the optional ptr7 facial position/normal pose section.

    Unrecognized ptr7 payloads return None and remain preserved through
    TrailingSections. All offsets stored by this function are either explicitly
    section-relative or absolute dt_na.dat file offsets.
    """
    if not getattr(model, 'ptr7', 0):
        return None

    section_relative = model.ptr7
    section_absolute = model.absolute + section_relative
    following_ptrs = [
        ptr for ptr in (
            model.gplPtr, model.ptr3, model.texPtr, model.ptr5,
            model.ptr6, model.ptr7, model.ptr8,
        )
        if section_relative < ptr < model.length
    ]
    section_end_relative = min(following_ptrs, default=model.length)
    section_length = section_end_relative - section_relative
    if section_length < 0x14:
        return None

    model.f.seek(section_absolute)
    section = model.f.read(section_length)

    def u16(offset):
        if offset < 0 or offset + 2 > len(section):
            raise ValueError('facial uint16 outside section')
        return struct.unpack_from('>H', section, offset)[0]

    def u32(offset):
        if offset < 0 or offset + 4 > len(section):
            raise ValueError('facial uint32 outside section')
        return struct.unpack_from('>I', section, offset)[0]

    def parse_attribute(record_offset, record_size, pose_count, run_end):
        if record_offset < 0 or record_offset + record_size > len(section):
            raise ValueError('facial attribute record outside section')
        entry_count = u32(record_offset)
        format_data = section[record_offset + 4:record_offset + 8]
        submesh_index, attribute_kind, component_count, component_size = format_data
        if component_count not in (3, 6) or component_size not in (1, 2, 4):
            raise ValueError('unrecognized facial attribute format')
        run_list_offset = u32(record_offset + 8)
        pose_offsets = [
            u32(record_offset + 0x0C + index * 4)
            for index in range(pose_count)
        ]
        pose_size = entry_count * component_count * component_size
        if not (run_list_offset <= run_end <= len(section)):
            raise ValueError('invalid facial run-list bounds')
        if any(offset + pose_size > len(section) for offset in pose_offsets):
            raise ValueError('facial pose array outside section')

        runs = []
        expanded_indices = []
        for offset in range(run_list_offset, run_end, 4):
            first_vertex = u16(offset)
            vertex_count = u16(offset + 2)
            runs.append({
                'FirstVertex': first_vertex,
                'VertexCount': vertex_count,
            })
            expanded_indices.extend(range(first_vertex, first_vertex + vertex_count))
        if len(expanded_indices) != entry_count:
            raise ValueError('facial run-list count does not match attribute count')

        return {
            'RecordOffset': hex(record_offset),
            'RecordAbsoluteOffset': hex(section_absolute + record_offset),
            'EntryCount': entry_count,
            'FormatData': _encode_bytes(format_data),
            'SubmeshIndex': submesh_index,
            'AttributeKind': attribute_kind,
            'ComponentCount': component_count,
            'ComponentSize': component_size,
            'RunListOffset': hex(run_list_offset),
            'RunListAbsoluteOffset': hex(section_absolute + run_list_offset),
            'RunListData': _encode_bytes(section[run_list_offset:run_end]),
            'Runs': runs,
            'PoseOffsets': [hex(offset) for offset in pose_offsets],
            'PoseAbsoluteOffsets': [hex(section_absolute + offset) for offset in pose_offsets],
            'PoseData': [
                _encode_bytes(section[offset:offset + pose_size])
                for offset in pose_offsets
            ],
        }, expanded_indices

    try:
        maximum_pose_count = u16(0x00)
        object_count = u16(0x02)
        attribute_type_count = u16(0x04)
        object_table_offset = u32(0x08)
        if (
            not maximum_pose_count
            or not object_count
            or object_table_offset + object_count * 12 > len(section)
        ):
            return None

        submesh_arrays = []
        if getattr(model, 'GPL', None):
            for submesh_index, descriptor in enumerate(model.GPL.geoDescriptors):
                layout = descriptor.layout
                position_header = layout.DOPositionHeader
                position_size = (
                    position_header.numPositions * position_header.compCount
                    * _vb_comp_size(position_header.quantizeInfo)
                )
                position_offset = layout.absolute + position_header.positionArrPtr
                model.f.seek(position_offset)
                position_data = model.f.read(position_size)

                normal_header = layout.DOLightingHeader
                normal_data = None
                if normal_header.normalsPtr:
                    normal_size = (
                        normal_header.numNormals * normal_header.compCount
                        * _vb_comp_size(normal_header.quantizeInfo)
                    )
                    model.f.seek(layout.absolute + normal_header.normalsPtr)
                    normal_data = model.f.read(normal_size)
                submesh_arrays.append((
                    submesh_index,
                    descriptor.n,
                    position_data,
                    position_header.compCount * _vb_comp_size(position_header.quantizeInfo),
                    normal_data,
                ))

        objects = []
        recognized_objects = 0
        for object_index in range(object_count):
            table_entry_offset = object_table_offset + object_index * 12
            pose_count = u16(table_entry_offset)
            attribute_count = u16(table_entry_offset + 2)
            attribute_record_size = u32(table_entry_offset + 4)
            object_data_offset = u32(table_entry_offset + 8)
            if (
                not pose_count
                or not attribute_count
                or attribute_record_size != 0x0C + pose_count * 4
                or object_data_offset + attribute_record_size * attribute_count > len(section)
            ):
                raise ValueError('facial object data outside section')

            record_offsets = [
                object_data_offset + index * attribute_record_size
                for index in range(attribute_count)
            ]
            run_offsets = [u32(offset + 8) for offset in record_offsets]
            all_pose_offsets = [
                u32(offset + 0x0C + pose_index * 4)
                for offset in record_offsets
                for pose_index in range(pose_count)
            ]
            attributes = []
            attribute_indices = []
            for attribute_index, record_offset in enumerate(record_offsets):
                run_end = (
                    run_offsets[attribute_index + 1]
                    if attribute_index + 1 < attribute_count
                    else min(all_pose_offsets)
                )
                attribute, indices = parse_attribute(
                    record_offset, attribute_record_size, pose_count, run_end
                )
                attributes.append(attribute)
                attribute_indices.append(indices)

            position_attribute_index = next(
                (index for index, attribute in enumerate(attributes)
                 if attribute['AttributeKind'] == 1),
                None,
            )
            if position_attribute_index is None:
                raise ValueError('facial object has no position attribute')
            position = attributes[position_attribute_index]
            position_indices = attribute_indices[position_attribute_index]
            normal_attribute_index = next(
                (index for index, attribute in enumerate(attributes)
                 if attribute['AttributeKind'] == 2),
                None,
            )
            normal = (
                attributes[normal_attribute_index]
                if normal_attribute_index is not None else None
            )
            normal_indices = (
                attribute_indices[normal_attribute_index]
                if normal_attribute_index is not None else []
            )

            submesh_index = position['SubmeshIndex']
            mesh_name = None
            for candidate_index, candidate_name, positions, position_buffer_stride, normals in submesh_arrays:
                if candidate_index != submesh_index:
                    continue
                position_stride = position['ComponentCount'] * position['ComponentSize']
                position_pose_zero_offset = int(position['PoseOffsets'][0], 16)
                position_pose_zero = section[
                    position_pose_zero_offset:
                    position_pose_zero_offset + position['EntryCount'] * position_stride
                ]
                position_matches = (
                    max(position_indices, default=-1) * position_buffer_stride
                    + position_stride <= len(positions)
                    and all(
                        position_pose_zero[index * position_stride:(index + 1) * position_stride]
                        == positions[
                            vertex_index * position_buffer_stride:
                            vertex_index * position_buffer_stride + position_stride
                        ]
                        for index, vertex_index in enumerate(position_indices)
                    )
                )
                if normal is not None:
                    normal_stride = normal['ComponentCount'] * normal['ComponentSize']
                    normal_pose_zero_offset = int(normal['PoseOffsets'][0], 16)
                    normal_pose_zero = section[
                        normal_pose_zero_offset:
                        normal_pose_zero_offset + normal['EntryCount'] * normal_stride
                    ]
                    normal_matches = (
                        normals is not None
                        and max(normal_indices, default=-1) * 6 + 6 <= len(normals)
                        and all(
                            normal_pose_zero[index * normal_stride:index * normal_stride + 6]
                            == normals[vertex_index * 6:(vertex_index + 1) * 6]
                            for index, vertex_index in enumerate(normal_indices)
                        )
                    )
                elif position['ComponentCount'] >= 6:
                    normal_matches = position_matches
                else:
                    normal_matches = True
                if position_matches and normal_matches:
                    mesh_name = candidate_name
                    break
            if mesh_name is None:
                submesh_index = None

            objects.append({
                'ObjectIndex': object_index,
                'TableEntryOffset': hex(table_entry_offset),
                'TableEntryAbsoluteOffset': hex(section_absolute + table_entry_offset),
                'TableEntryData': _encode_bytes(section[table_entry_offset:table_entry_offset + 12]),
                'ObjectDataOffset': hex(object_data_offset),
                'ObjectDataAbsoluteOffset': hex(section_absolute + object_data_offset),
                'PoseCount': pose_count,
                'AttributeCount': attribute_count,
                'AttributeRecordSize': attribute_record_size,
                'SubmeshIndex': submesh_index,
                'MeshName': mesh_name,
                'Position': position,
                'Normal': normal,
                'AuxiliaryAttributes': [
                    attribute for attribute in attributes
                    if attribute['AttributeKind'] not in (1, 2)
                ],
            })
            recognized_objects += 1

        if recognized_objects != object_count:
            return None
        return {
            'SectionOffset': hex(section_absolute),
            'HeaderFieldOffset': hex(model.absolute + 0x18),
            'SectionLength': section_length,
            'SectionData': _encode_bytes(section),
            'HeaderData': _encode_bytes(section[:object_table_offset]),
            'PoseCount': maximum_pose_count,
            'AttributeTypeCount': attribute_type_count,
            'ObjectCount': object_count,
            'ObjectTableOffset': hex(object_table_offset),
            'Objects': objects,
        }
    except (ValueError, struct.error) as exc:
        _slogger.warning(
            f'Could not decode facial pose data for model at '
            f'0x{model.absolute:08X}: {type(exc).__name__}: {exc}; '
            'raw ptr7 data remains preserved in TrailingSections.',
            source='export.facial_pose',
        )
        return None

def extract_act_header(model):
    """Return an ACTHeader dict with actor/skin IDs, geo name, and the
    tree-unknown word at Tree+0x00, or None if the model has no ACT section."""
    if not hasattr(model, 'ACT') or not model.ACT:
        return None
    act = model.ACT
    # Tree struct is at act.absolute + 0x8.  Its first word is read-and-discarded
    # by Tree.analyze(); re-read it here so we can preserve it on reassembly.
    model.f.seek(act.absolute + 0x8)
    tree_unknown = bti(model.f.read(4))
    return {
        "ActorID":        act.actorID,
        "SkinFileID":     act.skinFileID,
        "GeoName":        act.geoName,
        "ACTTreeUnknown": tree_unknown,
    }


def _read_act_user_data_descriptors(model):
    """Return the ACT user-data descriptor chain as a list of
    (kind, count, data_ptr, payload) tuples, or [] if the model has no ACT
    section or no user data at all (a legal, shipped state; see
    act_section.html#user-data)."""
    if not model.ACT or not model.ACT.userDataSize:
        return []
    act = model.ACT
    model.f.seek(act.absolute + act.userDataPtr)
    blob = model.f.read(act.userDataSize)
    descriptors = []
    pos = 0
    while pos < len(blob):
        size, kind, count, data_ptr = struct.unpack_from('>IHHI', blob, pos)
        if size < 0xC:               # smaller than its own header (0: the walk would never move on)
            _slogger.warning(
                f'model at 0x{act.absolute:08X}: ACT user-data descriptor at +0x{pos:X} has size {size}; '
                'the rest of the user data is not read.',
                source='export.act_user_data',
            )
            break
        descriptors.append((kind, count, data_ptr, blob[pos + 0xC:pos + size]))
        pos += size
    return descriptors


def _extract_mirror_table(model):
    """Return {bone_id: (mirror_bone_id, role)} decoded from the kind-2 ACT
    user-data entry, or {} if the model has no kind-2 entry or its table
    doesn't fit the expected boneCount-long shape (act_section.html#user-data
    -- a few known donors have oversized, non-involution mirror tables that
    this exports as null rather than guessing at)."""
    if not model.ACT:
        return {}
    mirror_descriptors = [d for d in _read_act_user_data_descriptors(model) if d[0] == 2]
    if not mirror_descriptors:
        return {}
    _, _, _, payload = mirror_descriptors[0]
    bone_count = model.ACT.boneCount
    if len(payload) < bone_count * 2:
        _slogger.warning(
            f'model at 0x{model.ACT.absolute:08X}: kind-2 mirror table payload '
            f'({len(payload)} bytes) is shorter than boneCount*2 ({bone_count * 2}); '
            'MirrorBoneId/MirrorRole will be exported as null for all bones.',
            source='export.act_user_data',
        )
        return {}
    return {
        bone_id: (payload[2 * bone_id], payload[2 * bone_id + 1])
        for bone_id in range(bone_count)
    }


def extract_act_user_data(model):
    """Return an ACTUserData dict describing the ACT user-data descriptor
    chain, or None if the model has no ACT section or no user data at all
    (a legal, shipped state; see act_section.html#user-data).

    Kind 2 (mirror table) and kind 3 (track table) are fully decoded
    elsewhere (BoneHierarchy's MirrorBoneId/MirrorRole and TrackId); this
    only needs to preserve which kinds are present, in order, plus every
    kind-4 entry verbatim as an opaque blob so a rebuild can reproduce
    it without understanding it.
    """
    descriptors = _read_act_user_data_descriptors(model)
    if not descriptors:
        return None
    return {
        "PresentKinds": [kind for kind, _count, _data_ptr, _payload in descriptors],
        "Kind4Entries": [
            {
                "Count": count,
                "DataPtr": data_ptr,
                "Payload": _encode_bytes(payload),
            }
            for kind, count, data_ptr, payload in descriptors if kind == 4
        ],
    }


def extract_bone_data(model):
    """Return a BoneHierarchy list for the .sluggie JSON, or None if no ACT/bones present.

    Each entry contains bone identity, local transform components, absolute
    world-space head position, parent link, and per-vertex weight influences
    grouped by submesh index.

    Vertex index mapping:
    - Non-skinned bones (Skinned=False): GEOID == submesh index; VertexIndex is
      already local to that submesh (0..numPositions-1).
    - Skinned bones (Skinned=True): the raw index from SK1/SK2/SKAcc entries is
      a GPL-relative global index (gplVertexArr // skn_vs + i).  This function
      converts it to a (submesh_index, local_vertex_index) pair using the
      per-submesh vertex buffer byte offsets within the GPL section.
    """
    if not model.ACT or not model.bones:
        return None

    bones = model.bones  # dict: bone_id -> Bone

    # Build lookup by bone id to access ACTBoneLayout fields not on Bone.
    layout_by_id = {bl.id: bl for bl in model.ACT.bone_layouts.values()}

    # bone_id -> (mirror_bone_id, role) from the kind-2 user-data entry.
    mirror_table = _extract_mirror_table(model)

    # Build per-submesh (global_vtx_start, vtx_count) for skinned index remapping.
    # gplVertexArr in SK1/SK2/SKAcc is a byte offset from the start of the runtime
    # dest buffer; divided by vertexSize it gives a global vertex index that runs
    # sequentially across all submeshes (0, 1, 2, … total_verts-1).  Therefore
    # the start index for submesh j is simply the sum of vertex counts of 0..j-1.
    submesh_vtx_starts = []
    if model.GPL and model.SKN:
        cumulative = 0
        for desc in model.GPL.geoDescriptors:
            count = desc.layout.DOPositionHeader.numPositions
            submesh_vtx_starts.append((cumulative, count))
            cumulative += count

    bone_list = []
    for bone_id in sorted(bones.keys()):
        bone = bones[bone_id]
        head  = [round(float(v), 6) for v in bone.head()]
        trans = [round(float(v), 6) for v in bone.orientation.translation]
        scale = [round(float(v), 6) for v in bone.orientation.scale]
        quat  = [round(float(v), 6) for v in bone.orientation.quaternion]

        # Group vertex influences by submesh index
        by_submesh = {}
        for v_idx, (raw_weight, _src_pos, _sources) in bone.vertexInfluences.items():
            w = round(float(raw_weight) / 256.0, 6)
            if bone.skinned and submesh_vtx_starts:
                sub_idx = None
                local_idx = v_idx
                for j, (start, count) in enumerate(submesh_vtx_starts):
                    if start <= v_idx < start + count:
                        sub_idx = j
                        local_idx = v_idx - start
                        break
                if sub_idx is None:
                    continue  # vertex doesn't fall in any known submesh — skip
            else:
                # Non-skinned: GEOID is the submesh index; v_idx is already local
                sub_idx = bone.GEOID
                local_idx = v_idx

            if sub_idx not in by_submesh:
                by_submesh[sub_idx] = []
            by_submesh[sub_idx].append((int(local_idx), w))

        influences_by_submesh = [
            {
                "SubmeshIndex": sub_idx,
                "Influences": _encode_bytes(
                    b''.join(struct.pack('>Hf', vi, w) for vi, w in inf_list)
                )
            }
            for sub_idx, inf_list in sorted(by_submesh.items())
        ]

        bl = layout_by_id.get(bone_id)
        srt_type     = bl.orientation_srt.type if bl and bl.orientation_srt else 0
        draw_priority = int(bl.priority) if bl else 0
        geo_raw = int(bl.geoFileIdRaw) if bl and hasattr(bl, 'geoFileIdRaw') else (0xFFFF if bone.skinned else int(bone.GEOID))
        # ACTBoneLayout.analyze() (act.py): orientationPTR(word,+0x00) +
        # branch(4 words,+0x04..+0x13) + geoFileIdRaw(half,+0x14). The GeoId
        # field is therefore at bl.absolute + 0x14, not + 0x0C (verified
        # against real donor bytes: bl.absolute+0x0C falls inside the
        # branch table, +0x14 reads the actual 0xFFFF/owner sentinel).
        geo_field_off = hex(bl.absolute + 0x14) if bl else None
        # Absolute offset of the SRT block (ACT abs + orientationPTR). The
        # bind-pose scale floats live at SRTOffset + 0x04; the patcher uses
        # this to write an edited scale back in place. None when the bone has
        # no SRT block (orientationPTR == 0).
        srt_field_off = (
            hex(model.ACT.absolute + bl.orientationPTR)
            if bl and bl.orientationPTR else None
        )

        mirror_bone_id, mirror_role = mirror_table.get(bone_id, (None, None))

        bone_list.append({
            "BoneId":          int(bone.id),
            "GeoId":           int(bone.GEOID),
            "GeoIdRaw":        geo_raw,
            "GeoIdFieldOffset": geo_field_off,
            "ParentBoneId":    int(bone.parent.id) if bone.parent else None,
            "Skinned":         bool(bone.skinned),
            "TrackId":         int(bone.track_id),
            "MirrorBoneId":    int(mirror_bone_id) if mirror_bone_id is not None else None,
            "MirrorRole":      int(mirror_role) if mirror_role is not None else None,
            "SRTType":         int(srt_type),
            "SRTOffset":       srt_field_off,
            "DrawPriority":    draw_priority,
            "InheritTransform": bool(bone.relative),
            "Translation":     trans,
            "Scale":           scale,
            "Quaternion":      quat,
            "HeadPosition":    head,
            "VertexInfluences": influences_by_submesh,
        })

    return bone_list


def model_document(model, dir_ind, file_index, offset, length, untangle_context=None):
    """The ``.sluggie`` document of one exported model: ``model`` is an analyzed ``Model0`` (alone in its DOL
    entry, or one member of an archive container), ``offset``/``length`` its own range in the DAT."""
    gpl_user_data, gpl_user_data_length = extract_gpl_userdata(model)
    document = {
        "SluggiesModel": {
            "ChunkNumber": dir_ind,
            "FileIndex": file_index,
            "ModelOffset": hex(offset),
            "ModelLength": length,
            "UseCompression": _use_compression,
            "GPLUserDataLength": gpl_user_data_length,
            "GPLUserData": gpl_user_data,
            "TEXHeader": extract_tex_header(model),
            "TextureDescriptors": extract_texture_descriptors(model, untangle_context=untangle_context),
            "Submeshes": extract_submeshes(model),
            "SkinData": extract_skin_data(model),
            "FacialPoseData": extract_facial_pose_data(model),
            "TrailingSections": extract_trailing_sections(model),
            "ACTHeader": extract_act_header(model),
            "ACTUserData": extract_act_user_data(model),
            "BoneHierarchy": extract_bone_data(model)
        }
    }
    if dir_ind in UntanglePolicy.STADIUM_DIRS:
        # The stadium's own collision, for the Blender importer (editable copy).
        import StadiumCollision
        try:
            entries = StadiumCollision.vanilla_entries_for_model(dir_ind, file_index)
        except (OSError, ValueError) as exc:
            _slogger.warning(f'dir {dir_ind} file {file_index}: stadium collision not exported: {exc}',
                             source='export')
            entries = None
        if entries is not None:
            document["SluggiesModel"][StadiumCollision.FIELD_VANILLA] = entries
    return document


def write_model_document(document, path):
    """Write ``document`` (``model_document``) as the ``.sluggie`` file ``path``."""
    _dump_sluggie(document, path, compact_faces_json(document))
