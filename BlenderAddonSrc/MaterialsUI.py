"""Manage Materials: the shader mode and specular strength of every material
of a character's High and Low models in one dialog.

Lists the materials of the active model and its LOD partner (whichever of
the two are loaded). A donor Type-7 surface gets a shader-mode drop-down
limited to the modes of its own class (lit or unlit, the in-place patcher's
guardrail), and a surface whose mode takes a specular strength gets a 0-255
field. OK writes them to the material's ``ShaderMode`` / ``SpecularStrength``,
and each model's export applies them.
"""

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty

from .TemplateSources import SHADER_MODE_DESCRIPTIONS, SPECULAR_SHADER_MODES
from .SluggiesToolsPanel import _find_target_armature as _target_armature, _no_target_armature_message
from .TextureSlotsUI import _lod_pair


# Type-7 shader FourCCs a donor surface can switch between, by class. Mirrors
# patch_inplace._LIGHTING_MODES / _NO_LIGHTING_MODES: a change across the two
# classes alters the vertex stride of the surface's primitive lists, so the
# patcher refuses it and the drop-down never offers it.
_LIT_MODES = tuple((mode, mode, SHADER_MODE_DESCRIPTIONS[mode])
                   for mode in ('Spec', 'RhSp', 'LhSp', 'GhSp', 'SpRf'))
_UNLIT_MODES = tuple((mode, mode, SHADER_MODE_DESCRIPTIONS[mode])
                     for mode in ('Shdw', 'Audi', 'Oeka'))
_MODE_CLASSES = {mode: modes for modes in (_LIT_MODES, _UNLIT_MODES) for mode, _n, _d in modes}


def _materials(arm_obj):
    """Every material of the armature's meshes, in slot order, each once."""
    seen = set()
    found = []
    for obj in sorted(arm_obj.children_recursive, key=lambda o: o.name):
        if obj.type != 'MESH':
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is not None and mat.name not in seen:
                seen.add(mat.name)
                found.append(mat)
    return found


def _is_type7(mat):
    """A Type-7 (shader) surface."""
    return mat.get('DisplayStateId') == 7


def _mode_editable(mat):
    """Whether export writes an edited ShaderMode back: a donor Type-7 surface
    in a known class. Add-material and custom-submesh surfaces take their
    shader from the template source, so an edit there would be ignored."""
    return (_is_type7(mat) and not mat.get('SluggiesNewSurface')
            and str(mat.get('ShaderMode') or '') in _MODE_CLASSES)


def _mode_items(self, _context):
    return _MODE_CLASSES.get(self.original_mode, _LIT_MODES)


class SluggiesMaterialItem(bpy.types.PropertyGroup):
    material_name: StringProperty()  # type: ignore[valid-type]
    low: BoolProperty()  # type: ignore[valid-type]
    new_surface: BoolProperty()  # type: ignore[valid-type]
    mode_editable: BoolProperty()  # type: ignore[valid-type]
    original_mode: StringProperty()  # type: ignore[valid-type]
    mode: EnumProperty(  # type: ignore[valid-type]
        name="Shader mode", items=_mode_items,
        description="Type-7 shader this surface draws with",
    )
    has_strength: BoolProperty()  # type: ignore[valid-type]
    original_strength: IntProperty(default=-1)  # type: ignore[valid-type]
    strength: IntProperty(  # type: ignore[valid-type]
        name="Specular strength", min=0, max=255,
        description="Specular highlight intensity (0 = none, 255 = maximum; linear)",
    )

    def effective_mode(self):
        return self.mode if self.mode_editable else self.original_mode

    def strength_applies(self):
        """A specular strength field: the surface has one and its (selected)
        mode reads byte 1 as a strength. A custom-submesh surface carries no
        ShaderMode; it only has SpecularStrength when its template is specular."""
        if not self.has_strength:
            return False
        mode = self.effective_mode()
        return not mode or mode in SPECULAR_SHADER_MODES


