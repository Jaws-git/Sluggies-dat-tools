"""PLAN_AddBones.md Phase 2 manual gate: a known-good PLAN_AddSubmesh.md
fixture (a `CustomSubmeshes` cube, `build_custom_submesh_fixture.py`'s own
mechanism -- real `PatchGPLAppendSubmesh` + `_apply_geo_id_patches`/
`_rebuild_act_bone_hierarchy`, not a hand-patched byte offset), rebuilt so
its host is a brand new leaf bone instead of a donor one.

This is the one thing Phase 0's fixtures (`build_add_bone_fixture.py`) never
exercised: they bypassed `HammerspaceMain.BuildModelBlock` entirely (cloning
every section by hand and calling `BuildHEADERModelBlock` directly), because
at the time `BoneHierarchyEdited` had no consumer. Now that
`BuildACTBoneHierarchy` picks its rebuild route from `BoneHierarchyEdited`
and owns a new bone's `GeoId` directly when a `CustomSubmeshes` entry names
it as `HostBoneId` (`HammerspaceMain._rebuild_act_bone_hierarchy`), this
script drives that path through the real, unmodified `BuildModelBlock` --
the same call `build_custom_submesh_fixture.py` and every real hammerspace
export ultimately makes.

Everything else about the model is left donor-identical: `BoneHierarchyEdited`
carries every existing bone forward unchanged (`UserAdded: false`), with its
real mirror/track table decoded straight from the donor ACT bytes via
`act_rebuild` (the checked-in Mario `.sluggie` predates Phase 1's own
`MirrorBoneId`/`MirrorRole` export, so this is the same ground truth Phase 1's
exporter itself would read). One new leaf bone is appended to the spine
(`--parent-bone`, default 3) with a real translation offset along its local Z
(`--offset`), exactly like Phase 0 P2's already-Dolphin-validated bone, and a
12-triangle cube (`build_custom_submesh_fixture.py`'s own builtin-template
cube, `builtin:rigid_spec_v1`) is hosted on it.

Usage:
    python build_add_bone_custom_submesh_fixture.py            # dry run
    python build_add_bone_custom_submesh_fixture.py --write    # install
"""
from __future__ import annotations

