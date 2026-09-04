import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

TOOLS_DIR = ROOT_DIR / 'SluggiesTools'
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import start
from texture_helper import PngTextureTarget


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

    def test_normal_edit_payload_requests_gpl_build(self):
        model = {
            'UseBase64': False,
            'Submeshes': [{
                'VertexBuffer': {},
                'NormalBuffer': {
                    'NormalBufferData': [0, 1],
                    'NormalBufferDataEdited': [0, 2],
                    'NormalFacesDataEdited': [0, 0],
                },
            }],
        }

        self.assertEqual(
            start.hammerspace_section_args(model),
            ['--gpl', 'build'],
        )

    def test_identical_stale_normal_payload_keeps_clone_defaults(self):
        model = {
            'UseBase64': False,
            'Submeshes': [{
                'VertexBuffer': {},
                'NormalBuffer': {
                    'NormalBufferData': [0, 1],
                    'NormalBufferDataEdited': [0, 1],
                    'NormalFacesDataEdited': [0, 0],
                },
            }],
        }

        self.assertEqual(start.hammerspace_section_args(model), [])

    def test_color_edit_payload_requests_gpl_build(self):
        model = {
            'UseBase64': False,
            'Submeshes': [{
                'VertexBuffer': {},
                'ColorChannels': [{
                    'ColorChannelData': [0, 1],
                    'ColorChannelDataEdited': [0, 2],
                    'ColorFacesDataEdited': [0, 0],
                }],
            }],
        }

        self.assertEqual(
            start.hammerspace_section_args(model),
            ['--gpl', 'build'],
        )

    def test_identical_stale_color_payload_keeps_clone_defaults(self):
        model = {
            'UseBase64': False,
            'Submeshes': [{
                'VertexBuffer': {},
                'ColorChannels': [{
                    'ColorChannelData': [0, 1],
                    'ColorChannelDataEdited': [0, 1],
                    'ColorFacesDataEdited': [0, 0],
                }],
            }],
        }

        self.assertEqual(start.hammerspace_section_args(model), [])

    def test_normal_and_color_edits_request_gpl_build(self):
        model = {
            'UseBase64': False,
            'Submeshes': [{
                'VertexBuffer': {},
                'NormalBuffer': {
                    'NormalBufferData': [0, 1],
                    'NormalBufferDataEdited': [0, 2],
                },
                'ColorChannels': [{
                    'ColorChannelData': [0, 1],
                    'ColorChannelDataEdited': [0, 2],
                }],
            }],
        }

        self.assertEqual(
            start.hammerspace_section_args(model),
            ['--gpl', 'build'],
        )

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


class PatchPngTests(unittest.TestCase):
    """PLAN_PngPatching Phase 6: _patch_png dispatches to the correct child
    script with the right arguments."""

    def _make_target(self, sluggie_path='/tmp/model.sluggie', png_path='/tmp/tex/0.png', texture_index=0):
        return PngTextureTarget(
            png_path=png_path,
            sluggie_path=sluggie_path,
            descriptor={'TextureIndex': texture_index, 'TextureFileName': '0.png'},
            texture_index=texture_index,
        )

    @mock.patch('start._current_model_in_hammerspace', return_value=False)
    @mock.patch('start.subprocess.run')
    def test_inplace_path_command(self, mock_run, _mock_hs_check):
        target = self._make_target()
        model = {'ChunkNumber': 18, 'FileIndex': 0}

        start._patch_png(target, model)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn('--texture-file', cmd)
        self.assertIn('--texture-index', cmd)
        self.assertIn('0', cmd)
        self.assertTrue(any('patch_inplace' in arg for arg in cmd))

    @mock.patch('start.subprocess.run')
    def test_hammerspace_path_command(self, mock_run):
        target = self._make_target()
        model = {'UseHammerspace': True, 'Submeshes': [{}]}

        start._patch_png(target, model)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn('--texture-file', cmd)
        self.assertIn('--texture-index', cmd)
        self.assertIn('--tex', cmd)
        self.assertIn('build', cmd)
        self.assertTrue(any('HammerspaceMain' in arg for arg in cmd))


