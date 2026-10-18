"""Sluggies Tools sidebar panel (3D View > Sidebar > Sluggies Tools)."""

import os
import re

import bmesh
import bpy
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatVectorProperty, IntProperty, StringProperty,
)
from mathutils import Matrix, Vector

from . import CustomSubmeshExport
from . import FieldCodec
from . import AnimScale
from .AnmExport import SLUGGIES_OT_export_anm_group, _POSE_BONE_PATH, _action_fcurves, anm_groups
from . import HostBones
from . import ModelCheckCore
from . import RigidRebuildExport
from . import SluggiePath
from . import TemplateSources
from .ExportSluggies import (
    SLUGGIES_OT_export,
    _connected_image_texture_nodes,
    _custom_submesh_template_texture_index,
    _detect_uniform_vertex_bone_id,
    _resolve_export_texture_context,
)
from .ImportSluggies import (
    SLUGGIES_OT_import,
    SLUGGIES_OT_import_anm,
    _build_material_nodes,
    _create_material,
    _set_surface_material_metadata,
)


# Every user-added bone gets this length in scene units, independent of how
# long its parent is — the display length is cosmetic (the exporter only reads
# the head position), and a fixed unit length keeps added bones selectable no
# matter how small the parent they hang off is.
ADDED_BONE_LENGTH = 1.0


_BONE_NAME_RE = re.compile(r'^bone_(\d+)$')
_SURFACE_ID_RE = re.compile(r'^sm(\d+)_ds\d+$')
_CUSTOM_SUBMESH_ID_RE = re.compile(r'^custom(\d+)$')
_SUBMESH_NAME_RE = re.compile(r'^CustomSubmesh_(\d+)$')
_INVALID_SUBMESH_NAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_CUSTOM_SUBMESH_CUBE_HALF_EXTENT = 0.2
_CUSTOM_SUBMESH_TEXTURE_SIZE = 256

_HOST_BONE_STATUS_TAGS = {
    HostBones.STATUS_RECOMMENDED: "free",
    HostBones.STATUS_ALLOWED: "root",
    HostBones.STATUS_DRIVES_SKINNING: "drives skinning",
}


def _bone_id_from_name(name):
    """bone_<id> -> id, else None."""
    m = _BONE_NAME_RE.match(name or '')
    return int(m.group(1)) if m else None


def _find_target_armature(context):
    """The Sluggies armature identified by *context*'s active object, if any.

    Strictly follows the active object (or its parent, for a mesh child) --
    it never falls back to scanning the whole scene. A scene can hold several
    imported models at once (for example a high- and low-poly version of the
    same character), each with its own bones and its own free/claimed state,
    so guessing among them would silently operate on the wrong armature.
    """
    obj = context.active_object
    if obj is None:
        return None
    if obj.type == 'ARMATURE' and 'SluggiesBoneMetadataVersion' in obj.keys():
        return obj
    if obj.parent is not None and obj.parent.type == 'ARMATURE' \
            and 'SluggiesBoneMetadataVersion' in obj.parent.keys():
        return obj.parent
    return None


def _any_sluggies_armature_imported(context):
    """Whether any Sluggies armature exists anywhere in the view layer,
    regardless of what is currently active. Used only to word the poll/empty
    messages correctly (\"import a model\" vs. \"select an armature\")."""
    return any(
        o.type == 'ARMATURE' and 'SluggiesBoneMetadataVersion' in o.keys()
        for o in context.view_layer.objects
    )


def _no_target_armature_message(context):
    if not _any_sluggies_armature_imported(context):
        return "Import a Sluggies model first"
    return "Select an armature (or one of its meshes) as the active object"


def _bone_records(arm_obj):
    """Read the import-time bone snapshot off *arm_obj*'s bones."""
    records = []
    for b in arm_obj.data.bones:
        bone_id = _bone_id_from_name(b.name)
        if bone_id is None:
            continue
        parent_id = _bone_id_from_name(b.parent.name) if b.parent is not None else None
        records.append(HostBones.BoneRecord(
            bone_id=bone_id,
            parent_id=parent_id,
            geo_id_raw=int(b.get('SluggiesGeoIdRaw', HostBones.GEO_ID_FREE)),
            skinned=bool(b.get('SluggiesSkinned', False)),
            user_added=bool(b.get('SluggiesUserAdded', False)),
        ))
    return records


def _donor_mesh_objects(context, arm_obj):
    """Imported donor submesh objects (excludes custom submeshes)."""
    return [
        o for o in context.view_layer.objects
        if o.type == 'MESH' and o.parent is arm_obj and not o.get('SluggiesCustomSubmesh')
    ]


def _submesh_index_of(obj):
    """Donor submesh index, parsed from any 'sm<N>_ds<M>' SurfaceId material."""
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        m = _SURFACE_ID_RE.match(mat.get('SurfaceId', '') or '')
        if m:
            return int(m.group(1))
    return None


def _gather_scene_claims(context, arm_obj, bone_records, exclude=None):
    """The scene state layered on top of the import-time snapshot (step 2).
    *exclude* is a custom submesh whose bone doesn't count as claimed (the
    one being re-attached)."""
    donor_objects = _donor_mesh_objects(context, arm_obj)

    owner_bone_by_submesh = {
        rec.geo_id_raw: rec.bone_id
        for rec in bone_records
        if not rec.skinned and rec.geo_id_raw != HostBones.GEO_ID_FREE
    }
    bone_geo_raw = {rec.bone_id: rec.geo_id_raw for rec in bone_records}
    known_bone_ids = {rec.bone_id for rec in bone_records}

    object_by_submesh = {}
    for obj in donor_objects:
        sub_idx = _submesh_index_of(obj)
        if sub_idx is not None and sub_idx not in object_by_submesh:
            object_by_submesh[sub_idx] = obj

    target_bone_by_submesh = {}
    for sub_idx, from_bone_id in owner_bone_by_submesh.items():
        obj = object_by_submesh.get(sub_idx)
        if obj is not None:
            target_bone_by_submesh[sub_idx] = _detect_uniform_vertex_bone_id(obj)

    retargets, _issues = HostBones.compute_rigid_retargets(
        owner_bone_by_submesh, bone_geo_raw, target_bone_by_submesh, known_bone_ids)

    custom_submesh_bone_ids = set()
    for o in context.view_layer.objects:
        if o.type == 'MESH' and o.get('SluggiesCustomSubmesh') and o is not exclude:
            bone_id = _detect_uniform_vertex_bone_id(o)
            if bone_id is not None:
                custom_submesh_bone_ids.add(bone_id)

    return HostBones.SceneClaims(
        retargets=tuple(retargets),
        custom_submesh_bone_ids=frozenset(custom_submesh_bone_ids),
    )


def _bone_metadata_is_current(arm_obj):
    """False for armatures imported before the current bone metadata format
    (version 1 stored a wrong SluggiesSkinned flag), which must be re-imported."""
    return HostBones.bone_metadata_is_current(arm_obj.get('SluggiesBoneMetadataVersion'))


# .sluggie path -> ChunkNumber, for armatures imported before the importer
# stored SluggiesChunkNumber (read once per file, not on every redraw).
_CHUNK_NUMBER_CACHE = {}


def _model_chunk_number(arm_obj):
    """The armature's .sluggie ChunkNumber (model directory), or None."""
    chunk = arm_obj.get('SluggiesChunkNumber')
    if chunk is not None:
        return int(chunk)
    path = SluggiePath.resolve(arm_obj, relink=False)   # draw-time caller
    if not path:
        return None
    if path not in _CHUNK_NUMBER_CACHE:
        try:
            _CHUNK_NUMBER_CACHE[path] = FieldCodec.load_sluggie(path)['SluggiesModel'].get('ChunkNumber')
        except (OSError, ValueError, KeyError):
            _CHUNK_NUMBER_CACHE[path] = None
    return _CHUNK_NUMBER_CACHE[path]


def _model_is_stadium(arm_obj):
    """Whether the armature's model is a stadium (dirs 7-16, the patcher's
    STADIUM_CHUNKS): there a custom submesh must hang on a free vanilla root
    bone, and new bones are refused."""
    return TemplateSources.is_stadium_chunk(_model_chunk_number(arm_obj))


def _ordered_host_bone_choices(context, arm_obj, exclude=None):
    records = _bone_records(arm_obj)
    claims = _gather_scene_claims(context, arm_obj, records, exclude)
    choices = HostBones.classify_host_bones(
        records, claims, roots_only=_model_is_stadium(arm_obj))
    return HostBones.order_host_bone_choices(choices, records)


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
        "Edit via the 'Set Specular Strength' panel button.")


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
        model = FieldCodec.load_sluggie(sluggie_path).get('SluggiesModel', {})
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


def _default_host_bone_choice(context, arm_obj, ordered_choices):
    """The recommended bone nearest the 3D cursor (plan step 3), falling back
    to the first available choice when nothing is 'recommended'."""
    recommended = [c for c in ordered_choices if c.status == HostBones.STATUS_RECOMMENDED]
    pool = recommended or ordered_choices
    if not pool:
        return None
    cursor_world = context.scene.cursor.location

    def _distance(choice):
        bone = arm_obj.data.bones.get(f'bone_{choice.bone_id}')
        if bone is None:
            return float('inf')
        head_world = arm_obj.matrix_world @ bone.head_local
        return (head_world - cursor_world).length

    return min(pool, key=_distance)


def _host_bone_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    if arm_obj is None:
        return [('NONE', "No armature", _no_target_armature_message(context), 0)]
    if not _bone_metadata_is_current(arm_obj):
        return [('NONE', "Re-import needed", HostBones.RE_IMPORT_MESSAGE, 0)]
    ordered = _ordered_host_bone_choices(context, arm_obj)
    if not ordered:
        return [('NONE', "No free bones", "Every bone already owns a mesh or is claimed", 0)]
    return _bone_choice_items(ordered)


