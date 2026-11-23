"""Export, materials and textures: new materials and their templates, the image nodes of a material, texture
changes of donor surfaces and custom submeshes, the copy plan for a PNG linked from outside ``tex/``, and
the texture context of an export (folder, High / Low partner, added textures).

Imports ``ExportInplaceMesh`` only among the ``Export*`` modules, and never ``ExportSluggies``.
"""

import json
import os
import struct
from . import FieldCodec
from . import TextureConvert
from . import TextureSlotsCore
from . import ModelCheckCore
from .ExportInplaceMesh import (
    _to_bytes,
)


NEW_SURFACE_BODY_MESSAGE = (
    "Partial face moves and new materials are supported on rigid submeshes only; "
    "on the body, move every face of a surface or none"
)


def _find_new_materials(obj, json_submesh, submesh_index=None):
    """Return a list of (material_name, reason) for materials that are NOT donor surfaces.

    A material is a valid donor surface when:
      - new path (2.2+): it carries a SurfaceId that exists in the donor's display states, or
      - legacy path: its name ends with ``_mat{tex_idx}`` where tex_idx is a texture index
        present in the donor's FaceTextureIndices.

    An Add-material surface (``SluggiesNewSurface``) is accepted on a rigid
    donor submesh when its ``SluggiesSurfaceOwner`` is that submesh
    (``sm<submesh_index>``); it is rejected on
    the skinned body and when it belongs to another submesh. Any other
    material (no SurfaceId, unknown SurfaceId, or unparseable legacy name) is
    rejected.

    A custom submesh (``SluggiesCustomSubmesh``) has no donor submesh to compare
    against — it accepts exactly its own ``<CustomSubmeshId>_ds*`` SurfaceIds
    and its own Add-material surfaces
    (``SluggiesNewSurface`` with ``SluggiesSurfaceOwner`` = its
    ``CustomSubmeshId``) and rejects
    everything else.
    """
    if obj.get("SluggiesCustomSubmesh"):
        custom_id = str(obj.get("CustomSubmeshId"))
        own_prefix = f'{custom_id}_ds'
        new_materials = []
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue
            sid = mat.get("SurfaceId")
            if mat.get("SluggiesNewSurface"):
                owner = mat.get("SluggiesSurfaceOwner")
                if owner != custom_id:
                    new_materials.append((mat.name, f"belongs to {owner or 'another submesh'}, not {custom_id}"))
                continue
            if not sid or not sid.startswith(own_prefix):
                reason = (f"SurfaceId '{sid}' is not this custom submesh's own surface"
                          if sid else "no SurfaceId")
                new_materials.append((mat.name, reason))
        return new_materials

    display_states = json_submesh.get("DisplayStates", [])
    donor_sids = {
        ds.get("SurfaceId") for ds in display_states if ds.get("SurfaceId")
    }

    # Legacy donor texture indices (old exports without SurfaceId/FaceCount).
    # FaceTextureIndices is an encoded uint16 BE array, one texture index per face.
    donor_tex_idxs = set()
    fti_raw = json_submesh.get("FaceTextureIndicesEdited") or json_submesh.get("FaceTextureIndices")
    if fti_raw:
        try:
            raw = _to_bytes(fti_raw)
            n = len(raw) // 2
            donor_tex_idxs = set(struct.unpack(f'>{n}H', raw))
        except Exception:
            donor_tex_idxs = set()

    owner_key = f"sm{submesh_index}" if submesh_index is not None else None
    new_materials = []
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        sid = mat.get("SurfaceId")
        if mat.get("SluggiesNewSurface"):
            owner = mat.get("SluggiesSurfaceOwner")
            if owner_key is not None and owner != owner_key:
                new_materials.append((mat.name, f"belongs to {owner or 'another submesh'}, not {owner_key}"))
            continue
        if sid:
            if sid not in donor_sids:
                new_materials.append((mat.name, f"SurfaceId '{sid}' is not a donor surface"))
            continue
        # No SurfaceId: legacy material — must be named '{...}_mat{tex_idx}' with a
        # donor texture index.
        try:
            tex_idx = int(mat.name.rsplit('_mat', 1)[1])
        except (IndexError, ValueError):
            new_materials.append((mat.name, "no SurfaceId and not a donor material name"))
            continue
        if donor_tex_idxs and tex_idx not in donor_tex_idxs:
            new_materials.append((mat.name, f"texture index {tex_idx} is not a donor texture"))
    return new_materials


