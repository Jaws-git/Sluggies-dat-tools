"""Export, the scene side of the rebuild paths: rest pose and bind matrices, geometry of a custom submesh in its
bone's space, loop attributes, the custom submesh and stadium collision entries, and the plans and entries
of ``RigidRebuild`` / ``SkinnedRebuild`` (the bpy-free encoders are ``RigidRebuildExport``,
``SkinnedRebuildExport`` and ``CustomSubmeshExport``).

``_BIND_MATRICES`` is a cache changed in place (the operator empties it when an export starts). Last in
the import order of the ``Export*`` modules; never imports ``ExportSluggies``.
"""

import contextlib
from dataclasses import dataclass
from . import CustomSubmeshExport
from . import ExportMode
from . import RigidRebuildExport
from . import SkinnedRebuildExport
from . import HostBones
from . import StadiumCollisionExport
from .ExportInplaceMesh import (
    _find_uv_layer,
    _get_loop_color,
    _per_loop_normals,
)
from .ExportBones import (
    _armature_donor_bone_records,
    _bone_geo_id_raw,
    _candidate_rigid_retargets,
    _detect_uniform_vertex_bone_id,
    _parse_bone_group_name,
)
from .ExportFacial import (
    _facial_shape_key_data,
)
from .ExportSkin import (
    skin_membership_changed,
)
from .ExportTextures import (
    NEW_SURFACE_BODY_MESSAGE,
    _custom_submesh_material,
)
from .ExportSurfaces import (
    _effective_type7_modes,
)


def _custom_submesh_triangles(obj):
    """Triangulate a custom submesh for export without changing the user's mesh: reads Blender's own loop
    triangles instead of running a Triangulate operator or modifier."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    return CustomSubmeshExport.triangles_from_loop_triangles(mesh.loop_triangles)


def encode_stadium_collision(context, objects, arm_obj, warnings):
    """``StadiumCollision`` entries for the meshes with collision turned on
    (StadiumCollisionExport.py): model-space faces, read like a custom
    submesh's (undeformed mesh, rest pose, transforms applied mathematically)."""
    if not objects:
        return []
    if arm_obj is None:
        raise ValueError(
            f"{objects[0].name}: stadium collision needs the stadium's armature; "
            "select it too (or parent the collision mesh to it)")
    with _armature_rest_pose(context, arm_obj):
        arm_world = _matrix_rows(arm_obj.matrix_world)
        obj_worlds = [_matrix_rows(obj.matrix_world) for obj in objects]
    entries = []
    for obj, obj_world in zip(objects, obj_worlds):
        if obj.mode == 'EDIT':
            obj.update_from_editmode()
        skipped = CustomSubmeshExport.unsupported_modifier_names(
            (modifier.name, modifier.type) for modifier in obj.modifiers)
        if skipped:
            warnings.append(
                f"{obj.name}: modifier(s) {', '.join(skipped)} are not evaluated for its "
                "collision; apply them first or that geometry is not solid in game.")
        settings = obj.sluggies_collision
        try:
            surface_type = StadiumCollisionExport.surface_type_number(
                settings.surface_type, settings.custom_type)
            positions, faces = StadiumCollisionExport.model_space_geometry(
                [tuple(v.co) for v in obj.data.vertices], _custom_submesh_triangles(obj),
                obj_world, arm_world)
            entries.append(StadiumCollisionExport.collision_entry(
                obj.name, positions, faces, surface_type, settings.sides, settings.territory,
                region=obj.get(StadiumCollisionExport.REGION_PROP)))
        except ValueError as exc:
            raise ValueError(f"{obj.name}: {exc}" if not str(exc).startswith(obj.name) else str(exc)) from exc
    return entries


def _custom_submesh_armature(obj):
    """The armature a custom submesh follows: its Armature modifier's object,
    else the nearest armature parent."""
    for modifier in obj.modifiers:
        if modifier.type == 'ARMATURE' and modifier.object is not None:
            return modifier.object
    node = obj.parent
    while node is not None:
        if node.type == 'ARMATURE':
            return node
        node = node.parent
    return None


