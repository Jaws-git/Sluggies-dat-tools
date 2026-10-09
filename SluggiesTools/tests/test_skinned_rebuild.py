"""SkinnedRebuild: whole rebuild of the skinned submesh 0 from arbitrary
geometry and weights (PLAN_ModelReplacements.md Milestone 4), on the
synthetic donor."""
import base64
import pathlib
import struct
import sys
import unittest
from unittest import mock

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT_DIR / 'SluggiesTools'
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
ADDON_DIR = ROOT_DIR / 'BlenderAddonSrc'
for import_path in (ROOT_DIR, TOOLS_DIR, HAMMERSPACE_DIR, ADDON_DIR, pathlib.Path(__file__).resolve().parent):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import ExportMode  # noqa: E402
import FacialPoseRebuild as fpr  # noqa: E402
import HammerspaceMain as main  # noqa: E402
import SkinnedRebuild as sr  # noqa: E402
import start  # noqa: E402
import synthetic_donor  # noqa: E402
from binfmt import SKN_MAX_SOURCE_BYTES, decode_field  # noqa: E402
from drawlist import decodeDrawList  # noqa: E402

STRIDE = synthetic_donor.SKINNED_VERTEX_STRIDE          # 12
BONE_A = synthetic_donor.SKINNED_BONE                     # 3
BONE_B = synthetic_donor.SKN_ACC_BONE                     # 6
BONE_C = 7                                                # added to the donor skin by _with_third_bone
SURFACE = f'sm0_ds{synthetic_donor.SURFACE_STATE_INDEX}'  # sm0_ds5
NEW_KEY = 'sm0_new0'
N = synthetic_donor.SKINNED_VERTEX_COUNT                  # 7


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode('ascii')


def _u16s(values) -> str:
    return _b64(struct.pack(f'>{len(values)}H', *values))


def _influences(weights) -> str:
    """*weights*: per vertex ``[(bone, weight), ...]``."""
    return _b64(b''.join(
        struct.pack('>HHf', vertex, bone, weight)
        for vertex, influences in enumerate(weights)
        for bone, weight in influences
    ))


def _records(vertex_count: int) -> bytes:
    """Distinct position+normal records so slot contents can be traced."""
    return struct.pack(
        f'>{vertex_count * 6}h',
        *(value for vertex in range(vertex_count)
          for value in (vertex * 10 + 1, vertex * 10 + 2, vertex * 10 + 3, 0, 0, 1000)),
    )


def _fan(vertex_count: int) -> list[tuple[int, int, int]]:
    return [(0, corner, corner + 1) for corner in range(1, vertex_count - 1)]


def _rebuild(model: dict, weights, vertex_count: int = N) -> dict:
    """A SkinnedRebuild with *vertex_count* fan vertices and one UV per vertex."""
    sub = model['Submeshes'][0]
    faces = _fan(vertex_count)
    flat = [index for face in faces for index in face]
    uvs = struct.pack(f'>{vertex_count * 2}h', *(value for vertex in range(vertex_count) for value in (vertex, vertex * 2)))
    return {
        'VertexBufferData': _b64(_records(vertex_count)),
        'Influences': _influences(weights),
        'UVChannels': [
            {'UVChannelIndex': uv['UVChannelIndex'], 'UVChannelData': _b64(uvs), 'UVFacesData': _u16s(flat)}
            for uv in sub['UVChannels']
        ],
        'FacesCount': len(faces),
        'FacesData': _u16s(flat),
        'FaceSurfaceTable': [SURFACE],
        'FaceSurfaceIndices': _u16s([0] * len(faces)),
        'Reason': ['topology'],
    }


def _with_third_bone(data: dict) -> None:
    """Give the donor skin a third bone (an SKAcc entry on BONE_C) so a
    three-bone vertex is legal. Only the .sluggie dict is read for that."""
    data['SluggiesModel']['SkinData']['SKAccs'].append({
        'BoneIndex': BONE_C, 'VertexCnt': 1,
        'BindPoseData': _b64(bytes(STRIDE)),
        'DestIndexData': _b64(struct.pack('>H', 0)),
        'WeightData': _b64(bytes([1])),
        'GplDestArrValue': 0,
    })


# --- facial fixture (Milestone 4.7) ------------------------------------------

FACIAL_POSES = 3                      # pose zero + two expressions
BODY_FACIAL_ENTRIES = (1, 3, 4)       # donor body slots object 0 maps
HEAD_FACIAL_ENTRIES = (0, 1)          # submesh 1 vertices object 1 maps


def _facial_section(pose_count: int = FACIAL_POSES) -> tuple[bytes, list]:
    """A decodable ptr7 section: object 0 on the body (interleaved 6 x int16
    position records plus one auxiliary attribute on submesh 1), object 1 on
    the rigid head (3 x int16 positions). Returns ``(bytes, objects)``."""
    header = (struct.pack('>HHHHIII', pose_count, 2, 2, 0, 0x34, 0x14, 0)
              + bytes([0, 0, 1, 0x62]) + bytes(12)      # body positions, 6 x int16
              + bytes([0, 1, 0, 0x61]) + bytes(12))     # SKAcc entry 1 supplement, 6 x int8
    records = _records(N)
    body_pose_zero = b''.join(records[v * STRIDE:(v + 1) * STRIDE] for v in BODY_FACIAL_ENTRIES)
    body_deltas = [
        struct.pack(f'>{6 * len(BODY_FACIAL_ENTRIES)}h', *(pose * 100 + k for k in range(6 * len(BODY_FACIAL_ENTRIES))))
        for pose in range(1, pose_count)
    ]
    body = fpr.FacialObject(pose_count, [
        fpr.FacialAttribute(bytes([0, 1, 6, 2]), fpr.runs_from_indices(BODY_FACIAL_ENTRIES), [body_pose_zero] + body_deltas),
        fpr.FacialAttribute(bytes([1, 0, 6, 1]), fpr.runs_from_indices([0]), [bytes(range(6))] * pose_count),   # SKAcc entry 1
    ])
    head = fpr.FacialObject(pose_count, [
        fpr.FacialAttribute(bytes([1, 1, 3, 2]), fpr.runs_from_indices(HEAD_FACIAL_ENTRIES),
                            [struct.pack('>6h', *([pose] * 6)) for pose in range(pose_count)]),
    ])
    objects = [body, head]
    return fpr.serialize_section(header, objects), objects


def _facial_data_from_section(section: bytes) -> dict:
    """``FacialPoseData`` as export.py stores it, read back from *section*."""
    pose_max, object_count, type_count, _pad, table = struct.unpack_from('>HHHHI', section, 0)
    objects = []
    for object_index in range(object_count):
        pose_count, attribute_count, record_size, data_offset = struct.unpack_from('>HHII', section, table + object_index * 12)
        record_offsets = [data_offset + k * record_size for k in range(attribute_count)]
        run_offsets = [struct.unpack_from('>I', section, offset + 8)[0] for offset in record_offsets]
        all_pose_offsets = [
            struct.unpack_from('>I', section, offset + 0x0C + p * 4)[0]
            for offset in record_offsets for p in range(pose_count)
        ]
        attributes = []
        for k, offset in enumerate(record_offsets):
            entry_count = struct.unpack_from('>I', section, offset)[0]
            fmt = section[offset + 4:offset + 8]
            run_end = run_offsets[k + 1] if k + 1 < attribute_count else min(all_pose_offsets)
            pose_offsets = [struct.unpack_from('>I', section, offset + 0x0C + p * 4)[0] for p in range(pose_count)]
            size = entry_count * fmt[2] * fmt[3]
            attributes.append({
                'RecordOffset': hex(offset), 'EntryCount': entry_count, 'FormatData': _b64(fmt),
                'SubmeshIndex': fmt[0], 'AttributeKind': fmt[1], 'ComponentCount': fmt[2], 'ComponentSize': fmt[3],
                'RunListData': _b64(section[run_offsets[k]:run_end]),
                'Runs': [{'FirstVertex': f, 'VertexCount': c} for f, c in struct.iter_unpack('>HH', section[run_offsets[k]:run_end])],
                'PoseData': [_b64(section[p:p + size]) for p in pose_offsets],
            })
        position = next(a for a in attributes if a['AttributeKind'] == 1)
        objects.append({
            'ObjectIndex': object_index, 'PoseCount': pose_count, 'AttributeCount': attribute_count,
            'SubmeshIndex': position['SubmeshIndex'], 'Position': position,
            'Normal': next((a for a in attributes if a['AttributeKind'] == 2), None),
            'AuxiliaryAttributes': [a for a in attributes if a['AttributeKind'] not in (1, 2)],
        })
    return {
        'SectionLength': len(section), 'SectionData': _b64(section), 'HeaderData': _b64(section[:table]),
        'PoseCount': pose_max, 'AttributeTypeCount': type_count, 'ObjectCount': object_count, 'Objects': objects,
    }


