from __future__ import annotations

import argparse
import base64
import copy
import json
import struct
import sys
from dataclasses import replace
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
HAMMERSPACE_DIR = TOOLS_DIR / "Hammerspace"
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import HammerspaceMain as hammerspace
import drawlist


# Fields that record where a submesh's arrays physically live in the DONOR
# file. BuildGPLMeshData's "preserve source layout" fast path (see
# `_can_preserve_gpl_internal_layout` / `_can_preserve_gpl_source_layout` in
# HammerspaceMain.py) uses these to reuse a submesh's exact byte layout from
# the input file. Our appended submesh is a byte-for-byte *value* clone of an
# existing submesh, so it carries the SAME donor offsets as its template --
# if left in place, two submeshes would claim the same source position. We
# clear them on the clone so the preserve-layout fast path is disabled
# (falls back to full re-serialization for every submesh, donor ones
# included).
#
# Verified 2026-09-16 (in response to a real-Dolphin render report of subtle
# body-texture noise on this probe): disabling the fast path does NOT
# introduce any quantization/rounding drift. BuildGPLMeshData's full-rebuild
# path re-emits every donor submesh's position/normal/color/UV arrays and
# primitive lists by copying the SAME raw bytes ParseSluggie decoded from the
# .sluggie's base64 fields (no float round-trip, no re-quantization -- see
# `vertex_data = _decode(vb['VertexBufferData'], use_b64)` and its UV/color/
# normal/prim-list equivalents in HammerspaceMain.ParseSluggie/BuildGPLMeshData).
# A direct byte diff against the real Mario donor confirms this: submesh 0's
# position+normal buffer, both UV channels, the color channel, the separate
# normal buffer and every non-empty primitive list are byte-for-byte
# IDENTICAL in content between the fast-path build and the full-rebuild
# build -- only their offsets within the GPL section move (the whole section
# is larger and the header/GEO-descriptor region grows to fit the new
# submesh). Header count/format fields (vertex count, QuantizeInfo, CompCount,
# etc.) are likewise recomputed from the same unedited source fields either
# way, so they match too. So "no longer byte-identical to the original file"
# only ever meant "relocated within the file" (unavoidable once the GPL
# section grows by one submesh, even with a hypothetical per-submesh preserve
# path), never "content-mutated". The array CONTENT was not the cause of the
# observed Dolphin texture noise. The rebuilt GPL section LENGTH was, confirmed
# in Dolphin on 2026-09-16: it ended 8 bytes past a 32-byte boundary, which
# shifted every texture payload off alignment (PLAN_AddSubmesh.md finding
# F10). `HammerspaceMain.BuildHEADERModelBlock` now pads every section start
# to a 32-byte boundary itself (Phase 2 step 4), so this no longer needs a
# fixture-local workaround.
def _clear_source_layout_fields(clone: dict) -> None:
    clone["SubmeshOffset"] = "0x0"
    clone["PositionDataPtrFieldOffset"] = "0x0"
    clone["VertexCountFieldOffset"] = "0x0"
    vb = clone.get("VertexBuffer")
    if vb is not None:
        vb["VertexBufferOffset"] = "0x0"
    for uv in clone.get("UVChannels", []):
        uv["UVDataPtrFieldOffset"] = "0x0"
        uv["UVCountFieldOffset"] = "0x0"
        uv["UVChannelOffset"] = "0x0"
    for cc in clone.get("ColorChannels", []):
        cc["ColorDataPtrFieldOffset"] = "0x0"
        cc["ColorCountFieldOffset"] = "0x0"
        cc["ColorChannelOffset"] = "0x0"
    nb = clone.get("NormalBuffer")
    if nb is not None:
        nb["NormalDataPtrFieldOffset"] = "0x0"
        nb["NormalCountFieldOffset"] = "0x0"
        nb["NormalBufferOffset"] = "0x0"
    for ds in clone.get("DisplayStates", []):
        ds["ShaderModeFieldOffset"] = None


def _null_primitive_lists(clone: dict) -> None:
    """Zero every primitive-bearing display state's triangle data on *clone*.

    Control probe for the GX/TEV render-state-carryover theory (see
    PLAN_AddSubmesh.md's Phase 0 status note, "control probe attempt 2"):
    isolates whether the new submesh's *state-setting* commands (Type-1
    texture bind, Type-3/4/6/7) alone can affect a later frame's body draw,
    independent of whether any triangles are actually issued. Only the
    primitive list is cleared -- the display-state list itself, and every
    other state in it, is left exactly as cloned, so all its register-setting
    commands still execute normally; the submesh's `GeoId` ownership is left
    untouched by this function (the caller still patches it), so this does
    NOT reproduce the appended-but-unowned crash.
    """
    for ds in clone.get("DisplayStates", []):
        if int(ds.get("PrimListLength") or 0) > 0:
            ds["PrimListDataEdited"] = ""
            ds["PrimListLength"] = 0


def _filter_display_states(clone: dict, exclude_types: set[int]) -> None:
    """Drop every display state whose ``DisplayStateId`` is in *exclude_types*.

    Bisection probe for the "owned but undrawn" control's finding (see
    PLAN_AddSubmesh.md's Phase 0 status note) that the body-texture noise
    survives with zero triangles drawn -- so it's caused by one or more of
    the state-setting commands (Type-1 texture bind, Type-3/4/6/7) in the
    cloned display-state list. There is no separate state-count field to
    keep in sync (`HammerspaceMain.BuildGPLMeshData` derives it from
    ``len(sub.draw_states)``), so entries can simply be dropped from the
    list.
    """
    clone["DisplayStates"] = [
        ds for ds in clone.get("DisplayStates", [])
        if int(ds.get("DisplayStateId")) not in exclude_types
    ]


