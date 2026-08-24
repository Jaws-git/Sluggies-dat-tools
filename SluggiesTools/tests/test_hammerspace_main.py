import pathlib
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for import_path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import HammerspaceMain as main
import texture_helper


class BuildSKNSkinningDataTests(unittest.TestCase):
    def test_preserves_recorded_source_array_gap_for_unchanged_geometry(self):
        def sk1(pointer):
            return main.SK1(
                bone_index=0,
                vertex_cnt=1,
                vertex_offset=0,
                bind_pose_data=b'\x01' * 12,
                vertex_arr_field_offset=0,
                gpl_vertex_arr_field_offset=0,
                vertex_arr_absolute_ptr=0x1000 + pointer,
                gpl_vertex_arr_value=0,
            )

        skinning = main.SkinningData(
            skn_offset=0x1000,
            gpl_base_offset=0,
            mem_clr_ptr_field_offset=0,
            mem_clr_sze_field_offset=0,
            mem_clr_ptr_value=0,
            mem_clr_absolute_ptr=0,
            mem_clr_size=0,
            flush_ind_arr_field_offset=0,
            flush_ind_absolute_ptr=None,
            flush_ind_size=0,
            flush_ind_data=b'',
            quantize_info=9,
            sk1s=[sk1(0xC0), sk1(0x100)],
            sk2s=[],
            sk_accs=[],
            preserve_source_layout=True,
        )

        block = main.BuildSKNSkinningData(SimpleNamespace(skinning=skinning), None)
        sk1_offset = struct.unpack_from('>I', block, 0x08)[0]
        first_source = struct.unpack_from('>I', block, sk1_offset + 0x30)[0]
        second_source = struct.unpack_from('>I', block, sk1_offset + 0x40 + 0x30)[0]

        self.assertEqual((first_source, second_source), (0xC0, 0x100))
        self.assertEqual(block[0xCC:0x100], b'\x00' * 0x34)

    def test_places_flush_indices_between_sk2_sources_and_weights(self):
        def sk2(vertex_count):
            return main.SK2(
                bone_index1=0,
                bone_index2=1,
                vertex_cnt=vertex_count,
                vertex_offset=0,
                bind_pose_data=b'\x01' * (vertex_count * 12),
                weight_data=b'\x80' * (vertex_count * 2),
                vertex_arr_field_offset=0,
                weight_arr_field_offset=0,
                gpl_vertex_arr_field_offset=0,
                vertex_arr_absolute_ptr=0,
                weight_arr_absolute_ptr=0,
                gpl_vertex_arr_value=0,
            )

        skinning = main.SkinningData(
            skn_offset=0,
            gpl_base_offset=0,
            mem_clr_ptr_field_offset=0,
            mem_clr_sze_field_offset=0,
            mem_clr_ptr_value=0,
            mem_clr_absolute_ptr=0,
            mem_clr_size=0,
            flush_ind_arr_field_offset=0,
            flush_ind_absolute_ptr=0,
            flush_ind_size=1,
            flush_ind_data=b'\x00\x00',
            quantize_info=9,
            sk1s=[],
            sk2s=[sk2(1), sk2(2)],
            sk_accs=[],
        )

        block = main.BuildSKNSkinningData(SimpleNamespace(skinning=skinning), None)
        sk2_offset = struct.unpack_from('>I', block, 0x0C)[0]
        first_source = struct.unpack_from('>I', block, sk2_offset + 0x60)[0]
        first_weight = struct.unpack_from('>I', block, sk2_offset + 0x64)[0]
        second_source = struct.unpack_from('>I', block, sk2_offset + 0x74 + 0x60)[0]
        second_weight = struct.unpack_from('>I', block, sk2_offset + 0x74 + 0x64)[0]
        flush_offset = struct.unpack_from('>I', block, 0x1C)[0]

        self.assertEqual(
            [first_source, second_source, flush_offset, first_weight, second_weight],
            [0x120, 0x140, 0x160, 0x180, 0x1A0],
        )


