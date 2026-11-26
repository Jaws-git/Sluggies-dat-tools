"""Make Main Body: planning which meshes become the body and what happens to the old one, the body's
surfaces, and the operator. ``AdaptMeshesUI`` and ``AdaptLowpolyUI`` build bodies through ``_make_body``.
Registered by ``SluggiesToolsPanel.register``.

Never imports ``SluggiesToolsPanel``.
"""

import bpy
from . import HostBones
from . import ModelCheckCore
from . import SluggiePath
from . import TemplateSources
from .ExportTextures import (
    _custom_submesh_template_texture_index,
)
from .SceneModels import (
    _BODY_SUBMESH,
    _DONOR_OBJECT_PROP_RE,
    _SURFACE_ID_RE,
    _body_objects,
    _body_submesh,
    _bone_id_from_name,
    _bone_metadata_is_current,
    _donor_mesh_objects,
    _find_target_armature,
    _model_is_stadium,
    _no_target_armature_message,
    _reports_failure,
    _rigid_mesh_kind,
)
from .HostBoneScene import (
    _default_reassign_target,
    _ordered_host_bone_choices,
    _renumber_added_bones_reporting,
    _stopped_after,
)
from .SurfacePlans import (
    _SLUGGIES_MATERIAL_PROPS,
    _apply_new_surface_plan,
    _model_tex_dir,
    _template_shader_mode,
    _template_specular_strength,
    _template_wrap_modes,
)
from .SubmeshOps import (
    _convert_to_custom_submesh,
    _ensure_armature_modifier,
    _parent_keeping_world,
    _rename_uv_map,
    _select_new_object,
)


_BODY_UV_NAME = 'uv0'


_MAIN_BODY_WARNING = "Each model can have only one skinned mesh."


_OLD_BODY_WARNING = "The current main body becomes a rigid submesh and keeps only one of its vertex groups."


# Join or Replace meshes keeps a replaced body whole instead.
_BACKUP_BODY_NOTE = "The current main body is set aside as '<name>.backup', off the armature and not exported."


def _dominant_bone_choice(arm_obj, obj, choices):
    """The host bone choice whose bone_<id> vertex group carries the most
    weight in *obj*, else the free bone nearest it."""
    bone_by_group = {group.index: _bone_id_from_name(group.name) for group in obj.vertex_groups}
    totals = {}
    for vertex in obj.data.vertices:
        for element in vertex.groups:
            bone_id = bone_by_group.get(element.group)
            if bone_id is not None:
                totals[bone_id] = totals.get(bone_id, 0.0) + element.weight
    weighted = [c for c in choices if totals.get(c.bone_id, 0.0) > 0]
    if weighted:
        return max(weighted, key=lambda c: totals[c.bone_id])
    return _default_reassign_target(arm_obj, obj, choices)


def _old_body_template_of(model, stadium):
    """template_of for _make_submesh_materials on a former body: each body
    surface keeps its look as `derived:<SurfaceId>` (Spec surfaces), an
    Add-material surface keeps its template, anything else gets the default."""
    default = ModelCheckCore.default_template(stadium)

    def template_of(mat):
        surface_id = str(mat.get('SurfaceId') or '')
        if mat.get('SluggiesNewSurface'):
            candidate = str(mat.get('TemplateSource') or '')
        elif _SURFACE_ID_RE.match(surface_id) and mat.get('ShaderMode') == 'Spec':
            candidate = f'derived:{surface_id}'
        else:
            candidate = ''
        if candidate:
            try:
                _custom_submesh_template_texture_index(model, candidate)
                return candidate
            except ValueError:
                pass
        return default

    return template_of


def _body_surface_plans(context, arm_obj, obj, model, owner=f'sm{_BODY_SUBMESH}', template_of=None):
    """(material, plan) for each material of the custom submesh *obj* as an
    Add-material surface of the body (or of donor submesh *owner*), keeping
    its template (or taking *template_of*(material)), specular strength and
    wrap modes. Raises ValueError before anything is changed."""
    existing = [mat.get('SurfaceId', '') for mat in bpy.data.materials if mat.get('SurfaceId')]
    default = ModelCheckCore.default_template(_model_is_stadium(arm_obj))
    plans = []
    for mat in dict.fromkeys(slot.material for slot in obj.material_slots if slot.material is not None):
        template_source = template_of(mat) if template_of is not None else str(
            (mat.get('TemplateSource') if mat.get('SluggiesNewSurface') else obj.get('TemplateSource'))
            or default)
        template_texture_index = _custom_submesh_template_texture_index(model, template_source)
        shader_mode = (_template_shader_mode(context, arm_obj, template_source) or mat.get('ShaderMode')
                       or ('Spec' if template_source.startswith('derived:') else ''))
        if 'SpecularStrength' in mat:
            specular_strength = max(0, min(255, int(mat['SpecularStrength'])))
        else:
            specular_strength = _template_specular_strength(context, arm_obj, template_source)
        if 'WrapS' in mat and 'WrapT' in mat:
            wrap_s, wrap_t = int(mat['WrapS']), int(mat['WrapT'])
        else:
            wrap_s, wrap_t = _template_wrap_modes(context, arm_obj, template_source)
        surface_id = TemplateSources.next_new_surface_key(owner, existing)
        existing.append(surface_id)
        plans.append((mat, {
            'kind': 'donor',
            'owner': owner,
            'surface_id': surface_id,
            'template_source': template_source,
            'template_texture_index': template_texture_index,
            'shader_mode': shader_mode,
            'specular_strength': specular_strength,
            'wrap_s': wrap_s,
            'wrap_t': wrap_t,
        }))
    if not plans:
        raise ValueError(f"{obj.name} has no material; give it one first")
    return plans