def _bone_choice_items(ordered):
    """Enum items for host bone choices: 'bone_<id> (tag)'."""
    items = []
    for idx, choice in enumerate(ordered):
        parent_label = f'bone_{choice.parent_id}' if choice.parent_id is not None else "none"
        tag = _HOST_BONE_STATUS_TAGS.get(choice.status, choice.status)
        items.append((
            f'bone_{choice.bone_id}',
            f'bone_{choice.bone_id} ({tag})',
            f"{choice.reason}; parent {parent_label}",
            idx,
        ))
    return items


def _template_source_description(choice):
    """Dialog tooltip. Built-ins carry their own description (what
    the shader mode does); donor sources just name their kind."""
    if choice.kind == 'builtin':
        template = TemplateSources.BUILTIN_TEMPLATES.get(choice.argument)
        if template is not None:
            return template.description
    return f"{choice.kind} template source"


def _template_source_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    if arm_obj is None:
        return [('NONE', "None", _no_target_armature_message(context), 0)]
    choices = TemplateSources.build_template_source_choices(
        _template_source_materials(context, arm_obj), stadium=_model_is_stadium(arm_obj))
    if not choices:
        return [('NONE', "None", "No usable surface template found", 0)]
    return [
        (c.template_source, c.template_source, _template_source_description(c), idx)
        for idx, c in enumerate(choices)
    ]


def _update_host_bone_preview(self, context):
    """Best-effort: Blender may not redraw the viewport while a props dialog
    is open, so this is a courtesy, not a guarantee."""
    arm_obj = _find_target_armature(context)
    bone_id = _bone_id_from_name(self.host_bone)
    if arm_obj is None or bone_id is None:
        return
    bone = arm_obj.data.bones.get(f'bone_{bone_id}')
    if bone is not None:
        arm_obj.data.bones.active = bone


def _next_custom_submesh_id(context):
    used = set()
    for o in context.view_layer.objects:
        cs_id = o.get('CustomSubmeshId')
        if cs_id:
            used.add(cs_id)
    n = 0
    while f'custom{n}' in used:
        n += 1
    return f'custom{n}'


def _default_submesh_name(context):
    """CustomSubmesh_<n>, the lowest n not already used by an object name in
    the scene. Purely a starting suggestion -- the user can type any name."""
    used = set()
    for o in context.view_layer.objects:
        m = _SUBMESH_NAME_RE.match(o.name)
        if m:
            used.add(int(m.group(1)))
    n = 0
    while n in used:
        n += 1
    return f'CustomSubmesh_{n}'


def _validate_submesh_name(name):
    """None if *name* is a usable object/material/PNG-file base name, else an
    error string. Object-name uniqueness is checked globally (bpy.data), not
    just the view layer, since a hidden or excluded object would otherwise
    collide silently."""
    name = name.strip()
    if not name:
        return "Submesh name cannot be empty"
    if _INVALID_SUBMESH_NAME_CHARS_RE.search(name):
        return 'Submesh name cannot contain any of: < > : " / \\ | ? *'
    if name in bpy.data.objects:
        return f"An object named {name!r} already exists"
    return None


def _mark_cube_uv_seams(bm):
    """Seam every edge except a spanning tree of face adjacency, so the
    closed cube can be cut open into a flat net. Without this, angle-based
    unwrap has nowhere to cut and fails to unwrap the cube at all."""
    bm.faces.ensure_lookup_table()
    visited = {bm.faces[0]}
    tree_edges = set()
    stack = [bm.faces[0]]
    while stack:
        face = stack.pop()
        for edge in face.edges:
            for other in edge.link_faces:
                if other is not face and other not in visited:
                    visited.add(other)
                    tree_edges.add(edge)
                    stack.append(other)
    for edge in bm.edges:
        edge.seam = edge not in tree_edges


def _unwrap_cube_angle_based(context, obj):
    """Angle-based unwrap along the seams _mark_cube_uv_seams set, so the
    cube ships with a real UV layout instead of the default single-square
    projection from bmesh.ops.create_cube."""
    if context.object is not None and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.01)
    bpy.ops.object.mode_set(mode='OBJECT')


def _create_custom_submesh_cube(context, arm_obj, bone_id, custom_submesh_id, submesh_name, template_source):
    """A 0.1-unit-half-extent cube at the host bone's origin, weighted 1.0 to
    bone_<bone_id> and parented to *arm_obj* like an imported rigid submesh
    (plan step 3), named *submesh_name* (plan step 3 extension: user-editable
    submesh name, defaulting to CustomSubmesh_<n>)."""
    obj_name = submesh_name
    mesh = bpy.data.meshes.new(obj_name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=_CUSTOM_SUBMESH_CUBE_HALF_EXTENT * 2)
    _mark_cube_uv_seams(bm)
    bm.to_mesh(mesh)
    bm.free()
    mesh.uv_layers.new(name='uv0')

    obj = bpy.data.objects.new(obj_name, mesh)
    context.collection.objects.link(obj)
    _unwrap_cube_angle_based(context, obj)

    obj['SluggiesCustomSubmesh'] = True
    obj['CustomSubmeshId'] = custom_submesh_id
    obj['TemplateSource'] = template_source

    vg = obj.vertex_groups.new(name=f'bone_{bone_id}')
    vg.add(list(range(len(mesh.vertices))), 1.0, 'REPLACE')

    mod = obj.modifiers.new(name="Armature", type='ARMATURE')
    mod.object = arm_obj

    # Parented like imported rigid meshes; the parent inverse cancels the
    # armature's own world transform (its 90 degree import rotation, and any
    # later move of the whole armature) so obj.location below lands in world
    # space right after parenting. Export uses world positions, so this
    # only affects viewport behaviour.
    obj.parent = arm_obj
    obj.matrix_parent_inverse = arm_obj.matrix_world.inverted()

    bone = arm_obj.data.bones.get(f'bone_{bone_id}')
    if bone is not None:
        obj.location = arm_obj.matrix_world @ bone.head_local

    return obj


def _select_new_object(context, obj):
    """Switch to Object Mode and make *obj* the sole selected/active object,
    so the user lands looking at the cube they just created."""
    if context.object is not None and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


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
        model = FieldCodec.load_sluggie(sluggie_path).get('SluggiesModel', {})
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
    return image, False, None


def _rigid_mesh_kind(obj):
    """('donor', submesh_index) for a rigid (CompCount 3) donor submesh,
    ('custom', None) for a custom submesh, or (None, None) for anything else."""
    if obj is None or obj.type != 'MESH':
        return None, None
    if obj.get('SluggiesCustomSubmesh'):
        return 'custom', None
    if obj.get('VertexBufferCompCount') == 3:
        return 'donor', _submesh_index_of(obj)
    return None, None


def _add_material_mesh_kind(obj):
    """Like _rigid_mesh_kind, but the skinned body (CompCount 6) counts as a
    donor submesh too: a new material on it is exported through the body
    rebuild."""
    kind, submesh_index = _rigid_mesh_kind(obj)
    if kind is None and obj is not None and obj.type == 'MESH' \
            and obj.get('VertexBufferCompCount') == 6 and not obj.get('SluggiesCustomSubmesh'):
        return 'donor', _submesh_index_of(obj)
    return kind, submesh_index


def _read_bone_hierarchy(sluggie_path):
    """The parsed .sluggie's BoneHierarchy list, or None if unreadable."""
    try:
        model = FieldCodec.load_sluggie(sluggie_path).get('SluggiesModel', {})
    except (OSError, ValueError):
        return None
    return model.get('BoneHierarchy')


