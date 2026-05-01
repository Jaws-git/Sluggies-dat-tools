import bpy
import json
import os
import base64
import struct
import subprocess
from bpy.props import BoolProperty, StringProperty
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


def encode_mesh_hammerspace(obj, json_submesh):
    """Encode all mesh data for hammerspace export (vertex count may differ from original).

    Returns a dict with:
      'VertexBufferDataEdited': base64 string  — full re-quantized vertex buffer
      'FacesDataEdited':        base64 string  — uint16 BE triangulated face indices
      'FacesCountEdited':       int            — triangle count
      'UVEdits': {ch_ind: (uv_data_b64, uv_faces_b64), ...}
    """
    mesh = obj.data
    mesh.calc_loop_triangles()
    triangles = mesh.loop_triangles

    vb_data = encode_vertex_buffer_edited(
        obj,
        obj["VertexBufferCompCount"],
        obj["VertexBufferQuantizeInfo"],
    )

    face_flat = [vi for tri in triangles for vi in tri.vertices]
    faces_data = base64.b64encode(struct.pack(f'>{len(face_flat)}H', *face_flat)).decode('ascii')

    uv_edits = {}
    for json_channel in json_submesh.get('UVChannels', []):
        ch_ind = json_channel.get('UVChannelIndex', 0)
        palette_name = json_channel.get('PaletteName', '')
        layer_name = palette_name or f'uv{ch_ind}'
        uv_layer = mesh.uv_layers.get(layer_name)
        if uv_layer is None:
            continue

        quant_info_uv = json_channel['UVChannelQuantizeInfo']
        comp_count_uv = json_channel['UVChannelCompCount']
        fmt_nibble = quant_info_uv >> 4
        shift = quant_info_uv & 0xF
        divisor = 1 << shift
        is_float = fmt_nibble in [4, 7, 0xa]

        coords = []
        coord_map = {}
        uv_tri_indices = []
        for tri in triangles:
            tri_uvs = []
            for loop_idx in tri.loops:
                uv = uv_layer.data[loop_idx].uv
                s = uv.x
                t = 1.0 - uv.y  # undo Blender V-flip applied on import
                if is_float:
                    key = (float(s), float(t))
                    qs, qt = float(s), float(t)
                else:
                    qs = round(s * divisor)
                    qt = round(t * divisor)
                    key = (int(qs), int(qt))
                if key not in coord_map:
                    coord_map[key] = len(coords)
                    coords.append((qs, qt))
                tri_uvs.append(coord_map[key])
            uv_tri_indices.append(tri_uvs)

        raw_bytes = bytearray()
        for (qs, qt) in coords:
            for val in [qs, qt] + [0.0] * (comp_count_uv - 2):
                if is_float:
                    raw_bytes += struct.pack('>f', float(val))
                else:
                    raw_bytes += struct.pack('>h', max(-32768, min(32767, int(val))))
        uv_data_b64 = base64.b64encode(bytes(raw_bytes)).decode('ascii')

        uv_flat = [idx for tri in uv_tri_indices for idx in tri]
        uv_faces_b64 = base64.b64encode(struct.pack(f'>{len(uv_flat)}H', *uv_flat)).decode('ascii')

        uv_edits[ch_ind] = (uv_data_b64, uv_faces_b64)

    return {
        'VertexBufferDataEdited': vb_data,
        'FacesDataEdited': faces_data,
        'FacesCountEdited': len(triangles),
        'UVEdits': uv_edits,
    }


def _comp_size_skin(quant_info):
    fmt = quant_info >> 4
    return 4 if fmt in [4, 7, 0xa] else 2


def _get_vgroup_weight(obj, group_name, vertex_index):
    """Return 0-1 weight for vertex_index in group_name, or 0.0 if absent."""
    vg = obj.vertex_groups.get(group_name)
    if vg is None:
        return 0.0
    try:
        return vg.weight(vertex_index)
    except RuntimeError:
        return 0.0


