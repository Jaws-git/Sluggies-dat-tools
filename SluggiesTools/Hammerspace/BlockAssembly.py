"""Assembly of a model block: ``BuildModelBlock``.

Validates the file, resolves the donor, builds or clones each section, lays the sections out behind
the block header and puts the result back into what the donor's DOL entry holds around the model.
Nothing here writes the output DAT, DOL or FST (``RouteWriter`` does).
"""

import os
import struct
from dataclasses import dataclass, replace

import slogger as _slogger
import HammerspaceHelper as hh
import UntangledTextures
import UntanglePolicy
from BlockValidator import validate_model_block
from GeometryRebuild import apply_desired_texture_assignments, rebuild_edited_uvs, rebuild_surface_assignments
from ArchiveContainer import rebuild_archive_container
from ModelFormat import align_array_offset
from InplacePatcher import root_scale as _root_scale

from SluggieModel import (
    TrailingSection,
    SluggieParsed,
    _SKINNED_REBUILD_SUBMESH,
    SectionModes,
    ParseSluggie,
    excluded_submesh_indices,
)
from Donor import (
    donor_entry,
    Donor,
    CloneGPL,
    CloneTEX,
    CloneSKN,
    CloneTrailingSections,
    CloneHEADER,
    _DonorRoute,
    _resolve_donor_route,
)
from HammerspaceValidate import (
    _validate_custom_submeshes,
    _validate_bone_hierarchy_edited,
    _validate_skin_bones,
    _facial_pose_submeshes,
    _validate_rigid_rebuilds,
    _validate_skinned_rebuild,
    _validate_section_modes,
    _validate_hammerspace_contract,
)
from RigidSubmesh import (
    _warn_stadium_custom_submesh_alpha,
    PatchGPLAppendSubmesh,
    PatchGPLReplaceRigidSubmeshes,
)
from SkinnedSubmesh import (
    _rigid_rebuild_facial_rebuilders,
    _facial_edit_rebuilders,
    _rebuild_facial_section,
    PatchGPLReplaceSkinnedSubmesh,
)
from GplPatch import (
    _position_edits,
    PatchGPLMaterialStates,
    PatchGPLExcludeSubmeshes,
    PatchGPLPositionArrays,
    PatchGPLUVArrays,
    PatchGPLUVRebuild,
)
from ActBuild import BuildACTBoneHierarchy, _root_scale_changes_act
from TexBuild import _merge_duplicate_texture_additions, BuildTEX, _resolve_texture_plan
from SknBuild import _scale_skn_bind_pose, BuildSKNSkinningData


@dataclass
class ModelBlockBuild:
    block: bytes
    parsed: SluggieParsed
    chunk_number: int
    file_index: int
    original_offset: int
    original_length: int
    section_modes: SectionModes
    section_sizes: dict[str, int]
    validation_report: dict
    donor_block: bytes | None = None    # a slot export's embedded donor entry (DonorEntry), else None


