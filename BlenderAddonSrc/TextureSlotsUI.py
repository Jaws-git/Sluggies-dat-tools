"""Manage texture slots: the High model's textures and which one each
Low-poly material shows.

A `_L_` model draws with its High model's textures, by index. The dialog
lists the High model's slots and lets each Low material pick one; the
material's image switches to that slot's PNG right away (viewport preview),
OK keeps it and Cancel puts back the images the dialog opened with. The Low
model's export writes the new image as a texture reassignment
(``DesiredTextureAssignments``).
"""

import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty

from . import FieldCodec
from . import ModelCheckCore
from . import SluggiePath
from . import TextureSlotsCore
from .ExportSluggies import _connected_image_texture_nodes


def _sluggies_armatures(context):
    return [
        o for o in context.view_layer.objects
        if o.type == 'ARMATURE' and 'SluggiesBoneMetadataVersion' in o.keys()
    ]


def _target_armature(context):
    """The Sluggies armature of the active object (itself, or a mesh's parent)."""
    obj = context.active_object
    if obj is None:
        return None
    for candidate in (obj, obj.parent):
        if candidate is not None and candidate.type == 'ARMATURE' \
                and 'SluggiesBoneMetadataVersion' in candidate.keys():
            return candidate
    return None


def _lod_pair(context, arm_obj):
    """``(high armature or None, low armature or None)`` for *arm_obj* and
    its partner among the scene's Sluggies armatures."""
    path = SluggiePath.resolve(arm_obj)
    if not path:
        return None, None
    others = [o for o in _sluggies_armatures(context) if o is not arm_obj]
    if TextureSlotsCore.is_low_path(path):
        high = _pick_partner_armature([o for o in others
                                       if TextureSlotsCore.is_lod_pair(SluggiePath.resolve(o), path)], path)
        return high, arm_obj
    low = _pick_partner_armature([o for o in others
                                  if TextureSlotsCore.is_lod_pair(path, SluggiePath.resolve(o))], path)
    return arm_obj, low


def _pick_partner_armature(matches, path):
    """The armature among *matches* (all from the partner folder) whose file
    pairs with *path*: several variants of one model may be loaded. The
    first match when the rule leaves several."""
    if len(matches) <= 1:
        return matches[0] if matches else None
    by_path = {}
    for obj in matches:
        by_path.setdefault(SluggiePath.resolve(obj), obj)
    return by_path.get(ModelCheckCore.pick_partner(list(by_path), path), matches[0])


def _low_materials(low_arm):
    """``(material, image node)`` for every material of the Low model's
    meshes that shows exactly one image, in slot order, each once."""
    seen = set()
    found = []
    for obj in sorted(low_arm.children_recursive, key=lambda o: o.name):
        if obj.type != 'MESH':
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or mat.name in seen:
                continue
            seen.add(mat.name)
            nodes = _connected_image_texture_nodes(mat)
            if len(nodes) == 1:
                found.append((mat, nodes[0]))
    return found


def _image_file_name(node):
    image = getattr(node, 'image', None)
    if image is None:
        return ''
    path = image.filepath_raw or image.filepath or ''
    return os.path.basename(os.path.normpath(bpy.path.abspath(path))) if path else ''


# The open dialog's High tex/ folder and slot file names by index: a Low
# item's update callback sees only the item, not the operator.
_preview = {'tex_dir': '', 'slots': {}}


def _material_image_node(material_name):
    mat = bpy.data.materials.get(material_name)
    nodes = _connected_image_texture_nodes(mat) if mat is not None else []
    return nodes[0] if len(nodes) == 1 else None


def _original_image(item):
    return bpy.data.images.get(item.original_image) if item.original_image else None


def _set_node_image(node, image):
    if node.image != image:
        node.image = image


def _preview_texture_index(item, context):
    """Show the picked slot's PNG on the material right away; the image the
    dialog opened with for its original slot, or a slot that is missing."""
    node = _material_image_node(item.material_name)
    if node is None:
        return
    image = None
    if item.texture_index != item.original_index:
        file_name = _preview['slots'].get(item.texture_index)
        path = os.path.join(_preview['tex_dir'], file_name) if file_name else ''
        if path and os.path.isfile(path):
            image = bpy.data.images.load(path, check_existing=True)
    _set_node_image(node, image or _original_image(item))


class SluggiesTextureSlot(bpy.types.PropertyGroup):
    index: IntProperty()  # type: ignore[valid-type]
    file_name: StringProperty()  # type: ignore[valid-type]
    added: BoolProperty()  # type: ignore[valid-type]
    missing: BoolProperty()  # type: ignore[valid-type]


class SluggiesLowTextureItem(bpy.types.PropertyGroup):
    material_name: StringProperty()  # type: ignore[valid-type]
    original_index: IntProperty(default=-1)  # type: ignore[valid-type]
    original_image: StringProperty()  # type: ignore[valid-type]
    texture_index: IntProperty(  # type: ignore[valid-type]
        name="Texture index", min=0,
        description="High-model texture slot this low-poly material shows",
        update=_preview_texture_index,
    )


