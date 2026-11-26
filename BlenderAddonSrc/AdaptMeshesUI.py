"""Adapt Model, step Join or Replace Meshes: the row property group, the inventory and status of the
armature's meshes, and the operator. Registered by ``SluggiesToolsPanel.register`` (which also owns the
``WindowManager`` properties of the dialog).

Never imports ``SluggiesToolsPanel``.
"""

import bpy
from bpy.props import (
    BoolProperty, EnumProperty,
)
from . import AdaptCleanup
from . import HostBones
from . import ModelCheckCore
from . import SluggiePath
from .ExportTextures import (
    _custom_submesh_template_texture_index,
)
from .SceneModels import (
    _body_objects,
    _body_submesh,
    _bone_id_from_name,
    _bone_metadata_is_current,
    _donor_mesh_objects,
    _draw_cached,
    _model_is_stadium,
    _owning_armature,
    _reports_failure,
    _selected_sluggies_armature,
    _strip_donor_object_props,
)
from .HostBoneScene import (
    _UNIFORM_BONE_CACHE,
    _object_world_bbox_center,
    _ordered_host_bone_choices,
    _preview_active_bone,
    _renumber_added_bones_reporting,
    _uniform_bone_id,
)
from .SurfacePlans import (
    _model_tex_dir,
)
from .EnumItems import (
    _cached_enum_items,
    _drop_enum_items,
    _host_bone_items,
    _pointer,
    _set_enum_items,
)
from .AdaptSteps import (
    _ADAPT_STEPS,
    _report_adapt_summary,
)
from .SubmeshOps import (
    _convert_to_custom_submesh,
    _ensure_armature_modifier,
    _parent_keeping_world,
)
from .BodyOps import (
    _BACKUP_BODY_NOTE,
    _carry_shape_key_names,
    _dominant_bone_choice,
    _make_body,
    _set_aside_old_bodies,
    _use_body_uv_name,
)


# Join or Replace meshes: one row per mesh that can be added, kept on the
# WindowManager (sluggies_adapt_mesh_rows) while the dialog is open.
_ADAPT_MESH_ACTIONS = (
    ('JOIN', "Join into main body",
     "Merge the mesh into the skinned main body; its vertex groups become skin weights"),
    ('REPLACE', "Replaces main body",
     "The mesh becomes the skinned main body (submesh 0); " + _BACKUP_BODY_NOTE[0].lower() + _BACKUP_BODY_NOTE[1:]),
    ('RIGID', "Add as rigid submesh", "A custom submesh that follows one bone rigidly"),
    ('REPLACE_STATIC', "Replaces rigid mesh",
     "The mesh takes the place of one of the model's own rigid meshes, on that mesh's bone: "
     "a bat, a glove, a cap. The current one is set aside as '<name>.backup', off the armature and "
     "not exported"),
    ('JOIN_STATIC', "Join into rigid mesh",
     "Merge the mesh into one of the model's own rigid meshes; it follows that mesh's bone"),
    ('IGNORE', "Ignore", "Leave the mesh as it is"),
)
_STATIC_ACTIONS = ('REPLACE_STATIC', 'JOIN_STATIC')


_ADAPT_MESH_BONE_ENUM = 'adapt_meshes.bone'
_ADAPT_MESH_STATIC_ENUM = 'adapt_meshes.static'
_NO_STATIC_ITEMS = [('NONE', "No rigid mesh", "This model has no rigid mesh of its own", 0)]

_BACKUP_STATIC_NOTE = "A replaced rigid mesh is set aside as '<name>.backup', off the armature and not exported."

# Identifier of each rigid mesh the open dialog offers -> the name of its
# object in the scene ('' when it has none). Filled by the dialog's invoke.
_STATIC_TARGET_OBJECTS = {}


def _adapt_meshes_armature(context):
    return bpy.data.objects.get(context.window_manager.sluggies_adapt_mesh_armature)


def _update_adapt_mesh_row(self, context):
    """Bone names show while any row adds a rigid submesh (as in Reassign)."""
    wm = context.window_manager
    arm_obj = _adapt_meshes_armature(context)
    if arm_obj is not None:
        rigid = any(row.action == 'RIGID' for row in wm.sluggies_adapt_mesh_rows)
        arm_obj.data.show_names = rigid or wm.sluggies_adapt_mesh_names_shown


