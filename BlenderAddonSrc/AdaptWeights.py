"""The 'Adapt' box's Map Weights or Autorig step: weights the skinned main
body to the Sluggies bones, one of two ways.

- Map: each vertex group the body brought from its own rig goes to the
  weight bone most of its weight lies nearest to (distance to the bone's
  head-tail segment, rest pose); one on the middle line but nearest limbs
  of both sides goes to the nearest middle bone. A group alone on its bone
  is renamed; several on one bone are added together, as a Vertex Weight Mix
  modifier would, but through the vertex group API: applying a modifier is
  blocked by shape keys. Groups another modifier uses stay as they are. How
  well it works depends on how well the mesh was fitted to the skeleton.
- Autorig: Blender's automatic weights (bone heat) against the weight bones
  only. Vertices it leaves empty (heat finds no solution on some meshes)
  take weights from their distance to the nearest bones.

Weight bones: the bones the donor's body is skinned to (SkinData), animated
bones owning a rigid mesh (a head or hand the new body may cover) and added
bones that host no custom submesh. Tips, markers, structural bones and the
long root-to-pelvis joints would grab vertices without moving them usefully.

Vertices that already carry a weight on a bone of the armature (a donor body
the new mesh was joined into) keep it, unless Autorig is told to redo the
whole body.
"""

import re

import numpy as np

from . import BoneRoles, ModelCheckCore


# Map is offered from this many weighted groups of the old rig on, covering
# at least this share of the vertices still to weight.
MIN_SOURCE_GROUPS = 4
MIN_SOURCE_COVERAGE = 0.5

MAX_INFLUENCES = ModelCheckCore.MAX_BONE_INFLUENCES

# A group with at least this share of its weight nearest left bones and as
# much nearest right ones lies on the middle line between limbs (hair, a
# tail, a cape): it goes to the nearest middle bone instead.
_STRADDLE_SHARE = 0.25

# Name rules (words of the old rig's group names). Face and hair parts follow
# the 'head' group's bone, fingers the nearest 'hand' group's, tails the hips'.
_FACE_TOKENS = frozenset({
    'face', 'facial', 'eye', 'eyes', 'eyeball', 'brow', 'eyebrow', 'lid', 'eyelid', 'lash', 'nose', 'nostril',
    'mouth', 'lip', 'lips', 'jaw', 'chin', 'cheek', 'tongue', 'teeth', 'tooth', 'ear', 'ears', 'hair', 'bang',
    'bangs', 'pony', 'ponytail', 'braid', 'pigtail', 'hat',
})
_FINGER_TOKENS = frozenset({'finger', 'fingers', 'thumb', 'index', 'middle', 'ring', 'pinky', 'little', 'palm'})
_TAIL_TOKENS = frozenset({'tail'})
_HIP_TOKENS = frozenset({'hip', 'hips', 'pelvis', 'waist'})

# Mean group-to-bone distance (share of the skeleton's height) above which
# the mesh is reported as not fitted to the skeleton. A stock body on its own
# skeleton: 0.012; a hand-fitted port: 0.02-0.03.
_POOR_FIT = 0.05

# Distance fallback: bones up to this factor of the nearest one's distance
# share a vertex (the two bones meeting at a joint), weighted 1/d^4.
_FALLBACK_BLEND = 1.25
_FALLBACK_POWER = 4

# Bones listed by name in a summary line; more are only counted.
_LIST_MAX = 6


def _bone_names(arm_obj):
    return {b.name for b in arm_obj.data.bones}


def _custom_submesh_hosts(arm_obj):
    """Names of the bones a custom submesh of *arm_obj* hangs on."""
    bones = _bone_names(arm_obj)
    return {g.name for obj in arm_obj.children if obj.type == 'MESH' and obj.get('SluggiesCustomSubmesh')
            for g in obj.vertex_groups if g.name in bones}