@contextlib.contextmanager
def _armature_rest_pose(context, arm_obj):
    """Evaluate the scene with *arm_obj* in its rest pose, then restore the
    user's pose setting. An object parented to a bone (or constrained to one)
    gets a pose-dependent ``matrix_world``; the rest pose makes it match the
    bind pose the game positions are relative to."""
    arm_data = arm_obj.data
    previous = arm_data.pose_position
    if previous == 'REST':               # already there (the user's setting, or an enclosing use): nothing to do
        yield
        return
    try:
        arm_data.pose_position = 'REST'
        context.view_layer.update()
        yield
    finally:
        arm_data.pose_position = previous
        context.view_layer.update()


def _matrix_rows(matrix):
    return [list(row) for row in matrix]


def _armature_local_geometry(context, obj, warnings):
    """The skinned body's geometry in armature (model) space: the same steps
    as _custom_submesh_bone_local_geometry with the identity in place of a
    host bone's bind matrix, since skinned positions are stored in model
    space (the SKN bind pose mirrors them)."""
    return _geometry_in_bind_space(context, obj, SkinnedRebuildExport.IDENTITY, warnings)


# [the BoneHierarchy list, its bind matrices] of the last call: every rigid and
# custom object of an export asks for the same list, and the answer is a
# pure-Python matrix product per bone. Matched by identity (the list is kept
# alive here), emptied when an export starts.
_BIND_MATRICES = [None, None]


def _bind_matrices(bone_hierarchy):
    if _BIND_MATRICES[0] is not bone_hierarchy:
        _BIND_MATRICES[:] = [bone_hierarchy, CustomSubmeshExport.bone_absolute_matrices(bone_hierarchy)]
    return _BIND_MATRICES[1]


def _custom_submesh_bone_local_geometry(context, obj, bone_hierarchy, host_bone_id, warnings):
    """World position is authoritative.

    Transforms are applied mathematically, never with ``transform_apply``, so
    the export leaves the user's scene as it was:
    1. flush Edit Mode into the mesh data,
    2. read the undeformed ``mesh.vertices`` (never the evaluated mesh, so the
       Armature modifier and the current pose can't leak into bind positions),
    3-4. map object-local coordinates through the rest-pose world matrices into
       the host bone's bind-local space, using the target .sluggie's own
       BoneHierarchy bind matrices (the importer's placement matrices),
    6. reverse winding when that transform mirrors,
    7. warn about modifiers that export doesn't evaluate.
    Item 5 (normals) uses the returned ``to_bone`` matrix.
    """
    arm_obj = _custom_submesh_armature(obj)
    if arm_obj is None:
        raise ValueError(
            f"{obj.name}: custom submesh has no Armature modifier or armature parent; "
            "it can't be placed relative to its host bone"
        )
    host_bind = _bind_matrices(bone_hierarchy).get(host_bone_id)
    if host_bind is None:
        raise ValueError(
            f"{obj.name}: host bone {host_bone_id} does not exist in the target "
            ".sluggie BoneHierarchy"
        )
    return _geometry_in_bind_space(context, obj, host_bind, warnings)


def _geometry_in_bind_space(context, obj, host_bind, warnings):
    """Items 1-7 of _custom_submesh_bone_local_geometry for an already
    resolved *host_bind* matrix (armature-space bind matrix of the host
    bone, or the identity for the skinned body)."""
    arm_obj = _custom_submesh_armature(obj)
    if arm_obj is None:
        raise ValueError(
            f"{obj.name}: mesh has no Armature modifier or armature parent; "
            "it can't be placed relative to the skeleton"
        )
    if obj.mode == 'EDIT':
        obj.update_from_editmode()

    skipped = CustomSubmeshExport.unsupported_modifier_names(
        (modifier.name, modifier.type) for modifier in obj.modifiers
    )
    if skipped:
        warnings.append(
            f"{obj.name}: modifier(s) {', '.join(skipped)} are not evaluated on export; "
            "apply them first or their geometry is missing in game."
        )

    with _armature_rest_pose(context, arm_obj):
        obj_world = _matrix_rows(obj.matrix_world)
        arm_world = _matrix_rows(arm_obj.matrix_world)

    vertex_cos = [tuple(v.co) for v in obj.data.vertices]
    try:
        return CustomSubmeshExport.bone_local_geometry(
            vertex_cos, _custom_submesh_triangles(obj), obj_world, arm_world, host_bind,
        )
    except ValueError as exc:
        raise ValueError(f"{obj.name}: {exc}") from exc