def _update_adapt_mesh_bone(self, context):
    _preview_active_bone(_adapt_meshes_armature(context), self.bone)


def _adapt_mesh_bone_items(self, context):
    arm_obj = _adapt_meshes_armature(context)
    return _cached_enum_items(_ADAPT_MESH_BONE_ENUM, _pointer(arm_obj), lambda: _host_bone_items(context, arm_obj))


def _adapt_mesh_static_items(self, context):
    return _cached_enum_items(_ADAPT_MESH_STATIC_ENUM, _pointer(_adapt_meshes_armature(context)),
                              lambda: list(_NO_STATIC_ITEMS))


def _current_static_object(objects):
    """Of the objects of one rigid submesh, the one that is its current
    state: an imported edit (`_edit`), else the first."""
    return next((o for o in objects if o.name.endswith('_edit')), objects[0] if objects else None)


def _static_mesh_targets(context, arm_obj, model):
    """[(submesh index, its object in the scene or None)] for each of the
    model's own rigid meshes: the rigid (three position components)
    submeshes of the .sluggie."""
    by_offset = {}
    for obj in _donor_mesh_objects(context, arm_obj):
        if 'VertexBufferOffset' in obj:
            by_offset.setdefault(str(obj['VertexBufferOffset']), []).append(obj)
    targets = []
    for index, submesh in enumerate(model.get('Submeshes') or []):
        vertex_buffer = submesh.get('VertexBuffer') or {}
        if int(vertex_buffer.get('VertexBufferCompCount') or 0) != 3:
            continue
        objects = by_offset.get(str(vertex_buffer.get('VertexBufferOffset')), [])
        targets.append((index, _current_static_object(objects)))
    return targets


def _static_mesh_items(targets):
    items = [
        (f'sm{index}', obj.name if obj is not None else f"submesh {index} (not in the scene)",
         f"The model's rigid mesh {index}" + (f" ({obj.name})" if obj is not None else ""), number)
        for number, (index, obj) in enumerate(targets)
    ]
    return items or list(_NO_STATIC_ITEMS)


def _static_owner_bone_id(arm_obj, index, obj):
    """The bone rigid mesh *index* follows: the one its object *obj* is on
    (it may have been reassigned), else the model's owner bone."""
    bone_id = _uniform_bone_id(obj) if obj is not None else None
    if bone_id is not None:
        return bone_id
    return next(
        (b_id for b in arm_obj.data.bones
         if (b_id := _bone_id_from_name(b.name)) is not None and not b.get('SluggiesUserAdded')
         and int(b.get('SluggiesGeoIdRaw', HostBones.GEO_ID_FREE)) == index), None)


class SluggiesAdaptMeshRow(bpy.types.PropertyGroup):
    """One mesh of the Join or Replace meshes dialog (name: the object's)."""
    action: EnumProperty(  # type: ignore[valid-type]
        name="Operation",
        items=_ADAPT_MESH_ACTIONS,
        default='RIGID',
        update=_update_adapt_mesh_row,
    )
    bone: EnumProperty(  # type: ignore[valid-type]
        name="Bone",
        description="Free bone the rigid submesh follows",
        items=_adapt_mesh_bone_items,
        update=_update_adapt_mesh_bone,
    )
    static: EnumProperty(  # type: ignore[valid-type]
        name="Rigid mesh",
        description="The model's own rigid mesh the mesh replaces or is joined into",
        items=_adapt_mesh_static_items,
    )


def _adapt_mesh_inventory(context, arm_obj):
    """(Sluggies meshes, meshes that can be added) of *arm_obj*: meshes
    below it or deformed by it."""
    objects = [
        o for o in context.view_layer.objects
        if o.type == 'MESH' and (_owning_armature(o.parent) is arm_obj
                                 or any(m.type == 'ARMATURE' and m.object is arm_obj for m in o.modifiers))
    ]
    addable, sluggies = AdaptCleanup.cleanup_targets(objects)
    return sluggies, addable


