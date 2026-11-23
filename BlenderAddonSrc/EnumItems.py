"""Dynamic enum items of the tools' dialogs: the cache that keeps the item lists (and their strings) alive
between redraws (``_ENUM_ITEMS``, emptied by ``SluggiesToolsPanel.unregister``) and the builders for host
bones, template sources, reassign targets, placement and Add Bone parents.

Imports ``SceneModels``, ``HostBoneScene`` and ``SurfacePlans`` among the tools modules. Never imports ``SluggiesToolsPanel``.
"""

from . import HostBones
from . import TemplateSources
from .SceneModels import (
    _bone_id_from_name,
    _bone_metadata_is_current,
    _find_target_armature,
    _model_is_stadium,
    _no_target_armature_message,
)
from .HostBoneScene import (
    STADIUM_ROOT_PARENT,
    _HOST_BONE_STATUS_TAGS,
    _ordered_host_bone_choices,
    _reassign_choices,
)
from .SurfacePlans import (
    _template_source_materials,
)


# Dynamic enum items by name: (key, items). Blender calls an items callback
# on every redraw of a dialog showing the enum and on every read of the
# property, so a list is built once and reused while its key (armature,
# active object) matches. invoke() drops it so each dialog starts fresh, and
# execute()/cancel() drop it when the dialog closes; execute() still re-checks
# the scene itself. Holding the items here also keeps their strings alive,
# which Blender requires of dynamic enums.
_ENUM_ITEMS = {}


def _cached_enum_items(name, key, build):
    cached = _ENUM_ITEMS.get(name)
    if cached is None or cached[0] != key:
        cached = _ENUM_ITEMS[name] = (key, build())
    return cached[1]


def _set_enum_items(name, key, items):
    _ENUM_ITEMS[name] = (key, items)


def _drop_enum_items(*names):
    for name in names:
        _ENUM_ITEMS.pop(name, None)


def _pointer(obj):
    return obj.as_pointer() if obj is not None else None


def _host_bone_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    return _cached_enum_items(
        'add_submesh.host_bone', _pointer(arm_obj), lambda: _host_bone_items(context, arm_obj))


def _host_bone_items(context, arm_obj, ordered=None):
    """Add Submesh's host bone items; *ordered* when the caller has the choices."""
    if arm_obj is None:
        return [('NONE', "No armature", _no_target_armature_message(context), 0)]
    if not _bone_metadata_is_current(arm_obj):
        return [('NONE', "Re-import needed", HostBones.RE_IMPORT_MESSAGE, 0)]
    if ordered is None:
        ordered = _ordered_host_bone_choices(context, arm_obj)
    if not ordered:
        return [('NONE', "No free bones", "Every bone already owns a mesh or is claimed", 0)]
    return _bone_choice_items(ordered)


def _bone_choice_items(ordered):
    """Enum items for host bone choices: 'bone_<id> (tag)'."""
    items = []
    for idx, choice in enumerate(ordered):
        parent_label = f'bone_{choice.parent_id}' if choice.parent_id is not None else "none"
        tag = _HOST_BONE_STATUS_TAGS.get(choice.status, choice.status)
        items.append((
            f'bone_{choice.bone_id}',
            f'bone_{choice.bone_id} ({tag})',
            f"{choice.reason}; parent {parent_label}",
            idx,
        ))
    return items


def _template_source_description(choice):
    """Dialog tooltip. Built-ins carry their own description (what
    the shader mode does); donor sources just name their kind."""
    if choice.kind == 'builtin':
        template = TemplateSources.BUILTIN_TEMPLATES.get(choice.argument)
        if template is not None:
            return template.description
    return f"{choice.kind} template source"


def _template_source_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    return _cached_enum_items(
        'template_source', _pointer(arm_obj), lambda: _template_source_items(context, arm_obj))


def _template_source_items(context, arm_obj):
    if arm_obj is None:
        return [('NONE', "None", _no_target_armature_message(context), 0)]
    choices = TemplateSources.build_template_source_choices(
        _template_source_materials(context, arm_obj), stadium=_model_is_stadium(arm_obj))
    if not choices:
        return [('NONE', "None", "No usable surface template found", 0)]
    return [
        (c.template_source, c.template_source, _template_source_description(c), idx)
        for idx, c in enumerate(choices)
    ]


def _reassign_target_bone_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    obj = context.active_object
    return _cached_enum_items(
        'reassign.target_bone', (_pointer(arm_obj), _pointer(obj)),
        lambda: _reassign_target_bone_items(context, arm_obj, obj))


def _reassign_target_bone_items(context, arm_obj, obj, ordered=None):
    """Reassign's target bone items; *ordered* when the caller has the choices."""
    if arm_obj is None or obj is None:
        return [('NONE', "No armature", _no_target_armature_message(context), 0)]
    if ordered is None:
        ordered = _reassign_choices(context, arm_obj, obj)
    if not ordered:
        return [('NONE', "No other free bones", "No other bone is free to move to", 0)]
    return _bone_choice_items(ordered)


_PLACEMENT_KEEP_WORLD = (
    'KEEP_WORLD', "Keep world position",
    "The mesh stays exactly where it is and follows the new bone from now on. "
    "Patched through Hammerspace, which export picks automatically.",
)


_PLACEMENT_KEEP_OFFSET = (
    'KEEP_OFFSET', "Keep offset to bone",
    "The mesh jumps to the same offset from the new bone as it had from the old one. "
    "Its bytes don't change, so this also works with the in-place patcher.",
)


def _placement_enum_items(self, context):
    # Both placements are open to a mesh with facial shape keys too: a kept
    # world position takes the rigid rebuild, which carries the poses
    # (RigidRebuild.FacialPoses) since 2026-10-10.
    return [_PLACEMENT_KEEP_WORLD, _PLACEMENT_KEEP_OFFSET]


def _add_bone_parent_enum_items(self, context):
    arm_obj = _find_target_armature(context)
    return _cached_enum_items(
        'add_bone.parent_bone', _pointer(arm_obj), lambda: _add_bone_parent_items(context, arm_obj))


def _add_bone_parent_items(context, arm_obj):
    if arm_obj is None:
        return [('NONE', "No armature", _no_target_armature_message(context), 0)]
    # data.bones iterates depth-first through the hierarchy; list by numeric id.
    bones = sorted(
        (b for b in arm_obj.data.bones if _bone_id_from_name(b.name) is not None),
        key=lambda b: _bone_id_from_name(b.name))
    items = []
    for idx, b in enumerate(bones):
        tag = "new" if b.get('SluggiesUserAdded') else "donor"
        items.append((b.name, b.name, f"Parent the new bone to {b.name} ({tag})", idx))
    if _model_is_stadium(arm_obj):
        # Every vanilla stadium bone is a root; a new one may be a root or a child.
        items.insert(0, (STADIUM_ROOT_PARENT, "None (root bone)",
                         "A new root bone at the 3D cursor", len(items)))
    return items or [('NONE', "No bones", "This armature has no bones", 0)]
