"""The TEX section of a hammerspace build.

``BuildTEX`` writes the section from the parsed descriptors and a texture plan (re-encoded, refilled
and added textures; everything else is cloned from the donor). ``_resolve_texture_plan`` builds that
plan from the ``tex/`` folder beside the ``.sluggie``.
"""

import struct

import slogger as _slogger

from SluggieModel import Texture, SluggieParsed
from Donor import Donor


def _merge_duplicate_texture_additions(model: dict, sluggie_path) -> None:
    """Collapse ``AdditionalTextureDescriptors`` entries that would encode to
    the same texture into their first occurrence, in place.

    The add-on used to append one entry per material even when several showed
    the same PNG, so each copy was encoded and stored separately (Tiny Kong,
    2026-09-27: four 512 KB copies of one image). Merging here fixes such
    ``.sluggie`` files without a re-export. ``DesiredTextureAssignments``
    indices and the ``AdditionalTextureFileName`` bindings of custom
    submeshes and of rebuilt submeshes' new surfaces are remapped to the
    kept entry.
    """
    additions = model.get('AdditionalTextureDescriptors') or []
    if len(additions) < 2:
        return
    import texture_helper as _tex
    descriptors = model.get('TextureDescriptors') or []
    canonical = _tex.find_duplicate_additional_textures(sluggie_path, descriptors, additions)
    kept = [order for order, first in enumerate(canonical) if first == order]
    if len(kept) == len(additions):
        return

    donor_count = len(descriptors)
    new_position = {order: kept.index(first) for order, first in enumerate(canonical)}
    for order, first in enumerate(canonical):
        if first != order:
            _slogger.info(
                f"[TEX] texture addition {additions[order]['TextureFileName']!r} "
                f"(index {donor_count + order}) is identical to "
                f"{additions[first]['TextureFileName']!r} (index {donor_count + first}); "
                'sharing one texture',
                source='hammerspace.main',
            )

    assignments = model.get('DesiredTextureAssignments')
    if assignments:
        for surface_id, index in assignments.items():
            if isinstance(index, int) and not isinstance(index, bool)                     and donor_count <= index < donor_count + len(additions):
                assignments[surface_id] = donor_count + new_position[index - donor_count]

    kept_name = {}
    for order, first in enumerate(canonical):
        kept_name.setdefault(
            additions[order].get('TextureFileName'), additions[first].get('TextureFileName'))
    owners = []
    for cs in model.get('CustomSubmeshes') or []:
        owners += [cs] + list(cs.get('AdditionalSurfaces') or [])
    # New surfaces of a rebuilt donor submesh bind additions by file name too
    # (a bat replaced by a mesh with two materials of one colour, 2026-11-26).
    for submesh in model.get('Submeshes') or []:
        for key in ('RigidRebuild', 'SkinnedRebuild'):
            rebuild = submesh.get(key)
            if isinstance(rebuild, dict):
                owners += [s for s in rebuild.get('NewSurfaces') or [] if isinstance(s, dict)]
    for owner in owners:
        texture_assignment = owner.get('TextureAssignment') or {}
        name = texture_assignment.get('AdditionalTextureFileName')
        if name in kept_name:
            texture_assignment['AdditionalTextureFileName'] = kept_name[name]

    model['AdditionalTextureDescriptors'] = [additions[order] for order in kept]
    _slogger.info(
        f'[TEX] merged {len(additions) - len(kept)} duplicate texture addition(s); '
        f'{len(kept)} of {len(additions)} remain',
        source='hammerspace.main',
    )


