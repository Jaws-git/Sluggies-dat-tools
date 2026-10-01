"""Free-bone proposal for the Add submesh feature (PLAN_AddSubmesh.md, Phase 5
step 2).

Pure Python, no ``bpy`` import, so it is unit-testable outside Blender. The
importer (``ImportSluggies.build_armature``) snapshots each bone's donor
ownership/skinning state onto the armature at import time
(``SluggiesGeoIdRaw``, ``SluggiesSkinned``, both read-only). This module turns
that snapshot plus current scene state (pending retargets, custom-submesh
claims) into the list of bones a new custom submesh may attach to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

GEO_ID_FREE = 0xFFFF
# 2: SluggiesSkinned comes from SkinData bone references. Version 1 copied
# BoneHierarchy's "Skinned" flag, which export.py sets for every mesh-free bone
# (GeoIdRaw == 0xFFFF), so every free bone looked like it drove skinning.
# 3: Add Bone needs SluggiesMirrorBoneId/
# SluggiesMirrorRole/SluggiesSRTType/SluggiesDrawPriority/
# SluggiesInheritTransform/SluggiesUserAdded on every bone to export
# BoneHierarchyEdited; armatures imported before these were written lack them.
BONE_METADATA_VERSION = 3
RE_IMPORT_MESSAGE = "Re-import this model to enable Add submesh"

STATUS_EXCLUDED = "excluded"
STATUS_RECOMMENDED = "recommended"
STATUS_ALLOWED = "allowed"
STATUS_DRIVES_SKINNING = "drives_skinning"

# Phase 0 probe 3 (2026-09-16, Mario hand bone 28): a bone that drives SKN
# skinning may still host a rigid submesh without disturbing its skinning
# role. Kept as one constant so the U2 decision lives in one place.
SKINNING_HOSTS_ALLOWED = True


@dataclass(frozen=True)
class BoneRecord:
    """One bone's donor ownership/skinning facts, as snapshotted at import."""

    bone_id: int
    parent_id: Optional[int]
    geo_id_raw: int
    skinned: bool


@dataclass(frozen=True)
class RigidRetarget:
    """A non-skinned donor submesh moved from one host bone to another."""

    submesh_index: int
    from_bone_id: int
    to_bone_id: int


@dataclass(frozen=True)
class RetargetIssue:
    """A requested retarget that could not be applied."""

    submesh_index: int
    target_bone_id: int
    reason: str  # 'unknown_bone' | 'target_claimed_by_other_submesh' | 'target_occupied'
    detail: object = None


@dataclass(frozen=True)
class SceneClaims:
    """Scene state layered on top of the import-time bone snapshot."""

    retargets: Tuple[RigidRetarget, ...] = ()
    custom_submesh_bone_ids: FrozenSet[int] = frozenset()


@dataclass(frozen=True)
class HostBoneChoice:
    bone_id: int
    parent_id: Optional[int]
    status: str
    reason: str