def _adapt_mesh_status(obj):
    collision = getattr(obj, 'sluggies_collision', None)
    if obj.get('SluggiesCustomSubmesh'):
        bone_id = _uniform_bone_id(obj)
        return f"Rigid mesh (added, bone_{bone_id})" if bone_id is not None else "Rigid mesh (added)"
    if obj.get('VertexBufferCompCount') == 6:
        return "Main body (skinned)"
    if obj.get('VertexBufferCompCount') == 3:
        return "Rigid mesh"
    return "Collision" if collision is not None and collision.enabled else "Sluggies mesh"


def _adapt_mesh_faults(wm):
    """(names of the faulty rows, messages) for the dialog's current choices."""
    rows = list(wm.sluggies_adapt_mesh_rows)
    faulty, messages = set(), []
    replacing = [r for r in rows if r.action == 'REPLACE']
    if len(replacing) > 1:
        faulty.update(r.name for r in replacing)
        messages.append("Only one mesh can replace the main body")
    skinned = [r for r in rows if r.action in ('JOIN', 'REPLACE')]
    if skinned and not wm.sluggies_adapt_mesh_has_body:
        faulty.update(r.name for r in skinned)
        messages.append("This model has no skinned main body: add the meshes as rigid submeshes")
    elif not replacing and not wm.sluggies_adapt_mesh_has_body_object \
            and any(r.action == 'JOIN' for r in rows):
        faulty.update(r.name for r in rows if r.action == 'JOIN')
        messages.append("No main body object in the scene to join into")
    by_bone = {}
    for row in rows:
        if row.action == 'RIGID':
            by_bone.setdefault(row.bone, []).append(row)
    for bone, group in by_bone.items():
        if bone == 'NONE':
            faulty.update(r.name for r in group)
            messages.append("No free bone left for a rigid submesh")
        elif len(group) > 1:
            faulty.update(r.name for r in group)
            messages.append(f"{bone} is picked for more than one rigid submesh")
    by_static = {}
    for row in rows:
        if row.action in _STATIC_ACTIONS:
            by_static.setdefault(row.static, []).append(row)
    for static, group in by_static.items():
        replacing_static = [r for r in group if r.action == 'REPLACE_STATIC']
        if static == 'NONE':
            faulty.update(r.name for r in group)
            messages.append("This model has no rigid mesh of its own: add the meshes as rigid submeshes")
        elif len(replacing_static) > 1:
            faulty.update(r.name for r in replacing_static)
            messages.append("Only one mesh can replace a rigid mesh; join the others into it")
        elif not replacing_static and not _STATIC_TARGET_OBJECTS.get(static):
            faulty.update(r.name for r in group)
            messages.append("That rigid mesh is not in the scene: let one mesh replace it instead")
    return faulty, messages


def _follow_one_bone(obj, bone_id):
    """Every vertex of *obj* in the one vertex group `bone_<bone_id>`, full
    weight, and no other group: what a rigid mesh exports with."""
    obj.vertex_groups.clear()
    group = obj.vertex_groups.new(name=f'bone_{bone_id}')
    group.add(list(range(len(obj.data.vertices))), 1.0, 'REPLACE')
    _UNIFORM_BONE_CACHE.clear()


def _make_static(obj, arm_obj, model, index, bone_id):
    """Turn the mesh *obj* into the model's rigid mesh *index*, where it is:
    that submesh's VertexBuffer* properties, one full-weight vertex group on
    *bone_id*, an Armature modifier, the armature as parent and its render UV
    map named like channel 0. Its materials stay plain until Convert
    Materials. Returns notes."""
    notes = []
    if obj.data.users > 1:
        obj.data = obj.data.copy()
        notes.append(f"{obj.name}'s mesh data was shared, so it got its own copy")
    _strip_donor_object_props(obj)
    for prop in ('SluggiesCustomSubmesh', 'CustomSubmeshId', 'TemplateSource'):
        if prop in obj:
            del obj[prop]
    for prop, value in ModelCheckCore.mesh_props_for_submesh(model, index).items():
        obj[prop] = value
    notes += [f"{obj.name}: {note}" for note in _use_body_uv_name(obj)]
    _follow_one_bone(obj, bone_id)
    _ensure_armature_modifier(obj, arm_obj)
    _parent_keeping_world(obj, arm_obj)
    return notes


