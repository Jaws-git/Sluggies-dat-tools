"""Adapt Model, step Generate Lowpoly: the row property group, the Low templates, mirroring of added bones
onto the Low armature, and the operator (the decimation itself is ``AdaptLowpoly``). Registered by
``SluggiesToolsPanel.register``.

Never imports ``SluggiesToolsPanel``.
"""

import bpy
from bpy.props import (
    BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty,
)
from . import AdaptLowpoly
from . import CustomSubmeshExport
from . import BoneRoles
from . import HostBones
from . import ModelCheckCore
from . import SluggiePath
from .ExportTextures import (
    _custom_submesh_template_texture_index,
)
from .SceneModels import (
    _SURFACE_ID_RE,
    _body_objects,
    _body_submesh,
    _bone_id_from_name,
    _bone_metadata_is_current,
    _donor_mesh_objects,
    _is_body_object,
    _is_collision_mesh,
    _lowpoly_partner,
    _model_is_stadium,
    _reports_failure,
    _selected_sluggies_armature,
)
from .HostBoneScene import (
    _UNIFORM_BONE_CACHE,
    _next_new_bone_creation_order,
    _ordered_host_bone_choices,
    _renumber_added_bones,
    _renumber_added_bones_reporting,
    _stopped_after,
    _uniform_bone_id,
)
from .SurfacePlans import (
    _is_primary_surface_of,
    _model_tex_dir,
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
    _DONOR_OBJECT_PROP_RE,
    _apply_body_surface_plans,
    _body_surface_plans,
    _make_body,
)
from .AdaptMeshesUI import (
    _adapt_mesh_inventory,
    _adapt_mesh_status,
)


class SluggiesLowpolyRow(bpy.types.PropertyGroup):
    """One High model mesh of the Generate Lowpoly dialog."""
    object_name: StringProperty()  # type: ignore[valid-type]
    kind: StringProperty()  # type: ignore[valid-type]
    vertices: IntProperty()  # type: ignore[valid-type]
    mode: EnumProperty(  # type: ignore[valid-type]
        name="Mode", items=AdaptLowpoly.MODES, default=AdaptLowpoly.COLLAPSE,
    )
    iterations: EnumProperty(  # type: ignore[valid-type]
        name="Iterations", items=AdaptLowpoly.UNSUBDIV_ITERATIONS, default='2',
    )
    tris_to_quads: BoolProperty(  # type: ignore[valid-type]
        name="Triangles to quads",
        description="Join triangles into quads first (60° face and shape angles, topology influence 1, "
                    "UVs, seams, sharp edges and materials compared): Un-Subdivide finds no grid in triangles",
        default=True,
    )
    ratio: FloatProperty(  # type: ignore[valid-type]
        name="Ratio", description="Share of the faces to keep",
        min=0.01, max=1.0, default=AdaptLowpoly.DEFAULT_RATIO, subtype='FACTOR',
    )


def _lowpoly_templates(low_model, stadium, obj, custom):
    """{material name: template} for *obj*'s materials on the Low model: the
    one each has on the High model (a donor Spec surface keeps its look as
    `derived:`) when the Low model resolves it, else the default. *custom*:
    *obj* becomes a custom submesh there."""
    default = ModelCheckCore.default_template(stadium)
    own_id = str(obj.get('CustomSubmeshId') or '')
    templates = {}
    for mat in dict.fromkeys(slot.material for slot in obj.material_slots if slot.material is not None):
        surface_id = str(mat.get('SurfaceId') or '')
        if mat.get('SluggiesNewSurface'):
            candidate = str(mat.get('TemplateSource') or '')
        elif own_id and _is_primary_surface_of(mat, own_id):
            candidate = str(obj.get('TemplateSource') or '')
        elif _SURFACE_ID_RE.match(surface_id) and mat.get('ShaderMode') == 'Spec':
            candidate = f'derived:{surface_id}'
        else:
            candidate = ''
        if candidate:
            try:
                _custom_submesh_template_texture_index(low_model, candidate)
                if custom:
                    CustomSubmeshExport.attribute_plan(low_model, candidate)
            except (ValueError, KeyError):
                candidate = ''
        templates[mat.name] = candidate or default
    return templates


def _strip_donor_props(obj):
    """Drop the High model's submesh and custom submesh properties from a copy."""
    for prop in ModelCheckCore.REQUIRED_PROPS + (
            'SluggiesCustomSubmesh', 'CustomSubmeshId', 'TemplateSource', 'SluggiesRigidPlacement') + tuple(
            key for key in obj.keys() if _DONOR_OBJECT_PROP_RE.match(key)):
        if prop in obj:
            del obj[prop]


