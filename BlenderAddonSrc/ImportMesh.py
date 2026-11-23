"""Import, objects: the texture files of a model, ``build_mesh`` (geometry, UVs, normals, colours, material
slots), facial shape keys, vertex groups, and the custom submesh and stadium collision objects.

Imports ``ImportDecode`` and ``SurfaceMaterials``, and never ``ImportSluggies``.
"""

import bpy
import os
import math
import struct
import mathutils
from . import StadiumCollisionExport
from . import FieldCodec
from . import ModelCheckCore
from .ImportDecode import (
    _decode_facial_pose,
    _decode_rebuild_facial_poses,
    _facial_edit_lookup,
    _facial_vertex_indices,
    _surface_face_ranges,
    _surface_ranges_from_face_ids,
    _to_bytes,
    decode_color_channel,
    decode_normal_buffer,
    decode_uv_channel,
)
from .SurfaceMaterials import (
    _create_material,
    _custom_submesh_materials,
    _resolve_texture_image_path,
    _set_surface_material_metadata,
)


def _texture_file_map(texture_descriptors):
    """Return {TextureIndex: filename} from sluggie metadata.

    Falls back to the legacy numeric file name when TextureFileName is absent.
    """
    mapping = {}
    for tex in texture_descriptors or []:
        tex_idx = tex.get("TextureIndex")
        if tex_idx is None:
            continue
        mapping[tex_idx] = tex.get("TextureFileName") or f"{tex_idx}.png"
    return mapping


def _assigned_surface_texture_files(model, texture_file_map):
    """{SurfaceId: PNG name} of the exported ``DesiredTextureAssignments``.

    An index below the donor descriptor count names a donor texture; a higher
    one an ``AdditionalTextureDescriptors`` entry (appended in that order by
    the export). The surface material then shows that PNG, so a re-import
    followed by an export assigns it again instead of dropping it."""
    assignments = model.get("DesiredTextureAssignments") or {}
    if not assignments:
        return {}
    donor_count = len(model.get("TextureDescriptors") or [])
    additions = model.get("AdditionalTextureDescriptors") or []
    files = {}
    for surface_id, index in assignments.items():
        try:
            index = int(index)
        except (TypeError, ValueError):
            continue
        if index < donor_count:
            name = (texture_file_map or {}).get(index)
        elif index - donor_count < len(additions):
            name = additions[index - donor_count].get("TextureFileName")
        else:
            name = None
        if name:
            files[str(surface_id)] = name
    return files


def _texture_file_map_from_sibling_sluggie(current_sluggie_path, model):
    """Return a fallback texture file map from a sibling .sluggie when needed.

    Some lowpoly (_L) exports have no local TextureDescriptors, but their sibling
    export folder still has the canonical hashed TextureFileName mapping.
    """
    current_dir = os.path.dirname(current_sluggie_path)
    parent_dir = os.path.dirname(current_dir)
    if not parent_dir or not os.path.isdir(parent_dir):
        return {}

    target_chunk = model.get("ChunkNumber")
    target_submesh_count = len(model.get("Submeshes", []))

    candidates = []
    for sibling_name in sorted(os.listdir(parent_dir)):
        sibling_dir = os.path.join(parent_dir, sibling_name)
        if not os.path.isdir(sibling_dir) or sibling_dir == current_dir:
            continue
        try:
            sluggies = [
                name for name in os.listdir(sibling_dir)
                if name.lower().endswith('.sluggie')
            ]
        except OSError:
            continue
        for slug_name in sluggies:
            candidates.append(os.path.join(sibling_dir, slug_name))

    # Variants saved under new names tie on the score below; the file named
    # like this one (else the exported one) wins the tie.
    partner = ModelCheckCore.pick_partner(candidates, current_sluggie_path)
    best_map = {}
    best_score = -1
    for candidate_path in candidates:
        try:
            candidate_root = FieldCodec.load_sluggie(candidate_path)
        except (OSError, ValueError):
            continue
        candidate_model = candidate_root.get("SluggiesModel", {})
        candidate_desc = candidate_model.get("TextureDescriptors", [])
        candidate_map = _texture_file_map(candidate_desc)
        if not candidate_map:
            continue

        score = 0
        if candidate_model.get("ChunkNumber") == target_chunk:
            score += 4
        if len(candidate_model.get("Submeshes", [])) >= target_submesh_count:
            score += 2
        if candidate_model.get("TEXHeader"):
            score += 1
        if candidate_path == partner:
            score += 0.5
        if score > best_score:
            best_score = score
            best_map = candidate_map

    return best_map


