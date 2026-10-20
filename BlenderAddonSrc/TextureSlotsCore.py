"""Texture slots of a high-/low-poly model pair, without bpy.

A `_L_` model has no TEX section: its materials bind texture indices into
the paired High model's TEX, including the textures the High model's own
export appends (index ``len(TextureDescriptors) + n`` for the n-th
``AdditionalTextureDescriptors`` entry), and a vanilla slot the High export
refilled (``TextureReplacements``) shows its new PNG. The "Manage texture slots" dialog
(TextureSlotsUI) lists those slots and lets the Low materials pick one.
"""

import os
from typing import List, NamedTuple, Optional

try:
    from . import ModelCheckCore
except ImportError:  # imported flat by the unit tests
    import ModelCheckCore


class TextureSlot(NamedTuple):
    index: int
    file_name: str
    added: bool  # appended by the High model's export, not in the vanilla TEX


# GX CI4/CI8/CI14x2: an addition never takes these formats, so a freed slot
# in one is not refilled either.
_INDEXED_FORMATS = frozenset((0x8, 0x9, 0xA))


def _setting_int(shader_mode):
    """A Type-1 ShaderMode as an int (GeometryRebuild._setting_int)."""
    if len(shader_mode) == 8 and all(c in "0123456789abcdefABCDEF" for c in shader_mode):
        return int(shader_mode, 16)
    return int.from_bytes(shader_mode.encode("ascii")[:4].ljust(4, b"\x00"), "big")


def _texture_assignment_owners(model):
    """Custom submesh surfaces and rebuild new surfaces: each binds its
    texture through its own ``TextureAssignment``."""
    for custom in model.get("CustomSubmeshes") or []:
        yield custom
        yield from custom.get("AdditionalSurfaces") or []
    for submesh in model.get("Submeshes") or []:
        for key in ("RigidRebuild", "SkinnedRebuild"):
            yield from (submesh.get(key) or {}).get("NewSurfaces") or []


def texture_references(model):
    """(used, shown_instead) of an exported model.

    *used*: every TEX index something still binds once the patcher applies
    ``DesiredTextureAssignments`` (the rule of
    GeometryRebuild.apply_desired_texture_assignments: a layer-0 Type-1
    setter takes the index its drawn surfaces request). Other layers, setters
    without drawn surfaces and custom/new surfaces count as they are.

    *shown_instead*: donor index -> the indices its re-pointed setters bind
    now, in model order.
    """
    assignments = model.get("DesiredTextureAssignments") or {}
    used, shown_instead = set(), {}
    for submesh_index, submesh in enumerate(model.get("Submeshes") or []):
        setters = {}  # state index -> (bound index, drawn surface ids)
        current = None
        for state_index, state in enumerate(submesh.get("DisplayStates") or []):
            if state.get("DisplayStateId") == 1:
                setting = _setting_int(state.get("ShaderModeEdited") or state.get("ShaderMode") or "")
                if (setting >> 13) & 7:
                    used.add(setting & 0x1FFF)
                else:
                    current = state_index
                    setters[current] = (setting & 0x1FFF, [])
            if current is not None and (
                int(state.get("FaceCount") or 0) > 0 or int(state.get("PrimListLength") or 0) > 0
            ):
                setters[current][1].append(state.get("SurfaceId") or f"sm{submesh_index}_ds{state_index}")
        for original, surfaces in setters.values():
            requested = [assignments[s] for s in surfaces if s in assignments]
            effective = requested[0] if requested else original
            used.add(effective)
            if effective != original:
                shown_instead.setdefault(original, []).append(effective)
    for owner in _texture_assignment_owners(model):
        donor = (owner.get("TextureAssignment") or {}).get("DonorTextureIndex")
        if donor is not None:
            used.add(int(donor))
    return used, shown_instead