GX_FORMAT_S16 = 3


def _scale_clone_positions(clone: dict, factor: float, use_base64: bool) -> None:
    """Scale the clone's bone-local positions by *factor* about the host bone.

    Order probe marker (PLAN_AddSubmesh.md Phase 0 probe 2, U3). A byte-exact
    clone looks identical to its template, so a swap between the template and
    the clone would be invisible in game. Scaling only the clone makes every
    submesh identifiable on its bone. Normals are unchanged by a uniform scale.
    Only signed-16-bit ``CompCount 3`` positions are supported; a value that
    would leave the int16 range is rejected, never clamped.
    """
    if not factor > 0:
        raise ValueError(f"position scale must be positive, got {factor}")
    vb = clone.get("VertexBuffer") or {}
    comp_count = int(vb.get("VertexBufferCompCount", 0))
    quantize_info = int(vb.get("VertexBufferQuantizeInfo", 0))
    if comp_count != 3 or quantize_info >> 4 != GX_FORMAT_S16:
        raise ValueError(
            "position scaling needs a rigid int16 position buffer "
            f"(CompCount 3, format {GX_FORMAT_S16}); got CompCount {comp_count}, "
            f"QuantizeInfo {quantize_info}"
        )
    raw = hammerspace._decode(vb["VertexBufferData"], use_base64)
    if len(raw) % (2 * comp_count):
        raise ValueError(
            f"position buffer length {len(raw)} is not a whole number of int16 xyz records"
        )
    values = struct.unpack(f">{len(raw) // 2}h", raw)
    scaled = [round(value * factor) for value in values]
    out_of_range = [value for value in scaled if not -0x8000 <= value <= 0x7FFF]
    if out_of_range:
        raise ValueError(
            f"position scale {factor} pushes {len(out_of_range)} component(s) outside int16 "
            f"(for example {out_of_range[0]})"
        )
    packed = struct.pack(f">{len(scaled)}h", *scaled)
    vb["VertexBufferData"] = base64.b64encode(packed).decode("ascii") if use_base64 else list(packed)
    # The clone must stay on the full-serializer path with its own data; a
    # stale edited payload would take precedence or trigger the position-edit
    # patch path instead.
    vb.pop("VertexBufferDataEdited", None)


CUBE_TYPE3_STATE_ID = 3
# Cube faces as (outward normal, u axis, v axis) with u x v = normal, so the
# corners -u-v, +u-v, +u+v, -u+v run counter-clockwise seen from outside.
_CUBE_FACES = (
    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
)
_CUBE_UVS = ((0, 0), (1, 0), (1, 1), (0, 1))


def _decode_field(value, use_base64: bool) -> bytes:
    return hammerspace._decode(value, use_base64)


def _encode_field(data: bytes, use_base64: bool):
    return base64.b64encode(data).decode("ascii") if use_base64 else list(data)


def _type3_descriptors(setting: int) -> list[dict]:
    """Decode a Type-3 setting into the ordered attribute index layout."""
    descriptors = []
    for key, shift in drawlist._ATTR_BIT_SHIFT.items():
        mode = (setting >> shift) & 0b11
        if mode == 0b00:
            continue
        if mode == 0b01:
            raise ValueError(f"Type-3 setting 0x{setting:08X} uses a direct {key} attribute")
        descriptors.append({"key": key, "index_size": 1 if mode == 0b10 else 2})
    return descriptors


def _s16_unit(quantize_info: int, what: str) -> int:
    if quantize_info >> 4 != GX_FORMAT_S16:
        raise ValueError(f"{what} must be int16 (format {GX_FORMAT_S16}); QuantizeInfo is {quantize_info}")
    unit = 1 << (quantize_info & 0x0F)
    if unit > 0x7FFF:
        raise ValueError(f"{what} QuantizeInfo {quantize_info} cannot represent 1.0 in int16")
    return unit


def _build_cube_faces(descriptors: list[dict]) -> list[list[dict]]:
    """Return 12 triangles in decoded (Blender, counter-clockwise) order."""
    faces = []
    for normal_index, (_normal, u_axis, v_axis) in enumerate(_CUBE_FACES):
        corners = []
        for corner, (su, sv) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
            point = [su * u_axis[k] + sv * v_axis[k] + _normal[k] for k in range(3)]
            position_index = (4 if point[0] > 0 else 0) | (2 if point[1] > 0 else 0) | (1 if point[2] > 0 else 0)
            vertex = {}
            for descriptor in descriptors:
                key = descriptor["key"]
                if key == "position":
                    vertex[key] = position_index
                elif key == "lighting":
                    vertex[key] = normal_index
                elif key in ("color0", "color1"):
                    vertex[key] = 0
                else:
                    vertex[key] = corner
            corners.append(vertex)
        faces.append([corners[0], corners[1], corners[2]])
        faces.append([corners[0], corners[2], corners[3]])
    return faces