def _add_rebuild_facial_shape_keys(obj, rebuild_poses, record_stride=12):
    """Shape keys of a rebuilt submesh (``SkinnedRebuild.FacialPoses`` or
    ``RigidRebuild.FacialPoses``): the donor's run lists name donor
    vertices, so the keys come from the rebuild's own deltas, in its vertex
    order."""
    decoded = _decode_rebuild_facial_poses(rebuild_poses, obj.get("VertexBufferQuantizeInfo", 0), record_stride)
    if not any(poses for _object_index, poses in decoded):
        return 0
    basis_key = obj.shape_key_add(name="Basis", from_mix=False)
    shape_key_count = 0
    for object_index, poses in decoded:
        for pose_index, entries in enumerate(poses, start=1):
            key = obj.shape_key_add(name=f"facial_object_{object_index}_pose_{pose_index}", from_mix=False)
            key.value = 0.0
            for vertex_index, delta in entries:
                if vertex_index < len(key.data):
                    basis = basis_key.data[vertex_index].co
                    key.data[vertex_index].co = tuple(basis[axis] + delta[axis] for axis in range(3))
            shape_key_count += 1
    return shape_key_count


def add_facial_shape_keys(obj, submesh_index, facial_data, facial_edited=None, rebuild_poses=None,
                          rebuild_record_stride=12):
    """Add facial expression keys; Blender Basis represents pose zero.
    *rebuild_poses* (a rebuild's ``FacialPoses``, possibly empty) replaces
    the donor's poses on a rebuilt submesh, whose vertices the donor run
    lists no longer describe; *rebuild_record_stride* is 12 for the body's
    interleaved records and 6 for a rigid submesh's."""
    if rebuild_poses is not None:
        return _add_rebuild_facial_shape_keys(obj, rebuild_poses, rebuild_record_stride)
    if not facial_data:
        return 0
    facial_objects = [
        entry for entry in facial_data.get("Objects", [])
        if entry.get("SubmeshIndex") == submesh_index
    ]
    if not facial_objects:
        return 0

    expression_objects = [
        facial_object for facial_object in facial_objects
        if facial_object.get("PoseCount", facial_data.get("PoseCount", 0)) > 1
    ]
    if not expression_objects:
        return 0
    quant_info = obj.get("VertexBufferQuantizeInfo", 0)
    edited_lookup = _facial_edit_lookup(facial_edited)
    basis_key = obj.shape_key_add(name="Basis", from_mix=False)

    for facial_object in facial_objects:
        object_index = facial_object.get("ObjectIndex")
        position = facial_object.get("Position", {})
        pose_zero_edit = edited_lookup.get((object_index, 0))
        if pose_zero_edit is None:
            continue
        indices = _facial_vertex_indices(position)
        coordinates = _decode_facial_pose(
            pose_zero_edit, quant_info,
            position.get("ComponentCount", 3),
            position.get("ComponentSize", 2),
        )
        if len(indices) != len(coordinates):
            continue
        for vertex_index, coordinate in zip(indices, coordinates):
            if vertex_index < len(basis_key.data):
                basis_key.data[vertex_index].co = coordinate
                # The mesh's own vertices too, so Basis and the mesh agree in Object Mode.
                obj.data.vertices[vertex_index].co = coordinate

    shape_key_count = 0
    for facial_object in expression_objects:
        object_index = facial_object.get("ObjectIndex")
        position = facial_object.get("Position", {})
        indices = _facial_vertex_indices(position)
        original_poses = position.get("PoseData", [])
        pose_count = facial_object.get("PoseCount", facial_data.get("PoseCount", 0))
        for pose_index in range(1, pose_count):
            key = obj.shape_key_add(
                name=f"facial_object_{object_index}_pose_{pose_index}",
                from_mix=False,
            )
            key.value = 0.0
            position = facial_object.get("Position", {})
            pose_data = edited_lookup.get(
                (object_index, pose_index),
                original_poses[pose_index] if pose_index < len(original_poses) else None,
            )
            if pose_data is None:
                continue
            coordinates = _decode_facial_pose(
                pose_data, quant_info,
                position.get("ComponentCount", 3),
                position.get("ComponentSize", 2),
            )
            if len(indices) != len(coordinates):
                continue
            for vertex_index, coordinate in zip(indices, coordinates):
                if vertex_index < len(key.data):
                    basis = basis_key.data[vertex_index].co
                    key.data[vertex_index].co = tuple(
                        basis[axis] + coordinate[axis] for axis in range(3)
                    )
            shape_key_count += 1

    return shape_key_count