class SLUGGIES_OT_manage_texture_slots(bpy.types.Operator):
    """List the High model's texture slots and pick the one each Low-poly material shows"""
    bl_idname = "sluggies.manage_texture_slots"
    bl_label = "Manage texture slots"
    bl_description = ("List the high-poly model's texture slots and choose which slot each "
                      "low-poly material uses")
    bl_options = {"UNDO"}

    slots: CollectionProperty(type=SluggiesTextureSlot)  # type: ignore[valid-type]
    items: CollectionProperty(type=SluggiesLowTextureItem)  # type: ignore[valid-type]
    high_name: StringProperty()  # type: ignore[valid-type]
    low_name: StringProperty()  # type: ignore[valid-type]
    tex_dir: StringProperty()  # type: ignore[valid-type]
    hint: StringProperty()  # type: ignore[valid-type]

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
        self.slots.clear()
        self.items.clear()
        self.hint = ''
        high_arm, low_arm = _lod_pair(context, _target_armature(context))
        self.high_name = high_arm.name if high_arm else ''
        self.low_name = low_arm.name if low_arm else ''
        if high_arm is None:
            self.hint = ("No high-poly model of this character is loaded. Import its "
                         ".sluggie too: the low-poly model uses the high-poly model's "
                         "textures, so its slots are listed from there.")
        elif low_arm is None:
            self.hint = ("No low-poly (L_) model of this character is loaded. Import it "
                         "to choose the textures its materials use.")
        else:
            high_path = SluggiePath.resolve(high_arm)
            try:
                model = FieldCodec.load_sluggie(high_path).get('SluggiesModel', {})
            except (OSError, ValueError) as exc:
                self.hint = f"Could not read {high_path}: {exc}"
                model = {}
            self.tex_dir = os.path.join(os.path.dirname(os.path.abspath(high_path)), 'tex')
            slots = TextureSlotsCore.texture_slots(model)
            _preview['tex_dir'] = self.tex_dir
            _preview['slots'] = {index: file_name for index, file_name, _added in slots}
            for slot in slots:
                entry = self.slots.add()
                entry.index, entry.file_name, entry.added = slot
                # checked once here, not in draw(), which runs on every hover
                entry.missing = not os.path.isfile(os.path.join(self.tex_dir, entry.file_name))
            if not slots and not self.hint:
                self.hint = f"{high_arm.name} has no texture slots."
            for mat, node in _low_materials(low_arm) if slots else []:
                file_name = _image_file_name(node)
                current = TextureSlotsCore.slot_for_file(slots, file_name)
                if current is None and not file_name:
                    # no PNG found on import: the material still binds its own index
                    current = int(mat.get('TextureIndex', -1))
                if current is None or not 0 <= current < len(slots):
                    continue  # an image that is no High texture: not ours to rebind
                item = self.items.add()
                item.material_name = mat.name
                # cached before texture_index: its update callback reads them
                item.original_index = current
                item.original_image = node.image.name if node.image else ''
                item.texture_index = current
        return context.window_manager.invoke_props_dialog(self, width=690)

    def _slot_name(self, index):
        for slot in self.slots:
            if slot.index == index:
                return slot.file_name
        return None

    def draw(self, context):
        layout = self.layout
        if self.hint:
            col = layout.column(align=True)
            for number, line in enumerate(_wrap(self.hint, 90)):
                col.label(text=line, icon='INFO' if number == 0 else 'BLANK1')
            return

        layout.label(text=f"High-poly texture slots ({self.high_name})")
        box = layout.box()
        split = box.split(factor=0.12)
        left, right = split.column(align=True), split.column(align=True)
        left.label(text="Slot")
        right.label(text="Texture")
        for slot in self.slots:
            left.label(text=str(slot.index))
            right.label(text=slot.file_name + ("  (added)" if slot.added else "")
                        + ("  (PNG missing in tex/)" if slot.missing else ""),
                        icon='ERROR' if slot.missing else 'NONE')

        layout.separator()
        layout.label(text=f"Low-poly materials ({self.low_name})")
        box = layout.box()
        if not self.items:
            box.label(text="No low-poly material shows one of these textures", icon='INFO')
        split = box.split(factor=0.5)
        left, right = split.column(align=True), split.column(align=True)
        if self.items:
            left.label(text="Material")
            right.label(text="Texture slot")
        for item in self.items:
            left.label(text=item.material_name)
            # the index field takes 20% of the column, the texture name the rest
            row = right.split(factor=0.2, align=True)
            row.prop(item, 'texture_index', text="")
            name = self._slot_name(item.texture_index)
            row.label(text=name or "no such slot", icon='NONE' if name else 'ERROR')
        layout.label(text="Export the low-poly model to apply; patch the high-poly model first.",
                     icon='INFO')

    def execute(self, context):
        if self.hint:
            return {"CANCELLED"}
        # The preview already set the images; this reports the result (and
        # sets them again, should an update have been missed).
        changed, errors = 0, []
        for item in self.items:
            if item.texture_index == item.original_index:
                continue
            file_name = self._slot_name(item.texture_index)
            node = _material_image_node(item.material_name)
            if file_name is None or node is None:
                errors.append(f"{item.material_name}: slot {item.texture_index} does not exist")
                continue
            path = os.path.join(self.tex_dir, file_name)
            if not os.path.isfile(path):
                errors.append(f"{item.material_name}: {path} not found")
                continue
            _set_node_image(node, bpy.data.images.load(path, check_existing=True))
            changed += 1
        for message in errors:
            self.report({"ERROR"}, message)
        if changed:
            self.report({"INFO"}, f"{changed} low-poly material(s) now use another texture slot; "
                                  "export the low-poly model to apply")
        return {"FINISHED"} if changed or not errors else {"CANCELLED"}

    def cancel(self, context):
        """Put back the images the dialog opened with."""
        for item in self.items:
            node = _material_image_node(item.material_name)
            if node is not None:
                _set_node_image(node, _original_image(item))


def _wrap(text, width):
    lines, line = [], ''
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        lines.append(line)
    return lines


_CLASSES = (SluggiesTextureSlot, SluggiesLowTextureItem, SLUGGIES_OT_manage_texture_slots)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
