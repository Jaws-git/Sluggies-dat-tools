"""Export, bones: the root bone scale, the edited bone hierarchy (added bones, SRT types, track ids) with its
validation, and the bone of a rigid submesh (vertex group helpers, ``encode_unskinned_bone_reassignments``,
rigid retarget candidates, the donor bone records of an armature).

Imports ``ExportInplaceMesh`` only among the ``Export*`` modules, and never ``ExportSluggies``.
"""

import re
from .HostBones import compute_rigid_retargets, GEO_ID_FREE
from . import AnmExport
from . import CustomSubmeshExport
from . import RigidRebuildExport
from . import TemplateSources
from . import HostBones
from .ExportInplaceMesh import (
    ExportCancelled,
)


def _find_root_scale_armature(candidates, context):
    """Return the imported armature object that carries the ``RootBoneScale``
    custom property, or None.

    The importer parents every skinned mesh to the armature, so the primary
    lookup walks up each candidate's parent chain. A scene-wide fallback covers
    the edge case where the mesh was reparented or the armature is simply
    selected without its meshes.
    """
    for obj in candidates:
        node = obj
        while node is not None:
            if node.type == 'ARMATURE' and "RootBoneScale" in node:
                return node
            node = node.parent
    for obj in context.scene.objects:
        if obj.type == 'ARMATURE' and "RootBoneScale" in obj:
            return obj
    return None


def encode_root_bone_scale_edited(candidates, data, warnings, context):
    """Read the armature's ``RootBoneScale`` and, when it differs from the
    unit scale [1, 1, 1], write it to ``SluggiesModel.RootBoneScaleEdited``.

    The field is popped when the scale is unchanged so an untouched import
    never writes it back. Returns the written 3-element scale list, or None
    when nothing was written (no armature found, or scale already unit).
    """
    model = data.get("SluggiesModel", {})
    arm_obj = _find_root_scale_armature(candidates, context)
    if arm_obj is None:
        model.pop("RootBoneScaleEdited", None)
        return None

    scale = arm_obj["RootBoneScale"]
    if scale is None or len(scale) != 3:
        warnings.append(
            f"{arm_obj.name}: RootBoneScale must be a 3-element vector; "
            "root-bone scale export skipped."
        )
        model.pop("RootBoneScaleEdited", None)
        return None

    value = [float(scale[0]), float(scale[1]), float(scale[2])]
    if value == [1.0, 1.0, 1.0]:
        model.pop("RootBoneScaleEdited", None)
        return None

    model["RootBoneScaleEdited"] = value
    return value


_BONE_NAME_RE = re.compile(r'^bone_(\d+)$')


def _bone_id_from_bone_name(name):
    """bone_<id> -> id, else None. Mirrors SceneModels._bone_id_from_name;
    duplicated here rather than imported to avoid a circular import (that
    module imports from this one)."""
    m = _BONE_NAME_RE.match(name or '')
    return int(m.group(1)) if m else None


def armature_has_new_bones(candidates, context):
    """Whether the .sluggie's armature carries any Add Bone bone; such a
    model is always exported in Hammerspace mode."""
    arm_obj = _find_root_scale_armature(candidates, context)
    if arm_obj is None:
        return False
    return any(b.get('SluggiesUserAdded') for b in arm_obj.data.bones)


SRT_TYPE_ROTATION = 0x4


SRT_TYPE_TRANSLATION = 0x8


