"""Export (High+Low): exports the High and the Low model of the selected armature in one step. Registered
by ``SluggiesToolsPanel.register``.

Never imports ``SluggiesToolsPanel``.
"""

import os
import bpy
from bpy.props import (
    BoolProperty, StringProperty,
)
from . import ModelCheckCore
from . import SluggiePath
from .ExportSluggies import SLUGGIES_OT_export, model_meshes
from .SceneModels import (
    _draw_cached,
    _high_low_pair,
    _selected_sluggies_armature,
)
from .AdaptSteps import (
    _ADAPT_STEPS,
)


class SLUGGIES_OT_export_high_low(bpy.types.Operator):
    """Export (High+Low) step of the 'Adapt' box: both models into .sluggie
    files of one name"""
    bl_idname = "sluggies.export_high_low"
    bl_label = "Export (High+Low)"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'EXPORT')
    bl_options = {"UNDO"}

    name: StringProperty(  # type: ignore[valid-type]
        name="Name",
        description=("File name of the High model's .sluggie (without .sluggie); the Low model's is its "
                     "offset, L_ and this name without its offset. An existing name is overwritten, a "
                     "new one is created next to the current files"),
    )
    use_custom_normals: BoolProperty(  # type: ignore[valid-type]
        name="Overwrite Normals",
        description=SLUGGIES_OT_export.__annotations__['use_custom_normals'].keywords['description'],
        default=False,
    )
    reimport_textures: BoolProperty(  # type: ignore[valid-type]
        name="Reimport textures from tex folder",
        description=SLUGGIES_OT_export.__annotations__['reimport_textures'].keywords['description'],
        default=False,
    )
    high_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]
    low_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Switch to Object Mode")
            return False
        arm_obj = _selected_sluggies_armature(context)
        if arm_obj is None:
            cls.poll_message_set("Select the High or Low model's armature or one of its child objects")
            return False
        high, _low, problem = _high_low_pair(context, arm_obj)
        if high is None:
            cls.poll_message_set(problem)
            return False
        return True

    def _models(self):
        return bpy.data.objects.get(self.high_name), bpy.data.objects.get(self.low_name)

    def _targets(self):
        high, low = self._models()
        return ModelCheckCore.pair_export_paths(
            SluggiePath.resolve(high, relink=False), SluggiePath.resolve(low, relink=False), self.name)

    def invoke(self, context, event):
        high, low, problem = _high_low_pair(context, _selected_sluggies_armature(context))
        if high is None:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        self.high_name, self.low_name = high.name, low.name
        # The High model's last exported (or imported) file.
        name = os.path.basename(SluggiePath.resolve(high, relink=False))
        self.name = name[:-len('.sluggie')] if name.lower().endswith('.sluggie') else name
        return context.window_manager.invoke_props_dialog(self, width=520)

    def _draw_lines(self, context):
        """[(text, icon)] the dialog lists under the name."""
        try:
            targets = self._targets()
        except ValueError as exc:
            return [(str(exc), 'ERROR')]
        lines = []
        for model, path in zip(self._models(), targets):
            state = "overwrite" if os.path.isfile(path) else "new"
            lines.append((f"{model.name}: {os.path.basename(path)} ({state})", 'FILE'))
            hidden = [obj.name for obj in [model] + model_meshes(context, model)[1] if not obj.visible_get()]
            if hidden:
                lines.append((f"Unhidden before the export: {', '.join(hidden)}", 'HIDE_OFF'))
        return lines

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'name')
        col = layout.column(align=True)
        key = ('export_high_low', context.view_layer.as_pointer(), self.high_name, self.low_name, self.name)
        for text, icon in _draw_cached(key, lambda: self._draw_lines(context)):
            col.label(text=text, icon=icon)
        layout.prop(self, 'use_custom_normals')
        layout.prop(self, 'reimport_textures')

    def execute(self, context):
        high, low = self._models()
        if high is None or low is None:
            self.report({"ERROR"}, "Select the High or Low model again")
            return {"CANCELLED"}
        try:
            targets = self._targets()
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        # The exporter skips hidden meshes and a hidden armature can't be
        # selected: unhide both models, then check both before writing either.
        # A failed export hides them again: CANCELLED must leave the scene as it was.
        unhidden = []       # (object, hide_get, hide_viewport) before
        for arm_obj in (high, low):
            for obj in [arm_obj] + model_meshes(context, arm_obj)[1]:
                if not obj.visible_get():
                    unhidden.append((obj, obj.hide_get(), obj.hide_viewport))
                    obj.hide_set(False)
                    obj.hide_viewport = False

        def hide_again():
            for obj, hidden, hidden_viewport in unhidden:
                obj.hide_viewport = hidden_viewport
                obj.hide_set(hidden)

        for arm_obj in (high, low):
            if not arm_obj.visible_get() or not model_meshes(context, arm_obj)[0]:
                hide_again()
                self.report({"ERROR"}, f"{arm_obj.name} or its meshes are in a hidden collection: "
                                       "show it and export again")
                return {"CANCELLED"}
        selected, active = list(context.selected_objects), context.view_layer.objects.active
        written = []
        try:
            # High first: the Low export borrows the High file's textures.
            # The armature alone: the exporter takes all its visible meshes.
            for arm_obj, path in zip((high, low), targets):
                for obj in context.selected_objects:
                    obj.select_set(False)
                arm_obj.select_set(True)
                context.view_layer.objects.active = arm_obj
                try:
                    result = bpy.ops.sluggies.export_json(
                        'EXEC_DEFAULT', filepath=path, exclude_unselected=True,
                        use_custom_normals=self.use_custom_normals, reimport_textures=self.reimport_textures)
                except RuntimeError as exc:  # the exporter's ERROR report
                    result, why = set(), f": {str(exc).removeprefix('Error: ').strip()}"
                else:
                    why = "; see the messages above"
                if 'FINISHED' not in result:
                    done = f" ({written[0]} was written)" if written else ""
                    self.report({"ERROR"}, f"Export of {arm_obj.name} failed{done}{why}")
                    return {"CANCELLED"}
                written.append(os.path.basename(path))
        finally:
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in selected:
                if obj.name in context.view_layer.objects:
                    obj.select_set(True)
            context.view_layer.objects.active = active
            if len(written) < 2:    # failed (or raised): selection first, then hide again
                hide_again()
        shown = f" (unhid {', '.join(obj.name for obj, _h, _v in unhidden)})" if unhidden else ""
        self.report({"INFO"}, f"Exported {high.name} to {written[0]} and {low.name} to {written[1]}{shown}")
        return {"FINISHED"}
