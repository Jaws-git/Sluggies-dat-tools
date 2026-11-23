"""Adapt Model, steps Clean Up and Assign to Armature (with its live preview, a timer that
``SluggiesToolsPanel.unregister`` stops) and the parked Transform step. Registered by
``SluggiesToolsPanel.register``.

Never imports ``SluggiesToolsPanel``.
"""

import bpy
from bpy.props import (
    BoolProperty, EnumProperty,
)
from mathutils import Matrix
from . import AdaptCleanup
from . import AdaptTransform
from .SceneModels import (
    _any_sluggies_armature_imported,
    _meshes,
    _sluggies_armatures,
)
from .EnumItems import (
    _cached_enum_items,
    _drop_enum_items,
)
from .AdaptSteps import (
    _ADAPT_STEPS,
    _ADAPT_TRANSFORM_DESCRIPTION,
    _report_adapt_summary,
)
from .SubmeshOps import (
    _default_make_submesh_armature,
)


class SLUGGIES_OT_adapt_cleanup(bpy.types.Operator):
    """Clean Up step of the 'Adapt' box (AdaptCleanup)"""
    bl_idname = "sluggies.adapt_cleanup"
    bl_label = "Clean Up"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'CLEANUP')
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Switch to Object Mode")
            return False
        return True

    def execute(self, context):
        selected = _with_external_rig_children(context.selected_objects)
        targets, skipped = AdaptCleanup.cleanup_targets(selected or context.view_layer.objects)
        if not targets:
            where = "selected" if selected else "in this scene"
            self.report({"WARNING"}, f"No meshes {where} to clean (.sluggie imports are left alone)")
            return {"CANCELLED"}
        counts = AdaptCleanup.run_cleanup(targets)
        lines = AdaptCleanup.summary_lines(counts)
        if selected and skipped:
            lines.append(f"Skipped {_meshes(len(skipped))} from a .sluggie")
        _report_adapt_summary(self, context, "Clean Up", lines)
        return {"FINISHED"}


def _with_external_rig_children(objects):
    """*objects*, plus every descendant of a selected armature that is not a
    Sluggies one: selecting an imported rig cleans the meshes under it."""
    found = dict.fromkeys(objects)
    for obj in objects:
        if obj.type == 'ARMATURE' and 'SluggiesBoneMetadataVersion' not in obj.keys():
            found.update(dict.fromkeys(obj.children_recursive))
    return list(found)


def _adapt_armature_enum_items(self, context):
    return _cached_enum_items('adapt_armature.armature', None, lambda: [
        (a.name, a.name, f"Parent the meshes to {a.name}", idx)
        for idx, a in enumerate(_sluggies_armatures(context))
    ] or [('NONE', "No armature", "Import a Sluggies model first", 0)])


def _followed_armatures(obj):
    """The armatures *obj* follows: its parent and its Armature modifiers' targets."""
    return [obj.parent] + [m.object for m in obj.modifiers if m.type == 'ARMATURE']


# Meshes Assign to Armature's dialog lists by name; more are only counted.
_ASSIGN_LIST_MAX = 25


# Assign to Armature's live alignment preview while its dialog is open:
# 'saved' {object: (matrix_basis, mesh, Armature modifier visibility)} from
# before the dialog, restored before every preview, on Cancel and before OK
# applies for real; 'request' the settings a timer previews next; 'lines' /
# 'error' what the last preview found; 'listed' {armature name: (targets,
# others, posed)} what the dialog's draw shows for that armature, worked out
# once (the dialog redraws on every hover). Empty while no dialog is open.
_ASSIGN_PREVIEW = {}


def _assign_settings(op):
    return dict(armature=op.armature, align=op.align, up_axis=op.up_axis, front_axis=op.front_axis,
                scale_fit=op.scale_fit, pose=op.pose, align_only=op.align and op.align_only)


def _update_assign_preview(self, context):
    """Operator property update: preview the new settings (from a timer:
    a dialog cannot change the scene itself)."""
    if 'saved' not in _ASSIGN_PREVIEW:
        return
    _ASSIGN_PREVIEW['request'] = _assign_settings(self)
    if not bpy.app.timers.is_registered(_run_assign_preview):
        bpy.app.timers.register(_run_assign_preview, first_interval=0.0)


