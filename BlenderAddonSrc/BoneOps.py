"""Tools operators on bones: Add Bone, Renumber Added Bones and Toggle Track. Registered by
``SluggiesToolsPanel.register``; the work is in ``HostBoneScene``.

Never imports ``SluggiesToolsPanel``.
"""

import bpy
from bpy.props import (
    BoolProperty, EnumProperty,
)
from . import HostBones
from .SceneModels import (
    _any_sluggies_armature_imported,
    _bone_id_from_name,
    _bone_metadata_is_current,
    _find_target_armature,
    _model_is_stadium,
    _no_target_armature_message,
)
from .HostBoneScene import (
    STADIUM_ROOT_PARENT,
    _begin_bone_name_display,
    _create_added_bone,
    _end_bone_name_display,
    _renumber_added_bones_reporting,
    _set_added_bone_track,
)
from .EnumItems import (
    _add_bone_parent_enum_items,
    _drop_enum_items,
)


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
            cls.poll_message_set("Switch to Object Mode")
            return False
        if not _any_sluggies_armature_imported(context):
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
        _renumber_added_bones_reporting(self, arm_obj, undo_step=True)
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
