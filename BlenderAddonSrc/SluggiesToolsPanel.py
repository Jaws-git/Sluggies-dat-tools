"""Sluggies Tools sidebar panel (3D View > Sidebar > Sluggies Tools)."""

import os
import re
import textwrap

import bmesh
import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty,
)
from mathutils import Matrix, Vector

from . import AdaptCleanup
from . import AdaptLowpoly
from . import AdaptTransform
from . import AdaptWeights
from . import CustomSubmeshExport
from . import FieldCodec
from . import AnimScale
from . import BoneRoles
from .AnmExport import SLUGGIES_OT_export_anm_group, _POSE_BONE_PATH, _action_fcurves, anm_groups
from . import HostBones
from . import ModelCheckCore
from . import RigidRebuildExport
from . import SluggiePath
from . import StadiumCollisionExport
from . import TemplateSources
from . import TextureFolder
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
    _new_image_texture_node,
    _set_surface_material_metadata,
)


# Every user-added bone gets this length in scene units, independent of how
# long its parent is — the display length is cosmetic (the exporter only reads
# the head position), and a fixed unit length keeps added bones selectable no
# matter how small the parent they hang off is.
ADDED_BONE_LENGTH = 1.0


_BONE_NAME_RE = re.compile(r'^bone_(\d+)$')
_SURFACE_ID_RE = re.compile(r'^sm(\d+)_ds\d+$')
_SURFACE_OWNER_RE = re.compile(r'^sm(\d+)$')
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
    """Donor submesh index, parsed from any 'sm<N>_ds<M>' SurfaceId material,
    else from an Add-material surface owned by 'sm<N>' (a body made with
    Make Main Body has only those)."""
    owned = None
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        m = _SURFACE_ID_RE.match(mat.get('SurfaceId', '') or '')
        if m:
            return int(m.group(1))
        m = _SURFACE_OWNER_RE.match(str(mat.get('SluggiesSurfaceOwner') or '')) \
            if mat.get('SluggiesNewSurface') else None
        if m and owned is None:
            owned = int(m.group(1))
    return owned


# Object pointer -> (signature, bone id) for _uniform_bone_id: the panel and
# its dialogs ask on every redraw, and the answer reads every vertex. Cleared
# when a Mesh data-block changes (weights, vertex groups, Edit Mode; posing
# updates only the objects, measured 2026-10-23), after undo/redo, on load and
# by this module's own vertex group writes. The signature also catches a
# swapped mesh, a changed vertex count and renamed vertex groups.
_UNIFORM_BONE_CACHE = {}


def _uniform_bone_id(obj):
    """_detect_uniform_vertex_bone_id, cached (see _UNIFORM_BONE_CACHE)."""
    mesh = obj.data
    signature = (mesh.as_pointer(), len(mesh.vertices), tuple(g.name for g in obj.vertex_groups))
    cached = _UNIFORM_BONE_CACHE.get(obj.as_pointer())
    if cached is not None and cached[0] == signature:
        return cached[1]
    bone_id = _detect_uniform_vertex_bone_id(obj)
    _UNIFORM_BONE_CACHE[obj.as_pointer()] = (signature, bone_id)
    return bone_id


@persistent
def _forget_uniform_bones_on_mesh_update(_scene, depsgraph):
    if any(isinstance(update.id, bpy.types.Mesh) for update in depsgraph.updates):
        _UNIFORM_BONE_CACHE.clear()


@persistent
def _forget_uniform_bones(*_args):
    _UNIFORM_BONE_CACHE.clear()


_UNIFORM_BONE_HANDLERS = (
    ('depsgraph_update_post', _forget_uniform_bones_on_mesh_update),
    ('undo_post', _forget_uniform_bones),
    ('redo_post', _forget_uniform_bones),
    ('load_post', _forget_uniform_bones),
)


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
            target_bone_by_submesh[sub_idx] = _uniform_bone_id(obj)

    retargets, _issues = HostBones.compute_rigid_retargets(
        owner_bone_by_submesh, bone_geo_raw, target_bone_by_submesh, known_bone_ids)

    # Only this armature's custom submeshes claim its bones: a high/low pair
    # shares bone ids, and the other model's mesh on bone_N leaves ours free.
    custom_submesh_bone_ids = set()
    for o in context.view_layer.objects:
        if o.type == 'MESH' and o.parent is arm_obj and o.get('SluggiesCustomSubmesh') \
                and o is not exclude:
            bone_id = _uniform_bone_id(o)
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


# Dynamic enum items by name: (key, items). Blender calls an items callback
# on every redraw of a dialog showing the enum and on every read of the
# property, so a list is built once and reused while its key (armature,
# active object) matches. invoke() drops it so each dialog starts fresh, and
# execute()/cancel() drop it when the dialog closes; execute() still re-checks
# the scene itself. Holding the items here also keeps their strings alive,
# which Blender requires of dynamic enums.
_ENUM_ITEMS = {}


def _cached_enum_items(name, key, build):
    cached = _ENUM_ITEMS.get(name)
    if cached is None or cached[0] != key:
        cached = _ENUM_ITEMS[name] = (key, build())
    return cached[1]


def _set_enum_items(name, key, items):
    _ENUM_ITEMS[name] = (key, items)


def _drop_enum_items(*names):
    for name in names:
        _ENUM_ITEMS.pop(name, None)


def _pointer(obj):
    return obj.as_pointer() if obj is not None else None


def _host_bone_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    return _cached_enum_items(
        'add_submesh.host_bone', _pointer(arm_obj), lambda: _host_bone_items(context, arm_obj))


def _host_bone_items(context, arm_obj, ordered=None):
    """Add Submesh's host bone items; *ordered* when the caller has the choices."""
    if arm_obj is None:
        return [('NONE', "No armature", _no_target_armature_message(context), 0)]
    if not _bone_metadata_is_current(arm_obj):
        return [('NONE', "Re-import needed", HostBones.RE_IMPORT_MESSAGE, 0)]
    if ordered is None:
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
    return _cached_enum_items(
        'template_source', _pointer(arm_obj), lambda: _template_source_items(context, arm_obj))


def _template_source_items(context, arm_obj):
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
    _UNIFORM_BONE_CACHE.clear()

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
    moving_bone_id = _uniform_bone_id(obj)
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
    bone_id = _uniform_bone_id(obj)
    if bone_id is None:
        return "No single bone_<id> vertex group with weight; assign one to reassign"
    if kind == 'donor':
        donor_bone_id = next(
            (b_id for b in arm_obj.data.bones
             if (b_id := _bone_id_from_name(b.name)) is not None
             and int(b.get('SluggiesGeoIdRaw', HostBones.GEO_ID_FREE)) == submesh_index), None)
        if donor_bone_id is not None and donor_bone_id != bone_id:
            return f"Attached to bone_{bone_id} (moved from bone_{donor_bone_id})"
    return f"Attached to bone_{bone_id}"


def _reassign_target_bone_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    obj = context.active_object
    return _cached_enum_items(
        'reassign.target_bone', (_pointer(arm_obj), _pointer(obj)),
        lambda: _reassign_target_bone_items(context, arm_obj, obj))


def _reassign_target_bone_items(context, arm_obj, obj, ordered=None):
    """Reassign's target bone items; *ordered* when the caller has the choices."""
    if arm_obj is None or obj is None:
        return [('NONE', "No armature", _no_target_armature_message(context), 0)]
    if ordered is None:
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
        _set_enum_items('reassign.target_bone', (_pointer(arm_obj), _pointer(obj)),
                        _reassign_target_bone_items(context, arm_obj, obj, ordered))

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
            _UNIFORM_BONE_CACHE.clear()

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
            _drop_enum_items('reassign.target_bone')

    def cancel(self, context):
        _end_bone_name_display(self, _find_target_armature(context))
        _drop_enum_items('reassign.target_bone')


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
        _drop_enum_items('template_source')
        template_choices = TemplateSources.build_template_source_choices(
            _template_source_materials(context, arm_obj), stadium=_model_is_stadium(arm_obj))
        if template_choices:
            self.template_source = template_choices[0].template_source
        self.assign_selected_faces = True
        # Read once: it parses the .sluggie, and draw() runs on every hover.
        self._texture_warning = _low_poly_texture_warning(arm_obj)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'material_name')
        layout.prop(self, 'template_source')
        if context.mode == 'EDIT_MESH':
            layout.prop(self, 'assign_selected_faces')
        warning = getattr(self, '_texture_warning', None)
        if warning:
            layout.label(text=warning, icon='INFO')

    def cancel(self, context):
        _drop_enum_items('template_source')

    def execute(self, context):
        try:
            return self._add_material(context)
        finally:
            _drop_enum_items('template_source')

    def _add_material(self, context):
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


def _mesh_armature(obj):
    """The armature *obj* deforms with: its Armature modifier's, else its parent."""
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object is not None:
            return mod.object
    if obj.parent is not None and obj.parent.type == 'ARMATURE':
        return obj.parent
    return None


class SLUGGIES_OT_select_unweighted(bpy.types.Operator):
    """Select the active mesh's vertices that no bone of its armature weights"""
    bl_idname = "sluggies.select_unweighted"
    bl_label = "Select Unweighted Vertices"
    bl_description = ("Enter Edit Mode on the active mesh and select every vertex with no weight "
                      "above 0 in a vertex group that matches a bone of its armature "
                      "(Armature modifier, else parent)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Select a mesh")
            return False
        return True

    def execute(self, context):
        obj = context.active_object
        arm_obj = _mesh_armature(obj)
        bone_names = set(arm_obj.data.bones.keys()) if arm_obj is not None else set()
        bone_group_indices = {g.index for g in obj.vertex_groups if g.name in bone_names}

        if obj.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')
        context.tool_settings.mesh_select_mode = (True, False, False)
        bm = bmesh.from_edit_mesh(obj.data)
        deform = bm.verts.layers.deform.active
        for f in bm.faces:
            f.select_set(False)
        for e in bm.edges:
            e.select_set(False)
        found = 0
        for v in bm.verts:
            weighted = deform is not None and any(
                index in bone_group_indices and weight > 0.0 for index, weight in v[deform].items())
            if weighted:
                v.select_set(False)
                continue
            v.hide_set(False)
            v.select_set(True)
            found += 1
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

        if arm_obj is None:
            self.report({"WARNING"}, f"{obj.name} has no armature: all {found} vertices are unweighted")
        elif found:
            self.report({"WARNING"}, f"Selected {found} unweighted vertices on {obj.name} ({arm_obj.name})")
        else:
            self.report({"INFO"}, f"Every vertex of {obj.name} is weighted to a bone of {arm_obj.name}")
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
        if kind is not None:
            box.operator(SLUGGIES_OT_make_main_body.bl_idname)
    box.operator(SLUGGIES_OT_add_material.bl_idname)
    if obj is not None and obj.type == 'MESH':
        box.operator(SLUGGIES_OT_select_unweighted.bl_idname)


STADIUM_ROOT_PARENT = '__ROOT__'


def _add_bone_parent_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    return _cached_enum_items(
        'add_bone.parent_bone', _pointer(arm_obj), lambda: _add_bone_parent_items(context, arm_obj))


def _add_bone_parent_items(context, arm_obj):
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
    if _model_is_stadium(arm_obj):
        # Every vanilla stadium bone is a root; a new one may be a root or a child.
        items.insert(0, (STADIUM_ROOT_PARENT, "None (root bone)",
                         "A new root bone at the 3D cursor", len(items)))
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


def _create_added_bone(context, arm_obj, parent_bone_name, animation_track=False, at_cursor=False):
    """Create one inert leaf bone parented to *parent_bone_name*: no
    GeoId, self-mirrored role 3, InheritTransform true, DrawPriority 0, and
    no track unless *animation_track* (then a fresh id, see
    `_set_added_bone_track`).
    *parent_bone_name* None makes a root bone at the 3D cursor instead (stadiums
    only: a mesh on a new root bone draws, Dolphin 2026-10-18). *at_cursor*
    starts a child bone at the 3D cursor too (stadiums: the bone can sit right
    next to its mesh, which keeps full position precision).
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
            parent_eb.tail if parent_eb is not None and not at_cursor
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
    BoneRoles.bone_collection(arm_obj.data, BoneRoles.ADDED).assign(new_bone)

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
    bl_description = ("Add a new leaf bone parented to the chosen bone (in a stadium: a root or "
                      "child bone at the 3D cursor)")
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
        _drop_enum_items('add_bone.parent_bone')
        active_bone = arm_obj.data.bones.active
        if _model_is_stadium(arm_obj):
            # A root bone is the default; a parent is picked deliberately.
            self.parent_bone = STADIUM_ROOT_PARENT
        elif active_bone is not None and _bone_id_from_name(active_bone.name) is not None:
            self.parent_bone = active_bone.name
        _begin_bone_name_display(self, arm_obj)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        arm_obj = _find_target_armature(context)
        try:
            if arm_obj is None:
                self.report({"ERROR"}, _no_target_armature_message(context))
                return {"CANCELLED"}
            stadium = _model_is_stadium(arm_obj)
            if stadium and self.parent_bone == STADIUM_ROOT_PARENT:
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
                new_bone = _create_added_bone(context, arm_obj, self.parent_bone, self.animation_track,
                                              at_cursor=stadium)
            except ValueError as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            track = int(new_bone.get('track_id', HostBones.NO_TRACK))
            suffix = f", animation track 0x{track:X}" if track != HostBones.NO_TRACK else ""
            self.report({"INFO"}, f"Added {new_bone.name}, parented to {self.parent_bone}{suffix}")
            return {"FINISHED"}
        finally:
            _end_bone_name_display(self, arm_obj)
            _drop_enum_items('add_bone.parent_bone')

    def cancel(self, context):
        _end_bone_name_display(self, _find_target_armature(context))
        _drop_enum_items('add_bone.parent_bone')


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
        _set_enum_items('add_submesh.host_bone', _pointer(arm_obj),
                        _host_bone_items(context, arm_obj, ordered))
        _drop_enum_items('template_source')

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
            _drop_enum_items('add_submesh.host_bone', 'template_source')

    def cancel(self, context):
        _end_bone_name_display(self, _find_target_armature(context))
        _drop_enum_items('add_submesh.host_bone', 'template_source')


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


_MAKE_SUBMESH_ENUMS = ('make_submesh.armature', 'make_submesh.host_bone')


def _make_submesh_armature_enum_items(self, context):
    return _cached_enum_items('make_submesh.armature', None, lambda: _make_submesh_armature_items(context))


def _make_submesh_armature_items(context):
    armatures = _sluggies_armatures(context)
    items = [(a.name, a.name, f"Attach the mesh to a bone of {a.name}", idx)
             for idx, a in enumerate(armatures)]
    return items or [('NONE', "No armature", "Import a Sluggies model first", 0)]


def _make_submesh_bone_enum_items(self, context):
    arm_obj = _make_submesh_armature(self)
    obj = context.active_object
    return _cached_enum_items(
        'make_submesh.host_bone', (_pointer(arm_obj), _pointer(obj)),
        lambda: _make_submesh_bone_items(context, arm_obj, obj))


def _make_submesh_bone_items(context, arm_obj, obj, ordered=None):
    """Make Submesh's host bone items; *ordered* when the caller has the choices."""
    if arm_obj is None:
        return [('NONE', "No armature", "Pick an armature first", 0)]
    if not _bone_metadata_is_current(arm_obj):
        return [('NONE', "Re-import needed", HostBones.RE_IMPORT_MESSAGE, 0)]
    if ordered is None:
        ordered = _ordered_host_bone_choices(context, arm_obj, exclude=obj)
    return _bone_choice_items(ordered) or [
        ('NONE', "No free bones", "Every bone already owns a mesh or is claimed", 0)]


