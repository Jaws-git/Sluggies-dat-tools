"""The 'Adapt' box's Generate Lowpoly step: builds the Low (`_L_`) model from
the High one, with both imported into the scene.

Each Sluggies mesh of the High model gets a mode: left out (Ignore), copied
as it is (Low unchanged), or copied and thinned by a Decimate modifier
(Un-Subdivide iterations, optionally after Triangles to Quads, or Collapse
ratio) that is applied at once. The copies then go to the Low armature
(SluggiesToolsPanel does that part: it owns the body and submesh helpers):

- the skinned main body replaces the Low model's body (submesh 0);
- a rigid mesh on bone N replaces the Low model's own mesh on bone N, or
  becomes an added (custom) submesh there when that bone is free.

Every copy carries LOW_SOURCE_PROP (its High object's name), so a second run
replaces what the first one made. The two models share their bone ids; bones
added on the High model are added to the Low model too, in the same order, so
their ids line up.
"""

import math
import os

import bmesh
import bpy

from . import ModelCheckCore


IGNORE, UNCHANGED, UNSUBDIV, COLLAPSE = 'IGNORE', 'UNCHANGED', 'UNSUBDIV', 'DECIMATE'
MODES = (
    (IGNORE, "Ignore", "Leave this mesh out of the Low model"),
    (UNCHANGED, "Low unchanged", "Copy the mesh to the Low model as it is"),
    (UNSUBDIV, "Unsubdivide",
     "Decimate (Un-Subdivide): undo grid-like subdivision, the given number of iterations"),
    (COLLAPSE, "Decimate ratio",
     "Decimate (Collapse): keep this share of the faces, merging the rest away"),
)
UNSUBDIV_ITERATIONS = (
    ('2', "2", "Un-Subdivide with 2 iterations"),
    ('4', "4", "Un-Subdivide with 4 iterations"),
)
# Face and shape angle limits of Triangles to Quads before Un-Subdivide.
TRIS_TO_QUADS_ANGLE = math.radians(60.0)
# Meshes with fewer vertices start as Low unchanged; bigger ones are decimated.
UNCHANGED_BELOW = 1000
DEFAULT_RATIO = 0.5

LOW_SOURCE_PROP = 'SluggiesLowSource'


def default_mode(vertex_count):
    return UNCHANGED if vertex_count < UNCHANGED_BELOW else COLLAPSE


def _folder(path):
    return os.path.basename(os.path.dirname(os.path.abspath(path)))


def is_low_model(sluggie_path):
    return ModelCheckCore.is_low_folder(_folder(sluggie_path))


def low_partner(high_path, candidates):
    """The `_L_` partner of the High model at *high_path* among *candidates*
    ({.sluggie path: value}), by the folder rule the exporter pairs them
    with; when several match, the one ModelCheckCore.pick_partner picks.
    None when there is none (or no single one)."""
    if not high_path or is_low_model(high_path):
        return None
    high_folder = _folder(high_path)
    paths = [path for path in candidates
             if ModelCheckCore.low_partner_matches(_folder(path), high_folder)]
    if len(paths) > 1:
        picked = ModelCheckCore.pick_partner(paths, high_path)
        paths = [picked] if picked is not None else []
    return candidates[paths[0]] if len(paths) == 1 else None


def added_bones(arm_obj):
    """*arm_obj*'s Add Bone bones, in creation order."""
    return sorted((b for b in arm_obj.data.bones if b.get('SluggiesUserAdded')),
                  key=lambda b: int(b.get('SluggiesCreationOrder', 0)))


def bones_to_mirror(high, low):
    """The High model's added bones the Low model lacks, in the order they
    must be added so they get the same ids there. Raises ValueError when the
    two skeletons differ in a way adding bones cannot fix."""
    def original_count(arm_obj):
        return sum(1 for b in arm_obj.data.bones if not b.get('SluggiesUserAdded'))

    if original_count(high) != original_count(low):
        raise ValueError(f"{high.name} and {low.name} do not share their original bones")
    high_added = added_bones(high)
    low_names = [b.name for b in added_bones(low)]
    if [b.name for b in high_added[:len(low_names)]] != low_names:
        raise ValueError(f"The bones added on {low.name} ({', '.join(low_names)}) are not the first ones "
                         f"added on {high.name}: give both models the same added bones first")
    return high_added[len(low_names):]


def copy_object(obj, collection):
    """A copy of *obj* with its own mesh and materials (the High model's stay
    untouched), linked to *collection*, visible, without Armature modifiers
    (the caller points one at the Low armature) and without parent, where
    *obj* is in the world."""
    copy = obj.copy()
    copy.data = obj.data.copy()
    collection.objects.link(copy)
    copy.parent = None
    copy.matrix_world = obj.matrix_world.copy()
    copy.hide_viewport = False
    try:
        copy.hide_set(False)
    except RuntimeError:   # *collection* is not in the view layer
        pass
    for mod in [m for m in copy.modifiers if m.type == 'ARMATURE']:
        copy.modifiers.remove(mod)
    copies = {}
    for slot in copy.material_slots:
        mat = slot.material
        if mat is not None:
            if mat not in copies:
                copies[mat] = mat.copy()
            slot.material = copies[mat]
    return copy


def tris_to_quads(mesh):
    """Join *mesh*'s triangles into quads, like Edit Mode's Triangles to
    Quads with TRIS_TO_QUADS_ANGLE limits, full topology influence and
    UVs, seams, sharp edges and materials compared. Returns the faces joined."""
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        before = len(bm.faces)
        bmesh.ops.join_triangles(
            bm, faces=bm.faces[:], cmp_seam=True, cmp_sharp=True, cmp_uvs=True, cmp_materials=True,
            angle_face_threshold=TRIS_TO_QUADS_ANGLE, angle_shape_threshold=TRIS_TO_QUADS_ANGLE,
            topology_influence=1.0)
        joined = before - len(bm.faces)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    return joined


def decimate(context, obj, mode, iterations=2, ratio=DEFAULT_RATIO, quads=False):
    """Thin *obj*'s mesh in place: a Decimate modifier (*mode* UNSUBDIV or
    COLLAPSE), first in the stack and applied; with *quads*, Un-Subdivide
    runs on the mesh after tris_to_quads (it finds no grid in triangles).
    Shape keys block applying a modifier, so they are removed first.
    Returns notes."""
    if mode not in (UNSUBDIV, COLLAPSE):
        return []
    notes = []
    if obj.data.shape_keys is not None:
        obj.shape_key_clear()
        notes.append(f"{obj.name}: shape keys removed to decimate it")
    if mode == UNSUBDIV and quads:
        tris_to_quads(obj.data)
    mod = obj.modifiers.new(name="Lowpoly Decimate", type='DECIMATE')
    if mode == UNSUBDIV:
        mod.decimate_type = 'UNSUBDIV'
        mod.iterations = int(iterations)
    else:
        mod.decimate_type = 'COLLAPSE'
        mod.ratio = ratio
    with context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                               selected_editable_objects=[obj]):
        if len(obj.modifiers) > 1:
            bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=0)
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return notes


def remove_object(obj):
    """Delete *obj*, and its mesh when nothing else uses it."""
    mesh = obj.data
    bpy.data.objects.remove(obj)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
