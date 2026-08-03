from model0 import *
import os
import shutil
import json
import base64
import re
import struct
import sys

_HS_DIR = os.path.join(os.path.dirname(__file__), 'Hammerspace')
if _HS_DIR not in sys.path:
    sys.path.insert(0, _HS_DIR)
try:
    import HammerspaceHelper as hh
except Exception:
    hh = None

EXPORT_TEX = '--notex' not in sys.argv
DEBUG_DONT_USE_BASE64 = '--debug' in sys.argv
UNTANGLE_TEX = '--untangle' in sys.argv
EXPORT_DAE = '--dae' in sys.argv


def _encode_bytes(data: bytes):
    """Encode binary data as a base64 string, or a list of byte ints when DEBUG_DONT_USE_BASE64."""
    if DEBUG_DONT_USE_BASE64:
        return list(data)
    return base64.b64encode(data).decode('ascii')


def itb (val, n):
    return val.to_bytes(n, 'big')

def bti (b):
    return int.from_bytes(b, 'big')

outdir = "../2_Output_Models/"
if not os.path.exists(outdir):
    os.mkdir(outdir)

folderNameMap = {
    "1": "test_actor",
    "2": "daisy_prototype",
    "5": "stadium_prototypes",
    "7": "Mario Stadium",
    "8": "Bowser Castle",
    "9": "Wario City",
    "10": "Yoshi Park",
    "11": "Peach Ice Garden",
    "12": "DK Jungle",
    "13": "Luigis Mansion",
    "14": "Daisy Cruiser",
    "15": "Bowser Jr Playroom",
    "16": "Toy Field",
    "18": "Mario",
    "19": "Luigi",
    "20": "Donkey Kong",
    "21": "Diddy Kong",
    "22": "Peach",
    "23": "Daisy",
    "24": "Yoshi",
    "25": "Baby Mario",
    "26": "Baby Luigi",
    "27": "Bowser",
    "28": "Wario",
    "29": "Waluigi",
    "30": "Koopa",
    "31": "Toad",
    "32": "Boo",
    "33": "Toadette",
    "34": "Shy Guy",
    "35": "Birdo",
    "36": "Monty Mole",
    "37": "Bowser Jr",
    "38": "Paratroopa",
    "39": "Pianta",
    "40": "Red Pianta",
    "41": "Yellow Pianta",
    "42": "Noki",
    "43": "Red Noki",
    "44": "Green Noki",
    "45": "Hammer Bro",
    "46": "Toadsworth",
    "47": "Blue Toad",
    "48": "Yellow Toad",
    "49": "Green Toad",
    "50": "Purple Toad",
    "51": "Magikoopa",
    "52": "Red Magikoopa",
    "53": "Green Magikoopa",
    "54": "Yellow Magikoopa",
    "55": "King Boo",
    "56": "Petey Piranha",
    "57": "Dixie Kong",
    "58": "Goomba",
    "59": "Paragoomba",
    "60": "Red Koopa",
    "61": "Green Paratroopa",
    "62": "Blue Shy Guy",
    "63": "Yellow Shy Guy",
    "64": "Green Shy Guy",
    "65": "Gray Shy Guy",
    "66": "Dry Bones",
    "67": "Green Dry Bones",
    "68": "Dark Bones",
    "69": "Blue Dry Bones",
    "70": "Fire Bro",
    "71": "Boomerang Bro",
    "72": "Wiggler",
    "73": "Blooper",
    "74": "Funky Kong",
    "75": "Tiny Kong",
    "76": "Kritter",
    "77": "Blue Kritter",
    "78": "Red Kritter",
    "79": "Brown Kritter",
    "80": "King K Rool",
    "81": "Baby Peach",
    "82": "Baby Daisy",
    "83": "Baby DK",
    "84": "Red Yoshi",
    "85": "Blue Yoshi",
    "86": "Yellow Yoshi",
    "87": "Light-Blue Yoshi",
    "88": "Pink Yoshi",
    "89": "Unused Yoshi A",
    "90": "Unused Yoshi B",
    "91": "Unused Toad",
    "92": "Unused Pianta",
    "93": "Unused Kritter",
    "94": "Unused Koopa",
    "95": "Red Male Mii",
    "96": "Orange Male Mii",
    "97": "Yellow Male Mii",
    "98": "Light-Green Male Mii",
    "99": "Green Male Mii",
    "100": "Blue Male Mii",
    "101": "Light-Blue Male Mii",
    "102": "Pink Male Mii",
    "103": "Purple Male Mii",
    "104": "Brown Male Mii",
    "105": "White Male Mii",
    "106": "Black Male Mii",
    "107": "Red Male Mii",
    "108": "Orange Male Mii",
    "109": "Yellow Male Mii",
    "110": "Light-Green Male Mii",
    "111": "Green Male Mii",
    "112": "Blue Male Mii",
    "113": "Light-Blue Male Mii",
    "114": "Pink Male Mii",
    "115": "Purple Male Mii",
    "116": "Brown Male Mii",
    "117": "White Male Mii",
    "118": "Black Male Mii",
    "122": "Stadium Select",
    "125": "Water Waves",
    "126": "Map Objects A",
    "127": "Map Objects B",
    "128": "Map Objects C",
    "129": "Map Objects D",
    "130": "Map Objects E",
    "131": "Map Objects F",
    "133": "Statues and Props",
    "136": "Scoreboards Items and Obstacles",
    "137": "Various A",
    "142": "Various B",
    "159": "Effects"
}


def top_level_folder_name(dir_ind):
    name_suffix = folderNameMap.get(str(dir_ind))
    if name_suffix:
        return f'{dir_ind} {name_suffix}'
    return str(dir_ind)


# An array of FILE_POINTER[]'s in the US dol
DIRS_START = 0x69C828
DIRS_END = 0x69CAD8
DIRS_LEN = (DIRS_END - DIRS_START) // 0x4
DIR_PTR_PTRS = range(DIRS_START, DIRS_END, 4)
DAT_FNAME_PTR = 0x8067f658
UNUSED_DIRS_TO_UNTANGLE_CLONE = [89, 90, 91, 92, 93, 94]