def _update_make_submesh_armature(self, context):
    """Preselect the free bone nearest the mesh on the newly picked armature."""
    arm_obj = _make_submesh_armature(self)
    obj = context.active_object
    if arm_obj is None or obj is None or not _bone_metadata_is_current(arm_obj):
        return
    ordered = _ordered_host_bone_choices(context, arm_obj, exclude=obj)
    _set_enum_items('make_submesh.host_bone', (_pointer(arm_obj), _pointer(obj)),
                    _make_submesh_bone_items(context, arm_obj, obj, ordered))
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


def _make_submesh_materials(context, obj, arm_obj, tex_dir, template_of=None):
    """Turn every material of *obj* (already flagged as a custom submesh) into
    one of its surfaces: the primary into `<CustomSubmeshId>_ds0`, the others
    into Add-material surfaces, with the default template and specular
    strength. *template_of*(material), when given, picks each converted
    material's template instead (the primary's becomes the submesh's
    TemplateSource). Materials that already are its surfaces are kept; shared
    or linked ones are copied first. Returns notes."""
    custom_submesh_id = str(obj['CustomSubmeshId'])
    added_template = ModelCheckCore.default_template(_model_is_stadium(arm_obj))
    materials, primary = _compact_material_slots(obj)
    if template_of is not None and not _is_primary_surface_of(primary, custom_submesh_id):
        obj['TemplateSource'] = template_of(primary)
    template_source = str(obj['TemplateSource'])
    mesh = obj.data
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
            plan = _new_surface_plan(
                context, arm_obj, obj, template_of(mat) if template_of is not None else added_template)
        if mat.users > 1 or mat.library is not None:
            mat = mat.copy()
            mesh.materials[index] = mat
        for prop in _SLUGGIES_MATERIAL_PROPS:
            if prop in mat:
                del mat[prop]
        if is_primary:
            _set_custom_primary_surface(mat, custom_submesh_id, shader_mode, specular_strength)
        else:
            _apply_new_surface_plan(mat, plan)
    notes = []
    for mat in mesh.materials:
        note = _ensure_surface_texture(context, mat, tex_dir)
        if note:
            notes.append(note)
    return notes


def _rename_uv_map(obj, new_name, layer=None):
    """Rename *obj*'s UV map *layer* (default: its first), and point its
    materials' UV Map and Normal Map nodes that named the old one at the new
    name."""
    layer = layer if layer is not None else obj.data.uv_layers[0]
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
        _drop_enum_items(*_MAKE_SUBMESH_ENUMS)
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
            self._end_dialog()

    def cancel(self, context):
        self._end_dialog()

    def _end_dialog(self):
        _end_bone_name_display(self, bpy.data.objects.get(getattr(self, '_display_armature', '')))
        _drop_enum_items(*_MAKE_SUBMESH_ENUMS)

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

        custom_submesh_id, notes = _convert_to_custom_submesh(
            context, obj, arm_obj, bone_id, template_source, tex_dir,
            str(obj.get('CustomSubmeshId') or '') if was_custom else '')
        _select_new_object(context, obj)
        message = f"{obj.name} is now custom submesh {custom_submesh_id} on bone_{bone_id} ({template_source})"
        self.report({"INFO"}, "; ".join([message] + notes))
        return {"FINISHED"}


def _convert_to_custom_submesh(context, obj, arm_obj, bone_id, template_source, tex_dir,
                               custom_submesh_id='', template_of=None, set_up_materials=True):
    """Make *obj* a custom rigid submesh of *arm_obj* on *bone_id*, where it
    is: one full-weight vertex group, an Armature modifier, the armature as
    parent and its materials as its surfaces (see _make_submesh_materials for
    *template_of*; skipped without *set_up_materials*, which the Adapt box
    leaves to Convert Materials). *custom_submesh_id* is kept unless empty or
    used by another object. Returns (CustomSubmeshId, notes)."""
    notes = []
    if obj.data.users > 1:
        obj.data = obj.data.copy()
        notes.append("its mesh data was shared, so it got its own copy")

    # A copy of a custom submesh (Shift+D) shares the original's id: give
    # it a new one.
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
    _UNIFORM_BONE_CACHE.clear()

    _ensure_armature_modifier(obj, arm_obj)

    _parent_keeping_world(obj, arm_obj)

    uv_layers = obj.data.uv_layers
    if not uv_layers:
        notes.append("no UV map: add one and unwrap it before exporting")
    if set_up_materials:
        try:
            notes += _make_submesh_materials(context, obj, arm_obj, tex_dir, template_of)
        except ValueError as exc:
            notes.append(f"materials not finished: {exc}")
    if len(uv_layers) == 1:
        _rename_uv_map(obj, _MADE_SUBMESH_UV_NAME)
    return custom_submesh_id, notes


def _ensure_armature_modifier(obj, arm_obj):
    """One Armature modifier on *obj*, following *arm_obj* by vertex groups."""
    armature_mods = [m for m in obj.modifiers if m.type == 'ARMATURE']
    mod = armature_mods[0] if armature_mods else obj.modifiers.new(name="Armature", type='ARMATURE')
    for extra in armature_mods[1:]:
        obj.modifiers.remove(extra)
    mod.object = arm_obj
    mod.vertex_group = ''
    mod.use_vertex_groups = True
    mod.use_bone_envelopes = False


def _parent_keeping_world(obj, arm_obj):
    """Parent *obj* to *arm_obj* like Add Submesh's cube, without moving it."""
    world = obj.matrix_world.copy()
    obj.parent = arm_obj
    obj.parent_type = 'OBJECT'
    obj.matrix_parent_inverse = arm_obj.matrix_world.inverted()
    obj.matrix_world = world


# The skinned body. The body rebuild handles submesh 0 only.
_BODY_SUBMESH = 0
_BODY_UV_NAME = 'uv0'
_MAIN_BODY_WARNING = "Each model can have only one skinned mesh."
_OLD_BODY_WARNING = "The current main body becomes a rigid submesh and keeps only one of its vertex groups."
# Join or Replace meshes keeps a replaced body whole instead.
_BACKUP_BODY_NOTE = "The current main body is set aside as '<name>.backup', off the armature and not exported."
# Object properties the importer writes on a donor submesh besides
# REQUIRED_PROPS (UV binding, legacy per-surface shader modes).
_DONOR_OBJECT_PROP_RE = re.compile(r'^(UV\d+_(TextureIndex|WrapS|WrapT)|DS_.+_ShaderMode)$')


def _body_submesh(model):
    """Submesh 0 of *model* when it is the skinned body, else None."""
    submeshes = model.get('Submeshes') or []
    sub = submeshes[_BODY_SUBMESH] if submeshes else None
    if sub is None or int((sub.get('VertexBuffer') or {}).get('VertexBufferCompCount', 0)) != 6:
        return None
    return sub


def _body_objects(context, arm_obj, body):
    """The imported objects tied to the skinned *body* submesh."""
    offset = str((body.get('VertexBuffer') or {}).get('VertexBufferOffset'))
    return [o for o in _donor_mesh_objects(context, arm_obj)
            if 'VertexBufferOffset' in o and str(o['VertexBufferOffset']) == offset]


def _dominant_bone_choice(arm_obj, obj, choices):
    """The host bone choice whose bone_<id> vertex group carries the most
    weight in *obj*, else the free bone nearest it."""
    bone_by_group = {group.index: _bone_id_from_name(group.name) for group in obj.vertex_groups}
    totals = {}
    for vertex in obj.data.vertices:
        for element in vertex.groups:
            bone_id = bone_by_group.get(element.group)
            if bone_id is not None:
                totals[bone_id] = totals.get(bone_id, 0.0) + element.weight
    weighted = [c for c in choices if totals.get(c.bone_id, 0.0) > 0]
    if weighted:
        return max(weighted, key=lambda c: totals[c.bone_id])
    return _default_reassign_target(arm_obj, obj, choices)


def _old_body_template_of(model, stadium):
    """template_of for _make_submesh_materials on a former body: each body
    surface keeps its look as `derived:<SurfaceId>` (Spec surfaces), an
    Add-material surface keeps its template, anything else gets the default."""
    default = ModelCheckCore.default_template(stadium)

    def template_of(mat):
        surface_id = str(mat.get('SurfaceId') or '')
        if mat.get('SluggiesNewSurface'):
            candidate = str(mat.get('TemplateSource') or '')
        elif _SURFACE_ID_RE.match(surface_id) and mat.get('ShaderMode') == 'Spec':
            candidate = f'derived:{surface_id}'
        else:
            candidate = ''
        if candidate:
            try:
                _custom_submesh_template_texture_index(model, candidate)
                return candidate
            except ValueError:
                pass
        return default

    return template_of


def _body_surface_plans(context, arm_obj, obj, model, owner=f'sm{_BODY_SUBMESH}', template_of=None):
    """(material, plan) for each material of the custom submesh *obj* as an
    Add-material surface of the body (or of donor submesh *owner*), keeping
    its template (or taking *template_of*(material)), specular strength and
    wrap modes. Raises ValueError before anything is changed."""
    existing = [mat.get('SurfaceId', '') for mat in bpy.data.materials if mat.get('SurfaceId')]
    default = ModelCheckCore.default_template(_model_is_stadium(arm_obj))
    plans = []
    for mat in dict.fromkeys(slot.material for slot in obj.material_slots if slot.material is not None):
        template_source = template_of(mat) if template_of is not None else str(
            (mat.get('TemplateSource') if mat.get('SluggiesNewSurface') else obj.get('TemplateSource'))
            or default)
        template_texture_index = _custom_submesh_template_texture_index(model, template_source)
        shader_mode = (_template_shader_mode(context, arm_obj, template_source) or mat.get('ShaderMode')
                       or ('Spec' if template_source.startswith('derived:') else ''))
        if 'SpecularStrength' in mat:
            specular_strength = max(0, min(255, int(mat['SpecularStrength'])))
        else:
            specular_strength = _template_specular_strength(context, arm_obj, template_source)
        if 'WrapS' in mat and 'WrapT' in mat:
            wrap_s, wrap_t = int(mat['WrapS']), int(mat['WrapT'])
        else:
            wrap_s, wrap_t = _template_wrap_modes(context, arm_obj, template_source)
        surface_id = TemplateSources.next_new_surface_key(owner, existing)
        existing.append(surface_id)
        plans.append((mat, {
            'kind': 'donor',
            'owner': owner,
            'surface_id': surface_id,
            'template_source': template_source,
            'template_texture_index': template_texture_index,
            'shader_mode': shader_mode,
            'specular_strength': specular_strength,
            'wrap_s': wrap_s,
            'wrap_t': wrap_t,
        }))
    if not plans:
        raise ValueError(f"{obj.name} has no material; give it one first")
    return plans


def _apply_body_surface_plans(mesh, plans):
    """Write _body_surface_plans onto *mesh*'s materials (shared or linked
    ones are copied first)."""
    for mat, plan in plans:
        target = mat
        if mat.users > 1 or mat.library is not None:
            target = mat.copy()
            for index, slot_material in enumerate(mesh.materials):
                if slot_material == mat:
                    mesh.materials[index] = target
        for prop in _SLUGGIES_MATERIAL_PROPS:
            if prop in target:
                del target[prop]
        _apply_new_surface_plan(target, plan)


def _use_body_uv_name(obj):
    """Name *obj*'s render UV map like the body's channel 0 (a UV map already
    called that is renamed out of the way). Returns notes."""
    mesh = obj.data
    uv_layer = mesh.uv_layers.active_render or mesh.uv_layers.active
    if uv_layer is None:
        return ["no UV map: add one and unwrap it before exporting"]
    if uv_layer.name != _BODY_UV_NAME:
        other = mesh.uv_layers.get(_BODY_UV_NAME)
        if other is not None:
            _rename_uv_map(obj, f'{_BODY_UV_NAME}_old', other)
        _rename_uv_map(obj, _BODY_UV_NAME, uv_layer)
    return []


def _plan_old_bodies(context, arm_obj, body, new_body, reserved=()):
    """[(object, bone id)]: the free bone each current body object (of the
    skinned *body* submesh) becomes a rigid submesh on when *new_body*
    replaces it. Bones in *reserved* are taken already. Raises ValueError."""
    # The new body's own bone counts as free: it stops being rigid.
    choices = [c for c in _ordered_host_bone_choices(context, arm_obj, exclude=new_body)
               if c.bone_id not in reserved]
    old_bodies = []
    for old in _body_objects(context, arm_obj, body):
        choice = _dominant_bone_choice(arm_obj, old, choices)
        if choice is None:
            raise ValueError(f"No free bone left for {old.name} to become a rigid submesh on")
        choices = [c for c in choices if c.bone_id != choice.bone_id]
        old_bodies.append((old, choice.bone_id))
    return old_bodies


def _demote_old_bodies(context, arm_obj, model, tex_dir, old_bodies):
    """Turn each _plan_old_bodies entry into a rigid custom submesh. Returns notes."""
    notes = []
    stadium = _model_is_stadium(arm_obj)
    template_of = _old_body_template_of(model, stadium)
    for old, bone_id in old_bodies:
        for prop in ModelCheckCore.REQUIRED_PROPS + tuple(
                key for key in old.keys() if _DONOR_OBJECT_PROP_RE.match(key)):
            if prop in old:
                del old[prop]
        custom_submesh_id, old_notes = _convert_to_custom_submesh(
            context, old, arm_obj, bone_id, ModelCheckCore.default_template(stadium), tex_dir,
            template_of=template_of)
        notes.append(f"{old.name} is now custom submesh {custom_submesh_id} on bone_{bone_id}")
        notes += old_notes
    return notes


def _set_aside_old_bodies(old_bodies):
    """Take each replaced body object out of the model, where it is: no
    parent, no Armature modifier, no submesh properties, named `<name>.backup`
    and marked ModelCheckCore.BACKUP_PROP. Returns the new names."""
    names = []
    for old in old_bodies:
        world = old.matrix_world.copy()
        old.parent = None
        old.matrix_world = world
        for mod in [m for m in old.modifiers if m.type == 'ARMATURE']:
            old.modifiers.remove(mod)
        for prop in ModelCheckCore.REQUIRED_PROPS + tuple(
                key for key in old.keys() if _DONOR_OBJECT_PROP_RE.match(key)):
            if prop in old:
                del old[prop]
        old[ModelCheckCore.BACKUP_PROP] = True
        old.name = f'{old.name}.backup'
        names.append(old.name)
    return names


def _make_body(obj, arm_obj, model, plans):
    """Turn the custom submesh *obj* into the skinned body: the submesh 0
    VertexBuffer* properties, its materials as Add-material surfaces of sm0
    (*plans*; None leaves them to Convert Materials), its render UV map named
    like the body's channel 0. Its vertex groups become the skin weights.
    Returns notes."""
    notes = []
    if obj.data.users > 1:
        obj.data = obj.data.copy()
        notes.append(f"{obj.name}'s mesh data was shared, so it got its own copy")
    for prop in ('SluggiesCustomSubmesh', 'CustomSubmeshId', 'TemplateSource', 'SluggiesRigidPlacement'):
        if prop in obj:
            del obj[prop]
    for prop, value in ModelCheckCore.mesh_props_for_submesh(model, _BODY_SUBMESH).items():
        obj[prop] = value

    if plans is not None:
        _apply_body_surface_plans(obj.data, plans)
    notes += _use_body_uv_name(obj)
    _ensure_armature_modifier(obj, arm_obj)
    _parent_keeping_world(obj, arm_obj)
    if not any(_bone_id_from_name(group.name) is not None for group in obj.vertex_groups):
        notes.append("no bone_<id> vertex group: weight it to the bones before exporting")
    return notes