def _custom_submesh_loop_attributes(obj, warnings):
    """Per-loop normals, UVs and colors of a custom submesh, indexed by Blender
    loop index. Call after the geometry,
    which flushes Edit Mode first.

    - Normals are Blender's loop normals (custom split normals when present).
    - UVs come from the render-active UV map. Only one map is exported; the
      game's second channel mirrors it.
    - Colors come from ``color0`` (the importer's name) or else the active
      color attribute, on corner or point domain. No color attribute means
      white.
    """
    mesh = obj.data
    loop_normals = _per_loop_normals(mesh, range(len(mesh.loops)))

    uv_layer = mesh.uv_layers.active_render or mesh.uv_layers.active
    if uv_layer is None:
        raise ValueError(f"{obj.name}: custom submesh has no UV map; add one and unwrap it")
    if len(mesh.uv_layers) > 1:
        warnings.append(
            f"{obj.name}: only UV map '{uv_layer.name}' (render-active) is exported; "
            "other UV maps are ignored."
        )
    loop_uvs = [tuple(uv_layer.data[i].uv) for i in range(len(mesh.loops))]

    color_attribute = mesh.color_attributes.get("color0") or mesh.color_attributes.active_color
    loop_colors = None
    if color_attribute is not None:
        if color_attribute.domain == 'CORNER':
            loop_colors = [tuple(_get_loop_color(color_attribute.data[i])) for i in range(len(mesh.loops))]
        elif color_attribute.domain == 'POINT':
            loop_colors = [
                tuple(_get_loop_color(color_attribute.data[loop.vertex_index])) for loop in mesh.loops
            ]
    return loop_normals, loop_uvs, loop_colors


def _custom_submesh_additional_materials(obj):
    """The Add-material surfaces of a custom submesh that at least one face
    uses, keyed by surface key in slot order. A new-surface material no face uses is reported as ignored."""
    custom_id = str(obj.get("CustomSubmeshId"))
    used_slots = {poly.material_index for poly in obj.data.polygons}
    materials, unused = {}, []
    for slot_index, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None or not mat.get("SluggiesNewSurface") or mat.get("SluggiesSurfaceOwner") != custom_id:
            continue
        key = mat.get("SurfaceId")
        if not key or not RigidRebuildExport.new_surface_key_matches(custom_id, key):
            continue
        if slot_index in used_slots:
            materials.setdefault(key, mat)
        else:
            unused.append(mat.name)
    return materials, unused


def _new_surface_entry(key, mat, texture_assignment):
    """One ``NewSurfaces`` / ``AdditionalSurfaces`` entry for an Add-material
    surface *mat* with its resolved *texture_assignment*."""
    surface = {
        "SurfaceKey": key,
        "MaterialName": mat.name,
        "TemplateSource": str(mat.get("TemplateSource") or ""),
        "TextureAssignment": dict(texture_assignment),
    }
    if "SpecularStrength" in mat:
        surface["SpecularStrength"] = max(0, min(255, int(mat["SpecularStrength"])))
    return surface


