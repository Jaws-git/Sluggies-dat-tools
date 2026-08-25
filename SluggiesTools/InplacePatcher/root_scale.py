"""Pure helpers for the root-bone (whole-model) SRT scale in-place patcher.

Kept free of any top-level side effects (no argument parsing, no file I/O, no
logging configuration) so the logic can be imported and unit-tested without
executing ``patch_inplace.py``'s run-once pipeline.

The SRT block of a bone stores its bind-pose transform; the scale is three
big-endian floats at ``SRTOffset + 0x04`` (see ``SRT.analyze`` in
``helper.py``). Writing an edited ``RootBoneScaleEdited`` there scales the
whole model, because the main root bone's transform is never overridden by ANM
scale tracks.
"""

import math
import struct

# Largest per-component scale accepted for a root-bone SRT write. The value
# must round-trip through a big-endian float and stay comfortably inside the
# range the Broadway engine applies as a bind-pose transform; anything beyond
# this is almost certainly an input mistake.
MAX_ROOT_SCALE_COMPONENT = 1e6


def pack_srt_scale(scale: list[float]) -> bytes:
    """Pack a 3-component scale as the 12 bytes stored at SRT block +0x04.

    The SRT scale is three big-endian floats, which is the exact layout the
    in-place writer overwrites.
    """
    return struct.pack('>3f', float(scale[0]), float(scale[1]), float(scale[2]))


def validate_scale(scale, bone_id: int, abort) -> list[float]:
    """Validate a ``RootBoneScaleEdited`` / ``Scale`` value.

    Returns the value as three floats, or calls *abort* (with a message) when
    it is malformed or contains a non-finite / out-of-range component. *abort*
    is injected so the module stays free of any dependency on the patcher's
    interactive abort path.
    """
    try:
        values = [float(v) for v in scale]
    except (TypeError, ValueError):
        abort(
            f"Bone {bone_id}: root-bone scale {scale!r} is not a sequence of numbers."
        )
        return []
    if len(values) != 3 or not all(math.isfinite(v) for v in values):
        abort(
            f"Bone {bone_id}: root-bone scale {scale!r} must be 3 finite numbers."
        )
        return []
    if any(abs(v) > MAX_ROOT_SCALE_COMPONENT for v in values):
        abort(
            f"Bone {bone_id}: root-bone scale {scale!r} is out of range "
            f"(-{MAX_ROOT_SCALE_COMPONENT:g}..{MAX_ROOT_SCALE_COMPONENT:g} per component)."
        )
        return []
    return values


def main_root_bone(bone_hierarchy: list[dict]) -> dict | None:
    """Return the bone dict that is the model's main root, or None.

    The main root is the parentless bone (``ParentBoneId`` is null) with the
    most descendants. This is deliberately NOT "BoneId 0": verification against
    dt_na.dat showed the main root is consistently Bone 1 (e.g. Mario, Yoshi,
    Toad), while Bone 0 and several high-id bones are parentless leaves with no
    descendants. Picking the parentless bone with the deepest subtree scales the
    whole visible model and never a stray leaf.
    """
    by_id = {b.get('BoneId'): b for b in bone_hierarchy}
    roots = [b for b in bone_hierarchy if b.get('ParentBoneId') is None]
    if not roots:
        return None

    def descendant_count(bone_id: int) -> int:
        count = 0
        for b in bone_hierarchy:
            parent_id = b.get('ParentBoneId')
            seen = set()
            while parent_id is not None and parent_id not in seen:
                if parent_id == bone_id:
                    count += 1
                    break
                seen.add(parent_id)
                parent = by_id.get(parent_id)
                parent_id = parent.get('ParentBoneId') if parent else None
        return count

    best = None
    best_count = -1
    for r in roots:
        c = descendant_count(int(r.get('BoneId', -1)))
        if c > best_count:
            best, best_count = r, c
    return best


