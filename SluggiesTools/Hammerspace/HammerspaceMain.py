import os
import sys
import base64
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(__file__))
import HammerspaceHelper as hh

# ---------------------------------------------------------------------------
# Parsed data structures
# ---------------------------------------------------------------------------

@dataclass
class DrawState:
    display_state_id:            int
    display_state_pad_bytes:     bytes
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
    model_offset:       int                 # absolute byte offset of model block in INPUT dat
    model_length:       int                 # byte length of model block in INPUT dat


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
# GPL build result
# ---------------------------------------------------------------------------

@dataclass
class GPLBuildResult:
    """Output of BuildGPLMeshData.

    Carries the raw bytes of the GPL section plus the metadata that the SKN
    builder needs to recalculate gplVertexArr / gplDestArr fields without
    having to re-parse the byte string.

    Attributes
    ----------
    gpl_bytes : bytes
        The complete GPL section.
    pos_gpl_offsets : list[int]
        For each submesh i, the GPL-section-relative byte offset of that
        submesh's raw position data array.  This is what the SKN builder uses
        as ``new_pos_gpl_off[i]`` when recalculating gplVertexArr.
    """
    gpl_bytes: bytes
    pos_gpl_offsets: list[int]


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

        # Determine whether prim lists have been rebuilt.  If not, the GPU
        # draw commands still reference the ORIGINAL compact UV/color arrays,
        # so we must use the original data even if *Edited fields exist.
        _prim_lists_edited = any(
            'PrimListDataEdited' in ds for ds in sub.get('DisplayStates', [])
        )

        if _prim_lists_edited:
            vertex_data = _decode(vb.get('VertexBufferDataEdited') or vb['VertexBufferData'], use_b64)
        else:
            vertex_data = _decode(vb['VertexBufferData'], use_b64)

        uv_channels = []
        for uv in sub.get('UVChannels', []):
            if _prim_lists_edited:
                _uv_src = uv.get('UVChannelDataEdited') or uv['UVChannelData']
            else:
                _uv_src = uv['UVChannelData']
            uv_channels.append(UVChannel(
                channel_index            = uv['UVChannelIndex'],
                palette_name             = uv['PaletteName'],
                texture_index            = uv.get('TextureIndex', 0),
                wrap_s                   = uv.get('WrapS', 0),
                wrap_t                   = uv.get('WrapT', 0),
                uv_data                  = _decode(_uv_src, use_b64),
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
        for ds in sub.get('DisplayStates', []):
            pad_hex = ds.get('DisplayStatePadBytes', '000000')
            draw_states.append(DrawState(
                display_state_id            = ds['DisplayStateId'],
                display_state_pad_bytes     = bytes.fromhex(pad_hex),
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
    # Use the ORIGINAL SkinData for structural ordering (bone indices,
    # gplVertexArr, vertex_offset) since the game engine depends on the
    # original SK entry order.  Substitute edited payload (bind-pose data,
    # weights, vertex counts) from SkinDataEdited when present AND the mesh
    # geometry was actually modified (prim lists rebuilt).
    raw_skn_orig = model.get('SkinData')
    raw_skn_edit = model.get('SkinDataEdited')
    # Determine if geometry was actually edited (any submesh has rebuilt prim
    # lists).  If not, SkinDataEdited may still exist but contains Blender
    # re-export precision drift — use original bind pose data instead.
    _geometry_edited = any(
        'PrimListDataEdited' in ds
        for sub in model.get('Submeshes', [])
        for ds in sub.get('DisplayStates', [])
    )
    raw_skn = raw_skn_orig or raw_skn_edit
    if raw_skn:
        # Build lookup dicts from edited data for payload substitution.
        _edit_sk1_by_bone = {}
        _edit_sk2_by_pair = {}
        _edit_acc_by_bone = {}
        if raw_skn_edit:
            for s in raw_skn_edit.get('SK1s', []):
                _edit_sk1_by_bone[s['BoneIndex']] = s
            for s in raw_skn_edit.get('SK2s', []):
                key = (s['BoneIndex1'], s['BoneIndex2'])
                _edit_sk2_by_pair[key] = s
            for s in raw_skn_edit.get('SKAccs', []):
                _edit_acc_by_bone[s['BoneIndex']] = s

        sk1s = []
        for s in raw_skn.get('SK1s', []):
            ed = _edit_sk1_by_bone.get(s['BoneIndex']) if _geometry_edited else None
            if _geometry_edited and ed:
                bp_src = ed.get('BindPoseDataEdited') or ed.get('BindPoseData') or s['BindPoseData']
            else:
                bp_src = s['BindPoseData']
            sk1s.append(SK1(
                bone_index                  = s['BoneIndex'],
                vertex_cnt                  = (ed or s)['VertexCnt'],
                vertex_offset               = s.get('VertexOffset', 0),
                bind_pose_data              = _decode(bp_src, use_b64),
                vertex_arr_field_offset     = _hex(s.get('VertexArrFieldOffset',     '0x0')),
                gpl_vertex_arr_field_offset = _hex(s.get('GplVertexArrFieldOffset',  '0x0')),
                vertex_arr_absolute_ptr     = _hex(s.get('VertexArrAbsolutePtr',     '0x0')),
                gpl_vertex_arr_value        = s.get('GplVertexArrValue', 0),
            ))

        sk2s = []
        for s in raw_skn.get('SK2s', []):
            key = (s['BoneIndex1'], s['BoneIndex2'])
            ed = _edit_sk2_by_pair.get(key) if _geometry_edited else None
            if _geometry_edited and ed:
                bp_src = ed.get('BindPoseDataEdited') or ed.get('BindPoseData') or s['BindPoseData']
                wt_src = ed.get('WeightDataEdited') or ed.get('WeightData') or s['WeightData']
            else:
                bp_src = s['BindPoseData']
                wt_src = s['WeightData']
            sk2s.append(SK2(
                bone_index1                 = s['BoneIndex1'],
                bone_index2                 = s['BoneIndex2'],
                vertex_cnt                  = (ed or s)['VertexCnt'],
                vertex_offset               = s.get('VertexOffset', 0),
                bind_pose_data              = _decode(bp_src, use_b64),
                weight_data                 = _decode(wt_src, use_b64),
                vertex_arr_field_offset     = _hex(s.get('VertexArrFieldOffset',     '0x0')),
                weight_arr_field_offset     = _hex(s.get('WeightArrFieldOffset',     '0x0')),
                gpl_vertex_arr_field_offset = _hex(s.get('GplVertexArrFieldOffset',  '0x0')),
                vertex_arr_absolute_ptr     = _hex(s.get('VertexArrAbsolutePtr',     '0x0')),
                weight_arr_absolute_ptr     = _hex(s.get('WeightArrAbsolutePtr',     '0x0')),
                gpl_vertex_arr_value        = s.get('GplVertexArrValue', 0),
            ))

        sk_accs = []
        for s in raw_skn.get('SKAccs', []):
            ed = _edit_acc_by_bone.get(s['BoneIndex']) if _geometry_edited else None
            if _geometry_edited and ed:
                bp_src = ed.get('BindPoseDataEdited') or ed.get('BindPoseData') or s['BindPoseData']
                wt_src = ed.get('WeightDataEdited') or ed.get('WeightData') or s['WeightData']
            else:
                bp_src = s['BindPoseData']
                wt_src = s['WeightData']
            dest_src = (ed or s).get('DestIndexData') or s['DestIndexData']
            sk_accs.append(SKAcc(
                bone_index                = s['BoneIndex'],
                vertex_cnt                = (ed or s)['VertexCnt'],
                bind_pose_data            = _decode(bp_src, use_b64),
                dest_index_data           = _decode(dest_src, use_b64),
                weight_data               = _decode(wt_src, use_b64),
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
        model_offset      = _hex(model.get('ModelOffset', '0x0')),
        model_length      = model.get('ModelLength', 0),
    )



# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def BuildGPLMeshData(parsed: SluggieParsed) -> GPLBuildResult:
    """Build the GPL (Mesh Data) section from the parsed sluggies data.

    Encodes vertex position/UV/color/normal arrays, assembles draw lists,
    and lays out all GEO descriptors, DOLayout structs, and data headers
    with correct relative pointers.

    Returns a GPLBuildResult containing the complete GPL section bytes and
    per-submesh GPL-relative position array offsets (needed by the SKN
    builder to recalculate gplVertexArr / gplDestArr without re-parsing).

    Pointer conventions
    -------------------
    - GEO Descriptor DOLayoutPtr and namePtr  →  GPL-section-relative
    - All pointers inside DOLayout and its sub-structs →  DOLayout-start-relative
    """
    import struct as _s

    GPL_MAGIC    = 0x00B749E0
    GPL_HDR_SIZE = 0x14   # magic + userDataLen + userDataPtr + N + descriptorPtr

    # -------------------------------------------------------------------------
    # Local helpers
    # -------------------------------------------------------------------------

    def _align4(data: bytes) -> bytes:
        r = len(data) % 4
        return data + b'\x00' * ((4 - r) % 4)

    def _align32(offset: int) -> int:
        """Round offset UP to next 32-byte boundary."""
        return (offset + 31) & ~31

    def _vb_comp_size(quant_info: int) -> int:
        """Bytes per vertex-buffer component: 4 for float32 formats, 2 for int16."""
        return 4 if (quant_info >> 4) in (4, 7, 0xa) else 2

    def _vertex_count(data: bytes, comp_count: int, quant_info: int) -> int:
        stride = _vb_comp_size(quant_info) * comp_count
        return len(data) // stride if stride else 0

    def _color_count(data: bytes, quant_info: int) -> int:
        fmt = quant_info >> 4
        stride = {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}.get(fmt, 2)
        return len(data) // stride

    def _setting_bytes(shader_mode: str) -> bytes:
        """Decode a ShaderMode string back to 4 raw bytes (the setting field)."""
        if len(shader_mode) == 8 and all(c in '0123456789abcdefABCDEF' for c in shader_mode):
            return bytes.fromhex(shader_mode)
        return shader_mode.encode('ascii', errors='replace').ljust(4, b'\x00')[:4]

    # -------------------------------------------------------------------------
    # Phase 1: per-submesh layout pass (all offsets DOLayout-relative)
    # Phase 2: count derivation from raw buffer sizes
    # -------------------------------------------------------------------------

    N = len(parsed.mesh.submeshes)
    sub_layouts = []

    for sub in parsed.mesh.submeshes:
        M_uv = len(sub.uv_channels)
        n_ds = len(sub.draw_states)

        # Fixed-size header region layout (sizes in bytes)
        # 0x00  DOLayout          0x18
        # 0x18  PositionHeader    0x08
        # 0x20  ColorHeader       0x08
        # 0x28  UV_Header × M_uv  M_uv × 0x10
        # ...   NormalHeader      0x0c
        # ...   DisplayHeader     0x0c
        # ...   DisplayState × n_ds  n_ds × 0x10
        POS_OFF = 0x18
        COL_OFF = 0x20
        UV_OFF  = 0x28
        NOR_OFF = UV_OFF  + M_uv * 0x10
        DSP_OFF = NOR_OFF + 0x0c
        DS_OFF  = DSP_OFF + 0x0c
        HDR_END = DS_OFF  + n_ds * 0x10

        cursor = HDR_END

        # --- mesh name string ---
        name_bytes = sub.mesh_name.encode('ascii', errors='replace') + b'\x00'
        name_off   = cursor
        cursor    += len(name_bytes)

        # --- per-UV palette name strings ---
        pal_name_offs       = []
        pal_name_bytes_list = []
        for uv in sub.uv_channels:
            pal_b = (uv.palette_name or '').encode('ascii', errors='replace') + b'\x00'
            pal_name_offs.append(cursor)
            pal_name_bytes_list.append(pal_b)
            cursor += len(pal_b)

        # Skinned meshes (cc=6): align position data to 32-byte boundary
        # (Wii Broadway dcbz requirement — SKN deformer write target).
        # Non-skinned meshes: no alignment required (original data is unaligned).
        is_interleaved = (sub.vertex_comp_count == 6)
        if is_interleaved:
            cursor = _align32(cursor)

        # --- position raw data ---
        pos_data     = _align4(sub.vertex_data)
        pos_data_off = cursor
        cursor      += len(pos_data)

        # --- color raw data (all channels share one buffer; use channel-0) ---
        col_data     = b''
        col_data_off = 0
        if sub.color_channels:
            col_data     = _align4(sub.color_channels[0].color_data)
            col_data_off = cursor
            cursor      += len(col_data)

        # --- UV raw data (one buffer per channel) ---
        uv_data_offs  = []
        uv_data_list  = []
        for uv in sub.uv_channels:
            uv_b = _align4(uv.uv_data)
            uv_data_offs.append(cursor)
            uv_data_list.append(uv_b)
            cursor += len(uv_b)

        # --- normal raw data ---
        # Skinned (interleaved) meshes use cc=6: pos+normal are packed together
        # in the position buffer.  NorHdr.rawPtr points 6 bytes into that
        # buffer; no separate normal data block is stored.
        nor_data     = b''
        nor_data_off = 0
        if is_interleaved and sub.normal_buffer:
            comp_size    = _vb_comp_size(sub.vertex_quantize_info)
            nor_data_off = pos_data_off + comp_size * 3
        elif sub.normal_buffer and sub.normal_buffer.normal_data:
            nor_data     = _align4(sub.normal_buffer.normal_data)
            nor_data_off = cursor
            cursor      += len(nor_data)

        # --- per-display-state primitive list data ---
        # Prim lists (GX display lists) MUST be 32-byte aligned — the GPU
        # command processor reads them via DMA in 32-byte bursts.
        pl_offs       = []
        pl_bytes_list = []
        for ds in sub.draw_states:
            if ds.prim_list_data:
                cursor = _align32(cursor)
                pl_b = _align4(ds.prim_list_data)
                pl_offs.append(cursor)
                pl_bytes_list.append(pl_b)
                cursor += len(pl_b)
            else:
                pl_offs.append(0)
                pl_bytes_list.append(b'')

        blob_size = cursor

        # Phase 2: derive counts from buffer sizes
        pos_count = _vertex_count(sub.vertex_data, sub.vertex_comp_count, sub.vertex_quantize_info)

        col_count = 0
        if sub.color_channels:
            cc0       = sub.color_channels[0]
            col_count = _color_count(cc0.color_data, cc0.quantize_info)

        uv_counts = [
            _vertex_count(uv.uv_data, uv.comp_count, uv.quantize_info)
            for uv in sub.uv_channels
        ]

        nor_count = 0
        if is_interleaved and sub.normal_buffer:
            nor_count = pos_count
        elif sub.normal_buffer and sub.normal_buffer.normal_data:
            nb        = sub.normal_buffer
            nor_count = _vertex_count(nb.normal_data, nb.comp_count, nb.quantize_info)

        sub_layouts.append({
            'is_interleaved':      is_interleaved,
            'sub':                 sub,
            'M_uv':                M_uv,
            'n_ds':                n_ds,
            'POS_OFF':             POS_OFF,
            'COL_OFF':             COL_OFF,
            'UV_OFF':              UV_OFF,
            'NOR_OFF':             NOR_OFF,
            'DSP_OFF':             DSP_OFF,
            'DS_OFF':              DS_OFF,
            'blob_size':           blob_size,
            'name_off':            name_off,
            'name_bytes':          name_bytes,
            'pal_name_offs':       pal_name_offs,
            'pal_name_bytes_list': pal_name_bytes_list,
            'pos_data':            pos_data,
            'pos_data_off':        pos_data_off,
            'pos_count':           pos_count,
            'col_data':            col_data,
            'col_data_off':        col_data_off,
            'col_count':           col_count,
            'uv_data_offs':        uv_data_offs,
            'uv_data_list':        uv_data_list,
            'uv_counts':           uv_counts,
            'nor_data':            nor_data,
            'nor_data_off':        nor_data_off,
            'nor_count':           nor_count,
            'pl_offs':             pl_offs,
            'pl_bytes_list':       pl_bytes_list,
        })

    # -------------------------------------------------------------------------
    # GPL-level address layout
    # -------------------------------------------------------------------------

    GEO_DESC_OFF  = GPL_HDR_SIZE          # 0x14
    GEO_DESC_SIZE = N * 8
    # Blob starts must be 32-byte aligned so that DOLayout-relative prim list
    # offsets (which are 32-aligned within the blob) are also 32-aligned in
    # GPL-absolute terms (GX DMA requirement for display lists).
    BLOBS_START   = _align32(GEO_DESC_OFF + GEO_DESC_SIZE)

    blob_gpl_offs = []
    cursor = BLOBS_START
    for lay in sub_layouts:
        blob_gpl_offs.append(cursor)
        cursor += lay['blob_size']
        cursor = _align32(cursor)  # next blob 32-aligned for prim list alignment

    # GPL-relative byte offset of each submesh's raw position data array.
    # Computed here so the SKN builder can use them directly instead of
    # re-parsing gpl_bytes (blob_gpl_offs[i] is the DOLayout base; pos_data_off
    # is DOLayout-relative, so their sum is the GPL-relative pos array offset).
    pos_gpl_offsets = [
        blob_gpl_offs[i] + sub_layouts[i]['pos_data_off']
        for i in range(N)
    ]

    user_data_gpl_off = cursor
    user_data_bytes   = parsed.gpl_user_data if parsed.gpl_user_data else b''
    total_size        = cursor + len(user_data_bytes)

    # -------------------------------------------------------------------------
    # Assembly
    # -------------------------------------------------------------------------

    gpl = bytearray(total_size)

    # GPL Header (0x14 bytes)
    _s.pack_into('>I', gpl, 0x00, GPL_MAGIC)
    _s.pack_into('>I', gpl, 0x04, parsed.gpl_user_data_len)
    _s.pack_into('>I', gpl, 0x08, user_data_gpl_off if user_data_bytes else 0)
    _s.pack_into('>I', gpl, 0x0c, N)
    _s.pack_into('>I', gpl, 0x10, GEO_DESC_OFF)

    for i, (lay, gpl_b) in enumerate(zip(sub_layouts, blob_gpl_offs)):
        sub     = lay['sub']
        POS_OFF = lay['POS_OFF'];  COL_OFF = lay['COL_OFF']
        UV_OFF  = lay['UV_OFF'];   NOR_OFF = lay['NOR_OFF']
        DSP_OFF = lay['DSP_OFF'];  DS_OFF  = lay['DS_OFF']
        M_uv    = lay['M_uv'];     n_ds    = lay['n_ds']

        # GEO Descriptor (GPL-relative pointers)
        desc = GEO_DESC_OFF + i * 8
        _s.pack_into('>I', gpl, desc,     gpl_b)                       # DOLayoutPtr
        _s.pack_into('>I', gpl, desc + 4, gpl_b + lay['name_off'])     # namePtr

        # DOLayout (DOLayout-relative sub-struct pointers)
        _s.pack_into('>I', gpl, gpl_b + 0x00, POS_OFF)
        _s.pack_into('>I', gpl, gpl_b + 0x04, COL_OFF)
        _s.pack_into('>I', gpl, gpl_b + 0x08, UV_OFF)
        _s.pack_into('>I', gpl, gpl_b + 0x0c, NOR_OFF)
        _s.pack_into('>I', gpl, gpl_b + 0x10, DSP_OFF)
        _s.pack_into('B',  gpl, gpl_b + 0x14, M_uv)
        # 0x15–0x17: padding (zero, already initialised)

        # Position Header (DOLayout-relative rawPtr)
        _s.pack_into('>I', gpl, gpl_b + POS_OFF + 0x00, lay['pos_data_off'])
        _s.pack_into('>H', gpl, gpl_b + POS_OFF + 0x04, lay['pos_count'])
        _s.pack_into('B',  gpl, gpl_b + POS_OFF + 0x06, sub.vertex_quantize_info)
        _s.pack_into('B',  gpl, gpl_b + POS_OFF + 0x07, sub.vertex_comp_count)

        # Color Header
        if sub.color_channels:
            cc0 = sub.color_channels[0]
            _s.pack_into('>I', gpl, gpl_b + COL_OFF + 0x00, lay['col_data_off'])
            _s.pack_into('>H', gpl, gpl_b + COL_OFF + 0x04, lay['col_count'])
            _s.pack_into('B',  gpl, gpl_b + COL_OFF + 0x06, cc0.quantize_info)
            _s.pack_into('B',  gpl, gpl_b + COL_OFF + 0x07, cc0.comp_count)
        # else: all-zero (zero-initialised array)

        # UV Headers (M_uv × 0x10)
        for j, uv in enumerate(sub.uv_channels):
            uv_off = gpl_b + UV_OFF + j * 0x10
            _s.pack_into('>I', gpl, uv_off + 0x00, lay['uv_data_offs'][j])   # textureCoordsArrPtr
            _s.pack_into('>H', gpl, uv_off + 0x04, lay['uv_counts'][j])
            _s.pack_into('B',  gpl, uv_off + 0x06, uv.quantize_info)
            _s.pack_into('B',  gpl, uv_off + 0x07, uv.comp_count)
            _s.pack_into('>I', gpl, uv_off + 0x08, lay['pal_name_offs'][j])  # paletteNamePtr
            _s.pack_into('>I', gpl, uv_off + 0x0c, 0)                        # palettePtr (runtime)

        # Normal (Lighting) Header
        # Interleaved: rawPtr already set to pos_data_off+6 (no separate buffer).
        # Non-interleaved: rawPtr points to the separate normal data block.
        if sub.normal_buffer and (lay['is_interleaved'] or sub.normal_buffer.normal_data):
            nb = sub.normal_buffer
            _s.pack_into('>I', gpl, gpl_b + NOR_OFF + 0x00, lay['nor_data_off'])
            _s.pack_into('>H', gpl, gpl_b + NOR_OFF + 0x04, lay['nor_count'])
            _s.pack_into('B',  gpl, gpl_b + NOR_OFF + 0x06, nb.quantize_info)
            _s.pack_into('B',  gpl, gpl_b + NOR_OFF + 0x07, nb.comp_count)
            _s.pack_into('>f', gpl, gpl_b + NOR_OFF + 0x08, nb.ambient_pct)
        # else: all-zero (skinned mesh: normalsPtr = 0)

        # Display Header
        first_pl = next(
            (lay['pl_offs'][k] for k, ds in enumerate(sub.draw_states) if ds.prim_list_data),
            0,
        )
        _s.pack_into('>I', gpl, gpl_b + DSP_OFF + 0x00, first_pl)   # primitivePtr (not used directly)
        _s.pack_into('>I', gpl, gpl_b + DSP_OFF + 0x04, DS_OFF)     # displayStatePtr
        _s.pack_into('>H', gpl, gpl_b + DSP_OFF + 0x08, n_ds)
        # 0x0a–0x0b: padding

        # Display States (n_ds × 0x10)
        for k, ds in enumerate(sub.draw_states):
            ds_off  = gpl_b + DS_OFF + k * 0x10
            setting = _s.unpack('>I', _setting_bytes(ds.shader_mode))[0]
            _s.pack_into('B',  gpl, ds_off + 0x00, ds.display_state_id)
            # bytes 0x01–0x03: renderer parameters (NOT padding)
            pad = ds.display_state_pad_bytes
            gpl[ds_off + 0x01 : ds_off + 0x04] = pad[:3] if len(pad) >= 3 else pad.ljust(3, b'\x00')
            _s.pack_into('>I', gpl, ds_off + 0x04, setting)
            _s.pack_into('>I', gpl, ds_off + 0x08, lay['pl_offs'][k])
            _s.pack_into('>I', gpl, ds_off + 0x0c,
                         len(ds.prim_list_data) if ds.prim_list_data else 0)

        # Raw data payloads  (DOLayout-relative offsets, written into gpl at gpl_b + off)
        def _put(rel_off: int, data: bytes) -> None:
            gpl[gpl_b + rel_off : gpl_b + rel_off + len(data)] = data

        _put(lay['name_off'], lay['name_bytes'])
        for pal_off, pal_b in zip(lay['pal_name_offs'], lay['pal_name_bytes_list']):
            _put(pal_off, pal_b)
        _put(lay['pos_data_off'], lay['pos_data'])
        if lay['col_data']:
            _put(lay['col_data_off'], lay['col_data'])
        for uv_off, uv_b in zip(lay['uv_data_offs'], lay['uv_data_list']):
            _put(uv_off, uv_b)
        if lay['nor_data']:
            _put(lay['nor_data_off'], lay['nor_data'])
        for pl_off, pl_b in zip(lay['pl_offs'], lay['pl_bytes_list']):
            if pl_b:
                _put(pl_off, pl_b)

    # User data (appended after all submesh blobs)
    if user_data_bytes:
        gpl[user_data_gpl_off : user_data_gpl_off + len(user_data_bytes)] = user_data_bytes

    return GPLBuildResult(gpl_bytes=bytes(gpl), pos_gpl_offsets=pos_gpl_offsets)


def CloneGPL(model_offset: int, model_length: int) -> bytes:
    """Clone the GPL section verbatim from INPUT dt_na.dat.

    Reads the model block header to determine GPL boundaries, then returns
    the raw GPL bytes unchanged.  No pointer fixups needed (all internal
    GPL pointers are GPL-section-relative or DOLayout-relative).
    """
    import struct as _s
    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(model_offset)
        hdr = f.read(0x20)
        gpl_off = _s.unpack_from('>I', hdr, 0x04)[0]
        act_off = _s.unpack_from('>I', hdr, 0x08)[0]
        tex_off = _s.unpack_from('>I', hdr, 0x0c)[0]
        skn_off = _s.unpack_from('>I', hdr, 0x10)[0]
        # GPL ends where the next present section starts
        next_off = act_off or tex_off or skn_off or model_length
        gpl_len = next_off - gpl_off
        f.seek(model_offset + gpl_off)
        data = f.read(gpl_len)
    print(f"    [CloneGPL] {gpl_len:,} bytes from block+0x{gpl_off:X}")
    return data


def _gpl_pos_offsets_from_bytes(gpl_bytes: bytes) -> list[int]:
    """Extract per-submesh GPL-relative position-data offsets from raw GPL bytes.

    Parses the GPL header and GEO descriptors to find each submesh's
    DOLayout, then reads the PositionHeader rawPtr field.  Returns a list
    of GPL-section-relative byte offsets (one per submesh).

    This is used when the GPL section is cloned (not rebuilt) and we still
    need the pos_gpl_offsets metadata for the SKN builder.
    """
    import struct as _s
    # GPL header: +0x0c = N (submesh count), +0x10 = descriptorPtr
    n_submeshes = _s.unpack_from('>I', gpl_bytes, 0x0c)[0]
    desc_ptr    = _s.unpack_from('>I', gpl_bytes, 0x10)[0]

    offsets = []
    for i in range(n_submeshes):
        # GEO descriptor: 8 bytes each → first uint32 = DOLayout GPL-rel ptr
        blob_ptr = _s.unpack_from('>I', gpl_bytes, desc_ptr + i * 8)[0]
        # DOLayout +0x00 = posHeaderPtr (DOLayout-relative)
        pos_hdr_ptr = _s.unpack_from('>I', gpl_bytes, blob_ptr)[0]
        # PositionHeader +0x00 = raw data array ptr (DOLayout-relative)
        pos_arr_ptr = _s.unpack_from('>I', gpl_bytes, blob_ptr + pos_hdr_ptr)[0]
        # GPL-relative offset = DOLayout base + pos_arr_ptr
        offsets.append(blob_ptr + pos_arr_ptr)

    return offsets


def BuildACTBoneHierarchy(parsed: SluggieParsed) -> bytes:
    """Return the ACT (Bone Hierarchy) section bytes.

    Bone hierarchy and animation data are not modified by hammerspace, so
    this reads the original ACT block verbatim from INPUT dt_na.dat using
    the section offsets stored in the model-block file header.

    Returns the raw ACT section bytes copied from the input file,
    or b'' if the model has no ACT section.
    """
    import struct as _s

    if not parsed.model_offset:
        return b''

    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(parsed.model_offset)
        hdr = f.read(0x20)
        act_off = _s.unpack_from('>I', hdr, 0x08)[0]
        tex_off = _s.unpack_from('>I', hdr, 0x0c)[0]
        if not act_off or not tex_off:
            return b''
        act_len = tex_off - act_off
        f.seek(parsed.model_offset + act_off)
        return f.read(act_len)


def CloneACT(model_offset: int, model_length: int) -> bytes:
    """Clone the ACT section verbatim from INPUT dt_na.dat.

    Returns the raw ACT bytes unchanged, or b'' if the model has no ACT section.
    """
    import struct as _s
    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(model_offset)
        hdr = f.read(0x20)
        act_off = _s.unpack_from('>I', hdr, 0x08)[0]
        if not act_off:
            print("    [CloneACT] No ACT section")
            return b''
        tex_off = _s.unpack_from('>I', hdr, 0x0c)[0]
        skn_off = _s.unpack_from('>I', hdr, 0x10)[0]
        next_off = tex_off or skn_off or model_length
        act_len = next_off - act_off
        f.seek(model_offset + act_off)
        data = f.read(act_len)
    print(f"    [CloneACT] {act_len:,} bytes from block+0x{act_off:X}")
    return data


def BuildTEXTextureData(parsed: SluggieParsed) -> bytes:
    """Return the TEX (Texture Data) section bytes.

    Texture patching is not supported; this reads the original TEX block
    verbatim from INPUT dt_na.dat using the section offsets stored in the
    model-block file header.

    Returns the raw TEX section bytes copied from the input file,
    or b'' if the model has no TEX section.
    """
    import struct as _s

    if not parsed.model_offset:
        return b''

    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(parsed.model_offset)
        hdr = f.read(0x20)
        tex_off = _s.unpack_from('>I', hdr, 0x0c)[0]
        skn_off = _s.unpack_from('>I', hdr, 0x10)[0]
        if not tex_off:
            return b''
        tex_len = (skn_off if skn_off else parsed.model_length) - tex_off
        f.seek(parsed.model_offset + tex_off)
        return f.read(tex_len)


def CloneTEX(model_offset: int, model_length: int) -> bytes:
    """Clone the TEX section verbatim from INPUT dt_na.dat.

    Returns the raw TEX bytes unchanged, or b'' if the model has no TEX section.
    """
    import struct as _s
    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(model_offset)
        hdr = f.read(0x20)
        tex_off = _s.unpack_from('>I', hdr, 0x0c)[0]
        if not tex_off:
            print("    [CloneTEX] No TEX section")
            return b''
        skn_off = _s.unpack_from('>I', hdr, 0x10)[0]
        trailing_offsets = [_s.unpack_from('>I', hdr, offset)[0] for offset in (0x14, 0x18, 0x1c)]
        next_off = min(
            [offset for offset in [skn_off, *trailing_offsets] if offset > tex_off]
            + [model_length]
        )
        tex_len = next_off - tex_off
        f.seek(model_offset + tex_off)
        data = f.read(tex_len)
    print(f"    [CloneTEX] {tex_len:,} bytes from block+0x{tex_off:X}")
    return data


def BuildSKNSkinningDataCopyOnly(parsed: SluggieParsed, gpl_result: GPLBuildResult) -> bytes:
    """Return the SKN (Skinning Data) section bytes verbatim from INPUT dt_na.dat,
    with memClrPtr patched to reflect the new GPL position-data offset.

    Reads the original SKN block from the input file using the section offset
    stored in the model-block file header.  After copying, memClrPtr at SKN
    header offset 0x14 is recomputed as:
        new_memClrPtr = gpl_result.pos_gpl_offsets[0] + min(all gplVertexArr)

    Returns the raw SKN section bytes (patched), or b'' if the model has no
    SKN section.
    """
    import struct as _s

    if not parsed.model_offset:
        return b''

    original_pos_gpl_rel = 0
    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(parsed.model_offset)
        hdr = f.read(0x20)
        skn_off = _s.unpack_from('>I', hdr, 0x10)[0]
        if not skn_off:
            return b''
        skn_len = parsed.model_length - skn_off
        f.seek(parsed.model_offset + skn_off)
        skn = bytearray(f.read(skn_len))

        # Read the original GPL's sub0 pos_gpl_rel so we can compute the
        # correct memClrPtr delta.  GPL section is always at model_offset+0x20.
        # GPL header layout: +0x10 = descriptorPtr.
        # Descriptor[0]: +0x00 = blob0_ptr (DOLayout-relative).
        # DOLayout[0]: +0x00 = posHeaderPtr (blob-relative).
        # DOPositionHeader: +0x00 = positionArrPtr (DOLayout-relative).
        # original_pos_gpl_rel = blob0_ptr + positionArrPtr.
        gpl_base = parsed.model_offset + 0x20
        f.seek(gpl_base + 0x10)
        desc_ptr  = _s.unpack_from('>I', f.read(4))[0]
        f.seek(gpl_base + desc_ptr)
        blob0_ptr = _s.unpack_from('>I', f.read(4))[0]
        f.seek(gpl_base + blob0_ptr)
        pos_hdr_ptr = _s.unpack_from('>I', f.read(4))[0]
        f.seek(gpl_base + blob0_ptr + pos_hdr_ptr)
        pos_arr_ptr = _s.unpack_from('>I', f.read(4))[0]
        original_pos_gpl_rel = blob0_ptr + pos_arr_ptr

    # Patch memClrPtr (SKN header offset 0x14) to match the new GPL layout.
    # The memClrPtr encodes a pos-data-relative delta that must be preserved
    # when the GPL section is rebuilt at a different layout.
    # Formula: new_memClrPtr = new_pos_gpl_rel + (old_memClrPtr - old_pos_gpl_rel)
    if gpl_result.pos_gpl_offsets and original_pos_gpl_rel:
        old_memClrPtr = _s.unpack_from('>I', skn, 0x14)[0]
        pos_relative_delta = old_memClrPtr - original_pos_gpl_rel
        new_memClrPtr = gpl_result.pos_gpl_offsets[0] + pos_relative_delta
        _s.pack_into('>I', skn, 0x14, new_memClrPtr)
        print(f'    [SKN] memClrPtr patched: 0x{old_memClrPtr:08X} → 0x{new_memClrPtr:08X}'
              f'  (orig_pos_gpl_rel=0x{original_pos_gpl_rel:X} delta=0x{pos_relative_delta:X})')

    return bytes(skn)


def _compute_original_pos_gpl_rel(parsed: SluggieParsed) -> int:
    """Read the original GPL's submesh 0 position-array GPL-relative offset.

    Returns the GPL-section-relative offset of the position data array for
    submesh 0, or 0 if it cannot be determined.
    """
    import struct as _s
    if not parsed.model_offset:
        return 0
    with open(hh.INPUT_DAT, 'rb') as f:
        gpl_base = parsed.model_offset + 0x20
        f.seek(gpl_base + 0x10)
        desc_ptr = _s.unpack_from('>I', f.read(4))[0]
        f.seek(gpl_base + desc_ptr)
        blob0_ptr = _s.unpack_from('>I', f.read(4))[0]
        f.seek(gpl_base + blob0_ptr)
        pos_hdr_ptr = _s.unpack_from('>I', f.read(4))[0]
        f.seek(gpl_base + blob0_ptr + pos_hdr_ptr)
        pos_arr_ptr = _s.unpack_from('>I', f.read(4))[0]
        return blob0_ptr + pos_arr_ptr


def BuildSKNSkinningData(parsed: SluggieParsed, gpl_result: GPLBuildResult) -> bytes:
    """Build the SKN (Skinning Data) section.

    Must be called after BuildGPLMeshData because memClrPtr in the SKN header
    is GPL-section-relative and must point to wherever submesh 0's position
    data lands in the new GPL layout.

    gplVertexArr / gplDestArr values are pos-data-relative (byte offsets from
    the start of submesh 0's position buffer) and are preserved verbatim from
    parsed.skinning — they do not change when the model block is relocated or
    the GPL section is rebuilt with the same vertex data.

    The only field that requires recomputation is memClrPtr:
        new_memClrPtr = gpl_result.pos_gpl_offsets[0] + min(all gplVertexArr)

    NOTE: This function builds the SKN section from scratch and does NOT
    include any trailing sub-sections (ptr6/ptr7/ptr8 data that lives after
    the SKN section in the original block).  If the original model has non-
    zero ptr6/ptr7/ptr8 pointers, that data must be appended separately to
    the returned bytes, or BuildSKNSkinningDataCopyOnly should be used
    instead.

    Returns the complete SKN section as a byte string, or b'' for non-skinned
    models.
    """
    import struct as _s

    skn = parsed.skinning
    if not skn:
        return b''

    # -------------------------------------------------------------------------
    # Local helpers
    # -------------------------------------------------------------------------

    _ALIGN = 32  # Wii CPU cache-line size; all source/weight/dest arrays must be 32-byte aligned

    def _align(data: bytes) -> bytes:
        r = len(data) % _ALIGN
        return data + b'\x00' * ((_ALIGN - r) % _ALIGN)

    def _vb_comp_size(quant_info: int) -> int:
        return 4 if (quant_info >> 4) in (4, 7, 0xa) else 2

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
    # Round up to next 32-byte boundary so the first variable data array is aligned
    VAR_DATA_OFF  = (SKACC_ARR_OFF + n_acc * SKACC_SIZE + _ALIGN - 1) & ~(_ALIGN - 1)

    # -------------------------------------------------------------------------
    # Phase SKN-1: variable-data layout pass
    # All offsets are SKN-section-relative (absolute within the final SKN bytes).
    # Layout order: [SK1 srcs] [SK2 src+wt per pair] [SKAcc src+destIdx+wt per triple] [flush]
    # -------------------------------------------------------------------------

    var_data   = bytearray()
    var_cursor = VAR_DATA_OFF

    # Per SK1: one source (bind-pose pos+normal) array
    sk1_src_off = []
    for sk in skn.sk1s:
        sk1_src_off.append(var_cursor)
        # Ensure the source array includes vertex_offset prefix bytes.
        # The game reads from srcArrPtr + vertex_offset, so the first
        # vertex_offset bytes are skipped padding.  SkinDataEdited from the
        # Blender exporter stores only the actual vertex data (no prefix),
        # so we must prepend zeros when the blob is too short.
        bp = sk.bind_pose_data
        expected_len = sk.vertex_offset + sk.vertex_cnt * vertex_stride
        if len(bp) < expected_len:
            bp = b'\x00' * (expected_len - len(bp)) + bp
        chunk = _align(bp)
        var_data.extend(chunk)
        var_cursor += len(chunk)

    # Per SK2: source array then weight array
    sk2_src_off = []
    sk2_wt_off  = []
    for sk in skn.sk2s:
        sk2_src_off.append(var_cursor)
        bp = sk.bind_pose_data
        expected_len = sk.vertex_offset + sk.vertex_cnt * vertex_stride
        if len(bp) < expected_len:
            bp = b'\x00' * (expected_len - len(bp)) + bp
        chunk = _align(bp)
        var_data.extend(chunk)
        var_cursor += len(chunk)

        sk2_wt_off.append(var_cursor)
        chunk = _align(sk.weight_data)
        var_data.extend(chunk)
        var_cursor += len(chunk)

    # Per SKAcc: source, dest-index, weight arrays
    acc_src_off  = []
    acc_dest_off = []
    acc_wt_off   = []
    for sk in skn.sk_accs:
        acc_src_off.append(var_cursor)
        chunk = _align(sk.bind_pose_data)
        var_data.extend(chunk)
        var_cursor += len(chunk)

        acc_dest_off.append(var_cursor)
        chunk = _align(sk.dest_index_data)
        var_data.extend(chunk)
        var_cursor += len(chunk)

        acc_wt_off.append(var_cursor)
        chunk = _align(sk.weight_data)
        var_data.extend(chunk)
        var_cursor += len(chunk)

    # Flush index array — read verbatim from INPUT dat (content unchanged)
    flush_off   = 0
    flush_bytes = b''
    if skn.flush_ind_size and skn.flush_ind_absolute_ptr:
        with open(hh.INPUT_DAT, 'rb') as _f:
            _f.seek(skn.flush_ind_absolute_ptr)
            flush_bytes = _f.read(skn.flush_ind_size * 2)
        flush_off = var_cursor
        chunk = _align(flush_bytes)
        var_data.extend(chunk)
        var_cursor += len(chunk)

    # -------------------------------------------------------------------------
    # Phase SKN-2: struct headers
    # gplVertexArr / gplDestArr are preserved verbatim — they are pos-data-
    # relative byte offsets and do not change when the block is relocated.
    # -------------------------------------------------------------------------

    # SK1 structs (0x40 bytes each)
    sk1_bytes = bytearray(n_sk1 * SK1_SIZE)
    for i, sk in enumerate(skn.sk1s):
        b = i * SK1_SIZE
        # +0x00..+0x2f: matrix placeholder — zeroed (runtime fills each frame)
        _s.pack_into('>I', sk1_bytes, b + 0x30, sk1_src_off[i])         # srcArrPtr  (SKN-rel)
        _s.pack_into('>I', sk1_bytes, b + 0x34, sk.gpl_vertex_arr_value) # gplVertexArr (verbatim)
        _s.pack_into('>H', sk1_bytes, b + 0x38, sk.bone_index)
        _s.pack_into('>H', sk1_bytes, b + 0x3a, sk.vertex_cnt)
        _s.pack_into('B',  sk1_bytes, b + 0x3c, sk.vertex_offset)
        # +0x3d..+0x3f: padding — already zero

    # SK2 structs (0x74 bytes each)
    sk2_bytes = bytearray(n_sk2 * SK2_SIZE)
    for i, sk in enumerate(skn.sk2s):
        b = i * SK2_SIZE
        # +0x00..+0x5f: two matrix placeholders — zeroed
        _s.pack_into('>I', sk2_bytes, b + 0x60, sk2_src_off[i])         # srcArrPtr
        _s.pack_into('>I', sk2_bytes, b + 0x64, sk2_wt_off[i])          # weightArrPtr
        _s.pack_into('>I', sk2_bytes, b + 0x68, sk.gpl_vertex_arr_value) # gplVertexArr (verbatim)
        _s.pack_into('>H', sk2_bytes, b + 0x6c, sk.bone_index1)
        _s.pack_into('>H', sk2_bytes, b + 0x6e, sk.bone_index2)
        _s.pack_into('>H', sk2_bytes, b + 0x70, sk.vertex_cnt)
        _s.pack_into('B',  sk2_bytes, b + 0x72, sk.vertex_offset)
        # +0x73: padding — already zero

    # SKAcc structs (0x44 bytes each)
    acc_bytes = bytearray(n_acc * SKACC_SIZE)
    for i, sk in enumerate(skn.sk_accs):
        b = i * SKACC_SIZE
        # +0x00..+0x2f: matrix placeholder — zeroed
        _s.pack_into('>I', acc_bytes, b + 0x30, acc_src_off[i])       # srcArrPtr
        _s.pack_into('>I', acc_bytes, b + 0x34, acc_dest_off[i])      # destIdxArrPtr
        _s.pack_into('>I', acc_bytes, b + 0x38, sk.gpl_dest_arr_value) # gplDestArr (verbatim)
        _s.pack_into('>I', acc_bytes, b + 0x3c, acc_wt_off[i])        # weightArrPtr
        _s.pack_into('>H', acc_bytes, b + 0x40, sk.bone_index)
        _s.pack_into('>H', acc_bytes, b + 0x42, sk.vertex_cnt)

    # -------------------------------------------------------------------------
    # Phase SKN-3: memClrPtr / memClrSize
    #
    # memClrPtr is GPL-section-relative and points to the destination buffer
    # where runtime CPU skinning writes transformed vertex data.  It is NOT
    # simply pos_data_start + min(gplVertexArr) — those are source offsets.
    #
    # For an unchanged-geometry rebuild (same vertex counts), we preserve the
    # original memClrPtr and memClrSize from the sluggie metadata, relocated
    # relative to the new GPL position data offset.  The formula matches
    # BuildSKNSkinningDataCopyOnly:
    #   delta = original_memClrPtr_gpl_rel - original_pos_data_gpl_rel
    #   new_memClrPtr = new_pos_data_gpl_rel + delta
    #
    # Since gpl_result rebuilds the layout identically, pos_gpl_offsets[0]
    # equals the original pos-data GPL-rel offset, so the delta is preserved.
    # -------------------------------------------------------------------------

    # Original memClrPtr (GPL-relative)
    orig_memClrPtr_gpl_rel = skn.mem_clr_absolute_ptr - skn.gpl_base_offset if skn.gpl_base_offset else 0
    orig_memClrSize = skn.mem_clr_size

    # Relocate: new_pos_gpl_offsets[0] + (orig_memClrPtr_gpl_rel - orig_pos_gpl_rel)
    # Since the GPL rebuild reproduces the same layout, orig_pos_gpl_rel == pos_gpl_offsets[0],
    # so new_memClrPtr == orig_memClrPtr_gpl_rel.  But we compute it properly for future
    # extensibility when GPL layout might differ.
    if gpl_result.pos_gpl_offsets and skn.gpl_base_offset:
        # Compute original pos-data GPL-relative offset from the input file
        # (same logic as CopyOnly)
        _orig_pos_gpl_rel = _compute_original_pos_gpl_rel(parsed)
        if _orig_pos_gpl_rel:
            delta = orig_memClrPtr_gpl_rel - _orig_pos_gpl_rel
        else:
            delta = orig_memClrPtr_gpl_rel - gpl_result.pos_gpl_offsets[0]
        new_memClrPtr = gpl_result.pos_gpl_offsets[0] + delta
    else:
        new_memClrPtr = orig_memClrPtr_gpl_rel

    new_memClrSize = orig_memClrSize

    # -------------------------------------------------------------------------
    # Phase SKN-4: SKN header + full assembly
    # -------------------------------------------------------------------------

    skn_hdr = bytearray(SKN_HDR_SIZE)
    _s.pack_into('>H', skn_hdr, 0x00, n_sk1)
    _s.pack_into('>H', skn_hdr, 0x02, n_sk2)
    _s.pack_into('>H', skn_hdr, 0x04, n_acc)
    _s.pack_into('B',  skn_hdr, 0x06, skn.quantize_info)
    # 0x07: padding
    _s.pack_into('>I', skn_hdr, 0x08, SK1_ARR_OFF)    # sk1Ptr  (SKN-rel, points to array start)
    _s.pack_into('>I', skn_hdr, 0x0c, SK2_ARR_OFF)    # sk2Ptr
    _s.pack_into('>I', skn_hdr, 0x10, SKACC_ARR_OFF)  # skAccPtr
    _s.pack_into('>I', skn_hdr, 0x14, new_memClrPtr)
    _s.pack_into('>I', skn_hdr, 0x18, new_memClrSize)
    _s.pack_into('>I', skn_hdr, 0x1c, flush_off if flush_bytes else 0)  # flushIndPtr
    _s.pack_into('>I', skn_hdr, 0x20, skn.flush_ind_size)

    # Insert alignment padding between struct arrays and variable data so that
    # var_data[0] lands at SKN offset VAR_DATA_OFF (where all pointers point).
    struct_end = SKN_HDR_SIZE + n_sk1 * SK1_SIZE + n_sk2 * SK2_SIZE + n_acc * SKACC_SIZE
    align_pad = VAR_DATA_OFF - struct_end
    skn_core = bytes(skn_hdr) + bytes(sk1_bytes) + bytes(sk2_bytes) + bytes(acc_bytes) + b'\x00' * align_pad + bytes(var_data)

    # Append trailing sub-sections (ptr6/ptr7/ptr8 data) from INPUT dt_na.dat.
    # These live between the end of the SKN section proper and the end of the
    # model block.  BuildHEADERModelBlock recomputes the header pointers using
    # (original_ptr - original_skn_off), so we must place the trailing data at
    # the SAME relative offset from our new SKN start as in the original file.
    trailing = b''
    if parsed.model_offset:
        import struct as _ts
        with open(hh.INPUT_DAT, 'rb') as _f:
            _f.seek(parsed.model_offset)
            orig_hdr = _f.read(0x20)
            orig_skn_off = _ts.unpack_from('>I', orig_hdr, 0x10)[0]
            if orig_skn_off:
                # Find the earliest ptr6/ptr7/ptr8 that falls after SKN start.
                trailing_ptrs = []
                for fo in (0x14, 0x18, 0x1c):
                    p = _ts.unpack_from('>I', orig_hdr, fo)[0]
                    if p and p >= orig_skn_off:
                        trailing_ptrs.append(p)
                if trailing_ptrs:
                    trail_start = min(trailing_ptrs)
                    trail_offset_in_skn = trail_start - orig_skn_off
                    trail_len = parsed.model_length - trail_start
                    _f.seek(parsed.model_offset + trail_start)
                    trail_data = _f.read(trail_len)
                    # Pad skn_core to reach the correct offset for trailing data.
                    if len(skn_core) < trail_offset_in_skn:
                        skn_core += b'\x00' * (trail_offset_in_skn - len(skn_core))
                    elif len(skn_core) > trail_offset_in_skn:
                        # SKN rebuilt larger than original — trailing data must
                        # be relocated.  Append directly after skn_core.
                        trail_offset_in_skn = len(skn_core)
                    trailing = trail_data
                    print(f'    [SKN] Trailing section appended at SKN+0x{trail_offset_in_skn:X} '
                          f'({len(trail_data)} bytes)')

    return skn_core + trailing


def CloneSKN(model_offset: int, model_length: int) -> bytes:
    """Clone the SKN section verbatim, excluding ptr6/ptr7/ptr8 sections."""
    import struct as _s
    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(model_offset)
        hdr = f.read(0x20)
        skn_off = _s.unpack_from('>I', hdr, 0x10)[0]
        if not skn_off:
            print("    [CloneSKN] No SKN section")
            return b''
        trailing_offsets = [_s.unpack_from('>I', hdr, offset)[0] for offset in (0x14, 0x18, 0x1c)]
        skn_end = min(
            [offset for offset in trailing_offsets if offset > skn_off]
            + [model_length]
        )
        skn_len = skn_end - skn_off
        f.seek(model_offset + skn_off)
        data = f.read(skn_len)
    print(f"    [CloneSKN] {skn_len:,} bytes from block+0x{skn_off:X}")
    return data


def CloneTrailingSections(model_offset: int, model_length: int) -> tuple[bytes, int]:
    """Clone the contiguous ptr6/ptr7/ptr8 tail and return its original offset."""
    import struct as _s
    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(model_offset)
        hdr = f.read(0x20)
        offsets = [
            _s.unpack_from('>I', hdr, field_offset)[0]
            for field_offset in (0x14, 0x18, 0x1c)
        ]
        offsets = [offset for offset in offsets if 0 < offset < model_length]
        if not offsets:
            print("    [CloneTrailing] No ptr6/ptr7/ptr8 sections")
            return b'', 0
        start = min(offsets)
        f.seek(model_offset + start)
        data = f.read(model_length - start)
    print(f"    [CloneTrailing] {len(data):,} bytes from block+0x{start:X}")
    return data, start


def BuildHEADERModelBlock(
    gpl_bytes: bytes,
    act_bytes: bytes,
    tex_bytes: bytes,
    skn_bytes: bytes,
    trailing_bytes: bytes = b'',
    original_header: bytes = b'',
    original_trailing_off: int = 0,
) -> bytes:
    """Assemble the full model block from its four sections.

    Writes the 0x20-byte file header with relative pointers to GPL, ACT,
    TEX, and SKN, then concatenates all four sections into one contiguous
    byte string ready to be written into hammerspace.

    Header layout (matches Model0.analyze() in model0.py):
        +0x00  uint32  firstWord  — always 0
        +0x04  uint32  gplPtr     — GPL section offset relative to block start
        +0x08  uint32  ptr3       — ACT section offset (0 if absent)
        +0x0c  uint32  texPtr     — TEX section offset (0 if absent)
        +0x10  uint32  ptr5       — SKN section offset (0 if absent)
        +0x14  uint32  ptr6       — extra section pointer (recomputed if non-zero)
        +0x18  uint32  ptr7       — extra section pointer (recomputed if non-zero)
        +0x1c  uint32  ptr8       — extra section pointer (recomputed if non-zero)

    Section order:
        0x00  File header (0x20 bytes)
        0x20  GPL section          (always present)
        0x20 + len(gpl)  ACT section (omitted when empty)
        ...   TEX section          (omitted when empty)
        ...   SKN section          (omitted when empty; includes trailing
              sub-sections pointed to by ptr6/ptr7/ptr8 when they fall
              after the original SKN offset)

    Parameters
    ----------
    original_header :
        The 0x20-byte file header from the original model block in INPUT
        dt_na.dat.  Used to read ptr6/ptr7/ptr8 for recomputation.
    original_trailing_off :
        The earliest original ptr6/ptr7/ptr8 section offset. Needed to preserve
        relative spacing between separately cloned trailing sections.

    Returns the complete model block as a byte string.
    """
    import struct as _s

    HDR_SIZE = 0x20

    gpl_off = HDR_SIZE
    act_off = gpl_off + len(gpl_bytes)
    tex_off = act_off + len(act_bytes)
    skn_off = tex_off + len(tex_bytes)
    trailing_off = skn_off + len(skn_bytes)

    hdr = bytearray(HDR_SIZE)
    _s.pack_into('>I', hdr, 0x00, 0)
    _s.pack_into('>I', hdr, 0x04, gpl_off)
    _s.pack_into('>I', hdr, 0x08, act_off if act_bytes else 0)
    _s.pack_into('>I', hdr, 0x0c, tex_off if tex_bytes else 0)
    _s.pack_into('>I', hdr, 0x10, skn_off if skn_bytes else 0)

    # Recompute ptr6/ptr7/ptr8 relative to the separately cloned tail.
    if len(original_header) >= HDR_SIZE and original_trailing_off and trailing_bytes:
        for field_offset in (0x14, 0x18, 0x1c):
            orig_ptr = _s.unpack_from('>I', original_header, field_offset)[0]
            if orig_ptr and orig_ptr >= original_trailing_off:
                new_ptr = trailing_off + (orig_ptr - original_trailing_off)
                _s.pack_into('>I', hdr, field_offset, new_ptr)
                print(f'    [HDR] +0x{field_offset:02X} patched: '
                      f'0x{orig_ptr:08X} → 0x{new_ptr:08X}')

    return bytes(hdr) + gpl_bytes + act_bytes + tex_bytes + skn_bytes + trailing_bytes


def CloneHEADER(model_offset: int) -> bytes:
    """Clone the 0x20-byte model block header verbatim from INPUT dt_na.dat.

    The header contains block-relative pointers to GPL, ACT, TEX, SKN, and
    any ptr6/ptr7/ptr8 sub-sections.  When all sections are cloned at their
    original sizes and reassembled in the same order, these pointers remain
    valid without any fixups.
    """
    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(model_offset)
        data = f.read(0x20)
    print(f"    [CloneHEADER] 0x20 bytes from offset 0x{model_offset:08X}")
    return data


# ---------------------------------------------------------------------------
# BuildClone — full-block verbatim copy (DEACTIVATED, kept for reference)
# ---------------------------------------------------------------------------

# def BuildClone(chunk_number: int, file_index: int) -> bytes:
#     """Read the entire original model block verbatim from INPUT dt_na.dat.
#
#     All internal pointers within a model block are section-relative or
#     block-relative, so relocating the block to a different file offset
#     requires NO pointer fixups inside the data itself.  Only the DOL
#     directory entry (handled by patchDolEntry) needs updating.
#
#     Returns the raw model block bytes ready for writeModelBlock().
#     """
#     offset, length = hh.readDolEntry(chunk_number, file_index)
#     if offset == -1 or length <= 0:
#         raise ValueError(f"Invalid DOL entry for chunk={chunk_number}, file_index={file_index}")
#
#     with open(hh.INPUT_DAT, 'rb') as f:
#         f.seek(offset)
#         block = f.read(length)
#
#     if len(block) != length:
#         raise IOError(f"Short read: expected {length} bytes at 0x{offset:08X}, got {len(block)}")
#
#     print(f"    Read {length:,} bytes from INPUT at 0x{offset:08X}")
#     return block


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse as _ap
    import json as _json

    _parser = _ap.ArgumentParser(description='Build a hammerspace model block from a .sluggies file.')
    _parser.add_argument('sluggies_path', help='Path to the .sluggies file')
    _parser.add_argument('--unpatch', action='store_true', help='Remove the model from hammerspace and restore the original DOL entry')
    _parser.add_argument('--clone', action='store_true',
                         help='Clone mode: copy the original model block verbatim into hammerspace '
                              '(no rebuild, no edits — proof-of-concept for hammerspace loading)')
    _args = _parser.parse_args()

    with open(_args.sluggies_path, 'r') as _f:
        _data = _json.load(_f)

    _model = _data['SluggiesModel']
    _chunk = _model['ChunkNumber']
    _index = _model['FileIndex']

    # ------------------------------------------------------------------
    # --clone: DEACTIVATED — superseded by per-section clone (default path).
    # The full-block BuildClone is commented out above; --clone flag is now
    # a no-op alias that falls through to the per-section clone below.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Normal path: currently using per-section clone for testing.
    # Old section-by-section rebuild is commented out below.
    # ------------------------------------------------------------------

    if _args.unpatch:
        _success, _rem_offset, _rem_length = hh.removeModelFromHammerspace(_chunk, _index)
        if _success:
            hh.appendHammerspaceLog(
                'Removed',
                os.path.basename(_args.sluggies_path),
                _chunk, _index,
                _rem_offset, _rem_length,
            )
        raise SystemExit(0 if _success else 1)

    # --- Per-section clone, including a separately addressable trailing tail ---
    print("=== Per-Section Clone (GPL/ACT/TEX/SKN + ptr6/ptr7/ptr8) ===")
    print(f"Chunk: {_chunk}, FileIndex: {_index}")

    # Check if this model is already in hammerspace and evict it first.
    _cur_offset, _cur_length = hh.readOutputDolEntry(_chunk, _index)
    if _cur_offset >= hh.BASE_SIZE:
        print(f"\n[0] Model already in hammerspace at 0x{_cur_offset:08X} — removing old version ...")
        _evict_ok, _evict_off, _evict_len = hh.removeModelFromHammerspace(_chunk, _index)
        if not _evict_ok:
            print("ERROR: Could not remove existing hammerspace entry. Aborting.")
            raise SystemExit(1)
        hh.appendHammerspaceLog(
            'Removed',
            os.path.basename(_args.sluggies_path),
            _chunk, _index,
            _evict_off, _evict_len,
        )

    _orig_offset, _orig_length = hh.readDolEntry(_chunk, _index)
    if _orig_offset == -1:
        print("ERROR: Could not read DOL entry. Aborting.")
        raise SystemExit(1)
    print(f"\n    Original block: 0x{_orig_offset:08X}, {_orig_length:,} bytes")

    # Parse the sluggie for SKN builder metadata
    _parsed = ParseSluggie(_data)
    print(f"    Parsed: {len(_parsed.mesh.submeshes)} submesh(es), "
          f"{len(_parsed.bones.bones) if _parsed.bones else 0} bone(s), "
          f"skinning={'yes' if _parsed.skinning else 'no'}")

    print("\n[1/6] Cloning GPL ...")
    _gpl = CloneGPL(_orig_offset, _orig_length)
    _gpl_result = GPLBuildResult(
        gpl_bytes=_gpl,
        pos_gpl_offsets=_gpl_pos_offsets_from_bytes(_gpl),
    )
    print(f"    GPL built: {len(_gpl):,} bytes, "
          f"pos_gpl_offsets = {['0x%X' % o for o in _gpl_result.pos_gpl_offsets]}")

    print("[2/6] Cloning ACT ...")
    _act = CloneACT(_orig_offset, _orig_length)

    print("[3/6] Cloning TEX ...")
    _tex = CloneTEX(_orig_offset, _orig_length)

    print("[4/6] Cloning SKN ...")
    _skn = CloneSKN(_orig_offset, _orig_length)

    print("[5/6] Cloning ptr6/ptr7/ptr8 sections ...")
    _trailing, _orig_trailing_off = CloneTrailingSections(_orig_offset, _orig_length)

    # Read original header for ptr6/ptr7/ptr8 recomputation
    import struct as _struct
    _orig_header = b''
    if _parsed.model_offset:
        with open(hh.INPUT_DAT, 'rb') as _fh:
            _fh.seek(_parsed.model_offset)
            _orig_header = _fh.read(0x20)

    print("[6/6] Assembling model block header ...")
    _block = BuildHEADERModelBlock(
        _gpl, _act, _tex, _skn,
        trailing_bytes=_trailing,
        original_header=_orig_header,
        original_trailing_off=_orig_trailing_off,
    )
    print(f"\n    Assembled block: {len(_block):,} bytes ({len(_block) / 1048576:.3f} MB)")
    if len(_block) != _orig_length:
        print(f"    WARNING: assembled size ({len(_block):,}) differs from original ({_orig_length:,})!"
              f"  delta={len(_block) - _orig_length:+d}")

    print("\n[6] Scanning hammerspace for free region ...")
    _new_offset = hh.findFreeMemoryChunk(len(_block))
    if _new_offset == -1:
        _required = hh.BASE_SIZE + len(_block) + hh.HS_BUFFER_BYTES
        print(f"    No free hammerspace found. Expanding to {_required:,} bytes ...")
        if not hh.ensureOutputDat(_required):
            print("ERROR: Unable to prepare OUTPUT dt_na.dat. Aborting.")
            raise SystemExit(1)
        _new_offset = hh.findFreeMemoryChunk(len(_block))
        if _new_offset == -1:
            print("ERROR: No contiguous free region found even after expansion. Aborting.")
            raise SystemExit(1)
    print(f"    Free region at 0x{_new_offset:08X}")

    print("\n[7] Writing assembled block to OUTPUT dt_na.dat ...")
    hh.writeModelBlock(_block, _new_offset)

    print("\n[8] Patching OUTPUT main.dol and disc FST ...")
    hh.patchDolEntry(_chunk, _index, _new_offset, len(_block))

    _shared = hh.findSharedEntries(_chunk, _index)
    if _shared:
        print(f"    Found {len(_shared)} shared chunk reference(s) — patching all:")
        for _sc, _si in _shared:
            hh.patchDolEntry(_sc, _si, _new_offset, len(_block))

    _dat_size = os.path.getsize(hh.OUTPUT_DAT)
    hh.patchFstFileSize(_dat_size)

    print("\n[9] Zeroing original model address space ...")
    hh.zeroOriginalModel(_chunk, _index)

    print("\n[Debug] Writing debug dumps ...")
    hh.writeDebugDumps(
        os.path.basename(_args.sluggies_path),
        _orig_offset, _orig_length, _block,
    )

    hh.appendHammerspaceLog(
        'Written (per-section clone)',
        os.path.basename(_args.sluggies_path),
        _chunk, _index,
        _new_offset, len(_block),
    )

    print("\nDone. (Per-section clone — original location zeroed)")

    print("\nDone.")
    raise SystemExit(0)
