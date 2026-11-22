"""The parsed form of a ``.sluggie`` for a hammerspace build.

The dataclasses a build works on (``SluggieParsed`` and its parts), ``ParseSluggie`` and the parsers of
the rebuild entries, the build's ``SectionModes``, and the format constants that both the parser and
the builders need. A pure parse: nothing here reads the game files or validates an edit
(``HammerspaceValidate`` runs first).
"""

import struct
from dataclasses import dataclass, field

import UntanglePolicy
from binfmt import comp_size as _vb_comp_size, decode_field as _decode
import FacialPoseRebuild as _facial


# ---------------------------------------------------------------------------
# Parsed data structures
# ---------------------------------------------------------------------------

@dataclass
class DrawState:
    display_state_id:            int
    display_state_pad_bytes:     bytes
    prim_list_data:              bytes
    active_descriptors:          list   # [{'key': str, 'index_size': int}]
    shader_mode:                 str


@dataclass
class NormalBuffer:
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
    vertex_data:                    bytes   # edited if present, else original
    vertex_comp_count:              int
    vertex_quantize_info:           int
    uv_channels:                    list    # [UVChannel]
    color_channels:                 list    # [ColorChannel]
    draw_states:                    list    # [DrawState]
    normal_buffer:                  NormalBuffer | None   # None for skinned meshes
    preserve_source_layout:         bool
    rigid_rebuild:                  'RigidRebuild | None' = None
    skinned_rebuild:                'SkinnedRebuild | None' = None


@dataclass
class MeshData:
    submeshes: list   # [Submesh]


@dataclass
class CustomSubmeshUVChannel:
    channel_index:  int
    uv_data:        bytes
    uv_faces_data:  bytes


@dataclass
class CustomSubmeshTextureAssignment:
    donor_texture_index:          int | None
    additional_texture_file_name: str | None


@dataclass
class CustomSubmesh:
    """A hammerspace-only, user-created rigid
    submesh appended to a donor model. Kept fully separate from donor
    Submeshes/MeshData; nothing here is written into or derived from donor
    structures. Bone-ownership, template-source and other cross-checks are
    ``_validate_custom_submeshes``'s job, not this parse step."""
    custom_submesh_id:  str
    mesh_name:          str
    host_bone_id:       int
    template_source:    str
    vertex_data:        bytes
    vertex_quantize_info: int   # s16 position format; 59 unless the exporter had to widen it
    normal_data:        bytes | None
    normal_faces_data:  bytes | None
    color_data:         bytes | None
    color_faces_data:   bytes | None
    uv_channels:        list   # [CustomSubmeshUVChannel]
    faces_count:        int
    faces_data:         bytes
    texture_assignment: CustomSubmeshTextureAssignment
    specular_strength:  int | None = None   # Type-7 param byte 0; None keeps the template's
    color_quantize_info: int = 48           # 48 RGBA4444 (default) or 0 RGB565; both 2 bytes
    # Add-material surfaces of this custom
    # submesh, each an appended canonical record group (the same shape as a
    # rebuilt donor submesh's NewSurfaces), and the per-triangle surface
    # choice: 0 = the primary surface, k = additional_surfaces[k - 1]. An
    # empty tuple means every face draws through the primary surface.
    additional_surfaces: list = field(default_factory=list)   # [RigidRebuildNewSurface]
    face_surface_indices: tuple = ()


@dataclass
class RigidRebuildNewSurface:
    """One surface appended to a rebuilt donor rigid submesh
    (``RigidRebuild.NewSurfaces[]``)."""
    surface_key:        str     # sm<N>_new<K>
    material_name:      str     # for messages only
    template_source:    str     # rigid:<SurfaceId> | derived:<SurfaceId> | builtin:<name>
    texture_assignment: CustomSubmeshTextureAssignment
    specular_strength:  int | None = None


@dataclass
class RigidRebuild:
    """A donor rigid (CompCount 3) submesh
    rebuilt as a whole blob from Blender data -- changed topology, an Object
    Mode transform, a host-bone change that keeps the world position, or
    faces moved between surfaces. Every buffer is in the donor's own formats
    (positions in ``position_quantize`` when set: an s16 format coarser than
    the donor's, for a mesh that outgrew its range) and bone-local to
    ``host_bone_id``. The donor
    display states are kept; ``face_surface_table`` names, per surface key,
    which donor surface (``SurfaceId``) or appended new surface each triangle
    draws through."""
    host_bone_id:         int
    vertex_data:          bytes
    normal_data:          bytes | None
    normal_faces_data:    bytes | None
    color_data:           bytes | None
    color_faces_data:     bytes | None
    uv_channels:          list   # [CustomSubmeshUVChannel], the donor channel set
    faces_count:          int
    faces_data:           bytes
    face_surface_table:   list   # [str]
    face_surface_indices: tuple  # one index into face_surface_table per triangle
    new_surfaces:         list   # [RigidRebuildNewSurface], in append order
    reasons:              list   # [str], logging only
    facial_poses:         list | None = None   # [FacialPoseRebuild.RigidPoses]; None = export without FacialPoses
    position_quantize:    int | None = None    # VertexBufferQuantizeInfo; None = the donor's


