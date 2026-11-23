"""Surfaces of the tools: template lookups among the scene's materials, the plan and metadata of a new
surface (``_new_surface_plan``, ``_apply_new_surface_plan``), custom submesh materials and textures,
solid textures for converted materials, and material slot compaction. The node tree and the metadata
writer themselves are ``SurfaceMaterials``, which calls back into this module lazily.

Imports ``SceneModels`` only among the tools modules. Never imports ``SluggiesToolsPanel``.
"""

import os
import bpy
from . import ModelCheckCore
from . import RigidRebuildExport
from . import SluggiePath
from . import TemplateSources
from . import TextureFolder
from .ExportTextures import (
    _connected_image_texture_nodes,
    _custom_submesh_template_texture_index,
    _resolve_export_texture_context,
)
from .SurfaceMaterials import (
    _build_material_nodes,
    _create_material,
    _new_image_texture_node,
    _set_surface_material_metadata,
)
from .SceneModels import (
    _INVALID_SUBMESH_NAME_CHARS_RE,
    _SURFACE_ID_RE,
    _add_material_mesh_kind,
    _donor_mesh_objects,
    _model_is_stadium,
    _owning_armature,
    _submesh_index_of,
)


_CUSTOM_SUBMESH_TEXTURE_SIZE = 256


def _template_source_materials(context, arm_obj):
    materials = []
    for obj in _donor_mesh_objects(context, arm_obj):
        comp_count = obj.get('VertexBufferCompCount')
        if comp_count not in (3, 6):
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or not mat.get('SurfaceId') or mat.get('SluggiesNewSurface'):
                continue
            materials.append(TemplateSources.TemplateSourceMaterial(
                surface_id=mat['SurfaceId'],
                comp_count=int(comp_count),
                shader_mode=mat.get('ShaderMode', ''),
            ))
    return materials


def _material_by_surface_id(context, arm_obj, surface_id):
    """The scene material carrying *surface_id*, donor or custom, or None."""
    for obj in context.view_layer.objects:
        if obj.type != 'MESH' or obj.parent is not arm_obj:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is not None and mat.get('SurfaceId') == surface_id:
                return mat
    return None


def _submesh0_material(context, arm_obj):
    """Any submesh 0 donor material, used as the wrap-mode fallback for
    `builtin:` templates."""
    for obj in _donor_mesh_objects(context, arm_obj):
        if _submesh_index_of(obj) != 0:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is not None and _SURFACE_ID_RE.match(mat.get('SurfaceId', '') or ''):
                return mat
    return None


def _template_shader_mode(context, arm_obj, template_source):
    if template_source.startswith('builtin:'):
        template = TemplateSources.BUILTIN_TEMPLATES.get(template_source[len('builtin:'):])
        return template.shader_mode if template is not None else ''
    surface_id = template_source.split(':', 1)[1] if ':' in template_source else ''
    mat = _material_by_surface_id(context, arm_obj, surface_id)
    return mat.get('ShaderMode', '') if mat is not None else ''


def _template_specular_strength(context, arm_obj, template_source):
    """Starting specular strength for a surface made from *template_source*:
    the donor surface's current value (including an edit made with Set
    Specular Strength) for `rigid:`/`derived:`, the built-in default
    otherwise."""
    surface_id = TemplateSources.template_surface_id(template_source)
    if surface_id:
        mat = _material_by_surface_id(context, arm_obj, surface_id)
        if mat is not None and 'SpecularStrength' in mat:
            return max(0, min(255, int(mat['SpecularStrength'])))
    return TemplateSources.BUILTIN_DEFAULT_SPECULAR_STRENGTH


