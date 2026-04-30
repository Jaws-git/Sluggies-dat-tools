from model0 import *
import os
import shutil
import json
import base64
import re
import struct
import sys

EXPORT_TEX = '--notex' not in sys.argv



def itb (val, n):
    return val.to_bytes(n, 'big')

def bti (b):
    return int.from_bytes(b, 'big')

outdir = "../2_Output_Models/"
if not os.path.exists(outdir):
    os.mkdir(outdir)

# An array of FILE_POINTER[]'s in the US dol
DIRS_START = 0x69C828
DIRS_END = 0x69CAD8
DIRS_LEN = (DIRS_END - DIRS_START) // 0x4
DIR_PTR_PTRS = range(DIRS_START, DIRS_END, 4)
DAT_FNAME_PTR = 0x8067f658

dol = open('../1_Input/main.dol', 'rb')
DIR_PTRS = []
for addr in DIR_PTR_PTRS:
    dol.seek(addr, 0)
    DIR_PTRS.append(bti(dol.read(4)) - 0x80003f00)

dirs = {}

# for x in DIR_PTRS:
#     print(hex(x))

for dir_ind in range(DIRS_LEN):
    dirs[dir_ind] = []
    file_ptr = DIR_PTRS[dir_ind]
    while file_ptr not in DIR_PTRS[:dir_ind] + DIR_PTRS[dir_ind + 1:]:
        # print(hex(file_ptr))
        dol.seek(file_ptr, 0)
        file_data = [bti(dol.read(4)) for _ in range(12)]
        if file_data[0] != DAT_FNAME_PTR:
            break
        offset_en = file_data[2]
        len_en = file_data[1]
        offset_sp = file_data[6]
        len_sp = file_data[5]
        offset_fr = file_data[10]
        len_fr = file_data[9]
        dirs[dir_ind].append({'en':[offset_en, len_en], 'sp':[offset_sp, len_sp], 'fr':[offset_fr, len_fr]})
        file_ptr += 12 * 4

# dirs = {2:dirs[2]}
# mario
# dirs = {0: [{'en': [0x4AA6C20, 0x69E60], 'sp': [0x4AA6C20, 0x69E60], 'fr': [0x4AA6C20, 0x69E60]}]}
# dirs = {18: dirs[18][:1]}
# dirs = {18: dirs[18][3:5]}
# dirs = {19: dirs[19][3:5]}
# mario stadium
# dirs = {7: dirs[7]}
# baby dk
# dirs = {83: dirs[83]}
# hammer bro
# dirs = {45: dirs[45]}
# funky
# dirs = {74: dirs[74]}
# dirs = {132: dirs[132]}

# for dir in list(dirs.keys()):
#     if dir < 18:
#         del dirs[dir]
#     else:
#         dirs[dir] = dirs[dir][3:5]

def compact_faces_json(obj, indent=2):
    """Serialize obj to indented JSON, then collapse face index triplets onto one line."""
    raw = json.dumps(obj, indent=indent)
    raw = re.sub(r'\[\s*(\d+),\s*(\d+),\s*(\d+)\s*\]', r'[\1, \2, \3]', raw, flags=re.DOTALL)
    return raw

def _vb_comp_size(quantize_info):
    fmt = quantize_info >> 4
    return 4 if fmt in [4, 7, 0xa] else 2

def _color_entry_size(quantize_info):
    """Return bytes per color entry based on the color quantize format nibble."""
    fmt = quantize_info >> 4
    return {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}.get(fmt, 2)

def extract_texture_descriptors(model):
    """Return a list of TEX descriptor dicts for a Model0 instance.

    Includes dimensions, format, palette info, and file offsets/lengths for
    every texture so patch_dat.py can validate buffer sizes before writing.
    """
    if not hasattr(model, 'TEXPalette') or not model.TEXPalette:
        return []
    palette = model.TEXPalette
    result = []
    for tex_ind, desc in enumerate(palette.descriptors):
        img_offset = palette.absolute + desc.dataPtr
        img_length = palette.dataLens.get(desc.dataPtr, 0)
        entry = {
            "TextureIndex": tex_ind,
            "TextureDescriptorOffset": hex(desc.absolute),
            "Width": desc.width,
            "Height": desc.height,
            "Format": desc.format,
            "PaletteEntries": desc.paletteEntries,
            "PaletteFormat": desc.paletteFormat,
            "EdgeLODEnable": bool(desc.edgeLODEnable),
            "MinLOD": desc.minLOD,
            "MaxLOD": desc.maxLOD,
            "ImageDataOffset": hex(img_offset),
            "ImageDataLength": img_length
        }
        if desc.paletteDataPtr:
            pal_offset = palette.absolute + desc.paletteDataPtr
            entry["PaletteDataOffset"] = hex(pal_offset)
            entry["PaletteDataLength"] = palette.dataLens.get(desc.paletteDataPtr, 0)
        result.append(entry)
    return result

