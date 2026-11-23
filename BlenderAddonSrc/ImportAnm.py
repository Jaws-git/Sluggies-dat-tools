"""Import of one ``.anm`` animation onto the armature of an imported model: the operator
(``SLUGGIES_OT_import_anm``, registered by ``ImportSluggies.register``), its File menu entry and the
metadata the ``.anm`` export reads back.

Imports ``ImportBones`` only among the ``Import*`` modules, and never ``ImportSluggies``.
"""

import bpy
import os
import struct
import mathutils
from bpy.props import StringProperty, CollectionProperty
from bpy_extras.io_utils import ImportHelper
from . import AnmFormat
from . import FieldCodec
from .ImportBones import (
    _anm_bone_corrections,
    _game_srt_matrix,
    game_rest_srt,
)


class SLUGGIES_OT_import_anm(bpy.types.Operator, ImportHelper):
    bl_idname = "sluggies.import_anm"
    bl_label = "Import Sluggers animation"
    bl_description = "Import ANM animation files onto the selected armature"
    bl_options = {"UNDO"}

    filename_ext = ".anm"
    filter_glob: StringProperty(default="*.anm", options={"HIDDEN"})  # type: ignore[valid-type]
    # files + directory let the file browser take a multi-selection.
    files: CollectionProperty(type=bpy.types.OperatorFileListElement,  # type: ignore[valid-type]
                              options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    def execute(self, context):
        arm_obj = context.active_object
        if arm_obj is None or arm_obj.type != 'ARMATURE':
            self.report({"ERROR"}, "Select an armature first")
            return {"CANCELLED"}

        arm_data = arm_obj.data
        if not any('track_id' in bone for bone in arm_data.bones):
            self.report({"ERROR"}, "Armature has no track_id data — import a .sluggie first")
            return {"CANCELLED"}

        track_to_bone = {}
        for bone in arm_data.bones:
            tid = bone.get('track_id', 0xFFFF)
            if tid != 0xFFFF:
                track_to_bone[tid] = bone.name

        # Reoriented bones need their keyframes moved onto the tail-aimed bone
        # frames; others share the game's rest frames (all stock rest scales
        # are 1). Armatures from before the "Reorient Bones" option (no
        # property) were reoriented.
        corrections = (_anm_bone_corrections(arm_data)
                       if arm_obj.get('SluggiesBonesReoriented', True) else None)

        if not self.files:
            file_list = [os.path.basename(self.filepath)]
        else:
            file_list = [f.name for f in self.files]

        if not arm_obj.animation_data:
            arm_obj.animation_data_create()

        # A script may call the operator with only filepath: directory is empty then.
        directory = self.directory or os.path.dirname(self.filepath)
        total_actions = 0
        for file_entry in file_list:
            filepath = os.path.join(directory, file_entry)
            anm_name = os.path.splitext(file_entry)[0]

            try:
                with open(filepath, 'rb') as f:
                    raw = f.read()
            except OSError as e:
                # Skipped, so the files before and after it are still imported.
                self.report({"WARNING"}, f"{file_entry}: could not be read ({e})")
                continue

            try:
                anm = AnmFormat.parse(raw)
            except (ValueError, struct.error) as e:
                self.report({"WARNING"}, f"{file_entry}: {e}")
                continue
            sequences = anm['sequences']
            group = anm_name

            for seq_i, sequence in enumerate(sequences):
                action_name = f"{anm_name}_seq{seq_i}"
                action = bpy.data.actions.new(name=action_name)
                action.use_fake_user = True
                _store_anm_metadata(action, anm, group, filepath, seq_i, set(track_to_bone))
                arm_obj.animation_data.action = action
                keyed = set()

                for track in sequence:
                    tid = track['track_id']
                    bone_name = track_to_bone.get(tid)
                    if bone_name is None:
                        continue

                    pose_bone = arm_obj.pose.bones.get(bone_name)
                    if pose_bone is None:
                        continue

                    bone_mat = pose_bone.bone.matrix_local
                    if pose_bone.parent:
                        E_local = pose_bone.parent.bone.matrix_local.inverted() @ bone_mat
                    else:
                        E_local = bone_mat
                    inv_E_local = E_local.inverted()
                    if corrections is not None:
                        inv_C_parent = (corrections[pose_bone.parent.name].inverted()
                                        if pose_bone.parent else mathutils.Matrix.Identity(4))
                        C_bone = corrections[bone_name]

                    rest_trans, rest_quat, rest_scale = game_rest_srt(arm_data.bones[bone_name])

                    pose_bone.rotation_mode = 'QUATERNION'
                    data_path_loc = f'pose.bones["{bone_name}"].location'
                    data_path_rot = f'pose.bones["{bone_name}"].rotation_quaternion'
                    data_path_scl = f'pose.bones["{bone_name}"].scale'

                    fc_loc = [action.fcurve_ensure_for_datablock(arm_obj, data_path_loc, index=i) for i in range(3)]
                    fc_rot = [action.fcurve_ensure_for_datablock(arm_obj, data_path_rot, index=i) for i in range(4)]
                    fc_scl = [action.fcurve_ensure_for_datablock(arm_obj, data_path_scl, index=i) for i in range(3)]

                    for kf in track['keys']:
                        frame = kf['time']
                        settings = kf['settings']

                        trans = settings.get(0, rest_trans)
                        scale = settings.get(1, rest_scale)
                        quat = settings.get(3, rest_quat)

                        L_anm = _game_srt_matrix(trans, quat, scale)

                        # Keyframes are game-space locals (child in game parent
                        # frame); the rest SRT gives identity either way.
                        if corrections is not None:
                            matrix_basis = inv_E_local @ inv_C_parent @ L_anm @ C_bone
                        else:
                            matrix_basis = inv_E_local @ L_anm
                        loc, rot_q, scl = matrix_basis.decompose()

                        for i in range(3):
                            fc_loc[i].keyframe_points.insert(frame, loc[i], options={'FAST'})
                        for i in range(4):
                            fc_rot[i].keyframe_points.insert(frame, rot_q[i], options={'FAST'})
                        for i in range(3):
                            fc_scl[i].keyframe_points.insert(frame, scl[i], options={'FAST'})

                    for fc in fc_loc + fc_rot + fc_scl:
                        fc.update()
                    keyed.add(bone_name)

                # The game holds a bone without a track at its rest pose. Key
                # it there, so switching to this action does not keep the
                # pose another action left on it.
                for pose_bone in arm_obj.pose.bones:
                    if pose_bone.name in keyed:
                        continue
                    pose_bone.rotation_mode = 'QUATERNION'
                    for prop, values in (('location', (0.0, 0.0, 0.0)),
                                         ('rotation_quaternion', (1.0, 0.0, 0.0, 0.0)),
                                         ('scale', (1.0, 1.0, 1.0))):
                        data_path = f'pose.bones["{pose_bone.name}"].{prop}'
                        for i, value in enumerate(values):
                            fc = action.fcurve_ensure_for_datablock(arm_obj, data_path, index=i)
                            fc.keyframe_points.insert(0, value, options={'FAST'})

                total_actions += 1

        self.report({"INFO"}, f"Imported {total_actions} animation action(s)")
        return {"FINISHED"}


def _store_anm_metadata(action, anm, group, filepath, seq_index, bone_track_ids):
    """What the .anm export needs to write this sequence back: the group,
    the sequence's place in it, and its original track list (tracks of
    armature bones keep only their header, the rest their data)."""
    records = AnmFormat.track_records(anm['sequences'][seq_index], bone_track_ids)
    action['SluggiesAnmGroup'] = group
    action['SluggiesAnmSource'] = filepath
    action['SluggiesAnmMagic'] = anm['magic']
    action['SluggiesAnmBankId'] = anm['bank_id']
    action['SluggiesAnmSequence'] = seq_index
    action['SluggiesAnmSequenceCount'] = len(anm['sequences'])
    action['SluggiesAnmTracks'] = FieldCodec.encode_compact(AnmFormat.dumps_records(records))
    # The sequence length, editable as the action's manual frame range.
    length = max((t['length'] for t in anm['sequences'][seq_index]), default=0.0)
    action.use_frame_range = True
    action.frame_start = 0
    action.frame_end = max(length, 1.0)


def menu_func_import_anm(self, context):
    self.layout.operator(SLUGGIES_OT_import_anm.bl_idname, text="Sluggers animation (.anm)")