def encode_custom_submesh(context, obj, model, texture_assignment, warnings, use_compression=True,
                          specular_strength=None, texture_assignments=None, infos=None):
    """Encode one ``SluggiesCustomSubmesh`` object as a ``CustomSubmeshes``
    entry: triangulate, map world
    positions into host-bone space, range-check and quantize, and encode
    per-loop normals, colors and UVs for the template's attribute set.
    Raises ValueError with the object named.

    *texture_assignments* maps each material name of the object's Add-material
    surfaces to its resolved TextureAssignment; the faces are then routed per
    material and those surfaces become ``AdditionalSurfaces``."""
    host_bone_id = _detect_uniform_vertex_bone_id(obj)
    if host_bone_id is None:
        raise ValueError(
            f"{obj.name}: every vertex must belong to exactly one bone_<id> vertex group "
            "(the host bone), all the same bone"
        )
    template_source = str(obj.get("TemplateSource") or "")
    try:
        plan = CustomSubmeshExport.attribute_plan(model, template_source)
    except ValueError as exc:
        raise ValueError(f"{obj.name}: {exc}") from exc
    bone_hierarchy = model.get("BoneHierarchyEdited") or model.get("BoneHierarchy") or []
    geometry = _custom_submesh_bone_local_geometry(
        context, obj, bone_hierarchy, host_bone_id, warnings,
    )
    loop_normals, loop_uvs, loop_colors = _custom_submesh_loop_attributes(obj, warnings)

    additional_surfaces, face_surface_indices = [], None
    additional_materials, _unused = _custom_submesh_additional_materials(obj)
    if additional_materials:
        primary = _custom_submesh_material(obj)
        routing = RigidRebuildExport.route_custom_submesh_faces(
            obj.name, str(obj.get("CustomSubmeshId")), str(primary.get("SurfaceId")),
            geometry.triangles, [poly.material_index for poly in obj.data.polygons],
            _slot_surfaces(obj),
        )
        warnings.extend(routing.warnings)
        for key in routing.new_keys:
            mat = additional_materials[key]
            if texture_assignments is None or mat.name not in texture_assignments:
                raise ValueError(f"{obj.name}: material '{mat.name}' has no resolved texture")
            additional_surfaces.append(_new_surface_entry(key, mat, texture_assignments[mat.name]))
        face_surface_indices = RigidRebuildExport.custom_submesh_face_surface_indices(routing)
        (infos if infos is not None else warnings).append(
            RigidRebuildExport.surface_report(obj.name, routing))
    return CustomSubmeshExport.build_custom_submesh_entry(
        obj.name, str(obj.get("CustomSubmeshId")), host_bone_id, template_source, plan,
        geometry, loop_normals, loop_uvs, loop_colors, texture_assignment, use_compression,
        warnings, specular_strength, additional_surfaces, face_surface_indices, infos,
    )


def validate_custom_submeshes(custom_objects, donor_candidates, model, warnings):
    """Checks on the final scene state
    before any custom submesh is encoded. Raises ValueError on the first
    blocking problem; ignored state goes into *warnings*.

    - Empty meshes are rejected.
    - Shape keys, extra materials and vertex groups other than the host bone
      are ignored with a warning (custom submeshes only; donor heads keep
      their facial shape keys without comment).
    - Each armature's import-time bone snapshot must match the target
      ``.sluggie``, or the armature belongs to another model.
    - Host bones are re-checked with ``classify_host_bones``: a bone taken
      since the cube was created, or claimed twice, is rejected.
    """
    hosts = []
    for obj in custom_objects:
        if obj.mode == 'EDIT':
            obj.update_from_editmode()
        mesh = obj.data
        if not mesh.vertices or not mesh.polygons:
            raise ValueError(f"{obj.name}: custom submesh has no faces")
        host_bone_id = _detect_uniform_vertex_bone_id(obj)
        if host_bone_id is None:
            raise ValueError(
                f"{obj.name}: every vertex must belong to exactly one bone_<id> vertex group "
                "(the host bone), all the same bone"
            )
        hosts.append(HostBones.CustomSubmeshHost(obj.name, host_bone_id))
        material = _custom_submesh_material(obj)
        shape_keys = mesh.shape_keys.key_blocks if mesh.shape_keys else []
        own_surfaces, unused_surfaces = _custom_submesh_additional_materials(obj)
        own_names = {mat.name for mat in own_surfaces.values()}
        warnings.extend(CustomSubmeshExport.ignored_state_warnings(
            obj.name,
            host_bone_id,
            [key.name for key in shape_keys],
            [slot.material.name for slot in obj.material_slots
             if slot.material is not None and slot.material.name not in own_names],
            material.name if material is not None else "",
            [group.name for group in obj.vertex_groups],
        ))
        for name in unused_surfaces:
            warnings.append(f"{obj.name}: material '{name}' is used by no face and is not exported.")

    sluggie_records = HostBones.bone_records_from_hierarchy(
        model.get("BoneHierarchy") or [], model.get("SkinData")
    )
    armatures = {}
    for obj in custom_objects:
        arm_obj = _custom_submesh_armature(obj)
        if arm_obj is not None:
            armatures.setdefault(arm_obj.name, arm_obj)
    for arm_obj in armatures.values():
        if not HostBones.bone_metadata_is_current(arm_obj.get('SluggiesBoneMetadataVersion')):
            raise ValueError(
                f"{arm_obj.name}: armature has no current bone metadata. "
                f"{HostBones.RE_IMPORT_MESSAGE}."
            )
        mismatches = HostBones.bone_metadata_mismatches(
            _armature_donor_bone_records(arm_obj), sluggie_records
        )
        if mismatches:
            shown = "; ".join(mismatches[:5])
            more = f" (and {len(mismatches) - 5} more)" if len(mismatches) > 5 else ""
            raise ValueError(
                f"{arm_obj.name}: armature does not match the target .sluggie "
                f"({shown}{more}). It was imported from a different model; export to "
                "the .sluggie it came from."
            )

    host_records = HostBones.bone_records_from_hierarchy(
        model.get("BoneHierarchyEdited") or model.get("BoneHierarchy") or [],
        model.get("SkinData"),
    )
    retargets, _issues, obj_by_submesh = _candidate_rigid_retargets(donor_candidates, model)
    errors = HostBones.custom_submesh_host_errors(
        host_records, retargets, hosts,
        {index: obj.name for index, obj in obj_by_submesh.items()},
    )
    if errors:
        raise ValueError(" ".join(errors))


