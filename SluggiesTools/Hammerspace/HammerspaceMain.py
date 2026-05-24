import os
import sys
import base64
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(__file__))
import HammerspaceHelper as hh
from HammerspaceChunkBuilder import cloneModelToHammerspace


# ---------------------------------------------------------------------------
# Parsed data structures
# ---------------------------------------------------------------------------

@dataclass
class DrawState:
    display_state_id:            int
    prim_list_data:              bytes
    active_descriptors:          list   # [{'key': str, 'index_size': int}]
    prim_list_ptr_field_offset:  int    # absolute file offset
    prim_list_size_field_offset: int
    prim_list_absolute_offset:   int
    prim_list_length:            int
    shader_mode_field_offset:    int
    shader_mode:                 str


@dataclass
class NormalBuffer:
    normal_data_ptr_field_offset: int   # absolute file offset of normalsPtr field
    normal_count_field_offset:    int   # absolute file offset of numNormals field
    normal_buffer_offset:         int   # absolute file offset of raw data
    normal_buffer_length:         int
    comp_count:                   int
    quantize_info:                int
    ambient_pct:                  float
    normal_data:                  bytes


@dataclass
class UVChannel:
    channel_index:            int
    palette_name:             str
    texture_index:            int
    wrap_s:                   int
    wrap_t:                   int
    uv_data:                  bytes   # edited if present, else original
    uv_faces_data:            bytes   # edited if present, else original
    comp_count:               int
    quantize_info:            int
    uv_data_ptr_field_offset: int
    uv_count_field_offset:    int


@dataclass
class ColorChannel:
    channel_index:    int
    color_data:       bytes
    color_faces_data: bytes
    comp_count:       int
    quantize_info:    int


@dataclass
class Submesh:
    submesh_index:                  int
    mesh_name:                      str
    faces_count:                    int
    faces_data:                     bytes   # edited if present, else original
    face_texture_indices:           bytes
    vertex_data:                    bytes   # edited if present, else original
    vertex_comp_count:              int
    vertex_quantize_info:           int
    uv_channels:                    list    # [UVChannel]
    color_channels:                 list    # [ColorChannel]
    draw_states:                    list    # [DrawState]
    position_data_ptr_field_offset: int
    vertex_count_field_offset:      int
    normal_buffer:                  NormalBuffer | None   # None for skinned meshes


@dataclass
class MeshData:
    submeshes: list   # [Submesh]


@dataclass
class Bone:
    bone_id:            int
    geo_id:             int
    parent_bone_id:     int | None
    skinned:            bool
    track_id:           int
    srt_type:           int    # SRT block type byte (0x4 / 0x8 / 0xc; 0 when no SRT)
    draw_priority:      int    # render priority byte from ACTBoneLayout +0x19
    inherit_transform:  bool   # inheritance flag from ACTBoneLayout +0x18
    translation:        list   # [x, y, z]
    scale:              list   # [x, y, z]
    quaternion:         list   # [w, x, y, z]
    head_position:      list   # [x, y, z]
    vertex_influences:  list   # [{'submesh_index': int, 'influences': bytes}]


@dataclass
class BoneData:
    bones: list   # [Bone]


@dataclass
class Texture:
    texture_index:             int
    width:                     int
    height:                    int
    format:                    int
    palette_entries:           int
    palette_format:            int
    edge_lod_enable:           bool
    min_lod:                   float
    max_lod:                   float
    unpacked:                  int    # byte at TEXDescriptor +0x0f
    desc_unknown_at_10:        bytes  # 7 raw bytes at TEXDescriptor +0x10–+0x16
    desc_unknown_at_1b:        bytes  # 5 raw bytes at TEXDescriptor +0x1b–+0x1f
    image_data_offset:         int
    image_data_length:         int
    palette_data_offset:       int | None
    palette_data_length:       int | None
    texture_descriptor_offset: int


@dataclass
class TextureData:
    textures: list   # [Texture]


@dataclass
class SK1:
    bone_index:                  int
    vertex_cnt:                  int
    vertex_offset:               int
    bind_pose_data:              bytes   # edited if present, else original
    vertex_arr_field_offset:     int
    gpl_vertex_arr_field_offset: int
    vertex_arr_absolute_ptr:     int
    gpl_vertex_arr_value:        int


@dataclass
class SK2:
    bone_index1:                 int
    bone_index2:                 int
    vertex_cnt:                  int
    vertex_offset:               int
    bind_pose_data:              bytes   # edited if present, else original
    weight_data:                 bytes   # edited if present, else original
    vertex_arr_field_offset:     int
    weight_arr_field_offset:     int
    gpl_vertex_arr_field_offset: int
    vertex_arr_absolute_ptr:     int
    weight_arr_absolute_ptr:     int
    gpl_vertex_arr_value:        int


