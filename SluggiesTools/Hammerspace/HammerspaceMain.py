import os
import sys
import base64
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(__file__))

# Step 2.2 – Initialize universal logger in child process.
_HS_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if _HS_TOOLS_DIR not in sys.path:
    sys.path.insert(0, _HS_TOOLS_DIR)

import slogger as _slogger
_slogger.configure()

import HammerspaceHelper as hh
from BlockValidator import validate_model_block
from ModelFormat import align_array_offset, compute_mem_clear_range, pad_array

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
    source_state_offset:         int


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
    source_header_offset:         int


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
    source_data_offset:       int


@dataclass
class ColorChannel:
    channel_index:    int
    color_data:       bytes
    color_faces_data: bytes
    comp_count:       int
    quantize_info:    int
    source_data_offset: int


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
    source_layout_offset:           int
    source_position_data_offset:    int


@dataclass
class MeshData:
    submeshes: list   # [Submesh]
    source_gpl_base_offset: int


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
    mem_clr_ptr_value:         int
    mem_clr_absolute_ptr:      int
    mem_clr_size:              int
    flush_ind_arr_field_offset: int
    flush_ind_absolute_ptr:    int | None
    flush_ind_size:            int
    flush_ind_data:            bytes
    quantize_info:             int
    sk1s:                      list   # [SK1]
    sk2s:                      list   # [SK2]
    sk_accs:                   list   # [SKAcc]
    preserve_source_layout:    bool = False


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
class TrailingSection:
    header_field_offset: int
    original_ptr:        int
    data:                bytes


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
    trailing_sections:  list[TrailingSection]  # schema-backed ptr6/ptr7/ptr8 payloads
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


@dataclass(frozen=True)
class SectionModes:
    gpl: str = 'clone'
    act: str = 'clone'
    tex: str = 'clone'
    skn: str = 'clone'
    trailing: str = 'clone'

    def as_dict(self) -> dict[str, str]:
        return {
            'GPL': self.gpl,
            'ACT': self.act,
            'TEX': self.tex,
            'SKN': self.skn,
            'trailing': self.trailing,
        }


