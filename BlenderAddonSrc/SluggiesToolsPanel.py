"""Sluggies Tools sidebar panel (3D View > Sidebar > Sluggies Tools): the panel, its boxes, Select
Unweighted, the Adapt Model step launcher and the collision settings, and ``register`` / ``unregister``
for every tools operator, property and handler.

The operators and their helpers live in the modules imported below, in import order: ``SceneModels``,
``HostBoneScene``, ``SurfacePlans``, ``EnumItems``, ``AdaptSteps``, then the operator modules
(``SubmeshOps``, ``BoneOps``, ``BodyOps``, ``AnimScaleOp``, ``AdaptAssignUI``, ``AdaptMeshesUI``,
``AdaptMaterialsUI``, ``AdaptWeightsUI``, ``AdaptLowpolyUI``, ``ExportHighLow``). None of them imports
this module; other add-on modules import a helper from the module that defines it.
"""

import functools
import textwrap

import bmesh
import bpy
from bpy.props import (
    BoolProperty, EnumProperty, FloatVectorProperty, IntProperty, StringProperty,
)

from .AnmExport import SLUGGIES_OT_export_anm_group
from . import HostBones
from . import SluggiePath
from . import StadiumCollisionExport
from .ExportSluggies import SLUGGIES_OT_export
from .ImportSluggies import SLUGGIES_OT_import
from .ImportAnm import SLUGGIES_OT_import_anm

from .SceneModels import (
    _ANY_ARMATURE_CACHE,
    _DRAW_CACHE,
    _add_material_mesh_kind,
    _any_sluggies_armature_imported,
    _bone_metadata_is_current,
    _clear_sluggies_object_props,
    _find_target_armature,
    _high_low_pair,
    _is_donor_mesh,
    _mesh_armature,
    _model_is_stadium,
    _no_target_armature_message,
    _rigid_mesh_kind,
    _selected_sluggies_armature,
)
from .HostBoneScene import (
    _HOST_BONE_STATUS_TAGS,
    _UNIFORM_BONE_CACHE,
    _UNIFORM_BONE_HANDLERS,
    _ordered_host_bone_choices,
    _rigid_mesh_attachment_label,
)
from .EnumItems import (
    _ENUM_ITEMS,
)
from .AdaptSteps import (
    _ADAPT_STEPS,
)
from .SubmeshOps import (
    SLUGGIES_OT_add_material,
    SLUGGIES_OT_add_submesh,
    SLUGGIES_OT_make_submesh,
    SLUGGIES_OT_reassign_bone,
)
from .BoneOps import (
    SLUGGIES_OT_add_bone,
    SLUGGIES_OT_renumber_added_bones,
    SLUGGIES_OT_toggle_bone_track,
)
from .BodyOps import (
    SLUGGIES_OT_make_main_body,
)
from .AnimScaleOp import (
    SLUGGIES_OT_scale_animations,
)
from .AdaptAssignUI import (
    SLUGGIES_OT_adapt_armature,
    SLUGGIES_OT_adapt_cleanup,
    _end_assign_preview,
    _run_assign_preview,
)
from .AdaptMeshesUI import (
    SLUGGIES_OT_adapt_meshes,
    SluggiesAdaptMeshRow,
    _REOPEN_WINDOW,
    _reopen_adapt_meshes,
)
from .AdaptMaterialsUI import (
    SLUGGIES_OT_adapt_materials,
    SluggiesConvertMaterialRow,
)
from .AdaptWeightsUI import (
    SLUGGIES_OT_adapt_weights,
)
from .AdaptLowpolyUI import (
    SLUGGIES_OT_adapt_lowpoly,
    SluggiesLowpolyRow,
)
from .ExportHighLow import (
    SLUGGIES_OT_export_high_low,
)


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


def _clear_props_targets(context):
    """The meshes Clear All Sluggies Properties works on: the selected ones and the active one."""
    objects = [o for o in context.selected_objects if o.type == 'MESH']
    active = context.active_object
    if active is not None and active.type == 'MESH' and active not in objects:
        objects.insert(0, active)
    return objects