class SLUGGIES_OT_make_main_body(bpy.types.Operator):
    """Make the active custom submesh the model's skinned body (submesh 0);
    the current body becomes a rigid custom submesh"""
    bl_idname = "sluggies.make_main_body"
    bl_label = "Make This Mesh the Main Body"
    bl_description = ("Make the active custom submesh the model's skinned body (submesh 0), "
                      "weighted by its vertex groups. The current body becomes a rigid submesh on one bone")
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        arm_obj = _find_target_armature(context)
        if arm_obj is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set(_no_target_armature_message(context))
            return False
        if not _bone_metadata_is_current(arm_obj):
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set(HostBones.RE_IMPORT_MESSAGE)
            return False
        kind, _submesh_index = _rigid_mesh_kind(context.active_object)
        if kind != 'custom':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set(
                    "Only a custom submesh can become the body" if kind == 'donor'
                    else "Select a custom submesh")
            return False
        return True

    def invoke(self, context, event):
        # Without a body in the scene nothing is demoted: no need to confirm.
        arm_obj = _find_target_armature(context)
        if arm_obj is None or not any(
                o.get('VertexBufferCompCount') == 6 for o in _donor_mesh_objects(context, arm_obj)):
            return self.execute(context)
        return context.window_manager.invoke_confirm(
            self, event, title=self.bl_label, message=_MAIN_BODY_WARNING + "\n" + _OLD_BODY_WARNING,
            confirm_text="OK", icon='WARNING')

    def execute(self, context):
        arm_obj = _find_target_armature(context)
        obj = context.active_object
        if arm_obj is None or _rigid_mesh_kind(obj)[0] != 'custom':
            self.report({"ERROR"}, "Select a custom submesh of a Sluggies model")
            return {"CANCELLED"}
        sluggie_path = SluggiePath.resolve(arm_obj)
        if not sluggie_path:
            self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Make main body"))
            return {"CANCELLED"}
        model, tex_dir, error = _model_tex_dir(sluggie_path)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        body = _body_submesh(model)
        if body is None:
            self.report({"ERROR"}, "This model's submesh 0 is not a skinned body")
            return {"CANCELLED"}
        _renumber_added_bones_reporting(self, arm_obj)

        # Everything that can fail is checked before the scene changes.
        try:
            plans = _body_surface_plans(context, arm_obj, obj, model)
            old_bodies = _plan_old_bodies(context, arm_obj, body, obj)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        notes = _make_body(obj, arm_obj, model, plans)
        notes += _demote_old_bodies(context, arm_obj, model, tex_dir, old_bodies)

        _select_new_object(context, obj)
        self.report({"INFO"}, "; ".join([f"{obj.name} is now the skinned body (submesh 0)"] + notes))
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


# The 'Adapt' box: guided steps for porting a model from elsewhere onto a
# Sluggers donor, in order. (step id, button text, tooltip). Steps without an
# entry in _ADAPT_STEP_OPERATORS only report that they are not implemented.
_ADAPT_TRANSFORM_DESCRIPTION = (
    "Turn, scale (evenly) and move the selected objects together so their meshes stand on the Sluggies "
    "armature they were assigned to: upright, facing its front, head at its model's height. A rough "
    "start for manual fitting")
_ADAPT_STEPS = (
    ('CLEANUP', "Clean Up",
     "On meshes not imported from a .sluggie (all of them, or the selected ones; a selected "
     "non-Sluggies armature includes its children): merge duplicate "
     "vertices, remove loose vertices and edges, empty vertex groups and unused material slots. "
     "Materials themselves stay as they are"),
    ('ARMATURE', "Assign to Armature",
     "Parent the meshes not imported from a .sluggie (all of them, or the selected ones; a selected "
     "non-Sluggies armature includes its children) to the "
     "Sluggies armature you pick, keeping where they are, and point their Armature modifiers at it"),
    # Hidden for now; SLUGGIES_OT_adapt_transform is kept unregistered. To bring
    # it back, uncomment this entry, its _ADAPT_STEP_OPERATORS entry and its
    # register/unregister lines.
    # ('TRANSFORM', "Scale and Position", _ADAPT_TRANSFORM_DESCRIPTION),
    ('MESHES', "Join or Replace meshes",
     "For the Sluggies armature selected (or above the selected object): join each added mesh into the "
     "main body, let one replace it, add it as a rigid submesh on a free bone, or ignore it"),
    ('WEIGHTS', "Map Weights or Autorig",
     "For the Sluggies armature selected (or above the selected object): map the main body's vertex "
     "groups from its old rig onto the Sluggers bones, or weight it automatically"),
    ('MATERIALS', "Convert Materials",
     "For the Sluggies armature selected (or above the selected object): pick the shader mode and "
     "specular strength of each new material of the main body and rigid submeshes, turn them into "
     "Sluggers surfaces with a PNG texture, and remove the nodes that do not feed a shader's colour. "
     "In a saved .blend, the textures the models use are then copied to <blend name>_Tex next to it"),
    ('LOWPOLY', "Generate Lowpoly",
     "For the High model selected (or above the selected object), with its Low (_L_) model imported too: "
     "copy its meshes to the Low model, each left out, unchanged or decimated. The main body replaces "
     "the Low model's body, a rigid mesh the Low model's mesh on its bone"),
    # Opens the regular Check Models dialog (its own tooltip shows).
    ('CHECK', "Check For Problems", "Check the models in the scene for problems"),
)


class SLUGGIES_OT_adapt_step(bpy.types.Operator):
    """One step of the 'Adapt' box (placeholder)"""
    bl_idname = "sluggies.adapt_step"
    bl_label = "Adapt Step"
    bl_options = {"REGISTER"}

    step: EnumProperty(
        name="Step",
        items=[(ident, label, desc) for ident, label, desc in _ADAPT_STEPS],
        options={'HIDDEN'},
    )  # type: ignore[valid-type]

    @classmethod
    def description(cls, context, properties):
        return next(desc for ident, _label, desc in _ADAPT_STEPS if ident == properties.step)

    def execute(self, context):
        label = next(label for ident, label, _desc in _ADAPT_STEPS if ident == self.step)
        self.report({"INFO"}, f"'{label}' is not implemented yet")
        return {"CANCELLED"}


class SLUGGIES_OT_adapt_cleanup(bpy.types.Operator):
    """Clean Up step of the 'Adapt' box (AdaptCleanup)"""
    bl_idname = "sluggies.adapt_cleanup"
    bl_label = "Clean Up"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'CLEANUP')
    bl_options = {"REGISTER", "UNDO"}

    merge_distance: FloatProperty(  # type: ignore[valid-type]
        name="Merge Distance",
        description="Vertices closer than this (in mesh units) are merged. Kept tiny so fine detail survives",
        default=AdaptCleanup.DEFAULT_MERGE_DISTANCE, min=0.0, soft_max=0.001, precision=6,
    )

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        return True

    def execute(self, context):
        selected = _with_external_rig_children(context.selected_objects)
        targets, skipped = AdaptCleanup.cleanup_targets(selected or context.view_layer.objects)
        if not targets:
            where = "selected" if selected else "in this scene"
            self.report({"WARNING"}, f"No meshes {where} to clean (.sluggie imports are left alone)")
            return {"CANCELLED"}
        counts = AdaptCleanup.run_cleanup(targets, self.merge_distance)
        lines = AdaptCleanup.summary_lines(counts)
        if selected and skipped:
            lines.append(f"Skipped {_meshes(len(skipped))} from a .sluggie")
        _report_adapt_summary(self, context, "Clean Up", lines)
        return {"FINISHED"}


def _with_external_rig_children(objects):
    """*objects*, plus every descendant of a selected armature that is not a
    Sluggies one: selecting an imported rig cleans the meshes under it."""
    found = dict.fromkeys(objects)
    for obj in objects:
        if obj.type == 'ARMATURE' and 'SluggiesBoneMetadataVersion' not in obj.keys():
            found.update(dict.fromkeys(obj.children_recursive))
    return list(found)


def _meshes(count):
    return f"{count} mesh{'es' if count != 1 else ''}"


def _report_adapt_summary(op, context, title, lines, warnings=()):
    """*lines* (and *warnings*, marked) in the status bar and a popup."""
    op.report({"WARNING"} if warnings else {"INFO"}, "; ".join(list(lines) + list(warnings)))

    def draw_summary(menu, _context):
        for line in lines:
            menu.layout.label(text=line)
        for line in warnings:
            menu.layout.label(text=line, icon='ERROR')
    if not bpy.app.background:  # a popup crashes a background (no UI) Blender
        context.window_manager.popup_menu(draw_summary, title=title, icon='INFO')


def _adapt_armature_enum_items(self, context):
    return _cached_enum_items('adapt_armature.armature', None, lambda: [
        (a.name, a.name, f"Parent the meshes to {a.name}", idx)
        for idx, a in enumerate(_sluggies_armatures(context))
    ] or [('NONE', "No armature", "Import a Sluggies model first", 0)])


def _followed_armatures(obj):
    """The armatures *obj* follows: its parent and its Armature modifiers' targets."""
    return [obj.parent] + [m.object for m in obj.modifiers if m.type == 'ARMATURE']


# Meshes Assign to Armature's dialog lists by name; more are only counted.
_ASSIGN_LIST_MAX = 25

# Assign to Armature's live alignment preview while its dialog is open:
# 'saved' {object: (matrix_basis, mesh, Armature modifier visibility)} from
# before the dialog, restored before every preview, on Cancel and before OK
# applies for real; 'request' the settings a timer previews next; 'lines' /
# 'error' what the last preview found. Empty while no dialog is open.
_ASSIGN_PREVIEW = {}


def _assign_settings(op):
    return dict(armature=op.armature, align=op.align, up_axis=op.up_axis, front_axis=op.front_axis,
                scale_fit=op.scale_fit, pose=op.pose)


def _update_assign_preview(self, context):
    """Operator property update: preview the new settings (from a timer:
    a dialog cannot change the scene itself)."""
    if 'saved' not in _ASSIGN_PREVIEW:
        return
    _ASSIGN_PREVIEW['request'] = _assign_settings(self)
    if not bpy.app.timers.is_registered(_run_assign_preview):
        bpy.app.timers.register(_run_assign_preview, first_interval=0.0)


def _run_assign_preview():
    request = _ASSIGN_PREVIEW.pop('request', None)
    if request is not None and 'saved' in _ASSIGN_PREVIEW:
        _apply_assign_preview(bpy.context, request)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    return None


def _start_assign_preview(targets):
    _ASSIGN_PREVIEW.clear()
    _ASSIGN_PREVIEW['saved'] = {
        obj: (obj.matrix_basis.copy(), obj.data,
              [(m, m.show_viewport) for m in obj.modifiers if m.type == 'ARMATURE'])
        for obj in targets
    }
    _ASSIGN_PREVIEW['backups'] = {}


def _restore_assign_state():
    """Put the dialog's meshes back as they were when it opened."""
    for obj, backup in _ASSIGN_PREVIEW.get('backups', {}).items():
        try:
            AdaptTransform.restore_mesh(obj, backup)
        except ReferenceError:
            pass
    _ASSIGN_PREVIEW['backups'] = {}
    for obj, (basis, mesh, modifiers) in _ASSIGN_PREVIEW.get('saved', {}).items():
        try:
            if obj.data != mesh:
                obj.data = mesh
            for mod, shown in modifiers:
                mod.show_viewport = shown
            obj.matrix_basis = basis
        except ReferenceError:
            pass


def _end_assign_preview():
    _restore_assign_state()
    _ASSIGN_PREVIEW.clear()


def _apply_assign_preview(context, settings):
    """Show *settings*' alignment on the dialog's meshes, as they will look
    once assigned (the old rig no longer deforming them)."""
    _restore_assign_state()
    _ASSIGN_PREVIEW['lines'], _ASSIGN_PREVIEW['error'] = [], None
    if not settings['align']:
        return
    arm_obj = bpy.data.objects.get(settings['armature']) if settings['armature'] != 'NONE' else None
    if arm_obj is None or arm_obj not in _sluggies_armatures(context):
        _ASSIGN_PREVIEW['error'] = "Pick a Sluggies armature"
        return
    targets = list(_ASSIGN_PREVIEW['saved'])
    try:
        _ASSIGN_PREVIEW['backups'] = _bake_assign_poses(context, targets, settings)
        matrix, lines = _assign_fit(context, arm_obj, targets, settings)
    except ValueError as exc:
        _restore_assign_state()
        _ASSIGN_PREVIEW['error'] = str(exc)
        return
    for obj in targets:
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE':
                mod.show_viewport = False
    worlds = {obj: matrix @ obj.matrix_world for obj in targets}
    # Parents first: moving a parent moves its children along.
    for obj in sorted(targets, key=_depth):
        obj.matrix_world = worlds[obj]
    _ASSIGN_PREVIEW['lines'] = lines


def _depth(obj):
    depth = 0
    while obj.parent is not None:
        obj, depth = obj.parent, depth + 1
    return depth


def _old_rigs(context, targets):
    """The non-Sluggies armatures *targets* follow, plus selected ones, by name."""
    armatures = set(_sluggies_armatures(context))
    old_rigs = {a for obj in targets for a in _followed_armatures(obj)
                if a is not None and a.type == 'ARMATURE' and a not in armatures}
    old_rigs.update(o for o in context.selected_objects if o.type == 'ARMATURE' and o not in armatures)
    return sorted(old_rigs, key=lambda a: a.name)


def _restore_meshes(backups):
    for obj, backup in backups.items():
        AdaptTransform.restore_mesh(obj, backup)


def _bake_assign_poses(context, targets, settings):
    """With 'Use pose', the old rig's current pose written into each target
    that follows a posed one. Returns {object: backup}."""
    if settings['pose'] != 'POSE':
        return {}
    posed = {rig for rig in _old_rigs(context, targets) if AdaptTransform.is_posed(rig)}
    backups = {}
    try:
        for obj in targets:
            if any(m.type == 'ARMATURE' and m.object in posed and m.show_viewport for m in obj.modifiers):
                if obj.data.users > 1:
                    obj.data = obj.data.copy()
                backup = AdaptTransform.bake_pose(context, obj)
                if backup is not None:
                    backups[obj] = backup
    except ValueError:
        _restore_meshes(backups)
        raise
    return backups


def _assign_fit(context, arm_obj, targets, settings):
    """AdaptTransform.fit for *targets*, measured as they will show once
    parented: without their Armature modifiers (a kept pose is baked in by
    then)."""
    modifiers = [m for obj in targets for m in obj.modifiers if m.type == 'ARMATURE' and m.show_viewport]
    try:
        for mod in modifiers:
            mod.show_viewport = False
        context.view_layer.update()
        return AdaptTransform.fit(
            arm_obj, targets, context.scene.objects, context.evaluated_depsgraph_get(),
            settings['up_axis'], settings['front_axis'], source_armatures=_old_rigs(context, targets),
            scale_fit=settings['scale_fit'], source_posed=settings['pose'] == 'POSE')
    finally:
        for mod in modifiers:
            mod.show_viewport = True
        context.view_layer.update()


