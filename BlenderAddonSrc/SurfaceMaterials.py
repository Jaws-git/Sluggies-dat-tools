"""Materials of model surfaces: the node tree of a surface material, the metadata a material carries for the
exporter (``_set_surface_material_metadata``), the image of a texture file, and the materials of new
surfaces and custom submeshes. Used by the importer, the tools panel and Check / Repair Models.

Imports ``ImportDecode`` only among the ``Import*`` modules, and never ``ImportSluggies`` or
``SluggiesToolsPanel``. ``SurfacePlans`` (which imports this module) and ``ExportTextures`` are imported
inside the functions that need them.
"""

import bpy
import os
from functools import lru_cache
from .ImportDecode import (
    _GX_WRAP,
)


def _create_material(mat_name, image, wrap_s=1):
    """Build an Image Texture -> Diffuse BSDF -> Material Output node tree."""
    mat = bpy.data.materials.new(name=mat_name)
    _build_material_nodes(mat, image, wrap_s)
    return mat


def _new_image_texture_node(nodes, right_x, y):
    """An Image Texture node at twice Blender's default width, its right edge
    at *right_x* so the wider node does not cover the node it feeds."""
    tex_node = nodes.new('ShaderNodeTexImage')
    tex_node.width *= 2
    tex_node.location = (right_x - tex_node.width, y)
    return tex_node


def _build_material_nodes(mat, image, wrap_s=1):
    """Replace *mat*'s node tree with the importer's texture tree (see _create_material)."""
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    out_node = nodes.new('ShaderNodeOutputMaterial')
    out_node.location = (800, 0)

    bsdf_node = nodes.new('ShaderNodeBsdfDiffuse')
    bsdf_node.location = (550, 0)

    tex_node = _new_image_texture_node(nodes, 440, 0)
    if image:
        tex_node.image = image
    tex_node.extension = _GX_WRAP.get(wrap_s, 'REPEAT')

    links.new(tex_node.outputs['Color'], bsdf_node.inputs['Color'])
    links.new(bsdf_node.outputs['BSDF'], out_node.inputs['Surface'])


def _set_surface_material_metadata(mat, ds_entry):
    display_state_id = ds_entry.get("DisplayStateId")
    mat["DisplayStateId"] = display_state_id
    # An exported edit wins, so a re-import keeps it (the export compares
    # with the donor value and drops the edit when they match).
    mat["ShaderMode"] = ds_entry.get("ShaderModeEdited") or ds_entry.get("ShaderMode", "")
    mat.id_properties_ui("DisplayStateId").update(
        description="GX display-state command type for this donor surface.")
    shader_description = (
        "Editable Type-7 shader mode. Known names: Spec=specular  "
        "Shdw=no-specular  SpRf=specular-reflection  "
        "RhSp/LhSp=right/left-hand-specular  GhSp=ghost. "
        "Edit via the 'Manage Materials' panel button."
        if display_state_id == 7 else
        f"Raw Type-{display_state_id} GX state setting. Exposed for "
        "surface comparison; only Type-7 shader-mode edits are exported."
    )
    mat.id_properties_ui("ShaderMode").update(description=shader_description)

    if display_state_id == 7:
        param_hex = (ds_entry.get("DisplayStateParamBytesEdited")
                     or ds_entry.get("DisplayStateParamBytes", "000000"))
        try:
            # The 3 exported bytes are struct offsets +1..+3; specular
            # intensity is offset +1, i.e. index 0.
            specular_strength = bytes.fromhex(param_hex)[0]
        except (ValueError, IndexError):
            specular_strength = 0
        mat["SpecularStrength"] = specular_strength
        mat.id_properties_ui("SpecularStrength").update(
            min=0, max=255, soft_min=0, soft_max=255,
            description="Specular highlight intensity (0-255, linear TEV multiplier). "
            "Confirmed in-game for 'Spec'/'LhSp'/'RhSp' shader modes. "
            "Edit via the 'Manage Materials' panel button.")