def compute_rigid_retargets(
    owner_bone_by_submesh: Dict[int, int],
    bone_geo_raw: Dict[int, int],
    target_bone_by_submesh: Dict[int, Optional[int]],
    known_bone_ids: Set[int],
) -> Tuple[List[RigidRetarget], List[RetargetIssue]]:
    """Detect valid non-skinned-submesh -> bone retargets.

    Shared by the exporter (``ExportSluggies.encode_unskinned_bone_reassignments``,
    operating on parsed ``.sluggie`` JSON) and the free-bone proposal (operating
    directly on armature/object scene state), so both apply the same rule: a
    donor rigid submesh moved to another bone frees its old owner and occupies
    the new one.

    - ``owner_bone_by_submesh``: current owner bone id per non-skinned donor
      submesh index.
    - ``bone_geo_raw``: current raw ``GeoId`` (``GEO_ID_FREE`` sentinel when
      free) per bone id, reflecting any edits already queued this call.
    - ``target_bone_by_submesh``: the single bone id every vertex of the
      submesh's object currently belongs to, or ``None`` when not uniform.
    - ``known_bone_ids``: bone ids that exist in ``BoneHierarchy``.
    """
    retargets: List[RigidRetarget] = []
    issues: List[RetargetIssue] = []
    claims: Dict[int, int] = {}

    for submesh_index, from_bone_id in sorted(owner_bone_by_submesh.items()):
        target_bone_id = target_bone_by_submesh.get(submesh_index)
        if target_bone_id is None or target_bone_id == from_bone_id:
            continue

        if target_bone_id not in known_bone_ids:
            issues.append(RetargetIssue(submesh_index, target_bone_id, "unknown_bone"))
            continue

        if target_bone_id in claims and claims[target_bone_id] != submesh_index:
            issues.append(RetargetIssue(
                submesh_index, target_bone_id, "target_claimed_by_other_submesh",
                claims[target_bone_id]))
            continue

        target_raw = bone_geo_raw.get(target_bone_id, GEO_ID_FREE)
        if target_raw not in (GEO_ID_FREE, submesh_index):
            issues.append(RetargetIssue(
                submesh_index, target_bone_id, "target_occupied", target_raw))
            continue

        retargets.append(RigidRetarget(submesh_index, from_bone_id, target_bone_id))
        claims[target_bone_id] = submesh_index

    return retargets, issues


STADIUM_CHILD_BONE_REASON = "stadium: a mesh on a child bone draws nearly transparent"


def classify_host_bones(
    bone_records: Iterable[BoneRecord],
    scene_claims: Optional[SceneClaims] = None,
    roots_only: bool = False,
) -> List[HostBoneChoice]:
    """Classify every bone as a host candidate for a new custom submesh.

    Returns one :class:`HostBoneChoice` per input bone record, including
    ``excluded`` ones (callers building a dialog list drop those; see
    :func:`order_host_bone_choices`).

    *roots_only* is for stadium models: there a mesh must hang on a root bone
    (Dolphin, 2026-10-01; HammerspaceMain refuses a child host bone), so
    every bone with a parent is excluded and a free root bone is the
    recommended choice.
    """
    scene_claims = scene_claims or SceneClaims()
    freed_bone_ids = {r.from_bone_id for r in scene_claims.retargets}
    occupied_bone_ids = {r.to_bone_id for r in scene_claims.retargets}
    claimed_bone_ids = set(scene_claims.custom_submesh_bone_ids)

    choices: List[HostBoneChoice] = []
    for rec in bone_records:
        owns_donor_mesh = rec.bone_id in occupied_bone_ids or (
            rec.geo_id_raw != GEO_ID_FREE and rec.bone_id not in freed_bone_ids
        )
        if owns_donor_mesh:
            choices.append(HostBoneChoice(
                rec.bone_id, rec.parent_id, STATUS_EXCLUDED, "already owns a mesh"))
        elif rec.bone_id in claimed_bone_ids:
            choices.append(HostBoneChoice(
                rec.bone_id, rec.parent_id, STATUS_EXCLUDED,
                "claimed by another custom submesh"))
        elif roots_only and rec.parent_id is not None:
            choices.append(HostBoneChoice(
                rec.bone_id, rec.parent_id, STATUS_EXCLUDED, STADIUM_CHILD_BONE_REASON))
        elif rec.skinned:
            if SKINNING_HOSTS_ALLOWED:
                choices.append(HostBoneChoice(
                    rec.bone_id, rec.parent_id, STATUS_DRIVES_SKINNING,
                    "drives skinning; allowed (Phase 0 probe 3, U2)"))
            else:
                choices.append(HostBoneChoice(
                    rec.bone_id, rec.parent_id, STATUS_EXCLUDED, "drives skinning"))
        elif roots_only:
            choices.append(HostBoneChoice(
                rec.bone_id, rec.parent_id, STATUS_RECOMMENDED,
                "stadium: free root bone"))
        elif rec.parent_id is None:
            choices.append(HostBoneChoice(
                rec.bone_id, rec.parent_id, STATUS_ALLOWED, "root bone"))
        else:
            choices.append(HostBoneChoice(
                rec.bone_id, rec.parent_id, STATUS_RECOMMENDED,
                "mesh-free, not used by skinning"))
    return choices


