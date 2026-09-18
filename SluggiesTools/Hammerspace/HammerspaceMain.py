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

import drawlist
import HammerspaceHelper as hh
from BlockValidator import validate_model_block
from GeometryRebuild import (
    _color_entry_size,
    apply_desired_texture_assignments,
    layout_skin_membership_edit,
    rebuild_edited_uvs,
    rebuild_surface_assignments,
)
from ArchiveContainer import parse_archive_container, rebuild_archive_container
from ModelFormat import align_array_offset, compute_mem_clear_range, pad_array
from InplacePatcher import root_scale as _root_scale
import act_rebuild


def _source_dat_path(absolute_offset: int) -> str:
    """Return the DAT containing data at an exported absolute offset."""
    return hh.OUTPUT_DAT if absolute_offset >= hh.BASE_SIZE else hh.INPUT_DAT


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
    preserve_source_layout:         bool


@dataclass
class MeshData:
    submeshes: list   # [Submesh]
    source_gpl_base_offset: int


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
    """PLAN_AddSubmesh.md Phase 1: a hammerspace-only, user-created rigid
    submesh appended to a donor model. Kept fully separate from donor
    Submeshes/MeshData; nothing here is written into or derived from donor
    structures. Bone-ownership, template-source and other cross-checks are
    Phase 1 step 3's job, not this parse step."""
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
    custom_submeshes:   list                # [CustomSubmesh], empty when none requested


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


def _position_edits(model: dict) -> list[tuple[int, dict, bytes]]:
    """Return validated unchanged-count position edits as (index, submesh, bytes)."""
    use_b64 = model.get('UseBase64', True)
    edits = []
    for submesh_index, submesh in enumerate(model.get('Submeshes', [])):
        vertex_buffer = submesh.get('VertexBuffer', {})
        encoded_edit = vertex_buffer.get('VertexBufferDataEdited')
        if encoded_edit is None:
            continue
        original = _decode(vertex_buffer['VertexBufferData'], use_b64)
        edited = _decode(encoded_edit, use_b64)
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
        if encoded_faces is not None and _decode(encoded_faces, use_b64) != _decode(
            submesh['FacesData'], use_b64
        ):
            raise ValueError(
                f'sub{submesh_index}: position-only edit changed face indices/order')
        edits.append((submesh_index, submesh, edited))
    return edits


# ---------------------------------------------------------------------------
# CustomSubmeshes validation (PLAN_AddSubmesh.md Phase 1 step 3)
# ---------------------------------------------------------------------------
# Cross-checks a CustomSubmeshes entry against the rest of the .sluggie
# contract before any DAT/DOL write. This runs ahead of ParseSluggie/
# BuildGPLMeshData, which don't otherwise touch CustomSubmeshes at all
# (Phase 1 step 2 is a pure parse); GPL/ACT/TEX assembly is Phase 2-4.

_CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES = frozenset({'RhSp', 'LhSp', 'SpRf', 'GhSp'})
_CUSTOM_SUBMESH_REJECTED_DERIVED_TYPE6 = '00000375'  # F9: never occurs on a rigid submesh

# Canonical rigid attribute formats (F6/F9), used for entries that don't come
# from a same-model `rigid:` template. Byte strides of the int16/uint8
# formats below; a `rigid:` source may in principle carry the donor
# template's own (near-universally identical) format, but the MVP encodes
# every CustomSubmeshes buffer in these canonical formats regardless of
# source kind, so structural validation can check them uniformly here.
# CompCount 3 x int16. Every accepted VertexBufferQuantizeInfo is an s16
# format (see _CUSTOM_SUBMESH_POSITION_QUANTIZE_RANGE), so only the fixed-point
# shift varies between custom submeshes -- the stride never does.
_CUSTOM_SUBMESH_POSITION_STRIDE = 6
_CUSTOM_SUBMESH_NORMAL_STRIDE   = 6   # CompCount 3, QuantizeInfo 62 (3 x int16)
_CUSTOM_SUBMESH_UV_STRIDE       = 4   # CompCount 2, QuantizeInfo 62 (2 x int16)
_CUSTOM_SUBMESH_COLOR_STRIDE    = 4   # CompCount 4, QuantizeInfo 48 (RGBA8)

# Byte-exact captures of complete vanilla rigid draw lists, one per shader
# mode (PLAN_EditRigidMeshes.md decision 9). Every capture source below holds
# exactly these records and nothing else, in canonical order
# (T1 L0, [T1 L1], T4, T3, T6, T7), so `States` is the source submesh's whole
# DisplayStates list and `IdenticalRigidLists` counts the vanilla rigid
# submeshes whose whole list is byte-identical to it (surveyed over all 1,472
# player-folder exports).
#
# Only `States`, `Layers` and `Sha256` drive the build. The T1 texture indices
# and the T3 setting are provenance only: _custom_submesh_builtin_records
# rebinds every layer to the host model's own textures and the caller
# regenerates Type 3 from the host submesh's attribute set. `Layers` is the
# number of T1 records, which is what Type 4 follows -- so the 1-layer `Shdw`
# form keeps `T4 fffffff0` even on a 2-UV-channel host.
#
# `VerifiedInGame` gates a template: _validate_template_source refuses an
# unverified one and the Add submesh / Add material dialogs hide it
# (TemplateSources.BUILTIN_TEMPLATE_NAMES), until PLAN_EditRigidMeshes.md
# Phase 0 probe 7 confirms it in Dolphin. The only copy:
# build_template_source_fixture.BUILTIN_TEMPLATES reads this whole registry,
# including provenance.
_CUSTOM_SUBMESH_BUILTIN_TEMPLATES = {
    'rigid_spec_v1': {
        'States': (
            (1, '000008', '11110000'),
            (1, '000000', '11002003'),
            (4, '000000', 'ffffff10'),
            (3, '000000', '000028a8'),
            (6, '010000', '00000374'),
            (7, '640064', 'Spec'),
        ),
        'Sha256': 'ee88bc44a3fb2a5864203b941519199ec61f1097eafef479eccf87de666dd862',
        'Layers': 2,
        'ShaderMode': 'Spec',
        'VerifiedInGame': True,  # PLAN_AddSubmesh.md Phase 0 probe 5 (U4)
        'Provenance': {
            'Model': '33 Toadette/135708512_kinopico.gpl',
            'MeshName': 'pony_l3',
            'SurfaceId': 'sm1_ds5',
            'IdenticalRigidLists': 17,
        },
    },
    # `Shdw` never occurs on a vanilla character, so this capture comes from a
    # map object. Of the 672 rigid surfaces drawn with an effective Type-7
    # `Shdw`, 516 bind one texture layer; `T4 fffffff0` (1 layer),
    # `T6 00000374` and T7 pad `000000` are each the most common value, and
    # this list's Type-1 word matches rigid_spec_v1's in every documented
    # field (layer 0, wraps 1, wrapt 1) and in its undocumented top byte, so
    # the capture differs from the verified `Spec` built-in only in the three
    # fields the shader mode is meant to change.
    'rigid_shdw_v1': {
        'States': (
            (1, '000008', '11110002'),
            (4, '000000', 'fffffff0'),
            (3, '000000', '00000828'),
            (6, '010000', '00000374'),
            (7, '000000', 'Shdw'),
        ),
        'Sha256': '324907992ca4c44273724ddc0b2bc9983c9972cc69bcc5a57fdbb876b452df2b',
        'Layers': 1,
        'ShaderMode': 'Shdw',
        'VerifiedInGame': False,  # awaits PLAN_EditRigidMeshes.md Phase 0 probe 7
        'Provenance': {
            'Model': '137 Various A/653486528_manhole01.gpl',
            'MeshName': 'manhole',
            'SurfaceId': 'sm0_ds4',
            'IdenticalRigidLists': 9,
        },
    },
    # `GhSp` occurs on exactly four vanilla rigid surfaces: Birdo's ring and
    # diamond in the high- and low-poly models, all four byte-identical.
    'rigid_ghsp_v1': {
        'States': (
            (1, '000708', '11110003'),
            (1, '000000', '11002004'),
            (4, '000000', 'ffffff10'),
            (3, '000000', '000028a8'),
            (6, '010000', '00000174'),
            (7, '640064', 'GhSp'),
        ),
        'Sha256': '6a5e44b42125c7033de42f0a5b8a2ad9a744d52fb4a496dce4793640f57a1560',
        'Layers': 2,
        'ShaderMode': 'GhSp',
        'VerifiedInGame': False,  # awaits PLAN_EditRigidMeshes.md Phase 0 probe 7
        'Provenance': {
            'Model': '35 Birdo/142642016_catherine.gpl',
            'MeshName': 'gold_ring',
            'SurfaceId': 'sm1_ds5',
            'IdenticalRigidLists': 4,
        },
    },
    # The hand visibility roles occur only on Mii hands (48 rigid surfaces
    # each). The male and female Mii lists differ solely in the T1 L0 pad
    # byte, which is why only 24 of the 48 are byte-identical to this capture.
    'rigid_rhsp_v1': {
        'States': (
            (1, '000308', '11110000'),
            (1, '000000', '11002003'),
            (4, '000000', 'ffffff10'),
            (3, '000000', '000028a8'),
            (6, '010000', '00000174'),
            (7, '96003c', 'RhSp'),
        ),
        'Sha256': 'e1ac62994ad67caefdad4b9ba68099da4b17aee417142df59cc5c27e063fbcd7',
        'Layers': 2,
        'ShaderMode': 'RhSp',
        'VerifiedInGame': False,  # awaits PLAN_EditRigidMeshes.md Phase 0 probe 7
        'Provenance': {
            'Model': '100 Blue Male Mii/333008640_mii_male.gplp',
            'MeshName': 'r_hand',
            'SurfaceId': 'sm7_ds5',
            'IdenticalRigidLists': 24,
        },
    },
    'rigid_lhsp_v1': {
        'States': (
            (1, '000408', '11110000'),
            (1, '000000', '11002003'),
            (4, '000000', 'ffffff10'),
            (3, '000000', '000028a8'),
            (6, '010000', '00000174'),
            (7, '96003c', 'LhSp'),
        ),
        'Sha256': 'c246a603b25923b7978231f0d5d63dfa5eb399d239527a3798ee968e61221ade',
        'Layers': 2,
        'ShaderMode': 'LhSp',
        'VerifiedInGame': False,  # awaits PLAN_EditRigidMeshes.md Phase 0 probe 7
        'Provenance': {
            'Model': '100 Blue Male Mii/333008640_mii_male.gplp',
            'MeshName': 'l_hand',
            'SurfaceId': 'sm4_ds5',
            'IdenticalRigidLists': 24,
        },
    },
}


def builtin_template_names(verified_only: bool = True) -> tuple[str, ...]:
    """Registry template names in registration order.

    ``verified_only`` keeps the ones PLAN_EditRigidMeshes.md Phase 0 probe 7
    has confirmed in game, which is what the dialogs offer and what
    ``_validate_template_source`` accepts. ``TemplateSources`` mirrors the
    result for the Blender side, which cannot import this module.
    """
    return tuple(
        name for name, template in _CUSTOM_SUBMESH_BUILTIN_TEMPLATES.items()
        if template.get('VerifiedInGame') or not verified_only
    )


def _custom_submesh_setting_bytes(shader_mode: str) -> bytes:
    if len(shader_mode) == 8 and all(c in '0123456789abcdefABCDEF' for c in shader_mode):
        return bytes.fromhex(shader_mode)
    return shader_mode.encode('ascii', errors='replace').ljust(4, b'\x00')[:4]


def _custom_submesh_state_records_sha256(records) -> str:
    """Hash display-state records as their 16-byte headers without pointers.

    Mirrors build_template_source_fixture.state_records_sha256 exactly (same
    algorithm and byte layout)."""
    import hashlib
    payload = b''.join(
        bytes([state_id]) + bytes.fromhex(pad) + _custom_submesh_setting_bytes(mode)
        for state_id, pad, mode in records
    )
    return hashlib.sha256(payload).hexdigest()


def _bone_geo_id_raw(bone: dict) -> int:
    """Literal ACT GeoId sentinel (0xFFFF = mesh-free), independent of the
    BoneHierarchy[].GeoId field, which normalises skinned bones to 0 (F7)."""
    if bone.get('GeoIdRaw') is not None:
        return int(bone['GeoIdRaw'])
    if bone.get('Skinned'):
        return 0xFFFF
    return int(bone.get('GeoId', 0xFFFF))


def _custom_submesh_effective_state(states: list, upto_index: int, display_state_id: int):
    """Last state of *display_state_id* at or before *upto_index* in *states*
    (the cumulative-state walk used throughout PLAN_AddSubmesh.md/
    PLAN_ModelReplacements.md to resolve GX state carried into a draw)."""
    effective = None
    for state in states[:upto_index + 1]:
        if int(state.get('DisplayStateId', -1)) == display_state_id:
            effective = state
    return effective


def _validate_custom_submesh_indexed_array(
    fail, label: str, use_b64: bool, data_field, faces_field, stride: int,
) -> None:
    """Structurally validate one (data, face-index) buffer pair.

    Confirms the data buffer decodes to a whole number of *stride*-byte
    entries within the uint16 index range, and that every face-index value
    addresses an existing entry -- the "index/count limits are within
    uint16" and "quantized coordinates are within the representable range"
    checks from PLAN_AddSubmesh.md Phase 1 step 3. Values already decoded
    from a properly strided buffer are always in-range for their own field
    width; a misaligned/truncated buffer is the actual failure mode this
    guards against.
    """
    if data_field is None:
        return
    data_bytes = _decode(data_field, use_b64)
    if not data_bytes or len(data_bytes) % stride:
        fail(f'{label} length {len(data_bytes)} is not a whole number of {stride}-byte entries')
        return
    entry_count = len(data_bytes) // stride
    if entry_count > 0xFFFF:
        fail(f'{label} entry count {entry_count} exceeds the uint16 index range')
    if faces_field is None:
        return
    faces_bytes = _decode(faces_field, use_b64)
    if not faces_bytes or len(faces_bytes) % 6:
        fail(f'{label} face-index buffer length {len(faces_bytes)} is not a whole number of uint16 triplets')
        return
    import struct as _struct
    for index in _struct.unpack(f'>{len(faces_bytes) // 2}H', faces_bytes):
        if index >= entry_count:
            fail(f'{label} face index {index} is out of range for {entry_count} entries')
            return


def _custom_submesh_rigid_surfaces(model: dict) -> dict:
    """Map every rigid (CompCount 3) donor submesh's SurfaceId to
    ``(that submesh's DisplayStates list, the surface's index within it)``,
    first-seen-wins. Shared by ``_validate_custom_submeshes`` (Phase 1 step 3)
    and ``PatchGPLAppendSubmesh`` (Phase 2)."""
    rigid_surfaces: dict[str, tuple[list, int]] = {}
    for sub in model.get('Submeshes') or []:
        if int((sub.get('VertexBuffer') or {}).get('VertexBufferCompCount', 0)) != 3:
            continue
        states = sub.get('DisplayStates') or []
        for index, state in enumerate(states):
            surface_id = state.get('SurfaceId')
            if surface_id and surface_id not in rigid_surfaces:
                rigid_surfaces[surface_id] = (states, index)
    return rigid_surfaces