def _object_world_bbox_center(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return sum(corners, Vector((0.0, 0.0, 0.0))) / 8


def _default_reassign_target(arm_obj, obj, ordered_choices):
    """The recommended bone nearest the object's own world bounding-box
    centre (plan: 'the object is what's being moved', not the 3D cursor)."""
    recommended = [c for c in ordered_choices if c.status == HostBones.STATUS_RECOMMENDED]
    pool = recommended or ordered_choices
    if not pool:
        return None
    center = _object_world_bbox_center(obj)

    def _distance(choice):
        bone = arm_obj.data.bones.get(f'bone_{choice.bone_id}')
        if bone is None:
            return float('inf')
        head_world = arm_obj.matrix_world @ bone.head_local
        return (head_world - center).length

    return min(pool, key=_distance)


def _reassign_choices(context, arm_obj, obj):
    kind, submesh_index = _rigid_mesh_kind(obj)
    records = _bone_records(arm_obj)
    claims = _gather_scene_claims(context, arm_obj, records)
    moving_bone_id = _detect_uniform_vertex_bone_id(obj)
    return HostBones.reassignment_choices(
        records, claims,
        moving_submesh_index=submesh_index if kind == 'donor' else None,
        moving_custom_bone_id=moving_bone_id if kind == 'custom' else None,
        roots_only=_model_is_stadium(arm_obj),
    )


def _rigid_mesh_attachment_label(context, arm_obj, obj):
    """'Attached to bone_<id>', with '(moved from bone_<id>)' when a donor
    rigid submesh's current bone differs from its donor owner, or a reason
    nothing can be shown."""
    kind, submesh_index = _rigid_mesh_kind(obj)
    if kind is None:
        return "Not a rigid or custom submesh"
    bone_id = _detect_uniform_vertex_bone_id(obj)
    if bone_id is None:
        return "No single bone_<id> vertex group with weight; assign one to reassign"
    if kind == 'donor':
        donor_bone_id = next(
            (r.bone_id for r in _bone_records(arm_obj) if r.geo_id_raw == submesh_index), None)
        if donor_bone_id is not None and donor_bone_id != bone_id:
            return f"Attached to bone_{bone_id} (moved from bone_{donor_bone_id})"
    return f"Attached to bone_{bone_id}"


def _reassign_target_bone_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    obj = context.active_object
    if arm_obj is None or obj is None:
        return [('NONE', "No armature", _no_target_armature_message(context), 0)]
    ordered = _reassign_choices(context, arm_obj, obj)
    if not ordered:
        return [('NONE', "No other free bones", "No other bone is free to move to", 0)]
    return _bone_choice_items(ordered)


_PLACEMENT_KEEP_WORLD = (
    'KEEP_WORLD', "Keep world position",
    "The mesh stays exactly where it is and follows the new bone from now on. "
    "Patched through Hammerspace, which export picks automatically.",
)
_PLACEMENT_KEEP_OFFSET = (
    'KEEP_OFFSET', "Keep offset to bone",
    "The mesh jumps to the same offset from the new bone as it had from the old one. "
    "Its bytes don't change, so this also works with the in-place patcher.",
)


def _placement_enum_items(self, context):
    # Both placements are open to a mesh with facial shape keys too: a kept
    # world position takes the rigid rebuild, which carries the poses
    # (RigidRebuild.FacialPoses) since 2026-10-10.
    return [_PLACEMENT_KEEP_WORLD, _PLACEMENT_KEEP_OFFSET]


def _update_reassign_target_preview(self, context):
    """Best-effort active-bone preview, matching _update_host_bone_preview."""
    arm_obj = _find_target_armature(context)
    bone_id = _bone_id_from_name(self.target_bone)
    if arm_obj is None or bone_id is None:
        return
    bone = arm_obj.data.bones.get(f'bone_{bone_id}')
    if bone is not None:
        arm_obj.data.bones.active = bone


class SLUGGIES_OT_reassign_bone(bpy.types.Operator):
    """Move a rigid or custom submesh to another bone (Keep world position
    is patched through Hammerspace)"""
    bl_idname = "sluggies.reassign_bone"
    bl_label = "Reassign to New Bone"
    bl_description = "Move the active rigid or custom submesh to another free bone"
    bl_options = {"UNDO"}

    target_bone: EnumProperty(
        name="Target bone",
        description="Bone the mesh will follow after reassignment",
        items=_reassign_target_bone_enum_items,
        update=_update_reassign_target_preview,
    )  # type: ignore[valid-type]
    placement: EnumProperty(
        name="Placement",
        description="How the mesh moves relative to its new bone",
        items=_placement_enum_items,
    )  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        obj = context.active_object
        arm_obj = _find_target_armature(context)
        if arm_obj is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set(_no_target_armature_message(context))
            return False
        if not _bone_metadata_is_current(arm_obj):
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set(HostBones.RE_IMPORT_MESSAGE)
            return False
        kind, _submesh_index = _rigid_mesh_kind(obj)
        if kind is None:
            if hasattr(cls, 'poll_message_set'):
                if obj is not None and obj.get('VertexBufferCompCount') == 6:
                    cls.poll_message_set(
                        "Skinned submeshes follow bone weights; edit vertex groups instead")
                else:
                    cls.poll_message_set("Select a rigid or custom submesh")
            return False
        return True

    def invoke(self, context, event):
        arm_obj = _find_target_armature(context)
        obj = context.active_object
        if arm_obj is None or obj is None:
            self.report({"ERROR"}, _no_target_armature_message(context))
            return {"CANCELLED"}
        _renumber_added_bones_reporting(self, arm_obj)

        ordered = _reassign_choices(context, arm_obj, obj)
        if not ordered:
            self.report({"ERROR"}, "No other bone is free to move to")
            return {"CANCELLED"}

        default_choice = _default_reassign_target(arm_obj, obj, ordered)
        if default_choice is not None:
            self.target_bone = f'bone_{default_choice.bone_id}'
        self.placement = 'KEEP_WORLD'
        _begin_bone_name_display(self, arm_obj)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        arm_obj = _find_target_armature(context)
        try:
            obj = context.active_object
            if arm_obj is None or obj is None:
                self.report({"ERROR"}, _no_target_armature_message(context))
                return {"CANCELLED"}

            kind, submesh_index = _rigid_mesh_kind(obj)
            if kind is None:
                self.report({"ERROR"}, "Select a rigid or custom submesh")
                return {"CANCELLED"}

            if self.target_bone == 'NONE':
                self.report({"ERROR"}, "No other bone is free to move to")
                return {"CANCELLED"}
            target_bone_id = _bone_id_from_name(self.target_bone)
            if target_bone_id is None or f'bone_{target_bone_id}' not in arm_obj.data.bones:
                self.report({"ERROR"}, f"Target bone {self.target_bone!r} no longer exists")
                return {"CANCELLED"}

            # The props dialog may have stayed open while the scene changed underneath it.
            ordered = _reassign_choices(context, arm_obj, obj)
            if not any(c.bone_id == target_bone_id for c in ordered):
                self.report(
                    {"ERROR"}, f"bone_{target_bone_id} is no longer free; pick another target bone")
                return {"CANCELLED"}

            current_bone_id = _detect_uniform_vertex_bone_id(obj)

            if self.placement == 'KEEP_OFFSET':
                if current_bone_id is None:
                    self.report(
                        {"ERROR"}, f"{obj.name} has no single bone_<id> vertex group to move from")
                    return {"CANCELLED"}
                sluggie_path = SluggiePath.resolve(arm_obj)
                bone_hierarchy = _read_bone_hierarchy(sluggie_path) if sluggie_path else None
                if not bone_hierarchy:
                    self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Keep offset")
                                if not sluggie_path else HostBones.RE_IMPORT_MESSAGE)
                    return {"CANCELLED"}
                bind_matrices = CustomSubmeshExport.bone_absolute_matrices(bone_hierarchy)
                b_old = bind_matrices.get(current_bone_id)
                b_new = bind_matrices.get(target_bone_id)
                if b_old is None or b_new is None:
                    self.report(
                        {"ERROR"}, "Bone bind matrix missing from BoneHierarchy; re-import this model")
                    return {"CANCELLED"}
                arm_world = [list(row) for row in arm_obj.matrix_world]
                obj_world = [list(row) for row in obj.matrix_world]
                new_world = CustomSubmeshExport.keep_offset_world_matrix(
                    obj_world, arm_world, b_old, b_new)
                obj.matrix_world = Matrix(new_world)

            for vg in list(obj.vertex_groups):
                if _bone_id_from_name(vg.name) is not None:
                    obj.vertex_groups.remove(vg)
            new_vg = obj.vertex_groups.new(name=f'bone_{target_bone_id}')
            new_vg.add(list(range(len(obj.data.vertices))), 1.0, 'REPLACE')

            mod = next((m for m in obj.modifiers if m.type == 'ARMATURE'), None)
            if mod is None:
                mod = obj.modifiers.new(name="Armature", type='ARMATURE')
            mod.object = arm_obj

            obj['SluggiesRigidPlacement'] = self.placement

            if current_bone_id is not None:
                self.report(
                    {"INFO"},
                    f"Moved {obj.name} from bone_{current_bone_id} to bone_{target_bone_id} "
                    f"({self.placement})")
            else:
                self.report({"INFO"}, f"Moved {obj.name} to bone_{target_bone_id} ({self.placement})")
            return {"FINISHED"}
        finally:
            _end_bone_name_display(self, arm_obj)

    def cancel(self, context):
        _end_bone_name_display(self, _find_target_armature(context))


def _new_surface_plan(context, arm_obj, obj, template_source):
    """Everything Add material writes onto a new surface of *obj* made from
    *template_source*, checked before any material is touched. Also used by
    Check Models to turn a plain Blender material into such a surface.
    Raises ValueError with the reason."""
    kind, submesh_index = _add_material_mesh_kind(obj)
    if kind is None:
        raise ValueError("Select a donor or custom submesh")
    owner = f'sm{submesh_index}' if kind == 'donor' else obj.get('CustomSubmeshId')
    if not owner:
        raise ValueError(f"{obj.name} has no CustomSubmeshId; re-create it with Add New Submesh")
    existing_surface_ids = [
        mat.get('SurfaceId', '') for mat in bpy.data.materials if mat.get('SurfaceId')
    ]
    surface_id = TemplateSources.next_new_surface_key(owner, existing_surface_ids)

    sluggie_path = SluggiePath.resolve(arm_obj)
    if not sluggie_path:
        raise ValueError(SluggiePath.missing_message(arm_obj, "Add material"))
    try:
        model = FieldCodec.load_sluggie(sluggie_path).get('SluggiesModel', {})
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