def load_dol_dirs(dol_path):
    """Parse model directory entries from a main.dol file."""
    with open(dol_path, 'rb') as dol:
        dir_ptrs = []
        for addr in DIR_PTR_PTRS:
            dol.seek(addr, 0)
            dir_ptrs.append(bti(dol.read(4)) - 0x80003f00)

        dirs = {}
        for dir_ind in range(DIRS_LEN):
            dirs[dir_ind] = []
            file_ptr = dir_ptrs[dir_ind]
            while file_ptr not in dir_ptrs[:dir_ind] + dir_ptrs[dir_ind + 1:]:
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
                dirs[dir_ind].append({'en': [offset_en, len_en], 'sp': [offset_sp, len_sp], 'fr': [offset_fr, len_fr]})
                file_ptr += 12 * 4

    return dirs


def clone_unused_dirs_to_hammerspace_for_untangle(input_dol_path, input_dat_path, report_lines=None):
    """Clone all model entries in dirs 89-94 into hammerspace and repoint output main.dol.

    This decouples unused character directories from shared offsets before
    texture untangling so each referenced model block can be modified independently.
    """
    if hh is None:
        raise RuntimeError('HammerspaceHelper import failed; cannot perform untangle pre-clone step.')

    source_dirs = load_dol_dirs(input_dol_path)

    required_growth = 0
    for dir_ind in UNUSED_DIRS_TO_UNTANGLE_CLONE:
        for file in source_dirs.get(dir_ind, []):
            length = file['en'][1]
            if length > 0:
                required_growth += length + hh.HS_BUFFER_BYTES

    base_size = os.path.getsize(hh.OUTPUT_DAT) if os.path.exists(hh.OUTPUT_DAT) else hh.BASE_SIZE
    reserve = 1024 * 1024
    required_total_size = max(base_size, hh.BASE_SIZE) + required_growth + reserve
    if not hh.ensureOutputDat(required_total_size):
        raise RuntimeError('Unable to prepare output dt_na.dat for untangle clone step.')
    hh.patchFstFileSize(os.path.getsize(hh.OUTPUT_DAT))

    clone_count = 0
    with open(input_dat_path, 'rb') as input_dat:
        for dir_ind in UNUSED_DIRS_TO_UNTANGLE_CLONE:
            for file_index, file in enumerate(source_dirs.get(dir_ind, [])):
                src_offset = file['en'][0]
                src_length = file['en'][1]
                if src_length <= 0:
                    continue

                input_dat.seek(src_offset, 0)
                block = input_dat.read(src_length)
                if len(block) != src_length:
                    raise RuntimeError(
                        f'Failed reading source model block for dir {dir_ind} file {file_index}: '
                        f'expected {src_length} bytes, got {len(block)}'
                    )

                new_offset = hh.findFreeMemoryChunk(src_length)
                if new_offset == -1:
                    grow_by = max(src_length + hh.HS_BUFFER_BYTES, 64 * 1024 * 1024)
                    next_size = os.path.getsize(hh.OUTPUT_DAT) + grow_by
                    if not hh.ensureOutputDat(next_size):
                        raise RuntimeError('Unable to expand output dt_na.dat for untangle clone step.')
                    hh.patchFstFileSize(os.path.getsize(hh.OUTPUT_DAT))
                    new_offset = hh.findFreeMemoryChunk(src_length)
                    if new_offset == -1:
                        raise RuntimeError(
                            f'Unable to find free hammerspace chunk for dir {dir_ind} file {file_index} '
                            f'({src_length} bytes).'
                        )

                hh.writeModelBlock(block, new_offset)
                hh.patchDolEntry(dir_ind, file_index, new_offset, src_length)
                clone_count += 1

                if report_lines is not None:
                    report_lines.append(
                        f'Hammerspace clone: dir {dir_ind} file {file_index} '
                        f'0x{src_offset:x} -> 0x{new_offset:x} ({src_length} bytes)'
                    )

    return clone_count

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
    """Serialize obj to indented JSON, then collapse short numeric arrays and common repeating elements onto one line each."""
    _n = r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?'
    raw = json.dumps(obj, indent=indent)
    # Collapse 4-element numeric arrays (e.g. Quaternion) — must run before 3-element
    raw = re.sub(rf'\[\s*({_n}),\s*({_n}),\s*({_n}),\s*({_n})\s*\]',
                 r'[\1, \2, \3, \4]', raw, flags=re.DOTALL)
    # Collapse 3-element numeric arrays (e.g. Translation, Scale, HeadPosition, face indices)
    raw = re.sub(rf'\[\s*({_n}),\s*({_n}),\s*({_n})\s*\]',
                 r'[\1, \2, \3]', raw, flags=re.DOTALL)
    # Collapse VertexStreamLayout descriptor objects {"key": "...", "index_size": n} onto one line
    raw = re.sub(r'\{\s*"key":\s*("[\w]+")\s*,\s*"index_size":\s*(\d+)\s*\}',
                 r'{"key": \1, "index_size": \2}', raw, flags=re.DOTALL)
    return raw

def _vb_comp_size(quantize_info):
    fmt = quantize_info >> 4
    return 4 if fmt in [4, 7, 0xa] else 2

def _color_entry_size(quantize_info):
    """Return bytes per color entry based on the color quantize format nibble."""
    fmt = quantize_info >> 4
    return {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}.get(fmt, 2)


def write_texture_hash_overlaps_report(output_dir, untangle_report_lines=None, untangle_warnings=None):
    """Write repeated PNG filename counts under the export root.

    Any PNG basename encountered more than once anywhere below output_dir is
    reported once in texture_hash_overlaps.txt.
    """
    report_path = os.path.join(output_dir, 'texture_hash_overlaps.txt')
    png_counts = {}

    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            if not fname.lower().endswith('.png'):
                continue
            png_counts[fname] = png_counts.get(fname, 0) + 1

    duplicate_names = sorted(
        (name, count) for name, count in png_counts.items() if count > 1
    )

    with open(report_path, 'w', encoding='utf-8') as report_f:
        for name, count in duplicate_names:
            report_f.write(f'{name} (x{count} identical duplicates)\n')
        if untangle_report_lines:
            report_f.write('\n')
            report_f.write('Untangle updates:\n')
            for line in untangle_report_lines:
                report_f.write(line + '\n')
        if untangle_warnings:
            report_f.write('\n')
            report_f.write('Untangle warnings:\n')
            for line in untangle_warnings:
                report_f.write(line + '\n')