class RunPatchingDispatchTests(unittest.TestCase):
    """PLAN_PngPatching Phase 6: run_patching routes .png and .sluggie files
    correctly and handles per-file failures without aborting."""

    def _write_sluggie(self, path, model_data=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump({'SluggiesModel': model_data or {}}, f)

    def _make_target(self, sluggie_path, png_path='0.png', texture_index=0):
        return PngTextureTarget(
            png_path=png_path,
            sluggie_path=sluggie_path,
            descriptor={'TextureIndex': texture_index, 'TextureFileName': '0.png'},
            texture_index=texture_index,
        )

    @mock.patch('start._current_model_in_hammerspace', return_value=False)
    @mock.patch('start.subprocess.run')
    def test_png_routes_to_patch_png(self, mock_run, _mock_hs_check):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = os.path.join(temp_dir, 'model', 'model.sluggie')
            self._write_sluggie(sluggie)
            target = self._make_target(sluggie)

            with mock.patch.object(start._tex, 'resolve_png_to_texture', return_value=target):
                start.run_patching(['0.png'])

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertIn('--texture-file', cmd)

    @mock.patch('start._current_model_in_hammerspace', return_value=False)
    @mock.patch('start.subprocess.run')
    def test_sluggie_routes_to_patch_sluggie(self, mock_run, _mock_hs_check):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = os.path.join(temp_dir, 'model.sluggie')
            self._write_sluggie(sluggie)

            with mock.patch.object(start, 'SEARCH_DIR', temp_dir):
                start.run_patching(['model.sluggie'])

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertNotIn('--texture-file', cmd)

    @mock.patch('start._current_model_in_hammerspace', return_value=False)
    @mock.patch('start.subprocess.run')
    def test_mixed_png_and_sluggie(self, mock_run, _mock_hs_check):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = os.path.join(temp_dir, 'model.sluggie')
            self._write_sluggie(sluggie)
            target = self._make_target(sluggie)

            with (
                mock.patch.object(start._tex, 'resolve_png_to_texture', return_value=target),
                mock.patch.object(start, 'SEARCH_DIR', temp_dir),
            ):
                start.run_patching(['0.png', 'model.sluggie'])

            self.assertEqual(mock_run.call_count, 2)
            cmds = [call[0][0] for call in mock_run.call_args_list]
            self.assertTrue(any('--texture-file' in c for c in cmds))
            self.assertTrue(any('--texture-file' not in c for c in cmds))

    @mock.patch('start.subprocess.run')
    def test_unpatch_png_rejected(self, mock_run):
        with mock.patch.object(start.slogger, 'error') as mock_error:
            start.run_patching(['0.png'], unpatch=True)

        mock_run.assert_not_called()
        mock_error.assert_called_once()
        self.assertIn('--unpatch does not accept .png', mock_error.call_args[0][0])

    @mock.patch('start._current_model_in_hammerspace', return_value=False)
    @mock.patch('start.subprocess.run')
    def test_png_child_failure_continues_batch(self, mock_run, _mock_hs_check):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = os.path.join(temp_dir, 'model', 'model.sluggie')
            self._write_sluggie(sluggie)
            target = self._make_target(sluggie)

            mock_run.side_effect = [
                subprocess.CalledProcessError(1, 'patch'),
                None,
            ]

            with (
                mock.patch.object(start._tex, 'resolve_png_to_texture', return_value=target),
                mock.patch.object(start.slogger, 'error'),
                mock.patch.object(start.slogger, 'info'),
            ):
                start.run_patching(['first.png', 'second.png'])

            self.assertEqual(mock_run.call_count, 2)

    @mock.patch('start.subprocess.run')
    def test_hammerspace_png_routes_to_hammerspace(self, mock_run):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = os.path.join(temp_dir, 'model', 'model.sluggie')
            self._write_sluggie(sluggie, model_data={'UseHammerspace': True, 'Submeshes': [{}]})
            target = self._make_target(sluggie)

            with mock.patch.object(start._tex, 'resolve_png_to_texture', return_value=target):
                start.run_patching(['0.png'])

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertTrue(any('HammerspaceMain' in arg for arg in cmd))
            self.assertIn('--texture-file', cmd)


class UnusedCharacterGuardTests(unittest.TestCase):
    """Unused characters (dirs 89-94) share a playable character's model block
    and have no data block of their own, so an in-place patch (hammerspace off)
    must be cancelled rather than written into the shared donor block."""

    EXPECTED_ERROR = (
        "Unused characters cannot be in-place patched. Please activate "
        "hammerspace mode during Blender export."
    )

    def test_dir_index_from_export_folder_name(self):
        with mock.patch.object(start, 'SEARCH_DIR', '/x/2_Output_Models'):
            self.assertEqual(
                start._unused_character_dir_index(
                    '/x/2_Output_Models/89 Unused Yoshi A/'
                    '715046144_yoshi.gpl/715046144_yoshi.gpl.sluggie'
                ),
                89,
            )

    def test_dir_index_non_digit_folder_returns_none(self):
        with mock.patch.object(start, 'SEARCH_DIR', '/x/2_Output_Models'):
            self.assertIsNone(
                start._unused_character_dir_index('/x/2_Output_Models/model/model.sluggie')
            )

    def test_dir_index_path_outside_search_dir_returns_none(self):
        with mock.patch.object(start, 'SEARCH_DIR', '/x/2_Output_Models'):
            self.assertIsNone(
                start._unused_character_dir_index('/elsewhere/89 Unused Yoshi A/model.sluggie')
            )

    def test_dir_index_empty_returns_none(self):
        self.assertIsNone(start._unused_character_dir_index(''))

    @mock.patch('start.subprocess.run')
    def test_sluggie_inplace_for_unused_character_is_cancelled(self, mock_run):
        with tempfile.TemporaryDirectory() as temp_dir:
            found = os.path.join(temp_dir, '89 Unused Yoshi A', 'm.gpl', 'm.gpl.sluggie')
            os.makedirs(os.path.dirname(found))
            with open(found, 'w') as f:
                json.dump({'SluggiesModel': {'UseHammerspace': False, 'Submeshes': [{}]}}, f)

            with (
                mock.patch.object(start, 'SEARCH_DIR', temp_dir),
                mock.patch.object(start.slogger, 'error') as mock_error,
            ):
                start._patch_sluggie(found, {'UseHammerspace': False, 'Submeshes': [{}]}, False)

            mock_run.assert_not_called()
            mock_error.assert_called_once()
            self.assertIn(self.EXPECTED_ERROR, mock_error.call_args[0][0])

    @mock.patch('start.subprocess.run')
    def test_sluggie_hammerspace_for_unused_character_is_allowed(self, mock_run):
        with tempfile.TemporaryDirectory() as temp_dir:
            found = os.path.join(temp_dir, '89 Unused Yoshi A', 'm.gpl', 'm.gpl.sluggie')
            os.makedirs(os.path.dirname(found))
            with open(found, 'w') as f:
                json.dump({'SluggiesModel': {'UseHammerspace': True, 'Submeshes': [{}]}}, f)

            with mock.patch.object(start, 'SEARCH_DIR', temp_dir):
                start._patch_sluggie(found, {'UseHammerspace': True, 'Submeshes': [{}]}, False)

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertTrue(any('HammerspaceMain' in arg for arg in cmd))

    @mock.patch('start.subprocess.run')
    def test_sluggie_inplace_for_regular_character_is_allowed(self, mock_run):
        with tempfile.TemporaryDirectory() as temp_dir:
            found = os.path.join(temp_dir, '18 Mario', 'm.gpl', 'm.gpl.sluggie')
            os.makedirs(os.path.dirname(found))
            with open(found, 'w') as f:
                json.dump({'SluggiesModel': {'UseHammerspace': False, 'Submeshes': [{}]}}, f)

            with (
                mock.patch.object(start, 'SEARCH_DIR', temp_dir),
                mock.patch.object(start, '_current_model_in_hammerspace', return_value=False),
            ):
                start._patch_sluggie(found, {'UseHammerspace': False, 'Submeshes': [{}]}, False)

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertTrue(any('patch_inplace' in arg for arg in cmd))

    @mock.patch('start.subprocess.run')
    def test_png_inplace_for_unused_character_is_cancelled(self, mock_run):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = os.path.join(temp_dir, '89 Unused Yoshi A', 'm.gpl', 'm.gpl.sluggie')
            os.makedirs(os.path.dirname(sluggie))
            with open(sluggie, 'w') as f:
                json.dump({'SluggiesModel': {'UseHammerspace': False, 'Submeshes': [{}]}}, f)
            target = PngTextureTarget(
                png_path=os.path.join(temp_dir, '0.png'),
                sluggie_path=sluggie,
                descriptor={'TextureIndex': 0, 'TextureFileName': '0.png'},
                texture_index=0,
            )

            with (
                mock.patch.object(start, 'SEARCH_DIR', temp_dir),
                mock.patch.object(start.slogger, 'error') as mock_error,
            ):
                start._patch_png(target, {'UseHammerspace': False, 'Submeshes': [{}]})

            mock_run.assert_not_called()
            mock_error.assert_called_once()
            self.assertIn(self.EXPECTED_ERROR, mock_error.call_args[0][0])

    @mock.patch('start.subprocess.run')
    def test_png_hammerspace_for_unused_character_is_allowed(self, mock_run):
        with tempfile.TemporaryDirectory() as temp_dir:
            sluggie = os.path.join(temp_dir, '89 Unused Yoshi A', 'm.gpl', 'm.gpl.sluggie')
            os.makedirs(os.path.dirname(sluggie))
            with open(sluggie, 'w') as f:
                json.dump({'SluggiesModel': {'UseHammerspace': True, 'Submeshes': [{}]}}, f)
            target = PngTextureTarget(
                png_path=os.path.join(temp_dir, '0.png'),
                sluggie_path=sluggie,
                descriptor={'TextureIndex': 0, 'TextureFileName': '0.png'},
                texture_index=0,
            )

            with mock.patch.object(start, 'SEARCH_DIR', temp_dir):
                start._patch_png(target, {'UseHammerspace': True, 'Submeshes': [{}]})

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertTrue(any('HammerspaceMain' in arg for arg in cmd))


if __name__ == '__main__':
    unittest.main()