class SLUGGIES_OT_add_material(bpy.types.Operator):
    """Add a new surface (material + empty texture slot) to a rigid or
    custom submesh, from a template. Blender-side only for now: export support for
    SluggiesNewSurface materials is a follow-up."""
    bl_idname = "sluggies.add_material"
    bl_label = "Add Material"
    bl_description = "Create a new surface (material + empty texture slot) on the active rigid or custom submesh"
    bl_options = {"UNDO"}

    material_name: StringProperty(
        name="Material name",
        description="Name for the new material (must be unique)",
    )  # type: ignore[valid-type]
    template_source: EnumProperty(
        name="Template",
        description="Donor or built-in surface this new surface's shading is cloned from",
        items=_template_source_enum_items,
    )  # type: ignore[valid-type]
    assign_selected_faces: BoolProperty(
        name="Assign selected faces",
        description="Assign the mesh's currently selected faces to the new material slot",
        default=True,
    )  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode not in ('OBJECT', 'EDIT_MESH'):
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode or Edit Mode")
            return False
        obj = context.active_object
        arm_obj = _find_target_armature(context)
        if arm_obj is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set(_no_target_armature_message(context))
            return False
        if not _bone_metadata_is_current(arm_obj):
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set(HostBones.RE_IMPORT_MESSAGE)
            return False
        kind, _submesh_index = _add_material_mesh_kind(obj)
        if kind is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Select a donor or custom submesh")
            return False
        return True

    def invoke(self, context, event):
        arm_obj = _find_target_armature(context)
        obj = context.active_object
        if arm_obj is None or obj is None:
            self.report({"ERROR"}, _no_target_armature_message(context))
            return {"CANCELLED"}

        self.material_name = _default_new_material_name(obj)
        template_choices = TemplateSources.build_template_source_choices(
            _template_source_materials(context, arm_obj), stadium=_model_is_stadium(arm_obj))
        if template_choices:
            self.template_source = template_choices[0].template_source
        self.assign_selected_faces = True
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'material_name')
        layout.prop(self, 'template_source')
        if context.mode == 'EDIT_MESH':
            layout.prop(self, 'assign_selected_faces')
        arm_obj = _find_target_armature(context)
        warning = _low_poly_texture_warning(arm_obj) if arm_obj is not None else None
        if warning:
            layout.label(text=warning, icon='INFO')

    def execute(self, context):
        arm_obj = _find_target_armature(context)
        obj = context.active_object
        if arm_obj is None or obj is None:
            self.report({"ERROR"}, _no_target_armature_message(context))
            return {"CANCELLED"}

        kind, submesh_index = _add_material_mesh_kind(obj)
        if kind is None:
            self.report({"ERROR"}, "Select a donor or custom submesh")
            return {"CANCELLED"}

        if self.template_source == 'NONE':
            self.report({"ERROR"}, "No usable surface template found")
            return {"CANCELLED"}

        material_name = self.material_name.strip()
        name_error = _validate_material_name(material_name)
        if name_error:
            self.report({"ERROR"}, name_error)
            return {"CANCELLED"}

        try:
            plan = _new_surface_plan(context, arm_obj, obj, self.template_source)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        mat = _create_material(material_name, None, wrap_s=plan['wrap_s'])
        _apply_new_surface_plan(mat, plan)

        obj.data.materials.append(mat)
        new_slot_index = len(obj.data.materials) - 1
        obj.active_material_index = new_slot_index

        assigned_count = None
        if context.mode == 'EDIT_MESH' and self.assign_selected_faces:
            bm = bmesh.from_edit_mesh(obj.data)
            assigned_count = sum(1 for f in bm.faces if f.select)
            bpy.ops.object.material_slot_assign()

        if assigned_count == 0:
            self.report(
                {"INFO"}, f"Added {mat.name} ({self.template_source}) to {obj.name}; no faces were assigned")
        else:
            self.report(
                {"INFO"},
                f"Added {mat.name} ({self.template_source}) to {obj.name}; "
                "load an image into its Sluggies texture node before exporting")
        return {"FINISHED"}


def _draw_rigid_mesh_box(layout, context):
    """The 'Rigid mesh' box (plan: renamed 'Mesh' for a custom submesh),
    showing the active object's current attachment and the Add submesh /
    Reassign to new bone / Add material buttons."""
    arm_obj = _find_target_armature(context)
    obj = context.active_object
    kind, _submesh_index = _rigid_mesh_kind(obj) if obj is not None else (None, None)
    body = kind is None and obj is not None and _add_material_mesh_kind(obj)[0] == 'donor'

    plain_mesh = kind is None and not body and obj is not None and obj.type == 'MESH' \
        and not _is_donor_mesh(obj)

    box = _collapsible_box(
        layout, context, 'mesh',
        "Mesh" if kind == 'custom' else "Skinned mesh" if body else "Rigid mesh")
    if box is None:
        return
    if arm_obj is None and not (plain_mesh and _any_sluggies_armature_imported(context)):
        box.label(text=_no_target_armature_message(context), icon='INFO')
    elif kind is not None:
        box.label(text=_rigid_mesh_attachment_label(context, arm_obj, obj))
    if not body:
        box.operator(SLUGGIES_OT_add_submesh.bl_idname)
        if obj is not None and obj.type == 'MESH':
            box.operator(SLUGGIES_OT_make_submesh.bl_idname)
        box.operator(SLUGGIES_OT_reassign_bone.bl_idname)
    box.operator(SLUGGIES_OT_add_material.bl_idname)
    if kind != 'custom':
        box.operator(SLUGGIES_OT_set_specular_strength.bl_idname)


def _add_bone_parent_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    if arm_obj is None:
        return [('NONE', "No armature", _no_target_armature_message(context), 0)]
    # data.bones iterates depth-first through the hierarchy; list by numeric id.
    bones = sorted(
        (b for b in arm_obj.data.bones if _bone_id_from_name(b.name) is not None),
        key=lambda b: _bone_id_from_name(b.name))
    items = []
    for idx, b in enumerate(bones):
        tag = "new" if b.get('SluggiesUserAdded') else "donor"
        items.append((b.name, b.name, f"Parent the new bone to {b.name} ({tag})", idx))
    return items or [('NONE', "No bones", "This armature has no bones", 0)]


def _next_new_bone_creation_order(arm_obj):
    orders = [
        int(b.get('SluggiesCreationOrder', -1))
        for b in arm_obj.data.bones if b.get('SluggiesUserAdded')
    ]
    return max(orders) + 1 if orders else 0


def _renumber_added_bones(arm_obj):
    """Rename Add Bone bones to the `bone_<id>` the exporter will give them
    (HostBones.added_bone_renames). Renaming a Bone makes Blender rename the
    matching vertex groups on every mesh the armature deforms, so custom
    submeshes keep their host. Two passes, since a target name can still be
    held by another added bone. Returns the applied renames."""
    bones = arm_obj.data.bones
    donor_count = sum(1 for b in bones if not b.get('SluggiesUserAdded'))
    renames = HostBones.added_bone_renames(donor_count, [
        (b.name, int(b.get('SluggiesCreationOrder', 0)))
        for b in bones if b.get('SluggiesUserAdded')
    ])
    temporary = {}
    for index, old_name in enumerate(renames):
        bones[old_name].name = f'__sluggies_renumber_{index}'
        temporary[bones[f'__sluggies_renumber_{index}'].name] = renames[old_name]
    for temp_name, new_name in temporary.items():
        bones[temp_name].name = new_name
    return renames


def _renumber_added_bones_reporting(op, arm_obj):
    renames = _renumber_added_bones(arm_obj)
    if renames:
        op.report({"INFO"}, "Renumbered added bones: " + ", ".join(
            f"{old} -> {new}" for old, new in sorted(renames.items())))
    return renames


class SLUGGIES_OT_renumber_added_bones(bpy.types.Operator):
    """Rename added bones to the ids they export with, after an added bone
    was deleted. Add Bone, Add Submesh and Reassign do this automatically;
    export refuses while the names are out of step."""
    bl_idname = "sluggies.renumber_added_bones"
    bl_label = "Renumber Added Bones"
    bl_description = ("Rename added bones (and their vertex groups) to the bone ids "
                      "they export with, e.g. after deleting an added bone")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        return _find_target_armature(context) is not None

    def execute(self, context):
        arm_obj = _find_target_armature(context)
        if arm_obj is None:
            self.report({"ERROR"}, _no_target_armature_message(context))
            return {"CANCELLED"}
        if not _renumber_added_bones_reporting(self, arm_obj):
            self.report({"INFO"}, "Added bone names already match their ids")
        return {"FINISHED"}


def _unused_bone_name(arm_obj):
    """A `bone_<N>` name not already used in *arm_obj*. Purely cosmetic: the
    exporter reassigns real ids from SluggiesCreationOrder, not from this
    name."""
    n = len(arm_obj.data.bones)
    while f'bone_{n}' in arm_obj.data.bones:
        n += 1
    return f'bone_{n}'


def _create_added_bone(context, arm_obj, parent_bone_name, animation_track=False):
    """Create one inert leaf bone parented to *parent_bone_name*: no
    GeoId, self-mirrored role 3, InheritTransform true, DrawPriority 0, and
    no track unless *animation_track* (then a fresh id, see
    `_set_added_bone_track`).
    *parent_bone_name* None makes a root bone at the 3D cursor instead: Add
    Bone does that in stadiums, whose bones are all roots (a mesh on a new
    root bone draws, Dolphin 2026-10-18).
    The SRT type byte is not set here: it is a component-presence mask over
    the bone's own rotation/translation, so the exporter derives it from the
    values it writes (act_section.html#srt-type) rather than inheriting a
    parent's mask that may not fit."""
    prev_active = context.view_layer.objects.active
    prev_mode = context.object.mode if context.object is not None else 'OBJECT'
    context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        edit_bones = arm_obj.data.edit_bones
        parent_eb = edit_bones.get(parent_bone_name) if parent_bone_name else None
        new_name = _unused_bone_name(arm_obj)
        new_eb = edit_bones.new(new_name)
        new_eb.parent = parent_eb
        new_eb.use_connect = False
        # World-aligned, facing -Y regardless of the parent bone's own
        # direction: convert the world -Y/+Z axes into the armature object's
        # local space, since edit-bone coordinates live there.
        world_to_local = arm_obj.matrix_world.to_3x3().inverted()
        local_dir = world_to_local @ Vector((0.0, -1.0, 0.0))
        local_up = world_to_local @ Vector((0.0, 0.0, 1.0))
        # `local_dir` is a world unit vector expressed in local space, so its
        # local length already equals one world unit under the object's scale:
        # scaling it by ADDED_BONE_LENGTH gives that length in the viewport,
        # whatever the parent bone measures.
        tail_offset = (
            local_dir * ADDED_BONE_LENGTH
            if local_dir.length > 1e-9 else Vector((0.0, 0.0, ADDED_BONE_LENGTH))
        )
        # Originates at the parent's tail, not floating off to the side, so
        # chained bones visually continue the parent like a real skeleton. A
        # root bone starts at the 3D cursor.
        new_eb.head = (
            parent_eb.tail if parent_eb is not None
            else arm_obj.matrix_world.inverted() @ context.scene.cursor.location
        )
        new_eb.tail = new_eb.head + tail_offset
        new_eb.align_roll(local_up)
        new_name = new_eb.name
    finally:
        bpy.ops.object.mode_set(mode=prev_mode if prev_mode in ('OBJECT', 'EDIT') else 'OBJECT')

    new_bone = arm_obj.data.bones[new_name]
    new_bone['SluggiesUserAdded'] = True
    new_bone['SluggiesCreationOrder'] = _next_new_bone_creation_order(arm_obj)
    new_bone['SluggiesGeoIdRaw'] = HostBones.GEO_ID_FREE
    new_bone['SluggiesSkinned'] = False
    new_bone['SluggiesDrawPriority'] = 0
    new_bone['SluggiesInheritTransform'] = True
    new_bone['track_id'] = HostBones.NO_TRACK
    if animation_track:
        _set_added_bone_track(arm_obj, new_bone, True)

    context.view_layer.objects.active = prev_active
    return new_bone