def _join_into_static(context, static_obj, objs, bone_id):
    """Merge the meshes *objs* into the rigid mesh object *static_obj*, as
    _join_into_body does for the body; the joined mesh then follows *bone_id*
    alone. Returns notes."""
    notes = _join_into_body(context, static_obj, objs)
    _follow_one_bone(static_obj, bone_id)
    return notes


def _join_into_body(context, body_obj, objs):
    """Merge the meshes *objs* into the skinned body object *body_obj*: their
    render UV maps join the body's channel 0, their vertex groups the skin
    weights. Their materials stay plain until Convert Materials. One join for
    all of them, so a refused join leaves none of them merged. Returns notes."""
    notes = []
    objs = list(objs)
    if not objs:
        return notes
    for obj in objs:
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        if not any(slot.material is not None for slot in obj.material_slots):
            # Joining adds an empty slot for a mesh without material: give it the
            # body's first one instead.
            first = next((m for m in body_obj.data.materials if m is not None), None)
            if first is not None:
                obj.data.materials.clear()
                obj.data.materials.append(first)
            notes.append(f"{obj.name} had no material: its faces take the body's first one")
        notes += [f"{obj.name}: {note}" for note in _use_body_uv_name(obj)]
    with context.temp_override(active_object=body_obj, object=body_obj, selected_objects=[body_obj, *objs],
                               selected_editable_objects=[body_obj, *objs]):
        bpy.ops.object.join()
    return notes


# The window Join or Replace was confirmed in, for _reopen_adapt_meshes.
_REOPEN_WINDOW = [None]


def _reopen_adapt_meshes():
    """Timer: show the Join or Replace dialog again with its rows as they
    were, in the window it was open in (else the first one)."""
    wm = bpy.context.window_manager
    window = _REOPEN_WINDOW[0]
    _REOPEN_WINDOW[0] = None
    windows = list(wm.windows)
    if window not in windows:
        window = windows[0] if windows else None
    if window is not None:
        try:
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.ops.sluggies.adapt_meshes('INVOKE_DEFAULT', reopen=True)
        except RuntimeError as exc:     # its poll refused: the armature or the mode changed
            print(f"Sluggies: Join or Replace could not reopen: {exc}")
    return None