def _srt_type_for(bone_name, translation, rotation, scale, warnings):
    """The SRT block's type byte for one new bone, derived from the values
    actually being written.

    The byte is a component-presence mask, not a free-form tag: bit 0x4 means
    "rotation is live", bit 0x8 means "translation is live", and the game
    ignores whichever component its bit doesn't claim. Surveyed over the
    player corpus, all 1,469 vanilla `SRTType 8` bones have an identity
    rotation and all 1,903 `SRTType 12` bones have a non-identity one, with no
    exceptions either way.

    So this cannot be inherited from the parent bone: a bone that copies its
    parent's `8` while carrying a real rotation has that rotation silently
    dropped in game, and the mesh riding it lands somewhere the user never saw
    in Blender.
    """
    srt_type = 0
    if abs(abs(rotation.w) - 1.0) > 1e-6 or any(
        abs(v) > 1e-6 for v in (rotation.x, rotation.y, rotation.z)
    ):
        srt_type |= SRT_TYPE_ROTATION
    if any(abs(v) > 1e-6 for v in (translation.x, translation.y, translation.z)):
        srt_type |= SRT_TYPE_TRANSLATION
    if any(abs(v - 1.0) > 1e-6 for v in (scale.x, scale.y, scale.z)):
        warnings.append(
            f"{bone_name}: bone scale is not 1; no vanilla bone carries one and the "
            "SRT type byte has no known scale bit, so the scale is exported but the "
            "game is expected to ignore it."
        )
    return srt_type


def added_bone_name_error(candidates, context):
    """Error text when an Add Bone bone's ``bone_<N>`` name differs from the id
    ``encode_bone_hierarchy_edited`` assigns it, else None."""
    arm_obj = _find_root_scale_armature(candidates, context)
    if arm_obj is None:
        return None
    bones = arm_obj.data.bones
    donor_count = sum(1 for b in bones if not b.get('SluggiesUserAdded'))
    return HostBones.added_bone_name_error(HostBones.added_bone_renames(donor_count, [
        (b.name, int(b.get('SluggiesCreationOrder', 0)))
        for b in bones if b.get('SluggiesUserAdded')
    ]))


def encode_bone_hierarchy_edited(candidates, data, warnings, context):
    """Write ``SluggiesModel.BoneHierarchyEdited`` when the target armature has
    any Add Bone leaf.

    Every donor bone is carried through unchanged (the patcher's validation
    is append-only), and every ``SluggiesUserAdded`` bone is assigned ids
    ``N, N+1, ...`` in ``SluggiesCreationOrder`` (the Blender bone *name* has
    no binary meaning). Each new bone's
    Blender rest matrix is converted to a local T/R/S against its parent,
    reusing the same bind-matrix convention
    ``CustomSubmeshExport.bone_local_matrix`` decodes back (T @ R @ S,
    quaternion stored as ``[-qw, qx, qy, qz]``).
    """
    model = data.get("SluggiesModel", {})
    arm_obj = _find_root_scale_armature(candidates, context)
    if arm_obj is None:
        model.pop("BoneHierarchyEdited", None)
        return

    new_bones = [b for b in arm_obj.data.bones if b.get('SluggiesUserAdded')]
    if not new_bones:
        model.pop("BoneHierarchyEdited", None)
        return

    donor_bones = model.get("BoneHierarchy") or []
    if not donor_bones:
        warnings.append(
            f"{arm_obj.name}: model has no BoneHierarchy; added bone(s) not exported."
        )
        model.pop("BoneHierarchyEdited", None)
        return

    donor_count = len(donor_bones)
    new_bones.sort(key=lambda b: (int(b.get('SluggiesCreationOrder', 0)), b.name))

    id_by_bone_name = {
        b.name: _bone_id_from_bone_name(b.name)
        for b in arm_obj.data.bones if not b.get('SluggiesUserAdded')
    }
    id_by_bone_name = {name: bid for name, bid in id_by_bone_name.items() if bid is not None}
    next_id = donor_count
    for b in new_bones:
        id_by_bone_name[b.name] = next_id
        next_id += 1

    edited = []
    for donor_bone in donor_bones:
        entry = dict(donor_bone)
        entry["UserAdded"] = False
        edited.append(entry)

    stadium = TemplateSources.is_stadium_chunk(model.get("ChunkNumber"))
    try:
        donor_absolute = CustomSubmeshExport.bone_absolute_matrices(donor_bones) if stadium else {}
    except ValueError as exc:
        raise ExportCancelled(f"BoneHierarchy of the source .sluggie: {exc}") from exc
    for b in new_bones:
        own_id = id_by_bone_name[b.name]
        parent = b.parent
        if parent is None and stadium:
            # A new stadium root bone: armature space is the game's model
            # space, so its rest matrix is its SRT.
            parent_id = None
            local = b.matrix_local
        elif parent is None or parent.name not in id_by_bone_name:
            warnings.append(f"{b.name}: has no valid parent bone; not exported.")
            continue
        elif stadium:
            # A new stadium child bone: relative to the parent's GAME frame, so the
            # bone sits in game where it sits in Blender (its game rest matrix is
            # its Blender rest matrix, like a new root's). A donor parent's game
            # frame is its BoneHierarchy SRT chain; a new parent's is its own
            # Blender rest matrix (Blender's display frame of a donor bone is
            # reoriented and differs from the game's).
            parent_id = id_by_bone_name[parent.name]
            if parent.get('SluggiesUserAdded'):
                parent_game = parent.matrix_local
            else:
                from mathutils import Matrix
                parent_game = Matrix(donor_absolute[parent_id])
            local = parent_game.inverted() @ b.matrix_local
        else:
            parent_id = id_by_bone_name[parent.name]
            local = parent.matrix_local.inverted() @ b.matrix_local
        translation, rotation, scale = local.decompose()
        geo_id_raw = int(b.get('SluggiesGeoIdRaw', GEO_ID_FREE))
        srt_type = _srt_type_for(b.name, translation, rotation, scale, warnings)

        edited.append({
            "BoneId": own_id,
            "GeoId": geo_id_raw,
            "GeoIdRaw": geo_id_raw,
            "ParentBoneId": parent_id,
            "Skinned": bool(b.get('SluggiesSkinned', False)),
            "TrackId": int(b.get('track_id', 0xFFFF)),
            "MirrorBoneId": own_id,
            "MirrorRole": 3,
            "SRTType": srt_type,
            "DrawPriority": int(b.get('SluggiesDrawPriority', 0)),
            "InheritTransform": bool(b.get('SluggiesInheritTransform', True)),
            "UserAdded": True,
            "Translation": [round(translation.x, 6), round(translation.y, 6), round(translation.z, 6)],
            "Scale": [round(scale.x, 6), round(scale.y, 6), round(scale.z, 6)],
            "Quaternion": [
                round(-rotation.w, 6), round(rotation.x, 6),
                round(rotation.y, 6), round(rotation.z, 6),
            ],
            "VertexInfluences": [],
        })

    model["BoneHierarchyEdited"] = edited