def extract_tex_header(model):
    """Return TEXPalette section header fields, or None if no TEX section."""
    if not hasattr(model, 'TEXPalette') or not model.TEXPalette:
        return None
    return {"CLUTCount": model.TEXPalette.numCLUTsMaybe}


def extract_texture_descriptors(model, untangle_context=None):
    """Return a list of TEX descriptor dicts for a Model0 instance.

    Includes dimensions, format, palette info, and file offsets/lengths for
    every texture so patch_inplace.py can validate buffer sizes before writing.
    """
    if not hasattr(model, 'TEXPalette') or not model.TEXPalette:
        return []
    palette = model.TEXPalette
    result = []
    model_name_overrides = {}
    if untangle_context and untangle_context.get('enabled'):
        model_name_overrides = untangle_context.get('name_overrides', {}).get(model.absolute, {})
    for tex_ind, desc in enumerate(palette.descriptors):
        img_offset = palette.absolute + desc.dataPtr
        img_length = palette.dataLens.get(desc.dataPtr, 0)
        # Preserve the two unknown byte regions that TEXDescriptor.analyze() discards.
        model.f.seek(desc.absolute + 0x10)
        unknown_10 = _encode_bytes(model.f.read(7))
        model.f.seek(desc.absolute + 0x1b)
        unknown_1b = _encode_bytes(model.f.read(5))
        entry = {
            "TextureIndex": tex_ind,
            "TextureFileName": model_name_overrides.get(tex_ind, desc.dolphinTextureBasename()) + '.png',
            "TextureDescriptorOffset": hex(desc.absolute),
            "Width": desc.width,
            "Height": desc.height,
            "Format": desc.format,
            "PaletteEntries": desc.paletteEntries,
            "PaletteFormat": desc.paletteFormat,
            "EdgeLODEnable": bool(desc.edgeLODEnable),
            "MinLOD": desc.minLOD,
            "MaxLOD": desc.maxLOD,
            "Unpacked": desc.unpacked,
            "DescUnknownAt10": unknown_10,
            "DescUnknownAt1B": unknown_1b,
            "ImageDataOffset": hex(img_offset),
            "ImageDataLength": img_length
        }
        if desc.paletteDataPtr:
            pal_offset = palette.absolute + desc.paletteDataPtr
            entry["PaletteDataOffset"] = hex(pal_offset)
            entry["PaletteDataLength"] = palette.dataLens.get(desc.paletteDataPtr, 0)
        result.append(entry)
    return result

def extract_gpl_userdata(model):
    """Return (encoded_bytes_or_None, length) for the GPL user-data block."""
    if not hasattr(model, 'GPL') or not model.GPL:
        return None, 0
    gpl = model.GPL
    if not gpl.userDataPtr or not gpl.userDataSize:
        return None, 0
    model.f.seek(gpl.absolute + gpl.userDataPtr)
    raw = model.f.read(gpl.userDataSize)
    return _encode_bytes(raw), gpl.userDataSize


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
        vb_data = _encode_bytes(model.f.read(vb_length))
        num_uv_channels = len(layout.DOTextureDataHeaders)
        faces_raw = []
        face_tex_indices = []  # per-face TextureIndex active on UV channel 0 (primary)
        uv_faces_raw = [[] for _ in range(num_uv_channels)]
        color_faces_raw = {0: [], 1: []}
        color_active = {0: False, 1: False}
        tex_assignments = {}  # ch_ind -> {'index', 'wraps', 'wrapt'} from last Type-1 draw state
        display_states_gfx = layout.getTriangles()
        for display_state in display_states_gfx:
            state = display_state['state']
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
            for triangle in display_state['triangles']:
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
        faces_data = _encode_bytes(struct.pack(f'>{len(flat)}H', *flat))
        # Per-face primary texture index (uint16, one per face)
        face_tex_data = _encode_bytes(struct.pack(f'>{len(face_tex_indices)}H', *face_tex_indices))
        # Extract raw UV buffers, per-face UV indices, and texture assignment per channel
        uv_channels = []
        for ch_ind, tex_layer in enumerate(layout.DOTextureDataHeaders):
            uv_offset = layout.absolute + tex_layer.textureCoordsArrPtr
            uv_length = tex_layer.numTextureCoords * tex_layer.compCount * _vb_comp_size(tex_layer.quantizeInfo)
            model.f.seek(uv_offset)
            uv_raw = _encode_bytes(model.f.read(uv_length))
            uv_flat = [idx for tri in uv_faces_raw[ch_ind] for idx in tri]
            uv_faces_data = _encode_bytes(struct.pack(f'>{len(uv_flat)}H', *uv_flat))
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
            color_raw = _encode_bytes(model.f.read(color_length))
            for ch_idx in [0, 1]:
                if not color_active[ch_idx]:
                    continue
                ch_flat = [idx for tri in color_faces_raw[ch_idx] for idx in tri]
                ch_faces_data = _encode_bytes(struct.pack(f'>{len(ch_flat)}H', *ch_flat))
                color_channels.append({
                    "ColorChannelIndex": ch_idx,
                    "ColorChannelOffset": hex(color_offset),
                    "ColorChannelLength": color_length,
                    "ColorChannelCompCount": color_hdr.compCount,
                    "ColorChannelQuantizeInfo": color_hdr.quantizeInfo,
                    "ColorChannelData": color_raw,
                    "ColorFacesData": ch_faces_data
                })
        # Build structural display-state info for pointer patching.
        # DODisplayState layout: [id:1][pad:3][setting:4][primitiveListPtr:4][primitiveListSize:4]
        # primitiveListPtr is relative to DOLayout.absolute (= SubmeshOffset).
        display_states_export = []
        for ds_obj, display_state in zip(layout.DODisplayHeader.displayStates, display_states_gfx):
            raw_prim = bytes(ds_obj.primitiveList.data)
            # Decode the 4-byte FourCC shader mode for Type-7 display states.
            # Setting field is at ds_obj.absolute + 4 (id=1B, pad=3B, then setting=4B).
            # Use ASCII only when all bytes are printable (32-126); otherwise hex.
            # This prevents control characters (e.g. \x00, \x11) from appearing as
            # raw unicode escapes in the JSON output.
            setting_bytes = itb(ds_obj.setting, 4)
            if all(32 <= b <= 126 for b in setting_bytes):
                setting_fourcc = setting_bytes.decode('ascii')
            else:
                setting_fourcc = setting_bytes.hex()
            display_states_export.append({
                "DisplayStateId": ds_obj.id,
                "DisplayStatePadBytes": ds_obj.pad_bytes.hex(),
                "ShaderModeFieldOffset": hex(ds_obj.absolute + 4),
                "ShaderMode": setting_fourcc,
                "PrimListPtrFieldOffset": hex(ds_obj.absolute + 8),
                "PrimListSizeFieldOffset": hex(ds_obj.absolute + 12),
                "PrimListAbsoluteOffset": hex(layout.absolute + ds_obj.primitiveListPtr),
                "PrimListLength": ds_obj.primitiveListSize,
                "PrimListData": _encode_bytes(raw_prim),
                "VertexStreamLayout": [
                    {"key": d['key'], "index_size": d['index_size']}
                    for d in display_state['state']['descriptors']
                ]
            })
        # Extract normal buffer for non-skinned meshes (separate DOLightingHeader array)
        lh = layout.DOLightingHeader
        normal_buffer = None
        if lh.normalsPtr != 0:
            normal_abs_offset = layout.absolute + lh.normalsPtr
            normal_length = lh.numNormals * lh.compCount * _vb_comp_size(lh.quantizeInfo)
            model.f.seek(normal_abs_offset)
            raw_normals = model.f.read(normal_length)
            ambient_pct = struct.unpack('>f', lh.ambientPercentage)[0]
            normal_buffer = {
                "NormalDataPtrFieldOffset": hex(lh.absolute),
                "NormalCountFieldOffset": hex(lh.absolute + 4),
                "NormalBufferOffset": hex(normal_abs_offset),
                "NormalBufferLength": normal_length,
                "NormalBufferCompCount": lh.compCount,
                "NormalBufferQuantizeInfo": lh.quantizeInfo,
                "NormalAmbientPct": round(ambient_pct, 6),
                "NormalBufferData": _encode_bytes(raw_normals)
            }

        submesh_entry = {
            "MeshName": descriptor.n,
            "SubmeshOffset": hex(layout.absolute),
            "PositionDataPtrFieldOffset": hex(pos.absolute),
            "VertexCountFieldOffset": hex(pos.absolute + 4),
            "FacesCount": face_count,
            "FacesData": faces_data,
            "FaceTextureIndices": face_tex_data,
            "DisplayStates": display_states_export,
            "VertexBuffer": {
                "VertexBufferOffset": hex(vb_offset),
                "VertexBufferLength": vb_length,
                "VertexBufferCompCount": pos.compCount,
                "VertexBufferQuantizeInfo": pos.quantizeInfo,
                "VertexBufferData": vb_data
            },
            "UVChannels": uv_channels,
            "ColorChannels": color_channels
        }
        if normal_buffer is not None:
            submesh_entry["NormalBuffer"] = normal_buffer
        submeshes.append(submesh_entry)
    return submeshes

