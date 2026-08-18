import struct
import sys
import pathlib
import unittest


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from drawlist import decodeDrawList, encodeDrawList
from GeometryRebuild import rebuild_edited_uvs


DESCRIPTORS = [
    {'key': 'position', 'index_size': 1},
    {'key': 'lighting', 'index_size': 1},
    {'key': 'texture0', 'index_size': 1},
]


def _u16s(values):
    return list(struct.pack(f'>{len(values)}H', *values))


def _record(s, t):
    return struct.pack('>2h', s, t)


def _face(positions, uv_indices, lighting_indices):
    return [
        {
            'position': position,
            'texture0': uv_index,
            'lighting': lighting_index,
        }
        for position, uv_index, lighting_index in zip(
            positions, uv_indices, lighting_indices
        )
    ]


def _normal_records():
    return [_record(0, 0), _record(1, 0), _record(1, 1), _record(0, 1)]


def _uv_edited():
    return [
        _record(1, 0), _record(10, 0), _record(10, 10),
        _record(1, 0), _record(10, 10), _record(0, 10),
    ]


def _model(edited_lighting_records):
    original_indices = [0, 1, 2, 0, 2, 3]
    uv_indices = [0, 1, 2, 0, 2, 3]
    faces = [
        _face((0, 1, 2), uv_indices[:3], original_indices[:3]),
        _face((0, 2, 3), uv_indices[3:], original_indices[3:]),
    ]
    primitive = encodeDrawList(faces, DESCRIPTORS) + b'\x00'
    uv_records = [_record(0, 0), _record(10, 0), _record(10, 10), _record(0, 10)]
    submesh = {
        'FacesCount': 2,
        'FacesData': _u16s([0, 1, 2, 0, 2, 3]),
        'UVChannels': [{
            'UVChannelIndex': 0,
            'UVChannelData': list(b''.join(uv_records)),
            'UVChannelDataEdited': list(_uv_edited()),
            'UVFacesData': _u16s(uv_indices),
            'UVFacesDataEdited': _u16s(range(6)),
            'UVChannelCompCount': 2,
            'UVChannelQuantizeInfo': 0x30,
        }],
        'NormalBuffer': {
            'NormalDataPtrFieldOffset': '0x00000100',
            'NormalCountFieldOffset': '0x00000104',
            'NormalBufferOffset': '0x00000200',
            'NormalBufferLength': 16,
            'NormalBufferCompCount': 2,
            'NormalBufferQuantizeInfo': 0x30,
            'NormalBufferData': list(b''.join(_normal_records())),
            'NormalBufferDataEdited': list(b''.join(edited_lighting_records)),
            'NormalFacesData': _u16s(original_indices),
        },
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
    }
    return {
        'SluggiesModel': {
            'UseBase64': False,
            'Submeshes': [submesh],
        },
    }


def _strip_uv_edits(submesh):
    for uv in submesh.get('UVChannels', []):
        uv.pop('UVChannelDataEdited', None)
        uv.pop('UVFacesDataEdited', None)


class NormalRebuildTests(unittest.TestCase):
    def test_unchanged_normals_are_dropped(self):
        donor = _normal_records()
        edited = [
            donor[0], donor[1], donor[2],
            donor[0], donor[2], donor[3],
        ]
        data = _model(edited)
        submesh = data['SluggiesModel']['Submeshes'][0]
        _strip_uv_edits(submesh)

        self.assertTrue(rebuild_edited_uvs(data))

        self.assertNotIn('NormalBufferDataEdited', submesh['NormalBuffer'])
        self.assertNotIn('NormalArraysEditedByImporter', submesh)
        self.assertNotIn('PrimListDataEdited', submesh['DisplayStates'][1])

    def test_value_edit_preserves_donor_indices_and_primitive_stream(self):
        edited = [
            _record(1, 0), _record(2, 0), _record(3, 1),
            _record(1, 0), _record(3, 1), _record(0, 2),
        ]
        data = _model(edited)
        submesh = data['SluggiesModel']['Submeshes'][0]
        _strip_uv_edits(submesh)

        self.assertTrue(rebuild_edited_uvs(data))

        normal = submesh['NormalBuffer']
        self.assertTrue(submesh['NormalArraysEditedByImporter'])
        self.assertNotIn('NormalPrimitiveListsRebuiltByImporter', submesh)
        self.assertEqual(len(normal['NormalBufferDataEdited']), 4 * 4)
        self.assertNotIn('PrimListDataEdited', submesh['DisplayStates'][1])

    def test_split_shared_slot_compacts_and_rebuilds_affected_state(self):
        edited = [
            _record(1, 0), _record(1, 0), _record(1, 1),
            _record(2, 0), _record(1, 1), _record(0, 1),
        ]
        data = _model(edited)
        submesh = data['SluggiesModel']['Submeshes'][0]
        _strip_uv_edits(submesh)

        self.assertTrue(rebuild_edited_uvs(data))

        normal = submesh['NormalBuffer']
        self.assertTrue(submesh['NormalPrimitiveListsRebuiltByImporter'])
        self.assertEqual(len(normal['NormalBufferDataEdited']), 5 * 4)
        rebuilt = bytes(submesh['DisplayStates'][1]['PrimListDataEdited'])
        faces = decodeDrawList(rebuilt, DESCRIPTORS)
        self.assertEqual(
            [[vertex['lighting'] for vertex in face] for face in faces],
            [[0, 1, 2], [4, 2, 3]],
        )

    def test_uv_and_normal_edits_rebuild_independent_fields(self):
        uv_edited = [
            _record(1, 0), _record(10, 0), _record(10, 10),
            _record(2, 0), _record(10, 10), _record(0, 10),
        ]
        normal_edited = [
            _record(1, 0), _record(1, 0), _record(1, 1),
            _record(2, 0), _record(1, 1), _record(0, 1),
        ]
        data = _model(normal_edited)
        submesh = data['SluggiesModel']['Submeshes'][0]
        uv = submesh['UVChannels'][0]
        uv['UVChannelDataEdited'] = list(b''.join(uv_edited))

        self.assertTrue(rebuild_edited_uvs(data))

        self.assertTrue(submesh['UVArraysEditedByImporter'])
        self.assertTrue(submesh['NormalArraysEditedByImporter'])
        self.assertTrue(submesh['UVPrimitiveListsRebuiltByImporter'])
        self.assertTrue(submesh['NormalPrimitiveListsRebuiltByImporter'])
        rebuilt = bytes(submesh['DisplayStates'][1]['PrimListDataEdited'])
        faces = decodeDrawList(rebuilt, DESCRIPTORS)
        self.assertEqual(
            [[vertex['texture0'] for vertex in face] for face in faces],
            [[0, 1, 2], [4, 2, 3]],
        )
        self.assertEqual(
            [[vertex['lighting'] for vertex in face] for face in faces],
            [[0, 1, 2], [4, 2, 3]],
        )


if __name__ == '__main__':
    unittest.main()