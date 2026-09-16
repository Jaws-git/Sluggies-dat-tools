"""Rebuild the Phase 0 probe-4 cube fixture through the Phase 2 builder.

Unlike build_add_submesh_fixture.py (Phase 0's hand-cloned-into-Submeshes[]
probe script), this exercises the real Phase 1/2 pipeline: a genuine
CustomSubmeshes entry, parsed by HammerspaceMain.ParseSluggie and appended by
HammerspaceMain.PatchGPLAppendSubmesh. GeoId patching is Phase 3's job and
doesn't exist in HammerspaceMain yet, so this still borrows
build_add_submesh_fixture._apply_add_submesh_geo_id_patch as a stopgap, same
as Phase 0 did.

Usage:
    uv run --project SluggiesTools/_build python SluggiesTools/build_custom_submesh_fixture.py [--write]
"""

from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_add_submesh_fixture as probe
from build_add_submesh_fixture import hammerspace

REAL_MARIO_SLUGGIE = (
    TOOLS_DIR.parent / '2_Output_Models' / '18 Mario' / '78277664_mario.gpl' / '78277664_mario.gpl.sluggie'
)
HOST_BONE_ID = 49  # Same bone Phase 0 probe 4 used: mesh-free, SKN-used leaf of hand bone 28.
HALF_EXTENT = 0.1


def _s16(values):
    return struct.pack(f'>{len(values)}h', *values)


def _encode(data: bytes, use_b64: bool):
    return base64.b64encode(data).decode('ascii') if use_b64 else list(data)


def _build_cube_entry(use_b64: bool, position_unit: int) -> dict:
    extent = round(HALF_EXTENT * position_unit)
    corners = []
    for i in range(8):
        corners += [
            extent if i & 4 else -extent,
            extent if i & 2 else -extent,
            extent if i & 1 else -extent,
        ]
    cube_faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    face_indices = []
    for a, b, c, d in cube_faces:
        face_indices += [a, b, c, a, c, d]
    faces_count = len(face_indices) // 3
    faces_data = struct.pack(f'>{len(face_indices)}H', *face_indices)

    normal_dirs = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    normal_data = _s16([int(round(c * 16000)) for n in normal_dirs for c in n])
    normal_faces = struct.pack(f'>{faces_count * 3}H', *(face_i for face_i in range(6) for _ in range(6)))

    uv_pairs = []
    for i in range(8):
        uv_pairs += [100 if i & 4 else 0, 100 if i & 2 else 0]
    uv_bytes = _s16(uv_pairs)

    return {
        'CustomSubmeshId': 'custom0',
        'MeshName': 'CustomSubmesh_0',
        'HostBoneId': HOST_BONE_ID,
        'TemplateSource': 'builtin:rigid_spec_v1',
        'VertexBufferData': _encode(_s16(corners), use_b64),
        'NormalBufferData': _encode(normal_data, use_b64),
        'NormalFacesData': _encode(normal_faces, use_b64),
        'ColorChannelData': _encode(bytes([255, 255, 255, 255]), use_b64),
        'ColorFacesData': _encode(struct.pack(f'>{faces_count * 3}H', *([0] * (faces_count * 3))), use_b64),
        'FacesCount': faces_count,
        'FacesData': _encode(faces_data, use_b64),
        'UVChannels': [
            {
                'UVChannelIndex': i,
                'UVChannelData': _encode(uv_bytes, use_b64),
                'UVFacesData': _encode(faces_data, use_b64),
            }
            for i in range(2)
        ],
        'TextureAssignment': {'DonorTextureIndex': 0},
    }


def build(write: bool) -> None:
    with REAL_MARIO_SLUGGIE.open('r', encoding='utf-8') as source_file:
        data = json.load(source_file)
    model = data['SluggiesModel']
    use_b64 = model.get('UseBase64', True)
    model['UseHammerspace'] = True

    bones_by_id = {int(b['BoneId']): b for b in model['BoneHierarchy']}
    host_bone = bones_by_id[HOST_BONE_ID]
    raw_geo_id = hammerspace._bone_geo_id_raw(host_bone)
    if raw_geo_id != 0xFFFF:
        raise SystemExit(f'host bone {HOST_BONE_ID} already owns submesh {raw_geo_id}')
    geo_id_field_offset = host_bone.get('GeoIdFieldOffset')
    if not geo_id_field_offset:
        raise SystemExit(f'host bone {HOST_BONE_ID} is missing GeoIdFieldOffset metadata')

    donor_count = len(model['Submeshes'])
    new_submesh_index = donor_count

    entry = _build_cube_entry(use_b64, position_unit=1 << (59 & 0xF))  # QuantizeInfo 59 -> 1<<11 = 2048
    model['CustomSubmeshes'] = [entry]

    fixture_path = (
        TOOLS_DIR.parent / 'Debug' / 'fixtures'
        / f'78277664_mario.gpl.custom_submesh_builtin_host{HOST_BONE_ID}_cube{HALF_EXTENT:g}.sluggie'
    )
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    with fixture_path.open('w', encoding='utf-8', newline='\n') as fixture_file:
        json.dump(data, fixture_file, indent=2)
        fixture_file.write('\n')

    modes = hammerspace.SectionModes(gpl='build', act='clone', tex='clone', skn='clone', trailing='clone')
    build_result = hammerspace.BuildModelBlock(data, modes, sluggie_path=fixture_path)
    if not build_result.validation_report['valid']:
        raise SystemExit(
            'block failed validation before the GeoId patch: '
            + '; '.join(build_result.validation_report['errors'])
        )

    offset_field = model.get('ModelOffset', 0)
    source_model_offset = int(offset_field, 16) if isinstance(offset_field, str) else int(offset_field)
    build_result = probe._apply_add_submesh_geo_id_patch(
        build_result, source_model_offset, HOST_BONE_ID, geo_id_field_offset, new_submesh_index,
    )
    if not build_result.validation_report['valid']:
        raise SystemExit(
            'block failed validation after the GeoId patch: '
            + '; '.join(build_result.validation_report['errors'])
        )

    prefix = int(build_result.validation_report.get('container_prefix_size', 0))
    alignment = probe.section_alignment_facts(build_result.block[prefix:])
    print(
        f'Fixture: {fixture_path}\n'
        f'New submesh: index {new_submesh_index} (builtin:rigid_spec_v1, host bone {HOST_BONE_ID})\n'
        f'Half extent: {HALF_EXTENT} (matches Phase 0 probe 4)\n'
        f'Block: {len(build_result.block):,} bytes, valid={build_result.validation_report["valid"]}\n'
        f'{probe._format_alignment_summary(alignment)}'
    )

    if write:
        output_offset = hammerspace.WriteModelBlock(build_result, fixture_path.name)
        print(f'Installed at output DAT offset 0x{output_offset:08X}')
    else:
        print('Dry run only; pass --write to install into hammerspace (3_Output_Dat)')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Install the result into hammerspace')
    args = parser.parse_args()
    build(args.write)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