def BuildTEX(parsed: SluggieParsed, texture_plan, donor: Donor) -> bytes:
    """Build a complete TEX section from parsed descriptors and an optional texture plan.

    Textures present in ``texture_plan.entries`` are re-encoded: their image and
    palette payloads come from the plan entry, and their width/height come from
    the entry's actual encoded dimensions. Contiguous entries after the donor
    count are appended by cloning the selected donor descriptor template.
    Donor textures not in the plan (skipped or absent) are cloned verbatim from
    INPUT dt_na.dat using the descriptor's original absolute offsets.

    Binary layout (all big-endian):

      TEX Header (4 bytes):
        +0x00  uint16  texture_count
        +0x02  uint16  clut_count

      Descriptor table (texture_count * 0x20 bytes):
        +0x00  uint32  image_data_offset   (relative to TEX section start)
        +0x04  uint32  palette_data_offset (relative to TEX section start, 0 if none)
        +0x08  uint16  height
        +0x0A  uint16  width
        +0x0C  uint8   edge_lod_enable
        +0x0D  uint8   min_lod
        +0x0E  uint8   max_lod
        +0x0F  uint8   unpacked
        +0x10  byte[7] unknown (additional mip count at +0x16)
        +0x17  uint8   image_format
        +0x18  uint16  palette_entries
        +0x1A  uint8   palette_format
        +0x1B  byte[5] unknown

      Data region:
        Image payloads packed sequentially, then palette payloads. The data
        region starts at the first 32-byte-aligned offset after the
        descriptor table (zero padding closes the gap), and every individual
        image and palette payload is likewise zero-padded up to the next
        32-byte boundary before the next payload starts, matching vanilla
        TEX sections.
    """
    from texture_helper import _image_payload_size, mipless_sampler_fields

    textures = list(parsed.textures.textures) if parsed.textures else []
    if not textures:
        _slogger.info("[BuildTEX] No textures; returning empty TEX section", source="hammerspace.main")
        return b''

    entries_by_index = {}
    if texture_plan is not None:
        for entry in texture_plan.entries:
            if not isinstance(entry.texture_index, int) or entry.texture_index < 0:
                raise ValueError(
                    f"[BuildTEX] invalid texture plan index {entry.texture_index!r}"
                )
            if entry.texture_index in entries_by_index:
                raise ValueError(f"[BuildTEX] duplicate texture plan index {entry.texture_index}")
            entries_by_index[entry.texture_index] = entry
        donor_count = len(textures)
        addition_indices = sorted(
            index for index in entries_by_index if index >= donor_count
        )
        expected_addition_indices = list(range(donor_count, donor_count + len(addition_indices)))
        if addition_indices != expected_addition_indices:
            raise ValueError(
                f"[BuildTEX] appended texture indices must be contiguous "
                f"{expected_addition_indices}, found {addition_indices}"
            )
        for index in addition_indices:
            entry = entries_by_index[index]
            template_index = entry.template_texture_index
            if template_index is None or template_index < 0 or template_index >= donor_count:
                raise ValueError(
                    f"[BuildTEX] appended texture {index} has invalid template texture "
                    f"{template_index}"
                )
            template = textures[template_index]
            if template.format in (0x8, 0x9, 0xA):
                raise ValueError(
                    f"[BuildTEX] appended texture {index} uses unsupported indexed "
                    f"template format 0x{template.format:02X}"
                )
            if entry.format != template.format:
                raise ValueError(
                    f"[BuildTEX] appended texture {index} format 0x{entry.format:02X} "
                    f"does not match template format 0x{template.format:02X}"
                )
            # Only the base level is encoded, so a mipmapped template's mip
            # sampler state (filter, LOD bias, mip count) must not carry over.
            max_lod = template.max_lod
            desc_unknown_at_10 = template.desc_unknown_at_10
            if len(desc_unknown_at_10) > 6 and desc_unknown_at_10[6]:
                max_lod, desc_unknown_at_10 = mipless_sampler_fields(
                    max_lod, desc_unknown_at_10
                )
                _slogger.info(
                    f"[BuildTEX] appended texture {index}: template {template_index} "
                    f"is mipmapped; writing single-level sampler state",
                    source="hammerspace.main",
                )
            textures.append(Texture(
                texture_index=index,
                width=entry.width,
                height=entry.height,
                format=template.format,
                palette_entries=entry.palette_entries,
                palette_format=entry.palette_format or 0,
                edge_lod_enable=template.edge_lod_enable,
                min_lod=template.min_lod,
                max_lod=max_lod,
                unpacked=template.unpacked,
                desc_unknown_at_10=desc_unknown_at_10,
                desc_unknown_at_1b=template.desc_unknown_at_1b,
                image_data_offset=0,
                image_data_length=0,
                palette_data_offset=None,
                palette_data_length=None,
            ))

    # --- Pass 1: determine payload bytes and dimensions for each texture ---
    image_payloads = []   # list of (texture_index, bytes) in descriptor order
    palette_payloads = [] # list of (texture_index, bytes) in descriptor order
    dims = {}             # texture_index -> (width, height)
    mip_counts = {}       # texture_index -> int

    for tex in textures:
        idx = tex.texture_index
        entry = entries_by_index.get(idx)
        if entry is not None:
            if entry.format != tex.format:
                raise ValueError(
                    f"[BuildTEX] texture {idx}: plan format 0x{entry.format:02X} "
                    f"does not match descriptor format 0x{tex.format:02X}"
                )
            # Re-encoded: validate payload size against the encoded dimensions.
            expected = _image_payload_size(entry.width, entry.height, entry.format)
            if len(entry.image_data) != expected:
                raise ValueError(
                    f"[BuildTEX] texture {idx}: encoded image payload is "
                    f"{len(entry.image_data)} bytes but {expected} bytes are "
                    f"expected for {entry.width}x{entry.height} format 0x{entry.format:02X}"
                )
            image_payloads.append((idx, entry.image_data))
            if entry.palette_data:
                palette_payloads.append((idx, entry.palette_data))
            dims[idx] = (entry.width, entry.height)
            # Single-image encoding path is base-only; reset mip count.
            mip_counts[idx] = 0
        else:
            # Cloned: read the payload from the DAT used during export.
            if not tex.image_data_offset or not tex.image_data_length:
                raise ValueError(
                    f"[BuildTEX] texture {idx}: cannot clone payload because "
                    f"image_data_offset={tex.image_data_offset} or "
                    f"image_data_length={tex.image_data_length} is zero"
                )
            image_bytes = donor.read(tex.image_data_offset, tex.image_data_length)
            if len(image_bytes) != tex.image_data_length:
                raise IOError(
                    f"[BuildTEX] texture {idx}: expected to read "
                    f"{tex.image_data_length} bytes at offset 0x{tex.image_data_offset:X} "
                    f"but only got {len(image_bytes)}"
                )
            image_payloads.append((idx, image_bytes))
            if tex.palette_data_offset and tex.palette_data_length:
                palette_bytes = donor.read(tex.palette_data_offset, tex.palette_data_length)
                if len(palette_bytes) != tex.palette_data_length:
                    raise IOError(
                        f"[BuildTEX] texture {idx}: expected to read "
                        f"{tex.palette_data_length} bytes at offset 0x{tex.palette_data_offset:X} "
                        f"but only got {len(palette_bytes)}"
                    )
                palette_payloads.append((idx, palette_bytes))
            dims[idx] = (tex.width, tex.height)
            mip_counts[idx] = tex.desc_unknown_at_10[6] if len(tex.desc_unknown_at_10) > 6 else 0

    clut_count = sum(
        1
        for tex in textures
        if (
            entries_by_index[tex.texture_index].palette_entries
            if tex.texture_index in entries_by_index
            else tex.palette_entries
        )
    )

    # --- Pass 2: lay out the section ---
    header_size = 4
    desc_size = 0x20
    desc_table_size = len(textures) * desc_size
    # Match the original TEX layout by starting the packed data region at the
    # first 32-byte boundary after the descriptor table. Every
    # individual image/palette payload start is also 32-byte aligned.
    data_start = (header_size + desc_table_size + 31) & ~31

    # Image payloads first, then palette payloads. Each payload start is
    # 32-byte aligned, matching vanilla TEX sections.
    cursor = data_start
    image_offsets = {}
    for idx, payload in image_payloads:
        image_offsets[idx] = cursor
        cursor = (cursor + len(payload) + 31) & ~31
    palette_offsets = {}
    for idx, payload in palette_payloads:
        palette_offsets[idx] = cursor
        cursor = (cursor + len(payload) + 31) & ~31

    out = bytearray()
    # Header
    out += struct.pack('>HH', len(textures), clut_count)
    # Descriptors
    for tex in textures:
        idx = tex.texture_index
        width, height = dims[idx]
        mip_count = mip_counts[idx]
        unknown_10 = bytearray(tex.desc_unknown_at_10)
        if len(unknown_10) < 7:
            unknown_10 = unknown_10 + bytes(7 - len(unknown_10))
        unknown_10[6] = mip_count & 0xFF
        unknown_1b = tex.desc_unknown_at_1b
        if len(unknown_1b) < 5:
            unknown_1b = unknown_1b + bytes(5 - len(unknown_1b))
        palette_offset = palette_offsets.get(idx, 0)
        out += struct.pack('>II', image_offsets[idx], palette_offset)
        out += struct.pack('>HH', height, width)
        out += struct.pack('>BBBB',
                       1 if tex.edge_lod_enable else 0,
                       int(tex.min_lod) & 0xFF,
                       int(tex.max_lod) & 0xFF,
                       tex.unpacked & 0xFF)
        out += bytes(unknown_10)
        out += struct.pack('>B', tex.format & 0xFF)
        out += struct.pack('>H', tex.palette_entries & 0xFFFF)
        out += struct.pack('>B', tex.palette_format & 0xFF)
        out += bytes(unknown_1b[:5])
    # Zero padding to reach the 32-byte-aligned data region (see data_start above).
    # This mirrors the original TEX sections' initial gap. It does not align
    # each payload independently.
    if len(out) < data_start:
        out += b'\x00' * (data_start - len(out))
    # Data region: each payload is followed by zero padding out to the next
    # 32-byte boundary, matching the offsets computed above.
    for idx, payload in image_payloads:
        out += payload
        padded_len = (len(payload) + 31) & ~31
        out += b'\x00' * (padded_len - len(payload))
    for idx, payload in palette_payloads:
        out += payload
        padded_len = (len(payload) + 31) & ~31
        out += b'\x00' * (padded_len - len(payload))

    _slogger.info(
        f"[BuildTEX] built {len(textures)} texture(s), "
        f"{len(image_payloads)} image payload(s), "
        f"{len(palette_payloads)} palette payload(s); "
        f"section size {len(out):,} bytes",
        source="hammerspace.main",
    )
    return bytes(out)


