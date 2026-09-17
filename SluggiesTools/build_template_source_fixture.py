"""Phase 0 probe 5 (U4): cube submeshes built from each template source.

Appends one 12-triangle cube per ``--cube BONE=SOURCE`` to a donor model and
installs the result in hammerspace. ``SOURCE`` is one of the template sources
from PLAN_AddSubmesh.md "Template sources":

* ``rigid:<SurfaceId>``  clone a same-model rigid submesh and draw the cube in
  that surface (the probe 4 path).
* ``derived:<SurfaceId>`` build a canonical rigid state list from the effective
  states of a plain ``Spec`` surface on skinned submesh 0.
* ``builtin:rigid_spec_v1`` start from a canonical rigid state list captured
  from vanilla data, bound to the host model's own textures.

Derived and built-in cubes use the canonical rigid attribute formats (F9). The
fixture always pads GPL to a 32-byte boundary (F10). The helpers are pure and
tested so Phase 2 can move them into ``HammerspaceMain``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import struct
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_add_submesh_fixture as probe
from build_add_submesh_fixture import drawlist, hammerspace


TYPE1, TYPE3, TYPE4, TYPE6, TYPE7 = 1, 3, 4, 6, 7
TEXTURE_LAYER_SHIFT = 13
TEXTURE_INDEX_MASK = 0x1FFF
REJECTED_TYPE6 = "00000375"  # occurs only in skinned lists (F9)
TYPE4_BY_UV_COUNT = {1: "fffffff0", 2: "ffffff10"}  # F9

POSITION_FORMAT = (3, 59)
NORMAL_FORMAT = (3, 62)
UV_FORMAT = (2, 62)
COLOR_FORMAT = (4, 48)
COLOR_WHITE = bytes.fromhex("ffffffff")

# Canonical rigid list captured byte-exact from vanilla data. The same six
# records occur in 17 rigid submeshes of the player-folder exports. The states
# and their hash live once, in HammerspaceMain (the patch-time builder).
BUILTIN_RIGID_SPEC_V1 = {
    "Name": "rigid_spec_v1",
    "Provenance": {
        "Model": "33 Toadette/135708512_kinopico.gpl",
        "MeshName": "pony_l3",
        "SurfaceId": "sm1_ds5",
        "IdenticalRigidLists": 17,
    },
    "States": hammerspace._CUSTOM_SUBMESH_BUILTIN_TEMPLATES["rigid_spec_v1"]["States"],
    "Sha256": hammerspace._CUSTOM_SUBMESH_BUILTIN_TEMPLATES["rigid_spec_v1"]["Sha256"],
}
BUILTIN_TEMPLATES = {BUILTIN_RIGID_SPEC_V1["Name"]: BUILTIN_RIGID_SPEC_V1}


def _setting_bytes(mode: str) -> bytes:
    if len(mode) == 8 and all(c in "0123456789abcdefABCDEF" for c in mode):
        return bytes.fromhex(mode)
    return mode.encode("ascii").ljust(4, b"\x00")[:4]


def state_records_sha256(records) -> str:
    """Hash display-state records as their 16-byte headers without pointers."""
    payload = b"".join(
        bytes([state_id]) + bytes.fromhex(pad) + _setting_bytes(mode)
        for state_id, pad, mode in records
    )
    return hashlib.sha256(payload).hexdigest()


def type3_setting(layout: list[dict]) -> int:
    """Encode an attribute index layout as a Type-3 setting (inverse of decoding)."""
    setting = 0
    for descriptor in layout:
        shift = drawlist._ATTR_BIT_SHIFT[descriptor["key"]]
        setting |= (0b10 if descriptor["index_size"] == 1 else 0b11) << shift
    return setting


def index_size_for(count: int) -> int:
    return 1 if count <= 0x100 else 2


def _texture_layer(mode: str) -> tuple[int, int]:
    setting = int(mode, 16)
    return (setting >> TEXTURE_LAYER_SHIFT) & 7, setting & TEXTURE_INDEX_MASK


def _with_texture_index(mode: str, texture_index: int) -> str:
    setting = int(mode, 16)
    return f"{(setting & ~TEXTURE_INDEX_MASK) | (texture_index & TEXTURE_INDEX_MASK):08x}"


def _record(state: dict) -> tuple[int, str, str]:
    return int(state["DisplayStateId"]), state["DisplayStatePadBytes"], state["ShaderMode"]


def derive_rigid_state_records(submesh0: dict, surface_id: str) -> list[tuple[int, str, str]]:
    """Build the canonical rigid list for one surface of skinned submesh 0.

    Copies the effective layer-0 and layer-1 Type-1 states, Type-6 and Type-7
    states in force when the surface draws, in the order
    ``T1, T1, T4, T3, T6, T7``. Type 4 follows the UV channel count and Type 3
    is a placeholder that ``canonical_rigid_submesh`` fills in.
    """
    if int(submesh0["VertexBuffer"]["VertexBufferCompCount"]) != 6:
        raise ValueError("derived: sources must be surfaces of the skinned submesh 0")
    states = submesh0.get("DisplayStates") or []
    matches = [index for index, state in enumerate(states) if state.get("SurfaceId") == surface_id]
    if not matches:
        raise ValueError(f"derived: surface {surface_id!r} does not exist on submesh 0")
    surface_index = matches[0]
    if int(states[surface_index].get("PrimListLength") or 0) <= 0:
        raise ValueError(f"derived: surface {surface_id!r} draws no primitives")

    layers: dict[int, dict] = {}
    type6 = type7 = None
    for state in states[:surface_index + 1]:
        state_id = int(state["DisplayStateId"])
        if state_id == TYPE1:
            layers[_texture_layer(state["ShaderMode"])[0]] = state
        elif state_id == TYPE6:
            type6 = state
        elif state_id == TYPE7:
            type7 = state
    if type7 is None or type7["ShaderMode"] != "Spec":
        found = type7["ShaderMode"] if type7 else "none"
        raise ValueError(
            f"derived: surface {surface_id!r} has effective Type-7 {found!r}; only plain "
            "'Spec' surfaces are allowed (hand and visibility roles are excluded)"
        )
    if type6 is None:
        raise ValueError(f"derived: surface {surface_id!r} has no effective Type-6 state")
    if type6["ShaderMode"] == REJECTED_TYPE6:
        raise ValueError(f"derived: surface {surface_id!r} uses Type-6 {REJECTED_TYPE6}, which never occurs on rigid submeshes")
    if 0 not in layers:
        raise ValueError(f"derived: surface {surface_id!r} has no layer-0 texture binding")

    uv_count = 2 if 1 in layers else 1
    records = [_record(layers[0])]
    if uv_count == 2:
        records.append(_record(layers[1]))
    records += [
        (TYPE4, "000000", TYPE4_BY_UV_COUNT[uv_count]),
        (TYPE3, "000000", "00000000"),
        _record(type6),
        _record(type7),
    ]
    return records


def builtin_rigid_state_records(submesh0: dict, name: str) -> list[tuple[int, str, str]]:
    """Bind a built-in canonical list to the host model's submesh-0 textures."""
    template = BUILTIN_TEMPLATES.get(name)
    if template is None:
        raise ValueError(f"builtin: unknown template {name!r}; known: {sorted(BUILTIN_TEMPLATES)}")
    if state_records_sha256(template["States"]) != template["Sha256"]:
        raise ValueError(f"builtin: stored bytes of {name!r} do not match their recorded hash")
    bindings: dict[int, int] = {}
    for state in submesh0.get("DisplayStates") or []:
        if int(state["DisplayStateId"]) == TYPE1:
            layer, texture_index = _texture_layer(state["ShaderMode"])
            bindings.setdefault(layer, texture_index)
    if 0 not in bindings:
        raise ValueError("builtin: host submesh 0 has no layer-0 texture binding")

    records = []
    for state_id, pad, mode in template["States"]:
        if state_id == TYPE1:
            layer, _ = _texture_layer(mode)
            if layer not in bindings:
                continue
            mode = _with_texture_index(mode, bindings[layer])
        records.append((state_id, pad, mode))
    uv_count = 2 if 1 in bindings else 1
    return [
        (TYPE4, pad, TYPE4_BY_UV_COUNT[uv_count]) if state_id == TYPE4 else (state_id, pad, mode)
        for state_id, pad, mode in records
    ]