def _replace_clone_with_cube(
    clone: dict, half_extent: float, display_state_index: int | None, use_base64: bool,
) -> dict:
    """Replace the clone's geometry with a 12-triangle cube (Phase 0 probe 4).

    Keeps the template's display-state list and attribute formats. The cube
    goes into one primitive-bearing state (default: the one with the largest
    primitive list); every other state's primitive list is emptied, which is
    how Phase 2 step 3 treats a `rigid:` template. Indices follow the Type-3
    layout in effect at that state. Positions are centred on the host bone
    (bone-local space); each face maps the whole texture (UV 0..1), and every
    UV channel gets the same coordinates, as UV1 mirrors UV0 for specular.
    Winding follows ``drawlist``: faces are built counter-clockwise seen from
    outside and ``encodeDrawList`` reverses them into the stored GX order.
    Returns metadata describing the cube.
    """
    if not half_extent > 0:
        raise ValueError(f"cube half extent must be positive, got {half_extent}")
    states = clone.get("DisplayStates") or []
    primitive_states = [
        index for index, ds in enumerate(states) if int(ds.get("PrimListLength") or 0) > 0
    ]
    if display_state_index is None:
        if not primitive_states:
            raise ValueError("template has no primitive-bearing display state for the cube")
        display_state_index = max(primitive_states, key=lambda i: int(states[i]["PrimListLength"]))
    if display_state_index not in primitive_states:
        raise ValueError(
            f"display state {display_state_index} does not draw primitives in the template; "
            f"choose one of {primitive_states}"
        )

    descriptors = None
    for ds in states[:display_state_index + 1]:
        if int(ds["DisplayStateId"]) == CUBE_TYPE3_STATE_ID:
            descriptors = _type3_descriptors(int(ds["ShaderMode"], 16))
    if not descriptors:
        raise ValueError(f"no Type-3 attribute layout is active at display state {display_state_index}")
    keys = [descriptor["key"] for descriptor in descriptors]
    if "position" not in keys:
        raise ValueError("active Type-3 layout has no position attribute")

    vb = clone["VertexBuffer"]
    if int(vb["VertexBufferCompCount"]) != 3:
        raise ValueError("cube positions need a rigid CompCount 3 position buffer")
    position_unit = _s16_unit(int(vb["VertexBufferQuantizeInfo"]), "position")
    extent = round(half_extent * position_unit)
    if not 0 < extent <= 0x7FFF:
        raise ValueError(f"cube half extent {half_extent} is outside the int16 position range")
    positions = []
    for index in range(8):
        positions += [
            extent if index & 4 else -extent,
            extent if index & 2 else -extent,
            extent if index & 1 else -extent,
        ]
    position_bytes = struct.pack(">24h", *positions)

    uv_channels = clone.get("UVChannels") or []
    for key in keys:
        if key.startswith("texture") and int(key[len("texture"):]) >= len(uv_channels):
            raise ValueError(f"active Type-3 layout uses {key}, but the template has no such UV channel")
    if "lighting" in keys and not clone.get("NormalBuffer"):
        raise ValueError("active Type-3 layout uses normals, but the template has no NormalBuffer")
    if any(key in keys for key in ("color0", "color1")) and not clone.get("ColorChannels"):
        raise ValueError("active Type-3 layout uses color, but the template has no color channel")

    faces = _build_cube_faces(descriptors)
    raw = drawlist.encodeDrawList(faces, descriptors) + b"\x00"
    primitive_bytes = raw + b"\x00" * ((-len(raw)) % 32)

    def face_table(key: str) -> bytes:
        return struct.pack(f">{len(faces) * 3}H", *(vertex.get(key, 0) for face in faces for vertex in face))

    vb["VertexBufferData"] = _encode_field(position_bytes, use_base64)
    vb["VertexBufferLength"] = len(position_bytes)
    vb.pop("VertexBufferDataEdited", None)

    nb = clone.get("NormalBuffer")
    if nb is not None:
        if int(nb["NormalBufferCompCount"]) != 3:
            raise ValueError("cube normals need a CompCount 3 normal buffer")
        normal_unit = _s16_unit(int(nb["NormalBufferQuantizeInfo"]), "normal")
        normal_bytes = struct.pack(
            ">18h", *(component * normal_unit for normal, _u, _v in _CUBE_FACES for component in normal)
        )
        nb["NormalBufferData"] = _encode_field(normal_bytes, use_base64)
        nb["NormalBufferLength"] = len(normal_bytes)
        nb["NormalFacesData"] = _encode_field(face_table("lighting"), use_base64)
        for key in ("NormalBufferDataEdited", "NormalFacesDataEdited"):
            nb.pop(key, None)

    for channel_index, uv in enumerate(uv_channels):
        if int(uv["UVChannelCompCount"]) != 2:
            raise ValueError(f"UV channel {channel_index} must have 2 components")
        uv_unit = _s16_unit(int(uv["UVChannelQuantizeInfo"]), f"UV channel {channel_index}")
        uv_bytes = struct.pack(">8h", *(value * uv_unit for pair in _CUBE_UVS for value in pair))
        uv["UVChannelData"] = _encode_field(uv_bytes, use_base64)
        uv["UVChannelLength"] = len(uv_bytes)
        uv["UVFacesData"] = _encode_field(face_table(f"texture{channel_index}"), use_base64)
        for key in ("UVChannelDataEdited", "UVFacesDataEdited"):
            uv.pop(key, None)

    for cc in clone.get("ColorChannels") or []:
        cc["ColorFacesData"] = _encode_field(face_table("color0"), use_base64)
        for key in ("ColorChannelDataEdited", "ColorFacesDataEdited"):
            cc.pop(key, None)

    original_texture_indices = clone.get("FaceTextureIndices")
    first_face = sum(int(ds.get("FaceCount") or 0) for ds in states[:display_state_index])
    texture_index = 0
    if original_texture_indices:
        table = _decode_field(original_texture_indices, use_base64)
        if len(table) >= 2 * (first_face + 1):
            texture_index = struct.unpack_from(">H", table, 2 * first_face)[0]
    clone["FacesCount"] = len(faces)
    clone["FacesData"] = _encode_field(face_table("position"), use_base64)
    clone["FaceTextureIndices"] = _encode_field(struct.pack(f">{len(faces)}H", *([texture_index] * len(faces))), use_base64)
    for key in ("FacesCountEdited", "FacesDataEdited", "FaceTextureIndicesEdited"):
        clone.pop(key, None)

    for index, ds in enumerate(states):
        ds.pop("PrimListDataEdited", None)
        if index == display_state_index:
            ds["PrimListData"] = _encode_field(primitive_bytes, use_base64)
            ds["PrimListLength"] = len(primitive_bytes)
            ds["FaceCount"] = len(faces)
            ds["VertexStreamLayout"] = descriptors
        elif int(ds.get("PrimListLength") or 0) > 0 or ds.get("PrimListData"):
            ds["PrimListData"] = ""
            ds["PrimListLength"] = 0
            ds["FaceCount"] = 0

    return {
        "HalfExtent": half_extent,
        "QuantizedHalfExtent": extent,
        "DisplayStateIndex": display_state_index,
        "SurfaceId": states[display_state_index].get("SurfaceId"),
        "FaceTextureIndex": texture_index,
        "VertexStreamLayout": descriptors,
        "PrimitiveListLength": len(primitive_bytes),
    }