def weight_bones(arm_obj):
    """The bones a body vertex may be weighted to (module docstring), in
    armature order. Falls back to every bone_<id> bone with a parent or
    children when the donor skins none."""
    hosts = _custom_submesh_hosts(arm_obj)
    named = [b for b in arm_obj.data.bones if ModelCheckCore.bone_id_from_name(b.name) is not None]
    chosen = []
    for bone in named:
        tracked = int(bone.get('track_id', BoneRoles.NO_TRACK)) != BoneRoles.NO_TRACK
        owns_mesh = int(bone.get('SluggiesGeoIdRaw', BoneRoles.GEO_ID_FREE)) != BoneRoles.GEO_ID_FREE
        if bone.get('SluggiesUserAdded'):
            if bone.name not in hosts:
                chosen.append(bone)
        elif bone.get('SluggiesSkinned') or (owns_mesh and tracked):
            chosen.append(bone)
    if not any(b.get('SluggiesSkinned') for b in chosen):
        chosen = [b for b in named if b.parent is not None or b.children]
    return chosen


def _modifier_groups(obj):
    """Vertex groups *obj*'s modifiers use by name (masks, the Armature
    modifier's own vertex_group): never renamed or removed."""
    used = set()
    for mod in obj.modifiers:
        for prop in ('vertex_group', 'vertex_group_a', 'vertex_group_b'):
            name = getattr(mod, prop, '')
            if name:
                used.add(name)
    return used


def _read_weights(obj):
    """Per vertex: {group name: weight} of its positive weights."""
    names = {g.index: g.name for g in obj.vertex_groups}
    return [{names[g.group]: g.weight for g in v.groups if g.weight > 0.0 and g.group in names}
            for v in obj.data.vertices]


def _source_groups(obj, bone_names, weights):
    """Names of *obj*'s weighted vertex groups that are not a bone of the
    armature and no modifier uses: the old rig's."""
    used = _modifier_groups(obj)
    weighted = {name for vertex in weights for name in vertex}
    return [g.name for g in obj.vertex_groups
            if g.name in weighted and g.name not in bone_names and g.name not in used]


def survey(obj, arm_obj):
    """What the body *obj* holds: dict with vertices, open (vertices without a
    weight on a bone of the armature), sources (old rig groups), covered
    (open vertices weighted by one) and mappable."""
    bone_names = _bone_names(arm_obj)
    weights = _read_weights(obj)
    sources = _source_groups(obj, bone_names, weights)
    source_set = set(sources)
    open_vertices = [w for w in weights if not any(name in bone_names for name in w)]
    covered = sum(1 for w in open_vertices if any(name in source_set for name in w))
    mappable = len(sources) >= MIN_SOURCE_GROUPS and (
        not open_vertices or covered >= MIN_SOURCE_COVERAGE * len(open_vertices))
    return dict(vertices=len(weights), open=len(open_vertices), sources=len(sources),
                covered=covered, mappable=mappable)


def _world_points(obj):
    """*obj*'s rest vertex positions in world space (Nx3)."""
    co = np.empty(len(obj.data.vertices) * 3, dtype=np.float64)
    obj.data.vertices.foreach_get('co', co)
    m = np.array(obj.matrix_world)
    return co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]


def _segment_distances(points, arm_obj, bones):
    """(N x len(bones)) distances from *points* to each bone's rest
    head-tail segment, in world space."""
    m = np.array(arm_obj.matrix_world)
    distances = np.empty((len(points), len(bones)), dtype=np.float64)
    for column, bone in enumerate(bones):
        head = m[:3, :3] @ np.array(bone.head_local) + m[:3, 3]
        tail = m[:3, :3] @ np.array(bone.tail_local) + m[:3, 3]
        axis = tail - head
        length2 = float(axis @ axis)
        rel = points - head
        t = np.clip(rel @ axis / length2, 0.0, 1.0) if length2 > 0.0 else np.zeros(len(points))
        distances[:, column] = np.linalg.norm(rel - t[:, None] * axis, axis=1)
    return distances


