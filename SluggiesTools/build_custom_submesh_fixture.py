"""Rebuild the Phase 0 cube probes through the real CustomSubmeshes pipeline.

PLAN_AddSubmesh.md Phase 2 manual check. Phase 0's fixtures appended a cloned
submesh to ``Submeshes`` and patched ``GeoId`` by hand. This script instead
writes ``CustomSubmeshes`` entries encoded by the Blender add-on's bpy-free
exporter (``BlenderAddonSrc/CustomSubmeshExport.py``), so the block is built
the way a real export is: ``PatchGPLAppendSubmesh`` appends the geometry and
``_apply_geo_id_patches`` gives each cube its owner bone.

Each cube is a 12-triangle box of half extent 0.1 centred on its host bone,
with every face mapped to the whole texture.

Usage (defaults reproduce probe 4 plus probe 5's Mario control cubes):
    python build_custom_submesh_fixture.py            # dry run
    python build_custom_submesh_fixture.py --write    # install into 3_Output_Dat
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent
ADDON_DIR = ROOT_DIR / "BlenderAddonSrc"
for import_path in (TOOLS_DIR, TOOLS_DIR / "Hammerspace", ADDON_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import CustomSubmeshExport as cse  # noqa: E402
import build_add_submesh_fixture as probe  # noqa: E402
from build_add_submesh_fixture import hammerspace  # noqa: E402

DEFAULT_SOURCE = (
    ROOT_DIR / "2_Output_Models" / "18 Mario" / "78277664_mario.gpl" / "78277664_mario.gpl.sluggie"
)
DEFAULT_FIXTURE = ROOT_DIR / "Debug" / "fixtures" / "78277664_mario.gpl.custom_submeshes_probe4_probe5.sluggie"

# (host bone, template source, donor texture index)
# - bone 49: finger leaf under hand bone 28, head surface + head texture (probe 4)
# - bone 85: the other hand, body Spec surface + body texture (probe 5)
# - bone 11: toe tip, built-in template + body texture (probe 5)
DEFAULT_CUBES = (
    (49, "rigid:sm2_ds7", 1),
    (85, "derived:sm0_ds5", 0),
    (11, "builtin:rigid_spec_v1", 0),
)

HALF_EXTENT = 0.1
# 8 corners; faces wound counter-clockwise seen from outside (the add-on's
# own cube test geometry, scaled to HALF_EXTENT).
CUBE_COS = [
    (x * HALF_EXTENT, y * HALF_EXTENT, z * HALF_EXTENT)
    for x, y, z in ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                    (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))
]
CUBE_FACES = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
              (2, 3, 7), (2, 7, 6), (1, 2, 6), (1, 6, 5), (0, 4, 7), (0, 7, 3)]
IDENTITY = [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def _cube_triangles():
    return [cse.Triangle(face, tuple(3 * i + k for k in range(3)), i)
            for i, face in enumerate(CUBE_FACES)]


def _cube_loop_attributes():
    """Per-loop face normals and UVs, indexed like _cube_triangles' loops.

    UVs project each corner onto its face's two in-plane axes, so every face
    shows the whole texture with no seam inside the face."""
    normals, uvs = [], []
    for face in CUBE_FACES:
        a, b, c = (CUBE_COS[v] for v in face)
        u = [b[k] - a[k] for k in range(3)]
        v = [c[k] - a[k] for k in range(3)]
        n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
        length = sum(x * x for x in n) ** 0.5
        n = [x / length for x in n]
        axis = max(range(3), key=lambda k: abs(n[k]))
        in_plane = [k for k in range(3) if k != axis]
        for vertex in face:
            normals.append(tuple(n))
            co = CUBE_COS[vertex]
            uvs.append(tuple(0.0 if co[k] < 0 else 1.0 for k in in_plane))
    return normals, uvs, None


def build_entries(model: dict, cubes) -> list[dict]:
    """Encode one CustomSubmeshes entry per (bone, source, texture) cube."""
    use_base64 = bool(model.get("UseBase64", True))
    bind_matrices = cse.bone_absolute_matrices(model["BoneHierarchy"])
    normals, uvs, colors = _cube_loop_attributes()
    entries = []
    for index, (bone_id, template_source, texture_index) in enumerate(cubes):
        host_bind = bind_matrices.get(bone_id)
        if host_bind is None:
            raise ValueError(f"bone {bone_id} does not exist in this model")
        # Place the cube in armature space exactly on the host bone, the way
        # Blender would see an object at the bone's bind position.
        geometry = cse.bone_local_geometry(
            CUBE_COS, _cube_triangles(), host_bind, IDENTITY, host_bind,
        )
        plan = cse.attribute_plan(model, template_source)
        name = f"cube_b{bone_id}"
        entries.append(cse.build_custom_submesh_entry(
            name, f"custom{index}", bone_id, template_source, plan, geometry,
            normals, uvs, colors, {"DonorTextureIndex": texture_index}, use_base64,
        ))
    return entries


def build(source_path: Path, fixture_path: Path, cubes, write: bool):
    with source_path.open("r", encoding="utf-8") as source_file:
        data = json.load(source_file)
    data = copy.deepcopy(data)
    model = data["SluggiesModel"]
    # Keep the probe donor-identical apart from the cubes.
    probe.strip_donor_edits(model)
    model.pop("AdditionalTextureDescriptors", None)
    model.pop("DesiredTextureAssignments", None)
    model["UseHammerspace"] = True
    model["ReimportTextures"] = False
    donor_count = len(model["Submeshes"])
    model["CustomSubmeshes"] = build_entries(model, cubes)

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    with fixture_path.open("w", encoding="utf-8", newline="\n") as fixture_file:
        json.dump(data, fixture_file, indent=2)
        fixture_file.write("\n")

    modes = hammerspace.SectionModes(gpl="build", act="clone", tex="clone", skn="clone", trailing="clone")
    result = hammerspace.BuildModelBlock(data, modes, sluggie_path=fixture_path)
    report = result.validation_report
    if not report["valid"]:
        raise ValueError("custom submesh block failed validation: " + "; ".join(report["errors"]))
    probe.require_built_submesh_count(result, donor_count + len(cubes))

    facts = report["validator_facts"]
    prefix = int(report.get("container_prefix_size", 0))
    alignment = probe.section_alignment_facts(result.block[prefix:])
    if alignment["misaligned"]:
        raise ValueError("block is off a 32-byte boundary: " + "; ".join(alignment["misaligned"]))

    owners = facts.get("act_geo_id_owners", {})
    lines = [f"Fixture: {fixture_path}"]
    for index, (bone_id, template_source, texture_index) in enumerate(cubes):
        submesh_index = donor_count + index
        owner_ok = owners.get(submesh_index) == [bone_id]
        lines.append(
            f"Submesh {submesh_index} on bone {bone_id} from {template_source}, "
            f"texture {texture_index}: owner {owners.get(submesh_index)} "
            f"({'ok' if owner_ok else 'WRONG'})"
        )
        if not owner_ok:
            raise ValueError(f"submesh {submesh_index} is not owned by bone {bone_id}")
    lines.append(f"Donor owners: { {k: v for k, v in owners.items() if k < donor_count} }")
    lines.append(probe._format_alignment_summary(alignment))
    lines.append(
        f"Block: {len(result.block)} bytes, delta {len(result.block) - result.original_length:+d}, "
        f"valid={report['valid']}"
    )
    if write:
        offset = hammerspace.WriteModelBlock(result, fixture_path.name)
        lines.append(f"Installed into 3_Output_Dat at 0x{offset:08X}" if offset is not None
                     else "Installed into 3_Output_Dat")
    else:
        lines.append("Dry run only; output DAT/DOL/FST were not modified")
    print("\n".join(lines))
    return result


def _parse_cube(text: str):
    try:
        bone, rest = text.split("=", 1)
        source, texture = rest.rsplit(",", 1)
        return int(bone), source, int(texture)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected BONE=SOURCE,TEXTURE, e.g. 49=rigid:sm2_ds7,1"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Donor .sluggie export")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="Fixture .sluggie to write")
    parser.add_argument("--cube", type=_parse_cube, action="append",
                        help="BONE=SOURCE,TEXTURE (repeatable); default: probe 4 + probe 5 cubes")
    parser.add_argument("--write", action="store_true", help="Install into output DAT/DOL/FST")
    args = parser.parse_args()
    build(args.source, args.fixture, args.cube or DEFAULT_CUBES, args.write)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
