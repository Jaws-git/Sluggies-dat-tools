"""Bone collections by a bone's role in the original model.

bpy-free, so it is testable outside Blender. The importer
(``ImportSluggies.assign_bone_collections``) and Add Bone
(``SluggiesToolsPanel``) put every bone into the collections below. A bone
can be in several (a skinning bone that also owns a rigid mesh). Roles come
from the imported model's own data, per model: a Low model can skin other
bones than its High model.

- Skinning: named by the model's SKN (SK1/SK2/SKAcc).
- Rigid mesh: owns a rigid submesh (GEO ID other than 0xFFFF).
- A bone with neither has no vertices of its own and takes one of:
  - Tracked joint: has an animation track id and children (root, hip, neck,
    head chains).
  - Tracked tip: has a track id, no children (bat/glove attach points, toe
    tips). A track id does not mean a stock bank animates the bone.
  - Structural: no track id, has children (branch points such as the pelvis
    fork).
  - End tip: no track id, no children (finger and nose tips).
  - Marker: no parent and no children (free roots; on characters mostly
    track ids 71 and 131-135, purpose unknown).
- Added: bones the user appended (Add Bone); they get no original role.

Survey of the 163 character-sized skinned models in dt_na.dat (2026-10-24):
every role occurs. Only the unused Daisy prototype (directory 2,
111584_daisy.gpl) has bones that are both Skinning and Rigid mesh: 28 finger
bones that each own a rigid finger segment; the playable models have none.
Marker and End tip start hidden.
"""

SKINNING = 'Skinning'
RIGID_MESH = 'Rigid mesh'
TRACKED_JOINT = 'Tracked joint'
TRACKED_TIP = 'Tracked tip'
STRUCTURAL = 'Structural'
END_TIP = 'End tip'
MARKER = 'Marker'
ADDED = 'Added'

# Order of the collections in the Bone Collections panel.
COLLECTION_ORDER = (SKINNING, RIGID_MESH, TRACKED_JOINT, TRACKED_TIP, STRUCTURAL, END_TIP, MARKER, ADDED)
HIDDEN_BY_DEFAULT = frozenset({END_TIP, MARKER})

GEO_ID_FREE = 0xFFFF
NO_TRACK = 0xFFFF


def bone_collection(arm_data, name):
    """The bone collection *name* of *arm_data* (a bpy Armature), created
    when missing; hidden when *name* is in ``HIDDEN_BY_DEFAULT``."""
    collection = arm_data.collections.get(name)
    if collection is None:
        collection = arm_data.collections.new(name)
        collection.is_visible = name not in HIDDEN_BY_DEFAULT
    return collection


def bone_roles(*, skinned, geo_id_raw, track_id, has_parent, has_children, user_added=False):
    """The collections of one bone, in ``COLLECTION_ORDER``."""
    owns_mesh = int(geo_id_raw) != GEO_ID_FREE
    if user_added:
        roles = [ADDED]
        if skinned:
            roles.insert(0, SKINNING)
        if owns_mesh:
            roles.insert(1 if skinned else 0, RIGID_MESH)
        return roles
    roles = []
    if skinned:
        roles.append(SKINNING)
    if owns_mesh:
        roles.append(RIGID_MESH)
    if roles:
        return roles
    tracked = int(track_id) != NO_TRACK
    if not has_parent and not has_children:
        return [MARKER]
    if tracked:
        return [TRACKED_JOINT if has_children else TRACKED_TIP]
    return [STRUCTURAL if has_children else END_TIP]
