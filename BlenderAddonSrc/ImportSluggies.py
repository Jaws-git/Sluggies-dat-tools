"""Import of a ``.sluggie`` (File > Import): the operator ``SLUGGIES_OT_import`` (decode, then build; a
failed import removes what it added), the menu entries and ``register`` (also for the ``.anm`` import).

The work lives in the modules imported below, in import order: ``ImportDecode`` (bpy-free field
decoders and edited / rebuild views), ``ImportBones``, ``SurfaceMaterials``, ``ImportMesh`` and
``ImportAnm``. None of them imports this module; other add-on modules import a helper from the module
that defines it.
"""

import bpy
import functools
import os
import struct
import traceback
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from . import CustomSubmeshImport
from . import StadiumCollisionExport
from . import FieldCodec
from . import SluggiePath

from .ImportDecode import (
    _edit_object_influences,
    _edited_skin_influences,
    _edited_submesh_view,
    _rigid_import_placement,
    _skinned_rebuild_influences,
    decode_face_texture_indices,
    decode_faces,
    decode_vertex_buffer,
)
from .ImportBones import (
    _apply_nonskinned_transform,
    _compute_bone_absolute_matrices,
    _restore_root_bone_scale,
    _submesh_owner_bones,
    add_imported_added_bones,
    add_rest_pose_action,
    assign_bone_collections,
    build_armature,
)
from .SurfaceMaterials import (
    _new_surface_materials,
    _resolve_texture_image_path,
)
from .ImportMesh import (
    _apply_import_rotation,
    _assigned_surface_texture_files,
    _texture_file_map,
    _texture_file_map_from_sibling_sluggie,
    add_facial_shape_keys,
    add_vertex_groups,
    build_collision_object,
    build_custom_submesh,
    build_mesh,
)
from .ImportAnm import (
    SLUGGIES_OT_import_anm,
    menu_func_import_anm,
)


# What an import creates, in the order a failed one is taken apart again (users before the data they use).
_IMPORTED_KINDS = ('objects', 'collections', 'meshes', 'armatures', 'actions', 'materials', 'images')


def _datablock_names():
    return {kind: {block.name for block in getattr(bpy.data, kind)} for kind in _IMPORTED_KINDS}


def _remove_datablocks_added_since(before):
    """Remove every datablock of ``_IMPORTED_KINDS`` that is not named in *before*; returns how many."""
    removed = 0
    for kind in _IMPORTED_KINDS:
        collection = getattr(bpy.data, kind)
        for block in [block for block in collection if block.name not in before[kind]]:
            try:
                collection.remove(block)
                removed += 1
            except (RuntimeError, ReferenceError):
                pass
    return removed


def _undoes_failed_import(execute):
    """For the import operator's ``execute``: a damaged or hand-edited file fails somewhere in the middle of
    the build (a short buffer, a missing key). Instead of a traceback over a half-built scene (the armature,
    some meshes, Blender left in armature Edit Mode), the error is reported, Object Mode restored and
    everything the import had created removed again."""
    @functools.wraps(execute)
    def guarded(self, context):
        if context.mode != 'OBJECT':
            self.report({"ERROR"}, "Switch to Object Mode before importing a .sluggie.")
            return {"CANCELLED"}
        before = _datablock_names()
        try:
            return execute(self, context)
        except Exception as exc:  # noqa: BLE001 - whatever a damaged file raises, the scene is put back
            traceback.print_exc()
            try:
                if bpy.context.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:  # noqa: BLE001
                pass
            removed = _remove_datablocks_added_since(before)
            self.report({"ERROR"},
                f"Import failed ({type(exc).__name__}: {exc}): {os.path.basename(self.filepath)} is damaged "
                f"or not a model file. The {removed} object(s) and data blocks it had built were removed "
                "again; the full traceback is in the system console.")
            return {"CANCELLED"}
    return guarded


