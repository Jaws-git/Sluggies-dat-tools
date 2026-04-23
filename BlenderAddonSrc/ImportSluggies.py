import bpy
import json
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


def build_mesh(name, positions, normals, faces, vb_meta, collection):
    """Create a Blender mesh object from a vertex list and link it to *collection*."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions, [], faces)
    mesh.update()

    if normals and len(normals) == len(positions) and len(faces):
        #mesh.use_auto_smooth = True 
        normals_per_loop = [normals[i] for face in faces for i in face]
        mesh.normals_split_custom_set(normals_per_loop)

    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    obj["VertexBufferOffset"]       = vb_meta["VertexBufferOffset"]
    obj["VertexBufferLength"]       = vb_meta["VertexBufferLength"]
    obj["VertexBufferCompCount"]    = vb_meta["VertexBufferCompCount"]
    obj["VertexBufferQuantizeInfo"] = vb_meta["VertexBufferQuantizeInfo"]

    # Register UI metadata so properties appear in the Custom Properties panel
    ui = obj.id_properties_ui("VertexBufferOffset")
    ui.update(description="Starting offset of the vertex buffer in dt_na.dat (hex)")
    ui = obj.id_properties_ui("VertexBufferLength")
    ui.update(description="Length of the vertex buffer in bytes")
    ui = obj.id_properties_ui("VertexBufferCompCount")
    ui.update(description="Components per vertex: 3=XYZ, 6=XYZ+Normal interleaved")
    ui = obj.id_properties_ui("VertexBufferQuantizeInfo")
    ui.update(description="Quantization byte: high nibble=format, low nibble=divisor exponent")

    return obj


class SLUGGIES_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "sluggies.import_json"
    bl_label = "Import Sluggers intermediate"
    bl_description = "Import a Sluggers intermediate JSON file"
    bl_options = {"UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        with open(self.filepath, 'r') as f:
            data = json.load(f)

        model = data["SluggiesModel"]
        model_number = model["ChunkNumber"]
        model_offset_hex = model["ModelOffset"]
        submeshes = model.get("Submeshes", [])

        collection = context.collection
        imported = 0
        for i, submesh in enumerate(submeshes):
            vb = submesh.get("VertexBuffer")
            if not vb:
                continue
            positions, normals = decode_vertex_buffer(vb)
            faces = decode_faces(submesh)
            mesh_name = f"{model_number}_{model_offset_hex}_submesh{i}"
            build_mesh(mesh_name, positions, normals, faces, vb, collection)
            imported += 1

        context.view_layer.update()
        self.report({"INFO"}, f"Imported {imported} submesh(es) from {self.filepath}")
        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(SLUGGIES_OT_import.bl_idname, text="Sluggers intermediate (.json)")


def register():
    bpy.utils.register_class(SLUGGIES_OT_import)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(SLUGGIES_OT_import)