def _find_template_submesh_index(submeshes: list[dict], template_submesh: int | str | None) -> int:
    if template_submesh is None:
        template_submesh = "head"
    if isinstance(template_submesh, int):
        index = template_submesh
        if index < 0 or index >= len(submeshes):
            raise ValueError(f"template submesh index {index} is outside the model")
        return index
    matches = [
        i for i, sub in enumerate(submeshes)
        if str(sub.get("MeshName", "")).lower() == str(template_submesh).lower()
    ]
    if not matches:
        raise ValueError(f"no submesh named {template_submesh!r} found for the template")
    return matches[0]


def _skn_used_bone_ids(skin_data: dict | None) -> set[int]:
    used: set[int] = set()
    if not skin_data:
        return used
    for entry in skin_data.get("SK1s", []) or []:
        used.add(int(entry["BoneIndex"]))
    for entry in skin_data.get("SK2s", []) or []:
        used.add(int(entry["BoneIndex1"]))
        used.add(int(entry["BoneIndex2"]))
    for entry in skin_data.get("SKAccs", []) or []:
        used.add(int(entry["BoneIndex"]))
    return used


def _bone_geo_id_raw(bone: dict) -> int:
    if bone.get("GeoIdRaw") is not None:
        return int(bone["GeoIdRaw"])
    if bone.get("Skinned"):
        return 0xFFFF
    return int(bone.get("GeoId", 0xFFFF))


def _select_host_bone(
    bone_hierarchy: list[dict],
    skn_used_ids: set[int],
    host_bone: int | None,
    allow_skinned_bone: bool,
) -> dict:
    if not bone_hierarchy:
        raise ValueError("model has no BoneHierarchy; cannot host a rigid submesh")

    by_id = {int(bone["BoneId"]): bone for bone in bone_hierarchy}

    if host_bone is not None:
        bone = by_id.get(host_bone)
        if bone is None:
            raise ValueError(f"bone {host_bone} does not exist in BoneHierarchy")
        raw = _bone_geo_id_raw(bone)
        if raw != 0xFFFF:
            raise ValueError(
                f"bone {host_bone} already owns submesh {raw}; "
                "cannot host another mesh (GeoId is a single uint16 per bone)"
            )
        if not allow_skinned_bone and host_bone in skn_used_ids:
            raise ValueError(
                f"bone {host_bone} drives skinning (referenced by SK1/SK2/SKAcc); "
                "pass --allow-skinned-bone to host a submesh on it anyway (probe 3, U2)"
            )
        return bone

    candidates = sorted(bone_hierarchy, key=lambda b: int(b["BoneId"]))
    eligible = [
        bone for bone in candidates
        if _bone_geo_id_raw(bone) == 0xFFFF
        and (allow_skinned_bone or int(bone["BoneId"]) not in skn_used_ids)
    ]
    if not eligible:
        raise ValueError(
            "no eligible host bone found: every mesh-free bone is SKN-used; "
            "pass --allow-skinned-bone to allow one (probe 3, U2)"
        )

    # F7: vanilla rigid-submesh owners are never root bones (the only root
    # owners are single-bone props). Prefer a non-root bone so the probe
    # actually exercises a bind-pose offset; fall back to a root bone only
    # when nothing else qualifies.
    non_root = [bone for bone in eligible if bone.get("ParentBoneId") is not None]
    return non_root[0] if non_root else eligible[0]


