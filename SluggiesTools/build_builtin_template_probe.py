"""Phase 0 probe 7: the unverified built-in templates on a rebuilt donor cap.

Each probed cap (main model and its LOD) is rebuilt as in
build_rigid_visibility_probe.py: primitive lists re-encoded in donor face order,
the blob spliced before GPLUserData, the old blob left unreferenced, every
other submesh untouched. Part of the cap's faces moves into an appended group
built from a built-in template.

Round 1:
  Mario    rigid_shdw_v1  one side (+y half)
  Wario    rigid_ghsp_v1  one side (+y half)
  Waluigi  rigid_rhsp_v1  front half (brim side, x below the median)
           rigid_lhsp_v1  back half
Round 2:
  Mario    rigid_lhsp_v1  front half (Mario wears the mitt on his left hand)
  Wario    own:Shdw       one side: a copy of the cap's own six records with
                          only the T7 mode changed to Shdw, which separates the
                          mode from rigid_shdw_v1's 1-layer form

The group is the template's own records (id, pad, mode), with each T1 rebound
to the texture the cap binds on that layer and Type 3 taken from the cap
(texture1 dropped for a 1-layer template, so the vertex format matches the
single texgen). The cap's own textures are kept on purpose: the shader mode
is the only difference. The hand-role faces are repointed to a new alpha-0
white color entry (fff0), since a role only acts on vertex alpha 0. The LOD
caps store RGB565 colors; their entries are all white ffff, which is also
opaque white in RGBA4444, so only the header format changes to (4, 48).

Usage: python build_builtin_template_probe.py [--round 1|2] [--write | --unpatch]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import build_rigid_visibility_probe as rebuild

hammerspace = rebuild.hammerspace
hh = rebuild.hh

PROBE_TAG = "builtin_template_probe_r{round}"
TEXTURE1_SHIFT = rebuild.probe.drawlist._ATTR_BIT_SHIFT["texture1"]
ALPHA0_WHITE = bytes.fromhex("fff0")
TEMPLATES = hammerspace._CUSTOM_SUBMESH_BUILTIN_TEMPLATES
ROUNDS = {
    1: {
        "18 Mario": [("rigid_shdw_v1", "side")],
        "28 Wario": [("rigid_ghsp_v1", "side")],
        "29 Waluigi": [("rigid_rhsp_v1", "front"), ("rigid_lhsp_v1", "back")],
    },
    2: {
        "18 Mario": [("rigid_lhsp_v1", "front")],
        "28 Wario": [("own:Shdw", "side")],
    },
}


def _region_faces(sub: dict, faces: list, region: str) -> list[bool]:
    """Flag the faces of *region*; the cap frame has the brim toward -x, y lateral."""
    positions = rebuild._positions(sub)
    centres = [[sum(positions[v["position"]][a] for v in face) for a in range(3)] for face in faces]
    if region == "side":
        return [c[1] > 0 for c in centres]
    median = sorted(c[0] for c in centres)[len(centres) // 2]
    return [(c[0] < median) == (region == "front") for c in centres]


def _own_group(cap_states: list, mode: str) -> list[dict]:
    """A copy of the cap's own canonical group with only the T7 mode changed."""
    group = copy.deepcopy(cap_states)
    for k, state in enumerate(group):
        state.update({"SurfaceId": f"own_{mode}_{k}", "FaceCount": 0, "PrimListData": "", "PrimListLength": 0})
    group[-1]["ShaderMode"] = mode
    return group


def _template_group(cap_states: list, name: str) -> list[dict]:
    """The template's records bound to the cap's textures and Type 3."""
    if name.startswith("own:"):
        return _own_group(cap_states, name.partition(":")[2])
    bindings = {}
    for state in cap_states:
        if state["DisplayStateId"] == 1:
            layer, texture = hammerspace._custom_submesh_texture_layer(state["ShaderMode"])
            bindings.setdefault(layer, texture)
    cap_type3 = next(state for state in cap_states if state["DisplayStateId"] == 3)
    layers = TEMPLATES[name]["Layers"]
    layout = [d for d in cap_type3["VertexStreamLayout"] if layers == 2 or d["key"] != "texture1"]
    type3 = int(cap_type3["ShaderMode"], 16)
    if layers == 1:
        type3 &= ~(0b11 << TEXTURE1_SHIFT)

    group = []
    for k, (state_id, pad, mode) in enumerate(TEMPLATES[name]["States"]):
        if state_id == 1:
            layer, _ = hammerspace._custom_submesh_texture_layer(mode)
            mode = hammerspace._custom_submesh_with_texture_index(mode, bindings[layer])
        elif state_id == 3:
            mode = f"{type3:08x}"
        state = copy.deepcopy(cap_states[0])
        state.update({
            "SurfaceId": f"{name}_{k}",
            "DisplayStateId": state_id,
            "DisplayStateParamBytes": pad,
            "ShaderMode": mode,
            "FaceCount": 0,
            "PrimListData": "",
            "PrimListLength": 0,
            "VertexStreamLayout": copy.deepcopy(layout),
        })
        group.append(state)
    if [s["DisplayStateId"] for s in group][-1] != 7:
        raise ValueError(f"{name}: template does not end in its drawing Type 7")
    return group


def _record_bytes(state: dict) -> bytes:
    mode = state["ShaderMode"]
    setting = bytes.fromhex(mode) if len(mode) == 8 else mode.encode("ascii")
    return bytes([state["DisplayStateId"]]) + bytes.fromhex(state["DisplayStateParamBytes"]) + setting