@dataclass
class SKAcc:
    bone_index:                int
    vertex_cnt:                int
    bind_pose_data:            bytes   # edited if present, else original
    dest_index_data:           bytes
    weight_data:               bytes   # edited if present, else original
    vertex_arr_field_offset:   int
    dest_arr_field_offset:     int
    gpl_dest_arr_field_offset: int
    weight_arr_field_offset:   int
    vertex_arr_absolute_ptr:   int
    dest_arr_absolute_ptr:     int
    gpl_dest_arr_value:        int
    weight_arr_absolute_ptr:   int


@dataclass
class SkinningData:
    skn_offset:                int
    gpl_base_offset:           int
    mem_clr_ptr_field_offset:  int
    mem_clr_sze_field_offset:  int
    mem_clr_absolute_ptr:      int
    mem_clr_size:              int
    flush_ind_arr_field_offset: int
    flush_ind_absolute_ptr:    int | None
    flush_ind_size:            int
    quantize_info:             int
    sk1s:                      list   # [SK1]
    sk2s:                      list   # [SK2]
    sk_accs:                   list   # [SKAcc]


@dataclass
class TEXHeader:
    clut_count: int    # uint16 at TEXPalette +0x02 (numCLUTsMaybe)


@dataclass
class ACTHeader:
    actor_id:          int    # uint16 at ACT header +0x04
    skin_file_id:      int    # uint16 at ACT header +0x14
    geo_name:          str    # string pointed to by ACT header +0x10
    act_tree_unknown:  int    # uint32 at Tree struct +0x00 (ACT +0x08)


@dataclass
class SluggieParsed:
    mesh:               MeshData
    bones:              BoneData | None     # None for static meshes
    textures:           TextureData
    skinning:           SkinningData | None  # None for non-skinned models
    gpl_user_data:      bytes | None        # raw GPL user-data block, or None
    gpl_user_data_len:  int                 # 0 when no user data
    act_header:         ACTHeader | None    # ACT section header fields, or None
    tex_header:         TEXHeader | None    # TEX section header fields, or None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _decode(val: str | list, use_base64: bool) -> bytes:
    """Decode a binary field from a sluggies JSON value.

    When use_base64 is True the value is a base64 string; otherwise it is a
    list of integer byte values (UseBase64=false export mode)."""
    if use_base64:
        return base64.b64decode(val)
    return bytes(val)


def _hex(val: str | int) -> int:
    """Accept either a hex string ('0x…') or a plain integer."""
    if isinstance(val, str):
        return int(val, 16)
    return int(val)


# ---------------------------------------------------------------------------
# ParseSluggie
# ---------------------------------------------------------------------------