def _set_custom_surface_metadata(mat, shader_mode, specular_strength):
    """Metadata for a custom submesh's surface. No ShaderMode property: the
    shader comes from the template source only, so an editable value would be
    silently ignored on export. A surface whose template draws with a
    specular shader gets DisplayStateId 7 and SpecularStrength, which the Set
    Specular Strength dialog edits and the exporter writes; others get
    neither."""
    if shader_mode not in TemplateSources.SPECULAR_SHADER_MODES:
        return
    mat['DisplayStateId'] = 7
    mat.id_properties_ui('DisplayStateId').update(
        description="GX display-state command type for this surface.")
    mat['SpecularStrength'] = int(specular_strength)
    mat.id_properties_ui('SpecularStrength').update(
        min=0, max=255, soft_min=0, soft_max=255,
        description="Specular highlight intensity (0-255, linear TEV multiplier). "
        "Edit via the 'Manage Materials' panel button.")


def _new_surface_metadata(shader_mode, specular_strength):
    """ds_entry for _set_surface_material_metadata on a new Type-7 surface;
    the param bytes carry the starting specular strength (index 0)."""
    return {
        'DisplayStateId': 7,
        'ShaderMode': shader_mode,
        'DisplayStateParamBytes': bytes([specular_strength, 0, 0]).hex(),
    }


def _template_wrap_modes(context, arm_obj, template_source):
    """(WrapS, WrapT) copied from the template's own imported material;
    `builtin:` has no donor
    material of its own, so it falls back to submesh 0's."""
    if not template_source.startswith('builtin:'):
        surface_id = template_source.split(':', 1)[1] if ':' in template_source else ''
        mat = _material_by_surface_id(context, arm_obj, surface_id)
        if mat is not None:
            return int(mat.get('WrapS', 1)), int(mat.get('WrapT', 1))
    mat = _submesh0_material(context, arm_obj)
    if mat is not None:
        return int(mat.get('WrapS', 1)), int(mat.get('WrapT', 1))
    return 1, 1


def _low_poly_texture_warning(arm_obj):
    """Dialog warning when this model borrows its tex/ folder from a paired
    high-poly export (plan: 'a new PNG must come from the paired high-poly
    model's tex/'), or None when the model owns its own tex/ folder."""
    sluggie_path = SluggiePath.resolve(arm_obj)
    if not sluggie_path:
        return None
    try:
        model = SluggiePath.load_model(sluggie_path)
    except (OSError, ValueError):
        return None
    try:
        _descriptors, _tex_dir, owns_textures = _resolve_export_texture_context(sluggie_path, model)
    except ValueError:
        return None
    if owns_textures:
        return None
    return "Low-poly model: load a PNG from the high-poly model's tex/ folder"


def _default_new_material_name(obj):
    n = 0
    while f'{obj.name}_new{n}' in bpy.data.materials:
        n += 1
    return f'{obj.name}_new{n}'


def _validate_material_name(name):
    name = name.strip()
    if not name:
        return "Material name cannot be empty"
    if _INVALID_SUBMESH_NAME_CHARS_RE.search(name):
        return 'Material name cannot contain any of: < > : " / \\ | ? *'
    if name in bpy.data.materials:
        return f"A material named {name!r} already exists"
    return None


def _create_custom_submesh_material(obj, custom_submesh_id, image, shader_mode, specular_strength):
    surface_id = f'{custom_submesh_id}_ds0'
    mat = _create_material(f'{obj.name}_{surface_id}', image, wrap_s=1)
    _set_custom_primary_surface(mat, custom_submesh_id, shader_mode, specular_strength)
    obj.data.materials.append(mat)
    return mat


def _set_custom_primary_surface(mat, custom_submesh_id, shader_mode, specular_strength):
    """Make *mat* the custom submesh's own surface, `<CustomSubmeshId>_ds0`."""
    mat['SurfaceId'] = f'{custom_submesh_id}_ds0'
    mat.id_properties_ui('SurfaceId').update(
        description="Stable draw-state identity for this custom submesh. Do not delete.")
    _set_custom_surface_metadata(mat, shader_mode, specular_strength)