def canonical_rigid_submesh(
    name: str, records: list[tuple[int, str, str]], submesh0: dict, use_base64: bool,
) -> dict:
    """Create a rigid submesh skeleton with canonical formats (F9) and *records*.

    The last record draws. Arrays are placeholders that the cube writer
    replaces. Type 3 is generated for positions, normals, one color entry and
    each UV channel, with index widths chosen from the cube's attribute counts.
    """
    uv_count = sum(1 for state_id, _, _ in records if state_id == TYPE1)
    source_channels = submesh0.get("UVChannels") or []
    if uv_count > len(source_channels):
        raise ValueError(f"host submesh 0 has {len(source_channels)} UV channel(s); template needs {uv_count}")
    layout = [
        {"key": "position", "index_size": index_size_for(8)},
        {"key": "lighting", "index_size": index_size_for(6)},
        {"key": "color0", "index_size": index_size_for(1)},
    ] + [{"key": f"texture{i}", "index_size": index_size_for(4)} for i in range(uv_count)]
    type3_mode = f"{type3_setting(layout):08x}"

    placeholder = probe._encode_field(b"\x00" * 6, use_base64)
    texture0 = next(_texture_layer(mode)[1] for state_id, _, mode in records if state_id == TYPE1)
    display_states = []
    for index, (state_id, pad, mode) in enumerate(records):
        draws = index == len(records) - 1
        display_states.append({
            "SurfaceId": f"{name}_ds{index}",
            "DisplayStateId": state_id,
            "DisplayStatePadBytes": pad,
            "ShaderMode": type3_mode if state_id == TYPE3 else mode,
            "ShaderModeFieldOffset": None,
            "PrimListPtrFieldOffset": "0x0",
            "PrimListSizeFieldOffset": "0x0",
            "PrimListAbsoluteOffset": "0x0",
            "PrimListLength": 32 if draws else 0,
            "PrimListData": placeholder if draws else "",
            "FaceCount": 0,
        })
    return {
        "MeshName": name,
        "SubmeshOffset": "0x0",
        "PositionDataPtrFieldOffset": "0x0",
        "VertexCountFieldOffset": "0x0",
        "FacesCount": 0,
        "FacesData": placeholder,
        "FaceTextureIndices": probe._encode_field(struct.pack(">H", texture0), use_base64),
        "DisplayStates": display_states,
        "VertexBuffer": {
            "VertexBufferOffset": "0x0",
            "VertexBufferLength": 6,
            "VertexBufferCompCount": POSITION_FORMAT[0],
            "VertexBufferQuantizeInfo": POSITION_FORMAT[1],
            "VertexBufferData": placeholder,
        },
        "NormalBuffer": {
            "NormalDataPtrFieldOffset": "0x0",
            "NormalCountFieldOffset": "0x0",
            "NormalBufferOffset": "0x0",
            "NormalBufferLength": 6,
            "NormalBufferCompCount": NORMAL_FORMAT[0],
            "NormalBufferQuantizeInfo": NORMAL_FORMAT[1],
            "NormalAmbientPct": 0.0,
            "NormalBufferData": placeholder,
            "NormalFacesData": placeholder,
        },
        "UVChannels": [
            {
                "UVChannelIndex": i,
                "PaletteName": source_channels[i].get("PaletteName", ""),
                "TextureIndex": source_channels[i].get("TextureIndex", 0),
                "WrapS": source_channels[i].get("WrapS", 0),
                "WrapT": source_channels[i].get("WrapT", 0),
                "UVDataPtrFieldOffset": "0x0",
                "UVCountFieldOffset": "0x0",
                "UVChannelOffset": "0x0",
                "UVChannelLength": 6,
                "UVChannelCompCount": UV_FORMAT[0],
                "UVChannelQuantizeInfo": UV_FORMAT[1],
                "UVChannelData": placeholder,
                "UVFacesData": placeholder,
            }
            for i in range(uv_count)
        ],
        "ColorChannels": [{
            "ColorChannelIndex": 0,
            "ColorDataPtrFieldOffset": "0x0",
            "ColorCountFieldOffset": "0x0",
            "ColorChannelOffset": "0x0",
            "ColorChannelLength": len(COLOR_WHITE),
            "ColorChannelCompCount": COLOR_FORMAT[0],
            "ColorChannelQuantizeInfo": COLOR_FORMAT[1],
            "ColorChannelData": probe._encode_field(COLOR_WHITE, use_base64),
            "ColorFacesData": placeholder,
        }],
    }