class BuildModelBlockTests(unittest.TestCase):
    def setUp(self):
        self.data = {'SluggiesModel': {'ChunkNumber': 18, 'FileIndex': 0}}
        self.parsed = mock.sentinel.parsed

    def _patch_common(self):
        return (
            mock.patch.object(main.hh, 'readDolEntry', return_value=(0x1000, 0x2000)),
            mock.patch.object(main, 'ParseSluggie', return_value=self.parsed),
            mock.patch.object(main, 'CloneGPL', return_value=b'GPL'),
            mock.patch.object(main, '_gpl_pos_offsets_from_bytes', return_value=[3]),
            mock.patch.object(main, 'CloneACT', return_value=b'ACT'),
            mock.patch.object(main, 'CloneTEX', return_value=b'TEX'),
            mock.patch.object(main, 'CloneSKN', return_value=b'SKN'),
            mock.patch.object(main, 'CloneTrailingSections', return_value=(b'TAIL', 0x80)),
            mock.patch.object(main, 'CloneHEADER', return_value=b'\x00' * 0x20),
        )

    def test_gpl_source_layout_requires_unchanged_primitive_list_lengths(self):
        draw_state = SimpleNamespace(
            prim_list_data=b'original',
            prim_list_length=len(b'original'),
            source_state_offset=0x120,
        )
        submesh = SimpleNamespace(
            source_layout_offset=0x100,
            position_data_ptr_field_offset=0x118,
            source_position_data_offset=0x180,
            draw_states=[draw_state],
        )
        mesh = SimpleNamespace(source_gpl_base_offset=0x20, submeshes=[submesh])

        self.assertTrue(main._can_preserve_gpl_source_layout(mesh))
        draw_state.prim_list_data = b'reassigned surface payload'
        self.assertTrue(main._can_preserve_gpl_internal_layout(mesh))
        self.assertFalse(main._can_preserve_gpl_source_layout(mesh))

    def _position_model(self, edited):
        return {
            'UseBase64': False,
            'Submeshes': [{
                'FacesCount': 1,
                'FacesCountEdited': 1,
                'FacesData': [0, 0, 0, 0, 0, 0],
                'FacesDataEdited': [0, 0, 0, 0, 0, 0],
                'VertexBuffer': {
                    'VertexBufferOffset': '0x30',
                    'VertexBufferLength': 6,
                    'VertexBufferData': [0, 0, 0, 1, 0, 2],
                    'VertexBufferDataEdited': edited,
                    'VertexBufferCompCount': 3,
                    'VertexBufferQuantizeInfo': 0x30,
                },
            }],
        }

    def test_position_edit_requires_unchanged_length_and_topology(self):
        valid = self._position_model([0, 3, 0, 1, 0, 2])
        self.assertEqual(len(main._position_edits(valid)), 1)

        changed_length = self._position_model([0, 3])
        with self.assertRaisesRegex(ValueError, 'changed byte length'):
            main._position_edits(changed_length)

        changed_faces = self._position_model([0, 3, 0, 1, 0, 2])
        changed_faces['Submeshes'][0]['FacesDataEdited'][-1] = 1
        with self.assertRaisesRegex(ValueError, 'changed face indices/order'):
            main._position_edits(changed_faces)

    def test_position_edit_patches_only_recorded_gpl_array_range(self):
        model = self._position_model([0, 3, 0, 1, 0, 2])
        original_gpl = bytes(range(32))
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
            block_header = bytearray(0x20)
            struct.pack_into('>I', block_header, 0x04, 0x20)
            input_dat.write_bytes(block_header)
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                patched = main.PatchGPLPositionArrays(original_gpl, model, 0)

        self.assertEqual(patched[:0x10], original_gpl[:0x10])
        self.assertEqual(patched[0x10:0x16], bytes([0, 3, 0, 1, 0, 2]))
        self.assertEqual(patched[0x16:], original_gpl[0x16:])

    def test_uv_edit_patches_only_recorded_gpl_array_range(self):
        model = {
            'UseBase64': False,
            'Submeshes': [{
                'UVArraysEditedByImporter': True,
                'UVChannels': [{
                    'UVChannelIndex': 0,
                    'UVChannelOffset': '0x34',
                    'UVChannelData': [0, 0, 0, 1],
                    'UVChannelDataEdited': [0, 2, 0, 1],
                }],
            }],
        }
        original_gpl = bytes(range(32))
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
            block_header = bytearray(0x20)
            struct.pack_into('>I', block_header, 0x04, 0x20)
            input_dat.write_bytes(block_header)
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                patched = main.PatchGPLUVArrays(original_gpl, model, 0)

        self.assertEqual(patched[:0x14], original_gpl[:0x14])
        self.assertEqual(patched[0x14:0x18], bytes([0, 2, 0, 1]))
        self.assertEqual(patched[0x18:], original_gpl[0x18:])

    def test_resized_uv_edit_appends_payloads_and_preserves_donor_bytes(self):
        original_gpl = bytes(range(128))
        model = {
            'UseBase64': False,
            'Submeshes': [{
                'SubmeshOffset': '0x30',
                'UVArraysEditedByImporter': True,
                'UVChannels': [{
                    'UVChannelIndex': 0,
                    'UVDataPtrFieldOffset': '0x50',
                    'UVCountFieldOffset': '0x54',
                    'UVChannelData': [1, 2, 3, 4],
                    'UVChannelDataEdited': [5, 6, 7, 8, 9, 10, 11, 12],
                    'UVChannelCompCount': 2,
                    'UVChannelQuantizeInfo': 0x30,
                }],
                'DisplayStates': [{
                    'PrimListPtrFieldOffset': '0x60',
                    'PrimListSizeFieldOffset': '0x64',
                    'PrimListDataEdited': [0x90, 0, 0, 0],
                }],
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
            block_header = bytearray(0x20)
            struct.pack_into('>I', block_header, 0x04, 0x20)
            input_dat.write_bytes(block_header)
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                patched = main.PatchGPLUVRebuild(original_gpl, model, 0)

        changed_header_ranges = set(range(0x30, 0x36)) | set(range(0x40, 0x48))
        self.assertTrue(all(
            patched[index] == original_gpl[index]
            for index in range(len(original_gpl))
            if index not in changed_header_ranges
        ))
        uv_pointer = struct.unpack_from('>I', patched, 0x30)[0]
        uv_count = struct.unpack_from('>H', patched, 0x34)[0]
        primitive_pointer, primitive_size = struct.unpack_from('>II', patched, 0x40)
        self.assertEqual(uv_count, 2)
        self.assertEqual(patched[0x10 + uv_pointer:0x10 + uv_pointer + 8], bytes(range(5, 13)))
        self.assertEqual(primitive_size, 32)
        self.assertEqual((0x10 + primitive_pointer) % 32, 0)

    def test_all_clone_build_only_assembles_without_output_mutation(self):
        patches = self._patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            with (
                mock.patch.object(main, 'validate_model_block', return_value={
                    'valid': True,
                    'errors': [],
                    'warnings': [],
                    'facts': {'section_pointers': {'GPL': 32, 'ACT': 35, 'TEX': 38, 'SKN': 41}},
                }),
                mock.patch.object(main.hh, 'writeModelBlock') as write_model,
                mock.patch.object(main.hh, 'patchDolEntry') as patch_dol,
            ):
                result = main.BuildModelBlock(self.data)

        self.assertEqual(struct.unpack_from('>5I', result.block), (0, 32, 35, 38, 64))
        self.assertEqual(result.block[0x20:41], b'GPLACTTEX')
        self.assertEqual(result.block[41:64], b'\x00' * 23)
        self.assertEqual(result.block[64:], b'SKNTAIL')
        self.assertEqual(result.section_sizes, {
            'GPL': 3,
            'ACT': 3,
            'TEX': 3,
            'SKN': 3,
            'trailing': 4,
        })
        self.assertTrue(result.validation_report['valid'])
        self.assertIn('validator_facts', result.validation_report)
        write_model.assert_not_called()
        patch_dol.assert_not_called()

    def test_build_preserves_dol_entry_prefix_before_inner_model(self):
        self.data['SluggiesModel'].update({
            'ModelOffset': 0x1020,
            'ModelLength': 0x40,
        })
        prefix = bytes(range(32))
        patches = self._patch_common()
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
            input_bytes = bytearray(0x1060)
            input_bytes[0x1000:0x1020] = prefix
            input_dat.write_bytes(input_bytes)
            with (
                mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)),
                mock.patch.object(main.hh, 'readDolEntry', return_value=(0x1000, 0x60)),
                patches[1], patches[2], patches[3], patches[4], patches[5],
                patches[6], patches[7], patches[8],
                mock.patch.object(main, 'validate_model_block', return_value={
                    'valid': True,
                    'errors': [],
                    'warnings': [],
                    'facts': {'section_pointers': {}},
                }),
            ):
                result = main.BuildModelBlock(self.data)

        self.assertEqual(result.block[:0x20], prefix)
        self.assertEqual(result.validation_report['container_prefix_size'], 0x20)
        self.assertEqual(result.validation_report['assembled_size'], len(result.block))

    def test_gpl_and_skn_build_modes_use_builders(self):
        self.data['SluggiesModel'].update({
            'UseHammerspace': True,
            'ModelOffset': 0x1000,
            'ModelLength': 0x2000,
            'Submeshes': [{
                'FacesData': b'\x00',
                'VertexBuffer': {
                    'VertexBufferData': b'\x00',
                    'VertexBufferCompCount': 3,
                    'VertexBufferQuantizeInfo': 0,
                },
                'DisplayStates': [{
                    'DisplayStateId': 0,
                    'PrimListLength': 1,
                    'PrimListData': b'\x00',
                    'ShaderMode': 'Spec',
                }],
            }],
        })
        patches = self._patch_common()
        built_gpl = main.GPLBuildResult(b'BUILT_GPL', [9])
        with patches[0], patches[1], patches[4], patches[5], patches[7], patches[8]:
            with (
                mock.patch.object(main, 'BuildGPLMeshData', return_value=built_gpl) as build_gpl,
                mock.patch.object(main, 'BuildSKNSkinningData', return_value=b'BUILT_SKN') as build_skn,
            ):
                result = main.BuildModelBlock(
                    self.data,
                    main.SectionModes(gpl='build', skn='build'),
                )

        build_gpl.assert_called_once_with(self.parsed)
        build_skn.assert_called_once_with(self.parsed, built_gpl)
        self.assertEqual(result.section_modes.gpl, 'build')
        self.assertEqual(result.section_modes.skn, 'build')

    def test_shader_mode_edit_patches_cloned_gpl(self):
        self.data['SluggiesModel']['Submeshes'] = [{
            'DisplayStates': [{'ShaderModeEdited': 'Shdw'}],
        }]
        patches = self._patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            with (
                mock.patch.object(main, '_validate_hammerspace_contract'),
                mock.patch.object(main, 'rebuild_surface_assignments', return_value=False),
                mock.patch.object(main, 'PatchGPLMaterialStates', return_value=b'PATCHED') as patch_states,
                mock.patch.object(main, 'BuildGPLMeshData') as build_gpl,
            ):
                result = main.BuildModelBlock(
                    self.data,
                    main.SectionModes(gpl='build'),
                )

        patch_states.assert_called_once()
        build_gpl.assert_not_called()
        self.assertEqual(result.section_sizes['GPL'], len(b'PATCHED'))

    def test_gpl_build_tolerates_missing_primlistdata(self):
        parsed = main.ParseSluggie({'SluggiesModel': {
            'UseBase64': False,
            'Submeshes': [{
                'FacesCount': 0,
                'FacesData': [0],
                'FaceTextureIndices': [],
                'VertexBuffer': {
                    'VertexBufferData': [0, 0, 0, 0, 0, 0],
                    'VertexBufferCompCount': 3,
                    'VertexBufferQuantizeInfo': 0,
                },
                'UVChannels': [],
                'ColorChannels': [],
                'DisplayStates': [{
                    'DisplayStateId': 0,
                    'PrimListData': None,
                    'ShaderMode': 'Spec',
                    'PrimListPtrFieldOffset': '0x0',
                    'PrimListSizeFieldOffset': '0x0',
                    'PrimListAbsoluteOffset': '0x0',
                    'PrimListLength': 0,
                    'DisplayStatePadBytes': '000000',
                }],
            }],
        }})
        result = main.BuildGPLMeshData(parsed)
        self.assertEqual(struct.unpack_from('>I', result.gpl_bytes, 0x00)[0], 0x00B749E0)
        self.assertEqual(len(result.pos_gpl_offsets), 1)
        descriptor_offset = struct.unpack_from('>I', result.gpl_bytes, 0x10)[0]
        layout_offset = struct.unpack_from('>I', result.gpl_bytes, descriptor_offset)[0]
        position_header_offset = struct.unpack_from('>I', result.gpl_bytes, layout_offset)[0]
        display_header_offset = struct.unpack_from('>I', result.gpl_bytes, layout_offset + 0x10)[0]
        position_data_offset = struct.unpack_from('>I', result.gpl_bytes, layout_offset + position_header_offset)[0]
        display_state_offset = struct.unpack_from('>I', result.gpl_bytes, layout_offset + display_header_offset + 4)[0]
        self.assertGreater(display_state_offset, position_data_offset + 6)

    def test_gpl_builder_pads_primitive_payload_and_size_to_32_bytes(self):
        parsed = main.ParseSluggie({'SluggiesModel': {
            'UseBase64': False,
            'Submeshes': [{
                'FacesCount': 0,
                'FacesData': [],
                'FaceTextureIndices': [],
                'VertexBuffer': {
                    'VertexBufferData': [0, 0, 0, 0, 0, 0],
                    'VertexBufferCompCount': 3,
                    'VertexBufferQuantizeInfo': 0,
                },
                'UVChannels': [],
                'ColorChannels': [],
                'DisplayStates': [{
                    'DisplayStateId': 7,
                    'PrimListData': [0x90, 0, 0, 0],
                    'ShaderMode': '00000000',
                    'PrimListPtrFieldOffset': '0x0',
                    'PrimListSizeFieldOffset': '0x0',
                    'PrimListAbsoluteOffset': '0x0',
                    'PrimListLength': 4,
                    'DisplayStatePadBytes': '000000',
                }],
            }],
        }})

        result = main.BuildGPLMeshData(parsed)
        descriptor_offset = struct.unpack_from('>I', result.gpl_bytes, 0x10)[0]
        layout_offset = struct.unpack_from('>I', result.gpl_bytes, descriptor_offset)[0]
        display_header_offset = struct.unpack_from('>I', result.gpl_bytes, layout_offset + 0x10)[0]
        display_state_offset = struct.unpack_from(
            '>I', result.gpl_bytes, layout_offset + display_header_offset + 4
        )[0]
        primitive_offset, primitive_size = struct.unpack_from(
            '>II', result.gpl_bytes, layout_offset + display_state_offset + 8
        )

        self.assertEqual(primitive_size, 32)
        self.assertEqual((layout_offset + primitive_offset) % 32, 0)
        self.assertEqual(
            result.gpl_bytes[layout_offset + primitive_offset:layout_offset + primitive_offset + 32],
            b'\x90\x00\x00\x00' + b'\x00' * 28,
        )

    def test_parser_consumes_rebuilt_surface_primitive_list(self):
        parsed = main.ParseSluggie({'SluggiesModel': {
            'UseBase64': False,
            'Submeshes': [{
                'FacesCount': 0,
                'FacesData': [],
                'FaceTextureIndices': [],
                'VertexBuffer': {
                    'VertexBufferData': [],
                    'VertexBufferCompCount': 3,
                    'VertexBufferQuantizeInfo': 0,
                },
                'UVChannels': [],
                'ColorChannels': [],
                'DisplayStates': [{
                    'DisplayStateId': 7,
                    'PrimListData': [0xAA],
                    'PrimListDataEdited': [0xBB],
                    'ShaderMode': '00000000',
                    'PrimListPtrFieldOffset': '0x0',
                    'PrimListSizeFieldOffset': '0x0',
                    'PrimListAbsoluteOffset': '0x0',
                    'PrimListLength': 1,
                    'DisplayStatePadBytes': '000000',
                }],
            }],
        }})

        self.assertEqual(parsed.mesh.submeshes[0].draw_states[0].prim_list_data, b'\xBB')

    def test_surface_only_rebuild_preserves_donor_geometry_arrays(self):
        parsed = main.ParseSluggie({'SluggiesModel': {
            'UseBase64': False,
            'Submeshes': [{
                'FacesCount': 0,
                'FacesData': [],
                'FaceTextureIndices': [],
                'SurfaceAssignmentsRebuiltByImporter': True,
                'VertexBuffer': {
                    'VertexBufferData': [1, 2],
                    'VertexBufferDataEdited': [3, 4],
                    'VertexBufferCompCount': 3,
                    'VertexBufferQuantizeInfo': 0,
                },
                'UVChannels': [{
                    'UVChannelIndex': 0,
                    'PaletteName': '',
                    'UVChannelData': [5, 6],
                    'UVChannelDataEdited': [7, 8, 9, 10],
                    'UVFacesData': [],
                    'UVChannelCompCount': 2,
                    'UVChannelQuantizeInfo': 0,
                }],
                'ColorChannels': [],
                'DisplayStates': [{
                    'DisplayStateId': 7,
                    'PrimListData': [0xAA],
                    'PrimListDataEdited': [0xBB],
                    'ShaderMode': '00000000',
                    'PrimListPtrFieldOffset': '0x0',
                    'PrimListSizeFieldOffset': '0x0',
                    'PrimListAbsoluteOffset': '0x0',
                    'PrimListLength': 1,
                    'DisplayStatePadBytes': '000000',
                }],
            }],
        }})

        submesh = parsed.mesh.submeshes[0]
        self.assertEqual(submesh.vertex_data, b'\x01\x02')
        self.assertEqual(submesh.uv_channels[0].uv_data, b'\x05\x06')
        self.assertEqual(submesh.draw_states[0].prim_list_data, b'\xBB')

    def test_model_block_uses_schema_trailing_sections_when_available(self):
        fake_parsed = SimpleNamespace(trailing_sections=[
            main.TrailingSection(header_field_offset=0x14, original_ptr=0x100, data=b'TAIL'),
        ])
        patches = self._patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            with (
                mock.patch.object(main, 'ParseSluggie', return_value=fake_parsed),
                mock.patch.object(main, 'CloneTrailingSections', side_effect=AssertionError('should not read donor trailing data')),
            ):
                result = main.BuildModelBlock(self.data)

        self.assertEqual(result.section_sizes['trailing'], 4)
        self.assertIn(b'TAIL', result.block[-4:])

    def test_header_builder_aligns_skn_section_to_32_bytes(self):
        block = main.BuildHEADERModelBlock(
            b'GPL',
            b'ACT',
            b'TEX',
            b'SKN',
        )

        skn_offset = struct.unpack_from('>I', block, 0x10)[0]
        self.assertEqual(skn_offset % 32, 0)
        self.assertEqual(block[skn_offset:skn_offset + 3], b'SKN')

    def test_header_builder_preserves_donor_gap_after_gpl(self):
        original_header = bytearray(0x20)
        struct.pack_into('>I', original_header, 0x04, 0x20)
        struct.pack_into('>I', original_header, 0x08, 0x30)

        block = main.BuildHEADERModelBlock(
            b'GPL',
            b'ACT',
            b'',
            b'',
            original_header=bytes(original_header),
        )

        self.assertEqual(struct.unpack_from('>I', block, 0x08)[0], 0x30)
        self.assertEqual(block[0x23:0x30], b'\x00' * 13)
        self.assertEqual(block[0x30:0x33], b'ACT')

    def test_header_builder_preserves_skn_relative_trailing_offset_once(self):
        original_header = bytearray(0x20)
        struct.pack_into('>I', original_header, 0x10, 0x100)
        struct.pack_into('>I', original_header, 0x18, 0x180)

        block = main.BuildHEADERModelBlock(
            b'GPL',
            b'ACT',
            b'TEX',
            b'SKN',
            trailing_bytes=b'TAIL',
            original_header=bytes(original_header),
            original_trailing_off=0x180,
        )

        skn_offset = struct.unpack_from('>I', block, 0x10)[0]
        ptr7_offset = struct.unpack_from('>I', block, 0x18)[0]
        self.assertEqual(ptr7_offset - skn_offset, 0x80)
        self.assertEqual(block[ptr7_offset:ptr7_offset + 4], b'TAIL')
        self.assertEqual(block.count(b'TAIL'), 1)

    def test_header_builder_relocates_trailing_pointers_relative_to_tail(self):
        original_header = bytearray(0x20)
        struct.pack_into('>I', original_header, 0x14, 0x100)
        struct.pack_into('>I', original_header, 0x18, 0x140)
        struct.pack_into('>I', original_header, 0x1C, 0x180)

        block = main.BuildHEADERModelBlock(
            b'GPL',
            b'ACT',
            b'TEX',
            b'SKN',
            trailing_bytes=b'TAIL',
            original_header=bytes(original_header),
            original_trailing_off=0x100,
        )

        skn_offset = struct.unpack_from('>I', block, 0x10)[0]
        tail_offset = skn_offset + len(b'SKN')
        self.assertEqual(struct.unpack_from('>I', block, 0x14)[0], tail_offset)
        self.assertEqual(struct.unpack_from('>I', block, 0x18)[0], tail_offset + 0x40)
        self.assertEqual(struct.unpack_from('>I', block, 0x1C)[0], tail_offset + 0x80)

    def test_header_builder_zeroes_trailing_pointers_when_tail_is_absent(self):
        original_header = bytearray(0x20)
        struct.pack_into('>I', original_header, 0x14, 0x100)
        struct.pack_into('>I', original_header, 0x18, 0x140)
        struct.pack_into('>I', original_header, 0x1C, 0x180)

        block = main.BuildHEADERModelBlock(
            b'GPL',
            b'ACT',
            b'TEX',
            b'SKN',
            trailing_bytes=b'',
            original_header=bytes(original_header),
            original_trailing_off=0x100,
        )

        self.assertEqual(struct.unpack_from('>I', block, 0x14)[0], 0)
        self.assertEqual(struct.unpack_from('>I', block, 0x18)[0], 0)
        self.assertEqual(struct.unpack_from('>I', block, 0x1C)[0], 0)

    def test_unimplemented_section_build_is_rejected_before_parsing(self):
        with mock.patch.object(main, 'ParseSluggie') as parse:
            with self.assertRaisesRegex(ValueError, 'ACT=build is not implemented'):
                main.BuildModelBlock(self.data, main.SectionModes(act='build'))
        parse.assert_not_called()

    def test_hammerspace_rebuild_rejects_missing_required_properties(self):
        data = {'SluggiesModel': {
            'ChunkNumber': 18,
            'FileIndex': 0,
            'UseHammerspace': True,
            'ModelOffset': 0x1000,
            'ModelLength': 0x2000,
        }}
        with mock.patch.object(main.hh, 'readDolEntry', return_value=(0x1000, 0x2000)):
            with self.assertRaisesRegex(ValueError, 'missing required rebuild properties'):
                main.BuildModelBlock(data, main.SectionModes(gpl='build'))

    def test_skn_only_rebuilds_tolerate_missing_primlistdata(self):
        data = {'SluggiesModel': {
            'ChunkNumber': 18,
            'FileIndex': 0,
            'UseHammerspace': True,
            'ModelOffset': 0x1000,
            'ModelLength': 0x2000,
            'Submeshes': [{
                'VertexBuffer': {
                    'VertexBufferData': b'\x00',
                    'VertexBufferCompCount': 3,
                    'VertexBufferQuantizeInfo': 0,
                },
                'FacesData': b'\x00',
                'DisplayStates': [{
                    'DisplayStateId': 0,
                    'PrimListLength': 0,
                    'ShaderMode': 'Spec',
                }],
            }],
        }}
        patches = self._patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            with (
                mock.patch.object(main, 'validate_model_block', return_value={
                    'valid': True,
                    'errors': [],
                    'warnings': [],
                    'facts': {'section_pointers': {'GPL': 32, 'ACT': 35, 'TEX': 38, 'SKN': 41}},
                }),
                mock.patch.object(main, 'BuildSKNSkinningData', return_value=b'SKN'),
            ):
                result = main.BuildModelBlock(data, main.SectionModes(skn='build'))

        self.assertTrue(result.validation_report['valid'])

    def test_legacy_non_hammerspace_files_skip_contract_validation(self):
        data = {'SluggiesModel': {
            'ChunkNumber': 18,
            'FileIndex': 0,
            'ModelOffset': 0x1000,
            'ModelLength': 0x2000,
        }}
        patches = self._patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            with (
                mock.patch.object(main, 'validate_model_block', return_value={
                    'valid': True,
                    'errors': [],
                    'warnings': [],
                    'facts': {'section_pointers': {'GPL': 32, 'ACT': 35, 'TEX': 38, 'SKN': 41}},
                }),
            ):
                result = main.BuildModelBlock(data)

        self.assertTrue(result.validation_report['valid'])

    def test_hammerspace_clone_only_runs_without_required_rebuild_properties(self):
        data = {'SluggiesModel': {
            'ChunkNumber': 18,
            'FileIndex': 0,
            'UseHammerspace': True,
        }}
        patches = self._patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            with (
                mock.patch.object(main, 'validate_model_block', return_value={
                    'valid': True,
                    'errors': [],
                    'warnings': [],
                    'facts': {'section_pointers': {'GPL': 32, 'ACT': 35, 'TEX': 38, 'SKN': 41}},
                }),
            ):
                result = main.BuildModelBlock(data)

        self.assertTrue(result.validation_report['valid'])

    def test_write_operation_owns_output_mutations(self):
        build = main.ModelBlockBuild(
            block=b'model-block',
            parsed=self.parsed,
            chunk_number=18,
            file_index=0,
            original_offset=0x1000,
            original_length=42,
            section_modes=main.SectionModes(),
            section_sizes={},
            validation_report={'valid': True},
        )
        with (
            mock.patch.object(main.hh, 'readOutputDolEntry', return_value=(0, 42)),
            mock.patch.object(main.hh, 'findFreeMemoryChunk', return_value=0x2000),
            mock.patch.object(main.hh, 'writeModelBlock') as write_model,
            mock.patch.object(main.hh, 'patchDolEntry') as patch_dol,
            mock.patch.object(main.hh, 'findSharedEntries', return_value=[(19, 1)]),
            mock.patch.object(main.hh, 'patchFstFileSize') as patch_fst,
            mock.patch.object(main.hh, 'zeroOriginalModel') as zero_original,
            mock.patch.object(main.hh, 'writeDebugDumps') as write_dumps,
            mock.patch.object(main.os.path, 'getsize', return_value=123456),
        ):
            new_offset = main.WriteModelBlock(build, 'fixture.sluggie')

        self.assertEqual(new_offset, 0x2000)
        write_model.assert_called_once_with(b'model-block', 0x2000)
        self.assertEqual(patch_dol.call_args_list, [
            mock.call(18, 0, 0x2000, 11),
            mock.call(19, 1, 0x2000, 11),
        ])
        patch_fst.assert_called_once_with(123456)
        zero_original.assert_called_once_with(18, 0)
        write_dumps.assert_called_once_with('fixture.sluggie', 0x1000, 42, b'model-block')

    def test_write_expands_after_existing_hammerspace_when_no_free_run_exists(self):
        build = main.ModelBlockBuild(
            block=b'model-block',
            parsed=self.parsed,
            chunk_number=18,
            file_index=0,
            original_offset=0x1000,
            original_length=42,
            section_modes=main.SectionModes(),
            section_sizes={},
            validation_report={'valid': True},
        )
        current_size = 0x1003
        expected_start = 0x1020
        expected_size = expected_start + len(build.block) + main.hh.HS_BUFFER_BYTES
        with (
            mock.patch.object(main.hh, 'readOutputDolEntry', return_value=(0, 42)),
            mock.patch.object(main.hh, 'findFreeMemoryChunk', side_effect=(-1, expected_start)),
            mock.patch.object(main.hh, 'ensureOutputDat', return_value=True) as ensure_dat,
            mock.patch.object(main.hh, 'writeModelBlock'),
            mock.patch.object(main.hh, 'patchDolEntry'),
            mock.patch.object(main.hh, 'findSharedEntries', return_value=[]),
            mock.patch.object(main.hh, 'patchFstFileSize'),
            mock.patch.object(main.hh, 'zeroOriginalModel'),
            mock.patch.object(main.hh, 'writeDebugDumps'),
            mock.patch.object(main.os.path, 'getsize', side_effect=(current_size, expected_size)),
        ):
            new_offset = main.WriteModelBlock(build, 'fixture.sluggie')

        self.assertEqual(new_offset, expected_start)
        self.assertEqual(ensure_dat.call_args_list, [mock.call(), mock.call(expected_size)])

    def test_write_rejects_failed_validation(self):
        build = mock.Mock(validation_report={'valid': False})
        with mock.patch.object(main.hh, 'readOutputDolEntry') as read_output:
            with self.assertRaisesRegex(ValueError, 'failed validation report'):
                main.WriteModelBlock(build, 'fixture.sluggie')
        read_output.assert_not_called()


class BuildTEXTests(unittest.TestCase):
    """Gate test for milestone 2: BuildTEX() TEX section binary layout."""

    def _make_texture(self, index, width, height, fmt, palette_entries=0,
                      palette_format=0, image_offset=0, image_length=0,
                      palette_offset=None, palette_length=None):
        return main.Texture(
            texture_index=index,
            width=width,
            height=height,
            format=fmt,
            palette_entries=palette_entries,
            palette_format=palette_format,
            edge_lod_enable=False,
            min_lod=0.0,
            max_lod=0.0,
            unpacked=0,
            desc_unknown_at_10=bytes(7),
            desc_unknown_at_1b=bytes(5),
            image_data_offset=image_offset,
            image_data_length=image_length,
            palette_data_offset=palette_offset,
            palette_data_length=palette_length,
            texture_descriptor_offset=0,
        )

    def _make_parsed(self, textures, clut_count=0):
        return main.SluggieParsed(
            mesh=main.MeshData(submeshes=[], source_gpl_base_offset=0),
            bones=None,
            textures=main.TextureData(textures=textures),
            skinning=None,
            gpl_user_data=None,
            gpl_user_data_len=0,
            act_header=None,
            tex_header=main.TEXHeader(clut_count=clut_count) if clut_count else None,
            trailing_sections=[],
            model_offset=0,
            model_length=0,
        )

    def test_build_tex_reencodes_one_and_clones_one(self):
        # Texture 0: re-encoded via plan entry (new dimensions 4x4, RGBA8).
        # Texture 1: not in plan -> cloned from INPUT dt_na.dat.
        reencoded_image = b'\xAA' * 64          # 4x4 RGBA8 = 64 bytes
        reencoded_palette = b'\x11\x22\x33\x44'  # 2 palette entries
        cloned_image = b'\xBB' * 32             # 8x8 RGBA8 = 32 bytes
        cloned_palette = b'\x55\x66'            # 1 palette entry

        tex0 = self._make_texture(
            0, width=64, height=64, fmt=0x6,
            palette_entries=2, palette_format=0x1,
        )
        tex1 = self._make_texture(
            1, width=8, height=8, fmt=0x6,
            palette_entries=1, palette_format=0x1,
            image_offset=0x1000, image_length=32,
            palette_offset=0x2000, palette_length=2,
        )
        parsed = self._make_parsed([tex0, tex1], clut_count=2)

        entry = texture_helper.TexturePlanEntry(
            texture_index=0,
            texture_file_name='tex0.png',
            width=4,
            height=4,
            format=0x6,
            format_name='RGBA8',
            image_data=reencoded_image,
            palette_data=reencoded_palette,
            palette_entries=2,
            palette_format=0x1,
        )
        plan = texture_helper.TexturePlan(entries=(entry,))

        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
            buf = bytearray(0x3000)
            buf[0x1000:0x1000 + 32] = cloned_image
            buf[0x2000:0x2000 + 2] = cloned_palette
            input_dat.write_bytes(bytes(buf))
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                section = main.BuildTEX(parsed, plan)

        # --- Header ---
        texture_count = struct.unpack_from('>H', section, 0x00)[0]
        clut_count = struct.unpack_from('>H', section, 0x02)[0]
        self.assertEqual(texture_count, 2)
        self.assertEqual(clut_count, 2)

        # --- Descriptor table ---
        desc0 = 4
        desc1 = 4 + 0x20
        img0_off = struct.unpack_from('>I', section, desc0 + 0x00)[0]
        pal0_off = struct.unpack_from('>I', section, desc0 + 0x04)[0]
        h0 = struct.unpack_from('>H', section, desc0 + 0x08)[0]
        w0 = struct.unpack_from('>H', section, desc0 + 0x0A)[0]
        fmt0 = section[desc0 + 0x17]
        pal_entries0 = struct.unpack_from('>H', section, desc0 + 0x18)[0]
        pal_fmt0 = section[desc0 + 0x1A]

        img1_off = struct.unpack_from('>I', section, desc1 + 0x00)[0]
        pal1_off = struct.unpack_from('>I', section, desc1 + 0x04)[0]
        h1 = struct.unpack_from('>H', section, desc1 + 0x08)[0]
        w1 = struct.unpack_from('>H', section, desc1 + 0x0A)[0]
        fmt1 = section[desc1 + 0x17]
        pal_entries1 = struct.unpack_from('>H', section, desc1 + 0x18)[0]

        # Re-encoded texture has the NEW dimensions in its descriptor.
        self.assertEqual((w0, h0), (4, 4))
        self.assertEqual(fmt0, 0x6)
        self.assertEqual(pal_entries0, 2)
        self.assertEqual(pal_fmt0, 0x1)
        # Cloned texture keeps its original dimensions.
        self.assertEqual((w1, h1), (8, 8))
        self.assertEqual(fmt1, 0x6)
        self.assertEqual(pal_entries1, 1)

        # --- Data region layout: images first, then palettes ---
        # Data region starts at the first 32-byte-aligned offset after the
        # descriptor table (Wii Broadway GPU DMA alignment requirement).
        data_start = (4 + 2 * 0x20 + 31) & ~31  # 0x60
        self.assertEqual(data_start % 32, 0)
        self.assertEqual(img0_off, data_start)
        self.assertEqual(img1_off, data_start + 64)
        self.assertEqual(pal0_off, data_start + 64 + 32)
        self.assertEqual(pal1_off, data_start + 64 + 32 + 4)

        # All offsets point to valid regions within the section.
        self.assertEqual(len(section), data_start + 64 + 32 + 4 + 2)
        for off, length in (
            (img0_off, 64), (img1_off, 32), (pal0_off, 4), (pal1_off, 2),
        ):
            self.assertGreaterEqual(off, data_start)
            self.assertLessEqual(off + length, len(section))

        # Re-encoded texture carries the new payload bytes.
        self.assertEqual(section[img0_off:img0_off + 64], reencoded_image)
        self.assertEqual(section[pal0_off:pal0_off + 4], reencoded_palette)
        # Cloned texture has its original data intact.
        self.assertEqual(section[img1_off:img1_off + 32], cloned_image)
        self.assertEqual(section[pal1_off:pal1_off + 2], cloned_palette)

    def test_build_tex_without_plan_clones_all(self):
        tex0 = self._make_texture(
            0, width=8, height=8, fmt=0x6,
            image_offset=0x100, image_length=8,
        )
        parsed = self._make_parsed([tex0])
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
            buf = bytearray(0x200)
            buf[0x100:0x108] = b'\xCD\xCD\xCD\xCD\xCD\xCD\xCD\xCD'
            input_dat.write_bytes(bytes(buf))
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                section = main.BuildTEX(parsed, None)

        texture_count = struct.unpack_from('>H', section, 0x00)[0]
        self.assertEqual(texture_count, 1)
        img_off = struct.unpack_from('>I', section, 4 + 0x00)[0]
        self.assertEqual(section[img_off:img_off + 8], b'\xCD' * 8)

    def test_build_tex_rejects_malformed_encoded_payload(self):
        tex0 = self._make_texture(0, width=64, height=64, fmt=0x6)
        parsed = self._make_parsed([tex0])
        entry = texture_helper.TexturePlanEntry(
            texture_index=0,
            texture_file_name='tex0.png',
            width=32,
            height=32,
            format=0x6,
            format_name='RGBA8',
            image_data=b'\xAA' * 10,  # wrong size for 32x32 RGBA8
            palette_data=b'',
            palette_entries=0,
            palette_format=None,
        )
        plan = texture_helper.TexturePlan(entries=(entry,))
        with self.assertRaisesRegex(ValueError, 'encoded image payload'):
            main.BuildTEX(parsed, plan)

    def test_build_tex_empty_returns_empty(self):
        parsed = self._make_parsed([])
        self.assertEqual(main.BuildTEX(parsed, None), b'')

    def test_build_tex_all_data_ptrs_are_32_byte_aligned(self):
        """Regression: 5 textures (Mario model scenario) — all image/palette
        data pointers MUST be 32-byte aligned for the Wii Broadway GPU."""
        # 5 textures, each with a 128-byte image payload and no palette.
        textures = [
            self._make_texture(i, width=8, height=8, fmt=0xE,
                               image_offset=0x100 + i * 0x100, image_length=128)
            for i in range(5)
        ]
        parsed = self._make_parsed(textures)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
            buf = bytearray(0x1000)
            for i in range(5):
                buf[0x100 + i * 0x100 : 0x100 + i * 0x100 + 128] = bytes([i]) * 128
            input_dat.write_bytes(bytes(buf))
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                section = main.BuildTEX(parsed, None)

        # All image data pointers must be 32-byte aligned.
        for i in range(5):
            desc_off = 4 + i * 0x20
            img_off = struct.unpack_from('>I', section, desc_off)[0]
            self.assertEqual(
                img_off % 32, 0,
                f'texture {i}: image_data_offset 0x{img_off:X} is not 32-byte aligned',
            )
            # Payload must be intact.
            self.assertEqual(section[img_off:img_off + 128], bytes([i]) * 128)

        # The descriptor table ends at 4 + 5*0x20 = 0xA4; data must start at 0xC0.
        expected_data_start = (4 + 5 * 0x20 + 31) & ~31
        self.assertEqual(expected_data_start, 0xC0)
        self.assertEqual(
            struct.unpack_from('>I', section, 4)[0],  # first image ptr
            expected_data_start,
        )
        # Padding between descriptor table and data region must be zero.
        desc_end = 4 + 5 * 0x20
        self.assertEqual(section[desc_end:expected_data_start], b'\x00' * (expected_data_start - desc_end))


class BuildModelBlockTEXBuildTests(unittest.TestCase):
    """Gate test for milestone 3: BuildModelBlock() wires BuildTEX into the
    assembly path when tex='build'."""

    def setUp(self):
        self.data = {'SluggiesModel': {'ChunkNumber': 18, 'FileIndex': 0}}
        self.parsed = mock.sentinel.parsed

    def _patch_common(self):
        return (
            mock.patch.object(main.hh, 'readDolEntry', return_value=(0x1000, 0x2000)),
            mock.patch.object(main, 'ParseSluggie', return_value=self.parsed),
            mock.patch.object(main, 'CloneGPL', return_value=b'GPL'),
            mock.patch.object(main, '_gpl_pos_offsets_from_bytes', return_value=[3]),
            mock.patch.object(main, 'CloneACT', return_value=b'ACT'),
            mock.patch.object(main, 'CloneTEX', return_value=b'TEX'),
            mock.patch.object(main, 'CloneSKN', return_value=b'SKN'),
            mock.patch.object(main, 'CloneTrailingSections', return_value=(b'TAIL', 0x80)),
            mock.patch.object(main, 'CloneHEADER', return_value=b'\x00' * 0x20),
        )

    def _run(self, modes, **kwargs):
        patches = self._patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            with (
                mock.patch.object(main, '_validate_hammerspace_contract'),
                mock.patch.object(main, 'validate_model_block', return_value={
                    'valid': True,
                    'errors': [],
                    'warnings': [],
                    'facts': {'section_pointers': {'GPL': 32, 'ACT': 35, 'TEX': 38, 'SKN': 41}},
                }),
            ):
                return main.BuildModelBlock(self.data, modes, **kwargs)

    def test_tex_build_with_reimport_uses_plan_and_buildtex(self):
        self.data['SluggiesModel'].update({
            'UseHammerspace': True,
            'ReimportTextures': True,
            'TextureDescriptors': [
                {'TextureIndex': 0, 'TextureFileName': '0.png'},
            ],
        })
        plan = mock.Mock(skipped=())
        with (
            mock.patch.object(
                texture_helper, 'build_hammerspace_texture_plan',
                return_value=plan,
            ) as build_plan,
            mock.patch.object(main, 'BuildTEX', return_value=b'BUILT_TEX') as build_tex,
            mock.patch.object(main, 'CloneTEX') as clone_tex,
        ):
            result = self._run(main.SectionModes(tex='build'), sluggie_path='model.sluggies')

        build_plan.assert_called_once_with(
            'model.sluggies',
            self.data['SluggiesModel']['TextureDescriptors'],
            allow_dimension_change=True,
        )
        build_tex.assert_called_once_with(self.parsed, plan)
        clone_tex.assert_not_called()
        self.assertEqual(result.section_sizes['TEX'], len(b'BUILT_TEX'))
        self.assertEqual(result.section_modes.tex, 'build')

    def test_tex_build_with_reimport_requires_sluggie_path(self):
        self.data['SluggiesModel'].update({
            'UseHammerspace': True,
            'ReimportTextures': True,
            'TextureDescriptors': [
                {'TextureIndex': 0, 'TextureFileName': '0.png'},
            ],
        })
        with (
            mock.patch.object(texture_helper, 'build_hammerspace_texture_plan') as build_plan,
            mock.patch.object(main, 'BuildTEX') as build_tex,
        ):
            with self.assertRaisesRegex(ValueError, 'sluggie path'):
                self._run(main.SectionModes(tex='build'))
        build_plan.assert_not_called()
        build_tex.assert_not_called()

    def test_tex_build_without_reimport_clones_payloads(self):
        self.data['SluggiesModel'].update({
            'UseHammerspace': True,
            'TextureDescriptors': [
                {'TextureIndex': 0, 'TextureFileName': '0.png'},
            ],
        })
        with (
            mock.patch.object(texture_helper, 'build_hammerspace_texture_plan') as build_plan,
            mock.patch.object(main, 'BuildTEX', return_value=b'BUILT_TEX') as build_tex,
        ):
            result = self._run(main.SectionModes(tex='build'), sluggie_path='model.sluggies')

        build_plan.assert_not_called()
        build_tex.assert_called_once_with(self.parsed, None)
        self.assertEqual(result.section_sizes['TEX'], len(b'BUILT_TEX'))

    def test_tex_clone_mode_still_clones(self):
        patches = self._patch_common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[6], patches[7], patches[8]:
            with (
                mock.patch.object(main, '_validate_hammerspace_contract'),
                mock.patch.object(main, 'CloneTEX', return_value=b'TEX') as clone_tex,
                mock.patch.object(main, 'BuildTEX') as build_tex,
                mock.patch.object(texture_helper, 'build_hammerspace_texture_plan') as build_plan,
                mock.patch.object(main, 'validate_model_block', return_value={
                    'valid': True,
                    'errors': [],
                    'warnings': [],
                    'facts': {'section_pointers': {'GPL': 32, 'ACT': 35, 'TEX': 38, 'SKN': 41}},
                }),
            ):
                result = main.BuildModelBlock(
                    self.data, main.SectionModes(tex='clone'), sluggie_path='model.sluggies'
                )

        build_plan.assert_not_called()
        build_tex.assert_not_called()
        clone_tex.assert_called_once()
        self.assertEqual(result.section_sizes['TEX'], len(b'TEX'))


class BuildModelBlockRootScaleTests(unittest.TestCase):
    """Integration test: ``_apply_root_scale_patch`` writes the edited root-bone
    SRT scale into the cloned ACT section at the ACT-section-relative offset.

    Only the ACT section payload is mocked. The real ``_act_section_absolute``
    reads the model-block header from a temp INPUT_DAT, and the real
    ``hammerspace_root_scale_patch`` resolves the main root bone and computes the
    offset, so this exercises the full write path end-to-end.
    """

    SOURCE_MODEL_OFFSET = 0x1000
    ACT_OFF = 0x40          # model header +0x08 -> ACT section rel. to model start
    ORIENTATION_PTR = 0x50  # SRT offset rel. to ACT section start

    def _write_input_dat(self, temp_dir, act_off):
        input_dat = pathlib.Path(temp_dir) / 'dt_na.dat'
        buf = bytearray(0x2000)
        # Model block header at SOURCE_MODEL_OFFSET; +0x08 holds act_off.
        struct.pack_into('>I', buf, self.SOURCE_MODEL_OFFSET + 0x08, act_off)
        input_dat.write_bytes(bytes(buf))
        return input_dat

    def _bones(self):
        # Bone 0 is a parentless leaf; Bone 1 is the parentless root of the
        # visible subtree; Bone 2 hangs off Bone 1. Main root must be Bone 1.
        srt_absolute = self.SOURCE_MODEL_OFFSET + self.ACT_OFF + self.ORIENTATION_PTR
        return [
            {'BoneId': 0, 'ParentBoneId': None, 'SRTOffset': None, 'Scale': [1.0, 1.0, 1.0]},
            {'BoneId': 1, 'ParentBoneId': None, 'SRTOffset': f'0x{srt_absolute:X}', 'Scale': [1.0, 1.0, 1.0]},
            {'BoneId': 2, 'ParentBoneId': 1, 'SRTOffset': None, 'Scale': [1.0, 1.0, 1.0]},
        ]

    def test_writes_edited_scale_at_act_relative_offset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = b'\x00' * 0x80
            data = {'SluggiesModel': {
                'RootBoneScaleEdited': [2.0, 1.5, 1.0],
                'BoneHierarchy': self._bones(),
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                patched = main._apply_root_scale_patch(act_bytes, data, self.SOURCE_MODEL_OFFSET)

        # Scale lands at orientationPTR + 0x04 within the ACT section.
        scale_offset = self.ORIENTATION_PTR + 0x04
        self.assertEqual(
            patched[scale_offset:scale_offset + 12],
            struct.pack('>3f', 2.0, 1.5, 1.0),
        )
        # Everything else in the cloned ACT section is untouched.
        self.assertEqual(patched[:scale_offset], act_bytes[:scale_offset])
        self.assertEqual(patched[scale_offset + 12:], act_bytes[scale_offset + 12:])

    def test_no_edit_returns_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            act_bytes = b'\x11' * 0x80
            data = {'SluggiesModel': {'BoneHierarchy': self._bones()}}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                result = main._apply_root_scale_patch(act_bytes, data, self.SOURCE_MODEL_OFFSET)
        self.assertEqual(result, act_bytes)

    def test_no_act_section_returns_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, 0)  # act_off == 0 -> no ACT
            act_bytes = b'\x00' * 0x80
            data = {'SluggiesModel': {
                'RootBoneScaleEdited': [2.0, 1.5, 1.0],
                'BoneHierarchy': self._bones(),
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                result = main._apply_root_scale_patch(act_bytes, data, self.SOURCE_MODEL_OFFSET)
        self.assertEqual(result, act_bytes)

    def test_mismatched_srt_offset_raises(self):
        # SRTOffset before the ACT section start -> .sluggie metadata mismatch.
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dat = self._write_input_dat(temp_dir, self.ACT_OFF)
            bones = self._bones()
            bones[1]['SRTOffset'] = f'0x{self.SOURCE_MODEL_OFFSET + self.ACT_OFF - 1:X}'
            act_bytes = b'\x00' * 0x80
            data = {'SluggiesModel': {
                'RootBoneScaleEdited': [2.0, 1.5, 1.0],
                'BoneHierarchy': bones,
            }}
            with mock.patch.object(main.hh, 'INPUT_DAT', str(input_dat)):
                with self.assertRaises(ValueError):
                    main._apply_root_scale_patch(act_bytes, data, self.SOURCE_MODEL_OFFSET)


if __name__ == '__main__':
    unittest.main()