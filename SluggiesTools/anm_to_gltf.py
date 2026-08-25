"""Convert raw .anm binary files to .glb (glTF binary) using skeleton data from .sluggie JSON."""

import struct
import json
import os
import sys
import glob

import numpy as np
from pygltflib import (
    GLTF2, Buffer, BufferView, Accessor, Animation, AnimationChannel,
    AnimationChannelTarget, AnimationSampler, Node, Skin, Scene,
    FLOAT, VEC3, VEC4, SCALAR, ARRAY_BUFFER,
)

ANM_MAGICS = (0x01321AFD, 0x013240DB, 0x01324210)
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


def quantized_component_size(quantize_info):
    fmt_nibble = quantize_info >> 4
    return 2 if fmt_nibble in (0, 3) else 4


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
            quantize_info, anm_type_raw, interp_type, _replace = struct.unpack_from('>BBBB', data, t_off + 12)
            anm_type = anm_type_raw & 0x1F

            active_channels = []
            for bit in [3, 1, 0]:
                if anm_type & (1 << bit):
                    active_channels.append(bit)

            keyframes = []
            for ki in range(kf_cnt):
                kf_off = kf_arr_ptr + ki * 12
                time_val = struct.unpack_from('>f', data, kf_off)[0]
                setting_bank_ptr = struct.unpack_from('>I', data, kf_off + 4)[0]

                settings = {}
                bank_pos = setting_bank_ptr
                for ch in active_channels:
                    if ch == 3:
                        qi = 0x3E
                        vals, bank_pos = read_quantized(data, bank_pos, 1, 4, qi)
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


def sluggie_to_gltf_quat(q):
    """Convert [-w, x, y, z] to glTF [x, y, z, w]."""
    neg_w, x, y, z = q
    w = -neg_w
    if w < 0:
        return [-x, -y, -z, -w]
    return [x, y, z, w]


def build_gltf(sequences, bone_hierarchy):
    gltf = GLTF2()
    gltf.scene = 0
    gltf.scenes = [Scene(nodes=[])]
    gltf.buffers = [Buffer(byteLength=0)]

    bin_data = bytearray()

    bone_id_to_node_idx = {}
    track_id_to_node_idx = {}
    bone_id_to_bone = {}

    for bone in bone_hierarchy:
        bone_id_to_bone[bone['BoneId']] = bone

    root_bones = []
    for bone in bone_hierarchy:
        node = Node(name=f"bone_{bone['BoneId']}")
        t = bone.get('Translation', [0, 0, 0])
        s = bone.get('Scale', [1, 1, 1])
        q = bone.get('Quaternion', [-1, 0, 0, 0])
        node.translation = list(t)
        node.scale = list(s)
        node.rotation = sluggie_to_gltf_quat(q)
        node_idx = len(gltf.nodes)
        gltf.nodes.append(node)
        bone_id_to_node_idx[bone['BoneId']] = node_idx
        tid = bone.get('TrackId', 0xFFFF)
        if tid != 0xFFFF:
            track_id_to_node_idx[tid] = node_idx
        if bone['ParentBoneId'] is None:
            root_bones.append(node_idx)

    for bone in bone_hierarchy:
        parent_id = bone['ParentBoneId']
        if parent_id is not None and parent_id in bone_id_to_node_idx:
            parent_node = gltf.nodes[bone_id_to_node_idx[parent_id]]
            child_idx = bone_id_to_node_idx[bone['BoneId']]
            if parent_node.children is None:
                parent_node.children = []
            parent_node.children.append(child_idx)

    for ri in root_bones:
        gltf.scenes[0].nodes.append(ri)

    joint_list = [bone_id_to_node_idx[b['BoneId']] for b in bone_hierarchy]

    armature_node = Node(name="Armature")
    armature_idx = len(gltf.nodes)
    gltf.nodes.append(armature_node)
    armature_node.children = list(root_bones)

    for ri in root_bones:
        if ri in gltf.scenes[0].nodes:
            gltf.scenes[0].nodes.remove(ri)
    gltf.scenes[0].nodes.append(armature_idx)

    ibm_data = bytearray()
    for _ in bone_hierarchy:
        ibm_data.extend(struct.pack('<16f',
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1))

    ibm_offset = len(bin_data)
    bin_data.extend(ibm_data)
    ibm_bv = BufferView(buffer=0, byteOffset=ibm_offset, byteLength=len(ibm_data))
    ibm_bv_idx = len(gltf.bufferViews)
    gltf.bufferViews.append(ibm_bv)
    ibm_acc = Accessor(
        bufferView=ibm_bv_idx, byteOffset=0, componentType=FLOAT,
        count=len(bone_hierarchy), type='MAT4')
    ibm_acc_idx = len(gltf.accessors)
    gltf.accessors.append(ibm_acc)

    skin = Skin(
        joints=joint_list,
        skeleton=armature_idx,
        inverseBindMatrices=ibm_acc_idx,
        name="Armature")
    gltf.skins = [skin]
    armature_node.skin = 0

    def add_accessor(values, acc_type, component_type=FLOAT):
        flat = []
        for v in values:
            if isinstance(v, (list, tuple)):
                flat.extend(v)
            else:
                flat.append(v)
        raw = struct.pack(f'<{len(flat)}f', *flat)
        while len(bin_data) % 4:
            bin_data.append(0)
        offset = len(bin_data)
        bin_data.extend(raw)
        bv = BufferView(buffer=0, byteOffset=offset, byteLength=len(raw))
        bv_idx = len(gltf.bufferViews)
        gltf.bufferViews.append(bv)
        acc = Accessor(
            bufferView=bv_idx, byteOffset=0, componentType=component_type,
            count=len(values), type=acc_type)
        if acc_type == SCALAR:
            acc.min = [min(flat)]
            acc.max = [max(flat)]
        acc_idx = len(gltf.accessors)
        gltf.accessors.append(acc)
        return acc_idx

    for seq_i, sequence in enumerate(sequences):
        anim = Animation(name=f"sequence_{seq_i}", channels=[], samplers=[])

        for track in sequence['tracks']:
            tid = track['track_id']
            if tid not in track_id_to_node_idx:
                continue
            node_idx = track_id_to_node_idx[tid]
            kfs = track['keyframes']
            if not kfs:
                continue

            times = [kf['time'] / FRAME_RATE for kf in kfs]
            active = track['active_channels']

            if 3 in active:
                quats = []
                for kf in kfs:
                    q = kf['settings'][3]
                    quats.append(sluggie_to_gltf_quat(q))
                time_acc = add_accessor(times, SCALAR)
                val_acc = add_accessor(quats, VEC4)
                sampler_idx = len(anim.samplers)
                anim.samplers.append(AnimationSampler(input=time_acc, output=val_acc, interpolation="LINEAR"))
                anim.channels.append(AnimationChannel(
                    sampler=sampler_idx,
                    target=AnimationChannelTarget(node=node_idx, path='rotation')))

            if 0 in active:
                trans = [kf['settings'][0] for kf in kfs]
                time_acc = add_accessor(times, SCALAR)
                val_acc = add_accessor(trans, VEC3)
                sampler_idx = len(anim.samplers)
                anim.samplers.append(AnimationSampler(input=time_acc, output=val_acc, interpolation="LINEAR"))
                anim.channels.append(AnimationChannel(
                    sampler=sampler_idx,
                    target=AnimationChannelTarget(node=node_idx, path='translation')))

            if 1 in active:
                scales = [kf['settings'][1] for kf in kfs]
                time_acc = add_accessor(times, SCALAR)
                val_acc = add_accessor(scales, VEC3)
                sampler_idx = len(anim.samplers)
                anim.samplers.append(AnimationSampler(input=time_acc, output=val_acc, interpolation="LINEAR"))
                anim.channels.append(AnimationChannel(
                    sampler=sampler_idx,
                    target=AnimationChannelTarget(node=node_idx, path='scale')))

        if anim.channels:
            gltf.animations.append(anim)

    gltf.buffers[0].byteLength = len(bin_data)
    gltf.set_binary_blob(bytes(bin_data))
    return gltf