def build_cap(path: Path, probes: list[tuple[str, str]]) -> "hammerspace.ModelBlockBuild":
    data = rebuild._load(path)
    model = data["SluggiesModel"]
    index, cap = rebuild._submesh(model, "cap")
    states = cap["DisplayStates"]
    if [s["DisplayStateId"] for s in states] != rebuild.CANONICAL_GROUP_IDS or states[5]["ShaderMode"] != "Spec":
        raise ValueError(f"{path.name}: cap is not one canonical Spec group")
    faces = rebuild._decode_state(states[5])

    color = None
    alpha0 = None
    if any(name in ("rigid_rhsp_v1", "rigid_lhsp_v1") for name, _ in probes):
        channel = cap["ColorChannels"][0]
        payload = rebuild.decode_field(channel["ColorChannelData"])
        if channel["ColorChannelQuantizeInfo"] == 0 and set(payload) == {0xFF}:
            channel["ColorChannelQuantizeInfo"], channel["ColorChannelCompCount"] = 48, 4
        if channel["ColorChannelQuantizeInfo"] != 48:
            raise ValueError(f"{path.name}: cap colors are not RGBA4444 or all-white RGB565")
        alpha0 = len(payload) // 2
        payload += ALPHA0_WHITE
        channel["ColorChannelData"] = rebuild._b64(payload)
        color = (alpha0 + 1, 48, channel["ColorChannelCompCount"], payload)

    expected = {}
    groups = []
    taken = [False] * len(faces)
    for name, region in probes:
        flags = [flag and not done for flag, done in zip(_region_faces(cap, faces, region), taken)]
        moved = [face for face, flag in zip(faces, flags) if flag]
        taken = [done or flag for done, flag in zip(taken, flags)]
        if alpha0 is not None:
            moved = [[{**v, "color0": alpha0} for v in face] for face in moved]
        group = _template_group(states[:6], name)
        keys = {d["key"] for d in group[-1]["VertexStreamLayout"]}
        moved = [[{k: v[k] for k in v if k in keys} for v in face] for face in moved]
        group[-1]["FaceCount"] = len(moved)
        group[-1]["PrimListDataEdited"] = rebuild._b64(rebuild._encode_state(group[-1], moved))
        groups.append((name, region, group, moved))
    remaining = [face for face, done in zip(faces, taken) if not done]
    states[5]["PrimListDataEdited"] = rebuild._b64(rebuild._encode_state(states[5], remaining))
    expected[5] = remaining
    for _, _, group, moved in groups:
        expected[len(states) + len(group) - 1] = moved
        states.extend(group)

    offset = int(model["ModelOffset"], 16)
    donor_gpl = hammerspace.CloneGPL(offset, model["ModelLength"])
    rebuilt_gpl = hammerspace.BuildGPLMeshData(hammerspace.ParseSluggie(data)).gpl_bytes
    spliced = rebuild.splice_blobs(donor_gpl, rebuilt_gpl, [index])
    records = rebuild.appended_copy_records(donor_gpl, index, None)
    records += [_record_bytes(state) for state in states[len(records):]]
    rebuild.verify(donor_gpl, spliced, index, cap, expected, records, color)
    rebuild.verify_untouched(donor_gpl, spliced, {index})

    donor = rebuild._load(path)
    original_clone = hammerspace.CloneGPL
    hammerspace.CloneGPL = lambda off, length: spliced
    try:
        modes = hammerspace.SectionModes(gpl="clone", act="clone", tex="clone", skn="clone", trailing="clone")
        block = hammerspace.BuildModelBlock(donor, modes, sluggie_path=path)
    finally:
        hammerspace.CloneGPL = original_clone
    moved_summary = ", ".join(f"{name} {region} {len(moved)}" for name, region, _, moved in groups)
    print(f"  {path.name}: cap {len(faces)} faces -> Spec {len(remaining)}, {moved_summary}; states verified")
    return rebuild._validated(block, path.name)


def targets(round_number: int) -> list[tuple[Path, list[tuple[str, str]]]]:
    found = []
    for folder, probes in ROUNDS[round_number].items():
        paths = sorted((rebuild.MODELS_DIR / folder).glob("*.gpl/*.sluggie"))
        caps = [p for p in paths
                if any(s["MeshName"] == "cap" for s in json.loads(p.read_text(encoding="utf-8"))["SluggiesModel"]["Submeshes"])]
        if len(caps) != 2:
            raise FileNotFoundError(f"{folder}: expected a main and an LOD model with a cap, found {len(caps)}")
        found += [(p, probes) for p in caps]
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--round", type=int, choices=sorted(ROUNDS), default=max(ROUNDS))
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true", help="install into output DAT/DOL/FST")
    action.add_argument("--unpatch", action="store_true", help="restore every probed model")
    args = parser.parse_args()
    try:
        for path, probes in targets(args.round):
            if args.unpatch:
                model = json.loads(path.read_text(encoding="utf-8"))["SluggiesModel"]
                ok, *_ = hh.removeModelFromHammerspace(model["ChunkNumber"], model["FileIndex"])
                print(f"  {path.name}: {'restored' if ok else 'not in hammerspace'}")
                continue
            block = build_cap(path, probes)
            if args.write:
                out = hammerspace.WriteModelBlock(block, f"{path.stem}.{PROBE_TAG.format(round=args.round)}")
                print(f"    installed at 0x{out:08X} ({len(block.block):,} bytes)")
        if not (args.write or args.unpatch):
            print("Dry run only; output DAT/DOL/FST were not modified")
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
