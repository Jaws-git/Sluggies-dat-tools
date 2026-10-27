"""The 'Adapt' box's Scale and Position step: one rigid transform plus a
uniform scale that brings the selected meshes (from elsewhere) roughly onto
a Sluggies armature, as a starting point for manual fitting.

Target (the Sluggies model, in its rest pose): the armature's up and front
(game +Y and +Z: a character stands on the armature origin and faces its
front), the height of its donor meshes, its pelvis/neck/hand bones by
animation track id (shared by every character skeleton, see AnimScale).

Source (the selected meshes, as displayed): up comes from the old rig's
names when they tell it (head/hat/hair above foot/ankle/toe), else from the
shape (below); the front from face parts (eyes, nose, mouth, ...) in front
of the head, else from the smallest turn that keeps the left/right mirror
axis sideways. Names are read from the meshes' vertex groups (centres) and,
for parts no group names, from the bones of an armature the meshes still
follow.

Scale fit (SCALE_FIT_ITEMS): by default the overall height, with the hands
pulling a little; 'arms' lines up hand height and spread alone, and the
hands' depth instead of the torso's (hair or a cape hanging down the back
shifts the torso's); 'head' puts
the head's joint with the neck (the old rig's head bone, else where the head
vertex group blends into the neck) at the height of the armature's head (or
neck) bone. The scale matches the overall height first; hand height and
spread (hand/finger vertex groups) pull it a little when both hands are
found. The model's lowest point lands on the armature origin, centred
sideways on it, with its torso around the spine bones. Hair (vertex groups
or materials named so, _HAIR_WORDS) counts for the height but not for the
sideways centre or the torso depth.
"""

import re

import numpy as np
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

from . import AdaptCleanup, AdaptWeights


# Animation track ids (AnimScale): pelvis, spine bones, neck, the two hands.
_PELVIS_TRACK = 5
_NECK_TRACK = 9
_SPINE_TRACKS = (5, 6, 7, 9)
_HAND_TRACKS = (0x13, 0x19)
# The bone that turns the head: the head track, else the neck (several
# skeletons have no head track). Its head is where the head meets the neck.
_HEAD_JOINT_TRACKS = (10, 9)
# Where pelvis and neck sit, as a fraction of the height, when the skeleton
# lacks those bones.
_FALLBACK_PELVIS = 0.45
_FALLBACK_NECK = 0.8

# Up guess: keep the current Z up while the Z extent is at least this share
# of the other non-mirror extent (a standing quadruped is longer than tall);
# below it the model is taken to lie down.
_KEEP_Z_UP_RATIO = 0.6
# Least-squares weights of hand height and hand spread against the overall
# height (1.0), and how far (relative) the hands may move the height-only
# scale before they are ignored as a bad match (A-pose vs T-pose, props).
_HAND_WEIGHT = 0.3
_HAND_MAX_PULL = 0.35

# Scale fit choices: the overall height (hands pulling a little), the hands
# lining up, or the head meeting the neck where the armature's does.
SCALE_FIT_ITEMS = [
    ('DEFAULT', "Default",
     "Match the model's overall height; hand/finger vertex groups adjust it a little"),
    ('ARMS', "Prefer arms fit",
     "Scale and place so the hands line up with the armature's (height, spread and depth); the head may end "
     "up higher or lower"),
    ('HEAD', "Prefer head fit",
     "Scale so the head meets the neck where the armature's does; the arms may end up longer or shorter"),
]
# Head joint from vertex groups: vertices partly weighted to the head group
# below its centre (the blend into the neck), else the head's lowest share.
_HEAD_BLEND_MAX = 0.99
_HEAD_BASE_SHARE = 0.05

# Vertex groups taken as hand or finger: common rig names (hand_L,
# LeftHand, Bip01 R Finger0, J_Bip_L_Thumb1, ...), not handles or IK targets.
_HAND_GROUP_RE = re.compile(r'hand|finger|thumb|palm', re.IGNORECASE)
_NOT_HAND_GROUP_RE = re.compile(r'handle|ik|pole|target|attach|prop|weapon', re.IGNORECASE)