def _connected_image_texture_nodes(material):
    """Return image nodes reachable from the active Material Output surface."""
    node_tree = getattr(material, "node_tree", None)
    if not getattr(material, "use_nodes", False) or node_tree is None:
        return []

    output_nodes = [
        node for node in node_tree.nodes
        if getattr(node, "type", None) == 'OUTPUT_MATERIAL'
    ]
    active_outputs = [
        node for node in output_nodes if getattr(node, "is_active_output", False)
    ]
    if active_outputs:
        output = active_outputs[0]
    elif len(output_nodes) == 1:
        output = output_nodes[0]
    else:
        return []

    try:
        surface = output.inputs['Surface']
    except (KeyError, TypeError):
        return []

    found = []
    visited = set()

    def visit_node(node):
        # Each link.from_node read makes a new Python wrapper, so id() differs
        # for one node reached over several links (e.g. Color and Alpha).
        as_pointer = getattr(node, "as_pointer", None)
        identity = as_pointer() if callable(as_pointer) else id(node)
        if identity in visited:
            return
        visited.add(identity)
        if getattr(node, "type", None) == 'TEX_IMAGE':
            found.append(node)
        for input_socket in getattr(node, "inputs", ()):
            for link in getattr(input_socket, "links", ()):
                source = getattr(link, "from_node", None)
                if source is not None:
                    visit_node(source)

    for link in getattr(surface, "links", ()):
        source = getattr(link, "from_node", None)
        if source is not None:
            visit_node(source)
    return found


# Mirrors texture_helper._TEMPLATE_FOOTPRINT_FIELDS plus the fields an
# addition overwrites: what remains is what a template lends an appended
# texture (format and sampler fields). Duplicated because the add-on stays
# standalone.
_TEMPLATE_UNLENT_FIELDS = frozenset((
    "Width", "Height", "ImageDataOffset", "ImageDataLength",
    "ImagePayloadLength", "ImageDataCapacity", "TextureDescriptorOffset",
    "TextureIndex", "TextureFileName", "AdditionalMipCount",
    "PaletteEntries", "PaletteFormat",
))


def _template_signature(descriptor):
    """The format and sampler fields a template descriptor lends an addition."""
    return json.dumps(
        {k: v for k, v in descriptor.items() if k not in _TEMPLATE_UNLENT_FIELDS},
        sort_keys=True, default=str,
    )


