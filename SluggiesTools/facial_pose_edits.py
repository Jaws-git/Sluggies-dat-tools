"""Facial position-pose edits of a ``.sluggie``, shared by the in-place and
the Hammerspace patcher.

A pose array is written as a whole and keeps its length: the add-on's sparse
``FacialPoseDataEdited`` arrays (Blender shape keys) replace their pose; a
pose zero without an edit follows a same-length ``VertexBufferDataEdited`` of
its submesh (pose zero is an absolute copy of the mapped GPL positions, so
moved vertices would snap back while the face animates). The in-place
patcher writes the arrays at ``PoseAbsoluteOffsets``; the Hammerspace builder
splices them into the re-serialized ptr7 section (``replace_position_poses``).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from binfmt import comp_size, decode_field


@dataclass(frozen=True)
class PoseArray:
    object_index: int
    submesh_index: int
    pose_index: int
    absolute_offset: int            # in the donor's dt_na.dat
    original: bytes
    data: bytes                     # what to write (== original: unchanged)


def _vertex_indices(position: dict) -> list[int]:
    return [
        vertex
        for run in position.get('Runs', [])
        for vertex in range(run.get('FirstVertex', 0), run.get('FirstVertex', 0) + run.get('VertexCount', 0))
    ]


def _edited_buffers(model: dict, restore: bool) -> dict:
    """``{submesh index: (original, edited, stride)}`` of same-length vertex buffers."""
    buffers = {}
    for index, submesh in enumerate(model.get('Submeshes', [])):
        vb = submesh.get('VertexBuffer', {})
        original_data = vb.get('VertexBufferData')
        edited_data = vb.get('VertexBufferDataEdited')
        if original_data is None or (edited_data is None and not restore):
            continue
        original = decode_field(original_data)
        edited = original if edited_data is None else decode_field(edited_data)
        stride = vb.get('VertexBufferCompCount', 3) * comp_size(vb.get('VertexBufferQuantizeInfo', 0))
        if len(original) == len(edited) and stride > 0 and len(original) % stride == 0:
            buffers[index] = (original, edited, stride)
    return buffers


def position_pose_arrays(model: dict, restore: bool = False) -> list[PoseArray]:
    """Every position pose array of the model's facial objects with what to write: the original with
    ``restore``, else the edit (see the module docstring). Objects whose metadata does not describe int16
    ``x y z`` records are skipped. Raises ValueError for an edit that does not fit."""
    facial = model.get('FacialPoseData')
    if not facial:
        return []
    edited_lookup = {
        (facial_object.get('ObjectIndex'), pose.get('PoseIndex')): decode_field(pose['PoseData'])
        for facial_object in (model.get('FacialPoseDataEdited') or {}).get('Objects', [])
        for pose in facial_object.get('PositionPoseEdits', [])
    }
    buffers = _edited_buffers(model, restore)
    arrays = []
    for facial_object in facial.get('Objects', []):
        object_index = facial_object.get('ObjectIndex')
        submesh_index = facial_object.get('SubmeshIndex')
        position = facial_object.get('Position', {})
        original_poses = [decode_field(data) for data in position.get('PoseData', [])]
        pose_offsets = [int(offset, 16) for offset in position.get('PoseAbsoluteOffsets', [])]
        vertex_count = position.get('EntryCount', 0)
        component_count = position.get('ComponentCount', 3)
        component_size = position.get('ComponentSize', 2)
        pose_stride = component_count * component_size
        vertex_indices = _vertex_indices(position)
        if (
            submesh_index is None
            or len(vertex_indices) != vertex_count
            or len(original_poses) != len(pose_offsets)
            or component_size != 2
            or component_count < 3
            or any(len(pose) != vertex_count * pose_stride for pose in original_poses)
        ):
            continue
        matched = buffers.get(submesh_index)
        for pose_index, (pose_offset, original_pose) in enumerate(zip(pose_offsets, original_poses)):
            sparse_edit = edited_lookup.get((object_index, pose_index))
            if restore:
                patched = original_pose
            elif sparse_edit is not None:
                if len(sparse_edit) != len(original_pose):
                    raise ValueError(f'facial object {object_index}, pose {pose_index}: edited length '
                                     f'{len(sparse_edit)} does not match original {len(original_pose)}')
                patched = sparse_edit
            elif pose_index == 0 and matched is not None:
                original, edited, vertex_stride = matched
                out = bytearray(original_pose)
                for mapped_index, vertex_index in enumerate(vertex_indices):
                    if vertex_index * vertex_stride + 6 > len(original):
                        raise ValueError(f'submesh {submesh_index}: facial vertex {vertex_index} is outside the '
                                         'editable vertex buffer')
                    before = struct.unpack_from('>3h', original, vertex_index * vertex_stride)
                    after = struct.unpack_from('>3h', edited, vertex_index * vertex_stride)
                    for component in range(3):
                        value_offset = mapped_index * pose_stride + component * component_size
                        value = struct.unpack_from('>h', out, value_offset)[0] + after[component] - before[component]
                        if not -32768 <= value <= 32767:
                            raise ValueError(f'submesh {submesh_index}: facial pose coordinate overflow in object '
                                             f'{object_index}, pose {pose_index}')
                        struct.pack_into('>h', out, value_offset, value)
                patched = bytes(out)
            else:
                patched = original_pose
            arrays.append(PoseArray(object_index, submesh_index, pose_index, pose_offset, original_pose, patched))
    return arrays


def changed_position_poses(model: dict) -> dict:
    """``{(object index, pose index): bytes}`` of the pose arrays the edits change."""
    return {(a.object_index, a.pose_index): a.data for a in position_pose_arrays(model) if a.data != a.original}


def replace_position_poses(changes: dict, kind: int, submesh_index_of):
    """A ``FacialPoseRebuild.rebuild_section`` rebuilder that swaps the position attribute's pose arrays of an
    object for the ``changes`` (``changed_position_poses``) and keeps everything else. ``kind`` is the
    position attribute kind; ``submesh_index_of(object_index)`` names the submesh its records address."""
    def rebuild(obj, object_index):
        poses = {p: data for (o, p), data in changes.items() if o == object_index}
        if not poses:
            return obj, []
        submesh = submesh_index_of(object_index)
        targets = [a for a in obj.attributes if a.kind == kind and a.submesh_index == submesh]
        if len(targets) != 1:
            raise ValueError(f'expected one position attribute on submesh {submesh}, found {len(targets)}')
        target = targets[0]
        new_poses = list(target.poses)
        for pose_index, data in poses.items():
            if not 0 <= pose_index < len(new_poses) or len(data) != len(new_poses[pose_index]):
                raise ValueError(f'pose {pose_index} edit does not fit the donor pose array')
            new_poses[pose_index] = data
        attributes = [type(target)(target.format_data, target.run_list, new_poses) if a is target else a
                      for a in obj.attributes]
        return type(obj)(obj.pose_count, attributes), []
    return rebuild