def extract_submeshes(model):
    """Return a list of submesh dicts with VertexBuffer, UV channel, and color channel info for a Model0 instance."""
    if not hasattr(model, 'GPL') or not model.GPL:
        return []
    submeshes = []
    for descriptor in model.GPL.geoDescriptors:
        layout = descriptor.layout
        pos = layout.DOPositionHeader
        vb_offset = layout.absolute + pos.positionArrPtr
        vb_length = pos.numPositions * pos.compCount * _vb_comp_size(pos.quantizeInfo)
        model.f.seek(vb_offset)
        vb_data = base64.b64encode(model.f.read(vb_length)).decode('ascii')
        num_uv_channels = len(layout.DOTextureDataHeaders)
        faces_raw = []
        face_tex_indices = []  # per-face TextureIndex active on UV channel 0 (primary)
        uv_faces_raw = [[] for _ in range(num_uv_channels)]
        color_faces_raw = {0: [], 1: []}
        color_active = {0: False, 1: False}
        tex_assignments = {}  # ch_ind -> {'index', 'wraps', 'wrapt'} from last Type-1 draw state
        draw_states_gfx = layout.getTriangles()
        for draw_state in draw_states_gfx:
            state = draw_state['state']
            active_descriptors = [d['key'] for d in state['descriptors']]
            # Record the last-seen texture assignment for each UV channel (Type-1 draw state)
            for ch_ind in range(num_uv_channels):
                key = 'texture' + str(ch_ind)
                if key in state and isinstance(state[key], dict) and 'index' in state[key]:
                    tex_assignments[ch_ind] = state[key]
            # Primary texture index for this draw batch (UV channel 0 / texture0)
            primary_assign = tex_assignments.get(0)
            current_primary_tex = primary_assign.get('index', 0) if primary_assign else 0
            has_color0 = 'color0' in active_descriptors
            has_color1 = 'color1' in active_descriptors
            if has_color0:
                color_active[0] = True
            if has_color1:
                color_active[1] = True
            for triangle in draw_state['triangles']:
                faces_raw.append([vertex['position'] for vertex in triangle])
                face_tex_indices.append(current_primary_tex)
                for ch_ind in range(num_uv_channels):
                    key = 'texture' + str(ch_ind)
                    if key in active_descriptors:
                        uv_faces_raw[ch_ind].append([vertex[key] for vertex in triangle])
                    else:
                        uv_faces_raw[ch_ind].append([0, 0, 0])
                color_faces_raw[0].append(
                    [vertex['color0'] for vertex in triangle] if has_color0 else [0, 0, 0]
                )
                color_faces_raw[1].append(
                    [vertex['color1'] for vertex in triangle] if has_color1 else [0, 0, 0]
                )
        face_count = len(faces_raw)
        # Pack position faces as big-endian uint16 triplets and base64-encode
        flat = [idx for tri in faces_raw for idx in tri]
        faces_data = base64.b64encode(struct.pack(f'>{len(flat)}H', *flat)).decode('ascii')
        # Per-face primary texture index (uint16, one per face)
        face_tex_data = base64.b64encode(struct.pack(f'>{len(face_tex_indices)}H', *face_tex_indices)).decode('ascii')
        # Extract raw UV buffers, per-face UV indices, and texture assignment per channel
        uv_channels = []
        for ch_ind, tex_layer in enumerate(layout.DOTextureDataHeaders):
            uv_offset = layout.absolute + tex_layer.textureCoordsArrPtr
            uv_length = tex_layer.numTextureCoords * tex_layer.compCount * _vb_comp_size(tex_layer.quantizeInfo)
            model.f.seek(uv_offset)
            uv_raw = base64.b64encode(model.f.read(uv_length)).decode('ascii')
            uv_flat = [idx for tri in uv_faces_raw[ch_ind] for idx in tri]
            uv_faces_data = base64.b64encode(struct.pack(f'>{len(uv_flat)}H', *uv_flat)).decode('ascii')
            assignment = tex_assignments.get(ch_ind, {})
            uv_channels.append({
                "UVChannelIndex": ch_ind,
                "PaletteName": tex_layer.paletteName,
                "TextureIndex": assignment.get('index'),
                "WrapS": assignment.get('wraps'),
                "WrapT": assignment.get('wrapt'),
                "UVDataPtrFieldOffset": hex(tex_layer.absolute),
                "UVCountFieldOffset": hex(tex_layer.absolute + 4),
                "UVChannelOffset": hex(uv_offset),
                "UVChannelLength": uv_length,
                "UVChannelCompCount": tex_layer.compCount,
                "UVChannelQuantizeInfo": tex_layer.quantizeInfo,
                "UVFacesData": uv_faces_data,
                "UVChannelData": uv_raw
            })
        # Extract color buffer and per-face color indices for each active channel
        color_channels = []
        color_hdr = layout.DOColorHeader
        if color_hdr.colorArrPtr and (color_active[0] or color_active[1]):
            color_offset = layout.absolute + color_hdr.colorArrPtr
            color_length = color_hdr.numColors * _color_entry_size(color_hdr.quantizeInfo)
            model.f.seek(color_offset)
            color_raw = base64.b64encode(model.f.read(color_length)).decode('ascii')
            for ch_idx in [0, 1]:
                if not color_active[ch_idx]:
                    continue
                ch_flat = [idx for tri in color_faces_raw[ch_idx] for idx in tri]
                ch_faces_data = base64.b64encode(struct.pack(f'>{len(ch_flat)}H', *ch_flat)).decode('ascii')
                color_channels.append({
                    "ColorChannelIndex": ch_idx,
                    "ColorChannelOffset": hex(color_offset),
                    "ColorChannelLength": color_length,
                    "ColorChannelCompCount": color_hdr.compCount,
                    "ColorChannelQuantizeInfo": color_hdr.quantizeInfo,
                    "ColorChannelData": color_raw,
                    "ColorFacesData": ch_faces_data
                })
        # Build structural draw-state info for Phase 3b pointer patching.
        # DODisplayState layout: [id:1][pad:3][setting:4][primitiveListPtr:4][primitiveListSize:4]
        # primitiveListPtr is relative to DOLayout.absolute (= SubmeshOffset).
        draw_states_export = []
        for ds_obj, draw_state in zip(layout.DODisplayHeader.displayStates, draw_states_gfx):
            raw_prim = bytes(ds_obj.primitiveList.data)
            draw_states_export.append({
                "DisplayStateId": ds_obj.id,
                "PrimListPtrFieldOffset": hex(ds_obj.absolute + 8),
                "PrimListSizeFieldOffset": hex(ds_obj.absolute + 12),
                "PrimListAbsoluteOffset": hex(layout.absolute + ds_obj.primitiveListPtr),
                "PrimListLength": ds_obj.primitiveListSize,
                "PrimListData": base64.b64encode(raw_prim).decode('ascii'),
                "ActiveDescriptors": [
                    {"key": d['key'], "index_size": d['index_size']}
                    for d in draw_state['state']['descriptors']
                ]
            })
        submeshes.append({
            "SubmeshOffset": hex(layout.absolute),
            "PositionDataPtrFieldOffset": hex(pos.absolute),
            "VertexCountFieldOffset": hex(pos.absolute + 4),
            "FacesCount": face_count,
            "FacesData": faces_data,
            "FaceTextureIndices": face_tex_data,
            "DrawStates": draw_states_export,
            "VertexBuffer": {
                "VertexBufferOffset": hex(vb_offset),
                "VertexBufferLength": vb_length,
                "VertexBufferCompCount": pos.compCount,
                "VertexBufferQuantizeInfo": pos.quantizeInfo,
                "VertexBufferData": vb_data
            },
            "UVChannels": uv_channels,
            "ColorChannels": color_channels
        })
    return submeshes