def _write_custom_submesh_texture(sluggie_path, submesh_name):
    """A blank RGBA PNG in the model's tex/ directory (plan step 3), named
    directly after *submesh_name* (plan step 3 extension).

    A lowpoly (`_L_`) model has no TextureDescriptors and no tex/ folder of
    its own — like export, it borrows the paired highpoly model's tex/
    folder (see _resolve_export_texture_context), so the new PNG lands
    where the rest of that model's textures already live.

    Returns (image, reused, error). An existing PNG of that name is never
    overwritten: it is loaded as-is and *reused* is True.
    """
    try:
        model = SluggiePath.load_model(sluggie_path)
    except (OSError, ValueError):
        model = {}
    try:
        _descriptors, tex_dir, _owns_textures = _resolve_export_texture_context(sluggie_path, model)
    except ValueError as exc:
        return None, False, str(exc)
    file_name = f'{submesh_name}.png'
    file_path = os.path.join(tex_dir, file_name)
    if os.path.exists(file_path):
        try:
            image = bpy.data.images.load(file_path, check_existing=True)
        except RuntimeError as exc:
            return None, False, f"Could not load existing {file_name} from tex/: {exc}"
        return image, True, None

    image = None
    try:
        os.makedirs(tex_dir, exist_ok=True)
        image = bpy.data.images.new(
            name=file_name,
            width=_CUSTOM_SUBMESH_TEXTURE_SIZE,
            height=_CUSTOM_SUBMESH_TEXTURE_SIZE,
            alpha=True,
        )
        image.filepath_raw = file_path
        image.file_format = 'PNG'
        image.save()
    except (OSError, RuntimeError) as exc:
        if image is not None:   # no image datablock without its file
            bpy.data.images.remove(image)
        return None, False, f"Could not write tex/{file_name}: {exc}"
    return image, False, None


def _new_surface_plan(context, arm_obj, obj, template_source, existing_surface_ids=None):
    """Everything Add material writes onto a new surface of *obj* made from
    *template_source*, checked before any material is touched. Also used by
    Check Models to turn a plain Blender material into such a surface.
    *existing_surface_ids*, when given, stands for the SurfaceIds of
    bpy.data.materials (a caller that plans several surfaces keeps it up to
    date instead of a scan per surface). Raises ValueError with the reason."""
    kind, submesh_index = _add_material_mesh_kind(obj)
    if kind is None:
        raise ValueError("Select a donor or custom submesh")
    owner = f'sm{submesh_index}' if kind == 'donor' else obj.get('CustomSubmeshId')
    if not owner:
        raise ValueError(f"{obj.name} has no CustomSubmeshId; re-create it with Add New Submesh")
    if existing_surface_ids is None:
        existing_surface_ids = _surface_ids_in_file()
    surface_id = TemplateSources.next_new_surface_key(owner, existing_surface_ids)

    sluggie_path = SluggiePath.resolve(arm_obj)
    if not sluggie_path:
        raise ValueError(SluggiePath.missing_message(arm_obj, "Add material"))
    try:
        model = SluggiePath.load_model(sluggie_path)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read {sluggie_path!r}: {exc}") from exc
    template_texture_index = _custom_submesh_template_texture_index(model, template_source)
    wrap_s, wrap_t = _template_wrap_modes(context, arm_obj, template_source)
    return {
        'kind': kind,
        'owner': owner,
        'surface_id': surface_id,
        'template_source': template_source,
        'template_texture_index': template_texture_index,
        'shader_mode': _template_shader_mode(context, arm_obj, template_source),
        'specular_strength': _template_specular_strength(context, arm_obj, template_source),
        'wrap_s': wrap_s,
        'wrap_t': wrap_t,
    }


def _surface_ids_in_file():
    return [mat.get('SurfaceId', '') for mat in bpy.data.materials if mat.get('SurfaceId')]