def _set_loop_color(entry, rgba):
    """Write one donor color into a BYTE_COLOR attribute without a curve change.

    The donor entries are raw normalized channel values, not scene-linear ones.
    ``entry.color`` applies an sRGB<->linear conversion on top of the 8-bit
    storage, which is not reversible: 73 of the 256 byte values come back from
    the export accessor shifted by one. ``color_srgb`` maps straight onto the
    stored bytes, so an untouched color attribute re-exports byte-for-byte.
    Mirrors ``_get_loop_color`` in ExportInplaceMesh.
    """
    try:
        entry.color_srgb = rgba
    except AttributeError:
        entry.color = rgba


def build_mesh(name, positions, normals, faces, vb_meta, collection,
               uv_channels=None, color_channels=None,
               face_texture_indices=None, sluggie_dir=None,
               submesh_meta=None, prebuilt_materials=None,
               texture_file_map=None, face_surface_ids=None, surface_texture_files=None):
    """Create a Blender mesh object from a vertex list and link it to *collection*.

    *face_surface_ids*, when given, names the surface of every face directly
    (a rebuilt rigid submesh routes faces by key), instead of the donor's
    cumulative ``FaceCount`` ranges. *surface_texture_files* ({SurfaceId:
    PNG name}, ``_assigned_surface_texture_files``) overrides the image of a
    new surface material; its ``TextureIndex`` stays the donor's."""
    # Checked before the mesh exists: a face that names a vertex the buffer does not have makes a mesh
    # Blender can crash on. No exported model has one (all of 2_Output_Assets, 2026-11-22).
    highest = max((index for face in faces for index in face), default=-1)
    if highest >= len(positions):
        raise ValueError(f"{name}: a face uses vertex {highest}, but the submesh has {len(positions)} vertices")
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions, [], faces)
    mesh.update()

    n_normals = len(normals)
    if normals and n_normals == len(positions) and len(faces):
        max_face_idx = max(i for face in faces for i in face)
        if max_face_idx < n_normals:
            #mesh.use_auto_smooth = True
            normals_per_loop = [normals[i] for face in faces for i in face]
            mesh.normals_split_custom_set(normals_per_loop)
        # else: face indices exceed vertex buffer — skip custom normals silently

    # Per-loop normals from the standalone NormalBuffer (non-skinned meshes):
    # the donor values become custom split normals so unedited loops round-trip
    # unchanged through the Blender export (plan 3.3, item 1) — mirrors the
    # interleaved CompCount==6 path above, using NormalFacesData per-loop
    # indices instead of position indices.
    if not mesh.has_custom_normals and faces:
        normal_buffer = submesh_meta.get("NormalBuffer") if submesh_meta else None
        if (
            normal_buffer
            and normal_buffer.get("NormalBufferData")
            and normal_buffer.get("NormalFacesData")
        ):
            buffer_normals = decode_normal_buffer(normal_buffer)
            nf_raw = _to_bytes(normal_buffer["NormalFacesData"])
            nf_count = len(nf_raw) // 2
            nf_flat = list(struct.unpack(f'>{nf_count}H', nf_raw)) if nf_count else []
            loop_count = sum(len(f) for f in faces)
            if nf_count == loop_count and max(nf_flat) < len(buffer_normals):
                mesh.normals_split_custom_set([buffer_normals[i] for i in nf_flat])
            else:
                print(
                    f"WARNING: {name}: NormalFacesData has {nf_count} loop indices "
                    f"but the mesh has {loop_count} loops; per-loop normals skipped"
                )

    if uv_channels:
        for ch_ind, uv_channel in enumerate(uv_channels):
            coords, uv_faces = decode_uv_channel(uv_channel)
            if not coords or not uv_faces:
                continue
            # Positional names ("uv0", "uv1", ...) so joined objects share
            # their UV layers instead of collecting one per model name.
            uv_layer = mesh.uv_layers.new(name=f"uv{ch_ind}")
            for poly in mesh.polygons:
                face_idx = poly.index
                if face_idx >= len(uv_faces):
                    continue
                uv_tri = uv_faces[face_idx]
                for loop_offset, loop_idx in enumerate(poly.loop_indices):
                    uv_idx = uv_tri[loop_offset % 3]
                    if uv_idx < len(coords):
                        s, t = coords[uv_idx]
                        # GX V increases downward; flip to match Blender convention
                        uv_layer.data[loop_idx].uv = (s, 1.0 - t)

    if color_channels:
        for color_channel in color_channels:
            ch_idx = color_channel.get("ColorChannelIndex", 0)
            colors, color_faces = decode_color_channel(color_channel)
            if not colors or not color_faces:
                continue
            layer_name = f"color{ch_idx}"
            vcol_layer = mesh.color_attributes.new(name=layer_name, type='BYTE_COLOR', domain='CORNER')
            for poly in mesh.polygons:
                face_idx = poly.index
                if face_idx >= len(color_faces):
                    continue
                col_tri = color_faces[face_idx]
                for loop_offset, loop_idx in enumerate(poly.loop_indices):
                    col_idx = col_tri[loop_offset % 3]
                    if col_idx < len(colors):
                        _set_loop_color(vcol_layer.data[loop_idx], colors[col_idx])

    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    # Store vertex buffer metadata as custom properties so the exporter can
    # match this object back to the correct submesh and re-quantize correctly.
    if vb_meta is not None:
        for prop in ("VertexBufferOffset", "VertexBufferLength",
                     "VertexBufferCompCount", "VertexBufferQuantizeInfo"):
            if prop in vb_meta:
                obj[prop] = vb_meta[prop]

    if submesh_meta is not None:
        # Compute once — needed for both shader-mode properties and material creation.
        display_states = submesh_meta.get("DisplayStates", [])
        surface_ranges = (
            _surface_ranges_from_face_ids(face_surface_ids) if face_surface_ids is not None
            else _surface_face_ranges(display_states)
        )

        # Object-level shader mode properties are only written for the legacy path
        # (old exports without FaceCount).  When per-surface materials exist the
        # shader mode lives on mat["ShaderMode"] instead.
        if surface_ranges is None:
            for ds_idx, ds in enumerate(display_states):
                if ds.get("DisplayStateId") != 7:
                    continue
                surface_id = ds.get("SurfaceId") or f"ds{ds_idx}"
                prop_val = f"DS_{surface_id}_ShaderMode"
                obj[prop_val] = ds.get("ShaderModeEdited") or ds.get("ShaderMode", "")
                obj.id_properties_ui(prop_val).update(
                    description=(
                        f"Surface {surface_id} shader mode (legacy — re-export to get per-surface materials). "
                        "Known 4-char names: Spec=specular  Shdw=no-specular  SpRf=specular-reflection  "
                        "RhSp/LhSp=right/left-hand-specular  GhSp=ghost."
                    ))
    else:
        display_states = []
        surface_ranges = None

    # Store UV channel material binding metadata as custom properties
    if uv_channels:
        for uv_channel in uv_channels:
            ch_ind = uv_channel.get("UVChannelIndex", 0)
            prefix = f"UV{ch_ind}_"
            tex_idx = uv_channel.get("TextureIndex")
            wrap_s  = uv_channel.get("WrapS")
            wrap_t  = uv_channel.get("WrapT")
            if tex_idx is not None:
                obj[prefix + "TextureIndex"] = tex_idx
                obj.id_properties_ui(prefix + "TextureIndex").update(
                    description=f"TPL texture index bound to UV channel {ch_ind}")
            if wrap_s is not None:
                obj[prefix + "WrapS"] = wrap_s
                obj.id_properties_ui(prefix + "WrapS").update(
                    description=f"GX S-axis wrap mode for UV channel {ch_ind}")
            if wrap_t is not None:
                obj[prefix + "WrapT"] = wrap_t
                obj.id_properties_ui(prefix + "WrapT").update(
                    description=f"GX T-axis wrap mode for UV channel {ch_ind}")

    # Create Blender materials and assign per-face material indices.
    # New path: one material per face-owning draw state (SurfaceId-keyed).
    # Legacy fallback: one material per unique texture index for old exports.
    if surface_ranges is not None and uv_channels and face_texture_indices:
        # Build texture-index → UV channel map for node tree setup
        tex_to_uv = {}
        for uv_ch in uv_channels:
            idx = uv_ch.get('TextureIndex')
            if idx is not None and idx not in tex_to_uv:
                tex_to_uv[idx] = uv_ch

        mat_slot = {}  # surface_id -> material slot index
        for surface_id, start_fi, fc in surface_ranges:
            if surface_id in mat_slot:
                continue   # a surface listed in several face runs keeps one slot
            slot_idx = len(mat_slot)
            fi_tex = face_texture_indices[start_fi] if start_fi < len(face_texture_indices) else 0
            uv_ch = tex_to_uv.get(fi_tex) or (uv_channels[0] if uv_channels else None)
            wrap_s = uv_ch.get('WrapS', 1) if uv_ch else 1
            wrap_t = uv_ch.get('WrapT', 1) if uv_ch else 1

            if prebuilt_materials is not None and surface_id in prebuilt_materials:
                mat = prebuilt_materials[surface_id]
            elif sluggie_dir is not None:
                tex_file = (texture_file_map or {}).get(fi_tex, f'{fi_tex}.png')
                assigned_file = (surface_texture_files or {}).get(surface_id)
                img_path = assigned_file and _resolve_texture_image_path(sluggie_dir, assigned_file)
                if not img_path:                   # no assignment, or its PNG is gone: the donor's
                    img_path = _resolve_texture_image_path(sluggie_dir, tex_file)
                image = bpy.data.images.load(img_path, check_existing=True) if img_path else None
                mat = _create_material(f'{name}_{surface_id}', image, wrap_s)
                # Store surface identity and editable properties on the material
                ds_entry = next((ds for ds in display_states if ds.get("SurfaceId") == surface_id), {})
                mat["SurfaceId"] = surface_id
                mat["TextureIndex"] = fi_tex
                mat["WrapS"] = wrap_s
                mat["WrapT"] = wrap_t
                _set_surface_material_metadata(mat, ds_entry)
                mat.id_properties_ui("SurfaceId").update(
                    description="Stable draw-state identity (sm{n}_ds{n}). Do not delete — export resolves this material by SurfaceId.")
                mat.id_properties_ui("TextureIndex").update(
                    description="TPL texture slot index active for this draw state.")
                mat.id_properties_ui("WrapS").update(
                    description="GX S-axis wrap mode for this draw state (0=extend, 1=repeat, 2=mirror).")
                mat.id_properties_ui("WrapT").update(
                    description="GX T-axis wrap mode for this draw state (0=extend, 1=repeat, 2=mirror).")
            else:
                continue

            mat_slot[surface_id] = slot_idx
            obj.data.materials.append(mat)

        # Assign faces to their draw-state material by cumulative face range
        for surface_id, start_fi, fc in surface_ranges:
            slot = mat_slot.get(surface_id, 0)
            for fi in range(start_fi, min(start_fi + fc, len(mesh.polygons))):
                mesh.polygons[fi].material_index = slot

    elif uv_channels and face_texture_indices and sluggie_dir is not None:
        # Legacy: per-texture materials (old exports without FaceCount)
        tex_to_uv = {}
        for uv_ch in uv_channels:
            idx = uv_ch.get('TextureIndex')
            if idx is not None and idx not in tex_to_uv:
                tex_to_uv[idx] = uv_ch

        unique_tex = sorted(set(face_texture_indices))
        mat_slot = {}  # TextureIndex -> material slot index
        for slot_idx, tex_idx in enumerate(unique_tex):
            mat_slot[tex_idx] = slot_idx
            uv_ch = tex_to_uv.get(tex_idx) or uv_channels[0]
            wrap_s = uv_ch.get('WrapS', 1)
            tex_file = (texture_file_map or {}).get(tex_idx, f'{tex_idx}.png')
            img_path = _resolve_texture_image_path(sluggie_dir, tex_file)
            image = bpy.data.images.load(img_path, check_existing=True) if img_path else None
            mat = _create_material(f'{name}_mat{tex_idx}', image, wrap_s)
            obj.data.materials.append(mat)

        for poly in mesh.polygons:
            fi = poly.index
            tex_idx = face_texture_indices[fi] if fi < len(face_texture_indices) else unique_tex[0]
            poly.material_index = mat_slot.get(tex_idx, 0)

    elif prebuilt_materials is not None and face_texture_indices:
        # Legacy prebuilt: reuse by tex_idx (old format, int keys)
        available_tex = sorted(t for t in set(face_texture_indices) if t in prebuilt_materials)
        mat_slot = {}
        for slot_idx, tex_idx in enumerate(available_tex):
            mat_slot[tex_idx] = slot_idx
            obj.data.materials.append(prebuilt_materials[tex_idx])

        fallback_slot = 0
        for poly in mesh.polygons:
            fi = poly.index
            tex_idx = face_texture_indices[fi] if fi < len(face_texture_indices) else (available_tex[0] if available_tex else 0)
            poly.material_index = mat_slot.get(tex_idx, fallback_slot)

    return obj


