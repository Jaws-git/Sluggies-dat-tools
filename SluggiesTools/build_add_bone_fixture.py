"""PLAN_AddBones.md Phase 0 probes P2-P4: build a hammerspace fixture that
appends one inert leaf bone to a donor's ACT skeleton, optionally with a
custom submesh hosted on it (P3).

Unlike ``build_add_submesh_fixture.py``, this fixture does not go through
``HammerspaceMain.BuildModelBlock`` for its ACT section: ``SectionModes.act``
only ever supports ``'clone'`` (there is no ``BoneHierarchyEdited`` consumer
yet -- that is Phase 1), and appending a bone changes the ACT section's
*length*, which is not something a post-hoc byte patch (the pattern
``_apply_root_scale_patch`` / ``_apply_add_submesh_geo_id_patch`` use) can
do. Instead this script clones every section itself, rebuilds only ACT via
``act_rebuild.append_leaf_bone`` + ``act_rebuild.rebuild_act_bytes`` (the
Phase 0 "throwaway ACT writer"), and reassembles the block with
``HammerspaceMain.BuildHEADERModelBlock`` directly -- which already
recomputes every section offset from each section's own byte length
(PLAN_AddBones.md F9), so a longer ACT section needs no special-casing
there.

The new bone's SRT is a clone of its parent bone's own SRT record (a real,
validated 0x34-byte block: type byte, scale, quaternion, translation, then 8
reserved zero bytes -- see ``act_rebuild``'s module docstring for the layout)
with the translation replaced by ``--offset`` along local Z, so the new bone
sits a visible distance further out from its parent along the same general
direction its parent came from. Track stays ``0xFFFF`` (no animation), and
the mirror entry is ``(own_id, 3)`` (self-mirrored, ordinary bone) -- the
"obviously user-added, otherwise inert" shape PLAN_AddBones.md's user
contract calls for.

With ``--with-submesh`` (P3), the new bone also gets a GeoId: a byte-for-byte
value clone of an existing rigid submesh (default template: ``head``,
reusing ``build_add_submesh_fixture``'s clone/clear-source-layout helpers --
see ``PLAN_AddSubmesh.md``) is appended to ``Submeshes`` and the new bone's
``geo_file_id_raw`` is set to that submesh's index directly when it is
constructed, instead of the donor-bone ``GeoIdEdited`` patch path
(``_apply_geo_id_patches``) -- that path iterates *donor* ``BoneHierarchy``
entries only, and the new bone doesn't have one. Without ``--with-submesh``
GeoId is ``0xFFFF`` (unowned, no mesh) -- P2's plain inert-leaf case.

``--bulk N`` (P4) repeats the P2 append N times in a single ACT rebuild
instead of once: every new bone is parented directly to ``--parent-bone``
(not chained to each other -- each is an independent leaf, exercising the
"append to the end of an already-nonempty child chain" path in
``act_rebuild.append_leaf_bone`` for bones 2..N), and fanned out along the
parent's local Z (``offset``, ``2*offset``, ...) so they don't all sit on
top of each other. This probes for a fixed-size runtime bone-matrix
allocation rather than the mechanism itself (already proven by P2/P3). If
``--with-submesh`` is combined with ``--bulk``, only the *last* appended
bone (the one furthest from the donor's own bone table) gets the cloned
submesh, giving a single visible liveness check at the far end of the
bulk-appended range without building N submeshes.
"""
from __future__ import annotations

import argparse
import copy
import json
import struct
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
HAMMERSPACE_DIR = TOOLS_DIR / "Hammerspace"
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import act_rebuild
import build_add_submesh_fixture as submesh_fixture
import HammerspaceMain as hammerspace
import HammerspaceHelper as hh


def _bone_by_id(bone_hierarchy: list[dict], bone_id: int) -> dict:
    for bone in bone_hierarchy:
        if int(bone["BoneId"]) == bone_id:
            return bone
    raise ValueError(f"bone {bone_id} not found in BoneHierarchy")


