"""Adapt Model, step Convert Materials: the row property group, the template choices and the operator.
Registered by ``SluggiesToolsPanel.register``.

Never imports ``SluggiesToolsPanel``.
"""

import os
import bpy
from bpy.props import (
    BoolProperty, EnumProperty, IntProperty, StringProperty,
)
from . import AdaptCleanup
from . import ModelCheckCore
from . import SluggiePath
from . import TemplateSources
from . import TextureFolder
from .ExportTextures import (
    _custom_submesh_template_texture_index,
)
from .SceneModels import (
    _BODY_SUBMESH,
    _SURFACE_ID_RE,
    _body_objects,
    _body_submesh,
    _materials,
    _model_is_stadium,
    _reports_failure,
    _selected_sluggies_armature,
)
from .SurfacePlans import (
    _SLUGGIES_MATERIAL_PROPS,
    _apply_new_surface_plan,
    _ensure_surface_texture,
    _is_added_surface_of,
    _is_primary_surface_of,
    _make_submesh_materials,
    _model_images,
    _model_tex_dir,
    _surface_ids_in_file,
    _template_shader_mode,
    _template_wrap_modes,
)
from .AdaptSteps import (
    _ADAPT_STEPS,
    _report_adapt_summary,
)


# Convert Materials: one row per material of the main body or a rigid custom
# submesh that is not a surface yet, on the operator while the dialog is open.
# Label suffix of the built-ins that share a shader mode.
_BUILTIN_VARIANT_LABELS = {
    'stadium_shdw_opaque_v1': " (opaque)",
    'stadium_shdw_cutout_v1': " (cut-out)",
}


# Drop-down items per stadium flag, kept alive as Blender requires.
_CONVERT_TEMPLATE_ITEMS = {}


def _convert_template_items(stadium):
    """(TemplateSource, shader mode label, tooltip) for each built-in
    template a converted material can take."""
    items = _CONVERT_TEMPLATE_ITEMS.get(stadium)
    if items is None:
        items = []
        for choice in TemplateSources.build_template_source_choices((), stadium):
            template = TemplateSources.BUILTIN_TEMPLATES[choice.argument]
            variant = _BUILTIN_VARIANT_LABELS.get(choice.argument)
            description = template.description if variant else \
                TemplateSources.SHADER_MODE_DESCRIPTIONS.get(template.shader_mode, template.description)
            items.append((choice.template_source, template.shader_mode + (variant or ""), description))
        items = _CONVERT_TEMPLATE_ITEMS[stadium] = tuple(items)
    return items


def _convert_row_template_items(self, _context):
    return _convert_template_items(self.stadium)


class SluggiesConvertMaterialRow(bpy.types.PropertyGroup):
    """One material of the Convert Materials dialog."""
    material_name: StringProperty()  # type: ignore[valid-type]  # '' = a new one is made
    object_name: StringProperty()  # type: ignore[valid-type]  # rigid custom submesh; '' = main body
    stadium: BoolProperty()  # type: ignore[valid-type]
    template: EnumProperty(  # type: ignore[valid-type]
        name="Shader mode", items=_convert_row_template_items,
        description="Shader the surface draws with (the built-in template it is made from)",
    )
    strength: IntProperty(  # type: ignore[valid-type]
        name="Specular strength", min=0, max=255,
        default=TemplateSources.BUILTIN_DEFAULT_SPECULAR_STRENGTH,
        description="Specular highlight intensity (0 = none, 255 = maximum; linear)",
    )

    def specular(self):
        name = self.template.partition(':')[2]
        template = TemplateSources.BUILTIN_TEMPLATES.get(name)
        return template is not None and template.shader_mode in TemplateSources.SPECULAR_SHADER_MODES


def _is_body_surface(mat):
    """A surface the body exports already: an imported sm0 one or an
    Add-material surface of sm0."""
    if mat.get('SluggiesNewSurface'):
        return mat.get('SluggiesSurfaceOwner') == f'sm{_BODY_SUBMESH}' \
            and all(prop in mat for prop in ModelCheckCore.NEW_SURFACE_PROPS)
    match = _SURFACE_ID_RE.match(str(mat.get('SurfaceId') or ''))
    return match is not None and int(match.group(1)) == _BODY_SUBMESH


