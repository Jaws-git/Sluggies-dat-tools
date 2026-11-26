"""Tools operators on submeshes: Reassign to Bone, Add Material, Add Submesh and Make Submesh, with the
helpers other operators reuse (``_convert_to_custom_submesh``, ``_ensure_armature_modifier``,
``_parent_keeping_world``). Registered by ``SluggiesToolsPanel.register``.

Never imports ``SluggiesToolsPanel``.
"""

import bmesh
import bpy
from bpy.props import (
    BoolProperty, EnumProperty, StringProperty,
)
from mathutils import Matrix, Vector
from . import CustomSubmeshExport
from . import HostBones
from . import ModelCheckCore
from . import SluggiePath
from . import TemplateSources
from .ExportTextures import (
    _custom_submesh_template_texture_index,
)
from .ExportBones import _detect_uniform_vertex_bone_id
from .SurfaceMaterials import (
    _create_material,
)
from .SceneModels import (
    _INVALID_SUBMESH_NAME_CHARS_RE,
    _SUBMESH_NAME_RE,
    _SURFACE_ID_RE,
    _add_material_mesh_kind,
    _any_sluggies_armature_imported,
    _bone_id_from_name,
    _bone_metadata_is_current,
    _donor_copy_original,
    _draw_cached,
    _find_target_armature,
    _is_donor_mesh,
    _model_is_stadium,
    _no_target_armature_message,
    _read_bone_hierarchy,
    _reports_failure,
    _rigid_mesh_kind,
    _sluggies_armatures,
    _strip_donor_object_props,
)
from .HostBoneScene import (
    _UNIFORM_BONE_CACHE,
    _begin_bone_name_display,
    _default_host_bone_choice,
    _default_reassign_target,
    _end_bone_name_display,
    _object_world_bbox_center,
    _ordered_host_bone_choices,
    _preview_active_bone,
    _reassign_choices,
    _renumber_added_bones_reporting,
    _update_host_bone_preview,
)
from .SurfacePlans import (
    _apply_new_surface_plan,
    _create_custom_submesh_material,
    _default_new_material_name,
    _low_poly_texture_warning,
    _make_submesh_materials,
    _model_tex_dir,
    _new_surface_plan,
    _template_shader_mode,
    _template_source_materials,
    _template_specular_strength,
    _validate_material_name,
    _write_custom_submesh_texture,
)
from .EnumItems import (
    _bone_choice_items,
    _cached_enum_items,
    _drop_enum_items,
    _host_bone_enum_items,
    _host_bone_items,
    _placement_enum_items,
    _pointer,
    _reassign_target_bone_enum_items,
    _reassign_target_bone_items,
    _set_enum_items,
    _template_source_enum_items,
)


_CUSTOM_SUBMESH_CUBE_HALF_EXTENT = 0.2


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


# Cube net cells (column, row) of a 4 x 3 grid of 0.25 squares, by face
# normal: the four sides in a row, top above and bottom below the -Y side.
_CUBE_NET_CELLS = {
    (-1, 0, 0): (0, 1), (0, -1, 0): (1, 1), (1, 0, 0): (2, 1), (0, 1, 0): (3, 1),
    (0, 0, 1): (1, 2), (0, 0, -1): (1, 0),
}


_CUBE_NET_MARGIN = 0.01


def _unwrap_cube_net(bm, uv_layer):
    """Lay the six faces of a bmesh cube out as a cross-shaped net in
    *uv_layer* (seams as _mark_cube_uv_seams set them), each face unmirrored
    (u x v = its normal). Pure bmesh: no operator, selection or mode switch."""
    cell = 0.25
    inner = cell - 2 * _CUBE_NET_MARGIN
    for face in bm.faces:
        normal = tuple(int(round(c)) for c in face.normal)
        column, row = _CUBE_NET_CELLS[normal]
        n = Vector(normal)
        v_axis = Vector((0, 0, 1)) if n.z == 0 else Vector((0, n.z, 0))
        u_axis = v_axis.cross(n)
        us = [loop.vert.co.dot(u_axis) for loop in face.loops]
        vs = [loop.vert.co.dot(v_axis) for loop in face.loops]
        u_min, u_span = min(us), (max(us) - min(us)) or 1.0
        v_min, v_span = min(vs), (max(vs) - min(vs)) or 1.0
        for loop, u, v in zip(face.loops, us, vs):
            loop[uv_layer].uv = (column * cell + _CUBE_NET_MARGIN + (u - u_min) / u_span * inner,
                                 row * cell + _CUBE_NET_MARGIN + (v - v_min) / v_span * inner)


def _create_custom_submesh_cube(context, arm_obj, bone_id, custom_submesh_id, submesh_name, template_source):
    """A 0.1-unit-half-extent cube at the host bone's origin, weighted 1.0 to
    bone_<bone_id> and parented to *arm_obj* like an imported rigid submesh
    (plan step 3), named *submesh_name* (plan step 3 extension: user-editable
    submesh name, defaulting to CustomSubmesh_<n>)."""
    obj_name = submesh_name
    mesh = bpy.data.meshes.new(obj_name)
    bm = bmesh.new()
    try:
        bmesh.ops.create_cube(bm, size=_CUSTOM_SUBMESH_CUBE_HALF_EXTENT * 2)
        _mark_cube_uv_seams(bm)
        _unwrap_cube_net(bm, bm.loops.layers.uv.new('uv0'))
        bm.to_mesh(mesh)
    finally:
        bm.free()

    obj = bpy.data.objects.new(obj_name, mesh)
    context.collection.objects.link(obj)

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