def _clone_parent_srt_with_offset(
    act_bytes: bytes, parsed: act_rebuild.ACTParsed, parent_id: int, offset: float,
) -> bytes:
    """Clone parent_id's own SRT blob, replacing its translation with (0, 0, offset).

    Keeps the parent's real type byte/scale/quaternion (so the new bone's
    local orientation matches its parent's own local frame) and the 8
    reserved zero bytes at the tail, only touching the translation floats.
    Raises if the parent has no SRT (a null-orientation bone -- identity
    local transform, per F8) since there is nothing to clone from.
    """
    parent = next(b for b in parsed.bones if b.id == parent_id)
    if parent.orientation_ptr == 0:
        raise ValueError(
            f"parent bone {parent_id} has no SRT (null orientation, F8); "
            "nothing to clone the new bone's local frame from"
        )
    srt_index = [b.id for b in parsed.bones if b.orientation_ptr != 0].index(parent_id)
    template = bytearray(parsed.srt_blobs[srt_index])
    struct.pack_into(">3f", template, 4 + 12 + 16, 0.0, 0.0, offset)
    return bytes(template)


def prepare_fixture_data(
    source_data: dict,
    parent_bone_id: int,
    offset: float,
    template_submesh: int | str | None = None,
    bulk_count: int = 1,
) -> dict:
    """Deep-copy *source_data*, strip donor edits, and set UseHammerspace.

    Does not touch ACT at all -- unlike ``build_add_submesh_fixture.py``'s
    GPL append, the new bone is not representable in today's
    ``BoneHierarchy``/``BoneHierarchyEdited`` schema (Phase 1 hasn't run),
    so the actual append happens directly on the cloned ACT bytes in
    ``build_fixture``. This function only prepares the JSON half of the
    fixture (for round-tripping the donor description and documenting what
    the probe did), and validates the parent bone exists.

    When *template_submesh* is given (P3), also appends a value clone of
    that submesh to ``Submeshes`` (via ``build_add_submesh_fixture``'s
    template-clone helpers) -- the GPL side of the probe. The new bone's own
    ``GeoId`` is set later, directly on the ``BoneRecord`` in
    ``build_fixture``, since it has no ``BoneHierarchy`` entry to carry a
    ``GeoIdEdited`` field.
    """
    data = copy.deepcopy(source_data)
    model = data["SluggiesModel"]
    _strip_donor_edits(model)
    bone_hierarchy = model.get("BoneHierarchy") or []
    parent = _bone_by_id(bone_hierarchy, parent_bone_id)
    model["UseHammerspace"] = True

    new_submesh_index = None
    template_submesh_index = None
    if template_submesh is not None:
        submeshes = model.get("Submeshes")
        if not submeshes:
            raise ValueError("source model has no Submeshes")
        template_submesh_index = submesh_fixture._find_template_submesh_index(submeshes, template_submesh)
        template = submeshes[template_submesh_index]
        template_vb = template.get("VertexBuffer") or {}
        if int(template_vb.get("VertexBufferCompCount", 0)) != 3:
            raise ValueError(
                f"template submesh {template_submesh_index} ({template.get('MeshName')!r}) is not "
                "rigid (VertexBufferCompCount != 3); only a rigid submesh can be cloned for a "
                "bone-attached probe"
            )
        clone = copy.deepcopy(template)
        submesh_fixture._clear_source_layout_fields(clone)
        new_submesh_index = len(submeshes)
        submeshes.append(clone)

    model["AddBoneFixture"] = {
        "ParentBoneId": parent_bone_id,
        "ParentBoneName": parent.get("BoneName"),
        "TranslationOffset": offset,
        "TrackId": 0xFFFF,
        "MirrorRole": 3,
        "BulkCount": bulk_count,
        "NewSubmeshIndex": new_submesh_index,
        "TemplateSubmeshIndex": template_submesh_index,
        "GeoId": new_submesh_index if new_submesh_index is not None else 0xFFFF,
    }
    return data


def _strip_donor_edits(node) -> None:
    if isinstance(node, dict):
        for key in [k for k in node if k.endswith("Edited")]:
            del node[key]
        for value in node.values():
            _strip_donor_edits(value)
    elif isinstance(node, list):
        for value in node:
            _strip_donor_edits(value)