@dataclass
class ModelBlockBuild:
    block: bytes
    parsed: SluggieParsed
    chunk_number: int
    file_index: int
    original_offset: int
    original_length: int
    section_modes: SectionModes
    section_sizes: dict[str, int]
    validation_report: dict


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
    source_gpl_base_offset = _hex((model.get('SkinData') or {}).get('GplBaseOffset', '0x0'))

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
                source_data_offset       = _hex(uv.get('UVChannelOffset', '0x0')),
            ))

        color_channels = []
        for cc in sub.get('ColorChannels', []):
            color_channels.append(ColorChannel(
                channel_index    = cc['ColorChannelIndex'],
                color_data       = _decode(cc['ColorChannelData'], use_b64),
                color_faces_data = _decode(cc['ColorFacesData'],   use_b64),
                comp_count       = cc['ColorChannelCompCount'],
                quantize_info    = cc['ColorChannelQuantizeInfo'],
                source_data_offset = _hex(cc.get('ColorChannelOffset', '0x0')),
            ))

        draw_states = []
        for ds in sub.get('DisplayStates', []):
            pad_hex = ds.get('DisplayStatePadBytes', '000000')
            prim_list_data = ds.get('PrimListData')
            if prim_list_data in (None, ''):
                prim_list_bytes = b''
            else:
                prim_list_bytes = _decode(prim_list_data, use_b64)
            draw_states.append(DrawState(
                display_state_id            = ds['DisplayStateId'],
                display_state_pad_bytes     = bytes.fromhex(pad_hex),
                prim_list_data              = prim_list_bytes,
                active_descriptors          = ds.get('VertexStreamLayout') or ds.get('ActiveDescriptors', []),
                prim_list_ptr_field_offset  = _hex(ds['PrimListPtrFieldOffset']),
                prim_list_size_field_offset = _hex(ds['PrimListSizeFieldOffset']),
                prim_list_absolute_offset   = _hex(ds['PrimListAbsoluteOffset']),
                prim_list_length            = ds['PrimListLength'],
                shader_mode_field_offset    = _hex(ds['ShaderModeFieldOffset']) if ds.get('ShaderModeFieldOffset') else 0,
                shader_mode                 = ds.get('ShaderMode', ''),
                source_state_offset         = _hex(ds['ShaderModeFieldOffset']) - 4 if ds.get('ShaderModeFieldOffset') else 0,
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
                source_header_offset         = _hex(raw_nb.get('NormalDataPtrFieldOffset', '0x0')),
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
            source_layout_offset           = _hex(sub.get('SubmeshOffset', '0x0')),
            source_position_data_offset    = _hex(vb.get('VertexBufferOffset', '0x0')),
        ))

    mesh_data = MeshData(
        submeshes=submeshes,
        source_gpl_base_offset=source_gpl_base_offset,
    )

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
            mem_clr_ptr_value         = raw_skn.get(
                'MemClrPtrValue',
                _hex(raw_skn.get('MemClrAbsolutePtr', '0x0'))
                - _hex(raw_skn.get('GplBaseOffset', '0x0')),
            ),
            mem_clr_absolute_ptr      = _hex(raw_skn.get('MemClrAbsolutePtr',        '0x0')),
            mem_clr_size              = raw_skn.get('MemClrSize', 0),
            flush_ind_arr_field_offset= _hex(raw_skn.get('FlushIndArrFieldOffset',   '0x0')),
            flush_ind_absolute_ptr    = _hex(raw_skn['FlushIndAbsolutePtr']) if raw_skn.get('FlushIndAbsolutePtr') else None,
            flush_ind_size            = raw_skn.get('FlushIndSize', 0),
            flush_ind_data            = _decode(raw_skn['FlushIndData'], use_b64) if raw_skn.get('FlushIndData') else b'',
            quantize_info             = raw_skn['QuantizeInfo'],
            sk1s                      = sk1s,
            sk2s                      = sk2s,
            sk_accs                   = sk_accs,
            preserve_source_layout    = not _geometry_edited,
        )
    else:
        skinning_data = None

    # ---- Trailing sections ------------------------------------------------
    trailing_sections = []
    for entry in model.get('TrailingSections', []) or []:
        if not isinstance(entry, dict):
            continue
        trailing_sections.append(TrailingSection(
            header_field_offset = _hex(entry.get('HeaderFieldOffset', '0x0')),
            original_ptr        = _hex(entry.get('OriginalPtr', '0x0')),
            data                = _decode(entry.get('Data'), use_b64) if entry.get('Data') else b'',
        ))

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
        trailing_sections = trailing_sections,
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
    preserve_source_layout = bool(parsed.mesh.source_gpl_base_offset) and all(
        sub.source_layout_offset
        and sub.position_data_ptr_field_offset
        and sub.source_position_data_offset
        and sub.draw_states
        and sub.draw_states[0].source_state_offset
        for sub in parsed.mesh.submeshes
    )

    sk_write_end = 0
    if parsed.skinning:
        skinning = parsed.skinning
        skin_stride = _vb_comp_size(skinning.quantize_info) * 6
        direct_writes = set()
        for entry in (*skinning.sk1s, *skinning.sk2s):
            direct_writes.update(
                entry.gpl_vertex_arr_value + entry.vertex_offset + index * skin_stride
                for index in range(entry.vertex_cnt)
            )
        accumulation_writes = set()
        for entry in skinning.sk_accs:
            destinations = _s.unpack(f'>{entry.vertex_cnt}H', entry.dest_index_data)
            accumulation_writes.update(
                entry.gpl_dest_arr_value + destination * skin_stride
                for destination in destinations
            )
        mem_clear_ptr, mem_clear_size = compute_mem_clear_range(
            direct_writes, accumulation_writes, skin_stride)
        write_offsets = direct_writes | accumulation_writes
        if write_offsets:
            sk_write_end = max(offset + skin_stride for offset in write_offsets)
        sk_write_end = max(sk_write_end, mem_clear_ptr + mem_clear_size)

    for submesh_index, sub in enumerate(parsed.mesh.submeshes):
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
        HDR_END = DSP_OFF + 0x0c

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
        if submesh_index == 0 and sk_write_end > len(pos_data):
            pos_data += b'\x00' * (sk_write_end - len(pos_data))
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

        # Donor GPL layouts place display-state records after all attribute
        # arrays. Some runtime state handling depends on that ordering.
        DS_OFF = cursor
        cursor += n_ds * 0x10

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

        if preserve_source_layout:
            source_base = parsed.mesh.source_gpl_base_offset
            source_layout = sub.source_layout_offset - source_base
            source_pos_header = sub.position_data_ptr_field_offset - sub.source_layout_offset
            source_pos_data = sub.source_position_data_offset - sub.source_layout_offset
            source_uv_headers = [
                uv.uv_data_ptr_field_offset - sub.source_layout_offset
                for uv in sub.uv_channels
            ]
            source_uv_data = [
                uv.source_data_offset - sub.source_layout_offset
                for uv in sub.uv_channels
            ]
            source_state = sub.draw_states[0].source_state_offset - sub.source_layout_offset
            source_display = source_state - 0x0C
            source_normal = (
                sub.normal_buffer.source_header_offset - sub.source_layout_offset
                if sub.normal_buffer else 0
            )
            source_normal_data = (
                sub.normal_buffer.normal_buffer_offset - sub.source_layout_offset
                if sub.normal_buffer else 0
            )
            source_color = (
                sub.color_channels[0].source_data_offset - sub.source_layout_offset
                if sub.color_channels else 0
            )
            source_color_header = (
                source_color - 8 if source_color else source_normal - 8
            )
            source_prim = [
                ds.prim_list_absolute_offset - sub.source_layout_offset
                if ds.prim_list_data else 0
                for ds in sub.draw_states
            ]

            POS_OFF = source_pos_header
            COL_OFF = source_color_header
            UV_OFF = source_uv_headers[0] if source_uv_headers else source_normal
            NOR_OFF = source_normal
            DSP_OFF = source_display
            DS_OFF = source_state
            pos_data_off = source_pos_data
            col_data_off = source_color
            uv_data_offs = source_uv_data
            nor_data_off = (
                source_pos_data + _vb_comp_size(sub.vertex_quantize_info) * 3
                if is_interleaved else source_normal_data
            )
            pl_offs = source_prim

            source_ends = [
                POS_OFF + 8,
                COL_OFF + 8,
                UV_OFF + M_uv * 0x10,
                NOR_OFF + 0x0C if NOR_OFF else 0,
                DSP_OFF + 0x0C,
                DS_OFF + n_ds * 0x10,
                pos_data_off + len(pos_data),
                col_data_off + len(col_data) if col_data_off else 0,
                *(offset + len(data) for offset, data in zip(uv_data_offs, uv_data_list)),
                nor_data_off + len(nor_data) if nor_data else 0,
                *(offset + len(data) for offset, data in zip(pl_offs, pl_bytes_list) if offset),
            ]
            cursor = max(source_ends)

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
    # Blob starts must be 32-byte aligned in flexible layouts. Unchanged
    # source-layout rebuilds retain the donor coordinates exactly.
    BLOBS_START = _align32(GEO_DESC_OFF + GEO_DESC_SIZE)
    if preserve_source_layout:
        blob_gpl_offs = [
            lay['sub'].source_layout_offset - parsed.mesh.source_gpl_base_offset
            for lay in sub_layouts
        ]
        cursor = max(gpl_off + lay['blob_size'] for gpl_off, lay in zip(blob_gpl_offs, sub_layouts))
        for index, (gpl_off, lay) in enumerate(zip(blob_gpl_offs, sub_layouts)):
            palette_offsets: dict[bytes, int] = {}
            unique_palette_bytes = []
            for pal_b in lay['pal_name_bytes_list']:
                if pal_b not in palette_offsets:
                    palette_offsets[pal_b] = 0
                    unique_palette_bytes.append(pal_b)
            palette_size = sum((len(pal_b) + 3) & ~3 for pal_b in unique_palette_bytes)
            next_layout = blob_gpl_offs[index + 1] if index + 1 < len(blob_gpl_offs) else cursor
            palette_cursor = next_layout - palette_size if index + 1 < len(blob_gpl_offs) else cursor
            pal_name_offs = []
            for pal_b in lay['pal_name_bytes_list']:
                if not palette_offsets[pal_b]:
                    palette_offsets[pal_b] = palette_cursor
                    palette_cursor += len(pal_b)
                    palette_cursor = (palette_cursor + 3) & ~3
                pal_name_offs.append(palette_offsets[pal_b] - gpl_off)
            lay['pal_name_offs'] = pal_name_offs
            cursor = max(cursor, palette_cursor)
        for gpl_off, lay in zip(blob_gpl_offs, sub_layouts):
            lay['name_off'] = cursor - gpl_off
            cursor += len(lay['name_bytes'])
        cursor = _align32(cursor)
    else:
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
    _slogger.info(f"[CloneGPL] {gpl_len:,} bytes from block+0x{gpl_off:X}", source="hammerspace.main")
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
            _slogger.info("[CloneACT] No ACT section", source="hammerspace.main")
            return b''
        tex_off = _s.unpack_from('>I', hdr, 0x0c)[0]
        skn_off = _s.unpack_from('>I', hdr, 0x10)[0]
        next_off = tex_off or skn_off or model_length
        act_len = next_off - act_off
        f.seek(model_offset + act_off)
        data = f.read(act_len)
    _slogger.info(f"[CloneACT] {act_len:,} bytes from block+0x{act_off:X}", source="hammerspace.main")
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
            _slogger.info("[CloneTEX] No TEX section", source="hammerspace.main")
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
    _slogger.info(f"[CloneTEX] {tex_len:,} bytes from block+0x{tex_off:X}", source="hammerspace.main")
    return data