def BuildHEADERModelBlock(
    gpl_bytes: bytes,
    act_bytes: bytes,
    tex_bytes: bytes,
    skn_bytes: bytes,
    trailing_bytes: bytes = b'',
    original_header: bytes = b'',
    original_trailing_off: int = 0,
    trailing_sections: list[TrailingSection] | None = None,
) -> bytes:
    """Assemble the full model block from its four sections.

    Writes the 0x20-byte file header with relative pointers to GPL, ACT,
    TEX, and SKN, then concatenates all four sections into one contiguous
    byte string ready to be written into hammerspace.

    Header layout (matches Model0.analyze() in model0.py):
        +0x00  uint32  firstWord  — always 0
        +0x04  uint32  gplPtr     — GPL section offset relative to block start
        +0x08  uint32  ptr3       — ACT section offset (0 if absent)
        +0x0c  uint32  texPtr     — TEX section offset (0 if absent)
        +0x10  uint32  ptr5       — SKN section offset (0 if absent)
        +0x14  uint32  ptr6       — extra section pointer (recomputed if non-zero)
        +0x18  uint32  ptr7       — extra section pointer (recomputed if non-zero)
        +0x1c  uint32  ptr8       — extra section pointer (recomputed if non-zero)

    Section order:
        0x00  File header (0x20 bytes)
        0x20  GPL section          (always present)
        0x20 + len(gpl)  ACT section (omitted when empty)
        ...   TEX section          (omitted when empty)
        ...   SKN section          (omitted when empty; includes trailing
              sub-sections pointed to by ptr6/ptr7/ptr8 when they fall
              after the original SKN offset)

    Parameters
    ----------
    original_header :
        The 0x20-byte file header from the original model block in INPUT
        dt_na.dat.  Used to read ptr6/ptr7/ptr8 for recomputation.
    original_trailing_off :
        The earliest original ptr6/ptr7/ptr8 section offset. Needed to preserve
        relative spacing between separately cloned trailing sections.

    Returns the complete model block as a byte string.
    """

    HDR_SIZE = 0x20

    def _section_align_padding(length: int) -> bytes:
        # every section start must sit at 0 mod 32 relative to the
        # block. HDR_SIZE (0x20) is itself 32-aligned, so rounding each
        # section's own length up to a multiple of 32 keeps every following
        # section start aligned too. This is a no-op whenever the section is
        # already a multiple of 32 long, which vanilla sections always are,
        # so unchanged/byte-identical builds are unaffected.
        return b'\x00' * ((-length) % 32)

    gpl_off = HDR_SIZE
    gpl_section_padding = b''
    if len(original_header) >= HDR_SIZE:
        original_gpl_off = struct.unpack_from('>I', original_header, 0x04)[0]
        original_next_sections = [
            struct.unpack_from('>I', original_header, offset)[0]
            for offset in (0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C)
        ]
        original_next = min(
            (offset for offset in original_next_sections if offset > original_gpl_off),
            default=0,
        )
        if original_gpl_off == gpl_off and original_next:
            original_gpl_span = original_next - original_gpl_off
            if len(gpl_bytes) <= original_gpl_span:
                gpl_section_padding = b'\x00' * (original_gpl_span - len(gpl_bytes))
    if not gpl_section_padding:
        # GPL grew past what donor-span preservation can cover (or there is
        # no donor header to preserve against, e.g. a synthetic block): fall
        # back to a plain 32-byte-boundary pad so ACT/TEX/SKN still start
        # aligned.
        gpl_section_padding = _section_align_padding(len(gpl_bytes))

    act_off = gpl_off + len(gpl_bytes) + len(gpl_section_padding)
    act_padding = _section_align_padding(len(act_bytes)) if act_bytes else b''
    tex_off = act_off + len(act_bytes) + len(act_padding)
    tex_padding = _section_align_padding(len(tex_bytes)) if tex_bytes else b''
    skn_unaligned_off = tex_off + len(tex_bytes) + len(tex_padding)
    skn_off = align_array_offset(skn_unaligned_off, 'skn_source') if skn_bytes else skn_unaligned_off
    skn_padding = b'\x00' * (skn_off - skn_unaligned_off)
    skn_trailing_padding = b''
    if (skn_bytes and trailing_bytes and len(original_header) >= HDR_SIZE
            and original_trailing_off):
        original_skn_off = struct.unpack_from('>I', original_header, 0x10)[0]
        if original_skn_off and original_trailing_off > original_skn_off:
            original_relative_offset = original_trailing_off - original_skn_off
            if len(skn_bytes) < original_relative_offset:
                skn_trailing_padding = b'\x00' * (original_relative_offset - len(skn_bytes))
    tail_start_unaligned = skn_off + len(skn_bytes) + len(skn_trailing_padding)
    tail_padding = _section_align_padding(tail_start_unaligned) if trailing_bytes else b''
    tail_start = tail_start_unaligned + len(tail_padding)

    hdr = bytearray(HDR_SIZE)
    struct.pack_into('>I', hdr, 0x00, 0)
    struct.pack_into('>I', hdr, 0x04, gpl_off)
    struct.pack_into('>I', hdr, 0x08, act_off if act_bytes else 0)
    struct.pack_into('>I', hdr, 0x0c, tex_off if tex_bytes else 0)
    struct.pack_into('>I', hdr, 0x10, skn_off if skn_bytes else 0)

    # Recompute ptr6/ptr7/ptr8 relative to the separately cloned tail.
    if trailing_bytes:
        if trailing_sections:
            # The sections are laid out one after another in address order
            # (the caller joins them that way), so each pointer is the tail
            # start plus the lengths before it. Vanilla sections are
            # contiguous, so this equals the donor's relative spacing; a
            # rebuilt section (ptr7) may change length.
            cursor = tail_start
            for section in sorted(trailing_sections, key=lambda sec: sec.original_ptr):
                if section.header_field_offset in (0x14, 0x18, 0x1c) and section.original_ptr:
                    struct.pack_into('>I', hdr, section.header_field_offset, cursor)
                    _slogger.info(f'[HDR] +0x{section.header_field_offset:02X} patched: '
                           f'0x{section.original_ptr:08X} -> 0x{cursor:08X}', source="hammerspace.main")
                cursor += len(section.data)
        elif len(original_header) >= HDR_SIZE and original_trailing_off:
            for field_offset in (0x14, 0x18, 0x1c):
                orig_ptr = struct.unpack_from('>I', original_header, field_offset)[0]
                if orig_ptr and orig_ptr >= original_trailing_off:
                    new_ptr = tail_start + (orig_ptr - original_trailing_off)
                    struct.pack_into('>I', hdr, field_offset, new_ptr)
                    _slogger.info(f'[HDR] +0x{field_offset:02X} patched: '
                           f'0x{orig_ptr:08X} -> 0x{new_ptr:08X}', source="hammerspace.main")

    return (bytes(hdr) + gpl_bytes + gpl_section_padding + act_bytes + act_padding
            + tex_bytes + tex_padding + skn_padding
            + skn_bytes + skn_trailing_padding + tail_padding + trailing_bytes)


