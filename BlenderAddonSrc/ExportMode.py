"""Automatic choice between in-place and Hammerspace export.

Pure Python, no ``bpy`` import, so it is unit-testable outside Blender. The
exporter (``ExportSluggies.SLUGGIES_OT_export``) exports in place whenever
nothing would be lost and switches to Hammerspace when an edit needs a
rebuilt model block. This module collects the reasons; the exporter feeds it
the Blender-side facts.

Two kinds of reason exist:

- model-level ones, known before any mesh is encoded
  (:func:`model_level_reasons`): they skip the in-place attempt entirely;
- per-mesh ones the in-place encoders find while encoding (vertex count,
  topology, UV seams, colours, ...): the exporter collects them during its
  in-place attempt and then encodes again in Hammerspace mode.
"""

from __future__ import annotations

import os
import struct
from typing import Iterable, List, Optional, Sequence, Tuple

try:  # inside Blender this module is part of the addon package
    from . import FieldCodec
except ImportError:  # imported flat by the unit tests
    import FieldCodec

# Mirrors SluggiesTools/Hammerspace/UntanglePolicy.UNUSED_CHARACTER_DIRS (the
# .sluggie ChunkNumber equals the model dir). The unused characters' blocks
# are split copies that only the Hammerspace path can patch.
UNUSED_CHARACTER_CHUNKS = (89, 90, 91, 92, 93, 94)

# Mirrors SluggiesTools/texture_helper.GX_MAX_TEXTURE_DIMENSION.
GX_MAX_TEXTURE_DIMENSION = 1024

# Fields an earlier Hammerspace export leaves on a submesh. The in-place
# patcher ignores every one of them, so a model that still carries them on a
# submesh outside this export must stay in Hammerspace.
_SUBMESH_HAMMERSPACE_FIELDS = ("FacesDataEdited", "FaceSurfaceIdsEdited")
_UV_HAMMERSPACE_FIELDS = ("UVFacesDataEdited",)
_COLOR_HAMMERSPACE_FIELDS = ("ColorChannelDataEdited",)

# Bytes per vertex-colour entry by quantize-format nibble; mirrors
# SluggiesTools/binfmt.color_entry_size.
_COLOR_ENTRY_SIZE = {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}


def _decode_field(value) -> bytes:
    return FieldCodec.decode_field(value) if value else b""


def is_unused_character_chunk(chunk_number) -> bool:
    """Whether a .sluggie ChunkNumber is one of the unused characters."""
    return isinstance(chunk_number, int) and chunk_number in UNUSED_CHARACTER_CHUNKS


def clamp_texture_dimensions(
    width: int, height: int, max_dimension: int = GX_MAX_TEXTURE_DIMENSION,
) -> Tuple[int, int]:
    """Mirrors ``texture_helper.clamp_texture_dimensions``."""
    longest = max(width, height)
    if longest <= max_dimension:
        return width, height
    factor = longest / max_dimension
    return (
        min(max_dimension, max(1, round(width / factor))),
        min(max_dimension, max(1, round(height / factor))),
    )