def _apply_body_surface_plans(mesh, plans):
    """Write _body_surface_plans onto *mesh*'s materials (shared or linked
    ones are copied first)."""
    for mat, plan in plans:
        target = mat
        if mat.users > 1 or mat.library is not None:
            target = mat.copy()
            for index, slot_material in enumerate(mesh.materials):
                if slot_material == mat:
                    mesh.materials[index] = target
        for prop in _SLUGGIES_MATERIAL_PROPS:
            if prop in target:
                del target[prop]
        _apply_new_surface_plan(target, plan)


def _use_body_uv_name(obj):
    """Name *obj*'s render UV map like the body's channel 0 (a UV map already
    called that is renamed out of the way). Returns notes."""
    mesh = obj.data
    uv_layer = mesh.uv_layers.active_render or mesh.uv_layers.active
    if uv_layer is None:
        return ["no UV map: add one and unwrap it before exporting"]
    if uv_layer.name != _BODY_UV_NAME:
        other = mesh.uv_layers.get(_BODY_UV_NAME)
        if other is not None:
            _rename_uv_map(obj, f'{_BODY_UV_NAME}_old', other)
        _rename_uv_map(obj, _BODY_UV_NAME, uv_layer)
    return []


def _plan_old_bodies(context, arm_obj, body, new_body, reserved=()):
    """[(object, bone id)]: the free bone each current body object (of the
    skinned *body* submesh) becomes a rigid submesh on when *new_body*
    replaces it. Bones in *reserved* are taken already. Raises ValueError."""
    # The new body's own bone counts as free: it stops being rigid.
    choices = [c for c in _ordered_host_bone_choices(context, arm_obj, exclude=new_body)
               if c.bone_id not in reserved]
    old_bodies = []
    for old in _body_objects(context, arm_obj, body):
        choice = _dominant_bone_choice(arm_obj, old, choices)
        if choice is None:
            raise ValueError(f"No free bone left for {old.name} to become a rigid submesh on")
        choices = [c for c in choices if c.bone_id != choice.bone_id]
        old_bodies.append((old, choice.bone_id))
    return old_bodies


def _demote_old_bodies(context, arm_obj, model, tex_dir, old_bodies):
    """Turn each _plan_old_bodies entry into a rigid custom submesh. Returns notes."""
    notes = []
    stadium = _model_is_stadium(arm_obj)
    template_of = _old_body_template_of(model, stadium)
    for old, bone_id in old_bodies:
        for prop in ModelCheckCore.REQUIRED_PROPS + tuple(
                key for key in old.keys() if _DONOR_OBJECT_PROP_RE.match(key)):
            if prop in old:
                del old[prop]
        custom_submesh_id, old_notes = _convert_to_custom_submesh(
            context, old, arm_obj, bone_id, ModelCheckCore.default_template(stadium), tex_dir,
            template_of=template_of)
        notes.append(f"{old.name} is now custom submesh {custom_submesh_id} on bone_{bone_id}")
        notes += old_notes
    return notes


def _set_aside_old_bodies(old_bodies):
    """Take each replaced body object out of the model, where it is: no
    parent, no Armature modifier, no submesh properties, named `<name>.backup`
    and marked ModelCheckCore.BACKUP_PROP. Returns the new names."""
    names = []
    for old in old_bodies:
        world = old.matrix_world.copy()
        old.parent = None
        old.matrix_world = world
        for mod in [m for m in old.modifiers if m.type == 'ARMATURE']:
            old.modifiers.remove(mod)
        for prop in ModelCheckCore.REQUIRED_PROPS + tuple(
                key for key in old.keys() if _DONOR_OBJECT_PROP_RE.match(key)):
            if prop in old:
                del old[prop]
        old[ModelCheckCore.BACKUP_PROP] = True
        old.name = f'{old.name}.backup'
        names.append(old.name)
    return names