def _validate_custom_submeshes(model: dict) -> None:
    """PLAN_AddSubmesh.md Phase 1 step 3: reject an invalid CustomSubmeshes
    entry before any DAT/DOL write. Runs ahead of ParseSluggie so a bad
    entry never reaches GPL/ACT/TEX assembly. Each error names the
    offending CustomSubmeshId so a failed patch is actionable."""
    custom_submeshes = model.get('CustomSubmeshes') or []
    if not custom_submeshes:
        return

    errors: list[str] = []
    if not model.get('UseHammerspace'):
        raise ValueError(
            'CustomSubmeshes require Hammerspace Mode (UseHammerspace) to be enabled'
        )

    use_b64 = model.get('UseBase64', True)
    # PLAN_AddBones.md's user contract: a custom submesh may host on a
    # user-added bone, which has no BoneHierarchy entry at all (it doesn't
    # exist in the donor ACT) -- BoneHierarchyEdited, when present, already
    # carries every donor bone forward plus any new ones, so it is the
    # complete bone set to validate a HostBoneId against.
    bones_by_id = {
        int(b['BoneId']): b
        for b in model.get('BoneHierarchyEdited') or model.get('BoneHierarchy') or []
    }
    donor_submeshes = model.get('Submeshes') or []
    submesh0 = donor_submeshes[0] if donor_submeshes else None

    rigid_surfaces = _custom_submesh_rigid_surfaces(model)

    claimed_bones: dict[int, str] = {}
    for cs in custom_submeshes:
        cs_id = cs.get('CustomSubmeshId', '<missing CustomSubmeshId>')

        def fail(message: str, _cs_id=cs_id) -> None:
            errors.append(f"custom submesh '{_cs_id}': {message}")

        host_bone_id = cs.get('HostBoneId')
        if not isinstance(host_bone_id, int) or isinstance(host_bone_id, bool) or not (0 <= host_bone_id <= 0xFFFF):
            fail(f'HostBoneId {host_bone_id!r} must be a uint16 bone index')
        else:
            bone = bones_by_id.get(host_bone_id)
            if bone is None:
                fail(f'host bone {host_bone_id} does not exist in BoneHierarchy')
            else:
                raw = _bone_geo_id_raw(bone)
                if raw != 0xFFFF:
                    fail(
                        f'host bone {host_bone_id} already owns submesh {raw}; '
                        'GeoId is a single uint16 per bone'
                    )
                elif host_bone_id in claimed_bones:
                    fail(
                        f'host bone {host_bone_id} is also claimed by custom submesh '
                        f"'{claimed_bones[host_bone_id]}'"
                    )
                else:
                    claimed_bones[host_bone_id] = cs_id

        template_source = cs.get('TemplateSource', '')
        kind, sep, argument = str(template_source).partition(':')
        if not sep or kind not in ('rigid', 'derived', 'builtin') or not argument:
            fail(
                f'TemplateSource {template_source!r} must be rigid:<SurfaceId>, '
                'derived:<SurfaceId> or builtin:<name>'
            )
            kind = None

        if kind == 'rigid':
            found = rigid_surfaces.get(argument)
            if found is None:
                fail(f"rigid: surface {argument!r} does not exist on a rigid submesh")
            else:
                states, index = found
                lighting = _custom_submesh_effective_state(states, index, 7)
                mode = lighting.get('ShaderMode') if lighting else None
                if mode in _CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES:
                    fail(
                        f"rigid: surface {argument!r} has hand/visibility-role "
                        f'Type-7 {mode!r}; only plain lit surfaces are allowed'
                    )
        elif kind == 'derived':
            if submesh0 is None or int((submesh0.get('VertexBuffer') or {}).get('VertexBufferCompCount', 0)) != 6:
                fail('derived: sources require a donor skinned submesh 0')
            else:
                states = submesh0.get('DisplayStates') or []
                matches = [i for i, s in enumerate(states) if s.get('SurfaceId') == argument]
                if not matches:
                    fail(f"derived: surface {argument!r} does not exist on submesh 0")
                elif int(states[matches[0]].get('PrimListLength') or 0) <= 0:
                    fail(f"derived: surface {argument!r} draws no primitives")
                else:
                    index = matches[0]
                    type7 = _custom_submesh_effective_state(states, index, 7)
                    type7_mode = type7.get('ShaderMode') if type7 else None
                    if type7_mode != 'Spec':
                        fail(
                            f"derived: surface {argument!r} has effective Type-7 "
                            f'{type7_mode!r}; only plain Spec surfaces are allowed'
                        )
                    type6 = _custom_submesh_effective_state(states, index, 6)
                    type6_mode = type6.get('ShaderMode') if type6 else None
                    if type6_mode == _CUSTOM_SUBMESH_REJECTED_DERIVED_TYPE6:
                        fail(
                            f"derived: surface {argument!r} uses Type-6 "
                            f'{_CUSTOM_SUBMESH_REJECTED_DERIVED_TYPE6}, which never '
                            'occurs on rigid submeshes'
                        )
        elif kind == 'builtin':
            template = _CUSTOM_SUBMESH_BUILTIN_TEMPLATES.get(argument)
            if template is None:
                fail(
                    f'builtin: unknown template {argument!r}; known: '
                    f'{sorted(_CUSTOM_SUBMESH_BUILTIN_TEMPLATES)}'
                )
            elif _custom_submesh_state_records_sha256(template['States']) != template['Sha256']:
                fail(f"builtin: stored bytes of {argument!r} do not match their recorded hash")
            elif not template.get('VerifiedInGame'):
                fail(
                    f'builtin: template {argument!r} is not verified in game yet '
                    '(PLAN_EditRigidMeshes.md Phase 0 probe 7); verified: '
                    f'{sorted(builtin_template_names(verified_only=True))}'
                )
            else:
                # Type 3 comes from the channels this submesh exported while
                # Type 4 follows the T1 records the template emits, so the two
                # have to agree. The emitted count is the template's own layer
                # count capped by what host submesh 0 binds -- a 2-layer
                # built-in legitimately degrades to the 1-layer form on a host
                # with no specular binding.
                bound_layers = _custom_submesh_builtin_layer_count(model, argument)
                uv_channel_count = len(cs.get('UVChannels') or [])
                if bound_layers != uv_channel_count:
                    fail(
                        f'builtin: template {argument!r} binds {bound_layers} texture '
                        f'layer(s) on this model but the custom submesh has '
                        f'{uv_channel_count} UV channel(s)'
                    )

        if kind in ('derived', 'builtin'):
            uv_channel_count = len(cs.get('UVChannels') or [])
            if uv_channel_count not in (1, 2):
                fail(f'{kind}: UV channel count must be 1 or 2, got {uv_channel_count}')

        faces_count = cs.get('FacesCount')
        if not isinstance(faces_count, int) or isinstance(faces_count, bool) or not (0 <= faces_count <= 0xFFFF):
            fail(f'FacesCount {faces_count!r} must be a uint16 value')
            faces_count = None

        position_quantize = cs.get(
            'VertexBufferQuantizeInfo', _CUSTOM_SUBMESH_POSITION_FORMAT[1]
        )
        if (
            not isinstance(position_quantize, int) or isinstance(position_quantize, bool)
            or position_quantize not in _CUSTOM_SUBMESH_POSITION_QUANTIZE_RANGE
        ):
            fail(
                f'VertexBufferQuantizeInfo {position_quantize!r} must be an s16 position '
                f'format between {_CUSTOM_SUBMESH_POSITION_QUANTIZE_RANGE[0]} and '
                f'{_CUSTOM_SUBMESH_POSITION_QUANTIZE_RANGE[-1]} (high nibble 3); custom '
                'submeshes never use float positions'
            )

        vertex_bytes = _decode(cs['VertexBufferData'], use_b64) if cs.get('VertexBufferData') else b''
        vertex_count = None
        if not vertex_bytes or len(vertex_bytes) % _CUSTOM_SUBMESH_POSITION_STRIDE:
            fail(f'VertexBufferData length {len(vertex_bytes)} is not a whole number of rigid position entries')
        else:
            vertex_count = len(vertex_bytes) // _CUSTOM_SUBMESH_POSITION_STRIDE
            if vertex_count > 0xFFFF:
                fail(f'vertex count {vertex_count} exceeds the uint16 index range')

        if faces_count is not None:
            faces_bytes = _decode(cs['FacesData'], use_b64) if cs.get('FacesData') is not None else b''
            if len(faces_bytes) != faces_count * 6:
                fail(
                    f'FacesData length {len(faces_bytes)} does not match FacesCount '
                    f'{faces_count} (expected {faces_count * 6} bytes)'
                )
            elif vertex_count is not None and faces_bytes:
                import struct as _struct
                for vertex_index in _struct.unpack(f'>{len(faces_bytes) // 2}H', faces_bytes):
                    if vertex_index >= vertex_count:
                        fail(f'face index {vertex_index} is out of range for {vertex_count} vertices')
                        break

        if cs.get('NormalBufferData') is not None:
            _validate_custom_submesh_indexed_array(
                fail, 'NormalBufferData', use_b64,
                cs.get('NormalBufferData'), cs.get('NormalFacesData'),
                _CUSTOM_SUBMESH_NORMAL_STRIDE,
            )
        if cs.get('ColorChannelData') is not None:
            _validate_custom_submesh_indexed_array(
                fail, 'ColorChannelData', use_b64,
                cs.get('ColorChannelData'), cs.get('ColorFacesData'),
                _CUSTOM_SUBMESH_COLOR_STRIDE,
            )
        for uv in cs.get('UVChannels') or []:
            _validate_custom_submesh_indexed_array(
                fail, f"UVChannels[{uv.get('UVChannelIndex')}]", use_b64,
                uv.get('UVChannelData'), uv.get('UVFacesData'),
                _CUSTOM_SUBMESH_UV_STRIDE,
            )

        texture_assignment = cs.get('TextureAssignment') or {}
        donor_index = texture_assignment.get('DonorTextureIndex')
        additional_name = texture_assignment.get('AdditionalTextureFileName')
        if (donor_index is None) == (additional_name is None):
            fail(
                'TextureAssignment must set exactly one of DonorTextureIndex or '
                'AdditionalTextureFileName'
            )
        elif donor_index is not None and not (isinstance(donor_index, int) and 0 <= donor_index <= 0xFFFF):
            fail(f'TextureAssignment.DonorTextureIndex {donor_index!r} must be a uint16 texture index')

    if errors:
        raise ValueError('; '.join(errors))


def _validate_bone_hierarchy_edited(model: dict) -> None:
    """PLAN_AddBones.md Phase 3: reject an invalid ``BoneHierarchyEdited``
    before any DAT/DOL write, alongside ``_validate_custom_submeshes``. Each
    error names the offending bone(s) so a failed patch is actionable.

    Operates purely on the ``.sluggie`` JSON (``BoneHierarchy``/
    ``BoneHierarchyEdited``/``CustomSubmeshes``/``SkinData``/
    ``SkinDataEdited``), not the parsed ACT bytes -- this is the data-level
    gate the plan wants ahead of ``_rebuild_act_bone_hierarchy``, which is
    itself only reachable once this passes. Sibling-chain order (the last
    F10 topology case, "sibling chain reordered") has no representation in
    the ``.sluggie`` schema at all (only ``ParentBoneId`` per bone), so it
    cannot be checked at this layer; it is enforced by construction instead
    -- the rebuilder only ever appends a donor bone's own ``prev``/``next``
    unchanged and links a new leaf onto the tail of its parent's chain.
    """
    bone_hierarchy_edited = model.get('BoneHierarchyEdited')
    if not bone_hierarchy_edited:
        return

    errors: list[str] = []

    def fail(message: str) -> None:
        errors.append(message)

    # Rule 1: Hammerspace flag set.
    if not model.get('UseHammerspace'):
        raise ValueError(
            'BoneHierarchyEdited requires Hammerspace Mode (UseHammerspace) to be enabled'
        )

    donor_bones = model.get('BoneHierarchy') or []
    donor_by_id = {int(b['BoneId']): b for b in donor_bones}
    donor_count = len(donor_bones)

    edited_ids = [int(b['BoneId']) for b in bone_hierarchy_edited]
    if len(set(edited_ids)) != len(edited_ids):
        fail('BoneHierarchyEdited has duplicate BoneId values')
    edited_by_id = {int(b['BoneId']): b for b in bone_hierarchy_edited}

    new_entries = [b for b in bone_hierarchy_edited if b.get('UserAdded')]
    new_ids = sorted(int(b['BoneId']) for b in new_entries)

    # Rule 5: new bone ids are exactly N, N+1, ... contiguous from the donor count.
    expected_new_ids = list(range(donor_count, donor_count + len(new_ids)))
    if new_ids != expected_new_ids:
        fail(
            f'new bone ids must be contiguous starting at the donor bone count '
            f'{donor_count} (got {new_ids}, expected {expected_new_ids})'
        )

    # Rule 6: total bone count <= 255 (F3: mirror ids are u8).
    total_bone_count = donor_count + len(new_ids)
    if total_bone_count > 255:
        fail(f'total bone count {total_bone_count} exceeds the 255-bone cap (PLAN_AddBones.md F3, Phase 3 rule 6)')

    # CustomSubmeshes may legitimately move a donor bone's GeoId away from
    # 0xFFFF (PLAN_AddSubmesh.md); that is not a Rule 2 violation.
    claimed_donor_bones = {
        int(cs['HostBoneId'])
        for cs in model.get('CustomSubmeshes') or []
        if isinstance(cs.get('HostBoneId'), int) and int(cs['HostBoneId']) < donor_count
    }

    # Rule 2: BoneHierarchyEdited is append-only against BoneHierarchy.
    for bone_id, donor_bone in donor_by_id.items():
        edited_bone = edited_by_id.get(bone_id)
        if edited_bone is None:
            fail(f'donor bone {bone_id} is missing from BoneHierarchyEdited (donor bones cannot be deleted)')
            continue
        if edited_bone.get('UserAdded'):
            fail(f'donor bone {bone_id} is marked UserAdded in BoneHierarchyEdited')
            continue

        donor_geo_raw = _bone_geo_id_raw(donor_bone)
        edited_geo_raw = _bone_geo_id_raw(edited_bone)
        if donor_geo_raw != edited_geo_raw:
            retargeted = donor_bone.get('GeoIdEdited') is not None and int(donor_bone['GeoIdEdited']) == edited_geo_raw
            if not retargeted and bone_id not in claimed_donor_bones:
                fail(
                    f'donor bone {bone_id} GeoId changed from {donor_geo_raw} to {edited_geo_raw} '
                    'without a CustomSubmeshes claim or GeoIdEdited retarget'
                )

        donor_mirror_id = donor_bone.get('MirrorBoneId')
        edited_mirror_id = edited_bone.get('MirrorBoneId')
        if donor_mirror_id is not None and donor_mirror_id != edited_mirror_id:
            fail(
                f'donor bone {bone_id} mirror pair changed from {donor_mirror_id} to {edited_mirror_id}'
            )
        donor_mirror_role = donor_bone.get('MirrorRole')
        edited_mirror_role = edited_bone.get('MirrorRole')
        if donor_mirror_role is not None and donor_mirror_role != edited_mirror_role:
            fail(
                f'donor bone {bone_id} mirror role changed from {donor_mirror_role} to {edited_mirror_role}'
            )

        # Rule 3: donor topology is byte-identical except for new leaves.
        donor_parent = donor_bone.get('ParentBoneId')
        edited_parent = edited_bone.get('ParentBoneId')
        donor_parent_id = None if donor_parent is None else int(donor_parent)
        edited_parent_id = None if edited_parent is None else int(edited_parent)
        if donor_parent_id != edited_parent_id:
            if edited_parent_id is not None and edited_parent_id in {int(b['BoneId']) for b in new_entries}:
                fail(
                    f'new bone {edited_parent_id} was inserted between donor bones '
                    f'{donor_parent_id} and {bone_id}; new bones can only hang off the '
                    'tree as leaves (PLAN_AddBones.md F10)'
                )
            elif (
                edited_parent_id is not None
                and edited_parent_id in donor_by_id
                and donor_by_id[edited_parent_id].get('ParentBoneId') is not None
                and int(donor_by_id[edited_parent_id]['ParentBoneId']) == bone_id
            ):
                fail(f'donor bones {bone_id} and {edited_parent_id} swapped their parent/child relation')
            else:
                fail(f'donor bone {bone_id} was reparented from {donor_parent_id} to {edited_parent_id}')

    # Rule 4: every new bone is a leaf of the donor tree -- no donor bone may
    # name a new bone as its parent (independent of how rule 3's diff reads
    # the exporter's ParentBoneId, so a donor-onto-new parenting is caught
    # even if the rule-3 diff above is somehow bypassed).
    new_id_set = {int(b['BoneId']) for b in new_entries}
    for bone in bone_hierarchy_edited:
        if bone.get('UserAdded'):
            continue
        parent_id = bone.get('ParentBoneId')
        if parent_id is not None and int(parent_id) in new_id_set:
            fail(
                f"donor bone {bone['BoneId']} names new bone {int(parent_id)} as its parent; "
                'new bones must be leaves (PLAN_AddBones.md F10/Phase 3 rule 4)'
            )

    # Rules 7-8: every new bone has an existing, non-root parent and the
    # user-contract defaults (no track, self-mirrored, role 3).
    for entry in new_entries:
        bone_id = int(entry['BoneId'])
        parent_id = entry.get('ParentBoneId')
        if parent_id is None:
            fail(f'new bone {bone_id} has no ParentBoneId; new bones may not be roots')
        elif int(parent_id) not in edited_by_id:
            fail(f"new bone {bone_id}'s parent {int(parent_id)} does not exist")

        track_id = entry.get('TrackId')
        if track_id is None or int(track_id) != 0xFFFF:
            fail(f'new bone {bone_id} has TrackId {track_id!r}, expected 0xFFFF (no track)')
        mirror_id = entry.get('MirrorBoneId')
        if mirror_id is None or int(mirror_id) != bone_id:
            fail(f'new bone {bone_id} has MirrorBoneId {mirror_id!r}, expected its own id {bone_id}')
        mirror_role = entry.get('MirrorRole')
        if mirror_role is None or int(mirror_role) != 3:
            fail(f'new bone {bone_id} has MirrorRole {mirror_role!r}, expected 3')

    # Rule 9: donor mirror table is a clean boneCount-long involution, or absent.
    mirror_entries = {
        bone_id: (donor_bone.get('MirrorBoneId'), donor_bone.get('MirrorRole'))
        for bone_id, donor_bone in donor_by_id.items()
    }
    present = {bid: pair for bid, pair in mirror_entries.items() if pair[0] is not None}
    if present and len(present) != donor_count:
        fail(
            f'donor mirror table is present on {len(present)} of {donor_count} bones; '
            'it must cover every bone or none (PLAN_AddBones.md F4/F6)'
        )
    else:
        for bone_id, (mirror_id, _role) in present.items():
            mirror_id = int(mirror_id)
            if mirror_id not in donor_by_id:
                fail(f'donor bone {bone_id} mirror id {mirror_id} does not exist')
                continue
            target = donor_by_id[mirror_id].get('MirrorBoneId')
            if target is None or int(target) != bone_id:
                fail(
                    f'donor mirror table is not an involution: bone {bone_id} -> {mirror_id} -> {target} '
                    '(PLAN_AddBones.md F6 -- refuse rather than guess)'
                )

    # Rule 10: no new bone drives skinning.
    for skin_key in ('SkinData', 'SkinDataEdited'):
        skin_data = model.get(skin_key)
        if not skin_data:
            continue
        for sk1 in skin_data.get('SK1s') or []:
            if int(sk1['BoneIndex']) >= donor_count:
                fail(f"{skin_key}: SK1 entry names bone {sk1['BoneIndex']}, which is not a donor bone")
        for sk2 in skin_data.get('SK2s') or []:
            for field in ('BoneIndex1', 'BoneIndex2'):
                if int(sk2[field]) >= donor_count:
                    fail(f"{skin_key}: SK2 entry names bone {sk2[field]}, which is not a donor bone")
        for skacc in skin_data.get('SKAccs') or []:
            if int(skacc['BoneIndex']) >= donor_count:
                fail(f"{skin_key}: SKAcc entry names bone {skacc['BoneIndex']}, which is not a donor bone")

    if errors:
        raise ValueError('; '.join(errors))


# ---------------------------------------------------------------------------
# CustomSubmeshes GPL append (PLAN_AddSubmesh.md Phase 2)
# ---------------------------------------------------------------------------
# Builds the display-state list and raw GPL blob for each parsed
# CustomSubmesh and appends them to an already-cloned/patched GPL section,
# following the same clone + append + repoint approach as PatchGPLUVRebuild.
# _validate_custom_submeshes has already rejected anything malformed before
# this runs, so the helpers below assume a valid, already-checked entry.

_CUSTOM_SUBMESH_TEXTURE_LAYER_SHIFT = 13
_CUSTOM_SUBMESH_TEXTURE_INDEX_MASK  = 0x1FFF
_CUSTOM_SUBMESH_TYPE4_BY_UV_COUNT   = {1: 'fffffff0', 2: 'ffffff10'}  # F9