class Dat(File):
    def __init__(self, f):
        super().__init__(f)

dat = Dat(open('../1_Input/dt_na.dat', 'rb'))

for dir_ind, file_arr in dirs.items():
    dir_dir = outdir + str(dir_ind) + '/'
    if not os.path.exists(dir_dir):
        os.mkdir(dir_dir)
    for file in file_arr:
        languages = ['en']
        # if file['en'][0] != file['sp'][0]:
        #     languages = ['en', 'sp', 'fr']
        try:
            for lan in languages:
                offset = file[lan][0]
                # print(hex(offset))
                l = file[lan][1]
                lan_dir = dir_dir
                if len(languages) > 1:
                    lan_dir += lan + '/'
                    if not os.path.exists(lan_dir):
                        os.mkdir(lan_dir)
                child = dat.add_child(offset, l, MaybeArchive)
                child.analyze()
                if child.child:
                    child.child.analyze()
                    child.child.toFile(lan_dir, export_tex=EXPORT_TEX)
                    if isinstance(child.child, Archive):
                        archive_dir = os.path.join(lan_dir, str(child.child.absolute))
                        for i in child.child.success:
                            sub_model = child.child.files[i]
                            sub_dir = os.path.join(archive_dir, sub_model.name)
                            json_name = f"{sub_model.name}.sluggie"
                            model_json = {
                                "SluggiesModel": {
                                    "ChunkNumber": dir_ind,
                                    "ModelOffset": hex(sub_model.absolute),
                                    "ModelLength": sub_model.length,
                                    "TextureDescriptors": extract_texture_descriptors(sub_model),
                                    "Submeshes": extract_submeshes(sub_model)
                                }
                            }
                            with open(os.path.join(sub_dir, json_name), 'w') as info_f:
                                info_f.write(compact_faces_json(model_json))
                    else:
                        model_name = child.child.name
                        model_dir = os.path.join(lan_dir, model_name)
                        json_name = f"{model_name}.sluggie"
                        model_json = {
                            "SluggiesModel": {
                                "ChunkNumber": dir_ind,
                                "ModelOffset": hex(offset),
                                "ModelLength": l,
                                "TextureDescriptors": extract_texture_descriptors(child.child),
                                "Submeshes": extract_submeshes(child.child)
                            }
                        }
                        with open(os.path.join(model_dir, json_name), 'w') as info_f:
                            info_f.write(compact_faces_json(model_json))
                del child
        except Exception as e:
            print ("failed in export")
            print (e)
            pass
    if len(os.listdir(dir_dir)) == 0:
        os.rmdir(dir_dir)
    print ("Analyzed dir " + str(dir_ind))