def _rigid_vertex_bone_ids(obj):
    """Per vertex, the bone ids of its positive-weight bone_<id> groups."""
    group_bone = {group.index: _parse_bone_group_name(group.name) for group in obj.vertex_groups}
    result = []
    for vertex in obj.data.vertices:
        ids = set()
        for g in vertex.groups:
            bone_id = group_bone.get(g.group)
            if g.weight > 0 and bone_id is not None:
                ids.add(bone_id)
        result.append(ids)
    return result


def _slot_surfaces(obj):
    """What each material slot says about its surface (RigidRebuildExport.SlotSurface)."""
    slots = []
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            slots.append(None)
            continue
        slots.append(RigidRebuildExport.SlotSurface(
            material_name=mat.name,
            surface_id=mat.get("SurfaceId") or None,
            new_surface=bool(mat.get("SluggiesNewSurface")),
            owner=mat.get("SluggiesSurfaceOwner"),
        ))
    return slots


def _loop_colors(mesh):
    """Per-loop ``color0`` colours in the donor's value space, or None."""
    attribute = mesh.color_attributes.get("color0")
    if attribute is None:
        return None
    if attribute.domain == 'CORNER':
        return [tuple(_get_loop_color(attribute.data[i])) for i in range(len(mesh.loops))]
    if attribute.domain == 'POINT':
        return [tuple(_get_loop_color(attribute.data[loop.vertex_index])) for loop in mesh.loops]
    return None


@dataclass
class _RigidRebuildPlan:
    """A rigid donor submesh that takes the rebuild path this export."""
    obj: object
    submesh_index: int
    donor: object
    host_bone_id: int
    geometry: object
    routing: object
    reasons: list
    loop_normals: list
    loop_uvs: dict
    loop_colors: object
    new_surface_materials: dict   # surface key -> material
    facial_keys: object = None    # [RigidRebuildExport.FacialObjectKeys] or None