def extract_skin_data(model):
    """Return a SkinData dict for a Model0 instance, or None if unskinned.

    Exports all SKN/SK1/SK2/SKAcc structural fields needed by skn_patch.py:
    - Absolute offsets of every pointer field (for patching)
    - Current pointer target values (for restoring on --unpatch)
    - Raw source data bytes (bind-pose positions/normals per bone entry)
    - Raw weight and destination-index arrays

    SK1 struct fields (at SK1.absolute):
      +0x30 vertexArr   — source position+normal data, relative to SKN.absolute
      +0x34 gplVertexArr — dest byte offset within the runtime memClr region
    SK2 struct fields (at SK2.absolute):
      +0x60 vertexArr, +0x64 weightArr, +0x68 gplVertexArr
    SKAcc struct fields (at SKAcc.absolute):
      +0x30 vertexArr, +0x34 destArr (index array), +0x38 gplDestArr, +0x3C weightArr
    """
    if not model.SKN:
        return None
    skn = model.SKN
    skn_abs = skn.absolute
    comp_size = _vb_comp_size(skn.quantizeInfo)

    sk1s = []
    for sk1 in skn.SK1s:
        src_abs  = skn_abs + sk1.vertexArr
        src_size = sk1.vertexOffset + sk1.vertexCnt * 2 * 3 * comp_size
        model.f.seek(src_abs)
        src_data = model.f.read(src_size)
        sk1s.append({
            "BoneIndex":           sk1.boneIndex,
            "VertexCnt":           sk1.vertexCnt,
            "VertexOffset":        sk1.vertexOffset,
            "VertexArrFieldOffset":    hex(sk1.absolute + 0x30),
            "GplVertexArrFieldOffset": hex(sk1.absolute + 0x34),
            "VertexArrAbsolutePtr":    hex(src_abs),
            "GplVertexArrValue":       sk1.gplVertexArr,
            "BindPoseData":             _encode_bytes(src_data)
        })

    sk2s = []
    for sk2 in skn.SK2s:
        src_abs  = skn_abs + sk2.vertexArr
        src_size = sk2.vertexOffset + sk2.vertexCnt * 2 * 3 * comp_size
        model.f.seek(src_abs)
        src_data = model.f.read(src_size)
        wt_abs   = skn_abs + sk2.weightArr
        model.f.seek(wt_abs)
        wt_data  = model.f.read(sk2.vertexCnt * 2)  # 2 weight bytes per vertex
        sk2s.append({
            "BoneIndex1":          sk2.boneIndex1,
            "BoneIndex2":          sk2.boneIndex2,
            "VertexCnt":           sk2.vertexCnt,
            "VertexOffset":        sk2.vertexOffset,
            "VertexArrFieldOffset":    hex(sk2.absolute + 0x60),
            "WeightArrFieldOffset":    hex(sk2.absolute + 0x64),
            "GplVertexArrFieldOffset": hex(sk2.absolute + 0x68),
            "VertexArrAbsolutePtr":    hex(src_abs),
            "WeightArrAbsolutePtr":    hex(wt_abs),
            "GplVertexArrValue":       sk2.gplVertexArr,
            "BindPoseData":             _encode_bytes(src_data),
            "WeightData":              _encode_bytes(wt_data)
        })

    skaccs = []
    for skacc in skn.SKAccs:
        src_abs      = skn_abs + skacc.vertexArr
        src_size     = skacc.vertexCnt * 2 * 3 * comp_size  # no vertexOffset on SKAcc
        model.f.seek(src_abs)
        src_data     = model.f.read(src_size)
        dest_idx_abs = skn_abs + skacc.destArr
        model.f.seek(dest_idx_abs)
        dest_idx_data = model.f.read(skacc.vertexCnt * 2)   # uint16 dest indices
        wt_abs       = skn_abs + skacc.weightArr
        model.f.seek(wt_abs)
        wt_data      = model.f.read(skacc.vertexCnt)         # 1 weight byte per vertex
        skaccs.append({
            "BoneIndex":           skacc.boneIndex,
            "VertexCnt":           skacc.vertexCnt,
            "VertexArrFieldOffset":  hex(skacc.absolute + 0x30),
            "DestArrFieldOffset":    hex(skacc.absolute + 0x34),
            "GplDestArrFieldOffset": hex(skacc.absolute + 0x38),
            "WeightArrFieldOffset":  hex(skacc.absolute + 0x3C),
            "VertexArrAbsolutePtr":  hex(src_abs),
            "DestArrAbsolutePtr":    hex(dest_idx_abs),
            "GplDestArrValue":       skacc.gplDestArr,
            "WeightArrAbsolutePtr":  hex(wt_abs),
            "BindPoseData":          _encode_bytes(src_data),
            "DestIndexData":         _encode_bytes(dest_idx_data),
            "WeightData":            _encode_bytes(wt_data)
        })

    return {
        "SKNOffset":            hex(skn_abs),
        "GplBaseOffset":        hex(model.GPL.absolute) if model.GPL else None,
        "MemClrPtrFieldOffset": hex(skn_abs + 0x14),
        "MemClrSzeFieldOffset": hex(skn_abs + 0x18),
        "MemClrAbsolutePtr":    hex(model.GPL.absolute + skn.memClrPtr),
        "MemClrSize":           skn.memClrSze,
        "FlushIndArrFieldOffset": hex(skn_abs + 0x1C),
        "FlushIndAbsolutePtr":  hex(skn_abs + skn.flushIndArr) if skn.flushIndArr else None,
        "FlushIndSize":         skn.flushIndSze,
        "FlushIndData":         _extract_flush_data(model, skn, skn_abs),
        "QuantizeInfo":         skn.quantizeInfo,
        "SK1s":  sk1s,
        "SK2s":  sk2s,
        "SKAccs": skaccs
    }