def _facial_poses(vertices, pose_count: int = FACIAL_POSES, deltas=None) -> list:
    """A ``FacialPoses`` entry for object 0 mapping *vertices*; *deltas* is
    per pose a list of 6-tuples per vertex (default: distinct values)."""
    if deltas is None:
        deltas = [[tuple(pose * 1000 + vertex * 10 + c for c in range(6)) for vertex in vertices] for pose in range(1, pose_count)]
    return [{
        'ObjectIndex': 0, 'Vertices': _u16s(vertices),
        'PoseDeltas': [_b64(struct.pack(f'>{6 * len(rows)}h', *(v for row in rows for v in row))) for rows in deltas],
    }]


def _parse_facial_runs(section: bytes, object_index: int) -> tuple[list, list]:
    """``(slots, pose arrays)`` of *object_index*'s position attribute."""
    table = struct.unpack_from('>I', section, 8)[0]
    pose_count, attribute_count, record_size, data_offset = struct.unpack_from('>HHII', section, table + object_index * 12)
    run_offset = struct.unpack_from('>I', section, data_offset + 8)[0]
    pose_offsets = struct.unpack_from(f'>{pose_count}I', section, data_offset + 0x0C)
    entry_count = struct.unpack_from('>I', section, data_offset)[0]
    run_end = struct.unpack_from('>I', section, data_offset + record_size + 8)[0] if attribute_count > 1 else min(pose_offsets)
    slots = [s for f, c in struct.iter_unpack('>HH', section[run_offset:run_end]) for s in range(f, f + c)]
    stride = section[data_offset + 6] * section[data_offset + 7]
    return slots, [section[p:p + entry_count * stride] for p in pose_offsets]


# --- block readers ----------------------------------------------------------

def _gpl_section(block: bytes) -> bytes:
    gpl, act = struct.unpack_from('>II', block, 4)
    return block[gpl:act]


def _skn_section(block: bytes) -> bytes:
    skn = struct.unpack_from('>I', block, 0x10)[0]
    return block[skn:]


def _descriptors(gpl: bytes) -> list[tuple[int, int]]:
    count, pointer = struct.unpack_from('>II', gpl, 0x0C)
    return [struct.unpack_from('>II', gpl, pointer + 8 * index) for index in range(count)]


def _blob(gpl: bytes, index: int) -> dict:
    start, name_ptr = _descriptors(gpl)[index]
    pos_h, col_h, uv_h, nor_h, dsp_h = struct.unpack_from('>5I', gpl, start)
    pos_ptr, pos_count, pos_q, pos_cc = struct.unpack_from('>IHBB', gpl, start + pos_h)
    normal = struct.unpack_from('>IHBB', gpl, start + nor_h) if nor_h else None
    ds_ptr, n_ds = struct.unpack_from('>IH', gpl, start + dsp_h + 4)
    records, lists = [], []
    for k in range(n_ds):
        record = start + ds_ptr + k * 0x10
        records.append(gpl[record:record + 8])
        pl_ptr, pl_size = struct.unpack_from('>II', gpl, record + 8)
        lists.append(gpl[start + pl_ptr:start + pl_ptr + pl_size] if pl_size else b'')
    return {
        'start': start,
        'name': gpl[name_ptr:gpl.index(b'\x00', name_ptr)],
        'pos_offset': start + pos_ptr,
        'pos_count': pos_count,
        'pos_format': (pos_q, pos_cc),
        'positions': gpl[start + pos_ptr:start + pos_ptr + pos_count * STRIDE],
        'normal': normal,
        'normal_offset': start + normal[0] if normal else None,
        'records': records,
        'lists': lists,
    }


def _record_faces(blob: dict, k: int) -> list[tuple[int, int, int]]:
    setting = None
    for record in blob['records'][:k + 1]:
        if record[0] == 3:
            setting = int.from_bytes(record[4:8], 'big')
    faces = decodeDrawList(blob['lists'][k], main._custom_submesh_type3_descriptors(setting))
    return [tuple(vertex['position'] for vertex in face) for face in faces]


def _skn_entries(skn: bytes) -> dict:
    n1, n2, na = struct.unpack_from('>3H', skn, 0)
    sk1_ptr, sk2_ptr, acc_ptr, memclr, memclr_size, flush_ptr, flush_count = struct.unpack_from('>7I', skn, 8)
    sk1 = [
        dict(src=struct.unpack_from('>I', skn, p + 0x30)[0], gva=struct.unpack_from('>I', skn, p + 0x34)[0],
             bone=struct.unpack_from('>H', skn, p + 0x38)[0], count=struct.unpack_from('>H', skn, p + 0x3A)[0],
             voff=skn[p + 0x3C])
        for p in (sk1_ptr + i * 0x40 for i in range(n1))
    ]
    sk2 = [
        dict(src=struct.unpack_from('>I', skn, p + 0x60)[0], wt=struct.unpack_from('>I', skn, p + 0x64)[0],
             gva=struct.unpack_from('>I', skn, p + 0x68)[0], bones=struct.unpack_from('>HH', skn, p + 0x6C),
             count=struct.unpack_from('>H', skn, p + 0x70)[0], voff=skn[p + 0x72])
        for p in (sk2_ptr + i * 0x74 for i in range(n2))
    ]
    acc = [
        dict(src=struct.unpack_from('>I', skn, p + 0x30)[0], dst=struct.unpack_from('>I', skn, p + 0x34)[0],
             gda=struct.unpack_from('>I', skn, p + 0x38)[0], wt=struct.unpack_from('>I', skn, p + 0x3C)[0],
             bone=struct.unpack_from('>H', skn, p + 0x40)[0], count=struct.unpack_from('>H', skn, p + 0x42)[0])
        for p in (acc_ptr + i * 0x44 for i in range(na))
    ]
    for entry in acc:
        entry['dests'] = struct.unpack_from(f'>{entry["count"]}H', skn, entry['dst'])
        entry['weights'] = skn[entry['wt']:entry['wt'] + entry['count']]
    for entry in sk2:
        entry['weights'] = skn[entry['wt']:entry['wt'] + 2 * entry['count']]
    return dict(sk1=sk1, sk2=sk2, acc=acc, memclr=(memclr, memclr_size), quantize=skn[6],
                flush=struct.unpack_from(f'>{flush_count}H', skn, flush_ptr) if flush_count else ())


# --- layout ---------------------------------------------------------------------