@lru_cache(maxsize=None)
def _resolve_texture_image_path(sluggie_dir, tex_file):
    """Find a texture PNG for this import, falling back to sibling export folders.

    Lowpoly exports in this workspace often live in a sibling folder without a
    local tex/ directory, while the paired highpoly export keeps the PNG cache.
    """
    candidate_dirs = [os.path.join(sluggie_dir, 'tex')]

    parent_dir = os.path.dirname(sluggie_dir)
    if parent_dir and os.path.isdir(parent_dir):
        sibling_tex_dir = os.path.join(parent_dir, 'tex')
        candidate_dirs.append(sibling_tex_dir)
        try:
            for sibling_name in sorted(os.listdir(parent_dir)):
                sibling_dir = os.path.join(parent_dir, sibling_name)
                if os.path.isdir(sibling_dir):
                    candidate_dirs.append(os.path.join(sibling_dir, 'tex'))
        except OSError:
            pass

    for tex_dir in candidate_dirs:
        img_path = os.path.join(tex_dir, tex_file)
        if os.path.exists(img_path):
            return img_path
    return None


def _template_shader_mode_from_model(model, template_source):
    """Effective Type-7 mode of a template source, read from the .sluggie."""
    kind, _sep, argument = str(template_source).partition(':')
    if kind == 'builtin':
        from . import TemplateSources
        template = TemplateSources.BUILTIN_TEMPLATES.get(argument)
        return template.shader_mode if template is not None else ''
    for submesh in model.get('Submeshes') or []:
        states = submesh.get('DisplayStates') or []
        mode = ''
        for state in states:
            if int(state.get('DisplayStateId', -1)) == 7:
                mode = state.get('ShaderModeEdited') or state.get('ShaderMode', '')
            if state.get('SurfaceId') == argument:
                return mode
    return ''


def _new_surface_materials(name, submesh, submesh_index, model, sluggie_dir, texture_file_map):
    """Re-create the Add-material surfaces of a rebuilt rigid submesh
    (``RigidRebuild.NewSurfaces``) as Blender materials, keyed by surface key,
    with the metadata Add material writes. The
    image comes from the appended PNG in tex/ or from the donor texture it
    binds; when the file is missing the node stays empty, so the next export
    stops on it instead of changing the texture silently."""
    from . import ExportTextures
    materials = {}
    rebuild = submesh.get("RigidRebuild") or submesh.get("SkinnedRebuild") or {}
    uv0 = (submesh.get("UVChannels") or [{}])[0]
    for entry in rebuild.get("NewSurfaces") or []:
        key = entry["SurfaceKey"]
        template = str(entry.get("TemplateSource") or "")
        assignment = entry.get("TextureAssignment") or {}
        tex_file = assignment.get("AdditionalTextureFileName")
        if tex_file is None and assignment.get("DonorTextureIndex") is not None:
            tex_file = (texture_file_map or {}).get(int(assignment["DonorTextureIndex"]))
        img_path = _resolve_texture_image_path(sluggie_dir, tex_file) if (sluggie_dir and tex_file) else None
        image = bpy.data.images.load(img_path, check_existing=True) if img_path else None
        if image is None:
            print(f"WARNING: {name}: PNG for new surface {key} ({tex_file}) not found; its texture node is left empty")
        wrap_s, wrap_t = int(uv0.get("WrapS", 1)), int(uv0.get("WrapT", 1))
        mat = _create_material(entry.get("MaterialName") or f"{name}_{key}", image, wrap_s)
        mat["SluggiesNewSurface"] = True
        mat.id_properties_ui("SluggiesNewSurface").update(
            description="Marks a Blender-only surface created by Add material, absent from the donor.")
        mat["SurfaceId"] = key
        mat.id_properties_ui("SurfaceId").update(
            description="Stable draw-state identity for this added surface. Do not delete.")
        mat["SluggiesSurfaceOwner"] = f"sm{submesh_index}"
        mat.id_properties_ui("SluggiesSurfaceOwner").update(
            description="The submesh (donor sm<N> or CustomSubmeshId) this surface belongs to.")
        mat["TemplateSource"] = template
        mat.id_properties_ui("TemplateSource").update(
            description="Donor or built-in template this surface's shading is cloned from.")
        try:
            mat["TemplateTextureIndex"] = ExportTextures._custom_submesh_template_texture_index(model, template)
        except ValueError:
            mat["TemplateTextureIndex"] = 0
        mat.id_properties_ui("TemplateTextureIndex").update(
            description="Donor texture index this surface's GX format clones; never a fallback texture.")
        strength = entry.get("SpecularStrength")
        _set_surface_material_metadata(mat, {
            "DisplayStateId": 7,
            "ShaderMode": _template_shader_mode_from_model(model, template),
            "DisplayStateParamBytes": bytes([int(strength) if strength is not None else 0, 0, 0]).hex(),
        })
        mat["WrapS"] = wrap_s
        mat["WrapT"] = wrap_t
        materials[key] = mat
    return materials