def _build_validation_report(
    block: bytes,
    original_length: int,
    section_modes: SectionModes,
    section_sizes: dict[str, int],
) -> dict:
    structural = validate_model_block(block)
    section_pointer_facts = structural.get('facts', {}).get('section_pointers', {})
    pointers = {
        'GPL': section_pointer_facts.get('GPL', 0),
        'ACT': section_pointer_facts.get('ACT', 0),
        'TEX': section_pointer_facts.get('TEX', 0),
        'SKN': section_pointer_facts.get('SKN', 0),
    }

    return {
        'valid': structural['valid'],
        'errors': structural['errors'],
        'warnings': structural.get('warnings', []),
        'section_modes': section_modes.as_dict(),
        'section_sizes': section_sizes,
        'section_pointers': pointers,
        'assembled_size': len(block),
        'original_size': original_length,
        'size_delta': len(block) - original_length,
        'validator_facts': structural.get('facts', {}),
    }


def _check_mode_requirements(model: dict, modes: SectionModes, texture_plan, sluggie_path) -> tuple[list[int], bool]:
    """Refuse edits whose section is not in build mode. Returns the rigid submeshes that are rebuilt
    and whether the skinned body is."""
    rigid_rebuild_indices = [
        index for index, sub in enumerate(model.get('Submeshes') or []) if sub.get('RigidRebuild')
    ]
    if rigid_rebuild_indices and modes.gpl != 'build':
        raise ValueError("RigidRebuild requires SectionModes.gpl='build'")
    skinned_rebuild = bool(
        (model.get('Submeshes') or [{}])[_SKINNED_REBUILD_SUBMESH].get('SkinnedRebuild')
    )
    if skinned_rebuild and (modes.gpl != 'build' or modes.skn != 'build'):
        raise ValueError("SkinnedRebuild requires SectionModes.gpl='build' and skn='build'")
    # A low-poly model has no TEX section: its assignments are indices into
    # the high-poly partner's TEX (checked against it in CheckLodPartner).
    # New PNGs (AdditionalTextureDescriptors) need the TEX builder, whatever
    # ReimportTextures says: that flag only decides whether the donor
    # textures are re-encoded from tex/ or cloned.
    if model.get('DesiredTextureAssignments') and modes.gpl != 'build':
        raise ValueError('DesiredTextureAssignments require the GPL build mode')
    if excluded_submesh_indices(model) and modes.gpl != 'build':
        raise ValueError('Excluded submeshes require the GPL build mode')
    custom_submesh_additional_texture_names = {
        (owner.get('TextureAssignment') or {}).get('AdditionalTextureFileName')
        for cs in model.get('CustomSubmeshes') or []
        for owner in [cs] + list(cs.get('AdditionalSurfaces') or [])
    } - {None}
    if custom_submesh_additional_texture_names and modes.tex != 'build':
        raise ValueError(
            "CustomSubmeshes with TextureAssignment.AdditionalTextureFileName require "
            "SectionModes.tex='build'"
        )
    new_surface_additional_texture_names = {
        (surface.get('TextureAssignment') or {}).get('AdditionalTextureFileName')
        for sub in model.get('Submeshes') or []
        for rebuild_key in ('RigidRebuild', 'SkinnedRebuild')
        for surface in (sub.get(rebuild_key) or {}).get('NewSurfaces') or []
    } - {None}
    if new_surface_additional_texture_names and modes.tex != 'build':
        raise ValueError(
            "RigidRebuild/SkinnedRebuild new surfaces with TextureAssignment.AdditionalTextureFileName "
            "require SectionModes.tex='build'"
        )
    if model.get('AdditionalTextureDescriptors') and modes.tex != 'build':
        raise ValueError("AdditionalTextureDescriptors require SectionModes.tex='build'")
    if model.get('TextureReplacements') and modes.tex != 'build':
        raise ValueError("TextureReplacements require SectionModes.tex='build'")
    if modes.tex == 'build' and texture_plan is None and (
        model.get('ReimportTextures') or model.get('AdditionalTextureDescriptors')
    ):
        _merge_duplicate_texture_additions(model, sluggie_path)
    return rigid_rebuild_indices, skinned_rebuild