def parse_source(spec: str) -> tuple[str, str]:
    kind, sep, argument = spec.partition(":")
    if not sep or kind not in ("rigid", "derived", "builtin") or not argument:
        raise ValueError(
            f"template source {spec!r} must be rigid:<SurfaceId>, derived:<SurfaceId> "
            "or builtin:<name>"
        )
    return kind, argument


def build_cube_submesh(model: dict, spec: str, name: str, half_extent: float) -> tuple[dict, dict]:
    """Return ``(submesh, metadata)`` for one cube from one template source."""
    use_base64 = bool(model.get("UseBase64", True))
    submeshes = model["Submeshes"]
    kind, argument = parse_source(spec)
    provenance = None
    if kind == "rigid":
        located = [
            (sub_index, state_index)
            for sub_index, sub in enumerate(submeshes)
            for state_index, state in enumerate(sub.get("DisplayStates") or [])
            if state.get("SurfaceId") == argument
        ]
        if not located:
            raise ValueError(f"rigid: surface {argument!r} does not exist in this model")
        sub_index, state_index = located[0]
        if int(submeshes[sub_index]["VertexBuffer"]["VertexBufferCompCount"]) != 3:
            raise ValueError(f"rigid: surface {argument!r} is not on a rigid submesh")
        submesh = copy.deepcopy(submeshes[sub_index])
        probe._clear_source_layout_fields(submesh)
        submesh["MeshName"] = name
        draw_state = state_index
    else:
        submesh0 = submeshes[0]
        if kind == "derived":
            records = derive_rigid_state_records(submesh0, argument)
        else:
            records = builtin_rigid_state_records(submesh0, argument)
            provenance = BUILTIN_TEMPLATES[argument]["Provenance"]
        submesh = canonical_rigid_submesh(name, records, submesh0, use_base64)
        draw_state = len(records) - 1
    cube = probe._replace_clone_with_cube(submesh, half_extent, draw_state, use_base64)
    metadata = {
        "TemplateSource": spec,
        "States": [
            [int(state["DisplayStateId"]), state["DisplayStatePadBytes"], state["ShaderMode"]]
            for state in submesh["DisplayStates"]
        ],
        "Cube": cube,
    }
    if provenance:
        metadata["BuiltinProvenance"] = provenance
    return submesh, metadata


