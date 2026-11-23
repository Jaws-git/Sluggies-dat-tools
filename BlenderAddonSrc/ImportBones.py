"""Import, bones: game SRT helpers and the ANM corrections, absolute bone matrices, ``build_armature``, bone
collections, the rest-pose action, bones added by the user (``add_imported_added_bones``) and
``restore_donor_bones``.

Imports ``ImportDecode`` only among the ``Import*`` modules, and never ``ImportSluggies``.
"""

import bpy
import math
import mathutils
from . import AnimScale
from . import BoneRoles
from . import HostBones
from .ImportDecode import (
    GEO_ID_FREE,
)


# Fallback tail length for a bone that gets no direction from the hierarchy,
# and the reference scale for clamping leaf-bone tails in build_armature.
LEAF_TAIL_FALLBACK = 0.05


# Reorient Bones: minimum length of a bone aimed at several children, and the
# armature-space direction for a bone whose children give it none (world -Y
# once the armature object's +90 deg X rotation is applied).
REORIENT_MIN_FORK_LENGTH = 0.15


REORIENT_FORWARD = mathutils.Vector((0.0, 0.0, 1.0))


def _game_srt_matrix(trans, quat, scale):
    """4x4 T @ R @ S from a game SRT; ``quat`` is the stored [-w, x, y, z].
    The quaternion is normalized first: an s16-quantized one is off unit
    length by up to 2e-4, which ``to_matrix`` would turn into a scale."""
    T = mathutils.Matrix.Translation(trans)
    R = mathutils.Quaternion((-quat[0], quat[1], quat[2], quat[3])).normalized().to_matrix().to_4x4()
    S = mathutils.Matrix.Diagonal((*scale, 1))
    return T @ R @ S


def game_rest_srt(bone):
    """(translation, stored quaternion [-w, x, y, z], scale) of *bone*'s game
    rest local. Imported bones carry it as game_* properties. An Add Bone bone
    has none: its rest is what the .sluggie export writes for it
    (ExportBones.encode_bone_hierarchy_edited), the Blender rest matrix
    relative to its parent's."""
    if bone.get('SluggiesUserAdded') and bone.parent is not None:
        t, r, s = (bone.parent.matrix_local.inverted() @ bone.matrix_local).decompose()
        return list(t), [-r.w, r.x, r.y, r.z], list(s)
    return (list(bone.get('game_translation', (0, 0, 0))),
            list(bone.get('game_quaternion', (-1, 0, 0, 0))),
            list(bone.get('game_scale', (1, 1, 1))))


def _anm_bone_corrections(arm_data):
    """{bone name: C} with C = inverse(game rest world) @ Blender rest matrix.

    Only for armatures imported with "Reorient Bones" on: build_armature aims
    each bone's tail at its children, so a Blender bone's axes differ from
    the game bone's. ANM keyframes are game locals; posing bone i to (game
    animated world) @ C_i deforms the mesh as the game does and leaves the
    rest pose untouched (the importer applies
    inverse(E_local) @ inverse(C_parent) @ L_anm @ C_bone).

    Game rest worlds come from the game_* bone properties and the parent chain
    (HeadPosition matches their translations). A bone with
    SluggiesInheritTransform off has no game parent; its animation still
    follows its Blender parent here (no stock model has such a child bone).
    """
    game_world = {}

    def world_of(bone):
        if bone.name not in game_world:
            local = _game_srt_matrix(*game_rest_srt(bone))
            if bone.parent is not None and bone.get('SluggiesInheritTransform', True):
                local = world_of(bone.parent) @ local
            game_world[bone.name] = local
        return game_world[bone.name]

    corrections = {}
    for bone in arm_data.bones:
        world = world_of(bone)
        # A collapsed (zero-scale) rest bone has no inverse: skip its correction.
        corrections[bone.name] = (world.inverted() @ bone.matrix_local
                                  if abs(world.determinant()) > 1e-12 else mathutils.Matrix.Identity(4))
    return corrections