def _unhide(obj):
    """Show *obj* (Edit Mode needs it); returns what _rehide needs."""
    hidden = (obj.hide_get(), obj.hide_viewport)
    obj.hide_set(False)
    obj.hide_viewport = False
    return hidden


def _rehide(obj, hidden):
    obj.hide_set(hidden[0])
    obj.hide_viewport = hidden[1]


def _mirror_added_bones(context, high, low, bones):
    """Add *bones* (added bones of *high*, in creation order) to *low*, placed
    and set up as on *high*, so they get the same ids. Returns the names
    that did not come out the same."""
    # Read *high*'s bones first: Edit Mode also takes in a selected *high*,
    # which makes new Bone structs and leaves the old references dangling.
    snapshot = [(bone.name, bone.parent.name if bone.parent is not None else None, bone.matrix_local.copy(),
                 bone.length, dict(bone.items())) for bone in bones]
    prev_active = context.view_layer.objects.active
    hidden = _unhide(low)
    context.view_layer.objects.active = low
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        edit_bones = low.data.edit_bones
        for name, parent_name, matrix, length, _props in snapshot:
            new_eb = edit_bones.new(name)
            new_eb.head = (0.0, 0.0, 0.0)
            new_eb.tail = (0.0, length, 0.0)
            new_eb.matrix = matrix
            new_eb.parent = edit_bones.get(parent_name) if parent_name else None
            new_eb.use_connect = False
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
        context.view_layer.objects.active = prev_active
        _rehide(low, hidden)
    order = _next_new_bone_creation_order(low)
    for name, _parent_name, _matrix, _length, props in snapshot:
        new_bone = low.data.bones.get(name)
        if new_bone is None:
            continue
        for key, value in props.items():
            new_bone[key] = value
        new_bone['SluggiesCreationOrder'] = order
        order += 1
        BoneRoles.bone_collection(low.data, BoneRoles.ADDED).assign(new_bone)
    _renumber_added_bones(low)
    return [name for name, *_rest in snapshot if name not in low.data.bones]