def _carry_shape_key_names(old_bodies, new_body):
    """Give *new_body* an empty shape key (a copy of its basis) for each
    shape key name of the replaced *old_bodies* it lacks, so the facial
    poses keep their names and only need sculpting. Returns notes."""
    names = list(dict.fromkeys(
        key.name for old in old_bodies if old.data.shape_keys is not None
        for key in old.data.shape_keys.key_blocks))
    if not names:
        return []
    keys = new_body.data.shape_keys
    existing = set(keys.key_blocks.keys()) if keys is not None else set()
    added = 0
    if keys is None:
        # The first key becomes the reference (basis) key.
        new_body.shape_key_add(name=names[0], from_mix=False)
        existing.add(names[0])
    for name in names:
        if name not in existing:
            new_body.shape_key_add(name=name, from_mix=False)
            existing.add(name)
            added += 1
    if not added:
        return []
    return [f"{new_body.name} got {added} empty shape key{'s' if added != 1 else ''} "
            f"named like the replaced body's: sculpt them for its facial animation"]


def _make_body(obj, arm_obj, model, plans, check_weights=True):
    """Turn the custom submesh *obj* into the skinned body: the submesh 0
    VertexBuffer* properties, its materials as Add-material surfaces of sm0
    (*plans*; None leaves them to Convert Materials), its render UV map named
    like the body's channel 0. Its vertex groups become the skin weights;
    *check_weights* notes a body without bone_<id> groups. Returns notes."""
    notes = []
    if obj.data.users > 1:
        obj.data = obj.data.copy()
        notes.append(f"{obj.name}'s mesh data was shared, so it got its own copy")
    for prop in ('SluggiesCustomSubmesh', 'CustomSubmeshId', 'TemplateSource', 'SluggiesRigidPlacement'):
        if prop in obj:
            del obj[prop]
    for prop, value in ModelCheckCore.mesh_props_for_submesh(model, _BODY_SUBMESH).items():
        obj[prop] = value

    if plans is not None:
        _apply_body_surface_plans(obj.data, plans)
    notes += _use_body_uv_name(obj)
    _ensure_armature_modifier(obj, arm_obj)
    _parent_keeping_world(obj, arm_obj)
    if check_weights and not any(_bone_id_from_name(group.name) is not None for group in obj.vertex_groups):
        notes.append("no bone_<id> vertex group: weight it to the bones before exporting")
    return notes


class SLUGGIES_OT_make_main_body(bpy.types.Operator):
    """Make the active custom submesh the model's skinned body (submesh 0);
    the current body becomes a rigid custom submesh"""
    bl_idname = "sluggies.make_main_body"
    bl_label = "Make This Mesh the Main Body"
    bl_description = ("Make the active custom submesh the model's skinned body (submesh 0), "
                      "weighted by its vertex groups. The current body becomes a rigid submesh on one bone")
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Switch to Object Mode")
            return False
        arm_obj = _find_target_armature(context)
        if arm_obj is None:
            cls.poll_message_set(_no_target_armature_message(context))
            return False
        if not _bone_metadata_is_current(arm_obj):
            cls.poll_message_set(HostBones.RE_IMPORT_MESSAGE)
            return False
        kind, _submesh_index = _rigid_mesh_kind(context.active_object)
        if kind != 'custom':
            cls.poll_message_set(
                "Only a custom submesh can become the body" if kind == 'donor'
                else "Select a custom submesh")
            return False
        return True

    def invoke(self, context, event):
        # Without a body in the scene nothing is demoted: no need to confirm.
        arm_obj = _find_target_armature(context)
        if arm_obj is None or not any(
                o.get('VertexBufferCompCount') == 6 for o in _donor_mesh_objects(context, arm_obj)):
            return self.execute(context)
        return context.window_manager.invoke_confirm(
            self, event, title=self.bl_label, message=_MAIN_BODY_WARNING + "\n" + _OLD_BODY_WARNING,
            confirm_text="OK", icon='WARNING')

    @_reports_failure
    def execute(self, context):
        arm_obj = _find_target_armature(context)
        obj = context.active_object
        if arm_obj is None or _rigid_mesh_kind(obj)[0] != 'custom':
            self.report({"ERROR"}, "Select a custom submesh of a Sluggies model")
            return {"CANCELLED"}
        sluggie_path = SluggiePath.resolve(arm_obj)
        if not sluggie_path:
            self.report({"ERROR"}, SluggiePath.missing_message(arm_obj, "Make main body"))
            return {"CANCELLED"}
        model, tex_dir, error = _model_tex_dir(sluggie_path)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        body = _body_submesh(model)
        if body is None:
            self.report({"ERROR"}, "This model's submesh 0 is not a skinned body")
            return {"CANCELLED"}
        renamed = _renumber_added_bones_reporting(self, arm_obj)

        # Everything that can fail is checked before the scene changes.
        try:
            plans = _body_surface_plans(context, arm_obj, obj, model)
            old_bodies = _plan_old_bodies(context, arm_obj, body, obj)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return _stopped_after(renamed)

        notes = _make_body(obj, arm_obj, model, plans)
        notes += _demote_old_bodies(context, arm_obj, model, tex_dir, old_bodies)

        _select_new_object(context, obj)
        self.report({"INFO"}, "; ".join([f"{obj.name} is now the skinned body (submesh 0)"] + notes))
        return {"FINISHED"}