def _compute_bone_absolute_matrices(bone_list):
    """Return a {BoneId: mathutils.Matrix} dict of absolute world-space transforms.

    For non-skinned submeshes the game stores vertices in bone-local space and
    applies bone.absolute_transform (= S@R@T accumulated up the parent chain in
    row-vector convention) as a bind-shape matrix to map them to world space.
    We replicate that here in Blender column-vector convention:
      game row-vector:   local = S_row @ R_row @ T_row
      Blender col-vector: local = T_col @ R_col @ S_col  (reversed order)
      absolute_blender = parent_abs_blender @ child_local_blender
    """
    bone_map = {b['BoneId']: b for b in bone_list}
    abs_mats = {}  # BoneId -> mathutils.Matrix

    # Topological pass: resolve parents before children.  Guard against cycles.
    remaining = list(bone_map.keys())
    for _ in range(len(remaining) + 1):
        still_remaining = []
        for bone_id in remaining:
            bone     = bone_map[bone_id]
            parent_id = bone.get('ParentBoneId')
            if parent_id is not None and parent_id not in abs_mats:
                still_remaining.append(bone_id)
                continue
            trans = bone['Translation']
            scale = bone['Scale']
            quat  = bone['Quaternion']   # stored as [-game_qw, game_qx, game_qy, game_qz]
            T = mathutils.Matrix.Translation(trans)
            # Negate w to recover game_qw; the stored negation was an artefact of the
            # row-vector SRT convention.  With correct w, to_matrix() gives R_col.
            R = mathutils.Quaternion((-quat[0], quat[1], quat[2], quat[3])).to_matrix().to_4x4()
            S = mathutils.Matrix([
                [scale[0], 0,       0,       0],
                [0,        scale[1], 0,      0],
                [0,        0,       scale[2], 0],
                [0,        0,       0,        1],
            ])
            local_mat = T @ R @ S
            if parent_id is not None:
                abs_mats[bone_id] = abs_mats[parent_id] @ local_mat
            else:
                abs_mats[bone_id] = local_mat
        remaining = still_remaining
        if not remaining:
            break

    return abs_mats


def _submesh_owner_bones(bone_list, submesh_index):
    """``(donor owner, effective owner)`` bone ids of a rigid submesh: the
    bone whose donor ``GeoId`` is the submesh, and the bone that owns it after
    the file's ``GeoIdEdited`` retargets (the same bone when there are none).
    Both are None for a skinned submesh."""
    donor = next(
        (int(bd['BoneId']) for bd in bone_list
         if not bd.get('Skinned') and bd.get('GeoId') == submesh_index),
        None,
    )
    edited = next(
        (int(bd['BoneId']) for bd in bone_list
         if bd.get('GeoIdEdited') is not None and int(bd['GeoIdEdited']) == submesh_index),
        None,
    )
    return donor, donor if edited is None else edited


def _apply_nonskinned_transform(obj, submesh_index, bone_list, abs_bone_mats, owner_bone_id=None):
    """Set *obj*.matrix_world to the absolute transform of the non-skinned bone
    that owns *submesh_index*, if one exists.  No-op for skinned submeshes.

    *owner_bone_id* names the bone to use; by default the effective owner
    (``GeoIdEdited`` first), so a retargeted mesh re-imports on its new bone."""
    if owner_bone_id is None:
        _donor, owner_bone_id = _submesh_owner_bones(bone_list, submesh_index)
    if owner_bone_id is None:
        return
    mat = abs_bone_mats.get(owner_bone_id)
    if mat is None:
        return
    if obj.parent is not None:
        # Relative to the armature, which carries the 90 degree import
        # rotation. Setting matrix_world instead depended on the armature's
        # world matrix not being evaluated yet: re-creating Add Bone bones
        # (an Edit Mode round trip) evaluated it first, and every mesh came
        # out without the rotation (2026-10-18).
        obj.matrix_parent_inverse = mathutils.Matrix.Identity(4)
        obj.matrix_basis = mat
    else:
        obj.matrix_world = mat