def _apply_new_surface_plan(mat, plan):
    """Write a `_new_surface_plan` onto *mat*."""
    mat['SluggiesNewSurface'] = True
    mat.id_properties_ui('SluggiesNewSurface').update(
        description="Marks a Blender-only surface created by Add material, absent from the donor.")
    mat['SurfaceId'] = plan['surface_id']
    mat.id_properties_ui('SurfaceId').update(
        description="Stable draw-state identity for this added surface. Do not delete.")
    mat['SluggiesSurfaceOwner'] = plan['owner']
    mat.id_properties_ui('SluggiesSurfaceOwner').update(
        description="The submesh (donor sm<N> or CustomSubmeshId) this surface belongs to.")
    mat['TemplateSource'] = plan['template_source']
    mat.id_properties_ui('TemplateSource').update(
        description="Donor or built-in template this surface's shading is cloned from.")
    mat['TemplateTextureIndex'] = plan['template_texture_index']
    mat.id_properties_ui('TemplateTextureIndex').update(
        description="Donor texture index this surface's GX format clones; never a fallback texture.")
    if plan['kind'] == 'donor':
        _set_surface_material_metadata(
            mat, _new_surface_metadata(plan['shader_mode'], plan['specular_strength']))
    else:
        _set_custom_surface_metadata(mat, plan['shader_mode'], plan['specular_strength'])
    mat['WrapS'] = plan['wrap_s']
    mat['WrapT'] = plan['wrap_t']
    mat.id_properties_ui('WrapS').update(description="GX wrap mode for U, copied from the template.")
    mat.id_properties_ui('WrapT').update(description="GX wrap mode for V, copied from the template.")


# Everything the add-on writes on a material. A material that becomes a
# surface of a custom submesh loses these first, so no imported state
# (another submesh's SurfaceId, TextureIndex, ShaderMode, ...) survives.
_SLUGGIES_MATERIAL_PROPS = (
    'SurfaceId', 'SluggiesNewSurface', 'SluggiesSurfaceOwner', 'TemplateSource',
    'TemplateTextureIndex', 'TextureIndex', 'DisplayStateId', 'ShaderMode',
    'DisplayStateParamBytes', 'SpecularStrength', 'WrapS', 'WrapT',
)


def _model_tex_dir(sluggie_path):
    """(model, tex dir, error) for the .sluggie at *sluggie_path*; the tex dir
    is the one export reads (a Low model's is its High model's)."""
    try:
        model = SluggiePath.load_model(sluggie_path)
    except (OSError, ValueError) as exc:
        return None, None, f"Could not read {sluggie_path!r}: {exc}"
    try:
        _descriptors, tex_dir, _owns = _resolve_export_texture_context(sluggie_path, model)
    except ValueError as exc:
        return model, None, str(exc)
    return model, tex_dir, None


def _linear_to_srgb(value):
    value = max(0.0, min(1.0, value))
    return value * 12.92 if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def _new_solid_texture(tex_dir, base_name, rgb):
    """A _CUSTOM_SUBMESH_TEXTURE_SIZE PNG filled with the linear colour *rgb*,
    saved into *tex_dir* under a free name after *base_name*. Always opaque:
    any alpha below 255 in a PNG is drawn as transparency."""
    os.makedirs(tex_dir, exist_ok=True)
    stem = _INVALID_SUBMESH_NAME_CHARS_RE.sub('_', base_name).strip(' .') or 'texture'
    file_name = ModelCheckCore.free_file_name(f'{stem}.png', os.listdir(tex_dir))
    image = bpy.data.images.new(
        name=file_name,
        width=_CUSTOM_SUBMESH_TEXTURE_SIZE,
        height=_CUSTOM_SUBMESH_TEXTURE_SIZE,
        alpha=True,
    )
    # generated_color is display (sRGB) space, shader colours are linear.
    image.generated_color = (*(_linear_to_srgb(c) for c in rgb[:3]), 1.0)
    image.filepath_raw = os.path.join(tex_dir, file_name)
    image.file_format = 'PNG'
    image.save()
    return image


