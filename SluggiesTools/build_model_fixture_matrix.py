"""Build the Milestone 0.1 model-replacement fixture matrix.

The exported Sluggie JSON supplies structural counts while the donor model
header in dt_na.dat supplies authoritative section boundaries and ptr6/ptr7/ptr8.
This command never modifies either input.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import struct
from typing import Any

import slogger


TOOLS_DIR = pathlib.Path(__file__).resolve().parent
PROJECT_DIR = TOOLS_DIR.parent
DEFAULT_OUTPUT_PATH = PROJECT_DIR / "_docs" / "meta" / "model_replacement_fixture_matrix.json"
SECTION_NAMES = ("GPL", "ACT", "TEX", "SKN", "ptr6", "ptr7", "ptr8")
EDITED_FIXTURE_KINDS = (
    "position_only",
    "same_count_reskin",
    "vertex_count_increase",
    "new_face",
    "material_reassignment",
    "new_png_texture",
)


def _integer(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(value, 0)


def _display_path(path: pathlib.Path) -> str:
    resolved = path.resolve()
    try:
        resolved = resolved.relative_to(PROJECT_DIR.resolve())
    except ValueError:
        pass
    return str(resolved).replace("\\", "/")


def _vertex_count(submesh: dict[str, Any]) -> int:
    vertex_buffer = submesh["VertexBuffer"]
    component_count = vertex_buffer["VertexBufferCompCount"]
    quantize_info = vertex_buffer["VertexBufferQuantizeInfo"]
    component_size = 4 if (quantize_info >> 4) in (4, 7, 10) else 2
    stride = component_count * component_size
    if stride <= 0 or vertex_buffer["VertexBufferLength"] % stride:
        raise ValueError(f"invalid vertex buffer stride for {submesh.get('MeshName', '<unnamed>')}")
    return vertex_buffer["VertexBufferLength"] // stride


def _read_section_layout(
    dat_path: pathlib.Path,
    model_offset: int,
    model_length: int,
) -> tuple[dict[str, int], dict[str, int]]:
    with dat_path.open("rb") as dat_file:
        dat_file.seek(model_offset)
        header = dat_file.read(0x20)
    if len(header) != 0x20:
        raise ValueError(f"model header at 0x{model_offset:X} is outside {dat_path}")

    zero, *pointers = struct.unpack(">8I", header)
    if zero != 0:
        raise ValueError(f"model at 0x{model_offset:X} does not start with a zero word")
    pointer_map = dict(zip(SECTION_NAMES, pointers))
    populated = sorted({pointer for pointer in pointers if 0 < pointer < model_length})
    sizes = {}
    for name, pointer in pointer_map.items():
        if not 0 < pointer < model_length:
            sizes[name] = 0
            continue
        end = min((candidate for candidate in populated if candidate > pointer), default=model_length)
        sizes[name] = end - pointer
    return pointer_map, sizes


def inspect_fixture(sluggie_path: pathlib.Path, dat_path: pathlib.Path) -> dict[str, Any]:
    with sluggie_path.open("r", encoding="utf-8") as sluggie_file:
        model = json.load(sluggie_file)["SluggiesModel"]

    model_offset = _integer(model["ModelOffset"])
    model_length = model["ModelLength"]
    pointers, section_sizes = _read_section_layout(dat_path, model_offset, model_length)
    submeshes = model.get("Submeshes", [])
    skin_data = model.get("SkinData") or {}
    facial_pose = model.get("FacialPoseData")
    display_state_count = sum(len(submesh.get("DisplayStates", [])) for submesh in submeshes)

    coverage = []
    if skin_data:
        coverage.append("skinned")
    if skin_data.get("SKAccs"):
        coverage.append("skacc")
    if display_state_count > 1:
        coverage.append("multiple_display_states")
    if pointers["ptr7"] and facial_pose:
        coverage.append("recognized_ptr7_facial_pose")

    return {
        "name": sluggie_path.stem,
        "source": _display_path(sluggie_path),
        "chunk_number": model["ChunkNumber"],
        "file_index": model["FileIndex"],
        "model_offset": f"0x{model_offset:X}",
        "model_length": model_length,
        "section_sizes": section_sizes,
        "submesh_count": len(submeshes),
        "vertex_count": sum(_vertex_count(submesh) for submesh in submeshes),
        "face_count": sum(submesh.get("FacesCount", 0) for submesh in submeshes),
        "display_state_count": display_state_count,
        "sk_entry_counts": {
            "SK1": len(skin_data.get("SK1s", [])),
            "SK2": len(skin_data.get("SK2s", [])),
            "SKAcc": len(skin_data.get("SKAccs", [])),
        },
        "texture_count": len(model.get("TextureDescriptors", [])),
        "nonzero_trailing_pointers": {
            name: f"0x{pointers[name]:X}"
            for name in ("ptr6", "ptr7", "ptr8")
            if pointers[name]
        },
        "coverage": coverage,
        "manual_control_test": {
            "character_select": "pending",
            "static_scene": "pending",
            "animated_gameplay": "pending",
        },
    }


def build_matrix(
    sluggie_paths: list[pathlib.Path],
    dat_path: pathlib.Path,
    existing_matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing_matrix = existing_matrix or {}
    existing_fixtures = {
        fixture.get("source"): fixture
        for fixture in existing_matrix.get("fixtures", [])
        if isinstance(fixture, dict) and fixture.get("source")
    }
    fixtures = []
    for path in sluggie_paths:
        fixture = inspect_fixture(path, dat_path)
        prior_fixture = existing_fixtures.get(fixture["source"], {})
        if isinstance(prior_fixture.get("manual_control_test"), dict):
            fixture["manual_control_test"] = prior_fixture["manual_control_test"]
        fixtures.append(fixture)

    existing_edits = existing_matrix.get("edited_blender_fixtures", {})
    edited_fixtures = {}
    for kind in EDITED_FIXTURE_KINDS:
        prior_edit = existing_edits.get(kind, {}) if isinstance(existing_edits, dict) else {}
        edited_fixtures[kind] = (
            prior_edit if isinstance(prior_edit, dict)
            else {"source": None, "status": "not_created"}
        )

    return {
        "milestone": "0.1",
        "donor_dat": _display_path(dat_path),
        "fixtures": fixtures,
        "edited_blender_fixtures": edited_fixtures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sluggies", nargs="+", type=pathlib.Path, help="entry00 .sluggie fixture files")
    parser.add_argument("--dat", type=pathlib.Path, default=PROJECT_DIR / "1_Input" / "dt_na.dat")
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=DEFAULT_OUTPUT_PATH,
    )
    args = parser.parse_args()

    try:
        existing_matrix = {}
        if args.output.exists():
            existing_matrix = json.loads(args.output.read_text(encoding="utf-8"))
        matrix = build_matrix(args.sluggies, args.dat, existing_matrix)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        slogger.error(
            f"Fixture matrix failed: {type(exc).__name__}: {exc}",
            source="fixtures.model_replacement",
        )
        return 1

    slogger.info(
        f"Fixture matrix written | Fixtures: {len(matrix['fixtures'])} | Output: {args.output}",
        source="fixtures.model_replacement",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())