def _restore_root_bone_scale(arm_obj, edited):
    """Put an exported ``RootBoneScaleEdited`` back on the armature, so a
    re-import followed by an export keeps the model scale."""
    try:
        value = tuple(float(v) for v in edited)
    except (TypeError, ValueError):
        return
    if len(value) == 3 and all(math.isfinite(v) for v in value):
        arm_obj["RootBoneScale"] = value


def build_armature(name, bone_list, collection, skin_data=None, reorient=True):
    """Create a Blender Armature from a BoneHierarchy list and link it to *collection*.

    The armature object is named *name* (the imported .sluggie file name without
    its extension). Bone names are ``bone_<id>``. With *reorient* (the import
    option "Reorient Bones"), tails are aimed at the child's head, or at the
    average head of several children (at least REORIENT_MIN_FORK_LENGTH long;
    forward when that gives no direction); leaf bones continue their parent's
    direction instead, falling
    back to a small global-Z offset when there is no usable parent so the bone
    stays visible in the viewport. Without it, that step is skipped and every
    bone keeps the game's own rest orientation (its local +Y is the bone axis,
    one length for all bones). Returns the armature object.
    """
    arm_data = bpy.data.armatures.new(name)
    arm_obj  = bpy.data.objects.new(name, arm_data)
    collection.objects.link(arm_obj)

    # Whole-model (root-bone) scale, editable by the user in the outliner.
    # Defaults to unit scale so an untouched import writes nothing back. The
    # exporter reads this and emits RootBoneScaleEdited when it differs from
    # [1,1,1]; the patcher then scales the model's main root bone SRT.
    arm_obj["RootBoneScale"] = (1.0, 1.0, 1.0)
    arm_obj.id_properties_ui("RootBoneScale").update(
        description=(
            "Whole-model scale applied to the main root bone. "
            "Leave at [1,1,1] for no change; any other value is written back "
            "to dt_na.dat on export."
        ))

    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm_data.edit_bones

    bone_id_to_name = {}
    for bd in bone_list:
        eb = edit_bones.new(f"bone_{bd['BoneId']}")
        h = bd['HeadPosition']
        eb.head = (h[0], h[1], h[2])
        # Placeholder tail — overridden below when a child is found
        eb.tail = (h[0], h[1], h[2] + LEAF_TAIL_FALLBACK)
        bone_id_to_name[bd['BoneId']] = eb.name

    # Collect every child head for each parent so we can aim tails
    child_heads = {}
    for bd in bone_list:
        pid = bd['ParentBoneId']
        if pid is not None:
            child_heads.setdefault(pid, []).append(mathutils.Vector(bd['HeadPosition']))

    for bd in bone_list:
        eb = edit_bones.get(bone_id_to_name.get(bd['BoneId'], ''))
        if eb is None:
            continue
        # Set parent relationship
        pid = bd['ParentBoneId']
        if pid is not None:
            parent_eb = edit_bones.get(bone_id_to_name.get(pid, ''))
            if parent_eb:
                eb.parent = parent_eb
        # Aim tail at the child, or at the children's average head. A fork's
        # average can sit almost on the head (pelvis between the thighs), so
        # forks get a minimum length; a bone with no direction points forward.
        if reorient and bd['BoneId'] in child_heads:
            heads_c = child_heads[bd['BoneId']]
            target = sum(heads_c, mathutils.Vector()) / len(heads_c)
            direction = target - eb.head
            if direction.length <= 1e-9:
                eb.tail = eb.head + REORIENT_FORWARD * REORIENT_MIN_FORK_LENGTH
            elif len(heads_c) > 1 and direction.length < REORIENT_MIN_FORK_LENGTH:
                eb.tail = eb.head + direction.normalized() * REORIENT_MIN_FORK_LENGTH
            else:
                eb.tail = target

    heads = [bd['HeadPosition'] for bd in bone_list]
    extent = max(
        max(h[axis] for h in heads) - min(h[axis] for h in heads)
        for axis in range(3)
    ) if heads else 0.0

    if not reorient:
        # No reorientation: each bone takes the game's rest rotation (bone axis =
        # the game bone's local +Y), all at one length scaled to the model.
        length = max(extent * 0.03, LEAF_TAIL_FALLBACK * 0.5)
        abs_mats = _compute_bone_absolute_matrices(bone_list)
        for bd in bone_list:
            eb = edit_bones.get(bone_id_to_name.get(bd['BoneId'], ''))
            if eb is None:
                continue
            eb.tail = eb.head + mathutils.Vector((0.0, length, 0.0))
            rot = abs_mats[bd['BoneId']].to_quaternion() if bd['BoneId'] in abs_mats else mathutils.Quaternion()
            eb.matrix = mathutils.Matrix.Translation(eb.head) @ rot.to_matrix().to_4x4()

    # Leaf bones have no child to aim at. Continuing the parent's direction
    # keeps chain terminators — fingertips and the like — in line with the
    # chain instead of forking off along +Z, where the stub reads as a real
    # finger segment and is easy to click by mistake.
    #
    # The parent's length is only a starting point: some leaves hang off long
    # structural bones (Luigi's bone_4 off a 0.85-unit spine bone) and would
    # shoot a spike across the model, while others hang off near-degenerate
    # bones (mini_luigi's bones sit 2e-06 apart) and would come out an
    # invisible sliver. Clamp into a band scaled to the model's own bone
    # spread, falling back to the fixed stub length for models too small or
    # too degenerate to derive a scale from.
    leaf_max = max(extent * 0.05, LEAF_TAIL_FALLBACK)
    leaf_min = LEAF_TAIL_FALLBACK * 0.5

    # Runs after the loop above so every parent already has its final tail.
    for bd in bone_list:
        if not reorient or bd['BoneId'] in child_heads:
            continue
        eb = edit_bones.get(bone_id_to_name.get(bd['BoneId'], ''))
        if eb is None or eb.parent is None:
            continue
        direction = eb.parent.tail - eb.parent.head
        # A parent with no direction at all gives nothing to continue, so the
        # +Z placeholder stands; normalizing here would collapse the bone and
        # Blender drops zero-length bones on leaving edit mode.
        if direction.length > 1e-9:
            length = min(max(direction.length, leaf_min), leaf_max)
            eb.tail = eb.head + direction.normalized() * length

    bpy.ops.object.mode_set(mode='OBJECT')

    skn_bone_ids = HostBones.skn_bone_ids(skin_data)
    for bd in bone_list:
        bone_name = bone_id_to_name.get(bd['BoneId'])
        if bone_name and bone_name in arm_data.bones:
            b = arm_data.bones[bone_name]
            b['track_id'] = bd.get('TrackId', 0xFFFF)
            # The game re-solves these bones (AnimScale): they follow the
            # parent's scaled position, not its scale. No visible effect
            # while every scale is 1, as in every stock animation.
            if AnimScale.is_layer_root(b['track_id']):
                b.inherit_scale = 'NONE'
            b['game_translation'] = list(bd['Translation'])
            b['game_quaternion'] = list(bd['Quaternion'])
            b['game_scale'] = list(bd['Scale'])
            # Donor ownership/skinning snapshot for the Add submesh free-bone
            # proposal (HostBones.py). Fixed
            # facts of the exported model, read-only, stored on Bone (not
            # EditBone) so they survive into saved .blend files.
            # SluggiesSkinned must come from SkinData: BoneHierarchy's own
            # "Skinned" flag only means GeoIdRaw == 0xFFFF.
            b['SluggiesGeoIdRaw'] = int(bd.get('GeoIdRaw', 0xFFFF))
            b['SluggiesSkinned'] = bd['BoneId'] in skn_bone_ids
            # Round-tripped for Add Bone: every bone (donor or new) needs these
            # written into BoneHierarchyEdited on export. (A new bone's own
            # SRTType is derived at export, not copied from its parent.)
            if bd.get('MirrorBoneId') is not None:
                b['SluggiesMirrorBoneId'] = int(bd['MirrorBoneId'])
            if bd.get('MirrorRole') is not None:
                b['SluggiesMirrorRole'] = int(bd['MirrorRole'])
            b['SluggiesSRTType'] = int(bd.get('SRTType', 0))
            b['SluggiesDrawPriority'] = int(bd.get('DrawPriority', 0))
            b['SluggiesInheritTransform'] = bool(bd.get('InheritTransform', True))
            # False for every imported (donor) bone; Add bone sets this True.
            b['SluggiesUserAdded'] = False

    arm_obj['SluggiesBoneMetadataVersion'] = HostBones.BONE_METADATA_VERSION
    arm_obj.show_in_front = True
    arm_obj.display_type = 'WIRE'
    arm_obj.rotation_euler[0] = math.pi / 2
    bpy.context.view_layer.objects.active = prev_active
    return arm_obj