def _rigid_rebuild_plan(context, obj, submesh_index, target_submesh, model, warnings):
    """Decide a rigid donor submesh's export path and gather what the rebuild needs. Returns None when the
    in-place / slot-preserving path applies. Raises ValueError, naming the
    object, for anything that must stop the export (slot hygiene,
    unsupported position format). A submesh with facial poses carries them
    through the rebuild as its facial shape keys (``FacialPoses``)."""
    donor = RigidRebuildExport.donor_rigid_submesh(
        submesh_index, target_submesh, RigidRebuildExport.facial_pose_submeshes(model),
    )
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    mesh = obj.data
    owner_bone_id = next(
        (int(bone["BoneId"]) for bone in model.get("BoneHierarchy") or []
         if _bone_geo_id_raw(bone) == submesh_index),
        None,
    )
    vertex_bone_ids = _rigid_vertex_bone_ids(obj)
    if owner_bone_id is not None and vertex_bone_ids and not any(vertex_bone_ids):
        # Exports before 2026-10-18 gave submesh 0's owner no influences when the
        # model also has mesh-less bones (stadiums, Miis, ...), so it imported with
        # no bone_<id> group at all. Such a mesh still sits on its owner bone.
        host_bone_id = owner_bone_id
    else:
        problem = RigidRebuildExport.vertex_bone_problem(obj.name, vertex_bone_ids)
        if problem:
            raise ValueError(problem)
        host_bone_id = _detect_uniform_vertex_bone_id(obj)
    if not mesh.polygons:
        raise ValueError(f"{obj.name}: mesh has no faces")
    bone_hierarchy = model.get("BoneHierarchyEdited") or model.get("BoneHierarchy") or []
    geometry = _custom_submesh_bone_local_geometry(context, obj, bone_hierarchy, host_bone_id, warnings)
    topology_changed = ExportMode.topology_changed(
        (poly.vertices for poly in mesh.polygons), target_submesh.get("FacesData"),
    )
    moved = RigidRebuildExport.positions_moved(
        [tuple(v.co) for v in mesh.vertices], geometry, donor.position_quantize, obj.name,
    )
    routing = RigidRebuildExport.route_faces(
        obj.name, donor, geometry.triangles,
        [poly.material_index for poly in mesh.polygons], _slot_surfaces(obj), topology_changed,
    )
    reasons = RigidRebuildExport.decide_reasons(
        topology_changed=topology_changed, moved=moved,
        host_changed=host_bone_id != owner_bone_id, surfaces_changed=routing.changed,
        out_of_range=RigidRebuildExport.outgrew_position_format(geometry, donor.position_quantize),
    )
    if not reasons:
        return None
    format_error = RigidRebuildExport.donor_position_format_error(donor, obj.name)
    if format_error:
        raise ValueError(format_error)
    shape_keys = mesh.shape_keys.key_blocks if mesh.shape_keys else []
    facial_keys = _facial_shape_key_data(obj, model, submesh_index) if donor.facial else None
    facial_warning = RigidRebuildExport.facial_warning(
        obj.name, donor, len(shape_keys), carried=facial_keys is not None,
    )
    if facial_warning:
        warnings.append(facial_warning)
    if not donor.facial and len(shape_keys) > 1:
        warnings.append(
            f"{obj.name}: its shape keys are ignored; this submesh has no facial poses in the "
            "game's data to carry them as."
        )
    warnings.extend(routing.warnings)
    all_channels = target_submesh.get("UVChannels", [])
    loop_uvs = {}
    for channel in all_channels:
        index = channel.get("UVChannelIndex", 0)
        layer = _find_uv_layer(mesh, all_channels, index)
        loop_uvs[index] = (
            [tuple(layer.data[i].uv) for i in range(len(mesh.loops))] if layer is not None else None
        )
    new_surface_materials = {}
    for slot in obj.material_slots:
        mat = slot.material
        if mat is not None and mat.get("SluggiesNewSurface") and mat.get("SurfaceId") in routing.new_keys:
            new_surface_materials.setdefault(mat["SurfaceId"], mat)
    return _RigidRebuildPlan(
        obj=obj, submesh_index=submesh_index, donor=donor, host_bone_id=host_bone_id,
        geometry=geometry, routing=routing, reasons=reasons,
        loop_normals=_per_loop_normals(mesh, range(len(mesh.loops))),
        loop_uvs=loop_uvs, loop_colors=_loop_colors(mesh),
        new_surface_materials=new_surface_materials,
        facial_keys=facial_keys,
    )


def _rigid_rebuild_entry(plan, texture_assignments, use_compression, warnings, infos):
    """Encode one plan as its RigidRebuild entry. *texture_assignments* maps a
    new-surface material name to its resolved TextureAssignment."""
    new_surfaces = []
    for key in plan.routing.new_keys:
        mat = plan.new_surface_materials[key]
        new_surfaces.append(_new_surface_entry(key, mat, texture_assignments[mat.name]))
    return RigidRebuildExport.build_rigid_rebuild_entry(
        plan.obj.name, plan.donor, plan.host_bone_id, plan.geometry, plan.loop_normals,
        plan.loop_uvs, plan.loop_colors, plan.routing, new_surfaces, plan.reasons,
        use_compression, warnings, infos, facial_keys=plan.facial_keys,
    )


def _vertex_bone_weights(obj):
    """Per vertex, ``{bone id: weight}`` of its positive-weight bone_<id> groups."""
    group_bone = {group.index: _parse_bone_group_name(group.name) for group in obj.vertex_groups}
    result = []
    for vertex in obj.data.vertices:
        weights = {}
        for g in vertex.groups:
            bone_id = group_bone.get(g.group)
            if g.weight > 0 and bone_id is not None:
                weights[bone_id] = weights.get(bone_id, 0.0) + g.weight
        result.append(weights)
    return result


