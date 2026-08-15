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
            mock.patch.object(main.hh, 'readDolEntry', return_value=(0x1000, 42)),
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


if __name__ == '__main__':
    unittest.main()