def _extract_flush_data(model, skn, skn_abs):
    """Read the raw flush index array bytes (uint16 × flushIndSze), or None."""
    if not skn.flushIndArr or not skn.flushIndSze:
        return None
    model.f.seek(skn_abs + skn.flushIndArr)
    return _encode_bytes(model.f.read(skn.flushIndSze * 2))

def extract_trailing_sections(model):
    """Return a list of trailing sub-sections referenced by header ptr6/ptr7/ptr8.

    Each entry carries the header field offset (0x14/0x18/0x1c), the original
    block-relative pointer, and the raw section bytes so the hammerspace
    importer can rebuild the block from the .sluggies file alone."""
    sections = []
    all_ptrs = sorted(
        p for p in [model.gplPtr, model.ptr3, model.texPtr,
                    model.ptr5, model.ptr6, model.ptr7, model.ptr8]
        if 0 < p < model.length
    )
    for field_off, ptr in ((0x14, model.ptr6), (0x18, model.ptr7), (0x1c, model.ptr8)):
        if not ptr or ptr >= model.length:
            continue
        nxt = min([x for x in all_ptrs if x > ptr] + [model.length])
        if nxt <= ptr:
            continue
        model.f.seek(model.absolute + ptr)
        data = model.f.read(nxt - ptr)
        sections.append({
            "HeaderFieldOffset": hex(field_off),
            "OriginalPtr":       hex(ptr),
            "Length":            nxt - ptr,
            "Data":              _encode_bytes(data),
        })
    return sections


