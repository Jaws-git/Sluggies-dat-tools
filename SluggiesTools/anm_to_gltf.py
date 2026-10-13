"""Convert raw .anm binary files to .glb (glTF binary) using skeleton data from .sluggie JSON.

Run by `start.py --export --glb` after each character folder is exported (export.py
dumps the raw files to <character>/anm/<name>_<file index>[_<theme>].anm, see
anm.anm_file_name), or by hand:
    python anm_to_gltf.py <character_directory>

Each .anm becomes a .glb of the same name next to it: the skeleton of the character's
.sluggie with the most bones, plus one glTF animation per ANM sequence. Tracks map to
bones through the bones' TrackId. Keyframes are sampled LINEAR at 60 fps (the game's
Bezier/Hermite/SQUAD tangents are not carried over).
"""

import glob
import os
import struct
import sys

import numpy as np

import slogger
from binfmt import load_sluggie
from glb_export import _Glb, FLOAT, gltf_rotation, trs_matrix
from Roster.anim import MAGICS as ANM_MAGICS
FRAME_RATE = 60.0


def read_quantized(data, offset, count, dimensions, quantize_info):
    fmt_nibble = quantize_info >> 4
    shift = quantize_info & 0x0F
    if fmt_nibble in (0, 3):
        fmt_char, fmt_size = '>h', 2
    else:
        fmt_char, fmt_size = '>f', 4
    divisor = 1 << shift
    result = []
    pos = offset
    for _ in range(count):
        components = []
        for _ in range(dimensions):
            val = struct.unpack_from(fmt_char, data, pos)[0]
            components.append(val / divisor)
            pos += fmt_size
        result.append(components)
    return result, pos


def parse_anm(data):
    magic = struct.unpack_from('>I', data, 0)[0]
    if magic not in ANM_MAGICS:
        raise ValueError(f"Not an ANM file: magic {magic:#010x}")

    seq_arr_ptr = struct.unpack_from('>I', data, 4)[0]
    _bank_id, seq_cnt, _track_cnt, _kf_cnt = struct.unpack_from('>HHHH', data, 8)

    sequences = []
    for si in range(seq_cnt):
        seq_off = seq_arr_ptr + si * 12
        _name_ptr, track_arr_ptr, track_cnt, _pad = struct.unpack_from('>IIHH', data, seq_off)

        tracks = []
        for ti in range(track_cnt):
            t_off = track_arr_ptr + ti * 16
            anm_time_raw, kf_arr_ptr, kf_cnt, track_id = struct.unpack_from('>fIHH', data, t_off)
            quantize_info, anm_type_raw, _interp_type, _replace = struct.unpack_from('>BBBB', data, t_off + 12)
            anm_type = anm_type_raw & 0x1F

            # 0 translation, 1 scale, 3 quaternion rotation (bank order: scale, rotation, translation)
            active_channels = [bit for bit in (1, 3, 0) if anm_type & (1 << bit)]

            keyframes = []
            for ki in range(kf_cnt):
                kf_off = kf_arr_ptr + ki * 12
                time_val = struct.unpack_from('>f', data, kf_off)[0]
                bank_pos = struct.unpack_from('>I', data, kf_off + 4)[0]

                settings = {}
                for ch in active_channels:
                    if ch == 3:
                        vals, bank_pos = read_quantized(data, bank_pos, 1, 4, 0x3E)
                        xyzw = vals[0]
                        settings[ch] = [-xyzw[3], xyzw[0], xyzw[1], xyzw[2]]
                    else:
                        vals, bank_pos = read_quantized(data, bank_pos, 1, 3, quantize_info)
                        settings[ch] = vals[0]

                keyframes.append({'time': time_val, 'settings': settings})

            tracks.append({
                'track_id': track_id,
                'anm_time': anm_time_raw,
                'active_channels': active_channels,
                'keyframes': keyframes,
            })

        sequences.append({'tracks': tracks})

    return sequences