_PRESENTATION_ORDER = (STATUS_RECOMMENDED, STATUS_ALLOWED, STATUS_DRIVES_SKINNING)


def order_host_bone_choices(
    choices: Iterable[HostBoneChoice],
    bone_records: Iterable[BoneRecord],
) -> List[HostBoneChoice]:
    """Group and order choices for the Add submesh dialog.

    Resolution order is ``recommended``, then ``allowed``, then
    ``drives_skinning``; ``excluded`` choices are dropped. Each group is
    ordered by numeric bone id, so ``bone_2`` always precedes ``bone_10``.
    (``recommended`` used to list leaf bones before inner ones, which read
    as a broken sort in the dialog; the default pick is nearest-bone anyway.)

    *bone_records* is unused and kept for call-site compatibility.
    """
    by_status: Dict[str, List[HostBoneChoice]] = {status: [] for status in _PRESENTATION_ORDER}
    for choice in choices:
        if choice.status in by_status:
            by_status[choice.status].append(choice)

    ordered: List[HostBoneChoice] = []
    for status in _PRESENTATION_ORDER:
        ordered.extend(sorted(by_status[status], key=lambda c: c.bone_id))
    return ordered


def reassignment_choices(
    bone_records: Iterable[BoneRecord],
    scene_claims: Optional[SceneClaims] = None,
    moving_submesh_index: Optional[int] = None,
    moving_custom_bone_id: Optional[int] = None,
    roots_only: bool = False,
) -> List[HostBoneChoice]:
    """Host-bone choices for the Reassign to new bone dialog (PLAN_EditRigidMeshes.md
    Phase 6): the same classification :func:`classify_host_bones` /
    :func:`order_host_bone_choices` give Add submesh, with the object being
    moved dropped from the claims first, then its own *current* bone
    excluded again as a separate, final step.

    The two-step shape matters: freeing the object's claim first is what
    makes its original donor bone reappear once it has been moved away
    (a claim on that bone would otherwise never lift, since bone_records
    only records donor/original ownership, never a pending retarget) --
    but the bone the object is *currently* on is never a useful
    reassignment target (moving an object to the bone it is already on is a
    no-op), so it is excluded again at the end regardless of how it became
    free. For a single-bone prop, the one bone in the model is both the
    object's original owner and its current bone, so it is freed and then
    immediately excluded again, leaving an empty list.

    Exactly one of *moving_submesh_index* (a donor rigid submesh, identified
    by its donor ``GeoIdRaw``) or *moving_custom_bone_id* (a custom
    submesh's current bone) should be given, matching which kind of object
    is being reassigned.
    """
    scene_claims = scene_claims or SceneClaims()
    bone_records = list(bone_records)
    retargets = scene_claims.retargets
    custom_submesh_bone_ids = scene_claims.custom_submesh_bone_ids
    current_bone_id = None

    if moving_submesh_index is not None:
        donor_bone_id = next(
            (r.bone_id for r in bone_records if r.geo_id_raw == moving_submesh_index), None)
        own_retarget = next(
            (r for r in retargets if r.submesh_index == moving_submesh_index), None)
        current_bone_id = own_retarget.to_bone_id if own_retarget is not None else donor_bone_id
        bone_records = [
            BoneRecord(r.bone_id, r.parent_id, GEO_ID_FREE, r.skinned)
            if r.geo_id_raw == moving_submesh_index else r
            for r in bone_records
        ]
        retargets = tuple(r for r in retargets if r.submesh_index != moving_submesh_index)
    elif moving_custom_bone_id is not None:
        current_bone_id = moving_custom_bone_id
        custom_submesh_bone_ids = frozenset(
            b for b in custom_submesh_bone_ids if b != moving_custom_bone_id)

    claims = SceneClaims(retargets=retargets, custom_submesh_bone_ids=custom_submesh_bone_ids)
    choices = classify_host_bones(bone_records, claims, roots_only=roots_only)
    ordered = order_host_bone_choices(choices, bone_records)
    if current_bone_id is not None:
        ordered = [c for c in ordered if c.bone_id != current_bone_id]
    return ordered


