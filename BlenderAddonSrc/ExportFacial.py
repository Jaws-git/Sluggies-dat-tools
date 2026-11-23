"""Export, facial poses: the pose edits of the facial submeshes (``update_facial_pose_edits``) and the shape-key
data a skinned rebuild carries.

Imports ``ExportInplaceMesh`` only among the ``Export*`` modules, and never ``ExportSluggies``.
"""

from . import SkinnedRebuildExport
from .ExportInplaceMesh import (
    _from_bytes,
    _pack_quantized_component,
    _to_bytes,
)


def _facial_vertex_indices(attribute):
    indices = []
    for run in attribute.get("Runs", []):
        first = run.get("FirstVertex", 0)
        count = run.get("VertexCount", 0)
        indices.extend(range(first, first + count))
    return indices


def _encode_facial_key(
    key, basis_key, source_pose_data, indices, pose_index, quant_info,
    component_count, component_size,
):
    """One pose array: pose zero holds the Basis key's positions, every later pose its key's offset from
    the Basis key (*basis_key*), the same Basis this export writes as pose zero. Blender moves the relative
    keys along when the Basis is edited, so measuring from the file's old pose zero put the Basis edit into
    every pose a second time (Blender 5.2.2, 2026-11-15: a Basis edit of 0.5 came back as poses moved by
    1.0)."""
    if component_size != 2 or component_count < 3:
        return None
    divisor = 1 << (quant_info & 0xF)
    stride = component_count * component_size
    raw = bytearray(_to_bytes(source_pose_data))
    if len(raw) != len(indices) * stride:
        return None
    for mapped_index, vertex_index in enumerate(indices):
        coordinate = key.data[vertex_index].co
        if pose_index == 0:
            values = coordinate
        else:
            basis = basis_key.data[vertex_index].co
            values = tuple(coordinate[axis] - basis[axis] for axis in range(3))
        for component, value in enumerate(values):
            # Out of range or not finite raises, as every other position path does: clamping would flatten
            # the shape onto the limit without a word.
            offset = mapped_index * stride + component * component_size
            raw[offset:offset + 2] = _pack_quantized_component(
                value, divisor,
                lambda vertex_index=vertex_index, component=component:
                    f"shape key {key.name} vertex {vertex_index} {'xyz'[component]}")
    return bytes(raw)


def encode_facial_pose_edits(
    obj, facial_data, submesh_index, use_compression=True, facial_edited=None
):
    """Return sparse position-pose edits for one submesh's Blender shape keys."""
    if not facial_data or not obj.data.shape_keys:
        return []
    key_blocks = obj.data.shape_keys.key_blocks
    basis_key = key_blocks.get("Basis")
    if basis_key is None:
        return []
    quant_info = obj.get("VertexBufferQuantizeInfo", 0)
    edited_lookup = {
        (facial_object.get("ObjectIndex"), pose.get("PoseIndex")): pose.get("PoseData")
        for facial_object in (facial_edited or {}).get("Objects", [])
        for pose in facial_object.get("PositionPoseEdits", [])
    }
    object_edits = []

    for facial_object in facial_data.get("Objects", []):
        if facial_object.get("SubmeshIndex") != submesh_index:
            continue
        position = facial_object.get("Position", {})
        indices = _facial_vertex_indices(position)
        if not indices or max(indices) >= len(obj.data.vertices):
            continue
        original_poses = position.get("PoseData", [])
        object_index = facial_object.get("ObjectIndex")
        pose_count = facial_object.get("PoseCount", facial_data.get("PoseCount", 0))
        if not original_poses:
            continue
        pose_edits = []
        for pose_index in range(pose_count):
            key = basis_key if pose_index == 0 else key_blocks.get(
                f"facial_object_{facial_object.get('ObjectIndex')}_pose_{pose_index}"
            )
            if key is None or pose_index >= len(original_poses):
                continue
            source_pose_data = edited_lookup.get(
                (object_index, pose_index), original_poses[pose_index]
            )
            try:
                edited_raw = _encode_facial_key(
                    key, basis_key, source_pose_data, indices, pose_index,
                    quant_info, position.get("ComponentCount", 3),
                    position.get("ComponentSize", 2),
                )
            except ValueError as exc:
                raise ValueError(f"{obj.name}: {exc}") from exc
            if edited_raw is None:
                continue
            if edited_raw != _to_bytes(original_poses[pose_index]):
                pose_edits.append({
                    "PoseIndex": pose_index,
                    "PoseData": _from_bytes(edited_raw, use_compression),
                })
        if pose_edits:
            object_edits.append({
                "ObjectIndex": facial_object.get("ObjectIndex"),
                "PositionPoseEdits": pose_edits,
            })
    return object_edits