def build_fixture(
    source_path: Path,
    fixture_path: Path,
    parent_bone_id: int,
    offset: float,
    write: bool,
    template_submesh: int | str | None = None,
    bulk_count: int = 1,
    bad_mirror_target: int | None = None,
) -> tuple[hammerspace.ModelBlockBuild, int | None]:
    """bad_mirror_target: PLAN_AddBones.md Phase 0 P5. Points the new bone's
    mirror entry at an existing bone id instead of itself, deliberately
    breaking the involution (F3), to see how loudly the engine reacts to a
    corrupt mirror table. Only valid with bulk_count == 1 -- P5 is a single
    deliberately-bad bone, not a bulk probe."""
    if bulk_count < 1:
        raise ValueError(f"bulk_count must be >= 1, got {bulk_count}")
    if bad_mirror_target is not None and bulk_count != 1:
        raise ValueError("--bad-mirror is only supported with bulk_count == 1 (P5 is a single bone)")

    with source_path.open("r", encoding="utf-8") as source_file:
        source_data = json.load(source_file)

    data = prepare_fixture_data(source_data, parent_bone_id, offset, template_submesh, bulk_count)
    model = data["SluggiesModel"]
    fixture_meta = model["AddBoneFixture"]
    chunk_number = model["ChunkNumber"]
    file_index = model["FileIndex"]
    model_offset = hammerspace._hex(model.get("ModelOffset"))
    model_length = model.get("ModelLength")

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    with fixture_path.open("w", encoding="utf-8", newline="\n") as fixture_file:
        json.dump(data, fixture_file, indent=2)
        fixture_file.write("\n")

    if fixture_meta["NewSubmeshIndex"] is not None:
        gpl_bytes = hammerspace.BuildGPLMeshData(hammerspace.ParseSluggie(data)).gpl_bytes
    else:
        gpl_bytes = hammerspace.CloneGPL(model_offset, model_length)
    act_bytes = hammerspace.CloneACT(model_offset, model_length)
    if not act_bytes:
        raise ValueError(f"donor model at 0x{model_offset:08X} has no ACT section")
    parsed = act_rebuild.parse_act(act_bytes)
    act_rebuild.validate_mirror_table(parsed)

    if bad_mirror_target is not None and bad_mirror_target not in {b.id for b in parsed.bones}:
        raise ValueError(f"--bad-mirror target bone {bad_mirror_target} does not exist in the donor")

    first_new_bone_id = parsed.bone_count
    new_bone_ids: list[int] = []
    appended = parsed
    for i in range(bulk_count):
        is_last = i == bulk_count - 1
        geo_id_i = fixture_meta["GeoId"] if (is_last and fixture_meta["NewSubmeshIndex"] is not None) else 0xFFFF
        srt_blob = _clone_parent_srt_with_offset(act_bytes, parsed, parent_bone_id, offset * (i + 1))
        new_bone_ids.append(appended.bone_count)
        appended = act_rebuild.append_leaf_bone(
            appended, parent_bone_id, srt_blob, geo_file_id_raw=geo_id_i,
            mirror_bone_id=bad_mirror_target,
        )
    new_bone_id = new_bone_ids[0]
    new_act_bytes = act_rebuild.rebuild_act_bytes(appended)
    if bad_mirror_target is None:
        act_rebuild.validate_mirror_table(act_rebuild.parse_act(new_act_bytes))  # self-check
    else:
        # P5 deliberately produces a non-involution; confirm it's rejected
        # by the same validator that would guard a real build, then proceed
        # anyway -- that rejection *is* the probe's other half (Phase 3
        # will rely on this same check to block a user from shipping this).
        try:
            act_rebuild.validate_mirror_table(act_rebuild.parse_act(new_act_bytes))
        except act_rebuild.ACTMirrorTableError as exc:
            print(f"Self-check: validate_mirror_table correctly rejects this ACT ({exc})")
        else:
            raise ValueError(
                "--bad-mirror was given but validate_mirror_table accepted the result; "
                "the bad mirror entry didn't actually break the involution"
            )

    tex_bytes = hammerspace.CloneTEX(model_offset, model_length)
    skn_bytes = hammerspace.CloneSKN(model_offset, model_length)
    trailing_bytes, original_trailing_off = hammerspace.CloneTrailingSections(model_offset, model_length)
    original_header = hammerspace.CloneHEADER(model_offset)

    inner_block = hammerspace.BuildHEADERModelBlock(
        gpl_bytes, new_act_bytes, tex_bytes, skn_bytes,
        trailing_bytes=trailing_bytes, original_header=original_header,
        original_trailing_off=original_trailing_off,
    )

    gpl_mode = "build" if fixture_meta["NewSubmeshIndex"] is not None else "clone"
    modes = hammerspace.SectionModes(gpl=gpl_mode, act="clone", tex="clone", skn="clone", trailing="clone")
    section_sizes = {
        "GPL": len(gpl_bytes), "ACT": len(new_act_bytes),
        "TEX": len(tex_bytes), "SKN": len(skn_bytes), "trailing": len(trailing_bytes),
    }
    original_offset, original_length = hh.readDolEntry(chunk_number, file_index)
    report = hammerspace._build_validation_report(inner_block, original_length, modes, section_sizes)
    if not report["valid"]:
        raise ValueError(
            "fixture model block failed validation: " + "; ".join(report["errors"])
        )
    alignment = submesh_fixture.section_alignment_facts(inner_block)
    report["section_alignment"] = alignment
    if alignment["misaligned"]:
        raise ValueError(
            "build has entries off a 32-byte boundary (PLAN_AddSubmesh.md F10): "
            + "; ".join(alignment["misaligned"])
        )

    build = hammerspace.ModelBlockBuild(
        block=inner_block, parsed=None, chunk_number=chunk_number, file_index=file_index,
        original_offset=original_offset, original_length=original_length,
        section_modes=modes, section_sizes=section_sizes, validation_report=report,
    )

    output_offset = None
    if write:
        output_offset = hammerspace.WriteModelBlock(build, fixture_path.name)

    act_bone_count = report.get("validator_facts", {}).get("act_bone_count")
    geo_id = fixture_meta["GeoId"]
    submesh_summary = (
        f"submesh {fixture_meta['NewSubmeshIndex']} (template {fixture_meta['TemplateSubmeshIndex']})"
        if fixture_meta["NewSubmeshIndex"] is not None else "none (P2: unowned)"
    )
    if bulk_count == 1:
        mirror_target = new_bone_id if bad_mirror_target is None else bad_mirror_target
        mirror_note = "" if bad_mirror_target is None else " -- DELIBERATELY WRONG (P5 probe)"
        bone_summary = (
            f"New bone: id {new_bone_id}, parent {parent_bone_id}, translation offset "
            f"(0, 0, {offset:g}) along the parent's local Z, GeoId={geo_id:#06x}, track=0xFFFF, "
            f"mirror=({mirror_target}, 3){mirror_note}"
        )
    else:
        bone_summary = (
            f"New bones: ids {new_bone_ids[0]}-{new_bone_ids[-1]} ({bulk_count} total), all "
            f"parented to {parent_bone_id}, fanned out (0, 0, {offset:g}*n) along its local Z, "
            f"track=0xFFFF, self-mirrored role 3; only bone {new_bone_ids[-1]} carries "
            f"GeoId={geo_id:#06x}"
        )
    print(
        f"Fixture: {fixture_path}\n"
        f"{bone_summary}\n"
        f"Submesh: {submesh_summary}\n"
        f"GPL: {gpl_mode} mode, {len(gpl_bytes)} bytes\n"
        f"ACT: {len(act_bytes)} -> {len(new_act_bytes)} bytes "
        f"({parsed.bone_count} -> {appended.bone_count} bones), "
        f"validator bone tree walk: {act_bone_count}\n"
        f"Block: {len(inner_block)} bytes, delta {report['size_delta']:+d}, valid={report['valid']}"
    )
    if output_offset is not None:
        print(f"Installed at output DAT offset 0x{output_offset:08X}")
    else:
        print("Dry run only; output DAT/DOL/FST were not modified")
    return build, output_offset


