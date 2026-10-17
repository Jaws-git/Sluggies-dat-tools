"""Scaling a whole character through its animation banks.

bpy-free, so it is testable outside Blender. The Blender operator
(``SluggiesToolsPanel.SLUGGIES_OT_scale_animations``) and the ``.anm`` export
(``AnmExport``) share these facts:

The game applies an ANM scale channel in the bone's own frame and inherits it
down the hierarchy, except at a few bones it re-solves itself (spine, hands,
neck, head: two-hand grip, look-at, ...). A re-solved bone is placed at its
parent's scaled offset with the parent's orientation, but takes only its own
scale. The game addresses those bones by animation track id, and the track
ids are role ids shared by every character skeleton, so the set below holds
for every character (confirmed in Dolphin on Mario, 2026-10-17: a scale
channel on exactly these six bones scales the whole body once; the bat is
attached through its own matrix and does not follow).

In Blender the same behaviour is ``Bone.inherit_scale = 'NONE'`` on those
bones (position follows the parent's scale, orientation and scale do not).
"""

# Track ids of the bones the game re-solves: 5 pelvis, 6 spine root, 9 neck,
# 10 head, 0x13 right hand, 0x19 left hand. A skeleton may lack some (several
# have no head track); the ones it has are its "layer roots".
LAYER_ROOT_TRACK_IDS = frozenset({5, 6, 9, 10, 0x13, 0x19})

# A bone axis this far off every world axis (cosine) gets a warning for
# non-uniform factors: the nearest-axis mapping below is then a guess.
MIN_ALIGNMENT = 0.866   # cos 30 deg


def is_layer_root(track_id):
    try:
        return int(track_id) in LAYER_ROOT_TRACK_IDS
    except (TypeError, ValueError):
        return False


def layer_roots(track_id_by_bone):
    """{bone name: track id} of the layer roots among *track_id_by_bone*
    ({bone name: track id}), sorted by track id."""
    found = {name: int(tid) for name, tid in track_id_by_bone.items() if is_layer_root(tid)}
    return dict(sorted(found.items(), key=lambda item: item[1]))


def is_uniform(factors, tol=1e-6):
    return max(factors) - min(factors) <= tol


def nearest_axes(axes):
    """For each local axis the index of the world axis it points along most.

    *axes*: 3x3 matrix as rows, mapping local to world (``axes[j][i]`` is the
    world-``j`` component of local axis ``i``)."""
    return [max(range(3), key=lambda j: abs(axes[j][i])) for i in range(3)]


def alignment(axes):
    """How close *axes* is to an axis permutation: the smallest, over the local
    axes, of the largest absolute component (1.0 = exact permutation)."""
    return min(max(abs(axes[j][i]) for j in range(3)) for i in range(3))


def local_factors(axes, factors):
    """The per-axis scale *factors* (given along the world axes) expressed
    along the bone's local axes: each local axis takes the factor of the
    world axis it points along most. Exact for an axis permutation and for
    uniform factors; otherwise the nearest permutation."""
    if is_uniform(factors):
        return [float(factors[0])] * 3
    return [float(factors[j]) for j in nearest_axes(axes)]