def png_dimensions(path) -> Optional[Tuple[int, int]]:
    """Return a PNG's ``(width, height)`` from its IHDR chunk, or None when
    the file is missing or not a PNG. Blender ships no Pillow."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", header[16:24])


def texture_size_changes(descriptors: Iterable[dict], texture_dir) -> List[str]:
    """Names of donor textures whose PNG no longer has the descriptor's size.

    In-place texture re-import can only overwrite a payload of the same size,
    so a resized PNG needs the rebuilt TEX section."""
    changed = []
    if not texture_dir:
        return changed
    for descriptor in descriptors:
        name = descriptor.get("TextureFileName")
        width, height = descriptor.get("Width"), descriptor.get("Height")
        if not name or width is None or height is None:
            continue
        size = png_dimensions(os.path.join(texture_dir, name))
        if size is None:
            continue
        if clamp_texture_dimensions(*size) != (int(width), int(height)):
            changed.append(name)
    return changed


def stale_hammerspace_submeshes(submeshes: Sequence[dict], exported_offsets) -> List[str]:
    """Submeshes outside this export that carry Hammerspace-only edits from
    an earlier export. An in-place export would leave them unapplied."""
    exported = {str(offset) for offset in exported_offsets}
    stale = []
    for index, submesh in enumerate(submeshes):
        offset = str((submesh.get("VertexBuffer") or {}).get("VertexBufferOffset"))
        if offset in exported:
            continue
        if (
            any(submesh.get(field) is not None for field in _SUBMESH_HAMMERSPACE_FIELDS)
            or any(
                channel.get(field) is not None
                for channel in submesh.get("UVChannels") or []
                for field in _UV_HAMMERSPACE_FIELDS
            )
            or any(
                channel.get(field) is not None
                for channel in submesh.get("ColorChannels") or []
                for field in _COLOR_HAMMERSPACE_FIELDS
            )
        ):
            stale.append(f"submesh {index}")
    return stale


def promote_inplace_uv_edits(submeshes: Sequence[dict], exported_offsets) -> int:
    """Give in-place UV edits on submeshes outside a Hammerspace export the
    donor's ``UVFacesData`` as ``UVFacesDataEdited``.

    An in-place UV edit keeps the donor slot layout and has no
    ``UVFacesDataEdited``; the Hammerspace builder only rebuilds UV channels
    that carry one, so without this the edit would be dropped. The donor
    indices map every loop to its slot, which is a valid per-loop mapping.
    Returns the number of channels promoted."""
    exported = {str(offset) for offset in exported_offsets}
    promoted = 0
    for submesh in submeshes:
        offset = str((submesh.get("VertexBuffer") or {}).get("VertexBufferOffset"))
        if offset in exported:
            continue
        for channel in submesh.get("UVChannels") or []:
            if (
                channel.get("UVChannelDataEdited") is not None
                and channel.get("UVFacesDataEdited") is None
                and channel.get("UVFacesData") is not None
            ):
                channel["UVFacesDataEdited"] = channel["UVFacesData"]
                promoted += 1
    return promoted


def model_level_reasons(
    model: dict,
    *,
    custom_submesh_names: Sequence[str] = (),
    has_new_bones: bool = False,
    changed_materials: Sequence[str] = (),
    resized_textures: Sequence[str] = (),
    stale_submeshes: Sequence[str] = (),
) -> List[str]:
    """Reasons, known before any mesh is encoded, that require Hammerspace."""
    reasons = []
    if custom_submesh_names:
        reasons.append(f"custom submeshes ({', '.join(custom_submesh_names)})")
    if has_new_bones:
        reasons.append("bones added with Add Bone")
    if changed_materials:
        reasons.append(f"texture changes on materials ({', '.join(changed_materials)})")
    if resized_textures:
        reasons.append(f"resized textures ({', '.join(resized_textures)})")
    if is_unused_character_chunk(model.get("ChunkNumber")):
        reasons.append("unused character (only Hammerspace can patch it)")
    if stale_submeshes:
        reasons.append(
            "earlier Hammerspace edits on parts not in this export "
            f"({', '.join(stale_submeshes)})"
        )
    return reasons


def topology_changed(polygon_vertices: Iterable[Sequence[int]], donor_faces_data) -> bool:
    """Whether the mesh faces differ from the donor's ``FacesData``.

    ``polygon_vertices`` is each Blender polygon's vertex indices in order.
    The in-place patcher keeps the donor draw list, so any changed, added,
    removed, re-wound or non-triangle face needs Hammerspace."""
    donor = _decode_field(donor_faces_data)
    if len(donor) % 6:
        return True
    donor_flat = struct.unpack(f">{len(donor) // 2}H", donor) if donor else ()
    flat = []
    for vertices in polygon_vertices:
        if len(vertices) != 3:
            return True
        flat.extend(vertices)
    return tuple(flat) != tuple(donor_flat)


def colors_changed(edited_loop_data: bytes, color_channel: dict) -> bool:
    """Whether per-loop colours (``encode_color_edits`` order, raw bytes)
    differ from the donor colours the channel's ``ColorFacesData`` maps to
    each loop. In-place export cannot write colours at all."""
    size = _COLOR_ENTRY_SIZE.get(int(color_channel.get("ColorChannelQuantizeInfo", 0)) >> 4, 2)
    donor = _decode_field(color_channel.get("ColorChannelData"))
    faces = _decode_field(color_channel.get("ColorFacesData"))
    indices = struct.unpack(f">{len(faces) // 2}H", faces) if faces else ()
    if len(edited_loop_data) != len(indices) * size:
        return True
    for loop, index in enumerate(indices):
        expected = donor[index * size:(index + 1) * size]
        if edited_loop_data[loop * size:(loop + 1) * size] != expected:
            return True
    return False


def donor_skin_bone_sets(skin_data: dict, vertex_size: int) -> dict:
    """``{global vertex index: set of bone ids}`` from the donor SK1/SK2/SKAcc
    entries (global index = runtime dest byte offset // vertex size, the
    numbering ``encode_skin_weights_inplace`` uses)."""
    sets: dict = {}
    for sk1 in skin_data.get("SK1s", []):
        start = (sk1["GplVertexArrValue"] + sk1.get("VertexOffset", 0)) // vertex_size
        for i in range(sk1["VertexCnt"]):
            sets.setdefault(start + i, set()).add(sk1["BoneIndex"])
    for sk2 in skin_data.get("SK2s", []):
        start = (sk2["GplVertexArrValue"] + sk2.get("VertexOffset", 0)) // vertex_size
        for i in range(sk2["VertexCnt"]):
            sets.setdefault(start + i, set()).update((sk2["BoneIndex1"], sk2["BoneIndex2"]))
    for skacc in skin_data.get("SKAccs", []):
        base = skacc["GplDestArrValue"] // vertex_size
        raw = _decode_field(skacc.get("DestIndexData"))
        for dest in struct.unpack(f">{len(raw) // 2}H", raw) if raw else ():
            sets.setdefault(base + dest, set()).add(skacc["BoneIndex"])
    return sets


def skin_membership_changed(donor_sets: dict, edited_sets: dict) -> bool:
    """Whether any vertex now has other skinning bones than in the donor.

    ``edited_sets`` covers the vertices of the exported skinned meshes only;
    vertices outside them keep their donor bones. The in-place skin encoder
    only rewrites weights inside each vertex's original entries, so any
    change of the bone set needs Hammerspace."""
    return any(donor_sets.get(vertex, set()) != bones for vertex, bones in edited_sets.items())


def mode_message(reasons: Sequence[str]) -> str:
    """The INFO line the exporter reports for the chosen mode."""
    if not reasons:
        return "Export mode: in-place (the edit fits the original model data)."
    return "Export mode: Hammerspace, needed for: " + "; ".join(reasons) + "."
