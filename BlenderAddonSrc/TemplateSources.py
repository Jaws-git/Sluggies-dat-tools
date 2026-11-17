"""Template-source candidate list for the Add submesh dialog.

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

import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple

# Mirrors HammerspaceMain._CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES. A rigid
# template drawn with one of these Type-7 shader modes inherits hand or
# ghost visibility behaviour and is excluded.
HAND_VISIBILITY_ROLES = frozenset({"RhSp", "LhSp", "SpRf", "GhSp"})

# Mirrors HammerspaceMain._CUSTOM_SUBMESH_SPECULAR_SHADER_MODES: Type-7 modes
# whose param byte 0 is a specular strength. A custom surface whose template
# draws with any other mode gets no SpecularStrength property.
SPECULAR_SHADER_MODES = frozenset({"Spec", "LhSp", "RhSp", "GhSp"})

# Tooltip of each Type-7 shader mode in the Manage Materials and Convert
# Materials drop-downs.
SHADER_MODE_DESCRIPTIONS = {
    "Spec": "Specular: a sphere-map highlight scaled by the specular strength. "
            "The usual character mode",
    "RhSp": "Right-hand specular: like Spec, but parts with vertex alpha 0 are "
            "hidden while the right hand wears the mitt",
    "LhSp": "Left-hand specular: like Spec, but parts with vertex alpha 0 are "
            "hidden while the left hand wears the mitt",
    "GhSp": "Ghost specular: shaped like the hand modes. Vanilla only on Birdo's "
            "ring and diamond; drew invisible as a new surface on Wario's cap",
    "SpRf": "Specular plus reflection. Rare (11 vanilla surfaces); its other "
            "parameter bytes are not decoded",
    "Shdw": "Unlit, no specular. Stadium and map objects only: crashes and draws "
            "black on characters",
    "Audi": "Unlit stadium shader (15 vanilla surfaces), not decoded",
    "Oeka": "Unlit shader (1 vanilla surface), not decoded",
}

# The Type-7 modes a donor surface can switch between, by class. Mirrors
# patch_inplace._LIGHTING_MODES / _NO_LIGHTING_MODES: a change across the two
# classes alters the vertex stride of the surface's primitive lists, so the
# patcher refuses it and Manage Materials never offers it.
LIT_SHADER_MODES = ('Spec', 'RhSp', 'LhSp', 'GhSp', 'SpRf')
UNLIT_SHADER_MODES = ('Shdw', 'Audi', 'Oeka')

# Mirrors HammerspaceMain.STADIUM_CHUNKS (= export.STADIUM_DIR_INDICES): the
# .sluggie ChunkNumber of the stadium models, 7 Mario Stadium ... 16 Toy
# Field. In a stadium a custom submesh must hang on a root bone (Dolphin,
# 2026-10-01); the patcher refuses anything else.
STADIUM_CHUNKS = range(7, 17)


def is_stadium_chunk(chunk_number) -> bool:
    """Whether a .sluggie ChunkNumber is a stadium model directory. A whole
    number stored as a float or as text (a hand-edited file) counts as that
    number, instead of silently meaning "character"."""
    if isinstance(chunk_number, bool):
        return False
    if isinstance(chunk_number, float) and chunk_number.is_integer():
        chunk_number = int(chunk_number)
    elif isinstance(chunk_number, str) and chunk_number.strip().isdecimal():
        chunk_number = int(chunk_number.strip())
    return isinstance(chunk_number, int) and chunk_number in STADIUM_CHUNKS


@dataclass(frozen=True)
class BuiltinTemplate:
    """One built-in template as the Blender side needs to know it."""

    layers: int             # texture layers its captured draw list binds
    shader_mode: str        # effective Type-7 mode
    description: str        # dialog tooltip
    verified_in_game: bool  # confirmed in Dolphin
    normals: bool = True    # False: the custom submesh exports no normals
    stadium_only: bool = False  # offered (and accepted) only on stadium models


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
        verified_in_game=True,
    ),
    "rigid_shdw_v1": BuiltinTemplate(
        layers=1, shader_mode="Shdw",
        description="Shadow shader, one texture layer; vanilla uses it on stadiums and map objects",
        verified_in_game=True,   # declared by the user, 2026-11-15
    ),
    "rigid_ghsp_v1": BuiltinTemplate(
        layers=2, shader_mode="GhSp",
        description="Ghost specular; vanilla uses it only on Birdo's ring and diamond",
        verified_in_game=True,   # declared by the user, 2026-11-15
    ),
    "rigid_rhsp_v1": BuiltinTemplate(
        layers=2, shader_mode="RhSp",
        description="Parts with vertex alpha 0 are hidden while the right hand wears the mitt",
        verified_in_game=True,
    ),
    "rigid_lhsp_v1": BuiltinTemplate(
        layers=2, shader_mode="LhSp",
        description="Parts with vertex alpha 0 are hidden while the left hand wears the mitt",
        verified_in_game=True,
    ),
    "stadium_shdw_opaque_v1": BuiltinTemplate(
        layers=1, shader_mode="Shdw",
        description="Stadium surface, opaque: transparent pixels draw black",
        verified_in_game=True,          # as rigid:sm1_ds4 on Yoshi Park (2026-10-01)
        normals=False, stadium_only=True,
    ),
    "stadium_shdw_cutout_v1": BuiltinTemplate(
        layers=1, shader_mode="Shdw",
        description="Stadium surface, cut-out: transparent pixels are see-through (on/off only)",
        verified_in_game=True,          # as rigid:sm1_ds42 on Yoshi Park (2026-10-01)
        normals=False, stadium_only=True,
    ),
}

# Only templates confirmed in game are offered; the others stay hidden, and
# HammerspaceMain._validate_template_source refuses them at patch time.
BUILTIN_TEMPLATE_NAMES: Tuple[str, ...] = tuple(
    name for name, template in BUILTIN_TEMPLATES.items() if template.verified_in_game
)


# Starting specular strength (Type-7 param byte +1, 0-255) of a surface made
# from a `builtin:` template. The captures carry their donor's own value
# (rigid_spec_v1: 100), so the new surface gets this one via the
# `.sluggie`'s CustomSubmeshes[].SpecularStrength instead. `rigid:`/`derived:`
# surfaces start at their donor surface's strength.
BUILTIN_DEFAULT_SPECULAR_STRENGTH = 50


def template_surface_id(template_source: str) -> str:
    """The donor SurfaceId a `rigid:`/`derived:` source clones; '' for
    `builtin:` or a malformed source."""
    kind, _sep, argument = template_source.partition(":")
    return argument if kind in ("rigid", "derived") else ""


def builtin_template_normals(name: str) -> bool:
    """Whether a custom submesh made from built-in *name* exports normals."""
    try:
        return BUILTIN_TEMPLATES[name].normals
    except KeyError:
        raise ValueError(
            f"builtin: unknown template {name!r}; known: {sorted(BUILTIN_TEMPLATES)}"
        ) from None


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
# with copyable donor surfaces, then the donor-based sources.
DIALOG_ORDER = ("builtin", "rigid", "derived")

# Donor VertexBufferCompCount values: 3 = rigid, 6 = skinned
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


def next_new_surface_key(owner: str, existing_surface_ids: Iterable[str]) -> str:
    """The lowest unused ``<owner>_new<K>`` SurfaceId for a new Add-material
    surface, where *owner* is
    ``sm<N>`` for a donor submesh or a ``CustomSubmeshId`` for a custom one.
    Gaps left by deleted surfaces are reused.
    """
    prefix = f"{owner}_new"
    used = set()
    for surface_id in existing_surface_ids:
        if surface_id.startswith(prefix):
            suffix = surface_id[len(prefix):]
            if suffix.isdigit():
                used.add(int(suffix))
    n = 0
    while n in used:
        n += 1
    return f"{prefix}{n}"


def build_template_source_choices(
    materials: Iterable[TemplateSourceMaterial],
    stadium: bool = False,
) -> List[TemplateSourceChoice]:
    """Build the dialog's template-source list in ``DIALOG_ORDER``.

    Built-ins come first, headed by `builtin:rigid_spec_v1`, so the dialog
    preselects it even when the model has copyable donor surfaces; the
    donor-based `rigid:` and `derived:` sources follow. Within
    `rigid:`/`derived:`, candidates are deduplicated by SurfaceId (first-seen
    wins) and sorted by SurfaceId, numbers by value (``natural_sort_key``).

    Only built-ins verified in game are listed
    (``BUILTIN_TEMPLATE_NAMES``; all are today), so an unverified capture is
    unselectable here; ``HammerspaceMain._validate_custom_submeshes`` refuses one too.

    Stadium-only built-ins are listed only when *stadium* is true, and then
    first, so a stadium preselects ``builtin:stadium_shdw_opaque_v1``.
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
        "builtin": [
            TemplateSourceChoice("builtin", name)
            for name in sorted(
                (n for n in BUILTIN_TEMPLATE_NAMES if stadium or not BUILTIN_TEMPLATES[n].stadium_only),
                key=lambda n: not (stadium and BUILTIN_TEMPLATES[n].stadium_only),
            )
        ],
        "rigid": [TemplateSourceChoice("rigid", s) for s in sorted(rigid_by_surface, key=natural_sort_key)],
        "derived": [TemplateSourceChoice("derived", s) for s in sorted(derived_by_surface, key=natural_sort_key)],
    }
    return [choice for kind in DIALOG_ORDER for choice in by_kind[kind]]


def natural_sort_key(text: str):
    """Sort key that orders the numbers inside *text* by value, so
    ``sm1_ds2`` comes before ``sm1_ds10``."""
    parts = re.split(r'(\d+)', str(text))
    return [(0, int(part), '') if part.isdigit() else (1, 0, part) for part in parts], str(text)