def prepare_template_source_fixture(
    source_data: dict, cubes: list[tuple[int, str]], half_extent: float = 0.1,
) -> dict:
    """Append one cube submesh per ``(host_bone, template_source)`` (pure, no I/O)."""
    if not cubes:
        raise ValueError("at least one cube is required")
    data = copy.deepcopy(source_data)
    model = data["SluggiesModel"]
    probe.strip_donor_edits(model)
    bones = {int(bone["BoneId"]): bone for bone in model.get("BoneHierarchy") or []}
    skn_used = probe._skn_used_bone_ids(model.get("SkinData"))
    seen: set[int] = set()
    entries = []
    for host_bone, spec in cubes:
        bone = bones.get(host_bone)
        if bone is None:
            raise ValueError(f"bone {host_bone} does not exist in BoneHierarchy")
        if probe._bone_geo_id_raw(bone) != 0xFFFF:
            raise ValueError(f"bone {host_bone} already owns submesh {probe._bone_geo_id_raw(bone)}")
        if host_bone in seen:
            raise ValueError(f"bone {host_bone} is claimed by more than one cube")
        seen.add(host_bone)
        submesh_index = len(model["Submeshes"])
        submesh, metadata = build_cube_submesh(model, spec, f"custom{len(entries)}", half_extent)
        model["Submeshes"].append(submesh)
        bone["GeoIdEdited"] = submesh_index
        entries.append({
            "SubmeshIndex": submesh_index,
            "HostBoneId": host_bone,
            "SknUsedHostBone": host_bone in skn_used,
            **metadata,
        })
    model["UseHammerspace"] = True
    model["TemplateSourceFixture"] = {"HalfExtent": half_extent, "Cubes": entries}
    return data