def assign_bone_collections(arm_obj, bone_names=None):
    """Sort every bone of *arm_obj* into the BoneRoles collections, from the
    properties the import stored on it (SluggiesSkinned, SluggiesGeoIdRaw,
    track_id, SluggiesUserAdded). Collections are created in
    ``BoneRoles.COLLECTION_ORDER``, only for roles the model has. With
    *bone_names*, only those bones, taken out of their old role collections
    first."""
    arm_data = arm_obj.data
    if bone_names is not None:
        for name in BoneRoles.COLLECTION_ORDER:
            collection = arm_data.collections.get(name)
            if collection is not None:
                for bone_name in bone_names:
                    collection.unassign(arm_data.bones[bone_name])
    roles_by_bone = {
        bone.name: BoneRoles.bone_roles(
            skinned=bool(bone.get('SluggiesSkinned', False)),
            geo_id_raw=int(bone.get('SluggiesGeoIdRaw', BoneRoles.GEO_ID_FREE)),
            track_id=int(bone.get('track_id', BoneRoles.NO_TRACK)),
            has_parent=bone.parent is not None,
            has_children=bool(bone.children),
            user_added=bool(bone.get('SluggiesUserAdded', False)),
        )
        for bone in arm_data.bones
        if bone_names is None or bone.name in bone_names
    }
    used = {role for roles in roles_by_bone.values() for role in roles}
    for name in BoneRoles.COLLECTION_ORDER:
        if name in used:
            BoneRoles.bone_collection(arm_data, name)
    for bone_name, roles in roles_by_bone.items():
        for role in roles:
            arm_data.collections[role].assign(arm_data.bones[bone_name])