def untracked_keys_warnings(candidates, context):
    """Warnings for added bones that imported .anm actions (or the armature's
    active action) move although they have no animation track: their TrackId
    stays 0xFFFF and the .anm export skips them, so the motion never reaches
    the game."""
    arm_obj = _find_root_scale_armature(candidates, context)
    if arm_obj is None:
        return []
    actions = [a for group in AnmExport.anm_groups().values() for a in group]
    if arm_obj.animation_data is not None and arm_obj.animation_data.action not in actions:
        actions.append(arm_obj.animation_data.action)
    return AnmExport.untracked_keys_warnings(arm_obj, actions)


# --- Blender-side pre-check for the donor-topology rules
# HammerspaceValidate._validate_bone_hierarchy_edited enforces authoritatively
# (act_section.html#appending-bones). This is a courtesy check so a mistake
# is visible while the user can still see and undo it in Blender; it is not
# a substitute for the patcher's own gate. Sibling-chain order is not in the
# .sluggie at all, so neither check can see it; the patcher's rebuilder keeps
# donor chains intact by construction.
def validate_bone_hierarchy_edited_export(model):
    """Return a list of blocking error strings for ``BoneHierarchyEdited``, or
    an empty list when nothing is wrong."""
    bone_hierarchy_edited = model.get("BoneHierarchyEdited")
    if not bone_hierarchy_edited:
        return []

    errors = []
    donor_bones = model.get("BoneHierarchy") or []
    donor_by_id = {int(b["BoneId"]): b for b in donor_bones}
    edited_by_id = {int(b["BoneId"]): b for b in bone_hierarchy_edited}
    new_ids = {int(b["BoneId"]) for b in bone_hierarchy_edited if b.get("UserAdded")}

    for bone_id, donor_bone in donor_by_id.items():
        edited_bone = edited_by_id.get(bone_id)
        if edited_bone is None:
            errors.append(f"donor bone {bone_id} is missing (donor bones cannot be deleted)")
            continue
        if edited_bone.get("UserAdded"):
            errors.append(f"donor bone {bone_id} is marked as a new bone")
            continue
        donor_parent = donor_bone.get("ParentBoneId")
        edited_parent = edited_bone.get("ParentBoneId")
        donor_parent_id = None if donor_parent is None else int(donor_parent)
        edited_parent_id = None if edited_parent is None else int(edited_parent)
        if donor_parent_id != edited_parent_id:
            if edited_parent_id in new_ids:
                errors.append(
                    f"new bone {edited_parent_id} was inserted between donor bones "
                    f"{donor_parent_id} and {bone_id}; new bones can only hang off "
                    "the tree as leaves"
                )
            else:
                errors.append(
                    f"donor bone {bone_id} was reparented from {donor_parent_id} "
                    f"to {edited_parent_id}"
                )

    stadium = TemplateSources.is_stadium_chunk(model.get("ChunkNumber"))
    for bone_id in sorted(new_ids):
        parent_id = edited_by_id[bone_id].get("ParentBoneId")
        if not stadium and parent_id is None:
            errors.append(f"new bone {bone_id} has no parent; new bones may not be roots outside stadiums")

    return errors