def _set_added_bone_track(arm_obj, bone, enabled):
    """Give an added bone its own animation track id (HostBones.ADDED_TRACK_IDS)
    or take it away. With a track, an .anm export writes the bone's keyframes
    and the patcher puts the id into the model's bone-to-track table. Returns
    the bone's track id."""
    if not enabled:
        bone['track_id'] = HostBones.NO_TRACK
    elif int(bone.get('track_id', HostBones.NO_TRACK)) not in HostBones.ADDED_TRACK_IDS:
        used = [int(b.get('track_id', HostBones.NO_TRACK)) for b in arm_obj.data.bones if b != bone]
        bone['track_id'] = HostBones.next_added_track_id(used)
    return int(bone['track_id'])


class SLUGGIES_OT_toggle_bone_track(bpy.types.Operator):
    """Give the active added bone its own animation track, or remove it"""
    bl_idname = "sluggies.toggle_bone_track"
    bl_label = "Toggle Animation Track"
    bl_description = ("Give the active added bone its own animation track id, so .anm export writes its "
                      "keyframes (experimental: new track ids are untested in game), or remove it")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        arm_obj = _find_target_armature(context)
        bone = arm_obj.data.bones.active if arm_obj is not None else None
        if bone is None or not bone.get('SluggiesUserAdded'):
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Make an added bone the active bone")
            return False
        return True

    def execute(self, context):
        arm_obj = _find_target_armature(context)
        bone = arm_obj.data.bones.active
        has_track = int(bone.get('track_id', HostBones.NO_TRACK)) != HostBones.NO_TRACK
        try:
            track_id = _set_added_bone_track(arm_obj, bone, not has_track)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if has_track:
            self.report({"INFO"}, f"{bone.name}: no animation track")
        else:
            self.report({"INFO"}, f"{bone.name}: animation track 0x{track_id:X}")
        return {"FINISHED"}


def _begin_bone_name_display(op, arm_obj):
    """Turn on the viewport bone-name overlay for the dialog's duration,
    remembering whether it was already on so `_end_bone_name_display` can
    restore the prior state instead of always turning it back off."""
    op._orig_show_names = arm_obj.data.show_names
    if not op._orig_show_names:
        arm_obj.data.show_names = True


def _end_bone_name_display(op, arm_obj):
    if arm_obj is not None and getattr(op, '_orig_show_names', True) is False:
        arm_obj.data.show_names = False




class SLUGGIES_OT_add_bone(bpy.types.Operator):
    """Add a new inert leaf bone to the skeleton, for a custom submesh to
    attach to once the donor's own free bones are exhausted
    (act_section.html#appending-bones; export then uses Hammerspace)."""
    bl_idname = "sluggies.add_bone"
    bl_label = "Add Bone"
    bl_description = "Add a new leaf bone parented to the chosen bone (in a stadium: a root bone at the 3D cursor)"
    bl_options = {"UNDO"}

    parent_bone: EnumProperty(
        name="Parent bone",
        description="Bone the new bone is rigidly parented to",
        items=_add_bone_parent_enum_items,
    )  # type: ignore[valid-type]
    animation_track: BoolProperty(
        name="Animation track",
        description=("Give the bone its own animation track id, so .anm export writes its keyframes "
                     "(experimental: new track ids are untested in game)"),
        default=False,
    )  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        if not _any_sluggies_armature_imported(context):
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Import a Sluggies model first")
            return False
        return True

    def invoke(self, context, event):
        arm_obj = _find_target_armature(context)
        if arm_obj is None:
            self.report({"ERROR"}, _no_target_armature_message(context))
            return {"CANCELLED"}
        if not _bone_metadata_is_current(arm_obj):
            self.report({"ERROR"}, HostBones.RE_IMPORT_MESSAGE)
            return {"CANCELLED"}
        _renumber_added_bones_reporting(self, arm_obj)
        if _model_is_stadium(arm_obj):
            # Every stadium bone is a root: no parent to pick.
            return self.execute(context)
        active_bone = arm_obj.data.bones.active
        if active_bone is not None and _bone_id_from_name(active_bone.name) is not None:
            self.parent_bone = active_bone.name
        _begin_bone_name_display(self, arm_obj)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        arm_obj = _find_target_armature(context)
        try:
            if arm_obj is None:
                self.report({"ERROR"}, _no_target_armature_message(context))
                return {"CANCELLED"}
            if _model_is_stadium(arm_obj):
                try:
                    new_bone = _create_added_bone(context, arm_obj, None, self.animation_track)
                except ValueError as e:
                    self.report({"ERROR"}, str(e))
                    return {"CANCELLED"}
                self.report({"INFO"}, f"Added root bone {new_bone.name} at the 3D cursor")
                return {"FINISHED"}
            if self.parent_bone == 'NONE' or self.parent_bone not in arm_obj.data.bones:
                self.report({"ERROR"}, f"Parent bone {self.parent_bone!r} no longer exists")
                return {"CANCELLED"}

            try:
                new_bone = _create_added_bone(context, arm_obj, self.parent_bone, self.animation_track)
            except ValueError as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            track = int(new_bone.get('track_id', HostBones.NO_TRACK))
            suffix = f", animation track 0x{track:X}" if track != HostBones.NO_TRACK else ""
            self.report({"INFO"}, f"Added {new_bone.name}, parented to {self.parent_bone}{suffix}")
            return {"FINISHED"}
        finally:
            _end_bone_name_display(self, arm_obj)

    def cancel(self, context):
        _end_bone_name_display(self, _find_target_armature(context))


class SLUGGIES_OT_add_submesh(bpy.types.Operator):
    """Add a custom rigid submesh, attached to a free bone (export then uses Hammerspace)"""
    bl_idname = "sluggies.add_submesh"
    bl_label = "Add New Submesh"
    bl_description = "Create a cube rigidly attached to a free bone, for hammerspace patching"
    bl_options = {"UNDO"}

    submesh_name: StringProperty(
        name="New submesh name",
        description="Name for the new object, material and texture file "
                     "(the .png is saved as <name>.png in the model's tex/ folder)",
    )  # type: ignore[valid-type]
    host_bone: EnumProperty(
        name="Host bone",
        description="Bone the new submesh follows rigidly",
        items=_host_bone_enum_items,
        update=_update_host_bone_preview,
    )  # type: ignore[valid-type]
    template_source: EnumProperty(
        name="Material template source",
        description="Donor display-state list and attribute formats the new submesh borrows",
        items=_template_source_enum_items,
    )  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        # Only an armature or one of its child objects names the model.
        if _find_target_armature(context) is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set(_no_target_armature_message(context))
            return False
        return True

    def invoke(self, context, event):
        arm_obj = _find_target_armature(context)
        if arm_obj is None:
            self.report({"ERROR"}, _no_target_armature_message(context))
            return {"CANCELLED"}
        if not _bone_metadata_is_current(arm_obj):
            self.report({"ERROR"}, HostBones.RE_IMPORT_MESSAGE)
            return {"CANCELLED"}
        if not SluggiePath.resolve(arm_obj):
            self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Add New Submesh"))
            return {"CANCELLED"}
        _renumber_added_bones_reporting(self, arm_obj)

        ordered = _ordered_host_bone_choices(context, arm_obj)
        if not ordered:
            self.report({"ERROR"}, "Every bone already owns a mesh or is claimed")
            return {"CANCELLED"}

        self.submesh_name = _default_submesh_name(context)
        default_choice = _default_host_bone_choice(context, arm_obj, ordered)
        if default_choice is not None:
            self.host_bone = f'bone_{default_choice.bone_id}'
        template_choices = TemplateSources.build_template_source_choices(
            _template_source_materials(context, arm_obj), stadium=_model_is_stadium(arm_obj))
        if template_choices:
            self.template_source = template_choices[0].template_source
        _begin_bone_name_display(self, arm_obj)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        arm_obj = _find_target_armature(context)
        try:
            if arm_obj is None:
                self.report({"ERROR"}, _no_target_armature_message(context))
                return {"CANCELLED"}

            if self.host_bone == 'NONE':
                self.report({"ERROR"}, "No free host bone available on this model")
                return {"CANCELLED"}
            if self.template_source == 'NONE':
                self.report({"ERROR"}, "No usable surface template found on this model")
                return {"CANCELLED"}

            submesh_name = self.submesh_name.strip()
            name_error = _validate_submesh_name(submesh_name)
            if name_error:
                self.report({"ERROR"}, name_error)
                return {"CANCELLED"}

            bone_id = _bone_id_from_name(self.host_bone)
            if bone_id is None or f'bone_{bone_id}' not in arm_obj.data.bones:
                self.report({"ERROR"}, f"Host bone {self.host_bone!r} no longer exists")
                return {"CANCELLED"}

            # Export-time re-check territory aside, re-run the free-bone
            # classification once more here: the props dialog may have stayed
            # open while another retarget or custom submesh claimed this bone.
            ordered = _ordered_host_bone_choices(context, arm_obj)
            if not any(c.bone_id == bone_id for c in ordered):
                self.report({"ERROR"}, f"bone_{bone_id} is no longer free; pick another host bone")
                return {"CANCELLED"}

            sluggie_path = SluggiePath.resolve(arm_obj)
            if not sluggie_path:
                self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Add New Submesh"))
                return {"CANCELLED"}

            custom_submesh_id = _next_custom_submesh_id(context)
            image, texture_reused, error = _write_custom_submesh_texture(sluggie_path, submesh_name)
            if error:
                self.report({"ERROR"}, error)
                return {"CANCELLED"}

            obj = _create_custom_submesh_cube(
                context, arm_obj, bone_id, custom_submesh_id, submesh_name, self.template_source)
            _create_custom_submesh_material(
                obj, custom_submesh_id, image,
                _template_shader_mode(context, arm_obj, self.template_source),
                _template_specular_strength(context, arm_obj, self.template_source))
            _select_new_object(context, obj)

            message = f"Added {obj.name} on bone_{bone_id} ({self.template_source})"
            if texture_reused:
                message += f"; using existing tex/{submesh_name}.png"
            self.report({"INFO"}, message)
            return {"FINISHED"}
        finally:
            _end_bone_name_display(self, arm_obj)

    def cancel(self, context):
        _end_bone_name_display(self, _find_target_armature(context))