def _set_weights(obj, vertex, new, bone_names):
    """Make *new* ({bone group name: weight}) the vertex's only weights on
    bone groups, creating missing groups."""
    groups = obj.vertex_groups
    for g in obj.data.vertices[vertex].groups:
        group = groups[g.group]
        if group.name in bone_names and group.name not in new:
            group.remove([vertex])
    for name, weight in new.items():
        group = groups.get(name) or groups.new(name=name)
        group.add([vertex], weight, 'REPLACE')


def _limit_influences(obj, vertices, bone_names):
    """Keep the MAX_INFLUENCES strongest bone weights (normalized) of each
    of *vertices* that has more. Returns how many were limited."""
    limited = 0
    weights = _read_weights(obj)
    for vertex in vertices:
        on_bones = {name: w for name, w in weights[vertex].items() if name in bone_names}
        if len(on_bones) > MAX_INFLUENCES:
            _set_weights(obj, vertex, ModelCheckCore.limited_weights(on_bones, MAX_INFLUENCES), bone_names)
            limited += 1
    return limited


def _bone_sides(bones):
    """Per bone: -1 or 1 for a bone on one side of the armature's mirror
    plane (its X), 0 for one on it."""
    spread = max((abs(b.head_local.x) for b in bones), default=0.0)
    tolerance = max(spread * 1e-3, 1e-6)
    sides = []
    for bone in bones:
        x = (bone.head_local.x + bone.tail_local.x) / 2
        on_plane = abs(bone.head_local.x) <= tolerance and abs(bone.tail_local.x) <= tolerance
        sides.append(0 if on_plane else (1 if x > 0 else -1))
    return np.array(sides)


def _votes(vertex_weights, column_of):
    """{bone column: summed weight} of a group's vertices by *column_of*."""
    votes = {}
    for vertex, weight in vertex_weights.items():
        column = column_of[vertex]
        votes[column] = votes.get(column, 0.0) + weight
    return votes


def name_tokens(name):
    """Lower-case words of a vertex group name: 'JNT_L_EyeBrowExt2' ->
    {'jnt', 'l', 'eye', 'brow', 'ext', '2'}."""
    spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    spaced = re.sub(r'([A-Za-z])(\d)|(\d)([A-Za-z])', r'\1\3 \2\4', spaced)
    return {t for t in re.split(r'[^a-z0-9]+', spaced.lower()) if t}


def _centroid(points, vertex_weights):
    vertices = list(vertex_weights)
    w = np.array([vertex_weights[v] for v in vertices])
    return (points[vertices] * w[:, None]).sum(axis=0) / w.sum()


def _within(arm_obj, bone_name, anchor_name):
    """Whether *bone_name* is *anchor_name* or one of its descendants."""
    bone = arm_obj.data.bones.get(bone_name)
    while bone is not None:
        if bone.name == anchor_name:
            return True
        bone = bone.parent
    return False


def _deepest_strong_bone(arm_obj, votes):
    """The bone of *votes* furthest down the hierarchy among those with a
    real share, when they lie on one chain (a head split between neck and
    head bones goes to the head); else the strongest."""
    total = sum(votes.values())
    strong = [b for b, w in votes.items() if w >= _STRADDLE_SHARE * total]
    for bone in strong:
        if all(_within(arm_obj, bone, other) for other in strong):
            return bone
    return max(votes, key=votes.get)


def _family(tokens):
    if tokens & _FACE_TOKENS:
        return 'face'
    if tokens & _FINGER_TOKENS:
        return 'finger'
    if tokens & _TAIL_TOKENS:
        return 'tail'
    return None


