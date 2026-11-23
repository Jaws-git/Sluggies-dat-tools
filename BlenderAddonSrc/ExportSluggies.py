"""Export of a ``.sluggie`` (File > Export, and the tools' Export buttons): the operator
``SLUGGIES_OT_export`` with ``execute`` split into phases, the meshes of a model (``model_meshes``), and
``register``.

The encoders live in the modules imported below, in import order: ``ExportInplaceMesh`` (field codec,
vertex buffer, UVs, normals, colours, ``ExportCancelled``), ``ExportBones``, ``ExportFacial``,
``ExportSkin``, ``ExportTextures``, ``ExportSurfaces`` and ``ExportRebuildGlue``. None of them imports this
module; other add-on modules import a helper from the module that defines it.
"""

import bpy
import copy
import functools
import os
import shutil
import traceback
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from . import ExportMode
from . import FieldCodec
from . import RigidRebuildExport
from . import SkinnedRebuildExport
from . import TemplateSources
from . import TextureConvert
from . import TextureSlotsCore
from . import ModelCheckCore
from . import SluggiePath
from . import StadiumCollisionExport

from .ExportInplaceMesh import (
    ExportCancelled,
    REQUIRED_PROPS,
    _apply_inplace_normal_edits,
    _to_bytes,
    _uv_layer_name,
    detect_length_mismatches,
    encode_color_edits,
    encode_mesh_hammerspace,
    encode_uv_channel_edited,
    encode_vertex_buffer_edited,
    validate_against_json,
)
from .ExportBones import (
    added_bone_name_error,
    armature_has_new_bones,
    encode_bone_hierarchy_edited,
    encode_root_bone_scale_edited,
    encode_unskinned_bone_reassignments,
    untracked_keys_warnings,
    validate_bone_hierarchy_edited_export,
)
from .ExportFacial import (
    _drop_facial_edits_for_submesh,
    update_facial_pose_edits,
)
from .ExportSkin import (
    _purge_skn_edited,
    _skin_data_edited_is_empty,
    encode_skin_hammerspace,
    encode_skin_weights_inplace,
    skinned_donor_objects,
)
from .ExportTextures import (
    _custom_submesh_material,
    _custom_submesh_template_texture_index,
    _empty_image_node_errors,
    _find_new_materials,
    _merge_texture_additions,
    _resolve_custom_submesh_texture_changes,
    _resolve_export_texture_context,
    _resolve_material_texture_changes,
)
from .ExportSurfaces import (
    _encode_face_surface_assignment,
)
from .ExportRebuildGlue import (
    _BIND_MATRICES,
    _custom_submesh_additional_materials,
    _partial_move_error,
    _rigid_rebuild_entry,
    _rigid_rebuild_plan,
    _skinned_rebuild_entry,
    _skinned_rebuild_plan,
    encode_custom_submesh,
    encode_stadium_collision,
    validate_custom_submeshes,
)


# Runtime testing found that cross-mode reassignment is unsafe. In particular,
# changing a hand-role batch from RhSp/LhSp to Spec leaves its primitive list in
# the hand display-state slot, where game-state visibility still suppresses it.
# Only identical effective Type-7 modes are accepted. Flip this flag back to
# False to disable reassignment exports entirely.
ENABLE_MATERIAL_REASSIGNMENT_EXPORT = True


def model_meshes(context, arm_obj):
    """(visible, hidden) meshes of the model *arm_obj*: its Sluggies, custom
    submesh and collision meshes below it or deformed by it."""
    shown, hidden = [], []
    for obj in context.view_layer.objects:
        if obj.type != 'MESH':
            continue
        owner = obj.parent
        while owner is not None and owner.type != 'ARMATURE':
            owner = owner.parent
        if owner is not arm_obj and not any(
                m.type == 'ARMATURE' and m.object is arm_obj for m in obj.modifiers):
            continue
        collision = getattr(obj, 'sluggies_collision', None)
        if not (obj.get('SluggiesCustomSubmesh') or (collision is not None and collision.enabled)
                or all(prop in obj for prop in REQUIRED_PROPS)):
            continue
        (shown if obj.visible_get() else hidden).append(obj)
    return shown, hidden


def export_selection(context):
    """(the objects the export treats as selected, hidden meshes left out):
    the selection, or for a selection of one model's armature and no mesh,
    that armature with all its visible meshes."""
    from . import ModelCheck
    selected = list(context.selected_objects)
    armatures = [obj for obj in selected if ModelCheck.is_sluggies_armature(obj)]
    if len(armatures) != 1 or any(obj.type == 'MESH' for obj in selected):
        return selected, []
    shown, hidden = model_meshes(context, armatures[0])
    return selected + shown, hidden


class _ExportRun:
    """What the phases of one export share (``SLUGGIES_OT_export.execute`` runs them in order).

    ``data`` is the parsed source ``.sluggie``. The phases before the mesh encode write into it before the
    export mode is known (bone hierarchy, facial edits of excluded meshes); that is safe only because nothing
    is written to disk when a phase cancels. During the mesh encode ``data``, ``warnings``, ``errors`` and
    ``infos`` are those of the current pass: the in-place attempt works on copies, and a pass that has to be
    repeated in Hammerspace mode starts again from the state before it."""

    def __init__(self):
        # Source and target (_load_source).
        self.selected = []
        self.hidden_meshes = []
        self.arm_obj = None
        self.source_path = ""
        self.save_as = False
        self.data = None
        self.use_compression = True
        self.submeshes = []
        # The selection by role (_collect_objects).
        self.candidates = []
        self.custom_submesh_candidates = []
        self.collision_objects = []
        self.original_colliders = []
        self.invisible_objects = []
        self.size_line = ""
        # Notes reported after the encode: ordinary warnings; expected, harmless notes (faces per surface,
        # mirrored UV 1); and severe but non-fatal conditions (part of the user's edit was discarded), which
        # are reported at ERROR level so they are not lost among the warnings.
        self.warnings = []
        self.infos = []
        self.errors = []
        # (object, submesh) and (object, submesh index) per selected original mesh (_match_submeshes).
        self.object_submeshes = []
        self.object_submesh_indices = []
        # Textures (_resolve_material_textures).
        self.local_texture_descriptors = []
        self.texture_descriptors = []
        self.texture_dir = None
        self.owns_texture_section = False
        self.paired_main_model = "paired main model"
        self.additions = []
        self.desired_assignments = {}
        self.changed_materials = []
        # tex/ file name -> source PNG linked from outside tex/, copied in once nothing can cancel the export.
        self.planned_texture_copies = {}
        # Custom submeshes (_encode_custom_submeshes).
        self.custom_submesh_entries = []
        self.custom_additions = []
        # Whole-submesh rebuilds by submesh index (_plan_rebuilds).
        self.rigid_plans = {}
        self.skinned_plans = {}
        self.new_surface_additions = []
        self.new_surface_assignments = {}
        self.excluded_names = []
        # Export mode and the mesh encode (_decide_mode, _encode_meshes).
        self.hammerspace_reasons = []
        self.use_hammerspace = False
        self.pass_reasons = []
        self.written = 0
        self.root_scale = None
        self.texture_copies = []