def _build_gpl_section(data: dict, parsed: SluggieParsed, modes: SectionModes, donor: Donor,
                       texture_plan, sluggie_path, position_edits: list,
                       rigid_rebuild_indices: list[int], skinned_rebuild: bool,
                       facial_rebuilders: dict) -> tuple[bytes, bool]:
    """The GPL section: the donor's, with every edit patched in or its blob replaced. Fills
    ``facial_rebuilders`` and, for a rebuilt body, ``parsed.skinning``. Returns the section and
    whether a rebuilt body brought its own facial poses."""
    model = data['SluggiesModel']
    body_facial_rebuilt = False
    if modes.gpl == 'build':
        has_material_state_edits = any(
            state.get('MaterialStateAliasedByImporter')
            or state.get('ShaderModeEdited') is not None
            or state.get('DisplayStateParamBytesEdited') is not None
            for submesh in model.get('Submeshes', [])
            for state in submesh.get('DisplayStates', [])
        )
        uv_lists_rebuilt = any(
            submesh.get('UVPrimitiveListsRebuiltByImporter')
            or submesh.get('NormalPrimitiveListsRebuiltByImporter')
            or submesh.get('ColorPrimitiveListsRebuiltByImporter')
            for submesh in model.get('Submeshes', [])
        )
        uv_array_edits = any(
            submesh.get('UVArraysEditedByImporter')
            for submesh in model.get('Submeshes', [])
        )
        normal_array_edits = any(
            submesh.get('NormalArraysEditedByImporter')
            for submesh in model.get('Submeshes', [])
        )
        color_array_edits = any(
            submesh.get('ColorArraysEditedByImporter')
            for submesh in model.get('Submeshes', [])
        )
        excluded_indices = excluded_submesh_indices(model)
        if (
            has_material_state_edits
            or position_edits
            or uv_array_edits
            or normal_array_edits
            or color_array_edits
            or rigid_rebuild_indices
            or skinned_rebuild
            or excluded_indices
            or getattr(parsed, 'custom_submeshes', None)
        ):
            gpl_bytes = CloneGPL(donor)
            donor_gpl_length = len(gpl_bytes)
            texture_index_by_file_name = {
                entry.texture_file_name: entry.texture_index
                for entry in (texture_plan.entries if texture_plan is not None else ())
            }
            if has_material_state_edits:
                gpl_bytes = PatchGPLMaterialStates(gpl_bytes, data, donor)
            if excluded_indices:
                # Donor field offsets: before any blob is replaced or moved.
                gpl_bytes = PatchGPLExcludeSubmeshes(gpl_bytes, model, donor)
            if position_edits:
                gpl_bytes = PatchGPLPositionArrays(gpl_bytes, model, donor)
            if uv_array_edits or normal_array_edits or color_array_edits:
                gpl_bytes = (
                    PatchGPLUVRebuild(gpl_bytes, model, donor)
                    if uv_lists_rebuilt
                    else PatchGPLUVArrays(gpl_bytes, model, donor)
                )
            if skinned_rebuild:
                # The body first: it replaces submesh 0's blob and hands back
                # the SKN structures for the new position buffer, which the
                # SKN build below serializes instead of the donor's.
                gpl_bytes, donor_gpl_length, rebuilt_skinning, body_facial_rebuilder = PatchGPLReplaceSkinnedSubmesh(
                    gpl_bytes, model, parsed, texture_index_by_file_name,
                    donor_gpl_length=donor_gpl_length,
                )
                parsed.skinning = rebuilt_skinning
                if body_facial_rebuilder is not None:
                    facial_rebuilders[_SKINNED_REBUILD_SUBMESH] = body_facial_rebuilder
                    body_facial_rebuilt = True
            if rigid_rebuild_indices:
                # Replace before the append: the append relocates every blob,
                # replaced ones included, as one unit.
                gpl_bytes, donor_gpl_length = PatchGPLReplaceRigidSubmeshes(
                    gpl_bytes, model, parsed, texture_index_by_file_name,
                    donor_gpl_length=donor_gpl_length,
                )
                facial_rebuilders.update(_rigid_rebuild_facial_rebuilders(model, parsed))
            if getattr(parsed, 'custom_submeshes', None):
                # Clone + append: the donor's blobs stay as they are.
                _warn_stadium_custom_submesh_alpha(model, parsed, sluggie_path)
                gpl_bytes = PatchGPLAppendSubmesh(
                    gpl_bytes, model, parsed, texture_index_by_file_name,
                    donor_gpl_length=donor_gpl_length,
                )
        else:
            gpl_bytes = CloneGPL(donor)
            donor_submesh_count = (
                struct.unpack_from('>I', gpl_bytes, 0x0C)[0]
                if len(gpl_bytes) >= 0x10 else None
            )
            if (donor_submesh_count is not None
                    and len(model.get('Submeshes', [])) != donor_submesh_count):
                # No edit route adds or removes a Submeshes entry (new geometry is a
                # CustomSubmesh), and no GPL is written from nothing.
                raise ValueError(
                    f"GPL build: the file lists {len(model.get('Submeshes', []))} submeshes, its donor model "
                    f'has {donor_submesh_count}; submeshes cannot be added to or removed from Submeshes '
                    '(add geometry as a custom submesh)')
            else:
                # Every edit compacted away (or none was exported): the donor
                # GPL is the right output.
                pending = [
                    f'sub{submesh_index} ds{state_index}'
                    for submesh_index, submesh in enumerate(model.get('Submeshes', []))
                    for state_index, state in enumerate(submesh.get('DisplayStates', []))
                    if state.get('PrimListDataEdited') is not None
                ]
                if pending:
                    raise ValueError(
                        'GPL build: primitive-list edits on '
                        f'{", ".join(pending)} have no patch route')
                _slogger.info(
                    '[GPL] build requested but no GPL edit remains after '
                    'preprocessing; using the donor GPL unchanged',
                    source='hammerspace.main',
                )

    else:
        gpl_bytes = CloneGPL(donor)
    return gpl_bytes, body_facial_rebuilt