def _convert_material_targets(context, arm_obj, model):
    """[(material or None, rigid custom submesh or None)]: each material of
    *arm_obj*'s main body (None) and rigid custom submeshes that is not one
    of its surfaces yet, once per owner. A rigid submesh without any
    material is listed with None: a new one is made for it."""
    body = _body_submesh(model)
    targets = []
    body_materials = [slot.material for obj in (_body_objects(context, arm_obj, body) if body else ())
                      for slot in obj.material_slots if slot.material is not None]
    targets += [(mat, None) for mat in dict.fromkeys(body_materials) if not _is_body_surface(mat)]
    for obj in sorted(context.view_layer.objects, key=lambda o: o.name):
        if obj.type != 'MESH' or obj.parent is not arm_obj or not obj.get('SluggiesCustomSubmesh'):
            continue
        custom_submesh_id = str(obj.get('CustomSubmeshId') or '')
        materials = list(dict.fromkeys(slot.material for slot in obj.material_slots if slot.material is not None))
        targets += [(mat, obj) for mat in materials
                    if not (_is_primary_surface_of(mat, custom_submesh_id)
                            or _is_added_surface_of(mat, custom_submesh_id))]
        if not materials:
            targets.append((None, obj))
    return targets


def _material_meshes():
    """{material: set of meshes whose slots hold it}, from one walk over
    bpy.data.meshes, for `_own_material` calls in a loop."""
    users = {}
    for mesh in bpy.data.meshes:
        for slot_material in mesh.materials:
            if slot_material is not None:
                users.setdefault(slot_material, set()).add(mesh)
    return users


def _own_material(mat, meshes, users=None):
    """*mat*, or a copy of it put in *meshes*' slots when a library or any
    other mesh uses it too, so converting it changes nothing else. *users*
    is `_material_meshes()`, kept up to date here when a copy is made."""
    if users is None:
        shared = mat.library is not None or any(
            mesh not in meshes and mat in mesh.materials[:] for mesh in bpy.data.meshes)
    else:
        shared = mat.library is not None or any(
            mesh not in meshes for mesh in users.get(mat, ()))
    if not shared:
        return mat
    copy = mat.copy()
    for mesh in meshes:
        replaced = False
        for index, slot_material in enumerate(mesh.materials):
            if slot_material == mat:
                mesh.materials[index] = copy
                replaced = True
        if replaced and users is not None:
            users.get(mat, set()).discard(mesh)
            users.setdefault(copy, set()).add(mesh)
    return copy