def build_template_source_fixture(
    source_path: Path, fixture_path: Path, cubes: list[tuple[int, str]],
    half_extent: float, write: bool,
):
    with source_path.open("r", encoding="utf-8") as source_file:
        source_data = json.load(source_file)
    data = prepare_template_source_fixture(source_data, cubes, half_extent)
    model = data["SluggiesModel"]
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    with fixture_path.open("w", encoding="utf-8", newline="\n") as fixture_file:
        json.dump(data, fixture_file, indent=2)
        fixture_file.write("\n")

    modes = hammerspace.SectionModes(gpl="build", act="clone", tex="clone", skn="clone", trailing="clone")
    build = hammerspace.BuildModelBlock(data, modes, sluggie_path=fixture_path)
    if not build.validation_report["valid"]:
        raise ValueError("fixture block failed validation: " + "; ".join(build.validation_report["errors"]))
    probe.require_built_submesh_count(build, len(model["Submeshes"]))

    offset = model.get("ModelOffset", 0)
    source_model_offset = int(offset, 16) if isinstance(offset, str) else int(offset)
    bones = {int(bone["BoneId"]): bone for bone in model["BoneHierarchy"]}
    for entry in model["TemplateSourceFixture"]["Cubes"]:
        field = bones[entry["HostBoneId"]].get("GeoIdFieldOffset")
        if not field:
            raise ValueError(f"bone {entry['HostBoneId']} has no GeoIdFieldOffset metadata")
        build = probe._apply_add_submesh_geo_id_patch(
            build, source_model_offset, entry["HostBoneId"], field, entry["SubmeshIndex"],
        )
        if not build.validation_report["valid"]:
            raise ValueError("fixture block failed validation after GeoId patches: " + "; ".join(build.validation_report["errors"]))

    prefix = int(build.validation_report.get("container_prefix_size", 0))
    alignment = probe.section_alignment_facts(build.block[prefix:])
    build.validation_report["section_alignment"] = alignment
    if alignment["misaligned"]:
        raise ValueError("fixture block is off a 32-byte boundary: " + "; ".join(alignment["misaligned"]))

    output_offset = hammerspace.WriteModelBlock(build, fixture_path.name) if write else None

    lines = [f"Fixture: {fixture_path}"]
    for entry in model["TemplateSourceFixture"]["Cubes"]:
        states = " ".join(f"T{state_id}:{mode}/{pad}" for state_id, pad, mode in entry["States"])
        lines.append(
            f"Submesh {entry['SubmeshIndex']} on bone {entry['HostBoneId']} "
            f"(SKN-used={entry['SknUsedHostBone']}) from {entry['TemplateSource']}: "
            f"{probe._format_cube_summary(entry['Cube'])}\n    states: {states}"
        )
    lines.append(probe._format_alignment_summary(alignment))
    lines.append(
        f"Block: {len(build.block)} bytes, delta {build.validation_report['size_delta']:+d}, "
        f"valid={build.validation_report['valid']}"
    )
    lines.append(
        f"Installed at output DAT offset 0x{output_offset:08X}" if output_offset is not None
        else "Dry run only; output DAT/DOL/FST were not modified"
    )
    print("\n".join(lines))
    return build, output_offset, data


def _parse_cube(value: str) -> tuple[int, str]:
    bone, sep, spec = value.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError("expected BONE=SOURCE, e.g. 29=derived:sm0_ds5")
    try:
        parse_source(spec)
        return int(bone), spec
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Phase 0 template-source probe (U4).")
    parser.add_argument("source", type=Path, help="Source .sluggie path")
    parser.add_argument(
        "--cube", type=_parse_cube, action="append", required=True, metavar="BONE=SOURCE",
        help="Host bone and template source, e.g. 29=derived:sm0_ds5 or 49=builtin:rigid_spec_v1",
    )
    parser.add_argument("--half-extent", type=float, default=0.1, help="Cube half extent in bone-local units")
    parser.add_argument("--output", type=Path, help="Generated fixture .sluggie path")
    parser.add_argument("--write", action="store_true", help="Install into output DAT/DOL/FST")
    args = parser.parse_args()

    source = args.source.resolve()
    if not source.is_file():
        parser.error(f"source does not exist: {source}")
    tag = "_".join(f"b{bone}-{spec.split(':')[0]}" for bone, spec in args.cube)
    output = (
        args.output.resolve() if args.output
        else TOOLS_DIR.parent / "Debug" / "fixtures" / f"{source.stem}.template_sources_{tag}.sluggie"
    )
    if output == source:
        parser.error("fixture output must differ from the source .sluggie")
    try:
        build_template_source_fixture(source, output, args.cube, args.half_extent, args.write)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
