"""Sluggies Tools sidebar panel (3D View > Sidebar > Sluggies Tools)."""

import json
import os
import re

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from mathutils import Matrix, Vector

from . import CustomSubmeshExport
from . import HostBones
from . import TemplateSources
from .ExportSluggies import (
    _custom_submesh_template_texture_index,
    _detect_uniform_vertex_bone_id,
    _resolve_export_texture_context,
)
from .ImportSluggies import _create_material, _set_surface_material_metadata, LEAF_TAIL_FALLBACK


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
    """Read the Phase 5 step 2 import-time snapshot off *arm_obj*'s bones."""
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


def _gather_scene_claims(context, arm_obj, bone_records):
    """The scene state layered on top of the import-time snapshot (step 2)."""
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
        if o.type == 'MESH' and o.get('SluggiesCustomSubmesh'):
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


def _ordered_host_bone_choices(context, arm_obj):
    records = _bone_records(arm_obj)
    claims = _gather_scene_claims(context, arm_obj, records)
    choices = HostBones.classify_host_bones(records, claims)
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
    `builtin:` templates (PLAN_EditRigidMeshes.md Phase 8 step 3)."""
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


def _template_wrap_modes(context, arm_obj, template_source):
    """(WrapS, WrapT) copied from the template's own imported material
    (PLAN_EditRigidMeshes.md Phase 8 step 3); `builtin:` has no donor
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
    sluggie_path = arm_obj.get('SluggieFilePath')
    if not sluggie_path:
        return None
    try:
        with open(sluggie_path, 'r') as handle:
            model = json.load(handle).get('SluggiesModel', {})
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
    """Dialog tooltip. Built-ins carry their own description (decision 9:
    what the shader mode does); donor sources just name their kind."""
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
        _template_source_materials(context, arm_obj))
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
    mesh.uv_layers.new(name='UVMap')

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
    # space right after parenting. Phase 6 exports world positions, so this
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


def _create_custom_submesh_material(obj, custom_submesh_id, image):
    surface_id = f'{custom_submesh_id}_ds0'
    mat = _create_material(f'{obj.name}_{surface_id}', 'UVMap', image, wrap_s=1)
    mat['SurfaceId'] = surface_id
    mat.id_properties_ui('SurfaceId').update(
        description="Stable draw-state identity for this custom submesh. Do not delete.")
    # Read-only for the MVP (plan step 3): the dialog only picks a template
    # source, never a shader mode directly.
    _set_surface_material_metadata(mat, {'DisplayStateId': 7, 'ShaderMode': 'Spec'})
    obj.data.materials.append(mat)
    return mat


def _write_custom_submesh_texture(sluggie_path, submesh_name):
    """A blank RGBA PNG in the model's tex/ directory (plan step 3), named
    directly after *submesh_name* (plan step 3 extension).

    A lowpoly (`_L_`) model has no TextureDescriptors and no tex/ folder of
    its own — like export, it borrows the paired highpoly model's tex/
    folder (see _resolve_export_texture_context), so the new PNG lands
    where the rest of that model's textures already live.

    Returns (image, error). Refuses to overwrite an existing file.
    """
    try:
        with open(sluggie_path, 'r') as handle:
            model = json.load(handle).get('SluggiesModel', {})
    except (OSError, ValueError):
        model = {}
    try:
        _descriptors, tex_dir, _owns_textures = _resolve_export_texture_context(sluggie_path, model)
    except ValueError as exc:
        return None, str(exc)
    file_name = f'{submesh_name}.png'
    file_path = os.path.join(tex_dir, file_name)
    if os.path.exists(file_path):
        return None, f"{file_name} already exists in tex/; refusing to overwrite"

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
    return image, None


def _rigid_mesh_kind(obj):
    """('donor', submesh_index) for a rigid (CompCount 3) donor submesh,
    ('custom', None) for a custom submesh, or (None, None) for anything else
    (PLAN_EditRigidMeshes.md Phase 6)."""
    if obj is None or obj.type != 'MESH':
        return None, None
    if obj.get('SluggiesCustomSubmesh'):
        return 'custom', None
    if obj.get('VertexBufferCompCount') == 3:
        return 'donor', _submesh_index_of(obj)
    return None, None


def _read_bone_hierarchy(sluggie_path):
    """The parsed .sluggie's BoneHierarchy list, or None if unreadable."""
    try:
        with open(sluggie_path, 'r') as handle:
            model = json.load(handle).get('SluggiesModel', {})
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


_PLACEMENT_KEEP_WORLD = (
    'KEEP_WORLD', "Keep world position",
    "The mesh stays exactly where it is and follows the new bone from now on. "
    "Requires Hammerspace Mode on export.",
)
_PLACEMENT_KEEP_OFFSET = (
    'KEEP_OFFSET', "Keep offset to bone",
    "The mesh jumps to the same offset from the new bone as it had from the old one. "
    "Its bytes don't change, so this also works with the in-place patcher.",
)