def _material_output_node(tree):
    outputs = [n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL']
    active = [n for n in outputs if n.is_active_output]
    return (active or outputs or [None])[0]


def _shader_color_input(mat):
    """The unlinked Base Color/Color input of the shader *mat* draws with, or None."""
    tree = getattr(mat, 'node_tree', None)
    if tree is None or not getattr(mat, 'use_nodes', True):
        return None
    output = _material_output_node(tree)
    surface = output.inputs.get('Surface') if output is not None else None
    if surface is None or not surface.links:
        return None
    shader = surface.links[0].from_node
    for name in ('Base Color', 'Color'):
        socket = shader.inputs.get(name)
        if socket is not None and not socket.is_linked:
            return socket
    return None


def _fix_surface_image(context, mat, image, tex_dir):
    """Save *image* as a PNG in tex/ when it has no PNG file on disk (generated,
    packed or another format). Returns a note, or None when nothing was needed."""
    from . import ModelCheck  # it imports this module
    path = ModelCheck._image_path(image)
    exists = bool(path) and os.path.isfile(path)
    if exists and path.lower().endswith('.png'):
        return None
    if path and not exists and image.packed_file is None:
        return f"'{mat.name}': its image file {path} is missing"
    try:
        ModelCheck.fix_image_to_tex(context, image.name, tex_dir)
    except RuntimeError as exc:
        return f"'{mat.name}': could not save '{image.name}' as a PNG ({exc})"
    return f"'{mat.name}': saved its image as tex/{os.path.basename(image.filepath_raw)}"


def _ensure_surface_texture(context, mat, tex_dir):
    """Give *mat* the one PNG texture a custom submesh surface needs. Without
    one, a PNG in the material's colour is made and wired into its shader (or
    into a fresh importer-style node tree when the shader has no free colour
    input). Returns a note, or None."""
    nodes = _connected_image_texture_nodes(mat)
    if len(nodes) > 1:
        return f"'{mat.name}': several textures connected; keep only one"
    if nodes and nodes[0].image is not None:
        return _fix_surface_image(context, mat, nodes[0].image, tex_dir)
    color_input = None if nodes else _shader_color_input(mat)
    rgb = color_input.default_value if color_input is not None else mat.diffuse_color
    image = _new_solid_texture(tex_dir, mat.name, rgb)
    if nodes:
        nodes[0].image = image
    elif color_input is not None:
        tex_node = _new_image_texture_node(
            mat.node_tree.nodes, color_input.node.location.x - 80, color_input.node.location.y)
        tex_node.image = image
        mat.node_tree.links.new(tex_node.outputs['Color'], color_input)
    else:
        _build_material_nodes(mat, image)
    return f"'{mat.name}': new texture tex/{image.name}"


def _is_primary_surface_of(mat, custom_submesh_id):
    return not mat.get('SluggiesNewSurface') \
        and str(mat.get('SurfaceId') or '').startswith(f'{custom_submesh_id}_ds')


def _is_added_surface_of(mat, custom_submesh_id):
    return bool(mat.get('SluggiesNewSurface')) \
        and mat.get('SluggiesSurfaceOwner') == custom_submesh_id \
        and RigidRebuildExport.new_surface_key_matches(custom_submesh_id, str(mat.get('SurfaceId') or '')) \
        and all(prop in mat for prop in ModelCheckCore.NEW_SURFACE_PROPS)


def _compact_material_slots(obj):
    """One data slot per material. Object-linked slots move to the mesh,
    empty slots and repeats are dropped, and faces on a dropped slot move to
    the primary material: the one already carrying the submesh's own
    surface, else the one most faces use. A mesh without materials gets a new
    one. Returns (materials in slot order, primary)."""
    mesh = obj.data
    for slot in obj.material_slots:
        if slot.link == 'OBJECT':
            mat = slot.material
            slot.link = 'DATA'
            slot.material = mat
    slot_materials = [slot.material for slot in obj.material_slots]
    face_slots = [0] * len(mesh.polygons)
    mesh.polygons.foreach_get('material_index', face_slots)

    materials, faces = [], {}
    for mat in slot_materials:
        if mat is not None and mat not in materials:
            materials.append(mat)
    for index in face_slots:
        mat = slot_materials[index] if index < len(slot_materials) else None
        if mat is not None:
            faces[mat.name] = faces.get(mat.name, 0) + 1
    custom_submesh_id = str(obj['CustomSubmeshId'])
    primary = next((m for m in materials if _is_primary_surface_of(m, custom_submesh_id)), None)
    if primary is None and materials:
        primary = max(materials, key=lambda m: faces.get(m.name, 0))
    if primary is None:
        primary = bpy.data.materials.new(name=f'{obj.name}_{custom_submesh_id}_ds0')
        materials.append(primary)

    if slot_materials != materials:
        new_index = {m.name: i for i, m in enumerate(materials)}
        remap = [new_index[m.name] if m is not None else new_index[primary.name] for m in slot_materials]
        face_slots = [remap[i] if i < len(remap) else new_index[primary.name] for i in face_slots]
        mesh.materials.clear()
        for mat in materials:
            mesh.materials.append(mat)
        mesh.polygons.foreach_set('material_index', face_slots)
        mesh.update()
    return materials, primary


def _make_submesh_materials(context, obj, arm_obj, tex_dir, template_of=None, ensure_textures=True):
    """Turn every material of *obj* (already flagged as a custom submesh) into
    one of its surfaces: the primary into `<CustomSubmeshId>_ds0`, the others
    into Add-material surfaces, with the default template and specular
    strength. *template_of*(material), when given, picks each converted
    material's template instead (the primary's becomes the submesh's
    TemplateSource). Materials that already are its surfaces are kept; shared
    or linked ones are copied first. With *ensure_textures*, each surface
    gets a PNG in *tex_dir* (_ensure_surface_texture). Returns notes."""
    custom_submesh_id = str(obj['CustomSubmeshId'])
    added_template = ModelCheckCore.default_template(_model_is_stadium(arm_obj))
    materials, primary = _compact_material_slots(obj)
    if template_of is not None and not _is_primary_surface_of(primary, custom_submesh_id):
        obj['TemplateSource'] = template_of(primary)
    template_source = str(obj['TemplateSource'])
    mesh = obj.data
    surface_ids = None      # SurfaceIds in the file, scanned once, kept up to date below
    for index, mat in enumerate(materials):
        is_primary = mat == primary
        is_own = (_is_primary_surface_of(mat, custom_submesh_id) if is_primary
                  else _is_added_surface_of(mat, custom_submesh_id))
        if is_own:
            continue
        # Read the template before the props go: it may be this very material.
        if is_primary:
            shader_mode = _template_shader_mode(context, arm_obj, template_source)
            specular_strength = _template_specular_strength(context, arm_obj, template_source)
        else:
            if surface_ids is None:
                surface_ids = _surface_ids_in_file()
            plan = _new_surface_plan(
                context, arm_obj, obj, template_of(mat) if template_of is not None else added_template,
                existing_surface_ids=surface_ids)
        old_surface_id = mat.get('SurfaceId')
        if mat.users > 1 or mat.library is not None:
            mat = mat.copy()    # the copy's props go below, the original keeps its id
            mesh.materials[index] = mat
        elif surface_ids is not None and old_surface_id in surface_ids:
            surface_ids.remove(old_surface_id)  # this material's own id goes below
        for prop in _SLUGGIES_MATERIAL_PROPS:
            if prop in mat:
                del mat[prop]
        if is_primary:
            _set_custom_primary_surface(mat, custom_submesh_id, shader_mode, specular_strength)
        else:
            _apply_new_surface_plan(mat, plan)
            surface_ids.append(plan['surface_id'])
    notes = []
    for mat in mesh.materials if ensure_textures else ():
        note = _ensure_surface_texture(context, mat, tex_dir)
        if note:
            notes.append(note)
    return notes


def _model_images(context):
    """The images the scene's models still use: those of the materials of
    every mesh on a Sluggies armature (set-aside `.backup` bodies are on none)."""
    images = {}
    for obj in context.scene.objects:
        if obj.type != 'MESH':
            continue
        if not any(arm is not None and 'SluggiesBoneMetadataVersion' in arm
                   for arm in [_owning_armature(obj.parent)] + [m.object for m in obj.modifiers
                                                                 if m.type == 'ARMATURE']):
            continue
        for slot in obj.material_slots:
            images.update(dict.fromkeys(TextureFolder.material_images(slot.material)))
    return list(images)