def _build_trailing_sections(model: dict, parsed: SluggieParsed, facial_rebuilders: dict,
                             clear_facial_pointer: bool, donor: Donor) -> tuple[bytes, int, list]:
    """The ptr6/ptr7/ptr8 tail: the file's sections (ptr7 rebuilt where facial poses changed), else
    the donor's. Returns ``(bytes, original offset, sections)``."""
    parsed_trailing_sections = getattr(parsed, 'trailing_sections', None) or []
    rebuilt_facial_section = None
    if not clear_facial_pointer:
        facial_rebuilders.update(_facial_edit_rebuilders(model, set(facial_rebuilders)))
    if facial_rebuilders and not clear_facial_pointer:
        rebuilt_facial_section = _rebuild_facial_section(model, facial_rebuilders)
        facial_slots = [k for k, section in enumerate(parsed_trailing_sections) if section.header_field_offset == 0x18]
        if len(facial_slots) != 1:
            raise ValueError(
                'a rebuilt or edited submesh has facial poses but the file has no TrailingSections entry for ptr7 '
                '(header +0x18); re-export the model'
            )
        parsed_trailing_sections = list(parsed_trailing_sections)
        parsed_trailing_sections[facial_slots[0]] = replace(
            parsed_trailing_sections[facial_slots[0]], data=rebuilt_facial_section,
        )
        parsed.trailing_sections = parsed_trailing_sections
    if parsed_trailing_sections:
        # Address order: the header builder lays the sections out one after
        # another and recomputes ptr6/ptr7/ptr8 from their lengths.
        parsed_trailing_sections = sorted(parsed_trailing_sections, key=lambda section: section.original_ptr)
        trailing_bytes = b''.join(section.data for section in parsed_trailing_sections)
        original_trailing_offset = min(
            (section.original_ptr for section in parsed_trailing_sections if section.original_ptr),
            default=0,
        )
    else:
        trailing_bytes, original_trailing_offset = CloneTrailingSections(donor)
    return trailing_bytes, original_trailing_offset, parsed_trailing_sections