def _resolve_material_texture_changes(
    object_submeshes,
    descriptors,
    tex_dir,
    path_resolver=os.path.abspath,
    validate_texture_files=True,
    skip_unset_images=False,
    require_donor_files=True,
    planned_copies=None,
):
    """Classify connected material images without mutating Blender or JSON data.

    *skip_unset_images*: an image node without an image keeps the material's
    texture instead of failing (a `_L_` model, whose PNGs live in the main
    model's folder and may not have been found on import).

    *require_donor_files* False: only a new PNG (an addition) must exist in
    tex/; an image named like a donor texture is not read (Reimport textures
    off: the donor payloads are kept).

    *planned_copies* (a dict, see `_plan_external_texture_copy`): an existing
    PNG outside tex/ that the export reads is planned as a copy into tex/
    instead of failing, and an image in another format (TextureConvert) as
    its PNG conversion; the caller performs the copies."""
    descriptor_by_name = {
        descriptor.get("TextureFileName"): int(descriptor["TextureIndex"])
        for descriptor in descriptors
        if descriptor.get("TextureFileName")
    }
    donor_count = len(descriptors)
    additions = []
    # (file name, template signature) -> appended index. One PNG shown by
    # several materials is appended once and shared, instead of being encoded
    # and stored once per material.
    appended_index_by_key = {}
    assignments = {}
    changed_materials = []

    for obj, _json_submesh in object_submeshes:
        for slot in obj.material_slots:
            material = slot.material
            if material is None:
                continue
            surface_id = material.get("SurfaceId")
            if not surface_id:
                continue
            if material.get("SluggiesNewSurface"):
                # An Add-material surface has no donor TextureIndex; its image
                # is resolved with the custom-submesh resolver instead.
                continue

            image_nodes = _connected_image_texture_nodes(material)
            if len(image_nodes) > 1:
                raise ValueError(
                    f"Multiple textures in one material are not supported: {material.name}"
                )
            if not image_nodes:
                continue
            image = getattr(image_nodes[0], "image", None)
            image_path = (
                getattr(image, "filepath_raw", "") or getattr(image, "filepath", "")
                if image is not None else ""
            )
            if not image_path and skip_unset_images:
                continue
            if not image_path:
                raise ValueError(f"Material image has no file path: {material.name}")

            resolved_path = path_resolver(image_path)
            file_name = os.path.basename(os.path.normpath(resolved_path))
            source_path = os.path.abspath(os.path.normpath(resolved_path))
            convert = TextureConvert.needs_conversion(file_name)
            problem = TextureConvert.convert_problem(source_path) if convert else None
            if problem:
                raise ValueError(
                    f"Material '{material.name}' image must be a "
                    f"{TextureConvert.FORMAT_NAMES}: {file_name} {problem}"
                )
            local_path = os.path.join(tex_dir, file_name)
            desired_index = descriptor_by_name.get(file_name)
            if planned_copies is not None and validate_texture_files and (
                require_donor_files or desired_index is None
            ) and os.path.isfile(source_path) and (convert or (
                os.path.normcase(os.path.dirname(source_path))
                != os.path.normcase(os.path.abspath(tex_dir))
            )):
                # Linked from outside tex/ (or another format, converted):
                # copied in (or an identical file there reused); a name tex/
                # holds with other bytes takes a free name, which then
                # appends a new texture.
                file_name = _plan_external_texture_copy(source_path, tex_dir, planned_copies)
                local_path = os.path.join(tex_dir, file_name)
                desired_index = descriptor_by_name.get(file_name)
            if validate_texture_files and file_name not in (planned_copies or {}) and (
                require_donor_files or desired_index is None
            ) and not os.path.isfile(local_path):
                raise ValueError(
                    f"Texture PNG for material '{material.name}' must exist in "
                    f"the resolved tex folder: {local_path}"
                )

            original_index = int(material.get("TextureIndex", -1))
            if original_index < 0 or original_index >= donor_count:
                raise ValueError(
                    f"Material '{material.name}' has invalid donor TextureIndex "
                    f"{original_index}"
                )
            if desired_index is None:
                key = (file_name, _template_signature(descriptors[original_index]))
                desired_index = appended_index_by_key.get(key)
            if desired_index is None:
                desired_index = donor_count + len(additions)
                if desired_index > 0x1FFF:
                    raise ValueError(
                        "the appended texture index exceeds the Type-1 13-bit field"
                    )
                additions.append({
                    "TextureFileName": file_name,
                    "TemplateTextureIndex": original_index,
                })
                appended_index_by_key[key] = desired_index

            if desired_index != original_index:
                if surface_id in assignments:
                    raise ValueError(
                        f"SurfaceId '{surface_id}' is assigned by multiple materials"
                    )
                assignments[surface_id] = desired_index
                changed_materials.append(material.name)

    return additions, assignments, changed_materials


def _custom_submesh_texture_layer(mode):
    """Decode a Type-1 ShaderMode hex string into (layer, texture_index).
    Bits 13-15 are the texture layer, bits 0-12 are the texture index. Duplicated from
    SubmeshTemplates._custom_submesh_texture_layer because the add-on stays
    standalone (no SluggiesTools imports)."""
    setting = int(mode, 16)
    return ((setting >> 13) & 7, setting & 0x1FFF)