def _collection_in_view_layer(context):
    """Whether context.collection (where new objects are linked) is part of
    the view layer and not excluded: an object linked into an excluded
    collection cannot be selected or made active."""
    target = context.collection
    stack = [context.view_layer.layer_collection]
    while stack:
        layer = stack.pop()
        if layer.collection == target:
            return not layer.exclude
        stack.extend(child for child in layer.children if not child.exclude)
    return False


def _select_new_object(context, obj):
    """Switch to Object Mode and make *obj* the sole selected/active object,
    so the user lands looking at the cube they just created."""
    if context.object is not None and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _update_reassign_target_preview(self, context):
    _preview_active_bone(_find_target_armature(context), self.target_bone)


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
            cls.poll_message_set("Switch to Object Mode")
            return False
        obj = context.active_object
        arm_obj = _find_target_armature(context)
        if arm_obj is None:
            cls.poll_message_set(_no_target_armature_message(context))
            return False
        if not _bone_metadata_is_current(arm_obj):
            cls.poll_message_set(HostBones.RE_IMPORT_MESSAGE)
            return False
        kind, _submesh_index = _rigid_mesh_kind(obj)
        if kind is None:
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
        _renumber_added_bones_reporting(self, arm_obj, undo_step=True)

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
                try:
                    bind_matrices = CustomSubmeshExport.bone_absolute_matrices(bone_hierarchy)
                except ValueError as exc:
                    self.report({"ERROR"}, f"BoneHierarchy of the source .sluggie: {exc}")
                    return {"CANCELLED"}
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
            cls.poll_message_set("Switch to Object Mode or Edit Mode")
            return False
        obj = context.active_object
        arm_obj = _find_target_armature(context)
        if arm_obj is None:
            cls.poll_message_set(_no_target_armature_message(context))
            return False
        if not _bone_metadata_is_current(arm_obj):
            cls.poll_message_set(HostBones.RE_IMPORT_MESSAGE)
            return False
        kind, _submesh_index = _add_material_mesh_kind(obj)
        if kind is None:
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
            cls.poll_message_set("Switch to Object Mode")
            return False
        # Only an armature or one of its child objects names the model.
        if _find_target_armature(context) is None:
            cls.poll_message_set(_no_target_armature_message(context))
            return False
        if not _collection_in_view_layer(context):
            cls.poll_message_set("The active collection is excluded from the view layer: "
                                 "pick another one in the Outliner")
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
        _renumber_added_bones_reporting(self, arm_obj, undo_step=True)

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

            # The cube first, the PNG last: a failure removes the cube, and
            # no blank PNG is left in tex/ for a submesh that does not exist.
            custom_submesh_id = _next_custom_submesh_id(context)
            obj = _create_custom_submesh_cube(
                context, arm_obj, bone_id, custom_submesh_id, submesh_name, self.template_source)

            def remove_cube():
                mesh = obj.data
                bpy.data.objects.remove(obj)
                bpy.data.meshes.remove(mesh)
                _UNIFORM_BONE_CACHE.clear()

            image, texture_reused, error = _write_custom_submesh_texture(sluggie_path, submesh_name)
            if error:
                remove_cube()
                self.report({"ERROR"}, error)
                return {"CANCELLED"}
            try:
                _create_custom_submesh_material(
                    obj, custom_submesh_id, image,
                    _template_shader_mode(context, arm_obj, self.template_source),
                    _template_specular_strength(context, arm_obj, self.template_source))
            except BaseException:
                remove_cube()   # the PNG stays: it is a valid file and reused on the next try
                raise
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


# The name Make Submesh gives a mesh's only UV map.
_MADE_SUBMESH_UV_NAME = 'UV0'


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
    _preview_active_bone(_make_submesh_armature(self), self.host_bone)


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


def _is_imported_original(obj):
    """An imported mesh Make Submesh must leave alone: the only object of its
    submesh. A copy of one (see _donor_copy_original) can become a submesh of
    its own. Asked on every redraw, so kept until the scene changes."""
    return _draw_cached(
        ('imported_original', obj.as_pointer()),
        lambda: _is_donor_mesh(obj) and _donor_copy_original(obj) is None)