def reuse_freed_texture_slots(model) -> List[str]:
    """Refill vanilla textures nothing binds any more with a new PNG.

    When every surface of a donor texture shows other images, the first new
    PNG (an ``AdditionalTextureDescriptors`` entry) shown in its place moves
    into that slot instead of being appended: the entry is dropped, the
    assignments binding it bind the slot, and ``TextureReplacements`` tells
    the patcher to encode the slot from that PNG (in the slot's own format).
    The other new PNGs stay appended, renumbered. Only entries bound through
    ``DesiredTextureAssignments`` move (a custom/new surface binds by file
    name), and only into a single-level, non-indexed slot (what an addition
    can encode). Rebuilt from scratch on every export. Returns messages.
    """
    model.pop("TextureReplacements", None)
    descriptors = model.get("TextureDescriptors") or []
    additions = list(model.get("AdditionalTextureDescriptors") or [])
    if not descriptors or not additions:
        return []
    donor_count = len(descriptors)
    names = [addition.get("TextureFileName") for addition in additions]
    bound_by_name = {
        (owner.get("TextureAssignment") or {}).get("AdditionalTextureFileName")
        for owner in _texture_assignment_owners(model)
    }
    used, shown_instead = texture_references(model)
    fill = {}  # addition order -> donor index it fills
    for index, descriptor in enumerate(descriptors):
        if index in used or int(descriptor.get("AdditionalMipCount") or 0) \
                or int(descriptor.get("Format", -1)) in _INDEXED_FORMATS:
            continue
        for shown in shown_instead.get(index, []):
            order = shown - donor_count
            if 0 <= order < len(additions) and order not in fill and names[order] not in bound_by_name:
                fill[order] = index
                break
    if not fill:
        return []

    remap, kept = {}, []
    for order, addition in enumerate(additions):
        if order in fill:
            remap[donor_count + order] = fill[order]
        else:
            remap[donor_count + order] = donor_count + len(kept)
            kept.append(addition)
    model["DesiredTextureAssignments"] = {
        surface: remap.get(index, index)
        for surface, index in (model.get("DesiredTextureAssignments") or {}).items()
    }
    if kept:
        model["AdditionalTextureDescriptors"] = kept
    else:
        model.pop("AdditionalTextureDescriptors", None)
    model["TextureReplacements"] = [
        {"TextureIndex": index, "TextureFileName": names[order]}
        for order, index in sorted(fill.items(), key=lambda item: item[1])
    ]
    return [
        f"Texture {index} ({descriptors[index].get('TextureFileName')}) is no longer shown; "
        f"{names[order]} takes its slot instead of a new one"
        for order, index in sorted(fill.items(), key=lambda item: item[1])
    ]


def replaced_texture_descriptors(descriptors, replacements):
    """*descriptors* with each ``TextureReplacements`` slot named after the
    PNG it now holds, so a Low model binds that slot by the new file name."""
    names = {entry.get("TextureIndex"): entry.get("TextureFileName") for entry in replacements or []}
    if not names:
        return list(descriptors)
    return [
        dict(d, TextureFileName=names[d.get("TextureIndex")]) if d.get("TextureIndex") in names else d
        for d in descriptors
    ]


def partner_added_texture_descriptors(descriptors, additions):
    """Descriptors for the textures a main model's export appends, so its
    `_L_` partner can bind them as existing textures.

    The patcher gives the n-th AdditionalTextureDescriptors entry TEX index
    len(descriptors) + n. It merges entries that share a file name and
    template format first, which renumbers the later ones, so a list with a
    repeated name yields nothing here rather than a possibly wrong index.
    """
    names = [addition.get("TextureFileName") for addition in additions]
    if not all(names) or len(set(names)) != len(names):
        return []
    return [
        {"TextureIndex": len(descriptors) + order, "TextureFileName": name}
        for order, name in enumerate(names)
    ]


def texture_slots(high_model: dict) -> List[TextureSlot]:
    """Every texture index a Low model can bind, from its High model's
    ``.sluggie`` (as last exported)."""
    descriptors = replaced_texture_descriptors(
        high_model.get("TextureDescriptors") or [], high_model.get("TextureReplacements"))
    slots = [
        TextureSlot(int(d.get("TextureIndex", i)), str(d.get("TextureFileName") or ""), False)
        for i, d in enumerate(descriptors)
    ]
    added = partner_added_texture_descriptors(
        descriptors, high_model.get("AdditionalTextureDescriptors") or [])
    slots += [TextureSlot(d["TextureIndex"], d["TextureFileName"], True) for d in added]
    return slots


def is_lod_pair(high_path: str, low_path: str) -> bool:
    """Whether *low_path*'s model is the `_L_` partner of *high_path*'s:
    sibling folders paired by the rule the exporter uses to borrow the High
    model's textures (ModelCheckCore.low_partner_matches)."""
    if not high_path or not low_path:
        return False
    high_dir = os.path.dirname(os.path.abspath(high_path))
    low_dir = os.path.dirname(os.path.abspath(low_path))
    return (
        os.path.normcase(os.path.dirname(high_dir)) == os.path.normcase(os.path.dirname(low_dir))
        and ModelCheckCore.low_partner_matches(os.path.basename(low_dir), os.path.basename(high_dir))
    )


def is_low_path(sluggie_path: str) -> bool:
    """Whether the ``.sluggie`` lies in a Low model folder (``..._L_...``)."""
    return ModelCheckCore.is_low_folder(
        os.path.basename(os.path.dirname(os.path.abspath(sluggie_path))))


def slot_for_file(slots: List[TextureSlot], file_name: str) -> Optional[int]:
    """The slot index showing *file_name*, or None. Exact match, like the
    exporter's lookup by ``TextureFileName``."""
    for slot in slots:
        if slot.file_name and slot.file_name == file_name:
            return slot.index
    return None
