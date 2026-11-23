"""Models in the scene: the target armature of the selection, mesh kinds (donor, rigid, body, collision),
bone records, chunk number and stadium test, the High / Low pair, and the draw-time caches every tools
module shares (``_DRAW_CACHE``, ``_ANY_ARMATURE_CACHE``, cleared by ``HostBoneScene``'s handlers).

First in the import order of the tools modules: it imports none of them. Never imports ``SluggiesToolsPanel``.
"""

import functools
import re
import traceback
from . import AdaptLowpoly
from . import HostBones
from . import ModelCheckCore
from . import SluggiePath
from . import TemplateSources


_BONE_NAME_RE = re.compile(r'^bone_(\d+)$')


_SURFACE_ID_RE = re.compile(r'^sm(\d+)_ds\d+$')


_SURFACE_OWNER_RE = re.compile(r'^sm(\d+)$')


_SUBMESH_NAME_RE = re.compile(r'^CustomSubmesh_(\d+)$')


_INVALID_SUBMESH_NAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _bone_id_from_name(name):
    """bone_<id> -> id, else None."""
    m = _BONE_NAME_RE.match(name or '')
    return int(m.group(1)) if m else None


def _find_target_armature(context):
    """The Sluggies armature identified by *context*'s active object, if any.

    Strictly follows the active object (or its parent, for a mesh child) --
    it never falls back to scanning the whole scene. A scene can hold several
    imported models at once (for example a high- and low-poly version of the
    same character), each with its own bones and its own free/claimed state,
    so guessing among them would silently operate on the wrong armature.
    """
    obj = context.active_object
    if obj is None:
        return None
    if obj.type == 'ARMATURE' and 'SluggiesBoneMetadataVersion' in obj.keys():
        return obj
    if obj.parent is not None and obj.parent.type == 'ARMATURE' \
            and 'SluggiesBoneMetadataVersion' in obj.parent.keys():
        return obj.parent
    return None


def _any_sluggies_armature_imported(context):
    """Whether any Sluggies armature exists anywhere in the view layer,
    regardless of what is currently active. Used only to word the poll/empty
    messages correctly (\"import a model\" vs. \"select an armature\").

    Cached per view layer until the next depsgraph update, undo, redo or
    load: every poll of the panel's buttons asks on each redraw, and the
    answer walks all objects (hundreds with a stadium)."""
    key = context.view_layer.as_pointer()
    known = _ANY_ARMATURE_CACHE.get(key)
    if known is None:
        known = _ANY_ARMATURE_CACHE[key] = any(
            o.type == 'ARMATURE' and 'SluggiesBoneMetadataVersion' in o.keys()
            for o in context.view_layer.objects
        )
    return known


_ANY_ARMATURE_CACHE = {}     # view layer pointer -> bool, see _any_sluggies_armature_imported


# What a dialog's draw lists from the scene, by a key of the caller's: dialogs
# redraw on every hover and property change. Cleared with
# _ANY_ARMATURE_CACHE (depsgraph update, undo, redo, load), so it holds only
# while nothing in the scene changed.
_DRAW_CACHE = {}


def _draw_cached(key, compute):
    """compute(), kept in _DRAW_CACHE under *key* until the scene changes."""
    if key not in _DRAW_CACHE:
        _DRAW_CACHE[key] = compute()
    return _DRAW_CACHE[key]


def _no_target_armature_message(context):
    if not _any_sluggies_armature_imported(context):
        return "Import a Sluggies model first"
    return "Select an armature (or one of its meshes) as the active object"


def _bone_records(arm_obj):
    """Read the import-time bone snapshot off *arm_obj*'s bones."""
    records = []
    for b in arm_obj.data.bones:
        bone_id = _bone_id_from_name(b.name)
        if bone_id is None:
            continue
        parent_id = _bone_id_from_name(b.parent.name) if b.parent is not None else None
        records.append(HostBones.BoneRecord(
            bone_id=bone_id,
            parent_id=parent_id,
            geo_id_raw=int(b.get('SluggiesGeoIdRaw', HostBones.GEO_ID_FREE)),
            skinned=bool(b.get('SluggiesSkinned', False)),
            user_added=bool(b.get('SluggiesUserAdded', False)),
        ))
    return records