def _run_assign_preview():
    request = _ASSIGN_PREVIEW.pop('request', None)
    if request is not None and 'saved' in _ASSIGN_PREVIEW:
        _apply_assign_preview(bpy.context, request)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    return None


def _start_assign_preview(context, targets):
    _ASSIGN_PREVIEW.clear()
    AdaptTransform.begin_cache()    # weights and topology do not change under the dialog
    _ASSIGN_PREVIEW['targets'] = list(targets)
    _ASSIGN_PREVIEW['selected'] = list(context.selected_objects)
    # The old rigs too: Align and scale only moves them.
    _ASSIGN_PREVIEW['saved'] = {
        obj: (obj.matrix_basis.copy(), obj.data,
              [(m, m.show_viewport) for m in obj.modifiers if m.type == 'ARMATURE'])
        for obj in list(targets) + _old_rigs(context, targets)
    }
    _ASSIGN_PREVIEW['backups'] = {}


def _restore_assign_state():
    """Put the dialog's meshes back as they were when it opened."""
    for obj, backup in _ASSIGN_PREVIEW.get('backups', {}).items():
        try:
            AdaptTransform.restore_mesh(obj, backup)
        except ReferenceError:
            pass
    _ASSIGN_PREVIEW['backups'] = {}
    for obj, (basis, mesh, modifiers) in _ASSIGN_PREVIEW.get('saved', {}).items():
        try:
            if obj.data != mesh:
                obj.data = mesh
            for mod, shown in modifiers:
                mod.show_viewport = shown
            obj.matrix_basis = basis
        except ReferenceError:
            pass


def _end_assign_preview(keep_cache=False):
    """Restore the scene; *keep_cache* leaves AdaptTransform's cache on for
    OK's own fit (the caller ends it)."""
    _restore_assign_state()
    _ASSIGN_PREVIEW.clear()
    if not keep_cache:
        AdaptTransform.end_cache()


def _apply_assign_preview(context, settings):
    """Show *settings*' alignment on the dialog's meshes, as they will look
    once assigned (the old rig no longer deforming them)."""
    _restore_assign_state()
    _ASSIGN_PREVIEW['lines'], _ASSIGN_PREVIEW['error'] = [], None
    if not settings['align']:
        return
    arm_obj = bpy.data.objects.get(settings['armature']) if settings['armature'] != 'NONE' else None
    if arm_obj is None or arm_obj not in _sluggies_armatures(context):
        _ASSIGN_PREVIEW['error'] = "Pick a Sluggies armature"
        return
    targets = _ASSIGN_PREVIEW['targets']
    try:
        # Filled while baking: a failure half way still leaves every baked
        # mesh where _restore_assign_state (and Cancel) puts it back.
        _bake_assign_poses(context, targets, settings, _ASSIGN_PREVIEW['backups'])
        matrix, lines = _assign_fit(context, arm_obj, targets, settings)
    except Exception as exc:  # noqa: BLE001 - a timer has no caller to report to: show it in the dialog
        _restore_assign_state()
        _ASSIGN_PREVIEW['error'] = str(exc).strip().splitlines()[-1] if str(exc).strip() \
            else type(exc).__name__
        return
    if not settings['align_only']:
        for obj in targets:
            for mod in obj.modifiers:
                if mod.type == 'ARMATURE':
                    mod.show_viewport = False
    _move_all(_aligned_objects(context, targets, settings), matrix)
    _ASSIGN_PREVIEW['lines'] = lines


def _aligned_objects(context, targets, settings):
    """What the alignment moves: the meshes, or with Align and scale only
    their old rigs along with them (a mesh moved away from the rig that
    deforms it would bend wrong), without what a moved parent takes along."""
    if not settings['align_only']:
        return list(targets)
    return _without_followers(set(targets) | set(_old_rigs(context, targets)))