def _wrap_inner_block(route: _DonorRoute, model: dict, inner_block: bytes, report: dict) -> bytes:
    """Put the built model back into what its DOL entry holds around it (an archive or a prefix), and
    give an unused character's cloned textures their untangled bytes. Adds the sizes to ``report``."""
    chunk_number = route.chunk_number
    original_offset = route.original_offset
    original_length = route.original_length
    source_model_length = route.donor.model_length
    route_prefix = route.route_prefix
    route_prefix_size = route.route_prefix_size
    archive_layout = route.archive_layout
    base_container = route.base_container
    base_layout = route.base_layout
    block_model_offset = route.block_model_offset
    embedded = route.donor.embedded
    if archive_layout is not None:
        block, archive_report = rebuild_archive_container(
            base_container,
            block_model_offset,
            inner_block,
            layout=base_layout,
        )
        report.update(archive_report)
        _slogger.info(
            f'[Container] archive slot {archive_report["archive_member_slot"]} '
            f'{archive_report["archive_member_original_size"]:,} -> '
            f'{archive_report["archive_member_new_size"]:,} bytes; '
            f'{archive_report["archive_slots_shifted"]} later slot(s) shifted by '
            f'{archive_report["archive_size_delta"]:+,}; archive '
            f'{archive_report["archive_original_size"]:,} -> '
            f'{archive_report["archive_new_size"]:,} bytes',
            source='hammerspace.main',
        )
    else:
        block = route_prefix + inner_block
        block_model_offset = route_prefix_size
    if route_prefix or archive_layout is not None:
        report['container_prefix_size'] = block_model_offset
        report['inner_assembled_size'] = len(inner_block)
        report['inner_original_size'] = source_model_length
        report['assembled_size'] = len(block)
        report['original_size'] = original_length
        report['size_delta'] = len(block) - original_length
    if not embedded and UntanglePolicy.is_split_dir(chunk_number) and model.get('TextureDescriptors'):
        # Unedited textures were cloned from the vanilla block; give them back
        # the untangled bytes the .sluggie's texture names were exported with.
        # (An embedded donor holds the exported bytes already.)
        with open(hh.INPUT_DAT, 'rb') as source:
            source.seek(original_offset)
            vanilla_entry = source.read(original_length)
        untangled = bytearray(block)
        for note in UntangledTextures.reapply(
            untangled, block_model_offset, model['TextureDescriptors'],
            vanilla_entry, route_prefix_size,
        ):
            _slogger.info(f'[Untangle] {note}', source='hammerspace.main')
        block = bytes(untangled)
    return block