def add_rest_pose_action(arm_obj):
    """A one-frame action keying every bone at its rest pose, made the
    armature's active action, so the Action Editor can switch back to rest
    after .anm imports. An empty action would not reset the pose: assigning
    it leaves bones where the previous action put them. It has no
    SluggiesAnmGroup, so the .anm export ignores it."""
    action = bpy.data.actions.new(name=f"{arm_obj.name}_RestPose")
    action.use_fake_user = True
    action['SluggiesRestPose'] = True
    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    arm_obj.animation_data.action = action

    def _key(data_path, values, group):
        for i, value in enumerate(values):
            # Blender 4.4+ (slotted actions) creates F-curves through the data-block.
            if hasattr(action, 'fcurve_ensure_for_datablock'):
                fc = action.fcurve_ensure_for_datablock(arm_obj, data_path, index=i, group_name=group)
            else:
                fc = action.fcurves.new(data_path, index=i, action_group=group)
            fc.keyframe_points.insert(0, value, options={'FAST'})
            fc.update()

    for pose_bone in arm_obj.pose.bones:
        pose_bone.rotation_mode = 'QUATERNION'
        path = f'pose.bones["{bpy.utils.escape_identifier(pose_bone.name)}"]'
        _key(f'{path}.location', (0.0, 0.0, 0.0), pose_bone.name)
        _key(f'{path}.rotation_quaternion', (1.0, 0.0, 0.0, 0.0), pose_bone.name)
        _key(f'{path}.scale', (1.0, 1.0, 1.0), pose_bone.name)
    return action