class SLUGGIES_OT_adapt_armature(bpy.types.Operator):
    """Assign to Armature step of the 'Adapt' box"""
    bl_idname = "sluggies.adapt_armature"
    bl_label = "Assign to Armature"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'ARMATURE')
    bl_options = {"REGISTER", "UNDO"}

    armature: EnumProperty(
        name="Armature",
        description="Sluggies armature the meshes are parented to",
        items=_adapt_armature_enum_items,
        update=_update_assign_preview,
    )  # type: ignore[valid-type]
    align: BoolProperty(  # type: ignore[valid-type]
        name="Align and scale to target armature",
        description=("Before parenting, turn, scale (evenly) and move the meshes together so they stand on "
                     "the armature: upright, facing its front, head at its model's height. Up and front are "
                     "read from the old rig's names (head, feet, eyes, ...) where it has them. Previewed "
                     "while the dialog is open"),
        default=False,
        update=_update_assign_preview,
    )
    up_axis: EnumProperty(  # type: ignore[valid-type]
        name="Up",
        description="Direction the model's head points to now",
        items=AdaptTransform.AXIS_ITEMS,
        update=_update_assign_preview,
    )
    front_axis: EnumProperty(  # type: ignore[valid-type]
        name="Facing",
        description="Direction the model faces now",
        items=AdaptTransform.AXIS_ITEMS,
        update=_update_assign_preview,
    )
    scale_fit: EnumProperty(  # type: ignore[valid-type]
        name="Scale fit",
        description="What decides the model's size",
        items=AdaptTransform.SCALE_FIT_ITEMS,
        update=_update_assign_preview,
    )
    pose: EnumProperty(  # type: ignore[valid-type]
        name="Old rig",
        description="Which shape the meshes keep from the armature they come with",
        items=[
            ('POSE', "Use pose",
             "Keep the shape the old rig's current pose gives the meshes (for example arms moved from an "
             "A-pose into a T-pose), shape keys included"),
            ('REST', "Use rest pose", "The meshes go back to the old rig's rest pose"),
        ],
        default='POSE',
        update=_update_assign_preview,
    )

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

    def _targets(self, context, arm_obj):
        """(meshes to assign, skipped .sluggie meshes, skipped meshes of
        another Sluggies armature). With nothing selected, meshes already on
        another Sluggies armature stay there (High and Low in one scene)."""
        selected = _with_external_rig_children(context.selected_objects)
        targets, skipped = AdaptCleanup.cleanup_targets(selected or context.view_layer.objects)
        others = []
        if not selected:
            armatures = set(_sluggies_armatures(context))
            others = [o for o in targets
                      if any(a in armatures and a is not arm_obj for a in _followed_armatures(o))]
            targets = [o for o in targets if o not in others]
        return targets, skipped, others

    def invoke(self, context, event):
        _drop_enum_items('adapt_armature.armature')
        armatures = _sluggies_armatures(context)
        targets, _skipped, _others = self._targets(context, None)
        arm_obj = _default_make_submesh_armature(context, targets[0], armatures) if targets \
            else next((a for a in armatures if a in context.selected_objects), None)
        if arm_obj is not None:
            self.armature = arm_obj.name
        _start_assign_preview(targets)
        if self.align:
            _update_assign_preview(self, context)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'armature')
        arm_obj = bpy.data.objects.get(self.armature) if self.armature != 'NONE' else None
        targets, _skipped, others = self._targets(context, arm_obj)
        if any(AdaptTransform.is_posed(rig) for rig in _old_rigs(context, targets)):
            layout.row().prop(self, 'pose', expand=True)
        layout.prop(self, 'align')
        if self.align:
            row = layout.row()
            row.prop(self, 'up_axis')
            row.prop(self, 'front_axis')
            layout.prop(self, 'scale_fit')
            if _ASSIGN_PREVIEW.get('error'):
                line = layout.row()
                line.alert = True
                line.label(text=_ASSIGN_PREVIEW['error'], icon='ERROR')
            elif _ASSIGN_PREVIEW.get('lines'):
                col = layout.box().column(align=True)
                for text in _ASSIGN_PREVIEW['lines']:
                    col.label(text=text)
        box = layout.box()
        box.label(text=f"{_meshes(len(targets)).capitalize()} to parent:")
        col = box.column(align=True)
        for obj in targets[:_ASSIGN_LIST_MAX]:
            col.label(text=obj.name, icon='MESH_DATA')
        if len(targets) > _ASSIGN_LIST_MAX:
            col.label(text=f"... and {len(targets) - _ASSIGN_LIST_MAX} more")
        if not targets:
            col.label(text="None (.sluggie imports are left alone)")
        if others:
            box.label(text=f"Left out: {_meshes(len(others))} on another Sluggies armature", icon='INFO')

    def execute(self, context):
        # OK starts from the state before the dialog: the preview only showed
        # what this applies.
        _end_assign_preview()
        try:
            return self._assign(context)
        finally:
            _drop_enum_items('adapt_armature.armature')

    def cancel(self, context):
        _end_assign_preview()
        _drop_enum_items('adapt_armature.armature')

    def _assign(self, context):
        arm_obj = bpy.data.objects.get(self.armature) if self.armature != 'NONE' else None
        if arm_obj is None or arm_obj not in _sluggies_armatures(context):
            self.report({"ERROR"}, "Pick a Sluggies armature")
            return {"CANCELLED"}
        targets, skipped, others = self._targets(context, arm_obj)
        if not targets:
            where = "selected" if context.selected_objects else "in this scene"
            self.report({"WARNING"}, f"No meshes {where} to assign (.sluggie imports are left alone)")
            return {"CANCELLED"}

        # Anything that can fail runs before the scene changes for good; a
        # baked pose is taken back on failure.
        fit_lines = []
        matrix = Matrix.Identity(4)
        backups = {}
        settings = _assign_settings(self)
        try:
            backups = _bake_assign_poses(context, targets, settings)
            if self.align:
                matrix, fit_lines = _assign_fit(context, arm_obj, targets, settings)
        except ValueError as exc:
            _restore_meshes(backups)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        # World matrices first: a target may be the parent of another.
        worlds = {obj: matrix @ obj.matrix_world for obj in targets}
        retargeted = added = removed = 0
        for obj, world in worlds.items():
            obj.parent = arm_obj
            obj.parent_type = 'OBJECT'
            obj.matrix_parent_inverse = arm_obj.matrix_world.inverted()
            obj.matrix_world = world
            modifiers = [m for m in obj.modifiers if m.type == 'ARMATURE']
            if not modifiers:
                modifiers = [obj.modifiers.new(name="Armature", type='ARMATURE')]
                added += 1
            elif modifiers[0].object is not arm_obj:
                retargeted += 1
            modifiers[0].object = arm_obj
            # Several Armature modifiers on one armature would deform twice.
            for extra in modifiers[1:]:
                obj.modifiers.remove(extra)
                removed += 1

        lines = [f"Parented {_meshes(len(targets))} to {arm_obj.name}"]
        if backups:
            lines.append(f"Kept the old rig's pose on {_meshes(len(backups))}")
        lines += fit_lines
        if retargeted:
            lines.append(f"Armature modifier of {_meshes(retargeted)} now follows {arm_obj.name}")
        if added:
            lines.append(f"Added an Armature modifier to {_meshes(added)}")
        if removed:
            lines.append(f"Removed {removed} extra Armature modifier{'s' if removed != 1 else ''}")
        if context.selected_objects and skipped:
            lines.append(f"Skipped {_meshes(len(skipped))} from a .sluggie")
        if others:
            lines.append(f"Skipped {_meshes(len(others))} on another Sluggies armature (select them to move them)")
        self.report({"INFO"}, "; ".join(lines))  # status bar only, no summary popup
        return {"FINISHED"}


def _selected_external_meshes(context):
    return AdaptCleanup.cleanup_targets(context.selected_objects)[0]


