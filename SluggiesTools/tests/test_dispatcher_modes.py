import pathlib
import sys
import unittest


ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import start


class HammerspaceSectionArgsTests(unittest.TestCase):
    def test_surface_assignments_request_gpl_build(self):
        model = {'Submeshes': [{}, {'FaceSurfaceIdsEdited': 'AAE='}]}

        self.assertEqual(start.hammerspace_section_args(model), ['--gpl', 'build'])

    def test_unchanged_model_keeps_clone_defaults(self):
        self.assertEqual(start.hammerspace_section_args({'Submeshes': [{}]}), [])

    def test_static_position_edit_requests_only_gpl_build(self):
        model = {
            'UseBase64': False,
            'Submeshes': [{
                'VertexBuffer': {
                    'VertexBufferData': [0, 1],
                    'VertexBufferDataEdited': [0, 2],
                    'VertexBufferCompCount': 3,
                },
            }],
        }

        self.assertEqual(
            start.hammerspace_section_args(model),
            ['--gpl', 'build'],
        )

    def test_skinned_position_edit_requests_gpl_and_skn_build(self):
        model = {
            'UseBase64': False,
            'SkinDataEdited': {'SK1s': []},
            'Submeshes': [{
                'VertexBuffer': {
                    'VertexBufferData': [0, 1],
                    'VertexBufferDataEdited': [0, 2],
                    'VertexBufferCompCount': 6,
                },
            }],
        }

        self.assertEqual(
            start.hammerspace_section_args(model),
            ['--gpl', 'build', '--skn', 'build'],
        )

    def test_identical_reexported_position_buffer_keeps_clone_defaults(self):
        model = {
            'UseBase64': False,
            'Submeshes': [{
                'VertexBuffer': {
                    'VertexBufferData': [0, 1],
                    'VertexBufferDataEdited': [0, 1],
                    'VertexBufferCompCount': 3,
                },
            }],
        }

        self.assertEqual(start.hammerspace_section_args(model), [])

    def test_uv_edit_payload_requests_gpl_build(self):
        model = {
            'UseBase64': False,
            'Submeshes': [{
                'VertexBuffer': {},
                'UVChannels': [{
                    'UVChannelDataEdited': [0, 1],
                    'UVFacesDataEdited': [0, 0],
                }],
            }],
        }

        self.assertEqual(
            start.hammerspace_section_args(model),
            ['--gpl', 'build'],
        )

    def test_identical_stale_uv_payload_keeps_clone_defaults(self):
        model = {
            'UseBase64': False,
            'Submeshes': [{
                'VertexBuffer': {},
                'UVChannels': [{
                    'UVChannelData': [0, 1],
                    'UVChannelDataEdited': [0, 1],
                    'UVFacesDataEdited': [0, 0],
                }],
            }],
        }

        self.assertEqual(start.hammerspace_section_args(model), [])

    def test_other_edit_markers_do_not_activate_unfinished_rebuilders(self):
        model = {'Submeshes': [{'FacesDataEdited': 'AAAA'}]}

        self.assertEqual(start.hammerspace_section_args(model), [])

    def test_reimport_textures_requests_tex_build(self):
        model = {'Submeshes': [{}], 'ReimportTextures': True}

        self.assertEqual(start.hammerspace_section_args(model), ['--tex', 'build'])

    def test_reimport_textures_with_geometry_edits_requests_gpl_and_tex_build(self):
        model = {
            'Submeshes': [{'FaceSurfaceIdsEdited': 'AAE='}],
            'ReimportTextures': True,
        }

        self.assertEqual(
            start.hammerspace_section_args(model),
            ['--gpl', 'build', '--tex', 'build'],
        )

    def test_reimport_textures_false_keeps_tex_clone(self):
        model = {'Submeshes': [{}], 'ReimportTextures': False}

        self.assertEqual(start.hammerspace_section_args(model), [])

    def test_absent_reimport_textures_keeps_tex_clone(self):
        model = {'Submeshes': [{}]}

        self.assertEqual(start.hammerspace_section_args(model), [])


if __name__ == '__main__':
    unittest.main()