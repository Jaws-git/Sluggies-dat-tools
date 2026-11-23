"""Scale model in all animations: the operator around ``AnimScale`` and the F-curve helper with its fallback
for older Blender versions. Registered by ``SluggiesToolsPanel.register``.

Never imports ``SluggiesToolsPanel``.
"""

import bpy
from . import AnimScale
from .AnmExport import _POSE_BONE_PATH, _action_fcurves, anm_groups
from . import HostBones
from .SceneModels import (
    _find_target_armature,
    _no_target_armature_message,
)


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
        if getattr(arm_obj.animation_data, 'use_tweak_mode', False):
            # Blender refuses to switch the action then: some actions would be scaled, the rest not, and a
            # second run would scale the first ones twice.
            self.report({"ERROR"}, "The armature's NLA is in tweak mode: leave it (Tab in the NLA editor) "
                                   "and scale again")
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