@dataclass
class SkinnedRebuild:
    """The skinned submesh 0 rebuilt
    as a whole from Blender data -- any vertex and face count, weights on the
    bones the donor's own skin uses. ``vertex_data`` holds one interleaved
    position + normal record per vertex (6 components in the donor's position
    format, model space, Blender vertex order); ``influences`` lists
    ``(vertex, bone, weight)`` triples. The patcher assigns position slots in
    the canonical order (SkinnedRebuild.layout_skin), rebuilds the SKN from
    them and re-encodes one primitive list (triangle strips plus a
    GX_TRIANGLES block for the leftovers) per drawing surface. The donor
    display states are kept, as for a RigidRebuild. ``position_quantize``
    (when set) replaces the donor's position format in the GPL position and
    normal headers and the SKN header alike."""
    vertex_data:          bytes
    influences:           list   # [(vertex, bone, weight)]
    color_data:           bytes | None
    color_faces_data:     bytes | None
    uv_channels:          list   # [CustomSubmeshUVChannel], the donor channel set
    faces_count:          int
    faces_data:           bytes
    face_surface_table:   list   # [str]
    face_surface_indices: tuple  # one index into face_surface_table per triangle
    new_surfaces:         list   # [RigidRebuildNewSurface], in append order
    reasons:              list   # [str], logging only
    facial_poses:         list = field(default_factory=list)   # [FacialPoseRebuild.BodyPoses]
    position_quantize:    int | None = None    # VertexBufferQuantizeInfo; None = the donor's


@dataclass
class Bone:
    bone_id:            int
    skinned:            bool
    track_id:           int
    srt_type:           int    # SRT block type byte (0x4 / 0x8 / 0xc; 0 when no SRT)
    translation:        list   # [x, y, z]
    scale:              list   # [x, y, z]
    quaternion:         list   # [w, x, y, z]


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


@dataclass
class TextureData:
    textures: list   # [Texture]


@dataclass
class SK1:
    bone_index:                  int
    vertex_cnt:                  int
    vertex_offset:               int
    bind_pose_data:              bytes   # edited if present, else original
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
    vertex_arr_absolute_ptr:   int
    dest_arr_absolute_ptr:     int
    gpl_dest_arr_value:        int
    weight_arr_absolute_ptr:   int


@dataclass
class SkinningData:
    skn_offset:                int
    mem_clr_ptr_value:         int
    mem_clr_size:              int
    flush_ind_absolute_ptr:    int | None
    flush_ind_size:            int
    flush_ind_data:            bytes
    quantize_info:             int
    sk1s:                      list   # [SK1]
    sk2s:                      list   # [SK2]
    sk_accs:                   list   # [SKAcc]
    preserve_source_layout:    bool = False


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
    trailing_sections:  list[TrailingSection]  # schema-backed ptr6/ptr7/ptr8 payloads
    model_offset:       int                 # absolute byte offset of model block in INPUT dat
    model_length:       int                 # byte length of model block in INPUT dat
    custom_submeshes:   list                # [CustomSubmesh], empty when none requested


def _hex(val: str | int) -> int:
    """Accept either a hex string ('0x…') or a plain integer."""
    if isinstance(val, str):
        return int(val, 16)
    return int(val)


# Stadium model directories, 7 Mario Stadium ... 16 Toy Field. Every
# vanilla stadium bone is a root. New bones there may be roots (new root bones
# draw correctly, Dolphin 2026-10-18; the earlier "does not draw" results were a
# one-colour-entry bug, see _assemble_rigid_submesh) or children (a mesh on a new
# child bone follows parent then child, Dolphin 2026-10-18, E7).
STADIUM_CHUNKS = UntanglePolicy.STADIUM_DIRS

_CUSTOM_SUBMESH_POSITION_FORMAT = (3, 59)  # CompCount, default QuantizeInfo
# Accepted VertexBufferQuantizeInfo values, coarsest first: the s16 formats from
# 48 (0x30, shift 0, +/-32767) up to the default 59 (0x3B, shift 11, +/-16).
# The exporter's CustomSubmeshExport.POSITION_QUANTIZE_CANDIDATES is the same
# set, ordered finest first -- it picks the most precise one the mesh fits in,
# so a prop far from its host bone widens the range instead of being rejected.
_CUSTOM_SUBMESH_POSITION_QUANTIZE_RANGE = tuple(range(0x30, 0x3C))
# Float positions (format 4, shift 0), as every stadium submesh stores them.
# Not written by the exporter; set by hand in the .sluggie for testing.
_CUSTOM_SUBMESH_FLOAT_POSITION_QUANTIZE = 0x40
_CUSTOM_SUBMESH_POSITION_QUANTIZE_VALUES = (
    _CUSTOM_SUBMESH_POSITION_QUANTIZE_RANGE + (_CUSTOM_SUBMESH_FLOAT_POSITION_QUANTIZE,)
)


def _custom_submesh_position_stride(quantize_info: int) -> int:
    """Bytes per custom-submesh position entry (3 components)."""
    return 3 * _vb_comp_size(quantize_info)
_CUSTOM_SUBMESH_NORMAL_FORMAT   = (3, 62)
_CUSTOM_SUBMESH_UV_FORMAT       = (2, 62)
_CUSTOM_SUBMESH_COLOR_FORMAT    = (4, 48)
# Optional CustomSubmeshes[].ColorChannelQuantizeInfo -> colour CompCount. Both
# are 2 bytes per entry (_CUSTOM_SUBMESH_COLOR_STRIDE). Stadiums use RGB565
# (3, 0) on 133 of 228 colour arrays; characters' rigid submeshes use both.
_CUSTOM_SUBMESH_COLOR_FORMATS   = {48: 4, 0: 3}