def prepare_fixture_data(
    source_data: dict,
    host_bone: int | None = None,
    template_submesh: int | str | None = None,
    allow_skinned_bone: bool = False,
    no_draw: bool = False,
    exclude_display_state_types: tuple[int, ...] = (),
    position_scale: float | None = None,
    cube_half_extent: float | None = None,
    cube_display_state: int | None = None,
) -> dict:
    """Build the U1 duplicate-submesh probe .sluggie payload (pure, no I/O).

    Deep-copies *source_data*, appends a byte-for-byte value clone of the
    template rigid submesh (default: the submesh named ``head``) as a new
    submesh at the end of ``Submeshes``, and records the chosen host bone in
    ``BoneHierarchy[i]['GeoIdEdited']`` plus an ``AddSubmeshFixture`` metadata
    block. Does not touch the ACT bytes -- that happens in ``build_fixture``
    as a post-processing step on the assembled block (see module docstring
    on ``_apply_add_submesh_geo_id_patch``), because hammerspace's ACT
    section is always cloned verbatim (`SectionModes.act` only supports
    ``'clone'``) and there is no existing hook that consumes
    ``GeoIdEdited`` in the hammerspace build path today.

    ``no_draw=True`` zeroes the clone's primitive-bearing display states
    (see ``_null_primitive_lists``) while keeping its ``GeoId`` ownership
    intact -- the "owned but undrawn" control probe for the render-state-
    carryover theory, distinct from ``build_fixture``'s ``skip_geo_id_patch``
    (which leaves the submesh unowned and, per the 2026-09-16 in-game test,
    crashes the game).

    ``exclude_display_state_types`` drops every display state whose
    ``DisplayStateId`` matches (see ``_filter_display_states``) -- the
    state-type bisection probe for narrowing down which of the cloned
    display states (Type-1 texture bind x2, Type-4, Type-3, Type-6, Type-7)
    is responsible for the render-state-carryover artifact confirmed by the
    ``no_draw`` control.

    ``position_scale`` scales only the clone's positions (see
    ``_scale_clone_positions``), so the order probe can tell every submesh
    apart in game. ``OrderBroken`` in the fixture metadata records whether the
    host bone ID is lower than an existing rigid owner's ID, which breaks the
    vanilla ascending owner order (F3).

    ``cube_half_extent`` replaces the clone's geometry with a 12-triangle
    cube (see ``_replace_clone_with_cube``), optionally drawn in
    ``cube_display_state``; this is the minimal-geometry probe 4.
    """
    data = copy.deepcopy(source_data)
    model = data["SluggiesModel"]
    submeshes = model.get("Submeshes")
    if not submeshes:
        raise ValueError("source model has no Submeshes")

    template_index = _find_template_submesh_index(submeshes, template_submesh)
    template = submeshes[template_index]
    template_vb = template.get("VertexBuffer") or {}
    if int(template_vb.get("VertexBufferCompCount", 0)) != 3:
        raise ValueError(
            f"template submesh {template_index} ({template.get('MeshName')!r}) is not rigid "
            f"(VertexBufferCompCount={template_vb.get('VertexBufferCompCount')}); "
            "only a rigid (CompCount 3) submesh can be cloned for a bone-attached probe"
        )

    clone = copy.deepcopy(template)
    _clear_source_layout_fields(clone)
    if exclude_display_state_types:
        _filter_display_states(clone, set(exclude_display_state_types))
    if no_draw:
        _null_primitive_lists(clone)
    if position_scale is not None and cube_half_extent is not None:
        raise ValueError("position scale and cube replacement are mutually exclusive")
    if cube_display_state is not None and cube_half_extent is None:
        raise ValueError("a cube display state needs a cube half extent")
    if position_scale is not None:
        _scale_clone_positions(clone, position_scale, bool(model.get("UseBase64", True)))
    cube_metadata = None
    if cube_half_extent is not None:
        cube_metadata = _replace_clone_with_cube(
            clone, cube_half_extent, cube_display_state, bool(model.get("UseBase64", True)),
        )
    new_submesh_index = len(submeshes)
    submeshes.append(clone)

    bone_hierarchy = model.get("BoneHierarchy") or []
    skn_used_ids = _skn_used_bone_ids(model.get("SkinData"))
    host_bone_record = _select_host_bone(bone_hierarchy, skn_used_ids, host_bone, allow_skinned_bone)
    host_bone_id = int(host_bone_record["BoneId"])
    skn_used_host_bone = host_bone_id in skn_used_ids

    rigid_owners = {
        _bone_geo_id_raw(bone): int(bone["BoneId"])
        for bone in bone_hierarchy
        if _bone_geo_id_raw(bone) != 0xFFFF
    }
    order_broken = any(owner_id > host_bone_id for owner_id in rigid_owners.values())

    for bone in model["BoneHierarchy"]:
        if int(bone["BoneId"]) == host_bone_id:
            bone["GeoIdEdited"] = new_submesh_index
            break

    model["UseHammerspace"] = True

    fixture_metadata = {
        "NewSubmeshIndex": new_submesh_index,
        "TemplateSubmeshIndex": template_index,
        "HostBoneId": host_bone_id,
        "SknUsedHostBone": skn_used_host_bone,
        "NoDraw": no_draw,
        "ExcludedDisplayStateTypes": sorted(exclude_display_state_types),
        "PositionScale": position_scale,
        "Cube": cube_metadata,
        "ExistingOwnerBones": {str(index): bone_id for index, bone_id in sorted(rigid_owners.items())},
        "OrderBroken": order_broken,
    }
    model["AddSubmeshFixture"] = fixture_metadata

    return data