class QuantizeWeightsTests(unittest.TestCase):
    def test_units_sum_to_256_largest_remainder(self):
        self.assertEqual(sr.quantize_weights([(3, 0.5), (6, 0.5)]), [(3, 128), (6, 128)])
        self.assertEqual(sr.quantize_weights([(3, 2.0), (6, 1.0)]), [(3, 171), (6, 85)])
        self.assertEqual(sum(units for _bone, units in sr.quantize_weights([(1, 0.3), (2, 0.3), (3, 0.4)])), 256)

    def test_merges_repeated_bones_and_drops_zero_and_negative(self):
        self.assertEqual(sr.quantize_weights([(3, 0.5), (3, 0.5), (6, 0.0), (9, -1.0)]), [(3, 256)])

    def test_tiny_influence_quantizing_to_zero_is_dropped(self):
        self.assertEqual(sr.quantize_weights([(3, 1.0), (6, 0.0001)]), [(3, 256)])

    def test_no_positive_weight_is_an_error(self):
        with self.assertRaises(ValueError):
            sr.quantize_weights([(3, 0.0)])


class LayoutSkinTests(unittest.TestCase):
    def test_one_bone_gives_one_sk1_on_line_zero(self):
        layout = sr.layout_skin([[(3, 256)]] * 5, STRIDE)
        self.assertEqual(len(layout.direct), 1)
        entry = layout.direct[0]
        self.assertEqual((entry.kind, entry.bones, entry.dest, entry.vertex_offset), ('SK1', (3,), 0, 0))
        self.assertEqual([slot for slot, _vertex in entry.slots], [0, 1, 2, 3, 4])
        self.assertEqual(layout.slot_of_vertex, [0, 1, 2, 3, 4])
        self.assertEqual(layout.slot_count, 5)
        self.assertEqual(layout.write_back_end, 64)     # align32(5 * 12)
        self.assertEqual(layout.flush, [])
        self.assertEqual(layout.accumulations, [])

    def test_entries_start_on_fresh_cache_lines_in_key_order(self):
        units = [[(6, 256)]] * 3 + [[(3, 256)]] * 5 + [[(3, 100), (6, 156)]] * 3
        layout = sr.layout_skin(units, STRIDE)
        kinds = [(entry.kind, entry.bones, entry.dest, entry.vertex_offset) for entry in layout.direct]
        # SK1(3) first: slots 0-4 end at byte 60; SK1(6) starts on line 64 ->
        # first whole vertex at 72 (slot 6, offset 8); then SK2 (3,6).
        self.assertEqual(kinds[0], ('SK1', (3,), 0, 0))
        self.assertEqual(kinds[1], ('SK1', (6,), 64, 8))
        self.assertEqual(kinds[2][:2], ('SK2', (3, 6)))
        self.assertEqual(kinds[2][2] % 32, 0)
        for entry in layout.direct:
            self.assertEqual((entry.dest + entry.vertex_offset) % STRIDE, 0)
        # No two entries share a cache line.
        lines = []
        for entry in layout.direct:
            end = entry.dest + entry.vertex_offset + entry.vertex_count * STRIDE
            lines.append(set(range(entry.dest // 32, sr.align32(end) // 32)))
        for i, a in enumerate(lines):
            for b in lines[i + 1:]:
                self.assertFalse(a & b)
        self.assertEqual(layout.direct[2].weights, [(100, 156)] * 3)
        # Every vertex has exactly one slot and the slots are distinct.
        self.assertEqual(len(set(layout.slot_of_vertex)), len(units))

    def test_pair_weights_follow_the_sorted_bone_order(self):
        layout = sr.layout_skin([[(9, 200), (4, 56)]] * 3, STRIDE)
        self.assertEqual(layout.direct[0].bones, (4, 9))
        self.assertEqual(layout.direct[0].weights, [(56, 200)] * 3)

    def test_short_entry_is_padded_with_its_first_vertex(self):
        layout = sr.layout_skin([[(3, 256)], [(6, 256)], [(6, 256)]], STRIDE)
        sk1_3, sk1_6 = layout.direct
        self.assertEqual([vertex for _slot, vertex in sk1_3.slots], [0, 0, 0])
        self.assertEqual([vertex for _slot, vertex in sk1_6.slots], [1, 2, 1])
        # SK1(3) fills bytes 0-36; SK1(6) starts on line 64, first whole
        # vertex at 72 = slot 6. The first occurrence of a vertex owns it.
        self.assertEqual([slot for slot, _vertex in sk1_6.slots], [6, 7, 8])
        self.assertEqual(layout.slot_of_vertex, [0, 6, 7])
        self.assertEqual(layout.slot_count, 9)

    def test_long_entry_is_split_at_the_locked_cache_cap(self):
        count = 1500
        layout = sr.layout_skin([[(3, 256)]] * count, STRIDE)
        self.assertGreater(len(layout.direct), 1)
        for entry in layout.direct:
            self.assertLessEqual(entry.vertex_offset + entry.vertex_count * STRIDE, SKN_MAX_SOURCE_BYTES['SK1'])
            self.assertGreaterEqual(entry.vertex_count, 3)
        self.assertEqual(sorted(slot for entry in layout.direct for slot, _vertex in entry.slots),
                         sorted(layout.slot_of_vertex))

    def test_third_influence_becomes_an_accumulation_with_a_flush_line(self):
        layout = sr.layout_skin([[(3, 128), (6, 100), (7, 28)]] * 4, STRIDE)
        self.assertEqual([entry.kind for entry in layout.direct], ['SK2'])
        self.assertEqual(len(layout.accumulations), 1)
        acc = layout.accumulations[0]
        self.assertEqual(acc.bone, 7)
        self.assertEqual(acc.members, [(0, 0, 28), (1, 1, 28), (2, 2, 28), (3, 3, 28)])
        # Slots 0-3 span bytes 0-48: lines 0 and 1, first slots 0 and 3.
        self.assertEqual(layout.flush, [0, 3])

    def test_too_many_slots_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'uint16'):
            sr.layout_skin([[(b, 256)] for b in range(1, 22000)], STRIDE)

    def test_position_buffer_holds_each_vertex_in_its_slot(self):
        layout = sr.layout_skin([[(3, 256)], [(6, 256)], [(6, 256)]], STRIDE)
        records = _records(3)
        buffer = sr.build_position_buffer(layout, records)
        self.assertEqual(len(buffer), layout.slot_count * STRIDE)
        for vertex, slot in enumerate(layout.slot_of_vertex):
            self.assertEqual(buffer[slot * STRIDE:(slot + 1) * STRIDE], records[vertex * STRIDE:(vertex + 1) * STRIDE])
        self.assertEqual(buffer[1 * STRIDE:3 * STRIDE], records[:STRIDE] * 2)   # padding repeats vertex 0
        self.assertEqual(buffer[3 * STRIDE:6 * STRIDE], bytes(3 * STRIDE))        # gap slots stay zero
        self.assertEqual(buffer[8 * STRIDE:9 * STRIDE], records[STRIDE:2 * STRIDE])  # padding repeats vertex 1
        self.assertEqual(sr.entry_source(layout, layout.direct[1], buffer), records[STRIDE:] + records[STRIDE:2 * STRIDE])


# --- facial pose section (Milestone 4.7) ------------------------------------------

class FacialPoseRebuildTests(unittest.TestCase):
    def test_section_round_trips_through_decode_and_serialize(self):
        section, _objects = _facial_section()
        facial = _facial_data_from_section(section)
        self.assertIsNone(fpr.facial_data_error(facial))
        objects = fpr.decode_objects(facial, base64.b64decode)
        self.assertEqual([len(o.attributes) for o in objects], [2, 1])
        self.assertEqual(objects[0].attributes[0].entry_count, len(BODY_FACIAL_ENTRIES))
        self.assertEqual(fpr.serialize_section(base64.b64decode(facial['HeaderData']), objects), section)
        self.assertEqual(len(section) % 32, 0)

    def test_runs_from_indices_end_with_the_terminator(self):
        self.assertEqual(fpr.runs_from_indices([0, 1, 2, 7, 9, 10]), struct.pack('>8H', 0, 3, 7, 1, 9, 2, 0, 0))
        self.assertEqual(fpr.runs_from_indices([]), fpr.RUN_TERMINATOR)
        self.assertTrue(fpr.run_list_terminated(fpr.runs_from_indices([5])))
        self.assertFalse(fpr.run_list_terminated(struct.pack('>HH', 5, 1)))

    def test_strip_type_descriptors(self):
        header = struct.pack('>HHHHIII', 5, 2, 2, 0, 0x34, 0x14, 0) + bytes([0, 0, 1, 0x62]) + bytes(12) + bytes([0, 3, 0, 0x61]) + bytes(12)
        stripped = fpr.strip_type_descriptors(header, {0})
        self.assertEqual(len(stripped), 0x24)
        self.assertEqual(struct.unpack_from('>HHHHIII', stripped, 0), (5, 2, 1, 0, 0x24, 0x14, 0))
        self.assertEqual(stripped[0x14:0x18], bytes([0, 0, 1, 0x62]))
        self.assertEqual(fpr.strip_type_descriptors(header, set()), header)
        with self.assertRaisesRegex(ValueError, 'every facial type descriptor'):
            fpr.strip_type_descriptors(header, {0, 1})

    def test_body_object_is_remapped_onto_the_new_slots(self):
        _section, objects = _facial_section()
        slot_of_vertex = [6, 7, 8, 0, 1, 2, 3]            # vertices 0-2 on a later line
        records = _records(N)
        buffer = bytearray(9 * STRIDE)
        for vertex, slot in enumerate(slot_of_vertex):
            buffer[slot * STRIDE:(slot + 1) * STRIDE] = records[vertex * STRIDE:(vertex + 1) * STRIDE]
        deltas = [[(pose, vertex, 0, 0, 0, 0) for vertex in (1, 3, 4)] for pose in (1, 2)]
        poses = fpr.BodyPoses(0, [1, 3, 4], [struct.pack('>18h', *(v for row in rows for v in row)) for rows in deltas])
        rebuilt, warnings = fpr.rebuild_body_object(objects[0], poses, slot_of_vertex, bytes(buffer), STRIDE, 0)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(len(rebuilt.attributes), 1)              # the SKAcc supplement is dropped
        position = rebuilt.attributes[0]
        self.assertEqual(position.format_data, bytes([0, 1, 6, 2]))
        self.assertEqual(position.run_list, struct.pack('>6H', 0, 2, 7, 1, 0, 0))   # slots 0, 1 (vertices 3, 4) and 7 (vertex 1), terminator
        self.assertEqual(position.poses[0], records[3 * STRIDE:5 * STRIDE] + records[1 * STRIDE:2 * STRIDE])
        self.assertEqual(position.poses[1], struct.pack('>18h', 1, 3, 0, 0, 0, 0, 1, 4, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0))
        self.assertEqual(position.poses[2][:2], struct.pack('>h', 2))

    def test_skacc_supplement_attribute_is_dropped_with_a_warning(self):
        _section, objects = _facial_section()
        poses = fpr.BodyPoses(0, [0], [bytes(STRIDE)] * 2)
        rebuilt, warnings = fpr.rebuild_body_object(objects[0], poses, [0], _records(1), STRIDE, 0)
        self.assertEqual(len(warnings), 1)
        self.assertIn('SKAcc entry 1', warnings[0])
        self.assertEqual([a.kind for a in rebuilt.attributes], [1])

    def test_rebuild_errors(self):
        _section, objects = _facial_section()
        body = objects[0]
        with self.assertRaisesRegex(ValueError, '3 poses'):
            fpr.rebuild_body_object(body, fpr.BodyPoses(0, [0], [bytes(STRIDE)]), [0, 1], _records(2), STRIDE, 0)
        with self.assertRaisesRegex(ValueError, 'outside'):
            fpr.rebuild_body_object(body, fpr.BodyPoses(0, [5], [bytes(STRIDE)] * 2), [0, 1], _records(2), STRIDE, 0)
        with self.assertRaisesRegex(ValueError, 'twice'):
            fpr.rebuild_body_object(body, fpr.BodyPoses(0, [1, 1], [bytes(2 * STRIDE)] * 2), [0, 1], _records(2), STRIDE, 0)
        with self.assertRaisesRegex(ValueError, 'delta array is'):
            fpr.rebuild_body_object(body, fpr.BodyPoses(0, [1], [bytes(5)] * 2), [0, 1], _records(2), STRIDE, 0)
        self.assertIn('separate normal', fpr.body_object_error(
            fpr.FacialObject(2, [body.attributes[0], fpr.FacialAttribute(bytes([0, 2, 3, 2]), b'', [b'', b''])]), 0))
        self.assertIn('6 x int16', fpr.body_object_error(objects[1], 1))

    def test_rebuild_section_requires_an_entry_per_body_object(self):
        section, _objects = _facial_section()
        facial = _facial_data_from_section(section)
        with self.assertRaisesRegex(ValueError, 'no entry'):
            fpr.rebuild_section(facial, base64.b64decode, {0: fpr.body_rebuilder([], [0] * N, _records(N), STRIDE, 0)}, {0})
        rebuilt, warnings = fpr.rebuild_section(
            facial, base64.b64decode,
            {0: fpr.body_rebuilder([fpr.BodyPoses(0, [2], [bytes(STRIDE)] * 2)], list(range(N)), _records(N), STRIDE, 0)},
            {fpr.ACCUMULATION_KIND},
        )
        self.assertEqual(len(warnings), 1)                                     # the SKAcc supplement attribute
        self.assertEqual(_parse_facial_runs(rebuilt, 0)[0], [2])
        self.assertEqual(_parse_facial_runs(rebuilt, 1), _parse_facial_runs(section, 1))   # the head is untouched
        # The kind-0 type descriptor went with it: one descriptor, table right after it.
        self.assertEqual(struct.unpack_from('>HHHHI', rebuilt, 0), (FACIAL_POSES, 2, 1, 0, 0x24))
        self.assertEqual(rebuilt[0x14:0x18], bytes([0, 0, 1, 0x62]))

    # --- rigid targets (2026-10-10) ---

    def _head(self, with_normals=True):
        """The fixture's head object (submesh 1, 3 x int16 positions on
        entries 0-1), plus a 3 x int16 normal attribute on entries 0 and 2."""
        _section, objects = _facial_section()
        head = objects[1]
        if with_normals:
            head = fpr.FacialObject(head.pose_count, head.attributes + [
                fpr.FacialAttribute(bytes([1, 2, 3, 2]), fpr.runs_from_indices([0, 2]),
                                    [struct.pack('>6h', *([10 + pose] * 6)) for pose in range(head.pose_count)]),
            ])
        return head

    def test_rigid_object_is_remapped_onto_the_rebuilt_arrays(self):
        positions = struct.pack('>12h', *range(100, 112))        # 4 rebuilt vertices
        normals = struct.pack('>15h', *range(200, 215))          # 5 rebuilt normal entries
        poses = fpr.RigidPoses(
            1, [3, 1], [struct.pack('>6h', 1, 3, 0, 1, 1, 0), struct.pack('>6h', 2, 3, 0, 2, 1, 0)],
            normal_entries=[4], normal_deltas=[struct.pack('>3h', 7, 0, 0), struct.pack('>3h', 8, 0, 0)],
        )
        rebuilt, warnings = fpr.rebuild_rigid_object(self._head(), poses, positions, normals, 6, 1)
        self.assertEqual(warnings, [])
        position, normal = rebuilt.attributes
        self.assertEqual(position.format_data, bytes([1, 1, 3, 2]))
        self.assertEqual(position.run_list, struct.pack('>6H', 1, 1, 3, 1, 0, 0))
        self.assertEqual(position.poses[0], positions[6:12] + positions[18:24])
        self.assertEqual(position.poses[1], struct.pack('>6h', 1, 1, 0, 1, 3, 0))      # vertex 1 first, then 3
        self.assertEqual(position.poses[2], struct.pack('>6h', 2, 1, 0, 2, 3, 0))
        self.assertEqual(normal.format_data, bytes([1, 2, 3, 2]))
        self.assertEqual(normal.run_list, struct.pack('>4H', 4, 1, 0, 0))
        self.assertEqual(normal.poses, [normals[24:30], struct.pack('>3h', 7, 0, 0), struct.pack('>3h', 8, 0, 0)])

    def test_rigid_object_without_poses_is_neutralized(self):
        positions = struct.pack('>12h', *range(100, 112))
        normals = struct.pack('>15h', *range(200, 215))
        rebuilt, warnings = fpr.rebuild_rigid_object(self._head(), None, positions, normals, 6, 1)
        self.assertEqual(len(warnings), 2)
        self.assertIn('neutralized', warnings[0])
        for attribute, buffer in zip(rebuilt.attributes, (positions, normals)):
            self.assertEqual(attribute.run_list, struct.pack('>4H', 0, 1, 0, 0))
            self.assertEqual(attribute.poses, [buffer[:6], bytes(6), bytes(6)])
        # Poses without a normal list neutralize the normal attribute alone.
        rebuilt, warnings = fpr.rebuild_rigid_object(
            self._head(), fpr.RigidPoses(1, [2], [bytes(6)] * 2), positions, normals, 6, 1)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(rebuilt.attributes[0].run_list, struct.pack('>4H', 2, 1, 0, 0))
        self.assertEqual(rebuilt.attributes[1].run_list, struct.pack('>4H', 0, 1, 0, 0))

    def test_rigid_object_errors(self):
        positions = struct.pack('>12h', *range(100, 112))
        head = self._head()
        with self.assertRaisesRegex(ValueError, 'no normal array'):
            fpr.rebuild_rigid_object(head, None, positions, None, None, 1)
        with self.assertRaisesRegex(ValueError, 'positions: maps an entry twice'):
            fpr.rebuild_rigid_object(head, fpr.RigidPoses(1, [1, 1], [bytes(12)] * 2), positions, positions, 6, 1)
        with self.assertRaisesRegex(ValueError, 'outside the 4 rebuilt entries'):
            fpr.rebuild_rigid_object(head, fpr.RigidPoses(1, [4], [bytes(6)] * 2), positions, positions, 6, 1)
        with self.assertRaisesRegex(ValueError, 'normals: pose 1 delta array is 5 bytes'):
            fpr.rebuild_rigid_object(
                head, fpr.RigidPoses(1, [0], [bytes(6)] * 2, [0], [bytes(5)] * 2), positions, positions, 6, 1)
        with self.assertRaisesRegex(ValueError, 'normals: 3 delta arrays for 3 poses'):
            fpr.rebuild_rigid_object(head, fpr.RigidPoses(1, [0], [bytes(6)] * 2, [0], [bytes(6)] * 3), positions, positions, 6, 1)
        with self.assertRaisesRegex(ValueError, 'rebuilt array holds 8-byte'):
            fpr.rebuild_rigid_object(head, fpr.RigidPoses(1, [0], [bytes(6)] * 2, [0], [bytes(6)] * 2), positions, positions, 8, 1)
        _section, objects = _facial_section()
        self.assertIn('a rigid submesh stores 3 x int16', fpr.rigid_object_error(objects[0], 0))
        self.assertIsNone(fpr.rigid_object_error(head, 1))
        with_color = fpr.FacialObject(3, head.attributes + [fpr.FacialAttribute(bytes([1, 3, 3, 2]), b'', [b''] * 3)])
        self.assertIn('kind(s) [3]', fpr.rigid_object_error(with_color, 1))

    def test_rebuild_section_with_body_and_rigid_targets(self):
        section, _objects = _facial_section()
        facial = _facial_data_from_section(section)
        positions = struct.pack('>12h', *range(100, 112))
        rebuilders = {
            0: fpr.body_rebuilder([fpr.BodyPoses(0, [2], [bytes(STRIDE)] * 2)], list(range(N)), _records(N), STRIDE, 0),
            1: fpr.rigid_rebuilder([fpr.RigidPoses(1, [3], [bytes(6)] * 2)], positions, None, None, 1),
        }
        rebuilt, warnings = fpr.rebuild_section(facial, base64.b64decode, rebuilders, {fpr.ACCUMULATION_KIND})
        self.assertEqual(len(warnings), 1)
        self.assertEqual(_parse_facial_runs(rebuilt, 0)[0], [2])
        slots, poses = _parse_facial_runs(rebuilt, 1)
        self.assertEqual(slots, [3])
        self.assertEqual(poses[0], positions[18:24])
        # A rigid-only rebuild keeps the kind-0 descriptor and the body's supplement.
        rebuilt, warnings = fpr.rebuild_section(facial, base64.b64decode, {1: rebuilders[1]})
        self.assertEqual(warnings, [])
        self.assertEqual(struct.unpack_from('>HHH', rebuilt, 0), (FACIAL_POSES, 2, 2))
        self.assertEqual(_parse_facial_runs(rebuilt, 0), _parse_facial_runs(section, 0))
        with self.assertRaisesRegex(ValueError, 'facial object 1: addresses submesh 1 but FacialPoses has no entry'):
            fpr.rebuild_section(facial, base64.b64decode, {1: fpr.rigid_rebuilder([], positions, None, None, 1)})


# --- validator -------------------------------------------------------------------

class SkinnedRebuildValidatorTests(unittest.TestCase):
    def setUp(self):
        self.model = synthetic_donor.build_sluggie()['SluggiesModel']
        self.model['UseHammerspace'] = True
        self.sub = self.model['Submeshes'][0]
        self.sub['SkinnedRebuild'] = _rebuild(self.model, [[(BONE_A, 1.0)]] * N)

    def _refused(self, pattern: str):
        with self.assertRaisesRegex(ValueError, pattern):
            main._validate_skinned_rebuild(self.model)

    def test_identity_entry_passes(self):
        main._validate_skinned_rebuild(self.model)

    def test_requires_hammerspace(self):
        self.model['UseHammerspace'] = False
        self._refused('UseHammerspace')

    def test_only_submesh_0(self):
        self.model['Submeshes'][1]['SkinnedRebuild'] = self.sub.pop('SkinnedRebuild')
        self._refused('only supported on submesh 0')

    def test_rigid_submesh_is_refused(self):
        self.sub['VertexBuffer']['VertexBufferCompCount'] = 3
        self._refused('VertexBufferCompCount 6')

    def test_skin_quantize_must_match_the_position_format(self):
        self.model['SkinData']['QuantizeInfo'] = 0x05
        self._refused('does not match the position format')

    def test_in_place_fields_and_skin_edits_are_refused(self):
        self.sub['FacesDataEdited'] = self.sub['FacesData']
        self._refused('in-place edit fields')
        del self.sub['FacesDataEdited']
        self.model['SkinDataEdited'] = {'SK1s': []}
        self._refused('SkinDataEdited')
        del self.model['SkinDataEdited']
        self.sub['RigidRebuild'] = {'HostBoneId': 5}
        self._refused('RigidRebuild')

    def test_unknown_bone_is_refused(self):
        self.sub['SkinnedRebuild']['Influences'] = _influences([[(BONE_A, 1.0)]] * (N - 1) + [[(99, 1.0)]])
        self._refused(r"bone\(s\) \[99\]")

    def test_unweighted_and_bad_weights_are_refused(self):
        self.sub['SkinnedRebuild']['Influences'] = _influences([[(BONE_A, 1.0)]] * (N - 2) + [[], [(BONE_A, 0.0)]])
        self._refused('non-positive')
        self._refused(f'2 vertex\\(es\\) have no skin influence: {N - 2}, {N - 1}')

    def test_influence_vertex_out_of_range(self):
        self.sub['SkinnedRebuild']['Influences'] = _influences([[(BONE_A, 1.0)]] * (N + 1))
        self._refused(f'vertex {N}, outside')

    def test_standalone_normals_are_refused(self):
        self.sub['SkinnedRebuild']['NormalBufferData'] = 'AA=='
        self._refused('interleaved')

    def test_faces_and_surfaces_are_checked(self):
        self.sub['SkinnedRebuild']['FacesCount'] = 2
        self._refused('FacesData length')
        self.sub['SkinnedRebuild'] = _rebuild(self.model, [[(BONE_A, 1.0)]] * N)
        self.sub['SkinnedRebuild']['FaceSurfaceTable'] = ['sm0_ds3']
        self._refused('neither a drawing surface')

    def test_new_surface_key_must_name_submesh_0(self):
        rebuild = self.sub['SkinnedRebuild']
        rebuild['FaceSurfaceTable'] = [SURFACE, 'sm1_new0']
        rebuild['FaceSurfaceIndices'] = _u16s([1] * rebuild['FacesCount'])
        rebuild['NewSurfaces'] = [{
            'SurfaceKey': 'sm1_new0', 'TemplateSource': 'builtin:rigid_spec_v1',
            'TextureAssignment': {'DonorTextureIndex': 1},
        }]
        self._refused('sm0_new<K>')

    def test_colors_required_when_the_donor_lists_a_channel(self):
        self.sub['ColorChannels'] = [{'ColorChannelIndex': 0, 'ColorChannelCompCount': 4,
                                      'ColorChannelQuantizeInfo': 48, 'ColorChannelData': 'AAA=',
                                      'ColorFacesData': _u16s([0] * 15)}]
        self._refused('ColorChannelData')

    def test_facial_poses_are_checked_against_the_donor_objects(self):
        section, _objects = _facial_section()
        self.model['FacialPoseData'] = _facial_data_from_section(section)
        rebuild = self.sub['SkinnedRebuild']
        rebuild['FacialPoses'] = _facial_poses([1, 3, 4])
        main._validate_skinned_rebuild(self.model)
        rebuild['FacialPoses'] = []
        self._refused(r'lacks facial object\(s\) \[0\]')
        rebuild['FacialPoses'] = _facial_poses([3, 1])
        self._refused('strictly ascending')
        rebuild['FacialPoses'] = _facial_poses([1, N])
        self._refused(f'vertex {N} is outside')
        rebuild['FacialPoses'] = _facial_poses([1], pose_count=2)
        self._refused('PoseDeltas must hold 2 arrays')
        rebuild['FacialPoses'] = _facial_poses([1])
        rebuild['FacialPoses'][0]['PoseDeltas'][0] = 'AAA='
        self._refused(r'PoseDeltas\[0\] is 2 bytes')
        rebuild['FacialPoses'] = _facial_poses([1]) + [{'ObjectIndex': 1, 'Vertices': _u16s([0]), 'PoseDeltas': []}]
        self._refused('does not address submesh 0')
        rebuild['FacialPoses'] = _facial_poses([1]) * 2
        self._refused('twice')
        rebuild['FacialPoses'] = _facial_poses([1])
        del self.model['FacialPoseData']
        self._refused('no FacialPoseData')


# --- builder ---------------------------------------------------------------------

class SkinnedRebuildBuildTests(unittest.TestCase):
    def setUp(self):
        self.env = self.enterContext(synthetic_donor.donor_environment(_with_third_bone))
        self.data = self.env.reload()
        self.model = self.data['SluggiesModel']
        self.model['UseHammerspace'] = True
        self.donor_gpl = main.CloneGPL(self.env.model_offset, self.env.model_length)

    def _build(self, **modes):
        modes.setdefault('skn', 'build')
        result = main.BuildModelBlock(
            self.data, main.SectionModes(gpl='build', **modes), sluggie_path=self.env.sluggie_path,
        )
        self.assertTrue(result.validation_report['valid'], result.validation_report.get('errors'))
        return result, _gpl_section(result.block), _skn_entries(_skn_section(result.block))

    def test_one_bone_body_replaces_submesh_0_and_the_skn(self):
        self.model['Submeshes'][0]['SkinnedRebuild'] = _rebuild(self.model, [[(BONE_A, 1.0)]] * N)
        result, gpl, skn = self._build()

        donor_descriptors, new_descriptors = _descriptors(self.donor_gpl), _descriptors(gpl)
        self.assertNotEqual(new_descriptors[0], donor_descriptors[0])
        # The donor body blob led the blob region and is dropped: the head
        # blob moves up by the span (rounded down to 32), byte for byte, and
        # the rebuilt body goes in right after it.
        cut = (donor_descriptors[1][0] - donor_descriptors[0][0]) & ~31
        self.assertGreater(cut, 0)
        self.assertEqual(new_descriptors[1], (donor_descriptors[1][0] - cut, donor_descriptors[1][1] - cut))
        head_len = len(self.donor_gpl) - donor_descriptors[1][0]
        self.assertEqual(gpl[new_descriptors[1][0]:new_descriptors[1][0] + head_len],
                         self.donor_gpl[donor_descriptors[1][0]:])
        self.assertEqual(new_descriptors[0][0], (new_descriptors[1][0] + head_len + 31) & ~31)

        blob = _blob(gpl, 0)
        self.assertEqual(blob['name'], b'body')
        self.assertEqual(blob['pos_count'], N)
        self.assertEqual(blob['pos_format'], (synthetic_donor.SKINNED_QUANTIZE_INFO, 6))
        self.assertEqual(blob['pos_offset'] % 32, 0)
        self.assertEqual(blob['positions'], _records(N))        # slot order == vertex order here
        self.assertIsNone(blob['normal'])                       # the synthetic donor has no normal header
        self.assertEqual(_record_faces(blob, synthetic_donor.SURFACE_STATE_INDEX), _fan(N))
        self.assertEqual(blob['records'], _blob(self.donor_gpl, 0)['records'])

        self.assertEqual(len(skn['sk1']), 1)
        self.assertEqual(skn['sk2'], [])
        self.assertEqual(skn['acc'], [])
        entry = skn['sk1'][0]
        self.assertEqual((entry['bone'], entry['count'], entry['gva'], entry['voff']), (BONE_A, N, 0, 0))
        self.assertEqual(skn['memclr'], (0, 0))
        self.assertEqual(skn['flush'], ())
        self.assertEqual(skn['quantize'], synthetic_donor.SKINNED_QUANTIZE_INFO)
        self.assertEqual(result.section_modes.skn, 'build')

    def test_two_and_three_bone_vertices_build_sk2_and_skacc(self):
        weights = [[(BONE_A, 1.0)]] * 3 + [[(BONE_A, 0.5), (BONE_B, 0.5)]] * 3 + [[(BONE_A, 0.5), (BONE_B, 0.3), (BONE_C, 0.2)]]
        self.model['Submeshes'][0]['SkinnedRebuild'] = _rebuild(self.model, weights)
        _result, gpl, skn = self._build()
        blob = _blob(gpl, 0)
        self.assertEqual([e['bone'] for e in skn['sk1']], [BONE_A])
        # Every (3,6) vertex shares one SK2 entry: three at 128/128, then the
        # three-bone vertex at 128/77 whose third bone accumulates 51 units.
        self.assertEqual([(e['bones'], e['count']) for e in skn['sk2']], [((BONE_A, BONE_B), 4)])
        self.assertEqual(skn['sk2'][0]['weights'], bytes([128, 128] * 3 + [128, 77]))
        self.assertEqual([(e['bone'], e['count'], e['gda'], e['weights']) for e in skn['acc']], [(BONE_C, 1, 0, bytes([51]))])
        for entry in skn['sk1'] + skn['sk2']:
            self.assertEqual(entry['gva'] % 32, 0)
            self.assertEqual((entry['gva'] + entry['voff']) % STRIDE, 0)
        sk2 = skn['sk2'][0]
        acc_slot = skn['acc'][0]['dests'][0]
        self.assertEqual(acc_slot, (sk2['gva'] + sk2['voff']) // STRIDE + 3)
        self.assertEqual(skn['memclr'], (0, 0))
        self.assertEqual(len(skn['flush']), 1)
        self.assertEqual(blob['pos_count'], (sk2['gva'] + sk2['voff']) // STRIDE + 4)
        # The draw list indexes slots, not Blender vertices.
        faces = _record_faces(blob, synthetic_donor.SURFACE_STATE_INDEX)
        self.assertEqual(len(faces), N - 2)
        self.assertEqual(faces[0][0], 0)
        self.assertEqual(faces[-1][2], acc_slot)

    def test_more_than_256_slots_widen_the_position_index(self):
        count = 300
        self.model['Submeshes'][0]['SkinnedRebuild'] = _rebuild(self.model, [[(BONE_A, 1.0)]] * count, count)
        _result, gpl, skn = self._build()
        blob = _blob(gpl, 0)
        self.assertEqual(int.from_bytes(_blob(self.donor_gpl, 0)['records'][3][4:8], 'big'), 0x2808)
        # Positions and both UV channels (one coordinate per vertex) pass 256
        # entries, so all three indices widen to uint16.
        self.assertEqual(int.from_bytes(blob['records'][3][4:8], 'big'), 0x3C0C)
        self.assertEqual(blob['pos_count'], count)
        self.assertEqual(_record_faces(blob, synthetic_donor.SURFACE_STATE_INDEX), _fan(count))
        self.assertEqual(sum(e['count'] for e in skn['sk1']), count)

    def test_new_surface_appends_a_canonical_group(self):
        rebuild = _rebuild(self.model, [[(BONE_A, 1.0)]] * N)
        rebuild['FaceSurfaceTable'] = [SURFACE, NEW_KEY]
        rebuild['FaceSurfaceIndices'] = _u16s([0, 0, 1, 1, 1])
        rebuild['NewSurfaces'] = [{
            'SurfaceKey': NEW_KEY, 'MaterialName': 'belt', 'TemplateSource': 'builtin:rigid_spec_v1',
            'TextureAssignment': {'DonorTextureIndex': 1}, 'SpecularStrength': 50,
        }]
        self.model['Submeshes'][0]['SkinnedRebuild'] = rebuild
        _result, gpl, _skn = self._build()
        blob = _blob(gpl, 0)
        donor_records = _blob(self.donor_gpl, 0)['records']
        self.assertEqual(blob['records'][:len(donor_records)], donor_records)
        self.assertEqual([r[0] for r in blob['records'][len(donor_records):]], [1, 1, 4, 3, 6, 7])
        self.assertEqual(len(_record_faces(blob, synthetic_donor.SURFACE_STATE_INDEX)), 2)
        self.assertEqual(len(_record_faces(blob, len(blob['records']) - 1)), 3)
        self.assertEqual(blob['records'][-1][1], 50)

    def test_normal_header_points_into_the_interleaved_buffer(self):
        def add_normals(data):
            _with_third_bone(data)
            sub = data['SluggiesModel']['Submeshes'][0]
            sub['NormalBuffer'] = {
                'NormalDataPtrFieldOffset': '0x0', 'NormalCountFieldOffset': '0x0', 'NormalBufferOffset': '0x0',
                'NormalBufferLength': N * STRIDE, 'NormalBufferCompCount': 6,
                'NormalBufferQuantizeInfo': synthetic_donor.SKINNED_QUANTIZE_INFO, 'NormalAmbientPct': 0.0,
                'NormalBufferData': sub['VertexBuffer']['VertexBufferData'],
                'NormalFacesData': sub['FacesData'],
            }
        self.env = self.enterContext(synthetic_donor.donor_environment(add_normals))
        self.data = self.env.reload()
        self.model = self.data['SluggiesModel']
        self.model['UseHammerspace'] = True
        self.model['Submeshes'][0]['SkinnedRebuild'] = _rebuild(self.model, [[(BONE_A, 1.0)]] * N)
        _result, gpl, _skn = self._build()
        blob = _blob(gpl, 0)
        self.assertEqual(blob['normal_offset'], blob['pos_offset'] + 6)
        self.assertEqual(blob['normal'][1:], (N, synthetic_donor.SKINNED_QUANTIZE_INFO, 6))

    def test_facial_pointer_is_cleared_when_poses_address_submesh_0(self):
        self.model['FacialPoseData'] = {'Objects': [{'ObjectIndex': 0, 'SubmeshIndex': 0}]}
        section = struct.pack('>HHHHI', 5, 1, 1, 0, 0x0C) + bytes(0x14)
        self.model['TrailingSections'] = [{'HeaderFieldOffset': '0x18', 'OriginalPtr': '0x1000', 'Length': len(section), 'Data': _b64(section)}]
        self.model['Submeshes'][0]['SkinnedRebuild'] = _rebuild(self.model, [[(BONE_A, 1.0)]] * N)
        # The synthetic donor header has no ptr7; give it one so the header
        # builder places the section (the pointer is recomputed from it).
        donor_header = bytearray(main.CloneHEADER(self.env.model_offset))
        struct.pack_into('>I', donor_header, 0x18, 0x1000)
        with mock.patch.object(main, 'CloneHEADER', return_value=bytes(donor_header)):
            result, _gpl, _skn = self._build()
            self.assertEqual(struct.unpack_from('>I', result.block, 0x18)[0], 0)
            # Poses on another submesh are kept (the stub section itself is
            # not a decodable facial section, which is the only complaint).
            self.model['FacialPoseData'] = {'Objects': [{'ObjectIndex': 0, 'SubmeshIndex': 1}]}
            result = main.BuildModelBlock(
                self.data, main.SectionModes(gpl='build', skn='build'), sluggie_path=self.env.sluggie_path,
            )
            self.assertEqual([e for e in result.validation_report['errors'] if 'ptr7' not in e], [])
            ptr7 = struct.unpack_from('>I', result.block, 0x18)[0]
            self.assertNotEqual(ptr7, 0)
            self.assertEqual(result.block[ptr7:ptr7 + len(section)], section)

    def test_facial_poses_rebuild_the_section_and_shift_the_following_pointer(self):
        section, _objects = _facial_section()
        ptr8 = bytes(range(64))
        self.model['FacialPoseData'] = _facial_data_from_section(section)
        self.model['TrailingSections'] = [
            {'HeaderFieldOffset': '0x18', 'OriginalPtr': '0x1000', 'Length': len(section), 'Data': _b64(section)},
            {'HeaderFieldOffset': '0x1c', 'OriginalPtr': hex(0x1000 + len(section)), 'Length': len(ptr8), 'Data': _b64(ptr8)},
        ]
        # Vertices 0-2 on BONE_B land on a later cache line than 3-6 on
        # BONE_A: slot_of_vertex is [6, 7, 8, 0, 1, 2, 3].
        rebuild = _rebuild(self.model, [[(BONE_B, 1.0)]] * 3 + [[(BONE_A, 1.0)]] * 4)
        rebuild['FacialPoses'] = _facial_poses([1, 3, 4])
        self.model['Submeshes'][0]['SkinnedRebuild'] = rebuild
        donor_header = bytearray(main.CloneHEADER(self.env.model_offset))
        struct.pack_into('>II', donor_header, 0x18, 0x1000, 0x1000 + len(section))
        with mock.patch.object(main, 'CloneHEADER', return_value=bytes(donor_header)):
            result, gpl, _skn = self._build()
        block = result.block
        ptr7_new, ptr8_new = struct.unpack_from('>II', block, 0x18)
        self.assertNotEqual(ptr7_new, 0)
        self.assertEqual(ptr7_new % 32, 0)
        rebuilt = block[ptr7_new:ptr8_new]
        self.assertEqual(block[ptr8_new:ptr8_new + len(ptr8)], ptr8)
        self.assertEqual(len(rebuilt) % 32, 0)
        slots, poses = _parse_facial_runs(rebuilt, 0)
        self.assertEqual(slots, [0, 1, 7])
        table = struct.unpack_from('>I', rebuilt, 8)[0]
        _pc, _ac, _rs, data = struct.unpack_from('>HHII', rebuilt, table)
        run = struct.unpack_from('>I', rebuilt, data + 8)[0]
        self.assertEqual(rebuilt[run:run + 12], struct.pack('>6H', 0, 2, 7, 1, 0, 0))   # terminated
        blob = _blob(gpl, 0)
        self.assertEqual(poses[0], b''.join(blob['positions'][s * STRIDE:(s + 1) * STRIDE] for s in slots))
        # Deltas follow their vertex: vertex 3 -> slot 0, 4 -> 1, 1 -> 7.
        self.assertEqual(struct.unpack('>18h', poses[1])[::6], (1030, 1040, 1010))
        self.assertEqual(_parse_facial_runs(rebuilt, 1), _parse_facial_runs(section, 1))
        self.assertEqual([e for e in result.validation_report['errors'] if 'ptr7' in e], [])

    def test_facial_poses_need_a_trailing_section_to_replace(self):
        section, _objects = _facial_section()
        self.model['FacialPoseData'] = _facial_data_from_section(section)
        rebuild = _rebuild(self.model, [[(BONE_A, 1.0)]] * N)
        rebuild['FacialPoses'] = _facial_poses([1])
        self.model['Submeshes'][0]['SkinnedRebuild'] = rebuild
        with self.assertRaisesRegex(ValueError, 'TrailingSections'):
            main.BuildModelBlock(self.data, main.SectionModes(gpl='build', skn='build'), sluggie_path=self.env.sluggie_path)

    def test_header_builder_lays_trailing_sections_out_by_length(self):
        original = bytearray(0x20)
        struct.pack_into('>IIII', original, 0x04, 0x20, 0, 0, 0x100)
        struct.pack_into('>II', original, 0x14, 0x200, 0x220)      # donor: ptr6 at 0x200, ptr8 0x20 later
        sections = [
            main.TrailingSection(header_field_offset=0x1c, original_ptr=0x220, data=bytes(32)),
            main.TrailingSection(header_field_offset=0x14, original_ptr=0x200, data=bytes(96)),   # grew past 0x20
        ]
        block = main.BuildHEADERModelBlock(
            bytes(64), b'', b'', bytes(32), trailing_bytes=bytes(128), original_header=bytes(original),
            original_trailing_off=0x200, trailing_sections=sections,
        )
        ptr6, ptr7, ptr8 = struct.unpack_from('>III', block, 0x14)
        self.assertEqual(ptr7, 0)
        self.assertEqual(ptr8 - ptr6, 96)
        self.assertEqual(ptr8 + 32, len(block))

    def test_rebuild_requires_both_build_modes(self):
        self.model['Submeshes'][0]['SkinnedRebuild'] = _rebuild(self.model, [[(BONE_A, 1.0)]] * N)
        with self.assertRaisesRegex(ValueError, "skn='build'"):
            main.BuildModelBlock(self.data, main.SectionModes(gpl='build'), sluggie_path=self.env.sluggie_path)
        with self.assertRaisesRegex(ValueError, "gpl='build'"):
            main.BuildModelBlock(self.data, main.SectionModes(skn='build'), sluggie_path=self.env.sluggie_path)

    def test_rebuild_with_a_rigid_rebuild_and_custom_submesh_in_one_build(self):
        import test_rigid_rebuild
        self.model['Submeshes'][0]['SkinnedRebuild'] = _rebuild(self.model, [[(BONE_A, 1.0)]] * N)
        self.model['Submeshes'][1]['RigidRebuild'] = test_rigid_rebuild._identity_rebuild(self.model)
        _result, gpl, _skn = self._build()
        descriptors = _descriptors(gpl)
        # Both donor blobs are dropped (body first, then the head leads the
        # region), so the rebuilt blobs start right after the table.
        count, table = struct.unpack_from('>II', gpl, 0x0C)
        self.assertEqual(min(descriptors[0][0], descriptors[1][0]), (table + count * 8 + 31) & ~31)
        self.assertEqual(_record_faces(_blob(gpl, 0), synthetic_donor.SURFACE_STATE_INDEX), _fan(N))
        self.assertEqual(test_rigid_rebuild._record_faces(test_rigid_rebuild._blob(gpl, 1), 5),
                         _fan(synthetic_donor.RIGID_VERTEX_COUNT))

    def test_untouched_model_builds_without_a_rebuild(self):
        result = main.BuildModelBlock(self.data, main.SectionModes(), sluggie_path=self.env.sluggie_path)
        self.assertTrue(result.validation_report['valid'])
        self.assertIsNone(result.parsed.mesh.submeshes[0].skinned_rebuild)


class SkinnedRebuildWiringTests(unittest.TestCase):
    def test_dispatcher_selects_gpl_skn_and_tex_build(self):
        model = {'Submeshes': [{'SkinnedRebuild': {'NewSurfaces': []}}]}
        self.assertEqual(start.hammerspace_section_args(model), ['--gpl', 'build', '--skn', 'build'])
        model['Submeshes'][0]['SkinnedRebuild']['NewSurfaces'] = [
            {'TextureAssignment': {'AdditionalTextureFileName': 'new.png'}},
        ]
        self.assertEqual(start.hammerspace_section_args(model), ['--gpl', 'build', '--skn', 'build', '--tex', 'build'])
        self.assertTrue(start._needs_hammerspace({'Submeshes': [{'SkinnedRebuild': {}}]}, 'x.sluggie'))

    def test_export_mode_treats_a_rebuild_as_a_hammerspace_only_edit(self):
        submeshes = [{'VertexBuffer': {'VertexBufferOffset': '0x10'}, 'SkinnedRebuild': {}}]
        self.assertEqual(ExportMode.stale_hammerspace_submeshes(submeshes, []), ['submesh 0'])
        self.assertEqual(ExportMode.stale_hammerspace_submeshes(submeshes, ['0x10']), [])

    def test_parse_sluggie_attaches_the_rebuild(self):
        data = synthetic_donor.build_sluggie()
        model = data['SluggiesModel']
        model['Submeshes'][0]['SkinnedRebuild'] = _rebuild(model, [[(BONE_A, 0.25), (BONE_B, 0.75)]] * N)
        model['Submeshes'][0]['SkinnedRebuild']['FacialPoses'] = _facial_poses([2, 5])
        parsed = main.ParseSluggie(data)
        got = parsed.mesh.submeshes[0].skinned_rebuild
        self.assertIsNone(parsed.mesh.submeshes[1].skinned_rebuild)
        self.assertEqual(len(got.facial_poses), 1)
        self.assertEqual((got.facial_poses[0].object_index, got.facial_poses[0].vertices), (0, [2, 5]))
        self.assertEqual(len(got.facial_poses[0].deltas), FACIAL_POSES - 1)
        self.assertEqual(len(got.facial_poses[0].deltas[0]), 2 * STRIDE)
        self.assertEqual(len(got.influences), 2 * N)
        self.assertEqual(got.influences[0], (0, BONE_A, 0.25))
        self.assertEqual(got.influences[1], (0, BONE_B, 0.75))
        self.assertEqual(got.face_surface_table, [SURFACE])
        self.assertEqual(len(got.uv_channels), 2)
        self.assertEqual(got.reasons, ['topology'])
        self.assertEqual(len(decode_field(model['Submeshes'][0]['SkinnedRebuild']['VertexBufferData'])), N * STRIDE)


if __name__ == '__main__':
    unittest.main()
