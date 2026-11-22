"""Hammerspace model replacement: the command line and the import name of the build.

``start.py`` runs this module as a script (``main``). The build itself lives in the sibling modules:
``SluggieModel`` (parsed ``.sluggie``), ``Donor`` (donor reads and section clones),
``HammerspaceValidate``, ``SubmeshTemplates``, ``RigidSubmesh``, ``SkinnedSubmesh``, ``GplPatch``,
``ActBuild``, ``TexBuild``, ``SknBuild``, ``BlockAssembly`` (``BuildModelBlock``) and ``RouteWriter``
(``WriteModelBlock``). Their public names, and the private ones tests and tools reach through this
module, are imported here (``__all__``). A test that replaces a function patches it on the module
that calls it, not here.
"""

import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(__file__))

# Initialize universal logger in child process.
_HS_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if _HS_TOOLS_DIR not in sys.path:
    sys.path.insert(0, _HS_TOOLS_DIR)

import slogger as _slogger
_slogger.configure()

import drawlist
import HammerspaceHelper as hh
import LodPartnerGuard
import LodTextureSync
import SlotTarget
import UntangledTextures
import UntanglePolicy
from binfmt import (
    align4 as _align4,
    color_entry_size as _color_entry_size,
    comp_size as _vb_comp_size,
    decode_field as _decode,
    load_sluggie,
)

from SluggieModel import (
    DrawState,
    NormalBuffer,
    UVChannel,
    ColorChannel,
    Submesh,
    MeshData,
    CustomSubmeshUVChannel,
    CustomSubmeshTextureAssignment,
    CustomSubmesh,
    RigidRebuildNewSurface,
    RigidRebuild,
    SkinnedRebuild,
    Bone,
    BoneData,
    Texture,
    TextureData,
    SK1,
    SK2,
    SKAcc,
    SkinningData,
    TrailingSection,
    SluggieParsed,
    STADIUM_CHUNKS,
    SectionModes,
    _parse_skinning,
    ParseSluggie,
    EXCLUDED_KEY,
    excluded_submesh_indices,
)
from Donor import (
    DONOR_ENTRY_KEY,
    donor_entry,
    Donor,
    CloneGPL,
    CloneACT,
    CloneTEX,
    CloneSKN,
    CloneTrailingSections,
    CloneHEADER,
    _rebase_absolute_offsets,
    _live_archive,
)
from SubmeshTemplates import (
    _CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES,
    _CUSTOM_SUBMESH_BUILTIN_TEMPLATES,
    builtin_template_names,
    _custom_submesh_state_records_sha256,
    _CUSTOM_SUBMESH_TYPE4_BY_UV_COUNT,
    _custom_submesh_texture_layer,
    _custom_submesh_with_texture_index,
    _custom_submesh_type3_descriptors,
    _custom_submesh_type3_setting,
    _custom_submesh_builtin_records,
    _custom_submesh_builtin_layer_count,
    _resolve_custom_submesh_records,
    _custom_submesh_patch_layer0_texture,
    _CUSTOM_SUBMESH_SPECULAR_SHADER_MODES,
    _custom_submesh_patch_specular_strength,
)
from HammerspaceValidate import (
    _bone_geo_id_raw,
    _custom_submesh_rigid_surfaces,
    _validate_custom_submeshes,
    ADDED_TRACK_IDS,
    _allowed_skin_bones,
    _validate_bone_hierarchy_edited,
    _validate_skin_bones,
    _follow_position_shift,
    _rigid_rebuild_drawable_surfaces,
    _validate_rigid_rebuilds,
    _validate_skinned_rebuild,
)
from RigidSubmesh import (
    _assemble_rigid_submesh,
    _custom_submesh_to_submesh,
    _build_custom_submesh,
    _build_rigid_submesh_blob,
    _submesh_position_half_extents,
    _warn_stadium_custom_submesh_alpha,
    _gpl_user_data_bounds_descriptor,
    PatchGPLAppendSubmesh,
    _splice_blobs_before_user_data,
    PatchGPLReplaceRigidSubmeshes,
)
from SkinnedSubmesh import PatchGPLReplaceSkinnedSubmesh
from GplPatch import (
    _position_edits,
    PatchGPLMaterialStates,
    PatchGPLExcludeSubmeshes,
    PatchGPLPositionArrays,
    PatchGPLUVArrays,
    PatchGPLUVRebuild,
)
from ActBuild import BuildACTBoneHierarchy, _apply_root_scale_patch, _apply_geo_id_patches
from TexBuild import _merge_duplicate_texture_additions, BuildTEX, _texture_replacement_overrides
from SknBuild import _scale_skn_bind_pose, BuildSKNSkinningData
from BlockAssembly import ModelBlockBuild, BuildHEADERModelBlock, BuildModelBlock
from RouteWriter import (
    CheckLodPartner,
    CheckTarget,
    PreviewLodTextureSync,
    WriteModelBlock,
    _write_route,
    WriteSlotBlocks,
    UnpatchTarget,
    ClearTarget,
    ClearEquipment,
    WriteSlotEquipment,
    WriteSlotAnimation,
)

