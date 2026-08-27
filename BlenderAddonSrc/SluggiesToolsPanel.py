"""Sluggies Tools sidebar panel (3D View > Sidebar > Sluggies Tools)."""

import bpy


class SLUGGIES_OT_transfer_pose(bpy.types.Operator):
    """Transfer the current pose into the .sluggie model (not implemented yet)"""
    bl_idname = "sluggies.transfer_pose"
    bl_label = "Transfer Pose"
    bl_description = "Transfer the current pose into the .sluggie model (not implemented yet)"

    def execute(self, context):
        self.report({"INFO"}, "Transfer Pose is not implemented yet")
        return {"FINISHED"}


class SLUGGIES_OT_transfer_animation(bpy.types.Operator):
    """Transfer the current animation into the .sluggie model (not implemented yet)"""
    bl_idname = "sluggies.transfer_animation"
    bl_label = "Transfer Animation"
    bl_description = "Transfer the current animation into the .sluggie model (not implemented yet)"

    def execute(self, context):
        self.report({"INFO"}, "Transfer Animation is not implemented yet")
        return {"FINISHED"}


class SLUGGIES_PT_tools(bpy.types.Panel):
    """Sluggies Tools sidebar panel"""
    bl_label = "Sluggies Tools"
    bl_idname = "SLUGGIES_PT_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sluggies"

    def draw(self, context):
        layout = self.layout
        layout.operator(SLUGGIES_OT_transfer_pose.bl_idname)
        layout.operator(SLUGGIES_OT_transfer_animation.bl_idname)


def register():
    bpy.utils.register_class(SLUGGIES_OT_transfer_pose)
    bpy.utils.register_class(SLUGGIES_OT_transfer_animation)
    bpy.utils.register_class(SLUGGIES_PT_tools)


def unregister():
    bpy.utils.unregister_class(SLUGGIES_PT_tools)
    bpy.utils.unregister_class(SLUGGIES_OT_transfer_animation)
    bpy.utils.unregister_class(SLUGGIES_OT_transfer_pose)