def _apply_import_rotation(obj):
    obj.matrix_world = obj.matrix_world @ mathutils.Matrix.Rotation(math.pi / 2, 4, 'X')


def add_vertex_groups(obj, submesh_index, bone_list, arm_obj, owner_bone_id=None, influences=None):
    """Add vertex groups from BoneHierarchy for *submesh_index* and attach an
    Armature modifier pointing at *arm_obj*.

    With *owner_bone_id* (a rigid submesh whose owner the file moved, or a
    rebuilt one whose vertices the donor influences do not cover) every
    vertex goes into ``bone_<owner>`` at weight 1.0 instead. With
    *influences* (``[(vertex, bone, weight)]`` of a rebuilt body) the groups
    come from those records."""
    if influences is not None:
        num_verts = len(obj.data.vertices)
        for v_idx, bone_id, weight in influences:
            group_name = f"bone_{bone_id}"
            vg = obj.vertex_groups.get(group_name)
            if vg is None:
                vg = obj.vertex_groups.new(name=group_name)
            if v_idx < num_verts:
                vg.add([v_idx], weight, 'REPLACE')
    elif owner_bone_id is not None:
        vg = obj.vertex_groups.new(name=f"bone_{owner_bone_id}")
        vg.add(list(range(len(obj.data.vertices))), 1.0, 'REPLACE')
    else:
        for bd in bone_list:
            for entry in bd.get('VertexInfluences', []):
                if entry['SubmeshIndex'] != submesh_index:
                    continue
                group_name = f"bone_{bd['BoneId']}"
                vg = obj.vertex_groups.get(group_name)
                if vg is None:
                    vg = obj.vertex_groups.new(name=group_name)
                num_verts = len(obj.data.vertices)
                raw = _to_bytes(entry['Influences'])
                for v_idx, weight in struct.iter_unpack('>Hf', raw):
                    if v_idx < num_verts:
                        vg.add([v_idx], weight, 'REPLACE')

    mod = obj.modifiers.new(name="Armature", type='ARMATURE')
    mod.object = arm_obj