_CUSTOM_SUBMESH_POSITION_FORMAT = (3, 59)  # CompCount, default QuantizeInfo (F6/F9)
# Accepted VertexBufferQuantizeInfo values, coarsest first: the s16 formats from
# 48 (0x30, shift 0, +/-32767) up to the default 59 (0x3B, shift 11, +/-16).
# The exporter's CustomSubmeshExport.POSITION_QUANTIZE_CANDIDATES is the same
# set, ordered finest first -- it picks the most precise one the mesh fits in,
# so a prop far from its host bone widens the range instead of being rejected.
_CUSTOM_SUBMESH_POSITION_QUANTIZE_RANGE = tuple(range(0x30, 0x3C))
_CUSTOM_SUBMESH_NORMAL_FORMAT   = (3, 62)
_CUSTOM_SUBMESH_UV_FORMAT       = (2, 62)
_CUSTOM_SUBMESH_COLOR_FORMAT    = (4, 48)


def _custom_submesh_texture_layer(mode: str) -> tuple[int, int]:
    """Decode a Type-1 ShaderMode hex string into (layer, texture_index)."""
    setting = int(mode, 16)
    return (
        (setting >> _CUSTOM_SUBMESH_TEXTURE_LAYER_SHIFT) & 7,
        setting & _CUSTOM_SUBMESH_TEXTURE_INDEX_MASK,
    )


def _custom_submesh_with_texture_index(mode: str, texture_index: int) -> str:
    """Return *mode* (a Type-1 ShaderMode hex string) with its texture index
    field replaced, keeping the layer and every other bit unchanged."""
    setting = int(mode, 16)
    rebound = (setting & ~_CUSTOM_SUBMESH_TEXTURE_INDEX_MASK) | (texture_index & _CUSTOM_SUBMESH_TEXTURE_INDEX_MASK)
    return f'{rebound:08x}'


def _custom_submesh_type3_descriptors(setting: int) -> list[dict]:
    """Decode a Type-3 setting word into the ordered attribute index layout
    (mirrors build_add_submesh_fixture._type3_descriptors)."""
    descriptors = []
    for key, shift in drawlist._ATTR_BIT_SHIFT.items():
        mode = (setting >> shift) & 0b11
        if mode == 0b00:
            continue
        if mode == 0b01:
            raise ValueError(
                f'Type-3 setting 0x{setting:08X} uses a direct {key} attribute, '
                'which CustomSubmeshes do not support'
            )
        descriptors.append({'key': key, 'index_size': 1 if mode == 0b10 else 2})
    return descriptors


def _custom_submesh_type3_setting(layout: list[dict]) -> int:
    """Encode an attribute index layout as a Type-3 setting (inverse of
    _custom_submesh_type3_descriptors)."""
    setting = 0
    for descriptor in layout:
        shift = drawlist._ATTR_BIT_SHIFT[descriptor['key']]
        setting |= (0b10 if descriptor['index_size'] == 1 else 0b11) << shift
    return setting


def _custom_submesh_index_size_for(count: int) -> int:
    return 1 if count <= 0x100 else 2


def _custom_submesh_canonical_layout(cs: 'CustomSubmesh') -> list[dict]:
    """The attribute index layout for a `derived:`/`builtin:` custom submesh:
    position, then lighting/color0 if present, then one texture{i} per UV
    channel -- index sizes sized to the submesh's own entry counts (F9)."""
    layout = [{
        'key': 'position',
        'index_size': _custom_submesh_index_size_for(
            len(cs.vertex_data) // _CUSTOM_SUBMESH_POSITION_STRIDE
        ),
    }]
    if cs.normal_data:
        layout.append({
            'key': 'lighting',
            'index_size': _custom_submesh_index_size_for(
                len(cs.normal_data) // _CUSTOM_SUBMESH_NORMAL_STRIDE
            ),
        })
    if cs.color_data:
        layout.append({
            'key': 'color0',
            'index_size': _custom_submesh_index_size_for(
                len(cs.color_data) // _CUSTOM_SUBMESH_COLOR_STRIDE
            ),
        })
    for uv in sorted(cs.uv_channels, key=lambda channel: channel.channel_index):
        layout.append({
            'key': f'texture{uv.channel_index}',
            'index_size': _custom_submesh_index_size_for(
                len(uv.uv_data) // _CUSTOM_SUBMESH_UV_STRIDE
            ),
        })
    return layout


def _custom_submesh_rigid_records(states: list, surface_index: int) -> list[list]:
    """`rigid:` template source: clone every DisplayStates record of the
    template submesh verbatim (state_id, pad, mode). Whichever record ends
    up drawing is decided by the caller; every other record's primitive list
    is dropped regardless of what it originally drew (F4/Phase 2 step 3)."""
    return [
        [
            int(state['DisplayStateId']),
            bytes.fromhex(state.get('DisplayStatePadBytes', '000000')),
            state.get('ShaderMode', ''),
        ]
        for state in states
    ]


def _custom_submesh_derived_records(submesh0_states: list, surface_id: str) -> list[list]:
    """`derived:` template source: build the canonical rigid record order
    from the effective states of a submesh-0 surface (ported from
    build_template_source_fixture.derive_rigid_state_records; validity is
    already enforced by _validate_custom_submeshes)."""
    surface_index = next(
        index for index, state in enumerate(submesh0_states)
        if state.get('SurfaceId') == surface_id
    )
    layers: dict[int, dict] = {}
    type6 = type7 = None
    for state in submesh0_states[:surface_index + 1]:
        state_id = int(state['DisplayStateId'])
        if state_id == 1:
            layers[_custom_submesh_texture_layer(state['ShaderMode'])[0]] = state
        elif state_id == 6:
            type6 = state
        elif state_id == 7:
            type7 = state

    def _rec(state: dict) -> list:
        return [
            int(state['DisplayStateId']),
            bytes.fromhex(state.get('DisplayStatePadBytes', '000000')),
            state['ShaderMode'],
        ]

    uv_count = 2 if 1 in layers else 1
    records = [_rec(layers[0])]
    if uv_count == 2:
        records.append(_rec(layers[1]))
    records += [
        [4, b'\x00\x00\x00', _CUSTOM_SUBMESH_TYPE4_BY_UV_COUNT[uv_count]],
        [3, b'\x00\x00\x00', '00000000'],  # placeholder; regenerated below
        _rec(type6),
        _rec(type7),
    ]
    return records


def _custom_submesh_builtin_records(model: dict, name: str) -> list[list]:
    """`builtin:` template source: bind the stored canonical rigid list to
    the host model's own submesh-0 textures (ported from
    build_template_source_fixture.builtin_rigid_state_records)."""
    template = _CUSTOM_SUBMESH_BUILTIN_TEMPLATES[name]
    submesh0 = (model.get('Submeshes') or [None])[0] or {}
    bindings: dict[int, int] = {}
    for state in submesh0.get('DisplayStates') or []:
        if int(state.get('DisplayStateId', -1)) == 1:
            layer, texture_index = _custom_submesh_texture_layer(state['ShaderMode'])
            bindings.setdefault(layer, texture_index)

    records = []
    for state_id, pad_hex, mode in template['States']:
        if state_id == 1:
            layer, _texture_index = _custom_submesh_texture_layer(mode)
            if layer not in bindings:
                continue
            mode = _custom_submesh_with_texture_index(mode, bindings[layer])
        records.append([state_id, bytes.fromhex(pad_hex), mode])

    # Type 4 follows the T1 records this template actually emits -- the
    # template's own layer count, capped by what the host binds. A 1-layer
    # built-in (`rigid_shdw_v1`) therefore keeps `T4 fffffff0` even when the
    # host submesh binds a layer-1 specular texture (decision 9).
    uv_count = sum(1 for record in records if record[0] == 1)
    for record in records:
        if record[0] == 4:
            record[2] = _CUSTOM_SUBMESH_TYPE4_BY_UV_COUNT[uv_count]  # placeholder; regenerated below
    return records


def _custom_submesh_builtin_layer_count(model: dict, name: str) -> int:
    """Texture layers built-in *name* actually binds on *model*.

    That is its captured layer count capped by host submesh 0's own bindings,
    which is exactly the number of T1 records _custom_submesh_builtin_records
    emits -- and therefore what Type 4 declares and what the custom submesh's
    UV channel count has to match.
    """
    submesh0 = (model.get('Submeshes') or [None])[0] or {}
    bindings = {
        _custom_submesh_texture_layer(state['ShaderMode'])[0]
        for state in submesh0.get('DisplayStates') or []
        if int(state.get('DisplayStateId', -1)) == 1
    }
    return sum(
        1 for state_id, _pad, mode in _CUSTOM_SUBMESH_BUILTIN_TEMPLATES[name]['States']
        if state_id == 1 and _custom_submesh_texture_layer(mode)[0] in bindings
    )


def _resolve_custom_submesh_records(
    model: dict, cs: 'CustomSubmesh', rigid_surfaces: dict,
) -> tuple[list[list], int, str]:
    """Return (records, drawing_index, kind) for one CustomSubmesh, per its
    TemplateSource kind (PLAN_AddSubmesh.md "Template sources"). ``records``
    entries are mutable ``[state_id, pad_bytes, shader_mode]`` lists; the
    caller still needs to resolve the active Type-3 layout and patch the
    layer-0 texture before turning them into DrawStates."""
    kind, _sep, argument = cs.template_source.partition(':')
    if kind == 'rigid':
        states, surface_index = rigid_surfaces[argument]
        return _custom_submesh_rigid_records(states, surface_index), surface_index, kind
    if kind == 'derived':
        submesh0 = (model.get('Submeshes') or [None])[0]
        records = _custom_submesh_derived_records(submesh0.get('DisplayStates') or [], argument)
        return records, len(records) - 1, kind
    if kind == 'builtin':
        records = _custom_submesh_builtin_records(model, argument)
        return records, len(records) - 1, kind
    raise ValueError(
        f"custom submesh '{cs.custom_submesh_id}': malformed TemplateSource "
        f'{cs.template_source!r}'
    )


def _custom_submesh_active_type3(records: list[list], upto_index: int) -> int | None:
    """Latest Type-3 setting at or before *upto_index* (cumulative-state walk,
    as in _custom_submesh_effective_state)."""
    for state_id, _pad, mode in reversed(records[:upto_index + 1]):
        if state_id == 3:
            return int(mode, 16)
    return None


def _custom_submesh_descriptors(
    cs: 'CustomSubmesh', records: list[list], drawing_index: int, kind: str,
) -> list[dict]:
    """Resolve the active Type-3 attribute layout for the drawing record.

    `rigid:` reuses the template's own Type-3 setting verbatim (F4 -- clone
    template bytes, change only what's understood); `derived:`/`builtin:`
    regenerate Type-3 for the custom submesh's own attribute set and index
    sizes (F9), overwriting the canonical placeholder record in place.
    """
    if kind == 'rigid':
        setting = _custom_submesh_active_type3(records, drawing_index)
        if setting is None:
            raise ValueError(
                f"custom submesh '{cs.custom_submesh_id}': rigid template has no "
                'active Type-3 attribute layout'
            )
        return _custom_submesh_type3_descriptors(setting)
    layout = _custom_submesh_canonical_layout(cs)
    setting = _custom_submesh_type3_setting(layout)
    for record in records:
        if record[0] == 3:
            record[2] = f'{setting:08x}'
            break
    return layout


def _custom_submesh_patch_layer0_texture(
    records: list[list], upto_index: int, texture_index: int,
) -> None:
    """Patch the effective layer-0 Type-1 texture index to *texture_index*
    (Phase 2 step 3: "Patch the effective Type-1 texture index for the new
    submesh only"). Layer 1 (specular, F9) is left as the template/host's own
    binding."""
    for record in reversed(records[:upto_index + 1]):
        state_id, _pad, mode = record
        if state_id == 1 and _custom_submesh_texture_layer(mode)[0] == 0:
            record[2] = _custom_submesh_with_texture_index(mode, texture_index)
            return
    raise ValueError('template has no layer-0 Type-1 texture binding to patch')


def _custom_submesh_faces(cs: 'CustomSubmesh', descriptors: list[dict]) -> list[list[dict]]:
    """Zip a CustomSubmesh's per-attribute face-index buffers into the
    [v0, v1, v2]-per-triangle structure drawlist.encodeDrawList expects,
    keyed by the active Type-3 descriptor layout."""
    import struct as _s

    uv_by_channel = {uv.channel_index: uv for uv in cs.uv_channels}

    def _indices(key: str) -> tuple[int, ...]:
        if key == 'position':
            raw = cs.faces_data
        elif key == 'lighting':
            raw = cs.normal_faces_data or b''
        elif key in ('color0', 'color1'):
            raw = cs.color_faces_data or b''
        elif key.startswith('texture'):
            channel = uv_by_channel.get(int(key[len('texture'):]))
            raw = channel.uv_faces_data if channel else b''
        else:
            raw = b''
        return _s.unpack(f'>{len(raw) // 2}H', raw) if raw else ()

    per_key_indices = {descriptor['key']: _indices(descriptor['key']) for descriptor in descriptors}

    faces = []
    for face_index in range(cs.faces_count):
        triangle = []
        for vertex_slot in range(3):
            flat_index = face_index * 3 + vertex_slot
            vertex = {
                key: (indices[flat_index] if flat_index < len(indices) else 0)
                for key, indices in per_key_indices.items()
            }
            triangle.append(vertex)
        faces.append(triangle)
    return faces


def _custom_submesh_to_submesh(
    cs: 'CustomSubmesh', submesh_index: int, records: list[list],
    drawing_index: int, primitive_bytes: bytes,
) -> 'Submesh':
    """Assemble a CustomSubmesh's already-resolved geometry and display
    states into a Submesh dataclass, ready for _build_rigid_submesh_blob.
    Fields the blob writer never reads (file-offset metadata that only
    matters for donor submeshes) are left at 0/empty."""
    uv_channels = [
        UVChannel(
            channel_index=uv.channel_index,
            palette_name='',
            texture_index=0,
            wrap_s=0,
            wrap_t=0,
            uv_data=uv.uv_data,
            uv_faces_data=uv.uv_faces_data,
            comp_count=_CUSTOM_SUBMESH_UV_FORMAT[0],
            quantize_info=_CUSTOM_SUBMESH_UV_FORMAT[1],
            uv_data_ptr_field_offset=0,
            uv_count_field_offset=0,
            source_data_offset=0,
        )
        for uv in sorted(cs.uv_channels, key=lambda channel: channel.channel_index)
    ]
    color_channels = []
    if cs.color_data:
        color_channels.append(ColorChannel(
            channel_index=0,
            color_data=cs.color_data,
            color_faces_data=cs.color_faces_data or b'',
            comp_count=_CUSTOM_SUBMESH_COLOR_FORMAT[0],
            quantize_info=_CUSTOM_SUBMESH_COLOR_FORMAT[1],
            source_data_offset=0,
        ))
    normal_buffer = None
    if cs.normal_data:
        normal_buffer = NormalBuffer(
            normal_data_ptr_field_offset=0,
            normal_count_field_offset=0,
            normal_buffer_offset=0,
            normal_buffer_length=len(cs.normal_data),
            comp_count=_CUSTOM_SUBMESH_NORMAL_FORMAT[0],
            quantize_info=_CUSTOM_SUBMESH_NORMAL_FORMAT[1],
            ambient_pct=0.0,
            normal_data=cs.normal_data,
            source_header_offset=0,
        )
    draw_states = [
        DrawState(
            display_state_id=state_id,
            display_state_pad_bytes=pad,
            prim_list_data=primitive_bytes if index == drawing_index else b'',
            active_descriptors=[],
            prim_list_ptr_field_offset=0,
            prim_list_size_field_offset=0,
            prim_list_absolute_offset=0,
            prim_list_length=len(primitive_bytes) if index == drawing_index else 0,
            shader_mode_field_offset=0,
            shader_mode=mode,
            source_state_offset=0,
        )
        for index, (state_id, pad, mode) in enumerate(records)
    ]
    return Submesh(
        submesh_index=submesh_index,
        mesh_name=cs.mesh_name,
        faces_count=cs.faces_count,
        faces_data=cs.faces_data,
        face_texture_indices=b'',
        vertex_data=cs.vertex_data,
        vertex_comp_count=_CUSTOM_SUBMESH_POSITION_FORMAT[0],
        vertex_quantize_info=cs.vertex_quantize_info,
        uv_channels=uv_channels,
        color_channels=color_channels,
        draw_states=draw_states,
        position_data_ptr_field_offset=0,
        vertex_count_field_offset=0,
        normal_buffer=normal_buffer,
        source_layout_offset=0,
        source_position_data_offset=0,
        preserve_source_layout=False,
    )


def _build_custom_submesh(
    model: dict, cs: 'CustomSubmesh', rigid_surfaces: dict, submesh_index: int,
    texture_index_by_file_name: dict[str, int] | None = None,
) -> 'Submesh':
    """Resolve one CustomSubmesh's template, geometry and texture binding
    into a Submesh dataclass ready for _build_rigid_submesh_blob.

    ``texture_index_by_file_name`` maps an ``AdditionalTextureFileName`` to
    its final TEX index (PLAN_AddSubmesh.md Phase 4 step 2: the caller builds
    the TEX plan first, then this patches the cloned Type-1 state with that
    final index -- there is no placeholder to re-patch later)."""
    records, drawing_index, kind = _resolve_custom_submesh_records(model, cs, rigid_surfaces)
    descriptors = _custom_submesh_descriptors(cs, records, drawing_index, kind)
    texture_index = cs.texture_assignment.donor_texture_index
    if texture_index is None:
        file_name = cs.texture_assignment.additional_texture_file_name
        mapping = texture_index_by_file_name or {}
        if file_name not in mapping:
            raise ValueError(
                f"custom submesh '{cs.custom_submesh_id}': no resolved TEX index "
                f"for AdditionalTextureFileName {file_name!r}; the TEX plan must "
                'be built before the GPL append (PLAN_AddSubmesh.md Phase 4 step 2)'
            )
        texture_index = mapping[file_name]
    _custom_submesh_patch_layer0_texture(records, drawing_index, texture_index)
    faces = _custom_submesh_faces(cs, descriptors)
    raw = drawlist.encodeDrawList(faces, descriptors) + b'\x00'
    primitive_bytes = raw + b'\x00' * ((-len(raw)) % 32)
    return _custom_submesh_to_submesh(cs, submesh_index, records, drawing_index, primitive_bytes)