def _without_followers(objects):
    """*objects* (a set) minus those with an ancestor in it: moving that
    ancestor moves them along."""
    def follows(obj):
        parent = obj.parent
        while parent is not None:
            if parent in objects:
                return True
            parent = parent.parent
        return False

    return [obj for obj in objects if not follows(obj)]


def _move_all(objects, matrix):
    """Left-multiply *matrix* onto each of *objects*' world matrix."""
    worlds = {obj: matrix @ obj.matrix_world for obj in objects}
    # Parents first: moving a parent moves its children along.
    for obj in sorted(objects, key=_depth):
        obj.matrix_world = worlds[obj]


def _depth(obj):
    depth = 0
    while obj.parent is not None:
        obj, depth = obj.parent, depth + 1
    return depth


def _old_rigs(context, targets):
    """The non-Sluggies armatures *targets* follow, plus selected ones, by
    name. While the Assign dialog is open the selection is the one its
    invoke saw (the preview timer's context may not have the member)."""
    armatures = set(_sluggies_armatures(context))
    old_rigs = {a for obj in targets for a in _followed_armatures(obj)
                if a is not None and a.type == 'ARMATURE' and a not in armatures}
    selected = _ASSIGN_PREVIEW['selected'] if 'selected' in _ASSIGN_PREVIEW else context.selected_objects
    old_rigs.update(o for o in selected if o.type == 'ARMATURE' and o not in armatures)
    return sorted(old_rigs, key=lambda a: a.name)


def _restore_meshes(backups):
    for obj, backup in backups.items():
        AdaptTransform.restore_mesh(obj, backup)


def _bake_assign_poses(context, targets, settings, backups=None):
    """With 'Use pose', the old rig's current pose written into each target
    that follows a posed one (not with Align and scale only: the rig keeps
    posing it). Returns {object: backup}. With *backups* (a dict) each
    backup goes in as soon as it is taken and the caller restores them on
    failure; without, a failure restores the baked meshes here."""
    own = backups is None
    backups = {} if own else backups
    if settings['pose'] != 'POSE' or settings['align_only']:
        return backups
    posed = {rig for rig in _old_rigs(context, targets) if AdaptTransform.is_posed(rig)}
    try:
        for obj in targets:
            if any(m.type == 'ARMATURE' and m.object in posed and m.show_viewport for m in obj.modifiers):
                if obj.data.users > 1:
                    obj.data = obj.data.copy()
                backup = AdaptTransform.bake_pose(context, obj)
                if backup is not None:
                    backups[obj] = backup
    except BaseException:
        if own:
            _restore_meshes(backups)
        raise
    return backups


def _assign_fit(context, arm_obj, targets, settings):
    """AdaptTransform.fit for *targets*, measured as they will show once
    parented: without their Armature modifiers (a kept pose is baked in by
    then). With Align and scale only, as they show now: the old rig keeps
    posing them, so its pose is baked in just for measuring (the vertex
    group readings use the mesh data, not the displayed shape)."""
    align_only = settings['align_only']
    baked = _bake_assign_poses(context, targets, dict(settings, pose='POSE', align_only=False)) \
        if align_only else {}
    modifiers = [m for obj in targets for m in obj.modifiers if m.type == 'ARMATURE' and m.show_viewport]
    try:
        for mod in modifiers:
            mod.show_viewport = False
        context.view_layer.update()
        return AdaptTransform.fit(
            arm_obj, targets, context.scene.objects, context.evaluated_depsgraph_get(),
            settings['up_axis'], settings['front_axis'], source_armatures=_old_rigs(context, targets),
            scale_fit=settings['scale_fit'], source_posed=align_only or settings['pose'] == 'POSE')
    finally:
        for mod in modifiers:
            mod.show_viewport = True
        _restore_meshes(baked)
        context.view_layer.update()