class SLUGGIES_OT_adapt_meshes(bpy.types.Operator):
    """Join or Replace meshes step of the 'Adapt' box"""
    bl_idname = "sluggies.adapt_meshes"
    bl_label = "Join or Replace Meshes"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'MESHES')
    bl_options = {"UNDO"}

    reopen: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Switch to Object Mode")
            return False
        if _selected_sluggies_armature(context) is None:
            cls.poll_message_set("Select a Sluggies armature or one of its child objects")
            return False
        return True

    def invoke(self, context, event):
        wm = context.window_manager
        arm_obj = _adapt_meshes_armature(context) if self.reopen else _selected_sluggies_armature(context)
        if arm_obj is None:
            self.report({"WARNING"}, "Select a Sluggies armature or one of its child objects")
            return {"CANCELLED"}
        if not _bone_metadata_is_current(arm_obj):
            self.report({"ERROR"}, HostBones.RE_IMPORT_MESSAGE)
            return {"CANCELLED"}
        sluggie_path = SluggiePath.resolve(arm_obj)
        if not sluggie_path:
            self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Join or Replace meshes"))
            return {"CANCELLED"}
        model, _tex_dir, error = _model_tex_dir(sluggie_path)
        if model is None:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        _renumber_added_bones_reporting(self, arm_obj, undo_step=True)
        ordered = _ordered_host_bone_choices(context, arm_obj)
        _set_enum_items(_ADAPT_MESH_BONE_ENUM, _pointer(arm_obj), _host_bone_items(context, arm_obj, ordered))
        statics = _static_mesh_targets(context, arm_obj, model)
        _set_enum_items(_ADAPT_MESH_STATIC_ENUM, _pointer(arm_obj), _static_mesh_items(statics))
        _STATIC_TARGET_OBJECTS.clear()
        _STATIC_TARGET_OBJECTS.update({f'sm{index}': obj.name if obj is not None else '' for index, obj in statics})
        self._statics = statics
        if not self.reopen:
            body = _body_submesh(model)
            wm.sluggies_adapt_mesh_armature = arm_obj.name
            wm.sluggies_adapt_mesh_names_shown = arm_obj.data.show_names
            wm.sluggies_adapt_mesh_has_body = body is not None
            wm.sluggies_adapt_mesh_has_body_object = body is not None and bool(
                _body_objects(context, arm_obj, body))
            self._fill_rows(context, arm_obj, ordered)
        return wm.invoke_props_dialog(self, width=560)

    def _fill_rows(self, context, arm_obj, ordered):
        """One row per addable mesh: a skinned one (several vertex groups)
        joins the body, others become rigid submeshes; every row gets its
        own free bone in case it becomes rigid, and the rigid mesh nearest
        it in case it replaces or joins one. On a model without a skinned
        body (a bat, a glove, an item) the usual wish is another object in
        the place of its rigid mesh, often made of several parts: the first
        mesh replaces the nearest one and the others join it."""
        wm = context.window_manager
        rows = wm.sluggies_adapt_mesh_rows
        rows.clear()
        _sluggies, addable = _adapt_mesh_inventory(context, arm_obj)
        pool = list(ordered)
        statics = getattr(self, '_statics', [])
        replaced = None     # the rigid mesh the first row replaces on a model without a body
        # Rigid meshes pick their bones first.
        for obj in sorted(addable, key=lambda o: len(o.vertex_groups) > 1):
            row = rows.add()
            row.name = obj.name
            skinned = len(obj.vertex_groups) > 1 and wm.sluggies_adapt_mesh_has_body
            choice = _dominant_bone_choice(arm_obj, obj, pool) if pool else None
            if choice is not None:
                row.bone = f'bone_{choice.bone_id}'
                pool.remove(choice)
            static = replaced if replaced is not None else self._nearest_static(obj, statics)
            if static is not None:
                row.static = f'sm{static}'
            if skinned:
                row.action = 'JOIN'
            elif static is not None and not wm.sluggies_adapt_mesh_has_body:
                row.action = 'REPLACE_STATIC' if replaced is None else 'JOIN_STATIC'
                replaced = static
            elif static is not None and choice is None:
                row.action = 'JOIN_STATIC'
            else:
                row.action = 'RIGID'

    @staticmethod
    def _nearest_static(obj, statics):
        """The submesh index of the rigid mesh nearest *obj* (those without
        an object in the scene last), or None when the model has none."""
        if not statics:
            return None
        center = _object_world_bbox_center(obj)

        def distance(entry):
            _index, static_obj = entry
            if static_obj is None:
                return float('inf')
            return (_object_world_bbox_center(static_obj) - center).length

        return min(statics, key=distance)[0]

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        arm_obj = _adapt_meshes_armature(context)
        if arm_obj is None:
            layout.label(text="The armature is gone", icon='ERROR')
            return
        layout.label(text=f"Armature: {arm_obj.name}", icon='ARMATURE_DATA')

        sluggies = _draw_cached(
            ('adapt_meshes', context.view_layer.as_pointer(), arm_obj.as_pointer()),
            lambda: [(obj.name, _adapt_mesh_status(obj))
                     for obj in _adapt_mesh_inventory(context, arm_obj)[0]])
        box = layout.box()
        box.label(text="Sluggies meshes")
        for name, status in sluggies:
            split = box.split(factor=0.5)
            split.label(text=name, icon='MESH_DATA')
            split.label(text=status)
        if not sluggies:
            box.label(text="None")

        faulty, messages = _adapt_mesh_faults(wm)
        box = layout.box()
        box.label(text="Meshes to add")
        for row in wm.sluggies_adapt_mesh_rows:
            line = box.row()
            line.alert = row.name in faulty
            split = line.split(factor=0.5)
            split.label(text=row.name, icon='MESH_DATA')
            choice = split.row(align=True)
            choice.prop(row, 'action', text="")
            if row.action == 'RIGID':
                choice.prop(row, 'bone', text="")
            elif row.action in _STATIC_ACTIONS:
                choice.prop(row, 'static', text="")
        if not len(wm.sluggies_adapt_mesh_rows):
            box.label(text="None: assign meshes to this armature first (step 2)")

        if wm.sluggies_adapt_mesh_has_body:
            layout.label(text="Only one mesh can be the fully skinned main body.", icon='INFO')
        if any(row.action == 'REPLACE' for row in wm.sluggies_adapt_mesh_rows):
            layout.label(text=_BACKUP_BODY_NOTE, icon='INFO')
        if any(row.action == 'REPLACE_STATIC' for row in wm.sluggies_adapt_mesh_rows):
            layout.label(text=_BACKUP_STATIC_NOTE, icon='INFO')
        if any(row.action in _STATIC_ACTIONS for row in wm.sluggies_adapt_mesh_rows):
            layout.label(text="A rigid mesh follows one bone: its vertex groups are replaced by that bone.",
                         icon='INFO')
        for message in messages:
            line = layout.row()
            line.alert = True
            line.label(text=message, icon='ERROR')

    @_reports_failure
    def execute(self, context):
        wm = context.window_manager
        faulty, messages = _adapt_mesh_faults(wm)
        if messages:
            # A props dialog cannot disable its OK button: show it again.
            self.report({"ERROR"}, messages[0])
            _REOPEN_WINDOW[0] = context.window
            bpy.app.timers.register(_reopen_adapt_meshes, first_interval=0.01)
            return {"CANCELLED"}
        try:
            return self._apply(context)
        finally:
            self._close(context)

    def cancel(self, context):
        self._close(context)

    def _close(self, context):
        wm = context.window_manager
        arm_obj = _adapt_meshes_armature(context)
        if arm_obj is not None:
            arm_obj.data.show_names = wm.sluggies_adapt_mesh_names_shown
        _drop_enum_items(_ADAPT_MESH_BONE_ENUM, _ADAPT_MESH_STATIC_ENUM)

    def _apply(self, context):
        wm = context.window_manager
        arm_obj = _adapt_meshes_armature(context)
        sluggie_path = SluggiePath.resolve(arm_obj) if arm_obj is not None else None
        if not sluggie_path:
            self.report({"ERROR"}, "The armature or its .sluggie is gone")
            return {"CANCELLED"}
        model, tex_dir, error = _model_tex_dir(sluggie_path)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        body = _body_submesh(model)

        chosen = {'JOIN': [], 'REPLACE': [], 'RIGID': []}
        for row in wm.sluggies_adapt_mesh_rows:
            obj = bpy.data.objects.get(row.name)
            if obj is not None and row.action in chosen:
                chosen[row.action].append((obj, _bone_id_from_name(row.bone) if row.action == 'RIGID' else None))
        joins = [obj for obj, _bone in chosen['JOIN']]
        new_body = chosen['REPLACE'][0][0] if chosen['REPLACE'] else None
        rigid = chosen['RIGID']
        # Rigid meshes: {submesh index: {'replace': object or None, 'join': [objects]}}
        static_plan = {}
        for row in wm.sluggies_adapt_mesh_rows:
            obj = bpy.data.objects.get(row.name)
            if obj is None or row.action not in _STATIC_ACTIONS or not row.static.startswith('sm'):
                continue
            entry = static_plan.setdefault(int(row.static[2:]), {'replace': None, 'join': []})
            if row.action == 'REPLACE_STATIC':
                entry['replace'] = obj
            else:
                entry['join'].append(obj)
        if not (joins or new_body or rigid or static_plan):
            self.report({"INFO"}, "Nothing to change")
            return {"CANCELLED"}

        # Everything that can fail is checked before the scene changes.
        stadium = _model_is_stadium(arm_obj)
        template = ModelCheckCore.default_template(stadium)
        try:
            free = {c.bone_id for c in _ordered_host_bone_choices(context, arm_obj)}
            for obj, bone_id in rigid:
                if bone_id not in free:
                    raise ValueError(f"bone_{bone_id} is no longer free; pick another bone for {obj.name}")
            if rigid:
                _custom_submesh_template_texture_index(model, template)
            if (joins or new_body) and body is None:
                raise ValueError("This model has no skinned main body")
            old_bodies = []
            if new_body is not None:
                old_bodies = [o for o in _body_objects(context, arm_obj, body) if o is not new_body]
                body_obj = new_body
            elif joins:
                bodies = _body_objects(context, arm_obj, body)
                # An imported edit (`_edit`) is the body's current state.
                body_obj = next((o for o in bodies if o.name.endswith('_edit')), bodies[0] if bodies else None)
                if body_obj is None:
                    raise ValueError("No main body object in the scene to join into")
            statics = dict(_static_mesh_targets(context, arm_obj, model))
            by_offset = {}
            for donor in _donor_mesh_objects(context, arm_obj):
                by_offset.setdefault(str(donor.get('VertexBufferOffset')), []).append(donor)
            for index, entry in static_plan.items():
                if index not in statics:
                    raise ValueError(f"The model has no rigid mesh {index}")
                current = statics[index]
                entry['bone'] = _static_owner_bone_id(arm_obj, index, current)
                if entry['bone'] is None:
                    raise ValueError(f"No bone of {arm_obj.name} owns rigid mesh {index}")
                offset = str(ModelCheckCore.mesh_props_for_submesh(model, index).get('VertexBufferOffset'))
                entry['old'] = [o for o in by_offset.get(offset, []) if o is not entry['replace']]
                entry['target'] = entry['replace'] or current
                if entry['target'] is None:
                    raise ValueError(f"Rigid mesh {index} is not in the scene: let one mesh replace it")
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        lines, notes = [], []
        if new_body is not None:
            if not any(slot.material is not None for slot in new_body.material_slots):
                notes.append(f"{new_body.name} has no material; give it one before Convert Materials")
            # The summary's closing "Next: weight..." line covers the weights.
            notes += _make_body(new_body, arm_obj, model, None, check_weights=False)
            notes += _carry_shape_key_names(old_bodies, new_body)
            lines.append(f"{new_body.name} is now the main body")
            backups = _set_aside_old_bodies(old_bodies)
            if backups:
                lines.append(f"Set aside (off the armature, not exported): {', '.join(backups)}")
        if joins:
            names = ", ".join(obj.name for obj in joins)
            notes += _join_into_body(context, body_obj, joins)
            lines.append(f"Joined into {body_obj.name}: {names}")
        for obj, bone_id in rigid:
            custom_submesh_id, obj_notes = _convert_to_custom_submesh(
                context, obj, arm_obj, bone_id, template, tex_dir, set_up_materials=False)
            lines.append(f"{obj.name} is now rigid submesh {custom_submesh_id} on bone_{bone_id}")
            notes += [f"{obj.name}: {note}" for note in obj_notes]
        for index, entry in sorted(static_plan.items()):
            target, bone_id = entry['target'], entry['bone']
            if entry['replace'] is not None:
                if not any(slot.material is not None for slot in target.material_slots):
                    notes.append(f"{target.name} has no material; give it one before Convert Materials")
                notes += _make_static(target, arm_obj, model, index, bone_id)
                lines.append(f"{target.name} is now rigid mesh {index} on bone_{bone_id}")
                backups = _set_aside_old_bodies(entry['old'])
                if backups:
                    lines.append(f"Set aside (off the armature, not exported): {', '.join(backups)}")
            if entry['join']:
                names = ", ".join(obj.name for obj in entry['join'])
                notes += _join_into_static(context, target, entry['join'], bone_id)
                lines.append(f"Joined into {target.name} (rigid mesh {index}, bone_{bone_id}): {names}")
        if static_plan:
            lines.append("Next: Convert Materials turns the new materials into the model's surfaces")
        if body is not None:
            lines.append("Next: weight the main body with Map Weights or Autorig")
        _report_adapt_summary(self, context, "Join or Replace Meshes", lines + notes)
        return {"FINISHED"}