def _apply_add_submesh_geo_id_patch(
    build: "hammerspace.ModelBlockBuild",
    source_model_offset: int,
    host_bone_id: int,
    geo_id_field_offset_hex: str,
    new_submesh_index: int,
) -> "hammerspace.ModelBlockBuild":
    """Patch ``GeoId = new_submesh_index`` onto the host bone in an assembled block.

    Follows the exact pattern ``_apply_root_scale_patch`` uses for
    ``RootBoneScaleEdited``: hammerspace's ACT section is always cloned
    verbatim (see ``_validate_section_modes`` -- ``ACT`` only supports
    ``'clone'``), so the donor's absolute ``GeoIdFieldOffset`` converted to an
    ACT-section-relative offset (via ``_act_section_absolute``) stays valid
    regardless of where the hammerspace block is relocated. There is no
    existing GeoId hook in the hammerspace build path (only the in-place
    patcher consumes ``GeoIdEdited`` today -- see
    ``InplacePatcher/patch_inplace.py``), so this function is this fixture
    script's own small ACT-byte patch, applied to the already-assembled
    ``build.block`` and re-validated with ``BlockValidator`` before use.
    """
    act_section_absolute = hammerspace._act_section_absolute(source_model_offset)
    if not act_section_absolute:
        raise ValueError("source model has no ACT section to patch")

    act_relative = int(geo_id_field_offset_hex, 16) - act_section_absolute
    if act_relative < 0:
        raise ValueError(
            f"bone {host_bone_id}: GeoIdFieldOffset 0x{int(geo_id_field_offset_hex, 16):X} "
            f"is before the ACT section start (0x{act_section_absolute:X}); the .sluggie's "
            "GeoIdFieldOffset metadata does not match this model's ACT layout"
        )

    prefix_size = int(build.validation_report.get("container_prefix_size", 0))
    block = build.block
    header = block[prefix_size:prefix_size + 0x20]
    if len(header) < 0x20:
        raise ValueError("assembled block is too short to contain a model header")
    act_block_off = struct.unpack_from(">I", header, 0x08)[0]
    if not act_block_off:
        raise ValueError("assembled block has no ACT section")
    act_start = prefix_size + act_block_off
    patch_off = act_start + act_relative
    if patch_off < act_start or patch_off + 2 > len(block):
        raise ValueError(
            f"bone {host_bone_id}: ACT-relative GeoId offset 0x{act_relative:X} "
            "falls outside the assembled ACT section"
        )

    current = struct.unpack_from(">H", block, patch_off)[0]
    if current != 0xFFFF:
        # Exports written before the export.py GeoIdFieldOffset fix (see that
        # file's extract_bone_hierarchy comment) recorded bl.absolute + 0x0C
        # instead of the real geoFileIdRaw field at bl.absolute + 0x14 -- an
        # 8-byte offset bug that predates this script. Older .sluggie files
        # on disk still carry the stale offset. Try the corrected location,
        # but only ever accept it if it actually reads the unowned sentinel;
        # otherwise fail rather than guess.
        fallback_off = patch_off + 8
        fallback_relative = act_relative + 8
        if fallback_off + 2 <= len(block) and struct.unpack_from(">H", block, fallback_off)[0] == 0xFFFF:
            hammerspace._slogger.warning(
                f"bone {host_bone_id}: GeoIdFieldOffset at ACT+0x{act_relative:X} reads "
                f"0x{current:04X}, not 0xFFFF; using ACT+0x{fallback_relative:X} instead "
                "(compensating for the pre-fix export.py off-by-8 GeoIdFieldOffset bug)",
                source="build_add_submesh_fixture",
            )
            patch_off = fallback_off
            act_relative = fallback_relative
            current = 0xFFFF
        else:
            raise ValueError(
                f"bone {host_bone_id}: GeoId field at ACT+0x{act_relative:X} reads "
                f"0x{current:04X}, expected the unowned sentinel 0xFFFF; refusing to "
                "overwrite an owned mesh"
            )

    patched = bytearray(block)
    struct.pack_into(">H", patched, patch_off, new_submesh_index)
    patched_bytes = bytes(patched)

    inner_block = patched_bytes[prefix_size:]
    original_length = build.validation_report.get("inner_original_size", build.original_length)
    report = hammerspace._build_validation_report(
        inner_block, original_length, build.section_modes, build.section_sizes,
    )
    if prefix_size:
        report["container_prefix_size"] = prefix_size
        report["inner_assembled_size"] = len(inner_block)
        report["inner_original_size"] = original_length
        report["assembled_size"] = len(patched_bytes)
        report["original_size"] = build.original_length
        report["size_delta"] = len(patched_bytes) - build.original_length

    hammerspace._slogger.info(
        f"[ACT] bone {host_bone_id} GeoId patched to submesh {new_submesh_index} "
        f"at ACT+0x{act_relative:X} (section-relative; stable across hammerspace relocation)",
        source="build_add_submesh_fixture",
    )

    return replace(build, block=patched_bytes, validation_report=report)


SECTION_ALIGNMENT = 32
_HEADER_SECTION_FIELDS = (
    ("GPL", 0x04), ("ACT", 0x08), ("TEX", 0x0C), ("SKN", 0x10),
    ("ptr6", 0x14), ("ptr7", 0x18), ("ptr8", 0x1C),
)


def section_alignment_facts(inner_block: bytes) -> dict:
    """Report section starts and texture/palette payload starts modulo 32.

    Offsets are relative to the inner model block (after any DOL-entry
    container prefix), which is how the survey of vanilla blocks measured
    them. ``misaligned`` lists every entry that is not 0 mod 32.
    """
    if len(inner_block) < 0x20:
        raise ValueError("block is too short to contain a model header")
    sections = {}
    for name, field in _HEADER_SECTION_FIELDS:
        pointer = struct.unpack_from(">I", inner_block, field)[0]
        if pointer:
            sections[name] = pointer
    textures = []
    tex = sections.get("TEX")
    if tex:
        count = struct.unpack_from(">H", inner_block, tex)[0]
        for index in range(count):
            descriptor = tex + 4 + index * 0x20
            if descriptor + 8 > len(inner_block):
                break
            image, palette = struct.unpack_from(">II", inner_block, descriptor)
            textures.append({
                "index": index,
                "image": tex + image if image else None,
                "palette": tex + palette if palette else None,
            })
    misaligned = [
        f"{name} section at +0x{pointer:X}"
        for name, pointer in sections.items()
        if pointer % SECTION_ALIGNMENT
    ]
    for texture in textures:
        for kind in ("image", "palette"):
            offset = texture[kind]
            if offset is not None and offset % SECTION_ALIGNMENT:
                misaligned.append(f"texture {texture['index']} {kind} at +0x{offset:X}")
    return {"sections": sections, "textures": textures, "misaligned": misaligned}


