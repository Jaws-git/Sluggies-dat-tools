"""Export an animation group (the actions imported from one .anm file) back
to an .anm file.

Two steps: SLUGGIES_OT_export_anm_group asks for the group, then
SLUGGIES_OT_export_anm asks for the file and writes it. Bone tracks are
sampled from the posed armature at every frame of each action's frame range
(constraints included) and stored with linear translation/scale and slerp
rotation keys, dropping keys that interpolation reproduces. Tracks the
armature does not drive (no bone with that track id, channel-less tracks)
are written back as imported.
"""

import math
import os
import struct

import bpy
import mathutils
from bpy.props import EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from . import AnmFormat
from . import FieldCodec
from .ImportSluggies import _anm_bone_corrections

# A channel the original track lacks is added when the bone leaves its rest
# value by more than this (translation in game units).
REST_VEC_TOL = 1e-3
REST_QUAT_TOL = 1e-4


def anm_groups():
    """{group: [action, ...]} of the actions an .anm import tagged."""
    groups = {}
    for action in bpy.data.actions:
        group = action.get('SluggiesAnmGroup')
        if group:
            groups.setdefault(group, []).append(action)
    return groups


def _group_sequences(actions):
    """Actions ordered by sequence index; raises ValueError on gaps/duplicates."""
    count = max(int(a.get('SluggiesAnmSequenceCount', 0)) for a in actions)
    by_index = {}
    for action in actions:
        by_index.setdefault(int(action.get('SluggiesAnmSequence', -1)), []).append(action)
    problems = []
    for index in range(count):
        found = by_index.get(index, [])
        if not found:
            problems.append(f"sequence {index} has no action")
        elif len(found) > 1:
            names = ', '.join(sorted(a.name for a in found))
            problems.append(f"sequence {index} has several actions ({names}); delete the extra ones")
    if problems:
        raise ValueError('; '.join(problems))
    return [by_index[i][0] for i in range(count)]


def _records(action):
    return AnmFormat.loads_records(FieldCodec.decode_field(action['SluggiesAnmTracks']))