def _placement_enum_items(self, context):
    obj = context.active_object
    if obj is not None and int(obj.get('FacialShapeKeyCount', 0) or 0) > 0:
        return [_PLACEMENT_KEEP_OFFSET]
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
    """Move a rigid or custom submesh to another bone (Hammerspace only for
    Keep world position)"""
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

        ordered = _reassign_choices(context, arm_obj, obj)
        if not ordered:
            self.report({"ERROR"}, "No other bone is free to move to")
            return {"CANCELLED"}

        default_choice = _default_reassign_target(arm_obj, obj, ordered)
        if default_choice is not None:
            self.target_bone = f'bone_{default_choice.bone_id}'
        self.placement = (
            'KEEP_OFFSET' if int(obj.get('FacialShapeKeyCount', 0) or 0) > 0 else 'KEEP_WORLD')
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        arm_obj = _find_target_armature(context)
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
            self.report({"ERROR"}, f"bone_{target_bone_id} is no longer free; pick another target bone")
            return {"CANCELLED"}

        current_bone_id = _detect_uniform_vertex_bone_id(obj)

        if self.placement == 'KEEP_OFFSET':
            if current_bone_id is None:
                self.report(
                    {"ERROR"}, f"{obj.name} has no single bone_<id> vertex group to move from")
                return {"CANCELLED"}
            sluggie_path = arm_obj.get('SluggieFilePath')
            bone_hierarchy = _read_bone_hierarchy(sluggie_path) if sluggie_path else None
            if not bone_hierarchy:
                self.report({"ERROR"}, HostBones.RE_IMPORT_MESSAGE)
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


class SLUGGIES_OT_add_material(bpy.types.Operator):
    """Add a new surface (material + empty texture slot) to a rigid or
    custom submesh, from a template (PLAN_EditRigidMeshes.md 'Add material
    button (concept)'). Blender-side only for now: export support for
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
        kind, _submesh_index = _rigid_mesh_kind(obj)
        if kind is None:
            if hasattr(cls, 'poll_message_set'):
                if obj is not None and obj.get('VertexBufferCompCount') == 6:
                    cls.poll_message_set("New materials on skinned submeshes are not supported yet")
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

        self.material_name = _default_new_material_name(obj)
        template_choices = TemplateSources.build_template_source_choices(
            _template_source_materials(context, arm_obj))
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

        kind, submesh_index = _rigid_mesh_kind(obj)
        if kind is None:
            self.report({"ERROR"}, "Select a rigid or custom submesh")
            return {"CANCELLED"}

        if self.template_source == 'NONE':
            self.report({"ERROR"}, "No usable surface template found")
            return {"CANCELLED"}

        material_name = self.material_name.strip()
        name_error = _validate_material_name(material_name)
        if name_error:
            self.report({"ERROR"}, name_error)
            return {"CANCELLED"}

        owner = f'sm{submesh_index}' if kind == 'donor' else obj.get('CustomSubmeshId')
        if not owner:
            self.report({"ERROR"}, f"{obj.name} has no CustomSubmeshId; re-create it with Add submesh")
            return {"CANCELLED"}
        existing_surface_ids = [
            mat.get('SurfaceId', '') for mat in bpy.data.materials if mat.get('SurfaceId')
        ]
        surface_id = TemplateSources.next_new_surface_key(owner, existing_surface_ids)

        sluggie_path = arm_obj.get('SluggieFilePath')
        if not sluggie_path:
            self.report({"ERROR"}, "Re-import this model to enable Add material")
            return {"CANCELLED"}
        try:
            with open(sluggie_path, 'r') as handle:
                model = json.load(handle).get('SluggiesModel', {})
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, f"Could not read {sluggie_path!r}: {exc}")
            return {"CANCELLED"}
        try:
            template_texture_index = _custom_submesh_template_texture_index(model, self.template_source)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        shader_mode = _template_shader_mode(context, arm_obj, self.template_source)
        wrap_s, wrap_t = _template_wrap_modes(context, arm_obj, self.template_source)

        active_uv = obj.data.uv_layers.active
        uv_layer_name = active_uv.name if active_uv is not None else 'UVMap'
        mat = _create_material(material_name, uv_layer_name, None, wrap_s=wrap_s)

        mat['SluggiesNewSurface'] = True
        mat.id_properties_ui('SluggiesNewSurface').update(
            description="Marks a Blender-only surface created by Add material, absent from the donor.")
        mat['SurfaceId'] = surface_id
        mat.id_properties_ui('SurfaceId').update(
            description="Stable draw-state identity for this added surface. Do not delete.")
        mat['SluggiesSurfaceOwner'] = owner
        mat.id_properties_ui('SluggiesSurfaceOwner').update(
            description="The submesh (donor sm<N> or CustomSubmeshId) this surface belongs to.")
        mat['TemplateSource'] = self.template_source
        mat.id_properties_ui('TemplateSource').update(
            description="Donor or built-in template this surface's shading is cloned from.")
        mat['TemplateTextureIndex'] = template_texture_index
        mat.id_properties_ui('TemplateTextureIndex').update(
            description="Donor texture index this surface's GX format clones; never a fallback texture.")
        _set_surface_material_metadata(mat, {'DisplayStateId': 7, 'ShaderMode': shader_mode})
        mat['WrapS'] = wrap_s
        mat['WrapT'] = wrap_t
        mat.id_properties_ui('WrapS').update(description="GX wrap mode for U, copied from the template.")
        mat.id_properties_ui('WrapT').update(description="GX wrap mode for V, copied from the template.")

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
    showing the active object's current attachment and the Reassign to new
    bone / Add material buttons."""
    arm_obj = _find_target_armature(context)
    obj = context.active_object
    kind, _submesh_index = _rigid_mesh_kind(obj) if obj is not None else (None, None)

    box = layout.box()
    box.label(text="Mesh" if kind == 'custom' else "Rigid mesh")
    if arm_obj is None:
        box.label(text=_no_target_armature_message(context), icon='INFO')
        return
    if kind is not None:
        box.label(text=_rigid_mesh_attachment_label(context, arm_obj, obj))
    box.operator(SLUGGIES_OT_reassign_bone.bl_idname)
    # SLUGGIES_OT_add_material is implemented (PLAN_EditRigidMeshes.md Phase 8)
    # but hidden from the panel: export/patch support (Phases 0-4) doesn't
    # exist yet, so a material it creates can't be round-tripped today.