def _format_alignment_summary(facts: dict) -> str:
    sections = " ".join(
        f"{name}={pointer % SECTION_ALIGNMENT}" for name, pointer in facts["sections"].items()
    )
    images = " ".join(
        f"t{texture['index']}={texture['image'] % SECTION_ALIGNMENT}"
        for texture in facts["textures"]
        if texture["image"] is not None
    )
    return f"Section starts mod 32: {sections}\nTexture payloads mod 32: {images}"


def _format_cube_summary(cube: dict | None) -> str:
    if not cube:
        return "none"
    layout = ", ".join(f"{d['key']}:u{d['index_size'] * 8}" for d in cube["VertexStreamLayout"])
    return (
        f"half extent {cube['HalfExtent']:g} (int16 {cube['QuantizedHalfExtent']}), "
        f"display state {cube['DisplayStateIndex']} ({cube['SurfaceId']}), "
        f"face texture {cube['FaceTextureIndex']}, layout [{layout}], "
        f"primitive list {cube['PrimitiveListLength']} bytes"
    )


def build_fixture(
    source_path: Path,
    fixture_path: Path,
    host_bone: int | None,
    template_submesh: int | str | None,
    allow_skinned_bone: bool,
    write: bool,
    skip_geo_id_patch: bool = False,
    no_draw: bool = False,
    exclude_display_state_types: tuple[int, ...] = (),
    position_scale: float | None = None,
    cube_half_extent: float | None = None,
    cube_display_state: int | None = None,
) -> tuple["hammerspace.ModelBlockBuild", int | None]:
    with source_path.open("r", encoding="utf-8") as source_file:
        source_data = json.load(source_file)

    data = prepare_fixture_data(
        source_data, host_bone, template_submesh, allow_skinned_bone, no_draw,
        exclude_display_state_types, position_scale, cube_half_extent, cube_display_state,
    )
    model = data["SluggiesModel"]
    fixture_meta = model["AddSubmeshFixture"]
    host_bone_id = int(fixture_meta["HostBoneId"])
    new_submesh_index = int(fixture_meta["NewSubmeshIndex"])

    host_bone_record = next(
        bone for bone in model["BoneHierarchy"] if int(bone["BoneId"]) == host_bone_id
    )
    geo_id_field_offset = host_bone_record.get("GeoIdFieldOffset")
    if not geo_id_field_offset:
        raise ValueError(
            f"bone {host_bone_id} is missing GeoIdFieldOffset metadata; "
            "re-export the model with the latest SluggiesTools export.py"
        )

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    with fixture_path.open("w", encoding="utf-8", newline="\n") as fixture_file:
        json.dump(data, fixture_file, indent=2)
        fixture_file.write("\n")

    modes = hammerspace.SectionModes(
        gpl="build",
        act="clone",
        tex="clone",
        skn="clone",
        trailing="clone",
    )
    build = hammerspace.BuildModelBlock(data, modes, sluggie_path=fixture_path)
    if not build.validation_report["valid"]:
        raise ValueError(
            "fixture model block failed validation before the GeoId patch: "
            + "; ".join(build.validation_report["errors"])
        )

    if skip_geo_id_patch:
        # Control probe for the GX/TEV render-state-carryover theory (see
        # PLAN_AddSubmesh.md's Phase 0 status note): the new submesh is still
        # appended to GPL (same descriptor count, same section growth, same
        # relocation as the normal probe), but no bone's GeoId is patched, so
        # nothing ever references submesh `new_submesh_index` and its display
        # states/primitive lists never execute. If the reported texture noise
        # still occurs with this fixture, the cause is structural (GPL growth
        # / descriptor-table change), not the new submesh's own GX draw
        # stream running.
        hammerspace._slogger.info(
            f"[ACT] GeoId patch skipped (--skip-geo-id-patch): submesh "
            f"{new_submesh_index} is appended but unowned; bone {host_bone_id} "
            "stays 0xFFFF",
            source="build_add_submesh_fixture",
        )
    else:
        source_model_offset = int(model.get("ModelOffset", 0), 16) if isinstance(model.get("ModelOffset"), str) else int(model.get("ModelOffset", 0))
        build = _apply_add_submesh_geo_id_patch(
            build, source_model_offset, host_bone_id, geo_id_field_offset, new_submesh_index,
        )
        if not build.validation_report["valid"]:
            raise ValueError(
                "fixture model block failed validation after the GeoId patch: "
                + "; ".join(build.validation_report["errors"])
            )

    prefix_size = int(build.validation_report.get("container_prefix_size", 0))
    alignment = section_alignment_facts(build.block[prefix_size:])
    build.validation_report["section_alignment"] = alignment
    if alignment["misaligned"]:
        raise ValueError(
            "build has entries off a 32-byte boundary (F10): "
            + "; ".join(alignment["misaligned"])
        )

    output_offset = None
    if write:
        output_offset = hammerspace.WriteModelBlock(build, fixture_path.name)

    skn_used = bool(fixture_meta["SknUsedHostBone"])
    print(
        f"Fixture: {fixture_path}\n"
        f"New submesh: index {new_submesh_index} (template submesh "
        f"{fixture_meta['TemplateSubmeshIndex']}, no_draw={no_draw}, "
        f"excluded_display_state_types={fixture_meta['ExcludedDisplayStateTypes']})\n"
        f"Host bone: {host_bone_id} (SKN-used={skn_used}, "
        f"GeoId patch {'SKIPPED (control probe)' if skip_geo_id_patch else 'applied'})\n"
        f"Existing rigid owners (submesh: bone): {fixture_meta['ExistingOwnerBones']}; "
        f"ascending owner order broken: {fixture_meta['OrderBroken']}\n"
        f"Clone position scale: {position_scale if position_scale is not None else 'none'}\n"
        f"Cube: {_format_cube_summary(fixture_meta['Cube'])}\n"
        f"{_format_alignment_summary(alignment)}\n"
        f"Block: {len(build.block)} bytes, delta {build.validation_report['size_delta']:+d}, "
        f"valid={build.validation_report['valid']}"
    )
    if output_offset is not None:
        print(f"Installed at output DAT offset 0x{output_offset:08X}")
    else:
        print("Dry run only; output DAT/DOL/FST were not modified")
    return build, output_offset


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the Phase 0 duplicate-submesh (U1) hammerspace fixture."
    )
    parser.add_argument("source", type=Path, help="Source .sluggie path")
    parser.add_argument("--output", type=Path, help="Generated fixture .sluggie path")
    parser.add_argument(
        "--host-bone", type=int, default=None,
        help="Bone ID to give the new submesh; default auto-picks the first eligible bone",
    )
    parser.add_argument(
        "--template-submesh", default="head",
        help="Template submesh name or integer index to clone (default: 'head')",
    )
    parser.add_argument(
        "--allow-skinned-bone", action="store_true",
        help="Permit picking a bone that SKN references (probe 3, U2)",
    )
    parser.add_argument("--write", action="store_true", help="Install into output DAT/DOL/FST")
    parser.add_argument(
        "--skip-geo-id-patch", action="store_true",
        help=(
            "Control probe: append the submesh but don't patch any bone's GeoId, "
            "so it's never referenced/drawn. WARNING: the 2026-09-16 in-game test "
            "found this crashes the game (an appended-but-unowned submesh is fatal, "
            "not merely invisible) -- kept for the record, not recommended to rerun."
        ),
    )
    parser.add_argument(
        "--no-draw", action="store_true",
        help=(
            "Control probe: keep the new submesh owned (GeoId patched normally) but "
            "zero its primitive-bearing display states, so its state-setting commands "
            "(texture bind/TEV) still execute but no triangles are drawn. Isolates "
            "whether reported render artifacts come from state-setting alone."
        ),
    )
    parser.add_argument(
        "--exclude-display-state-types", default="",
        metavar="ID[,ID...]",
        help=(
            "Bisection probe: drop display states with these DisplayStateId values "
            "(comma-separated ints, e.g. '1' or '1,4') from the cloned submesh, to "
            "narrow down which state type causes the render-state-carryover artifact "
            "confirmed by --no-draw. Combine with --no-draw to keep isolating from "
            "the triangle draw."
        ),
    )
    parser.add_argument(
        "--position-scale", type=float, default=None, metavar="FACTOR",
        help=(
            "Order probe marker (probe 2, U3): scale only the cloned submesh's positions "
            "about its host bone, e.g. 0.5, so the clone can't be mistaken for its template "
            "in game."
        ),
    )
    parser.add_argument(
        "--cube", type=float, default=None, metavar="HALF_EXTENT",
        help=(
            "Minimal-geometry probe 4: replace the clone's geometry with a 12-triangle cube of "
            "this half extent in bone-local units (e.g. 0.1), keeping the template's display "
            "states and texture bindings."
        ),
    )
    parser.add_argument(
        "--cube-display-state", type=int, default=None, metavar="INDEX",
        help=(
            "Template display state that draws the cube; default is the state with the "
            "largest primitive list. Every other state draws nothing."
        ),
    )
    args = parser.parse_args()

    try:
        exclude_display_state_types = tuple(
            int(value) for value in args.exclude_display_state_types.split(",") if value.strip()
        )
    except ValueError:
        parser.error("--exclude-display-state-types must be a comma-separated list of integers")

    if args.skip_geo_id_patch and args.no_draw:
        parser.error(
            "--skip-geo-id-patch and --no-draw are mutually exclusive controls: "
            "the former leaves the submesh unowned (known to crash the game), "
            "the latter keeps it owned but draws nothing"
        )

    source = args.source.resolve()
    if not source.is_file():
        parser.error(f"source does not exist: {source}")

    template_submesh: int | str = args.template_submesh
    try:
        template_submesh = int(args.template_submesh)
    except ValueError:
        pass

    try:
        with source.open("r", encoding="utf-8") as source_file:
            probe_data = json.load(source_file)
        probe = prepare_fixture_data(
            probe_data, args.host_bone, template_submesh, args.allow_skinned_bone, args.no_draw,
            exclude_display_state_types, args.position_scale, args.cube, args.cube_display_state,
        )
        host_bone_id = int(probe["SluggiesModel"]["AddSubmeshFixture"]["HostBoneId"])
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
        return 1

    if args.skip_geo_id_patch:
        control_suffix = "_control_undrawn"
    elif args.no_draw:
        control_suffix = "_control_owned_nodraw"
    else:
        control_suffix = ""
    if exclude_display_state_types:
        control_suffix += "_excl" + "-".join(str(v) for v in exclude_display_state_types)
    if args.position_scale is not None:
        control_suffix += f"_scale{args.position_scale:g}"
    if args.cube is not None:
        control_suffix += f"_cube{args.cube:g}"
        if args.cube_display_state is not None:
            control_suffix += f"_ds{args.cube_display_state}"
    output = (
        args.output.resolve()
        if args.output
        else (
            TOOLS_DIR.parent
            / "Debug"
            / "fixtures"
            / f"{source.stem}.add_submesh_host{host_bone_id}{control_suffix}.sluggie"
        )
    )
    if source == output:
        parser.error("fixture output must differ from the source .sluggie")

    try:
        build_fixture(
            source,
            output,
            args.host_bone,
            template_submesh,
            args.allow_skinned_bone,
            args.write,
            args.skip_geo_id_patch,
            args.no_draw,
            exclude_display_state_types,
            args.position_scale,
            args.cube,
            args.cube_display_state,
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
