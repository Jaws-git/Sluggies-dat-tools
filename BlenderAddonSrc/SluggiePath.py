"""Where an imported model's ``.sluggie`` file is.

The importer records the file on the armature (``SluggieFilePath``, absolute,
and ``SluggieFileRel``, relative to the .blend file when it is saved). Every
reader goes through :func:`resolve`, which finds the file again when the
project folder moved: the relative path, then the file's name next to the
export target, in the recorded folder, in the .blend's folder and in their
``2_Output_Assets`` tree (``ModelCheckCore.find_sluggie_file``). A hit is
written back, so the other tools heal with it. ``Locate source .sluggie``
(:class:`SLUGGIES_OT_locate_sluggie`) relinks by hand when nothing resolves.

Imports only ``FieldCodec`` and ``ModelCheckCore``: ``ModelCheck``, the
panel and the exporter import each other, and all of them import this.
"""

import os

import bpy
from bpy.app.handlers import persistent
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from . import FieldCodec
from . import ModelCheckCore

PATH_PROP = 'SluggieFilePath'
REL_PROP = 'SluggieFileRel'
CHUNK_PROP = 'SluggiesChunkNumber'

LOCATE_HINT = "Use 'Locate source .sluggie' in the Sluggies panel to point the model at its file"

# stored path -> path found for it, so a missing file is searched once per
# session, not on every redraw or export step.
_FOUND = {}


def stored(arm_obj):
    """The recorded absolute path ('' when none)."""
    return str(arm_obj.get(PATH_PROP) or '') if arm_obj is not None else ''


def _blend_dir():
    path = bpy.data.filepath
    return os.path.dirname(os.path.abspath(path)) if path else ''


def _relative_to_blend(path, blend_dir=None):
    blend_dir = _blend_dir() if blend_dir is None else blend_dir
    if not blend_dir:
        return ''
    try:
        if os.path.splitdrive(path)[0].lower() != os.path.splitdrive(blend_dir)[0].lower():
            return ''
        return os.path.relpath(path, blend_dir)
    except ValueError:
        return ''


def remember(arm_obj, path, blend_dir=None):
    """Record *path* as the armature's ``.sluggie`` (absolute, and relative
    to the saved .blend when there is one; *blend_dir* overrides it)."""
    path = os.path.abspath(path)
    arm_obj[PATH_PROP] = path
    rel = _relative_to_blend(path, blend_dir)
    if rel:
        arm_obj[REL_PROP] = rel
    elif REL_PROP in arm_obj.keys():
        del arm_obj[REL_PROP]
    _FOUND.pop(os.path.normcase(path), None)


def chunk_matches(path, chunk):
    """Whether the ``.sluggie`` at *path* is of model directory *chunk*
    (True when the file does not say)."""
    try:
        file_chunk = FieldCodec.load_sluggie(path).get('SluggiesModel', {}).get('ChunkNumber')
    except (OSError, ValueError, KeyError, AttributeError):
        return False
    return file_chunk is None or int(file_chunk) == int(chunk)


def resolve(arm_obj, hint='', relink=True):
    """The armature's ``.sluggie`` file, found again when it moved, or ''.

    *hint* is a path the caller is about to use (the export target): its
    folder is searched first. With *relink*, a hit is written back to the
    armature (off for draw-time callers, which may not write ID properties).
    """
    if arm_obj is None:
        return ''
    path = stored(arm_obj)
    if path and os.path.isfile(path):
        return path
    key = os.path.normcase(path)
    found = _FOUND.get(key)
    if not found or not os.path.isfile(found):
        found = _search(arm_obj, path, hint)
        if path:
            _FOUND[key] = found
    if found and relink:
        remember(arm_obj, found)
    return found


def _search(arm_obj, stored_path, hint):
    blend_dir = _blend_dir()
    rel = str(arm_obj.get(REL_PROP) or '')
    if rel and blend_dir:
        candidate = os.path.normpath(os.path.join(blend_dir, rel))
        if os.path.isfile(candidate):
            return candidate
    name = os.path.basename(stored_path or rel)
    if not name:
        return ''
    folders = [os.path.dirname(os.path.abspath(p)) for p in (hint, stored_path) if p]
    if rel and blend_dir:
        folders.append(os.path.dirname(os.path.join(blend_dir, rel)))
    folders.append(blend_dir)
    chunk = arm_obj.get(CHUNK_PROP)
    accept = (lambda p: chunk_matches(p, chunk)) if chunk is not None else None
    return ModelCheckCore.find_sluggie_file(name, folders, accept)


