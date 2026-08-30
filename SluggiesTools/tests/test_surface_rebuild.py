import pathlib
import sys
import unittest


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from drawlist import decodeDrawList, encodeDrawList
from GeometryRebuild import rebuild_surface_assignments


DESCRIPTORS = [{'key': 'position', 'index_size': 1}]


def _face(first_position):
    return [
        {'position': first_position},
        {'position': first_position + 1},
        {'position': first_position + 2},
    ]


def _display_state(face, pad='000000'):
    primitive_data = encodeDrawList([face], DESCRIPTORS) + b'\x00'
    return {
        'DisplayStateId': 7,
        'DisplayStatePadBytes': pad,
        'ShaderMode': '00000000',
        'VertexStreamLayout': DESCRIPTORS,
        'PrimListData': list(primitive_data),
    }


def _model(assignments=None):
    submesh = {
        'DisplayStates': [
            _display_state(_face(0), '010203'),
            _display_state(_face(3), '040506'),
        ],
    }
    if assignments is not None:
        submesh['FaceSurfaceIdsEdited'] = [
            byte
            for state_idx in assignments
            for byte in state_idx.to_bytes(2, 'big')
        ]
    return {'SluggiesModel': {'UseBase64': False, 'Submeshes': [submesh]}}


class SurfaceAssignmentRebuildTests(unittest.TestCase):
    def test_reassigns_existing_faces_between_donor_surfaces(self):
        data = _model([1, 0])
        original_states = data['SluggiesModel']['Submeshes'][0]['DisplayStates']
        original_payloads = [state['PrimListData'] for state in original_states]

        self.assertTrue(rebuild_surface_assignments(data))

        states = data['SluggiesModel']['Submeshes'][0]['DisplayStates']
        self.assertEqual([state['PrimListData'] for state in states], original_payloads)
        self.assertNotIn('PrimListDataEdited', states[0])
        self.assertNotIn('PrimListDataEdited', states[1])
        self.assertEqual(states[0]['DisplayStatePadBytes'], '040506')
        self.assertEqual(states[1]['DisplayStatePadBytes'], '010203')
        self.assertTrue(
            data['SluggiesModel']['Submeshes'][0]['SurfaceAssignmentsRebuiltByImporter']
        )

    def test_preserves_complete_triangle_strip_block_when_moved(self):
        strip = bytes([0x98, 0, 4, 0, 1, 2, 3, 0])
        data = _model([1, 1, 1])
        states = data['SluggiesModel']['Submeshes'][0]['DisplayStates']
        states[0]['PrimListData'] = list(strip)

        self.assertTrue(rebuild_surface_assignments(data))

        self.assertEqual(bytes(states[0]['PrimListData']), strip)
        self.assertNotIn('PrimListDataEdited', states[0])
        self.assertEqual(states[0]['DisplayStatePadBytes'], '040506')

    def test_rejects_partial_donor_surface_move(self):
        strip = bytes([0x98, 0, 4, 0, 1, 2, 3, 0])
        data = _model([1, 0, 1])
        data['SluggiesModel']['Submeshes'][0]['DisplayStates'][0]['PrimListData'] = list(strip)

        with self.assertRaisesRegex(ValueError, 'partial surface reassignment'):
            rebuild_surface_assignments(data)

    def test_explicit_unchanged_assignments_preserve_original_primitive_lists(self):
        data = _model([0, 1])

        self.assertFalse(rebuild_surface_assignments(data))

        states = data['SluggiesModel']['Submeshes'][0]['DisplayStates']
        self.assertNotIn('PrimListDataEdited', states[0])
        self.assertNotIn('PrimListDataEdited', states[1])

    def test_rejects_non_drawable_target_display_state(self):
        data = _model([2, 1])

        with self.assertRaisesRegex(ValueError, 'not an existing drawable donor surface'):
            rebuild_surface_assignments(data)

    def test_same_type_move_accepted(self):
        """A complete move between two donor surfaces that share the same
        FourCC (shader mode) is allowed and the source adopts the target's
        material state."""
        data = _model([1, 1])
        states = data['SluggiesModel']['Submeshes'][0]['DisplayStates']
        states[0]['ShaderMode'] = '47535043'
        states[1]['ShaderMode'] = '47535043'

        self.assertTrue(rebuild_surface_assignments(data))

        states = data['SluggiesModel']['Submeshes'][0]['DisplayStates']
        # Source adopts the target's shader mode and is flagged as aliased.
        self.assertEqual(states[0]['ShaderMode'], '47535043')
        self.assertTrue(states[0]['MaterialStateAliasedByImporter'])

    def test_different_type_move_rejected(self):
        """A complete move between donor surfaces with different FourCC
        (shader modes) is rejected — this is the cross-type corruption guard
        (e.g. Spec -> Shdw) that previously broke the vertex-stream/DMA
        contract."""
        data = _model([1, 1])
        states = data['SluggiesModel']['Submeshes'][0]['DisplayStates']
        states[0]['ShaderMode'] = '47535043'
        states[1]['ShaderMode'] = '47534844'

        with self.assertRaisesRegex(ValueError, 'different shader modes'):
            rebuild_surface_assignments(data)

    def test_different_type_guard_fires_before_adopt(self):
        """The same-type guard must reject the move before any material state
        is adopted, so a rejected cross-type move leaves the source untouched."""
        data = _model([1, 1])
        states = data['SluggiesModel']['Submeshes'][0]['DisplayStates']
        states[0]['ShaderMode'] = '47535043'
        states[1]['ShaderMode'] = '47534844'

        with self.assertRaisesRegex(ValueError, 'different shader modes'):
            rebuild_surface_assignments(data)

        # No adoption happened: source keeps its own shader mode and is not
        # flagged as aliased.
        states = data['SluggiesModel']['Submeshes'][0]['DisplayStates']
        self.assertEqual(states[0]['ShaderMode'], '47535043')
        self.assertNotIn('MaterialStateAliasedByImporter', states[0])


if __name__ == '__main__':
    unittest.main()