def _resolve_root_scale(model: dict, bone_hierarchy: list[dict], restore: bool, abort):
    """Resolve the main root bone and its target scale for a root-scale write.

    Shared by the in-place patcher (``root_scale_patch``) and the hammerspace
    patcher (``hammerspace_root_scale_patch``). Returns
    ``(bone_id, srt_offset_hex, target_scale, label)`` or None when there is
    nothing to write (no edit and nothing to restore, or no bone hierarchy).

    *abort* is injected for error reporting (missing ``SRTOffset`` metadata,
    malformed scale) so this module stays import-safe.
    """
    if not bone_hierarchy:
        return None

    main_root = main_root_bone(bone_hierarchy)
    if main_root is None:
        return None
    bone_id = int(main_root.get('BoneId', -1))

    if restore:
        scale = main_root.get('Scale')
        if scale is None:
            return None
        target = validate_scale(scale, bone_id, abort)
        label = "restore"
    else:
        edited = model.get('RootBoneScaleEdited')
        if edited is None:
            return None
        target = validate_scale(edited, bone_id, abort)
        label = "edited"

    srt_off = main_root.get('SRTOffset')
    if not srt_off:
        abort(
            f"Bone {bone_id}: cannot apply the root-bone scale ({label}) because this "
            f".sluggie is missing BoneHierarchy.SRTOffset metadata. Re-export the model "
            f"with the latest SluggiesTools export.py, then export from Blender again."
        )
        return None
    return (bone_id, srt_off, target, label)


def root_scale_patch(model: dict, bone_hierarchy: list[dict], restore: bool, abort) -> tuple | None:
    """Build the (bone_id, file_offset, raw_bytes) patch for the root-bone SRT scale.

    Patch mode (``restore`` False): writes ``SluggiesModel.RootBoneScaleEdited``
    (3 big-endian floats) into the SRT block of the main root bone. Unpatch mode
    (``restore`` True): writes the main root bone's original ``Scale`` back so a
    previously-applied scale edit is undone. Returns None when there is nothing
    to write (no edit and nothing to restore, or no bone hierarchy).

    The returned ``file_offset`` is an ABSOLUTE input-file offset
    (``SRTOffset + 0x04``), valid for the in-place patcher which writes back into
    the original ``dt_na.dat`` at the model's original location.

    *abort* is injected for error reporting (missing ``SRTOffset`` metadata,
    malformed scale) so this module stays import-safe.
    """
    resolved = _resolve_root_scale(model, bone_hierarchy, restore, abort)
    if resolved is None:
        return None
    bone_id, srt_off, target, _label = resolved
    scale_offset = int(srt_off, 16) + 0x04
    return (bone_id, scale_offset, pack_srt_scale(target))


def hammerspace_root_scale_patch(
    model: dict,
    bone_hierarchy: list[dict],
    act_section_absolute: int,
    abort,
) -> tuple | None:
    """Build a root-bone SRT scale patch for the hammerspace (relocated) patcher.

    Returns ``(bone_id, act_relative_scale_offset, raw_bytes)`` where the offset
    is RELATIVE to the start of the ACT section (``orientationPTR + 0x04``), or
    None when there is nothing to write (no ``RootBoneScaleEdited`` or no bone
    hierarchy).

    The hammerspace block is written to a new absolute offset, so the absolute
    ``SRTOffset`` recorded in the ``.sluggie`` is not valid for the output. The
    ACT section, however, is cloned verbatim, so the SRT offset relative to the
    ACT section start is stable. The caller applies ``raw_bytes`` at
    ``act_relative_scale_offset`` within the in-memory ACT section bytes.

    *act_section_absolute* is the absolute input-file offset of the ACT section
    (``model_offset + act_off`` from the model block header). *abort* is injected
    for error reporting (missing ``SRTOffset`` metadata, malformed scale, or an
    SRT offset that falls before the ACT section start).
    """
    resolved = _resolve_root_scale(model, bone_hierarchy, restore=False, abort=abort)
    if resolved is None:
        return None
    bone_id, srt_off, target, _label = resolved
    srt_relative = int(srt_off, 16) - act_section_absolute
    if srt_relative < 0:
        abort(
            f"Bone {bone_id}: SRTOffset 0x{int(srt_off, 16):X} is before the ACT section "
            f"start (0x{act_section_absolute:X}); the .sluggie's SRTOffset metadata does "
            f"not match this model's ACT section layout. Re-export the model with the "
            f"latest SluggiesTools export.py, then export from Blender again."
        )
        return None
    scale_relative = srt_relative + 0x04
    return (bone_id, scale_relative, pack_srt_scale(target))