def _u16_values(raw: bytes) -> tuple[int, ...]:
    return struct.unpack(f'>{len(raw) // 2}H', raw) if raw else ()


def _parse_new_surfaces(entries) -> list['RigidRebuildNewSurface']:
    """Decode a ``NewSurfaces`` (rigid rebuild) or ``AdditionalSurfaces``
    (custom submesh) list, which share one shape."""
    new_surfaces = []
    for entry in entries or []:
        assignment = entry.get('TextureAssignment') or {}
        new_surfaces.append(RigidRebuildNewSurface(
            surface_key=entry['SurfaceKey'],
            material_name=entry.get('MaterialName', entry['SurfaceKey']),
            template_source=entry['TemplateSource'],
            texture_assignment=CustomSubmeshTextureAssignment(
                donor_texture_index=assignment.get('DonorTextureIndex'),
                additional_texture_file_name=assignment.get('AdditionalTextureFileName'),
            ),
            specular_strength=entry.get('SpecularStrength'),
        ))
    return new_surfaces


def _parse_rigid_rebuild(sub: dict, use_compression: bool) -> 'RigidRebuild | None':
    """Decode ``Submeshes[i].RigidRebuild`` (validated by
    ``_validate_rigid_rebuilds``) into its dataclass; None when absent."""
    raw = sub.get('RigidRebuild')
    if not raw:
        return None

    def _optional(field: str) -> bytes | None:
        return _decode(raw[field], use_compression) if raw.get(field) is not None else None

    new_surfaces = _parse_new_surfaces(raw.get('NewSurfaces'))
    indices_raw = _optional('FaceSurfaceIndices') or b''
    return RigidRebuild(
        host_bone_id=int(raw['HostBoneId']),
        vertex_data=_decode(raw['VertexBufferData'], use_compression),
        normal_data=_optional('NormalBufferData'),
        normal_faces_data=_optional('NormalFacesData'),
        color_data=_optional('ColorChannelData'),
        color_faces_data=_optional('ColorFacesData'),
        uv_channels=[
            CustomSubmeshUVChannel(
                channel_index=int(uv['UVChannelIndex']),
                uv_data=_decode(uv['UVChannelData'], use_compression),
                uv_faces_data=_decode(uv['UVFacesData'], use_compression),
            )
            for uv in raw.get('UVChannels') or []
        ],
        faces_count=int(raw['FacesCount']),
        faces_data=_decode(raw['FacesData'], use_compression),
        face_surface_table=list(raw['FaceSurfaceTable']),
        face_surface_indices=struct.unpack(f'>{len(indices_raw) // 2}H', indices_raw),
        new_surfaces=new_surfaces,
        reasons=list(raw.get('Reason') or []),
        facial_poses=None if raw.get('FacialPoses') is None else [
            _facial.RigidPoses(
                object_index=int(entry['ObjectIndex']),
                vertices=list(_u16_values(_decode(entry['Vertices'], use_compression) or b'')),
                deltas=[_decode(delta, use_compression) for delta in entry.get('PoseDeltas') or []],
                normal_entries=(
                    None if entry.get('NormalEntries') is None
                    else list(_u16_values(_decode(entry['NormalEntries'], use_compression) or b''))
                ),
                normal_deltas=[_decode(delta, use_compression) for delta in entry.get('NormalPoseDeltas') or []],
            )
            for entry in raw['FacialPoses']
        ],
        position_quantize=raw.get('VertexBufferQuantizeInfo'),
    )


_SKINNED_REBUILD_COMP_COUNT = 6
_SKINNED_REBUILD_INFLUENCE = struct.Struct('>HHf')   # (vertex, bone, weight)
_SKINNED_REBUILD_SUBMESH = 0


def _parse_skinned_rebuild(sub: dict, use_compression: bool) -> 'SkinnedRebuild | None':
    """Decode ``Submeshes[0].SkinnedRebuild`` (validated by
    ``_validate_skinned_rebuild``) into its dataclass; None when absent."""
    raw = sub.get('SkinnedRebuild')
    if not raw:
        return None

    def _optional(field_name: str) -> bytes | None:
        return _decode(raw[field_name], use_compression) if raw.get(field_name) is not None else None

    indices_raw = _optional('FaceSurfaceIndices') or b''
    influences_raw = _optional('Influences') or b''
    return SkinnedRebuild(
        vertex_data=_decode(raw['VertexBufferData'], use_compression),
        influences=[
            (int(vertex), int(bone), float(weight))
            for vertex, bone, weight in _SKINNED_REBUILD_INFLUENCE.iter_unpack(influences_raw)
        ],
        color_data=_optional('ColorChannelData'),
        color_faces_data=_optional('ColorFacesData'),
        uv_channels=[
            CustomSubmeshUVChannel(
                channel_index=int(uv['UVChannelIndex']),
                uv_data=_decode(uv['UVChannelData'], use_compression),
                uv_faces_data=_decode(uv['UVFacesData'], use_compression),
            )
            for uv in raw.get('UVChannels') or []
        ],
        faces_count=int(raw['FacesCount']),
        faces_data=_decode(raw['FacesData'], use_compression),
        face_surface_table=list(raw['FaceSurfaceTable']),
        face_surface_indices=struct.unpack(f'>{len(indices_raw) // 2}H', indices_raw),
        new_surfaces=_parse_new_surfaces(raw.get('NewSurfaces')),
        reasons=list(raw.get('Reason') or []),
        facial_poses=[
            _facial.BodyPoses(
                object_index=int(entry['ObjectIndex']),
                vertices=[value for (value,) in struct.iter_unpack('>H', _decode(entry['Vertices'], use_compression))],
                deltas=[_decode(delta, use_compression) for delta in entry.get('PoseDeltas') or []],
            )
            for entry in raw.get('FacialPoses') or []
        ],
        position_quantize=raw.get('VertexBufferQuantizeInfo'),
    )