@dataclass
class _SkinnedRebuildPlan:
    """The skinned body takes the rebuild path this export."""
    obj: object
    submesh_index: int
    donor: object
    geometry: object
    routing: object
    reasons: list
    normals: list                 # per exported vertex, armature space, unit
    loop_uvs: dict
    loop_colors: object
    vertex_weights: list          # per Blender vertex {bone: weight}, skinned bones only
    new_surface_materials: dict   # surface key -> material
    facial_keys: object = None    # [SkinnedRebuildExport.FacialObjectKeys] or None


def _mark_skinned_added_bones(model, vertex_weights, object_name, warnings):
    """Set ``Skinned`` on every added bone the rebuilt body weights (the
    patcher writes GEO 0xFFFF for such a bone, as for a vanilla skinning bone)
    and say so in the log."""
    used = {bone for weights in vertex_weights for bone in weights}
    skinned = []
    for entry in model.get("BoneHierarchyEdited") or []:
        if entry.get("UserAdded") and int(entry["BoneId"]) in used:
            entry["Skinned"] = True
            skinned.append(int(entry["BoneId"]))
    if skinned:
        warnings.append(
            f"{object_name}: skinned to added bone(s) {', '.join(f'bone_{b}' for b in sorted(skinned))}."
        )


def _skinned_rebuild_plan(context, obj, submesh_index, target_submesh, model, warnings):
    """Decide the skinned body's export path and gather what the rebuild
    needs. Returns None when the slot-preserving paths apply.
    Raises ValueError, naming the object, for anything that must stop the
    export (slot hygiene, unweighted vertices, unsupported position format)."""
    donor = SkinnedRebuildExport.donor_skinned_submesh(
        submesh_index, target_submesh, model.get("SkinData"),
        RigidRebuildExport.facial_pose_submeshes(model),
    )
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    mesh = obj.data
    if not mesh.polygons:
        raise ValueError(f"{obj.name}: mesh has no faces")
    geometry = _armature_local_geometry(context, obj, warnings)
    topology_changed = ExportMode.topology_changed(
        (poly.vertices for poly in mesh.polygons), target_submesh.get("FacesData"),
    )
    moved = RigidRebuildExport.positions_moved(
        [tuple(v.co) for v in mesh.vertices], geometry, donor.position_quantize, obj.name,
    )
    routing = RigidRebuildExport.route_faces(
        obj.name, donor, geometry.triangles,
        [poly.material_index for poly in mesh.polygons], _slot_surfaces(obj), topology_changed,
    )
    surfaces_changed = SkinnedRebuildExport.surfaces_need_rebuild(
        routing, donor, _effective_type7_modes(target_submesh.get("DisplayStates", [])),
    )
    # Vertex groups added, removed or reassigned: the body is rebuilt with
    # the new weights rather than laid out around the donor's entries.
    weights_changed = skin_membership_changed([obj], {"SluggiesModel": model})
    reasons = SkinnedRebuildExport.decide_reasons(
        topology_changed=topology_changed, moved=moved, surfaces_changed=surfaces_changed,
        weights_changed=weights_changed,
        out_of_range=RigidRebuildExport.outgrew_position_format(geometry, donor.position_quantize),
    )
    if not reasons:
        return None
    format_error = SkinnedRebuildExport.donor_position_format_error(donor, obj.name)
    if format_error:
        raise ValueError(format_error)
    warnings.extend(routing.warnings)

    bone_hierarchy = model.get("BoneHierarchyEdited") or model.get("BoneHierarchy") or []
    parent_of = {int(b["BoneId"]): b.get("ParentBoneId") for b in bone_hierarchy if "BoneId" in b}
    allowed = SkinnedRebuildExport.allowed_skin_bones(bone_hierarchy)
    vertex_weights = _vertex_bone_weights(obj)
    remap = SkinnedRebuildExport.remap_influences(vertex_weights, allowed, parent_of)
    warnings.extend(SkinnedRebuildExport.remap_report(remap, obj.name))
    _mark_skinned_added_bones(model, remap.weights, obj.name, warnings)
    newly_skinned = SkinnedRebuildExport.newly_skinned_bones(
        remap.weights, donor, SkinnedRebuildExport.added_bone_ids(model.get("BoneHierarchyEdited")),
    )
    if newly_skinned:
        warnings.append(
            f"{obj.name}: skinned to original bone(s) the original body doesn't skin to: "
            f"{SkinnedRebuildExport.bone_list(newly_skinned)}."
        )
    used = {v for tri in geometry.triangles for v in tri.vertices}
    unweighted_error = SkinnedRebuildExport.unweighted_error(
        obj.name, [v for v in remap.unweighted if v in used], vertex_weights,
    )
    if unweighted_error:
        raise ValueError(unweighted_error)

    shape_keys = mesh.shape_keys.key_blocks if mesh.shape_keys else []
    facial_keys = _facial_shape_key_data(obj, model, submesh_index) if donor.facial else None
    facial_warning = SkinnedRebuildExport.facial_warning(
        obj.name, donor, len(shape_keys), carried=facial_keys is not None,
    )
    if facial_warning:
        warnings.append(facial_warning)

    loop_normals = _per_loop_normals(mesh, range(len(mesh.loops)))
    normals = CustomSubmeshExport.transform_normals(
        SkinnedRebuildExport.vertex_normals(geometry, loop_normals, len(mesh.vertices)), geometry.to_bone,
    )
    all_channels = target_submesh.get("UVChannels", [])
    loop_uvs = {}
    for channel in all_channels:
        index = channel.get("UVChannelIndex", 0)
        layer = _find_uv_layer(mesh, all_channels, index)
        loop_uvs[index] = (
            [tuple(layer.data[i].uv) for i in range(len(mesh.loops))] if layer is not None else None
        )
    new_surface_materials = {}
    for slot in obj.material_slots:
        mat = slot.material
        if mat is not None and mat.get("SluggiesNewSurface") and mat.get("SurfaceId") in routing.new_keys:
            new_surface_materials.setdefault(mat["SurfaceId"], mat)
    return _SkinnedRebuildPlan(
        obj=obj, submesh_index=submesh_index, donor=donor, geometry=geometry, routing=routing,
        reasons=reasons, normals=normals, loop_uvs=loop_uvs, loop_colors=_loop_colors(mesh),
        vertex_weights=remap.weights, new_surface_materials=new_surface_materials,
        facial_keys=facial_keys,
    )