def _apply_name_rules(arm_obj, mapping, votes_of, centroids, side_of):
    """Correct *mapping* in place by the old rig's group names: face and hair
    parts go with the head, fingers with the nearest hand, tails with the
    hips, unless they already sit on that bone or below it (a Sluggers bone
    of their own; a tail only on a middle one). Returns {'moved': count,
    'appendages': names of face/hair and tail groups}."""
    tokens = {name: name_tokens(name) for name in mapping}
    family = {name: _family(t) for name, t in tokens.items()}

    def heaviest(names):
        return max(names, key=lambda n: sum(votes_of[n].values()), default=None)

    head = heaviest([n for n, t in tokens.items() if 'head' in t and family[n] is None])
    if head is not None:
        mapping[head] = _deepest_strong_bone(arm_obj, votes_of[head])
    hands = [n for n, t in tokens.items() if 'hand' in t and family[n] is None]
    hips = heaviest([n for n, t in tokens.items() if t & _HIP_TOKENS and family[n] is None])

    moved = 0
    for name, kind in family.items():
        if kind == 'face':
            anchor = mapping[head] if head is not None else None
            keep = anchor is not None and _within(arm_obj, mapping[name], anchor)
        elif kind == 'finger':
            hand = min(hands, key=lambda h: np.linalg.norm(centroids[h] - centroids[name]), default=None)
            anchor = mapping[hand] if hand is not None else None
            keep = anchor is not None and _within(arm_obj, mapping[name], anchor)
        elif kind == 'tail':
            anchor = mapping[hips] if hips is not None else None
            keep = anchor is not None and _within(arm_obj, mapping[name], anchor) \
                and side_of.get(mapping[name], 0) == 0
        else:
            continue
        if anchor is not None and not keep and mapping[name] != anchor:
            mapping[name] = anchor
            moved += 1
    return dict(moved=moved, appendages={n for n, kind in family.items() if kind in ('face', 'tail')})


def _fit(arm_obj, bones, mapping, centroids, members, skip=()):
    """How well the mesh sits on the skeleton: the weighted mean distance
    from each group's centre to its bone, as a share of the skeleton's
    height. Hair, face parts and tails are left out: they hang away from
    their bones by nature."""
    m = np.array(arm_obj.matrix_world)
    ends = np.array([m[:3, :3] @ np.array(p) + m[:3, 3] for b in bones for p in (b.head_local, b.tail_local)])
    height = float((ends.max(axis=0) - ends.min(axis=0)).max()) if len(ends) else 0.0
    by_name = {b.name: b for b in bones}
    total = weighted = 0.0
    for name, bone in mapping.items():
        if name in skip or bone not in by_name:
            continue
        weight = sum(members[name].values())
        distance = _segment_distances(centroids[name][None, :], arm_obj, [by_name[bone]])[0, 0]
        weighted += distance * weight
        total += weight
    return weighted / total / height if total > 0.0 and height > 0.0 else 0.0


def map_groups(obj, arm_obj):
    """Map the old rig's vertex groups of *obj* onto the weight bones
    (module docstring). Returns a result dict for summary_lines."""
    bones = weight_bones(arm_obj)
    if not bones:
        raise ValueError(f"{arm_obj.name} has no bones to weight to")
    bone_names = _bone_names(arm_obj)
    weights = _read_weights(obj)
    sources = _source_groups(obj, bone_names, weights)
    members = {name: {} for name in sources}
    for vertex, vertex_weights in enumerate(weights):
        for name, weight in vertex_weights.items():
            if name in members:
                members[name][vertex] = weight

    involved = sorted({v for m in members.values() for v in m})
    sides = _bone_sides(bones)
    middle = np.flatnonzero(sides == 0)
    points = _world_points(obj)
    nearest = nearest_middle = np.empty(0, dtype=np.int64)
    if involved:
        distances = _segment_distances(points[involved], arm_obj, bones)
        nearest = distances.argmin(axis=1)
        if len(middle):
            nearest_middle = middle[distances[:, middle].argmin(axis=1)]
    nearest_of = dict(zip(involved, nearest.tolist()))
    middle_of = dict(zip(involved, nearest_middle.tolist()))

    mapping, votes_of = {}, {}
    for name, vertex_weights in members.items():
        votes = _votes(vertex_weights, nearest_of)
        total = sum(votes.values())
        straddles = all(sum(w for column, w in votes.items() if sides[column] == side) >= _STRADDLE_SHARE * total
                        for side in (-1, 1))
        if straddles and middle_of:
            votes = _votes(vertex_weights, middle_of)
        votes_of[name] = {bones[column].name: w for column, w in votes.items()}
        mapping[name] = max(votes_of[name], key=votes_of[name].get)

    centroids = {name: _centroid(points, vertex_weights) for name, vertex_weights in members.items()}
    side_of = {bone.name: int(side) for bone, side in zip(bones, sides)}
    by_name = _apply_name_rules(arm_obj, mapping, votes_of, centroids, side_of)
    fit = _fit(arm_obj, bones, mapping, centroids, members, skip=by_name['appendages'])

    by_bone = {}
    for name, bone in mapping.items():
        by_bone.setdefault(bone, []).append(name)
    groups = obj.vertex_groups
    renamed = merged = 0
    for bone, names in by_bone.items():
        target = groups.get(bone)
        if target is None:
            target = groups[names[0]]
            target.name = bone
            names = names[1:]
            renamed += 1
        for name in names:
            for vertex, weight in members[name].items():
                target.add([vertex], weight, 'ADD')
            groups.remove(groups[name])
            merged += 1

    limited = _limit_influences(obj, involved, bone_names)
    return dict(kind='MAP', object=obj.name, mapping=mapping, groups=len(mapping), bones=len(by_bone),
                renamed=renamed, merged=merged, limited=limited, by_name=by_name['moved'], fit=fit,
                kept=len(_modifier_groups(obj) & {g.name for g in groups} - bone_names))