def build_custom_submesh(cs, model, arm_obj, bind_matrices, collection, sluggie_dir, texture_file_map):
    """Blender object for a decoded custom submesh *cs*
    (CustomSubmeshImport.CustomSubmeshMesh): bone-local vertices placed at the
    host bone's bind matrix (where the exporter reads them back from), weighted
    to the host bone, with the object properties Add New Submesh writes."""
    mesh = bpy.data.meshes.new(cs.name)
    mesh.from_pydata(cs.positions, [], cs.faces)
    mesh.update()
    # from_pydata lays the loops out face by face, so loop k is corner k.
    if cs.loop_uvs is not None:
        uv_layer = mesh.uv_layers.new(name="uv0")
        for loop, uv in enumerate(cs.loop_uvs):
            uv_layer.data[loop].uv = uv
    if cs.loop_colors is not None:
        colors = mesh.color_attributes.new(name="color0", type='BYTE_COLOR', domain='CORNER')
        for loop, rgba in enumerate(cs.loop_colors):
            _set_loop_color(colors.data[loop], rgba)
    if cs.loop_normals is not None:
        mesh.normals_split_custom_set(cs.loop_normals)

    obj = bpy.data.objects.new(cs.name, mesh)
    collection.objects.link(obj)
    obj['SluggiesCustomSubmesh'] = True
    obj['CustomSubmeshId'] = cs.custom_submesh_id
    obj['TemplateSource'] = cs.template_source

    for mat in _custom_submesh_materials(obj.name, cs, model, sluggie_dir, texture_file_map):
        mesh.materials.append(mat)
    for poly, surface in zip(mesh.polygons, cs.face_surfaces):
        poly.material_index = surface

    group = obj.vertex_groups.new(name=f"bone_{cs.host_bone_id}")
    group.add(list(range(len(mesh.vertices))), 1.0, 'REPLACE')
    modifier = obj.modifiers.new(name="Armature", type='ARMATURE')
    modifier.object = arm_obj
    obj.parent = arm_obj
    obj.matrix_parent_inverse = mathutils.Matrix.Identity(4)
    obj.matrix_basis = bind_matrices[cs.host_bone_id]
    return obj


