import bpy
import json
import os
import base64
import struct
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper


def decode_faces(submesh):
    """Decode base64 uint16 BE face data back to a list of [i,j,k] triplets."""
    faces_data = submesh.get("FacesData")
    face_count = submesh.get("FacesCount", 0)
    if not faces_data or not face_count:
        return []
    raw = base64.b64decode(faces_data)
    flat = list(struct.unpack(f'>{face_count * 3}H', raw))
    return [flat[i*3:i*3+3] for i in range(face_count)]


def decode_vertex_buffer(vb):
    """Decode a VertexBuffer dict into (positions, normals) lists of tuples.

    For skinned meshes CompCount==6 the raw buffer interleaves [X,Y,Z,NX,NY,NZ]
    per vertex. For non-skinned meshes CompCount==3 only [X,Y,Z] is stored.
    Returns normals as an empty list when CompCount < 6.
    """
    raw = base64.b64decode(vb["VertexBufferData"])
    quant = vb["VertexBufferQuantizeInfo"]
    comp_count = vb["VertexBufferCompCount"]
    fmt_nibble = quant >> 4
    divisor = 1 << (quant & 0xF)

    if fmt_nibble in [4, 7, 0xa]:
        comp_fmt, comp_size = '>f', 4
    else:  # 0, 3 -> signed int16
        comp_fmt, comp_size = '>h', 2

    stride = comp_count * comp_size
    num_verts = len(raw) // stride

    positions = []
    normals = []
    for i in range(num_verts):
        off = i * stride
        comps = [
            struct.unpack_from(comp_fmt, raw, off + j * comp_size)[0] / divisor
            for j in range(comp_count)
        ]
        positions.append((comps[0], comps[1], comps[2]))
        if comp_count >= 6:
            normals.append((comps[3], comps[4], comps[5]))

    return positions, normals


