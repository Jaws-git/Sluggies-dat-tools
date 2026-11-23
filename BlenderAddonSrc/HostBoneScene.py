"""Host bones in the scene: the uniform-bone cache with the application handlers that empty it
(``_UNIFORM_BONE_HANDLERS``, registered by ``SluggiesToolsPanel.register``), which bone each rigid mesh
claims, the host-bone and reassign choices, and the helpers behind Add Bone (create, renumber, track).

Imports ``SceneModels`` only among the tools modules. Never imports ``SluggiesToolsPanel``.
"""

import bpy
from bpy.app.handlers import persistent
from mathutils import Vector
from . import BoneRoles
from . import HostBones
from .ExportBones import _detect_uniform_vertex_bone_id
from .SceneModels import (
    _ANY_ARMATURE_CACHE,
    _DRAW_CACHE,
    _bone_id_from_name,
    _bone_records,
    _donor_mesh_objects,
    _find_target_armature,
    _model_is_stadium,
    _rigid_mesh_kind,
    _submesh_index_of,
)


# Every user-added bone gets this length in scene units, independent of how
# long its parent is — the display length is cosmetic (the exporter only reads
# the head position), and a fixed unit length keeps added bones selectable no
# matter how small the parent they hang off is.
ADDED_BONE_LENGTH = 1.0


_HOST_BONE_STATUS_TAGS = {
    HostBones.STATUS_RECOMMENDED: "free",
    HostBones.STATUS_ALLOWED: "root",
    HostBones.STATUS_DRIVES_SKINNING: "drives skinning",
}


# Object pointer -> (signature, bone id) for _uniform_bone_id: the panel and
# its dialogs ask on every redraw, and the answer reads every vertex. An entry
# is dropped when its Mesh data-block changes (weights, vertex groups, Edit Mode; posing
# updates only the objects, measured 2026-10-23), after undo/redo, on load and
# by this module's own vertex group writes. The signature also catches a
# swapped mesh, a changed vertex count and renamed vertex groups.
_UNIFORM_BONE_CACHE = {}


def _uniform_bone_id(obj):
    """_detect_uniform_vertex_bone_id, cached (see _UNIFORM_BONE_CACHE)."""
    mesh = obj.data
    signature = (mesh.as_pointer(), len(mesh.vertices), tuple(g.name for g in obj.vertex_groups))
    cached = _UNIFORM_BONE_CACHE.get(obj.as_pointer())
    if cached is not None and cached[0] == signature:
        return cached[1]
    bone_id = _detect_uniform_vertex_bone_id(obj)
    _UNIFORM_BONE_CACHE[obj.as_pointer()] = (signature, bone_id)
    return bone_id


@persistent
def _forget_uniform_bones_on_mesh_update(_scene, depsgraph):
    # Runs on every depsgraph update (playback, transform drags): skip the
    # update walk while there is nothing to forget.
    _ANY_ARMATURE_CACHE.clear()
    _DRAW_CACHE.clear()
    if not _UNIFORM_BONE_CACHE:
        return
    # Only the meshes that changed: editing one mesh must not make the next
    # redraw read every vertex of every other rigid mesh again.
    changed = {update.id.original.as_pointer() for update in depsgraph.updates
               if isinstance(update.id, bpy.types.Mesh)}
    if changed:
        for key in [key for key, (signature, _bone_id) in _UNIFORM_BONE_CACHE.items() if signature[0] in changed]:
            del _UNIFORM_BONE_CACHE[key]


@persistent
def _forget_uniform_bones(*_args):
    _UNIFORM_BONE_CACHE.clear()
    _ANY_ARMATURE_CACHE.clear()
    _DRAW_CACHE.clear()


_UNIFORM_BONE_HANDLERS = (
    ('depsgraph_update_post', _forget_uniform_bones_on_mesh_update),
    ('undo_post', _forget_uniform_bones),
    ('redo_post', _forget_uniform_bones),
    ('load_post', _forget_uniform_bones),
)