# Name-read directions (words of vertex group and bone names, see
# AdaptWeights.name_tokens). Front parts are checked first, then feet, head, top.
_FRONT_WORDS = frozenset({
    'eye', 'eyes', 'eyeball', 'brow', 'eyebrow', 'lid', 'eyelid', 'lash', 'nose', 'nostril', 'mouth', 'lip',
    'lips', 'chin', 'cheek', 'tongue', 'teeth', 'tooth', 'snout', 'muzzle', 'beak',
})
_BOTTOM_WORDS = frozenset({'foot', 'feet', 'ankle', 'toe', 'toes', 'heel'})
_HEAD_WORDS = frozenset({'head', 'skull'})
_TOP_WORDS = frozenset({'hat', 'hair', 'crown'})
# Up from names: head minus feet must point along one axis (this share of its
# length at least) and span this share of the model's largest extent.
_NAMED_UP_DOMINANCE = 0.6
_NAMED_UP_MIN_SPAN = 0.12
# Front from names: face minus head, flattened onto the ground, likewise
# along one axis and at least this share of the largest extent.
_NAMED_FRONT_DOMINANCE = 0.6
_NAMED_FRONT_MIN_SPAN = 0.005

# Hair (vertex group or material name words): left out of the torso depth
# and the sideways centre, which a ponytail or a side braid would pull off.
_HAIR_WORDS = frozenset({
    'hair', 'hairs', 'ponytail', 'pony', 'pigtail', 'pigtails', 'twintail', 'twintails', 'braid', 'braids',
    'bang', 'bangs', 'fringe', 'tress', 'tresses', 'ahoge',
})
# A vertex is hair when hair groups carry at least this share of its weight.
_HAIR_WEIGHT_SHARE = 0.5

_MIRROR_SAMPLES = 2000
_KDTREE_MAX_POINTS = 60000

AXES = {
    'POS_X': Vector((1, 0, 0)), 'NEG_X': Vector((-1, 0, 0)),
    'POS_Y': Vector((0, 1, 0)), 'NEG_Y': Vector((0, -1, 0)),
    'POS_Z': Vector((0, 0, 1)), 'NEG_Z': Vector((0, 0, -1)),
}
AXIS_ITEMS = [('AUTO', "Auto", "Guess from the model's shape")] + [
    (ident, f"{'+' if ident.startswith('POS') else '-'}{ident[-1]}", f"World {ident[-1]} axis, "
     f"{'positive' if ident.startswith('POS') else 'negative'} direction")
    for ident in AXES
]


def axis_name(vec):
    """'+Z' style name of a world axis vector."""
    i = max(range(3), key=lambda k: abs(vec[k]))
    return f"{'+' if vec[i] > 0 else '-'}{'XYZ'[i]}"


def _world_points(obj, depsgraph=None):
    """*obj*'s vertices in world space (Nx3): as displayed (modifiers
    applied) with *depsgraph*, else the mesh data itself."""
    source = obj.evaluated_get(depsgraph) if depsgraph is not None else obj
    mesh = source.to_mesh() if depsgraph is not None else obj.data
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get('co', co)
    if depsgraph is not None:
        source.to_mesh_clear()
    m = np.array(obj.matrix_world)
    return co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]


def _hair_mask(obj, depsgraph=None):
    """Per vertex of _world_points(*obj*, *depsgraph*): whether it is hair,
    mostly weighted to vertex groups named like hair or on a face with a
    material named so (_HAIR_WORDS). None when nothing is named so."""
    groups = {g.index for g in obj.vertex_groups if AdaptWeights.name_tokens(g.name) & _HAIR_WORDS}
    slots = {i for i, slot in enumerate(obj.material_slots)
             if slot.material is not None and AdaptWeights.name_tokens(slot.material.name) & _HAIR_WORDS}
    if not groups and not slots:
        return None
    source = obj.evaluated_get(depsgraph) if depsgraph is not None else obj
    mesh = source.to_mesh() if depsgraph is not None else obj.data
    try:
        mask = np.zeros(len(mesh.vertices), dtype=bool)
        if groups:
            for v in mesh.vertices:
                total = hair = 0.0
                for g in v.groups:
                    total += g.weight
                    if g.group in groups:
                        hair += g.weight
                if total > 0.0 and hair >= _HAIR_WEIGHT_SHARE * total:
                    mask[v.index] = True
        if slots:
            for poly in mesh.polygons:
                if poly.material_index in slots:
                    mask[list(poly.vertices)] = True
    finally:
        if depsgraph is not None:
            source.to_mesh_clear()
    return mask


