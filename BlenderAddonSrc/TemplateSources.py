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


@dataclass(frozen=True)
class BuiltinTemplate:
    """One built-in template as the Blender side needs to know it."""

    layers: int             # texture layers its captured draw list binds
    shader_mode: str        # effective Type-7 mode
    description: str        # dialog tooltip
    verified_in_game: bool  # PLAN_EditRigidMeshes.md Phase 0 probe 7


# Mirrors HammerspaceMain._CUSTOM_SUBMESH_BUILTIN_TEMPLATES, which is
# authoritative -- the addon cannot import the tools package, so the layer
# counts and verification flags are duplicated here and the registry names
# this module as its mirror. `layers` caps the UV channels a custom submesh
# exports for the template (see CustomSubmeshExport.attribute_plan), so the
# 1-layer `Shdw` form stays 1-layer on a host that binds a specular texture.
BUILTIN_TEMPLATES = {
    "rigid_spec_v1": BuiltinTemplate(
        layers=2, shader_mode="Spec",
        description="Specular (default)",
        verified_in_game=True,          # PLAN_AddSubmesh.md Phase 0 probe 5
    ),
    "rigid_shdw_v1": BuiltinTemplate(
        layers=1, shader_mode="Shdw",
        description="No specular highlight; vanilla uses it on stadiums only",
        verified_in_game=False,
    ),
    "rigid_ghsp_v1": BuiltinTemplate(
        layers=2, shader_mode="GhSp",
        description="Effect unknown; vanilla uses it only on Birdo's ring and diamond",
        verified_in_game=False,
    ),
    "rigid_rhsp_v1": BuiltinTemplate(
        layers=2, shader_mode="RhSp",
        description="Shown and hidden together with the right hand",
        verified_in_game=False,
    ),
    "rigid_lhsp_v1": BuiltinTemplate(
        layers=2, shader_mode="LhSp",
        description="Shown and hidden together with the left hand",
        verified_in_game=False,
    ),
}

# Only templates probe 7 has confirmed in game are offered; the others stay
# hidden the same way U4 hid template groups it had not enabled, and
# HammerspaceMain._validate_template_source refuses them at patch time.
BUILTIN_TEMPLATE_NAMES: Tuple[str, ...] = tuple(
    name for name, template in BUILTIN_TEMPLATES.items() if template.verified_in_game
)


def builtin_template_layers(name: str) -> int:
    """Texture layers *name* binds. Raises for an unknown template, so a typo
    cannot silently fall back to the 2-layer form."""
    try:
        return BUILTIN_TEMPLATES[name].layers
    except KeyError:
        raise ValueError(
            f"builtin: unknown template {name!r}; known: {sorted(BUILTIN_TEMPLATES)}"
        ) from None


# How a TemplateSource string is resolved at build time (documentation only).
RESOLUTION_ORDER = ("rigid", "derived", "builtin")

# How the Add submesh / Add material dialogs list the kinds: built-ins first
# so `builtin:rigid_spec_v1` heads the list and is preselected even on a model
# with copyable donor surfaces, then the donor-based sources (decision 9).
DIALOG_ORDER = ("builtin", "rigid", "derived")

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
    """Build the dialog's template-source list in ``DIALOG_ORDER``.

    Built-ins come first, headed by `builtin:rigid_spec_v1`, so the dialog
    preselects it even when the model has copyable donor surfaces; the
    donor-based `rigid:` and `derived:` sources follow (decision 9). Within
    `rigid:`/`derived:`, candidates are deduplicated by SurfaceId (first-seen
    wins) and sorted by SurfaceId for a stable order.

    Only built-ins that Phase 0 probe 7 has verified in game are listed
    (``BUILTIN_TEMPLATE_NAMES``), so the unverified captures are unselectable
    here; ``HammerspaceMain._validate_custom_submeshes`` refuses them too.
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

    by_kind = {
        "builtin": [TemplateSourceChoice("builtin", name) for name in BUILTIN_TEMPLATE_NAMES],
        "rigid": [TemplateSourceChoice("rigid", s) for s in sorted(rigid_by_surface)],
        "derived": [TemplateSourceChoice("derived", s) for s in sorted(derived_by_surface)],
    }
    return [choice for kind in DIALOG_ORDER for choice in by_kind[kind]]