@dataclass(frozen=True)
class SectionModes:
    """Which sections a build rebuilds (``'build'``) instead of cloning from the donor. ACT and the
    ptr6/ptr7/ptr8 tail have no mode: the edits in the file decide (``BuildACTBoneHierarchy``,
    ``_build_trailing_sections``)."""
    gpl: str = 'clone'
    tex: str = 'clone'
    skn: str = 'clone'

    def as_dict(self) -> dict[str, str]:
        return {
            'GPL': self.gpl,
            'TEX': self.tex,
            'SKN': self.skn,
        }


# ---------------------------------------------------------------------------
# ParseSluggie
# ---------------------------------------------------------------------------

def _parse_submesh(index: int, sub: dict, use_compression: bool, position_edit_submeshes: set) -> Submesh:
    """One ``Submeshes`` entry. Edited arrays are used only where the matching primitive lists
    were rebuilt too (see the flags below)."""
    faces_count = sub.get('FacesCountEdited', sub['FacesCount'])
    faces_data  = _decode(sub.get('FacesDataEdited') or sub['FacesData'], use_compression)

    vb          = sub['VertexBuffer']

    # Determine whether prim lists have been rebuilt.  If not, the GPU
    # draw commands still reference the ORIGINAL compact UV/color arrays,
    # so we must use the original data even if *Edited fields exist.
    _prim_lists_edited = any(
        'PrimListDataEdited' in ds for ds in sub.get('DisplayStates', [])
    )
    _surface_only_rebuild = sub.get('SurfaceAssignmentsRebuiltByImporter', False)
    _position_only_edited = index in position_edit_submeshes
    _uv_arrays_edited = sub.get('UVArraysEditedByImporter', False)
    _uv_primitive_lists_rebuilt = sub.get('UVPrimitiveListsRebuiltByImporter', False)
    _topology_arrays_edited = (
        _prim_lists_edited
        and not _surface_only_rebuild
        and not _uv_primitive_lists_rebuilt
    )
    _geometry_arrays_edited = _topology_arrays_edited or _position_only_edited

    if _geometry_arrays_edited:
        vertex_data = _decode(vb.get('VertexBufferDataEdited') or vb['VertexBufferData'], use_compression)
    else:
        vertex_data = _decode(vb['VertexBufferData'], use_compression)

    uv_channels = []
    for uv in sub.get('UVChannels', []):
        if _uv_arrays_edited or _topology_arrays_edited:
            _uv_src = uv.get('UVChannelDataEdited') or uv['UVChannelData']
        else:
            _uv_src = uv['UVChannelData']
        uv_channels.append(UVChannel(
            channel_index            = uv['UVChannelIndex'],
            palette_name             = uv['PaletteName'],
            texture_index            = uv.get('TextureIndex', 0),
            wrap_s                   = uv.get('WrapS', 0),
            wrap_t                   = uv.get('WrapT', 0),
            uv_data                  = _decode(_uv_src, use_compression),
            uv_faces_data            = _decode(uv.get('UVFacesDataEdited')   or uv['UVFacesData'],   use_compression),
            comp_count               = uv['UVChannelCompCount'],
            quantize_info            = uv['UVChannelQuantizeInfo'],
        ))

    color_channels = []
    _color_arrays_edited = (
        sub.get('ColorArraysEditedByImporter', False)
        or _topology_arrays_edited
    )
    for cc in sub.get('ColorChannels', []):
        if _color_arrays_edited:
            _color_src = cc.get('ColorChannelDataEdited') or cc['ColorChannelData']
            _color_faces_src = cc.get('ColorFacesDataEdited') or cc['ColorFacesData']
        else:
            _color_src = cc['ColorChannelData']
            _color_faces_src = cc['ColorFacesData']
        color_channels.append(ColorChannel(
            channel_index    = cc['ColorChannelIndex'],
            color_data       = _decode(_color_src, use_compression),
            color_faces_data = _decode(_color_faces_src, use_compression),
            comp_count       = cc['ColorChannelCompCount'],
            quantize_info    = cc['ColorChannelQuantizeInfo'],
        ))

    draw_states = []
    for ds in sub.get('DisplayStates', []):
        pad_hex = ds.get('DisplayStateParamBytesEdited') or ds.get('DisplayStateParamBytes', '000000')
        prim_list_data = (
            ds.get('PrimListDataEdited')
            if 'PrimListDataEdited' in ds
            else ds.get('PrimListData')
        )
        if prim_list_data in (None, ''):
            prim_list_bytes = b''
        else:
            prim_list_bytes = _decode(prim_list_data, use_compression)
        draw_states.append(DrawState(
            display_state_id            = ds['DisplayStateId'],
            display_state_pad_bytes     = bytes.fromhex(pad_hex),
            prim_list_data              = prim_list_bytes,
            active_descriptors          = ds.get('VertexStreamLayout') or ds.get('ActiveDescriptors', []),
            shader_mode                 = ds.get('ShaderModeEdited') or ds.get('ShaderMode', ''),
        ))

    raw_nb = sub.get('NormalBuffer')
    normal_buffer = None
    if raw_nb:
        normal_buffer = NormalBuffer(
            comp_count                   = raw_nb['NormalBufferCompCount'],
            quantize_info                = raw_nb['NormalBufferQuantizeInfo'],
            ambient_pct                  = raw_nb.get('NormalAmbientPct', 0.0),
            normal_data                  = _decode(
                (
                    raw_nb.get('NormalBufferDataEdited') or raw_nb['NormalBufferData']
                    if _topology_arrays_edited
                    or sub.get('NormalArraysEditedByImporter', False)
                    else raw_nb['NormalBufferData']
                ),
                use_compression,
            ),
        )

    return Submesh(
        submesh_index                  = index,
        mesh_name                      = sub.get('MeshName', ''),
        faces_count                    = faces_count,
        faces_data                     = faces_data,
        vertex_data                    = vertex_data,
        vertex_comp_count              = vb['VertexBufferCompCount'],
        vertex_quantize_info           = vb['VertexBufferQuantizeInfo'],
        uv_channels                    = uv_channels,
        color_channels                 = color_channels,
        draw_states                    = draw_states,
        normal_buffer                  = normal_buffer,
        preserve_source_layout         = not (
            _geometry_arrays_edited or _uv_primitive_lists_rebuilt
        ),
        rigid_rebuild                  = _parse_rigid_rebuild(sub, use_compression),
        skinned_rebuild                = _parse_skinned_rebuild(sub, use_compression),
    )