def autorig(context, obj, arm_obj, whole_body=False, remove_old_groups=True):
    """Weight *obj* to the weight bones with Blender's automatic weights,
    with a distance fallback (module docstring). The caller fixes up the
    Armature modifier and parenting afterwards. Returns a result dict for
    summary_lines."""
    import bpy

    bones = weight_bones(arm_obj)
    if not bones:
        raise ValueError(f"{arm_obj.name} has no bones to weight to")
    bone_names = _bone_names(arm_obj)
    weights = _read_weights(obj)
    sources = _source_groups(obj, bone_names, weights)

    # Vertices that keep their bone weights, restored after the operator.
    kept = {} if whole_body else {
        vertex: {name: w for name, w in vertex_weights.items() if name in bone_names}
        for vertex, vertex_weights in enumerate(weights)
        if any(name in bone_names for name in vertex_weights)
    }
    to_weight = [v for v in range(len(weights)) if v not in kept]
    if not to_weight:
        raise ValueError(f"Every vertex of {obj.name} has Sluggers bone weights already: "
                         "tick 'Redo the whole body' to replace them")

    deform = {b.name: b.use_deform for b in arm_obj.data.bones}
    chosen = {b.name for b in bones}
    heat_error = None
    try:
        for bone in arm_obj.data.bones:
            bone.use_deform = bone.name in chosen
        with context.temp_override(active_object=arm_obj, object=arm_obj,
                                   selected_objects=[obj, arm_obj], selected_editable_objects=[obj, arm_obj]):
            bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    except RuntimeError as exc:
        heat_error = str(exc).strip().splitlines()[-1] if str(exc).strip() else "failed"
    finally:
        for bone in arm_obj.data.bones:
            bone.use_deform = deform.get(bone.name, bone.use_deform)

    for vertex, old in kept.items():
        _set_weights(obj, vertex, old, bone_names)

    # Vertices heat weighting left without a bone weight.
    weights = _read_weights(obj)
    empty = [v for v in to_weight if not any(name in bone_names for name in weights[v])]
    if empty:
        distances = _segment_distances(_world_points(obj)[empty], arm_obj, bones)
        order = np.argsort(distances, axis=1)[:, :MAX_INFLUENCES]
        near = np.take_along_axis(distances, order, axis=1)
        reach = near[:, :1] * _FALLBACK_BLEND + 1e-9
        raw = np.where(near <= reach, 1.0 / np.maximum(near, 1e-9) ** _FALLBACK_POWER, 0.0)
        raw /= raw.sum(axis=1, keepdims=True)
        for row, vertex in enumerate(empty):
            _set_weights(obj, vertex, {bones[c].name: float(w) for c, w in zip(order[row], raw[row]) if w > 0.0},
                         bone_names)

    removed = 0
    if remove_old_groups:
        for name in sources:
            group = obj.vertex_groups.get(name)
            if group is not None:
                obj.vertex_groups.remove(group)
                removed += 1
    limited = _limit_influences(obj, to_weight, bone_names)
    return dict(kind='AUTO', object=obj.name, weighted=len(to_weight), kept_vertices=len(kept),
                bones=len(bones), fallback=len(empty), heat_error=heat_error, removed=removed,
                limited=limited)