def check_target(filepath, actions):
    """None when *filepath* is new or holds this group, else the reason to refuse.

    Same magic and sequence count, and every sequence still has the track
    ids it was imported with (tracks an export added are allowed)."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'rb') as f:
            magic, file_seqs = AnmFormat.signature(f.read())
    except (OSError, ValueError, struct.error) as e:
        return f"{os.path.basename(filepath)} is not a readable .anm file ({e})"
    group = actions[0]['SluggiesAnmGroup']
    if magic != int(actions[0]['SluggiesAnmMagic']) or len(file_seqs) != len(actions):
        return (f"{os.path.basename(filepath)} holds a different animation group "
                f"({len(file_seqs)} sequences, magic {magic:#010x}) than '{group}'")
    for index, (action, file_ids) in enumerate(zip(actions, file_seqs)):
        missing = {r['id'] for r in _records(action)} - file_ids
        if missing:
            return (f"{os.path.basename(filepath)} holds a different animation group than '{group}' "
                    f"(sequence {index} lacks track ids {', '.join(hex(t) for t in sorted(missing))})")
    return None


def _game_rest(bone):
    """Rest (scale, quaternion [w, x, y, z], translation) in game terms."""
    nw, x, y, z = bone.get('game_quaternion', (-1, 0, 0, 0))
    return (list(bone.get('game_scale', (1, 1, 1))), [-nw, x, y, z],
            list(bone.get('game_translation', (0, 0, 0))))


def _deviates(samples, rest):
    """Channels in which *samples* leave the rest SRT."""
    scale, quat, trans = rest
    out = set()
    for _t, s, q, tr in samples:
        if max(abs(a - b) for a, b in zip(s, scale)) > REST_VEC_TOL:
            out.add(AnmFormat.CH_SCALE)
        if 1.0 - abs(sum(a * b for a, b in zip(q, quat))) > REST_QUAT_TOL:
            out.add(AnmFormat.CH_QUATERNION)
        if max(abs(a - b) for a, b in zip(tr, trans)) > REST_VEC_TOL:
            out.add(AnmFormat.CH_TRANSLATION)
    return out


class _ActionSwap:
    """Plays *action* on the armature alone; restores the previous action,
    slot, NLA mutes, influence and frame afterwards."""

    def __init__(self, context, arm_obj):
        self.scene = context.scene
        self.arm_obj = arm_obj

    def __enter__(self):
        ad = self.arm_obj.animation_data or self.arm_obj.animation_data_create()
        self.ad = ad
        self.frame = (self.scene.frame_current, self.scene.frame_subframe)
        self.action = ad.action
        self.slot = getattr(ad, 'action_slot', None)
        self.influence = ad.action_influence
        self.blend = ad.action_blend_type
        self.mutes = [(track, track.mute) for track in ad.nla_tracks]
        for track, _mute in self.mutes:
            track.mute = True
        ad.action_influence = 1.0
        ad.action_blend_type = 'REPLACE'
        return self

    def play(self, action):
        self.ad.action = action
        if hasattr(self.ad, 'action_slot') and self.ad.action_slot is None and len(action.slots):
            self.ad.action_slot = action.slots[0]

    def __exit__(self, *exc):
        self.ad.action = self.action
        if self.slot is not None and hasattr(self.ad, 'action_slot'):
            try:
                self.ad.action_slot = self.slot
            except (TypeError, RuntimeError):
                pass
        self.ad.action_influence = self.influence
        self.ad.action_blend_type = self.blend
        for track, mute in self.mutes:
            track.mute = mute
        self.scene.frame_set(self.frame[0], subframe=self.frame[1])
        return False


def _sample_action(scene, arm_obj, bones, corrections, frames):
    """{bone name: [(time, scale, quat [w,x,y,z], translation)]} of game locals."""
    inv_c = ({name: c.inverted() for name, c in corrections.items()}
             if corrections is not None else None)
    samples = {name: [] for name in bones}
    for frame in frames:
        scene.frame_set(frame)
        game_world = {}
        for pb in arm_obj.pose.bones:
            game_world[pb.name] = pb.matrix @ inv_c[pb.name] if inv_c is not None else pb.matrix.copy()
        for name in bones:
            pb = arm_obj.pose.bones[name]
            local = game_world[name]
            if pb.parent is not None:
                parent = game_world[pb.parent.name]
                if abs(parent.determinant()) > 1e-12:
                    local = parent.inverted() @ local
            loc, rot, scl = local.decompose()
            samples[name].append((float(frame), list(scl), [rot.w, rot.x, rot.y, rot.z], list(loc)))
    return samples


def build_group(context, arm_obj, actions):
    """(magic, bank id, sequences for AnmFormat.write, notes)."""
    arm_data = arm_obj.data
    bone_of_track = {}
    for bone in arm_data.bones:
        tid = bone.get('track_id', 0xFFFF)
        if tid != 0xFFFF:
            bone_of_track.setdefault(int(tid), bone.name)
    corrections = (_anm_bone_corrections(arm_data)
                   if arm_obj.get('SluggiesBonesReoriented', True) else None)

    sequences, notes = [], []
    with _ActionSwap(context, arm_obj) as swap:
        for action in actions:
            records = _records(action)
            start, end = action.frame_range
            frames = list(range(int(math.floor(start)), int(math.ceil(end)) + 1))
            length = float(end)

            missing = sorted({r['id'] for r in records if 'keys' not in r} - set(bone_of_track))
            if missing:
                raise ValueError(f"{action.name}: the armature has no bones for track ids "
                                 f"{', '.join(hex(t) for t in missing)}; select the armature "
                                 f"the animation was imported onto")

            swap.play(action)
            samples = _sample_action(context.scene, arm_obj, list(bone_of_track.values()),
                                     corrections, frames)

            tracks, present = [], set()
            for record in records:
                if 'keys' in record:
                    tracks.append(AnmFormat.passthrough_track(record))
                    continue
                tid = record['id']
                present.add(tid)
                bone = arm_data.bones[bone_of_track[tid]]
                bone_samples = samples[bone.name]
                channels = set(AnmFormat.channels_of(record['type']))
                channels |= _deviates(bone_samples, _game_rest(bone))
                tracks.append(AnmFormat.rebuilt_track(tid, length, bone_samples, channels,
                                                      flags=record['type'], replace=record['replace']))

            # Bones the original sequence left at rest but the action moves.
            added = []
            for tid in sorted(set(bone_of_track) - present):
                bone = arm_data.bones[bone_of_track[tid]]
                channels = _deviates(samples[bone.name], _game_rest(bone))
                if channels:
                    tracks.append(AnmFormat.rebuilt_track(tid, length, samples[bone.name], channels))
                    added.append(bone.name)
            if added:
                notes.append(f"{action.name}: new tracks for {', '.join(added)}")
            sequences.append(tracks)

    first = actions[0]
    return int(first['SluggiesAnmMagic']), int(first.get('SluggiesAnmBankId', 0)), sequences, notes


def _armature(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == 'ARMATURE' else None


_group_items_cache = []


def _group_items(_self, _context):
    # Blender needs the item strings kept alive while the enum is shown.
    global _group_items_cache
    _group_items_cache = [
        (name, name, f"{len(actions)} action(s)")
        for name, actions in sorted(anm_groups().items())
    ] or [('NONE', "(no imported .anm groups)", "")]
    return _group_items_cache


class SLUGGIES_OT_export_anm_group(bpy.types.Operator):
    bl_idname = "sluggies.export_anm_group"
    bl_label = "Export Sluggers animation"
    bl_description = ("Write an animation group (all actions imported from one .anm file) "
                      "back to an .anm file, sampled from the selected armature")

    group: EnumProperty(name="Animation group", items=_group_items)  # type: ignore[valid-type]

    def invoke(self, context, event):
        if _armature(context) is None:
            self.report({"ERROR"}, "Select the armature the animation was imported onto")
            return {"CANCELLED"}
        if not anm_groups():
            self.report({"ERROR"}, "No imported animation groups; import an .anm file first")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, confirm_text="OK")

    def draw(self, context):
        self.layout.prop(self, "group")

    def execute(self, context):
        if self.group == 'NONE':
            return {"CANCELLED"}
        bpy.ops.sluggies.export_anm('INVOKE_DEFAULT', group=self.group)
        return {"FINISHED"}


class SLUGGIES_OT_export_anm(bpy.types.Operator, ExportHelper):
    bl_idname = "sluggies.export_anm"
    bl_label = "Export Sluggers animation"
    bl_description = "Write an animation group to an .anm file"
    bl_options = {"INTERNAL"}

    filename_ext = ".anm"
    filter_glob: StringProperty(default="*.anm", options={"HIDDEN"})  # type: ignore[valid-type]
    group: StringProperty(options={"HIDDEN"})  # type: ignore[valid-type]

    def invoke(self, context, event):
        actions = anm_groups().get(self.group)
        if not self.filepath and actions:
            source = actions[0].get('SluggiesAnmSource', '')
            self.filepath = source or f"{self.group}.anm"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        arm_obj = _armature(context)
        if arm_obj is None:
            self.report({"ERROR"}, "Select the armature the animation was imported onto")
            return {"CANCELLED"}
        actions = anm_groups().get(self.group)
        if not actions:
            self.report({"ERROR"}, f"No actions belong to animation group '{self.group}'")
            return {"CANCELLED"}
        try:
            actions = _group_sequences(actions)
        except ValueError as e:
            self.report({"ERROR"}, f"'{self.group}': {e}")
            return {"CANCELLED"}

        refusal = check_target(self.filepath, actions)
        if refusal:
            self.report({"ERROR"}, f"Not written: {refusal}")
            return {"CANCELLED"}

        try:
            magic, bank_id, sequences, notes = build_group(context, arm_obj, actions)
            data = AnmFormat.write(magic, bank_id, sequences)
        except ValueError as e:
            self.report({"ERROR"}, f"Not written: {e}")
            return {"CANCELLED"}

        with open(self.filepath, 'wb') as f:
            f.write(data)
        for note in notes:
            self.report({"INFO"}, note)
        self.report({"INFO"}, f"Wrote {len(sequences)} sequence(s), {len(data):,} bytes to {self.filepath}")
        return {"FINISHED"}


def menu_func_export_anm(self, context):
    self.layout.operator(SLUGGIES_OT_export_anm_group.bl_idname, text="Sluggers animation (.anm)")


def register():
    bpy.utils.register_class(SLUGGIES_OT_export_anm_group)
    bpy.utils.register_class(SLUGGIES_OT_export_anm)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export_anm)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export_anm)
    bpy.utils.unregister_class(SLUGGIES_OT_export_anm)
    bpy.utils.unregister_class(SLUGGIES_OT_export_anm_group)