class SLUGGIES_OT_manage_materials(bpy.types.Operator):
    """Set the shader mode and specular strength of the High and Low model's materials"""
    bl_idname = "sluggies.manage_materials"
    bl_label = "Manage Materials"
    bl_description = ("List the high- and low-poly model's materials and set the shader "
                      "mode and specular strength of each")
    bl_options = {"UNDO"}

    items: CollectionProperty(type=SluggiesMaterialItem)  # type: ignore[valid-type]
    high_name: StringProperty()  # type: ignore[valid-type]
    low_name: StringProperty()  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if _target_armature(context) is None:
            cls.poll_message_set(_no_target_armature_message(context))
            return False
        return True

    def invoke(self, context, event):
        self.items.clear()
        target = _target_armature(context)
        high_arm, low_arm = _lod_pair(context, target)
        if high_arm is None and low_arm is None:
            high_arm = target  # no .sluggie path to tell High from Low: list it alone
        self.high_name = high_arm.name if high_arm else ''
        self.low_name = low_arm.name if low_arm else ''
        for arm_obj, low in ((high_arm, False), (low_arm, True)):
            for mat in _materials(arm_obj) if arm_obj is not None else []:
                item = self.items.add()
                item.material_name = mat.name
                item.low = low
                item.new_surface = bool(mat.get('SluggiesNewSurface'))
                if _is_type7(mat):
                    item.original_mode = str(mat.get('ShaderMode') or '')
                    item.mode_editable = _mode_editable(mat)
                    if item.mode_editable:
                        item.mode = item.original_mode
                if _is_type7(mat) and 'SpecularStrength' in mat:
                    item.has_strength = True
                    item.original_strength = int(mat['SpecularStrength'])
                    item.strength = item.original_strength
        return context.window_manager.invoke_props_dialog(self, width=560)

    @staticmethod
    def _columns(layout):
        split = layout.split(factor=0.5)
        name_col = split.column()
        rest = split.split(factor=0.5)
        return name_col, rest.column(), rest.column()

    def _draw_model(self, layout, low):
        name = self.low_name if low else self.high_name
        kind = "Low-poly" if low else "High-poly"
        layout.label(text=f"{kind} materials ({name})" if name else f"{kind} materials")
        box = layout.box()
        if not name:
            box.label(text=f"No {kind.lower()} model of this character is loaded", icon='INFO')
            return
        items = [item for item in self.items if item.low == low]
        if not items:
            box.label(text="No materials", icon='INFO')
            return
        col = box.column(align=True)
        name_col, mode_col, strength_col = self._columns(col.row())
        name_col.label(text="Material")
        mode_col.label(text="Shader mode")
        strength_col.label(text="Specular strength")
        for item in items:
            name_col, mode_col, strength_col = self._columns(col.row())
            name_col.label(text=item.material_name)
            if item.mode_editable:
                mode_col.prop(item, 'mode', text="")
            elif item.original_mode:
                mode_col.label(text=f"{item.original_mode} (template)" if item.new_surface
                               else item.original_mode)
            else:
                mode_col.label(text="from template" if item.has_strength else "-")
            if item.strength_applies():
                strength_col.prop(item, 'strength', text="")
            else:
                strength_col.label(text="no specular")

    def draw(self, context):
        layout = self.layout
        self._draw_model(layout, low=False)
        layout.separator()
        self._draw_model(layout, low=True)

    def execute(self, context):
        modes = {False: 0, True: 0}
        strengths = {False: 0, True: 0}
        for item in self.items:
            mat = bpy.data.materials.get(item.material_name)
            if mat is None:
                continue
            if item.mode_editable and item.mode != item.original_mode:
                mat['ShaderMode'] = item.mode
                modes[item.low] += 1
            if item.strength_applies() and item.strength != item.original_strength:
                mat['SpecularStrength'] = item.strength
                strengths[item.low] += 1
        for low in (False, True):
            changes = []
            if modes[low]:
                changes.append(f"shader mode on {modes[low]}")
            if strengths[low]:
                changes.append(f"specular strength on {strengths[low]}")
            if changes:
                kind = "low-poly" if low else "high-poly"
                self.report({"INFO"}, f"Changed {' and '.join(changes)} {kind} material(s); "
                                      f"export the {kind} model to apply")
        return {"FINISHED"}


_CLASSES = (SluggiesMaterialItem, SLUGGIES_OT_manage_materials)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