def build_collision_object(entry, arm_obj, collection):
    """A collision-only mesh for one ``StadiumCollision`` entry: model-space
    vertices under the armature (identity basis, where the exporter reads them
    back from), drawn as wire, with its collision settings restored. A model
    mesh or custom submesh that was solid comes back without the setting; this
    object carries it instead."""
    # Everything is read from the entry before anything is built, so a bad entry leaves nothing behind.
    region = entry.get('Region')
    name = entry['Name'] if region is not None else f"{entry['Name']}_collision"
    positions = [tuple(p) for p in entry['Positions']]
    faces = [tuple(f) for f in entry['Faces']]
    highest = max((index for face in faces for index in face), default=-1)
    if highest >= len(positions):
        raise ValueError(f"{name}: a face uses vertex {highest}, but it has {len(positions)} vertices")
    number = int(entry['SurfaceType'])
    sides = next((i for i, v, _l, _d in StadiumCollisionExport.SIDES if v == entry.get('Sides', 'both')), None)
    territory = next(
        (i for i, v, _l, _d in StadiumCollisionExport.TERRITORIES if v == entry.get('Territory', 'auto')), None)
    if sides is None or territory is None:
        raise ValueError(
            f"{name}: unknown Sides {entry.get('Sides')!r} or Territory {entry.get('Territory')!r}")

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(mesh.name, mesh)
    collection.objects.link(obj)
    obj.parent = arm_obj
    obj.matrix_parent_inverse = mathutils.Matrix.Identity(4)
    obj.matrix_basis = mathutils.Matrix.Identity(4)
    obj.display_type = 'WIRE'
    settings = obj.sluggies_collision
    settings.enabled = True
    settings.invisible = True  # a collision-only object, never drawn
    if region is not None:
        obj[StadiumCollisionExport.REGION_PROP] = int(region)
        obj.hide_render = True
    named = next((ident for ident, n, _l, _d in StadiumCollisionExport.SURFACE_TYPES if n == number), None)
    settings.surface_type = named or 'CUSTOM'
    if named is None:
        settings.custom_type = number
    settings.sides = sides
    settings.territory = territory
    return obj
