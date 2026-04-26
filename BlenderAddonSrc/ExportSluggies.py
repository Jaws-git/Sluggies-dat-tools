import bpy
import json
import os
import base64
import struct
import subprocess
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


def encode_uv_channel_edited(obj, json_channel):
    """Re-quantize Blender UV layer back into the game's ST coordinate format.

    Writes each Blender UV value back into its ORIGINAL slot position, using
    UVFacesData to look up which slot index the draw list expects for each face
    loop. This preserves the original coord-array layout so the unmodified draw
    list in the .dat file keeps working correctly.

    Returns uv_channel_data_b64, or None if the matching UV layer is not found.
    Warns (via returned string list) when a slot receives two conflicting values
    (i.e. the user split a UV seam that was previously shared).
    """
    palette_name = json_channel.get("PaletteName", "")
    ch_ind = json_channel.get("UVChannelIndex", 0)
    layer_name = palette_name or f"uv{ch_ind}"

    mesh_data = obj.data
    uv_layer = mesh_data.uv_layers.get(layer_name)
    if uv_layer is None:
        return None

    quant_info = json_channel["UVChannelQuantizeInfo"]
    comp_count = json_channel["UVChannelCompCount"]
    expected_length = json_channel["UVChannelLength"]
    fmt_nibble = quant_info >> 4
    shift = quant_info & 0xF
    divisor = 1 << shift
    is_float = fmt_nibble in [4, 7, 0xa]
    comp_size = 4 if is_float else 2
    num_slots = expected_length // (comp_count * comp_size)

    # Decode original per-face UV slot indices from UVFacesData
    uv_faces_raw = base64.b64decode(json_channel["UVFacesData"])
    n = len(uv_faces_raw) // 2
    flat = list(struct.unpack(f'>{n}H', uv_faces_raw))
    original_uv_faces = [flat[i * 3 : i * 3 + 3] for i in range(n // 3)]

    # Fill output slots using original slot assignments
    # Each slot is (qs, qt); None means the slot was never referenced by a loop.
    output_slots = [None] * num_slots
    conflicts = []

    for poly in mesh_data.polygons:
        face_idx = poly.index
        if face_idx >= len(original_uv_faces):
            continue
        uv_tri = original_uv_faces[face_idx]
        for loop_offset, loop_idx in enumerate(poly.loop_indices):
            uv_slot = uv_tri[loop_offset % 3]
            uv = uv_layer.data[loop_idx].uv
            s = uv.x
            t = 1.0 - uv.y  # undo Blender V-flip applied on import
            if is_float:
                qs, qt = s, t
            else:
                qs = round(s * divisor)
                qt = round(t * divisor)
            if output_slots[uv_slot] is None:
                output_slots[uv_slot] = (qs, qt)
            elif output_slots[uv_slot] != (qs, qt):
                conflicts.append(uv_slot)

    # Fall back to original data for any slot not touched by a loop
    if None in output_slots:
        orig_raw = base64.b64decode(json_channel["UVChannelData"])
        for slot_idx, val in enumerate(output_slots):
            if val is None:
                off = slot_idx * comp_count * comp_size
                os_ = struct.unpack_from('>f' if is_float else '>h', orig_raw, off)[0]
                ot_ = struct.unpack_from('>f' if is_float else '>h', orig_raw, off + comp_size)[0]
                output_slots[slot_idx] = (os_ / (1 if is_float else divisor),
                                          ot_ / (1 if is_float else divisor))

    # Encode the coord array in original slot order
    raw_bytes = bytearray()
    for (qs, qt) in output_slots:
        comps = [qs, qt] + [0.0] * (comp_count - 2)
        for val in comps:
            if is_float:
                raw_bytes += struct.pack('>f', float(val))
            else:
                raw_bytes += struct.pack('>h', max(-32768, min(32767, int(val))))

    return base64.b64encode(bytes(raw_bytes)).decode('ascii'), conflicts


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

    filename_ext = ".sluggie"
    filter_glob: StringProperty(default="*.sluggie", options={"HIDDEN"})

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

            # Re-encode UV channels from Blender UV layers
            for json_channel in target_submesh.get("UVChannels", []):
                result = encode_uv_channel_edited(obj, json_channel)
                ch_ind = json_channel.get("UVChannelIndex", 0)
                if result is None:
                    palette_name = json_channel.get("PaletteName", "")
                    layer_name = palette_name or f"uv{ch_ind}"
                    warnings.append(
                        f"{obj.name}: UV layer '{layer_name}' not found — UV channel {ch_ind} skipped."
                    )
                    continue
                uv_data_b64, conflicts = result
                for slot in set(conflicts):
                    warnings.append(
                        f"{obj.name}: UV ch {ch_ind} slot {slot} has conflicting values "
                        f"(UV seam was split) — first value used."
                    )
                json_channel["UVChannelDataEdited"] = uv_data_b64
                # UVFacesDataEdited is no longer written: the draw list indices
                # are unchanged so UVFacesData still applies after patching.

            written += 1

        for w in warnings:
            self.report({"WARNING"}, w)

        if written == 0:
            self.report({"ERROR"}, "No submeshes written. Check the warnings above.")
            return {"CANCELLED"}

        with open(self.filepath, 'w') as f:
            json.dump(data, f, indent=2)

        filename = os.path.basename(self.filepath)
        context.window_manager.clipboard = filename
        subprocess.run(['clip'], input=filename.encode('utf-16-le'), check=False)
        self.report({"INFO"},
            f"Wrote edited vertex data for {written} submesh(es) to {self.filepath}")
        return {"FINISHED"}


def menu_func_export(self, context):
    self.layout.operator(SLUGGIES_OT_export.bl_idname, text="Sluggers intermediate (.sluggie)")


def register():
    bpy.utils.register_class(SLUGGIES_OT_export)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.utils.unregister_class(SLUGGIES_OT_export)