def _build_rigid_submesh_blob(sub: 'Submesh') -> tuple[bytes, int]:
    """Serialize one brand-new rigid (CompCount 3) submesh as a self-contained
    GPL blob (DOLayout + headers + raw arrays + display states + primitive
    lists), ready to be appended to an existing GPL section.

    A restricted, single-submesh subset of BuildGPLMeshData's general
    (non-preserving) layout path: no skinning (custom submeshes never carry
    SK1/SK2/SKAcc, F1) and no donor-layout preservation (a new submesh has no
    donor bytes to preserve). Returns (blob_bytes, name_off), where name_off
    is relative to the start of blob_bytes -- which is also the DOLayout's
    own start, matching the GEO descriptor's DOLayoutPtr convention.
    """
    import struct as _s

    def _align4(data: bytes) -> bytes:
        r = len(data) % 4
        return data + b'\x00' * ((4 - r) % 4)

    def _align32(offset: int) -> int:
        return (offset + 31) & ~31

    def _vb_comp_size(quant_info: int) -> int:
        return 4 if (quant_info >> 4) in (4, 7, 0xa) else 2

    def _vertex_count(data: bytes, comp_count: int, quant_info: int) -> int:
        stride = _vb_comp_size(quant_info) * comp_count
        return len(data) // stride if stride else 0

    def _color_count(data: bytes, quant_info: int) -> int:
        fmt = quant_info >> 4
        stride = {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}.get(fmt, 2)
        return len(data) // stride

    M_uv = len(sub.uv_channels)
    n_ds = len(sub.draw_states)

    POS_OFF = 0x18
    COL_OFF = 0x20
    UV_OFF  = 0x28
    NOR_OFF = UV_OFF  + M_uv * 0x10
    DSP_OFF = NOR_OFF + 0x0c
    HDR_END = DSP_OFF + 0x0c

    cursor = HDR_END

    name_bytes = sub.mesh_name.encode('ascii', errors='replace') + b'\x00'
    name_off   = cursor
    cursor    += len(name_bytes)

    pal_name_offs       = []
    pal_name_bytes_list = []
    for uv in sub.uv_channels:
        pal_b = (uv.palette_name or '').encode('ascii', errors='replace') + b'\x00'
        pal_name_offs.append(cursor)
        pal_name_bytes_list.append(pal_b)
        cursor += len(pal_b)

    pos_data     = _align4(sub.vertex_data)
    pos_data_off = cursor
    cursor      += len(pos_data)

    col_data     = b''
    col_data_off = 0
    if sub.color_channels:
        col_data     = _align4(sub.color_channels[0].color_data)
        col_data_off = cursor
        cursor      += len(col_data)

    uv_data_offs = []
    uv_data_list = []
    for uv in sub.uv_channels:
        uv_b = _align4(uv.uv_data)
        uv_data_offs.append(cursor)
        uv_data_list.append(uv_b)
        cursor += len(uv_b)

    nor_data     = b''
    nor_data_off = 0
    if sub.normal_buffer and sub.normal_buffer.normal_data:
        nor_data     = _align4(sub.normal_buffer.normal_data)
        nor_data_off = cursor
        cursor      += len(nor_data)

    DS_OFF = cursor
    cursor += n_ds * 0x10

    pl_offs       = []
    pl_bytes_list = []
    for ds in sub.draw_states:
        if ds.prim_list_data:
            cursor = _align32(cursor)
            pl_b = ds.prim_list_data + b'\x00' * ((-len(ds.prim_list_data)) % 32)
            pl_offs.append(cursor)
            pl_bytes_list.append(pl_b)
            cursor += len(pl_b)
        else:
            pl_offs.append(0)
            pl_bytes_list.append(b'')

    blob_size = cursor

    pos_count = _vertex_count(sub.vertex_data, sub.vertex_comp_count, sub.vertex_quantize_info)
    col_count = 0
    if sub.color_channels:
        cc0 = sub.color_channels[0]
        col_count = _color_count(cc0.color_data, cc0.quantize_info)
    uv_counts = [
        _vertex_count(uv.uv_data, uv.comp_count, uv.quantize_info)
        for uv in sub.uv_channels
    ]
    nor_count = 0
    if sub.normal_buffer and sub.normal_buffer.normal_data:
        nb = sub.normal_buffer
        nor_count = _vertex_count(nb.normal_data, nb.comp_count, nb.quantize_info)

    has_lighting = bool(sub.normal_buffer and sub.normal_buffer.normal_data)

    blob = bytearray(blob_size)

    _s.pack_into('>I', blob, 0x00, POS_OFF)
    _s.pack_into('>I', blob, 0x04, COL_OFF)
    _s.pack_into('>I', blob, 0x08, UV_OFF)
    _s.pack_into('>I', blob, 0x0c, NOR_OFF if has_lighting else 0)
    _s.pack_into('>I', blob, 0x10, DSP_OFF)
    _s.pack_into('B',  blob, 0x14, M_uv)

    _s.pack_into('>I', blob, POS_OFF + 0x00, pos_data_off)
    _s.pack_into('>H', blob, POS_OFF + 0x04, pos_count)
    _s.pack_into('B',  blob, POS_OFF + 0x06, sub.vertex_quantize_info)
    _s.pack_into('B',  blob, POS_OFF + 0x07, sub.vertex_comp_count)

    if sub.color_channels:
        cc0 = sub.color_channels[0]
        _s.pack_into('>I', blob, COL_OFF + 0x00, col_data_off)
        _s.pack_into('>H', blob, COL_OFF + 0x04, col_count)
        _s.pack_into('B',  blob, COL_OFF + 0x06, cc0.quantize_info)
        _s.pack_into('B',  blob, COL_OFF + 0x07, cc0.comp_count)

    for j, uv in enumerate(sub.uv_channels):
        uv_off = UV_OFF + j * 0x10
        _s.pack_into('>I', blob, uv_off + 0x00, uv_data_offs[j])
        _s.pack_into('>H', blob, uv_off + 0x04, uv_counts[j])
        _s.pack_into('B',  blob, uv_off + 0x06, uv.quantize_info)
        _s.pack_into('B',  blob, uv_off + 0x07, uv.comp_count)
        _s.pack_into('>I', blob, uv_off + 0x08, pal_name_offs[j])
        _s.pack_into('>I', blob, uv_off + 0x0c, 0)

    if has_lighting:
        nb = sub.normal_buffer
        _s.pack_into('>I', blob, NOR_OFF + 0x00, nor_data_off)
        _s.pack_into('>H', blob, NOR_OFF + 0x04, nor_count)
        _s.pack_into('B',  blob, NOR_OFF + 0x06, nb.quantize_info)
        _s.pack_into('B',  blob, NOR_OFF + 0x07, nb.comp_count)
        _s.pack_into('>f', blob, NOR_OFF + 0x08, nb.ambient_pct)

    first_pl = next(
        (pl_offs[k] for k, ds in enumerate(sub.draw_states) if ds.prim_list_data),
        0,
    )
    _s.pack_into('>I', blob, DSP_OFF + 0x00, first_pl)
    _s.pack_into('>I', blob, DSP_OFF + 0x04, DS_OFF)
    _s.pack_into('>H', blob, DSP_OFF + 0x08, n_ds)

    for k, ds in enumerate(sub.draw_states):
        ds_off  = DS_OFF + k * 0x10
        setting = _s.unpack('>I', _custom_submesh_setting_bytes(ds.shader_mode))[0]
        _s.pack_into('B', blob, ds_off + 0x00, ds.display_state_id)
        pad = ds.display_state_pad_bytes
        blob[ds_off + 0x01 : ds_off + 0x04] = pad[:3] if len(pad) >= 3 else pad.ljust(3, b'\x00')
        _s.pack_into('>I', blob, ds_off + 0x04, setting)
        _s.pack_into('>I', blob, ds_off + 0x08, pl_offs[k])
        _s.pack_into('>I', blob, ds_off + 0x0c, len(pl_bytes_list[k]) if ds.prim_list_data else 0)

    def _put(rel_off: int, data: bytes) -> None:
        blob[rel_off : rel_off + len(data)] = data

    _put(name_off, name_bytes)
    for pal_off, pal_b in zip(pal_name_offs, pal_name_bytes_list):
        _put(pal_off, pal_b)
    _put(pos_data_off, pos_data)
    if col_data:
        _put(col_data_off, col_data)
    for uv_off, uv_b in zip(uv_data_offs, uv_data_list):
        _put(uv_off, uv_b)
    if nor_data:
        _put(nor_data_off, nor_data)
    for pl_off, pl_b in zip(pl_offs, pl_bytes_list):
        if pl_b:
            _put(pl_off, pl_b)

    return bytes(blob), name_off


def PatchGPLAppendSubmesh(
    gpl_bytes: bytes, model: dict, parsed: 'SluggieParsed',
    texture_index_by_file_name: dict[str, int] | None = None,
) -> bytes:
    """Append every parsed.custom_submeshes entry to a cloned/patched GPL
    section (PLAN_AddSubmesh.md Phase 2).

    ``texture_index_by_file_name`` resolves each custom submesh's
    ``AdditionalTextureFileName`` to its final TEX index; the caller builds
    the TEX plan before calling this (PLAN_AddSubmesh.md Phase 4 step 2).

    Unlike PatchGPLUVRebuild's pure tail-append, the GEO descriptor table
    must grow in place (Phase 0 only proved that shape -- the table
    immediately following the header -- works at runtime), so every existing
    blob is relocated as a single unit and each existing descriptor's
    DOLayoutPtr/namePtr (the only GPL-section-absolute pointers referencing
    it) is adjusted by the resulting shift. Nothing *inside* any existing
    blob changes: every pointer there is DOLayout-relative (self-relative to
    the blob), and SKN's cache-line write targets are offsets into submesh
    0's own position array (F1), not absolute GPL addresses, so relocating
    blobs as a unit doesn't affect SKN either (confirmed by Phase 0 probe 3's
    animated-skinning test). New blobs are inserted right after the existing
    ones and before GPLUserData, matching BuildGPLMeshData's own blob order.
    """
    import struct as _s

    if not parsed.custom_submeshes:
        return gpl_bytes

    def _align32(offset: int) -> int:
        return (offset + 31) & ~31

    def _align32_residue(offset: int, residue: int) -> int:
        """Round *offset* up to the next value congruent to *residue* mod 32."""
        return offset + ((residue - offset) & 31)

    rigid_surfaces = _custom_submesh_rigid_surfaces(model)
    donor_count = len(model.get('Submeshes') or [])
    new_submeshes = [
        _build_custom_submesh(
            model, cs, rigid_surfaces, donor_count + index, texture_index_by_file_name,
        )
        for index, cs in enumerate(parsed.custom_submeshes)
    ]

    patched = bytearray(gpl_bytes)
    magic, user_data_len, user_data_ptr, old_count, desc_ptr = _s.unpack_from('>5I', patched, 0x00)
    old_descriptors = [
        _s.unpack_from('>II', patched, desc_ptr + i * 8)
        for i in range(old_count)
    ]
    old_blob_region_start = (
        min(pointer for entry in old_descriptors for pointer in entry)
        if old_descriptors else _align32(desc_ptr + old_count * 8)
    )
    insertion_point = user_data_ptr if user_data_ptr else len(patched)
    if not (desc_ptr <= old_blob_region_start <= insertion_point <= len(patched)):
        raise ValueError('GPL section layout is not in the shape PatchGPLAppendSubmesh expects')

    pre_table            = bytes(patched[:desc_ptr])
    blobs_before_userdata = bytes(patched[old_blob_region_start:insertion_point])
    from_userdata_onward  = bytes(patched[insertion_point:])

    new_count = old_count + len(new_submeshes)

    out = bytearray()
    out += pre_table
    for dolayout_ptr, name_ptr in old_descriptors:
        # Relocated below once new_blob_region_start is known (out is still
        # the old table's length here); placeholder-free since old entries'
        # final shift only depends on where the (already-sized) table ends.
        out += _s.pack('>II', dolayout_ptr, name_ptr)
    out += b'\x00' * (len(new_submeshes) * 8)  # reserved for new descriptors
    # The blob region doesn't generally start on a 32-byte boundary itself
    # (vanilla data packs it directly after the descriptor table); what does
    # need to hold is that a skinned submesh 0's own *absolute* position-array
    # alignment survives the relocation. BuildGPLMeshData achieves that today
    # by choosing a blob-relative pos_data_off with a residue calibrated to
    # the blob's fixed GPL-relative base (_align32_residue). Shifting every
    # existing blob's base by a multiple of 32 preserves that residue
    # relationship untouched; any other shift would silently misalign it.
    padded_len = _align32_residue(len(out), old_blob_region_start % 32)
    out += b'\x00' * (padded_len - len(out))

    new_blob_region_start = len(out)
    blob_shift = new_blob_region_start - old_blob_region_start
    assert blob_shift % 32 == 0, 'blob relocation must preserve mod-32 residue'
    for i, (dolayout_ptr, name_ptr) in enumerate(old_descriptors):
        _s.pack_into('>II', out, desc_ptr + i * 8, dolayout_ptr + blob_shift, name_ptr + blob_shift)

    out += blobs_before_userdata

    new_descriptor_entries = []
    for sub in new_submeshes:
        out += b'\x00' * ((-len(out)) % 32)
        blob_gpl_off = len(out)
        blob_bytes, name_off = _build_rigid_submesh_blob(sub)
        out += blob_bytes
        new_descriptor_entries.append((blob_gpl_off, blob_gpl_off + name_off))

    out += b'\x00' * ((-len(out)) % 32)
    new_user_data_off = len(out) if user_data_ptr else 0
    out += from_userdata_onward

    new_table_slot_start = desc_ptr + old_count * 8
    for i, (dolayout_ptr, name_ptr) in enumerate(new_descriptor_entries):
        _s.pack_into('>II', out, new_table_slot_start + i * 8, dolayout_ptr, name_ptr)

    _s.pack_into('>I', out, 0x0c, new_count)
    if user_data_ptr:
        _s.pack_into('>I', out, 0x08, new_user_data_off)

    for cs, sub in zip(parsed.custom_submeshes, new_submeshes):
        _slogger.info(
            f"[GPL] appended custom submesh '{cs.custom_submesh_id}' as submesh "
            f'{sub.submesh_index} (host bone {cs.host_bone_id}, template '
            f'{cs.template_source})',
            source='hammerspace.main',
        )

    return bytes(out)


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
    position_edit_submeshes = set(model.get('_PositionEditSubmeshes', []))

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
        _surface_only_rebuild = sub.get('SurfaceAssignmentsRebuiltByImporter', False)
        _position_only_edited = i in position_edit_submeshes
        _uv_arrays_edited = sub.get('UVArraysEditedByImporter', False)
        _uv_primitive_lists_rebuilt = sub.get('UVPrimitiveListsRebuiltByImporter', False)
        _topology_arrays_edited = (
            _prim_lists_edited
            and not _surface_only_rebuild
            and not _uv_primitive_lists_rebuilt
        )
        _geometry_arrays_edited = _topology_arrays_edited or _position_only_edited

        if _geometry_arrays_edited:
            vertex_data = _decode(vb.get('VertexBufferDataEdited') or vb['VertexBufferData'], use_b64)
        else:
            vertex_data = _decode(vb['VertexBufferData'], use_b64)

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
                uv_data                  = _decode(_uv_src, use_b64),
                uv_faces_data            = _decode(uv.get('UVFacesDataEdited')   or uv['UVFacesData'],   use_b64),
                comp_count               = uv['UVChannelCompCount'],
                quantize_info            = uv['UVChannelQuantizeInfo'],
                uv_data_ptr_field_offset = _hex(uv.get('UVDataPtrFieldOffset', '0x0')),
                uv_count_field_offset    = _hex(uv.get('UVCountFieldOffset',    '0x0')),
                source_data_offset       = _hex(uv.get('UVChannelOffset', '0x0')),
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
                color_data       = _decode(_color_src, use_b64),
                color_faces_data = _decode(_color_faces_src, use_b64),
                comp_count       = cc['ColorChannelCompCount'],
                quantize_info    = cc['ColorChannelQuantizeInfo'],
                source_data_offset = _hex(cc.get('ColorChannelOffset', '0x0')),
            ))

        draw_states = []
        for ds in sub.get('DisplayStates', []):
            pad_hex = ds.get('DisplayStatePadBytes', '000000')
            prim_list_data = (
                ds.get('PrimListDataEdited')
                if 'PrimListDataEdited' in ds
                else ds.get('PrimListData')
            )
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
                shader_mode                 = ds.get('ShaderModeEdited') or ds.get('ShaderMode', ''),
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
                    (
                        raw_nb.get('NormalBufferDataEdited') or raw_nb['NormalBufferData']
                        if _topology_arrays_edited
                        or sub.get('NormalArraysEditedByImporter', False)
                        else raw_nb['NormalBufferData']
                    ),
                    use_b64,
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
            preserve_source_layout         = not (
                _geometry_arrays_edited or _uv_primitive_lists_rebuilt
            ),
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
    _topology_geometry_edited = any(
        'PrimListDataEdited' in ds
        for sub in model.get('Submeshes', [])
        for ds in sub.get('DisplayStates', [])
    )
    _position_geometry_edited = bool(position_edit_submeshes)
    _geometry_edited = _topology_geometry_edited or _position_geometry_edited
    raw_skn = raw_skn_orig or raw_skn_edit
    # Same-count reskin (PLAN_ModelReplacements.md 3.4): entry membership
    # itself changed (vertices reassigned between donor bones), so a donor
    # bone/pair may have gained or lost its SK1/SK2/SKAcc entry entirely.
    # The identity-substitution loops below assume the donor's entry COUNT
    # and per-entry identity are unchanged and only splice in payload — that
    # would silently drop new entries and keep stale entries for bones that
    # lost every vertex. Build straight from SkinDataEdited instead.
    # GeometryRebuild.layout_skin_membership_edit must have run first: the
    # exporter only writes placeholder GplVertexArrValue / VertexOffset.
    _membership_edited = bool(raw_skn_edit and raw_skn_edit.get('MembershipEdited'))
    # Flush-index data is rewritten onto SkinDataEdited by the topology-edit
    # skinning rebuild (GeometryRebuild._rebuild_skinning) and by the
    # membership layout pass, since it depends on the SKAcc write set. raw_skn
    # always prefers raw_skn_orig when the donor has skin data, so pull flush
    # data from raw_skn_edit specifically whenever either pass produced it.
    _flush_source = (
        raw_skn_edit
        if ((_topology_geometry_edited or _membership_edited) and raw_skn_edit
            and raw_skn_edit.get('FlushIndData') is not None)
        else raw_skn
    )
    if _membership_edited:
        sk1s = [
            SK1(
                bone_index                  = s['BoneIndex'],
                vertex_cnt                  = s['VertexCnt'],
                vertex_offset               = s.get('VertexOffset', 0),
                bind_pose_data              = _decode(s.get('BindPoseDataEdited') or s['BindPoseData'], use_b64),
                vertex_arr_field_offset     = _hex(s.get('VertexArrFieldOffset',    '0x0')),
                gpl_vertex_arr_field_offset = _hex(s.get('GplVertexArrFieldOffset', '0x0')),
                vertex_arr_absolute_ptr     = _hex(s.get('VertexArrAbsolutePtr',    '0x0')),
                gpl_vertex_arr_value        = s.get('GplVertexArrValue', 0),
            )
            for s in raw_skn_edit.get('SK1s', [])
        ]
        sk2s = [
            SK2(
                bone_index1                 = s['BoneIndex1'],
                bone_index2                 = s['BoneIndex2'],
                vertex_cnt                  = s['VertexCnt'],
                vertex_offset               = s.get('VertexOffset', 0),
                bind_pose_data              = _decode(s.get('BindPoseDataEdited') or s['BindPoseData'], use_b64),
                weight_data                 = _decode(s.get('WeightDataEdited') or s['WeightData'], use_b64),
                vertex_arr_field_offset     = _hex(s.get('VertexArrFieldOffset',    '0x0')),
                weight_arr_field_offset     = _hex(s.get('WeightArrFieldOffset',    '0x0')),
                gpl_vertex_arr_field_offset = _hex(s.get('GplVertexArrFieldOffset', '0x0')),
                vertex_arr_absolute_ptr     = _hex(s.get('VertexArrAbsolutePtr',    '0x0')),
                weight_arr_absolute_ptr     = _hex(s.get('WeightArrAbsolutePtr',    '0x0')),
                gpl_vertex_arr_value        = s.get('GplVertexArrValue', 0),
            )
            for s in raw_skn_edit.get('SK2s', [])
        ]
        sk_accs = [
            SKAcc(
                bone_index                = s['BoneIndex'],
                vertex_cnt                = s['VertexCnt'],
                bind_pose_data            = _decode(s.get('BindPoseDataEdited') or s['BindPoseData'], use_b64),
                dest_index_data           = _decode(s.get('DestIndexDataEdited') or s['DestIndexData'], use_b64),
                weight_data               = _decode(s.get('WeightDataEdited') or s['WeightData'], use_b64),
                vertex_arr_field_offset   = _hex(s.get('VertexArrFieldOffset',   '0x0')),
                dest_arr_field_offset     = _hex(s.get('DestArrFieldOffset',     '0x0')),
                gpl_dest_arr_field_offset = _hex(s.get('GplDestArrFieldOffset',  '0x0')),
                weight_arr_field_offset   = _hex(s.get('WeightArrFieldOffset',   '0x0')),
                vertex_arr_absolute_ptr   = _hex(s.get('VertexArrAbsolutePtr',   '0x0')),
                dest_arr_absolute_ptr     = _hex(s.get('DestArrAbsolutePtr',     '0x0')),
                gpl_dest_arr_value        = s.get('GplDestArrValue', 0),
                weight_arr_absolute_ptr   = _hex(s.get('WeightArrAbsolutePtr',   '0x0')),
            )
            for s in raw_skn_edit.get('SKAccs', [])
        ]
    elif raw_skn:
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
                else _edit_sk1_by_bone.get(s['BoneIndex']) if _geometry_edited else None
            )
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
        for entry_index, s in enumerate(raw_skn.get('SK2s', [])):
            key = (s['BoneIndex1'], s['BoneIndex2'])
            ed = (
                position_edit_lists['SK2s'][entry_index]
                if position_edit_lists is not None
                else _edit_sk2_by_pair.get(key) if _geometry_edited else None
            )
            if _geometry_edited and ed:
                bp_src = ed.get('BindPoseDataEdited') or ed.get('BindPoseData') or s['BindPoseData']
                wt_src = (
                    s['WeightData']
                    if position_edit_lists is not None
                    else ed.get('WeightDataEdited') or ed.get('WeightData') or s['WeightData']
                )
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
        for entry_index, s in enumerate(raw_skn.get('SKAccs', [])):
            ed = (
                position_edit_lists['SKAccs'][entry_index]
                if position_edit_lists is not None
                else _edit_acc_by_bone.get(s['BoneIndex']) if _geometry_edited else None
            )
            if _geometry_edited and ed:
                bp_src = ed.get('BindPoseDataEdited') or ed.get('BindPoseData') or s['BindPoseData']
                wt_src = (
                    s['WeightData']
                    if position_edit_lists is not None
                    else ed.get('WeightDataEdited') or ed.get('WeightData') or s['WeightData']
                )
            else:
                bp_src = s['BindPoseData']
                wt_src = s['WeightData']
            dest_src = (
                s['DestIndexData']
                if position_edit_lists is not None
                else (ed or s).get('DestIndexData') or s['DestIndexData']
            )
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

    if _membership_edited or raw_skn:
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
            flush_ind_size            = _flush_source.get('FlushIndSize', 0),
            flush_ind_data            = _decode(_flush_source['FlushIndData'], use_b64) if _flush_source.get('FlushIndData') else b'',
            quantize_info             = raw_skn['QuantizeInfo'],
            sk1s                      = sk1s,
            sk2s                      = sk2s,
            sk_accs                   = sk_accs,
            preserve_source_layout    = not _topology_geometry_edited,
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

    # ---- CustomSubmeshes ----------------------------------------------------
    # PLAN_AddSubmesh.md Phase 1 step 2: parse only, never touching donor
    # Submeshes/MeshData. Cross-checks (host bone freedom, template validity,
    # texture-assignment resolution, ...) are Phase 1 step 3's job.
    custom_submeshes = []
    for cs in model.get('CustomSubmeshes', []) or []:
        cs_uv_channels = [
            CustomSubmeshUVChannel(
                channel_index = uv['UVChannelIndex'],
                uv_data       = _decode(uv['UVChannelData'], use_b64),
                uv_faces_data = _decode(uv['UVFacesData'], use_b64),
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
            vertex_data        = _decode(cs['VertexBufferData'], use_b64),
            vertex_quantize_info = cs.get(
                'VertexBufferQuantizeInfo', _CUSTOM_SUBMESH_POSITION_FORMAT[1]
            ),
            normal_data        = _decode(cs['NormalBufferData'], use_b64) if cs.get('NormalBufferData') else None,
            normal_faces_data  = _decode(cs['NormalFacesData'], use_b64) if cs.get('NormalFacesData') else None,
            color_data         = _decode(cs['ColorChannelData'], use_b64) if cs.get('ColorChannelData') else None,
            color_faces_data   = _decode(cs['ColorFacesData'], use_b64) if cs.get('ColorFacesData') else None,
            uv_channels        = cs_uv_channels,
            faces_count        = cs['FacesCount'],
            faces_data         = _decode(cs['FacesData'], use_b64),
            texture_assignment = texture_assignment,
        ))

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
        custom_submeshes  = custom_submeshes,
    )



# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _can_preserve_gpl_internal_layout(mesh: MeshData) -> bool:
    return bool(mesh.source_gpl_base_offset) and all(
        getattr(sub, 'preserve_source_layout', True)
        and sub.source_layout_offset
        and sub.position_data_ptr_field_offset
        and sub.source_position_data_offset
        and sub.draw_states
        and sub.draw_states[0].source_state_offset
        for sub in mesh.submeshes
    )


def _can_preserve_gpl_source_layout(mesh: MeshData) -> bool:
    return _can_preserve_gpl_internal_layout(mesh) and all(
        sub.source_layout_offset
        and sub.position_data_ptr_field_offset
        and sub.source_position_data_offset
        and sub.draw_states
        and sub.draw_states[0].source_state_offset
        and all(
            len(draw_state.prim_list_data) == draw_state.prim_list_length
            for draw_state in sub.draw_states
        )
        for sub in mesh.submeshes
    )


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

    def _align32_residue(offset: int, residue: int) -> int:
        """Round offset up to the next value with the requested mod-32 residue."""
        return offset + ((residue - offset) & 31)

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
    preserve_internal_layout = _can_preserve_gpl_internal_layout(parsed.mesh)
    preserve_source_layout = _can_preserve_gpl_source_layout(parsed.mesh)

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
                pl_b = ds.prim_list_data + b'\x00' * (
                    (-len(ds.prim_list_data)) % 32
                )
                pl_offs.append(cursor)
                pl_bytes_list.append(pl_b)
                cursor += len(pl_b)
            else:
                pl_offs.append(0)
                pl_bytes_list.append(b'')

        if preserve_internal_layout:
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
                *(
                    offset + draw_state.prim_list_length
                    for offset, draw_state in zip(source_prim, sub.draw_states)
                    if offset
                ),
            ]
            cursor = max(source_ends)
            pl_offs = []
            for source_off, draw_state, data in zip(
                source_prim, sub.draw_states, pl_bytes_list
            ):
                if not data:
                    pl_offs.append(0)
                elif source_off and len(draw_state.prim_list_data) <= draw_state.prim_list_length:
                    pl_offs.append(source_off)
                else:
                    source_layout = sub.source_layout_offset - parsed.mesh.source_gpl_base_offset
                    cursor = _align32_residue(cursor, (-source_layout) & 31)
                    pl_offs.append(cursor)
                    cursor += len(data)

            if not preserve_source_layout:
                palette_offsets = {}
                pal_name_offs = []
                for pal_b in pal_name_bytes_list:
                    if pal_b not in palette_offsets:
                        palette_offsets[pal_b] = cursor
                        cursor += len(pal_b)
                        cursor = (cursor + 3) & ~3
                    pal_name_offs.append(palette_offsets[pal_b])
                name_off = cursor
                cursor += len(name_bytes)

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
            if preserve_internal_layout:
                source_layout = (
                    lay['sub'].source_layout_offset - parsed.mesh.source_gpl_base_offset
                )
                cursor = _align32_residue(cursor, source_layout & 31)
            blob_gpl_offs.append(cursor)
            cursor += lay['blob_size']
            if not preserve_internal_layout:
                cursor = _align32(cursor)

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

        # A DOLightingHeader exists only when the submesh carries normal data:
        # skinned (interleaved cc=6) meshes always do, non-skinned meshes only
        # when a separate NormalBuffer was exported.  When absent, the pointer
        # must stay 0 — a non-zero pointer to a zeroed header makes the game's
        # model loader allocate a normal vertex attribute, shifting the UV
        # attribute slots the (copied) display-state shader expects and
        # producing blocky color/UV distortion in-game.
        has_lighting = bool(
            sub.normal_buffer
            and (lay['is_interleaved'] or sub.normal_buffer.normal_data)
        )

        # GEO Descriptor (GPL-relative pointers)
        desc = GEO_DESC_OFF + i * 8
        _s.pack_into('>I', gpl, desc,     gpl_b)                       # DOLayoutPtr
        _s.pack_into('>I', gpl, desc + 4, gpl_b + lay['name_off'])     # namePtr

        # DOLayout (DOLayout-relative sub-struct pointers)
        _s.pack_into('>I', gpl, gpl_b + 0x00, POS_OFF)
        _s.pack_into('>I', gpl, gpl_b + 0x04, COL_OFF)
        _s.pack_into('>I', gpl, gpl_b + 0x08, UV_OFF)
        _s.pack_into('>I', gpl, gpl_b + 0x0c, NOR_OFF if has_lighting else 0)
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
        # Guarded by has_lighting (see DOLayout pointer write above) so the
        # pointer and the header body are emitted together, or not at all.
        if has_lighting:
            nb = sub.normal_buffer
            _s.pack_into('>I', gpl, gpl_b + NOR_OFF + 0x00, lay['nor_data_off'])
            _s.pack_into('>H', gpl, gpl_b + NOR_OFF + 0x04, lay['nor_count'])
            _s.pack_into('B',  gpl, gpl_b + NOR_OFF + 0x06, nb.quantize_info)
            _s.pack_into('B',  gpl, gpl_b + NOR_OFF + 0x07, nb.comp_count)
            _s.pack_into('>f', gpl, gpl_b + NOR_OFF + 0x08, nb.ambient_pct)
        # else: all-zero (no lighting header: pointer left 0)

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
                         len(lay['pl_bytes_list'][k]) if ds.prim_list_data else 0)

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
    """Clone the GPL section verbatim from the model's source DAT.

    Reads the model block header to determine GPL boundaries, then returns
    the raw GPL bytes unchanged.  No pointer fixups needed (all internal
    GPL pointers are GPL-section-relative or DOLayout-relative).
    """
    import struct as _s
    with open(_source_dat_path(model_offset), 'rb') as f:
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


def PatchGPLMaterialStates(gpl_bytes: bytes, data: dict, model_offset: int) -> bytes:
    """Patch aliased Type-7 material bytes over an otherwise verbatim donor GPL."""
    import struct as _s

    with open(_source_dat_path(model_offset), 'rb') as source:
        source.seek(model_offset + 0x04)
        raw = source.read(4)
    if len(raw) != 4:
        raise IOError(f'Could not read donor GPL offset at 0x{model_offset + 4:08X}')
    gpl_offset = _s.unpack('>I', raw)[0]
    gpl_absolute = model_offset + gpl_offset
    patched = bytearray(gpl_bytes)

    for submesh_index, submesh in enumerate(data['SluggiesModel'].get('Submeshes', [])):
        for state_index, state in enumerate(submesh.get('DisplayStates', [])):
            if not (
                state.get('MaterialStateAliasedByImporter')
                or state.get('ShaderModeEdited') is not None
            ):
                continue
            setting_field = _hex(state['ShaderModeFieldOffset'])
            state_relative = setting_field - 4 - gpl_absolute
            if state_relative < 0 or state_relative + 8 > len(patched):
                raise ValueError(
                    f'sub{submesh_index} ds{state_index}: material-state record '
                    f'offset 0x{state_relative:X} is outside cloned GPL size '
                    f'0x{len(patched):X}')
            pad = bytes.fromhex(state.get('DisplayStatePadBytes', '000000'))
            setting = state.get('ShaderModeEdited') or state.get('ShaderMode', '')
            if len(setting) == 8 and all(c in '0123456789abcdefABCDEF' for c in setting):
                setting_bytes = bytes.fromhex(setting)
            else:
                setting_bytes = setting.encode('ascii', errors='replace').ljust(4, b'\x00')[:4]
            patched[state_relative + 1:state_relative + 4] = pad[:3].ljust(3, b'\x00')
            patched[state_relative + 4:state_relative + 8] = setting_bytes
            _slogger.info(
                f'[GPL] patched material state sub{submesh_index} ds{state_index} '
                f'at GPL+0x{state_relative:X}',
                source='hammerspace.main',
            )

    return bytes(patched)


def PatchGPLPositionArrays(gpl_bytes: bytes, model: dict, model_offset: int) -> bytes:
    """Patch validated same-size position arrays over an otherwise cloned GPL."""
    import struct as _s

    with open(_source_dat_path(model_offset), 'rb') as source:
        source.seek(model_offset + 0x04)
        raw = source.read(4)
    if len(raw) != 4:
        raise IOError(f'Could not read donor GPL offset at 0x{model_offset + 4:08X}')
    gpl_absolute = model_offset + _s.unpack('>I', raw)[0]
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


def PatchGPLUVArrays(gpl_bytes: bytes, model: dict, model_offset: int) -> bytes:
    """Patch same-size edited UV/normal arrays over an otherwise cloned donor GPL."""
    import struct as _s

    with open(_source_dat_path(model_offset), 'rb') as source:
        source.seek(model_offset + 0x04)
        raw = source.read(4)
    if len(raw) != 4:
        raise IOError(f'Could not read donor GPL offset at 0x{model_offset + 4:08X}')
    gpl_absolute = model_offset + _s.unpack('>I', raw)[0]
    use_b64 = model.get('UseBase64', True)
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
            original = _decode(uv['UVChannelData'], use_b64)
            edited = _decode(encoded, use_b64)
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
            original = _decode(nb['NormalBufferData'], use_b64)
            edited = _decode(encoded, use_b64)
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
            original = _decode(color_channels[0]['ColorChannelData'], use_b64)
            for cc in color_channels[1:]:
                other = _decode(cc['ColorChannelData'], use_b64)
                if len(other) != len(original):
                    raise ValueError(
                        f'sub{submesh_index}: color channels report different '
                        f'donor array sizes ({len(original)} vs {len(other)})')
            for cc in color_channels:
                encoded = cc.get('ColorChannelDataEdited')
                if encoded is None:
                    continue
                edited = _decode(encoded, use_b64)
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