def BuildSKNSkinningDataCopyOnly(parsed: SluggieParsed, gpl_result: GPLBuildResult) -> bytes:
    """Return the SKN (Skinning Data) section bytes verbatim from INPUT dt_na.dat.

    Reads the original SKN block from the input file using the section offset
    stored in the model-block file header. memClrPtr, gplVertexArr, and
    gplDestArr are position-data-relative and require no GPL relocation.

    Returns the raw SKN section bytes (patched), or b'' if the model has no
    SKN section.
    """
    import struct as _s

    if not parsed.model_offset:
        return b''

    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(parsed.model_offset)
        hdr = f.read(0x20)
        skn_off = _s.unpack_from('>I', hdr, 0x10)[0]
        if not skn_off:
            return b''
        skn_len = parsed.model_length - skn_off
        f.seek(parsed.model_offset + skn_off)
        return f.read(skn_len)


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


def _vb_comp_size(quant_info: int) -> int:
    return 4 if (quant_info >> 4) in (4, 7, 0xa) else 2


def _source_blob_for_skn(entry: object, vertex_stride: int) -> bytes:
    blob = entry.bind_pose_data
    expected_len = getattr(entry, 'vertex_offset', 0) + entry.vertex_cnt * vertex_stride
    if len(blob) < expected_len:
        blob = b'\x00' * (expected_len - len(blob)) + blob
    return blob


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
    import struct as _s

    SK1_SIZE = 0x40
    SK2_SIZE = 0x74
    SKACC_SIZE = 0x44
    n_sk1 = len(skn.sk1s)
    n_sk2 = len(skn.sk2s)
    n_acc = len(skn.sk_accs)

    sk1_bytes = bytearray(n_sk1 * SK1_SIZE)
    for i, sk in enumerate(skn.sk1s):
        b = i * SK1_SIZE
        _s.pack_into('>I', sk1_bytes, b + 0x30, sk1_src_off[i])
        _s.pack_into('>I', sk1_bytes, b + 0x34, sk.gpl_vertex_arr_value)
        _s.pack_into('>H', sk1_bytes, b + 0x38, sk.bone_index)
        _s.pack_into('>H', sk1_bytes, b + 0x3a, sk.vertex_cnt)
        _s.pack_into('B', sk1_bytes, b + 0x3c, sk.vertex_offset)

    sk2_bytes = bytearray(n_sk2 * SK2_SIZE)
    for i, sk in enumerate(skn.sk2s):
        b = i * SK2_SIZE
        _s.pack_into('>I', sk2_bytes, b + 0x60, sk2_src_off[i])
        _s.pack_into('>I', sk2_bytes, b + 0x64, sk2_wt_off[i])
        _s.pack_into('>I', sk2_bytes, b + 0x68, sk.gpl_vertex_arr_value)
        _s.pack_into('>H', sk2_bytes, b + 0x6c, sk.bone_index1)
        _s.pack_into('>H', sk2_bytes, b + 0x6e, sk.bone_index2)
        _s.pack_into('>H', sk2_bytes, b + 0x70, sk.vertex_cnt)
        _s.pack_into('B', sk2_bytes, b + 0x72, sk.vertex_offset)

    acc_bytes = bytearray(n_acc * SKACC_SIZE)
    for i, sk in enumerate(skn.sk_accs):
        b = i * SKACC_SIZE
        _s.pack_into('>I', acc_bytes, b + 0x30, acc_src_off[i])
        _s.pack_into('>I', acc_bytes, b + 0x34, acc_dest_off[i])
        _s.pack_into('>I', acc_bytes, b + 0x38, sk.gpl_dest_arr_value)
        _s.pack_into('>I', acc_bytes, b + 0x3c, acc_wt_off[i])
        _s.pack_into('>H', acc_bytes, b + 0x40, sk.bone_index)
        _s.pack_into('>H', acc_bytes, b + 0x42, sk.vertex_cnt)

    return bytes(sk1_bytes), bytes(sk2_bytes), bytes(acc_bytes)