def _custom_submesh_template_texture_index(model, template_source):
    """Resolve a CustomSubmesh's TemplateSource to the donor texture index
    its new descriptor should clone the GX format from. A custom submesh has no donor material of its own, so
    this walks the same donor DisplayStates a `rigid:`/`derived:`/`builtin:`
    template resolves against at build time (mirroring
    SubmeshTemplates._custom_submesh_rigid_records/_derived_records/
    _builtin_records) to find the effective layer-0 Type-1 texture index:

    - `rigid:<SurfaceId>`: the template submesh's own effective layer-0
      texture at that surface.
    - `derived:<SurfaceId>`: submesh 0's effective layer-0 texture at that
      surface.
    - `builtin:<name>`: the host model's own submesh 0, first layer-0
      texture encountered (builtin templates are model-independent and
      always rebind to the host's own submesh-0 texture).
    """
    submeshes = model.get('Submeshes') or []
    submesh0_states = (submeshes[0].get('DisplayStates') if submeshes else None) or []

    def _effective_layer0(states, up_to_index):
        layer0 = None
        for state in states[:up_to_index + 1]:
            if int(state.get('DisplayStateId', -1)) != 1:
                continue
            layer, texture_index = _custom_submesh_texture_layer(state['ShaderMode'])
            if layer == 0:
                layer0 = texture_index
        return layer0

    if template_source.startswith('rigid:'):
        surface_id = template_source[len('rigid:'):]
        for submesh in submeshes:
            states = submesh.get('DisplayStates') or []
            for index, state in enumerate(states):
                if state.get('SurfaceId') != surface_id:
                    continue
                layer0 = _effective_layer0(states, index)
                if layer0 is None:
                    raise ValueError(
                        f"Template surface '{surface_id}' has no layer-0 texture"
                    )
                return layer0
        raise ValueError(f"Template surface '{surface_id}' not found in donor submeshes")

    if template_source.startswith('derived:'):
        surface_id = template_source[len('derived:'):]
        for index, state in enumerate(submesh0_states):
            if state.get('SurfaceId') != surface_id:
                continue
            layer0 = _effective_layer0(submesh0_states, index)
            if layer0 is None:
                raise ValueError(
                    f"Derived surface '{surface_id}' has no layer-0 texture"
                )
            return layer0
        raise ValueError(f"Derived surface '{surface_id}' not found in submesh 0")

    if template_source.startswith('builtin:'):
        for state in submesh0_states:
            if int(state.get('DisplayStateId', -1)) != 1:
                continue
            layer, texture_index = _custom_submesh_texture_layer(state['ShaderMode'])
            if layer == 0:
                return texture_index
        raise ValueError("Host model's submesh 0 has no layer-0 texture")

    raise ValueError(f"Unrecognized TemplateSource: {template_source!r}")


