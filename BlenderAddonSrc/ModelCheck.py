"""Check / Repair Models: scope, checks and fixes on the Blender scene.

The rules on plain data live in the bpy-free ``ModelCheckCore``; this module
reads the scene into them. Two callers: the Check Models dialog
(``ModelCheckUI``, every Sluggies armature in the scene) and the exporter
(:func:`export_errors`, the selected model only, error-severity checks).
"""

import json
import math
import os
import shutil
from dataclasses import dataclass, field
from typing import List, Optional

import bpy

from . import AnmExport
from . import CustomSubmeshExport
from . import ExportMode
from . import ExportSluggies as ex
from . import FieldCodec
from . import HostBones
from . import ModelCheckCore as core
from . import SluggiePath
from . import SluggiesToolsPanel as panel
from . import TemplateSources
from .ImportSluggies import _set_surface_material_metadata
from .ModelCheckCore import ERROR, INFO, WARNING, Finding


@dataclass
class ModelScope:
    armature: object                         # bpy armature object, or None
    meshes: list                             # mesh objects of the model
    model: Optional[dict]                    # the .sluggie's SluggiesModel
    sluggie_path: str = ""
    load_error: str = ""
    for_export: bool = False
    reimport_textures: bool = True           # export option; the dialog assumes on
    unparented: list = field(default_factory=list)  # meshes tied only by VertexBufferOffset
    _textures: object = None

    @property
    def name(self):
        return self.armature.name if self.armature is not None else "Model"

    @property
    def stadium(self):
        return TemplateSources.is_stadium_chunk((self.model or {}).get("ChunkNumber"))

    @property
    def folder(self):
        return os.path.basename(os.path.dirname(os.path.abspath(self.sluggie_path))) if self.sluggie_path else ""

    def texture_context(self):
        """(descriptors, tex dir, owns its textures), or (None, None, False)."""
        if self._textures is None:
            self._textures = (None, None, False)
            if self.model is not None and self.sluggie_path:
                try:
                    self._textures = ex._resolve_export_texture_context(self.sluggie_path, self.model)
                except ValueError:
                    pass
        return self._textures


# ---------------------------------------------------------------------------
# Scope gathering
# ---------------------------------------------------------------------------

def is_sluggies_armature(obj):
    """Armatures the .sluggie importer made (its markers on the object or bones)."""
    if obj is None or obj.type != 'ARMATURE':
        return False
    if SluggiePath.PATH_PROP in obj or 'SluggiesBoneMetadataVersion' in obj:
        return True
    return any(b.get('SluggiesGeoIdRaw') is not None for b in obj.data.bones)


def _parent_armature(obj):
    node = obj.parent
    while node is not None:
        if node.type == 'ARMATURE':
            return node
        node = node.parent
    return None


def _modifier_armatures(obj):
    return [m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object is not None]


def _load_model(path, cache):
    if path not in cache:
        try:
            cache[path] = (FieldCodec.load_sluggie(path)["SluggiesModel"], "")
        except FileNotFoundError:
            cache[path] = (None, "missing")
        except (OSError, ValueError, KeyError) as exc:
            cache[path] = (None, f"unreadable ({exc})")
    return cache[path]


def gather_scopes(context):
    """One scope per Sluggies armature in the scene (hidden and excluded
    collections included): its child meshes, meshes whose Armature modifier
    targets it, and unparented meshes whose VertexBufferOffset matches a
    submesh of its .sluggie."""
    cache = {}
    scopes = {}
    objects = list(context.scene.objects)
    for obj in objects:
        if is_sluggies_armature(obj):
            path = SluggiePath.resolve(obj)
            if path:
                model, error = _load_model(path, cache)
            else:   # the recorded file is gone and the search found nothing
                path, model, error = SluggiePath.stored(obj), None, "missing" if SluggiePath.stored(obj) else ""
            scopes[obj.name] = ModelScope(armature=obj, meshes=[], model=model,
                                          sluggie_path=path, load_error=error)
    loose = []
    for obj in objects:
        if obj.type != 'MESH':
            continue
        owner = _parent_armature(obj)
        if owner is None or owner.name not in scopes:
            owner = next((a for a in _modifier_armatures(obj) if a.name in scopes), None)
        if owner is not None:
            scopes[owner.name].meshes.append(obj)
        elif all(prop in obj for prop in core.REQUIRED_PROPS):
            loose.append(obj)
    for obj in loose:
        matches = [s for s in scopes.values() if s.model is not None
                   and core.submesh_index_by_offset(s.model, obj["VertexBufferOffset"]) is not None]
        if len(matches) == 1:
            matches[0].unparented.append(obj)
    return list(scopes.values())


def _sluggies_meshes(objects):
    return [
        obj for obj in objects
        if obj.type == 'MESH' and (obj.get('SluggiesCustomSubmesh')
                                   or all(prop in obj for prop in core.REQUIRED_PROPS))
    ]


def selection_armature(objects):
    """The Sluggies armature *objects* (a selection) name: the parent or
    Armature modifier target of a selected Sluggies mesh, else a selected
    armature. Only one the selection itself names: a scene-wide fallback
    could pick another model's armature (High and Low in one scene). Its
    ``SluggieFilePath`` is the exporter's source file."""
    objects = list(objects)
    for obj in _sluggies_meshes(objects):
        candidates = [_parent_armature(obj)] + _modifier_armatures(obj)
        armature = next((a for a in candidates if is_sluggies_armature(a)), None)
        if armature is not None:
            return armature
    return next((o for o in objects if is_sluggies_armature(o)), None)


def export_scope(context, model, filepath, objects, reimport_textures):
    """The exporter's scope: the armature the exporter itself uses and the
    selected meshes it exports."""
    meshes = _sluggies_meshes(objects)
    return ModelScope(
        armature=selection_armature(objects), meshes=meshes, model=model,
        sluggie_path=filepath, for_export=True, reimport_textures=reimport_textures,
    )


