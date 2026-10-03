"""ModelData -> glTF 2.0 binary (.glb), written by `start.py --export --glb`.

For viewing models in Blender and other glTF tools; the .sluggie files remain the
editing format. Layout (conventions measured by the Sluggers Characters Beta tool's
gltf_model.py, which we may reuse):

- Game space is glTF space (Y-up, right-handed): positions go in unchanged. glTF UVs
  have their origin top-left like the game's, so UVs go in unchanged too.
- Triangle corners keep gpl.getTriangles' order: it already faces glTF's way (Mario:
  99.6 % of faces agree with their vertex normals; the external tool's swap does not
  apply to this reader).
- One node per bone ("bone_<id>", TRS from its SRT), children under their parent when
  the bone is relative to it.
- Submesh 0 of a model with an SKN section is skinned (model-space positions). Every
  other submesh is stored in its owner bone's space and becomes a child node of that bone.
  Every model with bones gets a skin, even one no mesh uses: Blender only builds an
  armature from skin joints, so stadium and prop bones would otherwise import as empties.
- One primitive per draw group; its material binds the texture of its lowest texture
  layer by URI (tex/<dolphin name>.png, the files the export writes next to the .glb).
  An L_ model has no TEX section and binds its high-poly partner's textures by index, so
  the caller passes the partner's textures and a URI prefix into the partner's folder.
- Vertex colour 0 goes in as the custom attribute _COLOR0, which renderers ignore (a
  standard COLOR_0 would be multiplied into the base colour).
"""
import json
import os
import struct

import numpy as np

FLOAT, USHORT, UINT = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
REPEAT = 10497
MAX_INFLUENCES = 8


class _Glb:
    def __init__(self):
        self.bin = bytearray()
        self.gltf = {"asset": {"version": "2.0", "generator": "Sluggies-dat-tools glb_export.py"},
                     "buffers": [], "bufferViews": [], "accessors": []}

    def view(self, data, target=None):
        self.bin += bytes(-len(self.bin) % 4)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.gltf["bufferViews"].append(v)
        return len(self.gltf["bufferViews"]) - 1

    def accessor(self, arr, kind, ctype, target=ARRAY_BUFFER, minmax=False):
        arr = np.ascontiguousarray(arr)
        a = {"bufferView": self.view(arr.tobytes(), target), "componentType": ctype,
             "count": len(arr), "type": kind}
        if minmax:
            a["min"] = [float(v) for v in arr.min(0)]
            a["max"] = [float(v) for v in arr.max(0)]
        self.gltf["accessors"].append(a)
        return len(self.gltf["accessors"]) - 1

    def glb(self):
        self.bin += bytes(-len(self.bin) % 4)
        self.gltf["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(self.gltf, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        return (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.bin))
                + struct.pack("<II", len(js), 0x4E4F534A) + js
                + struct.pack("<II", len(self.bin), 0x004E4942) + bytes(self.bin))


def gltf_rotation(quaternion):
    """helper.SRT stores [-w, x, y, z]; glTF wants a unit (x, y, z, w)."""
    w, x, y, z = -quaternion[0], quaternion[1], quaternion[2], quaternion[3]
    q = np.array([x, y, z, w], dtype=float)
    norm = np.linalg.norm(q)
    q = q / norm if norm else np.array([0.0, 0.0, 0.0, 1.0])
    return q if q[3] >= 0 else -q


def trs_matrix(t, q, s):
    """Column-vector 4x4 from glTF translation, rotation (x, y, z, w) and scale."""
    x, y, z, w = q
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.identity(4)
    m[:3, :3] = r * np.array(s, dtype=float)
    m[:3, 3] = t
    return m


def _bone_nodes(bones):
    """(nodes, node index by bone id, root node indices, world matrix by bone id)."""
    order = sorted(bones, key=lambda b: b.id)
    node_of = {b.id: i for i, b in enumerate(order)}
    by_id = {b.id: b for b in order}
    nodes, local = [], {}
    for b in order:
        t = [float(v) for v in b.pose.translation]
        q = gltf_rotation(b.pose.quaternion)
        s = [float(v) for v in b.pose.scale]
        nodes.append({"name": f"bone_{b.id}", "translation": t,
                      "rotation": [float(v) for v in q], "scale": s})
        local[b.id] = trs_matrix(t, q, s)
    roots = []
    for b in order:
        if b.relative and b.parent in node_of:
            nodes[node_of[b.parent]].setdefault("children", []).append(node_of[b.id])
        else:
            roots.append(node_of[b.id])
    world = {}

    def world_of(bone_id):
        if bone_id not in world:
            b = by_id[bone_id]
            parent = b.parent if b.relative and b.parent in by_id else None
            world[bone_id] = (world_of(parent) @ local[bone_id]) if parent is not None else local[bone_id]
        return world[bone_id]
    for b in order:
        world_of(b.id)
    return order, nodes, node_of, roots, world