def _get_vgroup_weight(obj, group_name, vertex_index):
    """Return 0-1 weight for vertex_index in group_name, or 0.0 if absent."""
    vg = obj.vertex_groups.get(group_name)
    if vg is None:
        return 0.0
    try:
        return vg.weight(vertex_index)
    except RuntimeError:
        return 0.0


def _parse_bone_group_name(name):
    """Return bone id from Blender vertex-group name bone_<id>, else None."""
    if not isinstance(name, str) or not name.startswith("bone_"):
        return None
    try:
        return int(name[5:])
    except ValueError:
        return None


def _detect_uniform_vertex_bone_id(obj):
    """Return a single bone id when all vertices map to exactly one bone_<id>."""
    group_bone = {group.index: _parse_bone_group_name(group.name) for group in obj.vertex_groups}
    uniform_id = None
    for vd in obj.data.vertices:
        v_id = None
        for g in vd.groups:
            if g.weight <= 0:
                continue
            parsed = group_bone.get(g.group)
            if parsed is None:
                continue
            if v_id is not None and v_id != parsed:
                return None
            v_id = parsed
        if v_id is None or (uniform_id is not None and uniform_id != v_id):
            return None
        uniform_id = v_id
    return uniform_id


def encode_unskinned_bone_reassignments(candidates, data, warnings):
    """Write BoneHierarchy.GeoIdEdited when a non-skinned submesh is retargeted.

    Detection rule: for a non-skinned submesh object, every vertex must belong
    to exactly one positive-weight bone_<id> group, and that id differs from
    the current owning bone id. The rule itself lives in
    HostBones.compute_rigid_retargets, shared with the free-bone proposal
    so both call sites agree on what a
    valid retarget is.
    """
    model = data.get("SluggiesModel", {})
    bone_list = model.get("BoneHierarchy")
    submeshes = model.get("Submeshes", [])
    if not bone_list or not submeshes:
        return False

    for bd in bone_list:
        bd.pop("GeoIdEdited", None)

    retargets, issues, obj_by_submesh = _candidate_rigid_retargets(candidates, model)
    bone_by_id = {int(bd["BoneId"]): bd for bd in bone_list if "BoneId" in bd}

    for issue in issues:
        obj = obj_by_submesh.get(issue.submesh_index)
        obj_name = obj.name if obj is not None else f"submesh {issue.submesh_index}"
        if issue.reason == "unknown_bone":
            warnings.append(
                f"{obj_name}: target group bone_{issue.target_bone_id} is not present in BoneHierarchy; "
                f"non-skinned reassignment skipped."
            )
        elif issue.reason == "target_claimed_by_other_submesh":
            warnings.append(
                f"{obj_name}: bone_{issue.target_bone_id} is already requested by submesh "
                f"{issue.detail}; one non-skinned bone can own only one GeoId."
            )
        elif issue.reason == "target_occupied":
            warnings.append(
                f"{obj_name}: bone_{issue.target_bone_id} currently owns submesh {issue.detail}; "
                f"reassignment skipped to avoid GeoId collision."
            )

    wrote_any = False
    for r in retargets:
        bone_by_id[r.from_bone_id]["GeoIdEdited"] = GEO_ID_FREE
        bone_by_id[r.to_bone_id]["GeoIdEdited"] = r.submesh_index
        wrote_any = True

    return wrote_any