def extract_facial_pose_data(model):
    """Decode the optional ptr7 facial position/normal pose section.

    Unrecognized ptr7 payloads return None and remain preserved through
    TrailingSections. All offsets stored by this function are either explicitly
    section-relative or absolute dt_na.dat file offsets.
    """
    if not getattr(model, 'ptr7', 0):
        return None

    section_relative = model.ptr7
    section_absolute = model.absolute + section_relative
    following_ptrs = [
        ptr for ptr in (
            model.gplPtr, model.ptr3, model.texPtr, model.ptr5,
            model.ptr6, model.ptr7, model.ptr8,
        )
        if section_relative < ptr < model.length
    ]
    section_end_relative = min(following_ptrs, default=model.length)
    section_length = section_end_relative - section_relative
    if section_length < 0x14:
        return None

    model.f.seek(section_absolute)
    section = model.f.read(section_length)

    def u16(offset):
        if offset < 0 or offset + 2 > len(section):
            raise ValueError('facial uint16 outside section')
        return struct.unpack_from('>H', section, offset)[0]

    def u32(offset):
        if offset < 0 or offset + 4 > len(section):
            raise ValueError('facial uint32 outside section')
        return struct.unpack_from('>I', section, offset)[0]

    def parse_attribute(record_offset, record_size, pose_count, run_end):
        if record_offset < 0 or record_offset + record_size > len(section):
            raise ValueError('facial attribute record outside section')
        entry_count = u32(record_offset)
        format_data = section[record_offset + 4:record_offset + 8]
        submesh_index, attribute_kind, component_count, component_size = format_data
        if component_count not in (3, 6) or component_size not in (1, 2, 4):
            raise ValueError('unrecognized facial attribute format')
        run_list_offset = u32(record_offset + 8)
        pose_offsets = [
            u32(record_offset + 0x0C + index * 4)
            for index in range(pose_count)
        ]
        pose_size = entry_count * component_count * component_size
        if not (run_list_offset <= run_end <= len(section)):
            raise ValueError('invalid facial run-list bounds')
        if any(offset + pose_size > len(section) for offset in pose_offsets):
            raise ValueError('facial pose array outside section')

        runs = []
        expanded_indices = []
        for offset in range(run_list_offset, run_end, 4):
            first_vertex = u16(offset)
            vertex_count = u16(offset + 2)
            runs.append({
                'FirstVertex': first_vertex,
                'VertexCount': vertex_count,
            })
            expanded_indices.extend(range(first_vertex, first_vertex + vertex_count))
        if len(expanded_indices) != entry_count:
            raise ValueError('facial run-list count does not match attribute count')

        return {
            'RecordOffset': hex(record_offset),
            'RecordAbsoluteOffset': hex(section_absolute + record_offset),
            'EntryCount': entry_count,
            'FormatData': _encode_bytes(format_data),
            'SubmeshIndex': submesh_index,
            'AttributeKind': attribute_kind,
            'ComponentCount': component_count,
            'ComponentSize': component_size,
            'RunListOffset': hex(run_list_offset),
            'RunListAbsoluteOffset': hex(section_absolute + run_list_offset),
            'RunListData': _encode_bytes(section[run_list_offset:run_end]),
            'Runs': runs,
            'PoseOffsets': [hex(offset) for offset in pose_offsets],
            'PoseAbsoluteOffsets': [hex(section_absolute + offset) for offset in pose_offsets],
            'PoseData': [
                _encode_bytes(section[offset:offset + pose_size])
                for offset in pose_offsets
            ],
        }, expanded_indices

    try:
        maximum_pose_count = u16(0x00)
        object_count = u16(0x02)
        attribute_type_count = u16(0x04)
        object_table_offset = u32(0x08)
        if (
            not maximum_pose_count
            or not object_count
            or object_table_offset + object_count * 12 > len(section)
        ):
            return None

        submesh_arrays = []
        if getattr(model, 'GPL', None):
            for submesh_index, descriptor in enumerate(model.GPL.geoDescriptors):
                layout = descriptor.layout
                position_header = layout.DOPositionHeader
                position_size = (
                    position_header.numPositions * position_header.compCount
                    * _vb_comp_size(position_header.quantizeInfo)
                )
                position_offset = layout.absolute + position_header.positionArrPtr
                model.f.seek(position_offset)
                position_data = model.f.read(position_size)

                normal_header = layout.DOLightingHeader
                normal_data = None
                if normal_header.normalsPtr:
                    normal_size = (
                        normal_header.numNormals * normal_header.compCount
                        * _vb_comp_size(normal_header.quantizeInfo)
                    )
                    model.f.seek(layout.absolute + normal_header.normalsPtr)
                    normal_data = model.f.read(normal_size)
                submesh_arrays.append((
                    submesh_index,
                    descriptor.n,
                    position_data,
                    position_header.compCount * _vb_comp_size(position_header.quantizeInfo),
                    normal_data,
                ))

        objects = []
        recognized_objects = 0
        for object_index in range(object_count):
            table_entry_offset = object_table_offset + object_index * 12
            pose_count = u16(table_entry_offset)
            attribute_count = u16(table_entry_offset + 2)
            attribute_record_size = u32(table_entry_offset + 4)
            object_data_offset = u32(table_entry_offset + 8)
            if (
                not pose_count
                or not attribute_count
                or attribute_record_size != 0x0C + pose_count * 4
                or object_data_offset + attribute_record_size * attribute_count > len(section)
            ):
                raise ValueError('facial object data outside section')

            record_offsets = [
                object_data_offset + index * attribute_record_size
                for index in range(attribute_count)
            ]
            run_offsets = [u32(offset + 8) for offset in record_offsets]
            all_pose_offsets = [
                u32(offset + 0x0C + pose_index * 4)
                for offset in record_offsets
                for pose_index in range(pose_count)
            ]
            attributes = []
            attribute_indices = []
            for attribute_index, record_offset in enumerate(record_offsets):
                run_end = (
                    run_offsets[attribute_index + 1]
                    if attribute_index + 1 < attribute_count
                    else min(all_pose_offsets)
                )
                attribute, indices = parse_attribute(
                    record_offset, attribute_record_size, pose_count, run_end
                )
                attributes.append(attribute)
                attribute_indices.append(indices)

            position_attribute_index = next(
                (index for index, attribute in enumerate(attributes)
                 if attribute['AttributeKind'] == 1),
                None,
            )
            if position_attribute_index is None:
                raise ValueError('facial object has no position attribute')
            position = attributes[position_attribute_index]
            position_indices = attribute_indices[position_attribute_index]
            normal_attribute_index = next(
                (index for index, attribute in enumerate(attributes)
                 if attribute['AttributeKind'] == 2),
                None,
            )
            normal = (
                attributes[normal_attribute_index]
                if normal_attribute_index is not None else None
            )
            normal_indices = (
                attribute_indices[normal_attribute_index]
                if normal_attribute_index is not None else []
            )

            submesh_index = position['SubmeshIndex']
            mesh_name = None
            for candidate_index, candidate_name, positions, position_buffer_stride, normals in submesh_arrays:
                if candidate_index != submesh_index:
                    continue
                position_stride = position['ComponentCount'] * position['ComponentSize']
                position_pose_zero_offset = int(position['PoseOffsets'][0], 16)
                position_pose_zero = section[
                    position_pose_zero_offset:
                    position_pose_zero_offset + position['EntryCount'] * position_stride
                ]
                position_matches = (
                    max(position_indices, default=-1) * position_buffer_stride
                    + position_stride <= len(positions)
                    and all(
                        position_pose_zero[index * position_stride:(index + 1) * position_stride]
                        == positions[
                            vertex_index * position_buffer_stride:
                            vertex_index * position_buffer_stride + position_stride
                        ]
                        for index, vertex_index in enumerate(position_indices)
                    )
                )
                if normal is not None:
                    normal_stride = normal['ComponentCount'] * normal['ComponentSize']
                    normal_pose_zero_offset = int(normal['PoseOffsets'][0], 16)
                    normal_pose_zero = section[
                        normal_pose_zero_offset:
                        normal_pose_zero_offset + normal['EntryCount'] * normal_stride
                    ]
                    normal_matches = (
                        normals is not None
                        and max(normal_indices, default=-1) * 6 + 6 <= len(normals)
                        and all(
                            normal_pose_zero[index * normal_stride:index * normal_stride + 6]
                            == normals[vertex_index * 6:(vertex_index + 1) * 6]
                            for index, vertex_index in enumerate(normal_indices)
                        )
                    )
                elif position['ComponentCount'] >= 6:
                    normal_matches = position_matches
                else:
                    normal_matches = True
                if position_matches and normal_matches:
                    mesh_name = candidate_name
                    break
            if mesh_name is None:
                submesh_index = None

            objects.append({
                'ObjectIndex': object_index,
                'TableEntryOffset': hex(table_entry_offset),
                'TableEntryAbsoluteOffset': hex(section_absolute + table_entry_offset),
                'TableEntryData': _encode_bytes(section[table_entry_offset:table_entry_offset + 12]),
                'ObjectDataOffset': hex(object_data_offset),
                'ObjectDataAbsoluteOffset': hex(section_absolute + object_data_offset),
                'PoseCount': pose_count,
                'AttributeCount': attribute_count,
                'AttributeRecordSize': attribute_record_size,
                'SubmeshIndex': submesh_index,
                'MeshName': mesh_name,
                'Position': position,
                'Normal': normal,
                'AuxiliaryAttributes': [
                    attribute for attribute in attributes
                    if attribute['AttributeKind'] not in (1, 2)
                ],
            })
            recognized_objects += 1

        if recognized_objects != object_count:
            return None
        return {
            'SectionOffset': hex(section_absolute),
            'HeaderFieldOffset': hex(model.absolute + 0x18),
            'SectionLength': section_length,
            'SectionData': _encode_bytes(section),
            'HeaderData': _encode_bytes(section[:object_table_offset]),
            'PoseCount': maximum_pose_count,
            'AttributeTypeCount': attribute_type_count,
            'ObjectCount': object_count,
            'ObjectTableOffset': hex(object_table_offset),
            'Objects': objects,
        }
    except (ValueError, struct.error):
        return None