def decode_uv_channel(uv_channel):
    """Decode a UVChannel dict into:
    - coords: list of (s, t) float tuples decoded from the raw ST buffer
    - uv_faces: list of [i0, i1, i2] UV index triplets, aligned face-for-face with FacesData
    """
    raw = base64.b64decode(uv_channel["UVChannelData"])
    quant = uv_channel["UVChannelQuantizeInfo"]
    comp_count = uv_channel["UVChannelCompCount"]
    fmt_nibble = quant >> 4
    divisor = 1 << (quant & 0xF)

    if fmt_nibble in [4, 7, 0xa]:
        comp_fmt, comp_size = '>f', 4
    else:  # 0, 3 -> signed int16
        comp_fmt, comp_size = '>h', 2

    stride = comp_count * comp_size
    num_coords = len(raw) // stride

    coords = []
    for i in range(num_coords):
        off = i * stride
        s = struct.unpack_from(comp_fmt, raw, off)[0] / divisor
        t = struct.unpack_from(comp_fmt, raw, off + comp_size)[0] / divisor
        coords.append((s, t))

    # Decode per-face UV indices (uint16 BE triplets, same encoding as FacesData)
    uv_faces = []
    uv_faces_data = uv_channel.get("UVFacesData")
    if uv_faces_data:
        uv_raw = base64.b64decode(uv_faces_data)
        n = len(uv_raw) // 2
        flat = list(struct.unpack(f'>{n}H', uv_raw))
        uv_faces = [flat[i * 3 : i * 3 + 3] for i in range(n // 3)]

    return coords, uv_faces


def decode_color_channel(color_channel):
    """Decode a ColorChannel dict into a list of (r, g, b[, a]) float tuples and
    a list of [i0, i1, i2] color index triplets aligned face-for-face with FacesData."""
    raw = base64.b64decode(color_channel["ColorChannelData"])
    quant = color_channel["ColorChannelQuantizeInfo"]
    fmt = quant >> 4
    entry_sizes = {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}
    entry_size = entry_sizes.get(fmt, 2)
    num_colors = len(raw) // entry_size

    colors = []
    for i in range(num_colors):
        num = int.from_bytes(raw[i * entry_size : i * entry_size + entry_size], 'big')
        if fmt == 0:  # RGB565
            b = (num & 0b11111) / 31.0
            g = ((num >> 5) & 0b111111) / 63.0
            r = ((num >> 11) & 0b11111) / 31.0
            colors.append((r, g, b, 1.0))
        elif fmt == 3:  # RGBA4444
            a = (num & 0xF) / 15.0
            b = ((num >> 4) & 0xF) / 15.0
            g = ((num >> 8) & 0xF) / 15.0
            r = ((num >> 12) & 0xF) / 15.0
            colors.append((r, g, b, a))
        elif fmt in (1, 4):  # RGB8
            r = ((num >> 16) & 0xFF) / 255.0
            g = ((num >> 8) & 0xFF) / 255.0
            b = (num & 0xFF) / 255.0
            colors.append((r, g, b, 1.0))
        elif fmt in (2, 5):  # RGBA8
            r = ((num >> 24) & 0xFF) / 255.0
            g = ((num >> 16) & 0xFF) / 255.0
            b = ((num >> 8) & 0xFF) / 255.0
            a = (num & 0xFF) / 255.0
            colors.append((r, g, b, a))
        else:
            colors.append((1.0, 1.0, 1.0, 1.0))

    color_faces = []
    faces_data = color_channel.get("ColorFacesData")
    if faces_data:
        cf_raw = base64.b64decode(faces_data)
        n = len(cf_raw) // 2
        flat = list(struct.unpack(f'>{n}H', cf_raw))
        color_faces = [flat[i * 3 : i * 3 + 3] for i in range(n // 3)]

    return colors, color_faces


def decode_face_texture_indices(submesh, face_count):
    """Decode FaceTextureIndices into a list of int texture indices, one per face.

    Falls back to the TextureIndex of the first UV channel when the field is
    absent (e.g. files produced before this field was added).
    """
    data = submesh.get("FaceTextureIndices")
    if data:
        raw = base64.b64decode(data)
        n = len(raw) // 2
        return list(struct.unpack(f'>{n}H', raw))
    fallback = (submesh.get("UVChannels") or [{}])[0].get("TextureIndex") or 0
    return [fallback] * face_count


_GX_WRAP = {0: 'EXTEND', 1: 'REPEAT', 2: 'MIRROR'}


def _create_material(mat_name, uv_layer_name, image, wrap_s=1):
    """Build a UV Map -> Mapping -> Image Texture -> Diffuse BSDF -> Material Output node tree."""
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    out_node = nodes.new('ShaderNodeOutputMaterial')
    out_node.location = (800, 0)

    bsdf_node = nodes.new('ShaderNodeBsdfDiffuse')
    bsdf_node.location = (550, 0)

    tex_node = nodes.new('ShaderNodeTexImage')
    tex_node.location = (200, 0)
    if image:
        tex_node.image = image
    tex_node.extension = _GX_WRAP.get(wrap_s, 'REPEAT')

    mapping_node = nodes.new('ShaderNodeMapping')
    mapping_node.location = (-150, 0)

    uv_node = nodes.new('ShaderNodeUVMap')
    uv_node.location = (-450, 0)
    uv_node.uv_map = uv_layer_name

    links.new(uv_node.outputs['UV'], mapping_node.inputs['Vector'])
    links.new(mapping_node.outputs['Vector'], tex_node.inputs['Vector'])
    links.new(tex_node.outputs['Color'], bsdf_node.inputs['Color'])
    links.new(bsdf_node.outputs['BSDF'], out_node.inputs['Surface'])

    return mat


def build_mesh(name, positions, normals, faces, vb_meta, collection,
               uv_channels=None, color_channels=None,
               face_texture_indices=None, sluggie_dir=None,
               submesh_meta=None):
    """Create a Blender mesh object from a vertex list and link it to *collection*."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions, [], faces)
    mesh.update()

    if normals and len(normals) == len(positions) and len(faces):
        #mesh.use_auto_smooth = True 
        normals_per_loop = [normals[i] for face in faces for i in face]
        mesh.normals_split_custom_set(normals_per_loop)

    if uv_channels:
        for ch_ind, uv_channel in enumerate(uv_channels):
            coords, uv_faces = decode_uv_channel(uv_channel)
            if not coords or not uv_faces:
                continue
            # Use palette name as UV layer name; fall back to "uv<index>"
            layer_name = uv_channel.get("PaletteName") or f"uv{ch_ind}"
            uv_layer = mesh.uv_layers.new(name=layer_name)
            for poly in mesh.polygons:
                face_idx = poly.index
                if face_idx >= len(uv_faces):
                    continue
                uv_tri = uv_faces[face_idx]
                for loop_offset, loop_idx in enumerate(poly.loop_indices):
                    uv_idx = uv_tri[loop_offset % 3]
                    if uv_idx < len(coords):
                        s, t = coords[uv_idx]
                        # GX V increases downward; flip to match Blender convention
                        uv_layer.data[loop_idx].uv = (s, 1.0 - t)

    if color_channels:
        for color_channel in color_channels:
            ch_idx = color_channel.get("ColorChannelIndex", 0)
            colors, color_faces = decode_color_channel(color_channel)
            if not colors or not color_faces:
                continue
            layer_name = f"color{ch_idx}"
            vcol_layer = mesh.color_attributes.new(name=layer_name, type='FLOAT_COLOR', domain='CORNER')
            for poly in mesh.polygons:
                face_idx = poly.index
                if face_idx >= len(color_faces):
                    continue
                col_tri = color_faces[face_idx]
                for loop_offset, loop_idx in enumerate(poly.loop_indices):
                    col_idx = col_tri[loop_offset % 3]
                    if col_idx < len(colors):
                        vcol_layer.data[loop_idx].color = colors[col_idx]

    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    obj["VertexBufferOffset"]       = vb_meta["VertexBufferOffset"]
    obj["VertexBufferLength"]       = vb_meta["VertexBufferLength"]
    obj["VertexBufferCompCount"]    = vb_meta["VertexBufferCompCount"]
    obj["VertexBufferQuantizeInfo"] = vb_meta["VertexBufferQuantizeInfo"]

    if submesh_meta is not None:
        mesh_hdr_offset = submesh_meta.get("SubmeshOffset")
        pos_ptr_offset  = submesh_meta.get("PositionDataPtrFieldOffset")
        vcount_offset   = submesh_meta.get("VertexCountFieldOffset")
        if mesh_hdr_offset is not None:
            obj["MeshDataHeaderOffset"] = mesh_hdr_offset
            obj.id_properties_ui("MeshDataHeaderOffset").update(
                description="Absolute file offset of the mesh data header (SubmeshOffset); used as relative-base for pointer patching")
        if pos_ptr_offset is not None:
            obj["PositionDataPtrFieldOffset"] = pos_ptr_offset
            obj.id_properties_ui("PositionDataPtrFieldOffset").update(
                description="Absolute file offset of the 4-byte position-data pointer field in the mesh data header")
        if vcount_offset is not None:
            obj["VertexCountFieldOffset"] = vcount_offset
            obj.id_properties_ui("VertexCountFieldOffset").update(
                description="Absolute file offset of the 2-byte vertex-count field in the mesh data header")

    # Store UV channel material binding metadata as custom properties
    if uv_channels:
        for uv_channel in uv_channels:
            ch_ind = uv_channel.get("UVChannelIndex", 0)
            prefix = f"UV{ch_ind}_"
            tex_idx = uv_channel.get("TextureIndex")
            wrap_s  = uv_channel.get("WrapS")
            wrap_t  = uv_channel.get("WrapT")
            if tex_idx is not None:
                obj[prefix + "TextureIndex"] = tex_idx
                obj.id_properties_ui(prefix + "TextureIndex").update(
                    description=f"TPL texture index bound to UV channel {ch_ind}")
            if wrap_s is not None:
                obj[prefix + "WrapS"] = wrap_s
                obj.id_properties_ui(prefix + "WrapS").update(
                    description=f"GX S-axis wrap mode for UV channel {ch_ind}")
            if wrap_t is not None:
                obj[prefix + "WrapT"] = wrap_t
                obj.id_properties_ui(prefix + "WrapT").update(
                    description=f"GX T-axis wrap mode for UV channel {ch_ind}")

    # Register UI metadata so properties appear in the Custom Properties panel
    ui = obj.id_properties_ui("VertexBufferOffset")
    ui.update(description="Starting offset of the vertex buffer in dt_na.dat (hex)")
    ui = obj.id_properties_ui("VertexBufferLength")
    ui.update(description="Length of the vertex buffer in bytes")
    ui = obj.id_properties_ui("VertexBufferCompCount")
    ui.update(description="Components per vertex: 3=XYZ, 6=XYZ+Normal interleaved")
    ui = obj.id_properties_ui("VertexBufferQuantizeInfo")
    ui.update(description="Quantization byte: high nibble=format, low nibble=divisor exponent")

    # Create Blender materials and assign per-face material indices
    if uv_channels and face_texture_indices and sluggie_dir is not None:
        # Map TextureIndex -> the UV channel that references it (first match wins)
        tex_to_uv = {}
        for uv_ch in uv_channels:
            idx = uv_ch.get('TextureIndex')
            if idx is not None and idx not in tex_to_uv:
                tex_to_uv[idx] = uv_ch

        unique_tex = sorted(set(face_texture_indices))
        mat_slot = {}  # TextureIndex -> material slot index
        for slot_idx, tex_idx in enumerate(unique_tex):
            mat_slot[tex_idx] = slot_idx
            uv_ch = tex_to_uv.get(tex_idx) or uv_channels[0]
            ch_ind = uv_ch.get('UVChannelIndex', 0)
            layer_name = uv_ch.get('PaletteName') or f'uv{ch_ind}'
            wrap_s = uv_ch.get('WrapS', 1)
            img_path = os.path.join(sluggie_dir, 'tex', f'{tex_idx}.png')
            image = bpy.data.images.load(img_path, check_existing=True) if os.path.exists(img_path) else None
            mat = _create_material(f'{name}_mat{tex_idx}', layer_name, image, wrap_s)
            obj.data.materials.append(mat)

        for poly in mesh.polygons:
            fi = poly.index
            tex_idx = face_texture_indices[fi] if fi < len(face_texture_indices) else unique_tex[0]
            poly.material_index = mat_slot.get(tex_idx, 0)

    return obj


class SLUGGIES_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "sluggies.import_json"
    bl_label = "Import Sluggers intermediate"
    bl_description = "Import a Sluggers intermediate JSON file"
    bl_options = {"UNDO"}

    filename_ext = ".sluggie"
    filter_glob: StringProperty(default="*.sluggie", options={"HIDDEN"})

    def execute(self, context):
        with open(self.filepath, 'r') as f:
            data = json.load(f)

        model = data["SluggiesModel"]
        model_number = model["ChunkNumber"]
        model_offset_hex = model["ModelOffset"]
        submeshes = model.get("Submeshes", [])
        sluggie_dir = os.path.dirname(self.filepath)

        collection = context.collection
        imported = 0
        for i, submesh in enumerate(submeshes):
            vb = submesh.get("VertexBuffer")
            if not vb:
                continue
            positions, normals = decode_vertex_buffer(vb)
            faces = decode_faces(submesh)
            uv_channels = submesh.get("UVChannels", [])
            color_channels = submesh.get("ColorChannels", [])
            face_texture_indices = decode_face_texture_indices(submesh, len(faces))
            mesh_name = f"{model_number}_{model_offset_hex}_submesh{i}"
            build_mesh(mesh_name, positions, normals, faces, vb, collection,
                       uv_channels, color_channels,
                       face_texture_indices=face_texture_indices, sluggie_dir=sluggie_dir,
                       submesh_meta=submesh)
            imported += 1

        context.view_layer.update()
        self.report({"INFO"}, f"Imported {imported} submesh(es) from {self.filepath}")
        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(SLUGGIES_OT_import.bl_idname, text="Sluggers intermediate (.sluggie)")


def register():
    bpy.utils.register_class(SLUGGIES_OT_import)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(SLUGGIES_OT_import)
