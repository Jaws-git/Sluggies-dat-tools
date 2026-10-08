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
    {'key': 'color0', 'index_size': 1},
    {'key': 'texture0', 'index_size': 1},
]


def _u16s(values):
    return list(struct.pack(f'>{len(values)}H', *values))


def _rgba(r, g, b, a):
    return struct.pack('>4B', r, g, b, a)


def _record(s, t):
    return struct.pack('>2h', s, t)


def _face(positions, uv_indices, lighting_indices, color_indices):
    return [
        {
            'position': p,
            'texture0': u,
            'lighting': l,
            'color0': c,
        }
        for p, u, l, c in zip(positions, uv_indices, lighting_indices, color_indices)
    ]


def _donor_colors():
    return [_rgba(255, 0, 0, 255), _rgba(0, 255, 0, 255),
            _rgba(0, 0, 255, 255), _rgba(255, 255, 0, 255)]


def _model(edited_color_entries, edited_color_indices=None):
    original_indices = [0, 1, 2, 0, 2, 3]
    uv_indices = [0, 1, 2, 0, 2, 3]
    color_indices = [0, 1, 2, 0, 2, 3]
    faces = [
        _face((0, 1, 2), uv_indices[:3], original_indices[:3], color_indices[:3]),
        _face((0, 2, 3), uv_indices[3:], original_indices[3:], color_indices[3:]),
    ]
    primitive = encodeDrawList(faces, DESCRIPTORS) + b'\x00'
    uv_records = [_record(0, 0), _record(10, 0), _record(10, 10), _record(0, 10)]
    normal_records = [_record(0, 0), _record(1, 0), _record(1, 1), _record(0, 1)]
    donor_colors = _donor_colors()
    if edited_color_indices is None:
        edited_color_indices = list(range(len(edited_color_entries)))
    submesh = {
        'FacesCount': 2,
        'FacesData': _u16s([0, 1, 2, 0, 2, 3]),
        'UVChannels': [{
            'UVChannelIndex': 0,
            'UVChannelData': list(b''.join(uv_records)),
            'UVChannelCompCount': 2,
            'UVChannelQuantizeInfo': 0x30,
            'UVFacesData': _u16s(uv_indices),
        }],
        'NormalBuffer': {
            'NormalDataPtrFieldOffset': '0x00000100',
            'NormalCountFieldOffset': '0x00000104',
            'NormalBufferOffset': '0x00000200',
            'NormalBufferLength': 16,
            'NormalBufferCompCount': 2,
            'NormalBufferQuantizeInfo': 0x30,
            'NormalBufferData': list(b''.join(normal_records)),
            'NormalFacesData': _u16s(original_indices),
        },
        'ColorChannels': [{
            'ColorChannelIndex': 0,
            'ColorDataPtrFieldOffset': '0x00000300',
            'ColorCountFieldOffset': '0x00000304',
            'ColorChannelOffset': '0x00000400',
            'ColorChannelLength': len(b''.join(donor_colors)),
            'ColorChannelCompCount': 4,
            'ColorChannelQuantizeInfo': 0x50,
            'ColorChannelData': list(b''.join(donor_colors)),
            'ColorFacesData': _u16s(color_indices),
            'ColorChannelDataEdited': list(b''.join(edited_color_entries)),
            'ColorFacesDataEdited': _u16s(edited_color_indices),
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
    }
    return {
        'SluggiesModel': {
            'UseBase64': False,
            'Submeshes': [submesh],
        },
    }


class ColorRebuildTests(unittest.TestCase):
    def test_unchanged_colors_are_dropped(self):
        donor = _donor_colors()
        edited = [donor[0], donor[1], donor[2],
                  donor[0], donor[2], donor[3]]
        data = _model(edited)
        submesh = data['SluggiesModel']['Submeshes'][0]

        self.assertTrue(rebuild_edited_uvs(data))

        cc = submesh['ColorChannels'][0]
        self.assertNotIn('ColorChannelDataEdited', cc)
        self.assertNotIn('ColorFacesDataEdited', cc)
        self.assertNotIn('ColorArraysEditedByImporter', submesh)
        self.assertNotIn('PrimListDataEdited', submesh['DisplayStates'][1])

    def test_value_edit_preserves_donor_indices(self):
        # One edited entry per loop (identity indices), consistent per donor
        # slot: loops 0/3 share slot 0, loops 2/4 share slot 2.
        edited = [
            _rgba(128, 0, 0, 255), _rgba(0, 128, 0, 255),
            _rgba(0, 0, 128, 255), _rgba(128, 0, 0, 255),
            _rgba(0, 0, 128, 255), _rgba(64, 64, 0, 255),
        ]
        data = _model(edited)
        submesh = data['SluggiesModel']['Submeshes'][0]

        self.assertTrue(rebuild_edited_uvs(data))

        self.assertTrue(submesh['ColorArraysEditedByImporter'])
        self.assertNotIn('ColorPrimitiveListsRebuiltByImporter', submesh)
        cc = submesh['ColorChannels'][0]
        self.assertIn('ColorChannelDataEdited', cc)
        self.assertEqual(len(cc['ColorChannelDataEdited']), 4 * 4)
        self.assertNotIn('PrimListDataEdited', submesh['DisplayStates'][1])

    def test_collapsed_colors_rebuild_primitive_lists(self):
        # Loops 0 and 1 (donor slots 0 and 1) get the same color, so the two
        # slots collapse to one. Indices change from [0,1,2,0,2,3] to
        # [0,0,1,0,1,2] → prim list rebuild.
        edited = [
            _rgba(128, 0, 0, 255), _rgba(128, 0, 0, 255),
            _rgba(0, 0, 128, 255), _rgba(128, 0, 0, 255),
            _rgba(0, 0, 128, 255), _rgba(128, 128, 0, 255),
        ]
        data = _model(edited)
        submesh = data['SluggiesModel']['Submeshes'][0]

        self.assertTrue(rebuild_edited_uvs(data))

        self.assertTrue(submesh['ColorPrimitiveListsRebuiltByImporter'])
        cc = submesh['ColorChannels'][0]
        self.assertEqual(len(cc['ColorChannelDataEdited']), 4 * 4)
        rebuilt = bytes(submesh['DisplayStates'][1]['PrimListDataEdited'])
        faces = decodeDrawList(rebuilt, DESCRIPTORS)
        self.assertEqual(
            [[vertex['color0'] for vertex in face] for face in faces],
            [[0, 0, 1], [0, 1, 2]],
        )

    def test_edit_on_a_late_loop_is_kept(self):
        # Regression (2026-10-08, Mario cap): the edited per-loop array was
        # read through the donor indices, so only edited[0..3] were ever
        # looked at and a color painted on loop 5 was dropped as "unchanged".
        donor = _donor_colors()
        painted = _rgba(10, 20, 30, 255)
        data = _model([donor[0], donor[1], donor[2], donor[0], donor[2], painted])
        submesh = data['SluggiesModel']['Submeshes'][0]

        self.assertTrue(rebuild_edited_uvs(data))

        self.assertTrue(submesh['ColorArraysEditedByImporter'])
        cc = submesh['ColorChannels'][0]
        self.assertEqual(
            bytes(cc['ColorChannelDataEdited']),
            b''.join([donor[0], donor[1], donor[2], painted]),
        )
        self.assertNotIn('PrimListDataEdited', submesh['DisplayStates'][1])

    def test_repeated_donor_value_round_trips_unchanged(self):
        # Mario's body array repeats a value (ffff twice). An unedited export
        # must keep each loop on its own donor slot, not move it to the
        # first copy (which would rebuild every draw list for nothing).
        white = _rgba(255, 255, 255, 255)
        red = _rgba(255, 0, 0, 255)
        data = _model([red, white, white, red, white, white])
        submesh = data['SluggiesModel']['Submeshes'][0]
        cc = submesh['ColorChannels'][0]
        cc['ColorChannelData'] = list(red + white + white + white)

        rebuild_edited_uvs(data)

        self.assertNotIn('ColorChannelDataEdited', cc)
        self.assertNotIn('ColorArraysEditedByImporter', submesh)
        self.assertNotIn('PrimListDataEdited', submesh['DisplayStates'][1])

    def test_more_new_colors_than_donor_slots_are_appended(self):
        # Regression: once a value was appended, the slot search ran past the
        # donor entries and raised IndexError.
        edited = [_rgba(10 * n, 0, 0, 255) for n in range(1, 7)]
        data = _model(edited)
        submesh = data['SluggiesModel']['Submeshes'][0]

        self.assertTrue(rebuild_edited_uvs(data))

        cc = submesh['ColorChannels'][0]
        self.assertEqual(bytes(cc['ColorChannelDataEdited']), b''.join(edited))
        rebuilt = bytes(submesh['DisplayStates'][1]['PrimListDataEdited'])
        faces = decodeDrawList(rebuilt, DESCRIPTORS)
        self.assertEqual(
            [[vertex['color0'] for vertex in face] for face in faces],
            [[0, 1, 2], [3, 4, 5]],
        )

    def test_index_only_change_is_kept(self):
        # Loop 5 now shows donor color 0 instead of 3: the compact bytes equal
        # the donor array, but the indices moved, so it is still an edit.
        donor = _donor_colors()
        data = _model([donor[0], donor[1], donor[2], donor[0], donor[2], donor[0]])
        submesh = data['SluggiesModel']['Submeshes'][0]

        self.assertTrue(rebuild_edited_uvs(data))

        self.assertTrue(submesh['ColorArraysEditedByImporter'])
        self.assertTrue(submesh['ColorPrimitiveListsRebuiltByImporter'])
        rebuilt = bytes(submesh['DisplayStates'][1]['PrimListDataEdited'])
        faces = decodeDrawList(rebuilt, DESCRIPTORS)
        self.assertEqual(
            [[vertex['color0'] for vertex in face] for face in faces],
            [[0, 1, 2], [0, 2, 0]],
        )

    def test_absent_color_channels_are_skipped(self):
        data = _model([])
        submesh = data['SluggiesModel']['Submeshes'][0]
        del submesh['ColorChannels']

        self.assertFalse(rebuild_edited_uvs(data))
        self.assertNotIn('ColorArraysEditedByImporter', submesh)


if __name__ == '__main__':
    unittest.main()