def extract_act_header(model):
    """Return an ACTHeader dict with actor/skin IDs, geo name, and the
    tree-unknown word at Tree+0x00, or None if the model has no ACT section."""
    if not hasattr(model, 'ACT') or not model.ACT:
        return None
    act = model.ACT
    # Tree struct is at act.absolute + 0x8.  Its first word is read-and-discarded
    # by Tree.analyze(); re-read it here so we can preserve it on reassembly.
    model.f.seek(act.absolute + 0x8)
    tree_unknown = bti(model.f.read(4))
    return {
        "ActorID":        act.actorID,
        "SkinFileID":     act.skinFileID,
        "GeoName":        act.geoName,
        "ACTTreeUnknown": tree_unknown,
    }


def extract_bone_data(model):
    """Return a BoneHierarchy list for the .sluggie JSON, or None if no ACT/bones present.

    Each entry contains bone identity, local transform components, absolute
    world-space head position, parent link, and per-vertex weight influences
    grouped by submesh index.

    Vertex index mapping:
    - Non-skinned bones (Skinned=False): GEOID == submesh index; VertexIndex is
      already local to that submesh (0..numPositions-1).
    - Skinned bones (Skinned=True): the raw index from SK1/SK2/SKAcc entries is
      a GPL-relative global index (gplVertexArr // skn_vs + i).  This function
      converts it to a (submesh_index, local_vertex_index) pair using the
      per-submesh vertex buffer byte offsets within the GPL section.
    """
    if not model.ACT or not model.bones:
        return None

    bones = model.bones  # dict: bone_id -> Bone

    # Build lookup by bone id to access ACTBoneLayout fields not on Bone.
    layout_by_id = {bl.id: bl for bl in model.ACT.bone_layouts.values()}

    # Build per-submesh (global_vtx_start, vtx_count) for skinned index remapping.
    # gplVertexArr in SK1/SK2/SKAcc is a byte offset from the start of the runtime
    # dest buffer; divided by vertexSize it gives a global vertex index that runs
    # sequentially across all submeshes (0, 1, 2, … total_verts-1).  Therefore
    # the start index for submesh j is simply the sum of vertex counts of 0..j-1.
    submesh_vtx_starts = []
    if model.GPL and model.SKN:
        cumulative = 0
        for desc in model.GPL.geoDescriptors:
            count = desc.layout.DOPositionHeader.numPositions
            submesh_vtx_starts.append((cumulative, count))
            cumulative += count

    bone_list = []
    for bone_id in sorted(bones.keys()):
        bone = bones[bone_id]
        head  = [round(float(v), 6) for v in bone.head()]
        trans = [round(float(v), 6) for v in bone.orientation.translation]
        scale = [round(float(v), 6) for v in bone.orientation.scale]
        quat  = [round(float(v), 6) for v in bone.orientation.quaternion]

        # Group vertex influences by submesh index
        by_submesh = {}
        for v_idx, (raw_weight, _src_pos, _sources) in bone.vertexInfluences.items():
            w = round(float(raw_weight) / 256.0, 6)
            if bone.skinned and submesh_vtx_starts:
                sub_idx = None
                local_idx = v_idx
                for j, (start, count) in enumerate(submesh_vtx_starts):
                    if start <= v_idx < start + count:
                        sub_idx = j
                        local_idx = v_idx - start
                        break
                if sub_idx is None:
                    continue  # vertex doesn't fall in any known submesh — skip
            else:
                # Non-skinned: GEOID is the submesh index; v_idx is already local
                sub_idx = bone.GEOID
                local_idx = v_idx

            if sub_idx not in by_submesh:
                by_submesh[sub_idx] = []
            by_submesh[sub_idx].append((int(local_idx), w))

        influences_by_submesh = [
            {
                "SubmeshIndex": sub_idx,
                "Influences": _encode_bytes(
                    b''.join(struct.pack('>Hf', vi, w) for vi, w in inf_list)
                )
            }
            for sub_idx, inf_list in sorted(by_submesh.items())
        ]

        bl = layout_by_id.get(bone_id)
        srt_type     = bl.orientation_srt.type if bl and bl.orientation_srt else 0
        draw_priority = int(bl.priority) if bl else 0

        bone_list.append({
            "BoneId":          int(bone.id),
            "GeoId":           int(bone.GEOID),
            "ParentBoneId":    int(bone.parent.id) if bone.parent else None,
            "Skinned":         bool(bone.skinned),
            "TrackId":         int(bone.track_id),
            "SRTType":         int(srt_type),
            "DrawPriority":    draw_priority,
            "InheritTransform": bool(bone.relative),
            "Translation":     trans,
            "Scale":           scale,
            "Quaternion":      quat,
            "HeadPosition":    head,
            "VertexInfluences": influences_by_submesh,
        })

    return bone_list