def _plan_external_texture_copy(source_path, tex_dir, planned_copies, reserved_names=()):
    """Pick the tex/ file name an image outside tex/ is copied to on export.

    The patcher only reads bare file names from the model's tex/ folder, so an
    external PNG is copied in. The first free name among ``<stem>.png``,
    ``<stem>_1.png``, ... wins, where a name is usable when tex/ has no such
    file (and no other source is already planned for it), or when the file
    there is byte-identical to the source. That reuse keeps repeated exports
    from piling up copies, and an identical copy of a donor PNG still rebinds
    to that donor texture. A `reserved_names` entry (a donor descriptor's
    TextureFileName) is never claimed as a fresh copy target, even when its
    file is missing from tex/.

    An image in another format is converted first (TextureConvert): its PNG
    is what is copied and compared, under ``<stem>.png``, ``<stem>_1.png``,
    ... Raises ValueError when it cannot be converted.

    `planned_copies` maps tex/ file name -> source path (the converted PNG
    for another format) and is updated in place. Returns the chosen file name.
    """
    file_name = os.path.basename(source_path)
    stem, ext = os.path.splitext(file_name)
    if TextureConvert.needs_conversion(file_name):
        source_path = TextureConvert.converted_png(source_path)
        file_name, ext = TextureConvert.converted_name(file_name), '.png'
    try:
        with open(source_path, 'rb') as source_file:
            source_bytes = source_file.read()
    except OSError:
        source_bytes = None

    suffix = 0
    while True:
        candidate = file_name if suffix == 0 else f"{stem}_{suffix}{ext}"
        suffix += 1
        planned_source = planned_copies.get(candidate)
        if planned_source is not None:
            if os.path.normcase(planned_source) == os.path.normcase(source_path):
                return candidate
            continue
        target_path = os.path.join(tex_dir, candidate)
        if not os.path.exists(target_path):
            if candidate in reserved_names:
                continue
            planned_copies[candidate] = source_path
            return candidate
        if source_bytes is not None and os.path.isfile(target_path):
            try:
                with open(target_path, 'rb') as target_file:
                    if target_file.read() == source_bytes:
                        return candidate
            except OSError:
                pass


def _resolve_custom_submesh_texture_changes(
    custom_submesh_entries,
    descriptors,
    tex_dir,
    path_resolver=os.path.abspath,
    validate_texture_files=True,
    planned_copies=None,
):
    """Resolve each custom submesh material's connected image into a
    TextureAssignment. Applies the same
    per-material image-inspection rules as _resolve_material_texture_changes,
    but a custom submesh has no donor material to read a `TextureIndex`
    from: each entry instead supplies the donor texture index its template
    clones its GX format from (`_custom_submesh_template_texture_index`).

    Unlike donor materials, a custom submesh image may live outside tex/:
    it is planned as a copy into tex/ (`_plan_external_texture_copy`, which
    also converts other formats to PNG) that the caller performs once the
    export is otherwise validated.

    `custom_submesh_entries` is a list of (material, template_texture_index)
    pairs. Returns (additions, assignment_by_material_name, texture_copies):
    - `additions` is in the same shape as _resolve_material_texture_changes
      produces, appendable to AdditionalTextureDescriptors.
    - `assignment_by_material_name` maps each material's name to a dict
      usable directly as a CustomSubmesh TextureAssignment: either
      {"DonorTextureIndex": n} when the image already matches an existing
      descriptor (a rebind, "as before"), or
      {"AdditionalTextureFileName": name} for a newly appended one.
    - `texture_copies` is a list of (source_path, tex_dir_path) pairs.

    *planned_copies*: a dict shared with the other resolvers of one export,
    so no two sources claim one tex/ name (`texture_copies` then lists the
    shared plan).
    """
    descriptor_by_name = {
        descriptor.get("TextureFileName"): int(descriptor["TextureIndex"])
        for descriptor in descriptors
        if descriptor.get("TextureFileName")
    }
    donor_count = len(descriptors)
    additions = []
    assignments = {}
    if planned_copies is None:
        planned_copies = {}

    for material, template_texture_index in custom_submesh_entries:
        if template_texture_index < 0 or template_texture_index >= donor_count:
            raise ValueError(
                f"Material '{material.name}' has invalid template texture index "
                f"{template_texture_index}"
            )

        image_nodes = _connected_image_texture_nodes(material)
        if len(image_nodes) > 1:
            raise ValueError(
                f"Multiple textures in one material are not supported: {material.name}"
            )
        if not image_nodes:
            raise ValueError(
                f"Custom submesh material has no connected texture: {material.name}"
            )
        image = getattr(image_nodes[0], "image", None)
        image_path = (
            getattr(image, "filepath_raw", "") or getattr(image, "filepath", "")
            if image is not None else ""
        )
        if not image_path:
            raise ValueError(f"Material image has no file path: {material.name}")

        resolved_path = os.path.abspath(os.path.normpath(path_resolver(image_path)))
        file_name = os.path.basename(resolved_path)
        convert = TextureConvert.needs_conversion(file_name)
        problem = TextureConvert.convert_problem(resolved_path) if convert else None
        if problem:
            raise ValueError(
                f"Material '{material.name}' image must be a "
                f"{TextureConvert.FORMAT_NAMES}: {file_name} {problem}"
            )
        if validate_texture_files and not os.path.isfile(resolved_path):
            raise ValueError(
                f"Texture {'image' if convert else 'PNG'} for material "
                f"'{material.name}' does not exist: {resolved_path}"
            )
        in_tex_dir = (
            os.path.normcase(os.path.dirname(resolved_path))
            == os.path.normcase(os.path.abspath(tex_dir))
        )
        if convert or not in_tex_dir:
            file_name = _plan_external_texture_copy(
                resolved_path, tex_dir, planned_copies, descriptor_by_name
            )

        desired_index = descriptor_by_name.get(file_name)
        if desired_index is None:
            desired_index = donor_count + len(additions)
            if desired_index > 0x1FFF:
                raise ValueError(
                    "the appended texture index exceeds the Type-1 13-bit field"
                )
            additions.append({
                "TextureFileName": file_name,
                "TemplateTextureIndex": template_texture_index,
            })
            assignments[material.name] = {"AdditionalTextureFileName": file_name}
        else:
            assignments[material.name] = {"DonorTextureIndex": desired_index}

    texture_copies = [
        (source_path, os.path.join(tex_dir, name))
        for name, source_path in planned_copies.items()
    ]
    return additions, assignments, texture_copies