def _donor_mesh_objects(context, arm_obj):
    """Imported donor submesh objects (excludes custom submeshes)."""
    return [
        o for o in context.view_layer.objects
        if o.type == 'MESH' and o.parent is arm_obj and not o.get('SluggiesCustomSubmesh')
    ]


def _submesh_index_of(obj):
    """Donor submesh index, parsed from any 'sm<N>_ds<M>' SurfaceId material,
    else from an Add-material surface owned by 'sm<N>' (a body made with
    Make Main Body has only those)."""
    owned = None
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        m = _SURFACE_ID_RE.match(mat.get('SurfaceId', '') or '')
        if m:
            return int(m.group(1))
        m = _SURFACE_OWNER_RE.match(str(mat.get('SluggiesSurfaceOwner') or '')) \
            if mat.get('SluggiesNewSurface') else None
        if m and owned is None:
            owned = int(m.group(1))
    return owned


def _bone_metadata_is_current(arm_obj):
    """False for armatures imported before the current bone metadata format
    (version 1 stored a wrong SluggiesSkinned flag), which must be re-imported."""
    return HostBones.bone_metadata_is_current(arm_obj.get('SluggiesBoneMetadataVersion'))


# .sluggie path -> ChunkNumber, for armatures imported before the importer
# stored SluggiesChunkNumber (read once per file, not on every redraw).
_CHUNK_NUMBER_CACHE = {}


def _model_chunk_number(arm_obj):
    """The armature's .sluggie ChunkNumber (model directory), or None."""
    chunk = arm_obj.get('SluggiesChunkNumber')
    if chunk is not None:
        return int(chunk)
    path = SluggiePath.resolve(arm_obj, relink=False)   # draw-time caller
    if not path:
        return None
    if path not in _CHUNK_NUMBER_CACHE:
        try:
            _CHUNK_NUMBER_CACHE[path] = SluggiePath.load_model(path).get('ChunkNumber')
        except OSError:
            return None     # locked or briefly unreadable: not cached, the next call reads again
        except (ValueError, KeyError):
            _CHUNK_NUMBER_CACHE[path] = None
    return _CHUNK_NUMBER_CACHE[path]


def _model_is_stadium(arm_obj):
    """Whether the armature's model is a stadium (dirs 7-16, the patcher's
    STADIUM_CHUNKS): there a custom submesh must hang on a free vanilla root
    bone, and new bones are refused."""
    return TemplateSources.is_stadium_chunk(_model_chunk_number(arm_obj))


def _rigid_mesh_kind(obj):
    """('donor', submesh_index) for a rigid (CompCount 3) donor submesh,
    ('custom', None) for a custom submesh, or (None, None) for anything else."""
    if obj is None or obj.type != 'MESH':
        return None, None
    if obj.get('SluggiesCustomSubmesh'):
        return 'custom', None
    if obj.get('VertexBufferCompCount') == 3:
        return 'donor', _submesh_index_of(obj)
    return None, None


def _add_material_mesh_kind(obj):
    """Like _rigid_mesh_kind, but the skinned body (CompCount 6) counts as a
    donor submesh too: a new material on it is exported through the body
    rebuild."""
    kind, submesh_index = _rigid_mesh_kind(obj)
    if kind is None and obj is not None and obj.type == 'MESH' \
            and obj.get('VertexBufferCompCount') == 6 and not obj.get('SluggiesCustomSubmesh'):
        return 'donor', _submesh_index_of(obj)
    return kind, submesh_index


def _read_bone_hierarchy(sluggie_path):
    """The parsed .sluggie's BoneHierarchy list, or None if unreadable."""
    try:
        model = SluggiePath.load_model(sluggie_path)
    except (OSError, ValueError):
        return None
    return model.get('BoneHierarchy')


def _mesh_armature(obj):
    """The armature *obj* deforms with: its Armature modifier's, else its parent."""
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object is not None:
            return mod.object
    if obj.parent is not None and obj.parent.type == 'ARMATURE':
        return obj.parent
    return None


def _reports_failure(execute):
    """For the ``execute`` of an operator that changes the scene in several steps (Make Submesh, Make Main
    Body, Join or Replace Meshes, Convert Materials, Generate Lowpoly). Their own checks run before the first
    change; a file or Blender error after that (a texture that cannot be saved, a join that is refused)
    used to end in a traceback over a half-converted model, with no undo step. Now the error is reported
    and the operator returns FINISHED, so what it had changed is one step to undo."""
    @functools.wraps(execute)
    def guarded(self, context):
        try:
            return execute(self, context)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            traceback.print_exc()
            self.report({"ERROR"},
                f"{self.bl_label} stopped partway ({type(exc).__name__}: {exc}). Undo (Ctrl+Z) takes back "
                "what it had already changed; the traceback is in the system console.")
            return {"FINISHED"}
    return guarded