class SLUGGIES_OT_adapt_lowpoly(bpy.types.Operator):
    """Generate Lowpoly step of the 'Adapt' box (AdaptLowpoly)"""
    bl_idname = "sluggies.adapt_lowpoly"
    bl_label = "Generate Lowpoly"
    bl_description = next(desc for ident, _label, desc in _ADAPT_STEPS if ident == 'LOWPOLY')
    bl_options = {"UNDO"}

    rows: bpy.props.CollectionProperty(type=SluggiesLowpolyRow)  # type: ignore[valid-type]
    high_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]
    low_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Switch to Object Mode")
            return False
        high = _selected_sluggies_armature(context)
        if high is None:
            cls.poll_message_set("Select the High model's armature or one of its child objects")
            return False
        low, problem = _lowpoly_partner(context, high)
        if low is None:
            cls.poll_message_set(problem)
            return False
        return True

    def invoke(self, context, event):
        high = _selected_sluggies_armature(context)
        low, problem = _lowpoly_partner(context, high) if high is not None else (None, "Select a High model")
        if low is None:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        for arm_obj in (high, low):
            if not _bone_metadata_is_current(arm_obj):
                self.report({"ERROR"}, f"{arm_obj.name}: {HostBones.RE_IMPORT_MESSAGE}")
                return {"CANCELLED"}
        self.high_name, self.low_name = high.name, low.name
        self.rows.clear()
        sluggies, _addable = _adapt_mesh_inventory(context, high)
        meshes = [obj for obj in sluggies if not _is_collision_mesh(obj)]
        bodies = [obj for obj in meshes if _is_body_object(obj)]
        # Only one body object can go (an imported `_edit` is the body's current state).
        body = next((o for o in bodies if o.name.endswith('_edit')), bodies[0] if bodies else None)
        for obj in sorted(meshes, key=lambda o: (o is not body, o not in bodies, o.name)):
            row = self.rows.add()
            row.object_name = obj.name
            row.kind = _adapt_mesh_status(obj)
            row.vertices = len(obj.data.vertices)
            row.mode = AdaptLowpoly.IGNORE if obj in bodies and obj is not body \
                else AdaptLowpoly.default_mode(row.vertices)
        if not self.rows:
            self.report({"INFO"}, f"{high.name} has no Sluggies meshes to copy")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=760)

    @staticmethod
    def _columns(layout):
        split = layout.split(factor=0.3)
        name_col = split.column()
        rest = split.split(factor=0.36)
        kind_col = rest.column()
        rest = rest.split(factor=0.2)
        count_col = rest.column()
        rest = rest.split(factor=0.5)
        return name_col, kind_col, count_col, rest.column(), rest.column()

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"High: {self.high_name}    Low: {self.low_name}", icon='ARMATURE_DATA')
        box = layout.box()
        col = box.column(align=True)
        name_col, kind_col, count_col, mode_col, value_col = self._columns(col.row())
        name_col.label(text="Mesh")
        kind_col.label(text="Kind")
        count_col.label(text="Vertices")
        mode_col.label(text="Mode")
        value_col.label(text="Setting")
        for row in self.rows:
            name_col, kind_col, count_col, mode_col, value_col = self._columns(col.row())
            name_col.label(text=row.object_name, icon='MESH_DATA')
            kind_col.label(text=row.kind)
            count_col.label(text=f"{row.vertices:,}")
            mode_col.prop(row, 'mode', text="")
            if row.mode == AdaptLowpoly.UNSUBDIV:
                value_col.row(align=True).prop(row, 'iterations', expand=True)
                # Its own line: next to the iterations it is cut off.
                value_col.prop(row, 'tris_to_quads', text="Tris to quads")
            elif row.mode == AdaptLowpoly.COLLAPSE:
                value_col.prop(row, 'ratio', text="")
            else:
                value_col.label(text="")
        layout.label(text="The main body replaces the Low model's body; a rigid mesh replaces the Low model's "
                          "mesh on its bone, or is added there", icon='INFO')

    @_reports_failure
    def execute(self, context):
        high = bpy.data.objects.get(self.high_name)
        low = bpy.data.objects.get(self.low_name)
        if high is None or low is None:
            self.report({"ERROR"}, "The High or the Low armature is gone")
            return {"CANCELLED"}
        low_path = SluggiePath.resolve(low)
        if not low_path:
            self.report({"ERROR"}, SluggiePath.missing_message(low, "Generate Lowpoly"))
            return {"CANCELLED"}
        low_model, low_tex_dir, error = _model_tex_dir(low_path)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        chosen = []
        for row in self.rows:
            obj = bpy.data.objects.get(row.object_name)
            if obj is not None and row.mode != AdaptLowpoly.IGNORE:
                chosen.append((obj, row))
        if not chosen:
            self.report({"INFO"}, "Nothing to copy: every mesh is set to Ignore")
            return {"CANCELLED"}
        renamed = bool(_renumber_added_bones_reporting(self, high))
        renamed = bool(_renumber_added_bones_reporting(self, low)) or renamed
        # Everything that can fail is checked before the Low model changes.
        try:
            plan = self._plan(context, high, low, low_model, chosen)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return _stopped_after(renamed)
        result = self._apply(context, high, low, low_model, low_tex_dir, plan)
        return _stopped_after(renamed) if result == {"CANCELLED"} else result

    @staticmethod
    def _plan(context, high, low, low_model, chosen):
        """What goes where: {'body': (source, row) or None, 'old_bodies',
        'rigid': [(bone id, [(source, row)], Low submesh index or None,
        Low objects it replaces)], 'mirror': High added bones to add}.
        Raises ValueError."""
        bodies = [(obj, row) for obj, row in chosen if _is_body_object(obj)]
        if len(bodies) > 1:
            raise ValueError("Only one main body object can go to the Low model: set the others to Ignore")
        old_bodies = []
        if bodies:
            if not any(slot.material is not None for slot in bodies[0][0].material_slots):
                raise ValueError(f"{bodies[0][0].name} has no material; give it one first")
            low_body = _body_submesh(low_model)
            if low_body is None:
                raise ValueError(f"{low.name}'s submesh 0 is not a skinned body")
            old_bodies = _body_objects(context, low, low_body)

        by_bone = {}
        for obj, row in chosen:
            if _is_body_object(obj):
                continue
            bone_id = _uniform_bone_id(obj)
            if bone_id is None:
                raise ValueError(f"{obj.name} does not follow exactly one bone: weight it fully to one "
                                 "bone_<id> group, or set it to Ignore")
            by_bone.setdefault(bone_id, []).append((obj, row))

        used = {group.name for obj, _row in chosen for group in obj.vertex_groups
                if group.name in high.data.bones}
        mirror = []
        if used - set(low.data.bones.keys()):
            mirror = AdaptLowpoly.bones_to_mirror(high, low)
            missing = used - set(low.data.bones.keys()) - {bone.name for bone in mirror}
            if missing:
                raise ValueError(f"{low.name} lacks the bones {', '.join(sorted(missing))}")
            hidden = _unhide(low)
            visible = low.visible_get()
            _rehide(low, hidden)
            if not visible:
                raise ValueError(f"Show the collection of {low.name} first: the bones added on {high.name} "
                                 "are added to it too")
        free = {c.bone_id for c in _ordered_host_bone_choices(context, low)}
        free |= {_bone_id_from_name(bone.name) for bone in mirror}

        low_donors = [o for o in _donor_mesh_objects(context, low)
                      if not _is_body_object(o) and not _is_collision_mesh(o)]
        low_customs = [o for o in context.view_layer.objects
                       if o.type == 'MESH' and o.parent is low and o.get('SluggiesCustomSubmesh')]
        rigid = []
        for bone_id, sources in sorted(by_bone.items()):
            on_bone = [o for o in low_donors if _uniform_bone_id(o) == bone_id]
            indices = sorted({index for index in (ModelCheckCore.submesh_index_by_offset(
                low_model, o.get('VertexBufferOffset')) for o in on_bone) if index is not None})
            customs = [o for o in low_customs if _uniform_bone_id(o) == bone_id]
            # What an earlier run put there goes again.
            made = [o for o in customs if o.get(AdaptLowpoly.LOW_SOURCE_PROP)]
            if indices:
                offset = str(ModelCheckCore.mesh_props_for_submesh(low_model, indices[0])['VertexBufferOffset'])
                replaced = [o for o in on_bone if str(o.get('VertexBufferOffset')) == offset]
                rigid.append((bone_id, sources, indices[0], replaced + made))
            elif len(made) == len(customs) and (customs or bone_id in free):
                rigid.append((bone_id, sources, None, made))
            else:
                taken = ", ".join(o.name for o in customs if not o.get(AdaptLowpoly.LOW_SOURCE_PROP))
                raise ValueError(f"bone_{bone_id} is not free on {low.name}" + (f" ({taken} is on it)" if taken else "")
                                 + f": set {', '.join(obj.name for obj, _row in sources)} to Ignore")
        # Custom submeshes an earlier run made from these meshes go as well (a
        # mesh may have become the body since).
        names = {obj.name for obj, _row in chosen}
        stale = [o for o in low_customs if o.get(AdaptLowpoly.LOW_SOURCE_PROP) in names]
        return {'body': bodies[0] if bodies else None, 'old_bodies': old_bodies, 'rigid': rigid, 'mirror': mirror,
                'stale': stale}

    def _apply(self, context, high, low, low_model, low_tex_dir, plan):
        stadium = _model_is_stadium(low)
        default = ModelCheckCore.default_template(stadium)
        collection = low.users_collection[0]
        to_low = low.matrix_world @ high.matrix_world.inverted()
        copies, notes = [], []

        def make_copy(obj, row):
            copy = AdaptLowpoly.copy_object(obj, collection)
            copies.append(copy)
            copy.name = f'{obj.name}_low'
            copy[AdaptLowpoly.LOW_SOURCE_PROP] = obj.name
            copy.matrix_world = to_low @ obj.matrix_world
            if obj.data.shape_keys is not None:
                notes.append(f"{copy.name}: shape keys removed (Low models have no vertex animation)")
            notes.extend(AdaptLowpoly.decimate(context, copy, row.mode, row.iterations, row.ratio,
                                               row.tris_to_quads))
            return copy

        # Copies first: until they are done, a failure only removes them.
        try:
            body = None
            if plan['body'] is not None:
                source, row = plan['body']
                copy = make_copy(source, row)
                templates = _lowpoly_templates(low_model, stadium, copy, custom=False)
                plans = _body_surface_plans(context, low, copy, low_model,
                                            template_of=lambda mat: templates.get(mat.name, default))
                body = (source, copy, plans)
            rigid = []
            for bone_id, sources, index, replaced in plan['rigid']:
                group, templates = [], {}
                for source, row in sources:
                    copy = make_copy(source, row)
                    templates.update(_lowpoly_templates(low_model, stadium, copy, custom=index is None))
                    group.append(copy)
                copy = group[0]
                if len(group) > 1:   # several meshes on one bone become one
                    with context.temp_override(active_object=copy, object=copy, selected_objects=group,
                                               selected_editable_objects=group):
                        bpy.ops.object.join()
                    for joined in group[1:]:
                        copies.remove(joined)
                surface_plans = None
                if index is not None:
                    surface_plans = _body_surface_plans(
                        context, low, copy, low_model, owner=f'sm{index}',
                        template_of=lambda mat, templates=templates: templates.get(mat.name, default))
                rigid.append((bone_id, sources, index, replaced, copy, templates, surface_plans))
        except (RuntimeError, ValueError) as exc:
            for copy in copies:
                if copy.name in bpy.data.objects:
                    AdaptLowpoly.remove_object(copy)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        def vertices(sources):
            return sum(len(source.data.vertices) for source, _row in sources)

        lines, warnings = [], []
        if plan['mirror']:
            names = [bone.name for bone in plan['mirror']]
            unmatched = _mirror_added_bones(context, high, low, plan['mirror'])
            lines.append(f"Added bones {', '.join(names)} to {low.name}, as on {high.name}")
            if unmatched:
                warnings.append(f"{', '.join(unmatched)} came out under other names on {low.name}: "
                                "check its added bones")
        # An earlier run's custom submeshes go first, so their ids are free again.
        to_remove = dict.fromkeys(plan['old_bodies'] + plan['stale']
                                  + [obj for entry in rigid for obj in entry[3]])
        replaced_later = []
        for obj in to_remove:
            if obj.get('SluggiesCustomSubmesh'):
                AdaptLowpoly.remove_object(obj)
            else:
                replaced_later.append(obj)
        context.view_layer.update()   # drop the removed objects from view_layer.objects
        # Custom submeshes before the replacements: their templates may read
        # the Low model's own materials.
        for bone_id, sources, index, replaced, copy, templates, _plans in rigid:
            if index is not None:
                continue
            strengths = {mat.name: mat['SpecularStrength'] for mat in copy.data.materials
                         if mat is not None and 'SpecularStrength' in mat}
            _strip_donor_props(copy)
            # The copies show their High siblings' images: no new PNGs.
            custom_submesh_id, copy_notes = _convert_to_custom_submesh(
                context, copy, low, bone_id, default, low_tex_dir,
                template_of=lambda mat, templates=templates: templates.get(mat.name, default),
                ensure_textures=False)
            for mat in copy.data.materials:
                if mat is not None and 'SpecularStrength' in mat and mat.name in strengths:
                    mat['SpecularStrength'] = strengths[mat.name]
            lines.append(f"{', '.join(s.name for s, _r in sources)}: {vertices(sources):,} -> "
                         f"{len(copy.data.vertices):,} vertices, added submesh {custom_submesh_id} on bone_{bone_id}")
            notes += [f"{copy.name}: {note}" for note in copy_notes]
        for bone_id, sources, index, replaced, copy, _templates, surface_plans in rigid:
            if index is None:
                continue
            _strip_donor_props(copy)
            for prop, value in ModelCheckCore.mesh_props_for_submesh(low_model, index).items():
                copy[prop] = value
            _apply_body_surface_plans(copy.data, surface_plans)
            _ensure_armature_modifier(copy, low)
            _parent_keeping_world(copy, low)
            old = ", ".join(o.name for o in replaced if not o.get('SluggiesCustomSubmesh')) or f"submesh {index}"
            lines.append(f"{', '.join(s.name for s, _r in sources)}: {vertices(sources):,} -> "
                         f"{len(copy.data.vertices):,} vertices, replaces {old} on bone_{bone_id}")
        if body is not None:
            source, copy, plans = body
            _strip_donor_props(copy)
            notes += [f"{copy.name}: {note}" for note in _make_body(copy, low, low_model, plans)]
            old = ", ".join(o.name for o in plan['old_bodies']) or "nothing"
            lines.insert(0, f"{source.name}: {len(source.data.vertices):,} -> {len(copy.data.vertices):,} "
                            f"vertices, the Low model's main body (replaces {old})")
        for obj in replaced_later:
            if obj.name in bpy.data.objects:
                AdaptLowpoly.remove_object(obj)
        _UNIFORM_BONE_CACHE.clear()
        lines.append(f"Next: check {low.name} and export it with its High model")
        _report_adapt_summary(self, context, "Generate Lowpoly", lines + notes, warnings)
        return {"FINISHED"}