def _custom_submesh_material(obj):
    """The material carrying this custom submesh's own ``<CustomSubmeshId>_ds*``
    SurfaceId (the one the Add submesh operator created), first slot wins."""
    prefix = f'{obj.get("CustomSubmeshId")}_ds'
    for slot in obj.material_slots:
        material = slot.material
        if material is not None and str(material.get("SurfaceId") or "").startswith(prefix):
            return material
    return None


def _merge_texture_additions(donor_additions, custom_additions):
    """Append custom submesh texture additions after the donor-material ones.

    Donor additions keep their positions, so the appended indices already
    written into DesiredTextureAssignments stay valid. A PNG that is already
    being appended (by a donor material or another custom submesh) is not
    appended twice: custom submeshes bind by file name, so they share it."""
    merged = list(donor_additions)
    names = {addition["TextureFileName"] for addition in merged}
    for addition in custom_additions:
        if addition["TextureFileName"] not in names:
            merged.append(addition)
            names.add(addition["TextureFileName"])
    return merged


def _partner_added_texture_descriptors(descriptors, additions):
    """Descriptors for the textures a main model's export appends, so its
    `_L_` partner can bind them as existing textures.

    See TextureSlotsCore.partner_added_texture_descriptors.
    """
    return TextureSlotsCore.partner_added_texture_descriptors(descriptors, additions)