def ParseSluggie(data: dict) -> SluggieParsed:
    """Parse the JSON contents of a .sluggies file into in-memory data
    structures ready for hammerspace block assembly.

    Prefers *Edited fields (written by the Blender exporter in Hammerspace
    Mode) over the original data where both are present.

    Parameters
    ----------
    data:
        The full parsed JSON dict (top-level object from the .sluggies file,
        which has a single key ``'SluggiesModel'``).

    Returns
    -------
    SluggieParsed
        Container holding MeshData, BoneData (or None), TextureData, and
        SkinningData (or None).
    """

    model   = data['SluggiesModel']
    use_b64 = model.get('UseBase64', True)

    # ---- MeshData ----------------------------------------------------------
    submeshes = []
    for i, sub in enumerate(model.get('Submeshes', [])):
        faces_count = sub.get('FacesCountEdited', sub['FacesCount'])
        faces_data  = _decode(sub.get('FacesDataEdited') or sub['FacesData'], use_b64)
        raw_fti     = sub.get('FaceTextureIndicesEdited') or sub.get('FaceTextureIndices')
        face_tex_indices = _decode(raw_fti, use_b64) if raw_fti else b''

        vb          = sub['VertexBuffer']
        vertex_data = _decode(vb.get('VertexBufferDataEdited') or vb['VertexBufferData'], use_b64)

        uv_channels = []
        for uv in sub.get('UVChannels', []):
            uv_channels.append(UVChannel(
                channel_index            = uv['UVChannelIndex'],
                palette_name             = uv['PaletteName'],
                texture_index            = uv.get('TextureIndex', 0),
                wrap_s                   = uv.get('WrapS', 0),
                wrap_t                   = uv.get('WrapT', 0),
                uv_data                  = _decode(uv.get('UVChannelDataEdited') or uv['UVChannelData'], use_b64),
                uv_faces_data            = _decode(uv.get('UVFacesDataEdited')   or uv['UVFacesData'],   use_b64),
                comp_count               = uv['UVChannelCompCount'],
                quantize_info            = uv['UVChannelQuantizeInfo'],
                uv_data_ptr_field_offset = _hex(uv.get('UVDataPtrFieldOffset', '0x0')),
                uv_count_field_offset    = _hex(uv.get('UVCountFieldOffset',    '0x0')),
            ))

        color_channels = []
        for cc in sub.get('ColorChannels', []):
            color_channels.append(ColorChannel(
                channel_index    = cc['ColorChannelIndex'],
                color_data       = _decode(cc['ColorChannelData'], use_b64),
                color_faces_data = _decode(cc['ColorFacesData'],   use_b64),
                comp_count       = cc['ColorChannelCompCount'],
                quantize_info    = cc['ColorChannelQuantizeInfo'],
            ))

        draw_states = []
        for ds in sub.get('DrawStates', []):
            draw_states.append(DrawState(
                display_state_id            = ds['DisplayStateId'],
                prim_list_data              = _decode(ds['PrimListData'], use_b64),
                active_descriptors          = ds.get('VertexStreamLayout') or ds.get('ActiveDescriptors', []),
                prim_list_ptr_field_offset  = _hex(ds['PrimListPtrFieldOffset']),
                prim_list_size_field_offset = _hex(ds['PrimListSizeFieldOffset']),
                prim_list_absolute_offset   = _hex(ds['PrimListAbsoluteOffset']),
                prim_list_length            = ds['PrimListLength'],
                shader_mode_field_offset    = _hex(ds['ShaderModeFieldOffset']) if ds.get('ShaderModeFieldOffset') else 0,
                shader_mode                 = ds.get('ShaderMode', ''),
            ))

        raw_nb = sub.get('NormalBuffer')
        normal_buffer = None
        if raw_nb:
            normal_buffer = NormalBuffer(
                normal_data_ptr_field_offset = _hex(raw_nb['NormalDataPtrFieldOffset']),
                normal_count_field_offset    = _hex(raw_nb['NormalCountFieldOffset']),
                normal_buffer_offset         = _hex(raw_nb['NormalBufferOffset']),
                normal_buffer_length         = raw_nb['NormalBufferLength'],
                comp_count                   = raw_nb['NormalBufferCompCount'],
                quantize_info                = raw_nb['NormalBufferQuantizeInfo'],
                ambient_pct                  = raw_nb.get('NormalAmbientPct', 0.0),
                normal_data                  = _decode(
                    raw_nb.get('NormalBufferDataEdited') or raw_nb['NormalBufferData'], use_b64
                ),
            )

        submeshes.append(Submesh(
            submesh_index                  = i,
            mesh_name                      = sub.get('MeshName', ''),
            faces_count                    = faces_count,
            faces_data                     = faces_data,
            face_texture_indices           = face_tex_indices,
            vertex_data                    = vertex_data,
            vertex_comp_count              = vb['VertexBufferCompCount'],
            vertex_quantize_info           = vb['VertexBufferQuantizeInfo'],
            uv_channels                    = uv_channels,
            color_channels                 = color_channels,
            draw_states                    = draw_states,
            position_data_ptr_field_offset = _hex(sub.get('PositionDataPtrFieldOffset', '0x0')),
            vertex_count_field_offset      = _hex(sub.get('VertexCountFieldOffset',      '0x0')),
            normal_buffer                  = normal_buffer,
        ))

    mesh_data = MeshData(submeshes=submeshes)

    # ---- GPL user data ----------------------------------------------------
    gpl_user_data_len = model.get('GPLUserDataLength', 0)
    raw_gpl_ud = model.get('GPLUserData')
    gpl_user_data = _decode(raw_gpl_ud, use_b64) if raw_gpl_ud else None

    # ---- BoneData ----------------------------------------------------------
    raw_bones = model.get('BoneHierarchyEdited') or model.get('BoneHierarchy')
    if raw_bones:
        bones = []
        for b in raw_bones:
            influences = [
                {'submesh_index': inf['SubmeshIndex'],
                 'influences':    _decode(inf['Influences'], use_b64)}
                for inf in b.get('VertexInfluences', [])
            ]
            bones.append(Bone(
                bone_id            = b['BoneId'],
                geo_id             = b['GeoId'],
                parent_bone_id     = b.get('ParentBoneId'),
                skinned            = b['Skinned'],
                track_id           = b['TrackId'],
                srt_type           = b.get('SRTType', 0),
                draw_priority      = b.get('DrawPriority', 0),
                inherit_transform  = b.get('InheritTransform', True),
                translation        = b['Translation'],
                scale              = b['Scale'],
                quaternion         = b['Quaternion'],
                head_position      = b.get('HeadPosition', [0.0, 0.0, 0.0]),
                vertex_influences  = influences,
            ))
        bone_data = BoneData(bones=bones)
    else:
        bone_data = None

    # ---- TextureData -------------------------------------------------------
    textures = []
    for tex in model.get('TextureDescriptors', []):
        textures.append(Texture(
            texture_index             = tex['TextureIndex'],
            width                     = tex['Width'],
            height                    = tex['Height'],
            format                    = tex['Format'],
            palette_entries           = tex.get('PaletteEntries', 0),
            palette_format            = tex.get('PaletteFormat', 0),
            edge_lod_enable           = tex.get('EdgeLODEnable', False),
            min_lod                   = tex.get('MinLOD', 0.0),
            max_lod                   = tex.get('MaxLOD', 0.0),
            unpacked                  = tex.get('Unpacked', 0),
            desc_unknown_at_10        = _decode(tex['DescUnknownAt10'], use_b64) if tex.get('DescUnknownAt10') else bytes(7),
            desc_unknown_at_1b        = _decode(tex['DescUnknownAt1B'], use_b64) if tex.get('DescUnknownAt1B') else bytes(5),
            image_data_offset         = _hex(tex['ImageDataOffset']),
            image_data_length         = tex['ImageDataLength'],
            palette_data_offset       = _hex(tex['PaletteDataOffset']) if tex.get('PaletteDataOffset') else None,
            palette_data_length       = tex.get('PaletteDataLength'),
            texture_descriptor_offset = _hex(tex['TextureDescriptorOffset']),
        ))

    texture_data = TextureData(textures=textures)

    # ---- SkinningData ------------------------------------------------------
    raw_skn = model.get('SkinDataEdited') or model.get('SkinData')
    if raw_skn:
        sk1s = []
        for s in raw_skn.get('SK1s', []):
            sk1s.append(SK1(
                bone_index                  = s['BoneIndex'],
                vertex_cnt                  = s['VertexCnt'],
                vertex_offset               = s.get('VertexOffset', 0),
                bind_pose_data              = _decode(s.get('BindPoseDataEdited') or s['BindPoseData'], use_b64),
                vertex_arr_field_offset     = _hex(s.get('VertexArrFieldOffset',     '0x0')),
                gpl_vertex_arr_field_offset = _hex(s.get('GplVertexArrFieldOffset',  '0x0')),
                vertex_arr_absolute_ptr     = _hex(s.get('VertexArrAbsolutePtr',     '0x0')),
                gpl_vertex_arr_value        = s.get('GplVertexArrValue', 0),
            ))

        sk2s = []
        for s in raw_skn.get('SK2s', []):
            sk2s.append(SK2(
                bone_index1                 = s['BoneIndex1'],
                bone_index2                 = s['BoneIndex2'],
                vertex_cnt                  = s['VertexCnt'],
                vertex_offset               = s.get('VertexOffset', 0),
                bind_pose_data              = _decode(s.get('BindPoseDataEdited') or s['BindPoseData'], use_b64),
                weight_data                 = _decode(s.get('WeightDataEdited')   or s['WeightData'],   use_b64),
                vertex_arr_field_offset     = _hex(s.get('VertexArrFieldOffset',     '0x0')),
                weight_arr_field_offset     = _hex(s.get('WeightArrFieldOffset',     '0x0')),
                gpl_vertex_arr_field_offset = _hex(s.get('GplVertexArrFieldOffset',  '0x0')),
                vertex_arr_absolute_ptr     = _hex(s.get('VertexArrAbsolutePtr',     '0x0')),
                weight_arr_absolute_ptr     = _hex(s.get('WeightArrAbsolutePtr',     '0x0')),
                gpl_vertex_arr_value        = s.get('GplVertexArrValue', 0),
            ))

        sk_accs = []
        for s in raw_skn.get('SKAccs', []):
            sk_accs.append(SKAcc(
                bone_index                = s['BoneIndex'],
                vertex_cnt                = s['VertexCnt'],
                bind_pose_data            = _decode(s.get('BindPoseDataEdited') or s['BindPoseData'], use_b64),
                dest_index_data           = _decode(s['DestIndexData'],                                use_b64),
                weight_data               = _decode(s.get('WeightDataEdited')   or s['WeightData'],   use_b64),
                vertex_arr_field_offset   = _hex(s.get('VertexArrFieldOffset',   '0x0')),
                dest_arr_field_offset     = _hex(s.get('DestArrFieldOffset',     '0x0')),
                gpl_dest_arr_field_offset = _hex(s.get('GplDestArrFieldOffset',  '0x0')),
                weight_arr_field_offset   = _hex(s.get('WeightArrFieldOffset',   '0x0')),
                vertex_arr_absolute_ptr   = _hex(s.get('VertexArrAbsolutePtr',   '0x0')),
                dest_arr_absolute_ptr     = _hex(s.get('DestArrAbsolutePtr',     '0x0')),
                gpl_dest_arr_value        = s.get('GplDestArrValue', 0),
                weight_arr_absolute_ptr   = _hex(s.get('WeightArrAbsolutePtr',   '0x0')),
            ))

        skinning_data = SkinningData(
            skn_offset                = _hex(raw_skn.get('SKNOffset',                '0x0')),
            gpl_base_offset           = _hex(raw_skn.get('GplBaseOffset',            '0x0')),
            mem_clr_ptr_field_offset  = _hex(raw_skn.get('MemClrPtrFieldOffset',     '0x0')),
            mem_clr_sze_field_offset  = _hex(raw_skn.get('MemClrSzeFieldOffset',     '0x0')),
            mem_clr_absolute_ptr      = _hex(raw_skn.get('MemClrAbsolutePtr',        '0x0')),
            mem_clr_size              = raw_skn.get('MemClrSize', 0),
            flush_ind_arr_field_offset= _hex(raw_skn.get('FlushIndArrFieldOffset',   '0x0')),
            flush_ind_absolute_ptr    = _hex(raw_skn['FlushIndAbsolutePtr']) if raw_skn.get('FlushIndAbsolutePtr') else None,
            flush_ind_size            = raw_skn.get('FlushIndSize', 0),
            quantize_info             = raw_skn['QuantizeInfo'],
            sk1s                      = sk1s,
            sk2s                      = sk2s,
            sk_accs                   = sk_accs,
        )
    else:
        skinning_data = None

    # ---- TEXHeader ---------------------------------------------------------
    raw_tex_hdr = model.get('TEXHeader')
    tex_header = TEXHeader(clut_count=raw_tex_hdr['CLUTCount']) if raw_tex_hdr else None

    # ---- ACTHeader ---------------------------------------------------------
    raw_act_hdr = model.get('ACTHeader')
    if raw_act_hdr:
        act_header = ACTHeader(
            actor_id         = raw_act_hdr.get('ActorID', 0),
            skin_file_id     = raw_act_hdr.get('SkinFileID', 0),
            geo_name         = raw_act_hdr.get('GeoName', ''),
            act_tree_unknown = raw_act_hdr.get('ACTTreeUnknown', 0),
        )
    else:
        act_header = None

    return SluggieParsed(
        mesh              = mesh_data,
        bones             = bone_data,
        textures          = texture_data,
        skinning          = skinning_data,
        gpl_user_data     = gpl_user_data,
        gpl_user_data_len = gpl_user_data_len,
        act_header        = act_header,
        tex_header        = tex_header,
    )


if __name__ == '__main__':
    import argparse as _ap
    import json as _json

    _parser = _ap.ArgumentParser(description='Clone or remove a model block in hammerspace.')
    _parser.add_argument('sluggies_path', help='Path to the .sluggies file')
    _parser.add_argument('--unpatch', action='store_true', help='Remove the model from hammerspace and restore the original DOL entry')
    _args = _parser.parse_args()

    with open(_args.sluggies_path, 'r') as _f:
        _data = _json.load(_f)

    _parsed = ParseSluggie(_data)
    print(f"Parsed: {len(_parsed.mesh.submeshes)} submesh(es), "
          f"{len(_parsed.bones.bones) if _parsed.bones else 0} bone(s), "
          f"{len(_parsed.textures.textures)} texture(s), "
          f"skinning={'yes' if _parsed.skinning else 'no'}")

    _model = _data['SluggiesModel']
    _chunk = _model['ChunkNumber']
    _index = _model['FileIndex']

    if _args.unpatch:
        _success = hh.removeModelFromHammerspace(_chunk, _index)
    else:
        _success = cloneModelToHammerspace(_chunk, _index)
    raise SystemExit(0 if _success else 1)