def _texture_has_alpha(path):
    try:
        from PIL import Image
        with Image.open(path) as img:
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            return img.getchannel("A").getextrema()[0] < 255
    except (OSError, ValueError):
        return False


def _vertex_weights(bones, geom_ind):
    """{vertex index: [(bone id, weight), ...]} for one geometry, heaviest first."""
    out = {}
    for b in bones:
        if b.GEOID != geom_ind:
            continue
        for vertex, weight in b.influences.items():
            if weight > 0:
                out.setdefault(vertex, []).append((b.id, float(weight)))
    for vertex in out:
        out[vertex].sort(key=lambda bw: -bw[1])
    return out


def _owner_bone(bones, geom_ind):
    owners = [b.id for b in bones if b.GEOID == geom_ind and not getattr(b, "skinned", False)]
    return owners[-1] if owners else None


def model_data_to_glb(data, name="model", tex_dir=None, textures=None, tex_uri_prefix="tex/"):
    """Bytes of a .glb for a model0.ModelData. `tex_dir` is the folder holding the
    exported PNGs (used to give textures with alpha a MASK material). `textures`
    ({index: png name}) overrides data.textures, with `tex_uri_prefix` the URI path to
    them relative to the .glb (for an L_ model: its partner's)."""
    g = _Glb()
    gl = g.gltf
    order, nodes, node_of, roots, world = _bone_nodes(data.bones)
    gl["nodes"] = nodes
    scene_nodes = list(roots)

    if textures is None:
        textures = data.textures
    textures = {k: png for k, png in textures.items() if png} if data.export_tex else {}
    tex_of, mat_of = {}, {}
    if textures:
        gl["images"], gl["textures"], gl["materials"] = [], [], []
        gl["samplers"] = [{"wrapS": REPEAT, "wrapT": REPEAT}]

    def material(k):
        if k not in textures:
            return None
        if k not in mat_of:
            gl["images"].append({"name": f"tex{k}", "uri": f"{tex_uri_prefix}{textures[k]}"})
            gl["textures"].append({"source": len(gl["images"]) - 1, "sampler": 0})
            tex_of[k] = len(gl["textures"]) - 1
            mat = {"name": f"tex{k}", "doubleSided": True,
                   "pbrMetallicRoughness": {"baseColorTexture": {"index": tex_of[k]},
                                            "metallicFactor": 0.0, "roughnessFactor": 1.0}}
            if tex_dir and _texture_has_alpha(os.path.join(tex_dir, textures[k])):
                mat["alphaMode"], mat["alphaCutoff"] = "MASK", 0.5
            gl["materials"].append(mat)
            mat_of[k] = len(gl["materials"]) - 1
        return mat_of[k]

    has_skin = bool(order) and data.model is not None and getattr(data.model, "SKN", None) is not None
    if order:
        ibm = np.array([np.linalg.inv(world[b.id]).T.reshape(-1) for b in order], dtype=np.float32)
        gl["skins"] = [{"joints": list(range(len(order))), "skeleton": roots[0] if roots else 0,
                        "inverseBindMatrices": g.accessor(ibm, "MAT4", FLOAT, target=None)}]

    gl["meshes"] = []
    for geom_ind, geom in enumerate(data.geometries):
        skinned = has_skin and geom_ind == 0
        weights = _vertex_weights(data.bones, geom_ind) if skinned else {}
        position_normals = getattr(geom, "position_normals", None)
        prims = []
        for mesh in geom.meshes:
            layers = sorted(mesh.texture_layers)
            use_lighting = "lighting" in mesh.active_descriptors
            use_color = "color0" in mesh.active_descriptors
            keys, index, corners = {}, [], []
            for tri in mesh.triangles:
                for v in tri:
                    key = (v.position,
                           getattr(v, "lighting", None) if use_lighting else None,
                           v.colors.get(0) if use_color else None,
                           tuple(v.tex_coords.get(layer) for layer in layers))
                    if key not in keys:
                        keys[key] = len(corners)
                        corners.append(key)
                    index.append(keys[key])
            if not corners:
                continue
            P = np.array([geom.positions[p][:3] for p, _, _, _ in corners], dtype=np.float32)
            attrs = {"POSITION": g.accessor(P, "VEC3", FLOAT, minmax=True)}
            if use_lighting:
                N = []
                for p, n, _, _ in corners:
                    if n is not None and n < len(geom.normals):
                        N.append(geom.normals[n][:3])
                    elif position_normals is not None and p < len(position_normals):
                        N.append(position_normals[p][:3])
                    else:
                        N = None
                        break
                if N is not None:
                    N = np.array(N, dtype=np.float32)
                    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
                    attrs["NORMAL"] = g.accessor(N, "VEC3", FLOAT)
            for uv_set, layer in enumerate(layers):
                coords = geom.tex_coords[layer]
                T = np.array([coords[c[3][uv_set]][:2] if c[3][uv_set] is not None and c[3][uv_set] < len(coords)
                              else (0.0, 0.0) for c in corners], dtype=np.float32)
                attrs[f"TEXCOORD_{uv_set}"] = g.accessor(T, "VEC2", FLOAT)
            if use_color:
                C = []
                for _, _, c, _ in corners:
                    rgba = list(geom.colors[c]) if c is not None and c < len(geom.colors) else [1.0, 1.0, 1.0, 1.0]
                    C.append((rgba + [1.0])[:4] if len(rgba) == 3 else rgba[:4])
                attrs["_COLOR0"] = g.accessor(np.array(C, dtype=np.float32), "VEC4", FLOAT)
            if skinned:
                J, W = [], []
                for p, _, _, _ in corners:
                    w = weights.get(p, [])[:MAX_INFLUENCES] or [(order[0].id, 1.0)]
                    total = sum(x for _, x in w)
                    w = [(node_of[bone_id], x / total) for bone_id, x in w] + [(0, 0.0)] * MAX_INFLUENCES
                    J.append([j for j, _ in w[:MAX_INFLUENCES]])
                    W.append([x for _, x in w[:MAX_INFLUENCES]])
                J, W = np.array(J, dtype=np.uint16), np.array(W, dtype=np.float32)
                attrs["JOINTS_0"] = g.accessor(J[:, :4], "VEC4", USHORT)
                attrs["WEIGHTS_0"] = g.accessor(W[:, :4], "VEC4", FLOAT)
                if W[:, 4:].any():
                    attrs["JOINTS_1"] = g.accessor(J[:, 4:], "VEC4", USHORT)
                    attrs["WEIGHTS_1"] = g.accessor(W[:, 4:], "VEC4", FLOAT)
            prim = {"attributes": attrs, "mode": 4,
                    "indices": g.accessor(np.array(index, dtype=np.uint32), "SCALAR", UINT, ELEMENT_ARRAY_BUFFER)}
            mat = material(mesh.texture_layers[layers[0]]) if layers else None
            if mat is not None:
                prim["material"] = mat
            prims.append(prim)
        if not prims:
            continue
        gl["meshes"].append({"name": f"submesh{geom_ind}", "primitives": prims})
        node = {"name": f"submesh{geom_ind}", "mesh": len(gl["meshes"]) - 1}
        gl["nodes"].append(node)
        ni = len(gl["nodes"]) - 1
        owner = None if skinned else _owner_bone(data.bones, geom_ind)
        if skinned:
            node["skin"] = 0
            scene_nodes.append(ni)
        elif owner is not None:
            gl["nodes"][node_of[owner]].setdefault("children", []).append(ni)
        else:
            scene_nodes.append(ni)

    gl["scenes"] = [{"name": name, "nodes": scene_nodes}]
    gl["scene"] = 0
    return g.glb()


def write_glb(data, out_dir, name, partner=None):
    """Write <out_dir>/<name>.glb. `partner` is (folder name, {index: png name}) of the
    high-poly model whose textures an L_ model binds; the folder must be a sibling of
    `out_dir`'s model folder."""
    tex_dir = os.path.join(out_dir, "tex")
    textures, prefix = None, "tex/"
    if partner is not None:
        folder, textures = partner
        tex_dir = os.path.join(out_dir, "..", folder, "tex")
        prefix = f"../{folder}/tex/"
    path = os.path.join(out_dir, name + ".glb")
    with open(path, "wb") as f:
        f.write(model_data_to_glb(data, name=name, tex_dir=tex_dir, textures=textures, tex_uri_prefix=prefix))
    return path
