"""Adapt Model, step Join or Replace Meshes: the row property group, the inventory and status of the
armature's meshes, and the operator. Registered by ``SluggiesToolsPanel.register`` (which also owns the
``WindowManager`` properties of the dialog).

Never imports ``SluggiesToolsPanel``.
"""

import bpy
from bpy.props import (
    BoolProperty, EnumProperty,
)
from . import AdaptCleanup
from . import HostBones
from . import ModelCheckCore
from . import SluggiePath
from .ExportTextures import (
    _custom_submesh_template_texture_index,
)
from .SceneModels import (
    _body_objects,
    _body_submesh,
    _bone_id_from_name,
    _bone_metadata_is_current,
    _draw_cached,
    _model_is_stadium,
    _owning_armature,
    _reports_failure,
    _selected_sluggies_armature,
)
from .HostBoneScene import (
    _ordered_host_bone_choices,
    _preview_active_bone,
    _renumber_added_bones_reporting,
    _uniform_bone_id,
)
from .SurfacePlans import (
    _model_tex_dir,
)
from .EnumItems import (
    _cached_enum_items,
    _drop_enum_items,
    _host_bone_items,
    _pointer,
    _set_enum_items,
)
from .AdaptSteps import (
    _ADAPT_STEPS,
    _report_adapt_summary,
)
from .SubmeshOps import (
    _convert_to_custom_submesh,
)
from .BodyOps import (
    _BACKUP_BODY_NOTE,
    _carry_shape_key_names,
    _dominant_bone_choice,
    _make_body,
    _set_aside_old_bodies,
    _use_body_uv_name,
)


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
    _preview_active_bone(_adapt_meshes_armature(context), self.bone)


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


def _join_into_body(context, body_obj, objs):
    """Merge the meshes *objs* into the skinned body object *body_obj*: their
    render UV maps join the body's channel 0, their vertex groups the skin
    weights. Their materials stay plain until Convert Materials. One join for
    all of them, so a refused join leaves none of them merged. Returns notes."""
    notes = []
    objs = list(objs)
    if not objs:
        return notes
    for obj in objs:
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
    with context.temp_override(active_object=body_obj, object=body_obj, selected_objects=[body_obj, *objs],
                               selected_editable_objects=[body_obj, *objs]):
        bpy.ops.object.join()
    return notes


# The window Join or Replace was confirmed in, for _reopen_adapt_meshes.
_REOPEN_WINDOW = [None]


def _reopen_adapt_meshes():
    """Timer: show the Join or Replace dialog again with its rows as they
    were, in the window it was open in (else the first one)."""
    wm = bpy.context.window_manager
    window = _REOPEN_WINDOW[0]
    _REOPEN_WINDOW[0] = None
    windows = list(wm.windows)
    if window not in windows:
        window = windows[0] if windows else None
    if window is not None:
        try:
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.ops.sluggies.adapt_meshes('INVOKE_DEFAULT', reopen=True)
        except RuntimeError as exc:     # its poll refused: the armature or the mode changed
            print(f"Sluggies: Join or Replace could not reopen: {exc}")
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
            cls.poll_message_set("Switch to Object Mode")
            return False
        if _selected_sluggies_armature(context) is None:
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

        _renumber_added_bones_reporting(self, arm_obj, undo_step=True)
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

        sluggies = _draw_cached(
            ('adapt_meshes', context.view_layer.as_pointer(), arm_obj.as_pointer()),
            lambda: [(obj.name, _adapt_mesh_status(obj))
                     for obj in _adapt_mesh_inventory(context, arm_obj)[0]])
        box = layout.box()
        box.label(text="Sluggies meshes")
        for name, status in sluggies:
            split = box.split(factor=0.5)
            split.label(text=name, icon='MESH_DATA')
            split.label(text=status)
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

    @_reports_failure
    def execute(self, context):
        wm = context.window_manager
        faulty, messages = _adapt_mesh_faults(wm)
        if messages:
            # A props dialog cannot disable its OK button: show it again.
            self.report({"ERROR"}, messages[0])
            _REOPEN_WINDOW[0] = context.window
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
            # The summary's closing "Next: weight..." line covers the weights.
            notes += _make_body(new_body, arm_obj, model, None, check_weights=False)
            notes += _carry_shape_key_names(old_bodies, new_body)
            lines.append(f"{new_body.name} is now the main body")
            backups = _set_aside_old_bodies(old_bodies)
            if backups:
                lines.append(f"Set aside (off the armature, not exported): {', '.join(backups)}")
        if joins:
            names = ", ".join(obj.name for obj in joins)
            notes += _join_into_body(context, body_obj, joins)
            lines.append(f"Joined into {body_obj.name}: {names}")
        for obj, bone_id in rigid:
            custom_submesh_id, obj_notes = _convert_to_custom_submesh(
                context, obj, arm_obj, bone_id, template, tex_dir, set_up_materials=False)
            lines.append(f"{obj.name} is now rigid submesh {custom_submesh_id} on bone_{bone_id}")
            notes += [f"{obj.name}: {note}" for note in obj_notes]
        lines.append("Next: weight the main body with Map Weights or Autorig")
        _report_adapt_summary(self, context, "Join or Replace Meshes", lines + notes)
        return {"FINISHED"}