# ---------------------------------------------------------------------------
# Bind-pose vertex scaling
#
# Scaling the root SRT matrix alone does not change the rendered model size:
# the game skins vertices with *local* ANM bone transforms, which cancel the
# root scale. The reliable mechanism is to scale the SKN ``BindPoseData``
# vertex positions directly (the same data the Blender add-on's
# "Apply Root Bone Scale" button edits). Both the inplace patcher and the
# hammerspace patcher use the functions below.
# ---------------------------------------------------------------------------

_INT16_MIN = -32768
_INT16_MAX = 32767


def _quantized_component_layout(quantize_info: int) -> tuple[str, float]:
    """Return ``(struct_format, divisor)`` for one SKN quantized component.

    Mirrors ``SluggiesTools.helper.getQuantizedData``: the high nibble picks
    the raw format (``0x3``/``0x0`` → 16-bit int, ``0x4``/``0x7``/``0xa`` →
    32-bit float, anything else → 32-bit float) and the low nibble is the
    power-of-two divisor. ``helper`` applies the divisor to *every* format
    (including float) when decoding, so the divisor is meaningful for floats
    too -- even though it cancels out in the scale-and-requantize round trip
    (see ``_rescale_bind_pose_component``).
    """
    high = quantize_info >> 4
    low = quantize_info & 0xF
    divisor = float(2 ** low)
    if high in (0x3, 0x0):
        return ">h", divisor
    return ">f", divisor


def _rescale_bind_pose_component(raw: int, factor: float) -> int:
    """Re-quantize one 16-bit bind-pose component by *factor*.

    The divisor cancels out of the round trip: the engine decodes a component
    as ``decoded = raw / divisor`` and re-encodes with the *same* divisor, so
    ``new_raw = round(decoded * factor * divisor) = round(raw * factor)``. The
    result is clamped to the 16-bit signed range so the output stays valid for
    the original quantization format.
    """
    quantized = int(round(raw * factor))
    if quantized > _INT16_MAX:
        quantized = _INT16_MAX
    elif quantized < _INT16_MIN:
        quantized = _INT16_MIN
    return quantized


def scale_bind_pose_data(
    data: bytes,
    quantize_info: int,
    vertex_cnt: int,
    vertex_offset: int,
    factors: tuple[float, float, float],
) -> bytes:
    """Return *data* with the bind-pose vertex positions scaled by *factors*.

    *data* is the raw SKN ``BindPoseData`` blob: ``vertex_offset`` prefix bytes
    (unknown/unused) followed by ``vertex_cnt`` vertices of 6 components each
    (position x/y/z, then normal x/y/z). Only the three position components of
    each vertex are scaled; normals and the prefix are copied through
    unchanged. The quantization format (component size and divisor) is taken
    from *quantize_info* and preserved, so the output blob is the same length
    as the input.

    Returns *data* unchanged when the scale is a no-op (all factors 1.0) or
    when there is nothing to scale.
    """
    if vertex_cnt <= 0 or vertex_offset < 0:
        return data
    if factors == (1.0, 1.0, 1.0):
        return data

    comp_fmt, _divisor = _quantized_component_layout(quantize_info)
    comp_size = struct.calcsize(comp_fmt)
    stride = 6 * comp_size
    expected = vertex_offset + vertex_cnt * stride
    if len(data) < expected:
        # Truncated blob: scale only the vertices that are actually present.
        vertex_cnt = (len(data) - vertex_offset) // stride

    out = bytearray(data)
    for vi in range(vertex_cnt):
        base = vertex_offset + vi * stride
        for ci in range(3):  # position components only
            off = base + ci * comp_size
            (raw,) = struct.unpack_from(comp_fmt, data, off)
            if comp_fmt == ">f":
                (out[off : off + comp_size]) = struct.pack(">f", raw * factors[ci])
            else:
                (out[off : off + comp_size]) = struct.pack(
                    ">h", _rescale_bind_pose_component(raw, factors[ci])
                )
    return bytes(out)


def resolve_bind_pose_scale(model: dict) -> tuple[float, float, float] | None:
    """Return the model's ``RootBoneScaleEdited`` factors or None.

    This is the same scale applied to the root SRT matrix; it is reused here
    to scale the SKN bind-pose vertices. Returns None when the model has no
    ``RootBoneScaleEdited`` (or it is malformed).
    """
    raw = model.get("RootBoneScaleEdited")
    if raw is None:
        return None
    try:
        factors = (float(raw[0]), float(raw[1]), float(raw[2]))
    except (TypeError, ValueError, IndexError):
        return None
    return factors