# Length of a re-created Add Bone bone (HostBoneScene.ADDED_BONE_LENGTH; the
# panel imports this module, so the value is repeated, not imported).
ADDED_BONE_LENGTH = 1.0


def _srt_local_matrix(bone):
    """A bone's local rest matrix T @ R @ S from its .sluggie SRT fields, the
    same math as _compute_bone_absolute_matrices (Quaternion stored as
    ``[-qw, qx, qy, qz]``)."""
    trans, quat, scale = bone['Translation'], bone['Quaternion'], bone['Scale']
    rotation = mathutils.Quaternion((-quat[0], quat[1], quat[2], quat[3])).to_matrix().to_4x4()
    return mathutils.Matrix.Translation(trans) @ rotation @ mathutils.Matrix.Diagonal((*scale, 1.0))


def add_imported_added_bones(arm_obj, entries, donor_game_matrices=None):
    """Re-create the Add Bone bones of ``BoneHierarchyEdited`` (*entries* from
    CustomSubmeshImport.added_bone_entries) on *arm_obj*, with the properties
    Add Bone gives them. The exporter wrote each one's rest matrix relative to
    its parent's Blender rest matrix (a stadium root's relative to the
    armature), so this inverts it exactly. In a stadium a child of an original
    bone is written relative to that bone's GAME frame instead
    (``encode_bone_hierarchy_edited``): pass the stadium's
    *donor_game_matrices* (``{BoneId: absolute matrix}`` of the original
    bones) and those children are placed from it. Blender's own frame of an
    original bone is reoriented for display and would put the bone elsewhere
    (Blender 5.2.2, Yoshi Park, 2026-11-15: placed at (3, -2, 5), back at
    (3, -5, -2) and turned by 90 degrees). Returns the bone names."""
    if not entries:
        return []
    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')
    created = []
    try:
        edit_bones = arm_obj.data.edit_bones
        for order, entry in enumerate(entries):
            name = f"bone_{int(entry['BoneId'])}"
            if name in edit_bones:
                continue
            parent = entry.get('ParentBoneId')
            parent_eb = edit_bones.get(f"bone_{int(parent)}") if parent is not None else None
            eb = edit_bones.new(name)
            eb.head = (0.0, 0.0, 0.0)
            eb.tail = (0.0, ADDED_BONE_LENGTH, 0.0)
            if parent_eb is None:
                base = mathutils.Matrix.Identity(4)
            elif donor_game_matrices is not None and int(parent) in donor_game_matrices:
                base = mathutils.Matrix(donor_game_matrices[int(parent)])
            else:
                base = parent_eb.matrix
            eb.matrix = base @ _srt_local_matrix(entry)
            eb.parent = parent_eb
            eb.use_connect = False
            created.append((eb.name, order, entry))
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    for name, order, entry in created:
        b = arm_obj.data.bones[name]
        b['SluggiesUserAdded'] = True
        b['SluggiesCreationOrder'] = order
        b['SluggiesGeoIdRaw'] = int(entry.get('GeoIdRaw', GEO_ID_FREE))
        b['SluggiesSkinned'] = bool(entry.get('Skinned', False))
        b['SluggiesDrawPriority'] = int(entry.get('DrawPriority', 0))
        b['SluggiesInheritTransform'] = bool(entry.get('InheritTransform', True))
        b['track_id'] = int(entry.get('TrackId', GEO_ID_FREE))
    bpy.context.view_layer.objects.active = prev_active
    return [name for name, _order, _entry in created]