def skn_bone_ids(skin_data: Optional[dict]) -> Set[int]:
    """Bone ids referenced by any SK1, SK2 or SKAcc entry in a ``.sluggie``
    ``SkinData`` dict.

    This is the "drives skinning" fact (F7). ``BoneHierarchy``'s ``Skinned``
    flag is not: ``export.py`` sets it for every bone whose ``GeoIdRaw`` is
    ``0xFFFF``.
    """
    used: Set[int] = set()
    if not skin_data:
        return used
    for entry in skin_data.get("SK1s") or []:
        used.add(int(entry["BoneIndex"]))
    for entry in skin_data.get("SK2s") or []:
        used.add(int(entry["BoneIndex1"]))
        used.add(int(entry["BoneIndex2"]))
    for entry in skin_data.get("SKAccs") or []:
        used.add(int(entry["BoneIndex"]))
    return used


@dataclass(frozen=True)
class CustomSubmeshHost:
    """A custom submesh object being exported and the bone it is weighted to."""

    object_name: str
    bone_id: int


def custom_submesh_host_errors(
    bone_records: Iterable[BoneRecord],
    retargets: Iterable[RigidRetarget],
    hosts: Iterable[CustomSubmeshHost],
    submesh_names: Optional[Dict[int, str]] = None,
) -> List[str]:
    """Export-time re-check of custom submesh host bones (PLAN_AddSubmesh.md
    Phase 6 step 3), on the final scene state.

    A bone that was free when the cube was created may have been taken since.
    Rejected, with the conflicting object names:
    - a host bone the model doesn't have,
    - a host bone that owns a donor mesh, including one moved onto it by a
      pending retarget (the same :func:`classify_host_bones` rule the Add
      submesh dialog uses),
    - a bone hosting more than one custom submesh.

    *submesh_names* maps donor submesh index to its Blender object name.
    """
    bone_records = list(bone_records)
    retargets = tuple(retargets)
    submesh_names = submesh_names or {}
    record_ids = {rec.bone_id for rec in bone_records}
    choices = {
        choice.bone_id: choice
        for choice in classify_host_bones(bone_records, SceneClaims(retargets=retargets))
    }
    moved_onto = {r.to_bone_id: r.submesh_index for r in retargets}
    geo_raw = {rec.bone_id: rec.geo_id_raw for rec in bone_records}

    names_by_bone: Dict[int, List[str]] = {}
    for host in hosts:
        names_by_bone.setdefault(host.bone_id, []).append(host.object_name)

    errors: List[str] = []
    for bone_id in sorted(names_by_bone):
        names = ", ".join(names_by_bone[bone_id])
        # Checked first and independently of the other rules: a duplicated
        # cube (Shift+D) keeps its original's bone_<id> group, and the
        # "one mesh per bone" rule is the message that user needs.
        if len(names_by_bone[bone_id]) > 1:
            errors.append(
                f"{names}: all use host bone_{bone_id}, but a bone can own only one "
                "mesh. Move all but one to other bones."
            )
        if bone_id not in record_ids:
            errors.append(
                f"{names}: host bone_{bone_id} does not exist in the target model."
            )
            continue
        if choices[bone_id].status == STATUS_EXCLUDED:
            submesh_index = moved_onto.get(bone_id, geo_raw[bone_id])
            owner = submesh_names.get(submesh_index, f"submesh {submesh_index}")
            how = "was moved onto it" if bone_id in moved_onto else "already owns it"
            errors.append(
                f"{names}: host bone_{bone_id} is taken, {owner} {how}. "
                "Move the custom submesh to a free bone."
            )
    return errors