def _texture_replacement_overrides(model: dict, sluggie_path) -> dict[int, str]:
    """``TextureReplacements`` as ``png_overrides`` (donor index -> PNG in
    tex/): a vanilla texture no surface binds any more, refilled by the
    Blender export with a new PNG in that texture's own format."""
    import texture_helper as _tex
    descriptors = model.get('TextureDescriptors') or []
    overrides: dict[int, str] = {}
    for entry in model.get('TextureReplacements') or []:
        index = entry.get('TextureIndex')
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(descriptors):
            raise ValueError(f'TextureReplacements: TextureIndex {index!r} is not a donor texture')
        if index in overrides:
            raise ValueError(f'TextureReplacements: texture {index} is replaced twice')
        if int(descriptors[index].get('AdditionalMipCount') or 0):
            # The encode path skips mipmapped textures; the refill would be lost.
            raise ValueError(f'TextureReplacements: texture {index} is mipmapped and cannot be refilled')
        name = _tex.validate_texture_file_name(entry.get('TextureFileName'))
        if sluggie_path is None:
            raise ValueError('TextureReplacements require the sluggie path to resolve the tex/ folder')
        overrides[index] = _tex.resolve_texture_path(sluggie_path, name)
    return overrides


def _resolve_texture_plan(model: dict, sluggie_path, tex_png_overrides, texture_plan):
    """The texture plan of a ``tex='build'`` run: the caller's, or one built from the tex/ folder when
    the file reimports, replaces or adds textures (else None: every payload is cloned)."""
    # Resolved before the GPL append below so a custom submesh's
    # AdditionalTextureFileName can be patched with its final TEX index
    # immediately, instead of a placeholder re-patched later.
    additional_texture_descriptors = model.get('AdditionalTextureDescriptors') or []
    replacement_overrides = _texture_replacement_overrides(model, sluggie_path)
    if replacement_overrides:
        # An explicit override (single-texture CLI) wins over a refill.
        tex_png_overrides = {**replacement_overrides, **(tex_png_overrides or {})}
    if texture_plan is not None and (
        model.get('ReimportTextures') or tex_png_overrides or additional_texture_descriptors
    ):
        raise ValueError(
            'a caller-supplied texture_plan cannot be combined with '
            'ReimportTextures, png overrides, or AdditionalTextureDescriptors'
        )
    if texture_plan is None and (
        model.get('ReimportTextures') or tex_png_overrides or additional_texture_descriptors
    ):
        if sluggie_path is None:
            raise ValueError(
                "tex='build' with texture reimport inputs requires "
                "the sluggie path to resolve the tex/ folder; pass "
                "sluggie_path to BuildModelBlock"
            )
        import texture_helper as _tex
        additions = tuple(
            _tex.AdditionalTextureDescriptor(
                texture_file_name=entry['TextureFileName'],
                template_texture_index=int(entry['TemplateTextureIndex']),
            )
            for entry in additional_texture_descriptors
        )
        plan_kwargs = {
            'allow_dimension_change': True,
            'png_overrides': tex_png_overrides,
            'reimport_donor_textures': bool(model.get('ReimportTextures')),
        }
        if additions:
            plan_kwargs['additional_descriptors'] = additions
        texture_plan = _tex.build_hammerspace_texture_plan(
            sluggie_path,
            model.get('TextureDescriptors') or [],
            **plan_kwargs,
        )
        for _sk in texture_plan.skipped:
            _sk_fields = [f"expected {_sk.expected_payload_length} bytes"]
            if _sk.generated_payload_length is not None:
                _sk_fields.append(f"generated {_sk.generated_payload_length} bytes")
            _slogger.warning(
                f"texture {_sk.texture_index} ({_sk.texture_file_name}): "
                f"{', '.join(_sk_fields)}; left unchanged ({_sk.reason})",
                source='hammerspace.main',
            )
    return texture_plan
