"""Template-source candidate list for the Add submesh dialog
(PLAN_AddSubmesh.md, Phase 5 step 3, "Template sources").

Pure Python, no ``bpy`` import, so it is unit-testable outside Blender. The
caller (``SluggiesToolsPanel.py``) scans scene materials into
``TemplateSourceMaterial`` records; this module turns those into the ordered
list of ``rigid:<SurfaceId>`` / ``derived:<SurfaceId>`` / ``builtin:<name>``
choices the dialog offers, matching the resolution order and hand/visibility
exclusion in the "Template sources" section of the plan and
``HammerspaceMain._CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES`` /
``_CUSTOM_SUBMESH_BUILTIN_TEMPLATES`` (the authoritative, `.sluggie`-side
validation this dialog only has to approximate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

# Mirrors HammerspaceMain._CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES. A rigid
# template drawn with one of these Type-7 shader modes inherits hand or
# ghost visibility behaviour and is excluded (F9's "Hand-visibility roles").
HAND_VISIBILITY_ROLES = frozenset({"RhSp", "LhSp", "SpRf", "GhSp"})

# Phase 0 U4 (2026-09-16): the only builtin template captured so far, mirrors
# HammerspaceMain._CUSTOM_SUBMESH_BUILTIN_TEMPLATES. Both U4 sources
# (derived: and builtin:) passed, so nothing is hidden for the MVP.
BUILTIN_TEMPLATE_NAMES: Tuple[str, ...] = ("rigid_spec_v1",)

RESOLUTION_ORDER = ("rigid", "derived", "builtin")

# Donor VertexBufferCompCount values (F1/F6): 3 = rigid, 6 = skinned
# interleaved. A derived: source must come from a skinned surface.
_RIGID_COMP_COUNT = 3
_SKINNED_COMP_COUNT = 6
_DERIVED_REQUIRED_SHADER_MODE = "Spec"


@dataclass(frozen=True)
class TemplateSourceMaterial:
    """One donor surface material read from the scene."""

    surface_id: str
    comp_count: int
    shader_mode: str


@dataclass(frozen=True)
class TemplateSourceChoice:
    kind: str      # 'rigid' | 'derived' | 'builtin'
    argument: str  # SurfaceId for rigid/derived, template name for builtin

    @property
    def template_source(self) -> str:
        return f"{self.kind}:{self.argument}"


def build_template_source_choices(
    materials: Iterable[TemplateSourceMaterial],
) -> List[TemplateSourceChoice]:
    """Build the dialog's template-source list in resolution order.

    Within `rigid:`/`derived:`, candidates are deduplicated by SurfaceId
    (first-seen wins) and sorted by SurfaceId for a stable order. `builtin:`
    entries are always appended last, since they are model-independent and
    the U4 probes enabled them unconditionally.
    """
    rigid_by_surface = {}
    derived_by_surface = {}
    for m in materials:
        if m.comp_count == _RIGID_COMP_COUNT:
            if m.shader_mode not in HAND_VISIBILITY_ROLES:
                rigid_by_surface.setdefault(m.surface_id, m)
        elif m.comp_count == _SKINNED_COMP_COUNT:
            if m.shader_mode == _DERIVED_REQUIRED_SHADER_MODE:
                derived_by_surface.setdefault(m.surface_id, m)

    choices: List[TemplateSourceChoice] = []
    for surface_id in sorted(rigid_by_surface):
        choices.append(TemplateSourceChoice("rigid", surface_id))
    for surface_id in sorted(derived_by_surface):
        choices.append(TemplateSourceChoice("derived", surface_id))
    for name in BUILTIN_TEMPLATE_NAMES:
        choices.append(TemplateSourceChoice("builtin", name))
    return choices