# Everything the add-on writes on a material. A material that becomes a
# surface of a custom submesh loses these first, so no imported state
# (another submesh's SurfaceId, TextureIndex, ShaderMode, ...) survives.
_SLUGGIES_MATERIAL_PROPS = (
    'SurfaceId', 'SluggiesNewSurface', 'SluggiesSurfaceOwner', 'TemplateSource',
    'TemplateTextureIndex', 'TextureIndex', 'DisplayStateId', 'ShaderMode',
    'DisplayStateParamBytes', 'SpecularStrength', 'WrapS', 'WrapT',
)

# The name Make Submesh gives a mesh's only UV map.
_MADE_SUBMESH_UV_NAME = 'UV0'

# Dynamic enum strings must stay referenced from Python, or Blender may show
# garbage in the dropdown.
_MAKE_SUBMESH_ENUM_ITEMS = {}


def _is_donor_mesh(obj):
    """An imported submesh of a model (it carries the VertexBuffer* properties)."""
    return not obj.get('SluggiesCustomSubmesh') \
        and any(prop in obj for prop in ModelCheckCore.REQUIRED_PROPS)


def _sluggies_armatures(context):
    return [o for o in context.view_layer.objects
            if o.type == 'ARMATURE' and 'SluggiesBoneMetadataVersion' in o.keys()]


def _default_make_submesh_armature(context, obj, armatures):
    """The armature *obj* already follows (parent, Armature modifier), else a
    selected one, else the one whose origin is nearest the mesh."""
    candidates = [obj.parent] + [m.object for m in obj.modifiers if m.type == 'ARMATURE']
    candidates += list(context.selected_objects)
    for candidate in candidates:
        if candidate is not None and candidate in armatures:
            return candidate
    if not armatures:
        return None
    center = _object_world_bbox_center(obj)
    return min(armatures, key=lambda a: (a.matrix_world.translation - center).length)


def _make_submesh_armature(op):
    arm_obj = bpy.data.objects.get(op.armature) if op.armature not in ('', 'NONE') else None
    return arm_obj if arm_obj is not None and arm_obj.type == 'ARMATURE' else None


def _make_submesh_armature_enum_items(self, context):
    armatures = _sluggies_armatures(context)
    items = [(a.name, a.name, f"Attach the mesh to a bone of {a.name}", idx)
             for idx, a in enumerate(armatures)]
    items = items or [('NONE', "No armature", "Import a Sluggies model first", 0)]
    _MAKE_SUBMESH_ENUM_ITEMS['armature'] = items
    return items


def _make_submesh_bone_enum_items(self, context):
    arm_obj = _make_submesh_armature(self)
    if arm_obj is None:
        items = [('NONE', "No armature", "Pick an armature first", 0)]
    elif not _bone_metadata_is_current(arm_obj):
        items = [('NONE', "Re-import needed", HostBones.RE_IMPORT_MESSAGE, 0)]
    else:
        ordered = _ordered_host_bone_choices(context, arm_obj, exclude=context.active_object)
        items = _bone_choice_items(ordered) or [
            ('NONE', "No free bones", "Every bone already owns a mesh or is claimed", 0)]
    _MAKE_SUBMESH_ENUM_ITEMS['host_bone'] = items
    return items


def _update_make_submesh_armature(self, context):
    """Preselect the free bone nearest the mesh on the newly picked armature."""
    arm_obj = _make_submesh_armature(self)
    obj = context.active_object
    if arm_obj is None or obj is None or not _bone_metadata_is_current(arm_obj):
        return
    ordered = _ordered_host_bone_choices(context, arm_obj, exclude=obj)
    choice = _default_reassign_target(arm_obj, obj, ordered)
    if choice is not None:
        self.host_bone = f'bone_{choice.bone_id}'


def _update_make_submesh_bone_preview(self, context):
    """Best-effort active-bone preview, matching _update_host_bone_preview."""
    arm_obj = _make_submesh_armature(self)
    bone = arm_obj.data.bones.get(self.host_bone) if arm_obj is not None else None
    if bone is not None:
        arm_obj.data.bones.active = bone


def _model_tex_dir(sluggie_path):
    """(model, tex dir, error) for the .sluggie at *sluggie_path*; the tex dir
    is the one export reads (a Low model's is its High model's)."""
    try:
        model = FieldCodec.load_sluggie(sluggie_path).get('SluggiesModel', {})
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
        tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
        tex_node.image = image
        tex_node.location = (color_input.node.location.x - 320, color_input.node.location.y)
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


def _make_submesh_materials(context, obj, arm_obj, tex_dir):
    """Turn every material of *obj* (already flagged as a custom submesh) into
    one of its surfaces: the primary into `<CustomSubmeshId>_ds0`, the others
    into Add-material surfaces, with the default template and specular
    strength. Materials that already are its surfaces are kept; shared or
    linked ones are copied first. Returns notes."""
    custom_submesh_id = str(obj['CustomSubmeshId'])
    template_source = str(obj['TemplateSource'])
    added_template = ModelCheckCore.default_template(_model_is_stadium(arm_obj))
    materials, primary = _compact_material_slots(obj)
    mesh = obj.data
    for index, mat in enumerate(materials):
        is_primary = mat == primary
        is_own = (_is_primary_surface_of(mat, custom_submesh_id) if is_primary
                  else _is_added_surface_of(mat, custom_submesh_id))
        if is_own:
            continue
        if mat.users > 1 or mat.library is not None:
            mat = mat.copy()
            mesh.materials[index] = mat
        for prop in _SLUGGIES_MATERIAL_PROPS:
            if prop in mat:
                del mat[prop]
        if is_primary:
            _set_custom_primary_surface(
                mat, custom_submesh_id,
                _template_shader_mode(context, arm_obj, template_source),
                _template_specular_strength(context, arm_obj, template_source))
        else:
            _apply_new_surface_plan(mat, _new_surface_plan(context, arm_obj, obj, added_template))
    notes = []
    for mat in mesh.materials:
        note = _ensure_surface_texture(context, mat, tex_dir)
        if note:
            notes.append(note)
    return notes


def _rename_uv_map(obj, new_name):
    """Rename *obj*'s only UV map, and point its materials' UV Map and Normal
    Map nodes that named the old one at the new name."""
    layer = obj.data.uv_layers[0]
    old_name = layer.name
    if old_name == new_name:
        return
    layer.name = new_name
    for mat in obj.data.materials:
        tree = getattr(mat, 'node_tree', None) if mat is not None else None
        for node in tree.nodes if tree is not None else ():
            if node.type in ('UVMAP', 'NORMAL_MAP') and node.uv_map == old_name:
                node.uv_map = new_name