def build_chain_fixture(
    source_path: Path,
    fixture_path: Path,
    parent_bone_id: int,
    offset: float,
    write: bool,
    chain_count: int,
    cube_half_extent: float,
    template_submesh: int | str = "head",
    cube_display_state: int | None = None,
) -> tuple[hammerspace.ModelBlockBuild, int | None]:
    """PLAN_AddBones.md Phase 0 P6: a chain of new bones, each parented to the
    previous one (not all to the same donor parent, unlike --bulk), each
    hosting its own small cube submesh so the chain's shape is visible in
    game. Exercises the "new bone parented to a new bone" path in
    ``act_rebuild.append_leaf_bone`` that P2-P5 never touch (their parent is
    always a donor bone), and gives per-link deformation something to show
    if bone-chain animation interacts badly with the append.

    Every link reuses the *same* SRT template -- ``parent_bone_id``'s own
    donor SRT with translation replaced by ``(0, 0, offset)`` -- so each new
    bone sits ``offset`` further from its own parent along that same local
    Z axis, extending in a straight line (assuming ``InheritTransform``)
    rather than fanning out like ``--bulk``'s siblings.
    """
    if chain_count < 2:
        raise ValueError(f"chain_count must be >= 2 (a chain of 1 is just P2), got {chain_count}")

    with source_path.open("r", encoding="utf-8") as source_file:
        source_data = json.load(source_file)

    data = copy.deepcopy(source_data)
    model = data["SluggiesModel"]
    _strip_donor_edits(model)
    bone_hierarchy = model.get("BoneHierarchy") or []
    _bone_by_id(bone_hierarchy, parent_bone_id)  # validate it exists
    model["UseHammerspace"] = True

    submeshes = model.get("Submeshes")
    if not submeshes:
        raise ValueError("source model has no Submeshes")
    template_index = submesh_fixture._find_template_submesh_index(submeshes, template_submesh)
    template = submeshes[template_index]
    template_vb = template.get("VertexBuffer") or {}
    if int(template_vb.get("VertexBufferCompCount", 0)) != 3:
        raise ValueError(
            f"template submesh {template_index} ({template.get('MeshName')!r}) is not rigid "
            "(VertexBufferCompCount != 3); only a rigid submesh can be cloned for a cube"
        )
    use_base64 = bool(model.get("UseBase64", True))

    new_submesh_indices: list[int] = []
    for _ in range(chain_count):
        clone = copy.deepcopy(template)
        submesh_fixture._clear_source_layout_fields(clone)
        submesh_fixture._replace_clone_with_cube(clone, cube_half_extent, cube_display_state, use_base64)
        new_submesh_indices.append(len(submeshes))
        submeshes.append(clone)

    chunk_number = model["ChunkNumber"]
    file_index = model["FileIndex"]
    model_offset = hammerspace._hex(model.get("ModelOffset"))
    model_length = model.get("ModelLength")

    model["AddBoneChainFixture"] = {
        "ParentBoneId": parent_bone_id,
        "ChainCount": chain_count,
        "TranslationOffset": offset,
        "CubeHalfExtent": cube_half_extent,
        "NewSubmeshIndices": new_submesh_indices,
        "TemplateSubmeshIndex": template_index,
    }

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    with fixture_path.open("w", encoding="utf-8", newline="\n") as fixture_file:
        json.dump(data, fixture_file, indent=2)
        fixture_file.write("\n")

    gpl_bytes = hammerspace.BuildGPLMeshData(hammerspace.ParseSluggie(data)).gpl_bytes
    act_bytes = hammerspace.CloneACT(model_offset, model_length)
    if not act_bytes:
        raise ValueError(f"donor model at 0x{model_offset:08X} has no ACT section")
    parsed = act_rebuild.parse_act(act_bytes)
    act_rebuild.validate_mirror_table(parsed)

    current_parent = parent_bone_id
    new_bone_ids: list[int] = []
    appended = parsed
    for submesh_index in new_submesh_indices:
        srt_blob = _clone_parent_srt_with_offset(act_bytes, parsed, parent_bone_id, offset)
        new_bone_id = appended.bone_count
        appended = act_rebuild.append_leaf_bone(
            appended, current_parent, srt_blob, geo_file_id_raw=submesh_index,
        )
        new_bone_ids.append(new_bone_id)
        current_parent = new_bone_id

    new_act_bytes = act_rebuild.rebuild_act_bytes(appended)
    act_rebuild.validate_mirror_table(act_rebuild.parse_act(new_act_bytes))  # self-check

    tex_bytes = hammerspace.CloneTEX(model_offset, model_length)
    skn_bytes = hammerspace.CloneSKN(model_offset, model_length)
    trailing_bytes, original_trailing_off = hammerspace.CloneTrailingSections(model_offset, model_length)
    original_header = hammerspace.CloneHEADER(model_offset)

    inner_block = hammerspace.BuildHEADERModelBlock(
        gpl_bytes, new_act_bytes, tex_bytes, skn_bytes,
        trailing_bytes=trailing_bytes, original_header=original_header,
        original_trailing_off=original_trailing_off,
    )

    modes = hammerspace.SectionModes(gpl="build", act="clone", tex="clone", skn="clone", trailing="clone")
    section_sizes = {
        "GPL": len(gpl_bytes), "ACT": len(new_act_bytes),
        "TEX": len(tex_bytes), "SKN": len(skn_bytes), "trailing": len(trailing_bytes),
    }
    original_offset, original_length = hh.readDolEntry(chunk_number, file_index)
    report = hammerspace._build_validation_report(inner_block, original_length, modes, section_sizes)
    if not report["valid"]:
        raise ValueError("fixture model block failed validation: " + "; ".join(report["errors"]))
    alignment = submesh_fixture.section_alignment_facts(inner_block)
    report["section_alignment"] = alignment
    if alignment["misaligned"]:
        raise ValueError(
            "build has entries off a 32-byte boundary (PLAN_AddSubmesh.md F10): "
            + "; ".join(alignment["misaligned"])
        )

    build = hammerspace.ModelBlockBuild(
        block=inner_block, parsed=None, chunk_number=chunk_number, file_index=file_index,
        original_offset=original_offset, original_length=original_length,
        section_modes=modes, section_sizes=section_sizes, validation_report=report,
    )

    output_offset = None
    if write:
        output_offset = hammerspace.WriteModelBlock(build, fixture_path.name)

    print(
        f"Fixture: {fixture_path}\n"
        f"Chain: bones {new_bone_ids[0]}-{new_bone_ids[-1]} ({chain_count} total), each "
        f"parented to the previous (first to donor bone {parent_bone_id}), each offset "
        f"(0, 0, {offset:g}) from its own parent, each hosting its own cube (half extent "
        f"{cube_half_extent:g}, submeshes {new_submesh_indices[0]}-{new_submesh_indices[-1]}), "
        f"track=0xFFFF, self-mirrored role 3\n"
        f"GPL: build mode, {len(gpl_bytes)} bytes\n"
        f"ACT: {len(act_bytes)} -> {len(new_act_bytes)} bytes "
        f"({parsed.bone_count} -> {appended.bone_count} bones)\n"
        f"Block: {len(inner_block)} bytes, delta {report['size_delta']:+d}, valid={report['valid']}"
    )
    if output_offset is not None:
        print(f"Installed at output DAT offset 0x{output_offset:08X}")
    else:
        print("Dry run only; output DAT/DOL/FST were not modified")
    return build, output_offset


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the PLAN_AddBones.md Phase 0 P2-P4 (leaf bone, optionally with a "
                     "custom submesh) fixture."
    )
    parser.add_argument("source", type=Path, help="Source .sluggie path (e.g. an exported Mario)")
    parser.add_argument("--output", type=Path, help="Generated fixture .sluggie path")
    parser.add_argument(
        "--parent-bone", type=int, default=3,
        help="Bone ID the new leaf bone is parented to (default: 3, Mario's spine)",
    )
    parser.add_argument(
        "--offset", type=float, default=0.3,
        help="Translation offset (local units) along the parent's local Z, giving the new "
             "bone a real, visible-distance SRT (default: 0.3)",
    )
    parser.add_argument(
        "--with-submesh", nargs="?", const="head", default=None, metavar="TEMPLATE",
        help="P3: give the new bone a GeoId by cloning TEMPLATE (name or index; default: "
             "'head') as a new rigid submesh hosted on it. Omit for P2's plain inert leaf. "
             "With --bulk, only the last appended bone gets the submesh.",
    )
    parser.add_argument(
        "--bulk", type=int, default=1, metavar="N",
        help="P4: append N leaf bones instead of one, all parented to --parent-bone and "
             "fanned out along its local Z (default: 1, i.e. P2/P3's single-bone case)",
    )
    parser.add_argument(
        "--bad-mirror", type=int, default=None, metavar="BONE_ID",
        help="P5: deliberately mirror the new bone to BONE_ID (an existing donor bone) "
             "instead of itself, breaking the kind-2 involution (F3), to observe how the "
             "engine reacts. Only valid without --bulk.",
    )
    parser.add_argument(
        "--chain", type=int, default=None, metavar="N",
        help="P6: append a chain of N new bones (N >= 2), each parented to the previous one "
             "(unlike --bulk's siblings) and each hosting its own small cube submesh so the "
             "chain's shape is visible in game. Mutually exclusive with --bulk, --with-submesh, "
             "--bad-mirror.",
    )
    parser.add_argument(
        "--cube-half-extent", type=float, default=0.15, metavar="EXTENT",
        help="--chain only: half extent of each link's cube, in local units (default: 0.15)",
    )
    parser.add_argument("--write", action="store_true", help="Install into output DAT/DOL/FST")
    args = parser.parse_args()

    source = args.source.resolve()
    if not source.is_file():
        parser.error(f"source does not exist: {source}")

    template_submesh: int | str | None = args.with_submesh
    if template_submesh is not None:
        try:
            template_submesh = int(template_submesh)
        except ValueError:
            pass

    if args.bulk < 1:
        parser.error("--bulk must be >= 1")
    if args.bad_mirror is not None and args.bulk != 1:
        parser.error("--bad-mirror is only supported with --bulk 1 (P5 is a single bone)")
    if args.chain is not None:
        if args.chain < 2:
            parser.error("--chain must be >= 2")
        if args.bulk != 1 or args.with_submesh is not None or args.bad_mirror is not None:
            parser.error("--chain is mutually exclusive with --bulk, --with-submesh, --bad-mirror")

    if args.chain is not None:
        output = (
            args.output.resolve()
            if args.output
            else (
                TOOLS_DIR.parent / "Debug" / "fixtures"
                / f"{source.stem}.add_bone_chain{args.chain}_parent{args.parent_bone}.sluggie"
            )
        )
        if source == output:
            parser.error("fixture output must differ from the source .sluggie")
        try:
            build_chain_fixture(
                source, output, args.parent_bone, args.offset, args.write,
                args.chain, args.cube_half_extent,
            )
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            parser.exit(1, f"error: {exc}\n")
        return 0

    suffix = f".add_bone_parent{args.parent_bone}"
    if args.bulk > 1:
        suffix += f"_bulk{args.bulk}"
    if args.with_submesh is not None:
        suffix += f"_submesh{args.with_submesh}"
    if args.bad_mirror is not None:
        suffix += f"_badmirror{args.bad_mirror}"
    output = (
        args.output.resolve()
        if args.output
        else (TOOLS_DIR.parent / "Debug" / "fixtures" / f"{source.stem}{suffix}.sluggie")
    )
    if source == output:
        parser.error("fixture output must differ from the source .sluggie")

    try:
        build_fixture(
            source, output, args.parent_bone, args.offset, args.write, template_submesh, args.bulk,
            args.bad_mirror,
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