def encode_skin_weights_inplace(candidates, data, warnings):
    """Re-pack SK2/SKAcc weight bytes from Blender vertex groups (in-place mode).

    Vertex count and bone structure must be unchanged — only weight values are
    updated.  Writes 'WeightDataEdited' into each SK2/SKAcc entry inside the
    existing SkinData dict.  Returns True if any entries were written.
    """
    skin_data = data["SluggiesModel"].get("SkinData")
    if not skin_data or not (skin_data.get("SK2s") or skin_data.get("SKAccs")):
        return False
    gpl_base_hex = skin_data.get("GplBaseOffset")
    if not gpl_base_hex:
        warnings.append("SkinData missing GplBaseOffset — skin weight re-encode skipped. Re-export the .sluggie from export.py.")
        return False

    quant_info  = skin_data["QuantizeInfo"]
    vertex_size = 6 * _comp_size_skin(quant_info)
    gpl_base    = int(gpl_base_hex, 16)
    submeshes   = data["SluggiesModel"].get("Submeshes", [])

    # Build per-submesh (vtx_start, vtx_count) in GPL-relative vertex units
    sub_ranges = []
    for sm in submeshes:
        vb = sm["VertexBuffer"]
        vb_abs   = int(vb["VertexBufferOffset"], 16)
        vtx_start = (vb_abs - gpl_base) // vertex_size
        vb_cs    = _comp_size_skin(vb["VertexBufferQuantizeInfo"])
        vtx_count = vb["VertexBufferLength"] // (vb["VertexBufferCompCount"] * vb_cs)
        sub_ranges.append((vtx_start, vtx_count, vb["VertexBufferOffset"]))

    # Map VertexBufferOffset → object for quick lookup
    obj_by_vb = {str(obj["VertexBufferOffset"]): obj for obj in candidates if "VertexBufferOffset" in obj}

    def resolve(global_vtx):
        for j, (start, count, vb_off) in enumerate(sub_ranges):
            if start <= global_vtx < start + count:
                return obj_by_vb.get(str(vb_off)), global_vtx - start
        return None, global_vtx

    wrote_any = False

    for sk2 in skin_data.get("SK2s", []):
        n            = sk2["VertexCnt"]
        vtx_off      = sk2.get("VertexOffset", 0)
        global_start = (sk2["GplVertexArrValue"] + vtx_off) // vertex_size
        bone1        = sk2["BoneIndex1"]
        bone2        = sk2["BoneIndex2"]
        orig         = base64.b64decode(sk2["WeightData"])

        wt = bytearray()
        for i in range(n):
            obj, local_v = resolve(global_start + i)
            if obj is None:
                wt.append(orig[i * 2])
                wt.append(orig[i * 2 + 1])
            else:
                w1 = _get_vgroup_weight(obj, f"bone_{bone1}", local_v)
                w2 = _get_vgroup_weight(obj, f"bone_{bone2}", local_v)
                wt.append(max(0, min(255, round(w1 * 256))))
                wt.append(max(0, min(255, round(w2 * 256))))
        sk2["WeightDataEdited"] = base64.b64encode(bytes(wt)).decode('ascii')
        wrote_any = True

    for skacc in skin_data.get("SKAccs", []):
        n         = skacc["VertexCnt"]
        bone_id   = skacc["BoneIndex"]
        dest_base = skacc["GplDestArrValue"] // vertex_size
        orig      = base64.b64decode(skacc["WeightData"])
        dest_idxs = list(struct.unpack(f'>{n}H', base64.b64decode(skacc["DestIndexData"])))

        wt = bytearray()
        for i in range(n):
            obj, local_v = resolve(dest_base + dest_idxs[i])
            if obj is None:
                wt.append(orig[i])
            else:
                w = _get_vgroup_weight(obj, f"bone_{bone_id}", local_v)
                wt.append(max(0, min(255, round(w * 256))))
        skacc["WeightDataEdited"] = base64.b64encode(bytes(wt)).decode('ascii')
        wrote_any = True

    return wrote_any


