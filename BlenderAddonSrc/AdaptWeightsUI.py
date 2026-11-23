"""Adapt Model, step Map Weights or Autorig. Registered by ``SluggiesToolsPanel.register``.

Never imports ``SluggiesToolsPanel``.
"""

import bpy
from bpy.props import (
    BoolProperty, EnumProperty, StringProperty,
)
from . import AdaptWeights
from . import SluggiePath
from .SceneModels import (
    _body_objects,
    _body_submesh,
    _selected_sluggies_armature,
)
from .HostBoneScene import (
    _stopped_after,
)
from .SurfacePlans import (
    _model_tex_dir,
)
from .AdaptSteps import (
    _ADAPT_STEPS,
    _report_adapt_summary,
)
from .SubmeshOps import (
    _ensure_armature_modifier,
    _parent_keeping_world,
)


_ADAPT_WEIGHT_METHODS = (
    ('MAP', "Map vertex groups",
     "Keep the old rig's weights: each vertex group is renamed to, or merged into, the nearest Sluggers bone"),
    ('AUTO', "Automatic weights",
     "Throw the old weights away and let Blender weight the body anew from how close it is to each bone"),
)


class SLUGGIES_OT_adapt_weights(bpy.types.Operator):
    """Map Weights or Autorig step of the 'Adapt' box (AdaptWeights)"""
    bl_idname = "sluggies.adapt_weights"
    bl_label = "Map Weights or Autorig"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'WEIGHTS')
    bl_options = {"REGISTER", "UNDO"}

    method: EnumProperty(  # type: ignore[valid-type]
        name="Method", items=_ADAPT_WEIGHT_METHODS, default='MAP',
    )
    whole_body: BoolProperty(  # type: ignore[valid-type]
        name="Redo the whole body",
        description="Also reweight the vertices that already have Sluggers bone weights "
                    "(the donor body the new mesh was joined into)",
        default=False,
    )
    remove_old_groups: BoolProperty(  # type: ignore[valid-type]
        name="Remove the old rig's vertex groups",
        description="Delete the vertex groups that are not Sluggers bones (those a modifier uses stay)",
        default=True,
    )
    armature_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]
    object_names: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]  # '\n'-joined
    mappable: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]
    surveys: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]  # '\n'-joined lines
    any_kept: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Switch to Object Mode")
            return False
        if _selected_sluggies_armature(context) is None:
            cls.poll_message_set("Select a Sluggies armature or one of its child objects")
            return False
        return True

    def _bodies(self, context, arm_obj):
        """The body objects to weight, or reports and returns None. With
        several, the ones with work left (old rig groups or vertices without
        a bone weight) unless none has any."""
        sluggie_path = SluggiePath.resolve(arm_obj) if arm_obj is not None else None
        if not sluggie_path:
            self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Map Weights or Autorig")
                        if arm_obj is not None else "The armature is gone")
            return None
        model, _tex_dir, error = _model_tex_dir(sluggie_path)
        if model is None:
            self.report({"ERROR"}, error)
            return None
        body = _body_submesh(model)
        bodies = _body_objects(context, arm_obj, body) if body is not None else []
        if not bodies:
            self.report({"ERROR"}, "This model has no skinned main body in the scene"
                        + (" (step 3 makes one)" if body is not None else ""))
            return None
        surveys = {obj: AdaptWeights.survey(obj, arm_obj) for obj in bodies}
        busy = [obj for obj, s in surveys.items() if s['open'] or s['sources']]
        return [(obj, surveys[obj]) for obj in (busy or bodies)]

    def invoke(self, context, event):
        arm_obj = _selected_sluggies_armature(context)
        found = self._bodies(context, arm_obj)
        if found is None:
            return {"CANCELLED"}
        self.armature_name = arm_obj.name
        self.object_names = "\n".join(obj.name for obj, _s in found)
        self.mappable = any(s['mappable'] for _obj, s in found)
        self.any_kept = any(s['open'] < s['vertices'] for _obj, s in found)
        self.method = 'MAP' if self.mappable else 'AUTO'
        self.whole_body = not any(s['open'] for _obj, s in found)
        lines = []
        for obj, s in found:
            lines.append(f"{obj.name}: {s['vertices']:,} vertices, "
                         f"{s['sources']} vertex group{'s' if s['sources'] != 1 else ''} from its old rig")
            if s['open'] < s['vertices']:
                lines.append(f"{s['vertices'] - s['open']:,} vertices already have Sluggers bone weights")
        self.surveys = "\n".join(lines)
        return context.window_manager.invoke_props_dialog(self, width=640)

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Armature: {self.armature_name}", icon='ARMATURE_DATA')
        box = layout.box()
        for line in self.surveys.split("\n"):
            box.label(text=line, icon='MESH_DATA' if ':' in line else 'BLANK1')
        col = layout.column()
        if self.mappable:
            col.prop(self, 'method', expand=True)
        else:
            col.label(text="Too few vertex groups from an old rig to map: automatic weights only", icon='INFO')
        description = next(desc for ident, _label, desc in _ADAPT_WEIGHT_METHODS if ident == self.method)
        col.label(text=description + ".")
        if self.method == 'AUTO':
            if self.any_kept:
                col.prop(self, 'whole_body')
            col.prop(self, 'remove_old_groups')

    def execute(self, context):
        arm_obj = bpy.data.objects.get(self.armature_name) if self.armature_name \
            else _selected_sluggies_armature(context)
        if arm_obj is None:
            self.report({"ERROR"}, "The armature is gone")
            return {"CANCELLED"}
        if self.object_names:
            objects = [bpy.data.objects[name] for name in self.object_names.split("\n")
                       if name in bpy.data.objects]
        else:  # run again from Adjust Last Operation or a script
            found = self._bodies(context, arm_obj)
            objects = [obj for obj, _s in found] if found is not None else []
        if not objects:
            self.report({"ERROR"}, "The main body is gone")
            return {"CANCELLED"}

        lines, results = [], []
        changed = False     # an earlier body was reweighted, or got its own mesh data
        try:
            for obj in objects:
                if obj.data.users > 1:
                    obj.data = obj.data.copy()
                    changed = True
                    lines.append(f"{obj.name}'s mesh data was shared, so it got its own copy")
                if self.method == 'MAP':
                    weights = AdaptWeights.read_weights(obj)    # one pass for both
                    if not AdaptWeights.survey(obj, arm_obj, weights)['mappable']:
                        lines.append(f"{obj.name}: too few vertex groups from an old rig to map, left as it was")
                        continue
                    result = AdaptWeights.map_groups(obj, arm_obj, weights)
                else:
                    result = AdaptWeights.autorig(context, obj, arm_obj, self.whole_body, self.remove_old_groups)
                    _ensure_armature_modifier(obj, arm_obj)
                    _parent_keeping_world(obj, arm_obj)
                changed = True
                lines += AdaptWeights.summary_lines(result)
                results.append(result)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            for line in lines if changed else ():
                self.report({"INFO"}, line)     # what was done before the error stays done
            return _stopped_after(changed)
        title = "Map Weights" if self.method == 'MAP' else "Automatic Weights"
        _report_adapt_summary(self, context, title, lines, AdaptWeights.warning_lines(objects, arm_obj, results))
        return {"FINISHED"}