# Names other modules and the tests reach through this module (the build itself lives in the
# modules imported above).
__all__ = [
    'ADDED_TRACK_IDS',
    '_align4',
    '_allowed_skin_bones',
    '_apply_geo_id_patches',
    '_apply_root_scale_patch',
    '_assemble_rigid_submesh',
    'Bone',
    '_bone_geo_id_raw',
    'BoneData',
    '_build_custom_submesh',
    '_build_rigid_submesh_blob',
    'BuildACTBoneHierarchy',
    'BuildHEADERModelBlock',
    'BuildModelBlock',
    'BuildSKNSkinningData',
    'BuildTEX',
    'builtin_template_names',
    'CheckLodPartner',
    'CheckTarget',
    'ClearEquipment',
    'ClearTarget',
    'CloneACT',
    'CloneGPL',
    'CloneHEADER',
    'CloneSKN',
    'CloneTEX',
    'CloneTrailingSections',
    '_color_entry_size',
    'ColorChannel',
    '_custom_submesh_builtin_layer_count',
    '_custom_submesh_builtin_records',
    '_CUSTOM_SUBMESH_BUILTIN_TEMPLATES',
    '_CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES',
    '_custom_submesh_patch_layer0_texture',
    '_custom_submesh_patch_specular_strength',
    '_custom_submesh_rigid_surfaces',
    '_CUSTOM_SUBMESH_SPECULAR_SHADER_MODES',
    '_custom_submesh_state_records_sha256',
    '_custom_submesh_texture_layer',
    '_custom_submesh_to_submesh',
    '_custom_submesh_type3_descriptors',
    '_custom_submesh_type3_setting',
    '_CUSTOM_SUBMESH_TYPE4_BY_UV_COUNT',
    '_custom_submesh_with_texture_index',
    'CustomSubmesh',
    'CustomSubmeshTextureAssignment',
    'CustomSubmeshUVChannel',
    '_decode',
    'Donor',
    'donor_entry',
    'DONOR_ENTRY_KEY',
    'drawlist',
    'DrawState',
    'EXCLUDED_KEY',
    'excluded_submesh_indices',
    '_follow_position_shift',
    '_gpl_user_data_bounds_descriptor',
    'hh',
    '_live_archive',
    'LodPartnerGuard',
    'LodTextureSync',
    '_merge_duplicate_texture_additions',
    'MeshData',
    'ModelBlockBuild',
    'NormalBuffer',
    'os',
    '_parse_skinning',
    'ParseSluggie',
    'PatchGPLAppendSubmesh',
    'PatchGPLExcludeSubmeshes',
    'PatchGPLMaterialStates',
    'PatchGPLPositionArrays',
    'PatchGPLReplaceRigidSubmeshes',
    'PatchGPLReplaceSkinnedSubmesh',
    'PatchGPLUVArrays',
    'PatchGPLUVRebuild',
    '_position_edits',
    'PreviewLodTextureSync',
    '_rebase_absolute_offsets',
    '_resolve_custom_submesh_records',
    '_rigid_rebuild_drawable_surfaces',
    'RigidRebuild',
    'RigidRebuildNewSurface',
    '_scale_skn_bind_pose',
    'SectionModes',
    'SK1',
    'SK2',
    'SKAcc',
    'SkinnedRebuild',
    'SkinningData',
    '_slogger',
    'SluggieParsed',
    '_splice_blobs_before_user_data',
    'STADIUM_CHUNKS',
    'Submesh',
    '_submesh_position_half_extents',
    'Texture',
    '_texture_replacement_overrides',
    'TextureData',
    'TrailingSection',
    'UnpatchTarget',
    'UntanglePolicy',
    'UVChannel',
    '_validate_bone_hierarchy_edited',
    '_validate_custom_submeshes',
    '_validate_rigid_rebuilds',
    '_validate_skin_bones',
    '_validate_skinned_rebuild',
    '_vb_comp_size',
    '_warn_stadium_custom_submesh_alpha',
    '_write_route',
    'WriteModelBlock',
    'WriteSlotAnimation',
    'WriteSlotBlocks',
    'WriteSlotEquipment',
]