def update_facial_pose_edits(candidates, data, warnings):
    """Replace edits for selected facial submeshes and preserve unselected edits."""
    model = data.get("SluggiesModel", {})
    facial_data = model.get("FacialPoseData")
    if not facial_data:
        model.pop("FacialPoseDataEdited", None)
        return

    submeshes = model.get("Submeshes", [])
    use_compression = model.get("UseCompression", True)
    selected = {}
    for obj in candidates:
        submesh_index = next((
            index for index, submesh in enumerate(submeshes)
            if str(obj.get("VertexBufferOffset"))
            == str(submesh.get("VertexBuffer", {}).get("VertexBufferOffset"))
        ), None)
        if submesh_index is not None:
            selected[submesh_index] = obj

    selected_object_indices = {
        entry.get("ObjectIndex") for entry in facial_data.get("Objects", [])
        if entry.get("SubmeshIndex") in selected
    }
    retained = [
        entry for entry in model.get("FacialPoseDataEdited", {}).get("Objects", [])
        if entry.get("ObjectIndex") not in selected_object_indices
    ]
    generated = []
    for submesh_index, obj in selected.items():
        if any(entry.get("SubmeshIndex") == submesh_index
               for entry in facial_data.get("Objects", [])):
            if not obj.data.shape_keys:
                warnings.append(
                    f"{obj.name}: facial shape keys are missing; existing facial edits cleared."
                )
            generated.extend(encode_facial_pose_edits(
                obj, facial_data, submesh_index, use_compression,
                model.get("FacialPoseDataEdited"),
            ))

    edits = retained + generated
    if edits:
        model["FacialPoseDataEdited"] = {"Objects": edits}
    else:
        model.pop("FacialPoseDataEdited", None)


def _shape_key_vertex_normals(key):
    """Per-vertex normals of a shape key's shape (object space), or None
    when Blender cannot compute them."""
    try:
        flat = list(key.normals_vertex_get())
    except (AttributeError, RuntimeError, TypeError):
        return None
    return [tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)]


def _facial_shape_key_data(obj, model, submesh_index):
    """The mesh's facial shape keys as plain data for
    SkinnedRebuildExport.encode_facial_poses (the body) or
    RigidRebuildExport.encode_rigid_facial_poses (a rigid submesh), one
    entry per facial object on *submesh_index*: None when the model has no
    such object or the mesh has no Basis key (the poses are then dropped or
    neutralized)."""
    facial = model.get("FacialPoseData") or {}
    objects = [
        entry for entry in facial.get("Objects", []) if entry.get("SubmeshIndex") == submesh_index
    ]
    if not objects:
        return None
    mesh = obj.data
    key_blocks = mesh.shape_keys.key_blocks if mesh.shape_keys else None
    basis = key_blocks.get("Basis") if key_blocks else None
    if basis is None:
        return None
    basis_positions = [tuple(point.co) for point in basis.data]
    basis_normals = _shape_key_vertex_normals(basis)
    result = []
    for entry in objects:
        pose_count = int(entry.get("PoseCount", facial.get("PoseCount", 0)) or 0)
        positions, normals = [], []
        for pose in range(1, pose_count):
            key = key_blocks.get(
                SkinnedRebuildExport.FACIAL_KEY_NAME.format(object=entry.get("ObjectIndex"), pose=pose)
            )
            if key is None:
                positions.append(None)
                normals.append(None)
                continue
            positions.append([tuple(point.co) for point in key.data])
            normals.append(_shape_key_vertex_normals(key) if basis_normals is not None else None)
        result.append(SkinnedRebuildExport.FacialObjectKeys(
            object_index=int(entry.get("ObjectIndex")), pose_count=pose_count,
            pose_positions=positions, pose_normals=normals,
            basis_positions=basis_positions, basis_normals=basis_normals,
        ))
    return result


def _drop_facial_edits_for_submesh(data, submesh_index):
    """Remove FacialPoseDataEdited entries for *submesh_index*: a rebuilt
    submesh carries its poses in its rebuild's FacialPoses (or drops them),
    never as in-place pose edits."""
    model = data.get("SluggiesModel", {})
    facial = model.get("FacialPoseData") or {}
    dropped = {
        entry.get("ObjectIndex") for entry in facial.get("Objects", [])
        if entry.get("SubmeshIndex") == submesh_index
    }
    edited = model.get("FacialPoseDataEdited")
    if not edited or not dropped:
        return
    kept = [entry for entry in edited.get("Objects", []) if entry.get("ObjectIndex") not in dropped]
    if kept:
        model["FacialPoseDataEdited"] = {"Objects": kept}
    else:
        model.pop("FacialPoseDataEdited", None)