def missing_message(arm_obj, feature="this tool"):
    """Error text when :func:`resolve` found nothing."""
    path = stored(arm_obj)
    where = f"its source file {path} is missing" if path else "it records no source .sluggie file"
    return f"{arm_obj.name}: {where}. {LOCATE_HINT}, or re-import the model, to enable {feature}."


class SLUGGIES_OT_locate_sluggie(bpy.types.Operator, ImportHelper):
    bl_idname = "sluggies.locate_sluggie"
    bl_label = "Locate source .sluggie"
    bl_description = (
        "Point the selected model at its .sluggie file again, after the file "
        "or the project folder moved"
    )
    bl_options = {"UNDO"}

    filename_ext = ".sluggie"
    filter_glob: StringProperty(default="*.sluggie", options={"HIDDEN"})  # type: ignore[valid-type]
    armature_name: StringProperty(options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _target_armature(context) is not None

    def invoke(self, context, event):
        arm_obj = _target_armature(context)
        self.armature_name = arm_obj.name
        path = stored(arm_obj)
        if path:
            self.filepath = path if os.path.isfile(path) else os.path.basename(path)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        arm_obj = bpy.data.objects.get(self.armature_name) or _target_armature(context)
        if arm_obj is None:
            self.report({"ERROR"}, "Select a Sluggies armature (or one of its meshes)")
            return {"CANCELLED"}
        path = os.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({"ERROR"}, f"{path} does not exist")
            return {"CANCELLED"}
        try:
            model = FieldCodec.load_sluggie(path).get('SluggiesModel')
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, f"Could not read {path}: {exc}")
            return {"CANCELLED"}
        if not isinstance(model, dict):
            self.report({"ERROR"}, f"{os.path.basename(path)} is not a .sluggie model file")
            return {"CANCELLED"}
        chunk = arm_obj.get(CHUNK_PROP)
        file_chunk = model.get('ChunkNumber')
        if chunk is not None and file_chunk is not None and int(file_chunk) != int(chunk):
            self.report({"ERROR"},
                        f"{os.path.basename(path)} is a model of directory {file_chunk}; "
                        f"{arm_obj.name} was imported from directory {chunk}. Pick that model's file.")
            return {"CANCELLED"}
        remember(arm_obj, path)
        self.report({"INFO"}, f"{arm_obj.name} now uses {path}")
        return {"FINISHED"}


def _is_armature(obj):
    return obj is not None and obj.type == 'ARMATURE' and (
        PATH_PROP in obj.keys() or 'SluggiesBoneMetadataVersion' in obj.keys())


def _target_armature(context):
    """The active armature, or the active mesh's armature parent."""
    obj = context.active_object
    if obj is None:
        return None
    if _is_armature(obj):
        return obj
    if obj.type == 'MESH':
        if _is_armature(obj.parent):
            return obj.parent
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and _is_armature(mod.object):
                return mod.object
    return None


def draw_locate_button(layout, context):
    """The relink button, with a warning row when the active model's file is
    missing. Reads only (draw context)."""
    arm_obj = _target_armature(context)
    if arm_obj is None:
        return
    if not resolve(arm_obj, relink=False):
        path = stored(arm_obj)
        layout.label(text=f"Source file missing: {os.path.basename(path)}" if path
                     else "No source .sluggie recorded", icon='ERROR')
    layout.operator(SLUGGIES_OT_locate_sluggie.bl_idname, icon='FILE_REFRESH')


@persistent
def _refresh_relative_paths(filepath=None, *_args):
    """Before a save: the relative paths follow the .blend's new location
    (*filepath*, the file being written; ``bpy.data.filepath`` still names
    the old one)."""
    blend_dir = os.path.dirname(os.path.abspath(filepath)) if isinstance(filepath, str) and filepath else None
    for obj in bpy.data.objects:
        if obj.type != 'ARMATURE' or PATH_PROP not in obj.keys():
            continue
        path = stored(obj)
        if path and os.path.isfile(path):
            remember(obj, path, blend_dir)


def register():
    bpy.utils.register_class(SLUGGIES_OT_locate_sluggie)
    if _refresh_relative_paths not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_refresh_relative_paths)


def unregister():
    if _refresh_relative_paths in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_refresh_relative_paths)
    bpy.utils.unregister_class(SLUGGIES_OT_locate_sluggie)
