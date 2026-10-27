"""The 'Adapt' box's Clean Up step: tidies meshes imported from elsewhere
(not from a .sluggie) before they are ported onto a Sluggers donor."""

import bmesh
import bpy

from . import ModelCheckCore


# Corner attribute that carries custom normals through the loose-geometry delete.
_NORMALS_ATTR = "_sluggies_cleanup_normal"

# Modifier types Clean Up removes. Merge by Distance is left to the user.
_SMOOTH_MODIFIERS = {'SMOOTH', 'CORRECTIVE_SMOOTH', 'LAPLACIANSMOOTH'}
_MASK_MODIFIERS = {'MASK'}
# Physics tab simulations kept as modifiers (rigid bodies and force fields
# are not modifiers: _remove_physics clears them separately).
_PHYSICS_MODIFIERS = {'CLOTH', 'SOFT_BODY', 'COLLISION', 'FLUID', 'DYNAMIC_PAINT'}


def is_sluggies_mesh(obj):
    """A mesh the .sluggie importer or the add-on made (donor submesh, custom
    submesh, collision object, a set-aside `.backup` body): Clean Up leaves
    these alone."""
    if obj.get('SluggiesCustomSubmesh') or obj.get(ModelCheckCore.BACKUP_PROP) \
            or any(prop in obj for prop in ModelCheckCore.REQUIRED_PROPS):
        return True
    collision = getattr(obj, 'sluggies_collision', None)
    return collision is not None and collision.enabled


def cleanup_targets(objects):
    """(meshes to clean, Sluggies meshes skipped) among *objects*. Linked
    library data cannot be edited and is left out of both."""
    targets, skipped = [], []
    for obj in objects:
        if obj.type != 'MESH' or obj.library is not None or obj.data.library is not None:
            continue
        (skipped if is_sluggies_mesh(obj) else targets).append(obj)
    return targets, skipped