def _candidate_rigid_retargets(candidates, model):
    """The donor rigid-submesh retargets the selected *candidates* request,
    against the donor ``BoneHierarchy`` (any ``GeoIdEdited`` left by an earlier
    export is ignored, since this export rewrites it).

    Returns ``(retargets, issues, obj_by_submesh)``. Shared by
    :func:`encode_unskinned_bone_reassignments` and the custom submesh
    host-bone re-check, so both see the same final scene state.
    """
    bone_list = model.get("BoneHierarchy") or []
    submeshes = model.get("Submeshes", [])
    submesh_by_vb = {}
    for i, sm in enumerate(submeshes):
        vb = sm.get("VertexBuffer")
        if vb and "VertexBufferOffset" in vb:
            submesh_by_vb[str(vb["VertexBufferOffset"])] = i

    obj_by_submesh = {}
    for obj in candidates:
        if "VertexBufferOffset" not in obj:
            continue
        sub_idx = submesh_by_vb.get(str(obj["VertexBufferOffset"]))
        if sub_idx is not None:
            obj_by_submesh[sub_idx] = obj

    bone_by_id = {int(bd["BoneId"]): bd for bd in bone_list if "BoneId" in bd}
    owner_bone_by_submesh = {
        int(bd["GeoId"]): int(bd["BoneId"])
        for bd in bone_list
        if (not bd.get("Skinned")) and bd.get("GeoId") is not None and int(bd.get("GeoId", -1)) >= 0
    }

    bone_geo_raw = {bone_id: _bone_geo_id_raw(bd) for bone_id, bd in bone_by_id.items()}

    target_bone_by_submesh = {}
    for sub_idx, from_bone_id in owner_bone_by_submesh.items():
        obj = obj_by_submesh.get(sub_idx)
        if obj is not None:
            target_bone_by_submesh[sub_idx] = _detect_uniform_vertex_bone_id(obj)

    retargets, issues = compute_rigid_retargets(
        owner_bone_by_submesh, bone_geo_raw, target_bone_by_submesh, set(bone_by_id.keys())
    )
    return retargets, issues, obj_by_submesh


def _armature_donor_bone_records(arm_obj):
    """The import-time bone snapshot on *arm_obj* (``SluggiesGeoIdRaw``,
    ``SluggiesSkinned``), donor bones only: user-added bones have no
    counterpart in the target's ``BoneHierarchy``."""
    records = []
    for bone in arm_obj.data.bones:
        if bone.get('SluggiesUserAdded'):
            continue
        bone_id = _bone_id_from_bone_name(bone.name)
        if bone_id is None:
            continue
        parent_id = _bone_id_from_bone_name(bone.parent.name) if bone.parent is not None else None
        records.append(HostBones.BoneRecord(
            bone_id=bone_id,
            parent_id=parent_id,
            geo_id_raw=int(bone.get('SluggiesGeoIdRaw', GEO_ID_FREE)),
            skinned=bool(bone.get('SluggiesSkinned', False)),
        ))
    return records


# Literal ACT GeoId of a BoneHierarchy entry (GEO_ID_FREE when mesh-free).
_bone_geo_id_raw = RigidRebuildExport.bone_geo_id_raw
