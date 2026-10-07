"""PLAN_EditRigidMeshes.md Phase 0 probes 1-3, 5 and 6, built through the real
RigidRebuild path (Phase 2 builder) instead of the earlier script-local splice.

Each case writes a ``Submeshes[i].RigidRebuild`` entry into a copy of a real
export, builds it with ``HammerspaceMain.BuildModelBlock`` (gpl='build', plus
tex='build' for the cases with a new PNG), checks the rebuilt blob against the
intent, and installs it with ``--write``. ``--unpatch`` restores the model.

Cases (one build each, so a Dolphin result is attributable):

  identity            Mario cap rebuilt from its own decoded data (probe 1)
  topology            Mario cap midpoint-subdivided once: 4x the faces, more
                      than 256 positions, so position/normal indices widen to
                      uint16 and the Type 3 is patched (probe 2)
  retarget            Mario cap re-baked into bone 49 (a finger tip) so it
                      stays on the head in the rest pose, GeoIdEdited 54 ->
                      free, 49 -> 1; it should follow the hand in animation
                      (probe 3). Also tests GeoIdEdited through hammerspace.
  retarget-offset     Control: GeoIdEdited only, bytes unchanged, so the cap
                      sits at the finger tip (the in-place "keep offset" path)
  surfaces            Blue Male Mii body (3 surfaces, 3 textures): half of
                      sm13_ds7 (texture 2) moves to sm13_ds6 (texture 1), a
                      partial move (probe 5). The body's colour array is not
                      listed in the export, so this also checks the carry-over.
  surfaces-empty      Mii body: every sm13_ds6 face moves to sm13_ds5, which
                      leaves ds6 with a null primitive list (probe 5)
  newsurface-rigid    Mario cap: the +y half moves to a new surface built from
                      rigid:sm2_ds9 (the head's lighting, a donor texture)
  newsurface-derived  Mario cap: the +y half on derived:sm0_ds5 with a checker
                      PNG appended to the TEX section (probe 6)
  newsurface-builtin  Mario cap: the +y half on builtin:rigid_spec_v1 with the
                      checker PNG, specular strength 50 (probe 6)

Usage: python probe_rigid_rebuild.py CASE [--write | --unpatch]
"""
from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
for import_path in (TOOLS_DIR, TOOLS_DIR / "Hammerspace", TOOLS_DIR.parent / "BlenderAddonSrc"):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import CustomSubmeshExport as cse
import HammerspaceHelper as hh
import HammerspaceMain as hammerspace
import build_add_submesh_fixture as probe
import build_rigid_visibility_probe as vis
from binfmt import color_entry_size, comp_size, decode_field
from drawlist import decodeDrawList