def _remove_loose(mesh):
    """Delete vertices and edges that no face uses (no Merge by Distance:
    that is left to the user). Custom normals are kept as they were. Returns
    (loose vertices, loose edges) removed; a mesh without any is left
    untouched."""
    face_edges = {key for p in mesh.polygons for key in p.edge_keys}
    if all(e.key in face_edges for e in mesh.edges) \
            and len({v for p in mesh.polygons for v in p.vertices}) == len(mesh.vertices):
        return 0, 0

    custom_normals = mesh.has_custom_normals
    if custom_normals:
        attr = mesh.attributes.new(_NORMALS_ATTR, 'FLOAT_VECTOR', 'CORNER')
        attr.data.foreach_set('vector', [c for n in mesh.corner_normals for c in n.vector])

    bm = bmesh.new()
    bm.from_mesh(mesh)
    before = len(bm.verts)
    loose_edges = [e for e in bm.edges if not e.link_faces]
    bmesh.ops.delete(bm, geom=loose_edges, context='EDGES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_edges], context='VERTS')
    loose_verts = before - len(bm.verts)

    bm.to_mesh(mesh)
    bm.free()

    if custom_normals:
        attr = mesh.attributes[_NORMALS_ATTR]
        flat = [0.0] * (len(mesh.loops) * 3)
        attr.data.foreach_get('vector', flat)
        mesh.attributes.remove(attr)
        mesh.normals_split_custom_set([flat[i:i + 3] for i in range(0, len(flat), 3)])
    mesh.update()
    return loose_verts, len(loose_edges)


def _remove_modifiers(obj, types):
    """Delete *obj*'s modifiers of the given types. Returns how many."""
    doomed = [m for m in obj.modifiers if m.type in types]
    for mod in doomed:
        obj.modifiers.remove(mod)
    return len(doomed)


def _remove_physics(obj):
    """Delete *obj*'s physics simulations: the Physics tab's modifiers
    (cloth, soft body, collision, fluid, dynamic paint), its rigid body and
    rigid body constraint, and its force field. Returns how many."""
    removed = _remove_modifiers(obj, _PHYSICS_MODIFIERS)
    for attr, op in (('rigid_body', bpy.ops.rigidbody.object_remove),
                     ('rigid_body_constraint', bpy.ops.rigidbody.constraint_remove)):
        if getattr(obj, attr) is not None:
            with bpy.context.temp_override(object=obj, active_object=obj):
                op()
            removed += 1
    if obj.field is not None and obj.field.type != 'NONE':
        obj.field.type = 'NONE'
        removed += 1
    return removed


def _remove_empty_vertex_groups(obj):
    """Delete *obj*'s vertex groups that weight no vertex above 0."""
    used = {g.group for v in obj.data.vertices for g in v.groups if g.weight > 0.0}
    empty = [g for g in obj.vertex_groups if g.index not in used]
    for group in empty:
        obj.vertex_groups.remove(group)
    return len(empty)


def _remove_unused_material_slots(mesh):
    """Delete *mesh*'s material slots no face uses (faces keep their material:
    removing a slot shifts the later indices down)."""
    used = {p.material_index for p in mesh.polygons}
    unused = [i for i in range(len(mesh.materials)) if i not in used]
    for index in reversed(unused):
        mesh.materials.pop(index=index)
    return len(unused)


def _remove_unreachable_nodes(material):
    """Delete the nodes of *material* that never reach its active Material
    Output (through unmuted links), other outputs included, and frames left
    empty. A material without an output is left alone."""
    if material.node_tree is None or not getattr(material, 'use_nodes', True):
        return 0
    tree = material.node_tree
    outputs = [n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL']
    roots = [n for n in outputs if n.is_active_output] or outputs
    if not roots:
        return 0
    links_to = {}
    for link in tree.links:
        if link.is_valid and not link.is_muted:
            links_to.setdefault(link.to_node, []).append(link.from_node)
    reached = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node not in reached:
            reached.add(node)
            stack.extend(links_to.get(node, []))
    used_frames = set()
    for node in reached:
        frame = node.parent
        while frame is not None:
            used_frames.add(frame)
            frame = frame.parent
    doomed = [n for n in tree.nodes if n not in reached and (n.type != 'FRAME' or n not in used_frames)]
    for node in doomed:
        tree.nodes.remove(node)
    return len(doomed)


# Shader inputs Convert Materials keeps: the colour (Principled "Base Color",
# Diffuse/Emission "Color"); shader-type inputs (a Mix Shader's) lead on to
# the shaders behind them.
_COLOR_INPUTS = ("Base Color", "Color")


def _remove_non_color_links(material):
    """Unlink every input of the material's shader nodes except their colour
    and shader inputs, and every Material Output input except Surface: Sluggers
    surfaces take only a colour texture. Returns the links removed."""
    if material.node_tree is None or not getattr(material, 'use_nodes', True):
        return 0
    tree = material.node_tree
    doomed = []
    for link in tree.links:
        node, socket = link.to_node, link.to_socket
        if node.type == 'OUTPUT_MATERIAL':
            keep = socket.identifier == 'Surface'
        elif any(output.type == 'SHADER' for output in node.outputs):
            keep = socket.type == 'SHADER' or socket.name in _COLOR_INPUTS
        else:
            keep = True
        if not keep:
            doomed.append(link)
    for link in doomed:
        tree.links.remove(link)
    return len(doomed)


def convert_material_nodes(material):
    """Convert Materials' node cleanup: drop the node strings that do not
    feed a shader's colour, then every node that no longer reaches the
    Material Output. Returns (links removed, nodes removed)."""
    links = _remove_non_color_links(material)
    return links, _remove_unreachable_nodes(material)


def run_cleanup(targets):
    """Clean *targets* (mesh objects); mesh data shared between them is
    cleaned once. Materials themselves are left alone (their node cleanup,
    convert_material_nodes, belongs to the Convert Materials step). Returns
    the counts for the summary."""
    counts = dict(objects=len(targets), smooth_modifiers=0, mask_modifiers=0, physics=0,
                  loose_verts=0, loose_edges=0, vertex_groups=0, material_slots=0)
    for obj in targets:
        counts['smooth_modifiers'] += _remove_modifiers(obj, _SMOOTH_MODIFIERS)
        counts['mask_modifiers'] += _remove_modifiers(obj, _MASK_MODIFIERS)
        counts['physics'] += _remove_physics(obj)
    meshes = list(dict.fromkeys(obj.data for obj in targets))
    for mesh in meshes:
        loose_verts, loose_edges = _remove_loose(mesh)
        counts['loose_verts'] += loose_verts
        counts['loose_edges'] += loose_edges
    for obj in targets:
        counts['vertex_groups'] += _remove_empty_vertex_groups(obj)
    for mesh in meshes:
        counts['material_slots'] += _remove_unused_material_slots(mesh)
    return counts


def summary_lines(counts):
    """Short summary lines for *counts* (run_cleanup's result)."""
    n = counts['objects']
    lines = [f"Cleaned {n} mesh{'es' if n != 1 else ''}"]
    done = [
        (counts['smooth_modifiers'], "smoothing modifiers removed"),
        (counts['mask_modifiers'], "mask modifiers removed"),
        (counts['physics'], "physics simulations removed"),
        (counts['loose_verts'], "loose vertices removed"),
        (counts['loose_edges'], "loose edges removed"),
        (counts['vertex_groups'], "empty vertex groups removed"),
        (counts['material_slots'], "unused material slots removed"),
    ]
    lines += [f"{count:,} {text}" for count, text in done if count]
    if len(lines) == 1:
        lines.append("Nothing to clean")
    return lines