def _gather_scene_claims(context, arm_obj, bone_records, exclude=None):
    """The scene state layered on top of the import-time snapshot (step 2).
    *exclude* is a custom submesh whose bone doesn't count as claimed (the
    one being re-attached)."""
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
            target_bone_by_submesh[sub_idx] = _uniform_bone_id(obj)

    retargets, _issues = HostBones.compute_rigid_retargets(
        owner_bone_by_submesh, bone_geo_raw, target_bone_by_submesh, known_bone_ids)

    # Only this armature's custom submeshes claim its bones: a high/low pair
    # shares bone ids, and the other model's mesh on bone_N leaves ours free.
    custom_submesh_bone_ids = set()
    for o in context.view_layer.objects:
        if o.type == 'MESH' and o.parent is arm_obj and o.get('SluggiesCustomSubmesh') \
                and o is not exclude:
            bone_id = _uniform_bone_id(o)
            if bone_id is not None:
                custom_submesh_bone_ids.add(bone_id)

    return HostBones.SceneClaims(
        retargets=tuple(retargets),
        custom_submesh_bone_ids=frozenset(custom_submesh_bone_ids),
    )


def _ordered_host_bone_choices(context, arm_obj, exclude=None):
    records = _bone_records(arm_obj)
    claims = _gather_scene_claims(context, arm_obj, records, exclude)
    choices = HostBones.classify_host_bones(
        records, claims, roots_only=_model_is_stadium(arm_obj))
    return HostBones.order_host_bone_choices(choices)


def _nearest_choice(arm_obj, ordered_choices, point):
    """The recommended bone whose head is nearest the world-space *point*,
    falling back to all choices when nothing is 'recommended'."""
    recommended = [c for c in ordered_choices if c.status == HostBones.STATUS_RECOMMENDED]
    pool = recommended or ordered_choices
    if not pool:
        return None

    def _distance(choice):
        bone = arm_obj.data.bones.get(f'bone_{choice.bone_id}')
        if bone is None:
            return float('inf')
        head_world = arm_obj.matrix_world @ bone.head_local
        return (head_world - point).length

    return min(pool, key=_distance)


def _default_host_bone_choice(context, arm_obj, ordered_choices):
    """The recommended bone nearest the 3D cursor (plan step 3)."""
    return _nearest_choice(arm_obj, ordered_choices, context.scene.cursor.location)


def _preview_active_bone(arm_obj, bone_name):
    """Make *bone_name* the active bone of *arm_obj* while a dialog picks it.
    Best-effort: Blender may not redraw the viewport while a props dialog is
    open, so this is a courtesy, not a guarantee."""
    bone = arm_obj.data.bones.get(bone_name) if arm_obj is not None else None
    if bone is not None:
        arm_obj.data.bones.active = bone


def _update_host_bone_preview(self, context):
    _preview_active_bone(_find_target_armature(context), self.host_bone)