def _custom_submesh_image(name, assignment, sluggie_dir, texture_file_map):
    """The image a custom submesh surface binds: its added PNG from tex/, or
    the donor texture it names. None (with a console note) when the file is
    missing: the empty node then stops the next export instead of changing the
    texture silently, as for rebuilt surfaces."""
    tex_file = assignment.get("AdditionalTextureFileName")
    if tex_file is None and assignment.get("DonorTextureIndex") is not None:
        tex_file = (texture_file_map or {}).get(int(assignment["DonorTextureIndex"]))
    img_path = _resolve_texture_image_path(sluggie_dir, tex_file) if (sluggie_dir and tex_file) else None
    if img_path:
        return bpy.data.images.load(img_path, check_existing=True)
    print(f"WARNING: {name}: PNG {tex_file} not found; its texture node is left empty")
    return None


def _custom_submesh_materials(obj_name, cs, model, sluggie_dir, texture_file_map):
    """The surfaces of a decoded custom submesh *cs*, as Add New Submesh and
    Add material make them: the primary ``<id>_ds0`` first, then one material
    per ``AdditionalSurfaces`` entry, in slot order."""
    from . import TemplateSources
    from . import SurfacePlans
    from . import ExportTextures
    default_strength = TemplateSources.BUILTIN_DEFAULT_SPECULAR_STRENGTH
    primary = _create_material(
        f"{obj_name}_{cs.custom_submesh_id}_ds0",
        _custom_submesh_image(obj_name, cs.texture_assignment, sluggie_dir, texture_file_map),
        wrap_s=1,
    )
    SurfacePlans._set_custom_primary_surface(
        primary, cs.custom_submesh_id, _template_shader_mode_from_model(model, cs.template_source),
        cs.specular_strength if cs.specular_strength is not None else default_strength,
    )
    materials = [primary]
    for surface in cs.additional_surfaces:
        template = str(surface.get("TemplateSource") or "")
        mat = _create_material(
            surface.get("MaterialName") or f"{obj_name}_{surface['SurfaceKey']}",
            _custom_submesh_image(obj_name, surface.get("TextureAssignment") or {}, sluggie_dir, texture_file_map),
            wrap_s=1,
        )
        try:
            template_texture_index = ExportTextures._custom_submesh_template_texture_index(model, template)
        except ValueError:
            template_texture_index = 0
        strength = surface.get("SpecularStrength")
        SurfacePlans._apply_new_surface_plan(mat, {
            'kind': 'custom',
            'owner': cs.custom_submesh_id,
            'surface_id': surface['SurfaceKey'],
            'template_source': template,
            'template_texture_index': template_texture_index,
            'shader_mode': _template_shader_mode_from_model(model, template),
            'specular_strength': int(strength) if strength is not None else default_strength,
            'wrap_s': 1,
            'wrap_t': 1,
        })
        materials.append(mat)
    return materials