def find_sluggie(char_dir):
    pattern = os.path.join(char_dir, '**', '*.sluggie')
    candidates = glob.glob(pattern, recursive=True)
    best = None
    best_bones = 0
    for path in candidates:
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            bh = data.get('SluggiesModel', {}).get('BoneHierarchy')
            if bh and len(bh) > best_bones:
                best_bones = len(bh)
                best = path
        except (json.JSONDecodeError, KeyError):
            continue
    return best


def convert_anm_file(anm_path, bone_hierarchy):
    with open(anm_path, 'rb') as f:
        data = f.read()
    sequences = parse_anm(data)
    if not sequences:
        return None
    gltf = build_gltf(sequences, bone_hierarchy)
    out_path = os.path.splitext(anm_path)[0] + '.glb'
    gltf.save(out_path)
    return out_path


def convert_anm_directory(char_dir):
    anm_dir = os.path.join(char_dir, 'anm')
    if not os.path.isdir(anm_dir):
        return []

    sluggie_path = find_sluggie(char_dir)
    if not sluggie_path:
        print(f"No .sluggie file found in {char_dir}, skipping glTF conversion")
        return []

    with open(sluggie_path, 'r') as f:
        sluggie = json.load(f)
    bone_hierarchy = sluggie['SluggiesModel']['BoneHierarchy']
    if not bone_hierarchy:
        print(f"No BoneHierarchy in {sluggie_path}, skipping glTF conversion")
        return []

    results = []
    for anm_file in sorted(glob.glob(os.path.join(anm_dir, '*.anm'))):
        try:
            out = convert_anm_file(anm_file, bone_hierarchy)
            if out:
                results.append(out)
                print(f"  Converted {os.path.basename(anm_file)} -> {os.path.basename(out)}")
        except Exception as e:
            print(f"  Failed to convert {os.path.basename(anm_file)}: {e}")
    return results


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python anm_to_gltf.py <character_directory>")
        print("  Converts all .anm files in <character_directory>/anm/ to .glb")
        sys.exit(1)
    char_dir = sys.argv[1]
    results = convert_anm_directory(char_dir)
    print(f"\nConverted {len(results)} file(s)")