def _reports_unexpected_errors(execute):
    """For the export operator's ``execute``: an error no step expected becomes an error report Blender can
    show (and CANCELLED) instead of a bare traceback; the traceback goes to the system console.
    ``self._wrote_file`` (set by ``execute`` after the write) tells the user whether the file changed."""
    @functools.wraps(execute)
    def guarded(self, context):
        self._wrote_file = False
        try:
            return execute(self, context)
        except Exception as exc:  # noqa: BLE001 - the last line of defence of the whole export
            traceback.print_exc()
            state = ("The .sluggie file was already written; check it before patching."
                     if self._wrote_file else "Nothing was written.")
            self.report({"ERROR"},
                f"Export failed with an unexpected error ({type(exc).__name__}: {exc}). {state} "
                "The full traceback is in the system console (Window > Toggle System Console).")
            return {"CANCELLED"}
    return guarded


class SLUGGIES_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "sluggies.export_json"
    bl_label = "Export Sluggers intermediate"
    bl_description = (
        "Write the edited model into a .sluggie file: the one it was imported "
        "from, or a new file name (the original is left untouched)"
    )
    bl_options = {"UNDO"}

    filename_ext = ".sluggie"
    filter_glob: StringProperty(default="*.sluggie", options={"HIDDEN"})  # type: ignore[valid-type]
    # First, so the file browser lists it at the top.
    exclude_unselected: BoolProperty(  # type: ignore[valid-type]
        name="Exclude all unselected meshes",
        description=(
            "The patched model keeps only the meshes this export writes: every original mesh "
            "that is not selected is left out of the game (not drawn), and its earlier edits "
            "are dropped. The main body always stays; stadiums keep every mesh. With only the "
            "armature selected, all its visible meshes count as selected. Off: unselected "
            "meshes keep their original or earlier exported state"
        ),
        default=True,
    )
    use_custom_normals: BoolProperty(  # type: ignore[valid-type]
        name="Overwrite Normals",
        description=(
            "Overwrite both interleaved vertex-buffer normals (CompCount>=6) "
            "and standalone NormalBuffer arrays with Blender's current normals. "
            "Leave off to keep the original normal values unchanged."
        ),
        default=False,
    )
    reimport_textures: BoolProperty(  # type: ignore[valid-type]
        name="Reimport textures from tex folder",
        description=(
            "When patching, replace the model's original textures in "
            "dt_na.dat with the edited PNGs from the model's tex/ folder. "
            "A PNG whose size changed makes the export use Hammerspace, "
            "which rebuilds the TEX section. New textures (new PNGs on "
            "materials, custom submeshes and new materials) are always "
            "added, with or without this option."
        ),
        default=False,
    )

    def invoke(self, context, event):
        # The file browser starts on the armature's source .sluggie: export
        # back into it, or type a new name to save a variant.
        from . import ModelCheck
        arm_obj = ModelCheck.selection_armature(context.selected_objects)
        source_path = SluggiePath.resolve(arm_obj, hint=self.filepath)
        if not source_path and arm_obj is not None and not self.filepath:
            # Start the browser on the recorded name at least.
            self.filepath = os.path.basename(SluggiePath.stored(arm_obj))
        if source_path and not self.filepath:
            self.filepath = source_path
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}
        return super().invoke(context, event)

    @_reports_unexpected_errors
    def execute(self, context):
        if not getattr(self, "filepath", "").strip():
            self.report(
                {"ERROR"},
                "No target .sluggie file was selected. Pick the .sluggie to "
                "update or type a new file name, and export again.",
            )
            return {"CANCELLED"}

        # The phases, in order. Each reads and fills `run`; one that cannot
        # go on raises ExportCancelled. Nothing is written to disk before
        # _copy_textures, so a cancel before it leaves the files as they were.
        run = _ExportRun()
        _BIND_MATRICES[:] = [None, None]
        try:
            self._load_source(context, run)
            self._collect_objects(context, run)
            self._match_submeshes(run)
            self._resolve_material_textures(run)
            self._encode_bones(context, run)
            self._encode_custom_submeshes(context, run)
            self._plan_rebuilds(context, run)
            self._exclude_unselected(run)
            self._decide_mode(context, run)
            self._encode_meshes(context, run)
            self._report_mode(run)
            self._encode_collision(context, run)
            self._report_notes(run)
            self._finish_model_fields(run)
            self._copy_textures(run)
            self._write_file(context, run)
        except ExportCancelled as cancelled:
            for message in cancelled.messages:
                self.report({"ERROR"}, message)
            return {"CANCELLED"}
        return {"FINISHED"}

    def _load_source(self, context, run):
        """Find and read the armature's source ``.sluggie`` and check the target file."""
        # Edit Mode keeps the mesh in BMesh, so mesh.uv_layers etc. read as
        # empty (IndexError on a custom submesh's UVs, Blender 5.2,
        # 2026-09-29). Refuse instead of flushing half the data.
        if context.mode.startswith('EDIT'):
            raise ExportCancelled(
                "Export is not possible in Edit Mode. Switch to Object Mode "
                "(Tab) and export again.")

        # The model is read from the armature's source .sluggie and written
        # to the target (self.filepath), which may be a new file in any
        # folder. Every path-relative lookup below (tex/, the Low model's
        # partner) uses the target's folder. SluggiePath.resolve finds a
        # moved source again (and records the hit); an armature without a
        # source (older imports) or with a lost one reads the target, which
        # the export below then records as its source.
        from . import ModelCheck
        # Only an armature selected: all its visible meshes count as selected.
        selected, hidden_meshes = export_selection(context)
        arm_obj = ModelCheck.selection_armature(selected)
        source_path = SluggiePath.resolve(arm_obj, hint=self.filepath)
        stored_path = SluggiePath.stored(arm_obj)
        if not source_path:
            if stored_path:
                self.report({"WARNING"},
                    f"The armature's source file {stored_path} is missing; reading the target instead.")
            source_path = self.filepath
        if not os.path.isfile(source_path):
            raise ExportCancelled(
                f"{os.path.basename(self.filepath)} does not exist yet and "
                + (f"the armature's source file {stored_path} is missing. "
                   if stored_path else "the armature records no source .sluggie to read the model from. ")
                + "Export into the existing .sluggie of this model instead, "
                + SluggiePath.LOCATE_HINT[0].lower() + SluggiePath.LOCATE_HINT[1:]
                + ", or re-import the model.")
        save_as = (os.path.normcase(os.path.abspath(source_path))
                   != os.path.normcase(os.path.abspath(self.filepath)))

        try:
            with open(source_path, 'rb') as f:
                content = f.read().strip()
        except Exception as e:
            raise ExportCancelled(f"Could not read file: {e}") from e

        if not content:
            raise ExportCancelled(f"Source .sluggie file {source_path} is empty.")

        try:
            data = FieldCodec.loads_sluggie(content)
        except ValueError as e:
            raise ExportCancelled(f"Could not parse the .sluggie file: {e}") from e

        if "SluggiesModel" not in data:
            raise ExportCancelled("JSON does not contain a 'SluggiesModel' entry.")

        if save_as and os.path.exists(self.filepath):
            try:
                target_model = FieldCodec.load_sluggie(self.filepath).get("SluggiesModel")
            except (OSError, ValueError, AttributeError):
                target_model = None
            overwrite_error = ModelCheckCore.overwrite_error(
                data["SluggiesModel"], target_model, os.path.basename(self.filepath))
            if overwrite_error:
                raise ExportCancelled(overwrite_error)

        run.selected, run.hidden_meshes, run.arm_obj = selected, hidden_meshes, arm_obj
        run.source_path, run.save_as, run.data = source_path, save_as, data
        run.use_compression = data["SluggiesModel"].get("UseCompression", True)
        run.submeshes = data["SluggiesModel"].get("Submeshes", [])

    def _collect_objects(self, context, run):
        """Sort the selection into original meshes, custom submeshes and collision objects, and run the
        Check Models error checks on it."""
        from . import ModelCheck
        selected, data, submeshes = run.selected, run.data, run.submeshes

        # --- collect selected mesh objects that carry Sluggies custom properties ---
        # Custom submeshes have no
        # VertexBufferOffset of their own yet — they are new submeshes, not
        # edits to an existing one — so they are collected separately and
        # excluded from the VertexBufferOffset-matching candidates below.
        custom_submesh_candidates = [
            obj for obj in selected
            if obj.type == 'MESH' and obj.get('SluggiesCustomSubmesh')
        ]
        candidates = [
            obj for obj in selected
            if obj.type == 'MESH' and not obj.get('SluggiesCustomSubmesh')
            and all(prop in obj for prop in REQUIRED_PROPS)
        ]

        # Stadium collision: any selected mesh with collision turned on (a model
        # mesh, a custom submesh or a plain collision-only mesh).
        collision_objects = [
            obj for obj in selected
            if obj.type == 'MESH' and getattr(obj, 'sluggies_collision', None) is not None
            and obj.sluggies_collision.enabled
        ]
        # The stadium's original colliders (imported from StadiumCollisionVanilla)
        # count whether selected or not: once the scene has any, the patcher
        # rebuilds the whole collision file from them, so a deleted one is
        # collision removed, not collision left out by the selection.
        collision_armature = run.arm_obj
        original_colliders = [
            obj for obj in context.scene.objects
            if obj.type == 'MESH' and StadiumCollisionExport.REGION_PROP in obj
            and collision_armature is not None and obj.parent is collision_armature
        ]
        collision_objects += [
            obj for obj in original_colliders
            if obj.sluggies_collision.enabled and obj not in collision_objects
        ]

        # "Make invisible": the object exports its collision only, never drawn.
        invisible_objects = [obj for obj in collision_objects if obj.sluggies_collision.invisible]
        invisible_error = StadiumCollisionExport.invisible_model_mesh_error(
            [obj.name for obj in invisible_objects if obj in candidates])
        if invisible_error:
            raise ExportCancelled(invisible_error)
        custom_submesh_candidates = [obj for obj in custom_submesh_candidates if obj not in invisible_objects]

        if not candidates and not custom_submesh_candidates and not collision_objects:
            raise ExportCancelled(
                "No selected mesh objects with Sluggies custom properties found. "
                "Import the JSON first, then select the meshes you want to export.")

        if len(candidates) > len(submeshes):
            raise ExportCancelled(
                f"{len(candidates)} mesh(es) selected but the target JSON only defines "
                f"{len(submeshes)} submesh(es). Make sure you are exporting to the correct file.")

        # The Check Models error checks on the selected model, before anything
        # is encoded (ModelCheck is imported in the function: it imports this
        # module).
        check_errors = ModelCheck.export_errors(
            context, data["SluggiesModel"], self.filepath,
            candidates + custom_submesh_candidates + [
                obj for obj in selected if obj.type == 'ARMATURE'
            ],
            self.reimport_textures,
        )
        if check_errors:
            raise ExportCancelled(check_errors)
        try:
            size_line = ModelCheck.export_size_line(
                context, data["SluggiesModel"], self.filepath,
                candidates + custom_submesh_candidates,
            )
        except Exception as exc:  # the estimate must never stop an export
            size_line = f"Size estimate failed: {exc}"

        run.candidates, run.custom_submesh_candidates = candidates, custom_submesh_candidates
        run.collision_objects, run.original_colliders = collision_objects, original_colliders
        run.invisible_objects, run.size_line = invisible_objects, size_line

    def _match_submeshes(self, run):
        """Pair each selected original mesh with its submesh of the file and check its materials."""
        submeshes, warnings = run.submeshes, run.warnings
        matched_objects = {}
        for obj in run.candidates:
            # match by VertexBufferOffset (unique per submesh)
            target_submesh = next(
                (sm for sm in submeshes
                 if str(obj["VertexBufferOffset"]) == str(sm.get("VertexBuffer", {}).get("VertexBufferOffset"))),
                None
            )
            if target_submesh is None:
                warnings.append(
                    f"{obj.name}: no submesh with VertexBufferOffset="
                    f"{obj['VertexBufferOffset']} found in JSON — skipped."
                )
                continue
            submesh_index = next(i for i, sm in enumerate(submeshes) if sm is target_submesh)
            # Two selected objects for one submesh (e.g. a mesh and its
            # re-imported `_edit` copy) would silently let the last one win.
            if submesh_index in matched_objects:
                raise ExportCancelled(
                    f"{matched_objects[submesh_index].name} and {obj.name} both belong to "
                    f"submesh {submesh_index}. Select only one of them and export again."
                )
            matched_objects[submesh_index] = obj
            run.object_submeshes.append((obj, target_submesh))
            run.object_submesh_indices.append((obj, submesh_index))

            # Every material must be a surface of this submesh: imported, or
            # added with Add material (rigid submeshes only).
            new_materials = _find_new_materials(obj, target_submesh, submesh_index)
            if new_materials:
                names = ", ".join(f"'{n}' ({r})" for n, r in new_materials)
                raise ExportCancelled(
                    f"Error: {obj.name} uses material(s) that are not surfaces of this "
                    f"submesh: {names}. Use its imported materials or Add material, and "
                    f"reassign or remove the other faces."
                )

        # an empty Image Texture node blocks the export, whatever
        # the texture toggles say.
        image_errors = _empty_image_node_errors(run.candidates + run.custom_submesh_candidates)
        if image_errors:
            raise ExportCancelled(image_errors)

    def _resolve_material_textures(self, run):
        """Find the model's texture list (a Low model uses its High model's) and what the original
        meshes' materials change in it."""
        data = run.data
        run.local_texture_descriptors = (
            data["SluggiesModel"].get("TextureDescriptors") or []
        )
        owns_texture_section = bool(run.local_texture_descriptors)
        additions, desired_assignments, changed_materials = [], {}, []
        # A material showing another texture (a new PNG, or another of the
        # model's textures) is exported whatever the Reimport textures toggle
        # says: the toggle only decides whether the original textures are
        # re-encoded from tex/. A `_L_` model binds its High model's textures
        # by index (Manage texture slots sets these images).
        try:
            texture_descriptors, texture_dir, owns_texture_section, texture_owner_path = (
                _resolve_export_texture_context(
                    self.filepath, data["SluggiesModel"], with_owner=True)
            )
            if owns_texture_section or texture_descriptors:
                additions, desired_assignments, changed_materials = (
                    _resolve_material_texture_changes(
                        run.object_submeshes,
                        texture_descriptors,
                        texture_dir,
                        path_resolver=bpy.path.abspath,
                        validate_texture_files=owns_texture_section,
                        skip_unset_images=not owns_texture_section,
                        require_donor_files=self.reimport_textures,
                        planned_copies=run.planned_texture_copies,
                    )
                )
        except ValueError as exc:
            raise ExportCancelled(str(exc)) from exc
        # Names the High file a Low export paired with, so a wrong pairing
        # (file names that don't match) shows in the error.
        paired_main_model = (
            f"paired main model ({os.path.basename(texture_owner_path)})"
            if texture_owner_path else "paired main model"
        )

        if additions and not owns_texture_section:
            raise ExportCancelled(
                f"Low-poly model textures are owned by the {paired_main_model}. A low-poly "
                "material can only show one of the main model's textures (see Manage "
                "texture slots); add new PNGs on the main model and export it first: "
                f"{', '.join(addition['TextureFileName'] for addition in additions)}",
            )

        run.texture_descriptors, run.texture_dir = texture_descriptors, texture_dir
        run.owns_texture_section, run.paired_main_model = owns_texture_section, paired_main_model
        run.additions, run.desired_assignments = additions, desired_assignments
        run.changed_materials = changed_materials

    def _encode_bones(self, context, run):
        """Added bones (Add Bone): model-level, written only when the armature has any SluggiesUserAdded
        bone. Runs before the custom submeshes, which resolve their host bones against it: a custom submesh
        may be hosted on a user-added bone that has no BoneHierarchy entry yet."""
        selected, data, warnings = run.selected, run.data, run.warnings
        # Everything below reads bone ids from bone_<N> names; refuse while an
        # added bone's name and its exported id disagree (a deleted added
        # bone shifts the later ids, 2026-09-29).
        name_error = added_bone_name_error(selected, context)
        if name_error:
            raise ExportCancelled(name_error)
        encode_bone_hierarchy_edited(selected, data, warnings, context)
        warnings.extend(untracked_keys_warnings(selected, context))
        bone_hierarchy_errors = validate_bone_hierarchy_edited_export(data["SluggiesModel"])
        if bone_hierarchy_errors:
            raise ExportCancelled(bone_hierarchy_errors)

    def _encode_custom_submeshes(self, context, run):
        """Encode the selected custom submeshes. Their PNGs are added whatever the Reimport textures toggle
        says. CustomSubmeshes always mirrors the current selection, like AdditionalTextureDescriptors."""
        custom_submesh_candidates, candidates = run.custom_submesh_candidates, run.candidates
        warnings, infos = run.warnings, run.infos
        if not custom_submesh_candidates:
            return
        model = run.data["SluggiesModel"]
        try:
            validate_custom_submeshes(
                custom_submesh_candidates, candidates, model, warnings
            )
            primary_materials = []
            custom_texture_entries = []
            for obj in custom_submesh_candidates:
                new_materials = _find_new_materials(obj, None)
                if new_materials:
                    names = ", ".join(f"'{n}' ({r})" for n, r in new_materials)
                    raise ValueError(
                        f"{obj.name}: custom submeshes support only their own surface "
                        f"materials ({names})"
                    )
                material = _custom_submesh_material(obj)
                if material is None:
                    raise ValueError(
                        f"{obj.name}: custom submesh has no material with its own "
                        f"SurfaceId '{obj.get('CustomSubmeshId')}_ds0'"
                    )
                template_texture_index = _custom_submesh_template_texture_index(
                    model, str(obj.get("TemplateSource") or "")
                )
                primary_materials.append(material)
                custom_texture_entries.append((material, template_texture_index))
                # Add-material surfaces on the custom submesh resolve their
                # images the same way.
                additional_materials, _unused = _custom_submesh_additional_materials(obj)
                for extra in additional_materials.values():
                    custom_texture_entries.append(
                        (extra, int(extra.get("TemplateTextureIndex", -1)))
                    )
            (
                custom_additions, custom_assignments, _copies,
            ) = _resolve_custom_submesh_texture_changes(
                custom_texture_entries,
                run.texture_descriptors,
                run.texture_dir,
                path_resolver=bpy.path.abspath,
                planned_copies=run.planned_texture_copies,
            )
            run.custom_additions = custom_additions
            if custom_additions and not run.owns_texture_section:
                raise ValueError(
                    f"Low-poly model textures are owned by the {run.paired_main_model}; "
                    "a custom submesh on it can only use an existing texture: "
                    + ", ".join(obj.name for obj in custom_submesh_candidates)
                )
            for obj, material in zip(custom_submesh_candidates, primary_materials):
                run.custom_submesh_entries.append(encode_custom_submesh(
                    context, obj, model, custom_assignments[material.name],
                    warnings, run.use_compression,
                    material.get("SpecularStrength"),
                    texture_assignments=custom_assignments, infos=infos,
                ))
        except ValueError as exc:
            raise ExportCancelled(str(exc)) from exc

    def _plan_rebuilds(self, context, run):
        """Decide which original meshes are exported as a whole rebuild.

        Each rigid donor submesh takes the in-place path unless its topology, transform, host bone (world
        position kept) or surfaces changed, or its positions outgrew the donor's format; then it is exported
        as a whole RigidRebuild. The skinned body is a whole SkinnedRebuild when its topology, transform or
        surfaces changed in a way the slot-preserving paths cannot store. New Add-material surfaces resolve
        their images like custom submeshes."""
        data, warnings = run.data, run.warnings
        rigid_plans, skinned_plans = run.rigid_plans, run.skinned_plans
        try:
            for (obj, target_submesh), (_obj, submesh_index) in zip(
                    run.object_submeshes, run.object_submesh_indices):
                if RigidRebuildExport.is_rigid_submesh(target_submesh):
                    plan = _rigid_rebuild_plan(
                        context, obj, submesh_index, target_submesh, data["SluggiesModel"], warnings,
                    )
                    if plan is not None:
                        rigid_plans[submesh_index] = plan
                elif SkinnedRebuildExport.is_skinned_submesh(target_submesh) and submesh_index == 0:
                    plan = _skinned_rebuild_plan(
                        context, obj, submesh_index, target_submesh, data["SluggiesModel"], warnings,
                    )
                    if plan is not None:
                        skinned_plans[submesh_index] = plan
            new_surface_entries = [
                (plan.new_surface_materials[key], int(plan.new_surface_materials[key].get("TemplateTextureIndex", -1)))
                for plan in list(rigid_plans.values()) + list(skinned_plans.values())
                for key in plan.routing.new_keys
            ]
            if new_surface_entries:
                (
                    run.new_surface_additions, run.new_surface_assignments, _copies,
                ) = _resolve_custom_submesh_texture_changes(
                    new_surface_entries, run.texture_descriptors, run.texture_dir,
                    path_resolver=bpy.path.abspath,
                    planned_copies=run.planned_texture_copies,
                )
                new_names = ", ".join(mat.name for mat, _index in new_surface_entries)
                if run.new_surface_additions and not run.owns_texture_section:
                    raise ValueError(
                        f"Low-poly model textures are owned by the {run.paired_main_model}; a new "
                        f"material on it can only use an existing texture: {new_names}"
                    )
        except ValueError as exc:
            raise ExportCancelled(str(exc)) from exc

    def _exclude_unselected(self, run):
        """Unselected original meshes are left out of the patched model (option). Not on stadiums: a
        collision-only export selects no mesh at all."""
        data, submeshes, infos = run.data, run.submeshes, run.infos
        stadium_model = TemplateSources.is_stadium_chunk(data["SluggiesModel"].get("ChunkNumber"))
        excluded_indices = ExportMode.exclude_unselected_submeshes(
            submeshes, [index for _obj, index in run.object_submesh_indices],
            self.exclude_unselected and not stadium_model,
        )
        if self.exclude_unselected and stadium_model:
            infos.append("Stadium: unselected meshes are kept (Exclude all unselected meshes is for characters)")
        for index in excluded_indices:
            _drop_facial_edits_for_submesh(data, index)
        run.excluded_names = [submeshes[i].get("MeshName") or f"submesh {i}" for i in excluded_indices]
        if run.excluded_names:
            infos.append(f"Excluded from the patched model (not selected): {', '.join(run.excluded_names)}")
        if run.hidden_meshes:
            run.warnings.append("Armature selected: its hidden meshes are not exported: "
                                + ", ".join(obj.name for obj in run.hidden_meshes))

    def _decide_mode(self, context, run):
        """Export mode: in-place when nothing would be lost, else Hammerspace. Model-level reasons skip the
        in-place attempt; ``_encode_meshes`` adds the reasons only an encode can find."""
        data, submeshes = run.data, run.submeshes
        run.hammerspace_reasons = ExportMode.model_level_reasons(
            data["SluggiesModel"],
            custom_submesh_names=[obj.name for obj in run.custom_submesh_candidates],
            has_new_bones=armature_has_new_bones(run.selected, context),
            changed_materials=run.changed_materials,
            resized_textures=(
                ExportMode.texture_size_changes(run.local_texture_descriptors, run.texture_dir)
                if self.reimport_textures and run.owns_texture_section else []
            ),
            stale_submeshes=ExportMode.stale_hammerspace_submeshes(
                submeshes, [obj["VertexBufferOffset"] for obj, _sm in run.object_submeshes]
            ),
            excluded_submeshes=run.excluded_names,
        ) + [
            RigidRebuildExport.mode_reason(plan.obj.name, plan.reasons)
            for plan in run.rigid_plans.values()
        ] + [
            SkinnedRebuildExport.mode_reason(plan.obj.name, plan.reasons)
            for plan in run.skinned_plans.values()
        ]
        run.use_hammerspace = bool(run.hammerspace_reasons)

    def _encode_meshes(self, context, run):
        """Encode the original meshes and the model-level data that depends on them, in up to two passes.

        Without a model-level reason the meshes are first encoded in place on a copy; any edit that attempt
        cannot store adds a reason, and everything is encoded again in Hammerspace mode on the real data,
        with the notes as they were before the attempt."""
        base_data, base_warnings, base_errors, base_infos = run.data, run.warnings, run.errors, run.infos
        use_hammerspace = run.use_hammerspace
        while True:
            data = base_data if use_hammerspace else copy.deepcopy(base_data)
            run.data, run.use_hammerspace = data, use_hammerspace
            run.warnings, run.errors, run.infos = list(base_warnings), list(base_errors), list(base_infos)
            run.written = 0
            pass_reasons = run.pass_reasons = []

            for obj, submesh_index in run.object_submesh_indices:
                if self._encode_object(run, obj, submesh_index):
                    run.written += 1

            # The in-place skin encoder assumes the donor vertex layout, so a
            # mesh edit that already needs Hammerspace switches before it runs.
            if not use_hammerspace and pass_reasons:
                run.hammerspace_reasons = pass_reasons
                use_hammerspace = True
                continue

            self._encode_model_level(context, run)

            if use_hammerspace or not pass_reasons:
                break
            run.hammerspace_reasons = pass_reasons
            use_hammerspace = True

        if use_hammerspace:
            ExportMode.promote_inplace_uv_edits(
                data["SluggiesModel"]["Submeshes"],
                [obj["VertexBufferOffset"] for obj, _index in run.object_submesh_indices],
            )

    def _encode_object(self, run, obj, submesh_index):
        """Encode one original mesh into its submesh of the current pass, by the path that applies (rigid
        rebuild, body rebuild, Hammerspace or in place). False when the mesh was skipped."""
        target_submesh = run.data["SluggiesModel"]["Submeshes"][submesh_index]
        warnings, infos, use_compression = run.warnings, run.infos, run.use_compression
        new_surface_assignments = run.new_surface_assignments
        plan = run.rigid_plans.get(submesh_index)
        skinned_plan = run.skinned_plans.get(submesh_index)
        if plan is not None:
            # Whole-blob rebuild: none of the in-place edit fields may
            # stay on the submesh (the patcher refuses the mix).
            try:
                entry = _rigid_rebuild_entry(plan, new_surface_assignments, use_compression, warnings, infos)
            except ValueError as exc:
                raise ExportCancelled(str(exc)) from exc
            RigidRebuildExport.strip_in_place_edits(target_submesh)
            target_submesh.pop("SkinnedRebuild", None)
            target_submesh["RigidRebuild"] = entry
        elif skinned_plan is not None:
            try:
                entry = _skinned_rebuild_entry(skinned_plan, new_surface_assignments, use_compression, warnings, infos)
            except ValueError as exc:
                raise ExportCancelled(str(exc)) from exc
            SkinnedRebuildExport.strip_in_place_edits(target_submesh)
            target_submesh.pop("RigidRebuild", None)
            target_submesh["SkinnedRebuild"] = entry
        elif run.use_hammerspace:
            self._encode_object_hammerspace(run, obj, target_submesh)
        elif not self._encode_object_inplace(run, obj, target_submesh):
            return False

        surf_mat = self._write_display_state_edits(obj, target_submesh)

        if plan is not None or skinned_plan is not None:
            # Faces are routed inside the rebuild entry itself.
            target_submesh.pop("FaceSurfaceIdsEdited", None)
            return True

        self._encode_face_surface_edits(run, obj, target_submesh, surf_mat)
        return True

    def _encode_object_hammerspace(self, run, obj, target_submesh):
        """Hammerspace path of one mesh: every buffer is re-encoded from Blender and may change size."""
        warnings, errors = run.warnings, run.errors
        target_submesh.pop("RigidRebuild", None)
        target_submesh.pop("SkinnedRebuild", None)
        try:
            hs = encode_mesh_hammerspace(
                obj,
                target_submesh,
                use_custom_normals=self.use_custom_normals,
                use_compression=run.use_compression,
            )
        except ValueError as exc:
            raise ExportCancelled(f"{obj.name}: {exc}") from exc
        target_submesh["VertexBuffer"]["VertexBufferDataEdited"] = hs['VertexBufferDataEdited']
        target_submesh["FacesDataEdited"] = hs['FacesDataEdited']
        target_submesh["FacesCountEdited"] = hs['FacesCountEdited']
        target_submesh["FaceTextureIndicesEdited"] = hs['FaceTextureIndicesEdited']
        for json_channel in target_submesh.get("UVChannels", []):
            ch_ind = json_channel.get("UVChannelIndex", 0)
            if ch_ind in hs['UVEdits']:
                uv_data_enc, uv_faces_enc = hs['UVEdits'][ch_ind]
                json_channel["UVChannelDataEdited"] = uv_data_enc
                json_channel["UVFacesDataEdited"] = uv_faces_enc
            else:
                layer_name = _uv_layer_name(target_submesh.get("UVChannels", []), ch_ind)
                warnings.append(
                    f"{obj.name}: UV layer '{layer_name}' not found — UV channel {ch_ind} skipped."
                )

        # Per-loop normals (standalone NormalBuffer) and per-loop colors
        # — written only when the submesh actually has them.
        normal_buffer = target_submesh.get("NormalBuffer")
        if hs.get("NormalEdits") is not None and isinstance(normal_buffer, dict):
            normal_data_enc, normal_faces_enc = hs["NormalEdits"]
            normal_buffer["NormalBufferDataEdited"] = normal_data_enc
            normal_buffer["NormalFacesDataEdited"] = normal_faces_enc
        elif isinstance(normal_buffer, dict):
            # Submesh has a NormalBuffer the exporter refused to encode —
            # drop stale per-loop edits so the original NormalBuffer stays authoritative.
            if (self.use_custom_normals
                    and normal_buffer.get("NormalBufferData")):
                # The user asked for normals to be overwritten and the
                # submesh has a standalone buffer, so encode_normal_edits
                # bailed out: extended records (CompCount > 3) could not
                # keep their donor tail mapping. Silently dropping this
                # ships a model whose lighting is not what was edited.
                donor_loops = len(_to_bytes(
                    normal_buffer.get("NormalFacesData") or b""
                )) // 2
                errors.append(
                    f"{obj.name}: standalone normal overwrite DROPPED — "
                    f"extended normal records (CompCount "
                    f"{normal_buffer.get('NormalBufferCompCount')}) could not "
                    f"preserve their donor mapping ({donor_loops} donor loops). "
                    f"The donor normals were kept; your normal edits are NOT "
                    f"in this export. Keep the original topology or turn "
                    f"Overwrite Normals off."
                )
            normal_buffer.pop("NormalBufferDataEdited", None)
            normal_buffer.pop("NormalFacesDataEdited", None)
        for json_channel in target_submesh.get("ColorChannels", []):
            ch_ind = json_channel.get("ColorChannelIndex", 0)
            if ch_ind in hs["ColorEdits"]:
                color_data_enc, color_faces_enc = hs["ColorEdits"][ch_ind]
                json_channel["ColorChannelDataEdited"] = color_data_enc
                json_channel["ColorFacesDataEdited"] = color_faces_enc
            else:
                json_channel.pop("ColorChannelDataEdited", None)
                json_channel.pop("ColorFacesDataEdited", None)
                warnings.append(
                    f"{obj.name}: color attribute 'color{ch_ind}' not found — "
                    f"color channel {ch_ind} skipped."
                )

    def _encode_object_inplace(self, run, obj, target_submesh):
        """In-place path of one mesh: every buffer keeps its original size. An edit this path cannot store
        becomes a reason in ``run.pass_reasons``. False when the mesh was skipped (metadata mismatch)."""
        warnings, errors, pass_reasons = run.warnings, run.errors, run.pass_reasons
        use_compression = run.use_compression
        target_submesh.pop("RigidRebuild", None)
        target_submesh.pop("SkinnedRebuild", None)
        mismatches = validate_against_json(obj, target_submesh)
        if mismatches:
            warnings.append(
                f"{obj.name}: metadata mismatch ({'; '.join(mismatches)}) — skipped."
            )
            return False

        length_issues = detect_length_mismatches(obj, target_submesh)
        if length_issues:
            pass_reasons.append(f"{obj.name}: " + "; ".join(length_issues))
        elif ExportMode.topology_changed(
            (poly.vertices for poly in obj.data.polygons),
            target_submesh.get("FacesData"),
        ):
            pass_reasons.append(f"{obj.name}: faces changed")

        try:
            edited_data = encode_vertex_buffer_edited(
                obj,
                obj["VertexBufferCompCount"],
                obj["VertexBufferQuantizeInfo"],
                use_custom_normals=self.use_custom_normals,
                use_compression=use_compression,
                donor_data=target_submesh["VertexBuffer"].get("VertexBufferData"),
            )
        except ValueError as exc:
            raise ExportCancelled(f"{obj.name}: {exc}") from exc
        target_submesh["VertexBuffer"]["VertexBufferDataEdited"] = edited_data
        # Clear any stale hammerspace face data so it can't mismatch the
        # in-place vertex buffer (which must stay at the original vertex count).
        target_submesh.pop("FacesDataEdited", None)
        target_submesh.pop("FacesCountEdited", None)
        target_submesh.pop("FaceTextureIndicesEdited", None)
        loop_indices = [
            li for poly in obj.data.polygons for li in poly.loop_indices
        ]
        inplace_normal_buffer = target_submesh.get("NormalBuffer")
        if (self.use_custom_normals
                and isinstance(inplace_normal_buffer, dict)
                and "NormalBufferData" in inplace_normal_buffer):
            if _apply_inplace_normal_edits(
                obj,
                inplace_normal_buffer,
                loop_indices,
                errors,
                use_compression,
            ):
                pass_reasons.append(
                    f"{obj.name}: normals split on shared normal slots"
                )
        elif isinstance(inplace_normal_buffer, dict):
            inplace_normal_buffer.pop("NormalBufferDataEdited", None)
            inplace_normal_buffer.pop("NormalFacesDataEdited", None)
        # In-place patching cannot write colours: any colour edit
        # needs Hammerspace.
        for json_channel in target_submesh.get("ColorChannels", []):
            json_channel.pop("ColorChannelDataEdited", None)
            json_channel.pop("ColorFacesDataEdited", None)
            encoded_colors = encode_color_edits(
                obj, json_channel, loop_indices, use_compression=False
            )
            if encoded_colors is not None and ExportMode.colors_changed(
                bytes(encoded_colors[0]), json_channel
            ):
                pass_reasons.append(
                    f"{obj.name}: vertex colours "
                    f"(color{json_channel.get('ColorChannelIndex', 0)}) edited"
                )

        # Re-encode UV channels from Blender UV layers
        uv_seam_reason_added = False
        _all_uv_ch = target_submesh.get("UVChannels", [])
        for json_channel in _all_uv_ch:
            result = encode_uv_channel_edited(obj, json_channel, use_compression=use_compression, all_uv_channels=_all_uv_ch)
            ch_ind = json_channel.get("UVChannelIndex", 0)
            if result is None:
                layer_name = _uv_layer_name(_all_uv_ch, ch_ind)
                warnings.append(
                    f"{obj.name}: UV layer '{layer_name}' not found — UV channel {ch_ind} skipped."
                )
                continue
            uv_data_enc, conflicts = result
            if conflicts and not uv_seam_reason_added:
                pass_reasons.append(f"{obj.name}: UV seams split")
                uv_seam_reason_added = True
            json_channel["UVChannelDataEdited"] = uv_data_enc
            # UVFacesDataEdited is no longer written: the draw list indices
            # are unchanged so UVFacesData still applies after patching.
        return True

    @staticmethod
    def _write_display_state_edits(obj, target_submesh):
        """Write the shader mode and specular strength edits of the mesh's surface materials into its
        Type-7 display states. Returns the materials by SurfaceId."""
        # Write back DisplayState shader modes (Type-7 FourCC codes) if edited.
        # Prefer mat["ShaderMode"] on the surface material (new path, 2.2+).
        # Fall back to DS_{surface_id}_ShaderMode on the object (legacy path).
        surf_mat = {
            mat.get("SurfaceId"): mat
            for slot in obj.material_slots
            if (mat := slot.material) is not None and mat.get("SurfaceId")
        }
        for ds_idx, ds in enumerate(target_submesh.get("DisplayStates", [])):
            if ds.get("DisplayStateId") != 7:
                continue
            original = ds.get("ShaderMode", "")
            surface_id = ds.get("SurfaceId") or f"ds{ds_idx}"
            mat = surf_mat.get(surface_id)
            if mat is not None:
                new_val = str(mat.get("ShaderMode") or "")
            else:
                prop_val = f"DS_{surface_id}_ShaderMode"
                new_val = str(obj[prop_val]) if prop_val in obj else None
            if new_val is not None and new_val != original:
                ds["ShaderModeEdited"] = new_val
            else:
                ds.pop("ShaderModeEdited", None)

        # Write back specular strength (struct offset +1 = index 0 of the
        # 3-byte DisplayStateParamBytes) if edited via the material's
        # "SpecularStrength" custom property. Indices 1 and 2 are preserved.
        for ds_idx, ds in enumerate(target_submesh.get("DisplayStates", [])):
            if ds.get("DisplayStateId") != 7:
                continue
            original_hex = ds.get("DisplayStateParamBytes", "000000")
            surface_id = ds.get("SurfaceId") or f"ds{ds_idx}"
            mat = surf_mat.get(surface_id)
            if mat is None or "SpecularStrength" not in mat:
                ds.pop("DisplayStateParamBytesEdited", None)
                continue
            try:
                original_bytes = bytes.fromhex(original_hex).ljust(3, b'\x00')[:3]
            except ValueError:
                original_bytes = b'\x00\x00\x00'
            new_strength = max(0, min(255, int(mat["SpecularStrength"])))
            new_bytes = bytes([new_strength, original_bytes[1], original_bytes[2]])
            new_hex = new_bytes.hex()
            if new_hex != original_hex:
                ds["DisplayStateParamBytesEdited"] = new_hex
            else:
                ds.pop("DisplayStateParamBytesEdited", None)
        return surf_mat

    def _encode_face_surface_edits(self, run, obj, target_submesh, surf_mat):
        """Export the per-face draw-state assignment when faces have been moved to other materials."""
        try:
            face_sid_data, face_sid_changed = _encode_face_surface_assignment(
                obj, target_submesh.get("DisplayStates", []),
                surf_mat, run.use_compression, run.warnings,
            )
        except ValueError as exc:
            raise ExportCancelled(str(exc)) from exc
        if face_sid_changed and not RigidRebuildExport.is_rigid_submesh(target_submesh):
            partial_error = _partial_move_error(
                obj, target_submesh.get("DisplayStates", []), surf_mat,
            )
            if partial_error:
                raise ExportCancelled(partial_error)
        if face_sid_changed:
            if not ENABLE_MATERIAL_REASSIGNMENT_EXPORT:
                raise ExportCancelled(
                    f"{obj.name}: material reassignment is currently disabled. "
                    "Dolphin testing found unresolved runtime corruption even "
                    "for complete moves between compatible donor surfaces. "
                    "Restore every face to its originally imported material "
                    "before exporting."
                )
            target_submesh["FaceSurfaceIdsEdited"] = face_sid_data
            if not run.use_hammerspace:
                run.pass_reasons.append(f"{obj.name}: faces moved to other materials")
        else:
            target_submesh.pop("FaceSurfaceIdsEdited", None)

    def _encode_model_level(self, context, run):
        """The model-level data of one pass: bone reassignments of unskinned meshes, skin, facial poses and
        the root bone scale."""
        candidates, data, warnings = run.candidates, run.data, run.warnings
        skinned_plans, rigid_plans = run.skinned_plans, run.rigid_plans
        use_hammerspace, pass_reasons = run.use_hammerspace, run.pass_reasons
        # Skin data is model-level — purge stale edited fields, then re-encode.
        # Both only make sense when a skinned donor mesh is actually part of
        # this export: purging and re-encoding without one would drop the
        # model's whole SK1/SK2/SKAcc structure (and any skin edit from an
        # earlier run) on, say, a custom-submesh-only export.
        encode_unskinned_bone_reassignments(candidates, data, warnings)
        if skinned_plans:
            # The rebuilt body carries its own weights; SkinDataEdited
            # describes the donor's slots and must not be written.
            _purge_skn_edited(data)
        elif skinned_donor_objects(candidates, data):
            _purge_skn_edited(data)
            if use_hammerspace:
                try:
                    skn_ok, skn_msg = encode_skin_hammerspace(
                        candidates,
                        data,
                        warnings,
                        use_custom_normals=self.use_custom_normals,
                    )
                except ValueError as exc:
                    raise ExportCancelled(str(exc)) from exc
                if not skn_ok:
                    raise ExportCancelled(skn_msg)
                if skn_msg:
                    self.report({"INFO"}, skn_msg)
            elif encode_skin_weights_inplace(
                candidates, data, warnings, use_custom_normals=self.use_custom_normals
            ) is None:
                pass_reasons.append("skin data no longer fits its original size")
        elif data["SluggiesModel"].get("SkinData") and _skin_data_edited_is_empty(data):
            # Only an export from before this guard existed can have left
            # an empty SkinDataEdited on a skinned model; it would unskin
            # the model. Drop it so the donor structure is used again.
            _purge_skn_edited(data)
            warnings.append(
                "Removed an empty SkinDataEdited left by an earlier export that "
                "included no skinned mesh; the model's donor skinning is used again."
            )

        rebuilt_objects = [p.obj for p in list(skinned_plans.values()) + list(rigid_plans.values())]
        try:
            update_facial_pose_edits(
                [obj for obj in candidates if not any(rebuilt is obj for rebuilt in rebuilt_objects)],
                data, warnings,
            )
        except ValueError as exc:
            raise ExportCancelled(f"Facial pose edit: {exc}") from exc
        for rebuilt_plan in list(skinned_plans.values()) + list(rigid_plans.values()):
            _drop_facial_edits_for_submesh(data, rebuilt_plan.submesh_index)

        # Whole-model root-bone scale — model-level, written only when edited.
        run.root_scale = encode_root_bone_scale_edited(candidates, data, warnings, context)

    def _report_mode(self, run):
        """Report the chosen export mode, the size estimate and how each rigid or rebuilt mesh was written."""
        data, hammerspace_reasons = run.data, run.hammerspace_reasons
        self.report({"INFO"}, ExportMode.mode_message(hammerspace_reasons))
        if run.size_line:
            self.report({"INFO"}, run.size_line)
        for obj, submesh_index in run.object_submesh_indices:
            plan = run.rigid_plans.get(submesh_index)
            if plan is not None:
                self.report({"INFO"},
                    f"{obj.name}: rigid rebuild ({', '.join(plan.reasons)}): "
                    f"{len(plan.donor.faces)} -> {len(plan.geometry.faces)} faces, "
                    f"{len(plan.geometry.positions)} vertices on bone_{plan.host_bone_id}; "
                    + RigidRebuildExport.surface_report(obj.name, plan.routing)
                )
            elif submesh_index in run.skinned_plans:
                skinned_plan = run.skinned_plans[submesh_index]
                self.report({"INFO"},
                    f"{obj.name}: body rebuild ({', '.join(skinned_plan.reasons)}): "
                    f"{len(skinned_plan.donor.faces)} -> {len(skinned_plan.geometry.faces)} faces, "
                    f"{len(skinned_plan.geometry.positions)} vertices; "
                    + RigidRebuildExport.surface_report(obj.name, skinned_plan.routing)
                )
            elif RigidRebuildExport.is_rigid_submesh(data["SluggiesModel"]["Submeshes"][submesh_index]):
                self.report({"INFO"}, f"{obj.name}: in-place rigid edit (no rebuild needed).")

    def _encode_collision(self, context, run):
        """StadiumCollision mirrors the selection once a file has it; a file that never had collision gets
        no field, so exporting a night model leaves the collision file it shares with the day model alone."""
        collision_objects, original_colliders = run.collision_objects, run.original_colliders
        infos = run.infos
        model = run.data["SluggiesModel"]
        collision_error = StadiumCollisionExport.chunk_error(
            model.get("ChunkNumber"), [obj.name for obj in collision_objects])
        if collision_error:
            raise ExportCancelled(collision_error)
        if original_colliders:
            model[StadiumCollisionExport.REPLACE_FIELD] = True
        else:
            model.pop(StadiumCollisionExport.REPLACE_FIELD, None)
        if collision_objects or original_colliders or model.get(StadiumCollisionExport.FIELD):
            try:
                model[StadiumCollisionExport.FIELD] = encode_stadium_collision(
                    context, collision_objects, run.arm_obj, run.warnings)
            except ValueError as exc:
                raise ExportCancelled(str(exc)) from exc
            if original_colliders:
                infos.append(
                    f"Stadium collision rebuilt from the scene: {len(original_colliders)} original "
                    "collider(s) in the scene (deleted or unticked ones are removed from the game)")
            if collision_objects:
                infos.append(
                    f"Stadium collision: {len(collision_objects)} object(s) "
                    f"({', '.join(obj.name for obj in collision_objects)})")
                if run.invisible_objects:
                    infos.append("Collision only, not drawn: "
                                 + ", ".join(obj.name for obj in run.invisible_objects))
            else:
                infos.append("Stadium collision: no collision object selected; "
                             "the patcher restores the stadium's vanilla collision")
        else:
            model.pop(StadiumCollisionExport.FIELD, None)

    def _report_notes(self, run):
        """Report the notes the encode collected, and refuse an export that wrote nothing."""
        custom_submesh_entries = run.custom_submesh_entries
        # INFO stays out of the report popup: the file browser shows only
        # WARNING and above there; the Info editor still lists these.
        for i in run.infos:
            self.report({"INFO"}, i)
        for w in run.warnings:
            self.report({"WARNING"}, w)
        for e in run.errors:
            self.report({"ERROR"}, e)

        written = run.written
        written += len(custom_submesh_entries)
        run.written = written
        if written == 0 and not run.collision_objects:
            raise ExportCancelled("No submeshes written. Check the warnings above.")

    def _finish_model_fields(self, run):
        """Write the model-level fields that describe the whole export: the mode, the texture lists and
        the custom submeshes."""
        data, texture_dir, use_hammerspace = run.data, run.texture_dir, run.use_hammerspace
        additions, desired_assignments = run.additions, run.desired_assignments
        custom_additions, new_surface_additions = run.custom_additions, run.new_surface_additions
        custom_submesh_entries = run.custom_submesh_entries
        data["SluggiesModel"]["UseHammerspace"] = use_hammerspace
        data["SluggiesModel"]["ReimportTextures"] = (
            self.reimport_textures and bool(run.local_texture_descriptors)
        )
        # A RigidRebuild left on an unselected submesh by an earlier export
        # keeps its bone move and its PNGs (the two model-level fields above
        # are rebuilt from the selection on every export).
        carry_tex_dir = texture_dir or os.path.join(os.path.dirname(os.path.abspath(self.filepath)), 'tex')

        def _recover_addition(file_name, template_source):
            if not os.path.isfile(os.path.join(carry_tex_dir, file_name)):
                return None
            try:
                return _custom_submesh_template_texture_index(data["SluggiesModel"], template_source)
            except ValueError:
                return None

        carried = RigidRebuildExport.carry_unselected_rebuilds(
            data["SluggiesModel"],
            [index for _obj, index in run.object_submesh_indices],
            data["SluggiesModel"].get("AdditionalTextureDescriptors") or [],
            recover_addition=_recover_addition,
        )
        if carried.errors:
            raise ExportCancelled(carried.errors)
        for message in carried.messages:
            self.report({"INFO"}, message)
        additions = _merge_texture_additions(
            additions, custom_additions + new_surface_additions + carried.additions,
        )
        run.texture_copies = [
            (source_path, os.path.join(texture_dir, name))
            for name, source_path in run.planned_texture_copies.items()
        ]
        if additions:
            data["SluggiesModel"]["AdditionalTextureDescriptors"] = additions
        else:
            data["SluggiesModel"].pop("AdditionalTextureDescriptors", None)
        if desired_assignments:
            data["SluggiesModel"]["DesiredTextureAssignments"] = desired_assignments
        else:
            data["SluggiesModel"].pop("DesiredTextureAssignments", None)
        if custom_submesh_entries:
            data["SluggiesModel"]["CustomSubmeshes"] = custom_submesh_entries
        else:
            data["SluggiesModel"].pop("CustomSubmeshes", None)
        # A vanilla texture no surface shows any more takes the first new PNG
        # shown in its place instead of appending it.
        reused = TextureSlotsCore.reuse_freed_texture_slots(data["SluggiesModel"])
        for message in reused:
            self.report({"INFO"}, message)
        if reused:
            self.report({"INFO"},
                "Texture slots were refilled: a paired low-poly (_L_) model may now show other "
                "textures; check it with Manage texture slots.")

    def _copy_textures(self, run):
        """PNGs linked from outside tex/ (and other formats' PNG conversions) are copied in now, once
        nothing but the write itself can cancel the export, so the patcher finds them by name."""
        texture_copies = run.texture_copies
        try:
            for source_path, target_path in texture_copies:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                shutil.copyfile(source_path, target_path)
        except OSError as exc:
            raise ExportCancelled(f"Could not copy texture into tex/: {exc}") from exc
        for source_path, target_path in texture_copies:
            original = TextureConvert.source_of(source_path)
            if original:
                self.report({"INFO"}, f"Converted {original} to the PNG {target_path}")
            else:
                self.report({"INFO"}, f"Copied {source_path} to {target_path}")

    def _write_file(self, context, run):
        """Write the ``.sluggie`` and make the scene belong to it."""
        data, arm_obj, root_scale = run.data, run.arm_obj, run.root_scale
        try:
            FieldCodec.dump_sluggie(data, self.filepath)
        except OSError as exc:
            raise ExportCancelled(
                f"Could not write {self.filepath}: {exc}. The file is as it was before the export.") from exc
        self._wrote_file = True
        # The scene now belongs to the written file (a new name, or the
        # target an armature with a lost source read): the next export,
        # Check Models and Texture slots use it.
        if arm_obj is not None:
            SluggiePath.remember(arm_obj, self.filepath)
        if run.save_as and arm_obj is not None:
            self.report({"INFO"},
                f"Saved as a new file; {arm_obj.name} now uses {self.filepath} "
                f"({os.path.basename(run.source_path)} is unchanged).")

        context.window_manager.clipboard = ModelCheckCore.clipboard_text(self.filepath)
        scale_note = (
            f" (root bone scale {root_scale[0]:.4f}, {root_scale[1]:.4f}, {root_scale[2]:.4f})"
            if root_scale else ""
        )
        self.report({"INFO"},
            f"Wrote edited vertex data for {run.written} submesh(es){scale_note} to {self.filepath}")


def menu_func_export(self, context):
    self.layout.operator(SLUGGIES_OT_export.bl_idname, text="Sluggers intermediate (.sluggie)")


def register():
    bpy.utils.register_class(SLUGGIES_OT_export)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.utils.unregister_class(SLUGGIES_OT_export)
