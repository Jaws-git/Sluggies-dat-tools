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
BONE_METADATA_VERSION = 2
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


def classify_host_bones(
    bone_records: Iterable[BoneRecord],
    scene_claims: Optional[SceneClaims] = None,
) -> List[HostBoneChoice]:
    """Classify every bone as a host candidate for a new custom submesh.

    Returns one :class:`HostBoneChoice` per input bone record, including
    ``excluded`` ones (callers building a dialog list drop those; see
    :func:`order_host_bone_choices`).
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
        elif rec.skinned:
            if SKINNING_HOSTS_ALLOWED:
                choices.append(HostBoneChoice(
                    rec.bone_id, rec.parent_id, STATUS_DRIVES_SKINNING,
                    "drives skinning; allowed (Phase 0 probe 3, U2)"))
            else:
                choices.append(HostBoneChoice(
                    rec.bone_id, rec.parent_id, STATUS_EXCLUDED, "drives skinning"))
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
    ``drives_skinning``; ``excluded`` choices are dropped. Within
    ``recommended``, leaf bones come before inner bones (F7: vanilla rigid
    owners are mostly leaves), then by bone id; the other groups are ordered
    by bone id alone.
    """
    child_counts: Dict[int, int] = {}
    for rec in bone_records:
        if rec.parent_id is not None:
            child_counts[rec.parent_id] = child_counts.get(rec.parent_id, 0) + 1

    def is_leaf(bone_id: int) -> bool:
        return child_counts.get(bone_id, 0) == 0

    by_status: Dict[str, List[HostBoneChoice]] = {status: [] for status in _PRESENTATION_ORDER}
    for choice in choices:
        if choice.status in by_status:
            by_status[choice.status].append(choice)

    by_status[STATUS_RECOMMENDED].sort(key=lambda c: (not is_leaf(c.bone_id), c.bone_id))
    by_status[STATUS_ALLOWED].sort(key=lambda c: c.bone_id)
    by_status[STATUS_DRIVES_SKINNING].sort(key=lambda c: c.bone_id)

    ordered: List[HostBoneChoice] = []
    for status in _PRESENTATION_ORDER:
        ordered.extend(by_status[status])
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