class SLUGGIES_OT_adapt_materials(bpy.types.Operator):
    """Convert Materials step of the 'Adapt' box"""
    bl_idname = "sluggies.adapt_materials"
    bl_label = "Convert Materials"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'MATERIALS')
    bl_options = {"UNDO"}

    rows: bpy.props.CollectionProperty(type=SluggiesConvertMaterialRow)  # type: ignore[valid-type]
    armature_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]
    collect_textures: BoolProperty(  # type: ignore[valid-type]
        name="Collect and create copies of all textures in project folder",
        description="Needs a saved .blend: the copies go in a folder next to it",
        default=True, options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Switch to Object Mode")
            return False
        if _selected_sluggies_armature(context) is None:
            cls.poll_message_set("Select a Sluggies armature or one of its child objects")
            return False
        return True

    def _model(self, arm_obj):
        """(model, tex dir) of *arm_obj*, or reports and returns None."""
        sluggie_path = SluggiePath.resolve(arm_obj) if arm_obj is not None else None
        if not sluggie_path:
            self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Convert Materials")
                        if arm_obj is not None else "The armature is gone")
            return None
        model, tex_dir, error = _model_tex_dir(sluggie_path)
        if error:
            self.report({"ERROR"}, error)
            return None
        return model, tex_dir

    def invoke(self, context, event):
        arm_obj = _selected_sluggies_armature(context)
        found = self._model(arm_obj)
        if found is None:
            return {"CANCELLED"}
        model, _tex_dir = found
        stadium = _model_is_stadium(arm_obj)
        offered = {item[0] for item in _convert_template_items(stadium)}
        default = ModelCheckCore.default_template(stadium)
        self.armature_name = arm_obj.name
        self.collect_textures = bool(bpy.data.filepath)
        self.rows.clear()
        for mat, obj in _convert_material_targets(context, arm_obj, model):
            row = self.rows.add()
            row.material_name = mat.name if mat is not None else ''
            row.object_name = obj.name if obj is not None else ''
            row.stadium = stadium
            # A template the material or its submesh carries already, if offered.
            candidates = [mat.get('TemplateSource') if mat is not None and mat.get('SluggiesNewSurface') else None,
                          obj.get('TemplateSource') if obj is not None else None]
            row.template = next((str(c) for c in candidates if c and str(c) in offered), default)
            if mat is not None and 'SpecularStrength' in mat:
                row.strength = max(0, min(255, int(mat['SpecularStrength'])))
        if not self.rows and not any(TextureFolder.needs_conversion(i) for i in _model_images(context)):
            self.report({"INFO"}, "No materials to convert: the body and rigid submeshes use Sluggies surfaces")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=640)

    @staticmethod
    def _columns(layout):
        split = layout.split(factor=0.36)
        material_col = split.column()
        rest = split.split(factor=0.42)
        mesh_col = rest.column()
        rest = rest.split(factor=0.5)
        return material_col, mesh_col, rest.column(), rest.column()

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Armature: {self.armature_name}", icon='ARMATURE_DATA')
        box = layout.box()
        col = box.column(align=True)
        if not self.rows:
            col.label(text="No materials to convert, only textures")
        else:
            material_col, mesh_col, mode_col, strength_col = self._columns(col.row())
            material_col.label(text="Material")
            mesh_col.label(text="Mesh")
            mode_col.label(text="Shader mode")
            strength_col.label(text="Specular strength")
        for row in self.rows:
            material_col, mesh_col, mode_col, strength_col = self._columns(col.row())
            material_col.label(text=row.material_name or "(new material)", icon='MATERIAL')
            mesh_col.label(text=row.object_name or "Main body")
            mode_col.prop(row, 'template', text="")
            if row.specular():
                strength_col.prop(row, 'strength', text="")
            else:
                strength_col.label(text="no specular")
        layout.label(text="OK also removes the nodes that do not feed a shader's colour", icon='INFO')
        limit = ModelCheckCore.GX_MAX_TEXTURE_DIMENSION
        layout.label(text=f"... and makes every texture a PNG of at most {limit}x{limit} "
                          f"(the original kept as <name>{TextureFolder.BACKUP_SUFFIX}.<ext>)", icon='INFO')
        # An unsaved project has no folder to copy into: shown off and greyed out.
        row = layout.row()
        row.enabled = bool(bpy.data.filepath)
        row.prop(self, 'collect_textures')

    @_reports_failure
    def execute(self, context):
        arm_obj = bpy.data.objects.get(self.armature_name)
        found = self._model(arm_obj)
        if found is None:
            return {"CANCELLED"}
        model, tex_dir = found
        # Everything that can fail is checked before the scene changes.
        try:
            for row in self.rows:
                _custom_submesh_template_texture_index(model, row.template)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        body = _body_submesh(model)
        body_meshes = {obj.data for obj in (_body_objects(context, arm_obj, body) if body else ())}

        # Rows by owner, each with its own (unshared) material.
        body_rows, rigid_rows = [], {}
        material_users = _material_meshes()
        for row in self.rows:
            obj = bpy.data.objects.get(row.object_name) if row.object_name else None
            mat = bpy.data.materials.get(row.material_name) if row.material_name else None
            if (row.object_name and obj is None) or (row.material_name and mat is None):
                continue
            if mat is not None:
                mat = _own_material(mat, {obj.data} if obj is not None else body_meshes, material_users)
            if obj is None:
                body_rows.append((row, mat))
            else:
                rigid_rows.setdefault(obj, []).append((row, mat))

        links = nodes = 0
        for _row, mat in body_rows + [entry for entries in rigid_rows.values() for entry in entries]:
            if mat is not None:
                removed_links, removed_nodes = AdaptCleanup.convert_material_nodes(mat)
                links += removed_links
                nodes += removed_nodes

        # Before the surfaces are set up: they would save a non-PNG image as a PNG copy in tex/.
        collect = self.collect_textures and bool(bpy.data.filepath)
        folder = TextureFolder.folder_for(bpy.data.filepath) if collect else None
        converted, notes = TextureFolder.convert(_model_images(context), folder)
        owner = f'sm{_BODY_SUBMESH}'
        existing = _surface_ids_in_file() if body_rows else []    # kept up to date per row
        for row, mat in body_rows:
            old_surface_id = mat.get('SurfaceId')
            if old_surface_id in existing:
                existing.remove(old_surface_id)
            for prop in _SLUGGIES_MATERIAL_PROPS:
                if prop in mat:
                    del mat[prop]
            wrap_s, wrap_t = _template_wrap_modes(context, arm_obj, row.template)
            _apply_new_surface_plan(mat, {
                'kind': 'donor',
                'owner': owner,
                'surface_id': TemplateSources.next_new_surface_key(owner, existing),
                'template_source': row.template,
                'template_texture_index': _custom_submesh_template_texture_index(model, row.template),
                'shader_mode': _template_shader_mode(context, arm_obj, row.template),
                'specular_strength': row.strength if row.specular()
                else TemplateSources.BUILTIN_DEFAULT_SPECULAR_STRENGTH,
                'wrap_s': wrap_s,
                'wrap_t': wrap_t,
            })
            existing.append(mat['SurfaceId'])
            note = _ensure_surface_texture(context, mat, tex_dir)
            if note:
                notes.append(note)

        for obj, entries in rigid_rows.items():
            by_name = {mat.name: row for row, mat in entries if mat is not None}
            # A submesh without material is listed once (no material): the
            # primary material made for it takes that row's choices.
            new_row = next((row for row, mat in entries if mat is None), None)
            before = {mat.name for mat in obj.data.materials if mat is not None}

            def template_of(mat, by_name=by_name, new_row=new_row):
                row = by_name.get(mat.name, new_row)
                return row.template if row is not None else ModelCheckCore.default_template(
                    _model_is_stadium(arm_obj))

            notes += [f"{obj.name}: {note}" for note in
                      _make_submesh_materials(context, obj, arm_obj, tex_dir, template_of)]
            for mat in obj.data.materials:
                if mat is None:
                    continue
                row = by_name.get(mat.name) or (new_row if mat.name not in before else None)
                if row is not None and row.specular() and 'SpecularStrength' in mat:
                    mat['SpecularStrength'] = row.strength

        rigid_count = sum(len(entries) for entries in rigid_rows.values())
        lines = [f"Converted {_materials(len(body_rows) + rigid_count)}: "
                 f"{len(body_rows)} on the main body, {rigid_count} on rigid submeshes"] if self.rows else []
        if links or nodes:
            lines.append(f"Removed {links} node link{'s' if links != 1 else ''} and "
                         f"{nodes} node{'s' if nodes != 1 else ''} that did not feed a shader's colour")
        if converted:
            limit = ModelCheckCore.GX_MAX_TEXTURE_DIMENSION
            lines.append(f"{converted} texture{'s' if converted != 1 else ''} now a PNG of at most {limit}x{limit}")
        if collect:   # its textures go next to the .blend
            moved, written, texture_notes = TextureFolder.collect(_model_images(context), folder)
            lines.append(f"{moved} texture{'s' if moved != 1 else ''} in use now in //{os.path.basename(folder)}/ "
                         f"({written} copied there)")
            notes += texture_notes
        _report_adapt_summary(self, context, "Convert Materials", lines + notes)
        return {"FINISHED"}