class SLUGGIES_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "sluggies.import_json"
    bl_label = "Import Sluggers intermediate"
    bl_description = "Import a Sluggers intermediate JSON file"
    bl_options = {"UNDO"}

    filename_ext = ".sluggie"
    filter_glob: StringProperty(default="*.sluggie", options={"HIDDEN"}) # type: ignore[valid-type]
    reorient_bones: BoolProperty(  # type: ignore[valid-type]
        name="Reorient Bones",
        description=("Aim each bone at its child, or the average of several children "
                     "(leaf bones continue their parent) "
                     "for a readable skeleton. Off: bones keep the game's own rest "
                     "orientation. Only changes how the skeleton looks in Blender"),
        default=True,
    )

    @_undoes_failed_import
    def execute(self, context):
        # Per import only: a PNG that was missing (or has since been deleted) is looked up again.
        _resolve_texture_image_path.cache_clear()
        data = FieldCodec.load_sluggie(self.filepath)

        model = data["SluggiesModel"]
        model_number = model["ChunkNumber"]
        model_offset_hex = model["ModelOffset"]
        submeshes = model.get("Submeshes", [])
        texture_file_map = _texture_file_map(model.get("TextureDescriptors", []))
        sluggie_dir = os.path.dirname(self.filepath)
        if not texture_file_map:
            texture_file_map = _texture_file_map_from_sibling_sluggie(self.filepath, model)
        surface_texture_files = _assigned_surface_texture_files(model, texture_file_map)
        skin_edit = _edited_skin_influences(model)

        collection = context.collection

        # Build armature first so mesh objects can reference it immediately
        bone_list = model.get("BoneHierarchy")
        arm_obj = None
        abs_bone_mats = {}
        if bone_list:
            # Name the armature after the imported .sluggie file (without its
            # extension) so it is easy to identify in the outliner. The export
            # workflow does not rely on the armature name — meshes are matched
            # by their VertexBufferOffset custom property and skin weights are
            # read from bone_<id> vertex groups — so this is cosmetic.
            base_name = os.path.splitext(os.path.basename(self.filepath))[0]
            arm_obj = build_armature(base_name, bone_list, collection, model.get("SkinData"),
                                     reorient=self.reorient_bones)
            arm_obj['SluggiesBonesReoriented'] = bool(self.reorient_bones)
            _restore_root_bone_scale(arm_obj, model.get("RootBoneScaleEdited"))
            abs_bone_mats = _compute_bone_absolute_matrices(bone_list)
            # So the exporter and Add submesh can find this model's file and
            # tex/ directory without asking the user (SluggiePath.resolve).
            SluggiePath.remember(arm_obj, self.filepath)
            # The model directory; a stadium (TemplateSources.STADIUM_CHUNKS)
            # takes custom submeshes only on root bones.
            arm_obj['SluggiesChunkNumber'] = int(model_number)
            # Bones added with Add Bone (BoneHierarchyEdited), so their custom
            # submeshes and body weights have them again.
            from . import TemplateSources
            add_imported_added_bones(
                arm_obj, CustomSubmeshImport.added_bone_entries(model),
                abs_bone_mats if TemplateSources.is_stadium_chunk(model_number) else None)
            skipped_bones = CustomSubmeshImport.skipped_added_bone_ids(model)
            if skipped_bones:
                self.report(
                    {"WARNING"},
                    f"Added bone(s) {', '.join(str(b) for b in skipped_bones)} not imported: the parent "
                    "bone is not in the file. An export from this scene leaves them out.")
            assign_bone_collections(arm_obj)
            add_rest_pose_action(arm_obj)

        imported = 0
        for i, submesh in enumerate(submeshes):
            vb = submesh.get("VertexBuffer")
            if not vb:
                continue
            positions, normals = decode_vertex_buffer(vb)
            faces = decode_faces(submesh)
            uv_channels = submesh.get("UVChannels", [])
            color_channels = submesh.get("ColorChannels", [])
            face_texture_indices = decode_face_texture_indices(submesh, len(faces))
            mesh_name = f"{model_number}_{model_offset_hex}_submesh{i}"
            obj = build_mesh(mesh_name, positions, normals, faces, vb, collection,
                             uv_channels, color_channels,
                             face_texture_indices=face_texture_indices, sluggie_dir=sluggie_dir,
                             submesh_meta=submesh, texture_file_map=texture_file_map,
                             surface_texture_files=surface_texture_files)
            # The donor object stays on its donor bone as the "before" view;
            # the `_edit` object shows every edit, a bone move included, on
            # the effective owner (GeoIdEdited first).
            donor_owner, effective_owner = _submesh_owner_bones(bone_list or [], i)
            rebuilt = bool(submesh.get("RigidRebuild") or submesh.get("SkinnedRebuild"))
            skinned_influences = _skinned_rebuild_influences(submesh)
            original_owner, make_edit = _rigid_import_placement(submesh, donor_owner, effective_owner)
            if skinned_influences is None and not rebuilt and effective_owner == donor_owner:
                # Skin weights edited in place or by SkinDataEdited: the
                # `_edit` object carries them (the donor object keeps the
                # donor weights as the "before" view).
                skinned_influences = _edit_object_influences(i, bone_list, skin_edit)
                make_edit = make_edit or skinned_influences is not None
            if arm_obj is not None:
                add_vertex_groups(obj, i, bone_list, arm_obj)
                obj.parent = arm_obj
            else:
                _apply_import_rotation(obj)
            if bone_list:
                _apply_nonskinned_transform(obj, i, bone_list, abs_bone_mats, original_owner)
            # Facial pose edits belong to the `_edit` object when there is one (the donor object is the
            # "before" view); without one the donor object is the only place for them.
            ev = _edited_submesh_view(submesh) if make_edit else None
            has_edit_object = bool(ev and ev.get("VertexBuffer"))
            add_facial_shape_keys(
                obj, i, model.get("FacialPoseData"),
                None if has_edit_object else model.get("FacialPoseDataEdited")
            )
            imported += 1

            # Import the edited version of this submesh when one exists
            if make_edit:
                ev_vb = ev.get("VertexBuffer")
                if ev_vb:
                    edit_positions, edit_normals = decode_vertex_buffer(ev_vb)
                    edit_faces = decode_faces(ev)
                    edit_uv_channels = ev.get("UVChannels", [])
                    edit_color_channels = ev.get("ColorChannels", [])
                    edit_fti = decode_face_texture_indices(ev, len(edit_faces))

                    # Collect materials from the original mesh for reuse.
                    # New format: keyed by SurfaceId. Legacy format: keyed by tex_idx (int).
                    orig_materials = {}
                    for slot in obj.material_slots:
                        mat = slot.material
                        if mat is None:
                            continue
                        sid = mat.get("SurfaceId")
                        if sid:
                            orig_materials[sid] = mat
                        else:
                            try:
                                orig_materials[int(mat.name.rsplit('_mat', 1)[1])] = mat
                            except (IndexError, ValueError):
                                pass

                    face_surface_ids = ev.get("_FaceSurfaceIds")
                    if rebuilt:
                        orig_materials.update(_new_surface_materials(
                            mesh_name, submesh, i, model,
                            sluggie_dir, texture_file_map,
                        ))
                    edit_obj = build_mesh(
                        f"{mesh_name}_edit",
                        edit_positions, edit_normals, edit_faces,
                        vb, collection,
                        edit_uv_channels, edit_color_channels,
                        face_texture_indices=edit_fti,
                        sluggie_dir=None,
                        submesh_meta=ev,
                        prebuilt_materials=orig_materials or None,
                        texture_file_map=texture_file_map,
                        face_surface_ids=face_surface_ids,
                    )
                    if arm_obj is not None:
                        add_vertex_groups(
                            edit_obj, i, bone_list, arm_obj,
                            owner_bone_id=(
                                effective_owner
                                if skinned_influences is None and (rebuilt or effective_owner != donor_owner)
                                else None
                            ),
                            influences=skinned_influences,
                        )
                        edit_obj.parent = arm_obj
                    else:
                        _apply_import_rotation(edit_obj)
                    if bone_list:
                        _apply_nonskinned_transform(edit_obj, i, bone_list, abs_bone_mats, effective_owner)
                    rebuild = submesh.get("SkinnedRebuild") or submesh.get("RigidRebuild")
                    add_facial_shape_keys(
                        edit_obj, i, model.get("FacialPoseData"),
                        model.get("FacialPoseDataEdited"),
                        rebuild_poses=(rebuild.get("FacialPoses") or []) if rebuild else None,
                        rebuild_record_stride=12 if submesh.get("SkinnedRebuild") else 6,
                    )
                    imported += 1

        # Custom submeshes, placed with the bind matrices the exporter reads
        # them back with (BoneHierarchyEdited includes the added bones).
        custom_entries = model.get("CustomSubmeshes") or []
        if custom_entries and arm_obj is None:
            self.report({"WARNING"}, f"{len(custom_entries)} custom submesh(es) not imported: the model has no bones")
        elif custom_entries:
            bind_matrices = _compute_bone_absolute_matrices(model.get("BoneHierarchyEdited") or bone_list)
            for entry in custom_entries:
                try:
                    cs = CustomSubmeshImport.decode_custom_submesh(entry)
                except (KeyError, TypeError, ValueError, struct.error) as exc:
                    self.report({"WARNING"}, f"Custom submesh not imported: {exc}")
                    continue
                if cs.host_bone_id not in bind_matrices:
                    self.report({"WARNING"}, f"{cs.name}: host bone {cs.host_bone_id} does not exist; not imported")
                    continue
                for problem in cs.problems:
                    self.report({"WARNING"}, problem)
                build_custom_submesh(cs, model, arm_obj, bind_matrices, collection, sluggie_dir, texture_file_map)
                imported += 1

        collision_entries = StadiumCollisionExport.import_entries(model)
        if collision_entries and arm_obj is None:
            self.report({"WARNING"}, f"{len(collision_entries)} collision object(s) not imported: the model has no bones")
        elif collision_entries:
            original = [e for e in collision_entries if e.get('Region') is not None]
            col_collection = collection
            if original:
                # The stadium's own collision, in a collection of its own (hide it with one click).
                col_collection = bpy.data.collections.new(f"{arm_obj.name} collision")
                collection.children.link(col_collection)
            for entry in collision_entries:
                try:
                    build_collision_object(
                        entry, arm_obj, col_collection if entry.get('Region') is not None else collection)
                except (KeyError, ValueError, TypeError) as exc:
                    self.report({"WARNING"}, f"Collision object not imported: {exc}")

        context.view_layer.update()
        self.report({"INFO"}, f"Imported {imported} submesh(es) from {self.filepath}")
        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(SLUGGIES_OT_import.bl_idname, text="Sluggers intermediate (.sluggie)")


def register():
    bpy.utils.register_class(SLUGGIES_OT_import)
    bpy.utils.register_class(SLUGGIES_OT_import_anm)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_anm)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_anm)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(SLUGGIES_OT_import_anm)
    bpy.utils.unregister_class(SLUGGIES_OT_import)