class SLUGGIES_OT_adapt_transform(bpy.types.Operator):
    """Scale and Position step of the 'Adapt' box (AdaptTransform)"""
    bl_idname = "sluggies.adapt_transform"
    bl_label = "Scale and Position"
    bl_description = _ADAPT_TRANSFORM_DESCRIPTION
    bl_options = {"REGISTER", "UNDO"}

    up_axis: EnumProperty(  # type: ignore[valid-type]
        name="Up",
        description="Direction the model's head points to now",
        items=AdaptTransform.AXIS_ITEMS,
    )
    front_axis: EnumProperty(  # type: ignore[valid-type]
        name="Facing",
        description="Direction the model faces now",
        items=AdaptTransform.AXIS_ITEMS,
    )
    use_hands: BoolProperty(  # type: ignore[valid-type]
        name="Match Hands",
        description=("Let hand height and spread (from hand/finger vertex groups) adjust the scale a "
                     "little; the overall height still counts most"),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        if not _selected_external_meshes(context):
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Select the meshes to fit (not ones from a .sluggie)")
            return False
        return True

    def _armature(self, context, meshes):
        """The Sluggies armature the meshes were assigned to (most of them),
        else a selected one, else the scene's only one."""
        armatures = _sluggies_armatures(context)
        followed = [a for obj in meshes for a in dict.fromkeys(_followed_armatures(obj)) if a in armatures]
        if followed:
            return max(set(followed), key=followed.count)
        selected = [a for a in armatures if a in context.selected_objects]
        if len(selected) == 1:
            return selected[0]
        return armatures[0] if len(armatures) == 1 else None

    def execute(self, context):
        meshes = _selected_external_meshes(context)
        arm_obj = self._armature(context, meshes)
        if arm_obj is None:
            self.report({"ERROR"}, "Assign the meshes to a Sluggies armature first (step 2)")
            return {"CANCELLED"}
        try:
            matrix, lines = AdaptTransform.fit(
                arm_obj, meshes, context.scene.objects, context.evaluated_depsgraph_get(),
                self.up_axis, self.front_axis, self.use_hands)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        # Selected objects other than the Sluggies model's, without those
        # whose parent is moved already (they follow it).
        armatures = _sluggies_armatures(context)
        movable = {o for o in context.selected_objects
                   if o not in armatures and not (o.type == 'MESH' and AdaptCleanup.is_sluggies_mesh(o))}

        def moved_ancestor(obj):
            node = obj.parent
            while node is not None:
                if node in movable:
                    return True
                node = node.parent
            return False
        roots = [o for o in movable if not moved_ancestor(o)]
        worlds = {o: o.matrix_world.copy() for o in roots}
        for obj, world in worlds.items():
            obj.matrix_world = matrix @ world

        lines.insert(0, f"Fitted {len(roots)} object{'s' if len(roots) != 1 else ''} to {arm_obj.name}")
        lines.append("Wrong way round? Set Up/Facing in the Adjust Last Operation panel")
        _report_adapt_summary(self, context, "Scale and Position", lines)
        return {"FINISHED"}


# Join or Replace meshes: one row per mesh that can be added, kept on the
# WindowManager (sluggies_adapt_mesh_rows) while the dialog is open.
_ADAPT_MESH_ACTIONS = (
    ('JOIN', "Join into main body",
     "Merge the mesh into the skinned main body; its vertex groups become skin weights"),
    ('REPLACE', "Replaces main body",
     "The mesh becomes the skinned main body (submesh 0); " + _BACKUP_BODY_NOTE[0].lower() + _BACKUP_BODY_NOTE[1:]),
    ('RIGID', "Add as rigid submesh", "A custom submesh that follows one bone rigidly"),
    ('IGNORE', "Ignore", "Leave the mesh as it is"),
)
_ADAPT_MESH_BONE_ENUM = 'adapt_meshes.bone'


def _adapt_meshes_armature(context):
    return bpy.data.objects.get(context.window_manager.sluggies_adapt_mesh_armature)


def _update_adapt_mesh_row(self, context):
    """Bone names show while any row adds a rigid submesh (as in Reassign)."""
    wm = context.window_manager
    arm_obj = _adapt_meshes_armature(context)
    if arm_obj is not None:
        rigid = any(row.action == 'RIGID' for row in wm.sluggies_adapt_mesh_rows)
        arm_obj.data.show_names = rigid or wm.sluggies_adapt_mesh_names_shown


def _update_adapt_mesh_bone(self, context):
    """Best-effort active-bone preview, matching _update_host_bone_preview."""
    arm_obj = _adapt_meshes_armature(context)
    bone = arm_obj.data.bones.get(self.bone) if arm_obj is not None else None
    if bone is not None:
        arm_obj.data.bones.active = bone


def _adapt_mesh_bone_items(self, context):
    arm_obj = _adapt_meshes_armature(context)
    return _cached_enum_items(_ADAPT_MESH_BONE_ENUM, _pointer(arm_obj), lambda: _host_bone_items(context, arm_obj))


class SluggiesAdaptMeshRow(bpy.types.PropertyGroup):
    """One mesh of the Join or Replace meshes dialog (name: the object's)."""
    action: EnumProperty(  # type: ignore[valid-type]
        name="Operation",
        items=_ADAPT_MESH_ACTIONS,
        default='RIGID',
        update=_update_adapt_mesh_row,
    )
    bone: EnumProperty(  # type: ignore[valid-type]
        name="Bone",
        description="Free bone the rigid submesh follows",
        items=_adapt_mesh_bone_items,
        update=_update_adapt_mesh_bone,
    )


def _owning_armature(obj):
    """*obj* itself or its nearest parent that is an armature, or None."""
    while obj is not None and obj.type != 'ARMATURE':
        obj = obj.parent
    return obj


def _selected_sluggies_armature(context):
    """The Sluggies armature a selected object is or sits under (the active
    object first), or None."""
    active = context.active_object
    selected = list(context.selected_objects)
    for obj in ([active] if active in selected else []) + selected:
        arm_obj = _owning_armature(obj)
        if arm_obj is not None and 'SluggiesBoneMetadataVersion' in arm_obj:
            return arm_obj
    return None


def _adapt_mesh_inventory(context, arm_obj):
    """(Sluggies meshes, meshes that can be added) of *arm_obj*: meshes
    below it or deformed by it."""
    objects = [
        o for o in context.view_layer.objects
        if o.type == 'MESH' and (_owning_armature(o.parent) is arm_obj
                                 or any(m.type == 'ARMATURE' and m.object is arm_obj for m in o.modifiers))
    ]
    addable, sluggies = AdaptCleanup.cleanup_targets(objects)
    return sluggies, addable


def _adapt_mesh_status(obj):
    collision = getattr(obj, 'sluggies_collision', None)
    if obj.get('SluggiesCustomSubmesh'):
        bone_id = _uniform_bone_id(obj)
        return f"Rigid mesh (added, bone_{bone_id})" if bone_id is not None else "Rigid mesh (added)"
    if obj.get('VertexBufferCompCount') == 6:
        return "Main body (skinned)"
    if obj.get('VertexBufferCompCount') == 3:
        return "Rigid mesh"
    return "Collision" if collision is not None and collision.enabled else "Sluggies mesh"


def _adapt_mesh_faults(wm):
    """(names of the faulty rows, messages) for the dialog's current choices."""
    rows = list(wm.sluggies_adapt_mesh_rows)
    faulty, messages = set(), []
    replacing = [r for r in rows if r.action == 'REPLACE']
    if len(replacing) > 1:
        faulty.update(r.name for r in replacing)
        messages.append("Only one mesh can replace the main body")
    skinned = [r for r in rows if r.action in ('JOIN', 'REPLACE')]
    if skinned and not wm.sluggies_adapt_mesh_has_body:
        faulty.update(r.name for r in skinned)
        messages.append("This model has no skinned main body: add the meshes as rigid submeshes")
    elif not replacing and not wm.sluggies_adapt_mesh_has_body_object \
            and any(r.action == 'JOIN' for r in rows):
        faulty.update(r.name for r in rows if r.action == 'JOIN')
        messages.append("No main body object in the scene to join into")
    by_bone = {}
    for row in rows:
        if row.action == 'RIGID':
            by_bone.setdefault(row.bone, []).append(row)
    for bone, group in by_bone.items():
        if bone == 'NONE':
            faulty.update(r.name for r in group)
            messages.append("No free bone left for a rigid submesh")
        elif len(group) > 1:
            faulty.update(r.name for r in group)
            messages.append(f"{bone} is picked for more than one rigid submesh")
    return faulty, messages


def _join_into_body(context, body_obj, obj):
    """Merge *obj* into the skinned body object *body_obj*: its render UV map
    joins the body's channel 0, its vertex groups the skin weights. Its
    materials stay plain until Convert Materials. Returns notes."""
    notes = []
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    if not any(slot.material is not None for slot in obj.material_slots):
        # Joining adds an empty slot for a mesh without material: give it the
        # body's first one instead.
        first = next((m for m in body_obj.data.materials if m is not None), None)
        if first is not None:
            obj.data.materials.clear()
            obj.data.materials.append(first)
        notes.append(f"{obj.name} had no material: its faces take the body's first one")
    notes += [f"{obj.name}: {note}" for note in _use_body_uv_name(obj)]
    with context.temp_override(active_object=body_obj, object=body_obj, selected_objects=[body_obj, obj],
                               selected_editable_objects=[body_obj, obj]):
        bpy.ops.object.join()
    return notes


def _reopen_adapt_meshes():
    """Timer: show the Join or Replace dialog again with its rows as they were."""
    wm = bpy.context.window_manager
    if len(wm.windows):
        with bpy.context.temp_override(window=wm.windows[0]):
            bpy.ops.sluggies.adapt_meshes('INVOKE_DEFAULT', reopen=True)
    return None


class SLUGGIES_OT_adapt_meshes(bpy.types.Operator):
    """Join or Replace meshes step of the 'Adapt' box"""
    bl_idname = "sluggies.adapt_meshes"
    bl_label = "Join or Replace Meshes"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'MESHES')
    bl_options = {"UNDO"}

    reopen: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        if _selected_sluggies_armature(context) is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Select a Sluggies armature or one of its child objects")
            return False
        return True

    def invoke(self, context, event):
        wm = context.window_manager
        arm_obj = _adapt_meshes_armature(context) if self.reopen else _selected_sluggies_armature(context)
        if arm_obj is None:
            self.report({"WARNING"}, "Select a Sluggies armature or one of its child objects")
            return {"CANCELLED"}
        if not _bone_metadata_is_current(arm_obj):
            self.report({"ERROR"}, HostBones.RE_IMPORT_MESSAGE)
            return {"CANCELLED"}
        sluggie_path = SluggiePath.resolve(arm_obj)
        if not sluggie_path:
            self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Join or Replace meshes"))
            return {"CANCELLED"}
        model, _tex_dir, error = _model_tex_dir(sluggie_path)
        if model is None:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        _renumber_added_bones_reporting(self, arm_obj)
        ordered = _ordered_host_bone_choices(context, arm_obj)
        _set_enum_items(_ADAPT_MESH_BONE_ENUM, _pointer(arm_obj), _host_bone_items(context, arm_obj, ordered))
        if not self.reopen:
            body = _body_submesh(model)
            wm.sluggies_adapt_mesh_armature = arm_obj.name
            wm.sluggies_adapt_mesh_names_shown = arm_obj.data.show_names
            wm.sluggies_adapt_mesh_has_body = body is not None
            wm.sluggies_adapt_mesh_has_body_object = body is not None and bool(
                _body_objects(context, arm_obj, body))
            self._fill_rows(context, arm_obj, ordered)
        return wm.invoke_props_dialog(self, width=560)

    def _fill_rows(self, context, arm_obj, ordered):
        """One row per addable mesh: a skinned one (several vertex groups)
        joins the body, others become rigid submeshes; every row gets its
        own free bone in case it becomes rigid."""
        wm = context.window_manager
        rows = wm.sluggies_adapt_mesh_rows
        rows.clear()
        _sluggies, addable = _adapt_mesh_inventory(context, arm_obj)
        pool = list(ordered)
        # Rigid meshes pick their bones first.
        for obj in sorted(addable, key=lambda o: len(o.vertex_groups) > 1):
            row = rows.add()
            row.name = obj.name
            skinned = len(obj.vertex_groups) > 1 and wm.sluggies_adapt_mesh_has_body
            choice = _dominant_bone_choice(arm_obj, obj, pool) if pool else None
            if choice is not None:
                row.bone = f'bone_{choice.bone_id}'
                pool.remove(choice)
            row.action = 'JOIN' if skinned else 'RIGID'

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        arm_obj = _adapt_meshes_armature(context)
        if arm_obj is None:
            layout.label(text="The armature is gone", icon='ERROR')
            return
        layout.label(text=f"Armature: {arm_obj.name}", icon='ARMATURE_DATA')

        sluggies, _addable = _adapt_mesh_inventory(context, arm_obj)
        box = layout.box()
        box.label(text="Sluggies meshes")
        for obj in sluggies:
            split = box.split(factor=0.5)
            split.label(text=obj.name, icon='MESH_DATA')
            split.label(text=_adapt_mesh_status(obj))
        if not sluggies:
            box.label(text="None")

        faulty, messages = _adapt_mesh_faults(wm)
        box = layout.box()
        box.label(text="Meshes to add")
        for row in wm.sluggies_adapt_mesh_rows:
            line = box.row()
            line.alert = row.name in faulty
            split = line.split(factor=0.5)
            split.label(text=row.name, icon='MESH_DATA')
            choice = split.row(align=True)
            choice.prop(row, 'action', text="")
            if row.action == 'RIGID':
                choice.prop(row, 'bone', text="")
        if not len(wm.sluggies_adapt_mesh_rows):
            box.label(text="None: assign meshes to this armature first (step 2)")

        layout.label(text="Only one mesh can be the fully skinned main body.", icon='INFO')
        if any(row.action == 'REPLACE' for row in wm.sluggies_adapt_mesh_rows):
            layout.label(text=_BACKUP_BODY_NOTE, icon='INFO')
        for message in messages:
            line = layout.row()
            line.alert = True
            line.label(text=message, icon='ERROR')

    def execute(self, context):
        wm = context.window_manager
        faulty, messages = _adapt_mesh_faults(wm)
        if messages:
            # A props dialog cannot disable its OK button: show it again.
            self.report({"ERROR"}, messages[0])
            bpy.app.timers.register(_reopen_adapt_meshes, first_interval=0.01)
            return {"CANCELLED"}
        try:
            return self._apply(context)
        finally:
            self._close(context)

    def cancel(self, context):
        self._close(context)

    def _close(self, context):
        wm = context.window_manager
        arm_obj = _adapt_meshes_armature(context)
        if arm_obj is not None:
            arm_obj.data.show_names = wm.sluggies_adapt_mesh_names_shown
        _drop_enum_items(_ADAPT_MESH_BONE_ENUM)

    def _apply(self, context):
        wm = context.window_manager
        arm_obj = _adapt_meshes_armature(context)
        sluggie_path = SluggiePath.resolve(arm_obj) if arm_obj is not None else None
        if not sluggie_path:
            self.report({"ERROR"}, "The armature or its .sluggie is gone")
            return {"CANCELLED"}
        model, tex_dir, error = _model_tex_dir(sluggie_path)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        body = _body_submesh(model)

        chosen = {'JOIN': [], 'REPLACE': [], 'RIGID': []}
        for row in wm.sluggies_adapt_mesh_rows:
            obj = bpy.data.objects.get(row.name)
            if obj is not None and row.action in chosen:
                chosen[row.action].append((obj, _bone_id_from_name(row.bone) if row.action == 'RIGID' else None))
        joins = [obj for obj, _bone in chosen['JOIN']]
        new_body = chosen['REPLACE'][0][0] if chosen['REPLACE'] else None
        rigid = chosen['RIGID']
        if not (joins or new_body or rigid):
            self.report({"INFO"}, "Nothing to change")
            return {"CANCELLED"}

        # Everything that can fail is checked before the scene changes.
        stadium = _model_is_stadium(arm_obj)
        template = ModelCheckCore.default_template(stadium)
        try:
            free = {c.bone_id for c in _ordered_host_bone_choices(context, arm_obj)}
            for obj, bone_id in rigid:
                if bone_id not in free:
                    raise ValueError(f"bone_{bone_id} is no longer free; pick another bone for {obj.name}")
            if rigid:
                _custom_submesh_template_texture_index(model, template)
            if (joins or new_body) and body is None:
                raise ValueError("This model has no skinned main body")
            old_bodies = []
            if new_body is not None:
                old_bodies = [o for o in _body_objects(context, arm_obj, body) if o is not new_body]
                body_obj = new_body
            elif joins:
                bodies = _body_objects(context, arm_obj, body)
                # An imported edit (`_edit`) is the body's current state.
                body_obj = next((o for o in bodies if o.name.endswith('_edit')), bodies[0] if bodies else None)
                if body_obj is None:
                    raise ValueError("No main body object in the scene to join into")
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        lines, notes = [], []
        if new_body is not None:
            if not any(slot.material is not None for slot in new_body.material_slots):
                notes.append(f"{new_body.name} has no material; give it one before Convert Materials")
            notes += _make_body(new_body, arm_obj, model, None)
            lines.append(f"{new_body.name} is now the main body")
            backups = _set_aside_old_bodies(old_bodies)
            if backups:
                lines.append(f"Set aside (off the armature, not exported): {', '.join(backups)}")
        if joins:
            names = ", ".join(obj.name for obj in joins)
            for obj in joins:
                notes += _join_into_body(context, body_obj, obj)
            lines.append(f"Joined into {body_obj.name}: {names}")
        for obj, bone_id in rigid:
            custom_submesh_id, obj_notes = _convert_to_custom_submesh(
                context, obj, arm_obj, bone_id, template, tex_dir, set_up_materials=False)
            lines.append(f"{obj.name} is now rigid submesh {custom_submesh_id} on bone_{bone_id}")
            notes += [f"{obj.name}: {note}" for note in obj_notes]
        lines.append("Next: weight the main body with Map Weights or Autorig")
        _report_adapt_summary(self, context, "Join or Replace Meshes", lines + notes)
        return {"FINISHED"}


# Convert Materials: one row per material of the main body or a rigid custom
# submesh that is not a surface yet, on the operator while the dialog is open.
# Label suffix of the built-ins that share a shader mode.
_BUILTIN_VARIANT_LABELS = {
    'stadium_shdw_opaque_v1': " (opaque)",
    'stadium_shdw_cutout_v1': " (cut-out)",
}
# Drop-down items per stadium flag, kept alive as Blender requires.
_CONVERT_TEMPLATE_ITEMS = {}


def _convert_template_items(stadium):
    """(TemplateSource, shader mode label, tooltip) for each built-in
    template a converted material can take."""
    items = _CONVERT_TEMPLATE_ITEMS.get(stadium)
    if items is None:
        items = []
        for choice in TemplateSources.build_template_source_choices((), stadium):
            template = TemplateSources.BUILTIN_TEMPLATES[choice.argument]
            variant = _BUILTIN_VARIANT_LABELS.get(choice.argument)
            description = template.description if variant else \
                TemplateSources.SHADER_MODE_DESCRIPTIONS.get(template.shader_mode, template.description)
            items.append((choice.template_source, template.shader_mode + (variant or ""), description))
        items = _CONVERT_TEMPLATE_ITEMS[stadium] = tuple(items)
    return items


def _convert_row_template_items(self, _context):
    return _convert_template_items(self.stadium)


class SluggiesConvertMaterialRow(bpy.types.PropertyGroup):
    """One material of the Convert Materials dialog."""
    material_name: StringProperty()  # type: ignore[valid-type]  # '' = a new one is made
    object_name: StringProperty()  # type: ignore[valid-type]  # rigid custom submesh; '' = main body
    stadium: BoolProperty()  # type: ignore[valid-type]
    template: EnumProperty(  # type: ignore[valid-type]
        name="Shader mode", items=_convert_row_template_items,
        description="Shader the surface draws with (the built-in template it is made from)",
    )
    strength: IntProperty(  # type: ignore[valid-type]
        name="Specular strength", min=0, max=255,
        default=TemplateSources.BUILTIN_DEFAULT_SPECULAR_STRENGTH,
        description="Specular highlight intensity (0 = none, 255 = maximum; linear)",
    )

    def specular(self):
        name = self.template.partition(':')[2]
        template = TemplateSources.BUILTIN_TEMPLATES.get(name)
        return template is not None and template.shader_mode in TemplateSources.SPECULAR_SHADER_MODES


def _is_body_surface(mat):
    """A surface the body exports already: an imported sm0 one or an
    Add-material surface of sm0."""
    if mat.get('SluggiesNewSurface'):
        return mat.get('SluggiesSurfaceOwner') == f'sm{_BODY_SUBMESH}' \
            and all(prop in mat for prop in ModelCheckCore.NEW_SURFACE_PROPS)
    match = _SURFACE_ID_RE.match(str(mat.get('SurfaceId') or ''))
    return match is not None and int(match.group(1)) == _BODY_SUBMESH


def _convert_material_targets(context, arm_obj, model):
    """[(material or None, rigid custom submesh or None)]: each material of
    *arm_obj*'s main body (None) and rigid custom submeshes that is not one
    of its surfaces yet, once per owner. A rigid submesh without any
    material is listed with None: a new one is made for it."""
    body = _body_submesh(model)
    targets = []
    body_materials = [slot.material for obj in (_body_objects(context, arm_obj, body) if body else ())
                      for slot in obj.material_slots if slot.material is not None]
    targets += [(mat, None) for mat in dict.fromkeys(body_materials) if not _is_body_surface(mat)]
    for obj in sorted(context.view_layer.objects, key=lambda o: o.name):
        if obj.type != 'MESH' or obj.parent is not arm_obj or not obj.get('SluggiesCustomSubmesh'):
            continue
        custom_submesh_id = str(obj.get('CustomSubmeshId') or '')
        materials = list(dict.fromkeys(slot.material for slot in obj.material_slots if slot.material is not None))
        targets += [(mat, obj) for mat in materials
                    if not (_is_primary_surface_of(mat, custom_submesh_id)
                            or _is_added_surface_of(mat, custom_submesh_id))]
        if not materials:
            targets.append((None, obj))
    return targets


def _own_material(mat, meshes):
    """*mat*, or a copy of it put in *meshes*' slots when a library or any
    other mesh uses it too, so converting it changes nothing else."""
    shared = mat.library is not None or any(
        mesh not in meshes and mat in mesh.materials[:] for mesh in bpy.data.meshes)
    if not shared:
        return mat
    copy = mat.copy()
    for mesh in meshes:
        for index, slot_material in enumerate(mesh.materials):
            if slot_material == mat:
                mesh.materials[index] = copy
    return copy


