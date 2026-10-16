"""Texture slots of a high-/low-poly model pair, without bpy.

A `_L_` model has no TEX section: its materials bind texture indices into
the paired High model's TEX, including the textures the High model's own
export appends (index ``len(TextureDescriptors) + n`` for the n-th
``AdditionalTextureDescriptors`` entry). The "Manage texture slots" dialog
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
    descriptors = high_model.get("TextureDescriptors") or []
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