def _is_donor_mesh(obj):
    """An imported submesh of a model (it carries the VertexBuffer* properties)."""
    return not obj.get('SluggiesCustomSubmesh') \
        and any(prop in obj for prop in ModelCheckCore.REQUIRED_PROPS)


def _sluggies_armatures(context):
    return [o for o in context.view_layer.objects
            if o.type == 'ARMATURE' and 'SluggiesBoneMetadataVersion' in o.keys()]


# The skinned body. The body rebuild handles submesh 0 only.
_BODY_SUBMESH = 0


def _body_submesh(model):
    """Submesh 0 of *model* when it is the skinned body, else None."""
    submeshes = model.get('Submeshes') or []
    sub = submeshes[_BODY_SUBMESH] if submeshes else None
    if sub is None or int((sub.get('VertexBuffer') or {}).get('VertexBufferCompCount', 0)) != 6:
        return None
    return sub


def _body_objects(context, arm_obj, body):
    """The imported objects tied to the skinned *body* submesh."""
    offset = str((body.get('VertexBuffer') or {}).get('VertexBufferOffset'))
    return [o for o in _donor_mesh_objects(context, arm_obj)
            if 'VertexBufferOffset' in o and str(o['VertexBufferOffset']) == offset]


def _meshes(count):
    return f"{count} mesh{'es' if count != 1 else ''}"


def _owning_armature(obj):
    """*obj* itself or its nearest parent that is an armature, or None."""
    while obj is not None and obj.type != 'ARMATURE':
        obj = obj.parent
    return obj


def _selected_sluggies_armature(context):
    """The Sluggies armature a selected object is or sits under (the active
    object first), or None."""
    active = context.active_object
    selected = list(context.selected_objects)
    for obj in ([active] if active in selected else []) + selected:
        arm_obj = _owning_armature(obj)
        if arm_obj is not None and 'SluggiesBoneMetadataVersion' in arm_obj:
            return arm_obj
    return None


def _materials(count):
    return f"{count} material{'s' if count != 1 else ''}"


def _is_collision_mesh(obj):
    collision = getattr(obj, 'sluggies_collision', None)
    return collision is not None and collision.enabled


def _is_body_object(obj):
    """An imported (or Make Main Body) skinned body object."""
    return not obj.get('SluggiesCustomSubmesh') and int(obj.get('VertexBufferCompCount') or 0) == 6


def _lowpoly_partner(context, high):
    """(the imported Low model of the High model *high*, None), or (None, why
    there is none)."""
    high_path = SluggiePath.resolve(high, relink=False)
    if not high_path:
        return None, SluggiePath.missing_message(high, "Generate Lowpoly")
    if AdaptLowpoly.is_low_model(high_path):
        return None, f"{high.name} is a Low (_L_) model: select its High model"
    candidates = {}
    for arm_obj in _sluggies_armatures(context):
        path = SluggiePath.resolve(arm_obj, relink=False) if arm_obj is not high else ''
        if path:
            candidates[path] = arm_obj
    low = AdaptLowpoly.low_partner(high_path, candidates)
    if low is None:
        return None, f"Import the Low (_L_) version of {high.name} first"
    return low, None


def _high_low_pair(context, arm_obj):
    """(High, Low, None) for the imported model *arm_obj* (either of the two)
    and its imported partner, or (None, None, why there is none)."""
    path = SluggiePath.resolve(arm_obj, relink=False)
    if not path:
        return None, None, SluggiePath.missing_message(arm_obj, "Export (High+Low)")
    if not AdaptLowpoly.is_low_model(path):
        low, problem = _lowpoly_partner(context, arm_obj)
        return (arm_obj, low, None) if low is not None else (None, None, problem)
    highs = [h for h in _sluggies_armatures(context)
             if h is not arm_obj and _lowpoly_partner(context, h)[0] is arm_obj]
    if len(highs) != 1:
        return None, None, (f"Import the High version of {arm_obj.name} first" if not highs
                            else f"Several High models pair with {arm_obj.name}")
    return highs[0], arm_obj, None
