from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
HAMMERSPACE_DIR = TOOLS_DIR / "Hammerspace"
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import HammerspaceMain as hammerspace
import texture_helper


def _setting_int(value: str | int) -> int:
    return int(value, 16) if isinstance(value, str) else int(value)


def _active_texture_setter(display_states: list[dict], target_index: int, layer: int = 0) -> int:
    if target_index < 0 or target_index >= len(display_states):
        raise ValueError(f"display-state index {target_index} is outside the table")

    active_setter = None
    for state_index, state in enumerate(display_states[: target_index + 1]):
        if state.get("DisplayStateId") != 1:
            continue
        setting = _setting_int(state.get("ShaderModeEdited") or state["ShaderMode"])
        if ((setting >> 13) & 7) == layer:
            active_setter = state_index

    if active_setter is None:
        raise ValueError(
            f"display state {target_index} has no active Type-1 texture setter for layer {layer}"
        )
    return active_setter


def _effective_shader_mode(display_states: list[dict], target_index: int) -> str | None:
    shader_mode = None
    for state in display_states[: target_index + 1]:
        if state.get("DisplayStateId") == 7:
            shader_mode = state.get("ShaderModeEdited") or state.get("ShaderMode")
    return shader_mode


def prepare_fixture_data(
    source_data: dict,
    texture_file_name: str,
    submesh_index: int,
    display_state_index: int,
    template_texture_index: int,
    additional_targets: list[tuple[int, int]] | None = None,
) -> tuple[dict, dict, int]:
    data = copy.deepcopy(source_data)
    model = data["SluggiesModel"]
    descriptors = model["TextureDescriptors"]
    indices = [int(descriptor["TextureIndex"]) for descriptor in descriptors]
    expected_indices = list(range(len(descriptors)))
    if indices != expected_indices:
        raise ValueError(
            f"donor texture indices must be contiguous {expected_indices}, found {indices}"
        )
    if template_texture_index not in indices:
        raise ValueError(f"template texture {template_texture_index} does not exist")

    new_index = len(descriptors)
    if new_index > 0x1FFF:
        raise ValueError("the appended texture index exceeds the Type-1 13-bit field")

    template = next(
        descriptor
        for descriptor in descriptors
        if int(descriptor["TextureIndex"]) == template_texture_index
    )
    if int(template.get("AdditionalMipCount") or 0):
        raise ValueError("mipmapped descriptor templates are not supported")

    new_descriptor = copy.deepcopy(template)
    new_descriptor.update({
        "TextureIndex": new_index,
        "TextureFileName": texture_helper.validate_texture_file_name(texture_file_name),
        "ImageDataOffset": "0x0",
        "ImageDataLength": 0,
        "ImagePayloadLength": 0,
        "PaletteDataOffset": None,
        "PaletteDataLength": None,
        "TextureDescriptorOffset": "0x0",
    })
    descriptors.append(new_descriptor)

    submeshes = model["Submeshes"]
    targets = [(submesh_index, display_state_index), *(additional_targets or [])]
    if len(set(targets)) != len(targets):
        raise ValueError("fixture targets must be unique")

    target_metadata = []
    setter_index = None
    for target_submesh, target_display_state in targets:
        if target_submesh < 0 or target_submesh >= len(submeshes):
            raise ValueError(f"submesh index {target_submesh} is outside the model")
        display_states = submeshes[target_submesh]["DisplayStates"]
        if target_display_state < 0 or target_display_state >= len(display_states):
            raise ValueError(
                f"display-state index {target_display_state} is outside submesh {target_submesh}"
            )
        target_state = display_states[target_display_state]
        if not int(target_state.get("PrimListLength") or 0):
            raise ValueError(
                f"submesh {target_submesh} display state {target_display_state} "
                "has no primitive data"
            )

        target_setter = _active_texture_setter(display_states, target_display_state)
        setter = display_states[target_setter]
        old_setting = _setting_int(setter.get("ShaderModeEdited") or setter["ShaderMode"])
        layer = (old_setting >> 13) & 7
        original_texture = old_setting & 0x1FFF
        setter["ShaderModeEdited"] = f"{(old_setting & ~0x1FFF) | new_index:08x}"
        target_metadata.append({
            "SubmeshIndex": target_submesh,
            "DisplayStateIndex": target_display_state,
            "TextureSetterDisplayStateIndex": target_setter,
            "OriginalTextureIndex": original_texture,
            "EffectiveShaderMode": _effective_shader_mode(
                display_states, target_display_state
            ),
            "TextureLayer": layer,
            "WrapS": (old_setting >> 16) & 0xF,
            "WrapT": (old_setting >> 20) & 0xF,
        })
        if setter_index is None:
            setter_index = target_setter

    model["UseHammerspace"] = True
    model["ReimportTextures"] = False
    fixture_metadata = {
        "TextureIndex": new_index,
        "TextureFileName": texture_file_name,
        "TemplateTextureIndex": template_texture_index,
        "Targets": target_metadata,
    }
    existing_fixtures = list(model.get("AdditionalTextureFixtures") or [])
    model["AdditionalTextureFixture"] = fixture_metadata
    model["AdditionalTextureFixtures"] = [*existing_fixtures, fixture_metadata]
    assert setter_index is not None
    return data, new_descriptor, setter_index