def _mirror_scores(points):
    """Per world axis: mean distance from each (sampled) vertex, mirrored
    across the bounding box centre on that axis, to the nearest vertex,
    relative to the box diagonal. Lowest = the left/right axis."""
    lo, hi = points.min(axis=0), points.max(axis=0)
    diag = float(np.linalg.norm(hi - lo)) or 1.0
    tree_points = points[::max(1, len(points) // _KDTREE_MAX_POINTS)]
    tree = KDTree(len(tree_points))
    for i, p in enumerate(tree_points):
        tree.insert(p, i)
    tree.balance()
    samples = points[::max(1, len(points) // _MIRROR_SAMPLES)]
    scores = []
    for axis in range(3):
        centre = (lo[axis] + hi[axis]) / 2
        total = 0.0
        for p in samples:
            q = p.copy()
            q[axis] = 2 * centre - q[axis]
            total += tree.find(q)[2]
        scores.append(total / len(samples) / diag)
    return scores


def _guess_up(points, mirror_axis, exclude_axis=None):
    """World axis vector the model's head points along: Z when it is plausible
    (importers bring models in Z up), else the longest non-mirror axis, its
    sign from where the vertices crowd (a detailed head above the legs)."""
    extent = points.max(axis=0) - points.min(axis=0)
    others = [a for a in range(3) if a not in (mirror_axis, exclude_axis)] or \
        [a for a in range(3) if a != exclude_axis]
    if 2 in others and all(extent[2] >= _KEEP_Z_UP_RATIO * extent[a] for a in others):
        return Vector((0, 0, 1))
    axis = max(others, key=lambda a: extent[a])
    centre = (points[:, axis].min() + points[:, axis].max()) / 2
    up = Vector((0, 0, 0))
    up[axis] = 1.0 if points[:, axis].mean() >= centre else -1.0
    return up


def _frame(front, up):
    """Rotation matrix with columns (right, front, up); right = front x up."""
    right = front.cross(up)
    return Matrix((right, front, up)).transposed()


def _name_kind(name):
    words = AdaptWeights.name_tokens(name)
    for kind, kind_words in (('front', _FRONT_WORDS), ('bottom', _BOTTOM_WORDS), ('head', _HEAD_WORDS),
                             ('top', _TOP_WORDS)):
        if words & kind_words:
            return kind
    return None


def _rig_head(arm_obj, bone, posed):
    """World position of an old rig's *bone* head: as posed, or at rest."""
    pose_bone = arm_obj.pose.bones.get(bone.name) if posed and arm_obj.pose is not None else None
    return arm_obj.matrix_world @ (pose_bone.head if pose_bone is not None else bone.head_local)


def is_posed(arm_obj, tolerance=1e-4):
    """Whether *arm_obj* shows a pose that differs from its rest pose."""
    if arm_obj.data.pose_position == 'REST' or arm_obj.pose is None:
        return False
    for pose_bone in arm_obj.pose.bones:
        delta = pose_bone.matrix - pose_bone.bone.matrix_local
        if any(abs(value) > tolerance for row in delta for value in row):
            return True
    return False


def bake_pose(context, obj):
    """Write the shape *obj*'s Armature modifiers give it now (the old rig's
    pose) into its mesh, every shape key included, so it keeps that shape
    once another armature drives it. Other modifiers are switched off while
    measuring. Returns a backup for restore_mesh, or None when nothing to do."""
    armature_mods = [m for m in obj.modifiers if m.type == 'ARMATURE' and m.show_viewport and m.object is not None]
    if not armature_mods:
        return None
    mesh = obj.data
    count = len(mesh.vertices)
    keys = mesh.shape_keys
    backup = {'co': np.empty(count * 3, dtype=np.float32), 'keys': {}}
    mesh.vertices.foreach_get('co', backup['co'])
    blocks = list(keys.key_blocks) if keys is not None else []
    for block in blocks:
        data = np.empty(count * 3, dtype=np.float32)
        block.data.foreach_get('co', data)
        backup['keys'][block.name] = data

    shown = {m.name: m.show_viewport for m in obj.modifiers}
    key_state = (obj.show_only_shape_key, obj.active_shape_key_index,
                 {b.name: (b.vertex_group, b.mute) for b in blocks})

    def evaluate():
        context.view_layer.update()
        evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
        result = evaluated.to_mesh()
        try:
            if len(result.vertices) != count:
                raise ValueError(f"{obj.name}: its modifiers change the vertex count, so its pose cannot be kept")
            co = np.empty(count * 3, dtype=np.float32)
            result.vertices.foreach_get('co', co)
            return co
        finally:
            evaluated.to_mesh_clear()

    baked = {}
    try:
        for mod in obj.modifiers:
            mod.show_viewport = mod in armature_mods
        if not blocks:
            baked[None] = evaluate()
        else:
            # Each key on its own and unmasked: its full shape, then posed.
            obj.show_only_shape_key = True
            for block in blocks:
                block.vertex_group = ''
                block.mute = False
            for index, block in enumerate(blocks):
                obj.active_shape_key_index = index
                baked[block.name] = evaluate()
    finally:
        for mod in obj.modifiers:
            mod.show_viewport = shown.get(mod.name, mod.show_viewport)
        if blocks:
            obj.show_only_shape_key, obj.active_shape_key_index = key_state[0], key_state[1]
            for block in blocks:
                block.vertex_group, block.mute = key_state[2][block.name]

    if not blocks:
        mesh.vertices.foreach_set('co', baked[None])
    else:
        for block in blocks:
            block.data.foreach_set('co', baked[block.name])
        mesh.vertices.foreach_set('co', baked[keys.reference_key.name])
    mesh.update()
    return backup


def restore_mesh(obj, backup):
    """Undo bake_pose with its *backup*."""
    mesh = obj.data
    mesh.vertices.foreach_set('co', backup['co'])
    if mesh.shape_keys is not None:
        for block in mesh.shape_keys.key_blocks:
            if block.name in backup['keys']:
                block.data.foreach_set('co', backup['keys'][block.name])
    mesh.update()


def named_points(meshes, armatures=(), posed=False):
    """{'front'|'bottom'|'head'|'top': world point} read from names: the
    weighted centre of the meshes' vertex groups of that kind, else the mean
    head of *armatures*' bones of that kind (the old rig, *posed* or at
    rest)."""
    sums = {}
    for obj in meshes:
        kinds = {g.index: _name_kind(g.name) for g in obj.vertex_groups}
        kinds = {index: kind for index, kind in kinds.items() if kind}
        if not kinds:
            continue
        mw = obj.matrix_world
        for v in obj.data.vertices:
            for g in v.groups:
                kind = kinds.get(g.group)
                if kind and g.weight > 0.0:
                    entry = sums.setdefault(kind, [Vector(), 0.0])
                    entry[0] += (mw @ v.co) * g.weight
                    entry[1] += g.weight
    found = {kind: total / weight for kind, (total, weight) in sums.items() if weight > 0.0}
    from_bones = {}
    for arm_obj in armatures:
        for bone in arm_obj.data.bones:
            kind = _name_kind(bone.name)
            if kind and kind not in found:
                from_bones.setdefault(kind, []).append(_rig_head(arm_obj, bone, posed))
    found.update({kind: sum(pts, Vector()) / len(pts) for kind, pts in from_bones.items()})
    return found


def _dominant_axis(vec, dominance, min_length, skip=()):
    """The world axis vector *vec* points along, or None unless that
    component is at least *dominance* of its length and *min_length* long."""
    axes = [a for a in range(3) if a not in skip]
    axis = max(axes, key=lambda a: abs(vec[a]))
    if abs(vec[axis]) < min_length or abs(vec[axis]) < dominance * vec.length:
        return None
    out = Vector((0, 0, 0))
    out[axis] = 1.0 if vec[axis] > 0 else -1.0
    return out


def _named_up(named, size):
    top = [named[k] for k in ('head', 'top') if k in named]
    if not top or 'bottom' not in named:
        return None
    return _dominant_axis(sum(top, Vector()) / len(top) - named['bottom'],
                          _NAMED_UP_DOMINANCE, _NAMED_UP_MIN_SPAN * size)


def _named_front(named, up_axis, mirror_axis, size):
    if 'front' not in named or 'head' not in named:
        return None
    vec = named['front'] - named['head']
    vec[up_axis] = 0.0
    front = _dominant_axis(vec, _NAMED_FRONT_DOMINANCE, _NAMED_FRONT_MIN_SPAN * size, skip=(up_axis,))
    # The face sits on the mirror plane: a front along the mirror axis is a misread.
    if front is None or abs(front[mirror_axis]) > 0.5:
        return None
    return front


def _source_frame(points, target_frame, up_choice, front_choice, named=None):
    """(up, front, how up was found, how the front was found) of the
    selected meshes in world space; 'set', 'names' or 'guessed'."""
    named = named or {}
    size = float((points.max(axis=0) - points.min(axis=0)).max()) or 1.0
    scores = _mirror_scores(points)
    mirror_axis = min(range(3), key=lambda a: scores[a])
    up_how = 'set'
    if up_choice != 'AUTO':
        up = AXES[up_choice].copy()
    else:
        up, up_how = _named_up(named, size), 'names'
        if up is None:
            front_axis = None
            if front_choice != 'AUTO':
                front_axis = max(range(3), key=lambda k: abs(AXES[front_choice][k]))
            up, up_how = _guess_up(points, mirror_axis, front_axis), 'guessed'
    up_axis = max(range(3), key=lambda k: abs(up[k]))
    if up_axis == mirror_axis:
        # Up from names along the shape's mirror axis: the mirror axis is the
        # most symmetric of the other two.
        mirror_axis = min((a for a in range(3) if a != up_axis), key=lambda a: scores[a])

    front_how = 'set'
    if front_choice != 'AUTO':
        front = AXES[front_choice].copy()
        if abs(front.dot(up)) > 0.5:
            raise ValueError(f"Facing {axis_name(front)} is not at a right angle to up {axis_name(up)}")
    elif (front := _named_front(named, up_axis, mirror_axis, size)) is not None:
        front_how = 'names'
    else:
        front_how = 'guessed'
        # The depth axis is the less symmetric of the two across the up axis.
        side_axes = [a for a in range(3) if a != up_axis]
        depth_axis = max(side_axes, key=lambda a: scores[a])
        candidates = []
        for sign in (1.0, -1.0):
            vec = Vector((0, 0, 0))
            vec[depth_axis] = sign
            candidates.append(vec)
        # The smallest turn onto the armature: models mostly come in facing
        # the right way, or lying down with the face up.
        front = min(candidates, key=lambda f: (target_frame @ _frame(f, up).transposed())
                    .to_quaternion().angle)
    return up, front, up_how, front_how


def _sluggies_model_meshes(arm_obj, objects):
    """The armature's own meshes (donor and custom submeshes), not collision objects."""
    meshes = []
    for obj in objects:
        if obj.type != 'MESH' or not AdaptCleanup.is_sluggies_mesh(obj):
            continue
        collision = getattr(obj, 'sluggies_collision', None)
        if collision is not None and collision.enabled:
            continue
        followed = [obj.parent] + [m.object for m in obj.modifiers if m.type == 'ARMATURE']
        if arm_obj in followed:
            meshes.append(obj)
    return meshes


def _target(arm_obj, scene_objects):
    """The Sluggies side, in the armature frame (right, front, up) from its
    origin: dict with frame, origin, height, pelvis/neck height, spine depth,
    hands [(right, front, up) palm points] and notes."""
    notes = []
    m3 = arm_obj.matrix_world.to_3x3()
    up = (m3 @ Vector((0, 1, 0))).normalized()
    front = (m3 @ Vector((0, 0, 1))).normalized()
    frame = _frame(front, up)
    origin = arm_obj.matrix_world.translation.copy()
    to_local = frame.transposed()

    def local(p):
        return to_local @ (p - origin)

    heads = {}
    for bone in arm_obj.data.bones:
        tid = bone.get('track_id')
        if tid is not None:
            heads.setdefault(int(tid), bone)

    meshes = _sluggies_model_meshes(arm_obj, scene_objects)
    if meshes:
        points = np.vstack([_world_points(o) for o in meshes])
        local_points = (points - np.array(origin)) @ np.array(frame)
        height = float(local_points[:, 2].max())
        depth_mid = float(local_points[:, 1].min() + local_points[:, 1].max()) / 2
    else:
        bone_heights = [local(arm_obj.matrix_world @ b.head_local).z for b in arm_obj.data.bones]
        height = max(bone_heights, default=0.0)
        depth_mid = 0.0
        notes.append("The armature has no meshes: its height comes from its bones")
    if height <= 0.0:
        raise ValueError(f"{arm_obj.name} has no height above its origin")

    def bone_point(tid):
        return local(arm_obj.matrix_world @ heads[tid].head_local) if tid in heads else None

    pelvis, neck = bone_point(_PELVIS_TRACK), bone_point(_NECK_TRACK)
    head = next((bone_point(t) for t in _HEAD_JOINT_TRACKS if t in heads), None)
    spine = [bone_point(t) for t in _SPINE_TRACKS if t in heads]
    hands = []
    for tid in _HAND_TRACKS:
        if tid in heads:
            bones = [heads[tid]] + list(heads[tid].children_recursive)
            pts = [local(arm_obj.matrix_world @ b.head_local) for b in bones]
            hands.append(sum(pts, Vector()) / len(pts))
    return dict(
        frame=frame, origin=origin, height=height,
        pelvis=pelvis.z if pelvis is not None else _FALLBACK_PELVIS * height,
        neck=neck.z if neck is not None else _FALLBACK_NECK * height,
        spine_depth=sum((p.y for p in spine), 0.0) / len(spine) if spine else depth_mid,
        hands=hands if len(hands) == 2 else [], notes=notes,
        head=head.z if head is not None and head.z > 0.0 else None,
    )


def _source_head_joint(meshes, armatures, frame_world_to_src, posed=False):
    """Height (source frame z) where the head meets the neck, or None: the
    lowest 'head' bone of the old rig, else from the 'head' vertex groups
    (_HEAD_BLEND_MAX, _HEAD_BASE_SHARE)."""
    bones = [frame_world_to_src @ _rig_head(arm, b, posed)
             for arm in armatures for b in arm.data.bones if _name_kind(b.name) == 'head']
    if bones:
        return min(p.z for p in bones)
    samples = []  # (z, weight)
    for obj in meshes:
        heads = {g.index for g in obj.vertex_groups if _name_kind(g.name) == 'head'}
        if not heads:
            continue
        mw = obj.matrix_world
        for v in obj.data.vertices:
            weight = sum(g.weight for g in v.groups if g.group in heads)
            if weight > 0.0:
                samples.append(((frame_world_to_src @ (mw @ v.co)).z, min(weight, 1.0)))
    if not samples:
        return None
    total = sum(w for _z, w in samples)
    centre = sum(z * w for z, w in samples) / total
    blend = [z for z, w in samples if w < _HEAD_BLEND_MAX and z < centre]
    if len(blend) >= 3:
        return sum(blend) / len(blend)
    solid = sorted(z for z, w in samples if w >= 0.5) or sorted(z for z, _w in samples)
    base = solid[:max(1, int(len(solid) * _HEAD_BASE_SHARE))]
    return sum(base) / len(base)


def _source_hands(meshes, frame_world_to_src, lateral_centre, armatures=(), posed=False):
    """The two palm points (source frame) from hand/finger vertex groups,
    else from the old rig's hand/finger bones, split by side of the mirror
    plane, or [] unless both sides have some."""
    hands = _group_hands(meshes, frame_world_to_src, lateral_centre)
    if hands:
        return hands
    sides = {-1: [], 1: []}
    for arm in armatures:
        for bone in arm.data.bones:
            if _HAND_GROUP_RE.search(bone.name) and not _NOT_HAND_GROUP_RE.search(bone.name):
                point = frame_world_to_src @ _rig_head(arm, bone, posed)
                sides[1 if point.x >= lateral_centre else -1].append(point)
    if not all(sides.values()):
        return []
    return [sum(points, Vector()) / len(points) for points in sides.values()]


def _group_hands(meshes, frame_world_to_src, lateral_centre):
    sums = {-1: [Vector(), 0.0], 1: [Vector(), 0.0]}
    for obj in meshes:
        names = {g.index: g.name for g in obj.vertex_groups
                 if _HAND_GROUP_RE.search(g.name) and not _NOT_HAND_GROUP_RE.search(g.name)}
        if not names:
            continue
        mw = obj.matrix_world
        per_group = {}
        for v in obj.data.vertices:
            for g in v.groups:
                if g.group in names and g.weight > 0.0:
                    entry = per_group.setdefault(g.group, [Vector(), 0.0])
                    entry[0] += (mw @ v.co) * g.weight
                    entry[1] += g.weight
        for total, weight in per_group.values():
            centre = frame_world_to_src @ (total / weight)
            side = 1 if centre.x >= lateral_centre else -1
            sums[side][0] += centre * weight
            sums[side][1] += weight
    if not all(weight > 0.0 for _total, weight in sums.values()):
        return []
    return [total / weight for total, weight in sums.values()]


def _height_scale(target, meshes, to_src, ground, src_height, lateral_centre, use_hands, armatures, posed):
    """The 'Default' scale fit: the overall height, pulled a little by hand
    height and spread when both sides have hands. Returns (scale, note)."""
    scale = target['height'] / src_height
    if not use_hands:
        return scale, "Hands: not used"
    if not target['hands']:
        return scale, "Hands: the armature has no hand bones, scaled by height only"
    src_hands = _source_hands(meshes, to_src, lateral_centre, armatures, posed)
    if not src_hands:
        return scale, "Hands: no hand/finger groups or bones on both sides, scaled by height only"
    s_up = sum(h.z - ground for h in src_hands) / 2
    s_side = sum(abs(h.x - lateral_centre) for h in src_hands) / 2
    t_up = sum(h.z for h in target['hands']) / 2
    t_side = sum(abs(h.x) for h in target['hands']) / 2
    pairs = [(target['height'], src_height, 1.0), (t_up, s_up, _HAND_WEIGHT), (t_side, s_side, _HAND_WEIGHT)]
    fitted = sum(w * a * b for a, b, w in pairs) / sum(w * b * b for _a, b, w in pairs)
    if abs(fitted / scale - 1.0) > _HAND_MAX_PULL:
        return scale, "Hands: too far from the armature's (pose or props?), scaled by height only"
    return fitted, "Hands: height and spread matched too"


def fit(arm_obj, meshes, scene_objects, depsgraph, up_choice='AUTO', front_choice='AUTO', use_hands=True,
        source_armatures=(), scale_fit='DEFAULT', source_posed=False):
    """The world matrix to left-multiply onto the selected objects, plus
    summary lines. *meshes*: the selected meshes, measured as displayed;
    *source_armatures*: their old rig, read for names and, where the meshes'
    groups lack them, hand and head positions (its bones as posed with
    *source_posed*, else at rest); *scale_fit*: a SCALE_FIT_ITEMS id."""
    target = _target(arm_obj, scene_objects)
    per_mesh = [_world_points(o, depsgraph) for o in meshes]
    points = np.vstack(per_mesh)
    if len(points) < 3:
        raise ValueError("The selected meshes have no vertices")
    hair = np.concatenate([
        mask if mask is not None and len(mask) == len(pts) else np.zeros(len(pts), dtype=bool)
        for pts, mask in zip(per_mesh, (_hair_mask(o, depsgraph) for o in meshes))])
    if hair.all():   # all of it named hair: the names say nothing
        hair[:] = False

    named = named_points(meshes, source_armatures, source_posed) if 'AUTO' in (up_choice, front_choice) else {}
    up, front, up_how, front_how = _source_frame(points, target['frame'], up_choice, front_choice, named)
    src_frame = _frame(front, up)
    src = points @ np.array(src_frame)  # columns: right, front, up
    ground = float(src[:, 2].min())
    src_height = float(src[:, 2].max()) - ground
    if src_height <= 0.0:
        raise ValueError(f"The selected meshes are flat along their up axis {axis_name(up)}")
    body = src[~hair]
    lateral_centre = float(body[:, 0].min() + body[:, 0].max()) / 2

    to_src = src_frame.transposed()
    scale, hand_note = _height_scale(target, meshes, to_src, ground, src_height, lateral_centre,
                                     use_hands, source_armatures, source_posed)
    # (source, target) depth that the arms fit lines up instead of the torso's.
    hand_depths = None
    if scale_fit == 'ARMS':
        src_hands = _source_hands(meshes, to_src, lateral_centre, source_armatures, source_posed)
        if not target['hands']:
            hand_note = "Arms fit: the armature has no hand bones, the overall height decides"
        elif not src_hands:
            hand_note = "Arms fit: no hand/finger groups or bones on both sides, the overall height decides"
        else:
            s_up = sum(h.z - ground for h in src_hands) / 2
            s_side = sum(abs(h.x - lateral_centre) for h in src_hands) / 2
            t_up = sum(h.z for h in target['hands']) / 2
            t_side = sum(abs(h.x) for h in target['hands']) / 2
            if s_up * s_up + s_side * s_side > 0.0:
                scale = (t_up * s_up + t_side * s_side) / (s_up * s_up + s_side * s_side)
                hand_depths = (sum(h.y for h in src_hands) / 2, sum(h.y for h in target['hands']) / 2)
                hand_note = "Arms fit: hand height, spread and depth line up with the armature's"
    elif scale_fit == 'HEAD':
        s_head = _source_head_joint(meshes, source_armatures, to_src, source_posed)
        if target['head'] is None:
            hand_note = "Head fit: the armature has no neck or head bone, the overall height decides"
        elif s_head is None or s_head - ground <= 0.0:
            hand_note = "Head fit: no 'head' vertex group or bone found, the overall height decides"
        else:
            scale = target['head'] / (s_head - ground)
            hand_note = "Head fit: the head meets the neck where the armature's does"

    if hand_depths is not None:
        # The hands' depth: hair, capes or a belly can shift the torso's.
        depth_centre, target_depth = hand_depths
    else:
        # Torso depth: the vertices between the target's pelvis and neck
        # heights, hair left out.
        in_band = (src[:, 2] >= ground + target['pelvis'] / scale) & (src[:, 2] <= ground + target['neck'] / scale)
        band = src[in_band & ~hair]
        depth_points = band if len(band) else (src[in_band] if in_band.any() else src)
        depth_centre = float(depth_points[:, 1].min() + depth_points[:, 1].max()) / 2
        target_depth = target['spine_depth']

    anchor_src = src_frame @ Vector((lateral_centre, depth_centre, ground))
    anchor_tgt = target['origin'] + target['frame'] @ Vector((0.0, target_depth, 0.0))
    rotation = target['frame'] @ src_frame.transposed()
    matrix = Matrix.Translation(anchor_tgt) @ rotation.to_4x4() @ Matrix.Scale(scale, 4) \
        @ Matrix.Translation(-anchor_src)

    how = {'set': "", 'names': " (from the rig's names)", 'guessed': " (guessed from the shape)"}
    lines = [
        f"Up {axis_name(up)}{how[up_how]}, facing {axis_name(front)}{how[front_how]}",
        f"Scale x{scale:.4g}: height {src_height * scale:.3g} (armature model {target['height']:.3g})",
        hand_note,
    ] + ([f"Hair ({int(hair.sum()):,} vertices) left out of the torso depth and sideways centre"]
         if hair.any() else []) + target['notes']
    return matrix, lines