def bone_metadata_mismatches(
    armature_records: Iterable[BoneRecord],
    sluggie_records: Iterable[BoneRecord],
) -> List[str]:
    """Differences between an armature's import-time bone snapshot and the
    export target's ``BoneHierarchy``/``SkinData`` (PLAN_AddSubmesh.md Phase 5
    step 2, export-time re-check).

    Both describe the donor model, so any difference means the armature was
    imported from another model than the one being exported to. Compares bone
    ids, donor ``GeoIdRaw`` and the drives-skinning flag. *armature_records*
    must leave out user-added bones, which the donor never had.
    """
    armature = {rec.bone_id: rec for rec in armature_records}
    sluggie = {rec.bone_id: rec for rec in sluggie_records}
    mismatches: List[str] = []
    missing = sorted(set(sluggie) - set(armature))
    extra = sorted(set(armature) - set(sluggie))
    if missing:
        mismatches.append(
            "bones missing from the armature: " + ", ".join(f"bone_{b}" for b in missing))
    if extra:
        mismatches.append(
            "bones not in the target .sluggie: " + ", ".join(f"bone_{b}" for b in extra))
    for bone_id in sorted(set(armature) & set(sluggie)):
        ours, theirs = armature[bone_id], sluggie[bone_id]
        if ours.geo_id_raw != theirs.geo_id_raw:
            mismatches.append(
                f"bone_{bone_id} mesh owner 0x{ours.geo_id_raw:04X} vs "
                f"0x{theirs.geo_id_raw:04X}")
        if ours.skinned != theirs.skinned:
            mismatches.append(
                f"bone_{bone_id} drives skinning: {ours.skinned} vs {theirs.skinned}")
    return mismatches


def bone_metadata_is_current(version: object) -> bool:
    """Whether an armature's ``SluggiesBoneMetadataVersion`` can be trusted."""
    try:
        return int(version) >= BONE_METADATA_VERSION
    except (TypeError, ValueError):
        return False


def bone_records_from_hierarchy(
    bone_hierarchy: Sequence[dict],
    skin_data: Optional[dict] = None,
) -> List[BoneRecord]:
    """Build :class:`BoneRecord` entries from a parsed ``.sluggie``
    ``BoneHierarchy`` list (``export.py``'s ``bone_list`` shape) and its
    ``SkinData``."""
    skinned_ids = skn_bone_ids(skin_data)
    return [
        BoneRecord(
            bone_id=int(bd["BoneId"]),
            parent_id=(int(bd["ParentBoneId"]) if bd.get("ParentBoneId") is not None else None),
            geo_id_raw=int(bd.get("GeoIdRaw", GEO_ID_FREE)),
            skinned=int(bd["BoneId"]) in skinned_ids,
        )
        for bd in bone_hierarchy
    ]


def added_bone_renames(
    donor_bone_count: int,
    added_bones: Iterable[Tuple[str, int]],
) -> Dict[str, str]:
    """``{current name: bone_<exported id>}`` for every Add Bone bone whose
    Blender name disagrees with the id the exporter will give it.

    *added_bones* is ``(name, SluggiesCreationOrder)`` per ``SluggiesUserAdded``
    bone. The exporter (``encode_bone_hierarchy_edited``) numbers them
    ``donor_bone_count, +1, ...`` in ``(creation order, name)`` order, while
    everything else (host bones, retargets, the free-bone list) reads the id
    from the ``bone_<N>`` name. Deleting an added bone shifts the later ids,
    so the names must follow, or a mesh on ``bone_92`` would export onto
    whichever bone now holds id 92.
    """
    ordered = sorted(added_bones, key=lambda item: (int(item[1]), item[0]))
    renames = {}
    for offset, (name, _order) in enumerate(ordered):
        expected = f"bone_{donor_bone_count + offset}"
        if name != expected:
            renames[name] = expected
    return renames


def added_bone_name_error(renames: Dict[str, str]) -> Optional[str]:
    """Export-time message for :func:`added_bone_renames`, or None."""
    if not renames:
        return None
    shown = ", ".join(f"{old} exports as {new}" for old, new in sorted(renames.items())[:5])
    more = f" (and {len(renames) - 5} more)" if len(renames) > 5 else ""
    return (
        f"Added bone names don't match their exported ids ({shown}{more}), usually "
        "because an added bone was deleted. Click 'Renumber Added Bones' in the "
        "Sluggies sidebar, then export again."
    )