def BuildModelBlock(
    data: dict,
    section_modes: SectionModes | None = None,
    sluggie_path: str | os.PathLike[str] | None = None,
    tex_png_overrides: dict[int, str] | None = None,
    texture_plan=None,
    carry_live_members: bool = False,
) -> ModelBlockBuild:
    """Assemble a model block without modifying output DAT, DOL, or FST files.

    ``data`` itself is edited on the way: the rebuild steps write their
    results back into the document (surface assignments, rebuilt UVs, the
    desired texture assignments, merged duplicate texture additions). Pass
    a document that is not used for anything else afterwards, or a copy.

    When ``section_modes.tex == 'build'`` and the sluggie has
    ``ReimportTextures`` set, the donor textures are re-encoded from the edited
    PNGs in the sluggie's ``tex/`` folder via :func:`BuildTEX`; without it they
    are cloned from INPUT dt_na.dat. ``AdditionalTextureDescriptors`` are
    encoded and appended either way, and a ``TextureReplacements`` slot is
    re-encoded from its PNG either way. ``sluggie_path`` must point at the
    ``.sluggies`` file whenever a PNG is read. With neither, the rebuilt TEX
    section clones every texture payload, which is byte-equivalent to the
    clone path.

    A slot export's ``DonorEntry`` is the donor instead of the DOL route:
    every donor read comes from it (``Donor``).

    ``carry_live_members``: when the DOL entry is an archive, rebuild the
    archive the output routes it to now (``_live_archive``) instead of the
    vanilla one, so members patched earlier keep their edits. The model
    itself is still built from its vanilla donor. Only for a block written
    back to the model's own route; ignored with an embedded donor.
    """
    embedded_donor = donor_entry(data['SluggiesModel'])
    modes = section_modes or SectionModes()
    _validate_section_modes(modes)

    model = data['SluggiesModel']
    _validate_hammerspace_contract(model, modes)
    _validate_bone_hierarchy_edited(model)
    _validate_skin_bones(model)
    _validate_custom_submeshes(model)
    _validate_rigid_rebuilds(model)
    _validate_skinned_rebuild(model)
    rigid_rebuild_indices, skinned_rebuild = _check_mode_requirements(model, modes, texture_plan, sluggie_path)
    # Facial poses (ptr7) address vertices by index, and a rebuilt submesh
    # has a new vertex order. With ``FacialPoses`` in the rebuild the section
    # is rebuilt for that order (body and, since 2026-10-10,
    # rigid submeshes); a body without them drops the poses (the header
    # pointer is cleared, as the external tool does for every character), a
    # rigid submesh without them has its objects neutralized. Poses on
    # submeshes that are not rebuilt are untouched.
    clear_facial_pointer = skinned_rebuild and _SKINNED_REBUILD_SUBMESH in _facial_pose_submeshes(model)
    facial_rebuilders: dict = {}
    route = _resolve_donor_route(model, carry_live_members, embedded_donor)
    donor = route.donor

    position_edits = _position_edits(model) if modes.gpl == 'build' else []
    if modes.gpl == 'build':
        rebuild_surface_assignments(data)
        rebuild_edited_uvs(data)
        apply_desired_texture_assignments(data)
    parsed = ParseSluggie(data, [index for index, _, _ in position_edits])
    if getattr(parsed, 'custom_submeshes', None) and modes.gpl != 'build':
        raise ValueError("CustomSubmeshes require SectionModes.gpl='build'")
    if modes.tex == 'build':
        texture_plan = _resolve_texture_plan(model, sluggie_path, tex_png_overrides, texture_plan)
    gpl_bytes, body_facial_rebuilt = _build_gpl_section(
        data, parsed, modes, donor, texture_plan, sluggie_path,
        position_edits, rigid_rebuild_indices, skinned_rebuild, facial_rebuilders)
    if body_facial_rebuilt:
        clear_facial_pointer = False

    # BuildACTBoneHierarchy picks the clone route
    # (CloneACT + _apply_root_scale_patch + _apply_geo_id_patches, both
    # ACT-section-relative so they stay correct across hammerspace
    # relocation) or the rebuild route (BoneHierarchyEdited present -- new
    # leaf bones change the section's length, so it can no longer be a fixed
    # verbatim clone) on its own.
    act_bytes = BuildACTBoneHierarchy(data, donor)
    root_scale_applied = _root_scale_changes_act(data, donor)
    if modes.tex == 'build':
        tex_bytes = BuildTEX(parsed, texture_plan, donor)
    else:
        tex_bytes = CloneTEX(donor)
    if modes.skn == 'build':
        skn_bytes = BuildSKNSkinningData(parsed)
    else:
        skn_bytes = CloneSKN(donor)
    # Bind-pose vertex scaling: apply the model's
    # RootBoneScaleEdited factors to the SKN bind-pose data. Covers both build
    # mode (freshly built SKN) and clone mode (verbatim SKN clone) since both
    # produce the same SKN section layout. No-op when the model has no
    # non-uniform scale.
    bind_pose_factors = _root_scale.resolve_bind_pose_scale(model)
    if bind_pose_factors is not None and skn_bytes:
        skn_bytes = _scale_skn_bind_pose(skn_bytes, bind_pose_factors)
        _slogger.info(
            f'[SKN] bind-pose scaled by {bind_pose_factors} '
            f'(RootBoneScaleEdited)',
            source='hammerspace.main',
        )
    trailing_bytes, original_trailing_offset, parsed_trailing_sections = _build_trailing_sections(
        model, parsed, facial_rebuilders, clear_facial_pointer, donor)
    original_header = CloneHEADER(donor)
    inner_block = BuildHEADERModelBlock(
        gpl_bytes,
        act_bytes,
        tex_bytes,
        skn_bytes,
        trailing_bytes=trailing_bytes,
        original_header=original_header,
        original_trailing_off=original_trailing_offset,
        trailing_sections=parsed_trailing_sections or None,
    )
    if clear_facial_pointer and struct.unpack_from('>I', inner_block, 0x18)[0]:
        cleared = bytearray(inner_block)
        struct.pack_into('>I', cleared, 0x18, 0)
        inner_block = bytes(cleared)
        _slogger.warning(
            '[SkinnedRebuild] facial poses (ptr7) address submesh 0 by vertex index, which the '
            'rebuilt body renumbers: the pointer is cleared, so this model has no blink or '
            'mouth animation (the section bytes stay in the block unreferenced)',
            source='hammerspace.main',
        )
    section_sizes = {
        'GPL': len(gpl_bytes),
        'ACT': len(act_bytes),
        'TEX': len(tex_bytes),
        'SKN': len(skn_bytes),
        'trailing': len(trailing_bytes),
    }
    report = _build_validation_report(
        inner_block, donor.model_length, modes, section_sizes
    )
    report['root_scale_applied'] = root_scale_applied
    block = _wrap_inner_block(route, model, inner_block, report)
    return ModelBlockBuild(
        block=block,
        parsed=parsed,
        chunk_number=route.chunk_number,
        file_index=route.file_index,
        original_offset=route.original_offset,
        original_length=route.original_length,
        section_modes=modes,
        section_sizes=section_sizes,
        validation_report=report,
        donor_block=donor.data if donor.embedded else None,
    )