class SLUGGIES_OT_adapt_materials(bpy.types.Operator):
    """Convert Materials step of the 'Adapt' box"""
    bl_idname = "sluggies.adapt_materials"
    bl_label = "Convert Materials"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'MATERIALS')
    bl_options = {"UNDO"}

    rows: bpy.props.CollectionProperty(type=SluggiesConvertMaterialRow)  # type: ignore[valid-type]
    armature_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        if _selected_sluggies_armature(context) is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Select a Sluggies armature or one of its child objects")
            return False
        return True

    def _model(self, arm_obj):
        """(model, tex dir) of *arm_obj*, or reports and returns None."""
        sluggie_path = SluggiePath.resolve(arm_obj) if arm_obj is not None else None
        if not sluggie_path:
            self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Convert Materials")
                        if arm_obj is not None else "The armature is gone")
            return None
        model, tex_dir, error = _model_tex_dir(sluggie_path)
        if error:
            self.report({"ERROR"}, error)
            return None
        return model, tex_dir

    def invoke(self, context, event):
        arm_obj = _selected_sluggies_armature(context)
        found = self._model(arm_obj)
        if found is None:
            return {"CANCELLED"}
        model, _tex_dir = found
        stadium = _model_is_stadium(arm_obj)
        offered = {item[0] for item in _convert_template_items(stadium)}
        default = ModelCheckCore.default_template(stadium)
        self.armature_name = arm_obj.name
        self.rows.clear()
        for mat, obj in _convert_material_targets(context, arm_obj, model):
            row = self.rows.add()
            row.material_name = mat.name if mat is not None else ''
            row.object_name = obj.name if obj is not None else ''
            row.stadium = stadium
            # A template the material or its submesh carries already, if offered.
            candidates = [mat.get('TemplateSource') if mat is not None and mat.get('SluggiesNewSurface') else None,
                          obj.get('TemplateSource') if obj is not None else None]
            row.template = next((str(c) for c in candidates if c and str(c) in offered), default)
            if mat is not None and 'SpecularStrength' in mat:
                row.strength = max(0, min(255, int(mat['SpecularStrength'])))
        if not self.rows:
            self.report({"INFO"}, "No materials to convert: the body and rigid submeshes use Sluggies surfaces")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=640)

    @staticmethod
    def _columns(layout):
        split = layout.split(factor=0.36)
        material_col = split.column()
        rest = split.split(factor=0.42)
        mesh_col = rest.column()
        rest = rest.split(factor=0.5)
        return material_col, mesh_col, rest.column(), rest.column()

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Armature: {self.armature_name}", icon='ARMATURE_DATA')
        box = layout.box()
        col = box.column(align=True)
        material_col, mesh_col, mode_col, strength_col = self._columns(col.row())
        material_col.label(text="Material")
        mesh_col.label(text="Mesh")
        mode_col.label(text="Shader mode")
        strength_col.label(text="Specular strength")
        for row in self.rows:
            material_col, mesh_col, mode_col, strength_col = self._columns(col.row())
            material_col.label(text=row.material_name or "(new material)", icon='MATERIAL')
            mesh_col.label(text=row.object_name or "Main body")
            mode_col.prop(row, 'template', text="")
            if row.specular():
                strength_col.prop(row, 'strength', text="")
            else:
                strength_col.label(text="no specular")
        layout.label(text="OK also removes the nodes that do not feed a shader's colour", icon='INFO')
        if bpy.data.filepath:
            folder = os.path.basename(TextureFolder.folder_for(bpy.data.filepath))
            layout.label(text=f"... and copies the textures in use to //{folder}/", icon='INFO')

    def execute(self, context):
        arm_obj = bpy.data.objects.get(self.armature_name)
        found = self._model(arm_obj)
        if found is None:
            return {"CANCELLED"}
        model, tex_dir = found
        # Everything that can fail is checked before the scene changes.
        try:
            for row in self.rows:
                _custom_submesh_template_texture_index(model, row.template)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        body = _body_submesh(model)
        body_meshes = {obj.data for obj in (_body_objects(context, arm_obj, body) if body else ())}

        # Rows by owner, each with its own (unshared) material.
        body_rows, rigid_rows = [], {}
        for row in self.rows:
            obj = bpy.data.objects.get(row.object_name) if row.object_name else None
            mat = bpy.data.materials.get(row.material_name) if row.material_name else None
            if (row.object_name and obj is None) or (row.material_name and mat is None):
                continue
            if mat is not None:
                mat = _own_material(mat, {obj.data} if obj is not None else body_meshes)
            if obj is None:
                body_rows.append((row, mat))
            else:
                rigid_rows.setdefault(obj, []).append((row, mat))

        links = nodes = 0
        for _row, mat in body_rows + [entry for entries in rigid_rows.values() for entry in entries]:
            if mat is not None:
                removed_links, removed_nodes = AdaptCleanup.convert_material_nodes(mat)
                links += removed_links
                nodes += removed_nodes

        notes = []
        owner = f'sm{_BODY_SUBMESH}'
        for row, mat in body_rows:
            for prop in _SLUGGIES_MATERIAL_PROPS:
                if prop in mat:
                    del mat[prop]
            existing = [m.get('SurfaceId', '') for m in bpy.data.materials if m.get('SurfaceId')]
            wrap_s, wrap_t = _template_wrap_modes(context, arm_obj, row.template)
            _apply_new_surface_plan(mat, {
                'kind': 'donor',
                'owner': owner,
                'surface_id': TemplateSources.next_new_surface_key(owner, existing),
                'template_source': row.template,
                'template_texture_index': _custom_submesh_template_texture_index(model, row.template),
                'shader_mode': _template_shader_mode(context, arm_obj, row.template),
                'specular_strength': row.strength if row.specular()
                else TemplateSources.BUILTIN_DEFAULT_SPECULAR_STRENGTH,
                'wrap_s': wrap_s,
                'wrap_t': wrap_t,
            })
            note = _ensure_surface_texture(context, mat, tex_dir)
            if note:
                notes.append(note)

        for obj, entries in rigid_rows.items():
            by_name = {mat.name: row for row, mat in entries if mat is not None}
            # A submesh without material is listed once (no material): the
            # primary material made for it takes that row's choices.
            new_row = next((row for row, mat in entries if mat is None), None)
            before = {mat.name for mat in obj.data.materials if mat is not None}

            def template_of(mat, by_name=by_name, new_row=new_row):
                row = by_name.get(mat.name, new_row)
                return row.template if row is not None else ModelCheckCore.default_template(
                    _model_is_stadium(arm_obj))

            notes += [f"{obj.name}: {note}" for note in
                      _make_submesh_materials(context, obj, arm_obj, tex_dir, template_of)]
            for mat in obj.data.materials:
                if mat is None:
                    continue
                row = by_name.get(mat.name) or (new_row if mat.name not in before else None)
                if row is not None and row.specular() and 'SpecularStrength' in mat:
                    mat['SpecularStrength'] = row.strength

        rigid_count = sum(len(entries) for entries in rigid_rows.values())
        lines = [f"Converted {_materials(len(body_rows) + rigid_count)}: "
                 f"{len(body_rows)} on the main body, {rigid_count} on rigid submeshes"]
        if links or nodes:
            lines.append(f"Removed {links} node link{'s' if links != 1 else ''} and "
                         f"{nodes} node{'s' if nodes != 1 else ''} that did not feed a shader's colour")
        if bpy.data.filepath:   # a saved project: its textures go next to the .blend
            folder = TextureFolder.folder_for(bpy.data.filepath)
            moved, written, texture_notes = TextureFolder.collect(_model_images(context), folder)
            lines.append(f"{moved} texture{'s' if moved != 1 else ''} in use now in //{os.path.basename(folder)}/ "
                         f"({written} copied there)")
            notes += texture_notes
        _report_adapt_summary(self, context, "Convert Materials", lines + notes)
        return {"FINISHED"}


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


def _materials(count):
    return f"{count} material{'s' if count != 1 else ''}"


_ADAPT_WEIGHT_METHODS = (
    ('MAP', "Map vertex groups",
     "Keep the old rig's weights: each vertex group is renamed to, or merged into, the nearest Sluggers bone"),
    ('AUTO', "Automatic weights",
     "Throw the old weights away and let Blender weight the body anew from how close it is to each bone"),
)


class SLUGGIES_OT_adapt_weights(bpy.types.Operator):
    """Map Weights or Autorig step of the 'Adapt' box (AdaptWeights)"""
    bl_idname = "sluggies.adapt_weights"
    bl_label = "Map Weights or Autorig"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'WEIGHTS')
    bl_options = {"REGISTER", "UNDO"}

    method: EnumProperty(  # type: ignore[valid-type]
        name="Method", items=_ADAPT_WEIGHT_METHODS, default='MAP',
    )
    whole_body: BoolProperty(  # type: ignore[valid-type]
        name="Redo the whole body",
        description="Also reweight the vertices that already have Sluggers bone weights "
                    "(the donor body the new mesh was joined into)",
        default=False,
    )
    remove_old_groups: BoolProperty(  # type: ignore[valid-type]
        name="Remove the old rig's vertex groups",
        description="Delete the vertex groups that are not Sluggers bones (those a modifier uses stay)",
        default=True,
    )
    armature_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]
    object_names: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]  # '\n'-joined
    mappable: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]
    surveys: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]  # '\n'-joined lines
    any_kept: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        if _selected_sluggies_armature(context) is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Select a Sluggies armature or one of its child objects")
            return False
        return True

    def _bodies(self, context, arm_obj):
        """The body objects to weight, or reports and returns None. With
        several, the ones with work left (old rig groups or vertices without
        a bone weight) unless none has any."""
        sluggie_path = SluggiePath.resolve(arm_obj) if arm_obj is not None else None
        if not sluggie_path:
            self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Map Weights or Autorig")
                        if arm_obj is not None else "The armature is gone")
            return None
        model, _tex_dir, error = _model_tex_dir(sluggie_path)
        if model is None:
            self.report({"ERROR"}, error)
            return None
        body = _body_submesh(model)
        bodies = _body_objects(context, arm_obj, body) if body is not None else []
        if not bodies:
            self.report({"ERROR"}, "This model has no skinned main body in the scene"
                        + (" (step 3 makes one)" if body is not None else ""))
            return None
        surveys = {obj: AdaptWeights.survey(obj, arm_obj) for obj in bodies}
        busy = [obj for obj, s in surveys.items() if s['open'] or s['sources']]
        return [(obj, surveys[obj]) for obj in (busy or bodies)]

    def invoke(self, context, event):
        arm_obj = _selected_sluggies_armature(context)
        found = self._bodies(context, arm_obj)
        if found is None:
            return {"CANCELLED"}
        self.armature_name = arm_obj.name
        self.object_names = "\n".join(obj.name for obj, _s in found)
        self.mappable = any(s['mappable'] for _obj, s in found)
        self.any_kept = any(s['open'] < s['vertices'] for _obj, s in found)
        self.method = 'MAP' if self.mappable else 'AUTO'
        self.whole_body = not any(s['open'] for _obj, s in found)
        lines = []
        for obj, s in found:
            lines.append(f"{obj.name}: {s['vertices']:,} vertices, "
                         f"{s['sources']} vertex group{'s' if s['sources'] != 1 else ''} from its old rig")
            if s['open'] < s['vertices']:
                lines.append(f"{s['vertices'] - s['open']:,} vertices already have Sluggers bone weights")
        self.surveys = "\n".join(lines)
        return context.window_manager.invoke_props_dialog(self, width=640)

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Armature: {self.armature_name}", icon='ARMATURE_DATA')
        box = layout.box()
        for line in self.surveys.split("\n"):
            box.label(text=line, icon='MESH_DATA' if ':' in line else 'BLANK1')
        col = layout.column()
        if self.mappable:
            col.prop(self, 'method', expand=True)
        else:
            col.label(text="Too few vertex groups from an old rig to map: automatic weights only", icon='INFO')
        description = next(desc for ident, _label, desc in _ADAPT_WEIGHT_METHODS if ident == self.method)
        col.label(text=description + ".")
        if self.method == 'AUTO':
            if self.any_kept:
                col.prop(self, 'whole_body')
            col.prop(self, 'remove_old_groups')

    def execute(self, context):
        arm_obj = bpy.data.objects.get(self.armature_name) if self.armature_name \
            else _selected_sluggies_armature(context)
        if arm_obj is None:
            self.report({"ERROR"}, "The armature is gone")
            return {"CANCELLED"}
        if self.object_names:
            objects = [bpy.data.objects[name] for name in self.object_names.split("\n")
                       if name in bpy.data.objects]
        else:  # run again from Adjust Last Operation or a script
            found = self._bodies(context, arm_obj)
            objects = [obj for obj, _s in found] if found is not None else []
        if not objects:
            self.report({"ERROR"}, "The main body is gone")
            return {"CANCELLED"}

        lines, results = [], []
        try:
            for obj in objects:
                if obj.data.users > 1:
                    obj.data = obj.data.copy()
                    lines.append(f"{obj.name}'s mesh data was shared, so it got its own copy")
                if self.method == 'MAP':
                    if not AdaptWeights.survey(obj, arm_obj)['mappable']:
                        lines.append(f"{obj.name}: too few vertex groups from an old rig to map, left as it was")
                        continue
                    result = AdaptWeights.map_groups(obj, arm_obj)
                else:
                    result = AdaptWeights.autorig(context, obj, arm_obj, self.whole_body, self.remove_old_groups)
                    _ensure_armature_modifier(obj, arm_obj)
                    _parent_keeping_world(obj, arm_obj)
                lines += AdaptWeights.summary_lines(result)
                results.append(result)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        title = "Map Weights" if self.method == 'MAP' else "Automatic Weights"
        _report_adapt_summary(self, context, title, lines, AdaptWeights.warning_lines(objects, arm_obj, results))
        return {"FINISHED"}


class SluggiesLowpolyRow(bpy.types.PropertyGroup):
    """One High model mesh of the Generate Lowpoly dialog."""
    object_name: StringProperty()  # type: ignore[valid-type]
    kind: StringProperty()  # type: ignore[valid-type]
    vertices: IntProperty()  # type: ignore[valid-type]
    mode: EnumProperty(  # type: ignore[valid-type]
        name="Mode", items=AdaptLowpoly.MODES, default=AdaptLowpoly.COLLAPSE,
    )
    iterations: EnumProperty(  # type: ignore[valid-type]
        name="Iterations", items=AdaptLowpoly.UNSUBDIV_ITERATIONS, default='2',
    )
    tris_to_quads: BoolProperty(  # type: ignore[valid-type]
        name="Triangles to quads",
        description="Join triangles into quads first (60° face and shape angles, topology influence 1, "
                    "UVs, seams, sharp edges and materials compared): Un-Subdivide finds no grid in triangles",
        default=True,
    )
    ratio: FloatProperty(  # type: ignore[valid-type]
        name="Ratio", description="Share of the faces to keep",
        min=0.01, max=1.0, default=AdaptLowpoly.DEFAULT_RATIO, subtype='FACTOR',
    )


def _is_collision_mesh(obj):
    collision = getattr(obj, 'sluggies_collision', None)
    return collision is not None and collision.enabled


def _is_body_object(obj):
    """An imported (or Make Main Body) skinned body object."""
    return not obj.get('SluggiesCustomSubmesh') and int(obj.get('VertexBufferCompCount') or 0) == 6


def _lowpoly_partner(context, high):
    """(the imported Low model of the High model *high*, None), or (None, why
    there is none)."""
    high_path = SluggiePath.resolve(high, relink=False)
    if not high_path:
        return None, SluggiePath.missing_message(high, "Generate Lowpoly")
    if AdaptLowpoly.is_low_model(high_path):
        return None, f"{high.name} is a Low (_L_) model: select its High model"
    candidates = {}
    for arm_obj in _sluggies_armatures(context):
        path = SluggiePath.resolve(arm_obj, relink=False) if arm_obj is not high else ''
        if path:
            candidates[path] = arm_obj
    low = AdaptLowpoly.low_partner(high_path, candidates)
    if low is None:
        return None, f"Import the Low (_L_) version of {high.name} first"
    return low, None