def _skinned_rebuild_entry(plan, texture_assignments, use_compression, warnings, infos):
    """Encode one body plan as its SkinnedRebuild entry."""
    new_surfaces = []
    for key in plan.routing.new_keys:
        mat = plan.new_surface_materials[key]
        new_surfaces.append(_new_surface_entry(key, mat, texture_assignments[mat.name]))
    entry = SkinnedRebuildExport.build_skinned_rebuild_entry(
        plan.obj.name, plan.donor, plan.geometry, plan.normals, plan.loop_uvs, plan.loop_colors,
        plan.routing, plan.vertex_weights, new_surfaces, plan.reasons, use_compression, warnings, infos,
    )
    if plan.facial_keys is not None:
        entry["FacialPoses"] = SkinnedRebuildExport.encode_facial_poses(
            plan.obj.name, plan.facial_keys, plan.geometry,
            entry.get("VertexBufferQuantizeInfo", plan.donor.position_quantize),
            use_compression, warnings, infos,
        )
    return entry


def _partial_move_error(obj, display_states, surf_mat):
    """Submesh 0 (skinned) keeps the complete-move rule: a donor surface whose
    faces are split between materials is refused with the rigid-only hint."""
    original = []
    for index, state in enumerate(display_states):
        count = state.get("FaceCount")
        if count is None:
            return None
        original += [index] * int(count)
    polygons = obj.data.polygons
    if len(original) != len(polygons):
        return None
    sid_to_index = {state.get("SurfaceId"): i for i, state in enumerate(display_states) if state.get("SurfaceId")}
    slot_to_index = {}
    for slot_index, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is not None and mat.get("SurfaceId") in sid_to_index:
            slot_to_index[slot_index] = sid_to_index[mat["SurfaceId"]]
    targets = {}
    for poly, source in zip(polygons, original):
        targets.setdefault(source, set()).add(slot_to_index.get(poly.material_index, source))
    for source, moved_to in targets.items():
        if len(moved_to) > 1:
            name = display_states[source].get("SurfaceId") or f"ds{source}"
            return (
                f"{obj.name}: faces of surface '{name}' were moved only partly to other "
                f"materials. {NEW_SURFACE_BODY_MESSAGE}."
            )
    return None
