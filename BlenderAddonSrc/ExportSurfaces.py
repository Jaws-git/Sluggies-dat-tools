"""Export, surfaces: which display state each face of a donor submesh belongs to after the user moved faces
between material slots (``_encode_face_surface_assignment``), and the Type-7 modes in effect.

Imports ``ExportInplaceMesh`` only among the ``Export*`` modules, and never ``ExportSluggies``.
"""

import struct
from .ExportInplaceMesh import (
    _from_bytes,
)


def _effective_type7_modes(display_states):
    active_mode = None
    effective_modes = []
    for state in display_states:
        if state.get("DisplayStateId") == 7:
            active_mode = state.get("ShaderModeEdited") or state.get("ShaderMode", "")
        effective_modes.append(active_mode)
    return effective_modes


def _encode_face_surface_assignment(obj, display_states, surf_mat, use_compression, warnings):
    """Return (data, changed) encoding which display state owns each face.

    data is an encoded uint16 BE array (one ds_idx per face) or None when unchanged.
    Returns (None, False) for old exports without FaceCount, or when face count
    differs from the original (topology change — handled by milestone 4).
    Validates duplicate, foreign, and missing SurfaceIds; appends to warnings.
    """
    # Build original face → ds_idx from FaceCount cumulative ranges
    original_ds_idx = []
    for di, ds in enumerate(display_states):
        fc = ds.get("FaceCount")
        if fc is None:
            return None, False  # Old export without FaceCount
        for _ in range(fc):
            original_ds_idx.append(di)

    if not original_ds_idx:
        return None, False

    # Donor surface IDs from the sluggie display states
    donor_sid_to_ds_idx = {
        ds.get("SurfaceId"): di
        for di, ds in enumerate(display_states)
        if ds.get("SurfaceId")
    }

    # Detect duplicate SurfaceIds across material slots
    sid_slot_first = {}
    for slot_idx, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None:
            continue
        sid = mat.get("SurfaceId")
        if not sid:
            continue
        if sid in sid_slot_first:
            warnings.append(
                f"{obj.name}: SurfaceId '{sid}' is on multiple material slots "
                f"({sid_slot_first[sid]} and {slot_idx}) — only slot "
                f"{sid_slot_first[sid]} will be used."
            )
        else:
            sid_slot_first[sid] = slot_idx
        if sid not in donor_sid_to_ds_idx:
            warnings.append(
                f"{obj.name}: material '{mat.name}' has SurfaceId '{sid}' "
                f"not found in the donor's display states — faces assigned to "
                f"it keep their original draw-state assignment."
            )

    # Build mat_slot_idx → ds_idx (first occurrence of each SurfaceId wins)
    seen_sids = set()
    mat_slot_to_ds_idx = {}
    for slot_idx, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None:
            continue
        sid = mat.get("SurfaceId")
        if sid and sid in donor_sid_to_ds_idx and sid not in seen_sids:
            mat_slot_to_ds_idx[slot_idx] = donor_sid_to_ds_idx[sid]
            seen_sids.add(sid)

    n_faces = len(obj.data.polygons)
    if n_faces != len(original_ds_idx):
        return None, False  # Topology changed — face assignment handled in milestone 4

    current_ds_idx = []
    has_unresolved = False
    for poly in obj.data.polygons:
        di = mat_slot_to_ds_idx.get(poly.material_index)
        if di is None:
            has_unresolved = True
            current_ds_idx.append(original_ds_idx[poly.index])
        else:
            current_ds_idx.append(di)

    if has_unresolved:
        warnings.append(
            f"{obj.name}: some faces have materials without a valid donor SurfaceId; "
            f"those faces keep their original draw-state assignment."
        )

    if current_ds_idx == original_ds_idx:
        return None, False

    # A primitive-bearing non-Type-7 state inherits the most recent Type-7
    # shader mode. Compare that effective mode rather than the current command's
    # raw setting (for example, Type-1 11110001 is a texture binding, not a shader).
    effective_mode = _effective_type7_modes(display_states)
    for poly_index, (src_ds, dst_ds) in enumerate(zip(original_ds_idx, current_ds_idx)):
        if src_ds == dst_ds:
            continue
        if effective_mode[src_ds] != effective_mode[dst_ds]:
            src_name = display_states[src_ds].get("SurfaceId") or f"ds{src_ds}"
            dst_name = display_states[dst_ds].get("SurfaceId") or f"ds{dst_ds}"
            raise ValueError(
                f"{obj.name}: face {poly_index} cannot move from surface "
                f"'{src_name}' (shader mode {effective_mode[src_ds]}) to "
                f"surface '{dst_name}' (shader mode {effective_mode[dst_ds]}); "
                "only identical effective Type-7 shader reassignment is supported"
            )

    raw = struct.pack(f'>{n_faces}H', *current_ds_idx)
    return _from_bytes(raw, use_compression), True
