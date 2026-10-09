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
        self.assertEqual(new_descriptors[1], donor_descriptors[1])
        self.assertGreaterEqual(new_descriptors[0][0], len(self.donor_gpl))
        self.assertEqual(new_descriptors[0][0] % 32, 0)

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
        self.assertGreaterEqual(min(descriptors[0][0], descriptors[1][0]), len(self.donor_gpl))
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
        parsed = main.ParseSluggie(data)
        got = parsed.mesh.submeshes[0].skinned_rebuild
        self.assertIsNone(parsed.mesh.submeshes[1].skinned_rebuild)
        self.assertEqual(len(got.influences), 2 * N)
        self.assertEqual(got.influences[0], (0, BONE_A, 0.25))
        self.assertEqual(got.influences[1], (0, BONE_B, 0.75))
        self.assertEqual(got.face_surface_table, [SURFACE])
        self.assertEqual(len(got.uv_channels), 2)
        self.assertEqual(got.reasons, ['topology'])
        self.assertEqual(len(decode_field(model['Submeshes'][0]['SkinnedRebuild']['VertexBufferData'])), N * STRIDE)


if __name__ == '__main__':
    unittest.main()
