import bpy
import json
import base64
import struct
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper

REQUIRED_PROPS = (
    "VertexBufferOffset",
    "VertexBufferLength",
    "VertexBufferCompCount",
    "VertexBufferQuantizeInfo",
)


def encode_vertex_buffer_edited(obj, comp_count, quant_info):
    """Re-quantize edited vertex positions (and normals if comp_count==6)
    back into the original binary format and return a base64 string."""
    mesh = obj.data
    fmt_nibble = quant_info >> 4
    shift = quant_info & 0xF
    divisor = 1 << shift
    is_float = fmt_nibble in [4, 7, 0xa]

    raw_bytes = bytearray()
    for v in mesh.vertices:
        comps = [v.co.x, v.co.y, v.co.z]
        if comp_count >= 6:
            comps += [v.normal.x, v.normal.y, v.normal.z]
        for val in comps:
            if is_float:
                raw_bytes += struct.pack('>f', val)
            else:
                raw_val = max(-32768, min(32767, round(val * divisor)))
                raw_bytes += struct.pack('>h', raw_val)

    return base64.b64encode(bytes(raw_bytes)).decode('ascii')


def validate_against_json(obj, json_submesh):
    """Return a list of mismatch descriptions, empty if everything matches."""
    vb = json_submesh.get("VertexBuffer", {})
    mismatches = []
    for prop in REQUIRED_PROPS:
        obj_val = obj.get(prop)
        json_val = vb.get(prop)
        if obj_val is None:
            mismatches.append(f"object missing custom property '{prop}'")
        elif str(obj_val) != str(json_val):
            mismatches.append(f"{prop}: object={obj_val}, json={json_val}")
    return mismatches


class SLUGGIES_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "sluggies.export_json"
    bl_label = "Export Sluggers intermediate"
    bl_description = "Write edited vertex data back into a Sluggers intermediate JSON file"
    bl_options = {"UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        # --- load and sanity-check the target JSON ---
        try:
            with open(self.filepath, 'r') as f:
                content = f.read().strip()
        except Exception as e:
            self.report({"ERROR"}, f"Could not read file: {e}")
            return {"CANCELLED"}

        if not content:
            self.report({"ERROR"}, "Target JSON file is empty.")
            return {"CANCELLED"}

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            self.report({"ERROR"}, f"JSON parse error: {e}")
            return {"CANCELLED"}

        if "SluggiesModel" not in data:
            self.report({"ERROR"}, "JSON does not contain a 'SluggiesModel' entry.")
            return {"CANCELLED"}

        submeshes = data["SluggiesModel"].get("Submeshes", [])

        # --- collect selected mesh objects that carry Sluggies custom properties ---
        candidates = [
            obj for obj in context.selected_objects
            if obj.type == 'MESH' and all(prop in obj for prop in REQUIRED_PROPS)
        ]

        if not candidates:
            self.report({"ERROR"},
                "No selected mesh objects with Sluggies custom properties found. "
                "Import the JSON first, then select the meshes you want to export.")
            return {"CANCELLED"}

        if len(candidates) > len(submeshes):
            self.report({"ERROR"},
                f"{len(candidates)} mesh(es) selected but the target JSON only defines "
                f"{len(submeshes)} submesh(es). Make sure you are exporting to the correct file.")
            return {"CANCELLED"}

        written = 0
        warnings = []

        for obj in candidates:
            # match by VertexBufferOffset (unique per submesh)
            target_submesh = next(
                (sm for sm in submeshes
                 if str(obj["VertexBufferOffset"]) == str(sm.get("VertexBuffer", {}).get("VertexBufferOffset"))),
                None
            )
            if target_submesh is None:
                warnings.append(
                    f"{obj.name}: no submesh with VertexBufferOffset="
                    f"{obj['VertexBufferOffset']} found in JSON — skipped."
                )
                continue

            mismatches = validate_against_json(obj, target_submesh)
            if mismatches:
                warnings.append(
                    f"{obj.name}: metadata mismatch ({'; '.join(mismatches)}) — skipped."
                )
                continue

            edited_data = encode_vertex_buffer_edited(
                obj,
                obj["VertexBufferCompCount"],
                obj["VertexBufferQuantizeInfo"],
            )
            target_submesh["VertexBufferEdited"] = {
                "VertexBufferDataEdited": edited_data
            }
            written += 1

        for w in warnings:
            self.report({"WARNING"}, w)

        if written == 0:
            self.report({"ERROR"}, "No submeshes written. Check the warnings above.")
            return {"CANCELLED"}

        with open(self.filepath, 'w') as f:
            json.dump(data, f, indent=2)

        self.report({"INFO"},
            f"Wrote edited vertex data for {written} submesh(es) to {self.filepath}")
        return {"FINISHED"}


def menu_func_export(self, context):
    self.layout.operator(SLUGGIES_OT_export.bl_idname, text="Sluggers intermediate (.json)")


def register():
    bpy.utils.register_class(SLUGGIES_OT_export)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.utils.unregister_class(SLUGGIES_OT_export)