def _compute_skn_mem_clear_range(skn: SkinningData, vertex_stride: int) -> tuple[int, int]:
    import struct as _s

    direct_writes = set()
    for entry in (*skn.sk1s, *skn.sk2s):
        direct_writes.update(
            entry.gpl_vertex_arr_value + entry.vertex_offset + index * vertex_stride
            for index in range(entry.vertex_cnt)
        )
    accumulation_writes = set()
    for entry in skn.sk_accs:
        destinations = _s.unpack(f'>{entry.vertex_cnt}H', entry.dest_index_data)
        accumulation_writes.update(
            entry.gpl_dest_arr_value + destination * vertex_stride
            for destination in destinations
        )
    return compute_mem_clear_range(direct_writes, accumulation_writes, vertex_stride)


def BuildSKNSkinningData(parsed: SluggieParsed, gpl_result: GPLBuildResult) -> bytes:
    """Build the SKN (Skinning Data) section.

    memClrPtr, gplVertexArr, and gplDestArr are pos-data-relative byte offsets from
    the start of submesh 0's position buffer) and are preserved verbatim from
    parsed.skinning — they do not change when the model block is relocated or
    the GPL section is rebuilt with the same vertex data.

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
    _s.pack_into('>H', skn_hdr, 0x00, n_sk1)
    _s.pack_into('>H', skn_hdr, 0x02, n_sk2)
    _s.pack_into('>H', skn_hdr, 0x04, n_acc)
    _s.pack_into('B',  skn_hdr, 0x06, skn.quantize_info)
    _s.pack_into('>I', skn_hdr, 0x08, SK1_ARR_OFF)
    _s.pack_into('>I', skn_hdr, 0x0c, SK2_ARR_OFF)
    _s.pack_into('>I', skn_hdr, 0x10, SKACC_ARR_OFF)
    _s.pack_into('>I', skn_hdr, 0x14, new_memClrPtr)
    _s.pack_into('>I', skn_hdr, 0x18, new_memClrSize)
    _s.pack_into('>I', skn_hdr, 0x1c, flush_off if flush_bytes else 0)
    _s.pack_into('>I', skn_hdr, 0x20, skn.flush_ind_size)

    struct_end = SKN_HDR_SIZE + n_sk1 * SK1_SIZE + n_sk2 * SK2_SIZE + n_acc * SKACC_SIZE
    align_pad = VAR_DATA_OFF - struct_end
    return bytes(skn_hdr) + sk1_bytes + sk2_bytes + acc_bytes + b'\x00' * align_pad + bytes(var_data)


def CloneSKN(model_offset: int, model_length: int) -> bytes:
    """Clone the SKN section verbatim, excluding ptr6/ptr7/ptr8 sections."""
    import struct as _s
    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(model_offset)
        hdr = f.read(0x20)
        skn_off = _s.unpack_from('>I', hdr, 0x10)[0]
        if not skn_off:
            _slogger.info("[CloneSKN] No SKN section", source="hammerspace.main")
            return b''
        trailing_offsets = [_s.unpack_from('>I', hdr, offset)[0] for offset in (0x14, 0x18, 0x1c)]
        skn_end = min(
            [offset for offset in trailing_offsets if offset > skn_off]
            + [model_length]
        )
        skn_len = skn_end - skn_off
        f.seek(model_offset + skn_off)
        data = f.read(skn_len)
    _slogger.info(f"[CloneSKN] {skn_len:,} bytes from block+0x{skn_off:X}", source="hammerspace.main")
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
            _slogger.info("[CloneTrailing] No ptr6/ptr7/ptr8 sections", source="hammerspace.main")
            return b'', 0
        start = min(offsets)
        f.seek(model_offset + start)
        data = f.read(model_length - start)
    _slogger.info(f"[CloneTrailing] {len(data):,} bytes from block+0x{start:X}", source="hammerspace.main")
    return data, start


def BuildHEADERModelBlock(
    gpl_bytes: bytes,
    act_bytes: bytes,
    tex_bytes: bytes,
    skn_bytes: bytes,
    trailing_bytes: bytes = b'',
    original_header: bytes = b'',
    original_trailing_off: int = 0,
    trailing_sections: list[TrailingSection] | None = None,
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
    gpl_section_padding = b''
    if len(original_header) >= HDR_SIZE:
        original_gpl_off = _s.unpack_from('>I', original_header, 0x04)[0]
        original_next_sections = [
            _s.unpack_from('>I', original_header, offset)[0]
            for offset in (0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C)
        ]
        original_next = min(
            (offset for offset in original_next_sections if offset > original_gpl_off),
            default=0,
        )
        if original_gpl_off == gpl_off and original_next:
            original_gpl_span = original_next - original_gpl_off
            if len(gpl_bytes) <= original_gpl_span:
                gpl_section_padding = b'\x00' * (original_gpl_span - len(gpl_bytes))

    act_off = gpl_off + len(gpl_bytes) + len(gpl_section_padding)
    tex_off = act_off + len(act_bytes)
    skn_unaligned_off = tex_off + len(tex_bytes)
    skn_off = align_array_offset(skn_unaligned_off, 'skn_source') if skn_bytes else skn_unaligned_off
    skn_padding = b'\x00' * (skn_off - skn_unaligned_off)
    skn_trailing_padding = b''
    if (skn_bytes and trailing_bytes and len(original_header) >= HDR_SIZE
            and original_trailing_off):
        original_skn_off = _s.unpack_from('>I', original_header, 0x10)[0]
        if original_skn_off and original_trailing_off > original_skn_off:
            original_relative_offset = original_trailing_off - original_skn_off
            if len(skn_bytes) < original_relative_offset:
                skn_trailing_padding = b'\x00' * (original_relative_offset - len(skn_bytes))
    tail_start = skn_off + len(skn_bytes) + len(skn_trailing_padding)

    hdr = bytearray(HDR_SIZE)
    _s.pack_into('>I', hdr, 0x00, 0)
    _s.pack_into('>I', hdr, 0x04, gpl_off)
    _s.pack_into('>I', hdr, 0x08, act_off if act_bytes else 0)
    _s.pack_into('>I', hdr, 0x0c, tex_off if tex_bytes else 0)
    _s.pack_into('>I', hdr, 0x10, skn_off if skn_bytes else 0)

    # Recompute ptr6/ptr7/ptr8 relative to the separately cloned tail.
    if trailing_bytes:
        if trailing_sections:
            section_ptrs = [sec.original_ptr for sec in trailing_sections if sec.original_ptr]
            if section_ptrs:
                original_trailing_off = min(section_ptrs)
        if len(original_header) >= HDR_SIZE and original_trailing_off:
            for field_offset in (0x14, 0x18, 0x1c):
                orig_ptr = _s.unpack_from('>I', original_header, field_offset)[0]
                if orig_ptr and orig_ptr >= original_trailing_off:
                    new_ptr = tail_start + (orig_ptr - original_trailing_off)
                    _s.pack_into('>I', hdr, field_offset, new_ptr)
                    _slogger.info(f'[HDR] +0x{field_offset:02X} patched: '
                           f'0x{orig_ptr:08X} → 0x{new_ptr:08X}', source="hammerspace.main")

    return (bytes(hdr) + gpl_bytes + gpl_section_padding + act_bytes + tex_bytes + skn_padding
            + skn_bytes + skn_trailing_padding + trailing_bytes)


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
    _slogger.info(f"[CloneHEADER] 0x20 bytes from offset 0x{model_offset:08X}", source="hammerspace.main")
    return data


def _validate_section_modes(modes: SectionModes) -> None:
    supported = {
        'GPL': {'clone', 'build'},
        'ACT': {'clone'},
        'TEX': {'clone'},
        'SKN': {'clone', 'build'},
        'trailing': {'clone'},
    }
    for section, mode in modes.as_dict().items():
        if mode not in {'clone', 'build'}:
            raise ValueError(f"{section} mode must be 'clone' or 'build', got {mode!r}")
        if mode not in supported[section]:
            raise ValueError(f"{section}=build is not implemented; supported mode: clone")


def _collect_missing_hammerspace_properties(model: dict) -> list[str]:
    """Return the subset of hammerspace rebuild properties that are missing."""
    missing: list[str] = []

    for key in ('ChunkNumber', 'FileIndex', 'ModelOffset', 'ModelLength'):
        if key not in model or model.get(key) in (None, ''):
            missing.append(key)

    if 'Submeshes' not in model or not isinstance(model.get('Submeshes'), list) or not model['Submeshes']:
        missing.append('Submeshes')
        return missing

    for sub_idx, sub in enumerate(model['Submeshes']):
        if not isinstance(sub, dict):
            missing.append(f'Submeshes[{sub_idx}]')
            continue

        vb = sub.get('VertexBuffer')
        if not isinstance(vb, dict):
            missing.append(f'Submeshes[{sub_idx}].VertexBuffer')
        else:
            for key in ('VertexBufferData', 'VertexBufferCompCount', 'VertexBufferQuantizeInfo'):
                if key not in vb or vb.get(key) in (None, ''):
                    missing.append(f'Submeshes[{sub_idx}].VertexBuffer.{key}')

        if 'FacesData' not in sub or sub.get('FacesData') in (None, ''):
            missing.append(f'Submeshes[{sub_idx}].FacesData')

        ds = sub.get('DisplayStates')
        if not isinstance(ds, list) or not ds:
            missing.append(f'Submeshes[{sub_idx}].DisplayStates')
            continue

        for ds_idx, display_state in enumerate(ds):
            if not isinstance(display_state, dict):
                missing.append(f'Submeshes[{sub_idx}].DisplayStates[{ds_idx}]')
                continue
            for key in ('DisplayStateId', 'PrimListLength', 'ShaderMode'):
                if key not in display_state or display_state.get(key) in (None, ''):
                    missing.append(f'Submeshes[{sub_idx}].DisplayStates[{ds_idx}].{key}')
            if (display_state.get('PrimListLength', 0) > 0
                    and display_state.get('PrimListData') in (None, '')):
                missing.append(f'Submeshes[{sub_idx}].DisplayStates[{ds_idx}].PrimListData')

    return missing


def _validate_hammerspace_contract(model: dict, modes: SectionModes) -> None:
    """Require a real hammerspace `.sluggies` contract for rebuild operations.

    Temporary compatibility shim for milestone testing: SKN-only rebuilds are
    allowed to proceed even when the exporter has not yet filled in display-state
    `PrimListData`, because the current in-game test matrix needs a runnable path.
    Once the exporter provides the missing data, this should be tightened back up
    so the contract gate rejects incomplete rebuild payloads again.
    """
    if all(
        (
            modes.gpl == 'clone',
            modes.act == 'clone',
            modes.tex == 'clone',
            modes.skn == 'clone',
            modes.trailing == 'clone',
        )
    ):
        return

    if modes.skn == 'build' and modes.gpl == 'clone' and modes.act == 'clone' and modes.tex == 'clone' and modes.trailing == 'clone':
        missing = _collect_missing_hammerspace_properties(model)
        if missing and 'PrimListData' in '\n'.join(missing):
            _slogger.warning(
                'Temporary hammerspace contract relaxation: allowing SKN-only rebuild with '
                'missing PrimListData until exporter data is available',
                source='hammerspace.main',
            )
            return

    missing = _collect_missing_hammerspace_properties(model)
    if missing:
        missing_list = ', '.join(missing)
        raise ValueError(
            'hammerspace rebuild is missing required rebuild properties: '
            f'{missing_list}; re-export the model from Blender/SluggiesTools before patching'
        )


def _build_validation_report(
    block: bytes,
    original_length: int,
    section_modes: SectionModes,
    section_sizes: dict[str, int],
) -> dict:
    structural = validate_model_block(block)
    section_pointer_facts = structural.get('facts', {}).get('section_pointers', {})
    pointers = {
        'GPL': section_pointer_facts.get('GPL', 0),
        'ACT': section_pointer_facts.get('ACT', 0),
        'TEX': section_pointer_facts.get('TEX', 0),
        'SKN': section_pointer_facts.get('SKN', 0),
    }

    return {
        'valid': structural['valid'],
        'errors': structural['errors'],
        'warnings': structural.get('warnings', []),
        'section_modes': section_modes.as_dict(),
        'section_sizes': section_sizes,
        'section_pointers': pointers,
        'assembled_size': len(block),
        'original_size': original_length,
        'size_delta': len(block) - original_length,
        'validator_facts': structural.get('facts', {}),
    }


def BuildModelBlock(data: dict, section_modes: SectionModes | None = None) -> ModelBlockBuild:
    """Assemble a model block without modifying output DAT, DOL, or FST files."""
    modes = section_modes or SectionModes()
    _validate_section_modes(modes)

    model = data['SluggiesModel']
    _validate_hammerspace_contract(model, modes)
    chunk_number = model['ChunkNumber']
    file_index = model['FileIndex']
    original_offset, original_length = hh.readDolEntry(chunk_number, file_index)
    if original_offset == -1 or original_length <= 0:
        raise ValueError(
            f'Invalid donor DOL entry for chunk={chunk_number}, file_index={file_index}: '
            f'offset={original_offset}, length={original_length}'
        )

    parsed = ParseSluggie(data)
    if modes.gpl == 'build':
        gpl_result = BuildGPLMeshData(parsed)
    else:
        gpl_bytes = CloneGPL(original_offset, original_length)
        gpl_result = GPLBuildResult(
            gpl_bytes=gpl_bytes,
            pos_gpl_offsets=_gpl_pos_offsets_from_bytes(gpl_bytes),
        )

    act_bytes = CloneACT(original_offset, original_length)
    tex_bytes = CloneTEX(original_offset, original_length)
    if modes.skn == 'build':
        skn_bytes = BuildSKNSkinningData(parsed, gpl_result)
    else:
        skn_bytes = CloneSKN(original_offset, original_length)
    parsed_trailing_sections = getattr(parsed, 'trailing_sections', None) or []
    if parsed_trailing_sections:
        trailing_bytes = b''.join(section.data for section in parsed_trailing_sections)
        original_trailing_offset = min(
            (section.original_ptr for section in parsed_trailing_sections if section.original_ptr),
            default=0,
        )
    else:
        trailing_bytes, original_trailing_offset = CloneTrailingSections(
            original_offset,
            original_length,
        )
    original_header = CloneHEADER(original_offset)
    block = BuildHEADERModelBlock(
        gpl_result.gpl_bytes,
        act_bytes,
        tex_bytes,
        skn_bytes,
        trailing_bytes=trailing_bytes,
        original_header=original_header,
        original_trailing_off=original_trailing_offset,
        trailing_sections=getattr(parsed, 'trailing_sections', None),
    )
    section_sizes = {
        'GPL': len(gpl_result.gpl_bytes),
        'ACT': len(act_bytes),
        'TEX': len(tex_bytes),
        'SKN': len(skn_bytes),
        'trailing': len(trailing_bytes),
    }
    report = _build_validation_report(block, original_length, modes, section_sizes)
    return ModelBlockBuild(
        block=block,
        parsed=parsed,
        chunk_number=chunk_number,
        file_index=file_index,
        original_offset=original_offset,
        original_length=original_length,
        section_modes=modes,
        section_sizes=section_sizes,
        validation_report=report,
    )


def WriteModelBlock(build: ModelBlockBuild, model_name: str) -> int:
    """Write an assembled block to hammerspace and patch its output references."""
    if not build.validation_report.get('valid'):
        raise ValueError('refusing to write a model block with a failed validation report')

    chunk_number = build.chunk_number
    file_index = build.file_index

    current_offset, _ = hh.readOutputDolEntry(chunk_number, file_index)
    if current_offset >= hh.BASE_SIZE:
        _slogger.info(
            f'Model already in hammerspace at 0x{current_offset:08X}; removing old version',
            source='hammerspace.main',
        )
        evicted, evicted_offset, evicted_length = hh.removeModelFromHammerspace(
            chunk_number,
            file_index,
        )
        if not evicted:
            raise RuntimeError('Could not remove existing hammerspace entry')
        _slogger.info(
            f'Hammerspace Log: Removed | Model: {model_name} | Chunk: {chunk_number} | '
            f'File: {file_index} | Address: 0x{evicted_offset:08X} | '
            f'Size: {evicted_length / (1024 * 1024):.2f} MB',
            source='hammerspace.main',
        )

    new_offset = hh.findFreeMemoryChunk(len(build.block))
    if new_offset == -1:
        if not hh.ensureOutputDat():
            raise RuntimeError('Unable to prepare output dt_na.dat')
        current_size = os.path.getsize(hh.OUTPUT_DAT)
        next_region_start = (current_size + hh.HS_ALIGN_BYTES - 1) & ~(hh.HS_ALIGN_BYTES - 1)
        required_size = next_region_start + len(build.block) + hh.HS_BUFFER_BYTES
        if not hh.ensureOutputDat(required_size):
            raise RuntimeError('Unable to prepare output dt_na.dat')
        new_offset = hh.findFreeMemoryChunk(len(build.block))
        if new_offset == -1:
            raise RuntimeError('No contiguous hammerspace region found after expansion')

    hh.writeModelBlock(build.block, new_offset)
    hh.patchDolEntry(chunk_number, file_index, new_offset, len(build.block))
    for shared_chunk, shared_index in hh.findSharedEntries(chunk_number, file_index):
        hh.patchDolEntry(shared_chunk, shared_index, new_offset, len(build.block))

    hh.patchFstFileSize(os.path.getsize(hh.OUTPUT_DAT))
    hh.zeroOriginalModel(chunk_number, file_index)
    hh.writeDebugDumps(
        model_name,
        build.original_offset,
        build.original_length,
        build.block,
    )
    _slogger.info(
        f'Hammerspace Log: Written | Model: {model_name} | Chunk: {chunk_number} | '
        f'File: {file_index} | Address: 0x{new_offset:08X} | '
        f'Size: {len(build.block) / (1024 * 1024):.2f} MB | '
        f'Modes: {build.section_modes.as_dict()}',
        source='hammerspace.main',
    )
    return new_offset


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
    _parser.add_argument('--unpatch', action='store_true', help='Remove the model from hammerspace')
    _parser.add_argument('--dry-run', action='store_true', help='Assemble and validate without modifying output files')
    _parser.add_argument('--clone', action='store_true', help='Deprecated all-clone alias')
    _parser.add_argument('--gpl', choices=('clone', 'build'), default='clone')
    _parser.add_argument('--act', choices=('clone', 'build'), default='clone')
    _parser.add_argument('--tex', choices=('clone', 'build'), default='clone')
    _parser.add_argument('--skn', choices=('clone', 'build'), default='clone')
    _parser.add_argument('--trailing', choices=('clone', 'build'), default='clone')
    _args = _parser.parse_args()

    with open(_args.sluggies_path, 'r') as _file:
        _data = _json.load(_file)
    _model = _data['SluggiesModel']
    _chunk = _model['ChunkNumber']
    _index = _model['FileIndex']
    _model_name = os.path.basename(_args.sluggies_path)

    if _args.unpatch:
        _success, _removed_offset, _removed_length = hh.removeModelFromHammerspace(_chunk, _index)
        if _success:
            _slogger.info(
                f'Hammerspace Log: Removed | Model: {_model_name} | Chunk: {_chunk} | '
                f'File: {_index} | Address: 0x{_removed_offset:08X} | '
                f'Size: {_removed_length / (1024 * 1024):.2f} MB',
                source='hammerspace.main',
            )
        raise SystemExit(0 if _success else 1)

    _modes = SectionModes(
        gpl=_args.gpl,
        act=_args.act,
        tex=_args.tex,
        skn=_args.skn,
        trailing=_args.trailing,
    )
    if _args.clone and _modes != SectionModes():
        _parser.error('--clone cannot be combined with build section modes')

    try:
        _build = BuildModelBlock(_data, _modes)
        _slogger.info(
            'Build validation report:\n' + _json.dumps(_build.validation_report, indent=2),
            source='hammerspace.main',
        )
        if not _build.validation_report['valid']:
            raise ValueError('assembled model block failed validation')
        if _args.dry_run:
            _slogger.info('Dry run complete; output DAT, DOL, and FST were not modified.', source='hammerspace.main')
        else:
            WriteModelBlock(_build, _model_name)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as _exc:
        _slogger.error(
            f'Hammerspace operation failed | Model: {_model_name} | '
            f'{type(_exc).__name__}: {_exc}',
            source='hammerspace.main',
        )
        raise SystemExit(1)

    raise SystemExit(0)