def encode_skin_hammerspace(candidates, data, warnings):
    """Rebuild SK1/SK2/SKAcc from Blender vertex groups for hammerspace export.

    Splitting rules:
      1 influence  → SK1
      2 influences → SK2
      3+ influences → SK2 (top 2 by weight) + SKAcc (remainder)

    Bone pairs in SK2 are stored with the lower BoneId first.
    Source data (bind-pose XYZ + NxNyNz) is encoded with SkinData.QuantizeInfo.
    Writes 'SkinDataEdited' at the model level.  Returns True if written.
    """
    skin_data = data["SluggiesModel"].get("SkinData")
    if not skin_data:
        return False

    submeshes  = data["SluggiesModel"].get("Submeshes", [])
    quant_info = skin_data["QuantizeInfo"]
    cs         = _comp_size_skin(quant_info)
    fmt_nibble = quant_info >> 4
    is_float   = fmt_nibble in [4, 7, 0xa]
    divisor    = 1 << (quant_info & 0xF)

    def pack_val(v):
        if is_float:
            return struct.pack('>f', float(v))
        return struct.pack('>h', max(-32768, min(32767, round(float(v) * divisor))))

    # Map VertexBufferOffset → (submesh_idx, obj)
    obj_to_sub = {}
    for j, sm in enumerate(submeshes):
        vb_off = str(sm["VertexBuffer"]["VertexBufferOffset"])
        for obj in candidates:
            if "VertexBufferOffset" in obj and str(obj["VertexBufferOffset"]) == vb_off:
                obj_to_sub[id(obj)] = (j, obj)
                break

    sk1_groups  = {}   # bone_id → [(sub_idx, local_v, obj)]
    sk2_groups  = {}   # (b_lo, b_hi) → [(sub_idx, local_v, w_lo, w_hi, obj)]
    skacc_groups = {}  # bone_id → [(sub_idx, local_v, weight, dest_local_v, obj)]

    for _, (sub_idx, obj) in obj_to_sub.items():
        for v in obj.data.vertices:
            parsed = []
            for vge in v.groups:
                vg = obj.vertex_groups[vge.group]
                if not vg.name.startswith("bone_") or vge.weight <= 0.0:
                    continue
                try:
                    parsed.append((int(vg.name[5:]), vge.weight))
                except ValueError:
                    continue
            parsed.sort(key=lambda x: -x[1])
            if not parsed:
                continue

            v_idx = v.index
            if len(parsed) == 1:
                b, _ = parsed[0]
                sk1_groups.setdefault(b, []).append((sub_idx, v_idx, obj))
            else:
                # SK2 from top 2, canonically ordered by bone id
                (b1, w1), (b2, w2) = parsed[0], parsed[1]
                b_lo, b_hi = (b1, b2) if b1 <= b2 else (b2, b1)
                w_lo = w1 if b1 <= b2 else w2
                w_hi = w2 if b1 <= b2 else w1
                sk2_groups.setdefault((b_lo, b_hi), []).append(
                    (sub_idx, v_idx, w_lo, w_hi, obj))
                # SKAcc for any remaining influences
                for b, w in parsed[2:]:
                    skacc_groups.setdefault(b, []).append(
                        (sub_idx, v_idx, w, v_idx, obj))

    def encode_src(entries):
        raw = bytearray()
        for entry in entries:
            obj   = entry[-1]
            v     = obj.data.vertices[entry[1]]
            for val in [v.co.x, v.co.y, v.co.z, v.normal.x, v.normal.y, v.normal.z]:
                raw += pack_val(val)
        return base64.b64encode(bytes(raw)).decode('ascii')

    new_sk1s = [
        {"BoneIndex": b, "VertexCnt": len(e), "SourceData": encode_src(e)}
        for b, e in sorted(sk1_groups.items())
    ]

    new_sk2s = []
    for (b_lo, b_hi), entries in sorted(sk2_groups.items()):
        wt = bytearray()
        for e in entries:
            wt.append(max(0, min(255, round(e[2] * 256))))
            wt.append(max(0, min(255, round(e[3] * 256))))
        new_sk2s.append({
            "BoneIndex1": b_lo, "BoneIndex2": b_hi,
            "VertexCnt": len(entries),
            "SourceData": encode_src(entries),
            "WeightData": base64.b64encode(bytes(wt)).decode('ascii'),
        })

    new_skaccs = []
    for bone_id, entries in sorted(skacc_groups.items()):
        wt   = bytearray()
        dest = bytearray()
        for e in entries:
            wt   += bytes([max(0, min(255, round(e[2] * 256)))])
            dest += struct.pack('>H', e[3])
        new_skaccs.append({
            "BoneIndex": bone_id, "VertexCnt": len(entries),
            "SourceData":    encode_src(entries),
            "WeightData":    base64.b64encode(bytes(wt)).decode('ascii'),
            "DestIndexData": base64.b64encode(bytes(dest)).decode('ascii'),
        })

    data["SluggiesModel"]["SkinDataEdited"] = {
        "QuantizeInfo": quant_info,
        "SK1s":   new_sk1s,
        "SK2s":   new_sk2s,
        "SKAccs": new_skaccs,
    }
    return True


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
    use_hammerspace: BoolProperty(
        name="Hammerspace Mode",
        description=(
            "Allow vertex count changes. Encodes new face indices and dense UV "
            "coords for Phase 3 writeExpandedMesh() pointer patching. "
            "Leave off for simple in-place edits that preserve vertex count."
        ),
        default=False,
    )

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

            if self.use_hammerspace:
                hs = encode_mesh_hammerspace(obj, target_submesh)
                target_submesh["VertexBufferEdited"] = {
                    "VertexBufferDataEdited": hs['VertexBufferDataEdited']
                }
                target_submesh["FacesDataEdited"] = hs['FacesDataEdited']
                target_submesh["FacesCountEdited"] = hs['FacesCountEdited']
                for json_channel in target_submesh.get("UVChannels", []):
                    ch_ind = json_channel.get("UVChannelIndex", 0)
                    if ch_ind in hs['UVEdits']:
                        uv_data_b64, uv_faces_b64 = hs['UVEdits'][ch_ind]
                        json_channel["UVChannelDataEdited"] = uv_data_b64
                        json_channel["UVFacesDataEdited"] = uv_faces_b64
                    else:
                        palette_name = json_channel.get("PaletteName", "")
                        layer_name = palette_name or f"uv{ch_ind}"
                        warnings.append(
                            f"{obj.name}: UV layer '{layer_name}' not found — UV channel {ch_ind} skipped."
                        )
            else:
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

        # Skin data is model-level — encode once after all submeshes are processed
        if self.use_hammerspace:
            encode_skin_hammerspace(candidates, data, warnings)
        else:
            encode_skin_weights_inplace(candidates, data, warnings)

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