def unweighted(objects, arm_obj):
    """(skinning bones no vertex of *objects* weights, vertices without any
    bone weight as {object name: count})."""
    bone_names = _bone_names(arm_obj)
    used, bare = set(), {}
    for obj in objects:
        for vertex_weights in _read_weights(obj):
            on_bones = [name for name in vertex_weights if name in bone_names]
            used.update(on_bones)
            if not on_bones:
                bare[obj.name] = bare.get(obj.name, 0) + 1
    expected = [b.name for b in weight_bones(arm_obj) if b.get('SluggiesSkinned') or b.get('SluggiesUserAdded')]
    return [name for name in expected if name not in used], bare


def _names(names):
    shown = ", ".join(names[:_LIST_MAX])
    return shown + (f" and {len(names) - _LIST_MAX} more" if len(names) > _LIST_MAX else "")


def summary_lines(result):
    """Summary lines for map_groups's or autorig's *result*."""
    p = ModelCheckCore.plural
    if result['kind'] == 'MAP':
        lines = [f"{result['object']}: mapped {p(result['groups'], 'vertex group')} onto "
                 f"{p(result['bones'], 'bone')} ({result['renamed']} renamed, {result['merged']} merged in)"]
        if result['by_name']:
            lines.append(f"{p(result['by_name'], 'group')} placed by name: face and hair with the head, "
                         "fingers with the hand, tails with the hips")
        if result['kept']:
            lines.append(f"Left as they were: {p(result['kept'], 'vertex group')} a modifier uses")
    else:
        lines = [f"{result['object']}: weighted {p(result['weighted'], 'vertex', 'vertices')} automatically "
                 f"to up to {p(result['bones'], 'bone')}"]
        if result['kept_vertices']:
            lines.append(f"Kept the Sluggers weights of {p(result['kept_vertices'], 'vertex', 'vertices')}")
        if result['heat_error']:
            lines.append(f"Blender's automatic weights failed ({result['heat_error']})")
        if result['fallback']:
            lines.append(f"{p(result['fallback'], 'vertex', 'vertices')} without a heat solution "
                         "weighted by distance to the nearest bones")
        if result['removed']:
            lines.append(f"Removed {p(result['removed'], 'vertex group')} of the old rig")
    if result['limited']:
        lines.append(f"{p(result['limited'], 'vertex', 'vertices')} limited to the {MAX_INFLUENCES} "
                     "strongest bones")
    return lines


def warning_lines(objects, arm_obj, results=()):
    """Warnings about a poor fit (Map's *results*) and what is left
    unweighted after either way."""
    bones, bare = unweighted(objects, arm_obj)
    lines = []
    for result in results:
        if result.get('fit', 0.0) > _POOR_FIT:
            lines.append(f"{result['object']} does not sit well on the skeleton, so it may bend oddly")
            lines.append(f"(its parts are on average {result['fit']:.0%} of the height away from their bones): "
                         "undo, fit the mesh to the armature and map again")
    if bones:
        lines.append(f"No weights on {len(bones)} skinning bone{'s' if len(bones) != 1 else ''}: "
                     f"{_names(bones)}")
    for name, count in bare.items():
        lines.append(f"{name}: {ModelCheckCore.plural(count, 'vertex', 'vertices')} without a bone weight")
    if bare:
        lines.append("Find them with 'Select Unweighted Vertices' (Skinned mesh box)")
    return lines