def build_fixture(
    source_path: Path,
    png_path: Path,
    fixture_path: Path,
    submesh_index: int,
    display_state_index: int,
    template_texture_index: int,
    write: bool,
    additional_targets: list[tuple[int, int]] | None = None,
    second_png_path: Path | None = None,
    second_target: tuple[int, int] | None = None,
    second_template_texture_index: int = 0,
) -> tuple[hammerspace.ModelBlockBuild, int | None]:
    with source_path.open("r", encoding="utf-8") as source_file:
        source_data = json.load(source_file)

    data, _, _ = prepare_fixture_data(
        source_data,
        png_path.name,
        submesh_index,
        display_state_index,
        template_texture_index,
        additional_targets,
    )
    png_paths = [png_path]
    if second_png_path is not None:
        if second_target is None:
            raise ValueError("second_target is required with second_png_path")
        data, _, _ = prepare_fixture_data(
            data,
            second_png_path.name,
            second_target[0],
            second_target[1],
            second_template_texture_index,
        )
        png_paths.append(second_png_path)

    model = data["SluggiesModel"]
    fixture_entries = model["AdditionalTextureFixtures"]
    descriptors_by_index = {
        int(descriptor["TextureIndex"]): descriptor
        for descriptor in model["TextureDescriptors"]
    }
    plan_entries = []
    encoded_by_index = {}
    for fixture_entry, texture_path in zip(fixture_entries, png_paths):
        descriptor = descriptors_by_index[int(fixture_entry["TextureIndex"])]
        encoded = texture_helper.encode_png_to_tpl(
            texture_path,
            int(descriptor["Format"]),
            descriptor.get("PaletteFormat") if descriptor.get("PaletteEntries") else None,
        )
        descriptor.update({
            "Width": encoded.width,
            "Height": encoded.height,
            "ImageDataLength": len(encoded.image_data),
            "ImagePayloadLength": len(encoded.image_data),
            "PaletteEntries": encoded.palette_entries,
            "PaletteFormat": encoded.palette_format or 0,
            "PaletteDataLength": len(encoded.palette_data) or None,
        })
        encoded_by_index[int(descriptor["TextureIndex"])] = encoded
        plan_entries.append(texture_helper.TexturePlanEntry(
            texture_index=int(descriptor["TextureIndex"]),
            texture_file_name=descriptor["TextureFileName"],
            width=encoded.width,
            height=encoded.height,
            format=encoded.format,
            format_name=encoded.format_name,
            image_data=encoded.image_data,
            palette_data=encoded.palette_data,
            palette_entries=encoded.palette_entries,
            palette_format=encoded.palette_format,
        ))

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    with fixture_path.open("w", encoding="utf-8", newline="\n") as fixture_file:
        json.dump(data, fixture_file, indent=2)
        fixture_file.write("\n")

    plan = texture_helper.TexturePlan(entries=tuple(plan_entries))
    modes = hammerspace.SectionModes(
        gpl="build",
        act="clone",
        tex="build",
        skn="clone",
        trailing="clone",
    )
    build = hammerspace.BuildModelBlock(
        data,
        modes,
        sluggie_path=fixture_path,
        texture_plan=plan,
    )
    if not build.validation_report["valid"]:
        raise ValueError(
            "fixture model block failed validation: "
            + "; ".join(build.validation_report["errors"])
        )

    output_offset = None
    if write:
        output_offset = hammerspace.WriteModelBlock(build, fixture_path.name)

    texture_lines = []
    binding_lines = []
    for fixture_entry in fixture_entries:
        encoded = encoded_by_index[int(fixture_entry["TextureIndex"])]
        texture_lines.append(
            f"{fixture_entry['TextureIndex']} ({encoded.width}x{encoded.height} "
            f"{encoded.format_name}, {len(encoded.image_data)} bytes, "
            f"mod32={len(encoded.image_data) % 32})"
        )
        binding_lines.extend(
            f"submesh {target['SubmeshIndex']} DS{target['DisplayStateIndex']} "
            f"via DS{target['TextureSetterDisplayStateIndex']} -> "
            f"texture {fixture_entry['TextureIndex']}"
            for target in fixture_entry["Targets"]
        )
    print(
        f"Fixture: {fixture_path}\n"
        "Textures: " + ", ".join(texture_lines) + "\n"
        "Bindings: " + ", ".join(binding_lines)
        + "\n"
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
        description="Build the Phase 0 appended-texture hammerspace fixture."
    )
    parser.add_argument("source", type=Path, help="Source .sluggie path")
    parser.add_argument("png", type=Path, help="New PNG inside the model tex directory")
    parser.add_argument("--output", type=Path, help="Generated fixture .sluggie path")
    parser.add_argument("--submesh", type=int, default=0)
    parser.add_argument("--display-state", type=int, default=5)
    parser.add_argument(
        "--additional-target",
        action="append",
        default=[],
        metavar="SUBMESH:DS",
        help="Additional draw state that shares the appended texture; repeatable",
    )
    parser.add_argument("--template-texture", type=int, default=0)
    parser.add_argument("--second-png", type=Path)
    parser.add_argument(
        "--second-target",
        metavar="SUBMESH:DS",
        help="Draw state assigned to --second-png",
    )
    parser.add_argument("--second-template-texture", type=int, default=0)
    parser.add_argument("--write", action="store_true", help="Install into output DAT/DOL/FST")
    args = parser.parse_args()

    source = args.source.resolve()
    png = args.png.resolve()
    try:
        additional_targets = []
        for value in args.additional_target:
            submesh_text, state_text = value.split(":", 1)
            additional_targets.append((int(submesh_text), int(state_text)))
    except ValueError:
        parser.error("--additional-target must use integer SUBMESH:DS syntax")

    if (args.second_png is None) != (args.second_target is None):
        parser.error("--second-png and --second-target must be used together")
    second_png = args.second_png.resolve() if args.second_png else None
    second_target = None
    if args.second_target:
        try:
            submesh_text, state_text = args.second_target.split(":", 1)
            second_target = (int(submesh_text), int(state_text))
        except ValueError:
            parser.error("--second-target must use integer SUBMESH:DS syntax")
    if second_png is not None and not second_png.is_file():
        parser.error(f"second PNG does not exist: {second_png}")

    target_suffix = "_".join(
        [f"sm{args.submesh}_ds{args.display_state}"]
        + [f"sm{submesh}_ds{state}" for submesh, state in additional_targets]
    )
    if second_target is not None:
        target_suffix += f"_sm{second_target[0]}_ds{second_target[1]}_alignment"
    output = (
        args.output.resolve()
        if args.output
        else (
            TOOLS_DIR.parent
            / "Debug"
            / "fixtures"
            / f"{source.stem}.additional_texture_{target_suffix}.sluggie"
        )
    )
    if source == output:
        parser.error("fixture output must differ from the source .sluggie")
    if not source.is_file():
        parser.error(f"source does not exist: {source}")
    if not png.is_file():
        parser.error(f"PNG does not exist: {png}")

    try:
        build_fixture(
            source,
            png,
            output,
            args.submesh,
            args.display_state,
            args.template_texture,
            args.write,
            additional_targets,
            second_png,
            second_target,
            args.second_template_texture,
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())