class SLUGGIES_OT_adapt_armature(bpy.types.Operator):
    """Assign to Armature step of the 'Adapt' box"""
    bl_idname = "sluggies.adapt_armature"
    bl_label = "Assign to Armature"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'ARMATURE')
    bl_options = {"REGISTER", "UNDO"}

    armature: EnumProperty(
        name="Armature",
        description="Sluggies armature the meshes are parented to",
        items=_adapt_armature_enum_items,
        update=_update_assign_preview,
    )  # type: ignore[valid-type]
    align: BoolProperty(  # type: ignore[valid-type]
        name="Align and scale to target armature",
        description=("Before parenting, turn, scale (evenly) and move the meshes together so they stand on "
                     "the armature: upright, facing its front, head at its model's height. Up and front are "
                     "read from the old rig's names (head, feet, eyes, ...) where it has them. Previewed "
                     "while the dialog is open"),
        default=False,
        update=_update_assign_preview,
    )
    align_only: BoolProperty(  # type: ignore[valid-type]
        name="Align and scale only - no reassignment",
        description=("Only turn, scale and move the meshes onto the armature; they stay on their old rig, "
                     "which moves along with them, and keep its pose. Nothing is parented and no Armature "
                     "modifier changes"),
        default=False,
        update=_update_assign_preview,
    )
    up_axis: EnumProperty(  # type: ignore[valid-type]
        name="Up",
        description="Direction the model's head points to now",
        items=AdaptTransform.AXIS_ITEMS,
        update=_update_assign_preview,
    )
    front_axis: EnumProperty(  # type: ignore[valid-type]
        name="Facing",
        description="Direction the model faces now",
        items=AdaptTransform.AXIS_ITEMS,
        update=_update_assign_preview,
    )
    scale_fit: EnumProperty(  # type: ignore[valid-type]
        name="Scale fit",
        description="What decides the model's size",
        items=AdaptTransform.SCALE_FIT_ITEMS,
        update=_update_assign_preview,
    )
    pose: EnumProperty(  # type: ignore[valid-type]
        name="Old rig",
        description="Which shape the meshes keep from the armature they come with",
        items=[
            ('POSE', "Use pose",
             "Keep the shape the old rig's current pose gives the meshes (for example arms moved from an "
             "A-pose into a T-pose), shape keys included"),
            ('REST', "Use rest pose", "The meshes go back to the old rig's rest pose"),
        ],
        default='POSE',
        update=_update_assign_preview,
    )

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Switch to Object Mode")
            return False
        if not _any_sluggies_armature_imported(context):
            cls.poll_message_set("Import a Sluggies model first")
            return False
        return True

    def _targets(self, context, arm_obj):
        """(meshes to assign, skipped .sluggie meshes, skipped meshes of
        another Sluggies armature). With nothing selected, meshes already on
        another Sluggies armature stay there (High and Low in one scene)."""
        selected = _with_external_rig_children(context.selected_objects)
        targets, skipped = AdaptCleanup.cleanup_targets(selected or context.view_layer.objects)
        others = []
        if not selected:
            armatures = set(_sluggies_armatures(context))
            others = [o for o in targets
                      if any(a in armatures and a is not arm_obj for a in _followed_armatures(o))]
            targets = [o for o in targets if o not in others]
        return targets, skipped, others

    def invoke(self, context, event):
        _drop_enum_items('adapt_armature.armature')
        armatures = _sluggies_armatures(context)
        targets, _skipped, _others = self._targets(context, None)
        arm_obj = _default_make_submesh_armature(context, targets[0], armatures) if targets \
            else next((a for a in armatures if a in context.selected_objects), None)
        if arm_obj is not None:
            self.armature = arm_obj.name
        _start_assign_preview(context, targets)
        if self.align:
            _update_assign_preview(self, context)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def _listed(self, context):
        """(targets, others, any old rig posed) for the picked armature; kept
        while the dialog is open, as the selection and the old rigs' poses do
        not change under it (the preview moves objects, not pose bones)."""
        listed = _ASSIGN_PREVIEW.setdefault('listed', {})
        entry = listed.get(self.armature)
        if entry is None:
            arm_obj = bpy.data.objects.get(self.armature) if self.armature != 'NONE' else None
            targets, _skipped, others = self._targets(context, arm_obj)
            posed = any(AdaptTransform.is_posed(rig) for rig in _old_rigs(context, targets))
            entry = listed[self.armature] = (targets, others, posed)
        return entry

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'armature')
        targets, others, posed = self._listed(context)
        align_only = self.align and self.align_only
        if not align_only and posed:
            layout.row().prop(self, 'pose', expand=True)
        layout.prop(self, 'align')
        if self.align:
            layout.prop(self, 'align_only')
            row = layout.row()
            row.prop(self, 'up_axis')
            row.prop(self, 'front_axis')
            layout.prop(self, 'scale_fit')
            if _ASSIGN_PREVIEW.get('error'):
                line = layout.row()
                line.alert = True
                line.label(text=_ASSIGN_PREVIEW['error'], icon='ERROR')
            elif _ASSIGN_PREVIEW.get('lines'):
                col = layout.box().column(align=True)
                for text in _ASSIGN_PREVIEW['lines']:
                    col.label(text=text)
        box = layout.box()
        box.label(text=f"{_meshes(len(targets)).capitalize()} to {'align' if align_only else 'parent'}:")
        col = box.column(align=True)
        for obj in targets[:_ASSIGN_LIST_MAX]:
            col.label(text=obj.name, icon='MESH_DATA')
        if len(targets) > _ASSIGN_LIST_MAX:
            col.label(text=f"... and {len(targets) - _ASSIGN_LIST_MAX} more")
        if not targets:
            col.label(text="None (.sluggie imports are left alone)")
        if others:
            box.label(text=f"Left out: {_meshes(len(others))} on another Sluggies armature", icon='INFO')

    def execute(self, context):
        # OK starts from the state before the dialog: the preview only showed
        # what this applies.
        _end_assign_preview(keep_cache=True)
        try:
            return self._assign(context)
        finally:
            AdaptTransform.end_cache()
            _drop_enum_items('adapt_armature.armature')

    def cancel(self, context):
        _end_assign_preview()
        _drop_enum_items('adapt_armature.armature')

    def _assign(self, context):
        arm_obj = bpy.data.objects.get(self.armature) if self.armature != 'NONE' else None
        if arm_obj is None or arm_obj not in _sluggies_armatures(context):
            self.report({"ERROR"}, "Pick a Sluggies armature")
            return {"CANCELLED"}
        targets, skipped, others = self._targets(context, arm_obj)
        if not targets:
            where = "selected" if context.selected_objects else "in this scene"
            self.report({"WARNING"}, f"No meshes {where} to assign (.sluggie imports are left alone)")
            return {"CANCELLED"}

        # Anything that can fail runs before the scene changes for good; a
        # baked pose is taken back on failure.
        fit_lines = []
        matrix = Matrix.Identity(4)
        backups = {}
        settings = _assign_settings(self)
        try:
            _bake_assign_poses(context, targets, settings, backups)
            if self.align:
                matrix, fit_lines = _assign_fit(context, arm_obj, targets, settings)
        except ValueError as exc:
            _restore_meshes(backups)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except BaseException:
            _restore_meshes(backups)    # an unexpected error: still nothing half baked
            raise

        if settings['align_only']:
            moved = _aligned_objects(context, targets, settings)
            _move_all(moved, matrix)
            rigs = [obj.name for obj in moved if obj.type == 'ARMATURE']
            lines = [f"Aligned {_meshes(len(targets))} to {arm_obj.name}, not parented"
                     + (f" (moved along: {', '.join(rigs)})" if rigs else "")] + fit_lines
            self.report({"INFO"}, "; ".join(lines))
            return {"FINISHED"}

        # World matrices first: a target may be the parent of another.
        worlds = {obj: matrix @ obj.matrix_world for obj in targets}
        retargeted = added = removed = 0
        for obj, world in worlds.items():
            obj.parent = arm_obj
            obj.parent_type = 'OBJECT'
            obj.matrix_parent_inverse = arm_obj.matrix_world.inverted()
            obj.matrix_world = world
            modifiers = [m for m in obj.modifiers if m.type == 'ARMATURE']
            if not modifiers:
                modifiers = [obj.modifiers.new(name="Armature", type='ARMATURE')]
                added += 1
            elif modifiers[0].object is not arm_obj:
                retargeted += 1
            modifiers[0].object = arm_obj
            # Several Armature modifiers on one armature would deform twice.
            for extra in modifiers[1:]:
                obj.modifiers.remove(extra)
                removed += 1

        lines = [f"Parented {_meshes(len(targets))} to {arm_obj.name}"]
        if backups:
            lines.append(f"Kept the old rig's pose on {_meshes(len(backups))}")
        lines += fit_lines
        if retargeted:
            lines.append(f"Armature modifier of {_meshes(retargeted)} now follows {arm_obj.name}")
        if added:
            lines.append(f"Added an Armature modifier to {_meshes(added)}")
        if removed:
            lines.append(f"Removed {removed} extra Armature modifier{'s' if removed != 1 else ''}")
        if context.selected_objects and skipped:
            lines.append(f"Skipped {_meshes(len(skipped))} from a .sluggie")
        if others:
            lines.append(f"Skipped {_meshes(len(others))} on another Sluggies armature (select them to move them)")
        self.report({"INFO"}, "; ".join(lines))  # status bar only, no summary popup
        return {"FINISHED"}


