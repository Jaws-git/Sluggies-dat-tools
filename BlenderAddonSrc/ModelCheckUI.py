"""Check Models dialog: one box per object with its findings, a Fix
checkbox where a fix exists (none ticked by default) and a Go to button.
OK runs the ticked fixes as one undo step, then reports what is left."""

import json

import bpy
from bpy.props import BoolProperty, CollectionProperty, StringProperty

from . import ModelCheck
from . import ModelCheckCore as core

SEVERITY_ICON = {core.ERROR: 'CANCEL', core.WARNING: 'ERROR', core.INFO: 'INFO'}
DIALOG_MIN_WIDTH = 450
# Label column / Fix-or-Go-to column.
SPLIT_FACTOR = 0.7


class SluggiesCheckRow(bpy.types.PropertyGroup):
    object_name: StringProperty()  # type: ignore[valid-type]
    severity: StringProperty()  # type: ignore[valid-type]
    text: StringProperty()  # type: ignore[valid-type]
    detail: StringProperty()  # type: ignore[valid-type]
    fix_id: StringProperty()  # type: ignore[valid-type]
    fix_args: StringProperty()  # type: ignore[valid-type]
    fix: BoolProperty(name="Fix", description="Fix this automatically on OK", default=False)  # type: ignore[valid-type]


class SluggiesCheckSize(bpy.types.PropertyGroup):
    model_name: StringProperty()  # type: ignore[valid-type]
    text: StringProperty()  # type: ignore[valid-type]


class SLUGGIES_OT_check_detail(bpy.types.Operator):
    """A finding's label; the tooltip holds the long explanation and a click
    copies it to the Info editor."""
    bl_idname = "sluggies.check_detail"
    bl_label = "Finding"
    bl_options = {'INTERNAL'}

    detail: StringProperty()  # type: ignore[valid-type]

    @classmethod
    def description(cls, context, properties):
        return properties.detail

    def execute(self, context):
        self.report({'INFO'}, self.detail)
        return {'FINISHED'}


def _unhide_collections(layer_collection, obj):
    """Un-exclude and unhide every layer collection on the path to *obj*."""
    if obj.name in layer_collection.collection.objects:
        return True
    for child in layer_collection.children:
        if _unhide_collections(child, obj):
            child.exclude = False
            child.hide_viewport = False
            child.collection.hide_viewport = False
            return True
    return False


class SLUGGIES_OT_check_goto(bpy.types.Operator):
    bl_idname = "sluggies.check_goto"
    bl_label = "Go to"
    bl_description = "Unhide, select and frame this object"
    bl_options = {'INTERNAL'}

    object_name: StringProperty()  # type: ignore[valid-type]

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if obj is None:
            self.report({'WARNING'}, f"{self.object_name} no longer exists")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        _unhide_collections(context.view_layer.layer_collection, obj)
        obj.hide_viewport = False
        obj.hide_set(False)
        for other in context.view_layer.objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                region = next(r for r in area.regions if r.type == 'WINDOW')
                with context.temp_override(area=area, region=region):
                    bpy.ops.view3d.view_selected()
                break
        return {'FINISHED'}


def _fill_rows(context):
    wm = context.window_manager
    rows, sizes = wm.sluggies_check_rows, wm.sluggies_check_sizes
    rows.clear()
    sizes.clear()
    scopes = ModelCheck.gather_scopes(context)
    findings = ModelCheck.run_checks(context, scopes)
    for finding in findings:
        row = rows.add()
        row.object_name = finding.object_name
        row.severity = finding.severity
        row.text = finding.text
        row.detail = finding.detail
        row.fix_id = finding.fix_id or ""
        row.fix_args = json.dumps(finding.fix_args)
    for scope in scopes:
        try:
            estimate = ModelCheck.estimate_scope_size(scope)
        except Exception as exc:  # an estimate must never stop the check
            print(f"Sluggies: size estimate for {scope.name} failed: {exc}")
            estimate = None
        if estimate is not None:
            size = sizes.add()
            size.model_name = scope.name
            size.text = core.size_line(*estimate)
    return scopes, findings


class SLUGGIES_OT_check_models(bpy.types.Operator):
    """Check every imported Sluggies model for problems export, the patcher or
    the game would refuse, and fix the ones that can be fixed safely"""
    bl_idname = "sluggies.check_models"
    bl_label = "Check Models"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        scopes, findings = _fill_rows(context)
        if not scopes:
            self.report({'WARNING'}, "No imported Sluggies model in this scene")
            return {'CANCELLED'}
        if not findings:
            sizes = [s.text for s in context.window_manager.sluggies_check_sizes]

            def draw_clean(menu, _context):
                menu.layout.label(text="Models are good to go!", icon='CHECKMARK')
                for line in sizes:
                    menu.layout.label(text=line, icon='DISK_DRIVE')
            # A plain popup: no OK/Cancel, and CANCELLED pushes no undo step.
            context.window_manager.popup_menu(draw_clean, title="Check Models")
            return {'CANCELLED'}
        rows = context.window_manager.sluggies_check_rows
        longest = max([len(r.text) for r in rows] + [len(r.object_name) + 8 for r in rows])
        width = max(DIALOG_MIN_WIDTH, int((longest * 7 + 140) * context.preferences.view.ui_scale))
        return context.window_manager.invoke_props_dialog(self, width=width)

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        groups = {}
        for row in wm.sluggies_check_rows:
            groups.setdefault(row.object_name, []).append(row)
        for name, group in groups.items():
            box = layout.box()
            split = box.split(factor=SPLIT_FACTOR)
            split.label(text=name, icon='OBJECT_DATA')
            split.operator(SLUGGIES_OT_check_goto.bl_idname, text="Go to",
                           icon='VIEWZOOM').object_name = name
            for row in group:
                split = box.split(factor=SPLIT_FACTOR)
                split.operator(SLUGGIES_OT_check_detail.bl_idname, text=row.text,
                               icon=SEVERITY_ICON.get(row.severity, 'INFO'), emboss=False).detail = row.detail
                if row.fix_id:
                    split.prop(row, "fix", text="Fix automatically")
                else:
                    split.label(text="")
        sizes = wm.sluggies_check_sizes
        if len(sizes):
            layout.separator()
            for size in sizes:
                layout.label(text=f"{size.model_name}: {size.text}", icon='DISK_DRIVE')

    def execute(self, context):
        wm = context.window_manager
        requests = [(row.fix_id, json.loads(row.fix_args))
                    for row in wm.sluggies_check_rows if row.fix_id and row.fix]
        if not requests:
            return {'CANCELLED'}
        fixed, failed, messages = ModelCheck.run_fixes(context, requests)
        _scopes, left = _fill_rows(context)
        summary = core.fix_summary(fixed, failed, len(left))
        self.report({'INFO'}, summary)
        for message in messages:
            self.report({'WARNING'}, message)

        def draw_summary(menu, _context):
            menu.layout.label(text=summary)
            for message in messages:
                menu.layout.label(text=message, icon='ERROR')
        wm.popup_menu(draw_summary, title="Check Models", icon='INFO')
        return {'FINISHED'}


CLASSES = (
    SluggiesCheckRow, SluggiesCheckSize, SLUGGIES_OT_check_detail, SLUGGIES_OT_check_goto,
    SLUGGIES_OT_check_models,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.sluggies_check_rows = CollectionProperty(type=SluggiesCheckRow)
    bpy.types.WindowManager.sluggies_check_sizes = CollectionProperty(type=SluggiesCheckSize)


def unregister():
    del bpy.types.WindowManager.sluggies_check_sizes
    del bpy.types.WindowManager.sluggies_check_rows
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