class Dat(File):
    def __init__(self, f):
        super().__init__(f)


def prepare_untangle_output_files():
    output_dat_dir = '../3_Output_Dat'
    output_dat_path = os.path.join(output_dat_dir, 'dt_na.dat')
    output_dol_path = os.path.join(output_dat_dir, 'main.dol')
    input_dat_path = '../1_Input/dt_na.dat'
    input_dol_path = '../1_Input/main.dol'

    if not os.path.exists(output_dat_dir):
        os.mkdir(output_dat_dir)

    if os.path.exists(output_dat_path) or os.path.exists(output_dol_path):
        answer = input('Untangle mode will overwrite 3_Output_Dat/dt_na.dat and main.dol. Continue? (y/n): ').strip().lower()
        if answer != 'y':
            print('Untangle export canceled by user.')
            return None, None

    shutil.copyfile(input_dat_path, output_dat_path)
    shutil.copyfile(input_dol_path, output_dol_path)
    return output_dat_path, output_dol_path

untangle_context = None
active_dol_path = '../1_Input/main.dol'
active_dat_path = '../1_Input/dt_na.dat'

if UNTANGLE_TEX:
    if not EXPORT_TEX:
        print('Warning: --untangle has no effect with --notex; untangle mode disabled.')
    else:
        untangle_output_path, untangle_output_dol_path = prepare_untangle_output_files()
        if untangle_output_path is None:
            sys.exit(0)

        untangle_bootstrap_report = []
        clone_count = clone_unused_dirs_to_hammerspace_for_untangle(
            input_dol_path='../1_Input/main.dol',
            input_dat_path='../1_Input/dt_na.dat',
            report_lines=untangle_bootstrap_report,
        )

        active_dol_path = untangle_output_dol_path
        active_dat_path = untangle_output_path

        untangle_context = {
            'enabled': True,
            'seen_names': set(),
            'seen_image_starts': {},
            'report_lines': [
                f'Hammerspace clone pre-pass complete: cloned {clone_count} entries from dirs 89-94.'
            ] + untangle_bootstrap_report,
            'warnings': [],
            'name_overrides': {},
            'max_attempts': 8192,
            'dat_output_handle': open(untangle_output_path, 'r+b')
        }

dirs = load_dol_dirs(active_dol_path)
dat = Dat(open(active_dat_path, 'rb'))

for dir_ind, file_arr in dirs.items():
    set_log_dir_index(dir_ind)
    dir_dir = outdir + top_level_folder_name(dir_ind) + '/'
    if not os.path.exists(dir_dir):
        os.mkdir(dir_dir)
    for file_index, file in enumerate(file_arr):
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
                    child.child.toFile(lan_dir, export_tex=EXPORT_TEX, export_dae=EXPORT_DAE, untangle_context=untangle_context)
                    if isinstance(child.child, Archive):
                        archive_dir = os.path.join(lan_dir, str(child.child.absolute))
                        for i in child.child.success:
                            sub_model = child.child.files[i]
                            sub_dir = os.path.join(archive_dir, sub_model.name)
                            json_name = f"{sub_model.name}.sluggie"
                            _gpl_ud, _gpl_ud_len = extract_gpl_userdata(sub_model)
                            model_json = {
                                "SluggiesModel": {
                                    "ChunkNumber": dir_ind,
                                    "FileIndex": file_index,
                                    "ModelOffset": hex(sub_model.absolute),
                                    "ModelLength": sub_model.length,
                                    "UseBase64": not DEBUG_DONT_USE_BASE64,
                                    "GPLUserDataLength": _gpl_ud_len,
                                    "GPLUserData": _gpl_ud,
                                    "TEXHeader": extract_tex_header(sub_model),
                                    "TextureDescriptors": extract_texture_descriptors(sub_model, untangle_context=untangle_context),
                                    "Submeshes": extract_submeshes(sub_model),
                                    "SkinData": extract_skin_data(sub_model),
                                    "FacialPoseData": extract_facial_pose_data(sub_model),
                                    "TrailingSections": extract_trailing_sections(sub_model),
                                    "ACTHeader": extract_act_header(sub_model),
                                    "BoneHierarchy": extract_bone_data(sub_model)
                                }
                            }
                            with open(os.path.join(sub_dir, json_name), 'w') as info_f:
                                info_f.write(compact_faces_json(model_json))
                    else:
                        model_name = child.child.name
                        model_dir = os.path.join(lan_dir, model_name)
                        json_name = f"{model_name}.sluggie"
                        _gpl_ud, _gpl_ud_len = extract_gpl_userdata(child.child)
                        model_json = {
                            "SluggiesModel": {
                                "ChunkNumber": dir_ind,
                                "FileIndex": file_index,
                                "ModelOffset": hex(offset),
                                "ModelLength": l,
                                "UseBase64": not DEBUG_DONT_USE_BASE64,
                                "GPLUserDataLength": _gpl_ud_len,
                                "GPLUserData": _gpl_ud,
                                "TEXHeader": extract_tex_header(child.child),
                                "TextureDescriptors": extract_texture_descriptors(child.child, untangle_context=untangle_context),
                                "Submeshes": extract_submeshes(child.child),
                                "SkinData": extract_skin_data(child.child),
                                "FacialPoseData": extract_facial_pose_data(child.child),
                                "TrailingSections": extract_trailing_sections(child.child),
                                "ACTHeader": extract_act_header(child.child),
                                "BoneHierarchy": extract_bone_data(child.child)
                            }
                        }
                        with open(os.path.join(model_dir, json_name), 'w') as info_f:
                            info_f.write(compact_faces_json(model_json))
                del child
        except ExpectedFormatSkip as exc:
            print(f'[dir {dir_ind}] {exc}')
        except Exception as e:
            print(f'[dir {dir_ind}] skipping entry: {type(e).__name__}: {e}')
            pass
    if len(os.listdir(dir_dir)) == 0:
        os.rmdir(dir_dir)
    print (f'[dir {dir_ind}] Finished analyzing')

if untangle_context and untangle_context.get('dat_output_handle'):
    untangle_context['dat_output_handle'].flush()
    untangle_context['dat_output_handle'].close()

write_texture_hash_overlaps_report(
    outdir,
    untangle_report_lines=(untangle_context or {}).get('report_lines'),
    untangle_warnings=(untangle_context or {}).get('warnings')
)