def _parse_bones(model: dict, use_compression: bool) -> BoneData | None:
    """``BoneHierarchyEdited`` when present, else ``BoneHierarchy``."""
    raw_bones = model.get('BoneHierarchyEdited') or model.get('BoneHierarchy')
    if raw_bones:
        bones = []
        for b in raw_bones:
            bones.append(Bone(
                bone_id            = b['BoneId'],
                skinned            = b['Skinned'],
                track_id           = b['TrackId'],
                srt_type           = b.get('SRTType', 0),
                translation        = b['Translation'],
                scale              = b['Scale'],
                quaternion         = b['Quaternion'],
            ))
        bone_data = BoneData(bones=bones)
    else:
        bone_data = None
    return bone_data


def _parse_textures(model: dict, use_compression: bool) -> TextureData:
    """``TextureDescriptors``."""
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
            desc_unknown_at_10        = _decode(tex['DescUnknownAt10'], use_compression) if tex.get('DescUnknownAt10') else bytes(7),
            desc_unknown_at_1b        = _decode(tex['DescUnknownAt1B'], use_compression) if tex.get('DescUnknownAt1B') else bytes(5),
            image_data_offset         = _hex(tex['ImageDataOffset']),
            image_data_length         = tex['ImageDataLength'],
            palette_data_offset       = _hex(tex['PaletteDataOffset']) if tex.get('PaletteDataOffset') else None,
            palette_data_length       = tex.get('PaletteDataLength'),
        ))

    texture_data = TextureData(textures=textures)
    return texture_data


def _donor_aligned_weights(entry: dict, use_compression: bool):
    """``WeightDataEdited`` of a donor SK2/SKAcc entry when it can stand in for ``WeightData``, else None.

    The field holds the entry's weights in the donor's order: an in-place export writes it on every entry,
    a Hammerspace export on the entries with repainted weights. It is used only at the donor's length (an
    in-place vertex-count edit shortens it, and only the in-place patcher applies that)."""
    edited = entry.get('WeightDataEdited')
    if edited is None or entry.get('VertexCntEdited') is not None:
        return None
    if len(_decode(edited, use_compression)) != len(_decode(entry['WeightData'], use_compression)):
        raise ValueError(
            f"WeightDataEdited of the skin entry of bone "
            f"{entry.get('BoneIndex', entry.get('BoneIndex1'))} does not have the length of its WeightData")
    return edited


def _donor_aligned_bind_pose(entry: dict, use_compression: bool):
    """``BindPoseDataEdited`` of a donor SK entry when it can stand in for ``BindPoseData``, else None.

    An in-place export writes the field on every entry (the bind-pose positions and normals of its
    vertices, in the donor's order). Like the weights it is used only at the donor's length."""
    edited = entry.get('BindPoseDataEdited')
    if edited is None or entry.get('VertexCntEdited') is not None:
        return None
    if len(_decode(edited, use_compression)) != len(_decode(entry['BindPoseData'], use_compression)):
        raise ValueError(
            f"BindPoseDataEdited of the skin entry of bone "
            f"{entry.get('BoneIndex', entry.get('BoneIndex1'))} does not have the length of its BindPoseData")
    return edited


def _edits_by_occurrence(edits: list, key_of) -> dict:
    """``{key: [edited entries with that key, in file order]}``."""
    by_key = {}
    for edit in edits:
        by_key.setdefault(key_of(edit), []).append(edit)
    return by_key


def _nth_edit(by_key: dict, seen: dict, key):
    """The edited entry for the next donor entry with *key*: a bone (or pair) that owns several donor
    entries takes them in order, each donor entry its own. When the edit has fewer entries for the key
    than the donor, the last one is used again."""
    edits = by_key.get(key)
    if not edits:
        return None
    position = seen.get(key, 0)
    seen[key] = position + 1
    return edits[min(position, len(edits) - 1)]