# ---------------------------------------------------------------------------
# Reading helpers
# ---------------------------------------------------------------------------

def _is_custom(obj):
    return bool(obj.get('SluggiesCustomSubmesh'))


def _submesh_index(scope, obj):
    if scope.model is None or _is_custom(obj) or "VertexBufferOffset" not in obj:
        return None
    return core.submesh_index_by_offset(scope.model, obj["VertexBufferOffset"])


def _added_bones(arm):
    return [b for b in arm.data.bones if b.get('SluggiesUserAdded')] if arm is not None else []


def _materials(obj):
    seen = []
    for slot in obj.material_slots:
        if slot.material is not None and slot.material not in seen:
            seen.append(slot.material)
    return seen


def _image_path(image):
    path = getattr(image, "filepath_raw", "") or getattr(image, "filepath", "")
    return bpy.path.abspath(path) if path else ""


def _same_dir(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _same_file_bytes(a, b):
    try:
        with open(a, 'rb') as fa, open(b, 'rb') as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _used_materials(obj):
    used = {poly.material_index for poly in obj.data.polygons}
    return {slot.material for i, slot in enumerate(obj.material_slots)
            if i in used and slot.material is not None}


def _used_vertices(mesh):
    return {v for poly in mesh.polygons for v in poly.vertices}


# ---------------------------------------------------------------------------
# Checks: model level (object_name = the armature)
# ---------------------------------------------------------------------------

def check_edit_mode(context, scope):
    for obj in [scope.armature] + scope.meshes:
        if obj is not None and obj.mode == 'EDIT':
            yield Finding("edit_mode", ERROR, obj.name, "In Edit Mode",
                          "Export is not possible in Edit Mode. Switch to Object Mode (Tab) and "
                          "export again.", "object_mode", {})


def check_sluggie_file(context, scope):
    if scope.for_export or scope.armature is None:
        return
    if not scope.sluggie_path:
        yield Finding("sluggie_path", WARNING, scope.name, "No source .sluggie",
                      "The armature does not record the .sluggie it was imported from, so most "
                      "checks and Add New Submesh/Add material can't run. "
                      + SluggiePath.LOCATE_HINT + ", or re-import the model.")
    elif scope.load_error:
        yield Finding("sluggie_path", ERROR, scope.name, "Source .sluggie " + scope.load_error.split(' ')[0],
                      f"The armature's source file {scope.sluggie_path} is {scope.load_error}. "
                      "Restore the file, " + SluggiePath.LOCATE_HINT[0].lower() + SluggiePath.LOCATE_HINT[1:]
                      + ", or re-import the model.")


def check_bone_metadata(context, scope):
    arm = scope.armature
    if arm is None or HostBones.bone_metadata_is_current(arm.get('SluggiesBoneMetadataVersion')):
        return
    blocking = any(_is_custom(o) for o in scope.meshes) or bool(_added_bones(arm))
    yield Finding("bone_metadata", ERROR if blocking else WARNING, arm.name, "Old import: re-import",
                  f"{arm.name}: armature has no current bone metadata (imported with an older "
                  f"add-on). {HostBones.RE_IMPORT_MESSAGE}.")


def check_wrong_model(context, scope):
    arm = scope.armature
    if arm is None or scope.model is None:
        return
    if not HostBones.bone_metadata_is_current(arm.get('SluggiesBoneMetadataVersion')):
        return
    records = ex._armature_donor_bone_records(arm)
    present = {r.bone_id for r in records}
    sluggie = [r for r in HostBones.bone_records_from_hierarchy(
        scope.model.get("BoneHierarchy") or [], scope.model.get("SkinData")) if r.bone_id in present]
    mismatches = HostBones.bone_metadata_mismatches(records, sluggie)
    if mismatches:
        shown = "; ".join(mismatches[:5])
        yield Finding("wrong_model", ERROR, arm.name, "Armature from other model",
                      f"{arm.name}: armature does not match the target .sluggie ({shown}). It was "
                      "imported from a different model; export to the .sluggie it came from.")


def check_added_bone_names(context, scope):
    arm = scope.armature
    if arm is None:
        return
    bones = arm.data.bones
    donor_count = sum(1 for b in bones if not b.get('SluggiesUserAdded'))
    renames = HostBones.added_bone_renames(donor_count, [
        (b.name, int(b.get('SluggiesCreationOrder', 0))) for b in bones if b.get('SluggiesUserAdded')])
    message = HostBones.added_bone_name_error(renames)
    if message:
        yield Finding("bone_names", ERROR, arm.name, "Added bones need renumbering", message,
                      "renumber_bones", {"armature": arm.name})


def check_donor_bones(context, scope):
    arm = scope.armature
    if arm is None or scope.model is None or scope.for_export:
        return
    bones = [core.BoneInfo(b.name, b.parent.name if b.parent else None, bool(b.get('SluggiesUserAdded')))
             for b in arm.data.bones]
    problems = core.donor_bone_problems(bones, scope.model.get("BoneHierarchy") or [])
    texts = {
        "renamed": ("Original bone renamed", "Original bones renamed",
                    "keep their bone_<id> names: vertex groups follow the bone name, so weights on "
                    "a renamed bone are ignored on export. Rename them back"),
        "missing": ("Original bone deleted", "Original bones deleted",
                    "are missing from the armature. Export keeps them (they come from the "
                    ".sluggie), but weights on them and their animation are lost in Blender. "
                    "Re-import to get them back"),
        "reparented": ("Original bone re-parented", "Original bones re-parented",
                       "have another parent in Blender than in the game. Export ignores the "
                       "change; the game keeps the original hierarchy"),
        "orphan": ("Added bone without parent", "Added bones without parent",
                   "have no parent bone and are not exported. Parent them to a bone"),
    }
    for kind, names in problems.items():
        if names:
            one, many, why = texts[kind]
            yield Finding(f"donor_bones_{kind}", WARNING, arm.name, one if len(names) == 1 else many,
                          f"{', '.join(names[:8])}{' ...' if len(names) > 8 else ''}: these bones {why}.")


def check_stadium_bones(context, scope):
    arm = scope.armature
    added = [b.name for b in _added_bones(arm)]
    if added and scope.stadium:
        yield Finding("stadium_bones", ERROR, arm.name, "Stadium can't take new bones",
                      f"{arm.name}: added bone(s) {', '.join(added)}: stadiums cannot take new bones, "
                      "a mesh on a new bone does not draw in a stadium (Dolphin, 2026-10-02). Delete "
                      "the added bones and host custom submeshes on free vanilla root bones.")


def check_untracked_keys(context, scope):
    arm = scope.armature
    if arm is None or scope.for_export or not _added_bones(arm):
        return
    actions = [a for group in AnmExport.anm_groups().values() for a in group]
    if arm.animation_data is not None and arm.animation_data.action not in actions:
        actions.append(arm.animation_data.action)
    names = AnmExport.untracked_keyed_bones(arm, actions)
    if names:
        yield Finding("untracked_keys", WARNING, arm.name,
                      f"{core.plural(len(names), 'keyed bone')} without track",
                      f"{', '.join(names)}: {HostBones.UNTRACKED_KEYS_MESSAGE}",
                      "bone_track", {"armature": arm.name, "bones": names})


def check_root_scale(context, scope):
    arm = scope.armature
    if arm is None or 'RootBoneScale' not in arm:
        return
    if not core.root_bone_scale_ok(arm['RootBoneScale']):
        yield Finding("root_scale", WARNING, arm.name, "Root bone scale invalid",
                      f"{arm.name}: RootBoneScale must be three numbers; it is ignored on export.",
                      "reset_root_scale", {"armature": arm.name})


def check_texture_reimport_hint(context, scope):
    if scope.for_export or scope.model is None:
        return
    # New PNGs are added on every export; only edits to the model's own
    # textures need the toggle. A changed size is the edit that shows.
    _descriptors, tex_dir, owns = scope.texture_context()
    if not tex_dir or not owns:
        return
    resized = ExportMode.texture_size_changes(scope.model.get("TextureDescriptors") or [], tex_dir)
    if resized:
        yield Finding("reimport_hint", INFO, scope.name, "Export with Reimport textures",
                      "Original textures with a new size: " + ", ".join(sorted(resized)[:8])
                      + ". Enable 'Reimport textures from tex folder' when exporting to write them.")


# ---------------------------------------------------------------------------
# Checks: mesh level
# ---------------------------------------------------------------------------

def check_mesh_props(context, scope):
    if scope.for_export or scope.model is None:
        return
    for obj in scope.meshes:
        if _is_custom(obj):
            continue
        if not all(prop in obj for prop in core.REQUIRED_PROPS):
            surfaces = {core.donor_surface_submesh(m.get("SurfaceId")) for m in _materials(obj)}
            surfaces.discard(None)
            index = core.match_submesh(obj.name, len(obj.data.vertices), surfaces, scope.model)
            if index is None:
                yield Finding("mesh_props", WARNING, obj.name, "Not linked to a submesh",
                              f"{obj.name} lacks the VertexBuffer* properties that tie it to a "
                              "submesh of the .sluggie, and no submesh matches it, so it is not "
                              "exported. Re-import the model, or make new parts with Add New Submesh.")
            else:
                yield Finding("mesh_props", WARNING, obj.name, "Submesh link lost",
                              f"{obj.name} lacks the VertexBuffer* properties that tie it to "
                              f"submesh {index}, so it is not exported. The fix restores them "
                              "from the .sluggie.",
                              "restore_mesh_props", {"object": obj.name, "sluggie": scope.sluggie_path,
                                                     "index": index})
        elif _submesh_index(scope, obj) is None:
            yield Finding("mesh_props", WARNING, obj.name, "Not in this .sluggie",
                          f"{obj.name}: no submesh with VertexBufferOffset={obj['VertexBufferOffset']} "
                          f"in {os.path.basename(scope.sluggie_path)}; it is skipped on export.")


def check_duplicate_claims(context, scope):
    by_index = {}
    for obj in scope.meshes:
        index = _submesh_index(scope, obj)
        if index is not None:
            by_index.setdefault(index, []).append(obj)
    for index, objs in by_index.items():
        if len(objs) < 2:
            continue
        names = ", ".join(o.name for o in objs)
        if scope.for_export:
            yield Finding("duplicate_claim", ERROR, objs[1].name, "Same submesh twice",
                          f"{objs[0].name} and {objs[1].name} both belong to submesh {index}. Select "
                          "only one of them and export again.")
        else:
            yield Finding("duplicate_claim", INFO, objs[1].name, f"Same submesh as {objs[0].name}",
                          f"{names} all belong to submesh {index}; export only one of them.")
    by_custom = {}
    for obj in scope.meshes:
        if _is_custom(obj):
            by_custom.setdefault(str(obj.get('CustomSubmeshId')), []).append(obj)
    for custom_id, objs in by_custom.items():
        if len(objs) > 1:
            yield Finding("duplicate_custom", ERROR, objs[1].name, "Duplicated custom submesh",
                          f"{', '.join(o.name for o in objs)} share CustomSubmeshId {custom_id} "
                          "(copied with Shift+D?). Make each new part with Add New Submesh.")


def check_armature_link(context, scope):
    arm = scope.armature
    for obj in scope.meshes:
        targets = _modifier_armatures(obj)
        follows = ex._custom_submesh_armature(obj)
        if follows is None and _is_custom(obj):
            yield Finding("armature_link", ERROR, obj.name, "No Armature modifier",
                          f"{obj.name}: custom submesh has no Armature modifier or armature parent; "
                          "it can't be placed relative to its host bone.",
                          "armature_modifier" if arm is not None else None,
                          {"object": obj.name, "armature": arm.name} if arm is not None else {})
        elif arm is not None and not scope.for_export and arm not in targets:
            text = "Modifier on other armature" if targets else "No Armature modifier"
            yield Finding("armature_link", WARNING, obj.name, text,
                          f"{obj.name} has no Armature modifier on {arm.name}, so it does not follow "
                          "the bones in Blender (and a rebuilt mesh can't be placed).",
                          "armature_modifier", {"object": obj.name, "armature": arm.name})
    if arm is None or scope.for_export:
        return
    for obj in scope.meshes + scope.unparented:
        if _parent_armature(obj) is not arm:
            yield Finding("parent", WARNING, obj.name, "Not parented to armature",
                          f"{obj.name} belongs to {arm.name} but is not its child. The fix parents "
                          "it to the armature and keeps its world position.",
                          "reparent", {"object": obj.name, "armature": arm.name})


def check_modifiers(context, scope):
    if scope.for_export:
        return
    for obj in scope.meshes:
        names = CustomSubmeshExport.unsupported_modifier_names((m.name, m.type) for m in obj.modifiers)
        if names:
            text = f"{names[0]} modifier not applied" if len(names) == 1 else \
                f"{len(names)} modifiers not applied"
            yield Finding("modifiers", WARNING, obj.name, text,
                          f"{obj.name}: modifier(s) {', '.join(names)} are not evaluated on export; "
                          "apply them first or their geometry is missing in game.")


def check_geometry(context, scope):
    for obj in scope.meshes:
        mesh = obj.data
        scale = obj.matrix_world.to_scale()
        if any(abs(v) < 1e-8 for v in scale):
            yield Finding("zero_scale", ERROR, obj.name, "Scaled to zero",
                          f"{obj.name} is scaled to zero on an axis; its geometry can't be exported. "
                          "Fix the object's scale.")
        coords = [0.0] * (len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", coords)
        if not all(math.isfinite(c) for c in coords):
            yield Finding("bad_coords", ERROR, obj.name, "Invalid vertex positions",
                          f"{obj.name} has vertices with invalid (NaN or infinite) positions.")
        if not mesh.polygons:
            index = _submesh_index(scope, obj)
            donor_faces = 0
            if index is not None:
                donor_faces = int(scope.model["Submeshes"][index].get("FacesCount") or 0)
            # A donor submesh that never had faces exports fine as it is.
            if _is_custom(obj) or donor_faces:
                yield Finding("no_faces", ERROR, obj.name, "No faces",
                              f"{obj.name}: mesh has no faces.")


def check_custom_host_bone(context, scope):
    for obj in scope.meshes:
        if not _is_custom(obj):
            continue
        ok, majority = core.host_bone_vote(ex._rigid_vertex_bone_ids(obj))
        if not ok:
            yield Finding("one_bone", ERROR, obj.name, "Not on one bone",
                          f"{obj.name}: every vertex must belong to exactly one bone_<id> vertex "
                          "group (the host bone), all the same bone."
                          + (f" The fix puts every vertex on bone_{majority}." if majority is not None else ""),
                          "one_bone" if majority is not None else None,
                          {"object": obj.name, "bone_id": majority} if majority is not None else {})


def check_body_weights(context, scope):
    model = scope.model
    if model is None or not model.get("SkinData"):
        return
    arm = scope.armature
    for obj in scope.meshes:
        index = _submesh_index(scope, obj)
        if index != 0 or obj.get("VertexBufferCompCount") != 6:
            continue
        weights = ex._vertex_bone_weights(obj)
        hierarchy = model.get("BoneHierarchy") or []
        parent_of = {int(b["BoneId"]): b.get("ParentBoneId") for b in hierarchy if "BoneId" in b}
        added = set()
        for bone in _added_bones(arm):
            bone_id = core.bone_id_from_name(bone.name)
            if bone_id is not None:
                added.add(bone_id)
                parent_of[bone_id] = core.bone_id_from_name(bone.parent.name) if bone.parent else None
        allowed = core.allowed_skin_bones(HostBones.skn_bone_ids(model.get("SkinData")), added)
        donor_count = core.vertex_buffer_vertex_count(model["Submeshes"][0].get("VertexBuffer") or {})
        covered = core.donor_covered_vertices(hierarchy, 0) if donor_count == len(obj.data.vertices) else None
        unweighted = core.unweighted_vertices(weights, _used_vertices(obj.data), allowed, parent_of, covered)
        if unweighted:
            yield Finding("unweighted", ERROR, obj.name, f"{core.plural(len(unweighted), 'vert')} unweighted",
                          f"{obj.name}: {len(unweighted)} vertex(es) have no weight on any bone the "
                          "original model skins to or any added bone. Assign them to bone_<id> "
                          "vertex groups of such bones.")
        over = core.overweighted_vertices(weights)
        if over and not scope.for_export:
            yield Finding("overweighted", WARNING, obj.name,
                          f"{core.plural(len(over), 'vert')} on > {core.MAX_BONE_INFLUENCES} bones",
                          f"{obj.name}: {len(over)} vertex(es) are weighted to more than "
                          f"{core.MAX_BONE_INFLUENCES} bones; no model in the game does that. The fix "
                          "keeps the strongest five and normalizes them.",
                          "limit_weights", {"object": obj.name})


def check_materials(context, scope):
    arm = scope.armature
    for obj in scope.meshes:
        index = _submesh_index(scope, obj)
        custom = _is_custom(obj)
        if not custom and index is None:
            continue
        used_slots = {poly.material_index for poly in obj.data.polygons}
        if not scope.for_export:
            empty = [i for i, slot in enumerate(obj.material_slots) if slot.material is None]
            empty_used = [i for i in empty if i in used_slots]
            if empty_used:
                yield Finding("empty_slot", WARNING, obj.name, "Faces on empty slot",
                              f"{obj.name}: faces use a material slot without a material; they keep "
                              "their original surface or are refused. Assign a material.")
            if set(empty) - set(empty_used):
                yield Finding("empty_slot_unused", WARNING, obj.name, "Empty material slot",
                              f"{obj.name} has empty material slots no face uses. The fix removes them.",
                              "remove_slots", {"object": obj.name,
                                               "slots": sorted(set(empty) - set(empty_used))})
        target = scope.model["Submeshes"][index] if index is not None else None
        foreign = ex._find_new_materials(obj, target, index)
        if foreign:
            names = ", ".join(f"'{n}' ({r})" for n, r in foreign)
            detail = (f"{obj.name}: custom submeshes support only their own surface materials ({names})"
                      if custom else
                      f"Error: {obj.name} uses material(s) that are not surfaces of this submesh: "
                      f"{names}. Use its imported materials or Add material, and reassign or remove "
                      "the other faces.")
            plain = [n for n, _r in foreign
                     if not bpy.data.materials[n].get("SurfaceId")
                     and not bpy.data.materials[n].get("SluggiesNewSurface")]
            if scope.for_export or not plain or arm is None or scope.model is None:
                yield Finding("foreign_material", ERROR, obj.name, "Material not of this submesh", detail)
            else:
                yield Finding("plain_material", ERROR, obj.name,
                              "Plain Blender material" if len(plain) == 1 else
                              f"{len(plain)} plain Blender materials",
                              detail + " The fix turns " + ", ".join(plain) + " into new surfaces "
                              f"(as Add material, template {core.default_template(scope.stadium)}).",
                              "material_defaults", {"object": obj.name, "materials": plain,
                                                    "armature": arm.name,
                                                    "template": core.default_template(scope.stadium)})
                if len(plain) < len(foreign):
                    yield Finding("foreign_material", ERROR, obj.name, "Material of other submesh", detail)
        if scope.for_export:
            continue
        missing = []
        for mat in _materials(obj):
            if mat.get("SluggiesNewSurface"):
                if any(prop not in mat for prop in core.NEW_SURFACE_PROPS):
                    missing.append((mat.name, False))
            elif core.donor_surface_submesh(mat.get("SurfaceId")) is not None:
                if any(prop not in mat for prop in core.DONOR_MATERIAL_PROPS):
                    missing.append((mat.name, True))
        fixable = [n for n, ok in missing if ok]
        if fixable and scope.model is not None:
            yield Finding("material_props", WARNING, obj.name, "Material properties missing",
                          f"{', '.join(fixable)}: Sluggies material properties are missing; export "
                          "can't map the texture/shader. The fix restores them from the .sluggie.",
                          "restore_material_props", {"object": obj.name, "materials": fixable,
                                                     "sluggie": scope.sluggie_path})
        broken = [n for n, ok in missing if not ok]
        if broken:
            yield Finding("material_props_new", ERROR, obj.name, "New material broken",
                          f"{', '.join(broken)}: Add-material properties are missing. Delete the "
                          "material and make it again with Add material.")
        sids = {}
        for slot in obj.material_slots:
            sid = slot.material.get("SurfaceId") if slot.material is not None else None
            if sid:
                sids[sid] = sids.get(sid, 0) + 1
        doubled = [sid for sid, n in sids.items() if n > 1]
        if doubled:
            yield Finding("duplicate_surface", WARNING, obj.name, "Surface on two slots",
                          f"{obj.name}: SurfaceId {', '.join(doubled)} is on several material slots; "
                          "only the first slot is used.")
        unused_new = [i for i, slot in enumerate(obj.material_slots)
                      if slot.material is not None and slot.material.get("SluggiesNewSurface")
                      and i not in used_slots]
        if unused_new:
            yield Finding("unused_surface", INFO, obj.name, "Unused new material",
                          f"{obj.name}: new material(s) "
                          f"{', '.join(obj.material_slots[i].material.name for i in unused_new)} are "
                          "used by no face and not exported. The fix removes their slots.",
                          "remove_slots", {"object": obj.name, "slots": unused_new})


def check_custom_uvs(context, scope):
    for obj in scope.meshes:
        if not _is_custom(obj):
            continue
        layers = obj.data.uv_layers
        if not len(layers):
            yield Finding("no_uv", ERROR, obj.name, "No UV map",
                          f"{obj.name}: custom submesh has no UV map; add one and unwrap it.")
        elif len(layers) > 1 and not scope.for_export:
            yield Finding("extra_uv", WARNING, obj.name, "Extra UV maps ignored",
                          f"{obj.name}: only the render-active UV map is exported; other UV maps "
                          "are ignored.")
        if obj.data.shape_keys and not scope.for_export:
            yield Finding("shape_keys", WARNING, obj.name, "Shape keys ignored",
                          f"{obj.name}: shape keys on a custom submesh are not exported.")


def check_textures(context, scope):
    descriptors, tex_dir, _owns = scope.texture_context()
    reserved = [d.get("TextureFileName") for d in descriptors or [] if d.get("TextureFileName")]
    for obj in scope.meshes:
        custom = _is_custom(obj)
        used = _used_materials(obj)
        for mat in _materials(obj):
            new = bool(mat.get("SluggiesNewSurface")) or custom
            if not (mat.get("SurfaceId") or mat.get("SluggiesNewSurface")):
                continue
            nodes = ex._connected_image_texture_nodes(mat)
            if any(getattr(node, "image", None) is None for node in nodes):
                yield Finding("empty_node", ERROR, obj.name, "Texture node empty",
                              f"'{mat.name}' on {obj.name} has an Image Texture node without an image; "
                              "load a PNG into it (or remove the node) before exporting")
                continue
            if not nodes:
                if new:
                    yield Finding("empty_node", ERROR, obj.name, "No texture image",
                                  f"'{mat.name}' on {obj.name} has no image in its texture node; "
                                  "load a PNG before exporting")
                continue
            if len(nodes) > 1:
                yield Finding("multi_image", ERROR, obj.name, "Several textures in material",
                              f"Multiple textures in one material are not supported: {mat.name}")
                continue
            # A texture problem blocks the export when the export reads the
            # PNG: a new PNG always, the model's own textures only with
            # Reimport textures.
            gate = scope.reimport_textures or new \
                or os.path.basename(_image_path(nodes[0].image)) not in reserved
            primary = custom and str(mat.get("SurfaceId") or "").startswith(f"{obj.get('CustomSubmeshId')}_ds")
            if scope.for_export and (not gate or (new and mat not in used and not primary)):
                # Export resolves only the new surfaces faces use.
                continue
            yield from _image_findings(scope, obj, mat, nodes[0].image, new, tex_dir, reserved)


def _image_findings(scope, obj, mat, image, new, tex_dir, reserved):
    path = _image_path(image)
    fix_tex = {"image": image.name, "tex_dir": tex_dir} if tex_dir else {}
    if not path:
        yield Finding("image_path", ERROR, obj.name, "Image not saved",
                      f"Material image has no file path: {mat.name}. The fix saves it as a PNG "
                      "into the model's tex/ folder.", "image_to_tex" if tex_dir else None, fix_tex)
        return
    if image.is_dirty and not scope.for_export:
        yield Finding("unsaved_image", WARNING, obj.name, "Unsaved image",
                      f"'{image.name}' ({mat.name}) was edited in Blender but not saved; export reads "
                      "the PNG from disk, so the edit is lost. The fix saves it.",
                      "save_image", {"image": image.name})
    if not path.lower().endswith('.png'):
        yield Finding("not_png", ERROR, obj.name, "Image not a PNG",
                      f"Material '{mat.name}' image must be a PNG: {os.path.basename(path)}. The fix "
                      "saves it as a PNG into tex/.", "image_to_tex" if tex_dir else None, fix_tex)
        return
    if not os.path.isfile(path):
        packed = image.packed_file is not None
        yield Finding("image_missing", ERROR, obj.name, "PNG file missing",
                      f"'{mat.name}': the PNG {path} does not exist."
                      + (" It is packed into the .blend; the fix writes it into tex/." if packed else ""),
                      "image_to_tex" if packed and tex_dir else None, fix_tex if packed else {})
        return
    if tex_dir and not new and not _same_dir(os.path.dirname(path), tex_dir) \
            and not _same_file_bytes(path, os.path.join(tex_dir, os.path.basename(path))):
        # The export reads tex/<name>: a PNG elsewhere is only lost when
        # tex/ lacks it or holds other bytes.
        yield Finding("outside_tex", ERROR, obj.name, "PNG outside tex/",
                      f"Texture PNG for material '{mat.name}' must exist in the resolved tex folder: "
                      f"{os.path.join(tex_dir, os.path.basename(path))}. The fix copies {path} there "
                      "(a free name if taken) and points the image at the copy.",
                      "copy_to_tex", {**fix_tex, "reserved": reserved})
    size = ExportMode.png_dimensions(path)
    if size and max(size) > core.GX_MAX_TEXTURE_DIMENSION and not scope.for_export:
        yield Finding("big_texture", WARNING, obj.name, f"Texture over {core.GX_MAX_TEXTURE_DIMENSION} px",
                      f"{os.path.basename(path)} is {size[0]}x{size[1]}; the patcher scales it down to "
                      f"fit {core.GX_MAX_TEXTURE_DIMENSION}x{core.GX_MAX_TEXTURE_DIMENSION} (GX limit).")


# (function, run on export). Export-run checks report their ERROR findings
# only; checks that make no sense on a selection skip themselves there.
CHECKS = [
    (check_edit_mode, True),
    (check_sluggie_file, False),
    (check_bone_metadata, True),
    (check_wrong_model, True),
    (check_added_bone_names, True),
    (check_donor_bones, False),
    (check_stadium_bones, True),
    (check_untracked_keys, False),
    (check_root_scale, False),
    (check_mesh_props, False),
    (check_duplicate_claims, True),
    (check_armature_link, True),
    (check_modifiers, False),
    (check_geometry, True),
    (check_custom_host_bone, True),
    (check_body_weights, True),
    (check_materials, True),
    (check_custom_uvs, True),
    (check_textures, True),
    (check_texture_reimport_hint, False),
]


def check_low_high_bones(scopes):
    """A Low model's added bones that its High model in the scene lacks:
    the patcher refuses a Low model drawing on bones its High model does not
    have."""
    for low in scopes:
        if low.armature is None or not core.is_low_folder(low.folder):
            continue
        used = set()
        for obj in low.meshes:
            used.update(vg.name for vg in obj.vertex_groups)
        low_added = {b.name for b in _added_bones(low.armature)} & used
        highs = [high for high in scopes
                 if high.armature is not None and core.low_partner_matches(low.folder, high.folder)]
        # Several variants of the High model loaded: only the one that pairs
        # with this Low file (all of them when the rule cannot tell).
        partner = core.pick_partner([high.sluggie_path for high in highs], low.sluggie_path)
        if partner is not None and len(highs) > 1:
            highs = [high for high in highs if high.sluggie_path == partner][:1]
        for high in highs:
            missing = sorted(low_added - {b.name for b in _added_bones(high.armature)})
            if missing:
                yield Finding("low_high_bones", WARNING, low.name, "Added bones missing on High",
                              f"{', '.join(missing)}: the Low model uses these added bones but its "
                              f"High model {high.name} lacks them; the patcher refuses that pair. "
                              "Add the same bones to the High model.")


def run_checks(context, scopes):
    findings = []
    for scope in scopes:
        for check, _export in CHECKS:
            findings.extend(check(context, scope))
    findings.extend(check_low_high_bones(scopes))
    return core.sort_findings(findings)


def export_errors(context, model, filepath, objects, reimport_textures):
    """Error messages of the checks the exporter runs on the selected model."""
    scope = export_scope(context, model, filepath, objects, reimport_textures)
    messages = []
    for check, on_export in CHECKS:
        if on_export:
            messages.extend(f.detail for f in check(context, scope) if f.severity == ERROR)
    return messages


# ---------------------------------------------------------------------------
# Size estimate
# ---------------------------------------------------------------------------

def _triangle_count(mesh):
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


def _custom_mesh_counts(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    return core.MeshCounts(
        vertices=len(mesh.vertices), triangles=len(mesh.loop_triangles), uv_channels=2,
        color_channels=1 if len(mesh.color_attributes) else 0, has_normal_buffer=True,
        comp_count=3, comp_size=2,
    )


def estimate_scope_size(scope, objects=None):
    """(model bytes, texture bytes) of *scope*'s model as Blender holds it
    (or of *objects* only, the export selection), or None without a .sluggie."""
    model = scope.model
    if model is None:
        return None
    objects = scope.meshes if objects is None else objects
    mesh_counts, custom = {}, 0
    for obj in objects:
        if _is_custom(obj):
            custom += core.mesh_bytes(_custom_mesh_counts(obj))
            continue
        index = _submesh_index(scope, obj)
        if index is None:
            continue
        # The original and its `_edit` copy (or backups) both claim the
        # submesh: an `_edit` wins, else the first one.
        if index in mesh_counts and not obj.name.split('.')[0].endswith('_edit'):
            continue
        mesh_counts[index] = (len(obj.data.vertices), _triangle_count(obj.data))
    descriptors, tex_dir, owns = scope.texture_context()
    texture_sizes, new_textures = {}, []
    if owns and tex_dir:
        names = {d.get("TextureFileName") for d in descriptors or []}
        for name in names:
            size = ExportMode.png_dimensions(os.path.join(tex_dir, name or ""))
            if size:
                texture_sizes[name] = size
        seen = set()
        for obj in objects:
            for mat in _materials(obj):
                for node in ex._connected_image_texture_nodes(mat):
                    path = _image_path(node.image) if node.image is not None else ""
                    file_name = os.path.basename(path)
                    if not path or file_name in names or file_name in seen:
                        continue
                    size = ExportMode.png_dimensions(path)
                    if size:
                        seen.add(file_name)
                        template = mat.get("TemplateTextureIndex", mat.get("TextureIndex", -1))
                        new_textures.append((size[0], size[1], int(template)))
    return core.estimate_size(model if owns else {**model, "TextureDescriptors": []},
                              mesh_counts, custom, texture_sizes, new_textures)


def export_size_line(context, model, filepath, objects):
    scope = export_scope(context, model, filepath, objects, True)
    sizes = estimate_scope_size(scope, scope.meshes)
    return core.size_line(*sizes) if sizes else None


# ---------------------------------------------------------------------------
# Fixes: fix_id -> function(context, **fix_args); raise ValueError on failure
# ---------------------------------------------------------------------------

def _object(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"object {name} is gone")
    return obj


def _image(name):
    image = bpy.data.images.get(name)
    if image is None:
        raise ValueError(f"image {name} is gone")
    return image


_FIX_MODELS = {}


def _fix_model(path):
    model, error = _load_model(path, _FIX_MODELS)
    if model is None:
        raise ValueError(f".sluggie {error or 'missing'}")
    return model


def fix_object_mode(context):
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')


def fix_reparent(context, object, armature):
    obj, arm = _object(object), _object(armature)
    world = obj.matrix_world.copy()
    obj.parent = arm
    obj.parent_type = 'OBJECT'
    obj.matrix_world = world


def fix_armature_modifier(context, object, armature):
    obj, arm = _object(object), _object(armature)
    mod = next((m for m in obj.modifiers if m.type == 'ARMATURE'), None)
    if mod is None:
        mod = obj.modifiers.new(name="Armature", type='ARMATURE')
    mod.object = arm


def fix_restore_mesh_props(context, object, sluggie, index):
    obj = _object(object)
    for prop, value in core.mesh_props_for_submesh(_fix_model(sluggie), index).items():
        if value is None:
            raise ValueError(f"submesh {index} has no {prop}")
        obj[prop] = value


def fix_restore_material_props(context, object, materials, sluggie):
    model = _fix_model(sluggie)
    for name in materials:
        mat = bpy.data.materials.get(name)
        if mat is None:
            raise ValueError(f"material {name} is gone")
        sid = mat.get("SurfaceId")
        index = core.donor_surface_submesh(sid)
        ds = core.display_state_for_surface(model, sid)
        if ds is None or index is None:
            raise ValueError(f"{name}: surface {sid} not in the .sluggie")
        if "TextureIndex" not in mat:
            mat["TextureIndex"] = ex._custom_submesh_template_texture_index(model, f"rigid:{sid}")
        wrap_s, wrap_t = core.wrap_modes_for_texture(model, index, mat["TextureIndex"])
        if "WrapS" not in mat:
            mat["WrapS"] = wrap_s
        if "WrapT" not in mat:
            mat["WrapT"] = wrap_t
        if "DisplayStateId" not in mat or "ShaderMode" not in mat:
            kept = {k: mat[k] for k in ("ShaderMode", "SpecularStrength") if k in mat}
            _set_surface_material_metadata(mat, ds)
            for key, value in kept.items():
                mat[key] = value


def fix_material_defaults(context, object, materials, armature, template):
    obj, arm = _object(object), _object(armature)
    for name in materials:
        mat = bpy.data.materials.get(name)
        if mat is None:
            raise ValueError(f"material {name} is gone")
        plan = panel._new_surface_plan(context, arm, obj, template)
        if mat.users > 1:
            # Shared with another object: that one keeps the plain material.
            copy = mat.copy()
            for slot in obj.material_slots:
                if slot.material == mat:
                    slot.material = copy
            mat = copy
        panel._apply_new_surface_plan(mat, plan)


def fix_save_image(context, image):
    img = _image(image)
    if not _image_path(img):
        raise ValueError(f"{image} has no file path")
    img.save()


def _safe_stem(name):
    stem = os.path.splitext(name)[0]
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in stem) or "texture"


def fix_image_to_tex(context, image, tex_dir):
    """Save an image without a usable PNG file (generated, packed, not a
    PNG) as a PNG into tex/ and point it there."""
    img = _image(image)
    os.makedirs(tex_dir, exist_ok=True)
    file_name = core.free_file_name(_safe_stem(img.name) + ".png", os.listdir(tex_dir))
    path = os.path.join(tex_dir, file_name)
    img.filepath_raw = path
    img.file_format = 'PNG'
    if img.packed_file is not None:
        img.unpack(method='WRITE_ORIGINAL')
        if not os.path.isfile(path):
            img.save()
    else:
        img.save()


def fix_copy_to_tex(context, image, tex_dir, reserved=()):
    img = _image(image)
    source = _image_path(img)
    if not os.path.isfile(source):
        raise ValueError(f"{source} not found")
    os.makedirs(tex_dir, exist_ok=True)
    file_name = ex._plan_external_texture_copy(source, tex_dir, {}, tuple(reserved))
    target = os.path.join(tex_dir, file_name)
    if not os.path.isfile(target):
        shutil.copyfile(source, target)
    img.filepath = target


def fix_one_bone(context, object, bone_id):
    obj = _object(object)
    for vg in list(obj.vertex_groups):
        if core.bone_id_from_name(vg.name) is not None:
            obj.vertex_groups.remove(vg)
    group = obj.vertex_groups.new(name=f"bone_{bone_id}")
    group.add(list(range(len(obj.data.vertices))), 1.0, 'REPLACE')


def fix_limit_weights(context, object):
    obj = _object(object)
    bone_groups = {g.index: g for g in obj.vertex_groups if core.bone_id_from_name(g.name) is not None}
    for vertex in obj.data.vertices:
        weights = {e.group: e.weight for e in vertex.groups if e.group in bone_groups and e.weight > 0}
        if len(weights) <= core.MAX_BONE_INFLUENCES:
            continue
        kept = core.limited_weights(weights)
        for group_index in weights:
            if group_index in kept:
                bone_groups[group_index].add([vertex.index], kept[group_index], 'REPLACE')
            else:
                bone_groups[group_index].remove([vertex.index])


def fix_remove_slots(context, object, slots):
    obj = _object(object)
    for index in sorted(set(slots), reverse=True):
        if index >= len(obj.material_slots):
            continue
        obj.active_material_index = index
        with context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.material_slot_remove()


def fix_renumber_bones(context, armature):
    panel._renumber_added_bones(_object(armature))


def fix_reset_root_scale(context, armature):
    _object(armature)['RootBoneScale'] = [1.0, 1.0, 1.0]


def fix_bone_track(context, armature, bones):
    arm = _object(armature)
    for name in bones:
        bone = arm.data.bones.get(name)
        if bone is None:
            raise ValueError(f"bone {name} is gone")
        panel._set_added_bone_track(arm, bone, True)


FIXES = {
    "object_mode": fix_object_mode,
    "reparent": fix_reparent,
    "armature_modifier": fix_armature_modifier,
    "restore_mesh_props": fix_restore_mesh_props,
    "restore_material_props": fix_restore_material_props,
    "material_defaults": fix_material_defaults,
    "save_image": fix_save_image,
    "image_to_tex": fix_image_to_tex,
    "copy_to_tex": fix_copy_to_tex,
    "one_bone": fix_one_bone,
    "limit_weights": fix_limit_weights,
    "remove_slots": fix_remove_slots,
    "renumber_bones": fix_renumber_bones,
    "reset_root_scale": fix_reset_root_scale,
    "bone_track": fix_bone_track,
}
# Fixes affect each other (a mode switch first, renaming bones last), so
# they run in this order whatever order the findings were ticked in.
FIX_ORDER = list(FIXES)


def run_fixes(context, requests):
    """Run ``(fix_id, fix_args)`` pairs in FIX_ORDER; one call per distinct
    pair. Returns (requests fixed, requests failed, failure messages)."""
    _FIX_MODELS.clear()
    calls = {}
    for fix_id, args in requests:
        calls.setdefault((fix_id, json.dumps(args, sort_keys=True)), []).append(args)
    fixed, failed, messages = 0, 0, []
    for (fix_id, _key), group in sorted(calls.items(), key=lambda item: FIX_ORDER.index(item[0][0])):
        try:
            FIXES[fix_id](context, **group[0])
        except Exception as exc:  # each fix reports its own failure, the run goes on
            failed += len(group)
            messages.append(f"{fix_id.replace('_', ' ')}: {exc}")
        else:
            fixed += len(group)
    return fixed, failed, messages