class SLUGGIES_OT_clear_sluggies_props(bpy.types.Operator):
    """Turn the selected meshes back into plain meshes"""
    bl_idname = "sluggies.clear_sluggies_props"
    bl_label = "Clear All Sluggies Properties"
    bl_description = ("Remove every Sluggies custom property from the selected meshes (the link to an "
                      "imported submesh, a custom submesh's identity, the backup mark) and switch their "
                      "ball collision off, so they are plain meshes again: Join or Replace Meshes and "
                      "Make into Submesh take them like any other mesh. Materials, vertex groups, "
                      "parent and modifiers stay as they are")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Switch to Object Mode")
            return False
        if not _clear_props_targets(context):
            cls.poll_message_set("Select a mesh")
            return False
        return True

    def invoke(self, context, event):
        names = [o.name for o in _clear_props_targets(context)]
        shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
        return context.window_manager.invoke_confirm(
            self, event, title="Clear all Sluggies properties?",
            message=(f"{shown}: the Sluggies custom properties are removed and ball collision is "
                     "switched off. The mesh is no longer an imported or custom submesh and is not "
                     "exported until it is made part of a model again."),
            confirm_text="OK", icon='WARNING')

    def execute(self, context):
        cleared, plain = [], []
        for obj in _clear_props_targets(context):
            removed, was_solid = _clear_sluggies_object_props(obj)
            (cleared if removed or was_solid else plain).append((obj.name, len(removed)))
        _UNIFORM_BONE_CACHE.clear()
        _DRAW_CACHE.clear()
        if not cleared:
            self.report({"INFO"}, "Nothing to clear: no Sluggies properties on "
                        + ", ".join(name for name, _count in plain))
            return {"CANCELLED"}
        self.report({"INFO"}, "Cleared " + ", ".join(
            f"{name} ({count} propert{'y' if count == 1 else 'ies'})" if count else f"{name} (ball collision off)"
            for name, count in cleared))
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
        box.operator(SLUGGIES_OT_clear_sluggies_props.bl_idname)


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
    'EXPORT': SLUGGIES_OT_export_high_low.bl_idname,
}


def _draw_adapt_box(layout, context):
    """The 'Adapt' box: one button per _ADAPT_STEPS entry, numbered in order."""
    box = _collapsible_box(layout, context, 'adapt', "Adapt")
    if box is None:
        return
    col = box.column(align=True)
    if not bpy.data.filepath:  # never saved: no .blend to save to yet
        col.operator("wm.save_as_mainfile", text="0. Create a Blend file!", icon='FILE_BLEND')
    any_selected = bool(context.selected_objects)
    for n, (ident, label, _desc) in enumerate(_ADAPT_STEPS, 1):
        if ident == 'EXPORT':
            arm_obj = _selected_sluggies_armature(context)
            if arm_obj is None or _high_low_pair(context, arm_obj)[0] is None:
                continue
        if any_selected:
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
    for i, lines in enumerate(_wrapped_rules(chars)):
        if i:
            col.separator(factor=0.5)
        col.label(text="• " + lines[0])
        for line in lines[1:]:
            col.label(text="   " + line)


@functools.lru_cache(maxsize=8)
def _wrapped_rules(chars):
    """_MODEL_RULES wrapped to *chars* per line (the sidebar width changes rarely)."""
    return tuple(tuple(textwrap.wrap(rule, chars - 2) or [""]) for rule in _MODEL_RULES)


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
    bpy.utils.register_class(SLUGGIES_OT_clear_sluggies_props)
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
    bpy.utils.register_class(SLUGGIES_OT_export_high_low)
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
    for timer in (_run_assign_preview, _reopen_adapt_meshes):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    _end_assign_preview()
    _REOPEN_WINDOW[0] = None
    for name, handler in _UNIFORM_BONE_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)
    _UNIFORM_BONE_CACHE.clear()
    _ANY_ARMATURE_CACHE.clear()
    _DRAW_CACHE.clear()
    _ENUM_ITEMS.clear()
    del bpy.types.Scene.sluggies_animation_scale
    del bpy.types.Scene.sluggies_show_free_host_bones
    for key in _COLLAPSIBLE_BOXES:
        delattr(bpy.types.Scene, f'sluggies_show_{key}_box')
    del bpy.types.Object.sluggies_collision
    bpy.utils.unregister_class(SluggiesCollisionSettings)
    bpy.utils.unregister_class(SLUGGIES_PT_tools)
    bpy.utils.unregister_class(SLUGGIES_OT_export_high_low)
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
    bpy.utils.unregister_class(SLUGGIES_OT_clear_sluggies_props)
    bpy.utils.unregister_class(SLUGGIES_OT_select_unweighted)
    bpy.utils.unregister_class(SLUGGIES_OT_add_material)
    bpy.utils.unregister_class(SLUGGIES_OT_reassign_bone)
    bpy.utils.unregister_class(SLUGGIES_OT_toggle_bone_track)
    bpy.utils.unregister_class(SLUGGIES_OT_renumber_added_bones)
    bpy.utils.unregister_class(SLUGGIES_OT_add_bone)
    bpy.utils.unregister_class(SLUGGIES_OT_make_main_body)
    bpy.utils.unregister_class(SLUGGIES_OT_make_submesh)
    bpy.utils.unregister_class(SLUGGIES_OT_add_submesh)