def _parse_skinning(model: dict, use_compression: bool, position_edit_submeshes: set) -> SkinningData | None:
    """``SkinData``, with the edited payload of ``SkinDataEdited`` where the geometry was edited, and with
    the weights of ``WeightDataEdited`` on the donor entries where the layout is kept."""
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
    _topology_geometry_edited = any(
        'PrimListDataEdited' in ds
        for sub in model.get('Submeshes', [])
        for ds in sub.get('DisplayStates', [])
    )
    _position_geometry_edited = bool(position_edit_submeshes)
    _geometry_edited = _topology_geometry_edited or _position_geometry_edited
    raw_skn = raw_skn_orig or raw_skn_edit
    if raw_skn_edit and raw_skn_edit.get('MembershipEdited'):
        raise ValueError(
            'this file moves vertices between bones as a SkinDataEdited membership edit, '
            'which the patcher no longer applies; re-export it with add-on 0.8.5 or later '
            '(the body is rebuilt instead)'
        )
    # Flush-index data is rewritten onto SkinDataEdited by the topology-edit
    # path, since it depends on the SKAcc write set. raw_skn always prefers
    # raw_skn_orig when the donor has skin data, so pull flush data from
    # raw_skn_edit specifically whenever that pass produced it.
    _flush_source = (
        raw_skn_edit
        if (_topology_geometry_edited and raw_skn_edit
            and raw_skn_edit.get('FlushIndData') is not None)
        else raw_skn
    )
    if raw_skn:
        # Build lookup dicts from edited data for payload substitution.
        # A bone can own several entries (the source size limits split them), so the n-th donor entry
        # of a bone takes the n-th edited entry of that bone.
        _edit_sk1_by_bone = _edits_by_occurrence(
            (raw_skn_edit or {}).get('SK1s', []), lambda s: s['BoneIndex'])
        _edit_sk2_by_pair = _edits_by_occurrence(
            (raw_skn_edit or {}).get('SK2s', []), lambda s: (s['BoneIndex1'], s['BoneIndex2']))
        _edit_acc_by_bone = _edits_by_occurrence(
            (raw_skn_edit or {}).get('SKAccs', []), lambda s: s['BoneIndex'])
        _seen_sk1, _seen_sk2, _seen_acc = {}, {}, {}

        position_edit_lists = None
        if _position_geometry_edited and raw_skn_orig and raw_skn_edit:
            position_edit_lists = {}
            identity_fields = {
                'SK1s': ('BoneIndex', 'VertexCnt', 'GplVertexArrValue'),
                'SK2s': (
                    'BoneIndex1', 'BoneIndex2', 'VertexCnt', 'GplVertexArrValue'
                ),
                'SKAccs': ('BoneIndex', 'VertexCnt', 'GplDestArrValue'),
            }
            for list_name, fields in identity_fields.items():
                originals = raw_skn_orig.get(list_name, [])
                edits = raw_skn_edit.get(list_name, [])
                if len(edits) != len(originals):
                    hint = ''
                    if not edits:
                        # An export that included no skinned mesh used to write
                        # an empty SkinDataEdited over the donor's structure.
                        hint = (
                            '. SkinDataEdited is empty, which means the export that '
                            'wrote this file did not include the skinned mesh. Select '
                            'the skinned mesh along with whatever else you are '
                            'exporting and export again'
                        )
                    raise ValueError(
                        f'position-only SKN edit changed {list_name} count from '
                        f'{len(originals)} to {len(edits)}{hint}')
                edits_by_identity = {}
                for edit in edits:
                    identity = tuple(edit.get(field) for field in fields)
                    edits_by_identity.setdefault(identity, []).append(edit)
                ordered_edits = []
                for entry_index, original in enumerate(originals):
                    identity = tuple(original.get(field) for field in fields)
                    candidates = edits_by_identity.get(identity)
                    if not candidates:
                        raise ValueError(
                            f'position-only SKN edit has no match for donor '
                            f'{list_name}[{entry_index}] identity {identity}')
                    ordered_edits.append(candidates.pop(0))
                extras = sum(len(candidates) for candidates in edits_by_identity.values())
                if extras:
                    raise ValueError(
                        f'position-only SKN edit contains {extras} unmatched '
                        f'{list_name} entries')
                position_edit_lists[list_name] = ordered_edits

        sk1s = []
        for entry_index, s in enumerate(raw_skn.get('SK1s', [])):
            ed = (
                position_edit_lists['SK1s'][entry_index]
                if position_edit_lists is not None
                else _nth_edit(_edit_sk1_by_bone, _seen_sk1, s['BoneIndex']) if _geometry_edited else None
            )
            if _geometry_edited and ed:
                bp_src = ed.get('BindPoseDataEdited') or ed.get('BindPoseData') or s['BindPoseData']
            else:
                # No model-level edit for this entry: an in-place bind-pose edit sits on the donor entry.
                bp_src = _donor_aligned_bind_pose(s, use_compression) or s['BindPoseData']
            sk1s.append(SK1(
                bone_index                  = s['BoneIndex'],
                vertex_cnt                  = (ed or s)['VertexCnt'],
                vertex_offset               = s.get('VertexOffset', 0),
                bind_pose_data              = _decode(bp_src, use_compression),
                vertex_arr_absolute_ptr     = _hex(s.get('VertexArrAbsolutePtr',     '0x0')),
                gpl_vertex_arr_value        = s.get('GplVertexArrValue', 0),
            ))

        sk2s = []
        for entry_index, s in enumerate(raw_skn.get('SK2s', [])):
            key = (s['BoneIndex1'], s['BoneIndex2'])
            ed = (
                position_edit_lists['SK2s'][entry_index]
                if position_edit_lists is not None
                else _nth_edit(_edit_sk2_by_pair, _seen_sk2, key) if _geometry_edited else None
            )
            if _geometry_edited and ed:
                bp_src = ed.get('BindPoseDataEdited') or ed.get('BindPoseData') or s['BindPoseData']
                wt_src = (
                    s['WeightData']
                    if position_edit_lists is not None
                    else ed.get('WeightDataEdited') or ed.get('WeightData') or s['WeightData']
                )
            else:
                bp_src = _donor_aligned_bind_pose(s, use_compression) or s['BindPoseData']
                wt_src = s['WeightData']
            if position_edit_lists is not None or not (_geometry_edited and ed):
                # The donor's slots are kept: repainted weights come from the donor-aligned field.
                wt_src = _donor_aligned_weights(s, use_compression) or wt_src
            sk2s.append(SK2(
                bone_index1                 = s['BoneIndex1'],
                bone_index2                 = s['BoneIndex2'],
                vertex_cnt                  = (ed or s)['VertexCnt'],
                vertex_offset               = s.get('VertexOffset', 0),
                bind_pose_data              = _decode(bp_src, use_compression),
                weight_data                 = _decode(wt_src, use_compression),
                vertex_arr_absolute_ptr     = _hex(s.get('VertexArrAbsolutePtr',     '0x0')),
                weight_arr_absolute_ptr     = _hex(s.get('WeightArrAbsolutePtr',     '0x0')),
                gpl_vertex_arr_value        = s.get('GplVertexArrValue', 0),
            ))

        sk_accs = []
        for entry_index, s in enumerate(raw_skn.get('SKAccs', [])):
            ed = (
                position_edit_lists['SKAccs'][entry_index]
                if position_edit_lists is not None
                else _nth_edit(_edit_acc_by_bone, _seen_acc, s['BoneIndex']) if _geometry_edited else None
            )
            if _geometry_edited and ed:
                bp_src = ed.get('BindPoseDataEdited') or ed.get('BindPoseData') or s['BindPoseData']
                wt_src = (
                    s['WeightData']
                    if position_edit_lists is not None
                    else ed.get('WeightDataEdited') or ed.get('WeightData') or s['WeightData']
                )
            else:
                bp_src = _donor_aligned_bind_pose(s, use_compression) or s['BindPoseData']
                wt_src = s['WeightData']
            if position_edit_lists is not None or not (_geometry_edited and ed):
                wt_src = _donor_aligned_weights(s, use_compression) or wt_src
            dest_src = (
                s['DestIndexData']
                if position_edit_lists is not None
                else (ed or s).get('DestIndexData') or s['DestIndexData']
            )
            sk_accs.append(SKAcc(
                bone_index                = s['BoneIndex'],
                vertex_cnt                = (ed or s)['VertexCnt'],
                bind_pose_data            = _decode(bp_src, use_compression),
                dest_index_data           = _decode(dest_src, use_compression),
                weight_data               = _decode(wt_src, use_compression),
                vertex_arr_absolute_ptr   = _hex(s.get('VertexArrAbsolutePtr',   '0x0')),
                dest_arr_absolute_ptr     = _hex(s.get('DestArrAbsolutePtr',     '0x0')),
                gpl_dest_arr_value        = s.get('GplDestArrValue', 0),
                weight_arr_absolute_ptr   = _hex(s.get('WeightArrAbsolutePtr',   '0x0')),
            ))

    if raw_skn:
        skinning_data = SkinningData(
            skn_offset                = _hex(raw_skn.get('SKNOffset',                '0x0')),
            mem_clr_ptr_value         = raw_skn.get(
                'MemClrPtrValue',
                _hex(raw_skn.get('MemClrAbsolutePtr', '0x0'))
                - _hex(raw_skn.get('GplBaseOffset', '0x0')),
            ),
            mem_clr_size              = raw_skn.get('MemClrSize', 0),
            flush_ind_absolute_ptr    = _hex(raw_skn['FlushIndAbsolutePtr']) if raw_skn.get('FlushIndAbsolutePtr') else None,
            flush_ind_size            = _flush_source.get('FlushIndSize', 0),
            flush_ind_data            = _decode(_flush_source['FlushIndData'], use_compression) if _flush_source.get('FlushIndData') else b'',
            quantize_info             = raw_skn['QuantizeInfo'],
            sk1s                      = sk1s,
            sk2s                      = sk2s,
            sk_accs                   = sk_accs,
            preserve_source_layout    = not _topology_geometry_edited,
        )
    else:
        skinning_data = None
    return skinning_data