def _drop_unused_material_slots(obj):
    """Remove the material slots of *obj* no face uses. A part separated from
    an imported mesh keeps every slot of the whole mesh (76 on a Mario
    Stadium mesh), and each slot would become a surface of the new submesh.
    Returns how many were removed."""
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    mesh = obj.data
    face_slots = [0] * len(mesh.polygons)
    mesh.polygons.foreach_get('material_index', face_slots)
    used = sorted(set(face_slots))
    slot_materials = [slot.material for slot in obj.material_slots]
    if not slot_materials or len(used) == len(slot_materials) or not used:
        return 0
    kept = [slot_materials[index] for index in used if index < len(slot_materials)]
    if len(kept) != len(used):          # a face on a slot that does not exist: leave it to the compaction
        return 0
    remap = {old: new for new, old in enumerate(used)}
    mesh.materials.clear()
    for mat in kept:
        mesh.materials.append(mat)
    mesh.polygons.foreach_set('material_index', [remap[index] for index in face_slots])
    mesh.update()
    return len(slot_materials) - len(kept)


def _donor_copy_template_of(model, stadium):
    """template_of for _make_submesh_materials on a copy of an imported rigid
    mesh: each imported surface keeps its look as `rigid:<SurfaceId>`, an
    Add-material surface keeps its template, and whatever the model cannot
    resolve for a custom submesh gets the default."""
    default = ModelCheckCore.default_template(stadium)

    def template_of(mat):
        surface_id = str(mat.get('SurfaceId') or '')
        if mat.get('SluggiesNewSurface'):
            candidate = str(mat.get('TemplateSource') or '')
        elif _SURFACE_ID_RE.match(surface_id):
            candidate = f'rigid:{surface_id}'
        else:
            candidate = ''
        if candidate:
            try:
                _custom_submesh_template_texture_index(model, candidate)
                CustomSubmeshExport.attribute_plan(model, candidate)
                return candidate
            except (ValueError, KeyError):
                pass
        return default

    return template_of


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
            cls.poll_message_set("Switch to Object Mode")
            return False
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            cls.poll_message_set("Make a mesh object active")
            return False
        if _is_imported_original(obj):
            cls.poll_message_set(
                "This mesh is an imported part of the model (a copy of it can become a new submesh)")
            return False
        if not _any_sluggies_armature_imported(context):
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
                _renumber_added_bones_reporting(self, armature, undo_step=True)
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
        original = _donor_copy_original(context.active_object)
        if original is not None:
            col.label(text=f"Copy of {original.name}: becomes a new submesh,", icon='DUPLICATE')
            col.label(text="its materials keep their look where possible.")
        col.label(text="All vertex groups are replaced by the host bone.", icon='INFO')
        col.label(text="Materials get the default template; ones without")
        col.label(text="a texture get a PNG in their colour in tex/.")

    @_reports_failure
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
        original = _donor_copy_original(obj)
        if obj is None or obj.type != 'MESH' or (_is_donor_mesh(obj) and original is None):
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

        # A copy of an imported mesh stops being that submesh: its twin keeps
        # it, and the copy's surfaces keep the imported look where they can.
        template_of = None
        dropped_slots = 0
        if original is not None:
            template_of = _donor_copy_template_of(model, _model_is_stadium(arm_obj))
            _strip_donor_object_props(obj)
            dropped_slots = _drop_unused_material_slots(obj)
        custom_submesh_id, notes = _convert_to_custom_submesh(
            context, obj, arm_obj, bone_id, template_source, tex_dir,
            str(obj.get('CustomSubmeshId') or '') if was_custom else '', template_of=template_of)
        _select_new_object(context, obj)
        message = (f"{obj.name} is now custom submesh {custom_submesh_id} on bone_{bone_id} "
                   f"({obj.get('TemplateSource') or template_source})")
        if original is not None:
            message += f"; {original.name} stays the imported submesh"
            if dropped_slots:
                message += f"; removed {dropped_slots} material slot(s) no face uses"
        self.report({"INFO"}, "; ".join([message] + notes))
        return {"FINISHED"}


def _convert_to_custom_submesh(context, obj, arm_obj, bone_id, template_source, tex_dir,
                               custom_submesh_id='', template_of=None, set_up_materials=True,
                               ensure_textures=True):
    """Make *obj* a custom rigid submesh of *arm_obj* on *bone_id*, where it
    is: one full-weight vertex group, an Armature modifier, the armature as
    parent and its materials as its surfaces (see _make_submesh_materials for
    *template_of* and *ensure_textures*; skipped without *set_up_materials*,
    which the Adapt box leaves to Convert Materials). *custom_submesh_id* is
    kept unless empty or used by another object. Returns (CustomSubmeshId, notes)."""
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

    # The materials first: this is the step that writes files (a PNG per surface without one), and a
    # failed write must not find the mesh already stripped of its vertex groups and re-parented.
    material_notes = []
    if set_up_materials:
        try:
            material_notes = _make_submesh_materials(context, obj, arm_obj, tex_dir, template_of, ensure_textures)
        except ValueError as exc:
            material_notes = [f"materials not finished: {exc}"]

    obj.vertex_groups.clear()
    group = obj.vertex_groups.new(name=f'bone_{bone_id}')
    group.add(list(range(len(obj.data.vertices))), 1.0, 'REPLACE')
    _UNIFORM_BONE_CACHE.clear()

    _ensure_armature_modifier(obj, arm_obj)

    _parent_keeping_world(obj, arm_obj)

    uv_layers = obj.data.uv_layers
    if not uv_layers:
        notes.append("no UV map: add one and unwrap it before exporting")
    notes += material_notes
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