def anm_to_glb(sequences, bone_hierarchy, name='anm'):
    """Bytes of a .glb: the .sluggie BoneHierarchy as a skinned armature (no mesh)
    plus one animation per sequence."""
    g = _Glb()
    gl = g.gltf

    order = sorted(bone_hierarchy, key=lambda b: b['BoneId'])
    node_of = {b['BoneId']: i for i, b in enumerate(order)}
    nodes, local, track_node = [], {}, {}
    for b in order:
        t = [float(v) for v in b.get('Translation', [0, 0, 0])]
        q = gltf_rotation(b.get('Quaternion', [-1, 0, 0, 0]))
        s = [float(v) for v in b.get('Scale', [1, 1, 1])]
        nodes.append({"name": f"bone_{b['BoneId']}", "translation": t,
                      "rotation": [float(v) for v in q], "scale": s})
        local[b['BoneId']] = trs_matrix(t, q, s)
        tid = b.get('TrackId', 0xFFFF)
        if tid != 0xFFFF:
            track_node[tid] = node_of[b['BoneId']]

    roots, parent_of = [], {}
    for b in order:
        parent = b['ParentBoneId']
        if parent is not None and parent in node_of:
            nodes[node_of[parent]].setdefault("children", []).append(node_of[b['BoneId']])
            parent_of[b['BoneId']] = parent
        else:
            roots.append(node_of[b['BoneId']])

    world = {}

    def world_of(bone_id):
        if bone_id not in world:
            parent = parent_of.get(bone_id)
            world[bone_id] = (world_of(parent) @ local[bone_id]) if parent is not None else local[bone_id]
        return world[bone_id]

    nodes.append({"name": "Armature", "children": roots, "skin": 0})
    armature = len(nodes) - 1
    gl["nodes"] = nodes
    ibm = np.array([np.linalg.inv(world_of(b['BoneId'])).T.reshape(-1) for b in order], dtype=np.float32)
    gl["skins"] = [{"name": "Armature", "joints": list(range(len(order))), "skeleton": armature,
                    "inverseBindMatrices": g.accessor(ibm, "MAT4", FLOAT, target=None)}]
    gl["scenes"] = [{"name": name, "nodes": [armature]}]
    gl["scene"] = 0

    paths = {3: ('rotation', "VEC4"), 0: ('translation', "VEC3"), 1: ('scale', "VEC3")}
    animations = []
    for seq_i, sequence in enumerate(sequences):
        channels, samplers = [], []
        for track in sequence['tracks']:
            node = track_node.get(track['track_id'])
            kfs = track['keyframes']
            if node is None or not kfs:
                continue
            times = g.accessor(np.array([[kf['time'] / FRAME_RATE] for kf in kfs], dtype=np.float32),
                               "SCALAR", FLOAT, target=None, minmax=True)
            for ch in track['active_channels']:
                if ch == 3:
                    values = [gltf_rotation(kf['settings'][3]) for kf in kfs]
                else:
                    values = [kf['settings'][ch] for kf in kfs]
                path, kind = paths[ch]
                out = g.accessor(np.array(values, dtype=np.float32), kind, FLOAT, target=None)
                samplers.append({"input": times, "output": out, "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})
        if channels:
            animations.append({"name": f"sequence_{seq_i}", "channels": channels, "samplers": samplers})
    if animations:
        gl["animations"] = animations
    return g.glb()


def find_sluggie(char_dir):
    """The .sluggie under char_dir with the largest BoneHierarchy (the body model)."""
    best, best_bones = None, 0
    for path in glob.glob(os.path.join(char_dir, '**', '*.sluggie'), recursive=True):
        try:
            data = load_sluggie(path)
            bh = data.get('SluggiesModel', data).get('BoneHierarchy')
        except (OSError, ValueError, AttributeError):
            continue
        if bh and len(bh) > best_bones:
            best, best_bones = path, len(bh)
    return best


def convert_anm_file(anm_path, bone_hierarchy):
    with open(anm_path, 'rb') as f:
        sequences = parse_anm(f.read())
    if not sequences:
        return None
    out_path = os.path.splitext(anm_path)[0] + '.glb'
    with open(out_path, 'wb') as f:
        f.write(anm_to_glb(sequences, bone_hierarchy, name=os.path.basename(out_path)))
    return out_path


def convert_anm_directory(char_dir):
    anm_dir = os.path.join(char_dir, 'anm')
    if not os.path.isdir(anm_dir):
        return []

    sluggie_path = find_sluggie(char_dir)
    if not sluggie_path:
        slogger.warning(f"No .sluggie with bones in {char_dir}, skipping ANM glTF conversion", source='anm_to_gltf')
        return []
    sluggie = load_sluggie(sluggie_path)
    bone_hierarchy = sluggie.get('SluggiesModel', sluggie)['BoneHierarchy']

    results = []
    for anm_file in sorted(glob.glob(os.path.join(anm_dir, '*.anm'))):
        try:
            out = convert_anm_file(anm_file, bone_hierarchy)
            if out:
                results.append(out)
        except Exception as e:
            slogger.warning(f"Failed to convert {os.path.basename(anm_file)}: {type(e).__name__}: {e}",
                            source='anm_to_gltf')
    slogger.info(f"Converted {len(results)} ANM file(s) to .glb", source='anm_to_gltf')
    return results


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python anm_to_gltf.py <character_directory>")
        print("  Converts all .anm files in <character_directory>/anm/ to .glb")
        sys.exit(1)
    results = convert_anm_directory(sys.argv[1])
    print(f"Converted {len(results)} file(s)")
