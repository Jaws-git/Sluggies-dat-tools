"""A synthetic donor model: a `.sluggie` plus the `dt_na.dat` block it describes.

Tests must not read production data -- `1_Input/dt_na.dat` and the
`2_Output_Models/*.sluggie` working exports are gitignored, so a test that
reads them skips in CI and rots silently on the one machine that runs it (see
`.clinerules`). This module stands in for the real Mario export that the
end-to-end `BuildModelBlock` tests used to open.

The donor is a whole, self-consistent model, not a stub:

- **GPL**: two submeshes -- submesh 0 skinned (`VertexBufferCompCount` 6),
  submesh 1 rigid (`CompCount` 3) and owned by a bone -- each carrying the
  canonical six display states (two texture layers, type 4, type 3, type 6,
  and a type 7 that names the surface), so `sm0_ds5` and `sm1_ds5` resolve
  the way the custom-submesh template sources expect.
- **ACT**: a nine-bone skeleton with a root, a spine with three children, a
  left/right mirror pair, the rigid owner bone, and several free bones for a
  custom submesh to host on.
- **TEX**: two small CMPR textures.
- **SKN**: one SK1 and one SKAcc covering submesh 0's vertices.

The `.sluggie` and the block are built together so they cannot drift: the
block's GPL section *is* `BuildGPLMeshData`'s output for this `.sluggie`, and
the `.sluggie`'s absolute `*Offset` fields are filled in from where the
sections actually land.

Use it through :func:`donor_environment`, which writes both files to a temp
directory and points `HammerspaceHelper` at them.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import pathlib
import struct
import sys
import tempfile
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for _path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import act_rebuild  # noqa: E402
import HammerspaceHelper as hh  # noqa: E402
import HammerspaceMain as main  # noqa: E402

# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------

CHUNK_NUMBER = 18
FILE_INDEX = 0
#: Where the donor block sits in the synthetic INPUT dat. Well below
#: ``hh.BASE_SIZE`` so it reads as a vanilla (not hammerspace-resident) donor.
MODEL_OFFSET = 0x2000
#: The DOL entry holds exactly this model, so there is no container prefix.
DOL_ENTRY_OFFSET = MODEL_OFFSET

# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

#: Submesh 0 is skinned: 6 int16 components per vertex (quantize format 3,
#: which `binfmt.comp_size` reads as 2 bytes per component).
SKINNED_COMP_COUNT = 6
SKINNED_QUANTIZE_INFO = 0x30
SKINNED_VERTEX_COUNT = 4
SKINNED_VERTEX_STRIDE = SKINNED_COMP_COUNT * 2
#: The SK1 writes vertices 0..2 directly; the SKAcc accumulates onto vertex 3.
#: They must not overlap, or the donor's memClr range (which covers exactly
#: the accumulation-only vertices) would collapse to empty.
SK1_VERTEX_COUNT = 3
SK_ACC_VERTEX = 3

#: Submesh 1 is rigid: 3 int16 components, shift 11 (divisor 2048) -- the
#: same quantization a real rigid donor submesh uses.
RIGID_COMP_COUNT = 3
RIGID_QUANTIZE_INFO = 59
RIGID_VERTEX_COUNT = 8

#: Bone ids. Bone 5 owns the rigid submesh; bones 2, 4, 6, 7, 8 are free
#: (GeoIdRaw 0xFFFF) and not referenced by SKN, so a custom submesh can host
#: on them. Bone 3 drives skinning.
BONE_PARENTS = {0: None, 1: 0, 2: 1, 3: 1, 4: 1, 5: 2, 6: 3, 7: 4, 8: 0}
RIGID_OWNER_BONE = 5
RIGID_OWNER_SUBMESH = 1
SKINNED_BONE = 3
SKN_ACC_BONE = 6
BONE_MIRRORS = {0: (0, 3), 1: (1, 3), 2: (2, 3), 3: (4, 1), 4: (3, 2),
                5: (5, 3), 6: (7, 1), 7: (6, 2), 8: (8, 3)}
BONE_TRACK_IDS = {0: 0, 1: 4, 2: 7, 3: 11, 4: 12, 5: 0xFFFF,
                  6: 21, 7: 22, 8: 0xFFFF}
BONE_SRT_TYPE = 0xC

ACT_HEADER_SIZE = act_rebuild.HEADER_SIZE
BONE_RECORD_SIZE = act_rebuild.BONE_RECORD_SIZE
#: Offset of a bone record's ``geoFileId`` u16 within the record.
BONE_GEO_ID_FIELD = 0x14

#: The canonical six-record display-state list a rigid custom submesh
#: template is built from: two texture layers, type 4, type 3, type 6 and the
#: type 7 that carries the surface's shader mode. Index 5 is the surface, so
#: the surface ids come out as ``sm0_ds5`` / ``sm1_ds5``.
DISPLAY_STATE_TEMPLATE = [
    (1, '000008', '11110000'),
    (1, '000000', '11002001'),
    (4, '000000', 'ffffff10'),
    (3, '000000', '00002808'),
    (6, '010000', '00000374'),
    (7, '320064', 'Spec'),
]
SURFACE_STATE_INDEX = 5
#: A `derived:` template source only accepts a plain `Spec` Type-7 surface,
#: so submesh 0 (the one `derived:sm0_ds5` reads) keeps that shader mode.
#: The Type-3 setting above declares position + texture0 + texture1, each as
#: a one-byte index, so every draw command carries three index bytes per
#: vertex and each submesh needs the two UV channels the two Type-1 texture
#: layers name.
DRAW_ATTRIBUTE_COUNT = 3
UV_CHANNEL_COUNT = 2
UV_COMP_COUNT = 2
UV_QUANTIZE_INFO = 0x30
PALETTE_NAME = 'synthetic'

TEXTURE_COUNT = 2
#: 4x4 CMPR: one 8-byte block per texture.
TEXTURE_WIDTH = 4
TEXTURE_HEIGHT = 4
TEXTURE_FORMAT = 0xE
TEXTURE_PAYLOAD_LENGTH = 8


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def _align32(value: int) -> int:
    return (value + 31) & ~31


# --------------------------------------------------------------------------
# The .sluggie
# --------------------------------------------------------------------------

def _display_states(submesh_index: int) -> list[dict]:
    states = []
    for index, (state_id, pad, shader_mode) in enumerate(DISPLAY_STATE_TEMPLATE):
        state = {
            'DisplayStateId': state_id,
            'DisplayStatePadBytes': pad,
            'ShaderMode': shader_mode,
            'PrimListLength': 0,
            'PrimListData': '',
            'PrimListPtrFieldOffset': '0x0',
            'PrimListSizeFieldOffset': '0x0',
            'PrimListAbsoluteOffset': '0x0',
            'ShaderModeFieldOffset': '0x0',
        }
        if index == SURFACE_STATE_INDEX:
            state['SurfaceId'] = f'sm{submesh_index}_ds{index}'
        states.append(state)
    return states


def _drawing_state(states: list[dict], prim_list: bytes) -> None:
    """Give the surface state a real primitive list -- the one record that
    actually draws (a template source needs `PrimListLength > 0` to be
    eligible)."""
    states[SURFACE_STATE_INDEX]['PrimListLength'] = len(prim_list)
    states[SURFACE_STATE_INDEX]['PrimListData'] = _b64(prim_list)


def _draw_command(vertex_count: int) -> bytes:
    """A single GX triangle-fan draw command over `vertex_count` vertices,
    padded to 32 bytes the way the GPL builder emits them. Each vertex
    carries one index byte per attribute the Type-3 setting declares."""
    body = struct.pack('>BH', 0x98, vertex_count)
    for vertex in range(vertex_count):
        body += bytes([vertex] * DRAW_ATTRIBUTE_COUNT)
    return body.ljust(_align32(len(body)), b'\x00')


def _uv_channels(vertex_count: int, faces_data: bytes) -> list[dict]:
    """One UV per vertex on each channel, with the face list reusing the
    position indices -- the simplest mapping that keeps every UV referenced."""
    uvs = struct.pack(
        f'>{vertex_count * UV_COMP_COUNT}h',
        *(value for vertex in range(vertex_count) for value in (vertex * 64, vertex * 32)),
    )
    return [
        {
            'UVChannelIndex': channel,
            'PaletteName': PALETTE_NAME,
            'TextureIndex': channel,
            'WrapS': 0,
            'WrapT': 0,
            'UVChannelData': _b64(uvs),
            'UVFacesData': _b64(faces_data),
            'UVChannelCompCount': UV_COMP_COUNT,
            'UVChannelQuantizeInfo': UV_QUANTIZE_INFO,
            'UVChannelOffset': '0x0',
            'UVDataPtrFieldOffset': '0x0',
            'UVCountFieldOffset': '0x0',
        }
        for channel in range(UV_CHANNEL_COUNT)
    ]


def _skinned_vertex_data() -> bytes:
    values = []
    for index in range(SKINNED_VERTEX_COUNT):
        values += [100 * index, 200 * index, 300 * index, 0, 0, 2048]
    return struct.pack(f'>{len(values)}h', *values)


def _rigid_vertex_data() -> bytes:
    values = []
    for corner in range(RIGID_VERTEX_COUNT):
        values += [205 if corner & 4 else -205,
                   205 if corner & 2 else -205,
                   205 if corner & 1 else -205]
    return struct.pack(f'>{len(values)}h', *values)


def _faces(vertex_count: int) -> tuple[int, bytes]:
    indices = []
    for corner in range(1, vertex_count - 1):
        indices += [0, corner, corner + 1]
    return len(indices) // 3, struct.pack(f'>{len(indices)}H', *indices)


def _texture_descriptors() -> list[dict]:
    return [
        {
            'TextureIndex': index,
            'TextureFileName': f'{index}.png',
            'Width': TEXTURE_WIDTH,
            'Height': TEXTURE_HEIGHT,
            'Format': TEXTURE_FORMAT,
            'PaletteEntries': 0,
            'PaletteFormat': 0,
            'EdgeLODEnable': False,
            'MinLOD': 0.0,
            'MaxLOD': 0.0,
            'Unpacked': 0,
            'ImageDataOffset': '0x0',
            'ImageDataLength': TEXTURE_PAYLOAD_LENGTH,
            'TextureDescriptorOffset': '0x0',
        }
        for index in range(TEXTURE_COUNT)
    ]


def _bone_hierarchy() -> list[dict]:
    bones = []
    for bone_id, parent_id in BONE_PARENTS.items():
        geo_id_raw = RIGID_OWNER_SUBMESH if bone_id == RIGID_OWNER_BONE else 0xFFFF
        bones.append({
            'BoneId': bone_id,
            'GeoId': geo_id_raw,
            'GeoIdRaw': geo_id_raw,
            'GeoIdFieldOffset': '0x0',   # filled in once the ACT is placed
            'ParentBoneId': parent_id,
            'Skinned': geo_id_raw == 0xFFFF,
            'TrackId': BONE_TRACK_IDS[bone_id],
            'MirrorBoneId': BONE_MIRRORS[bone_id][0],
            'MirrorRole': BONE_MIRRORS[bone_id][1],
            'SRTType': BONE_SRT_TYPE,
            'SRTOffset': '0x0',          # filled in once the ACT is placed
            'DrawPriority': 0,
            'InheritTransform': True,
            'Translation': [0.0, 0.1 * bone_id, 0.0],
            'Scale': [1.0, 1.0, 1.0],
            'Quaternion': [1.0, 0.0, 0.0, 0.0],
            'HeadPosition': [0.0, 0.0, 0.0],
            'VertexInfluences': [],
        })
    return bones


def build_sluggie() -> dict:
    """The donor `.sluggie` dict, with placeholder absolute offsets.

    :func:`build_donor` fills the offsets in once the block is laid out."""
    skinned_faces_count, skinned_faces = _faces(SKINNED_VERTEX_COUNT)
    rigid_faces_count, rigid_faces = _faces(RIGID_VERTEX_COUNT)

    skinned_states = _display_states(0)
    _drawing_state(skinned_states, _draw_command(SKINNED_VERTEX_COUNT))
    rigid_states = _display_states(1)
    _drawing_state(rigid_states, _draw_command(RIGID_VERTEX_COUNT))

    return {'SluggiesModel': {
        'ChunkNumber': CHUNK_NUMBER,
        'FileIndex': FILE_INDEX,
        'ModelOffset': f'0x{MODEL_OFFSET:x}',
        'ModelLength': 0,               # filled in by build_donor
        'UseBase64': True,
        'GPLUserDataLength': 0,
        'TextureDescriptors': _texture_descriptors(),
        'Submeshes': [
            {
                'MeshName': 'body',
                'FacesCount': skinned_faces_count,
                'FacesData': _b64(skinned_faces),
                'VertexBuffer': {
                    'VertexBufferData': _b64(_skinned_vertex_data()),
                    'VertexBufferCompCount': SKINNED_COMP_COUNT,
                    'VertexBufferQuantizeInfo': SKINNED_QUANTIZE_INFO,
                    'VertexBufferOffset': '0x0',
                },
                'UVChannels': _uv_channels(SKINNED_VERTEX_COUNT, skinned_faces),
                'ColorChannels': [],
                'DisplayStates': skinned_states,
            },
            {
                'MeshName': 'head',
                'FacesCount': rigid_faces_count,
                'FacesData': _b64(rigid_faces),
                'VertexBuffer': {
                    'VertexBufferData': _b64(_rigid_vertex_data()),
                    'VertexBufferCompCount': RIGID_COMP_COUNT,
                    'VertexBufferQuantizeInfo': RIGID_QUANTIZE_INFO,
                    'VertexBufferOffset': '0x0',
                },
                'UVChannels': _uv_channels(RIGID_VERTEX_COUNT, rigid_faces),
                'ColorChannels': [],
                'DisplayStates': rigid_states,
            },
        ],
        'BoneHierarchy': _bone_hierarchy(),
        'SkinData': _skin_data(),
    }}


def _skin_data() -> dict:
    """One SK1 (bone 3 owns every submesh-0 vertex) and one SKAcc (bone 6
    accumulates onto vertex 0) -- the smallest shape that still exercises
    both SKN entry kinds."""
    bind_pose = struct.pack('>12f', *([0.0] * 12))
    return {
        'QuantizeInfo': SKINNED_QUANTIZE_INFO,
        'GplBaseOffset': '0x0',
        'SK1s': [{
            'BoneIndex': SKINNED_BONE,
            'VertexCnt': SK1_VERTEX_COUNT,
            'VertexOffset': 0,
            'BindPoseData': _b64(bind_pose),
            'GplVertexArrValue': 0,
        }],
        'SK2s': [],
        'SKAccs': [{
            'BoneIndex': SKN_ACC_BONE,
            'VertexCnt': 1,
            'BindPoseData': _b64(bind_pose),
            'DestIndexData': _b64(struct.pack('>H', SK_ACC_VERTEX)),
            'WeightData': _b64(bytes([0xFF])),
            'GplDestArrValue': 0,
        }],
        'FlushIndData': _b64(struct.pack('>H', SK_ACC_VERTEX)),
    }


# --------------------------------------------------------------------------
# The model block
# --------------------------------------------------------------------------

def _build_act_bytes() -> bytes:
    """Serialize the skeleton above, deriving every tree link from
    ``BONE_PARENTS``."""
    bone_ids = sorted(BONE_PARENTS)
    children = {bone_id: [] for bone_id in bone_ids}
    for bone_id in bone_ids:
        parent_id = BONE_PARENTS[bone_id]
        if parent_id is not None:
            children[parent_id].append(bone_id)

    def table_off(bone_id: int) -> int:
        return ACT_HEADER_SIZE + bone_id * BONE_RECORD_SIZE

    bones, srt_blobs = [], []
    for bone_id in bone_ids:
        parent_id = BONE_PARENTS[bone_id]
        siblings = children[parent_id] if parent_id is not None else [bone_id]
        position = siblings.index(bone_id)
        bones.append(act_rebuild.BoneRecord(
            orientation_ptr=1,  # rebuild_act_bytes recomputes the real pointer
            prev=table_off(siblings[position - 1]) if position else 0,
            next=table_off(siblings[position + 1]) if position + 1 < len(siblings) else 0,
            parent=table_off(parent_id) if parent_id is not None else 0,
            first_child=table_off(children[bone_id][0]) if children[bone_id] else 0,
            geo_file_id_raw=RIGID_OWNER_SUBMESH if bone_id == RIGID_OWNER_BONE else 0xFFFF,
            id=bone_id, inheritance=1, priority=0, pad_half=0,
        ))
        srt_blobs.append(act_rebuild.pack_srt_blob(
            BONE_SRT_TYPE, [1.0, 1.0, 1.0], [1.0, 0.0, 0.0, 0.0], [0.0, 0.1 * bone_id, 0.0],
        ))

    bone_count = len(bone_ids)
    track_payload = struct.pack(f'>{bone_count}H', *(BONE_TRACK_IDS[b] for b in bone_ids))
    track_payload += b'\x00' * (-len(track_payload) % 4)
    mirror_payload = bytes(byte for bone_id in bone_ids for byte in BONE_MIRRORS[bone_id])
    mirror_payload += b'\x00' * (-len(mirror_payload) % 4)
    user_data = [
        act_rebuild.UserDataDescriptor(
            kind=act_rebuild.KIND_TRACK, count=0, data_ptr=0x0C, payload=track_payload),
        act_rebuild.UserDataDescriptor(
            kind=act_rebuild.KIND_MIRROR, count=0, data_ptr=0x0C, payload=mirror_payload),
    ]
    name_gap = b'synthetic.gpl\x00\x00\x00'
    total_length = (
        ACT_HEADER_SIZE
        + bone_count * BONE_RECORD_SIZE
        + len(srt_blobs) * act_rebuild.SRT_RECORD_SIZE
        + len(name_gap)
        + sum(act_rebuild.DESCRIPTOR_HEADER_SIZE + len(d.payload) for d in user_data)
    )
    return act_rebuild.rebuild_act_bytes(act_rebuild.ACTParsed(
        version_num=0x7B7960, actor_id=0, bone_count=bone_count,
        tree_unknown=0, root_ptr=ACT_HEADER_SIZE, skin_file_id=0, pad16=0,
        bones=bones, srt_blobs=srt_blobs, name_gap=name_gap, tail_gap=b'',
        user_data=user_data, total_length=total_length,
    ))


def _build_tex_bytes() -> bytes:
    """A TEX section with `TEXTURE_COUNT` 4x4 CMPR textures, each payload on
    its own 32-byte boundary (F10)."""
    descriptor_table_end = 4 + TEXTURE_COUNT * 0x20
    data_start = _align32(descriptor_table_end)
    section = bytearray(data_start + TEXTURE_COUNT * 0x20)
    struct.pack_into('>HH', section, 0, TEXTURE_COUNT, 0)
    for index in range(TEXTURE_COUNT):
        image_ptr = data_start + index * 0x20
        descriptor = 4 + index * 0x20
        struct.pack_into('>I', section, descriptor, image_ptr)
        struct.pack_into('>HH', section, descriptor + 8, TEXTURE_WIDTH, TEXTURE_HEIGHT)
        section[descriptor + 0x17] = TEXTURE_FORMAT
        section[image_ptr:image_ptr + TEXTURE_PAYLOAD_LENGTH] = bytes([0xAA] * TEXTURE_PAYLOAD_LENGTH)
    return bytes(section)


def _build_skn_bytes() -> bytes:
    """One SK1 over every submesh-0 vertex plus one SKAcc onto vertex 0,
    matching :func:`_skin_data`."""
    stride = SKINNED_VERTEX_STRIDE
    struct_start = 0x24
    sk1_struct = struct_start
    acc_struct = struct_start + 0x48

    src_off = _align32(acc_struct + 0x48)
    acc_src_off = _align32(src_off + SK1_VERTEX_COUNT * stride)
    acc_dst_off = _align32(acc_src_off + stride)
    acc_wt_off = _align32(acc_dst_off + 2)
    flush_off = _align32(acc_wt_off + 1)
    section = bytearray(_align32(flush_off + 2))

    struct.pack_into('>H', section, 0x00, 1)   # SK1 count
    struct.pack_into('>H', section, 0x02, 0)   # SK2 count
    struct.pack_into('>H', section, 0x04, 1)   # SKAcc count
    section[0x06] = SKINNED_QUANTIZE_INFO
    struct.pack_into('>I', section, 0x08, sk1_struct)
    struct.pack_into('>I', section, 0x0C, 0)
    struct.pack_into('>I', section, 0x10, acc_struct)
    # memClr covers exactly the accumulation-only vertices (ModelFormat.
    # compute_mem_clear_range), which here is the single SKAcc vertex.
    struct.pack_into('>I', section, 0x14, SK_ACC_VERTEX * stride)
    struct.pack_into('>I', section, 0x18, _align32(stride))
    struct.pack_into('>I', section, 0x1C, flush_off)  # flush ptr
    struct.pack_into('>I', section, 0x20, 1)          # flush entry count

    struct.pack_into('>I', section, sk1_struct + 0x30, src_off)
    struct.pack_into('>I', section, sk1_struct + 0x34, 0)
    struct.pack_into('>H', section, sk1_struct + 0x42, SK1_VERTEX_COUNT)

    struct.pack_into('>I', section, acc_struct + 0x30, acc_src_off)
    struct.pack_into('>I', section, acc_struct + 0x34, acc_dst_off)
    struct.pack_into('>I', section, acc_struct + 0x38, 0)
    struct.pack_into('>I', section, acc_struct + 0x3C, acc_wt_off)
    struct.pack_into('>H', section, acc_struct + 0x42, 1)

    struct.pack_into('>H', section, acc_dst_off, SK_ACC_VERTEX)
    struct.pack_into('>H', section, flush_off, SK_ACC_VERTEX)
    section[acc_wt_off] = 0xFF
    return bytes(section)


def build_donor() -> tuple[dict, bytes, int]:
    """Return ``(sluggie_dict, dat_bytes, model_length)``.

    The GPL section is ``BuildGPLMeshData``'s own output for this `.sluggie`,
    so the block and the JSON describe the same model by construction. Every
    section starts on a 32-byte boundary (F10), and the `.sluggie`'s absolute
    `GeoIdFieldOffset` / `SRTOffset` fields are filled in from where the ACT
    actually lands.
    """
    data = build_sluggie()
    gpl_bytes = main.BuildGPLMeshData(main.ParseSluggie(data)).gpl_bytes
    act_bytes = _build_act_bytes()
    tex_bytes = _build_tex_bytes()
    skn_bytes = _build_skn_bytes()

    gpl_off = 0x20
    act_off = _align32(gpl_off + len(gpl_bytes))
    tex_off = _align32(act_off + len(act_bytes))
    skn_off = _align32(tex_off + len(tex_bytes))
    model_length = _align32(skn_off + len(skn_bytes))

    block = bytearray(model_length)
    struct.pack_into('>8I', block, 0, 0, gpl_off, act_off, tex_off, skn_off, 0, 0, 0)
    block[gpl_off:gpl_off + len(gpl_bytes)] = gpl_bytes
    block[act_off:act_off + len(act_bytes)] = act_bytes
    block[tex_off:tex_off + len(tex_bytes)] = tex_bytes
    block[skn_off:skn_off + len(skn_bytes)] = skn_bytes

    model = data['SluggiesModel']
    model['ModelLength'] = model_length
    act_absolute = MODEL_OFFSET + act_off
    srt_base = act_absolute + ACT_HEADER_SIZE + len(BONE_PARENTS) * BONE_RECORD_SIZE
    for bone in model['BoneHierarchy']:
        bone_id = int(bone['BoneId'])
        record = act_absolute + ACT_HEADER_SIZE + bone_id * BONE_RECORD_SIZE
        bone['GeoIdFieldOffset'] = f'0x{record + BONE_GEO_ID_FIELD:x}'
        bone['SRTOffset'] = f'0x{srt_base + bone_id * act_rebuild.SRT_RECORD_SIZE:x}'

    dat = bytearray(MODEL_OFFSET + model_length)
    dat[MODEL_OFFSET:MODEL_OFFSET + model_length] = block
    return data, bytes(dat), model_length


class DonorEnvironment:
    """Paths and data for one materialized donor."""

    def __init__(self, directory: pathlib.Path, data: dict, model_length: int):
        self.directory = directory
        self.data = data
        self.model_length = model_length
        self.sluggie_path = directory / 'synthetic.gpl.sluggie'
        self.input_dat = directory / 'dt_na.dat'
        self.output_dat = directory / 'out_dt_na.dat'
        self.model_offset = MODEL_OFFSET

    def reload(self) -> dict:
        """A fresh copy of the donor `.sluggie`, read back from disk the way
        a test that opens the real export would."""
        with self.sluggie_path.open('r', encoding='utf-8') as handle:
            return json.load(handle)


@contextlib.contextmanager
def donor_environment():
    """Materialize the donor and point `HammerspaceHelper` at it.

    Writes the `.sluggie` and a synthetic `dt_na.dat` into a temp directory,
    then patches `hh.INPUT_DAT` / `hh.OUTPUT_DAT` and `hh.readDolEntry` so the
    clone routes (ACT, TEX, SKN and the trailing tail are clone-only) read the
    donor block back out of it.
    """
    data, dat_bytes, model_length = build_donor()
    with tempfile.TemporaryDirectory() as temp_dir:
        env = DonorEnvironment(pathlib.Path(temp_dir), data, model_length)
        env.input_dat.write_bytes(dat_bytes)
        env.output_dat.write_bytes(dat_bytes)
        with env.sluggie_path.open('w', encoding='utf-8') as handle:
            json.dump(data, handle, indent=2)
        os.makedirs(env.directory / 'tex', exist_ok=True)
        with (
            mock.patch.object(hh, 'INPUT_DAT', str(env.input_dat)),
            mock.patch.object(hh, 'OUTPUT_DAT', str(env.output_dat)),
            mock.patch.object(
                hh, 'readDolEntry', return_value=(DOL_ENTRY_OFFSET, model_length)),
        ):
            yield env