def restore_donor_bones(context, arm_obj, bone_list, skin_data=None):
    """Put the .sluggie's skeleton (*bone_list*, its BoneHierarchy) back on
    *arm_obj*: donor bones deleted in Blender come back where a fresh import
    puts them (Reorient Bones as on the original import), with their track
    id and import metadata, and every donor bone gets its original parent.
    Added bones keep their place, parent and collections. Keys of a deleted
    bone stay in its actions under its name, so its animation comes back
    with it. Returns (restored names, re-parented names)."""
    bones = arm_obj.data.bones
    expected = {f"bone_{bd['BoneId']}": (f"bone_{bd['ParentBoneId']}" if bd['ParentBoneId'] is not None else None)
                for bd in bone_list}
    for name in expected:
        if name in bones and bones[name].get('SluggiesUserAdded'):
            raise ValueError(f"added bone {name} holds the name of an original bone; renumber added bones first")
    missing = [name for name in expected if name not in bones]
    reparent = [name for name in expected if name in bones
                and (bones[name].parent.name if bones[name].parent else None) != expected[name]]
    if not missing and not reparent:
        return [], []
    if arm_obj.name not in context.view_layer.objects:
        raise ValueError(f"{arm_obj.name} is not in the view layer; include its collection")
    old_parents = {bones[name].parent.name for name in reparent if bones[name].parent is not None}

    view_layer = context.view_layer
    prev_active = view_layer.objects.active
    selected = [obj for obj in view_layer.objects if obj.select_get()]
    for obj in selected:
        obj.select_set(False)
    try:
        fresh = {}
        if missing:
            # A throwaway fresh import gives the deleted bones the same rest
            # frames (tails, roll) the original import gave them.
            temp = build_armature("__sluggies_restore_bones", bone_list, context.scene.collection, skin_data,
                                  reorient=bool(arm_obj.get('SluggiesBonesReoriented', True)))
            temp_data = temp.data
            for name in missing:
                bone = temp_data.bones[name]
                # Plain copies: the temp armature's ID properties die with it.
                props = {key: value.to_list() if hasattr(value, 'to_list') else value
                         for key, value in bone.items()}
                fresh[name] = (bone.head_local.copy(), bone.tail_local.copy(), bone.matrix_local.copy(),
                               bone.inherit_scale, props)
            bpy.data.objects.remove(temp)
            bpy.data.armatures.remove(temp_data)

        hidden = (arm_obj.hide_get(), arm_obj.hide_viewport)
        arm_obj.hide_set(False)
        arm_obj.hide_viewport = False
        arm_obj.select_set(True)
        view_layer.objects.active = arm_obj
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            edit_bones = arm_obj.data.edit_bones
            for name in missing:
                head, tail, matrix, _inherit, _props = fresh[name]
                eb = edit_bones.new(name)
                eb.head = head
                eb.tail = tail
                eb.matrix = matrix
                eb.use_connect = False
            for name in missing + reparent:
                parent = expected[name]
                edit_bones[name].parent = edit_bones[parent] if parent is not None else None
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
            arm_obj.select_set(False)
            arm_obj.hide_set(hidden[0])
            arm_obj.hide_viewport = hidden[1]

        bones = arm_obj.data.bones
        for name in missing:
            _head, _tail, _matrix, inherit, props = fresh[name]
            bone = bones[name]
            bone.inherit_scale = inherit
            for key, value in props.items():
                bone[key] = value
        touched = set(missing) | set(reparent) | old_parents | {expected[n] for n in missing + reparent}
        assign_bone_collections(arm_obj, [name for name in touched if name is not None
                                          and not bones[name].get('SluggiesUserAdded')])
    finally:
        for obj in selected:
            obj.select_set(True)
        view_layer.objects.active = prev_active
    return missing, reparent