def _parse_trailing_sections(model: dict, use_compression: bool) -> list:
    """``TrailingSections``."""
    trailing_sections = []
    for entry in model.get('TrailingSections', []) or []:
        if not isinstance(entry, dict):
            continue
        trailing_sections.append(TrailingSection(
            header_field_offset = _hex(entry.get('HeaderFieldOffset', '0x0')),
            original_ptr        = _hex(entry.get('OriginalPtr', '0x0')),
            data                = _decode(entry.get('Data'), use_compression) if entry.get('Data') else b'',
        ))
    return trailing_sections


def _parse_custom_submeshes(model: dict, use_compression: bool) -> list:
    """``CustomSubmeshes``."""
    # Parse only, never touching donor
    # Submeshes/MeshData. Cross-checks (host bone freedom, template validity,
    # texture-assignment resolution, ...) are _validate_custom_submeshes' job.
    custom_submeshes = []
    for cs in model.get('CustomSubmeshes', []) or []:
        cs_uv_channels = [
            CustomSubmeshUVChannel(
                channel_index = uv['UVChannelIndex'],
                uv_data       = _decode(uv['UVChannelData'], use_compression),
                uv_faces_data = _decode(uv['UVFacesData'], use_compression),
            )
            for uv in cs.get('UVChannels', [])
        ]
        raw_texture_assignment = cs.get('TextureAssignment', {}) or {}
        texture_assignment = CustomSubmeshTextureAssignment(
            donor_texture_index          = raw_texture_assignment.get('DonorTextureIndex'),
            additional_texture_file_name = raw_texture_assignment.get('AdditionalTextureFileName'),
        )
        custom_submeshes.append(CustomSubmesh(
            custom_submesh_id  = cs['CustomSubmeshId'],
            mesh_name          = cs['MeshName'],
            host_bone_id       = cs['HostBoneId'],
            template_source    = cs['TemplateSource'],
            vertex_data        = _decode(cs['VertexBufferData'], use_compression),
            vertex_quantize_info = cs.get(
                'VertexBufferQuantizeInfo', _CUSTOM_SUBMESH_POSITION_FORMAT[1]
            ),
            normal_data        = _decode(cs['NormalBufferData'], use_compression) if cs.get('NormalBufferData') else None,
            normal_faces_data  = _decode(cs['NormalFacesData'], use_compression) if cs.get('NormalFacesData') else None,
            color_data         = _decode(cs['ColorChannelData'], use_compression) if cs.get('ColorChannelData') else None,
            color_faces_data   = _decode(cs['ColorFacesData'], use_compression) if cs.get('ColorFacesData') else None,
            uv_channels        = cs_uv_channels,
            faces_count        = cs['FacesCount'],
            faces_data         = _decode(cs['FacesData'], use_compression),
            texture_assignment = texture_assignment,
            specular_strength  = cs.get('SpecularStrength'),
            color_quantize_info = cs.get('ColorChannelQuantizeInfo', _CUSTOM_SUBMESH_COLOR_FORMAT[1]),
            additional_surfaces = _parse_new_surfaces(cs.get('AdditionalSurfaces')),
            face_surface_indices = (
                _u16_values(_decode(cs['FaceSurfaceIndices'], use_compression))
                if cs.get('FaceSurfaceIndices') is not None else ()
            ),
        ))
    return custom_submeshes