def _lowpoly_templates(low_model, stadium, obj, custom):
    """{material name: template} for *obj*'s materials on the Low model: the
    one each has on the High model (a donor Spec surface keeps its look as
    `derived:`) when the Low model resolves it, else the default. *custom*:
    *obj* becomes a custom submesh there."""
    default = ModelCheckCore.default_template(stadium)
    own_id = str(obj.get('CustomSubmeshId') or '')
    templates = {}
    for mat in dict.fromkeys(slot.material for slot in obj.material_slots if slot.material is not None):
        surface_id = str(mat.get('SurfaceId') or '')
        if mat.get('SluggiesNewSurface'):
            candidate = str(mat.get('TemplateSource') or '')
        elif own_id and _is_primary_surface_of(mat, own_id):
            candidate = str(obj.get('TemplateSource') or '')
        elif _SURFACE_ID_RE.match(surface_id) and mat.get('ShaderMode') == 'Spec':
            candidate = f'derived:{surface_id}'
        else:
            candidate = ''
        if candidate:
            try:
                _custom_submesh_template_texture_index(low_model, candidate)
                if custom:
                    CustomSubmeshExport.attribute_plan(low_model, candidate)
            except (ValueError, KeyError):
                candidate = ''
        templates[mat.name] = candidate or default
    return templates


def _strip_donor_props(obj):
    """Drop the High model's submesh and custom submesh properties from a copy."""
    for prop in ModelCheckCore.REQUIRED_PROPS + (
            'SluggiesCustomSubmesh', 'CustomSubmeshId', 'TemplateSource', 'SluggiesRigidPlacement') + tuple(
            key for key in obj.keys() if _DONOR_OBJECT_PROP_RE.match(key)):
        if prop in obj:
            del obj[prop]


def _unhide(obj):
    """Show *obj* (Edit Mode needs it); returns what _rehide needs."""
    hidden = (obj.hide_get(), obj.hide_viewport)
    obj.hide_set(False)
    obj.hide_viewport = False
    return hidden


def _rehide(obj, hidden):
    obj.hide_set(hidden[0])
    obj.hide_viewport = hidden[1]


def _mirror_added_bones(context, high, low, bones):
    """Add *bones* (added bones of *high*, in creation order) to *low*, placed
    and set up as on *high*, so they get the same ids. Returns the names
    that did not come out the same."""
    # Read *high*'s bones first: Edit Mode also takes in a selected *high*,
    # which makes new Bone structs and leaves the old references dangling.
    snapshot = [(bone.name, bone.parent.name if bone.parent is not None else None, bone.matrix_local.copy(),
                 bone.length, dict(bone.items())) for bone in bones]
    prev_active = context.view_layer.objects.active
    hidden = _unhide(low)
    context.view_layer.objects.active = low
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        edit_bones = low.data.edit_bones
        for name, parent_name, matrix, length, _props in snapshot:
            new_eb = edit_bones.new(name)
            new_eb.head = (0.0, 0.0, 0.0)
            new_eb.tail = (0.0, length, 0.0)
            new_eb.matrix = matrix
            new_eb.parent = edit_bones.get(parent_name) if parent_name else None
            new_eb.use_connect = False
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
        context.view_layer.objects.active = prev_active
        _rehide(low, hidden)
    order = _next_new_bone_creation_order(low)
    for name, _parent_name, _matrix, _length, props in snapshot:
        new_bone = low.data.bones.get(name)
        if new_bone is None:
            continue
        for key, value in props.items():
            new_bone[key] = value
        new_bone['SluggiesCreationOrder'] = order
        order += 1
        BoneRoles.bone_collection(low.data, BoneRoles.ADDED).assign(new_bone)
    _renumber_added_bones(low)
    return [name for name, *_rest in snapshot if name not in low.data.bones]


class SLUGGIES_OT_adapt_lowpoly(bpy.types.Operator):
    """Generate Lowpoly step of the 'Adapt' box (AdaptLowpoly)"""
    bl_idname = "sluggies.adapt_lowpoly"
    bl_label = "Generate Lowpoly"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'LOWPOLY')
    bl_options = {"UNDO"}

    rows: bpy.props.CollectionProperty(type=SluggiesLowpolyRow)  # type: ignore[valid-type]
    high_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]
    low_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Switch to Object Mode")
            return False
        high = _selected_sluggies_armature(context)
        if high is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set("Select the High model's armature or one of its child objects")
            return False
        low, problem = _lowpoly_partner(context, high)
        if low is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set(problem)
            return False
        return True

    def invoke(self, context, event):
        high = _selected_sluggies_armature(context)
        low, problem = _lowpoly_partner(context, high) if high is not None else (None, "Select a High model")
        if low is None:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        for arm_obj in (high, low):
            if not _bone_metadata_is_current(arm_obj):
                self.report({"ERROR"}, f"{arm_obj.name}: {HostBones.RE_IMPORT_MESSAGE}")
                return {"CANCELLED"}
        self.high_name, self.low_name = high.name, low.name
        self.rows.clear()
        sluggies, _addable = _adapt_mesh_inventory(context, high)
        meshes = [obj for obj in sluggies if not _is_collision_mesh(obj)]
        bodies = [obj for obj in meshes if _is_body_object(obj)]
        # Only one body object can go (an imported `_edit` is the body's current state).
        body = next((o for o in bodies if o.name.endswith('_edit')), bodies[0] if bodies else None)
        for obj in sorted(meshes, key=lambda o: (o is not body, o not in bodies, o.name)):
            row = self.rows.add()
            row.object_name = obj.name
            row.kind = _adapt_mesh_status(obj)
            row.vertices = len(obj.data.vertices)
            row.mode = AdaptLowpoly.IGNORE if obj in bodies and obj is not body \
                else AdaptLowpoly.default_mode(row.vertices)
        if not self.rows:
            self.report({"INFO"}, f"{high.name} has no Sluggies meshes to copy")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=760)

    @staticmethod
    def _columns(layout):
        split = layout.split(factor=0.3)
        name_col = split.column()
        rest = split.split(factor=0.36)
        kind_col = rest.column()
        rest = rest.split(factor=0.2)
        count_col = rest.column()
        rest = rest.split(factor=0.5)
        return name_col, kind_col, count_col, rest.column(), rest.column()

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"High: {self.high_name}    Low: {self.low_name}", icon='ARMATURE_DATA')
        box = layout.box()
        col = box.column(align=True)
        name_col, kind_col, count_col, mode_col, value_col = self._columns(col.row())
        name_col.label(text="Mesh")
        kind_col.label(text="Kind")
        count_col.label(text="Vertices")
        mode_col.label(text="Mode")
        value_col.label(text="Setting")
        for row in self.rows:
            name_col, kind_col, count_col, mode_col, value_col = self._columns(col.row())
            name_col.label(text=row.object_name, icon='MESH_DATA')
            kind_col.label(text=row.kind)
            count_col.label(text=f"{row.vertices:,}")
            mode_col.prop(row, 'mode', text="")
            if row.mode == AdaptLowpoly.UNSUBDIV:
                line = value_col.row(align=True)
                line.prop(row, 'iterations', expand=True)
                line.prop(row, 'tris_to_quads', text="Tris to quads")
            elif row.mode == AdaptLowpoly.COLLAPSE:
                value_col.prop(row, 'ratio', text="")
            else:
                value_col.label(text="")
        layout.label(text="The main body replaces the Low model's body; a rigid mesh replaces the Low model's "
                          "mesh on its bone, or is added there", icon='INFO')

    def execute(self, context):
        high = bpy.data.objects.get(self.high_name)
        low = bpy.data.objects.get(self.low_name)
        if high is None or low is None:
            self.report({"ERROR"}, "The High or the Low armature is gone")
            return {"CANCELLED"}
        low_path = SluggiePath.resolve(low)
        if not low_path:
            self.report({"ERROR"}, SluggiePath.missing_message(low, "Generate Lowpoly"))
            return {"CANCELLED"}
        low_model, low_tex_dir, error = _model_tex_dir(low_path)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        chosen = []
        for row in self.rows:
            obj = bpy.data.objects.get(row.object_name)
            if obj is not None and row.mode != AdaptLowpoly.IGNORE:
                chosen.append((obj, row))
        if not chosen:
            self.report({"INFO"}, "Nothing to copy: every mesh is set to Ignore")
            return {"CANCELLED"}
        _renumber_added_bones_reporting(self, high)
        _renumber_added_bones_reporting(self, low)
        # Everything that can fail is checked before the Low model changes.
        try:
            plan = self._plan(context, high, low, low_model, chosen)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return self._apply(context, high, low, low_model, low_tex_dir, plan)

    @staticmethod
    def _plan(context, high, low, low_model, chosen):
        """What goes where: {'body': (source, row) or None, 'old_bodies',
        'rigid': [(bone id, [(source, row)], Low submesh index or None,
        Low objects it replaces)], 'mirror': High added bones to add}.
        Raises ValueError."""
        bodies = [(obj, row) for obj, row in chosen if _is_body_object(obj)]
        if len(bodies) > 1:
            raise ValueError("Only one main body object can go to the Low model: set the others to Ignore")
        old_bodies = []
        if bodies:
            if not any(slot.material is not None for slot in bodies[0][0].material_slots):
                raise ValueError(f"{bodies[0][0].name} has no material; give it one first")
            low_body = _body_submesh(low_model)
            if low_body is None:
                raise ValueError(f"{low.name}'s submesh 0 is not a skinned body")
            old_bodies = _body_objects(context, low, low_body)

        by_bone = {}
        for obj, row in chosen:
            if _is_body_object(obj):
                continue
            bone_id = _uniform_bone_id(obj)
            if bone_id is None:
                raise ValueError(f"{obj.name} does not follow exactly one bone: weight it fully to one "
                                 "bone_<id> group, or set it to Ignore")
            by_bone.setdefault(bone_id, []).append((obj, row))

        used = {group.name for obj, _row in chosen for group in obj.vertex_groups
                if group.name in high.data.bones}
        mirror = []
        if used - set(low.data.bones.keys()):
            mirror = AdaptLowpoly.bones_to_mirror(high, low)
            missing = used - set(low.data.bones.keys()) - {bone.name for bone in mirror}
            if missing:
                raise ValueError(f"{low.name} lacks the bones {', '.join(sorted(missing))}")
            hidden = _unhide(low)
            visible = low.visible_get()
            _rehide(low, hidden)
            if not visible:
                raise ValueError(f"Show the collection of {low.name} first: the bones added on {high.name} "
                                 "are added to it too")
        free = {c.bone_id for c in _ordered_host_bone_choices(context, low)}
        free |= {_bone_id_from_name(bone.name) for bone in mirror}

        low_donors = [o for o in _donor_mesh_objects(context, low)
                      if not _is_body_object(o) and not _is_collision_mesh(o)]
        low_customs = [o for o in context.view_layer.objects
                       if o.type == 'MESH' and o.parent is low and o.get('SluggiesCustomSubmesh')]
        rigid = []
        for bone_id, sources in sorted(by_bone.items()):
            on_bone = [o for o in low_donors if _uniform_bone_id(o) == bone_id]
            indices = sorted({index for index in (ModelCheckCore.submesh_index_by_offset(
                low_model, o.get('VertexBufferOffset')) for o in on_bone) if index is not None})
            customs = [o for o in low_customs if _uniform_bone_id(o) == bone_id]
            # What an earlier run put there goes again.
            made = [o for o in customs if o.get(AdaptLowpoly.LOW_SOURCE_PROP)]
            if indices:
                offset = str(ModelCheckCore.mesh_props_for_submesh(low_model, indices[0])['VertexBufferOffset'])
                replaced = [o for o in on_bone if str(o.get('VertexBufferOffset')) == offset]
                rigid.append((bone_id, sources, indices[0], replaced + made))
            elif len(made) == len(customs) and (customs or bone_id in free):
                rigid.append((bone_id, sources, None, made))
            else:
                taken = ", ".join(o.name for o in customs if not o.get(AdaptLowpoly.LOW_SOURCE_PROP))
                raise ValueError(f"bone_{bone_id} is not free on {low.name}" + (f" ({taken} is on it)" if taken else "")
                                 + f": set {', '.join(obj.name for obj, _row in sources)} to Ignore")
        # Custom submeshes an earlier run made from these meshes go as well (a
        # mesh may have become the body since).
        names = {obj.name for obj, _row in chosen}
        stale = [o for o in low_customs if o.get(AdaptLowpoly.LOW_SOURCE_PROP) in names]
        return {'body': bodies[0] if bodies else None, 'old_bodies': old_bodies, 'rigid': rigid, 'mirror': mirror,
                'stale': stale}

    def _apply(self, context, high, low, low_model, low_tex_dir, plan):
        stadium = _model_is_stadium(low)
        default = ModelCheckCore.default_template(stadium)
        collection = low.users_collection[0]
        to_low = low.matrix_world @ high.matrix_world.inverted()
        copies, notes = [], []

        def make_copy(obj, row):
            copy = AdaptLowpoly.copy_object(obj, collection)
            copies.append(copy)
            copy.name = f'{obj.name}_low'
            copy[AdaptLowpoly.LOW_SOURCE_PROP] = obj.name
            copy.matrix_world = to_low @ obj.matrix_world
            notes.extend(AdaptLowpoly.decimate(context, copy, row.mode, row.iterations, row.ratio,
                                               row.tris_to_quads))
            return copy

        # Copies first: until they are done, a failure only removes them.
        try:
            body = None
            if plan['body'] is not None:
                source, row = plan['body']
                copy = make_copy(source, row)
                templates = _lowpoly_templates(low_model, stadium, copy, custom=False)
                plans = _body_surface_plans(context, low, copy, low_model,
                                            template_of=lambda mat: templates.get(mat.name, default))
                body = (source, copy, plans)
            rigid = []
            for bone_id, sources, index, replaced in plan['rigid']:
                group, templates = [], {}
                for source, row in sources:
                    copy = make_copy(source, row)
                    templates.update(_lowpoly_templates(low_model, stadium, copy, custom=index is None))
                    group.append(copy)
                copy = group[0]
                if len(group) > 1:   # several meshes on one bone become one
                    with context.temp_override(active_object=copy, object=copy, selected_objects=group,
                                               selected_editable_objects=group):
                        bpy.ops.object.join()
                    for joined in group[1:]:
                        copies.remove(joined)
                surface_plans = None
                if index is not None:
                    surface_plans = _body_surface_plans(
                        context, low, copy, low_model, owner=f'sm{index}',
                        template_of=lambda mat, templates=templates: templates.get(mat.name, default))
                rigid.append((bone_id, sources, index, replaced, copy, templates, surface_plans))
        except (RuntimeError, ValueError) as exc:
            for copy in copies:
                if copy.name in bpy.data.objects:
                    AdaptLowpoly.remove_object(copy)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        def vertices(sources):
            return sum(len(source.data.vertices) for source, _row in sources)

        lines, warnings = [], []
        if plan['mirror']:
            names = [bone.name for bone in plan['mirror']]
            unmatched = _mirror_added_bones(context, high, low, plan['mirror'])
            lines.append(f"Added bones {', '.join(names)} to {low.name}, as on {high.name}")
            if unmatched:
                warnings.append(f"{', '.join(unmatched)} came out under other names on {low.name}: "
                                "check its added bones")
        # An earlier run's custom submeshes go first, so their ids are free again.
        to_remove = dict.fromkeys(plan['old_bodies'] + plan['stale']
                                  + [obj for entry in rigid for obj in entry[3]])
        replaced_later = []
        for obj in to_remove:
            if obj.get('SluggiesCustomSubmesh'):
                AdaptLowpoly.remove_object(obj)
            else:
                replaced_later.append(obj)
        context.view_layer.update()   # drop the removed objects from view_layer.objects
        # Custom submeshes before the replacements: their templates may read
        # the Low model's own materials.
        for bone_id, sources, index, replaced, copy, templates, _plans in rigid:
            if index is not None:
                continue
            strengths = {mat.name: mat['SpecularStrength'] for mat in copy.data.materials
                         if mat is not None and 'SpecularStrength' in mat}
            _strip_donor_props(copy)
            custom_submesh_id, copy_notes = _convert_to_custom_submesh(
                context, copy, low, bone_id, default, low_tex_dir,
                template_of=lambda mat, templates=templates: templates.get(mat.name, default))
            for mat in copy.data.materials:
                if mat is not None and 'SpecularStrength' in mat and mat.name in strengths:
                    mat['SpecularStrength'] = strengths[mat.name]
            lines.append(f"{', '.join(s.name for s, _r in sources)}: {vertices(sources):,} -> "
                         f"{len(copy.data.vertices):,} vertices, added submesh {custom_submesh_id} on bone_{bone_id}")
            notes += [f"{copy.name}: {note}" for note in copy_notes]
        for bone_id, sources, index, replaced, copy, _templates, surface_plans in rigid:
            if index is None:
                continue
            _strip_donor_props(copy)
            for prop, value in ModelCheckCore.mesh_props_for_submesh(low_model, index).items():
                copy[prop] = value
            _apply_body_surface_plans(copy.data, surface_plans)
            _ensure_armature_modifier(copy, low)
            _parent_keeping_world(copy, low)
            old = ", ".join(o.name for o in replaced if not o.get('SluggiesCustomSubmesh')) or f"submesh {index}"
            lines.append(f"{', '.join(s.name for s, _r in sources)}: {vertices(sources):,} -> "
                         f"{len(copy.data.vertices):,} vertices, replaces {old} on bone_{bone_id}")
        if body is not None:
            source, copy, plans = body
            _strip_donor_props(copy)
            notes += [f"{copy.name}: {note}" for note in _make_body(copy, low, low_model, plans)]
            old = ", ".join(o.name for o in plan['old_bodies']) or "nothing"
            lines.insert(0, f"{source.name}: {len(source.data.vertices):,} -> {len(copy.data.vertices):,} "
                            f"vertices, the Low model's main body (replaces {old})")
        for obj in replaced_later:
            if obj.name in bpy.data.objects:
                AdaptLowpoly.remove_object(obj)
        _UNIFORM_BONE_CACHE.clear()
        lines.append(f"Next: check {low.name} and export it with its High model")
        _report_adapt_summary(self, context, "Generate Lowpoly", lines + notes, warnings)
        return {"FINISHED"}


