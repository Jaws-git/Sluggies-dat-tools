"""Phase 0 probe 5 extension: draw order and hand visibility roles on a rebuilt rigid submesh.

Rebuilds rigid submeshes of the male Mii (main model and its LOD) with every
primitive list re-encoded as GX_TRIANGLES in donor face order. The rebuilt blob
goes before GPLUserData and its descriptor is repointed; the old blob stays
unreferenced (E4). Every other submesh is untouched.

  body   identity rebuild of a 3-surface, 3-texture submesh (draw order)
  r_hand half the faces move out of RhSp into an appended Spec group
  r_arm2 half the faces move into an appended RhSp group

An appended group copies the submesh's own T1 L0, T1 L1, T4, T3, T6, T7 records
and changes only the T7 shader mode, so the role is the only difference.
Both gloves are shrunk to 10% around their bounding-box centre, so they don't
cover the hands.

--glove-shift N moves each glove N raw position units (s16, 2048 = 1.0) along
its local +x axis at full size instead, which is the hand bone's
wrist-to-fingertip axis (hand vertices span x 44..860, gloves 205..1483). The
mitt then floats past the fingertips and still shows which hand wears it.

--opaque-moved repoints the moved r_hand faces' color0 index to the hand's
opaque color entry (the hand's other entry has alpha 0), to test whether the
role acts on vertex alpha.

Usage: python build_rigid_visibility_probe.py [--colors 100-106] [--opaque-moved]
       [--glove-shift N] [--write | --unpatch]
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import struct
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
for import_path in (TOOLS_DIR, TOOLS_DIR / "Hammerspace"):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import HammerspaceHelper as hh
import HammerspaceMain as hammerspace
import build_add_submesh_fixture as probe
from binfmt import color_entry_size, comp_size
from drawlist import decodeDrawList, encodeDrawList

MODELS_DIR = TOOLS_DIR.parent / "2_Output_Models"
PROBE_TAG = "rigid_vis_probe"
GLOVE_SCALE = 0.1
CANONICAL_GROUP_IDS = [1, 1, 4, 3, 6, 7]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    model = data["SluggiesModel"]
    if not model.get("UseBase64", True):
        raise ValueError(f"{path.name}: probe expects base64 fields")
    probe.strip_donor_edits(model)
    model["UseHammerspace"] = True
    return data


def _submesh(model: dict, name: str) -> tuple[int, dict]:
    for index, sub in enumerate(model["Submeshes"]):
        if sub["MeshName"] == name:
            return index, sub
    raise KeyError(f"no submesh named {name!r}")


def _positions(sub: dict) -> list[tuple[int, int, int]]:
    vb = sub["VertexBuffer"]
    if vb["VertexBufferCompCount"] != 3 or comp_size(vb["VertexBufferQuantizeInfo"]) != 2:
        raise ValueError(f"{sub['MeshName']}: expected rigid s16 positions")
    raw = base64.b64decode(vb["VertexBufferData"])
    return [struct.unpack_from(">3h", raw, i * 6) for i in range(len(raw) // 6)]


def _layout(state: dict) -> list[dict]:
    return [{"key": d["key"], "direct": False, "index_size": d["index_size"]}
            for d in state["VertexStreamLayout"]]


def _decode_state(state: dict) -> list:
    return decodeDrawList(base64.b64decode(state["PrimListData"]), _layout(state))


def _encode_state(state: dict, faces: list) -> bytes:
    raw = encodeDrawList(faces, _layout(state))
    original = base64.b64decode(state["PrimListData"])
    return raw + b"\x00" if original and original[-1] == 0 else raw


def _drawing_states(sub: dict) -> list[int]:
    return [k for k, state in enumerate(sub["DisplayStates"]) if state["PrimListData"]]


def _split_faces(sub: dict, faces: list) -> tuple[list, list]:
    """Split *faces* at the median face centre along the submesh's longest axis."""
    positions = _positions(sub)
    extents = [max(p[a] for p in positions) - min(p[a] for p in positions) for a in range(3)]
    axis = extents.index(max(extents))
    centres = [sum(positions[v["position"]][axis] for v in face) for face in faces]
    median = sorted(centres)[len(centres) // 2]
    stay = [face for face, c in zip(faces, centres) if c < median]
    move = [face for face, c in zip(faces, centres) if c >= median]
    return stay, move


def _rebuild_identity(sub: dict) -> dict:
    """Re-encode every drawing state of *sub* from its own decoded faces."""
    expected = {}
    for k in _drawing_states(sub):
        state = sub["DisplayStates"][k]
        faces = _decode_state(state)
        state["PrimListDataEdited"] = _b64(_encode_state(state, faces))
        expected[k] = faces
    return expected


def _opaque_color_index(sub: dict) -> int:
    """Index of the only fully opaque RGBA4444 entry in the submesh's color array."""
    channel = sub["ColorChannels"][0]
    if channel["ColorChannelQuantizeInfo"] != 48:
        raise ValueError(f"{sub['MeshName']}: expected RGBA4444 colors")
    raw = base64.b64decode(channel["ColorChannelData"])
    opaque = [i for i in range(len(raw) // 2) if raw[2 * i + 1] & 0x0F == 0x0F]
    if len(opaque) != 1:
        raise ValueError(f"{sub['MeshName']}: expected one opaque color entry, got {opaque}")
    return opaque[0]


def _move_half_to_new_group(sub: dict, shader_mode: str, color0: int | None = None) -> dict:
    """Move half of the single drawing surface's faces into an appended group.

    With *color0*, the moved faces' color0 index is repointed to that entry.
    """
    states = sub["DisplayStates"]
    ids = [state["DisplayStateId"] for state in states]
    if ids != CANONICAL_GROUP_IDS or _drawing_states(sub) != [5]:
        raise ValueError(f"{sub['MeshName']}: expected one canonical record group, got ids {ids}")
    faces = _decode_state(states[5])
    stay, move = _split_faces(sub, faces)
    if color0 is not None:
        move = [[{**v, "color0": color0} for v in face] for face in move]
    states[5]["PrimListDataEdited"] = _b64(_encode_state(states[5], stay))

    group = copy.deepcopy(states[:6])
    for k, state in enumerate(group):
        state["SurfaceId"] = f"{state['SurfaceId']}_probe{k}"
        state["FaceCount"] = 0
    group[5]["ShaderMode"] = shader_mode
    group[5]["FaceCount"] = len(move)
    group[5]["PrimListDataEdited"] = _b64(_encode_state(group[5], move))
    states.extend(group)
    return {5: stay, 11: move}


# ---------------------------------------------------------------------------
# GPL splice (E4): new blobs before GPLUserData, old blobs unreferenced
# ---------------------------------------------------------------------------

def _gpl_header(gpl: bytes) -> tuple[int, int, int, int]:
    _, _, user_data_ptr, count, desc_ptr = struct.unpack_from(">5I", gpl, 0)
    return user_data_ptr, count, desc_ptr, user_data_ptr or len(gpl)


def _blob_range(gpl: bytes, index: int) -> tuple[int, int, int]:
    user_data_ptr, count, desc_ptr, region_end = _gpl_header(gpl)
    descriptors = [struct.unpack_from(">II", gpl, desc_ptr + i * 8) for i in range(count)]
    starts = sorted({p for entry in descriptors for p in entry} | {region_end})
    start, name_ptr = descriptors[index]
    end = next(p for p in starts if p > max(start, name_ptr))
    return start, name_ptr, end


def splice_blobs(donor_gpl: bytes, rebuilt_gpl: bytes, indices: list[int]) -> bytes:
    user_data_ptr, count, desc_ptr, region_end = _gpl_header(donor_gpl)
    out = bytearray(donor_gpl[:region_end])
    for index in indices:
        start, name_ptr, end = _blob_range(rebuilt_gpl, index)
        out += b"\x00" * ((-len(out)) % 32)
        new_start = len(out)
        out += rebuilt_gpl[start:end]
        struct.pack_into(">II", out, desc_ptr + index * 8, new_start, new_start + name_ptr - start)
    out += b"\x00" * ((-len(out)) % 32)
    if user_data_ptr:
        struct.pack_into(">I", out, 0x08, len(out))
    out += donor_gpl[region_end:]
    return bytes(out)


# ---------------------------------------------------------------------------
# Static check: states unchanged except pointers, appended groups as intended
# ---------------------------------------------------------------------------

def _read_blob(gpl: bytes, index: int) -> dict:
    start, name_ptr, _ = _blob_range(gpl, index)
    pos_h, col_h, uv_h, nor_h, dsp_h = struct.unpack_from(">5I", gpl, start)
    m_uv = gpl[start + 0x14]

    def array(header: int, stride_of) -> tuple:
        ptr, cnt, q, cc = struct.unpack_from(">IHBB", gpl, start + header)
        return (cnt, q, cc, gpl[start + ptr:start + ptr + cnt * stride_of(q, cc)]) if cnt else None

    info = {
        "name": gpl[name_ptr:gpl.index(b"\x00", name_ptr)],
        "pad": gpl[start + 0x15:start + 0x18],
        "position": array(pos_h, lambda q, cc: comp_size(q) * cc),
        "color": array(col_h, lambda q, cc: color_entry_size(q)) if col_h else None,
        "uv": [],
        "normal": None,
    }
    for j in range(m_uv):
        h = uv_h + j * 0x10
        pal_ptr = struct.unpack_from(">I", gpl, start + h + 8)[0]
        pal = gpl[start + pal_ptr:gpl.index(b"\x00", start + pal_ptr)]
        info["uv"].append((array(h, lambda q, cc: comp_size(q) * cc), pal))
    if nor_h:
        info["normal"] = (array(nor_h, lambda q, cc: comp_size(q) * cc),
                          gpl[start + nor_h + 8:start + nor_h + 12])
    ds_ptr, n_ds = struct.unpack_from(">IH", gpl, start + dsp_h + 4)
    info["states"] = []
    for k in range(n_ds):
        rec = start + ds_ptr + k * 0x10
        pl_ptr, pl_size = struct.unpack_from(">II", gpl, rec + 8)
        info["states"].append((gpl[rec:rec + 8], gpl[start + pl_ptr:start + pl_ptr + pl_size] if pl_size else b"",
                               (start + pl_ptr) % 32 if pl_size else 0))
    return info


def appended_copy_records(donor_gpl: bytes, sub_index: int, appended_mode: str | None) -> list[bytes]:
    """Donor records, plus a copy of the first six with the last T7 mode swapped."""
    records = [record for record, _, _ in _read_blob(donor_gpl, sub_index)["states"]]
    if appended_mode is None:
        return records
    return records + records[:5] + [records[5][:4] + appended_mode.encode("ascii")]


def verify(donor_gpl: bytes, spliced: bytes, sub_index: int, sub: dict, expected: dict,
           records: list[bytes], color: tuple | None = None) -> None:
    """Check the rebuilt blob against the donor.

    *records* are the expected 8-byte record headers (id, pad, setting) in
    order; *color*, when given, replaces the donor color array expectation.
    """
    old, new = _read_blob(donor_gpl, sub_index), _read_blob(spliced, sub_index)
    if color is not None:
        old["color"] = color
    where = f"{sub['MeshName']} (sm{sub_index})"
    for key in ("name", "pad", "position", "color", "uv", "normal"):
        if old[key] != new[key]:
            raise ValueError(f"{where}: {key} differs from the donor")
    if len(new["states"]) != len(records):
        raise ValueError(f"{where}: {len(new['states'])} records, expected {len(records)}")
    for k, expected_record in enumerate(records):
        record, primitive, residue = new["states"][k]
        if record != expected_record:
            raise ValueError(f"{where}: record {k} is {record.hex()}, expected {expected_record.hex()}")
        if residue:
            raise ValueError(f"{where}: record {k} primitive list is not 32-byte aligned")
        state = sub["DisplayStates"][k]
        faces = decodeDrawList(primitive, _layout(state)) if primitive else []
        if faces != expected.get(k, []):
            raise ValueError(f"{where}: record {k} draws {len(faces)} faces, "
                             f"expected {len(expected.get(k, []))}")


def verify_untouched(donor_gpl: bytes, spliced: bytes, rebuilt: set[int]) -> None:
    count = struct.unpack_from(">I", donor_gpl, 0x0C)[0]
    for i in sorted(set(range(count)) - rebuilt):
        if _blob_range(donor_gpl, i)[:2] != _blob_range(spliced, i)[:2]:
            raise ValueError(f"sm{i} moved although it was not rebuilt")
    if spliced[0x14:_gpl_header(donor_gpl)[3]] != donor_gpl[0x14:_gpl_header(donor_gpl)[3]]:
        changed = {i for i in range(count)
                   if spliced[0x14 + i * 8:0x1C + i * 8] != donor_gpl[0x14 + i * 8:0x1C + i * 8]}
        if changed != rebuilt:
            raise ValueError(f"descriptors {sorted(changed)} changed, expected {sorted(rebuilt)}")


# ---------------------------------------------------------------------------
# Builds
# ---------------------------------------------------------------------------

def _validated(block: "hammerspace.ModelBlockBuild", what: str) -> "hammerspace.ModelBlockBuild":
    if not block.validation_report["valid"]:
        raise ValueError(f"{what}: " + "; ".join(block.validation_report["errors"]))
    prefix = int(block.validation_report.get("container_prefix_size", 0))
    inner = int(block.validation_report.get("inner_assembled_size", len(block.block) - prefix))
    alignment = probe.section_alignment_facts(block.block[prefix:prefix + inner])
    if alignment["misaligned"]:
        raise ValueError(f"{what}: off a 32-byte boundary: " + "; ".join(alignment["misaligned"]))
    return block


def _keep_unexported_color_array(donor_gpl: bytes, index: int, sub: dict) -> None:
    """Carry a donor color array the export omitted into the rebuilt blob.

    Rigid Mii submeshes without color0 in their Type 3 still point at a
    one-entry RGB565 array. The export lists no ColorChannels for them, so the
    builder would write an empty color header; keep the donor's instead.
    """
    color = _read_blob(donor_gpl, index)["color"]
    if color is None or sub["ColorChannels"]:
        return
    count, quantize_info, comp_count, payload = color
    sub["ColorChannels"].append({
        "ColorChannelIndex": 0,
        "ColorChannelData": _b64(payload),
        "ColorFacesData": "",
        "ColorChannelCompCount": comp_count,
        "ColorChannelQuantizeInfo": quantize_info,
    })


def build_mii(path: Path, opaque_moved: bool = False) -> "hammerspace.ModelBlockBuild":
    data = _load(path)
    model = data["SluggiesModel"]
    plan = []  # (index, submesh, expected faces per record, appended T7 mode)
    body_index, body = _submesh(model, "body")
    plan.append((body_index, body, _rebuild_identity(body), None))
    hand_index, hand = _submesh(model, "r_hand")
    if hand["DisplayStates"][-1]["ShaderMode"] != "RhSp":
        raise ValueError(f"{path.name}: r_hand does not draw with RhSp")
    color0 = _opaque_color_index(hand) if opaque_moved else None
    plan.append((hand_index, hand, _move_half_to_new_group(hand, "Spec", color0), "Spec"))
    arm_index, arm = _submesh(model, "r_arm2")
    plan.append((arm_index, arm, _move_half_to_new_group(arm, "RhSp"), "RhSp"))

    offset = int(model["ModelOffset"], 16)
    donor_gpl = hammerspace.CloneGPL(offset, model["ModelLength"])
    for index, sub, _, _ in plan:
        _keep_unexported_color_array(donor_gpl, index, sub)
    rebuilt_gpl = hammerspace.BuildGPLMeshData(hammerspace.ParseSluggie(data)).gpl_bytes
    spliced = splice_blobs(donor_gpl, rebuilt_gpl, sorted(index for index, *_ in plan))
    for index, sub, expected, mode in plan:
        verify(donor_gpl, spliced, index, sub, expected, appended_copy_records(donor_gpl, index, mode))
    verify_untouched(donor_gpl, spliced, {index for index, *_ in plan})

    donor = _load(path)
    original_clone = hammerspace.CloneGPL
    hammerspace.CloneGPL = lambda off, length: spliced
    try:
        modes = hammerspace.SectionModes(gpl="clone", act="clone", tex="clone", skn="clone", trailing="clone")
        block = hammerspace.BuildModelBlock(donor, modes, sluggie_path=path)
    finally:
        hammerspace.CloneGPL = original_clone
    summary = ", ".join(f"{sub['MeshName']} {sum(map(len, exp.values()))} faces" for _, sub, exp, _ in plan)
    print(f"  {path.name}: rebuilt {summary}; states verified")
    return _validated(block, path.name)


def build_glove(path: Path, shift: int | None = None) -> "hammerspace.ModelBlockBuild":
    data = _load(path)
    model = data["SluggiesModel"]
    if len(model["Submeshes"]) != 1:
        raise ValueError(f"{path.name}: expected one glove submesh")
    sub = model["Submeshes"][0]
    positions = _positions(sub)
    if shift is None:
        centre = [(min(p[a] for p in positions) + max(p[a] for p in positions)) / 2 for a in range(3)]
        moved = [tuple(round(centre[a] + (p[a] - centre[a]) * GLOVE_SCALE) for a in range(3))
                 for p in positions]
        what = f"scaled to {GLOVE_SCALE:.0%}"
    else:
        moved = [(p[0] + shift, p[1], p[2]) for p in positions]
        what = f"shifted {shift:+d} along local x"
    if any(not -0x8000 <= c <= 0x7FFF for p in moved for c in p):
        raise ValueError(f"{path.name}: glove positions leave the s16 range")
    sub["VertexBuffer"]["VertexBufferDataEdited"] = _b64(b"".join(struct.pack(">3h", *p) for p in moved))
    modes = hammerspace.SectionModes(gpl="build", act="clone", tex="clone", skn="clone", trailing="clone")
    block = hammerspace.BuildModelBlock(data, modes, sluggie_path=path)
    print(f"  {path.name}: {len(positions)} vertices {what}")
    return _validated(block, path.name)


def _color_folders(first: int, last: int) -> list[Path]:
    folders = []
    for number in range(first, last + 1):
        matches = list(MODELS_DIR.glob(f"{number} * Male Mii"))
        if len(matches) != 1:
            raise FileNotFoundError(f"no unique male Mii export folder {number} in {MODELS_DIR}")
        folders.append(matches[0])
    return folders


def targets(folder: Path) -> tuple[list[Path], list[Path]]:
    miis = sorted(folder.glob("*mii_male.gpl*/*.sluggie"))
    gloves = sorted(folder.glob("*/*_glove_[LR].gpl/*.sluggie"))
    if len(miis) != 2 or len(gloves) != 2:
        raise FileNotFoundError(f"{folder.name}: expected 2 Mii models and 2 gloves, "
                                f"found {len(miis)} and {len(gloves)}")
    return miis, gloves


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--colors", default="100-106", help="male Mii export folders, e.g. 100 or 100-106")
    parser.add_argument("--opaque-moved", action="store_true",
                        help="give the moved r_hand faces the opaque color entry")
    parser.add_argument("--glove-shift", type=int, default=None,
                        help="move gloves along local +x by this many raw units instead of shrinking")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true", help="install into output DAT/DOL/FST")
    action.add_argument("--unpatch", action="store_true", help="restore every probed model")
    args = parser.parse_args()
    first, _, last = args.colors.partition("-")
    try:
        folders = _color_folders(int(first), int(last or first))
        for folder in folders:
            print(folder.name)
            miis, gloves = targets(folder)
            for path in miis + gloves:
                if args.unpatch:
                    model = json.loads(path.read_text(encoding="utf-8"))["SluggiesModel"]
                    ok, *_ = hh.removeModelFromHammerspace(model["ChunkNumber"], model["FileIndex"])
                    print(f"  {path.name}: {'restored' if ok else 'not in hammerspace'}")
                    continue
                block = build_mii(path, args.opaque_moved) if path in miis else build_glove(path, args.glove_shift)
                if args.write:
                    out = hammerspace.WriteModelBlock(block, f"{path.stem}.{PROBE_TAG}")
                    print(f"    installed at 0x{out:08X} ({len(block.block):,} bytes)")
        if not (args.write or args.unpatch):
            print("Dry run only; output DAT/DOL/FST were not modified")
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