def _resolve_export_texture_context(sluggie_path, model, with_owner=False):
    """Return descriptor/path context, borrowing it for a paired `_L_` model.

    A borrowed list also holds the main model's appended textures (indices
    after its own descriptors): the `_L_` model binds textures by index into
    the main model's TEX, so a custom submesh on it can use them once the
    main model is patched.

    *with_owner* appends the paired main model's .sluggie path (None when
    nothing is borrowed), for error messages."""
    def _result(descriptors, tex_dir, owns, owner_path=None):
        if with_owner:
            return descriptors, tex_dir, owns, owner_path
        return descriptors, tex_dir, owns

    model_dir = os.path.dirname(os.path.abspath(sluggie_path))
    local_descriptors = model.get("TextureDescriptors") or []
    if local_descriptors:
        return _result(local_descriptors, os.path.join(model_dir, 'tex'), True)

    folder_name = os.path.basename(model_dir)
    marker_index = folder_name.lower().find('_l_')
    if marker_index < 0:
        return _result([], os.path.join(model_dir, 'tex'), False)

    paired_suffix = folder_name[marker_index + 3:].lower()
    parent_dir = os.path.dirname(model_dir)
    candidates = []
    try:
        sibling_names = sorted(os.listdir(parent_dir))
    except OSError:
        sibling_names = []
    for sibling_name in sibling_names:
        sibling_dir = os.path.join(parent_dir, sibling_name)
        sibling_lower = sibling_name.lower()
        if (
            not os.path.isdir(sibling_dir)
            or '_l_' in sibling_lower
            or not sibling_lower.endswith('_' + paired_suffix)
        ):
            continue
        tex_dir = os.path.join(sibling_dir, 'tex')
        if not os.path.isdir(tex_dir):
            continue
        try:
            sluggie_names = sorted(
                name for name in os.listdir(sibling_dir)
                if name.lower().endswith('.sluggie')
            )
        except OSError:
            continue
        owners = {}
        for sluggie_name in sluggie_names:
            candidate_path = os.path.join(sibling_dir, sluggie_name)
            try:
                candidate_model = FieldCodec.load_sluggie(candidate_path).get('SluggiesModel', {})
            except (OSError, ValueError):
                continue
            if candidate_model.get('TextureDescriptors'):
                owners[candidate_path] = candidate_model
        # Several High files in one folder (variants saved under new names):
        # the one named like this Low file, else the exported one.
        owner_path = ModelCheckCore.pick_partner(list(owners), sluggie_path)
        if owner_path is None and owners:
            raise ValueError(
                f"Low-poly model '{folder_name}': the main model folder '{sibling_name}' holds several "
                f".sluggie files ({', '.join(os.path.basename(p) for p in owners)}) and none is named "
                "like this one or after its folder; rename the one to pair with"
            )
        if owner_path is not None:
            owner_model = owners[owner_path]
            descriptors = owner_model['TextureDescriptors']
            candidates.append((
                TextureSlotsCore.replaced_texture_descriptors(
                    descriptors, owner_model.get('TextureReplacements'),
                ) + _partner_added_texture_descriptors(
                    descriptors,
                    owner_model.get('AdditionalTextureDescriptors') or [],
                ),
                tex_dir,
                owner_path,
            ))

    if len(candidates) > 1:
        raise ValueError(
            f"Low-poly model '{folder_name}' matches multiple sibling texture owners"
        )
    if candidates:
        descriptors, tex_dir, owner_path = candidates[0]
        return _result(descriptors, tex_dir, False, owner_path)
    return _result([], os.path.join(model_dir, 'tex'), False)


# ---------------------------------------------------------------------------
# Rigid rebuild: bpy glue around
# RigidRebuildExport. A donor rigid submesh whose edit the in-place fields
# cannot hold (changed topology, Object Mode transform, kept world position on
# another bone, faces moved between surfaces) is exported as a whole
# Submeshes[i].RigidRebuild instead.
# ---------------------------------------------------------------------------

def _empty_image_node_errors(objects):
    """An Image Texture node without an image on any Sluggies
    material (donor, new surface, custom submesh) cancels the export, whatever
    the texture toggles say. A new-surface or custom-submesh material with no
    image node at all is blocked the same way; a donor material without one
    keeps its donor texture."""
    errors = []
    for obj in objects:
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or not (mat.get("SurfaceId") or mat.get("SluggiesNewSurface")):
                continue
            image_nodes = _connected_image_texture_nodes(mat)
            if any(getattr(node, "image", None) is None for node in image_nodes):
                errors.append(
                    f"'{mat.name}' on {obj.name} has an Image Texture node without an image; "
                    "load a PNG into it (or remove the node) before exporting"
                )
            elif not image_nodes and (mat.get("SluggiesNewSurface") or obj.get("SluggiesCustomSubmesh")):
                errors.append(
                    f"'{mat.name}' on {obj.name} has no image in its texture node; "
                    "load a PNG before exporting"
                )
    return errors