def _object_world_bbox_center(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return sum(corners, Vector((0.0, 0.0, 0.0))) / 8


def _default_reassign_target(arm_obj, obj, ordered_choices):
    """The recommended bone nearest the object's own world bounding-box
    centre (plan: 'the object is what's being moved', not the 3D cursor)."""
    return _nearest_choice(arm_obj, ordered_choices, _object_world_bbox_center(obj))


def _reassign_choices(context, arm_obj, obj):
    kind, submesh_index = _rigid_mesh_kind(obj)
    records = _bone_records(arm_obj)
    claims = _gather_scene_claims(context, arm_obj, records)
    moving_bone_id = _uniform_bone_id(obj)
    return HostBones.reassignment_choices(
        records, claims,
        moving_submesh_index=submesh_index if kind == 'donor' else None,
        moving_custom_bone_id=moving_bone_id if kind == 'custom' else None,
        roots_only=_model_is_stadium(arm_obj),
    )


def _rigid_mesh_attachment_label(context, arm_obj, obj):
    """'Attached to bone_<id>', with '(moved from bone_<id>)' when a donor
    rigid submesh's current bone differs from its donor owner, or a reason
    nothing can be shown."""
    kind, submesh_index = _rigid_mesh_kind(obj)
    if kind is None:
        return "Not a rigid or custom submesh"
    bone_id = _uniform_bone_id(obj)
    if bone_id is None:
        return "No single bone_<id> vertex group with weight; assign one to reassign"
    if kind == 'donor':
        donor_bone_id = next(
            (b_id for b in arm_obj.data.bones
             if (b_id := _bone_id_from_name(b.name)) is not None
             and int(b.get('SluggiesGeoIdRaw', HostBones.GEO_ID_FREE)) == submesh_index), None)
        if donor_bone_id is not None and donor_bone_id != bone_id:
            return f"Attached to bone_{bone_id} (moved from bone_{donor_bone_id})"
    return f"Attached to bone_{bone_id}"


STADIUM_ROOT_PARENT = '__ROOT__'


def _next_new_bone_creation_order(arm_obj):
    orders = [
        int(b.get('SluggiesCreationOrder', -1))
        for b in arm_obj.data.bones if b.get('SluggiesUserAdded')
    ]
    return max(orders) + 1 if orders else 0


def _renumber_added_bones(arm_obj):
    """Rename Add Bone bones to the `bone_<id>` the exporter will give them
    (HostBones.added_bone_renames). Renaming a Bone makes Blender rename the
    matching vertex groups on every mesh the armature deforms, so custom
    submeshes keep their host. Two passes, since a target name can still be
    held by another added bone. Returns the applied renames."""
    bones = arm_obj.data.bones
    donor_count = sum(1 for b in bones if not b.get('SluggiesUserAdded'))
    renames = HostBones.added_bone_renames(donor_count, [
        (b.name, int(b.get('SluggiesCreationOrder', 0)))
        for b in bones if b.get('SluggiesUserAdded')
    ])
    temporary = {}
    for index, old_name in enumerate(renames):
        bones[old_name].name = f'__sluggies_renumber_{index}'
        temporary[bones[f'__sluggies_renumber_{index}'].name] = renames[old_name]
    for temp_name, new_name in temporary.items():
        bones[temp_name].name = new_name
    return renames


def _stopped_after(changed):
    """The return of an operator that stops on an error: CANCELLED tells
    Blender nothing happened (no undo step), so once it *changed* something
    (renamed bones, reweighted a body, unhid objects) it finishes instead;
    the error is reported either way."""
    return {"FINISHED"} if changed else {"CANCELLED"}


def _renumber_added_bones_reporting(op, arm_obj, undo_step=False):
    """Rename added bones to their export ids and report it. *undo_step*: the
    caller is a dialog's invoke, which changes data before the user
    confirmed: the rename gets an undo step of its own, so Cancel leaves it
    as a step that can be undone instead of merging it into the next one."""
    renames = _renumber_added_bones(arm_obj)
    if renames:
        op.report({"INFO"}, "Renumbered added bones: " + ", ".join(
            f"{old} -> {new}" for old, new in sorted(renames.items())))
        if undo_step:
            bpy.ops.ed.undo_push(message="Renumber added bones")
    return renames


def _unused_bone_name(arm_obj):
    """A `bone_<N>` name not already used in *arm_obj*. Purely cosmetic: the
    exporter reassigns real ids from SluggiesCreationOrder, not from this
    name."""
    n = len(arm_obj.data.bones)
    while f'bone_{n}' in arm_obj.data.bones:
        n += 1
    return f'bone_{n}'


def _create_added_bone(context, arm_obj, parent_bone_name, animation_track=False, at_cursor=False):
    """Create one inert leaf bone parented to *parent_bone_name*: no
    GeoId, self-mirrored role 3, InheritTransform true, DrawPriority 0, and
    no track unless *animation_track* (then a fresh id, see
    `_set_added_bone_track`).
    *parent_bone_name* None makes a root bone at the 3D cursor instead (stadiums
    only: a mesh on a new root bone draws, Dolphin 2026-10-18). *at_cursor*
    starts a child bone at the 3D cursor too (stadiums: the bone can sit right
    next to its mesh, which keeps full position precision).
    The SRT type byte is not set here: it is a component-presence mask over
    the bone's own rotation/translation, so the exporter derives it from the
    values it writes (act_section.html#srt-type) rather than inheriting a
    parent's mask that may not fit."""
    # Everything that can refuse is asked before the bone exists: a used-up track id range used to leave
    # the new bone behind without its properties.
    track_id = HostBones.NO_TRACK
    if animation_track:
        track_id = HostBones.next_added_track_id(
            [int(b.get('track_id', HostBones.NO_TRACK)) for b in arm_obj.data.bones])
    prev_active = context.view_layer.objects.active
    prev_mode = context.object.mode if context.object is not None else 'OBJECT'
    try:
        context.view_layer.objects.active = arm_obj
        try:
            bpy.ops.object.mode_set(mode='EDIT')
        except RuntimeError as exc:
            raise ValueError(
                f"{arm_obj.name} cannot be put into Edit Mode (is the armature hidden, or its collection "
                f"excluded from the view layer?): {exc}") from exc
        try:
            edit_bones = arm_obj.data.edit_bones
            parent_eb = edit_bones.get(parent_bone_name) if parent_bone_name else None
            new_name = _unused_bone_name(arm_obj)
            new_eb = edit_bones.new(new_name)
            new_eb.parent = parent_eb
            new_eb.use_connect = False
            # World-aligned, facing -Y regardless of the parent bone's own
            # direction: convert the world -Y/+Z axes into the armature object's
            # local space, since edit-bone coordinates live there.
            world_to_local = arm_obj.matrix_world.to_3x3().inverted()
            local_dir = world_to_local @ Vector((0.0, -1.0, 0.0))
            local_up = world_to_local @ Vector((0.0, 0.0, 1.0))
            # `local_dir` is a world unit vector expressed in local space, so its
            # local length already equals one world unit under the object's scale:
            # scaling it by ADDED_BONE_LENGTH gives that length in the viewport,
            # whatever the parent bone measures.
            tail_offset = (
                local_dir * ADDED_BONE_LENGTH
                if local_dir.length > 1e-9 else Vector((0.0, 0.0, ADDED_BONE_LENGTH))
            )
            # Originates at the parent's tail, not floating off to the side, so
            # chained bones visually continue the parent like a real skeleton. A
            # root bone starts at the 3D cursor.
            new_eb.head = (
                parent_eb.tail if parent_eb is not None and not at_cursor
                else arm_obj.matrix_world.inverted() @ context.scene.cursor.location
            )
            new_eb.tail = new_eb.head + tail_offset
            new_eb.align_roll(local_up)
            new_name = new_eb.name
        finally:
            bpy.ops.object.mode_set(mode=prev_mode if prev_mode in ('OBJECT', 'EDIT') else 'OBJECT')

        new_bone = arm_obj.data.bones[new_name]
        new_bone['SluggiesUserAdded'] = True
        new_bone['SluggiesCreationOrder'] = _next_new_bone_creation_order(arm_obj)
        new_bone['SluggiesGeoIdRaw'] = HostBones.GEO_ID_FREE
        new_bone['SluggiesSkinned'] = False
        new_bone['SluggiesDrawPriority'] = 0
        new_bone['SluggiesInheritTransform'] = True
        new_bone['track_id'] = track_id
        BoneRoles.bone_collection(arm_obj.data, BoneRoles.ADDED).assign(new_bone)

    finally:
        context.view_layer.objects.active = prev_active
    return new_bone


def _set_added_bone_track(arm_obj, bone, enabled):
    """Give an added bone its own animation track id (HostBones.ADDED_TRACK_IDS)
    or take it away. With a track, an .anm export writes the bone's keyframes
    and the patcher puts the id into the model's bone-to-track table. Returns
    the bone's track id."""
    if not enabled:
        bone['track_id'] = HostBones.NO_TRACK
    elif int(bone.get('track_id', HostBones.NO_TRACK)) not in HostBones.ADDED_TRACK_IDS:
        used = [int(b.get('track_id', HostBones.NO_TRACK)) for b in arm_obj.data.bones if b != bone]
        bone['track_id'] = HostBones.next_added_track_id(used)
    return int(bone['track_id'])


def _begin_bone_name_display(op, arm_obj):
    """Turn on the viewport bone-name overlay for the dialog's duration,
    remembering whether it was already on so `_end_bone_name_display` can
    restore the prior state instead of always turning it back off."""
    op._orig_show_names = arm_obj.data.show_names
    if not op._orig_show_names:
        arm_obj.data.show_names = True


def _end_bone_name_display(op, arm_obj):
    if arm_obj is not None and getattr(op, '_orig_show_names', True) is False:
        arm_obj.data.show_names = False