def _format_report_json(value, indent=2, _level=0, _key=None):
    """Indented JSON, but with scalar-only lists kept on a single line."""
    import json
    pad = ' ' * (indent * (_level + 1))
    end = ' ' * (indent * _level)
    if isinstance(value, dict):
        if not value:
            return '{}'
        items = [f'{pad}{json.dumps(str(k))}: {_format_report_json(v, indent, _level + 1, k)}'
                 for k, v in value.items()]
        return '{\n' + ',\n'.join(items) + '\n' + end + '}'
    if isinstance(value, (list, tuple)):
        if all(not isinstance(v, (dict, list, tuple)) for v in value):
            return json.dumps(list(value))
        if _key == 'gpl_texture_references':
            items = [pad + json.dumps(v) for v in value]
            return '[\n' + ',\n'.join(items) + '\n' + end + ']'
        items = [pad + _format_report_json(v, indent, _level + 1) for v in value]
        return '[\n' + ',\n'.join(items) + '\n' + end + ']'
    return json.dumps(value)


_INLINE_SKIN_EDIT_KEYS = ('BindPoseDataEdited', 'WeightDataEdited', 'BoneIndexEdited', 'DestIndexDataEdited')


def PromoteInplaceEdits(model: dict, modes: SectionModes) -> SectionModes:
    """Make an in-place ``.sluggie``'s edits reach a hammerspace build.

    A targeted patch (SlotTarget) is always a hammerspace build, because the
    in-place patcher writes over the source's own block; so is a slot export
    (``DonorEntry``), whose donor is not in the DAT. The hammerspace
    builder reads the same fields with two exceptions, handled here:

    * in-place UV edits have no ``UVFacesDataEdited``, and the builder only
      rebuilds channels that carry one: the donor's ``UVFacesData`` is the
      valid per-loop mapping for an unchanged layout (the add-on's
      ``ExportMode.promote_inplace_uv_edits`` does the same);
    * bind-pose/weight edits written into the SK entries themselves need the
      SKN builder.

    Facial pose edits need nothing here: every build splices them into the
    ptr7 section (``_facial_edit_rebuilders``). SK vertex-count changes exist
    only in the in-place patcher and are refused. Returns the section modes
    the edits need.
    """
    if model.get('UseHammerspace'):
        return modes
    skin = model.get('SkinData') or {}
    entries = [entry for key in ('SK1s', 'SK2s', 'SKAccs') for entry in skin.get(key) or []]
    if any(entry.get('VertexCntEdited') is not None for entry in entries):
        raise ValueError('this file changes skin entry vertex counts in place, which only the in-place '
                         'patcher applies; it cannot be patched into another slot, and a slot export not at all')
    use_compression = model.get('UseCompression', True)
    gpl, skn = modes.gpl, modes.skn
    for submesh in model.get('Submeshes') or []:
        for channel in submesh.get('UVChannels') or []:
            if (channel.get('UVChannelDataEdited') is not None
                    and channel.get('UVFacesDataEdited') is None
                    and channel.get('UVFacesData') is not None
                    and _decode(channel['UVChannelDataEdited'], use_compression)
                    != _decode(channel.get('UVChannelData'), use_compression)):
                channel['UVFacesDataEdited'] = channel['UVFacesData']
                gpl = 'build'
    if any(entry.get(key) is not None for entry in entries for key in _INLINE_SKIN_EDIT_KEYS):
        skn = 'build'
    if (gpl, skn) != (modes.gpl, modes.skn):
        _slogger.info(
            f'[Slot] in-place edits promoted to a hammerspace build: GPL={gpl}, SKN={skn}',
            source='hammerspace.main',
        )
    return replace(modes, gpl=gpl, skn=skn)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    """The command line (``start.py`` runs this module as a script). Returns the exit code;
    a usage error exits through argparse (code 2)."""
    import argparse as _ap

    _parser = _ap.ArgumentParser(description='Build a hammerspace model block from a .sluggies file.')
    _parser.add_argument('sluggies_path', nargs='?', help='Path to the .sluggies file')
    _parser.add_argument('--unpatch', action='store_true', help='Remove the model from hammerspace')
    _parser.add_argument('--dry-run', action='store_true', help='Assemble and validate without modifying output files')
    _parser.add_argument('--clone', action='store_true', help='Deprecated all-clone alias')
    _parser.add_argument('--gpl', choices=('clone', 'build'), default='clone')
    _parser.add_argument('--tex', choices=('clone', 'build'), default='clone')
    _parser.add_argument('--skn', choices=('clone', 'build'), default='clone')
    _parser.add_argument('--texture-file', default=None,
                         help='Absolute path to a .png file for single-texture replacement')
    _parser.add_argument('--texture-index', type=int, default=None,
                         help='TextureDescriptor index targeted by --texture-file')
    _parser.add_argument('--output-offset', type=lambda value: int(value, 0), default=None,
                         help='Explicit aligned hammerspace DAT offset (for boundary testing)')
    _parser.add_argument('--target-id', default=None, metavar='0xNN',
                         help="Write into this character ID's slot instead of the model's own route")
    _parser.add_argument('--as-low', action='store_true',
                         help='With --target-id: the high-poly model is the low-poly model too (file 1)')
    _parser.add_argument('--validate-only', action='store_true',
                         help='With --target-id: build and validate the block a slot patch writes; no slot or LOD '
                              'checks, nothing written (start.py --patch-slot runs it before any write)')
    _parser.add_argument('--build-to', default=None, metavar='BLOCK',
                         help='Build and validate the block and save it to this file; no slot or LOD checks, '
                              'nothing patched (start.py builds the second model of a pair this way)')
    _parser.add_argument('--lod-partner-block', default=None, metavar='BLOCK',
                         help="The high-/low-poly partner's incoming block (--build-to), patched right after this "
                              'model: the partner checks use it instead of the live partner')
    _parser.add_argument('--clear-target', default=None, metavar='0xNN',
                         help="Restore this stock slot's HP and L_ models to vanilla (no .sluggie); with "
                              "--target-file: only that equipment file or animation bank (2-14)")
    _parser.add_argument('--target-file', type=int, default=None, metavar='N',
                         help='With --target-id: the slot file an equipment .sluggie goes to (2 bat, 3 left glove, '
                              '4 right glove, 5 extra bat; default: the file it was exported from)')
    _parser.add_argument('--write-slot-equipment', nargs=3, default=None, metavar=('0xNN', 'FILE', 'BLOCK'),
                         help="Write a finished equipment block (a roster pack's) into this slot's file 2-5 as it is")
    _parser.add_argument('--write-slot-animation', nargs=3, default=None, metavar=('0xNN', 'FILE', 'ANM'),
                         help="Write an animation bank (.anm) into this slot's file 6-14 as it is")
    _parser.add_argument('--write-slot-blocks', nargs=3, default=None, metavar=('0xNN', 'HIGH', 'LOW'),
                         help="Write finished model blocks (a roster pack's) into this slot as they are; '-' keeps "
                              "that file (no .sluggie)")
    _parser.add_argument('--high-as-low', action='store_true',
                         help='With --write-slot-blocks: the LOW block may be a second High model (a character '
                              'file import)')
    _args = _parser.parse_args(argv)
    if _args.write_slot_equipment is not None:
        if _args.sluggies_path is not None:
            _parser.error('--write-slot-equipment takes no .sluggie file')
        _cid, _file, _path = _args.write_slot_equipment
        try:
            with open(_path, 'rb') as _file_handle:
                _equipment_block = _file_handle.read()
            _equipment_file = int(_file, 0)
        except (OSError, ValueError) as _exc:
            _slogger.error(f'Slot write refused | {_exc}', source='hammerspace.main')
            return 1
        return 0 if WriteSlotEquipment(_cid, _equipment_file, _equipment_block) else 1
    if _args.write_slot_animation is not None:
        if _args.sluggies_path is not None:
            _parser.error('--write-slot-animation takes no .sluggie file')
        _cid, _file, _path = _args.write_slot_animation
        try:
            with open(_path, 'rb') as _file_handle:
                _bank = _file_handle.read()
            _bank_file = int(_file, 0)
        except (OSError, ValueError) as _exc:
            _slogger.error(f'Slot write refused | {_exc}', source='hammerspace.main')
            return 1
        return 0 if WriteSlotAnimation(_cid, _bank_file, _bank) else 1
    if _args.target_file is not None and _args.target_id is None and _args.clear_target is None:
        _parser.error('--target-file needs --target-id (or --clear-target)')
    if _args.write_slot_blocks is not None:
        if _args.sluggies_path is not None:
            _parser.error('--write-slot-blocks takes no .sluggie file')
        _cid, *_paths = _args.write_slot_blocks
        try:
            _blocks = []
            for _path in _paths:
                if _path == '-':
                    _blocks.append(None)
                    continue
                with open(_path, 'rb') as _file:
                    _blocks.append(_file.read())
        except OSError as _exc:
            _slogger.error(f'Slot write refused | {_exc}', source='hammerspace.main')
            return 1
        return 0 if WriteSlotBlocks(_cid, *_blocks, high_as_low=_args.high_as_low) else 1
    if _args.high_as_low:
        _parser.error('--high-as-low needs --write-slot-blocks')
    if _args.as_low and _args.target_id is None:
        _parser.error('--as-low needs --target-id')
    if _args.validate_only and _args.target_id is None:
        _parser.error('--validate-only needs --target-id')
    if (_args.build_to is not None or _args.lod_partner_block is not None) and _args.unpatch:
        _parser.error('--build-to and --lod-partner-block cannot be combined with --unpatch')
    if _args.clear_target is not None:
        if _args.sluggies_path is not None:
            _parser.error('--clear-target takes no .sluggie file')
        if _args.target_file is not None:
            return 0 if ClearEquipment(_args.clear_target, _args.target_file) else 1
        return 0 if ClearTarget(_args.clear_target) else 1
    if _args.sluggies_path is None:
        _parser.error('the .sluggie path is required')

    if (_args.texture_file is None) != (_args.texture_index is None):
        _parser.error('--texture-file and --texture-index must be used together')

    _data = load_sluggie(_args.sluggies_path)
    _model = _data['SluggiesModel']
    _chunk = _model['ChunkNumber']
    _index = _model['FileIndex']
    _model_name = os.path.basename(_args.sluggies_path)
    _target = None
    if _args.target_id is not None and not _args.validate_only and _args.build_to is None:
        try:
            _target = SlotTarget.make_target(_args.target_id, (_chunk, _index), _args.as_low, _args.target_file)
        except (SlotTarget.TargetError, OSError) as _exc:
            _slogger.error(f'Slot patch refused | Model: {_model_name} | {_exc}', source='hammerspace.main')
            return 1
        if _target is None:
            _slogger.info(
                f"Slot {_args.target_id} is this model's own route (chunk {_chunk}, file {_index}); "
                'patching it as usual',
                source='hammerspace.main',
            )

    if _args.unpatch and _target is not None:
        return 0 if UnpatchTarget(_target) else 1

    if _args.unpatch:
        _unpatch_errors = LodPartnerGuard.lod_partner_unpatch_errors(_chunk, _index)
        if _unpatch_errors:
            _slogger.error(
                f'Hammerspace unpatch refused | Model: {_model_name} | '
                'restoring the original model would break its partner model: '
                + ' '.join(_unpatch_errors),
                source='hammerspace.main',
            )
            return 1
        _high_before = LodPartnerGuard.read_current_block(_chunk, _index)
        _split_baseline = None
        if UntanglePolicy.is_split(_chunk, _index):
            # a slot export's texture offsets are its own block's: the route's exported .sluggie names the
            # untangled textures
            _baseline_model = (UntangledTextures.find_sluggie_model(_chunk, _index)
                               if donor_entry(_model) is not None else _model)
            _split_baseline, _notes = UntangledTextures.split_baseline(_chunk, _index, _baseline_model)
            for _note in _notes:
                _slogger.info(f'[Untangle] {_note}', source='hammerspace.main')
        _success, _removed_offset, _removed_length = hh.removeModelFromHammerspace(
            _chunk, _index, split_baseline=_split_baseline)
        if _success:
            try:
                _restored = LodPartnerGuard.read_current_block(_chunk, _index)
                _summary = LodPartnerGuard.act_summary(_restored) if _restored else None
                if _summary is not None and _summary.is_low_poly:
                    LodTextureSync.resync_live_low(_chunk, _index)
                elif _summary is not None:
                    LodTextureSync.sync_partner_of_high(_chunk, _index, _restored, _high_before)
            except (OSError, ValueError, RuntimeError) as _exc:
                _slogger.error(
                    f'[LOD] texture assignment sync after unpatch failed | Model: {_model_name} | '
                    f'{type(_exc).__name__}: {_exc}',
                    source='hammerspace.main',
                )
                _success = False
            _slogger.info(
                f'Hammerspace Log: Removed | Model: {_model_name} | Chunk: {_chunk} | '
                f'File: {_index} | Address: 0x{_removed_offset:08X} | '
                f'Size: {_removed_length / (1024 * 1024):.2f} MB',
                source='hammerspace.main',
            )
        return 0 if _success else 1

    _tex_png_overrides = None
    if _args.texture_file is not None:
        _tex_png_overrides = {_args.texture_index: _args.texture_file}
        if _args.tex != 'build':
            _args.tex = 'build'

    _modes = SectionModes(gpl=_args.gpl, tex=_args.tex, skn=_args.skn)
    if _args.clone and _modes != SectionModes():
        _parser.error('--clone cannot be combined with build section modes')

    try:
        # every slot-targeted run promotes (the model's own route too), so a slot write builds the very block its
        # build check measured (memory_guard: the player memory check uses that size)
        if _args.target_id is not None or donor_entry(_model) is not None:
            _modes = PromoteInplaceEdits(_model, _modes)
        # an archive written back to this model's own route keeps the members patched before it
        _own_route = _target is None and not _args.validate_only and _args.build_to is None
        _build = BuildModelBlock(_data, _modes, sluggie_path=_args.sluggies_path,
                                 tex_png_overrides=_tex_png_overrides,
                                 carry_live_members=_args.target_id is None or _own_route)
        _slogger.info(
            'Build validation report:\n' + _format_report_json(_build.validation_report),
            source='hammerspace.main',
        )
        if not _build.validation_report['valid']:
            raise ValueError('assembled model block failed validation')
        _partner_block = None
        if _args.lod_partner_block is not None:
            with open(_args.lod_partner_block, 'rb') as _file_handle:
                _partner_block = _file_handle.read()
        if _args.build_to is not None:
            with open(_args.build_to, 'wb') as _file_handle:
                _file_handle.write(_build.block)
            _slogger.info(
                f'Block built for the partner check | Model: {_model_name} | {len(_build.block)} bytes | '
                'nothing written to the game files',
                source='hammerspace.main')
        elif _args.validate_only:
            try:
                import memory_guard
                memory_guard.record_size(hh._ROOT, _args.sluggies_path, len(_build.block))
            except (ImportError, OSError, ValueError) as _exc:
                _slogger.warning(f'Block size not recorded for the player memory check | {_exc}',
                                 source='hammerspace.main')
            _slogger.info(
                f'Slot build check passed | Model: {_model_name} | '
                f'Size: {len(_build.block) / (1024 * 1024):.2f} MB ({len(_build.block)} bytes) | nothing written',
                source='hammerspace.main')
        elif _args.dry_run:
            if _target is not None:
                CheckTarget(_build, _target)
            CheckLodPartner(_build, _target, _partner_block)
            PreviewLodTextureSync(_build, _target)
            _slogger.info('Dry run complete; output DAT, DOL, and FST were not modified.', source='hammerspace.main')
        else:
            WriteModelBlock(_build, _model_name, output_offset=_args.output_offset, target=_target,
                            partner_block=_partner_block)
    except Exception as _exc:      # whatever a builder raises (struct.error, IndexError, ...) ends here
        _slogger.error(
            f'Hammerspace operation failed | Model: {_model_name} | '
            f'{type(_exc).__name__}: {_exc}',
            source='hammerspace.main',
        )
        return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
