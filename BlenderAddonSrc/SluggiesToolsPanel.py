"""Sluggies Tools sidebar panel (3D View > Sidebar > Sluggies Tools)."""

import json
import os
import re

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import HostBones
from . import TemplateSources
from .ExportSluggies import _detect_uniform_vertex_bone_id, _resolve_export_texture_context
from .ImportSluggies import _create_material, _set_surface_material_metadata


_BONE_NAME_RE = re.compile(r'^bone_(\d+)$')
_SURFACE_ID_RE = re.compile(r'^sm(\d+)_ds\d+$')
_CUSTOM_SUBMESH_ID_RE = re.compile(r'^custom(\d+)$')
_SUBMESH_NAME_RE = re.compile(r'^CustomSubmesh_(\d+)$')
_INVALID_SUBMESH_NAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_CUSTOM_SUBMESH_CUBE_HALF_EXTENT = 0.1
_CUSTOM_SUBMESH_TEXTURE_SIZE = 64

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
            if mat is None or not mat.get('SurfaceId'):
                continue
            materials.append(TemplateSources.TemplateSourceMaterial(
                surface_id=mat['SurfaceId'],
                comp_count=int(comp_count),
                shader_mode=mat.get('ShaderMode', ''),
            ))
    return materials


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


def _template_source_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    if arm_obj is None:
        return [('NONE', "None", _no_target_armature_message(context), 0)]
    choices = TemplateSources.build_template_source_choices(
        _template_source_materials(context, arm_obj))
    if not choices:
        return [('NONE', "None", "No usable surface template found", 0)]
    return [
        (c.template_source, c.template_source, f"{c.kind} template source", idx)
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
        name="Template source",
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
        if 'SluggieFilePath' not in arm_obj.keys():
            self.report({"ERROR"}, "Re-import this model to enable Add submesh")
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
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        arm_obj = _find_target_armature(context)
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

        layout.separator()
        _draw_free_host_bones(layout, context)


def register():
    bpy.utils.register_class(SLUGGIES_OT_add_submesh)
    bpy.utils.register_class(SLUGGIES_PT_tools)
    bpy.types.Scene.sluggies_show_free_host_bones = BoolProperty(
        name="Free host bones",
        description="Show the read-only list of bones a new custom submesh could attach to",
        default=False,
    )


def unregister():
    del bpy.types.Scene.sluggies_show_free_host_bones
    bpy.utils.unregister_class(SLUGGIES_PT_tools)
    bpy.utils.unregister_class(SLUGGIES_OT_add_submesh)