def ParseSluggie(data: dict, position_edit_submeshes=()) -> SluggieParsed:
    """Parse the JSON contents of a .sluggies file into in-memory data
    structures ready for hammerspace block assembly.

    Prefers *Edited fields (written by the Blender exporter in Hammerspace
    Mode) over the original data where both are present.

    Parameters
    ----------
    data:
        The full parsed JSON dict (top-level object from the .sluggies file,
        which has a single key ``'SluggiesModel'``). It is not modified.
    position_edit_submeshes:
        Indices of the submeshes whose positions are edited without a topology
        change (``_position_edits``); their edited position and skin arrays are used.

    Returns
    -------
    SluggieParsed
        Container holding MeshData, BoneData (or None), TextureData, and
        SkinningData (or None).
    """

    model = data['SluggiesModel']
    use_compression = model.get('UseCompression', True)
    position_edit_submeshes = set(position_edit_submeshes)

    mesh_data = MeshData(
        submeshes=[
            _parse_submesh(index, sub, use_compression, position_edit_submeshes)
            for index, sub in enumerate(model.get('Submeshes', []))
        ],
    )
    bone_data = _parse_bones(model, use_compression)
    texture_data = _parse_textures(model, use_compression)
    skinning_data = _parse_skinning(model, use_compression, position_edit_submeshes)
    trailing_sections = _parse_trailing_sections(model, use_compression)

    return SluggieParsed(
        mesh              = mesh_data,
        bones             = bone_data,
        textures          = texture_data,
        skinning          = skinning_data,
        trailing_sections = trailing_sections,
        model_offset      = _hex(model.get('ModelOffset', '0x0')),
        model_length      = model.get('ModelLength', 0),
        custom_submeshes  = _parse_custom_submeshes(model, use_compression),
    )


EXCLUDED_KEY = 'Excluded'   # BlenderAddonSrc/ExportMode.EXCLUDED_KEY


def excluded_submesh_indices(model: dict) -> list:
    """Indices of the donor submeshes the export left out (``Excluded``).
    Raises ValueError for one that cannot be excluded: the skinned body, or
    one that also carries a rebuild (its blob would be replaced)."""
    indices = []
    for index, submesh in enumerate(model.get('Submeshes', [])):
        if not submesh.get(EXCLUDED_KEY):
            continue
        if index == 0 or int(submesh.get('VertexBuffer', {}).get('VertexBufferCompCount', 0)) == 6:
            raise ValueError(f'sub{index}: the skinned body cannot be excluded')
        if submesh.get('RigidRebuild') is not None or submesh.get('SkinnedRebuild') is not None:
            raise ValueError(f'sub{index}: excluded and rebuilt at once; export the model again')
        indices.append(index)
    return indices