def _selected_external_meshes(context):
    return AdaptCleanup.cleanup_targets(context.selected_objects)[0]


class SLUGGIES_OT_adapt_transform(bpy.types.Operator):
    """Scale and Position step of the 'Adapt' box (AdaptTransform)"""
    bl_idname = "sluggies.adapt_transform"
    bl_label = "Scale and Position"
    bl_description = _ADAPT_TRANSFORM_DESCRIPTION
    bl_options = {"REGISTER", "UNDO"}

    up_axis: EnumProperty(  # type: ignore[valid-type]
        name="Up",
        description="Direction the model's head points to now",
        items=AdaptTransform.AXIS_ITEMS,
    )
    front_axis: EnumProperty(  # type: ignore[valid-type]
        name="Facing",
        description="Direction the model faces now",
        items=AdaptTransform.AXIS_ITEMS,
    )
    use_hands: BoolProperty(  # type: ignore[valid-type]
        name="Match Hands",
        description=("Let hand height and spread (from hand/finger vertex groups) adjust the scale a "
                     "little; the overall height still counts most"),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Switch to Object Mode")
            return False
        if not _selected_external_meshes(context):
            cls.poll_message_set("Select the meshes to fit (not ones from a .sluggie)")
            return False
        return True

    def _armature(self, context, meshes):
        """The Sluggies armature the meshes were assigned to (most of them),
        else a selected one, else the scene's only one."""
        armatures = _sluggies_armatures(context)
        followed = [a for obj in meshes for a in dict.fromkeys(_followed_armatures(obj)) if a in armatures]
        if followed:
            return max(set(followed), key=followed.count)
        selected = [a for a in armatures if a in context.selected_objects]
        if len(selected) == 1:
            return selected[0]
        return armatures[0] if len(armatures) == 1 else None

    def execute(self, context):
        meshes = _selected_external_meshes(context)
        arm_obj = self._armature(context, meshes)
        if arm_obj is None:
            self.report({"ERROR"}, "Assign the meshes to a Sluggies armature first (step 2)")
            return {"CANCELLED"}
        try:
            matrix, lines = AdaptTransform.fit(
                arm_obj, meshes, context.scene.objects, context.evaluated_depsgraph_get(),
                self.up_axis, self.front_axis, self.use_hands)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        # Selected objects other than the Sluggies model's, without those
        # whose parent is moved already (they follow it).
        armatures = _sluggies_armatures(context)
        movable = {o for o in context.selected_objects
                   if o not in armatures and not (o.type == 'MESH' and AdaptCleanup.is_sluggies_mesh(o))}
        roots = _without_followers(movable)
        worlds = {o: o.matrix_world.copy() for o in roots}
        for obj, world in worlds.items():
            obj.matrix_world = matrix @ world

        lines.insert(0, f"Fitted {len(roots)} object{'s' if len(roots) != 1 else ''} to {arm_obj.name}")
        lines.append("Wrong way round? Set Up/Facing in the Adjust Last Operation panel")
        _report_adapt_summary(self, context, "Scale and Position", lines)
        return {"FINISHED"}