class SLUGGIES_OT_make_submesh(bpy.types.Operator):
    """Turn the active mesh into a custom rigid submesh on a free bone, where
    it is (export then uses Hammerspace)"""
    bl_idname = "sluggies.make_submesh"
    bl_label = "Make Active Mesh into a Submesh"
    bl_description = ("Attach the active mesh to one free bone as a custom submesh and set up its "
                      "materials and textures. The mesh keeps its world position")
    bl_options = {"UNDO"}

    armature: EnumProperty(
        name="Armature",
        description="Model the mesh becomes part of",
        items=_make_submesh_armature_enum_items,
        update=_update_make_submesh_armature,
    )  # type: ignore[valid-type]
    host_bone: EnumProperty(
        name="Host bone",
        description="Bone the mesh follows rigidly",
        items=_make_submesh_bone_enum_items,
        update=_update_make_submesh_bone_preview,
    )  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Make a mesh object active")
            return False
        if _is_donor_mesh(obj):
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("This mesh is an imported part of the model")
            return False
        if not _any_sluggies_armature_imported(context):
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Import a Sluggies model first")
            return False
        return True

    def invoke(self, context, event):
        obj = context.active_object
        armatures = _sluggies_armatures(context)
        arm_obj = _default_make_submesh_armature(context, obj, armatures)
        if arm_obj is None:
            self.report({"ERROR"}, "Import a Sluggies model first")
            return {"CANCELLED"}
        # Every armature the dialog offers, so its bone list shows the
        # names the bones export with.
        for armature in armatures:
            if _bone_metadata_is_current(armature):
                _renumber_added_bones_reporting(self, armature)
        self.armature = arm_obj.name
        _update_make_submesh_armature(self, context)
        self._display_armature = arm_obj.name
        _begin_bone_name_display(self, arm_obj)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'armature')
        layout.prop(self, 'host_bone')
        col = layout.column(align=True)
        col.label(text="All vertex groups are replaced by the host bone.", icon='INFO')
        col.label(text="Materials get the default template; ones without")
        col.label(text="a texture get a PNG in their colour in tex/.")

    def execute(self, context):
        try:
            return self._make_submesh(context)
        finally:
            self._restore_bone_names()

    def cancel(self, context):
        self._restore_bone_names()

    def _restore_bone_names(self):
        _end_bone_name_display(self, bpy.data.objects.get(getattr(self, '_display_armature', '')))

    def _make_submesh(self, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH' or _is_donor_mesh(obj):
            self.report({"ERROR"}, "Make a mesh object active that is not an imported part of a model")
            return {"CANCELLED"}
        arm_obj = _make_submesh_armature(self)
        if arm_obj is None:
            self.report({"ERROR"}, "Pick a Sluggies armature")
            return {"CANCELLED"}
        if not _bone_metadata_is_current(arm_obj):
            self.report({"ERROR"}, HostBones.RE_IMPORT_MESSAGE)
            return {"CANCELLED"}
        sluggie_path = SluggiePath.resolve(arm_obj)
        if not sluggie_path:
            self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Convert to submesh"))
            return {"CANCELLED"}
        bone_id = _bone_id_from_name(self.host_bone)
        if bone_id is None or self.host_bone not in arm_obj.data.bones:
            self.report({"ERROR"}, "No free host bone available on this model")
            return {"CANCELLED"}
        # The props dialog may have stayed open while the scene changed underneath it.
        if not any(c.bone_id == bone_id for c in _ordered_host_bone_choices(context, arm_obj, exclude=obj)):
            self.report({"ERROR"}, f"bone_{bone_id} is no longer free; pick another host bone")
            return {"CANCELLED"}

        # A mesh that is already a custom submesh of this armature keeps its
        # template; anything else gets the default one.
        was_custom = bool(obj.get('SluggiesCustomSubmesh'))
        template_source = str(obj.get('TemplateSource') or '') \
            if was_custom and obj.parent is arm_obj else ''
        template_source = template_source or ModelCheckCore.default_template(_model_is_stadium(arm_obj))
        model, tex_dir, error = _model_tex_dir(sluggie_path)
        if not error:
            try:
                _custom_submesh_template_texture_index(model, template_source)
            except ValueError as exc:
                error = str(exc)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        notes = []
        if obj.data.users > 1:
            obj.data = obj.data.copy()
            notes.append("its mesh data was shared, so it got its own copy")

        # A copy of a custom submesh (Shift+D) shares the original's id: give
        # it a new one.
        custom_submesh_id = str(obj.get('CustomSubmeshId') or '') if was_custom else ''
        if not custom_submesh_id or any(
                o is not obj and o.get('CustomSubmeshId') == custom_submesh_id
                for o in context.view_layer.objects):
            custom_submesh_id = _next_custom_submesh_id(context)
        obj['SluggiesCustomSubmesh'] = True
        obj['CustomSubmeshId'] = custom_submesh_id
        obj['TemplateSource'] = template_source

        obj.vertex_groups.clear()
        group = obj.vertex_groups.new(name=f'bone_{bone_id}')
        group.add(list(range(len(obj.data.vertices))), 1.0, 'REPLACE')

        armature_mods = [m for m in obj.modifiers if m.type == 'ARMATURE']
        mod = armature_mods[0] if armature_mods else obj.modifiers.new(name="Armature", type='ARMATURE')
        for extra in armature_mods[1:]:
            obj.modifiers.remove(extra)
        mod.object = arm_obj
        mod.vertex_group = ''
        mod.use_vertex_groups = True
        mod.use_bone_envelopes = False

        # Parented like Add Submesh's cube, without moving it.
        world = obj.matrix_world.copy()
        obj.parent = arm_obj
        obj.parent_type = 'OBJECT'
        obj.matrix_parent_inverse = arm_obj.matrix_world.inverted()
        obj.matrix_world = world

        uv_layers = obj.data.uv_layers
        if not uv_layers:
            notes.append("no UV map: add one and unwrap it before exporting")
        try:
            notes += _make_submesh_materials(context, obj, arm_obj, tex_dir)
        except ValueError as exc:
            notes.append(f"materials not finished: {exc}")
        if len(uv_layers) == 1:
            _rename_uv_map(obj, _MADE_SUBMESH_UV_NAME)

        _select_new_object(context, obj)
        message = f"{obj.name} is now custom submesh {custom_submesh_id} on bone_{bone_id} ({template_source})"
        self.report({"INFO"}, "; ".join([message] + notes))
        return {"FINISHED"}


def _specular_materials(obj):
    """Ordered list of Type-7 (specular-capable) materials on *obj*'s slots."""
    if obj is None:
        return []
    return [
        mat for slot in obj.material_slots
        if (mat := slot.material) is not None and mat.get("DisplayStateId") == 7
    ]


class SluggiesSpecularStrengthItem(bpy.types.PropertyGroup):
    surface_label: StringProperty()  # type: ignore[valid-type]
    strength: IntProperty(name="Strength", min=0, max=255, soft_min=0, soft_max=255)  # type: ignore[valid-type]


class SLUGGIES_OT_set_specular_strength(bpy.types.Operator):
    """Edit the specular intensity (DisplayStateParamBytes index 0, struct offset +1) for every
    Type-7 display state on the active submesh's material slots: 0-255,
    linear, confirmed in-game (_docs/_docs_model_format/overview.html)."""
    bl_idname = "sluggies.set_specular_strength"
    bl_label = "Set Specular Strength"
    bl_description = "Edit specular intensity for the active submesh's specular (Type-7) surfaces"
    bl_options = {"UNDO"}

    items: CollectionProperty(type=SluggiesSpecularStrengthItem)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Select a submesh mesh object")
            return False
        if not _specular_materials(obj):
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Active submesh has no specular (Type-7) surfaces")
            return False
        return True

    def invoke(self, context, event):
        self.items.clear()
        for mat in _specular_materials(context.active_object):
            item = self.items.add()
            surface_id = mat.get("SurfaceId", mat.name)
            shader_mode = mat.get("ShaderMode", "")
            item.surface_label = f"{surface_id} ({shader_mode})" if shader_mode else surface_id
            item.strength = int(mat.get("SpecularStrength", 0))
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        for item in self.items:
            row = col.row(align=True)
            row.label(text=item.surface_label)
            row.prop(item, 'strength', text="")

    def execute(self, context):
        materials = _specular_materials(context.active_object)
        for mat, item in zip(materials, self.items):
            mat["SpecularStrength"] = item.strength
        return {"FINISHED"}


def _layer_root_bones(arm_obj):
    """{bone name: track id} of the bones the game scales the body from
    (AnimScale.LAYER_ROOT_TRACK_IDS), sorted by track id."""
    return AnimScale.layer_roots({b.name: b.get('track_id', HostBones.NO_TRACK)
                                  for b in arm_obj.data.bones})


class SLUGGIES_OT_scale_animations(bpy.types.Operator):
    """Scale the whole model in every imported animation bank"""
    bl_idname = "sluggies.scale_animations"
    bl_label = "Scale model in all animations"
    bl_description = (
        "Multiply the model's size in every imported animation bank by the X/Y/Z factor below "
        "(scene axes, Z up). Keys the scale of the bones the game scales the body from (pelvis, "
        "spine, hands, neck, head) in every action of every imported .anm group; export each group "
        "and replace the banks afterwards. The bat does not follow: scale its mesh separately")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if _find_target_armature(context) is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set(_no_target_armature_message(context))
            return False
        return True

    def execute(self, context):
        arm_obj = _find_target_armature(context)
        roots = _layer_root_bones(arm_obj)
        if not roots:
            ids = ', '.join(hex(t) for t in sorted(AnimScale.LAYER_ROOT_TRACK_IDS))
            self.report({"ERROR"}, f"{arm_obj.name} has none of the bones the game scales the body "
                                   f"from (animation tracks {ids})")
            return {"CANCELLED"}
        groups = anm_groups()
        if not groups:
            self.report({"ERROR"}, "Import the character's animation banks first "
                                   "(Import Sluggers .anm, files 6-14 of its anm folder)")
            return {"CANCELLED"}
        factors = tuple(float(f) for f in context.scene.sluggies_animation_scale)
        if min(factors) <= 0.0:
            self.report({"ERROR"}, "Scale factors must be positive")
            return {"CANCELLED"}
        if all(abs(f - 1.0) < 1e-9 for f in factors):
            self.report({"INFO"}, "Factor 1 on every axis: nothing to change")
            return {"CANCELLED"}

        # The factors along each bone's own axes (a scale channel is applied in
        # the bone's frame), and the game's inheritance rule for the preview:
        # the body below a layer root follows its position, not its scale.
        per_bone, misaligned = {}, []
        for name in roots:
            bone = arm_obj.data.bones[name]
            axes = (arm_obj.matrix_world @ bone.matrix_local).to_3x3().normalized()
            rows = [list(axes[j]) for j in range(3)]
            per_bone[name] = AnimScale.local_factors(rows, factors)
            if not AnimScale.is_uniform(factors) and AnimScale.alignment(rows) < AnimScale.MIN_ALIGNMENT:
                misaligned.append(name)
            bone.inherit_scale = 'NONE'

        anim = arm_obj.animation_data
        prev_action = anim.action if anim is not None else None
        prev_slot = getattr(anim, 'action_slot', None) if anim is not None else None

        actions = keys = created = 0
        try:
            for group_actions in groups.values():
                for action in group_actions:
                    keyed = set()
                    for fc in _action_fcurves(action):
                        match = _POSE_BONE_PATH.match(fc.data_path)
                        if not match or match.group(2) != 'scale' or not 0 <= fc.array_index < 3:
                            continue
                        name = match.group(1).replace('\\"', '"').replace('\\\\', '\\')
                        if name not in per_bone or not fc.keyframe_points:
                            continue
                        f = per_bone[name][fc.array_index]
                        for kp in fc.keyframe_points:
                            kp.co[1] *= f
                            kp.handle_left[1] *= f
                            kp.handle_right[1] *= f
                            keys += 1
                        fc.update()
                        keyed.add((name, fc.array_index))

                    # A scale channel without keys holds the rest scale (1.0):
                    # key the factor at the action's first and last frame.
                    missing = [(name, i) for name in per_bone for i in range(3)
                               if (name, i) not in keyed]
                    if missing:
                        frames = sorted(set(action.frame_range))
                        for name, i in missing:
                            fc = _ensure_action_fcurve(arm_obj, action, f'pose.bones["{name}"].scale', i, name)
                            for frame in frames:
                                fc.keyframe_points.insert(frame, per_bone[name][i], options={'FAST'})
                            fc.update()
                            created += 1
                    actions += 1
        finally:
            if arm_obj.animation_data is not None:
                arm_obj.animation_data.action = prev_action
                if prev_slot is not None:
                    arm_obj.animation_data.action_slot = prev_slot

        self.report({"INFO"}, f"Scaled {len(per_bone)} bone(s) ({', '.join(per_bone)}) in {actions} "
                              f"action(s) of {len(groups)} group(s): {keys} keyframe(s) multiplied, "
                              f"{created} added. Export every group and replace the banks; "
                              f"the bat needs its own mesh scale")
        if misaligned:
            self.report({"WARNING"}, f"Non-uniform factors are a guess on {', '.join(misaligned)}: "
                                     f"the bone's axes do not line up with the scene's")
        if len(groups) < 9:
            self.report({"WARNING"}, f"{len(groups)} of the 9 banks imported; the model is scaled "
                                     f"only in the banks you replace")
        return {"FINISHED"}

def _ensure_action_fcurve(arm_obj, action, data_path, index, group):
    """The F-curve of *action* for *data_path*[*index*], created if missing.
    Blender 4.4+ creates it only for the data-block the action is assigned to,
    so this assigns *action* to *arm_obj*; the caller restores the original."""
    if hasattr(action, 'fcurve_ensure_for_datablock'):
        if arm_obj.animation_data is None:
            arm_obj.animation_data_create()
        if arm_obj.animation_data.action != action:
            arm_obj.animation_data.action = action
        return action.fcurve_ensure_for_datablock(arm_obj, data_path, index=index, group_name=group)
    fc = action.fcurves.find(data_path, index=index)
    return fc if fc is not None else action.fcurves.new(data_path, index=index, action_group=group)


# Sidebar boxes that collapse, each with a `Scene.sluggies_show_<key>_box`
# toggle (expanded by default).
_COLLAPSIBLE_BOXES = ('import_export', 'textures', 'bones', 'animations', 'mesh')


def _collapsible_box(layout, context, key, title):
    """A box whose title row toggles `Scene.sluggies_show_<key>_box`; returns
    the box to draw into, or None while collapsed."""
    box = layout.box()
    prop = f'sluggies_show_{key}_box'
    expanded = getattr(context.scene, prop)
    box.prop(
        context.scene, prop,
        text=title, icon='TRIA_DOWN' if expanded else 'TRIA_RIGHT',
        icon_only=False, emboss=False,
    )
    return box if expanded else None


def _draw_free_host_bones(layout, context):
    """Collapsible, read-only 'Free host bones' list (plan step 4): the same
    `classify_host_bones` output the Add Submesh dialog uses, shown here so a
    user re-pointing an existing submesh's vertex group can see a valid new
    bone without a trial export. Collapsed by default (`Scene.sluggies_show_free_host_bones`)."""
    box = layout.box()
    expanded = context.scene.sluggies_show_free_host_bones
    box.prop(
        context.scene, 'sluggies_show_free_host_bones',
        text="Free host bones", icon='TRIA_DOWN' if expanded else 'TRIA_RIGHT',
        icon_only=False, emboss=False,
    )
    if not expanded:
        return

    arm_obj = _find_target_armature(context)
    if arm_obj is None:
        box.label(text=_no_target_armature_message(context), icon='INFO')
        return
    if not _bone_metadata_is_current(arm_obj):
        box.label(text=HostBones.RE_IMPORT_MESSAGE, icon='INFO')
        return

    ordered = _ordered_host_bone_choices(context, arm_obj)
    if not ordered:
        box.label(text="Every bone already owns a mesh or is claimed", icon='INFO')
        return

    col = box.column(align=True)
    for choice in ordered:
        tag = _HOST_BONE_STATUS_TAGS.get(choice.status, choice.status)
        col.label(text=f'bone_{choice.bone_id} ({tag})')


# The "Animations" box (Scale model in all animations, AnimScale) is hidden from the panel for now;
# its operator stays registered (F3 search). Ground alignment of a scaled model is still open.
_SHOW_ANIMATIONS_BOX = False


class SLUGGIES_PT_tools(bpy.types.Panel):
    """Sluggies Tools sidebar panel"""
    bl_label = "Sluggies Tools"
    bl_idname = "SLUGGIES_PT_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sluggies"

    def draw(self, context):
        layout = self.layout
        # The File > Import/Export operators, so they are one click away.
        box = _collapsible_box(layout, context, 'import_export', "Import/Export")
        if box is not None:
            col = box.column(align=True)
            col.operator(SLUGGIES_OT_import.bl_idname, text="Import .sluggie", icon='IMPORT')
            col.operator(SLUGGIES_OT_export.bl_idname, text="Export .sluggie", icon='EXPORT')
            col.separator()
            col.operator(SLUGGIES_OT_import_anm.bl_idname, text="Import Sluggers .anm", icon='IMPORT')
            col.operator(SLUGGIES_OT_export_anm_group.bl_idname, text="Export Sluggers .anm", icon='EXPORT')
            col.separator()
            SluggiePath.draw_locate_button(col, context)

        layout.separator()
        box = _collapsible_box(layout, context, 'textures', "Textures")
        if box is not None:
            # By id: TextureSlotsUI registers it.
            box.operator("sluggies.manage_texture_slots", text="Manage texture slots", icon='TEXTURE')

        layout.separator()
        box = _collapsible_box(layout, context, 'bones', "Bones")
        if box is not None:
            box.operator(SLUGGIES_OT_add_bone.bl_idname)
            box.operator(SLUGGIES_OT_renumber_added_bones.bl_idname)
            box.operator(SLUGGIES_OT_toggle_bone_track.bl_idname)
            _draw_free_host_bones(box, context)

        if _SHOW_ANIMATIONS_BOX:
            layout.separator()
            box = _collapsible_box(layout, context, 'animations', "Animations")
            if box is not None:
                box.operator(SLUGGIES_OT_scale_animations.bl_idname)
                box.prop(context.scene, 'sluggies_animation_scale', text="")

        layout.separator()
        _draw_rigid_mesh_box(layout, context)

        layout.separator()
        # By id: ModelCheckUI imports this module.
        layout.operator("sluggies.check_models", text="Check Models", icon='CHECKMARK')


def register():
    bpy.utils.register_class(SLUGGIES_OT_add_submesh)
    bpy.utils.register_class(SLUGGIES_OT_make_submesh)
    bpy.utils.register_class(SLUGGIES_OT_add_bone)
    bpy.utils.register_class(SLUGGIES_OT_renumber_added_bones)
    bpy.utils.register_class(SLUGGIES_OT_toggle_bone_track)
    bpy.utils.register_class(SLUGGIES_OT_reassign_bone)
    bpy.utils.register_class(SLUGGIES_OT_add_material)
    bpy.utils.register_class(SluggiesSpecularStrengthItem)
    bpy.utils.register_class(SLUGGIES_OT_set_specular_strength)
    bpy.utils.register_class(SLUGGIES_OT_scale_animations)
    bpy.utils.register_class(SLUGGIES_PT_tools)
    bpy.types.Scene.sluggies_show_free_host_bones = BoolProperty(
        name="Free host bones",
        description="Show the read-only list of bones a new custom submesh could attach to",
        default=False,
    )
    for key in _COLLAPSIBLE_BOXES:
        setattr(bpy.types.Scene, f'sluggies_show_{key}_box', BoolProperty(
            name="Expanded",
            description="Show or hide this group",
            default=True,
        ))
    bpy.types.Scene.sluggies_animation_scale = FloatVectorProperty(
        name="Animation scale",
        description="X/Y/Z factor for 'Scale model in all animations', along the scene's axes (Z up)",
        size=3,
        subtype='XYZ',
        default=(1.0, 1.0, 1.0),
        min=0.01,
        soft_max=4.0,
    )


def unregister():
    del bpy.types.Scene.sluggies_animation_scale
    del bpy.types.Scene.sluggies_show_free_host_bones
    for key in _COLLAPSIBLE_BOXES:
        delattr(bpy.types.Scene, f'sluggies_show_{key}_box')
    bpy.utils.unregister_class(SLUGGIES_PT_tools)
    bpy.utils.unregister_class(SLUGGIES_OT_scale_animations)
    bpy.utils.unregister_class(SLUGGIES_OT_set_specular_strength)
    bpy.utils.unregister_class(SluggiesSpecularStrengthItem)
    bpy.utils.unregister_class(SLUGGIES_OT_add_material)
    bpy.utils.unregister_class(SLUGGIES_OT_reassign_bone)
    bpy.utils.unregister_class(SLUGGIES_OT_toggle_bone_track)
    bpy.utils.unregister_class(SLUGGIES_OT_renumber_added_bones)
    bpy.utils.unregister_class(SLUGGIES_OT_add_bone)
    bpy.utils.unregister_class(SLUGGIES_OT_make_submesh)
    bpy.utils.unregister_class(SLUGGIES_OT_add_submesh)
