import pathlib
import copy
import struct
import sys
import unittest


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from drawlist import decodeDrawList, encodeDrawList
from GeometryRebuild import _rewrite_uv_primitive_blocks, rebuild_edited_uvs


DESCRIPTORS = [
    {'key': 'position', 'index_size': 1},
    {'key': 'texture0', 'index_size': 1},
]


def _u16s(values):
    return list(struct.pack(f'>{len(values)}H', *values))


def _record(s, t):
    return struct.pack('>2h', s, t)


def _face(positions, uv_indices):
    return [
        {'position': position, 'texture0': uv_index}
        for position, uv_index in zip(positions, uv_indices)
    ]


def _model(original_uv_indices, edited_records):
    faces = [
        _face((0, 1, 2), original_uv_indices[:3]),
        _face((0, 2, 3), original_uv_indices[3:]),
    ]
    primitive = encodeDrawList(faces, DESCRIPTORS) + b'\x00'
    original_records = [_record(0, 0), _record(10, 0), _record(10, 10), _record(0, 10)]
    return {
        'SluggiesModel': {
            'UseBase64': False,
            'Submeshes': [{
                'FacesCount': 2,
                'FacesData': _u16s([0, 1, 2, 0, 2, 3]),
                'UVChannels': [{
                    'UVChannelIndex': 0,
                    'UVChannelData': list(b''.join(original_records)),
                    'UVChannelDataEdited': list(b''.join(edited_records)),
                    'UVFacesData': _u16s(original_uv_indices),
                    'UVFacesDataEdited': _u16s(range(6)),
                    'UVChannelCompCount': 2,
                    'UVChannelQuantizeInfo': 0x30,
                }],
                'DisplayStates': [{
                    'DisplayStateId': 3,
                    'ShaderMode': '00000028',
                    'VertexStreamLayout': [],
                }, {
                    'DisplayStateId': 7,
                    'ShaderMode': '00000000',
                    'PrimListData': list(primitive),
                    'VertexStreamLayout': DESCRIPTORS,
                }],
            }],
        },
    }


class UVRebuildTests(unittest.TestCase):
    def test_donor_identical_sibling_channel_mirrors_single_channel_edit(self):
        original_indices = [0, 1, 2, 0, 2, 3]
        edited = [
            _record(1, 0), _record(10, 0), _record(10, 10),
            _record(1, 0), _record(10, 10), _record(0, 10),
        ]
        data = _model(original_indices, edited)
        submesh = data['SluggiesModel']['Submeshes'][0]
        sibling = copy.deepcopy(submesh['UVChannels'][0])
        sibling['UVChannelIndex'] = 1
        sibling.pop('UVChannelDataEdited')
        sibling.pop('UVFacesDataEdited')
        submesh['UVChannels'].append(sibling)

        self.assertTrue(rebuild_edited_uvs(data))

        source, target = submesh['UVChannels']
        self.assertEqual(target['UVChannelDataEdited'], source['UVChannelDataEdited'])
        self.assertEqual(target['UVFacesDataEdited'], source['UVFacesDataEdited'])

    def test_quad_batch_triangulates_only_conflicting_quad(self):
        descriptors = [{'key': 'texture0', 'index_size': 1}]
        raw = bytes([0x80, 0, 8, 0, 1, 2, 3, 4, 5, 6, 7, 0])
        desired = [0, 1, 2, 3, 4, 5, 6, 5, 4, 4, 7, 6]

        rebuilt, face_cursor, rebuilt_blocks = _rewrite_uv_primitive_blocks(
            raw,
            descriptors,
            0,
            {0: {'indices': desired}},
            'fixture',
        )

        self.assertEqual(face_cursor, 4)
        self.assertEqual(rebuilt_blocks, 1)
        self.assertIn(0x90, rebuilt)
        self.assertIn(0x80, rebuilt)
        faces = decodeDrawList(rebuilt, descriptors)
        self.assertEqual(
            [[vertex['texture0'] for vertex in face] for face in faces],
            [desired[index:index + 3] for index in range(0, len(desired), 3)],
        )

    def test_value_edit_preserves_donor_indices_and_primitive_stream(self):
        original_indices = [0, 1, 2, 0, 2, 3]
        edited = [
            _record(1, 0), _record(10, 0), _record(10, 10),
            _record(1, 0), _record(10, 10), _record(0, 10),
        ]
        data = _model(original_indices, edited)

        self.assertTrue(rebuild_edited_uvs(data))

        submesh = data['SluggiesModel']['Submeshes'][0]
        uv = submesh['UVChannels'][0]
        self.assertTrue(submesh['UVArraysEditedByImporter'])
        self.assertNotIn('UVPrimitiveListsRebuiltByImporter', submesh)
        self.assertEqual(uv['UVFacesDataEdited'], _u16s(original_indices))
        self.assertNotIn('PrimListDataEdited', submesh['DisplayStates'][1])

    def test_split_shared_slot_compacts_and_rebuilds_affected_state(self):
        original_indices = [0, 1, 2, 0, 2, 3]
        edited = [
            _record(1, 0), _record(10, 0), _record(10, 10),
            _record(2, 0), _record(10, 10), _record(0, 10),
        ]
        data = _model(original_indices, edited)

        self.assertTrue(rebuild_edited_uvs(data))

        submesh = data['SluggiesModel']['Submeshes'][0]
        uv = submesh['UVChannels'][0]
        self.assertTrue(submesh['UVPrimitiveListsRebuiltByImporter'])
        self.assertEqual(len(uv['UVChannelDataEdited']), 5 * 4)
        rebuilt = bytes(submesh['DisplayStates'][1]['PrimListDataEdited'])
        faces = decodeDrawList(rebuilt, DESCRIPTORS)
        self.assertEqual(
            [[vertex['texture0'] for vertex in face] for face in faces],
            [[0, 1, 2], [4, 2, 3]],
        )


if __name__ == '__main__':
    unittest.main()