def PatchGPLUVRebuild(gpl_bytes: bytes, model: dict, model_offset: int) -> bytes:
    """Append resized UV/list payloads and redirect pointers over cloned GPL."""
    import struct as _s

    with open(_source_dat_path(model_offset), 'rb') as source:
        source.seek(model_offset + 0x04)
        raw = source.read(4)
    if len(raw) != 4:
        raise IOError(f'Could not read donor GPL offset at 0x{model_offset + 4:08X}')
    gpl_absolute = model_offset + _s.unpack('>I', raw)[0]
    use_b64 = model.get('UseBase64', True)
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
            original = _decode(uv['UVChannelData'], use_b64)
            edited = _decode(encoded, use_b64)
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
            _s.pack_into('>I', patched, pointer_field, data_offset - submesh_relative)
            _s.pack_into('>H', patched, count_field, len(edited) // stride)
            _slogger.info(
                f'[GPL] appended UV array sub{submesh_index} '
                f'uv{uv["UVChannelIndex"]} at GPL+0x{data_offset:X} '
                f'({len(edited):,} bytes)',
                source='hammerspace.main',
            )

        nb = submesh.get('NormalBuffer')
        encoded = nb.get('NormalBufferDataEdited') if nb else None
        if encoded is not None:
            original = _decode(nb['NormalBufferData'], use_b64)
            edited = _decode(encoded, use_b64)
            if edited != original:
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
                _s.pack_into('>I', patched, pointer_field, data_offset - submesh_relative)
                _s.pack_into('>H', patched, count_field, len(edited) // stride)
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
                original = _decode(cc['ColorChannelData'], use_b64)
                edited = _decode(encoded, use_b64)
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
                    _s.pack_into('>I', patched, pointer_field,
                                 data_offset - submesh_relative)
                    _s.pack_into('>H', patched, count_field,
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
                raw_primitive = _decode(edited_primitive, use_b64)
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
                _s.pack_into('>I', patched, pointer_field, primitive_relative)
                _s.pack_into('>I', patched, size_field, primitive_size)
                _slogger.info(
                    f'[GPL] appended primitive list sub{submesh_index} ds{state_index} '
                    f'({primitive_size:,} bytes)',
                    source='hammerspace.main',
                )
            if state.get('ShaderModeEdited') is not None:
                setting_field = _hex(state['ShaderModeFieldOffset']) - gpl_absolute
                setting = state['ShaderModeEdited']
                setting_bytes = (
                    bytes.fromhex(setting)
                    if len(setting) == 8 and all(c in '0123456789abcdefABCDEF' for c in setting)
                    else setting.encode('ascii', errors='replace').ljust(4, b'\x00')[:4]
                )
                patched[setting_field:setting_field + 4] = setting_bytes

    return bytes(patched)


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


def BuildACTBoneHierarchy(data: dict, source_model_offset: int, source_model_length: int) -> bytes:
    """Return the ACT (Bone Hierarchy) section bytes for a hammerspace build.

    Two routes (PLAN_AddBones.md Phase 2 step 4):

    - **Clone route** (no ``BoneHierarchyEdited``): identical to the
      pre-Phase-2 behaviour -- ``CloneACT`` verbatim, then
      ``_apply_root_scale_patch`` and ``_apply_geo_id_patches``. Every
      existing hammerspace build stays byte-identical.
    - **Rebuild route** (``BoneHierarchyEdited`` present): appends every
      user-added bone to the donor's own parsed bone table
      (``_rebuild_act_bone_hierarchy``) instead of leaving the ACT section a
      fixed-size verbatim clone, so bone count can change.

    Returns ``b''`` when the model has no ACT section.
    """
    act_bytes = CloneACT(source_model_offset, source_model_length)
    if not act_bytes:
        return b''

    model = data['SluggiesModel']
    if not model.get('BoneHierarchyEdited'):
        act_bytes = _apply_root_scale_patch(act_bytes, data, source_model_offset)
        act_bytes = _apply_geo_id_patches(act_bytes, data, source_model_offset)
        return act_bytes

    return _rebuild_act_bone_hierarchy(act_bytes, data, source_model_offset)


def _rebuild_act_bone_hierarchy(act_bytes: bytes, data: dict, source_model_offset: int) -> bytes:
    """PLAN_AddBones.md Phase 2: rebuild the ACT bone hierarchy instead of
    cloning it verbatim, so new leaf bones from ``BoneHierarchyEdited`` can be
    appended.

    Parses the donor ACT bytes with ``act_rebuild`` -- the same machinery
    Phase 0's P1 probe validated as byte-identical across the full player
    corpus -- which preserves every donor bone's own tree links, SRT block
    and header/name/tail bytes exactly. Every ``BoneHierarchyEdited`` entry
    with ``UserAdded`` set is then appended in ``BoneId`` order via
    ``act_rebuild.append_leaf_bone`` (F10: always a new leaf at the end of
    its parent's child chain), fed back in so each append sees the previous
    one's result -- the same incremental pattern Phase 0 P4's ``--bulk``
    probe already validated in Dolphin for repeated appends.

    GeoId ownership: a **donor** bone's ``GeoIdEdited``/custom-submesh claim
    still goes through ``_apply_root_scale_patch``/``_apply_geo_id_patches``
    on the *rebuilt* bytes below -- their ACT-relative offset math
    (``orientationPTR``, and the bone-table ``geo_file_id_raw`` field
    addressed purely by bone id) is unaffected by appending trailing bones,
    since append-only growth never moves an existing bone's table slot or
    SRT position (verified: every donor bone's ``orientationPTR`` sits before
    any new bone's SRT in bone-table order). A **new** bone has no donor
    ``GeoIdFieldOffset`` to patch through, so its ``GeoId`` is instead owned
    directly here: a custom submesh naming a new bone as ``HostBoneId`` sets
    that bone's ``geo_file_id_raw`` on the in-memory ``BoneRecord`` before
    the final byte serialization, mirroring what PLAN_AddBones.md's Phase 0
    P3 probe fixture already did by hand.
    """
    model = data['SluggiesModel']
    bone_hierarchy_edited = model['BoneHierarchyEdited']

    parsed = act_rebuild.parse_act(act_bytes)
    act_rebuild.validate_mirror_table(parsed)

    new_entries = sorted(
        (b for b in bone_hierarchy_edited if b.get('UserAdded')),
        key=lambda b: int(b['BoneId']),
    )

    expected_id = parsed.bone_count
    for entry in new_entries:
        bone_id = int(entry['BoneId'])
        if bone_id != expected_id:
            raise ValueError(
                f"BoneHierarchyEdited: new bone id {bone_id} is not contiguous with the "
                f"existing bone count ({expected_id} expected); new bone ids must be "
                "N, N+1, ... starting at the donor bone count (PLAN_AddBones.md Phase 3 rule 5)"
            )
        parent_id = entry.get('ParentBoneId')
        if parent_id is None:
            raise ValueError(f"new bone {bone_id} has no ParentBoneId; new bones may not be roots")
        parent_id = int(parent_id)
        if parent_id >= expected_id:
            raise ValueError(
                f"new bone {bone_id}'s parent {parent_id} does not exist yet; a new bone's "
                "parent must already be present (a donor bone, or an earlier new bone in "
                "BoneHierarchyEdited order)"
            )

        srt_type = int(entry.get('SRTType', 0))
        srt_blob = (
            act_rebuild.pack_srt_blob(
                srt_type, entry['Scale'], entry['Quaternion'], entry['Translation'],
            )
            if srt_type else None
        )

        parsed = act_rebuild.append_leaf_bone(
            parsed, parent_id, srt_blob,
            geo_file_id_raw=0xFFFF if entry.get('Skinned') else int(entry.get('GeoId', 0xFFFF)),
            inheritance=1 if entry.get('InheritTransform', True) else 0,
            priority=int(entry.get('DrawPriority', 0)),
            track_id=int(entry.get('TrackId', 0xFFFF)),
            mirror_bone_id=int(entry.get('MirrorBoneId', bone_id)),
            mirror_role=int(entry.get('MirrorRole', 3)),
        )
        expected_id += 1

    custom_submeshes = model.get('CustomSubmeshes') or []
    if custom_submeshes:
        donor_submesh_count = len(model.get('Submeshes') or [])
        donor_bone_count = parsed.bone_count - len(new_entries)
        bones_by_id = {b.id: b for b in parsed.bones}
        for index, cs in enumerate(custom_submeshes):
            host_bone_id = int(cs['HostBoneId'])
            if host_bone_id < donor_bone_count:
                continue  # donor host bone: handled by _apply_geo_id_patches below
            bone = bones_by_id.get(host_bone_id)
            if bone is None:
                raise ValueError(
                    f"custom submesh '{cs.get('CustomSubmeshId', '?')}': host bone "
                    f"{host_bone_id} does not exist in the rebuilt ACT bone hierarchy"
                )
            if bone.geo_file_id_raw != 0xFFFF:
                raise ValueError(
                    f"custom submesh '{cs.get('CustomSubmeshId', '?')}': host bone "
                    f"{host_bone_id} already owns GeoId {bone.geo_file_id_raw}, expected 0xFFFF"
                )
            bone.geo_file_id_raw = donor_submesh_count + index

    act_bytes = act_rebuild.rebuild_act_bytes(parsed)
    act_bytes = _apply_root_scale_patch(act_bytes, data, source_model_offset)
    act_bytes = _apply_geo_id_patches(act_bytes, data, source_model_offset)
    return act_bytes


def CloneACT(model_offset: int, model_length: int) -> bytes:
    """Clone the ACT section verbatim from the model's source DAT.

    Returns the raw ACT bytes unchanged, or b'' if the model has no ACT section.
    """
    import struct as _s
    with open(_source_dat_path(model_offset), 'rb') as f:
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


def _act_section_absolute(source_model_offset: int) -> int:
    """Return the absolute INPUT-file offset of a model's ACT section.

    Reads the model block header at ``source_model_offset`` in INPUT dt_na.dat
    and returns ``source_model_offset + act_off`` (``act_off`` is the big-endian
    uint32 at header +0x08). Returns 0 when the model has no ACT section
    (``act_off`` is 0).

    This is the reference point used to convert the ``.sluggie``'s absolute
    ``SRTOffset`` (``ACT.absolute + orientationPTR``) into an ACT-section-relative
    offset, which stays valid after the hammerspace block is relocated.
    """
    import struct as _s
    with open(_source_dat_path(source_model_offset), 'rb') as f:
        f.seek(source_model_offset)
        hdr = f.read(0x20)
    if len(hdr) < 0x20:
        return 0
    act_off = _s.unpack_from('>I', hdr, 0x08)[0]
    if not act_off:
        return 0
    return source_model_offset + act_off


def _apply_root_scale_patch(act_bytes: bytes, data: dict, source_model_offset: int) -> bytes:
    """Apply the root-bone SRT scale patch to a cloned ACT section.

    Reads the model's ``BoneHierarchy`` and ``RootBoneScaleEdited`` from the
    ``.sluggies`` data, computes the root-bone SRT scale patch (3 big-endian
    floats) via ``root_scale.hammerspace_root_scale_patch``, and writes it into
    the in-memory ACT section bytes at the ACT-section-relative offset
    (``orientationPTR + 0x04``).

    Because the hammerspace block is written to a new absolute offset, the
    absolute ``SRTOffset`` recorded in the ``.sluggie`` is not valid for the
    output. The ACT section, however, is cloned verbatim, so the SRT offset
    relative to the ACT section start is stable. Writing the scale at that
    relative offset keeps it correct regardless of hammerspace block size
    changes or relocation.

    Returns the (possibly modified) ACT section bytes. When there is no
    ``RootBoneScaleEdited`` (or no bone hierarchy / no ACT section), the original
    ``act_bytes`` are returned unchanged.
    """
    model = data['SluggiesModel']
    bone_hierarchy = model.get('BoneHierarchyEdited') or model.get('BoneHierarchy')
    if not bone_hierarchy or not act_bytes:
        return act_bytes
    act_section_absolute = _act_section_absolute(source_model_offset)
    if not act_section_absolute:
        return act_bytes
    patch = _root_scale.hammerspace_root_scale_patch(
        model, bone_hierarchy, act_section_absolute, _hs_abort
    )
    if patch is None:
        return act_bytes
    bone_id, scale_relative, raw = patch
    if scale_relative < 0 or scale_relative + len(raw) > len(act_bytes):
        raise ValueError(
            f'root-bone SRT scale for bone {bone_id} at ACT+0x{scale_relative:X} '
            f'({len(raw)} bytes) exceeds the cloned ACT section size '
            f'0x{len(act_bytes):X}; the .sluggie SRTOffset metadata does not match '
            f'this model ACT layout'
        )
    patched = bytearray(act_bytes)
    patched[scale_relative:scale_relative + len(raw)] = raw
    _slogger.info(
        f'[ACT] root-bone SRT scale: bone {bone_id} wrote {raw.hex()} at '
        f'ACT+0x{scale_relative:X} (section-relative; stable across hammerspace '
        f'relocation)',
        source='hammerspace.main',
    )
    return bytes(patched)


def _hs_abort(message: str) -> None:
    """Log and raise for a root-scale patch error in the hammerspace pipeline."""
    _slogger.error(message, source='hammerspace.main')
    raise ValueError(message)


def _apply_geo_id_patches(act_bytes: bytes, data: dict, source_model_offset: int) -> bytes:
    """Patch bone ``GeoId`` fields in a cloned ACT section
    (PLAN_AddSubmesh.md Phase 3 steps 1-2): each custom submesh's host bone
    gets ``GeoId = <new submesh index>``, and every ``GeoIdEdited`` bone (the
    donor rigid-submesh retargeting the in-place patcher already supports,
    via ``BoneHierarchy[].GeoIdEdited``) gets its edited value written too.
    Hammerspace silently ignored ``GeoIdEdited`` before this.

    Follows the same pattern as ``_apply_root_scale_patch``: hammerspace's ACT
    section is always cloned verbatim, so a donor bone's absolute
    ``GeoIdFieldOffset`` converted to an ACT-section-relative offset (via
    ``_act_section_absolute``) stays valid regardless of where the
    hammerspace block is relocated. New submesh indices are assigned in
    ``model['CustomSubmeshes']`` order starting at ``len(model['Submeshes'])``,
    matching ``PatchGPLAppendSubmesh``'s own ``donor_count + index`` rule, so
    the GPL append and the ACT ownership patch always agree.

    ``GeoIdEdited`` retargets are written first, using the same skip-if-no-op
    rule as ``InplacePatcher/patch_inplace.py`` (compare against
    ``_bone_geo_id_raw``, the literal un-edited ACT sentinel). Custom submesh
    claims are written second and read the already-patched bytes, so a
    retarget that frees a bone in the same build (``GeoIdEdited = 0xFFFF``)
    makes that bone claimable by a custom submesh in the same call, and a
    retarget that assigns a bone away from ``0xFFFF`` makes it correctly
    unclaimable.

    Returns the (possibly modified) ACT section bytes. When there is neither
    a ``CustomSubmeshes`` entry nor a ``GeoIdEdited`` bone (or no ACT
    section), the original ``act_bytes`` are returned unchanged.
    ``_validate_custom_submeshes`` has already checked that every host bone
    exists, is unclaimed (``GeoIdRaw == 0xFFFF``) and is claimed by exactly
    one custom submesh, so the only new failure mode for custom submeshes
    here is missing/stale ``GeoIdFieldOffset`` metadata. A stale field is
    tolerated only for the known pre-fix off-by-8 export.py bug (see the
    fallback below); anything else is rejected rather than guessed.
    """
    import struct as _s

    model = data['SluggiesModel']
    bone_hierarchy = model.get('BoneHierarchy') or []
    custom_submeshes = model.get('CustomSubmeshes') or []
    geo_id_edited_bones = [b for b in bone_hierarchy if b.get('GeoIdEdited') is not None]
    if not custom_submeshes and not geo_id_edited_bones:
        return act_bytes
    if not act_bytes:
        return act_bytes
    act_section_absolute = _act_section_absolute(source_model_offset)
    if not act_section_absolute:
        return act_bytes

    bones_by_id = {int(b['BoneId']): b for b in bone_hierarchy}
    donor_count = len(model.get('Submeshes') or [])
    patched = bytearray(act_bytes)

    for bone in geo_id_edited_bones:
        bone_id = int(bone['BoneId'])
        target_geo_raw = int(bone['GeoIdEdited'])
        if target_geo_raw < 0 or target_geo_raw > 0xFFFF:
            raise ValueError(
                f'bone {bone_id}: GeoIdEdited value {target_geo_raw} is out of '
                'range (0..65535)'
            )
        if target_geo_raw == _bone_geo_id_raw(bone):
            continue  # no-op retarget, nothing to write

        field_off_hex = bone.get('GeoIdFieldOffset')
        if not field_off_hex:
            raise ValueError(
                f'bone {bone_id}: GeoIdEdited is set but GeoIdFieldOffset metadata '
                'is missing; re-export the model with the latest SluggiesTools '
                'export.py'
            )
        act_relative = int(field_off_hex, 16) - act_section_absolute
        if act_relative < 0 or act_relative + 2 > len(patched):
            raise ValueError(
                f'bone {bone_id}: GeoIdFieldOffset 0x{int(field_off_hex, 16):X} '
                f'(ACT+0x{act_relative:X}) falls outside the cloned ACT section '
                f'(0x{len(patched):X} bytes); the .sluggie\'s GeoIdFieldOffset '
                'metadata does not match this model\'s ACT layout'
            )

        _s.pack_into('>H', patched, act_relative, target_geo_raw)
        _slogger.info(
            f'[ACT] bone {bone_id} GeoId -> {target_geo_raw} at ACT+0x{act_relative:X} '
            '(GeoIdEdited retarget; section-relative, stable across hammerspace '
            'relocation)',
            source='hammerspace.main',
        )

    for index, cs in enumerate(custom_submeshes):
        cs_id = cs.get('CustomSubmeshId', '<missing CustomSubmeshId>')
        host_bone_id = int(cs['HostBoneId'])
        if host_bone_id not in bones_by_id:
            # PLAN_AddBones.md Phase 2: a host bone with no BoneHierarchy entry
            # is a user-added bone -- _rebuild_act_bone_hierarchy already owns
            # its GeoId directly (it has no donor GeoIdFieldOffset to patch
            # through), so there is nothing for this loop to do here.
            continue
        new_submesh_index = donor_count + index
        bone = bones_by_id[host_bone_id]

        field_off_hex = bone.get('GeoIdFieldOffset')
        if not field_off_hex:
            raise ValueError(
                f"custom submesh '{cs_id}': host bone {host_bone_id} is missing "
                "GeoIdFieldOffset metadata; re-export the model with the latest "
                "SluggiesTools export.py"
            )
        act_relative = int(field_off_hex, 16) - act_section_absolute
        if act_relative < 0 or act_relative + 2 > len(patched):
            raise ValueError(
                f"custom submesh '{cs_id}': host bone {host_bone_id} GeoIdFieldOffset "
                f"0x{int(field_off_hex, 16):X} (ACT+0x{act_relative:X}) falls outside "
                f"the cloned ACT section (0x{len(patched):X} bytes); the .sluggie's "
                "GeoIdFieldOffset metadata does not match this model's ACT layout"
            )

        current = _s.unpack_from('>H', patched, act_relative)[0]
        if current != 0xFFFF:
            # Exports written before the export.py GeoIdFieldOffset fix (see
            # that file's extract_bone_hierarchy comment, and
            # build_add_submesh_fixture.py's identical fallback) recorded
            # bl.absolute + 0x0C instead of the real geoFileIdRaw field at
            # bl.absolute + 0x14, an 8-byte offset bug. Older .sluggie files
            # on disk still carry the stale offset. Try the corrected
            # location, but only accept it if it actually reads the unowned
            # sentinel; otherwise fail rather than guess.
            fallback_relative = act_relative + 8
            if (
                fallback_relative + 2 <= len(patched)
                and _s.unpack_from('>H', patched, fallback_relative)[0] == 0xFFFF
            ):
                _slogger.warning(
                    f"custom submesh '{cs_id}': host bone {host_bone_id} GeoIdFieldOffset "
                    f"at ACT+0x{act_relative:X} reads 0x{current:04X}, not 0xFFFF; using "
                    f"ACT+0x{fallback_relative:X} instead (compensating for the pre-fix "
                    "export.py off-by-8 GeoIdFieldOffset bug)",
                    source='hammerspace.main',
                )
                act_relative = fallback_relative
                current = 0xFFFF
            else:
                raise ValueError(
                    f"custom submesh '{cs_id}': host bone {host_bone_id} GeoId field at "
                    f"ACT+0x{act_relative:X} reads 0x{current:04X}, not 0xFFFF; it may "
                    "already have been claimed by another patch, or GeoIdFieldOffset "
                    "metadata is stale"
                )

        _s.pack_into('>H', patched, act_relative, new_submesh_index)
        _slogger.info(
            f"[ACT] custom submesh '{cs_id}': bone {host_bone_id} GeoId -> "
            f'{new_submesh_index} at ACT+0x{act_relative:X} (section-relative; '
            'stable across hammerspace relocation)',
            source='hammerspace.main',
        )

    return bytes(patched)


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

    with open(_source_dat_path(parsed.model_offset), 'rb') as f:
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
    """Clone the TEX section verbatim from the model's source DAT.

    Returns the raw TEX bytes unchanged, or b'' if the model has no TEX section.
    """
    import struct as _s
    with open(_source_dat_path(model_offset), 'rb') as f:
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


def BuildTEX(parsed: SluggieParsed, texture_plan=None) -> bytes:
    """Build a complete TEX section from parsed descriptors and an optional texture plan.

    Textures present in ``texture_plan.entries`` are re-encoded: their image and
    palette payloads come from the plan entry, and their width/height come from
    the entry's actual encoded dimensions. Contiguous entries after the donor
    count are appended by cloning the selected donor descriptor template.
    Donor textures not in the plan (skipped or absent) are cloned verbatim from
    INPUT dt_na.dat using the descriptor's original absolute offsets.

    Binary layout (all big-endian):

      TEX Header (4 bytes):
        +0x00  uint16  texture_count
        +0x02  uint16  clut_count

      Descriptor table (texture_count * 0x20 bytes):
        +0x00  uint32  image_data_offset   (relative to TEX section start)
        +0x04  uint32  palette_data_offset (relative to TEX section start, 0 if none)
        +0x08  uint16  height
        +0x0A  uint16  width
        +0x0C  uint8   edge_lod_enable
        +0x0D  uint8   min_lod
        +0x0E  uint8   max_lod
        +0x0F  uint8   unpacked
        +0x10  byte[7] unknown (additional mip count at +0x16)
        +0x17  uint8   image_format
        +0x18  uint16  palette_entries
        +0x1A  uint8   palette_format
        +0x1B  byte[5] unknown

      Data region:
        Image payloads packed sequentially, then palette payloads. The data
        region starts at the first 32-byte-aligned offset after the
        descriptor table (zero padding closes the gap), and every individual
        image and palette payload is likewise zero-padded up to the next
        32-byte boundary before the next payload starts, matching vanilla
        TEX sections (F10).
    """
    import struct as _s
    from texture_helper import _image_payload_size

    textures = list(parsed.textures.textures) if parsed.textures else []
    if not textures:
        _slogger.info("[BuildTEX] No textures; returning empty TEX section", source="hammerspace.main")
        return b''

    entries_by_index = {}
    if texture_plan is not None:
        for entry in texture_plan.entries:
            if not isinstance(entry.texture_index, int) or entry.texture_index < 0:
                raise ValueError(
                    f"[BuildTEX] invalid texture plan index {entry.texture_index!r}"
                )
            if entry.texture_index in entries_by_index:
                raise ValueError(f"[BuildTEX] duplicate texture plan index {entry.texture_index}")
            entries_by_index[entry.texture_index] = entry
        donor_count = len(textures)
        addition_indices = sorted(
            index for index in entries_by_index if index >= donor_count
        )
        expected_addition_indices = list(range(donor_count, donor_count + len(addition_indices)))
        if addition_indices != expected_addition_indices:
            raise ValueError(
                f"[BuildTEX] appended texture indices must be contiguous "
                f"{expected_addition_indices}, found {addition_indices}"
            )
        for index in addition_indices:
            entry = entries_by_index[index]
            template_index = entry.template_texture_index
            if template_index is None or template_index < 0 or template_index >= donor_count:
                raise ValueError(
                    f"[BuildTEX] appended texture {index} has invalid template texture "
                    f"{template_index}"
                )
            template = textures[template_index]
            if template.format in (0x8, 0x9, 0xA):
                raise ValueError(
                    f"[BuildTEX] appended texture {index} uses unsupported indexed "
                    f"template format 0x{template.format:02X}"
                )
            if entry.format != template.format:
                raise ValueError(
                    f"[BuildTEX] appended texture {index} format 0x{entry.format:02X} "
                    f"does not match template format 0x{template.format:02X}"
                )
            textures.append(Texture(
                texture_index=index,
                width=entry.width,
                height=entry.height,
                format=template.format,
                palette_entries=entry.palette_entries,
                palette_format=entry.palette_format or 0,
                edge_lod_enable=template.edge_lod_enable,
                min_lod=template.min_lod,
                max_lod=template.max_lod,
                unpacked=template.unpacked,
                desc_unknown_at_10=template.desc_unknown_at_10,
                desc_unknown_at_1b=template.desc_unknown_at_1b,
                image_data_offset=0,
                image_data_length=0,
                palette_data_offset=None,
                palette_data_length=None,
                texture_descriptor_offset=0,
            ))

    # --- Pass 1: determine payload bytes and dimensions for each texture ---
    image_payloads = []   # list of (texture_index, bytes) in descriptor order
    palette_payloads = [] # list of (texture_index, bytes) in descriptor order
    dims = {}             # texture_index -> (width, height)
    mip_counts = {}       # texture_index -> int

    for tex in textures:
        idx = tex.texture_index
        entry = entries_by_index.get(idx)
        if entry is not None:
            if entry.format != tex.format:
                raise ValueError(
                    f"[BuildTEX] texture {idx}: plan format 0x{entry.format:02X} "
                    f"does not match descriptor format 0x{tex.format:02X}"
                )
            # Re-encoded: validate payload size against the encoded dimensions.
            expected = _image_payload_size(entry.width, entry.height, entry.format)
            if len(entry.image_data) != expected:
                raise ValueError(
                    f"[BuildTEX] texture {idx}: encoded image payload is "
                    f"{len(entry.image_data)} bytes but {expected} bytes are "
                    f"expected for {entry.width}x{entry.height} format 0x{entry.format:02X}"
                )
            image_payloads.append((idx, entry.image_data))
            if entry.palette_data:
                palette_payloads.append((idx, entry.palette_data))
            dims[idx] = (entry.width, entry.height)
            # Single-image encoding path is base-only; reset mip count.
            mip_counts[idx] = 0
        else:
            # Cloned: read the payload from the DAT used during export.
            if not tex.image_data_offset or not tex.image_data_length:
                raise ValueError(
                    f"[BuildTEX] texture {idx}: cannot clone payload because "
                    f"image_data_offset={tex.image_data_offset} or "
                    f"image_data_length={tex.image_data_length} is zero"
                )
            with open(_source_dat_path(tex.image_data_offset), 'rb') as f:
                f.seek(tex.image_data_offset)
                image_bytes = f.read(tex.image_data_length)
            if len(image_bytes) != tex.image_data_length:
                raise IOError(
                    f"[BuildTEX] texture {idx}: expected to read "
                    f"{tex.image_data_length} bytes at offset 0x{tex.image_data_offset:X} "
                    f"but only got {len(image_bytes)}"
                )
            image_payloads.append((idx, image_bytes))
            if tex.palette_data_offset and tex.palette_data_length:
                with open(_source_dat_path(tex.palette_data_offset), 'rb') as f:
                    f.seek(tex.palette_data_offset)
                    palette_bytes = f.read(tex.palette_data_length)
                if len(palette_bytes) != tex.palette_data_length:
                    raise IOError(
                        f"[BuildTEX] texture {idx}: expected to read "
                        f"{tex.palette_data_length} bytes at offset 0x{tex.palette_data_offset:X} "
                        f"but only got {len(palette_bytes)}"
                    )
                palette_payloads.append((idx, palette_bytes))
            dims[idx] = (tex.width, tex.height)
            mip_counts[idx] = tex.desc_unknown_at_10[6] if len(tex.desc_unknown_at_10) > 6 else 0

    clut_count = sum(
        1
        for tex in textures
        if (
            entries_by_index[tex.texture_index].palette_entries
            if tex.texture_index in entries_by_index
            else tex.palette_entries
        )
    )

    # --- Pass 2: lay out the section ---
    header_size = 4
    desc_size = 0x20
    desc_table_size = len(textures) * desc_size
    # Match the original TEX layout by starting the packed data region at the
    # first 32-byte boundary after the descriptor table (F10). Every
    # individual image/palette payload start is also 32-byte aligned.
    data_start = (header_size + desc_table_size + 31) & ~31

    # Image payloads first, then palette payloads. Each payload start is
    # 32-byte aligned (F10), matching vanilla TEX sections.
    cursor = data_start
    image_offsets = {}
    for idx, payload in image_payloads:
        image_offsets[idx] = cursor
        cursor = (cursor + len(payload) + 31) & ~31
    palette_offsets = {}
    for idx, payload in palette_payloads:
        palette_offsets[idx] = cursor
        cursor = (cursor + len(payload) + 31) & ~31

    out = bytearray()
    # Header
    out += _s.pack('>HH', len(textures), clut_count)
    # Descriptors
    for tex in textures:
        idx = tex.texture_index
        width, height = dims[idx]
        mip_count = mip_counts[idx]
        unknown_10 = bytearray(tex.desc_unknown_at_10)
        if len(unknown_10) < 7:
            unknown_10 = unknown_10 + bytes(7 - len(unknown_10))
        unknown_10[6] = mip_count & 0xFF
        unknown_1b = tex.desc_unknown_at_1b
        if len(unknown_1b) < 5:
            unknown_1b = unknown_1b + bytes(5 - len(unknown_1b))
        palette_offset = palette_offsets.get(idx, 0)
        out += _s.pack('>II', image_offsets[idx], palette_offset)
        out += _s.pack('>HH', height, width)
        out += _s.pack('>BBBB',
                       1 if tex.edge_lod_enable else 0,
                       int(tex.min_lod) & 0xFF,
                       int(tex.max_lod) & 0xFF,
                       tex.unpacked & 0xFF)
        out += bytes(unknown_10)
        out += _s.pack('>B', tex.format & 0xFF)
        out += _s.pack('>H', tex.palette_entries & 0xFFFF)
        out += _s.pack('>B', tex.palette_format & 0xFF)
        out += bytes(unknown_1b[:5])
    # Zero padding to reach the 32-byte-aligned data region (see data_start above).
    # This mirrors the original TEX sections' initial gap. It does not align
    # each payload independently.
    if len(out) < data_start:
        out += b'\x00' * (data_start - len(out))
    # Data region: each payload is followed by zero padding out to the next
    # 32-byte boundary (F10), matching the offsets computed above.
    for idx, payload in image_payloads:
        out += payload
        padded_len = (len(payload) + 31) & ~31
        out += b'\x00' * (padded_len - len(payload))
    for idx, payload in palette_payloads:
        out += payload
        padded_len = (len(payload) + 31) & ~31
        out += b'\x00' * (padded_len - len(payload))

    _slogger.info(
        f"[BuildTEX] built {len(textures)} texture(s), "
        f"{len(image_payloads)} image payload(s), "
        f"{len(palette_payloads)} palette payload(s); "
        f"section size {len(out):,} bytes",
        source="hammerspace.main",
    )
    return bytes(out)


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

    with open(_source_dat_path(parsed.model_offset), 'rb') as f:
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
    with open(_source_dat_path(parsed.model_offset), 'rb') as f:
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
    import struct as _s
    n_sk1 = _s.unpack_from('>H', skn_bytes, 0x00)[0]
    n_sk2 = _s.unpack_from('>H', skn_bytes, 0x02)[0]
    n_acc = _s.unpack_from('>H', skn_bytes, 0x04)[0]
    quantize_info = _s.unpack_from('B', skn_bytes, 0x06)[0]
    sk1_ptr = _s.unpack_from('>I', skn_bytes, 0x08)[0]
    sk2_ptr = _s.unpack_from('>I', skn_bytes, 0x0c)[0]
    skacc_ptr = _s.unpack_from('>I', skn_bytes, 0x10)[0]
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
        arr = _s.unpack_from('>I', skn_bytes, b + 0x30)[0]
        cnt = _s.unpack_from('>H', skn_bytes, b + 0x3a)[0]
        off = _s.unpack_from('B', skn_bytes, b + 0x3c)[0]
        _scale_at(arr, cnt, off)
    for i in range(n_sk2):
        b = sk2_ptr + i * 0x74
        if b + 0x74 > len(skn_bytes):
            break
        arr = _s.unpack_from('>I', skn_bytes, b + 0x60)[0]
        cnt = _s.unpack_from('>H', skn_bytes, b + 0x70)[0]
        off = _s.unpack_from('B', skn_bytes, b + 0x72)[0]
        _scale_at(arr, cnt, off)
    for i in range(n_acc):
        b = skacc_ptr + i * 0x44
        if b + 0x44 > len(skn_bytes):
            break
        arr = _s.unpack_from('>I', skn_bytes, b + 0x30)[0]
        cnt = _s.unpack_from('>H', skn_bytes, b + 0x42)[0]
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
    with open(_source_dat_path(model_offset), 'rb') as f:
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
    with open(_source_dat_path(model_offset), 'rb') as f:
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

    def _section_align_padding(length: int) -> bytes:
        # F10: every section start must sit at 0 mod 32 relative to the
        # block. HDR_SIZE (0x20) is itself 32-aligned, so rounding each
        # section's own length up to a multiple of 32 keeps every following
        # section start aligned too. This is a no-op whenever the section is
        # already a multiple of 32 long, which vanilla sections always are
        # (F10), so unchanged/byte-identical builds are unaffected.
        return b'\x00' * ((-length) % 32)

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
    if not gpl_section_padding:
        # GPL grew past what donor-span preservation can cover (or there is
        # no donor header to preserve against, e.g. a synthetic block): fall
        # back to a plain 32-byte-boundary pad so ACT/TEX/SKN still start
        # aligned (F10).
        gpl_section_padding = _section_align_padding(len(gpl_bytes))

    act_off = gpl_off + len(gpl_bytes) + len(gpl_section_padding)
    act_padding = _section_align_padding(len(act_bytes)) if act_bytes else b''
    tex_off = act_off + len(act_bytes) + len(act_padding)
    tex_padding = _section_align_padding(len(tex_bytes)) if tex_bytes else b''
    skn_unaligned_off = tex_off + len(tex_bytes) + len(tex_padding)
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
    tail_start_unaligned = skn_off + len(skn_bytes) + len(skn_trailing_padding)
    tail_padding = _section_align_padding(tail_start_unaligned) if trailing_bytes else b''
    tail_start = tail_start_unaligned + len(tail_padding)

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

    return (bytes(hdr) + gpl_bytes + gpl_section_padding + act_bytes + act_padding
            + tex_bytes + tex_padding + skn_padding
            + skn_bytes + skn_trailing_padding + tail_padding + trailing_bytes)


def CloneHEADER(model_offset: int) -> bytes:
    """Clone the 0x20-byte model block header verbatim from the source DAT.

    The header contains block-relative pointers to GPL, ACT, TEX, SKN, and
    any ptr6/ptr7/ptr8 sub-sections.  When all sections are cloned at their
    original sizes and reassembled in the same order, these pointers remain
    valid without any fixups.
    """
    with open(_source_dat_path(model_offset), 'rb') as f:
        f.seek(model_offset)
        data = f.read(0x20)
    _slogger.info(f"[CloneHEADER] 0x20 bytes from offset 0x{model_offset:08X}", source="hammerspace.main")
    return data


def _validate_section_modes(modes: SectionModes) -> None:
    supported = {
        'GPL': {'clone', 'build'},
        'ACT': {'clone'},
        'TEX': {'clone', 'build'},
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


def BuildModelBlock(
    data: dict,
    section_modes: SectionModes | None = None,
    sluggie_path: str | os.PathLike[str] | None = None,
    tex_png_overrides: dict[int, str] | None = None,
    texture_plan=None,
) -> ModelBlockBuild:
    """Assemble a model block without modifying output DAT, DOL, or FST files.

    When ``section_modes.tex == 'build'`` and the sluggie has
    ``ReimportTextures`` set, the TEX section is rebuilt from the edited PNGs
    in the sluggie's ``tex/`` folder via :func:`BuildTEX`. ``sluggie_path``
    must then point at the ``.sluggies`` file so the texture plan builder can
    resolve the PNGs. Without ``ReimportTextures`` (or when ``sluggie_path``
    is absent), the rebuilt TEX section clones every texture payload from
    INPUT dt_na.dat, which is byte-equivalent to the clone path.
    """
    modes = section_modes or SectionModes()
    _validate_section_modes(modes)

    model = data['SluggiesModel']
    _validate_hammerspace_contract(model, modes)
    _validate_bone_hierarchy_edited(model)
    _validate_custom_submeshes(model)
    if model.get('DesiredTextureAssignments') and (
        modes.gpl != 'build'
        or modes.tex != 'build'
        or not model.get('ReimportTextures')
    ):
        raise ValueError(
            'DesiredTextureAssignments require GPL and TEX build modes with '
            'ReimportTextures enabled'
        )
    custom_submesh_additional_texture_names = {
        (cs.get('TextureAssignment') or {}).get('AdditionalTextureFileName')
        for cs in model.get('CustomSubmeshes') or []
    } - {None}
    if custom_submesh_additional_texture_names and (
        modes.tex != 'build' or not model.get('ReimportTextures')
    ):
        raise ValueError(
            "CustomSubmeshes with TextureAssignment.AdditionalTextureFileName require "
            "SectionModes.tex='build' with ReimportTextures enabled"
        )
    chunk_number = model['ChunkNumber']
    file_index = model['FileIndex']
    original_offset, original_length = hh.readDolEntry(chunk_number, file_index)
    if original_offset == -1 or original_length <= 0:
        raise ValueError(
            f'Invalid donor DOL entry for chunk={chunk_number}, file_index={file_index}: '
            f'offset={original_offset}, length={original_length}'
        )
    source_model_offset = _hex(model.get('ModelOffset', original_offset))
    source_model_length = model.get('ModelLength', original_length)
    route_prefix = b''
    route_prefix_size = 0
    route_container = b''
    archive_layout = None
    if source_model_offset >= hh.BASE_SIZE:
        # Hammerspace-resident source: the model block already lives in the
        # hammerspace region of OUTPUT_DAT (e.g. a previously patched clone or
        # unused character).  There is no DOL-entry container prefix to
        # preserve — the clone functions read the model block directly from
        # source_model_offset.
        _slogger.info(
            f'[Container] source model is hammerspace-resident at '
            f'0x{source_model_offset:08X}; no DOL-entry prefix to preserve',
            source='hammerspace.main',
        )
    else:
        route_prefix_size = source_model_offset - original_offset
        if route_prefix_size < 0 or route_prefix_size + source_model_length > original_length:
            raise ValueError(
                f'schema model range 0x{source_model_offset:08X}+{source_model_length:,} '
                f'is outside donor DOL entry 0x{original_offset:08X}+{original_length:,}')
        route_suffix_size = original_length - route_prefix_size - source_model_length
        if route_prefix_size or route_suffix_size:
            # The DOL entry holds more than this model, so read all of it: the
            # bytes around the model have to be carried into the new block.
            with open(_source_dat_path(original_offset), 'rb') as source:
                source.seek(original_offset)
                route_container = source.read(original_length)
            if len(route_container) != original_length:
                raise IOError(
                    f'could not read the {original_length} byte DOL entry '
                    f'at 0x{original_offset:08X}')
            route_prefix = route_container[:route_prefix_size]
            archive_layout = parse_archive_container(route_container)
            if archive_layout is not None and archive_layout.member_at(route_prefix_size) is None:
                # An archive whose table does not list this model: rebuilding it
                # would shift members the schema knows nothing about.
                raise ValueError(
                    f'model at 0x{source_model_offset:08X} is not a populated slot of '
                    f'the archive at 0x{original_offset:08X}')
            if archive_layout is not None:
                member = archive_layout.member_at(route_prefix_size)
                if member.length != source_model_length:
                    raise ValueError(
                        f'archive slot {member.slot} spans {member.length:,} bytes but the '
                        f'schema reports ModelLength={source_model_length:,}')
                if member.offset % hh.HS_ALIGN_BYTES:
                    # The block lands on a 32-byte boundary, so a member off the
                    # boundary would drag the model's hot data off it too.
                    raise ValueError(
                        f'archive slot {member.slot} starts at +0x{member.offset:X}, which is '
                        f'not a multiple of {hh.HS_ALIGN_BYTES}')
                _slogger.info(
                    f'[Container] DOL entry is an archive: {archive_layout.file_count} slots, '
                    f'{len(archive_layout.members)} populated; rebuilding slot {member.slot} '
                    f'at +0x{member.offset:X} and carrying the other '
                    f'{len(archive_layout.members) - 1} member(s) along',
                    source='hammerspace.main',
                )
            else:
                if route_suffix_size:
                    _slogger.warning(
                        f'[Container] DOL entry 0x{original_offset:08X}+{original_length:,} has '
                        f'{route_suffix_size:,} bytes after the model but is not a readable '
                        f'archive; those bytes are dropped from the rebuilt block',
                        source='hammerspace.main',
                    )
                if route_prefix_size:
                    _slogger.info(
                        f'[Container] preserving {route_prefix_size}-byte DOL entry prefix; '
                        f'inner model starts at +0x{route_prefix_size:X}',
                        source='hammerspace.main',
                    )

    position_edits = _position_edits(model) if modes.gpl == 'build' else []
    if position_edits:
        model['_PositionEditSubmeshes'] = [index for index, _, _ in position_edits]
    else:
        model.pop('_PositionEditSubmeshes', None)
    if modes.gpl == 'build':
        rebuild_surface_assignments(data)
        rebuild_edited_uvs(data)
        apply_desired_texture_assignments(data)
    if modes.skn == 'build':
        layout_skin_membership_edit(data)
    parsed = ParseSluggie(data)
    if getattr(parsed, 'custom_submeshes', None) and modes.gpl != 'build':
        raise ValueError("CustomSubmeshes require SectionModes.gpl='build'")
    if modes.tex == 'build':
        # Resolved before the GPL append below so a custom submesh's
        # AdditionalTextureFileName can be patched with its final TEX index
        # immediately, instead of a placeholder re-patched later
        # (PLAN_AddSubmesh.md Phase 4 step 2, "build order").
        additional_texture_descriptors = model.get('AdditionalTextureDescriptors') or []
        if texture_plan is not None and (
            model.get('ReimportTextures') or tex_png_overrides or additional_texture_descriptors
        ):
            raise ValueError(
                'a caller-supplied texture_plan cannot be combined with '
                'ReimportTextures, png overrides, or AdditionalTextureDescriptors'
            )
        if additional_texture_descriptors and not model.get('ReimportTextures'):
            raise ValueError(
                "AdditionalTextureDescriptors require 'ReimportTextures' to be enabled"
            )
        if texture_plan is None and (
            model.get('ReimportTextures') or tex_png_overrides or additional_texture_descriptors
        ):
            if sluggie_path is None:
                raise ValueError(
                    "tex='build' with texture reimport inputs requires "
                    "the sluggie path to resolve the tex/ folder; pass "
                    "sluggie_path to BuildModelBlock"
                )
            import texture_helper as _tex
            additions = tuple(
                _tex.AdditionalTextureDescriptor(
                    texture_file_name=entry['TextureFileName'],
                    template_texture_index=int(entry['TemplateTextureIndex']),
                )
                for entry in additional_texture_descriptors
            )
            plan_kwargs = {
                'allow_dimension_change': True,
                'png_overrides': tex_png_overrides,
            }
            if additions:
                plan_kwargs['additional_descriptors'] = additions
            texture_plan = _tex.build_hammerspace_texture_plan(
                sluggie_path,
                model.get('TextureDescriptors') or [],
                **plan_kwargs,
            )
            for _sk in texture_plan.skipped:
                _sk_fields = [f"expected {_sk.expected_payload_length} bytes"]
                if _sk.generated_payload_length is not None:
                    _sk_fields.append(f"generated {_sk.generated_payload_length} bytes")
                _slogger.warning(
                    f"texture {_sk.texture_index} ({_sk.texture_file_name}): "
                    f"{', '.join(_sk_fields)}; left unchanged ({_sk.reason})",
                    source='hammerspace.main',
                )
    if modes.gpl == 'build':
        has_material_state_edits = any(
            state.get('MaterialStateAliasedByImporter')
            or state.get('ShaderModeEdited') is not None
            for submesh in model.get('Submeshes', [])
            for state in submesh.get('DisplayStates', [])
        )
        uv_lists_rebuilt = any(
            submesh.get('UVPrimitiveListsRebuiltByImporter')
            or submesh.get('NormalPrimitiveListsRebuiltByImporter')
            or submesh.get('ColorPrimitiveListsRebuiltByImporter')
            for submesh in model.get('Submeshes', [])
        )
        uv_array_edits = any(
            submesh.get('UVArraysEditedByImporter')
            for submesh in model.get('Submeshes', [])
        )
        normal_array_edits = any(
            submesh.get('NormalArraysEditedByImporter')
            for submesh in model.get('Submeshes', [])
        )
        color_array_edits = any(
            submesh.get('ColorArraysEditedByImporter')
            for submesh in model.get('Submeshes', [])
        )
        if (
            has_material_state_edits
            or position_edits
            or uv_array_edits
            or normal_array_edits
            or color_array_edits
            or getattr(parsed, 'custom_submeshes', None)
        ):
            gpl_bytes = CloneGPL(source_model_offset, source_model_length)
            if has_material_state_edits:
                gpl_bytes = PatchGPLMaterialStates(
                    gpl_bytes, data, source_model_offset
                )
            if position_edits:
                gpl_bytes = PatchGPLPositionArrays(
                    gpl_bytes, model, source_model_offset
                )
            if uv_array_edits or normal_array_edits or color_array_edits:
                gpl_bytes = (
                    PatchGPLUVRebuild(gpl_bytes, model, source_model_offset)
                    if uv_lists_rebuilt
                    else PatchGPLUVArrays(gpl_bytes, model, source_model_offset)
                )
            if getattr(parsed, 'custom_submeshes', None):
                # Always clone + append (never the full BuildGPLMeshData
                # rebuild): a same-count rebuild's preserve-layout fast path
                # is known to corrupt unmodified donor data (Phase 0 finding,
                # PLAN_AddSubmesh.md Phase 2), and a new submesh has no donor
                # layout to preserve regardless.
                texture_index_by_file_name = {
                    entry.texture_file_name: entry.texture_index
                    for entry in (texture_plan.entries if texture_plan is not None else ())
                }
                gpl_bytes = PatchGPLAppendSubmesh(
                    gpl_bytes, model, parsed, texture_index_by_file_name,
                )
            gpl_result = GPLBuildResult(
                gpl_bytes=gpl_bytes,
                pos_gpl_offsets=_gpl_pos_offsets_from_bytes(gpl_bytes),
            )
        else:
            gpl_result = BuildGPLMeshData(parsed)
    else:
        gpl_bytes = CloneGPL(source_model_offset, source_model_length)
        gpl_result = GPLBuildResult(
            gpl_bytes=gpl_bytes,
            pos_gpl_offsets=_gpl_pos_offsets_from_bytes(gpl_bytes),
        )

    # BuildACTBoneHierarchy (PLAN_AddBones.md Phase 2) picks the clone route
    # (CloneACT + _apply_root_scale_patch + _apply_geo_id_patches, both
    # ACT-section-relative so they stay correct across hammerspace
    # relocation) or the rebuild route (BoneHierarchyEdited present -- new
    # leaf bones change the section's length, so it can no longer be a fixed
    # verbatim clone) on its own.
    act_bytes = BuildACTBoneHierarchy(data, source_model_offset, source_model_length)
    _act_before = CloneACT(source_model_offset, source_model_length)
    root_scale_applied = bool(_act_before) and (
        _apply_root_scale_patch(_act_before, data, source_model_offset) != _act_before
    )
    if modes.tex == 'build':
        tex_bytes = BuildTEX(parsed, texture_plan)
    else:
        tex_bytes = CloneTEX(source_model_offset, source_model_length)
    if modes.skn == 'build':
        skn_bytes = BuildSKNSkinningData(parsed, gpl_result)
    else:
        skn_bytes = CloneSKN(source_model_offset, source_model_length)
    # Bind-pose vertex scaling (PLAN_BoneScaling): apply the model's
    # RootBoneScaleEdited factors to the SKN bind-pose data. Covers both build
    # mode (freshly built SKN) and clone mode (verbatim SKN clone) since both
    # produce the same SKN section layout. No-op when the model has no
    # non-uniform scale.
    bind_pose_factors = _root_scale.resolve_bind_pose_scale(model)
    if bind_pose_factors is not None and skn_bytes:
        skn_bytes = _scale_skn_bind_pose(skn_bytes, bind_pose_factors)
        _slogger.info(
            f'[SKN] bind-pose scaled by {bind_pose_factors} '
            f'(RootBoneScaleEdited)',
            source='hammerspace.main',
        )
    parsed_trailing_sections = getattr(parsed, 'trailing_sections', None) or []
    if parsed_trailing_sections:
        trailing_bytes = b''.join(section.data for section in parsed_trailing_sections)
        original_trailing_offset = min(
            (section.original_ptr for section in parsed_trailing_sections if section.original_ptr),
            default=0,
        )
    else:
        trailing_bytes, original_trailing_offset = CloneTrailingSections(
            source_model_offset,
            source_model_length,
        )
    original_header = CloneHEADER(source_model_offset)
    inner_block = BuildHEADERModelBlock(
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
    report = _build_validation_report(
        inner_block, source_model_length, modes, section_sizes
    )
    report['root_scale_applied'] = root_scale_applied
    if archive_layout is not None:
        block, archive_report = rebuild_archive_container(
            route_container,
            route_prefix_size,
            inner_block,
            layout=archive_layout,
        )
        report.update(archive_report)
        _slogger.info(
            f'[Container] archive slot {archive_report["archive_member_slot"]} '
            f'{archive_report["archive_member_original_size"]:,} -> '
            f'{archive_report["archive_member_new_size"]:,} bytes; '
            f'{archive_report["archive_slots_shifted"]} later slot(s) shifted by '
            f'{archive_report["archive_size_delta"]:+,}; archive '
            f'{archive_report["archive_original_size"]:,} -> '
            f'{archive_report["archive_new_size"]:,} bytes',
            source='hammerspace.main',
        )
    else:
        block = route_prefix + inner_block
    if route_prefix or archive_layout is not None:
        report['container_prefix_size'] = route_prefix_size
        report['inner_assembled_size'] = len(inner_block)
        report['inner_original_size'] = source_model_length
        report['assembled_size'] = len(block)
        report['original_size'] = original_length
        report['size_delta'] = len(block) - original_length
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

    current_offset, current_length = hh.readOutputDolEntry(chunk_number, file_index)
    replacing_hammerspace_block = current_offset >= hh.BASE_SIZE
    if replacing_hammerspace_block:
        _slogger.info(
            f'Model already in hammerspace at 0x{current_offset:08X}; '
            'keeping the old route live until replacement commits',
            source='hammerspace.main',
        )

    # Live hammerspace blocks can end in zero padding, which the zero-byte scan
    # would otherwise treat as free. Placing the new block over the old block's
    # zero tail let the zeroRange below wipe the new block's header.
    reserved_ranges = list(hh.routedHammerspaceRanges())
    if replacing_hammerspace_block:
        reserved_ranges.append((current_offset, current_length))
    new_offset = hh.findFreeMemoryChunk(len(build.block), reserved_ranges=reserved_ranges)
    if new_offset == -1:
        if not hh.ensureOutputDat():
            raise RuntimeError('Unable to prepare output dt_na.dat')
        current_size = os.path.getsize(hh.OUTPUT_DAT)
        next_region_start = (current_size + hh.HS_ALIGN_BYTES - 1) & ~(hh.HS_ALIGN_BYTES - 1)
        required_size = next_region_start + len(build.block) + hh.HS_BUFFER_BYTES
        if not hh.ensureOutputDat(required_size):
            raise RuntimeError('Unable to prepare output dt_na.dat')
        new_offset = hh.findFreeMemoryChunk(len(build.block), reserved_ranges=reserved_ranges)
        if new_offset == -1:
            raise RuntimeError('No contiguous hammerspace region found after expansion')

    if replacing_hammerspace_block and (
        new_offset < current_offset + current_length
        and current_offset < new_offset + len(build.block)
    ):
        raise RuntimeError(
            f'new hammerspace block 0x{new_offset:08X}+{len(build.block):,} overlaps the '
            f'live block 0x{current_offset:08X}+{current_length:,} it replaces; refusing to '
            'write, because zeroing the old block would corrupt the new one'
        )

    shared_entries = hh.findSharedEntries(chunk_number, file_index)
    hh.writeModelBlock(build.block, new_offset)
    hh.patchDolEntry(chunk_number, file_index, new_offset, len(build.block))
    for shared_chunk, shared_index in shared_entries:
        hh.patchDolEntry(shared_chunk, shared_index, new_offset, len(build.block))

    hh.patchFstFileSize(os.path.getsize(hh.OUTPUT_DAT))
    hh.zeroOriginalModel(chunk_number, file_index)
    if replacing_hammerspace_block and current_offset != new_offset:
        hh.zeroRange(current_offset, current_length)
        _slogger.info(
            f'Hammerspace Log: Replaced | Model: {model_name} | '
            f'Old address: 0x{current_offset:08X} | '
            f'Old size: {current_length / (1024 * 1024):.2f} MB',
            source='hammerspace.main',
        )
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
    _parser.add_argument('--texture-file', default=None,
                         help='Absolute path to a .png file for single-texture replacement')
    _parser.add_argument('--texture-index', type=int, default=None,
                         help='TextureDescriptor index targeted by --texture-file')
    _args = _parser.parse_args()

    if (_args.texture_file is None) != (_args.texture_index is None):
        _parser.error('--texture-file and --texture-index must be used together')

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

    _tex_png_overrides = None
    if _args.texture_file is not None:
        _tex_png_overrides = {_args.texture_index: _args.texture_file}
        if _args.tex != 'build':
            _args.tex = 'build'

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
        _build = BuildModelBlock(_data, _modes, sluggie_path=_args.sluggies_path,
                                 tex_png_overrides=_tex_png_overrides)
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
