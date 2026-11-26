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
import time

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

# stored path -> path found for it ('' when nothing was found), so a missing
# file is searched once, not on every redraw or export step. A miss is
# searched again by the tools that act on the file (relink), and after a
# load, or a save into another folder (the .blend's folder is part of the
# search).
_FOUND = {}
_FOUND_BLEND_DIR = [None]     # the .blend's folder _FOUND was filled for

# path -> time.monotonic() of its last positive os.path.isfile: draw-time
# callers (relink off) trust it for _SEEN_SECONDS, so a sidebar redraw does
# not stat the same file many times (slow on a synced or network folder).
_SEEN = {}

# normcase(abspath) -> (mtime_ns, size, SluggiesModel dict): one user action
# reads the same .sluggie from several helpers (and once per material), each
# read being xz plus JSON. Kept while the file is unchanged; a few entries.
_MODELS = {}
_MODELS_MAX = 4


def load_model(path):
    """The ``SluggiesModel`` dict of the ``.sluggie`` at *path*, read once
    while the file is unchanged. Shared between callers: read it, do not
    change it. Raises OSError / ValueError like ``FieldCodec.load_sluggie``;
    a file without a model gives ``{}``."""
    key = os.path.normcase(os.path.abspath(path))
    stat = os.stat(path)
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _MODELS.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    model = FieldCodec.load_sluggie(path).get('SluggiesModel', {})
    if not isinstance(model, dict):
        raise ValueError(f"{os.path.basename(path)} is not a .sluggie model file")
    _MODELS.pop(key, None)
    while len(_MODELS) >= _MODELS_MAX:
        _MODELS.pop(next(iter(_MODELS)))
    _MODELS[key] = (stamp, model)
    return model
_SEEN_SECONDS = 2.0


def _is_file(path, relink=True):
    now = time.monotonic()
    if not relink and now - _SEEN.get(path, -_SEEN_SECONDS) < _SEEN_SECONDS:
        return True
    if os.path.isfile(path):
        _SEEN[path] = now
        return True
    _SEEN.pop(path, None)
    return False


def stored(arm_obj):
    """The recorded absolute path ('' when none)."""
    return str(arm_obj.get(PATH_PROP) or '') if arm_obj is not None else ''


def _blend_dir():
    # While an add-on is being enabled, bpy.data is a restricted stand-in without `filepath`.
    path = getattr(bpy.data, 'filepath', '')
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
        file_chunk = load_model(path).get('ChunkNumber')
    except (OSError, ValueError, KeyError, AttributeError):
        return False
    return file_chunk is None or int(file_chunk) == int(chunk)


def resolve(arm_obj, hint='', relink=True):
    """The armature's ``.sluggie`` file, found again when it moved, or ''.

    *hint* is a path the caller is about to use (the export target): its
    folder is searched first. With *relink*, a hit is written back to the
    armature and a remembered miss is searched again (off for draw-time
    callers, which may not write ID properties and must stay fast).
    """
    if arm_obj is None:
        return ''
    path = stored(arm_obj)
    if path and _is_file(path, relink):
        return path
    key = os.path.normcase(path)
    found = _FOUND.get(key)
    if found is None or (relink and not found) or (found and not os.path.isfile(found)):
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


@persistent
def _forget_found(*_args):
    """After a load: search remembered misses again."""
    _FOUND.clear()
    _SEEN.clear()
    _FOUND_BLEND_DIR[0] = _blend_dir()


@persistent
def _forget_found_after_save(*_args):
    """After a save: search remembered misses again only when the .blend went
    into another folder (Save As, the first save). A plain save changes
    nothing the search looks at, and the search walks a folder tree and
    parses every namesake file."""
    if _blend_dir() != _FOUND_BLEND_DIR[0]:
        _forget_found()


_HANDLERS = (('load_post', _forget_found), ('save_post', _forget_found_after_save))


def register():
    bpy.utils.register_class(SLUGGIES_OT_locate_sluggie)
    if _refresh_relative_paths not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_refresh_relative_paths)
    _FOUND_BLEND_DIR[0] = _blend_dir()
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler not in handlers:
            handlers.append(handler)


def unregister():
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)
    if _refresh_relative_paths in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_refresh_relative_paths)
    bpy.utils.unregister_class(SLUGGIES_OT_locate_sluggie)
