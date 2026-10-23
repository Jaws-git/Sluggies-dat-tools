"""Manage specular strength: the specular strength of every material of a
character's High and Low models in one dialog.

Lists the materials of the active model and its LOD partner (whichever of
the two are loaded). A specular (Type-7) material gets a 0-255 field; OK
writes it to the material's ``SpecularStrength``, and each model's export
applies it.
"""

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty

from .TextureSlotsUI import _lod_pair, _sluggies_armatures, _target_armature


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


def _is_specular(mat):
    """A specular (Type-7) surface."""
    return mat.get('DisplayStateId') == 7


class SluggiesSpecularMaterialItem(bpy.types.PropertyGroup):
    material_name: StringProperty()  # type: ignore[valid-type]
    low: BoolProperty()  # type: ignore[valid-type]
    has_specular: BoolProperty()  # type: ignore[valid-type]
    original_strength: IntProperty(default=-1)  # type: ignore[valid-type]
    strength: IntProperty(  # type: ignore[valid-type]
        name="Specular strength", min=0, max=255,
        description="Specular highlight intensity (0 = none, 255 = maximum; linear)",
    )


class SLUGGIES_OT_manage_specular_strength(bpy.types.Operator):
    """Set the specular strength of the High and Low model's materials"""
    bl_idname = "sluggies.manage_specular_strength"
    bl_label = "Manage specular strength"
    bl_description = ("List the high- and low-poly model's materials and set the specular "
                      "strength of each specular one")
    bl_options = {"UNDO"}

    items: CollectionProperty(type=SluggiesSpecularMaterialItem)  # type: ignore[valid-type]
    high_name: StringProperty()  # type: ignore[valid-type]
    low_name: StringProperty()  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if _target_armature(context) is None:
            if hasattr(cls, 'poll_message_set'):
                cls.poll_message_set(
                    "Select an armature (or one of its meshes) as the active object"
                    if _sluggies_armatures(context) else "Import a Sluggies model first")
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
                item.has_specular = _is_specular(mat)
                if item.has_specular:
                    item.original_strength = int(mat.get('SpecularStrength', 0))
                    item.strength = item.original_strength
        return context.window_manager.invoke_props_dialog(self, width=480)

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
        split = box.split(factor=0.65)
        left, right = split.column(align=True), split.column(align=True)
        left.label(text="Material")
        right.label(text="Specular strength")
        for item in items:
            left.label(text=item.material_name)
            if item.has_specular:
                right.prop(item, 'strength', text="")
            else:
                right.label(text="no specular")

    def draw(self, context):
        layout = self.layout
        self._draw_model(layout, low=False)
        layout.separator()
        self._draw_model(layout, low=True)

    def execute(self, context):
        changed = {False: 0, True: 0}
        for item in self.items:
            if not item.has_specular or item.strength == item.original_strength:
                continue
            mat = bpy.data.materials.get(item.material_name)
            if mat is not None:
                mat['SpecularStrength'] = item.strength
                changed[item.low] += 1
        for low, count in changed.items():
            if count:
                kind = "low-poly" if low else "high-poly"
                self.report({"INFO"}, f"Specular strength changed on {count} {kind} material(s); "
                                      f"export the {kind} model to apply")
        return {"FINISHED"}


_CLASSES = (SluggiesSpecularMaterialItem, SLUGGIES_OT_manage_specular_strength)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