import argparse
import copy
import json
import struct
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent
ADDON_DIR = ROOT_DIR / "BlenderAddonSrc"
for import_path in (TOOLS_DIR, TOOLS_DIR / "Hammerspace", ADDON_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import act_rebuild  # noqa: E402
import CustomSubmeshExport as cse  # noqa: E402
import build_add_submesh_fixture as probe  # noqa: E402
import build_custom_submesh_fixture as csf  # noqa: E402
from build_add_submesh_fixture import hammerspace  # noqa: E402

DEFAULT_SOURCE = (
    ROOT_DIR / "2_Output_Models" / "18 Mario" / "78277664_mario.gpl" / "78277664_mario.gpl.sluggie"
)
DEFAULT_FIXTURE = (
    ROOT_DIR / "Debug" / "fixtures" / "78277664_mario.gpl.add_bone_custom_submesh.sluggie"
)


def _decode_mirror_track_table(act_bytes: bytes):
    """Ground-truth per-bone (MirrorBoneId, MirrorRole, TrackId), decoded
    straight from the donor ACT bytes (see module docstring: the checked-in
    .sluggie predates Phase 1's own export of these fields)."""
    parsed = act_rebuild.parse_act(act_bytes)
    act_rebuild.validate_mirror_table(parsed)
    mirror_desc = next(d for d in parsed.user_data if d.kind == act_rebuild.KIND_MIRROR)
    track_desc = next(d for d in parsed.user_data if d.kind == act_rebuild.KIND_TRACK)
    table = {}
    for bone_id in range(parsed.bone_count):
        mirror_bone_id = mirror_desc.payload[2 * bone_id]
        mirror_role = mirror_desc.payload[2 * bone_id + 1]
        track_id, = struct.unpack_from('>H', track_desc.payload, 2 * bone_id)
        table[bone_id] = (mirror_bone_id, mirror_role, track_id)
    return parsed, table


def _build_bone_hierarchy_edited(model: dict, mirror_track_table: dict) -> list[dict]:
    edited = []
    for bone in model['BoneHierarchy']:
        bone_id = int(bone['BoneId'])
        mirror_bone_id, mirror_role, track_id = mirror_track_table[bone_id]
        edited.append({
            'BoneId': bone_id,
            'GeoId': bone['GeoId'],
            'ParentBoneId': bone['ParentBoneId'],
            'Skinned': bone['Skinned'],
            'TrackId': track_id,
            'MirrorBoneId': mirror_bone_id,
            'MirrorRole': mirror_role,
            'SRTType': bone['SRTType'],
            'DrawPriority': bone['DrawPriority'],
            'InheritTransform': bone['InheritTransform'],
            'UserAdded': False,
            'Translation': bone['Translation'],
            'Scale': bone['Scale'],
            'Quaternion': bone['Quaternion'],
            'VertexInfluences': bone['VertexInfluences'],
        })
    return edited


def prepare_fixture_data(
    source_data: dict, parent_bone_id: int, offset: float, template_source: str, texture_index: int,
) -> dict:
    data = copy.deepcopy(source_data)
    model = data['SluggiesModel']
    probe.strip_donor_edits(model)
    model.pop('AdditionalTextureDescriptors', None)
    model.pop('DesiredTextureAssignments', None)
    model['UseHammerspace'] = True
    model['ReimportTextures'] = False

    donor_by_id = {int(b['BoneId']): b for b in model['BoneHierarchy']}
    if parent_bone_id not in donor_by_id:
        raise ValueError(f"parent bone {parent_bone_id} does not exist in BoneHierarchy")

    source_model_offset = hammerspace._hex(model['ModelOffset'])
    source_model_length = model['ModelLength']
    donor_act_bytes = hammerspace.CloneACT(source_model_offset, source_model_length)
    if not donor_act_bytes:
        raise ValueError('source model has no ACT section')
    donor_parsed, mirror_track_table = _decode_mirror_track_table(donor_act_bytes)

    new_id = donor_parsed.bone_count
    bone_hierarchy_edited = _build_bone_hierarchy_edited(model, mirror_track_table)
    parent_srt_type = int(donor_by_id[parent_bone_id]['SRTType']) or 0xC
    new_bone_local = {
        'BoneId': new_id,
        'GeoId': 0xFFFF,
        'ParentBoneId': parent_bone_id,
        'Skinned': False,
        'TrackId': 0xFFFF,
        'MirrorBoneId': new_id,
        'MirrorRole': 3,
        'SRTType': parent_srt_type,
        'DrawPriority': 0,
        'InheritTransform': True,
        'UserAdded': True,
        'Translation': [0.0, 0.0, offset],
        'Scale': [1.0, 1.0, 1.0],
        'Quaternion': [1.0, 0.0, 0.0, 0.0],
        'VertexInfluences': [],
    }
    bone_hierarchy_edited.append(new_bone_local)
    model['BoneHierarchyEdited'] = bone_hierarchy_edited

    # Place the cube at the new bone's own bind position: parent's real
    # absolute bind matrix (from the model's own donor BoneHierarchy) times
    # the new bone's local T/R/S -- the new bone has no BoneHierarchy entry
    # for cse.bone_absolute_matrices to find, so its absolute matrix is
    # computed by hand the same way that helper computes every other bone's.
    absolute = cse.bone_absolute_matrices(model['BoneHierarchy'])
    parent_absolute = absolute[parent_bone_id]
    new_absolute = cse.mat4_mul(parent_absolute, cse.bone_local_matrix(new_bone_local))

    use_base64 = bool(model.get('UseBase64', True))
    geometry = cse.bone_local_geometry(
        csf.CUBE_COS, csf._cube_triangles(), new_absolute, csf.IDENTITY, new_absolute,
    )
    plan = cse.attribute_plan(model, template_source)
    normals, uvs, colors = csf._cube_loop_attributes()
    donor_count = len(model['Submeshes'])
    entry = cse.build_custom_submesh_entry(
        f'cube_b{new_id}', 'custom0', new_id, template_source, plan,
        geometry, normals, uvs, colors, {'DonorTextureIndex': texture_index}, use_base64,
    )
    model['CustomSubmeshes'] = [entry]

    model['AddBoneCustomSubmeshFixture'] = {
        'ParentBoneId': parent_bone_id,
        'NewBoneId': new_id,
        'TranslationOffset': offset,
        'TemplateSource': template_source,
        'TextureIndex': texture_index,
        'NewSubmeshIndex': donor_count,
    }
    return data


def build(
    source_path: Path, fixture_path: Path, parent_bone_id: int, offset: float,
    template_source: str, texture_index: int, write: bool,
):
    with source_path.open('r', encoding='utf-8') as source_file:
        source_data = json.load(source_file)
    data = prepare_fixture_data(source_data, parent_bone_id, offset, template_source, texture_index)
    model = data['SluggiesModel']
    fixture_meta = model['AddBoneCustomSubmeshFixture']
    new_bone_id = fixture_meta['NewBoneId']
    new_submesh_index = fixture_meta['NewSubmeshIndex']

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    with fixture_path.open('w', encoding='utf-8', newline='\n') as fixture_file:
        json.dump(data, fixture_file, indent=2)
        fixture_file.write('\n')

    modes = hammerspace.SectionModes(gpl='build', act='clone', tex='clone', skn='clone', trailing='clone')
    result = hammerspace.BuildModelBlock(data, modes, sluggie_path=fixture_path)
    report = result.validation_report
    if not report['valid']:
        raise ValueError('fixture block failed validation: ' + '; '.join(report['errors']))
    probe.require_built_submesh_count(result, new_submesh_index + 1)

    facts = report['validator_facts']
    act_bone_count = facts.get('act_bone_count')
    donor_bone_count = len(model['BoneHierarchy'])
    if act_bone_count != donor_bone_count + 1:
        raise ValueError(
            f'assembled ACT has {act_bone_count} bones, expected {donor_bone_count + 1} '
            f'(donor {donor_bone_count} + 1 new leaf)'
        )

    owners = facts.get('act_geo_id_owners', {})
    owner = owners.get(new_submesh_index)
    if owner != [new_bone_id]:
        raise ValueError(
            f'submesh {new_submesh_index} owner is {owner}, expected [{new_bone_id}] '
            '(the new leaf bone)'
        )

    prefix = int(report.get('container_prefix_size', 0))
    alignment = probe.section_alignment_facts(result.block[prefix:])
    report['section_alignment'] = alignment
    if alignment['misaligned']:
        raise ValueError('block is off a 32-byte boundary: ' + '; '.join(alignment['misaligned']))

    lines = [
        f'Fixture: {fixture_path}',
        f"New bone: id {new_bone_id}, parent {parent_bone_id}, translation offset "
        f"(0, 0, {offset:g}) along the parent's local Z, track=0xFFFF, mirror=({new_bone_id}, 3)",
        f'New submesh: index {new_submesh_index} ({fixture_meta["TemplateSource"]}, '
        f'texture {texture_index}) owned by bone {new_bone_id} (owner check: ok)',
        f'ACT: {donor_bone_count} -> {act_bone_count} bones',
        probe._format_alignment_summary(alignment),
        f"Block: {len(result.block)} bytes, delta {len(result.block) - result.original_length:+d}, "
        f"valid={report['valid']}",
    ]
    if write:
        offset_written = hammerspace.WriteModelBlock(result, fixture_path.name)
        lines.append(
            f'Installed at output DAT offset 0x{offset_written:08X}' if offset_written is not None
            else 'Installed into 3_Output_Dat'
        )
    else:
        lines.append('Dry run only; output DAT/DOL/FST were not modified')
    print('\n'.join(lines))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE, help='Donor .sluggie export')
    parser.add_argument('--fixture', type=Path, default=DEFAULT_FIXTURE, help='Fixture .sluggie to write')
    parser.add_argument(
        '--parent-bone', type=int, default=3,
        help="Bone ID the new leaf bone is parented to (default: 3, Mario's spine)",
    )
    parser.add_argument(
        '--offset', type=float, default=0.3,
        help='Translation offset (local units) along the parent local Z (default: 0.3, matches '
             'Phase 0 P2/P3)',
    )
    parser.add_argument(
        '--template-source', default='builtin:rigid_spec_v1',
        help="CustomSubmeshes TemplateSource (default: 'builtin:rigid_spec_v1', needs no "
             'donor rigid surface to exist)',
    )
    parser.add_argument('--texture-index', type=int, default=0, help='Donor texture index for the cube (default: 0, body)')
    parser.add_argument('--write', action='store_true', help='Install into output DAT/DOL/FST')
    args = parser.parse_args()

    source = args.source.resolve()
    if not source.is_file():
        parser.error(f'source does not exist: {source}')
    fixture = args.fixture.resolve()
    if fixture == source:
        parser.error('fixture output must differ from the source .sluggie')

    try:
        build(
            source, fixture, args.parent_bone, args.offset, args.template_source,
            args.texture_index, args.write,
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        parser.exit(1, f'error: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