# Adapt steps implemented by their own operator instead of the placeholder
# (bl_idname; Check Models is ModelCheckUI's, which imports this module).
_ADAPT_STEP_OPERATORS = {
    'CLEANUP': SLUGGIES_OT_adapt_cleanup.bl_idname,
    'ARMATURE': SLUGGIES_OT_adapt_armature.bl_idname,
    # 'TRANSFORM': SLUGGIES_OT_adapt_transform.bl_idname,  # hidden, see _ADAPT_STEPS
    'MESHES': SLUGGIES_OT_adapt_meshes.bl_idname,
    'MATERIALS': SLUGGIES_OT_adapt_materials.bl_idname,
    'WEIGHTS': SLUGGIES_OT_adapt_weights.bl_idname,
    'LOWPOLY': SLUGGIES_OT_adapt_lowpoly.bl_idname,
    'CHECK': "sluggies.check_models",
}


def _draw_adapt_box(layout, context):
    """The 'Adapt' box: one button per _ADAPT_STEPS entry, numbered in order."""
    box = _collapsible_box(layout, context, 'adapt', "Adapt")
    if box is None:
        return
    col = box.column(align=True)
    if not bpy.data.filepath:  # never saved: no .blend to save to yet
        col.operator("wm.save_as_mainfile", text="0. Create a Blend file!", icon='FILE_BLEND')
    for n, (ident, label, _desc) in enumerate(_ADAPT_STEPS, 1):
        if context.selected_objects:
            label = {'CLEANUP': "Clean (selected)", 'ARMATURE': "Assign to Armature (selected)"}.get(ident, label)
        if ident in _ADAPT_STEP_OPERATORS:
            col.operator(_ADAPT_STEP_OPERATORS[ident], text=f"{n}. {label}")
        else:
            col.operator(SLUGGIES_OT_adapt_step.bl_idname, text=f"{n}. {label}").step = ident


# Sidebar boxes that collapse, each with a `Scene.sluggies_show_<key>_box`
# toggle.
_COLLAPSIBLE_BOXES = ('rules', 'import_export', 'adapt', 'textures', 'bones', 'animations', 'mesh',
                      'collision')
# Boxes that start collapsed; all others start expanded.
_COLLAPSED_BY_DEFAULT = ('rules', 'textures', 'bones', 'mesh')

# Text of the 'Sluggers Model Rules' box, one bullet per entry.
_MODEL_RULES = (
    "Every model contains only one skinned submesh: submesh0.",
    "Every other mesh object must be a rigid mesh that follows only one bone.",
    "Every vertex of the skinned mesh needs a weight, on at most 4 bones.",
    "A mesh can have at most 65,535 vertices.",
    "Textures can be at most 1024x1024.",
    "If larger textures are needed, use Dolphin's texture Load folder for overriding.",
    "Every character has a High and a Low poly version (_L_).",
    "Low poly models use the skeleton and textures of the High version; "
    "they do not store their own textures.",
    "New meshes brought in from elsewhere that need bone deformation should be "
    "joined into the skinned mesh, via 'Make Active Mesh into a Submesh' or Ctrl+J.",
    "Facial expressions are done via shape keys.",
    "Use 'Check Models' to detect problems and, where possible, fix them automatically.",
    "Never rename bones, and never leave gaps in the bone IDs.",
    "Don't change the skeletal structure: never delete or re-parent original bones.",
    "Don't rename the original (imported) mesh objects.",
    "All meshes of one model must be children of the same armature.",
)


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


def _draw_rules_box(layout, context):
    """The 'Sluggers Model Rules' box: read-only bullets, wrapped to the sidebar width."""
    box = _collapsible_box(layout, context, 'rules', "Sluggers Model Rules")
    if box is None:
        return
    # Labels don't wrap in Blender; estimate characters per line from the region width.
    width = context.region.width / max(context.preferences.view.ui_scale, 0.1)
    chars = max(20, int((width - 40) / 6.5))
    col = box.column(align=True)
    for i, rule in enumerate(_MODEL_RULES):
        if i:
            col.separator(factor=0.5)
        lines = textwrap.wrap(rule, chars - 2) or [""]
        col.label(text="• " + lines[0])
        for line in lines[1:]:
            col.label(text="   " + line)


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


class SluggiesCollisionSettings(bpy.types.PropertyGroup):
    """``Object.sluggies_collision``: makes a mesh solid for the ball in a stadium
    (exported as a ``StadiumCollision`` entry; StadiumCollisionExport.py)."""
    enabled: BoolProperty(
        name="Ball Collision",
        description=("Export this mesh as stadium collision: the ball bounces off its faces. "
                     "Collision is invisible; the mesh is also drawn only if it is a model mesh "
                     "or a custom submesh"),
        default=False,
    )
    invisible: BoolProperty(
        name="Make invisible",
        description=("Export only the collision: the mesh itself is not drawn in game. "
                     "For new submeshes and plain meshes (a plain mesh is never drawn anyway)"),
        default=False,
    )
    surface_type: EnumProperty(
        name="Surface",
        description="What the game does when the ball touches it",
        items=[(ident, label, desc) for ident, _number, label, desc in StadiumCollisionExport.SURFACE_TYPES],
        default=StadiumCollisionExport.SURFACE_TYPES[0][0],
    )
    custom_type: IntProperty(
        name="Type number", description="Surface type number (1-127)", min=1, max=127, default=2,
    )
    sides: EnumProperty(
        name="Sides",
        items=[(ident, label, desc) for ident, _value, label, desc in StadiumCollisionExport.SIDES],
        default=StadiumCollisionExport.SIDES[0][0],
    )
    territory: EnumProperty(
        name="Territory",
        items=[(ident, label, desc) for ident, _value, label, desc in StadiumCollisionExport.TERRITORIES],
        default=StadiumCollisionExport.TERRITORIES[0][0],
    )


def _draw_collision_box(layout, context):
    """The 'Stadium collision' box: the active mesh's collision settings."""
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return
    arm_obj = _find_target_armature(context)
    if arm_obj is not None and not _model_is_stadium(arm_obj) and not obj.sluggies_collision.enabled:
        return
    layout.separator()
    box = _collapsible_box(layout, context, 'collision', "Stadium collision")
    if box is None:
        return
    settings = obj.sluggies_collision
    box.prop(settings, 'enabled')
    if not settings.enabled:
        return
    col = box.column()
    col.prop(settings, 'invisible')
    col.prop(settings, 'surface_type')
    if settings.surface_type == 'CUSTOM':
        col.prop(settings, 'custom_type')
    col.prop(settings, 'sides')
    col.prop(settings, 'territory')
    note = box.column(align=True)
    note.label(text="Front = decided by face normals", icon='INFO')



class SLUGGIES_PT_tools(bpy.types.Panel):
    """Sluggies Tools sidebar panel"""
    bl_label = "Sluggies Tools"
    bl_idname = "SLUGGIES_PT_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sluggies"

    def draw(self, context):
        layout = self.layout
        _draw_rules_box(layout, context)

        layout.separator()
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
        _draw_adapt_box(layout, context)

        layout.separator()
        box = _collapsible_box(layout, context, 'textures', "Textures & Materials")
        if box is not None:
            # By id: MaterialsUI and TextureSlotsUI register them.
            box.operator("sluggies.manage_materials", text="Manage Materials", icon='MATERIAL')
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
        _draw_collision_box(layout, context)

        layout.separator()
        # By id: ModelCheckUI imports this module.
        layout.operator("sluggies.check_models", text="Check Models", icon='CHECKMARK')


def register():
    bpy.utils.register_class(SLUGGIES_OT_add_submesh)
    bpy.utils.register_class(SLUGGIES_OT_make_submesh)
    bpy.utils.register_class(SLUGGIES_OT_make_main_body)
    bpy.utils.register_class(SLUGGIES_OT_add_bone)
    bpy.utils.register_class(SLUGGIES_OT_renumber_added_bones)
    bpy.utils.register_class(SLUGGIES_OT_toggle_bone_track)
    bpy.utils.register_class(SLUGGIES_OT_reassign_bone)
    bpy.utils.register_class(SLUGGIES_OT_add_material)
    bpy.utils.register_class(SLUGGIES_OT_select_unweighted)
    bpy.utils.register_class(SLUGGIES_OT_scale_animations)
    bpy.utils.register_class(SLUGGIES_OT_adapt_step)
    bpy.utils.register_class(SLUGGIES_OT_adapt_cleanup)
    bpy.utils.register_class(SLUGGIES_OT_adapt_armature)
    # bpy.utils.register_class(SLUGGIES_OT_adapt_transform)  # hidden, see _ADAPT_STEPS
    bpy.utils.register_class(SluggiesAdaptMeshRow)
    bpy.utils.register_class(SLUGGIES_OT_adapt_meshes)
    bpy.types.WindowManager.sluggies_adapt_mesh_rows = bpy.props.CollectionProperty(type=SluggiesAdaptMeshRow)
    bpy.types.WindowManager.sluggies_adapt_mesh_armature = StringProperty()
    bpy.types.WindowManager.sluggies_adapt_mesh_names_shown = BoolProperty()
    bpy.types.WindowManager.sluggies_adapt_mesh_has_body = BoolProperty()
    bpy.types.WindowManager.sluggies_adapt_mesh_has_body_object = BoolProperty()
    bpy.utils.register_class(SluggiesConvertMaterialRow)
    bpy.utils.register_class(SLUGGIES_OT_adapt_materials)
    bpy.utils.register_class(SLUGGIES_OT_adapt_weights)
    bpy.utils.register_class(SluggiesLowpolyRow)
    bpy.utils.register_class(SLUGGIES_OT_adapt_lowpoly)
    bpy.utils.register_class(SLUGGIES_PT_tools)
    bpy.utils.register_class(SluggiesCollisionSettings)
    bpy.types.Object.sluggies_collision = bpy.props.PointerProperty(type=SluggiesCollisionSettings)
    bpy.types.Scene.sluggies_show_free_host_bones = BoolProperty(
        name="Free host bones",
        description="Show the read-only list of bones a new custom submesh could attach to",
        default=False,
    )
    for key in _COLLAPSIBLE_BOXES:
        setattr(bpy.types.Scene, f'sluggies_show_{key}_box', BoolProperty(
            name="Expanded",
            description="Show or hide this group",
            default=key not in _COLLAPSED_BY_DEFAULT,
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
    for name, handler in _UNIFORM_BONE_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler not in handlers:
            handlers.append(handler)


def unregister():
    for name, handler in _UNIFORM_BONE_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)
    _UNIFORM_BONE_CACHE.clear()
    _ENUM_ITEMS.clear()
    del bpy.types.Scene.sluggies_animation_scale
    del bpy.types.Scene.sluggies_show_free_host_bones
    for key in _COLLAPSIBLE_BOXES:
        delattr(bpy.types.Scene, f'sluggies_show_{key}_box')
    del bpy.types.Object.sluggies_collision
    bpy.utils.unregister_class(SluggiesCollisionSettings)
    bpy.utils.unregister_class(SLUGGIES_PT_tools)
    bpy.utils.unregister_class(SLUGGIES_OT_adapt_lowpoly)
    bpy.utils.unregister_class(SluggiesLowpolyRow)
    bpy.utils.unregister_class(SLUGGIES_OT_adapt_weights)
    bpy.utils.unregister_class(SLUGGIES_OT_adapt_materials)
    bpy.utils.unregister_class(SluggiesConvertMaterialRow)
    for prop in ('sluggies_adapt_mesh_rows', 'sluggies_adapt_mesh_armature', 'sluggies_adapt_mesh_names_shown',
                 'sluggies_adapt_mesh_has_body', 'sluggies_adapt_mesh_has_body_object'):
        delattr(bpy.types.WindowManager, prop)
    bpy.utils.unregister_class(SLUGGIES_OT_adapt_meshes)
    bpy.utils.unregister_class(SluggiesAdaptMeshRow)
    # bpy.utils.unregister_class(SLUGGIES_OT_adapt_transform)  # hidden, see _ADAPT_STEPS
    bpy.utils.unregister_class(SLUGGIES_OT_adapt_armature)
    bpy.utils.unregister_class(SLUGGIES_OT_adapt_cleanup)
    bpy.utils.unregister_class(SLUGGIES_OT_adapt_step)
    bpy.utils.unregister_class(SLUGGIES_OT_scale_animations)
    bpy.utils.unregister_class(SLUGGIES_OT_select_unweighted)
    bpy.utils.unregister_class(SLUGGIES_OT_add_material)
    bpy.utils.unregister_class(SLUGGIES_OT_reassign_bone)
    bpy.utils.unregister_class(SLUGGIES_OT_toggle_bone_track)
    bpy.utils.unregister_class(SLUGGIES_OT_renumber_added_bones)
    bpy.utils.unregister_class(SLUGGIES_OT_add_bone)
    bpy.utils.unregister_class(SLUGGIES_OT_make_main_body)
    bpy.utils.unregister_class(SLUGGIES_OT_make_submesh)
    bpy.utils.unregister_class(SLUGGIES_OT_add_submesh)