MODELS_DIR = TOOLS_DIR.parent / "2_Output_Models"
MARIO = MODELS_DIR / "18 Mario" / "78277664_mario.gpl" / "78277664_mario.gpl.sluggie"
MII = MODELS_DIR / "100 Blue Male Mii" / "333008640_mii_male.gpl" / "333008640_mii_male.gpl.sluggie"
CHECKER_PNG = "rigid_rebuild_probe_checker.png"
PROBE_TAG = "rigid_rebuild_probe_{case}"
FINGER_BONE = 49
GEO_ID_FREE = 0xFFFF
CASES = (
    "identity", "topology", "retarget", "retarget-offset", "surfaces", "surfaces-empty",
    "newsurface-rigid", "newsurface-derived", "newsurface-builtin",
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _u16s(values) -> bytes:
    return struct.pack(f">{len(values)}H", *values)


def _unpack_u16(raw: bytes) -> tuple[int, ...]:
    return struct.unpack(f">{len(raw) // 2}H", raw)


def _load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    model = data["SluggiesModel"]
    probe.strip_donor_edits(model)
    model["UseHammerspace"] = True
    return data


# ---------------------------------------------------------------------------
# Decoded donor submesh -> RigidRebuild entry
# ---------------------------------------------------------------------------

class Decoded:
    """A donor rigid submesh as plain lists: positions (s16 triplets), pooled
    normals / colours / UVs and per-loop indices into them, faces, and the
    donor state index each face draws through."""

    def __init__(self, sub: dict):
        vb = sub["VertexBuffer"]
        if vb["VertexBufferCompCount"] != 3 or comp_size(vb["VertexBufferQuantizeInfo"]) != 2:
            raise ValueError(f"{sub['MeshName']}: not an s16 rigid submesh")
        self.sub = sub
        raw = decode_field(vb["VertexBufferData"])
        self.positions = [list(struct.unpack_from(">3h", raw, i * 6)) for i in range(len(raw) // 6)]
        self.faces = [list(t) for t in zip(*[iter(_unpack_u16(decode_field(sub["FacesData"])))] * 3)]
        nb = sub.get("NormalBuffer")
        self.normal_comps = int(nb["NormalBufferCompCount"]) if nb else 0
        if nb:
            raw = decode_field(nb["NormalBufferData"])
            stride = self.normal_comps * 2
            self.normals = [list(struct.unpack_from(f">{self.normal_comps}h", raw, i * stride)) for i in range(len(raw) // stride)]
            self.normal_loops = list(_unpack_u16(decode_field(nb["NormalFacesData"])))
        else:
            self.normals, self.normal_loops = [], []
        colors = sub.get("ColorChannels") or []
        if colors:
            raw = decode_field(colors[0]["ColorChannelData"])
            size = color_entry_size(colors[0]["ColorChannelQuantizeInfo"])
            self.colors = [raw[i * size:(i + 1) * size] for i in range(len(raw) // size)]
            self.color_loops = list(_unpack_u16(decode_field(colors[0]["ColorFacesData"])))
        else:
            self.colors, self.color_loops = [], []
        self.uvs, self.uv_loops = {}, {}
        for uv in sub.get("UVChannels") or []:
            raw = decode_field(uv["UVChannelData"])
            comps = int(uv["UVChannelCompCount"])
            self.uvs[uv["UVChannelIndex"]] = [list(struct.unpack_from(f">{comps}h", raw, i * comps * 2)) for i in range(len(raw) // (comps * 2))]
            self.uv_loops[uv["UVChannelIndex"]] = list(_unpack_u16(decode_field(uv["UVFacesData"])))
        self.face_states = []
        for k, state in enumerate(sub["DisplayStates"]):
            self.face_states += [k] * int(state.get("FaceCount") or 0)
        if len(self.face_states) != len(self.faces):
            raise ValueError(f"{sub['MeshName']}: FaceCount ranges do not cover the faces")

    def to_rebuild(self, host_bone: int, table: list[str], indices: list[int], new_surfaces=None, reason="probe") -> dict:
        entry = {
            "HostBoneId": host_bone,
            "VertexBufferData": _b64(b"".join(struct.pack(">3h", *p) for p in self.positions)),
            "UVChannels": [
                {
                    "UVChannelIndex": channel,
                    "UVChannelData": _b64(b"".join(struct.pack(f">{len(c)}h", *c) for c in coords)),
                    "UVFacesData": _b64(_u16s(self.uv_loops[channel])),
                }
                for channel, coords in sorted(self.uvs.items())
            ],
            "FacesCount": len(self.faces),
            "FacesData": _b64(_u16s([v for face in self.faces for v in face])),
            "FaceSurfaceTable": table,
            "FaceSurfaceIndices": _b64(_u16s(indices)),
            "Reason": [reason],
        }
        if self.normals:
            entry["NormalBufferData"] = _b64(b"".join(struct.pack(f">{len(n)}h", *n) for n in self.normals))
            entry["NormalFacesData"] = _b64(_u16s(self.normal_loops))
        if self.colors:
            entry["ColorChannelData"] = _b64(b"".join(self.colors))
            entry["ColorFacesData"] = _b64(_u16s(self.color_loops))
        if new_surfaces:
            entry["NewSurfaces"] = new_surfaces
        return entry

    def surface_ids(self) -> list[str]:
        return [self.sub["DisplayStates"][k]["SurfaceId"] for k in sorted(set(self.face_states))]

    def identity_table(self) -> tuple[list[str], list[int]]:
        table = self.surface_ids()
        by_state = {self.sub["DisplayStates"][k]["SurfaceId"]: i for i, k in enumerate(sorted(set(self.face_states)))}
        return table, [by_state[self.sub["DisplayStates"][k]["SurfaceId"]] for k in self.face_states]

    def face_centres(self) -> list[list[float]]:
        return [[sum(self.positions[v][a] for v in face) / 3 for a in range(3)] for face in self.faces]


def _pool(values: list, keyed) -> tuple[list, dict]:
    pool, index = [], {}
    for value in values:
        key = keyed(value)
        if key not in index:
            index[key] = len(pool)
            pool.append(value)
    return pool, index


def subdivide(decoded: Decoded) -> None:
    """Midpoint-subdivide every triangle into four, in place. New corners
    average the two endpoints' normals (renormalized), UVs and colours."""
    midpoints: dict[tuple[int, int], int] = {}

    def midpoint(a: int, b: int) -> int:
        key = (min(a, b), max(a, b))
        if key not in midpoints:
            midpoints[key] = len(decoded.positions)
            decoded.positions.append([round((decoded.positions[a][i] + decoded.positions[b][i]) / 2) for i in range(3)])
        return midpoints[key]

    def avg_normal(x, y):
        n = [(x[i] + y[i]) / 2 for i in range(3)]
        length = sum(v * v for v in n) ** 0.5 or 1.0
        return [round(v / length * 16383) for v in n] + list(x[3:])

    def avg(x, y):
        return [round((x[i] + y[i]) / 2) for i in range(len(x))]

    new_faces, new_normals, new_colors = [], [], []
    new_uvs = {channel: [] for channel in decoded.uvs}
    for face_index, (a, b, c) in enumerate(decoded.faces):
        loops = [face_index * 3 + i for i in range(3)]
        ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
        corner_n = [decoded.normals[decoded.normal_loops[l]] for l in loops] if decoded.normals else None
        corner_c = [decoded.colors[decoded.color_loops[l]] for l in loops] if decoded.colors else None
        corner_uv = {ch: [decoded.uvs[ch][decoded.uv_loops[ch][l]] for l in loops] for ch in decoded.uvs}
        # per new face: (vertex, source corner pair) triples; a pair (i, i) is a donor corner
        quads = [
            [(a, (0, 0)), (ab, (0, 1)), (ca, (2, 0))],
            [(ab, (0, 1)), (b, (1, 1)), (bc, (1, 2))],
            [(ca, (2, 0)), (bc, (1, 2)), (c, (2, 2))],
            [(ab, (0, 1)), (bc, (1, 2)), (ca, (2, 0))],
        ]
        for quad in quads:
            new_faces.append([v for v, _ in quad])
            for _v, (i, j) in quad:
                if corner_n:
                    new_normals.append(corner_n[i] if i == j else avg_normal(corner_n[i], corner_n[j]))
                if corner_c:
                    new_colors.append(corner_c[i])
                for ch in decoded.uvs:
                    new_uvs[ch].append(corner_uv[ch][i] if i == j else avg(corner_uv[ch][i], corner_uv[ch][j]))
    decoded.faces = new_faces
    decoded.face_states = [k for k in decoded.face_states for _ in range(4)]
    if decoded.normals:
        decoded.normals, index = _pool(new_normals, tuple)
        decoded.normal_loops = [index[tuple(n)] for n in new_normals]
    if decoded.colors:
        decoded.colors, index = _pool(new_colors, bytes)
        decoded.color_loops = [index[bytes(c)] for c in new_colors]
    for ch, loops in new_uvs.items():
        decoded.uvs[ch], index = _pool(loops, tuple)
        decoded.uv_loops[ch] = [index[tuple(uv)] for uv in loops]


def rebake(decoded: Decoded, bone_hierarchy: list, old_bone: int, new_bone: int) -> None:
    """Re-express positions and normals in *new_bone*'s bind space so the
    mesh keeps its rest-pose world position (Reassign to new bone, Keep
    world position)."""
    absolute = cse.bone_absolute_matrices(bone_hierarchy)
    relative = cse.mat4_mul(cse.mat4_inverse(absolute[new_bone]), absolute[old_bone])
    divisor = cse.int16_divisor(decoded.sub["VertexBuffer"]["VertexBufferQuantizeInfo"])
    moved = []
    for p in decoded.positions:
        x, y, z = cse.transform_point(relative, [v / divisor for v in p])
        moved.append([cse.quantize_int16(v, divisor, "rebaked position") for v in (x, y, z)])
    decoded.positions = moved
    if decoded.normals:
        nq = cse.int16_divisor(decoded.sub["NormalBuffer"]["NormalBufferQuantizeInfo"])
        rotated = cse.transform_normals([[v / nq for v in n[:3]] for n in decoded.normals], relative)
        decoded.normals = [
            [cse.quantize_int16(v, nq, "rebaked normal") for v in n] + list(old[3:])
            for n, old in zip(rotated, decoded.normals)
        ]


def _effective_layer0_texture(states: list, surface_id: str) -> int:
    texture = None
    for state in states:
        if int(state["DisplayStateId"]) == 1:
            layer, index = hammerspace._custom_submesh_texture_layer(state["ShaderMode"])
            if layer == 0:
                texture = index
        if state.get("SurfaceId") == surface_id:
            break
    if texture is None:
        raise ValueError(f"{surface_id}: no layer-0 texture")
    return texture


def _write_checker(tex_dir: Path) -> None:
    from PIL import Image
    image = Image.new("RGBA", (256, 256))
    pixels = image.load()
    for y in range(256):
        for x in range(256):
            on = ((x // 32) + (y // 32)) % 2 == 0
            pixels[x, y] = (255, 200, 0, 255) if on else (20, 20, 160, 255)
    image.save(tex_dir / CHECKER_PNG)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

def _submesh(model: dict, name: str) -> tuple[int, dict]:
    for index, sub in enumerate(model["Submeshes"]):
        if sub["MeshName"] == name:
            return index, sub
    raise KeyError(name)


def _owner(model: dict, index: int) -> int:
    return next(int(b["BoneId"]) for b in model["BoneHierarchy"] if hammerspace._bone_geo_id_raw(b) == index)


def prepare(case: str) -> tuple[Path, dict, int, dict, "hammerspace.SectionModes"]:
    """Return (sluggie path, data, rebuilt submesh index, expected faces per
    surface key, section modes) for *case*."""
    path = MII if case.startswith("surfaces") else MARIO
    data = _load(path)
    model = data["SluggiesModel"]
    modes = hammerspace.SectionModes(gpl="build")
    index, sub = _submesh(model, "body" if case.startswith("surfaces") else "cap")
    decoded = Decoded(sub)
    host = _owner(model, index)
    table, indices = decoded.identity_table()
    new_surfaces = None

    if case == "topology":
        subdivide(decoded)
        table, indices = decoded.identity_table()
    elif case == "retarget":
        rebake(decoded, model["BoneHierarchy"], host, FINGER_BONE)
        _retarget(model, host, FINGER_BONE, index)
        host = FINGER_BONE
    elif case == "retarget-offset":
        # GeoIdEdited alone: no GPL edit, so the GPL is cloned (gpl='build'
        # without an edit marker would fall through to the legacy full
        # rebuild, which fails validation on this model; known since
        # 2026-09-28). The ACT clone path applies GeoIdEdited either way.
        _retarget(model, host, FINGER_BONE, index)
        sub.pop("RigidRebuild", None)
        return path, data, index, {}, hammerspace.SectionModes()
    elif case == "surfaces":
        # Half of ds7's faces (past the median along the body's longest axis) move to ds6.
        source, target = table.index("sm13_ds7"), table.index("sm13_ds6")
        centres = decoded.face_centres()
        extents = [max(p[a] for p in decoded.positions) - min(p[a] for p in decoded.positions) for a in range(3)]
        axis = extents.index(max(extents))
        own = [centres[i][axis] for i, k in enumerate(indices) if k == source]
        median = sorted(own)[len(own) // 2]
        indices = [target if k == source and centres[i][axis] >= median else k for i, k in enumerate(indices)]
    elif case == "surfaces-empty":
        source, target = table.index("sm13_ds6"), table.index("sm13_ds5")
        indices = [target if k == source else k for k in indices]
    elif case.startswith("newsurface"):
        kind = case.split("-")[1]
        key = f"sm{index}_new0"
        states0 = model["Submeshes"][0]["DisplayStates"]
        if kind == "rigid":
            template = "rigid:sm2_ds9"
            texture = {"DonorTextureIndex": _effective_layer0_texture(model["Submeshes"][2]["DisplayStates"], "sm2_ds9")}
            strength = None
        else:
            template = "derived:sm0_ds5" if kind == "derived" else "builtin:rigid_spec_v1"
            template_texture = _effective_layer0_texture(states0, "sm0_ds5") if kind == "derived" else _effective_layer0_texture(states0, states0[-1].get("SurfaceId"))
            _write_checker(path.parent / "tex")
            model["ReimportTextures"] = True
            model["AdditionalTextureDescriptors"] = [{"TextureFileName": CHECKER_PNG, "TemplateTextureIndex": template_texture}]
            texture = {"AdditionalTextureFileName": CHECKER_PNG}
            strength = 50 if kind == "builtin" else None
            modes = hammerspace.SectionModes(gpl="build", tex="build")
        new_surfaces = [{"SurfaceKey": key, "MaterialName": f"probe_{kind}", "TemplateSource": template, "TextureAssignment": texture}]
        if strength is not None:
            new_surfaces[0]["SpecularStrength"] = strength
        table = table + [key]
        centres = decoded.face_centres()
        indices = [len(table) - 1 if centres[i][1] > 0 else k for i, k in enumerate(indices)]
    elif case != "identity":
        raise ValueError(f"unknown case {case!r}")

    sub["RigidRebuild"] = decoded.to_rebuild(host, table, indices, new_surfaces, reason=case)
    expected = {}
    for face, k in zip(decoded.faces, indices):
        expected.setdefault(table[k], []).append(tuple(face))
    return path, data, index, expected, modes


def _retarget(model: dict, old_bone: int, new_bone: int, submesh_index: int) -> None:
    bones = {int(b["BoneId"]): b for b in model["BoneHierarchy"]}
    if hammerspace._bone_geo_id_raw(bones[new_bone]) != GEO_ID_FREE:
        raise ValueError(f"bone {new_bone} is not free")
    bones[old_bone]["GeoIdEdited"] = GEO_ID_FREE
    bones[new_bone]["GeoIdEdited"] = submesh_index


# ---------------------------------------------------------------------------
# Static check of the built block
# ---------------------------------------------------------------------------

def _gpl_of(block: bytes) -> bytes:
    gpl, act = struct.unpack_from(">II", block, 4)
    return block[gpl:act]


def check(build: "hammerspace.ModelBlockBuild", donor_gpl: bytes, index: int, sub: dict, expected: dict, case: str) -> None:
    report = build.validation_report
    if not report["valid"]:
        raise ValueError("block invalid: " + "; ".join(report["errors"]))
    prefix = int(report.get("container_prefix_size", 0))
    inner = int(report.get("inner_assembled_size", len(build.block) - prefix))
    alignment = probe.section_alignment_facts(build.block[prefix:prefix + inner])
    if alignment["misaligned"]:
        raise ValueError("off a 32-byte boundary: " + "; ".join(alignment["misaligned"]))
    gpl = _gpl_of(build.block[prefix:prefix + inner])
    if case == "retarget-offset":
        if gpl != donor_gpl:
            raise ValueError("retarget-offset must not change the GPL")
        return
    vis.verify_untouched(donor_gpl, gpl, {index})
    old, new = vis._read_blob(donor_gpl, index), vis._read_blob(gpl, index)
    donor_records = [record for record, _, _ in old["states"]]
    new_records = [record for record, _, _ in new["states"]]
    if new_records[:len(donor_records)] != donor_records and case != "topology":
        raise ValueError("donor display-state records changed")
    if case == "topology":
        changed = [k for k, (a, b) in enumerate(zip(donor_records, new_records)) if a != b]
        if any(donor_records[k][0] != 3 for k in changed):
            raise ValueError(f"records {changed} changed; only the Type 3 may (widening)")
        print(f"  Type 3 widened: {[donor_records[k][4:].hex() + ' -> ' + new_records[k][4:].hex() for k in changed]}")
    if old["color"] is not None and new["color"] is None:
        raise ValueError("the donor colour array was dropped")
    states = sub["DisplayStates"]
    key_by_record = {state["SurfaceId"]: k for k, state in enumerate(states) if state.get("SurfaceId")}
    # Appended groups follow in NewSurfaces order; each one's Type 7 draws.
    new_keys = iter(s["SurfaceKey"] for s in (sub.get("RigidRebuild") or {}).get("NewSurfaces") or [])
    for k in range(len(donor_records), len(new_records)):
        if new_records[k][0] == 7:
            key_by_record[next(new_keys)] = k
    setting = None
    for k, (record, primitive, residue) in enumerate(new["states"]):
        if record[0] == 3:
            setting = int.from_bytes(record[4:8], "big")
        if residue:
            raise ValueError(f"record {k} primitive list is not 32-byte aligned")
        faces = decodeDrawList(primitive, hammerspace._custom_submesh_type3_descriptors(setting)) if primitive else []
        key = next((key for key, value in key_by_record.items() if value == k), None)
        want = expected.get(key, []) if key else []
        got = [tuple(v["position"] for v in face) for face in faces]
        if got != want:
            raise ValueError(f"record {k} ({key}) draws {len(got)} faces, expected {len(want)}")
    summary = ", ".join(f"{key} {len(faces)}" for key, faces in expected.items())
    print(f"  {sub['MeshName']} (sm{index}): {len(new_records)} records, {new['position'][0]} positions; {summary}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case", choices=CASES)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true", help="install into output DAT/DOL/FST")
    action.add_argument("--unpatch", action="store_true", help="restore the probed model")
    args = parser.parse_args()
    try:
        path = MII if args.case.startswith("surfaces") else MARIO
        if args.unpatch:
            model = json.loads(path.read_text(encoding="utf-8"))["SluggiesModel"]
            ok, *_ = hh.removeModelFromHammerspace(model["ChunkNumber"], model["FileIndex"])
            print(f"{path.name}: {'restored' if ok else 'not in hammerspace'}")
            return 0
        path, data, index, expected, modes = prepare(args.case)
        model = data["SluggiesModel"]
        donor_gpl = hammerspace.CloneGPL(int(model["ModelOffset"], 16), model["ModelLength"])
        build = hammerspace.BuildModelBlock(data, modes, sluggie_path=path)
        check(build, donor_gpl, index, model["Submeshes"][index], expected, args.case)
        print(f"{path.name}: {args.case} builds, {len(build.block):,} bytes, valid")
        if args.write:
            out = hammerspace.WriteModelBlock(build, f"{path.stem}.{PROBE_TAG.format(case=args.case.replace('-', '_'))}")
            print(f"  installed at 0x{out:08X}")
        else:
            print("Dry run only; output DAT/DOL/FST were not modified")
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