def _add_bone_parent_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    if arm_obj is None:
        return [('NONE', "No armature", _no_target_armature_message(context), 0)]
    items = []
    for idx, b in enumerate(arm_obj.data.bones):
        bone_id = _bone_id_from_name(b.name)
        if bone_id is None:
            continue
        tag = "new" if b.get('SluggiesUserAdded') else "donor"
        items.append((b.name, b.name, f"Parent the new bone to {b.name} ({tag})", idx))
    return items or [('NONE', "No bones", "This armature has no bones", 0)]


def _next_new_bone_creation_order(arm_obj):
    orders = [
        int(b.get('SluggiesCreationOrder', -1))
        for b in arm_obj.data.bones if b.get('SluggiesUserAdded')
    ]
    return max(orders) + 1 if orders else 0


def _unused_bone_name(arm_obj):
    """A `bone_<N>` name not already used in *arm_obj* (plan step 2). Purely
    cosmetic: the exporter reassigns real ids from SluggiesCreationOrder, not
    from this name (user contract, PLAN_AddBones.md 'Proposed user contract')."""
    n = len(arm_obj.data.bones)
    while f'bone_{n}' in arm_obj.data.bones:
        n += 1
    return f'bone_{n}'


def _create_added_bone(context, arm_obj, parent_bone_name):
    """Create one inert leaf bone parented to *parent_bone_name* (plan step 2),
    following the user contract: no GeoId/track, self-mirrored role 3,
    InheritTransform true, DrawPriority 0. The SRT type byte is not set here:
    it is a component-presence mask over the bone's own rotation/translation,
    so the exporter derives it from the values it writes (PLAN_AddBones.md
    F11) rather than inheriting a parent's mask that may not fit."""
    prev_active = context.view_layer.objects.active
    prev_mode = context.object.mode if context.object is not None else 'OBJECT'
    context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        edit_bones = arm_obj.data.edit_bones
        parent_eb = edit_bones.get(parent_bone_name)
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
        tail_offset = (
            local_dir.normalized() * LEAF_TAIL_FALLBACK
            if local_dir.length > 1e-9 else Vector((0.0, 0.0, LEAF_TAIL_FALLBACK))
        )
        # Originates at the parent's tail, not floating off to the side, so
        # chained bones visually continue the parent like a real skeleton.
        new_eb.head = parent_eb.tail
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
    new_bone['track_id'] = 0xFFFF

    context.view_layer.objects.active = prev_active
    return new_bone


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
    attach to once the donor's own free bones are exhausted (PLAN_AddBones.md,
    Hammerspace Mode required on export)."""
    bl_idname = "sluggies.add_bone"
    bl_label = "Add Bone"
    bl_description = "Add a new leaf bone parented to the chosen bone (Hammerspace only)"
    bl_options = {"UNDO"}

    parent_bone: EnumProperty(
        name="Parent bone",
        description="Bone the new bone is rigidly parented to",
        items=_add_bone_parent_enum_items,
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
            if self.parent_bone == 'NONE' or self.parent_bone not in arm_obj.data.bones:
                self.report({"ERROR"}, f"Parent bone {self.parent_bone!r} no longer exists")
                return {"CANCELLED"}

            new_bone = _create_added_bone(context, arm_obj, self.parent_bone)
            self.report({"INFO"}, f"Added {new_bone.name}, parented to {self.parent_bone}")
            return {"FINISHED"}
        finally:
            _end_bone_name_display(self, arm_obj)

    def cancel(self, context):
        _end_bone_name_display(self, _find_target_armature(context))


class SLUGGIES_OT_add_submesh(bpy.types.Operator):
    """Add a custom rigid submesh, attached to a free bone (Hammerspace only)"""
    bl_idname = "sluggies.add_submesh"
    bl_label = "Add Submesh"
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
        if 'SluggieFilePath' not in arm_obj.keys() or not _bone_metadata_is_current(arm_obj):
            self.report({"ERROR"}, HostBones.RE_IMPORT_MESSAGE)
            return {"CANCELLED"}

        ordered = _ordered_host_bone_choices(context, arm_obj)
        if not ordered:
            self.report({"ERROR"}, "Every bone already owns a mesh or is claimed")
            return {"CANCELLED"}

        self.submesh_name = _default_submesh_name(context)
        default_choice = _default_host_bone_choice(context, arm_obj, ordered)
        if default_choice is not None:
            self.host_bone = f'bone_{default_choice.bone_id}'
        template_choices = TemplateSources.build_template_source_choices(
            _template_source_materials(context, arm_obj))
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

            # Export-time re-check territory (Phase 6) aside, re-run the free-bone
            # classification once more here: the props dialog may have stayed
            # open while another retarget or custom submesh claimed this bone.
            ordered = _ordered_host_bone_choices(context, arm_obj)
            if not any(c.bone_id == bone_id for c in ordered):
                self.report({"ERROR"}, f"bone_{bone_id} is no longer free; pick another host bone")
                return {"CANCELLED"}

            sluggie_path = arm_obj.get('SluggieFilePath')
            if not sluggie_path:
                self.report({"ERROR"}, "Re-import this model to enable Add submesh")
                return {"CANCELLED"}

            custom_submesh_id = _next_custom_submesh_id(context)
            image, error = _write_custom_submesh_texture(sluggie_path, submesh_name)
            if error:
                self.report({"ERROR"}, error)
                return {"CANCELLED"}

            obj = _create_custom_submesh_cube(
                context, arm_obj, bone_id, custom_submesh_id, submesh_name, self.template_source)
            _create_custom_submesh_material(obj, custom_submesh_id, image)
            _select_new_object(context, obj)

            self.report({"INFO"}, f"Added {obj.name} on bone_{bone_id} ({self.template_source})")
            return {"FINISHED"}
        finally:
            _end_bone_name_display(self, arm_obj)

    def cancel(self, context):
        _end_bone_name_display(self, _find_target_armature(context))


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


class SLUGGIES_PT_tools(bpy.types.Panel):
    """Sluggies Tools sidebar panel"""
    bl_label = "Sluggies Tools"
    bl_idname = "SLUGGIES_PT_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sluggies"

    def draw(self, context):
        layout = self.layout
        layout.operator(SLUGGIES_OT_add_submesh.bl_idname)
        layout.operator(SLUGGIES_OT_add_bone.bl_idname)

        layout.separator()
        _draw_rigid_mesh_box(layout, context)

        layout.separator()
        _draw_free_host_bones(layout, context)


def register():
    bpy.utils.register_class(SLUGGIES_OT_add_submesh)
    bpy.utils.register_class(SLUGGIES_OT_add_bone)
    bpy.utils.register_class(SLUGGIES_OT_reassign_bone)
    bpy.utils.register_class(SLUGGIES_OT_add_material)
    bpy.utils.register_class(SLUGGIES_PT_tools)
    bpy.types.Scene.sluggies_show_free_host_bones = BoolProperty(
        name="Free host bones",
        description="Show the read-only list of bones a new custom submesh could attach to",
        default=False,
    )


def unregister():
    del bpy.types.Scene.sluggies_show_free_host_bones
    bpy.utils.unregister_class(SLUGGIES_PT_tools)
    bpy.utils.unregister_class(SLUGGIES_OT_add_material)
    bpy.utils.unregister_class(SLUGGIES_OT_reassign_bone)
    bpy.utils.unregister_class(SLUGGIES_OT_add_bone)
    bpy.utils.unregister_class(SLUGGIES_OT_add_submesh)
